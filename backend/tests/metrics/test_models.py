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
