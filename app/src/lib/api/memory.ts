/** Client for the live memory-snapshot endpoint. */

import { apiFetch } from "./client";
import type { MemorySnapshot } from "./types";

export function fetchMemorySnapshot(): Promise<MemorySnapshot> {
  return apiFetch<MemorySnapshot>("/memory");
}
