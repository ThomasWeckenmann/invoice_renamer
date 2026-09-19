# Worker startup packaging plan

Status: Block 1 complete, its gate cleared. Block 2 implemented, awaiting
verification on macOS. Blocks 3-5 not started.

## Goal

Reduce app startup time by shipping the Python worker as a PyInstaller
`--onedir` bundle inside the Tauri application. Users still install and open
one macOS `.app`; its internal worker dependencies are already on disk.

## Evidence and limits

- `scripts/build_worker_sidecar.sh` currently builds with `--onefile`, labels
  that mode as dev/CI use only, and copies a single executable into
  `src-tauri/binaries/` with the Rust target triple in its filename.
- `src-tauri/tauri.conf.json` packages that executable through `externalBin`.
- `src-tauri/src/worker.rs` resolves it through the shell sidecar API, passes
  a session token and port, and waits for a readiness marker with a 15-second
  timeout. It also supervises the worker's whole process group.
- The reported macOS binary is 214 MB and startup sometimes takes about ten
  seconds. These are reported observations, not measurements reproduced here.
- PyInstaller onefile mode extracts bundled native dependencies and support
  files into a temporary directory before the worker starts. Deferring Python
  imports does not eliminate that extraction cost. Confirm the reported lazy
  ML imports when measuring; do not assume Python initialization costs nothing.
- Extraction is a credible contributor, but its share of the delay and any
  macOS validation overhead need measurements. Do not promise subsecond startup.
- Current PyInstaller applies UPX only on Windows. A generated `upx=True`
  setting does not establish that the macOS binary uses UPX; disabling it is
  not the proposed macOS startup fix.

## Block 1: Measure the existing worker and an equivalent onedir build

- On the affected Mac, record the build revision, architecture, dependency
  versions, binary size, and actual startup logs.
- Measure process spawn to the worker readiness marker for the current
  onefile build and an equivalent onedir build using the same environment.
- Add minimal timing instrumentation if needed to distinguish time before
  Python entry, Python initialization, worker readiness, and app usability.
  Keep tokens and invoice data out of timing output.
- Record first launch separately from several subsequent launches; report
  individual results and median timings. Distinguish direct worker timings
  from packaged app timings and signed/downloaded app behavior.
- Proceed with the packaging migration once the comparison supports it.
  If extraction is not a material contributor, inspect the measured slow
  phase before choosing further changes.

Acceptance: raw timings identify the startup contribution removed by onedir,
with a baseline suitable for comparison after integration.

### Block 1 results, measured 2026-09-18

Revision `bf012cf` (dirty), macOS 26.5.2 on arm64, measured directly against
each built worker with `scripts/measure_worker_startup.py -n 5` — process spawn
to the readiness marker, not the packaged app. Every run, in seconds:

| build | run | total | to Python | Python to ready |
| --- | --- | --- | --- | --- |
| onefile | 1 | 7.383 | 3.401 | 3.982 |
| onefile | 2 | 6.185 | 3.100 | 3.085 |
| onefile | 3 | 6.350 | 3.068 | 3.281 |
| onefile | 4 | 6.303 | 3.038 | 3.265 |
| onefile | 5 | 6.155 | 2.983 | 3.172 |
| onefile | median of 2-5 | 6.244 | 3.053 | 3.219 |
| onedir | 1 | 1.404 | 0.851 | 0.553 |
| onedir | 2 | 0.440 | 0.120 | 0.321 |
| onedir | 3 | 0.432 | 0.118 | 0.314 |
| onedir | 4 | 0.452 | 0.131 | 0.321 |
| onedir | 5 | 0.486 | 0.120 | 0.366 |
| onedir | median of 2-5 | 0.446 | 0.120 | 0.321 |

Each median is taken over its own column's per-run values, so the two phases
are not expected to add up to the total.

On-disk size: onefile 213.8 MB, onedir 651.7 MB in a 61.0 MB executable plus
its support tree.

What the timings establish: onedir removes roughly 5.8s per launch, in two
contributions of nearly equal size, only one of which was predicted.

- Bundle extraction, 2.93s, before Python runs at all.
- The worker's own imports, 2.90s.

What they do not establish is why that second contribution exists. One
hypothesis is that onefile extracts to a new temporary directory per launch,
so libraries are loaded and validated from a path macOS has never seen, while
onedir's stable path lets that work be cached. It fits onedir's first launch
costing 3x its own median while onefile, cold on every run, showing little
first-launch penalty - but fitting is not confirming. The test that would
settle it: run the onedir worker from a freshly copied directory on each
launch, and see whether its timings return to onefile levels.

Not captured, and worth collecting if that cause is pursued: the built
worker's dependency versions, and the raw startup logs behind these timings.

The migration is worth doing whatever the cause, and the 15-second startup
timeout in `worker.rs` needs no change: its headroom goes from 2.4x to roughly
34x. The cost is 3x the on-disk size, which Block 2 must weigh for the
per-build resource copy in development, not only for the shipped bundle.

## Block 2: Build and package the complete worker directory

- Update `scripts/build_worker_sidecar.sh` to produce and stage a complete
  onedir distribution, including the executable and all support files.
- Define an explicit resource destination in `src-tauri/tauri.conf.json`
  using `bundle.resources`, replacing the existing single-file `externalBin`
  configuration for this worker.
- Preserve the PyInstaller directory layout, executable permissions, and
  symlinks. Verify what Tauri actually copies, especially on macOS.
- Keep staged outputs architecture-specific or validate the active target
  before packaging so stale artifacts cannot enter a different build.
- Establish resource paths for both development and installed builds. Update
  existing build/CI callers and setup documentation where required.
- Keep the full worker dependency set initially; dependency trimming or
  splitting the ML runtime into another service is outside this change.

Acceptance: the built app contains the complete worker distribution and can
run it without a system Python installation or the source checkout.

### Why the worker is not a bundled resource

`bundle.resources` turned out to be unusable for this tree. Tauri's
`copy_resources` calls `copy_file` per entry, which ends in `fs::copy` and so
follows symlinks; its symlink-preserving `copy_dir` is used for macOS
frameworks but never for resources. The macOS worker has 24 symlinks in
`_internal` aliasing libraries that live under `torch/lib` and `PIL/.dylibs`,
and resolving them produces a second real copy of each, adding roughly 410 MiB.
PyInstaller documents that dereferencing its symlinks inflates the
distribution and can cause runtime problems; the duplicate-library failure has
not been reproduced in this app, and Block 4's model-loading step is where it
would show up.

Tauri offers no post-bundle hook, so the worker is inserted by
`scripts/build_macos_app.sh`, which wraps the whole build. Two ordering
constraints it has to respect: the app is signed during bundling whenever an
identity is configured, so anything inserted afterwards leaves a stale seal and
the script re-signs; and the staged worker is architecture-specific, so it
checks the triple before spending a full compile.

This leaves Linux bundles without a worker. That platform has no packaging
pipeline of its own and `cargo tauri dev` remains its supported path.

Two defects found in review and fixed before this block was accepted:

- The wrapper located the built app at the default `src-tauri/target` path
  regardless of `CARGO_TARGET_DIR` or a `.cargo/config.toml` override.
  Reproduced: with the target directory redirected, it silently patched a
  stale app left at the default path and reported success while the actual
  new build, elsewhere, stayed workerless. It now asks `cargo metadata` for
  the real target directory instead of assuming it.
- Re-signing after the worker was inserted used a bare `codesign --force
  --sign`, dropping any configured entitlements and hardened runtime -
  settings Tauri itself applies when it first seals the bundle. The wrapper
  now reads `bundle.macOS.entitlements` and `bundle.macOS.hardenedRuntime`
  from `tauri.conf.json` and passes them to the re-sign.

## Block 3: Launch the bundled executable and retain supervision

- Resolve the worker executable through Tauri's resource path API and spawn
  that absolute path from Rust. Do not depend on the current directory or PATH.
- Preserve port and session-token environment variables, piped stdout/stderr,
  readiness detection, ongoing pipe draining, and startup error reporting.
- Preserve process-group isolation, graceful termination, forced cleanup,
  and cleanup after failed startup. OCR subprocesses still need supervision
  even if the new packaging changes the PyInstaller process tree.
- Keep changes focused; extract path resolution only if useful for ownership
  or testing. Review comments that assume a onefile launcher forks Python.
- Retain the startup timeout initially; changing it is not a substitute for
  removing the measured startup cost.
- Extend the narrow existing Rust tests for executable resolution and launch
  failures, and run the existing readiness and termination tests.

Acceptance: development and packaged launches reach readiness through the
existing frontend contract, and failures and shutdown leave no worker behind.

## Block 4: Happy path end-to-end and performance verification

- Build the actual macOS application with the new worker resources and launch
  it from an installed location outside the checkout, including a path with
  spaces. Use an environment without development Python tools on PATH.
- Exercise the happy path across build, resource packaging, Rust launch,
  worker readiness, frontend connection, synthetic invoice analysis, rename,
  and app shutdown. Verify the expected renamed output and that the worker
  port closes and its subprocesses exit.
- Exercise OCR and local model loading with suitable non-sensitive fixtures
  and a provisioned model to catch missing dynamically loaded dependencies.
  Keep model-loading time separate from app-startup measurements.
- Repeat the baseline startup measurements on the packaged app, comparing
  first and subsequent launches. Confirm the worker no longer performs
  onefile temporary extraction at launch.
- Run focused Rust checks and the frontend production build; run backend or
  frontend tests when their behavior or contracts change. Record any checks
  that require macOS and cannot run in the development environment.
- Update human-facing packaging instructions and add the required changelog
  entry when implementation is complete, following repository conventions.

Acceptance: the installed app completes the full workflow with a measured
startup improvement and no dependency, readiness, or cleanup regression.

## Block 5: Security, sanity, and safety review

- Verify the final macOS bundle's nested executable/library placement,
  signatures, required entitlements, and notarization workflow. Resolve any
  resource-layout or signing constraints before considering packaging done.
- Test the distributed app's first launch with normal macOS validation;
  do not disable Gatekeeper or strip quarantine to claim success.
- Ensure executable resolution stays within the intended bundled resources,
  uses no shell interpolation, and does not fall back to a user-writable
  cached worker or an arbitrary executable on PATH.
- Verify session-token secrecy and existing localhost authentication remain
  intact. Timing and error logs must not expose credentials or invoice data.
- Check missing/non-executable resources, early exit, startup timeout, quit
  during startup, and normal quit for clear errors and complete cleanup.
- Verify the build cannot silently package an old onefile worker or a worker
  for the wrong architecture. Confirm the installed bundle is not modified
  at runtime and model/cache writes still use their intended writable paths.

Acceptance: normal distribution validation succeeds and negative cases
preserve authentication, process cleanup, and predictable executable selection.

## References

- [PyInstaller operating modes](https://pyinstaller.org/en/stable/operating-mode.html)
- [PyInstaller usage and UPX behavior](https://www.pyinstaller.org/en/stable/usage.html)
- [Tauri additional resources](https://v2.tauri.app/develop/resources/)
- [Tauri macOS application bundles](https://v2.tauri.app/distribute/macos-application-bundle/)
