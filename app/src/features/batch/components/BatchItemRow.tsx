/** One imported invoice: status, extracted fields, editable filename, and
 * approve/cancel/remove actions. */

import type { ChangeEvent } from "react";
import { formatAmount } from "../../../lib/format";
import type { RenameOutcome } from "../useRenameTransaction";
import { displayFilename, type BatchItem } from "../types";

interface BatchItemRowProps {
  item: BatchItem;
  renameOutcome?: RenameOutcome;
  onEditFilename: (id: string, filename: string) => void;
  onApprove: (id: string) => void;
  onUnapprove: (id: string) => void;
  onCancel: (id: string) => void;
  onRemove: (id: string) => void;
}

const STATUS_LABELS: Record<BatchItem["status"], string> = {
  pending: "Pending",
  queued: "Queued",
  running: "Analyzing…",
  needs_review: "Needs review",
  approved: "Approved",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function BatchItemRow({
  item,
  renameOutcome,
  onEditFilename,
  onApprove,
  onUnapprove,
  onCancel,
  onRemove,
}: BatchItemRowProps) {
  const { extraction } = item.proposal ?? { extraction: null };
  const canReview = item.status === "needs_review" || item.status === "approved";
  const isRenamed = renameOutcome?.status === "renamed";

  const handleFilenameChange = (event: ChangeEvent<HTMLInputElement>) => {
    onEditFilename(item.id, event.target.value);
  };

  return (
    <li className="batch-item" data-status={item.status}>
      <div className="batch-item__header">
        <span className="batch-item__original-name">{item.file.name}</span>
        <span className="batch-item__status">{STATUS_LABELS[item.status]}</span>
      </div>

      {canReview && item.proposal && (
        <div className="batch-item__review">
          <label>
            Proposed filename
            <input
              type="text"
              value={displayFilename(item)}
              onChange={handleFilenameChange}
              disabled={isRenamed}
              aria-label={`Proposed filename for ${item.file.name}`}
            />
          </label>

          {renameOutcome?.status === "renamed" && (
            <p className="batch-item__renamed" role="status">
              Renamed to {renameOutcome.destinationPath}
            </p>
          )}
          {renameOutcome?.status === "failed" && (
            <p role="alert" className="batch-item__error">
              Rename failed: {renameOutcome.message}
            </p>
          )}

          {extraction && (
            <dl className="batch-item__fields">
              <dt>Date</dt>
              <dd>{extraction.invoice_date ?? "—"}</dd>
              <dt>Seller</dt>
              <dd>{extraction.seller ?? "—"}</dd>
              <dt>Product</dt>
              <dd>{extraction.product_summary ?? "—"}</dd>
              <dt>Amount</dt>
              <dd>{formatAmount(extraction.gross_total, extraction.currency)}</dd>
            </dl>
          )}

          {item.proposal.requires_review && (
            <p className="batch-item__flag" role="status">
              Missing required fields — please review before approving.
            </p>
          )}

          {extraction && extraction.warnings.length > 0 && (
            <ul className="batch-item__warnings">
              {extraction.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}

          <label className="batch-item__approve">
            <input
              type="checkbox"
              checked={item.status === "approved"}
              disabled={isRenamed}
              onChange={(event) => (event.target.checked ? onApprove(item.id) : onUnapprove(item.id))}
            />
            Approve
          </label>
        </div>
      )}

      {item.status === "failed" && item.error && (
        <p role="alert" className="batch-item__error">
          {item.error}
        </p>
      )}

      <div className="batch-item__actions">
        {(item.status === "queued" || item.status === "running") && (
          <button type="button" onClick={() => onCancel(item.id)}>
            Cancel
          </button>
        )}
        <button type="button" onClick={() => onRemove(item.id)}>
          Remove
        </button>
      </div>
    </li>
  );
}
