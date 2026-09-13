"""Orchestrates prompting, JSON parsing, Pydantic validation, and one repair retry.

Never raises on a bad model response: a response that still fails validation
after the repair attempt becomes an all-null InvoiceExtraction carrying a
warning, so a single bad extraction can't crash a batch run.
"""

import json

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
                return InvoiceExtraction(
                    warnings=[*document.warnings, f"model output could not be validated: {error}"]
                )
            attempts += 1
            response = model.generate(build_repair_prompt(prompt, response, str(error)))


def _parse(response: str) -> InvoiceExtraction:
    data = json.loads(response)
    return InvoiceExtraction.model_validate(data)
