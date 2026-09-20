"""Builds a proposed filename from a validated invoice extraction.

Format: YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf
"""

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.naming.schema import FilenameProposal

# Keeps proposals usable across filesystems; still fully editable before rename.
_MAX_STEM_LENGTH = 150

_MISSING_DATE = "0000-00-00"
_MISSING_SEGMENT = "Unknown"
_MISSING_CURRENCY = "XXX"

_GERMAN_TRANSLITERATIONS = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
}

# Whitelist rather than a forbidden-char blocklist: the model is prompted to
# avoid punctuation but can't be relied on to comply, so filename-safety is
# enforced deterministically here instead.
_ALLOWED_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]")
_WHITESPACE_RE = re.compile(r"\s+")
_MULTI_DASH_RE = re.compile(r"-{2,}")


def _transliterate_german(text: str) -> str:
    for source, replacement in _GERMAN_TRANSLITERATIONS.items():
        text = text.replace(source, replacement)
    return text


def _to_ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def _normalize_segment(text: str | None) -> tuple[str, bool]:
    """Returns (filename-safe segment, whether the field was missing)."""
    if text is None or not text.strip():
        return _MISSING_SEGMENT, True

    normalized = _to_ascii(_transliterate_german(text))
    normalized = _WHITESPACE_RE.sub("-", normalized.strip())
    normalized = _ALLOWED_CHARS_RE.sub("", normalized)
    normalized = _MULTI_DASH_RE.sub("-", normalized).strip("-")
    return (normalized, False) if normalized else (_MISSING_SEGMENT, True)


def _format_date(value: date | None) -> tuple[str, bool]:
    if value is None:
        return _MISSING_DATE, True
    return value.isoformat(), False


def _round_amount(value: Decimal | None) -> tuple[str, bool]:
    if value is None:
        return "0", True
    rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(rounded), False


def _truncated_fields(
    date_part: str, seller_part: str, product_part: str, max_prefix_length: int
) -> list[str]:
    """Which of date/seller/product survive only partially, or not at all, once
    the combined prefix is cut to max_prefix_length - assumes truncation is
    already known to happen (the caller checks the untruncated length first)."""
    date_end = len(date_part)
    seller_end = date_end + 1 + len(seller_part)
    if max_prefix_length >= seller_end:
        return ["product"]
    if max_prefix_length >= date_end:
        return ["seller", "product"]
    return ["date", "seller", "product"]


def build_filename_proposal(extraction: InvoiceExtraction) -> FilenameProposal:
    date_part, date_missing = _format_date(extraction.invoice_date)
    seller_part, seller_missing = _normalize_segment(extraction.seller_short or extraction.seller)
    product_part, product_missing = _normalize_segment(
        extraction.product_summary_short or extraction.product_summary
    )
    amount_part, amount_missing = _round_amount(extraction.gross_total)
    currency_missing = extraction.currency is None
    currency_part = extraction.currency or _MISSING_CURRENCY

    # The amount/currency suffix is always kept whole; only the date/seller/product
    # prefix is truncated to fit the overall length budget, so a long product name
    # can never push the amount or currency out of the proposed filename.
    suffix = f"_{amount_part}-{currency_part}"
    prefix = f"{date_part}_{seller_part}_{product_part}"
    max_prefix_length = max(0, _MAX_STEM_LENGTH - len(suffix))
    warnings: list[str] = []
    if len(prefix) > max_prefix_length:
        truncated = _truncated_fields(date_part, seller_part, product_part, max_prefix_length)
        warnings.append(
            f"{'/'.join(truncated)} truncated to fit the {_MAX_STEM_LENGTH}-character "
            "filename limit"
        )
        prefix = prefix[:max_prefix_length].rstrip("_-")

    stem = f"{prefix}{suffix}"

    missing_fields = [
        name
        for name, missing in (
            ("date", date_missing),
            ("seller", seller_missing),
            ("product", product_missing),
            ("amount", amount_missing),
            ("currency", currency_missing),
        )
        if missing
    ]
    requires_review = bool(missing_fields) or bool(extraction.warnings) or bool(warnings)

    return FilenameProposal(
        extraction=extraction,
        proposed_filename=f"{stem}.pdf",
        requires_review=requires_review,
        missing_fields=missing_fields,
        warnings=warnings,
    )
