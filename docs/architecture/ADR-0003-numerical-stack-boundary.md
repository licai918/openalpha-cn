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

## Update, 2026-08-12 (`V2-P3-012`): the first factor whose per-security work grows with its reach

`V2-P3-002`'s answer ("no matrix, one scalar call per security") was measured on `reversal_1d`,
whose evaluator performs **one** division whatever its window holds. The momentum-and-reversal
family breaks that assumption: `_compounded_session_return` multiplies one growth factor per
session of the window, so a 120-session momentum does 120 multiplications per security where the
5-session reversal does five. That is a different shape of arithmetic from the one the 2026-08-11
section measured, which is why "the last one did not need numpy" is not an argument on its own,
and it is re-measured here rather than inherited.

### Measured, at ADR-0002's stated panel scale

A synthetic `daily` partition of **5,534 securities × 130 sessions = 719,420 rows**, carrying both
price columns (`close` and `pre_close`), written through the real store and read through
`compute_factor` over the whole cross section at one `as_of`:

| step | cold | warm |
|---|---|---|
| `compute_factor`, `momentum_120_sessions` (125-session reach, 120 multiplications/security) | **2.93 s** | 2.84 s |
| `compute_factor`, `reversal_5_sessions` (5-session reach, 5 multiplications/security) | **2.50 s** | 2.48 s |
| `write_panel_batch` for the same partition | **507.74 s** | — |

All 5,534 observations were `computed` in every run, so these are the cost of a full cross section
rather than of a census of refusals.

### The difference between the two rows is the whole of the answer

The two factors read the identical partition through the identical code path and differ only in
how many terms their product has. The gap is **0.43 s** for `5,534 × 115 = 636,410` extra
multiplications, about **0.7 µs each** — and that gap is the *entire* quantity a vectorised
implementation could attack. Against the 507.74 s write that has to follow it, 0.08%; against the
2.93 s build it sits in, 15%.

So the trade is: remove part of 0.43 s, and take on two runtime dependencies plus every consequence
this ADR lists (the `follow_imports=skip` mypy consequence, the thread-count pinning, the wheel
size). The answer is `V2-P3-002`'s and for a sharper reason than "fast enough" — the term numpy
would shrink is smaller than the run-to-run spread of the step that follows it.
**Runtime dependencies remain nine.**

### The honest bound

This says nothing about `V2-P3-013`'s volatility and liquidity family, whose residual and
idiosyncratic volatility are per-security regressions rather than products, and nothing about
`V2-P3-005`'s rank correlation. Both are open questions that should be measured on their own
workloads — which is the method this section follows rather than the conclusion it reached.

**Correction, 2026-08-12: the sentence above asks the wrong question of `V2-P3-013`.** That
issue measured it and the answer is that there is no regression to cost. A residual volatility
needs a market or factor return series to regress against, and this panel holds none: the
fifteen descriptors include `index_weight` (constituent weights) but no index level anywhere,
and `FactorWindow` carries one security's rows, so an evaluator could not reach a market series
even if one were stored. A single-factor time-series regression is *univariate* — closed form,
`O(n)`, no matrix — so had the regressor existed this section's conclusion would have carried
over unchanged. The blocker is the data and the window's shape, not the numerical stack. The
measurement lives in `panel_factors.py`'s module docstring and is held by
`tests/unit/test_factor_volatility_liquidity.py::test_the_reason_no_residual_ships_is_a_property_of_the_panel_and_of_the_window`,
which turns red the day an index price series arrives. The open question that remains for this
ADR is `V2-P3-004`'s: a risk model with *k* correlated continuous regressors has no closed form.

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
## Update, 2026-08-13 (`V2-P3-006`): the third workload, and it is `Decimal` rather than `float`

The Decision above stands unchanged and this section adds no new one. It records the first
workload to arrive **after** the Context's two were both answered, measured the same way rather
than assumed to inherit their answer -- which is exactly what the section above asked the next one
to do.

`openalpha_cn.backtest.factor_portfolio` cuts a cross section into quantile portfolios and prices
each one's round trip through `AShareExecutionPolicy`. One period is one `sorted()` -- the ranks,
shared with `factor_ic.average_ranks` -- plus **2n** order simulations, each of which builds a
`pydantic` `ExecutionResult` out of four `Decimal` quantizations. Measured at ADR-0002's
whole-market cross section of **5,534 securities**, best of seven runs, on the machine this issue
was built on (which is why the comparisons below are drawn against figures re-read from this
document rather than re-measured beside it):

| step, n = 5,534 | time |
|---|---|
| a whole period's round trips (5,534 buys + 5,534 sells, fees included) | **35.9 ms** |
| the `MarketBar` construction alone, 5,534 bars | 8.5 ms |

Two comparisons, and neither is close:

- **Against the read that feeds it.** `compute_factor` over a 675,148-row partition is 2.24 s, so
  a whole-market period of grouped portfolio returns is **62x** smaller than the single read that
  produces the cross section it consumes.
- **Against its own neighbour on the same plane.** `apply_factor_transform` over the same 5,534
  participants is 35.9-37.6 ms across its four configurations. This is the same order for the same
  cross section, which is the useful reading: the costing step is not a new order of magnitude in
  the factor pipeline, it is one more pass of it.

**The new argument this workload adds is about the dtype rather than the clock.** The money here
is `Decimal`, and it is `Decimal` because `backtest/execution.py` is: a commission is
`max(rate * notional, ¥5.00)` quantized to the cent under `ROUND_HALF_UP`, and a stamp duty is a
statutory rate on a published price. numpy has no `Decimal` dtype: an array of them is an
`object` array, whose arithmetic dispatches to the same Python `Decimal.__mul__` this module
already calls, one element at a time. Whether that is faster or slower than the loop it would
replace is **not measured here and is not the point** -- the point is that the vectorisation this
ADR exists to weigh is structurally unavailable for the type, and the type that *is* vectorised
(`float64`) is the wrong one for a fee schedule whose whole purpose is not to carry a binary
rounding error into a reported return. **Runtime dependencies remain nine.**

### What is *not* claimed

Nothing here says anything about `V2-P3-014`'s report over the whole factor set at once, about
`V2-P3-008`'s pairwise correlation matrix, or about a rolling portfolio with a holdings state
(`V2-P3-007`) -- the last of which has a per-period cost that grows with the *previous* period's
holdings rather than with the cross section, and is a different shape. Each should be measured on
its own workload.


## Update, 2026-08-13 (`V2-P3-008`): the pairwise matrix the section above declined to answer for

The Decision above stands unchanged and this section adds no new one. What it records is the
workload the `V2-P3-005` section named as **out** of its scope by name -- "a rolling covariance
matrix across `V2-P3-008`'s whole factor set" -- arriving and being measured on its own terms
rather than inheriting that section's answer. The runtime dependency set is still the nine it was.

`openalpha_cn.backtest.factor_redundancy` is a standard-library leaf that imports `factor_ic`'s
`average_ranks` and `_pearson` and adds a sorted set intersection per pair. Nineteen factors ship,
so the matrix is **171** pairs. Measured at ADR-0002's whole-market cross section of **5,534
securities**, best of 5 runs, on the machine that produced the figures above:

| step, n = 5,534, 19 factors | time |
|---|---|
| one `spearman` pair, bare arithmetic | 3.49 ms |
| one `spearman` pair through `correlate_cross_section` | **4.97 ms** |
| the whole 171-pair matrix at `pearson` | 0.398 s |
| the whole 171-pair matrix at `spearman` | **0.860 s** |
| a year of daily whole-market matrices (244 x 171) at `spearman` | 210 s |

The 3.49 ms row reproduces the `V2-P3-005` section's 3.56 ms, which is the point of quoting it:
the arithmetic did not change, and the 1.48 ms on top of it is the pair machinery -- the sorted
intersection, the two dict lookups per subject and the census -- rather than a different
correlation. Per-element cost rises 0.530 us -> 0.630 us -> 0.745 us across `n = 500`, `5,534` and
`55,340`, the same `n log n` the sort predicts.

### The optimisation that is 6.3x faster and is wrong

Ranking each factor **once** and correlating the stored rank vectors turns the 171-pair matrix
from 0.860 s into **0.137 s**. It is not taken, and the reason is correctness rather than taste: a
rank is a position within a set, so the ranks of a subset are not the subset of the ranks. Two
factors are admitted for different subjects whenever their coverage differs -- which is the
ordinary case rather than the exception -- and restricting a 40-name rank vector to a 25-name
intersection and correlating disagreed with the honest answer on **200 of 200** random trials, by
as much as **0.100**. `tests/unit/backtest/test_factor_redundancy.py::
test_ranking_the_whole_market_and_restricting_is_not_ranking_the_intersection` drives it.

So the module ranks inside each pair's own intersection and pays the 0.860 s. That is the number
this section is about and the one a reader should compare against.

### The comparison, and it is closer than the two above it

- **Against the writes that produced the inputs.** `write_panel_batch` for one 675,148-row
  partition spans 56.7 s to 617.9 s across the five measurements this ADR declines to reduce to
  one number. Nineteen factors' partitions is nineteen of those. A whole year of the whole
  171-pair matrix is 210 s -- **inside the range of a single partition write**, and 5x to 56x
  smaller than nineteen of them.
- **Against one cross section.** A single as_of's whole matrix is 0.860 s against
  `compute_factor`'s 2.24 s for one factor over one partition. The redundancy analysis over all
  nineteen factors costs less than reading one of them.

**This is not the "630x smaller" the `V2-P3-005` section reported, and the difference is stated
rather than smoothed.** That section measured one IC; this measures 171 correlations, so the
factor between the analysis and the data plane has fallen by two orders of magnitude. 210 s for a
year is a real number and would be worth optimising if it sat in a request path. It does not:
`V2-P3-014` is a report, the matrix is computed once per study rather than once per decision, and
the whole of it fits in the time one of its own inputs takes to write.

### What numpy would buy, and what it would cost here

A 5,534 x 19 rank matrix and one `numpy.corrcoef` call would collapse the 171 pairs into a single
BLAS `syrk` -- and it would be the **unsound** shortcut above, because `corrcoef` over a dense
matrix has no per-pair intersection at all. Recovering the honest answer under numpy means either
171 separate calls on 171 different sub-arrays, which is where the array overhead stops
amortising, or a masked reformulation this repository would then have to test against the plain
one. Paying the Consequences for that is a worse trade than at `V2-P3-005` rather than a better one.

Consequence 6 also binds harder here than there. `RedundancyPoint`'s validator asserts
`oriented_correlation == raw_correlation * s(left) * s(right)` with `==`,
`tests/unit/backtest/test_factor_redundancy.py::
test_a_pairs_correlation_does_not_depend_on_which_side_is_offered_first` asserts
`correlate_cross_section(a, b) == correlate_cross_section(b, a)` exactly, and
`LOCKSTEP_DECIMAL_PLACES` reads a verdict off the **fifteenth decimal place** of a correlation.
All three hold because the arithmetic is IEEE double in a fixed order. Under a threaded BLAS
reduction the first two become approximations and the third becomes a coin flip.

### What is *not* claimed

This is the pairwise matrix over nineteen factors at one `as_of`, and **not** the
eigen-decomposition a principal-components or a risk-model treatment of the same matrix would
need. A 19 x 19 symmetric eigensolve is a different shape of arithmetic -- iterative rather than a
sum -- and nothing measured here carries over to it. If `V2-P4` wants one it should be measured
the same way rather than assumed to inherit this answer, which is now the third time this ADR has
written that sentence and the third time it has been the right one.


## Update, 2026-08-16 (`V2-P3-007`): the rolling portfolio this ADR named as unmeasured

The Decision above stands unchanged and this section adds no new one. What it records is the one
workload the `V2-P3-006` section put in its own "What is *not* claimed" list by name -- "a rolling
portfolio with a holdings state (`V2-P3-007`) -- the last of which has a per-period cost that grows
with the *previous* period's holdings rather than with the cross section, and is a different
shape". It arrived, it was measured on its own terms, and the shape claim turns out to be right and
to matter less than it sounds. The runtime dependency set is still the nine it was.

`openalpha_cn.backtest.factor_tradeability` is a standard-library leaf over `factor_ic` and
`factor_portfolio`. Per period it runs one `rank_groups` (a `sorted()`), one pass over the pairs
and one `min` over the long group's held names; per rebalance it runs two set intersections over
two groups' holdings and sums `Decimal` fee legs. Measured at ADR-0002's whole-market cross section
of **5,534 securities** cut into ten deciles, best of 7 runs, with the two references re-measured
in the same process so the ratios are internally consistent:

| step, n = 5,534, 10 groups | time |
|---|---|
| `TradeabilityStudy.measure` (funnel + per-group + capacity) | **2.7 ms** |
| the same with no liquidity offered (funnel + per-group only) | 2.1 ms |
| `TradeabilityStudy.turnover` over two periods, 553 holdings each | **0.7 ms** |
| *(reference, same process)* `QuantilePortfolioStudy.measure`, bars prebuilt | 58.2 ms |
| *(reference, same process)* constructing 5,534 `MarketBar` pairs | 21.5 ms |

**The whole report is 21x cheaper than the period it reports on.** That is the useful reading and
it is why no further work was done here: a year of daily whole-market periods is 244 x 2.7 ms =
0.66 s of coverage and capacity against 244 x 58.2 ms = 14.2 s of the quantile study that has to
run first, which is itself 62x smaller than the `compute_factor` read before *that*. The absolute
figures are on a different machine from the `V2-P3-006` section's 35.9 ms and are not comparable to
it; the 58.2 ms row exists precisely so the ratio does not have to be.

### The "different shape" claim, and what it actually costs

It is true and it is bounded. A rebalance's cost is `O(|H(k-1)| + |H(k)|)` -- two set intersections
over the two ends' holdings -- rather than linear in the cross section, so a study with a wide long
group pays more per transition than one with a narrow one. At ten deciles of the whole market that
is 553 names a side and the whole two-period series is 0.7 ms, which is **0.012x** one period of
the quantile study it reads. The growth is linear in a quantity a declared `group_count` already
bounds by `n / group_count`, so the worst case over this repository's own inputs is the two-group
cut: 2,767 names a side, still one `set()` construction per end.

### What numpy would buy, and it is less than in any previous section

Nothing here is a reduction over a numeric array. The per-group decomposition is integer counting
into `group_count` buckets; the funnel is five integers read off two censuses; the turnover is two
set differences and a ratio; and the capacity is a `min` and a `sum` over `Decimal`. The `Decimal`
argument the `V2-P3-006` section made applies unchanged and applies to the only money in the module
-- numpy has no `Decimal` dtype, an array of them is an `object` array, and `float64` is the wrong
type for a participation cap multiplied against a published turnover and then compared against a
declared position capital.

Consequence 6 binds on one line: `Rebalance.name_turnover` is an integer ratio and
`tests/unit/backtest/test_factor_tradeability.py::
test_the_resolution_is_the_unit_the_name_turnover_counts_in` divides it by `resolution` and
requires exactly `2.0` back across four group widths. That holds because both sides are small
integers in IEEE double; it is not a claim that would survive a reformulation into a floating
reduction.

### What is *not* claimed

Nothing here says anything about `V2-P3-014`'s report over the whole factor set at once, and
nothing says anything about a rolling portfolio that computes a **return**. This module publishes
turnover and a cost bracket and deliberately no net asset value -- see
`KNOWN_TRADEABILITY_LIMITATIONS.the_rolling_portfolio_is_a_turnover_and_cost_model_and_not_a
_return_series` for the two contract-level reasons -- so the chained-arithmetic workload a net
asset value would introduce has not been measured and does not inherit this answer. That is the
fourth time this ADR has written that sentence.

## Update, 2026-08-20 (`V2-P4-014`): the first **model fit**, and where the line actually falls

The Decision above stands unchanged and this section adds no new one. What it records is that a
workload this ADR's Context never named -- not a factor, a transform, a correlation or a portfolio,
but a *fit* -- has now been written, and the runtime dependency set is still the nine it was.

`openalpha_cn.backtest.alpha_baseline` is a standard-library leaf and is in both per-module
`backtest/` contracts, so `numpy` is forbidden to it by `backtest-studies-touch-no-store` and
`pandas`/`scipy`/`sklearn` by the whole-package one. `CrossSectionalRankModel.fit` learns one
coefficient per declared column -- that column's mean training rank IC -- and
`FittedCrossSectionalRankModel.predict` scores a cross section by the coefficient-weighted sum of
each security's cross-sectional rank position. There is no matrix, no broadcast and no solve
anywhere in the module; every step is `factor_ic`'s own `average_ranks` and `_pearson`, imported
rather than re-implemented.

Measured at ADR-0002's whole-market cross section of **5,534 securities** over three columns,
best of five runs, on the machine that produced the sections above:

| step | time |
|---|---|
| `fit`, 20 training days x 5,534 securities x 3 columns (**real** `OutcomeLabel`s) | **216.4 ms** |
| the same, per training cross section (four sorts: three columns and the targets) | 10.8 ms |
| `predict`, one 5,534-name cross section | **11.4 ms** |
| *(reference, same process)* building the 110,680 real labels the fit consumed | 6.3 s |

The scaling is the `n log n` the sorts predict, and it is measured rather than asserted:
`predict`'s per-element cost rises 1.926 us -> 2.055 us -> 2.766 us across `n = 500`, `5,534` and
`55,340`, a 1.44x rise over a 110x rise in `n` -- the same shape as the 1.55x the `V2-P3-005`
section measured for the rank correlation this module is built out of.

**The fit is 29x cheaper than constructing the labels it fits on**, which is the useful reading and
is why no further work was done here. A whole-market daily walk-forward is bounded below by
`label_outcome`, and by `compute_factor` before that (2.24 s for one 675,148-row partition, in
the `V2-P3-002` section above), and 10.8 ms per training cross section disappears into both.

### Where the line falls, which is not where "expressible" falls

This is the first section in this ADR that **declines** something it could have written, so it is
worth being exact about which.

A **joint** least-squares fit over `p` columns is expressible in the standard library: the normal
equations are a `p x p` Gram matrix and Gaussian elimination with partial pivoting is about thirty
lines. It was not written, and the reason is not the arithmetic. What the standard library does
not offer is a QR, an SVD or an honest condition number -- the things that turn "the solve returned
a number" into "the number means something" -- and this repository's own columns are the
adversarial case for a solve without them: `backtest/factor_redundancy.py` exists because these
factors are correlated, and `V2-P4-012`'s feature grammar stores one factor's `raw`, `processed`
and `neutralized` tiers as three columns of one matrix, which are near-duplicates by construction.
`V2-P4-013`'s own test corpus is the extreme case rather than a hypothetical: its two columns are
exactly rank-anticorrelated, so the Gram matrix there is singular -- measured, determinant zero to
`1e-9` -- and a joint fit has no answer at all, while the marginal one answers `+1` and `-1`.

So the coefficients are **marginal**, each bounded in `[-1, 1]` by construction. That is a real
modelling cost -- two redundant columns are counted twice -- and it is recorded as
`KNOWN_BASELINE_LIMITATIONS`' entry on marginal coefficients rather than hidden. The distinction
this section adds is that the boundary is not between what can and cannot be *computed* without
numpy; it is between a failure mode that is loud and one that is a large coefficient nobody can
tell from a signal. Every previous section answered "the workload did not need the stack". This
one answers "the workload that would have needed it was replaced by one that is bounded, and the
replacement's cost is named".

### What numpy would buy here

For the fit as written, the same thing it would have bought `V2-P3-005`: a constant factor on a
sort, on a workload two orders of magnitude cheaper than the read that feeds it. For the fit that
was *not* written, something real -- `numpy.linalg.lstsq` is a driver over LAPACK's `gelsd`, an
SVD with a rank cutoff, which is precisely the safety net the section above says the standard
library cannot supply. **This is therefore the first section in this ADR where the answer is "it
would buy something", and it is still not an argument to adopt the stack here**: a baseline whose
coefficients can flip sign between folds because two columns were 0.99 correlated is not the
comparison floor Implementation Decision 13 asks for, whatever library computed it.

### What is *not* claimed

`V2-P4-015`'s LightGBM baseline inherits **nothing** from this section. `V2-P4-011` already
measured that it cannot follow the reference model into `backtest/` -- `numpy` is forbidden there
by `backtest-studies-touch-no-store` and the rest by the whole-package contract -- so it has to
argue its own home and its own dependency, against this ADR's Decision rather than around it.
Nothing here says a gradient-boosted tree is expressible in the standard library, and nothing here
should be read as having tried. That is the fifth time this ADR has written a sentence of this
shape.

## Update, 2026-08-20 (`V2-P4-015`): the section that had to answer, and answered no

Every section above records a workload arriving and not needing the stack. This one is different in
kind: `V2-P4-015`'s row **names a library** -- `LightGBM 基线 + 容器修复` -- so "the workload did not
need it" is not available as an answer. The question is whether to take the dependency, and the
Decision above still stands unchanged: **the runtime dependency set is still the nine it was.**

`V2-P4-011` wrote that this issue must argue its own home. That turned out to be the second
question, and it dissolved once the first was answered: `openalpha_cn.backtest.alpha_tree` is a
standard-library leaf, joined both per-module `backtest/` contracts on arrival, and **no contract
was widened** -- `lint-imports` is 8 kept / 0 broken with two source lists one entry longer.

### Why a tree, specifically, does not need what the previous section said stdlib lacks

The `V2-P4-014` section declined a joint least-squares fit because the standard library has no QR,
no SVD and no honest condition number, and because this repository's columns are the adversarial
case: `V2-P4-013`'s corpus has two exactly rank-anticorrelated columns whose Gram determinant is
zero to `1e-9`. That argument does not transfer to a tree, and the reason is one line of the
implementation:

> `_grow`'s only division is by a **count**.

The squared-error gain of a candidate split is `L²/n_L + R²/n_R`, and `min_leaf_securities >= 2`
keeps both denominators at two or more. There is no matrix, no conditioning question and no rank
cutoff to choose. Two perfectly correlated columns make a *solve* undefined; they make a *tree*
pick one and condition the other away at the next node. So the line the previous section drew --
between a failure mode that is loud and one that is a large coefficient nobody can tell from a
signal -- puts a tree on the near side of it with no library at all. **This is the first section in
this ADR where the workload's shape, rather than its size, is the argument.**

A second difference is worth naming because it is about *storage* rather than about arithmetic. A
tree's regularisation is `max_depth`, `tree_count`, `learning_rate` and `min_leaf_securities` --
four flat scalars in `AlphaModelDeclaration.hyperparameters`, so they travel into the artifact and
into `V2-P4-016`'s address. A rank cutoff chosen inside `lstsq` at run time travels nowhere.
(`AlphaModelDeclaration`'s docstring left "widening this is `V2-P4-015`'s call" open; the call is
that four flat scalars fit and the field is **not** widened.)

### Measured, at ADR-0002's whole-market scale

20 prediction days x 5,534 securities x 3 columns = **110,680 pooled rows** built from real
`OutcomeLabel`s, best of three, on the machine that produced the sections above:

| step | time |
|---|---|
| `BoostedRankTreeModel.fit` at `BASELINE_HYPERPARAMETERS` (60 trees, depth 3, 900 encoded nodes) | **4.55 s** (76 ms/tree) |
| the same at 40 trees of depth 3 (600 encoded nodes) | 3.09 s (77 ms/tree) |
| `predict`, one 5,534-name cross section | **94.0 ms** |
| *(the rank baseline, re-measured on the same corpus)* `fit` | 247.8 ms |
| *(the rank baseline, re-measured on the same corpus)* `predict` | 11.3 ms |
| *(reference, same process)* building the 110,680 real labels the fits consumed | 3.62 s |

So the tree fit is **18.4x** the rank baseline's, **2.0x** one `compute_factor` over a 675,148-row
partition (2.24 s), **1.26x** the label build that has to precede it, and **8.0%** of the smallest
of the five `write_panel_batch` measurements (56.7 s). Prediction is **8.3x** the rank baseline's
and still 94 ms for a whole market.

**A claim this section made and then falsified.** A prototype measured 2.62 s and this section was
first drafted saying the fit is *"0.42x the label build"* -- comfortably beneath the step that
feeds it. Re-measured with the shipped implementation the fit is **1.26x** the label build, not
0.42x, and the two figures for the label build differ as well: `V2-P4-014` recorded 6.3 s where
three runs here give 3.62 / 3.72 / 3.73 s. That is the same phenomenon this ADR's own
`write_panel_batch` correction records, arriving on a smaller quantity, and the number quoted above
is the one that reproduced three times in one process. The conclusion survives the correction and
is stated at the strength it now has: a tree fit is the **same order** as the steps already around
it, and an order below the write path.

### What LightGBM would buy, and why it is still not taken

Real things, and they are named rather than waved at: histogram binning in C++ with multithreading
(this implementation is one thread of CPython), leaf-wise best-first growth, L1/L2 leaf penalties,
row and column subsampling, native categorical handling and a learned missing-value branch
direction. What it would cost is the whole of the Consequences above -- a `libgomp1` layer in the
runtime image, an override block entry so `mypy --strict` survives the first `import lightgbm`, the
`NPY`/`PD`/`S` ruff evaluation, and more than one new distribution -- `lightgbm`'s published
metadata requires `numpy` and `scipy`, which is a fact this repository cannot check for itself,
because no test here may reach the network and neither wheel is in `uv.lock`.

The decisive argument is none of those, and it is about what a *baseline* is. Implementation
Decision 13 admits a more complex model only when it beats the floor on a pre-defined
out-of-sample, net-of-cost criterion. A floor with several hundred tunable parameters and a
leaf-wise growth policy is the thing that criterion exists to judge, not the thing it judges
against. The measurement that says this is not rhetorical is the `monotone` corpus: on a target
that rises with one column the rank baseline reads `+1.0000` and the tree reads `+0.9995`, so
**the tree loses to the model it is paired with**, and a floor is a pair rather than a winner.

What the tree adds, on the same fold and through the same `evaluate_fold`: on a target that is the
product of two columns the rank baseline reads `-0.0189` and the tree `+0.8465`, because each
column's marginal rank IC is zero by construction. And on two near-duplicate columns beside one
real one the rank baseline's coefficients come out `[0.2941, 0.2934, 0.9468]` -- the pair carrying
0.588 against the real column's 0.947 -- which is
`KNOWN_BASELINE_LIMITATIONS.the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice`
measured rather than restated; the tree reads `+0.9992` against `+0.9735`.

### The optional extra was considered, and it is worse than either answer

`pyproject.toml` has an `akshare` extra, so shipping something behind a flag has a precedent here.
It is not a precedent for shipping a **baseline** behind one: D13's acceptance gate would become a
gate that a default install cannot open, and a comparison floor nobody can run is not a floor.

The extras route also turned out to be **the way this ADR would actually be reversed**, and that
was measured rather than supposed.
`test_the_optional_and_development_dependency_tables_are_not_a_way_around_the_nine` says in its own
docstring that a numerical stack in `[project.optional-dependencies]` "is the same install by
another name and is forbidden outright" -- and it is a check over *declared names* against eight
distributions. `lightgbm` is not one of the eight, so an extra named after that wheel would have
been waved through -- and the sentence that makes this a measurement rather than a supposition is
about a different extra: **`akshare`** is not one of the eight either, has been in that table
since P1, and reaches `pandas` and therefore `numpy`. The guard has been passing an extra that
carries a numerical stack for as long as it has existed.

`test_a_numerical_stack_cannot_arrive_through_an_extra_that_only_names_its_wheel` closes it over
`uv.lock`'s resolved graph rather than over the declared names, and both halves are measurements:

| install | distributions reached | numerical stack |
|---|---|---|
| the default nine | 25 | **none** |
| `--extra akshare` | 35 | `numpy`, `pandas` |

The first row is what this ADR has asserted nine times and never actually checked -- the nine-name
pin cannot say it, because a numerical stack arrives as somebody else's dependency. The second is
written into `EXTRAS_THAT_CARRY_A_NUMERICAL_STACK` rather than exempted, and the distinction it
draws is this ADR's own: `akshare` is an optional *data provider* whose pandas dependency is the
provider's, which Context item 2 above already records. A model needing a numerical stack would be
this repository's own code depending on one.

### The container half of the row, measured against the current files

The row also names four deployment defects. Three of the four are not what the row says they are,
and the one that is real has nothing to do with LightGBM:

| the row's claim | measured |
|---|---|
| `libgomp1` is installed into the wrong stage | It is in **neither** stage: no `apt-get` appears anywhere in the `Dockerfile`, and `ls /usr/lib/*/libgomp*` in the shipped image finds nothing. Consequence 5's *conclusion* is right and its diagnosis is not. It stays out, because the dependency that links it was not taken. |
| `shm_size` is unset and `/dev/shm` is absent | `/dev/shm` **exists**, writable, at Docker's own 64 MB default -- `grep /dev/shm /proc/mounts` inside the running container. The seam audit's F84 says there is none. |
| the tmpfs is too small | Nothing this image ships writes to `/tmp`: no `tempfile`, no `TMPDIR` consumer anywhere under `src/`. Raising it would be a number nobody measured, for a consumer that does not exist -- the joblib spill F84 names arrives only with the dependency this section declined. |
| OMP threads are unpinned | Already pinned, all five, by `V2-P0B-009`, and held by literal in `tests/unit/test_repository_assets.py`. Consequence 6 is satisfied. |

**The spill that is real belongs to the DuckDB this repository already ships.** DuckDB defaults an
in-memory connection's `temp_directory` to the *relative* path `.tmp`, resolved against the
process's working directory -- and `panel/store.py` opens `duckdb.connect(":memory:")` on every
`write_panel_batch` to stage a partition. With `WORKDIR /app` and `deploy/compose.yml`'s own
`read_only: true`, that directory is on the read-only layer. Reproduced in the shipped image, one
query and one 200 MB memory limit, three working directories:

| working directory | result |
|---|---|
| `/app` (the image layer, read-only) | `IO Error: Failed to create directory ".tmp": Read-only file system` |
| `/tmp` (the 64 MB tmpfs the row asks to enlarge) | `Out of Memory Error: failed to offload data block ... (57.3 MiB/57.5 MiB used). This limit was set by the 'max_temp_directory_size' setting` |
| `/data` (the runtime volume) | the query completes, and `.tmp` exists afterwards |

The middle row is why the row's prescription is the wrong fix and why no number was guessed for it:
**a tmpfs is RAM, so a spill that lands in one has not spilled**, and enlarging 64 MB only moves the
same wall. A spill needs a filesystem and this container has exactly one. The fix is one line --
the runtime stage's last `WORKDIR` is `/data` -- plus `PYTHONSAFEPATH=1`, which is what makes it
safe rather than worse: `python -m uvicorn` prepends the cwd to `sys.path` ahead of site-packages,
so without it a file dropped into the data volume could shadow an installed module (`python -m
site` under `-w /data` prints `/data` as `sys.path[0]`; with the variable, `sys.path` starts at the
stdlib). Both are pinned by tests, and the DuckDB default that makes a working directory
load-bearing at all is pinned against the library rather than against this paragraph -- so the day
DuckDB resolves it absolutely, the argument goes red instead of going stale.

### What is *not* claimed

Nothing here says this implementation reaches LightGBM's accuracy on any real dataset. No such
comparison was run, because running one requires the dependency the decision declined, and that is
recorded as `KNOWN_TREE_LIMITATIONS`'
`this_is_a_histogram_boosting_of_the_kind_lightgbm_does_and_not_lightgbm` rather than left implied.
Nothing here says the three corpora the comparison was taken on mean anything about alpha: they are
noiseless and deterministic, they exist to make a direction flip, and `V2-P4-022` owns the corpus
with a known signal-to-noise ratio. And nothing here says a model whose parameters are learned by
gradient descent over a dense tensor is expressible without the stack. That question is genuinely
open, no issue on this chain has posed it, and nothing measured here carries over to it. That is
the sixth time this ADR has written a sentence of this shape.
