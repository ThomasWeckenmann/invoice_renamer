# Model download / checksum / resume manager

## Context

The model catalog (`models/catalog_data.py`) was just trimmed to the two benchmark-validated models (Granite-3.3-2B-Instruct, Qwen3-0.6B), and the supporting data model (`ModelCatalogEntry`/`ModelFile` with pinned revisions + real sha256/size per file), compatibility checker, and picker-assembly logic (`models/picker.py`) already exist. But none of it is reachable: there is no API route beyond `/health`, no code anywhere that downloads a model file, verifies it, or removes it, and no concept of "where do model weights live on disk." Per `01_implementation-plan.md`, this is explicitly the next piece of Milestone 3 — wiring the picker and a download/checksum/resume manager to the finalized 2-model catalog.

The frontend (`app/src/`) is a placeholder with zero API client or state infrastructure, and the plan's own building-block table treats the model-picker *UI* as separate later work (BB-10, Milestone 4). So this plan is backend-only: a Python module that installs/removes models on disk, and the FastAPI routes to drive it. Verification is automated tests plus a manual real-download smoke test the user runs themselves (a sandboxed environment shouldn't pull multi-GB weights over the network).

Two Explore passes and one Plan-agent design pass (cross-checked against the actual installed `huggingface_hub==1.31.0` source) already validated the approach below against this codebase's real files and conventions.

## Design decisions

**Revision history**: two rounds of code review caught real gaps by reading the actually-installed `huggingface_hub==1.31.0` source and the lifecycle logic line-by-line, rather than trusting the library's general reputation for resumability or a happy-path test alone. Round 1 caught the resumability claim, the size-only "installed" check, cancellation-lost-on-the-last-file, missing thread-safety, restart/orphan cleanup, and the `fetch` late-binding bug. Round 2 — after those fixes were designed — caught that the fixes still didn't compose safely together: separate lock-guarded steps within one route handler (e.g. DELETE's read-then-act) can still interleave with a *different* route handler's own separate lock-guarded steps, a stale marker could survive a partial repair, and a cleanup failure during cancellation had no recovery path. The design below folds both rounds in; see "Rejected/superseded approaches" at the end of this section.

- **Reuse `huggingface_hub.hf_hub_download` for the actual HTTP transfer**, called once per exact catalog-listed filename (never `snapshot_download` with glob patterns) — fetching only the exact filenames in `ModelFile.path` is the literal mechanism for "never download or execute model repository code" (we never touch `.py` files or anything not already enumerated in the catalog).
- **Resumability is file-level, not byte-level, and this is a deliberate, documented limitation, not an oversight.** Verified against the installed source (`file_download.py:1968`, `_download_to_tmp_and_move`): each `hf_hub_download` call writes to a process-unique UUID-suffixed temp file opened in `"wb"` and unlinks it on failure — there is no shared `.incomplete` file reused across separate calls, so a dropped connection or a cancelled/crashed process cannot resume a file's transfer from where it left off; the file starts over. (`http_get`'s own `resume_size`/Range support only helps within one `hf_hub_download` call's internal retry loop for a transient connection drop mid-attempt — not across separate `install()` invocations.) What we *do* guarantee: `install()` never re-downloads a file that is already present on disk **and re-verified by hash** — so resuming a multi-file model after an interruption skips every file that completed before the interruption and only re-transfers the file that was in flight (and any after it). For a model whose single largest file is itself multi-GB (Granite's `model-00001-of-00002.safetensors` is ~5GB), an interruption mid-transfer of that one file means re-downloading that whole file, not just the missing bytes — accepted as reasonable for a local desktop app's one-time model install, not worth hand-rolling HTTP range-request/retry logic ourselves to avoid.
- **We still compute our own `sha256` after each file lands** and compare to the catalog's pinned hash — this is our own integrity guarantee, not something to delegate to HF's ETag. On retry, an existing file's hash is re-verified (not just its size) before deciding to skip re-fetching it — re-hashing a few GB locally is far cheaper than blindly trusting a leftover file, or than re-downloading it over the network.
- **Installation is only "true" once an atomic, revision-scoped marker file is written — after every catalog file has verified, and only if cancellation wasn't requested in the meantime.** Using file *sizes* alone to decide "installed" creates a window, right as the last file lands, where a poll can report installed before that file's hash has actually been checked, and a crash in that window would otherwise leave a not-actually-verified model permanently misclassified as installed. The marker (`<install_dir>/.installed.json`) is written via write-to-temp-then-`os.replace`, atomic on the same filesystem. **The marker records each file's expected `sha256`/`size_bytes`, not just its path** — if the catalog's own recorded hash/size for a file is ever corrected in a later app version (without the HF revision itself changing), the old marker no longer matches the current catalog entry and `is_installed()` correctly stops trusting it, forcing re-verification. `is_installed()` itself does a cheap existence+size check per file (not a re-hash) *in addition to* the marker match — hashing only happens inside `install()`, never on the `GET /models` hot path.
- **The marker is invalidated (deleted) the moment `install()` is about to touch a file that isn't already correctly verified — not unconditionally at the top of `install()`.** This closes a repair-specific gap: if a previously-installed file goes missing or corrupts and a repair `install()` call begins, the *old* marker for the *other, still-good* files would otherwise keep claiming "installed" for the whole model while the broken file is mid-repair; if the process crashes right after new (unverified) bytes land for that file but before its hash check runs, the stale marker would make those unverified bytes look installed on restart. Deleting the marker lazily — only right before the first real file mutation in a given `install()` call — closes this while also not needlessly invalidating a marker on a call that turns out to need no changes at all (every file already verified).
- **Cancellation is checked before and after every file's fetch-or-skip step — including the last one — and once more, atomically, immediately before the marker is written.** That last check is the one that actually matters: it happens inside a `may_finalize()` callback supplied by the API layer's `ModelInstallCoordinator` (see below) and executed **under the same lock** the coordinator uses when a `DELETE` sets the cancellation flag — so "cancel requested" and "about to finalize" can never both believe they won. If `may_finalize()` returns `False`, `install()` raises `InstallCancelled` instead of writing the marker, even though every file already verified correctly — deliberately honoring the cancel request over "the bytes happened to end up fine anyway." Cancellation does not delete already-verified files, so a future `install()` call resumes cheaply. (One narrow, accepted residual race remains: if `install()`'s disk write of the marker itself — which happens just *after* releasing the lock `may_finalize()` held — overlaps with a `DELETE` arriving in that exact instant, the install can still complete successfully even though a cancellation was "in flight." This can't corrupt any state — it just means an extremely-last-moment cancel is not guaranteed to win — and closing it fully would require holding a lock across a disk write, which isn't worth it for a sub-millisecond window in a single-user local app.)
- **A single `ModelInstallCoordinator` (not a bare dict) owns the in-memory state, the `data_dir`, and one `threading.Lock`, and is created once in `create_app()`** (not lazily on first request) — lazy `getattr`/`setattr` initialization on `app.state` has its own race: two simultaneous first requests could each construct a fresh store and one would silently discard the other's state. Every state-affecting operation (`start`, `remove_or_cancel`, and the background thread's own success/failure/cancellation transitions) is one method that holds the lock for its **entire** decision-plus-mutation, including the filesystem `remove()` call where relevant — not a separate read-then-act sequence across two lock acquisitions. This is what actually closes the cross-route race a first design missed: without it, a `DELETE` reading a `VERIFICATION_FAILED` state, then (unlocked) calling `remove()`, could race a concurrent `POST` that restarts the same model in between — the retry's fresh download would have its files deleted out from under it by the `DELETE` call that started first but finished last. Holding one lock across the whole operation makes `start` and `remove_or_cancel` for the same model id fully mutually exclusive. Read-only status checks (`GET /models`) only hold the lock for the quick in-memory dict lookup, releasing it before any filesystem read, so a `DELETE`'s (rare, brief) `rmtree` under the lock doesn't stall the picker for longer than that.
- **A `VERIFICATION_FAILED` model is retried by calling `POST` again directly — not by requiring a `DELETE` first.** `install()` already resumes from whatever's on disk, so a failed download's natural retry path is another `start()` call, which is allowed whenever the existing state isn't actively `DOWNLOADING` (a `VERIFICATION_FAILED` entry doesn't block it; it gets overwritten with a fresh `DOWNLOADING` state).
- **If cleanup after a cancellation itself fails** (e.g. `remove()` raises an `OSError` — permissions, a locked file), **the model transitions to `VERIFICATION_FAILED` with that error recorded, rather than staying stuck at `DOWNLOADING` forever.** Left uncaught, that failure would propagate out of the background thread with nothing left to ever clear the `DOWNLOADING` state — every future `POST`/`DELETE` for that model would 409/hang indefinitely until the whole process restarts. Landing in `VERIFICATION_FAILED` keeps it retryable via the point above.
- **`install()`'s injectable `fetch` parameter defaults to `None` and resolves `_default_fetch` lazily inside the function body**, not as a bound default-argument value (`fetch: FetchFn = _default_fetch`) — the latter captures the function object at `install()`'s *definition* time, so monkeypatching the module-level `_default_fetch` name in a test would silently have no effect on already-bound calls, defeating the entire point of making it injectable for tests (and risking a real network call from a test that believed it had swapped in a fake).
- **Reuse the picker's already-defined, currently-unused `InstallStatus.DOWNLOADING`/`VERIFICATION_FAILED`** instead of inventing new states. `GET /models` checks the coordinator's in-memory state *first*; only when no active state exists for an id does it fall back to the (now marker-gated, race-free) `is_installed()` filesystem check.
- **`GET /models`'s response needs fields `ModelPickerEntry` doesn't have** (`files_done`, `files_total`, `error`) to actually expose download progress/failure detail. Rather than modify `picker.py`'s existing, tested contract, `models_routes.py` defines its own `ModelStatusEntry(ModelPickerEntry)` — a subclass adding those three fields as `None`-defaulted optionals — and uses that as the `response_model` for `GET /models`, `POST /models/{id}/download`, and `DELETE /models/{id}` alike, so all three endpoints return the same shape.
- **`DELETE` can clean up an orphaned partial install directory that has no in-memory state** (e.g. the app was killed mid-download and restarted) — the final fallback is no longer a 404 when nothing is in memory; it's "does an install directory exist on disk at all for this id," and if so, remove it. `VERIFICATION_FAILED` is explicitly documented as in-memory-only, meaning it does not survive a process restart — after a restart, a failed-but-uncleaned install just looks like an ordinary orphaned partial directory (still cleanable via the same fallback), not a distinctly-flagged failure. This is an accepted boundary for a local desktop app whose backend restarts on every launch anyway, not a persisted state machine.
- **No generic job/task system.** `GET /models` is the only polling surface; no separate `/jobs/{id}`.
- **No compatibility gating on the download route.** `picker.py`'s existing, tested philosophy is "flag incompatible entries, don't exclude them" — the download route must not quietly contradict that by refusing to download something the picker still shows as selectable.
- **Progress granularity is per-file, not per-byte** (`on_file_verified(done, total)`) — `hf_hub_download` doesn't expose a byte callback, and there's no frontend consumer yet to justify the extra plumbing.
- **`resolve_data_dir()` also creates the directory** (`mkdir(parents=True, exist_ok=True)`) before returning it — `psutil.disk_usage` (used by `detect_capabilities`) requires the path to already exist, and the installer needs it to exist too.
- Explicitly out of scope: any frontend/UI work, a generic `/jobs/{id}` system for the unrelated future `/analyses` endpoint, real Tauri-side wiring of `INVOICE_RENAMER_DATA_DIR` (deferred to the future signed-build milestone), changing `picker.py`'s flag-vs-exclude behavior, search/filter capability, and hand-rolled HTTP range-request logic to achieve true byte-level resume (see the resumability note above).

### Rejected/superseded approaches

- Round 1: claimed `hf_hub_download` gives byte-level resumability "for free" via a reused `.incomplete` file; treated file existence+size as sufficient for "installed"; checked cancellation only before each file; used a bare `DownloadStateStore` with per-method locking but no cross-method exclusivity; a bound `fetch: FetchFn = _default_fetch` default. All corrected above.
- Round 2: kept per-method locking (`try_start`/`get`/`clear`/`mark_failed` each independently atomic) but left route handlers to sequence multiple such calls non-atomically (e.g. DELETE: `get()` then, unlocked, `remove()` then `clear()`) — which reintroduces a race between route handlers, just one level up from the dict itself. Also invalidated the marker unconditionally at the top of `install()` (would destroy a good marker on a no-op call) and had no recovery path if cleanup-after-cancellation itself failed. Corrected above by making the coordinator's methods the unit of atomicity (holding the lock across the filesystem action, not just the dict mutation), lazy marker invalidation, and a cleanup-failure path into `VERIFICATION_FAILED`.

## Files to add/change

### 1. `backend/src/invoice_renamer/models/installer.py` (new)

Module docstring: purpose is installing/removing catalog models on disk with checksum verification, resuming at file granularity by skipping already-verified files.

```python
DATA_DIR_ENV_VAR = "INVOICE_RENAMER_DATA_DIR"

def resolve_data_dir() -> Path:
    # os.environ.get(DATA_DIR_ENV_VAR) directly (no injectable env param —
    # matches auth.py's get_expected_session_token() convention); OS-branch
    # default in the same style as detection.py's _detect_acceleration()
    # (Darwin -> ~/Library/Application Support/invoice-renamer, Windows ->
    # %APPDATA%/invoice-renamer, else ~/.local/share/invoice-renamer).
    # Creates the directory (mkdir(parents=True, exist_ok=True)) before returning.

def install_dir_for(entry: ModelCatalogEntry, data_dir: Path) -> Path:
    return data_dir / "models" / entry.id / entry.revision   # revision-scoped

def _resolve_file_path(install_dir: Path, file: ModelFile) -> Path:
    # joins install_dir / file.path; asserts the resolved path is still
    # under install_dir (Path.is_relative_to after resolve()); raises
    # ValueError otherwise (path-traversal defense-in-depth).

_MARKER_NAME = ".installed.json"

def _marker_payload(entry: ModelCatalogEntry) -> dict[str, object]:
    # {"model_id": entry.id, "revision": entry.revision,
    #  "files": [{"path": f.path, "sha256": f.sha256, "size_bytes": f.size_bytes}
    #            for f in entry.files]}
    # Including each file's hash/size (not just its path) means a later fix to
    # the catalog's own recorded sha256/size for a file correctly invalidates
    # trust in an old marker, even if the HF revision string didn't change.

def _read_marker(install_dir: Path) -> dict[str, object] | None:
    # returns parsed JSON if the marker exists and is valid, else None.

def _write_marker_atomically(install_dir: Path, entry: ModelCatalogEntry) -> None:
    # json.dumps(_marker_payload(entry)) to a temp file in install_dir, then
    # os.replace(tmp, install_dir / _MARKER_NAME) — atomic on the same
    # filesystem, so there is no window where a half-written marker could be
    # read as valid.

def _invalidate_marker(install_dir: Path) -> None:
    # (install_dir / _MARKER_NAME).unlink(missing_ok=True)

def is_installed(entry: ModelCatalogEntry, data_dir: Path) -> bool:
    # install_dir = install_dir_for(entry, data_dir); marker = _read_marker(install_dir)
    # returns False if marker is missing or its payload != _marker_payload(entry)
    # (id/revision/per-file hash+size must all match the CURRENT catalog entry).
    # Otherwise, a cheap existence+size check per file (NOT a re-hash — hashing
    # only happens inside install()); False if any file is missing/wrong size.

def _file_is_already_verified(dest: Path, file: ModelFile) -> bool:
    # dest.exists() and dest.stat().st_size == file.size_bytes and
    # hashlib.sha256(dest.read_bytes()).hexdigest() == file.sha256
    # (streamed, not read_bytes() in the real implementation, for large files)
    # Reused both to decide whether a file can be skipped on resume/repair,
    # AND to verify a freshly-fetched file — same check, same meaning.

class InstallCancelled(Exception): ...
class ChecksumMismatch(Exception):
    def __init__(self, entry_id: str, file_path: str): ...

FetchFn = Callable[[str, str, str, Path], Path]  # (repository, revision, filename, dest_dir) -> path

def _default_fetch(repository: str, revision: str, filename: str, dest_dir: Path) -> Path:
    # wraps huggingface_hub.hf_hub_download(repo_id=repository, filename=filename,
    # revision=revision, local_dir=dest_dir)

def install(
    entry: ModelCatalogEntry,
    data_dir: Path,
    *,
    fetch: FetchFn | None = None,
    on_file_verified: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    may_finalize: Callable[[], bool] | None = None,
) -> None:
    fetch = fetch if fetch is not None else _default_fetch   # lazy resolution —
    # see the "fetch defaults to None" design decision: a bound default
    # argument would capture _default_fetch at def-time, making it
    # unmonkeypatchable in tests.
    #
    # install_dir = install_dir_for(entry, data_dir); install_dir.mkdir(parents=True, exist_ok=True)
    # marker_invalidated = False
    # for i, file in enumerate(entry.files):
    #     if cancel is not None and cancel.is_set(): raise InstallCancelled          # before
    #     dest = _resolve_file_path(install_dir, file)                              # validated
    #     if not _file_is_already_verified(dest, file):
    #         if not marker_invalidated:
    #             _invalidate_marker(install_dir)   # lazy: only right before the FIRST
    #             marker_invalidated = True         # real mutation, so a no-op call
    #                                                # (nothing needed fixing) never
    #                                                # touches a perfectly good marker
    #         if dest.exists(): dest.unlink()       # stale/corrupt leftover
    #         fetch(entry.repository, entry.revision, file.path, install_dir)
    #         if not _file_is_already_verified(dest, file):    # reuse the same check
    #             dest.unlink(missing_ok=True)
    #             raise ChecksumMismatch(entry.id, file.path)
    #     if cancel is not None and cancel.is_set(): raise InstallCancelled          # after —
    #         # runs for EVERY file (fetched OR skipped-as-already-verified), so a
    #         # cancellation during the last file's processing is never missed
    #         # regardless of which branch that file took
    #     if on_file_verified is not None: on_file_verified(i + 1, len(entry.files))
    # if may_finalize is not None and not may_finalize():
    #     raise InstallCancelled   # the atomic, lock-coordinated final check — see
    #                              # the ModelInstallCoordinator design decision
    # _write_marker_atomically(install_dir, entry)

def remove(entry: ModelCatalogEntry, data_dir: Path) -> None:
    # shutil.rmtree(install_dir_for(...), ignore_errors=False) if it exists; no-op otherwise.
    # Used for: explicit user removal, orphaned-partial-directory cleanup (no
    # in-memory state), and cancellation cleanup (called by the coordinator,
    # not by install() itself — see models_routes.py below).
```

### 2. `backend/pyproject.toml`

Add `"huggingface_hub>=1.31.0"` to `[project].dependencies` (already resolved transitively via `transformers`; this just makes the now-direct import explicit/pinned).

### 3. `backend/src/invoice_renamer/api/models_routes.py` (new)

Module docstring: API routes for host capabilities and installing/removing local models.

```python
@dataclass
class DownloadState:
    status: InstallStatus                              # DOWNLOADING or VERIFICATION_FAILED
    files_done: int = 0
    files_total: int = 0
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

class ModelInstallCoordinator:
    """Owns per-model download state, the data directory, and one lock held across
    each *entire* state-affecting operation (not just each dict mutation) — so e.g.
    a DELETE's decide-then-remove sequence can never interleave with a concurrent
    POST's decide-then-start sequence for the same model. FastAPI runs sync route
    handlers in a threadpool, so this isn't a hypothetical concern even though
    api/server.py never runs multiple uvicorn *workers* (single process)."""

    def __init__(self, data_dir: Path) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, DownloadState] = {}
        self._data_dir = data_dir

    def status_for(self, entry: ModelCatalogEntry) -> InstallStatus:
        with self._lock:
            state = self._states.get(entry.id)
            if state is not None:
                return state.status
        # Released the lock before this filesystem read — it doesn't touch
        # shared state, so it doesn't need exclusivity, and a rare concurrent
        # DELETE holding the lock for its own rmtree shouldn't stall GET /models.
        return InstallStatus.INSTALLED if is_installed(entry, self._data_dir) else InstallStatus.NOT_INSTALLED

    def progress_for(self, model_id: str) -> DownloadState | None:
        with self._lock: return self._states.get(model_id)

    def start(self, entry: ModelCatalogEntry, background_tasks: BackgroundTasks) -> None:
        with self._lock:
            existing = self._states.get(entry.id)
            if existing is not None and existing.status is InstallStatus.DOWNLOADING:
                raise HTTPException(409, "already downloading")
            if existing is None and is_installed(entry, self._data_dir):
                raise HTTPException(409, "already installed")
            # existing.status is VERIFICATION_FAILED, or nothing at all: proceed.
            # Overwriting a VERIFICATION_FAILED entry here IS the retry path —
            # no separate DELETE-then-POST dance required.
            state = DownloadState(status=InstallStatus.DOWNLOADING)
            self._states[entry.id] = state
        background_tasks.add_task(self._run, entry, state)

    def remove_or_cancel(self, entry: ModelCatalogEntry) -> None:
        with self._lock:
            state = self._states.get(entry.id)
            if state is not None and state.status is InstallStatus.DOWNLOADING:
                state.cancel.set()   # no filesystem access here — the background
                return               # thread notices and does its own cleanup
            if state is not None:    # VERIFICATION_FAILED
                remove(entry, self._data_dir)   # may raise OSError -> 500; state
                del self._states[entry.id]      # is left in place so the model
                return                           # stays visibly retryable, not
                                                  # silently dropped from tracking
            if install_dir_for(entry, self._data_dir).exists():
                remove(entry, self._data_dir)   # orphaned partial dir, no in-memory
                return                           # state (e.g. after a restart)
            raise HTTPException(404, "not installed")

    def _run(self, entry: ModelCatalogEntry, state: DownloadState) -> None:
        def on_file_verified(done: int, total: int) -> None:
            state.files_done, state.files_total = done, total

        def may_finalize() -> bool:
            # Called by install() right before writing the marker. Holding the
            # SAME lock remove_or_cancel() uses for state.cancel.set() means a
            # cancellation requested up to this exact instant is guaranteed to
            # be observed here — this is what actually closes the "cancelled
            # during the last file, install finishes anyway" gap.
            with self._lock:
                return not state.cancel.is_set()

        try:
            install(entry, self._data_dir, cancel=state.cancel,
                    on_file_verified=on_file_verified, may_finalize=may_finalize)
        except InstallCancelled:
            with self._lock:
                try:
                    remove(entry, self._data_dir)
                except OSError as exc:
                    # Cleanup itself failed: land in a RETRYABLE state instead
                    # of leaving DOWNLOADING stuck forever with no thread left
                    # to ever clear it.
                    state.status, state.error = InstallStatus.VERIFICATION_FAILED, f"cleanup after cancel failed: {exc}"
                    return
                self._states.pop(entry.id, None)
            return
        except Exception as exc:                      # ChecksumMismatch or any I/O failure
            with self._lock:
                state.status, state.error = InstallStatus.VERIFICATION_FAILED, str(exc)
            return
        with self._lock:
            self._states.pop(entry.id, None)           # success: the marker on disk is now the truth
```

```python
class ModelStatusEntry(ModelPickerEntry):
    """ModelPickerEntry plus the progress/error detail GET /models, POST .../download,
    and DELETE ... all need to expose — kept out of picker.py itself so its existing,
    tested contract stays untouched."""
    files_done: int | None = None
    files_total: int | None = None
    error: str | None = None
```

- `GET /capabilities` → `detect_capabilities(disk_path=resolve_data_dir())`.
- `GET /models` → for each catalog entry, `coordinator.status_for(entry)` feeds into `build_model_picker(..., has_cloud_key=False)` (one-line comment: no cloud-key infra exists yet, no `CLOSED_CLOUD` entries in the real catalog today) to get each `ModelPickerEntry`; wraps each into a `ModelStatusEntry`, filling `files_done`/`files_total`/`error` from `coordinator.progress_for(entry.id)` when present.
- `POST /models/{id}/download` → 404 unknown id; else `coordinator.start(entry, background_tasks)` (raises the appropriate `HTTPException` for the 409 cases internally); return `202` with the current `ModelStatusEntry`.
- `DELETE /models/{id}` → 404 unknown id; else `coordinator.remove_or_cancel(entry)`; return the current `ModelStatusEntry`.
- `app.state.model_install_coordinator: ModelInstallCoordinator` is created **eagerly in `create_app()`** (`app.state.model_install_coordinator = ModelInstallCoordinator(resolve_data_dir())`), not lazily on first request — lazy `getattr`/`setattr` init has its own race where two simultaneous first requests could each construct a fresh coordinator and one would silently discard the other's state. This is the only change `create_app()` needs; its signature stays the same.

Mount via `app.include_router(models_router)` in `api/app.py` — the existing app-level `Depends(require_session_token)` auto-protects these new routes, no per-route auth wiring needed.

### 4. Tests

**`backend/tests/models/test_installer.py`** (new) — local `_open_local_entry(**overrides)` factory matching `test_picker.py`'s exact shape, but with 2-3 small `ModelFile`s carrying real precomputed sha256 over fixed byte strings so a fake `fetch` can write deterministic content:
- data-dir resolution: env var respected; per-OS default branches (monkeypatch `installer.platform.system`)
- `install_dir_for` scoped by id+revision
- `is_installed`: false when dir/marker absent; false when the marker exists but its payload (id/revision/per-file hash+size) doesn't match the current entry; false when the marker is valid but a file is missing/wrong size; true when marker valid and every file present
- **`is_installed` is false even when every file happens to be present and correctly sized, if the marker was never written** — the crash-mid-install / no-marker-written scenario this whole design exists to prevent
- **`is_installed` is false when the marker's recorded file hash/size doesn't match the current catalog entry's**, even though the marker's id/revision/paths still match — simulates the catalog fixing a wrong recorded hash for a file without bumping the revision string; a stale marker from before the fix must not keep claiming installed
- `install`: writes+verifies every file, fires `on_file_verified` with increasing `(done, total)`, writes the marker only after the loop completes
- `install`: checksum mismatch → raises `ChecksumMismatch`, deletes only the bad file, leaves already-verified files in place, marker not written, `is_installed()` still false
- **`install` resumes by skipping a file that's already present and hash-verified** — a fake `fetch` call-counter asserts it's never called for that file on the second `install()` call
- **`install` re-downloads a file that exists with the right size but a wrong hash** (simulated corruption) rather than trusting size alone
- **`install`'s repair of one broken file among several good ones invalidates the marker before touching anything, and doesn't touch the still-good files** — pre-populate all files correctly + a valid marker, corrupt one file's bytes on disk, call `install()` again, and assert: the marker is gone as soon as the repair starts (assert via a `fetch` side-effect that checks marker absence *during* the call), the good files' bytes are untouched, and a fresh marker exists only after the repair completes
- **`install` never touches an existing, valid marker when every file already verifies** (a redundant/defensive call with nothing to fix) — fake `fetch` must never be called, and the marker file's mtime/content is unchanged
- `install`: cancellation before the first file raises `InstallCancelled` immediately, `fetch` never called
- **`install`: cancellation set *during* the last file's fetch (via a side effect inside the fake `fetch`) still raises `InstallCancelled` and does not write the marker** — assert the file's bytes are on disk (preserved for a future resume) but `is_installed()` is still false
- **`install`: cancellation set *during* the last file when that file was actually a skip (already-verified, not re-fetched)** — the cancel-check must fire after the skip branch too, not just after a real fetch; same assertions as above
- **`install`: `may_finalize` returning `False` after every file verified correctly still raises `InstallCancelled` and does not write the marker** — the atomic "about to finalize" gate, tested independently of the `cancel` Event itself (a fake `may_finalize` that just returns `False`)
- `remove`: deletes the install dir; no-op when nothing installed
- security block: a `ModelFile(path="../../etc/passwd")` is rejected by `_resolve_file_path` and by `install()` (asserting `fetch` is never called for it)
- **regression test for the late-binding default bug**: `monkeypatch.setattr(installer, "_default_fetch", fake)` *after* import, then call `install()` with no `fetch=` argument at all, and assert the fake was used — this is the exact scenario the original `fetch: FetchFn = _default_fetch` signature would have silently failed, so the test must call `install()` without explicitly passing `fetch` to be meaningful

**`backend/tests/models/test_install_coordinator.py`** (new) — mostly no filesystem/network involved (a fake `install`/`remove` injected or monkeypatched at module level where the coordinator calls them):
- `start` raises 409 when already `DOWNLOADING`; raises 409 when already installed; succeeds (and schedules a background task) otherwise
- **`start` succeeds directly on a `VERIFICATION_FAILED` model** (retry via POST, no DELETE needed) — asserts the old state is replaced with a fresh `DOWNLOADING` one
- `remove_or_cancel`: sets the cancel event and returns without touching the filesystem when `DOWNLOADING`; removes + clears state when `VERIFICATION_FAILED`; removes an orphaned partial directory with no in-memory state; raises 404 when nothing exists at all
- **`remove_or_cancel` racing a concurrent `start` for the same id**: using real threads with a controllable fake `remove()` that blocks on an `Event` until released — start a `remove_or_cancel()` call (state is `VERIFICATION_FAILED`) on one thread, let it block mid-`remove()`, attempt `start()` on another thread and assert it blocks (doesn't proceed) until the first thread's `remove()` finishes and releases the lock; then assert the second thread's `start()` succeeds cleanly afterward with no partial/mixed state. This is the exact "DELETE removes files a concurrent retry is writing" race from review.
- **cleanup-after-cancellation failure transitions to `VERIFICATION_FAILED`, not a stuck `DOWNLOADING`** — a fake `remove()` that raises `OSError` inside the `InstallCancelled` handler; assert the state ends up `VERIFICATION_FAILED` with the error recorded, and that a subsequent `start()` call is then allowed (proving it's retryable, not permanently stuck)
- **concurrency**: `concurrent.futures.ThreadPoolExecutor` with ~20-50 threads all calling `start` for the same id simultaneously (with a fake `install` that's a no-op) — assert exactly one thread's background task actually got scheduled, the rest got 409. Catches a missing/broken lock; deterministic once correct.
- `may_finalize`-style coordination: a fake `install()` that calls the supplied `may_finalize` callback itself (simulating "cancel arrived right before finalizing") and asserts the coordinator's callback correctly reflects a concurrent `remove_or_cancel()` call's cancellation

**`backend/tests/api/test_models.py`** (new) — mirrors `test_health.py`'s `client(monkeypatch)` fixture (`INVOICE_RENAMER_SESSION_TOKEN` + `INVOICE_RENAMER_DATA_DIR=str(tmp_path)`), with `models_routes.SHORTLISTED_CATALOG` monkeypatched to a small local catalog and `installer._default_fetch` monkeypatched to a deterministic fake (now correctly interceptable, per the fix above) so no real network calls happen:
- capabilities/list-models basic shape
- download: 404 unknown, 409 already-installed, 409 already-downloading
- delete: 404 unknown-id-with-nothing-on-disk
- **delete cleans up an orphaned partial directory with no in-memory state** — write a partial install directory directly (a couple of files, no marker, nothing in the coordinator, simulating "the process restarted mid-download") and assert `DELETE` returns success and removes it, not 404
- **happy path end-to-end** (`BackgroundTasks.add_task` monkeypatched to run synchronously): `GET /models` (NOT_INSTALLED) → `POST download` (202) → `GET /models` (INSTALLED) → `DELETE` → `GET /models` (NOT_INSTALLED, install dir gone) — crosses catalog → installer → coordinator → picker → compatibility → API
- **a `VERIFICATION_FAILED` model can be retried with a second `POST` alone** (no `DELETE` in between) and ends up `INSTALLED`
- **controlled concurrency**: a fake `install()` that blocks on a `threading.Event` until told to proceed, run via a real background thread (not monkeypatched synchronous) — issue `POST download`, confirm a second `POST` for the same id gets 409 while the first is still blocked, then issue `DELETE` while still blocked (assert it returns immediately without touching the filesystem), then release the block and confirm the background thread's own cancellation cleanup runs without error and `GET /models` settles to `NOT_INSTALLED`
- `GET /models`'s response includes `files_done`/`files_total` while a fake, controllably-blocked download is in progress, and `error` once one has failed — the `ModelStatusEntry` contract this design promised
- security/sanity block: all 4 routes 401 without a token (parametrized, mirroring `test_health.py`); checksum failure never reaches `INSTALLED` (ends in `VERIFICATION_FAILED`, bad file absent); delete-after-verification-failure clears both state and files; a path-traversal `ModelFile` never writes outside `tmp_path`; `GET /models` reports the active coordinator status even when a contrived filesystem state would otherwise look installed (proves the "check active state first" ordering, not just that it happens to work when the two never disagree)

**`backend/tests/models/test_catalog_data.py`** — one additional test against the *real* `SHORTLISTED_CATALOG`: every real entry's files resolve within its install dir (cheap, no network — catches a bad catalog path before it would matter at install time).

### 5. `CHANGES.md`

New minor-version entry once implemented: local models can now actually be installed/removed (resumable, checksum-verified) via `GET /capabilities`, `GET /models`, `POST /models/{id}/download`, `DELETE /models/{id}`.

## Verification

1. `cd backend && uv run pytest` — all new + existing tests green (no real network access in any test).
2. `uv run ruff format --check src tests`, `uv run ruff check src tests`, `uv run mypy src`.
3. Manual real-download smoke test (user runs on their own machine, since this sandbox shouldn't pull multi-GB weights): start the worker, `curl` (or the benchmark script's auth pattern) `POST /models/qwen3-0.6b/download`, poll `GET /models` until `INSTALLED`, confirm files exist under the resolved data dir, then `DELETE /models/qwen3-0.6b` and confirm removal.
