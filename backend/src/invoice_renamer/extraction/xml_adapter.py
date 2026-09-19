"""Maps a supported, already-classified ZUGFeRD/Factur-X CII XML candidate into
validated InvoiceExtraction fields. Never sent to the model - this is pure,
deterministic parsing of a namespace-qualified document tree.

Field semantics (verified against the CII D16B / EN16931 schema and the sample
Factur-X invoice in fixtures/ZUGFeRD-Example.pdf):
- Issue date: rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString, only
  format="102" (CCYYMMDD, UN/CEFACT code list 2379) is supported.
- Seller name: .../ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty/ram:Name.
- Currency: .../ram:ApplicableHeaderTradeSettlement/ram:InvoiceCurrencyCode.
- Gross total: .../ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount,
  which is TaxBasisTotalAmount + TaxTotalAmount - the invoice total including VAT.
  This is deliberately not ram:DuePayableAmount, which subtracts any prepayment and
  so can differ from the invoice's actual gross total.
- Product summary: the single line item's product name, or the dominant item's name
  when one item's line total is at least double the runner-up's; otherwise left for
  model fallback rather than guessed.

Every field is independent CII schema-wise, allows at most one occurrence (maxOccurs=1
in the real schema), so more than one match at a field's path means the document
doesn't conform to the profile it claims - that field is rejected with a warning
rather than silently using whichever match came first.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Generic, TypeVar
from xml.etree.ElementTree import Element

from invoice_renamer.documents.xml_attachments import NAMESPACES, XmlCandidate
from invoice_renamer.extraction.models import Evidence, InvoiceExtraction
from invoice_renamer.extraction.validators import validate_iso4217_currency

_ISSUE_DATE_PATH = "rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"
_SELLER_NAME_PATH = (
    "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement"
    "/ram:SellerTradeParty/ram:Name"
)
_CURRENCY_PATH = (
    "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement/ram:InvoiceCurrencyCode"
)
_GROSS_TOTAL_PATH = (
    "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement"
    "/ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount"
)
_LINE_ITEMS_PATH = "rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem"
_LINE_ITEM_NAME_PATH = "ram:SpecifiedTradeProduct/ram:Name"
_LINE_ITEM_TOTAL_PATH = (
    "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation"
    "/ram:LineTotalAmount"
)

_SUPPORTED_DATE_FORMAT_CODE = "102"
_DATE_FORMAT_102 = "%Y%m%d"

_DECIMAL_RE = re.compile(r"^-?\d+(\.\d+)?$")

# Far beyond any real invoice amount, and safely under Decimal's default 28-digit
# context precision - so a value that passes this can never later overflow
# naming/builder.py's quantize() when the filename is built.
_MAX_AMOUNT = Decimal("999999999999999999")

# No real seller/product/date/currency value is anywhere near this long; a longer
# one is implausible input, not a value worth guessing at.
_MAX_FIELD_TEXT_LENGTH = 500

# _MAX_AMOUNT's 18 digits, plus a sign and a decimal point, come nowhere close to
# this. Checked before regex matching or Decimal construction, so a pathological
# amount (hundreds of thousands of digits) is rejected without spending time
# parsing it or building a value just to discard it on magnitude afterward.
_MAX_AMOUNT_TEXT_LENGTH = 32

# Rejected fields echo the XML text that caused the rejection so a reviewer can
# see what was wrong; that echo itself must stay short regardless of how long the
# original value was - this bounds every such echo, not just amounts.
_MAX_WARNING_VALUE_CHARS = 50

# A line item's amount must be at least this many times its runner-up's to count as
# an unambiguous, single dominant product for the filename - see module docstring.
_DOMINANCE_RATIO = 2


def _text(element: Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _bounded(text: str | None, field_name: str) -> tuple[str | None, str | None]:
    """Returns (text, warning). None passes through with no warning - a field
    simply being absent is the normal, silent fallback case. Only implausibly
    long text is rejected, since none of these fields are ever legitimately
    anywhere near _MAX_FIELD_TEXT_LENGTH."""
    if text is None:
        return None, None
    if len(text) > _MAX_FIELD_TEXT_LENGTH:
        return (
            None,
            f"{field_name}: XML value exceeds {_MAX_FIELD_TEXT_LENGTH} characters; falling back",
        )
    return text, None


def _truncate_for_warning(text: str | None) -> str | None:
    if text is None:
        return None
    return (
        text if len(text) <= _MAX_WARNING_VALUE_CHARS else f"{text[:_MAX_WARNING_VALUE_CHARS]}..."
    )


def _find_unique(root: Element, path: str) -> tuple[Element | None, bool]:
    """Returns (element, ambiguous). ambiguous=True means more than one element
    matched a path the CII schema allows at most once - the document doesn't
    conform to the profile it claims, so the field must be rejected rather than
    silently using whichever match happens to come first."""
    matches = root.findall(path, NAMESPACES)
    if not matches:
        return None, False
    if len(matches) > 1:
        return None, True
    return matches[0], False


def _parse_decimal(text: str) -> Decimal | None:
    if len(text) > _MAX_AMOUNT_TEXT_LENGTH:
        return None
    if not _DECIMAL_RE.match(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    if abs(value) > _MAX_AMOUNT:
        return None
    return value


_T = TypeVar("_T")


@dataclass
class _FieldResult(Generic[_T]):
    value: _T | None
    xml_path: str | None
    warning: str | None


def _extract_issue_date(root: Element) -> _FieldResult[date]:
    element, ambiguous = _find_unique(root, _ISSUE_DATE_PATH)
    if ambiguous:
        return _FieldResult(
            None, None, "invoice_date: multiple conflicting XML issue dates; falling back"
        )
    text, warning = _bounded(_text(element), "invoice_date")
    if warning is not None:
        return _FieldResult(None, None, warning)
    if element is None or text is None:
        return _FieldResult(None, None, None)

    format_code = element.get("format")
    if format_code != _SUPPORTED_DATE_FORMAT_CODE:
        return _FieldResult(
            None,
            None,
            f"invoice_date: unsupported XML date format "
            f"{_truncate_for_warning(format_code)!r}; falling back",
        )

    try:
        parsed = datetime.strptime(text, _DATE_FORMAT_102).date()
    except ValueError:
        return _FieldResult(
            None,
            None,
            f"invoice_date: unparseable XML date {_truncate_for_warning(text)!r}; falling back",
        )

    return _FieldResult(parsed, _ISSUE_DATE_PATH, None)


def _extract_seller(root: Element) -> _FieldResult[str]:
    element, ambiguous = _find_unique(root, _SELLER_NAME_PATH)
    if ambiguous:
        return _FieldResult(
            None, None, "seller: multiple conflicting XML seller names; falling back"
        )
    text, warning = _bounded(_text(element), "seller")
    if warning is not None:
        return _FieldResult(None, None, warning)
    if text is None:
        return _FieldResult(None, None, None)
    return _FieldResult(text, _SELLER_NAME_PATH, None)


def _extract_currency(root: Element) -> _FieldResult[str]:
    element, ambiguous = _find_unique(root, _CURRENCY_PATH)
    if ambiguous:
        return _FieldResult(
            None, None, "currency: multiple conflicting XML currency codes; falling back"
        )
    text, warning = _bounded(_text(element), "currency")
    if warning is not None:
        return _FieldResult(None, None, warning)
    if text is None:
        return _FieldResult(None, None, None)
    try:
        validated = validate_iso4217_currency(text)
    except ValueError:
        return _FieldResult(
            None,
            None,
            f"currency: invalid XML currency code {_truncate_for_warning(text)!r}; falling back",
        )
    return _FieldResult(validated, _CURRENCY_PATH, None)


def _extract_gross_total(root: Element) -> _FieldResult[Decimal]:
    element, ambiguous = _find_unique(root, _GROSS_TOTAL_PATH)
    if ambiguous:
        return _FieldResult(
            None, None, "gross_total: multiple conflicting XML grand totals; falling back"
        )
    text = _text(element)
    if text is None:
        return _FieldResult(None, None, None)
    value = _parse_decimal(text)
    if value is None:
        return _FieldResult(
            None,
            None,
            f"gross_total: invalid or out-of-range XML amount "
            f"{_truncate_for_warning(text)!r}; falling back",
        )
    if value < 0:
        return _FieldResult(
            None,
            None,
            f"gross_total: XML grand total {_truncate_for_warning(text)!r} is negative; "
            "falling back",
        )
    return _FieldResult(value, _GROSS_TOTAL_PATH, None)


@dataclass
class _LineItem:
    # None means "this line item exists but the value couldn't be determined" -
    # distinct from "no line items at all", which never reaches this dataclass.
    name: str | None
    amount: Decimal | None


def _read_line_item(item: Element) -> _LineItem:
    name_element, name_ambiguous = _find_unique(item, _LINE_ITEM_NAME_PATH)
    name = None if name_ambiguous else _bounded(_text(name_element), "product_summary")[0]

    total_element, total_ambiguous = _find_unique(item, _LINE_ITEM_TOTAL_PATH)
    total_text = None if total_ambiguous else _text(total_element)
    amount = _parse_decimal(total_text) if total_text is not None else None
    if amount is not None and amount < 0:
        amount = None

    return _LineItem(name=name, amount=amount)


def _extract_product_summary(root: Element) -> _FieldResult[str]:
    line_item_elements = root.findall(_LINE_ITEMS_PATH, NAMESPACES)
    if not line_item_elements:
        return _FieldResult(None, None, None)

    items = [_read_line_item(item) for item in line_item_elements]

    if len(items) == 1:
        name = items[0].name
        # A single item needs no amount at all - there's nothing to compare it
        # against, so only whether its name is resolvable matters.
        return (
            _FieldResult(name, _LINE_ITEMS_PATH, None)
            if name is not None
            else _FieldResult(None, None, None)
        )

    if any(item.name is None for item in items):
        return _FieldResult(
            None,
            None,
            "product_summary: multiple XML line items include one with no usable name; "
            "falling back",
        )
    if any(item.amount is None for item in items):
        return _FieldResult(
            None,
            None,
            "product_summary: multiple XML line items include one with no usable amount; "
            "falling back",
        )

    # Every item is now guaranteed a name and a non-negative amount, so this
    # comprehension keeps every entry - it exists only to prove that to mypy.
    priced: list[tuple[str, Decimal]] = [
        (item.name, item.amount)
        for item in items
        if item.name is not None and item.amount is not None
    ]
    ranked = sorted(priced, key=lambda entry: entry[1], reverse=True)
    top_name, top_amount = ranked[0]
    _, second_amount = ranked[1]
    if top_amount > 0 and top_amount >= _DOMINANCE_RATIO * second_amount:
        return _FieldResult(top_name, _LINE_ITEMS_PATH, None)

    return _FieldResult(
        None, None, "product_summary: no single XML line item clearly dominates; falling back"
    )


def extract_invoice_from_xml(candidate: XmlCandidate) -> InvoiceExtraction:
    invoice_date = _extract_issue_date(candidate.root)
    seller = _extract_seller(candidate.root)
    product_summary = _extract_product_summary(candidate.root)
    gross_total = _extract_gross_total(candidate.root)
    currency = _extract_currency(candidate.root)

    field_paths_and_warnings: list[tuple[str, str | None, str | None]] = [
        ("invoice_date", invoice_date.xml_path, invoice_date.warning),
        ("seller", seller.xml_path, seller.warning),
        ("product_summary", product_summary.xml_path, product_summary.warning),
        ("gross_total", gross_total.xml_path, gross_total.warning),
        ("currency", currency.xml_path, currency.warning),
    ]
    evidence: dict[str, Evidence] = {}
    warnings: list[str] = []
    for name, xml_path, warning in field_paths_and_warnings:
        if xml_path is not None:
            evidence[name] = Evidence(xml_field=xml_path)
        if warning is not None:
            warnings.append(warning)

    try:
        return InvoiceExtraction(
            invoice_date=invoice_date.value,
            seller=seller.value,
            product_summary=product_summary.value,
            gross_total=gross_total.value,
            currency=currency.value,
            evidence=evidence,
            warnings=warnings,
        )
    except ValueError as error:
        # Defense in depth: every field above is already independently validated
        # against the same rules InvoiceExtraction enforces, so this should be
        # unreachable in practice. If it ever isn't, don't let a schema mismatch
        # discard XML fields that did pass validation - fall back to the model
        # for everything instead of raising out of the analysis pipeline.
        return InvoiceExtraction(
            warnings=[*warnings, f"XML extraction rejected by schema: {error}"]
        )
