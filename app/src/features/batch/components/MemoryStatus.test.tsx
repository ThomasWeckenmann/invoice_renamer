/** Tests for the persistent memory-status readout, in particular that a
 * failed GPU measurement is shown explicitly rather than silently omitted
 * like the ordinary "no GPU to report" case. */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { MemorySnapshot } from "../../../lib/api/types";
import type { UseMemoryStatusResult } from "../useMemoryStatus";
import * as useMemoryStatusModule from "../useMemoryStatus";
import { MemoryStatus } from "./MemoryStatus";

vi.mock("../useMemoryStatus");

function snapshot(overrides: Partial<MemorySnapshot> = {}): MemorySnapshot {
  return {
    sampled_at: 1234,
    system_total_bytes: 16_000_000_000,
    system_available_bytes: 8_000_000_000,
    worker_rss_bytes: 1_000_000_000,
    runtime_device: "mps",
    gpu: null,
    gpu_error: null,
    ...overrides,
  };
}

function mockStatus(overrides: Partial<UseMemoryStatusResult> = {}) {
  vi.mocked(useMemoryStatusModule.useMemoryStatus).mockReturnValue({
    snapshot: snapshot(),
    error: null,
    stale: false,
    ...overrides,
  });
}

describe("MemoryStatus", () => {
  it("shows a measuring placeholder before the first snapshot arrives", () => {
    mockStatus({ snapshot: null });

    render(<MemoryStatus />);

    expect(screen.getByText("Measuring memory…")).toBeInTheDocument();
  });

  it("shows the GPU reading when one is available", () => {
    mockStatus({
      snapshot: snapshot({
        gpu: {
          backend: "mps",
          allocated_bytes: null,
          reserved_bytes: null,
          driver_allocated_bytes: 3_000_000_000,
        },
      }),
    });

    render(<MemoryStatus />);

    expect(screen.getByText(/GPU \(Metal\)/)).toBeInTheDocument();
  });

  it("shows GPU unavailable with the error in a tooltip when the GPU read failed", () => {
    mockStatus({
      snapshot: snapshot({ gpu: null, gpu_error: "backend rejected the query mid-unload" }),
    });

    render(<MemoryStatus />);

    const gpuItem = screen.getByText("GPU unavailable");
    expect(gpuItem).toHaveAttribute("title", "backend rejected the query mid-unload");
  });

  it("omits the GPU line entirely when there is no GPU and no error", () => {
    mockStatus({ snapshot: snapshot({ runtime_device: "cpu", gpu: null, gpu_error: null }) });

    render(<MemoryStatus />);

    expect(screen.queryByText(/GPU/)).not.toBeInTheDocument();
  });
});
