/** Renders the imported batch as a list of BatchItemRow entries. */

import type { RenameOutcome } from "../useRenameTransaction";
import type { BatchItem } from "../types";
import { BatchItemRow } from "./BatchItemRow";

interface BatchListProps {
  items: BatchItem[];
  renameOutcomes: Record<string, RenameOutcome>;
  compact: boolean;
  onEditFilename: (id: string, filename: string) => void;
  onApprove: (id: string) => void;
  onUnapprove: (id: string) => void;
  onCancel: (id: string) => void;
  onAnalyze: (id: string) => void;
  canAnalyze: boolean;
  onRemove: (id: string) => void;
}

export function BatchList({
  items,
  renameOutcomes,
  compact,
  onEditFilename,
  onApprove,
  onUnapprove,
  onCancel,
  onAnalyze,
  canAnalyze,
  onRemove,
}: BatchListProps) {
  if (items.length === 0) {
    return <p>No invoices imported yet.</p>;
  }

  return (
    <ul className="batch-list">
      {items.map((item) => (
        <BatchItemRow
          key={item.id}
          item={item}
          renameOutcome={renameOutcomes[item.id]}
          compact={compact}
          onEditFilename={onEditFilename}
          onApprove={onApprove}
          onUnapprove={onUnapprove}
          onCancel={onCancel}
          onAnalyze={onAnalyze}
          canAnalyze={canAnalyze}
          onRemove={onRemove}
        />
      ))}
    </ul>
  );
}
