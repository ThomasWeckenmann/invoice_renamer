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
  preserve brand/model names, or null. Keep it short (a few words)
- gross_total: the final invoice total the customer must pay, including VAT,
  as a plain number. KEEP THE DECIMAL POINT: "21.42" stays 21.42, never
  2142. "37.46" stays 37.46, never 3746. This is the one exception to the
  no-punctuation rule below - that rule is for seller/product_summary text,
  not for this number, and the decimal point here is required. Convert
  only if the invoice itself uses a different format: German-style
  "1.234,56" becomes 1234.56. Look for a line labeled "Rechnungsbetrag",
  "Gesamtbetrag", "Total", "Invoice Total", or "Amount Due". Do NOT return
  a net/pre-tax amount (labeled e.g. "Nettobetrag", "Net Amount",
  "Subtotal") - that is a different, smaller number on the same invoice.
  If no gross total is labeled, use null - never substitute the net amount
- currency: the ISO-4217 currency code, or null
- language: "de", "en", or "unknown"

seller and product_summary become part of a filename: use only letters,
digits, hyphens, and underscores - no spaces, quotes, slashes, colons,
parentheses, or other punctuation (e.g. "MacBook Air" becomes
"MacBook-Air", "Müller & Söhne GmbH" becomes "Mueller-Soehne-GmbH").
Also make it as short as possible.

Never guess a value you are not confident in - use null instead. Do not add
any keys beyond the ones listed above. Respond with the raw JSON object
only: no markdown code fences, no surrounding text."""


def build_extraction_prompt(document: NormalizedDocument) -> str:
    pages = "\n\n".join(f"--- Page {page.page_number} ---\n{page.text}" for page in document.pages)
    return f"{pages}\n\n{_FIELD_INSTRUCTIONS}"


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
