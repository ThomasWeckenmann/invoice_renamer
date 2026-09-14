/** Hand-authored TypeScript mirror of the local worker's Pydantic API contracts. */

export type Language = "de" | "en" | "unknown";

export interface Evidence {
  page: number | null;
  excerpt: string | null;
  xml_field: string | null;
}

export interface InvoiceExtraction {
  invoice_date: string | null;
  seller: string | null;
  product_summary: string | null;
  gross_total: string | null;
  currency: string | null;
  language: Language;
  evidence: Record<string, Evidence>;
  warnings: string[];
}

export interface FilenameProposal {
  extraction: InvoiceExtraction;
  proposed_filename: string;
  requires_review: boolean;
}

export type ExecutionMode = "local" | "cloud";
export type CostSource = "provider_reported" | "estimated";

export interface RunMetrics {
  total_ms: number;
  pdf_extraction_ms: number;
  ocr_ms: number;
  inference_ms: number;
  execution_mode: ExecutionMode;
  model_id: string;
  provider: string;
  model_revision: string | null;
  pages_total: number;
  pages_ocr: number[];
  input_tokens: number | null;
  output_tokens: number | null;
  tokens_per_second: number | null;
  cost: string | null;
  cost_currency: string | null;
  cost_source: CostSource | null;
  warnings: string[];
}

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface AnalysisJobView {
  id: string;
  model_id: string;
  original_filename: string;
  status: JobStatus;
  proposal: FilenameProposal | null;
  metrics: RunMetrics | null;
  error: string | null;
}

export type ModelKind = "open_local" | "closed_cloud";
export type MemoryTier = "small" | "medium" | "large";

export interface ModelFile {
  path: string;
  sha256: string;
  size_bytes: number;
}

export interface ModelCatalogEntry {
  id: string;
  display_name: string;
  kind: ModelKind;
  license: string;
  repository: string | null;
  revision: string | null;
  files: ModelFile[];
  memory_tier: MemoryTier | null;
  prompt_template: string | null;
  provider: string | null;
  context_window: number | null;
}

export type InstallStatus = "not_installed" | "downloading" | "installed" | "verification_failed";

export interface ModelStatusEntry {
  entry: ModelCatalogEntry;
  status: InstallStatus;
  compatible: boolean;
  compatibility_reasons: string[];
  requires_cloud_key: boolean;
  files_done: number | null;
  files_total: number | null;
  error: string | null;
}

export type AccelerationBackend = "mps" | "cuda" | "rocm" | "cpu";

export interface SystemCapabilities {
  acceleration: AccelerationBackend;
  memory_gb: number;
  free_disk_gb: number;
}
