# Targeted repair prompt

Status: not started. Split out of `06_extraction-fallback-fixes.md` -
out of scope there.

## Goal

Make the salvage-triggered repair/retry call (`06_extraction-fallback-fixes.md`
Block 3) more likely to actually recover a field a model got wrong, and
cheaper to run, by asking only for the specific field(s) that failed instead
of resending the full 6-field prompt and re-asking for everything again.

## Current evidence

- Observed directly on a real qwen3-0.6b run (`model-validation-error_01.pdf`):
  the first attempt returned `currency: 'DE'`; Block 3's repair call fired
  correctly and resent the *exact same* `_FIELD_INSTRUCTIONS` block, and the
  model returned `'DE'` again on retry - the retry didn't help, because it
  saw the identical weak instruction that caused the mistake the first time.
- The current instruction for currency is one bare line with no
  disambiguation from language: `"- currency: the ISO-4217 currency code,
  or null"` (`inference/prompts.py:29`) - it sits right next to the
  `language` field's own instructions (`"de", "en", or "unknown"`), which
  plausibly primes a small model to reach for a similar short code.
- `inference/extractor.py`'s salvage-triggered repair branch calls
  `build_repair_prompt(prompt, response, str(salvage_error))`
  (`extractor.py:83`), which resends the entire invoice page text plus the
  entire `_FIELD_INSTRUCTIONS` block and asks for all 6 keys again - even
  though `salvage_error` already names exactly which field(s) failed and why.
- `_salvage` (`extractor.py:128`) already extracts which field(s) failed via
  `detail["loc"]`/`detail["msg"]`; pydantic's error objects also carry
  `detail["input"]` (the model's actual rejected value) - currently unused,
  available to show the model what it got wrong.

## Behavior decisions

- Scope this to the repair call only, not the first/main extraction prompt -
  the developer already chose salvage-only over tightening the main prompt
  when this was first discussed (`06_extraction-fallback-fixes.md`); this
  plan doesn't reopen that.
- Only apply the narrower prompt when there's something to target: the
  salvage-triggered repair path (`salvage_error is not None` in
  `extract_invoice`), where the field names are already known. The
  JSON-parse-failure / totally-unsalvageable repair path keeps using the
  existing full `build_repair_prompt` - there's no specific field to target
  when the whole response was too broken to inspect.
- Re-ask only for fields the model is actually asked to supply: the 5
  filename fields plus `language`. A rejected `evidence` field is never
  re-asked - the model was never asked to provide it in the first place, so
  there's nothing meaningful to correct.
- Refactor `_FIELD_INSTRUCTIONS` from one monolithic string into per-field
  instruction snippets (keyed by field name) plus a shared intro/footer, so
  the full extraction prompt and the new narrow repair prompt build from the
  same source text - no duplicated, driftable copies of e.g. the
  gross_total decimal-point rule.
- Add one short disambiguating line per field for the retry prompt
  specifically (not the main prompt) wherever a small model's mistake
  pattern is plausible - at minimum currency ("NOT a language or country
  code").
- Show the model its own previous (rejected) value and the validation reason
  for each field being re-asked, reusing pydantic's own
  `error.errors()[i]["input"]`/`["msg"]` - already available, currently
  unused for this purpose (`_salvage`'s own warnings deliberately exclude
  `input` from *user-facing* text; reusing it in a *model-facing* prompt is
  a different concern and not a conflict with that).

## Block 1: Targeted single/multi-field repair prompt

- Restructure `_FIELD_INSTRUCTIONS` in `inference/prompts.py` into a mapping
  of field name -> instruction snippet (`invoice_date`, `seller`,
  `product_summary`, `gross_total`, `currency`, `language`), plus the shared
  intro ("Extract these fields...") and shared footer (filename-sanitization
  note + "never guess"/"no extra keys"/"raw JSON only"). `build_extraction_prompt`
  must produce output identical to today's by joining all snippets in the
  same order - add a test asserting this (e.g. compare against the current
  literal string, or assert every original instruction substring is present
  in the same relative order).
- Add a new `build_field_repair_prompt(document, rejected)` (exact
  signature TBD during implementation) that: includes the invoice page text
  (still needed as context), lists only the rejected field(s)' instruction
  snippets plus each field's previous rejected value and validation
  message, an added disambiguation line for currency (and any other field
  where one is warranted), and asks for a JSON object with exactly those
  keys - reusing the shared footer.
- Wire it into `inference/extractor.py`'s salvage branch: thread
  `error.errors()` (or an equivalent field -> (previous value, message)
  mapping) through so the repair call can be built with it, instead of just
  the field -> message strings `_salvage` already tracks for its own
  warnings. Keep the JSON-parse-failure branch using the existing full
  `build_repair_prompt` unchanged.
- Unit tests: the new prompt contains only the rejected field(s)'
  instructions (not the other fields'), shows the previous rejected value
  and reason, includes the currency disambiguation line when currency is
  rejected, and omits it when currency isn't involved. A multi-field-rejected
  case asks for all of them in one prompt, not one call per field.

Acceptance: a salvage-triggered repair call is meaningfully shorter than
today's (skips instructions for fields that already validated) and
explicitly tells the model what it got wrong and why, for every field
capable of being salvaged.

## Block 2: Happy path end-to-end verification

- Re-run the real captured `model-validation-error_01.pdf`/`_02.pdf` raw
  responses through the new repair-prompt builder (not just unit-testing it
  in isolation) and inspect the actual resulting prompt text.
- Add a scripted-model extractor test where the first response has a
  salvageable field and the repair response fixes it, asserting the repair
  prompt sent (`model.prompts[1]`) is the new narrow one, not the full one.
- Manually re-run the small qwen3-0.6b model against
  `model-validation-error_01.pdf`/`_02.pdf` (or similar real invoices) and
  record whether the narrower, disambiguated retry actually recovers
  `currency` more often than the current full-prompt retry - this is the
  actual point of the change and can't be proven by unit tests alone.

Acceptance: the new prompt is proven correct end to end (not just via its
own unit tests), and there's a recorded real-world comparison of recovery
rate before/after.

## Block 3: Security, sanity, and safety review

- Confirm the previous (rejected) value shown back to the model can't blow
  up the prompt - the length bounds `_salvage`'s own warnings already
  respect for user-facing text don't automatically apply here since this
  text goes to the model, not the UI; decide and document an explicit bound
  (or confirm existing upstream field-length limits already make this moot).
- Confirm a rejected field with a non-string previous value (e.g. a list,
  dict, or other type from a hostile/broken model response) can be safely
  rendered into the prompt without crashing (e.g. `json.dumps`, not naive
  string formatting that could raise on an unexpected type).
- Confirm the refactored `_FIELD_INSTRUCTIONS` produces an unchanged
  `build_extraction_prompt` output (covered by Block 1's parity test) -
  re-verify explicitly here, since a silent wording drift in the *main*
  prompt would reopen the "also tighten the prompt" scope the developer
  explicitly deferred earlier.
- Run backend `pytest`, `ruff format --check`, `ruff check`, and `mypy src`.
  No frontend contract changes expected (prompt text is backend-internal) -
  confirm `InvoiceExtraction`/`FilenameProposal` shapes are untouched and
  skip frontend verification if so.

Acceptance: the new repair-prompt path can't be crashed or blown up by a
hostile/malformed previous value, and the main extraction prompt is provably
unaffected.

## Implementation bookkeeping

Keep implementation changes focused and give new code files short purpose
comments/docstrings. Once implementation starts, update `CHANGES.md` using
the repository version convention. This planning-only change needs no
changelog entry.
