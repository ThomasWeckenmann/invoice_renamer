"""Runs one language model over a set of invoices and scores it against hand-labeled
ground truth, per the model-selection benchmark's field accuracy / hallucination /
correction-rate / latency / failure criteria.

Peak memory isn't measured here: correctly attributing it to one model requires an
isolated process per model, which this harness doesn't set up. Measure it externally
(e.g. `/usr/bin/time -v`, Activity Monitor) when running for real. Product-label
usefulness is a human judgment call, not scored here - the report carries each
extraction's product_summary for a person to read.
"""

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image

from invoice_renamer.documents.ocr import OcrEngine, OcrResult, TesseractOcrEngine
from invoice_renamer.documents.reader import read_document
from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.inference.extractor import extract_invoice
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.metrics.models import RunMetrics

SCORED_FIELDS = ("invoice_date", "seller", "product_summary", "gross_total", "currency")

_REPAIR_FAILURE_MARKER = "model output could not be validated"


def _values_match(field_name: str, extracted: object, expected: object) -> bool:
    if extracted is None or expected is None:
        return extracted is expected
    if field_name == "gross_total":
        try:
            return Decimal(str(extracted)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    extracted_text = " ".join(str(extracted).split()).casefold()
    expected_text = " ".join(str(expected).split()).casefold()
    if field_name in ("seller", "product_summary"):
        # Models often return a substantively correct answer padded with extra
        # detail (a full legal entity name, a trailing "- Region xyz" suffix) -
        # score that as correct rather than undercounting these two fields.
        # One-directional only: the ground truth must appear as a whole phrase
        # inside the extracted text. The reverse direction (extracted inside
        # expected) would let a short, vague, or truncated wrong answer count
        # as correct just because it happens to be a substring of the right
        # one - e.g. seller="a" would match "MediaMarkt", or
        # product_summary="Pro" would match "MacBook Pro 16".
        return _contains_phrase(expected_text, extracted_text)
    return extracted_text == expected_text


def _contains_phrase(phrase: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def score_extraction(
    extraction: InvoiceExtraction, ground_truth: dict[str, object] | None
) -> dict[str, bool | None]:
    """Per scored field: True/False if ground truth has that field, None if it doesn't
    (so an invoice missing a label doesn't get counted as wrong)."""
    ground_truth = ground_truth or {}
    return {
        field_name: (
            _values_match(field_name, getattr(extraction, field_name), ground_truth[field_name])
            if field_name in ground_truth
            else None
        )
        for field_name in SCORED_FIELDS
    }


def _hallucinated_fields(
    extraction: InvoiceExtraction, ground_truth: dict[str, object] | None
) -> list[str]:
    ground_truth = ground_truth or {}
    return [
        field_name
        for field_name in SCORED_FIELDS
        if field_name in ground_truth
        and ground_truth[field_name] is None
        and getattr(extraction, field_name) is not None
    ]


class _TimingOcrEngine:
    """Wraps an OcrEngine to measure cumulative OCR time separately from the rest
    of read_document(), which bundles both into one call with no split of its own.
    """

    def __init__(self, inner: OcrEngine) -> None:
        self._inner = inner
        self.elapsed_ms = 0

    def recognize(self, image: Image.Image, *, language: str) -> OcrResult:
        start = time.perf_counter()
        result = self._inner.recognize(image, language=language)
        self.elapsed_ms += int((time.perf_counter() - start) * 1000)
        return result


@dataclass
class InvoiceResult:
    filename: str
    extraction: InvoiceExtraction
    metrics: RunMetrics
    ground_truth: dict[str, object] | None
    field_matches: dict[str, bool | None]
    hallucinated_fields: list[str]
    failed: bool
    # False when a crash cut the run short: metrics may hold whatever partial
    # timing was captured before the crash, which isn't comparable to a real
    # end-to-end duration and must not enter the latency averages as if it were.
    timing_complete: bool = True

    @property
    def needs_correction(self) -> bool:
        return self.failed or any(match is False for match in self.field_matches.values())

    @property
    def is_judged_for_correction(self) -> bool:
        # A failure is always a real signal; a clean pass only means something
        # if at least one field was actually checked against a label.
        return self.failed or any(match is not None for match in self.field_matches.values())

    @property
    def is_judged_for_hallucination(self) -> bool:
        # Hallucination can only be caught where ground truth explicitly says a
        # field should be null; an invoice with no such label proves nothing.
        return self.ground_truth is not None and any(
            value is None for value in self.ground_truth.values()
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "extraction": self.extraction.model_dump(mode="json"),
            "metrics": self.metrics.model_dump(mode="json"),
            "ground_truth": self.ground_truth,
            "field_matches": self.field_matches,
            "hallucinated_fields": self.hallucinated_fields,
            "failed": self.failed,
            "needs_correction": self.needs_correction,
            "timing_complete": self.timing_complete,
        }


@dataclass
class ModelBenchmarkResult:
    model_id: str
    invoices: list[InvoiceResult] = field(default_factory=list)

    @property
    def field_accuracy(self) -> dict[str, float | None]:
        accuracy: dict[str, float | None] = {}
        for field_name in SCORED_FIELDS:
            judged: list[bool] = [
                match
                for result in self.invoices
                if (match := result.field_matches[field_name]) is not None
            ]
            accuracy[field_name] = (sum(judged) / len(judged)) if judged else None
        return accuracy

    @property
    def failure_rate(self) -> float:
        # Doesn't need ground truth: a failure is detected from the extraction
        # output itself, so this rate is always meaningful.
        return _rate(result.failed for result in self.invoices)

    @property
    def correction_rate(self) -> float | None:
        judged = [
            result.needs_correction for result in self.invoices if result.is_judged_for_correction
        ]
        return _rate(judged) if judged else None

    @property
    def hallucination_rate(self) -> float | None:
        judged = [
            bool(result.hallucinated_fields)
            for result in self.invoices
            if result.is_judged_for_hallucination
        ]
        return _rate(judged) if judged else None

    @property
    def average_total_ms(self) -> float | None:
        values = [r.metrics.total_ms for r in self.invoices if r.timing_complete]
        return _average(values) if values else None

    @property
    def average_inference_ms(self) -> float | None:
        values = [r.metrics.inference_ms for r in self.invoices if r.timing_complete]
        return _average(values) if values else None

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "field_accuracy": self.field_accuracy,
            "failure_rate": self.failure_rate,
            "correction_rate": self.correction_rate,
            "hallucination_rate": self.hallucination_rate,
            "average_total_ms": self.average_total_ms,
            "average_inference_ms": self.average_inference_ms,
            "invoices": [invoice.to_dict() for invoice in self.invoices],
        }


def _rate(flags: Iterable[bool]) -> float:
    values = list(flags)
    return (sum(values) / len(values)) if values else 0.0


def _average(numbers: Iterable[int]) -> float:
    values = list(numbers)
    return (sum(values) / len(values)) if values else 0.0


def _crashed_field_matches(ground_truth: dict[str, object] | None) -> dict[str, bool | None]:
    """A crash means no trustworthy value was produced for any field - including
    one where ground truth happens to be null, since the all-null fallback
    extraction would otherwise look like a correct (accidental) match."""
    ground_truth = ground_truth or {}
    return {
        field_name: False if field_name in ground_truth else None for field_name in SCORED_FIELDS
    }


def _crashed_invoice_result(
    filename: str,
    error: Exception,
    *,
    model_id: str,
    model_revision: str | None,
    ground_truth: dict[str, object] | None,
    pdf_extraction_ms: int = 0,
    ocr_ms: int = 0,
    inference_ms: int = 0,
    pages_total: int = 1,
    pages_ocr: list[int] | None = None,
) -> InvoiceResult:
    extraction = InvoiceExtraction(warnings=[f"benchmark run crashed: {error!r}"])
    metrics = RunMetrics(
        total_ms=pdf_extraction_ms + ocr_ms + inference_ms,
        pdf_extraction_ms=pdf_extraction_ms,
        ocr_ms=ocr_ms,
        inference_ms=inference_ms,
        model_id=model_id,
        provider="transformers",
        model_revision=model_revision,
        pages_total=pages_total,
        pages_ocr=pages_ocr or [],
        warnings=extraction.warnings,
    )
    return InvoiceResult(
        filename=filename,
        extraction=extraction,
        metrics=metrics,
        ground_truth=ground_truth,
        field_matches=_crashed_field_matches(ground_truth),
        hallucinated_fields=[],
        failed=True,
        timing_complete=False,
    )


def run_invoice(
    pdf_bytes: bytes,
    filename: str,
    model: LanguageModel,
    *,
    model_id: str,
    model_revision: str | None,
    ground_truth: dict[str, object] | None,
) -> InvoiceResult:
    ocr_engine = _TimingOcrEngine(TesseractOcrEngine())

    read_start = time.perf_counter()
    try:
        document = read_document(pdf_bytes, ocr_engine=ocr_engine)
    except Exception as error:  # e.g. a corrupt PDF - nothing at all was measured
        return _crashed_invoice_result(
            filename,
            error,
            model_id=model_id,
            model_revision=model_revision,
            ground_truth=ground_truth,
        )
    read_ms = int((time.perf_counter() - read_start) * 1000)
    ocr_ms = ocr_engine.elapsed_ms
    pdf_extraction_ms = max(read_ms - ocr_ms, 0)
    pages_total = len(document.pages)
    pages_ocr = [page.page_number for page in document.pages if page.needs_ocr]

    inference_start = time.perf_counter()
    try:
        extraction = extract_invoice(document, model)
    except Exception as error:  # the read succeeded, so keep that real timing/page data
        inference_ms = int((time.perf_counter() - inference_start) * 1000)
        return _crashed_invoice_result(
            filename,
            error,
            model_id=model_id,
            model_revision=model_revision,
            ground_truth=ground_truth,
            pdf_extraction_ms=pdf_extraction_ms,
            ocr_ms=ocr_ms,
            inference_ms=inference_ms,
            pages_total=pages_total,
            pages_ocr=pages_ocr,
        )
    inference_ms = int((time.perf_counter() - inference_start) * 1000)

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

    return InvoiceResult(
        filename=filename,
        extraction=extraction,
        metrics=metrics,
        ground_truth=ground_truth,
        field_matches=score_extraction(extraction, ground_truth),
        hallucinated_fields=_hallucinated_fields(extraction, ground_truth),
        failed=any(_REPAIR_FAILURE_MARKER in warning for warning in extraction.warnings),
    )


def run_benchmark(
    pdf_paths: list[Path],
    model: LanguageModel,
    *,
    model_id: str,
    model_revision: str | None = None,
    ground_truth: dict[str, dict[str, object]] | None = None,
    on_invoice_start: Callable[[Path], None] | None = None,
    on_invoice_done: Callable[[InvoiceResult], None] | None = None,
) -> ModelBenchmarkResult:
    result = ModelBenchmarkResult(model_id=model_id)
    for path in pdf_paths:
        if on_invoice_start is not None:
            on_invoice_start(path)
        invoice_ground_truth = (ground_truth or {}).get(path.name)
        try:
            invoice_result = run_invoice(
                path.read_bytes(),
                path.name,
                model,
                model_id=model_id,
                model_revision=model_revision,
                ground_truth=invoice_ground_truth,
            )
        except Exception as error:  # e.g. path.read_bytes() itself failing
            invoice_result = _crashed_invoice_result(
                path.name,
                error,
                model_id=model_id,
                model_revision=model_revision,
                ground_truth=invoice_ground_truth,
            )
        result.invoices.append(invoice_result)
        if on_invoice_done is not None:
            on_invoice_done(invoice_result)
    return result
