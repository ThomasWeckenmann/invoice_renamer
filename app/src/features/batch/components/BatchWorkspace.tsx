/** Top-level batch workspace: import, model selection, progress, review,
 * approval, and the final rename-and-Undo transaction. */

import { useEffect, useState } from "react";
import "./batch.css";
import { itemHasIssue } from "../types";
import { useBatchWorkspace } from "../useBatchWorkspace";
import { useModelCatalog } from "../useModelCatalog";
import { useRenameTransaction } from "../useRenameTransaction";
import { BatchList } from "./BatchList";
import { BatchProgressBar } from "./BatchProgressBar";
import { ImportDropzone } from "./ImportDropzone";
import { ModelSelector } from "./ModelSelector";

export function BatchWorkspace() {
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  // Global, not per-item: read fresh at the moment each analyze/re-run fires,
  // so toggling it and re-running an item picks up the new value immediately.
  const [shortenFields, setShortenFields] = useState(true);
  const [compactView, setCompactView] = useState(true);
  const [issuesOnly, setIssuesOnly] = useState(false);
  const catalog = useModelCatalog();
  const batch = useBatchWorkspace();
  const rename = useRenameTransaction();

  // Qwen is the default pick when it's already installed, so a returning
  // user doesn't have to reselect a model every launch.
  useEffect(() => {
    if (selectedModelId !== null) {
      return;
    }
    const qwen = catalog.models.find(
      (model) =>
        model.entry.id.toLowerCase().includes("qwen") && model.status === "installed" && model.compatible,
    );
    if (qwen) {
      setSelectedModelId(qwen.entry.id);
    }
  }, [catalog.models, selectedModelId]);

  const selectedModel = catalog.models.find((model) => model.entry.id === selectedModelId);
  const canAnalyzeItem = selectedModel?.status === "installed";
  const canAnalyzeAll = batch.pendingCount > 0 && canAnalyzeItem;
  const reviewCount = batch.items.filter((item) => item.status === "needs_review").length;
  const renameableCount = batch.items.filter(
    (item) => item.status === "approved" && rename.outcomes[item.id]?.status !== "renamed",
  ).length;
  const issueCount = batch.items.filter(itemHasIssue).length;
  const visibleItems = issuesOnly ? batch.items.filter(itemHasIssue) : batch.items;
  const hasProcessedItem = batch.items.some((item) => item.proposal !== null || item.metrics !== null);

  const handleAnalyze = () => {
    if (selectedModelId) {
      batch.startAnalysis(selectedModelId, shortenFields);
    }
  };

  const handleAnalyzeItem = (id: string) => {
    if (selectedModelId && canAnalyzeItem) {
      batch.rerunItem(id, selectedModelId, shortenFields);
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

      <section className="batch-section batch-section--invoices">
        <div className="batch-workspace__toolbar">
          <div className="batch-workspace__toolbar-row">
            <h2>Invoices ({batch.items.length})</h2>
            <div className="batch-workspace__toolbar-actions">
              <button
                type="button"
                className="btn sm"
                disabled={!hasProcessedItem}
                onClick={() => setCompactView((prev) => !prev)}
              >
                {compactView ? "Full view" : "Compact view"}
              </button>
              <button
                type="button"
                className="btn sm"
                disabled={!issuesOnly && issueCount === 0}
                onClick={() => setIssuesOnly((prev) => !prev)}
              >
                {issuesOnly ? "Show all" : `Warnings/errors only (${issueCount})`}
              </button>
              <label
                className={`btn batch-workspace__toggle${
                  shortenFields ? " batch-workspace__toggle--on" : ""
                }`}
              >
                <input
                  type="checkbox"
                  className="batch-workspace__toggle-input"
                  checked={shortenFields}
                  onChange={(event) => setShortenFields(event.target.checked)}
                />
                <span className="batch-workspace__toggle-dot" aria-hidden="true" />
                Shorten Names
              </label>
              <button type="button" className="btn ok" disabled={!canAnalyzeAll} onClick={handleAnalyze}>
                Analyze {batch.pendingCount > 0 ? `(${batch.pendingCount})` : ""}
              </button>
              <button type="button" className="btn" disabled={reviewCount === 0} onClick={batch.approveAll}>
                Approve all
              </button>
              <button
                type="button"
                className="btn"
                disabled={batch.items.length === 0}
                onClick={batch.removeAll}
              >
                Remove all
              </button>
            </div>
          </div>
          <BatchProgressBar items={batch.items} />
        </div>

        <div className="batch-workspace__list-scroll">
          <BatchList
            items={visibleItems}
            renameOutcomes={rename.outcomes}
            compact={compactView}
            emptyMessage={
              issuesOnly ? "No items with warnings or failures." : "No invoices imported yet."
            }
            onEditFilename={batch.editFilename}
            onApprove={batch.approveItem}
            onUnapprove={batch.unapproveItem}
            onCancel={batch.cancelItem}
            onAnalyze={handleAnalyzeItem}
            canAnalyze={canAnalyzeItem}
            onRemove={batch.removeItem}
          />
        </div>

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
