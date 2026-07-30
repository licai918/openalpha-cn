# Contributing

OpenAlpha CN accepts focused, evidence-backed contributions.

## Before changing code

1. Read `AGENTS.md`, including the v2 hard rules.
2. Read the current handoff from `docs/HANDOFF_CURRENT.md`.
3. Pick an issue from `docs/specs/v2/openalpha-cn-v2-roadmap.md` and read its phase gate.
   Check `docs/specs/v2/openalpha-cn-v2-prd.md` for scope and
   `docs/specs/v2/openalpha-cn-v2-seam-audit.md` for the evidence behind the subsystem you touch.
   `docs/specs/openalpha-cn-v1-spec.md` is the contract baseline, not the work pointer.
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

Write a failing behavioral test before changing logic. Keep each pull request to one issue and include:

- the roadmap issue ID (`V2-P0A-001` … `V2-P5-023`), or the v1 ledger feature ID for maintenance work;
- acceptance criteria, matching the issue's phase gate;
- source and license evidence;
- test output;
- the capability-ledger row, with a `file#symbol` reference that resolves and an `acceptance_test`
  bound to a real pytest node id;
- user-visible documentation changes;
- known limitations.

If the change adds a numerical dependency, moves a test file, alters a persisted contract, or writes
to a data store, re-read the corresponding v2 hard rule in `AGENTS.md` first — each of those four has
a CI gate that fails in a non-obvious place.

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
