"""The detected host's hardware shape, used to judge model compatibility.

Only the data shape lives here; `detection.py` does the probing that fills it.
"""

import math
from enum import Enum

from pydantic import BaseModel, field_validator


class AccelerationBackend(str, Enum):
    MPS = "mps"
    CUDA = "cuda"
    ROCM = "rocm"
    CPU = "cpu"


class SystemCapabilities(BaseModel):
    acceleration: AccelerationBackend
    memory_gb: float
    free_disk_gb: float

    @field_validator("memory_gb", "free_disk_gb")
    @classmethod
    def _validate_non_negative_finite(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("must be a finite, non-negative number")
        return value
