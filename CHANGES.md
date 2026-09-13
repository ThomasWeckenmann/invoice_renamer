# Changelog

## Version 0.4.0 (2026-09-13, claude sonnet-5)

- Add the BB-04 OCR adapter: Tesseract-based text recovery for pages without a text layer

  Pages lacking usable text are now rendered via pypdfium2 and OCR'd with
  Tesseract, reconstructing line breaks from its word bounding-box groupings
  and reporting per-page confidence. Requires the tesseract-ocr binary plus
  eng/deu language data installed locally; ocrmac stays a future macOS adapter.

- bugfix: stop announcing worker readiness before the socket is actually bound

  A failed port bind could still be reported as 'ready' to the desktop shell,
  since FastAPI's startup lifespan ran before uvicorn's socket bind. The ready
  marker now only prints after uvicorn confirms the bind succeeded.

- bugfix: count non-whitespace characters when deciding a page needs OCR

  Padding a page with wide runs of spaces could push it over the usability
  threshold on raw character count alone, skipping OCR on sparse text.

- bugfix: preserve the amount/currency suffix when truncating long filenames

  A long seller/product name could truncate the whole filename stem,
  silently dropping the amount and currency without flagging review.

## Version 0.3.0 (2026-09-13, claude sonnet-5)

- Add the Milestone 2 core-processing pipeline: PDF reader, extraction contract, and filename builder

  The document reader extracts per-page text and flags pages needing OCR
  without running an OCR engine yet (deferred to Milestone 3, per the plan).
  It also detects embedded ZUGFeRD/Factur-X XML by presence, not yet parsing
  individual fields. Synthetic PDF fixtures now live under fixtures/.

## Version 0.2.0 (2026-09-13, claude sonnet-5)

- Add the Milestone 1 feasibility-spike walking skeleton: FastAPI worker, Tauri shell, and frontend placeholder

  The Tauri shell spawns the FastAPI worker as a PyInstaller sidecar with a
  per-launch session token and port, waiting for its readiness signal before
  exposing the endpoint to the frontend. Verified end-to-end on Linux; macOS
  signing and the onedir/Nuitka packaging comparison remain open per the plan.

## Version 0.1.0 (2026-09-13, claude sonnet-5)

- Initial project skeleton.