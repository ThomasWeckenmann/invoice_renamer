/** Tests for the import dropzone: native-dialog selection and window-level
 * drag-and-drop, both of which must surface real filesystem paths. */

import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ImportedFile } from "../types";
import { ImportDropzone } from "./ImportDropzone";

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));

const mockedInvoke = vi.mocked(invoke);
const mockedOpen = vi.mocked(open);
const mockedGetCurrentWebview = vi.mocked(getCurrentWebview);

function pdfBytes(): number[] {
  return Array.from(new TextEncoder().encode("%PDF-1.4"));
}

describe("ImportDropzone", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("reads bytes for each path chosen via the native dialog and reports them", async () => {
    mockedOpen.mockResolvedValue(["/invoices/a.pdf", "/invoices/b.pdf"]);
    mockedInvoke.mockResolvedValue(pdfBytes());
    const onFilesImported = vi.fn();

    render(<ImportDropzone onFilesImported={onFilesImported} onImportError={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose files" }));

    await waitFor(() => expect(onFilesImported).toHaveBeenCalledTimes(1));
    const [imported] = onFilesImported.mock.calls[0] as [ImportedFile[]];
    expect(imported).toHaveLength(2);
    expect(imported[0]).toMatchObject({ sourcePath: "/invoices/a.pdf" });
    expect(imported[0].file.name).toBe("a.pdf");
    expect(mockedInvoke).toHaveBeenCalledWith("read_file_bytes", { path: "/invoices/a.pdf" });
  });

  it("does nothing when the dialog is cancelled", async () => {
    mockedOpen.mockResolvedValue(null);
    const onFilesImported = vi.fn();

    render(<ImportDropzone onFilesImported={onFilesImported} onImportError={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose files" }));

    await waitFor(() => expect(mockedOpen).toHaveBeenCalled());
    expect(onFilesImported).toHaveBeenCalledWith([]);
  });

  it("reports a read failure via onImportError instead of throwing", async () => {
    mockedOpen.mockResolvedValue(["/invoices/a.pdf"]);
    mockedInvoke.mockRejectedValue(new Error("permission denied"));
    const onImportError = vi.fn();

    render(<ImportDropzone onFilesImported={vi.fn()} onImportError={onImportError} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose files" }));

    await waitFor(() => expect(onImportError).toHaveBeenCalledWith("permission denied"));
  });

  it("reads bytes for a JPEG path chosen via the native dialog", async () => {
    mockedOpen.mockResolvedValue(["/invoices/scan.jpg"]);
    mockedInvoke.mockResolvedValue(Array.from(new Uint8Array([0xff, 0xd8, 0xff])));
    const onFilesImported = vi.fn();

    render(<ImportDropzone onFilesImported={onFilesImported} onImportError={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose files" }));

    await waitFor(() => expect(onFilesImported).toHaveBeenCalledTimes(1));
    const [imported] = onFilesImported.mock.calls[0] as [ImportedFile[]];
    expect(imported).toHaveLength(1);
    expect(imported[0].sourcePath).toBe("/invoices/scan.jpg");
    expect(imported[0].file.type).toBe("image/jpeg");
  });

  it("imports PDF and JPEG paths dropped on the window and ignores unsupported paths", async () => {
    type DragDropHandler = Parameters<ReturnType<typeof getCurrentWebview>["onDragDropEvent"]>[0];
    let dragDropHandler: DragDropHandler | undefined;
    mockedGetCurrentWebview.mockReturnValue({
      onDragDropEvent: vi.fn((handler: DragDropHandler) => {
        dragDropHandler = handler;
        return Promise.resolve(() => {});
      }),
    } as unknown as ReturnType<typeof getCurrentWebview>);
    mockedInvoke.mockResolvedValue(pdfBytes());
    const onFilesImported = vi.fn();

    render(<ImportDropzone onFilesImported={onFilesImported} onImportError={vi.fn()} />);
    await waitFor(() => expect(dragDropHandler).toBeDefined());

    dragDropHandler!({
      event: "drag-drop",
      id: 1,
      payload: {
        type: "drop",
        paths: ["/invoices/dropped.pdf", "/invoices/scan.jpg", "/invoices/notes.txt"],
        position: { x: 0, y: 0 } as never,
      },
    });

    await waitFor(() => expect(onFilesImported).toHaveBeenCalledTimes(1));
    const [imported] = onFilesImported.mock.calls[0] as [ImportedFile[]];
    expect(imported).toHaveLength(2);
    expect(imported[0].sourcePath).toBe("/invoices/dropped.pdf");
    expect(imported[1].sourcePath).toBe("/invoices/scan.jpg");
  });

  it("hands a drop to the callback passed on the latest render", async () => {
    type DragDropHandler = Parameters<ReturnType<typeof getCurrentWebview>["onDragDropEvent"]>[0];
    let dragDropHandler: DragDropHandler | undefined;
    const onDragDropEvent = vi.fn((handler: DragDropHandler) => {
      dragDropHandler = handler;
      return Promise.resolve(() => {});
    });
    mockedGetCurrentWebview.mockReturnValue({
      onDragDropEvent,
    } as unknown as ReturnType<typeof getCurrentWebview>);
    mockedInvoke.mockResolvedValue(pdfBytes());
    const firstCallback = vi.fn();
    const latestCallback = vi.fn();

    const { rerender } = render(
      <ImportDropzone onFilesImported={firstCallback} onImportError={vi.fn()} />,
    );
    await waitFor(() => expect(dragDropHandler).toBeDefined());
    rerender(<ImportDropzone onFilesImported={latestCallback} onImportError={vi.fn()} />);

    dragDropHandler!({
      event: "drag-drop",
      id: 1,
      payload: { type: "drop", paths: ["/invoices/dropped.pdf"], position: { x: 0, y: 0 } as never },
    });

    await waitFor(() => expect(latestCallback).toHaveBeenCalledTimes(1));
    expect(firstCallback).not.toHaveBeenCalled();
    expect(onDragDropEvent).toHaveBeenCalledTimes(1);
  });

  it("hands a drop to the callback that is current once reading finishes", async () => {
    type DragDropHandler = Parameters<ReturnType<typeof getCurrentWebview>["onDragDropEvent"]>[0];
    let dragDropHandler: DragDropHandler | undefined;
    mockedGetCurrentWebview.mockReturnValue({
      onDragDropEvent: vi.fn((handler: DragDropHandler) => {
        dragDropHandler = handler;
        return Promise.resolve(() => {});
      }),
    } as unknown as ReturnType<typeof getCurrentWebview>);
    let finishRead: (bytes: number[]) => void = () => {};
    mockedInvoke.mockReturnValue(
      new Promise((resolve) => {
        finishRead = resolve;
      }),
    );
    const firstCallback = vi.fn();
    const latestCallback = vi.fn();

    const { rerender } = render(
      <ImportDropzone onFilesImported={firstCallback} onImportError={vi.fn()} />,
    );
    await waitFor(() => expect(dragDropHandler).toBeDefined());

    dragDropHandler!({
      event: "drag-drop",
      id: 1,
      payload: { type: "drop", paths: ["/invoices/dropped.pdf"], position: { x: 0, y: 0 } as never },
    });
    await waitFor(() => expect(mockedInvoke).toHaveBeenCalled());
    rerender(<ImportDropzone onFilesImported={latestCallback} onImportError={vi.fn()} />);
    finishRead(pdfBytes());

    await waitFor(() => expect(latestCallback).toHaveBeenCalledTimes(1));
    expect(firstCallback).not.toHaveBeenCalled();
  });
});
