//! Desktop shell: launches the local FastAPI worker sidecar and exposes it to the frontend.

mod commands;
mod fs_atomic;
mod history;
mod worker;

use std::sync::Mutex;

use tauri::{Manager, RunEvent};

use commands::WorkerState;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_worker_endpoint,
            commands::read_file_bytes,
            commands::rename::rename_batch,
            commands::rename::undo_last_rename_batch,
            commands::rename::get_last_batch_summary,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let worker = tauri::async_runtime::block_on(worker::spawn_worker(&handle))?;
            let _ = app.manage::<WorkerState>(Mutex::new(Some(worker)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                let state = app_handle.state::<WorkerState>();
                let worker = state
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .take();
                if let Some(worker) = worker {
                    worker.kill();
                }
            }
        });
}
