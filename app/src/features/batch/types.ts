/** UI-facing state for one imported PDF as it moves through analysis, review, and approval. */

import type { FilenameProposal, RunMetrics } from "../../lib/api/types";

export type BatchItemStatus =
  | "pending"
  | "queued"
  | "running"
  | "needs_review"
  | "approved"
  | "failed"
  | "cancelled";

/** A file captured with its real filesystem path, from the native open
 * dialog or a window drop - the only sources that can provide one. */
export interface ImportedFile {
  file: File;
  sourcePath: string;
}

export interface BatchItem {
  id: string;
  file: File;
  sourcePath: string;
  status: BatchItemStatus;
  jobId: string | null;
  proposal: FilenameProposal | null;
  editedFilename: string | null;
  metrics: RunMetrics | null;
  error: string | null;
}

export function displayFilename(item: BatchItem): string {
  return item.editedFilename ?? item.proposal?.proposed_filename ?? item.file.name;
}
