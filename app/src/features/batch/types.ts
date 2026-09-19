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
  memoryWarning: string | null;
  error: string | null;
}

export function displayFilename(item: BatchItem): string {
  return item.editedFilename ?? item.proposal?.proposed_filename ?? item.file.name;
}

/** True for a needs_review item carrying extraction warnings or missing
 * required fields - the subset that needs a second look before approving. */
export function itemHasWarnings(item: BatchItem): boolean {
  const proposal = item.proposal;
  return Boolean(proposal && (proposal.extraction.warnings.length > 0 || proposal.missing_fields.length > 0));
}

/** True for an item in the batch progress bar's "warnings" or "failed"
 * bucket - used by the issues-only filter to match the same partition. */
export function itemHasIssue(item: BatchItem): boolean {
  return (
    item.status === "failed" ||
    item.status === "cancelled" ||
    (item.status === "needs_review" && itemHasWarnings(item))
  );
}
