//! Tauri commands invokable from the frontend.

pub mod rename;

use std::fs;
use std::sync::Mutex;

use tauri::State;

use crate::worker::{WorkerEndpoint, WorkerHandle};

pub type WorkerState = Mutex<Option<WorkerHandle>>;

/// Returns the port and session token the frontend must use to reach the worker.
#[tauri::command]
pub fn get_worker_endpoint(worker: State<WorkerState>) -> Result<WorkerEndpoint, String> {
    worker
        .lock()
        .map_err(|_| "worker state lock was poisoned".to_string())?
        .as_ref()
        .map(|handle| handle.endpoint.clone())
        .ok_or_else(|| "worker is not running".to_string())
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
