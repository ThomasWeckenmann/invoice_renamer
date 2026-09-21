# Swap the local inference runtime from Transformers/PyTorch to llama.cpp

Status: Block 1 code complete (`inference/llamacpp_extractor.py` +
`tests/inference/test_llamacpp_extractor.py`, all unit tests/ruff/mypy
green). Live verification against a real GGUF file is deferred to Block 6
by user decision (test project, no real-weight download for now) — see the
note at the end of Block 1.

## Goal

Replace the app's local-model inference stack — currently Hugging Face
Transformers driving PyTorch, running 16-bit weights on Metal/CUDA/CPU — with
llama.cpp running quantized GGUF weights, for lower RAM use and faster
inference (see `_temp/modell-laufzeiten-und-quantisierung.md`, a prior
conversation note: no runtime change or catalog entry was decided there —
the user has since decided on llama.cpp specifically, not the MLX/PyTorch
dual-track option that note also discussed). Ship a single runtime for our
supported platforms (macOS and Linux), replacing Transformers/PyTorch model
inference rather than running two inference backends side by side.
In the shipped app, Transformers remains solely for torch-free tokenizer/chat-template
rendering. Benchmark tooling and its existing Transformers execution path are
out of scope and remain unchanged for now.

Per the user: no automated happy-path end-to-end block in this plan — the
result will be tested live instead. A security/sanity block is still
included, per standing planning convention. Each block below still specifies
automated `Checks` where one is feasible without real model weights, matching
the existing test suite's own pattern of using fakes/mocks rather than
downloading multi-GB files in CI.

## Current evidence

### Inference stack (all touched — this is the whole point of the swap)

- `TransformersExtractor` (`backend/src/invoice_renamer/inference/transformers_extractor.py`)
  is the only `LanguageModel` (`inference/language_model.py:11`) implementation
  today. `LanguageModel` is a one-method Protocol — `generate(prompt: str) -> str`
  — so a new backend only needs to satisfy that shape; nothing in
  `inference/extractor.py` (retry/repair/salvage orchestration) or
  `inference/shortener.py` (field-shortening pass) is Transformers-specific,
  and neither needs to change.
- `_load_model_and_tokenizer` (`transformers_extractor.py:31-51`) always loads
  `torch.bfloat16` weights (line 43) and moves them onto the resolved device
  with `.to(device)` (line 45), confirming actual placement afterward (line 49)
  since `.to()` can silently no-op — this is exactly the RAM cost the
  `_temp` note measured (1.5 GB / 5.1 GB in 16-bit for the two shipped models).
- `generate()` (`transformers_extractor.py:126-181`) builds a single
  `{"role": "user", ...}` chat message and calls
  `tokenizer.apply_chat_template(..., enable_thinking=False)` (lines 130-142).
  `enable_thinking=False` is load-bearing, not cosmetic: per
  `docs/model_benchmark_findings.md` ("Lessons that cost real time"), Qwen3's
  hybrid think/non-think template defaults to thinking mode and emits an
  unfenced `<think>...</think>` block that broke JSON parsing 100% of the
  time before this was added. Whatever replaces this call must preserve that
  behavior for Qwen3, not just produce *a* chat-formatted prompt.
- `ModelCatalogEntry.prompt_template` (`models/catalog.py:41`) is set today
  (`"granite-instruct"` / `"chatml"` in `models/catalog_data.py:19,89`) but is
  **dead** — grepping the backend finds no code that reads it; only the
  frontend type mirror (`app/src/lib/api/types.ts:99`) and its test fixtures
  reference it. It becomes load-bearing for the first time in this plan (see
  Block 1).
- `ModelRuntime` (`inference/runtime.py:79-155`) caches one loaded model,
  keyed on `(entry.id, entry.revision)` (line 115), and is explicitly *not*
  thread-safe by design — only the single analysis worker thread calls
  `get_or_load()`/`unload()` (class docstring, lines 80-85). `select_device()`
  (lines 41-63) maps `AccelerationBackend` to a torch device string and then
  confirms the guess against the actually-installed torch build
  (`torch.cuda.is_available()` / `torch.backends.mps.is_available()`),
  downgrading to `"cpu"` if the guess doesn't hold — capability *detection*
  (`models/detection.py`) is deliberately torch-free so listing models stays
  cheap at startup (`detection.py:1-11`); only this confirmation step, called
  right before a load, pays the torch-import cost. `_unload_current()`
  (`runtime.py:141-155`) does `del self._extractor`, `gc.collect()`, then
  `torch.cuda.empty_cache()` / `torch.mps.empty_cache()` — all torch-specific.
  `LoadInstalledFn` (`runtime.py:66-76`) references `TransformersExtractor`
  only under `TYPE_CHECKING` (line 16), so swapping the concrete type is a
  one-line change plus whatever the new class is actually named.
- `select_device()` is called from exactly one production call site —
  `AnalysisCoordinator._run_job`'s `load_model()` closure
  (`api/analyses_routes.py:309-316`), immediately before
  `self._runtime.get_or_load(entry, self._data_dir, device)` (line 315).
- `inference/memory_status.py` samples GPU memory only once a model is
  already loaded onto a device (`sample_memory`, lines 86-120,
  gated on `runtime_device is not None and runtime_device != "cpu"` at
  line 101) via `_gpu_memory()` (lines 55-83): MPS reports
  `torch.mps.driver_allocated_memory()` (line 65, a single driver-wide
  figure, no allocated/reserved split); CUDA/ROCm report
  `torch.cuda.memory_allocated()` / `memory_reserved()` (lines 78-79). Both
  branches import `torch` lazily and are entirely torch-specific. Worker RSS
  (`_worker_rss()`, lines 51-52) and system totals
  (`_system_memory()`, lines 46-48) are torch-free and unaffected by this plan.
- `MemoryStatus.tsx` renders whatever shape `GpuMemorySnapshot` sends:
  `GPU_BACKEND_LABELS`/`GPU_TOOLTIPS` (lines 8-20) are keyed by
  `"mps"/"cuda"/"rocm"`, and `describeGpu()` (lines 31-43) branches on
  `driver_allocated_bytes` vs `allocated_bytes`/`reserved_bytes` — the exact
  two shapes `memory_status.py`'s two branches produce today. This is driven
  entirely by backend output; no frontend change is needed beyond whatever
  new shape the backend sends.

### Model catalog, install, and compatibility (mostly reusable as-is)

- `ModelCatalogEntry` (`models/catalog.py:33-49`) and `ModelFile`
  (`catalog.py:20-30`, `path`/`sha256`/`size_bytes`) are format-agnostic — a
  GGUF file (or a small set of GGUF shards, the same way Granite's two
  safetensors shards are two `ModelFile` entries today,
  `catalog_data.py:44-58`) fits the existing schema with no change.
- `models/installer.py` — `install_dir_for()` (line 37-39),
  `is_installed()` (80-90), `install()` (131-179, resumable at file
  granularity, sha256-verified, atomic marker write) — downloads via
  `hf_hub_download` (`_default_fetch`, `installer.py:123-128`) and is
  completely agnostic to what kind of file it's fetching. It supports only
  one repository/revision per catalog entry and downloads only listed files.
  It can remain unchanged only if that pinned repository contains both the
  GGUF weights and the matching tokenizer/template assets (see Block 2).
- `models/compatibility.py`'s per-tier memory minimums
  (`_MEMORY_TIER_MINIMUMS_GB`, lines 12-16: 8/16/32 GB for
  small/medium/large) were set for 16-bit Transformers weights and are
  almost certainly too conservative for 4-bit GGUF — worth revisiting once
  real numbers exist, not before.
- `models/picker.py` (`build_model_picker`) and the `/models`,
  `/models/{id}/download`, `/models/{id}` routes
  (`api/models_routes.py`) are entirely generic over `ModelCatalogEntry`
  and `ModelInstallCoordinator`'s per-model download state. Their catalog
  imports must select the new app-only GGUF catalog (Block 2).
- `RunMetrics.provider: str` (`metrics/models.py:41`) is hardcoded to the
  literal `"transformers"` in three places:
  `analysis/pipeline.py:105,162,329` and three more in
  `evaluation/benchmark.py:264,328,401`. Grep confirms these six are the
  only production writers; `backend/tests/analysis/test_pipeline.py:76`
  and `backend/tests/metrics/test_models.py:16,49` assert the literal.

### Benchmark harness

- `backend/scripts/benchmark_models.py` is a real CLI, not test code
  (module docstring: "Requires real model downloads... run on a machine
  with the disk and memory to hold them, not in a constrained sandbox").
  It calls `TransformersExtractor.load(entry.repository, entry.revision,
  device=args.device)` directly against the Hub (line 95), bypassing the
  installed-model data directory entirely, with `--device` accepting
  `"cpu, mps, or cuda"` (line 29) passed straight through.
  `evaluation/benchmark.py`'s `run_invoice`/`run_benchmark` are already
  generic over `LanguageModel`. Leave this CLI, its device flag, evaluation
  code, provider literals, results, and findings document unchanged. Do not
  require benchmark runs or new benchmark measurements in this plan.
- `docs/model_benchmark_findings.md` provides historical accuracy context:
  Granite-3.3-2B-Instruct and Qwen3-0.6B's
  per-field accuracy on the existing 5-invoice private set (`gross_total`
  80%/40%, `seller` 60%/60%, `product_summary` 80%/60%, `invoice_date`
  100%/60%, `currency` 100%/100%) were measured on 16-bit Transformers/MPS
  weights. Quantization can shift accuracy in either direction and hasn't
  been measured for these two models at all yet.

### Packaging and dependencies

- `backend/pyproject.toml:10-25` lists `torch>=2.9.1` and
  `transformers>=5.17.0` as unconditional dependencies, with a Linux-only
  CPU-wheel index pin (`[tool.uv.sources]`, lines 56-62) to avoid pulling
  multi-GB CUDA libraries in dev/CI. Preserve the legacy development setup
  needed by the unchanged benchmarks/tests; exclude torch from the shipped
  app build. Retain transformers for tokenizer/template rendering.
- `scripts/build_worker_sidecar.sh` runs PyInstaller (`--onedir` by
  default) against `packaging/worker_entrypoint.py`, staging the result
  into `src-tauri/resources/worker` for Tauri to bundle. It has no
  model-runtime-specific logic — the risk is whether PyInstaller correctly
  discovers and bundles whatever compiled shared library/extension the new
  Python binding ships (this is unverified; see Block 5).
- Per `AGENTS.md`'s Linux build isolation section, backend dependency
  installs and worker builds on this Linux devcontainer must go through
  `scripts/linux_workspace.sh` — installing a native-extension dependency
  (a compiled `.so`) directly in this shared checkout would overwrite the
  Mac host's own copy exactly the way `torch`'s CPU wheel already does today.
- `README.md:27-49` documents the build/run steps and states the two
  shipped models by name with their measured speed/accuracy tradeoff
  (line 49); `README.md:116` states both models' license via the catalog.
  Update descriptions for the concrete GGUF app entries without adding new
  timing claims; benchmark measurement is deferred.

## Product decisions

- **Replace inference in the app only.** The app uses llama.cpp with no
  Transformers inference fallback or runtime-selection flag. Keep the legacy
  extractor, catalog, and their tests for the unchanged benchmark workflow;
  their later migration/removal is outside this plan. The MLX-on-Mac-plus-PyTorch-elsewhere
  option discussed in `_temp/modell-laufzeiten-und-quantisierung.md` is not
  what was decided; it would mean maintaining two execution paths
  indefinitely, which that note itself flagged as the "dauerhafte
  Nachteil". A single llama.cpp runtime is also the whole reason it's
  attractive here: one embeddable runtime across macOS and Linux.
- **Same two catalog models, converted to GGUF — no new model added.**
  This plan is a runtime swap, not a model re-selection. Llama3.2 and any
  other new candidate stays out of scope; the `_temp` note treats
  evaluating it as a separate next step, not part of "rework the runtime".
  Use an existing, reputable GGUF conversion of Granite-3.3-2B-Instruct and
  Qwen3-0.6B if one exists and its provenance/license checks out (matching
  `catalog_data.py`'s existing standard of pinning real, re-verifiable
  Hugging Face metadata); convert in-house from the pinned Transformers
  revisions only if no trustworthy GGUF release exists. Do not invent
  repository names, revisions, or checksums while planning this — Block 2
  verifies them against the real Hugging Face API the same way
  `catalog_data.py`'s own header comment already requires.
  One quantization level per model for now (the existing catalog has no
  concept of multiple variants of the same model); picking the specific
  level (e.g. something in the Q4_K_M/Q5_K_M range, matching what the
  `_temp` note observed as Ollama's own default for Llama3.2) is a Block 2
  implementation decision to make empirically, not fixed here.
- **Keep a lightweight, torch-free tokenizer purely for chat-template
  rendering, at least initially.** Rather than trusting llama.cpp's own
  GGUF-embedded chat-template engine (and whatever mechanism it offers for
  Qwen3's `enable_thinking` kwarg) from day one, render the exact same chat
  template text today's `tokenizer.apply_chat_template(...,
  enable_thinking=False)` produces — via `transformers.AutoTokenizer`
  *without* `torch`/`AutoModelForCausalLM` — and hand llama.cpp the already-
  rendered prompt string through its raw completion API, not its chat-
  completion wrapper. This directly preserves the one behavior
  `docs/model_benchmark_findings.md` proves is load-bearing (Qwen3's
  thinking-mode default) instead of re-deriving it against a different
  template engine. `transformers`'s tokenizer-only path has no torch
  dependency and downloads only small tokenizer files, so this keeps the
  RAM/dependency win intact. Revisit once llama.cpp's own template handling
  for these two models is verified to match, as a possible follow-up
  simplification — not required for this plan.
- **`ModelCatalogEntry.prompt_template` becomes a real compatibility gate,
  deliberately not a template selector.** Revised during Block 1
  implementation: the original framing here ("actually driving which chat
  template gets applied") turned out to describe the wrong mechanism.
  Overriding with a hand-authored Jinja template per label is a regression
  risk, not just extra work - Qwen3's actual shipped template contains
  model-specific conditional logic for `enable_thinking` (its hybrid
  think/non-think suppression, confirmed load-bearing per
  `docs/model_benchmark_findings.md`) that a generic reimplementation of
  "the chatml format" would not replicate. `TransformersExtractor` never
  overrode the template either - both backends always trust the installed
  tokenizer's own embedded default. `prompt_template` instead moves from
  decorative (currently unread) to an allowlist `load_installed()` checks
  before loading, plus a check that the installed tokenizer actually has a
  non-empty `chat_template` at all (Block 1).
- **Compatibility memory-tier minimums are not retuned in this plan.**
  `_MEMORY_TIER_MINIMUMS_GB` stays as-is until real GGUF RAM numbers exist;
  retuning it is flagged as a fast Block 2 or 6 follow-up once those numbers
  are measured, not blocking this plan's completion.
- **No end-to-end automated block.** Per the user, the working path will be
  verified live rather than via an automated cross-block test. The security/
  sanity block stays, per standing convention.

## Scope boundary: benchmarks deferred

Do not edit the benchmark CLI, evaluation harness, benchmark tests,
reports, or findings document, or run real-model benchmarks as part of this
migration. Existing unit tests may still run in the normal regression suite.
No new baseline,
benchmark report naming scheme, or timing comparison is required now.
Validate the app live in Block 6; benchmark migration and measurement come
later. Preserve the legacy catalog and extractor used by the unchanged CLI
so app changes do not indirectly switch its downloads to GGUF.

## Block 1: A working `LlamaCppExtractor`, proven standalone

- Add a llama.cpp Python binding as a new backend dependency (the concrete
  package/pin is an implementation decision, not fixed here; confirm actual
  current wheel/build support for Metal on macOS and CUDA on
  Linux before picking one — packaging availability shifts and must
  be checked live, not assumed from memory. See Block 5 for the packaging
  side of this same question).
- Add `backend/src/invoice_renamer/inference/llamacpp_extractor.py`,
  mirroring `transformers_extractor.py`'s shape: a `LlamaCppExtractor` class
  implementing `LanguageModel.generate(prompt: str) -> str`
  (`inference/language_model.py:11`), with `load_installed()` for ModelRuntime
  and a local-file loading path for standalone validation. No new
  direct-from-Hub benchmark loader is needed. Keep the installed-loader
  signature close to the current application call contract.
- Implement chat-template rendering per the "lightweight tokenizer" product
  decision above: reuse the exact
  `apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
  enable_thinking=False)` equivalent of `transformers_extractor.py:130-141`
  (tokenizer-only, no model/torch import), then pass the rendered text into
  llama.cpp's raw/text completion API — not its chat-completion wrapper —
  so template selection is explicit and already-proven rather than
  delegated to a second template engine.
- Load the tokenizer and chat-template assets from the same verified local
  install as the GGUF file (Block 2), with `local_files_only=True` and
  `trust_remote_code=False`. Do not fall back to an upstream repository or
  a pre-existing Hugging Face cache when required assets are missing.
- Set context size and generation limits explicitly; do not inherit binding
  defaults. Preserve the current maximum of 512 new tokens. Choose and
  record a concrete per-model context size before acceptance, based on
  tokenized extraction, repair, and shortening prompts and the memory
  budget. The selected size must fit the prompt plus the output allowance,
  including template/control tokens. For reference, the reviewed
  [llama-cpp-python API](https://llama-cpp-python.readthedocs.io/en/latest/api-reference/)
  defaults to `n_ctx=512` and `max_tokens=16`; neither is a suitable implicit
  policy for this app. Verify the chosen binding/version's own behavior.
- Count tokens using the tokenizer actually used by llama.cpp for inference.
  If a prompt plus its output allowance exceeds the configured context,
  report a clear context-limit failure through the existing job error path;
  do not silently truncate invoice text or reduce the output allowance.
  Define model-specific end-of-generation/stop-token handling and verify
  control-token recognition and BOS insertion on the rendered prompt.
  A completion stopped by the output limit must be surfaced as incomplete,
  rather than accepted as a successful extraction merely because it parses.
  Explicitly specify repetition-penalty behavior as well as greedy decoding,
  documenting any semantic difference from the current implementation.
- Preserve the behaviors `docs/model_benchmark_findings.md`'s "Lessons that
  cost real time" section documents as real, previously-hit bugs, not
  hypothetical ones: strip a markdown code fence from the response if
  present (`extractor.py`'s `_strip_markdown_fence` already does this
  downstream, so no duplicate handling is needed here — confirm it still
  applies to llama.cpp's raw text output unchanged); greedy decoding
  (temperature/sampling equivalent to `do_sample=False`); and an
  empty-completion diagnostic path matching
  `transformers_extractor.py:166-181` (decode again without stripping
  special tokens, return the diagnostic as the "response" so it survives
  into `extract_invoice()`'s warning, not just stderr).
- Confirm GGUF metadata's own chat template (if present) is *not* silently
  used instead of the rendered prompt — an accidental double-templating
  (rendering with `transformers` and then having llama.cpp apply its own
  template again on top) would corrupt every prompt.
- Prove this against one real, manually-downloaded GGUF file (any small
  public model, not necessarily the final catalog choice) and a real
  invoice fixture from `fixtures/`, end to end through the *existing*,
  unmodified `inference/extractor.py`/`inference/prompts.py` — confirming
  the `LanguageModel` Protocol boundary genuinely required no changes on
  that side.

Checks: unit tests for `LlamaCppExtractor.generate()` using a fake
completion backend (mirroring `test_transformers_extractor.py`'s
`_FakeModel`/`_FakeTokenizer` pattern) — assert the exact rendered prompt
text sent to the completion call, greedy/deterministic decoding parameters,
markdown-fence and empty-completion behavior. Cover prompts at and beyond
the context boundary, the explicit 512-token output allowance, output-limit
termination, and model-specific stop/control tokens. A `load_installed()` test
mirroring `test_load_installed_resolves_install_dir_and_forces_local_files_only`
using a fake/mocked binding, confirming it resolves the same
`install_dir_for()` path and never touches the network for an installed
model, including tokenizer/template loading. Missing tokenizer assets must
fail locally even when a remote repository would be reachable.

Acceptance: `LlamaCppExtractor.generate()` produces a real, valid
extraction JSON for a real invoice against a real (manually downloaded, not
yet catalog-wired) GGUF model, and Qwen3's thinking-mode suppression is
confirmed live against a Qwen3 GGUF specifically (not just asserted in a
mocked test), since that's the one documented failure mode most likely to
resurface silently.

**Deferred by user decision:** the live-GGUF proof above did not happen —
no real model file was downloaded. What's actually implemented and verified
(mocked tests only) is a `LlamaCppExtractor` that: tokenizes the rendered
chat-template prompt exactly once with `special=True` (so control-token text
like `<|im_start|>` is recognized as the real special token, not split into
subwords) and passes the resulting token ids — never the raw string —
straight to `Llama.create_completion()`; confirmed against the installed
`llama-cpp-python==0.3.35` source (`_create_completion`) that a `list[int]`
prompt makes llama.cpp skip its own internal BOS/EOS insertion entirely, so
this module's own `add_bos=not already_has_bos` check (does the rendered
template text already start with the tokenizer's BOS marker?) is the only
BOS decision in effect — not a guess, read directly from the library's
source in the isolated build mirror. A `finish_reason == "length"` response
(cut off by `max_new_tokens`, confirmed via the installed binding's
`CompletionChoice` type) is now wrapped as an explicit incomplete-completion
diagnostic rather than returned as if it were a successful extraction, with
the completion text kept only in that returned diagnostic — worker stderr
logs counts and the termination reason only, never response content that
can hold real invoice data (a second review round caught the first version
of this fix printing the full text to stderr). `entry.prompt_template`
gates loading against an explicit allowlist of chat formats this
default-template-rendering approach has actually been designed for
(`chatml`, `granite-instruct`) plus a check that the installed tokenizer
actually has a non-empty `chat_template` — deliberately still not a
template *selector* (see the revised product decision above: a
hand-authored override risks silently breaking Qwen3's `enable_thinking`
handling). `last_n_tokens_size` is set to the full `n_ctx` so the
repetition penalty considers the whole sequence, matching
`TransformersExtractor`'s behavior instead of the binding's 64-token
default (confirmed in its source).

Still unconfirmed without a real model: whether `tokenizer.eos_token`
actually matches each catalog model's real chat-template turn-end marker
(the text-level `stop` list is a backstop; llama.cpp's native per-token EOS
stop is what actually governs this), and the chosen `n_ctx=4096` default is
a placeholder, not a measured figure. Also newly known and *not* closed:
the empty-completion diagnostic can no longer show genuinely raw/unstripped
text the way `TransformersExtractor`'s did (llama.cpp's `detokenize()`
defaults to stripping special tokens, and `create_completion()`'s response
carries no raw token ids to redecode instead) — a real, currently-accepted
gap versus the old diagnostic's ability to reveal a degenerate,
repeated-control-token generation.
Close these out live in Block 6, or earlier if real weights become
available sooner.

## Block 2: GGUF catalog entries for Granite-3.3-2B-Instruct and Qwen3-0.6B

- Identify a real GGUF release for each of the two shipped models,
  verifying repository, license, and revision against the live Hugging
  Face API — the same standard `catalog_data.py`'s own header comment
  already sets ("copied from Hugging Face's model API... at the pinned
  revision, not invented; re-verify against the repository before bumping
  a revision"). Do not reuse a checksum or revision from anywhere in this
  plan document as if verified; none are.
- Each catalog entry must list a complete offline bundle: the selected GGUF
  file (or all required shards), tokenizer files, tokenizer configuration,
  chat-template files, and any model configuration required by AutoTokenizer.
  Pin and verify the tokenizer/template source revision against the base
  model used for the conversion; do not assume an unrelated repository's
  current template matches. Include sizes and checksums for every asset.
- Keep the existing single-repository installer contract. Prefer a verified
  GGUF repository that already includes matching tokenizer/template assets.
  If none exists, prepare an app-controlled Hugging Face bundle containing
  the verified GGUF and the matching assets, retaining licenses and source
  provenance, and pin that bundle's immutable revision. Publication/access
  must be resolved before the catalog entry is accepted. Do not introduce
  hidden secondary downloads at model-load time; any multi-source installer
  design would instead require an explicit plan revision.
- Add the GGUF entries in a focused app catalog module and switch only the
  application consumers in `api/models_routes.py` and `api/analyses_routes.py`
  to that catalog. Preserve `models/catalog_data.py` and `SHORTLISTED_CATALOG`
  for the unchanged benchmark CLI. The app still exposes only two models,
  using the same IDs; this is not a second runtime option in the UI.
  Existing install directories under
  `install_dir_for()`'s `data_dir / "models" / entry.id / entry.revision`
  scheme naturally key by the new GGUF revision and don't collide with a
  user's already-downloaded safetensors copy — it simply becomes orphaned
  disk usage the user can clean up, not a conflict.
- Set `prompt_template` to a value `LlamaCppExtractor`'s allowlist accepts
  (`"chatml"` / `"granite-instruct"`, per the revised product decision
  above) - this is the field's first real consumer (see Current evidence
  above), gating compatibility rather than selecting a template.
- Revisit `memory_tier` per model given real GGUF file sizes; the existing
  `_MEMORY_TIER_MINIMUMS_GB` thresholds stay unchanged per the product
  decision above, but each entry's own tier assignment may still change if
  the smaller download shifts which tier is realistic.

Checks: add app GGUF catalog coverage following the existing
`backend/tests/models/test_catalog_data.py` shape/consistency patterns
(file list non-empty, sizes match `total_size_bytes`, etc.), preserving the
legacy catalog tests. Assert that each bundle declares its required tokenizer/template
assets as well as its GGUF files. No live download in CI.

Acceptance: both catalog entries install cleanly through the existing,
unmodified `models/installer.py` path (download, checksum verification,
resume, marker write) against their real Hugging Face repos. From a clean
Hugging Face cache, install each bundle, disable network access, and confirm
tokenizer loading, exact template rendering, and real model generation.

## Block 3: Runtime and lifecycle integration

- Update `ModelRuntime`'s `LoadInstalledFn` Protocol
  (`runtime.py:66-76`) and its `TYPE_CHECKING`-only import
  (`runtime.py:16`) to reference `LlamaCppExtractor` instead of
  `TransformersExtractor`; update the lazy default-loader import inside
  `get_or_load()` (`runtime.py:120-124`) the same way.
- Replace `select_device()`'s torch-specific body (`runtime.py:41-63`)
  with an llama.cpp-equivalent capability confirmation: map
  `AccelerationBackend` to a GPU-offload intent (e.g. full offload vs.
  CPU-only), then confirm the installed binding actually supports GPU
  offload before committing to it (mirroring today's "guess from the host,
  confirm against the real installed build, downgrade to CPU if the guess
  doesn't hold" pattern) rather than trusting the host-only detection in
  `models/detection.py` on its own. Update the one production call site
  (`api/analyses_routes.py:309-316`) to match the new parameter shape.
  Leave the benchmark CLI and its `--device` semantics unchanged.
- Replace `_unload_current()`'s torch cleanup (`runtime.py:144-154`:
  `del`, `gc.collect()`, `torch.cuda.empty_cache()`/`torch.mps.empty_cache()`)
  with whatever `LlamaCppExtractor` actually needs freed — confirm whether
  `del` alone (relying on the binding's own destructor) is sufficient or
  whether an explicit free call is needed, rather than assuming parity with
  today's torch-specific calls.
- Update the app pipeline's `provider="transformers"` literals
  (`analysis/pipeline.py:105,162,329`) and app-specific assertions to the new
  provider name. Leave `evaluation/benchmark.py` and its tests unchanged;
  generic metrics tests may continue to use the legacy provider as valid data.
- Retain `transformers_extractor.py` and its tests for the existing benchmark
  workflow. Ensure the app no longer imports it and that the packaged worker
  does not include its torch dependencies. Delete neither in this plan.

Checks: update `backend/tests/inference/test_runtime.py`'s fakes (currently
typed against `TransformersExtractor`, e.g. `_FakeExtractor`,
`runtime.py`'s own test imports at `test_runtime.py:12`) to the new type
name only — the caching/unload-ordering behavior under test doesn't change.
Add a `select_device()`-equivalent test confirming it downgrades to
CPU-only when the installed binding reports no GPU offload support, the
same shape as today's torch-availability-based downgrade.

Acceptance: a real analysis job loads a GGUF model through the full
`AnalysisCoordinator` → `ModelRuntime` → `LlamaCppExtractor` path (not just
Block 1's standalone script), and the existing idle-timeout/explicit-unload
lifecycle from plan 10 continues to work unmodified against the new
extractor type.

## Block 4: Memory status for the new runtime

- Replace `_gpu_memory()`'s torch-specific branches
  (`memory_status.py:55-83`) with whatever the llama.cpp binding actually
  exposes for GPU memory — investigate what's queryable first; do not
  assume a MPS/CUDA-equivalent allocator API exists. It may turn out worker
  RSS (`_worker_rss()`, unaffected by this plan) becomes the *only*
  reliable figure, since GGUF weights loaded via llama.cpp may not expose a
  separate allocator context the way torch's CUDA/MPS caching allocators
  do. If so, that's a real, user-facing capability loss to surface
  honestly (a plainer memory readout), not to paper over by inventing a
  number.
- Update `GpuMemorySnapshot` (`memory_status.py:16-24`) to whatever shape
  is actually measurable — this may mean fewer fields, not a like-for-like
  swap of the existing `driver_allocated_bytes`/`allocated_bytes`/
  `reserved_bytes` trio.
- Update `MemoryStatus.tsx` to match: `GPU_BACKEND_LABELS`/`GPU_TOOLTIPS`
  (lines 8-20), `describeGpu()` (lines 31-43), and the Apple Silicon shared-
  memory disclaimer (lines 21-24, `APPLE_SILICON_SHARED_MEMORY_NOTE`) — the
  disclaimer's premise (GPU and system RAM share one pool on Apple Silicon)
  still holds for llama.cpp/Metal, but the specific reported figure it's
  attached to may not exist anymore.
- Update `app/src/lib/api/types.ts`'s `GpuMemorySnapshot` interface and the
  three test fixtures that construct one
  (`useModelCatalog.test.ts:22`, `MemoryStatus.test.tsx:40`,
  `ModelSelector.test.tsx:19` — these set `prompt_template`, not `gpu`
  directly, but confirm none of them assume the old GPU shape elsewhere in
  the same file).

Checks: extend `backend/tests/inference/test_memory_status.py` (which
already independently injects each provider — `system_memory_fn`,
`worker_rss_fn`, `gpu_memory_fn` — per its own module docstring) with the
new GPU provider shape, including a failure-returns-partial-snapshot case
matching the existing `test_gpu_provider_failure_returns_a_partial_snapshot_not_an_exception`.
Frontend rendering tests for whatever the new `GpuMemorySnapshot` shape is,
replacing (not just adding to) the mps/cuda/rocm-specific assertions tied
to the old shape.

Acceptance: the memory status line shows real, live numbers during an
actual model load on the target Mac — even if that number set is smaller
than today's — with no stale field left rendering a torch-era number that
llama.cpp doesn't actually produce.

## Block 5: Dependencies and packaging

- Add the llama.cpp binding chosen in Block 1. Retain `transformers` and
  tokenizer/template dependencies for the app. Keep torch available to the
  existing development/benchmark workflow and preserve its Linux CPU-wheel
  source configuration while that workflow needs it. If dependency groups
  are used to separate app packaging from development, keep torch in the
  default development setup so existing benchmark commands and tests still
  work without edits. Update the lockfile accordingly.
- Build the app worker in an isolated environment without torch and verify
  tokenizer rendering there. Prevent PyInstaller from collecting legacy
  inference code or torch through optional imports. Do not remove torch from
  the shared development environment merely to produce the lean app bundle.
- Confirm Metal support on macOS and CUDA support on Linux for the
  chosen binding: whether prebuilt wheels with GPU support exist for the
  target platforms/architectures, or whether a build-from-source step
  (compiler toolchain, `CMAKE_ARGS` or equivalent) is required at install
  time — this is genuinely unverified and platform-support in this
  ecosystem changes; check live rather than assuming. Update
  `README.md`'s Prerequisites section (`README.md:11-25`) if a new build
  toolchain requirement is introduced beyond what's already listed for
  Rust/Node/Tesseract.
- Verify `scripts/build_worker_sidecar.sh`'s PyInstaller build
  (`--onedir`, unchanged script logic) actually discovers and bundles the
  binding's compiled shared library/extension and any auxiliary files it
  needs at runtime (e.g. a Metal shader/library file, if the chosen binding
  ships one separately from the main `.dylib`/`.so`) — do this on both
  macOS and Linux, the latter only via
  `scripts/linux_workspace.sh . scripts/build_worker_sidecar.sh` per
  `AGENTS.md`'s Linux build isolation section, never directly in this
  shared checkout.
- Update `README.md:49` and `README.md:116`'s model description/license
  text to describe the GGUF app models. Do not add new timing claims or
  present historical Transformers timings as measurements of the new app.

Checks: `backend/tests/sidecar/test_worker_entrypoint.py`'s existing
coverage should keep passing unmodified (it doesn't touch the inference
stack). No new automated packaging test is expected — this block is
primarily a real build-and-run verification on both platforms, not unit-
testable in isolation.

Acceptance: `scripts/build_worker_sidecar.sh` produces a worker that starts
and loads a real GGUF model, staged and run through `cargo tauri dev` on
the target Mac, including fixture analysis and unload. A Linux build
produced via `scripts/linux_workspace.sh` must likewise start, load a real
model, analyze a fixture, and unload. Confirm CPU operation without CUDA
installed, plus actual GPU
offload for the Metal/CUDA builds on supported hardware. All packaged
workers must operate without torch installed; transformers remains present
solely for tokenizer/template rendering. Missing target hardware leaves
that platform's acceptance pending, not implicitly passed. The retained
legacy benchmark extractor and torch must not be included in the app bundle.

## Block 6: Live app validation

- Exercise the actual app on supported hardware: download each catalog
  model, load it offline, analyze a real invoice, inspect the proposed
  filename, enable name shortening, switch models, and unload explicitly
  and through the existing idle-timeout behavior.
- Inspect extracted fields and shortened names against the invoice. Confirm
  usable JSON, Qwen3 thinking suppression, and visible handling of context
  limits or incomplete output. Investigate regressions using actual model
  output before proposing a fix.
- Verify the app's memory status and responsiveness during repeated analyses.
  This is functional app validation, not a new benchmark or a claim of
  accuracy/speed parity. Formal comparisons and timings are deferred.

Checks: run the app-focused checks from Blocks 1–5. No new benchmark tests,
benchmark runs, report changes, or edits to the benchmark findings document.

Acceptance: both catalog models complete the live app workflow, including
name shortening, model switching, and unloading. Any observed app regression
is resolved or explicitly accepted before the app migration is complete.

## Block 7: Security, sanity, and safety

- Revision/provenance pinning: confirm the new GGUF catalog entries pin a
  real, immutable revision the same way `TransformersExtractor.load()`
  currently enforces a full 40-hex-char commit hash
  (`transformers_extractor.py:28,83-84`). Verify the app catalog pins the
  complete GGUF/tokenizer bundle and the installer checks every declared
  file. The app loader must consume only that installed bundle. The legacy
  benchmark loader remains unchanged and is outside this review.
- `trust_remote_code=False` (`transformers_extractor.py:40,43`) has no
  direct llama.cpp equivalent since GGUF loading doesn't execute
  repository-supplied Python — confirm and document that this specific
  risk class doesn't reappear in a different form (e.g. a malicious GGUF
  file exploiting a parser bug in the C++ loader) rather than assuming
  it's moot.
- Path traversal: `models/installer.py`'s `_resolve_file_path()`
  (`installer.py:42-47`) already guards against a `ModelFile.path` escaping
  the install directory — confirm this still applies unchanged to GGUF
  filenames (it should, since it's format-agnostic) rather than assuming.
- Resource cleanup: confirm `LlamaCppExtractor`'s unload path (Block 3)
  can't leak a native handle/mmap on a failed or repeated load, mirroring
  the existing OOM-during-load safety net (`runtime.py:125-131`'s `finally`
  clearing `_loading`/`_loading_entry_id` even on a raising load).
- Privacy: confirm no invoice content or raw prompt/response text enters
  packaging logs, PyInstaller build output, or a new binding's own
  diagnostic/verbose logging by default — `transformers_extractor.py:147-151`'s
  token-count-only stderr print (never full prompt text) is the existing
  standard to match.
- Dependency supply chain: confirm the chosen llama.cpp binding's package
  source/maintainer is legitimate and its build doesn't fetch and execute
  arbitrary code from an unpinned location during `pip`/`uv` install (a
  real concern for packages that compile native code at install time).

Checks: none beyond what's already specified per item above; this is a
review checklist against the actual implementation, run once Blocks 1-6 are
done.

Acceptance: every item above is either confirmed safe with a cited
reference to the actual code, or has a tracked follow-up if something
genuinely can't be closed out in this plan.

## Verification and implementation bookkeeping

On macOS, run these directly. On Linux, prefix each with
`scripts/linux_workspace.sh backend`/`scripts/linux_workspace.sh app`
instead of `cd`-ing there yourself, per `AGENTS.md`'s Linux build isolation
section — this matters more than usual in this plan, since it's replacing a
native-extension dependency (`torch`) with another one.

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

No automated end-to-end block exists in this plan — the user will validate
the working end-to-end path (model download → load → analyze → propose
filename → unload) live on real hardware instead. Do not add invoices to
the repository; use private local invoice samples for live app validation.
Do not run or modify benchmarks as part of this plan.


## Inference Time with old runtime incl. "Shorten Names"

`App → Python-Worker → Hugging Face Transformers → PyTorch → Metal/MPS on Mac`

- invoice02.pdf > granite-3.3-2b-instruct · 22.3 s > qwen3-0.6b · 6.8 s 
- invoice04.pdf > granite-3.3-2b-instruct · 14.8 s > qwen3-0.6b · 4.4 s 
- invoice05.pdf > granite-3.3-2b-instruct · 18.9 s > qwen3-0.6b · 6.0 s
