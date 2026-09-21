# JPG scan support

Status: All 6 blocks implemented and verified. Backend fully verified in
this sandbox throughout (pytest/ruff/mypy); the developer separately
confirmed `npm run test` and `cargo test --lib` pass, and live-tested
`.jpg`/`.jpeg` imports successfully. Two items remain genuinely
unconfirmed, not failing: `npm run build`'s `vite build` step, and a batch
mixing one PDF and one JPEG together (see Blocks 5-6's notes) - not yet
moved to docs/plans_done/.

## Goal

Accept a single-page JPEG (`.jpg`/`.jpeg`) scanned invoice as an alternative
input format alongside PDF, routed through the existing OCR engine into the
same extraction/filename/RunMetrics pipeline, without changing PDF behavior.

## Current evidence

- Upload gate is PDF-only by magic bytes: `_PDF_MAGIC = b"%PDF-"`
  (`backend/src/invoice_renamer/api/analyses_routes.py:34`), enforced at
  `analyses_routes.py:252-253` (`HTTPException(422, "upload is not a PDF
  file")`). No MIME-type check exists; a renamed non-PDF file with the right
  first 5 bytes would already pass this gate today.
- `analysis/pipeline.py::run_document_analysis` (`pipeline.py:118-278`) is PDF
  end to end: `open_validated_pdf` (`documents/pdf_open.py`, `pypdf.PdfReader`,
  rejects empty/>200-page PDFs), `route_xml`/`discover_invoice_xml`
  (`documents/xml_attachments.py` - ZUGFeRD/Factur-X *attachment* discovery,
  meaningless for a file with no PDF container), then `read_document`
  (`documents/reader.py:24-52`).
- `read_document` extracts each page's text via `pypdf`'s
  `page.extract_text()`; when a page yields fewer than
  `_MIN_USABLE_TEXT_CHARS` (20) non-whitespace characters, it rasterizes that
  page via `render_page_to_image` (`documents/render.py`, opens the bytes with
  `pypdfium2.PdfDocument` - PDF-specific) and calls
  `ocr_engine.recognize(image: PIL.Image, *, language) -> OcrResult`
  (`documents/ocr.py`). The OCR engine itself (`TesseractOcrEngine` /
  `AppleVisionOcrEngine`) only ever consumes a `PIL.Image` - it has no PDF
  dependency of its own.
- The model is text-only: `extract_invoice` (`inference/extractor.py`) builds
  a prompt from the page text (`inference/prompts.py`) and calls
  `LanguageModel.generate(prompt: str) -> str`
  (`inference/language_model.py`); the concrete `TransformersExtractor` is a
  local HF `AutoModelForCausalLM` text model with no vision encoder. OCR'd
  text from a JPEG would feed this exact same code path unchanged.
- Pillow (`pillow>=12.3.0`, `backend/pyproject.toml:15`) is already a backend
  dependency and decodes JPEG natively - no new image-decoding dependency is
  needed.
- Output filename extension is hardcoded regardless of input format:
  `naming/builder.py:132` - `proposed_filename=f"{stem}.pdf"`.
  `build_filename_proposal(extraction: InvoiceExtraction) -> FilenameProposal`
  takes no format/extension input today (`naming/builder.py`,
  `naming/schema.py`).
- Frontend import surface assumes PDF in three places:
  `app/src/lib/tauri/dialog.ts:9` (`filters: [{ name: "PDF", extensions:
  ["pdf"] }]`), `app/src/features/batch/components/ImportDropzone.tsx:16-17,26`
  (`isPdfPath()` silently drops any non-`.pdf` dropped path with no error
  shown to the user), and `app/src/lib/tauri/files.ts:19-22`
  (`readPathAsFile()` hardcodes `type: "application/pdf"` on the constructed
  `File` regardless of the source path's real extension).
- `src-tauri/src/commands/rename.rs`'s `split_stem_and_extension`
  (`rename.rs:93-98`) splits on the last `.` in the filename with no
  format-specific logic - it already handles a `.jpg` extension correctly by
  inspection, just untested against one (`rename.rs:390-394` only asserts
  `.pdf`/no-extension/dotfile cases).
- No thumbnail/image-preview UI exists in `app/src/features/batch/` today, so
  adding JPEG implies no new frontend preview work.
- Existing hardcoded-PDF tests that will need JPEG-path additions (not
  replacements): `ImportDropzone.test.tsx`, `files.test.ts`,
  `useBatchWorkspace.test.ts`, `useRenameTransaction.test.ts`,
  `BatchItemRow.test.tsx`.
- `scripts/generate_fixture_pdfs.py` is the existing convention for
  synthetic, non-sensitive test fixtures (reportlab + pypdfium2 + pypdf);
  there is no equivalent JPEG fixture generator yet.

## Behavior decisions

- Scope to JPEG only (`.jpg`/`.jpeg`) for this plan. Do not add PNG, TIFF,
  HEIC, or multi-image-as-one-invoice stitching now; leave the format
  detection/routing point extensible for later, but don't build for it.
- A JPEG is always treated as exactly one page, reusing the existing
  `NormalizedDocument`/`PageText` shape with a single entry - no changes
  needed in `inference/prompts.py`/`inference/extractor.py`.
- A JPEG never has a text layer, so it always goes through OCR - skip the
  `_MIN_USABLE_TEXT_CHARS` heuristic entirely for this format; there is no
  "extract text directly" option to try first.
- A JPEG has no PDF container, so it never has embedded ZUGFeRD/Factur-X XML.
  Never call `discover_invoice_xml`/`route_xml` for image input; a JPEG run's
  `extraction_source` must always be `model` and `xml_status` must always be
  `none` - never fabricate XML involvement.
- The upload gate becomes a real format *sniff*, not just a PDF check: detect
  PDF (`%PDF-`) or JPEG (`\xff\xd8\xff`) magic bytes and route accordingly;
  anything else keeps today's 422 rejection semantics.
- The proposed filename's extension must match the source format (`.jpg` for
  JPEG input, `.pdf` for PDF input, unchanged) - never force `.pdf` onto a
  renamed image.
- Define an explicit maximum decoded-pixel-dimension bound for JPEG (a PDF's
  equivalent bound is `MAX_PAGES`; a raster image has no page count, so this
  needs its own limit to avoid a decompression-bomb-scale allocation from a
  small file). Reuse the existing `_MAX_UPLOAD_BYTES` byte cap as-is - it is
  already format-agnostic.
- Frontend copy that currently says "PDF" and would become misleading (the
  dropzone label, any PDF-specific error text) must be updated to describe
  both accepted formats.

## Block 1: Detect and validate JPEG uploads

- Replace the single `_PDF_MAGIC` `startswith` check in `analyses_routes.py`
  with a small format sniff recognizing `%PDF-` and JPEG's `\xff\xd8\xff`,
  producing an explicit detected format (e.g. a small enum) instead of
  assuming PDF everywhere `pdf_bytes` is threaded today. Keep the existing
  422 status/behavior for anything matching neither.
- Add a JPEG-specific open/validate step analogous to
  `documents/pdf_open.open_validated_pdf` - decode via `PIL.Image.open`,
  catch decode errors as a validation failure (not a 500), and enforce the
  new maximum-pixel-dimension bound before any full decode/OCR work.
- Thread the detected format through the job record and into
  `run_document_analysis`'s call signature, so downstream code branches
  deliberately rather than implicitly assuming PDF.
- Tests: valid JPEG accepted; corrupt/truncated JPEG rejected (422, not 500);
  oversized-dimension JPEG rejected; non-PDF/non-JPEG bytes still rejected
  exactly as today; a PDF upload's behavior is provably unchanged.

Acceptance: the upload gate distinguishes PDF, JPEG, and unsupported bytes,
with bounded rejection behavior for all three, and zero behavior change for
existing PDF uploads.

### Block 1 accepted, 2026-09-21

Implemented as `documents/format.py` (`DocumentFormat` enum with `.extension`,
`detect_document_format`) and `documents/image_open.py`
(`open_validated_jpeg`, `MAX_DIMENSION_PIXELS = 8000` - checked from the
JPEG header via `PIL.Image.open(...).size` before `.load()` triggers a full
decode, comfortably above a 600dpi A4 scan). `analyses_routes.py`'s upload
handler now calls `detect_document_format` in place of the old
`_PDF_MAGIC` check and, for JPEG, synchronously calls `open_validated_jpeg`
and returns 422 on `ValueError` - unlike PDF, which still only gets a
magic-byte check at upload and is deep-validated later inside the job (kept
that way deliberately, matching the plan's per-test-list intent that a
corrupt JPEG fails fast at upload while a corrupt PDF still fails the job
async, as today). `AnalysisJob.pdf_bytes`/`AnalysisCoordinator.submit`'s
`pdf_bytes` param were renamed to `document_bytes` (mechanical, all
positional call sites unaffected) since the field now also holds JPEG
bytes; `document_format: DocumentFormat` was added alongside it and threaded
into `run_document_analysis`. Verified: `backend/tests/documents/test_format.py`,
`backend/tests/documents/test_image_open.py` (valid/corrupt/truncated/
oversized-via-monkeypatched-limit cases), and new `test_analyses.py` cases
(`test_valid_jpeg_upload_is_accepted_and_completes`,
`test_corrupt_jpeg_upload_is_422_and_never_creates_a_job`,
`test_oversized_jpeg_dimensions_is_422_and_never_creates_a_job`) plus the
full existing PDF suite unchanged.

### Block 1 follow-up fix, 2026-09-21 - EXIF orientation ignored

Found during the developer's own manual testing (P2, not caught by this
session's own tests): `open_validated_jpeg` decoded a JPEG's raw pixels but
never applied its EXIF `Orientation` tag, so a phone/scanner photo saved
sideways or upside-down (common - cameras write pixels in sensor order and
rely on the tag for display rotation) reached OCR unrotated. Reproduced by
the developer with a real orientation-6 photo: OCR text came back garbled;
confirmed the fix (`ImageOps.exif_transpose`) restores it.

Fixed in `image_open.py`: `open_validated_jpeg` now returns
`ImageOps.exif_transpose(image)` instead of the raw decoded image, applied
after `.load()` so the dimension bound (Block 1) still checks the
as-stored size (harmless either way, since the bound is symmetric across
both axes). New fixture `fixtures/scanned_invoice_exif_orientation_6.jpg`
(via a new `scripts/generate_fixture_pdfs.py::_scanned_invoice_jpeg_exif_rotated`,
again generated in isolation rather than through `main()`) has real
rendered invoice text stored pre-rotated 90 degrees with an
Orientation=6 tag. Verified: `test_image_open.py`'s
`test_exif_orientation_is_normalized_before_returning` (a fast, non-OCR
pixel-marker check that also catches a wrong-direction rotation, which a
bare size-swap assertion wouldn't) and `test_reader.py`'s
`test_jpeg_with_exif_orientation_is_still_recovered_correctly_via_ocr`
(reproduces the developer's exact bug end to end: OCR text confirmed
garbled without the fix, correct with it). Full backend suite: 455 passed,
`ruff format`/`ruff check`/`mypy src` all clean.

### Block 1 follow-up fix, 2026-09-21 - CMYK JPEGs fail OCR (regression from the EXIF fix above)

Found during the developer's own manual testing (P2), immediately after the
EXIF fix above shipped: a CMYK JPEG (produced by some scanners/Adobe tools)
now raised `OSError: cannot write mode CMYK as PNG` from inside pytesseract
itself. Root cause traced into `pytesseract.pytesseract.prepare()`: it picks
a save format via `'PNG' if not image.format else image.format`, and
`ImageOps.exif_transpose` returns a copy with `.format` cleared to `None` -
so every JPEG now serializes through pytesseract's PNG fallback, which
can't write CMYK pixel data (before the EXIF fix, `.format` was still
`"JPEG"`, and Pillow's JPEG encoder does support CMYK, so this path
happened to work by accident). Confirmed by the developer with a real CMYK
scan; converting to RGB before OCR fixes it.

Fixed in `image_open.py`: `open_validated_jpeg` now returns
`ImageOps.exif_transpose(image).convert("RGB")` instead of the transposed
image directly - RGB is always PNG-writable, so this holds regardless of
which format pytesseract ends up guessing, without needing to special-case
CMYK specifically or try to restore `.format`. New fixture
`fixtures/scanned_invoice_cmyk.jpg` (via a new
`scripts/generate_fixture_pdfs.py::_scanned_invoice_jpeg_cmyk`, again
generated in isolation) has real rendered invoice text saved in CMYK mode.
Verified: `test_image_open.py`'s `test_cmyk_jpeg_is_normalized_to_rgb` and
`test_cmyk_jpeg_can_actually_be_ocrd` (both reproduced failing against the
pre-fix code first), and `test_reader.py`'s
`test_cmyk_jpeg_is_still_recovered_correctly_via_ocr` (exact end-to-end
reproduction: `OSError` without the fix, correct extracted text with it).
Full backend suite: 458 passed, `ruff format`/`ruff check`/`mypy src` all
clean.

**Portability follow-up, same day:** the developer's own Mac run (no
Tesseract installed there - Apple Vision is the platform default) caught
that `test_cmyk_jpeg_can_actually_be_ocrd` called `pytesseract` directly,
unconditionally, unlike every other Tesseract-specific test in
`test_ocr.py`, which skips via `shutil.which("tesseract") is None`. Fixed
by adding that same `_requires_tesseract` skip guard to the one test that
needed it; `test_cmyk_jpeg_is_normalized_to_rgb` and the OCR-level
`test_reader.py` regression already went through `default_ocr_engine()`
(platform-correct) and needed no change.

## Block 2: Route a JPEG through OCR into a NormalizedDocument

- Add a JPEG document-reading path (e.g. a function alongside
  `read_document` in `documents/reader.py`, or a small sibling module
  matching this repo's existing per-concern module split) that decodes the
  validated JPEG straight to a `PIL.Image` (no `pypdfium2`/PDF render step
  needed - render.py stays PDF-only) and calls the existing
  `ocr_engine.recognize(image, language="eng+deu")` unconditionally, with no
  usable-text-length heuristic.
- Return a single-page `NormalizedDocument` (`pages=[PageText(page_number=1,
  needs_ocr=True, ...)]`, `embedded_xml=None`) - the same shape
  `inference/extractor.py` already consumes.
- Wire this path into `run_document_analysis` (`analysis/pipeline.py`),
  branching on the format detected in Block 1: skip `open_validated_pdf`,
  `route_xml`, and `read_document`'s PDF branch entirely for JPEG input, but
  keep the PDF branch byte-for-byte identical to today.
- Tests: a synthetic JPEG fixture with known rendered invoice text produces
  the expected OCR'd `NormalizedDocument`; `extract_invoice` runs unchanged
  on that document; resulting `RunMetrics` shows `pages_total=1`,
  `pages_ocr=[1]`, `xml_status="none"`, `extraction_source="model"`.

Acceptance: a JPEG reaches the same extraction code PDFs use today, via OCR
only, with no XML routing ever attempted and no change to PDF-path metrics.

### Block 2 accepted, 2026-09-21

Implemented as `documents/reader.py::read_image_document(image, *,
ocr_engine=None)` (always OCRs, single `PageText(page_number=1, needs_ocr=True,
...)`, `embedded_xml=None`) and `analysis/pipeline.py::_run_jpeg_analysis`, a
new private function `run_document_analysis` (now taking `document_format:
DocumentFormat = DocumentFormat.PDF`) delegates to when the format is JPEG,
before any PDF-specific code (`open_validated_pdf`/`route_xml`/`read_document`)
runs. The existing PDF branch is unchanged except for a `pdf_bytes` ->
`document_bytes` parameter rename (positional-safe, verified by the full
existing PDF test suite passing unmodified). A `_run_jpeg_analysis` failure
propagates and fails the job outright, matching the plan's decision (no
partial-XML case exists to preserve for a format that never has XML).
Verified: `backend/tests/documents/test_reader.py`'s new
`test_jpeg_image_is_recovered_via_ocr_as_a_single_page` (using a new checked-in
`fixtures/scanned_invoice.jpg`, confirmed to OCR its exact rendered text via
`TesseractOcrEngine` before relying on it) and
`test_jpeg_uses_custom_ocr_engine_when_provided`, plus
`test_pipeline.py`'s `test_jpeg_happy_path_ocrs_the_image_and_produces_a_jpg_suffixed_filename`
and `test_a_corrupt_jpeg_raises_instead_of_returning_a_synthesized_result`
(asserting `extraction_source="model"`, `xml_status="none"`, `xml_ms=0`,
`xml_fields_used=[]`, `xml_attachment_name=None` - XML was never touched, not
just reported as unused). The new fixture was generated via a new
`scripts/generate_fixture_pdfs.py::_scanned_invoice_jpeg` helper, but run in
isolation (calling that one function directly, not the script's `main()`) -
running the whole script regenerates every PDF fixture with a new reportlab
build timestamp and produces spurious diffs across all of them (same
footgun `04_worker-startup-packaging.md` already hit); confirmed no other
fixture changed.

## Block 3: Filename extension follows source format

- Give `build_filename_proposal` (`naming/builder.py`) the information it
  needs to pick `.jpg` vs `.pdf` for `proposed_filename` - thread the
  detected format from Block 1 through to this call instead of the current
  hardcoded `f"{stem}.pdf"`.
- Tests: JPEG input produces a `.jpg`-suffixed proposed filename with an
  otherwise identical stem-building/truncation/review-flag behavior;
  existing PDF tests in `naming/` are unchanged.

Acceptance: the proposed filename's extension always matches what was
actually uploaded.

### Block 3 accepted, 2026-09-21

`build_filename_proposal` now takes `document_format: DocumentFormat =
DocumentFormat.PDF` and builds `proposed_filename` as
`f"{stem}{document_format.extension}"`. Default keeps every existing PDF
call site (including `_xml_only_result` and the main PDF branch in
`pipeline.py`, neither of which was touched) byte-for-byte unchanged; only
`_run_jpeg_analysis` passes `document_format=DocumentFormat.JPEG`. Verified:
`backend/tests/naming/test_builder.py`'s new
`test_jpeg_source_gets_a_jpg_extension_instead_of_pdf` and
`test_pdf_is_still_the_default_extension`, plus the existing PDF-only tests
in that file unchanged.

## Block 4: Frontend import surface accepts JPEG

- `app/src/lib/tauri/dialog.ts`: extend the native open-dialog filter to
  include `jpg`/`jpeg` extensions alongside `pdf`.
- `app/src/features/batch/components/ImportDropzone.tsx`: generalize
  `isPdfPath()` into a supported-extension check covering both formats, and
  update the dropzone label copy so it no longer says "PDF" only.
- `app/src/lib/tauri/files.ts`: `readPathAsFile()` must derive the
  constructed `File`'s MIME type from the real source extension
  (`.jpg`/`.jpeg` -> `image/jpeg`, `.pdf` -> `application/pdf`) instead of
  hardcoding `application/pdf`.
- Add JPEG-path cases to the existing hardcoded-PDF tests
  (`ImportDropzone.test.tsx`, `files.test.ts`, `useBatchWorkspace.test.ts`,
  `useRenameTransaction.test.ts`, `BatchItemRow.test.tsx`) alongside the
  current PDF cases - do not replace PDF coverage.
- Add a `.jpg` case to `rename.rs`'s `split_stem_and_extension` tests
  confirming the existing generic logic already handles it (expected to need
  no production-code change there, only a regression test).

Acceptance: a user can pick or drop a `.jpg`/`.jpeg` file through the same
import surface as a PDF, with correct MIME typing and no silent drop.

### Block 4 accepted, 2026-09-21

Implemented as planned: `dialog.ts`'s `pickPdfFiles` renamed to
`pickInvoiceFiles` with `extensions: ["pdf", "jpg", "jpeg"]`;
`ImportDropzone.tsx`'s `isPdfPath` renamed to `isSupportedInvoicePath`
(checks all three extensions) and its label now reads 'Drag PDF or JPG
invoices here'; `files.ts`'s `readPathAsFile` now derives MIME type from
the real extension via a small lookup table instead of hardcoding
`application/pdf`. `rename.rs` got a new
`split_stem_and_extension_handles_jpeg_scans_too` test (no production-code
change needed there, confirmed by inspection - the function already splits
generically on the last `.`).

Beyond the plan's listed bullets: while adding a JPEG case to
`BatchItemRow.test.tsx`, found that `lib/format.ts`'s
`describeExtractionSource` would have shown the literal string 'PDF text +
AI (OCR)' for a JPEG-sourced run (the `extraction_source: 'model'` label was
hardcoded assuming PDF was the only non-XML source) - a real, user-visible
misleading string, not a hypothetical. Fixed by generalizing the label to
'Document text + AI'; the one existing test asserting the old string was
updated, and no other display code referenced it.

Verified: TypeScript (`tsc -b`) and `eslint .` both clean via
`node_modules/.bin/` directly. `npm run test` (vitest) could not run in
this sandbox - same pre-existing issue as the ZUGFeRD plan's Block 5
(`app/node_modules` synced to the developer's Mac over virtiofs, missing
the Linux-only `@rollup/rollup-linux-arm64-gnu` optional dependency; fixing
it needs a locally-destructive `node_modules` write this session's policy
correctly denies). `cargo test --lib` also could not run: `build.rs`
refuses to compile because the staged worker sidecar under `resources/`
was built for `aarch64-apple-darwin`, not this sandbox's Linux target (a
deliberate build-time guard, not a bug) - confirmed no repo files were
touched by the failed attempt (used an isolated `CARGO_TARGET_DIR`).
**The developer needs to run `npm run test` and `cargo test --lib` (from
`src-tauri/`) on their own Mac to confirm the new frontend/Rust cases
actually pass**, and rebuild the PyInstaller worker sidecar before their
next `cargo tauri dev`/`cargo tauri build`, since Blocks 1-3 changed
`backend/src/invoice_renamer/**` and sidecar rebuilds are never automatic.

## Block 5: Happy path end-to-end and regression verification

- Add a synthetic JPEG invoice fixture (rendered invoice text baked into a
  JPEG image, analogous to `scripts/generate_fixture_pdfs.py`'s PDF
  fixtures) with known ground-truth field values.
- Submit it through the full HTTP analysis API, poll to completion, and
  assert the exact OCR'd extraction, a `.jpg`-suffixed filename, and
  `RunMetrics` (`extraction_source="model"`, `xml_status="none"`,
  `pages_total=1`).
- Exercise the frontend import -> analyze -> review -> rename flow manually
  with a real JPEG, including a batch mixing one PDF and one JPEG in the
  same run, and confirm the existing review/edit/approve/rename-on-disk
  flow works unchanged for both.
- Confirm the full existing PDF-only regression suite (backend pytest,
  frontend vitest) still passes unchanged.

Acceptance: the API-to-UI happy path proves a JPEG scan can be analyzed,
reviewed, and renamed correctly, without regressing any PDF behavior.

### Block 5 accepted, 2026-09-21

Most of this block's automated coverage already existed from Blocks 1-2's
own tests (`scanned_invoice.jpg` as the synthetic ground-truth fixture;
`test_valid_jpeg_upload_is_accepted_and_completes` already submits through
the full HTTP API, polls to completion, and checks the `.jpg` filename and
`RunMetrics`). Two real gaps remained and were filled:

- **"Exact OCR'd extraction" wasn't actually proven** - every existing test
  used a scripted fake model response, which proves the wiring but not that
  real OCR text reaches the model. Added
  `test_pipeline.py::test_jpeg_ocr_text_actually_reaches_the_model_prompt`,
  asserting the fixture's real OCR'd text (`Invoice #4004`, `Global
  Traders`, `275.00 EUR`) appears verbatim in the captured model prompt.
- **No test exercised a PDF and a JPEG through the same coordinator** -
  added `test_analyses.py::test_a_pdf_job_and_a_jpeg_job_complete_correctly_through_the_same_coordinator`,
  submitting one of each to the single background worker thread and
  confirming neither job's format leaks into the other's result.
  Also strengthened the existing JPEG happy-path test with
  `xml_attachment_name`/`xml_profile_id`/`xml_fields_used` assertions at
  the public API-contract level (previously only checked at the internal
  pipeline level).

Manual frontend verification: the developer reported `npm run test` and
`cargo test --lib` passing on their own Mac, plus a live test importing and
renaming a batch of `.jpg` and `.jpeg` files successfully. **Not
explicitly confirmed: a single batch mixing one PDF and one JPEG together**
(the plan's own third bullet) - the developer's reports covered
same-format batches; worth one more manual check before treating this
block as fully closed, though the new automated coordinator test above
covers the equivalent backend behavior.

Full backend suite: 464 passed, 2 skipped, `ruff format`/`ruff check`/
`mypy src` all clean (one run hit a pre-existing, unrelated timing flake in
`test_server.py::test_app_creation_does_not_import_the_inference_stack` -
a subprocess-based test with a tight 5-second budget - confirmed
reproducible only under this sandbox's heavier-than-usual load today, not
caused by anything in this block; a clean re-run passed everything).

## Block 6: Security, sanity, and safety review

- Treat JPEG bytes as untrusted input: enforce the pixel-dimension bound
  from Block 1 before any full decode, so a small file can't force a huge
  in-memory allocation (decompression-bomb style).
- Corrupt, truncated, zero-byte, and polyglot inputs (bytes that could
  plausibly match both formats' magic, or neither) must be rejected
  deterministically and never misrouted into the wrong format's pipeline.
- Confirm OCR'd text from a JPEG passes through the same filename
  sanitizer/UI-text rendering as PDF-derived OCR text - no new
  injection/rendering surface from this format.
- Confirm a JPEG run's `RunMetrics` never reports `xml_status`/
  `extraction_source` values implying XML was considered or used.
- Run narrow tests per block, then full backend `pytest`/`ruff`/`mypy` and
  frontend `lint`/`test`/`build`. Smoke-test the packaged worker sidecar,
  since backend document-handling code changed (sidecar rebuild is not
  automatic after backend edits - see project convention).

Acceptance: hostile or malformed image uploads have bounded, visible
outcomes; PDF behavior is provably unchanged; all verification commands
pass.

### Block 6 accepted, 2026-09-21

Unlike the ZUGFeRD plan's Block 6, this review found no new bugs to fix -
every bullet's underlying guarantee already held by construction from
Blocks 1-3 and the EXIF/CMYK follow-up fixes; this block added tests that
prove each guarantee directly rather than just asserting behavior that
happened to work:

- **Dimension bound before full decode**: Block 1's own test only checked
  that oversized input raised `ValueError`, which would pass even if a full
  decode happened first. Added
  `test_image_open.py::test_oversized_dimensions_are_rejected_before_a_full_decode`,
  which patches `Image.Image.load` to record calls and asserts it was never
  invoked. (First attempt was itself flaky: the test's own JPEG-construction
  helper calls `Image.save()`, which calls `.load()` internally - building
  the test fixture *after* patching miscounted that as a call from
  `open_validated_jpeg`. Fixed by building the fixture bytes before
  patching.)
- **Polyglot inputs never misroute**: by construction, `%PDF-` and
  `\xff\xd8\xff` are mutually exclusive as byte-string prefixes, so true
  ambiguity is impossible - but nothing had tested the realistic polyglot
  case (format bytes appearing *later* in a file of the other format, e.g.
  a real scanned PDF's own embedded JPEG page image). Added
  `test_format.py::test_pdf_containing_embedded_jpeg_bytes_is_still_classified_as_pdf`
  and `test_jpeg_containing_pdf_looking_bytes_is_still_classified_as_jpeg`.
- **Shared sanitizer, no new injection surface**: sanitization in
  `naming/builder.py` has no format-specific branch - `document_format`
  only ever picks the trailing extension - so this held by construction.
  Added `test_builder.py::test_jpeg_source_text_is_sanitized_exactly_like_pdf_source_text`
  to prove it directly, feeding path-traversal/shell/script-like text
  through the JPEG code path and confirming the sanitizer's whitelist still
  applies.
- **RunMetrics never fabricates XML involvement for a JPEG run**: already
  covered by Block 5's strengthened API-contract test
  (`xml_attachment_name`/`xml_profile_id`/`xml_fields_used` all
  null/empty) and Block 1's crash test (a failed JPEG never produces a
  result object at all, so there's no synthesized metrics to mislabel).
- **Verification commands**: full backend `pytest` (464 passed, 2 skipped),
  `ruff format --check`, `ruff check`, and `mypy src` all clean. Frontend
  `tsc -b`/`eslint .` clean in-sandbox; the developer separately confirmed
  `npm run test` and `cargo test --lib` pass on their own Mac. `npm run
  build` (the `vite build` step specifically) was not run by either this
  session (blocked by the same missing `@rollup/rollup-linux-arm64-gnu`
  sandbox issue as `vitest`) or confirmed by the developer - still open.
  Worker sidecar smoke test: not re-run explicitly, but the developer's own
  report of successfully live-testing `.jpg`/`.jpeg` batches through the
  real app implies their sidecar was already rebuilt and working against
  this backend code.

Two items remain open, not because anything failed but because they were
never explicitly exercised: **`npm run build`** and **a single batch mixing
one PDF and one JPEG together** (Block 5's third bullet - see that
block's note).

## Implementation bookkeeping

Keep implementation changes focused and give new code files short purpose
comments/docstrings. Once implementation starts, update `CHANGES.md` using
the repository version convention. This planning-only change needs no
changelog entry.
