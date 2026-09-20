/** Small display-formatting helpers shared across batch-workspace components. */

import type { RunMetrics } from "./api/types";

export function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${exponent === 0 ? value : value.toFixed(1)} ${units[exponent]}`;
}

export function formatAmount(amount: string | null, currency: string | null): string {
  if (amount === null) return "—";
  return currency ? `${amount} ${currency}` : amount;
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

const SOURCE_LABELS: Record<RunMetrics["extraction_source"], string> = {
  xml: "ZUGFeRD / Factur-X XML",
  xml_and_model: "XML + AI",
  model: "PDF text + AI",
};

/** How the run's field values were produced, for the Run details disclosure. */
export function describeExtractionSource(metrics: RunMetrics): string {
  const label = SOURCE_LABELS[metrics.extraction_source];
  return metrics.pages_ocr.length > 0 ? `${label} (OCR)` : label;
}

// Kept in sync by hand with extraction_router.FILENAME_FIELDS on the backend.
const FILENAME_FIELD_COUNT = 5;

/** Explains what embedded invoice XML was found and whether it was used, or
 * null when no XML attachment was ever detected (nothing worth mentioning). */
export function describeXmlDetection(metrics: RunMetrics): string | null {
  switch (metrics.xml_status) {
    case "none":
      return null;
    case "supported":
      return `Detected and used (${metrics.xml_fields_used.length} of ${FILENAME_FIELD_COUNT} fields)`;
    case "unsupported":
      return metrics.xml_profile_id
        ? `Detected but uses an unsupported invoice profile (${metrics.xml_profile_id}); used AI instead`
        : "Detected but not a supported invoice format; used AI instead";
    case "invalid":
      return "Detected but not valid XML; used AI instead";
    case "ambiguous":
      return "Multiple different invoice XML attachments found; used AI instead";
  }
}
