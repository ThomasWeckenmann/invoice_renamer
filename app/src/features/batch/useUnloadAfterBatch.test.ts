/** Tests for the "unload after batch" run tracker: it must fire exactly one
 * unload once every tracked submission settles - including one still
 * uploading with no job id yet - keep watching jobs through polling errors,
 * and handle a busy (409) or failing unload without hanging. */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as analysesApi from "../../lib/api/analyses";
import { ApiError } from "../../lib/api/client";
import * as memoryApi from "../../lib/api/memory";
import type { AnalysisJobView } from "../../lib/api/types";
import { useUnloadAfterBatch, type UseUnloadAfterBatchResult } from "./useUnloadAfterBatch";

vi.mock("../../lib/api/analyses");
vi.mock("../../lib/api/memory");

function job(overrides: Partial<AnalysisJobView> = {}): AnalysisJobView {
  return {
    id: "job-1",
    model_id: "granite-3.3-2b",
    original_filename: "invoice.pdf",
    status: "running",
    proposal: null,
    metrics: null,
    error: null,
    ...overrides,
  };
}

/** A submission whose upload has already resolved to a job id by the time
 * it's tracked - the common case in most of these tests. */
function trackJob(result: { current: UseUnloadAfterBatchResult }, jobId: string) {
  const token = result.current.beginSubmission();
  result.current.settleSubmission(token, jobId);
}

describe("useUnloadAfterBatch", () => {
  afterEach(() => {
    vi.resetAllMocks();
  });

  it("requests an unload once the single tracked job completes", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it("waits for every tracked job to settle before unloading", async () => {
    vi.mocked(analysesApi.fetchJob).mockImplementation((jobId: string) =>
      Promise.resolve(job({ id: jobId, status: jobId === "job-1" ? "running" : "completed" })),
    );
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => {
      trackJob(result, "job-1");
      trackJob(result, "job-2");
    });

    // job-2 is already terminal but job-1 is still running - must not fire yet.
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(memoryApi.unloadModel).not.toHaveBeenCalled();

    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ id: "job-1", status: "completed" }));

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it("does not fire while a sibling submission is still uploading with no job id yet", async () => {
    // Regression test: a batch of two files where job-1's upload+submission
    // settles and completes quickly, but job-2's upload is still in flight.
    // Before job ids alone were tracked, the outstanding set would look
    // empty in that gap and fire a premature unload, forcing a reload for
    // job-2's still-pending analysis.
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());

    let pendingToken = "";
    act(() => {
      pendingToken = result.current.beginSubmission(); // job-2's upload starts...
      trackJob(result, "job-1"); // ...while job-1's has already resolved
    });

    await waitFor(() => expect(analysesApi.fetchJob).toHaveBeenCalledWith("job-1"));
    // Give job-1's poll time to resolve "completed" - the batch must still
    // not be considered done while job-2's upload hasn't settled.
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(memoryApi.unloadModel).not.toHaveBeenCalled();

    act(() => result.current.settleSubmission(pendingToken, "job-2")); // upload finishes

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it("a pending submission that's definitely rejected still lets the batch complete", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    let token = "";
    act(() => {
      token = result.current.beginSubmission();
    });

    act(() => result.current.settleSubmission(token, null)); // e.g. upload failed outright

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it("a failed or cancelled job still counts as settled", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "failed", error: "boom" }));
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it("a polling failure keeps the job tracked instead of counting as settled", async () => {
    vi.mocked(analysesApi.fetchJob)
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1), { timeout: 3000 });
    expect(vi.mocked(analysesApi.fetchJob).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("retries after a 409 (busy) unload response instead of giving up", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel)
      .mockRejectedValueOnce(new ApiError(409, "cannot unload while a job is queued or running"))
      .mockResolvedValueOnce(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(2), { timeout: 3000 });
    expect(result.current.unloadError).toBeNull();
  });

  it("requestUnload is a no-op while any submission is still outstanding", async () => {
    // Regression test: an unload attempt (whether the initial one, a
    // scheduled busy-retry, or a manual retry click) must be re-checked at
    // the moment it actually runs, not just when it was scheduled - a new
    // submission can start in between.
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => {
      result.current.beginSubmission(); // still pending, never settled
    });

    act(() => result.current.retryUnload());

    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(memoryApi.unloadModel).not.toHaveBeenCalled();
  });

  it("a scheduled busy-retry does not fire once a new submission has started", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel)
      .mockRejectedValueOnce(new ApiError(409, "cannot unload while a job is queued or running"))
      .mockResolvedValue(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    // job-1 settles, triggers an unload attempt that comes back busy, and
    // schedules a retry BUSY_RETRY_DELAY_MS (800ms) later.
    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1), { timeout: 3000 });

    let newToken = "";
    act(() => {
      newToken = result.current.beginSubmission(); // a new run starts meanwhile
    });

    // Give the scheduled retry time to fire - beginSubmission must have
    // cancelled it, so it should not call unloadModel a second time while
    // the new submission is still pending.
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1);

    act(() => result.current.settleSubmission(newToken, "job-2"));

    await waitFor(() => expect(memoryApi.unloadModel).toHaveBeenCalledTimes(2), { timeout: 3000 });
  });

  it("surfaces a non-409 unload failure without retrying automatically", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel).mockRejectedValue(new Error("worker unreachable"));

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    await waitFor(() => expect(result.current.unloadError).toBe("worker unreachable"));

    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(memoryApi.unloadModel).toHaveBeenCalledTimes(1);
  });

  it("retryUnload lets the caller try again after a failure", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(memoryApi.unloadModel)
      .mockRejectedValueOnce(new Error("worker unreachable"))
      .mockResolvedValueOnce(undefined);

    const { result } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    await waitFor(() => expect(result.current.unloadError).toBe("worker unreachable"));

    act(() => result.current.retryUnload());

    await waitFor(() => expect(result.current.unloadError).toBeNull());
    expect(memoryApi.unloadModel).toHaveBeenCalledTimes(2);
  });

  it("stops polling on unmount without requesting an unload", async () => {
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(job({ status: "running" }));
    vi.mocked(memoryApi.unloadModel).mockResolvedValue(undefined);

    const { result, unmount } = renderHook(() => useUnloadAfterBatch());
    act(() => trackJob(result, "job-1"));

    await waitFor(() => expect(analysesApi.fetchJob).toHaveBeenCalled());
    unmount();

    const callsAtUnmount = vi.mocked(analysesApi.fetchJob).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 900));

    expect(vi.mocked(analysesApi.fetchJob).mock.calls.length).toBe(callsAtUnmount);
    expect(memoryApi.unloadModel).not.toHaveBeenCalled();
  });
});
