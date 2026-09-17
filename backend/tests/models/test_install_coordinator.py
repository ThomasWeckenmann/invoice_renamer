"""Tests for ModelInstallCoordinator: the lock-guarded start/cancel/remove state machine."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException

from invoice_renamer.api import models_routes
from invoice_renamer.api.models_routes import ModelInstallCoordinator
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.picker import InstallStatus


def _entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-open-small",
        "display_name": "Example Open Model (Small)",
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [ModelFile(path="model.bin", sha256="a" * 64, size_bytes=10)],
        "memory_tier": MemoryTier.SMALL,
    }
    defaults.update(overrides)
    return ModelCatalogEntry(**defaults)  # type: ignore[arg-type]


def _noop_background_tasks() -> BackgroundTasks:
    # Never actually runs the background task in these tests - each test
    # drives the coordinator's private methods/state directly instead.
    return BackgroundTasks()


def test_start_raises_409_when_already_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: False)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    coordinator.start(entry, _noop_background_tasks())

    with pytest.raises(HTTPException) as excinfo:
        coordinator.start(entry, _noop_background_tasks())

    assert excinfo.value.status_code == 409


def test_start_raises_409_when_already_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: True)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()

    with pytest.raises(HTTPException) as excinfo:
        coordinator.start(entry, _noop_background_tasks())

    assert excinfo.value.status_code == 409


def test_start_succeeds_and_schedules_background_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: False)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    tasks = BackgroundTasks()

    coordinator.start(entry, tasks)

    assert coordinator.progress_for(entry.id) is not None
    assert coordinator.progress_for(entry.id).status is InstallStatus.DOWNLOADING  # type: ignore[union-attr]
    assert len(tasks.tasks) == 1


def test_start_succeeds_directly_on_verification_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: False)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    coordinator._states[entry.id] = models_routes.DownloadState(
        status=InstallStatus.VERIFICATION_FAILED, error="boom"
    )

    coordinator.start(entry, _noop_background_tasks())

    state = coordinator.progress_for(entry.id)
    assert state is not None
    assert state.status is InstallStatus.DOWNLOADING
    assert state.error is None


def test_remove_or_cancel_sets_cancel_event_without_touching_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remove_calls: list[str] = []
    monkeypatch.setattr(
        models_routes, "remove", lambda entry, data_dir: remove_calls.append(entry.id)
    )
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    coordinator._states[entry.id] = models_routes.DownloadState(status=InstallStatus.DOWNLOADING)

    coordinator.remove_or_cancel(entry)

    assert coordinator._states[entry.id].cancel.is_set()
    assert remove_calls == []


def test_remove_or_cancel_removes_and_clears_state_when_verification_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remove_calls: list[str] = []
    monkeypatch.setattr(
        models_routes, "remove", lambda entry, data_dir: remove_calls.append(entry.id)
    )
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    coordinator._states[entry.id] = models_routes.DownloadState(
        status=InstallStatus.VERIFICATION_FAILED
    )

    coordinator.remove_or_cancel(entry)

    assert remove_calls == [entry.id]
    assert entry.id not in coordinator._states


def test_remove_or_cancel_removes_orphaned_partial_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry()
    coordinator = ModelInstallCoordinator(tmp_path)
    install_dir = models_routes.install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    (install_dir / "model.bin").write_bytes(b"partial")

    coordinator.remove_or_cancel(entry)

    assert not install_dir.exists()


def test_remove_or_cancel_raises_404_when_nothing_exists(tmp_path: Path) -> None:
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()

    with pytest.raises(HTTPException) as excinfo:
        coordinator.remove_or_cancel(entry)

    assert excinfo.value.status_code == 404


def test_remove_or_cancel_racing_a_concurrent_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_remove = threading.Event()
    remove_started = threading.Event()

    def blocking_remove(entry: ModelCatalogEntry, data_dir: Path) -> None:
        remove_started.set()
        release_remove.wait(timeout=5)

    monkeypatch.setattr(models_routes, "remove", blocking_remove)
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: False)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    coordinator._states[entry.id] = models_routes.DownloadState(
        status=InstallStatus.VERIFICATION_FAILED
    )

    start_returned = threading.Event()

    def do_remove() -> None:
        coordinator.remove_or_cancel(entry)

    def do_start() -> None:
        remove_started.wait(timeout=5)  # only attempt once remove() is actually blocking
        coordinator.start(entry, _noop_background_tasks())
        start_returned.set()

    remove_thread = threading.Thread(target=do_remove)
    start_thread = threading.Thread(target=do_start)
    remove_thread.start()
    start_thread.start()

    # start() must be blocked on the coordinator's lock while remove() is
    # still running under it.
    assert not start_returned.wait(timeout=0.3)

    release_remove.set()
    remove_thread.join(timeout=5)
    start_thread.join(timeout=5)

    assert start_returned.is_set()
    state = coordinator.progress_for(entry.id)
    assert state is not None
    assert state.status is InstallStatus.DOWNLOADING


def test_cleanup_after_cancellation_failure_lands_in_verification_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_install(entry: ModelCatalogEntry, data_dir: Path, **kwargs: object) -> None:
        raise models_routes.InstallCancelled

    def failing_remove(entry: ModelCatalogEntry, data_dir: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(models_routes, "install", failing_install)
    monkeypatch.setattr(models_routes, "remove", failing_remove)
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: False)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    state = models_routes.DownloadState(status=InstallStatus.DOWNLOADING)
    coordinator._states[entry.id] = state

    coordinator._run(entry, state)

    assert state.status is InstallStatus.VERIFICATION_FAILED
    assert state.error is not None and "permission denied" in state.error

    # Proves it's retryable, not permanently stuck.
    monkeypatch.setattr(models_routes, "remove", lambda entry, data_dir: None)
    coordinator.start(entry, _noop_background_tasks())
    retried_state = coordinator.progress_for(entry.id)
    assert retried_state is not None
    assert retried_state.status is InstallStatus.DOWNLOADING


def test_concurrent_start_calls_schedule_exactly_one_background_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: False)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()

    results: list[str] = []

    def attempt_start() -> None:
        try:
            coordinator.start(entry, _noop_background_tasks())
            results.append("ok")
        except HTTPException as exc:
            assert exc.status_code == 409
            results.append("409")

    with ThreadPoolExecutor(max_workers=30) as executor:
        list(executor.map(lambda _: attempt_start(), range(30)))

    assert results.count("ok") == 1
    assert results.count("409") == 29


def test_may_finalize_reflects_concurrent_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_may_finalize: list[object] = []

    def fake_install(entry: ModelCatalogEntry, data_dir: Path, **kwargs: object) -> None:
        may_finalize = kwargs["may_finalize"]
        captured_may_finalize.append(may_finalize)
        cancel = kwargs["cancel"]
        assert isinstance(cancel, threading.Event)
        cancel.set()  # simulate a concurrent remove_or_cancel() call setting it
        assert may_finalize() is False  # type: ignore[operator]
        raise models_routes.InstallCancelled

    monkeypatch.setattr(models_routes, "install", fake_install)
    monkeypatch.setattr(models_routes, "remove", lambda entry, data_dir: None)
    monkeypatch.setattr(models_routes, "is_installed", lambda entry, data_dir: False)
    coordinator = ModelInstallCoordinator(tmp_path)
    entry = _entry()
    state = models_routes.DownloadState(status=InstallStatus.DOWNLOADING)
    coordinator._states[entry.id] = state

    coordinator._run(entry, state)

    assert len(captured_may_finalize) == 1
    assert entry.id not in coordinator._states
