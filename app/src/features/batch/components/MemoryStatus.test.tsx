/** Tests for the persistent memory-status readout: model residency wording,
 * the GPU-accelerated badge, per-stat tooltips, and RAM usage color coding. */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { MemorySnapshot, ModelStatusEntry } from "../../../lib/api/types";
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
    runtime_device: "gpu",
    loaded_entry_id: null,
    loading: false,
    loading_entry_id: null,
    gpu_in_use: false,
    ...overrides,
  };
}

function modelEntry(id: string, displayName: string): ModelStatusEntry {
  return {
    entry: {
      id,
      display_name: displayName,
      license: "apache-2.0",
      repository: `example-org/${id}`,
      revision: "a".repeat(40),
      files: [],
      memory_tier: "small",
      prompt_template: null,
      description: null,
    },
    status: "installed",
    compatible: true,
    compatibility_reasons: [],
    files_done: null,
    files_total: null,
    error: null,
  };
}

const MODELS = [modelEntry("granite-3.3-2b", "Granite 3.3 2B Instruct")];

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

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText("Measuring memory…")).toBeInTheDocument();
  });

  it("shows the GPU accelerated badge when the GPU is in use", () => {
    mockStatus({ snapshot: snapshot({ gpu_in_use: true }) });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText("GPU accelerated")).toBeInTheDocument();
  });

  it("omits the GPU line entirely when the GPU is not in use", () => {
    mockStatus({ snapshot: snapshot({ runtime_device: "cpu", gpu_in_use: false }) });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.queryByText(/GPU/)).not.toBeInTheDocument();
  });

  it('shows "No model loaded" when nothing is resident or loading', () => {
    mockStatus({ snapshot: snapshot({ loaded_entry_id: null, loading: false }) });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText("No model loaded")).toBeInTheDocument();
  });

  it("shows the resolved display name while loading", () => {
    mockStatus({
      snapshot: snapshot({ loading: true, loading_entry_id: "granite-3.3-2b" }),
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText("Loading Granite 3.3 2B Instruct…")).toBeInTheDocument();
  });

  it("falls back to the raw id when the catalog lookup misses", () => {
    mockStatus({
      snapshot: snapshot({ loading: true, loading_entry_id: "unknown-model" }),
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText("Loading unknown-model…")).toBeInTheDocument();
  });

  it("shows the resolved display name once loaded", () => {
    mockStatus({
      snapshot: snapshot({ loading: false, loaded_entry_id: "granite-3.3-2b" }),
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText("Loaded Granite 3.3 2B Instruct")).toBeInTheDocument();
  });

  it("suppresses active loading wording and marks residency as last-known while stale", () => {
    mockStatus({
      snapshot: snapshot({ loading: true, loading_entry_id: "granite-3.3-2b" }),
      stale: true,
      error: "worker unreachable",
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.queryByText(/^Loading/)).not.toBeInTheDocument();
    expect(screen.getByText("No model loaded (last known)")).toBeInTheDocument();
  });

  it("marks a stale loaded reading as last-known rather than dropping it", () => {
    mockStatus({
      snapshot: snapshot({ loading: false, loaded_entry_id: "granite-3.3-2b" }),
      stale: true,
      error: "worker unreachable",
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText("Loaded Granite 3.3 2B Instruct (last known)")).toBeInTheDocument();
  });

  it("gives each stat an explanatory tooltip", () => {
    mockStatus({ snapshot: snapshot({ gpu_in_use: true }) });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText(/^RAM/)).toHaveAttribute(
      "title",
      "System RAM used / total — includes all apps and the OS.",
    );
    expect(screen.getByText(/^Worker/)).toHaveAttribute(
      "title",
      "Python process RAM — excludes the app window and shell.",
    );
    expect(screen.getByText("GPU accelerated")).toHaveAttribute(
      "title",
      "Inference is GPU accelerated.",
    );
  });

  it("suppresses per-item tooltips while stale so the outer stale tooltip isn't shadowed", () => {
    // A child's own title always wins over an ancestor's for the tooltip a
    // browser shows on hover - so a stale per-item tooltip here would hide
    // the outer "last measurement failed" title on that item permanently.
    mockStatus({
      snapshot: snapshot({ gpu_in_use: true }),
      stale: true,
      error: "worker unreachable",
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText(/^RAM/)).not.toHaveAttribute("title");
    expect(screen.getByText(/^Worker/)).not.toHaveAttribute("title");
    expect(screen.getByText("GPU accelerated")).not.toHaveAttribute("title");
  });

  it("colors the RAM item critical when available memory is very low", () => {
    mockStatus({
      snapshot: snapshot({ system_total_bytes: 16_000_000_000, system_available_bytes: 1_000_000_000 }),
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText(/^RAM/)).toHaveClass("memory-status__item--critical");
  });

  it("colors the RAM item as a warning in the mid-low range", () => {
    mockStatus({
      snapshot: snapshot({ system_total_bytes: 16_000_000_000, system_available_bytes: 3_000_000_000 }),
    });

    render(<MemoryStatus models={MODELS} />);

    expect(screen.getByText(/^RAM/)).toHaveClass("memory-status__item--warning");
  });

  it("does not color the RAM item when memory is plentiful", () => {
    mockStatus({
      snapshot: snapshot({ system_total_bytes: 16_000_000_000, system_available_bytes: 12_000_000_000 }),
    });

    render(<MemoryStatus models={MODELS} />);

    const ramItem = screen.getByText(/^RAM/);
    expect(ramItem).not.toHaveClass("memory-status__item--warning");
    expect(ramItem).not.toHaveClass("memory-status__item--critical");
  });

  it("never colors the RAM item while the reading is stale, regardless of the last-known percentage", () => {
    mockStatus({
      snapshot: snapshot({ system_total_bytes: 16_000_000_000, system_available_bytes: 1_000_000_000 }),
      stale: true,
      error: "worker unreachable",
    });

    render(<MemoryStatus models={MODELS} />);

    const ramItem = screen.getByText(/^RAM/);
    expect(ramItem).not.toHaveClass("memory-status__item--warning");
    expect(ramItem).not.toHaveClass("memory-status__item--critical");
  });
});
