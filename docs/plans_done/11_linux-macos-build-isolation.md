# Isolate Linux builds from the Mac workspace

Status: Implemented in 0.38.0; reviewed and tested on Linux on 2026-09-21.
Verification scope and remaining limitations are recorded below. Moving this
file to `docs/plans_done/` and committing are left to the user.

## Implementation and verification record

Implemented:

- `scripts/linux_workspace.sh` refreshes a checkout-specific, host-specific
  mirror under `~/.cache/invoice-renamer-linux-workspace/` and holds a lock
  across synchronization and command execution.
- Destination checks reject known shared filesystem types, symlinked paths,
  and mismatched ownership markers. Ordinary local Linux checkouts remain
  supported. The worker build guard applies to shared Linux filesystems.
- `scripts/linux_workspace_excludes.txt` preserves Linux build output while
  excluding Mac dependencies/artifacts, nested `.env`/`.env.local` files,
  and the configured private-data directories from synchronization.
- Python environment and Cargo target paths are explicitly set inside the
  mirror. Conflicting overrides, including both `npm_config_cache` and
  `NPM_CONFIG_CACHE`, are rejected; `VIRTUAL_ENV` is cleared.
- `scripts/linux_workspace_run.py` supervises the command in a separate
  process group. It forwards interruption signals, stops descendants before
  releasing the lock, and escalates to SIGKILL after a two-second grace period.
- README, AGENTS.md, devcontainer prerequisites, and the existing 0.38.0
  changelog entry were updated. Application runtime behavior was not changed.

Verified through the real Linux mirror:

| Command | Result |
| --- | --- |
| `scripts/linux_workspace.sh backend uv run pytest` | 496 passed, 2 macOS-only tests skipped |
| `scripts/linux_workspace.sh app npm run test` | 123 passed |
| `scripts/linux_workspace.sh src-tauri cargo test` | 40 passed, 1 ignored |
| `scripts/linux_workspace.sh . python3 -m unittest discover -s scripts/tests -v` | 4 passed after the final fixes |

The backend/frontend/Rust suites ran before the final supervisor and uppercase
cache-override fixes. Focused supervisor tests and disposable-fixture checks
were rerun after those fixes. Supervisor tests cover exit status, stdin,
SIGINT/SIGTERM/SIGHUP descendant cleanup, and escalation for ignored SIGTERM.

Disposable-fixture checks with real rsync passed for paths with spaces,
source edits/additions/deletions, executable modes, nested environment-file
exclusions, unchanged Mac artifact sentinels, retained Linux output, source
symlink/FIFO rejection, ownership-marker mismatch, lock-symlink rejection,
override rejection, concurrent locking, interruption cleanup, and subsequent
lock acquisition. Shared-destination rejection was checked with a filesystem
probe stub, not an actual second shared mount. Shell syntax and diff whitespace
checks also passed.

Verification limits and implementation differences:

- The full original build matrix below was not executed in this review:
  backend formatting/lint/mypy, frontend lint/build, Linux worker packaging,
  and a rebuilt devcontainer remain unverified here. No Linux GUI session or
  macOS packaged-app launch was performed by the reviewer. The user reported
  macOS dev working; the reported bare `cargo tauri build` omitted the worker.
  The supported macOS packaging command is `scripts/build_macos_app.sh`;
  successful launch after that command has not been reported in this thread.
- Package caches retain their normal home-directory defaults rather than
  being relocated under the mirror. This assumes those cache locations are
  container-local; separately mounted or symlinked default caches are not
  validated by the destination check.
- The supervisor covers command execution. Interruption during rsync,
  failed-sync recovery, and interrupted worker staging were not exercised in
  this review. Forced SIGKILL of the supervisor and descendants that deliberately
  create a separate session are outside its graceful-cleanup guarantee.
- Cross-target rejection remains with the existing staged-worker target guard;
  the wrapper itself does not reject every cross-target configuration. That
  guard was not separately exercised in this review.

The sections below retain the original implementation and acceptance checklist;
they are not a claim that every planned verification step was executed.

## Goal and existing support

Keep one editable source checkout while Linux checks and builds leave the
Mac's dependencies and generated artifacts intact.

Platform detection already exists:

- `scripts/build_worker_sidecar.sh` reads Rust's host triple.
- `src-tauri/build.rs` checks the staged worker against Cargo's target.
- `backend/pyproject.toml` selects platform-specific Python dependencies.

Reuse these mechanisms. The missing piece is separate writable directories:
`app/node_modules`, `app/dist`, `backend/.venv`, PyInstaller output, Cargo
output, and `src-tauri/resources/worker` currently share checkout paths.
The worker target guard detects a collision after it occurs; it cannot
prevent one.

## Simplified design

Keep a disposable Linux-local mirror and use standard `rsync` to refresh
its source before running existing commands there. This preserves npm and
Tauri's relative paths without changing application code or maintaining a
separate output-path override for every tool.

The shell wrapper supports:

```bash
scripts/linux_workspace.sh backend uv sync --locked
scripts/linux_workspace.sh app npm ci
scripts/linux_workspace.sh backend uv run pytest
scripts/linux_workspace.sh app npm run build
scripts/linux_workspace.sh src-tauri cargo check
scripts/linux_workspace.sh . scripts/build_worker_sidecar.sh
```

The first argument selects an allowed working directory (`.`, `backend`,
`app`, or `src-tauri`); the remaining arguments are executed directly there.
Dependency installation stays explicit. Existing tools own dependency
resolution, lockfile enforcement, and incremental builds.

Use a fixed directory beneath the container user's home, outside the shared
mount, with a checkout-path hash to distinguish repositories and the existing
Rust host triple to distinguish architectures. Keep a source-path ownership
marker beside the mirror. No configurable destination or cleanup command in
this first implementation; the mirror can be discarded with the container.

Remove the earlier custom source-manifest/deletion engine, dependency-version
tracking, named setup/check dispatcher, and automatic setup refresh. Keep only
the path checks and locking needed to make synchronization safe.

The mirror is a snapshot, not an editing workspace. Edit the shared checkout
and rerun the wrapper after changes. A development server holds the mirror
lock until it exits; live synchronization is outside scope. macOS commands
stay as they are. Linux release packaging is also outside scope.

## Block 1: Minimal wrapper and workflow integration

- Add the wrapper with a short purpose comment. Require Linux, `rsync`,
  `flock`, `findmnt`, Python 3, and the existing Rust host-triple lookup;
  print the mirror path.
- Validate the fixed destination and its ownership before writing. It must
  be outside the checkout and shared mount, with no symlinked destination
  components. Fail on an ownership mismatch or unsupported cross-target setup.
- Hold one `flock` across synchronization and command execution; fail clearly
  when busy. Propagate exit status and signals without leaving a running child
  after releasing the lock.
- Use `rsync` recursion, timestamps, permissions, and `--delete` with a small,
  explicit exclusion file. Preserve modified and new source files and remove
  deleted source. Exclude `.git`, local environment files, private benchmark
  data, model weights, dependencies, virtual environments, bytecode, caches,
  TypeScript build metadata, generated Tauri files, and build/worker output.
  Preserve lockfiles, synthetic fixtures, packaging hooks, and executable bits.
- Apply exclusions on both transfer and deletion so Linux-generated artifacts
  survive later syncs. Never use `--delete-excluded`. These are standard
  [rsync exclusion semantics](https://download.samba.org/pub/rsync/rsync.1).
  Do not parse `.gitignore` as though it were an rsync filter file.
- Reject source symlinks in the selected input before synchronization for this
  initial workflow; do not dereference links into private or host-local data.
  Fail on unsupported special files instead of silently copying partial input.
- Set Python environment/cache and Cargo output paths inside the Linux-local
  root. Clear inherited active-environment settings and reject conflicting
  tool output overrides. Keep package caches on container-local storage.
- Execute existing worker and Tauri commands in the mirror. Preserve the
  worker target guard, development lookup, release lookup, and Mac signing.
- Add `rsync`, `flock`, and missing native build/OCR prerequisites to the
  devcontainer where needed, checking actual tool errors and README requirements.
  Do not install project dependencies into the shared checkout at container startup.
- Document wrapper setup, verification, and dev commands in README and AGENTS.md,
  including snapshot behavior and artifact paths. Add an early guard to worker
  build entry points against staging directly in the shared Linux checkout.

Acceptance: ordinary tools run in an isolated tree through one wrapper. There
is no custom dependency manager or application-level platform routing.
Arbitrary commands run directly in the shared checkout remain outside this
protection; documentation must state that boundary.

## Block 2: Happy-path end-to-end verification

- Use a disposable source checkout with Mac artifact sentinels. Run the wrapper
  with real `rsync` and stub build tools; verify working directories, environment,
  executable modes, source edits/additions/deletions, and retained Linux output.
- Install dependencies in the mirror, then run backend pytest/format/lint/mypy,
  frontend lint/test/build, Rust checks/tests, and the Linux worker build using
  the existing commands. Verify the worker marker matches the Linux host.
- Repeat after a source edit and deletion; confirm the next invocation sees
  both changes and all Mac sentinel hashes, modes, and link targets are intact.
- Where a GUI is available, launch Linux development mode and verify frontend
  loading, worker readiness/authenticated health, and clean shutdown. On Mac,
  run the existing build and launch without repairing Linux-overwritten files.
- Report unavailable hardware/GUI checks explicitly. Capture raw failures
  before diagnosing native-toolchain issues separately from path collisions.

Acceptance: the complete source-to-check/build flow uses the mirror, preserves
Mac artifacts, and requires no new platform detection in application code.

## Block 3: Security, sanity, and safety

- Test destination/ownership failures, symlinked paths, paths with spaces,
  private-data exclusions, inherited output overrides, and cross-target rejection.
- Test concurrent invocations and interruption during synchronization/build;
  failed sync must prevent command execution, and a subsequent invocation must
  recover without using the shared worker or accepting incomplete staging.
- Verify `rsync` removes deleted source while preserving every excluded output
  directory. Keep its deletion scope limited to the validated, owned mirror.
- Keep private invoices, credentials, models, and application data outside the
  mirror. Dependency updates remain explicit edits to authoritative source
  lockfiles; never copy mirror output or lockfiles back automatically.
- Confirm no changes to runtime authentication, application-data locations,
  invoice handling, Mac signing, or release worker fallback rules.

Acceptance: supported commands cannot overwrite Mac artifacts or delete
unrelated files, and failures identify the actual problem.

## Bookkeeping

No changelog entry for planning. During implementation, update `CHANGES.md`
according to repository conventions. Leave staging and committing to the user.
