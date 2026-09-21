"""Opens and bounds-checks a JPEG image, mirroring pdf_open's validity limits
for the PDF path.
"""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

# Sanity ceiling on decoded pixel dimensions, checked from the header before any
# full decode - guards against a small file decompressing into a huge in-memory
# bitmap. Comfortably above even a 600dpi A4 scan (~4960x7016px).
MAX_DIMENSION_PIXELS = 8000


def open_validated_jpeg(image_bytes: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(image_bytes))
        width, height = image.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise ValueError(f"not a readable JPEG: {error}") from error

    if width > MAX_DIMENSION_PIXELS or height > MAX_DIMENSION_PIXELS:
        raise ValueError(
            f"JPEG dimensions ({width}x{height}) exceed the {MAX_DIMENSION_PIXELS}px-per-side limit"
        )

    try:
        image.load()
    except OSError as error:
        raise ValueError(f"not a readable JPEG: {error}") from error

    # A phone/scanner camera's own EXIF orientation tag is common and, left
    # unapplied, hands OCR sideways or upside-down pixels - garbling its
    # output even though the file itself decoded fine.
    oriented = ImageOps.exif_transpose(image)

    # exif_transpose returns a copy with no .format, so a non-RGB source
    # (e.g. a CMYK JPEG, produced by some scanners/Adobe tools) would make
    # pytesseract fall back to PNG serialization, which can't write CMYK at
    # all. Normalizing to RGB here keeps that path working regardless of
    # what pytesseract ends up guessing.
    return oriented.convert("RGB")
