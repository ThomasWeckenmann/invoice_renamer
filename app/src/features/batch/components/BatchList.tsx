/** Renders the imported batch as a list of BatchItemRow entries. */

import type { RenameOutcome } from "../useRenameTransaction";
import type { BatchItem } from "../types";
import { BatchItemRow } from "./BatchItemRow";

interface BatchListProps {
  items: BatchItem[];
  renameOutcomes: Record<string, RenameOutcome>;
  onEditFilename: (id: string, filename: string) => void;
  onApprove: (id: string) => void;
  onUnapprove: (id: string) => void;
  onCancel: (id: string) => void;
  onRemove: (id: string) => void;
}

export function BatchList({
  items,
  renameOutcomes,
  onEditFilename,
  onApprove,
  onUnapprove,
  onCancel,
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
          onEditFilename={onEditFilename}
          onApprove={onApprove}
          onUnapprove={onUnapprove}
          onCancel={onCancel}
          onRemove={onRemove}
        />
      ))}
    </ul>
  );
}
