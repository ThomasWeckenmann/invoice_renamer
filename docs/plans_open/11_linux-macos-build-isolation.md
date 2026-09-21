# Isolate Linux builds from the Mac workspace

Status: Proposed. Planning only; implementation has not started.

## Goal

Run Linux dependency installation, tests, and builds without overwriting
the Mac's environments or generated artifacts in the shared checkout.
Keep one editable source tree and make the safe Linux workflow automatic.

## Current evidence

- `scripts/build_worker_sidecar.sh` writes every platform's worker to
  `src-tauri/resources/worker` and overwrites `resources/worker.target`.
  PyInstaller output is separated by mode, but not platform, under
  `backend/.pyinstaller/{onedir,onefile}`.
- `src-tauri/build.rs` rejects a staged worker for another target even
  during Linux checks. The guard detects the collision but does not avoid it.
- `src-tauri/src/worker.rs` resolves the development worker relative to
  the compiled crate directory. Release builds deliberately have no
  development fallback.
- `app/node_modules`, its TypeScript build metadata, and `app/dist` are
  shared paths. Plans 05, 06, 07, and 09 record Linux frontend verification
  blocked by the Mac dependency tree lacking Linux-native packages.
- Backend commands use the default project environment; Cargo uses its
  default output unless separately overridden. Previous notes record
  virtiofs Python installation trouble and isolated Cargo workarounds.
- `.devcontainer/devcontainer.json` currently establishes no build-workspace
  isolation. Its Dockerfile also does not provision the full native Tauri
  prerequisites listed in `README.md`; path isolation alone cannot supply them.

## Design decision

Use a host-local Linux build mirror outside the shared checkout. Copy
source into it before each command, then run existing tools there.
The Mac continues using its current checkout and commands. The mirror is
disposable build input, never a second place to edit source or commit work.

This isolates the entire toolchain together, including npm's conventional
`node_modules` path and Tauri's relative paths. Do not swap a shared
`node_modules` symlink or copy Linux dependencies back into the checkout.
Do not introduce platform branches throughout application code.

Proposed Linux root:

`/home/vscode/.cache/invoice-renamer/builds/<checkout-key>/<host-triple>/workspace`

Resolve the user's cache directory rather than hardcoding `/home/vscode`.
Derive the checkout key from the canonical source path and retain that
path in an ownership marker. Use the Rust host triple to separate OS and
architecture; reject cross-target builds in this initial workflow.
Allow an explicit local root override with the same safety validation.
In the devcontainer, verify the chosen root is outside the shared mount.

| Generated data | Mac | Linux |
| --- | --- | --- |
| Python environment and caches | Existing checkout paths | Mirror paths |
| Frontend dependencies, caches, dist | Existing checkout paths | Mirror paths |
| Cargo target and generated Tauri files | Existing checkout paths | Mirror paths |
| PyInstaller output and staged worker | Existing checkout paths | Mirror paths |

Application data, downloaded models, and private invoice samples are not
build inputs and must not be mirrored or deleted. Linux release packaging
remains outside scope; currently only Linux development runs are supported.

## Block 1: Safe source synchronization

- Add a focused Linux workspace helper and command wrapper, with short
  purpose comments. Resolve source/destination paths and print the selected
  platform and workspace before executing tools.
- Build a source manifest from tracked and non-ignored untracked files so
  current, uncommitted edits are tested. Exclude `.git`, private samples,
  model data, local environment files, environments, dependency trees,
  generated Tauri files, build output, caches, and sockets explicitly.
  Include lockfiles, fixtures, packaging hooks, and executable scripts.
- Preserve source-relative layout and executable bits. Reject source
  symlinks that escape the checkout or reach excluded data; do not follow
  arbitrary links into another machine's environments.
- Record synchronized source files separately. Remove deleted source
  files from the mirror using that manifest; preserve mirror-only generated
  output. Never use broad deletion against the checkout or an unowned root.
- Serialize synchronization and command execution for each mirror using a
  Linux file lock. Fail clearly if it is already in use. Do not synchronize
  source into a running build or development server.
- Document snapshot semantics: commands test source copied at invocation.
  The initial dev workflow requires restarting the wrapper after source
  edits; automatic live synchronization is deferred.

Checks: fixture checkout containing modified/untracked/deleted files,
spaces in paths, executable scripts, ignored artifacts, and escaping
symlinks. Verify source contents and Mac sentinel artifacts remain intact.

Acceptance: repeated syncs update source correctly and retain only the
Linux mirror's own generated artifacts.

## Block 2: Toolchain commands and worker integration

- Provide one documented entry point, proposed as
  `scripts/linux_workspace.sh <command>`, with explicit commands for
  setup, backend checks, frontend checks, Rust checks, worker build, and dev.
- Run `uv sync --locked` and `npm ci` inside the mirror for setup. Track
  lockfile/tool-version changes and require or perform refreshed setup
  before checks; avoid reinstalling unchanged dependencies on every run.
  Routine checks must not rewrite source lockfiles.
- Pin Python project environment, Python bytecode/cache destinations, and
  Cargo target output to the mirror. Reject inherited environment/config
  overrides that would redirect these to shared paths. Resolve package
  download caches locally as well; do not inherit an active Mac environment.
- Run existing PyInstaller scripts from the mirror so staged worker,
  marker, and temporary output are all Linux-local. Keep target validation
  in `build.rs`; Linux should never see the Mac marker in the first place.
- Run Tauri from the mirror so frontend hooks and the compiled development
  worker path resolve there. Preserve release-only bundled-worker lookup
  and the macOS copy/signing workflow unchanged.
- Fail early with exact missing-prerequisite information. Add the Linux
  native build/OCR prerequisites to the devcontainer setup where needed;
  distinguish toolchain failures from cross-platform artifact collisions.
- Expose the absolute artifact location in output. Do not copy outputs
  back to shared `app/dist`, `src-tauri/target`, or `resources`.

Checks: stub tools record cwd/environment/output destinations; assert every
write location belongs to the Linux workspace. Check lockfile changes,
host-target mismatches, missing worker, and inherited path overrides.

Acceptance: Linux checks and worker builds succeed without replacing any
Mac-generated file or weakening the existing architecture guard.

## Block 3: Workflow adoption and migration

- Update README with the Linux wrapper commands and actual output paths;
  keep Mac setup/build commands clear and unchanged.
- Update AGENTS.md verification instructions to route Linux commands through
  the wrapper. Explain where to edit source and how to rerun after edits.
- Add devcontainer setup integration that prepares or identifies the local
  workspace without installing into shared project paths. Document that a
  container rebuild may discard caches and require setup again.
- Add a cheap guard to existing worker-build entry points when invoked
  from the shared Linux checkout, directing users to the wrapper before
  any output mutation. The same scripts must work inside the owned mirror.
- Explicitly document the support boundary: arbitrary direct `npm`, `uv`,
  or `cargo` commands in a shared checkout can bypass the wrapper. Do not
  claim this is filesystem-level protection; supported Linux commands and
  agent instructions must consistently use the isolated workflow.
- Leave legacy Mac artifacts in place. Do not automatically delete shared
  environments or guess which platform owns existing output. Provide a
  narrowly scoped clean command for the owned Linux mirror only.

Acceptance: a fresh Linux session has one clear setup/check route; the Mac
requires no dependency reinstall merely because Linux verification ran.

## Block 4: Happy-path end-to-end verification

1. Record hashes, symlink targets, and modes of representative Mac artifacts
   in the shared checkout, including its worker and target marker.
2. From Linux, initialize the mirror, install dependencies, and run backend
   pytest/format/lint/mypy and frontend lint/test/build through the wrapper.
3. Run Rust checks/tests, build the Linux worker, and launch development
   mode where a graphical session is available. Verify worker readiness
   and authenticated health, frontend loading, and clean process shutdown.
4. Edit a source fixture in a disposable test checkout, rerun through the
   wrapper, and prove the change reaches the test; repeat with deletion.
5. Recheck Mac artifact hashes/modes/links: Linux commands changed none.
6. On the Mac, run its existing checks and build script without repairing
   dependencies or rebuilding a worker solely because Linux overwrote it.
   Launch the app and verify its bundled worker and signature checks.

Automate cross-directory isolation with fixture artifacts and stub tools;
repeat the real workflow on Linux and macOS. Report unavailable hardware
or GUI verification explicitly rather than marking it passed. Capture real
error output before changing implementation in response to failures.

Acceptance: source edits reach Linux verification, both hosts can build
from the same editable source tree, and their artifacts stay independent.

## Block 5: Security, sanity, and safety

- Reject empty/root/source destinations, destinations inside the source,
  symlinked roots, ownership mismatches, and unsafe cleanup paths before
  any copy or deletion. Verify the destination is on Linux-local storage.
- Ensure simultaneous wrapper invocations cannot race sync, dependency
  installation, cleanup, or worker staging. Release locks on failure and
  signal termination; terminate wrapper-owned child processes as needed.
- Never import private invoices, credentials, model weights, or Mac
  dependency/build trees through manifests, environment overrides, or links.
- Exercise failed sync, interrupted install/build, and incomplete worker
  staging. A later invocation must fail clearly or recover safely, without
  falling back to the shared worker or reporting stale output as current.
- Keep frontend source/lockfiles authoritative in the shared checkout;
  dependency updates remain an explicit source-edit workflow, not an
  automatic mirror-to-source synchronization step.
- Confirm no changes to runtime authentication, application data locations,
  Mac signing, release worker fallback rules, or invoice handling.

Acceptance: the safe workflow cannot mutate the Mac's generated artifacts
or delete unrelated files, and failures remain diagnosable and recoverable.

## Bookkeeping

No changelog entry for this plan. During implementation, update `CHANGES.md`
according to repository conventions. Leave staging and committing to the
user. This work prevents artifact collisions; OS-specific dependency and
native toolchain defects still require their own diagnosis.
