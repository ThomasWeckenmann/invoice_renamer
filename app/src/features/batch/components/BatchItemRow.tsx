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
import { CloseIcon, OpenIcon, SparkleIcon, TrashIcon } from "./icons";

interface BatchItemRowProps {
  item: BatchItem;
  renameOutcome?: RenameOutcome;
  compact?: boolean;
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

function statusLabel(item: BatchItem, isRenamed: boolean): string {
  return isRenamed && item.status === "approved" ? "Renamed" : STATUS_LABELS[item.status];
}

export function BatchItemRow({
  item,
  renameOutcome,
  compact = false,
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

  const statusIndicator =
    item.status === "running" ? <SparkleIcon className="batch-item__status-icon" /> : null;

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
        <div className="batch-item__icon-actions">
          <button type="button" className="btn-icon" onClick={handleOpen} aria-label="Open" title="Open">
            <OpenIcon />
          </button>
          {(item.status === "queued" || item.status === "running") && (
            <button
              type="button"
              className="btn-icon"
              onClick={() => onCancel(item.id)}
              aria-label="Cancel"
              title="Cancel"
            >
              <CloseIcon />
            </button>
          )}
          {showAnalyze && (
            <button
              type="button"
              className="btn-icon"
              disabled={!canAnalyze}
              onClick={() => onAnalyze(item.id)}
              aria-label={item.status === "pending" ? "Analyze" : "Re-Run"}
              title={item.status === "pending" ? "Analyze" : "Re-Run"}
            >
              <SparkleIcon />
            </button>
          )}
          <button
            type="button"
            className="btn-icon"
            onClick={() => onRemove(item.id)}
            aria-label="Remove"
            title="Remove"
          >
            <TrashIcon />
          </button>
        </div>
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
            {statusIndicator}
            {statusLabel(item, isRenamed)}
          </label>
        ) : (
          <span className="batch-item__status">
            {statusIndicator}
            {statusLabel(item, isRenamed)}
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
          <input
            type="text"
            className="batch-item__filename-input"
            value={displayFilename(item)}
            onChange={handleFilenameChange}
            disabled={isRenamed}
            aria-label={`Proposed filename for ${item.file.name}`}
          />

          {renameOutcome?.status === "failed" && (
            <p role="alert" className="batch-item__error">
              Rename failed: {renameOutcome.message}
            </p>
          )}

          {extraction && !compact && (
            <dl className="batch-item__fields">
              <div className="batch-item__field">
                <dt>Date</dt>
                <dd>
                  {extraction.invoice_date ?? "—"}
                  {xmlBadge("invoice_date")}
                </dd>
              </div>
              <div className="batch-item__field">
                <dt>Seller</dt>
                <dd>
                  {extraction.seller_short ?? extraction.seller ?? "—"}
                  {xmlBadge("seller")}
                </dd>
              </div>
              {extraction.seller_short && extraction.seller_short !== extraction.seller && (
                <div className="batch-item__field">
                  <dt>Seller (full)</dt>
                  <dd className="batch-item__fields-dd--muted">{extraction.seller}</dd>
                </div>
              )}
              <div className="batch-item__field">
                <dt>Product</dt>
                <dd>
                  {extraction.product_summary_short ?? extraction.product_summary ?? "—"}
                  {xmlBadge("product_summary")}
                </dd>
              </div>
              {extraction.product_summary_short &&
                extraction.product_summary_short !== extraction.product_summary && (
                  <div className="batch-item__field">
                    <dt>Product (full)</dt>
                    <dd className="batch-item__fields-dd--muted">
                      {extraction.product_summary}
                    </dd>
                  </div>
                )}
              <div className="batch-item__field">
                <dt>Amount</dt>
                <dd>
                  {formatAmount(extraction.gross_total, extraction.currency)}
                  {xmlBadge("gross_total")}
                </dd>
              </div>
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

          {item.metrics && !compact && (
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
    </li>
  );
}
