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
  onRerun: (id: string) => void;
  canRerun: boolean;
  onRemove: (id: string) => void;
}

export function BatchList({
  items,
  renameOutcomes,
  onEditFilename,
  onApprove,
  onUnapprove,
  onCancel,
  onRerun,
  canRerun,
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
          onRerun={onRerun}
          canRerun={canRerun}
          onRemove={onRemove}
        />
      ))}
    </ul>
  );
}
