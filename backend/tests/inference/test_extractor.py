"""Tests for prompting, JSON parsing, Pydantic validation, and the repair retry."""

import json
from datetime import date
from decimal import Decimal

import pytest

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
    }
)


class _ScriptedLanguageModel:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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


def test_json_wrapped_in_a_markdown_fence_with_language_tag_is_parsed() -> None:
    model = _ScriptedLanguageModel([f"```json\n{_VALID_JSON}\n```"])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller == "Apple"
    assert len(model.prompts) == 1  # parsed on the first attempt, no repair needed


def test_json_wrapped_in_a_bare_markdown_fence_is_parsed() -> None:
    model = _ScriptedLanguageModel([f"```\n{_VALID_JSON}\n```"])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller == "Apple"
    assert len(model.prompts) == 1


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


def test_schema_violation_on_one_field_is_recovered_by_a_repair_call() -> None:
    # Salvage doesn't short-circuit the retry loop - it still asks the model
    # to try again for the field it got wrong, and a successful repair wins.
    invalid = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel([invalid, _VALID_JSON])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller == "Apple"
    assert extraction.product_summary == "MacBook Air"
    assert extraction.currency == "EUR"
    assert len(model.prompts) == 2
    assert extraction.warnings == []


def test_schema_violation_persisting_through_repair_falls_back_to_salvage() -> None:
    invalid = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel([invalid, invalid])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller == "Apple"
    assert extraction.product_summary == "MacBook Air"
    assert extraction.currency is None
    assert len(model.prompts) == 2
    assert any("currency" in warning for warning in extraction.warnings)


def test_malformed_repair_response_still_uses_a_remaining_attempt() -> None:
    # Regression: falling back to an already-salvaged result must only
    # happen once attempts are actually exhausted - a malformed repair
    # response must not short-circuit budget that's still available.
    invalid_currency = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel([invalid_currency, "not json at all", _VALID_JSON])

    extraction = extract_invoice(_document(), model, max_repair_attempts=2)

    assert extraction.currency == "EUR"
    assert extraction.seller == "Apple"
    assert len(model.prompts) == 3
    assert extraction.warnings == []


def test_a_worse_repair_salvage_does_not_overwrite_a_better_earlier_one() -> None:
    # Regression: the first response salvages 4 useful fields (only currency
    # dropped); the repair response is worse (only seller survives). The
    # better, earlier result must win, not the most recent one.
    four_fields_bad_currency = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    only_seller_survives = json.dumps(
        {
            "invoice_date": ["bad"],
            "seller": "Apple",
            "product_summary": ["bad"],
            "gross_total": "not-a-number",
            "currency": "not-a-code",
            "language": "en",
        }
    )
    model = _ScriptedLanguageModel([four_fields_bad_currency, only_seller_survives])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller == "Apple"
    assert extraction.product_summary == "MacBook Air"
    assert extraction.gross_total == Decimal("2180")
    assert extraction.invoice_date == date(2026, 9, 12)
    assert extraction.currency is None
    assert len(model.prompts) == 2
    assert any("currency" in warning for warning in extraction.warnings)
    assert not any("product_summary" in warning for warning in extraction.warnings)


def test_repair_generation_failure_returns_the_saved_salvage_instead_of_raising() -> None:
    # Regression: extract_invoice's own docstring promises it never raises on
    # a bad model response - a generate() call failing during the *optional*
    # repair attempt must not break that promise when a good salvaged result
    # is already in hand.
    invalid_currency = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel([invalid_currency, RuntimeError("model backend crashed")])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller == "Apple"
    assert extraction.product_summary == "MacBook Air"
    assert extraction.currency is None
    assert len(model.prompts) == 2
    assert any("currency" in warning for warning in extraction.warnings)


def test_malformed_repair_then_generation_crash_returns_the_saved_salvage() -> None:
    # Regression: the *other* unprotected generate() call site (retrying
    # after a malformed/unparseable repair response) must be just as
    # crash-safe as the salvage-branch one - a generation failure there must
    # not discard a salvaged result already in hand either. Sequence:
    # salvageable response -> malformed repair -> generation exception, with
    # a repair attempt still available (max_repair_attempts=2).
    invalid_currency = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel(
        [invalid_currency, "not json at all", RuntimeError("model backend crashed")]
    )

    extraction = extract_invoice(_document(), model, max_repair_attempts=2)

    assert extraction.seller == "Apple"
    assert extraction.product_summary == "MacBook Air"
    assert extraction.currency is None
    assert len(model.prompts) == 3
    assert any("currency" in warning for warning in extraction.warnings)


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


def test_model_supplied_warnings_are_dropped() -> None:
    document = _document(warnings=["page 1: low OCR confidence on the total"])
    response = json.dumps({**json.loads(_VALID_JSON), "warnings": ["ambiguous product name"]})
    model = _ScriptedLanguageModel([response])

    extraction = extract_invoice(document, model)

    assert "page 1: low OCR confidence on the total" in extraction.warnings
    assert "ambiguous product name" not in extraction.warnings


def test_model_supplied_short_fields_are_dropped_even_when_shortening_is_disabled() -> None:
    # seller_short/product_summary_short must only ever come from the dedicated
    # shortening pass - if the primary extraction response includes them too
    # (the prompt doesn't ask for them, but nothing stops a model from adding
    # them), they must never surface here regardless of the shorten toggle.
    response = json.dumps(
        {**json.loads(_VALID_JSON), "seller_short": "Sneaky", "product_summary_short": "Sneaky"}
    )
    model = _ScriptedLanguageModel([response])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller_short is None
    assert extraction.product_summary_short is None


def test_document_warnings_are_preserved_on_repeated_failure() -> None:
    document = _document(warnings=["page 1: low OCR confidence on the total"])
    model = _ScriptedLanguageModel(["still not json", "still not json"])

    extraction = extract_invoice(document, model, max_repair_attempts=1)

    assert "page 1: low OCR confidence on the total" in extraction.warnings
    assert any("could not be validated" in warning for warning in extraction.warnings)


def test_final_warning_includes_the_raw_response_that_failed_to_parse() -> None:
    document = _document()
    model = _ScriptedLanguageModel(["not json at all", "still not json"])

    extraction = extract_invoice(document, model, max_repair_attempts=1)

    assert any("still not json" in warning for warning in extraction.warnings)


def test_final_warning_truncates_a_very_long_raw_response() -> None:
    document = _document()
    long_response = "x" * 1000
    model = _ScriptedLanguageModel([long_response, long_response])

    extraction = extract_invoice(document, model, max_repair_attempts=1)

    warning = next(w for w in extraction.warnings if "could not be validated" in w)
    assert "(truncated)" in warning
    assert len(warning) < len(long_response)


def test_multiple_bad_fields_are_all_salvaged_together() -> None:
    invalid = json.dumps(
        {**json.loads(_VALID_JSON), "currency": "not-a-code", "language": "french"}
    )
    model = _ScriptedLanguageModel([invalid, invalid])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller == "Apple"
    assert extraction.product_summary == "MacBook Air"
    assert extraction.currency is None
    assert extraction.language.value == "unknown"
    assert len(model.prompts) == 2
    assert any("currency" in warning for warning in extraction.warnings)
    assert any("language" in warning for warning in extraction.warnings)


def test_malformed_evidence_field_is_dropped_as_a_whole() -> None:
    invalid = json.dumps({**json.loads(_VALID_JSON), "evidence": {"seller": "not-a-dict"}})
    model = _ScriptedLanguageModel([invalid, invalid])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller == "Apple"
    assert extraction.evidence == {}
    assert len(model.prompts) == 2
    assert any("evidence" in warning for warning in extraction.warnings)


def test_multiple_bad_evidence_keys_produce_one_deduplicated_warning() -> None:
    invalid = json.dumps(
        {
            **json.loads(_VALID_JSON),
            "evidence": {"seller": "not-a-dict", "product_summary": "also not a dict"},
        }
    )
    model = _ScriptedLanguageModel([invalid, invalid])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.evidence == {}
    evidence_warnings = [w for w in extraction.warnings if "evidence" in w]
    assert len(evidence_warnings) == 1


def test_document_and_salvage_warnings_are_both_preserved() -> None:
    document = _document(warnings=["page 1: low OCR confidence on the total"])
    invalid = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel([invalid, invalid])

    extraction = extract_invoice(document, model, max_repair_attempts=1)

    assert "page 1: low OCR confidence on the total" in extraction.warnings
    assert any("currency" in warning for warning in extraction.warnings)
    assert len(extraction.warnings) == 2


def test_long_pydantic_message_is_truncated_in_the_salvage_warning() -> None:
    # A well-formed but unrecognized currency code hits the validator's second,
    # longer error message (the format check's message alone is short).
    invalid = json.dumps({**json.loads(_VALID_JSON), "currency": "ZZZ"})
    model = _ScriptedLanguageModel([invalid, invalid])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    warning = next(w for w in extraction.warnings if w.startswith("currency"))
    assert warning.endswith("...")
    assert len(warning) < len("currency: ") + 61


def test_all_five_filename_fields_invalid_triggers_repair_not_salvage() -> None:
    all_invalid = json.dumps(
        {
            "invoice_date": "not-a-date",
            "seller": ["bad"],
            "product_summary": ["bad"],
            "gross_total": "not-a-number",
            "currency": "not-a-code",
            "language": "en",
        }
    )
    model = _ScriptedLanguageModel([all_invalid, _VALID_JSON])

    extraction = extract_invoice(_document(), model)

    # Nothing useful survived salvage, so the original error went to repair
    # instead - the second, valid response is what actually won here.
    assert extraction.seller == "Apple"
    assert len(model.prompts) == 2


def test_all_five_filename_fields_invalid_with_repeated_failure_reports_terminal_marker() -> None:
    all_invalid = json.dumps(
        {
            "invoice_date": "not-a-date",
            "seller": ["bad"],
            "product_summary": ["bad"],
            "gross_total": "not-a-number",
            "currency": "not-a-code",
            "language": "en",
        }
    )
    model = _ScriptedLanguageModel([all_invalid, all_invalid])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller is None
    assert any("could not be validated" in warning for warning in extraction.warnings)
    assert len(model.prompts) == 2


def test_one_surviving_filename_field_is_enough_to_salvage() -> None:
    mostly_invalid = json.dumps(
        {
            "invoice_date": "not-a-date",
            "seller": "Apple",
            "product_summary": ["bad"],
            "gross_total": "not-a-number",
            "currency": "not-a-code",
            "language": "en",
        }
    )
    model = _ScriptedLanguageModel([mostly_invalid, mostly_invalid])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller == "Apple"
    assert extraction.product_summary is None
    assert len(model.prompts) == 2


def test_blank_surviving_field_does_not_count_as_useful() -> None:
    # seller is whitespace-only - a valid string to pydantic, but not usable
    # for a filename, so it must not count toward "something useful survived."
    mostly_invalid = json.dumps(
        {
            "invoice_date": "not-a-date",
            "seller": "   ",
            "product_summary": ["bad"],
            "gross_total": "not-a-number",
            "currency": "not-a-code",
            "language": "en",
        }
    )
    model = _ScriptedLanguageModel([mostly_invalid, _VALID_JSON])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller == "Apple"  # from the repaired response, not "   "
    assert len(model.prompts) == 2


def test_already_valid_empty_response_needs_no_salvage() -> None:
    empty = json.dumps(
        {
            "invoice_date": None,
            "seller": None,
            "product_summary": None,
            "gross_total": None,
            "currency": None,
            "language": "unknown",
        }
    )
    model = _ScriptedLanguageModel([empty])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller is None
    assert extraction.warnings == []
    assert len(model.prompts) == 1


@pytest.mark.parametrize("non_object_json", ["[]", '"hello"', "42", "true", "null"])
def test_non_object_json_skips_salvage_and_is_repaired(non_object_json: str) -> None:
    model = _ScriptedLanguageModel([non_object_json, _VALID_JSON])

    extraction = extract_invoice(_document(), model)

    assert extraction.seller == "Apple"
    assert len(model.prompts) == 2


def test_non_object_json_with_zero_repair_attempts_fails_fast() -> None:
    model = _ScriptedLanguageModel(["[]"])

    extraction = extract_invoice(_document(), model, max_repair_attempts=0)

    assert extraction.seller is None
    assert len(model.prompts) == 1


def test_malformed_first_response_then_a_salvageable_repaired_one() -> None:
    # Salvage must apply on every attempt, not just the first - the repair
    # call can introduce its own single-field mistake just as easily.
    invalid_currency = json.dumps({**json.loads(_VALID_JSON), "currency": "not-a-code"})
    model = _ScriptedLanguageModel(["not json at all", invalid_currency])

    extraction = extract_invoice(_document(), model, max_repair_attempts=1)

    assert extraction.seller == "Apple"
    assert extraction.currency is None
    assert len(model.prompts) == 2
    assert any("currency" in warning for warning in extraction.warnings)
