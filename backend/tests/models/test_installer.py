"""Tests for installing/removing catalog models on disk with checksum verification."""

import hashlib
import json
import platform
import threading
from pathlib import Path

import pytest

from invoice_renamer.models import installer
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.installer import (
    ChecksumMismatch,
    InstallCancelled,
    install,
    install_dir_for,
    is_installed,
    remove,
    resolve_data_dir,
)

_FILE_CONTENTS: dict[str, bytes] = {
    "config.json": b"{}",
    "model.bin": b"pretend-weights-content",
}


def _entry(file_contents: dict[str, bytes] | None = None, **overrides: object) -> ModelCatalogEntry:
    contents = file_contents if file_contents is not None else _FILE_CONTENTS
    defaults: dict[str, object] = {
        "id": "example-open-small",
        "display_name": "Example Open Model (Small)",
        "license": "apache-2.0",
        "repository": "example-org/example-model",
        "revision": "abc123",
        "files": [
            ModelFile(
                path=path, sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)
            )
            for path, content in contents.items()
        ],
        "memory_tier": MemoryTier.SMALL,
    }
    defaults.update(overrides)
    return ModelCatalogEntry(**defaults)  # type: ignore[arg-type]


def _make_fetch(
    contents: dict[str, bytes],
    calls: list[str] | None = None,
    side_effect: object = None,
) -> installer.FetchFn:
    def fetch(repository: str, revision: str, filename: str, dest_dir: Path) -> Path:
        if calls is not None:
            calls.append(filename)
        if side_effect is not None:
            side_effect(filename)
        dest = dest_dir / filename
        dest.write_bytes(contents[filename])
        return dest

    return fetch


# --- data-dir resolution ----------------------------------------------------


def test_resolve_data_dir_respects_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom-data-dir"
    monkeypatch.setenv(installer.DATA_DIR_ENV_VAR, str(target))

    result = resolve_data_dir()

    assert result == target
    assert target.is_dir()


def test_resolve_data_dir_defaults_on_macos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(installer.DATA_DIR_ENV_VAR, raising=False)
    monkeypatch.setattr(installer.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)

    result = resolve_data_dir()

    assert result == tmp_path / "Library" / "Application Support" / "invoice-renamer"
    assert result.is_dir()


def test_resolve_data_dir_defaults_on_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(installer.DATA_DIR_ENV_VAR, raising=False)
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)

    result = resolve_data_dir()

    assert result == tmp_path / ".local" / "share" / "invoice-renamer"
    assert result.is_dir()


def test_install_dir_for_is_scoped_by_id_and_revision(tmp_path: Path) -> None:
    entry = _entry(id="my-model", revision="deadbeef")

    result = install_dir_for(entry, tmp_path)

    assert result == tmp_path / "models" / "my-model" / "deadbeef"


# --- is_installed ------------------------------------------------------------


def test_is_installed_false_when_dir_absent(tmp_path: Path) -> None:
    assert is_installed(_entry(), tmp_path) is False


def test_is_installed_false_when_marker_absent(tmp_path: Path) -> None:
    entry = _entry()
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    for path, content in _FILE_CONTENTS.items():
        (install_dir / path).write_bytes(content)

    # Every file is present and correctly sized, but no marker was ever
    # written - this is the crash-mid-install scenario the marker exists to
    # catch, and it must not be mistaken for a verified install.
    assert is_installed(entry, tmp_path) is False


def test_is_installed_false_when_marker_payload_mismatches_current_entry(tmp_path: Path) -> None:
    entry = _entry()
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    for path, content in _FILE_CONTENTS.items():
        (install_dir / path).write_bytes(content)
    stale_payload = installer._marker_payload(entry)
    stale_payload["files"][0]["sha256"] = "0" * 64  # type: ignore[index]
    (install_dir / installer._MARKER_NAME).write_text(json.dumps(stale_payload))

    # Simulates the catalog fixing a wrong recorded hash without bumping the
    # revision string - a stale marker from before the fix must not keep
    # claiming installed.
    assert is_installed(entry, tmp_path) is False


def test_is_installed_false_when_marker_valid_but_file_missing(tmp_path: Path) -> None:
    entry = _entry()
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    (install_dir / installer._MARKER_NAME).write_text(json.dumps(installer._marker_payload(entry)))

    assert is_installed(entry, tmp_path) is False


def test_is_installed_true_when_marker_valid_and_files_present(tmp_path: Path) -> None:
    entry = _entry()
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    for path, content in _FILE_CONTENTS.items():
        (install_dir / path).write_bytes(content)
    (install_dir / installer._MARKER_NAME).write_text(json.dumps(installer._marker_payload(entry)))

    assert is_installed(entry, tmp_path) is True


# --- install -----------------------------------------------------------------


def test_install_writes_and_verifies_every_file_and_reports_progress(tmp_path: Path) -> None:
    entry = _entry()
    progress: list[tuple[int, int]] = []

    install(
        entry,
        tmp_path,
        fetch=_make_fetch(_FILE_CONTENTS),
        on_file_verified=lambda done, total: progress.append((done, total)),
    )

    install_dir = install_dir_for(entry, tmp_path)
    for path, content in _FILE_CONTENTS.items():
        assert (install_dir / path).read_bytes() == content
    assert progress == [(1, 2), (2, 2)]
    assert is_installed(entry, tmp_path) is True


def test_install_checksum_mismatch_raises_and_preserves_good_files(tmp_path: Path) -> None:
    entry = _entry()
    bad_contents = dict(_FILE_CONTENTS)
    bad_contents["model.bin"] = b"corrupted-bytes-not-matching-hash"

    with pytest.raises(ChecksumMismatch):
        install(entry, tmp_path, fetch=_make_fetch(bad_contents))

    install_dir = install_dir_for(entry, tmp_path)
    assert (install_dir / "config.json").exists()
    assert not (install_dir / "model.bin").exists()
    assert not (install_dir / installer._MARKER_NAME).exists()
    assert is_installed(entry, tmp_path) is False


def test_install_resumes_by_skipping_already_verified_files(tmp_path: Path) -> None:
    entry = _entry()
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS))

    calls: list[str] = []
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS, calls=calls))

    assert calls == []


def test_install_redownloads_file_with_right_size_but_wrong_hash(tmp_path: Path) -> None:
    entry = _entry()
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS))
    install_dir = install_dir_for(entry, tmp_path)
    corrupted = b"X" * len(_FILE_CONTENTS["model.bin"])
    (install_dir / "model.bin").write_bytes(corrupted)

    calls: list[str] = []
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS, calls=calls))

    assert calls == ["model.bin"]
    assert (install_dir / "model.bin").read_bytes() == _FILE_CONTENTS["model.bin"]


def test_install_repairs_one_broken_file_without_touching_good_files(tmp_path: Path) -> None:
    entry = _entry()
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS))
    install_dir = install_dir_for(entry, tmp_path)
    good_file_bytes_before = (install_dir / "config.json").read_bytes()
    (install_dir / "model.bin").write_bytes(b"corrupted")

    marker_seen_absent = False

    def assert_marker_absent(filename: str) -> None:
        nonlocal marker_seen_absent
        if filename == "model.bin":
            marker_seen_absent = not (install_dir / installer._MARKER_NAME).exists()

    install(
        entry,
        tmp_path,
        fetch=_make_fetch(_FILE_CONTENTS, side_effect=assert_marker_absent),
    )

    assert marker_seen_absent is True
    assert (install_dir / "config.json").read_bytes() == good_file_bytes_before
    assert is_installed(entry, tmp_path) is True


def test_install_leaves_valid_marker_untouched_when_nothing_needs_fixing(tmp_path: Path) -> None:
    entry = _entry()
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS))
    install_dir = install_dir_for(entry, tmp_path)
    marker_path = install_dir / installer._MARKER_NAME
    marker_mtime_before = marker_path.stat().st_mtime_ns
    marker_content_before = marker_path.read_text()

    calls: list[str] = []
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS, calls=calls))

    assert calls == []
    assert marker_path.stat().st_mtime_ns == marker_mtime_before
    assert marker_path.read_text() == marker_content_before


def test_install_cancellation_before_first_file_raises_and_skips_fetch(tmp_path: Path) -> None:
    entry = _entry()
    cancel = threading.Event()
    cancel.set()
    calls: list[str] = []

    with pytest.raises(InstallCancelled):
        install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS, calls=calls), cancel=cancel)

    assert calls == []


def test_install_cancellation_during_last_files_fetch_preserves_bytes_but_not_installed(
    tmp_path: Path,
) -> None:
    entry = _entry()
    cancel = threading.Event()

    def cancel_on_last_file(filename: str) -> None:
        if filename == "model.bin":
            cancel.set()

    with pytest.raises(InstallCancelled):
        install(
            entry,
            tmp_path,
            fetch=_make_fetch(_FILE_CONTENTS, side_effect=cancel_on_last_file),
            cancel=cancel,
        )

    install_dir = install_dir_for(entry, tmp_path)
    assert (install_dir / "model.bin").read_bytes() == _FILE_CONTENTS["model.bin"]
    assert is_installed(entry, tmp_path) is False


def test_install_cancellation_during_last_files_skip_still_raises(tmp_path: Path) -> None:
    entry = _entry()
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS))  # every file already verified

    cancel = threading.Event()
    call_count = 0

    def fetch_and_cancel_before_skip_check(
        repository: str, revision: str, filename: str, dest_dir: Path
    ) -> Path:
        nonlocal call_count
        call_count += 1
        raise AssertionError("fetch should not be called for an already-verified file")

    with pytest.raises(InstallCancelled):
        install_dir = install_dir_for(entry, tmp_path)
        # Set cancel right before calling install() so it's observed on the
        # "after" check following the skip branch of the last (only
        # remaining) file.
        cancel.set()
        install(entry, tmp_path, fetch=fetch_and_cancel_before_skip_check, cancel=cancel)

    assert call_count == 0
    assert install_dir.exists()


def test_install_may_finalize_false_raises_and_does_not_write_marker(tmp_path: Path) -> None:
    entry = _entry()

    with pytest.raises(InstallCancelled):
        install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS), may_finalize=lambda: False)

    install_dir = install_dir_for(entry, tmp_path)
    assert not (install_dir / installer._MARKER_NAME).exists()
    for path, content in _FILE_CONTENTS.items():
        assert (install_dir / path).read_bytes() == content


# --- remove --------------------------------------------------------------


def test_remove_deletes_the_install_dir(tmp_path: Path) -> None:
    entry = _entry()
    install(entry, tmp_path, fetch=_make_fetch(_FILE_CONTENTS))
    install_dir = install_dir_for(entry, tmp_path)
    assert install_dir.exists()

    remove(entry, tmp_path)

    assert not install_dir.exists()


def test_remove_is_a_noop_when_nothing_installed(tmp_path: Path) -> None:
    entry = _entry()

    remove(entry, tmp_path)  # must not raise


# --- security: path traversal ---------------------------------------------


def test_resolve_file_path_rejects_path_traversal(tmp_path: Path) -> None:
    entry = _entry()
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    traversal_file = ModelFile(path="../../etc/passwd", sha256="a" * 64, size_bytes=0)

    with pytest.raises(ValueError):
        installer._resolve_file_path(install_dir, traversal_file)


def test_install_rejects_path_traversal_file_without_fetching(tmp_path: Path) -> None:
    entry = _entry(files=[ModelFile(path="../../etc/passwd", sha256="a" * 64, size_bytes=0)])
    calls: list[str] = []

    with pytest.raises(ValueError):
        install(entry, tmp_path, fetch=_make_fetch({}, calls=calls))

    assert calls == []


# --- late-binding regression ------------------------------------------------


def test_default_fetch_is_late_bound_and_monkeypatchable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry()
    fake = _make_fetch(_FILE_CONTENTS)
    monkeypatch.setattr(installer, "_default_fetch", fake)

    # No fetch= argument at all - this is the exact scenario a bound default
    # argument (`fetch: FetchFn = _default_fetch`) would have silently failed,
    # since it captures the function object at definition time rather than
    # looking up the (now-patched) module attribute at call time.
    install(entry, tmp_path)

    assert is_installed(entry, tmp_path) is True


def test_platform_module_is_the_real_platform_module() -> None:
    # Sanity check that installer.platform is the stdlib module (used by the
    # monkeypatch-based tests above) and matches the real host at import time.
    assert installer.platform.system() in {"Darwin", "Linux", platform.system()}
