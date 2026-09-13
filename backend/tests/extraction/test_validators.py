"""Tests for the shared ISO-4217 currency validator."""

import pytest

from invoice_renamer.extraction.validators import validate_iso4217_currency


@pytest.mark.parametrize("code", ["EUR", "USD", "GBP", "JPY"])
def test_recognized_codes_are_accepted(code: str) -> None:
    assert validate_iso4217_currency(code) == code


@pytest.mark.parametrize(
    "value",
    [
        "eur",  # lowercase
        "EU",  # too short
        "EURO",  # too long
        "12A",  # not alphabetic
        "ZZZ",  # right shape, not a real currency
        "ÄBC",  # non-ASCII letters
        "中文币",  # non-Latin script, same length
    ],
)
def test_invalid_or_unrecognized_codes_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        validate_iso4217_currency(value)
