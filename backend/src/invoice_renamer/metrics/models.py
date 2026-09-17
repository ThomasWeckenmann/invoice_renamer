"""Pydantic contract for per-run timing and execution metrics."""

import math

from pydantic import BaseModel, Field, field_validator, model_validator


class RunMetrics(BaseModel):
    total_ms: int
    pdf_extraction_ms: int
    ocr_ms: int
    inference_ms: int
    model_id: str
    provider: str
    model_revision: str | None = None
    pages_total: int
    pages_ocr: list[int] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("total_ms", "pdf_extraction_ms", "ocr_ms", "inference_ms")
    @classmethod
    def _validate_non_negative_ms(cls, value: int) -> int:
        if value < 0:
            raise ValueError("timings must not be negative")
        return value

    @field_validator("pages_total")
    @classmethod
    def _validate_pages_total(cls, value: int) -> int:
        if value < 1:
            raise ValueError("pages_total must be at least 1")
        return value

    @field_validator("input_tokens", "output_tokens")
    @classmethod
    def _validate_non_negative_tokens(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("token counts must not be negative")
        return value

    @field_validator("tokens_per_second")
    @classmethod
    def _validate_non_negative_rate(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("tokens_per_second must be a finite, non-negative number")
        return value

    @model_validator(mode="after")
    def _validate_pages_ocr_within_range(self) -> "RunMetrics":
        if any(page < 1 or page > self.pages_total for page in self.pages_ocr):
            raise ValueError("pages_ocr entries must be within 1..pages_total")
        return self
