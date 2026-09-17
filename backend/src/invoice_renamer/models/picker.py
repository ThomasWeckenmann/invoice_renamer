"""Builds the model-picker view: catalog entries joined with status and
hardware compatibility.
"""

from enum import Enum

from pydantic import BaseModel

from invoice_renamer.models.capabilities import SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry
from invoice_renamer.models.compatibility import check_compatibility


class InstallStatus(str, Enum):
    NOT_INSTALLED = "not_installed"
    DOWNLOADING = "downloading"
    INSTALLED = "installed"
    VERIFICATION_FAILED = "verification_failed"


class ModelPickerEntry(BaseModel):
    entry: ModelCatalogEntry
    status: InstallStatus
    compatible: bool
    compatibility_reasons: list[str]


def build_model_picker(
    catalog: list[ModelCatalogEntry],
    *,
    installed_ids: set[str],
    capabilities: SystemCapabilities,
) -> list[ModelPickerEntry]:
    entries = []
    for entry in catalog:
        is_installed = entry.id in installed_ids
        compatibility = check_compatibility(entry, capabilities, is_installed=is_installed)
        status = InstallStatus.INSTALLED if is_installed else InstallStatus.NOT_INSTALLED
        entries.append(
            ModelPickerEntry(
                entry=entry,
                status=status,
                compatible=compatibility.compatible,
                compatibility_reasons=compatibility.reasons,
            )
        )
    return entries
