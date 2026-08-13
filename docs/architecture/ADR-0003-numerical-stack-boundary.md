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
union of every visible session (`_panel_axis_points`), the refusal of a panel narrower than the
lookback, and two binary searches per security for the window's span in panel sessions
(`_session_span`). Re-measured on the same shape -- 5,534 securities x 122 sessions = 675,148
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

## Update, 2026-08-12 (`V2-P3-003`): the preprocessing transforms did not need it either

The Decision above still stands unchanged and this section adds no new one. What it records is
that the second candidate consumer arrived — winsorization, standardization and a missing-value
policy over a whole cross section — and was re-measured on its own workload rather than assumed
to inherit the previous section's answer. That distinction matters: `compute_factor`'s arithmetic
is one scalar call per security and has no shape a numerical stack helps with, while a transform
is genuinely *cross-sectional* (a sort for the quantiles, a two-pass mean and deviation, an
average-rank pass), which is the first thing in this repository that looks like an array
operation.

Measured at ADR-0002's stated panel scale — 5,534 participants at one `as_of`, 3% of them holes
for the missing-value policy to fill, three repetitions per configuration, minimum reported:

| step | time |
|---|---|
| `apply_factor_transform`, quantile winsorization + z-score | **36.7 ms** (6.6 µs/security) |
| the same, mad winsorization + z-score | 35.9 ms |
| the same, quantile winsorization + centred rank | 37.6 ms |
| the same, mad winsorization + centred rank | 36.9 ms |
| `observation_digest` alone (canonical JSON + one sha256 over the source cross section) | 2.8 ms |
| `processed_observation_batch` + `transform_manifest_batch` for the same panel | 14.9 ms |

The comparison is what decides it, and it points the same way at both ends. The transform is
**1.6%** of the `compute_factor` (2.24 s) that must run before it, and **0.06%** of the
`write_panel_batch` that follows it at the *smallest* of the five write measurements above
(56.7 s). So a numpy implementation of these four steps could at best remove a quantity already
two orders of magnitude below the step before it and three below the step after it — in exchange
for two runtime dependencies, an explicit `float(...)`/`cast(...)` at every public boundary in
the layer, the `NPY`/`PD`/`S` ruff evaluation and the thread-count pinning in Consequence 6.

Two implementation notes, because both are places where pure Python was the *better* answer
rather than merely an adequate one:

- The quantile rule is written out (`panel_factors._quantile`, linear interpolation between
  order statistics) rather than taken from `statistics.quantiles`, whose default is an
  *exclusive* rule returning cut points. Pinning the definition is what lets
  `FactorTransformSpec.min_cross_section` be derived from it — `1 / lower_quantile` — instead of
  chosen.
- The z-score divides by the **population** deviation, computed two-pass with `math.fsum`. The
  one-pass `E[x²] − E[x]²` form cancels catastrophically on a cross section whose values are
  large and close together, which is an ordinary shape for a price-level factor.

`V2-P3-004`'s neutralisation — a cross-sectional regression against industry dummies and market
cap — is still the issue that should re-open this question, and it is still the first workload in
the repository with a matrix in it. The nine runtime dependencies are unchanged.

## Update, 2026-08-12 (`V2-P3-004`): the workload this ADR named arrived, and the matrix dissolved

The Decision above stands unchanged and this section adds no new one. What it records is that
**the consumer this ADR's own Context named — "the factor layer needs cross-sectional regression
(→ coefficients)" — arrived**, three sections after being deferred twice, and was measured with a
real design matrix in front of whoever decides. The runtime dependency set is still nine.

### What the workload actually is

`openalpha_cn.panel_neutralization.apply_factor_neutralization` regresses one `as_of`'s processed
factor cross section on **a complete set of industry dummies plus one market-cap regressor** and
stores the residual. At ADR-0002's stated panel scale that is a 5,534 × 32 design — which is
genuinely the first matrix in this repository, and is why this ADR pointed at it twice.

**It has a closed form, and that is what decides the question rather than any benchmark of
`numpy.linalg.lstsq`.** By the Frisch–Waugh–Lovell theorem the residual of a regression on
`[D, x]`, where `D` is a complete set of group indicators, equals the residual of the
group-demeaned `y` on the group-demeaned `x`. So the whole build is: one pass for the group
means, one for a single slope, one for the residuals. `O(n)`, no matrix formed anywhere.

### Measured, at 5,534 participants over 31 industries

| step | time |
|---|---|
| the closed form (group means + one slope + 5,534 residuals) | **1.6 ms** |
| a dense least-squares solve of the same design, 31 dummies + cap | 143.5 ms |
| the same, intercept + 30 dummies + cap | 152.7 ms |
| `apply_factor_neutralization` end to end (guards, two digests, 5,534 row objects) | **17.4 ms** |

The dense reference is pure Python — Gram matrix, Gaussian elimination with partial pivoting,
`math.fsum` throughout — and it lives in `tests/unit/test_factor_neutralization_rules.py` rather
than in `src/`, because it is an instrument and not a product. **The two agree to 8.88e-16** on
every one of the 5,534 residuals, and so do the two identifications of the dummy set, which is
what makes "the closed form *is* the OLS residual" a comparison rather than an assertion.

Against the steps around it, the whole neutralisation is **0.8%** of the 2.24 s `compute_factor`
that must run before it, **47%** of the 36.7 ms `apply_factor_transform` between them, and
**0.03%** of the smallest of the five `write_panel_batch` measurements after it (56.7 s). A numpy
implementation could at best remove a quantity two orders of magnitude below the step before it
and three below the step after it, in exchange for two runtime dependencies, an explicit
`float(...)`/`cast(...)` at every public boundary in the layer, the `NPY`/`PD`/`S` ruff evaluation
and the thread-count pinning Consequence 6 requires.

### The conditioning argument, stated at the strength it has

Consequence 6 and the ordinary objection to normal equations both point the same way, and the
diagonal of the Gram matrix this design would form is measured — **on a named probe seed, because
the ratio is a property of the draw and not of the design**. On the `_panel(7)` cross section that
`test_the_closed_form_reproduces_a_dense_least_squares_solve` drives, a **level** market-cap
regressor gives a diagonal spanning `151` to `3.55e17`, a ratio of **2.35e15**, within a factor of
ten of double precision's own epsilon; `_panel(19)` gives `149` to `2.05e17` and `1.37e15`. Under
`log` the ratio is 6.3e3 on both. The closed form never forms that matrix — its only division is
by the within-industry sum of squared deviations, one positive number that the `degenerate_design`
coverage code tests directly.

**What is not claimed is that this rescued a failure that was observed.** The dense reference,
solved with pivoting and `fsum`, still agreed to 4.44e-16 on the raw-capitalisation design,
because a dummy block is orthogonal by construction and the effective conditioning is far better
than the diagonal suggests. So the closed form is chosen for its `O(n)` cost and its absent
matrix, and the conditioning is a reason to prefer it rather than a defect it avoided.

### The honest bound on this answer

This is the regression D8 asks for and **not every regression**. A multi-factor risk model with
`k` correlated continuous regressors has no such closed form and would need a real solve; the
question this ADR poses would be genuinely open again there, and nothing measured here carries
over to it. The same is true of `V2-P3-005`'s rank correlation, which is still unwritten.

### One defect found, and it is the one this ADR already records twice

The first measurement of `apply_factor_neutralization` read **148.5 ms**, not 17.4. Two causes,
both of them shapes this repository has paid for before:

- **A pydantic `computed_field` read inside a per-security loop**, for the third time. `_neutralized_row`
  read `spec.neutralization_id` per row — 5,534 `stable_model_id` calls for one cross section, 31 ms.
  The 2026-08-11 section above records `compute_factor` doing this with a build manifest, and
  `V2-P3-003`'s review records it arriving again *inside the fix for something else* (24.4 ms
  against 0.19 ms hoisted). The fix here is a signature: `_neutralized_row` takes both identities
  as `str` and has no object to read one off.
- **A linear lookup inside the same loop.** `IndustryMarketCapCrossSection.get` scans its
  characteristics, so the participant loop was `O(n²)` — 322 ms of a 394 ms profiled build, 82% of
  the call, against 1.6 ms for the regression it feeds. The engine now indexes once; the public
  accessor keeps its linear form, which is right for a caller asking about one security.

The manifest identity is byte-identical before and after both fixes, which is what makes them
optimisations rather than changes.

## Update, 2026-08-12 (`V2-P3-005`): the rank correlation did not need it either

The Decision above stands unchanged and this section adds no new one. What it records is that the
**second** of the two workloads the Context named -- "rank correlation (-> a float information
coefficient)" -- has now been written, and the runtime dependency set is still the nine it was.
That closes both halves of the Context's claim: `V2-P3-004`'s section above answered the
regression, this one answers the correlation, and neither needed numpy or pandas.

`openalpha_cn.backtest.factor_ic` is a standard-library leaf. One `as_of`'s IC is one `sorted()`
per side plus three linear passes; the sort is the whole of the `O(n log n)`, and there is no
matrix, no broadcast and no solve anywhere in the module. Measured at ADR-0002's own whole-market
cross section of **5,534 securities**, best of 25 runs, on the machine that produced the
`V2-P3-004` figures above:

| step, n = 5,534 | time |
|---|---|
| `_pearson` (three linear passes, no sort) | **0.92 ms** |
| `average_ranks` on a continuous cross section (one sort) | 1.30 ms |
| `average_ranks` on a heavily tied one (21 distinct values) | 0.70 ms |
| `spearman` end to end (two `average_ranks` + one `_pearson`) | **3.56 ms** |
| a whole year of daily as_ofs at spearman (244 cross sections) | 0.90 s |
| a five-rung decay curve over that year (244 x 5) | 4.48 s |

The scaling is the `n log n` the sort predicts, and it is measured rather than asserted:
per-element cost rises 0.539 us -> 0.655 us -> 0.836 us across `n = 500`, `5,534` and `55,340`, a
1.55x rise over a 110x rise in `n`.

Two comparisons decide the question, and neither is close:

- **Against the read that feeds it.** `compute_factor` over a 675,148-row partition is 2.24 s
  (the section above). One IC on the cross section that read produces is 3.56 ms, **630x
  smaller**. A year of them is 0.90 s, still under half the cost of the single read.
- **Against the write beside it.** `write_panel_batch` for the same partition spans 56.7 s to
  617.9 s across the five measurements this ADR declines to reduce to one number. A whole year of
  rank ICs is 0.90 s, so even the smallest of the five is **63x** the entire year's correlation
  work.

Paying the Consequences above for 3.56 ms would buy: two runtime dependencies, an explicit
`float(...)`/`cast(...)` at every public boundary of a module whose whole surface is floats, the
`NPY`/`PD`/`S` ruff evaluation, and -- the one that actually costs something here -- the
determinism hazard in Consequence 6. `numpy.argsort` and a BLAS dot product reduce in an order
that depends on thread count, and this module's outputs are compared with `==` in several places:
`ICPoint`'s validator asserts `ic == -raw_ic` exactly, and
`tests/unit/backtest/test_factor_ic.py::test_the_declared_direction_decides_the_sign_and_reaches_the_stability_summary`
asserts `up_summary.mean_ic == -down_summary.mean_ic`. Those identities hold because the
arithmetic is IEEE double in a fixed order; under a threaded reduction they would become
approximations, and the sign convention this issue exists to make readable would stop being
exactly reversible.

### What is *not* claimed

The honest bound the `V2-P3-004` section drew still stands, and this section draws its own beside
it. This is the correlation `V2-P3-005` needs and **not every statistic** P4 might want: a
bootstrap over 244 as_ofs x 1,000 resamples, or a rolling covariance matrix across `V2-P3-008`'s
whole factor set, is a different shape of arithmetic and nothing measured here carries over to it.
The Context's two named workloads are now both answered; the next one to arrive should be measured
the same way rather than assumed to inherit this answer.
