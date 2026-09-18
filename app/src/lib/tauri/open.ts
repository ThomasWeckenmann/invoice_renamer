/** Opens a file with the operating system's default application (e.g.
 * Preview on macOS), via the Tauri backend. */

import { invoke } from "@tauri-apps/api/core";

export function openWithSystemDefault(path: string): Promise<void> {
  return invoke("open_with_system_default", { path });
}
