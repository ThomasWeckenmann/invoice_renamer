# ZUGFeRD / Factur-X extraction plan

Status: All 6 blocks complete and verified: backend pytest (332 passed)/ruff/mypy
strict, frontend eslint/tsc in-sandbox, and frontend vitest (66 passed, 11 files)
plus a manual `cargo tauri dev` run confirmed working by the developer on their
own machine. Block 6 has been corrected three times now after the developer's
own manual testing found real gaps past this session's own tests (7, then 3,
then 1) - see that block's dated notes for the full lists and fixes. Given that
history, do not treat this as fully hardened without another independent pass;
not yet moved to docs/plans_done/.

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

### Block 1 accepted, 2026-09-19

Implemented as `documents/pdf_open.py` (shared page-count validation, split out
of `documents/reader.py`) and `documents/xml_attachments.py`
(`discover_invoice_xml`). Supported format matrix recorded in that module's
docstring and verified directly against `fixtures/ZUGFeRD-Example.pdf`'s real
embedded `factur-x.xml`: CII D16B root
(`{urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100}CrossIndustryInvoice`),
EN16931/COMFORT profile only (`urn:cen.eu:en16931:2017`). Other real, recognized
profiles (MINIMUM, BASIC WL, BASIC, EXTENDED, XRechnung CIUS) classify
`unsupported`, not `supported`. States NONE/SUPPORTED/UNSUPPORTED/INVALID/AMBIGUOUS
are distinguished with a single concise, content-free warning string. Identical
attachments under different known names dedupe by content hash; distinct XML
under two different known names is AMBIGUOUS, not first-wins. Attachment bytes
are preserved raw and handed to the XML parser undecoded; the decompression
limit `pypdf` can't be bounded ahead of time is documented in code where it
happens. Synthetic fixtures added for every state (`with_zugferd_xml*.pdf`) via
`scripts/generate_fixture_pdfs.py`; the user's real example stays local-only
(`fixtures/ZUGFeRD-Example.pdf`, not referenced by any automated test).
Verified: `backend/tests/documents/test_xml_attachments.py`,
`backend/tests/documents/test_reader.py`.

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

### Block 2 accepted, 2026-09-19

Implemented as `extraction/xml_adapter.py` (`extract_invoice_from_xml`), using
namespace-qualified `rsm:`/`ram:`/`udt:` paths only. Maps issue date (format=102,
CCYYMMDD only), seller name, gross total, and currency; gross total is read from
`SpecifiedTradeSettlementHeaderMonetarySummation/GrandTotalAmount` (tax-inclusive),
never `DuePayableAmount` - covered by a test where a prepayment makes the two
differ. Each field is validated independently (regex-gated `Decimal` parsing,
ISO-4217 validation, the same nonnegative-amount rule `InvoiceExtraction` already
enforces for credit notes) so one bad field can't discard the others; rejected
fields get a short, field-named warning and fall back to the model rather than
raising. Product summary uses the single-line-item case directly, and a
'top line total >= 2x runner-up' dominance rule for multiple items (matches the
real example: Project management 1000 vs Consulting 200); two comparable amounts
correctly leave it for model fallback. `Evidence.xml_field` is populated with the
exact XPath for every accepted field, never for a rejected or model-sourced one.
Verified: `backend/tests/extraction/test_xml_adapter.py`, plus a live run against
`fixtures/ZUGFeRD-Example.pdf` confirming the exact expected values end to end.

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

### Block 3 accepted, 2026-09-19

Implemented as `analysis/extraction_router.py` (`route_xml`, `xml_supplies_filename`,
`merge_xml_and_model`), used by both `analysis/pipeline.py` and, opt-in, the
benchmark. `pipeline.run_document_analysis` now takes a `model_factory` callable
instead of a loaded model and only calls it once XML is confirmed incomplete;
`AnalysisCoordinator._worker_loop` in `api/analyses_routes.py` builds that factory
lazily and only calls `ModelRuntime.mark_warmed()` when `RunMetrics.inference_ran`
is true. `evaluation/benchmark.py::run_invoice`/`run_benchmark` gained an explicit
`use_xml` parameter (default `False`, unchanged prior behavior = text-only mode,
matching the plan's 'retain a named text-only mode' option); `--use-xml` added to
`scripts/benchmark_models.py`. Merge always lets a valid XML value win over a
conflicting model value, per field. Verified: `backend/tests/analysis/test_pipeline.py`
(complete XML with a model spy that asserts it's never called, partial XML with a
model scripted to conflict, invalid/ambiguous-XML fallback, fallback model
failure preserving XML fields), and the full existing `backend/tests/api/test_analyses.py`
coordinator suite (queue/cancel/memory-preflight/model-switching) unchanged.
Backend `pytest`, `ruff format --check`, `ruff check`, and `mypy src` (strict) all
pass with zero errors.

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

### Block 4 accepted, 2026-09-19

`RunMetrics` gained `xml_ms`, `extraction_source`, `xml_status`,
`xml_attachment_name`, `xml_profile_id`, `xml_fields_used`, `inference_ran`, all
with safe historical defaults (`xml_ms=0`, `extraction_source="model"`,
`xml_status="none"`, `inference_ran=True`), mirrored by hand in
`app/src/lib/api/types.ts` per this repo's existing no-codegen convention.
`BatchItemRow.tsx` now shows a `Source` row (`describeExtractionSource` in
`lib/format.ts`: `ZUGFeRD / Factur-X XML` / `XML + AI` / `PDF text + AI`, `(OCR)`
suffix when any page needed it), a conditional `XML` row for detected-but-rejected
cases (`describeXmlDetection`, naming the unsupported profile/malformed/ambiguous
reason), `Not used` for the Model row when `inference_ran` is false, and a small
per-field `XML` badge (from `extraction.evidence[field].xml_field`) so mixed
XML+model results are reviewable field by field.
Verified: `backend/tests/metrics/test_models.py`,
`app/src/features/batch/components/BatchItemRow.test.tsx` (new source/XML-row/
badge/`Not used` cases; existing cases updated for the new required fields).

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

### Block 5 accepted, 2026-09-19

Done and verified: `with_zugferd_xml.pdf` is exactly the described hybrid fixture
(complete supported XML: Beispiel GmbH / Cloud Hosting / 595.00 EUR / 2026-01-15;
its own visible page text says Apple / MacBook Air / 2180.00 EUR). New tests in
`backend/tests/api/test_analyses.py` submit it through the full HTTP API with a
fake installed-model environment (no real weights), poll to completion, and
assert the exact XML-derived filename/fields/evidence/metrics and zero model
loads; a companion test does the same for `with_zugferd_xml_partial.pdf` with a
model scripted to conflict on every XML-supplied field, proving XML wins field by
field; another covers malformed-XML fallback; another submits one XML job and one
ordinary job through the same coordinator and confirms only the ordinary one
touches the model loader. The real `fixtures/ZUGFeRD-Example.pdf` was run locally
through the actual extraction/mapping code (not just detected) and its fields
matched exactly: `2021-09-24 / Webware Internet Solutions GmbH / Project
management / 1428.00 EUR` - no product-summary fallback was necessary, since one
line item (Project management, 1000/1200 of the pre-tax total) clearly dominates
the other (Consulting, 200/1200) under the >=2x rule.

This session's own sandbox couldn't run frontend `vitest` (`app/node_modules` is
synced to the developer's real Mac over virtiofs and was missing the Linux-only
`@rollup/rollup-linux-arm64-gnu` optional dependency; fixing it needs a local
`node_modules` write, which this session's permission policy correctly denied as
locally destructive - see `project_npm_node_modules_shared_virtiofs`) or build/run
the Tauri app (same virtiofs risk for `src-tauri/target/`, per
`project_cargo_available_in_sandbox`). What this sandbox verified instead:
`./node_modules/.bin/eslint .` and `./node_modules/.bin/tsc --noEmit` both clean
against every changed frontend file. The developer then ran both remaining steps
on their own Mac and confirmed: `npm run test` - 66 passed across 11 files,
including every new `BatchItemRow.test.tsx` case; and `./scripts/build_worker_sidecar.sh`
followed by `cargo tauri dev` (run from the repo root, not `app/` - `src-tauri/`
is a sibling of `app/`, not nested under it) - working as expected end to end.

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

### Block 6 first accepted 2026-09-19, then corrected the same day after the developer's own testing found it insufficient

The first pass of this block was marked accepted based on this session's own
security tests (XXE, billion-laughs, oversized/malformed payloads, path-traversal
attachment names) - all of which passed, but which only exercised payloads with
`<!ENTITY>` declarations or obviously-corrupt bytes. The developer then manually
reproduced 7 further issues this pass had missed entirely, including a wrong
claim in this file's own text that DTDs were forbidden by default. Each is listed
with what was actually wrong and the fix, verified both by direct reproduction
and by new regression tests:

1. **Same-name attachment revisions were never compared.** pypdf groups two
   attachments that share one exact name into one dict entry as a list of
   revisions; `discover_invoice_xml` only ever inspected `revisions[0]`, so two
   genuinely different invoices both named `factur-x.xml` resolved to whichever
   came first with no ambiguity warning. Fixed: all revisions (bounded by a new
   `_MAX_REVISIONS_PER_NAME` cap) now feed the same content-hash ambiguity check
   already used across different attachment names. New fixture
   `with_zugferd_xml_same_name_conflict.pdf`;
   `test_same_attachment_name_with_conflicting_revisions_is_ambiguous`.
2. **Conflicting header values were resolved by `find()` with no uniqueness
   check.** Two different `GrandTotalAmount` values under duplicated
   `SpecifiedTradeSettlementHeaderMonetarySummation` blocks silently produced
   the first one, no warning - same gap for date, seller, and currency. Fixed:
   `_find_unique()` now uses `findall()` and rejects (with a field-named warning)
   any path matching more than once, since the CII schema allows at most one.
   `test_conflicting_grand_totals_are_rejected_not_first_wins`,
   `test_conflicting_seller_names_are_rejected`.
3. **A fallback crash discarded already-valid XML fields.** If `model_factory()`
   or the model itself raised (not just returned bad output, which
   `extract_invoice` already handled), the exception propagated uncaught and the
   whole job failed - losing partial XML fields the plan explicitly said to
   preserve. Fixed: the PDF/OCR/model fallback in `pipeline.py` is now wrapped in
   a `try`/`except` that, when XML produced anything, returns those fields with a
   `model fallback failed: ...` warning instead of failing the job; when XML
   produced nothing, it still re-raises exactly as before.
   `test_model_load_crash_preserves_partial_xml_instead_of_failing_the_job`,
   `test_fallback_crash_with_no_xml_at_all_still_fails_the_job`.
4. **An unrecognized XML declaration encoding crashed with an uncaught
   `LookupError`**, not `ParseError`/`DefusedXmlException`, so it wasn't caught
   and failed the job instead of falling back. Fixed: parsing now catches any
   `Exception` (untrusted external XML can fail in more ways than the two
   "expected" types) and treats all of them as INVALID.
   `test_unknown_xml_declaration_encoding_is_rejected_not_crashed`.
5. **Product-dominance selection silently dropped line items with no usable
   amount** instead of letting an unresolvable competitor block a confident
   pick - three items priced 500/10/unknown chose "500" with no warning, since
   the unknown one was filtered out before the dominance comparison ran. Fixed:
   any multi-item invoice where even one item has no usable name or amount now
   blocks dominance entirely (a single-item invoice still needs no amount at
   all, since there's nothing to compare it against).
   `test_line_item_with_no_usable_amount_blocks_dominance`,
   `test_single_line_item_needs_no_amount_at_all` (regression guard for the
   single-item case).
6. **No magnitude bound on amounts.** A 40-digit total passed XML validation,
   then crashed filename generation in `naming/builder.py`'s `_round_amount()`
   with `decimal.InvalidOperation` (exceeding Decimal's default 28-digit context
   precision). Fixed: `_parse_decimal()` now rejects anything over
   `_MAX_AMOUNT` (18 nines - far beyond any real invoice, safely under that
   precision ceiling). `test_implausibly_large_amount_is_rejected_not_crashed_later`.
7. **The original DTD claim was wrong, and depth/field-length limits were
   absent.** `defusedxml.ElementTree.fromstring`'s actual defaults are
   `forbid_dtd=False, forbid_entities=True, forbid_external=True` - a bare
   `<!DOCTYPE ... SYSTEM "...">` with no `<!ENTITY>` parsed and classified
   SUPPORTED, confirmed directly. A deeply-nested payload well under the 10 MB
   byte cap also parsed fully (490 MB peak RSS, 2.5s at the cap boundary - not a
   crash, but a real, avoidable cost). Fixed: parsing now uses
   `defusedxml.ElementTree.iterparse(..., forbid_dtd=True)` with incremental
   depth tracking, rejecting anything over `_MAX_XML_DEPTH` (50 - real CII
   invoices nest single digits deep) in milliseconds instead of fully parsing
   it; a new `_MAX_FIELD_TEXT_LENGTH` (500 chars) bound rejects implausibly long
   field text the same way. `test_bare_doctype_with_no_entities_is_still_rejected`,
   `test_deeply_nested_xml_under_the_byte_cap_is_rejected_fast`,
   `test_implausibly_long_seller_name_is_rejected`.

Two claims from the first pass needed correcting as a result: "a hard crash
anywhere in the pipeline fails the whole job" is no longer true by design (see
#3 above - that's now the intended behavior for a fallback crash with valid XML
present), and the DTD claim in point 7. Everything else from the first pass
(no filesystem paths built from attachment names/XML, warnings kept structural
except a rejected field's own raw text - shown only to the invoice's own
uploader, sanitizer pass-through, `extraction_source` only claiming XML when
`xml_fields_used` is non-empty, scope limited to one CII/EN16931 profile with no
conformance-validation claim) still holds and was re-verified after these fixes.
Backend `pytest` (325 passed, 2 pre-existing platform-gated skips),
`ruff format --check`, `ruff check`, and `mypy src` (strict) all pass with zero
findings after the fixes. Frontend `eslint`/`tsc --noEmit` pass (unaffected -
these fixes are backend-only); frontend `vitest` (66 passed) and a manual
`cargo tauri dev` run were confirmed working by the developer on their own
machine (see Block 5).

### Block 6 corrected a second time, 2026-09-19, after the developer reproduced 3 more gaps in the fixes above

1. **The revision/name caps hid conflicts instead of rejecting on them.** Fix #1
   above added `_MAX_REVISIONS_PER_NAME`/checked `_MAX_KNOWN_NAMES_CHECKED`, but
   both silently truncated to the first N candidates *before* the ambiguity
   check ran, so a conflicting attachment past the cutoff was never even looked
   at - five identical revisions followed by a genuinely different sixth still
   classified SUPPORTED. Fixed: exceeding either cap now rejects immediately
   (AMBIGUOUS, since exceeding a count cap means uniqueness can no longer be
   proven) instead of proceeding on a truncated view.
   `test_revisions_beyond_the_cap_reject_rather_than_silently_truncate`,
   `test_revisions_exactly_at_the_cap_still_resolve_supported` (regression
   guard), `test_too_many_distinctly_named_attachments_reject_rather_than_truncate`.
2. **Fallback-crash recovery (fix #3 above) reported fabricated execution
   details.** `_xml_only_result` unconditionally zeroed `pdf_extraction_ms`,
   `ocr_ms`, `inference_ms`, and `pages_ocr` even when real OCR/read work had
   already completed before the crash, and unconditionally labeled
   `extraction_source` as `xml` even when XML had contributed zero fields (the
   `xml_extraction is None` re-raise check didn't cover "XML detected but every
   field rejected"). Fixed: `pipeline.py` now tracks real timing/page values
   through the try block (referencing whatever was last actually computed, not
   resetting to zero in `except`), and re-raises whenever `fields_from_xml` is
   empty rather than only when `xml_extraction is None`. New fixture
   `with_zugferd_xml_partial_scanned.pdf` (partial XML on a page needing real
   OCR) proves genuine `ocr_ms`/`pages_ocr` survive a crash;
   `test_xml_detected_with_zero_usable_fields_plus_crash_still_fails_the_job`
   proves the zero-fields case now fails the job instead of fabricating an XML
   result.
3. **Amount fields bypassed the text-length limit.** Every other field already
   went through `_bounded()`'s length check before parsing, but `_extract_gross_total`
   read the header amount straight from `_text()`, and line-item amounts in
   `_read_line_item` never had a length check at all - a 100,000-digit amount
   produced a 100,064-character warning that echoed the whole value. Fixed:
   `_parse_decimal()` (shared by both call sites) now rejects text over a new
   `_MAX_AMOUNT_TEXT_LENGTH` (32 chars - `_MAX_AMOUNT`'s 18 digits plus
   headroom) before any regex matching or `Decimal` construction, and every
   warning that echoes rejected XML text (amounts, dates, currency codes, and
   the `format` attribute) now goes through a new `_truncate_for_warning()`
   (50-char cap) regardless of source.
   `test_pathologically_long_amount_produces_a_short_warning_not_an_echo`.

Verified: backend `pytest` (331 passed, 2 pre-existing platform-gated skips),
`ruff format --check`, `ruff check`, and `mypy src` (strict) all pass with zero
findings after these fixes too; the real `fixtures/ZUGFeRD-Example.pdf` result
re-checked and unchanged.

**Addendum, same day:** the developer found one more gap in fix #2 immediately
above - `_xml_only_result` still hardcoded `inference_ran=False` unconditionally,
so a crash inside `extract_invoice`/`model.generate()` itself (as opposed to a
crash in `model_factory()`, which never reaches `generate()` at all) reported a
real, nonzero `inference_ms` (confirmed: 52ms) right next to `inference_ran:
False` - internally contradictory metrics, the same class of bug as fix #2, just
one level deeper. Fixed: `inference_ran` is now an explicit parameter of
`_xml_only_result`, set to `inference_start is not None` in the crash-recovery
branch (true whenever `generate()` was actually invoked, regardless of whether
it then raised) and `False` in the complete-XML fast path (which never attempts
inference at all). `test_generate_crash_reports_inference_ran_true_not_a_contradiction`
locks this in; `test_model_load_crash_preserves_partial_xml_instead_of_failing_the_job`
(a `model_factory()` crash, before `generate()` is ever reached) continues to
assert `inference_ran is False`, confirming the two crash sites are still told
apart correctly. Backend `pytest` (332 passed), `ruff`, and `mypy src` all still
pass with zero findings.

**Known remaining asymmetry, not fixed here:** `evaluation/benchmark.py`'s
`use_xml=True` mode shares the same `route_xml`/`merge_xml_and_model` functions
as the app pipeline but was not given the same fix #3 (its own crash handling
via `_crashed_invoice_result` still discards partial XML on a fallback crash).
Left alone deliberately: for benchmark scoring, a fallback crash should probably
still count as a failure rather than silently succeed on XML alone, which is the
opposite intent from the app's UX fix - flagged for the developer to decide
rather than assumed.

## Implementation bookkeeping

Keep implementation changes focused and give new code files short purpose
comments/docstrings. Once implementation starts, update `CHANGES.md` using the
repository version convention. This planning-only change needs no changelog entry.
