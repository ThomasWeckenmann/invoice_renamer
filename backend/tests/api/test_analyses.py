"""Tests for POST /analyses, GET /jobs/{id}, and DELETE /jobs/{id}."""

import json
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoice_renamer.api import analyses_routes
from invoice_renamer.api.app import create_app
from invoice_renamer.inference.transformers_extractor import TransformersExtractor
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile, ModelKind
from invoice_renamer.models.installer import _marker_payload, install_dir_for

TOKEN = "test-session-token"
FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"

_MODEL_A = "model-a"
_MODEL_B = "model-b"

_VALID_PDF = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
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


def _entry(model_id: str) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=model_id,
        display_name=model_id,
        kind=ModelKind.OPEN_LOCAL,
        license="apache-2.0",
        repository=f"example-org/{model_id}",
        revision="a" * 40,
        memory_tier=MemoryTier.SMALL,
        files=[ModelFile(path="model.bin", sha256="a" * 64, size_bytes=1)],
    )


def _catalog() -> list[ModelCatalogEntry]:
    return [_entry(_MODEL_A), _entry(_MODEL_B)]


def _install(entry: ModelCatalogEntry, data_dir: Path) -> None:
    install_dir = install_dir_for(entry, data_dir)
    install_dir.mkdir(parents=True, exist_ok=True)
    for file in entry.files:
        (install_dir / file.path).write_bytes(b"x" * file.size_bytes)
    (install_dir / ".installed.json").write_text(json.dumps(_marker_payload(entry)))


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _submit(client: TestClient, model_id: str, pdf_bytes: bytes = _VALID_PDF) -> dict[str, object]:
    response = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": model_id},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    return response.json()


def _poll_until(
    client: TestClient, job_id: str, *, terminal_statuses: tuple[str, ...], timeout: float = 5.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}", headers=_auth_headers()).json()
        if body["status"] in terminal_statuses:
            return body
        time.sleep(0.02)
    pytest.fail(f"job {job_id} never reached one of {terminal_statuses}")


class _FakeExtractor:
    """Stands in for TransformersExtractor: implements only LanguageModel's
    generate(), scripted to return a fixed response and optionally block on a
    threading.Event first, to control worker-thread timing from a test.
    `calls`, when given, records one entry per generate() call - the precise
    signal for whether a cancelled job's pipeline actually ran, since
    ModelRuntime's cache means the *loader* is only called once regardless of
    how many jobs share a model."""

    def __init__(
        self,
        response: str,
        *,
        block: threading.Event | None = None,
        calls: list[str] | None = None,
    ) -> None:
        self._response = response
        self._block = block
        self._calls = calls

    def generate(self, prompt: str) -> str:
        if self._calls is not None:
            self._calls.append(prompt)
        if self._block is not None:
            self._block.wait(timeout=5)
        return self._response


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("INVOICE_RENAMER_SESSION_TOKEN", TOKEN)
    monkeypatch.setenv("INVOICE_RENAMER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(analyses_routes, "SHORTLISTED_CATALOG", _catalog())
    return TestClient(create_app())


def test_happy_path_end_to_end(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE),
    )

    submitted = _submit(client, _MODEL_A)
    assert submitted["status"] == "queued"

    completed = _poll_until(client, submitted["id"], terminal_statuses=("completed", "failed"))

    assert completed["status"] == "completed"
    assert completed["proposal"]["proposed_filename"] == "2026-09-12_Apple_MacBook-Air_2180-EUR.pdf"
    assert completed["metrics"]["pages_total"] == 1
    assert completed["error"] is None


def test_known_but_not_installed_model_is_422(client: TestClient) -> None:
    response = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": _MODEL_A},
        files={"file": ("invoice.pdf", _VALID_PDF, "application/pdf")},
    )

    assert response.status_code == 422


def test_unknown_model_id_is_404(client: TestClient) -> None:
    response = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": "does-not-exist"},
        files={"file": ("invoice.pdf", _VALID_PDF, "application/pdf")},
    )

    assert response.status_code == 404


def test_non_pdf_upload_is_422_and_never_creates_a_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: calls.append(1) or _FakeExtractor(_VALID_MODEL_RESPONSE),
    )

    response = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": _MODEL_A},
        files={"file": ("invoice.pdf", b"not a pdf at all", "application/pdf")},
    )

    assert response.status_code == 422
    assert calls == []


def test_corrupt_pdf_shaped_upload_ends_the_job_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE),
    )
    corrupt_pdf = b"%PDF-1.7\n" + b"garbage not a real pdf structure" * 20

    submitted = _submit(client, _MODEL_A, corrupt_pdf)
    failed = _poll_until(client, submitted["id"], terminal_statuses=("completed", "failed"))

    assert failed["status"] == "failed"
    assert failed["error"]
    assert failed["proposal"] is None


def test_queued_jobs_run_in_submission_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    block = threading.Event()
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE, block=block),
    )

    first = _submit(client, _MODEL_A)
    second = _submit(client, _MODEL_A)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (
            client.get(f"/jobs/{first['id']}", headers=_auth_headers()).json()["status"]
            == "running"
        ):
            break
        time.sleep(0.02)
    else:
        pytest.fail("first job never started running")

    assert client.get(f"/jobs/{second['id']}", headers=_auth_headers()).json()["status"] == "queued"

    block.set()

    assert (
        _poll_until(client, first["id"], terminal_statuses=("completed",))["status"] == "completed"
    )
    assert (
        _poll_until(client, second["id"], terminal_statuses=("completed",))["status"] == "completed"
    )


def test_cancel_a_queued_job_marks_it_cancelled_without_ever_running_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    block = threading.Event()
    generate_calls: list[str] = []

    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor(_VALID_MODEL_RESPONSE, block=block, calls=generate_calls)

    monkeypatch.setattr(TransformersExtractor, "load_installed", fake_loader)

    first = _submit(client, _MODEL_A)  # occupies the worker, blocked
    second = _submit(client, _MODEL_A)  # stays queued

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(generate_calls) < 1:
        time.sleep(0.02)

    cancelled = client.delete(f"/jobs/{second['id']}", headers=_auth_headers())
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    block.set()
    _poll_until(client, first["id"], terminal_statuses=("completed",))

    # The cancelled job's own generate() call never happened - only the first
    # job's did. ModelRuntime's cache would hide this if we only checked the
    # loader, since both jobs share the same cached model.
    assert len(generate_calls) == 1
    assert (
        client.get(f"/jobs/{second['id']}", headers=_auth_headers()).json()["status"] == "cancelled"
    )


def test_cancel_a_running_job_settles_cancelled_not_completed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    block = threading.Event()
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE, block=block),
    )

    job = _submit(client, _MODEL_A)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get(f"/jobs/{job['id']}", headers=_auth_headers()).json()["status"] == "running":
            break
        time.sleep(0.02)
    else:
        pytest.fail("job never started running")

    cancel_response = client.delete(f"/jobs/{job['id']}", headers=_auth_headers())
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "running"  # still running until generate() returns

    block.set()

    settled = _poll_until(client, job["id"], terminal_statuses=("cancelled", "completed"))
    assert settled["status"] == "cancelled"


def test_switching_models_frees_the_first_extractor_before_the_second_loads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry_a = _entry(_MODEL_A)
    entry_b = _entry(_MODEL_B)
    _install(entry_a, tmp_path)
    _install(entry_b, tmp_path)

    state: dict[str, object] = {}

    def loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        if entry.id == _MODEL_A:
            extractor = _FakeExtractor(_VALID_MODEL_RESPONSE)
            state["ref"] = weakref.ref(extractor)
            return extractor
        assert state["ref"]() is None, "model A must be released before model B loads"  # type: ignore[operator]
        return _FakeExtractor(_VALID_MODEL_RESPONSE)

    monkeypatch.setattr(TransformersExtractor, "load_installed", loader)

    job_a = _submit(client, _MODEL_A)
    _poll_until(client, job_a["id"], terminal_statuses=("completed",))

    job_b = _submit(client, _MODEL_B)
    completed_b = _poll_until(client, job_b["id"], terminal_statuses=("completed", "failed"))

    assert completed_b["status"] == "completed"


def test_pending_bytes_cap_returns_429_until_jobs_drain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    block = threading.Event()
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE, block=block),
    )
    monkeypatch.setattr(analyses_routes, "_MAX_PENDING_BYTES", len(_VALID_PDF))

    first = _submit(client, _MODEL_A)  # fills the cap exactly, starts running (blocked)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (
            client.get(f"/jobs/{first['id']}", headers=_auth_headers()).json()["status"]
            == "running"
        ):
            break
        time.sleep(0.02)

    over_cap = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": _MODEL_A},
        files={"file": ("invoice.pdf", _VALID_PDF, "application/pdf")},
    )
    assert over_cap.status_code == 429

    block.set()
    _poll_until(client, first["id"], terminal_statuses=("completed",))

    after_drain = _submit(client, _MODEL_A)
    assert after_drain["status"] == "queued"
    # Drained before the test returns: the fixture's monkeypatches (the fake
    # catalog, the fake loader) are undone at teardown, but the coordinator's
    # worker thread is a daemon that outlives the test - an undrained job
    # would run later against the real catalog/loader and blow up on another
    # thread, surfacing as a flaky failure in whatever test happens to be
    # running at that point.
    _poll_until(client, after_drain["id"], terminal_statuses=("completed",))


def test_pending_bytes_cap_is_race_free_under_concurrent_submissions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    block = threading.Event()
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE, block=block),
    )
    # Room for exactly 3 concurrent submissions.
    monkeypatch.setattr(analyses_routes, "_MAX_PENDING_BYTES", len(_VALID_PDF) * 3)

    def attempt() -> tuple[int, str | None]:
        response = client.post(
            "/analyses",
            headers=_auth_headers(),
            data={"model_id": _MODEL_A},
            files={"file": ("invoice.pdf", _VALID_PDF, "application/pdf")},
        )
        job_id = response.json()["id"] if response.status_code == 202 else None
        return response.status_code, job_id

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: attempt(), range(10)))

    status_codes = [status_code for status_code, _ in results]
    assert status_codes.count(202) == 3
    assert status_codes.count(429) == 7

    block.set()
    # Drained before the test returns - see the comment in the sibling cap
    # test for why an undrained job here would be a latent flake elsewhere.
    for status_code, job_id in results:
        if status_code == 202:
            assert job_id is not None
            _poll_until(client, job_id, terminal_statuses=("completed",))


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/analyses"),
        ("get", "/jobs/some-id"),
        ("delete", "/jobs/some-id"),
    ],
)
def test_routes_require_a_token(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)

    assert response.status_code == 401


def test_upload_past_the_size_limit_is_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    monkeypatch.setattr(analyses_routes, "_MAX_UPLOAD_BYTES", 10)

    response = client.post(
        "/analyses",
        headers=_auth_headers(),
        data={"model_id": _MODEL_A},
        files={"file": ("invoice.pdf", _VALID_PDF, "application/pdf")},
    )

    assert response.status_code == 413


def test_terminal_job_drops_its_pdf_bytes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(_MODEL_A)
    _install(entry, tmp_path)
    monkeypatch.setattr(
        TransformersExtractor,
        "load_installed",
        lambda entry, data_dir, *, device: _FakeExtractor(_VALID_MODEL_RESPONSE),
    )

    submitted = _submit(client, _MODEL_A)
    _poll_until(client, submitted["id"], terminal_statuses=("completed", "failed"))

    coordinator: analyses_routes.AnalysisCoordinator = client.app.state.analysis_coordinator  # type: ignore[attr-defined]
    assert coordinator.get(submitted["id"]).pdf_bytes is None
