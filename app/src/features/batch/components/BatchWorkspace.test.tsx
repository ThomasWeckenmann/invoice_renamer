/** Tests for the workspace's default model selection: an installed Granite
 * model is preselected until the user picks another model. */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchModels } from "../../../lib/api/models";
import type { ModelStatusEntry } from "../../../lib/api/types";
import { BatchWorkspace } from "./BatchWorkspace";

vi.mock("../../../lib/api/models", () => ({
  fetchModels: vi.fn(),
  downloadModel: vi.fn(),
  deleteModel: vi.fn(),
}));

const mockedFetchModels = vi.mocked(fetchModels);

function makeModel(id: string, displayName: string): ModelStatusEntry {
  return {
    entry: {
      id,
      display_name: displayName,
      license: "apache-2.0",
      repository: `example-org/${id}`,
      revision: "abc123",
      files: [{ path: "model.gguf", sha256: "x", size_bytes: 1 }],
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

describe("BatchWorkspace", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("preselects an installed Granite model and keeps an explicit pick", async () => {
    mockedFetchModels.mockResolvedValue([
      makeModel("qwen3-4b", "Qwen3 4B Instruct 2507"),
      makeModel("granite-3.3-2b", "Granite 3.3 2B Instruct"),
    ]);

    render(<BatchWorkspace />);

    const granite = await screen.findByRole("radio", { name: "Granite 3.3 2B Instruct" });
    const qwen = screen.getByRole("radio", { name: "Qwen3 4B Instruct 2507" });
    expect(granite).toBeChecked();
    expect(qwen).not.toBeChecked();

    fireEvent.click(qwen);

    expect(qwen).toBeChecked();
    expect(granite).not.toBeChecked();
  });

  it("selects nothing by default when Granite is not installed", async () => {
    mockedFetchModels.mockResolvedValue([makeModel("qwen3-4b", "Qwen3 4B Instruct 2507")]);

    render(<BatchWorkspace />);

    const qwen = await screen.findByRole("radio", { name: "Qwen3 4B Instruct 2507" });
    expect(qwen).not.toBeChecked();
  });
});
