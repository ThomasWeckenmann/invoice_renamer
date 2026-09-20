# Live memory monitoring and model information

Status: Proposed. Planning only; implementation has not started.

## Goal

Replace the one-time, per-invoice memory warnings with a persistent display
of measured memory use. Help users choose between the two models by showing
download size and the benchmark-backed accuracy/speed tradeoff before download.

## Current evidence

- `analysis/memory_preflight.py` compares a fixed catalog memory figure plus
  1 GB headroom against available system RAM. `api/analyses_routes.py` runs
  this once at submission and credits an already warmed model using another
  fixed catalog figure. This is not a live measurement of the current model.
- The catalog figures (8.23 GB for Granite and 2.18 GB for Qwen) were measured
  on one Mac. They cannot describe every machine, document, or inference
  phase. The user's reported incorrect warnings have not been reproduced
  with live measurements; do not claim a specific runtime cause is proven.
- `BatchItemRow.tsx` displays the stored warning only while queued/running.
  It provides no memory information before submission or after completion.
- `ModelSelector.tsx` already sums catalog file sizes in the Download button.
  That information disappears when the button is replaced by install status
  or Remove, and neither model has a tradeoff description.
- `docs/model_benchmark_findings.md` confirms Granite's better extraction
  accuracy and Qwen's faster inference on the existing invoice benchmark.
  Qwen remains the existing default when installed. No new benchmark is
  needed to justify this qualitative comparison.

## Product decisions

- Show a compact memory indicator in the Model section, outside its
  collapsible contents. Keep it visible before, during, and after analysis.
- Sample approximately once per second while the workspace is visible,
  including while idle. Pause polling when hidden and refresh on return.
- Show separately labelled measurements:
  - System RAM available / total.
  - Inference process RAM (the Python worker's resident memory).
  - GPU memory allocated by the inference backend, when supported.
- Do not label worker RAM as total application RAM: it excludes the Tauri
  shell, webview, and separate subprocesses. Do not add RAM and GPU figures
  together; on unified-memory hardware they can overlap.
- GPU counters must identify their meaning. On MPS, show Metal driver
  allocations including caches; on CUDA/ROCm, show allocated tensors and
  reserved memory separately. Reserved memory includes allocated memory.
- `model.get_memory_footprint()` measures parameters and buffers, not total
  inference memory. It is not the primary live counter. A model-weights
  detail may be measured once after loading if useful, but is not required
  for this feature and must never be presented as total runtime usage.
- Show unavailable or stale measurements explicitly instead of zero or a
  reassuring status. A failed memory read must not interrupt analysis.
- Remove the old estimated-headroom warnings. Do not replace them with new
  arbitrary thresholds, automatic model switching, or inference blocking.
  Preserve actual inference errors and existing installation compatibility
  checks; these are separate from the submission-time memory warning.
- Keep model size and descriptions visible for every installation state:

  | Model | Approximate download | Description |
  |---|---:|---|
  | Qwen3 0.6B | 1.5 GB | Faster inference and lower memory use |
  | Granite 3.3 2B Instruct | 5.1 GB | Better extraction accuracy in our benchmarks, but slower inference and higher memory use |

- Calculate sizes from `entry.files[].size_bytes`, never from duplicate
  hardcoded size values. Use decimal GB for these labels (1 GB = 10^9 bytes).
  The current shared `formatBytes` divides by 1024 while labelling GB;
  avoid using that convention for these new values. Use a clearly scoped
  formatter and apply it consistently to the new memory and model labels.
- Label file size as Download size, distinct from runtime memory. It is the
  catalog's complete file total, not a promise about remaining transfer,
  temporary installation space, or exact filesystem allocation.
- Keep the copy qualitative; do not promise a fixed latency or speed ratio.
  Do not change model selection defaults, prompts, or extraction behavior.

## Block 1: Measured memory API

- Add a focused memory sampling module and a small API route, proposed
  `GET /memory`, registered through `api/app.py` using the existing API
  client/authentication conventions.
- Return a typed snapshot with sampling time, system total/available bytes,
  worker RSS bytes, actual runtime device, and nullable GPU measurements.
  Make unsupported counters distinguishable from a measured zero and allow
  partial snapshots when an individual provider fails.
- Use `psutil.virtual_memory()` and `psutil.Process().memory_info().rss` for
  system and worker measurements. `psutil` is already in use.
- For an initialized MPS runtime use `torch.mps.driver_allocated_memory()`.
  For initialized CUDA/ROCm use the selected device's allocated/reserved
  counters. CPU-only execution has no GPU measurement.
- Preserve lazy inference initialization: opening the app or polling memory
  must not load a model, import the heavy inference stack unnecessarily, or
  initialize an unused GPU backend. Report GPU measurements as unavailable
  until the actual runtime is ready.
- Expose only small runtime metadata snapshots needed by the sampler.
  `ModelRuntime` currently relies on a single analysis worker owning model
  mutations. Do not expose the extractor to request handlers or hold a
  model/inference lock for the duration of sampling. Ensure snapshots stay
  coherent through loading, switching, failed loading, and unloading.
- Sample independently of the analysis queue so requests can complete while
  generation runs. Do not call GPU synchronization, garbage collection, or
  cache clearing to make measurements look smaller or more exact.
- Add matching frontend API types and a request helper alongside the
  existing manually maintained API contracts.

Checks: injected counter tests for CPU, MPS, CUDA/ROCm, partial failures,
uninitialized runtimes, and model transitions; API response contract tests;
a request during a deliberately blocked fake inference must still return.

Acceptance: memory snapshots work before the first job and during inference
without loading a model or waiting for generation to complete.

## Block 2: Persistent memory display and warning removal

- Add a focused `useMemoryStatus` hook and `MemoryStatus` component under
  `app/src/features/batch/`, keeping polling and rendering logic out of the
  existing large workspace orchestrators.
- Fetch immediately, then schedule the next request after the prior request
  settles. Allow no overlapping polls; clean up timers and pending requests
  on unmount, and ignore late responses from obsolete requests.
- Pause while the document is hidden, resume immediately when visible, and
  use bounded retry delays after errors. A failed endpoint must not cause a
  tight request loop or repeated alert messages.
- Mount the indicator outside the Model section's collapse condition.
  Provide readable labels, consistent units, and compact wrapping on narrow
  windows. Avoid a screen-reader live announcement on every numeric update.
- On a failed refresh, mark the last measurement stale or show unavailable;
  recover automatically after a successful refresh. The user should not
  mistake cached numbers for current measurements.
- Remove `memory_warning` / `memoryWarning` from job creation, API responses,
  frontend mappings/types, row rendering, and associated styles/fixtures.
  Delete `analysis/memory_preflight.py` and its obsolete tests.
- Remove catalog `estimated_memory_gb` values/validation and runtime warmed
  bookkeeping if their only consumers are the removed preflight mechanism.
  Audit references first; retain any independently necessary runtime state.
  Update comments and contract fixtures alongside those removals.

Checks: polling lifecycle with fake timers, stale/recovery states, unavailable
GPU readings, collapse persistence, and removal of per-invoice warnings.
Retain coverage that submission, repeated jobs, and model switching work
after preflight bookkeeping is removed.

Acceptance: users can inspect current memory without starting inference;
numbers keep refreshing during analysis and no old RAM warning appears.

## Block 3: Model size and benchmark-backed descriptions

- Add a short user-facing description to catalog entries and propagate it
  through existing model API types. Keep descriptions beside catalog data
  rather than scattering model-ID-specific text through UI components.
- Use the exact qualitative descriptions above for the two shipped models.
  The evidence is the existing local benchmark findings, not an assumption
  that every larger model must outperform every smaller model.
- Move the download-size information into persistent model-row details,
  beside the description, so users can compare models before downloading
  and after installing. Keep button labels concise and avoid duplicating
  the same size in both the row and button.
- Preserve selection, download progress, retry, removal, compatibility
  reasons, and Qwen's existing automatic selection behavior.

Checks: calculated decimal sizes round to 1.5 GB and 5.1 GB; descriptions and
sizes remain visible across installation states; existing model actions and
selection tests still pass.

Acceptance: both models clearly communicate download size, speed, memory,
and the established accuracy tradeoff before the user chooses a download.

## Block 4: Happy-path end-to-end verification

- Cover the complete flow across the preceding blocks: open the workspace,
  see memory measurements before any inference, compare both models' sizes
  and descriptions, download/select a model, import a synthetic invoice,
  start analysis, observe memory updates while generation is active, and
  review the completed result without any obsolete memory warning.
- Collapse the Model section during analysis and verify the memory display
  remains visible. Run another invoice using the cached model, then switch
  models and verify the displayed runtime device/measurements remain valid.
- Exercise the real API and UI together with controlled download/inference
  providers and deterministic memory readings where the existing test
  infrastructure permits. Do not describe isolated component tests as an
  end-to-end test. If no suitable browser harness exists, record and execute
  this as an explicit manual end-to-end test alongside automated API and
  workspace integration coverage, without adding a large test framework
  solely for this feature.
- Repeat the memory portion on the target Mac with a real installed model
  to verify counters and polling work during actual generation, rather than
  only between calls. Use existing private samples locally if needed.

Acceptance: the complete user flow passes, with live readings before,
during, and after inference, persistent model information, and unchanged
analysis/review behavior. Record the automated and manual results separately.

## Block 5: Security, sanity, and safety

- Verify the new route inherits session-token authentication and existing
  origin restrictions. Unauthenticated requests must fail; do not add
  filesystem paths, invoice contents, prompts, or environment details to
  the memory response.
- Check unsupported devices, unavailable counters, worker disconnects,
  failed model loads, and model switching during polling. A measurement
  failure must neither fail an analysis job nor display a false zero.
- Verify responses contain finite, non-negative byte counts where available,
  timestamps distinguish stale readings, and nullable fields preserve the
  difference between unsupported measurements and real zero allocations.
- Confirm GPU numbers refer to the actual execution device, reserved memory
  is not added to allocated memory, and unified RAM/GPU figures are never
  summed. Confirm decimal units match the displayed values.
- Check that polling neither initializes an unused accelerator nor invokes
  synchronization/cache-clearing operations, accumulates timers, or queues
  requests behind inference. Inspect actual request timing on the target
  machine and investigate raw readings/errors before changing behavior.
- Confirm descriptions match the documented benchmark conclusions, model
  sizes come from catalog files, and compatibility checks and real
  out-of-memory errors remain intact after warning removal.
- Inspect narrow-window layout and keyboard/screen-reader usability; numeric
  refreshes must not steal focus or produce constant announcements.

Acceptance: telemetry stays observational, bounded, accurately labelled,
and resilient to unavailable measurements without exposing private data or
changing inference decisions.

## Verification and implementation bookkeeping

Run focused tests for each block, then the repository checks after the API
and UI changes are integrated:

```bash
cd backend
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
```

```bash
cd app
npm run lint
npm run test
npm run build
```

On the target Mac, inspect raw counter readings alongside the displayed
values before loading, during inference, and after completion. Confirm
updates continue during real generation and document measurement scope
when comparing with Activity Monitor; the counters need not match its
different memory accounting exactly. Use existing private benchmark data
locally if needed; do not add invoices to the repository.

No changelog entry for this planning-only change. Once implementation starts,
update `CHANGES.md` according to the repository version convention. Leave
staging and committing to the user.
