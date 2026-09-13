"""Pydantic contract for per-run timing, execution-path, and cost metrics."""

import math
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from invoice_renamer.extraction.validators import validate_iso4217_currency


class ExecutionMode(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class CostSource(str, Enum):
    """Whether a reported cost came from the provider or was locally estimated."""

    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"


class RunMetrics(BaseModel):
    total_ms: int
    pdf_extraction_ms: int
    ocr_ms: int
    inference_ms: int
    execution_mode: ExecutionMode
    model_id: str
    provider: str
    model_revision: str | None = None
    pages_total: int
    pages_ocr: list[int] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None
    cost: Decimal | None = None
    cost_currency: str | None = None
    cost_source: CostSource | None = None
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

    @field_validator("cost")
    @classmethod
    def _validate_non_negative_cost(cls, value: Decimal | None) -> Decimal | None:
        # Checked in this order: Decimal comparisons against NaN raise
        # InvalidOperation rather than returning False like float does.
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("cost must be a finite, non-negative number")
        return value

    @field_validator("cost_currency")
    @classmethod
    def _validate_cost_currency(cls, value: str | None) -> str | None:
        return None if value is None else validate_iso4217_currency(value)

    @model_validator(mode="after")
    def _validate_pages_ocr_within_range(self) -> "RunMetrics":
        if any(page < 1 or page > self.pages_total for page in self.pages_ocr):
            raise ValueError("pages_ocr entries must be within 1..pages_total")
        return self

    @model_validator(mode="after")
    def _validate_cost_is_labeled(self) -> "RunMetrics":
        # Cost is never fabricated: whenever it's present, its currency and
        # whether it's provider-reported or estimated must be too.
        if self.cost is not None and (self.cost_currency is None or self.cost_source is None):
            raise ValueError("cost requires both cost_currency and cost_source to be set")
        return self
