"""The detected host's hardware shape, used to judge model compatibility.

Actual detection (probing memory/disk/acceleration backend) isn't built yet;
this is only the data shape that detection will eventually produce.
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
