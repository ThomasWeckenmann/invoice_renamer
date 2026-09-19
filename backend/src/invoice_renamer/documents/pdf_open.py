"""Opens and bounds-checks a PDF's page count, shared by page reading and XML
discovery so both apply the same validity limits without reading page content.
"""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

# Sanity ceiling; real invoices are a handful of pages.
MAX_PAGES = 200


def open_validated_pdf(pdf_bytes: bytes) -> tuple[PdfReader, int]:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        page_count = len(reader.pages)
    except (PyPdfError, ValueError) as error:
        raise ValueError(f"not a readable PDF: {error}") from error

    if page_count == 0:
        raise ValueError("PDF has no pages")
    if page_count > MAX_PAGES:
        raise ValueError(f"PDF has too many pages (> {MAX_PAGES})")

    return reader, page_count
