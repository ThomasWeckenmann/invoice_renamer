/** Compact, persistent readout of model residency, live system/worker memory
 * use, and whether inference is GPU-accelerated, kept visible outside the
 * Model section's collapsible state. */

import { formatDecimalGB } from "../../../lib/format";
import type { MemorySnapshot, ModelStatusEntry } from "../../../lib/api/types";
import { useMemoryStatus } from "../useMemoryStatus";

const RAM_TOOLTIP = "System RAM used / total — includes all apps and the OS.";
const WORKER_TOOLTIP = "Python process RAM — excludes the app window and shell.";
// llama.cpp exposes one build-wide offload flag, not a per-backend memory
// counter, so this is a qualitative indicator rather than a byte figure.
// The backend can no longer tell Metal's unified memory (shared with system
// RAM) apart from a discrete GPU's own VRAM, so the tooltip can't claim
// either one specifically.
const GPU_TOOLTIP = "Inference is GPU accelerated.";

// Percent of system RAM still available, below which the RAM item is colored
// to signal urgency - not exact science, just a glance-able heads-up.
const RAM_CRITICAL_BELOW_PERCENT = 10;
const RAM_WARNING_BELOW_PERCENT = 25;

function resolveDisplayName(models: ModelStatusEntry[], modelId: string): string {
  return models.find((model) => model.entry.id === modelId)?.entry.display_name ?? modelId;
}

// Loading wording claims something is happening *right now*, which a stale
// reading can't back up - fall back to last-known residency instead.
function residencyText(snapshot: MemorySnapshot, stale: boolean, models: ModelStatusEntry[]): string {
  if (snapshot.loading && !stale) {
    const name = snapshot.loading_entry_id ? resolveDisplayName(models, snapshot.loading_entry_id) : "model";
    return `Loading ${name}…`;
  }
  if (snapshot.loaded_entry_id) {
    const name = resolveDisplayName(models, snapshot.loaded_entry_id);
    return stale ? `Loaded ${name} (last known)` : `Loaded ${name}`;
  }
  return stale ? "No model loaded (last known)" : "No model loaded";
}

function ramSeverityClass(snapshot: MemorySnapshot, stale: boolean): string {
  if (stale || snapshot.system_total_bytes <= 0) {
    return "";
  }
  const percentAvailable = (snapshot.system_available_bytes / snapshot.system_total_bytes) * 100;
  if (percentAvailable < RAM_CRITICAL_BELOW_PERCENT) {
    return " memory-status__item--critical";
  }
  if (percentAvailable < RAM_WARNING_BELOW_PERCENT) {
    return " memory-status__item--warning";
  }
  return "";
}

export function MemoryStatus({ models }: { models: ModelStatusEntry[] }) {
  const { snapshot, error, stale } = useMemoryStatus();

  if (!snapshot) {
    return (
      <span className="memory-status memory-status--unavailable">
        {error ? "Memory unavailable" : "Measuring memory…"}
      </span>
    );
  }

  const usedBytes = snapshot.system_total_bytes - snapshot.system_available_bytes;
  // A child's own title always wins the tooltip a browser shows for it, even
  // over an ancestor's - so a stale tooltip would silently hide the outer
  // "last measurement failed" one. Leaving the child's title unset instead
  // lets the browser fall back to the nearest ancestor's title (the outer
  // span's, set below), which is the standard HTML tooltip behavior.
  const itemTitle = (staticTooltip: string | undefined) => (stale ? undefined : staticTooltip);

  return (
    <span
      className={`memory-status${stale ? " memory-status--stale" : ""}`}
      title={stale ? `Last measurement failed: ${error ?? "unknown error"}` : undefined}
    >
      <span className="memory-status__item">{residencyText(snapshot, stale, models)}</span>
      <span
        className={`memory-status__item${ramSeverityClass(snapshot, stale)}`}
        title={itemTitle(RAM_TOOLTIP)}
      >
        RAM {formatDecimalGB(usedBytes)} / {formatDecimalGB(snapshot.system_total_bytes)}
      </span>
      <span className="memory-status__item" title={itemTitle(WORKER_TOOLTIP)}>
        Worker {formatDecimalGB(snapshot.worker_rss_bytes)}
      </span>
      {snapshot.gpu_in_use && (
        <span className="memory-status__item" title={itemTitle(GPU_TOOLTIP)}>
          GPU accelerated
        </span>
      )}
      {stale && <span className="memory-status__item memory-status__stale-note">stale</span>}
    </span>
  );
}
