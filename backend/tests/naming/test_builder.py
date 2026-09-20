"""Tests for deterministic filename proposal construction."""

from datetime import date
from decimal import Decimal

from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.naming.builder import build_filename_proposal


def _extraction(**overrides: object) -> InvoiceExtraction:
    defaults: dict[str, object] = {
        "invoice_date": date(2026, 9, 12),
        "seller": "Apple",
        "product_summary": "MacBook Air",
        "gross_total": Decimal("2180"),
        "currency": "EUR",
    }
    defaults.update(overrides)
    return InvoiceExtraction(**defaults)  # type: ignore[arg-type]


def test_full_extraction_produces_expected_filename() -> None:
    proposal = build_filename_proposal(_extraction())

    assert proposal.proposed_filename == "2026-09-12_Apple_MacBook-Air_2180-EUR.pdf"
    assert proposal.requires_review is False
    assert proposal.missing_fields == []
    assert proposal.warnings == []


def test_missing_date_uses_placeholder_and_requires_review() -> None:
    proposal = build_filename_proposal(_extraction(invoice_date=None))

    assert proposal.proposed_filename.startswith("0000-00-00_")
    assert proposal.requires_review is True
    assert proposal.missing_fields == ["date"]


def test_missing_seller_uses_placeholder_and_requires_review() -> None:
    proposal = build_filename_proposal(_extraction(seller=None))

    assert "_Unknown_" in proposal.proposed_filename
    assert proposal.requires_review is True
    assert proposal.missing_fields == ["seller"]


def test_missing_currency_uses_placeholder_and_requires_review() -> None:
    proposal = build_filename_proposal(_extraction(currency=None))

    assert proposal.proposed_filename.endswith("-XXX.pdf")
    assert proposal.requires_review is True
    assert proposal.missing_fields == ["currency"]


def test_warnings_force_review_even_when_fields_are_complete() -> None:
    proposal = build_filename_proposal(_extraction(warnings=["low OCR confidence"]))

    assert proposal.requires_review is True
    assert proposal.missing_fields == []


def test_short_fields_are_preferred_over_the_full_seller_and_product() -> None:
    proposal = build_filename_proposal(
        _extraction(seller_short="Amazon", product_summary_short="Galaxy Projektor")
    )

    assert proposal.proposed_filename == "2026-09-12_Amazon_Galaxy-Projektor_2180-EUR.pdf"


def test_missing_short_fields_fall_back_to_the_full_seller_and_product() -> None:
    proposal = build_filename_proposal(_extraction(seller_short=None, product_summary_short=None))

    assert proposal.proposed_filename == "2026-09-12_Apple_MacBook-Air_2180-EUR.pdf"


def test_german_umlauts_are_transliterated() -> None:
    proposal = build_filename_proposal(_extraction(seller="Müller & Söhne GmbH"))

    assert "Mueller" in proposal.proposed_filename
    assert "Soehne" in proposal.proposed_filename


def test_amount_rounds_half_up() -> None:
    proposal = build_filename_proposal(_extraction(gross_total=Decimal("2180.5")))

    assert "_2181-EUR.pdf" == proposal.proposed_filename[-len("_2181-EUR.pdf") :]


def test_forbidden_characters_are_stripped() -> None:
    proposal = build_filename_proposal(_extraction(product_summary='Foo/Bar:Baz*?"<>|'))

    assert "FooBarBaz" in proposal.proposed_filename
    for forbidden in '/:*?"<>|':
        assert forbidden not in proposal.proposed_filename


def test_whitespace_collapses_to_hyphen() -> None:
    proposal = build_filename_proposal(_extraction(product_summary="Multi   word   name"))

    assert "Multi-word-name" in proposal.proposed_filename


def test_general_punctuation_is_stripped() -> None:
    proposal = build_filename_proposal(
        _extraction(product_summary="Platform Consumption, Tier 1 (Region).")
    )

    stem = proposal.proposed_filename.removesuffix(".pdf")
    assert "Platform-Consumption-Tier-1-Region" in stem
    for punct in ",().":
        assert punct not in stem


def test_punctuation_removal_does_not_leave_a_double_hyphen() -> None:
    proposal = build_filename_proposal(_extraction(seller="Müller & Söhne GmbH"))

    assert "Mueller-Soehne-GmbH" in proposal.proposed_filename
    assert "--" not in proposal.proposed_filename


def test_leading_and_trailing_punctuation_leaves_no_stray_hyphen() -> None:
    proposal = build_filename_proposal(_extraction(product_summary="& Foo &"))

    assert "_Foo_" in proposal.proposed_filename


def test_non_german_accents_are_stripped_to_ascii() -> None:
    proposal = build_filename_proposal(_extraction(seller="Café Français"))

    assert "Cafe-Francais" in proposal.proposed_filename


def test_long_product_summary_is_truncated_without_losing_amount_or_currency() -> None:
    proposal = build_filename_proposal(_extraction(product_summary="Word " * 60))

    stem = proposal.proposed_filename.removesuffix(".pdf")
    assert len(stem) <= 150
    # The amount/currency suffix must survive truncation, not just overall length.
    assert stem.endswith("_2180-EUR")
    # Truncation must never be silent: flagged for review with a stated reason.
    assert proposal.requires_review is True
    assert len(proposal.warnings) == 1
    assert "product" in proposal.warnings[0]
    assert "truncated" in proposal.warnings[0]


def test_extremely_long_seller_truncates_seller_and_product_both() -> None:
    proposal = build_filename_proposal(_extraction(seller="Word " * 60, product_summary="Short"))

    stem = proposal.proposed_filename.removesuffix(".pdf")
    assert len(stem) <= 150
    assert stem.endswith("_2180-EUR")
    assert proposal.requires_review is True
    assert len(proposal.warnings) == 1
    assert "seller" in proposal.warnings[0]
    assert "product" in proposal.warnings[0]


def test_truncation_warning_never_echoes_the_actual_truncated_text() -> None:
    # The warning must name which fields were cut and by what limit, never
    # any of the (potentially arbitrarily long, or sensitive) original text -
    # a distinctive marker embedded in the product name must not leak into
    # the bounded, factual warning message.
    marker = "UNIQUE_MARKER_TOKEN"
    proposal = build_filename_proposal(_extraction(product_summary=f"{marker}{'-word' * 60}"))

    assert proposal.requires_review is True
    assert len(proposal.warnings) == 1
    assert marker not in proposal.warnings[0]
    assert proposal.warnings[0] == "product truncated to fit the 150-character filename limit"
