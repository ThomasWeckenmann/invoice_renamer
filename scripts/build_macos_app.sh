#!/usr/bin/env bash
# Builds the macOS app and inserts the worker into it with its symlinks intact.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
staged_worker="$repo_root/src-tauri/resources/worker"
target_marker="$repo_root/src-tauri/resources/worker.target"
tauri_conf="$repo_root/src-tauri/tauri.conf.json"
worker_name="invoice-renamer-worker"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "error: this builds the macOS .app; on other platforms use 'cargo tauri dev'" >&2
  exit 1
fi

# Checked before the long build rather than after it, so a missing worker costs
# seconds instead of a full compile.
if [ ! -x "$staged_worker/$worker_name" ]; then
  echo "error: no worker staged at $staged_worker; run scripts/build_worker_sidecar.sh first" >&2
  exit 1
fi

host_triple="$(rustc -vV | sed -n 's/^host: //p')"
staged_triple="$(cat "$target_marker" 2>/dev/null || true)"
if [ "$staged_triple" != "$host_triple" ]; then
  echo "error: staged worker is for '${staged_triple:-unknown}', not '$host_triple'; re-run scripts/build_worker_sidecar.sh" >&2
  exit 1
fi

count_entries() { find "$1" \( -type f -o -type l \) | wc -l | tr -d ' '; }
count_links() { find "$1" -type l | wc -l | tr -d ' '; }

source_entries="$(count_entries "$staged_worker")"
source_links="$(count_links "$staged_worker")"

# Cargo's target directory is not always src-tauri/target: CARGO_TARGET_DIR or
# build.target-dir in a .cargo/config.toml can redirect it (this checkout's own
# notes call out doing exactly that, to keep a Linux build from overwriting a
# macOS target dir shared in over virtiofs). Asking cargo directly, rather than
# assuming the default, is what stops this script from silently patching a
# stale app left at the default path while the actual new build sits elsewhere
# untouched - reproduced while testing this script.
cargo_target_dir="$(cd "$repo_root/src-tauri" && cargo metadata --no-deps --format-version 1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')"
app_path="$cargo_target_dir/release/bundle/macos/Invoice Renamer.app"

echo "Building the app..."
(cd "$repo_root" && cargo tauri build)

if [ ! -d "$app_path" ]; then
  echo "error: expected an app at $app_path but the build produced none" >&2
  exit 1
fi

# Whether the bundle carries a signature seal decides if inserting files
# invalidates it. Tauri only seals the bundle when a signing identity is
# configured, so on an unsigned build there is nothing to preserve - but the
# moment one is configured, the worker has to go in before the seal is made.
bundle_was_sealed=false
if [ -e "$app_path/Contents/_CodeSignature/CodeResources" ]; then
  bundle_was_sealed=true
fi

dest_worker="$app_path/Contents/Resources/worker"
echo "Inserting the worker..."
rm -rf "$dest_worker"
mkdir -p "$(dirname "$dest_worker")"
# ditto rather than cp: it is the platform's own bundle-aware copy, and it
# keeps symlinks as symlinks. Tauri's resource copying does not, which is why
# the worker is inserted here instead of through bundle.resources - the
# PyInstaller layout aliases libraries into _internal, and PyInstaller's own
# docs warn that resolving those aliases into real files both inflates the
# distribution and can cause runtime problems it does not further specify.
ditto "$staged_worker" "$dest_worker"

echo "Verifying the inserted worker..."
failed=false
if [ ! -x "$dest_worker/$worker_name" ]; then
  echo "  error: $worker_name is missing or not executable" >&2
  failed=true
fi

dest_entries="$(count_entries "$dest_worker")"
dest_links="$(count_links "$dest_worker")"
if [ "$dest_entries" != "$source_entries" ]; then
  echo "  error: copied $dest_entries entries, expected $source_entries" >&2
  failed=true
fi
if [ "$dest_links" != "$source_links" ]; then
  echo "  error: copied $dest_links symlinks, expected $source_links (they were resolved into real files)" >&2
  failed=true
fi

while IFS= read -r link; do
  echo "  error: dangling symlink ${link#"$dest_worker/"}" >&2
  failed=true
done < <(find "$dest_worker" -type l ! -exec test -e {} \; -print)

if [ "$failed" = true ]; then
  echo "error: the inserted worker is not a faithful copy; the app is not usable" >&2
  exit 1
fi
echo "  $dest_entries entries, $source_links symlinks preserved, none dangling"

if [ "$bundle_was_sealed" = true ]; then
  identity="${APPLE_SIGNING_IDENTITY:-$(python3 -c "
import json
conf = json.load(open('$tauri_conf'))
print(conf.get('bundle', {}).get('macOS', {}).get('signingIdentity') or '')
")}"
  if [ -z "$identity" ] && codesign -dv "$app_path" 2>&1 | grep -q "Signature=adhoc"; then
    identity="-"
  fi
  if [ -z "$identity" ]; then
    echo "error: the app was signed before the worker went in, so its seal is now stale, and no identity is available to re-sign with. Set APPLE_SIGNING_IDENTITY and re-run." >&2
    exit 1
  fi

  # Read back the same two settings Tauri itself passes to codesign when it
  # first sealed the app, so a plain --force --sign does not quietly drop
  # them: an app that loses its entitlements or hardened runtime can behave
  # differently or fail notarization despite verifying as signed.
  entitlements_path="$(python3 -c "
import json
conf = json.load(open('$tauri_conf'))
ent = conf.get('bundle', {}).get('macOS', {}).get('entitlements')
print(ent if isinstance(ent, str) else '')
")"
  hardened_runtime="$(python3 -c "
import json
conf = json.load(open('$tauri_conf'))
print('1' if conf.get('bundle', {}).get('macOS', {}).get('hardenedRuntime') else '')
")"

  codesign_args=(--force --sign "$identity")
  if [ -n "$entitlements_path" ]; then
    codesign_args+=(--entitlements "$repo_root/src-tauri/$entitlements_path")
  fi
  if [ -n "$hardened_runtime" ]; then
    codesign_args+=(--options runtime)
  fi

  echo "Re-sealing the app, whose signature the insertion invalidated..."
  codesign "${codesign_args[@]}" "$app_path"
  codesign --verify --strict "$app_path"
fi

echo "Built $app_path"
