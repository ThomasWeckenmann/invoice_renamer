"""Tests for AnalysisCoordinator's idle-timeout auto-unload and explicit
unload command, exercised directly against the coordinator (not the API) so
an injected clock can simulate elapsed idle time without a real wait.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from invoice_renamer.api import analyses_routes
from invoice_renamer.api.analyses_routes import AnalysisCoordinator, AnalysisJob, DocumentFormat
from invoice_renamer.documents.format import detect_document_format
from invoice_renamer.inference.llamacpp_extractor import LlamaCppExtractor
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.installer import _marker_payload, install_dir_for

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"
_VALID_PDF = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
_MODEL_A = "model-a"
_VALID_MODEL_RESPONSE = json.dumps(
    {
        "invoice_date": "2026-09-12",
        "seller": "Apple",
        "product_summary": "MacBook Air",
        "gross_total": "2180",
        "currency": "EUR",
        "language": "en",
        "warnings": [],
    }
)


def _entry(model_id: str = _MODEL_A) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=model_id,
        display_name=model_id,
        license="apache-2.0",
        repository=f"example-org/{model_id}",
        revision="a" * 40,
        memory_tier=MemoryTier.SMALL,
        files=[ModelFile(path="model.bin", sha256="a" * 64, size_bytes=1)],
    )


def _install(entry: ModelCatalogEntry, data_dir: Path) -> None:
    install_dir = install_dir_for(entry, data_dir)
    install_dir.mkdir(parents=True, exist_ok=True)
    for file in entry.files:
        (install_dir / file.path).write_bytes(b"x" * file.size_bytes)
    (install_dir / ".installed.json").write_text(json.dumps(_marker_payload(entry)))


class _FakeExtractor:
    def __init__(
        self, response: str = _VALID_MODEL_RESPONSE, *, block: threading.Event | None = None
    ):
        self._response = response
        self._block = block

    def generate(self, prompt: str) -> str:
        if self._block is not None:
            self._block.wait(timeout=5)
        return self._response

    def close(self) -> None:
        pass


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _wait_until(predicate: object, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.01)
    pytest.fail("condition never became true")


def _submit_and_wait(coordinator: AnalysisCoordinator, entry: ModelCatalogEntry) -> None:
    job = coordinator.submit(
        entry,
        _VALID_PDF,
        "invoice.pdf",
        document_format=detect_document_format(_VALID_PDF) or DocumentFormat.PDF,
    )
    _wait_until(lambda: coordinator.get(job.id).status.value in ("completed", "failed"))
    assert coordinator.get(job.id).status.value == "completed"


def test_idle_timeout_unloads_after_the_deadline_elapses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(analyses_routes, "_IDLE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor()
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    clock = _FakeClock()
    coordinator = AnalysisCoordinator(tmp_path, clock=clock)

    _submit_and_wait(coordinator, entry)
    assert coordinator.runtime_snapshot().loaded_entry_id == _MODEL_A

    clock.advance(analyses_routes._IDLE_UNLOAD_SECONDS + 1)

    _wait_until(lambda: coordinator.runtime_snapshot().loaded_entry_id is None)


def test_idle_timeout_does_not_fire_before_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(analyses_routes, "_IDLE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor()
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    clock = _FakeClock()
    coordinator = AnalysisCoordinator(tmp_path, clock=clock)

    _submit_and_wait(coordinator, entry)
    clock.advance(analyses_routes._IDLE_UNLOAD_SECONDS - 1)

    time.sleep(0.2)  # several poll intervals
    assert coordinator.runtime_snapshot().loaded_entry_id == _MODEL_A


def test_idle_timeout_does_not_fire_while_a_job_is_queued_or_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(analyses_routes, "_IDLE_POLL_SECONDS", 0.02)
    block = threading.Event()
    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor(block=block)
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    clock = _FakeClock()
    coordinator = AnalysisCoordinator(tmp_path, clock=clock)

    job = coordinator.submit(entry, _VALID_PDF, "invoice.pdf", document_format=DocumentFormat.PDF)
    _wait_until(lambda: coordinator.get(job.id).status.value == "running")

    clock.advance(analyses_routes._IDLE_UNLOAD_SECONDS + 1)
    time.sleep(0.2)
    assert coordinator.runtime_snapshot().loaded_entry_id == _MODEL_A

    block.set()
    _wait_until(lambda: coordinator.get(job.id).status.value == "completed")


def test_request_unload_frees_the_model_when_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor()
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    coordinator = AnalysisCoordinator(tmp_path)

    _submit_and_wait(coordinator, entry)
    assert coordinator.runtime_snapshot().loaded_entry_id == _MODEL_A

    coordinator.request_unload()  # must not raise

    assert coordinator.runtime_snapshot().loaded_entry_id is None


def test_request_unload_is_a_no_op_when_nothing_is_loaded(tmp_path: Path) -> None:
    coordinator = AnalysisCoordinator(tmp_path)

    coordinator.request_unload()  # must not raise

    assert coordinator.runtime_snapshot().loaded_entry_id is None


def test_request_unload_raises_409_while_a_job_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import HTTPException

    block = threading.Event()
    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor(block=block)
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    coordinator = AnalysisCoordinator(tmp_path)

    job = coordinator.submit(entry, _VALID_PDF, "invoice.pdf", document_format=DocumentFormat.PDF)
    _wait_until(lambda: coordinator.get(job.id).status.value == "running")

    with pytest.raises(HTTPException) as excinfo:
        coordinator.request_unload()
    assert excinfo.value.status_code == 409

    block.set()
    _wait_until(lambda: coordinator.get(job.id).status.value == "completed")


def test_request_unload_raises_409_while_a_job_is_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import HTTPException

    block = threading.Event()
    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor(block=block)
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    coordinator = AnalysisCoordinator(tmp_path)

    running = coordinator.submit(
        entry, _VALID_PDF, "invoice.pdf", document_format=DocumentFormat.PDF
    )
    _wait_until(lambda: coordinator.get(running.id).status.value == "running")
    coordinator.submit(entry, _VALID_PDF, "invoice-2.pdf", document_format=DocumentFormat.PDF)

    with pytest.raises(HTTPException) as excinfo:
        coordinator.request_unload()
    assert excinfo.value.status_code == 409

    block.set()
    _wait_until(lambda: coordinator.runtime_snapshot().loaded_entry_id == _MODEL_A)


def test_request_unload_surfaces_a_failure_as_500_and_keeps_the_worker_alive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import HTTPException

    from invoice_renamer.inference import runtime as runtime_module

    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor()
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    coordinator = AnalysisCoordinator(tmp_path)

    _submit_and_wait(coordinator, entry)

    def failing_unload(self: object) -> None:
        raise RuntimeError("cache clear exploded")

    monkeypatch.setattr(runtime_module.ModelRuntime, "unload", failing_unload)

    with pytest.raises(HTTPException) as excinfo:
        coordinator.request_unload()
    assert excinfo.value.status_code == 500

    # The worker thread survived the exception and still processes new jobs.
    monkeypatch.setattr(runtime_module.ModelRuntime, "unload", lambda self: None)
    _submit_and_wait(coordinator, entry)


def test_concurrent_unload_requests_all_receive_a_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        LlamaCppExtractor, "load_installed", lambda e, d, *, device: _FakeExtractor()
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    coordinator = AnalysisCoordinator(tmp_path)

    _submit_and_wait(coordinator, entry)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(coordinator.request_unload) for _ in range(5)]
        for future in futures:
            future.result(timeout=5)  # none raise, none hang

    assert coordinator.runtime_snapshot().loaded_entry_id is None


def test_unload_and_job_admission_are_serialized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression test: the busy check that authorizes an unload and the
    unload itself must run under one uninterrupted lock hold. If a job could
    be admitted in a gap between them, it would still be silently unloaded
    out from under - wasting an unload immediately followed by a reload for
    that job, and breaking the "never unload while queued" guarantee."""
    from invoice_renamer.inference import runtime as runtime_module

    load_calls: list[str] = []
    monkeypatch.setattr(
        LlamaCppExtractor,
        "load_installed",
        lambda e, d, *, device: load_calls.append(e.id) or _FakeExtractor(),
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    coordinator = AnalysisCoordinator(tmp_path)

    _submit_and_wait(coordinator, entry)
    assert coordinator.runtime_snapshot().loaded_entry_id == _MODEL_A

    unload_entered = threading.Event()
    release_unload = threading.Event()
    original_unload = runtime_module.ModelRuntime.unload

    def blocking_unload(self: runtime_module.ModelRuntime) -> None:
        unload_entered.set()
        release_unload.wait(timeout=5)
        original_unload(self)

    monkeypatch.setattr(runtime_module.ModelRuntime, "unload", blocking_unload)

    unload_thread = threading.Thread(target=coordinator.request_unload)
    unload_thread.start()
    assert unload_entered.wait(timeout=5), "unload never started"

    submitted: list[AnalysisJob] = []
    submit_thread = threading.Thread(
        target=lambda: submitted.append(
            coordinator.submit(entry, _VALID_PDF, "invoice.pdf", document_format=DocumentFormat.PDF)
        )
    )
    submit_thread.start()
    submit_thread.join(timeout=0.3)
    # If submit() could complete here, a job would have been admitted while
    # the unload it should have been blocked by was still in flight.
    assert submit_thread.is_alive(), "submit() completed while the unload was still in flight"

    release_unload.set()
    unload_thread.join(timeout=5)
    submit_thread.join(timeout=5)

    assert len(submitted) == 1
    _wait_until(lambda: coordinator.get(submitted[0].id).status.value == "completed")
    # Checking residency right after the joins above would race the worker,
    # which can reload for the new job before this assertion even runs -
    # load_calls is the non-racy witness that a real reload happened, proving
    # the unload actually took effect rather than having been skipped.
    assert load_calls == [_MODEL_A, _MODEL_A]


def test_idle_unload_and_job_admission_are_serialized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same property as test_unload_and_job_admission_are_serialized above,
    for the idle-timeout path instead of the explicit-command path: the
    elapsed-idle check and the unload itself must run under one
    uninterrupted lock hold, since a wait() call can return - by notify or
    by its own internal timeout - at essentially the same moment another
    thread's submit() finishes admitting a job. Without this, a job
    admitted in that gap could still be unloaded out from under it despite
    the while condition having let it through moments earlier."""
    from invoice_renamer.inference import runtime as runtime_module

    load_calls: list[str] = []
    monkeypatch.setattr(analyses_routes, "_IDLE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(
        LlamaCppExtractor,
        "load_installed",
        lambda e, d, *, device: load_calls.append(e.id) or _FakeExtractor(),
    )
    entry = _entry()
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [entry])
    _install(entry, tmp_path)
    clock = _FakeClock()
    coordinator = AnalysisCoordinator(tmp_path, clock=clock)

    _submit_and_wait(coordinator, entry)
    assert coordinator.runtime_snapshot().loaded_entry_id == _MODEL_A

    unload_entered = threading.Event()
    release_unload = threading.Event()
    original_unload = runtime_module.ModelRuntime.unload

    def blocking_unload(self: runtime_module.ModelRuntime) -> None:
        unload_entered.set()
        release_unload.wait(timeout=5)
        original_unload(self)

    monkeypatch.setattr(runtime_module.ModelRuntime, "unload", blocking_unload)

    clock.advance(analyses_routes._IDLE_UNLOAD_SECONDS + 1)
    assert unload_entered.wait(timeout=5), "idle unload never started"

    submitted: list[AnalysisJob] = []
    submit_thread = threading.Thread(
        target=lambda: submitted.append(
            coordinator.submit(entry, _VALID_PDF, "invoice.pdf", document_format=DocumentFormat.PDF)
        )
    )
    submit_thread.start()
    submit_thread.join(timeout=0.3)
    # If submit() could complete here, a job would have been admitted while
    # the idle unload it should have been blocked by was still in flight.
    assert submit_thread.is_alive(), "submit() completed while the idle unload was still in flight"

    release_unload.set()
    submit_thread.join(timeout=5)

    assert len(submitted) == 1
    _wait_until(lambda: coordinator.get(submitted[0].id).status.value == "completed")
    # Checking residency right after the joins above would race the worker,
    # which can reload for the new job before this assertion even runs -
    # load_calls is the non-racy witness that a real reload happened, proving
    # the idle unload actually took effect rather than having been skipped.
    assert load_calls == [_MODEL_A, _MODEL_A]
