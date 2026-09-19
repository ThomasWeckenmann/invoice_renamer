"""OCR adapter interface, plus the Tesseract and Apple Vision implementations.

macOS - dev checkout or packaged app alike - uses Vision, since it ships
with the OS and needs nothing bundled or installed. Linux keeps Tesseract,
which the platform has no story for bundling anyway.
"""

import sys
from dataclasses import dataclass
from typing import Protocol

import pytesseract
from PIL import Image


@dataclass
class OcrResult:
    text: str
    confidence: float | None


class OcrEngine(Protocol):
    def recognize(self, image: Image.Image, *, language: str) -> OcrResult: ...


class TesseractOcrEngine:
    """Default cross-platform OCR engine, using the Tesseract binary via pytesseract."""

    def recognize(self, image: Image.Image, *, language: str = "eng+deu") -> OcrResult:
        data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)

        # Group words back into lines using Tesseract's own block/paragraph/line
        # numbering, which it derives from each word's bounding box.
        lines: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[float] = []
        for index, word in enumerate(data["text"]):
            word = word.strip()
            if not word:
                continue
            key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
            lines.setdefault(key, []).append(word)

            confidence = float(data["conf"][index])
            if confidence >= 0:
                confidences.append(confidence)

        text = "\n".join(" ".join(words) for words in lines.values())
        average_confidence = (sum(confidences) / len(confidences) / 100) if confidences else None
        return OcrResult(text=text, confidence=average_confidence)


# This app's OCR call sites use Tesseract-style '+'-joined codes (e.g.
# 'eng+deu'); ocrmac's language_preference wants BCP-47 tags instead.
_VISION_LANGUAGE_TAGS = {
    "eng": "en-US",
    "deu": "de-DE",
}

VisionObservation = tuple[str, float, tuple[float, float, float, float]]


def _to_vision_language_tags(language: str) -> list[str]:
    try:
        return [_VISION_LANGUAGE_TAGS[code] for code in language.split("+")]
    except KeyError as error:
        raise ValueError(f"no Vision language tag mapped for Tesseract code {error}") from error


# How close two observations' vertical centers must be, relative to their
# height, to count as the same row. Two entries in the same row of an
# invoice table (e.g. an item name and its price in separate columns) rarely
# share an exact y - font metrics and sub-pixel rendering shift each box's
# baseline slightly - so grouping by exact or near-exact y can put a later
# row's entry between two entries of an earlier row. Half the taller box's
# height is a simple, scale-relative tolerance for "close enough".
_ROW_CENTER_TOLERANCE_FACTOR = 0.5


def _vertical_center(observation: VisionObservation) -> float:
    _, _, (_, y, _, height) = observation
    return y + height / 2


def _group_into_rows(observations: list[VisionObservation]) -> list[list[VisionObservation]]:
    """Clusters observations into visual rows, top to bottom.

    Processes observations in descending vertical-center order and only
    ever compares a candidate to the row most recently started, which is
    enough to cluster correctly as long as the input is sorted - two rows
    can never interleave once observations are ordered by position.
    """
    ordered = sorted(observations, key=_vertical_center, reverse=True)

    rows: list[list[VisionObservation]] = []
    for observation in ordered:
        if rows:
            row_reference = rows[-1][0]
            tolerance = _ROW_CENTER_TOLERANCE_FACTOR * max(observation[2][3], row_reference[2][3])
            if abs(_vertical_center(observation) - _vertical_center(row_reference)) <= tolerance:
                rows[-1].append(observation)
                continue
        rows.append([observation])

    for row in rows:
        row.sort(key=lambda observation: observation[2][0])  # left edge, ascending

    return rows


def aggregate_vision_observations(observations: list[VisionObservation]) -> OcrResult:
    """Combines ocrmac's `(text, confidence, bbox)` tuples into one `OcrResult`.

    Each tuple is already one recognized line or table cell, unlike
    Tesseract's word-level output. Reconstructing reading order needs two
    steps, not one sort: cluster observations that sit in the same visual
    row (see `_group_into_rows`), then within each row order left to right -
    Vision's bounding box origin is bottom-left with y increasing upward, so
    the top row has the largest vertical center.
    """
    if not observations:
        return OcrResult(text="", confidence=None)

    rows = _group_into_rows(observations)
    text = "\n".join(" ".join(observation[0] for observation in row) for row in rows)
    confidence = sum(observation[1] for observation in observations) / len(observations)
    return OcrResult(text=text, confidence=confidence)


class AppleVisionOcrEngine:
    """macOS OCR engine using Apple's Vision framework, via `ocrmac`.

    Nothing to bundle: Vision ships with the OS, so unlike Tesseract this
    engine has no external binary or language data of its own. `ocrmac` is
    imported lazily so this module keeps importing cleanly on Linux, where
    it isn't even installed (see the `sys_platform` marker in pyproject.toml).
    """

    def recognize(self, image: Image.Image, *, language: str = "eng+deu") -> OcrResult:
        from ocrmac import ocrmac

        observations = ocrmac.OCR(
            image,
            recognition_level="accurate",
            language_preference=_to_vision_language_tags(language),
        ).recognize()
        return aggregate_vision_observations(observations)


def default_ocr_engine() -> OcrEngine:
    """The OCR engine this platform should use when the caller has no preference."""
    return AppleVisionOcrEngine() if sys.platform == "darwin" else TesseractOcrEngine()
