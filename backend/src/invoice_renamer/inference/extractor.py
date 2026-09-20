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
from invoice_renamer.inference.prompts import (
    FIELD_NAMES,
    build_extraction_prompt,
    build_field_repair_prompt,
    build_repair_prompt,
)


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
            # Fully valid - but a useful field can still be null (a
            # deliberate "I'm not confident" answer, not an error). Ask about
            # those too, narrowly, with whatever attempt budget remains.
            final, _ = _repair_fields(
                extraction,
                document,
                model,
                _null_fields_for_retry(extraction),
                attempts=attempts,
                max_repair_attempts=max_repair_attempts,
            )
            return _merge_document_warnings(document.warnings, final)

        # Something was dropped to get here - keep it only if it's at least
        # as useful as anything salvaged already.
        if salvaged_fallback is None or _usable_field_count(extraction) > _usable_field_count(
            salvaged_fallback
        ):
            salvaged_fallback = extraction

        if attempts >= max_repair_attempts:
            return _merge_document_warnings(document.warnings, salvaged_fallback)

        rejected = _rejected_fields_for_repair(salvage_error)
        # Combine what pydantic rejected with any other useful field that's
        # still null - one narrow call can recover both kinds at once instead
        # of the null ones being silently skipped because the sole repair
        # attempt went to the rejected field. `rejected` wins on overlap: a
        # field that's both null and rejected has a real previous value and
        # reason to show, which is more useful than the generic null message.
        askable = {**_null_fields_for_retry(salvaged_fallback), **rejected}
        if not askable:
            # Nothing the model was ever asked to supply is askable here (the
            # only rejection was e.g. evidence) - fall back to the full
            # prompt, which is re-parsed as a complete extraction below.
            attempts += 1
            try:
                response = model.generate(build_repair_prompt(prompt, response, str(salvage_error)))
            except Exception:
                return _merge_document_warnings(document.warnings, salvaged_fallback)
            continue

        salvaged_fallback, _ = _repair_fields(
            salvaged_fallback,
            document,
            model,
            askable,
            attempts=attempts,
            max_repair_attempts=max_repair_attempts,
        )
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


def _rejected_fields_for_repair(error: ValidationError) -> dict[str, tuple[object, str]]:
    """Maps each rejected field the model was actually asked to supply to its
    (previous rejected value, validation message), for the narrow repair
    prompt. A rejected evidence field is excluded - the model was never asked
    to supply it, so there's nothing meaningful to ask it to correct."""
    rejected: dict[str, tuple[object, str]] = {}
    for detail in error.errors():
        loc = detail["loc"]
        field_name = loc[0] if loc else None
        if not isinstance(field_name, str) or field_name not in FIELD_NAMES:
            continue
        rejected.setdefault(field_name, (detail["input"], str(detail["msg"])))
    return rejected


# A null is a legitimate, deliberate model answer ("I'm not confident, so I
# won't guess"), not an error - so it gets a different message than a real
# validation failure, and previous_value is always None (there's nothing to
# show the model it got wrong, only that nothing came back).
_NOT_FOUND_MESSAGE = (
    "no value found - it may be labeled differently or appear elsewhere in the text above"
)


def _null_fields_for_retry(extraction: InvoiceExtraction) -> dict[str, tuple[object, str]]:
    """Which of the 5 useful fields a fully-valid response still left null. A
    0 amount is a real, meaningful value (not "no answer"), so - like
    everywhere else in this module - _has_usable_value's None/blank-string
    check is what decides what counts as missing here, not falsiness."""
    return {
        name: (None, _NOT_FOUND_MESSAGE)
        for name in _USEFUL_SALVAGE_FIELDS
        if not _has_usable_value(getattr(extraction, name))
    }


def _validate_and_merge(
    base: InvoiceExtraction, data: dict[str, object], field_names: dict[str, tuple[object, str]]
) -> tuple[InvoiceExtraction, frozenset[str]]:
    """Validates each of field_names independently against `data` (where
    present) and merges every field that validates with a usable value onto
    `base`. Fields are validated one at a time - deliberately not as one
    combined InvoiceExtraction - so one field's rejection can never discard a
    sibling field's valid recovery in the same response. A field outside
    field_names is never read from `data`, so a reply that includes an
    unsolicited field (whether a compliant narrow answer or not) can never
    overwrite a field the model wasn't asked about. Returns (merged, recovered
    field names) - the caller uses the latter to stop re-asking about a field
    that already came back good."""
    updates: dict[str, object] = {}
    for name in field_names:
        if name not in data:
            continue
        try:
            validated = InvoiceExtraction.model_validate({name: data[name]})
        except ValidationError:
            continue
        value = getattr(validated, name)
        if _has_usable_value(value):
            updates[name] = value
    if not updates:
        return base, frozenset()
    # A field that just recovered no longer needs its salvage warning (e.g.
    # "currency: not a valid ISO 4217 code") hanging around as a stale caveat.
    remaining_warnings = [
        warning
        for warning in base.warnings
        if not any(warning.startswith(f"{name}: ") for name in updates)
    ]
    merged = base.model_copy(update={**updates, "warnings": remaining_warnings})
    return merged, frozenset(updates)


def _repair_fields(
    extraction: InvoiceExtraction,
    document: NormalizedDocument,
    model: LanguageModel,
    askable_fields: dict[str, tuple[object, str]],
    *,
    attempts: int,
    max_repair_attempts: int,
) -> tuple[InvoiceExtraction, int]:
    """Repeatedly asks only for the given field(s) - each one either a value
    pydantic rejected (previous value + validation message) or a still-null
    useful field on an otherwise-valid response (previous value None, a
    generic "not found" message) - merging every field that validates onto
    `extraction` rather than treating the reply as a wholesale new one. A
    compliant model's reply to this narrow a prompt only contains the
    field(s) asked for, so parsing it as a full extraction would wipe every
    other already-good field back to null (observed directly: qwen3-0.6b
    found a date and a total on a narrow retry it had returned null for on
    the first, 6-field pass). Bounded by the remaining attempt budget; a
    field that recovers is dropped from what's asked for on the next round,
    and any failure along the way (bad JSON, a generation error, a
    still-invalid value) simply leaves `extraction` as it was and, if budget
    remains, tries again for whatever is still missing."""
    while askable_fields and attempts < max_repair_attempts:
        attempts += 1
        try:
            response = model.generate(build_field_repair_prompt(document, askable_fields))
            data = json.loads(_strip_markdown_fence(response))
        except Exception:
            continue
        if isinstance(data, dict):
            extraction, recovered = _validate_and_merge(extraction, data, askable_fields)
            askable_fields = {
                name: value for name, value in askable_fields.items() if name not in recovered
            }
    return extraction, attempts


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
