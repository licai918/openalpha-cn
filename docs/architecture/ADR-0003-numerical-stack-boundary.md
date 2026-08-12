# ADR-0003: Numerical stack boundary

Date: 2026-08-06
Status: Accepted

## Context

Measured facts about the current codebase:

1. The runtime dependency set today is exactly 7 packages: `duckdb`, `fastapi`, `pydantic`,
   `pytz`, `typer`, `tzdata`, `uvicorn`. There is no `numpy`, `pandas`, `scipy`,
   `scikit-learn`, or `polars`.
2. `pyproject.toml:72-86`'s mypy override block already lists `numpy`/`pandas` — not because
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

## Update, 2026-08-11 (`V2-P3-002`): the factor engine did not need it

The Decision above stands unchanged — numpy + pandas remain the adopted stack, and this
section adds no new one. What it records is that the *first* consumer named in the Context
("the factor layer needs cross-sectional regression and rank correlation") arrived, and did
not need either package, so the runtime dependency set is still the nine it was.

`openalpha_cn.panel_factors` computes a factor for a whole cross section at one `as_of`: it
groups DuckDB's own row tuples by `(subject, session)`, takes the last `lookback_sessions` of
each security's own sessions, and calls one scalar function per security. There is no matrix,
no broadcast, no linear algebra and no regression in that workload; the column projection is
done in SQL, so a factor reading one column of `daily` never materialises the other eight.

The Context's claim was accurate about the *layer* and imprecise about *which issue*. The
cross-sectional regression is `V2-P3-004`'s neutralisation and the rank correlation is
`V2-P3-005`'s IC, and neither has been written. That is the right place to re-open this
question, with the workload in front of whoever decides — because the Consequences above are
the price of the decision, and paying them one issue early buys nothing: two more runtime
dependencies, an explicit `float(...)`/`cast(...)` at every public boundary in the layer, and
the `NPY`/`PD`/`S` ruff evaluation, all in exchange for arithmetic that is currently a division.

Measured rather than asserted, at ADR-0002's own stated panel scale. A synthetic `daily`
partition of **5,534 securities x 122 sessions = 675,148 rows** was written through the real
store, and `compute_factor` evaluated the whole cross section at one `as_of`:

| step | time |
|---|---|
| `compute_factor`, cold (read + group + classify + evaluate 5,534 securities) | **1.95 s** |
| the same call again, warm | 1.91 s |
| (for context, the existing write path: `write_panel_batch` for that partition) | see below |

Two seconds of single-threaded pure Python for 675k rows is ~2.9 us/row end to end, and a full
244-session year extrapolates to ~4 s. That is not a number numpy would rescue: the read itself
is DuckDB's, the grouping is a `dict` insert per row, and the arithmetic is one division per
security. It is also far below the *write* path this same partition already pays, which is where
a performance issue on this plane actually lives -- see the correction below for how far, and
why a single number for it is no longer quoted.

**Correction (`V2-P3-002` review, 2026-08-11): the write figure was `288 s` and is withdrawn as
an absolute.** Five measurements of one nominal quantity now exist and they span more than an
order of magnitude:

| measurement of `write_panel_batch` at 675,148 rows | time |
|---|---|
| the original, as first recorded here | 288 s |
| re-measured at the same row count during review | 56.7 s |
| extrapolated from a fifth of the scale | 234 s |
| re-measured during the `V2-P3-001`/`002` identity remediation | 350.6 s |
| re-measured again during this remediation (10 stored columns) | 617.9 s |

Column count and machine explain some of that and plainly not all of it, and none of the five
was taken under stated, controlled conditions. So this table no longer quotes a second-count for
this step: an absolute nobody can reproduce is worse than a comparison everybody can.

What survives is the comparison this row was here to make, and the smallest of the five is still
enough for it: against the 1.95 s read, the write is **29x** at 56.7 s, 148x at 288 s, 180x at
350.6 s and 317x at 617.9 s. So "the write path dominates by at least an order of magnitude, and
by two on four of the five measurements" is what this ADR now claims, and the decision it
records -- that the factor engine's arithmetic is not where a numerical stack would pay for
itself -- does not depend on which of the five is right. (The earlier paragraph's "two orders of
magnitude" was written when 288 s was the only figure; at 56.7 s it would have been an
overclaim.) The read side is the half that reproduces: 1.61 s cold and 1.60 s warm on an
independently built partition of the same size, slightly faster than the 1.95 s above.

The first run of this measurement said 2.01 s, and the difference is worth naming because it
was a real defect rather than noise: `manifest_id` is a pydantic `computed_field`, which is not
cached, and it was being read inside the per-security loop -- so the whole build manifest was
re-canonicalised and re-hashed 5,534 times. `domain/panel_batch.py` documents exactly that trap
(`payload_digest`, 10.5 ms on first access and 10.2 ms on the second) and this module walked
into it anyway; hoisting the read out of the loop is the fix.

### Re-measured after the `V2-P3-001`/`002` review remediation

The remediation added two per-build passes and one per-security check to the same workload: the
union of every visible session (`_panel_sessions`), the refusal of a panel narrower than the
lookback, and two binary searches per security for the window's span in panel sessions
(`_window_span`). Re-measured on the same shape -- 5,534 securities x 122 sessions = 675,148
rows through the real store:

| step | before | after |
|---|---|---|
| `compute_factor`, cold | 1.95 s | **2.24 s** (3.3 us/row) |
| the same call again, warm | 1.91 s | 2.26 s |

The conclusion is unchanged and so is the *kind* of arithmetic: still no matrix, no broadcast,
no regression, and the added work is a sorted-set union plus `bisect` over a list of at most
2,000 dates. The write path still dominates: `write_panel_batch` for the same partition took
350.6 s on this run, which is the fourth row of the correction table above and 180x this read.
That is one of five measurements spanning more than an order of magnitude, so the ordering is
what is claimed and not the second-count -- see the correction for why.
`V2-P3-004`'s neutralisation is still the issue that should re-open the question.

The nine runtime dependencies are pinned by `tests/unit/test_repository_assets.py`, which is
what would go red if this update were wrong.
