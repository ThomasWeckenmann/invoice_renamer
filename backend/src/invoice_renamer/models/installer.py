"""Installs/removes catalog models on disk with checksum verification,
resuming at file granularity by skipping already-verified files.
"""

import hashlib
import json
import os
import platform
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from huggingface_hub import hf_hub_download

from invoice_renamer.models.catalog import ModelCatalogEntry, ModelFile

DATA_DIR_ENV_VAR = "INVOICE_RENAMER_DATA_DIR"

_MARKER_NAME = ".installed.json"
_HASH_CHUNK_SIZE = 1024 * 1024


def resolve_data_dir() -> Path:
    env_value = os.environ.get(DATA_DIR_ENV_VAR)
    if env_value:
        data_dir = Path(env_value)
    elif platform.system() == "Darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "invoice-renamer"
    else:
        data_dir = Path.home() / ".local" / "share" / "invoice-renamer"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def install_dir_for(entry: ModelCatalogEntry, data_dir: Path) -> Path:
    assert entry.revision is not None  # enforced by ModelCatalogEntry validation
    return data_dir / "models" / entry.id / entry.revision


def _resolve_file_path(install_dir: Path, file: ModelFile) -> Path:
    resolved_install_dir = install_dir.resolve()
    resolved_file_path = (install_dir / file.path).resolve()
    if not resolved_file_path.is_relative_to(resolved_install_dir):
        raise ValueError(f"file path escapes install directory: {file.path!r}")
    return install_dir / file.path


def _marker_payload(entry: ModelCatalogEntry) -> dict[str, object]:
    return {
        "model_id": entry.id,
        "revision": entry.revision,
        "files": [
            {"path": file.path, "sha256": file.sha256, "size_bytes": file.size_bytes}
            for file in entry.files
        ],
    }


def _read_marker(install_dir: Path) -> dict[str, object] | None:
    marker_path = install_dir / _MARKER_NAME
    try:
        payload: dict[str, object] = json.loads(marker_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload


def _write_marker_atomically(install_dir: Path, entry: ModelCatalogEntry) -> None:
    tmp_path = install_dir / f".{_MARKER_NAME}.tmp"
    tmp_path.write_text(json.dumps(_marker_payload(entry)))
    os.replace(tmp_path, install_dir / _MARKER_NAME)


def _invalidate_marker(install_dir: Path) -> None:
    (install_dir / _MARKER_NAME).unlink(missing_ok=True)


def is_installed(entry: ModelCatalogEntry, data_dir: Path) -> bool:
    install_dir = install_dir_for(entry, data_dir)
    marker = _read_marker(install_dir)
    if marker != _marker_payload(entry):
        return False

    for file in entry.files:
        dest = _resolve_file_path(install_dir, file)
        if not dest.exists() or dest.stat().st_size != file.size_bytes:
            return False
    return True


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_is_already_verified(dest: Path, file: ModelFile) -> bool:
    if not dest.exists() or dest.stat().st_size != file.size_bytes:
        return False
    return _sha256_of(dest) == file.sha256


class InstallCancelled(Exception):
    pass


class ChecksumMismatch(Exception):
    def __init__(self, entry_id: str, file_path: str) -> None:
        super().__init__(f"checksum mismatch for {entry_id}: {file_path}")
        self.entry_id = entry_id
        self.file_path = file_path


FetchFn = Callable[
    [str, str, str, Path], Path
]  # (repository, revision, filename, dest_dir) -> path


def _default_fetch(repository: str, revision: str, filename: str, dest_dir: Path) -> Path:
    return Path(
        hf_hub_download(
            repo_id=repository, filename=filename, revision=revision, local_dir=dest_dir
        )
    )


def _effective_source(entry: ModelCatalogEntry, file: ModelFile) -> tuple[str, str]:
    """A file's own repository/revision override, or the entry's own when
    unset - see ModelFile's docstring."""
    if file.repository is not None and file.revision is not None:
        return file.repository, file.revision
    return entry.repository, entry.revision


def install(
    entry: ModelCatalogEntry,
    data_dir: Path,
    *,
    fetch: FetchFn | None = None,
    on_file_verified: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    may_finalize: Callable[[], bool] | None = None,
) -> None:
    fetch = fetch if fetch is not None else _default_fetch
    assert entry.repository is not None and entry.revision is not None  # OPEN_LOCAL-only

    install_dir = install_dir_for(entry, data_dir)
    install_dir.mkdir(parents=True, exist_ok=True)

    marker_invalidated = False
    for index, file in enumerate(entry.files):
        if cancel is not None and cancel.is_set():
            raise InstallCancelled

        dest = _resolve_file_path(install_dir, file)
        if not _file_is_already_verified(dest, file):
            if not marker_invalidated:
                _invalidate_marker(install_dir)
                marker_invalidated = True
            if dest.exists():
                dest.unlink()
            file_repository, file_revision = _effective_source(entry, file)
            fetch(file_repository, file_revision, file.path, install_dir)
            if not _file_is_already_verified(dest, file):
                dest.unlink(missing_ok=True)
                raise ChecksumMismatch(entry.id, file.path)

        if cancel is not None and cancel.is_set():
            raise InstallCancelled

        if on_file_verified is not None:
            on_file_verified(index + 1, len(entry.files))

    if may_finalize is not None and not may_finalize():
        raise InstallCancelled

    # Skip the write when nothing was touched and the on-disk marker already
    # matches this entry - a redundant call must not bump the marker's mtime.
    # (A missing/stale marker still gets (re)written even with nothing
    # invalidated during this call - e.g. every file already verified after a
    # crash that landed the last file's bytes but never reached this point.)
    if marker_invalidated or _read_marker(install_dir) != _marker_payload(entry):
        _write_marker_atomically(install_dir, entry)


def remove(entry: ModelCatalogEntry, data_dir: Path) -> None:
    install_dir = install_dir_for(entry, data_dir)
    if install_dir.exists():
        shutil.rmtree(install_dir)
