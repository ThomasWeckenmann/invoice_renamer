"""Tests for PDF text extraction, OCR routing, and ZUGFeRD XML detection."""

from pathlib import Path

import pytest

from invoice_renamer.documents.reader import read_document

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


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


def test_garbage_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        read_document(b"this is not a pdf")


def test_empty_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        read_document(b"")
