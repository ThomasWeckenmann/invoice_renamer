/** Tests for the batch workspace hook: import filtering, submit+poll to
 * completion, filename edits, approval, and cancellation. */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as analysesApi from "../../lib/api/analyses";
import type { AnalysisJobView } from "../../lib/api/types";
import type { ImportedFile } from "./types";
import { useBatchWorkspace } from "./useBatchWorkspace";

vi.mock("../../lib/api/analyses");

function pdfFile(name = "invoice.pdf"): File {
  return new File(["%PDF-1.4"], name, { type: "application/pdf" });
}

function importedPdf(name = "invoice.pdf"): ImportedFile {
  return { file: pdfFile(name), sourcePath: `/invoices/${name}` };
}

function importedJpeg(name = "scan.jpg"): ImportedFile {
  return {
    file: new File(["\xff\xd8\xff"], name, { type: "image/jpeg" }),
    sourcePath: `/invoices/${name}`,
  };
}

function queuedJob(overrides: Partial<AnalysisJobView> = {}): AnalysisJobView {
  return {
    id: "job-1",
    model_id: "granite-3.3-2b",
    original_filename: "invoice.pdf",
    status: "queued",
    proposal: null,
    metrics: null,
    error: null,
    ...overrides,
  };
}

describe("useBatchWorkspace", () => {
  afterEach(() => {
    vi.resetAllMocks();
  });

  it("addFiles adds each imported file with its source path", () => {
    const { result } = renderHook(() => useBatchWorkspace());

    act(() => {
      result.current.addFiles([importedPdf("a.pdf"), importedPdf("b.pdf")]);
    });

    expect(result.current.items).toHaveLength(2);
    expect(result.current.items[0].file.name).toBe("a.pdf");
    expect(result.current.items[0].sourcePath).toBe("/invoices/a.pdf");
    expect(result.current.items[0].status).toBe("pending");
  });

  it("startAnalysis submits pending items and polls the job to completion", async () => {
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValue(queuedJob());
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(
      queuedJob({
        status: "completed",
        proposal: {
          extraction: {
            invoice_date: "2026-01-05",
            seller: "Acme",
            product_summary: "Widget",
            seller_short: null,
            product_summary_short: null,
            gross_total: "42.00",
            currency: "EUR",
            language: "en",
            evidence: {},
            warnings: [],
          },
          proposed_filename: "2026-01-05_Acme_Widget_42-EUR.pdf",
          requires_review: false,
          missing_fields: [],
          warnings: [],
        },
        metrics: null,
      }),
    );

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));

    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    expect(result.current.items[0].status).toBe("queued");
    expect(analysesApi.submitAnalysis).toHaveBeenCalledWith(expect.any(File), "granite-3.3-2b", true);

    await waitFor(() => expect(result.current.items[0].status).toBe("needs_review"), {
      timeout: 3000,
    });
    expect(result.current.items[0].proposal?.proposed_filename).toBe(
      "2026-01-05_Acme_Widget_42-EUR.pdf",
    );
  });

  it("startAnalysis works the same for an imported JPEG scan", async () => {
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValue(queuedJob({ original_filename: "scan.jpg" }));
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(
      queuedJob({
        status: "completed",
        proposal: {
          extraction: {
            invoice_date: "2026-01-05",
            seller: "Acme",
            product_summary: "Widget",
            seller_short: null,
            product_summary_short: null,
            gross_total: "42.00",
            currency: "EUR",
            language: "en",
            evidence: {},
            warnings: [],
          },
          proposed_filename: "2026-01-05_Acme_Widget_42-EUR.jpg",
          requires_review: false,
          missing_fields: [],
          warnings: [],
        },
        metrics: null,
      }),
    );

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedJpeg()]));

    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    expect(result.current.items[0].status).toBe("queued");

    await waitFor(() => expect(result.current.items[0].status).toBe("needs_review"), {
      timeout: 3000,
    });
    expect(result.current.items[0].proposal?.proposed_filename).toBe(
      "2026-01-05_Acme_Widget_42-EUR.jpg",
    );
  });

  it("surfaces a submit failure as a failed item", async () => {
    vi.mocked(analysesApi.submitAnalysis).mockRejectedValue(new Error("model not installed"));

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    act(() => result.current.startAnalysis("granite-3.3-2b", true));

    await waitFor(() => expect(result.current.items[0].status).toBe("failed"));
    expect(result.current.items[0].error).toBe("model not installed");
  });

  it("editFilename overrides the proposed filename, and approve/unapprove toggle status", () => {
    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    const id = result.current.items[0].id;

    act(() => result.current.editFilename(id, "custom-name.pdf"));
    expect(result.current.items[0].editedFilename).toBe("custom-name.pdf");

    act(() => result.current.approveItem(id));
    expect(result.current.items[0].status).toBe("approved");

    act(() => result.current.unapproveItem(id));
    expect(result.current.items[0].status).toBe("needs_review");
  });

  it("cancelItem cancels the backend job and marks the item cancelled", async () => {
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValue(queuedJob());
    vi.mocked(analysesApi.cancelJob).mockResolvedValue(queuedJob({ status: "cancelled" }));

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    const id = result.current.items[0].id;

    await waitFor(() => expect(result.current.items[0].jobId).toBe("job-1"));

    act(() => result.current.cancelItem(id));

    expect(result.current.items[0].status).toBe("cancelled");
    expect(analysesApi.cancelJob).toHaveBeenCalledWith("job-1");
  });

  it("rerunItem resubmits a cancelled item and clears its prior state", async () => {
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValue(queuedJob());
    vi.mocked(analysesApi.cancelJob).mockResolvedValue(queuedJob({ status: "cancelled" }));

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    const id = result.current.items[0].id;

    await waitFor(() => expect(result.current.items[0].jobId).toBe("job-1"));
    act(() => result.current.cancelItem(id));
    expect(result.current.items[0].status).toBe("cancelled");

    vi.mocked(analysesApi.submitAnalysis).mockResolvedValue(queuedJob({ id: "job-2" }));
    vi.mocked(analysesApi.fetchJob).mockResolvedValue(queuedJob({ id: "job-2", status: "completed" }));

    act(() => result.current.rerunItem(id, "qwen3-0.6b", true));

    expect(result.current.items[0].status).toBe("queued");
    expect(result.current.items[0].error).toBeNull();
    expect(analysesApi.submitAnalysis).toHaveBeenLastCalledWith(expect.any(File), "qwen3-0.6b", true);

    await waitFor(() => expect(result.current.items[0].jobId).toBe("job-2"));
    await waitFor(() => expect(result.current.items[0].status).toBe("needs_review"), {
      timeout: 3000,
    });
  });

  it("a poll response from the cancelled run does not overwrite a rerun", async () => {
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValueOnce(queuedJob({ id: "job-1" }));
    let resolveFetchJob!: (job: AnalysisJobView) => void;
    vi.mocked(analysesApi.fetchJob).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetchJob = resolve;
        }),
    );
    vi.mocked(analysesApi.cancelJob).mockResolvedValue(queuedJob({ status: "cancelled" }));

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    const id = result.current.items[0].id;

    await waitFor(() => expect(result.current.items[0].jobId).toBe("job-1"));
    // Wait for the poll interval to fire so a fetchJob call for job-1 is in
    // flight (blocked on the unresolved promise above) when we cancel.
    await waitFor(() => expect(analysesApi.fetchJob).toHaveBeenCalled(), { timeout: 2000 });

    act(() => result.current.cancelItem(id));
    expect(result.current.items[0].status).toBe("cancelled");

    // Rerun before the stale job-1 poll resolves.
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValueOnce(queuedJob({ id: "job-2" }));
    act(() => result.current.rerunItem(id, "qwen3-0.6b", true));
    await waitFor(() => expect(result.current.items[0].jobId).toBe("job-2"));

    // The stale job-1 poll now resolves as "running" - it must not clobber
    // the fresh job-2 state (this is the race a cancelled run's leftover
    // poll used to win against a rerun).
    await act(async () => {
      resolveFetchJob(queuedJob({ id: "job-1", status: "running" }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.items[0].jobId).toBe("job-2");
    expect(result.current.items[0].status).toBe("queued");
    expect(analysesApi.fetchJob).toHaveBeenCalledTimes(1);
  });

  it("a submit response from the cancelled run does not overwrite a rerun", async () => {
    let resolveFirstSubmit!: (job: AnalysisJobView) => void;
    vi.mocked(analysesApi.submitAnalysis).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirstSubmit = resolve;
      }),
    );
    vi.mocked(analysesApi.cancelJob).mockResolvedValue(queuedJob({ status: "cancelled" }));

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    const id = result.current.items[0].id;

    // Cancel while the first submit is still in flight - no job id yet.
    act(() => result.current.cancelItem(id));
    expect(result.current.items[0].status).toBe("cancelled");

    // Rerun before the stale submit resolves.
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValueOnce(queuedJob({ id: "job-2" }));
    act(() => result.current.rerunItem(id, "qwen3-0.6b", true));
    await waitFor(() => expect(result.current.items[0].jobId).toBe("job-2"));

    // The stale first submit now resolves - it must be cancelled on the
    // backend, not allowed to overwrite job-2's fresh state.
    await act(async () => {
      resolveFirstSubmit(queuedJob({ id: "job-1" }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.items[0].jobId).toBe("job-2");
    expect(analysesApi.cancelJob).toHaveBeenCalledWith("job-1");
  });

  it("removeItem drops the item from the list", () => {
    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    const id = result.current.items[0].id;

    act(() => result.current.removeItem(id));

    expect(result.current.items).toHaveLength(0);
  });

  it("cancelling while the upload is still in flight does not get resurrected by the late response", async () => {
    let resolveSubmit!: (job: AnalysisJobView) => void;
    vi.mocked(analysesApi.submitAnalysis).mockReturnValue(
      new Promise((resolve) => {
        resolveSubmit = resolve;
      }),
    );
    vi.mocked(analysesApi.cancelJob).mockResolvedValue(queuedJob({ status: "cancelled" }));

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    const id = result.current.items[0].id;

    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    expect(result.current.items[0].status).toBe("queued");

    // Cancel while the upload is still pending: no job id exists yet, so
    // there's nothing to send a cancel request for.
    act(() => result.current.cancelItem(id));
    expect(result.current.items[0].status).toBe("cancelled");
    expect(analysesApi.cancelJob).not.toHaveBeenCalled();

    // The upload now resolves late, handing back the first real job id.
    await act(async () => {
      resolveSubmit(queuedJob({ id: "late-job" }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.items[0].status).toBe("cancelled");
    expect(analysesApi.cancelJob).toHaveBeenCalledWith("late-job");
    expect(analysesApi.fetchJob).not.toHaveBeenCalled();
  });

  it("a poll response that lands after cancellation does not resurrect the item", async () => {
    vi.mocked(analysesApi.submitAnalysis).mockResolvedValue(queuedJob());
    let resolveFetchJob!: (job: AnalysisJobView) => void;
    vi.mocked(analysesApi.fetchJob).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetchJob = resolve;
        }),
    );
    vi.mocked(analysesApi.cancelJob).mockResolvedValue(queuedJob({ status: "cancelled" }));

    const { result } = renderHook(() => useBatchWorkspace());
    act(() => result.current.addFiles([importedPdf()]));
    act(() => result.current.startAnalysis("granite-3.3-2b", true));
    const id = result.current.items[0].id;

    await waitFor(() => expect(result.current.items[0].jobId).toBe("job-1"));
    // Wait for the poll interval to fire so a fetchJob call is in flight
    // (blocked on the unresolved promise above) at the moment we cancel.
    await waitFor(() => expect(analysesApi.fetchJob).toHaveBeenCalled(), { timeout: 2000 });

    act(() => result.current.cancelItem(id));
    expect(result.current.items[0].status).toBe("cancelled");

    await act(async () => {
      resolveFetchJob(queuedJob({ status: "running" }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.items[0].status).toBe("cancelled");
  });
});
