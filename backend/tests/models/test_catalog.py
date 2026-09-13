"""Tests for the model catalog contract's per-kind validation and defaults."""

import pytest
from pydantic import ValidationError

from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile, ModelKind


def _open_local_entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-open-small",
        "display_name": "Example Open Model (Small)",
        "kind": ModelKind.OPEN_LOCAL,
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=1_000_000)],
        "memory_tier": MemoryTier.SMALL,
    }
    defaults.update(overrides)
    return ModelCatalogEntry(**defaults)  # type: ignore[arg-type]


def _closed_cloud_entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-closed",
        "display_name": "Example Closed Model",
        "kind": ModelKind.CLOSED_CLOUD,
        "license": "proprietary",
        "provider": "openrouter",
        "context_window": 128_000,
    }
    defaults.update(overrides)
    return ModelCatalogEntry(**defaults)  # type: ignore[arg-type]


def test_open_local_entry_round_trips() -> None:
    entry = _open_local_entry()

    assert entry.repository == "example-org/example-model"
    assert entry.total_size_bytes == 1_000_000


def test_closed_cloud_entry_round_trips() -> None:
    entry = _closed_cloud_entry()

    assert entry.provider == "openrouter"
    assert entry.total_size_bytes == 0


@pytest.mark.parametrize("missing_field", ["repository", "revision", "files", "memory_tier"])
def test_open_local_entry_requires_local_fields(missing_field: str) -> None:
    overrides = {missing_field: None if missing_field != "files" else []}
    with pytest.raises(ValidationError):
        _open_local_entry(**overrides)


def test_closed_cloud_entry_requires_provider() -> None:
    with pytest.raises(ValidationError):
        _closed_cloud_entry(provider=None)


def test_negative_file_size_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelFile(path="model.bin", sha256="a" * 64, size_bytes=-1)


def test_total_size_bytes_sums_all_files() -> None:
    entry = _open_local_entry(
        files=[
            ModelFile(path="a.bin", sha256="a" * 64, size_bytes=100),
            ModelFile(path="b.bin", sha256="b" * 64, size_bytes=250),
        ]
    )

    assert entry.total_size_bytes == 350
