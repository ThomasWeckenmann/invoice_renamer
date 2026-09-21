#!/usr/bin/env bash
# Builds the FastAPI worker into the distribution directory the Tauri app bundles.
set -euo pipefail

# A signal sent to just this script's pid (not its process group - e.g. via
# scripts/linux_workspace.sh's `exec`, which hands this script that pid
# directly) would otherwise stop here without reaching the uv/PyInstaller
# child below, orphaning it. Forward to the whole group instead.
forward_signal_to_group() {
  trap - TERM INT
  kill -- -$$ 2>/dev/null || true
}
trap forward_signal_to_group TERM INT

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(uname -s)" = "Linux" ] && [ -z "${LINUX_WORKSPACE_ACTIVE:-}" ]; then
  # Matches scripts/linux_workspace.sh's own shared-mount check: only a
  # checkout actually reachable from another host (virtiofs, 9p, a network
  # share) risks colliding with a macOS build of the same repo. A checkout
  # on a plain local filesystem has no such risk and builds directly.
  repo_fstype="$(findmnt -no FSTYPE -T "$repo_root" 2>/dev/null || true)"
  case "$repo_fstype" in
    virtiofs | 9p | fuse.* | cifs | smb3 | nfs | nfs4)
      echo "error: this checkout is on a $repo_fstype mount, which may be shared with a macOS checkout of the same repo. Build the worker through the isolated mirror instead:" >&2
      echo "  scripts/linux_workspace.sh . scripts/build_worker_sidecar.sh" >&2
      exit 1
      ;;
  esac
fi
backend_dir="$repo_root/backend"
resources_dir="$repo_root/src-tauri/resources/worker"
target_marker="$repo_root/src-tauri/resources/worker.target"
legacy_binaries_dir="$repo_root/src-tauri/binaries"
sidecar_name="invoice-renamer-worker"

mode="onedir"
case "${1:-}" in
  "" | --onedir) ;;
  --onefile) mode="onefile" ;;
  *)
    echo "usage: ${BASH_SOURCE[0]##*/} [--onedir|--onefile]" >&2
    exit 2
    ;;
esac

target_triple="$(rustc -vV | sed -n 's/^host: //p')"
if [ -z "$target_triple" ]; then
  echo "error: could not determine the Rust host target triple (is rustc on PATH?)" >&2
  exit 1
fi

# Each mode gets its own output tree. Onefile writes dist/<name> as a file and
# onedir writes it as a directory of the same name, so a shared distpath would
# have each build clobber the other's result.
build_root="$backend_dir/.pyinstaller/$mode"

echo "Building $sidecar_name for $target_triple with PyInstaller ($mode)..."

(
  cd "$backend_dir"
  uv run --group build pyinstaller \
    "--$mode" \
    --name "$sidecar_name" \
    --distpath "$build_root/dist" \
    --workpath "$build_root/build" \
    --specpath "$build_root" \
    --add-data "$backend_dir/THIRD-PARTY-LICENSES:." \
    --collect-binaries llama_cpp \
    packaging/worker_entrypoint.py
  # --collect-binaries llama_cpp: llama-cpp-python ships its compiled
  # libllama/libggml* shared libraries as plain package data under
  # llama_cpp/lib/, loaded via ctypes at runtime - PyInstaller's static
  # import analysis has no way to discover a ctypes-loaded library on its
  # own, so without this flag the worker fails at model-load time with
  # "Shared library with base name 'llama' not found". This preserves the
  # llama_cpp/lib/ subdirectory layout load_shared_library() expects
  # relative to the package (confirmed against the installed binding's
  # source and a real onedir build in the isolated Linux mirror).
  # --add-data's source must be absolute: a relative one resolves against
  # --specpath, not this subshell's cwd - confirmed by a throwaway build,
  # since PyInstaller's own docs don't spell this out. Its destination "."
  # lands inside onedir's _internal/ (PyInstaller's modern layout keeps only
  # the executable at the top level), so the license file ships at
  # <worker dir>/_internal/THIRD-PARTY-LICENSES, not beside the executable.
)

if [ "$mode" = "onefile" ]; then
  # Deliberately not staged. Onefile exists only to re-measure startup against
  # the packaged onedir build; staging it somewhere the bundler reads is how a
  # stale single-file worker would end up shipped by accident.
  echo "Wrote $build_root/dist/$sidecar_name (comparison build, not packaged)"
  exit 0
fi

# Replaced wholesale rather than copied over: a file that a previous build
# produced and this one no longer does would otherwise linger in the bundle.
rm -rf "$resources_dir"
mkdir -p "$(dirname "$resources_dir")"
# -R keeps symlinks as symlinks and -p keeps the executable bits, both of which
# PyInstaller's layout depends on.
cp -Rp "$build_root/dist/$sidecar_name" "$resources_dir"
chmod +x "$resources_dir/$sidecar_name"

# Kept beside the staged tree rather than inside it, so it is not itself
# bundled. The Tauri build reads it to refuse a worker built for a different
# architecture - see src-tauri/build.rs.
printf '%s\n' "$target_triple" > "$target_marker"

if [ -d "$legacy_binaries_dir" ]; then
  echo "note: $legacy_binaries_dir is left over from the externalBin sidecar and is no longer used; delete it" >&2
fi

echo "Staged the worker for $target_triple in $resources_dir"
