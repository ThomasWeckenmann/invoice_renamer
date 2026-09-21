"""Tests for JPEG decode/dimension validation."""

import shutil
from io import BytesIO

import pytesseract
import pytest
from PIL import Image

from invoice_renamer.documents import image_open
from invoice_renamer.documents.image_open import open_validated_jpeg

_requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="Tesseract is not installed (no longer required on macOS - see README)",
)


def _jpeg_bytes(size: tuple[int, int] = (20, 10)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color="white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_valid_jpeg_is_accepted() -> None:
    image = open_validated_jpeg(_jpeg_bytes((30, 15)))

    assert image.size == (30, 15)


def test_garbage_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        open_validated_jpeg(b"\xff\xd8\xffnot actually a jpeg")


def test_empty_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        open_validated_jpeg(b"")


def test_truncated_jpeg_raises_value_error() -> None:
    full = _jpeg_bytes((200, 200))
    truncated = full[: len(full) // 2]

    with pytest.raises(ValueError):
        open_validated_jpeg(truncated)


def test_oversized_dimensions_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_open, "MAX_DIMENSION_PIXELS", 10)

    with pytest.raises(ValueError, match="exceed"):
        open_validated_jpeg(_jpeg_bytes((20, 10)))


def test_dimensions_within_the_limit_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_open, "MAX_DIMENSION_PIXELS", 20)

    image = open_validated_jpeg(_jpeg_bytes((20, 10)))

    assert image.size == (20, 10)


def _oriented_jpeg_bytes(upright_size: tuple[int, int]) -> bytes:
    """Builds a JPEG whose raw stored pixels simulate a camera-saved EXIF
    Orientation=6 photo: the true upright image (`upright_size`, with a
    black marker in its top-left corner) is stored pre-rotated 90 degrees,
    tagged Orientation=6 to correct it back on read."""
    upright = Image.new("RGB", upright_size, color="white")
    marker_size = (max(1, upright_size[0] // 4), max(1, upright_size[1] // 4))
    upright.paste((0, 0, 0), (0, 0, *marker_size))
    stored = upright.transpose(Image.ROTATE_90)

    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation tag
    buffer = BytesIO()
    stored.save(buffer, format="JPEG", quality=95, exif=exif.tobytes())
    return buffer.getvalue()


def test_exif_orientation_is_normalized_before_returning() -> None:
    upright_size = (40, 20)

    image = open_validated_jpeg(_oriented_jpeg_bytes(upright_size))

    assert image.size == upright_size
    # The upright image's marker sits in its top-left corner - left
    # uncorrected (or corrected the wrong way), this pixel would be white.
    assert image.getpixel((1, 1)) == (0, 0, 0)
    assert image.getpixel((upright_size[0] - 2, upright_size[1] - 2)) == (255, 255, 255)


def _cmyk_jpeg_bytes(size: tuple[int, int] = (20, 10)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color="white").convert("CMYK").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_cmyk_jpeg_is_normalized_to_rgb() -> None:
    image = open_validated_jpeg(_cmyk_jpeg_bytes())

    assert image.mode == "RGB"


@_requires_tesseract
def test_cmyk_jpeg_can_actually_be_ocrd() -> None:
    # pytesseract falls back to PNG serialization once exif_transpose clears
    # .format, and Pillow's PNG encoder can't write CMYK at all - reproduces
    # as an OSError from pytesseract itself if color mode isn't normalized.
    image = open_validated_jpeg(_cmyk_jpeg_bytes())

    pytesseract.image_to_string(image)  # must not raise
