"""Tests for the model catalog contract's validation and defaults."""

import pytest
from pydantic import ValidationError

from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile


def _entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-model-small",
        "display_name": "Example Model (Small)",
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=1_000_000)],
        "memory_tier": MemoryTier.SMALL,
    }
    defaults.update(overrides)
    return ModelCatalogEntry(**defaults)  # type: ignore[arg-type]


def test_entry_round_trips() -> None:
    entry = _entry()

    assert entry.repository == "example-org/example-model"
    assert entry.total_size_bytes == 1_000_000


@pytest.mark.parametrize("missing_field", ["repository", "revision", "files", "memory_tier"])
def test_entry_requires_required_fields(missing_field: str) -> None:
    overrides = {missing_field: None if missing_field != "files" else []}
    with pytest.raises(ValidationError):
        _entry(**overrides)


@pytest.mark.parametrize("empty_field", ["repository", "revision"])
def test_entry_rejects_empty_string_fields(empty_field: str) -> None:
    with pytest.raises(ValidationError):
        _entry(**{empty_field: ""})


def test_negative_file_size_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelFile(path="model.bin", sha256="a" * 64, size_bytes=-1)


def test_total_size_bytes_sums_all_files() -> None:
    entry = _entry(
        files=[
            ModelFile(path="a.bin", sha256="a" * 64, size_bytes=100),
            ModelFile(path="b.bin", sha256="b" * 64, size_bytes=250),
        ]
    )

    assert entry.total_size_bytes == 350
