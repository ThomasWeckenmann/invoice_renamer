"""Caches the one currently-loaded local model so repeated jobs against the
same model don't reload multi-GB weights per request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry

if TYPE_CHECKING:
    from invoice_renamer.inference.llamacpp_extractor import LlamaCppExtractor


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Small immutable copy of ModelRuntime's state for a request handler (e.g.
    the memory sampler) to read without touching the extractor itself or
    taking any lock."""

    loaded_entry_id: str | None
    device: str | None
    loading: bool
    loading_entry_id: str | None


def _llama_cpp_supports_gpu_offload() -> bool:
    import llama_cpp

    return bool(llama_cpp.llama_supports_gpu_offload())


def select_device(
    capabilities: SystemCapabilities,
    *,
    supports_gpu_offload_fn: Callable[[], bool] | None = None,
) -> str:
    """Maps detected capabilities to a GPU-offload intent for llama.cpp
    ("cpu" or "gpu"), confirmed against the installed binding before it is
    used.

    Capability detection is deliberately binding-free so listing models stays
    cheap at startup, which means it can only infer whether *some*
    accelerator is present on the host (an `nvidia-smi` on PATH, Apple
    Silicon). That says nothing about whether the installed llama.cpp wheel
    was actually built with GPU support - a CPU-only build on a CUDA host
    would otherwise be handed "gpu" here and silently run on CPU anyway (or
    fail, depending on the build). This runs immediately before loading a
    model, where llama_cpp is imported anyway, so asking it directly costs
    nothing and downgrading to CPU is always safe. Unlike torch, llama.cpp
    exposes one build-wide offload flag rather than separate CUDA/MPS/ROCm
    availability checks - whichever accelerator the installed binding was
    compiled against is the one a "gpu" result will actually use.
    """
    if capabilities.acceleration is AccelerationBackend.CPU:
        return "cpu"

    supports_gpu_offload_fn = supports_gpu_offload_fn or _llama_cpp_supports_gpu_offload
    return "gpu" if supports_gpu_offload_fn() else "cpu"


class LoadInstalledFn(Protocol):
    """Callable[[...], ...] can't express a keyword-only parameter - under mypy
    strict mode, a value typed as Callable[[ModelCatalogEntry, Path, str], R] may
    only be called positionally, so calling it as load_installed(entry, data_dir,
    device=device) (matching load_installed()'s own keyword-only `device`) would
    itself fail type-checking. A callable Protocol describes the real call shape
    instead."""

    def __call__(
        self, entry: ModelCatalogEntry, data_dir: Path, *, device: str
    ) -> LlamaCppExtractor: ...


class ModelRuntime:
    """Not thread-safe by design: only the analysis worker thread (one thread,
    one job at a time) ever calls get_or_load() or unload(), so no internal
    lock is needed. loaded_entry_id() and snapshot() are the exceptions -
    plain attribute reads safe to call from another thread (e.g. a request
    handler sampling live memory) since a snapshot that's a call away from
    stale is already the expected shape of that kind of check."""

    def __init__(self, *, load_installed: LoadInstalledFn | None = None) -> None:
        self._loaded: tuple[str, str | None] | None = None
        self._extractor: LlamaCppExtractor | None = None
        self._device: str | None = None
        self._loading = False
        self._loading_entry_id: str | None = None
        # Lazily resolved (not a bound default argument) so tests can
        # monkeypatch LlamaCppExtractor.load_installed after construction,
        # same pattern as installer.py's FetchFn.
        self._load_installed = load_installed

    def loaded_entry_id(self) -> str | None:
        """The id of the currently cached model, or None if nothing is loaded."""
        return self._loaded[0] if self._loaded is not None else None

    def snapshot(self) -> RuntimeSnapshot:
        """Small immutable copy of what's currently loaded, safe to read from
        another thread (see class docstring)."""
        return RuntimeSnapshot(
            loaded_entry_id=self.loaded_entry_id(),
            device=self._device,
            loading=self._loading,
            loading_entry_id=self._loading_entry_id,
        )

    def get_or_load(
        self, entry: ModelCatalogEntry, data_dir: Path, device: str
    ) -> LlamaCppExtractor:
        key = (entry.id, entry.revision)
        if self._loaded == key and self._extractor is not None:
            return self._extractor
        self._unload_current()
        load_installed = self._load_installed
        if load_installed is None:
            # Import the inference stack only when an analysis first needs a model.
            from invoice_renamer.inference.llamacpp_extractor import LlamaCppExtractor

            load_installed = LlamaCppExtractor.load_installed
        self._loading, self._loading_entry_id = True, entry.id
        try:
            self._extractor = load_installed(entry, data_dir, device=device)
        finally:
            # Cleared on a raising load too, so a failed load never leaves the
            # status line stuck on "Loading..." forever.
            self._loading, self._loading_entry_id = False, None
        self._loaded = key
        self._device = device
        return self._extractor

    def unload(self) -> None:
        """Force-unload the current model without loading a replacement. A
        no-op when nothing is loaded."""
        self._unload_current()

    def _unload_current(self) -> None:
        if self._extractor is None:
            return
        extractor = self._extractor
        # Detach before closing so a cleanup failure cannot leave a partially
        # closed model cached or trigger repeated idle-unload attempts.
        self._extractor = None
        self._loaded = None
        self._device = None
        extractor.close()
