/** Owns the rename-approved transaction and Undo: sends approved items to
 * the Tauri rename command, tracks each item's outcome, and exposes Undo
 * for any past batch still on record (persisted, so it survives reloads),
 * with prev/next navigation between them. Also exposes a single-level Redo
 * that re-applies whatever the most recent Undo just reversed, cleared as
 * soon as any other rename or undo happens. */

import { useCallback, useEffect, useState } from "react";
import {
  listRenameBatches,
  renameBatch,
  undoRenameBatch,
  type BatchSummary,
  type RenameItemInput,
  type RenameItemOutcome,
  type RenameItemResult,
} from "../../lib/tauri/rename";
import { basename } from "../../lib/format";
import { displayFilename, type BatchItem } from "./types";

export type RenameOutcome =
  | { status: "renamed"; destinationPath: string }
  | { status: "failed"; message: string };

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function mergeRenameResults(
  prev: Record<string, RenameOutcome>,
  results: RenameItemOutcome[],
): Record<string, RenameOutcome> {
  const next = { ...prev };
  for (const result of results) {
    next[result.request_id] =
      result.outcome === "renamed"
        ? { status: "renamed", destinationPath: result.destination_path }
        : { status: "failed", message: result.message };
  }
  return next;
}

/** Describes any undo results that didn't fully succeed, for display. */
function describeUndoFailures(results: RenameItemResult[]): string | null {
  const failures = results.filter(
    (result): result is Extract<RenameItemResult, { outcome: "failed" }> =>
      result.outcome === "failed",
  );
  if (failures.length === 0) {
    return null;
  }
  const detail = failures.map((f) => `${f.source_path}: ${f.message}`).join("; ");
  return `${failures.length} of ${results.length} file(s) could not be restored: ${detail}`;
}

export interface UseRenameTransactionResult {
  outcomes: Record<string, RenameOutcome>;
  isRenaming: boolean;
  renameError: string | null;
  renameApproved: (items: BatchItem[]) => void;
  canUndo: boolean;
  /** Every batch still undoable, most recent first. */
  undoableBatches: BatchSummary[];
  /** Index into `undoableBatches` currently shown for Undo; 0 is most recent. */
  selectedBatchIndex: number;
  selectedBatch: BatchSummary | null;
  canSelectOlderBatch: boolean;
  canSelectNewerBatch: boolean;
  selectOlderBatch: () => void;
  selectNewerBatch: () => void;
  /** Re-fetches undoable batches (also resets to the most recent), so a
   * file repaired outside the app is reflected next time the confirm
   * dialog opens instead of only after another rename/undo/redo. */
  refreshUndoableBatches: () => Promise<void>;
  isUndoing: boolean;
  undoError: string | null;
  undoSelectedBatch: () => void;
  /** True right after an Undo, until any other rename or undo happens. */
  redoAvailable: boolean;
  redoCount: number;
  redoLastUndo: () => void;
}

export function useRenameTransaction(): UseRenameTransactionResult {
  const [outcomes, setOutcomes] = useState<Record<string, RenameOutcome>>({});
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [undoableBatches, setUndoableBatches] = useState<BatchSummary[]>([]);
  const [selectedBatchIndex, setSelectedBatchIndex] = useState(0);
  const [isUndoing, setIsUndoing] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);
  const [redoItems, setRedoItems] = useState<RenameItemInput[] | null>(null);

  const refreshUndoableBatches = useCallback(async () => {
    try {
      const batches = await listRenameBatches();
      setUndoableBatches(batches);
      setSelectedBatchIndex(0);
    } catch {
      // Best-effort UI hint only; leave the last known state on failure.
    }
  }, []);

  // Initial load on mount. State is set only in the promise callback, and
  // not after unmount; later refreshes go through refreshUndoableBatches.
  useEffect(() => {
    let active = true;
    listRenameBatches()
      .then((batches) => {
        if (!active) {
          return;
        }
        setUndoableBatches(batches);
        setSelectedBatchIndex(0);
      })
      .catch(() => {
        // Best-effort UI hint only; leave the empty initial state on failure.
      });
    return () => {
      active = false;
    };
  }, []);

  const renameApproved = useCallback(
    (items: BatchItem[]) => {
      // Excludes items already renamed: their source path no longer exists,
      // and resending it would fail preflight and abort the whole batch.
      const eligible = items.filter(
        (item) => item.status === "approved" && outcomes[item.id]?.status !== "renamed",
      );
      if (eligible.length === 0) {
        return;
      }

      setRedoItems(null);
      setIsRenaming(true);
      setRenameError(null);
      void renameBatch(
        eligible.map((item) => ({
          // Reusing the item's own id as the correlation token: it's
          // already unique per row, including across duplicate imports of
          // the same source path (which source_path alone can't tell apart).
          request_id: item.id,
          source_path: item.sourcePath,
          desired_filename: displayFilename(item),
        })),
      )
        .then((outcome) => {
          setOutcomes((prev) => mergeRenameResults(prev, outcome.results));
          if (outcome.batch_id) {
            void refreshUndoableBatches();
          }
          if (outcome.history_warning) {
            setRenameError(outcome.history_warning);
          }
        })
        .catch((err: unknown) => setRenameError(errorMessage(err)))
        .finally(() => setIsRenaming(false));
    },
    [outcomes, refreshUndoableBatches],
  );

  const selectOlderBatch = useCallback(() => {
    setSelectedBatchIndex((prev) => Math.min(prev + 1, undoableBatches.length - 1));
  }, [undoableBatches.length]);

  const selectNewerBatch = useCallback(() => {
    setSelectedBatchIndex((prev) => Math.max(prev - 1, 0));
  }, []);

  const undoSelectedBatch = useCallback(() => {
    const batch = undoableBatches[selectedBatchIndex];
    if (!batch) {
      return;
    }
    setRedoItems(null);
    setIsUndoing(true);
    setUndoError(null);
    void undoRenameBatch(batch.batch_id)
      .then((outcome) => {
        const reversed = outcome.results.filter(
          (result): result is Extract<RenameItemResult, { outcome: "renamed" }> =>
            result.outcome === "renamed",
        );
        // Captured before it's mutated below, so a file being reversed can
        // still be traced back to the row id that owned it - the only
        // place that association is available, since the backend only
        // ever deals in paths, never row ids.
        const rowIdByRenamedPath = new Map<string, string>();
        for (const [id, current] of Object.entries(outcomes)) {
          if (current.status === "renamed") {
            rowIdByRenamedPath.set(current.destinationPath, id);
          }
        }

        const reversedDestinations = new Set(reversed.map((result) => result.source_path));
        setOutcomes((prev) => {
          const next = { ...prev };
          for (const [id, current] of Object.entries(prev)) {
            if (current.status === "renamed" && reversedDestinations.has(current.destinationPath)) {
              delete next[id];
            }
          }
          return next;
        });

        if (reversed.length > 0) {
          // Undo carries the pre-undo (renamed) path as `source_path` and
          // the restored original as `destination_path` - Redo reverses
          // that once more, back to the renamed name. Reusing the row id
          // (when one owned this file) keeps the row's lock/Open target in
          // sync after Redo instead of orphaning it under a path-keyed id
          // no row will ever look up.
          setRedoItems(
            reversed.map((result) => ({
              request_id: rowIdByRenamedPath.get(result.source_path) ?? result.destination_path,
              source_path: result.destination_path,
              desired_filename: basename(result.source_path),
            })),
          );
        }

        const failureMessage = describeUndoFailures(outcome.results);
        const warnings = [failureMessage, outcome.history_warning].filter(
          (message): message is string => message !== null,
        );
        if (warnings.length > 0) {
          setUndoError(warnings.join("; "));
        }

        void refreshUndoableBatches();
      })
      .catch((err: unknown) => setUndoError(errorMessage(err)))
      .finally(() => setIsUndoing(false));
  }, [outcomes, refreshUndoableBatches, selectedBatchIndex, undoableBatches]);

  const redoLastUndo = useCallback(() => {
    if (!redoItems) {
      return;
    }
    setRedoItems(null);
    setIsRenaming(true);
    setRenameError(null);
    void renameBatch(redoItems)
      .then((outcome) => {
        setOutcomes((prev) => mergeRenameResults(prev, outcome.results));
        if (outcome.batch_id) {
          void refreshUndoableBatches();
        }
        if (outcome.history_warning) {
          setRenameError(outcome.history_warning);
        }
      })
      .catch((err: unknown) => setRenameError(errorMessage(err)))
      .finally(() => setIsRenaming(false));
  }, [redoItems, refreshUndoableBatches]);

  return {
    outcomes,
    isRenaming,
    renameError,
    renameApproved,
    canUndo: undoableBatches.length > 0,
    undoableBatches,
    selectedBatchIndex,
    selectedBatch: undoableBatches[selectedBatchIndex] ?? null,
    canSelectOlderBatch: selectedBatchIndex < undoableBatches.length - 1,
    canSelectNewerBatch: selectedBatchIndex > 0,
    selectOlderBatch,
    selectNewerBatch,
    refreshUndoableBatches,
    isUndoing,
    undoError,
    undoSelectedBatch,
    redoAvailable: redoItems !== null,
    redoCount: redoItems?.length ?? 0,
    redoLastUndo,
  };
}
