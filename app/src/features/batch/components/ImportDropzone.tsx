/** Import surface for PDF and JPEG invoices: a native open dialog for
 * browsing, and window-level drag-and-drop. Both return real filesystem
 * paths, which the later rename step needs and a plain `<input type=file>`
 * cannot provide. */

import { useEffect, useEffectEvent, useState } from "react";
import { pickInvoiceFiles } from "../../../lib/tauri/dialog";
import { subscribeToDragDrop } from "../../../lib/tauri/dragDrop";
import { readPathAsFile } from "../../../lib/tauri/files";
import type { ImportedFile } from "../types";

interface ImportDropzoneProps {
  onFilesImported: (files: ImportedFile[]) => void;
  onImportError: (message: string) => void;
}

const SUPPORTED_EXTENSIONS = [".pdf", ".jpg", ".jpeg"];

function isSupportedInvoicePath(path: string): boolean {
  const lowerPath = path.toLowerCase();
  return SUPPORTED_EXTENSIONS.some((extension) => lowerPath.endsWith(extension));
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

async function importPaths(paths: string[]): Promise<ImportedFile[]> {
  const imported: ImportedFile[] = [];
  for (const sourcePath of paths.filter(isSupportedInvoicePath)) {
    const file = await readPathAsFile(sourcePath);
    imported.push({ file, sourcePath });
  }
  return imported;
}

export function ImportDropzone({ onFilesImported, onImportError }: ImportDropzoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  // Effect events read the callback props at the moment they fire, so a drop
  // reports to whichever callbacks are current once reading finishes, and the
  // drag-drop subscription is set up once on mount instead of churning on
  // every render a caller passes a fresh callback identity.
  const reportImported = useEffectEvent((files: ImportedFile[]) => onFilesImported(files));
  const reportImportError = useEffectEvent((message: string) => onImportError(message));

  useEffect(() => {
    const unlisten = subscribeToDragDrop({
      onHoverChange: setIsDragOver,
      onDrop: (paths) => {
        importPaths(paths)
          .then((files) => reportImported(files))
          .catch((err: unknown) => reportImportError(errorMessage(err)));
      },
    });
    return () => {
      void unlisten.then((stop) => stop());
    };
  }, []);

  const handleBrowse = () => {
    pickInvoiceFiles()
      .then(importPaths)
      .then(onFilesImported)
      .catch((err: unknown) => onImportError(errorMessage(err)));
  };

  return (
    <div className={`import-dropzone${isDragOver ? " import-dropzone--active" : ""}`}>
      <p className="import-dropzone__label">
        <strong>Drag PDF or JPG invoices here</strong>
      </p>
      <span className="import-dropzone__spacer" />
      <button type="button" className="btn sm" onClick={handleBrowse}>
        Choose files
      </button>
    </div>
  );
}
