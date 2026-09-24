# Add security and contribution guidance and automated checks

Status: Blocks 1-2 written 2026-09-24 (no macOS CI job; clippy with
`-D warnings`; SECURITY.md email fallback). CI not yet run on GitHub;
Block 3 and the CI part of Block 4 pending.

## Goal

- Tell people how to report a vulnerability privately and how to contribute
  without committing private invoices.
- Run the existing backend, frontend, and Rust checks automatically on every
  push and pull request, so a break shows up without a local run on each
  platform.

## Current state (verified 2026-09-24)

- No `SECURITY.md`, `CONTRIBUTING.md`, or `.github/` directory exists.
- Verification commands are documented in `AGENTS.md`; build prerequisites in
  `README.md`.
- `src-tauri/build.rs` skips its worker-target check when no worker is staged,
  so `cargo check`/`cargo test` run in a fresh clone without building the
  Python worker.
- Two backend tests (live Apple Vision OCR) are skipped outside macOS.

## Block 1: SECURITY.md and CONTRIBUTING.md

- `SECURITY.md`: supported version (latest `main` only); report privately via
  GitHub's 'Report a vulnerability' (security advisories), not public issues.
  Short scope notes matching actual behavior: local worker on `127.0.0.1` with
  a per-launch session token, model downloads pinned to revisions and verified
  by SHA-256, invoice analysis runs locally.
- `CONTRIBUTING.md`: short. Link the README build steps and `AGENTS.md`
  conventions (changelog entries, `scripts/linux_workspace.sh` for shared
  checkouts, verification commands). State that fixtures must be synthetic and
  private invoices must never be committed.
- Verify: local links resolve; no claim stronger than the README's privacy
  section.

## Block 2: CI workflow (GitHub Actions)

One workflow, `.github/workflows/ci.yml`, on push and pull request to `main`,
`ubuntu-latest`, three parallel jobs:

- backend: install Tesseract with eng/deu data, `uv sync --frozen`, pytest,
  `ruff format --check`, `ruff check`, `mypy src`.
- frontend: `npm ci`, lint, test, build (in `app/`).
- rust: WebKitGTK/system packages from the README, `cargo fmt --check`,
  `cargo clippy`, `cargo test` (no staged worker needed, see above).

Constraints:

- `permissions: contents: read`; no secrets; no model downloads.
- Pin third-party actions to commit SHAs.
- Cache uv, npm, and cargo to keep runs short.
- Optional later: a `macos-latest` backend job for the two Vision OCR tests.

Verify: the workflow runs green on a branch before merging; note runtimes.

## Block 3: happy path end to end

On one commit: CI green for all three jobs, and locally on macOS a fresh clone
following the README (build worker, `cargo tauri dev`, analyze one XML and one
non-XML fixture copy, rename, undo).

## Block 4: security / sanity / safety

- New files contain no secrets, tokens, private paths, or invoice data.
- CI uses least-privilege permissions and pinned actions; it does not run on
  untrusted events with write access (no `pull_request_target`).
- `CHANGES.md` entry per block when implemented.
