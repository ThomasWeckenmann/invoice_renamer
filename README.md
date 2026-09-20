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

On first launch, use the model manager in the app to download a local model (e.g. Granite-3.3-2B-Instruct) before analyzing invoices — no model is bundled or downloaded automatically.

Re-run step 1 after changing backend (Python) code — the worker is a separate build artifact and isn't rebuilt automatically by `cargo tauri dev`. It's built for the machine you build it on, and the Tauri build refuses to package a worker built for a different architecture.

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
