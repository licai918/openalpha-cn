# ADR-0003: Numerical stack boundary

Date: 2026-08-06
Status: Accepted

## Context

Measured facts about the current codebase:

1. The runtime dependency set today is exactly 7 packages: `duckdb`, `fastapi`, `pydantic`,
   `pytz`, `typer`, `tzdata`, `uvicorn`. There is no `numpy`, `pandas`, `scipy`,
   `scikit-learn`, or `polars`.
2. `pyproject.toml:71-85`'s mypy override block already lists `numpy`/`pandas` — not because
   they are used yet, but because `akshare` → `pandas` → `numpy` is a transitive dependency,
   and CI runs `uv sync --all-extras`. That means **the CI environment physically has both
   packages installed while the project declares neither**; the local `.venv` does not have
   them at all.
3. `` `src/openalpha_cn/domain` `` is currently the only package in the codebase with zero
   numeric-library dependencies and zero infrastructure dependencies.
4. The factor layer needs cross-sectional regression (→ coefficients) and rank correlation
   (→ a float information coefficient) — exactly the shape of "a function that returns a
   pandas expression."

## Decision

Adopt **numpy + pandas** as the numerical stack. `polars` was considered and rejected; that
choice is not re-litigated here. Boundary rules:

- `openalpha_cn.domain` must not import any numeric library.
- `DataFrame` / `ndarray` types may only appear in `panel/`, `factors/`, and `models/`.
- A new numeric dependency may only be added to the runtime dependency group, and each
  addition must append one line of justification to this ADR.

The `domain/`-forbidden-import list below is enforced verbatim by the `domain-purity`
contract in `pyproject.toml`'s `[tool.importlinter]` section (see
[ADR-0001](./ADR-0001-local-first-runtime.md)'s guardrail and
`tests/unit/test_import_layering.py`). It is intentionally broader than "numeric libraries
only": it also forbids `duckdb`/`sqlite3` (ADR-0001's guardrail) and every sibling
`openalpha_cn` subpackage — `domain` must not reach sideways into infrastructure, evidence,
or presentation code either. `tests/unit/test_adr_consistency.py` parses this exact block
and set-compares it against the live contract's `forbidden_modules`, so the two cannot drift
apart silently.

<!-- domain-purity-forbidden-modules:start -->
```text
numpy
pandas
scipy
sklearn
duckdb
sqlite3
openalpha_cn.agents
openalpha_cn.api
openalpha_cn.backtest
openalpha_cn.decisions
openalpha_cn.evidence
openalpha_cn.models
openalpha_cn.product
openalpha_cn.providers
openalpha_cn.runtime
openalpha_cn.storage
openalpha_cn.tools
```
<!-- domain-purity-forbidden-modules:end -->

## Consequences

These are warnings for whoever introduces the numerical stack, not abstract concerns:

1. **mypy strict's chain reaction.** `follow_imports = "skip"` on numpy/pandas degrades
   every symbol from those packages to `Any`; combined with strict's `warn_return_any`, any
   function that returns a pandas/numpy expression fails type-checking. Nearly every public
   function in the factor layer will need an explicit `float(...)` / `cast(...)` at its
   boundary. There is already a local precedent for this pattern: `storage/parquet.py` is
   dense with `cast()` at the DuckDB boundary — 15 call sites as of `081b568`, all of them
   converting `Any`-typed row values back into declared types.
2. `sklearn` and `lightgbm` are **not** in the mypy override block. Under strict mode, the
   first `import sklearn` (or `lightgbm`) fails `mypy src` outright — introducing either one
   requires adding an override entry in the same change.
3. `disallow_any_generics` rejects a bare `np.ndarray`; any new numeric code must spell out
   the full generic parameters or define a type alias.
4. **The ruff rule set has zero coverage for numeric code** — no `NPY`, no `PD`, no `S` (the
   last one matters directly for `pickle`/`joblib.load` model loading). Adding the
   numerical stack should come with an evaluation of adding these three rule groups.
5. **Containers.** `lightgbm`'s manylinux wheel dynamically links `libgomp.so.1`, which
   `python:3.12-slim` does not ship. `libgomp1` must be installed in the **runtime** image
   stage, not only the builder stage. `deploy/compose.yml`'s `read_only: true` plus a
   `tmpfs /tmp:size=64m` and no `/dev/shm` will make joblib's parallel spill-to-disk fail.
6. **Determinism hazard.** Without pinning `OMP_NUM_THREADS` / `OPENBLAS_NUM_THREADS`,
   BLAS/OpenMP floating-point reduction order changes with thread count. For a system whose
   core selling point is content-addressed determinism, this is a direct reproducibility
   hazard — the numerical stack's introduction must pin thread counts in the same change.

## Guardrail

`openalpha_cn.domain` importing anything on the list above is a hard failure of the
`domain-purity` import-linter contract (`uv run lint-imports`), independent of this ADR. See
[ADR-0002](./ADR-0002-two-data-planes.md) for why the panel plane — where `DataFrame` /
`ndarray` are allowed — exists as a separate storage layer rather than living inside the
evidence plane or the domain layer.
