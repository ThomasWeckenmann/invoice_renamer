//! Tauri commands invokable from the frontend.

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
