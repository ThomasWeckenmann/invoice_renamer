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
  seller_short: string | null;
  product_summary_short: string | null;
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
  missing_fields: string[];
}

export type ExtractionSource = "xml" | "xml_and_model" | "model";
export type XmlStatus = "none" | "supported" | "unsupported" | "invalid" | "ambiguous";

export interface RunMetrics {
  total_ms: number;
  pdf_extraction_ms: number;
  ocr_ms: number;
  inference_ms: number;
  xml_ms: number;
  model_id: string;
  provider: string;
  model_revision: string | null;
  pages_total: number;
  pages_ocr: number[];
  input_tokens: number | null;
  output_tokens: number | null;
  tokens_per_second: number | null;
  warnings: string[];
  extraction_source: ExtractionSource;
  xml_status: XmlStatus;
  xml_attachment_name: string | null;
  xml_profile_id: string | null;
  xml_fields_used: string[];
  inference_ran: boolean;
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
  memory_warning: string | null;
}

export type MemoryTier = "small" | "medium" | "large";

export interface ModelFile {
  path: string;
  sha256: string;
  size_bytes: number;
}

export interface ModelCatalogEntry {
  id: string;
  display_name: string;
  license: string;
  repository: string;
  revision: string;
  files: ModelFile[];
  memory_tier: MemoryTier;
  prompt_template: string | null;
  estimated_memory_gb: number | null;
}

export type InstallStatus = "not_installed" | "downloading" | "installed" | "verification_failed";

export interface ModelStatusEntry {
  entry: ModelCatalogEntry;
  status: InstallStatus;
  compatible: boolean;
  compatibility_reasons: string[];
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
