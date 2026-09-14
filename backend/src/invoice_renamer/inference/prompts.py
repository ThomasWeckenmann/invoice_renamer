"""Builds the prompts sent to a language model for invoice field extraction."""

from invoice_renamer.documents.models import NormalizedDocument

_FIELD_INSTRUCTIONS = """\
Extract these fields from the invoice text above and return them as a single
strict JSON object with exactly these keys:

- invoice_date: the invoice issue date, REWRITTEN as YYYY-MM-DD - convert
  the invoice's own date format, don't copy it verbatim (e.g. 07.07.2026
  becomes 2026-07-07; "August 2, 2026" becomes 2026-08-02; 01-Sep-2026
  becomes 2026-09-01), or null
- seller: the actual seller (not a marketplace or payment processor when
  distinguishable), or null
- product_summary: the main expensive product if one clearly dominates,
  otherwise a short summary; keep it in the invoice's own language and
  preserve brand/model names, or null
- gross_total: the final invoice total the customer must pay, including VAT,
  as a plain number - look for a line labeled "Rechnungsbetrag", "Gesamtbetrag",
  "Total", "Invoice Total", or "Amount Due". Do NOT return a net/pre-tax amount
  (labeled e.g. "Nettobetrag", "Net Amount", "Subtotal") - that is a different,
  smaller number on the same invoice. If no gross total is labeled, use null -
  never substitute the net amount
- currency: the ISO-4217 currency code, or null
- language: "de", "en", or "unknown"
- warnings: a list of short strings describing anything uncertain

Never guess a value you are not confident in - use null instead. Respond
with the raw JSON object only: no markdown code fences, no surrounding
text."""


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
        "Return a corrected, strict JSON object with exactly the same keys. "
        "No markdown code fences, no surrounding text."
    )
