"""Orchestrates prompting, JSON parsing, Pydantic validation, and one repair retry.

Never raises on a bad model response: a response that still fails validation
after the repair attempt becomes an all-null InvoiceExtraction carrying a
warning, so a single bad extraction can't crash a batch run.
"""

import json
import re

from pydantic import ValidationError

from invoice_renamer.documents.models import NormalizedDocument
from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.inference.prompts import build_extraction_prompt, build_repair_prompt


def extract_invoice(
    document: NormalizedDocument,
    model: LanguageModel,
    *,
    max_repair_attempts: int = 1,
) -> InvoiceExtraction:
    prompt = build_extraction_prompt(document)
    response = model.generate(prompt)

    attempts = 0
    while True:
        try:
            extraction = _parse(response)
            return extraction.model_copy(
                update={"warnings": [*document.warnings, *extraction.warnings]}
            )
        except (json.JSONDecodeError, ValidationError) as error:
            if attempts >= max_repair_attempts:
                # Includes the raw response (truncated) so a saved report can show
                # what the model actually produced, not just that parsing failed.
                raw = response if len(response) <= 500 else f"{response[:500]}...(truncated)"
                return InvoiceExtraction(
                    warnings=[
                        *document.warnings,
                        f"model output could not be validated: {error}; raw response: {raw!r}",
                    ]
                )
            attempts += 1
            response = model.generate(build_repair_prompt(prompt, response, str(error)))


# Instruct-tuned models commonly wrap JSON in a markdown code fence even when
# told not to; stripping it is more robust than relying on prompt compliance.
_MARKDOWN_JSON_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_markdown_fence(response: str) -> str:
    match = _MARKDOWN_JSON_FENCE.match(response.strip())
    return match.group(1) if match else response


def _parse(response: str) -> InvoiceExtraction:
    data = json.loads(_strip_markdown_fence(response))
    return InvoiceExtraction.model_validate(data)
