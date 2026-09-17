"""Tests for combining the catalog with install status and compatibility."""

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.picker import InstallStatus, build_model_picker


def _entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-model-small",
        "display_name": "Example Model (Small)",
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=1024**3)],
        "memory_tier": MemoryTier.SMALL,
    }
    defaults.update(overrides)
    return ModelCatalogEntry(**defaults)  # type: ignore[arg-type]


_CAPABILITIES = SystemCapabilities(
    acceleration=AccelerationBackend.CPU, memory_gb=16.0, free_disk_gb=200.0
)


def test_installed_entry_has_installed_status() -> None:
    entry = _entry()

    [picked] = build_model_picker([entry], installed_ids={entry.id}, capabilities=_CAPABILITIES)

    assert picked.status is InstallStatus.INSTALLED


def test_uninstalled_entry_has_not_installed_status() -> None:
    entry = _entry()

    [picked] = build_model_picker([entry], installed_ids=set(), capabilities=_CAPABILITIES)

    assert picked.status is InstallStatus.NOT_INSTALLED


def test_installed_entry_is_not_penalized_for_its_own_download_size() -> None:
    entry = _entry()
    low_disk = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=16.0, free_disk_gb=0.1
    )

    [picked] = build_model_picker([entry], installed_ids={entry.id}, capabilities=low_disk)

    assert picked.compatible is True
    assert picked.compatibility_reasons == []


def test_uninstalled_entry_is_still_penalized_for_insufficient_disk() -> None:
    entry = _entry()
    low_disk = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=16.0, free_disk_gb=0.1
    )

    [picked] = build_model_picker([entry], installed_ids=set(), capabilities=low_disk)

    assert picked.compatible is False
    assert any("disk" in reason for reason in picked.compatibility_reasons)


def test_incompatible_entry_stays_listed_but_flagged() -> None:
    entry = _entry(memory_tier=MemoryTier.LARGE)
    low_memory = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=4.0, free_disk_gb=200.0
    )

    [picked] = build_model_picker([entry], installed_ids=set(), capabilities=low_memory)

    assert picked.compatible is False
    assert picked.compatibility_reasons != []
