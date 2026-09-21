"""Sanity checks for the real (non-placeholder) app GGUF catalog entries."""

import re
from pathlib import Path

from invoice_renamer.models.capabilities import AccelerationBackend, SystemCapabilities
from invoice_renamer.models.catalog import ModelCatalogEntry, ModelFile
from invoice_renamer.models.compatibility import check_compatibility
from invoice_renamer.models.gguf_catalog import SHORTLISTED_CATALOG
from invoice_renamer.models.installer import _resolve_file_path, install_dir_for

_PINNED_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _gguf_file(entry: ModelCatalogEntry) -> ModelFile:
    gguf_files = [file for file in entry.files if file.path.endswith(".gguf")]
    assert len(gguf_files) == 1, entry.id
    return gguf_files[0]


def test_entries_have_unique_ids() -> None:
    ids = [entry.id for entry in SHORTLISTED_CATALOG]
    assert len(ids) == len(set(ids))


def test_entries_pin_a_real_commit_revision_not_a_floating_ref() -> None:
    for entry in SHORTLISTED_CATALOG:
        assert entry.revision is not None
        assert _PINNED_REVISION.match(entry.revision), entry.id


def test_every_file_has_a_real_sha256() -> None:
    for entry in SHORTLISTED_CATALOG:
        for file in entry.files:
            assert _SHA256.match(file.sha256), f"{entry.id}: {file.path}"


def test_each_entry_declares_exactly_one_gguf_file() -> None:
    for entry in SHORTLISTED_CATALOG:
        gguf_files = [file for file in entry.files if file.path.endswith(".gguf")]
        assert len(gguf_files) == 1, entry.id


def test_each_entry_declares_its_required_tokenizer_and_template_assets() -> None:
    required = {"tokenizer.json", "tokenizer_config.json"}
    for entry in SHORTLISTED_CATALOG:
        paths = {file.path for file in entry.files}
        assert required <= paths, entry.id


def test_the_gguf_file_uses_the_entrys_own_source() -> None:
    for entry in SHORTLISTED_CATALOG:
        gguf_file = _gguf_file(entry)
        assert gguf_file.repository is None
        assert gguf_file.revision is None


def test_tokenizer_files_pin_a_source_distinct_from_the_guffs_own() -> None:
    for entry in SHORTLISTED_CATALOG:
        gguf_file = _gguf_file(entry)
        tokenizer_files = [file for file in entry.files if file is not gguf_file]
        assert tokenizer_files, entry.id
        for file in tokenizer_files:
            assert file.repository is not None, (entry.id, file.path)
            assert file.revision is not None, (entry.id, file.path)
            assert _PINNED_REVISION.match(file.revision), (entry.id, file.path)
            assert (file.repository, file.revision) != (entry.repository, entry.revision), (
                entry.id,
                file.path,
            )


def test_all_tokenizer_files_in_an_entry_share_one_base_model_source() -> None:
    for entry in SHORTLISTED_CATALOG:
        gguf_file = _gguf_file(entry)
        tokenizer_files = [file for file in entry.files if file is not gguf_file]
        sources = {(file.repository, file.revision) for file in tokenizer_files}
        assert len(sources) == 1, entry.id


def test_entries_are_compatible_with_the_plans_8gb_floor() -> None:
    capabilities = SystemCapabilities(
        acceleration=AccelerationBackend.CPU, memory_gb=8.0, free_disk_gb=100.0
    )
    for entry in SHORTLISTED_CATALOG:
        result = check_compatibility(entry, capabilities)
        assert result.compatible, (entry.id, result.reasons)


def test_every_entrys_files_resolve_within_its_install_dir(tmp_path: Path) -> None:
    for entry in SHORTLISTED_CATALOG:
        install_dir = install_dir_for(entry, tmp_path)
        install_dir.mkdir(parents=True)
        for file in entry.files:
            resolved = _resolve_file_path(install_dir, file)
            assert resolved.resolve().is_relative_to(install_dir.resolve()), (entry.id, file.path)
