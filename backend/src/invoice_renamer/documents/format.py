"""Detects which supported document format an upload is, from magic bytes alone."""

from enum import Enum

_PDF_MAGIC = b"%PDF-"
_JPEG_MAGIC = b"\xff\xd8\xff"


class DocumentFormat(str, Enum):
    PDF = "pdf"
    JPEG = "jpeg"

    @property
    def extension(self) -> str:
        return ".pdf" if self is DocumentFormat.PDF else ".jpg"


def detect_document_format(data: bytes) -> DocumentFormat | None:
    """Returns the format `data` starts with, or None if it matches neither
    supported format's magic bytes."""
    if data.startswith(_PDF_MAGIC):
        return DocumentFormat.PDF
    if data.startswith(_JPEG_MAGIC):
        return DocumentFormat.JPEG
    return None
