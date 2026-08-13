"""The panel feature computation engine (`V2-P3-002`) and its preprocessing transforms
(`V2-P3-003`): factor observations and their processed twins, on the panel plane.

## Two issues in one module, and the reason that is the right seam

`V2-P3-002` computes a raw factor observation per `(security, as_of)`. `V2-P3-003` turns one
`as_of`'s cross section of those into a winsorized, standardized, missing-value-resolved cross
section that is stored **beside** the raw one and never over it. The second half lives here
rather than in a `panel_factor_transforms.py` of its own, and the reasons are stated at the
strength they actually have -- the first version of this list overstated the first one and is
corrected here:

- **The shared surface is real and is the whole of it.** The two halves share
  `EVENT_TIME_COLUMN`, `FACTOR_PROVIDER_ID`, the dataset-name budget, `_iso`, `_stored_value`,
  `_coverage_code` and `_refuse_to_drop_a_stored_build`, and the processed plane's guards are
  written as mirrors of the raw plane's (`_refuse_a_processed_panel_that_does_not_own_its_rows`
  against `_refuse_a_source_panel_that_does_not_own_its_observations`,
  `_refuse_two_applications_of_one_transform_at_one_as_of` against
  `_refuse_two_builds_of_one_factor_at_one_as_of`). A split means duplicating those or importing
  them across a seam, and it puts each mirror a file away from the thing it mirrors.
- **A split module would add a row to `tests/unit/test_panel_ingest_import_isolation.py`'s
  dependency table**, because `apply_factor_transform` consumes a `FactorPanel` and
  `write_processed_factor_panels` files its output under a dataset name built from the same
  `FactorDefinition`.
- **What this list used to claim about a *second* allowlist is false for most of this code, and
  saying so is worth more than the argument was.** It read that a split module would also have
  to join `tests/unit/panel/test_visible_read_callers.py`'s `FILTERED_READ_CALLERS`, because
  reading a processed partition back has the raw one's mid-year problem. That holds for exactly
  two functions -- `load_processed_factor_observations` and `load_factor_transform_manifests` --
  which with the requirements and decoders they need are the 268 lines below
  "reading the processed plane back", out of the 1,809 this half occupies. The contracts, the
  four estimators, `apply_factor_transform` and both batch builders call no store method at all,
  so a module holding them would widen no allowlist at all. And a name added to
  `FILTERED_READ_CALLERS` is not a guard relaxed: that constant's own docstring says "adding a
  name here is a deliberate act with a review attached, which is the property this test exists
  to create" -- the same designed extension path `KNOWN_STORAGE_LIMITATIONS` is, and the one
  this issue used to extend it.
- **The size is stated rather than excused.** At 4,214 lines this module is the largest in
  `src/` (`providers/tushare.py`, 3,867, is now second) and larger than `panel_ingest.py`'s
  3,221, so "in family" -- which this list said before the transform half landed -- is not true.
  The seam a split would fall on is the one the third bullet found: the arithmetic and the
  writers on one side, the two filtered readers on the other. That is not the raw/processed seam
  this section is about, which is why it is not taken here; `V2-P3-004`'s neutralisation is the
  next issue to add to this file and the point at which the trade should be re-taken with a
  third transform's worth of evidence rather than a second's.

What is *not* shared is the storage: a processed observation is a different record type in a
different pair of datasets, which is D8's "与原值分离" and is argued in
`domain/factor_transform.py`.

## Where the output goes, and why that is structural rather than a convention

`V2-P3-002` says factor observations write the **panel** plane and are **forbidden** from
`ParquetEvidenceStore`. Both halves are load-bearing and neither is enforced by hope:

- The panel side is the ordinary one. An observation batch is a `ColumnarPanelBatch` written
  through `panel_ingest.write_panel_batch`, so it gets the same partition layout, the same
  content hash, the same `PartitionCoverage` and the same readiness contract every other
  dataset gets. Nothing about the factor plane is a second storage format.
- The evidence side is a **type boundary**, not a rule. `ParquetEvidenceStore.append` takes
  `tuple[EvidenceSnapshot, ...]`, and this module produces `FactorObservation`s, which are not
  `EvidenceSnapshot`s and have no adapter to one anywhere in the tree. A `FactorObservation`
  cannot be handed to that store without somebody first writing the conversion.

  That last sentence is exactly as strong as it sounds and no stronger, so say what it is not.
  `EvidenceSnapshot.kind` is `str(min_length=1, max_length=64)`, so
  `EvidenceSnapshot(kind="factor_observation", ...)` **is** constructible and the store would
  accept it. `evidence/builder.py`'s closed `_NORMALIZERS` table refuses an unknown `kind`, but
  it guards the *normalisation* path from a `ProviderBatch` -- not the store's front door. So
  the structural part is "there is no conversion and no import edge", and the auditable part is
  `tests/unit/panel/test_visible_read_callers.py::
  test_no_top_level_panel_module_can_reach_the_evidence_plane_at_all`, which asserts on the
  live import graph that no `panel_*` module reaches `openalpha_cn.storage` or
  `openalpha_cn.evidence` -- the same graph `test_panel_ingest_import_isolation.py` already
  polices, asked the question this issue owes an answer to. Writing the conversion would mean
  adding an import that a test refuses. That is a *structural* obstacle with a review attached,
  which is the honest description; "impossible" would not be.

## Four datasets per factor, because a manifest is not an observation and a year is a partition

`factor_obs_<key>_v<n>` holds one row per `(security, as_of)`. `factor_manifest_<key>_v<n>`
holds one row per `(build, input partition)`, keyed by `FactorBuildManifest.manifest_id`, which
every observation carries as a column. `V2-P3-003` adds `factor_proc_<key>_v<n>` and
`factor_procmn_<key>_v<n>`, the same pair for processed observations and their transform
manifests; see "The processed plane is a second pair of datasets" below. They are separate
datasets because the second is
per-build rather than per-security -- denormalising it onto the observations would repeat the
same provenance 5,534 times per as_of, and could not represent a factor with a variable number
of input partitions at all.

**Per factor, and that half is a memory budget rather than a taste.** `PanelStore` partitions by
`(dataset, year)` and replaces a partition whole, so everything belonging to one partition has
to reach the store in one call. With one shared `factor_observations` dataset that partition is
*a year of every factor*: `V2-P3-009`..`013` deliver ~17 factors, a whole-market cross section
is 5,534 names and a year of daily as_ofs is 244, which is 22,955,032 observations that must all
be alive at once -- and they are alive several times over, as `FactorObservation` objects, as the
nine Python lists `factor_observation_batch` builds out of them, and as the tuples `to_rows()`
materialises.

Measured with `tracemalloc` over 110,680 real observations (5,534 names x 20 as_ofs) driven
through `factor_observation_batch`, `merge_panel_batches` and `to_rows()`:

| stage                                   | per observation | 1 factor-year | 17 factor-years |
| --------------------------------------- | --------------- | ------------- | --------------- |
| `FactorObservation` objects alone        | 214 B           | 0.3 GB        | 4.9 GB          |
| peak through batch + merge + `to_rows()` | 649 B           | 0.9 GB        | **14.9 GB**     |

Putting the factor in the *dataset name* is the only axis this plane offers for splitting a
partition, and it takes the unit of work from the last column back to the second -- the 1.35e6
figure this module already quoted, without the 17x nobody had multiplied out.

What it does not fix is the `as_of` axis: every as_of of one factor belonging to one year still
has to reach `write_factor_panels` together, at the 0.9 GB peak above. That is stated with its
measurement rather than argued away, and `V2-P3-014` is where a build schedule that respects it
lives.

The manifest dataset's `subject` column holds a `manifest_id` rather than a security.
`trade_cal` sets the precedent (its subject is an exchange): `subject` is the entity the row is
about, and for a manifest row that is the build. It also buys the write guard --
`PartitionCoverage.subjects` is then the set of builds a partition holds, so
`_refuse_to_drop_a_stored_build` can see an overwrite that would destroy one **without reading a
single row**.

## Neither factor dataset has a `DATASET_CADENCE` entry, and that is now asserted

`DATASET_CADENCE` maps a *fetched* dataset to how often its upstream publishes. A derived
dataset has no upstream, so `panel doctor` and `panel_gate` refuse to be asked about one. That
was true of the two datasets this module wrote before and it is true of the two-per-factor it
writes now, so the family is open-ended rather than a pair -- which is exactly the shape that
goes unnoticed. `tests/unit/test_factor_engine_rules.py::
test_the_factor_planes_datasets_are_derived_and_therefore_have_no_cadence` pins it against a
live `FACTOR_DEFINITIONS`, so a factor added by `V2-P3-009`..`013` is covered without anybody
remembering to extend a list. `V2-P3-014`/`015` own the factor-side health report itself.

## The one thing this module reads differently from every other reader

Every loader in `panel_ingest` reads through `PanelStore.read_if_ready`, whose
`not_yet_knowable` check is judged per partition -- and a partition is a year. Roadmap section
11 records what that costs here: a factor cannot be evaluated at a mid-year `as_of` at all,
because the year's own December rows block the whole partition for every `as_of` inside it.
This module therefore reads through `PanelStore.read_visible_at`, which runs the identical rule
table and substitutes a row-level `available_time <= as_of` predicate for that one code. See
that method's docstring for the full argument, `panel/catalog.py::ROW_FILTERABLE_ISSUE_CODES`
for why exactly one code is compensable, and `tests/unit/panel/test_visible_read_callers.py`
for the allowlist that keeps the path from spreading silently.

**The freshness bound that read re-decides is a property of the whole requirement, not of the
partition being projected**, which is what makes a cross-year window computable at all: the loop
below reads one year per call, and a January window naming the previous year would otherwise be
refused for a reach that is a look-back window old by construction. `read_visible_at` pools the
re-decided checks over `requirement.years`, so the bound this engine forces callers to state
bounds the age of the *answer* -- see that method for the measurement, and
`tests/integration/panel/test_factor_engine.py::
test_a_declared_freshness_bound_survives_the_cross_year_window_it_has_to_allow` for both
directions of it.

**The alternative was measured, not assumed.** The cost of *not* filtering is a panel rebuilt
once per `as_of` (P2's technical acceptance put it at 120x a single annual build); the cost of
computing only on year boundaries is that `V2-P3-005`'s IC decay and `V2-P4-013`'s
walk-forward have one observation per year to work with, which is not a research programme.

## What is a coverage code and what is a refusal

`FactorEngineError`'s docstring draws the line: a security with too little history is an
*observation*, and "this build has no answer for anybody" is a refusal. One case sat on the
wrong side of it and is now on the right side.

A build whose **visible panel holds fewer sessions than the factor's own lookback window** can
produce nothing for anybody -- not because of the data, but because of how it was asked. Every
security's session set is a subset of the panel's, so if the panel has 36 sessions and the
factor needs 120, `insufficient_history` for the entire cross section is arithmetic rather than
a finding. `compute_factor` refuses it, and the refusal names the number of years the caller
asked for, because that is where the fault almost always is: the `years` a factor reads are
`requirement.years`, buried behind a mapping behind one of eight mandatory arguments, and a
120-session window evaluated in January needs the *previous* year in that tuple. Measured before
the guard existed: two real partitions, a 120-session factor, `as_of` 2027-01-20, `years=(2027,)`
-- 36 rows read, a census of three `insufficient_history` and zero of everything else, no
exception, no warning, and `write_factor_panels` stored it. `years=(2026, 2027)` computes all
three.

What is *not* refused is a build where the panel is wide enough and the securities are not: a
universe of names that listed last month genuinely has no 120-session momentum, and that is the
answer `insufficient_history` exists to give. The two are distinguishable exactly and cheaply,
which is why one is a refusal and the other is a code.

The same sentence is now true of the **report-period** axis, with a longer lever: a statement
partition is filed by *announcement* year while a reach is counted in report periods, so an
eight-period window can need four announcement years named in `requirement.years`
(`cli.py::PANEL_BUILD_SPAN_TARGETS` is the writer-side half of that fact). See "Two axes" below.

## Two axes, because a filing does not live on the session one

`V2-P3-009`..`011` are all built on filings, and `providers/tushare.py::_announcement_timeline`
dates every statement row at `ann_date` on all four clocks -- so a filing's `event_time` is the
day it was *disclosed*, and disclosure days are neither one-per-period nor one-period-per-day.
An A-share issuer routinely discloses its annual report and its Q1 report together, which under
a session index is two rows of one `(subject, event_time)` key and therefore a refusal of the
whole build; and one period announced twice fills two slots of a window that was meant to count
periods.

So `domain/factor.py::PERIOD_INDEXED_DATASETS` (the four statement endpoints, derived from
`FINANCIAL_STATEMENT_DATASETS`) names the datasets `_read_dataset` takes on the `report_period`
column instead, `FactorWindow` carries both tuples, and a `FactorDefinition` declares each reach
exactly when `required_fields` puts it on that axis. The measurement is
`tests/integration/panel/test_factor_report_periods.py`, which writes one corpus twice -- once
under a session-axis name and once under `income` -- and asserts the first raises and the second
computes. Neither the multiplicity refusal nor the coverage vocabulary was widened for it: two
rows of one `(subject, period)` at one announcement still raise (that is the shape carrying
81.7% of `fina_indicator`'s real duplication), and a period shortfall or a period span overrun
is `insufficient_history`, distinguishable on the stored row by the period window rather than by
a sixth code.

## Coverage is a code, never a bool

`FactorCoverage` has five members and `domain/factor.py` argues each one. The short version:
"could not compute" is not one fact. A security that had not listed yet (`not_in_universe`)
should have no value and reporting a data fault for it would put a permanent false defect on
every historical cross section; a security that listed nine sessions ago
(`insufficient_history`) is a correct answer to a 120-session window; a null column
(`input_missing`) is a fetch problem; a zero denominator (`undefined_value`) is a definition
problem. `V2-P3-005` has to exclude all four from a correlation rather than treat them as
zeros, and only a code set lets it.

## The processed plane is a second pair of datasets, and the transform is a *column* in it

`V2-P3-003`'s "与原值分离" is a storage layout rather than a discipline. A processed value is a
`ProcessedFactorObservation` in `factor_proc_<key>_v<n>`; the raw one it came from stays exactly
where it was, untouched, and the processed row names it by `(source_manifest_id, subject,
as_of)`. There is no code path that writes a processed value into the raw dataset: the row type
there is `FactorObservation`, whose coverage vocabulary has no member a processed value could
take, and `factor_observation_batch` builds its columns from that type alone.

**The factor is the partition axis and the transform is not**, which is forced arithmetic rather
than a preference. `MAX_IDENTIFIER_LENGTH` caps a panel dataset at 63 characters and
`MAX_FACTOR_KEY_LENGTH` is 40, so a name carrying both keys needs at least
`len("fproc_") + 40 + len("_v999") + len("_") + len("_v999") = 57` characters before the
transform key gets a single letter. So one processed partition per factor holds **every**
transform of that factor, and two costs follow, both stated because neither is free:

- a read of one transform opens the rows of all of them and filters in Python
  (`load_processed_factor_observations`), because `read_visible_at` projects columns and does not
  take a predicate. Measured on a 2,000-name cross section: 32.8 ms with one transform in the
  partition and 160.0 ms with eight, for the same 2,000 rows returned -- linear in the stored
  rows and **4.9x** at eight. That loader's docstring carries the table and what `V2-P3-014`
  should do about it;
- a year of one factor's processed observations has to reach `write_processed_factor_panels` in
  one call **across every transform of it**, since `PanelStore.write_partition` replaces a
  partition whole. That is the `as_of`-axis constraint this module already documents, multiplied
  by the number of transforms, and `V2-P3-014`'s build schedule is where it is scheduled around.

## The cross section a transform sees is the source panel's, and nothing else

Winsorization and standardization are cross-sectional: one security's processed value depends on
the other securities at the same `as_of`. That is a look-ahead surface, and it is closed here by
arithmetic rather than by care:

`apply_factor_transform` takes a `FactorPanel` and **no store**. It has no `PanelStore`
parameter, no `as_of` parameter (it uses the panel's own), and reads no row from anywhere -- so
the securities that participate in a cross-sectional statistic are exactly the observations
`compute_factor` produced at that `as_of`, which `V2-P3-002` already established were read
through `PanelStore.read_visible_at` at a declared freshness bound pooled over
`requirement.years`. This layer does not re-open that; it cannot reach it.

Two consequences are asserted rather than argued.
`test_a_transform_introduces_no_security_the_source_panel_did_not_have` pins that the output's
subject set is the input's, and `test_the_processed_values_at_a_mid_year_as_of_are_the_ones_the_
visible_window_implies` derives every expected z-score from the closes that were knowable at
that `as_of` -- so a transform that ever consulted a later row would produce different numbers
rather than a different subject list.

**Only `computed` observations participate in the statistics, and the imputed values do not feed
back into them.** The first half is not a choice: a non-`computed` observation has `value=None`
by a rule with three enforcement points, so there is no number to include, and treating it as
zero is the thing `FactorPanel.values()` exists to refuse. The second half *is* a choice and it
is the load-bearing one -- if a `fill_cross_sectional_median` imputation re-entered the mean and
standard deviation it was derived from, every processed value in the cross section would move
with the *coverage rate*, so the same market on the same day would standardize differently
because one security's filing was late. Measured in both directions by
`test_a_filled_observation_does_not_move_the_statistics_it_was_filled_from`: adding non-computed
observations to a panel leaves every computed security's processed value byte-identical while
moving `transform_manifest_id` (the source digest changed, and it must).

## No numpy, no pandas -- and that is a measurement rather than a preference

ADR-0003 permits both. This module needs neither, and adding them would take the runtime
dependency set from nine to eleven and pull in ADR-0003's recorded mypy consequence
(`follow_imports=skip` plus `warn_return_any` makes every function returning a pandas
expression an error). What the engine actually does is: group rows by `(subject, point)` on that
dataset's own axis, take the last `lookback_sessions` or `lookback_periods` of each subject's
own points, and call one scalar function per subject. On the period axis it also keeps the
greatest visible announcement per key, which is one comparison per row. There is no matrix, no
broadcast, no linear algebra and no cross-sectional regression
-- `V2-P3-004`'s neutralisation is the first issue that has one, and it is the right place to
re-open the question with a real workload behind it. The grouping is a `dict` of tuples over
DuckDB's own row tuples, which is the same shape `panel_ingest`'s loaders already use at panel
scale, and the projection is done in SQL so a factor reading one column of `daily` never
materialises the other eight.

Measured at ADR-0002's stated panel scale rather than argued: a synthetic `daily` partition of
5,534 securities x 122 sessions (675,148 rows) written through the real store, and
`compute_factor` over the whole cross section at one `as_of` -- read, grouping, classification
and evaluation together -- takes **1.95 s** cold and 1.91 s warm, about 2.9 us/row. That half
reproduces: an independent re-measurement on its own partition of the same row count came back
at 1.61 s cold and 1.60 s warm.

The write path dominates it and is where a performance problem on this plane actually is -- but
only the *ordering* is claimed here, and only as wide as the weakest measurement supports. The
absolute figure this docstring first quoted (288 s) did not reproduce: four measurements of that
one quantity now read 288 s, 56.7 s, 234 s (extrapolated from a fifth of the scale) and 617.9 s,
which against the 1.95 s read is 148x, 29x and 317x. So the claim is "at least an order of
magnitude, and two on three of the four", not the flat "two orders of magnitude" this paragraph
carried when 288 s was the only figure. ADR-0003 carries the table and the one defect the
original measurement found (a `computed_field` read inside the per-security loop, which
re-hashed the build manifest 5,534 times).

**`V2-P3-003`'s transforms re-opened the question with their own workload and came to the same
answer, measured rather than inherited.** A winsorization is `O(n log n)` (one sort) and a
z-score, a rank and a median are `O(n)` or `O(n log n)`, which is a different shape of arithmetic
from `compute_factor`'s per-security scalar call -- so "the last one did not need numpy" is not
an argument, and the numbers are:

| workload at 5,534 participants (3% of them holes to fill) | time |
|---|---|
| `apply_factor_transform`, quantile winsorization + z-score | **36.7 ms** (6.6 us/security) |
| the same, mad winsorization + z-score | 35.9 ms |
| the same, quantile + centred rank | 37.6 ms |
| the same, mad + centred rank | 36.9 ms |
| `observation_digest` alone (the canonical-JSON hash of the source cross section) | 2.8 ms |
| `processed_observation_batch` + `transform_manifest_batch` for the same panel | 14.9 ms |

Against the 2.24 s `compute_factor` that has to run first, the whole transform is **1.6%**, and
against the write path that follows it (56.7 s at the smallest of ADR-0003's five measurements)
it is 0.06%. A numpy implementation of the same four steps could at best remove a number that is
already two orders of magnitude below the step before it and three below the step after it, in
exchange for the two runtime dependencies and every consequence ADR-0003 lists. `V2-P3-004`'s
cross-sectional regression is still the issue that should re-open it, and it is still the first
one with a matrix in it.

## The volatility and liquidity family (`V2-P3-013`), and the one thing it could not build

Four definitions ship for it -- `return_vol_60`, `downside_vol_60`, `turnover_60` and
`amihud_60` -- and two judgements are shared by all of them and stated here rather than four
times in their notes.

**No factor in this family is named for a residual, because none of them is one.** The roadmap
line asks for "residual volatility" and "idiosyncratic volatility", which are one construct in
the literature: the dispersion of the part of a return that a market or factor model does not
explain. Computing either needs a market return series aligned to the security's own sessions,
and **this panel holds no index or market price series at all** -- the fifteen datasets
`providers/tushare.py` declares are prices, valuations, adjustment factors, four statement
endpoints, calendar, universe, industry tree and membership, index *weights*, suspensions, price
limits and name history, and not one of them carries an index's close. The gap is not only a
fetch away either: `FactorWindow` is one security's own rows, `_classify` is called once per
subject, and an evaluator has no way to reach a different subject's series -- so even a stored
`000300.SH` price row would be invisible to the formula that needed it.

That makes the blocker a **data and engine-shape** one rather than the numerical-stack one
`ADR-0003` warns about, and the distinction is worth recording because the ADR poses the wrong
question for this issue. A single-factor time-series regression `r = a + b*r_m + e` is
*univariate*: `b = cov(r, r_m) / var(r_m)`, the residual deviation is one more pass, the whole
thing is `O(n)` in pure Python and would need neither numpy nor a new machine. It is `k`
correlated continuous regressors that has no closed form, which is the case ADR-0003's own
"honest bound" section names. So nothing here was blocked on arithmetic; what is missing is the
regressor. `return_vol_60` is the total volatility such a residual reduces to when `b*r_m` cannot
be subtracted, `downside_vol_60` adds asymmetry rather than a second estimate of one number, and
both say so in their own notes.

**Nothing in this family reads a neutralised or a processed observation.** `V2-P3-004`'s
cross-sectional residual is a different object from a volatility model's residual -- one number
per `(security, as_of)` rather than a series to take a deviation of -- and it also carries
`V2-P4-026` as a hard precondition, because a neutralisation is not visible inside its own
coverage year. A factor that depended on it would inherit that blocker; these four read raw panel
columns and inherit nothing.

## What is deliberately not here

**Four of the five factor families.** `V2-P3-009`..`012` own value, quality, growth, and momentum
and reversal. `reversal_1d` predates all of them and is the engine's own verification factor
rather than a `V2-P3-012` deliverable -- see `REVERSAL_1D`'s own docstring for what it does and
does not claim.

**No universe loading.** `compute_factor` takes the cross section as an argument rather than
deriving it, because `stock_basic` has exactly the same mid-year readiness problem this module
solves for its own inputs, and answering it for the universe too would put a second policy
decision inside an engine. `V2-P4-004`'s two-stage funnel is where a universe is chosen.

**No `panel_doctor` cadence, no `panel_gate` code.** The factor datasets are derived rather than
fetched, so "how fresh should this be" is a question about the build schedule rather than about
an upstream's publication cadence, and `DATASET_CADENCE` has no honest entry for them.
`V2-P3-014`'s immutable experiment artifacts are where a factor-side health report belongs.
"""

import bisect
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, Literal, Protocol, cast
from zoneinfo import ZoneInfo

from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PRE_CLOSE_COLUMN,
)
from openalpha_cn.domain.factor import (
    FACTOR_DIRECTIONS,
    PERIOD_INDEXED_DATASETS,
    FactorBuildManifest,
    FactorCoverage,
    FactorDefinition,
    FactorDirection,
    FactorField,
    FactorInputProvenance,
    FactorInputRef,
    FactorNote,
    FactorObservation,
    FactorRegistry,
    set_digest,
    validate_factor_observation,
)
from openalpha_cn.domain.factor_transform import (
    MISSING_VALUE_ACTIONS,
    MISSING_VALUE_COVERAGE_ORDER,
    PROCESSED_COVERAGE_CODES,
    PROCESSED_COVERAGE_ORDER,
    STANDARDIZATION_METHODS,
    STANDARDIZATION_NEUTRAL,
    WINSORIZATION_METHODS,
    FactorTransformError,
    FactorTransformManifest,
    FactorTransformRegistry,
    FactorTransformSpec,
    FactorTransformStatistics,
    MissingValueAction,
    MissingValuePolicy,
    ProcessedCoverage,
    ProcessedFactorObservation,
    StandardizationMethod,
    WinsorizationMethod,
    WinsorizationPolicy,
    observation_digest,
    validate_processed_factor_observation,
)
from openalpha_cn.domain.financial_statements import REPORT_PERIOD_COLUMN
from openalpha_cn.domain.panel_batch import (
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    PanelStorageError,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import PanelStore, PartitionRef
from openalpha_cn.panel_ingest import (
    merge_panel_batches,
    split_panel_batch_by_year,
    write_panel_batch,
)

FactorAxis = Literal["session", "period"]
"""Which index a dataset's rows are taken on: a trading session, or a fiscal report period.

Two members and no third, because the axis is a property of the *dataset* and is decided by
`domain/factor.py::PERIOD_INDEXED_DATASETS` -- a closed set derived from the four statement
endpoints. A `Literal` rather than a bool for `FactorCoverage`'s reason: "not a session" is not
one fact, and a third axis (an intraday one, say) would have to be declared here rather than
arrive as the false branch of an `if`.
"""

EVENT_TIME_COLUMN: Final[str] = "event_time"
"""The clock column the engine resolves to a session date.

Used instead of each dataset's own date column (`daily.trade_date`, `income.end_date`, ...)
because it is the one column `ColumnarPanelBatch` writes on every row of every dataset, so the
engine's session grouping is dataset-independent rather than a table of per-dataset date column
names that a new dataset has to be added to. It is a UTC instant and is resolved in
`date_timezone` for exactly the reason `panel_partition_year` is: 08:00 Asia/Shanghai on
1 January is 31 December in UTC.
"""

FACTOR_PROVIDER_ID: Final[str] = "openalpha-cn/panel-factors"
"""What `PartitionCoverage.provider_id` says about a factor partition.

Not a real provider, and the name says so rather than borrowing `"tushare"`: the rows were
computed here, from partitions that name their own providers in
`FactorBuildManifest.inputs`. A coverage record claiming an upstream that never served these
rows would be the kind of plausible-looking provenance `V2-P0B-009` removed elsewhere.
"""

FACTOR_OBSERVATION_DATASET_PREFIX: Final[str] = "factor_obs_"
FACTOR_MANIFEST_DATASET_PREFIX: Final[str] = "factor_manifest_"
"""The two dataset-name prefixes, one per factor. See this module's docstring for the budget.

`MAX_FACTOR_KEY_LENGTH` is sized against the longer of these two plus `"_v999"` so that the
longest declarable factor key still names a legal panel dataset;
`tests/unit/test_factor_engine_rules.py` builds that worst case out of both constants rather
than restating the arithmetic in a comment.
"""

FACTOR_OBSERVATION_KIND: Final[str] = "factor_observation"
FACTOR_MANIFEST_KIND: Final[str] = "factor_build_manifest"

FACTOR_COVERAGE_ORDER: Final[tuple[FactorCoverage, ...]] = (
    "computed",
    "not_in_universe",
    "insufficient_history",
    "input_missing",
    "undefined_value",
)
"""The coverage codes in reporting order, restated as a tuple for a stable census key order.

Reconciled against `domain/factor.py::FACTOR_COVERAGE_CODES` by
`tests/unit/test_factor_engine_rules.py`, so the two copies cannot drift -- the same treatment
`panel_fixtures.STATEMENT_DATASETS` gets against the domain's own tuple.
"""


def factor_observation_dataset(definition: FactorDefinition) -> str:
    """The panel dataset one factor's observations are filed under.

    A function of the definition rather than a constant, because the factor is the partition
    axis this plane has; see this module's docstring. Built from `key` and `version` rather than
    from `factor_id` so that a directory listing of the store says what the rows are about --
    the same reason `factor_key` and `factor_version` are stored beside the opaque `factor_id`
    on every observation row.
    """
    return f"{FACTOR_OBSERVATION_DATASET_PREFIX}{definition.key}_v{definition.version}"


def factor_manifest_dataset(definition: FactorDefinition) -> str:
    """The panel dataset one factor's build manifests are filed under."""
    return f"{FACTOR_MANIFEST_DATASET_PREFIX}{definition.key}_v{definition.version}"


FACTOR_OBSERVATION_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "factor_id",
    "factor_key",
    "factor_version",
    "value",
    "coverage",
    "manifest_id",
    "input_row_count",
    "input_session_first",
    "input_session_last",
    "input_period_first",
    "input_period_last",
)
"""One stored observation, column by column, answering `V2-P3-002`'s six-part acceptance.

`subject` (the security) and the four clocks are added by `ColumnarPanelBatch` itself, so the
six the acceptance names land as: **subject** -> `subject`; **as-of** -> `event_time` /
`available_time`, which for a derived row are both the `as_of` the build was made at;
**value** -> `value`, null unless `coverage` is `computed`; **coverage marker** -> `coverage`,
one of five codes; **input reference** -> `input_row_count` and the two window pairs for the
rows, and `manifest_id` for the partitions; **build manifest** -> `manifest_id`, resolvable in
this factor's own `factor_manifest_<key>_v<n>`.

**Two window pairs rather than one**, because a factor can be on both axes at once and one pair
of columns would then hold two kinds of date for one row. Both are null on an observation whose
factor does not read that axis, which is most observations on either -- `FactorDefinition`'s own
equivalence is what makes "null here" mean "not on this axis" rather than "unrecorded".

`factor_key` and `factor_version` are stored beside `factor_id` even though the ID determines
them, because `factor_id` is opaque: a reader querying the partition directly (which is what
`V2-P3-002` exists to make possible) would otherwise need this build's registry to know what
the rows are about.

**`direction` is stored too, on the manifest rather than on every row.** That argument is
stronger for `direction` than for the key it was written about: a reader who cannot see which
end of the cross section is the good one cannot read the *sign* of these numbers, and a rank
correlation of `-0.03` is evidence for a `lower_is_better` factor and against a
`higher_is_better` one. It goes on `FactorBuildManifest` beside `lookback_sessions` and
`max_window_sessions`, which is where a build's declared parameters live, because it is one fact
per build and putting it on the row would repeat it 5,534 times per as_of for nothing.
"""

FACTOR_OBSERVATION_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *FACTOR_OBSERVATION_DATA_COLUMNS,
)
"""What a reader asks for, and the positional contract of the rows back."""

_OBSERVATION_COLUMN_KINDS: Final[Mapping[str, PanelColumnKind]] = MappingProxyType(
    {
        "factor_id": "string",
        "factor_key": "string",
        "factor_version": "integer",
        "value": "float",
        "coverage": "string",
        "manifest_id": "string",
        "input_row_count": "integer",
        "input_session_first": "string",
        "input_session_last": "string",
        "input_period_first": "string",
        "input_period_last": "string",
    }
)

FACTOR_CENSUS_COLUMN_PREFIX: Final[str] = "census_"

FACTOR_CENSUS_COLUMNS: Final[tuple[str, ...]] = tuple(
    f"{FACTOR_CENSUS_COLUMN_PREFIX}{code}" for code in FACTOR_COVERAGE_ORDER
)
"""One stored count per declared coverage code, derived from the vocabulary rather than listed.

`FactorPanel.coverage_census()` is the only thing that says whether a build answered anybody,
and it speaks **only when a caller asks it** -- which nothing does today, because `V2-P3-014`
and `015` are the faces that would. A build in which every observation is `insufficient_history`
or `input_missing` therefore reached Parquet looking exactly like one that scored the whole
market. Storing the census puts the answer where a reader of the partition meets it, at a cost
of five integers per input row rather than per observation.

Derived from `FACTOR_COVERAGE_ORDER` so that a sixth coverage code gets a column without anybody
remembering to add one; `tests/unit/test_factor_engine_rules.py` asserts the correspondence in
both directions.
"""

FACTOR_MANIFEST_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "factor_id",
    "factor_key",
    "factor_version",
    "as_of_time",
    "date_timezone",
    "code_commit",
    "direction",
    "lookback_sessions",
    "max_window_sessions",
    "lookback_periods",
    "max_window_periods",
    "subject_count",
    "subject_digest",
    "universe_count",
    "universe_digest",
    *FACTOR_CENSUS_COLUMNS,
    "input_dataset",
    "input_year",
    "input_batch_digest",
    "input_partition_hash",
    "input_visible_rows",
    "input_withheld_rows",
)
"""One `(build, input partition)` pair. The build's own fields repeat across its input rows.

Flat rather than nested because a partition is a rectangle: `FactorBuildManifest.inputs` is a
variable-length tuple, and the only alternatives are a JSON blob in one column (which the panel
plane exists to stop) or a second manifest dataset. Repetition across two or three input rows
per build is the cheaper of the three, and `manifest_id` reassembles them.

`input_batch_digest` is the one column here that is **not** a field of the hashed manifest: it
comes from `FactorPanel.input_provenance`, because a digest that moves on every re-fetch cannot
be part of a reproducible content address. See `domain/factor.py::FactorInputProvenance`. It is
stored all the same, in the same row, next to the hash that is in the identity -- recorded and
out of the address, which is the arrangement `built_at` has.
"""

FACTOR_MANIFEST_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *FACTOR_MANIFEST_DATA_COLUMNS,
)

_CENSUS_COLUMN_KINDS: Final[dict[str, PanelColumnKind]] = {
    name: "integer" for name in FACTOR_CENSUS_COLUMNS
}

_MANIFEST_COLUMN_KINDS: Final[Mapping[str, PanelColumnKind]] = MappingProxyType(
    {
        "factor_id": "string",
        "factor_key": "string",
        "factor_version": "integer",
        "as_of_time": "timestamp",
        "date_timezone": "string",
        "code_commit": "string",
        "direction": "string",
        "lookback_sessions": "integer",
        "max_window_sessions": "integer",
        "lookback_periods": "integer",
        "max_window_periods": "integer",
        "subject_count": "integer",
        "subject_digest": "string",
        "universe_count": "integer",
        "universe_digest": "string",
        **_CENSUS_COLUMN_KINDS,
        "input_dataset": "string",
        "input_year": "integer",
        "input_batch_digest": "string",
        "input_partition_hash": "string",
        "input_visible_rows": "integer",
        "input_withheld_rows": "integer",
    }
)


class FactorEngineError(RuntimeError):
    """Raised when a factor cannot be computed at all, as opposed to not being computable for
    one security.

    The split is the point. A security with too little history is an *observation* carrying
    `insufficient_history`; a blocked input partition, a definition with no evaluator, a
    requirement that does not require the columns the factor reads, or a dataset serving two
    rows for one `(subject, session)` are all "this build has no answer for anybody", and a
    build that returned a panel of `input_missing` for those would be a fail-open dressed as
    coverage.

    A `RuntimeError` rather than a `ValueError`, matching `PanelStorageError`: these are states
    of the store and of the wiring, not malformed values. `domain/factor.py`'s `FactorError`
    stays the `ValueError` for a malformed definition or observation.
    """


# --- the window an evaluator sees ------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorWindow:
    """One security's complete, session-ordered inputs over the lookback window.

    An evaluator never sees a partial window: `_classify` reaches it only after every required
    `(dataset, column)` has been proved present and non-null on every session of the window, so
    a formula can index `series(...)[-1]` and `series(...)[-2]` without a guard. That is a
    deliberate division of labour -- "is the data there" is the engine's question and has a
    coverage code for its answer, "what does the data mean" is the factor's.

    `sessions` is ascending and has exactly `lookback_sessions` entries, so `[-1]` is the most
    recent session that was knowable at `as_of` -- not necessarily `as_of`'s own calendar date,
    because a session publishes after its close (`daily` at 16:30 Asia/Shanghai) and an `as_of`
    at noon sees yesterday's.

    `periods` is the same statement about the other axis: ascending, exactly `lookback_periods`
    entries, and `[-1]` is the most recent report period the security had **announced** by
    `as_of` -- which is not the period of its most recent announcement, because a security may
    announce an earlier period after a later one. Empty for a factor that reads no filing, as
    `sessions` is empty for one that reads only filings; a factor declares each axis exactly when
    it reads it, so an evaluator indexing `periods` on a definition that declared no period reach
    is indexing an empty tuple and fails loudly rather than silently.
    """

    subject: str
    as_of: datetime
    sessions: tuple[date, ...]
    periods: tuple[date, ...]
    values: Mapping[tuple[str, str], tuple[float, ...]]

    def series(self, dataset: str, column: str) -> tuple[float, ...]:
        """`dataset.column` over that dataset's own axis, aligned index for index.

        Aligned to `sessions` for an ordinary panel dataset and to `periods` for one of
        `PERIOD_INDEXED_DATASETS`, which is what lets `V2-P3-009`'s EP divide a filing by a price
        without the two having to live on one index. `FactorDefinition.session_datasets` and
        `period_datasets` say which is which, and an evaluator that wants to know can ask its own
        definition rather than this window.

        Raises `FactorEngineError` for a column the definition did not declare, rather than
        `KeyError`: an evaluator reaching for an undeclared column is a definition whose
        `required_fields` is wrong, which is the field `V2-P3-002`'s coverage check is built on.
        """
        try:
            return self.values[(dataset, column)]
        except KeyError:
            raise FactorEngineError(
                f"this factor did not declare {dataset}.{column} in required_fields, so the "
                f"engine did not read it; this window carries "
                f"{sorted(f'{name}.{item}' for name, item in self.values)}"
            ) from None


class FactorEvaluator(Protocol):
    """The formula half of a factor: a complete window in, a number or `None` out.

    `None` means *undefined* -- a zero denominator, a logarithm of a non-positive number -- and
    becomes `undefined_value`. It does not mean "missing data": the engine has already proved
    the data is there before an evaluator is called. A non-finite return (`inf`, `nan`) is
    treated identically to `None`, because an evaluator that computes its way to `inf` has said
    the same thing less deliberately.

    Kept out of `FactorDefinition` because a definition must survive `model_dump(mode="json")`
    to be content-addressed and a callable does not. The two tables are bound at run time; see
    `FACTOR_EVALUATORS`.
    """

    def __call__(self, window: FactorWindow) -> float | None: ...


# --- the one definition that ships, and its evaluator ----------------------------------------


REVERSAL_1D: Final[FactorDefinition] = FactorDefinition(
    key="reversal_1d",
    version=1,
    family="momentum_reversal",
    direction="lower_is_better",
    required_fields=(FactorField(dataset="daily", column="close"),),
    lookback_sessions=2,
    max_window_sessions=2,
    lookback_periods=None,
    max_window_periods=None,
)
"""The single registered factor, chosen to depend on `daily` and nothing else.

Three properties made it the right verification subject and each was a choice: it reads one
column, so a coverage check has exactly one way to fail and the test that provokes
`input_missing` can be pointed at it; its lookback is 2, the smallest window for which
`insufficient_history` is reachable at all (a 1-session window is satisfied by any security
with one row); and its formula has a denominator, so `undefined_value` is reachable rather than
declared and never emitted. A factor with no possible undefined result would have made that
code a table entry with no branch behind it -- which is the exact drift `V2-P0A-001`'s AST
validation and `panel build`'s `_audit_written_partitions` were both added to close.

**It declares no report-period reach, and that is a fourth property rather than an omission.**
`FactorDefinition` requires each axis to be declared exactly when `required_fields` puts the
factor on it, so `lookback_periods=None` here is the contract's own statement that a close-to-
close return reads no filing. The period axis is exercised against definitions the tests declare,
which is where a factor that reads `income` belongs until `V2-P3-009` ships one for research
reasons rather than for engine-verification ones: shipping a `fina_indicator` probe in this
registry would put a factor in `FACTOR_DEFINITIONS` that no issue owns and that
`V2-P3-008`'s redundancy analysis would then group.
"""

REVERSAL_1D_NOTE: Final[FactorNote] = FactorNote(
    subject=REVERSAL_1D.qualified_key,
    summary=(
        "The engine's verification factor: one session's close-to-close simple return, "
        "close[t] / close[t-1] - 1, over the two consecutive sessions most recently knowable "
        "at as_of. It "
        "exists to exercise V2-P3-002 end to end against a daily-only input and is not one of "
        "V2-P3-009..013's deliverables; V2-P3-012 owns the momentum and reversal family and "
        "will not be built on top of this. The declared direction is the family's conventional "
        "prior -- a lower recent return is taken to be the better one -- and is a declaration "
        "this repository has measured nothing about; V2-P3-005 is where an IC would say "
        "anything, and V2-P3's own gate records that most first-batch factors being "
        "insignificant is the expected result."
    ),
)
"""`REVERSAL_1D`'s prose, out of `factor_id`.

Word for word what the definition's own `summary` field carried until this change, moved rather
than rewritten so the diff shows a relocation and not an edit. It is *outside* the content
address now, so fixing a typo in it moves nothing -- which is the whole point, and is measured in
`tests/unit/domain/test_factor.py`. See `domain/factor.py::FactorNote`.
"""


def _reversal_1d(window: FactorWindow) -> float | None:
    """`close[t] / close[t-1] - 1`, or `None` when the prior close is zero.

    `daily_prices.DAILY_PRICE_COLUMNS` records that nineteen sessions spanning 2001-2026 (58,055
    bars) carried no null and no non-positive close, and `daily_bars_from_panel_rows` refuses
    one, so a zero prior close is not reachable through this repository's own writers today.
    The guard is here anyway and is tested directly on this function: `undefined_value` has to
    be a branch that runs, and a factor engine whose only division was unguarded would be one
    whose first real quotient factor (`V2-P3-009`'s EP, whose denominator is a price, and BP,
    whose numerator can be negative) discovered the question in production.
    """
    closes = window.series("daily", "close")
    previous = closes[-2]
    if previous == 0.0:
        return None
    return closes[-1] / previous - 1.0


# --- `V2-P3-013`: the volatility and liquidity family -----------------------------------------
#
# See this module's docstring section "The volatility and liquidity family" for the two
# judgements the whole family rests on -- why no factor here is named for a residual, and why
# every one of them is 60 sessions wide.


VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS: Final[int] = 60
"""One A-share quarter, and the reach every `volatility_liquidity` factor declares.

**Not calibrated on any fixture, and not calibrated on this repository's data either**, which is
the honest statement of what it is: nothing here has measured the sampling properties of an
A-share turnover or Amihud series, so a window justified by "the estimator's variance" would be a
number read off a formula fed with a parameter nobody has measured. What decides it is three
facts that need no data of ours:

- **It is a quarter.** 2024 held 242 sessions (`domain/daily_prices.py::MIN_SESSION_ROW_SHARE`'s
  census), so a quarter is 60.5 of them. Every source definition in this family is stated on a
  calendar horizon -- Amihud (2002) averages the daily ratio over a *year*, Barra's turnover
  descriptors are one month / three months / twelve -- and a quarter is the shortest of the three
  conventional A-share horizons (20 / 60 / 120) that all four of them can be read at.
- **One arithmetic fact that is exact.** The relative standard error of a sample standard
  deviation is `1 / sqrt(2(N-1))` under normality: 16.4% at 20 sessions, **9.2% at 60**, 6.5% at
  120. Sixty is where that crosses ten percent, and doubling it again buys 2.7 points.
- **One horizon across the family, deliberately.** `V2-P3-008`'s redundancy analysis groups by
  family, and two factors measured over different horizons are partly measuring the horizon. With
  one reach for all four, a correlation between two of them is a statement about their content.

Chosen before any test was written and never moved to make one pass, which is the failure mode
this repository has already paid for: a delivery whose proof hangs on a free parameter.
"""

VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS: Final[int] = 80
"""How far a 60-session window here may be stretched by halts: 20 panel sessions of slack.

`FactorDefinition.max_window_sessions` is the count's span bound, and the slack -- the difference
between the two -- is exactly the number of sessions the security may have missed inside its own
window. Both ends of the range are decided rather than one:

- **Not zero.** Equality is `REVERSAL_1D`'s setting and is right for a one-session return; here it
  would report `insufficient_history` for every name that took a single announcement halt in the
  quarter. `domain/daily_prices.py` measures the base rate: on the ordinary session 2024-06-28,
  `adj_factor` served 5,387 rows against `daily`'s 5,338, and **26** of the 49-name difference
  were listed-and-halted names rather than delisted ones -- half a percent of the market halted on
  one unremarkable day, each of them for some number of consecutive sessions.
- **Not more than a trading month.** 2024's 242 sessions are 20.2 a month, so 20 sessions of slack
  is "this factor tolerates up to one month of halt inside its quarter". At 80 panel sessions the
  window already reaches across four calendar months; wider than that, a value labelled as the
  quarter ending at `as_of` is a statement about a different quarter.

The bound is separable from the count rather than merely declared, which is the `V2-P3-004`
review's lesson (a column asserted on a fixture that cannot tell two answers apart):
`tests/integration/panel/test_volatility_liquidity_family.py::
test_the_span_bound_is_separable_from_the_count_at_its_own_boundary` drives 79, 80 and 81 panel
sessions over the same 60 own-sessions and gets `computed`, `computed`, `insufficient_history`.
"""

AMOUNT_COLUMN: Final[str] = "amount"
"""`daily`'s session turnover column, **in thousands of yuan**; see `CNY_PER_AMOUNT_UNIT`."""

TURNOVER_RATE_COLUMN: Final[str] = "turnover_rate"
"""`daily_basic`'s float-share turnover column, in percent. Not `turnover_rate_f`; see
`TURNOVER_60_NOTE` for the measurement that decides between them.

Spelled here rather than imported because `domain/daily_prices.py` carries these two only inside
its column *tuples*, and a second literal is a second thing to drift.
`tests/unit/test_factor_volatility_liquidity.py::
test_the_columns_this_family_reads_are_columns_the_daily_contract_declares` holds both against
those tuples, which is the binding a shared constant would have bought.
"""

CNY_PER_AMOUNT_UNIT: Final[float] = 1000.0
"""Yuan per unit of `daily.amount`, **measured** rather than taken from an upstream's field list.

Tushare's `daily` publishes `vol` in lots and `amount` in thousands of yuan, and the way to know
that without asking the endpoint is that only one reading of the pair puts the session's implied
VWAP inside the session's own low-high range. On the eleven real rows this repository already
stores in `tests/unit/domain/test_daily_prices.py` -- `000001.SZ`, `600519.SH`, `002736.SZ` and
`000569.SZ`, spanning 2001-01-02 to 2026-06-15 -- `amount * 1000 / (vol * 100)` lands inside
`[low, high]` on all eleven, and the other three readings of the same two columns land outside it
on all eleven, by factors of ten to a thousand. `000001.SZ` on 2026-06-12 is the plain case:
11.1351 against a range of 10.88 to 11.25, where "shares and yuan" would give 1.1135.

`tests/unit/test_factor_volatility_liquidity.py::
test_the_amount_column_is_thousands_of_yuan_and_the_other_readings_are_out_of_range` is that
measurement as an executable one. It matters because `AMIHUD_60`'s value carries the unit: a
factor whose denominator is off by 1,000 is off by 1,000 in every report that quotes it, and
nothing downstream -- a rank IC, a z-score -- would notice, because both are scale-free.
"""


def _session_returns(window: FactorWindow) -> tuple[float, ...] | None:
    """One simple return per session of the window, or `None` when a `pre_close` is zero.

    **`close / pre_close - 1`, and the path matters more than the arithmetic does.**
    `domain/daily_prices.py` measures three ways to compute a session return and one of them is
    wrong: across `000001.SZ`'s 2026-06-12 ex-dividend morning the two correct paths agree to
    2.1e-7 (+2.742230% and +2.742251%) while `close[t] / close[t-1] - 1` answers **-0.530973%**,
    with the sign reversed, and across 37,602 rows of seven session pairs it is wrong by up to
    118.30. Every factor in this family is built on returns, so that is the defect that would have
    reached all of them at once.

    `pre_close` is already restated for whatever corporate action took effect that morning, which
    is why this needs no adjustment factor and reads one row per return rather than two. That has
    a second consequence the whole family rests on: **a window of N sessions yields exactly N
    returns**, because each return is computed inside its own row. A close-to-close path would
    yield N-1 and would make the sample size behind a value one less than the count the definition
    declares -- an off-by-one in a denominator, on every value, silently.

    `None` for a zero `pre_close` becomes `undefined_value`. Unreachable through this repository's
    own writers (`DAILY_PRICE_COLUMNS`: no null and no non-positive value in any of the five
    across 58,055 bars spanning 2001 to 2026, and `daily_bars_from_panel_rows` refuses one), so it
    is driven directly in `tests/unit/test_factor_volatility_liquidity.py` rather than declared --
    `_reversal_1d`'s precedent and for its reason.
    """
    closes = window.series(DAILY_DATASET, CLOSE_COLUMN)
    previous = window.series(DAILY_DATASET, PRE_CLOSE_COLUMN)
    if any(value == 0.0 for value in previous):
        return None
    return tuple(close / prior - 1.0 for close, prior in zip(closes, previous, strict=True))


def _sample_stdev(values: Sequence[float]) -> float | None:
    """The Bessel-corrected standard deviation of `values`, two-pass, or `None` below two.

    **Sample rather than population, which is the opposite of `_population_stdev`'s choice one
    file section away, and both are right.** That one standardises a *cross section*, where the
    values in hand are the whole population being described; this one summarises a *time series
    window*, which is a sample of a process whose mean is estimated from the same 60 numbers. The
    `N-1` is the degree of freedom that estimate costs, and it is also what makes
    `VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS`' quoted `1 / sqrt(2(N-1))` the right formula.

    Two-pass with `math.fsum` for `_population_stdev`'s reason: the one-pass `E[x^2] - E[x]^2`
    form cancels catastrophically on values that are large and close together.

    `None` below two values is unreachable at any reach this family declares -- the engine hands an
    evaluator exactly `lookback_sessions` complete rows -- and is a branch rather than an
    assumption for the reason the zero-denominator guards are.
    """
    count = len(values)
    if count < 2:
        return None
    mean = math.fsum(values) / count
    return math.sqrt(math.fsum((value - mean) ** 2 for value in values) / (count - 1))


RETURN_VOL_60: Final[FactorDefinition] = FactorDefinition(
    key="return_vol_60",
    version=1,
    family="volatility_liquidity",
    direction="lower_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
    ),
    lookback_sessions=VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS,
    max_window_sessions=VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS,
    lookback_periods=None,
    max_window_periods=None,
)
"""One A-share quarter's daily return dispersion. `V2-P3-013`'s "residual volatility" slot."""

RETURN_VOL_60_NOTE: Final[FactorNote] = FactorNote(
    subject=RETURN_VOL_60.qualified_key,
    summary=(
        "The sample standard deviation of the 60 most recent daily simple returns, each one "
        "close / pre_close - 1 computed inside its own session's row -- the path "
        "domain/daily_prices.py measures as correct, and not close[t] / close[t-1] - 1, which "
        "reverses the sign across an ex-rights morning and is wrong by up to 118.30 over the "
        "37,602 rows that module measured. It occupies V2-P3-013's residual-volatility slot and "
        "is deliberately NOT named for a residual. Residual volatility and idiosyncratic "
        "volatility are one construct in the literature -- the dispersion of the part of a return "
        "a market or factor model does not explain -- and neither is computable in this build: "
        "the panel holds no index or market return series at all (its fifteen datasets are "
        "prices, valuations, adjustment factors, statements, calendar, universe, industry, index "
        "weights, suspensions, price limits and name history), and FactorWindow carries one "
        "security's own rows, so an evaluator could not read a market series even if one were "
        "stored. This is the total volatility a residual volatility reduces to when beta times "
        "the market return cannot be subtracted, named for what it is rather than for what the "
        "roadmap line asked for. The declared direction is the low-volatility anomaly's "
        "conventional prior -- a lower recent dispersion is taken to be the better one -- and it "
        "is a declaration this repository has measured nothing about; V2-P3-005 is where an IC "
        "would say anything, and V2-P3's own gate records that most first-batch factors being "
        "insignificant is the expected result."
    ),
)
"""`RETURN_VOL_60`'s prose, out of `factor_id`. See `domain/factor.py::FactorNote`."""


def _return_vol_60(window: FactorWindow) -> float | None:
    """The sample standard deviation of the window's 60 session returns.

    `None` -- hence `undefined_value` -- exactly when `_session_returns` has no answer, which is a
    zero `pre_close`, or when the window holds fewer than two returns, which the engine's own
    window formation makes unreachable at this factor's declared reach.
    """
    returns = _session_returns(window)
    if returns is None:
        return None
    return _sample_stdev(returns)


DOWNSIDE_VOL_60: Final[FactorDefinition] = FactorDefinition(
    key="downside_vol_60",
    version=1,
    family="volatility_liquidity",
    direction="lower_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
    ),
    lookback_sessions=VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS,
    max_window_sessions=VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS,
    lookback_periods=None,
    max_window_periods=None,
)
"""The same quarter's *downside* dispersion. `V2-P3-013`'s "idiosyncratic volatility" slot."""

DOWNSIDE_VOL_60_NOTE: Final[FactorNote] = FactorNote(
    subject=DOWNSIDE_VOL_60.qualified_key,
    summary=(
        "The downside semi-deviation of the same 60 daily returns: the root mean square of the "
        "negative ones, sqrt(sum of min(r, 0)^2 / 60). The divisor is the declared window length "
        "and NOT the number of negative returns in it, so the sample size behind a value is the "
        "one the definition declares rather than a function of the data -- a window with three "
        "down days divides by 60, not by 3. There is no Bessel correction here and there is one "
        "on return_vol_60, and that is a distinction rather than an inconsistency: a variance "
        "around an estimated mean costs a degree of freedom and a second moment about the fixed "
        "threshold zero estimates nothing. It occupies V2-P3-013's idiosyncratic-volatility slot "
        "under exactly the disclosure return_vol_60 carries -- it is not the residual of any "
        "regression, and no residual volatility is computable in this build. What it adds over "
        "return_vol_60 is asymmetry rather than a second estimate of one number: a security whose "
        "60 sessions were all up carries a positive return_vol_60 and a downside_vol_60 of "
        "exactly zero, and the two are equal only by coincidence. The declared direction is the "
        "conventional prior -- less downside dispersion is taken to be better -- and this "
        "repository has measured nothing about it."
    ),
)
"""`DOWNSIDE_VOL_60`'s prose, out of `factor_id`."""


def _downside_vol_60(window: FactorWindow) -> float | None:
    """`sqrt(sum over the window of min(r, 0)^2 / N)`, N being the window's own length.

    Zero is a real answer and not a missing one: a security with no down session in the quarter
    has no downside dispersion, and `FactorObservation` stores it as `computed` with `0.0`. That
    is the case `undefined_value` must *not* be used for, which is why the only `None` here comes
    from the return path itself and from an empty window.
    """
    returns = _session_returns(window)
    if returns is None or not returns:
        return None
    squares = math.fsum(value * value for value in returns if value < 0.0)
    return math.sqrt(squares / len(returns))


TURNOVER_60: Final[FactorDefinition] = FactorDefinition(
    key="turnover_60",
    version=1,
    family="volatility_liquidity",
    direction="lower_is_better",
    required_fields=(FactorField(dataset=DAILY_BASIC_DATASET, column=TURNOVER_RATE_COLUMN),),
    lookback_sessions=VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS,
    max_window_sessions=VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS,
    lookback_periods=None,
    max_window_periods=None,
)
"""Mean float-share turnover over the quarter, in percent. `V2-P3-013`'s turnover deliverable."""

TURNOVER_60_NOTE: Final[FactorNote] = FactorNote(
    subject=TURNOVER_60.qualified_key,
    summary=(
        "The arithmetic mean of daily_basic.turnover_rate over the 60 most recent sessions, in "
        "percent -- the unit is measured on this repository's own stored rows rather than taken "
        "from a field list: 000001.SZ on 2026-06-12 traded 2,032,355.46 lots against a "
        "float_share of 1,940,560.0653 ten-thousand shares, which is 1.0473 percent and is "
        "exactly the stored turnover_rate. It reads turnover_rate and not turnover_rate_f, and "
        "that is a fail-closed choice with a measured consequence rather than a style preference. "
        "domain/daily_prices.py records turnover_rate populated on every one of 51,708 rows "
        "across eighteen sessions spanning 2001 to 2026, while turnover_rate_f sits in "
        "DAILY_BASIC_NULLABLE_COLUMNS -- the writer accepts a null in it, measured at 17 rows "
        "over four sessions between 2001 and 2008 -- and because the engine hands an evaluator "
        "only complete windows, ONE null session is input_missing for every one of the 60 as_ofs "
        "whose window contains it. Its sibling free_share is worse still: 300290.SZ carries a "
        "null on 74 consecutive trading days. Reading turnover_rate_f here would also falsify "
        "that module's own stated reason for the split, that nothing in P3 or P4 reads the "
        "free-float pair, and which columns are fail-closed is V2-P1-007's decision rather than "
        "this issue's. The cost is stated plainly: turnover against float shares understates "
        "trading intensity for a name whose float carries a large strategic holding, and the two "
        "columns are not interchangeable -- 1.0473 against 2.4905 for 000001.SZ and 0.4039 "
        "against 0.9334 for 600519.SH, on the one session this repository stores both for. The "
        "declared direction is the low-turnover prior and this repository has measured nothing "
        "about it."
    ),
)
"""`TURNOVER_60`'s prose, out of `factor_id`."""


def _turnover_60(window: FactorWindow) -> float | None:
    """The mean of `daily_basic.turnover_rate` over the window, in percent.

    No guard on the values themselves: a turnover rate is a non-negative percentage and a zero one
    is a real answer (a session with a bar and almost no trade), not a missing one. The only
    `None` is the empty window, which the engine's window formation makes unreachable here.
    """
    rates = window.series(DAILY_BASIC_DATASET, TURNOVER_RATE_COLUMN)
    if not rates:
        return None
    return math.fsum(rates) / len(rates)


AMIHUD_60: Final[FactorDefinition] = FactorDefinition(
    key="amihud_60",
    version=1,
    family="volatility_liquidity",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=AMOUNT_COLUMN),
    ),
    lookback_sessions=VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS,
    max_window_sessions=VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS,
    lookback_periods=None,
    max_window_periods=None,
)
"""Amihud (2002)'s illiquidity ratio over the quarter, in `1/CNY`. `V2-P3-013`'s Amihud."""

AMIHUD_60_NOTE: Final[FactorNote] = FactorNote(
    subject=AMIHUD_60.qualified_key,
    summary=(
        "Amihud (2002)'s illiquidity ratio: the mean over the 60 most recent sessions of "
        "|close / pre_close - 1| divided by that session's turnover in yuan. daily.amount is "
        "published in THOUSANDS of yuan, which is measured rather than assumed -- across eleven "
        "real rows this repository already stores, spanning 2001-01-02 to 2026-06-15, "
        "amount * 1000 / (vol * 100) is the only reading of that column pair whose implied VWAP "
        "falls inside the session's own low-to-high range, on all eleven, and each of the other "
        "three readings falls outside it on all eleven -- so the denominator is "
        "amount * CNY_PER_AMOUNT_UNIT and a stored value carries the unit 1/CNY. That unit is "
        "load-bearing and invisible downstream: a rank IC and a z-score are both scale-free, so a "
        "denominator wrong by a factor of 1,000 would reach every report that quotes the number "
        "and no test that only ranks it. A session whose amount is zero or negative makes the "
        "ratio undefined and the whole observation undefined_value, which is the code "
        "FactorCoverage documents for a zero denominator; averaging over only the sessions that "
        "do have turnover was considered and rejected, because it makes the sample size behind a "
        "value a function of the data rather than the declared 60. Amihud's own paper averages "
        "the daily ratio over a year; one quarter is this family's single declared horizon. The "
        "declared direction is the illiquidity premium's conventional prior, higher_is_better, "
        "which agrees in economics with turnover_60's lower_is_better while being its opposite in "
        "sign; both are priors this repository has measured nothing about."
    ),
)
"""`AMIHUD_60`'s prose, out of `factor_id`."""


def _amihud_60(window: FactorWindow) -> float | None:
    """`mean(|r| / (amount * CNY_PER_AMOUNT_UNIT))` over the window, in `1/CNY`.

    `None` -- hence `undefined_value` -- on a zero `pre_close` and on any session whose `amount`
    is not strictly positive. **Any** such session, not the offending one skipped: a mean taken
    over whichever sessions happened to have turnover would be a value whose sample size is a
    function of the data, and this repository has taken eight Critical findings on quantities
    calibrated over a sample nobody declared.
    """
    returns = _session_returns(window)
    if returns is None or not returns:
        return None
    amounts = window.series(DAILY_DATASET, AMOUNT_COLUMN)
    if any(value <= 0.0 for value in amounts):
        return None
    return math.fsum(
        abs(value) / (amount * CNY_PER_AMOUNT_UNIT)
        for value, amount in zip(returns, amounts, strict=True)
    ) / len(returns)


FACTOR_DEFINITIONS: Final[FactorRegistry] = FactorRegistry(
    (REVERSAL_1D, RETURN_VOL_60, DOWNSIDE_VOL_60, TURNOVER_60, AMIHUD_60),
    notes=(
        REVERSAL_1D_NOTE,
        RETURN_VOL_60_NOTE,
        DOWNSIDE_VOL_60_NOTE,
        TURNOVER_60_NOTE,
        AMIHUD_60_NOTE,
    ),
)
"""Every factor this build declares, and the prose about it. `V2-P3-009`..`012` extend both."""

FACTOR_EVALUATORS: Final[Mapping[str, FactorEvaluator]] = MappingProxyType(
    {
        REVERSAL_1D.qualified_key: _reversal_1d,
        RETURN_VOL_60.qualified_key: _return_vol_60,
        DOWNSIDE_VOL_60.qualified_key: _downside_vol_60,
        TURNOVER_60.qualified_key: _turnover_60,
        AMIHUD_60.qualified_key: _amihud_60,
    }
)
"""Every factor this build can actually compute, keyed by `key/vN`.

Two tables rather than one because a definition has to be hashable and a callable is not, and
two tables can drift -- which is the failure this repository has already measured once, in
`panel build`: `PANEL_BUILD_TARGETS` gained keys whose branches did not exist and the command
answered exit 0 with an empty partition list. `_refuse_table_drift` below runs at import and
refuses the module rather than letting a definition with no evaluator reach a caller, and
`compute_factor` refuses again at the call, so an injected evaluator table cannot smuggle the
gap back in.
"""


def _refuse_table_drift(
    registry: FactorRegistry, evaluators: Mapping[str, FactorEvaluator]
) -> None:
    """Refuse a registry and an evaluator table that do not name exactly the same factors.

    Both directions are faults and they fail differently, so both are named. A definition with
    no evaluator is a factor a caller can ask for and nothing can compute -- the shape that
    produced an empty success elsewhere. An evaluator with no definition is a formula with no
    declared identity, lookback or required fields, so nothing could hash it, gate it or
    interpret its sign.
    """
    declared = set(registry.qualified_keys)
    implemented = set(evaluators)
    if declared == implemented:
        return
    raise FactorEngineError(
        f"the factor registry and the evaluator table disagree: "
        f"{sorted(declared - implemented)} are declared with no evaluator and "
        f"{sorted(implemented - declared)} are implemented with no definition. A declared "
        "factor with no implementation is a request that answers successfully with nothing"
    )


_refuse_table_drift(FACTOR_DEFINITIONS, FACTOR_EVALUATORS)


# --- the computed result ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorPanel:
    """One factor at one `as_of`: the manifest, every observation, and the wall clock.

    `built_at` is here and **not** on `FactorBuildManifest`, which is the arrangement roadmap
    section 9 says was wanted and not had for `config_digest`/`random_seed`: the wall clock is
    recorded (it becomes the observation partition's `ColumnarPanelBatch.fetched_at`, hence
    `PartitionCoverage.fetched_at`) and is kept out of the content address, so recomputing the
    same factor from the same partitions at the same `as_of` yields the same `manifest_id`.
    """

    definition: FactorDefinition
    manifest: FactorBuildManifest
    observations: tuple[FactorObservation, ...]
    built_at: datetime
    input_provenance: tuple[FactorInputProvenance, ...]
    """The provider-side digest of each input partition, in `manifest.inputs`' own order.

    Here for `built_at`'s reason and not `manifest.inputs`': `PartitionCoverage.batch_digest`
    hashes the provider batch's `fetched_at`, so a partition re-fetched with byte-identical rows
    carries a different one -- and while it was a manifest field, a build recomputed from
    unchanged inputs got a new `manifest_id` and could then never be written, because a stored
    build may not be dropped. Recorded, stored on the manifest row as `input_batch_digest`, out
    of the content address. See `domain/factor.py::FactorInputProvenance`.
    """

    @property
    def as_of(self) -> datetime:
        return self.manifest.as_of

    def coverage_census(self) -> Mapping[str, int]:
        """How many observations carry each coverage code, including the zeros.

        Every declared code is present with a count, so a report reads "0 undefined_value"
        rather than having to infer it from an absent key -- the same reason
        `DatasetReadiness.checks_waived` names what did not run instead of leaving it out.

        Keyed in `FACTOR_COVERAGE_ORDER`'s declared order rather than alphabetically, which is
        the only thing that constant is for: alphabetical order puts `computed` third and reads
        as an arbitrary list, while the declared order is the precedence `_classify` applies.
        """
        census: dict[str, int] = dict.fromkeys(FACTOR_COVERAGE_ORDER, 0)
        for observation in self.observations:
            census[observation.coverage] += 1
        return MappingProxyType(census)

    def values(self) -> Mapping[str, float]:
        """The computed cross section: subject to value, omitting every non-`computed` code.

        Omitting rather than defaulting is the whole argument for the coverage code set: a
        security that could not be scored is not one that scored zero, and a caller that wants
        it in the frame has to decide what to do with it by looking at `observations`.
        """
        return MappingProxyType(
            {
                observation.subject: observation.value
                for observation in self.observations
                if observation.value is not None
            }
        )


# --- computing ---------------------------------------------------------------------------------


def compute_factor(
    store: PanelStore,
    definition: FactorDefinition,
    *,
    as_of: datetime,
    subjects: Sequence[str],
    universe: Collection[str],
    requirements: Mapping[str, ReadinessRequirement],
    code_commit: str,
    built_at: datetime,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
    evaluators: Mapping[str, FactorEvaluator] | None = None,
) -> FactorPanel:
    """Evaluate `definition` for `subjects` at `as_of`, reading only what was knowable then.

    ## The arguments with no defaults, and why each one refuses to have one

    - **`requirements`**, one `ReadinessRequirement` per dataset the factor reads, is supplied
      by the caller rather than built here. This is `panel_gate`'s argument transplanted: the
      gate may not build its own requirement because "a gate that built its own
      `ReadinessRequirement` could ask a dataset a different question from the one its own
      reader asks, and the two verdicts would drift". The same holds for a factor engine, and
      more sharply -- `daily_requirement` derives `required_dates` from a real calendar and
      clamps them at the session that had published, and an engine inventing its own would
      quietly ask something weaker. Each one is cross-checked here rather than trusted: its
      `dataset` must be the key, its `as_of` must be this `as_of`, and its `required_fields`
      must actually include the columns this factor reads -- otherwise readiness could report
      `ready` for a partition that does not have them and the scan would fail as a binder error
      several layers down.
    - **`universe`** is the cross section at `as_of`, and it is mandatory because
      `not_in_universe` is one of the five answers. A defaulted universe would be a check that
      was never configured reporting as one that passed, which is the rule
      `ReadinessRequirement`'s four fields already follow.
    - **`code_commit`** and **`built_at`** are provenance the panel plane cannot resolve for
      itself: no top-level `panel_*` module may import `runtime`, where
      `resolve_code_commit()` lives. A default of `"development"` is the placeholder
      `V2-P0B-009` deleted.

    ## What blocks, and what becomes a coverage code

    A blocked input partition raises. It does not become `input_missing` for every security:
    the difference between "this dataset is unusable" and "this security has a hole in it" is
    the difference `V2-P1-013`'s acceptance ("assert blocking, not an empty success") exists to
    keep, and a panel of five thousand `input_missing` rows is an empty success with a coverage
    column on it.

    The years read are each requirement's own `years`. A lookback window that would reach
    outside them yields `insufficient_history` rather than a truncated value -- fail-closed, and
    visible in the census rather than in the numbers -- **unless it would do so for the entire
    cross section**, which is a fault in the request rather than an answer about the data and
    raises; see this module's docstring's "What is a coverage code and what is a refusal".

    ## What determines the answers, and therefore what `manifest_id` has to cover

    Every argument here is either represented in `manifest.manifest_id` or is exempt with a
    reason, and `tests/integration/panel/test_factor_engine.py::
    test_every_determinant_of_this_build_is_either_in_the_identity_or_exempted_by_name` reads
    this function's own signature and fails on a parameter that is in neither list. That audit
    exists because varying the fields a model *declares* cannot show that the model declares
    everything that decides the output -- the manifest recorded `subject_count` and
    `universe_count` and not the sets, and two builds over disjoint cross sections shared an
    identity until it did.

    The exemptions, stated rather than left implicit: `store` is a handle whose *content*
    reaches the identity through each input's `partition_content_hash`; `built_at` is the wall
    clock, deliberately out (see `FactorPanel`); `evaluators` is a substitution seam for tests
    whose production value is the module's own table, which `code_commit` stands for; and
    `requirements` decides whether a read is *permitted* rather than what it returns -- the part
    of it that does decide (`years`) arrives in the identity as `manifest.inputs`.
    """
    table = FACTOR_EVALUATORS if evaluators is None else evaluators
    evaluator = _resolve_evaluator(definition, table)
    ordered_subjects = _validated_subjects(subjects)
    _validate_requirements(definition, requirements, as_of=as_of)
    zone = _resolve_timezone(date_timezone)

    readings: dict[str, _DatasetReading] = {}
    inputs: list[FactorInputRef] = []
    provenance: list[FactorInputProvenance] = []
    for dataset in definition.datasets:
        reading, refs, digests = _read_dataset(
            store,
            dataset=dataset,
            columns=definition.columns_of(dataset),
            requirement=requirements[dataset],
            zone=zone,
        )
        readings[dataset] = reading
        inputs.extend(refs)
        provenance.extend(digests)

    panel_sessions = _panel_axis_points(readings, axis="session")
    panel_periods = _panel_axis_points(readings, axis="period")
    _refuse_a_panel_narrower_than_the_lookback(
        definition,
        panel_sessions=panel_sessions,
        panel_periods=panel_periods,
        requirements=requirements,
    )
    manifest = FactorBuildManifest(
        factor_id=definition.factor_id,
        factor_key=definition.key,
        factor_version=definition.version,
        as_of=as_of,
        date_timezone=date_timezone,
        code_commit=code_commit,
        direction=definition.direction,
        lookback_sessions=definition.lookback_sessions,
        max_window_sessions=definition.max_window_sessions,
        lookback_periods=definition.lookback_periods,
        max_window_periods=definition.max_window_periods,
        subject_count=len(ordered_subjects),
        subject_digest=set_digest(ordered_subjects),
        universe_count=len(set(universe)),
        universe_digest=set_digest(universe),
        inputs=tuple(inputs),
    )
    listed = set(universe)
    # Read once, outside the loop. `manifest_id` is a pydantic `computed_field`, and
    # `domain/panel_batch.py` measured what that means on a hot path: a computed field is *not*
    # cached, so `ProviderBatch.payload_digest` cost 10.5 ms on its first access and 10.2 ms on
    # its second. Leaving `manifest.manifest_id` inside the comprehension would re-canonicalise
    # and re-hash the whole manifest once per security -- 5,534 times for a whole-market cross
    # section, for a value that cannot change while this loop runs.
    manifest_id = manifest.manifest_id
    observations = tuple(
        _classify(
            definition,
            subject=subject,
            as_of=as_of,
            in_universe=subject in listed,
            readings=readings,
            panel_sessions=panel_sessions,
            evaluator=evaluator,
            manifest_id=manifest_id,
        )
        for subject in ordered_subjects
    )
    return FactorPanel(
        definition=definition,
        manifest=manifest,
        observations=observations,
        built_at=built_at,
        input_provenance=tuple(provenance),
    )


@dataclass(frozen=True, slots=True)
class _DatasetReading:
    """One dataset's visible rows, indexed on that dataset's own axis.

    `points_by_subject` and `values` are keyed by a session for an ordinary dataset and by a
    report period for one of `PERIOD_INDEXED_DATASETS`; `axis` says which, so `_classify` never
    has to consult a dataset name. One reading type rather than two, because everything after the
    read is the same arithmetic on a different index -- and two types would mean two copies of
    `_stored_rows`, `_complete_series` and the window formation.
    """

    points_by_subject: Mapping[str, tuple[date, ...]]
    values: Mapping[tuple[str, date], tuple[float | None, ...]]
    columns: tuple[str, ...]
    axis: FactorAxis


def _panel_axis_points(
    readings: Mapping[str, _DatasetReading], *, axis: FactorAxis
) -> tuple[date, ...]:
    """Every point on one axis the visible read returned, across datasets and securities.

    The engine's own calendar for that axis, and the *only* one it has: `compute_factor` is not
    given a `TradingCalendar` and must not build one, for the reason it is not given a universe --
    a second source for "which days were open" is a second thing that can disagree with the
    partition it is reading.

    **On the session axis that is a calendar. On the period axis it is a census, and the
    difference is load-bearing.** Every security in a cross section is quoted on every open day,
    so a session absent from this union is a session the market was closed -- the union *is* the
    trading calendar of the read. Report periods have no such property: a period absent from the
    union means only that nobody in this build filed it, which is one witness away from meaning
    nothing at all. Measured, on one corpus and one security, changing only whether some *other*
    security filed the missing period: `computed` with a fifteen-month "year-on-year" when nobody
    did, `insufficient_history` when one did. So the span check no longer counts on this union for
    periods -- it counts on `FISCAL_QUARTER_ENDS`, which is knowable without reading a row; see
    `_period_span`.

    What still reads it on **both** axes is `_refuse_a_panel_narrower_than_the_lookback`, and
    there the census is exactly the right question: a security's own points are always a subset of
    the panel's, so a panel holding fewer points than the reach makes `insufficient_history` for
    the whole cross section arithmetic. That is a statement about what this read can answer, not
    about what the world contains.
    """
    points: set[date] = set()
    for reading in readings.values():
        if reading.axis != axis:
            continue
        for days in reading.points_by_subject.values():
            points.update(days)
    return tuple(sorted(points))


def _refuse_a_panel_narrower_than_the_lookback(
    definition: FactorDefinition,
    *,
    panel_sessions: tuple[date, ...],
    panel_periods: tuple[date, ...],
    requirements: Mapping[str, ReadinessRequirement],
) -> None:
    """Refuse a build whose visible panel cannot satisfy a declared reach for **anybody**.

    Every security's point set is a subset of the panel's on the same axis, so a panel holding
    fewer sessions than `lookback_sessions` -- or fewer report periods than `lookback_periods` --
    makes `insufficient_history` for the whole cross section a matter of arithmetic. That is
    `FactorEngineError`'s own category -- "this build has no answer for anybody" -- and a panel of
    `insufficient_history` returned for it is the fail-open dressed as coverage that class exists
    to name.

    The message leads with the years, because that is where the fault is: the points a factor can
    see are the ones in `requirement.years`, and a 120-session window evaluated in January needs
    the previous year in that tuple or nothing qualifies. The period axis has the same fault with
    a longer lever -- a statement partition is filed by **announcement** year while its request
    window is a report-period year, so an eight-period reach can need four announcement years
    named -- and `cli.py::PANEL_BUILD_SPAN_TARGETS` is the writer-side half of that same fact.

    What this does **not** refuse is a wide-enough panel over securities that are individually too
    young. That is a real answer and `insufficient_history` is the code for it.
    """
    years = sorted({year for requirement in requirements.values() for year in requirement.years})
    for axis, lookback, points, noun in (
        ("session", definition.lookback_sessions, panel_sessions, "sessions"),
        ("period", definition.lookback_periods, panel_periods, "report periods"),
    ):
        if lookback is None or len(points) >= lookback:
            continue
        raise FactorEngineError(
            f"{definition.qualified_key} needs {lookback} {noun} and the visible panel over "
            f"year(s) {years} holds {len(points)}, so no security in any cross section could "
            "qualify and every observation would be insufficient_history. That is a fault in the "
            "request rather than an answer about the data: widen the `years` of the requirements "
            f"this factor reads -- a {axis} window that spans a year boundary needs the earlier "
            "year named too -- or evaluate at a later as_of"
        )


def _resolve_evaluator(
    definition: FactorDefinition, evaluators: Mapping[str, FactorEvaluator]
) -> FactorEvaluator:
    evaluator = evaluators.get(definition.qualified_key)
    if evaluator is None:
        raise FactorEngineError(
            f"{definition.qualified_key} is declared but has no evaluator; this build can "
            f"compute {sorted(evaluators)}. A declared factor with no implementation would "
            "otherwise produce a panel of observations that all say nothing was computable"
        )
    return evaluator


def _validated_subjects(subjects: Sequence[str]) -> tuple[str, ...]:
    ordered = tuple(subjects)
    if not ordered:
        raise FactorEngineError(
            "compute_factor needs at least one subject; an empty cross section produces an "
            "empty panel that is indistinguishable from one where nothing could be computed"
        )
    if len(set(ordered)) != len(ordered):
        duplicates = sorted({item for item in ordered if ordered.count(item) > 1})
        raise FactorEngineError(
            f"{duplicates} appears more than once in subjects; a duplicated security would "
            "produce two observations of one fact and be counted twice in every census"
        )
    return ordered


def _validate_requirements(
    definition: FactorDefinition,
    requirements: Mapping[str, ReadinessRequirement],
    *,
    as_of: datetime,
) -> None:
    """Refuse a requirement set that does not ask what this factor's read needs answered."""
    needed = set(definition.datasets)
    supplied = set(requirements)
    if needed != supplied:
        raise FactorEngineError(
            f"{definition.qualified_key} reads {sorted(needed)} and was given requirements for "
            f"{sorted(supplied)}; every dataset it reads needs the requirement its own reader "
            "would put, and a requirement for a dataset it does not read was built for a "
            "different question"
        )
    for dataset, requirement in requirements.items():
        if requirement.dataset != dataset:
            raise FactorEngineError(
                f"the requirement filed under {dataset!r} is for {requirement.dataset!r}; a "
                "verdict about one dataset cannot gate a read of another"
            )
        if requirement.as_of != as_of:
            raise FactorEngineError(
                f"the {dataset} requirement is written for as_of "
                f"{requirement.as_of.isoformat()} and this build is at {as_of.isoformat()}; a "
                "readiness verdict taken at a different instant is a verdict about a different "
                "read"
            )
        if not requirement.years:
            raise FactorEngineError(
                f"the {dataset} requirement names no year, so there is no partition to read"
            )
        if requirement.required_fields is None:
            raise FactorEngineError(
                f"the {dataset} requirement waives required_fields, so it would report ready "
                f"for a partition with none of {list(definition.columns_of(dataset))} in it; a "
                "factor's inputs are exactly what that check exists for"
            )
        if requirement.max_staleness is None:
            raise FactorEngineError(
                f"the {dataset} requirement waives max_staleness, and this engine reads through "
                "read_visible_at, which answers with the rows knowable at as_of rather than "
                "with the partition. A waived bound therefore accepts a slice that reaches "
                "arbitrarily far short of as_of while every structural check clears: measured, "
                "a build stamped 2026-06-30 over a visible slice ending 2026-01-09, reported as "
                "coverage='computed'. State a bound -- read_visible_at re-decides it against "
                "the rows every year in this requirement makes visible, so a window spanning a "
                "year end is bounded by the age of its answer rather than by its own span"
            )
        needed_columns = set(definition.columns_of(dataset))
        if dataset in PERIOD_INDEXED_DATASETS:
            # The engine projects `report_period` for these datasets whether or not the factor
            # names it, because it is the axis their rows are taken on. A requirement that did
            # not require it would report `ready` for a partition the projection then fails to
            # bind -- the same fault this check exists for on the factor's own columns, one
            # column further out.
            needed_columns.add(REPORT_PERIOD_COLUMN)
        missing = sorted(needed_columns - set(requirement.required_fields))
        if missing:
            raise FactorEngineError(
                f"{definition.qualified_key} reads {missing} from {dataset} and the requirement "
                f"does not require them ({list(requirement.required_fields)}); readiness would "
                "clear a partition that cannot answer this factor"
            )


def _read_dataset(
    store: PanelStore,
    *,
    dataset: str,
    columns: tuple[str, ...],
    requirement: ReadinessRequirement,
    zone: ZoneInfo,
) -> tuple[_DatasetReading, tuple[FactorInputRef, ...], tuple[FactorInputProvenance, ...]]:
    """Every visible row of every requested year of one dataset, plus its input references.

    One `read_visible_at` per year, matching `load_daily_bars`' shape (readiness is assessed per
    call, on catalog metadata rather than Parquet). The projection is
    `(subject, event_time, *columns)` -- `available_time` is deliberately not projected, because
    the predicate is applied in SQL and a caller-side re-filter would be a second copy of the
    rule that can disagree with the first.

    **The refusal is reported from `blocking_issues`, not from `readiness.issues`**, and the
    difference is not cosmetic. A filtered read now has two verdicts -- one about the partition
    and one about the rows it was going to return -- and only the second can say "the slice you
    would have got reaches five months before your `as_of`". Reading the first alone reports
    `not_yet_knowable` for such a refusal, which is a true statement about the year and a
    misleading account of why this call failed: measured against a partition whose visible slice
    ended 2026-01-09 at an `as_of` of 2026-06-30, `readiness.issues` said only "the year is not
    over" while the bound that was actually breached went unnamed.

    Two records come back per partition rather than one, in the same order: the hashed
    `FactorInputRef` and the unhashed `FactorInputProvenance`. Splitting them here rather than at
    the manifest is what keeps the wall clock the provider batch carries out of a content
    address that has to be reproducible; see `domain/factor.py::FactorInputProvenance`.

    ## Two axes, one read, and the multiplicity rule each of them is under

    A dataset in `PERIOD_INDEXED_DATASETS` is indexed by its `report_period` column and every
    other by the session its `event_time` resolves to. That is the whole of the branch, and both
    halves keep a **fail-closed** multiplicity rule rather than one of them relaxing it:

    - **Session axis, unchanged.** Two rows for one `(subject, session)` raise. A dataset with
      several versions of one observation needs a reducer chosen for it before a factor may read
      it, and nothing here chooses one.
    - **Period axis.** Two rows for one `(subject, period)` announced on **different** days are an
      ordinary point-in-time restatement, and the later announcement is the one a reader standing
      at `as_of` would have -- exactly `financial_statements.StatementHistory.filing_for`'s rule,
      which takes `max(announced_on)` among the filings visible on the day. Two rows announced on
      the **same** day are the case no column in these datasets orders: `fina_indicator` carries
      more than one row for 81.7% of its `(ts_code, end_date, ann_date)` keys, has no
      `update_flag` and no `f_ann_date`, and `providers/tushare.py::_announcement_timeline`
      deliberately gives such rows byte-equal four-clock timelines. Those raise, with the same
      sentence the session axis uses.

    So the refusal that fired before this axis existed still fires: under a session index the
    same-day duplicates collided on `(subject, event_time)` and raised, and under a period index
    they collide on `(subject, period, announcement)` and raise. What stopped raising is the case
    that was never a duplicate at all -- an annual and a Q1 disclosed on one day, two periods
    under one `event_time` -- which is why an ordinary `income` input could not be read before.
    `tests/integration/panel/test_factor_report_periods.py` measures both directions.

    **The refusal is decided on the triple and not on the row the scan happens to reach first**,
    and that is the correction of a defect rather than a way of spelling it. The check used to sit
    behind the restatement branch -- `announcement < previous` continued, `announcement ==
    previous` raised -- so a same-day pair was only ever compared against whatever
    `announced[key]` held at the time. Once a *later* announcement of the same period had been
    seen, both members of the pair were silently discarded. Measured on one corpus of three rows
    (two versions announced 2024-04-20, a restatement announced 2024-08-10), varying nothing but
    the order they were written in:

        [dup_a, dup_b, later] -> raised
        [later, dup_a, dup_b] -> computed 555.0
        [dup_a, later, dup_b] -> computed 555.0

    `panel/store.py::read_visible_at` issues `SELECT ... FROM read_parquet(?) WHERE ... <= ?` with
    no `ORDER BY`, over a multi-row-group partition DuckDB is free to scan in parallel, so that
    order is decided by neither the caller nor the provider. One partition could refuse a build on
    one run and answer it on the next -- and `V2-P3-014`'s artifacts have to be reproducible. The
    value the answering runs produced was *right*; what was non-deterministic was whether the
    build succeeded, which is the worse of the two to leave in.

    A row whose `event_time` resolves after `as_of` raises here too, on both axes. The visible
    read decides what a caller may see from `available_time` alone, and this engine orders and
    indexes on `event_time`; when a partition's clocks disagree the two questions have different
    answers, and the one that reaches the window is `event_time`'s. Measured with the later
    announcement of a period given an `available_time` before the earlier one's: the engine took
    the restatement at an `as_of` two months before it was announced. `_announcement_timeline`
    makes all four clocks equal for every statement row, so nothing today can construct it --
    which is exactly why it is checked rather than assumed.

    The selection is a second statement of `filing_for`'s rule rather than a call into it:
    `statement_histories_from_panel_rows` requires the dataset's **whole** projected column set,
    so reusing it would make a factor that reads one column fetch all ten. The copy is held
    against the original by `tests/integration/panel/test_factor_report_periods.py::
    test_the_engines_period_selection_is_the_domains_filing_for`, which runs both over one corpus.
    It is a **test-time** audit and not a run-time one -- `_refuse_table_drift` runs at import and
    this does not -- so it holds only what its corpus varies, and its corpus now varies the year
    set the two sides read; see `_refuse_a_read_that_cannot_see_what_as_of_holds` for the
    divergence that audit did not have the language to see until it did.
    """
    period_indexed = dataset in PERIOD_INDEXED_DATASETS
    axis: FactorAxis = "period" if period_indexed else "session"
    projection = (
        (SUBJECT_COLUMN_NAME, EVENT_TIME_COLUMN, REPORT_PERIOD_COLUMN, *columns)
        if period_indexed
        else (SUBJECT_COLUMN_NAME, EVENT_TIME_COLUMN, *columns)
    )
    offset = 3 if period_indexed else 2
    as_of_day = requirement.as_of.astimezone(zone).date()
    _refuse_a_read_that_cannot_see_what_as_of_holds(
        store, dataset=dataset, requirement=requirement, as_of_day=as_of_day
    )
    points: dict[str, list[date]] = {}
    values: dict[tuple[str, date], tuple[float | None, ...]] = {}
    announced: dict[tuple[str, date], date] = {}
    filed: set[tuple[str, date, date]] = set()
    references: list[FactorInputRef] = []
    provenance: list[FactorInputProvenance] = []
    for year in sorted(set(requirement.years)):
        outcome = store.read_visible_at(requirement, year=year, columns=projection)
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at {requirement.as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.blocking_issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.blocking_issues)}"
            )
        coverage = store.read_coverage(dataset, year)
        if coverage is None or coverage.partition_content_hash is None:
            raise PanelStorageError(
                f"{dataset} year={year} cleared readiness but has no coverage record to cite as "
                "an input reference; the catalog changed underneath this read"
            )
        references.append(
            FactorInputRef(
                dataset=dataset,
                year=year,
                partition_content_hash=coverage.partition_content_hash,
                visible_row_count=outcome.visible_row_count,
                withheld_row_count=outcome.withheld_row_count,
            )
        )
        provenance.append(
            FactorInputProvenance(dataset=dataset, year=year, batch_digest=coverage.batch_digest)
        )
        for row in outcome.rows:
            subject = str(row[0])
            announcement = _session_date(row[1], dataset=dataset, zone=zone)
            if announcement > as_of_day:
                raise FactorEngineError(
                    f"{dataset} carries a row for {subject} whose event_time resolves to "
                    f"{announcement.isoformat()}, after the as_of {as_of_day.isoformat()} this "
                    "build reads at; the visible read cleared it on available_time while this "
                    "engine orders and indexes on event_time, so the two clocks disagree about "
                    "whether it had happened and a filing announced after as_of could win its "
                    "period"
                )
            point = (
                _report_period(row[2], dataset=dataset, subject=subject)
                if period_indexed
                else announcement
            )
            if (subject, point, announcement) in filed:
                raise FactorEngineError(
                    f"{dataset} carries more than one row for {subject} on "
                    f"{point.isoformat()}; this engine reads one row per security per "
                    f"{axis}, so a dataset with several versions of one observation needs a "
                    "reducer chosen for it before a factor may read it"
                )
            filed.add((subject, point, announcement))
            key = (subject, point)
            if key in values:
                if announcement < announced[key]:
                    continue
            else:
                points.setdefault(subject, []).append(point)
            announced[key] = announcement
            values[key] = tuple(
                _numeric(value, dataset=dataset, column=name, subject=subject, point=point)
                for name, value in zip(columns, row[offset:], strict=True)
            )
    return (
        _DatasetReading(
            MappingProxyType({name: tuple(sorted(days)) for name, days in points.items()}),
            MappingProxyType(values),
            columns,
            axis,
        ),
        tuple(references),
        tuple(provenance),
    )


def _refuse_a_read_that_cannot_see_what_as_of_holds(
    store: PanelStore,
    *,
    dataset: str,
    requirement: ReadinessRequirement,
    as_of_day: date,
) -> None:
    """The engine's `StatementHistory.answerable_through`, applied to the read rather than after
    it.

    `panel_ingest.load_statement_histories` compares the years it was asked for against
    `store.registered_years(dataset)` and bounds the assembled history at the year before the
    first stored partition it skipped, after which `filings_on` and everything built on it refuse
    rather than answer -- `KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
    .a_partial_year_read_answers_from_inside_its_window`. `compute_factor` had no such bound and
    read exactly `requirement.years`, which made the same partial read report a **pre-restatement
    value as `computed`**: measured, a store holding 2024 and 2025, a build reading only 2024 at
    `as_of=2025-05-20` answered 110.0 while the domain's history over both years answers 999.0 and
    its `answerable_through=2024` refuses the day outright.

    `max_staleness` does not stand in for this and that is measured too, at the 120 days this
    engine's own tests argue for: a restatement announced in January leaves the skipped year's
    partition reaching a date well inside the bound, so every structural check clears and the
    stale number is returned.

    **Only a year at or after the earliest year this read covers can hide anything.** A stored
    year *below* that is history the factor chose not to reach for, and its cost is
    `insufficient_history` on securities that needed it -- an honest, visible answer. A stored
    year at or after it is an announcement a reader standing at `as_of` holds and this read cannot
    see, so the bound is the year before the first such year, and a build stamped after it is
    refused. A registered year later than `as_of`'s own is no bound at all: nothing in it is
    knowable yet, and `read_visible_at`'s predicate is what says so.

    Refused rather than reported as coverage, for `_refuse_a_panel_narrower_than_the_lookback`'s
    reason: the fault is in the request, it is the same for every security in the cross section,
    and the remedy is a wider `years`.
    """
    covered = set(requirement.years)
    earliest = min(covered)
    unread = sorted(year for year in store.registered_years(dataset) if year not in covered)
    inside = [year for year in unread if year >= earliest]
    if not inside:
        return
    answerable_through = inside[0] - 1
    if as_of_day.year <= answerable_through:
        return
    raise FactorEngineError(
        f"{dataset} is stored for year(s) {unread} that this requirement does not name, and "
        f"{inside[0]} is at or after the earliest year it reads ({earliest}), so this read "
        f"answers only through {answerable_through} and this build is stamped "
        f"{as_of_day.isoformat()}. A filing announced in a year the read skips is one a reader "
        "standing at as_of holds and this read cannot see, so the value would be reported as "
        "computed from the version it superseded -- StatementHistory.answerable_through refuses "
        "the same day for the same reason. Name every stored year from the earliest this factor "
        "reads through the one as_of falls in"
    )


def _session_date(value: object, *, dataset: str, zone: ZoneInfo) -> date:
    if not isinstance(value, datetime):
        raise FactorEngineError(
            f"{dataset}.{EVENT_TIME_COLUMN} read back as {type(value).__name__}, not a "
            "datetime; the engine resolves a session date from it and cannot from anything else"
        )
    return value.astimezone(zone).date()


FISCAL_QUARTER_ENDS: Final[tuple[tuple[int, int], ...]] = ((3, 31), (6, 30), (9, 30), (12, 31))
"""The four `(month, day)` pairs an A-share fiscal period can end on.

A statutory fact rather than a convention this module chose: a PRC listed company's accounting
year is the calendar year, so `end_date` on all four statement endpoints is one of these four
days -- every value the probes in `domain/financial_statements.py` record is
(`20260331`, `20251231`, `20060331`, `20051231`, `19891231`, ...), and a period that is not one
of them is refused by `_report_period` rather than rounded into a quarter.

This is the grid `_period_span` counts on, and it is the whole of what makes
`max_window_periods == lookback_periods` mean "no missed filing inside the window"; see that
field's docstring and `_period_span`.
"""


def _report_period(value: object, *, dataset: str, subject: str) -> date:
    """A stored `report_period` cell as a fiscal period, or a refusal that names the row.

    A refusal rather than a coverage code, for `_numeric`'s reason: a `report_period` that is not
    an ISO date is a property of the *partition* rather than of this security's fundamentals, and
    `input_missing` would tell a reader to re-fetch a row that is already there. It is stored as
    text (`providers/tushare.py` projects `end_date` through `_calendar_date_text`) because the
    panel plane has no date kind, so this is the same decode `financial_statements
    ._parse_iso_date` performs on the same column -- and the same refusal.

    A well-formed date that is not one of `FISCAL_QUARTER_ENDS` is refused on the same grounds and
    for a sharper reason: `_period_span` counts the window's reach in fiscal quarters, and a
    period that is not a quarter end has no place on that grid. Rounding one into the quarter it
    falls in would be the fiscal-quarter arithmetic of this module's own devising that
    `_panel_axis_points` refuses to perform, and it would do it silently -- 2024-05-15 and
    2024-06-30 would become one point.
    """
    if not isinstance(value, str):
        raise FactorEngineError(
            f"{dataset}.{REPORT_PERIOD_COLUMN} holds {type(value).__name__} for {subject}; this "
            "engine indexes a filing by its fiscal period and cannot resolve one from anything "
            "but the stored ISO date"
        )
    try:
        period = date.fromisoformat(value)
    except ValueError as error:
        raise FactorEngineError(
            f"{dataset}.{REPORT_PERIOD_COLUMN} holds {value!r} for {subject}, which is not an "
            "ISO date; the report-period axis is ordered by it"
        ) from error
    if (period.month, period.day) not in FISCAL_QUARTER_ENDS:
        raise FactorEngineError(
            f"{dataset}.{REPORT_PERIOD_COLUMN} holds {value!r} for {subject}, which is not an "
            "A-share fiscal quarter end; this engine measures a period window's reach in "
            "quarters, and a period off that grid would either be rounded into a neighbour or "
            "make the reach unmeasurable"
        )
    return period


def _numeric(
    value: object, *, dataset: str, column: str, subject: str, point: date
) -> float | None:
    """A stored cell as a float, `None` for a missing observation, or a refusal.

    A refusal rather than a coverage code for a non-numeric column, because that is a property
    of the *definition* (a factor declaring `daily.trade_date` as an input) and not of this
    security's data: reporting `input_missing` would tell a reader to re-fetch, which would
    never fix it. `bool` is refused explicitly because it is an `int` in Python and `True`
    would otherwise arrive as `1.0`.

    `point` is the row's index on its dataset's own axis -- a session or a report period -- and
    is named for the axis rather than for one member of it, because the message it lands in is
    read by somebody looking for the row.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FactorEngineError(
            f"{dataset}.{column} holds {type(value).__name__} for {subject} on "
            f"{point.isoformat()}; a factor input must be a stored number, and this column "
            "cannot be one of this factor's required_fields"
        )
    return float(value)


def _classify(
    definition: FactorDefinition,
    *,
    subject: str,
    as_of: datetime,
    in_universe: bool,
    readings: Mapping[str, _DatasetReading],
    panel_sessions: tuple[date, ...],
    evaluator: FactorEvaluator,
    manifest_id: str,
) -> FactorObservation:
    """One security's coverage code and, if there is one, its value.

    The order of the checks is the order of `FactorCoverage`'s own argument and it is not
    arbitrary: universe before history, because a name that had not listed yet has no history
    *and should not*; history before nullity, because a window that cannot be formed has no
    cells to check; nullity before arithmetic, because an evaluator is only ever handed a
    complete window.

    "History" is four questions rather than one and every one of them is `insufficient_history`:
    on each declared axis, whether the security has the reach's worth of its own points at all,
    and whether the most recent of them fit inside the declared span. They stay distinguishable
    on the stored row without a fifth or sixth coverage code, and that is what the two window
    pairs buy: a count shortfall carries no window on the axis that fell short (there was none to
    record) and a span overrun carries the window it was refused for, on each axis independently.

    **The report-period axis reuses `insufficient_history` rather than earning its own code**, and
    that is the `max_window_sessions` precedent applied rather than a convenience. A security with
    three filings and a factor needing five does not have enough history *near `as_of`* -- which
    is what the code says -- and a `insufficient_filings` beside it would split one fact into two
    codes that `V2-P3-005` would have to exclude from a correlation in exactly the same way.
    """
    if not in_universe:
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="not_in_universe",
            factor_id=definition.factor_id,
            manifest_id=manifest_id,
            input_row_count=0,
            input_session_first=None,
            input_session_last=None,
            input_period_first=None,
            input_period_last=None,
        )
    sessions_held = _points_held(subject, readings=readings, axis="session")
    periods_held = _points_held(subject, readings=readings, axis="period")
    sessions = _form_window(sessions_held, definition.lookback_sessions)
    periods = _form_window(periods_held, definition.lookback_periods)
    if sessions is None or periods is None:
        session_first, session_last = _window_ends(sessions)
        period_first, period_last = _window_ends(periods)
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="insufficient_history",
            factor_id=definition.factor_id,
            manifest_id=manifest_id,
            input_row_count=_stored_rows(
                subject, sessions=sessions_held, periods=periods_held, readings=readings
            ),
            input_session_first=session_first,
            input_session_last=session_last,
            input_period_first=period_first,
            input_period_last=period_last,
        )
    row_count = _stored_rows(subject, sessions=sessions, periods=periods, readings=readings)
    ends = {
        "input_session_first": sessions[0] if sessions else None,
        "input_session_last": sessions[-1] if sessions else None,
        "input_period_first": periods[0] if periods else None,
        "input_period_last": periods[-1] if periods else None,
    }
    if _overruns_its_span(
        definition, sessions=sessions, periods=periods, panel_sessions=panel_sessions
    ):
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="insufficient_history",
            factor_id=definition.factor_id,
            manifest_id=manifest_id,
            input_row_count=row_count,
            **ends,
        )
    series = _complete_series(subject, sessions=sessions, periods=periods, readings=readings)
    if series is None:
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="input_missing",
            factor_id=definition.factor_id,
            manifest_id=manifest_id,
            input_row_count=row_count,
            **ends,
        )
    computed = evaluator(
        FactorWindow(
            subject=subject, as_of=as_of, sessions=sessions, periods=periods, values=series
        )
    )
    usable = computed is not None and math.isfinite(computed)
    return FactorObservation(
        subject=subject,
        as_of=as_of,
        value=float(computed) if usable and computed is not None else None,
        coverage="computed" if usable else "undefined_value",
        factor_id=definition.factor_id,
        manifest_id=manifest_id,
        input_row_count=row_count,
        **ends,
    )


def _points_held(
    subject: str, *, readings: Mapping[str, _DatasetReading], axis: FactorAxis
) -> tuple[date, ...]:
    """Every point on one axis this security has a row on, ascending and de-duplicated."""
    held: set[date] = set()
    for reading in readings.values():
        if reading.axis == axis:
            held.update(reading.points_by_subject.get(subject, ()))
    return tuple(sorted(held))


def _form_window(held: tuple[date, ...], lookback: int | None) -> tuple[date, ...] | None:
    """The most recent `lookback` points, `()` for an axis this factor is not on, `None` short.

    Three outcomes rather than two, because "this factor declares no reach on this axis" and
    "this security is short of the reach it declares" are different facts with different answers:
    the first is not a coverage question at all and the second is `insufficient_history`. A
    function that returned `()` for both would make a statement-only factor report every security
    as short of a session window it never asked for.
    """
    if lookback is None:
        return ()
    if len(held) < lookback:
        return None
    return held[-lookback:]


def _window_ends(window: tuple[date, ...] | None) -> tuple[date | None, date | None]:
    """A formed window's first and last point, or `(None, None)` when there is no window."""
    if not window:
        return (None, None)
    return (window[0], window[-1])


def _overruns_its_span(
    definition: FactorDefinition,
    *,
    sessions: tuple[date, ...],
    periods: tuple[date, ...],
    panel_sessions: tuple[date, ...],
) -> bool:
    """Whether either formed window reaches across more points than it declared.

    The two axes ask the same question and measure it against **different grids**, which is the
    correction of a defect rather than an asymmetry for its own sake; see `_period_span`.
    """
    if (
        sessions
        and definition.max_window_sessions is not None
        and _session_span(sessions, panel_sessions=panel_sessions) > definition.max_window_sessions
    ):
        return True
    return (
        bool(periods)
        and definition.max_window_periods is not None
        and _period_span(periods) > definition.max_window_periods
    )


def _session_span(window: tuple[date, ...], *, panel_sessions: tuple[date, ...]) -> int:
    """How many **panel** sessions the window reaches across, first and last included.

    Equal to `len(window)` for a security present at every session in it, and larger by exactly
    the number it missed. Counted against the panel's own session set rather than in calendar
    days, because a calendar-day bound would be a second calendar for the engine to disagree with
    the partition it is reading, and because "halted for three weeks" is a number of sessions
    rather than a number of days.

    The panel's session set is the right grid here and is *not* the right grid on the period axis,
    and the difference is that the sessions a panel returns are the trading calendar: every
    security in the cross section is quoted on every open day, so a session missing from the union
    is a session the market was closed. Nothing of the kind is true of report periods.

    `panel_sessions` is sorted, so this is two binary searches rather than a scan -- the check runs
    once per security per axis per build (5,534 times for a whole-market cross section).
    """
    left = bisect.bisect_left(panel_sessions, window[0])
    right = bisect.bisect_right(panel_sessions, window[-1])
    return right - left


def _period_span(window: tuple[date, ...]) -> int:
    """How many **fiscal quarters** the period window reaches across, first and last included.

    Counted on `FISCAL_QUARTER_ENDS`' grid and therefore on nothing this build happened to read,
    which is the whole of the fix. The panel's own period set -- the union of every
    `report_period` the visible read returned -- is what this used to count on, and it made the
    contract's central claim false in a way that was measured rather than argued:

        lookback_periods=5, max_window_periods=5, a security missing 2024-12-31 and no other
        security in the cross section filing that period either
        -> coverage='computed', value=0.5555555555555556
           window ['2023-12-31','2024-03-31','2024-06-30','2024-09-30','2025-03-31']
           [-5] to [-1] is fifteen months, and the "year-on-year" is 140/90-1

    The gap was not on the panel's grid, so the panel's grid could not see it: what
    `max_window_periods == lookback_periods` bought was "no filing this security missed that some
    *other* security in this build filed", which is a property of the cross section rather than of
    the security -- one witness away from a different answer, as the second half of that
    measurement showed: under the panel measure, adding one other security that filed 2024-12-31
    turned the identical build into `insufficient_history`.

    A quarter grid is exact and read-independent because an A-share fiscal year is the calendar
    year: the periods between two quarter ends are enumerable without consulting a single row.
    That is not the "fiscal-quarter arithmetic of this module's own devising" `_panel_axis_points`
    declines to perform -- that one would have to rule on which quarter a *non*-quarter-end
    `end_date` belongs to, and `_report_period` refuses such a period outright instead.

    Strictly stronger than the panel count it replaces, never weaker: every panel period is a
    quarter end, so the panel's points between the window's ends are a subset of the grid's.
    """
    return _quarter_index(window[-1]) - _quarter_index(window[0]) + 1


def _quarter_index(period: date) -> int:
    """A fiscal quarter end as its ordinal on the quarter grid, so two of them can be subtracted.

    `_report_period` has already refused anything that is not one of `FISCAL_QUARTER_ENDS`, so
    `month // 3` is the quarter of the year rather than a rounding.
    """
    return period.year * 4 + period.month // 3 - 1


def _stored_rows(
    subject: str,
    *,
    sessions: tuple[date, ...],
    periods: tuple[date, ...],
    readings: Mapping[str, _DatasetReading],
) -> int:
    """How many input rows this security actually has over both windows, across every dataset.

    Counted rather than derived as `len(window) * len(readings)`, which is only right when every
    dataset covers every point and is exactly wrong on the two observations where the number
    matters most: an `input_missing` row is one where a cell is absent, and an
    `insufficient_history` row is one whose datasets disagree about how much history there is.
    A count that over-reported on precisely those two would be a provenance field that is
    accurate only when nobody needs it.

    Each reading is counted over its **own** axis's window, so a factor that reads a price and a
    filing gets one number covering both rather than a session count with the filings missing.
    """
    windows: Mapping[FactorAxis, tuple[date, ...]] = {"session": sessions, "period": periods}
    return sum(
        1
        for reading in readings.values()
        for point in windows[reading.axis]
        if (subject, point) in reading.values
    )


def _complete_series(
    subject: str,
    *,
    sessions: tuple[date, ...],
    periods: tuple[date, ...],
    readings: Mapping[str, _DatasetReading],
) -> Mapping[tuple[str, str], tuple[float, ...]] | None:
    """Every required column over `window`, or `None` if any cell is absent or null.

    One `None` for both shapes, deliberately: a security with no row in one dataset on a session
    the others cover and a security with a stored null in that column are the same fact to a
    factor -- the input is not there -- and `input_missing`'s remedy (fetch it) is the same for
    both. Distinguishing them would need a second coverage code whose only difference is which
    of two indistinguishable-to-the-caller repairs to make.

    **What an `input_missing` observation does and does not let a reader locate**, stated because
    "the remedy is the same: fetch it" answers a different question from "fetch *what*":

    - Across *datasets* it is recoverable. `input_row_count` is a count of rows actually present
      over the window, so a two-dataset factor that got 3 of 4 names the dataset that is short by
      subtraction (measured in `tests/integration/panel/test_factor_engine.py`: 3 against the 4
      a complete window has).
    - Across *columns of one dataset* it is not, and neither is a missing row against a stored
      null within one dataset. This function returns at the first failure, so a factor reading
      `income.revenue` and `income.n_income` over one window cannot say which was null.
      `V2-P3-009`'s EP reads a price and a filing at once and is the first definition for which
      that matters; `V2-P3-007`'s coverage report is where the per-`(dataset, column, session)`
      answer belongs, because it is a report over many builds rather than a field on one row.

    The bound is here rather than in a task note because it is a property of this function's
    early return, and widening `input_missing` into two codes would not fix it -- the missing
    fact is *which* input, not which kind of absence.
    """
    windows: Mapping[FactorAxis, tuple[date, ...]] = {"session": sessions, "period": periods}
    series: dict[tuple[str, str], list[float]] = {}
    for dataset, reading in readings.items():
        for column in reading.columns:
            series[(dataset, column)] = []
        for point in windows[reading.axis]:
            cells = reading.values.get((subject, point))
            if cells is None:
                return None
            for column, cell in zip(reading.columns, cells, strict=True):
                if cell is None:
                    return None
                series[(dataset, column)].append(cell)
    return MappingProxyType({key: tuple(values) for key, values in series.items()})


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError) as error:
        raise FactorEngineError(f"date_timezone {name!r} is not a known IANA time zone") from error


# --- writing -----------------------------------------------------------------------------------


def factor_observation_batch(panel: FactorPanel) -> ColumnarPanelBatch:
    """One panel's observations as a columnar batch, ready for the store.

    **Every clock on every row is the build's `as_of`**, and the reasoning is worth stating
    because it is the opposite of what a fetched dataset does. A factor observation is a
    statement made *at* `as_of` out of information knowable *at* `as_of`: its event is the
    cross-section instant, it became knowable at that instant, and it has no revision. Setting
    `ingested_time` to the wall clock instead would put the build time inside the partition's
    content hash, so recomputing an unchanged factor would rewrite the partition every time --
    and `revision_time` on the wall clock would make `PartitionCoverage.revised_row_count` count
    every row as a revision, which is the field that exists to count actual restatements.

    The wall clock is not lost: it is the batch's `fetched_at`, which is hashed into
    `content_digest` and stored as `PartitionCoverage.fetched_at`. So a rebuild is a byte-
    identical partition with a fresh provenance record -- exactly the case `write_panel_batch`
    documents when it re-records coverage over an idempotent no-op write.

    **Every observation is re-validated here**, which is the second call site
    `domain/factor.py::validate_factor_observation` exists for: `FactorObservation.__post_init__`
    is a method and a subclass can override it, and the write boundary is the last place a row
    that skipped the constructor's rules can be stopped before it is a column in a Parquet file.
    `panel/catalog.py` made the same move for the same reason. The cost is a handful of
    comparisons per row against a write that is already dominated by Parquet serialisation.
    """
    observations = panel.observations
    for observation in observations:
        validate_factor_observation(observation)
    instants = tuple(observation.as_of for observation in observations)
    columns: dict[str, list[object]] = {
        "factor_id": [observation.factor_id for observation in observations],
        "factor_key": [panel.definition.key] * len(observations),
        "factor_version": [panel.definition.version] * len(observations),
        "value": [observation.value for observation in observations],
        "coverage": [observation.coverage for observation in observations],
        "manifest_id": [observation.manifest_id for observation in observations],
        "input_row_count": [observation.input_row_count for observation in observations],
        "input_session_first": [_iso(item.input_session_first) for item in observations],
        "input_session_last": [_iso(item.input_session_last) for item in observations],
        "input_period_first": [_iso(item.input_period_first) for item in observations],
        "input_period_last": [_iso(item.input_period_last) for item in observations],
    }
    return ColumnarPanelBatch(
        provider_id=FACTOR_PROVIDER_ID,
        dataset=factor_observation_dataset(panel.definition),
        kind=FACTOR_OBSERVATION_KIND,
        as_of=panel.as_of,
        fetched_at=panel.built_at,
        status="success",
        subjects=tuple(observation.subject for observation in observations),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=instants,
            revision_time=instants,
        ),
        columns=tuple(
            PanelColumn(name, _OBSERVATION_COLUMN_KINDS[name], tuple(values))
            for name, values in columns.items()
        ),
        source_uri=None,
    )


def _batch_digests_by_partition(panel: FactorPanel) -> Mapping[tuple[str, int], str]:
    """`input_provenance` keyed by `(dataset, year)`, refusing a partition it does not cover.

    A refusal rather than a `""` default, because the manifest row's whole purpose is to be a
    provenance record: a `FactorPanel` assembled by hand with the two tuples out of step would
    otherwise store a digest column that quietly names the wrong partition, which is worse than
    the missing one it would be standing in for.
    """
    digests = {(item.dataset, item.year): item.batch_digest for item in panel.input_provenance}
    missing = sorted(
        f"{item.dataset}/{item.year}"
        for item in panel.manifest.inputs
        if (item.dataset, item.year) not in digests
    )
    if missing:
        raise FactorEngineError(
            f"this panel's manifest names input partition(s) {missing} and its input_provenance "
            "does not carry a batch digest for them; the two are produced together by "
            "compute_factor and a panel where they disagree cannot be stored"
        )
    return digests


def factor_manifest_batch(panel: FactorPanel) -> ColumnarPanelBatch:
    """One panel's build manifest, one row per input partition, keyed by `manifest_id`.

    Two of the column families are not manifest fields and each is here for a stated reason.
    `input_batch_digest` comes from `panel.input_provenance`, matched to the hashed input by
    `(dataset, year)` -- it moves on every re-fetch and therefore cannot be in the content
    address, which is `built_at`'s arrangement applied to the input side. The `census_*` columns
    come from `panel.coverage_census()`: a build that answered nobody is otherwise
    indistinguishable in storage from one that scored the whole market, and `coverage_census()`
    speaks only to a caller that thinks to ask.
    """
    manifest = panel.manifest
    inputs = manifest.inputs
    digests = _batch_digests_by_partition(panel)
    census = panel.coverage_census()
    instants = tuple(manifest.as_of for _ in inputs)
    columns: dict[str, list[object]] = {
        "factor_id": [manifest.factor_id] * len(inputs),
        "factor_key": [manifest.factor_key] * len(inputs),
        "factor_version": [manifest.factor_version] * len(inputs),
        "as_of_time": [manifest.as_of] * len(inputs),
        "date_timezone": [manifest.date_timezone] * len(inputs),
        "code_commit": [manifest.code_commit] * len(inputs),
        "direction": [manifest.direction] * len(inputs),
        "lookback_sessions": [manifest.lookback_sessions] * len(inputs),
        "max_window_sessions": [manifest.max_window_sessions] * len(inputs),
        "lookback_periods": [manifest.lookback_periods] * len(inputs),
        "max_window_periods": [manifest.max_window_periods] * len(inputs),
        "subject_count": [manifest.subject_count] * len(inputs),
        "subject_digest": [manifest.subject_digest] * len(inputs),
        "universe_count": [manifest.universe_count] * len(inputs),
        "universe_digest": [manifest.universe_digest] * len(inputs),
        **{
            f"{FACTOR_CENSUS_COLUMN_PREFIX}{code}": [census[code]] * len(inputs)
            for code in FACTOR_COVERAGE_ORDER
        },
        "input_dataset": [item.dataset for item in inputs],
        "input_year": [item.year for item in inputs],
        "input_batch_digest": [digests[(item.dataset, item.year)] for item in inputs],
        "input_partition_hash": [item.partition_content_hash for item in inputs],
        "input_visible_rows": [item.visible_row_count for item in inputs],
        "input_withheld_rows": [item.withheld_row_count for item in inputs],
    }
    return ColumnarPanelBatch(
        provider_id=FACTOR_PROVIDER_ID,
        dataset=factor_manifest_dataset(panel.definition),
        kind=FACTOR_MANIFEST_KIND,
        as_of=manifest.as_of,
        fetched_at=panel.built_at,
        status="success",
        subjects=tuple(manifest.manifest_id for _ in inputs),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=instants,
            revision_time=instants,
        ),
        columns=tuple(
            PanelColumn(name, _MANIFEST_COLUMN_KINDS[name], tuple(values))
            for name, values in columns.items()
        ),
        source_uri=None,
    )


def write_factor_panels(
    store: PanelStore,
    panels: Sequence[FactorPanel],
    *,
    supersedes: Collection[str] = (),
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Write every panel's observations and manifests, merged into one partition per year.

    Takes a **sequence** for the reason `write_daily_panel` and `write_adjustment_factors` do:
    `PanelStore.write_partition` replaces a partition whole and has no append, so a caller
    writing one `as_of` at a time would destroy the year each time. Every `as_of` of one factor
    whose observations belong to a partition year has to reach the store in one call. Different
    *factors* no longer have to, because each one has its own datasets; see this module's
    docstring for the memory measurement that decides it.

    That is a real constraint rather than an implementation detail, so it is guarded rather than
    documented: `_refuse_to_drop_a_stored_build` reads the target manifest partition's stored
    build list off the catalog -- one row, no partition scan -- and refuses a write that would
    drop any of them. The observation partitions are covered by the same check rather than by a
    second one of their own; see that function for why a securities-level guard was both too
    strict and too weak.

    ## `supersedes`, and why a rebuild needs a way to *say* it is one

    "A rebuild that supersedes an earlier build must name it" was the rule and there was no way
    to name one: the only way past the guard was to re-supply the superseded build, which is the
    opposite of superseding it. `supersedes` is that name -- `manifest_id`s this call is
    deliberately replacing, which the guard subtracts before deciding whether anything was
    dropped. `load_factor_manifests` is how a caller discovers what a partition holds in order
    to name one.

    A `manifest_id` named here that the partition does not hold is refused rather than ignored,
    for the reason a no-op waiver is always refused in this repository: a typo would silently
    turn the guard off for the write it accompanied.

    ## One build per `(factor, as_of)` in a call

    Two panels of one factor at one `as_of` are two answers to one question, and storing both
    puts two rows on every `(subject, as_of)` for a reader to choose between. Refused here.
    Together with the drop guard that also settles the stored side: a second build at an `as_of`
    the partition already holds either arrives beside the first (refused here) or without it
    (refused there, unless it says `supersedes`).

    **Every guard runs before the first write.** An earlier version checked each partition just
    before writing it and left a refused call having already replaced the observations and not
    the manifests -- two halves of one write disagreeing, which is worse than either outcome and
    which `test_a_write_that_would_drop_a_stored_build_is_refused` caught. There is still no
    cross-partition atomicity on offer here; what the ordering buys is that a refusal changes
    nothing at all.
    """
    if not panels:
        raise FactorEngineError(
            "write_factor_panels needs at least one panel; an empty write would be a call that "
            "reports success and stores nothing"
        )
    _refuse_two_builds_of_one_factor_at_one_as_of(panels)
    planned = [
        (year, yearly)
        for batches in _batches_by_dataset(panels)
        for year, yearly in split_panel_batch_by_year(
            merge_panel_batches(batches), date_timezone=date_timezone
        )
    ]
    # Guards first, writes second -- see this function's docstring for what that ordering is
    # worth and what it is not.
    superseded = set(supersedes)
    # One catalog row per target manifest partition, read before anything is judged: the
    # `supersedes` names have to be checked against *every* partition this write touches before
    # the first drop is refused, or a typo would be reported as whichever partition happened to
    # be judged first rather than as the typo it is.
    stored: list[tuple[ColumnarPanelBatch, int, frozenset[str]]] = []
    for year, yearly in planned:
        if yearly.kind != FACTOR_MANIFEST_KIND:
            continue
        existing = store.read_coverage(yearly.dataset, year)
        stored.append(
            (yearly, year, frozenset() if existing is None else frozenset(existing.subjects))
        )
    unmatched = sorted(superseded - {build for _, _, builds in stored for build in builds})
    if unmatched:
        raise FactorEngineError(
            f"supersedes names {unmatched}, which no partition this write touches holds; a "
            "manifest_id that matches nothing is a typo, and letting it through would turn the "
            "drop guard off for the write it arrived with"
        )
    for yearly, year, builds in stored:
        _refuse_to_drop_a_stored_build(yearly, year, builds=builds, superseded=superseded)
    return tuple(
        write_panel_batch(store, yearly, year=year, date_timezone=date_timezone)
        for year, yearly in planned
    )


def _batches_by_dataset(
    panels: Sequence[FactorPanel],
) -> tuple[tuple[ColumnarPanelBatch, ...], ...]:
    """One group of same-dataset batches per factor and per kind, observations before manifests.

    `merge_panel_batches` concatenates batches of *one* dataset, and each factor now has two of
    its own, so a call carrying several factors produces several groups rather than two. Grouped
    by dataset name rather than by definition so the grouping key is the same string the store
    files the partition under -- two definitions that produced one dataset name would be a
    collision this loop would silently merge, and `FactorRegistry` already refuses the only way
    to have two definitions with one `key/vN`.
    """
    grouped: dict[str, list[ColumnarPanelBatch]] = {}
    for build in (factor_observation_batch, factor_manifest_batch):
        for panel in panels:
            batch = build(panel)
            grouped.setdefault(batch.dataset, []).append(batch)
    return tuple(tuple(batches) for batches in grouped.values())


def _refuse_two_builds_of_one_factor_at_one_as_of(panels: Sequence[FactorPanel]) -> None:
    """Refuse a call that answers one `(factor, as_of)` question twice."""
    seen: dict[tuple[str, datetime], int] = {}
    for panel in panels:
        key = (panel.definition.factor_id, panel.as_of)
        seen[key] = seen.get(key, 0) + 1
    repeated = sorted(
        f"{factor_id} at {as_of.isoformat()}" for (factor_id, as_of), n in seen.items() if n > 1
    )
    if repeated:
        raise FactorEngineError(
            f"this write carries more than one build of {repeated}; a second answer to one "
            "cross-section question would store two rows for every (subject, as_of) and leave a "
            "reader to choose between them. Supersede the earlier build instead"
        )


def _refuse_to_drop_a_stored_build(
    batch: ColumnarPanelBatch,
    year: int,
    *,
    builds: Collection[str],
    superseded: Collection[str],
) -> None:
    """Block a write that would remove a build the manifest partition already holds.

    `panel_ingest._refuse_to_drop_stored_subjects` for the factor plane: a partition is replaced
    whole, so a batch missing something the stored partition had destroys data and reports
    success. A manifest partition's subject is a `manifest_id`, so a dropped subject is an
    `as_of` somebody computed that is now gone.

    ## Why the observation partition is not guarded the same way

    It was, and the second guard was wrong in both directions rather than merely redundant.

    - **It refuses correct writes.** An observation partition's subjects are securities. A
      rebuild that supersedes a build with a narrower cross section legitimately leaves fewer
      names in the year -- and `supersedes` names `manifest_id`s, which a securities-level guard
      cannot match against anything. The write is right and the guard says no.
    - **It permits incorrect ones.** A write that dropped a whole `as_of` while keeping the same
      names passes it, because the subject set is unchanged.

    The build reading has neither fault, and it is *complete* for observations as well:
    `manifest_id` now covers `subject_digest`, so a write carrying every stored build carries
    every stored security by construction, and a write that drops a security necessarily changes
    a build's identity and is caught here. What a caller loses is a message naming the securities
    rather than the build; what it gains is a unit of work -- the build -- that `supersedes` and
    `load_factor_manifests` can both address.

    **Shared verbatim with `write_processed_factor_panels`** (`V2-P3-003`), because the fact it
    enforces -- a partition is replaced whole -- is the same one on the processed plane, and the
    unit has the same shape there: a transform manifest partition's subject is a
    `transform_manifest_id`. That sharing is also why this refusal is a `FactorEngineError` on
    both planes while a transform's *computational* refusals are `FactorTransformError`s; see
    `domain/factor_transform.py::FactorTransformError` for the seam.

    `builds` is `PartitionCoverage.subjects` for this partition, read by the caller: one catalog
    row, no partition scan. An empty one is a partition with no coverage record and is not
    protected, for the reason the ingest version gives -- there is nothing to read the stored
    subjects from, that state is an interrupted write which readiness already blocks as
    `coverage_missing`, and refusing the overwrite would leave the store with no way back.
    """
    dropped = sorted(set(builds) - set(batch.subjects) - set(superseded))
    if dropped:
        raise FactorEngineError(
            f"{batch.dataset} year={year} already holds {len(set(builds))} subject(s) and "
            f"this write carries {len(set(batch.subjects))}; it would drop {dropped[:5]}"
            f"{'...' if len(dropped) > 5 else ''}. A partition is replaced whole, so everything "
            "belonging to this year has to be written in one call -- read the stored builds with "
            "load_factor_manifests and either recompute them into this call or name the ones "
            "this rebuild replaces in write_factor_panels' `supersedes`"
        )


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# --- reading back --------------------------------------------------------------------------------


def factor_observation_requirement(
    definition: FactorDefinition, *, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What one factor's observation partition must satisfy before its values may be read back.

    Three of the four checks are waived and each is a judgement rather than a shortcut.

    - **`required_dates` is waived.** The dates in this dataset are the `as_of`s somebody chose
      to compute, not the sessions an exchange was open. Deriving an expectation from a calendar
      would report a permanent `date_gap` on a partition that is complete by construction --
      the same reason `adjustment_requirement` waives it on a compressed factor partition.
    - **`required_subjects` is waived** because the cross section is what the read is for;
      naming it would be circular, which is `daily_requirement`'s own argument.
    - **`required_fields` is not waived**: the nine stored columns plus the subject are exactly
      what `load_factor_observations` decodes, and a partition missing one of them would fail as
      a binder error rather than as a readiness verdict.
    - **`max_staleness` is waived, and this one is a judgement rather than an obvious call.**
      Every fetched dataset states a bound because a price panel whose newest session is a month
      old has missed a month of the market. A *derived* partition has no upstream to fall behind
      -- "the newest observation here is three months old" means nobody ran a build, which is a
      fact about a schedule the panel plane does not own. `V2-P3-014`'s experiment artifacts and
      `V2-P3-015`'s CLI face are where a build cadence exists to be checked against; a bound
      invented here would refuse a perfectly sound historical backfill. The waiver is on the
      record either way, in `DatasetReadiness.checks_waived`.
    """
    return ReadinessRequirement(
        dataset=factor_observation_dataset(definition),
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=FACTOR_OBSERVATION_PANEL_COLUMNS,
        max_staleness=None,
    )


def factor_manifest_requirement(
    definition: FactorDefinition, *, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What one factor's manifest partition must satisfy before its builds may be read back.

    The same three waivers as `factor_observation_requirement` and for the same reasons; the
    dates here are `as_of`s rather than sessions, the subjects are `manifest_id`s rather than a
    cross section, and a derived partition has no upstream to be stale against.
    """
    return ReadinessRequirement(
        dataset=factor_manifest_dataset(definition),
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=FACTOR_MANIFEST_PANEL_COLUMNS,
        max_staleness=None,
    )


def load_factor_observations(
    store: PanelStore,
    definition: FactorDefinition,
    *,
    years: Sequence[int],
    as_of: datetime,
) -> tuple[FactorObservation, ...]:
    """Read one factor's stored observations back, filtered to what was knowable at `as_of`.

    Through `read_visible_at` rather than `read_if_ready`, and for the same reason the inputs
    are: an observation's `available_time` is the `as_of` it was computed at, so a year
    partition holding a year of daily cross sections has a `max_available_time` in December and
    `read_if_ready` would refuse it at every `as_of` inside the year -- including the ones whose
    own observations are sitting in it.

    The factor is the **dataset**, not a filter. An earlier version took an optional `factor_id`
    and narrowed one shared partition with a SQL equality; the partition is now per factor, so a
    read of one factor never opens another one's file at all. That is the same saving one layer
    down, and it is the read-side half of the write-side memory argument in this module's
    docstring.
    """
    requirement = factor_observation_requirement(definition, years=years, as_of=as_of)
    dataset = requirement.dataset
    found: list[FactorObservation] = []
    for year in sorted(set(years)):
        outcome = store.read_visible_at(
            requirement,
            year=year,
            columns=(EVENT_TIME_COLUMN, *FACTOR_OBSERVATION_PANEL_COLUMNS),
        )
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at "
                f"{as_of.isoformat()}: {[issue.code for issue in outcome.blocking_issues]}"
            )
        found.extend(_observation_from_row(row, dataset=dataset) for row in outcome.rows)
    return tuple(found)


def load_factor_manifests(
    store: PanelStore,
    definition: FactorDefinition,
    *,
    years: Sequence[int],
    as_of: datetime,
) -> tuple[FactorBuildManifest, ...]:
    """Every build of one factor stored in `years` and knowable at `as_of`, reassembled.

    The read `write_factor_panels`' refusal points at, and the reason it can point anywhere:
    a partition is replaced whole and a stored build may not be dropped, so a caller who wants
    to add an `as_of` to a year has to know what the year already holds. Before this existed
    the only recovery from a refused write was to remember what had been written.

    One row per `(build, input partition)` is stored; this folds them back by `manifest_id`, so
    a build that read two partitions comes back as one manifest with two `inputs`. The rows are
    ordered by `(dataset, year)` within a build so the reassembly is deterministic rather than
    dependent on scan order -- `FactorBuildManifest` refuses a repeated partition either way.

    **`input_batch_digest` is stored and is deliberately not reassembled here.** It is not a
    field of `FactorBuildManifest` (see `domain/factor.py::FactorInputProvenance`), so putting it
    back on one would either not fit or would change the `manifest_id` of the manifest this
    function returns -- and the whole point of this read is that a reassembled build reproduces
    the identity it was stored under. It is a column in the partition for a reader that wants it.

    The refusal reads `blocking_issues` rather than `readiness.issues`, for the reason
    `_read_dataset` gives. It makes no difference to *this* requirement -- `factor_manifest_
    requirement` waives both checks `evaluate_visible_slice` can re-decide, so the two are equal
    here by construction -- and it is the idiom rather than the equality that has to hold: the
    day this requirement states a bound, the code that reports the refusal must not be the
    version that reports only half of it.
    """
    requirement = factor_manifest_requirement(definition, years=years, as_of=as_of)
    dataset = requirement.dataset
    rows_by_build: dict[str, list[Mapping[str, object]]] = {}
    for year in sorted(set(years)):
        outcome = store.read_visible_at(
            requirement, year=year, columns=FACTOR_MANIFEST_PANEL_COLUMNS
        )
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at "
                f"{as_of.isoformat()}: {[issue.code for issue in outcome.blocking_issues]}"
            )
        for row in outcome.rows:
            cells = _manifest_cells(row, dataset=dataset)
            rows_by_build.setdefault(str(cells[SUBJECT_COLUMN_NAME]), []).append(cells)
    return tuple(
        _manifest_from_rows(rows, dataset=dataset, manifest_id=manifest_id)
        for manifest_id, rows in rows_by_build.items()
    )


def _manifest_cells(row: Sequence[object], *, dataset: str) -> Mapping[str, object]:
    """One stored manifest row as a column-keyed mapping, refusing the wrong width.

    `_observation_from_row`'s argument, one dataset over: a partition written by a build with a
    different column list would otherwise decode into plausible values in the wrong fields.
    """
    if len(row) != len(FACTOR_MANIFEST_PANEL_COLUMNS):
        raise FactorEngineError(
            f"a {dataset} row has {len(row)} values, expected "
            f"{len(FACTOR_MANIFEST_PANEL_COLUMNS)} "
            f"({', '.join(FACTOR_MANIFEST_PANEL_COLUMNS)})"
        )
    return dict(zip(FACTOR_MANIFEST_PANEL_COLUMNS, row, strict=True))


def _manifest_from_rows(
    rows: Sequence[Mapping[str, object]], *, dataset: str, manifest_id: str
) -> FactorBuildManifest:
    """Rebuild one manifest from its `(build, input partition)` rows, and prove it is the one.

    The reassembled `manifest_id` is checked against the `manifest_id` the rows were stored
    under. That is not belt and braces: it is the only thing that makes this function's output
    trustworthy, because every field it reads is one the identity was computed from, and a
    decoder that silently produced a manifest with a different address would be handing back a
    build nobody ever ran.
    """
    head = rows[0]
    as_of = head["as_of_time"]
    if not isinstance(as_of, datetime):
        raise FactorEngineError(
            f"a {dataset} row carries {type(as_of).__name__} for as_of_time, not a datetime"
        )
    manifest = FactorBuildManifest(
        factor_id=str(head["factor_id"]),
        factor_key=str(head["factor_key"]),
        factor_version=int(str(head["factor_version"])),
        as_of=as_of,
        date_timezone=str(head["date_timezone"]),
        code_commit=str(head["code_commit"]),
        direction=_direction_code(head["direction"], dataset=dataset),
        lookback_sessions=_stored_count(head["lookback_sessions"]),
        max_window_sessions=_stored_count(head["max_window_sessions"]),
        lookback_periods=_stored_count(head["lookback_periods"]),
        max_window_periods=_stored_count(head["max_window_periods"]),
        subject_count=int(str(head["subject_count"])),
        subject_digest=str(head["subject_digest"]),
        universe_count=int(str(head["universe_count"])),
        universe_digest=str(head["universe_digest"]),
        inputs=tuple(
            FactorInputRef(
                dataset=str(item["input_dataset"]),
                year=int(str(item["input_year"])),
                partition_content_hash=str(item["input_partition_hash"]),
                visible_row_count=int(str(item["input_visible_rows"])),
                withheld_row_count=int(str(item["input_withheld_rows"])),
            )
            for item in sorted(
                rows, key=lambda cells: (str(cells["input_dataset"]), str(cells["input_year"]))
            )
        ),
    )
    if manifest.manifest_id != manifest_id:
        raise FactorEngineError(
            f"a {dataset} build stored under {manifest_id!r} reassembles to "
            f"{manifest.manifest_id!r}; the rows and the identity they were filed under disagree, "
            "so this partition was written by a build whose manifest contract is not this one's"
        )
    return manifest


def _stored_count(value: object) -> int | None:
    """A stored reach column as an integer, preserving the null that means "not on this axis".

    `int(str(None))` raises and `int(str(cell))` would turn a genuine null into a `TypeError`
    several frames from the column it came out of, so the null is a branch rather than an
    accident. It is a real value here: a factor that reads no filing stores no period reach, and
    `FactorBuildManifest` accepts `None` exactly for that case.
    """
    return None if value is None else int(str(value))


def _direction_code(value: object, *, dataset: str) -> FactorDirection:
    """A stored `direction` cell as one of the two declared codes, `_coverage_code`'s argument.

    Returned from the vocabulary rather than cast, so a partition written by a build that knows
    a third direction is refused where the dataset can be named rather than decoded into a
    `FactorDirection` the type system believes is one of two and is not.
    """
    text = str(value)
    for code in sorted(FACTOR_DIRECTIONS):
        if code == text:
            return cast(FactorDirection, code)
    raise FactorEngineError(
        f"a {dataset} row carries direction {text!r}, which this build does not declare "
        f"({sorted(FACTOR_DIRECTIONS)}); it was written by a build that knows a code this one "
        "does not"
    )


def _observation_from_row(row: Sequence[object], *, dataset: str) -> FactorObservation:
    """Rebuild one observation from a row shaped `(event_time, *FACTOR_OBSERVATION_PANEL_COLUMNS)`.

    Refuses a row of the wrong width rather than unpacking it positionally into whatever fits:
    a partition written by a build with a different column list would otherwise decode into
    plausible values in the wrong fields.
    """
    expected = 1 + len(FACTOR_OBSERVATION_PANEL_COLUMNS)
    if len(row) != expected:
        raise FactorEngineError(
            f"a {dataset} row has {len(row)} values, expected {expected} "
            f"({EVENT_TIME_COLUMN}, {', '.join(FACTOR_OBSERVATION_PANEL_COLUMNS)})"
        )
    cells = dict(zip((EVENT_TIME_COLUMN, *FACTOR_OBSERVATION_PANEL_COLUMNS), row, strict=True))
    as_of = cells[EVENT_TIME_COLUMN]
    if not isinstance(as_of, datetime):
        raise FactorEngineError(
            f"a {dataset} row carries {type(as_of).__name__} for "
            f"{EVENT_TIME_COLUMN}, not a datetime"
        )
    return FactorObservation(
        subject=str(cells[SUBJECT_COLUMN_NAME]),
        as_of=as_of,
        value=_stored_value(cells["value"], dataset=dataset),
        coverage=_coverage_code(cells["coverage"], dataset=dataset),
        factor_id=str(cells["factor_id"]),
        manifest_id=str(cells["manifest_id"]),
        input_row_count=int(str(cells["input_row_count"])),
        input_session_first=_stored_date(cells["input_session_first"]),
        input_session_last=_stored_date(cells["input_session_last"]),
        input_period_first=_stored_date(cells["input_period_first"]),
        input_period_last=_stored_date(cells["input_period_last"]),
    )


def _stored_date(value: object) -> date | None:
    """A stored window-end column as a date, preserving the null that means "no window"."""
    return None if value is None else date.fromisoformat(str(value))


def _stored_value(value: object, *, dataset: str) -> float | None:
    """A stored `value` cell as a finite float or `None`, or a refusal that names the dataset.

    `_coverage_code`'s symmetric case, and it was missing. That function defends the `coverage`
    column against "a build that knows a code this one does not"; nothing defended the `value`
    column against a number this build's own rules say cannot be there. `float(str(cell))` parses
    `'nan'` and `'inf'` without complaint, so a partition carrying either decoded into a
    `computed` observation and reached `FactorPanel.values()` -- which is the input to a rank
    correlation. `undefined_value` is the code a non-finite result belongs under, and a stored
    row that says otherwise is a row this build cannot interpret.

    `FactorObservation` refuses it a moment later too, and that is deliberate rather than
    redundant: this is the same refusal one layer earlier, where the message can name the dataset
    the row came out of.
    """
    if value is None:
        return None
    parsed = float(str(value))
    if not math.isfinite(parsed):
        raise FactorEngineError(
            f"a {dataset} row carries value {value!r}, which is not a finite number; "
            "`undefined_value` is the coverage code a non-finite result is stored under, and a "
            "`computed` row holding one poisons every mean and rank built on the column"
        )
    return parsed


def _coverage_code(value: object, *, dataset: str) -> FactorCoverage:
    """A stored `coverage` cell as one of the five declared codes, or a refusal.

    Matched against `FACTOR_COVERAGE_ORDER` and *returned from it* rather than cast: a cast
    would make a partition written by a build with a sixth code decode into a `FactorObservation`
    whose `coverage` the type system believes is one of five and is not. `FactorObservation`
    would in fact catch it a moment later -- it re-checks the code against
    `FACTOR_COVERAGE_CODES` -- and this is the same refusal one layer earlier, where the message
    can name the dataset the row came out of.
    """
    text = str(value)
    for code in FACTOR_COVERAGE_ORDER:
        if code == text:
            return code
    raise FactorEngineError(
        f"a {dataset} row carries coverage {text!r}, which this build does "
        f"not declare ({list(FACTOR_COVERAGE_ORDER)}); it was written by a build that knows a "
        "code this one does not"
    )


# ==================================================================================================
# V2-P3-003: the versioned preprocessing transform
# ==================================================================================================

# --- the two datasets a transform writes ----------------------------------------------------------


FACTOR_PROCESSED_DATASET_PREFIX: Final[str] = "factor_proc_"
FACTOR_TRANSFORM_MANIFEST_DATASET_PREFIX: Final[str] = "factor_procmn_"
"""The processed plane's two dataset-name prefixes, one pair per **factor**.

Deliberately short, and the shortness is a budget rather than terseness for its own sake.
`MAX_IDENTIFIER_LENGTH` is 63 and `MAX_FACTOR_KEY_LENGTH` is 40, so the longer of these two plus
the longest declarable factor key plus `"_v999"` is `14 + 40 + 5 = 59`.
`tests/unit/test_factor_transform_rules.py::
test_the_longest_legal_factor_key_still_names_a_legal_processed_dataset` builds that worst case
out of the constants rather than restating the arithmetic, so widening either one fails there
instead of at the first write.
"""

FACTOR_PROCESSED_KIND: Final[str] = "processed_factor_observation"
FACTOR_TRANSFORM_MANIFEST_KIND: Final[str] = "factor_transform_manifest"


def processed_factor_dataset(definition: FactorDefinition) -> str:
    """The panel dataset one factor's **processed** observations are filed under.

    Keyed by the factor and not by the transform; see this module's docstring for the
    name-length arithmetic that forces it and the two costs that follow.
    """
    return f"{FACTOR_PROCESSED_DATASET_PREFIX}{definition.key}_v{definition.version}"


def factor_transform_manifest_dataset(definition: FactorDefinition) -> str:
    """The panel dataset one factor's transform manifests are filed under."""
    return f"{FACTOR_TRANSFORM_MANIFEST_DATASET_PREFIX}{definition.key}_v{definition.version}"


PROCESSED_OBSERVATION_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "transform_id",
    "transform_key",
    "transform_version",
    "value",
    "coverage",
    "transform_manifest_id",
    "source_factor_id",
    "source_manifest_id",
    "source_coverage",
)
"""One stored processed observation, column by column.

`subject` and the four clocks come from `ColumnarPanelBatch`, exactly as they do for a raw
observation, and for a derived row every clock is the build's `as_of`.

The three `source_*` columns are what makes D8's "报告可比较 raw / processed / neutralized ...
而不覆盖源观测" answerable from a stored row:

- **`source_manifest_id`** with the row's own `subject` and `as_of` is the exact key of the raw
  observation in `factor_obs_<key>_v<n>`. `tests/integration/panel/test_factor_transforms.py::
  test_a_processed_row_names_the_exact_raw_row_it_came_from` performs that join rather than
  describing it, and `apply_factor_transform` refuses a source panel whose observations do not
  all carry its own `manifest_id`, so the pointer is a proved key rather than an assumed one.
- **`source_factor_id`** is the raw *definition*, so a reader of this partition alone knows which
  factor was transformed without parsing the dataset name.
- **`source_coverage`** is the raw row's own five-code marker, carried so that
  `source_not_computed` does not collapse the distinction the raw vocabulary spent five members
  drawing -- and so that "this value was imputed **because** the input was null" is a fact on one
  row rather than a join away.

`transform_key` and `transform_version` sit beside the opaque `transform_id` for the reason
`factor_key` and `factor_version` sit beside `factor_id`: a reader querying the partition
directly would otherwise need this build's registry to know what the rows are about.
"""

PROCESSED_OBSERVATION_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *PROCESSED_OBSERVATION_DATA_COLUMNS,
)

_PROCESSED_COLUMN_KINDS: Final[Mapping[str, PanelColumnKind]] = MappingProxyType(
    {
        "transform_id": "string",
        "transform_key": "string",
        "transform_version": "integer",
        "value": "float",
        "coverage": "string",
        "transform_manifest_id": "string",
        "source_factor_id": "string",
        "source_manifest_id": "string",
        "source_coverage": "string",
    }
)

PROCESSED_CENSUS_COLUMNS: Final[tuple[str, ...]] = tuple(
    f"{FACTOR_CENSUS_COLUMN_PREFIX}{code}" for code in PROCESSED_COVERAGE_ORDER
)
"""One stored count per declared processed coverage code, derived from the vocabulary.

`FACTOR_CENSUS_COLUMNS`' argument one plane over, and sharper here: a transform whose cross
section was too thin produces `insufficient_cross_section` for every security and a partition
full of nulls, which is byte-indistinguishable in storage from a transform that ran and imputed
nothing -- unless the counts are stored. Sharing `FACTOR_CENSUS_COLUMN_PREFIX` is deliberate:
these columns live in a different dataset from the raw census columns, so there is no collision,
and one prefix is one fewer string for a reader to learn.
"""

MISSING_VALUE_COLUMN_PREFIX: Final[str] = "missing_"

MISSING_VALUE_COLUMNS: Final[tuple[str, ...]] = tuple(
    f"{MISSING_VALUE_COLUMN_PREFIX}{code}" for code in MISSING_VALUE_COVERAGE_ORDER
)
"""One stored column per non-`computed` coverage code, holding the action the spec declared.

Derived from `MISSING_VALUE_COVERAGE_ORDER` rather than listed, so a sixth coverage code brings
a column without anybody remembering to add one -- and `domain/factor_transform.py`'s import-time
audit already refuses a sixth code with no policy field, so the two ends cannot drift apart in
either direction.
"""

TRANSFORM_MANIFEST_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "transform_id",
    "transform_key",
    "transform_version",
    "source_factor_id",
    "source_factor_key",
    "source_factor_version",
    "source_manifest_id",
    "source_observation_digest",
    "as_of_time",
    "code_commit",
    "winsorization_method",
    "winsorization_lower_quantile",
    "winsorization_upper_quantile",
    "winsorization_mad_scale",
    "standardization_method",
    "min_cross_section",
    *MISSING_VALUE_COLUMNS,
    *PROCESSED_CENSUS_COLUMNS,
    "participant_count",
    "winsorized_low_count",
    "winsorized_high_count",
    "imputed_count",
    "lower_bound",
    "upper_bound",
    "location",
    "scale",
)
"""One row per transform build. Three families, and only the first is in the content address.

- **The ten head columns** are `FactorTransformManifest`'s own fields (minus `schema_version`),
  so `_transform_manifest_from_row` reassembles a build from them and checks that the identity it
  reproduces is the one the row was stored under. "Head" is a description of this literal and not
  a requirement on it: that decoder addresses cells by column *name*, so moving a hashed field
  down the tuple would change nothing at run time. What the grouping buys is that
  `_TRANSFORM_MANIFEST_HEAD_COLUMNS` can be a slice instead of a second list.
- **The declared policy** -- the winsorization method and its parameters, the standardization
  method, `min_cross_section` and the four missing-value actions -- is a projection of
  `transform_id`, stored for `FactorBuildManifest.direction`'s reason and a sharper one: a
  processed `value` column is *uninterpretable* without knowing whether it is a z-score, a
  centred rank or a raw winsorized number, and a reader who cannot resolve `transform_id`
  against a registry has no other way to find out.
- **The statistics** are `FactorTransformStatistics`: outputs, recorded and deliberately out of
  the content address (an identity computed from a build's outputs is one nobody can predict
  before running the build). `winsorized_low_count` and `winsorized_high_count` are the pair that
  makes a declared winsorization *falsifiable* on a stored partition -- a 1% policy that clipped
  one name out of three clipped 33% of the cross section, and only a count says so.

Flat rather than nested for `FACTOR_MANIFEST_DATA_COLUMNS`' reason: a partition is a rectangle,
and the alternatives are a JSON blob in one column (which the panel plane exists to stop) or a
third dataset.
"""

TRANSFORM_MANIFEST_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *TRANSFORM_MANIFEST_DATA_COLUMNS,
)

_TRANSFORM_MANIFEST_HEAD_COLUMNS: Final[tuple[str, ...]] = TRANSFORM_MANIFEST_DATA_COLUMNS[:10]
"""The ten columns `FactorTransformManifest` is reassembled from -- an audit handle, not a
run-time one.

**Nothing in `src/` reads this.** `_transform_manifest_from_row` zips
`TRANSFORM_MANIFEST_PANEL_COLUMNS` against the row and addresses every cell by name, so the
hashed fields could sit anywhere in the tuple and the decoder would not notice. Its one consumer
is `tests/unit/test_factor_transform_rules.py::
test_the_stored_head_columns_are_exactly_the_hashed_manifests_own_fields`, which reconciles the
slice against `FactorTransformManifest`'s own field set -- so an eleventh manifest field, or a
hashed field that stopped being stored, fails there instead of at the first read-back. That is
what keeps `10` from being a number somebody has to remember; it is not a claim that the column
*order* is load-bearing.

A slice of the tuple above rather than a second list, so the two cannot drift.
"""


def _kinds(names: Sequence[str], kind: PanelColumnKind) -> dict[str, PanelColumnKind]:
    """One SQL kind for a family of derived column names, keeping the family and its type paired.

    `dict.fromkeys(names, kind)` infers `dict[str, str]` under mypy strict, so the alternative is
    a `cast` at each of the two call sites -- and a `cast` is exactly what should not stand
    between a derived column list and its declared type, because the thing being asserted is that
    every member of the family has one.
    """
    return dict.fromkeys(names, kind)


_TRANSFORM_MANIFEST_COLUMN_KINDS: Final[Mapping[str, PanelColumnKind]] = MappingProxyType(
    {
        "transform_id": "string",
        "transform_key": "string",
        "transform_version": "integer",
        "source_factor_id": "string",
        "source_factor_key": "string",
        "source_factor_version": "integer",
        "source_manifest_id": "string",
        "source_observation_digest": "string",
        "as_of_time": "timestamp",
        "code_commit": "string",
        "winsorization_method": "string",
        "winsorization_lower_quantile": "float",
        "winsorization_upper_quantile": "float",
        "winsorization_mad_scale": "float",
        "standardization_method": "string",
        "min_cross_section": "integer",
        **_kinds(MISSING_VALUE_COLUMNS, "string"),
        **_kinds(PROCESSED_CENSUS_COLUMNS, "integer"),
        "participant_count": "integer",
        "winsorized_low_count": "integer",
        "winsorized_high_count": "integer",
        "imputed_count": "integer",
        "lower_bound": "float",
        "upper_bound": "float",
        "location": "float",
        "scale": "float",
    }
)


# --- the transform that ships ---------------------------------------------------------------------


CROSS_SECTION_STANDARD: Final[FactorTransformSpec] = FactorTransformSpec(
    key="cross_section_standard",
    version=1,
    winsorization=WinsorizationPolicy(method="quantile", lower_quantile=0.01, upper_quantile=0.99),
    standardization="zscore",
    missing_values=MissingValuePolicy(
        not_in_universe="exclude",
        insufficient_history="exclude",
        input_missing="fill_cross_sectional_median",
        undefined_value="exclude",
    ),
    min_cross_section=100,
)
"""The single registered transform, and every one of its settings is a stated judgement.

It is not a default in the sense of "what you get if you say nothing": `apply_factor_transform`
takes the spec as a mandatory argument, and this is the one this build ships so that the shipped
configuration is exercised end to end rather than only probe specs invented by tests.

`min_cross_section = 100 = 1 / lower_quantile` is the only number here that is *derived* rather
than chosen, and it is derived because the alternative is the failure this repository has already
paid for once -- a delivered proof hanging on a free parameter. `_quantile` interpolates between
order statistics, so the `q`-quantile of `n` points sits at position `(n - 1) * q` and the number
of names strictly below it is `ceil((n - 1) q)` -- **never fewer than one**. That floor is what
makes a small cross section misbehave: the fraction actually clipped is `max(1 / n, ~q)`, so a
policy that says 1% clips 33% of a three-name cross section and 10% of a ten-name one, and
`n = 1 / q` is the smallest size at which it first clips what it declares.
`tests/unit/test_factor_transform_rules.py::
test_a_one_percent_winsorization_clips_a_third_of_a_three_name_cross_section` measures the whole
curve, counts and fractions both.
"""

CROSS_SECTION_STANDARD_NOTE: Final[FactorNote] = FactorNote(
    subject=CROSS_SECTION_STANDARD.qualified_key,
    summary=(
        "The conventional cross-sectional preprocessing: clip to the empirical 1st and 99th "
        "percentiles of the securities that were scored at this as_of, then z-score what "
        "remains against the population mean and standard deviation of the clipped values. A "
        "security that was not in the universe is excluded rather than filled -- it is not a "
        "hole, it is a name that should have no value. A security that was in the universe and "
        "too young for the lookback is excluded too, because imputing the median for it would "
        "score a listing on data it does not have. A null input is filled with the median of "
        "the processed cross section, which is the case a fill is actually for. An undefined "
        "arithmetic result is excluded rather than refused, because a zero denominator is a "
        "property of the factor's own definition and a whole build should not die of one "
        "security's. min_cross_section is 100 because that is 1 / lower_quantile: the smallest "
        "cross section for which a 1% winsorization clips about 1% of it. Below it the bound is "
        "an interpolation whose position is set by n rather than by the tail -- at n=3 the same "
        "policy clips a third of the cross section -- and this transform declines to produce "
        "numbers whose winsorization was a function of how many names happened to be scored."
    ),
)
"""`CROSS_SECTION_STANDARD`'s prose, out of `transform_id`. See `domain/factor.py::FactorNote`.

Word for word what the spec's own `summary` field carried until this change, so the diff shows a
relocation rather than an edit.
"""

FACTOR_TRANSFORMS: Final[FactorTransformRegistry] = FactorTransformRegistry(
    (CROSS_SECTION_STANDARD,), notes=(CROSS_SECTION_STANDARD_NOTE,)
)
"""Every preprocessing transform this build declares, and the prose about it.

`V2-P3-014`'s three-tier report extends both.
"""


# --- winsorization -------------------------------------------------------------------------------


class _Winsorizer(Protocol):
    """The clipping half of a transform: a policy and an ascending cross section in, bounds out.

    `None` means "this method clips nothing", which is `method="none"`'s whole answer and is not
    the same as bounds that happen to be the minimum and the maximum -- the second would report
    every value as unclipped while putting a `lower_bound` on the manifest that a reader would
    take for a policy decision.
    """

    def __call__(
        self, policy: WinsorizationPolicy, ordered: Sequence[float]
    ) -> tuple[float, float] | None: ...


def _no_winsorization(
    policy: WinsorizationPolicy, ordered: Sequence[float]
) -> tuple[float, float] | None:
    return None


def _quantile_bounds(
    policy: WinsorizationPolicy, ordered: Sequence[float]
) -> tuple[float, float] | None:
    """`[quantile(lower), quantile(upper)]` of the ascending participants."""
    lower, upper = policy.lower_quantile, policy.upper_quantile
    if lower is None or upper is None:
        raise FactorTransformError(
            f"a quantile winsorization declares no quantiles ({lower!r}, {upper!r}); "
            "WinsorizationPolicy's validator refuses that, so this policy reached the engine "
            "through model_construct or a subclass rather than through the contract"
        )
    return (_quantile(ordered, lower), _quantile(ordered, upper))


def _mad_bounds(
    policy: WinsorizationPolicy, ordered: Sequence[float]
) -> tuple[float, float] | None:
    """`median +/- mad_scale * MAD`, where `MAD` is the median absolute deviation from the median.

    The deviations are re-sorted rather than assumed to inherit the input's order: `|x - median|`
    is V-shaped about the median, so an ascending series of values is a descending-then-ascending
    series of deviations, and `_quantile` of it unsorted would be refused (it checks) or, without
    that check, would return an arbitrary element.
    """
    scale = policy.mad_scale
    if scale is None:
        raise FactorTransformError(
            "a mad winsorization declares no mad_scale; WinsorizationPolicy's validator refuses "
            "that, so this policy reached the engine through model_construct or a subclass "
            "rather than through the contract"
        )
    centre = _quantile(ordered, 0.5)
    deviation = _median(sorted(abs(value - centre) for value in ordered))
    return (centre - scale * deviation, centre + scale * deviation)


_WINSORIZERS: Final[Mapping[WinsorizationMethod, _Winsorizer]] = MappingProxyType(
    {
        "none": _no_winsorization,
        "quantile": _quantile_bounds,
        "mad": _mad_bounds,
    }
)


# --- standardization ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Standardized:
    """One standardized cross section, plus whatever location and scale were estimated for it."""

    values: tuple[float, ...]
    location: float | None
    scale: float | None


class _Standardizer(Protocol):
    """The scaling half of a transform: winsorized values in, standardized values out.

    `None` means **this method found nothing to order in this cross section**, and it becomes
    `degenerate_cross_section`. It is returned by the method rather than decided by the engine
    from a `min == max` test, which is the difference between a rule and a guess: a z-score's
    degeneracy is `stdev == 0` or a `stdev` that is not finite, and floating-point overflow makes
    the second reachable on values that are very much not equal -- `[1e308, -1e308]` sums a
    variance to `inf`.
    """

    def __call__(self, values: Sequence[float]) -> _Standardized | None: ...


def _standardize_none(values: Sequence[float]) -> _Standardized | None:
    """Pass the winsorized values through, estimating nothing.

    The one method that is never degenerate. It makes no ordering claim -- "do not standardize"
    is exactly what it was asked to do -- so a constant cross section is a faithful answer rather
    than a z-score's `0 / 0`. `STANDARDIZATION_NEUTRAL` records the other side of that: there is
    no neutral point on a scale nothing was centred on, and a `fill_neutral` beside it is refused
    at declaration time.
    """
    return _Standardized(values=tuple(values), location=None, scale=None)


def _standardize_zscore(values: Sequence[float]) -> _Standardized | None:
    """`(x - mean) / population_stdev`, or `None` when there is no usable scale.

    Two-pass rather than `E[x^2] - E[x]^2`, and `math.fsum` rather than `sum`, because the
    one-pass form cancels catastrophically on a cross section whose values are large and close
    together -- a factor whose raw values sit around 1e6 with a spread of 1e-3 is exactly that
    shape, and the naive variance of it comes out negative as often as not.

    Population rather than sample: see `StandardizationMethod`. There is no third guard on the
    output because there cannot be one to catch -- `scale` is the root mean square of
    `x - mean`, so `|x_i - mean| <= sqrt(n) * scale` for every `i`, and every z-score of a cross
    section with a finite positive scale is bounded by `sqrt(n)` by construction.

    Squared with `delta * delta` rather than `delta ** 2`, and that is not a style choice:
    CPython's float `**` raises `OverflowError` where `*` returns `inf`, so the exponent form
    turns an over-wide cross section into an exception from inside an arithmetic helper instead
    of into the `degenerate_cross_section` this function is supposed to report.
    `test_a_z_score_whose_variance_overflows_is_degenerate_rather_than_infinite` drives it.
    """
    count = len(values)
    mean = math.fsum(values) / count
    scale = math.sqrt(math.fsum((value - mean) * (value - mean) for value in values) / count)
    if scale <= 0.0 or not math.isfinite(scale):
        return None
    return _Standardized(
        values=tuple((value - mean) / scale for value in values), location=mean, scale=scale
    )


def _standardize_rank(values: Sequence[float]) -> _Standardized | None:
    """The centred average rank: `(rank - (n + 1) / 2) / n`, or `None` when everything ties.

    Mean exactly zero for every `n`, including `n = 1`, which is why the denominator is `n` and
    not the `n - 1` that would make the range exactly `[-0.5, 0.5]`: a rule with a special case at
    `n = 1` is a rule with a branch nothing exercises until the day it does. Bounded by `0.5`
    either way, so the output is always finite.

    Estimates no location and no scale: a rank is not an affine image of its input, so neither
    number would describe what was done, and storing a mean the transform did not subtract would
    be a provenance field that is wrong rather than absent.

    **All-tied is `None` rather than a cross section of zeros**, and that is the judgement worth
    stating because the arithmetic has an answer here and `zscore`'s does not. A cross section
    where every value ties has nothing to order, and `0.0` for everybody reads downstream as a
    valid centred score -- an information coefficient or a neutralisation would consume it as
    data. A z-score's `0 / 0` and a rank's total tie are the same fact about the cross section,
    and answering them differently would make the degeneracy depend on a knob that is about
    output *shape* rather than about whether there is anything to say. `none` is different in
    kind, not in degree: it declines to order at all.

    Ties are found by **exact** float equality, which is the honest bound: two values differing
    in the last bit rank as distinct, so a factor with a heavily discretised numerator gets an
    ordering its raw numbers barely support. A tolerance would need a scale to be relative to,
    and the only scale on offer is the cross section's own dispersion -- the quantity `rank`
    exists to avoid depending on.
    """
    if min(values) == max(values):
        return None
    count = len(values)
    middle = (count + 1) / 2.0
    return _Standardized(
        values=tuple((rank - middle) / count for rank in _average_ranks(values)),
        location=None,
        scale=None,
    )


_STANDARDIZERS: Final[Mapping[StandardizationMethod, _Standardizer]] = MappingProxyType(
    {
        "none": _standardize_none,
        "zscore": _standardize_zscore,
        "rank": _standardize_rank,
    }
)


# --- the missing-value policy, applied ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Imputation:
    """What a missing-value action produced: a value or none, and the code it is stored under."""

    value: float | None
    coverage: ProcessedCoverage


@dataclass(frozen=True, slots=True)
class _FillContext:
    """What a fill may draw on: the processed cross section's median, and the method's neutral.

    Both are computed from the **already standardized** participants, which is the whole of the
    "an imputation does not feed back into the statistics it was drawn from" property: the
    context is built after `_STANDARDIZERS` has run and nothing in it re-enters the mean, the
    deviation or the winsorization bounds.
    """

    processed_median: float
    neutral: float | None


class _MissingValueApplier(Protocol):
    def __call__(self, context: _FillContext) -> _Imputation: ...


def _exclude_from_the_cross_section(context: _FillContext) -> _Imputation:
    return _Imputation(value=None, coverage="source_not_computed")


def _fill_with_the_cross_sectional_median(context: _FillContext) -> _Imputation:
    return _Imputation(value=context.processed_median, coverage="imputed")


def _fill_with_the_neutral_value(context: _FillContext) -> _Imputation:
    if context.neutral is None:
        raise FactorTransformError(
            "fill_neutral was asked for under a standardization with no neutral point; "
            "FactorTransformSpec's validator refuses that combination, so this spec reached the "
            "engine through model_construct or a subclass rather than through the contract"
        )
    return _Imputation(value=context.neutral, coverage="imputed")


_MISSING_VALUE_APPLIERS: Final[Mapping[MissingValueAction, _MissingValueApplier]] = (
    MappingProxyType(
        {
            "exclude": _exclude_from_the_cross_section,
            "fill_cross_sectional_median": _fill_with_the_cross_sectional_median,
            "fill_neutral": _fill_with_the_neutral_value,
        }
    )
)
"""Three of the four declared actions. The fourth is named below rather than left missing."""

REFUSAL_ACTION: Final[MissingValueAction] = "refuse"
"""The one `MissingValueAction` that is not an imputation and therefore not in the table above.

Named as a constant rather than left as an absence, because "the table is missing an entry" and
"the table deliberately does not cover this member" look identical to a reader and are opposite
facts to a maintainer. `_refuse_transform_table_drift` asserts both halves -- that the appliers
plus this one are exactly `MISSING_VALUE_ACTIONS`, and that this one is *not* among the appliers
-- so a fifth action has to be classified into one of the two before the module will import.

It is applied before anything else in `apply_factor_transform`
(`_refuse_source_codes_the_policy_rejects`) rather than inside the per-observation loop, and the
ordering is load-bearing: a caller who declared "an `undefined_value` in this cross section is a
fault" gets that refusal even at an `as_of` whose cross section was too thin to process at all,
because the fault is in the inputs and the thinness is a separate fact about them.
"""


def _refuse_transform_table_drift(
    winsorizers: Mapping[WinsorizationMethod, _Winsorizer],
    standardizers: Mapping[StandardizationMethod, _Standardizer],
    appliers: Mapping[MissingValueAction, _MissingValueApplier],
    coverage_order: Sequence[str],
) -> None:
    """Refuse a vocabulary with a member no branch implements, and a branch with no member.

    `_refuse_table_drift`'s argument applied to four closed sets at once, with the same measured
    failure behind it: `PANEL_BUILD_TARGETS` gained keys whose branches did not exist and the
    command answered exit 0 with an empty partition list. The shape here would be worse than an
    empty success -- a declared `WinsorizationMethod` with no winsorizer raises `KeyError` from a
    dict lookup at the first cross section that uses it, in production, with a message that names
    neither the method nor the spec.

    Five checks rather than one, because they fail differently and a reader has to know which: a
    method with no implementation, an implementation with no declared method, a standardization
    with no declared neutral point (which decides whether `fill_neutral` is even legal beside it),
    an action that is neither an imputation nor the one refusal, and a census order that has
    drifted from the vocabulary it restates.

    Every input is an argument rather than a module global, so all five failure directions are
    drivable from a test. An audit whose only call site is the one that passes is an audit nobody
    has seen fail, which is the shape `_refuse_table_drift` earned a third test for.
    """
    # The implemented sets are widened to `set[str]` rather than left as sets of the `Literal`
    # they are keyed by: mypy reads `frozenset[str] != set[Literal[...]]` as a non-overlapping
    # comparison and refuses it, which would make the audit's own equality unwritable.
    checks: tuple[tuple[str, frozenset[str], set[str]], ...] = (
        ("winsorization method", WINSORIZATION_METHODS, {str(key) for key in winsorizers}),
        ("standardization method", STANDARDIZATION_METHODS, {str(key) for key in standardizers}),
        (
            "standardization neutral point",
            STANDARDIZATION_METHODS,
            {str(key) for key in STANDARDIZATION_NEUTRAL},
        ),
    )
    for name, declared, implemented in checks:
        if declared != implemented:
            raise FactorTransformError(
                f"the {name} vocabulary and its table disagree: "
                f"{sorted(declared - implemented)} are declared with no entry and "
                f"{sorted(implemented - declared)} are implemented with nothing declaring them. "
                "A declared method with no branch behind it fails at the first cross section "
                "that asks for it"
            )
    covered = {str(key) for key in appliers} | {str(REFUSAL_ACTION)}
    if covered != MISSING_VALUE_ACTIONS or REFUSAL_ACTION in appliers:
        raise FactorTransformError(
            f"the missing-value actions are {sorted(MISSING_VALUE_ACTIONS)} and this build "
            f"imputes {sorted(appliers)} with {REFUSAL_ACTION!r} handled separately; every "
            "action must be exactly one of the two, because an action in neither is one no "
            "cross section will ever apply and an action in both is one applied twice"
        )
    if set(coverage_order) != PROCESSED_COVERAGE_CODES or len(set(coverage_order)) != len(
        coverage_order
    ):
        raise FactorTransformError(
            f"the processed census order is {list(coverage_order)} and the declared codes are "
            f"{sorted(PROCESSED_COVERAGE_CODES)}; the order decides a census key order and a "
            "stored column list, and two copies of a closed set drift"
        )


_refuse_transform_table_drift(
    _WINSORIZERS, _STANDARDIZERS, _MISSING_VALUE_APPLIERS, PROCESSED_COVERAGE_ORDER
)


# --- the arithmetic ------------------------------------------------------------------------------


def _quantile(ordered: Sequence[float], fraction: float) -> float:
    """The `fraction`-quantile of an **ascending** sequence, by linear interpolation.

    The definition is pinned here rather than borrowed, and the choice matters more than it
    looks: `statistics.quantiles` cuts a distribution into `n` intervals under an *exclusive*
    rule by default and returns cut points rather than one quantile; numpy's default is this one.
    The rule is that the quantile sits at position `(len - 1) * fraction` among the order
    statistics, interpolating linearly between the two it falls between -- so `fraction=0` is the
    minimum, `fraction=1` is the maximum, and `fraction=0.5` is the median (the average of the two
    middle values on an even-length sequence).

    `_median` is this function at `0.5` rather than `statistics.median`, so the transform layer
    has one definition of "the middle" instead of two that agree today.

    **The consequence on a small cross section is the one worth knowing**, and it is a property
    of the rule rather than of any implementation: at `len = 3`, `fraction = 0.01` puts the
    position at `0.02`, so the "1% quantile" sits 2% of the way from the smallest value to the
    second smallest -- a bound whose distance from the minimum is set by the *size* of the cross
    section and not by its tail. `FactorTransformSpec.min_cross_section` is the declared answer to
    that, and `tests/unit/test_factor_transform_rules.py` carries the arithmetic as numbers.

    Ascending order is a precondition rather than something this function establishes, because
    every caller already holds a sorted sequence and re-sorting at 5,534 names per `as_of` is a
    real cost. The precondition is *checked* rather than assumed: an unsorted argument would
    otherwise return a plausible number computed from the wrong order statistics, which is the
    kind of defect that never surfaces as an error.
    """
    if not ordered:
        raise FactorTransformError("a quantile of an empty cross section has no value")
    if not 0.0 <= fraction <= 1.0:
        raise FactorTransformError(f"a quantile fraction must be in [0, 1]; got {fraction!r}")
    if any(ordered[index] > ordered[index + 1] for index in range(len(ordered) - 1)):
        raise FactorTransformError(
            "_quantile takes an ascending sequence and was given an unsorted one; it would "
            "otherwise return a number computed from the wrong order statistics"
        )
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def _median(ordered: Sequence[float]) -> float:
    """The middle of an ascending sequence, defined as `_quantile(ordered, 0.5)`."""
    return _quantile(ordered, 0.5)


def _average_ranks(values: Sequence[float]) -> tuple[float, ...]:
    """One-based ranks in the argument's own order, with tied values sharing their average rank.

    `(1.0, 3.0, 3.0, 7.0)` ranks as `(1.0, 2.5, 2.5, 4.0)`: the two tied values would have taken
    ranks 2 and 3, and averaging is what keeps the rank sum equal to `n (n + 1) / 2` whatever the
    ties are -- which is what makes `_standardize_rank`'s centring exact rather than
    approximately zero.

    Ties are found by exact float equality; see `_standardize_rank` for the bound that puts on
    this.
    """
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + end) / 2.0 + 1.0
        for position in range(start, end + 1):
            ranks[order[position]] = average
        start = end + 1
    return tuple(ranks)


def _population_stdev(values: Sequence[float]) -> float:
    """The population standard deviation, exposed for the test that pins the estimator.

    `_standardize_zscore` computes it inline -- it needs the mean anyway, and a second pass over
    5,534 values per `as_of` for a number it already holds is a cost with no buyer. This is the
    same arithmetic under a name a test can drive directly, and the two are reconciled by
    `tests/unit/test_factor_transform_rules.py::
    test_the_z_score_divides_by_the_population_deviation_this_function_returns`, so the
    "obvious" `statistics.stdev` substitution fails there instead of shipping numbers that are
    0.5% different at n=100 and 22% different at n=3.
    """
    if not values:
        raise FactorTransformError("the standard deviation of an empty cross section has no value")
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - mean) * (value - mean) for value in values) / len(values))


# --- the processed result -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessedFactorPanel:
    """One transform of one factor at one `as_of`: the manifest, every row, and what it measured.

    `FactorPanel`'s shape one plane up, and `built_at` is here rather than on
    `FactorTransformManifest` for the same reason it is here rather than on
    `FactorBuildManifest`: the wall clock is recorded (it becomes the partition's
    `ColumnarPanelBatch.fetched_at`) and kept out of the content address, so re-applying the same
    transform to the same source build reproduces its `transform_manifest_id` -- which is what
    makes a rebuild writable past `_refuse_to_drop_a_stored_build` at all.
    """

    definition: FactorDefinition
    spec: FactorTransformSpec
    manifest: FactorTransformManifest
    observations: tuple[ProcessedFactorObservation, ...]
    statistics: FactorTransformStatistics
    built_at: datetime

    @property
    def as_of(self) -> datetime:
        return self.manifest.as_of

    def coverage_census(self) -> Mapping[str, int]:
        """How many rows carry each processed coverage code, including the zeros.

        Every declared code is present with a count, `FactorPanel.coverage_census()`'s reason: a
        report reads "0 imputed" rather than inferring it from an absent key.
        """
        census: dict[str, int] = dict.fromkeys(PROCESSED_COVERAGE_ORDER, 0)
        for observation in self.observations:
            census[observation.coverage] += 1
        return MappingProxyType(census)

    def values(self) -> Mapping[str, float]:
        """Every subject that has a processed number, **including the imputed ones**.

        The inclusive reading, because a caller that declared a fill policy asked for those
        numbers; `measured_values()` is the other half and `imputed_subjects()` is how a caller
        separates them without re-deriving the policy.
        """
        return MappingProxyType(
            {
                observation.subject: observation.value
                for observation in self.observations
                if observation.value is not None
            }
        )

    def measured_values(self) -> Mapping[str, float]:
        """Only the subjects whose number came from a `computed` raw observation.

        The cross section an information coefficient should be computed on: an imputed median is
        a number this repository made up, and a correlation that consumed it would be measuring
        the fill rate as much as the factor. `V2-P3-005` is the first caller that has to choose,
        and the point of having two methods is that it has to choose rather than inherit.
        """
        return MappingProxyType(
            {
                observation.subject: observation.value
                for observation in self.observations
                if observation.coverage == "processed" and observation.value is not None
            }
        )

    def imputed_subjects(self) -> tuple[str, ...]:
        """Every subject whose number the missing-value policy supplied, in row order."""
        return tuple(
            observation.subject
            for observation in self.observations
            if observation.coverage == "imputed"
        )


# --- applying ------------------------------------------------------------------------------------


def apply_factor_transform(
    panel: FactorPanel,
    spec: FactorTransformSpec,
    *,
    code_commit: str,
    built_at: datetime,
) -> ProcessedFactorPanel:
    """Winsorize, standardize and resolve the missing values of one cross section.

    ## The four arguments, and the one that is not here

    - **`panel`** is the whole input. There is no `store`, no `as_of` and no universe: the
      securities that participate in a cross-sectional statistic are exactly the observations
      `compute_factor` produced at that `as_of`, and the `as_of` is the panel's own. That absence
      is the point-in-time argument at this layer -- a transform cannot read a row that was not
      knowable, because it cannot read a row at all -- and it is audited by signature rather than
      asserted: `test_the_transform_takes_no_store_and_therefore_no_second_visibility_rule` reads
      the parameters and their annotations.
    - **`spec`** is mandatory and has no default. A defaulted preprocessing policy is the shape
      `ReadinessRequirement`'s four checks were changed to refuse: the most permissive
      configuration would also be the easiest one to get, and the *stored* numbers would then
      depend on a decision nobody recorded taking.
    - **`code_commit`** is provenance the panel plane cannot resolve for itself (no top-level
      `panel_*` module may import `runtime`, where `resolve_code_commit()` lives), and it has no
      default for the reason `V2-P0B-009` deleted `"development"`.
    - **`built_at`** is the wall clock, deliberately out of the content address.

    **`date_timezone` is deliberately not a parameter**, and its absence is a claim rather than
    an omission: this function resolves no date. `compute_factor` needs one because it turns a
    UTC instant into a session date and the resolution decides which rows fall in a window; a
    transform reads values and codes off observations that already carry an aware `as_of`. The
    only timezone in its life belongs to `write_processed_factor_panels`, which uses one to
    decide a partition **year**, exactly as `write_factor_panels` does.

    ## The order of operations, and why each step is where it is

    1. **Refuse what the policy declared unacceptable.** Before anything else, so that a caller
       who said "an `undefined_value` here is a fault" gets that answer even at an `as_of` whose
       cross section was too thin to process. See `REFUSAL_ACTION`.
    2. **Select the participants: exactly the `computed` observations.** Not a choice so much as
       an arithmetic -- every other code carries `value=None` by a rule with three enforcement
       points, so there is no number to include, and substituting zero is what
       `FactorPanel.values()` exists to refuse.
    3. **Refuse a cross section thinner than `spec.min_cross_section`**, as a whole-panel
       `insufficient_cross_section` rather than as an exception: a thin cross section at some
       historical `as_of` is an answer about the market, and a build that raised on it could not
       backfill a year that contains one.
    4. **Winsorize the raw participant values.** Bounds come from the *raw* cross section,
       because that is what a tail is.
    5. **Standardize the winsorized values.** In that order, because clipping first is the entire
       reason to clip: the standard deviation a z-score divides by should not be the one the
       outliers set. Reversing the two changes every number, and
       `test_the_deviation_the_z_score_divides_by_is_the_winsorized_one` measures the difference
       rather than asserting the order.
    6. **Resolve the non-participants** from the standardized cross section. Fills are drawn
       *after* step 5 and never re-enter it; see this module's docstring for the measurement.

    ## What determines the answers, and therefore what `transform_manifest_id` has to cover

    Every argument is either represented in `manifest.transform_manifest_id` or exempt with a
    written reason, and `tests/integration/panel/test_factor_transforms.py::
    test_every_determinant_of_this_transform_is_either_in_the_identity_or_exempted_by_name` reads
    this function's own signature and fails on a parameter in neither list --
    `test_every_determinant_of_this_build_is_either_in_the_identity_or_exempted_by_name`'s
    instrument, applied to the function this issue adds. A fifth parameter fails the audit until
    somebody classifies it.

    `panel` reaches the identity twice over: through `source_manifest_id`, which says which build
    it was, and through `source_observation_digest`, which says what the numbers in it were. The
    second is not redundant -- a manifest identifies a computation's *inputs*, so two panels
    carrying one `manifest_id` and different observations are constructible, and without the
    digest the audit would be exempting `panel` on a promise. See
    `domain/factor_transform.py::observation_digest`.
    """
    observations = panel.observations
    if not observations:
        raise FactorTransformError(
            "apply_factor_transform needs a panel with at least one observation; an empty cross "
            "section produces an empty processed panel that is indistinguishable from one where "
            "nothing could be transformed"
        )
    _refuse_a_source_panel_that_does_not_own_its_observations(panel)
    _refuse_source_codes_the_policy_rejects(observations, spec)
    manifest = FactorTransformManifest(
        transform_id=spec.transform_id,
        transform_key=spec.key,
        transform_version=spec.version,
        source_factor_id=panel.definition.factor_id,
        source_factor_key=panel.definition.key,
        source_factor_version=panel.definition.version,
        source_manifest_id=panel.manifest.manifest_id,
        source_observation_digest=observation_digest(observations),
        as_of=panel.as_of,
        code_commit=code_commit,
    )
    # Read once, outside every loop below. `transform_manifest_id` is a pydantic
    # `computed_field`, which is not cached -- `domain/panel_batch.py` measured 10.5 ms on a
    # first access and 10.2 ms on a second for `ProviderBatch.payload_digest`, and ADR-0003
    # records this module walking into exactly that trap once already, re-hashing a build
    # manifest 5,534 times inside a per-security loop.
    manifest_id = manifest.transform_manifest_id
    participants = tuple(item for item in observations if item.coverage == "computed")
    raw = tuple(item.value for item in participants if item.value is not None)
    if len(raw) != len(participants):
        raise FactorTransformError(
            f"{len(participants) - len(raw)} of this panel's `computed` observations carry no "
            "value; exactly the `computed` code carries one and every other code carries None, "
            "so such a row reached the panel past both of validate_factor_observation's call "
            "sites -- through a subclass that overrode __post_init__"
        )

    if len(participants) < spec.min_cross_section:
        return _uniform_processed_panel(
            panel,
            spec,
            manifest=manifest,
            manifest_id=manifest_id,
            coverage="insufficient_cross_section",
            statistics=FactorTransformStatistics(
                participant_count=len(participants),
                winsorized_low_count=0,
                winsorized_high_count=0,
                imputed_count=0,
                lower_bound=None,
                upper_bound=None,
                location=None,
                scale=None,
            ),
            built_at=built_at,
        )

    ordered = sorted(raw)
    bounds = _WINSORIZERS[spec.winsorization.method](spec.winsorization, ordered)
    if bounds is None:
        winsorized, lower, upper, low_count, high_count = raw, None, None, 0, 0
    else:
        lower, upper = bounds
        _refuse_a_scale_estimator_that_collapsed(spec, ordered=ordered, lower=lower, upper=upper)
        winsorized = tuple(min(max(value, lower), upper) for value in raw)
        low_count = sum(1 for value in raw if value < lower)
        high_count = sum(1 for value in raw if value > upper)

    standardized = _STANDARDIZERS[spec.standardization](winsorized)
    if standardized is None:
        return _uniform_processed_panel(
            panel,
            spec,
            manifest=manifest,
            manifest_id=manifest_id,
            coverage="degenerate_cross_section",
            statistics=FactorTransformStatistics(
                participant_count=len(participants),
                winsorized_low_count=low_count,
                winsorized_high_count=high_count,
                imputed_count=0,
                lower_bound=lower,
                upper_bound=upper,
                location=None,
                scale=None,
            ),
            built_at=built_at,
        )

    processed = dict(zip((item.subject for item in participants), standardized.values, strict=True))
    context = _FillContext(
        processed_median=_median(sorted(standardized.values)),
        neutral=STANDARDIZATION_NEUTRAL[spec.standardization],
    )
    rows: list[ProcessedFactorObservation] = []
    imputed = 0
    for observation in observations:
        if observation.coverage == "computed":
            rows.append(
                _processed_row(
                    observation,
                    spec,
                    manifest_id=manifest_id,
                    value=processed[observation.subject],
                    coverage="processed",
                )
            )
            continue
        imputation = _MISSING_VALUE_APPLIERS[spec.missing_values.action_for(observation.coverage)](
            context
        )
        imputed += int(imputation.value is not None)
        rows.append(
            _processed_row(
                observation,
                spec,
                manifest_id=manifest_id,
                value=imputation.value,
                coverage=imputation.coverage,
            )
        )
    return ProcessedFactorPanel(
        definition=panel.definition,
        spec=spec,
        manifest=manifest,
        observations=tuple(rows),
        statistics=FactorTransformStatistics(
            participant_count=len(participants),
            winsorized_low_count=low_count,
            winsorized_high_count=high_count,
            imputed_count=imputed,
            lower_bound=lower,
            upper_bound=upper,
            location=standardized.location,
            scale=standardized.scale,
        ),
        built_at=built_at,
    )


def _processed_row(
    observation: FactorObservation,
    spec: FactorTransformSpec,
    *,
    manifest_id: str,
    value: float | None,
    coverage: ProcessedCoverage,
) -> ProcessedFactorObservation:
    """One processed row, carrying the pointer back to the raw row it came from."""
    return ProcessedFactorObservation(
        subject=observation.subject,
        as_of=observation.as_of,
        value=value,
        coverage=coverage,
        transform_id=spec.transform_id,
        transform_manifest_id=manifest_id,
        source_factor_id=observation.factor_id,
        source_manifest_id=observation.manifest_id,
        source_coverage=observation.coverage,
    )


def _uniform_processed_panel(
    panel: FactorPanel,
    spec: FactorTransformSpec,
    *,
    manifest: FactorTransformManifest,
    manifest_id: str,
    coverage: ProcessedCoverage,
    statistics: FactorTransformStatistics,
    built_at: datetime,
) -> ProcessedFactorPanel:
    """One row per source observation, all carrying a whole-panel code and no value.

    Both whole-panel codes reach every observation, **including the ones whose source was not
    `computed`**, and that is a judgement rather than a shortcut. "There is no processed cross
    section at this `as_of`" is the dominant fact; reporting `source_not_computed` for some names
    while others said `degenerate_cross_section` would suggest the first group could have been
    processed and merely lacked an input, which is false -- nothing was processed. The reason
    each individual name had no raw value is not lost: `source_coverage` is on every row.
    """
    return ProcessedFactorPanel(
        definition=panel.definition,
        spec=spec,
        manifest=manifest,
        observations=tuple(
            _processed_row(
                observation, spec, manifest_id=manifest_id, value=None, coverage=coverage
            )
            for observation in panel.observations
        ),
        statistics=statistics,
        built_at=built_at,
    )


def _refuse_a_source_panel_that_does_not_own_its_observations(panel: FactorPanel) -> None:
    """Refuse a panel whose rows do not all belong to the build its manifest describes.

    What makes `(source_manifest_id, subject, as_of)` a **proved** key of the raw partition
    rather than an assumed one. `compute_factor` stamps one `manifest_id`, one `factor_id` and
    one `as_of` on every observation it produces, so this never fires on its output -- but this
    function's input is a `FactorPanel`, which is a public frozen dataclass anybody can
    construct, and a hand-assembled one with a mismatched row would store a processed value
    whose provenance pointer names a build that does not hold it. That is a dangling reference
    written as a fact, which is worse than a missing one: `_batch_digests_by_partition` refuses
    the same shape one plane down for the same reason.
    """
    if panel.manifest.factor_id != panel.definition.factor_id:
        raise FactorTransformError(
            f"this panel's manifest describes factor {panel.manifest.factor_id!r} and its "
            f"definition is {panel.definition.factor_id!r}; the two are produced together by "
            "compute_factor and a panel where they disagree cannot be transformed"
        )
    manifest_id = panel.manifest.manifest_id
    factor_id = panel.definition.factor_id
    as_of = panel.as_of
    stray = sorted(
        {
            item.subject
            for item in panel.observations
            if item.manifest_id != manifest_id or item.factor_id != factor_id or item.as_of != as_of
        }
    )
    if stray:
        raise FactorTransformError(
            f"{stray[:5]}{'...' if len(stray) > 5 else ''} carry a build, a factor or an as_of "
            f"that is not this panel's ({manifest_id}, {factor_id}, {as_of.isoformat()}); every "
            "processed row points at its source with (source_manifest_id, subject, as_of), and a "
            "row from another build would make that pointer name a build that does not hold it"
        )


def _refuse_source_codes_the_policy_rejects(
    observations: Sequence[FactorObservation], spec: FactorTransformSpec
) -> None:
    """Raise when the cross section carries a coverage code the policy declared unacceptable.

    `REFUSAL_ACTION`'s branch. The message names the code, the count and one example, because
    "some observation somewhere was undefined" is not actionable and the census this refusal
    replaces would have been.
    """
    offending = {
        item.coverage: [other.subject for other in observations if other.coverage == item.coverage]
        for item in observations
        if item.coverage != "computed"
        and spec.missing_values.action_for(item.coverage) == REFUSAL_ACTION
    }
    if not offending:
        return
    detail = "; ".join(
        f"{code}: {len(subjects)} security(ies), e.g. {sorted(subjects)[0]}"
        for code, subjects in sorted(offending.items())
    )
    raise FactorTransformError(
        f"{spec.qualified_key} declares {REFUSAL_ACTION!r} for coverage code(s) this cross "
        f"section carries -- {detail}. That is the action for a caller who would rather a build "
        "fail than silently carry a hole: an undefined_value census that grew overnight is a "
        "broken factor, and a policy that imputed it would report the same numbers it reported "
        "yesterday. Change the action, or fix the input the code is about"
    )


def _refuse_a_scale_estimator_that_collapsed(
    spec: FactorTransformSpec, *, ordered: Sequence[float], lower: float, upper: float
) -> None:
    """Refuse bounds that clip a cross section with dispersion down to a single point.

    The one run-time refusal about the *data* rather than about the spec, and its predicate is
    exact: bounds are equal **and** the raw participants were not.

    Both halves are load-bearing and the asymmetry is the whole design. `mad` on a cross section
    where more than half the names share one value estimates `MAD = 0`, so the interval is
    `[median, median]` and **every** participant is clipped to the median -- which is not
    winsorization by any reading of the word, and which would then flow into `zscore` as a zero
    deviation or into `none` as a cross section where the 40% of names carrying all the
    information have been flattened onto the 60% that carry none. Measured on
    `[1.0, 1.0, 1.0, 1.0, 9.0]` with `mad_scale=3`, and on the same values with
    `lower_quantile=0.25, upper_quantile=0.75`: both estimators collapse.

    The second half is what keeps this from firing on data that was *already* constant. A cross
    section of five identical values collapses any estimator and nothing was destroyed by doing
    so; that is a fact about the market at that `as_of`, and `degenerate_cross_section` is the
    code for it. A refusal there would make an unrelated knob -- which winsorization was declared
    -- decide whether an all-tied cross section is an answer or an error.

    A `FactorTransformError` rather than a coverage code because the alternative outcomes are
    both silent: under `zscore` the collapse arrives downstream as `degenerate_cross_section`,
    which reads as "the market had no dispersion today" and is false, and under `none` it arrives
    as a cross section of identical numbers with no code at all.
    """
    if lower != upper or ordered[0] == ordered[-1]:
        return
    raise FactorTransformError(
        f"{spec.qualified_key}'s {spec.winsorization.method} winsorization put both bounds on "
        f"{lower!r} for a cross section spanning {ordered[0]!r} to {ordered[-1]!r}, so it would "
        f"clip all {len(ordered)} participants to one point. That is not pulling in the tails: a "
        "median absolute deviation of zero means more than half the cross section shares one "
        "value, and a quantile interval of zero width means the same thing between the declared "
        "quantiles. Widen the interval, use a quantile rule instead of a mad one, or declare "
        "winsorization 'none' -- and see whether the factor is producing a value for most of the "
        "market at all"
    )


# --- writing the processed plane --------------------------------------------------------------


def _refuse_a_processed_panel_that_does_not_own_its_rows(panel: ProcessedFactorPanel) -> None:
    """Refuse a processed panel whose four parts do not describe **one** application.

    `_refuse_a_source_panel_that_does_not_own_its_observations`' mirror on the output side, and
    it exists for that function's reason rather than for a new one: `ProcessedFactorPanel` is a
    public frozen dataclass anybody can construct, `apply_factor_transform` never produces an
    inconsistent one, and the write boundary is where a hand-assembled one becomes a column in a
    Parquet file. The input side already had this guard; without this the output side did not.

    **What a missing check actually stores.** The two batch builders below read one row's worth
    of facts off three different fields. `transform_manifest_batch` takes its ten head columns
    off `manifest` and its nine policy columns off `spec`; `processed_observation_batch` takes
    `transform_key`/`transform_version` off `spec` and every other column off the rows; both take
    the dataset name off `definition`. So `dataclasses.replace(result, spec=other)` is accepted
    by every other guard and stores a manifest row whose `transform_id` names one transform and
    whose `standardization_method` names another -- which falsifies
    `TRANSFORM_MANIFEST_DATA_COLUMNS`' claim that the stored policy is *a projection of*
    `transform_id`, and leaves a stored `value` column that reads as a z-score and is a centred
    rank. `_transform_manifest_from_row`'s identity self-check cannot see it: the ten head
    columns it reassembles are internally consistent, and the policy columns are not among them.

    Row level as well as head level, and for `_refuse_a_source_panel_that_does_not_own_its_
    observations`' exact argument one plane up: a row carrying another build's
    `transform_manifest_id` is a pointer, written as a fact, at a manifest this partition does
    not hold -- and `load_processed_factor_observations` filters on the row's own `transform_id`,
    so a row whose `transform_id` is not the spec's is a row the transform that wrote it cannot
    read back.

    **Every identity is read once, above the row loop**, which is not style: `transform_id` and
    `transform_manifest_id` are pydantic `computed_field`s and are not cached, so reading one
    inside the comprehension re-hashes the model per security. This function was first written
    with `spec.transform_id` in the predicate and measured at **24.4 ms** on a 5,534-name cross
    section -- 27,675 `stable_model_id` calls for 5,534 rows -- against **0.19 ms** hoisted. That
    is ADR-0003's recorded defect (a `computed_field` re-hashing a build manifest inside a
    per-security loop) arriving a second time, inside the fix for something else, which is the
    shape this repository's own lessons say to expect. Hoisted, both batch builders together
    measure 14.7 ms at that scale against the 14.9 ms this module's docstring recorded for them
    before the guard existed, so the check is free at the size it has to run at.
    """
    spec = panel.spec
    manifest = panel.manifest
    definition = panel.definition
    transform_id = spec.transform_id
    manifest_id = manifest.transform_manifest_id
    source_factor_id = manifest.source_factor_id
    source_manifest_id = manifest.source_manifest_id
    as_of = manifest.as_of
    mismatched = [
        f"{column} is {stored!r} on the manifest and {declared!r} on the {origin}"
        for column, stored, declared, origin in (
            ("transform_id", manifest.transform_id, transform_id, "spec"),
            ("transform_key", manifest.transform_key, spec.key, "spec"),
            ("transform_version", str(manifest.transform_version), str(spec.version), "spec"),
            ("source_factor_id", source_factor_id, definition.factor_id, "definition"),
            ("source_factor_key", manifest.source_factor_key, definition.key, "definition"),
            (
                "source_factor_version",
                str(manifest.source_factor_version),
                str(definition.version),
                "definition",
            ),
        )
        if stored != declared
    ]
    if mismatched:
        raise FactorEngineError(
            f"this processed panel's manifest does not describe the transform and factor it "
            f"carries ({'; '.join(mismatched)}); the stored identity columns come off the "
            "manifest and the stored policy columns come off the spec, so a panel where the two "
            "are not one application files one transform's numbers under another's transform_id "
            "with a policy that never produced them -- and the head columns reassemble "
            "consistently, so no reader could tell. Apply the transform rather than assembling "
            "its result"
        )
    stray = sorted(
        {
            row.subject
            for row in panel.observations
            if row.transform_id != transform_id
            or row.transform_manifest_id != manifest_id
            or row.source_factor_id != source_factor_id
            or row.source_manifest_id != source_manifest_id
            or row.as_of != as_of
        }
    )
    if stray:
        raise FactorEngineError(
            f"{stray[:5]}{'...' if len(stray) > 5 else ''} carry a transform, a build or an "
            f"as_of that is not this panel's ({transform_id}, {manifest_id}, "
            f"{source_manifest_id}, {as_of.isoformat()}); every processed row is filed under the "
            "manifest row this same call writes, and a row naming another one is a pointer at a "
            "build this partition does not hold"
        )


def processed_observation_batch(panel: ProcessedFactorPanel) -> ColumnarPanelBatch:
    """One transform's processed rows as a columnar batch, ready for the store.

    Every clock on every row is the build's `as_of`, `factor_observation_batch`'s argument
    unchanged: a processed observation is a statement made *at* `as_of` out of information
    knowable *at* `as_of`, it became knowable at that instant, and it has no revision. The wall
    clock is the batch's `fetched_at`.

    **Every row is re-validated here**, which is the second call site
    `domain/factor_transform.py::validate_processed_factor_observation` exists for: a
    `__post_init__` is a method, a frozen dataclass with `slots=True` is still subclassable, and
    the write boundary is the last place a row that skipped the constructor's rules can be
    stopped before it is a column in a Parquet file. The rules that matter most at this boundary
    are the two provenance ones -- a `processed` row must come from a `computed` source and an
    `imputed` row must not -- because a violation of either stores a number this repository
    invented under the code that says a security produced it.

    **The panel is checked as a whole first**, by
    `_refuse_a_processed_panel_that_does_not_own_its_rows`: `transform_key` and
    `transform_version` below come off `panel.spec` while every row's `transform_id` comes off
    the row, so a panel whose spec is not the one that produced its rows would write two
    transforms' names into one row.
    """
    _refuse_a_processed_panel_that_does_not_own_its_rows(panel)
    observations = panel.observations
    for observation in observations:
        validate_processed_factor_observation(observation)
    instants = tuple(observation.as_of for observation in observations)
    columns: dict[str, list[object]] = {
        "transform_id": [observation.transform_id for observation in observations],
        "transform_key": [panel.spec.key] * len(observations),
        "transform_version": [panel.spec.version] * len(observations),
        "value": [observation.value for observation in observations],
        "coverage": [observation.coverage for observation in observations],
        "transform_manifest_id": [item.transform_manifest_id for item in observations],
        "source_factor_id": [observation.source_factor_id for observation in observations],
        "source_manifest_id": [observation.source_manifest_id for observation in observations],
        "source_coverage": [observation.source_coverage for observation in observations],
    }
    return ColumnarPanelBatch(
        provider_id=FACTOR_PROVIDER_ID,
        dataset=processed_factor_dataset(panel.definition),
        kind=FACTOR_PROCESSED_KIND,
        as_of=panel.as_of,
        fetched_at=panel.built_at,
        status="success",
        subjects=tuple(observation.subject for observation in observations),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=instants,
            revision_time=instants,
        ),
        columns=tuple(
            PanelColumn(name, _PROCESSED_COLUMN_KINDS[name], tuple(values))
            for name, values in columns.items()
        ),
        source_uri=None,
    )


def transform_manifest_batch(panel: ProcessedFactorPanel) -> ColumnarPanelBatch:
    """One transform build as a single row, keyed by `transform_manifest_id`.

    One row rather than `factor_manifest_batch`'s one-per-input-partition, because a transform
    has exactly one input: the source build. The `subject` is the build, `trade_cal`'s precedent
    -- `subject` is the entity the row is about -- and it buys the same write guard, since
    `PartitionCoverage.subjects` is then the set of transform builds a partition holds and
    `_refuse_to_drop_a_stored_build` can see an overwrite that would destroy one without reading
    a single row.

    The census and the statistics are here for `factor_manifest_batch`'s reason: a build that
    processed nobody is otherwise indistinguishable in storage from one that standardized the
    whole market, and the objects that could say so speak only to a caller that thinks to ask.

    **The ten head columns come off `manifest` and the nine policy columns come off `spec`**, so
    `_refuse_a_processed_panel_that_does_not_own_its_rows` runs first: without it this function
    is the one that writes a row whose `transform_id` and whose `standardization_method` describe
    two different transforms, and the identity check on the way back cannot see it.
    """
    _refuse_a_processed_panel_that_does_not_own_its_rows(panel)
    manifest = panel.manifest
    spec = panel.spec
    statistics = panel.statistics
    census = panel.coverage_census()
    columns: dict[str, list[object]] = {
        "transform_id": [manifest.transform_id],
        "transform_key": [manifest.transform_key],
        "transform_version": [manifest.transform_version],
        "source_factor_id": [manifest.source_factor_id],
        "source_factor_key": [manifest.source_factor_key],
        "source_factor_version": [manifest.source_factor_version],
        "source_manifest_id": [manifest.source_manifest_id],
        "source_observation_digest": [manifest.source_observation_digest],
        "as_of_time": [manifest.as_of],
        "code_commit": [manifest.code_commit],
        "winsorization_method": [spec.winsorization.method],
        "winsorization_lower_quantile": [spec.winsorization.lower_quantile],
        "winsorization_upper_quantile": [spec.winsorization.upper_quantile],
        "winsorization_mad_scale": [spec.winsorization.mad_scale],
        "standardization_method": [spec.standardization],
        "min_cross_section": [spec.min_cross_section],
        **{
            f"{MISSING_VALUE_COLUMN_PREFIX}{code}": [spec.missing_values.action_for(code)]
            for code in MISSING_VALUE_COVERAGE_ORDER
        },
        **{
            f"{FACTOR_CENSUS_COLUMN_PREFIX}{code}": [census[code]]
            for code in PROCESSED_COVERAGE_ORDER
        },
        "participant_count": [statistics.participant_count],
        "winsorized_low_count": [statistics.winsorized_low_count],
        "winsorized_high_count": [statistics.winsorized_high_count],
        "imputed_count": [statistics.imputed_count],
        "lower_bound": [statistics.lower_bound],
        "upper_bound": [statistics.upper_bound],
        "location": [statistics.location],
        "scale": [statistics.scale],
    }
    return ColumnarPanelBatch(
        provider_id=FACTOR_PROVIDER_ID,
        dataset=factor_transform_manifest_dataset(panel.definition),
        kind=FACTOR_TRANSFORM_MANIFEST_KIND,
        as_of=manifest.as_of,
        fetched_at=panel.built_at,
        status="success",
        subjects=(manifest.transform_manifest_id,),
        timeline=TimelineColumns(
            event_time=(manifest.as_of,),
            available_time=(manifest.as_of,),
            ingested_time=(manifest.as_of,),
            revision_time=(manifest.as_of,),
        ),
        columns=tuple(
            PanelColumn(name, _TRANSFORM_MANIFEST_COLUMN_KINDS[name], tuple(values))
            for name, values in columns.items()
        ),
        source_uri=None,
    )


def write_processed_factor_panels(
    store: PanelStore,
    panels: Sequence[ProcessedFactorPanel],
    *,
    supersedes: Collection[str] = (),
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Write every processed panel and its manifest, merged into one partition per year.

    `write_factor_panels`' shape, and every one of its arguments applies unchanged: a partition
    is replaced whole and has no append, so everything belonging to one `(dataset, year)` has to
    reach the store in one call; `supersedes` names the `transform_manifest_id`s this call is
    deliberately replacing; a `supersedes` entry no partition holds is refused rather than
    ignored, because a typo would silently turn the drop guard off for the write it arrived with;
    and every guard runs before the first write, so a refusal changes nothing at all.

    **The unit of the drop guard is a transform build**, which is what makes it complete here.
    `_refuse_to_drop_a_stored_build` reads the target manifest partition's stored subjects off
    the catalog -- one row, no partition scan -- and a `transform_manifest_id` covers
    `source_observation_digest`, so a write carrying every stored build carries every stored
    security *and every stored transform of them* by construction. The processed observation
    partition is covered by that rather than by a guard of its own, for the reason the raw one is:
    a securities-level guard both refuses correct writes (a rebuild over a narrower cross section)
    and permits incorrect ones (a write that drops a whole `as_of` while keeping the names).

    **One partition holds every transform of one factor**, so the call that writes a year has to
    carry every `(transform, as_of)` pair belonging to it -- not just every `as_of` of one
    transform. That is the one way this differs from `write_factor_panels`' constraint, it
    follows from the dataset-name arithmetic in this module's docstring rather than from a
    choice, and it is *enforced* rather than documented: a second transform written on its own
    would drop the first's build and the guard refuses it.
    """
    if not panels:
        raise FactorEngineError(
            "write_processed_factor_panels needs at least one panel; an empty write would be a "
            "call that reports success and stores nothing"
        )
    _refuse_two_applications_of_one_transform_at_one_as_of(panels)
    planned = [
        (year, yearly)
        for batches in _processed_batches_by_dataset(panels)
        for year, yearly in split_panel_batch_by_year(
            merge_panel_batches(batches), date_timezone=date_timezone
        )
    ]
    superseded = set(supersedes)
    stored: list[tuple[ColumnarPanelBatch, int, frozenset[str]]] = []
    for year, yearly in planned:
        if yearly.kind != FACTOR_TRANSFORM_MANIFEST_KIND:
            continue
        existing = store.read_coverage(yearly.dataset, year)
        stored.append(
            (yearly, year, frozenset() if existing is None else frozenset(existing.subjects))
        )
    unmatched = sorted(superseded - {build for _, _, builds in stored for build in builds})
    if unmatched:
        raise FactorEngineError(
            f"supersedes names {unmatched}, which no partition this write touches holds; a "
            "transform_manifest_id that matches nothing is a typo, and letting it through would "
            "turn the drop guard off for the write it arrived with"
        )
    for yearly, year, builds in stored:
        _refuse_to_drop_a_stored_build(yearly, year, builds=builds, superseded=superseded)
    return tuple(
        write_panel_batch(store, yearly, year=year, date_timezone=date_timezone)
        for year, yearly in planned
    )


def _processed_batches_by_dataset(
    panels: Sequence[ProcessedFactorPanel],
) -> tuple[tuple[ColumnarPanelBatch, ...], ...]:
    """One group of same-dataset batches per factor and per kind, observations before manifests.

    `_batches_by_dataset`' shape. Grouped by dataset **name** rather than by definition, so the
    grouping key is the same string the store files the partition under -- and here that matters
    more than it does one plane down, because several *transforms* of one factor legitimately
    produce the same dataset name and have to merge into one partition rather than race for it.
    """
    grouped: dict[str, list[ColumnarPanelBatch]] = {}
    for build in (processed_observation_batch, transform_manifest_batch):
        for panel in panels:
            batch = build(panel)
            grouped.setdefault(batch.dataset, []).append(batch)
    return tuple(tuple(batches) for batches in grouped.values())


def _refuse_two_applications_of_one_transform_at_one_as_of(
    panels: Sequence[ProcessedFactorPanel],
) -> None:
    """Refuse a call that answers one `(transform, factor, as_of)` question twice.

    Keyed by the three rather than by `transform_manifest_id`, because two applications that
    differed only in their source build carry two identities and would still store two rows for
    every `(subject, as_of, transform_id)` -- which is a reader left to choose between them, the
    exact thing `_refuse_two_builds_of_one_factor_at_one_as_of` exists to prevent one plane down.

    **All three components are read off `panel.manifest`, and that is the fix for a defect this
    function had.** The transform half used to come from `panel.spec` -- a *declaration* -- while
    the rows this call stores carry the observations' own `transform_id` and are filed under
    `manifest.transform_manifest_id`. Two panels sharing a manifest and carrying two specs
    therefore keyed apart and both were written: measured, two manifest rows under one
    `transform_manifest_id` and sixteen processed rows for an eight-name cross section, which is
    exactly the reader-left-to-choose this function's own docstring says it prevents.
    `_refuse_a_processed_panel_that_does_not_own_its_rows` now refuses that panel outright at the
    batch builders, and this key is off the manifest so the two guards cannot disagree about what
    a duplicate is.
    """
    seen: dict[tuple[str, str, datetime], int] = {}
    for panel in panels:
        manifest = panel.manifest
        key = (manifest.transform_id, manifest.source_factor_id, manifest.as_of)
        seen[key] = seen.get(key, 0) + 1
    repeated = sorted(
        f"{transform_id} of {factor_id} at {as_of.isoformat()}"
        for (transform_id, factor_id, as_of), count in seen.items()
        if count > 1
    )
    if repeated:
        raise FactorEngineError(
            f"this write carries more than one application of {repeated}; a second answer to one "
            "cross-section question would store two rows for every (subject, as_of, transform) "
            "and leave a reader to choose between them. Supersede the earlier build instead"
        )


# --- reading the processed plane back ----------------------------------------------------------


def processed_factor_requirement(
    definition: FactorDefinition, *, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What a processed partition must satisfy before its values may be read back.

    The same three waivers `factor_observation_requirement` takes and for exactly the same
    reasons -- the dates here are the `as_of`s somebody chose to compute rather than the sessions
    an exchange was open, the subjects are the cross section the read is *for*, and a derived
    partition has no upstream to be stale against, so a bound invented here would refuse a sound
    historical backfill. `required_fields` is not waived: the nine stored columns plus the subject
    are exactly what `load_processed_factor_observations` decodes, and a partition missing one
    would fail as a binder error rather than as a readiness verdict.
    """
    return ReadinessRequirement(
        dataset=processed_factor_dataset(definition),
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=PROCESSED_OBSERVATION_PANEL_COLUMNS,
        max_staleness=None,
    )


def transform_manifest_requirement(
    definition: FactorDefinition, *, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What a transform-manifest partition must satisfy before its builds may be read back."""
    return ReadinessRequirement(
        dataset=factor_transform_manifest_dataset(definition),
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=TRANSFORM_MANIFEST_PANEL_COLUMNS,
        max_staleness=None,
    )


def load_processed_factor_observations(
    store: PanelStore,
    definition: FactorDefinition,
    spec: FactorTransformSpec,
    *,
    years: Sequence[int],
    as_of: datetime,
) -> tuple[ProcessedFactorObservation, ...]:
    """One transform's stored processed rows, filtered to what was knowable at `as_of`.

    Through `read_visible_at` rather than `read_if_ready` for `load_factor_observations`' reason:
    a processed row's `available_time` is the `as_of` it was computed at, so a year partition
    holding a year of daily cross sections has a `max_available_time` in December and
    `read_if_ready` would refuse it at every `as_of` inside the year -- including the ones whose
    own rows are sitting in it.

    **The transform is a filter here and the factor is the dataset**, which is the opposite of
    the arrangement one plane down and is forced by the dataset-name arithmetic (see this
    module's docstring). The cost is stated rather than hidden: reading one transform of a factor
    opens the rows of every transform of it and drops the others in Python, because
    `read_visible_at` projects columns and takes no predicate. The alternative -- returning every
    transform's rows in one tuple -- would hand a caller z-scores and centred ranks mixed
    together under one type, with the filter left as an exercise; `load_factor_transform_manifests`
    is how a caller discovers which transforms a partition holds in order to ask for one.

    **And the cost has a magnitude, because a cost stated without one is a cost nobody can plan
    around.** Measured on a 2,000-name cross section at one `as_of`, storing `N` transforms of one
    factor in the partition and then reading exactly one of them back (best of three warm reads):

    | transforms in the partition | stored rows | rows returned | read |
    |---|---|---|---|
    | 1 | 2,000 | 2,000 | 32.8 ms |
    | 2 | 4,000 | 2,000 | 51.4 ms |
    | 4 | 8,000 | 2,000 | 88.4 ms |
    | 8 | 16,000 | 2,000 | **160.0 ms** |

    Linear in the stored rows and **4.9x at eight transforms for the same answer**. That factor
    is the one `V2-P3-014` pays: a three-tier report holds raw, processed and neutralized at once,
    and a year is 244 `as_of`s, so the multiplier lands on every one of them rather than on a
    single read. The way out is not a predicate on this call -- `read_visible_at` has none -- but
    a partition axis, and the dataset-name budget in this module's docstring is why there is not
    one. `V2-P3-014` is where a report that needs several transforms should read the year once
    and group in Python instead of calling this function per transform.
    """
    requirement = processed_factor_requirement(definition, years=years, as_of=as_of)
    dataset = requirement.dataset
    found: list[ProcessedFactorObservation] = []
    for year in sorted(set(years)):
        outcome = store.read_visible_at(
            requirement,
            year=year,
            columns=(EVENT_TIME_COLUMN, *PROCESSED_OBSERVATION_PANEL_COLUMNS),
        )
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at "
                f"{as_of.isoformat()}: {[issue.code for issue in outcome.blocking_issues]}"
            )
        found.extend(
            row
            for row in (
                _processed_observation_from_row(cells, dataset=dataset) for cells in outcome.rows
            )
            if row.transform_id == spec.transform_id
        )
    return tuple(found)


def load_factor_transform_manifests(
    store: PanelStore,
    definition: FactorDefinition,
    *,
    years: Sequence[int],
    as_of: datetime,
) -> tuple[FactorTransformManifest, ...]:
    """Every transform build of one factor stored in `years` and knowable at `as_of`.

    The read `write_processed_factor_panels`' refusal points at, and the reason it can point
    anywhere: a partition is replaced whole and a stored build may not be dropped, so a caller
    who wants to add an `as_of` -- or a second transform -- to a year has to know what the year
    already holds. It also carries the discovery half `load_processed_factor_observations` needs,
    since a partition holds every transform of the factor and a caller has to name one.

    **The policy and statistic columns are stored and are deliberately not reassembled here.**
    They are not fields of `FactorTransformManifest` (see `TRANSFORM_MANIFEST_DATA_COLUMNS`), so
    putting them back on one would either not fit or would move the `transform_manifest_id` of
    the manifest this function returns -- and the whole point of this read is that a reassembled
    build reproduces the identity it was stored under. They are columns in the partition for a
    reader that wants them; `FACTOR_TRANSFORMS.by_id` is how a caller turns a `transform_id` back
    into the policy this build declares.
    """
    requirement = transform_manifest_requirement(definition, years=years, as_of=as_of)
    dataset = requirement.dataset
    found: list[FactorTransformManifest] = []
    for year in sorted(set(years)):
        outcome = store.read_visible_at(
            requirement, year=year, columns=TRANSFORM_MANIFEST_PANEL_COLUMNS
        )
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at "
                f"{as_of.isoformat()}: {[issue.code for issue in outcome.blocking_issues]}"
            )
        found.extend(_transform_manifest_from_row(row, dataset=dataset) for row in outcome.rows)
    return tuple(found)


def _processed_observation_from_row(
    row: Sequence[object], *, dataset: str
) -> ProcessedFactorObservation:
    """Rebuild one processed row from `(event_time, *PROCESSED_OBSERVATION_PANEL_COLUMNS)`.

    Refuses the wrong width rather than unpacking positionally into whatever fits, and decodes
    both coverage columns *from* their vocabularies rather than casting -- `_coverage_code` for
    the source's five codes and `_processed_coverage_code` for this plane's five. A partition
    written by a build that knows a sixth of either would otherwise decode into a dataclass whose
    fields the type system believes are closed sets and are not.
    """
    expected = 1 + len(PROCESSED_OBSERVATION_PANEL_COLUMNS)
    if len(row) != expected:
        raise FactorEngineError(
            f"a {dataset} row has {len(row)} values, expected {expected} "
            f"({EVENT_TIME_COLUMN}, {', '.join(PROCESSED_OBSERVATION_PANEL_COLUMNS)})"
        )
    cells = dict(zip((EVENT_TIME_COLUMN, *PROCESSED_OBSERVATION_PANEL_COLUMNS), row, strict=True))
    as_of = cells[EVENT_TIME_COLUMN]
    if not isinstance(as_of, datetime):
        raise FactorEngineError(
            f"a {dataset} row carries {type(as_of).__name__} for "
            f"{EVENT_TIME_COLUMN}, not a datetime"
        )
    return ProcessedFactorObservation(
        subject=str(cells[SUBJECT_COLUMN_NAME]),
        as_of=as_of,
        value=_stored_processed_value(cells["value"], dataset=dataset),
        coverage=_processed_coverage_code(cells["coverage"], dataset=dataset),
        transform_id=str(cells["transform_id"]),
        transform_manifest_id=str(cells["transform_manifest_id"]),
        source_factor_id=str(cells["source_factor_id"]),
        source_manifest_id=str(cells["source_manifest_id"]),
        source_coverage=_coverage_code(cells["source_coverage"], dataset=dataset),
    )


def _stored_processed_value(value: object, *, dataset: str) -> float | None:
    """A stored processed `value` cell as a finite float or `None`, or a refusal.

    `_stored_value`'s rule for the processed column, and it is a separate function rather than a
    call to that one because the message has to name the right remedy: a non-finite *raw* value
    belongs under `undefined_value`, and this plane has no code that carries one at all -- an
    infinity here is a row no declared coverage code describes.
    """
    if value is None:
        return None
    parsed = float(str(value))
    if not math.isfinite(parsed):
        raise FactorEngineError(
            f"a {dataset} row carries value {value!r}, which is not a finite number; no declared "
            "processed coverage code carries one, and a `processed` row holding it poisons every "
            "mean, rank and regression built on the column"
        )
    return parsed


def _processed_coverage_code(value: object, *, dataset: str) -> ProcessedCoverage:
    """A stored processed `coverage` cell as one of the five declared codes, or a refusal.

    `_coverage_code`'s argument on this plane's vocabulary: matched against
    `PROCESSED_COVERAGE_ORDER` and returned *from* it rather than cast, so a partition written by
    a build that knows a sixth code is refused where the dataset can be named.
    """
    text = str(value)
    for code in PROCESSED_COVERAGE_ORDER:
        if code == text:
            return code
    raise FactorEngineError(
        f"a {dataset} row carries coverage {text!r}, which this build does not declare "
        f"({list(PROCESSED_COVERAGE_ORDER)}); it was written by a build that knows a code this "
        "one does not"
    )


def _transform_manifest_from_row(row: Sequence[object], *, dataset: str) -> FactorTransformManifest:
    """Rebuild one transform manifest from its row, and prove it is the one it was stored under.

    `_manifest_from_rows`' argument, without the fold: a transform build reads exactly one input
    -- the source factor build -- so it is one row rather than one per input partition.

    The reassembled `transform_manifest_id` is checked against the `manifest_id` the row was
    filed under, and that is not belt and braces: every field this reads is one the identity was
    computed from, so a decoder that dropped or mistyped one would hand back a build nobody ever
    ran, under the ID a caller then uses to name it in `supersedes`.
    """
    if len(row) != len(TRANSFORM_MANIFEST_PANEL_COLUMNS):
        raise FactorEngineError(
            f"a {dataset} row has {len(row)} values, expected "
            f"{len(TRANSFORM_MANIFEST_PANEL_COLUMNS)} "
            f"({', '.join(TRANSFORM_MANIFEST_PANEL_COLUMNS)})"
        )
    cells = dict(zip(TRANSFORM_MANIFEST_PANEL_COLUMNS, row, strict=True))
    as_of = cells["as_of_time"]
    if not isinstance(as_of, datetime):
        raise FactorEngineError(
            f"a {dataset} row carries {type(as_of).__name__} for as_of_time, not a datetime"
        )
    manifest = FactorTransformManifest(
        transform_id=str(cells["transform_id"]),
        transform_key=str(cells["transform_key"]),
        transform_version=int(str(cells["transform_version"])),
        source_factor_id=str(cells["source_factor_id"]),
        source_factor_key=str(cells["source_factor_key"]),
        source_factor_version=int(str(cells["source_factor_version"])),
        source_manifest_id=str(cells["source_manifest_id"]),
        source_observation_digest=str(cells["source_observation_digest"]),
        as_of=as_of,
        code_commit=str(cells["code_commit"]),
    )
    stored = str(cells[SUBJECT_COLUMN_NAME])
    if manifest.transform_manifest_id != stored:
        raise FactorEngineError(
            f"a {dataset} build stored under {stored!r} reassembles to "
            f"{manifest.transform_manifest_id!r}; the row and the identity it was filed under "
            "disagree, so this partition was written by a build whose manifest contract is not "
            "this one's"
        )
    return manifest
