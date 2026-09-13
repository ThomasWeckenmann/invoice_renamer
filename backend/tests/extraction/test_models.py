"""Tests for the InvoiceExtraction contract's validation rules and defaults."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_renamer.extraction.models import Evidence, InvoiceExtraction, Language


def test_defaults_are_empty_and_unknown() -> None:
    extraction = InvoiceExtraction()

    assert extraction.invoice_date is None
    assert extraction.language is Language.UNKNOWN
    assert extraction.evidence == {}
    assert extraction.warnings == []


def test_full_construction_round_trips() -> None:
    extraction = InvoiceExtraction(
        invoice_date=date(2026, 9, 12),
        seller="Apple",
        product_summary="MacBook Air",
        gross_total=Decimal("2180"),
        currency="EUR",
        language=Language.ENGLISH,
        evidence={"seller": Evidence(page=1, excerpt="Apple Inc.")},
        warnings=["low confidence on product summary"],
    )

    assert extraction.currency == "EUR"
    assert extraction.evidence["seller"].page == 1


@pytest.mark.parametrize("currency", ["eur", "EU", "EURO", "12A", "ZZZ", "ÄBC", "中文币"])
def test_invalid_currency_codes_are_rejected(currency: str) -> None:
    with pytest.raises(ValidationError):
        InvoiceExtraction(currency=currency)


def test_negative_gross_total_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceExtraction(gross_total=Decimal("-1"))


def test_zero_gross_total_is_allowed() -> None:
    extraction = InvoiceExtraction(gross_total=Decimal("0"))

    assert extraction.gross_total == Decimal("0")
