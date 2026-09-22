"""Catalog contract describing every local model the app supports.

The concrete catalog contents (which real models are listed) are decided by
benchmarking, not by this schema; nothing here is a shipped model list.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class MemoryTier(str, Enum):
    """Rough resource class a model falls into, not a precise figure."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class ModelFile(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    # Overrides the entry's own repository/revision for this one file, e.g. a
    # tokenizer asset pinned to the base model's repository while the entry's
    # own repository/revision points at a separate GGUF quantization release.
    # Left unset, a file uses the entry's repository/revision - today's
    # single-source behavior is unchanged.
    repository: str | None = None
    revision: str | None = None

    @field_validator("size_bytes")
    @classmethod
    def _validate_non_negative_size(cls, value: int) -> int:
        if value < 0:
            raise ValueError("size_bytes must not be negative")
        return value

    @model_validator(mode="after")
    def _validate_repository_and_revision_are_both_set_or_neither(self) -> "ModelFile":
        if (self.repository is None) != (self.revision is None):
            raise ValueError("repository and revision must both be set, or both left unset")
        return self


class ModelCatalogEntry(BaseModel):
    id: str
    display_name: str
    license: str
    repository: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    files: list[ModelFile] = Field(min_length=1)
    memory_tier: MemoryTier
    prompt_template: str | None = None
    # llama.cpp context window (n_ctx): max prompt+generation tokens this
    # entry can process in one call. Unused by legacy Transformers entries
    # (left None). Required for any entry loaded via
    # LlamaCppExtractor.load_installed() - see that method's own validation.
    context_size: int | None = None
    # Short, user-facing qualitative comparison (speed/memory/accuracy
    # tradeoff), backed by docs/model_benchmark_findings.md - not a claim
    # every larger model must outperform every smaller one.
    description: str | None = None

    @property
    def total_size_bytes(self) -> int:
        return sum(file.size_bytes for file in self.files)
