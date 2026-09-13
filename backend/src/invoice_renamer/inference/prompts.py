"""Builds the prompts sent to a language model for invoice field extraction."""

from invoice_renamer.documents.models import NormalizedDocument

_FIELD_INSTRUCTIONS = """\
Extract these fields from the invoice text above and return them as a single
strict JSON object with exactly these keys:

- invoice_date: the invoice issue date as YYYY-MM-DD, or null
- seller: the actual seller (not a marketplace or payment processor when
  distinguishable), or null
- product_summary: the main expensive product if one clearly dominates,
  otherwise a short summary; keep it in the invoice's own language and
  preserve brand/model names, or null
- gross_total: the gross total including VAT as a plain number, or null
- currency: the ISO-4217 currency code, or null
- language: "de", "en", or "unknown"
- warnings: a list of short strings describing anything uncertain

Never guess a value you are not confident in - use null instead. Do not
include any text outside the JSON object."""


def build_extraction_prompt(document: NormalizedDocument) -> str:
    pages = "\n\n".join(f"--- Page {page.page_number} ---\n{page.text}" for page in document.pages)
    return f"{pages}\n\n{_FIELD_INSTRUCTIONS}"


def build_repair_prompt(original_prompt: str, previous_response: str, error: str) -> str:
    # generate() is a single stateless call with no guaranteed conversation
    # history, so the invoice text and field instructions must be repeated
    # here rather than assumed to still be available to the model.
    return (
        f"{original_prompt}\n\n"
        "Your previous response was not valid according to the required "
        "schema.\n\nPrevious response:\n"
        f"{previous_response}\n\nValidation error:\n{error}\n\n"
        "Return a corrected, strict JSON object with exactly the same keys, "
        "and no text outside the JSON object."
    )
