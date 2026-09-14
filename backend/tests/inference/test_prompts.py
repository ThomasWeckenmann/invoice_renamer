"""Tests for the extraction prompt's field instructions."""

from invoice_renamer.documents.models import NormalizedDocument, PageText
from invoice_renamer.inference.prompts import build_extraction_prompt


def _document(text: str) -> NormalizedDocument:
    return NormalizedDocument(
        pages=[PageText(page_number=1, text=text, needs_ocr=False)], warnings=[]
    )


def test_gross_total_instruction_warns_against_the_net_amount() -> None:
    prompt = build_extraction_prompt(_document("irrelevant"))

    assert "Nettobetrag" in prompt
    assert "Rechnungsbetrag" in prompt
    assert "net" in prompt.casefold()


def test_gross_total_instruction_does_not_permit_a_net_fallback() -> None:
    prompt = build_extraction_prompt(_document("irrelevant"))

    assert "fall back to a net amount" not in prompt.casefold()
    assert "use null" in prompt.casefold()
