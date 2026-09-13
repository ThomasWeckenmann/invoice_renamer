"""Tests for prompting, JSON parsing, Pydantic validation, and the repair retry."""

import json

from invoice_renamer.documents.models import NormalizedDocument, PageText
from invoice_renamer.inference.extractor import extract_invoice

_VALID_JSON = json.dumps(
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


class _ScriptedLanguageModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def _document(
    text: str = "Seller: Apple\nTotal: 2180 EUR", *, warnings: list[str] | None = None
) -> NormalizedDocument:
    return NormalizedDocument(
        pages=[PageText(page_number=1, text=text, needs_ocr=False)],
        warnings=warnings or [],
    )


def test_valid_response_is_parsed_without_a_repair_call() -> None:
    model = _ScriptedLanguageModel([_VALID_JSON])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller == "Apple"
    assert extraction.currency == "EUR"
    assert len(model.prompts) == 1


def test_document_text_is_included_in_the_prompt() -> None:
    model = _ScriptedLanguageModel([_VALID_JSON])

    extract_invoice(_document("Seller: Acme Corp"), model)

    assert "Seller: Acme Corp" in model.prompts[0]
    assert "--- Page 1 ---" in model.prompts[0]


def test_malformed_json_is_repaired_on_retry() -> None:
    model = _ScriptedLanguageModel(["not json at all", _VALID_JSON])

    extraction = extract_invoice(_document("Seller: Acme Corp"), model)

    assert extraction.seller == "Apple"
    assert len(model.prompts) == 2
    assert "Validation error" in model.prompts[1]
    # The repair call is a separate, stateless generate(); it must repeat the
    # original invoice text and field instructions, not just the error.
    assert "Seller: Acme Corp" in model.prompts[1]
    assert "--- Page 1 ---" in model.prompts[1]


def test_schema_violation_is_repaired_on_retry() -> None:
    invalid = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel([invalid, _VALID_JSON])

    extraction = extract_invoice(_document(), model)

    assert extraction.currency == "EUR"
    assert len(model.prompts) == 2


def test_repeated_failure_yields_all_null_extraction_with_warning() -> None:
    model = _ScriptedLanguageModel(["still not json", "still not json"])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller is None
    assert extraction.invoice_date is None
    assert len(extraction.warnings) == 1
    assert len(model.prompts) == 2


def test_zero_repair_attempts_fails_fast() -> None:
    model = _ScriptedLanguageModel(["not json"])

    extraction = extract_invoice(_document(), model, max_repair_attempts=0)

    assert extraction.seller is None
    assert len(model.prompts) == 1


def test_document_warnings_are_preserved_on_success() -> None:
    document = _document(warnings=["page 1: low OCR confidence on the total"])
    model = _ScriptedLanguageModel([_VALID_JSON])

    extraction = extract_invoice(document, model)

    assert extraction.seller == "Apple"
    assert "page 1: low OCR confidence on the total" in extraction.warnings


def test_document_warnings_are_preserved_alongside_model_warnings() -> None:
    document = _document(warnings=["page 1: low OCR confidence on the total"])
    response = json.dumps({**json.loads(_VALID_JSON), "warnings": ["ambiguous product name"]})
    model = _ScriptedLanguageModel([response])

    extraction = extract_invoice(document, model)

    assert "page 1: low OCR confidence on the total" in extraction.warnings
    assert "ambiguous product name" in extraction.warnings


def test_document_warnings_are_preserved_on_repeated_failure() -> None:
    document = _document(warnings=["page 1: low OCR confidence on the total"])
    model = _ScriptedLanguageModel(["still not json", "still not json"])

    extraction = extract_invoice(document, model, max_repair_attempts=1)

    assert "page 1: low OCR confidence on the total" in extraction.warnings
    assert any("could not be validated" in warning for warning in extraction.warnings)
