/** Tests for the memory-status hook: fetch-then-settle polling, visibility
 * pause/resume, and bounded backoff after a failed request. */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as memoryApi from "../../lib/api/memory";
import type { MemorySnapshot } from "../../lib/api/types";
import { useMemoryStatus } from "./useMemoryStatus";

vi.mock("../../lib/api/memory");

function snapshot(overrides: Partial<MemorySnapshot> = {}): MemorySnapshot {
  return {
    sampled_at: 1234,
    system_total_bytes: 16_000_000_000,
    system_available_bytes: 8_000_000_000,
    worker_rss_bytes: 1_000_000_000,
    runtime_device: null,
    loaded_entry_id: null,
    loading: false,
    loading_entry_id: null,
    gpu_in_use: false,
    ...overrides,
  };
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { value: hidden, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("useMemoryStatus", () => {
  afterEach(() => {
    vi.resetAllMocks();
    // Reset the underlying value only - no dispatch. Cleanup (testing-library's
    // afterEach, registered in src/test/setup.ts) unmounts each test's hook,
    // but hook ordering between the two isn't guaranteed, so dispatching a
    // real event here could still hit a not-yet-unmounted previous hook and
    // call the mock after it's been reset to no implementation.
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  });

  it("fetches immediately on mount", async () => {
    vi.mocked(memoryApi.fetchMemorySnapshot).mockResolvedValue(snapshot());

    const { result } = renderHook(() => useMemoryStatus());

    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    expect(result.current.error).toBeNull();
    expect(result.current.stale).toBe(false);
  });

  it("polls again after the previous request settles", async () => {
    vi.mocked(memoryApi.fetchMemorySnapshot).mockResolvedValue(snapshot());

    renderHook(() => useMemoryStatus());

    await waitFor(
      () => expect(vi.mocked(memoryApi.fetchMemorySnapshot).mock.calls.length).toBeGreaterThan(1),
      { timeout: 3000 },
    );
  });

  it("marks stale on a failed request and recovers on the next success", async () => {
    vi.mocked(memoryApi.fetchMemorySnapshot)
      .mockRejectedValueOnce(new Error("worker unreachable"))
      .mockResolvedValue(snapshot());

    const { result } = renderHook(() => useMemoryStatus());

    await waitFor(() => expect(result.current.stale).toBe(true));
    expect(result.current.error).toBe("worker unreachable");

    await waitFor(() => expect(result.current.stale).toBe(false), { timeout: 3000 });
    expect(result.current.snapshot).not.toBeNull();
  });

  it("stops polling while hidden and resumes immediately when visible again", async () => {
    setHidden(true);
    vi.mocked(memoryApi.fetchMemorySnapshot).mockResolvedValue(snapshot());

    const { result } = renderHook(() => useMemoryStatus());

    // No fetch while the workspace starts out hidden.
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(memoryApi.fetchMemorySnapshot).not.toHaveBeenCalled();

    setHidden(false);

    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
  });

  it("a request already in flight when hiding does not overwrite the fresh one from resuming", async () => {
    let resolveStale!: (value: MemorySnapshot) => void;
    vi.mocked(memoryApi.fetchMemorySnapshot).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStale = resolve;
      }),
    );

    const { result } = renderHook(() => useMemoryStatus());
    // The mount-time request above is now in flight and blocked.

    setHidden(true);
    const freshSnapshot = snapshot({ worker_rss_bytes: 2_000_000_000 });
    vi.mocked(memoryApi.fetchMemorySnapshot).mockResolvedValueOnce(freshSnapshot);
    setHidden(false); // starts a second, fresh request

    await waitFor(() => expect(result.current.snapshot).toEqual(freshSnapshot));

    // The stale first request now resolves - it must not clobber the fresh
    // state, nor schedule a second, overlapping poll chain.
    await act(async () => {
      resolveStale(snapshot({ worker_rss_bytes: 1 }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.snapshot).toEqual(freshSnapshot);
  });
});
