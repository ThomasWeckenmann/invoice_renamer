/** Top-level batch workspace: import, model selection, progress, review,
 * approval, and the final rename-and-Undo transaction. */

import { useEffect, useState } from "react";
import "./batch.css";
import { itemHasIssue } from "../types";
import { useBatchWorkspace } from "../useBatchWorkspace";
import { useModelCatalog } from "../useModelCatalog";
import { useRenameTransaction } from "../useRenameTransaction";
import { useUnloadAfterBatch } from "../useUnloadAfterBatch";
import { BatchList } from "./BatchList";
import { BatchProgressBar } from "./BatchProgressBar";
import { ImportDropzone } from "./ImportDropzone";
import { CheckIcon, EyeIcon, FilterIcon, SparkleIcon } from "./icons";
import { MemoryStatus } from "./MemoryStatus";
import { ModelSelector } from "./ModelSelector";
import { UndoConfirmDialog } from "./UndoConfirmDialog";

export function BatchWorkspace() {
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  // Global, not per-item: read fresh at the moment each analyze/re-run fires,
  // so toggling it and re-running an item picks up the new value immediately.
  const [shortenFields, setShortenFields] = useState(true);
  const [compactView, setCompactView] = useState(true);
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [modelsCollapsed, setModelsCollapsed] = useState(false);
  const [confirmingUndo, setConfirmingUndo] = useState(false);
  // Read fresh at submit time (see handleAnalyze/handleAnalyzeItem) - later
  // toggles must only affect runs started after the toggle, not ones already
  // in flight.
  const [unloadAfterBatch, setUnloadAfterBatch] = useState(false);
  const catalog = useModelCatalog();
  const batch = useBatchWorkspace();
  const rename = useRenameTransaction();
  const unloadTracking = useUnloadAfterBatch();

  // Llama 3.2 is the default pick when it's already installed, so a
  // returning user doesn't have to reselect a model every launch.
  useEffect(() => {
    if (selectedModelId !== null) {
      return;
    }
    const llama = catalog.models.find(
      (model) =>
        model.entry.id.toLowerCase().includes("llama") &&
        model.status === "installed" &&
        model.compatible,
    );
    if (llama) {
      setSelectedModelId(llama.entry.id);
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
      batch.startAnalysis(selectedModelId, shortenFields, unloadAfterBatch ? unloadTracking : undefined);
    }
  };

  const handleAnalyzeItem = (id: string) => {
    if (selectedModelId && canAnalyzeItem) {
      // A single analyze/re-run is its own one-item run for unload-tracking
      // purposes, same as a full batch.
      batch.rerunItem(id, selectedModelId, shortenFields, unloadAfterBatch ? unloadTracking : undefined);
    }
  };

  const handleOpenUndoConfirm = () => {
    setConfirmingUndo(true);
    void rename.refreshUndoableBatches();
  };

  const handleConfirmUndo = () => {
    setConfirmingUndo(false);
    rename.undoSelectedBatch();
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
        <div className="batch-section__header">
          <h2>Model</h2>
          <div className="batch-section__header-end">
            <MemoryStatus models={catalog.models} />
            {modelsCollapsed && (
              <span className="batch-section__header-note">
                {selectedModel ? selectedModel.entry.display_name : "None selected"}
              </span>
            )}
            <button
              type="button"
              className="btn-icon batch-section__collapse"
              onClick={() => setModelsCollapsed((prev) => !prev)}
              aria-expanded={!modelsCollapsed}
              aria-label={modelsCollapsed ? "Expand models" : "Collapse models"}
              title={modelsCollapsed ? "Expand models" : "Collapse models"}
            >
              {modelsCollapsed ? "+" : "−"}
            </button>
          </div>
        </div>
        {unloadTracking.trackingError && (
          <p role="alert" className="batch-workspace__note">
            {unloadTracking.trackingError}
          </p>
        )}
        {unloadTracking.unloadError && !unloadTracking.trackingError && (
          <p role="alert" className="batch-workspace__note">
            Couldn't unload the model after the batch: {unloadTracking.unloadError}{" "}
            <button type="button" className="btn sm" onClick={unloadTracking.retryUnload}>
              Retry
            </button>
          </p>
        )}
        {!modelsCollapsed && (
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
        )}
      </section>

      <section className="batch-section batch-section--invoices">
        <div className="batch-workspace__toolbar">
          <div className="batch-workspace__toolbar-row">
            <h2>Invoices ({batch.items.length})</h2>
            <div className="batch-workspace__toolbar-actions">
              <EyeIcon className="batch-workspace__group-icon" title="View & filter" />
              <label
                className={`btn batch-workspace__toggle${
                  compactView ? " batch-workspace__toggle--on" : ""
                }${!hasProcessedItem ? " batch-workspace__toggle--disabled" : ""}`}
                title={
                  compactView
                    ? "Show full details for every row"
                    : "Hide extracted fields and run details to fit more rows"
                }
              >
                <input
                  type="checkbox"
                  className="batch-workspace__toggle-input"
                  checked={compactView}
                  disabled={!hasProcessedItem}
                  onChange={(event) => setCompactView(event.target.checked)}
                />
                <span className="batch-workspace__toggle-dot" aria-hidden="true" />
                Compact view
              </label>
              <button
                type="button"
                className={`btn${issuesOnly ? " btn--on" : ""}`}
                disabled={!issuesOnly && issueCount === 0}
                onClick={() => setIssuesOnly((prev) => !prev)}
                title={
                  issuesOnly
                    ? "Show every invoice again"
                    : "Show only invoices with warnings or failures"
                }
              >
                <FilterIcon className="btn__icon" />
                {`Warnings/errors (${issueCount})`}
              </button>
              <span className="batch-workspace__toolbar-divider" aria-hidden="true" />
              <SparkleIcon className="batch-workspace__group-icon" title="AI analysis" />
              <label
                className={`btn batch-workspace__toggle${
                  shortenFields ? " batch-workspace__toggle--on" : ""
                }`}
                title="Generate shortened seller and product names for the filename"
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
              <label
                className={`btn batch-workspace__toggle${
                  unloadAfterBatch ? " batch-workspace__toggle--on" : ""
                }`}
                title="Unload the model when the next batch finishes. Enable before starting analysis; changes do not affect batches already running."
              >
                <input
                  type="checkbox"
                  className="batch-workspace__toggle-input"
                  checked={unloadAfterBatch}
                  onChange={(event) => setUnloadAfterBatch(event.target.checked)}
                />
                <span className="batch-workspace__toggle-dot" aria-hidden="true" />
                Unload after batch
              </label>
              <button
                type="button"
                className={`btn ok${batch.isAnalyzing ? " btn--analyzing" : ""}`}
                disabled={!canAnalyzeAll}
                onClick={handleAnalyze}
                title={
                  batch.isAnalyzing ? "Analysis in progress" : "Run AI analysis on all pending invoices"
                }
              >
                <SparkleIcon
                  className={`btn__icon${batch.isAnalyzing ? " btn__icon--pulse" : ""}`}
                />
                {batch.isAnalyzing
                  ? "Analyzing…"
                  : `Analyze ${batch.pendingCount > 0 ? `(${batch.pendingCount})` : ""}`}
              </button>
              <span className="batch-workspace__toolbar-divider" aria-hidden="true" />
              <CheckIcon className="batch-workspace__group-icon" title="Batch actions" />
              <button
                type="button"
                className="btn"
                disabled={reviewCount === 0}
                onClick={batch.approveAll}
                title="Approve every invoice awaiting review"
              >
                Approve all
              </button>
              <button
                type="button"
                className="btn"
                disabled={batch.items.length === 0}
                onClick={batch.removeAll}
                title="Remove every imported invoice from the list"
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
            onClick={handleOpenUndoConfirm}
          >
            {rename.isUndoing ? "Undoing…" : "Undo…"}
          </button>
          {rename.redoAvailable && (
            <button
              type="button"
              className="btn"
              disabled={rename.isRenaming}
              onClick={rename.redoLastUndo}
              title={`Redo: reapply the rename Undo just reversed (${rename.redoCount} file${
                rename.redoCount === 1 ? "" : "s"
              })`}
            >
              {rename.isRenaming ? "Redoing…" : `Redo (${rename.redoCount})`}
            </button>
          )}
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

      {confirmingUndo && rename.selectedBatch && (
        <UndoConfirmDialog
          batch={rename.selectedBatch}
          position={rename.selectedBatchIndex + 1}
          total={rename.undoableBatches.length}
          canSelectOlder={rename.canSelectOlderBatch}
          canSelectNewer={rename.canSelectNewerBatch}
          onSelectOlder={rename.selectOlderBatch}
          onSelectNewer={rename.selectNewerBatch}
          onConfirm={handleConfirmUndo}
          onCancel={() => setConfirmingUndo(false)}
        />
      )}
    </div>
  );
}
