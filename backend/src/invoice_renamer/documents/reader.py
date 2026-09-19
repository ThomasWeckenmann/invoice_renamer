"""Extracts per-page text from PDF bytes and OCRs pages whose extracted text is too
short to be usable. Embedded ZUGFeRD/Factur-X XML discovery lives in
xml_attachments.py, so it can run without paying for this module's page/OCR work.
"""

from pypdf import PdfReader
from pypdf._page import PageObject

from invoice_renamer.documents.models import NormalizedDocument, PageText
from invoice_renamer.documents.ocr import OcrEngine, default_ocr_engine
from invoice_renamer.documents.pdf_open import open_validated_pdf
from invoice_renamer.documents.render import render_page_to_image
from invoice_renamer.documents.xml_attachments import (
    XmlDiscoveryResult,
    XmlDiscoveryStatus,
    discover_invoice_xml,
)

# Below this many non-whitespace characters, a page's extracted text is
# considered unusable and routed to OCR instead.
_MIN_USABLE_TEXT_CHARS = 20


def read_document(
    pdf_bytes: bytes,
    *,
    ocr_engine: OcrEngine | None = None,
    reader: PdfReader | None = None,
    xml_result: XmlDiscoveryResult | None = None,
) -> NormalizedDocument:
    """Reads every page's text (OCR-ing pages that need it) and reports any
    supported embedded invoice XML as decoded text.

    `reader` and `xml_result` let a caller that already opened/validated the PDF
    and ran XML discovery (the analysis pipeline) reuse that work instead of
    re-parsing the PDF and re-decompressing the attachment a second time.
    """
    if reader is None:
        reader, _ = open_validated_pdf(pdf_bytes)
    if xml_result is None:
        xml_result = discover_invoice_xml(reader)

    ocr_engine = ocr_engine or default_ocr_engine()
    pages = [
        _read_page(index, page, pdf_bytes, ocr_engine) for index, page in enumerate(reader.pages)
    ]

    embedded_xml = None
    if xml_result.status == XmlDiscoveryStatus.SUPPORTED and xml_result.candidate is not None:
        embedded_xml = xml_result.candidate.raw_bytes.decode("utf-8", errors="replace")

    return NormalizedDocument(pages=pages, embedded_xml=embedded_xml)


def _read_page(index: int, page: PageObject, pdf_bytes: bytes, ocr_engine: OcrEngine) -> PageText:
    text = (page.extract_text() or "").strip()
    non_whitespace_chars = len("".join(text.split()))
    needs_ocr = non_whitespace_chars < _MIN_USABLE_TEXT_CHARS
    if not needs_ocr:
        return PageText(page_number=index + 1, text=text, needs_ocr=False)

    image = render_page_to_image(pdf_bytes, index)
    result = ocr_engine.recognize(image, language="eng+deu")
    return PageText(
        page_number=index + 1,
        text=result.text,
        needs_ocr=True,
        ocr_confidence=result.confidence,
    )
