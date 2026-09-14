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
