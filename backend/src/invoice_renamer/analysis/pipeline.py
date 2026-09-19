"""Runs one PDF through the full read -> extract -> filename pipeline and
times it, producing the same FilenameProposal/RunMetrics shape a job publishes.
"""

import time

from PIL import Image

from invoice_renamer.documents.ocr import OcrEngine, OcrResult, default_ocr_engine
from invoice_renamer.documents.reader import read_document
from invoice_renamer.inference.extractor import extract_invoice
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.metrics.models import RunMetrics
from invoice_renamer.naming.builder import build_filename_proposal
from invoice_renamer.naming.schema import FilenameProposal


class _TimingOcrEngine:
    """Wraps an OcrEngine to measure cumulative OCR time separately from the
    rest of read_document(), which bundles both into one call with no split
    of its own."""

    def __init__(self, inner: OcrEngine) -> None:
        self._inner = inner
        self.elapsed_ms = 0

    def recognize(self, image: Image.Image, *, language: str) -> OcrResult:
        start = time.perf_counter()
        result = self._inner.recognize(image, language=language)
        self.elapsed_ms += int((time.perf_counter() - start) * 1000)
        return result


def run_document_analysis(
    pdf_bytes: bytes,
    model: LanguageModel,
    *,
    model_id: str,
    model_revision: str | None,
) -> tuple[FilenameProposal, RunMetrics]:
    ocr_engine = _TimingOcrEngine(default_ocr_engine())

    read_start = time.perf_counter()
    document = read_document(pdf_bytes, ocr_engine=ocr_engine)
    read_ms = int((time.perf_counter() - read_start) * 1000)
    ocr_ms = ocr_engine.elapsed_ms
    pdf_extraction_ms = max(read_ms - ocr_ms, 0)
    pages_total = len(document.pages)
    pages_ocr = [page.page_number for page in document.pages if page.needs_ocr]

    inference_start = time.perf_counter()
    extraction = extract_invoice(document, model)
    inference_ms = int((time.perf_counter() - inference_start) * 1000)

    proposal = build_filename_proposal(extraction)
    metrics = RunMetrics(
        total_ms=pdf_extraction_ms + ocr_ms + inference_ms,
        pdf_extraction_ms=pdf_extraction_ms,
        ocr_ms=ocr_ms,
        inference_ms=inference_ms,
        model_id=model_id,
        provider="transformers",
        model_revision=model_revision,
        pages_total=pages_total,
        pages_ocr=pages_ocr,
        warnings=extraction.warnings,
    )
    return proposal, metrics
