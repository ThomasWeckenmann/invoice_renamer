"""Tests for hardware-compatibility judging of catalog entries."""

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.compatibility import check_compatibility


def _entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-model-small",
        "display_name": "Example Model (Small)",
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=2 * 1024**3)],
        "memory_tier": MemoryTier.SMALL,
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
    result = check_compatibility(_entry(), _capabilities())

    assert result.compatible is True
    assert result.reasons == []


def test_insufficient_memory_is_incompatible() -> None:
    result = check_compatibility(_entry(memory_tier=MemoryTier.LARGE), _capabilities(memory_gb=8.0))

    assert result.compatible is False
    assert any("memory" in reason for reason in result.reasons)


def test_insufficient_disk_is_incompatible() -> None:
    huge_file = ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=500 * 1024**3)
    result = check_compatibility(_entry(files=[huge_file]), _capabilities(free_disk_gb=10.0))

    assert result.compatible is False
    assert any("disk" in reason for reason in result.reasons)


def test_installed_entry_is_not_judged_against_download_disk_space() -> None:
    result = check_compatibility(_entry(), _capabilities(free_disk_gb=1.0), is_installed=True)

    assert result.compatible is True
    assert result.reasons == []


def test_uninstalled_entry_is_still_judged_against_download_disk_space() -> None:
    result = check_compatibility(_entry(), _capabilities(free_disk_gb=1.0), is_installed=False)

    assert result.compatible is False
    assert any("disk" in reason for reason in result.reasons)


def test_installed_entry_is_still_judged_against_memory() -> None:
    result = check_compatibility(
        _entry(memory_tier=MemoryTier.LARGE),
        _capabilities(memory_gb=4.0),
        is_installed=True,
    )

    assert result.compatible is False
    assert any("memory" in reason for reason in result.reasons)
