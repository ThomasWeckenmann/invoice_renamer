"""Tests for /capabilities, /models, and the download/delete lifecycle routes."""

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoice_renamer.api import models_routes
from invoice_renamer.api.app import create_app
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.installer import install_dir_for

TOKEN = "test-session-token"

_MODEL_ID = "tiny-model"
_FILE_CONTENT = b"pretend-weights"


def _small_catalog() -> list[ModelCatalogEntry]:
    import hashlib

    return [
        ModelCatalogEntry(
            id=_MODEL_ID,
            display_name="Tiny Model",
            license="apache-2.0",
            repository="example-org/tiny-model",
            revision="abc123",
            memory_tier=MemoryTier.SMALL,
            files=[
                ModelFile(
                    path="model.bin",
                    sha256=hashlib.sha256(_FILE_CONTENT).hexdigest(),
                    size_bytes=len(_FILE_CONTENT),
                )
            ],
        )
    ]


def _fake_fetch(repository: str, revision: str, filename: str, dest_dir: Path) -> Path:
    dest = dest_dir / filename
    dest.write_bytes(_FILE_CONTENT)
    return dest


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("INVOICE_RENAMER_SESSION_TOKEN", TOKEN)
    monkeypatch.setenv("INVOICE_RENAMER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models_routes, "SHORTLISTED_CATALOG", _small_catalog())
    monkeypatch.setattr("invoice_renamer.models.installer._default_fetch", _fake_fetch)
    return TestClient(create_app())


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_capabilities_basic_shape(client: TestClient) -> None:
    response = client.get("/capabilities", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert "memory_gb" in body
    assert "free_disk_gb" in body
    assert "acceleration" in body


def test_list_models_basic_shape(client: TestClient) -> None:
    response = client.get("/models", headers=_auth_headers())

    assert response.status_code == 200
    [model] = response.json()
    assert model["entry"]["id"] == _MODEL_ID
    assert model["status"] == "not_installed"


def test_download_unknown_model_is_404(client: TestClient) -> None:
    response = client.post("/models/does-not-exist/download", headers=_auth_headers())

    assert response.status_code == 404


def test_download_already_installed_is_409(client: TestClient, tmp_path: Path) -> None:
    entry = _small_catalog()[0]
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    (install_dir / "model.bin").write_bytes(_FILE_CONTENT)
    import json

    from invoice_renamer.models.installer import _marker_payload

    (install_dir / ".installed.json").write_text(json.dumps(_marker_payload(entry)))

    response = client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())

    assert response.status_code == 409


def test_download_already_downloading_is_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    block = threading.Event()
    started = threading.Event()

    def blocking_install(entry: ModelCatalogEntry, data_dir: Path, **kwargs: object) -> None:
        started.set()
        block.wait(timeout=5)

    monkeypatch.setattr(models_routes, "install", blocking_install)

    # TestClient/BackgroundTasks run the task synchronously as part of
    # handling the request, so the first POST must run on its own thread for
    # a second, concurrent POST to observe it mid-flight.
    first_thread = threading.Thread(
        target=lambda: client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    )
    first_thread.start()
    assert started.wait(timeout=5)

    second = client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())

    block.set()
    first_thread.join(timeout=5)
    assert second.status_code == 409


def test_delete_unknown_id_with_nothing_on_disk_is_404(client: TestClient) -> None:
    response = client.delete(f"/models/{_MODEL_ID}", headers=_auth_headers())

    assert response.status_code == 404


def test_delete_cleans_up_orphaned_partial_directory(client: TestClient, tmp_path: Path) -> None:
    entry = _small_catalog()[0]
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    (install_dir / "model.bin").write_bytes(b"partial-garbage")

    response = client.delete(f"/models/{_MODEL_ID}", headers=_auth_headers())

    assert response.status_code == 200
    assert not install_dir.exists()


def test_happy_path_end_to_end(client: TestClient, tmp_path: Path) -> None:
    not_installed = client.get("/models", headers=_auth_headers()).json()[0]
    assert not_installed["status"] == "not_installed"

    download = client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    assert download.status_code == 202

    installed = client.get("/models", headers=_auth_headers()).json()[0]
    assert installed["status"] == "installed"

    delete = client.delete(f"/models/{_MODEL_ID}", headers=_auth_headers())
    assert delete.status_code == 200

    final = client.get("/models", headers=_auth_headers()).json()[0]
    assert final["status"] == "not_installed"
    entry = _small_catalog()[0]
    assert not install_dir_for(entry, tmp_path).exists()


def test_verification_failed_model_retried_with_second_post_alone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def flaky_install(entry: ModelCatalogEntry, data_dir: Path, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("network blip")
        from invoice_renamer.models.installer import install as real_install

        real_install(
            entry,
            data_dir,
            fetch=_fake_fetch,
            on_file_verified=kwargs.get("on_file_verified"),  # type: ignore[arg-type]
            may_finalize=kwargs.get("may_finalize"),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(models_routes, "install", flaky_install)

    first = client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    assert first.status_code == 202
    failed = client.get("/models", headers=_auth_headers()).json()[0]
    assert failed["status"] == "verification_failed"
    assert failed["error"] is not None

    second = client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    assert second.status_code == 202
    settled = client.get("/models", headers=_auth_headers()).json()[0]
    assert settled["status"] == "installed"


def test_controlled_concurrency_download_cancel_delete(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proceed = threading.Event()
    started = threading.Event()

    def blocking_install(entry: ModelCatalogEntry, data_dir: Path, **kwargs: object) -> None:
        started.set()
        cancel = kwargs["cancel"]
        assert isinstance(cancel, threading.Event)
        proceed.wait(timeout=5)
        if cancel.is_set():
            from invoice_renamer.models.installer import InstallCancelled

            raise InstallCancelled

    monkeypatch.setattr(models_routes, "install", blocking_install)

    first_thread = threading.Thread(
        target=lambda: client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    )
    first_thread.start()
    assert started.wait(timeout=5)

    second = client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    assert second.status_code == 409

    entry = _small_catalog()[0]
    install_dir = install_dir_for(entry, tmp_path)
    delete_response = client.delete(f"/models/{_MODEL_ID}", headers=_auth_headers())
    assert delete_response.status_code == 200
    assert not install_dir.exists()  # DELETE while DOWNLOADING never touches the filesystem itself

    proceed.set()
    first_thread.join(timeout=5)

    for _ in range(50):
        status = client.get("/models", headers=_auth_headers()).json()[0]["status"]
        if status == "not_installed":
            break
        import time

        time.sleep(0.05)
    else:
        pytest.fail("background thread never settled to not_installed")


def test_get_models_reports_progress_and_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def progress_install(entry: ModelCatalogEntry, data_dir: Path, **kwargs: object) -> None:
        on_file_verified = kwargs["on_file_verified"]
        on_file_verified(1, 3)  # type: ignore[operator]
        release.wait(timeout=5)
        raise RuntimeError("boom")

    monkeypatch.setattr(models_routes, "install", progress_install)

    client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())

    in_progress = client.get("/models", headers=_auth_headers()).json()[0]
    assert in_progress["files_done"] == 1
    assert in_progress["files_total"] == 3

    release.set()
    for _ in range(50):
        failed = client.get("/models", headers=_auth_headers()).json()[0]
        if failed["status"] == "verification_failed":
            assert failed["error"] is not None
            break
        import time

        time.sleep(0.05)
    else:
        pytest.fail("background thread never reached verification_failed")


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/capabilities"),
        ("get", "/models"),
        ("post", f"/models/{_MODEL_ID}/download"),
        ("delete", f"/models/{_MODEL_ID}"),
    ],
)
def test_routes_require_a_token(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)

    assert response.status_code == 401


def test_checksum_failure_never_reaches_installed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "invoice_renamer.models.installer._default_fetch",
        lambda repository, revision, filename, dest_dir: (
            (dest_dir / filename).write_bytes(b"wrong") and (dest_dir / filename)
        ),
    )

    client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())

    for _ in range(50):
        status = client.get("/models", headers=_auth_headers()).json()[0]
        if status["status"] != "downloading":
            break
        import time

        time.sleep(0.05)

    assert status["status"] == "verification_failed"
    entry = _small_catalog()[0]
    install_dir = install_dir_for(entry, tmp_path)
    assert not (install_dir / "model.bin").exists()


def test_delete_after_verification_failure_clears_state_and_files(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _small_catalog()[0]
    install_dir = install_dir_for(entry, tmp_path)

    def failing_install(entry: ModelCatalogEntry, data_dir: Path, **kwargs: object) -> None:
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "model.bin").write_bytes(b"partial")
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(models_routes, "install", failing_install)

    client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    for _ in range(50):
        status = client.get("/models", headers=_auth_headers()).json()[0]["status"]
        if status == "verification_failed":
            break
        import time

        time.sleep(0.05)

    response = client.delete(f"/models/{_MODEL_ID}", headers=_auth_headers())
    assert response.status_code == 200
    assert not install_dir.exists()

    after = client.get("/models", headers=_auth_headers()).json()[0]
    assert after["status"] == "not_installed"


def test_path_traversal_file_never_writes_outside_tmp_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    traversal_catalog = [
        ModelCatalogEntry(
            id=_MODEL_ID,
            display_name="Tiny Model",
            license="apache-2.0",
            repository="example-org/tiny-model",
            revision="abc123",
            memory_tier=MemoryTier.SMALL,
            files=[ModelFile(path="../../escape.bin", sha256="a" * 64, size_bytes=0)],
        )
    ]
    monkeypatch.setattr(models_routes, "SHORTLISTED_CATALOG", traversal_catalog)

    client.post(f"/models/{_MODEL_ID}/download", headers=_auth_headers())
    for _ in range(50):
        status = client.get("/models", headers=_auth_headers()).json()[0]["status"]
        if status == "verification_failed":
            break
        import time

        time.sleep(0.05)

    assert status == "verification_failed"
    assert not (tmp_path / "escape.bin").exists()
    assert not (tmp_path.parent / "escape.bin").exists()


def test_get_models_reports_active_coordinator_status_over_filesystem_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _small_catalog()[0]
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    (install_dir / "model.bin").write_bytes(_FILE_CONTENT)
    import json

    from invoice_renamer.models.installer import _marker_payload

    (install_dir / ".installed.json").write_text(json.dumps(_marker_payload(entry)))

    coordinator = client.app.state.model_install_coordinator  # type: ignore[attr-defined]
    # Injected directly (not via start(), which would itself refuse to start
    # a download for a model the filesystem already reports as installed) -
    # the point of this test is GET /models' status-source ordering, not the
    # download lifecycle.
    coordinator._states[entry.id] = models_routes.DownloadState(
        status=models_routes.InstallStatus.DOWNLOADING
    )

    status = client.get("/models", headers=_auth_headers()).json()[0]["status"]

    assert status == "downloading"
