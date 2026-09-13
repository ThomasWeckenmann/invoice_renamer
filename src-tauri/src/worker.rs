//! Spawns and supervises the local FastAPI worker sidecar process.

use std::net::TcpListener;
use std::time::Duration;

use rand::distr::Alphanumeric;
use rand::Rng;
use serde::Serialize;
use tauri::{AppHandle, Runtime};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc::Receiver;
use tokio::time::timeout;

pub const SESSION_TOKEN_ENV_VAR: &str = "INVOICE_RENAMER_SESSION_TOKEN";
pub const PORT_ENV_VAR: &str = "INVOICE_RENAMER_PORT";

const READY_MARKER: &str = "INVOICE_RENAMER_WORKER_READY";
const SIDECAR_NAME: &str = "invoice-renamer-worker";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const TOKEN_LEN: usize = 32;

/// Connection details the frontend needs to reach the worker directly.
#[derive(Clone, Serialize)]
pub struct WorkerEndpoint {
    pub port: u16,
    pub token: String,
}

/// A running worker process plus the endpoint used to reach it.
pub struct WorkerHandle {
    pub endpoint: WorkerEndpoint,
    child: CommandChild,
}

impl WorkerHandle {
    pub fn kill(self) {
        if let Err(err) = self.child.kill() {
            log::warn!("failed to kill worker process: {err}");
        }
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

/// Spawns the worker sidecar and blocks until it reports readiness or fails.
pub async fn spawn_worker<R: Runtime>(app: &AppHandle<R>) -> Result<WorkerHandle, String> {
    let port = pick_free_port()?;
    let token = generate_session_token();

    let (mut events, child) = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|err| format!("could not prepare worker sidecar: {err}"))?
        .env(SESSION_TOKEN_ENV_VAR, &token)
        .env(PORT_ENV_VAR, port.to_string())
        .spawn()
        .map_err(|err| format!("could not spawn worker sidecar: {err}"))?;

    wait_for_ready(&mut events, STARTUP_TIMEOUT).await?;

    Ok(WorkerHandle {
        endpoint: WorkerEndpoint { port, token },
        child,
    })
}

async fn wait_for_ready(
    events: &mut Receiver<CommandEvent>,
    startup_timeout: Duration,
) -> Result<(), String> {
    let outcome = timeout(startup_timeout, async {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    if String::from_utf8_lossy(&line).trim() == READY_MARKER {
                        return Ok(());
                    }
                }
                CommandEvent::Stderr(line) => {
                    log::warn!("worker stderr: {}", String::from_utf8_lossy(&line).trim());
                }
                CommandEvent::Error(err) => {
                    return Err(format!("worker process error: {err}"));
                }
                CommandEvent::Terminated(payload) => {
                    return Err(format!("worker exited before becoming ready: {payload:?}"));
                }
                _ => {}
            }
        }
        Err("worker process ended without becoming ready".to_string())
    })
    .await;

    outcome.unwrap_or_else(|_| Err("timed out waiting for worker to become ready".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEST_TIMEOUT: Duration = Duration::from_millis(200);

    #[tokio::test]
    async fn ready_marker_on_stdout_resolves_ok() {
        let (tx, mut rx) = tokio::sync::mpsc::channel(4);
        tx.send(CommandEvent::Stdout(b"some other line".to_vec()))
            .await
            .unwrap();
        tx.send(CommandEvent::Stdout(READY_MARKER.as_bytes().to_vec()))
            .await
            .unwrap();

        assert!(wait_for_ready(&mut rx, TEST_TIMEOUT).await.is_ok());
    }

    #[tokio::test]
    async fn early_termination_is_an_error() {
        let (tx, mut rx) = tokio::sync::mpsc::channel(4);
        tx.send(CommandEvent::Terminated(
            tauri_plugin_shell::process::TerminatedPayload {
                code: Some(1),
                signal: None,
            },
        ))
        .await
        .unwrap();

        assert!(wait_for_ready(&mut rx, TEST_TIMEOUT).await.is_err());
    }

    #[tokio::test]
    async fn process_error_is_an_error() {
        let (tx, mut rx) = tokio::sync::mpsc::channel(4);
        tx.send(CommandEvent::Error("boom".to_string()))
            .await
            .unwrap();

        assert!(wait_for_ready(&mut rx, TEST_TIMEOUT).await.is_err());
    }

    #[tokio::test]
    async fn channel_closed_without_marker_is_an_error() {
        let (tx, mut rx) = tokio::sync::mpsc::channel(4);
        drop(tx);

        assert!(wait_for_ready(&mut rx, TEST_TIMEOUT).await.is_err());
    }

    #[tokio::test]
    async fn silence_times_out_as_an_error() {
        let (_tx, mut rx) = tokio::sync::mpsc::channel(4);

        assert!(wait_for_ready(&mut rx, TEST_TIMEOUT).await.is_err());
    }

    #[test]
    fn generated_tokens_have_expected_length_and_charset() {
        let token = generate_session_token();

        assert_eq!(token.len(), TOKEN_LEN);
        assert!(token.chars().all(|c| c.is_ascii_alphanumeric()));
    }
}
