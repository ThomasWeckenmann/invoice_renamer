"""Tests for magic-byte format detection."""

from invoice_renamer.documents.format import DocumentFormat, detect_document_format


def test_pdf_magic_bytes_are_detected() -> None:
    assert detect_document_format(b"%PDF-1.7\n...") is DocumentFormat.PDF


def test_jpeg_magic_bytes_are_detected() -> None:
    assert detect_document_format(b"\xff\xd8\xff\xe0\x00\x10JFIF") is DocumentFormat.JPEG


def test_unrecognized_bytes_return_none() -> None:
    assert detect_document_format(b"not a document at all") is None


def test_empty_bytes_return_none() -> None:
    assert detect_document_format(b"") is None


def test_pdf_extension_is_dot_pdf() -> None:
    assert DocumentFormat.PDF.extension == ".pdf"


def test_jpeg_extension_is_dot_jpg() -> None:
    assert DocumentFormat.JPEG.extension == ".jpg"
