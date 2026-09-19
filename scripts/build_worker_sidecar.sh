#!/usr/bin/env bash
# Builds the FastAPI worker into the distribution directory the Tauri app bundles.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
    packaging/worker_entrypoint.py
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
