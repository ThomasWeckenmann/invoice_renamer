# AGENTS.md

This file is for coding agents and contributors working in this repository. Keep `README.md` focused on human-facing setup and product information; keep implementation conventions, architectural warnings, and debugging notes here.

## Working Rules

- Do not implement code, dependency, configuration, documentation, or repository-instruction changes unless the user explicitly asks for changes. Treat reports, observations, questions, and diagnostic statements as discussion only until the user asks for edits. This includes when the user asks a workflow/process question ("should we do X now or later?", "do you want me to commit first?") — answer the question and stop; a question is not authorization to implement, even if your answer proposes a concrete next step.
- The user handles staging, tracking, and committing once everything is as expected. Do not flag untracked, unstaged, or uncommitted files in reviews unless the user explicitly asks for git state.
- Prefer existing local patterns over new abstractions.
- Every new code file gets a short docstring/comment at the top explaining its purpose (module-level docstring for Python, a leading comment block for other languages). Keep it to 1-2 lines — what the file is for, not how it works.
- Never reference planning artifacts in code comments/docstrings — no "Block N", plan/block names, section titles, or phrases like "per the plan"/"see docs/plans_open/...". A comment must stand on its own for a reader who has never seen the plan; it explains the code's own reasoning (the why), not where that reasoning came from in a doc that will eventually be archived to `docs/plans_done/`.
- Use focused edits and verify with the narrowest useful tests, then broaden verification when behavior moved across boundaries.
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

## Planning

When planning multi-block features, always:
- add a "happy path end-to-end" test that crosses all blocks.
- add a block at the end which covers security/sanity/safety

## Verification Commands

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
