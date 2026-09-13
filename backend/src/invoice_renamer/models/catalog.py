"""Catalog contract describing every model the app supports, open or closed.

The concrete catalog contents (which real models are listed) are decided by
benchmarking, not by this schema; nothing here is a shipped model list.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class ModelKind(str, Enum):
    OPEN_LOCAL = "open_local"
    CLOSED_CLOUD = "closed_cloud"


class MemoryTier(str, Enum):
    """Rough resource class an open/local model falls into, not a precise figure."""

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
    kind: ModelKind
    license: str

    # Open/local-only metadata.
    repository: str | None = None
    revision: str | None = None
    files: list[ModelFile] = Field(default_factory=list)
    memory_tier: MemoryTier | None = None
    prompt_template: str | None = None

    # Closed/cloud-only metadata.
    provider: str | None = None
    context_window: int | None = None

    @property
    def total_size_bytes(self) -> int:
        return sum(file.size_bytes for file in self.files)

    @model_validator(mode="after")
    def _validate_kind_specific_fields(self) -> "ModelCatalogEntry":
        if self.kind is ModelKind.OPEN_LOCAL:
            if not self.repository or not self.revision or not self.files or not self.memory_tier:
                raise ValueError(
                    "open_local entries require repository, revision, files, and memory_tier"
                )
        elif not self.provider:
            raise ValueError("closed_cloud entries require a provider")
        return self
