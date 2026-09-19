/** Client for the analysis-job endpoints: submit a PDF, poll status, cancel. */

import { apiFetch } from "./client";
import type { AnalysisJobView } from "./types";

export function submitAnalysis(
  file: File,
  modelId: string,
  shortenFields: boolean,
): Promise<AnalysisJobView> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("model_id", modelId);
  form.append("shorten_fields", String(shortenFields));
  return apiFetch<AnalysisJobView>("/analyses", { method: "POST", body: form });
}

export function fetchJob(jobId: string): Promise<AnalysisJobView> {
  return apiFetch<AnalysisJobView>(`/jobs/${encodeURIComponent(jobId)}`);
}

export function cancelJob(jobId: string): Promise<AnalysisJobView> {
  return apiFetch<AnalysisJobView>(`/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
}
