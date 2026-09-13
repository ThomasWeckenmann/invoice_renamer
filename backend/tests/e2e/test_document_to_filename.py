"""Happy-path test crossing the document reader, extraction contract, and filename builder.

The actual text-to-fields extraction (BB-06/BB-08) isn't built yet, so this
test constructs the InvoiceExtraction by hand from the reader's output, the
way a real extraction adapter will, and asserts the pipeline still produces a
valid, non-review filename end to end.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_renamer.documents.reader import read_document
from invoice_renamer.extraction.models import Evidence, InvoiceExtraction, Language
from invoice_renamer.naming.builder import build_filename_proposal

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


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
