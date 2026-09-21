"""Tests for run_document_analysis, the read -> extract -> filename pipeline behind a job."""

import json
import time
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from invoice_renamer.analysis.pipeline import run_document_analysis
from invoice_renamer.documents.format import DocumentFormat

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class _FakeLanguageModel:
    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, prompt: str) -> str:
        return self._response


class _ExtractsThenCrashesOnShorten:
    """Succeeds on the first generate() call (extraction) but raises on any
    call after that (the shortening pass) - a hard crash, not just bad JSON,
    since shorten_fields already tolerates bad JSON on its own."""

    def __init__(self, extraction_response: str) -> None:
        self._extraction_response = extraction_response
        self._calls = 0

    def generate(self, prompt: str) -> str:
        self._calls += 1
        if self._calls == 1:
            return self._extraction_response
        raise RuntimeError("simulated shortening crash")


def _valid_model_response(**overrides: object) -> str:
    fields: dict[str, object] = {
        "invoice_date": "2026-09-12",
        "seller": "Apple",
        "product_summary": "MacBook Air",
        "gross_total": "2180",
        "currency": "EUR",
        "language": "en",
        "warnings": [],
    }
    fields.update(overrides)
    return json.dumps(fields)


def test_happy_path_produces_the_same_proposal_as_the_e2e_test() -> None:
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    model_response = _valid_model_response()

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision="c1899de289a04d12100db370d81485cdf75e47ca",
        shorten_enabled=True,
    )

    assert proposal.proposed_filename == "2026-09-12_Apple_MacBook-Air_2180-EUR.pdf"
    assert proposal.requires_review is False
    assert metrics.pages_total == 1
    assert metrics.total_ms >= 0
    assert metrics.pdf_extraction_ms >= 0
    assert metrics.ocr_ms >= 0
    assert metrics.inference_ms >= 0
    assert metrics.model_id == "qwen3-0.6b"
    assert metrics.model_revision == "c1899de289a04d12100db370d81485cdf75e47ca"
    assert metrics.provider == "transformers"
    assert metrics.extraction_source == "model"
    assert metrics.xml_status == "none"
    assert metrics.inference_ran is True


def test_a_corrupt_pdf_raises_instead_of_returning_a_synthesized_result() -> None:
    def _never_called() -> _FakeLanguageModel:
        raise AssertionError("model must not load for a PDF that fails to open")

    with pytest.raises(ValueError):
        run_document_analysis(
            b"not a pdf",
            _never_called,
            model_id="qwen3-0.6b",
            model_revision=None,
            shorten_enabled=True,
        )


def test_jpeg_happy_path_ocrs_the_image_and_produces_a_jpg_suffixed_filename() -> None:
    image_bytes = (FIXTURES_DIR / "scanned_invoice.jpg").read_bytes()
    model_response = _valid_model_response()

    proposal, metrics = run_document_analysis(
        image_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision="c1899de289a04d12100db370d81485cdf75e47ca",
        shorten_enabled=True,
        document_format=DocumentFormat.JPEG,
    )

    assert proposal.proposed_filename == "2026-09-12_Apple_MacBook-Air_2180-EUR.jpg"
    assert proposal.requires_review is False
    assert metrics.pages_total == 1
    assert metrics.pages_ocr == [1]
    assert metrics.ocr_ms >= 0
    assert metrics.inference_ran is True
    # A JPEG has no PDF container, so XML routing must never even be attempted.
    assert metrics.extraction_source == "model"
    assert metrics.xml_status == "none"
    assert metrics.xml_ms == 0
    assert metrics.xml_fields_used == []
    assert metrics.xml_attachment_name is None


def test_a_corrupt_jpeg_raises_instead_of_returning_a_synthesized_result() -> None:
    def _never_called() -> _FakeLanguageModel:
        raise AssertionError("model must not load for a JPEG that fails to open")

    with pytest.raises(ValueError):
        run_document_analysis(
            b"\xff\xd8\xffnot actually a jpeg",
            _never_called,
            model_id="qwen3-0.6b",
            model_revision=None,
            shorten_enabled=True,
            document_format=DocumentFormat.JPEG,
        )


def test_complete_xml_skips_ocr_but_still_shortens_seller_and_product() -> None:
    # XML supplies every filename field, so read_document/OCR are still skipped,
    # but seller/product_summary still come straight from the invoice's own XML
    # (often verbose) - one model call shortens them before the filename is built.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml.pdf").read_bytes()
    shorten_response = json.dumps({"seller_short": "Beispiel", "product_short": "Hosting"})

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(shorten_response),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert proposal.proposed_filename == "2026-01-15_Beispiel_Hosting_595-EUR.pdf"
    assert proposal.requires_review is False
    # The full XML values survive alongside the shortened ones - shortening
    # never overwrites them, only adds seller_short/product_summary_short.
    assert proposal.extraction.seller == "Beispiel GmbH"
    assert proposal.extraction.seller_short == "Beispiel"
    assert proposal.extraction.product_summary == "Cloud Hosting"
    assert proposal.extraction.product_summary_short == "Hosting"
    assert metrics.extraction_source == "xml"
    assert metrics.xml_status == "supported"
    assert metrics.xml_attachment_name == "factur-x.xml"
    assert metrics.inference_ran is True
    assert metrics.ocr_ms == 0
    assert metrics.inference_ms >= 0
    assert metrics.pages_total == 1
    assert metrics.pages_ocr == []
    assert set(metrics.xml_fields_used) == {
        "invoice_date",
        "seller",
        "product_summary",
        "gross_total",
        "currency",
    }
    # An XML-complete run's only model call is the shortening pass - it must
    # never be tagged as if it were an extraction call.
    assert [call.phase for call in metrics.model_calls] == ["shortening"]


def test_shorten_disabled_never_loads_a_model_for_a_complete_xml_result() -> None:
    # With the global toggle off, a complete XML result must be exactly as
    # cheap as before the shortening pass existed - no model load at all.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml.pdf").read_bytes()

    def _never_called() -> _FakeLanguageModel:
        raise AssertionError("shortening is disabled; the model must not load")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _never_called,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert proposal.proposed_filename == "2026-01-15_Beispiel-GmbH_Cloud-Hosting_595-EUR.pdf"
    assert proposal.extraction.seller_short is None
    assert proposal.extraction.product_summary_short is None
    assert metrics.inference_ran is False
    assert metrics.inference_ms == 0


def test_shorten_disabled_skips_the_second_model_call_on_the_model_path() -> None:
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    calls: list[str] = []

    class _CountingModel:
        def generate(self, prompt: str) -> str:
            calls.append(prompt)
            return _valid_model_response()

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _CountingModel(),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert len(calls) == 1  # extraction only, no shortening call
    assert proposal.extraction.seller == "Apple"
    assert proposal.extraction.seller_short is None
    assert metrics.inference_ran is True


def test_complete_xml_survives_a_shortening_failure_unshortened() -> None:
    # The shortening call is a nice-to-have on top of an already-complete XML
    # result - a crash in it (model-load or otherwise) must not turn a good
    # result into a failed job, just leave the fields as XML supplied them.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml.pdf").read_bytes()

    def _crashing_factory() -> _FakeLanguageModel:
        raise RuntimeError("simulated model-load failure")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _crashing_factory,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert proposal.proposed_filename == "2026-01-15_Beispiel-GmbH_Cloud-Hosting_595-EUR.pdf"
    assert metrics.extraction_source == "xml"
    assert metrics.inference_ran is False
    assert metrics.inference_ms == 0
    assert any("field shortening failed" in warning for warning in proposal.extraction.warnings)


def test_ordinary_invoice_survives_a_shortening_crash_with_the_extraction_intact() -> None:
    # No XML at all - a shortening crash here must not discard the extraction
    # that already succeeded and fail the whole job over an optional add-on.
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    model = _ExtractsThenCrashesOnShorten(_valid_model_response())

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: model,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert proposal.extraction.seller == "Apple"
    assert proposal.extraction.product_summary == "MacBook Air"
    assert proposal.extraction.seller_short is None
    assert metrics.extraction_source == "model"
    assert metrics.inference_ran is True
    assert any("field shortening failed" in warning for warning in proposal.extraction.warnings)


def test_partial_xml_survives_a_shortening_crash_keeping_the_models_fields() -> None:
    # Partial XML plus a shortening crash: must keep the model-supplied seller,
    # not fall back to the XML-only result (which would lose it entirely).
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_partial.pdf").read_bytes()
    model = _ExtractsThenCrashesOnShorten(_valid_model_response(seller="Model Seller"))

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: model,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert proposal.extraction.seller == "Model Seller"
    assert proposal.extraction.product_summary == "Cloud Hosting"  # from XML, unaffected
    assert metrics.extraction_source == "xml_and_model"
    assert metrics.inference_ran is True
    assert any("field shortening failed" in warning for warning in proposal.extraction.warnings)


def test_partial_xml_falls_back_to_the_model_for_the_missing_field_only() -> None:
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_partial.pdf").read_bytes()
    # The model is scripted to also disagree on fields XML *did* supply - those
    # must not win, only the seller it's uniquely providing should land.
    model_response = _valid_model_response(
        seller="Model Seller",
        invoice_date="2030-01-01",
        gross_total="1",
        currency="USD",
        product_summary="Wrong Product",
    )
    calls: list[str] = []

    def _spy_model() -> _FakeLanguageModel:
        calls.append("loaded")
        return _FakeLanguageModel(model_response)

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _spy_model,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert calls == ["loaded"]
    assert proposal.extraction.seller == "Model Seller"
    assert proposal.extraction.invoice_date.isoformat() == "2026-01-15"
    assert str(proposal.extraction.gross_total) == "595.00"
    assert proposal.extraction.currency == "EUR"
    assert proposal.extraction.product_summary == "Cloud Hosting"
    assert metrics.extraction_source == "xml_and_model"
    assert metrics.inference_ran is True
    assert set(metrics.xml_fields_used) == {
        "invoice_date",
        "product_summary",
        "gross_total",
        "currency",
    }


def test_invalid_xml_falls_back_to_the_model_with_a_warning() -> None:
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_malformed.pdf").read_bytes()
    model_response = _valid_model_response()

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert proposal.extraction.seller == "Apple"
    assert metrics.extraction_source == "model"
    assert metrics.xml_status == "invalid"
    assert metrics.inference_ran is True
    assert any("not valid XML" in warning for warning in proposal.extraction.warnings)


def test_ambiguous_xml_falls_back_to_the_model_with_a_warning() -> None:
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_multiple_distinct.pdf").read_bytes()
    model_response = _valid_model_response()

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert metrics.xml_status == "ambiguous"
    assert metrics.extraction_source == "model"
    assert any("multiple distinct" in warning for warning in proposal.extraction.warnings)


def test_fallback_model_failure_still_surfaces_partial_xml_and_the_failure_warning() -> None:
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_partial.pdf").read_bytes()

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel("not json"),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    # XML's own fields survive even though the model side failed entirely.
    assert proposal.extraction.invoice_date.isoformat() == "2026-01-15"
    assert proposal.extraction.product_summary == "Cloud Hosting"
    assert proposal.extraction.seller is None
    assert "seller" in proposal.missing_fields
    assert any("could not be validated" in warning for warning in proposal.extraction.warnings)
    assert metrics.inference_ran is True


def test_model_load_crash_preserves_partial_xml_instead_of_failing_the_job() -> None:
    # Distinct from the case above: here the fallback *machinery itself* raises
    # (a model-load crash, not just bad model output), which used to propagate
    # uncaught and lose the already-valid XML fields entirely.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_partial.pdf").read_bytes()

    def _crashing_factory() -> _FakeLanguageModel:
        raise RuntimeError("simulated model-load failure")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _crashing_factory,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert proposal.extraction.invoice_date.isoformat() == "2026-01-15"
    assert proposal.extraction.product_summary == "Cloud Hosting"
    assert proposal.extraction.seller is None
    assert "seller" in proposal.missing_fields
    assert any("model fallback failed" in warning for warning in proposal.extraction.warnings)
    assert metrics.extraction_source == "xml"
    assert metrics.inference_ran is False
    assert metrics.ocr_ms == 0
    assert metrics.inference_ms == 0


def test_generate_crash_reports_inference_ran_true_not_a_contradiction() -> None:
    # Distinct again: here generate() was actually invoked (unlike the model-load
    # crash above, where it never got that far) and ran for a measurable time
    # before raising. inference_ran must agree with the nonzero inference_ms -
    # reporting "0ms, never ran" right next to a real duration is self-contradictory.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_partial.pdf").read_bytes()

    class _CrashingModel:
        def generate(self, prompt: str) -> str:
            time.sleep(0.02)
            raise RuntimeError("simulated generate() crash mid-inference")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _CrashingModel(),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert metrics.inference_ran is True
    assert metrics.inference_ms > 0
    assert metrics.extraction_source == "xml"
    assert any("model fallback failed" in warning for warning in proposal.extraction.warnings)


def test_fallback_crash_with_no_xml_at_all_still_fails_the_job() -> None:
    # No XML fields exist to preserve here, so this must behave exactly as
    # before the fix: propagate, and let the caller (the coordinator) fail
    # the job rather than manufacture a result from nothing.
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()

    def _crashing_factory() -> _FakeLanguageModel:
        raise RuntimeError("simulated model-load failure")

    with pytest.raises(RuntimeError, match="simulated model-load failure"):
        run_document_analysis(
            pdf_bytes,
            _crashing_factory,
            model_id="qwen3-0.6b",
            model_revision=None,
            shorten_enabled=True,
        )


def test_xml_detected_with_zero_usable_fields_plus_crash_still_fails_the_job() -> None:
    # xml_extraction is not None here (XML was SUPPORTED), but every field was
    # rejected (ambiguous seller) - fields_from_xml is empty, so there's nothing
    # to preserve. Must behave like the "no XML at all" case, not silently
    # succeed with an all-None extraction mislabeled as an XML result.
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>urn:cen.eu:en16931:2017</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument><ram:ID>NOFIELDS-1</ram:ID></rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Seller A</ram:Name></ram:SellerTradeParty>
      <ram:SellerTradeParty><ram:Name>Seller B</ram:Name></ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""
    writer = PdfWriter(clone_from=str(FIXTURES_DIR / "selectable_text_en.pdf"))
    writer.add_attachment("factur-x.xml", xml)
    buffer = BytesIO()
    writer.write(buffer)
    pdf_bytes = buffer.getvalue()

    def _crashing_factory() -> _FakeLanguageModel:
        raise RuntimeError("simulated model crash")

    with pytest.raises(RuntimeError, match="simulated model crash"):
        run_document_analysis(
            pdf_bytes,
            _crashing_factory,
            model_id="qwen3-0.6b",
            model_revision=None,
            shorten_enabled=True,
        )


def test_two_non_dominant_xml_items_combine_and_skip_the_model_entirely() -> None:
    # Every filename field (including the combined product_summary) comes from
    # XML alone, so - like any other complete-XML result - the model must
    # never load, not even for the combination itself.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_combine_fits.pdf").read_bytes()

    def _never_called() -> _FakeLanguageModel:
        raise AssertionError("shortening is disabled; the model must not load")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _never_called,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert proposal.proposed_filename == "2026-01-15_Beispiel-GmbH_Item-A-Item-B_595-EUR.pdf"
    assert proposal.requires_review is False
    assert proposal.warnings == []
    assert proposal.extraction.product_summary == "Item A + Item B"
    assert proposal.extraction.evidence["product_summary"].xml_field is not None
    assert metrics.extraction_source == "xml"
    assert metrics.inference_ran is False


def test_combined_xml_product_long_enough_to_truncate_is_flagged_for_review() -> None:
    # Same combination logic, but the two names are long enough that the
    # combined product_summary alone forces the filename-stem truncation -
    # this must come back requires_review=True with a visible reason, not a
    # silently cut filename.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_combine_truncates.pdf").read_bytes()

    def _never_called() -> _FakeLanguageModel:
        raise AssertionError("shortening is disabled; the model must not load")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _never_called,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert proposal.requires_review is True
    assert any("truncated" in warning for warning in proposal.warnings)
    assert proposal.missing_fields == []
    assert metrics.extraction_source == "xml"


class _CountingModel:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self._response


def test_happy_path_crosses_combination_truncation_and_salvage_together() -> None:
    # The end-to-end case tying all three fixes together: XML combines two
    # non-dominant items into a product name long enough to force filename
    # truncation, XML has no seller so the model still runs (fallback), and
    # the model's only mistake is an invalid currency, which must be salvaged
    # rather than discarding its otherwise-good seller - while XML still
    # wins for every field it actually supplies.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_combine_truncates_no_seller.pdf").read_bytes()
    model_response = _valid_model_response(
        seller="Model Seller",
        invoice_date="2030-01-01",
        product_summary="Wrong Product",
        gross_total="1",
        currency="not-a-code",
    )
    model = _CountingModel(model_response)

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: model,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    # Seller survives salvage - XML never supplied one.
    assert proposal.extraction.seller == "Model Seller"
    # XML wins for every field it actually supplies, ignoring the model's
    # conflicting guesses.
    assert proposal.extraction.invoice_date.isoformat() == "2026-01-15"
    assert str(proposal.extraction.gross_total) == "595.00"
    assert proposal.extraction.currency == "EUR"
    assert proposal.extraction.product_summary is not None
    assert proposal.extraction.product_summary.startswith("X" * 90)
    # The truncation warning and the salvage warning both survive together,
    # neither clobbering the other.
    assert proposal.requires_review is True
    assert any("truncated" in warning for warning in proposal.warnings)
    assert any("currency" in warning for warning in proposal.extraction.warnings)
    assert metrics.extraction_source == "xml_and_model"
    # Exactly the initial extraction attempt plus one repair retry - salvage
    # still spends a repair attempt before settling for the partial result.
    assert model.calls == 2


def test_partial_xml_product_missing_from_an_unusable_competitor_survives_salvage() -> None:
    # A separate partial-XML case from the crossing test above: here the
    # product itself (not the seller) is what's missing from XML, because a
    # competing line item has no usable amount - and the model's response
    # has its own single-field mistake (invalid currency) that must be
    # salvaged rather than losing the product it got right.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_unusable_competitor.pdf").read_bytes()
    model_response = _valid_model_response(
        seller="Wrong Seller",
        invoice_date="2030-01-01",
        product_summary="Model Product",
        gross_total="1",
        currency="not-a-code",
    )

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert proposal.extraction.product_summary == "Model Product"
    # The other four XML fields are unaffected by the model's conflicting guesses.
    assert proposal.extraction.invoice_date.isoformat() == "2026-01-15"
    assert proposal.extraction.seller == "Beispiel GmbH"
    assert str(proposal.extraction.gross_total) == "595.00"
    assert proposal.extraction.currency == "EUR"
    # Both the XML warning (unusable competitor) and the salvage warning reach
    # the pipeline result together.
    assert any("no usable amount" in warning for warning in proposal.extraction.warnings)
    assert any("currency" in warning for warning in proposal.extraction.warnings)
    assert metrics.extraction_source == "xml_and_model"


def test_pure_model_case_salvages_everything_but_the_invalid_currency() -> None:
    # No XML at all: a salvageable single-field model mistake must leave only
    # currency missing, not the total-loss "every field missing" the
    # all-or-nothing validation used to produce.
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    model_response = _valid_model_response(currency="not-a-code")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert proposal.extraction.seller == "Apple"
    assert proposal.extraction.product_summary == "MacBook Air"
    assert str(proposal.extraction.gross_total) == "2180"
    assert proposal.extraction.currency is None
    assert proposal.missing_fields == ["currency"]
    assert any("currency" in warning for warning in proposal.extraction.warnings)
    assert metrics.extraction_source == "model"


def test_fallback_crash_preserves_real_ocr_and_page_timing_not_zeros() -> None:
    # Partial XML attached to a scanned (no text layer) page, so read_document
    # must actually run OCR before the model crashes - the recovered metrics
    # must reflect that real work, not report it as if nothing happened.
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_partial_scanned.pdf").read_bytes()

    def _crashing_factory() -> _FakeLanguageModel:
        raise RuntimeError("simulated model crash")

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _crashing_factory,
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert metrics.extraction_source == "xml"
    assert metrics.pages_total == 1
    assert metrics.pages_ocr == [1]
    assert metrics.ocr_ms > 0
    assert metrics.total_ms >= metrics.ocr_ms
    assert proposal.extraction.invoice_date.isoformat() == "2026-01-15"


def test_model_calls_records_the_single_extraction_call() -> None:
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    model_response = _valid_model_response()

    _, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert len(metrics.model_calls) == 1
    assert metrics.model_calls[0].response == model_response
    assert metrics.model_calls[0].error is None
    assert metrics.model_calls[0].phase == "extraction"
    assert "Extract these fields" in metrics.model_calls[0].prompt


def test_model_calls_records_the_initial_and_repair_call_in_order() -> None:
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    invalid_response = _valid_model_response(currency="not-a-code")
    narrow_repair_response = json.dumps({"currency": "EUR"})

    class _ScriptedModel:
        def __init__(self) -> None:
            self._responses = [invalid_response, narrow_repair_response]

        def generate(self, prompt: str) -> str:
            return self._responses.pop(0)

    _, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _ScriptedModel(),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=False,
    )

    assert len(metrics.model_calls) == 2
    assert metrics.model_calls[0].response == invalid_response
    assert metrics.model_calls[1].response == narrow_repair_response
    assert metrics.model_calls[0].phase == "extraction"
    assert metrics.model_calls[1].phase == "extraction"
    # The repair call is the narrow one - it must not repeat the full 6-field
    # instructions, only the rejected field's.
    assert "ISO-4217 currency code" in metrics.model_calls[1].prompt
    assert "REWRITTEN as YYYY-MM-DD" not in metrics.model_calls[1].prompt


def test_model_calls_tags_the_shortening_call_distinctly_from_extraction() -> None:
    # Regression: shorten_fields' call used to be numbered as if it were
    # another extraction retry ('Retry 2') even though it's an unrelated
    # pass with its own prompt - it must be tagged by its own phase instead.
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    model_response = _valid_model_response()

    _, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert [call.phase for call in metrics.model_calls] == ["extraction", "shortening"]


def test_model_calls_records_a_crashed_call_with_its_error() -> None:
    pdf_bytes = (FIXTURES_DIR / "with_zugferd_xml_partial_scanned.pdf").read_bytes()

    class _CrashingModel:
        def generate(self, prompt: str) -> str:
            raise RuntimeError("simulated model crash")

    _, metrics = run_document_analysis(
        pdf_bytes,
        lambda: _CrashingModel(),
        model_id="qwen3-0.6b",
        model_revision=None,
        shorten_enabled=True,
    )

    assert len(metrics.model_calls) == 1
    assert metrics.model_calls[0].response is None
    assert "simulated model crash" in metrics.model_calls[0].error
    assert metrics.model_calls[0].phase == "extraction"
