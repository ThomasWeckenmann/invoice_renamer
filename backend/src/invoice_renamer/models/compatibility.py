"""Judges whether a catalog entry can run on the detected host."""

from pydantic import BaseModel, Field

from invoice_renamer.models.capabilities import SystemCapabilities
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelKind

_BYTES_PER_GB = 1024**3

# Rough minimum system/unified memory recommended per tier, in GB. A guideline
# for surfacing compatibility, not a benchmarked requirement.
_MEMORY_TIER_MINIMUMS_GB: dict[MemoryTier, float] = {
    MemoryTier.SMALL: 8.0,
    MemoryTier.MEDIUM: 16.0,
    MemoryTier.LARGE: 32.0,
}


class CompatibilityResult(BaseModel):
    compatible: bool
    reasons: list[str] = Field(default_factory=list)


def check_compatibility(
    entry: ModelCatalogEntry, capabilities: SystemCapabilities, *, is_installed: bool = False
) -> CompatibilityResult:
    if entry.kind is ModelKind.CLOSED_CLOUD:
        return CompatibilityResult(compatible=True)

    assert entry.memory_tier is not None  # enforced by ModelCatalogEntry validation
    reasons = []

    minimum_gb = _MEMORY_TIER_MINIMUMS_GB[entry.memory_tier]
    if capabilities.memory_gb < minimum_gb:
        reasons.append(
            f"needs at least {minimum_gb:g} GB memory; "
            f"this device has {capabilities.memory_gb:g} GB"
        )

    # An already-installed model has already paid its disk cost; only a model
    # still needing to be downloaded should be judged against free disk space.
    if not is_installed:
        needed_gb = entry.total_size_bytes / _BYTES_PER_GB
        if capabilities.free_disk_gb < needed_gb:
            reasons.append(
                f"needs {needed_gb:.1f} GB free disk; "
                f"this device has {capabilities.free_disk_gb:.1f} GB"
            )

    return CompatibilityResult(compatible=not reasons, reasons=reasons)
