# Enable React Compiler lint rules and switch the test client to httpx2

Status: completed 2026-09-24. Blocks 1-5 implemented and verified on Linux
(lint clean with all 16 react-hooks rules, 122 frontend tests, build, 542
backend tests). Block 6 confirmed by the user on macOS: Granite preselected,
drop and Browse import, analysis with unload after batch, memory status,
rename, Undo and Redo. Block 7 covered by the unchanged Rust rename path, the
green rename/unmount tests, and the Block 6 walkthrough.

## Goal

Two independent maintenance items:

1. Turn on the React Compiler rules of `eslint-plugin-react-hooks` 7 and fix
   the 10 existing findings in the frontend hooks without changing behavior.
2. Remove the `StarletteDeprecationWarning` from the backend test run by using
   `httpx2` as the test client dependency.

Out of scope: the anyio `BlockingPortal` deprecation warning. It is emitted by
Starlette's own `testclient.py` and disappears with a future Starlette release;
no warning filter is added for it.

## Current state (verified 2026-09-24)

- `app/eslint.config.js` enables only `rules-of-hooks` and `exhaustive-deps`.
  With the plugin's full `recommended` rule set, lint reports exactly 10 errors:

  | Rule | Location | Cause |
  |---|---|---|
  | `refs` | `ImportDropzone.tsx:43,45` | Latest-callback refs assigned during render |
  | `refs` | `useBatchWorkspace.ts:66` | `itemsRef.current = items` during render |
  | `immutability` | `useBatchWorkspace.ts:135` | `pollJob` calls itself inside its own `useCallback` |
  | `immutability` | `useMemoryStatus.ts:64` | `poll` reschedules itself via `setTimeout(poll)` |
  | `immutability` | `useModelCatalog.ts:53` | `refresh` reschedules itself |
  | `immutability` | `useUnloadAfterBatch.ts:80` | `requestUnload` reschedules itself on 409 |
  | `immutability` | `useUnloadAfterBatch.ts:98` | `pollJob` calls itself |
  | `set-state-in-effect` | `BatchWorkspace.tsx:51` | Effect sets the default Granite selection |
  | `set-state-in-effect` | `useRenameTransaction.ts:105` | Mount effect calls a callback that sets state |

- React is 19.3, so `useEffectEvent` is available.
- Backend dev group lists `httpx`; Starlette 1.6.0's `TestClient` prefers
  `httpx2` and warns when it falls back to `httpx`. `httpx2` (2.13.x) is
  published by the pydantic organization. `httpx` stays in `uv.lock` as a
  runtime dependency of `huggingface_hub`; no source or test file imports
  `httpx` directly.

## Block 1: httpx2 as test client dependency

- In `backend/pyproject.toml` dev group, replace `httpx` with `httpx2`;
  update `uv.lock` without syncing the shared `.venv` (`uv add/remove --no-sync`).
- Verify: full backend suite green; the `StarletteDeprecationWarning` is gone;
  `uv.lock` diff limited to the dev-group swap plus the new `httpx2` package.

## Block 2: `refs` findings

- `ImportDropzone.tsx`: replace the two latest-callback refs with
  `useEffectEvent` handlers used inside the mount-only drag-drop subscription.
  The subscription must still be set up once on mount, not per render.
- `useBatchWorkspace.ts`: keep `itemsRef` (read by event-driven callbacks), but
  sync it in a `useLayoutEffect` instead of during render, so it is current
  before any user event after the commit.
- Verify: `ImportDropzone` and `useBatchWorkspace` tests green; add a test that
  a drop after a callback-prop change uses the latest callback, if not covered.

## Block 3: `immutability` findings (self-scheduling callbacks)

Timing-sensitive polling/retry code. Keep each hook's public API, generation
checks, and timer bookkeeping unchanged.

- Pattern: inside each `useCallback`, define a local named function that
  recurses on itself (`const tick = () => { ... setTimeout(tick) }`) and have
  the callback start it. The recursion then no longer references the
  callback variable before its declaration; dependency arrays stay the same.
- Apply to `useBatchWorkspace.pollJob`, `useMemoryStatus.poll`,
  `useModelCatalog.refresh`, `useUnloadAfterBatch.requestUnload` and
  `useUnloadAfterBatch.pollJob`. One hook at a time, tests after each.
- Verify: the existing polling tests for all four hooks stay green without
  timing changes to the tests themselves.

## Block 4: `set-state-in-effect` findings

- `BatchWorkspace.tsx`: derive the effective model selection during render
  (`selectedModelId ?? installed compatible Granite id`) instead of copying it
  into state from an effect. Behavior difference to accept or avoid: with the
  derived form, an implicit Granite default follows the catalog (e.g. it
  clears if Granite is removed before the user picks anything). Keep an
  explicit user choice in state exactly as today.
- `useRenameTransaction.ts`: load undoable batches on mount with the state
  updates inside the promise callback and an unmount guard, instead of calling
  the shared `refreshUndoableBatches` synchronously from the effect.
  `refreshUndoableBatches` stays for post-rename/undo refreshes.
- Verify: `useRenameTransaction` tests and `App`/workspace tests green; add a
  test for the Granite default if none exists.

## Block 5: enable the rules

- `app/eslint.config.js`: replace the two explicit hook rules with the
  plugin's `recommended` rule set and drop the comment explaining why the
  compiler rules were off.
- Verify: `npm run lint` clean; `npm run test`; `npm run build`.

## Block 6: happy path end to end

On macOS: `npm --prefix app install`, `cd backend && uv sync`, rebuild the
worker, `cargo tauri dev`. Import one XML and one non-XML fixture copy by drop
and Browse, confirm the Granite default is preselected, analyze with
'Unload after batch' on (model unloads after the batch), check the memory
status updates, rename, open Undo (batch listed), undo, redo.

## Block 7: security / sanity / safety

- The refactors must not change rename/undo safety: no-overwrite and
  identity checks live in Rust and are untouched; confirm the rename hook
  tests and the Undo/Redo GUI steps in Block 6.
- Polling: no duplicate timers or leaked timers after unmount (existing
  unmount tests stay green).
- `httpx2` is dev-only: not imported by `src/`, not bundled in the worker.
- `CHANGES.md` entry once implementation lands.
