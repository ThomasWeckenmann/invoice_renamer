/** Native open-file dialog for picking invoice PDFs or JPEG scans, returning
 * real filesystem paths (a plain `<input type=file>` never can). */

import { open } from "@tauri-apps/plugin-dialog";

export async function pickInvoiceFiles(): Promise<string[]> {
  const selected = await open({
    multiple: true,
    filters: [{ name: "Invoices", extensions: ["pdf", "jpg", "jpeg"] }],
  });
  if (selected === null) {
    return [];
  }
  return Array.isArray(selected) ? selected : [selected];
}
