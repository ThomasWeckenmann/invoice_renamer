"""Tests for the RunMetrics contract's validation rules and defaults."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_renamer.metrics.models import CostSource, ExecutionMode, RunMetrics


def _metrics(**overrides: object) -> RunMetrics:
    defaults: dict[str, object] = {
        "total_ms": 1200,
        "pdf_extraction_ms": 50,
        "ocr_ms": 0,
        "inference_ms": 1100,
        "execution_mode": ExecutionMode.LOCAL,
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
    assert metrics.cost is None


def test_full_local_run_round_trips() -> None:
    metrics = _metrics(pages_total=3, pages_ocr=[2, 3], input_tokens=500, output_tokens=120)

    assert metrics.execution_mode is ExecutionMode.LOCAL
    assert metrics.pages_ocr == [2, 3]


def test_full_cloud_run_with_labeled_cost_round_trips() -> None:
    metrics = _metrics(
        execution_mode=ExecutionMode.CLOUD,
        provider="openrouter",
        model_id="anthropic/claude-haiku",
        cost=Decimal("0.0042"),
        cost_currency="USD",
        cost_source=CostSource.PROVIDER_REPORTED,
    )

    assert metrics.cost == Decimal("0.0042")
    assert metrics.cost_source is CostSource.PROVIDER_REPORTED


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


def test_negative_cost_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(cost=Decimal("-1"), cost_currency="USD", cost_source=CostSource.ESTIMATED)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_cost_is_rejected(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        _metrics(cost=value, cost_currency="USD", cost_source=CostSource.ESTIMATED)


@pytest.mark.parametrize("currency", ["usd", "ZZZ", "ÄBC"])
def test_invalid_cost_currency_is_rejected(currency: str) -> None:
    with pytest.raises(ValidationError):
        _metrics(cost=Decimal("1"), cost_currency=currency, cost_source=CostSource.ESTIMATED)


def test_cost_without_currency_or_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(cost=Decimal("1"))


def test_cost_without_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metrics(cost=Decimal("1"), cost_currency="USD")
