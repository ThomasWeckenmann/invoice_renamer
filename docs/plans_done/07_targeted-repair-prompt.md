# Targeted repair prompt

Status: Done. Block 1 implemented and verified; scope grew during
implementation (see Implementation notes below) to include null-field
retry, an AI-calls inspector UI, and a follow-up fix that also touched the
main extraction prompt, deviating from the 'main prompt out of scope'
decision below.

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

## Implementation notes (post-implementation)

Block 1 shipped as designed, plus additional work requested during
implementation, beyond this plan's original scope:

- Null-field retry: a fully-valid response that still leaves a useful field
  (invoice_date/seller/product_summary/gross_total/currency) null now also
  gets one bounded, narrow retry call - not just a pydantic-rejected field.
  Confirmed against a real qwen3-0.6b run: it recovered both a date and a
  total on retry that it had returned null for on the first pass. A
  rejected field and a null field are combined into one retry call when
  both occur together, so one doesn't starve the other of the single
  available repair attempt.
- AI-calls inspector: an (i) button in the app's Run details now opens a
  popup showing every model call (prompt + response/error) made for an
  invoice, in order, labeled by phase (extraction vs. the separate
  shortening pass) with per-phase retry numbering. Backend: `RunMetrics`
  gained `model_calls: list[ModelCall]`, populated by wrapping the model in
  a recording decorator in `analysis/pipeline.py`.
- Two real bugs were found (via code review) and fixed, both confirmed by
  direct reproduction before and after: a narrow repair reply was treated
  as a wholesale new extraction (wiping every other already-good field to
  null), and a reply mixing one recoverable field with one still-bad field
  discarded the recoverable one because all fields in a reply were
  validated together instead of independently.
- The AI-calls inspector then surfaced a real prompt-design issue: the
  filename-safety note (for seller/product_summary) lived in a shared
  footer sent with every prompt, including narrow retries not asking about
  those fields - plausibly why a real reply volunteered them unprompted.
  Fixed by moving the note into the seller/product_summary field
  instructions themselves. Those instructions are shared with
  `build_extraction_prompt`, so - deviating from the "main prompt out of
  scope" decision above - the main extraction prompt's wording changed
  too, not just the repair prompt.

Verification: backend `pytest` (432 passed), `ruff format --check`, `ruff
check`, and `mypy src` all pass. Frontend `tsc -b` and `eslint` pass;
`npm run test`/`npm run build` could not be run in this sandbox (shared
node_modules with the developer's Mac via virtiofs - see project memory).

## Implementation bookkeeping

Keep implementation changes focused and give new code files short purpose
comments/docstrings. Once implementation starts, update `CHANGES.md` using
the repository version convention. This planning-only change needs no
changelog entry.
