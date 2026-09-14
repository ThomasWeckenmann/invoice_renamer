/** Tests for the model-catalog hook: fetch on mount, download/remove actions, and
 * polling while a download is in progress. */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as modelsApi from "../../lib/api/models";
import type { ModelStatusEntry } from "../../lib/api/types";
import { useModelCatalog } from "./useModelCatalog";

vi.mock("../../lib/api/models");

function makeEntry(overrides: Partial<ModelStatusEntry> = {}): ModelStatusEntry {
  return {
    entry: {
      id: "granite-3.3-2b",
      display_name: "Granite 3.3 2B",
      kind: "open_local",
      license: "apache-2.0",
      repository: "ibm-granite/granite-3.3-2b-instruct",
      revision: "abc123",
      files: [{ path: "model.safetensors", sha256: "x", size_bytes: 1000 }],
      memory_tier: "medium",
      prompt_template: null,
      provider: null,
      context_window: null,
    },
    status: "not_installed",
    compatible: true,
    compatibility_reasons: [],
    requires_cloud_key: false,
    files_done: null,
    files_total: null,
    error: null,
    ...overrides,
  };
}

describe("useModelCatalog", () => {
  afterEach(() => {
    vi.resetAllMocks();
  });

  it("fetches models on mount", async () => {
    const entry = makeEntry();
    vi.mocked(modelsApi.fetchModels).mockResolvedValue([entry]);

    const { result } = renderHook(() => useModelCatalog());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.models).toEqual([entry]);
    expect(result.current.error).toBeNull();
  });

  it("surfaces a fetch failure as an error message", async () => {
    vi.mocked(modelsApi.fetchModels).mockRejectedValue(new Error("worker unreachable"));

    const { result } = renderHook(() => useModelCatalog());

    await waitFor(() => expect(result.current.error).toBe("worker unreachable"));
  });

  it("download() merges the returned entry", async () => {
    vi.mocked(modelsApi.fetchModels).mockResolvedValue([makeEntry()]);
    const downloading = makeEntry({ status: "downloading", files_done: 1, files_total: 4 });
    vi.mocked(modelsApi.downloadModel).mockResolvedValue(downloading);

    const { result } = renderHook(() => useModelCatalog());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.download("granite-3.3-2b");
    });
    expect(result.current.models[0]).toEqual(downloading);
  });

  it("polls again while a model is downloading, picking up the install completing", async () => {
    vi.mocked(modelsApi.fetchModels).mockResolvedValueOnce([
      makeEntry({ status: "downloading", files_done: 3, files_total: 4 }),
    ]);
    vi.mocked(modelsApi.fetchModels).mockResolvedValue([makeEntry({ status: "installed" })]);

    const { result } = renderHook(() => useModelCatalog());

    await waitFor(
      () => expect(result.current.models[0]?.status).toBe("installed"),
      { timeout: 2000 },
    );
    expect(vi.mocked(modelsApi.fetchModels).mock.calls.length).toBeGreaterThan(1);
  });

  it("keeps polling after a transient failure while a download is active", async () => {
    vi.mocked(modelsApi.fetchModels)
      .mockResolvedValueOnce([makeEntry({ status: "downloading", files_done: 1, files_total: 4 })])
      .mockRejectedValueOnce(new Error("transient network error"))
      .mockResolvedValue([makeEntry({ status: "installed" })]);

    const { result } = renderHook(() => useModelCatalog());

    // The middle poll fails; a naive implementation that only reschedules on
    // a *successful* fetch would get stuck reporting "downloading" forever.
    await waitFor(() => expect(result.current.models[0]?.status).toBe("installed"), {
      timeout: 4000,
    });
    expect(vi.mocked(modelsApi.fetchModels).mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("refresh() lets the user manually recover from an initial load failure", async () => {
    vi.mocked(modelsApi.fetchModels)
      .mockRejectedValueOnce(new Error("worker unreachable"))
      .mockResolvedValue([makeEntry({ status: "installed" })]);

    const { result } = renderHook(() => useModelCatalog());
    await waitFor(() => expect(result.current.error).toBe("worker unreachable"));

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.models[0]?.status).toBe("installed");
  });

  it("remove() merges the returned entry", async () => {
    vi.mocked(modelsApi.fetchModels).mockResolvedValue([makeEntry({ status: "installed" })]);
    const removed = makeEntry({ status: "not_installed" });
    vi.mocked(modelsApi.deleteModel).mockResolvedValue(removed);

    const { result } = renderHook(() => useModelCatalog());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.remove("granite-3.3-2b");
    });
    expect(result.current.models[0]).toEqual(removed);
  });
});
