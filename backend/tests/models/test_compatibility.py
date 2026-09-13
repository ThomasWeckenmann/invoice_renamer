"""Tests for hardware-compatibility judging of catalog entries."""

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile, ModelKind
from invoice_renamer.models.compatibility import check_compatibility


def _open_local_entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-open-small",
        "display_name": "Example Open Model (Small)",
        "kind": ModelKind.OPEN_LOCAL,
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=2 * 1024**3)],
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
    }
    defaults.update(overrides)
    return ModelCatalogEntry(**defaults)  # type: ignore[arg-type]


def _capabilities(**overrides: object) -> SystemCapabilities:
    defaults: dict[str, object] = {
        "acceleration": AccelerationBackend.CPU,
        "memory_gb": 16.0,
        "free_disk_gb": 200.0,
    }
    defaults.update(overrides)
    return SystemCapabilities(**defaults)  # type: ignore[arg-type]


def test_entry_that_fits_is_compatible() -> None:
    result = check_compatibility(_open_local_entry(), _capabilities())

    assert result.compatible is True
    assert result.reasons == []


def test_insufficient_memory_is_incompatible() -> None:
    result = check_compatibility(
        _open_local_entry(memory_tier=MemoryTier.LARGE), _capabilities(memory_gb=8.0)
    )

    assert result.compatible is False
    assert any("memory" in reason for reason in result.reasons)


def test_insufficient_disk_is_incompatible() -> None:
    huge_file = ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=500 * 1024**3)
    result = check_compatibility(
        _open_local_entry(files=[huge_file]), _capabilities(free_disk_gb=10.0)
    )

    assert result.compatible is False
    assert any("disk" in reason for reason in result.reasons)


def test_installed_entry_is_not_judged_against_download_disk_space() -> None:
    result = check_compatibility(
        _open_local_entry(), _capabilities(free_disk_gb=1.0), is_installed=True
    )

    assert result.compatible is True
    assert result.reasons == []


def test_uninstalled_entry_is_still_judged_against_download_disk_space() -> None:
    result = check_compatibility(
        _open_local_entry(), _capabilities(free_disk_gb=1.0), is_installed=False
    )

    assert result.compatible is False
    assert any("disk" in reason for reason in result.reasons)


def test_installed_entry_is_still_judged_against_memory() -> None:
    result = check_compatibility(
        _open_local_entry(memory_tier=MemoryTier.LARGE),
        _capabilities(memory_gb=4.0),
        is_installed=True,
    )

    assert result.compatible is False
    assert any("memory" in reason for reason in result.reasons)


def test_closed_cloud_entries_are_always_compatible() -> None:
    result = check_compatibility(
        _closed_cloud_entry(), _capabilities(memory_gb=0.5, free_disk_gb=0.1)
    )

    assert result.compatible is True
    assert result.reasons == []
