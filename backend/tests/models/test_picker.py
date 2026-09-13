"""Tests for combining the catalog with install status, compatibility, and cloud-key gating."""

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile, ModelKind
from invoice_renamer.models.picker import InstallStatus, build_model_picker


def _open_local_entry(**overrides: object) -> ModelCatalogEntry:
    defaults: dict[str, object] = {
        "id": "example-open-small",
        "display_name": "Example Open Model (Small)",
        "kind": ModelKind.OPEN_LOCAL,
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [ModelFile(path="model.safetensors", sha256="a" * 64, size_bytes=1024**3)],
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


_CAPABILITIES = SystemCapabilities(
    acceleration=AccelerationBackend.CPU, memory_gb=16.0, free_disk_gb=200.0
)


def test_installed_entry_has_installed_status() -> None:
    entry = _open_local_entry()

    [picked] = build_model_picker(
        [entry], installed_ids={entry.id}, capabilities=_CAPABILITIES, has_cloud_key=False
    )

    assert picked.status is InstallStatus.INSTALLED


def test_uninstalled_entry_has_not_installed_status() -> None:
    entry = _open_local_entry()

    [picked] = build_model_picker(
        [entry], installed_ids=set(), capabilities=_CAPABILITIES, has_cloud_key=False
    )

    assert picked.status is InstallStatus.NOT_INSTALLED


def test_closed_entry_requires_cloud_key_when_absent() -> None:
    entry = _closed_cloud_entry()

    [picked] = build_model_picker(
        [entry], installed_ids=set(), capabilities=_CAPABILITIES, has_cloud_key=False
    )

    assert picked.requires_cloud_key is True


def test_closed_entry_does_not_require_cloud_key_when_present() -> None:
    entry = _closed_cloud_entry()

    [picked] = build_model_picker(
        [entry], installed_ids=set(), capabilities=_CAPABILITIES, has_cloud_key=True
    )

    assert picked.requires_cloud_key is False


def test_open_local_entry_never_requires_cloud_key() -> None:
    entry = _open_local_entry()

    [picked] = build_model_picker(
        [entry], installed_ids=set(), capabilities=_CAPABILITIES, has_cloud_key=False
    )

    assert picked.requires_cloud_key is False


def test_installed_entry_is_not_penalized_for_its_own_download_size() -> None:
    entry = _open_local_entry()
    low_disk = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=16.0, free_disk_gb=0.1
    )

    [picked] = build_model_picker(
        [entry], installed_ids={entry.id}, capabilities=low_disk, has_cloud_key=False
    )

    assert picked.compatible is True
    assert picked.compatibility_reasons == []


def test_uninstalled_entry_is_still_penalized_for_insufficient_disk() -> None:
    entry = _open_local_entry()
    low_disk = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=16.0, free_disk_gb=0.1
    )

    [picked] = build_model_picker(
        [entry], installed_ids=set(), capabilities=low_disk, has_cloud_key=False
    )

    assert picked.compatible is False
    assert any("disk" in reason for reason in picked.compatibility_reasons)


def test_incompatible_entry_stays_listed_but_flagged() -> None:
    entry = _open_local_entry(memory_tier=MemoryTier.LARGE)
    low_memory = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=4.0, free_disk_gb=200.0
    )

    [picked] = build_model_picker(
        [entry], installed_ids=set(), capabilities=low_memory, has_cloud_key=False
    )

    assert picked.compatible is False
    assert picked.compatibility_reasons != []
