"""Caches the one currently-loaded local model so repeated jobs against the
same model don't reload multi-GB weights per request.
"""

from __future__ import annotations

import gc
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry

if TYPE_CHECKING:
    from invoice_renamer.inference.transformers_extractor import TransformersExtractor


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Small immutable copy of ModelRuntime's state for a request handler (e.g.
    the memory sampler) to read without touching the extractor itself or
    taking any lock."""

    loaded_entry_id: str | None
    device: str | None


_DEVICE_BY_BACKEND = {
    AccelerationBackend.MPS: "mps",
    AccelerationBackend.CUDA: "cuda",
    # ROCm-enabled PyTorch builds expose AMD GPUs through the same torch.cuda
    # device namespace, so "cuda" is the correct device string here too.
    AccelerationBackend.ROCM: "cuda",
    AccelerationBackend.CPU: "cpu",
}


def select_device(capabilities: SystemCapabilities) -> str:
    """Maps detected capabilities to a torch device string, confirmed against
    torch itself before it is used.

    Capability detection is deliberately torch-free so listing models stays
    cheap at startup, which means it can only infer an accelerator from the
    host (an `nvidia-smi` on PATH, Apple Silicon). That says nothing about the
    installed torch wheel: a CPU-only build on a CUDA host would otherwise be
    handed "cuda" here and fail at load. This runs immediately before loading a
    model, where torch is imported anyway, so asking it directly costs nothing
    and downgrading to CPU is always safe.
    """
    device = _DEVICE_BY_BACKEND[capabilities.acceleration]
    if device == "cpu":
        return device

    import torch

    if device == "cuda" and torch.cuda.is_available():
        return device
    if device == "mps" and torch.backends.mps.is_available():
        return device
    return "cpu"


class LoadInstalledFn(Protocol):
    """Callable[[...], ...] can't express a keyword-only parameter - under mypy
    strict mode, a value typed as Callable[[ModelCatalogEntry, Path, str], R] may
    only be called positionally, so calling it as load_installed(entry, data_dir,
    device=device) (matching load_installed()'s own keyword-only `device`) would
    itself fail type-checking. A callable Protocol describes the real call shape
    instead."""

    def __call__(
        self, entry: ModelCatalogEntry, data_dir: Path, *, device: str
    ) -> TransformersExtractor: ...


class ModelRuntime:
    """Not thread-safe by design: only the analysis worker thread (one thread,
    one job at a time) ever calls get_or_load(), so no internal lock is
    needed. loaded_entry_id() and snapshot() are the exceptions - plain
    attribute reads safe to call from another thread (e.g. a request handler
    sampling live memory) since a snapshot that's a call away from stale is
    already the expected shape of that kind of check."""

    def __init__(self, *, load_installed: LoadInstalledFn | None = None) -> None:
        self._loaded: tuple[str, str | None] | None = None
        self._extractor: TransformersExtractor | None = None
        self._device: str | None = None
        # Lazily resolved (not a bound default argument) so tests can
        # monkeypatch TransformersExtractor.load_installed after construction,
        # same pattern as installer.py's FetchFn.
        self._load_installed = load_installed

    def loaded_entry_id(self) -> str | None:
        """The id of the currently cached model, or None if nothing is loaded."""
        return self._loaded[0] if self._loaded is not None else None

    def snapshot(self) -> RuntimeSnapshot:
        """Small immutable copy of what's currently loaded, safe to read from
        another thread (see class docstring)."""
        return RuntimeSnapshot(loaded_entry_id=self.loaded_entry_id(), device=self._device)

    def get_or_load(
        self, entry: ModelCatalogEntry, data_dir: Path, device: str
    ) -> TransformersExtractor:
        key = (entry.id, entry.revision)
        if self._loaded == key and self._extractor is not None:
            return self._extractor
        self._unload_current()
        load_installed = self._load_installed
        if load_installed is None:
            # Import the inference stack only when an analysis first needs a model.
            from invoice_renamer.inference.transformers_extractor import TransformersExtractor

            load_installed = TransformersExtractor.load_installed
        self._extractor = load_installed(entry, data_dir, device=device)
        self._loaded = key
        self._device = device
        return self._extractor

    def _unload_current(self) -> None:
        if self._extractor is None:
            return
        import torch

        del self._extractor
        self._extractor = None
        self._loaded = None
        self._device = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
