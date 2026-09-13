"""Tests for the Tesseract OCR engine adapter."""

from pathlib import Path

from invoice_renamer.documents.ocr import TesseractOcrEngine
from invoice_renamer.documents.render import render_page_to_image

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


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


def test_blank_image_yields_no_text_and_no_confidence() -> None:
    image = render_page_to_image((FIXTURES_DIR / "blank_page.pdf").read_bytes(), 0)

    result = TesseractOcrEngine().recognize(image, language="eng")

    assert result.text == ""
    assert result.confidence is None
