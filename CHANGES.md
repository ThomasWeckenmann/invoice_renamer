# Changelog

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