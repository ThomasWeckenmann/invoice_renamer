"""API routes for submitting invoice analysis jobs and polling their status/result."""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from invoice_renamer.analysis.pipeline import run_document_analysis
from invoice_renamer.documents.format import DocumentFormat, detect_document_format
from invoice_renamer.documents.image_open import open_validated_jpeg
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.inference.runtime import (
    LoadInstalledFn,
    ModelRuntime,
    RuntimeSnapshot,
    select_device,
)
from invoice_renamer.metrics.models import RunMetrics
from invoice_renamer.models.capabilities import SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry
from invoice_renamer.models.catalog_data import SHORTLISTED_CATALOG
from invoice_renamer.models.detection import detect_capabilities
from invoice_renamer.models.installer import is_installed
from invoice_renamer.naming.schema import FilenameProposal

analyses_router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_MAX_PENDING_BYTES = 500 * 1024 * 1024  # ~10 max-size documents queued/running at once
_IDLE_UNLOAD_SECONDS = 5 * 60
# How often the worker rechecks elapsed idle time while a model is loaded and
# nothing is queued - independent of the injected clock, so real polling stays
# responsive to a notify() without needing a precisely-timed wakeup.
_IDLE_POLL_SECONDS = 5.0


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
    document_bytes: bytes | None  # dropped once terminal
    document_format: DocumentFormat
    shorten_fields: bool = True
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


def _job_view(job: AnalysisJob) -> AnalysisJobView:
    return AnalysisJobView(
        id=job.id,
        model_id=job.model_id,
        original_filename=job.original_filename,
        status=job.status,
        proposal=job.proposal,
        metrics=job.metrics,
        error=job.error,
    )


def _entry_by_id(model_id: str) -> ModelCatalogEntry | None:
    for entry in SHORTLISTED_CATALOG:
        if entry.id == model_id:
            return entry
    return None


def _entry_or_404(model_id: str) -> ModelCatalogEntry:
    entry = _entry_by_id(model_id)
    if entry is None:
        raise HTTPException(404, f"unknown model id: {model_id}")
    return entry


@dataclass
class _UnloadCommand:
    """Handoff for an explicit unload request: the requesting thread blocks on
    `done` while the worker thread - the only thread allowed to mutate
    ModelRuntime - actually performs the unload and fills in the result."""

    done: threading.Event = field(default_factory=threading.Event)
    result: str | None = None  # "ok" or "busy", set by the worker
    error: str | None = None


class AnalysisCoordinator:
    """Serializes analysis on one worker, with queue membership and job status
    changed under the same lock so cancellation cannot race job admission."""

    def __init__(
        self,
        data_dir: Path,
        *,
        load_installed: LoadInstalledFn | None = None,
        capabilities_fn: Callable[[], SystemCapabilities] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._jobs: dict[str, AnalysisJob] = {}
        self._queue: deque[str] = deque()
        self._data_dir = data_dir
        self._runtime = ModelRuntime(load_installed=load_installed)
        self._capabilities_fn = capabilities_fn or (lambda: detect_capabilities(disk_path=data_dir))
        self._clock = clock or time.monotonic
        self._last_activity = self._clock()
        self._pending_unloads: list[_UnloadCommand] = []
        # Set/cleared only by the worker thread, under self._lock, around the
        # job body it runs outside the lock - see request_unload()'s docstring
        # for why this can't be inferred from _pending_unloads alone.
        self._running_job_id: str | None = None
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def runtime_snapshot(self) -> RuntimeSnapshot:
        """Small immutable copy of what's currently loaded, for the memory
        sampler - see ModelRuntime.snapshot() for why this is safe to call
        from a request-handling thread."""
        return self._runtime.snapshot()

    def request_unload(self) -> None:
        """Blocks the calling (request-handling) thread until the worker
        thread has processed an explicit unload command - ModelRuntime is
        only ever mutated from the worker thread, matching get_or_load().

        A running job is only ever visible *before* the worker loop reaches
        its pending-command check - by the time the worker gets there, a job
        it was running has necessarily already finished. So "busy" for a
        RUNNING job has to be decided here, immediately, under the same lock
        the worker uses to set/clear _running_job_id; a QUEUED job is instead
        rechecked by the worker itself right before it would unload, since a
        job can be submitted in the window between this check and that one.

        Raises HTTPException(409) if a job is queued/running, or
        HTTPException(500) if the unload itself raised."""
        command = _UnloadCommand()
        with self._condition:
            if self._running_job_id is not None or self._queue:
                raise HTTPException(409, "cannot unload while a job is queued or running")
            self._pending_unloads.append(command)
            self._condition.notify()
        command.done.wait()
        if command.error is not None:
            raise HTTPException(500, command.error)
        if command.result == "busy":
            raise HTTPException(409, "cannot unload while a job is queued or running")

    def submit(
        self,
        entry: ModelCatalogEntry,
        document_bytes: bytes,
        original_filename: str,
        *,
        document_format: DocumentFormat,
        shorten_fields: bool = True,
    ) -> AnalysisJob:
        if not is_installed(entry, self._data_dir):
            raise HTTPException(422, f"model {entry.id!r} is not installed")
        job = AnalysisJob(
            id=str(uuid4()),
            model_id=entry.id,
            original_filename=original_filename,
            status=JobStatus.QUEUED,
            document_bytes=document_bytes,
            document_format=document_format,
            shorten_fields=shorten_fields,
        )
        with self._condition:
            pending = sum(
                len(j.document_bytes)
                for j in self._jobs.values()
                if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
                and j.document_bytes is not None
            )
            if pending + len(document_bytes) > _MAX_PENDING_BYTES:
                raise HTTPException(
                    429, "pending analysis queue is full; retry once earlier jobs complete"
                )
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
                    # The worker already popped it under this same lock, right
                    # on the QUEUED/RUNNING boundary - fall through and treat
                    # it like the RUNNING case below on a later call if needed.
                    pass
                else:
                    job.status, job.document_bytes = JobStatus.CANCELLED, None
            elif job.status is JobStatus.RUNNING:
                job.cancel_requested = True
            return job

    def _worker_loop(self) -> None:
        while True:
            job = self._next_step()
            if job is not None:
                self._run_job(job)

    def _next_step(self) -> AnalysisJob | None:
        """Waits for, and handles, whatever the worker has to do next: an
        explicit unload command, an idle-timeout unload, or a queued job.

        A command or an idle unload is fully decided *and executed* here,
        still holding self._lock - not just decided here and carried out
        after releasing it. Releasing the lock in between would let submit()
        admit a new job into the now-empty-looking queue in the gap, which
        would make the unload run anyway despite that job, wasting an
        unload+immediate-reload on it and breaking the queued-job guarantee
        this is supposed to provide. The unload itself (gc.collect(), and on
        GPU builds a cache-clear call) briefly blocks submit()/cancel()/get()
        for its duration - acceptable, since correctness here matters more
        than that brief availability cost, unlike the job body below, which
        can run for a long time and is deliberately run outside the lock.
        """
        with self._condition:
            while not self._queue and not self._pending_unloads:
                # Rechecked fresh at the top of every iteration - the only
                # point guaranteed to hold self._lock with nothing released
                # since. A wait() call below releases the lock while blocked;
                # it can return (by notify *or* by its internal timeout
                # firing) at essentially the same moment another thread's
                # submit() finishes admitting a job, so the elapsed-idle
                # check right after a wait() timeout can't be trusted
                # without also reconfirming queue/pending here - otherwise a
                # job admitted in that gap could still get unloaded out from
                # under it despite the while condition having let it through.
                loaded = self._runtime.loaded_entry_id() is not None
                if loaded and self._clock() - self._last_activity >= _IDLE_UNLOAD_SECONDS:
                    self._run_idle_unload_locked()
                    return None
                # Nothing loaded means nothing to time out - wait indefinitely;
                # only a notify (a submission or an unload command) matters.
                self._condition.wait(timeout=_IDLE_POLL_SECONDS if loaded else None)
                # Woken by notify or by timing out - loop back to the top,
                # where both the while condition and the check above are
                # re-evaluated against current state, not stale pre-wait state.

            if self._pending_unloads:
                commands, self._pending_unloads = self._pending_unloads, []
                if self._queue:
                    for command in commands:
                        command.result = "busy"
                        command.done.set()
                else:
                    self._run_unload_commands_locked(commands)
                return None

            job_id = self._queue.popleft()
            job = self._jobs[job_id]
            job.status = JobStatus.RUNNING
            self._running_job_id = job_id
            return job

    def _run_job(self, job: AnalysisJob) -> None:
        entry = _entry_or_404(job.model_id)
        # Clear this local reference after each job so the runtime can free
        # the previous model before loading a different one.
        model: LanguageModel | None = None

        def load_model() -> LanguageModel:
            # Deferred until run_document_analysis finds it actually needs
            # inference - a job that a complete XML extraction can answer
            # alone must never load a model at all.
            nonlocal model
            device = select_device(self._capabilities_fn())
            model = self._runtime.get_or_load(entry, self._data_dir, device)
            return model

        try:
            assert job.document_bytes is not None
            proposal, metrics = run_document_analysis(
                job.document_bytes,
                load_model,
                model_id=entry.id,
                model_revision=entry.revision,
                shorten_enabled=job.shorten_fields,
                document_format=job.document_format,
            )
        except Exception as exc:
            with self._lock:
                job.status, job.error, job.document_bytes = JobStatus.FAILED, str(exc), None
                self._last_activity = self._clock()
                self._running_job_id = None
            return
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
                job.status, job.proposal, job.metrics = (
                    JobStatus.COMPLETED,
                    proposal,
                    metrics,
                )
            job.document_bytes = None
            self._last_activity = self._clock()
            self._running_job_id = None

    def _run_unload_commands_locked(self, commands: list[_UnloadCommand]) -> None:
        """Caller must hold self._lock - see _next_step()."""
        try:
            self._runtime.unload()
        except Exception as exc:
            logger.exception("explicit model unload failed")
            for command in commands:
                command.error = str(exc)
                command.done.set()
            return
        for command in commands:
            command.result = "ok"
            command.done.set()

    def _run_idle_unload_locked(self) -> None:
        """Caller must hold self._lock - see _next_step()."""
        try:
            self._runtime.unload()
        except Exception:
            # ModelRuntime clears its residency state before the exception-
            # prone cache-clearing calls, so loaded_entry_id() already
            # reflects "unloaded" here - this can't repeat into a tight loop,
            # it's just logged so a leaked GPU cache isn't silent.
            logger.exception("idle model unload failed")


@analyses_router.post("/analyses", status_code=202)
async def create_analysis(
    request: Request,
    file: UploadFile = File(...),
    model_id: str = Form(...),
    shorten_fields: bool = Form(True),
) -> AnalysisJobView:
    entry = _entry_or_404(model_id)

    document_bytes = b""
    while chunk := await file.read(1024 * 1024):
        document_bytes += chunk
        if len(document_bytes) > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"upload exceeds the {_MAX_UPLOAD_BYTES}-byte limit")

    document_format = detect_document_format(document_bytes)
    if document_format is None:
        raise HTTPException(422, "upload must be a PDF or JPEG file")
    if document_format is DocumentFormat.JPEG:
        try:
            open_validated_jpeg(document_bytes)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    job = coordinator.submit(
        entry,
        document_bytes,
        file.filename or f"upload{document_format.extension}",
        document_format=document_format,
        shorten_fields=shorten_fields,
    )
    return _job_view(job)


@analyses_router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> AnalysisJobView:
    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    return _job_view(coordinator.get(job_id))


@analyses_router.delete("/jobs/{job_id}")
def cancel_job(job_id: str, request: Request) -> AnalysisJobView:
    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    return _job_view(coordinator.cancel(job_id))
