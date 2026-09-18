/** Tests for the batch item row's run-metrics disclosure and the
 * open-with-system-default action. */

import { invoke } from "@tauri-apps/api/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RenameOutcome } from "../useRenameTransaction";
import type { BatchItem } from "../types";
import { BatchItemRow } from "./BatchItemRow";

const mockedInvoke = vi.mocked(invoke);

function reviewItem(overrides: Partial<BatchItem> = {}): BatchItem {
  return {
    id: "item-1",
    file: new File(["%PDF-1.4"], "invoice.pdf", { type: "application/pdf" }),
    sourcePath: "/invoices/invoice.pdf",
    status: "needs_review",
    jobId: "job-1",
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
    memoryWarning: null,
    error: null,
    ...overrides,
  };
}

const noop = () => {};

describe("BatchItemRow", () => {
  it("shows a memory warning when the job carries one, regardless of review status", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({
            status: "queued",
            proposal: null,
            memoryWarning:
              "Granite 3.3 2B Instruct typically uses about 8.2 GB of memory during " +
              "analysis, but only 1.5 GB is currently free.",
          })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("currently free");
  });

  it("renders no memory warning when the job has none", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem()}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.queryByText(/currently free/)).not.toBeInTheDocument();
  });

  it("renders no run-details disclosure when metrics are absent", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem()}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.queryByText("Run details")).not.toBeInTheDocument();
  });

  it("shows timings, pages, and token usage from RunMetrics", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({
            metrics: {
              total_ms: 4200,
              pdf_extraction_ms: 100,
              ocr_ms: 600,
              inference_ms: 3500,
              model_id: "granite-3.3-2b",
              provider: "transformers",
              model_revision: "abc123",
              pages_total: 3,
              pages_ocr: [2],
              input_tokens: 512,
              output_tokens: 64,
              tokens_per_second: 12.5,
              warnings: [],
            },
          })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.getByText("Run details")).toBeInTheDocument();
    expect(screen.getByText("granite-3.3-2b @ abc123")).toBeInTheDocument();
    expect(screen.getByText("4.2 s")).toBeInTheDocument();
    expect(screen.getByText("3.5 s")).toBeInTheDocument();
    expect(screen.getByText("3 (1 via OCR)")).toBeInTheDocument();
    expect(screen.getByText("512 in / 64 out (12.5 tok/s)")).toBeInTheDocument();
  });

  it("omits the tokens row when no token counts are available", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({
            metrics: {
              total_ms: 800,
              pdf_extraction_ms: 100,
              ocr_ms: 0,
              inference_ms: 700,
              model_id: "qwen3-0.6b",
              provider: "transformers",
              model_revision: null,
              pages_total: 1,
              pages_ocr: [],
              input_tokens: null,
              output_tokens: null,
              tokens_per_second: null,
              warnings: [],
            },
          })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.getByText("qwen3-0.6b")).toBeInTheDocument();
    expect(screen.queryByText("Tokens")).not.toBeInTheDocument();
  });
});

describe("BatchItemRow rerun action", () => {
  it("offers Re-Run for a cancelled item and calls back with its id", () => {
    const onRerun = vi.fn();
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({ status: "cancelled", proposal: null })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={onRerun}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    const button = screen.getByRole("button", { name: "Re-Run" });
    fireEvent.click(button);

    expect(onRerun).toHaveBeenCalledWith("item-1");
  });

  it("offers Re-Run for a failed item, disabled when no model is selected", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({ status: "failed", proposal: null, error: "boom" })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={false}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.getByRole("button", { name: "Re-Run" })).toBeDisabled();
  });

  it("does not offer Re-Run once the item has already been renamed", () => {
    const renameOutcome: RenameOutcome = {
      status: "renamed",
      destinationPath: "/invoices/2026-01-05_Acme_Widget_42-EUR.pdf",
    };
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({ status: "approved" })}
          renameOutcome={renameOutcome}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.queryByRole("button", { name: "Re-Run" })).not.toBeInTheDocument();
  });

  it("does not offer Re-Run for a pending item", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({ status: "pending", proposal: null })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.queryByRole("button", { name: "Re-Run" })).not.toBeInTheDocument();
  });
});

describe("BatchItemRow open action", () => {
  afterEach(() => {
    mockedInvoke.mockReset();
  });

  it("offers Open regardless of review status", () => {
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({ status: "queued", proposal: null })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    expect(screen.getByRole("button", { name: "Open" })).toBeInTheDocument();
  });

  it("opens the source path with the system default application before a rename", () => {
    mockedInvoke.mockResolvedValue(undefined);
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({ sourcePath: "/invoices/invoice.pdf" })}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(mockedInvoke).toHaveBeenCalledWith("open_with_system_default", {
      path: "/invoices/invoice.pdf",
    });
  });

  it("opens the destination path once the item has been renamed", () => {
    mockedInvoke.mockResolvedValue(undefined);
    const renameOutcome: RenameOutcome = {
      status: "renamed",
      destinationPath: "/invoices/2026-01-05_Acme_Widget_42-EUR.pdf",
    };
    render(
      <ul>
        <BatchItemRow
          item={reviewItem({ sourcePath: "/invoices/invoice.pdf" })}
          renameOutcome={renameOutcome}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(mockedInvoke).toHaveBeenCalledWith("open_with_system_default", {
      path: "/invoices/2026-01-05_Acme_Widget_42-EUR.pdf",
    });
  });

  it("shows an error when the file can't be opened", async () => {
    mockedInvoke.mockRejectedValue(new Error("no application found"));
    render(
      <ul>
        <BatchItemRow
          item={reviewItem()}
          onEditFilename={noop}
          onApprove={noop}
          onUnapprove={noop}
          onCancel={noop}
          onRerun={noop}
          canRerun={true}
          onRemove={noop}
        />
      </ul>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't open the file: no application found",
    );
  });
});
