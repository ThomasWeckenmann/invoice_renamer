# ZUGFeRD / Factur-X extraction plan

Status: Proposed; implementation has not started.

## Goal

Use supported embedded invoice XML as the authoritative source for invoice
fields, rather than merely detecting it. Fall back to PDF text and the local
model when necessary, and show the actual extraction source in Run details.
The filename and its evidence must demonstrate that XML values were used.

## Current evidence

- `fixtures/ZUGFeRD-Example.pdf` contains `factur-x.xml`, with a
  `CrossIndustryInvoice` root and an EN16931 profile identifier. The embedded
  XML was inspected directly; the installed application was not exercised.
- `documents/reader.py` recognizes selected attachment names and returns an
  `embedded_xml` string, but reads page text and performs OCR first.
- `inference/prompts.py` uses only page text. Nothing currently consumes
  `embedded_xml` to populate invoice fields.
- `InvoiceExtraction` already has field evidence with an `xml_field` member.
  The filename builder already sanitizes seller and product text.
- `RunMetrics` and `BatchItemRow.tsx` have no XML status or extraction source.
- The analysis coordinator requires an installed model at submission, loads
  it before reading the document, and assumes every completed run warms it.
- The benchmark has its own read/extract path; changing the application alone
  would leave evaluation behavior different.

## Behavior decisions

- Parse supported XML deterministically; do not send raw XML to the model.
- Valid XML fields win. The model may fill missing or rejected fields but
  must never overwrite valid XML values, even when page text disagrees.
- Skip inference when XML supplies all filename fields. Unknown language alone
  does not trigger inference. A complete XML invoice must also skip OCR.
- Missing XML follows the existing PDF path. Malformed, unsupported, ambiguous,
  or unsafe XML produces a concise warning and falls back to PDF extraction.
- Preserve valid partial XML results if fallback fails; show remaining missing
  fields and the fallback warning for review rather than losing those results.
- Support the CII structure present in the supplied example first. Before
  implementing mappings, verify namespace/profile/date/amount semantics against
  the relevant official specifications and record the supported matrix here.
  Do not claim all ZUGFeRD versions or profiles are supported. Legacy formats,
  UBL, standalone XML imports, and formal standards conformance validation are
  outside this first implementation.
- Retain the current model-selection and installed-model submission prerequisite
  for this change. Defer actual model loading until fallback needs it. Allowing
  analysis without any installed model is a separate workflow change.

## Block 1: Discover and classify embedded invoice XML

- Separate attachment inspection and PDF page counting from page text/OCR work
  so XML can be evaluated first without bypassing existing PDF validity limits.
- Use a focused document module for attachment metadata and classification.
  Preserve attachment bytes for XML encoding-aware parsing; avoid lossy UTF-8
  replacement before parsing.
- Retain current case-insensitive known names, verify names needed by the
  supported format matrix, and inspect root namespace/profile before accepting
  a candidate. A recognized filename alone does not prove a supported invoice.
- Distinguish no candidate, supported candidate, unsupported candidate, and
  invalid candidate. Deduplicate identical attachments; multiple distinct invoice
  candidates are ambiguous and must not silently select the first.
- Bound attachment enumeration and decoding. Define explicit size/count limits
  and enforce them as early as the PDF library permits; document any decompression
  limit that cannot be enforced before allocation.
- Add synthetic fixtures for supported XML, no XML, malformed XML, wrong root,
  unsupported profile, duplicate attachments, and distinct multiple invoices.
  Use the user's example locally; do not make automated tests depend on adding
  a potentially private or externally licensed PDF to version control.

Acceptance: candidate identity, bytes, and classification are testable without
running OCR or a model; ordinary PDFs retain their existing behavior.

## Block 2: Map XML into validated invoice fields

- Add a focused extraction adapter returning validated fields, field evidence,
  warnings, and supported-format metadata. Use namespace-qualified paths rather
  than broad searches by local tag name.
- Map invoice issue date, seller name, invoice gross total, and invoice currency.
  Use the invoice grand total including tax, not net total or outstanding balance;
  test prepaid invoices where grand total and balance due differ. Do not silently
  alter the existing nonnegative-amount policy for credit notes.
- Parse supported dates explicitly and money directly into `Decimal`. Validate
  each field independently so one bad value does not discard other valid values.
  Reject non-finite amounts, ambiguous header values, and invalid currency codes.
- Derive a short product summary from an unambiguous single line item. For multiple
  items, use a clearly dominant item only under an explicit, tested rule using
  comparable line amounts; otherwise leave the summary for model fallback.
- Preserve original seller/product text in extraction; reuse existing filename
  normalization. Leave language unknown unless a supported explicit source exists.
- Populate `Evidence.xml_field` for every accepted XML value. Attach bounded,
  actionable warnings to rejected fields; never present model values as XML evidence.

Acceptance: unit tests assert exact values and XML paths, including conflicting
seller/buyer names, issue/due dates, net/gross/due amounts, and partial invoices.

## Block 3: Integrate XML-first analysis and fallback

- Keep routing and field merging in a focused analysis service shared by the
  application and relevant evaluation path. Keep the language-model adapter
  responsible for text extraction, not XML parsing or routing.
- Inspect and parse XML first. If filename fields are complete, build the proposal
  without extracting page text, rendering pages, running OCR, or loading a model.
- Otherwise run the existing PDF text/OCR and model path, merging only missing
  usable values. Preserve field provenance and both XML and fallback warnings.
- Make model acquisition lazy in the coordinator. Only mark the runtime warmed
  after inference actually runs; retain model cleanup, queue, cancellation, and
  memory-preflight behavior. Avoid displaying model-memory warnings for a completed
  XML-only run or treating its selected model as an executed model.
- Keep model-selection benchmarks explicit: either retain a named text-only mode
  or add an extraction-mode setting. XML-only results must not silently inflate
  model accuracy scores or appear as model inference performance.
- Add focused routing tests with OCR and model spies, including complete XML,
  partial XML, conflicting fallback values, invalid XML, ordinary PDFs, and
  fallback failure. Verify zero model loads and zero OCR calls for complete XML.

Acceptance: XML changes the resulting extraction and filename, valid XML values
survive conflicting model output, and ordinary PDF analysis still works.

## Block 4: Expose the actual source in contracts and Run details

- Extend backend metrics and frontend API types together with extraction source
  (`xml`, `xml_and_model`, `model`), XML status, attachment/format metadata,
  accepted XML field names, and whether inference actually ran. Distinguish
  detected XML from XML that contributed values, and unavailable fallback from
  successful model extraction.
- Record XML processing time separately and include it in total processing time
  without double-counting PDF reading or inference. Keep total page count accurate
  even when pages are not read; XML-only runs have no OCR pages and zero inference
  time. Preserve existing timing boundaries and document model-load exclusions.
- Show human-readable labels such as `ZUGFeRD / Factur-X XML`, `XML + AI`, and
  `PDF text + AI`, with OCR usage where applicable. For rejected XML, show the
  fallback reason. Show which fields came from XML so mixed results are reviewable.
- Distinguish the selected model from actual model execution; show `Not used`
  for XML-only runs. Do not invent token counts or display a selected model as
  the source of an XML-only result.
- Provide safe defaults for older metrics without falsely classifying historical
  runs as having used XML. Update API, metrics, evaluation serialization, and
  component tests, including missing metadata.

Acceptance: users can tell whether XML was detected, used fully or partly, or
rejected, and whether a model/OCR actually ran.

## Block 5: Happy path end-to-end and regression verification

- Add a synthetic hybrid PDF with complete supported XML and deliberately different
  visible PDF values. Submit it through the analysis API, poll to completion, and
  assert exact XML-derived fields, evidence, sanitized filename, source metrics,
  and zero model loads/inference/OCR calls. Use a fake installed-model environment
  to satisfy the retained submission prerequisite without downloading weights.
- Cover the frontend contract through a matching API-response fixture and Run
  details assertions. Manually import the synthetic PDF in the app and verify
  analysis, displayed source, review/edit/approve, and rename of a temporary copy.
- Exercise a partial XML invoice end to end: model fallback fills gaps, deliberately
  conflicting model fields cannot overwrite XML, and Run details shows mixed use.
- Run the supplied `ZUGFeRD-Example.pdf` locally and compare the displayed fields
  against its actual attachment; record whether a product-summary fallback was
  necessary. Do not substitute attachment presence for proof of extraction.
- Verify ordinary text PDFs, scanned PDFs, malformed XML fallback, re-runs,
  cancellation, and a batch mixing XML and model-based jobs.

Acceptance: the API-to-UI happy path proves that embedded XML controls values
and filenames, with truthful execution details and working existing review flows.

## Block 6: Security, sanity, and safety review

- Treat attachment names and XML as untrusted input. Disable DTDs, external
  entities, external resource access, and entity expansion. Add explicit limits
  for XML size, depth, and field lengths, with tests for malicious payloads.
- Test malformed encodings, oversized/decompression-heavy attachments, excessive
  candidate counts, ambiguous invoices, and unsupported namespaces/profiles.
  Recoverable XML problems must not crash an otherwise readable PDF batch.
- Never extract attachments to filesystem paths supplied by the PDF. Keep raw
  invoice XML and sensitive field values out of routine logs and error messages.
- Check that XML strings pass through the existing filename sanitizer and UI text
  rendering, and cannot bypass review, rename collision checks, or path safety.
- Verify partial/failure outcomes never report XML use without accepted XML fields
  or successful model use when inference failed. Document that supported parsing
  is not signature, authenticity, or full invoice conformance validation.
- Run narrow tests per block, then backend pytest, Ruff formatting/lint, and mypy;
  run frontend lint, tests, and build. Smoke-test the packaged worker if parser
  dependencies or bundling change. Record results and remaining limits in this plan.

Acceptance: hostile or unsupported attachments have bounded, visible outcomes,
all relevant checks pass, and no unsupported format is advertised as supported.

## Implementation bookkeeping

Keep implementation changes focused and give new code files short purpose
comments/docstrings. Once implementation starts, update `CHANGES.md` using the
repository version convention. This planning-only change needs no changelog entry.
