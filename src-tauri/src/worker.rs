//! Spawns and supervises the local FastAPI worker sidecar process.

use std::net::TcpListener;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command as StdCommand, Stdio};
use std::time::Duration;

use libc::{c_int, pid_t, SIGKILL, SIGTERM};
use rand::distr::Alphanumeric;
use rand::Rng;
use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, ChildStderr, ChildStdout, Command as TokioCommand};
use tokio::sync::oneshot;
use tokio::time::timeout;

pub const SESSION_TOKEN_ENV_VAR: &str = "INVOICE_RENAMER_SESSION_TOKEN";
pub const PORT_ENV_VAR: &str = "INVOICE_RENAMER_PORT";

const READY_MARKER: &str = "INVOICE_RENAMER_WORKER_READY";
const SIDECAR_NAME: &str = "invoice-renamer-worker";
/// Subdirectory holding the staged worker, under both the packaged app's
/// resource directory and the local dev staging tree - see
/// `scripts/build_worker_sidecar.sh` and `scripts/build_macos_app.sh`.
const WORKER_RESOURCE_SUBDIR: &str = "worker";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_GRACE_PERIOD: Duration = Duration::from_secs(5);
const GROUP_EXIT_POLL_TIMEOUT: Duration = Duration::from_secs(2);
const TOKEN_LEN: usize = 32;

/// Connection details the frontend needs to reach the worker directly.
#[derive(Clone, Serialize)]
pub struct WorkerEndpoint {
    pub port: u16,
    pub token: String,
}

/// A running worker process plus the endpoint used to reach it.
///
/// Whether the PyInstaller launcher itself forks a separate Python child
/// differs between its onefile and onedir builds, and the worker also
/// shells out to `tesseract` for OCR - so `child` alone is never a reliable
/// handle on everything that needs to die. `pgid` identifies the whole
/// process group the launcher was placed in at spawn time and is what
/// shutdown actually targets, regardless of that internal process shape.
pub struct WorkerHandle {
    pub endpoint: WorkerEndpoint,
    child: Child,
    pgid: u32,
}

impl WorkerHandle {
    /// Terminates the worker and everything it spawned, then waits for exit.
    pub async fn shutdown(self) {
        terminate(self.child, self.pgid, SHUTDOWN_GRACE_PERIOD).await;
    }
}

fn pick_free_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|err| format!("could not reserve a port: {err}"))?;
    listener
        .local_addr()
        .map(|addr| addr.port())
        .map_err(|err| format!("could not read reserved port: {err}"))
}

fn generate_session_token() -> String {
    rand::rng()
        .sample_iter(Alphanumeric)
        .take(TOKEN_LEN)
        .map(char::from)
        .collect()
}

/// Puts the child in a new session and process group of its own, rather
/// than the one it would otherwise inherit from this app.
///
/// This runs in the forked child between `fork` and `exec`, so there is no
/// race with whatever the child (the PyInstaller launcher) forks after it
/// starts running: its own children inherit the new group too, which is
/// what lets `terminate` reap the whole subtree by process group instead of
/// tracking individual pids.
fn detach_into_own_process_group(command: &mut StdCommand) {
    // SAFETY: `setsid` takes no arguments, touches no memory shared with
    // the parent, and is async-signal-safe, so it is sound to call from a
    // `pre_exec` hook running post-fork, pre-exec in the child.
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

/// Sends `signal` to every process in group `pgid`.
///
/// A missing group (everything in it already exited) reports `ESRCH`,
/// which is the expected steady state once shutdown has finished and is
/// silently ignored here.
fn signal_process_group(pgid: u32, signal: c_int) {
    // SAFETY: `killpg` only delivers a signal to processes we already own
    // (our own sidecar's group); it cannot affect unrelated processes and
    // has no memory-safety implications either way.
    unsafe {
        libc::killpg(pgid as pid_t, signal);
    }
}

/// Reports whether any process in group `pgid` still exists.
///
/// Signal 0 delivers nothing; it only performs the existence/permission
/// check. `ESRCH` means the group has no members left, which is the only
/// case treated as "gone" — any other outcome (including a permission
/// error, which can't happen for our own descendants) is treated as "still
/// there" so a transient error never causes a premature "it's gone".
fn process_group_exists(pgid: u32) -> bool {
    // SAFETY: signal 0 performs no action beyond the existence check.
    let result = unsafe { libc::killpg(pgid as pid_t, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
}

/// Polls until group `pgid` has no members left, or `poll_timeout` elapses.
///
/// The direct child (our launcher) is reaped synchronously the moment it
/// exits, via tokio's `SIGCHLD`-driven reactor, but anything it forked is an
/// independently scheduled process: the kernel can deliver `SIGKILL` to it
/// at the same instant, without that process finishing its own teardown
/// (and releasing what it holds, like a listening socket) in that instant
/// too. Confirming the whole group is gone — not just our direct child —
/// is what actually backs the "cannot outlive the app" guarantee.
async fn wait_for_group_exit(pgid: u32, poll_timeout: Duration) {
    const POLL_INTERVAL: Duration = Duration::from_millis(10);

    let deadline = tokio::time::Instant::now() + poll_timeout;
    while process_group_exists(pgid) {
        if tokio::time::Instant::now() >= deadline {
            log::warn!("worker process group {pgid} still has members {poll_timeout:?} after the SIGKILL sweep");
            return;
        }
        tokio::time::sleep(POLL_INTERVAL).await;
    }
}

/// Sends a graceful signal to the worker's whole process group, gives it
/// `grace_period` to exit, then force-kills and confirms the whole group —
/// not just the launcher we hold a handle to — is gone.
///
/// The follow-up `SIGKILL` runs unconditionally: if the group already
/// exited from the `SIGTERM`, it is a harmless no-op (`ESRCH`); if the
/// launcher exited but left a forked child behind (the bug this guards
/// against — a killed launcher cannot forward a signal to a child it never
/// gets the chance to react to), the child still shares the group and gets
/// reaped here.
async fn terminate(mut child: Child, pgid: u32, grace_period: Duration) {
    signal_process_group(pgid, SIGTERM);
    let _ = timeout(grace_period, child.wait()).await;

    signal_process_group(pgid, SIGKILL);
    let _ = child.wait().await;

    wait_for_group_exit(pgid, GROUP_EXIT_POLL_TIMEOUT).await;
}

/// Absolute paths, in priority order, where the staged worker executable
/// might live: the packaged app's resource directory first, then - only
/// when `include_dev_fallback` is set - the directory
/// `scripts/build_worker_sidecar.sh` stages into for local development. The
/// dev candidate is built from the compiled-in crate root rather than the
/// process's current directory, so resolution does not depend on cwd;
/// neither candidate is looked up on PATH.
///
/// The dev fallback must not apply to a release build: `cargo tauri build`
/// runs on the same checkout that `cargo tauri dev` stages a worker into, so
/// an unconditional fallback would let a packaged app with a missing or
/// corrupt bundled worker silently launch the checkout's dev-staged one
/// instead - masking exactly the packaging defect this resolution exists to
/// catch. `cargo tauri dev` builds with debug assertions on and `cargo
/// tauri build` does not, which is what `include_dev_fallback` is keyed on.
fn worker_executable_candidates(
    resource_dir: Option<PathBuf>,
    include_dev_fallback: bool,
) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(resource_dir) = resource_dir {
        candidates.push(resource_dir.join(WORKER_RESOURCE_SUBDIR).join(SIDECAR_NAME));
    }
    if include_dev_fallback {
        candidates.push(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("resources")
                .join(WORKER_RESOURCE_SUBDIR)
                .join(SIDECAR_NAME),
        );
    }
    candidates
}

/// Picks the first candidate that exists as a file, or reports every path
/// that was tried.
///
/// Tauri's own resource directory resolution collapses to the `cargo`
/// output directory in a dev run (there is no bundle to resolve a
/// `Resources` folder inside), which is why a dev build falls through to
/// the dev staging path instead of failing outright.
fn find_worker_executable(candidates: &[PathBuf]) -> Result<PathBuf, String> {
    candidates
        .iter()
        .find(|path| path.is_file())
        .cloned()
        .ok_or_else(|| {
            let tried = candidates
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join(", ");
            format!(
                "worker executable not found; tried: {tried}. Run \
                 scripts/build_worker_sidecar.sh."
            )
        })
}

/// Resolves the absolute path to the staged worker executable through
/// Tauri's resource path API, without depending on PATH or the process's
/// current directory.
fn resolve_worker_executable<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    find_worker_executable(&worker_executable_candidates(
        app.path().resource_dir().ok(),
        cfg!(debug_assertions),
    ))
}

/// Spawns the worker sidecar and blocks until it reports readiness or fails.
pub async fn spawn_worker<R: Runtime>(app: &AppHandle<R>) -> Result<WorkerHandle, String> {
    let port = pick_free_port()?;
    let token = generate_session_token();
    let worker_path = resolve_worker_executable(app)?;

    let mut std_command = StdCommand::new(&worker_path);
    std_command
        .env(SESSION_TOKEN_ENV_VAR, &token)
        .env(PORT_ENV_VAR, port.to_string())
        .stdout(Stdio::piped())
        .stdin(Stdio::piped())
        .stderr(Stdio::piped());
    detach_into_own_process_group(&mut std_command);

    let mut child = TokioCommand::from(std_command)
        .spawn()
        .map_err(|err| format!("could not spawn worker at {}: {err}", worker_path.display()))?;
    let pgid = child.id().expect("freshly spawned child has a pid");

    let stdout = child.stdout.take().expect("sidecar stdout is piped");
    let stderr = child.stderr.take().expect("sidecar stderr is piped");

    if let Err(err) = wait_for_ready(&mut child, stdout, stderr, STARTUP_TIMEOUT).await {
        terminate(child, pgid, SHUTDOWN_GRACE_PERIOD).await;
        return Err(err);
    }

    Ok(WorkerHandle {
        endpoint: WorkerEndpoint { port, token },
        child,
        pgid,
    })
}

async fn wait_for_ready(
    child: &mut Child,
    stdout: ChildStdout,
    stderr: ChildStderr,
    startup_timeout: Duration,
) -> Result<(), String> {
    let (ready_tx, mut ready_rx) = oneshot::channel::<()>();

    // Both readers run for as long as the pipe stays open, independent of
    // whether readiness has already been reported: nothing else drains
    // these pipes, and a full pipe buffer would otherwise stall the worker.
    tokio::spawn(async move {
        let mut ready_tx = Some(ready_tx);
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if line.trim() == READY_MARKER {
                if let Some(tx) = ready_tx.take() {
                    let _ = tx.send(());
                }
            }
        }
    });

    tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            log::warn!("worker stderr: {}", line.trim());
        }
    });

    let outcome = timeout(startup_timeout, async {
        tokio::select! {
            ready = &mut ready_rx => ready.map_err(|_| {
                "worker's stdout closed before it reported readiness".to_string()
            }),
            status = child.wait() => Err(format!("worker exited before becoming ready: {status:?}")),
        }
    })
    .await;

    outcome.unwrap_or_else(|_| Err("timed out waiting for worker to become ready".to_string()))
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::time::Instant;

    use super::*;

    const TEST_TIMEOUT: Duration = Duration::from_millis(500);
    const TEST_GRACE_PERIOD: Duration = Duration::from_millis(300);

    /// Spawns `sh -c script` detached into its own process group, exactly
    /// as `spawn_worker` does for the real sidecar, so tests exercise the
    /// same fork/exec/setsid path production code takes.
    fn spawn_group_leader(script: &str) -> (Child, u32) {
        let mut std_command = StdCommand::new("sh");
        std_command.arg("-c").arg(script);
        std_command.stdout(std::process::Stdio::piped());
        std_command.stderr(std::process::Stdio::piped());
        detach_into_own_process_group(&mut std_command);

        let child = TokioCommand::from(std_command)
            .spawn()
            .expect("failed to spawn test shell process");
        let pgid = child.id().expect("freshly spawned child has a pid");
        (child, pgid)
    }

    async fn read_line_matching(stdout: &mut ChildStdout, prefix: &str) -> String {
        let mut lines = BufReader::new(stdout).lines();
        loop {
            let line = timeout(Duration::from_secs(2), lines.next_line())
                .await
                .expect("timed out waiting for expected output")
                .expect("failed to read line")
                .expect("stream closed before expected output arrived");
            if let Some(rest) = line.strip_prefix(prefix) {
                return rest.trim().to_string();
            }
        }
    }

    fn process_alive(pid: i32) -> bool {
        // SAFETY: signal 0 performs no action beyond an existence/permission
        // check; it cannot affect the target process.
        unsafe { libc::kill(pid, 0) == 0 }
    }

    #[tokio::test]
    async fn ready_marker_on_stdout_resolves_ok() {
        let (mut child, pgid) =
            spawn_group_leader("echo some other line; echo INVOICE_RENAMER_WORKER_READY; sleep 30");
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();

        let result = wait_for_ready(&mut child, stdout, stderr, TEST_TIMEOUT).await;
        assert!(result.is_ok(), "expected Ok, got {result:?}");

        terminate(child, pgid, TEST_GRACE_PERIOD).await;
    }

    #[tokio::test]
    async fn early_exit_before_ready_is_an_error() {
        let (mut child, pgid) = spawn_group_leader("exit 1");
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();

        let result = wait_for_ready(&mut child, stdout, stderr, TEST_TIMEOUT).await;
        assert!(result.is_err(), "expected Err, got {result:?}");

        terminate(child, pgid, TEST_GRACE_PERIOD).await;
    }

    #[tokio::test]
    async fn closed_stdout_without_ready_marker_is_an_error() {
        // The process stays alive (never hits the child.wait() branch) but
        // closes its stdout without ever printing the marker, dropping the
        // ready sender. That must surface as an error, not be mistaken for
        // an actual readiness signal.
        let (mut child, pgid) = spawn_group_leader("exec 1>&-; sleep 30");
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();

        let result = wait_for_ready(&mut child, stdout, stderr, TEST_TIMEOUT).await;
        assert!(result.is_err(), "expected Err, got {result:?}");

        terminate(child, pgid, TEST_GRACE_PERIOD).await;
    }

    #[tokio::test]
    async fn silence_times_out_as_an_error() {
        let (mut child, pgid) = spawn_group_leader("sleep 30");
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();

        let result = wait_for_ready(&mut child, stdout, stderr, Duration::from_millis(100)).await;
        assert!(result.is_err(), "expected Err, got {result:?}");

        // Regression coverage for the startup-timeout cleanup gap: a failed
        // wait_for_ready used to leave the child (and anything it forked)
        // running forever with no handle left to reach it.
        terminate(child, pgid, TEST_GRACE_PERIOD).await;
        assert!(
            !process_alive(pgid as i32),
            "launcher should have been force-killed after a startup timeout"
        );
    }

    #[tokio::test]
    async fn terminate_kills_the_whole_group_not_just_the_launcher() {
        // Mirrors the real PyInstaller shape: a launcher process (the
        // direct child we hold) forks a worker of its own and waits on it.
        // Neither process traps SIGTERM, so both die from the group signal
        // itself rather than from one relaying it to the other.
        let (mut child, pgid) = spawn_group_leader("sleep 30 & echo \"CHILD_PID $!\"; wait");
        let mut stdout = child.stdout.take().unwrap();
        let child_pid: i32 = read_line_matching(&mut stdout, "CHILD_PID ")
            .await
            .parse()
            .expect("child pid line should be a valid pid");

        assert!(process_alive(child_pid), "forked child should be running");

        terminate(child, pgid, TEST_GRACE_PERIOD).await;

        assert!(
            !process_alive(pgid as i32),
            "launcher should no longer be running"
        );
        assert!(
            !process_alive(child_pid),
            "the launcher's forked child should not have outlived it"
        );
    }

    #[tokio::test]
    async fn graceful_exit_short_circuits_the_grace_period() {
        // The launcher has no TERM trap, so its default disposition (exit
        // immediately) applies; terminate should return well before the
        // grace period elapses instead of always waiting it out.
        let (child, pgid) = spawn_group_leader("sleep 30");

        let started = Instant::now();
        terminate(child, pgid, Duration::from_secs(10)).await;
        let elapsed = started.elapsed();

        assert!(
            elapsed < Duration::from_secs(2),
            "expected a quick graceful exit, took {elapsed:?}"
        );
        assert!(!process_alive(pgid as i32));
    }

    #[tokio::test]
    async fn ignored_sigterm_falls_back_to_sigkill() {
        let (mut child, pgid) = spawn_group_leader("trap '' TERM; echo TRAP_READY; sleep 30");
        let mut stdout = child.stdout.take().unwrap();
        // Wait for the trap to actually be installed before signaling: sent
        // too early, SIGTERM would hit the shell's still-default
        // disposition and kill it before it reaches the `trap` builtin,
        // which would defeat the point of this test.
        read_line_matching(&mut stdout, "TRAP_READY").await;
        drop(stdout);

        let started = Instant::now();
        terminate(child, pgid, TEST_GRACE_PERIOD).await;
        let elapsed = started.elapsed();

        assert!(
            elapsed >= TEST_GRACE_PERIOD,
            "expected terminate to wait out the grace period before forcing, took {elapsed:?}"
        );
        assert!(
            !process_alive(pgid as i32),
            "SIGKILL should have removed a process that ignored SIGTERM"
        );
    }

    #[tokio::test]
    async fn worker_port_stops_accepting_connections_after_termination() {
        if StdCommand::new("python3")
            .arg("--version")
            .output()
            .is_err()
        {
            eprintln!("skipping: python3 not found on PATH");
            return;
        }

        let port = pick_free_port().expect("failed to reserve a test port");
        let script = format!(
            "python3 -c 'import os, socket, sys, time; \
             print(f\"CHILD_PID {{os.getpid()}}\", flush=True); \
             s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); \
             s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); \
             s.bind((\"127.0.0.1\", int(sys.argv[1]))); \
             s.listen(1); \
             print(\"PORT_READY\", flush=True); \
             time.sleep(30)' {port} & \
             echo \"CHILD_PID $!\"; wait"
        );
        let (mut child, pgid) = spawn_group_leader(&script);
        let mut stdout = child.stdout.take().unwrap();

        read_line_matching(&mut stdout, "PORT_READY").await;
        std::net::TcpStream::connect(("127.0.0.1", port))
            .expect("worker port should accept connections once ready");

        terminate(child, pgid, TEST_GRACE_PERIOD).await;

        let refused = std::net::TcpStream::connect(("127.0.0.1", port)).is_err();
        assert!(
            refused,
            "worker port should stop accepting connections once terminate() has returned"
        );
    }

    #[test]
    fn generated_tokens_have_expected_length_and_charset() {
        let token = generate_session_token();

        assert_eq!(token.len(), TOKEN_LEN);
        assert!(token.chars().all(|c| c.is_ascii_alphanumeric()));
    }

    fn unique_temp_dir(label: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "invoice-renamer-worker-resolution-tests-{label}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir for resolution test");
        dir
    }

    #[test]
    fn candidates_check_the_resource_dir_before_the_dev_staging_path() {
        let resource_dir = std::path::PathBuf::from("/resources/of/the/packaged/app");

        let candidates = worker_executable_candidates(Some(resource_dir.clone()), true);

        assert_eq!(
            candidates[0],
            resource_dir.join(WORKER_RESOURCE_SUBDIR).join(SIDECAR_NAME)
        );
        assert!(candidates[1]
            .ends_with(std::path::Path::new(WORKER_RESOURCE_SUBDIR).join(SIDECAR_NAME)));
    }

    #[test]
    fn candidates_fall_back_to_only_the_dev_staging_path_without_a_resource_dir() {
        let candidates = worker_executable_candidates(None, true);

        assert_eq!(candidates.len(), 1);
        assert_eq!(
            candidates[0],
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("resources")
                .join(WORKER_RESOURCE_SUBDIR)
                .join(SIDECAR_NAME)
        );
    }

    #[test]
    fn candidates_omit_the_dev_staging_path_when_the_dev_fallback_is_disabled() {
        // The case a release build must hit: no dev fallback, even with no
        // resource dir at all, so a missing bundled worker fails instead of
        // silently picking up whatever is staged in the build machine's checkout.
        assert!(worker_executable_candidates(None, false).is_empty());

        let resource_dir = std::path::PathBuf::from("/resources/of/the/packaged/app");
        let candidates = worker_executable_candidates(Some(resource_dir.clone()), false);
        assert_eq!(
            candidates,
            vec![resource_dir.join(WORKER_RESOURCE_SUBDIR).join(SIDECAR_NAME)]
        );
    }

    #[test]
    fn find_worker_executable_skips_a_missing_candidate_for_one_that_exists() {
        let dir = unique_temp_dir("found");
        let missing = dir.join("missing").join(SIDECAR_NAME);
        let staged = dir.join("staged").join(SIDECAR_NAME);
        fs::create_dir_all(staged.parent().unwrap()).unwrap();
        fs::write(&staged, b"").expect("write fake staged executable");

        let resolved = find_worker_executable(&[missing, staged.clone()]);

        assert_eq!(resolved, Ok(staged));
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn find_worker_executable_reports_every_path_it_tried_when_none_exist() {
        let dir = unique_temp_dir("missing");
        let first = dir.join("resource").join(SIDECAR_NAME);
        let second = dir.join("dev").join(SIDECAR_NAME);

        let err = find_worker_executable(&[first.clone(), second.clone()])
            .expect_err("neither candidate exists");

        assert!(err.contains(&first.display().to_string()));
        assert!(err.contains(&second.display().to_string()));
        fs::remove_dir_all(&dir).ok();
    }

    /// Regression coverage for the launch-failure path introduced by
    /// resolving the worker ourselves instead of through `tauri_plugin_shell`:
    /// a resolved path that stops existing between resolution and spawn (or
    /// was never staged) must surface as a clear spawn error, not a PATH
    /// fallback to an unrelated same-named executable.
    #[tokio::test]
    async fn spawning_a_nonexistent_resolved_path_is_an_error() {
        let dir = unique_temp_dir("nonexistent-spawn");
        let missing = dir.join(SIDECAR_NAME);

        let mut std_command = StdCommand::new(&missing);
        std_command
            .stdout(Stdio::piped())
            .stdin(Stdio::piped())
            .stderr(Stdio::piped());
        detach_into_own_process_group(&mut std_command);

        let result = TokioCommand::from(std_command).spawn();

        assert!(result.is_err(), "expected Err, got {result:?}");
        fs::remove_dir_all(&dir).ok();
    }

    /// A staged worker that exists but lost its executable bit (a stripped
    /// archive, a botched `ditto`/codesign step, a filesystem that doesn't
    /// preserve permissions) must fail the same clear way as a missing one -
    /// not hang, and not have the OS fall back to some other same-named
    /// executable on PATH, since the resolved path always contains a `/` and
    /// so is never subject to a PATH search in the first place.
    #[tokio::test]
    async fn spawning_a_non_executable_resolved_path_is_an_error() {
        use std::os::unix::fs::PermissionsExt;

        let dir = unique_temp_dir("non-executable-spawn");
        let staged = dir.join(SIDECAR_NAME);
        fs::write(&staged, b"#!/bin/sh\necho should never run\n")
            .expect("write fake staged executable");
        fs::set_permissions(&staged, fs::Permissions::from_mode(0o644))
            .expect("strip the executable bit");

        let mut std_command = StdCommand::new(&staged);
        std_command
            .stdout(Stdio::piped())
            .stdin(Stdio::piped())
            .stderr(Stdio::piped());
        detach_into_own_process_group(&mut std_command);

        let result = TokioCommand::from(std_command).spawn();

        assert!(result.is_err(), "expected Err, got {result:?}");
        fs::remove_dir_all(&dir).ok();
    }
}
