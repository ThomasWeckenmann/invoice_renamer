"""Pydantic contract for structured invoice data produced by any extraction adapter."""

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Language(str, Enum):
    GERMAN = "de"
    ENGLISH = "en"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    """Where an extracted field's value came from, so a user can verify it."""

    page: int | None = None
    excerpt: str | None = None
    xml_field: str | None = None


class InvoiceExtraction(BaseModel):
    """Structured fields extracted from an invoice. Missing fields stay None; adapters must
    never guess a value they aren't confident in."""

    invoice_date: date | None = None
    seller: str | None = None
    product_summary: str | None = None
    gross_total: Decimal | None = None
    currency: str | None = None
    language: Language = Language.UNKNOWN
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) != 3 or not value.isalpha() or value != value.upper():
            raise ValueError("currency must be a 3-letter uppercase ISO-4217 code")
        return value

    @field_validator("gross_total")
    @classmethod
    def _validate_gross_total(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("gross_total must not be negative")
        return value
