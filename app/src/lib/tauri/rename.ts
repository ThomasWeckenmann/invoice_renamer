/** Client for the Tauri rename-transaction commands: apply an approved
 * batch and undo any past one still on record. Types mirror the Rust
 * command contracts in src-tauri/src/commands/rename.rs field for field. */

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

/** One file's source/destination paths from an undoable batch, for
 * previewing what Undo will do without performing it. `still_valid` is a
 * preflight check run ahead of time (same rule the actual Undo enforces),
 * so a file moved or deleted since the batch was recorded can be flagged
 * before the user commits to undoing it. */
export interface BatchSummaryEntry {
  source_path: string;
  destination_path: string;
  still_valid: boolean;
}

export interface BatchSummary {
  batch_id: string;
  applied_at_unix_ms: number;
  item_count: number;
  entries: BatchSummaryEntry[];
}

export function renameBatch(items: RenameItemInput[]): Promise<RenameBatchOutcome> {
  return invoke<RenameBatchOutcome>("rename_batch", { items });
}

export function undoRenameBatch(batchId: string): Promise<UndoBatchOutcome> {
  return invoke<UndoBatchOutcome>("undo_rename_batch", { batchId });
}

export function listRenameBatches(): Promise<BatchSummary[]> {
  return invoke<BatchSummary[]>("list_rename_batches");
}
