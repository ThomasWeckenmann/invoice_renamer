# Mac Invoice Renamer — Implementation Plan

## Objective

Build a macOS and Linux desktop app that analyzes German and English invoice PDFs and proposes filenames in this form:

`YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf`

Example: `2026-09-12_Apple_MacBook-Air_2180-EUR.pdf`

The app processes locally by default, previews all proposals, renames approved originals, and supports Undo. Cloud analysis through OpenRouter is always explicit.

## Progress so far

Written for picking this project back up in a fresh session; update it as work continues so it stays a reliable snapshot rather than trusting conversation history.

- **Milestone 1 — Step 1a done, Step 1b not started.** A bare Tauri shell (`src-tauri/`) spawns the FastAPI worker (`backend/src/invoice_renamer/api/`) as a PyInstaller-packaged sidecar, handing it a per-launch session token and port, waiting for a stdout readiness marker only printed after uvicorn's socket bind actually succeeds (see `api/server.py`'s `_ReadyAnnouncingServer`). Verified end to end on real macOS hardware: the window opens, the worker starts, `/health` responds. Step 1b (bundling the local model + platform OCR into the signed/sandboxed spike build) has not been started, and neither has the PyInstaller-vs-Nuitka / onedir-vs-onefile packaging comparison the plan calls for — today's sidecar binary is a onefile dev/CI convenience build, not the shipping shape.
- **Milestone 2 — done**, except ZUGFeRD/Factur-X field parsing. `documents/reader.py` extracts per-page text via `pypdf`, routes low-text pages through OCR (`documents/ocr.py`, Tesseract via `pytesseract`, `documents/render.py` for `pypdfium2` rendering), and detects embedded ZUGFeRD/Factur-X XML by standard attachment filename — but only returns it as raw text; parsing it into fields and merging/conflict-checking against visible text is still open. `extraction/models.py` (`InvoiceExtraction`, `Evidence`) and `naming/builder.py` (`build_filename_proposal`) are both done and tested.
- **Milestone 3 — partially done; model selection is the real blocker for the rest.**
  - Done and tested: the inference interface (`inference/`: `LanguageModel` protocol, `extract_invoice()` with prompt building and one JSON-repair retry — no real model plugged in yet); `RunMetrics` (`metrics/models.py`, a contract with no production caller yet); the model catalog schema, hardware-compatibility judging, and picker view (`models/catalog.py`, `compatibility.py`, `picker.py` — schema and logic only, catalog contents are test placeholders, not real models); real hardware detection (`models/detection.py`, `psutil`-based memory/disk, a torch-free heuristic for the acceleration backend since PyTorch isn't a dependency yet).
  - Not started, and blocked on picking a real model: `TransformersExtractor` (an actual local Hugging Face model behind the `LanguageModel` protocol), the download/checksum/resume manager for installing it, a real (non-placeholder) catalog listing actual candidate models, and anything that actually constructs a `RunMetrics` from a real run.
  - **Start here next**: have the model-selection discussion this plan's "Model selection benchmark" section calls for (which 2-3 small instruct models to shortlist, quantization, what to benchmark against) — it's been deferred repeatedly and nothing else in Milestone 3 can proceed without it.
- Backend verification (`cd backend`): `uv run pytest` (124 tests as of this writing), `uv run ruff format --check src tests`, `uv run ruff check src tests`, `uv run mypy src` — all green. Running the OCR tests requires `tesseract-ocr` + `tesseract-ocr-deu` installed locally (`brew install tesseract tesseract-lang` on macOS).
- Milestones 4-6 (desktop workflow, cloud option, release hardening) are untouched.

## Development environment

- Day-to-day coding and most testing happen in a sandboxed, non-macOS environment (a Linux dev container). Pace favors small, verifiable steps over speed, since this project doubles as a learning exercise.
- Everything cross-platform builds and tests fully there: FastAPI, Pydantic contracts, the document reader, the filename builder, and local-model inference logic all run correctly on CPU. MPS is an acceleration backend, not a functional requirement, so inference logic can be developed and unit-tested without Apple Silicon.
- Four pieces are macOS-only and cannot be built or verified in that environment: the Tauri desktop shell (BB-01), the `ocrmac` OCR adapter (BB-04), MPS-specific behavior for BB-06, and macOS signing/notarization/packaging (BB-14). Develop these behind interfaces/mocks in the sandboxed environment, then verify the real build by running it directly on macOS hardware outside any agent session.
- The app targets macOS and Linux. OCR (BB-04) and the local model runtime (BB-06) are pluggable per OS: an open-source OCR engine and CUDA/ROCm/CPU inference by default, with `ocrmac`/Apple Vision and MPS used automatically when macOS is detected. Whether Linux ships as a full MVP release target (its own packaging pipeline) or stays a development/compatibility target only is an open choice, revisited when BB-14 packaging work starts.
- Before the full feasibility spike, run a minimal packaging spike first: a bare Tauri shell launching a hello-world FastAPI sidecar, no PyTorch or OCR, to de-risk sidecar packaging and signing in the smallest possible slice. See Milestone 1 below.

## MVP scope

- Import one or many PDFs by file picker or drag-and-drop.
- Handle selectable text, scans, mixed PDFs, and embedded ZUGFeRD/Factur-X data.
- Analyze with a locally installed Hugging Face model.
- Show progress, extracted fields, warnings, and editable filenames.
- Approve individual items or the full batch.
- Rename without overwriting existing files.
- Undo the last batch.
- Download, replace, and remove the local model.
- Configure one OpenRouter key and explicitly choose cloud processing.
- Browse all app-supported models in separate **Open / Local** and **Closed / Cloud** sections.
- Keep closed models visible but disabled until an OpenRouter key is configured.
- Show run metrics: inference and total time, model/provider, processing path, warnings, token usage when available, and optional cloud cost.

Later: watched folders, Finder extensions, website deployment, automatic updates, and direct vision models.

## Defined building blocks

Each building block owns one responsibility, exposes a narrow interface, and can be tested independently.

| ID | Building block | Owner | Public interface / output | Milestone |
|---|---|---|---|---|
| BB-01 | Desktop host | Tauri/Rust | Start/stop worker, session token, native dialogs | 1 |
| BB-02 | Local API and jobs | FastAPI/Python | OpenAPI endpoints, job status, cancellation | 1 |
| BB-03 | Document reader | Python | PDF bytes → normalized pages and embedded XML | 2 |
| BB-04 | OCR adapter | Python + OCR engine (open-source default, `ocrmac` on macOS) | Page image → positioned text and confidence | 1–2 |
| BB-05 | Extraction contract | Python/Pydantic | Normalized document → validated `InvoiceExtraction` | 2 |
| BB-06 | Local model runtime | Transformers/PyTorch | Extraction request → model response and metrics | 1–3 |
| BB-07 | Model catalog and manager | Python | Supported models, compatibility, download/remove/status | 3 |
| BB-08 | Cloud adapter | Python/OpenRouter | Explicit cloud request → same extraction contract | 5 |
| BB-09 | Filename builder | Python | `InvoiceExtraction` → `FilenameProposal` | 2 |
| BB-10 | Batch workspace | React/TypeScript | Import, model selection, progress, edits, approval | 4 |
| BB-11 | File transaction and Undo | Tauri/Rust | Atomic preflight, rename result, persistent Undo record | 4 |
| BB-12 | Run report | Python + React | Timings, model/provider, OCR path, tokens, cost, warnings | 3–5 |
| BB-13 | Settings and secrets | Tauri/Rust | Preferences plus OS-keychain-backed OpenRouter key (Keychain on macOS, Secret Service/libsecret on Linux) | 5 |
| BB-14 | Packaging pipeline | Tauri + Python tooling | Signed app containing the packaged sidecar | 1 and 6 |

Primary flow:

`BB-10 → BB-01/02 → BB-03/04 → BB-05/06 or BB-08 → BB-09/12 → BB-10 → BB-11`

`BB-07` supplies the local model to `BB-06`; `BB-13` supplies cloud credentials to `BB-08`.

Tauri owns source-file access and mutations. Python receives PDF bytes for analysis and never renames files. This keeps filesystem permissions and Undo in the desktop layer and makes the analysis API reusable.

## Repository structure

```text
invoice-renamer/
├── app/                         # React + TypeScript
│   ├── src/components/
│   ├── src/features/batch/
│   ├── src/features/settings/
│   ├── src/lib/api/
│   └── src/types/generated/
├── src-tauri/                   # Tauri/Rust
│   ├── src/commands/
│   ├── src/history/
│   ├── capabilities/
│   └── tauri.conf.json
├── backend/                     # Python package
│   ├── src/invoice_renamer/
│   │   ├── api/
│   │   ├── documents/
│   │   ├── extraction/
│   │   ├── inference/
│   │   ├── metrics/
│   │   ├── models/
│   │   └── naming/
│   └── tests/
├── fixtures/                    # Synthetic/non-sensitive test PDFs
├── scripts/                     # Build and contract generation
└── docs/
```

Keep private invoice samples outside Git and reference them through a local test configuration.

## Core data contract

Use Pydantic as the source of truth and generate TypeScript types from FastAPI’s OpenAPI schema.

```text
InvoiceExtraction
  invoice_date: date | null
  seller: string | null
  product_summary: string | null
  gross_total: decimal | null
  currency: ISO-4217 code | null
  language: de | en | unknown
  evidence: evidence per extracted field
  warnings: list[string]

FilenameProposal
  extraction: InvoiceExtraction
  proposed_filename: string
  requires_review: boolean

RunMetrics
  total_ms: integer
  pdf_extraction_ms: integer
  ocr_ms: integer
  inference_ms: integer
  execution_mode: local | cloud
  model_id: string
  provider: string
  model_revision: string | null
  pages_total: integer
  pages_ocr: list[integer]
  input_tokens: integer | null
  output_tokens: integer | null
  tokens_per_second: number | null
  cost: decimal | null
  cost_currency: string | null
  warnings: list[string]
```

Evidence contains page number and a short source excerpt or XML field reference. Confidence may be displayed as a hint but never approves a rename by itself.

## Local API

All requests require a random session token created by Tauri when starting the worker.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Worker and dependency status |
| GET | `/capabilities` | Acceleration backend (MPS/CUDA/CPU), OCR, memory, disk, and model readiness |
| GET | `/models` | Supported open/closed models, availability, installation, and recommendations |
| POST | `/models/{id}/download` | Start or resume a model download |
| DELETE | `/models/{id}` | Remove an installed model |
| POST | `/analyses` | Upload PDF bytes and create an analysis job |
| GET | `/jobs/{id}` | Read status and result |
| DELETE | `/jobs/{id}` | Cancel a queued/running job |

Return `202` for long-running work. Poll job status initially; add server-sent events only if polling becomes limiting.

Job results include `FilenameProposal` and `RunMetrics`. Cost is nullable and labeled as provider-reported or estimated; never fabricate a value when unavailable.

## Document processing

1. Validate PDF type, size, and page count.
2. Check for embedded ZUGFeRD/Factur-X XML and parse supported fields.
3. Extract text page-by-page with `pypdf`.
4. Detect pages with missing or poor-quality text using documented heuristics.
5. Render those pages with `pypdfium2` and OCR with the platform engine (open-source default, Apple Vision on macOS).
6. Reconstruct OCR lines using bounding boxes and preserve page boundaries.
7. Merge validated XML fields with visible text; flag conflicts.
8. Send normalized text to the selected inference adapter.

OCR only the pages that need it. Cache intermediate text during the job, then remove temporary rendered pages.

## Inference design

Define one interface:

```text
extract_invoice(document_text, schema, options) -> InvoiceExtraction
```

Implement two adapters:

- `TransformersExtractor`: local Hugging Face model using the best available backend (MPS on Apple Silicon, CUDA/ROCm on Linux, else CPU), auto-selected with a manual override in settings.
- `OpenRouterExtractor`: explicit cloud request with strict structured output.

Validate every response with Pydantic. Allow one controlled repair/retry for invalid local JSON. Missing fields remain `null`; prompts must prohibit guessing.

### Model selection benchmark

Shortlist 2–3 instruct models using these gates:

- Runs through Transformers on MPS (macOS) or CUDA/CPU (Linux).
- Fits an M2 with 16 GB on macOS; Linux memory budget is defined per tested device rather than a fixed floor.
- German and English instruction following.
- License permits intended distribution.
- Pinned revision; no `trust_remote_code`.
- Reliable structured extraction with acceptable latency.

Start with small models around 2–4B parameters. Test comparable precision/quantization where supported. Select by invoice results, not general benchmarks.

## Filename builder

Build filenames with deterministic Python functions after extraction:

1. Format invoice date as `YYYY-MM-DD`.
2. Normalize seller and product into short filename-safe segments.
3. Preserve brands/model names and transliterate `ä/ö/ü/ß`.
4. Round gross total to a whole unit and append currency.
5. Remove forbidden/control characters and collapse whitespace.
6. Apply a documented maximum length.
7. Mark missing required fields for review.

Tauri resolves collisions immediately before renaming and never overwrites a file.

## Batch rename and Undo

- React sends approved source paths and edited destination names to one Tauri command.
- Tauri validates that every source still exists and every destination is available.
- If validation fails, perform no renames.
- Apply the batch and persist source path, destination path, timestamp, and file identity.
- Undo validates identities and destination availability before reversing.
- Report partial operating-system failures clearly and retain recovery information.

Store rename history locally in the app container. Do not store invoice text in history.

## Model management

- Maintain a signed or bundled model manifest: ID, repository, revision, files, hashes, size, license, memory tier, and prompt template.
- The model picker has separate **Open / Local** and **Closed / Cloud** sections, plus search and capability filters.
- Show every model supported by the app's extraction contract; exclude incompatible catalog entries rather than allowing broken selections.
- Open models show installation state, size, license, memory recommendation, and local compatibility.
- Closed models show provider, context/capabilities, and pricing metadata when available.
- Without an OpenRouter key, closed models remain visible and greyed out with a key-setup action. Setting or removing the key updates availability immediately.
- Store weights in the app’s data container, outside the signed application bundle.
- Support resumable downloads, checksum verification, cancellation, removal, and cleanup of partial files.
- Never download or execute model repository code.
- Local inference works offline after installation.

## Privacy and security

- Local mode performs no network requests after model installation.
- Cloud uploads occur only after explicit selection and clear UI labeling.
- Store the OpenRouter key in the OS keychain (Keychain on macOS, Secret Service/libsecret on Linux).
- Redact keys, invoice text, and personal data from logs.
- Use loopback only, a per-launch token, restricted CORS, request-size limits, and worker shutdown.
- Keep telemetry disabled for the MVP.

## Build and packaging

- Frontend: Vite, React, TypeScript, and Tauri 2.
- Python: isolated package managed with `uv`.
- Package the Python worker as a standalone-directory sidecar; compare PyInstaller and Nuitka in the feasibility spike.
- Include Python, FastAPI, PyTorch, OCR bridge, and PDF libraries; model weights download separately.
- Sign nested binaries and libraries before signing the final macOS app.
- Maintain separate normal and App Store Tauri configurations for macOS.
- Linux packaging (e.g. AppImage/deb) is a separate pipeline from macOS signing/notarization; whether it is built at all for the MVP depends on the open Linux shipping-target decision above.

App Store feasibility requires an early sandbox test covering user-selected file access, sidecar execution, MPS, OCR, model storage/download, renaming, and Undo.

## Testing

### Automated

- Filename normalization, transliteration, rounding, and collisions.
- Pydantic validation and malformed model output.
- Text-quality routing to OCR.
- XML/text merge and conflict warnings.
- Batch preflight, rename, rollback, and Undo.
- API authentication, job lifecycle, cancellation, and file limits.
- Provider contract tests with mocked local/cloud responses.

### Evaluation set

Use 20–50 private representative invoices when available: German/English, scans, selectable text, mixed pages, multiple dates, marketplace sellers, multi-item purchases, and difficult totals.

Track field accuracy, hallucinations, correction rate, product-label usefulness, latency, peak memory, and failures. Keep a holdout set for final validation.

## Milestones

1. **Feasibility spike — BB-01, 02, 04, 06, 14:**
   - Step 1a — minimal packaging spike: a bare Tauri shell launches a hello-world FastAPI sidecar, no PyTorch or OCR; verify build and signing on macOS directly.
   - Step 1b — full spike: add the local model (logic developed and CPU-tested in the sandboxed environment, MPS behavior verified on macOS) and platform OCR (open-source engine, or Apple Vision on macOS); confirm the signed/sandboxed build works end to end.
2. **Core processing — BB-03, 04, 05, 09:** PDF text/OCR/XML pipeline, schemas, validation, and filename builder.
3. **Local AI — BB-06, 07, 12:** model and OCR-engine benchmark, selected model, app-managed download, offline inference, and run metrics.
4. **Desktop workflow — BB-10, 11:** import, batch progress, editable previews, rename, collision handling, and Undo.
5. **Cloud option — BB-08, 12, 13:** OS keychain, OpenRouter adapter, explicit cloud selection, cost display, and privacy UI.
6. **Release hardening — BB-14 and full system:** packaging, failure recovery, performance, accessibility, macOS signing, optional App Store submission, and Linux packaging if pursued as a shipping target.

Each milestone must produce a runnable end-to-end slice. Do not build the full UI before the feasibility spike passes.

## MVP acceptance criteria

- Runs on a clean M2/16 GB Mac without a separate Python or model-runtime installation; runs on a representative Linux desktop if Linux ships as an MVP target.
- Processes representative German and English invoices locally.
- Never sends a local-mode invoice over the network.
- Produces valid, editable filename proposals and flags unknown values.
- Batch rename never overwrites files and can be undone safely.
- Model download is visible, resumable, verified, removable, and offline afterward.
- Cloud processing requires an explicit action and uses the OS-keychain-stored OpenRouter key.
- Open and closed models are clearly separated; closed models are disabled without a key.
- Every completed run shows inference time and useful execution metadata; cloud cost is shown when available.
