/** Tests that the rename API wrappers invoke the right Tauri command names
 * and argument shapes, since a mismatch with the Rust side fails silently
 * at runtime rather than at compile time. */

import { invoke } from "@tauri-apps/api/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getLastBatchSummary, renameBatch, undoLastRenameBatch } from "./rename";

const mockedInvoke = vi.mocked(invoke);

describe("rename API", () => {
  afterEach(() => {
    mockedInvoke.mockReset();
  });

  it("renameBatch invokes rename_batch with the items payload", async () => {
    mockedInvoke.mockResolvedValue({ batch_id: "batch-1", results: [], history_warning: null });
    const items = [{ request_id: "req-1", source_path: "/a.pdf", desired_filename: "b.pdf" }];

    await renameBatch(items);

    expect(mockedInvoke).toHaveBeenCalledWith("rename_batch", { items });
  });

  it("undoLastRenameBatch invokes undo_last_rename_batch with no args", async () => {
    mockedInvoke.mockResolvedValue({ batch_id: "batch-1", results: [], history_warning: null });

    await undoLastRenameBatch();

    expect(mockedInvoke).toHaveBeenCalledWith("undo_last_rename_batch");
  });

  it("getLastBatchSummary invokes get_last_batch_summary with no args", async () => {
    mockedInvoke.mockResolvedValue(null);

    await getLastBatchSummary();

    expect(mockedInvoke).toHaveBeenCalledWith("get_last_batch_summary");
  });
});
