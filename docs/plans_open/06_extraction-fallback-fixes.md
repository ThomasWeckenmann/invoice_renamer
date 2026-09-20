# Extraction fallback fixes

Status: Block 1 complete and verified (pytest/ruff/mypy). Blocks 2-5 not started.

## Goal

Fix real gaps found by running the local XML-issue benchmark set
(`local_benchmark_data/xml-issues/`, private, not in git):

1. When XML line items don't have one dominant item, `product_summary` falls
   back to the model instead of combining the top 2 items itself.
2. A long combined (or otherwise long) seller/product value gets silently
   truncated mid-word by the filename-length limit, with no warning and no
   `requires_review` signal - found while verifying fix 1's actual output.
3. When the model's fallback JSON has one bad field (e.g. an invalid
   currency), the whole response is discarded - including fields that
   validated fine - so a single-field model mistake can lose a
   correctly-extracted `product_summary` and mark the run `failed` in the
   benchmark.

## Current evidence

- Review on 2026-09-20 ran the current `route_xml` against both private PDFs.
  `xml-issue-01-unusable-amount-fallback.pdf` actually reports
  `product_summary: multiple XML line items include one with no usable amount; falling back`
  and also has no XML `currency`. It must retain model fallback under this
  plan; its filename does not establish which XML branch it exercises.
- `xml-issue-02-dominance-fallback.pdf` reports
  `product_summary: no single XML line item clearly dominates; falling back`;
  all four other filename fields are supplied by XML. This is the local
  fixture for Block 1. Once combination succeeds within the length bound,
  it no longer exercises extraction fallback, so Block 3 (salvage) needs a
  separate partial-XML fixture that remains incomplete after Block 1.
- The previously observed model fallback for file 02 had a correctly-extracted
  `product_summary` alongside an invalid `currency` value (a 2-letter
  language-style code, not a 3-letter ISO-4217 code). The whole response
  fails `InvoiceExtraction.model_validate` on `currency` alone
  (`extraction/validators.py` via `extraction/models.py:44-46`).
  `inference/extractor.py:_parse` validates the whole object at once, so the
  correctly-extracted `product_summary` is discarded along with `currency`;
  after the one repair retry also fails, `extract_invoice` returns an
  all-null `InvoiceExtraction` whose warning contains
  `model output could not be validated`, which
  `evaluation/benchmark.py:40`'s `_REPAIR_FAILURE_MARKER` matches, marking
  the invoice `failed` in the benchmark even though `merge_xml_and_model`
  (`analysis/extraction_router.py:53`) would have kept the model's
  `currency` out of the result anyway (XML already had it, and a valid XML
  field always wins).
- Confirmed this all-or-nothing validation is a general `extract_invoice`
  gap, not XML-specific: two further pure-model runs (no embedded XML at
  all - `local_benchmark_data/model-validation-errors/model-validation-error_01.pdf`
  and `_02.pdf`, small Qwen model) hit the exact same `currency: 'DE'`
  pattern. There, the all-or-nothing failure is worse: with no XML value to
  fall back on for any field, the resulting all-null `InvoiceExtraction`
  makes the app report *every* field as missing
  (`app/src/features/batch/components/BatchItemRow.tsx:234`,
  `missing_fields.join(", ")`) even though the model actually got
  date/seller/product/amount right and only `currency` was wrong. Block 3's
  (salvage) fix applies unchanged here too - no XML-specific handling needed.
- A third file with no embedded invoice XML at all was already removed from
  the benchmark set by the developer - out of scope, nothing to fix here in
  XML handling.
- Three other files in the same folder
  (`xml-issue-03-unsupported-profile-xrechnung-a.pdf`,
  `xml-issue-04-unsupported-profile-extended.pdf`,
  `xml-issue-05-unsupported-profile-xrechnung-b.pdf`) hit the *unsupported
  CIUS-suffixed profile* discovery gap (profile URNs carrying the base
  `urn:cen.eu:en16931:2017` plus an XRechnung 3.0 or Factur-X Extended
  suffix), a separate, not-yet-scoped issue in `discover_invoice_xml`'s
  exact-match `SUPPORTED_PROFILE_IDS` (`documents/xml_attachments.py:59`).
  Not addressed by this plan.

## Behavior decisions

- Combine, don't guess further: when 2+ XML line items all have a usable
  name and amount but none is at least `_DOMINANCE_RATIO`x the runner-up,
  join the top 2 by amount as `f"{top_name} + {second_name}"` instead of
  falling back to the model. Any item (in or out of the top 2) still
  missing a usable name/amount still blocks this entirely and falls back,
  exactly as today - we can't trust a ranking that has an unresolved
  competitor in it.
- Bound the combined string explicitly; never silently truncate mid-name.
  If the combined text doesn't fit the existing field-length limit, fall
  back to the model with a warning instead of cutting a name in half.
- The filename sanitizer (`naming/builder.py:_normalize_segment`) already
  strips `+` and collapses whitespace to `-` for every product_summary
  regardless of source, so `"Item A + Item B"` already becomes
  `Item-A-Item-B` in the actual filename with no builder changes needed -
  verify this directly rather than assuming it.
- Fix model-response validation to salvage field-by-field: on a
  `ValidationError` from a JSON object, remove only the specific field(s)
  pydantic rejected and re-validate using their schema defaults. Nullable
  invoice fields default to `None`, `language` to `Language.UNKNOWN`, and
  `evidence` to `{}`. Preserve the existing stripping of model-authored
  warnings and short fields before validation. Guard error locations before
  accessing `loc[0]`; non-object JSON and unrecognized/root-level errors
  must use the existing repair path rather than crash.
- Salvage succeeds only when re-validation succeeds and at least one of
  `invoice_date`, `seller`, `product_summary`, `gross_total`, or `currency`
  survives with a non-null value (text must also be nonblank). Language and
  evidence alone do not count. Otherwise retain the existing repair retry
  and terminal failure marker. Apply this usefulness check only after a
  validation failure; already-valid empty responses keep existing behavior.
- Every field dropped by salvage gets a short, bounded warning naming the
  field and reason (same convention as `xml_adapter.py`'s warnings) so it's
  visible in Run details and the benchmark, not silently gone. Merge these
  code-generated warnings with document warnings on successful extraction;
  the current success path overwrites extraction warnings.
- This changes benchmark scoring for responses like the historical file 02
  fallback: a salvageable single-field model mistake no longer trips
  `_REPAIR_FAILURE_MARKER`. That is the intended fix, not a side effect to
  guard against.
- The combined product_summary from Block 1 gets no special-casing for
  shortening: `pipeline.py` already calls `shorten_fields` on
  `extraction.product_summary` unconditionally whenever the user's
  `shorten_enabled` toggle is on, regardless of source (XML or model) -
  confirmed in `inference/shortener.py`. The combined value goes through
  that same path like any other product_summary.

## Block 1: Combine top-2 XML line items when none dominates

- In `extraction/xml_adapter.py:_extract_product_summary`, replace the final
  `return _FieldResult(None, None, "... falling back")` (line 316-318) with
  a combined `top_name + " + " + second_name` result once every item already
  passed the existing name/amount checks and the dominance check failed.
  Reuse `_LINE_ITEMS_PATH` as the `xml_path`, exactly like the single-item
  and dominant-item cases above it.
- Add an explicit max length for the combined string (reuse
  `_MAX_FIELD_TEXT_LENGTH` or a dedicated constant) and fall back to
  `None`/model with a warning if it's exceeded, instead of truncating.
- Update this module's docstring ("Product summary: ...") and any inline
  comments that describe non-dominant items as requiring model fallback.
- Update `backend/tests/extraction/test_xml_adapter.py`'s existing
  "two comparable amounts correctly leave it for model fallback" case (from
  plan 05, Block 2) to assert the new combined value instead; add cases for
  3+ items (only top 2 combined), a combined string over the length cap,
  and confirm the "any item missing name/amount blocks it" behavior is
  unchanged when the unusable item isn't one of the top 2 by amount.
- Verify against the real `xml-issue-02-dominance-fallback.pdf` locally
  (private fixture, not committed) that the combined `product_summary` and
  resulting filename are what's actually expected, not just that a warning
  disappeared.
- Verify file 01 still leaves product and currency for model fallback and
  retains its unusable-amount warning; do not weaken the competitor checks
  to make this file produce a combined XML product.

Acceptance: an XML invoice with 2+ non-dominant line items produces a
combined `product_summary` with XML evidence and no model fallback for that
field; an invoice with an unusable competitor item still falls back exactly
as before.

### Block 1 accepted, 2026-09-20

Implemented in `extraction/xml_adapter.py:_extract_product_summary`: once
every item has a usable name/amount and none dominates, the top 2 by
amount are joined with `_COMBINE_SEPARATOR = " + "` and returned with
`_LINE_ITEMS_PATH` evidence and no warning; exceeding `_MAX_FIELD_TEXT_LENGTH`
on the combined string falls back with a warning instead of truncating.
Module docstring updated to describe the new behavior. Tests in
`tests/extraction/test_xml_adapter.py`: the existing "two comparable items"
case now asserts the combined value; new cases cover 3+ items (only top 2
combined), a combined string over the length cap, out-of-document-order
ranking (items listed lower-amount-first, proving the top-2 pick sorts by
amount rather than trusting XML order), and a low-value third item with no
name (proving the name/amount guard blocks combination for *any* unusable
item, not just ones large enough to contend for the top 2 - the pre-existing
"unusable amount blocks dominance" test only exercised the dominance branch
with a 50x-dominant top item, not this new combination branch). The existing
"unusable amount blocks dominance" and "single item needs no amount" tests
were re-run unchanged and still pass.
Verified against the real local fixtures: `xml-issue-02-dominance-fallback.pdf`
now combines its two line items and needs no model fallback at all
(`warnings: []`); `xml-issue-01-unusable-amount-fallback.pdf` is unaffected,
unchanged from before this block.
Backend `pytest` (367 passed, 2 pre-existing platform skips), `ruff format
--check`, `ruff check`, and `mypy src` (strict) all pass with zero findings.

**Filename verification (not just extraction warnings):** ran
`xml-issue-02-dominance-fallback.pdf`'s extraction through
`build_filename_proposal` directly, since zero XML warnings only proves
extraction succeeded, not that the resulting filename is what a reviewer
would expect. Actual result: the 136-character combined `product_summary`
plus a real date/seller pushes the stem to `naming/builder.py`'s
`_MAX_STEM_LENGTH = 150` cap, and the existing hard-truncation
(`prefix[:max_prefix_length].rstrip("_-")`) cuts the second product name
mid-word (observed: `...Torrey-Pi_265-EUR.pdf`, dropping `ne-Green`), with
no warning and `requires_review: False` - a reviewer would see a plausible-
looking but silently truncated filename with no signal anything was cut.
This is a pre-existing `naming/builder.py` behavior (any single long
product name already truncated the same way before this block), not a new
bug introduced here, but Block 1 makes it far more likely to trigger, since
two real product names combined regularly exceed what a single name would.
**Flagged, not fixed as part of this block** - `naming/builder.py`'s
truncation is unrelated to `xml_adapter.py`'s scope, and fixing it (e.g.
flagging `requires_review` whenever the stem is actually truncated,
regardless of source) is a general naming-builder change the developer
should confirm before it's made, not something to fold in silently here.

The two local fixtures were renamed after this block landed
(`xml-issue-01-dominance-fallback.pdf` -> `xml-issue-01-unusable-amount-fallback.pdf`,
`xml-issue-02-model-validation-salvage.pdf` -> `xml-issue-02-dominance-fallback.pdf`)
so their names match what they actually exercise; every reference in this
plan uses the current names.

## Block 2: Flag filename truncation instead of silently cutting a name in half

Found during Block 1's own local verification (see above): `naming/builder.py`
already hard-truncates an over-length date/seller/product prefix to fit
`_MAX_STEM_LENGTH = 150` with no warning and no `requires_review` signal.
This predates Block 1 (any single long product name already triggered it),
but Block 1's combined product names make it far more likely, so it's fixed
here as its own block rather than silently folded into Block 1's scope.

- Add `warnings: list[str] = Field(default_factory=list)` to
  `FilenameProposal` (`naming/schema.py`), distinct from
  `extraction.warnings` - mirrors that schema's own existing comment about
  `missing_fields` being kept distinct "so the UI can explain why
  requires_review is true."
- In `naming/builder.py:build_filename_proposal`, detect when
  `if len(prefix) > max_prefix_length` actually fires and append one
  bounded, factual warning to the new list (name the field(s) likely
  truncated and the character limit; never echo the full pre-truncation
  text - it can be arbitrarily long). Set
  `requires_review = bool(missing_fields) or bool(extraction.warnings) or bool(warnings)`.
- General fix, not XML-specific: any long seller/product value from any
  source (XML combine, unshortened model output) that gets truncated must
  be flagged the same way - no special-casing by source.
- Mirror the new `FilenameProposal.warnings` field by hand in
  `app/src/lib/api/types.ts` (existing no-codegen convention - see plan 05
  Block 4), and render it in `BatchItemRow.tsx` alongside the existing
  `missing_fields`/`extraction.warnings` blocks so a truncated filename is
  visibly explained, not just silently flagged.
- Add unit tests (`backend/tests/naming/test_builder.py` or wherever these
  live): a seller/product combination long enough to force truncation sets
  `requires_review=True` with an explanatory warning; a combination that
  fits stays warning-free exactly as today. Add a frontend component test
  asserting the new warning renders.
- Verify against the real `xml-issue-02-dominance-fallback.pdf` fixture:
  the previously-silent `...Torrey-Pi_265-EUR.pdf` truncation must now come
  back with `requires_review=True` and a visible reason.

Acceptance: any filename whose date/seller/product prefix is actually
truncated to fit the stem limit is flagged for review with a visible
reason, regardless of whether the long value came from XML or the model;
proposals that don't truncate are unaffected.

## Block 3: Field-level salvage for model extraction validation

- In `inference/extractor.py:_parse`, preserve the existing removal of
  `warnings`, `seller_short`, and `product_summary_short` from model JSON.
  Attempt ordinary `InvoiceExtraction.model_validate(data)` first.
- On `ValidationError`, attempt salvage only for a dictionary and only
  when every error has a nonempty location whose first component is a
  recognized, present extraction field. Never blindly index `loc[0]`:
  arrays, scalars, and JSON `null` yield root-level errors with `loc=()`.
  If any error cannot be safely mapped, re-raise the original validation
  error for the existing repair loop.
- Deduplicate rejected top-level field names, remove those keys from a
  copy of the sanitized dictionary, and re-validate. Keep all other values.
  Removing keys restores schema defaults, including non-nullable
  `language=Language.UNKNOWN` and `evidence={}`. Nested evidence errors
  reject the whole `evidence` field; the accepted schema is not entirely flat.
- Require at least one surviving filename field as defined above before
  returning a salvaged result. If nothing useful survives, re-raise the
  original validation error so a repair can still recover the invoice.
- Append one code-generated warning per rejected top-level field, in
  deterministic order, naming that field and using at most 50 characters
  of the first associated pydantic error message plus a truncation marker.
  Do not include `input`, `ctx`, or the raw response in salvage warnings.
  The seven eligible fields bound both warning count and echoed text.
- Change `extract_invoice`'s success return to preserve both document and
  extraction warnings instead of replacing the latter. Model-authored
  warnings must remain excluded.
- Malformed JSON, non-object JSON, unmappable errors, failed re-validation,
  and salvage with no useful surviving fields all retain the existing
  repair-prompt loop and, after `max_repair_attempts`, the all-null response
  with the `model output could not be validated` warning.
- Apply the same salvage step to the repaired response inside the retry
  loop too, not just the first attempt - a repair retry can introduce a new
  single-field mistake just as easily as the original response.
- Update the existing schema-violation repair test to expect immediate
  currency salvage without a repair call. Add unit tests for one and
  multiple bad fields; invalid language; malformed/nested evidence;
  document plus salvage warning preservation; warning deduplication and
  bounds; and continued exclusion of model warnings and short fields.
- Cover malformed JSON plus arrays, strings, numbers, booleans, and `null`
  through repair success and exhausted retries, including zero retries.
  Test a malformed first response followed by a salvageable repaired one.
  Cover a root-level/unmappable error defensively without an uncaught
  `IndexError` or `TypeError`.
- Test all five filename fields invalid with valid language/evidence:
  salvage must request repair, and repeated failure must retain the
  terminal failure marker. Also cover null/blank remaining fields and the
  boundary where one useful filename field survives. Lock down unchanged
  handling of already-valid empty responses.
- Verify salvage with scripted responses in the separate partial-XML
  integration fixture below. File 02 after Block 1 cannot prove salvage.
- Verify against the real `model-validation-error_01.pdf` and `_02.pdf`
  (pure-model, no embedded XML) locally that only `currency` ends up
  missing - the app's `missing_fields` list
  (`app/src/features/batch/components/BatchItemRow.tsx:234`) must no
  longer show date/seller/product/amount as missing when the model
  actually got them right.

Acceptance: a salvageable response preserves valid fields and both warning
sources without a repair call. Non-nullable rejected fields use schema
defaults; non-object JSON cannot crash extraction; rejected responses with
no useful filename fields still attempt repair and report terminal failure
if repair cannot recover them.

## Block 4: Happy path end-to-end verification

- Run the actual pipeline against both private files. File 01 must retain
  extraction fallback for product and currency and its unusable-amount
  warning. File 02 must produce the expected combined XML product and
  filename, preserving its other XML fields, and - per Block 2 - its
  proposal must come back `requires_review=True` with a truncation warning,
  since its combined product name is long enough to actually hit the stem
  limit; record the real filename either way, don't assert it fits. Set
  `shorten_enabled=False` when asserting no model calls; shortening is a
  separate permitted call when enabled. Check the enabled shortening path
  separately.
- Generate synthetic PDFs via `scripts/generate_fixture_pdfs.py` and add
  automated pipeline tests in `backend/tests/analysis/test_pipeline.py`:
  a complete XML invoice with two non-dominant items short enough to fit
  the stem limit must combine their names, preserve XML evidence, normalize
  `Item A + Item B` to `Item-A-Item-B`, skip the model when shortening is
  disabled, and come back `requires_review=False`. A second synthetic case
  with combined names long enough to force truncation must come back
  `requires_review=True` with Block 2's warning, proving that fix end to
  end through the pipeline and not just via `naming/builder.py` unit tests.
- Add a happy-path test crossing all three fixes, not just combination and
  salvage: two comparable XML items combine (Block 1) into a product name
  deliberately long enough, alongside the seller/date, to force the
  filename-stem truncation from Block 2 - don't pick short names here, or
  this test silently stops covering Block 2 the same way the original file
  02 fixture did. XML seller is absent so extraction fallback still runs;
  script a valid model seller plus an invalid currency and conflicting
  product/date/amount. Assert: the seller survives salvage (Block 3); XML
  product/date/amount/currency win over the model's conflicting guesses
  (Block 1); the resulting `FilenameProposal` has `requires_review=True`
  with Block 2's truncation warning in `proposal.warnings`; Block 3's
  currency-salvage warning is still present in `extraction.warnings` at
  the same time (both warning sources must survive together, neither
  clobbering the other); and only one extraction call occurs.
- Add a separate partial-XML case whose product remains unavailable
  because a competitor has no usable amount. Script a valid model product
  alongside invalid currency; assert the product survives salvage, the
  other four XML fields remain unchanged, and XML/document/salvage warnings
  reach the pipeline result. Add a pure-model case asserting only currency
  is missing when the other four filename fields validate.
- Exercise the benchmark runner with scripted salvage success and repeated
  all-fields-invalid responses. Assert `failed=False` for useful salvage
  and `failed=True` with the existing marker after unrecoverable retries;
  pipeline tests alone do not exercise benchmark scoring.
- Re-run the full `local_benchmark_data/xml-issues/` set through the
  benchmark script with XML routing enabled. File 02 should use XML;
  file 01 should retain fallback and its expected XML warning. Record
  actual results and remaining missing fields rather than requiring file 01
  to pass regardless of model output. Unsupported-profile results remain
  outside this plan's acceptance criteria.

Acceptance: automated synthetic tests prove all three fixes together
through the actual pipeline and verify benchmark failure classification.
Local files confirm their actual XML branches; file 01's unresolved
competitor remains a fallback, file 02 no longer needs extraction fallback,
and file 02's proposal truthfully reports whether its filename was
truncated.

## Block 5: Security, sanity, and safety review

- Confirm the combined-string length bound in Block 1 can't be bypassed by
  two line items each just under `_MAX_FIELD_TEXT_LENGTH` (already checked
  above, but re-verify the combined check runs before the value is ever
  stored as the extracted value with XML evidence or reaches the filename
  builder; `xml_path` itself remains the fixed `_LINE_ITEMS_PATH`).
- Confirm salvage in Block 3 can only ever *remove* model-supplied fields,
  never invent, coerce, or partially repair a value pydantic rejected -
  a dropped field must use its existing schema default (`None`,
  `Language.UNKNOWN`, or `{}`), never a best-guess substitute.
- Confirm a salvage warning never echoes more of the model's raw response
  than the existing 500-char raw-response allowance in the all-null path,
  so this doesn't open a new way to leak large/sensitive model output into
  logs or Run details. Check at most one warning per eligible field and
  the 50-character error-message bound, including nested evidence errors.
- Confirm `merge_xml_and_model` still can't let a salvaged (now-partial)
  model result overwrite a valid XML field - re-run the existing
  conflicting-fields tests from plan 05's Block 3 unchanged.
- Confirm the benchmark's `_REPAIR_FAILURE_MARKER` still fires for
  validation-failing responses with no useful filename fields after salvage
  and repair. Valid language/evidence must not turn that case into success.
- Confirm non-object JSON and unmappable error locations retain repair
  handling, and salvage warnings survive alongside document warnings
  without accepting model-authored warnings.
- Confirm Block 2's truncation warning never echoes the full pre-truncation
  seller/product text (which can be arbitrarily long), only a bounded,
  factual description; confirm it fires identically for an XML-combined
  value, an unshortened model value, or any other source - no special-casing.
- Run backend `pytest`, `ruff format --check`, `ruff check`, and `mypy src`
  (strict). Blocks 1 and 3 (salvage) are backend-only - `evidence` and
  `warnings` shapes on `InvoiceExtraction` are unchanged. Block 2 does
  change a frontend-visible contract (`FilenameProposal.warnings`), so run
  frontend `lint`/`test`/`build` too and confirm `types.ts` matches the
  backend schema. Record results in this plan.

Acceptance: all three fixes are bounded, non-guessing, and provably can't
regress the existing XML-wins and benchmark-failure-detection guarantees
from plan 05.

## Implementation bookkeeping

Keep implementation changes focused and give new code files short purpose
comments/docstrings. Once implementation starts, update `CHANGES.md` using
the repository version convention. This planning-only change needs no
changelog entry.
