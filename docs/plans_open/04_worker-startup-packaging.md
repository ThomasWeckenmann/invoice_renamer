# Worker startup packaging plan

Status: Blocks 1-3a complete and verified on macOS (dev and packaged launches
reach readiness at onedir speed; English and German OCR both work through
Apple Vision in dev and in the packaged app; the license file lands as
designed in the signed app). Blocks 4-5 not started.

## Goal

Reduce app startup time by shipping the Python worker as a PyInstaller
`--onedir` bundle inside the Tauri application. Users still install and open
one macOS `.app`; its internal worker dependencies are already on disk. On
macOS - dev checkout or packaged app alike - recognize scanned invoices
through the OS's own Vision framework instead of a bundled Tesseract, so OCR
works without users installing Homebrew or any OCR package at all. Linux
keeps using system Tesseract, unchanged.

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

### Block 3 notes

`worker.rs` no longer uses `tauri_plugin_shell`'s `sidecar()` API, which
required an `externalBin` entry that Block 2 had already removed - that call
would have failed at runtime for every launch. It now resolves the worker's
absolute path itself: `app.path().resource_dir()` (the packaged app's
`Contents/Resources`) first, falling back to the dev staging directory
`scripts/build_worker_sidecar.sh` writes to, resolved from the compiled-in
`CARGO_MANIFEST_DIR` rather than the process's current directory. Neither
candidate is looked up on PATH. The fallback is required, not defensive
padding: Tauri's own resource resolution collapses to the `cargo` output
directory in a dev run, which never holds the staged worker.

The resolved path is passed straight to `std::process::Command::new`,
replacing the shell plugin's own command construction; stdout/stdin/stderr
piping, env vars, process-group detachment, readiness detection, pipe
draining, and termination are all unchanged. `tauri-plugin-shell` had no
other caller in this codebase (confirmed by grep) and was not listed in
`capabilities/default.json`, so its plugin registration and Cargo dependency
were removed rather than left dead.

A doc comment on `WorkerHandle` asserted the PyInstaller launcher always
forks a Python child, which was written against the onefile build; it now
says this differs by build mode and that process-group signaling is what
makes the distinction not matter.

A review pass (Codex) flagged that the initial version made the dev staging
fallback unconditional: since `cargo tauri build` runs on the same checkout
`cargo tauri dev` stages a worker into, a release build with a missing or
corrupt bundled worker could silently launch the checkout's dev-staged one
instead, masking the exact packaging defect this resolution exists to catch.
The dev fallback is now gated on `cfg!(debug_assertions)` - true for `cargo
tauri dev`, false for `cargo tauri build` - so a release build only ever
trusts the packaged resource directory.

Verified in this sandbox with an isolated `CARGO_TARGET_DIR` (this checkout's
`src-tauri/target` is shared with the developer's Mac over virtiofs) and with
the developer's staged macOS worker under `src-tauri/resources/` temporarily
moved aside so `build.rs`'s target-triple guard did not block the Linux
compile, then moved back unchanged: `cargo check --lib`, `cargo test --lib`
(38 passed, 1 pre-existing ignored), `cargo clippy --lib -- -D warnings`, and
`cargo fmt --check` all pass. Tests cover path-resolution priority and
fallback, the fallback being absent for a release build, picking the first
existing candidate, the not-found error listing every path tried, and a
spawn against a resolved-but-missing path failing cleanly instead of falling
back to PATH.

Confirmed on macOS by the developer: both `cargo tauri dev` and
`scripts/build_macos_app.sh` reach worker readiness through the new
resolution path, at the onedir startup speed Block 1 measured.

## Block 3a: Use Apple Vision for OCR on macOS, keep Tesseract elsewhere

### Scope and current behavior

The worker includes `pytesseract`, which invokes a separate Tesseract
executable; it does not supply that executable, and the README asks users to
install Tesseract and language packages themselves. An earlier version of
this block instead planned to bundle a full Tesseract distribution (binary,
native libraries, and language data) inside the macOS app. That was dropped:
the packaging, Mach-O relocation, and license-notice work it required only
existed to solve a macOS-specific problem, and macOS already ships an OCR
engine of its own - the Vision framework - with no bundling, licensing, or
Homebrew dependency at all. `backend/src/invoice_renamer/documents/ocr.py`
already anticipated this in its module docstring: "Apple Vision (via
`ocrmac`) plugs in behind the same `OcrEngine` protocol on macOS; that
adapter is not implemented yet."

The engine choice follows the OS, not whether the run is packaged: every
macOS run - `cargo tauri dev` and a source checkout's own `uv run pytest`
included, not only the packaged app - uses Apple Vision, since the framework
is part of the OS itself and there is nothing macOS-specific left for
Tesseract to do. Linux keeps system Tesseract exactly as documented today,
unchanged, because it has no packaging pipeline (Block 2) and was never a
target for bundling anything. `documents/reader.py` requests `eng+deu`;
preserve that language coverage and the existing `OcrEngine` contract
(`recognize(image, *, language) -> OcrResult`) on both paths.

### Chosen library and its actual API

[`ocrmac`](https://github.com/straussmaximilian/ocrmac) (MIT license, PyPI,
latest 1.0.1) wraps `VNRecognizeTextRequest` via `pyobjc-framework-Vision`.
Confirmed from its source and PyPI metadata rather than assumed:

- `ocrmac.OCR(image, language_preference=[...], recognition_level="accurate",
  framework="vision").recognize()` returns `(text, confidence, bbox)` tuples.
  `image` accepts a file path or a PIL `Image.Image` - this project already
  has a decoded image at the OCR call site (`render_page_to_image`), so no
  round-trip through disk is needed.
- `language_preference` takes BCP-47 tags, e.g. `["en-US", "de-DE"]`, not
  Tesseract's `"eng+deu"` string - the new engine must translate between the
  two on its input from `reader.py`, which stays on the existing string form.
- `framework="vision"` (the default) is required, not `"livetext"`: the
  LiveText path always reports confidence `1.0`, which would silently
  discard the real per-recognition confidence `OcrResult.confidence` is
  meant to carry. Nothing in this app currently gates behavior on that
  value, so a scale difference from Tesseract's word-averaged confidence is
  acceptable, but throwing away real confidence entirely is not.
- Runtime dependencies are `click`, `Pillow` (already a dependency),
  `pyobjc-framework-Vision`, and its own `pyobjc-core` bridge - no bundled
  binaries, no bundled language data, nothing for a Mach-O relocation or
  staging script to do. `ocrmac`, `pyobjc-core`, and `pyobjc-framework-Vision`
  are all confirmed MIT-licensed (checked their PyPI metadata individually,
  not inferred from one of them).
- Requires macOS 10.15+ and Python 3.9+, both already satisfied.

### Licenses and redistribution

Lighter than the dropped Tesseract-bundling plan, but not nothing: this
repository has no existing third-party-license mechanism at all (checked -
nothing under that name in `docs/` or the app), and an MIT license's
condition - reproducing the copyright and permission notice - is a
requirement to satisfy, not a label PyPI's `License :: OSI Approved :: MIT
License` classifier already discharges on its own. PyInstaller does not
bundle a package's `LICENSE`/`NOTICE` file into a frozen build automatically;
only what the code imports at runtime gets collected.

- Collect the actual `LICENSE` text for `ocrmac`, `pyobjc-core`,
  `pyobjc-framework-Vision`, and `click` (`ocrmac`'s own dependency) and ship
  it somewhere a user can read from the installed app - a bundled
  `THIRD-PARTY-LICENSES` file is enough; there is no requirement to build UI
  for it. Done: `backend/THIRD-PARTY-LICENSES`, with each license fetched
  from its actual upstream source rather than assumed from a PyPI classifier
  - `pyobjc-core` and `pyobjc-framework-Vision` share identical text from the
    monorepo they're both built from, and that text itself flags that a
    vendored `libffi-src`, if present, carries a separate license not yet
    confirmed from this sandbox.
  - `scripts/build_worker_sidecar.sh` now passes it to PyInstaller via
    `--add-data`, verified with a throwaway build in this sandbox (not the
    real worker, to avoid touching the shared staged macOS build): the file
    lands at `<worker dir>/_internal/THIRD-PARTY-LICENSES`, not beside the
    executable - PyInstaller's modern onedir layout keeps only the
    executable at the top level. A relative `--add-data` source resolves
    against `--specpath`, not the invoking shell's cwd, which is not
    documented and only caught by that throwaway build - the script now uses
    an absolute path.
- Verify the file is actually present at that path in the real frozen
  `--onedir` output and the final signed `.app`, not just in this sandbox's
  throwaway smoke test - confirmed in Block 5, not assumed here.

### Implementation

- Add `ocrmac` to `backend/pyproject.toml` as a macOS-only dependency (an
  environment marker such as `sys_platform == 'darwin'`), so a Linux dev
  install never pulls in `pyobjc-framework-Vision`.
- Add an `AppleVisionOcrEngine` implementing the existing `OcrEngine`
  protocol in `documents/ocr.py` (or a sibling module if that keeps the file
  focused - see the repository's guidance on splitting out non-trivial new
  state rather than growing one file). It translates the `"eng+deu"`-style
  language string into `ocrmac`'s BCP-47 list, calls `OCR(...).recognize()`
  with `framework="vision"`, and reassembles `OcrResult(text, confidence)`
  from the returned tuples - preserving whatever line-grouping behavior is
  necessary for the text to make sense to the analysis pipeline downstream.
- Pick the engine per platform where `TesseractOcrEngine()` is constructed
  today (`documents/reader.py`), e.g. Apple Vision when `sys.platform ==
  "darwin"`, Tesseract otherwise. Keep this selection narrow and testable in
  isolation rather than threading a platform check through `reader.py`'s own
  logic.
- Verify `pyobjc-framework-Vision` actually freezes and runs under
  PyInstaller onedir. `pyinstaller-hooks-contrib` is already a build
  dependency and commonly carries PyObjC framework hooks, but whether it
  covers this specific framework is unconfirmed - this must be run and
  observed on the actual build Mac, not assumed from the dependency being
  present. If imports are missing at runtime, add the narrowest hook or
  `--collect-all`/`--hidden-import` PyInstaller option that fixes it, in
  `scripts/build_worker_sidecar.sh` or a PyInstaller hook file, not a
  broad "collect everything" fallback.
- Confirm the Vision framework needs no macOS entitlement or user-facing
  permission prompt for recognizing text in an already-in-memory image (it
  does not touch the camera or photo library, unlike other Vision use
  cases) - verify this against the actual signed, hardened-runtime build
  rather than assuming it from general Vision framework use.
- Update the README to describe Tesseract as a Linux-only requirement, since
  macOS - dev checkout or packaged app - now uses Vision either way. Add the
  implementation changelog entry once this is verified working, not for this
  planning rewrite.

### Verification and acceptance

- Unit-test the language-string translation and the platform-based engine
  selection without needing a Mac - both are plain logic, independent of
  actually calling Vision or Tesseract.
- Unit-test the confidence/text aggregation that turns `ocrmac`'s returned
  `(text, confidence, bbox)` tuples into an `OcrResult` using controlled,
  synthetic tuples - not real Vision output - covering multiple lines, a
  single line, and the empty-results case (mirroring the existing blank-page
  Tesseract test). This is where correctness of the aggregation logic itself
  is actually established, and it needs no Mac to run.
- Add a scanned German invoice fixture (`fixtures/` is synthetic and
  non-sensitive already) if one does not exist; a `language_preference`
  covering German that no fixture ever exercises does not establish German
  support actually works. On macOS, run the real `AppleVisionOcrEngine`
  against both `fixtures/scanned_invoice.pdf` and the German fixture, and
  confirm the recognized text and a valid confidence (a float, in range,
  when any text was recognized) - not that confidence *varies*, which
  the implementation forwards from Vision but never guarantees.
- Run that same real-image check through the packaged onedir worker
  (`scripts/build_worker_sidecar.sh` + `scripts/build_macos_app.sh`), since
  the PyInstaller-freezing risk above only shows up in a frozen build, not
  under `uv run pytest`.
- Confirm Linux behavior is unaffected: existing `TesseractOcrEngine` tests
  keep passing there, and the platform check picks Tesseract on Linux (and
  Vision on macOS, including a plain macOS dev checkout - see above).
- Carry this into Block 4's full scanned-invoice analysis, rename, and
  shutdown happy path.

Acceptance: every macOS run, packaged or dev, recognizes English and German
scanned invoices using only the OS's own Vision framework, with no
Tesseract, Homebrew, or bundled OCR asset involved, and Linux OCR behavior
is unchanged.

### Block 3a implementation notes

`documents/ocr.py` gained `AppleVisionOcrEngine` (calls `ocrmac.OCR(...,
framework="vision")`, imported lazily inside `recognize()` so the module
still imports on Linux, where `ocrmac` isn't installed), a pure
`aggregate_vision_observations()` that sorts Vision's unordered
`(text, confidence, bbox)` tuples top-to-bottom by bounding-box position and
averages confidence, and `default_ocr_engine()` (`sys.platform == "darwin"`
picks Vision, otherwise Tesseract). `documents/reader.py` and
`analysis/pipeline.py` - the actual production call sites - now go through
`default_ocr_engine()` instead of constructing `TesseractOcrEngine()`
directly; `evaluation/benchmark.py` was deliberately left pinned to
Tesseract, since switching a model-accuracy benchmark's OCR engine based on
whoever's machine runs it is a reproducibility question outside this
block's scope. `ocrmac>=1.0.1` was added to `backend/pyproject.toml` gated
on `sys_platform == 'darwin'`, with a matching mypy override so its absence
on Linux isn't a type-check error.

Added `fixtures/scanned_invoice_de.pdf` (via
`scripts/generate_fixture_pdfs.py`, generating only that one file rather
than re-running the whole script, since reportlab embeds a build timestamp
that would have produced spurious diffs in every other fixture) and
confirmed it actually OCRs correctly through the existing `TesseractOcrEngine`
in this sandbox before relying on it.

Collected real license text (fetched from each project's own repository,
not inferred from a PyPI classifier) into `backend/THIRD-PARTY-LICENSES` for
`ocrmac`, `pyobjc-core`, `pyobjc-framework-Vision`, and `click`,  and wired
`scripts/build_worker_sidecar.sh` to stage it via PyInstaller's `--add-data`.
Two things about that flag turned out not to be documented and were only
caught by a throwaway build in this sandbox (a trivial script, isolated
`--distpath`/`--specpath`/`--workpath`, never touching the real worker or
the developer's staged macOS build): a relative `--add-data` source resolves
against `--specpath`, not the invoking shell's cwd, so the script now passes
an absolute path; and the destination lands inside onedir's `_internal/`,
not beside the executable, since modern PyInstaller keeps only the
executable at the top level.

Verified in this sandbox: `uv run pytest` (277 passed, 2 skipped - the two
real-Vision tests, correctly, on Linux), `ruff format --check`, `ruff
check`, and `mypy` all clean, using an isolated `UV_PROJECT_ENVIRONMENT`
throughout (`backend/.venv` is the developer's real macOS venv, shared over
virtiofs - confirmed still pointing at the macOS interpreter afterward, not
resynced). `uv.lock` picked up `ocrmac`/`pyobjc-core`/`pyobjc-framework-Vision`/
`pyobjc-framework-Cocoa` correctly gated to `sys_platform == 'darwin'`.

### Block 3a confirmed on macOS, 2026-09-19

Confirmed by the developer, resolving everything this sandbox couldn't
check: `pyobjc-framework-Vision` freezes and runs correctly under
PyInstaller onedir, in both `cargo tauri dev` and the packaged app moved to
`/Applications`. English and German scanned invoices both recognize
correctly through Apple Vision in both contexts, with no OCR issues and no
entitlement/permission prompt encountered. The license file lands exactly
where the sandbox's throwaway build predicted -
`Contents/Resources/worker/_internal/THIRD-PARTY-LICENSES` inside the real
signed `/Applications/Invoice Renamer.app` - with the full, correct text for
`ocrmac`, `pyobjc-core`, `pyobjc-framework-Vision`, and `click`.

Still open: whether the installed `pyobjc-core` actually vendors `libffi-src`
(flagged in its own license file as carrying a separate license if present)
was not checked and remains a note in `THIRD-PARTY-LICENSES` itself, not a
blocker for this block.

A review pass found two defects, fixed before this block is accepted:

- `aggregate_vision_observations` sorted purely by vertical position with
  horizontal position only breaking exact ties. Vision's bounding boxes for
  two entries in the same invoice-table row (e.g. an item name and its
  price in separate columns) rarely share an exact y - reproduced this
  turning "Item A, 10.00 EUR, Item B, 20.00 EUR" into "10.00 EUR, Item A,
  20.00 EUR, Item B". It now clusters observations into rows by vertical
  center within a tolerance (half the taller box's height) before ordering
  left to right within each row, with a regression test reproducing the
  exact scrambled sequence above.
- The three `TesseractOcrEngine` integration tests ran unconditionally, so
  they would fail with `TesseractNotFoundError` on a Mac following the
  README's own updated instructions, which no longer install Tesseract
  there. They're now skipped whenever `shutil.which("tesseract")` finds
  nothing, which also means Linux - where the README still requires
  Tesseract - keeps full, unskipped coverage rather than losing it to a
  blanket platform check.

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
  Include a scanned invoice using Block 3a's Apple Vision engine through the
  complete analysis, rename, and shutdown workflow, on a Mac with no system
  Tesseract or Homebrew installed, confirming macOS OCR never depends on
  either; verify the expected OCR text and renamed output. Keep model-loading
  time separate from app-startup measurements.
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

### Block 4 progress, 2026-09-19

Confirmed by the developer: `scripts/build_macos_app.sh`'s output moved to
`/Applications/Invoice Renamer.app` (outside the checkout, path contains a
space) launched from Finder and completed the happy path - import, analysis,
rename - with the expected output, no issues reported.

`scripts/measure_worker_startup.py -n 5` run directly against the installed
bundle's own worker (`/Applications/Invoice Renamer.app/Contents/Resources/
worker/invoice-renamer-worker`), not a build-tree copy:

| run | total | to Python | Python to ready |
| --- | --- | --- | --- |
| 1 | 0.481 | 0.132 | 0.349 |
| 2 | 0.430 | 0.117 | 0.312 |
| 3 | 0.429 | 0.117 | 0.312 |
| 4 | 0.433 | 0.120 | 0.313 |
| 5 | 0.428 | 0.116 | 0.312 |
| median of 2-5 | 0.429 | 0.117 | 0.312 |

Matches Block 1's onedir baseline (0.446s median) within noise, confirming
the shipped bundle - not just a build-tree copy - keeps the onedir speedup
and shows no first-launch extraction spike.

Resolved since first written:

- Worker port/process cleanup after quitting the app: confirmed clean
  (`ps aux | grep -i invoice-renamer-worker` showed nothing but the grep
  itself after quit).
- OCR: confirmed via Block 3a - English and German scans both recognize
  correctly through Apple Vision, in dev and in the packaged app.
- Frontend production build (`npm run build` in `app/`): run by the
  developer, succeeded.
- Dev-Python-tools-on-PATH: moot rather than resolved - a GUI/Finder launch
  doesn't inherit a dev shell's PATH anyway, so this was never a meaningful
  test to begin with.

Resolved: removed a model, downloaded it fresh via the in-app model
manager, used it - confirmed working.

Still open before this block is accepted:

- Update human-facing packaging instructions if this testing surfaces any
  gap, and add the changelog entry once the block is accepted.

## Block 5: Security, sanity, and safety review

- Verify the final macOS bundle's nested executable/library placement,
  signatures, required entitlements, and notarization workflow. Resolve any
  resource-layout or signing constraints before considering packaging done.
  Confirm the frozen `pyobjc-framework-Vision`/`ocrmac` dependency needs no
  entitlement beyond what the app already requests, and open and read
  `Contents/Resources/worker/_internal/THIRD-PARTY-LICENSES` inside the
  actual signed `.app` (Block 3a's `--add-data` wiring, verified only with a
  throwaway build so far - not this file, at this path, inside a real signed
  app) to confirm the required MIT copyright/permission notices for
  `ocrmac`, `pyobjc-core`, and `pyobjc-framework-Vision` are really there - a
  PyPI license classifier is not itself compliance and is not a substitute
  for this check.
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
- [ocrmac](https://github.com/straussmaximilian/ocrmac) (MIT; wraps `VNRecognizeTextRequest` via `pyobjc-framework-Vision`)
- [Apple Vision framework - recognizing text in images](https://developer.apple.com/documentation/vision/recognizing-text-in-images)
