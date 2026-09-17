# Changelog

## Version 0.15.0 (2026-09-17, claude sonnet-5)

- Generate macOS/Linux Tauri icons from the new app logo

  The source PNG at app/src/assets/logo.png generated the icon.icns and
  PNG icon set in src-tauri/icons, wired into tauri.conf.json's
  bundle.icon (bundle.active stays false). Showing the logo in-app was
  tried and reverted per user feedback: it looked out of place.

## Version 0.14.0 (2026-09-17, claude sonnet-5)

- Remove closed/cloud model support and drop the catalog's kind field

  The model catalog, picker, and API now describe local models only; kind,
  provider, context_window, and requires_cloud_key are gone from both the
  Python/TypeScript contracts and the model picker UI (one flat list, no
  open/closed split). R&D and implementation-plan docs updated to match.

## Version 0.13.0 (2026-09-14, claude sonnet-5)

- Add the file rename transaction and Undo (BB-11), with native file import to unblock it

  Approved invoices can now be renamed in place: collisions resolve
  automatically, rename/Undo use an atomic no-overwrite primitive, and
  Undo persists across restarts. Import now uses the native dialog and
  window drag-drop, since a browser file input can't expose a real path.

## Version 0.12.0 (2026-09-14, claude sonnet-5)

- Add the batch workspace UI: import, model selection, progress, review, and approval

  React feature (app/src/features/batch) covers PDF import, model
  selection/download, job progress polling, editable filename review, and
  per-item/batch approval. Rename-commit is deferred to the file-transaction
  layer; two cancel/poll races found in review are fixed.

- bugfix: Fix cargo tauri dev failing to find app/package.json
- bugfix: Allow the cargo tauri dev origin through the worker's CORS policy
- Remove leftover Windows-only code paths

## Version 0.11.0 (2026-09-14, claude sonnet-5)

- Add the invoice analysis pipeline and its job API

  POST /analyses, GET /jobs/{id}, and DELETE /jobs/{id} run an uploaded PDF
  through read -> extract -> filename-proposal on one serialized worker
  thread. Load installed models locally and defer heavy inference imports
  until needed so worker startup stays fast.

## Version 0.10.0 (2026-09-14, claude sonnet-5)

- Add the model download/checksum/resume manager and its API routes

  Local models can now be installed/removed (resumable per-file, sha256
  verified) via GET /capabilities, GET /models, POST /models/{id}/download,
  and DELETE /models/{id}. Frontend/UI wiring is out of scope here.

## Version 0.9.1 (2026-09-14, claude sonnet-5)

- Confirm Qwen3-0.6B ships alongside Granite-3.3-2B-Instruct (default), re-validated against real invoices

  The seller/product_summary scoring fix was re-run against real invoices and
  confirmed correct (identical results to before the fix). Decided to ship
  Qwen3-0.6B now despite its known accuracy gaps, to refine later rather than
  gate on further validation. Findings: `docs/model_benchmark_findings.md`.

## Version 0.9.0 (2026-09-13, claude sonnet-5)

- Add local invoice extraction: a Transformers backend, a benchmark harness, and a trimmed two-model catalog

  Catalog now lists Granite-3.3-2B-Instruct (provisional default) and
  Qwen3-0.6B (available, not yet recommended) after benchmarking five
  candidates. Includes a gross_total net-vs-gross prompt fix and a
  seller/product_summary scoring fix. Findings: `docs/model_benchmark_findings.md`.

## Version 0.8.0 (2026-09-13, claude sonnet-5)

- Add real hardware-capability detection: memory, free disk, and acceleration backend

  Acceleration detection is a torch-free heuristic (platform/arch for MPS,
  nvidia-smi/rocm-smi presence for CUDA/ROCm, else CPU) since PyTorch isn't a
  dependency yet. Should be replaced by torch's own availability checks once
  a model is chosen and TransformersExtractor exists.

## Version 0.7.0 (2026-09-13, claude sonnet-5)

- Add the model catalog: entry schema, hardware compatibility, and the picker view

  ModelCatalogEntry enforces different required fields per open/local vs.
  closed/cloud entries and rejects negative file sizes. Compatibility checks
  memory unconditionally but only judges free disk space against models not
  yet installed, so an installed model isn't penalized for its own size.

## Version 0.6.0 (2026-09-13, claude sonnet-5)

- Add the RunMetrics contract: per-run timings, execution path, tokens, and labeled cost

  Cost is nullable and, per the plan, never fabricated: whenever set, it must
  be finite and paired with a currency and a cost_source (provider_reported
  vs. estimated). Currency codes and tokens_per_second are checked for real
  ISO-4217 membership and finiteness, not just shape, rejecting NaN/Infinity.

## Version 0.5.0 (2026-09-13, claude sonnet-5)

- Add the invoice extraction interface: prompting, JSON validation, and one repair retry

  extract_invoice() sits behind a LanguageModel protocol so TransformersExtractor
  and OpenRouterExtractor can share one parse/validate/repair flow once built.
  Repair calls repeat the original prompt (generate() has no guaranteed history),
  and document-level warnings carry through on both the success and fallback paths.

## Version 0.4.0 (2026-09-13, claude sonnet-5)

- Add the BB-04 OCR adapter: Tesseract-based text recovery for pages without a text layer

  Pages lacking usable text are now rendered via pypdfium2 and OCR'd with
  Tesseract, reconstructing line breaks from its word bounding-box groupings
  and reporting per-page confidence. Requires the tesseract-ocr binary plus
  eng/deu language data installed locally; ocrmac stays a future macOS adapter.

## Version 0.3.0 (2026-09-13, claude sonnet-5)

- Add the Milestone 2 core-processing pipeline: PDF reader, extraction contract, and filename builder

  The document reader extracts per-page text and flags pages needing OCR
  without running an OCR engine yet (deferred to Milestone 3, per the plan).
  It also detects embedded ZUGFeRD/Factur-X XML by presence, not yet parsing
  individual fields. Synthetic PDF fixtures now live under fixtures/.

## Version 0.2.0 (2026-09-13, claude sonnet-5)

- Add the Milestone 1 feasibility-spike walking skeleton: FastAPI worker, Tauri shell, and frontend placeholder

  The Tauri shell spawns the FastAPI worker as a PyInstaller sidecar with a
  per-launch session token and port, waiting for its readiness signal before
  exposing the endpoint to the frontend. Verified end-to-end on Linux; macOS
  signing and the onedir/Nuitka packaging comparison remain open per the plan.

## Version 0.1.0 (2026-09-13, claude sonnet-5)

- Initial project skeleton.