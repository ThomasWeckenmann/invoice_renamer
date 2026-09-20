"""Pydantic contract for per-run timing and execution metrics."""

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Kept in sync by hand with extraction_router.ExtractionSource and
# xml_attachments.XmlDiscoveryStatus - see those modules for what each value means.
ExtractionSourceValue = Literal["xml", "xml_and_model", "model"]
XmlStatusValue = Literal["none", "supported", "unsupported", "invalid", "ambiguous"]

# Which of run_document_analysis's two, unrelated model passes a call belongs
# to - extract_invoice's own repair/retry calls are never mixed with
# shorten_fields' (also possibly-repaired) call(s), so a UI listing them can
# label each correctly instead of numbering every call as if it were one
# continuous series of extraction retries.
ModelCallPhase = Literal["extraction", "shortening"]


class ModelCall(BaseModel):
    """One prompt/response round trip to the language model, in call order -
    lets a user inspect exactly what was sent and what came back, including
    any repair/retry calls beyond the initial one in its phase."""

    prompt: str
    response: str | None = None
    error: str | None = None
    phase: ModelCallPhase = "extraction"


class RunMetrics(BaseModel):
    total_ms: int
    pdf_extraction_ms: int
    ocr_ms: int
    inference_ms: int
    # XML discovery/parsing time, already included in total_ms; 0 (its default)
    # for runs from before this field existed, since no XML routing ran then.
    xml_ms: int = 0
    model_id: str
    provider: str
    model_revision: str | None = None
    pages_total: int
    pages_ocr: list[int] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None
    warnings: list[str] = Field(default_factory=list)
    # Empty for a pre-existing run this field didn't capture yet, or a run
    # that never called the model at all (pure XML, no shortening).
    model_calls: list[ModelCall] = Field(default_factory=list)
    # Defaults describe a pre-XML-routing run: model-only, no XML ever detected,
    # inference ran to produce the result - never mark historical metrics as
    # having used XML they never had a chance to see.
    extraction_source: ExtractionSourceValue = "model"
    xml_status: XmlStatusValue = "none"
    xml_attachment_name: str | None = None
    xml_profile_id: str | None = None
    xml_fields_used: list[str] = Field(default_factory=list)
    inference_ran: bool = True

    @field_validator("total_ms", "pdf_extraction_ms", "ocr_ms", "inference_ms", "xml_ms")
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
