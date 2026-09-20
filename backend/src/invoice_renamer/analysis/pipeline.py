"""Runs one PDF through the full read -> extract -> filename pipeline and times it,
producing the same FilenameProposal/RunMetrics shape a job publishes.

Embedded invoice XML is inspected first. When it alone supplies every field the
filename needs, page text is never read, OCR never runs, and the model is never
loaded - `model_factory` is only called once inference actually turns out to be
necessary, so the caller (the analysis coordinator) can tell whether it needs to
mark its cached model as warmed.
"""

import time
from collections.abc import Callable

from PIL import Image

from invoice_renamer.analysis.extraction_router import (
    ExtractionSource,
    merge_xml_and_model,
    route_xml,
    xml_fields_used,
    xml_supplies_filename,
)
from invoice_renamer.documents.ocr import OcrEngine, OcrResult, default_ocr_engine
from invoice_renamer.documents.pdf_open import open_validated_pdf
from invoice_renamer.documents.reader import read_document
from invoice_renamer.documents.xml_attachments import XmlDiscoveryResult
from invoice_renamer.extraction.models import InvoiceExtraction
from invoice_renamer.inference.extractor import extract_invoice
from invoice_renamer.inference.language_model import LanguageModel
from invoice_renamer.inference.shortener import shorten_fields
from invoice_renamer.metrics.models import ModelCall, ModelCallPhase, RunMetrics
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


class _RecordingLanguageModel:
    """Wraps a LanguageModel to record every prompt/response pair, in call
    order, so a user can inspect exactly what was sent and got back -
    including any repair/retry calls extract_invoice or shorten_fields made
    internally, which are otherwise invisible outside this module. `phase`
    tags which of those two, unrelated passes is currently running; the
    caller flips it (see the `phase = "shortening"` assignment below) between
    calling extract_invoice and shorten_fields with the same instance, so a
    shortening call's own repair retry is never mislabeled as another
    extraction retry."""

    def __init__(self, inner: LanguageModel, *, phase: ModelCallPhase = "extraction") -> None:
        self._inner = inner
        self.calls: list[ModelCall] = []
        self.phase: ModelCallPhase = phase

    def generate(self, prompt: str) -> str:
        try:
            response = self._inner.generate(prompt)
        except Exception as error:
            self.calls.append(ModelCall(prompt=prompt, error=repr(error), phase=self.phase))
            raise
        self.calls.append(ModelCall(prompt=prompt, response=response, phase=self.phase))
        return response


def _xml_only_result(
    extraction: InvoiceExtraction,
    *,
    xml_result: XmlDiscoveryResult,
    xml_ms: int,
    pdf_extraction_ms: int,
    ocr_ms: int,
    inference_ms: int,
    pages_total: int,
    pages_ocr: list[int],
    fields_from_xml: list[str],
    model_id: str,
    model_revision: str | None,
    inference_ran: bool,
    model_calls: list[ModelCall],
) -> tuple[FilenameProposal, RunMetrics]:
    proposal = build_filename_proposal(extraction)
    metrics = RunMetrics(
        total_ms=xml_ms + pdf_extraction_ms + ocr_ms + inference_ms,
        pdf_extraction_ms=pdf_extraction_ms,
        ocr_ms=ocr_ms,
        inference_ms=inference_ms,
        xml_ms=xml_ms,
        model_id=model_id,
        provider="transformers",
        model_revision=model_revision,
        pages_total=pages_total,
        pages_ocr=pages_ocr,
        warnings=extraction.warnings,
        extraction_source=ExtractionSource.XML.value,
        xml_status=xml_result.status.value,
        xml_attachment_name=xml_result.attachment_name,
        xml_profile_id=xml_result.profile_id,
        xml_fields_used=fields_from_xml,
        inference_ran=inference_ran,
        model_calls=model_calls,
    )
    return proposal, metrics


def run_document_analysis(
    pdf_bytes: bytes,
    model_factory: Callable[[], LanguageModel],
    *,
    model_id: str,
    model_revision: str | None,
    shorten_enabled: bool,
) -> tuple[FilenameProposal, RunMetrics]:
    xml_start = time.perf_counter()
    reader, page_count = open_validated_pdf(pdf_bytes)
    xml_result, xml_extraction = route_xml(reader)
    xml_ms = int((time.perf_counter() - xml_start) * 1000)
    fields_from_xml = xml_fields_used(xml_extraction)

    if xml_extraction is not None and xml_supplies_filename(xml_extraction):
        # XML already answers every filename field, but its seller/product text
        # comes straight from the invoice (often a verbose marketplace listing
        # title) - still worth one focused model call to shorten it, when the
        # user has that turned on. A failure here must not discard an
        # otherwise-complete XML result.
        shorten_ms = 0
        shorten_ran = False
        shortened_extraction = xml_extraction
        xml_recording_model: _RecordingLanguageModel | None = None
        if shorten_enabled:
            try:
                xml_recording_model = _RecordingLanguageModel(model_factory(), phase="shortening")
                shorten_start = time.perf_counter()
                shortened_extraction = shorten_fields(xml_extraction, xml_recording_model)
                shorten_ms = int((time.perf_counter() - shorten_start) * 1000)
                shorten_ran = True
            except Exception as error:
                shortened_extraction = xml_extraction.model_copy(
                    update={
                        "warnings": [*xml_extraction.warnings, f"field shortening failed: {error}"]
                    }
                )
        return _xml_only_result(
            shortened_extraction,
            xml_result=xml_result,
            xml_ms=xml_ms,
            pdf_extraction_ms=0,
            ocr_ms=0,
            inference_ms=shorten_ms,
            pages_total=page_count,
            pages_ocr=[],
            fields_from_xml=fields_from_xml,
            model_id=model_id,
            model_revision=model_revision,
            inference_ran=shorten_ran,
            model_calls=xml_recording_model.calls if xml_recording_model is not None else [],
        )

    # Reflect whatever real work actually completed before a possible exception
    # below, rather than resetting to zero on failure - the whole point of
    # preserving XML on a fallback crash (see the except block) is to report
    # what genuinely happened, not to pretend nothing was attempted.
    pdf_extraction_ms = 0
    ocr_ms = 0
    pages_total = page_count
    pages_ocr: list[int] = []
    inference_ms = 0
    inference_start: float | None = None
    recording_model: _RecordingLanguageModel | None = None

    try:
        ocr_engine = _TimingOcrEngine(default_ocr_engine())
        read_start = time.perf_counter()
        document = read_document(
            pdf_bytes, ocr_engine=ocr_engine, reader=reader, xml_result=xml_result
        )
        read_ms = int((time.perf_counter() - read_start) * 1000)
        ocr_ms = ocr_engine.elapsed_ms
        pdf_extraction_ms = max(read_ms - ocr_ms, 0)
        pages_total = len(document.pages)
        pages_ocr = [page.page_number for page in document.pages if page.needs_ocr]

        if xml_result.warning is not None:
            document = document.model_copy(
                update={"warnings": [*document.warnings, xml_result.warning]}
            )

        recording_model = _RecordingLanguageModel(model_factory())
        inference_start = time.perf_counter()
        model_extraction = extract_invoice(document, recording_model)
        extraction = merge_xml_and_model(xml_extraction, model_extraction)
        if shorten_enabled:
            # A shortening failure here is on top of an already-successful
            # extraction - it must not fall into the except block below, which
            # would wrongly treat it as a total extraction failure and either
            # fail an ordinary invoice outright or discard model-supplied
            # fields for a partial-XML one.
            try:
                recording_model.phase = "shortening"
                extraction = shorten_fields(extraction, recording_model)
            except Exception as error:
                extraction = extraction.model_copy(
                    update={"warnings": [*extraction.warnings, f"field shortening failed: {error}"]}
                )
        inference_ms = int((time.perf_counter() - inference_start) * 1000)
    except Exception as error:
        if not fields_from_xml:
            # Nothing usable would survive either way - XML never existed or
            # contributed nothing, so this is a genuine failure, not a case
            # where something valid would otherwise be discarded. Let the
            # coordinator fail the job, exactly as before this XML-routing
            # path existed.
            raise
        assert xml_extraction is not None  # implied by fields_from_xml being non-empty
        # True whenever generate() was actually invoked, even though it (or
        # something after it) then raised - inference_ran must agree with
        # inference_ms below: a nonzero duration with inference_ran=False would
        # claim both "it ran for N ms" and "it never ran" at once.
        inference_ran = inference_start is not None
        if inference_start is not None:
            # The crash happened during/after the generate() call itself, not
            # during model loading - credit the time actually spent on it.
            inference_ms = int((time.perf_counter() - inference_start) * 1000)
        extraction = xml_extraction.model_copy(
            update={"warnings": [*xml_extraction.warnings, f"model fallback failed: {error}"]}
        )
        return _xml_only_result(
            extraction,
            xml_result=xml_result,
            xml_ms=xml_ms,
            pdf_extraction_ms=pdf_extraction_ms,
            ocr_ms=ocr_ms,
            inference_ms=inference_ms,
            pages_total=pages_total,
            pages_ocr=pages_ocr,
            fields_from_xml=fields_from_xml,
            model_id=model_id,
            model_revision=model_revision,
            inference_ran=inference_ran,
            model_calls=recording_model.calls if recording_model is not None else [],
        )

    proposal = build_filename_proposal(extraction)
    source = ExtractionSource.XML_AND_MODEL if fields_from_xml else ExtractionSource.MODEL
    assert recording_model is not None  # the try block above always sets it before this point
    metrics = RunMetrics(
        total_ms=xml_ms + pdf_extraction_ms + ocr_ms + inference_ms,
        pdf_extraction_ms=pdf_extraction_ms,
        ocr_ms=ocr_ms,
        inference_ms=inference_ms,
        xml_ms=xml_ms,
        model_id=model_id,
        provider="transformers",
        model_revision=model_revision,
        pages_total=pages_total,
        pages_ocr=pages_ocr,
        warnings=extraction.warnings,
        extraction_source=source.value,
        xml_status=xml_result.status.value,
        xml_attachment_name=xml_result.attachment_name,
        xml_profile_id=xml_result.profile_id,
        xml_fields_used=fields_from_xml,
        inference_ran=True,
        model_calls=recording_model.calls,
    )
    return proposal, metrics
