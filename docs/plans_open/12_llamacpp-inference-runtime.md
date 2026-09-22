# Swap the local inference runtime from Transformers/PyTorch to llama.cpp

Status: Block 1 code complete (`inference/llamacpp_extractor.py` +
`tests/inference/test_llamacpp_extractor.py`, all unit tests/ruff/mypy
green). Live verification against a real GGUF file was originally deferred
to Block 6 by user decision (test project, no real-weight download for
now) but happened sooner, during Block 2 — see the notes at the end of
Block 1 and Block 2. Block 2 code complete (design revised 2026-09-21 to
pin GGUF and tokenizer/template assets to separate, independently-verified
sources per file instead of one bundle repository or an app-published
mirror; `models/gguf_catalog.py` added with real, live-verified entries for
both shipped models, `models/catalog.py`/`models/installer.py` updated for
per-file source overrides, all unit tests/ruff/mypy green) — see the note
at the end of Block 2. Block 3 code complete (`ModelRuntime`,
`select_device()`, and the app pipeline's `provider` literal swapped to
llama.cpp; all unit tests/ruff/mypy green) — see the note at the end of
Block 3. `n_ctx` moved from a shared `4096` placeholder to a real,
measured per-model `context_size` (both GGUF entries now `16384`), done
ahead of Block 6 after the user hit the placeholder's limit live — see the
note added at the end of Block 1 (2026-09-22). Block 8 code complete
(Qwen3-0.6B swapped for Llama 3.2 3B Instruct in the app catalog, live-
verified end to end in this sandbox; not yet rebuilt into the worker
sidecar or run through the real app) — see Block 8, added 2026-09-22.

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
  **Revised 2026-09-22 (see Block 8):** the user independently evaluated
  Llama 3.2 3B Instruct's accuracy (outside this repo, in Jupyter) and
  decided to swap it in for Qwen3-0.6B. This is now a model re-selection
  for one of the two slots, done after the runtime swap itself (Blocks
  1-3) was already complete and live-verified - not a reversal of "ship a
  single llama.cpp runtime," just of "no new model added."
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
- **GGUF and tokenizer/template assets are pinned to separate sources, per
  file — revised before Block 2 implementation began.** The installer's
  original single-repository-per-entry contract assumed one pinned Hugging
  Face repository would hold everything a catalog entry needs. Checking the
  real Hugging Face API (not assumed) found no reputable GGUF release for
  either model that also ships the tokenizer/chat-template files
  `transformers.AutoTokenizer` needs: Qwen's own `Qwen3-0.6B-GGUF`,
  `unsloth/Qwen3-0.6B-GGUF`, `bartowski/Qwen_Qwen3-0.6B-GGUF`,
  `second-state/Qwen3-0.6B-GGUF`, `mradermacher/Qwen3-0.6B-GGUF`,
  `ggml-org/Qwen3-0.6B-GGUF`, and Granite's equivalents
  (`ibm-granite/granite-3.3-2b-instruct-GGUF`,
  `bartowski/ibm-granite_granite-3.3-2b-instruct-GGUF`,
  `unsloth/granite-3.3-2b-instruct-GGUF`) all ship only `.gguf` files (plus
  at most a bare `config.json`) — llama.cpp itself reads the tokenizer from
  GGUF metadata, so repackagers have no reason to also ship separate
  tokenizer files. The fallback originally written here — publishing a new
  app-controlled Hugging Face bundle repo per model — was rejected by user
  decision: it would make this project responsible for hosting duplicate
  copies of third-party model weights indefinitely and maintaining their
  provenance, a substantial ongoing cost to work around what is otherwise a
  narrow installer limitation. Instead, `ModelFile` (`models/catalog.py`)
  gains its own optional `repository`/`revision` override, defaulting to
  `None` (meaning "use the entry's own `repository`/`revision`"), and
  `models/installer.py` resolves each file's effective source independently
  before fetching it. A GGUF catalog entry pins its own `repository`/
  `revision` to a verified quantization release and pins each
  tokenizer/template `ModelFile`'s `repository`/`revision` to the matching
  base-model revision (the same commit already pinned for that model's
  Transformers entry, where applicable). Every file, regardless of source,
  is still sha256/size-verified before being accepted, exactly as today.
  Existing catalog entries — the legacy Transformers entries in
  `catalog_data.py`, and any future GGUF entry with no reason to split
  sources — are unaffected: leaving a `ModelFile`'s `repository`/`revision`
  unset is a single-source entry, identical to today's behavior. This is
  the multi-source installer design the plan originally deferred ("any
  multi-source installer design would instead require an explicit plan
  revision") and is now in scope for Block 2 — still with no hidden
  download, since every source is declared per-file in the catalog module
  itself, not inferred or fetched from an unpinned location at load time.
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

**Partially closed during Block 2 (2026-09-21):** real weights became
available sooner, per the note above. Against the real catalog GGUF files
(Block 2's `unsloth/Qwen3-0.6B-GGUF` and `ibm-granite/granite-3.3-2b-instruct-GGUF`,
both Q4_K_M), `LlamaCppExtractor.generate()` produced clean completions for
both models with no leaked `<think>` block and no `finish_reason == "length"`
diagnostic - i.e. each model's own EOS token did stop generation correctly
in practice, for the prompts tried. `inference/extractor.py:extract_invoice()`
against a real fixture also completed cleanly for Qwen3. Still open: this
was only exercised at `n_ctx=4096` (Qwen, against real extraction prompts)
and `n_ctx=2048` (Granite, against a short test prompt only, not a real
extraction prompt) - the chosen `n_ctx` is still not a measured-for-Granite's-
real-prompts figure, and the empty-completion diagnostic's raw-text gap
(previous paragraph) is unrelated to real weights and remains open
regardless.

**n_ctx moved from a shared placeholder to a real per-model catalog value
(2026-09-22):** the user hit `Prompt (3671 tokens) + max_new_tokens (512)
exceeds context window (4096)` on a real invoice during live testing -
`load_installed()`'s hardcoded `n_ctx=4096` default was never sized to
either model's real capability. `ModelCatalogEntry` gained an optional
`context_size` field (`models/catalog.py`); `load_installed()` now defaults
`n_ctx` to `entry.context_size` and raises clearly if a catalog entry has
none set, instead of silently reusing one shared number across models (the
explicit `n_ctx=` parameter still exists, for standalone validation only).
Both GGUF entries in `models/gguf_catalog.py` are now set to
`context_size=16384`. This was measured, not guessed: loaded both real
Q4_K_M GGUF files already available in this sandbox from Block 2's live
verification, at several `n_ctx` values, and read the KV-cache size
directly from llama.cpp's own load log rather than trusting a theoretical
estimate - confirmed linear scaling with `n_ctx` (Qwen: 448 MiB at 4096 ->
~1.75 GiB at 16384; Granite: 320 MiB at 4096 -> ~1.25 GiB at 16384),
comfortably inside both entries' 8 GB `memory_tier` floor alongside their
own weights. 16384 was chosen over a smaller bump because
`build_repair_prompt` (`prompts.py`) resends the full original prompt once
per repair round (not twice - confirmed by reading it, not assumed), so a
repair round on the user's own 3671-token prompt would already need
roughly 3671 + response + error text + a fresh 512-token allowance - close
enough to the old 4096 ceiling to fail too. Re-verified live end to end
after the change: loaded the real Qwen3 GGUF through the unmodified
`load_installed()` (no `n_ctx` override) and confirmed it resolves
`n_ctx=16384` from the catalog entry and produces a real completion.
`models/compatibility.py`'s tier minimums are unchanged, per the existing
product decision above - the added KV-cache RAM was checked against the
current 8 GB floor, not used to justify retuning it now. Not re-verified
against Granite's real extraction prompts specifically (still the same gap
noted in the paragraph above), and not yet validated against a real large
multi-page invoice on the user's own Mac - only against the one real
prompt length reported and against this sandbox's synthetic fixtures.

## Block 2: GGUF catalog entries for Granite-3.3-2B-Instruct and Qwen3-0.6B

- Identify a real GGUF release for each of the two shipped models,
  verifying repository, license, and revision against the live Hugging
  Face API — the same standard `catalog_data.py`'s own header comment
  already sets ("copied from Hugging Face's model API... at the pinned
  revision, not invented; re-verify against the repository before bumping
  a revision"). Do not reuse a checksum or revision from anywhere in this
  plan document as if verified; none are.
- Each catalog entry must list a complete offline bundle: the selected GGUF
  file (or all required shards, from the pinned quantization repository),
  plus tokenizer files, tokenizer configuration, chat-template files, and
  any model configuration required by AutoTokenizer (from the pinned
  base-model repository, via each such file's own `repository`/`revision`
  override — see the revised product decision above). Pin and verify the
  tokenizer/template source revision against the base model actually used
  for the GGUF conversion; do not assume an unrelated repository's current
  template matches. Include sizes and checksums for every asset, regardless
  of which repository it comes from.
- Extend `models/catalog.py`'s `ModelFile` with optional `repository`/
  `revision` fields (defaulting to `None`, meaning "use the entry's own
  `repository`/`revision`"), and update `models/installer.py` to resolve
  each file's effective repository/revision independently before calling
  `fetch()` — per the revised product decision above. `install()`,
  `is_installed()`, and `_resolve_file_path()` are otherwise unchanged:
  checksum verification, file-granularity resumability, and the atomic
  marker write already operate per file. Pin each catalog entry's own
  `repository`/`revision` to a verified GGUF quantization release, and pin
  each tokenizer/chat-template `ModelFile`'s `repository`/`revision` to the
  matching base-model revision. Do not introduce any download whose source
  isn't a `repository`/`revision` pinned directly in the catalog module
  itself — a per-file override is still a fully declared, verified source,
  not a hidden or inferred one.
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
legacy catalog tests. Assert that each bundle declares its required
tokenizer/template assets as well as its GGUF files, and that its
tokenizer/template files pin a `repository`/`revision` distinct from (and
verified against) the GGUF's own. Extend `backend/tests/models/test_installer.py`
for the new per-file source resolution: a file with no override uses the
entry's own `repository`/`revision` (today's existing single-source tests
must keep passing unmodified), a file with an override is fetched from its
own `repository`/`revision` instead, and checksum verification/resumability
behave identically regardless of source. No live download in CI.

Acceptance: both catalog entries install cleanly through the installer's
updated (still resumable, sha256-verified, atomically-marked) path against
their real, separately-pinned GGUF and tokenizer/template Hugging Face
repos. From a clean Hugging Face cache, install each bundle, disable
network access, and confirm tokenizer loading, exact template rendering,
and real model generation.

**Implemented and live-verified (2026-09-21):** `models/catalog.py` and
`models/installer.py` changed per the revised product decision above
(`ModelFile.repository`/`revision` override, `installer._effective_source()`).
The new app catalog lives in `models/gguf_catalog.py`
(`GRANITE_3_3_2B_INSTRUCT_GGUF`, `QWEN3_0_6B_GGUF`, exported together as
`SHORTLISTED_CATALOG` - same exported name as `catalog_data.py`'s, so
`api/models_routes.py`/`api/analyses_routes.py` needed only a one-line
import-path change and every existing test that monkeypatches
`SHORTLISTED_CATALOG` on those route modules kept working unmodified).
Chosen quantization: Q4_K_M for both models (the lower end of the
Q4_K_M/Q5_K_M range this block left open). Sources, checked against the
live Hugging Face API for every reputable GGUF release of both models (see
the revised product decision's list) and picked for provenance:
- **Granite 3.3 2B Instruct:** `ibm-granite/granite-3.3-2b-instruct-GGUF`
  (IBM's own first-party GGUF conversion of their own model, tagged
  `license:apache-2.0`, `base_model:ibm-granite/granite-3.3-2b-instruct`) -
  preferred over any third-party quantizer since it's published by the same
  org as the base model.
- **Qwen3 0.6B:** Qwen's own official GGUF repo ships only a single Q8_0
  quant, outside the target range, so `unsloth/Qwen3-0.6B-GGUF` is used
  instead (Unsloth AI, a Hugging Face-verified organization widely used for
  GGUF quantization).
- Both entries' tokenizer/template files are pinned to the exact same
  base-model repository/revision already verified in `catalog_data.py`
  (`ibm-granite/granite-3.3-2b-instruct` @ `707f574c...`, `Qwen/Qwen3-0.6B`
  @ `c1899de2...`) - confirmed by downloading each file at that pinned
  revision and re-hashing it locally, which reproduced `catalog_data.py`'s
  own already-pinned sha256/size values exactly. The minimal file set
  `transformers.AutoTokenizer.from_pretrained()` actually needs was
  determined empirically (not assumed): `tokenizer.json`,
  `tokenizer_config.json`, `merges.txt`, `vocab.json` for Qwen; the same
  four plus `special_tokens_map.json` and `added_tokens.json` for Granite -
  neither model's `config.json`/`generation_config.json` is required for
  tokenizer-only loading, so neither is in the GGUF catalog entries (unlike
  the legacy Transformers entries, which need them to load model weights).
- Every GGUF file's declared sha256 was independently verified by
  downloading the real file and re-hashing it locally (not merely trusting
  the Hugging Face API's own reported LFS sha256) - both matched exactly.
- Went beyond this block's own "no live download in CI" Checks and this
  Acceptance's minimum: ran the real, unmodified `models/installer.install()`
  against both live catalog entries end to end (not a mocked `fetch`),
  confirmed `is_installed()` afterward, then loaded each through the real
  (unmodified) `LlamaCppExtractor.load_installed()` and called `generate()`
  - both produced clean completions with no leaked `<think>` block,
  confirming Qwen3's `enable_thinking=False` suppression live (closing out
  one of Block 1's items deferred for lack of real weights). Also ran the
  full, unmodified `inference/extractor.py:extract_invoice()` against
  `fixtures/selectable_text_en.pdf` through the real Qwen3 GGUF end to end,
  producing valid, parseable extraction JSON. `local_files_only=True` and a
  local GGUF file path make the load path structurally incapable of a
  network call regardless (also covered by Block 1's own
  `test_load_installed_resolves_install_dir_and_forces_local_files_only`),
  so this wasn't re-proven by physically cutting network access.
- Not covered by this live run, still open for Block 5/6: GPU offload
  (this sandbox is Linux/CPU-only; Metal/CUDA offload is unverified),
  packaging/PyInstaller bundling, and the app's own UI/API flow (this was a
  direct Python-level call, not through `AnalysisCoordinator`/the FastAPI
  app - that full-stack path is Block 3's own already-passing unit tests
  plus Block 6's live app validation).

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

**Implemented (mocked tests only — the live-analysis-job half of Acceptance
above is deferred to Block 6, same as Block 1, since it needs a real GGUF
model):** `select_device()` no longer returns a torch device string; it
returns `"cpu"` or `"gpu"` (a GPU-offload intent, not a specific backend —
llama.cpp exposes one build-wide offload flag rather than separate
CUDA/MPS/ROCm availability, confirmed via `llama_cpp.llama_supports_gpu_offload()`),
confirmed against the installed binding via an injectable
`supports_gpu_offload_fn` parameter (mirroring `memory_status.py`'s existing
injectable-provider pattern) rather than a hardcoded torch check. This
required no change to `LlamaCppExtractor.load_installed()`'s own `device`
parameter or its `n_gpu_layers = 0 if device == "cpu" else -1` check from
Block 1 — any non-`"cpu"` string was already treated as full offload.
`_unload_current()` calls `LlamaCppExtractor.close()` (added in Block 1)
directly instead of `del` + `gc.collect()` + a torch cache-clear call —
confirmed via the installed `llama-cpp-python` source that `close()`
deterministically closes an `ExitStack` of the model/context/batch handles
(freeing native/mmap resources immediately), rather than relying on `__del__`
via Python GC, so no `gc.collect()` equivalent is needed. The app pipeline's
provider literal is now `"llama.cpp"` (`analysis/pipeline.py:105,162,329`);
`test_pipeline.py`'s app-specific assertion was updated to match, while
`test_models.py`'s generic metrics tests keep using `"transformers"` as
arbitrary valid data, per this block's own instructions. All call sites
that resolved the default loader via `TransformersExtractor.load_installed`
(`ModelRuntime.get_or_load()`, and every test that monkeypatched it —
`test_runtime.py`, `test_analysis_coordinator.py`, `test_analyses.py`,
`test_memory.py`) now reference `LlamaCppExtractor.load_installed` instead;
each test file's fake extractor gained a no-op `close()` method since
`_unload_current()` now calls it on every switch/unload.

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

**In progress (2026-09-21):** found and fixed live, while the user was
testing Block 2/3 on their Mac (`cargo tauri dev` against a packaged
worker) and hit `Shared library with base name 'llama' not found` at
model-load time. Reproduced in the isolated Linux mirror
(`scripts/linux_workspace.sh . scripts/build_worker_sidecar.sh`, safe -
doesn't touch the Mac's staged worker) and confirmed the root cause via a
real build's `warn-*.txt`: llama-cpp-python's `libllama`/`libggml*`
libraries are loaded via `ctypes` from a `lib/` subdirectory of its own
package, which PyInstaller's static import analysis has no way to
discover. Fixed by adding `--collect-binaries llama_cpp` to the PyInstaller
invocation (preserves the `llama_cpp/lib/` layout `load_shared_library()`
expects, confirmed against the installed binding's source). Verified past
just file presence: started the actual frozen onedir worker binary,
listed models, submitted a real analysis job against the already-installed
Qwen3 GGUF from Block 2's live verification, and got a completed extraction
back through the full FastAPI app - the same code path the real Tauri app
uses. This closes this bullet's Linux half; still needs macOS confirmation
by the user (Metal-enabled llama-cpp-python wheel, real `cargo tauri dev`).

Also found live, not yet fixed: `torch` is still fully bundled in the
worker (confirmed present, e.g. `torch/lib/libtorch_cpu.so`) even though
nothing in the app's actual runtime path needs it - proven by running the
real extractor end to end with `torch` hidden from Python's import system
entirely (`sys.meta_path`/`importlib.util.find_spec` patched to report it
absent); extraction still completed. The likely mechanism: a hook
force-collects `transformers.models.*` submodules for its Auto-class
dynamic resolution, and many of those files do a top-level `import torch`
that's never actually reached by this app's tokenizer-only path, but is
still statically visible to PyInstaller. `--exclude-module torch` would
almost certainly fix this and shrink the bundle substantially, but is not
yet applied: `inference/memory_status.py`'s `_gpu_memory()` (Block 4, not
done) still does a lazy `import torch` whenever `runtime_device != "cpu"` -
on the user's Mac, `select_device()` will very likely return `"gpu"`
(Metal-enabled builds are the default there), so hitting `/memory` while a
model is loaded would crash with `ModuleNotFoundError` if torch were
excluded now. Excluding torch from packaging should wait for Block 4's own
torch removal from `memory_status.py`, not be done ahead of it.

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
  real, immutable revision — now potentially two per entry, since GGUF and
  tokenizer/template assets may come from separate sources (see Block 2's
  revised product decision) — the same way `TransformersExtractor.load()`
  currently enforces a full 40-hex-char commit hash
  (`transformers_extractor.py:28,83-84`). Verify every declared file's
  effective `repository`/`revision` (the entry-level default, or a per-file
  override) is a real, pinned commit, not a floating ref, and that the
  installer checksum-verifies every declared file regardless of which
  source it came from. The app loader must consume only that installed
  bundle. The legacy benchmark loader remains unchanged and is outside this
  review.
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

## Block 8: Swap Qwen3-0.6B for Llama 3.2 3B Instruct

Added 2026-09-22, after Blocks 1-3 were already complete and live-verified.
Per the revised product decision above, this replaces one of the two GGUF
catalog entries; it does not touch the runtime/lifecycle work Blocks 1-3
already finished. The user independently evaluated Llama 3.2 3B Instruct's
extraction accuracy in Jupyter (outside this repo) and decided to proceed
before any formal entry exists in `docs/model_benchmark_findings.md`.

- Verified live against the real Hugging Face API (not assumed): Meta
  publishes no first-party GGUF for Llama 3.2 3B Instruct, and the real
  base-model repository (`meta-llama/Llama-3.2-3B-Instruct`) is a **gated**
  repository (`gated: "manual"`) requiring an accepted license and an HF
  auth token this app's installer doesn't support. `unsloth/Llama-3.2-3B-
  Instruct-GGUF` (213k+ downloads, the same verified HF org already trusted
  for Qwen3's GGUF) is ungated and was used for the GGUF weights.
  Unsloth also publishes an ungated full-precision mirror of the base model
  (`unsloth/Llama-3.2-3B-Instruct`) that ships real tokenizer/template
  files - used for the tokenizer `ModelFile` overrides instead of the
  gated Meta repository, avoiding the auth problem entirely rather than
  building new auth support into the installer.
- Both the GGUF file's sha256 and the tokenizer file set's sha256s were
  independently verified by downloading each real file and re-hashing it
  locally (not trusting the HF API's reported LFS hash alone), matching
  Block 2's own standard. Llama 3's tokenizer needs only `tokenizer.json`,
  `tokenizer_config.json`, and `special_tokens_map.json` - no
  `merges.txt`/`vocab.json` the way Qwen/Granite's GPT2-style tokenizers
  need - confirmed empirically by loading `AutoTokenizer` with exactly that
  file set and nothing else, not assumed from the architecture.
- Added `"llama3"` to `LlamaCppExtractor`'s `_SUPPORTED_PROMPT_TEMPLATES`
  allowlist. Unlike Qwen3, Llama 3.2's chat template has no hidden
  thinking-mode toggle to suppress - confirmed live: loaded the real GGUF
  through the unmodified `load_installed()`/`generate()` path (no code
  changes beyond the allowlist entry), confirmed the rendered prompt starts
  with the tokenizer's own `<|begin_of_text|>` BOS marker (so this
  backend's existing `already_has_bos` duplicate-BOS guard applies
  correctly here too, not just for Qwen/Granite), and got a clean, complete
  generation with no leaked control tokens and no `finish_reason ==
  "length"` diagnostic.
- `context_size=16384`, measured the same way as the other two entries:
  llama.cpp's own load log reported 1792 MiB of KV cache at `n_ctx=16384`
  for the real GGUF file - exactly matching Qwen3's per-token KV cost
  (both models: 28 layers, 8 kv heads, head_dim 128), comfortably inside
  the entry's 8 GB `memory_tier` floor alongside the ~1.9 GB Q4_K_M
  weights (larger than Qwen's ~400 MB, since this is a 3B model, not the
  0.6B model it replaces).
- License is the Llama 3.2 Community License, not Apache 2.0 like the
  other entry - recorded accurately in the catalog rather than reused from
  the entry it replaced. This app downloads the model directly to the
  user's own machine (not bundled or redistributed by this project) and
  doesn't fine-tune or rebrand it, which is consistent with a plain-use
  reading of that license, but a legal read of the license's own terms
  (attribution/naming restrictions, the >700M MAU clause) wasn't done here
  and is worth the user's own confirmation before wider distribution.
- Removed `QWEN3_0_6B_GGUF` from `models/gguf_catalog.py` and
  `SHORTLISTED_CATALOG`. Left `catalog_data.py`'s legacy Transformers
  `QWEN3_0_6B` entry untouched, per the existing product decision that the
  benchmark CLI's catalog is out of scope for this plan.
- Updated the one place the app hardcoded a Qwen-specific default:
  `BatchWorkspace.tsx`'s auto-select-on-launch heuristic matched on an
  installed model id containing `"qwen"`; now matches `"llama"`. No other
  production code referenced the old model id - the remaining
  `"qwen3-0.6b"` occurrences left unchanged are either the unrelated legacy
  Transformers catalog, historical comments in `extractor.py`/`prompts.py`
  documenting real past observations that don't depend on which model
  ships today, or frontend test fixtures using it as an arbitrary
  placeholder string unrelated to the real catalog.
- Updated `README.md`'s model list and license text; removed the old
  Qwen-vs-Granite timing comparison instead of re-asserting it for a
  different model with no measured numbers to back it.

Checks: `tests/models/test_gguf_catalog.py`'s existing structural checks
(unique ids, pinned commit revisions, real sha256s, exactly one GGUF file,
required tokenizer assets present, tokenizer source distinct from the
GGUF's own, 8 GB compatibility floor) all run generically over
`SHORTLISTED_CATALOG` and needed no changes to cover the new entry.
`test_llamacpp_extractor.py`'s prompt-template-allowlist parametrization
extended to cover `"llama3"`. Full backend suite (545 passed, 2 pre-existing
skips), `ruff format --check`, `ruff check`, and `mypy` all green. Frontend
`npm run lint`, `npm run test` (123 passed), and `npm run build` all green.

Acceptance: real end-to-end generation confirmed live in this sandbox
(model load through `LlamaCppExtractor.load_installed()`, template
rendering, `generate()`) against the actual downloaded GGUF and tokenizer
files - not yet re-confirmed through the full `AnalysisCoordinator` →
`ModelRuntime` path or the packaged Tauri app on the user's own Mac (Block
3's own wiring is already generic over catalog entries and unit-tested, so
this is expected to work, but hasn't been watched happen end-to-end through
the real app). The worker sidecar must be rebuilt
(`scripts/build_worker_sidecar.sh` / `cargo tauri dev`, per `AGENTS.md`'s
Linux build isolation section on Linux) before this is visible there - it
was not rebuilt as part of this block. No entry added to
`docs/model_benchmark_findings.md` yet - the user's Jupyter accuracy check
is not a substitute for the documented, repeatable 5-invoice comparison the
other two models have; a real benchmark entry is a tracked follow-up, not
required to close this block.

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

## Inference Time with new runtime incl. "Shorten Names"

- invoice02.pdf > granite-3.3-2b-instruct · 10.8 s
- invoice04.pdf > granite-3.3-2b-instruct · 6.0 s
- invoice05.pdf > granite-3.3-2b-instruct · 9.4 s
