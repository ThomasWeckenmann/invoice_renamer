"""Live memory sampling: system, worker-process, and (only once an
accelerator is actually initialized by a loaded model) GPU counters. Every
provider is independently injectable so tests can simulate CPU/MPS/CUDA/ROCm
hosts and partial failures without touching real hardware.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import psutil
from pydantic import BaseModel


class GpuMemorySnapshot(BaseModel):
    backend: str  # "mps", "cuda", or "rocm"
    # Populated for CUDA/ROCm; left None for MPS, which exposes no separate
    # reserved-vs-allocated split.
    allocated_bytes: int | None = None
    reserved_bytes: int | None = None
    # Populated for MPS only: the Metal driver's total allocation, including
    # its internal caches - not directly comparable to CUDA's allocated_bytes.
    driver_allocated_bytes: int | None = None


class MemorySnapshot(BaseModel):
    sampled_at: float
    system_total_bytes: int
    system_available_bytes: int
    worker_rss_bytes: int
    # None until a model has actually loaded onto a device - no accelerator
    # is probed or initialized before then.
    runtime_device: str | None
    gpu: GpuMemorySnapshot | None
    # Set when a GPU provider raised, without failing the rest of the snapshot.
    gpu_error: str | None = None


def _system_memory() -> tuple[int, int]:
    vm = psutil.virtual_memory()
    return vm.total, vm.available


def _worker_rss() -> int:
    return psutil.Process().memory_info().rss


def _gpu_memory(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
    """Only ever called for a device a model has already loaded onto - never
    probes or initializes an accelerator that's merely detected as present."""
    import torch

    if device == "mps":
        if not torch.backends.mps.is_available():
            return None, None
        return (
            GpuMemorySnapshot(
                backend="mps", driver_allocated_bytes=torch.mps.driver_allocated_memory()
            ),
            None,
        )
    if device == "cuda":
        if not torch.cuda.is_available():
            return None, None
        # A ROCm-enabled torch build reports itself through torch.version.hip
        # while still using the torch.cuda namespace (see runtime.py).
        backend = "rocm" if getattr(torch.version, "hip", None) else "cuda"
        return (
            GpuMemorySnapshot(
                backend=backend,
                allocated_bytes=torch.cuda.memory_allocated(),
                reserved_bytes=torch.cuda.memory_reserved(),
            ),
            None,
        )
    return None, None


def sample_memory(
    *,
    runtime_device: str | None,
    system_memory_fn: Callable[[], tuple[int, int]] = _system_memory,
    worker_rss_fn: Callable[[], int] = _worker_rss,
    gpu_memory_fn: Callable[[str], tuple[GpuMemorySnapshot | None, str | None]] = _gpu_memory,
) -> MemorySnapshot:
    system_total, system_available = system_memory_fn()
    worker_rss = worker_rss_fn()

    gpu: GpuMemorySnapshot | None = None
    gpu_error: str | None = None
    if runtime_device is not None and runtime_device != "cpu":
        try:
            gpu, gpu_error = gpu_memory_fn(runtime_device)
        except Exception as exc:
            # A provider failing (e.g. a backend rejecting a query mid-unload)
            # must not take down the rest of an otherwise-good snapshot.
            gpu, gpu_error = None, str(exc)

    return MemorySnapshot(
        sampled_at=time.time(),
        system_total_bytes=system_total,
        system_available_bytes=system_available,
        worker_rss_bytes=worker_rss,
        runtime_device=runtime_device,
        gpu=gpu,
        gpu_error=gpu_error,
    )
