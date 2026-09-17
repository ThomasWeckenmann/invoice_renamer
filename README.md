# Invoice Renamer

A macOS and Linux desktop app that analyzes German and English invoice PDFs and proposes filenames in the form:

`YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf`

See `docs/plans_open/01_implementation-plan.md` for the full implementation plan.

This project isn't distributed as a built app — build it yourself from source using the steps below.

## Prerequisites

- **Rust**, via [rustup](https://rustup.rs), plus the Tauri CLI: `cargo install tauri-cli --version "^2.0.0" --locked`
- **Node.js** 20.19+ or 22.12+ (Vite 7's requirement) and npm
- **Python 3.12** and [uv](https://docs.astral.sh/uv/)
- **Tesseract OCR**, with English and German language data:
  - macOS: `brew install tesseract tesseract-lang`
  - Debian/Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-deu`
- **Linux only** — Tauri's native webview dependencies:
  ```
  sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
    libssl-dev libayatana-appindicator3-dev librsvg2-dev
  ```
- **macOS only** — Xcode Command Line Tools: `xcode-select --install`

## Building and running

From a clone of this repository:

1. Build the Python worker sidecar (PyInstaller, onefile):

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

Re-run step 1 after changing backend (Python) code — the sidecar binary is a separate build artifact and isn't rebuilt automatically by `cargo tauri dev`.

## Building a standalone app

`cargo tauri dev` above is the easiest way to develop or just use the app day to day. To get a real app you can launch directly (e.g. by double-clicking), build a release bundle instead, after completing steps 1 and 2 above:

```
cargo tauri build
```

This produces a native package under `src-tauri/target/release/bundle/`:

- macOS: `macos/Invoice Renamer.app`
- Linux: `deb/`, `appimage/`, and/or `rpm/`, depending on what's installed

This app isn't signed or notarized, since it's meant to be built and run by you, not distributed. That means macOS Gatekeeper blocks a plain double-click the first time — right-click the `.app` and choose Open once to bypass that; it opens normally after.
