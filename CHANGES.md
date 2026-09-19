# Changelog

## Version 0.32.0 (2026-09-19, claude sonnet-5)

- Redesign the batch workspace toolbar and status pills, and add a collapsible Model panel

  Icon-only row actions, grouped/dividered toolbar buttons with leading
  icons, tooltips, and a green active-state highlight, a fixed-width
  status pill, a collapsible Model section that shows the selected model
  while minimized, and a 1050x950 default startup window size.

## Version 0.31.0 (2026-09-19, claude sonnet-5)

- Pulse the Analyze button and swap its label to 'Analyzing...' while a run is in flight

  Reuses the same pulsing-dot animation already shown on a running
  item's status pill, so the button keeps giving feedback even though
  it's disabled for the whole run.

## Version 0.30.0 (2026-09-19, claude sonnet-5)

- Keep the toolbar and commit bar pinned while the invoice list scrolls independently

  Also: removed the leftover '+' glyph, turned 'Shorten seller + product
  names' into a 'Shorten Names' toggle button, trimmed the drag-drop
  label to 'Drag PDF invoices here', and auto-select Qwen when it's
  already installed.

## Version 0.29.0 (2026-09-19, claude sonnet-5)

- Default to compact view and add a 'Warnings/errors only' filter button

  The filter matches the progress bar's own warnings/failed buckets (a
  shared itemHasIssue helper), so both stay in sync. Analyze is now
  green, and the compact/full view toggle stays disabled until at least
  one item has been processed (nothing to collapse before then).

## Version 0.28.0 (2026-09-19, claude sonnet-5)

- Add a 'Remove all' button to clear the invoice list in one click

  Cancels any in-flight analysis jobs for queued/running items first, same
  as removing a single item.

## Version 0.27.0 (2026-09-19, claude sonnet-5)

- Add a 'Compact view' toggle that hides the field list and run details per invoice row

  Status pill, editable proposed filename, missing-field/warning flags, and
  the action buttons all stay visible when collapsed - only the
  date/seller/product/amount list and the run-details disclosure hide.

## Version 0.26.0 (2026-09-19, claude sonnet-5)

- Add a segmented progress bar showing done/queued/warnings/failed counts above the invoice list

  Buckets are mutually exclusive: done excludes needs-review items that
  carry extraction warnings or missing fields (those count as warnings
  instead), and failed folds in cancelled items.

## Version 0.25.0 (2026-09-19, claude sonnet-5)

- Add a 'Shorten seller + product names' checkbox and show both the full and shortened values

  A global toggle, read fresh at each Analyze/Re-Run, controls a second
  model call that shortens seller/product text without overwriting the
  originals - both display in the review list. Falls back to the full text
  on failure or when off, and never invents a value for a null field.

## Version 0.24.0 (2026-09-19, claude sonnet-5)

- Use embedded ZUGFeRD/Factur-X invoice XML as the authoritative source for invoice fields

  Supported CII/EN16931 XML now wins over model output field by field, skipping
  OCR/model loading when complete; unsupported/malformed XML falls back
  unchanged. Went through three rounds of manual-testing-driven hardening since -
  see the plan's Block 6 for the full lists of gaps found and fixed.

## Version 0.23.1 (2026-09-19, claude sonnet-5)

- bugfix: sign the packaged macOS app after inserting the worker, instead of skipping signing entirely

  build_macos_app.sh only re-signed when the app already had a prior seal,
  but Tauri never signs the bundle when no signingIdentity is configured, so
  that check was always false and the shipped app carried none - failing
  Gatekeeper's first launch with an unbypassable 'app is damaged' error.

## Version 0.23.0 (2026-09-19, claude sonnet-5)

- Recognize scanned invoices on macOS with the built-in Vision framework instead of Tesseract

  macOS (dev or packaged) now uses Apple's Vision framework for OCR instead
  of Tesseract, needing no Homebrew or OCR package install; Linux is
  unchanged. Verified on macOS: English and German OCR both work in dev and
  in the packaged app, with required third-party license notices bundled.

## Version 0.22.1 (2026-09-19, claude sonnet-5)

- bugfix: resolve the worker executable directly instead of the removed externalBin sidecar API

  Block 2 removed the externalBin config the old sidecar() call needed, so
  it could no longer find anything at launch. Worker spawn now resolves the
  packaged resource path itself, only falling back to the dev staging path
  in debug builds, so a release build never masks a missing bundled worker.

## Version 0.22.0 (2026-09-18, claude opus-5)

- Bundle the worker as a PyInstaller onedir distribution

  Measured on macOS, worker startup drops from 6.24s to 0.45s, at 3x the
  on-disk size. Build the app with scripts/build_macos_app.sh from now on:
  Tauri's resource copy resolves the symlinks PyInstaller's layout relies on,
  so the worker is inserted after bundling, verified, and re-signed.

- bugfix: fix the frontend build/dev hooks failing with 'cd: app: No such file or directory'

  Tauri runs beforeDevCommand/beforeBuildCommand from inside app/ already, so
  the existing 'cd app && npm run ...' looked for a nested app/app/. Pre-dates
  this session's changes; cargo tauri build failed outright, and cargo tauri
  dev silently killed itself a few seconds after launch.

## Version 0.21.0 (2026-09-18, claude design, claude sonnet-5 , claude opus-5)

- Redesign the batch workspace visual style

  Applies a token-based light theme (color, spacing, monospace filenames,
  status pills, run-details grid) across BatchWorkspace, BatchList,
  BatchItemRow, ImportDropzone, and ModelSelector. Presentation only - no
  changes to component props, hooks, or the native window chrome.

## Version 0.20.0 (2026-09-18, claude sonnet-5)

- Add a Re-Run button for cancelled, failed, and reviewed batch items

  Cancelling an item previously left it permanently stuck, with no way to
  resubmit short of removing and re-importing the PDF. Re-Run resets the
  row and resubmits with the selected model, using a per-item generation
  counter so a stale response from a superseded run can't clobber it.

## Version 0.19.0 (2026-09-18, claude sonnet-5)

- Add an Open button to open a batch item's PDF in the system default app

  Opens the current file (the renamed destination once renamed, else the
  original source) via a new open_with_system_default Tauri command, e.g.
  Preview on macOS. Waits for the launcher's exit status on a background
  thread, so a missing default app is reported as an error, not a freeze.

## Version 0.18.2 (2026-09-18, claude sonnet-5)

- Rename the displayed 'Needs review' status to 'Awaiting approval'

  Display label only; the internal needs_review state and behavior are
  unchanged.

## Version 0.18.1 (2026-09-18, claude sonnet-5)

- bugfix: make worker shutdown reliable so it can't outlive the app

  Killing only the PyInstaller launcher left its forked Python child (and
  tesseract subprocess) orphaned; shutdown now signals the whole process
  group and confirms it's empty. Closing the window now quits the app too,
  and a closed stdout pipe with no ready marker no longer looks like success.

## Version 0.18.0 (2026-09-18, claude sonnet-5)

- Warn before a job starts if free memory looks thin for the selected model

  ModelCatalogEntry gets an optional estimated_memory_gb (measured MPS
  driver memory, set for both shipped models). POST /analyses compares it
  against free RAM, crediting back a resident model's footprint only once
  it has actually run once, and attaches an advisory memory_warning.

## Version 0.17.1 (2026-09-18, claude sonnet-5)

- bugfix: strip punctuation from filename segments deterministically instead of trusting model output

  Seller/product segments only blocked OS-illegal characters, so periods,
  commas, ampersands, and parens from model output survived into proposed
  filenames. _normalize_segment now keeps only letters, digits, hyphens, and
  underscores, collapsing any resulting double hyphens.

## Version 0.17.0 (2026-09-18, claude sonnet-5)

- Show run metrics (timings, model, pages, tokens) on each reviewed invoice

  BatchItemRow gets a collapsible 'Run details' section reading the
  RunMetrics the backend already computed but the UI never displayed:
  total/inference time, OCR page count, and token usage when available.

## Version 0.16.1 (2026-09-17, claude sonnet-5)

- bugfix: stop the review banner from claiming fields are missing when they aren't

  requires_review also went true for unrelated extraction warnings, so a
  fully-extracted invoice could show 'Missing required fields'. A new
  missing_fields list on FilenameProposal lets the UI show the accurate
  reason: which fields are actually missing, versus warnings to review.

## Version 0.16.0 (2026-09-17, claude sonnet-5)

- Enable release app bundling and document self-build steps in README.md

  Flips bundle.active to true; cargo tauri build now produces a real
  .app (verified on macOS) or deb/appimage/rpm on Linux. A macOS-only
  config skips .dmg generation, which isn't needed and failed locally.
  README.md documents the build steps and prerequisites.

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