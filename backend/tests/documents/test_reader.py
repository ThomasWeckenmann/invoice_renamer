"""Tests for PDF text extraction, OCR routing, and ZUGFeRD XML detection."""

from pathlib import Path

import pytest
from PIL import Image

from invoice_renamer.documents.ocr import OcrResult
from invoice_renamer.documents.reader import read_document

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class _StubOcrEngine:
    def recognize(self, image: Image.Image, *, language: str) -> OcrResult:
        return OcrResult(text="stubbed text", confidence=0.42)


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_selectable_text_page_does_not_need_ocr() -> None:
    document = read_document(_load("selectable_text_en.pdf"))

    assert len(document.pages) == 1
    assert "Seller: Apple" in document.pages[0].text
    assert document.pages[0].needs_ocr is False
    assert document.embedded_xml is None


def test_german_text_extracts_correctly() -> None:
    document = read_document(_load("selectable_text_de.pdf"))

    assert "Mueller GmbH" in document.pages[0].text


def test_blank_page_needs_ocr() -> None:
    document = read_document(_load("blank_page.pdf"))

    assert document.pages[0].needs_ocr is True
    assert document.pages[0].text == ""
    assert document.pages[0].ocr_confidence is None


def test_scanned_page_is_recovered_via_ocr() -> None:
    document = read_document(_load("scanned_invoice.pdf"))

    page = document.pages[0]
    assert page.needs_ocr is True
    assert "Seller: Acme Corp" in page.text
    assert "Total: 450.00 EUR" in page.text
    assert page.ocr_confidence is not None
    assert page.ocr_confidence > 0.5


def test_sparse_text_padded_with_spaces_still_needs_ocr() -> None:
    # Text length alone ("A" + 30 spaces + "B" = 32 chars) clears the 20-char
    # threshold; only 2 characters are non-whitespace, so this must still
    # route to OCR rather than being accepted as real page text.
    document = read_document(_load("sparse_whitespace.pdf"), ocr_engine=_StubOcrEngine())

    assert document.pages[0].needs_ocr is True
    assert document.pages[0].text == "stubbed text"


def test_mixed_pages_are_routed_independently() -> None:
    document = read_document(_load("mixed_pages.pdf"))

    assert len(document.pages) == 2
    assert document.pages[0].page_number == 1
    assert document.pages[0].needs_ocr is False
    assert document.pages[1].page_number == 2
    assert document.pages[1].needs_ocr is True


def test_zugferd_xml_is_detected_and_decoded() -> None:
    document = read_document(_load("with_zugferd_xml.pdf"))

    assert document.embedded_xml is not None
    assert "CrossIndustryInvoice" in document.embedded_xml
    assert "INV-0001" in document.embedded_xml


def test_custom_ocr_engine_is_used_when_provided() -> None:
    document = read_document(_load("blank_page.pdf"), ocr_engine=_StubOcrEngine())

    assert document.pages[0].text == "stubbed text"
    assert document.pages[0].ocr_confidence == 0.42


def test_garbage_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        read_document(b"this is not a pdf")


def test_empty_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        read_document(b"")
