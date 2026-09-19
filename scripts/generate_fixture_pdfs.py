"""Generates the synthetic (non-sensitive) PDF fixtures used by backend tests."""

from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_PAGE_SIZE = (400, 500)

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

    zugferd_writer = PdfWriter(clone_from=str(FIXTURES_DIR / "selectable_text_en.pdf"))
    zugferd_writer.add_attachment("factur-x.xml", _ZUGFERD_XML.encode("utf-8"))
    with (FIXTURES_DIR / "with_zugferd_xml.pdf").open("wb") as handle:
        zugferd_writer.write(handle)

    print(f"Wrote fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
