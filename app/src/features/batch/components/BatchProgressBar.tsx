/** Segmented summary bar showing how the batch is split across done, queued,
 * needs-review-with-warnings, and failed/cancelled items. */

import { itemHasWarnings, type BatchItem } from "../types";

interface BatchCounts {
  total: number;
  done: number;
  queued: number;
  warnings: number;
  failed: number;
}

function countBatch(items: BatchItem[]): BatchCounts {
  let done = 0;
  let queued = 0;
  let warnings = 0;
  let failed = 0;
  for (const item of items) {
    switch (item.status) {
      case "pending":
      case "queued":
      case "running":
        queued += 1;
        break;
      case "needs_review":
        if (itemHasWarnings(item)) {
          warnings += 1;
        } else {
          done += 1;
        }
        break;
      case "approved":
        done += 1;
        break;
      case "failed":
      case "cancelled":
        failed += 1;
        break;
    }
  }
  return { total: items.length, done, queued, warnings, failed };
}

export function BatchProgressBar({ items }: { items: BatchItem[] }) {
  const { total, done, queued, warnings, failed } = countBatch(items);
  if (total === 0) {
    return null;
  }

  const summary = [
    `${done}/${total} done`,
    queued > 0 ? `${queued} queued` : null,
    warnings > 0 ? `${warnings} warning${warnings === 1 ? "" : "s"}` : null,
    failed > 0 ? `${failed} failed` : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");

  const segments: Array<{ key: string; count: number; className: string }> = [
    { key: "done", count: done, className: "batch-progress__segment--done" },
    { key: "warnings", count: warnings, className: "batch-progress__segment--warnings" },
    { key: "failed", count: failed, className: "batch-progress__segment--failed" },
    { key: "queued", count: queued, className: "batch-progress__segment--queued" },
  ];

  return (
    <div className="batch-progress">
      <div className="batch-progress__bar" role="img" aria-label={summary}>
        {segments.map(
          (segment) =>
            segment.count > 0 && (
              <div
                key={segment.key}
                className={`batch-progress__segment ${segment.className}`}
                style={{ flexGrow: segment.count }}
              />
            ),
        )}
      </div>
      <p className="batch-progress__summary">{summary}</p>
    </div>
  );
}
