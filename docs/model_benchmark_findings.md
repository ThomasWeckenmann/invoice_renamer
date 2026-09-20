# Local model benchmark findings

Running log of what we've learned trying to pick the local model for Milestone 3. Update this as more runs happen; it's a findings record, not a plan — see `docs/plans_open/01_implementation-plan.md` for what's being built and what's next.

Test machine: user's M2/32GB Mac, `--device mps`. Test set: 5 real private invoices in `local_benchmark_data/` (gitignored — German/English, one OCR'd), ground truth hand-corrected to natural-language form (not filename-slug form) in `local_benchmark_data/ground_truth.json`. Results accumulate in `local_benchmark_data/results.json` (the benchmark CLI merges by `model_id`, so re-running one model replaces just its entry).

## Start here (for a fresh session)

- **Decided: ship both. The app now auto-selects Qwen3-0.6B once it is installed; Granite-3.3-2B-Instruct stays the accuracy recommendation and is one click away in the Model panel.** Qwen3-0.6B was chosen for the default because it is 2-3x faster per invoice and every proposed filename is reviewed before any rename happens, so its accuracy gaps cost a correction rather than a wrong result. That reverses the original recommendation below, which predates the auto-select behaviour — the accuracy numbers themselves still stand. Both are the only two entries in `SHORTLISTED_CATALOG` (`models/catalog_data.py`). Qwen3-0.6B needed correction on all 5 test invoices (including a silent malformed-number miss, `21.42`→`2142`, with no warning raised) — accepted as a known limitation to improve later (fine-tuning the prompt, or other fixes), not a blocker to shipping it now.
- **Removed from the catalog, cache cleared**: Qwen3-4B-Instruct-2507 and Phi-4-mini-instruct (catastrophically slow on this MPS setup, root cause unresolved — see below) and gemma-3-270m-it (fast but genuinely inaccurate at 270M scale — wrong values, not a format bug). Ruled-out models get removed from `catalog_data.py` entirely, not kept as dead entries (same as the earlier Qwen2.5-3B-Instruct/Phi-3.5-mini-instruct removals).
- **The `seller`/`product_summary` scoring fix had a real bug of its own (bidirectional substring matching let `seller="a"` match `"MediaMarkt"`, `product_summary="Pro"` match `"MacBook Pro 16"`) — fixed to one-directional + word-boundary-aware, and re-confirmed against real invoices.** The re-run produced the exact same matches as the pre-fix numbers (Granite `seller` 60%/`product_summary` 80%; Qwen3-0.6B `seller` 60%/`product_summary` 60%) — the bug was real (proven by the reproductions above) but didn't happen to flip any result on this specific 5-invoice set. Numbers in the table below are now confirmed, not stale.
- **Both models re-run after the `gross_total` prompt fix (trustworthy — exact numeric comparison, unaffected by the string-scoring bug) — Granite pulled ahead, Qwen3-0.6B did not benefit.** Granite: `gross_total` 60%→80% (its invoice01 net-vs-gross miss is now fixed). Qwen3-0.6B: `gross_total` fell 60%→40% — the prompt fix did **not** fix the exact case it targeted (invoice01 still returns the net amount `251.68` verbatim), and a previously-correct invoice (`37.46`) regressed to a malformed `3746` (dropped decimal point). Since generation is greedy (`do_sample=False`), this is a real effect of the longer instruction text, not run-to-run noise — Qwen3-0.6B appears sensitive to prompt length/complexity in a way Granite isn't. See "Prompt-fix outcome" below.
- **Qwen3-0.6B's latency roughly doubled between runs** (5.4s→12.5s avg/invoice; Granite's stayed flat, 16.0s→16.5s). Nothing in the code changes between these runs touches inference speed, so this looks like machine variance (thermal throttling, background load on the test Mac) rather than a real regression — noted, not yet explained. Don't treat either model's absolute latency numbers as precise; the relative gap (Granite meaningfully slower than Qwen3-0.6B) has held across every run so far.
- Environment notes for this sandbox (if debugging in the same container): `.venv` installs intermittently fail/hang on this virtiofs mount, worse as free disk drops. Workaround: `export UV_LINK_MODE=copy UV_CONCURRENT_INSTALLS=1` before `uv sync`/`uv run`, and retry once if a rename/copy error appears — it's transient, not a real dependency problem.
- **`avatar63/qwen-receipt-extractor` (HF) — checked, skipped.** Its Hugging Face metadata shows it's a **LoRA adapter** (`library_name: peft`) for `Qwen/Qwen2.5-0.5B-Instruct`, not a standalone model — the repo only has `adapter_config.json` + `adapter_model.safetensors`. Our catalog schema and `TransformersExtractor.load()` only support a single standalone model repo; testing this properly would need `peft` as a new dependency, base+adapter loading support, and a catalog schema change. User decided not worth it for a niche receipt-tuned adapter — staying with the two-model catalog above.

## Models tried

| Model | Params | License | MPS speed | Status |
|---|---|---|---|---|
| Qwen2.5-3B-Instruct | 3B | Qwen RESEARCH (non-commercial) | Fast (~13-60s/invoice) | Superseded — license issue, and a newer generation exists |
| Qwen3-4B-Instruct-2507 | 4B | Apache 2.0 | **Catastrophically slow** (1 invoice = 1566s / 26min) | Ruled out on speed. Confirmed `mps:0` device placement (not silently on CPU). Cause unconfirmed — no MPS op-fallback warning present. Likely architecture-specific MPS inefficiency (Qwen3 changed attention internals vs 2.5, e.g. QK-norm) — **unresolved**, not worth more time unless MPS/transformers versions change. |
| Phi-4-mini-instruct | 3.8B | MIT | **Also very slow** — stuck 3+ min in prefill alone (561 prompt tokens) before any token streamed | Ruled out on speed, same practical outcome as Qwen3-4B. |
| **Granite-3.3-2B-Instruct** | ~2.5B | Apache 2.0 | **Fast** (~16-18s/invoice avg, some run-to-run variance) | **Shipped; recommended for accuracy, not auto-selected.** 0% hard failures across all 5 invoices; best accuracy across every field measured so far. See accuracy table below. |
| **Qwen3-0.6B** | 0.6B | Apache 2.0 | **Fastest working model** (~5-12s/invoice avg, some run-to-run variance) | **Shipped; auto-selected when installed.** Needed correction on all 5 invoices, including a silent malformed-number miss; accuracy trails Granite and didn't improve from the latest prompt fix (see below). Known limitation, accepted for now — to be improved later rather than blocking shipment. |
| gemma-3-270m-it | 270M | Custom "Gemma" license (needs a read) | Blazing fast (~6.9s/invoice avg) | **Ruled out on accuracy**, not speed. See below — genuinely wrong values (currency confused with country code on every invoice, wrong fields picked up, invalid JSON number syntax), not a fixable wrapper/format bug. |

## Accuracy — head to head (same 5 invoices, same ground truth)

| Field | Granite-3.3-2B | Qwen3-0.6B |
|---|---|---|
| `invoice_date` | 100% (5/5) | 60% (3/5) — remaining 2 are wrong *values* (grabbed a different date present in the text), not a format issue anymore |
| `currency` | 100% (5/5) | 100% (5/5) |
| `gross_total` | **80% (4/5)** — invoice01 fixed by the prompt change; invoice02 (OCR-garbled) still wrong (`460` vs `400.00`) | **40% (2/5)** — regressed from 60%; see "Prompt-fix outcome" below |
| `seller` | 60% (3/5) | 60% (3/5) |
| `product_summary` | 80% (4/5) | 60% (3/5) |

All fields above are now confirmed under the corrected (one-directional, word-boundary) `seller`/`product_summary` scorer — re-running produced identical matches to the pre-fix numbers, so the earlier bidirectional-matching bug didn't happen to affect this particular 5-invoice set (see "Scoring fix history" below for why it was still worth fixing). Raw results: `local_benchmark_data/results.json` (current, post-scoring-fix) vs `local_benchmark_data/results_pre_prompt_refinements.json` (before the prompt/scoring fixes).

### Prompt-fix outcome: helped Granite, backfired on Qwen3-0.6B

The `gross_total` prompt instruction was strengthened to name the target label ("Rechnungsbetrag"/"Gesamtbetrag"/etc.) and explicitly rule out net-amount labels (`inference/prompts.py`). Per-invoice effect:

- **Granite**: invoice01 flipped from wrong (`349.5`) to correct (`299.5`) — the fix worked as intended. invoice02 changed from one wrong value (`409.0`) to another (`460`) — still wrong, likely because that invoice is OCR-garbled and no label is legible either way.
- **Qwen3-0.6B**: invoice01 — the exact case the fix targeted — is **unchanged**: still returns the net amount `251.68` verbatim, ignoring the new instruction. invoice03, previously correct (`37.46`), **regressed** to a malformed `3746` (dropped decimal point) — a new, unrelated failure mode. Since decoding is greedy (`do_sample=False`), this isn't noise: the longer prompt is a genuine causal input change, and this small model appears to degrade elsewhere when given more explicit instruction text rather than reliably picking up the new guidance.

Takeaway: the fix is a net positive for the catalog (Granite improved, no regression risk for future models), but Qwen3-0.6B may need a *shorter*, more surgical instruction rather than more explicit detail if it's to stay competitive — not yet attempted.

### Scoring fix history for `seller`/`product_summary` — two rounds, second one a real bug caught in review

Round 1: exact-string match after casefold+strip undercounted every model equally on substantively correct but more-detailed answers (a full legal entity name, a trailing "- Region xyz" suffix). "Fixed" by matching on substring containment in *either* direction.

Round 2 (this fix was itself wrong): bidirectional containment let a short, vague, or truncated *wrong* answer score as correct whenever it happened to be a substring of the right one — `seller="a"` matched `"MediaMarkt"`, `product_summary="Pro"` matched `"MacBook Pro 16"`. My own spot-check ("Recklinghausen" vs "MediaMarkt" still failing) didn't catch this because it only tested the *other* direction. Caught in code review, not by testing. Now fixed to be one-directional (ground truth must appear as a whole word/phrase inside the model's answer, via a word-boundary regex) — this accepts the legitimate padding case while rejecting both reproduced false positives. Re-run against real invoices confirmed identical results to before the fix — see the accuracy table above.

### gemma-3-270m-it — genuine accuracy failures (not fixable the way the other two bugs were)

100% failure rate, but unlike Qwen3-0.6B's, these are real wrong *values*, not a wrapper/format problem the pipeline can strip:
- `"currency": "DE"` (country code, not `EUR`) on **all 5 invoices** — systematic, not a fluke.
- Wrong fields entirely: `invoice_date` returned as the invoice *number* (`"2026034718"`) on one invoice, `seller` returned as a postal code (`"45657"`) on another.
- Invalid JSON number syntax: `"gross_total": 299,50` (German comma-decimal used as a literal, unquoted JSON token — not valid JSON at all).
- Garbled dates even beyond format: `"2026-13-07"` (month 13), `"August 2026"` (no day).

Conclusion: too small (270M) for reliable instruction-following/format compliance on this task, independent of speed. Not worth further prompt tuning — the errors are semantic, not formatting.

## Lessons that cost real time (see also the AGENTS.md rule this produced)

- **A generic JSON parse error ("Expecting value: line 1 column 1 (char 0)") is not proof of an empty response.** It just means "first character wasn't valid JSON" — a markdown code fence (` ```json `) produces the identical error, and so does a `<think>` reasoning preamble (see below). Cost about an hour chasing a "degenerate generation" theory (added `repetition_penalty`, empty-output diagnostics) before actually looking at the raw model output, which showed perfectly correct JSON wrapped in a fence. Fixed by stripping fences before parsing (`inference/extractor.py`) — this is what fixed the accuracy numbers, not any generation-parameter tuning.
- **Qwen3's hybrid think/non-think chat template defaults to thinking mode** (unlike the `-2507` "instruct-only" 4B checkpoint, which doesn't have this template). It emits a `<think>...</think>` block with no markdown fence before the JSON — same `json.loads()` failure signature as the fence bug, different cause, only visible via live token streaming. 100% failure rate on Qwen3-0.6B until fixed. Fix: `TransformersExtractor.generate()` now passes `enable_thinking=False` to `apply_chat_template()` unconditionally — models whose template doesn't reference that kwarg just ignore it, confirmed safe across all catalog entries.
- **Small models copy the source date format verbatim instead of normalizing it**, even when the prompt says "as YYYY-MM-DD" — Qwen3-0.6B returned `"07.07.2026"`, `"August 2, 2026"`, etc., which fail Pydantic's date parser. Fixed by rewriting the prompt instruction to be explicit about *converting* the format with concrete before/after examples (`inference/prompts.py`), not just naming the target format. This is a shared prompt used by all models, so the fix applies everywhere going forward — but it means results recorded *before* this fix (Granite, gemma, the original Qwen3-4B/Phi-4-mini runs) aren't perfectly comparable to results recorded after.
- **Ground truth was originally written in filename-slug format** (`"campingplus"`, `"gross_total": "300"` pre-rounded) instead of raw-extraction format, which made early accuracy numbers look far worse than reality, especially for `gross_total` (rounded values silently failed exact-match against precise model output). Corrected by reading the actual PDF text and hand-fixing the ground truth file.
- **`apply_chat_template(..., return_tensors="pt")` returns a `BatchEncoding`, not a bare tensor** — confirmed by downloading just a tokenizer (no weights) and testing directly, not assumed.
- **`--device mps` alone doesn't prove MPS is actually being used** — `TransformersExtractor` now prints the real device from `next(model.parameters()).device` right after `.to(device)`, since `.to()` can silently no-op.
- **A gated HF repo (e.g. gemma-3-270m-it) blocks anonymous access to every file, even tiny config files** — not just weights. The non-gated metadata API (`/api/models/{repo}`) still returns real license/architecture info without auth, but file *content* (hence checksums) needs the accepting user's own token — had the user run a local curl-with-token loop and paste back hashes rather than guessing or asking for their token.

## Open questions

- Root cause of Qwen3-4B / Phi-4-mini slowness on this MPS setup — unresolved, not currently blocking (Granite and Qwen3-0.6B both work and are fast).
- Whether Qwen3-0.6B's `gross_total` regression can be recovered with a shorter, more surgical instruction (its context window/instruction-following seems to degrade with the longer prompt) — not yet attempted. Low priority unless Qwen3-0.6B's speed advantage becomes decisive.
- Granite's remaining `gross_total` miss (invoice02, OCR-garbled source text) — likely not fixable via prompting since no legible total label exists in the OCR output; would need OCR quality improvements instead.
- **Decided: `SHORTLISTED_CATALOG` ships both Granite-3.3-2B-Instruct (default) and Qwen3-0.6B** (see "Start here" above). No tier-selection UI/logic exists yet — the catalog just lists both; nothing today picks one over the other automatically. Qwen3-0.6B's accuracy gap (especially the silent malformed-number failure mode) is a known limitation to fine-tune later, not a blocker.
- Root cause of Qwen3-0.6B's latency roughly doubling between the two most recent runs (5.4s→12.5s avg) while Granite's stayed flat — likely machine variance (thermal/background load), not investigated further since it doesn't change the relative speed ranking.
- `avatar63/qwen-receipt-extractor` — checked and skipped (it's a LoRA adapter, not a standalone model; not worth the `peft` dependency + adapter-loading support for a niche receipt-tuned model). See "Start here" above.
