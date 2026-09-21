/** Reads bytes for a real filesystem path via the Tauri backend, and turns
 * a source path into a `File` the existing upload flow can send. */

import { invoke } from "@tauri-apps/api/core";

export function basename(path: string): string {
  const normalized = path.replace(/[\\/]+$/, "");
  const parts = normalized.split(/[\\/]/);
  return parts[parts.length - 1] || normalized;
}

async function readFileBytes(path: string): Promise<ArrayBuffer> {
  const bytes = await invoke<number[]>("read_file_bytes", { path });
  const buffer = new ArrayBuffer(bytes.length);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

const MIME_TYPES_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
};

function mimeTypeForPath(path: string): string {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  return MIME_TYPES_BY_EXTENSION[extension] ?? "application/octet-stream";
}

export async function readPathAsFile(path: string): Promise<File> {
  const buffer = await readFileBytes(path);
  return new File([buffer], basename(path), { type: mimeTypeForPath(path) });
}
