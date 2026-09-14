/** Renders the imported batch as a list of BatchItemRow entries. */

import type { BatchItem } from "../types";
import { BatchItemRow } from "./BatchItemRow";

interface BatchListProps {
  items: BatchItem[];
  onEditFilename: (id: string, filename: string) => void;
  onApprove: (id: string) => void;
  onUnapprove: (id: string) => void;
  onCancel: (id: string) => void;
  onRemove: (id: string) => void;
}

export function BatchList({
  items,
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
