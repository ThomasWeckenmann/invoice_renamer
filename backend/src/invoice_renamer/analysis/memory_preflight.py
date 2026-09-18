"""Soft pre-flight check comparing a model's known memory footprint against
currently available system memory, so a thin margin can be surfaced before a
job starts rather than discovered as a crash mid-inference.
"""

from invoice_renamer.models.catalog import ModelCatalogEntry

# Buffer above the model's own footprint reserved for the OS and other apps.
# Free memory is a moving target - another process can claim memory between
# this check and the actual generate() call - so this is advisory only, not
# a hard guarantee the job will succeed.
_HEADROOM_GB = 1.0


def check_memory_headroom(
    entry: ModelCatalogEntry, available_gb: float, *, resident_memory_gb: float = 0.0
) -> str | None:
    """Returns a warning message if available memory looks thin for this
    model, or None if there's no known footprint to compare against or the
    margin looks fine.

    `resident_memory_gb` is the footprint of whatever model is currently
    loaded in the runtime (0 if none, or if its footprint isn't known). That
    memory is already reflected in `available_gb` as *unavailable*, but it
    isn't actually needed on top of what this job requires: if the resident
    model is the one being requested, it will be reused with no new
    allocation; if it's a different one, it gets freed before this model
    loads. Either way it's credited back so a model already paid for isn't
    double-counted against the next job.
    """
    if entry.estimated_memory_gb is None:
        return None

    effective_available_gb = available_gb + resident_memory_gb
    needed_gb = entry.estimated_memory_gb + _HEADROOM_GB
    if effective_available_gb >= needed_gb:
        return None

    return (
        f"{entry.display_name} typically uses about {entry.estimated_memory_gb:.1f} GB of "
        f"memory during analysis, but only {available_gb:.1f} GB is currently free. "
        "Consider closing other applications before starting this job."
    )
