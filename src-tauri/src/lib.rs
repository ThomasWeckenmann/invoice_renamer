//! Desktop shell: launches the local FastAPI worker sidecar and exposes it to the frontend.

mod commands;
mod fs_atomic;
mod history;
mod worker;

use tauri::{Manager, RunEvent, WindowEvent};

use commands::WorkerState;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_worker_endpoint,
            commands::get_worker_status,
            commands::read_file_bytes,
            commands::open_with_system_default,
            commands::rename::rename_batch,
            commands::rename::undo_last_rename_batch,
            commands::rename::get_last_batch_summary,
        ])
        .setup(|app| {
            // Booted in the background rather than awaited here: this runs
            // before the windowing event loop starts pumping, so blocking on
            // a worker that takes seconds to come up leaves the window
            // painted blank for that whole time. The frontend polls
            // `get_worker_status` and shows its own loading state instead.
            let _ = app.manage(WorkerState::new());
            let handle = app.handle().clone();
            let task = tauri::async_runtime::spawn(async move {
                let status = match worker::spawn_worker(&handle).await {
                    Ok(worker) => commands::WorkerStatus::Ready(worker),
                    Err(err) => {
                        log::error!("worker failed to start: {err}");
                        commands::WorkerStatus::Failed(err)
                    }
                };
                *handle
                    .state::<WorkerState>()
                    .status
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner()) = status;
            });
            *app.state::<WorkerState>()
                .startup_task
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(task);
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
                commands::shutdown_worker(&app_handle.state::<WorkerState>());
            }
        });
}
