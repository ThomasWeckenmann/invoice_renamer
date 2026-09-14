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

export interface BatchItem {
  id: string;
  file: File;
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
