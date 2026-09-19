"""Tests for the field-shortening pass: prompting, JSON parsing, and the repair retry."""

import json

from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.inference.shortener import shorten_fields

_VALID_JSON = json.dumps({"seller_short": "Amazon", "product_short": "Galaxy Projektor"})


class _ScriptedLanguageModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def _extraction(**overrides: object) -> InvoiceExtraction:
    fields: dict[str, object] = {
        "seller": "Amazon EU S.a r.l.",
        "product_summary": (
            "Galaxy Projektor, 13 in 1 Home Planetarium Star Light Projektor fuer Schlafzimmer"
        ),
    }
    fields.update(overrides)
    return InvoiceExtraction(**fields)


def test_valid_response_populates_the_short_fields() -> None:
    model = _ScriptedLanguageModel([_VALID_JSON])

    extraction = shorten_fields(_extraction(), model)

    assert extraction.seller_short == "Amazon"
    assert extraction.product_summary_short == "Galaxy Projektor"
    assert len(model.prompts) == 1


def test_original_fields_are_left_untouched_alongside_the_short_ones() -> None:
    model = _ScriptedLanguageModel([_VALID_JSON])

    extraction = shorten_fields(_extraction(), model)

    assert extraction.seller == "Amazon EU S.a r.l."
    assert extraction.product_summary == (
        "Galaxy Projektor, 13 in 1 Home Planetarium Star Light Projektor fuer Schlafzimmer"
    )


def test_original_fields_are_included_in_the_prompt() -> None:
    model = _ScriptedLanguageModel([_VALID_JSON])

    shorten_fields(_extraction(seller="Acme Corp GmbH", product_summary="Widget"), model)

    assert "Acme Corp GmbH" in model.prompts[0]
    assert "Widget" in model.prompts[0]


def test_neither_field_present_skips_the_model_call_entirely() -> None:
    model = _ScriptedLanguageModel([])

    extraction = shorten_fields(_extraction(seller=None, product_summary=None), model)

    assert extraction.seller is None
    assert extraction.product_summary is None
    assert extraction.seller_short is None
    assert extraction.product_summary_short is None
    assert model.prompts == []


def test_null_seller_short_in_the_response_leaves_the_short_field_unset() -> None:
    response = json.dumps({"seller_short": None, "product_short": "Galaxy Projektor"})
    model = _ScriptedLanguageModel([response])

    extraction = shorten_fields(_extraction(), model)

    assert extraction.seller_short is None
    assert extraction.seller == "Amazon EU S.a r.l."
    assert extraction.product_summary_short == "Galaxy Projektor"


def test_hallucinated_seller_short_is_rejected_when_the_original_seller_is_null() -> None:
    # The original seller is unknown (null) but the model invents one anyway -
    # this must never surface, since it would let missing_fields/requires_review
    # be bypassed by fabricated text.
    response = json.dumps({"seller_short": "Invented", "product_short": "Galaxy Projektor"})
    model = _ScriptedLanguageModel([response])

    extraction = shorten_fields(_extraction(seller=None), model)

    assert extraction.seller_short is None
    assert extraction.seller is None
    assert extraction.product_summary_short == "Galaxy Projektor"


def test_hallucinated_product_short_is_rejected_when_the_original_product_is_null() -> None:
    response = json.dumps({"seller_short": "Amazon", "product_short": "Invented"})
    model = _ScriptedLanguageModel([response])

    extraction = shorten_fields(_extraction(product_summary=None), model)

    assert extraction.product_summary_short is None
    assert extraction.product_summary is None
    assert extraction.seller_short == "Amazon"


def test_hallucinated_seller_short_is_rejected_when_the_original_seller_is_blank() -> None:
    # Blank/whitespace, not None - the None-only guard must not be fooled by this.
    response = json.dumps({"seller_short": "Invented", "product_short": "Galaxy Projektor"})
    model = _ScriptedLanguageModel([response])

    extraction = shorten_fields(_extraction(seller="   "), model)

    assert extraction.seller_short is None


def test_whitespace_only_shortened_seller_is_rejected() -> None:
    # A whitespace-only seller_short is truthy in Python, so `seller_short or
    # seller` in naming/builder.py would pick it over a perfectly good
    # original - it must never be accepted here in the first place.
    response = json.dumps({"seller_short": "   ", "product_short": "Galaxy Projektor"})
    model = _ScriptedLanguageModel([response])

    extraction = shorten_fields(_extraction(), model)

    assert extraction.seller_short is None
    assert extraction.seller == "Amazon EU S.a r.l."


def test_empty_string_shortened_product_is_rejected() -> None:
    response = json.dumps({"seller_short": "Amazon", "product_short": ""})
    model = _ScriptedLanguageModel([response])

    extraction = shorten_fields(_extraction(), model)

    assert extraction.product_summary_short is None


def test_json_wrapped_in_a_markdown_fence_is_parsed() -> None:
    model = _ScriptedLanguageModel([f"```json\n{_VALID_JSON}\n```"])

    extraction = shorten_fields(_extraction(), model)

    assert extraction.seller_short == "Amazon"
    assert len(model.prompts) == 1


def test_malformed_json_is_repaired_on_retry() -> None:
    model = _ScriptedLanguageModel(["not json at all", _VALID_JSON])

    extraction = shorten_fields(_extraction(), model)

    assert extraction.seller_short == "Amazon"
    assert len(model.prompts) == 2
    assert "Validation error" in model.prompts[1]


def test_repeated_failure_leaves_the_short_fields_unset_with_a_skip_warning() -> None:
    model = _ScriptedLanguageModel(["still not json", "still not json"])

    extraction = shorten_fields(_extraction(), model, max_repair_attempts=1)

    assert extraction.seller_short is None
    assert extraction.product_summary_short is None
    assert extraction.seller == "Amazon EU S.a r.l."
    assert any("field shortening skipped" in warning for warning in extraction.warnings)
    assert len(model.prompts) == 2


def test_existing_warnings_are_preserved_on_a_skip() -> None:
    model = _ScriptedLanguageModel(["still not json", "still not json"])

    extraction = shorten_fields(
        _extraction(warnings=["page 1: low OCR confidence"]), model, max_repair_attempts=1
    )

    assert "page 1: low OCR confidence" in extraction.warnings
    assert any("field shortening skipped" in warning for warning in extraction.warnings)


def test_unrelated_extraction_fields_are_untouched() -> None:
    model = _ScriptedLanguageModel([_VALID_JSON])

    extraction = shorten_fields(
        _extraction(gross_total="42.00", currency="EUR"),
        model,
    )

    assert str(extraction.gross_total) == "42.00"
    assert extraction.currency == "EUR"
