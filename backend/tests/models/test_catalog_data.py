"""Sanity checks for the real (non-placeholder) shortlisted catalog entries."""

import re

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import ModelKind
from invoice_renamer.models.catalog_data import SHORTLISTED_CATALOG
from invoice_renamer.models.compatibility import check_compatibility

_PINNED_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def test_entries_have_unique_ids() -> None:
    ids = [entry.id for entry in SHORTLISTED_CATALOG]
    assert len(ids) == len(set(ids))


def test_entries_are_open_local() -> None:
    assert all(entry.kind is ModelKind.OPEN_LOCAL for entry in SHORTLISTED_CATALOG)


def test_entries_pin_a_real_commit_revision_not_a_floating_ref() -> None:
    for entry in SHORTLISTED_CATALOG:
        assert entry.revision is not None
        assert _PINNED_REVISION.match(entry.revision), entry.id


def test_every_file_has_a_real_sha256() -> None:
    for entry in SHORTLISTED_CATALOG:
        for file in entry.files:
            assert _SHA256.match(file.sha256), f"{entry.id}: {file.path}"


def test_entries_are_compatible_with_the_plans_16gb_floor() -> None:
    capabilities = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=16.0, free_disk_gb=100.0
    )
    for entry in SHORTLISTED_CATALOG:
        result = check_compatibility(entry, capabilities)
        assert result.compatible, (entry.id, result.reasons)
