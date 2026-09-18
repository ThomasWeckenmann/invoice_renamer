/** Reads how far the Tauri shell has got through booting the local worker,
 * which the UI waits on before it can talk to the API at all. */

import { invoke } from "@tauri-apps/api/core";

export type WorkerStatus =
  | { state: "starting" }
  | { state: "ready" }
  | { state: "failed"; message: string };

export function fetchWorkerStatus(): Promise<WorkerStatus> {
  return invoke<WorkerStatus>("get_worker_status");
}
