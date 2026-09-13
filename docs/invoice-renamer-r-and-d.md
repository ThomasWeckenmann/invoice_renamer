# Mac Invoice Renamer — R&D

## Status

Core product and architecture decisions are complete. Exact models, packaging, and performance must be validated with a prototype. No implementation or model benchmark exists yet.

## Product decisions

- macOS and Linux desktop app. macOS minimum: M2 with 16 GB unified memory. Linux: no defined minimum memory spec; use GPU acceleration when available (CUDA/ROCm), otherwise fall back to CPU.
- German and English invoices.
- Supports selectable-text, scanned, mixed-page, and ZUGFeRD/Factur-X PDFs.
- Local processing is the default.
- Cloud processing requires explicit user selection; invoices are never uploaded automatically.
- The app manages local model downloads and storage.
- Editable filename previews with individual or batch approval.
- Rename original PDFs in place and provide Undo.
- Show supported open and closed models in clearly separated sections.
- Keep closed models visible but disabled until an OpenRouter key is configured.
- After each run, show inference time, model/provider, processing path, warnings, token usage when available, and optional cloud cost.
- Mac App Store distribution remains an option, not a requirement.
- A website may reuse the frontend later but is not part of the initial scope.

## Architecture

| Layer | Choice |
|---|---|
| Desktop shell | Tauri 2 |
| Frontend | React + TypeScript |
| Backend | Python + FastAPI worker bundled as a Tauri sidecar |
| Local inference | Hugging Face Transformers + PyTorch; backend auto-selected (MPS on Apple Silicon, CUDA/ROCm on Linux, else CPU) |
| OCR | Open-source engine by default; Apple Vision through `ocrmac` automatically on macOS |
| Cloud inference | OpenRouter with one user-supplied API key |

Keep PDF processing and inference independent of FastAPI. Keep React independent of desktop file operations. This allows testing the Python core separately and reusing the API/UI for a future website.

Bind the local API to loopback, authenticate app requests, restrict origins, and stop the worker with the app.

## Processing pipeline

1. Inspect the PDF and embedded attachments. Prefer validated ZUGFeRD/Factur-X XML when available.
2. Extract existing text from each page.
3. Render and OCR pages with missing or unusable text.
4. Send the combined text to the selected local or cloud language model.
5. Require structured fields: invoice date, seller, product summary, gross total, currency, and supporting evidence.
6. Validate the fields and build the filename with deterministic Python code.
7. Show editable previews; rename only after approval.
8. Record source, destination, and file identity for Undo.

The model returns data only. It never chooses paths or performs filesystem operations.

## Filename rules

Format:

`YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf`

Example:

`2026-09-12_Apple_MacBook-Air_2180-EUR.pdf`

- Date: invoice issue date (`Rechnungsdatum`).
- Seller: actual seller rather than marketplace or payment processor when distinguishable.
- Product: main expensive product when clearly dominant; otherwise a short summary.
- Summary language: follow the invoice language; preserve brand and model names.
- Amount: gross total including VAT, rounded to the nearest whole unit in the original currency.
- Currency: always append its code, such as `EUR` or `USD`; no conversion.
- Transliterate German characters: `ä → ae`, `ö → oe`, `ü → ue`, `ß → ss`.
- Missing or ambiguous fields: flag for editing; never silently invent values.
- Collisions: never overwrite an existing file; add a deterministic suffix.

Minor thresholds, midpoint rounding, maximum length, and suffix format can use documented implementation defaults because every proposal is editable before approval.

## Local model setup

Transformers/PyTorch was selected for AI-engineering learning. Begin with `pipeline()`, then move to processors/tokenizers, direct model loading, generation settings, precision, memory management, structured extraction, and evaluation.

On first use, the app should detect chip, memory, macOS compatibility, and free disk space; recommend a model; show download size; download with progress/resume; verify files; and support removal or replacement.

Treat downloaded models as data. Pin model revisions, verify checksums, and disable remote model code. After download, local mode should work offline.

The exact model and quantization are intentionally undecided until benchmarking. MLX and llama.cpp are possible later optimization exercises, not initial runtimes.

## Cloud setup

- OpenRouter uses one user-supplied key stored in the OS keychain (Keychain on macOS, Secret Service/libsecret on Linux).
- Cloud mode is selected explicitly per job or batch.
- Closed models remain visible but greyed out until the key is set.
- Use only endpoints supporting the required input and structured output.
- Request strict JSON Schema, require supported parameters, and validate responses locally.
- Exact default cloud model remains to be benchmarked.

## Validation before full implementation

Build a thin end-to-end prototype that:

1. Packages Tauri, React, Python, FastAPI, PyTorch, and platform OCR (`ocrmac` on macOS, an open-source engine elsewhere).
2. Runs a small local model through MPS on an M2/16 GB Mac.
3. Reads one selectable PDF and one scanned PDF.
4. Produces an editable filename proposal, renames the file, and undoes it.
5. Runs signed and sandboxed to test the possible App Store path.

Then benchmark 2–3 small local models on 20–50 representative invoices. Measure field accuracy, useful product summaries, hallucinations, correction rate, cold/warm latency, peak memory, download size, and packaging reliability. Choose the smallest model that meets the quality target.

## Remaining implementation choices

- Exact local model, quantization, and default OpenRouter model.
- Cross-platform OCR engine (e.g. Tesseract vs. EasyOCR) for non-macOS, pending benchmark; avoid adding a second ML framework (e.g. PaddleOCR) given the packaging cost already carried by PyTorch.
- Whether Linux ships as a full MVP release target (its own packaging/signing/distribution pipeline) or remains a development/compatibility target only, decided when packaging work starts.
- PDF extraction/rendering libraries; likely `pypdf` plus `pypdfium2`.
- Supported macOS floor.
- Model download manifest and update policy.
- Signed/sandboxed Python worker configuration and App Store feasibility.

These require prototypes or benchmarks rather than more product discussion.

## Essential sources

- [Hugging Face Transformers pipelines](https://huggingface.co/docs/transformers/en/main_classes/pipelines)
- [Hugging Face Transformers on Apple Silicon](https://huggingface.co/docs/transformers/en/perf_train_special)
- [PyTorch MPS backend](https://docs.pytorch.org/docs/stable/notes/mps.html)
- [Apple Vision text recognition](https://developer.apple.com/documentation/vision/locating-and-displaying-recognized-text)
- [`ocrmac`](https://github.com/straussmaximilian/ocrmac)
- [Tauri external binaries](https://v2.tauri.app/develop/sidecar/)
- [Tauri App Store distribution](https://v2.tauri.app/distribute/app-store/)
- [FastAPI features](https://fastapi.tiangolo.com/features/)
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [OpenRouter privacy and data collection](https://openrouter.ai/docs/guides/privacy/data-collection)
- [pypdf text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
- [ZUGFeRD/Factur-X](https://www.ferd-net.de/standards/zugferd)
- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
