# Analysis pipeline and job API

## Context

BB-10 (batch workspace, React) is next per `01_implementation-plan.md`, but the backend
has no way to actually analyze an invoice yet: `POST /analyses` and `GET /jobs/{id}`
from the plan's Local API table don't exist, and nothing wires the already-built pieces
(`documents/reader.py`, `inference/extractor.py`, `naming/builder.py`) into one callable
operation. A batch workspace UI with nothing real to call would just be a mock. This plan
builds that missing backend slice so BB-10 can be built against a real API.

**A more important gap surfaced while researching this**: `TransformersExtractor.load()`
(`inference/transformers_extractor.py`) calls `AutoTokenizer`/`AutoModelForCausalLM
.from_pretrained(repository, revision=revision, ...)` with a Hugging Face Hub repo id —
this resolves through HF's own network/cache machinery, completely independent of
`models/installer.py`'s managed `install_dir_for()` directory. Confirmed against the only
real caller today, `scripts/benchmark_models.py:85`
(`TransformersExtractor.load(entry.repository, entry.revision, device=...)`), which is a
dev-only evaluation script that's *supposed* to hit the network to test candidate models.
But if a production job pipeline reused `load()` as-is, "download a model via the picker,
then run it" would silently either re-fetch over the network (breaking "local mode
performs no network requests after model installation") or fail offline. This plan adds a
second, local-only loading path rather than patching the existing one, since the two have
genuinely different jobs (see Design decisions).

## Design decisions

**Revision history**: a review pass against this draft caught four concrete issues and
one call-site bug before any code was written, all folded into the design below rather
than left as follow-ups: (1) the worker loop's own local variable would keep the
previous model's weights referenced — and therefore unfreed — across a model switch, on
top of whatever `ModelRuntime` itself already released; (2) `load_installed()` needed to
force `stream=False`, since the existing `TransformersExtractor` default of
`stream=True` prints generated text (invoice field content) to stderr via
`TextStreamer` — fine for the interactive benchmark CLI, not for production inference;
(3) the "PDF bytes held in memory only" claim was too strong — Starlette's multipart
parser spools `UploadFile` content through an OS temp file once it exceeds ~1MiB, before
this app's own code ever sees it, so the claim is narrowed to what this app actually
controls; (4) the job queue had no bound on total pending bytes, so a fast batch import
could queue gigabytes of PDFs faster than the single worker drains them; and a
code-sketch bug where `ModelRuntime`'s injected loader was called with `device`
positionally against `load_installed()`'s own keyword-only `device` parameter.

- **`TransformersExtractor` gets a second constructor, `load_installed()`, instead of
  changing `load()`.** `load(repository, revision, ...)` stays exactly as-is — it's a
  legitimate direct-from-hub path used only by `scripts/benchmark_models.py` to evaluate
  candidate models that aren't necessarily in the shortlisted catalog yet, and touching it
  risks the benchmark harness's own tested behavior for no reason. `load_installed(entry,
  data_dir, *, device=..., stream: bool = False, ...)` resolves
  `install_dir_for(entry, data_dir)` and calls
  `from_pretrained(str(install_dir), local_files_only=True, trust_remote_code=False)` for
  both the tokenizer and the model. `local_files_only=True` is a hard guarantee, enforced
  by `transformers` itself, that the production path can never make a network call — not
  just an intent documented in a comment. Both constructors share a small private
  `_load_model_and_tokenizer(source, *, revision, local_files_only, device)` helper for
  the from_pretrained/`.to()`/`.eval()`/device-check lines they'd otherwise duplicate.
- **`load_installed()` defaults `stream=False`, unlike `load()`'s `stream=True`.**
  `TransformersExtractor.generate()` only prints anything when `self._stream` is true
  (the live-token `TextStreamer` and the `prompt_tokens=...` line), so forcing it off
  fully silences per-token output for real invoice runs. `load()` keeps `stream=True` as
  its default because that live progress output is a deliberate feature of the
  interactive benchmark CLI; production inference has no interactive terminal watching
  it and must not print invoice field content to the sidecar's stderr, which the desktop
  shell could capture or log. (The separate empty-completion diagnostic print in
  `generate()`, which logs the raw decoded output when a completion comes back blank, is
  unconditional and stays out of scope here — it only fires on that one failure path and
  is a deliberate debugging aid, not per-token streaming.)
- **Job = one PDF.** `POST /analyses` takes one file and one `model_id`; a batch import is
  the frontend issuing one call per file. This matches the plan's own API table
  (`POST /analyses` "Upload PDF bytes and create an analysis job") and keeps the job
  contract as simple as the model-install one.
- **Inference is serialized through a single background worker thread consuming a FIFO
  queue — not `BackgroundTasks.add_task` per request.** This is the key way this design
  differs from `ModelInstallCoordinator`: concurrent downloads are fine (independent
  network transfers), but concurrent `model.generate()` calls on one loaded model would
  either race on shared model state or blow the memory budget the model-selection
  benchmark gated on. One worker thread, one job at a time, in submission order, is also
  simply what "one loaded local model" implies. `AnalysisCoordinator` owns a
  `collections.deque` of pending job ids plus a `threading.Condition` (built on the same
  lock used for job-state mutation) instead of `queue.Queue`, because cancelling a
  *queued* job needs to remove an arbitrary id from the middle of the queue, which
  `queue.Queue` doesn't support.
- **Reused directly from `ModelInstallCoordinator`'s design**: one lock held across an
  entire decide-then-mutate operation, not separate lock acquisitions for "check" and
  "act." Concretely, `AnalysisCoordinator.cancel()` and the worker thread's own
  pop-job-and-mark-RUNNING step both run under the same lock, so cancelling a job that's
  right on the boundary between QUEUED and RUNNING can never be lost or double-handled —
  the exact race class the download manager's two review rounds caught. Unlike the
  installer's `remove()` (fast, safe to hold the lock across), inference is slow (seconds
  to tens of seconds), so the lock is held only for queue/state mutation, never across the
  actual `run_document_analysis()` call — otherwise every `GET /jobs/{id}` poll for any
  job would stall behind whichever job is currently running.
- **Cancellation only prevents a QUEUED job from starting; a RUNNING job runs to
  completion and its result is discarded.** `DELETE` on a QUEUED job removes it from the
  deque and marks it CANCELLED immediately — no partial work exists yet. `DELETE` on a
  RUNNING job sets `cancel_requested = True`; the worker thread, after
  `run_document_analysis()` returns, checks that flag before publishing COMPLETED and
  marks the job CANCELLED instead if it's set. True mid-generation interruption is
  possible with `transformers`' `StoppingCriteria`, but wiring it through would mean
  extending the `LanguageModel` Protocol (`inference/language_model.py`) and every test
  double that implements it (`extract_invoice`'s tests, the e2e tests, the benchmark
  harness) with a cancellation parameter neither `extract_invoice`'s retry loop nor any
  existing caller needs today. A single job's `generate()` call is bounded
  (`max_new_tokens=512`) and typically finishes in low single-digit seconds even on CPU —
  "let it finish, discard the result" is an accepted, explicitly-documented MVP tradeoff,
  not a silent gap, mirroring how the download manager accepted file-level (not
  byte-level) resume.
- **`ModelRuntime` (new, `inference/runtime.py`) caches at most one loaded
  `TransformersExtractor`, keyed by `(model_id, revision)`, and is touched only by the
  worker thread.** Because only one thread ever calls it, it needs no internal locking —
  documented explicitly as a load-bearing invariant, not left implicit. Switching to a
  different model calls a `_unload_current()` step (`del` the model/tokenizer references,
  `gc.collect()`, then `torch.cuda.empty_cache()`/`torch.mps.empty_cache()` when that
  backend is active) before loading the new one, so repeatedly swapping models in one
  session doesn't leak GPU/MPS memory.
- **The worker loop must also drop its own local reference to the previous job's model,
  not just rely on `ModelRuntime` dropping its internal one.** `_unload_current()` only
  removes `ModelRuntime`'s own `self._extractor` reference; if `_worker_loop`'s local
  `model` variable from the prior iteration is still bound to that same object when the
  next `get_or_load()` call runs, the old extractor has a live reference outside
  `ModelRuntime`'s control and neither `gc.collect()` nor CPython's refcounting can
  reclaim its memory — so the new model would load while the old one is still fully
  resident, doubling peak memory right at the moment memory pressure is highest. The fix
  is structural: each loop iteration explicitly clears its own `model` reference (a
  `finally: del model` around the per-job processing block, covering both the success
  and failure paths) before control returns to the top of the loop, so by the time the
  *next* iteration's `get_or_load()` call may trigger `_unload_current()`, `ModelRuntime`'s
  own reference really is the last one and dropping it actually frees the memory.
- **Device selection reuses `detect_capabilities()`'s `AccelerationBackend` rather than
  re-probing torch directly.** Mapping: `MPS -> "mps"`, `CUDA -> "cuda"`, `CPU -> "cpu"`,
  and **`ROCM -> "cuda"`** — ROCm-enabled PyTorch builds expose AMD GPUs through the same
  `torch.cuda` device namespace, so `"cuda"` is the correct device string even on ROCm,
  not a mismatch. This mapping lives in `inference/runtime.py`, next to the thing that
  needs it, rather than in `models/detection.py` (whose docstring already flags itself as
  the pre-`TransformersExtractor` heuristic version, out of scope to fix here).
- **A job is rejected at submission (422) if its model isn't installed** — reuses
  `is_installed()` from `models/installer.py`, mirroring the picker's own source of truth.
  Not re-checked once a job starts running: if a model is deleted via `DELETE
  /models/{id}` while a job for it is still queued, that's treated the same as any other
  mid-run failure (the load fails, the job goes to FAILED with a clear error) rather than
  adding a second validation pass — deleting an installed model while a job for it is
  in-flight is an edge case, not a path worth special-casing.
- **This app's own code holds uploaded PDF bytes in memory only, and drops them from the
  job's state once it reaches a terminal status** (COMPLETED/FAILED/CANCELLED) — keeps a
  multi-file batch import's memory footprint bounded to whatever's still queued or
  running, and keeps invoice content out of any artifact this app itself writes,
  consistent with `01_implementation-plan.md`'s "redact invoice text... from logs"
  principle extended to "don't persist it at all beyond the run." **This claim is
  narrower than "zero disk writes for the whole request," and deliberately so**: FastAPI's
  multipart parsing (`UploadFile`, via Starlette) spools upload content through an OS
  temp file once it exceeds Starlette's ~1MiB in-memory threshold, before this app's route
  handler ever runs — real invoice PDFs (especially scanned ones) routinely exceed that.
  That temp file is created and cleaned up entirely inside Starlette's own request
  handling, outside this app's control short of hand-rolling raw-body upload parsing
  instead of `UploadFile`/multipart. For a personal, loopback-only, session-token-gated
  desktop app — where an attacker able to read another process's OS temp files already
  has code execution on the same machine, at which point the sidecar's own memory is just
  as reachable — accepting that framework-level, auto-cleaned temp file is a reasonable
  tradeoff against the complexity of a custom upload mechanism. If that changes (e.g. a
  future networked/cloud-adjacent deployment shape), revisit the upload mechanism itself
  rather than trying to patch around `UploadFile`.
- **A cheap synchronous `%PDF-` magic-byte check at `POST /analyses` rejects obviously
  non-PDF uploads with 422 immediately**, rather than only discovering that inside the
  worker thread via `read_document`'s own `ValueError`. Full validation (page count,
  corruption, etc.) still only happens inside the worker via the real `read_document()` —
  duplicating its logic at the route isn't worth it — but a wrong-file-type mistake gets
  instant feedback instead of a round-trip through the queue.
- **`POST /analyses` rejects a submission that would push total pending bytes (QUEUED +
  RUNNING jobs) over `_MAX_PENDING_BYTES` (500MiB — roughly ten max-size 50MiB PDFs) with
  `429` and a clear message**, so a fast batch import can't queue gigabytes of PDF bytes
  faster than the single worker thread drains them. No separate running counter: `submit()`
  sums `len(job.pdf_bytes)` over jobs still holding bytes (QUEUED/RUNNING; terminal jobs
  already dropped theirs) under the same lock used for insertion — O(number of jobs ever
  submitted this session), which is negligible at the scale a single desktop session
  actually reaches (tens to low hundreds of invoices), so a dedicated running total isn't
  worth the extra state to keep in sync. The check and the insertion happen under one
  lock acquisition, the same "decide-then-mutate atomically" pattern used everywhere else
  in this design, so concurrent submissions can't both pass the check and jointly exceed
  the cap.
- **No generic `/jobs/{id}` result persistence or history** — jobs live in memory only,
  same accepted boundary the download manager already established for its own
  in-memory-only `VERIFICATION_FAILED` state: this backend restarts on every app launch.
- **`python-multipart` becomes a direct dependency** — FastAPI's `File`/`Form` handling
  requires it; it wasn't needed before since no route accepted uploads.

## Files to add/change

### 1. `backend/src/invoice_renamer/inference/transformers_extractor.py` (change)

```python
def _load_model_and_tokenizer(
    source: str, *, revision: str | None, local_files_only: bool, device: str
) -> tuple[PreTrainedModel, PreTrainedTokenizerBase]:
    # Shared from_pretrained/.to()/.eval()/actual-device-check body used by both
    # load() and load_installed() — revision=None is valid for load_installed
    # (a local directory has no separate revision argument; the directory path
    # itself is already revision-scoped by install_dir_for()).

class TransformersExtractor:
    ...
    @classmethod
    def load(cls, repository: str, revision: str, *, device="cpu", ...) -> "TransformersExtractor":
        # unchanged behavior: from_pretrained(repository, revision=revision, trust_remote_code=False)
        # — still the direct-from-hub path used only by scripts/benchmark_models.py.

    @classmethod
    def load_installed(
        cls,
        entry: ModelCatalogEntry,
        data_dir: Path,
        *,
        device: str = "cpu",
        stream: bool = False,   # forced off by default - see the stream=False design decision
        **kwargs,
    ) -> "TransformersExtractor":
        install_dir = install_dir_for(entry, data_dir)
        # from_pretrained(str(install_dir), local_files_only=True, trust_remote_code=False)
        # for both tokenizer and model — local_files_only=True makes "never hits the
        # network once installed" an enforced guarantee, not just documented intent.
        # The returned instance is constructed with stream=stream (default False).
```

### 2. `backend/src/invoice_renamer/inference/runtime.py` (new)

Module docstring: caches the one currently-loaded local model so repeated jobs against the
same model don't reload multi-GB weights per request.

```python
def select_device(capabilities: SystemCapabilities) -> str:
    # MPS->"mps", CUDA->"cuda", ROCM->"cuda" (ROCm PyTorch uses the cuda device
    # namespace), CPU->"cpu".

class LoadInstalledFn(Protocol):
    """Callable[[...], ...] can't express a keyword-only parameter - under mypy
    strict mode, a value typed as Callable[[ModelCatalogEntry, Path, str], R] may
    only be called positionally, so calling it as load_installed(entry, data_dir,
    device=device) (matching load_installed()'s own keyword-only `device`) would
    itself fail type-checking. A callable Protocol describes the real call shape
    instead."""

    def __call__(
        self, entry: ModelCatalogEntry, data_dir: Path, *, device: str
    ) -> "TransformersExtractor": ...

# Injectable for tests, same lazy-default-resolution pattern as installer.py's
# FetchFn (a bound default argument would capture TransformersExtractor
# .load_installed at def time, unmonkeypatchable in tests).

class ModelRuntime:
    """Not thread-safe by design: only the analysis worker thread (one thread,
    one job at a time) ever calls get_or_load(), so no internal lock is needed."""

    def __init__(self, *, load_installed: LoadInstalledFn | None = None) -> None:
        self._loaded: tuple[str, str] | None = None   # (model_id, revision)
        self._extractor: TransformersExtractor | None = None
        self._load_installed = load_installed  # lazily resolved, see LoadInstalledFn note

    def get_or_load(
        self, entry: ModelCatalogEntry, data_dir: Path, device: str
    ) -> "TransformersExtractor":
        key = (entry.id, entry.revision)
        if self._loaded == key:
            return self._extractor  # cache hit, no reload
        self._unload_current()
        load_installed = self._load_installed or TransformersExtractor.load_installed
        self._extractor = load_installed(entry, data_dir, device=device)  # keyword, not positional
        self._loaded = key
        return self._extractor

    def _unload_current(self) -> None:
        # del self._extractor; self._extractor = None; self._loaded = None; gc.collect()
        # then torch.cuda.empty_cache() / torch.mps.empty_cache() guarded by
        # torch.cuda.is_available() / torch.backends.mps.is_available().
```

### 3. `backend/src/invoice_renamer/analysis/pipeline.py` (new package + module)

Module docstring: runs one PDF through the full read -> extract -> filename pipeline and
times it, producing the same `FilenameProposal`/`RunMetrics` shape a job publishes.

```python
def run_document_analysis(
    pdf_bytes: bytes,
    model: LanguageModel,
    *,
    model_id: str,
    model_revision: str | None,
) -> tuple[FilenameProposal, RunMetrics]:
    # Modeled on evaluation/benchmark.py::run_invoice's read/extract/time structure,
    # but simpler and un-defensive: no ground-truth scoring, no crashed-result
    # synthesis. read_document() and extract_invoke() are called directly; if either
    # raises, this function lets the exception propagate — the coordinator (below)
    # is what turns that into a FAILED job with a message, since "was this an
    # analysis failure worth showing the user" is a job-layer concern, not a
    # pipeline-layer one.
    #
    # ocr_engine = _TimingOcrEngine-equivalent wrapping TesseractOcrEngine() to
    # split OCR time out of total read time, same technique as benchmark.py.
    # document = read_document(pdf_bytes, ocr_engine=timing_ocr_engine)
    # extraction = extract_invoice(document, model)
    # proposal = build_filename_proposal(extraction)
    # metrics = RunMetrics(execution_mode=LOCAL, model_id=model_id, provider="transformers",
    #                       model_revision=model_revision, pages_total=..., pages_ocr=...,
    #                       warnings=extraction.warnings, ...timings...)
    # return proposal, metrics
```

### 4. `backend/src/invoice_renamer/api/analyses_routes.py` (new)

Module docstring: API routes for submitting invoice analysis jobs and polling their
status/result.

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class AnalysisJob:
    id: str
    model_id: str
    original_filename: str
    status: JobStatus
    pdf_bytes: bytes | None       # dropped once terminal
    proposal: FilenameProposal | None = None
    metrics: RunMetrics | None = None
    error: str | None = None
    cancel_requested: bool = False

class AnalysisJobView(BaseModel):
    """Public shape for GET /jobs/{id} and the POST /analyses response."""
    id: str
    model_id: str
    original_filename: str
    status: JobStatus
    proposal: FilenameProposal | None = None
    metrics: RunMetrics | None = None
    error: str | None = None

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_MAX_PENDING_BYTES = 500 * 1024 * 1024  # ~10 max-size PDFs queued/running at once
_PDF_MAGIC = b"%PDF-"

class AnalysisCoordinator:
    """One background worker thread consuming a FIFO queue of job ids — see the
    'inference must be serialized' design decision. The lock+condition pairing
    mirrors ModelInstallCoordinator's 'one lock across decide-then-mutate' lesson,
    applied here to queue membership + job status instead of install state."""

    def __init__(
        self,
        data_dir: Path,
        *,
        load_installed: LoadInstalledFn | None = None,
        capabilities_fn: Callable[[], SystemCapabilities] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._jobs: dict[str, AnalysisJob] = {}
        self._queue: deque[str] = deque()
        self._data_dir = data_dir
        self._runtime = ModelRuntime(load_installed=load_installed)
        self._capabilities_fn = capabilities_fn or (lambda: detect_capabilities(disk_path=data_dir))
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def submit(self, entry: ModelCatalogEntry, pdf_bytes: bytes, original_filename: str) -> AnalysisJob:
        if not is_installed(entry, self._data_dir):
            raise HTTPException(422, f"model {entry.id!r} is not installed")
        job = AnalysisJob(id=str(uuid4()), model_id=entry.id, original_filename=original_filename,
                           status=JobStatus.QUEUED, pdf_bytes=pdf_bytes)
        with self._condition:
            pending = sum(
                len(j.pdf_bytes) for j in self._jobs.values()
                if j.status in (JobStatus.QUEUED, JobStatus.RUNNING) and j.pdf_bytes is not None
            )
            if pending + len(pdf_bytes) > _MAX_PENDING_BYTES:
                raise HTTPException(429, "pending analysis queue is full; retry once earlier jobs complete")
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._condition.notify()
        return job

    def get(self, job_id: str) -> AnalysisJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job id")
        return job

    def cancel(self, job_id: str) -> AnalysisJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "unknown job id")
            if job.status is JobStatus.QUEUED:
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass  # worker already popped it under this same lock; falls
                          # through to the RUNNING branch's semantics naturally
                          # on the *next* cancel() call if needed - see note below
                else:
                    job.status, job.pdf_bytes = JobStatus.CANCELLED, None
            elif job.status is JobStatus.RUNNING:
                job.cancel_requested = True
            return job

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._queue:
                    self._condition.wait()
                job_id = self._queue.popleft()
                job = self._jobs[job_id]
                job.status = JobStatus.RUNNING
            entry = _entry_or_404(job.model_id)
            model = None  # see the "worker loop must drop its own reference" design
                          # decision - this local binding is explicitly cleared below
                          # so it can never outlive the job it was loaded for
            try:
                device = select_device(self._capabilities_fn())
                model = self._runtime.get_or_load(entry, self._data_dir, device)
                proposal, metrics = run_document_analysis(
                    job.pdf_bytes, model, model_id=entry.id, model_revision=entry.revision
                )
            except Exception as exc:
                with self._lock:
                    job.status, job.error, job.pdf_bytes = JobStatus.FAILED, str(exc), None
                continue
            finally:
                # Drops this loop's own reference to the extractor (success or
                # failure) so that if the *next* job needs a different model,
                # ModelRuntime._unload_current()'s del of its own reference is
                # truly the last one, and the old weights are actually freed
                # before the new model loads - not just "unload requested."
                del model
            with self._lock:
                if job.cancel_requested:
                    job.status = JobStatus.CANCELLED
                else:
                    job.status, job.proposal, job.metrics = JobStatus.COMPLETED, proposal, metrics
                job.pdf_bytes = None
```

- `POST /analyses` (multipart `file: UploadFile`, `model_id: str = Form(...)`) — 404
  unknown `model_id`; reads bytes with a running size check, `413` past
  `_MAX_UPLOAD_BYTES`; `422` if the bytes don't start with `_PDF_MAGIC`; else
  `coordinator.submit(...)` (`422` if the model isn't installed, `429` if the pending-bytes
  cap would be exceeded — both raised by `submit()` itself), return `202` with the
  `AnalysisJobView`.
- `GET /jobs/{id}` -> `coordinator.get(id)` -> `AnalysisJobView`.
- `DELETE /jobs/{id}` -> `coordinator.cancel(id)` -> `AnalysisJobView`.
- `app.state.analysis_coordinator` created eagerly in `create_app()`, same rationale as
  `model_install_coordinator` (no lazy first-request race).

### 5. `backend/src/invoice_renamer/api/app.py` (change)

Add `app.state.analysis_coordinator = AnalysisCoordinator(resolve_data_dir())` and
`app.include_router(analyses_router)`.

### 6. `backend/pyproject.toml` (change)

Add `"python-multipart>=0.0.20"` to `[project].dependencies`.

### 7. Tests

**`backend/tests/inference/test_transformers_extractor.py`** (extend) — add, without
touching existing `load()` tests:
- `load_installed()` resolves `install_dir_for(entry, data_dir)` and calls
  `from_pretrained` with that path and `local_files_only=True` — assert via monkeypatched
  `AutoTokenizer.from_pretrained`/`AutoModelForCausalLM.from_pretrained` capturing call
  args, **not** a real model load.
- regression guard: `load()`'s calls still pass the hub `repository` id and no
  `local_files_only`, proving the two paths stay genuinely distinct after this change.
- **`load_installed()` constructs its `TransformersExtractor` with `stream=False` by
  default** — assert on the returned instance's `_stream` attribute (or, more
  behaviorally, call `.generate()` with a fake model/tokenizer per the existing
  `_FakeModel`/`_FakeTokenizer` fixtures and assert nothing is written to stdout/stderr
  and no `TextStreamer` is constructed) — the concrete regression test for the
  invoice-content-in-logs concern this design decision exists to close.

**`backend/tests/inference/test_runtime.py`** (new):
- `get_or_load` calls the injected loader once and returns the cached instance on a
  second call with the same `(id, revision)` — loader call-counter assertion.
- `get_or_load` with a *different* entry calls the loader again and returns the new
  instance.
- **`get_or_load` switching models actually releases the previous extractor for garbage
  collection *before* loading the next one, not just eventually.** Checking a weakref
  only *after* the second `get_or_load` call returns is a weak test: `self._extractor =
  load_installed(...)`'s own reassignment drops the old reference regardless of whether
  unload-before-load ordering was implemented correctly, so a "load B first, unload A
  after" bug would still pass a check made after the call returns. Instead: take a
  `weakref.ref` on the first loaded fake extractor, drop the test's own reference to it,
  then call `get_or_load` with a *second fake loader whose body itself asserts the
  weakref is already dead* before it returns B's instance — i.e. the assertion runs
  *during* the second load, not after. That's the only point that actually distinguishes
  "A was freed before B started loading" (fixed) from "A and B were both resident at
  once, A just happened to get freed once the call returned" (buggy) — the observable
  effect this whole design decision exists to prevent is peak memory, not eventual
  cleanup.
- the injected loader is called as `load_installed(entry, data_dir, device=device)` —
  i.e. `device` passed **by keyword** — asserted via the fake loader's captured call
  signature (regression test for the positional-vs-keyword-only mismatch).
- **the default (non-injected) loader path** — `ModelRuntime()` constructed with no
  `load_installed` override — resolves to `TransformersExtractor.load_installed` and
  calls it successfully with `device` as a keyword argument; verified with
  `AutoTokenizer.from_pretrained`/`AutoModelForCausalLM.from_pretrained` monkeypatched
  (same technique as the `test_transformers_extractor.py` tests above), so this exercises
  the real lazy-default-resolution path, not just the injectable one.
- `select_device`: all four `AccelerationBackend` values map correctly, including
  `ROCM -> "cuda"`.

**`backend/tests/analysis/test_pipeline.py`** (new) — using `fixtures/*.pdf` and a
`_FakeLanguageModel` (same shape as the one in `tests/e2e/test_document_to_filename.py`):
- happy path: `selectable_text_en.pdf` + a scripted valid JSON response produces the same
  `2026-09-12_Apple_MacBook-Air_2180-EUR.pdf` proposal as the existing e2e test, plus a
  `RunMetrics` with sane non-negative timings and `pages_total == 1`.
- a corrupt/non-PDF byte string raises rather than returning a synthesized result — proves
  `run_document_analysis` deliberately does *not* swallow errors the way
  `evaluation/benchmark.py::run_invoice` does.

**`backend/tests/api/test_analyses.py`** (new) — mirrors `test_models.py`'s fixture
pattern (`INVOICE_RENAMER_SESSION_TOKEN` + `INVOICE_RENAMER_DATA_DIR=tmp_path`), with
`analyses_routes.SHORTLISTED_CATALOG` monkeypatched to a small local catalog, a fake
`is_installed` (or a real one against a pre-populated `tmp_path` install dir, reusing
`install_dir_for`), and an injectable fake `load_installed` returning a
`_FakeLanguageModel`-backed stand-in so no real model ever loads:
- **happy path end-to-end** (satisfies the required cross-block test): `POST /analyses`
  with `fixtures/selectable_text_en.pdf` + an installed fake model -> `202 QUEUED` -> poll
  `GET /jobs/{id}` (bounded retry loop; the fake loader/model responds instantly) until
  `COMPLETED` -> asserts the real proposal filename and metrics. Crosses upload route ->
  `AnalysisCoordinator` -> worker thread -> `ModelRuntime` (faked loader) ->
  `run_document_analysis` -> real `read_document`/`extract_invoke`/`build_filename_proposal`.
- `422` when `model_id` is known but not installed; `404` for an unknown `model_id`.
- `422` for a non-PDF upload (wrong magic bytes) — job never created, fake loader never
  called.
- a genuinely corrupt-but-PDF-shaped upload (real `%PDF-` header, garbage after) ends the
  job `FAILED` with a non-empty `error`, not `COMPLETED` with a null-everything proposal.
- **queueing order**: submit two jobs against a fake loader blocked on a
  `threading.Event`; assert the second stays `QUEUED` while the first is `RUNNING`, then
  release and confirm both settle `COMPLETED` in submission order.
- **cancellation**: `DELETE` on a still-`QUEUED` job -> `CANCELLED` immediately, fake
  loader never invoked for it. `DELETE` on a `RUNNING` job (blocked via the same `Event`
  pattern) leaves it `RUNNING` until released, then it settles `CANCELLED` (not
  `COMPLETED`) — the discard-on-completion behavior this design chose over true
  mid-generation interruption.
- **switching models across two real (coordinator-driven) jobs frees the first model's
  memory *before* the second model loads, not merely by the time it completes** — this
  is the test that catches the originally-reported bug, since `test_runtime.py`'s own
  test only proves `ModelRuntime` in isolation behaves correctly, not that the worker
  loop built around it does. Submit a job for model A with a fake loader that returns an
  instrumented extractor; once it `COMPLETED`s, take a `weakref.ref` to that extractor.
  Submit a job for a different model B whose fake loader **asserts the weakref to A is
  already dead from inside the loader call itself**, before returning B's instance — not
  a check made after job B completes. Checking only after completion would pass even on
  the original buggy loop, because the worker's `model = ...` reassignment eventually
  drops the stale reference to A regardless of whether `del model` ran first; only
  asserting *during* B's load proves A was actually released before B started, which is
  the actual peak-memory property this design cares about.
- **pending-bytes cap**: with `_MAX_PENDING_BYTES` monkeypatched down to something small,
  submit enough queued jobs (via the blocked-fake-loader pattern) to fill it, then assert
  the next `POST /analyses` gets `429` with a clear message; release the block, let jobs
  drain, and confirm a subsequent submission succeeds again. A concurrency variant
  (`ThreadPoolExecutor` firing many simultaneous submissions right at the cap boundary,
  mirroring `test_models.py`'s stampede test) confirms the check+insert is race-free
  under the shared lock rather than merely correct in the single-threaded case.
- security/sanity block: all three routes `401` without a token (parametrized, mirroring
  `test_health.py`); an upload past a (test-lowered) `_MAX_UPLOAD_BYTES` gets `413`; a
  completed/failed/cancelled job's `pdf_bytes` is gone (assert via a coordinator-internal
  accessor, not the public API, since the view model never exposes raw bytes anyway) —
  this asserts only what this app's own code controls, not the framework-level multipart
  temp file discussed in the design decisions, which is Starlette's responsibility and
  not something this test suite can or should assert on.

### 8. `CHANGES.md`

New minor-version entry once implemented (not now — this is still a plan document):
invoice analysis can now run end to end from an uploaded PDF to a `FilenameProposal` +
`RunMetrics` via `POST /analyses` / `GET /jobs/{id}` / `DELETE /jobs/{id}`, including the
fix making local inference actually use the picker-installed model files instead of a
separate network/cache path.

## Verification

1. `cd backend && uv run pytest` — all new + existing tests green; no real model load or
   network access in any test (`load_installed`/`load` both stay monkeypatched at the
   `from_pretrained` or coordinator-injection level).
2. `uv run ruff format --check src tests`, `uv run ruff check src tests`, `uv run mypy src`.
3. Manual smoke test once BB-10's UI can drive it (or via `curl`/httpie against a real
   installed model on macOS): install a model via the existing `/models` routes, `POST
   /analyses` a real fixture PDF, poll `/jobs/{id}` to `COMPLETED`, confirm the proposal
   and timings look right, and confirm (e.g. via a network monitor or offline test) that
   no network call happens during the analysis itself.
