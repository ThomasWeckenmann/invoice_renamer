/** Model picker: list of local models with install status, download/remove
 * controls, and selection for the active batch run. */

import { formatBytes } from "../../../lib/format";
import type { ModelStatusEntry } from "../../../lib/api/types";

interface ModelSelectorProps {
  models: ModelStatusEntry[];
  loading: boolean;
  error: string | null;
  selectedModelId: string | null;
  onSelect: (modelId: string) => void;
  onDownload: (modelId: string) => void;
  onRemove: (modelId: string) => void;
  onRefresh: () => void;
}

function ModelRow({
  model,
  selected,
  onSelect,
  onDownload,
  onRemove,
}: {
  model: ModelStatusEntry;
  selected: boolean;
  onSelect: () => void;
  onDownload: () => void;
  onRemove: () => void;
}) {
  const { entry, status } = model;

  return (
    <div className="model-row" data-status={status}>
      <label>
        <input
          type="radio"
          name="selected-model"
          checked={selected}
          disabled={status !== "installed" || !model.compatible}
          onChange={onSelect}
        />
        {entry.display_name}
      </label>

      {status === "not_installed" && (
        <button type="button" onClick={onDownload} disabled={!model.compatible}>
          Download
          {entry.files.length > 0
            ? ` (${formatBytes(entry.files.reduce((sum, file) => sum + file.size_bytes, 0))})`
            : ""}
        </button>
      )}
      {status === "downloading" && (
        <span className="model-row__progress">
          Downloading{" "}
          {model.files_total ? `(${model.files_done ?? 0}/${model.files_total} files)` : "…"}
        </span>
      )}
      {status === "verification_failed" && (
        <span className="model-row__error">
          {model.error ?? "verification failed"}
          <button type="button" onClick={onDownload}>
            Retry
          </button>
        </span>
      )}
      {status === "installed" && (
        <button type="button" onClick={onRemove}>
          Remove
        </button>
      )}

      {!model.compatible && model.compatibility_reasons.length > 0 && (
        <ul className="model-row__reasons">
          {model.compatibility_reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ModelSelector({
  models,
  loading,
  error,
  selectedModelId,
  onSelect,
  onDownload,
  onRemove,
  onRefresh,
}: ModelSelectorProps) {
  if (loading && models.length === 0) {
    return <p>Loading models…</p>;
  }

  return (
    <div className="model-selector">
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={onRefresh}>
            Retry
          </button>
        </p>
      )}

      <fieldset>
        <legend>Models</legend>
        {models.length === 0 ? (
          <p>No models available.</p>
        ) : (
          models.map((model) => (
            <ModelRow
              key={model.entry.id}
              model={model}
              selected={model.entry.id === selectedModelId}
              onSelect={() => onSelect(model.entry.id)}
              onDownload={() => onDownload(model.entry.id)}
              onRemove={() => onRemove(model.entry.id)}
            />
          ))
        )}
      </fieldset>
    </div>
  );
}
