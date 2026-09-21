# Invoice Renamer

A macOS and Linux desktop app that analyzes German and English invoice PDFs and proposes filenames in the form:

`YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf`

See `docs/plans_open/01_implementation-plan.md` for the full implementation plan.

This project isn't distributed as a built app — build it yourself from source using the steps below.

## Prerequisites

- **Rust**, via [rustup](https://rustup.rs), plus the Tauri CLI: `cargo install tauri-cli --version "^2.0.0" --locked`
- **Node.js** 20.19+ or 22.12+ (Vite 7's requirement) and npm
- **Python 3.12** and [uv](https://docs.astral.sh/uv/)
- **Linux only** — Tesseract OCR, with English and German language data:
  `sudo apt install tesseract-ocr tesseract-ocr-deu`. macOS needs no OCR
  package at all - scanned invoices are recognized through the OS's own
  Vision framework.
- **Linux only** — Tauri's native webview dependencies:
  ```
  sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
    libssl-dev libayatana-appindicator3-dev librsvg2-dev
  ```
- **macOS only** — Xcode Command Line Tools: `xcode-select --install`

## Building and running

From a clone of this repository:

1. Build the Python worker the app bundles (PyInstaller):

   ```
   scripts/build_worker_sidecar.sh
   ```

2. Install frontend dependencies:

   ```
   cd app && npm install && cd ..
   ```

3. Run the app in dev mode, from the repository root:

   ```
   cargo tauri dev
   ```

On first launch, use the model manager in the app to download a local model before analyzing invoices — no model is bundled or downloaded automatically. Two are offered: **Qwen3-0.6B**, which the app selects automatically once it is installed, and **Granite-3.3-2B-Instruct**, which is slower (~16-18s vs ~5-12s per invoice) but noticeably more accurate on amounts and dates. Pick Granite in the Model panel if a proposed filename has to be right more often than it has to be fast; see `docs/model_benchmark_findings.md` for the measured difference.

Re-run step 1 after changing backend (Python) code — the worker is a separate build artifact and isn't rebuilt automatically by `cargo tauri dev`. It's built for the machine you build it on, and the Tauri build refuses to package a worker built for a different architecture.

### Linux, when this checkout is shared with a Mac

Skip this if you're on a plain Linux install. It applies to setups like this
project's devcontainer, where the same checkout is mounted into both a Mac
host and a Linux container: `backend/.venv`, `app/node_modules`,
`src-tauri/target`, and the staged worker are ordinary paths inside the
checkout, so a Linux install or build writes Linux-specific files into paths
the Mac side also uses, and the next `cargo tauri dev` on the Mac breaks.

`scripts/linux_workspace.sh` avoids that by mirroring the checkout into a
Linux-local directory outside the shared mount and running your command
there, leaving the checkout's own `.venv`/`node_modules`/`target`/staged
worker untouched. Run the same three steps through it instead:

```
scripts/linux_workspace.sh . scripts/build_worker_sidecar.sh
scripts/linux_workspace.sh app npm install
scripts/linux_workspace.sh . cargo tauri dev
```

General form: `scripts/linux_workspace.sh {.|backend|app|src-tauri} <command...>`
— the first argument picks the working directory inside the mirror, the rest
is run there as-is (e.g. `scripts/linux_workspace.sh backend uv run pytest`).

It's a snapshot, not a live editing workspace: each run re-syncs the mirror
from the checkout before running your command, so edit the checkout as usual
and re-run the wrapper to pick up changes — there's no automatic live sync,
and a running `cargo tauri dev` holds the mirror until you stop it. Commands
run directly in the checkout (not through the wrapper) are not protected by
any of this.

## Building a standalone app

`cargo tauri dev` above is the easiest way to develop or just use the app day to day. To get a real app you can launch directly (e.g. by double-clicking), build a release bundle instead, after completing steps 1 and 2 above:

```
scripts/build_macos_app.sh
```

This produces `src-tauri/target/release/bundle/macos/Invoice Renamer.app`.

Use that script rather than `cargo tauri build` on its own. The worker is a directory of libraries that PyInstaller ties together with symlinks, and Tauri's resource bundling resolves symlinks into duplicate files. The script runs the Tauri build, copies the worker into the app with its layout intact, checks the copy arrived complete, and re-signs the app if the build had signed it.

On Linux, `cargo tauri build` still produces `deb/`, `appimage/` and/or `rpm/` packages, but they don't contain the worker; `cargo tauri dev` is the supported way to run the app there.

This app isn't signed or notarized, since it's meant to be built and run by you, not distributed. That means macOS Gatekeeper blocks a plain double-click the first time — right-click the `.app` and choose Open once to bypass that; it opens normally after.

## Licenses

Invoice Renamer's own source code is licensed under the [MIT License](LICENSE).
Copyright (c) 2026 Thomas Weckenmann.

The [third-party notices](backend/THIRD-PARTY-LICENSES) include the following macOS OCR dependencies:

| Dependency | License |
| --- | --- |
| ocrmac | MIT |
| pyobjc-core | MIT |
| pyobjc-framework-Vision | MIT |
| click | BSD-3-Clause |

These notices are included in the packaged worker at `_internal/THIRD-PARTY-LICENSES`. They cover the macOS OCR additions, not every application dependency; see the notices for the separate libffi caveat.

The [model catalog](backend/src/invoice_renamer/models/catalog_data.py) records Apache 2.0 for both Granite 3.3 2B Instruct and Qwen3 0.6B. Models are downloaded separately through the app and are not bundled with it. Dependency and model licenses are separate from the project's own license.
