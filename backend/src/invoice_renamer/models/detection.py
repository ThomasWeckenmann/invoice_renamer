"""Detects the host's memory, free disk, and best-guess acceleration backend.

Acceleration detection is a torch-free heuristic: PyTorch isn't a dependency
yet, pending model selection. Once TransformersExtractor exists,
torch.backends.mps.is_available() / torch.cuda.is_available() should become
the authoritative check in place of the platform/binary sniffing here.
"""

import platform
import shutil
from pathlib import Path

import psutil

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities

_BYTES_PER_GB = 1024**3


def _detect_acceleration() -> AccelerationBackend:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return AccelerationBackend.MPS

    if shutil.which("nvidia-smi") is not None:
        return AccelerationBackend.CUDA

    if shutil.which("rocm-smi") is not None or Path("/opt/rocm").exists():
        return AccelerationBackend.ROCM

    return AccelerationBackend.CPU


def detect_capabilities(*, disk_path: Path | str | None = None) -> SystemCapabilities:
    """Detects real host capabilities. `disk_path` defaults to the home directory;
    pass the eventual app data directory once one exists."""
    usage_path = Path(disk_path) if disk_path is not None else Path.home()

    return SystemCapabilities(
        acceleration=_detect_acceleration(),
        memory_gb=psutil.virtual_memory().total / _BYTES_PER_GB,
        free_disk_gb=psutil.disk_usage(str(usage_path)).free / _BYTES_PER_GB,
    )
