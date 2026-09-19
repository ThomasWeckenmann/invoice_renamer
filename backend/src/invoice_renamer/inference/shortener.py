"""Runs a second, focused model call to compress an already-extracted seller/
product_summary into short, filename-friendly text, stored separately as
seller_short/product_summary_short so the original values survive alongside it.

Never raises on a bad model response and never discards already-good data: a
response that still fails validation after the repair attempt leaves the short
fields unset, with a warning noting the skip.
"""

import json
import re

from pydantic import BaseModel, ValidationError

from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.inference.prompts import build_repair_prompt, build_shorten_prompt

_MARKDOWN_JSON_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


class _ShortenedFields(BaseModel):
    seller_short: str | None = None
    product_short: str | None = None


def _has_content(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def shorten_fields(
    extraction: InvoiceExtraction,
    model: LanguageModel,
    *,
    max_repair_attempts: int = 1,
) -> InvoiceExtraction:
    if extraction.seller is None and extraction.product_summary is None:
        return extraction

    prompt = build_shorten_prompt(extraction.seller, extraction.product_summary)
    response = model.generate(prompt)

    attempts = 0
    while True:
        try:
            shortened = _parse(response)
            return extraction.model_copy(
                update={
                    # Never accept a short value when the original is blank/absent
                    # (a model that hallucinates one despite that must not bypass
                    # missing_fields/requires_review with invented text), and never
                    # accept a blank/whitespace-only short value either (it would
                    # win the `seller_short or seller` fallback in naming/builder.py
                    # since a non-empty whitespace string is truthy, silently
                    # replacing a perfectly good original with "Unknown").
                    "seller_short": (
                        shortened.seller_short
                        if _has_content(extraction.seller) and _has_content(shortened.seller_short)
                        else None
                    ),
                    "product_summary_short": (
                        shortened.product_short
                        if _has_content(extraction.product_summary)
                        and _has_content(shortened.product_short)
                        else None
                    ),
                }
            )
        except (json.JSONDecodeError, ValidationError) as error:
            if attempts >= max_repair_attempts:
                return extraction.model_copy(
                    update={
                        "warnings": [
                            *extraction.warnings,
                            f"field shortening skipped: {error}",
                        ]
                    }
                )
            attempts += 1
            response = model.generate(build_repair_prompt(prompt, response, str(error)))


def _strip_markdown_fence(response: str) -> str:
    match = _MARKDOWN_JSON_FENCE.match(response.strip())
    return match.group(1) if match else response


def _parse(response: str) -> _ShortenedFields:
    data = json.loads(_strip_markdown_fence(response))
    return _ShortenedFields.model_validate(data)
