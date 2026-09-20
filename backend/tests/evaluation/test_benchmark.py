"""Tests for benchmark scoring and aggregation, using a scripted LanguageModel
fake so these run without a real model."""

import json
from pathlib import Path

from invoice_renamer.evaluation.benchmark import run_benchmark, score_extraction
from invoice_renamer.extraction.models import InvoiceExtraction, Language

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"

_CORRECT_RESPONSE = json.dumps(
    {
        "invoice_date": None,
        "seller": "Apple",
        "product_summary": "MacBook Air",
        "gross_total": "2180.00",
        "currency": "EUR",
        "language": "en",
        "warnings": [],
    }
)

_SALVAGEABLE_BAD_CURRENCY_RESPONSE = json.dumps(
    {
        "invoice_date": None,
        "seller": "Apple",
        "product_summary": "MacBook Air",
        "gross_total": "2180.00",
        "currency": "not-a-code",
        "language": "en",
        "warnings": [],
    }
)

_ALL_FIELDS_INVALID_RESPONSE = json.dumps(
    {
        "invoice_date": "not-a-date",
        "seller": ["bad"],
        "product_summary": ["bad"],
        "gross_total": "not-a-number",
        "currency": "not-a-code",
        "language": "en",
    }
)

_WRONG_SELLER_RESPONSE = json.dumps(
    {
        "invoice_date": None,
        "seller": "Someone Else",
        "product_summary": "MacBook Air",
        "gross_total": "2180.00",
        "currency": "EUR",
        "language": "en",
        "warnings": [],
    }
)

_GROUND_TRUTH = {
    "selectable_text_en.pdf": {
        "seller": "Apple",
        "product_summary": "MacBook Air",
        "gross_total": "2180.00",
        "currency": "EUR",
    }
}


class _ScriptedLanguageModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def generate(self, prompt: str) -> str:
        return self._responses.pop(0)


def test_score_extraction_matches_case_insensitively_and_numerically() -> None:
    extraction = InvoiceExtraction(seller="apple ", gross_total="2180", currency="EUR")

    scores = score_extraction(
        extraction, {"seller": "Apple", "gross_total": "2180.00", "currency": "eur"}
    )

    assert scores["seller"] is True
    assert scores["gross_total"] is True
    assert scores["currency"] is True


def test_score_extraction_tolerates_extra_detail_in_seller_and_product_summary() -> None:
    # The model's answer is more detailed (a full legal name, a trailing
    # region suffix) than the shorter canonical ground truth - that's the
    # real-world padding pattern this scoring loosens for.
    extraction = InvoiceExtraction(
        seller="Lauschke Caravan und Freizeit e.K. - Gewerbering 1A",
        product_summary="Red Internet & Phone 250 Cable U",
    )

    scores = score_extraction(
        extraction,
        {
            "seller": "Lauschke Caravan und Freizeit e.K.",
            "product_summary": "Red Internet & Phone 250",
        },
    )

    assert scores["seller"] is True
    assert scores["product_summary"] is True


def test_score_extraction_still_rejects_an_unrelated_seller_or_product_summary() -> None:
    extraction = InvoiceExtraction(seller="Someone Else", product_summary="Unrelated Product")

    scores = score_extraction(extraction, {"seller": "Apple", "product_summary": "MacBook Air"})

    assert scores["seller"] is False
    assert scores["product_summary"] is False


def test_score_extraction_rejects_a_vague_or_truncated_wrong_answer() -> None:
    # A short wrong answer must not count as correct just because it happens
    # to be a substring of the right one in the other direction.
    extraction = InvoiceExtraction(seller="a", product_summary="Pro")

    scores = score_extraction(
        extraction, {"seller": "MediaMarkt", "product_summary": "MacBook Pro 16"}
    )

    assert scores["seller"] is False
    assert scores["product_summary"] is False


def test_score_extraction_requires_a_whole_word_boundary_match() -> None:
    # "art" is a substring of "cart" but not the same word - must not match.
    extraction = InvoiceExtraction(seller="Cartwright Ltd")

    scores = score_extraction(extraction, {"seller": "art"})

    assert scores["seller"] is False


def test_score_extraction_leaves_unlabeled_fields_unscored() -> None:
    extraction = InvoiceExtraction(seller="Apple")

    scores = score_extraction(extraction, {"seller": "Apple"})

    assert scores["seller"] is True
    assert scores["product_summary"] is None


def test_run_benchmark_on_a_correct_response_scores_full_accuracy() -> None:
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf"],
        model,
        model_id="test-model",
        ground_truth=_GROUND_TRUTH,
    )

    assert result.field_accuracy["seller"] == 1.0
    assert result.correction_rate == 0.0
    assert result.failure_rate == 0.0
    assert result.invoices[0].extraction.language is Language.ENGLISH


def test_run_benchmark_on_a_wrong_field_needs_correction() -> None:
    model = _ScriptedLanguageModel([_WRONG_SELLER_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf"],
        model,
        model_id="test-model",
        ground_truth=_GROUND_TRUTH,
    )

    assert result.field_accuracy["seller"] == 0.0
    assert result.correction_rate == 1.0


def test_run_benchmark_scores_a_salvageable_single_field_mistake_as_not_failed() -> None:
    # A single-field model mistake (invalid currency) must be salvaged, not
    # discarded whole - the benchmark's failure marker only fires for a
    # response nothing useful ever survives from.
    model = _ScriptedLanguageModel(
        [_SALVAGEABLE_BAD_CURRENCY_RESPONSE, _SALVAGEABLE_BAD_CURRENCY_RESPONSE]
    )

    result = run_benchmark([FIXTURES_DIR / "selectable_text_en.pdf"], model, model_id="test-model")

    assert result.invoices[0].failed is False
    assert result.invoices[0].extraction.seller == "Apple"
    assert result.invoices[0].extraction.currency is None
    assert any("currency" in warning for warning in result.invoices[0].extraction.warnings)


def test_run_benchmark_on_repeated_all_fields_invalid_still_reports_terminal_failure() -> None:
    # Nothing useful survives salvage here (every filename field is invalid),
    # so repair must still be attempted and, once that also fails, the
    # terminal failure marker must still fire exactly as before salvage existed.
    model = _ScriptedLanguageModel([_ALL_FIELDS_INVALID_RESPONSE, _ALL_FIELDS_INVALID_RESPONSE])

    result = run_benchmark([FIXTURES_DIR / "selectable_text_en.pdf"], model, model_id="test-model")

    assert result.invoices[0].failed is True
    assert any(
        "model output could not be validated" in warning
        for warning in result.invoices[0].extraction.warnings
    )


def test_run_benchmark_on_malformed_json_counts_as_a_failure() -> None:
    model = _ScriptedLanguageModel(["not json", "still not json"])

    result = run_benchmark([FIXTURES_DIR / "selectable_text_en.pdf"], model, model_id="test-model")

    assert result.failure_rate == 1.0
    assert result.correction_rate == 1.0


def test_correction_and_hallucination_rate_are_unavailable_without_ground_truth() -> None:
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark([FIXTURES_DIR / "selectable_text_en.pdf"], model, model_id="test-model")

    assert result.correction_rate is None
    assert result.hallucination_rate is None


def test_correction_rate_ignores_invoices_with_no_labels_even_in_a_mixed_batch() -> None:
    # One invoice has a ground-truth label and is correct; the other has none at
    # all. The unlabeled invoice must not silently count as "no correction needed".
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE, _CORRECT_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf", FIXTURES_DIR / "selectable_text_de.pdf"],
        model,
        model_id="test-model",
        ground_truth={"selectable_text_en.pdf": _GROUND_TRUTH["selectable_text_en.pdf"]},
    )

    assert result.correction_rate == 0.0  # only the one judged invoice counts


def test_hallucination_rate_only_counts_invoices_with_an_explicit_null_label() -> None:
    # Ground truth labels seller but never asserts anything should be null, so
    # there is nothing here that could prove a hallucination either way.
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf"],
        model,
        model_id="test-model",
        ground_truth={"selectable_text_en.pdf": {"seller": "Apple"}},
    )

    assert result.hallucination_rate is None


def test_hallucination_rate_counts_a_value_where_ground_truth_says_null() -> None:
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf"],
        model,
        model_id="test-model",
        ground_truth={"selectable_text_en.pdf": {"invoice_date": None}},
    )

    assert result.hallucination_rate == 0.0  # extraction correctly left invoice_date null


class _CrashingLanguageModel:
    def generate(self, prompt: str) -> str:
        raise RuntimeError("simulated model crash")


class _CrashOnSecondCall:
    """Delegates to a wrapped model for the first call, then crashes - so a
    batch can mix one real result with one crash."""

    def __init__(self, inner: _ScriptedLanguageModel) -> None:
        self._inner = inner
        self._calls = 0

    def generate(self, prompt: str) -> str:
        self._calls += 1
        if self._calls > 1:
            raise RuntimeError("simulated model crash")
        return self._inner.generate(prompt)


def test_run_benchmark_survives_a_crashing_model_and_completes_the_report() -> None:
    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf", FIXTURES_DIR / "selectable_text_de.pdf"],
        _CrashingLanguageModel(),
        model_id="test-model",
    )

    assert len(result.invoices) == 2
    assert result.failure_rate == 1.0


def test_a_crash_during_inference_preserves_real_read_timing_and_page_count() -> None:
    # read_document() succeeds before the model crashes, so that timing/page
    # data is real and must survive into the crash-fallback result.
    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf"], _CrashingLanguageModel(), model_id="test-model"
    )

    metrics = result.invoices[0].metrics
    assert metrics.pages_total == 1
    assert metrics.pdf_extraction_ms >= 0
    assert result.invoices[0].timing_complete is False


def test_a_crashed_invoices_fabricated_zero_does_not_pollute_the_latency_average() -> None:
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf", FIXTURES_DIR / "selectable_text_de.pdf"],
        _CrashOnSecondCall(model),
        model_id="test-model",
    )

    assert result.failure_rate == 0.5
    # If the crashed invoice's zero timing leaked into the average, this would
    # be pulled toward 0 instead of reflecting only the one real measurement.
    assert result.average_total_ms == result.invoices[0].metrics.total_ms


def test_crashed_labeled_fields_count_as_wrong_not_unscored() -> None:
    # Reproduces the reported case: one correct extraction plus one crash must
    # not average out to 100% seller accuracy - the crash is a real miss.
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf", FIXTURES_DIR / "selectable_text_de.pdf"],
        _CrashOnSecondCall(model),
        model_id="test-model",
        ground_truth={
            "selectable_text_en.pdf": _GROUND_TRUTH["selectable_text_en.pdf"],
            "selectable_text_de.pdf": {"seller": "Acme Corp"},
        },
    )

    assert result.field_accuracy["seller"] == 0.5


def test_run_benchmark_survives_an_unreadable_pdf(tmp_path: Path) -> None:
    garbage_pdf = tmp_path / "not_actually_a_pdf.pdf"
    garbage_pdf.write_bytes(b"this is not PDF content")
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark(
        [garbage_pdf, FIXTURES_DIR / "selectable_text_en.pdf"], model, model_id="test-model"
    )

    assert len(result.invoices) == 2
    assert result.invoices[0].failed is True
    assert result.invoices[1].failed is False


def test_ocr_time_is_measured_separately_from_pdf_extraction_time() -> None:
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark([FIXTURES_DIR / "scanned_invoice.pdf"], model, model_id="test-model")

    metrics = result.invoices[0].metrics
    assert metrics.ocr_ms > 0
    assert metrics.total_ms == metrics.pdf_extraction_ms + metrics.ocr_ms + metrics.inference_ms


def test_to_dict_serializes_nested_pydantic_models_as_structured_objects() -> None:
    model = _ScriptedLanguageModel([_CORRECT_RESPONSE])

    result = run_benchmark(
        [FIXTURES_DIR / "selectable_text_en.pdf"],
        model,
        model_id="test-model",
        ground_truth=_GROUND_TRUTH,
    )

    report = result.to_dict()
    invoice_dict = report["invoices"][0]  # type: ignore[index]
    assert isinstance(invoice_dict["extraction"], dict)
    assert invoice_dict["extraction"]["seller"] == "Apple"
    assert isinstance(invoice_dict["metrics"], dict)
    assert invoice_dict["metrics"]["model_id"] == "test-model"
