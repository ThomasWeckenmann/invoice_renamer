/** Drag-and-drop and file-picker import surface for PDF invoices. */

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";

interface ImportDropzoneProps {
  onFilesSelected: (files: FileList | File[]) => void;
}

export function ImportDropzone({ onFilesSelected }: ImportDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragOver(false);
    if (event.dataTransfer.files.length > 0) {
      onFilesSelected(event.dataTransfer.files);
    }
  };

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files && event.target.files.length > 0) {
      onFilesSelected(event.target.files);
    }
    event.target.value = "";
  };

  return (
    <div
      className={`import-dropzone${isDragOver ? " import-dropzone--active" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setIsDragOver(true);
      }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={handleDrop}
    >
      <p>Drag PDF invoices here, or</p>
      <button type="button" onClick={() => inputRef.current?.click()}>
        Choose files
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        multiple
        hidden
        onChange={handleChange}
        aria-label="Choose PDF invoices"
      />
    </div>
  );
}
