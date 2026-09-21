/** Client for the live memory-snapshot endpoint and manual model unloading. */

import { apiFetch } from "./client";
import type { MemorySnapshot } from "./types";

export function fetchMemorySnapshot(): Promise<MemorySnapshot> {
  return apiFetch<MemorySnapshot>("/memory");
}

export function unloadModel(): Promise<void> {
  return apiFetch<void>("/memory/loaded-model", { method: "DELETE" });
}
