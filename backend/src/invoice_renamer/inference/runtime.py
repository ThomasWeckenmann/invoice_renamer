"""Caches the one currently-loaded local model so repeated jobs against the
same model don't reload multi-GB weights per request.
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry

if TYPE_CHECKING:
    from invoice_renamer.inference.transformers_extractor import TransformersExtractor

_DEVICE_BY_BACKEND = {
    AccelerationBackend.MPS: "mps",
    AccelerationBackend.CUDA: "cuda",
    # ROCm-enabled PyTorch builds expose AMD GPUs through the same torch.cuda
    # device namespace, so "cuda" is the correct device string here too.
    AccelerationBackend.ROCM: "cuda",
    AccelerationBackend.CPU: "cpu",
}


def select_device(capabilities: SystemCapabilities) -> str:
    return _DEVICE_BY_BACKEND[capabilities.acceleration]


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
    one job at a time) ever calls get_or_load(), so no internal lock is needed."""

    def __init__(self, *, load_installed: LoadInstalledFn | None = None) -> None:
        self._loaded: tuple[str, str | None] | None = None
        self._extractor: TransformersExtractor | None = None
        # Lazily resolved (not a bound default argument) so tests can
        # monkeypatch TransformersExtractor.load_installed after construction,
        # same pattern as installer.py's FetchFn.
        self._load_installed = load_installed

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
        return self._extractor

    def _unload_current(self) -> None:
        if self._extractor is None:
            return
        import torch

        del self._extractor
        self._extractor = None
        self._loaded = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
