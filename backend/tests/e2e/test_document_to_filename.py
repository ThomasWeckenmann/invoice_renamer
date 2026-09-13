"""Happy-path test crossing the document reader, extraction contract, and filename builder.

No real language model (BB-06/BB-08) is built yet, so the first two tests
construct the InvoiceExtraction by hand from the reader's output, the way a
real extraction adapter will. The last test drives the same pipeline through
the actual inference interface (extract_invoice), using a scripted fake
model, proving the reader -> inference -> filename-builder chain works end
to end without depending on a real model choice.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_renamer.documents.reader import read_document
from invoice_renamer.extraction.models import Evidence, InvoiceExtraction, Language
from invoice_renamer.inference.extractor import extract_invoice
from invoice_renamer.naming.builder import build_filename_proposal

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class _FakeLanguageModel:
    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, prompt: str) -> str:
        return self._response


def test_selectable_text_invoice_produces_a_clean_filename_proposal() -> None:
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()

    document = read_document(pdf_bytes)
    assert document.pages[0].needs_ocr is False
    assert "Seller: Apple" in document.pages[0].text

    extraction = InvoiceExtraction(
        invoice_date=date(2026, 9, 12),
        seller="Apple",
        product_summary="MacBook Air",
        gross_total=Decimal("2180"),
        currency="EUR",
        language=Language.ENGLISH,
        evidence={
            "seller": Evidence(page=1, excerpt="Seller: Apple"),
            "product_summary": Evidence(page=1, excerpt="Product: MacBook Air"),
        },
    )

    proposal = build_filename_proposal(extraction)

    assert proposal.proposed_filename == "2026-09-12_Apple_MacBook-Air_2180-EUR.pdf"
    assert proposal.requires_review is False


def test_scanned_invoice_is_recovered_via_ocr_and_produces_a_filename_proposal() -> None:
    pdf_bytes = (FIXTURES_DIR / "scanned_invoice.pdf").read_bytes()

    document = read_document(pdf_bytes)
    assert document.pages[0].needs_ocr is True
    assert "Seller: Acme Corp" in document.pages[0].text

    extraction = InvoiceExtraction(
        invoice_date=date(2026, 1, 15),
        seller="Acme Corp",
        product_summary="Consulting services",
        gross_total=Decimal("450"),
        currency="EUR",
        language=Language.ENGLISH,
        evidence={
            "seller": Evidence(page=1, excerpt="Seller: Acme Corp", xml_field=None),
        },
        warnings=["seller/product identified from OCR text at 96% confidence"],
    )

    proposal = build_filename_proposal(extraction)

    assert proposal.proposed_filename == "2026-01-15_Acme-Corp_Consulting-services_450-EUR.pdf"
    # OCR-derived extractions carry a confidence warning, so they always get a review pass.
    assert proposal.requires_review is True


def test_reader_to_inference_to_filename_via_the_extraction_interface() -> None:
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    document = read_document(pdf_bytes)

    model_response = json.dumps(
        {
            "invoice_date": "2026-09-12",
            "seller": "Apple",
            "product_summary": "MacBook Air",
            "gross_total": "2180",
            "currency": "EUR",
            "language": "en",
            "warnings": [],
        }
    )

    extraction = extract_invoice(document, _FakeLanguageModel(model_response))
    proposal = build_filename_proposal(extraction)

    assert proposal.proposed_filename == "2026-09-12_Apple_MacBook-Air_2180-EUR.pdf"
    assert proposal.requires_review is False
