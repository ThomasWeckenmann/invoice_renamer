/** Tests for the import dropzone: file-picker selection and drag-and-drop. */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImportDropzone } from "./ImportDropzone";

function pdfFile(name = "invoice.pdf"): File {
  return new File(["%PDF-1.4"], name, { type: "application/pdf" });
}

describe("ImportDropzone", () => {
  it("calls onFilesSelected when a file is chosen via the picker input", () => {
    const onFilesSelected = vi.fn();
    render(<ImportDropzone onFilesSelected={onFilesSelected} />);

    const input = screen.getByLabelText("Choose PDF invoices");
    const file = pdfFile();
    fireEvent.change(input, { target: { files: [file] } });

    expect(onFilesSelected).toHaveBeenCalledTimes(1);
    const [files] = onFilesSelected.mock.calls[0] as [FileList];
    expect(files[0]).toBe(file);
  });

  it("calls onFilesSelected on drop", () => {
    const onFilesSelected = vi.fn();
    const { container } = render(<ImportDropzone onFilesSelected={onFilesSelected} />);

    const dropzone = container.querySelector(".import-dropzone");
    expect(dropzone).not.toBeNull();
    const file = pdfFile();
    fireEvent.drop(dropzone as Element, { dataTransfer: { files: [file] } });

    expect(onFilesSelected).toHaveBeenCalledTimes(1);
  });
});
