"""Tests for ModelRuntime's single-slot cache and its unload-before-load ordering."""

import threading
import time
import weakref
from pathlib import Path

import pytest

from invoice_renamer.inference.llamacpp_extractor import LlamaCppExtractor
from invoice_renamer.inference.runtime import ModelRuntime, select_device
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
    """Stands in for LlamaCppExtractor - a distinct object per load so a
    weakref can prove whether the earlier instance was actually released."""

    def close(self) -> None:
        pass


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


def test_snapshot_is_empty_until_something_is_loaded(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]

    snapshot = runtime.snapshot()

    assert snapshot.loaded_entry_id is None
    assert snapshot.device is None
    assert snapshot.loading is False
    assert snapshot.loading_entry_id is None


def test_snapshot_reports_the_loaded_entry_and_device(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    runtime.get_or_load(_entry("model-a"), tmp_path, "mps")

    snapshot = runtime.snapshot()

    assert snapshot.loaded_entry_id == "model-a"
    assert snapshot.device == "mps"


def test_snapshot_reflects_a_switch_to_a_different_model_and_device(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    runtime.get_or_load(_entry("model-a"), tmp_path, "mps")

    runtime.get_or_load(_entry("model-b"), tmp_path, "cpu")

    snapshot = runtime.snapshot()
    assert snapshot.loaded_entry_id == "model-b"
    assert snapshot.device == "cpu"


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


def test_loading_is_true_only_for_the_duration_of_the_load_call(tmp_path: Path) -> None:
    observed: list[tuple[bool, str | None]] = []
    release = threading.Event()

    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        observed.append((True, entry.id))  # loading must already be true here
        release.wait(timeout=5)
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    assert runtime.snapshot().loading is False

    thread = threading.Thread(target=runtime.get_or_load, args=(_entry("model-a"), tmp_path, "cpu"))
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not runtime.snapshot().loading:
        time.sleep(0.01)

    snapshot = runtime.snapshot()
    assert snapshot.loading is True
    assert snapshot.loading_entry_id == "model-a"

    release.set()
    thread.join(timeout=5)

    final = runtime.snapshot()
    assert final.loading is False
    assert final.loading_entry_id is None
    assert final.loaded_entry_id == "model-a"


def test_loading_clears_after_a_failed_load(tmp_path: Path) -> None:
    def failing_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        raise RuntimeError("boom")

    runtime = ModelRuntime(load_installed=failing_loader)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        runtime.get_or_load(_entry("model-a"), tmp_path, "cpu")

    snapshot = runtime.snapshot()
    assert snapshot.loading is False
    assert snapshot.loading_entry_id is None
    assert snapshot.loaded_entry_id is None


def test_unload_clears_the_loaded_model(tmp_path: Path) -> None:
    def fake_loader(entry: ModelCatalogEntry, data_dir: Path, *, device: str) -> _FakeExtractor:
        return _FakeExtractor()

    runtime = ModelRuntime(load_installed=fake_loader)  # type: ignore[arg-type]
    runtime.get_or_load(_entry("model-a"), tmp_path, "cpu")
    assert runtime.loaded_entry_id() == "model-a"

    runtime.unload()

    assert runtime.loaded_entry_id() is None
    assert runtime.snapshot().device is None


def test_unload_is_a_no_op_when_nothing_is_loaded(tmp_path: Path) -> None:
    runtime = ModelRuntime(load_installed=lambda entry, data_dir, *, device: _FakeExtractor())  # type: ignore[arg-type]

    runtime.unload()  # must not raise

    assert runtime.loaded_entry_id() is None


def test_default_loader_resolves_to_llamacpp_extractor_load_installed(
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

    monkeypatch.setattr(LlamaCppExtractor, "load_installed", fake_load_installed)

    runtime = ModelRuntime()
    entry = _entry()
    result = runtime.get_or_load(entry, tmp_path, "cpu")

    assert isinstance(result, _FakeExtractor)
    assert captured == {"entry": entry, "data_dir": tmp_path, "device": "cpu"}


def _capabilities(backend: AccelerationBackend) -> SystemCapabilities:
    return SystemCapabilities(acceleration=backend, memory_gb=16, free_disk_gb=100)


@pytest.mark.parametrize(
    "backend",
    [AccelerationBackend.MPS, AccelerationBackend.CUDA, AccelerationBackend.ROCM],
)
def test_select_device_returns_gpu_when_the_installed_binding_supports_offload(
    backend: AccelerationBackend,
) -> None:
    assert select_device(_capabilities(backend), supports_gpu_offload_fn=lambda: True) == "gpu"


@pytest.mark.parametrize(
    "backend",
    [AccelerationBackend.MPS, AccelerationBackend.CUDA, AccelerationBackend.ROCM],
)
def test_select_device_downgrades_to_cpu_when_the_binding_reports_no_gpu_offload_support(
    backend: AccelerationBackend,
) -> None:
    """Detection only sees the host's hardware, so an installed llama.cpp
    build with no GPU support compiled in, on a machine with an accelerator
    present, must not be handed a device it would then fail to load onto."""
    assert select_device(_capabilities(backend), supports_gpu_offload_fn=lambda: False) == "cpu"


def test_select_device_does_not_consult_the_binding_for_a_cpu_host() -> None:
    """A CPU capability needs no confirmation - checking anyway would import
    llama_cpp on hosts that never load a model."""

    def fail() -> bool:
        raise AssertionError("GPU offload support must not be probed for a CPU host")

    assert (
        select_device(_capabilities(AccelerationBackend.CPU), supports_gpu_offload_fn=fail) == "cpu"
    )


def test_select_device_defaults_to_the_real_llama_cpp_binding_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an injected fn, select_device must actually consult the
    installed llama_cpp binding rather than silently defaulting to one
    fixed answer."""
    from invoice_renamer.inference import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_llama_cpp_supports_gpu_offload", lambda: True)

    assert select_device(_capabilities(AccelerationBackend.CUDA)) == "gpu"
