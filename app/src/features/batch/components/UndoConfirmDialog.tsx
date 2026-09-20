/** Confirmation modal for Undo: lets you page between past rename batches
 * with prev/next, and lists each file as 'current name → original name'
 * for whichever batch is currently shown. Entries whose file has moved or
 * gone missing since the batch was recorded are flagged, and Undo is
 * disabled for that batch until it can be safely applied.
 *
 * Uses the native <dialog> element via showModal() so the browser handles
 * focus trapping, Escape-to-close, and focus restoration on close - rather
 * than hand-rolling those, which is easy to get subtly wrong. */

import { useEffect, useRef } from "react";
import { basename } from "../../../lib/format";
import type { BatchSummary } from "../../../lib/tauri/rename";

interface UndoConfirmDialogProps {
  batch: BatchSummary;
  position: number;
  total: number;
  canSelectOlder: boolean;
  canSelectNewer: boolean;
  onSelectOlder: () => void;
  onSelectNewer: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function UndoConfirmDialog({
  batch,
  position,
  total,
  canSelectOlder,
  canSelectNewer,
  onSelectOlder,
  onSelectNewer,
  onConfirm,
  onCancel,
}: UndoConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const appliedAt = new Date(batch.applied_at_unix_ms).toLocaleString();
  const staleCount = batch.entries.filter((entry) => !entry.still_valid).length;

  useEffect(() => {
    dialogRef.current?.showModal();
    // Default focus to the safe action, not the (possibly destructive) confirm.
    cancelButtonRef.current?.focus();
  }, []);

  // The single place that turns a native close (Escape, backdrop click, or
  // either button below) back into the onConfirm/onCancel callbacks, keyed
  // by the returnValue each close path sets.
  const handleClose = () => {
    if (dialogRef.current?.returnValue === "confirm") {
      onConfirm();
    } else {
      onCancel();
    }
  };

  return (
    <dialog
      ref={dialogRef}
      className="modal"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="undo-confirm-title"
      onClose={handleClose}
      onClick={(event) => {
        if (event.target === dialogRef.current) {
          dialogRef.current?.close("cancel");
        }
      }}
    >
      <h3 id="undo-confirm-title">Undo a rename batch?</h3>

      {total > 1 && (
        <div className="undo-confirm__nav">
          <button
            type="button"
            className="btn-icon"
            disabled={!canSelectOlder}
            onClick={onSelectOlder}
            aria-label="Older batch"
            title="Older batch"
          >
            ‹
          </button>
          <span className="undo-confirm__nav-label">
            Batch {position} of {total} · {appliedAt}
          </span>
          <button
            type="button"
            className="btn-icon"
            disabled={!canSelectNewer}
            onClick={onSelectNewer}
            aria-label="Newer batch"
            title="Newer batch"
          >
            ›
          </button>
        </div>
      )}

      <p className="modal__lead">
        This will revert {batch.item_count} file{batch.item_count === 1 ? "" : "s"} back to their
        original names{total <= 1 ? ` (applied ${appliedAt})` : ""}.
      </p>

      <ul className="undo-confirm__list">
        {batch.entries.map((entry) => (
          <li
            key={entry.destination_path}
            className={`undo-confirm__item${entry.still_valid ? "" : " undo-confirm__item--stale"}`}
          >
            <span className="undo-confirm__name">{basename(entry.destination_path)}</span>
            <span className="undo-confirm__arrow" aria-hidden="true">
              →
            </span>
            <span className="undo-confirm__name undo-confirm__name--original">
              {basename(entry.source_path)}
            </span>
            {!entry.still_valid && (
              <span className="undo-confirm__stale-note">file moved or no longer found</span>
            )}
          </li>
        ))}
      </ul>

      <p
        className={`modal__note modal__note--warn${staleCount > 0 ? "" : " modal__note--hidden"}`}
        role={staleCount > 0 ? "alert" : undefined}
      >
        {staleCount} of {batch.item_count} file{staleCount === 1 ? "" : "s"} can no longer be
        found where expected, so this batch can't be undone until that's resolved.
      </p>

      <div className="modal__actions">
        <button
          ref={cancelButtonRef}
          type="button"
          className="btn"
          onClick={() => dialogRef.current?.close("cancel")}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn pri"
          disabled={staleCount > 0}
          onClick={() => dialogRef.current?.close("confirm")}
        >
          Undo this batch
        </button>
      </div>
    </dialog>
  );
}
