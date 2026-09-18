//! Desktop shell: launches the local FastAPI worker sidecar and exposes it to the frontend.

mod commands;
mod fs_atomic;
mod history;
mod worker;

use std::sync::Mutex;

use tauri::{Manager, RunEvent, WindowEvent};

use commands::WorkerState;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_worker_endpoint,
            commands::read_file_bytes,
            commands::open_with_system_default,
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
        // macOS otherwise leaves the app (and the worker) running with no
        // window once the last one closes, per platform convention; this
        // app has no tray/background story, so closing its one window
        // should mean quitting, matching Linux's default behavior and
        // funneling both through the same RunEvent::Exit cleanup below.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                window.app_handle().exit(0);
            }
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
                    tauri::async_runtime::block_on(worker.shutdown());
                }
            }
        });
}
