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

export async function readPathAsFile(path: string): Promise<File> {
  const buffer = await readFileBytes(path);
  return new File([buffer], basename(path), { type: "application/pdf" });
}
