"""Generates the synthetic (non-sensitive) PDF fixtures used by backend tests."""

from pathlib import Path

from pypdf import PdfWriter
from reportlab.pdfgen import canvas

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

_ZUGFERD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:factur-x:invoice">
  <rsm:ExchangedDocument><ram:ID>INV-0001</ram:ID></rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>
"""


def _text_page_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=(400, 500))
    y = 460
    for line in lines:
        c.drawString(40, y, line)
        y -= 20
    c.save()


def _blank_page_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(400, 500))
    c.showPage()
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

    mixed_text_page = FIXTURES_DIR / "_mixed_text_page.pdf"
    _text_page_pdf(mixed_text_page, ["Page one has real text."])
    blank_page = FIXTURES_DIR / "_mixed_blank_page.pdf"
    _blank_page_pdf(blank_page)

    writer = PdfWriter()
    for part in (mixed_text_page, blank_page):
        writer.append(str(part))
    with (FIXTURES_DIR / "mixed_pages.pdf").open("wb") as handle:
        writer.write(handle)
    mixed_text_page.unlink()
    blank_page.unlink()

    zugferd_writer = PdfWriter(clone_from=str(FIXTURES_DIR / "selectable_text_en.pdf"))
    zugferd_writer.add_attachment("factur-x.xml", _ZUGFERD_XML.encode("utf-8"))
    with (FIXTURES_DIR / "with_zugferd_xml.pdf").open("wb") as handle:
        zugferd_writer.write(handle)

    print(f"Wrote fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
