"""Renders a single PDF page to an image for OCR."""

from typing import cast

import pypdfium2 as pdfium
from PIL import Image

# Higher than 1x to give the OCR engine enough resolution on small invoice text.
_RENDER_SCALE = 2.0


def render_page_to_image(pdf_bytes: bytes, page_index: int) -> Image.Image:
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[page_index]
        return cast(Image.Image, page.render(scale=_RENDER_SCALE).to_pil())
    finally:
        document.close()
