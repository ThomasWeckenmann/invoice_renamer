"""Tests for the Tesseract and Apple Vision OCR engine adapters."""

import shutil
import sys
from pathlib import Path

import pytest

from invoice_renamer.documents.ocr import (
    AppleVisionOcrEngine,
    TesseractOcrEngine,
    _to_vision_language_tags,
    aggregate_vision_observations,
    default_ocr_engine,
)
from invoice_renamer.documents.render import render_page_to_image

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"
_requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="Tesseract is not installed (no longer required on macOS - see README)",
)


@_requires_tesseract
def test_recognizes_text_and_reconstructs_lines() -> None:
    image = render_page_to_image((FIXTURES_DIR / "scanned_invoice.pdf").read_bytes(), 0)

    result = TesseractOcrEngine().recognize(image, language="eng+deu")

    assert result.text.splitlines() == [
        "Invoice #2002",
        "Seller: Acme Corp",
        "Total: 450.00 EUR",
    ]
    assert result.confidence is not None
    assert 0.0 < result.confidence <= 1.0


@_requires_tesseract
def test_recognizes_german_text() -> None:
    image = render_page_to_image((FIXTURES_DIR / "scanned_invoice_de.pdf").read_bytes(), 0)

    result = TesseractOcrEngine().recognize(image, language="eng+deu")

    assert result.text.splitlines() == [
        "Rechnung Nr. 3003",
        "Verkaeufer: Mueller GmbH",
        "Betrag: 199,00 EUR",
    ]
    assert result.confidence is not None
    assert 0.0 < result.confidence <= 1.0


@_requires_tesseract
def test_blank_image_yields_no_text_and_no_confidence() -> None:
    image = render_page_to_image((FIXTURES_DIR / "blank_page.pdf").read_bytes(), 0)

    result = TesseractOcrEngine().recognize(image, language="eng")

    assert result.text == ""
    assert result.confidence is None


def test_vision_language_tags_translate_tesseract_codes() -> None:
    assert _to_vision_language_tags("eng+deu") == ["en-US", "de-DE"]
    assert _to_vision_language_tags("eng") == ["en-US"]


def test_vision_language_tags_reject_an_unmapped_code() -> None:
    with pytest.raises(ValueError, match="fra"):
        _to_vision_language_tags("eng+fra")


def test_aggregate_vision_observations_empty_list_yields_no_text_or_confidence() -> None:
    result = aggregate_vision_observations([])

    assert result.text == ""
    assert result.confidence is None


def test_aggregate_vision_observations_orders_lines_top_to_bottom() -> None:
    # Vision's bounding box origin is bottom-left with y increasing upward,
    # so the larger y (0.8) is the higher, and therefore earlier, line.
    observations = [
        ("Total: 450.00 EUR", 0.9, (0.1, 0.2, 0.5, 0.05)),
        ("Invoice #2002", 0.8, (0.1, 0.8, 0.5, 0.05)),
        ("Seller: Acme Corp", 0.7, (0.1, 0.5, 0.5, 0.05)),
    ]

    result = aggregate_vision_observations(observations)

    assert result.text.splitlines() == [
        "Invoice #2002",
        "Seller: Acme Corp",
        "Total: 450.00 EUR",
    ]
    assert result.confidence == pytest.approx((0.9 + 0.8 + 0.7) / 3)


def test_aggregate_vision_observations_single_line() -> None:
    result = aggregate_vision_observations([("Invoice #2002", 0.95, (0.1, 0.5, 0.5, 0.05))])

    assert result.text == "Invoice #2002"
    assert result.confidence == pytest.approx(0.95)


def test_aggregate_vision_observations_keeps_table_rows_together() -> None:
    # Two invoice line-item rows, each split into a left (item) and right
    # (price) column observation. The two columns of one row rarely share an
    # exact y - reproduced here with a small vertical offset between them -
    # and sorting by y alone previously scrambled the reading order into
    # "10.00 EUR, Item A, 20.00 EUR, Item B" instead of keeping each row's
    # two columns together.
    observations = [
        ("10.00 EUR", 0.9, (0.5, 0.62, 0.3, 0.05)),
        ("Item A", 0.9, (0.1, 0.60, 0.3, 0.05)),
        ("20.00 EUR", 0.9, (0.5, 0.42, 0.3, 0.05)),
        ("Item B", 0.9, (0.1, 0.40, 0.3, 0.05)),
    ]

    result = aggregate_vision_observations(observations)

    assert result.text.splitlines() == ["Item A 10.00 EUR", "Item B 20.00 EUR"]


def test_default_ocr_engine_picks_by_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(default_ocr_engine(), AppleVisionOcrEngine)

    monkeypatch.setattr(sys, "platform", "linux")
    assert isinstance(default_ocr_engine(), TesseractOcrEngine)


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision is only available on macOS")
def test_apple_vision_recognizes_english_text() -> None:
    image = render_page_to_image((FIXTURES_DIR / "scanned_invoice.pdf").read_bytes(), 0)

    result = AppleVisionOcrEngine().recognize(image, language="eng+deu")

    assert "Invoice #2002" in result.text
    assert "Acme Corp" in result.text
    assert result.confidence is not None
    assert 0.0 <= result.confidence <= 1.0


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision is only available on macOS")
def test_apple_vision_recognizes_german_text() -> None:
    image = render_page_to_image((FIXTURES_DIR / "scanned_invoice_de.pdf").read_bytes(), 0)

    result = AppleVisionOcrEngine().recognize(image, language="eng+deu")

    assert "3003" in result.text
    assert "Mueller GmbH" in result.text
    assert result.confidence is not None
    assert 0.0 <= result.confidence <= 1.0
