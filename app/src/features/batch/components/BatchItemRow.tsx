/** One imported invoice: status, extracted fields, editable filename, and
 * approve/cancel/remove actions. */

import { useState, type ChangeEvent } from "react";
import {
  describeExtractionSource,
  describeXmlDetection,
  formatAmount,
  formatDuration,
} from "../../../lib/format";
import { openWithSystemDefault } from "../../../lib/tauri/open";
import type { RenameOutcome } from "../useRenameTransaction";
import { displayFilename, type BatchItem } from "../types";

interface BatchItemRowProps {
  item: BatchItem;
  renameOutcome?: RenameOutcome;
  onEditFilename: (id: string, filename: string) => void;
  onApprove: (id: string) => void;
  onUnapprove: (id: string) => void;
  onCancel: (id: string) => void;
  onAnalyze: (id: string) => void;
  canAnalyze: boolean;
  onRemove: (id: string) => void;
}

const STATUS_LABELS: Record<BatchItem["status"], string> = {
  pending: "Pending",
  queued: "Queued",
  running: "Analyzing…",
  needs_review: "Approve",
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
  onAnalyze,
  canAnalyze,
  onRemove,
}: BatchItemRowProps) {
  const { extraction } = item.proposal ?? { extraction: null };
  const canReview = item.status === "needs_review" || item.status === "approved";
  const isRenamed = renameOutcome?.status === "renamed";
  // The backend attaches a memory pre-flight warning to the job at submit
  // time and never clears it, so it's still there in the final poll
  // response - stale advice once the run has moved past queued/running.
  const showMemoryWarning =
    item.memoryWarning !== null && (item.status === "queued" || item.status === "running");
  // Every state except a job already in flight, which offers Cancel instead.
  const showAnalyze = !isRenamed && item.status !== "queued" && item.status !== "running";
  const [openError, setOpenError] = useState<string | null>(null);
  const metricsSummary = item.metrics
    ? `${item.metrics.model_id} · ${formatDuration(item.metrics.total_ms)} · ` +
      `${item.metrics.pages_total} page${item.metrics.pages_total === 1 ? "" : "s"}`
    : "";
  const xmlBadge = (fieldName: string) =>
    extraction?.evidence[fieldName]?.xml_field ? (
      <span className="batch-item__field-source" title="From embedded invoice XML">
        XML
      </span>
    ) : null;

  const handleFilenameChange = (event: ChangeEvent<HTMLInputElement>) => {
    onEditFilename(item.id, event.target.value);
  };

  const handleOpen = () => {
    setOpenError(null);
    const path = isRenamed ? renameOutcome.destinationPath : item.sourcePath;
    openWithSystemDefault(path).catch((err: unknown) => {
      setOpenError(err instanceof Error ? err.message : String(err));
    });
  };

  return (
    <li className="batch-item" data-status={item.status}>
      <div className="batch-item__header">
        <span className="batch-item__original-name">{item.file.name}</span>
        {canReview ? (
          <label
            className={`batch-item__status batch-item__status--toggle${
              isRenamed ? " batch-item__status--locked" : ""
            }`}
          >
            <input
              type="checkbox"
              className="batch-item__status-input"
              checked={item.status === "approved"}
              disabled={isRenamed}
              onChange={(event) =>
                event.target.checked ? onApprove(item.id) : onUnapprove(item.id)
              }
              aria-label={`${item.status === "approved" ? "Unapprove" : "Approve"} ${item.file.name}`}
            />
            <span className="batch-item__status-dot" aria-hidden="true" />
            {STATUS_LABELS[item.status]}
          </label>
        ) : (
          <span className="batch-item__status">
            <span className="batch-item__status-dot" aria-hidden="true" />
            {STATUS_LABELS[item.status]}
          </span>
        )}
      </div>

      {showMemoryWarning && (
        <p className="batch-item__memory-warning" role="status">
          {item.memoryWarning}
        </p>
      )}

      {canReview && item.proposal && (
        <div className="batch-item__review">
          <label className="batch-item__filename-field">
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
              <dd>
                {extraction.invoice_date ?? "—"}
                {xmlBadge("invoice_date")}
              </dd>
              <dt>Seller</dt>
              <dd>
                {extraction.seller ?? "—"}
                {xmlBadge("seller")}
              </dd>
              <dt>Product</dt>
              <dd>
                {extraction.product_summary ?? "—"}
                {xmlBadge("product_summary")}
              </dd>
              <dt>Amount</dt>
              <dd>
                {formatAmount(extraction.gross_total, extraction.currency)}
                {xmlBadge("gross_total")}
              </dd>
            </dl>
          )}

          {item.proposal.missing_fields.length > 0 && (
            <p className="batch-item__flag" role="status">
              Missing required field{item.proposal.missing_fields.length > 1 ? "s" : ""}:{" "}
              {item.proposal.missing_fields.join(", ")} — please review before approving.
            </p>
          )}

          {extraction && extraction.warnings.length > 0 && (
            <>
              <p className="batch-item__flag" role="status">
                Review the warnings below before approving.
              </p>
              <ul className="batch-item__warnings">
                {extraction.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </>
          )}

          {item.metrics && (
            <details className="batch-item__metrics">
              <summary>
                <span className="batch-item__metrics-label">Run details</span>
                <span className="batch-item__metrics-summary">{metricsSummary}</span>
              </summary>
              <dl>
                <div>
                  <dt>Source</dt>
                  <dd>{describeExtractionSource(item.metrics)}</dd>
                </div>
                {describeXmlDetection(item.metrics) && (
                  <div>
                    <dt>XML</dt>
                    <dd>{describeXmlDetection(item.metrics)}</dd>
                  </div>
                )}
                <div>
                  <dt>Model</dt>
                  <dd>
                    {item.metrics.inference_ran
                      ? `${item.metrics.model_id}${
                          item.metrics.model_revision ? ` @ ${item.metrics.model_revision}` : ""
                        }`
                      : "Not used"}
                  </dd>
                </div>
                <div>
                  <dt>Total time</dt>
                  <dd>{formatDuration(item.metrics.total_ms)}</dd>
                </div>
                <div>
                  <dt>Inference time</dt>
                  <dd>{formatDuration(item.metrics.inference_ms)}</dd>
                </div>
                <div>
                  <dt>Pages</dt>
                  <dd>
                    {item.metrics.pages_total}
                    {item.metrics.pages_ocr.length > 0
                      ? ` (${item.metrics.pages_ocr.length} via OCR)`
                      : ""}
                  </dd>
                </div>
                {(item.metrics.input_tokens !== null || item.metrics.output_tokens !== null) && (
                  <div>
                    <dt>Tokens</dt>
                    <dd>
                      {item.metrics.input_tokens ?? "—"} in / {item.metrics.output_tokens ?? "—"} out
                      {item.metrics.tokens_per_second !== null
                        ? ` (${item.metrics.tokens_per_second.toFixed(1)} tok/s)`
                        : ""}
                    </dd>
                  </div>
                )}
              </dl>
            </details>
          )}
        </div>
      )}

      {item.status === "failed" && item.error && (
        <p role="alert" className="batch-item__error">
          {item.error}
        </p>
      )}

      {openError && (
        <p role="alert" className="batch-item__error">
          Couldn't open the file: {openError}
        </p>
      )}

      <div className="batch-item__actions">
        <button type="button" className="btn sm" onClick={handleOpen}>
          Open
        </button>
        {(item.status === "queued" || item.status === "running") && (
          <button type="button" className="btn sm" onClick={() => onCancel(item.id)}>
            Cancel
          </button>
        )}
        {showAnalyze && (
          <button
            type="button"
            className="btn sm"
            disabled={!canAnalyze}
            onClick={() => onAnalyze(item.id)}
          >
            {item.status === "pending" ? "Analyze" : "Re-Run"}
          </button>
        )}
        <button type="button" className="btn sm" onClick={() => onRemove(item.id)}>
          Remove
        </button>
      </div>
    </li>
  );
}
