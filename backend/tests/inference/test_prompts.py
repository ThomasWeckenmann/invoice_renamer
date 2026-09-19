"""Tests for the extraction prompt's field instructions."""

from invoice_renamer.documents.models import NormalizedDocument, PageText
from invoice_renamer.inference.prompts import build_extraction_prompt, build_shorten_prompt


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


def test_prompt_does_not_request_warnings_from_the_model() -> None:
    prompt = " ".join(build_extraction_prompt(_document("irrelevant")).casefold().split())

    assert "warnings" not in prompt
    assert "do not add any keys" in prompt


def test_product_summary_instruction_asks_for_short_text() -> None:
    prompt = " ".join(build_extraction_prompt(_document("irrelevant")).casefold().split())

    assert "keep it short" in prompt


def test_seller_and_product_summary_are_restricted_to_filename_safe_characters() -> None:
    prompt = " ".join(build_extraction_prompt(_document("irrelevant")).casefold().split())

    assert "become part of a filename" in prompt
    assert "macbook-air" in prompt
    assert "mueller-soehne-gmbh" in prompt


def test_shorten_prompt_includes_both_given_fields() -> None:
    prompt = build_shorten_prompt("Amazon EU S.a r.l.", "Galaxy Projektor 13 in 1")

    assert "Amazon EU S.a r.l." in prompt
    assert "Galaxy Projektor 13 in 1" in prompt


def test_shorten_prompt_asks_for_the_short_json_keys() -> None:
    prompt = build_shorten_prompt("Amazon", "Galaxy Projektor")

    assert "seller_short" in prompt
    assert "product_short" in prompt


def test_shorten_prompt_forbids_translation() -> None:
    prompt = build_shorten_prompt("Amazon", "Galaxy Projektor").casefold()

    assert "do not translate" in prompt
