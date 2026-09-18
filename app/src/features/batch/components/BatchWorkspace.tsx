/** Top-level batch workspace: import, model selection, progress, review,
 * approval, and the final rename-and-Undo transaction. */

import { useState } from "react";
import "./batch.css";
import { useBatchWorkspace } from "../useBatchWorkspace";
import { useModelCatalog } from "../useModelCatalog";
import { useRenameTransaction } from "../useRenameTransaction";
import { BatchList } from "./BatchList";
import { ImportDropzone } from "./ImportDropzone";
import { ModelSelector } from "./ModelSelector";

export function BatchWorkspace() {
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const catalog = useModelCatalog();
  const batch = useBatchWorkspace();
  const rename = useRenameTransaction();

  const selectedModel = catalog.models.find((model) => model.entry.id === selectedModelId);
  const canAnalyzeItem = selectedModel?.status === "installed";
  const canAnalyzeAll = batch.pendingCount > 0 && canAnalyzeItem;
  const reviewCount = batch.items.filter((item) => item.status === "needs_review").length;
  const renameableCount = batch.items.filter(
    (item) => item.status === "approved" && rename.outcomes[item.id]?.status !== "renamed",
  ).length;

  const handleAnalyze = () => {
    if (selectedModelId) {
      batch.startAnalysis(selectedModelId);
    }
  };

  const handleAnalyzeItem = (id: string) => {
    if (selectedModelId && canAnalyzeItem) {
      batch.rerunItem(id, selectedModelId);
    }
  };

  return (
    <div className="batch-workspace">
      <section className="batch-section">
        <h2>Import</h2>
        <ImportDropzone
          onFilesImported={(files) => {
            setImportError(null);
            batch.addFiles(files);
          }}
          onImportError={setImportError}
        />
        {importError && (
          <p role="alert" className="batch-workspace__note">
            {importError}
          </p>
        )}
      </section>

      <section className="batch-section">
        <h2>Model</h2>
        <ModelSelector
          models={catalog.models}
          loading={catalog.loading}
          error={catalog.error}
          selectedModelId={selectedModelId}
          onSelect={setSelectedModelId}
          onDownload={(modelId) => void catalog.download(modelId)}
          onRemove={(modelId) => void catalog.remove(modelId)}
          onRefresh={() => void catalog.refresh()}
        />
      </section>

      <section className="batch-section">
        <div className="batch-workspace__toolbar">
          <h2>Invoices ({batch.items.length})</h2>
          <div className="batch-workspace__toolbar-actions">
            <button type="button" className="btn" disabled={!canAnalyzeAll} onClick={handleAnalyze}>
              Analyze {batch.pendingCount > 0 ? `(${batch.pendingCount})` : ""}
            </button>
            <button type="button" className="btn" disabled={reviewCount === 0} onClick={batch.approveAll}>
              Approve all
            </button>
          </div>
        </div>

        <BatchList
          items={batch.items}
          renameOutcomes={rename.outcomes}
          onEditFilename={batch.editFilename}
          onApprove={batch.approveItem}
          onUnapprove={batch.unapproveItem}
          onCancel={batch.cancelItem}
          onAnalyze={handleAnalyzeItem}
          canAnalyze={canAnalyzeItem}
          onRemove={batch.removeItem}
        />

        <div className="batch-workspace__commit">
          <button
            type="button"
            className="btn"
            disabled={!rename.canUndo || rename.isUndoing}
            onClick={rename.undoLastBatch}
          >
            {rename.isUndoing ? "Undoing…" : "Undo last batch"}
          </button>
          <span className="batch-workspace__commit-spacer" />
          <button
            type="button"
            className="btn pri"
            disabled={renameableCount === 0 || rename.isRenaming}
            onClick={() => rename.renameApproved(batch.items)}
          >
            {rename.isRenaming ? "Renaming…" : `Rename approved (${renameableCount})`}
          </button>
          {rename.renameError && (
            <p role="alert" className="batch-workspace__note">
              {rename.renameError}
            </p>
          )}
          {rename.undoError && (
            <p role="alert" className="batch-workspace__note">
              {rename.undoError}
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
