"""Extracts per-page text and detects embedded ZUGFeRD/Factur-X XML from PDF bytes.

OCR is not performed here: pages whose extracted text is too short to be
usable are flagged via `needs_ocr` for a separate OCR adapter to handle.
"""

from io import BytesIO

from pypdf import PdfReader
from pypdf._page import PageObject
from pypdf.errors import PyPdfError

from invoice_renamer.documents.models import NormalizedDocument, PageText

# Below this many non-whitespace characters, a page's extracted text is
# considered unusable and routed to OCR instead.
_MIN_USABLE_TEXT_CHARS = 20

# Sanity ceiling; real invoices are a handful of pages.
_MAX_PAGES = 200

# Standard attachment filenames used by the ZUGFeRD/Factur-X specs, checked
# case-insensitively.
_ZUGFERD_ATTACHMENT_NAMES = {
    "zugferd-invoice.xml",
    "factur-x.xml",
    "xrechnung.xml",
}


def read_document(pdf_bytes: bytes) -> NormalizedDocument:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        page_count = len(reader.pages)
    except (PyPdfError, ValueError) as error:
        raise ValueError(f"not a readable PDF: {error}") from error

    if page_count == 0:
        raise ValueError("PDF has no pages")
    if page_count > _MAX_PAGES:
        raise ValueError(f"PDF has too many pages (> {_MAX_PAGES})")

    pages = [_read_page(index, page) for index, page in enumerate(reader.pages)]

    return NormalizedDocument(pages=pages, embedded_xml=_find_embedded_xml(reader))


def _read_page(index: int, page: PageObject) -> PageText:
    text = (page.extract_text() or "").strip()
    return PageText(
        page_number=index + 1,
        text=text,
        needs_ocr=len(text) < _MIN_USABLE_TEXT_CHARS,
    )


def _find_embedded_xml(reader: PdfReader) -> str | None:
    for name, contents in reader.attachments.items():
        if name.lower() not in _ZUGFERD_ATTACHMENT_NAMES:
            continue
        if not contents:
            continue
        return contents[0].decode("utf-8", errors="replace")
    return None
