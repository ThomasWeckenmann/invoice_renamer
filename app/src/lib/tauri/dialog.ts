/** Native open-file dialog for picking invoice PDFs, returning real
 * filesystem paths (a plain `<input type=file>` never can). */

import { open } from "@tauri-apps/plugin-dialog";

export async function pickPdfFiles(): Promise<string[]> {
  const selected = await open({
    multiple: true,
    filters: [{ name: "PDF", extensions: ["pdf"] }],
  });
  if (selected === null) {
    return [];
  }
  return Array.isArray(selected) ? selected : [selected];
}
