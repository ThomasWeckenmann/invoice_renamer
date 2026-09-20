"""Tests for ModelRuntime's single-slot cache and its unload-before-load ordering."""

import weakref
from pathlib import Path

import pytest
import torch

from invoice_renamer.inference.runtime import ModelRuntime, select_device
from invoice_renamer.inference.transformers_extractor import TransformersExtractor
from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile


def _entry(model_id: str = "model-a", revision: str = "a" * 40) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=model_id,
        display_name=model_id,
        license="apache-2.0",
        repository=f"example-org/{model_id}",
        revision=revision,
        memory_tier=MemoryTier.SMALL,
        files=[ModelFile(path="model.bin", sha256="a" * 64, size_bytes=1)],
    )


class _FakeExtractor:
    """Stands in for TransformersExtractor - a distinct object per load so a
    weakref can prove whether the earlier instance was actually released."""


def test_get_or_load_caches_on_repeated_calls_with_the_same_key(tmp_path: Path) -> None:
    calls: list[tuple[ModelCatalogEntry, Path, str]] = []

    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        calls.append((entry, data_dir, device))
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    entry = _entry()

    first = runtime.get_or_load(entry, tmp_path, "cpu")
    second = runtime.get_or_load(entry, tmp_path, "cpu")

    assert first is second
    assert len(calls) == 1


def test_loaded_entry_id_is_none_until_something_is_loaded(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    assert runtime.loaded_entry_id() is None

    runtime.get_or_load(_entry("model-a"), tmp_path, "cpu")
    assert runtime.loaded_entry_id() == "model-a"


def test_is_warmed_starts_false_and_only_becomes_true_when_marked(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    assert runtime.is_warmed() is False

    runtime.get_or_load(_entry("model-a"), tmp_path, "cpu")
    assert runtime.is_warmed() is False  # loaded, but no generate() call has completed yet

    runtime.mark_warmed()
    assert runtime.is_warmed() is True


def test_is_warmed_survives_reuse_of_the_same_cached_model(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    entry = _entry("model-a")
    runtime.get_or_load(entry, tmp_path, "cpu")
    runtime.mark_warmed()

    runtime.get_or_load(entry, tmp_path, "cpu")  # cache hit, same key
    assert runtime.is_warmed() is True


def test_is_warmed_resets_when_a_different_model_loads(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    runtime.get_or_load(_entry("model-a"), tmp_path, "cpu")
    runtime.mark_warmed()

    runtime.get_or_load(_entry("model-b"), tmp_path, "cpu")
    assert runtime.is_warmed() is False


def test_get_or_load_with_a_different_entry_reloads(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]

    first = runtime.get_or_load(_entry("model-a"), tmp_path, "cpu")
    second = runtime.get_or_load(_entry("model-b"), tmp_path, "cpu")

    assert first is not second


def test_get_or_load_frees_the_previous_extractor_before_loading_the_next(tmp_path: Path) -> None:
    # first_extractor lives only as a local inside first_loader's own call
    # frame, so once that call returns, ModelRuntime's own self._extractor is
    # the *only* remaining strong reference - the precondition this test needs
    # to actually distinguish "A freed before B loads" from "A freed once the
    # whole call returns, B just happened to already be loaded".
    refs: dict[str, "weakref.ref[_FakeExtractor]"] = {}

    def first_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        first_extractor = _FakeExtractor()
        refs["first"] = weakref.ref(first_extractor)
        return first_extractor

    def second_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        # The point of this test: proven dead *during* the second load, not
        # merely by the time this call - or the whole test - returns.
        assert refs["first"]() is None, "previous extractor must be released before the next loads"
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=first_loader)  # type: ignore[arg-type]
    runtime.get_or_load(_entry("model-a"), tmp_path, "cpu")
    assert refs["first"]() is not None  # still alive - it's the cached, current model

    runtime._load_installed = second_loader  # type: ignore[assignment]
    runtime.get_or_load(_entry("model-b"), tmp_path, "cpu")


def test_get_or_load_calls_the_loader_with_device_as_a_keyword(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        captured["device"] = device
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    runtime.get_or_load(_entry(), tmp_path, "mps")

    assert captured["device"] == "mps"


def test_default_loader_resolves_to_transformers_extractor_load_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_load_installed(
        entry: ModelCatalogEntry, data_dir: Path, *, device: str
    ) -> _FakeExtractor:
        captured["entry"] = entry
        captured["data_dir"] = data_dir
        captured["device"] = device
        return _FakeExtractor()

    monkeypatch.setattr(TransformersExtractor, "load_installed", fake_load_installed)

    runtime = ModelRuntime()
    entry = _entry()
    result = runtime.get_or_load(entry, tmp_path, "cpu")

    assert isinstance(result, _FakeExtractor)
    assert captured == {"entry": entry, "data_dir": tmp_path, "device": "cpu"}


def _capabilities(backend: AccelerationBackend) -> SystemCapabilities:
    return SystemCapabilities(acceleration=backend, memory_gb=16, free_disk_gb=100)


def _set_torch_availability(monkeypatch: pytest.MonkeyPatch, *, cuda: bool, mps: bool) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: mps)


@pytest.mark.parametrize(
    "backend,expected_device",
    [
        (AccelerationBackend.MPS, "mps"),
        (AccelerationBackend.CUDA, "cuda"),
        (AccelerationBackend.ROCM, "cuda"),
        (AccelerationBackend.CPU, "cpu"),
    ],
)
def test_select_device_maps_every_backend_torch_supports(
    backend: AccelerationBackend, expected_device: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_torch_availability(monkeypatch, cuda=True, mps=True)

    assert select_device(_capabilities(backend)) == expected_device


@pytest.mark.parametrize(
    "backend",
    [AccelerationBackend.MPS, AccelerationBackend.CUDA, AccelerationBackend.ROCM],
)
def test_select_device_downgrades_to_cpu_when_torch_cannot_use_the_accelerator(
    backend: AccelerationBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detection only sees the host's hardware, so a CPU-only torch wheel on a
    machine with an accelerator present must not be handed a device it would
    then fail to load onto."""
    _set_torch_availability(monkeypatch, cuda=False, mps=False)

    assert select_device(_capabilities(backend)) == "cpu"


def test_select_device_does_not_consult_torch_for_a_cpu_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CPU capability needs no confirmation - checking anyway would import
    torch on hosts that never load a model."""

    def fail() -> bool:
        raise AssertionError("torch availability must not be probed for a CPU host")

    monkeypatch.setattr(torch.cuda, "is_available", fail)
    monkeypatch.setattr(torch.backends.mps, "is_available", fail)

    assert select_device(_capabilities(AccelerationBackend.CPU)) == "cpu"
