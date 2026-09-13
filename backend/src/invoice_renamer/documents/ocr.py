"""OCR adapter interface and the default cross-platform Tesseract implementation.

Apple Vision (via `ocrmac`) plugs in behind the same `OcrEngine` protocol on
macOS; that adapter is not implemented yet.
"""

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
