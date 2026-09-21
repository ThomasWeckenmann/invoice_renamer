# AGENTS.md

This file is for coding agents and contributors working in this repository. Keep `README.md` focused on human-facing setup and product information; keep implementation conventions, architectural warnings, and debugging notes here.

## Working Rules

- Keep chat replies terse. Plain step-by-step instructions (exact commands, exact lines) over prose explanations. No preamble, no restating what was asked, no trailing summary unless asked. The user reads every word slower than it's generated - don't waste their time.
- Do not implement code, dependency, configuration, documentation, or repository-instruction changes unless the user explicitly asks for changes. Treat reports, observations, questions, and diagnostic statements as discussion only until the user asks for edits. This includes when the user asks a workflow/process question ("should we do X now or later?", "do you want me to commit first?") — answer the question and stop; a question is not authorization to implement, even if your answer proposes a concrete next step.
- The user handles staging, tracking, and committing once everything is as expected. Do not flag untracked, unstaged, or uncommitted files in reviews unless the user explicitly asks for git state.
- Prefer existing local patterns over new abstractions.
- Every new code file gets a short docstring/comment at the top explaining its purpose (module-level docstring for Python, a leading comment block for other languages). Keep it to 1-2 lines — what the file is for, not how it works.
- Never reference planning artifacts in code comments/docstrings — no "Block N", plan/block names, section titles, or phrases like "per the plan"/"see docs/plans_open/...". A comment must stand on its own for a reader who has never seen the plan; it explains the code's own reasoning (the why), not where that reasoning came from in a doc that will eventually be archived to `docs/plans_done/`.
- Use focused edits and verify with the narrowest useful tests, then broaden verification when behavior moved across boundaries.
- When debugging a failure (crash, wrong output, timeout), get the actual raw data — real error text, real model/response output, real state — in front of you before proposing or implementing a fix. Do not act on a plausible-sounding theory instead of evidence. A generic error message can have several causes (e.g. a JSON parse error at position 0 means "first character wasn't valid JSON," not necessarily "empty response") — add logging/instrumentation to see what's actually there rather than guessing which cause applies.
- Avoid bloating large modules. When a new feature adds non-trivial state, mutations, or UI, prefer a focused hook/component/service module that matches existing ownership patterns instead of adding more logic to route orchestrators or already-large views.
- **After completing any task that changes product behavior, application code, tests, docs, or repository instructions, automatically add a changelog entry to `CHANGES.md` so the user can inspect and decide whether to commit.** Do not wait to be asked. Review-only tasks do not need standalone changelog entries unless they also include code or documentation changes.
- **Never add a changelog entry for planning work** — writing or revising a plan (`docs/plans_open/*.md`), or repo config/doc edits made purely to prepare for a plan (e.g. sanitizing `README.md`/`.env`/`docker-compose.yml` before real implementation starts), is not a changelog-worthy change. `CHANGES.md` exists for hindsight review of shipped behavior; a record of how a plan evolved before anything was built is not useful there and clutters it. Start logging once actual implementation against an approved plan begins.

## Changelog Convention (`CHANGES.md`)

Determine the next version number by reading the latest entry in `CHANGES.md`:
- Bug fixes → patch bump (1.5.4 → 1.5.5)
- New user-visible features → minor bump (1.5.x → 1.6.0)
- Breaking changes or large architectural shifts → major bump

If the latest entry has **not yet been committed**, update it in place - do not create a new version. Only bump the version and add a new entry once the previous one has been committed to git.

Entry format - one short commit-message line followed by an optional explanatory paragraph:

```
## Version X.Y.Z (YYYY-MM-DD, claude sonnet-4.6)

- One-line summary usable as a commit message

  Optional follow-up paragraph with detail when the change is
  non-obvious or has important caveats. Omit if the summary is self-contained.
```

The one-liner starts with `- ` and is written in imperative mood (no period). Prefix with `bugfix: ` for bug fixes. Keep it short enough to work as a git commit message.

Never use double quote characters (`"`) anywhere in changelog entries. Use single quotes (`'`) instead, because the user may reuse the one-liner directly as a git commit message.

Use at most one optional follow-up paragraph per version entry, and keep that entire paragraph to 4 lines or less total. Only exceed 4 total lines for genuinely important information (e.g. a required migration step, a binding security/behavioral decision, a breaking change) - not implementation narration.

**Before finalizing an entry, literally count the paragraph's lines.** If it's over 4, the fix is to cut content, never to keep it because it "explains one more non-obvious thing".

## Repository Structure

- Python backend package: `backend/src/invoice_renamer/`
- Backend tests: `backend/tests/`
- React/TypeScript frontend: `app/src/`
- Tauri/Rust shell: `src-tauri/`
- Synthetic test PDFs (non-sensitive): `fixtures/`
- Build and contract generation scripts: `scripts/`
- Docs and plans: `docs/`

Keep private invoice samples outside Git and reference them through a local test configuration.

## Linux build isolation

When this checkout is shared between a Mac and a Linux container (e.g. this
project's devcontainer, mounted from a Mac host over virtiofs),
`backend/.venv`, `app/node_modules`, `src-tauri/target`, and the staged
worker (`src-tauri/resources/worker`) are ordinary paths inside the checkout
that both sides use — the sandbox's own Linux kernel makes execution look
isolated, but the filesystem underneath is not. Installing dependencies or
building directly in the checkout on Linux overwrites the Mac's copies with
Linux-specific ones, and the next macOS build/run breaks until they're
reinstalled there. This actually happened, repeatedly, before
`scripts/linux_workspace.sh` existed:

- `cargo build`/`test`/`clippy`/`fmt` in `src-tauri/` re-ran `build.rs`,
  which copied the Linux-built sidecar into a path the Mac's own
  `cargo tauri dev` had staged its macOS sidecar into, so the next native
  run on the Mac tried to exec a Linux ELF binary and failed opaquely.
- `npm install` / `rm -rf node_modules && npm install` in `app/` installed
  npm's Linux-only optional native packages (`@rollup/rollup-linux-*`,
  esbuild) over the Mac's `darwin-*` ones, since npm only installs the
  current platform's variant — the Mac's next `npm run dev`/`cargo tauri dev`
  then failed with `Cannot find module '@rollup/rollup-darwin-*'`.
- `backend/.venv` is literally the Mac's own macOS virtualenv
  (`pyvenv.cfg` pointed at a macOS Python interpreter path), reached through
  the same mount. A bare `uv run pytest`/`ruff`/`mypy` resynced it with Linux
  wheels (torch especially, pinned to a separate CPU-only index for Linux),
  breaking the Mac's backend until it was re-synced there.

Run backend/frontend/Rust commands through `scripts/linux_workspace.sh`
instead of directly, whenever you're on Linux and unsure whether the
checkout is shared this way (assume it is in this devcontainer):

```
scripts/linux_workspace.sh backend uv run pytest
scripts/linux_workspace.sh app npm run build
scripts/linux_workspace.sh src-tauri cargo check
scripts/linux_workspace.sh . scripts/build_worker_sidecar.sh
scripts/linux_workspace.sh . cargo tauri dev
```

It mirrors the checkout into `~/.cache/invoice-renamer-linux-workspace/...`
(outside the shared mount) and runs the given command there. It's a
snapshot: it re-syncs before every run, so re-run it after editing source;
there's no live sync, and a long-running command (a dev server) holds the
mirror's lock until it exits. `scripts/build_worker_sidecar.sh` itself
refuses to run on Linux outside the mirror (checks `$LINUX_WORKSPACE_ACTIVE`,
which the wrapper sets). macOS commands are unaffected and run as documented
in `README.md` — this exists only for the Linux side of the shared checkout.

## Planning

When planning multi-block features, always:
- add a "happy path end-to-end" test that crosses all blocks.
- add a block at the end which covers security/sanity/safety

## Verification Commands

On macOS, run these directly. On Linux, prefix each with
`scripts/linux_workspace.sh <dir>` instead of `cd`-ing there yourself — see
[Linux build isolation](#linux-build-isolation) — e.g.
`scripts/linux_workspace.sh backend uv run pytest`.

Backend (from `backend/`):

```bash
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
```

Frontend (from `app/`):

```bash
npm run lint
npm run test
npm run build
```
