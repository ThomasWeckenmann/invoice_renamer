"""Tests for the soft pre-flight memory-headroom check."""

from invoice_renamer.analysis.memory_preflight import check_memory_headroom
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile


def _entry(estimated_memory_gb: float | None) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id="model-a",
        display_name="Model A",
        license="apache-2.0",
        repository="example-org/model-a",
        revision="a" * 40,
        memory_tier=MemoryTier.SMALL,
        files=[ModelFile(path="model.bin", sha256="a" * 64, size_bytes=1)],
        estimated_memory_gb=estimated_memory_gb,
    )


def test_no_estimate_means_no_warning() -> None:
    assert check_memory_headroom(_entry(None), available_gb=0.1) is None


def test_ample_headroom_produces_no_warning() -> None:
    assert check_memory_headroom(_entry(2.0), available_gb=8.0) is None


def test_thin_margin_produces_a_warning_naming_the_model() -> None:
    warning = check_memory_headroom(_entry(8.0), available_gb=2.0)

    assert warning is not None
    assert "Model A" in warning
    assert "8.0 GB" in warning
    assert "2.0 GB" in warning


def test_headroom_buffer_is_applied_on_top_of_the_estimate() -> None:
    # Available memory exactly matches the estimate, with no buffer left over.
    warning = check_memory_headroom(_entry(4.0), available_gb=4.0)

    assert warning is not None


def test_reusing_the_already_resident_model_is_not_double_counted() -> None:
    # available_gb already reflects this same model sitting resident (e.g. 12
    # GB dropped to 3.8 GB after it loaded); reusing it needs no new memory.
    warning = check_memory_headroom(_entry(8.0), available_gb=3.8, resident_memory_gb=8.0)

    assert warning is None


def test_a_different_resident_model_is_credited_back_as_freeable() -> None:
    # A big resident model (8 GB) is about to be unloaded to make room for a
    # small one (2 GB) - the 8 GB it currently occupies should count as
    # freeable, not as unavailable for the incoming model.
    warning = check_memory_headroom(_entry(2.0), available_gb=3.8, resident_memory_gb=8.0)

    assert warning is None


def test_resident_memory_credit_is_not_enough_to_cover_a_much_bigger_model() -> None:
    warning = check_memory_headroom(_entry(20.0), available_gb=3.8, resident_memory_gb=8.0)

    assert warning is not None
