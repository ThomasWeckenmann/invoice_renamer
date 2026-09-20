"""Orchestrates prompting, JSON parsing, Pydantic validation, and repair retries.

Never raises on a bad model response: a response that still fails validation
after the repair attempts become an all-null InvoiceExtraction carrying a
warning, so a single bad extraction can't crash a batch run. A response with
one invalid field among otherwise-good ones is salvaged instead of discarded
whole (see _salvage()), but salvage still spends a repair attempt trying to
get the dropped field back before settling for the partial result - it's a
safety net under the retry loop, not a shortcut around it.
"""

import json
import re

from pydantic import ValidationError

from invoice_renamer.documents.models import NormalizedDocument
from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.inference.prompts import build_extraction_prompt, build_repair_prompt


def _merge_document_warnings(
    document_warnings: list[str], extraction: InvoiceExtraction
) -> InvoiceExtraction:
    # extraction.warnings only ever holds salvage warnings here (the model
    # was never asked for "warnings", and that key is stripped from its JSON
    # before validation) - merge rather than replace, or a salvaged field's
    # warning would be silently dropped.
    return extraction.model_copy(update={"warnings": [*document_warnings, *extraction.warnings]})


def _terminal_failure(document_warnings: list[str], reason: str) -> InvoiceExtraction:
    return InvoiceExtraction(
        warnings=[*document_warnings, f"model output could not be validated: {reason}"]
    )


def extract_invoice(
    document: NormalizedDocument,
    model: LanguageModel,
    *,
    max_repair_attempts: int = 1,
) -> InvoiceExtraction:
    prompt = build_extraction_prompt(document)
    response = model.generate(prompt)

    attempts = 0
    # The best salvaged (partial but useful) result seen so far, kept only as
    # a fallback. Nothing past this point may ever make the final result
    # worse than what's already saved here: a later attempt that salvages
    # fewer useful fields must not overwrite it, and a repair generation
    # call that fails outright must not lose it either.
    salvaged_fallback: InvoiceExtraction | None = None
    while True:
        try:
            extraction, salvage_error = _parse(response)
        except (json.JSONDecodeError, ValidationError) as error:
            if attempts >= max_repair_attempts:
                if salvaged_fallback is not None:
                    return _merge_document_warnings(document.warnings, salvaged_fallback)
                # Includes the raw response (truncated) so a saved report can show
                # what the model actually produced, not just that parsing failed.
                raw = response if len(response) <= 500 else f"{response[:500]}...(truncated)"
                return _terminal_failure(document.warnings, f"{error}; raw response: {raw!r}")
            attempts += 1
            try:
                response = model.generate(build_repair_prompt(prompt, response, str(error)))
            except Exception as generation_error:
                # This retry is either recovering from a broken response or
                # (if salvaged_fallback is set) topping up an already-useful
                # salvaged one - a generation failure here must never be
                # worse than just keeping what's already in hand.
                if salvaged_fallback is not None:
                    return _merge_document_warnings(document.warnings, salvaged_fallback)
                return _terminal_failure(
                    document.warnings, f"repair generation failed: {generation_error!r}"
                )
            continue

        if salvage_error is None:
            return _merge_document_warnings(document.warnings, extraction)  # fully valid

        # Something was dropped to get here - keep it only if it's at least
        # as useful as anything salvaged already.
        if salvaged_fallback is None or _usable_field_count(extraction) > _usable_field_count(
            salvaged_fallback
        ):
            salvaged_fallback = extraction

        if attempts >= max_repair_attempts:
            return _merge_document_warnings(document.warnings, salvaged_fallback)
        attempts += 1
        try:
            response = model.generate(build_repair_prompt(prompt, response, str(salvage_error)))
        except Exception:
            # The repair call is a pure bonus on top of an already-useful
            # salvaged result - a generation failure here must not lose it.
            return _merge_document_warnings(document.warnings, salvaged_fallback)


# Instruct-tuned models commonly wrap JSON in a markdown code fence even when
# told not to; stripping it is more robust than relying on prompt compliance.
_MARKDOWN_JSON_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_markdown_fence(response: str) -> str:
    match = _MARKDOWN_JSON_FENCE.match(response.strip())
    return match.group(1) if match else response


# Top-level InvoiceExtraction fields the model's JSON can actually populate
# (warnings/seller_short/product_summary_short are stripped below before
# validation ever sees them, so a ValidationError can never name those).
# Bounds both how many salvage warnings one response can produce and which
# field names ever get echoed into one.
_ELIGIBLE_SALVAGE_FIELDS = frozenset(
    {"invoice_date", "seller", "product_summary", "gross_total", "currency", "language", "evidence"}
)

# Of the eligible fields above, only these make a salvaged result worth
# keeping over an all-null response - language/evidence alone isn't enough
# to build a filename from.
_USEFUL_SALVAGE_FIELDS = ("invoice_date", "seller", "product_summary", "gross_total", "currency")

_MAX_SALVAGE_MESSAGE_CHARS = 50


def _has_usable_value(value: object) -> bool:
    return value.strip() != "" if isinstance(value, str) else value is not None


def _usable_field_count(extraction: InvoiceExtraction) -> int:
    """How many of the 5 filename fields this extraction can actually supply -
    used to compare two salvaged results so a worse one can never replace a
    better one already in hand."""
    return sum(1 for name in _USEFUL_SALVAGE_FIELDS if _has_usable_value(getattr(extraction, name)))


def _salvage(data: dict[str, object], error: ValidationError) -> InvoiceExtraction:
    """Drops exactly the top-level fields pydantic rejected and re-validates,
    keeping everything the model got right. Only loc[0] is ever inspected, so
    a nested error (e.g. under evidence) is treated the same as a top-level
    one - the whole named field is dropped either way. Re-raises the original
    error (for the caller's existing repair-prompt retry) whenever salvage
    can't be trusted: an error whose loc[0] isn't one of the known fields,
    re-validation still failing, or nothing useful surviving the drop."""
    rejected: dict[str, str] = {}
    for detail in error.errors():
        loc = detail["loc"]
        field_name = loc[0] if loc else None
        if not isinstance(field_name, str) or field_name not in _ELIGIBLE_SALVAGE_FIELDS:
            raise error
        rejected.setdefault(field_name, str(detail["msg"]))

    salvaged_data = {key: value for key, value in data.items() if key not in rejected}
    try:
        extraction = InvoiceExtraction.model_validate(salvaged_data)
    except ValidationError:
        raise error from None

    if not any(_has_usable_value(getattr(extraction, name)) for name in _USEFUL_SALVAGE_FIELDS):
        raise error

    warnings = [
        f"{field}: {message[:_MAX_SALVAGE_MESSAGE_CHARS]}"
        f"{'...' if len(message) > _MAX_SALVAGE_MESSAGE_CHARS else ''}"
        for field, message in rejected.items()
    ]
    return extraction.model_copy(update={"warnings": warnings})


def _parse(response: str) -> tuple[InvoiceExtraction, ValidationError | None]:
    """Returns (extraction, salvage_error). salvage_error is None for a fully
    valid response, or the ValidationError salvage recovered from otherwise -
    the caller uses it to ask the model to try again for the dropped field(s)
    rather than settling for the gap immediately."""
    data = json.loads(_strip_markdown_fence(response))
    if isinstance(data, dict):
        # The model is no longer asked for warnings, but instruct-tuned models
        # commonly echo one back out of training habit; drop it so it can't
        # leak model-authored text into a field only code should populate.
        data.pop("warnings", None)
        # seller_short/product_summary_short must only ever come from the
        # dedicated shorten_fields() pass, gated by shorten_enabled - if the
        # primary extraction response happens to include these keys too
        # (the prompt doesn't ask for them, but nothing stops a model from
        # adding them), they must not slip through here regardless.
        data.pop("seller_short", None)
        data.pop("product_summary_short", None)
    try:
        return InvoiceExtraction.model_validate(data), None
    except ValidationError as error:
        if not isinstance(data, dict):
            raise
        return _salvage(data, error), error
