/** Tests for the rename transaction hook: sending approved items, mapping
 * results back onto items by request id, and Undo. */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as renameApi from "../../lib/tauri/rename";
import type { BatchItem } from "./types";
import { useRenameTransaction } from "./useRenameTransaction";

vi.mock("../../lib/tauri/rename");

function approvedItem(overrides: Partial<BatchItem> = {}): BatchItem {
  return {
    id: "item-1",
    file: new File(["%PDF-1.4"], "invoice.pdf", { type: "application/pdf" }),
    sourcePath: "/invoices/invoice.pdf",
    status: "approved",
    jobId: null,
    proposal: {
      extraction: {
        invoice_date: "2026-01-05",
        seller: "Acme",
        product_summary: "Widget",
        gross_total: "42.00",
        currency: "EUR",
        language: "en",
        evidence: {},
        warnings: [],
      },
      proposed_filename: "2026-01-05_Acme_Widget_42-EUR.pdf",
      requires_review: false,
      missing_fields: [],
    },
    editedFilename: null,
    metrics: null,
    error: null,
    ...overrides,
  };
}

describe("useRenameTransaction", () => {
  afterEach(() => {
    vi.resetAllMocks();
  });

  it("checks for an undoable batch on mount", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);

    const { result } = renderHook(() => useRenameTransaction());

    await waitFor(() => expect(renameApi.getLastBatchSummary).toHaveBeenCalled());
    expect(result.current.canUndo).toBe(false);
  });

  it("enables Undo on mount when a persisted batch is still undoable", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue({
      batch_id: "batch-1",
      applied_at_unix_ms: 1000,
      item_count: 2,
    });

    const { result } = renderHook(() => useRenameTransaction());

    await waitFor(() => expect(result.current.canUndo).toBe(true));
  });

  it("sends only approved items, keyed by request id", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.renameBatch).mockResolvedValue({
      batch_id: "batch-1",
      results: [
        {
          request_id: "item-1",
          outcome: "renamed",
          source_path: "/invoices/invoice.pdf",
          destination_path: "/invoices/2026-01-05_Acme_Widget_42-EUR.pdf",
        },
      ],
      history_warning: null,
    });

    const { result } = renderHook(() => useRenameTransaction());
    const items = [approvedItem(), approvedItem({ id: "item-2", status: "needs_review" })];

    act(() => result.current.renameApproved(items));

    await waitFor(() => expect(result.current.isRenaming).toBe(false));
    expect(renameApi.renameBatch).toHaveBeenCalledWith([
      {
        request_id: "item-1",
        source_path: "/invoices/invoice.pdf",
        desired_filename: "2026-01-05_Acme_Widget_42-EUR.pdf",
      },
    ]);
    expect(result.current.outcomes["item-1"]).toEqual({
      status: "renamed",
      destinationPath: "/invoices/2026-01-05_Acme_Widget_42-EUR.pdf",
    });
    expect(result.current.canUndo).toBe(true);
  });

  it("keeps duplicate-source-path rows distinct, since only request id correlates results", async () => {
    // Two rows imported from the same source path (e.g. the file was
    // picked twice). Rust can only actually rename the first one - its
    // source disappears before the second attempt runs - but each row
    // must still report its own, correct outcome.
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.renameBatch).mockResolvedValue({
      batch_id: "batch-1",
      results: [
        {
          request_id: "item-1",
          outcome: "renamed",
          source_path: "/invoices/dup.pdf",
          destination_path: "/invoices/renamed.pdf",
        },
        {
          request_id: "item-2",
          outcome: "failed",
          source_path: "/invoices/dup.pdf",
          message: "no longer exists",
        },
      ],
      history_warning: null,
    });

    const { result } = renderHook(() => useRenameTransaction());
    const items = [
      approvedItem({ id: "item-1", sourcePath: "/invoices/dup.pdf" }),
      approvedItem({ id: "item-2", sourcePath: "/invoices/dup.pdf" }),
    ];

    act(() => result.current.renameApproved(items));

    await waitFor(() => expect(result.current.outcomes["item-1"]).toBeDefined());
    expect(result.current.outcomes["item-1"]).toEqual({
      status: "renamed",
      destinationPath: "/invoices/renamed.pdf",
    });
    expect(result.current.outcomes["item-2"]).toEqual({
      status: "failed",
      message: "no longer exists",
    });
  });

  it("records a per-item failure without touching other items", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.renameBatch).mockResolvedValue({
      batch_id: null,
      results: [
        {
          request_id: "item-1",
          outcome: "failed",
          source_path: "/invoices/invoice.pdf",
          message: "permission denied",
        },
      ],
      history_warning: null,
    });

    const { result } = renderHook(() => useRenameTransaction());
    act(() => result.current.renameApproved([approvedItem()]));

    await waitFor(() =>
      expect(result.current.outcomes["item-1"]).toEqual({
        status: "failed",
        message: "permission denied",
      }),
    );
    expect(result.current.canUndo).toBe(false);
  });

  it("surfaces a history_warning after a successful rename as a rename error", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.renameBatch).mockResolvedValue({
      batch_id: null,
      results: [
        {
          request_id: "item-1",
          outcome: "renamed",
          source_path: "/invoices/invoice.pdf",
          destination_path: "/invoices/renamed.pdf",
        },
      ],
      history_warning: "files were renamed, but the Undo record could not be saved",
    });

    const { result } = renderHook(() => useRenameTransaction());
    act(() => result.current.renameApproved([approvedItem()]));

    await waitFor(() =>
      expect(result.current.renameError).toBe(
        "files were renamed, but the Undo record could not be saved",
      ),
    );
    expect(result.current.outcomes["item-1"]).toEqual({
      status: "renamed",
      destinationPath: "/invoices/renamed.pdf",
    });
  });

  it("excludes already-renamed items from a later rename call", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.renameBatch).mockResolvedValue({
      batch_id: "batch-1",
      results: [
        {
          request_id: "item-1",
          outcome: "renamed",
          source_path: "/invoices/invoice.pdf",
          destination_path: "/invoices/renamed.pdf",
        },
      ],
      history_warning: null,
    });

    const { result } = renderHook(() => useRenameTransaction());
    const item = approvedItem();
    act(() => result.current.renameApproved([item]));
    await waitFor(() => expect(result.current.outcomes["item-1"]).toBeDefined());

    vi.mocked(renameApi.renameBatch).mockClear();
    act(() => result.current.renameApproved([item]));

    expect(renameApi.renameBatch).not.toHaveBeenCalled();
  });

  it("surfaces a whole-batch rename failure", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.renameBatch).mockRejectedValue(
      new Error("cannot rename, no changes were made: /invoices/invoice.pdf: no longer exists"),
    );

    const { result } = renderHook(() => useRenameTransaction());
    act(() => result.current.renameApproved([approvedItem()]));

    await waitFor(() =>
      expect(result.current.renameError).toBe(
        "cannot rename, no changes were made: /invoices/invoice.pdf: no longer exists",
      ),
    );
  });

  it("undo clears outcomes for items the undo actually reversed", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue({
      batch_id: "batch-1",
      applied_at_unix_ms: 1000,
      item_count: 1,
    });
    vi.mocked(renameApi.renameBatch).mockResolvedValue({
      batch_id: "batch-1",
      results: [
        {
          request_id: "item-1",
          outcome: "renamed",
          source_path: "/invoices/invoice.pdf",
          destination_path: "/invoices/renamed.pdf",
        },
      ],
      history_warning: null,
    });
    vi.mocked(renameApi.undoLastRenameBatch).mockResolvedValue({
      batch_id: "batch-1",
      results: [
        {
          outcome: "renamed",
          source_path: "/invoices/renamed.pdf",
          destination_path: "/invoices/invoice.pdf",
        },
      ],
      history_warning: null,
    });

    const { result } = renderHook(() => useRenameTransaction());
    act(() => result.current.renameApproved([approvedItem()]));
    await waitFor(() => expect(result.current.outcomes["item-1"]).toBeDefined());

    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    act(() => result.current.undoLastBatch());

    await waitFor(() => expect(result.current.outcomes["item-1"]).toBeUndefined());
    await waitFor(() => expect(result.current.canUndo).toBe(false));
    expect(result.current.undoError).toBeNull();
  });

  it("surfaces a per-file undo failure instead of silently ignoring it", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue({
      batch_id: "batch-1",
      applied_at_unix_ms: 1000,
      item_count: 2,
    });
    vi.mocked(renameApi.undoLastRenameBatch).mockResolvedValue({
      batch_id: "batch-1",
      results: [
        {
          outcome: "renamed",
          source_path: "/invoices/renamed-a.pdf",
          destination_path: "/invoices/a.pdf",
        },
        {
          outcome: "failed",
          source_path: "/invoices/renamed-b.pdf",
          message: "permission denied",
        },
      ],
      history_warning: null,
    });

    const { result } = renderHook(() => useRenameTransaction());
    act(() => result.current.undoLastBatch());

    await waitFor(() =>
      expect(result.current.undoError).toBe(
        "1 of 2 file(s) could not be restored: /invoices/renamed-b.pdf: permission denied",
      ),
    );
  });

  it("surfaces an undo history_warning alongside any per-file failures", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.undoLastRenameBatch).mockResolvedValue({
      batch_id: "batch-1",
      results: [
        {
          outcome: "renamed",
          source_path: "/invoices/renamed.pdf",
          destination_path: "/invoices/invoice.pdf",
        },
      ],
      history_warning: "the Undo record could not be updated",
    });

    const { result } = renderHook(() => useRenameTransaction());
    act(() => result.current.undoLastBatch());

    await waitFor(() =>
      expect(result.current.undoError).toBe("the Undo record could not be updated"),
    );
  });

  it("surfaces an undo command failure without clearing outcomes", async () => {
    vi.mocked(renameApi.getLastBatchSummary).mockResolvedValue(null);
    vi.mocked(renameApi.undoLastRenameBatch).mockRejectedValue(
      new Error("there is no rename batch to undo"),
    );

    const { result } = renderHook(() => useRenameTransaction());
    act(() => result.current.undoLastBatch());

    await waitFor(() => expect(result.current.undoError).toBe("there is no rename batch to undo"));
  });
});
