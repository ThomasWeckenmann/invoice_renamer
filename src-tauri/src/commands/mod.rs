//! Tauri commands invokable from the frontend.

pub mod rename;

use std::fs;
use std::sync::Mutex;

use serde::Serialize;
use tauri::async_runtime::JoinHandle;
use tauri::State;

use crate::worker::{WorkerEndpoint, WorkerHandle};

/// How far the worker sidecar has got through startup. The worker is booted
/// in the background rather than before the first window, so the frontend
/// can render while it starts and has to be able to tell "not up yet" from
/// "never coming".
#[derive(Default)]
pub enum WorkerStatus {
    #[default]
    Starting,
    Ready(WorkerHandle),
    Failed(String),
}

impl WorkerStatus {
    pub fn view(&self) -> WorkerStatusView {
        match self {
            WorkerStatus::Starting => WorkerStatusView::Starting,
            WorkerStatus::Ready(_) => WorkerStatusView::Ready,
            WorkerStatus::Failed(message) => WorkerStatusView::Failed {
                message: message.clone(),
            },
        }
    }
}

/// Public shape of `WorkerStatus`, without the handle itself.
#[derive(Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum WorkerStatusView {
    Starting,
    Ready,
    Failed { message: String },
}

pub struct WorkerState {
    pub status: Mutex<WorkerStatus>,
    /// The background boot task, taken at shutdown: a quit that lands while
    /// the worker is still starting has no handle to terminate yet, so it
    /// waits for this task rather than leaving the worker it produces
    /// running with nothing left to reap it.
    pub startup_task: Mutex<Option<JoinHandle<()>>>,
}

impl WorkerState {
    pub fn new() -> Self {
        WorkerState {
            status: Mutex::new(WorkerStatus::Starting),
            startup_task: Mutex::new(None),
        }
    }
}

impl Default for WorkerState {
    fn default() -> Self {
        WorkerState::new()
    }
}

/// Terminates the worker on the way out.
///
/// A quit that lands mid-startup has no handle to terminate yet - it is still
/// inside the boot task - so this waits for that task first rather than
/// exiting past a worker that is about to exist. The task is bounded by the
/// sidecar's own startup timeout and terminates the worker itself on every
/// failure path, so waiting is enough to guarantee nothing outlives the app.
pub fn shutdown_worker(state: &WorkerState) {
    let startup_task = state
        .startup_task
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .take();
    if let Some(task) = startup_task {
        let _ = tauri::async_runtime::block_on(task);
    }

    let status = std::mem::take(
        &mut *state
            .status
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()),
    );
    if let WorkerStatus::Ready(worker) = status {
        tauri::async_runtime::block_on(worker.shutdown());
    }
}

/// Reports whether the worker is still starting, ready, or failed to start,
/// so the frontend can hold the workspace behind a loading state instead of
/// showing an empty window or a misleading error.
#[tauri::command]
pub fn get_worker_status(worker: State<WorkerState>) -> WorkerStatusView {
    worker
        .status
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .view()
}

/// Returns the port and session token the frontend must use to reach the worker.
#[tauri::command]
pub fn get_worker_endpoint(worker: State<WorkerState>) -> Result<WorkerEndpoint, String> {
    match &*worker
        .status
        .lock()
        .map_err(|_| "worker state lock was poisoned".to_string())?
    {
        WorkerStatus::Ready(handle) => Ok(handle.endpoint.clone()),
        WorkerStatus::Starting => Err("worker is still starting".to_string()),
        WorkerStatus::Failed(message) => Err(message.clone()),
    }
}

/// Reads a file chosen through the native open dialog or dropped onto the
/// window, so the frontend can upload its bytes for analysis. A plain
/// browser `File` from an `<input>` or a DOM drop event never carries a
/// real filesystem path, so this is the only way the app gets both the
/// bytes and the source path a later rename needs.
#[tauri::command]
pub fn read_file_bytes(path: String) -> Result<Vec<u8>, String> {
    fs::read(&path).map_err(|err| format!("{path}: {err}"))
}

/// Opens a file with the operating system's default application (e.g.
/// Preview on macOS) - the same as double-clicking it in Finder. Uses the
/// blocking `open::that`, not a detached spawn: on Linux and Windows a
/// detached launcher reports success the instant it starts, even if it
/// then exits with "no application found" - waiting for the launcher's own
/// exit (not the opened app's) is what lets that failure surface here. Some
/// launchers don't detach and stay running until the viewer closes, so that
/// wait runs on a blocking-safe thread via `spawn_blocking` - a plain
/// (non-async) command would otherwise run the wait inline on the
/// webview's IPC thread and freeze the window for as long as it takes.
#[tauri::command]
pub async fn open_with_system_default(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || open::that(&path))
        .await
        .map_err(|err| err.to_string())?
        .map_err(|err| err.to_string())
}

#[cfg(test)]
#[cfg(unix)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    /// Regression coverage for quitting mid-startup: the worker handle only
    /// exists once the boot task has finished, so a shutdown that returned
    /// before then would let the app exit past a worker about to start.
    #[test]
    fn shutdown_waits_for_a_startup_still_in_flight() {
        let state = Arc::new(WorkerState::new());
        let finished = Arc::new(AtomicBool::new(false));

        let task_state = Arc::clone(&state);
        let task_finished = Arc::clone(&finished);
        let task = tauri::async_runtime::spawn(async move {
            tokio::time::sleep(Duration::from_millis(100)).await;
            *task_state
                .status
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) =
                WorkerStatus::Failed("never came up".to_string());
            task_finished.store(true, Ordering::SeqCst);
        });
        *state
            .startup_task
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(task);

        shutdown_worker(&state);

        assert!(
            finished.load(Ordering::SeqCst),
            "shutdown returned before the startup task finished"
        );
    }

    #[test]
    fn shutdown_is_a_no_op_when_the_worker_never_started() {
        let state = WorkerState::new();

        shutdown_worker(&state);

        assert!(matches!(
            &*state
                .status
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()),
            WorkerStatus::Starting
        ));
    }

    /// Writes an executable shell script named `name` into `dir` that exits
    /// non-zero without doing anything - standing in for a launcher that
    /// starts fine but can't find an application to open the file with
    /// (e.g. `xdg-open` exiting 3 for an unhandled file type). A launcher
    /// that never starts at all (missing from PATH) is a different failure
    /// mode that a detached spawn already reports correctly; only a
    /// launcher that starts and then fails distinguishes a blocking wait
    /// from a detached one.
    fn write_failing_launcher(dir: &std::path::Path, name: &str) {
        let path = dir.join(name);
        let mut file = fs::File::create(&path).expect("create fake launcher");
        file.write_all(b"#!/bin/sh\nexit 1\n")
            .expect("write fake launcher");
        let mut perms = fs::metadata(&path)
            .expect("stat fake launcher")
            .permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&path, perms).expect("chmod fake launcher");
    }

    /// Regression coverage for a launcher that starts but exits without
    /// opening anything. Points PATH at a directory of fake launchers
    /// (covering every candidate name `open::that` tries on Linux) that
    /// all exit non-zero, which is process-wide state, so this only runs
    /// in isolation:
    /// `cargo test --lib open_with_system_default -- --ignored --test-threads=1`.
    #[test]
    #[ignore]
    fn open_with_system_default_reports_launcher_failure() {
        let dir =
            std::env::temp_dir().join(format!("invoice-renamer-open-tests-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create fake launcher dir");
        for name in ["xdg-open", "gio", "gnome-open", "kde-open"] {
            write_failing_launcher(&dir, name);
        }

        let original_path = std::env::var_os("PATH");
        // SAFETY: run in isolation per the doc comment above, so no other
        // thread observes PATH while it's overridden.
        unsafe {
            std::env::set_var("PATH", &dir);
        }

        let result = tauri::async_runtime::block_on(open_with_system_default(
            "/nonexistent/invoice.pdf".to_string(),
        ));

        unsafe {
            match &original_path {
                Some(path) => std::env::set_var("PATH", path),
                None => std::env::remove_var("PATH"),
            }
        }
        let _ = fs::remove_dir_all(&dir);

        assert!(result.is_err());
    }
}
