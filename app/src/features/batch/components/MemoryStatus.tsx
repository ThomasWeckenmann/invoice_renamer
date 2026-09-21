/** Compact, persistent readout of model residency and live system/worker/GPU
 * memory use, kept visible outside the Model section's collapsible state. */

import { formatDecimalGB } from "../../../lib/format";
import type { GpuMemorySnapshot, MemorySnapshot, ModelStatusEntry } from "../../../lib/api/types";
import { useMemoryStatus } from "../useMemoryStatus";

const GPU_BACKEND_LABELS: Record<string, string> = {
  mps: "GPU (Metal)",
  cuda: "GPU (CUDA)",
  rocm: "GPU (ROCm)",
};

const RAM_TOOLTIP = "System RAM used / total — includes all apps and the OS.";
const WORKER_TOOLTIP = "Python process RAM — excludes the app window and shell.";
const GPU_TOOLTIPS: Record<string, string> = {
  mps: "GPU allocations (shared RAM, incl. cache) — uses the Mac's shared memory pool.",
  cuda: "GPU tensors / reserved VRAM — reserved already includes tensors.",
  rocm: "GPU tensors / reserved VRAM — reserved already includes tensors.",
};
// Apple Silicon's GPU draws from the same pool GET /memory reports as system
// RAM, so the two readings can't be added together like a discrete GPU's can.
const APPLE_SILICON_SHARED_MEMORY_NOTE =
  "GPU memory shares system RAM; these figures are not additive.";

// Percent of system RAM still available, below which the RAM item is colored
// to signal urgency - not exact science, just a glance-able heads-up.
const RAM_CRITICAL_BELOW_PERCENT = 10;
const RAM_WARNING_BELOW_PERCENT = 25;

function describeGpu(gpu: GpuMemorySnapshot | null): string | null {
  if (!gpu) return null;
  const label = GPU_BACKEND_LABELS[gpu.backend] ?? `GPU (${gpu.backend})`;
  if (gpu.driver_allocated_bytes !== null) {
    return `${label} ${formatDecimalGB(gpu.driver_allocated_bytes)} (incl. cache)`;
  }
  if (gpu.allocated_bytes !== null) {
    const reserved =
      gpu.reserved_bytes !== null ? `, ${formatDecimalGB(gpu.reserved_bytes)} reserved` : "";
    return `${label} ${formatDecimalGB(gpu.allocated_bytes)} allocated${reserved}`;
  }
  return null;
}

interface GpuDisplay {
  text: string;
  title?: string;
  // True when `title` is the new static backend explanation, which a stale
  // reading should suppress. False/absent for gpu_error, a live per-snapshot
  // error that predates it and stays relevant regardless of a later poll's
  // staleness - see the itemTitle() comment at the call site.
  isStaticTooltip?: boolean;
}

// A failed GPU read (gpu_error set) is distinct from there simply being no
// GPU to report (no error, gpu null) - the former must stay visible as an
// explicit "unavailable" rather than silently vanishing like the latter.
function gpuDisplay(snapshot: MemorySnapshot): GpuDisplay | null {
  if (snapshot.gpu_error) {
    return { text: "GPU unavailable", title: snapshot.gpu_error };
  }
  const text = describeGpu(snapshot.gpu);
  if (!text || !snapshot.gpu) {
    return null;
  }
  const backend = snapshot.gpu.backend;
  const tooltip = GPU_TOOLTIPS[backend];
  const title =
    backend === "mps"
      ? [tooltip, APPLE_SILICON_SHARED_MEMORY_NOTE].filter(Boolean).join(" ")
      : tooltip;
  return { text, title, isStaticTooltip: true };
}

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

  const gpu = gpuDisplay(snapshot);
  const usedBytes = snapshot.system_total_bytes - snapshot.system_available_bytes;
  // A child's own title always wins the tooltip a browser shows for it, even
  // over an ancestor's - so a stale *static* tooltip would silently hide the
  // outer "last measurement failed" one. Leaving the child's title unset
  // instead lets the browser fall back to the nearest ancestor's title (the
  // outer span's, set below), which is the standard HTML tooltip behavior.
  // A live gpu_error is exempt (see GpuDisplay.isStaticTooltip): it explains
  // this specific snapshot's own failed GPU read, not the freshness of the
  // most recent poll, so it stays relevant and shown even while stale.
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
      {gpu && (
        <span
          className="memory-status__item"
          title={gpu.isStaticTooltip ? itemTitle(gpu.title) : gpu.title}
        >
          {gpu.text}
        </span>
      )}
      {stale && <span className="memory-status__item memory-status__stale-note">stale</span>}
    </span>
  );
}
