/** Owns the rename-approved transaction and Undo: sends approved items to
 * the Tauri rename command, tracks each item's outcome, and exposes Undo
 * for the most recently applied batch (persisted, so it survives reloads). */

import { useCallback, useEffect, useState } from "react";
import {
  getLastBatchSummary,
  renameBatch,
  undoLastRenameBatch,
  type RenameItemResult,
} from "../../lib/tauri/rename";
import { displayFilename, type BatchItem } from "./types";

export type RenameOutcome =
  | { status: "renamed"; destinationPath: string }
  | { status: "failed"; message: string };

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
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
  isUndoing: boolean;
  undoError: string | null;
  undoLastBatch: () => void;
}

export function useRenameTransaction(): UseRenameTransactionResult {
  const [outcomes, setOutcomes] = useState<Record<string, RenameOutcome>>({});
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [canUndo, setCanUndo] = useState(false);
  const [isUndoing, setIsUndoing] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);

  const refreshUndoAvailability = useCallback(async () => {
    try {
      const summary = await getLastBatchSummary();
      setCanUndo(summary !== null);
    } catch {
      // Best-effort UI hint only; leave the last known state on failure.
    }
  }, []);

  useEffect(() => {
    void refreshUndoAvailability();
  }, [refreshUndoAvailability]);

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
          setOutcomes((prev) => {
            const next = { ...prev };
            for (const result of outcome.results) {
              next[result.request_id] =
                result.outcome === "renamed"
                  ? { status: "renamed", destinationPath: result.destination_path }
                  : { status: "failed", message: result.message };
            }
            return next;
          });
          if (outcome.batch_id) {
            setCanUndo(true);
          }
          if (outcome.history_warning) {
            setRenameError(outcome.history_warning);
          }
        })
        .catch((err: unknown) => setRenameError(errorMessage(err)))
        .finally(() => setIsRenaming(false));
    },
    [outcomes],
  );

  const undoLastBatch = useCallback(() => {
    setIsUndoing(true);
    setUndoError(null);
    void undoLastRenameBatch()
      .then((outcome) => {
        const reversedDestinations = new Set(
          outcome.results
            .filter((result) => result.outcome === "renamed")
            .map((result) => result.source_path),
        );
        setOutcomes((prev) => {
          const next = { ...prev };
          for (const [id, current] of Object.entries(prev)) {
            if (current.status === "renamed" && reversedDestinations.has(current.destinationPath)) {
              delete next[id];
            }
          }
          return next;
        });

        const failureMessage = describeUndoFailures(outcome.results);
        const warnings = [failureMessage, outcome.history_warning].filter(
          (message): message is string => message !== null,
        );
        if (warnings.length > 0) {
          setUndoError(warnings.join("; "));
        }

        void refreshUndoAvailability();
      })
      .catch((err: unknown) => setUndoError(errorMessage(err)))
      .finally(() => setIsUndoing(false));
  }, [refreshUndoAvailability]);

  return {
    outcomes,
    isRenaming,
    renameError,
    renameApproved,
    canUndo,
    isUndoing,
    undoError,
    undoLastBatch,
  };
}
