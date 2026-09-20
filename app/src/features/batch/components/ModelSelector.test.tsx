/** Tests for the model selector's error-recovery affordance and its
 * persistent description/download-size details. */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ModelStatusEntry } from "../../../lib/api/types";
import { ModelSelector } from "./ModelSelector";

function makeModel(overrides: Partial<ModelStatusEntry> = {}): ModelStatusEntry {
  return {
    entry: {
      id: "qwen3-0.6b",
      display_name: "Qwen3 0.6B",
      license: "apache-2.0",
      repository: "Qwen/Qwen3-0.6B",
      revision: "abc123",
      files: [{ path: "model.safetensors", sha256: "x", size_bytes: 1_519_182_365 }],
      memory_tier: "small",
      prompt_template: null,
      description: "Faster inference and lower memory use",
    },
    status: "not_installed",
    compatible: true,
    compatibility_reasons: [],
    files_done: null,
    files_total: null,
    error: null,
    ...overrides,
  };
}

const noop = () => {};

describe("ModelSelector", () => {
  it("shows a retry control on error and wires it to onRefresh", () => {
    const onRefresh = vi.fn();
    render(
      <ModelSelector
        models={[]}
        loading={false}
        error="worker unreachable"
        selectedModelId={null}
        onSelect={() => {}}
        onDownload={() => {}}
        onRemove={() => {}}
        onRefresh={onRefresh}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("worker unreachable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("shows the description and decimal-GB download size for a not-yet-installed model", () => {
    render(
      <ModelSelector
        models={[makeModel()]}
        loading={false}
        error={null}
        selectedModelId={null}
        onSelect={noop}
        onDownload={noop}
        onRemove={noop}
        onRefresh={noop}
      />,
    );

    expect(screen.getByText("Faster inference and lower memory use")).toBeInTheDocument();
    expect(screen.getByText("1.5 GB download")).toBeInTheDocument();
    // The Download button stays concise - the size lives in the row, not the button.
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
  });

  it("keeps the description and size visible once the model is installed", () => {
    render(
      <ModelSelector
        models={[makeModel({ status: "installed" })]}
        loading={false}
        error={null}
        selectedModelId={null}
        onSelect={noop}
        onDownload={noop}
        onRemove={noop}
        onRefresh={noop}
      />,
    );

    expect(screen.getByText("Faster inference and lower memory use")).toBeInTheDocument();
    expect(screen.getByText("1.5 GB download")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });
});
