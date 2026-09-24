# Contributing

Thanks for your interest. This is a personal project, so larger changes are
best discussed in an issue first.

## Setup

Follow [Build and run](README.md#build-and-run) in the README. If a Linux
container or VM shares the checkout with a Mac, run Linux commands through
`scripts/linux_workspace.sh` (see [AGENTS.md](AGENTS.md#linux-build-isolation)).

## Before opening a pull request

Run the checks for the parts you changed (CI runs all of them):

```bash
# backend/
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src

# app/
npm run lint
npm run test
npm run build

# src-tauri/
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
```

Add an entry to [CHANGES.md](CHANGES.md) following the convention in
[AGENTS.md](AGENTS.md#changelog-convention-changesmd).

## Invoices and test data

Never commit real invoices, even redacted ones. Test fixtures in `fixtures/`
must be synthetic; `scripts/generate_fixture_pdfs.py` shows how the existing
ones are made. Keep private samples outside the repository.

## Security issues

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
