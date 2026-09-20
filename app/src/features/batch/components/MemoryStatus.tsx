/** Compact, persistent readout of live system/worker/GPU memory use, kept
 * visible outside the Model section's collapsible state. */

import { formatDecimalGB } from "../../../lib/format";
import type { GpuMemorySnapshot, MemorySnapshot } from "../../../lib/api/types";
import { useMemoryStatus } from "../useMemoryStatus";

const GPU_BACKEND_LABELS: Record<string, string> = {
  mps: "GPU (Metal)",
  cuda: "GPU (CUDA)",
  rocm: "GPU (ROCm)",
};

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
}

// A failed GPU read (gpu_error set) is distinct from there simply being no
// GPU to report (no error, gpu null) - the former must stay visible as an
// explicit "unavailable" rather than silently vanishing like the latter.
function gpuDisplay(snapshot: MemorySnapshot): GpuDisplay | null {
  if (snapshot.gpu_error) {
    return { text: "GPU unavailable", title: snapshot.gpu_error };
  }
  const text = describeGpu(snapshot.gpu);
  return text ? { text } : null;
}

export function MemoryStatus() {
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

  return (
    <span
      className={`memory-status${stale ? " memory-status--stale" : ""}`}
      title={stale ? `Last measurement failed: ${error ?? "unknown error"}` : undefined}
    >
      <span className="memory-status__item">
        RAM {formatDecimalGB(usedBytes)} / {formatDecimalGB(snapshot.system_total_bytes)}
      </span>
      <span className="memory-status__item">Worker {formatDecimalGB(snapshot.worker_rss_bytes)}</span>
      {gpu && (
        <span className="memory-status__item" title={gpu.title}>
          {gpu.text}
        </span>
      )}
      {stale && <span className="memory-status__item memory-status__stale-note">stale</span>}
    </span>
  );
}
