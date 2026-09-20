"""Tests for GET /memory: auth, shape before/after a model loads, and that a
request completes even while the worker thread is blocked inside inference.
"""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoice_renamer.api import analyses_routes
from invoice_renamer.api.app import create_app
from invoice_renamer.inference.transformers_extractor import TransformersExtractor
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.installer import _marker_payload, install_dir_for

TOKEN = "test-session-token"
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


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


class _FakeExtractor:
    def __init__(self, response: str, *, block: threading.Event | None = None) -> None:
        self._response = response
        self._block = block

    def generate(self, prompt: str) -> str:
        if self._block is not None:
            self._block.wait(timeout=5)
        return self._response


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("INVOICE_RENAMER_SESSION_TOKEN", TOKEN)
    monkeypatch.setenv("INVOICE_RENAMER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", [_entry()])
    return TestClient(create_app())


def test_requires_a_token(client: TestClient) -> None:
    response = client.get("/memory")

    assert response.status_code == 401


def test_shape_before_any_model_has_loaded(client: TestClient) -> None:
    response = client.get("/memory", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_device"] is None
    assert body["gpu"] is None
    assert body["gpu_error"] is None
    assert body["system_total_bytes"] > 0
    assert body["worker_rss_bytes"] > 0
    assert body["sampled_at"] > 0


def test_reports_the_runtime_device_once_a_model_has_loaded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry()
    _install(entry, tmp_path)
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE),
    )

    submitted = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": _MODEL_A},
        files={"file": ("invoice.pdf", _VALID_PDF, "application/pdf")},
    ).json()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{submitted['id']}", headers=_auth_headers()).json()["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(0.02)

    body = client.get("/memory", headers=_auth_headers()).json()
    assert body["runtime_device"] == "cpu"
    # No GPU on a CPU device: absent, not a measured zero.
    assert body["gpu"] is None


def test_returns_promptly_while_the_worker_is_blocked_inside_inference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry()
    _install(entry, tmp_path)
    block = threading.Event()
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE, block=block),
    )

    submitted = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": _MODEL_A},
        files={"file": ("invoice.pdf", _VALID_PDF, "application/pdf")},
    ).json()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (
            client.get(f"/jobs/{submitted['id']}", headers=_auth_headers()).json()["status"]
            == "running"
        ):
            break
        time.sleep(0.02)
    else:
        pytest.fail("job never started running")

    started = time.monotonic()
    response = client.get("/memory", headers=_auth_headers())
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 2.0  # never waits on the blocked generate() call

    block.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (
            client.get(f"/jobs/{submitted['id']}", headers=_auth_headers()).json()["status"]
            == "completed"
        ):
            break
        time.sleep(0.02)
