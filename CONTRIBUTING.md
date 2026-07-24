# Contributing

OpenAlpha CN accepts focused, evidence-backed contributions.

## Before changing code

1. Read `AGENTS.md`.
2. Read the current handoff from `docs/HANDOFF_CURRENT.md`.
3. Check the v1 specification and implementation plan.
4. Search existing issues before opening a new one.
5. Confirm the data and license boundary for any provider change.

## Development gate

```powershell
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

Write a failing behavioral test before changing logic. Keep each pull request to one vertical slice and include:

- the feature or issue ID;
- acceptance criteria;
- source and license evidence;
- test output;
- user-visible documentation changes;
- known limitations.

Do not include real credentials, private data, runtime databases, paid-provider payloads, or copied data without confirmed redistribution rights.

## Commit messages

Use focused messages such as:

```text
feat: add point-in-time evidence visibility
fix: reject naive evidence timestamps
test: cover provider failure semantics
docs: document Tushare token boundary
ci: add Windows replay validation
```
