"""Builds the prompts sent to a language model for invoice field extraction."""

import json

from invoice_renamer.documents.models import NormalizedDocument

# Order the full extraction prompt lists fields in; also the only field names
# the narrow repair prompt (build_field_repair_prompt) will ever re-ask for.
FIELD_NAMES: tuple[str, ...] = (
    "invoice_date",
    "seller",
    "product_summary",
    "gross_total",
    "currency",
    "language",
)

# Per-field instruction snippets, keyed so both the full extraction prompt and
# the narrow repair prompt build from the same source text instead of two
# driftable copies.
_FIELD_INSTRUCTIONS: dict[str, str] = {
    "invoice_date": """\
- invoice_date: the invoice issue date, REWRITTEN as YYYY-MM-DD - convert
  the invoice's own date format, don't copy it verbatim (e.g. 07.07.2026
  becomes 2026-07-07; "August 2, 2026" becomes 2026-08-02; 01-Sep-2026
  becomes 2026-09-01), or null""",
    "seller": """\
- seller: the actual seller (not a marketplace or payment processor when
  distinguishable), or null. Becomes part of a filename: use only letters,
  digits, hyphens, and underscores - no spaces, quotes, slashes, colons,
  parentheses, or other punctuation, and keep it as short as possible
  (e.g. "Müller & Söhne GmbH" becomes "Mueller-Soehne-GmbH")""",
    "product_summary": """\
- product_summary: the main expensive product if one clearly dominates,
  otherwise a short summary; keep it in the invoice's own language and
  preserve brand/model names, or null. Keep it short (a few words).
  Becomes part of a filename: use only letters, digits, hyphens, and
  underscores - no spaces, quotes, slashes, colons, parentheses, or other
  punctuation (e.g. "MacBook Air" becomes "MacBook-Air")""",
    "gross_total": """\
- gross_total: the final invoice total the customer must pay, including VAT,
  as a plain number. KEEP THE DECIMAL POINT: "21.42" stays 21.42, never
  2142. "37.46" stays 37.46, never 3746. This is the one exception to the
  no-punctuation rule used for seller/product_summary text - it doesn't
  apply here, and the decimal point is required. Convert only if the
  invoice itself uses a different format: German-style "1.234,56" becomes
  1234.56. Look for a line labeled "Rechnungsbetrag", "Gesamtbetrag",
  "Total", "Invoice Total", or "Amount Due". Do NOT return a net/pre-tax
  amount (labeled e.g. "Nettobetrag", "Net Amount", "Subtotal") - that is a
  different, smaller number on the same invoice. If no gross total is
  labeled, use null - never substitute the net amount""",
    "currency": "- currency: the ISO-4217 currency code, or null",
    "language": '- language: "de", "en", or "unknown"',
}

# Extra line shown only on the narrow repair prompt, for fields where a small
# model's mistake pattern (observed on a real qwen3-0.6b run) is plausible
# enough to call out explicitly.
_REPAIR_DISAMBIGUATION: dict[str, str] = {
    "currency": (
        "  This is NOT a language or country code - it is a 3-letter "
        "ISO-4217 currency code (e.g. EUR, USD, GBP). Do not repeat the "
        "language field's value here."
    ),
}

_INTRO = """\
Extract these fields from the invoice text above and return them as a single
strict JSON object with exactly these keys:
"""

_REPAIR_INTRO = """\
Extract only the field(s) below from the invoice text above. Return a
single strict JSON object with exactly these key(s):
"""

_FOOTER = """\
Never guess a value you are not confident in - use null instead. Do not add
any keys beyond the ones listed above. Respond with the raw JSON object
only: no markdown code fences, no surrounding text."""


def _pages_text(document: NormalizedDocument) -> str:
    return "\n\n".join(f"--- Page {page.page_number} ---\n{page.text}" for page in document.pages)


def build_extraction_prompt(document: NormalizedDocument) -> str:
    bullets = "\n".join(_FIELD_INSTRUCTIONS[name] for name in FIELD_NAMES)
    body = f"{_INTRO}\n{bullets}\n\n{_FOOTER}"
    return f"{_pages_text(document)}\n\n{body}"


def _field_repair_block(name: str, previous_value: object, message: str) -> str:
    # previous_value is None both for a field the model never got a chance to
    # be wrong about (still-null-after-a-valid-response) and, in principle,
    # for a rejected value that happened to be None - but pydantic never
    # rejects None for these fields (all are optional), so in practice this
    # only distinguishes "nothing to show" from "here's what it got wrong".
    lines = [_FIELD_INSTRUCTIONS[name]]
    disambiguation = _REPAIR_DISAMBIGUATION.get(name)
    if disambiguation is not None:
        lines.append(disambiguation)
    if previous_value is None:
        lines.append(f"  {message}")
    else:
        lines.append(f"  Previous attempt: {json.dumps(previous_value)} - {message}")
    return "\n".join(lines)


def build_field_repair_prompt(
    document: NormalizedDocument, fields: dict[str, tuple[object, str]]
) -> str:
    """Narrow repair prompt re-asking only specific field(s), instead of
    resending the full field list and every already-good field along with it.
    Used both for a field pydantic rejected (previous value + validation
    message shown) and for a field a fully-valid response still left null
    (previous value is None; message explains there's nothing to show)."""
    blocks = "\n".join(
        _field_repair_block(name, *fields[name]) for name in FIELD_NAMES if name in fields
    )
    body = f"{_REPAIR_INTRO}\n{blocks}\n\n{_FOOTER}"
    return f"{_pages_text(document)}\n\n{body}"


_SHORTEN_INSTRUCTIONS = """\
You shorten invoice fields that were already extracted by another process,
for use in a filename. You are not extracting from the original invoice
text - only compressing the two fields given above.

Return a single strict JSON object with exactly these keys:

- seller_short: the brand/company name only. Strip legal suffixes (GmbH,
  S.a r.l., Inc., Ltd., AG, & Co. KG) and marketplace/entity boilerplate.
  1-2 words. null if seller above is null.
- product_short: the core product only. Strip marketing copy, dimensions,
  compatibility lists, and feature bullets. Keep brand/model names if
  present. 2-4 words. null if product_summary above is null.

Keep the original language - do not translate. Never invent details that
aren't in the input. Respond with the raw JSON object only: no markdown
code fences, no surrounding text."""


def build_shorten_prompt(seller: str | None, product_summary: str | None) -> str:
    fields = f"seller: {seller}\nproduct_summary: {product_summary}"
    return f"{fields}\n\n{_SHORTEN_INSTRUCTIONS}"


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
