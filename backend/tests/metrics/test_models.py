"""Tests for the RunMetrics contract's validation rules and defaults."""

import pytest
from pydantic import ValidationError

from invoice_renamer.metrics.models import RunMetrics


def _metrics(**overrides: object) -> RunMetrics:
    defaults: dict[str, object] = {
        "total_ms": 1200,
        "pdf_extraction_ms": 50,
        "ocr_ms": 0,
        "inference_ms": 1100,
        "model_id": "qwen2.5-1.5b-instruct",
        "provider": "transformers",
        "pages_total": 1,
    }
    defaults.update(overrides)
    return RunMetrics(**defaults)  # type: ignore[arg-type]


def test_defaults_are_empty_or_none() -> None:
    metrics = _metrics()

    assert metrics.pages_ocr == []
    assert metrics.warnings == []
    assert metrics.model_revision is None
    assert metrics.xml_ms == 0
    assert metrics.extraction_source == "model"
    assert metrics.xml_status == "none"
    assert metrics.xml_attachment_name is None
    assert metrics.xml_profile_id is None
    assert metrics.xml_fields_used == []
    assert metrics.inference_ran is True


def test_a_historical_metrics_blob_without_xml_fields_deserializes_safely() -> None:
    # Shape RunMetrics had before this plan - no xml_ms/extraction_source/etc at
    # all. Must load without error, and must never claim XML was used for a run
    # that never had the chance to see any.
    historical = {
        "total_ms": 900,
        "pdf_extraction_ms": 100,
        "ocr_ms": 0,
        "inference_ms": 800,
        "model_id": "qwen2.5-1.5b-instruct",
        "provider": "transformers",
        "pages_total": 1,
    }

    metrics = RunMetrics.model_validate(historical)

    assert metrics.extraction_source == "model"
    assert metrics.xml_status == "none"
    assert metrics.inference_ran is True


def test_xml_only_run_round_trips() -> None:
    metrics = _metrics(
        total_ms=5,
        pdf_extraction_ms=0,
        ocr_ms=0,
        inference_ms=0,
        xml_ms=5,
        extraction_source="xml",
        xml_status="supported",
        xml_attachment_name="factur-x.xml",
        xml_profile_id="urn:cen.eu:en16931:2017",
        xml_fields_used=["invoice_date", "seller"],
        inference_ran=False,
    )

    assert metrics.extraction_source == "xml"
    assert metrics.inference_ran is False
    assert metrics.xml_fields_used == ["invoice_date", "seller"]


def test_negative_xml_ms_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(xml_ms=-1)


def test_full_run_round_trips() -> None:
    metrics = _metrics(pages_total=3, pages_ocr=[2, 3], input_tokens=500, output_tokens=120)

    assert metrics.pages_ocr == [2, 3]


@pytest.mark.parametrize("field", ["total_ms", "pdf_extraction_ms", "ocr_ms", "inference_ms"])
def test_negative_timings_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _metrics(**{field: -1})


def test_pages_total_below_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(pages_total=0)


def test_pages_ocr_entry_above_pages_total_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(pages_total=2, pages_ocr=[3])


def test_pages_ocr_entry_below_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(pages_total=2, pages_ocr=[0])


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_negative_token_counts_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _metrics(**{field: -1})


def test_negative_tokens_per_second_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(tokens_per_second=-0.1)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_tokens_per_second_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        _metrics(tokens_per_second=value)
