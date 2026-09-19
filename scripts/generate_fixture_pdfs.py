"""Generates the synthetic (non-sensitive) PDF fixtures used by backend tests."""

from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_PAGE_SIZE = (400, 500)

# Real CII/Factur-X namespaces - must match documents/xml_attachments.py exactly,
# so these fixtures actually exercise the supported/unsupported paths they're
# named for rather than failing on a namespace typo.
_EN16931_PROFILE = "urn:cen.eu:en16931:2017"


def _cii_invoice_xml(
    *,
    profile: str = _EN16931_PROFILE,
    invoice_id: str = "INV-0001",
    issue_date: str | None = "20260115",
    date_format: str | None = "102",
    seller_name: str | None = "Beispiel GmbH",
    currency: str | None = "EUR",
    line_items: list[tuple[str, str]] | None = None,
    grand_total: str | None = "595.00",
) -> str:
    """Builds a synthetic but namespace-correct CII invoice, with every field
    optional so fixtures can each omit exactly the field they're testing."""
    line_items = line_items if line_items is not None else [("Cloud Hosting", "500.00")]

    date_block = ""
    if issue_date is not None:
        format_attr = f' format="{date_format}"' if date_format is not None else ""
        date_block = f"""
    <ram:IssueDateTime>
      <udt:DateTimeString{format_attr}>{issue_date}</udt:DateTimeString>
    </ram:IssueDateTime>"""

    seller_block = f"<ram:Name>{seller_name}</ram:Name>" if seller_name is not None else ""
    currency_block = (
        f"<ram:InvoiceCurrencyCode>{currency}</ram:InvoiceCurrencyCode>" if currency is not None else ""
    )
    total_block = (
        f"<ram:GrandTotalAmount>{grand_total}</ram:GrandTotalAmount>" if grand_total is not None else ""
    )
    items_block = "\n".join(
        f"""    <ram:IncludedSupplyChainTradeLineItem>
      <ram:SpecifiedTradeProduct><ram:Name>{name}</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeSettlement>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>{total}</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>"""
        for name, total in line_items
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter>
      <ram:ID>{profile}</ram:ID>
    </ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>{invoice_id}</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>{date_block}
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
{items_block}
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>{seller_block}</ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      {currency_block}
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        {total_block}
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""


# Complete, supported: every filename field present, one unambiguous line item.
_ZUGFERD_XML = _cii_invoice_xml()

# Missing seller only - exercises partial-XML-plus-model-fallback routing.
_ZUGFERD_XML_PARTIAL = _cii_invoice_xml(seller_name=None)

_ZUGFERD_XML_MALFORMED = "<rsm:CrossIndustryInvoice><rsm:Unclosed>"

_ZUGFERD_XML_WRONG_ROOT = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">
  <ID>INV-0002</ID>
</Invoice>
"""

# A real, recognized Factur-X profile this app deliberately doesn't support yet.
_ZUGFERD_XML_UNSUPPORTED_PROFILE = _cii_invoice_xml(profile="urn:factur-x.eu:1p0:minimum")

_ZUGFERD_XML_DISTINCT_A = _cii_invoice_xml(invoice_id="INV-A", seller_name="Company A")
_ZUGFERD_XML_DISTINCT_B = _cii_invoice_xml(invoice_id="INV-B", seller_name="Company B")


def _text_page_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=(400, 500))
    y = 460
    for line in lines:
        c.drawString(40, y, line)
        y -= 20
    c.save()


def _sparse_whitespace_pdf(path: Path) -> None:
    """A page with almost no real content padded out with wide runs of spaces.

    Exercises the OCR-routing heuristic's non-whitespace character count:
    total text length alone would look 'usable' here.
    """
    c = canvas.Canvas(str(path), pagesize=_PAGE_SIZE)
    c.drawString(40, 460, "A" + " " * 30 + "B")
    c.save()


def _blank_page_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=_PAGE_SIZE)
    c.showPage()
    c.save()


def _scanned_page_pdf(path: Path, lines: list[str]) -> None:
    """Builds a page with rasterized text but no text layer, to exercise OCR.

    Draws the text as a normal PDF first, rasterizes that page to an image,
    then places only the image on the final page so pypdf.extract_text()
    returns nothing and the page must go through OCR.
    """
    text_pdf_path = path.with_suffix(".tmp.pdf")
    _text_page_pdf(text_pdf_path, lines)

    document = pdfium.PdfDocument(str(text_pdf_path))
    image = document[0].render(scale=2.0).to_pil()
    document.close()
    text_pdf_path.unlink()

    c = canvas.Canvas(str(path), pagesize=_PAGE_SIZE)
    c.drawImage(ImageReader(image), 0, 0, width=_PAGE_SIZE[0], height=_PAGE_SIZE[1])
    c.save()


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    _text_page_pdf(
        FIXTURES_DIR / "selectable_text_en.pdf",
        ["Invoice #1001", "Seller: Apple", "Product: MacBook Air", "Total: 2180.00 EUR"],
    )
    _text_page_pdf(
        FIXTURES_DIR / "selectable_text_de.pdf",
        ["Rechnung Nr. 1001", "Verkaeufer: Mueller GmbH", "Betrag: 199,00 EUR"],
    )
    _blank_page_pdf(FIXTURES_DIR / "blank_page.pdf")
    _sparse_whitespace_pdf(FIXTURES_DIR / "sparse_whitespace.pdf")
    _scanned_page_pdf(
        FIXTURES_DIR / "scanned_invoice.pdf",
        ["Invoice #2002", "Seller: Acme Corp", "Total: 450.00 EUR"],
    )
    _scanned_page_pdf(
        FIXTURES_DIR / "scanned_invoice_de.pdf",
        ["Rechnung Nr. 3003", "Verkaeufer: Mueller GmbH", "Betrag: 199,00 EUR"],
    )

    mixed_text_page = FIXTURES_DIR / "_mixed_text_page.pdf"
    _text_page_pdf(mixed_text_page, ["Page one has plenty of real invoice text here."])
    blank_page = FIXTURES_DIR / "_mixed_blank_page.pdf"
    _blank_page_pdf(blank_page)

    writer = PdfWriter()
    for part in (mixed_text_page, blank_page):
        writer.append(str(part))
    with (FIXTURES_DIR / "mixed_pages.pdf").open("wb") as handle:
        writer.write(handle)
    mixed_text_page.unlink()
    blank_page.unlink()

    def _with_attachments(
        target_name: str, attachments: list[tuple[str, str]], *, base: str = "selectable_text_en.pdf"
    ) -> None:
        writer = PdfWriter(clone_from=str(FIXTURES_DIR / base))
        for name, xml in attachments:
            writer.add_attachment(name, xml.encode("utf-8"))
        with (FIXTURES_DIR / target_name).open("wb") as handle:
            writer.write(handle)

    _with_attachments("with_zugferd_xml.pdf", [("factur-x.xml", _ZUGFERD_XML)])
    _with_attachments("with_zugferd_xml_partial.pdf", [("factur-x.xml", _ZUGFERD_XML_PARTIAL)])
    _with_attachments("with_zugferd_xml_malformed.pdf", [("factur-x.xml", _ZUGFERD_XML_MALFORMED)])
    _with_attachments("with_zugferd_xml_wrong_root.pdf", [("factur-x.xml", _ZUGFERD_XML_WRONG_ROOT)])
    _with_attachments(
        "with_zugferd_xml_unsupported_profile.pdf",
        [("factur-x.xml", _ZUGFERD_XML_UNSUPPORTED_PROFILE)],
    )
    # Two different known attachment names, byte-identical content: must dedupe
    # to one SUPPORTED candidate, not read as two conflicting invoices.
    _with_attachments(
        "with_zugferd_xml_duplicate.pdf",
        [("factur-x.xml", _ZUGFERD_XML), ("xrechnung.xml", _ZUGFERD_XML)],
    )
    # Two different known attachment names, genuinely different invoices: ambiguous.
    _with_attachments(
        "with_zugferd_xml_multiple_distinct.pdf",
        [("factur-x.xml", _ZUGFERD_XML_DISTINCT_A), ("xrechnung.xml", _ZUGFERD_XML_DISTINCT_B)],
    )
    # The SAME attachment name used twice with genuinely different content - pypdf
    # groups same-named attachments into one entry with multiple revisions, so
    # this exercises that path distinctly from the two-different-names case above.
    _with_attachments(
        "with_zugferd_xml_same_name_conflict.pdf",
        [("factur-x.xml", _ZUGFERD_XML_DISTINCT_A), ("factur-x.xml", _ZUGFERD_XML_DISTINCT_B)],
    )
    # Partial XML (missing seller) on a page with no text layer, forcing real
    # OCR/read work - used to prove a fallback crash preserves that real timing
    # and page data instead of reporting it as zero.
    _with_attachments(
        "with_zugferd_xml_partial_scanned.pdf",
        [("factur-x.xml", _ZUGFERD_XML_PARTIAL)],
        base="scanned_invoice.pdf",
    )

    print(f"Wrote fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
