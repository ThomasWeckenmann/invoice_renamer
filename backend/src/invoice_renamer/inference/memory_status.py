"""Live memory sampling: system, worker-process, and a qualitative
GPU-in-use flag. System/worker readings are independently injectable so
tests can simulate different hosts without touching real hardware; GPU use
is derived directly from the already-confirmed runtime device rather than
queried live.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import psutil
from pydantic import BaseModel


class MemorySnapshot(BaseModel):
    sampled_at: float
    system_total_bytes: int
    system_available_bytes: int
    worker_rss_bytes: int
    # None until a model has actually loaded onto a device - no accelerator
    # is probed or initialized before then.
    runtime_device: str | None
    # The currently cached model, or None if nothing is resident.
    loaded_entry_id: str | None = None
    # True only for the duration of an in-flight load (first load or a
    # switch) - distinct from "nothing loaded" and "something loaded".
    loading: bool = False
    loading_entry_id: str | None = None
    # True once a model is loaded onto the GPU (runtime_device == "gpu").
    # llama.cpp exposes one build-wide offload flag, not a per-allocation
    # byte counter the way torch's CUDA/MPS backends did, so this is a
    # qualitative "GPU is being used" signal rather than a memory figure.
    gpu_in_use: bool = False


def _system_memory() -> tuple[int, int]:
    vm = psutil.virtual_memory()
    return vm.total, vm.available


def _worker_rss() -> int:
    return psutil.Process().memory_info().rss


def sample_memory(
    *,
    runtime_device: str | None,
    loaded_entry_id: str | None = None,
    loading: bool = False,
    loading_entry_id: str | None = None,
    system_memory_fn: Callable[[], tuple[int, int]] = _system_memory,
    worker_rss_fn: Callable[[], int] = _worker_rss,
) -> MemorySnapshot:
    system_total, system_available = system_memory_fn()
    worker_rss = worker_rss_fn()

    return MemorySnapshot(
        sampled_at=time.time(),
        system_total_bytes=system_total,
        system_available_bytes=system_available,
        worker_rss_bytes=worker_rss,
        runtime_device=runtime_device,
        loaded_entry_id=loaded_entry_id,
        loading=loading,
        loading_entry_id=loading_entry_id,
        gpu_in_use=runtime_device == "gpu",
    )
