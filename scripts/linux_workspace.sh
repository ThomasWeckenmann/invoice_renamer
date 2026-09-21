#!/usr/bin/env bash
# Mirrors this checkout into a Linux-local tree and runs a command there, so
# Linux dependency installs/builds never touch a Mac's venv/node_modules/
# target that share this checkout over a virtiofs-style mount.
set -euo pipefail

usage() {
  echo "usage: ${BASH_SOURCE[0]##*/} {.|backend|app|src-tauri} <command> [args...]" >&2
  exit 2
}

if [ "$(uname -s)" != "Linux" ]; then
  echo "error: ${BASH_SOURCE[0]##*/} only runs on Linux; run macOS commands directly in the checkout." >&2
  exit 1
fi

for tool in rsync flock rustc findmnt python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "error: $tool is required on PATH." >&2
    exit 1
  }
done

[ $# -ge 1 ] || usage
work_dir_arg="$1"
shift
case "$work_dir_arg" in
  . | backend | app | src-tauri) ;;
  *) usage ;;
esac
[ $# -ge 1 ] || usage

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exclude_file="$repo_root/scripts/linux_workspace_excludes.txt"

target_triple="$(rustc -vV | sed -n 's/^host: //p')"
[ -n "$target_triple" ] || {
  echo "error: could not determine the Rust host target triple (is rustc on PATH?)" >&2
  exit 1
}

repo_hash="$(printf '%s' "$repo_root" | sha256sum | cut -c1-16)"
mirror_root="$HOME/.cache/invoice-renamer-linux-workspace/${repo_hash}-${target_triple}"
lock_file="$mirror_root.lock"
marker_file="$mirror_root.source"

echo "linux_workspace: mirror at $mirror_root"

# --- Destination safety, checked before anything is written ---------------

case "$mirror_root" in
  "$repo_root" | "$repo_root"/*)
    echo "error: mirror root $mirror_root is inside the checkout $repo_root." >&2
    exit 1
    ;;
esac

is_shared_fstype() {
  case "$1" in
    virtiofs | 9p | fuse.* | cifs | smb3 | nfs | nfs4) return 0 ;;
    *) return 1 ;;
  esac
}

nearest_existing_ancestor() {
  local p="$1"
  while [ ! -d "$p" ]; do
    p="$(dirname "$p")"
  done
  printf '%s' "$p"
}

dest_dir="$(nearest_existing_ancestor "$mirror_root")"
dest_fstype="$(findmnt -no FSTYPE -T "$dest_dir")"
if is_shared_fstype "$dest_fstype"; then
  echo "error: the mirror destination ($dest_dir) is itself on a $dest_fstype mount; it must live on local, non-shared storage." >&2
  exit 1
fi

repo_fstype="$(findmnt -no FSTYPE -T "$repo_root")"
if is_shared_fstype "$repo_fstype"; then
  repo_mount="$(df -P "$repo_root" | tail -n1 | awk '{print $NF}')"
  dest_mount="$(df -P "$dest_dir" | tail -n1 | awk '{print $NF}')"
  if [ "$repo_mount" = "$dest_mount" ]; then
    echo "error: the checkout is on a $repo_fstype mount ($repo_mount), and the mirror destination ($dest_dir) resolves to the same mount." >&2
    echo "The mirror must live on a separate mount from a shared checkout like this one." >&2
    exit 1
  fi
fi

check_no_symlink_components() {
  local p="$1"
  while [ "$p" != "/" ] && [ -n "$p" ]; do
    if [ -L "$p" ]; then
      echo "error: $p is a symlink; refusing a symlinked mirror destination path." >&2
      exit 1
    fi
    p="$(dirname "$p")"
  done
}
check_no_symlink_components "$mirror_root"
for sibling_path in "$lock_file" "$marker_file"; do
  if [ -L "$sibling_path" ]; then
    echo "error: $sibling_path is a symlink; refusing to open it." >&2
    exit 1
  fi
done

if [ -e "$mirror_root" ]; then
  owner_uid="$(stat -c %u "$mirror_root")"
  if [ "$owner_uid" != "$(id -u)" ]; then
    echo "error: $mirror_root is owned by uid $owner_uid, not the current user; refusing to reuse it." >&2
    exit 1
  fi
  if [ ! -f "$marker_file" ] || [ "$(cat "$marker_file")" != "$repo_root" ]; then
    echo "error: $mirror_root was not recorded as belonging to $repo_root; refusing to overwrite it." >&2
    echo "Remove it by hand first if it is a stale mirror from elsewhere." >&2
    exit 1
  fi
fi
mkdir -p "$mirror_root"
printf '%s' "$repo_root" >"$marker_file"

# --- Lock, held across sync and the command --------------------------------

exec 200>"$lock_file"
if ! flock -n 200; then
  echo "error: another linux_workspace.sh run is using this mirror ($lock_file); wait for it to finish." >&2
  exit 1
fi

# --- Reject unsupported input before syncing anything ----------------------

listing="$(rsync -rn --list-only --exclude-from="$exclude_file" "$repo_root/")"
symlink_entries="$(printf '%s\n' "$listing" | awk 'substr($1, 1, 1) == "l"')"
if [ -n "$symlink_entries" ]; then
  echo "error: refusing to sync source symlinks (not supported by this wrapper):" >&2
  echo "$symlink_entries" >&2
  exit 1
fi
special_entries="$(printf '%s\n' "$listing" | awk 'substr($1, 1, 1) != "d" && substr($1, 1, 1) != "-" && substr($1, 1, 1) != "l"')"
if [ -n "$special_entries" ]; then
  echo "error: refusing to sync unsupported file types under $repo_root:" >&2
  echo "$special_entries" >&2
  exit 1
fi

# --- Sync, then run the command in the mirror -------------------------------

rsync -rtp --delete --exclude-from="$exclude_file" "$repo_root/" "$mirror_root/"

for var in UV_PROJECT_ENVIRONMENT UV_CACHE_DIR CARGO_TARGET_DIR CARGO_HOME XDG_CACHE_HOME npm_config_cache NPM_CONFIG_CACHE; do
  if [ -n "${!var:-}" ]; then
    echo "error: \$$var is already set; unset it before using ${BASH_SOURCE[0]##*/}, which manages tool output paths itself inside the mirror." >&2
    exit 1
  fi
done
unset VIRTUAL_ENV || true

# Explicit rather than relying on uv/cargo's own cwd-relative defaults: this
# holds regardless of exactly how the given command locates its project (a
# subshell `cd`, a --project/--manifest-path flag, and so on). Package
# caches (UV_CACHE_DIR, CARGO_HOME) are left at their normal defaults, which
# already live under $HOME and are shared across mirrors on purpose.
export UV_PROJECT_ENVIRONMENT="$mirror_root/backend/.venv"
export CARGO_TARGET_DIR="$mirror_root/src-tauri/target"

if [ "$work_dir_arg" = "." ]; then
  work_dir="$mirror_root"
else
  work_dir="$mirror_root/$work_dir_arg"
fi

export LINUX_WORKSPACE_ACTIVE=1
cd "$work_dir"
# The supervisor retains our lock while stopping the command's process group.
exec python3 "$mirror_root/scripts/linux_workspace_run.py" "$@"
