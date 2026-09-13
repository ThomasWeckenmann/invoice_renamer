#!/usr/bin/env bash
# Builds the FastAPI worker into the platform sidecar binary Tauri expects.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_dir="$repo_root/backend"
binaries_dir="$repo_root/src-tauri/binaries"
sidecar_name="invoice-renamer-worker"

target_triple="$(rustc -vV | sed -n 's/^host: //p')"
if [ -z "$target_triple" ]; then
  echo "error: could not determine the Rust host target triple (is rustc on PATH?)" >&2
  exit 1
fi

echo "Building $sidecar_name for $target_triple with PyInstaller (onefile, dev/CI use only)..."

(
  cd "$backend_dir"
  uv run --group build pyinstaller \
    --onefile \
    --name "$sidecar_name" \
    --distpath "$backend_dir/.pyinstaller/dist" \
    --workpath "$backend_dir/.pyinstaller/build" \
    --specpath "$backend_dir/.pyinstaller" \
    packaging/worker_entrypoint.py
)

mkdir -p "$binaries_dir"
dest="$binaries_dir/${sidecar_name}-${target_triple}"
cp "$backend_dir/.pyinstaller/dist/$sidecar_name" "$dest"
chmod +x "$dest"

echo "Wrote $dest"
