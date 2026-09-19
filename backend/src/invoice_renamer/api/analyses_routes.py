"""API routes for submitting invoice analysis jobs and polling their status/result."""

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

import psutil
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from invoice_renamer.analysis.memory_preflight import check_memory_headroom
from invoice_renamer.analysis.pipeline import run_document_analysis
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.inference.runtime import LoadInstalledFn, ModelRuntime, select_device
from invoice_renamer.metrics.models import RunMetrics
from invoice_renamer.models.capabilities import SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry
from invoice_renamer.models.catalog_data import SHORTLISTED_CATALOG
from invoice_renamer.models.detection import detect_capabilities
from invoice_renamer.models.installer import is_installed
from invoice_renamer.naming.schema import FilenameProposal

analyses_router = APIRouter()

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_MAX_PENDING_BYTES = 500 * 1024 * 1024  # ~10 max-size PDFs queued/running at once
_PDF_MAGIC = b"%PDF-"
_BYTES_PER_GB = 1024**3


def _available_memory_gb() -> float:
    return psutil.virtual_memory().available / _BYTES_PER_GB


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
    pdf_bytes: bytes | None  # dropped once terminal
    proposal: FilenameProposal | None = None
    metrics: RunMetrics | None = None
    error: str | None = None
    cancel_requested: bool = False
    memory_warning: str | None = None


class AnalysisJobView(BaseModel):
    """Public shape for GET /jobs/{id} and the POST /analyses response."""

    id: str
    model_id: str
    original_filename: str
    status: JobStatus
    proposal: FilenameProposal | None = None
    metrics: RunMetrics | None = None
    error: str | None = None
    memory_warning: str | None = None


def _job_view(job: AnalysisJob) -> AnalysisJobView:
    return AnalysisJobView(
        id=job.id,
        model_id=job.model_id,
        original_filename=job.original_filename,
        status=job.status,
        proposal=job.proposal,
        metrics=job.metrics,
        error=job.error,
        memory_warning=job.memory_warning,
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


class AnalysisCoordinator:
    """Serializes analysis on one worker, with queue membership and job status
    changed under the same lock so cancellation cannot race job admission."""

    def __init__(
        self,
        data_dir: Path,
        *,
        load_installed: LoadInstalledFn | None = None,
        capabilities_fn: Callable[[], SystemCapabilities] | None = None,
        available_memory_gb_fn: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._jobs: dict[str, AnalysisJob] = {}
        self._queue: deque[str] = deque()
        self._data_dir = data_dir
        self._runtime = ModelRuntime(load_installed=load_installed)
        self._capabilities_fn = capabilities_fn or (lambda: detect_capabilities(disk_path=data_dir))
        self._available_memory_gb_fn = available_memory_gb_fn or _available_memory_gb
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def submit(
        self, entry: ModelCatalogEntry, pdf_bytes: bytes, original_filename: str
    ) -> AnalysisJob:
        if not is_installed(entry, self._data_dir):
            raise HTTPException(422, f"model {entry.id!r} is not installed")
        loaded_id = self._runtime.loaded_entry_id()
        resident_entry = _entry_by_id(loaded_id) if loaded_id is not None else None
        # Only credit a resident model's *measured* footprint once it's
        # actually reached it - right after loading, before its first
        # generate() call, real usage can be well under that figure.
        resident_memory_gb = (
            resident_entry.estimated_memory_gb
            if resident_entry is not None
            and resident_entry.estimated_memory_gb is not None
            and self._runtime.is_warmed()
            else 0.0
        )
        job = AnalysisJob(
            id=str(uuid4()),
            model_id=entry.id,
            original_filename=original_filename,
            status=JobStatus.QUEUED,
            pdf_bytes=pdf_bytes,
            memory_warning=check_memory_headroom(
                entry, self._available_memory_gb_fn(), resident_memory_gb=resident_memory_gb
            ),
        )
        with self._condition:
            pending = sum(
                len(j.pdf_bytes)
                for j in self._jobs.values()
                if j.status in (JobStatus.QUEUED, JobStatus.RUNNING) and j.pdf_bytes is not None
            )
            if pending + len(pdf_bytes) > _MAX_PENDING_BYTES:
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
                assert job.pdf_bytes is not None
                proposal, metrics = run_document_analysis(
                    job.pdf_bytes, load_model, model_id=entry.id, model_revision=entry.revision
                )
                if metrics.inference_ran:
                    # generate() has now actually run on this loaded model, so its
                    # measured footprint is trustworthy for the next submission's
                    # pre-flight credit (see submit()).
                    self._runtime.mark_warmed()
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
                    job.status, job.proposal, job.metrics = (
                        JobStatus.COMPLETED,
                        proposal,
                        metrics,
                    )
                job.pdf_bytes = None


@analyses_router.post("/analyses", status_code=202)
async def create_analysis(
    request: Request, file: UploadFile = File(...), model_id: str = Form(...)
) -> AnalysisJobView:
    entry = _entry_or_404(model_id)

    pdf_bytes = b""
    while chunk := await file.read(1024 * 1024):
        pdf_bytes += chunk
        if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"upload exceeds the {_MAX_UPLOAD_BYTES}-byte limit")

    if not pdf_bytes.startswith(_PDF_MAGIC):
        raise HTTPException(422, "upload is not a PDF file")

    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    job = coordinator.submit(entry, pdf_bytes, file.filename or "upload.pdf")
    return _job_view(job)


@analyses_router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> AnalysisJobView:
    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    return _job_view(coordinator.get(job_id))


@analyses_router.delete("/jobs/{job_id}")
def cancel_job(job_id: str, request: Request) -> AnalysisJobView:
    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    return _job_view(coordinator.cancel(job_id))
