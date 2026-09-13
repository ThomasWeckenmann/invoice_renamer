"""Tests for the SystemCapabilities contract's validation rules."""

import pytest
from pydantic import ValidationError

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities


def _capabilities(**overrides: object) -> SystemCapabilities:
    defaults: dict[str, object] = {
        "acceleration": AccelerationBackend.CPU,
        "memory_gb": 16.0,
        "free_disk_gb": 200.0,
    }
    defaults.update(overrides)
    return SystemCapabilities(**defaults)  # type: ignore[arg-type]


def test_valid_construction_round_trips() -> None:
    capabilities = _capabilities(acceleration=AccelerationBackend.MPS)

    assert capabilities.acceleration is AccelerationBackend.MPS


@pytest.mark.parametrize("field", ["memory_gb", "free_disk_gb"])
def test_negative_values_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _capabilities(**{field: -1.0})


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
@pytest.mark.parametrize("field", ["memory_gb", "free_disk_gb"])
def test_non_finite_values_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        _capabilities(**{field: value})
