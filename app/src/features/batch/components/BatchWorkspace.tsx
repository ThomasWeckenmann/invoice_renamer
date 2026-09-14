/** Top-level batch workspace: import, model selection, progress, review, and approval. */

import { useState } from "react";
import "./batch.css";
import { useBatchWorkspace } from "../useBatchWorkspace";
import { useModelCatalog } from "../useModelCatalog";
import { BatchList } from "./BatchList";
import { ImportDropzone } from "./ImportDropzone";
import { ModelSelector } from "./ModelSelector";

export function BatchWorkspace() {
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const catalog = useModelCatalog();
  const batch = useBatchWorkspace();

  const selectedModel = catalog.models.find((model) => model.entry.id === selectedModelId);
  const canAnalyze = batch.pendingCount > 0 && selectedModel?.status === "installed";
  const reviewCount = batch.items.filter((item) => item.status === "needs_review").length;

  const handleAnalyze = () => {
    if (selectedModelId) {
      batch.startAnalysis(selectedModelId);
    }
  };

  return (
    <div className="batch-workspace">
      <section>
        <h2>Import</h2>
        <ImportDropzone onFilesSelected={batch.addFiles} />
      </section>

      <section>
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

      <section>
        <div className="batch-workspace__toolbar">
          <h2>Invoices ({batch.items.length})</h2>
          <button type="button" disabled={!canAnalyze} onClick={handleAnalyze}>
            Analyze {batch.pendingCount > 0 ? `(${batch.pendingCount})` : ""}
          </button>
          <button type="button" disabled={reviewCount === 0} onClick={batch.approveAll}>
            Approve all
          </button>
        </div>

        <BatchList
          items={batch.items}
          onEditFilename={batch.editFilename}
          onApprove={batch.approveItem}
          onUnapprove={batch.unapproveItem}
          onCancel={batch.cancelItem}
          onRemove={batch.removeItem}
        />

        <div className="batch-workspace__commit">
          <button type="button" disabled>
            Rename approved ({batch.approvedCount})
          </button>
          <p className="batch-workspace__note">
            Renaming isn&rsquo;t implemented yet in this preview build. Analysis and review are ready
            to use; approved items will be renamed once the file-transaction step lands.
          </p>
        </div>
      </section>
    </div>
  );
}
