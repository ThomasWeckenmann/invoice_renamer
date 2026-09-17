"""Catalog contract describing every local model the app supports.

The concrete catalog contents (which real models are listed) are decided by
benchmarking, not by this schema; nothing here is a shipped model list.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class MemoryTier(str, Enum):
    """Rough resource class a model falls into, not a precise figure."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class ModelFile(BaseModel):
    path: str
    sha256: str
    size_bytes: int

    @field_validator("size_bytes")
    @classmethod
    def _validate_non_negative_size(cls, value: int) -> int:
        if value < 0:
            raise ValueError("size_bytes must not be negative")
        return value


class ModelCatalogEntry(BaseModel):
    id: str
    display_name: str
    license: str
    repository: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    files: list[ModelFile] = Field(min_length=1)
    memory_tier: MemoryTier
    prompt_template: str | None = None

    @property
    def total_size_bytes(self) -> int:
        return sum(file.size_bytes for file in self.files)
