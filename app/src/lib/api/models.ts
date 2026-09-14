/** Client for the model-catalog endpoints: capabilities, listing, install, and removal. */

import { apiFetch } from "./client";
import type { ModelStatusEntry, SystemCapabilities } from "./types";

export function fetchCapabilities(): Promise<SystemCapabilities> {
  return apiFetch<SystemCapabilities>("/capabilities");
}

export function fetchModels(): Promise<ModelStatusEntry[]> {
  return apiFetch<ModelStatusEntry[]>("/models");
}

export function downloadModel(modelId: string): Promise<ModelStatusEntry> {
  return apiFetch<ModelStatusEntry>(`/models/${encodeURIComponent(modelId)}/download`, {
    method: "POST",
  });
}

export function deleteModel(modelId: string): Promise<ModelStatusEntry> {
  return apiFetch<ModelStatusEntry>(`/models/${encodeURIComponent(modelId)}`, {
    method: "DELETE",
  });
}
