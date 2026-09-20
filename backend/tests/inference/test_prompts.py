"""Tests for the extraction prompt's field instructions."""

from invoice_renamer.documents.models import NormalizedDocument, PageText
from invoice_renamer.inference.prompts import (
    build_extraction_prompt,
    build_field_repair_prompt,
    build_shorten_prompt,
)


def _document(text: str) -> NormalizedDocument:
    return NormalizedDocument(
        pages=[PageText(page_number=1, text=text, needs_ocr=False)], warnings=[]
    )


# The literal field-instruction text - build_extraction_prompt's output must
# match this exactly, so a change here is a deliberate wording decision, not
# an accidental drift from the per-field snippets it's built from.
_EXPECTED_FIELD_INSTRUCTIONS = """\
Extract these fields from the invoice text above and return them as a single
strict JSON object with exactly these keys:

- invoice_date: the invoice issue date, REWRITTEN as YYYY-MM-DD - convert
  the invoice's own date format, don't copy it verbatim (e.g. 07.07.2026
  becomes 2026-07-07; "August 2, 2026" becomes 2026-08-02; 01-Sep-2026
  becomes 2026-09-01), or null
- seller: the actual seller (not a marketplace or payment processor when
  distinguishable), or null. Becomes part of a filename: use only letters,
  digits, hyphens, and underscores - no spaces, quotes, slashes, colons,
  parentheses, or other punctuation, and keep it as short as possible
  (e.g. "Müller & Söhne GmbH" becomes "Mueller-Soehne-GmbH")
- product_summary: the main expensive product if one clearly dominates,
  otherwise a short summary; keep it in the invoice's own language and
  preserve brand/model names, or null. Keep it short (a few words).
  Becomes part of a filename: use only letters, digits, hyphens, and
  underscores - no spaces, quotes, slashes, colons, parentheses, or other
  punctuation (e.g. "MacBook Air" becomes "MacBook-Air")
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
  labeled, use null - never substitute the net amount
- currency: the ISO-4217 currency code, or null
- language: "de", "en", or "unknown"

Never guess a value you are not confident in - use null instead. Do not add
any keys beyond the ones listed above. Respond with the raw JSON object
only: no markdown code fences, no surrounding text."""


def test_extraction_prompt_matches_the_current_wording() -> None:
    prompt = build_extraction_prompt(_document("irrelevant"))

    assert prompt == f"--- Page 1 ---\nirrelevant\n\n{_EXPECTED_FIELD_INSTRUCTIONS}"


def test_gross_total_instruction_warns_against_the_net_amount() -> None:
    prompt = build_extraction_prompt(_document("irrelevant"))

    assert "Nettobetrag" in prompt
    assert "Rechnungsbetrag" in prompt
    assert "net" in prompt.casefold()


def test_gross_total_instruction_does_not_permit_a_net_fallback() -> None:
    prompt = build_extraction_prompt(_document("irrelevant"))

    assert "fall back to a net amount" not in prompt.casefold()
    assert "use null" in prompt.casefold()


def test_prompt_does_not_request_warnings_from_the_model() -> None:
    prompt = " ".join(build_extraction_prompt(_document("irrelevant")).casefold().split())

    assert "warnings" not in prompt
    assert "do not add any keys" in prompt


def test_product_summary_instruction_asks_for_short_text() -> None:
    prompt = " ".join(build_extraction_prompt(_document("irrelevant")).casefold().split())

    assert "keep it short" in prompt


def test_seller_and_product_summary_are_restricted_to_filename_safe_characters() -> None:
    prompt = " ".join(build_extraction_prompt(_document("irrelevant")).casefold().split())

    assert prompt.count("becomes part of a filename") == 2  # once per field, not a shared note
    assert "macbook-air" in prompt
    assert "mueller-soehne-gmbh" in prompt


def test_shorten_prompt_includes_both_given_fields() -> None:
    prompt = build_shorten_prompt("Amazon EU S.a r.l.", "Galaxy Projektor 13 in 1")

    assert "Amazon EU S.a r.l." in prompt
    assert "Galaxy Projektor 13 in 1" in prompt


def test_shorten_prompt_asks_for_the_short_json_keys() -> None:
    prompt = build_shorten_prompt("Amazon", "Galaxy Projektor")

    assert "seller_short" in prompt
    assert "product_short" in prompt


def test_shorten_prompt_forbids_translation() -> None:
    prompt = build_shorten_prompt("Amazon", "Galaxy Projektor").casefold()

    assert "do not translate" in prompt


def test_field_repair_prompt_includes_only_the_rejected_fields_instructions() -> None:
    prompt = build_field_repair_prompt(
        _document("irrelevant"), {"currency": ("DE", "not a valid currency code")}
    )

    assert "ISO-4217 currency code" in prompt
    assert "the actual seller" not in prompt
    assert "REWRITTEN as YYYY-MM-DD" not in prompt


def test_field_repair_prompt_shows_the_previous_value_and_reason() -> None:
    prompt = build_field_repair_prompt(
        _document("irrelevant"), {"currency": ("DE", "not a valid currency code")}
    )

    assert '"DE"' in prompt
    assert "not a valid currency code" in prompt


def test_field_repair_prompt_shows_a_plain_message_when_there_is_no_previous_value() -> None:
    # A field that came back null on a fully-valid response has nothing
    # rejected to show - only the message ("no value found...") applies.
    prompt = build_field_repair_prompt(
        _document("irrelevant"), {"gross_total": (None, "no value found - look again")}
    )

    assert "no value found - look again" in prompt
    assert "Previous attempt" not in prompt


def test_field_repair_prompt_disambiguates_currency() -> None:
    prompt = build_field_repair_prompt(_document("irrelevant"), {"currency": ("DE", "bad")})

    assert "not a language or country code" in prompt.casefold()


def test_field_repair_prompt_omits_currency_disambiguation_when_currency_is_not_rejected() -> None:
    prompt = build_field_repair_prompt(
        _document("irrelevant"), {"seller": (["bad"], "not a string")}
    )

    assert "not a language or country code" not in prompt.casefold()


def test_field_repair_prompt_safely_renders_a_non_string_previous_value() -> None:
    prompt = build_field_repair_prompt(
        _document("irrelevant"), {"seller": (["bad", "value"], "not a string")}
    )

    assert '["bad", "value"]' in prompt


def test_field_repair_prompt_asks_for_multiple_rejected_fields_in_one_prompt() -> None:
    prompt = build_field_repair_prompt(
        _document("irrelevant"),
        {"currency": ("DE", "bad"), "seller": (["bad"], "not a string")},
    )

    assert "ISO-4217 currency code" in prompt
    assert "the actual seller" in prompt


def test_field_repair_prompt_includes_the_invoice_text() -> None:
    prompt = build_field_repair_prompt(_document("Seller: Acme Corp"), {"currency": ("DE", "bad")})

    assert "Seller: Acme Corp" in prompt
    assert "--- Page 1 ---" in prompt


def test_field_repair_prompt_reuses_the_shared_footer() -> None:
    prompt = build_field_repair_prompt(_document("irrelevant"), {"currency": ("DE", "bad")})

    assert "no markdown code fences, no surrounding text" in prompt


def test_field_repair_prompt_for_currency_alone_never_mentions_seller_or_product_summary() -> None:
    # Regression: the filename-safety note used to live in a shared footer
    # sent with every repair prompt, naming seller/product_summary even when
    # they weren't being asked about - a real qwen3-0.6b run volunteered them
    # anyway in a currency-only retry reply, plausibly primed by that mention.
    # The note now lives in each field's own bullet, so it only appears when
    # that field is actually being asked about.
    prompt = build_field_repair_prompt(
        _document("irrelevant"), {"currency": ("DE", "not a valid currency code")}
    ).casefold()

    assert "seller" not in prompt
    assert "product_summary" not in prompt
    assert "becomes part of a filename" not in prompt
