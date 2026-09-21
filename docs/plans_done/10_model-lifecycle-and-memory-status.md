# Model loading/unloading and memory status clarity

Status: Blocks 1–4 implemented. Block 5 closed using the user-reported green
automated and live checks. Block 6 reviewed on 2026-09-21; focused lifecycle
fixes applied, with the accessibility limitation recorded below.

## Goal

The currently-loaded local model is never released, so it occupies several
GB of RAM/VRAM for as long as the app runs. Add both an idle-timeout
auto-unload and an opt-in per-batch auto-unload, make an in-progress model
load visible instead of a silent multi-second gap, and make the existing
memory status line easier to interpret (what each figure excludes, and a
visual cue when free RAM is low).

## Current evidence

- `ModelRuntime` (`backend/src/invoice_renamer/inference/runtime.py:77-135`)
  is documented as not thread-safe by design: only the single analysis
  worker thread calls `get_or_load()`/`_unload_current()`. `loaded_entry_id()`
  and `snapshot()` (lines 94-101) are the sanctioned exceptions, safe to
  read from another thread as plain attribute reads. There is no `loading`
  flag today: between `_unload_current()` returning and `load_installed()`
  returning (lines 109-116), `snapshot()` reports `None` for the loaded
  model's id because unloading clears it, with nothing
  distinguishing "idle" from "loading".
- `AnalysisCoordinator._worker_loop` (`backend/src/invoice_renamer/api/analyses_routes.py:188-228`)
  runs on one daemon thread and blocks on `self._condition.wait()` with no
  timeout when the queue is empty (line 192). Nothing currently fires on
  idle. `get_or_load()` is called once, at line 207, only from this thread.
- No idle-timeout or explicit unload action exists. `ModelRuntime._unload_current()` (lines 121-134)
  only ever runs as a side effect of `get_or_load()` loading a *different*
  model; there is no way to force-unload the currently selected model.
- `GET /memory` (`backend/src/invoice_renamer/api/memory_routes.py:11-15`)
  reads `coordinator.runtime_snapshot()` only for `.device`.
  `MemorySnapshot` (`backend/src/invoice_renamer/inference/memory_status.py:27-37`)
  has no field for an in-progress load.
- `MemoryStatus.tsx` (`app/src/features/batch/components/MemoryStatus.tsx:63-71`)
  already renders `RAM used/total`, `Worker <rss>`, and
  `GPU (Metal|CUDA|ROCm) ...` — the label text already matches this note's
  target labels. The only `title` attributes are the stale-poll tooltip
  (line 61) and the GPU-error tooltip (line 68); there is no per-stat
  explanatory text and no usage-based coloring. The only conditional style
  is `.memory-status--stale` (`app/src/features/batch/components/batch.css:60-81`),
  triggered by a failed poll, unrelated to usage level.
- `useMemoryStatus` (`app/src/features/batch/useMemoryStatus.ts:8`) polls
  every 1000ms, pausing while the document is hidden and backing off to
  10s on error — this is the channel new fields should ride on rather than
  a second endpoint.
- There is no backend "batch" entity: `BatchWorkspace.tsx` submits one
  `AnalysisJob` per file individually (`useBatchWorkspace.ts:235`,
  `AnalysisCoordinator.submit()` at `analyses_routes.py:126`). Per-run
  options like `shortenFields` are plain local `useState` in
  `BatchWorkspace.tsx:23`, passed straight into `startAnalysis()` — the
  established pattern for a new per-run checkbox. "Batch" is a frontend
  grouping only; nothing server-side tracks when a group of jobs is done.
- `ModelSelector.tsx` (`app/src/features/batch/components/ModelSelector.tsx:18-86`)
  already shows download size and description per row (from plan 08); it
  has no "currently loading" indicator.

## Product decisions

- Ship both unload mechanisms, not either/or: a 5-minute idle-timeout
  (Ollama-style default, always on) and an opt-in "unload model after this
  batch" checkbox (LM Studio-style explicit control, per run). They share
  one `ModelRuntime.unload()` entry point and don't conflict.
- Loading visibility is an indeterminate label only ("Loading Granite 3.3
  2B Instruct…"), never a progress bar. `AutoModelForCausalLM.from_pretrained()`
  exposes no chunked-read progress for local files; a real percentage would
  need custom tracking around the safetensors files, which is out of scope.
- The idle timer measures from when the *last job finished*, not from load
  time. A long-running job is never "idle" regardless of its duration.
- `unload()` is a new public method on `ModelRuntime`, called only from the
  worker thread (idle-timeout check and the batch-completion action both
  ultimately run there), preserving the existing "only one thread mutates"
  invariant. No lock is added.
- The batch-completion checkbox is a frontend behavior, not a backend
  concept. Add explicit frontend run tracking: visible item status alone
  cannot establish backend completion, since cancellation stops item
  polling immediately and polling errors mark items failed locally.
  Confirm backend terminal states before requesting unload. The backend
  does not need to learn what a "batch" is.
- Backend guards the unload action itself: return a busy response if a
  job is QUEUED or RUNNING, and recheck on the worker before unloading.
  The frontend retains the request until work finishes; a busy response
  must not silently consume the run's unload intent.
- Model selection and residency are independent. Add a loaded-model label
  driven by `/memory`; unloading clears that label but preserves selection.
- Color-code only the system RAM stat (used/total), not Worker or GPU —
  the note asks for "ram usage" specifically. Default thresholds (percent
  *available*, adjustable later): under 10% = critical, under 25% =
  warning, otherwise the existing neutral color. Ship as CSS custom
  properties near the existing `--warn-ink` usage in `batch.css`, not
  inline hex values, so thresholds are one place to tune.
- Suppress RAM coloring while the reading is stale (component already
  threads a `stale` flag) — a color based on outdated numbers is worse
  than no color.
- Tooltips are additive text on the existing compact labels, not a label
  rewrite — `RAM` / `Worker` / `GPU (Metal)` etc. already match the target
  copy. Add the Apple Silicon disclaimer ("GPU memory shares system RAM;
  these figures are not additive.") only when `gpu.backend === "mps"`.
- No new persisted settings/config. The idle timeout is a backend constant;
  the batch-unload checkbox is local React state, matching how
  `shortenFields` already works. A configurable timeout is a possible
  follow-up, not part of this plan.

## Block 1: Loading-state visibility

- Add `_loading: bool` and `_loading_entry_id: str | None` to `ModelRuntime`
  (`runtime.py`), set immediately before the `load_installed(...)` call in
  `get_or_load()` (line 116) and cleared in a `finally` covering both
  success and a failed load (e.g. an OOM during load must not leave
  `loading` stuck `True`).
- Extend `RuntimeSnapshot` (lines 19-26) with `loading: bool` and
  `loading_entry_id: str | None`; `snapshot()` (lines 98-101) copies them
  the same lock-free way it already copies `loaded_entry_id`/`device`.
- Extend `MemorySnapshot` (`memory_status.py:27-37`) with the same two
  fields plus `loaded_entry_id`, populated in `get_memory()` (`memory_routes.py:11-15`) from
  `coordinator.runtime_snapshot()` — reuse the existing polled channel
  rather than adding a second endpoint.
- Frontend: extend the `MemorySnapshot` API type, and in `MemoryStatus.tsx`
  render "Loading `<display name>`…" while `loading` is true, resolving
  the display name from `loading_entry_id` against the already-fetched
  model catalog. Fall back to the raw id if the catalog lookup misses.
  Pass the catalog from `BatchWorkspace` rather than fetching it again.
  Otherwise show "Loaded `<display name>`" when `loaded_entry_id` is set,
  or "No model loaded" when it is null. Preserve the selected model.
  Suppress active loading wording for a stale reading and identify the
  residency information as last-known state.

Checks: `ModelRuntime` unit test asserting `loading` is `True` only for the
duration of an injected, controllable `load_installed` call (block/release
deterministically, matching the existing test pattern for that protocol),
and `False` again after both a successful and a raising load. API contract
test for the three new `/memory` fields. Frontend tests for loading,
loaded, unloaded, and stale states, including an unresolved model id and
selection remaining unchanged after unload.

Acceptance: the runtime reports loading for the actual load duration, on
first load and on a model switch. The UI reflects observed state at the
existing polling cadence; loads shorter than a polling interval may not
be displayed, and hidden documents do not poll. No artificial minimum
display duration or change to inference behavior is introduced.

## Block 2: Automatic unload (idle timeout + batch-completion checkbox)

- Add `ModelRuntime.unload()`: a public wrapper around the existing
  `_unload_current()` (lines 121-134) so a caller can force-unload the
  current model without loading a replacement.
- Change `AnalysisCoordinator._worker_loop`'s `self._condition.wait()`
  (`analyses_routes.py:192`) to a bounded wait so the loop periodically
  wakes with an empty queue, checks elapsed time since the last job
  finished, and calls `self._runtime.unload()` once 5 minutes have passed.
  Use an injectable monotonic clock. Track the last-activity timestamp as
  a plain attribute written only by the worker thread after cleanup for
  every processed job, including failed and cancelled jobs, matching
  the existing "only one thread mutates" pattern — no new lock.
  Do not repeatedly unload an empty runtime. Queued jobs cancelled before
  execution do not reset the timer because they did not use the worker.
- Never unload while a job is QUEUED or RUNNING; a job arriving right as
  the timeout would fire must win if admitted before the unload decision.
  Serialize the final idle check and unload with job admission under the
  existing condition lock; submissions arriving after that decision wait
  until cleanup finishes, then proceed normally.
- Add a small backend action to force an unload on demand (e.g.
  `DELETE /memory/loaded-model`, modeled on the existing
  `DELETE /jobs/{id}` cancel route at `analyses_routes.py:284-285`),
  routed through a pending command and condition notification to the
  worker. Return 204 after successful cleanup (also when already empty),
  409 when busy, and 500 on cleanup failure. Recheck queued/running work
  when the worker handles the command; accepting a command is not proof
  that unloading happened. Complete every command's response even when
  cleanup raises, without blocking the API event loop.
- Catch unload exceptions inside the worker for both idle and explicit
  paths. Log the actual exception, preserve accurate runtime residency
  state, and keep processing jobs. Clear pending commands on all outcomes;
  prevent a failed idle cleanup from becoming a tight retry loop. A cache
  cleanup failure after dropping the extractor must not restore a false
  loaded-model label or claim that all allocator memory was released.
- Frontend: add an "unload model after this batch" checkbox next to
  `shortenFields` in `BatchWorkspace.tsx:23`, as local `useState` (not
  persisted). Capture its value when analysis starts; later toggles affect
  future runs only. Each Analyze invocation records its submitted item
  ids/generations; an individual analyze/rerun creates a one-item run.
  Track overlapping runs independently in a focused hook/helper rather
  than adding lifecycle orchestration to `BatchWorkspace.tsx`.
- Keep run tracking independent of visible rows. Cancelling/removing a row
  or superseding its generation does not forget its old backend job.
  Await in-flight submissions, cancel late returned jobs when appropriate,
  and continue lifecycle polling until every accepted job is terminal.
  `needs_review`/`approved` represent completed analysis; a local poll
  failure or optimistic cancellation does not prove backend termination.
  Definite submission rejections settle that submission; an ambiguous
  transport failure must be surfaced rather than treated as confirmed
  backend completion. The idle timeout remains the fallback in that case.
- When a checked run finishes, retain one pending unload intent. Wait
  until all tracked submissions/jobs are settled before sending it;
  overlapping completed intents may share one unload. On 409, retry at
  the existing job-poll cadence after rechecking tracked work. On success,
  consume the completed intents exactly once. On transport/server errors,
  show a non-blocking failure with an explicit retry action; do not claim
  success or retry indefinitely. Stop lifecycle polling on unmount; the
  backend idle timeout remains the fallback when the workspace closes.

Checks: worker-loop test that idle unload fires after a simulated timeout
using an injectable clock (mirroring the existing injectable
`load_installed` pattern) and does not fire while a job is queued/running
or before the timeout elapses. `unload()` no-op test when nothing is
loaded. API tests for successful, empty, busy, and failing unload commands,
including work admitted between command submission and execution. Inject
an unload failure and verify the worker handles a subsequent job and all
command waiters receive a response. Frontend tests for one successful
unload after confirmed completion, zero calls when unchecked, checkbox
changes mid-run, overlapping runs, reruns, cancellation/removal during
upload or inference, polling errors, busy retries, failure feedback, and
unmount cleanup.

Acceptance: an idle worker frees its model 5 minutes after the last job,
with no job running. A user who checks "unload after batch" sees the
loaded-model indicator clear after confirmed completion and successful
unload, without waiting out the idle timer. Other active work postpones
unloading; failures remain visible and do not terminate the worker.

## Block 3: Status line label clarity

- Add explanatory `title` text per stat in `MemoryStatus.tsx:63-71`:
  RAM → "System RAM used / total — includes all apps and the OS."; Worker
  → "Python process RAM — excludes the app window and shell."; GPU
  (Metal) → "GPU allocations (shared RAM, incl. cache) — uses the Mac's
  shared memory pool."; GPU (CUDA)/(ROCm) → "GPU tensors / reserved VRAM
  — reserved already includes tensors."
- Add the Apple Silicon disclaimer only when `gpu.backend === "mps"`:
  "GPU memory shares system RAM; these figures are not additive." Render
  it once, attached to the GPU item, not duplicated on every stat.
- Preserve the existing stale-poll title (line 61) and GPU-error title
  (line 68) — when either applies it takes priority over the new static
  help text rather than being silently overwritten.

Checks: rendering tests asserting each stat's `title` text under mps/cuda/
rocm/no-gpu fixtures, and that the Apple Silicon note appears only for mps.
Regression test that stale/error titles still win when present.

Acceptance: each stat's tooltip explains what it measures and excludes,
the compact label text is unchanged, and existing stale/error messaging
still takes priority when active.

## Block 4: RAM usage color coding

- Compute percent-available from `system_total_bytes`/`system_available_bytes`
  (already in `MemorySnapshot`) in `MemoryStatus.tsx` and apply a modifier
  class to the RAM item only (e.g. `memory-status__item--critical` /
  `--warning`), alongside the existing `memory-status__item` class
  (lines 63-65). Worker and GPU items are never recolored.
- Add the corresponding rules in `batch.css` near the existing
  `--stale`/`--warn-ink` styling (lines 60-81), using CSS custom
  properties for the threshold colors.
- Thresholds (percent available): under 10% = critical, under 25% =
  warning, otherwise unchanged. Suppress coloring entirely while `stale`
  is true, reusing the flag already threaded through the component.

Checks: component tests at threshold boundaries (just above/below 10% and
25% available) and confirming a stale snapshot never applies a usage
color regardless of the last-known percentage.

Acceptance: low free system RAM is visible at a glance; a stale/unavailable
reading never flashes a misleading color.

## Block 5: Happy-path end-to-end — complete

The user reports all automated tests and live testing green, including the
implemented lifecycle and memory-status behavior. Accept that verification
for this block; no additional integration suite or repeat live-test pass is
required just to duplicate it. This records user-reported results, not a
new test run by the reviewing agent.

The end-to-end acceptance remains load → analysis → batch unload → reload
with batch-unload disabled → idle unload → reload, together with residency,
RAM colors, and tooltips. Existing API coverage also exercises explicit
unload followed by reload in `backend/tests/api/test_memory.py`.

## Block 6: Security, sanity, and safety — reviewed

This is a final code/coverage checklist, not another test-suite block.
Existing test results are user-reported; the review inspected their coverage
without rerunning them. Focused fixes below postdate those green results.

- [x] Authentication: `/memory/loaded-model` inherits the app-wide
  `require_session_token` dependency and existing CORS configuration in
  `api/app.py`. `test_unload_route_requires_a_token` covers missing auth.
  CORS is browser policy; the session token authenticates requests.
- [x] Admission safety: `_next_step()` rechecks queue/pending state after
  every wake and holds the admission lock through unload. Existing
  coordinator coverage includes queued/running rejection and serialization
  of both idle and explicit unload with submission.
- [x] Loading failures: `get_or_load()` clears loading state in `finally`;
  `test_loading_clears_after_a_failed_load` covers the exception path.
- [x] Competing unloads: one worker serializes idle and explicit actions;
  unloading an empty runtime is harmless. Concurrent explicit requests
  receive responses. The frontend guards outstanding uploads/jobs and
  cancels scheduled busy retries when a new tracked submission starts.
- [x] Cleanup failures: the worker catches unload exceptions, signals
  explicit-command waiters, and continues processing. Runtime residency is
  cleared before allocator cleanup. Existing coverage checks a 500 response
  and successful subsequent work; periodic idle checks avoid a tight loop.
- [x] Completion tracking: cancellation/removal does not discard accepted
  jobs; lifecycle polling requires backend terminal status. Pending uploads
  reserve slots synchronously. Review fix: distinguish explicit client
  rejections from ambiguous transport/server failures. Unknown outcomes
  retain their slot, show a generic warning, and pause automatic batch
  unloading for this workspace session; backend idle unloading still works.
- [x] Unmount safety: review fix adds mounted-state checks before settling
  late submissions or sending unload requests. Late responses cannot
  recreate lifecycle timers or trigger unloading after cleanup. Already
  issued backend requests cannot be recalled by unmounting.
- [x] Privacy: new residency text uses catalog names/ids and memory labels
  use aggregate counters. The ambiguous-upload warning includes no raw
  response, filename, invoice content, or path. Existing GPU error details
  and unload exception messages remain diagnostic text, not aggregate
  measurements; no invoice payload is added to these paths.
- [x] Accessibility reviewed: numeric values and textual residency remain
  visible independently of colors. Stale static tooltips defer to the
  stale-error message. Known limitation: native `title` tooltips on spans
  are hover-only and are not a reliable keyboard/touch help mechanism.
  A focusable help affordance is deferred; do not claim full tooltip
  accessibility in this implementation.

Review outcome: no additional test suite requested. Two focused frontend
lifecycle fixes were applied; the existing green automated/live report
predates them. TypeScript checking, focused ESLint, and diff whitespace
checking passed after these fixes. Retain the known tooltip accessibility
limitation explicitly.

## Verification and implementation bookkeeping

```bash
cd backend
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
```

```bash
cd app
npm run lint
npm run test
npm run build
```

On the target Mac, watch the status line during a real model load and a
real 5-minute idle period, and confirm CUDA/ROCm/MPS-specific tooltip text
against whatever hardware is available. Use existing private benchmark
data locally if needed; do not add invoices to the repository.

The commands above are the verification reference, not a requirement to
repeat the already-green suites for this checklist. No tests were added or
rerun during block 6. Record implementation and review fixes in the existing
uncommitted `CHANGES.md` entry; leave staging and committing to the user.
