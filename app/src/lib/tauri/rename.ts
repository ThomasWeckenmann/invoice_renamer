/** Client for the Tauri rename-transaction commands: apply an approved
 * batch and undo the most recent one. Types mirror the Rust command
 * contracts in src-tauri/src/commands/rename.rs field for field. */

import { invoke } from "@tauri-apps/api/core";

export interface RenameItemInput {
  /** Echoed back on the matching result - source_path alone can't
   * correlate results to UI rows when a duplicate import sends the same
   * path more than once. */
  request_id: string;
  source_path: string;
  desired_filename: string;
}

export type RenameItemResult =
  | { outcome: "renamed"; source_path: string; destination_path: string }
  | { outcome: "failed"; source_path: string; message: string };

export type RenameItemOutcome = { request_id: string } & RenameItemResult;

export interface RenameBatchOutcome {
  batch_id: string | null;
  results: RenameItemOutcome[];
  /** Set when files were renamed but the Undo record for them failed to
   * save - `results` still reflects what actually happened on disk. */
  history_warning: string | null;
}

export interface UndoBatchOutcome {
  batch_id: string;
  results: RenameItemResult[];
  history_warning: string | null;
}

export interface LastBatchSummary {
  batch_id: string;
  applied_at_unix_ms: number;
  item_count: number;
}

export function renameBatch(items: RenameItemInput[]): Promise<RenameBatchOutcome> {
  return invoke<RenameBatchOutcome>("rename_batch", { items });
}

export function undoLastRenameBatch(): Promise<UndoBatchOutcome> {
  return invoke<UndoBatchOutcome>("undo_last_rename_batch");
}

export function getLastBatchSummary(): Promise<LastBatchSummary | null> {
  return invoke<LastBatchSummary | null>("get_last_batch_summary");
}
