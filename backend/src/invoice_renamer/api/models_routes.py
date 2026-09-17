"""API routes for host capabilities and installing/removing local models."""

import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from invoice_renamer.models.capabilities import SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry
from invoice_renamer.models.catalog_data import SHORTLISTED_CATALOG
from invoice_renamer.models.detection import detect_capabilities
from invoice_renamer.models.installer import (
    InstallCancelled,
    install,
    install_dir_for,
    is_installed,
    remove,
)
from invoice_renamer.models.picker import InstallStatus, ModelPickerEntry, build_model_picker

models_router = APIRouter()


@dataclass
class DownloadState:
    status: InstallStatus  # DOWNLOADING or VERIFICATION_FAILED
    files_done: int = 0
    files_total: int = 0
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


class ModelStatusEntry(ModelPickerEntry):
    """ModelPickerEntry plus the progress/error detail GET /models, POST .../download,
    and DELETE ... all need to expose — kept out of picker.py itself so its existing,
    tested contract stays untouched."""

    files_done: int | None = None
    files_total: int | None = None
    error: str | None = None


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

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def status_for(self, entry: ModelCatalogEntry) -> InstallStatus:
        with self._lock:
            state = self._states.get(entry.id)
            if state is not None:
                return state.status
        # Released the lock before this filesystem read — it doesn't touch
        # shared state, so it doesn't need exclusivity, and a rare concurrent
        # DELETE holding the lock for its own rmtree shouldn't stall GET /models.
        return (
            InstallStatus.INSTALLED
            if is_installed(entry, self._data_dir)
            else InstallStatus.NOT_INSTALLED
        )

    def progress_for(self, model_id: str) -> DownloadState | None:
        with self._lock:
            return self._states.get(model_id)

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
                state.cancel.set()  # no filesystem access here — the background
                return  # thread notices and does its own cleanup
            if state is not None:  # VERIFICATION_FAILED
                remove(entry, self._data_dir)  # may raise OSError -> 500; state
                del self._states[entry.id]  # is left in place so the model
                return  # stays visibly retryable, not silently dropped from tracking
            if install_dir_for(entry, self._data_dir).exists():
                remove(entry, self._data_dir)  # orphaned partial dir, no in-memory
                return  # state (e.g. after a restart)
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
            install(
                entry,
                self._data_dir,
                cancel=state.cancel,
                on_file_verified=on_file_verified,
                may_finalize=may_finalize,
            )
        except InstallCancelled:
            with self._lock:
                try:
                    remove(entry, self._data_dir)
                except OSError as exc:
                    # Cleanup itself failed: land in a RETRYABLE state instead
                    # of leaving DOWNLOADING stuck forever with no thread left
                    # to ever clear it.
                    state.status = InstallStatus.VERIFICATION_FAILED
                    state.error = f"cleanup after cancel failed: {exc}"
                    return
                self._states.pop(entry.id, None)
            return
        except Exception as exc:  # ChecksumMismatch or any I/O failure
            with self._lock:
                state.status, state.error = InstallStatus.VERIFICATION_FAILED, str(exc)
            return
        with self._lock:
            self._states.pop(entry.id, None)  # success: the marker on disk is now the truth


def _entry_or_404(model_id: str) -> ModelCatalogEntry:
    for entry in SHORTLISTED_CATALOG:
        if entry.id == model_id:
            return entry
    raise HTTPException(404, f"unknown model id: {model_id}")


def _status_entry(
    entry: ModelCatalogEntry, coordinator: ModelInstallCoordinator, capabilities: SystemCapabilities
) -> ModelStatusEntry:
    status = coordinator.status_for(entry)
    installed_ids = {entry.id} if status is InstallStatus.INSTALLED else set()
    [picked] = build_model_picker([entry], installed_ids=installed_ids, capabilities=capabilities)
    picked.status = status

    progress = coordinator.progress_for(entry.id)
    return ModelStatusEntry(
        **picked.model_dump(),
        files_done=progress.files_done if progress is not None else None,
        files_total=progress.files_total if progress is not None else None,
        error=progress.error if progress is not None else None,
    )


@models_router.get("/capabilities")
def get_capabilities(request: Request) -> SystemCapabilities:
    coordinator: ModelInstallCoordinator = request.app.state.model_install_coordinator
    return detect_capabilities(disk_path=coordinator.data_dir)


@models_router.get("/models")
def list_models(request: Request) -> list[ModelStatusEntry]:
    coordinator: ModelInstallCoordinator = request.app.state.model_install_coordinator
    capabilities = detect_capabilities(disk_path=coordinator.data_dir)
    return [_status_entry(entry, coordinator, capabilities) for entry in SHORTLISTED_CATALOG]


@models_router.post("/models/{model_id}/download", status_code=202)
def download_model(
    model_id: str, request: Request, background_tasks: BackgroundTasks
) -> ModelStatusEntry:
    entry = _entry_or_404(model_id)
    coordinator: ModelInstallCoordinator = request.app.state.model_install_coordinator
    coordinator.start(entry, background_tasks)
    capabilities = detect_capabilities(disk_path=coordinator.data_dir)
    return _status_entry(entry, coordinator, capabilities)


@models_router.delete("/models/{model_id}")
def delete_model(model_id: str, request: Request) -> ModelStatusEntry:
    entry = _entry_or_404(model_id)
    coordinator: ModelInstallCoordinator = request.app.state.model_install_coordinator
    coordinator.remove_or_cancel(entry)
    capabilities = detect_capabilities(disk_path=coordinator.data_dir)
    return _status_entry(entry, coordinator, capabilities)
