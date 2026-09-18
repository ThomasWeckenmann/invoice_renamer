/** Import surface for PDF invoices: a native open dialog for browsing, and
 * window-level drag-and-drop. Both return real filesystem paths, which the
 * later rename step needs and a plain `<input type=file>` cannot provide. */

import { useEffect, useRef, useState } from "react";
import { pickPdfFiles } from "../../../lib/tauri/dialog";
import { subscribeToDragDrop } from "../../../lib/tauri/dragDrop";
import { readPathAsFile } from "../../../lib/tauri/files";
import type { ImportedFile } from "../types";

interface ImportDropzoneProps {
  onFilesImported: (files: ImportedFile[]) => void;
  onImportError: (message: string) => void;
}

function isPdfPath(path: string): boolean {
  return path.toLowerCase().endsWith(".pdf");
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

async function importPaths(paths: string[]): Promise<ImportedFile[]> {
  const imported: ImportedFile[] = [];
  for (const sourcePath of paths.filter(isPdfPath)) {
    const file = await readPathAsFile(sourcePath);
    imported.push({ file, sourcePath });
  }
  return imported;
}

export function ImportDropzone({ onFilesImported, onImportError }: ImportDropzoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  // Kept current via refs rather than as effect deps, so the drag-drop
  // subscription is set up once on mount instead of churning on every
  // render a caller passes a fresh callback identity.
  const onFilesImportedRef = useRef(onFilesImported);
  onFilesImportedRef.current = onFilesImported;
  const onImportErrorRef = useRef(onImportError);
  onImportErrorRef.current = onImportError;

  useEffect(() => {
    const unlisten = subscribeToDragDrop({
      onHoverChange: setIsDragOver,
      onDrop: (paths) => {
        importPaths(paths)
          .then((files) => onFilesImportedRef.current(files))
          .catch((err: unknown) => onImportErrorRef.current(errorMessage(err)));
      },
    });
    return () => {
      void unlisten.then((stop) => stop());
    };
  }, []);

  const handleBrowse = () => {
    pickPdfFiles()
      .then(importPaths)
      .then(onFilesImportedRef.current)
      .catch((err: unknown) => onImportErrorRef.current(errorMessage(err)));
  };

  return (
    <div className={`import-dropzone${isDragOver ? " import-dropzone--active" : ""}`}>
      <span className="import-dropzone__glyph" aria-hidden="true">
        +
      </span>
      <p className="import-dropzone__label">
        <strong>Drag PDF invoices here</strong>, or
      </p>
      <span className="import-dropzone__spacer" />
      <button type="button" className="btn sm" onClick={handleBrowse}>
        Choose files
      </button>
    </div>
  );
}
