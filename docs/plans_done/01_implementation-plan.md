# Mac Invoice Renamer — Implementation Plan

## Objective

Build a macOS and Linux desktop app that analyzes German and English invoice PDFs and proposes filenames in this form:

`YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf`

Example: `2026-09-12_Apple_MacBook-Air_2180-EUR.pdf`

The app processes invoices locally, previews all proposals, renames approved originals, and supports Undo.

## Progress so far

Written for picking this project back up in a fresh session; update it as work continues so it stays a reliable snapshot rather than trusting conversation history.

- **Milestone 1 — Step 1a done, Step 1b not started.** A bare Tauri shell (`src-tauri/`) spawns the FastAPI worker (`backend/src/invoice_renamer/api/`) as a PyInstaller-packaged sidecar, handing it a per-launch session token and port, waiting for a stdout readiness marker only printed after uvicorn's socket bind actually succeeds (see `api/server.py`'s `_ReadyAnnouncingServer`). Verified end to end on real macOS hardware: the window opens, the worker starts, `/health` responds. Step 1b (bundling the local model + Tesseract into the spike build) has not been started. The PyInstaller-vs-Nuitka / onedir-vs-onefile packaging comparison the plan originally called for is no longer planned either — decided 2026-09-17 that the existing onefile PyInstaller sidecar stays as-is (see the Milestone 5 progress note below).
- **Milestone 2 — done.** `documents/reader.py` extracts per-page text via `pypdf`, routes low-text pages through OCR (`documents/ocr.py`, Tesseract via `pytesseract`, `documents/render.py` for `pypdfium2` rendering), and detects embedded ZUGFeRD/Factur-X XML by standard attachment filename, returning it as raw text. `extraction/models.py` (`InvoiceExtraction`, `Evidence`) and `naming/builder.py` (`build_filename_proposal`) are both done and tested. **Decided 2026-09-17: parsing that ZUGFeRD/Factur-X XML into structured fields and merging/conflict-checking it against visible text is out of scope**, not just deferred — invoices carrying it already extract fine via the normal OCR/text+LLM path, and it mainly matters for German B2B/B2G e-invoicing the user doesn't expect to receive regularly. The XML detection itself (and its test, `test_zugferd_xml_is_detected_and_decoded`) stays as-is; only the "parse it into fields" step is dropped.
- **Milestone 3 — done.** Detailed empirical results, per-model speed/accuracy data, and debugging lessons live in `docs/model_benchmark_findings.md` (a findings log, not a plan) — read that before re-deriving anything here.
  - Catalog trimmed to the 2 benchmark-validated candidates (`models/catalog_data.py`): Granite-3.3-2B-Instruct and Qwen3-0.6B. Removed as not viable: Qwen3-4B-Instruct-2507, Phi-4-mini-instruct, gemma-3-270m-it (see findings doc for why). Earlier superseded: Qwen2.5-3B-Instruct, Phi-3.5-mini-instruct.
  - Built and tested: `TransformersExtractor` (`inference/transformers_extractor.py` — pinned-revision loading, `trust_remote_code=False`, correct `BatchEncoding` handling, device-placement print, live token streaming with a prompt-token-count print so a slow `generate()` call is diagnosable instead of a silent black box); a model-agnostic benchmark/scoring harness (`evaluation/benchmark.py`) plus CLI (`scripts/benchmark_models.py`, `--models` to target specific catalog entries, incremental per-invoice report writes that merge into an existing report file rather than overwrite it); markdown-code-fence stripping in `inference/extractor.py` before JSON parsing (this, not any generation-parameter tuning, is what fixed most early "failures").
  - **Decided: Granite-3.3-2B-Instruct ships as the default; Qwen3-0.6B ships too.** Qwen3-0.6B needed correction on all 5 test invoices, including a silent malformed-number miss (`21.42` → `2142`, no warning raised) — accepted as a known limitation to fine-tune later, not a blocker.
  - A `seller`/`product_summary` scoring bug (`evaluation/benchmark.py::_values_match` was accepting a bidirectional substring match, so a vague/truncated wrong answer like `seller="a"` could score as correct against `"MediaMarkt"`) was fixed and re-confirmed against real invoices — the corrected one-directional/word-boundary matcher produced identical results to before the fix, so this bug didn't happen to affect these 5 invoices, but was worth fixing regardless.
  - The picker (`models/picker.py`) is wired to a real download/checksum/resume manager (`models/installer.py`, `api/models_routes.py`; design + rationale in `docs/plans_open/02_model-download-manager.md`): file-level resume, per-file sha256 re-verification, an atomic revision-scoped marker file so a crash mid-install is never misclassified as installed, and a lock-guarded `ModelInstallCoordinator` so concurrent download/cancel/delete requests for the same model can't race. Routes: `GET /capabilities`, `GET /models`, `POST /models/{id}/download`, `DELETE /models/{id}`. Manually smoke-tested end to end on real macOS hardware against the real Qwen3-0.6B repo (download → checksum verify → install → delete), all as expected.
  - The cloud option milestone (formerly Milestone 5, BB-08/12/13) is dropped: the app is local-only now, no OpenRouter/closed-model support. **Start here next**: BB-12's missing run-report UI, or the remaining Milestone 5 work (failure recovery, performance, accessibility) — see the Milestone 5 note below for what's already decided and done there.
- Backend verification (`cd backend`): `uv run pytest` (231 tests as of this writing), `uv run ruff format --check src tests`, `uv run ruff check src tests`, `uv run mypy src` — all green. Running the OCR tests requires `tesseract-ocr` + `tesseract-ocr-deu` installed locally (`brew install tesseract tesseract-lang` on macOS).
- **Milestone 4 — BB-10 and BB-11 done, verified on real macOS hardware.** Approve → rename → Undo confirmed working end to end by the user on their Mac (2026-09-14), including the native dialog and Undo actually reversing a rename. `app/src/lib/api/` is a hand-authored TypeScript mirror of the worker's Pydantic contracts (no OpenAPI codegen exists yet, so this isn't the `types/generated/` the repo-structure sketch describes) plus a fetch client that resolves the session endpoint via the existing `get_worker_endpoint` Tauri command. `app/src/features/batch/` covers PDF import, model selection (a single local-model list, with live download/remove controls and progress polling via `useModelCatalog` — the catalog's earlier Open/Local vs Closed/Cloud grouping was removed 2026-09-17 along with all closed/cloud-model support), job submission with per-item status polling (`useBatchWorkspace`), an editable-filename review list with extracted fields/warnings, per-item/approve-all approval, and the rename-and-Undo transaction (`useRenameTransaction`). Vitest tests pass; `npm run lint` and `npm run build` (tsc + vite) are clean.
  - **BB-11 (`src-tauri/src/commands/rename.rs`, `src-tauri/src/history/`, `src-tauri/src/fs_atomic.rs`)**: `rename_batch` re-checks every source exists immediately before renaming anything (abort-all if one is missing), resolves same-directory destination collisions by appending `(n)` before the extension, then renames each item independently through `fs_atomic::rename_no_replace` so one OS-level failure (permissions, a mid-flight race) doesn't block the rest of the batch. Successful renames are persisted to `<app-data>/rename_history.json` keyed by Unix-device/inode identity. `undo_last_rename_batch` validates every recorded entry's identity and destination availability before reversing anything, reverses independently through the same primitive, and leaves any entry that fails at the OS level in the record for a retry rather than losing track of it. `get_last_batch_summary` lets the UI show Undo availability on load/reload. Pure logic (`resolve_destination`, `preflight`, filename validation, `fs_atomic`, history load/save) has Rust unit tests; the commands themselves need a real Tauri app context and are untested beyond that.
  - **Fixed after two rounds of external review, six real bugs total** (all confirmed and fixed, most verified by new tests reproducing each): (1) `fs::rename` silently overwrites an existing destination, including a dangling symlink that `Path::exists` can't even see past preflight's check - both rename and Undo now go through `fs_atomic::rename_no_replace`. (1a) The first fix attempt used `libc::linkat` (hard-link the destination) then `libc::unlink` (remove the source) - review round two correctly caught that this pair isn't atomic either: a file written to the source *between* those two calls gets silently deleted by the unlink. Replaced with the real single-syscall primitives - `renameat2`/`RENAME_NOREPLACE` on Linux, `renamex_np`/`RENAME_EXCL` on macOS - which the kernel itself refuses atomically if the destination is occupied, no window at all; `resolve_destination`'s pre-check also switched from `exists()` to `symlink_metadata()` so it stops proposing names a dangling symlink already occupies. (2) A history-file write/load failure after files were already renamed used to propagate via `?` and discard the real per-file results together with the error - `rename_batch`/`undo_last_rename_batch` now always return what actually happened on disk, with a new `history_warning` field carrying the tracking failure separately; history writes are also now atomic (temp file + rename) instead of a direct truncating write. (2a) Review round two also caught that a *successful* rename whose `file_identity()` call failed right after (untracked for Undo) was reported as plain success with no warning at all - that path now folds into `history_warning` too, aggregated with any batch-level history-save failure. (3) The frontend correlated `rename_batch` results back to UI rows by `source_path`, which collapses when an import has a duplicate path; `RenameItemInput`/results now carry an explicit `request_id` (the batch item's own id) so duplicate paths correlate correctly regardless. (4) `useRenameTransaction`'s Undo handler only ever looked at `"renamed"` results and never checked for `"failed"` ones, so a per-file Undo failure returned by a *successful* command call produced no `undoError` at all - it now surfaces those via `undoError`, combined with any `history_warning`.
  - **This also closes BB-10's known path-capture gap**, since BB-11 needs real source paths to rename originals: `ImportDropzone` now gets them from `@tauri-apps/plugin-dialog`'s path-returning `open()` (replacing the plain `<input type=file>`, which can never expose a path) and from `@tauri-apps/api/webview`'s `onDragDropEvent` (replacing the HTML5 `drop` handler, which — confirmed live on macOS earlier — never fires for real files because Tauri intercepts native OS drag-drop before it reaches the DOM). Bytes for upload are read back through a new custom Rust command, `read_file_bytes`, deliberately not the `tauri-plugin-fs` plugin — the fs plugin's ACL scope does not auto-extend to dialog-selected paths (confirmed against the Tauri v2 docs), while a plain `#[tauri::command]` using `std::fs::read` is exempt from that scope system entirely and needs no capability entry, matching how `get_worker_endpoint` already works. Only `dialog:default` was added to `capabilities/default.json`; the drag-drop event and the custom read command needed no capability changes.
  - **Verification note, corrected while writing this entry**: `cargo` looked unavailable at first (`which cargo` fails, `crates.io` returns HTTP 403) because this sandbox's default shell PATH omits it, not because it's actually missing — it's installed via rustup at `~/.cargo/bin` and works once that's on PATH. With that fix, `cargo build`, `cargo test --lib` (27/27 passing across `commands::rename`, `fs_atomic`, and `history`), `cargo fmt --check`, and `cargo clippy --lib -- -D warnings` all pass clean for the full crate (lib + bin target), including the new `tauri-plugin-dialog` dependency — so the general dev-environment note above ("the Tauri desktop shell... cannot be built... in that environment") is stale and should be re-checked before being repeated; add `export PATH="$HOME/.cargo/bin:$PATH"` first. **Update**: the user has since run the real app on macOS and confirmed import → analyze → approve → rename → Undo all work end to end (real native dialog, real rename, real Undo reversal). **Confirmed working 2026-09-18** (real macOS hardware): Undo surviving an app restart (the persisted-history read path), and collision handling against a real pre-existing file. MPS remains a real macOS-only concern, still unverified (`ocrmac` dropped — Tesseract is used on every platform; signing isn't needed for this self-build project).
  - **Review-banner bug, found 2026-09-14, fixed 2026-09-17.** The review-list banner "Missing required fields — please review before approving." (`BatchItemRow.tsx`) used to show any time `FilenameProposal.requires_review` was true, but that flag was never only about missing fields — `naming/builder.py`'s `build_filename_proposal` also forced it true whenever `extraction.warnings` was non-empty. Seen live: an invoice with every field correctly extracted still showed the "missing fields" banner because the model had put boilerplate/disclaimer text it was unsure about into `warnings`. Fix: `FilenameProposal` gained a `missing_fields: list[str]` field (`naming/schema.py`/`naming/builder.py`), computed from the same per-field `*_missing` booleans that already fed `requires_review`. `BatchItemRow.tsx` now shows "Missing required field(s): <names>" only when `missing_fields` is non-empty, and a separate "Review the warnings below" line only when `extraction.warnings` is non-empty — the two cases no longer share one message. New backend tests assert `missing_fields` per case in `tests/naming/test_builder.py`; frontend mock proposals updated to include the new field. Backend: 248/248 tests pass, ruff/mypy clean. Frontend: lint clean; `npm run test`/`npm run build` couldn't run in this sandbox (pre-existing virtiofs `node_modules` issue — the Mac's native rollup binary doesn't work on Linux — unrelated to this change), so verify those on macOS before treating this as fully confirmed. **Follow-up incident**: the fix surfaced a pre-existing extraction-quality issue that the old misleading banner had been masking — real invoices produced warnings that were just German payment-terms/disclaimer boilerplate the model didn't know how to categorize (e.g. "Monatlich nicht wiederkehrende Beträge sind mit Zugang der Rechnung fällig."), not genuine field-confidence issues. Root cause: `inference/prompts.py`'s field instructions defined `warnings` as just "a list of short strings describing anything uncertain" — too open-ended. First attempt (tightened the instruction to scope warnings to one specific field, excluding general invoice text) reportedly didn't help — but that test happened against a stale sidecar binary (the user hadn't rebuilt it after the prompt change), so it isn't real evidence the tightening failed.
  - **Decided 2026-09-17: drop the model-generated `warnings` entirely rather than re-test the tightened prompt.** Independent evidence already in `docs/model_benchmark_findings.md` showed this mechanism unreliable in the other direction too — Qwen3-0.6B silently mis-extracted `21.42` as `2142` with no warning raised — so it wasn't reliably catching real problems either. `missing_fields` already covers missing values deterministically and needs no model cooperation. Change: `inference/prompts.py` no longer asks for a `warnings` key (and now explicitly says "Do not add any keys beyond the ones listed above"); `inference/extractor.py::_parse` defensively pops any `warnings` key the model still echoes back before Pydantic validation, since instruct-tuned models commonly include one out of training habit even when not asked. The one legitimate non-model warning source — `extractor.py`'s own "model output could not be validated: ..." message on repeated JSON/schema failure, which `evaluation/benchmark.py` depends on to detect failed extractions — is untouched. Tests updated: `test_prompts.py` now asserts `warnings` never appears in the prompt; `test_extractor.py`'s `test_model_supplied_warnings_are_dropped` (renamed from a test that used to assert the opposite) confirms a model-supplied warning is dropped while document-level warnings still pass through. All 249 backend tests pass, ruff/mypy clean. **Not yet verified against a real model/invoice** — no model is downloaded in this sandbox; the user needs to rebuild the sidecar (`scripts/build_worker_sidecar.sh`) and confirm no more boilerplate warnings appear.
  - **Update 2026-09-17: the user re-tested without rebuilding the sidecar, again.** All 5 PDFs showed the exact warning text `"gross_total: amount is handwritten and hard to read"` — which is the literal example string from the now-superseded *tightened* prompt (the intermediate attempt before dropping `warnings` entirely), verbatim, on invoices where it plainly doesn't apply. That's strong independent confirmation the sidecar was still stale (a real model producing that exact string on 5 unrelated invoices would be near-impossible otherwise) — not evidence against the "drop warnings entirely" fix. Still not verified against a real rebuilt binary.
  - **Wrapped up 2026-09-18.** The `warnings`-removal fix itself was never actually re-tested against a rebuilt binary — the investigation moved on to real-invoice prompt tuning (via the notebook mentioned below, and later the user's own separate project) before that happened, and surfaced three more concrete, unrelated small-model limitations along the way (all against Qwen3-0.6B, the user's preferred model for speed): (1) `gross_total` losing its decimal point (`21.42`→`2142`, then again `37.46`-equivalent → bare `2142` on an Anthropic invoice) — addressed with a worked-example decimal-separator instruction (German `1.234,56` vs plain `37.46`), not yet confirmed reliable; (2) `seller`/`product_summary` ignoring a requested `a-zA-Z0-9-_`-only character whitelist even with concrete examples (`"MacBook Air"` → `"MacBook-Air"`) — tested against 5 real invoices, **every single one** still had spaces/punctuation, so this prompt-only approach is confirmed unreliable, not just untested; (3) `product_summary` not dropping location/tier qualifiers (`"Platform Consumption - Region osc-fr1"`) despite an explicit worked example. Decided: prompt-only formatting compliance has a real ceiling on a 0.6B model - the recommended direction was a deterministic character-whitelist enforcement in `naming/builder.py::_normalize_segment` rather than continuing to chase prompt wording. **Done 2026-09-18**: `_normalize_segment` now keeps only `[A-Za-z0-9_-]` (previously it only stripped the OS-illegal set `/:*?"<>|` plus control characters, so periods/commas/ampersands/parens from model output still reached proposed filenames even from Granite's cleaner output), collapsing any resulting double hyphens and trimming stray leading/trailing ones; `_FORBIDDEN_CHARS_RE` is gone, subsumed by the whitelist. New tests in `tests/naming/test_builder.py` cover general punctuation stripping, the ampersand-collapse case, and leading/trailing punctuation. This does not address items (1) and (3) above (decimal-separator reliability, location/tier qualifiers in `product_summary`) - those remain prompt-quality problems a character whitelist can't fix. LoRA fine-tuning was discussed as a further option for the formatting-only behaviors specifically, but set aside for now given the real infrastructure cost (labeled data, training loop, adapter versioning) relative to the free deterministic fix. The final prompt text (decimal-separator + character-whitelist + "keep it short" instructions) was committed to `inference/prompts.py` at the user's request for real-app testing, even though its reliability on Qwen3-0.6B is now in question - Granite-3.3-2B remains the more instruction-compliant model, at the ~8 GB MPS memory cost documented above.
- **Mac crash during first inference, 2026-09-17, under investigation.** A fresh session, Granite-3.3-2B-Instruct, 5 imported PDFs, crashed the user's Mac during inference of the *first* PDF — i.e. after only one `generate()` call. This rules out "memory accumulating across repeated calls in a batch" as the cause of *this* incident (there was no batch yet), which is a different concern than the `ModelRuntime` finding below. No crash log or memory data was captured. Built `backend/notebooks/memory_debug.ipynb` (a `notebook` dependency group in `backend/pyproject.toml`: `ipykernel`, `jupyterlab`) so the user could load the model and step through single/repeated inference outside the full Tauri app, with `psutil`/`torch.mps` memory reporting at each step and instructions for watching Activity Monitor/`vm_stat` alongside it. Verified against real models on real macOS hardware (see the two findings below) — but the notebook and its dependency group were removed from this workspace 2026-09-18 once the user moved to doing this kind of exploration in a separate project outside this repo, so `backend/notebooks/` no longer exists here. The findings below remain valid; the tool that produced them doesn't live in this repo anymore.
  - **Separate finding, now confirmed not to be a problem (2026-09-18, real macOS hardware, Qwen3-0.6B, 16 GB Mac).** `ModelRuntime.get_or_load()` (`inference/runtime.py`) only calls `torch.mps.empty_cache()` inside `_unload_current()`, i.e. when *switching to a different model* — never between repeated `generate()` calls on the *same* loaded model, so this looked worth checking. Measured in `memory_debug.ipynb`'s "repeated calls" section: across 5 back-to-back `generate()` calls with no `empty_cache()` in between, `torch.mps.current_allocated_memory()`/`driver_allocated_memory()` stayed exactly flat (1.11 GB / 2.18 GB on every single call) — PyTorch's own MPS allocator reuses its cached blocks internally without needing an explicit flush. So `ModelRuntime`'s missing per-call `empty_cache()` is not a real issue; no code change needed there. Separately confirmed in the same session: `torch.mps.empty_cache()` does fully release MPS memory back to the OS when called explicitly (allocated/driver both dropped to exactly 0.00 GB after `del extractor; gc.collect(); torch.mps.empty_cache()`) — process RSS itself barely moved throughout, since Apple Silicon's unified-memory/Metal-driver allocations aren't fully reflected in a plain process RSS reading; `torch.mps.*` and system-wide `available` are the metrics that actually show what's happening. This rules out repeated-calls accumulation as a cause of the first-PDF crash too (see above) — confirms it, doesn't just leave it unrelated.
  - **Likely root cause found (2026-09-18, real macOS hardware, Granite-3.3-2B-Instruct, 16 GB Mac).** Same repeated-calls test as above but with Granite: flat again (`4.72 GB` allocated / `8.23 GB` driver on every one of 5 calls, no growth) — so it's confirmed generally, not just for Qwen. But the steady-state number itself is the real finding: **8.23 GB of MPS driver memory reserved the moment generation starts**, out of a 16 GB machine — over half the system, immediately, not built up over time. `available` dropped as low as ~2.07 GB while it ran. This lines up with the crash far better than any leak theory: it happened on the *first* inference specifically because that's the moment usage jumps straight to ~8 GB: with anything else already using a few more GB (browser, IDE, etc.), there's nothing left. Flush still works correctly on this model too (driver 8.23 GB → exactly 0.00 GB). Conclusion: not a code bug - Granite-3.3-2B's real MPS footprint is large relative to a 16 GB Mac, tighter than the "M2 with 16 GB unified memory" MVP-scope minimum implies once anything else is also running.
  - **Pre-flight memory check — done 2026-09-18.** `ModelCatalogEntry` gained an optional `estimated_memory_gb` field (`models/catalog.py`, validated positive when set) - a measured figure, not derived from file size, distinguished in `catalog_data.py`'s docstring from the HF-sourced fields around it. Both shipped models now carry their real measured MPS driver figures from the memory-debugging session above: Qwen3-0.6B `2.18`, Granite-3.3-2B `8.23`. New `analysis/memory_preflight.py::check_memory_headroom(entry, available_gb, resident_memory_gb=...)` compares the requested model's footprint plus a 1 GB headroom buffer against current free RAM, and returns a warning message or `None` (no estimate, or the margin looks fine). Wired into `AnalysisCoordinator.submit()` (`api/analyses_routes.py`) via an injectable `available_memory_gb_fn` (defaults to `psutil.virtual_memory().available`), computed once at submission and carried on the job as `memory_warning` through every subsequent poll. Frontend: `BatchItemRow.tsx` shows it as an amber advisory line, independent of review status, from `AnalysisJobView.memory_warning` → `BatchItem.memoryWarning`.
    - **Double-counting bug found and fixed same day, before this ever shipped**: the first version compared the new model's footprint against raw `available_gb` with no awareness that `ModelRuntime` (`inference/runtime.py`) caches one loaded model across jobs. Once a model was actually resident, the OS-reported available memory permanently reflected its footprint as gone, so *every subsequent submission* - even reusing the exact same already-loaded model - re-warned against a shrunken number, and switching to a much smaller model inherited the same artificially low reading instead of crediting back what the bigger resident model would free on unload. Fix: `ModelRuntime` gained `loaded_entry_id()` (a plain, cross-thread-safe attribute read, unlike `get_or_load()` itself which stays worker-thread-only); `submit()` looks up that entry's `estimated_memory_gb` (0 if none loaded or unknown) and passes it as `resident_memory_gb`, which the check adds back onto `available_gb` before comparing - correct whether the resident model is the one being reused (no new allocation needed) or a different one about to be freed. New tests: `tests/inference/test_runtime.py::test_loaded_entry_id_is_none_until_something_is_loaded`; three cases added to `tests/analysis/test_memory_preflight.py` (same-model reuse, different-model credit-back, credit too small for a much bigger model); two integration cases in `tests/api/test_analyses.py` reproducing both the reuse case and the big-to-small switch case end to end through real job submission/polling.
    - **Second bug found and fixed same day, on external review of the first fix**: crediting a resident model's full `estimated_memory_gb` back is only valid once that model has actually reached its measured steady-state footprint - right after `get_or_load()` returns but before its first `generate()` call finishes, real usage can be well under that figure (e.g. weights loaded but no activation/KV-cache memory yet). Since a second job can be submitted while the first is still mid-`generate()`, `submit()` could see the model as resident and credit its full peak before that peak was actually reached. Fix: `ModelRuntime` gained `is_warmed()`/`mark_warmed()` (`is_warmed()` cross-thread-safe like `loaded_entry_id()`; `mark_warmed()` worker-thread-only, called in `_worker_loop` right after `run_document_analysis()` succeeds); reset to unwarmed on unload/reload of a different model, preserved across cache-hit reuse of the same one. `submit()` now only passes a nonzero `resident_memory_gb` when `is_warmed()` is true. New tests: three `is_warmed`/`mark_warmed` lifecycle cases in `tests/inference/test_runtime.py`; one integration case in `tests/api/test_analyses.py` that blocks a first job mid-`generate()` (via the existing `threading.Event` pattern) and confirms a same-model second submission during that window is *not* credited.
    - Backend: 270/270 tests pass, ruff/mypy clean (two `test_server.py` subprocess-timeout failures seen on one run were confirmed environmental/flaky - passed both in isolation and on a clean full re-run). Frontend unchanged by either bugfix (backend-only). `npm run lint` and `npx tsc -b` clean; `npm run test`/`npm run build` still blocked by the pre-existing sandbox virtiofs/rollup issue - verify on macOS, including that the warning text and threshold read sensibly against a real thin-memory Mac, that switching models mid-session no longer misreports, and that a second invoice queued while the first is still analyzing doesn't get a premature "memory is fine" reading.
- **Milestone 5 (release hardening) — scope decided 2026-09-17, in progress.** The user confirmed this project is self-build/open-source only (published on GitHub for others to clone and build themselves, never distributed as a built binary) and explicitly declined CI and the onedir/Nuitka sidecar comparison (today's onefile PyInstaller sidecar, `scripts/build_worker_sidecar.sh`, stays as-is). This guts most of BB-14 as originally scoped in this doc: no macOS signing/notarization, no App Store submission/entitlements, no separate normal/App-Store Tauri configs, no CI. What's left of BB-14 for this distribution model is narrower than the milestone list below still implies (failure recovery, performance, accessibility polish on the existing app). README.md now documents the actual self-build steps (prerequisites, sidecar build, `npm install`, and both the `cargo tauri dev` and `cargo tauri build` paths below) — previously it had none at all.
  - **Real launchable `.app`, done and verified on macOS 2026-09-17.** The scoping conversation above first landed on self-builders just running `cargo tauri dev`, but the user separately asked for a real double-clickable `.app` too — that supersedes the "no installer/bundle output" framing implied above. `tauri.conf.json`'s `bundle.active` flipped to `true` (icons and the `externalBin` sidecar entry were already wired from earlier work); `cargo tauri build` now produces a real unsigned `.app` on macOS and, as an unrequested but harmless side effect of enabling bundling at all, deb/appimage/rpm on Linux (no dedicated Linux packaging pipeline/CI was built — a self-builder just gets those formats for free from the same `cargo tauri build` command if their system has the matching tooling). The user's own macOS build produced a working `Invoice Renamer.app`, but failed bundling the `.dmg` (`bundle_dmg.sh` error, likely an hdiutil/Finder-automation permissions issue — not root-caused, since the `.dmg` isn't needed for this no-distribution project anyway). Fix: a new `src-tauri/tauri.macos.conf.json` (auto-merged by the Tauri CLI on macOS) restricts `bundle.targets` to `["app"]`, so `.dmg` generation is skipped entirely. Verified in the sandbox via an isolated `CARGO_TARGET_DIR` (never the shared `src-tauri/target/`, per the virtiofs note above) building a `.deb`. **Confirmed on real macOS hardware 2026-09-17**: after deleting `src-tauri/target/release/bundle` and rebuilding, `cargo tauri build` completes cleanly with no `.dmg` attempted and no `bundle_dmg.sh` error — the `.app`-only packaging is done.
  - **BB-12 run-report UI — done 2026-09-18.** The backend was already complete and tested (`metrics/models.py`'s `RunMetrics` — total/PDF-extraction/OCR/inference timings, model id/provider/revision, OCR page list, token counts, tokens-per-second, warnings — computed in `analysis/pipeline.py` and returned on the job status endpoint in `api/analyses_routes.py`), and the frontend types/state already carried it through (`RunMetrics` in `app/src/lib/api/types.ts`, `useBatchWorkspace.ts` populates `BatchItem.metrics`); only the render was missing. `BatchItemRow.tsx` now shows a collapsible "Run details" `<details>` (model id/revision, total/inference time, page count with OCR-page count, and input/output tokens + tokens-per-second when the model returned them — currently always null in practice since `pipeline.py` never wires up token counting. **Decided 2026-09-18: not worth adding** — that row stays hidden by design, not as a pending gap. `metrics.warnings` is deliberately not re-shown here since `pipeline.py` currently sets it to the exact same list as `extraction.warnings`, which the row already renders separately. New `formatDuration` helper in `app/src/lib/format.ts`. New `BatchItemRow.test.tsx` covers the metrics-present/absent/no-tokens cases. `npm run lint` and `npx tsc -b` are clean; `npm run test`/`npm run build` couldn't run in this sandbox (pre-existing virtiofs `node_modules`/rollup issue, unrelated to this change, same as the review-banner entry above) — verify those and the real rendering on macOS.

## Development environment

- Day-to-day coding and most testing happen in a sandboxed, non-macOS environment (a Linux dev container). Pace favors small, verifiable steps over speed, since this project doubles as a learning exercise.
- Everything cross-platform builds and tests fully there: FastAPI, Pydantic contracts, the document reader, the filename builder, and local-model inference logic all run correctly on CPU. MPS is an acceleration backend, not a functional requirement, so inference logic can be developed and unit-tested without Apple Silicon.
- **Decided 2026-09-17: no `ocrmac`/Apple Vision adapter.** Tesseract is used as the OCR engine on every platform, not just as a fallback — BB-04 is done as scoped. Verified (real Tesseract 5.5.0 binary, not mocked): `tests/documents/test_ocr.py` (recognized text/line reconstruction, confidence range), `tests/documents/test_reader.py` (OCR routing for scanned/mixed/blank pages), and `tests/e2e/test_document_to_filename.py` (full scanned-PDF → OCR → extraction → filename pipeline) — 13/13 passing. Caveat: only against clean synthetic fixture PDFs (`fixtures/scanned_invoice.pdf`), not real noisy/skewed photographed scans.
- One piece stays macOS-only and cannot be verified in that environment regardless: MPS-specific behavior for BB-06. Develop it behind interfaces/mocks in the sandboxed environment, then verify the real build by running it directly on macOS hardware outside any agent session.
- The Tauri desktop shell (BB-01/BB-11 Rust code) is *not* that remaining macOS-only piece: `cargo` is present via rustup at `~/.cargo/bin` (just not on the sandbox's default shell PATH — `export PATH="$HOME/.cargo/bin:$PATH"` first), and `cargo build`/`cargo test`/`cargo fmt --check`/`cargo clippy` all run and pass here for the full crate, including linking the bin target (this container has the Linux GTK/webkit2gtk dev packages Tauri needs). What's genuinely unverified in this headless container is *running* the GUI (no display) and any macOS-specific runtime behavior (real native dialogs, drag-drop, keychain) — verify those on macOS hardware.
  - **Caveat learned the hard way**: this sandbox's `/workspaces/invoice_renamer` is a `virtiofs` mount of the *same* files as the real macOS checkout, not an isolated copy. `src-tauri/target/debug/` is therefore shared too. Tauri's `build.rs` copies the sidecar binary declared in `externalBin` from `src-tauri/binaries/<name>-<target-triple>` into the flat `target/debug/<name>` (no triple suffix) on every build where it reruns — a `cargo build`/`test`/`clippy` run in this Linux sandbox copies the *Linux* sidecar into that path, silently overwriting the macOS one a real `cargo tauri dev` on the Mac needs, which then fails to exec it (`TerminatedPayload { code: Some(126) }`, no useful message from either side, since no logger is initialized to surface the worker's own stderr — see the note on that below). This happened once already and cost real debugging time. Recovery: `rm target/debug/invoice-renamer-worker && touch binaries/invoice-renamer-worker-aarch64-apple-darwin` before the next `cargo tauri dev`, so Cargo's `rerun-if-changed` tracking re-triggers the copy from the correct source. Before running a full `cargo build`/`test`/`clippy` pass here again, either confirm nothing under `binaries/`/`tauri.conf.json`/capabilities changed since build.rs last ran in this sandbox (so it won't rerun and can't re-copy), or just warn the user their sidecar copy may need the same recovery afterward.
  - **Separately worth fixing eventually (not done)**: no logger backend (`env_logger`, `tauri_plugin_log`, etc.) is initialized anywhere in `src-tauri`, so `worker.rs`'s `log::warn!("worker stderr: ...")` calls are silent no-ops — the worker sidecar's own stderr is captured by Tauri internally but never surfaced anywhere, which is exactly what made this incident hard to diagnose (Tauri only reports an opaque exit code). Initializing a logger (even just to stderr) would make future worker-startup failures self-diagnosing instead of requiring a manual `INVOICE_RENAMER_PORT=... INVOICE_RENAMER_SESSION_TOKEN=... ./target/debug/invoice-renamer-worker` reproduction by hand.
- The app targets macOS and Linux. OCR (BB-04) is Tesseract on every platform. The local model runtime (BB-06) is pluggable per OS: CUDA/ROCm/CPU inference by default, with MPS used automatically when macOS is detected. Linux ships as a full self-build target alongside macOS (see the Milestone 5 progress note above) — no separate packaging pipeline, since self-builders just run `cargo tauri build`/`dev` themselves.
- Before the full feasibility spike, run a minimal packaging spike first: a bare Tauri shell launching a hello-world FastAPI sidecar, no PyTorch or OCR, to de-risk sidecar packaging and signing in the smallest possible slice. See Milestone 1 below.

## MVP scope

- Import one or many PDFs by file picker or drag-and-drop.
- Handle selectable text, scans, and mixed PDFs.
- Analyze with a locally installed Hugging Face model.
- Show progress, extracted fields, warnings, and editable filenames.
- Approve individual items or the full batch.
- Rename without overwriting existing files.
- Undo the last batch.
- Download, replace, and remove the local model.
- Browse all app-supported local models.
- Show run metrics: inference and total time, model/provider, warnings, and token usage when available.

Later: watched folders, Finder extensions, website deployment, automatic updates, and direct vision models.

## Defined building blocks

Each building block owns one responsibility, exposes a narrow interface, and can be tested independently.

| ID | Building block | Owner | Public interface / output | Milestone |
|---|---|---|---|---|
| BB-01 | Desktop host | Tauri/Rust | Start/stop worker, session token, native dialogs | 1 |
| BB-02 | Local API and jobs | FastAPI/Python | OpenAPI endpoints, job status, cancellation | 1 |
| BB-03 | Document reader | Python | PDF bytes → normalized pages and embedded XML | 2 |
| BB-04 | OCR adapter | Python + Tesseract OCR | Page image → positioned text and confidence | 1–2 |
| BB-05 | Extraction contract | Python/Pydantic | Normalized document → validated `InvoiceExtraction` | 2 |
| BB-06 | Local model runtime | Transformers/PyTorch | Extraction request → model response and metrics | 1–3 |
| BB-07 | Model catalog and manager | Python | Supported models, compatibility, download/remove/status | 3 |
| BB-09 | Filename builder | Python | `InvoiceExtraction` → `FilenameProposal` | 2 |
| BB-10 | Batch workspace | React/TypeScript | Import, model selection, progress, edits, approval | 4 |
| BB-11 | File transaction and Undo | Tauri/Rust | Atomic preflight, rename result, persistent Undo record | 4 |
| BB-12 | Run report | Python + React | Timings, model/provider, OCR path, tokens, warnings | 3 |
| BB-14 | Packaging pipeline | Tauri + Python tooling | Signed app containing the packaged sidecar | 1 and 5 |

Primary flow:

`BB-10 → BB-01/02 → BB-03/04 → BB-05/06 → BB-09/12 → BB-10 → BB-11`

`BB-07` supplies the local model to `BB-06`.

BB-08 (cloud adapter) and BB-13 (settings/OpenRouter key) are dropped: the app is local-only, no cloud/closed-model path. The `ocrmac`/Apple Vision OCR adapter is also dropped: Tesseract is used on every platform (see the Development environment section above for verification).

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
  model_id: string
  provider: string
  model_revision: string | null
  pages_total: integer
  pages_ocr: list[integer]
  input_tokens: integer | null
  output_tokens: integer | null
  tokens_per_second: number | null
  warnings: list[string]
```

Evidence contains page number and a short source excerpt or XML field reference. Confidence may be displayed as a hint but never approves a rename by itself.

## Local API

All requests require a random session token created by Tauri when starting the worker.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Worker and dependency status |
| GET | `/capabilities` | Acceleration backend (MPS/CUDA/CPU), OCR, memory, disk, and model readiness |
| GET | `/models` | Supported local models, availability, installation, and recommendations |
| POST | `/models/{id}/download` | Start or resume a model download |
| DELETE | `/models/{id}` | Remove an installed model |
| POST | `/analyses` | Upload PDF bytes and create an analysis job |
| GET | `/jobs/{id}` | Read status and result |
| DELETE | `/jobs/{id}` | Cancel a queued/running job |

Return `202` for long-running work. Poll job status initially; add server-sent events only if polling becomes limiting.

Job results include `FilenameProposal` and `RunMetrics`.

## Document processing

1. Validate PDF type, size, and page count.
2. Extract text page-by-page with `pypdf`.
3. Detect pages with missing or poor-quality text using documented heuristics.
4. Render those pages with `pypdfium2` and OCR with Tesseract (cross-platform).
5. Reconstruct OCR lines using bounding boxes and preserve page boundaries.
6. Send normalized text to the selected inference adapter.

Embedded ZUGFeRD/Factur-X XML is detected (by standard attachment filename) and returned as raw text, but parsing it into structured fields and merging/conflict-checking against visible text is out of scope (decided 2026-09-17 — not needed for the invoices this app actually handles; see the Progress so far note above).

OCR only the pages that need it. Cache intermediate text during the job, then remove temporary rendered pages.

## Inference design

Define one interface:

```text
extract_invoice(document_text, schema, options) -> InvoiceExtraction
```

Implement one adapter:

- `TransformersExtractor`: local Hugging Face model using the best available backend (MPS on Apple Silicon, CUDA/ROCm on Linux, else CPU), auto-selected with a manual override in settings.

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
- The model picker lists every supported local model, plus search and capability filters.
- Show every model supported by the app's extraction contract; exclude incompatible catalog entries rather than allowing broken selections.
- Models show installation state, size, license, memory recommendation, and local compatibility.
- Store weights in the app’s data container, outside the signed application bundle.
- Support resumable downloads, checksum verification, cancellation, removal, and cleanup of partial files.
- Never download or execute model repository code.
- Local inference works offline after installation.

## Privacy and security

- Local mode performs no network requests after model installation.
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
- Provider contract tests with mocked local responses.

### Evaluation set

Use 20–50 private representative invoices when available: German/English, scans, selectable text, mixed pages, multiple dates, marketplace sellers, multi-item purchases, and difficult totals.

Track field accuracy, hallucinations, correction rate, product-label usefulness, latency, peak memory, and failures. Keep a holdout set for final validation.

## Milestones

1. **Feasibility spike — BB-01, 02, 04, 06, 14:**
   - Step 1a — minimal packaging spike: a bare Tauri shell launches a hello-world FastAPI sidecar, no PyTorch or OCR; verify build and signing on macOS directly.
   - Step 1b — full spike: add the local model (logic developed and CPU-tested in the sandboxed environment, MPS behavior verified on macOS) and Tesseract OCR; confirm the build works end to end.
2. **Core processing — BB-03, 04, 05, 09:** PDF text/OCR/XML pipeline, schemas, validation, and filename builder.
3. **Local AI — BB-06, 07, 12:** model and OCR-engine benchmark, selected model, app-managed download, offline inference, and run metrics.
4. **Desktop workflow — BB-10, 11:** import, batch progress, editable previews, rename, collision handling, and Undo.
5. **Release hardening — BB-14 and full system:** packaging, failure recovery, performance, accessibility, macOS signing, optional App Store submission, and Linux packaging if pursued as a shipping target.

Each milestone must produce a runnable end-to-end slice. Do not build the full UI before the feasibility spike passes.

## MVP acceptance criteria

- Runs on a clean M2/16 GB Mac without a separate Python or model-runtime installation; runs on a representative Linux desktop if Linux ships as an MVP target.
- Processes representative German and English invoices locally.
- Never sends a local-mode invoice over the network.
- Produces valid, editable filename proposals and flags unknown values.
- Batch rename never overwrites files and can be undone safely.
- Model download is visible, resumable, verified, removable, and offline afterward.
- Every completed run shows inference time and useful execution metadata.
