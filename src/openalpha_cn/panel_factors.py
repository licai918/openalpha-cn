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
computes. The coverage vocabulary was not widened for it: a period shortfall or a period span
overrun is `insufficient_history`, distinguishable on the stored row by the period window rather
than by a sixth code.

Two rows of one `(subject, period)` at one announcement -- the shape carrying 81.7% of
`fina_indicator`'s real duplication -- **code that security `ambiguous_filing`** when they
disagree in the columns the factor reads and collapse when they agree, which is
`build_statement_history`'s own rule and is what makes this engine's answer
`ReportFiling.value_of`'s answer. `V2-P3-009` made the collapse and `_read_dataset` argues it;
refusing on multiplicity alone had refused 372 of `income`'s 633 duplicate keys that say exactly
the same thing twice. `V2-P3-018` made the remainder a per-security code instead of a build
refusal; see "Coverage is a code, never a bool" below.

## Coverage is a code, never a bool

`FactorCoverage` has six members and `domain/factor.py` argues each one. The short version:
"could not compute" is not one fact. A security that had not listed yet (`not_in_universe`)
should have no value and reporting a data fault for it would put a permanent false defect on
every historical cross section; a security that listed nine sessions ago
(`insufficient_history`) is a correct answer to a 120-session window; a filing the publisher
stated twice and disagreed with itself about (`ambiguous_filing`) is a fault nobody can fetch
their way out of; a null column (`input_missing`) is a fetch problem; a zero denominator
(`undefined_value`) is a definition problem. `V2-P3-005` has to exclude all five from a
correlation rather than treat them as zeros, and only a code set lets it.

`ambiguous_filing` is `V2-P3-018`'s member and it is the one that made the statement families
buildable at all. Before it, a single contradictory filing anywhere in a cross section refused
the whole build, at measured rates of 8.51% of `income`'s filings, 0.95% of `balancesheet`'s,
**17.11%** of `cashflow`'s and 11.80% of `fina_indicator`'s -- so none of `V2-P3-009`..`011`'s
six factors could be built over a real whole-market partition. Reusing one of the five that
existed would have been wrong on the meaning and the wrongness is directional rather than
aesthetic: `input_missing` tells a reader to fetch, and a re-fetch returns the same two rows.

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

**`V2-P3-012`'s family re-opened it a third time and is the first workload whose per-security
cost grows with the factor's own reach**, so "the last one did not need numpy" is not an argument
here either. `_compounded_session_return` multiplies one growth factor per session of the window,
so a 120-session momentum does 120 multiplications per security where a 5-session reversal does
five. Measured on 5,534 securities x 130 sessions (719,420 rows, both price columns), whole cross
section, one `as_of`: `momentum_120_sessions` **2.93 s** cold / 2.84 s warm, `reversal_5_sessions`
**2.50 s** / 2.48 s, and `write_panel_batch` for the same partition **507.74 s**. The difference
between the two factors -- 0.43 s for 636,410 extra multiplications, about 0.7 us each -- is the
whole of what a vectorised implementation could remove, and it is 0.08% of the write that follows
it. ADR-0003's 2026-08-12 `V2-P3-012` update carries the table and the bound (`V2-P3-013`'s
per-security regressions are a different shape again and are not covered by it).

Against the 2.24 s `compute_factor` that has to run first, the whole transform is **1.6%**, and
against the write path that follows it (56.7 s at the smallest of ADR-0003's five measurements)
it is 0.06%. A numpy implementation of the same four steps could at best remove a number that is
already two orders of magnitude below the step before it and three below the step after it, in
exchange for the two runtime dependencies and every consequence ADR-0003 lists. `V2-P3-004`'s
cross-sectional regression is still the issue that should re-open it, and it is still the first
one with a matrix in it.

## `V2-P3-012`'s family ships here, and the fifth deliverable is **not** a fifth definition

The momentum-and-reversal family is four `FactorDefinition`s -- `MOMENTUM_20_SESSIONS`,
`MOMENTUM_60_SESSIONS`, `MOMENTUM_120_SESSIONS` and `REVERSAL_5_SESSIONS` -- and the issue's fifth
item, *industry-relative momentum*, is a **composition of machinery that already exists** rather
than a definition beside them. The reason is structural and is checkable in three ways rather than
argued:

- **An evaluator has no cross section, by type.** `compute_factor` calls one `FactorEvaluator` per
  security with a `FactorWindow` carrying that security's own series and nothing else. An
  industry-relative value is `momentum` minus the mean momentum of the security's industry peers
  *at the same `as_of`*, which is a statistic over other securities. There is no argument through
  which an evaluator could see one, so this is not "the engine cannot reach the industry table"; it
  is that the engine cannot reach the cross section at all. That is deliberate --
  `apply_factor_transform` and `apply_factor_neutralization` are where a cross-sectional statistic
  is allowed to live, and both take their cross section as a **value** so the look-ahead surface is
  closed by arithmetic.
- **An industry code could not be a `required_field` even if it helped.** `_numeric` refuses a
  non-numeric cell outright, and `industry_classification`'s level columns are strings. So
  `FactorField(dataset="index_member_all", column="l1_code")` constructs -- the field validator is
  syntactic -- and `compute_factor` then raises rather than computing.
  `tests/integration/panel/test_factor_momentum_reversal.py::
  test_an_industry_code_is_declarable_as_a_factor_input_and_refused_at_the_read` drives exactly
  that, so the claim is a measurement.
- **The composition already produces the quantity.** `V2-P3-004`'s `INDUSTRY_AND_SIZE` regresses a
  processed cross section on a complete set of SW2021 L1 dummies plus `log(total_mv)` and stores
  the residual, which by Frisch-Waugh-Lovell is `(y - mean_y_g) - beta * (x - mean_x_g)`. The first
  term **is** industry-relative momentum. So `V2-P3-012` delivers
  `compute_factor(MOMENTUM_60_SESSIONS) -> apply_factor_transform(CROSS_SECTION_STANDARD) ->
  apply_factor_neutralization(INDUSTRY_AND_SIZE)`, and
  `tests/integration/panel/test_factor_momentum_reversal.py::
  test_the_industry_relative_momentum_is_the_neutralised_residual_of_a_momentum_factor` runs the
  three tiers over one corpus and holds every residual to a hand-computed number.

**The difference between that residual and a pure industry demeaning is stated rather than
elided.** It is exactly `beta * (x - mean_x_g)`: the shipped neutralisation removes industry *and*
size, and no declarable `FactorNeutralizationSpec` removes industry alone (`market_cap_scale` has
two members and neither is "none"). The two are different numbers on a real cross section, which
is asserted with a floor rather than asserted to be small --
`test_the_size_term_is_what_separates_the_residual_from_a_pure_industry_demeaning` measures the
gap and refuses a fixture on which it vanishes.

**And the composition is not wired into a schedule or a read path, deliberately.** Roadmap
section 11 records that a neutralised row's four clocks are all the build `as_of`, and that
`as_of` must be at or after the `daily_basic` year partition's `max_available_time` -- so residuals
for any day of year Y are invisible to any read before Y ends. `V2-P4-026` is the fix and
`V2-P4-013`'s hard prerequisite, and the roadmap's instruction to `V2-P3-005` and
`V2-P3-009`..`013` is to stack no further code on the assumption that residuals are visible
day by day. Nothing here does: the composition is three in-memory calls, none of which reads a
stored residual back.

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

## The value family (`V2-P3-009` and `V2-P3-017`), and the first factors on two axes at once

Four definitions ship for it -- `earnings_yield_ttm`, `book_to_price`, `sales_yield_ttm` and
`deducted_earnings_yield_ttm` -- and five judgements are shared by all of them and stated here
rather than four times over. The first three arrived with `V2-P3-009`; the fourth is EPcut and
could not arrive with them, because its numerator was in none of the four stored projections.
`V2-P3-017` put it there and `DEDUCTED_NET_PROFIT_COLUMN` carries what that took.

**A value factor is a filing over a price, so it is on both axes, and the engine already
supported that before any shipped factor used it.** `FactorDefinition` requires each reach to be
declared exactly when `required_fields` puts the factor on that axis, `FactorWindow` carries a
`sessions` tuple and a `periods` tuple with `series()` aligned to whichever its dataset is on,
`FactorObservation` carries a window pair per axis, and `_classify` forms, bounds and completes
both independently. All of that was built by the change that added the report-period axis and was
exercised only by definitions the tests declared;
`tests/integration/panel/test_factor_report_periods.py::
test_a_factor_on_both_axes_gets_one_window_per_axis` is the measurement, and these three are what
that machinery was built for. So the time-point mismatch a value factor has -- a daily
denominator and a quarterly numerator -- is not resolved by aligning one to the other. Each side
is read on its own index and the evaluator divides `[-1]` by `[-1]`, which is what the two
tuples exist for.

**The denominator is `daily_basic.total_mv`, carried into yuan.** Not `circ_mv` (a filing
describes the whole company), and not the published `pe`, `pe_ttm`, `pb`, `ps` and `ps_ttm`
inverted, which is the choice with the sharper argument. All five of those are in
`DAILY_BASIC_NULLABLE_COLUMNS` and their null rate is not incidental: 1,102 null `pe` and 1,214
null `pe_ttm` of 5,338 rows on 2024-06-28, because a published multiple has no value for a
loss-maker. A factor built by inverting one would answer `input_missing` for a fifth of the
market -- precisely the fifth a signed yield scores correctly -- and it would be scoring the
upstream's own trailing-window arithmetic rather than the filings, which is the reason
`_compounded_session_return` refuses `pct_chg` and calls it the third witness.
`CNY_PER_MARKET_CAP_UNIT` is measured on stored rows, and the unit is load-bearing in a way
nothing downstream can catch: a
rank IC and a z-score are scale-free, so a denominator wrong by 10,000 reaches every report that
quotes a number and no test that only ranks.

**The flow numerators are trailing twelve months, not the latest cumulative.** A-share statements
accumulate within the calendar fiscal year, so the latest cumulative figure is three, six, nine or
twelve months of profit depending on the period -- and a cross section read at one `as_of` mixes
those the moment two issuers are on different filing schedules. `TRAILING_TWELVE_MONTH_PERIODS`
carries the identity and what makes it legitimate: `max_window_periods == lookback_periods` is the
contract's statement that no filing is missing inside the window, measured against the
fiscal-quarter grid rather than against the periods this build happened to read. The cost is a
coverage difference *inside the family* -- five contiguous filings for EP and SP, one for BP --
and it is visible in the census rather than hidden.

**A negative numerator is `computed` and negative.** A loss-making issuer has a negative earnings
yield and an insolvent one a negative book-to-price; the arithmetic has an answer and
`undefined_value` is documented for the case where it does not. This is the reason the family is
stated as yields rather than as multiples: `E/P` is monotone through zero and `P/E` is not.

**The columns are chosen against the measured concentration of the refusals.**
`domain/financial_statements.py` records that the refusals are not spread but pooled in four
columns -- `ebit` is 258 of `income`'s 288, `total_share` 34 of `balancesheet`'s 43,
`free_cashflow` all 450 of `cashflow`'s and `fcff` 441 of `fina_indicator`'s 507 -- and this
family reads **none** of them, nor `bps`, whose two versions of one `603049.SH` filing differ by a
factor of ten. That choice is now backed by the three columns' **own** measured rates rather than
by arithmetic on a dataset total, because `V2-P3-009`'s review measured them over two disjoint
91-security samples:

| column | sample A | sample B | combined |
|---|---:|---:|---:|
| `income.total_revenue` | 6 / 5,210 | 7 / 5,357 | 13 / 10,567 = **0.123%** |
| `income.n_income_attr_p` | 7 / 5,210 | 13 / 5,357 | 20 / 10,567 = **0.189%** |
| `balancesheet.total_hldr_eqy_exc_min_int` | 13 / 5,120 | 8 / 5,249 | 21 / 10,369 = **0.203%** |

**The bound those numbers replace was itself a sample property, which is the point.** Until this
review the budgets here were derived: "the nine non-`ebit` `income` columns share at most 30
refused reads over 3,201 filings, the six non-`total_share` `balancesheet` columns at most 9 over
3,170" -- 0.94% and 0.28% spread over nine and six columns. Both target columns come in under
those bounds, but the *intermediate* quantity does not: on the review's two samples the
non-`ebit` `income` residue is 55 / 5,210 and 92 / 5,357 (1.72%) and the non-`total_share`
`balancesheet` residue 60 / 5,120 (1.17%) and 32 / 5,249 (0.61%), two to four times the recorded
figures. `domain/financial_statements.py` says it in its own voice -- "how little is a property
of the sample and not of the dataset", and "the `0` is the one to distrust" -- and a derived
bound whose middle term moves by 4x is a delivery proof hanging on a free parameter. What it does
**not** buy either way is immunity, and the counterexample is in this repository's own fixtures:
`002538.SZ`'s 2022 annual carries three versions of `total_hldr_eqy_exc_min_int` spanning 3.8%.

**A non-finite stored cell now refuses the build, on both axes, and only one axis has a
measurement behind it.** `_numeric` used to pass `nan` and the infinities through, so an
evaluator reached one and returned it and `FactorEvaluator`'s rule made it `undefined_value` for
that one security. The period axis forces the tightening -- the collapse is built on `==` and
`nan != nan`, so two byte-identical rows would be reported as a disagreement the publisher never
stated -- but the session axis has no collapse and therefore no such forcing: there the argument
is that a non-finite cell is poison for every window statistic, which is a judgement rather than
a measurement. The cost of being wrong about it is that one security's `nan` refuses the whole
cross section where it used to cost that security alone. Nothing this repository writes can
produce one (`providers/tushare.py::_finite_number` refuses it at the boundary), so no existing
test separates the two behaviours and the whole tree stayed green through the change -- which is
exactly why it is recorded here rather than only in `_numeric`'s docstring. A fail-closed
tightening that no suite can see is the kind that has to be written down.

**The one thing `V2-P3-009` did not deliver was EPcut, and `V2-P3-017` delivered it by widening
a projection rather than by finding a column.** For two issues `earnings_yield_ttm` occupied that
slot on `RETURN_VOL_60`'s terms -- EP is what EPcut reduces to when the non-recurring term cannot
be subtracted -- because none of the four stored projections carried a deducted-profit column,
which was a boundary of the *projection* and not of the upstream. `fina_indicator` serves
`profit_dedt` itself; `income` serves nothing of the family at all and drops the name silently
when asked, so there was one place to put it and `V2-P3-017` put it there. What that cost, in
order: a twelfth column on `fina_indicator`'s projection, every stored partition of that dataset
re-read as `field_missing` until it is re-fetched, the contract test pinned to the field list,
and this factor's own ambiguity -- 1.075% and 0.769% of filings on two disjoint samples against
EP's measured 0.189% and 0.459%, because the number is published on the one endpoint with no
version column. `DEDUCTED_NET_PROFIT_COLUMN` carries the measurements and
`domain/financial_statements.py` carries why the eleven columns already there were not re-priced
by it.

**The second thing it did not deliver was a build over a real whole-market partition, and
`V2-P3-018` delivered it.** `_read_dataset` collapses the duplicate rows that agree, which is
most of them; the ones that genuinely disagree (261 of `income`'s 3,201 filings, and 8.2% / 8.7%
of the review's two samples) used to refuse the whole build rather than one security, and now
carry `ambiguous_filing` on the securities whose window reaches them. That was filed as
`V2-P3-018` rather than done here because `FACTOR_CENSUS_COLUMNS` is derived from the coverage
vocabulary and is part of `FACTOR_MANIFEST_DATA_COLUMNS`, so a sixth code changes the stored
manifest partition's schema -- a change with a migration attached, which is what that issue
carried out. `V2-P3-009` expected `V2-P3-010`'s ROE to arrive on `fina_indicator.roe`, off the
dataset whose ambiguous-filing rate is 13.7%; it did not, and "The quality family" below is the
argument for computing it instead. The wall was unmoved either way -- `accruals_ttm` reads three
statement datasets over five contiguous periods and `gross_margin_stability` reads eight -- which
is why it had to be taken down rather than routed around.

## The quality family (`V2-P3-010`), and the first factors that read no session at all

Four definitions ship for it -- `return_on_equity_ttm`, `return_on_capital_ttm`,
`gross_margin_stability` and `accruals_ttm` -- and five judgements are shared by all of them and
stated here rather than four times over.

**They are the first shipped factors on the report-period axis alone.** `V2-P3-009`'s three read
a filing over a price and are on both axes; these four read filings and nothing else, so
`lookback_sessions` is `None` and `FactorDefinition` requires it to be -- a session reach on a
factor with no session dataset is a number in the content address that no branch can consult. The
consequence a caller sees is that `compute_factor`'s `panel_sessions` is empty for them and every
history question is asked of `_period_span`'s fiscal-quarter grid. The window itself is the
**union** of the report periods of every statement dataset the factor reads, which `_points_held`
already did and no shipped factor had exercised across two statement endpoints: a security that
filed an income statement for a period and no balance sheet is `input_missing` rather than
silently short a term.

**Every ratio here is a flow over a stock, and the stock is the window's last period.** The
numerator is the same cumulative-to-TTM identity the value family argues for; the denominator is
one cell at `[-1]`. The textbook alternative -- an average of the window's two ends -- is
available (`window[0]` is the same quarter one year earlier by construction) and is not taken,
because `earnings_yield_ttm` and `book_to_price` already ship and already divide this same
trailing profit and this same closing equity by one market capitalisation. So `EP / BP` **is**
`return_on_equity_ttm`, exactly, and an averaged denominator would put two incompatible
statements of what a book value is inside one build. The bias that buys is disclosed with its
direction in `CAPITAL_TURNOVER_PERIODS`.

**A denominator gets the sign rule a numerator does not, and it is the same column.**
`book_to_price` reports an insolvent issuer's negative equity as `computed` and negative;
`return_on_equity_ttm` reports the same negative equity as `undefined_value`. That is not an
inconsistency -- a negative denominator turns a profit into a negative return and a loss into a
positive one, so the ordering the whole cross section is built on inverts for exactly the names in
the worst condition. `_capital_denominator` is the one guard and `_market_capitalisation` is its
precedent on the other axis.

**No factor of *this* family reads `fina_indicator`, and ROE is where that decision was made.**
The full argument is `RETURN_ON_EQUITY_TTM`'s; the decisive half is that a published ROE is a
*cumulative-period* return and no arithmetic converts it to a trailing one, because the
cumulative-to-TTM identity is an identity about **sums** and a ratio is not a sum. Measured on the
served rows rather than argued: `600519.SH`'s `roe` reads 10.5688 at 2024Q1, 19.2038 at H1, 26.833
at Q3 and 38.4283 at the annual -- one profit-accumulation curve, not four estimates of one
number. And the formula behind it cannot be checked from inside this projection: against the
closing-equity computation `n_income_attr_p / total_hldr_eqy_exc_min_int`, the published annual
`roe` differs by 2.0%-9.5% over `600519.SH`'s eight most recent annuals, 2.3%-11.7% over
`000001.SZ`'s and 1.4%-**36.7%** over `000002.SZ`'s, whose 2025 annual is a published `-55.4220`
against a computed `-75.7507`. `fina_indicator` carries **no equity column at all**, so a reader
holding only that endpoint has nothing to reconcile a return against.

`V2-P3-017` moved half of that last sentence and left the argument standing, which is worth
stating rather than quietly patching. That endpoint's projection now carries one profit column --
`profit_dedt`, in yuan -- so "neither a profit column nor an equity column" is no longer true of
it, and `deducted_earnings_yield_ttm` reads it. What the new column does **not** give ROE is a
reconciliation: it is the *deducted* profit and the published `roe` is not computed from it, and
there is still no equity to divide by. The distinction the ROE argument turns on is untouched in
the other direction too -- `profit_dedt` is a cumulative **sum** and the identity applies to it,
where `roe` is a rate and it does not; see `DEDUCTED_NET_PROFIT_COLUMN`, which measures both
curves on the same security.

**Two of the four are blind to financial issuers, by construction and measured.** A bank, insurer
or broker publishes no cost of sales and no current / non-current balance-sheet split, so
`oper_cost` and `total_cur_liab` are null and `_complete_series` answers `input_missing` rather
than inventing a number. Measured on the served rows over every stored period since 2015:
`total_cur_liab` is null on **68 of 68** of `000001.SZ`'s balance sheets, **67 of 67** of
`601318.SH`'s and **64 of 64** of `600030.SH`'s (their newest non-null periods are 2006-03-31,
2006-09-30 and 2006-09-30), and `oper_cost` on **59 of 59**, **57 of 57** and **56 of 56** of
their income rows -- while the two `comp_type=1` industrials probed beside them carry **0 of 62**
and **0 of 63** nulls in `total_cur_liab` and **0 of 60** apiece in `oper_cost`. So
`return_on_capital_ttm` and `gross_margin_stability` score the non-financial cross section and say
so, which is the right answer for a company type whose capital employed and gross margin are not
defined quantities.

### What `V2-P3-010` measured on the live endpoint, and what it replaces

`V2-P3-009` was delivered with per-column budgets derived from another sample's totals and its
review replaced them with direct measurements. This family did the same for its own eight columns
rather than inheriting either, because two of them (`total_assets`, `n_cashflow_act`) carry a
recorded refusal count of exactly `0` and `domain/financial_statements.py` says in its own voice
that "the `0` is the one to distrust". **Two disjoint samples** of 93 and 92 securities -- every
60th code of `stock_basic`'s 5,543 listed ones, offset 0 and offset 30 -- were paged to exhaustion
on all four endpoints on 2026-08-13, and every `(security, period, announcement)` key was
collapsed on the **stored projection**, which is the rule `_read_dataset` applies:

| dataset | filings | ambiguous | rate | recorded |
|---|---:|---:|---:|---:|
| `income` | 10,595 | 902 | 8.51% | 8.15% |
| `balancesheet` | 10,393 | 99 | 0.95% | 1.29% |
| `cashflow` | 9,602 | 1,643 | **17.11%** | 15.80% |
| `fina_indicator` | 10,865 | 1,282 | 11.80% | 13.70% |

| column | sample A | sample B | combined | recorded |
|---|---:|---:|---:|---|
| `income.n_income_attr_p` | 7 / 5,372 | 17 / 5,223 | 24 / 10,595 = **0.227%** | 0.189% |
| `income.n_income` | 7 | 17 | 24 / 10,595 = **0.227%** | -- |
| `income.total_revenue` | 11 | 9 | 20 / 10,595 = 0.189% | 0.123% |
| `income.oper_cost` | 14 | 11 | 25 / 10,595 = **0.236%** | -- |
| `income.ebit` (**not read**) | 464 | 424 | 888 / 10,595 = 8.38% | 258 of 288 |
| `balancesheet.total_hldr_eqy_exc_min_int` | 15 | 20 | 35 / 10,393 = **0.337%** | 0.203% |
| `balancesheet.total_assets` | 16 | 19 | 35 / 10,393 = **0.337%** | **0**, then 18 |
| `balancesheet.total_cur_liab` | 4 | 10 | 14 / 10,393 = 0.135% | -- |
| `cashflow.n_cashflow_act` | 1 | 4 | 5 / 9,602 = **0.052%** | **0** |
| `cashflow.free_cashflow` (**not read**) | 835 | 808 | 1,643 / 9,602 = 17.11% | 450 of 450 |
| `fina_indicator.roe` (**not read**) | 16 | 13 | 29 / 10,865 = 0.267% | 5, then 33 |

**Four things this table says that a docstring would not have.** Both recorded zeros are
falsified: `total_assets` at 35, having already moved from `0` to 18 once, and `n_cashflow_act` at
5 for the first time. Neither move is large in absolute terms and neither is zero, which is the
distinction `domain/financial_statements.py` asks for. `cashflow` is worse than
recorded rather than better, which matters because `accruals_ttm` reads it and because
`free_cashflow` still accounts for **every** ambiguous filing that endpoint has. Every one of
`V2-P3-009`'s three measured columns came back *higher* on these two samples than on its own two,
so the review's figures are a sample property in the same way the ones they replaced were. And the
column this family declines, `roe`, refuses **less often** than the pair replacing it: see
`RETURN_ON_EQUITY_TTM`'s fourth argument, which was written the other way round and is kept in its
falsified form beside the measurement.

**The one identity this family rests on that the projection can check itself**: `n_income =
total_profit - income_tax`, which is what makes "the term `RETURN_ON_CAPITAL_TTM`'s numerator is
missing is exactly the after-tax interest" a measurement rather than a reading of the statement's
shape. Over the six securities probed it holds on every period from 2007 onward -- 446 rows, worst
relative gap **8.9e-7**, and five of the six at machine precision -- and fails before it, on
`000001.SZ`'s 2005H1 (3.1e-1) and `000002.SZ`'s 1996H1 (5.9e-2), which is the pre-2007 CAS under
which 净利润 was already net of minority interest. The assertion is therefore made on modern rows
and states its own boundary.

## The growth family (`V2-P3-011`), and the first factors that read a filing and nothing else

Three definitions ship for it -- `revenue_yoy`, `net_profit_yoy` and `revenue_yoy_acceleration` --
and six judgements are shared by all of them and stated here rather than three times over.

**A year-on-year is a ratio of a cumulative figure to itself four quarters back, and no trailing
twelve months is read anywhere in this family.** A-share statements accumulate within the calendar
fiscal year, so `cumulative[P]` and `cumulative[P - 4 quarters]` cover the **same span of the same
fiscal year** -- nine months against nine months at Q3, twelve against twelve at the annual. The
seasonality `V2-P3-009`'s trailing sum exists to remove is already cancelled by the two sides
covering the same months, so the value family's three-term identity has nothing to add here and is
not called. What makes the offset twelve months is the same thing that made that identity legal:
`max_window_periods == lookback_periods`, measured on the fiscal-quarter grid by `_period_span`.

**That is not a simplification but a correctness requirement, and the reason is an alignment
hazard the parallel `V2-P3-010` measured.** `_trailing_twelve_months` searches
`window.periods[:-1]` for a fiscal year end, and how many that slice holds is a function of the
reach **and of where the window ends**: `[:-1]` is `N - 1` consecutive quarters, and `K`
consecutive quarters hold `K // 4` or `K // 4 + 1` year ends depending on the alignment. At
`N = 5` it is four and therefore exactly one in all four alignments, which is what `V2-P3-009`
rests on. At `N = 8` it is seven, which is **one or two** -- so a reader who reuses the helper at
that reach gets `None` for three alignments and, for the fourth, a confident wrong number built
from the previous year's December. At `N = 9` it is eight, exactly two years of quarters, so the
helper answers `None` in all four -- but that is nine being odd about a four-cycle rather than a
guard anybody wrote. This family does not rely on it: `_year_on_year` reads two cells at a fixed
index offset and searches for nothing, which is why it has no alignment behaviour at all.
`tests/unit/test_factor_growth_family.py::
test_a_nine_period_window_holds_two_year_ends_in_every_alignment`
drives all four and asserts the **count**, not only the `None`.

**A year-earlier base that is not strictly positive is `undefined_value`, and this is the family's
hardest judgement.** `-100` last year against `+50` this year has the arithmetic answer `-1.5`,
and that answer is monotonically backwards: the derivative of `num / base - 1` in `num` is
`1 / base`, so at a negative base the better this year's outcome the *lower* the factor value, and
a `higher_is_better` cross section ranks the completed turnaround below the deepening loss -- on
exactly the subset of the market a growth factor claims to find. `_market_capitalisation` refuses
a non-positive denominator in this module for the same stated reason. Dividing by `abs(base)`
instead is refused rather than adopted: it scores a swing from `-1` to `+1` at `+2.0`, the same as
a rise from 100 to 300, and nothing measured here says those two belong at one rank.

**The costs are measured on this repository's own live probe rather than estimated.** Two
**disjoint** 60-security stride samples of the 5,543 listed securities, every `income` filing
announced 2016-2026 (4,359 filings) resolved under this engine's own rules -- later announcement
wins, same-day rows collapse when the projected cells agree and refuse per column when they do not
-- and evaluated at two `as_of` days, 2024-06-30 and 2025-06-30, for 240 (security, `as_of`) pairs:

| what it decides | measured |
|---|---|
| the reach, `1` / `5` / `9` contiguous periods | 240 / 240, **230 / 240**, **220 / 240** |
| what `REVENUE_YOY_ACCELERATION` costs over `REVENUE_YOY` | 10 of the 230, **4.3%** |
| year-earlier `n_income_attr_p` not strictly positive | 49 / 230, **21.3%** |
| year-earlier `total_revenue` not strictly positive | 0 / 230, **0%** |
| *either* base non-positive, so an acceleration is undefined -- profit | 61 / 220, **27.7%** |
| the same on `total_revenue`, which is why the acceleration is on it | 0 / 220, **0%** |
| `n_income` and `n_income_attr_p` giving a **different** year-on-year | 139 / 181, **76.8%** |
| `total_revenue` and `revenue` giving a different year-on-year | 4 / 230, **1.7%** |
| an acceleration that came out equal to its own recent rate | 0 / 220 |

Three of those decide something no fixture can. The last two are the pair worth reading together:
a growth rate divides a constant scale out, so the fact that `n_income` is 3.169 times
`n_income_attr_p` on `600739.SH`'s 2024 annual settles nothing about the *rates* -- and it turns
out the two profit columns disagree about the rate for three securities in four while the two
revenue columns disagree for one in sixty. The column choice that matters most for a level is the
one that matters least here, and the reverse.

**The refusal rates measured for the two columns read are higher than this repository's record
of them, and the record is not corrected here because it is a different measurement.**
`total_revenue` refused **17 of 4,359 filings (0.390%)** and `n_income_attr_p` **20 of 4,359
(0.459%)**, against the 0.123% and 0.189% `V2-P3-009`'s review recorded over its own two samples
-- 3.2x and 2.4x. Both remain small and neither changes a decision here; what they change is the
standing of any *quoted* rate for these columns, which `domain/financial_statements.py` already
says in its own voice ("how little is a property of the sample and not of the dataset"). The
concentration is confirmed rather than disturbed: `ebit` alone refuses 595 of the 599 ambiguous
filings in the same corpus, 13.65% of it, and this family reads none of the pooled columns.

**The horizon heterogeneity is disclosed rather than removed.** The ratio's span is whatever the
security's newest filing is, so at one `as_of` a name whose latest period is Q1 reports a
three-month growth and a name that has not filed Q1 yet reports a twelve-month one, and both are
`computed` and ranked together. Removing it means a TTM-over-TTM year-on-year, which costs a
nine-period reach for the plain rate and a thirteen-period one for the acceleration; the coverage
that buys is the probe's first number, and it is why the trade is refused rather than ignored.
`tests/integration/panel/test_growth_family.py::
test_two_securities_with_different_newest_filings_grow_over_different_spans`
is the heterogeneity on one partition, so the disclosure is falsifiable rather than prose.

## What is deliberately not here

**None of the five factor families.** `V2-P3-009`'s value family, `V2-P3-010`'s quality family,
`V2-P3-011`'s growth family, `V2-P3-012`'s momentum and reversal family and `V2-P3-013`'s
volatility and liquidity family all ship here. `REVERSAL_1D` stays exactly where it is: it
predates all of them and is the engine's own verification factor rather than a research
deliverable -- see its own docstring for what it does and does not claim, and
`REVERSAL_5_SESSIONS`' for why it is not that factor with a wider window.

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
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, Literal, Protocol, TypeVar, cast
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
from openalpha_cn.domain.factor_neutralization import processed_observation_digest
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
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATASET,
    REPORT_PERIOD_COLUMN,
)
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
    "ambiguous_filing",
    "input_missing",
    "undefined_value",
)
"""The coverage codes in reporting order, restated as a tuple for a stable census key order.

Reconciled against `domain/factor.py::FACTOR_COVERAGE_CODES` by
`tests/unit/test_factor_engine_rules.py`, so the two copies cannot drift -- the same treatment
`panel_fixtures.STATEMENT_DATASETS` gets against the domain's own tuple.

**The order after `computed` is `_classify`'s own order of precedence**, and `V2-P3-018` put
`ambiguous_filing` in the middle of the tuple rather than on the end for that reason: it is
decided after the two history questions (a window that cannot be formed has no filing to be
ambiguous about) and before `input_missing` (a re-fetch repairs a null cell and returns the same
two contradictory rows for an ambiguous one, so reporting the repairable code would send a
reader to a repair that does not work). `tests/unit/test_factor_engine_rules.py::
test_the_census_order_is_the_order_classify_decides_the_codes_in` holds the two together.
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
one of six codes; **input reference** -> `input_row_count` and the two window pairs for the
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
    "observation_digest",
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
        "observation_digest": "string",
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


_UNSEALED_MANIFEST_ID: Final[str] = "fmn_unsealed"
"""What an observation carries between being classified and being sealed, inside `compute_factor`.

`FactorBuildManifest.observation_digest` is a field, so the manifest cannot be constructed until
the cross section exists -- and `FactorObservation.manifest_id` means the manifest cannot be left
out of the cross section either. One of the two has to be provisional for the length of one
function, and this is the half that is safe to make so: the digest is taken over
`(subject, coverage, value)`, which never mentions an identity, so no placeholder can reach the
address it is standing in for. `compute_factor` replaces every row before returning, and
`tests/integration/panel/test_factor_engine.py::
test_no_observation_of_a_computed_panel_carries_the_unsealed_placeholder` is what keeps that from
being a promise -- it asserts on this constant rather than on the string, so renaming it cannot
quietly retire the check.

A named constant rather than `""` because an empty `manifest_id` is a value
`validate_factor_observation` accepts and a stored partition could plausibly hold; this one could
not have been written by any build, so a row carrying it anywhere is unambiguous.
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

    **That leniency is on the way out, not on the way in.** A non-finite *stored cell* is refused
    by `_numeric` before any evaluator sees it, so the two rules point opposite ways on purpose:
    an evaluator's `inf` is this factor's own arithmetic reporting an undefined answer for one
    security, and a partition's `inf` is a fact about the panel that the period axis's collapse
    cannot compare and the session axis has no honest use for.

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


# --- V2-P3-012: the momentum and reversal family ----------------------------------------------


SHORT_REVERSAL_SESSIONS: Final[int] = 5
"""The short-horizon reversal's reach, **and** the sessions every momentum here declines to read.

One constant with two readers, because the two are one decision. `REVERSAL_5_SESSIONS` compounds
this many sessions; each of the three momentum factors declares this many sessions *more* than it
compounds and then drops exactly these from the recent end of its window. What that buys is
arithmetic rather than a hope: at any `as_of`, for any security, the sessions a momentum factor
multiplies and the sessions the reversal factor multiplies are **disjoint** --
`tests/integration/panel/test_factor_momentum_reversal.py::
test_the_sessions_a_momentum_reads_and_the_sessions_the_reversal_reads_are_disjoint` measures it
against two stores that differ only in the newest five sessions' returns. The stored window
columns cannot show it and that is worth knowing: `input_session_first`/`input_session_last`
record the **declared** 25-session window, whose newest session is the reversal's newest session
too. The declared windows nest; the sessions actually multiplied do not overlap.

**Disjointness is the reason the number is 5, and it is the half this repository can prove.**
With no skip a 20-session momentum's window *contains* the 5-session reversal's, and the two
values are then bound by an exact identity -- both are products of the same per-session growth
factors, so `1 + m20 == (1 + m15) * (1 + r5)` to floating point. `V2-P3-008`'s redundancy analysis
groups by family, and a family shipping a pair related by an identity would hand it a finding
about arithmetic rather than about the market.
`tests/unit/test_factor_momentum_reversal_rules.py::
test_an_unskipped_momentum_is_its_reversal_times_the_rest_of_its_own_window` drives the identity,
so the reason is measured rather than asserted in prose.

**The conventional reason is named and is not claimed.** Momentum is customarily stated over a
period ending before the short-term reversal window, so that the two do not measure one another.
That is the family's prior; this repository has measured nothing about it, and `V2-P3-005` is the
issue where an information coefficient would say anything at all.

What the skip costs is stated rather than hidden: a 20-session momentum now needs **25** sessions
of visible history, so a name that listed 22 sessions ago is `insufficient_history` for it where
an unskipped definition would have answered. That is the price of the disjointness, paid in the
coverage census where it is visible.
"""

MAX_HALTED_SESSIONS_IN_FIVE: Final[int] = 1
"""How much of a **momentum** window may be halt: one session in five of its own reach.

`max_window_sessions` is the number of panel sessions a window may reach across, and the excess
over `lookback_sessions` is exactly the number of sessions the security did not trade inside it.
So this constant is the whole of that judgement for the three momentum factors:
`max_window_sessions == lookback_sessions + lookback_sessions * 1 // 5`.

**Why not equality.** `max_window_sessions == lookback_sessions` is the strict setting and it is
the right one for a five-session reversal (see `REVERSAL_5_SESSIONS`). It is the wrong one for a
half-year momentum, and this repository has the measurement: `domain/daily_prices.py::
MIN_SESSION_ROW_SHARE` records that 2015-07-09 served **1,363** bars against that year's median of
2,359 -- 42% of the market with no row, with 07-08 at 1,489 and 07-10 at 1,425. Under equality
every one of those names carries `insufficient_history` for the next 125 sessions, on a factor
whose whole subject is the price path they do have.

**Why one in five and not one in two.** The tolerance is what the value's *calendar* reach is
allowed to become, and at one in two a "120-session momentum" may span 250 panel sessions --
which is a year, not half of one, on the 242/243/244-session years `daily_prices.py` measured for
2024/2018/2015. The bound therefore has to sit well under 1.0, and one in five keeps the widest
reach of the 120-session factor at 150 sessions: about seven months against six nominal.

**What makes the number auditable rather than free.** The three reaches form a ladder, and the
tolerance is required to keep each rung out of the next one's nominal reach: 30 < 65 < 125, and
78 < 125, so no momentum factor's worst-case window can cover the interval the next factor is
*defined* over. `tests/unit/test_factor_momentum_reversal_rules.py::
test_no_momentum_windows_worst_case_span_reaches_the_next_rungs_own_reach` asserts the ladder and
`test_every_momentum_span_is_the_declared_halt_tolerance_of_its_own_reach` asserts the arithmetic,
so widening this constant fails a test rather than merely changing a number.

**What is not claimed.** There is no measured distribution of A-share halt *durations* in this
repository -- the 2015 census above is a market-wide count on three days, not a per-security
length -- so this is a declared judgement with a stated cost, not a calibration. The cost: a name
that missed more than 25 of the 125 sessions behind its 120-session momentum gets
`insufficient_history` rather than a value.
"""

MOMENTUM_20_SESSIONS: Final[FactorDefinition] = FactorDefinition(
    key="momentum_20_sessions",
    version=1,
    family="momentum_reversal",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
    ),
    lookback_sessions=25,
    max_window_sessions=30,
    lookback_periods=None,
    max_window_periods=None,
)
"""Twenty sessions of compounded return, ending five sessions before `as_of`'s newest session.

`25 = 20 + SHORT_REVERSAL_SESSIONS`: the reach is what the window has to *hold*, and the skip is
what the evaluator declines to read off the recent end of it. `30 = 25 + 25 * 1 // 5` is
`MAX_HALTED_SESSIONS_IN_FIVE` applied to that reach, and 30 is under `MOMENTUM_60_SESSIONS`' own
reach of 65 -- so a halted name's 20-session momentum can never cover the interval the 60-session
factor is defined over.

**Both price columns are read and neither is optional**; see `_compounded_session_return` for the
measurement that decides it. A momentum is a multi-period return, and the only per-session return
this repository will compute is `close / pre_close - 1`.
"""

MOMENTUM_60_SESSIONS: Final[FactorDefinition] = FactorDefinition(
    key="momentum_60_sessions",
    version=1,
    family="momentum_reversal",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
    ),
    lookback_sessions=65,
    max_window_sessions=78,
    lookback_periods=None,
    max_window_periods=None,
)
"""Sixty sessions of compounded return, ending five sessions before `as_of`'s newest session.

`65 = 60 + SHORT_REVERSAL_SESSIONS` and `78 = 65 + 65 * 1 // 5`, which is under
`MOMENTUM_120_SESSIONS`' reach of 125. See `MOMENTUM_20_SESSIONS` and
`MAX_HALTED_SESSIONS_IN_FIVE`.
"""

MOMENTUM_120_SESSIONS: Final[FactorDefinition] = FactorDefinition(
    key="momentum_120_sessions",
    version=1,
    family="momentum_reversal",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
    ),
    lookback_sessions=125,
    max_window_sessions=150,
    lookback_periods=None,
    max_window_periods=None,
)
"""A hundred and twenty sessions of compounded return, ending five sessions before the newest.

`125 = 120 + SHORT_REVERSAL_SESSIONS` and `150 = 125 + 125 * 1 // 5`. There is no next rung, so
the ladder check for this one is against the trading year rather than against another factor:
150 is well under the 242 sessions of the shortest A-share year `domain/daily_prices.py` measured
(2024: 242, 2018: 243, 2015: 244), so a name halted to the tolerance still has a half-year
momentum rather than an annual one.

**This is the definition `V2-P3-002`'s reach fields were built for**, and the consequence lands on
its caller rather than here: a 125-session window evaluated in January reaches into the previous
calendar year, so `requirement.years` must name that year too or
`_refuse_a_panel_narrower_than_the_lookback` refuses the whole build.
`tests/integration/panel/test_factor_momentum_reversal.py::
test_a_january_as_of_needs_the_previous_year_named_and_says_so_when_it_is_not` drives both sides.
"""

REVERSAL_5_SESSIONS: Final[FactorDefinition] = FactorDefinition(
    key="reversal_5_sessions",
    version=1,
    family="momentum_reversal",
    direction="lower_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
    ),
    lookback_sessions=SHORT_REVERSAL_SESSIONS,
    max_window_sessions=SHORT_REVERSAL_SESSIONS,
    lookback_periods=None,
    max_window_periods=None,
)
"""Five consecutive sessions of compounded return, the most recent ones knowable at `as_of`.

**`max_window_sessions == lookback_sessions` is the strict setting and is chosen here**, which is
the opposite of the three momentum factors and is the same judgement `REVERSAL_1D` makes: the
whole content of a short-horizon reversal is that the interval is recent *and unbroken*. A
five-session window spanning thirty panel sessions is a six-week return reported as a one-week
one, and `insufficient_history` is the honest answer for a security that was halted through it.
The cost is that a name halted for even one session in the last five has no value here, which is a
strictly larger cost than the momentum factors pay and is the reason the two settings differ.

**It is not `REVERSAL_1D` widened.** That factor is the engine's verification subject and computes
`close[t] / close[t-1] - 1` -- the third of the three paths `domain/daily_prices.py` measured, the
one that reads `-0.5310%` where the two correct paths read `+2.7422%` across an ex-rights morning.
Its own note says `V2-P3-012` "will not be built on top of this", and this is what that means:
this factor reads `pre_close` as well as `close`, so it is a product of published session returns
rather than a ratio of two closes. On a window with no corporate action in it the two agree; the
one this repository measured is the window where they do not.
"""


def _compounded_session_return(window: FactorWindow, *, skip: int) -> float | None:
    """`prod(close[i] / pre_close[i]) - 1` over the window, less its `skip` newest sessions.

    ## The return path, which is the one thing this family cannot get wrong

    `domain/daily_prices.py` measured three ways to compute one session's return across
    `000001.SZ`'s 2026-06-12 ex-dividend date and found that two agree and one is wrong in the
    **sign**:

        close / pre_close - 1, which is the endpoint's own pct_chg   +2.742230%
        (close*f) / (prev_close*f_prev) - 1, the adj_factor path     +2.742251%
        close / prev_close - 1                                       -0.530973%

    A momentum is that quantity compounded, so the third path's error compounds with it. This
    function takes the first: `pre_close` is the previous session's close **already restated for
    whatever corporate action took effect that morning**, so the per-session growth factor
    `close / pre_close` is corporate-action-correct on every session by construction, and their
    product telescopes to the true multi-period return without this module owning one line of
    adjustment arithmetic.

    Two consequences worth stating because they are what make the choice load-bearing rather than
    stylistic:

    - **A halt inside the window costs nothing.** The window is the security's *own* sessions, and
      the `pre_close` of the session on which it resumes is the pre-halt close restated. So the
      product spans the halt correctly; what the halt does cost is span, which
      `max_window_sessions` prices and `MAX_HALTED_SESSIONS_IN_FIVE` sets.
    - **A limit-up or limit-down session is an ordinary session and is compounded as one.** The bar
      exists, and `close / pre_close` on it is the return the security actually had -- clipped by
      the band, which is a fact about the market rather than about the data. No coverage code is
      spent on it and no session is masked.
    - **The `adj_factor` path is not taken**, though it is equally correct, because it would make
      every momentum factor read a second dataset -- and a two-dataset factor turns a missing
      `adj_factor` row into `input_missing` on a security whose prices are all present.
      `daily_prices.py` measured 49 such names on 2024-06-28, in the other direction.

    **Neither `suspend_d` nor `stk_limit` is a `required_field` of anything in this family**, and
    both absences are decisions rather than omissions. `suspend_d`'s own columns are text
    (`suspend_type`, `suspend_timing`) and `_numeric` refuses a non-numeric cell, so a halt state
    is not declarable as a factor input at all; a halt reaches these factors as the sessions the
    security does not own, which is where `max_window_sessions` prices it. `stk_limit`'s two
    columns *are* floats and could be declared, and are not: masking a limit session would be a
    judgement about the market that nothing here has measured, it would make every value depend on
    a second dataset's coverage, and that dataset carries the Beijing-board encoding where
    `down_limit` is a fixed `0.0` (`domain/price_limits.py`: every row with `down_limit <= 0` over
    235 sessions is a `.BJ` code with a sentinel `up_limit`). A factor whose window silently
    dropped sessions on a sentinel it did not understand is the shape `V2-P2` already paid for.

    **`pct_chg` is deliberately not read either**, though it is stored in the same rows and would
    save a column. It is the *third witness*: `session_returns` reconciles it against
    `close / pre_close - 1` to catch a `close` that arrived wrong, and a factor that consumed the
    witness instead of the thing witnessed would be scoring the upstream's own arithmetic. It is
    also published on two grids (`MAX_PUBLISHED_RETURN_DISAGREEMENT` is one tick of the coarse
    one, `1e-4` in return space), and one tick per session compounded over 120 sessions is a
    quantity nobody has bounded.

    ## `None` is a zero denominator and nothing else

    `DAILY_PRICE_COLUMNS` records that nineteen sessions spanning 2001-2026 (58,055 bars) carried
    no null and no non-positive `pre_close`, and `daily_bars_from_panel_rows` refuses one, so this
    branch is not reachable through this repository's own writers. It is here for `_reversal_1d`'s
    stated reason -- `undefined_value` has to be a branch that runs -- and it is driven directly in
    `tests/unit/test_factor_momentum_reversal_rules.py::
    test_a_zero_pre_close_anywhere_in_the_window_is_undefined_rather_than_a_division`.

    `skip` is subtracted from the *end* of the window, so the sessions read are the oldest
    `len(window) - skip` of it; `SHORT_REVERSAL_SESSIONS` is what the momentum factors pass and
    `0` is what the reversal passes. Every definition in this family declares a reach strictly
    greater than the `skip` its evaluator uses, so the slice is never empty.
    """
    closes = window.series(DAILY_DATASET, CLOSE_COLUMN)
    previous = window.series(DAILY_DATASET, PRE_CLOSE_COLUMN)
    read = len(closes) - skip
    growth = 1.0
    for close, before in zip(closes[:read], previous[:read], strict=True):
        if before == 0.0:
            return None
        growth *= close / before
    return growth - 1.0


def _momentum_sessions(window: FactorWindow) -> float | None:
    """The evaluator all three momentum factors are bound to: skip five, compound the rest.

    **One function for three keys, and that is a statement rather than a saving.** The three
    momenta differ in exactly one thing -- the reach their definitions declare -- and the reach is
    what forms the window before an evaluator is called. Three identical one-line functions would
    have implied a difference that does not exist, and a mutation swapping any two of them would
    have changed nothing while looking like it might. `FACTOR_EVALUATORS` maps three keys here;
    `_refuse_table_drift` compares key sets and is indifferent to how many distinct callables the
    table holds.
    """
    return _compounded_session_return(window, skip=SHORT_REVERSAL_SESSIONS)


def _reversal_5_sessions(window: FactorWindow) -> float | None:
    """`REVERSAL_5_SESSIONS`: all five sessions of its window, compounded. No skip.

    A second function rather than a fourth key on `_momentum_sessions`, because the `skip` really
    is different -- which is the whole of what separates the two halves of this family.
    """
    return _compounded_session_return(window, skip=0)


_MOMENTUM_DIRECTION_PROSE: Final[str] = (
    " The declared direction is the family's conventional prior -- a security that has risen "
    "over the stated interval is taken to be the better one -- and this repository has measured "
    "nothing whatever about it, on this factor or on any other. V2-P3-005 is where an "
    "information coefficient would say something, and V2-P3's own gate records that most "
    "first-batch factors being insignificant is the expected result rather than a failure."
)
"""The direction sentence the three momentum notes share, held to `REVERSAL_1D_NOTE`'s standard.

Written once because it is one claim about three factors and a copy is a thing that drifts; it is
a plain string concatenated into each note rather than a `FactorNote` of its own, because a note
is keyed by the contract it is about and this sentence is about three of them.
"""

MOMENTUM_20_SESSIONS_NOTE: Final[FactorNote] = FactorNote(
    subject=MOMENTUM_20_SESSIONS.qualified_key,
    summary=(
        "Twenty sessions of compounded published session return -- the product of "
        "close / pre_close over the twenty sessions ending five sessions before the newest one "
        "knowable at as_of -- so the declared reach is 25 sessions and the evaluator reads the "
        "oldest 20 of them. The five it drops are exactly the five reversal_5_sessions reads, "
        "which makes the two factors' windows disjoint instead of leaving them bound by the "
        "identity a shared window forces. pre_close rather than the previous session's close "
        "because pre_close is already restated for that morning's corporate action, which is the "
        "difference domain/daily_prices.py measures as +2.7422% against -0.5310% on one real "
        "ex-dividend date. A window may span 30 panel sessions, one halted session in five of its "
        "own reach, which is under the 65 sessions momentum_60_sessions is defined over."
        + _MOMENTUM_DIRECTION_PROSE
    ),
)

MOMENTUM_60_SESSIONS_NOTE: Final[FactorNote] = FactorNote(
    subject=MOMENTUM_60_SESSIONS.qualified_key,
    summary=(
        "Sixty sessions of compounded published session return, on momentum_20_sessions' terms "
        "throughout: the product of close / pre_close over the sixty sessions ending five "
        "sessions before the newest one knowable at as_of, a declared reach of 65 sessions, and "
        "a span bound of 78 -- one halted session in five of its own reach, which stays under "
        "the 125 sessions momentum_120_sessions is defined over so the two cannot become each "
        "other on a heavily halted name. It is the horizon in this family that no other factor "
        "here brackets from both sides, which is the only sense in which it is the middle one."
        + _MOMENTUM_DIRECTION_PROSE
    ),
)

MOMENTUM_120_SESSIONS_NOTE: Final[FactorNote] = FactorNote(
    subject=MOMENTUM_120_SESSIONS.qualified_key,
    summary=(
        "A hundred and twenty sessions of compounded published session return, on "
        "momentum_20_sessions' terms: a declared reach of 125 sessions, the oldest 120 of them "
        "read, and a span bound of 150. There is no wider factor in this family to bracket it, "
        "so the bound is checked against the trading year instead -- 150 is well under the 242 "
        "sessions of the shortest A-share year domain/daily_prices.py measured, so even a name "
        "halted to the tolerance still has a half-year momentum rather than an annual one. This "
        "is the factor V2-P3-002's session reach fields were argued for: a name halted for three "
        "months has a 120-session window spanning far more calendar than 120 sessions, and "
        "max_window_sessions is what refuses it rather than reporting it as computed."
        + _MOMENTUM_DIRECTION_PROSE
    ),
)

REVERSAL_5_SESSIONS_NOTE: Final[FactorNote] = FactorNote(
    subject=REVERSAL_5_SESSIONS.qualified_key,
    summary=(
        "Five consecutive sessions of compounded published session return: the product of "
        "close / pre_close over the five most recent sessions knowable at as_of, with no skip. "
        "Its span bound equals its reach, which is the strict setting and the opposite of the "
        "three momentum factors' -- the whole content of a short-horizon reversal is that the "
        "interval is recent and unbroken, so a name halted for even one session in the last five "
        "is insufficient_history rather than a five-session return spread over six. It is not "
        "reversal_1d widened: that factor divides two closes, which is the one of three return "
        "paths domain/daily_prices.py measured as wrong across an ex-rights morning, and this "
        "one multiplies published session returns. The declared direction is the family's "
        "conventional prior -- a lower recent return is taken to be the better one -- and this "
        "repository has measured nothing about it; V2-P3-005 is where an information coefficient "
        "would say anything, and reversal_1d's own note makes the same disclaimer for the same "
        "reason."
    ),
)

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


# --- `V2-P3-009`: the value family --------------------------------------------------------------
#
# See this module's docstring section "The value family" for the five judgements every factor
# here shares -- the two axes, the denominator, the trailing window, the sign, and the column
# choice -- and for why EPcut is not among them.


MARKET_CAP_COLUMN: Final[str] = "total_mv"
"""`daily_basic`'s whole-company market capitalisation, **in units of 10,000 yuan**.

The denominator of every factor in this family, and `circ_mv` is deliberately not it.
`panel_neutralization.INDUSTRY_AND_SIZE` already made the same choice one plane down and for the
reason that applies here too: the whole company is what a filing describes, and the restricted
fraction `circ_mv` excludes is not a fact about the earnings the numerator counts. A book value
divided by the float's market value would be a ratio whose two halves cover different share
counts.

Spelled here rather than imported, on `TURNOVER_RATE_COLUMN`'s terms: `domain/daily_prices.py`
carries this name only inside `DAILY_BASIC_DATA_COLUMNS`, and
`tests/unit/test_factor_value_family.py::
test_the_columns_this_family_reads_are_columns_the_stored_contracts_declare` holds it against
that tuple in both directions.
"""

CNY_PER_MARKET_CAP_UNIT: Final[float] = 10_000.0
"""Yuan per unit of `daily_basic.total_mv`, **measured** on stored rows rather than assumed.

`CNY_PER_AMOUNT_UNIT`'s problem in the other dataset, and it matters here for a sharper reason:
a value factor divides a filing, published in yuan, by a market capitalisation published in
something else, so the unit is not a scale on one side of a ratio -- it is the *only* thing that
makes the two sides commensurable. Get it wrong and every earnings yield in the panel is off by
a factor of 10,000 while every rank IC, every z-score and every neutralised residual is
unchanged, because all three are scale-free.

The measurement is a two-step chain over the four real `daily_basic` rows this repository already
stores in `tests/unit/domain/test_daily_prices.py`, and neither step needs an upstream field list:

1. **`total_share` and `float_share` are in units of 10,000 shares.** `000001.SZ` on 2026-06-12
   traded 2,032,355.46 lots against `float_share=1,940,560.0653`, and `vol * 100 / (float_share *
   10,000)` is 1.0473% -- exactly the stored `turnover_rate`. Reading `float_share` as shares puts
   it at 10,473%.
2. **`total_mv` is `close * total_share`, so it carries the same 10,000.** `11.24 *
   1,940,591.8198 = 21,812,252.0546` against a stored `21,812,252.0568`, and `1,291.91 *
   125,008.1601 = 161,499,292.11` against a stored `161,499,291.9856`. The widest relative gap
   over the four rows and both capitalisation columns is **2.4e-9**, which is the rounding of two
   columns published to two and four decimals; the next candidate reading is out by 1e4. In yuan
   those two caps are 218.1bn and 1.615tn, which are the right order of magnitude for those two
   companies and the other readings are not.

`tests/unit/test_factor_value_family.py::
test_the_market_cap_unit_is_ten_thousand_yuan_and_no_other_reading_reproduces_the_close` is that
chain as an executable measurement, on all four stored rows.
"""

NET_PROFIT_COLUMN: Final[str] = "n_income_attr_p"
"""EP's numerator: net profit **attributable to owners of the parent**, cumulative, in yuan.

`n_income` is the consolidated figure and includes the minority interest, which no holder of a
listed share has a claim on -- and `total_mv` prices exactly the parent's shares. So the pair
`(n_income_attr_p, total_mv)` covers one claim on both sides and `(n_income, total_mv)` does not.
The two columns are both stored and are different numbers on real rows: `600739.SH`'s 2024 annual
gives `n_income` 664,195,391.66 against `n_income_attr_p` 209,556,865.25, a factor of 3.2.

**It is not one of the concentrated columns, and its own rate is measured rather than bounded.**
`income`'s 288 refused field reads on the 53-security corpus are 258 `ebit` plus 30 spread over
the other nine, which bounded this column at 30 in 3,201 filings *on that sample*.
`V2-P3-009`'s review measured the column itself instead, over two disjoint 91-security samples:
7 of 5,210 filings and 13 of 5,357, **20 of 10,567 (0.189%)**. The bound held; the intermediate
quantity it was derived from did not (the non-`ebit` residue is 1.72% on the wider sample against
the 0.94% recorded), which is why the measurement replaces it -- see this module's docstring
section "The value family".

**And that measurement does not travel either.** `V2-P3-011`'s live probe re-measured this column
over two *different* disjoint 60-security samples -- 4,359 filings, sampled by stride over the
whole listed universe rather than over one issue's corpus -- and got **20 of 4,359 (0.459%)**,
**2.4x** the figure above. Neither number is wrong about its own sample and neither changes a
decision; what they jointly establish is that a refusal rate quoted for one of these columns is a
property of the sample it was taken on, which is `domain/financial_statements.py`'s own sentence
landing for the third time. Any later issue that needs this number should measure it rather than
cite either. See this module's docstring section "The growth family".
"""

TOTAL_REVENUE_COLUMN: Final[str] = "total_revenue"
"""SP's numerator: 营业总收入, cumulative, in yuan -- the top line, and not `revenue`.

Both are stored and the choice is between them rather than a default. `total_revenue` is the
topmost line of the CAS income statement and `revenue` (营业收入) is one of the lines it totals, so
the top line is the **inclusive** one: whatever an issuer reports above its operating revenue is
inside `total_revenue` and outside `revenue`, and the narrower column drops it without saying so.
`domain/financial_statements.py` records that the projection "holds only columns every company
type publishes", and the top line is the one that means the same thing on every `comp_type`.

**Where the two actually part is measured, and it is the opposite of where the shape of the
statement puts it.** `V2-P3-009`'s review probed the served `income` rows by `comp_type` -- the
whole history of 14 banks, insurers and brokers, and a 47-name industrial sample:

| `comp_type` | rows | equal | differing |
|---|---:|---:|---:|
| `2` bank | 647 | 647 | **0** |
| `3` insurer | 358 | 358 | **0** |
| `4` broker | 345 | 345 | **0** |
| `1` ordinary industrial | 1,468 | 1,431 | **37** |

The company types whose top line is textbook *not* one number -- a bank's interest income, an
insurer's earned premiums -- arrive with the two columns carrying the same number on all 1,350 of
those rows, and the only place this endpoint separates them is the ordinary industrial.
`600519.SH` separates them on **all 42** of its stored periods: 2024Q3 is 123,122,542,625 of
`total_revenue` against 120,776,131,875 of `revenue`, so `revenue` is short of the top line by
1.94% of itself; `002208.SZ`'s 2022Q3 is 1,612,572,587 against 1,604,974,825, 0.47%.

**So a fixture can decide this, and one does.**
`tests/unit/test_factor_value_family.py::
test_the_top_line_and_revenue_are_two_different_numbers_on_a_real_industrial_row` asserts those
rows and the choice against the stored projection, so swapping the column is a deliberate act
that lands 1.94% away rather than on the same answer. That replaces the argument this docstring
made until `V2-P3-009`'s review falsified it in both directions -- that the two are equal on an
ordinary industrial and only a bank could tell them apart. Every real `income` row *this
repository stores* does carry the two equal, but three of its four securities (`603333.SH`,
`600739.SH`, `920403.BJ`) are `comp_type=1` names inside the 97.5% that agree and the fourth,
`000001.SZ`, is a bank and not the "ordinary industrial" that sentence called it --
`tests/contract/providers/test_tushare_financials.py` says so in its own fixture comment, that
its `oper_cost` is null on both rows "because a bank publishes no cost of sales". So that corpus
never had the power to decide this either way, which is this repository's own lesson about an
assertion whose fixture cannot separate two answers, appearing for the sixth time.

**Everything above is about the two columns' *levels*, and `V2-P3-011` needed the same question
asked about their *rates*.** A growth rate divides a constant scale out, so "the two are 1.94%
apart on every one of `600519.SH`'s periods" settles nothing about a year-on-year built from
either. Measured on the same live probe that re-measured the refusal rate below -- 230
(security, `as_of`) pairs -- the two columns give a **different** year-on-year on **4 of them,
1.7%**, against 76.8% for the `n_income` / `n_income_attr_p` pair. So this is the column choice
that separates most on a level and least on a rate, which is why `REVENUE_YOY` re-argues it rather
than inheriting it.

**The refusal rate below does not travel either.** `V2-P3-011`'s probe measured this column at
**17 of 4,359 filings (0.390%)** over two disjoint 60-security stride samples of the whole listed
universe, **3.2x** the 0.123% recorded here. Both are true of their own samples and neither
changes a decision; see `NET_PROFIT_COLUMN` for the same finding on the other column and this
module's docstring section "The growth family" for what it means for a quoted rate.
"""

BOOK_EQUITY_COLUMN: Final[str] = "total_hldr_eqy_exc_min_int"
"""BP's numerator: owners' equity **excluding** minority interest, a stock rather than a flow.

Three candidates existed and two are refused with a measurement:

- **`fina_indicator.bps` times a share count.** Refused outright. `bps` is the field
  `domain/financial_statements.py` opens with: the two versions of `603049.SH`'s 2024 annual give
  22.2055 and 2.2206, a factor of ten, under one `ann_date` on an endpoint with **no
  `update_flag`, no `f_ann_date` and no `report_type`** -- so nothing in the panel orders them and
  the wrong pick is a book-to-price wrong by 10x. The share count would have to come from
  `balancesheet.total_share`, which is that endpoint's *most* disagreed field (34 of its 43
  refusals; `000002.SZ` 2023H1 at 11,630,709,471 against 11,930,709,471). A numerator built from
  those two would multiply this family's exposure to the one thing this repository has measured
  most carefully about these datasets.
- **`total_assets - total_liab`.** Refused because it is the *consolidated* equity: it includes
  the minority interest that `total_mv` does not price. On `000002.SZ`'s 2023H1 rows the
  difference is real -- 1,684,196,409,372.7 less 1,281,551,927,215.46 is 402,644,482,157.24
  against a stored `total_hldr_eqy_exc_min_int` of 249,326,669,106.12, 61% larger. It also reads
  two columns where one will do, so a null or a disagreement in either refuses the read.
- **`total_hldr_eqy_exc_min_int`**, which is the stored column that already means what the
  denominator prices, and is outside `balancesheet`'s concentration: 34 of that dataset's 43
  refused reads are `total_share`. That used to be the whole argument, as "at most 9 for the
  other six columns over 3,170 filings" -- an upper bound derived from a residue *on one sample*,
  and `V2-P3-009`'s review measured the residue at 60 / 5,120 and 32 / 5,249 on two wider ones,
  two to four times the 0.28% that bound implied. The column's own rate is measured instead, on
  those same two disjoint 91-security samples: 13 of 5,120 filings and 8 of 5,249, **21 of
  10,369 (0.203%)**, which is inside the old bound but is now a number about this column rather
  than about the five it shares a residue with.

**Not immune, and the repository holds the counterexample.** `002538.SZ`'s 2022 annual, announced
2023-04-21, is three rows whose values of this column are 5,346,322,691.02, 5,297,379,808.74 and
5,150,097,232.47 -- so a real filing in this repository's own fixtures disagrees about BP's
numerator by 3.8%. `domain/financial_statements.py`'s warning that "no projected column should be
read as immune" is not rhetorical here; it is this column, on that filing.
"""

TRAILING_TWELVE_MONTH_PERIODS: Final[int] = 5
"""How many contiguous filings a trailing-twelve-month numerator needs: the latest and four back.

**The window is five because the arithmetic needs three of its members and the contract only
guarantees them contiguously.** A-share statements are *cumulative within the fiscal year* -- Q1
is three months, H1 six, Q3 nine, the annual twelve -- and a PRC listed company's accounting year
is the calendar year (`FISCAL_QUARTER_ENDS`). So the trailing twelve months ending at period `P`
is

    TTM(P) = cumulative[P] + cumulative[the December before P] - cumulative[P - 4 quarters]

and a window of five contiguous quarters ending at `P` contains all three by construction:
`window[0]` is `P - 4 quarters`, `window[-1]` is `P`, and `window[:-1]` is four consecutive
quarters and therefore holds **exactly one** December. At `P = 31 December` the December before
`P` *is* `window[0]`, the two terms cancel and the answer is the annual cumulative itself, which
is correct.

**What makes the index arithmetic legitimate is `max_window_periods == lookback_periods`**, which
`FactorDefinition` documents as "no missed filing inside the window" and `_period_span` makes true
against the fiscal-quarter grid rather than against the periods this build happened to read. That
equality is not a spare setting here: on a window with a gap, `window[:-1]` can hold no December
at all, and `_trailing_twelve_months` returns `None` rather than differencing two periods that are
not the ones the formula names.

**The cost is stated rather than absorbed, and it is wider than the identity's own appetite.**
Five filings is about fifteen months of disclosure history, so a security that listed within the
last year is `insufficient_history` for `EP` and `SP` where `book_to_price` -- one period, a
stock quantity, no differencing -- answers. That is a real coverage difference between two
factors of one family and it is the price of not putting a six-month numerator and a nine-month
numerator into one cross section, which is what reading the latest cumulative alone would do the
moment two issuers file on different schedules.

The arithmetic above reads **three** of the five periods -- `window[-1]`, the December inside
`window[:-1]` and `window[0]` -- while `5 / 5` requires all five to be there and contiguous. So a
security that has every period the formula names and is missing only one of the two it never
reads is `insufficient_history` too: the window cannot be formed, and `_trailing_twelve_months`
is never reached. **That is the window model's cost and not the identity's**, and it is priced
that way deliberately -- the contract's guarantee is "no missed filing inside the window", which
is what makes the December search a search over four *consecutive* quarters rather than over
whatever four the build happened to read. A reach that admitted the gap would have to decide
which quarters a three-period window is allowed to skip, and that decision is exactly what
`max_window_periods` exists to refuse.
"""

MARKET_CAP_SESSIONS: Final[int] = 1
"""How many sessions of `daily_basic` a value factor reads: the newest one knowable at `as_of`.

A price is a fact about an instant and the denominator of a value ratio is the price *now*, not
an average of prices. So the reach is one, and `max_window_sessions` is one too -- with a
one-session count the span bound can never bind (one of a security's own sessions always spans
exactly one panel session), and `1` is the setting that says so rather than implying a tolerance
this factor does not have. `FactorDefinition` requires the pair to be stated whenever
`required_fields` names a session dataset, which is why it is a declared `1` and not an absence.

Averaging the cap over a window was considered and rejected for `AMIHUD_60`'s reason in the other
direction: it would make the value a function of a horizon nobody here has measured, and it would
make a name halted for part of the window carry a denominator from before the halt.
"""

FISCAL_YEAR_END_MONTH: Final[int] = 12
"""The month a PRC fiscal year ends in, which is what `_trailing_twelve_months` searches for.

A statutory fact rather than a parameter -- the same one `FISCAL_QUARTER_ENDS` rests on -- and
named so the search reads as "find the fiscal year end" rather than as a bare `12`.
"""


def _market_capitalisation(window: FactorWindow) -> float | None:
    """The newest knowable market capitalisation, **in yuan**, or `None` when it is not positive.

    `None` becomes `undefined_value`, and unlike this module's other zero-denominator guards it is
    reachable through a partition this repository's own writers accept.
    `domain/daily_prices.py::_stored_number` requires `total_mv` to be a finite float and **not**
    a positive one -- only the five columns of `DAILY_PRICE_COLUMNS` get `_stored_price`'s
    positivity check -- so a stored `0.0` is readable, and
    `tests/integration/panel/test_value_family.py::
    test_a_zero_market_capitalisation_is_undefined_value_for_every_factor_in_the_family` provokes
    it end to end on disk. `AMIHUD_60`'s zero `amount` is the same argument in the same shape.

    Non-positive rather than zero, because a negative capitalisation would divide into a value
    whose sign is the numerator's reversed -- a loss-making company reported as the cheapest name
    in the cross section. What this repository has measured about `total_mv` is that it was
    *populated* on every one of 51,708 rows across eighteen sessions spanning 2001 to 2026; it has
    measured nothing about its sign, so the wider guard is the one whose failure mode is a
    refusal rather than a wrong number.
    """
    capitalisation = window.series(DAILY_BASIC_DATASET, MARKET_CAP_COLUMN)[-1]
    in_yuan = capitalisation * CNY_PER_MARKET_CAP_UNIT
    if in_yuan <= 0.0:
        return None
    return in_yuan


def _trailing_twelve_month_sum(
    periods: Sequence[date], cumulative: Sequence[float]
) -> float | None:
    """The identity itself, over a bare pair of aligned sequences rather than over a window.

    `cumulative[-1] + cumulative[the December among periods[:-1]] - cumulative[0]`, which is what
    `TRAILING_TWELVE_MONTH_PERIODS` states. Split out of `_trailing_twelve_months` by `V2-P3-010`
    because that function reads a whole `FactorWindow` and the quality family needs the same
    arithmetic on a **slice** of one: `gross_margin_stability` slides a five-period window across
    an eight-period one, and there is no `FactorWindow` for a slice. Two copies of an identity
    this repository argues about at length would be two things that can drift apart, which is
    `_yield_on_market_capitalisation`'s reason one level down.

    `None` -- hence `undefined_value` -- when `periods[:-1]` does not hold exactly one fiscal year
    end, which is what the caller's contiguity buys and what a gap takes away. See
    `_trailing_twelve_months` for why that branch is unreachable through the engine at the value
    family's reach, and `_gross_margin_stability` for why it stays unreachable across every slice
    of a contiguous eight.
    """
    year_ends = [
        index for index, period in enumerate(periods[:-1]) if period.month == FISCAL_YEAR_END_MONTH
    ]
    if len(year_ends) != 1:
        return None
    return cumulative[-1] + cumulative[year_ends[0]] - cumulative[0]


def _trailing_twelve_months(window: FactorWindow, *, dataset: str, column: str) -> float | None:
    """`cumulative[-1] + cumulative[the December in the window] - cumulative[0]`.

    The identity `TRAILING_TWELVE_MONTH_PERIODS` states, read off the window's own periods rather
    than off a calendar this module would then have to agree with: the window is aligned to
    `FactorWindow.periods` index for index, so the December is found by looking at the periods and
    the arithmetic is done on the values at the same offsets.

    `None` -- hence `undefined_value` -- when `window.periods[:-1]` does not hold exactly one
    fiscal year end. **That is unreachable at this family's declared reaches and is a branch
    rather than an assumption**, for `_sample_stdev`'s stated reason: `_report_period` refuses a
    period that is not one of `FISCAL_QUARTER_ENDS`, `_form_window` hands over exactly
    `lookback_periods` of them, and `_overruns_its_span` refuses a window whose fiscal-quarter
    span exceeds `max_window_periods` -- so at `5 == 5` the four earlier periods are four
    consecutive quarters and exactly one of them ends a year. Relax the span bound and the branch
    fires;
    `tests/unit/test_factor_value_family.py::
    test_a_window_with_a_gap_has_no_trailing_twelve_months_rather_than_a_wrong_one` drives it on a
    window this engine will not build, which is the only way to drive it at all.
    """
    return _trailing_twelve_month_sum(window.periods, window.series(dataset, column))


def _yield_on_market_capitalisation(
    window: FactorWindow, *, dataset: str, column: str
) -> float | None:
    """A trailing-twelve-month flow over the newest market capitalisation, both in yuan.

    One function for `earnings_yield_ttm` and `sales_yield_ttm`, on `_momentum_sessions`' terms:
    the two factors differ in exactly one thing -- which `income` column they name -- and two
    copies of this body would have implied a difference that does not exist.

    **A negative result is `computed` and not `undefined_value`, and that is the reason this
    family is stated as a yield rather than as a multiple.** A loss-making issuer has a negative
    trailing net profit and therefore a negative earnings yield, and the arithmetic has an answer:
    `E/P` is monotone through zero, so `-0.5` ranks below `+0.01` and the ordering says what it
    should. `P/E` is not -- it runs to `+inf` as earnings approach zero from above and reappears at
    `-inf` from below, which is why the published `daily_basic.pe`/`pe_ttm` columns are simply
    *null* for a loss-maker (1,102 and 1,214 nulls of 5,338 rows on 2024-06-28,
    `domain/daily_prices.py`'s own census). Inverting the published multiple would therefore hand
    this factor an `input_missing` for about a fifth of the market -- precisely the fifth a signed
    yield scores correctly.
    """
    capitalisation = _market_capitalisation(window)
    if capitalisation is None:
        return None
    trailing = _trailing_twelve_months(window, dataset=dataset, column=column)
    if trailing is None:
        return None
    return trailing / capitalisation


_VALUE_DIRECTION_PROSE: Final[str] = (
    " The declared direction is the value premium's conventional prior -- the cheaper security, "
    "the one whose fundamental buys more per yuan of price, is taken to be the better one -- and "
    "this repository has measured nothing whatever about it, on this factor or on any other. "
    "V2-P3-005 is where an information coefficient would say something, and V2-P3's own gate "
    "records that most first-batch factors being insignificant is the expected result rather "
    "than a failure."
)
"""The direction sentence the four value notes share, held to `REVERSAL_1D_NOTE`'s standard.

Written once because it is one claim about four factors and a copy is a thing that drifts;
`_MOMENTUM_DIRECTION_PROSE` is the precedent and the reason is the same. `V2-P3-017`'s EPcut
concatenates it unchanged, which is the point of writing it once -- and
`tests/unit/test_factor_engine_rules.py::
test_every_shipped_factor_discloses_the_direction_it_declares_and_that_it_is_unmeasured` is the
registry-wide loop that would have caught a twentieth factor shipped without it.
"""

_VALUE_DENOMINATOR_PROSE: Final[str] = (
    " The denominator is daily_basic.total_mv on the newest session knowable at as_of, carried "
    "into yuan by CNY_PER_MARKET_CAP_UNIT -- 10,000, which is measured on this repository's own "
    "stored rows rather than taken from a field list: close * total_share reproduces total_mv on "
    "all four of them to within 2.4e-9 relative, which is the rounding of two columns published "
    "to two and four decimals, while the next candidate reading is out by a factor of 10,000; and "
    "turnover_rate reproduces from float_share only when the share counts are read as "
    "ten-thousands. total_mv and not circ_mv, because a filing describes the whole company "
    "and the restricted fraction circ_mv excludes is not a fact about it. A capitalisation that "
    "is not strictly positive makes the ratio undefined_value rather than a number, and that "
    "code is reachable on a partition this repository's own reader accepts: total_mv is outside "
    "DAILY_BASIC_NULLABLE_COLUMNS, so daily_valuations_from_panel_rows refuses a null in it, and "
    "it is outside DAILY_PRICE_COLUMNS, so nothing requires it to be positive and a stored zero "
    "reads back."
)
"""The denominator sentence all four notes share. One claim about four factors; see
`_VALUE_DIRECTION_PROSE`."""


EARNINGS_YIELD_TTM: Final[FactorDefinition] = FactorDefinition(
    key="earnings_yield_ttm",
    version=1,
    family="value",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN),
        FactorField(dataset=DAILY_BASIC_DATASET, column=MARKET_CAP_COLUMN),
    ),
    lookback_sessions=MARKET_CAP_SESSIONS,
    max_window_sessions=MARKET_CAP_SESSIONS,
    lookback_periods=TRAILING_TWELVE_MONTH_PERIODS,
    max_window_periods=TRAILING_TWELVE_MONTH_PERIODS,
)
"""EP: trailing-twelve-month net profit attributable to the parent, over market capitalisation.

**The first factor this repository ships on both axes at once**, and the first that reads a
filing at all. The reaches are `1 / 1` sessions and `5 / 5` periods, which is the pair the
contract's own docstring was written against -- see `TRAILING_TWELVE_MONTH_PERIODS` for why five
and `MARKET_CAP_SESSIONS` for why one.

**It occupied EPcut's slot for two issues and no longer does, and the handover is the disclosure.**
扣非后盈利收益率 is this factor with the non-recurring gains and losses removed from its numerator,
and until `V2-P3-017` no column of any of the four stored statement projections carried that
number or anything it could be derived from: `income`'s ten are the two revenue lines, cost,
operating and pre-tax profit, tax, net profit at both levels, published EPS and `ebit`, and
`fina_indicator`'s were eleven ratios and per-share figures. That was a boundary of the
*projection* rather than of the upstream, which `V2-P3-009`'s review measured directly rather
than inferring: `fina_indicator` **returns `profit_dedt` itself**. `V2-P3-017` added it as that
projection's twelfth column and `DEDUCTED_EARNINGS_YIELD_TTM` is the factor that reads it, so
this factor is no longer a stand-in for anything.

**What stays true is the reduction, and it is why both ship.** EP is what EPcut reduces to when
the non-recurring part cannot be subtracted, exactly as `RETURN_VOL_60` is what a residual
volatility reduces to when the market return cannot be -- and here the "cannot" is now a
measured frequency rather than a permanent condition: `profit_dedt` lives on the endpoint with
no `update_flag`, no `f_ann_date` and no `report_type`, where its versions disagree on 1.075%
and 0.769% of filings over two disjoint 101-security samples against this column's own 0.189%
and 0.459%. So the security EPcut codes `ambiguous_filing` is the security this factor still
scores, several times as often as the reverse.
`tests/unit/test_factor_value_family.py::
test_ep_is_what_epcut_reduces_to_and_the_two_are_not_the_same_number` holds the pair together,
and `tests/integration/panel/test_value_family.py::
test_a_contradiction_in_the_deducted_profit_codes_epcut_and_leaves_ep_computed`
drives the asymmetry on one build.
"""

EARNINGS_YIELD_TTM_NOTE: Final[FactorNote] = FactorNote(
    subject=EARNINGS_YIELD_TTM.qualified_key,
    summary=(
        "EP: the trailing twelve months of income.n_income_attr_p divided by market "
        "capitalisation. A-share statements are cumulative within the calendar fiscal year, so "
        "the numerator is cumulative[latest] + cumulative[the December inside the window] - "
        "cumulative[the same quarter one year earlier], read off a window of five contiguous "
        "report periods; max_window_periods equals lookback_periods, which is the contract's own "
        "statement that no filing is missing inside it and is what makes that index arithmetic "
        "arithmetic rather than a guess. n_income_attr_p and not n_income, because total_mv "
        "prices the parent's shares and the minority interest is not a claim any holder of one "
        "has -- 600739.SH's 2024 annual gives 664,195,391.66 against 209,556,865.25 for the same "
        "period, a factor of 3.2. A negative trailing profit is computed and negative rather than "
        "undefined_value, which is the whole reason this is a yield and not a multiple: E/P is "
        "monotone through zero and P/E is not, which is why daily_basic.pe_ttm is simply null for "
        "a loss-maker on 1,214 of 5,338 rows and why this factor does not invert it. This is NOT "
        "EPcut and it held EPcut's slot until V2-P3-017: the deducted-profit number was in none "
        "of the four stored statement projections while fina_indicator served profit_dedt itself, "
        "so that was a projection boundary and not an upstream absence, and V2-P3-017 added the "
        "column and shipped deducted_earnings_yield_ttm on it. EP is what EPcut reduces to when "
        "the non-recurring term cannot be subtracted, and how often that is has been measured "
        "rather than assumed: profit_dedt is published only on the endpoint with no update_flag, "
        "no f_ann_date and no report_type, where its versions disagree on 1.075% and 0.769% of "
        "filings over two disjoint 101-security samples against 0.189% and 0.459% for this "
        "factor's own column. So both ship, and the security EPcut cannot answer for is usually "
        "one this factor can. The cost of the five-period reach is stated: a security with fewer "
        "than five contiguous filings is insufficient_history here "
        "and computed for book_to_price." + _VALUE_DENOMINATOR_PROSE + _VALUE_DIRECTION_PROSE
    ),
)
"""`EARNINGS_YIELD_TTM`'s prose, out of `factor_id`. See `domain/factor.py::FactorNote`."""


def _earnings_yield_ttm(window: FactorWindow) -> float | None:
    """`EARNINGS_YIELD_TTM`: trailing net profit over capitalisation, both in yuan."""
    return _yield_on_market_capitalisation(window, dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN)


BOOK_TO_PRICE: Final[FactorDefinition] = FactorDefinition(
    key="book_to_price",
    version=1,
    family="value",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=BALANCE_SHEET_DATASET, column=BOOK_EQUITY_COLUMN),
        FactorField(dataset=DAILY_BASIC_DATASET, column=MARKET_CAP_COLUMN),
    ),
    lookback_sessions=MARKET_CAP_SESSIONS,
    max_window_sessions=MARKET_CAP_SESSIONS,
    lookback_periods=1,
    max_window_periods=1,
)
"""BP: owners' equity excluding minority interest at the latest knowable period, over market cap.

**One period, and the `1 / 1` pair is a statement rather than a default.** A balance sheet line is
a *stock*: the figure at 30 September already is the book value at 30 September, so there is
nothing to accumulate and nothing to difference, and a five-period reach here would refuse
securities for want of history the arithmetic never touches. The contract still requires the span
bound, and at a count of one it can never bind -- one of a security's own periods spans exactly
one fiscal quarter -- so `1` says "the latest filing" without implying a tolerance.

The consequence is the coverage difference this family is asked about and does not hide: on the
same cross section `book_to_price` scores every security with one filing and the two
trailing-twelve-month factors score only those with five contiguous ones.
`tests/integration/panel/test_value_family.py::
test_the_one_period_reach_and_the_five_period_reach_answer_differently_for_the_same_security` is
that difference on one partition rather than in prose.
"""

BOOK_TO_PRICE_NOTE: Final[FactorNote] = FactorNote(
    subject=BOOK_TO_PRICE.qualified_key,
    summary=(
        "BP: balancesheet.total_hldr_eqy_exc_min_int at the latest report period knowable at "
        "as_of, divided by market capitalisation. One period and not five, because a balance "
        "sheet line is a stock and not a cumulative flow -- there is nothing to accumulate, so a "
        "wider reach would refuse securities for history the arithmetic never reads. The "
        "numerator is the stored equity column and NOT fina_indicator.bps times a share count: "
        "the two versions of 603049.SH's 2024 annual give bps as 22.2055 and as 2.2206 under one "
        "ann_date on an endpoint with no update_flag, no f_ann_date and no report_type, and the "
        "share count would have had to come from balancesheet.total_share, which is that "
        "endpoint's most-disagreed field at 34 of its 43 refused reads. Nor is it total_assets "
        "minus total_liab, which is the consolidated equity and includes the minority interest "
        "total_mv does not price -- on 000002.SZ's 2023H1 rows that difference is "
        "402,644,482,157.24 against a stored 249,326,669,106.12, 61% larger -- and which reads "
        "two columns where one answers. The column chosen is not immune and this repository holds "
        "the counterexample: 002538.SZ's 2022 annual is three versions giving 5,346,322,691.02, "
        "5,297,379,808.74 and 5,150,097,232.47, a 3.8% spread, and a factor read of it refuses "
        "rather than picking. A negative book value -- an insolvent issuer -- is computed and "
        "negative, on the same monotonicity argument earnings_yield_ttm makes for a loss."
        + _VALUE_DENOMINATOR_PROSE
        + _VALUE_DIRECTION_PROSE
    ),
)
"""`BOOK_TO_PRICE`'s prose, out of `factor_id`."""


def _book_to_price(window: FactorWindow) -> float | None:
    """`BOOK_TO_PRICE`: the latest knowable book equity over capitalisation, both in yuan.

    No trailing sum: `window.periods` holds one period and `[-1]` is it. A negative book value is
    a real answer for an insolvent issuer and is `computed`; the only `None` is the non-positive
    capitalisation `_market_capitalisation` guards.
    """
    capitalisation = _market_capitalisation(window)
    if capitalisation is None:
        return None
    return window.series(BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN)[-1] / capitalisation


SALES_YIELD_TTM: Final[FactorDefinition] = FactorDefinition(
    key="sales_yield_ttm",
    version=1,
    family="value",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN),
        FactorField(dataset=DAILY_BASIC_DATASET, column=MARKET_CAP_COLUMN),
    ),
    lookback_sessions=MARKET_CAP_SESSIONS,
    max_window_sessions=MARKET_CAP_SESSIONS,
    lookback_periods=TRAILING_TWELVE_MONTH_PERIODS,
    max_window_periods=TRAILING_TWELVE_MONTH_PERIODS,
)
"""SP: trailing-twelve-month total operating revenue over market capitalisation.

`EARNINGS_YIELD_TTM` with one column changed, which is the whole of the difference and is why the
two share `_yield_on_market_capitalisation`. It carries the family's coverage where EP does not:
a revenue line is positive for almost every going concern, so a cross section that is a fifth
loss-making has a full SP and a signed EP -- which is a reason to ship both rather than a reason
to prefer either. See `TOTAL_REVENUE_COLUMN` for why the top line and not `revenue`.
"""

SALES_YIELD_TTM_NOTE: Final[FactorNote] = FactorNote(
    subject=SALES_YIELD_TTM.qualified_key,
    summary=(
        "SP: the trailing twelve months of income.total_revenue divided by market "
        "capitalisation, on earnings_yield_ttm's terms throughout -- the same cumulative-to-TTM "
        "identity over the same five contiguous report periods, the same denominator, the same "
        "sign convention. total_revenue and not revenue: the first is the topmost line of the CAS "
        "income statement and the second is one of the lines it totals, so the top line is the "
        "inclusive one and the narrower column drops whatever an issuer reports above its "
        "operating revenue. Where the two part is measured rather than argued, and it is NOT "
        "where the shape of the statement puts it: over the whole history of 14 banks, insurers "
        "and brokers the two columns carry the same number on all 1,350 rows (comp_type 2 / 3 / "
        "4, zero differences), while on a 47-name comp_type=1 industrial sample they differ on 37 "
        "of 1,468 -- 600519.SH on ALL 42 of its stored periods, 123,122,542,625 of total_revenue "
        "against 120,776,131,875 of revenue at 2024Q3, 1.94% of revenue that the narrower column "
        "does not carry. Neither column is one of income's concentrated ones: ebit is 258 of that "
        "dataset's 288 refused field reads on the 53-security corpus, and total_revenue's OWN "
        "refusal rate was measured directly rather than bounded, at 13 of 10,567 filings "
        "(0.123%) over two disjoint 91-security samples. A 600739.SH-shaped filing, whose two "
        "rows both carry update_flag=1 and disagree about revenue by 4.6% while agreeing about "
        "n_income_attr_p, refuses this factor's read and not earnings_yield_ttm's, which is what "
        "per-field refusal means on a real pair of rows."
        + _VALUE_DENOMINATOR_PROSE
        + _VALUE_DIRECTION_PROSE
    ),
)
"""`SALES_YIELD_TTM`'s prose, out of `factor_id`."""


def _sales_yield_ttm(window: FactorWindow) -> float | None:
    """`SALES_YIELD_TTM`: trailing total revenue over capitalisation, both in yuan."""
    return _yield_on_market_capitalisation(
        window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN
    )


# --- `V2-P3-017`: the value family's fourth member, and the first read of `fina_indicator` -----
#
# See this module's docstring section "The value family" for the judgements this factor shares
# with the three above it, and `DEDUCTED_NET_PROFIT_COLUMN` for the two that are its own.


DEDUCTED_NET_PROFIT_COLUMN: Final[str] = "profit_dedt"
"""EPcut's numerator: 扣除非经常性损益后的净利润, cumulative, in yuan, on `fina_indicator`.

**Which endpoint is not a choice.** `income` does not serve this number, and `V2-P3-017`
measured that on all four statement endpoints on 2026-08-17 rather than inferring it: asked for
every field they have, `income` answers with 85 names, `balancesheet` 152, `cashflow` 97 and
`fina_indicator` 108, and the deducted family appears in exactly one of those four lists. Naming
it in `income`'s projection would not widen that read -- the endpoint answers a request for it
with the columns it does serve and drops the name silently, so
`providers/tushare.py::_response_rows` would refuse **every** `income` fetch by
`checked_response_fields`. See `domain/financial_statements.FINANCIAL_INDICATOR_DATA_COLUMNS`.

**The attributable level, and that is measured rather than read off a field name.** The question
is the one `NET_PROFIT_COLUMN` answers for EP -- deducted profit *attributable to the parent's
owners*, or deducted profit for the whole consolidated entity -- and the two are far apart on
exactly the security this repository already uses to separate them. `600739.SH`'s 2024 annual
gives `n_income` 664,195,391.66 against `n_income_attr_p` 209,556,865.25, a factor of 3.169; the
same filing's `profit_dedt` is **218,927,918.51**, which is 1.045x the attributable figure and
0.330x the consolidated one. Over six securities chosen for large minority interests --
`600739.SH`, `000002.SZ`, `601318.SH`, `000001.SZ`, `600519.SH`, `600030.SH`, four periods each
-- `profit_dedt / n_income_attr_p` stays in `[0.917, 1.120]` while `profit_dedt / n_income`
tracks one minus the minority share (0.330 to 1.067). So the pair `(profit_dedt, total_mv)`
covers one claim on both sides, which is `NET_PROFIT_COLUMN`'s rule reaching the same answer on
a different endpoint. `tests/unit/test_factor_value_family.py::
test_the_deducted_profit_is_the_attributable_level_and_not_the_consolidated_one` is that
measurement.

**It is a cumulative sum and not a ratio, which is what lets the TTM identity touch it.**
`RETURN_ON_EQUITY_TTM` refuses `fina_indicator.roe` because the cumulative-to-TTM identity is an
identity about *sums* and a published ratio is not a sum. That argument does not reach this
column: it is denominated in yuan and it accumulates within the calendar fiscal year exactly as
`income`'s flows do. `600519.SH`'s 2018 filings are 8,510,778,903.45 at Q1, 15,884,168,512.98 at
H1, 24,929,011,158.67 at Q3 and 35,585,443,648.60 at the annual -- one accumulation curve, and
the differences 7.37bn / 9.04bn / 10.66bn are the individual quarters. That is the same shape
`TRAILING_TWELVE_MONTH_PERIODS` states and the opposite of `roe`'s 10.5688 / 19.2038 / 26.833 /
38.4283, which is a *rate* rising with the same accumulation and cannot be differenced.

**Its own refusal rate is measured and is several times EP's.** On two disjoint 101-security
samples taken by stride over the whole listed universe on 2026-08-17, the surviving versions of a
filing disagree about this column **66 of 6,138 filings (1.075%)** and **46 of 5,980 (0.769%)**
-- combined **112 of 12,118, 0.924%**. `income.n_income_attr_p`, EP's numerator, has been
measured at 0.189% and at 0.459% on two earlier samples. Neither pair of numbers travels (the two
here differ by 1.4x between themselves, which is this repository's own sentence about a rate being
a property of its sample landing again), but the ordering is the same on every one of them and
the reason is structural rather than statistical: `fina_indicator` carries no `update_flag`, no
`f_ann_date` and no `report_type`, 81.7% of its keys carry more than one row, and nothing in the
response orders them. EPcut is therefore genuinely harder to read than EP, and both ship so a
caller can see the difference rather than be told about it; see
`KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
.the_deducted_profit_is_only_on_the_endpoint_with_no_version_column`.
"""


DEDUCTED_EARNINGS_YIELD_TTM: Final[FactorDefinition] = FactorDefinition(
    key="deducted_earnings_yield_ttm",
    version=1,
    family="value",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=FINANCIAL_INDICATOR_DATASET, column=DEDUCTED_NET_PROFIT_COLUMN),
        FactorField(dataset=DAILY_BASIC_DATASET, column=MARKET_CAP_COLUMN),
    ),
    lookback_sessions=MARKET_CAP_SESSIONS,
    max_window_sessions=MARKET_CAP_SESSIONS,
    lookback_periods=TRAILING_TWELVE_MONTH_PERIODS,
    max_window_periods=TRAILING_TWELVE_MONTH_PERIODS,
)
"""EPcut: trailing-twelve-month deducted net profit attributable to the parent, over market cap.

`EARNINGS_YIELD_TTM` with one `(dataset, column)` changed, which is the whole of the difference
and is why the two share `_yield_on_market_capitalisation` -- the same identity over the same
five contiguous report periods, the same denominator, the same sign convention. The reaches are
`1 / 1` sessions and `5 / 5` periods for `TRAILING_TWELVE_MONTH_PERIODS`' and
`MARKET_CAP_SESSIONS`' reasons, restated for no other factor and not restated here either.

**The first shipped factor that reads `fina_indicator`, and that is the cost rather than an
incidental.** The other three value factors read `income` and `balancesheet`, which carry
`update_flag` and `f_ann_date`; this one reads the endpoint that carries neither. What that buys
is the only stored column from which a deducted-profit yield can be computed at all
(`DEDUCTED_NET_PROFIT_COLUMN` measures both halves of that sentence); what it costs is the
ambiguity rate of that endpoint, which is why this factor and `earnings_yield_ttm` both ship
instead of one replacing the other. `EARNINGS_YIELD_TTM` is what this factor reduces to when the
non-recurring term cannot be subtracted, and until `V2-P3-017` it was the *only* thing this
repository could offer for the slot.

**It is not `earnings_yield_ttm` renamed, and a fixture proves that rather than a docstring.**
The two share a denominator and differ in one numerator, which is precisely the shape in which
an assertion stops discriminating -- this repository's own recurring lesson. So the value
family's partition carries a `profit_dedt` series that is neither `n_income_attr_p` nor a
multiple of it in the way any neighbouring column is, and
`tests/integration/panel/test_value_family.py::
test_the_four_factors_compute_off_two_axes_and_give_four_different_numbers` asserts all four
values pairwise distinct on one build.
"""

DEDUCTED_EARNINGS_YIELD_TTM_NOTE: Final[FactorNote] = FactorNote(
    subject=DEDUCTED_EARNINGS_YIELD_TTM.qualified_key,
    summary=(
        "EPcut: the trailing twelve months of fina_indicator.profit_dedt -- net profit after "
        "non-recurring gains and losses are deducted -- divided by market capitalisation, on "
        "earnings_yield_ttm's terms throughout: the same cumulative-to-TTM identity over the "
        "same five contiguous report periods, the same denominator, the same sign convention, "
        "and max_window_periods equal to lookback_periods so that no filing is missing inside "
        "the window. The column lives on fina_indicator because income does not serve it: "
        "measured on 2026-08-17 by asking all four statement endpoints for every field they "
        "have, the deducted family appears in exactly one of the four lists (85 / 152 / 97 / "
        "108 names), and an income request that names profit_dedt comes back silently without "
        "it. It is the ATTRIBUTABLE level "
        "and that is measured rather than taken from the field name -- 600739.SH's 2024 annual "
        "gives n_income 664,195,391.66 against n_income_attr_p 209,556,865.25, a factor of "
        "3.169, and profit_dedt for the same filing is 218,927,918.51, which is 1.045x the "
        "attributable figure and 0.330x the consolidated one; over six securities with large "
        "minority interests the ratio to n_income_attr_p stays inside [0.917, 1.120]. So this "
        "numerator and total_mv cover one claim, which is the pair earnings_yield_ttm chooses "
        "n_income_attr_p for. The TTM identity applies because profit_dedt is a cumulative SUM "
        "in yuan and not a published ratio -- 600519.SH's 2018 filings run 8,510,778,903.45 at "
        "Q1, 15,884,168,512.98 at H1, 24,929,011,158.67 at Q3 and 35,585,443,648.60 at the "
        "annual -- which is exactly the property return_on_equity_ttm found fina_indicator.roe "
        "lacks. THE COST IS THE ENDPOINT: fina_indicator carries no update_flag, no f_ann_date "
        "and no report_type, 81.7% of its keys carry more than one row, and this column's own "
        "measured disagreement rate is 66 of 6,138 filings (1.075%) on one 101-security sample "
        "and 46 of 5,980 (0.769%) on a disjoint one, against 0.189% and 0.459% measured for "
        "income.n_income_attr_p. So EPcut is ambiguous_filing where EP is computed, more often, "
        "and earnings_yield_ttm ships beside it rather than being replaced by it: EP is what "
        "this factor reduces to when the non-recurring term cannot be subtracted. A negative "
        "trailing deducted profit is computed and negative, not undefined_value, for "
        "earnings_yield_ttm's monotonicity reason; a security with fewer than five contiguous "
        "fina_indicator filings is insufficient_history here and computed for book_to_price."
        + _VALUE_DENOMINATOR_PROSE
        + _VALUE_DIRECTION_PROSE
    ),
)
"""`DEDUCTED_EARNINGS_YIELD_TTM`'s prose, out of `factor_id`."""


def _deducted_earnings_yield_ttm(window: FactorWindow) -> float | None:
    """`DEDUCTED_EARNINGS_YIELD_TTM`: trailing deducted profit over capitalisation, in yuan."""
    return _yield_on_market_capitalisation(
        window, dataset=FINANCIAL_INDICATOR_DATASET, column=DEDUCTED_NET_PROFIT_COLUMN
    )


# --- `V2-P3-010`: the quality family -----------------------------------------------------------
#
# See this module's docstring section "The quality family" for the five judgements every factor
# here shares -- the single axis, the mixed stock-and-flow shape, the sign rule a denominator
# gets that a numerator does not, why nothing here reads `fina_indicator`, and what the two
# missing add-backs cost.


CONSOLIDATED_NET_PROFIT_COLUMN: Final[str] = "n_income"
"""`income`'s net profit for the **whole consolidated entity**, minority interest included.

`NET_PROFIT_COLUMN` is the other one and the choice between them is made per factor by what the
denominator covers, which is the same rule `EARNINGS_YIELD_TTM` applies against `total_mv` and
the opposite answer:

- `RETURN_ON_EQUITY_TTM` divides by `total_hldr_eqy_exc_min_int`, the equity **excluding**
  minority interest, so its numerator is `n_income_attr_p`. Both halves cover the parent's
  owners.
- `RETURN_ON_CAPITAL_TTM` divides by capital employed, which is every provider of long-term
  finance -- the parent's owners, the minority holders and the non-current creditors -- so its
  numerator is the consolidated figure. Pairing `n_income_attr_p` with capital employed would put
  one claim over a base that finances three.
- `ACCRUALS_TTM` subtracts `cashflow.n_cashflow_act`, which is the whole entity's operating cash,
  and divides by `total_assets`, which is the whole entity's assets. A consolidated cash flow
  minus an attributable profit is a difference between two different reporting boundaries.

They are different numbers on real rows and this repository already holds the counterexample:
`600739.SH`'s 2024 annual gives `n_income` 664,195,391.66 against `n_income_attr_p`
209,556,865.25, a factor of 3.2.
"""

TOTAL_ASSETS_COLUMN: Final[str] = "total_assets"
"""`balancesheet`'s asset total: `ACCRUALS_TTM`'s scaler and half of capital employed.

**Its recorded refusal count is `0` and its measured one is not, which is why it was
re-measured.** `domain/financial_statements.py` records this column losing 0 reads in the
53-security corpus and **18** in an independent 76-security probe taken the same day, and says in
its own voice that "the `0` is the one to distrust". `V2-P3-010`'s own probe read it back over two
disjoint samples at 16 of 5,250 and 19 of 5,143, **35 of 10,393 filings (0.337%)** -- level with
`total_hldr_eqy_exc_min_int` and behind only `total_share`'s 57. So the zero was a sample property
for the third recorded time, and the number that justifies reading this column is the measured
one. See this module's docstring section "The quality family" for the whole table.
"""

CURRENT_LIABILITIES_COLUMN: Final[str] = "total_cur_liab"
"""`balancesheet`'s current-liability total, subtracted from assets to give capital employed.

The one column of the three this family reads from `balancesheet` that a **financial** issuer does
not publish: a bank's balance sheet is not split into current and non-current, so the cell is
null and `_complete_series` answers `input_missing` rather than inventing a split. Measured over
every stored period since 2015 -- null on 68 of 68 for `000001.SZ`, 67 of 67 for `601318.SH` and
64 of 64 for `600030.SH`, whose newest non-null periods are 2006-03-31, 2006-09-30 and
2006-09-30, against 0 of 62 and 0 of 63 for two `comp_type=1` industrials. So
`RETURN_ON_CAPITAL_TTM` is blind to banks, insurers and brokers by construction, and its own
docstring states that as a coverage fact rather than leaving it to be discovered.

Its disagreement rate is the *lowest* of the three `balancesheet` columns this family reads: **14
of 10,393 filings (0.135%)** over `V2-P3-010`'s two disjoint samples, against 35 apiece for
`total_assets` and `total_hldr_eqy_exc_min_int`. So the second column `RETURN_ON_CAPITAL_TTM`
reads from this endpoint costs it little; what it costs is the company types above.
"""

OPERATING_COST_COLUMN: Final[str] = "oper_cost"
"""`income`'s cost of sales, 营业成本 -- the term a gross margin subtracts from the top line.

Paired with `TOTAL_REVENUE_COLUMN` and not with `revenue`, because the two are the two halves of
one published subtotal: 营业总收入 less 营业总成本 is the CAS operating result, and 营业收入 less
营业成本 is the narrower pair. `V2-P3-009`'s review measured where the two revenue columns part --
37 of 1,468 `comp_type=1` rows, `600519.SH` on all 42 of its stored periods -- so mixing one
column of each pair is a margin wrong by up to 1.94% of revenue on a real issuer. The top line is
kept for `SALES_YIELD_TTM`'s reason, and the cost line is the only one this projection carries.

**Null for a financial issuer**, which is a fact this repository already recorded in its own
fixtures: `tests/contract/providers/test_tushare_financials.py` notes that `000001.SZ`'s
`oper_cost` is null on both of its rows "because a bank publishes no cost of sales". `V2-P3-010`
measured how complete that is over every stored period since 2015 -- null on 59 of 59 for
`000001.SZ`, 57 of 57 for `601318.SH` and 56 of 56 for `600030.SH`, against 0 of 60 and 0 of 60
for two `comp_type=1` industrials. So `GROSS_MARGIN_STABILITY` is `input_missing` for the company
types whose gross margin is not a defined quantity, which is the right answer rather than a gap.

**And it is the most-disagreed of `income`'s non-`ebit` columns**, which matters here more than
elsewhere because this factor reads eight contiguous filings rather than five: **25 of 10,595
filings (0.236%)** over `V2-P3-010`'s two disjoint samples, against `total_revenue`'s 20 and
`n_income_attr_p`'s 24. It is the higher of the two columns this factor reads, and the reach it is
read at is the widest **report-period** one in the build.
"""

OPERATING_CASH_FLOW_COLUMN: Final[str] = "n_cashflow_act"
"""`cashflow`'s net cash from operating activities: the cash half of an accrual.

**This is the one column in the family whose recorded refusal count is exactly `0`, and that is
the reason it was re-measured rather than quoted.** `domain/financial_statements.py` records
`cashflow` as the worst of the four endpoints by ambiguous filings -- 450 of 2,849, 15.80% -- and
records **all 450** of that endpoint's refused field reads as `free_cashflow`'s, which leaves this
column at zero over 14,245 field reads. That module's own sentence about such a number is
"`revenue`, `total_assets` and `roe` lose 5, 0 and 5 reads in the 53-security corpus and 6, 18 and
33 in an independent 76-security probe... the `0` is the one to distrust", and `total_assets` is
the column that moved from `0` to 18.

**Measured, it is not zero.** `V2-P3-010`'s probe read `cashflow` back over two disjoint samples
at 9,602 filings, of which **1,643 (17.11%)** are ambiguous -- worse than the 15.80% recorded --
and this column disagrees on **5 of 9,602 (0.052%)**, 1 in the first sample and 4 in the second.
So the zero was a sample property again and the real figure is small rather than absent, which is
a different claim and the one this factor is entitled to make.

`free_cashflow` is refused outright and needs no argument beyond the census: it is 450 of 450 as
recorded and **1,643 of 1,643** as measured -- every ambiguous `cashflow` filing in 185 securities
disagrees about it -- with a worst case of `+316,026,934` against `-294,173,456` for one filing of
`300002.SZ`.
"""

CAPITAL_TURNOVER_PERIODS: Final[int] = TRAILING_TWELVE_MONTH_PERIODS
"""The reach every flow-over-stock factor here declares: `TRAILING_TWELVE_MONTH_PERIODS`.

Named rather than reused literally because the two constants answer different questions that
happen to have the same answer. `TRAILING_TWELVE_MONTH_PERIODS` is "how many contiguous filings
does the cumulative-to-TTM identity need"; this is "how many does a return-on-capital ratio need",
and the answer is the same five because the *numerator* is that identity and the denominator is
one stored cell taken at `[-1]` of the same window. Binding them keeps a future change to the
identity's reach from silently leaving these three behind at a width the numerator no longer has.

**The denominator is the window's last period and not an average of its ends**, which is a
judgement rather than a default and it is the one place this family departs from the textbook.
The textbook ROE divides by *average* equity, and the average is available here -- `window[0]` is
the same quarter one year earlier by construction. It is not taken, for a reason that is a
property of this repository rather than of accounting: `earnings_yield_ttm` and `book_to_price`
already ship, they divide the same trailing profit and the same closing equity by the same market
capitalisation, and so `EP / BP` **is** this factor, exactly, with the capitalisation cancelling.
An averaged denominator would make the repository carry two incompatible statements of what a
book value is, and the one that is already shipped is the one three stored factors would have to
be restated to change.
`tests/integration/panel/test_quality_family.py::
test_the_return_on_equity_is_the_earnings_yield_over_the_book_to_price` holds the identity through
the real engine over one corpus, so it is a measurement rather than a remark.

The cost is stated rather than absorbed: closing equity exceeds average equity for an issuer that
raised capital during the year, so this ROE reads **lower** than the textbook one for exactly the
names that grew their equity fastest, and higher for a buyback. That is a bias with a direction,
which is what makes it a disclosure rather than a rounding.
"""

GROSS_MARGIN_PERIODS: Final[int] = 8
"""How many contiguous filings a gross-margin stability needs: eight, and the width is derived.

**A stability is a dispersion of something, and the something has to be comparable across the
window** -- which is where the value family's whole argument lands again, one level up. Three
candidate somethings, and only the third survives:

- **The cumulative margin as filed.** `(total_revenue - oper_cost) / total_revenue` off each
  stored row is a *ratio* of two figures accumulated over the same span, so unlike a cumulative
  level it is already scale-free and needs no repair. What it is not is horizon-free: a Q1 row is
  a three-month margin and a Q3 row a nine-month one, so their dispersion measures the seasonal
  shape of the year at least as much as it measures instability.
- **The single-quarter margin**, recovered by differencing consecutive cumulative rows. It needs
  only five periods and it is a genuine quarterly margin -- and its dispersion is dominated by
  seasonality by construction: a retailer whose fourth quarter is its best would score as
  unstable every year of its life, which is a fact about the calendar rather than about the
  business.
- **The trailing-twelve-month margin**, which is what this factor takes. Every observation spans
  a full year, so the seasonal term is in all of them equally and cancels out of their dispersion.

The width follows from that choice and from one arithmetic fact rather than from a preference:
a trailing-twelve-month figure costs `TRAILING_TWELVE_MONTH_PERIODS` contiguous filings, and `k`
of them ending at consecutive quarter ends cost `TRAILING_TWELVE_MONTH_PERIODS + k - 1`. At
`k = GROSS_MARGIN_OBSERVATIONS` that is eight, about two years of disclosure -- the widest
**report-period** reach this build declares, against `CAPITAL_TURNOVER_PERIODS`' five and
`BOOK_TO_PRICE`'s one, and the coverage price is paid by every young issuer. It is not the widest
reach in the build: `MOMENTUM_120_SESSIONS` counts 125 sessions on the other axis, and the two
numbers are not comparable because the axes are not.

**This is where `_trailing_twelve_months` could not be reused, and the failure mode is worse than
`V2-P3-009` recorded.** The identity finds the fiscal year end inside `periods[:-1]` and answers
`None` unless there is exactly one, which `V2-P3-009` names as the branch a gapped five-period
window reaches. `periods[:-1]` of a contiguous **eight** is seven quarters, and seven consecutive
quarters hold **one or two** Decembers depending on which quarter the window ends at -- so handing
the identity a whole eight is not fail-closed:

| window ends at | Decembers in `periods[:-1]` | `_trailing_twelve_month_sum` |
|---|---:|---|
| Q1, Q2 or Q3 | 2 | `None` -- refuses |
| **Q4** | **1** | **a number, and the number is wrong** |

At a December ending period the search finds the December of the year *before* the one the
identity names, and the expression becomes `cumulative[-1] + cumulative[that earlier December] -
cumulative[the window's first quarter]` -- the true trailing twelve months **plus a whole extra
fiscal year less that year's first quarter**. That is the shape this repository books Criticals
for: a guard that looks fail-closed and answers confidently on one quarter in four, with an error
of the same order as the answer. So the identity is applied to five-period *slices* of the eight
rather than to
the eight, and each slice's own `[:-1]` is four **consecutive** quarters, of which exactly one
ends a year at every alignment.
`tests/unit/test_factor_quality_family.py::
test_every_slice_of_a_contiguous_eight_period_window_holds_exactly_one_fiscal_year_end` is that
claim as an enumeration over all four slices at all four quarter ends, and
`test_the_whole_eight_period_window_is_no_trailing_year_and_does_not_always_refuse`
is the table above, including the wrong number.
"""

GROSS_MARGIN_OBSERVATIONS: Final[int] = 4
"""How many trailing-twelve-month margins the dispersion is taken over: four.

Four consecutive quarter ends, so the number this factor reports is how far the trailing-year
gross margin moved over the **most recent year** -- a one-year drift rather than a multi-year
regime. Two things make four the number rather than a round one:

- `_sample_stdev` is Bessel-corrected, so `k` observations carry `k - 1` degrees of freedom and
  `k = 2` would be a scaled absolute difference wearing a standard deviation's name. Four is the
  smallest count at which the estimator is doing what its own docstring says.
- Each extra observation costs one more contiguous filing, and every one of them is a
  `(filing, column)` read that can cost this security its value. It used to cost the **whole
  build**; `V2-P3-018` made it `ambiguous_filing` for the one security, which changes the price
  and not the direction. At `income`'s ambiguous-filing rate -- 8.15% recorded and **8.51%** on
  `V2-P3-010`'s own two-sample probe -- width is not free, and a reach that read three years of
  margins would be a coverage decision made for a smoother statistic.

The four are **overlapping**: consecutive trailing-twelve-month windows share three of their four
quarters, so the series is autocorrelated by construction and its dispersion is a drift measure
rather than four independent draws. That is a property of what is being measured -- "how much did
the trailing-year margin move" -- and not a defect to be corrected by thinning, which at this
width would leave one observation.
"""


def _capital_denominator(value: float) -> float | None:
    """A stock quantity a flow is divided by, or `None` when it is not strictly positive.

    One guard for the family's three ratios, and it is the **opposite** of the rule
    `BOOK_TO_PRICE` applies to the same `balancesheet` column -- deliberately, because the rule
    follows the position and not the column. An insolvent issuer's negative equity is a real
    numerator over a positive price and `book_to_price` reports it as `computed` and negative; the
    same negative equity as a *denominator* turns a profit into a negative return on equity and a
    loss into a positive one, so the ordering a cross section is built on would invert for exactly
    the names in the worst condition. `_market_capitalisation` refuses a non-positive denominator
    for that reason and this is the same refusal on the other axis.

    Non-positive rather than zero, for the same reason: zero is the case that raises `ZeroDivision`
    and negative is the case that answers confidently and wrongly.
    `tests/unit/test_factor_quality_family.py::
    test_a_negative_equity_is_a_numerator_for_book_to_price_and_a_refusal_for_roe`
    drives both halves on one number, which is the only way to state that the two rules are a
    choice rather than an inconsistency.
    """
    if value <= 0.0:
        return None
    return value


def _return_on_equity_ttm(window: FactorWindow) -> float | None:
    """`RETURN_ON_EQUITY_TTM`: trailing profit over the closing equity, both in yuan."""
    equity = _capital_denominator(window.series(BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN)[-1])
    if equity is None:
        return None
    profit = _trailing_twelve_months(window, dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN)
    if profit is None:
        return None
    return profit / equity


def _return_on_capital_ttm(window: FactorWindow) -> float | None:
    """`RETURN_ON_CAPITAL_TTM`: trailing consolidated profit over capital employed.

    Capital employed is `total_assets - total_cur_liab`, read at `[-1]` of the same window the
    numerator is summed over. See `RETURN_ON_CAPITAL_TTM` for why that subtraction is the
    denominator this projection can actually state, and for what the missing interest add-back
    costs the numerator.
    """
    assets = window.series(BALANCE_SHEET_DATASET, TOTAL_ASSETS_COLUMN)[-1]
    current_liabilities = window.series(BALANCE_SHEET_DATASET, CURRENT_LIABILITIES_COLUMN)[-1]
    employed = _capital_denominator(assets - current_liabilities)
    if employed is None:
        return None
    profit = _trailing_twelve_months(
        window, dataset=INCOME_DATASET, column=CONSOLIDATED_NET_PROFIT_COLUMN
    )
    if profit is None:
        return None
    return profit / employed


def _gross_margin_stability(window: FactorWindow) -> float | None:
    """The sample standard deviation of four trailing-twelve-month gross margins.

    The identity is applied to five-period slices of the eight-period window rather than to the
    window, which is what keeps every search for a fiscal year end a search over four *consecutive*
    quarters -- see `GROSS_MARGIN_PERIODS`. Each slice yields one trailing revenue and one trailing
    cost, and their margin is `(revenue - cost) / revenue`.

    `None` -- hence `undefined_value` -- when any slice's trailing revenue is not strictly
    positive, which is `_capital_denominator`'s argument on a flow: a zero divides and a negative
    inverts the margin's sign, and a cross section that ranked an inverted margin beside ordinary
    ones would order the two backwards. A slice whose periods hold no single fiscal year end is
    the same answer, and at the declared `8 / 8` reach it is unreachable -- the branch is kept for
    `_sample_stdev`'s stated reason and driven directly in
    `tests/unit/test_factor_quality_family.py::
    test_a_gapped_eight_period_window_has_no_margin_series_rather_than_a_short_one`.
    """
    periods = window.periods
    revenue = window.series(INCOME_DATASET, TOTAL_REVENUE_COLUMN)
    cost = window.series(INCOME_DATASET, OPERATING_COST_COLUMN)
    margins: list[float] = []
    for start in range(len(periods) - TRAILING_TWELVE_MONTH_PERIODS + 1):
        stop = start + TRAILING_TWELVE_MONTH_PERIODS
        span = periods[start:stop]
        trailing_revenue = _trailing_twelve_month_sum(span, revenue[start:stop])
        trailing_cost = _trailing_twelve_month_sum(span, cost[start:stop])
        if trailing_revenue is None or trailing_cost is None or trailing_revenue <= 0.0:
            return None
        margins.append((trailing_revenue - trailing_cost) / trailing_revenue)
    return _sample_stdev(margins)


def _accruals_ttm(window: FactorWindow) -> float | None:
    """`ACCRUALS_TTM`: the trailing profit that did not arrive as operating cash, over assets.

    Both trailing sums are taken over the same window and the scaler is the same window's closing
    `total_assets`, so the three terms are one security's own statements at one point in time.
    """
    assets = _capital_denominator(window.series(BALANCE_SHEET_DATASET, TOTAL_ASSETS_COLUMN)[-1])
    if assets is None:
        return None
    profit = _trailing_twelve_months(
        window, dataset=INCOME_DATASET, column=CONSOLIDATED_NET_PROFIT_COLUMN
    )
    operating_cash = _trailing_twelve_months(
        window, dataset=CASH_FLOW_DATASET, column=OPERATING_CASH_FLOW_COLUMN
    )
    if profit is None or operating_cash is None:
        return None
    return (profit - operating_cash) / assets


_QUALITY_UNMEASURED_DIRECTION_PROSE: Final[str] = (
    " The declared direction is the quality premium's conventional prior and this repository has "
    "measured nothing whatever about it, on this factor or on any other. V2-P3-005 is where an "
    "information coefficient would say something, and V2-P3's own gate records that most "
    "first-batch factors being insignificant is the expected result rather than a failure."
)
"""The direction sentence the four quality notes share, held to `REVERSAL_1D_NOTE`'s standard.

Written once because it is one claim about four factors and a copy is a thing that drifts;
`_VALUE_DIRECTION_PROSE` is the precedent and the reason is the same.
"""

_QUALITY_AXIS_PROSE: Final[str] = (
    " This factor is on the report-period axis and nothing else: it declares no session reach, "
    "which FactorDefinition requires exactly because required_fields names no session dataset. "
    "The window it is handed is the union of the report periods of EVERY statement dataset it "
    "reads, so a security that filed an income statement for a period and no balance sheet is "
    "input_missing rather than silently short a term. EVERY FLOW this factor reads is accumulated "
    "inside the calendar fiscal year, so it is read through the cumulative-to-TTM identity rather "
    "than as the latest stored figure -- cumulative[latest] + cumulative[the December inside the "
    "window] - cumulative[the same quarter one year earlier] -- and max_window_periods equals "
    "lookback_periods, which is the contract's own statement that no filing is missing inside the "
    "window and is what makes that index arithmetic arithmetic rather than a guess."
)
"""The axis-and-window sentence the four quality notes share. One claim about four factors."""


RETURN_ON_EQUITY_TTM: Final[FactorDefinition] = FactorDefinition(
    key="return_on_equity_ttm",
    version=1,
    family="quality",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN),
        FactorField(dataset=BALANCE_SHEET_DATASET, column=BOOK_EQUITY_COLUMN),
    ),
    lookback_sessions=None,
    max_window_sessions=None,
    lookback_periods=CAPITAL_TURNOVER_PERIODS,
    max_window_periods=CAPITAL_TURNOVER_PERIODS,
)
"""ROE: trailing-twelve-month profit attributable to the parent, over the closing parent equity.

**It is computed from two statement columns rather than read from `fina_indicator.roe`, and that
is this issue's first decision rather than an inherited one.** `V2-P3-009` refused the published
`pe`, `pb` and `ps` for one reason -- inverting a published multiple scores the upstream's own
arithmetic -- and that reason applies here too but is *not* the decisive one. Three arguments
survived a live probe, in the order of how much they decide; a fourth was written, measured, and
**falsified in the direction that matters**, and it is kept below rather than deleted.

1. **A published ROE is a cumulative-period return and no arithmetic converts it to a trailing
   one.** A-share statements accumulate inside the calendar fiscal year, so a Q1 `roe` is three
   months of profit over equity and a Q3 `roe` is nine, and one cross section read at one `as_of`
   mixes both the moment two issuers file on different schedules -- exactly the defect
   `TRAILING_TWELVE_MONTH_PERIODS` exists to remove from `earnings_yield_ttm`. There the repair is
   an identity over *sums*; here there is none, because a ratio is not a sum. `roe[P] +
   roe[December] - roe[P - 4 quarters]` is the trailing return on equity of nothing at all: its
   three terms carry three different denominators. So reading the column means shipping a
   mixed-horizon quantity into a cross section, which is the one thing this family may not do.
2. **The formula behind the number is not stated and cannot be checked from inside the
   projection.** Nothing in the response says whether the denominator is opening, closing or
   weighted-average equity, whether the numerator is attributable or consolidated, or whether it
   is the deducted profit. `fina_indicator` carries no profit column and no equity column, so a
   reader holding only that endpoint has nothing to reconcile against; the reconciliation exists
   only because this factor's own two columns exist, which is the argument for reading them.
3. **`fina_indicator` is the endpoint this repository's statement contract was written about.**
   It has **no `update_flag`, no `f_ann_date` and no `report_type`**, 81.7% of its keys carry more
   than one row, and its ambiguous-filing rate is 13.70% as recorded and **11.80%** over
   `V2-P3-010`'s two disjoint samples, against `income`'s 8.51% and `balancesheet`'s 0.95% on the
   same securities. And the disagreement reaches this exact number: the two versions of
   `603049.SH`'s 2024 annual give `roe` as **23.9249 and 176.0751**, a factor of 7.4, under one
   `ann_date` with nothing in the dataset to order them, and nothing in the response says which is
   current.
4. **The refusal-exposure argument was written, measured and points the other way.** The sentence
   this docstring carried first was that `roe`'s recorded loss -- 5 reads in a 53-security corpus
   and 33 in a 76-security one, a 6.6x move -- made it the riskier read. `V2-P3-010`'s probe
   measured all three columns over two disjoint samples, 185 securities, and the ordering is the
   opposite:

   | column | sample A | sample B | combined |
   |---|---:|---:|---:|
   | `fina_indicator.roe` | 16 / 5,491 | 13 / 5,374 | 29 / 10,865 (**0.267%**) |
   | `n_income_attr_p` | 7 / 5,372 | 17 / 5,223 | 24 / 10,595 (**0.227%**) |
   | `total_hldr_eqy_exc_min_int` | 15 / 5,250 | 20 / 5,143 | 35 / 10,393 (**0.337%**) |

   The computed route reads **both** of the other two, over **five** contiguous periods, where the
   served column would have been one read at one period. So computing costs *more* refusal
   surface, not less -- about 0.56% against 0.27% per filing before the reach is counted at all.
   The 6.6x move is real and is a fact about samples rather than about this column; it does not
   make the published column the safer read, and this factor does not claim it does.

**So what it costs is the fourth argument turned around, stated rather than absorbed.** Reading
two datasets means an ambiguity in *either* costs the security its value; the five-period reach
means a security with fewer than five contiguous filings is `insufficient_history` here where one
`fina_indicator.roe` row would have answered; and the wider reach meets more ambiguous filings.
The third of those was the sharpest of the three until `V2-P3-018`, because the cost of an
ambiguity anywhere in the cross section was the **whole build**; it is now `ambiguous_filing` on
the securities whose own window holds the contradictory filing, so the cost is a coverage rate
rather than a wall. All three are paid for arguments 1 to 3, and 1 is the one that cannot be
bought off: a mixed-horizon quantity in a cross section is not a factor with a coverage cost, it
is a different number per security.
"""

RETURN_ON_EQUITY_TTM_NOTE: Final[FactorNote] = FactorNote(
    subject=RETURN_ON_EQUITY_TTM.qualified_key,
    summary=(
        "ROE: the trailing twelve months of income.n_income_attr_p divided by "
        "balancesheet.total_hldr_eqy_exc_min_int at the last period of the same window. It is "
        "COMPUTED and is deliberately NOT fina_indicator.roe, and the decisive reason is not the "
        "one V2-P3-009 gave for refusing the published multiples: a published ROE is a "
        "CUMULATIVE-period return -- three months of profit over equity at Q1, nine at Q3 -- and "
        "no arithmetic converts it to a trailing one, because the cumulative-to-TTM identity is "
        "an identity about sums and a ratio is not a sum; roe[P] + roe[December] - roe[P-4] has "
        "three different denominators and is the trailing return on equity of nothing. Three "
        "further reasons: the formula behind the number is unstated (opening, closing or "
        "weighted-average equity; attributable or consolidated; deducted or not) and "
        "fina_indicator carries neither a profit nor an equity column to reconcile it against; "
        "fina_indicator is the endpoint with no update_flag, no f_ann_date and no report_type, "
        "81.7% of its keys carrying more than one row and 11.80% of its filings ambiguous on "
        "V2-P3-010's own two-sample probe against income's 8.51% and balancesheet's 0.95%, and "
        "the disagreement reaches this exact number -- the two versions of 603049.SH's 2024 "
        "annual give roe as 23.9249 and as 176.0751, a factor of 7.4. A FOURTH argument was "
        "written, measured on a live probe of 185 securities and FALSIFIED in the direction that "
        "matters, and it is recorded rather than deleted: the refusal exposure of the computed "
        "route is WORSE, not better. Over two disjoint samples fina_indicator.roe's versions "
        "disagree on 29 of 10,865 filings (0.267%) against 24 of 10,595 (0.227%) for "
        "income.n_income_attr_p and 35 of 10,393 (0.337%) for "
        "balancesheet.total_hldr_eqy_exc_min_int -- and this factor reads BOTH of the latter two "
        "over FIVE contiguous periods where the served column would have been one read at one "
        "period, about 0.56% against 0.27% per filing before the reach is counted. What it costs "
        "is therefore paid rather than hidden: two datasets means an ambiguity "
        "in EITHER costs the security its value, five contiguous filings means a young issuer is "
        "insufficient_history where one fina_indicator row would have answered, and a wider reach "
        "meets more ambiguous filings -- which V2-P3-018 turned from a refusal of the whole cross "
        "section into an ambiguous_filing code on that one security. The denominator is the "
        "CLOSING equity and not "
        "the average of the window's ends, which departs from the textbook on purpose: "
        "earnings_yield_ttm and book_to_price already ship and divide this same trailing profit "
        "and this same closing equity by one market capitalisation, so EP / BP is exactly this "
        "factor and an averaged denominator would make the repository carry two incompatible "
        "statements of what a book value is. The bias that buys has a direction and is disclosed: "
        "closing equity exceeds average equity for an issuer that raised capital during the year, "
        "so this ROE reads lower than the textbook one for the names that grew equity fastest. A "
        "non-positive equity is undefined_value and NOT a negative ratio, which is the opposite "
        "of book_to_price's rule on the same column -- the rule follows the position, because a "
        "negative denominator turns a profit into a negative return and a loss into a positive "
        "one." + _QUALITY_AXIS_PROSE + _QUALITY_UNMEASURED_DIRECTION_PROSE
    ),
)
"""`RETURN_ON_EQUITY_TTM`'s prose, out of `factor_id`. See `domain/factor.py::FactorNote`."""

RETURN_ON_CAPITAL_TTM: Final[FactorDefinition] = FactorDefinition(
    key="return_on_capital_ttm",
    version=1,
    family="quality",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=INCOME_DATASET, column=CONSOLIDATED_NET_PROFIT_COLUMN),
        FactorField(dataset=BALANCE_SHEET_DATASET, column=TOTAL_ASSETS_COLUMN),
        FactorField(dataset=BALANCE_SHEET_DATASET, column=CURRENT_LIABILITIES_COLUMN),
    ),
    lookback_sessions=None,
    max_window_sessions=None,
    lookback_periods=CAPITAL_TURNOVER_PERIODS,
    max_window_periods=CAPITAL_TURNOVER_PERIODS,
)
"""ROIC: trailing consolidated profit over capital employed, `total_assets - total_cur_liab`.

**Both halves of the textbook ratio are unavailable in this projection and each is replaced by
the thing it reduces to, on `RETURN_VOL_60`'s terms.** The textbook ROIC is `EBIT * (1 - t)` over
invested capital, and:

- **The numerator's add-back cannot be done.** `income.ebit` is served and is unusable: it carries
  **258 of that endpoint's 288** recorded refused field reads and **888 of the 902** ambiguous
  filings `V2-P3-010`'s own two-sample probe found (8.38% of 10,595), with a worst case of
  `-7,579,086` against `+3,427,524` for one filing of `603333.SH` -- opposite signs on the number
  the ratio is about. Reconstructing it needs interest expense, and 财务费用 is in none of the ten
  stored `income` columns. What the projection does carry is the identity `n_income = total_profit
  - income_tax`, which is the after-tax profit of the whole consolidated entity -- i.e. NOPAT less
  the after-tax net interest that cannot be added back.
  `tests/unit/test_factor_quality_family.py::
  test_the_consolidated_profit_is_the_pre_tax_profit_less_the_tax_on_real_rows` asserts that
  identity on real served rows **with its own boundary**: it holds on every period from 2007
  onward over the six securities probed (446 rows, worst relative residual 8.9e-7) and fails
  before it, because the pre-2007 CAS reported 净利润 already net of minority interest. That is
  what makes "the missing term is exactly interest" a measurement rather than a reading of the
  statement's shape, and it is stated with the boundary rather than as a law.
- **The denominator's operating form cannot be stated either.** Invested capital in the operating
  approach is total assets less the *non-interest-bearing* current liabilities, and this
  projection carries no split of `total_cur_liab` into its interest-bearing and operating parts.
  `total_assets - total_cur_liab` is capital employed -- equity at every level plus the
  non-current liabilities -- which is the widest base this projection can state exactly, and it is
  the base the *consolidated* profit is the return to. That pairing is why the numerator is
  `n_income` and not `n_income_attr_p`.

So this factor understates a NOPAT-based ROIC for any issuer with non-current borrowings, by the
after-tax interest on them, and the understatement is largest for the most leveraged names. That
is a bias with a direction and it is disclosed rather than bounded, because nothing here can
measure it.

**It is blind to financial issuers, by construction and not by accident.** A bank's balance sheet
carries no current / non-current split, so `total_cur_liab` is null and the read is
`input_missing` -- the same shape as `GROSS_MARGIN_STABILITY`'s null `oper_cost`, and the right
answer for a company type whose "capital employed" is not a defined quantity.
"""

RETURN_ON_CAPITAL_TTM_NOTE: Final[FactorNote] = FactorNote(
    subject=RETURN_ON_CAPITAL_TTM.qualified_key,
    summary=(
        "ROIC: the trailing twelve months of income.n_income divided by capital employed, which "
        "is balancesheet.total_assets less balancesheet.total_cur_liab at the last period of the "
        "same window. BOTH halves of the textbook ratio are unavailable in this projection and "
        "each is replaced by what it reduces to, which is RETURN_VOL_60's rule rather than a "
        "rename. The numerator should be NOPAT = EBIT * (1 - t): income.ebit is served and is "
        "unusable at 258 of that endpoint's 288 refused field reads, worst case -7,579,086 "
        "against +3,427,524 for one 603333.SH filing -- opposite signs on the very quantity -- "
        "and no stored column carries interest expense, so the add-back cannot be reconstructed. "
        "n_income is total_profit less income_tax, which is the after-tax profit of the whole "
        "consolidated entity, i.e. NOPAT less the after-tax net interest; that identity is "
        "asserted on real stored rows rather than read off the statement's shape. The denominator "
        "should be total assets less the NON-INTEREST-BEARING current liabilities, and this "
        "projection carries no split of total_cur_liab, so capital employed -- equity at every "
        "level plus non-current liabilities -- is the widest base it can state exactly. The "
        "consequence is a bias with a direction and it is disclosed rather than bounded: this "
        "factor understates a NOPAT-based ROIC by the after-tax interest on non-current "
        "borrowings, most for the most leveraged names. The numerator is n_income and NOT "
        "n_income_attr_p precisely because capital employed is financed by the parent's owners, "
        "the minority holders and the non-current creditors together, and the two columns are "
        "different numbers on real rows -- 600739.SH's 2024 annual gives 664,195,391.66 against "
        "209,556,865.25. A capital employed that is not strictly positive is undefined_value "
        "rather than a signed ratio. It is blind to banks, insurers and brokers by construction: "
        "a financial balance sheet has no current / non-current split, total_cur_liab is null and "
        "the read is input_missing, which is the right answer for a company type whose capital "
        "employed is not a defined quantity."
        + _QUALITY_AXIS_PROSE
        + _QUALITY_UNMEASURED_DIRECTION_PROSE
    ),
)
"""`RETURN_ON_CAPITAL_TTM`'s prose, out of `factor_id`."""

GROSS_MARGIN_STABILITY: Final[FactorDefinition] = FactorDefinition(
    key="gross_margin_stability",
    version=1,
    family="quality",
    direction="lower_is_better",
    required_fields=(
        FactorField(dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN),
        FactorField(dataset=INCOME_DATASET, column=OPERATING_COST_COLUMN),
    ),
    lookback_sessions=None,
    max_window_sessions=None,
    lookback_periods=GROSS_MARGIN_PERIODS,
    max_window_periods=GROSS_MARGIN_PERIODS,
)
"""毛利率稳定性: the dispersion of four trailing-twelve-month gross margins.

**The value is the instability and the key names the property, which is a deliberate pairing
rather than a slip.** Stability is not a number; the dispersion of the thing whose stability is
being asked about is, and `direction="lower_is_better"` is the field that says which end is the
good one -- exactly what `domain/factor.py::FactorDirection` exists for. The alternative was to
report `-stdev` so that "higher is better" reads literally, and it was refused because negating a
dispersion puts a sign on the value whose entire content is already in `direction`, and because
`V2-P3-005` reads that field to sign an IC.

**A standard deviation and not a coefficient of variation.** A gross margin is already
dimensionless, so the usual reason to divide by the mean does not apply -- and the mean is the
wrong thing to divide by here for the reason `EARNINGS_YIELD_TTM` is a yield rather than a
multiple: an issuer whose average margin is near zero would get an explosive coefficient, and one
whose average margin is negative would get a *negative* one -- ranking the most erratic loss-maker
below every stable name in a `lower_is_better` cross section. `E/P` is monotone through zero and
`stdev / mean` is not, and `tests/unit/test_factor_quality_family.py::
test_a_negative_average_margin_still_has_a_positive_dispersion` drives both halves on one window.

See `GROSS_MARGIN_PERIODS` for why eight contiguous filings and why the trailing-twelve-month
margin rather than the cumulative or the single-quarter one, and `GROSS_MARGIN_OBSERVATIONS` for
why four overlapping observations.
"""

GROSS_MARGIN_STABILITY_NOTE: Final[FactorNote] = FactorNote(
    subject=GROSS_MARGIN_STABILITY.qualified_key,
    summary=(
        "Gross-margin stability: the Bessel-corrected sample standard deviation of four "
        "trailing-twelve-month gross margins, each (trailing total_revenue - trailing oper_cost) "
        "/ trailing total_revenue, taken at four consecutive report-period ends off a window of "
        "eight contiguous filings. The VALUE is the instability and the KEY names the property "
        "the roadmap asks for; direction=lower_is_better is the field that says which end is "
        "good, and reporting a negated dispersion was refused because the sign would duplicate "
        "that field. The margin is trailing and not cumulative-as-filed and not single-quarter, "
        "because a stability is a dispersion of something comparable: a Q1 cumulative margin is a "
        "three-month margin and a Q3 one is nine months, and a single-quarter series is dominated "
        "by seasonality by construction -- a retailer whose fourth quarter is its best would "
        "score as unstable every year of its life. Every trailing observation spans a full year, "
        "so the seasonal term is in all four equally and cancels out of their dispersion. THE "
        "EIGHT-PERIOD WINDOW IS WHERE V2-P3-009's TTM HELPER STOPS APPLYING, AND ITS FAILURE "
        "MODE THERE IS NOT FAIL-CLOSED: the identity finds the fiscal year end inside "
        "periods[:-1] and refuses unless there is exactly one, and periods[:-1] of a contiguous "
        "eight is SEVEN consecutive quarters, which holds two Decembers when the window ends at "
        "Q1, Q2 or Q3 -- refused -- and exactly ONE when it ends at Q4, where it answers a number "
        "covering about two years of flow rather than one. So the identity is applied to "
        "five-period SLICES of the eight -- four of them, at four consecutive quarter ends -- and "
        "each slice's own periods[:-1] is four CONSECUTIVE quarters, of which exactly one ends a "
        "year at every alignment. Four is the fewest observations at which a Bessel-corrected "
        "estimator is doing what its name says, and each further one costs a contiguous filing "
        "whose ambiguity codes this security ambiguous_filing (V2-P3-018, which made that a "
        "per-security answer rather than a refusal of the whole build). The four "
        "overlap by three quarters each, so this is a drift measure and not four independent "
        "draws. A standard deviation and not a coefficient of variation: a gross margin is "
        "already dimensionless, and dividing by a mean that can be near zero or negative would "
        "give an explosive or sign-inverted number for exactly the issuers a margin factor is "
        "asked about. A slice whose trailing revenue is not strictly positive is undefined_value. "
        "It is input_missing for banks, insurers and brokers, because a financial publishes no "
        "cost of sales and oper_cost is null -- which is the right answer for a company type "
        "whose gross margin is not a defined quantity."
        + _QUALITY_AXIS_PROSE
        + _QUALITY_UNMEASURED_DIRECTION_PROSE
    ),
)
"""`GROSS_MARGIN_STABILITY`'s prose, out of `factor_id`."""

ACCRUALS_TTM: Final[FactorDefinition] = FactorDefinition(
    key="accruals_ttm",
    version=1,
    family="quality",
    direction="lower_is_better",
    required_fields=(
        FactorField(dataset=INCOME_DATASET, column=CONSOLIDATED_NET_PROFIT_COLUMN),
        FactorField(dataset=CASH_FLOW_DATASET, column=OPERATING_CASH_FLOW_COLUMN),
        FactorField(dataset=BALANCE_SHEET_DATASET, column=TOTAL_ASSETS_COLUMN),
    ),
    lookback_sessions=None,
    max_window_sessions=None,
    lookback_periods=CAPITAL_TURNOVER_PERIODS,
    max_window_periods=CAPITAL_TURNOVER_PERIODS,
)
"""应计项: the trailing profit that did not arrive as operating cash, scaled by total assets.

**The cash-flow definition and not the balance-sheet one, and the reason is that the balance-sheet
one is provably contaminated in this projection.** Sloan's accrual is the part of reported
earnings that is not backed by cash, and it has two standard constructions:

- **`net income - operating cash flow`**, which is what this factor takes. It is one subtraction
  between two figures the endpoints publish directly, and the quantity it names is the accrual
  concept itself.
- **The change in non-cash working capital**, which would be `(total_cur_assets - money_cap -
  total_cur_liab)` differenced across a year. All four columns are stored, so this was buildable
  -- and it is wrong here, because `total_cur_liab` includes short-term borrowings and this
  projection carries no column that removes them. The measure would then move with an issuer's
  *financing* decisions, and the depreciation add-back the textbook version also needs is in none
  of the stored columns either.

**`free_cashflow` is refused and needs no argument beyond the census**: it carries **all 450** of
`cashflow`'s recorded refused field reads and **all 1,643** of the ambiguous filings
`V2-P3-010`'s two-sample probe found, with a worst case of `+316,026,934` against `-294,173,456`
for one filing of `300002.SZ`. `n_cashflow_act` is the column this factor reads instead, and its
recorded refusal count is exactly **`0`** -- which is why it was re-measured rather than quoted;
see `OPERATING_CASH_FLOW_COLUMN` and this module's docstring section "The quality family".

**The scaler is `total_assets` at the window's last period.** Sloan scales by *average* total
assets; the closing figure is taken for `CAPITAL_TURNOVER_PERIODS`' reason, so that every ratio in
this family divides by a stock read at one point in time rather than by a two-point average of a
path nothing here can see. `cashflow` is the endpoint with the highest ambiguous-filing rate of the
four -- 450 of 2,849 as recorded and **1,643 of 9,602 (17.11%)** over `V2-P3-010`'s own two
disjoint samples -- so this is the factor of the family with the widest refusal surface, and it
reads three datasets where the others read two.
"""

ACCRUALS_TTM_NOTE: Final[FactorNote] = FactorNote(
    subject=ACCRUALS_TTM.qualified_key,
    summary=(
        "Accruals: the trailing twelve months of income.n_income less the trailing twelve months "
        "of cashflow.n_cashflow_act, divided by balancesheet.total_assets at the last period of "
        "the same window -- the part of reported profit that did not arrive as operating cash. "
        "The cash-flow construction and NOT the balance-sheet one, because the balance-sheet one "
        "is provably contaminated in this projection: the change in non-cash working capital "
        "would be (total_cur_assets - money_cap - total_cur_liab) differenced across a year, all "
        "four columns are stored, and total_cur_liab includes short-term borrowings that no "
        "stored column removes -- so the measure would move with an issuer's financing decisions "
        "-- while the depreciation add-back the textbook version also needs is in none of the "
        "stored columns. free_cashflow is refused on the census alone: it carries ALL 450 of "
        "cashflow's refused field reads, worst case +316,026,934 against -294,173,456 for one "
        "300002.SZ filing -- and over V2-P3-010's own two disjoint samples EVERY one of "
        "cashflow's 1,643 ambiguous filings disagrees about it. n_cashflow_act's own recorded "
        "refusal count is exactly 0 over 14,245 field reads, which is the number "
        "domain/financial_statements.py says to distrust -- total_assets moved from 0 to 18 "
        "between two samples of one day -- so V2-P3-010 measured the column itself rather than "
        "quoting the zero, and it is 5 of 9,602 filings (0.052%) rather than none. The numerator "
        "is n_income and not "
        "n_income_attr_p because an operating cash flow is the whole consolidated entity's, and "
        "total_assets is the whole entity's too, so all three terms share one reporting boundary. "
        "This is the factor of the family with the widest refusal surface: it reads three "
        "datasets where the others read two, and cashflow's ambiguous-filing rate is the highest "
        "of the four endpoints at 450 of 2,849 as recorded and 1,643 of 9,602 (17.11%) as "
        "measured. A non-positive total_assets is "
        "undefined_value. The direction is the accruals anomaly's conventional prior -- lower "
        "accruals are taken to be the better earnings -- and a negative value is computed and "
        "negative, because an issuer whose operating cash exceeds its reported profit is the case "
        "this factor exists to find." + _QUALITY_AXIS_PROSE + _QUALITY_UNMEASURED_DIRECTION_PROSE
    ),
)
"""`ACCRUALS_TTM`'s prose, out of `factor_id`."""
# --- `V2-P3-011`: the growth family -------------------------------------------------------------
#
# See this module's docstring section "The growth family" for the judgements the three factors
# share. `QUARTERS_PER_YEAR` carries the one piece of index arithmetic all of them rest on, and
# `_year_on_year` is the whole of the arithmetic; nothing here calls `_trailing_twelve_months`,
# and `YEAR_ON_YEAR_ACCELERATION_PERIODS` records what happens to a reader who tries.


QUARTERS_PER_YEAR: Final[int] = 4
"""How many fiscal quarters a year holds, which on this grid is an exact offset and not a ratio.

`FISCAL_QUARTER_ENDS` has four members and a PRC listed company's accounting year is the calendar
year, so "the same period one year earlier" is `window[index - 4]` on a **contiguous** window --
an index subtraction, not a search for a matching month and not a date arithmetic this module
would then have to agree with a calendar about. `_period_span` is what makes the window
contiguous, and `_quarter_index` is the same fact stated on the other side: it is `year * 4 +
month // 3 - 1`, so two periods four apart on the grid are twelve months apart on the calendar.
"""

YEAR_ON_YEAR_PERIODS: Final[int] = QUARTERS_PER_YEAR + 1
"""How many contiguous filings a cumulative year-on-year needs: the latest and four back.

`window[-1]` is the period being reported and `window[-5]` is the same period one fiscal year
earlier, so the reach is the offset plus the point it is measured from. Five, which is
`TRAILING_TWELVE_MONTH_PERIODS`' number **for a different reason and deliberately not the same
constant**: that one is five because a trailing twelve months is a three-term identity whose
middle term is a December the formula has to find inside the window, and this one is five because
four is how far back a year is. Binding the two would make a later change to either move the
other, and they are not one decision.

**Nothing between the two ends is read.** `window[1:4]` exists only so that
`max_window_periods == lookback_periods` can say the ends are four quarters apart; the arithmetic
touches `[0]` and `[-1]` alone. That is the same cost `TRAILING_TWELVE_MONTH_PERIODS` prices for
its own family -- a security holding both ends and missing one filing between them is
`insufficient_history` rather than computed off the periods the formula names -- and it is the
window model's price rather than the identity's, in exactly that constant's words.
"""

YEAR_ON_YEAR_ACCELERATION_PERIODS: Final[int] = 2 * QUARTERS_PER_YEAR + 1
"""How many contiguous filings the change in a year-on-year rate needs: nine.

Two year-on-year rates measured one year apart share their middle period, so the reach is two
years of offset plus the point: `window[-1]` over `window[-5]`, less `window[-5]` over
`window[-9]`. `domain/factor.py::lookback_periods` names nine as the widest reach in
`V2-P3-009`..`013` and this is it.

**No trailing twelve months is read anywhere in this family, and on a nine-period window that is
a correctness requirement rather than a simplification.** `_trailing_twelve_months` finds its
December by searching `window.periods[:-1]`, and how many year ends that slice holds is a
function of **N and of where the window ends**, not of N alone: `[:-1]` is `N - 1` consecutive
quarters, and `K` consecutive quarters hold `K // 4` or `K // 4 + 1` year ends depending on the
alignment. At `N = 5` it is four quarters and therefore **exactly one** in all four alignments,
which is what makes `V2-P3-009`'s identity legitimate. At `N = 8` it is seven, which is one or
two -- so a helper reused there answers `None` for three alignments and, for the fourth, returns a
confident wrong number built from the *previous* year's December. **At `N = 9` it is eight, which
is exactly two years of quarters and therefore exactly two year ends in every alignment**, so the
helper answers `None` for all four -- but that is a property of nine being odd about a four-cycle
and not a guard anybody wrote, and a family that relied on it would be relying on an arithmetic
coincidence. This one does not rely on it: it never calls the helper.
`tests/unit/test_factor_growth_family.py::
test_a_nine_period_window_holds_two_year_ends_in_every_alignment`
drives all four alignments and asserts the year-end **count** rather than only the `None`, so the
day somebody widens this reach to eight the test says why it broke.
"""

NEWEST_PERIOD: Final[int] = -1
"""The index of the most recent report period knowable at `as_of`, per `FactorWindow`."""

YEAR_EARLIER_PERIOD: Final[int] = NEWEST_PERIOD - QUARTERS_PER_YEAR
"""`-5`: the index the acceleration's earlier year-on-year is measured at.

Named rather than written twice, because it is the same offset `_year_on_year` applies to its own
argument and a second literal `-5` would be two spellings of one decision.
"""


def _year_on_year(window: FactorWindow, *, dataset: str, column: str, index: int) -> float | None:
    """`cumulative[index] / cumulative[index - 4] - 1`, or `None` when that base is not positive.

    The A-share cumulative figure is the fiscal year to date, so the period four quarters back is
    the **same span of the same fiscal year**: a Q3 figure is nine months against nine months, an
    annual is twelve against twelve. That is what makes this ratio a year-on-year without any
    accumulation -- the seasonality `V2-P3-009`'s trailing sum exists to remove is already
    cancelled by the two sides covering the same months.

    **What that does not buy is one horizon across the cross section, and the difference is stated
    rather than run together with the one above.** `EARNINGS_YIELD_TTM` accumulates because a
    *level* read at one `as_of` mixes three months of one issuer's profit with twelve of another's,
    which is not a comparison at all. A ratio of two same-span figures is a comparison whichever
    span it is -- but a three-month growth and a twelve-month growth are still two different
    quantities being ranked together. A trailing twelve months over a trailing twelve months
    removes that, at the cost of a nine-period reach for a plain rate and a thirteen-period one for
    the acceleration; the trade is taken the other way here and its cost is disclosed, not denied.
    See `_GROWTH_HORIZON_PROSE` and this module's docstring section "The growth family".

    **Both ends are read at a fixed offset and neither is searched for**, which is why this
    function has no alignment behaviour at all: `_period_span` refuses a window whose ends are
    further apart on the fiscal-quarter grid than `max_window_periods`, so at
    `max_window_periods == lookback_periods` the offset `QUARTERS_PER_YEAR` **is** twelve months
    in every one of the four alignments.
    `tests/unit/test_factor_growth_family.py::
    test_the_year_on_year_reads_the_same_quarter_one_year_earlier_in_all_four_alignments` drives
    the four rather than asserting this, and
    `tests/unit/test_factor_growth_family.py::
    test_a_gapped_window_handed_to_the_evaluator_returns_a_wrong_number`
    pins the number this function produces when that guarantee is removed -- because it produces
    one, quietly, rather than refusing.

    **A base that is not strictly positive is `undefined_value` and that is the family's hardest
    judgement.** `-100` last year against `+50` this year is the classic trap: the ratio has an
    arithmetic answer, `50 / -100 - 1 = -1.5`, and that answer is **monotonically backwards**.
    The derivative of `num / base - 1` in `num` is `1 / base`, so at a negative base the better
    this year's outcome the *lower* the factor value -- and a `higher_is_better` cross section
    would then rank the completed turnaround below the deepening loss, on exactly the subset of
    the market a growth factor claims to find. `_market_capitalisation` refuses a non-positive
    denominator in this module for the same stated reason and this is that rule on the other axis.
    A zero base is the ordinary division and is refused with it.

    The alternative -- dividing by `abs(base)`, which is what makes `-100 -> +50` read `+1.5` --
    is refused rather than adopted, because it is a different function whose values are not
    comparable with the ones beside them: it scores a swing from `-1` to `+1` at `+2.0`, the same
    as a rise from 100 to 300, and this repository has measured nothing that would say those two
    belong at one rank. Refusing costs coverage that is measured rather than guessed; see
    `NET_PROFIT_YOY`.
    """
    cumulative = window.series(dataset, column)
    base = cumulative[index - QUARTERS_PER_YEAR]
    if base <= 0.0:
        return None
    return cumulative[index] / base - 1.0


_GROWTH_DIRECTION_PROSE: Final[str] = (
    " The declared direction is the growth premium's conventional prior -- the security whose "
    "fundamental is rising faster is taken to be the better one -- and this repository has "
    "measured nothing whatever about it, on this factor or on any other. A negative information "
    "coefficient on it would therefore be a result rather than a bug, and nothing in this "
    "repository licenses reading the sign either way before V2-P3-005 measures one. V2-P3-005 is "
    "where an IC would say something, and V2-P3's own gate records that most first-batch factors "
    "being insignificant is the expected result rather than a failure."
)
"""The direction sentence the three growth notes share, held to `REVERSAL_1D_NOTE`'s standard.

Written once because it is one claim about three factors and a copy is a thing that drifts;
`_VALUE_DIRECTION_PROSE` is the precedent and the reason is the same. It says one thing that
family's does not, and the addition is a refusal rather than a claim: it declines to characterise
the prior's strength at all. A sentence about what the literature finds for growth would be
prose this repository cannot falsify, which is the one kind it has been burned by.
"""

_GROWTH_BASE_PROSE: Final[str] = (
    " A cumulative figure four quarters back is the same span of the same fiscal year -- nine "
    "months against nine months at Q3, twelve against twelve at the annual -- so the ratio is a "
    "year-on-year without any accumulation, and no trailing twelve months is read anywhere in "
    "this family. Both ends are read at a fixed index offset rather than searched for, which is "
    "why the arithmetic is the same in all four quarter alignments; what makes that offset twelve "
    "months is max_window_periods == lookback_periods, measured on the fiscal-quarter grid by "
    "_period_span, and a window with a gap in it hands this arithmetic two periods that are not a "
    "year apart and gets a number rather than a refusal. A year-earlier base that is not strictly "
    "positive is undefined_value and not a number: 50 over a base of -100 has the arithmetic "
    "answer -1.5, and that answer is monotonically backwards, since the derivative of num / base "
    "in num is 1 / base and a higher_is_better cross section would then rank a completed "
    "turnaround below a deepening loss. Dividing by the absolute base instead is refused rather "
    "than adopted: it scores a swing from -1 to +1 at +2.0, the same as a rise from 100 to 300, "
    "and nothing measured here says those two belong at one rank."
)
"""The arithmetic sentence all three notes share. One claim about three factors; see
`_GROWTH_DIRECTION_PROSE`."""

_GROWTH_HORIZON_PROSE: Final[str] = (
    " The cost this family does not hide is that the ratio's horizon is whatever the security's "
    "newest filing is: at one as_of a name whose latest period is Q1 reports a three-month "
    "growth and a name that has not filed Q1 yet reports a twelve-month one, and both are "
    "computed and ranked together. That is a real heterogeneity and it is the price of reading "
    "the freshest disclosure rather than a trailing window -- a TTM-over-TTM year-on-year would "
    "make every security's horizon twelve months and would cost a nine-period reach for the plain "
    "rate and a thirteen-period one for the acceleration. The nine-against-five half of that trade "
    "is measured on this repository's own live probe rather than argued; the thirteen-period half "
    "is not measured and is stated as the arithmetic it is."
)
"""The disclosed cost all three notes share; see `REVENUE_YOY` for the measurement behind it."""


REVENUE_YOY: Final[FactorDefinition] = FactorDefinition(
    key="revenue_yoy",
    version=1,
    family="growth",
    direction="higher_is_better",
    required_fields=(FactorField(dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN),),
    lookback_sessions=None,
    max_window_sessions=None,
    lookback_periods=YEAR_ON_YEAR_PERIODS,
    max_window_periods=YEAR_ON_YEAR_PERIODS,
)
"""营收同比: `income.total_revenue` over the same period one fiscal year earlier, less one.

**The first shipped factor that reads a filing and nothing else**, which is the other half of the
report-period axis `V2-P3-009` opened. A growth rate is a ratio of one column to itself, so there
is no price in it and `lookback_sessions is None` is the contract's own statement of that rather
than an omission -- `FactorDefinition` refuses a session reach on a factor whose `required_fields`
are all filings.

`total_revenue` and not `revenue`, on `TOTAL_REVENUE_COLUMN`'s argument unchanged: the top line is
the inclusive one, and `V2-P3-009`'s review measured where the two part -- all 42 of `600519.SH`'s
stored periods, 1.94% at 2024Q3 -- so the choice lands somewhere else rather than on the same
number. **That the same is true of the two columns' year-on-year *rates* does not follow from it
and is measured separately**, because a growth rate divides a scale out: two columns in a constant
ratio give the identical growth, and this family's own integration fixture would have hidden the
choice entirely if it had copied the value family's constant multiples. `V2-P3-011`'s live probe
measured it at **4 of 230** (security, `as_of`) pairs, 1.7% -- so this column choice moves a level
by 1.94% on the securities it separates and a *rate* almost nowhere, which is the opposite of
`NET_PROFIT_YOY`'s pair and is the reason each is measured rather than argued from the other. It is
kept anyway: `total_revenue` is the column the level factor beside it reads, and a family whose
growth and yield described two different revenue definitions would make `V2-P3-008`'s redundancy
analysis compare two things.

The reach is `5 / 5`, which is `TRAILING_TWELVE_MONTH_PERIODS`' number for a different reason --
see `YEAR_ON_YEAR_PERIODS` -- so this factor and `sales_yield_ttm` are `insufficient_history` for
the same securities, and `book_to_price` is not.
"""

REVENUE_YOY_NOTE: Final[FactorNote] = FactorNote(
    subject=REVENUE_YOY.qualified_key,
    summary=(
        "Revenue year-on-year: income.total_revenue at the newest report period knowable at "
        "as_of, divided by the same column four quarters earlier, less one -- read off a window "
        "of five contiguous report periods whose max_window_periods equals its lookback_periods, "
        "which is the contract's own statement that no filing is missing inside it. This is the "
        "first shipped factor that reads a filing and NOTHING else: a growth rate is one column "
        "over itself, so there is no price in it and no session reach is declared. total_revenue "
        "and not revenue, on the top line's inclusiveness and on V2-P3-009's measurement that the "
        "two part on all 42 of 600519.SH's stored periods (123,122,542,625 against "
        "120,776,131,875 at 2024Q3, 1.94%) -- but a growth rate divides a constant scale out, so "
        "that the two columns' RATES also differ is measured separately rather than inherited, "
        "and this family's fixtures move the neighbouring columns by a changing ratio for exactly "
        "that reason. V2-P3-011's live probe -- two disjoint 60-security stride samples of the "
        "5,543 listed securities, 4,359 income filings announced 2016-2026, resolved under this "
        "engine's own collapse and refusal rules and evaluated at two as_of days -- puts the two "
        "revenue columns' rates apart on only 4 of 230 (security, as_of) pairs, 1.7%, against 139 "
        "of 181 for the two PROFIT columns. So the column choice that moves a level most here "
        "moves a rate least, which is why each is measured rather than argued from the other; "
        "total_revenue is kept because it is the column sales_yield_ttm reads, and a family whose "
        "growth and yield described two revenue definitions would make V2-P3-008's redundancy "
        "analysis compare two things. The same probe measures this reach's own cost: 230 of 240 "
        "pairs form a five-period window where 240 of 240 form a one-period one, and total_revenue "
        "refused 17 of the 4,359 filings (0.390%) -- 3.2 times the 0.123% V2-P3-009's review "
        "recorded on its own samples, which is a fact about how far a quoted refusal rate travels "
        "rather than about this column. A negative growth is computed and negative: a shrinking "
        "issuer is a real answer and the ratio is monotone through it as long as the base is "
        "positive." + _GROWTH_BASE_PROSE + _GROWTH_HORIZON_PROSE + _GROWTH_DIRECTION_PROSE
    ),
)
"""`REVENUE_YOY`'s prose, out of `factor_id`. See `domain/factor.py::FactorNote`."""


def _revenue_yoy(window: FactorWindow) -> float | None:
    """`REVENUE_YOY`: the newest cumulative top line over the same period a year earlier."""
    return _year_on_year(
        window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN, index=NEWEST_PERIOD
    )


NET_PROFIT_YOY: Final[FactorDefinition] = FactorDefinition(
    key="net_profit_yoy",
    version=1,
    family="growth",
    direction="higher_is_better",
    required_fields=(FactorField(dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN),),
    lookback_sessions=None,
    max_window_sessions=None,
    lookback_periods=YEAR_ON_YEAR_PERIODS,
    max_window_periods=YEAR_ON_YEAR_PERIODS,
)
"""净利同比: `income.n_income_attr_p` over the same period one fiscal year earlier, less one.

**`n_income_attr_p` and not `n_income`, and `earnings_yield_ttm`'s argument for that pair does not
reach here.** That one is about *pairing*: `total_mv` prices the parent's shares, so a numerator
including the minority interest would be one claim over another. This factor has no price in it at
all -- both sides are the same column -- so the pairing argument is silent and the choice has to
be made again on its own evidence. Three things decide it:

- **A partial acquisition moves the two columns by different amounts, in the direction that
  flatters the wider one.** Consolidation is what the two columns are *defined* by: a newly bought
  51% subsidiary contributes *all* of its profit to `n_income` and 51% of it to
  `n_income_attr_p`, so a growth rate on the consolidated column reads an ownership event as
  operating growth at roughly twice the size a holder of one listed share experienced. The reverse
  event -- an issuer buying out a minority -- moves `n_income_attr_p` and not `n_income`. Which of
  the two is commoner in this market is **not** measured here and no claim is made about it; what
  is measured is that the two columns give different rates, below.
- **The two are not a rescaling of each other, so this is not a choice a fixture can hide.**
  `600739.SH`'s 2024 annual carries `n_income` 664,195,391.66 against `n_income_attr_p`
  209,556,865.25, a factor of 3.169 -- two thirds of that consolidated profit belongs to somebody
  else. **A constant factor would cancel out of a growth rate entirely**, so that level gap settles
  nothing here; `V2-P3-011`'s live probe measured how often the two give a different *rate* and the
  answer is **139 of the 181** (security, `as_of`) pairs that have a comparable pair, **76.8%**.
  See `tests/unit/test_factor_growth_family.py::
  test_the_two_profit_columns_are_two_different_growth_rates_on_real_rows`, which asserts three of
  them by magnitude -- `002023.SZ`'s consolidated growth is 2.09 times its attributable one.
- **`earnings_yield_ttm` already reads this column**, so the level factor and the growth factor
  describe one series. `V2-P3-005`'s IC and `V2-P3-008`'s redundancy analysis then compare like
  with like rather than two profit definitions that differ by a factor of three on a real filing.

**This is the member of the family the non-positive base costs most, and the cost is measured.**
A loss-making issuer has a negative year-earlier base, so this factor is `undefined_value` for it
-- **49 of the 230** (security, `as_of`) pairs that form a five-period window on `V2-P3-011`'s two
disjoint 60-security probe samples, **21.3%**, against **0 of 230** for `total_revenue`. That is
the fifth of the market `earnings_yield_ttm` was deliberately built to score, and the asymmetry is
stated rather than smoothed: `V2-P3-009` chose a signed **yield** precisely because `E/P` is
monotone through zero, and a growth **rate** is not, because the zero is in its denominator rather
than its numerator. So the family's own coverage census will show this factor answering for four
securities in five where `revenue_yoy` answers for all of them, and that is the honest shape of a
profit growth rate rather than a defect in this one.
"""

NET_PROFIT_YOY_NOTE: Final[FactorNote] = FactorNote(
    subject=NET_PROFIT_YOY.qualified_key,
    summary=(
        "Net profit year-on-year: income.n_income_attr_p at the newest report period knowable at "
        "as_of over the same column four quarters earlier, less one, on revenue_yoy's window and "
        "terms throughout. n_income_attr_p and NOT n_income, argued here rather than inherited: "
        "earnings_yield_ttm chooses the attributable column because total_mv prices the parent's "
        "shares, and this factor has no price in it, so that argument is silent. What decides it "
        "instead is that consolidating a newly bought 51% subsidiary adds all of its profit to "
        "n_income and 51% to n_income_attr_p, so the consolidated column reads an ownership event "
        "as operating growth at about twice the size a holder of one listed share experienced -- "
        "which of consolidation and buying out a minority is commoner in this market is NOT "
        "measured here and no claim is made about it; that the two are not a rescaling of each "
        "other, since 600739.SH's "
        "2024 annual carries 664,195,391.66 against 209,556,865.25, a factor of 3.169, where a "
        "constant factor would cancel out of a growth rate entirely -- so what settles it is that "
        "V2-P3-011's live probe puts the two columns' RATES apart on 139 of the 181 (security, "
        "as_of) pairs that have a comparable pair, 76.8%, on two disjoint 60-security samples of "
        "the listed universe; and that earnings_yield_ttm "
        "already reads this column, so the level and the growth describe one series for "
        "V2-P3-005's IC and V2-P3-008's redundancy analysis. This is the member of the family the "
        "non-positive base costs most and the cost is measured rather than estimated: a "
        "loss-making issuer has a negative year-earlier base and is undefined_value here, on 49 "
        "of the 230 windows that form on that probe (21.3%) against 0 of 230 for total_revenue -- "
        "which is precisely the fifth of the market earnings_yield_ttm was built to score. That "
        "asymmetry is the point rather than an oversight -- a signed yield is monotone through "
        "zero because the zero is in its numerator, and a growth rate is not because the zero is "
        "in its denominator. This column's own refusal rate was measured on the same corpus at 20 "
        "of 4,359 filings (0.459%), 2.4 times the 0.189% V2-P3-009's review recorded, so no quoted "
        "rate for it is carried forward here without being re-measured."
        + _GROWTH_BASE_PROSE
        + _GROWTH_HORIZON_PROSE
        + _GROWTH_DIRECTION_PROSE
    ),
)
"""`NET_PROFIT_YOY`'s prose, out of `factor_id`."""


def _net_profit_yoy(window: FactorWindow) -> float | None:
    """`NET_PROFIT_YOY`: the newest cumulative attributable profit over the year-earlier one."""
    return _year_on_year(
        window, dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN, index=NEWEST_PERIOD
    )


REVENUE_YOY_ACCELERATION: Final[FactorDefinition] = FactorDefinition(
    key="revenue_yoy_acceleration",
    version=1,
    family="growth",
    direction="higher_is_better",
    required_fields=(FactorField(dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN),),
    lookback_sessions=None,
    max_window_sessions=None,
    lookback_periods=YEAR_ON_YEAR_ACCELERATION_PERIODS,
    max_window_periods=YEAR_ON_YEAR_ACCELERATION_PERIODS,
)
"""同比加速度: this year's revenue year-on-year less the same quarter's year-on-year a year ago.

**A difference of two year-on-year rates, and not a year-on-year of a year-on-year.** The second
reading is arithmetically available -- `(1 + g_now) / (1 + g_then) - 1` -- and is refused for the
reason the base guard exists: `g_then` is a growth rate, so `1 + g_then` crosses zero at a 100%
contraction and the quotient is sign-inverted on the other side of it. A difference of two rates
is defined wherever both rates are, is in the units both are in (a rate per year), and is what
"acceleration" means when the thing being differenced is already a rate.

**The two rates are measured one year apart, and that is what makes nine periods the reach rather
than six.** `YoY(P) - YoY(P-1)` is the cheaper construction and is wrong on cumulative figures:
`YoY(Q3)` is a nine-month growth and `YoY(H1)` a six-month one, so their difference is part
acceleration and part change of horizon. `YoY(P) - YoY(P-4)` differences two rates over the same
season, so the horizon cancels along with the seasonality. It reads `window[-1]`, `window[-5]` and
`window[-9]`, three periods of the nine the reach declares.

**Revenue and not net profit, and the reason is the base guard compounding, measured.** This
factor needs *two* positive bases where `NET_PROFIT_YOY` needs one, so a profit acceleration is
`undefined_value` for every issuer that lost money in either year-earlier period: **61 of the 220**
(security, `as_of`) pairs that form a nine-period window on `V2-P3-011`'s two disjoint probe
samples, **27.7%**, against **0 of 220** on `total_revenue`. A revenue line is positive for almost
every going concern, which is `SALES_YIELD_TTM`'s own observation, so the construction that
compounds the guard is put on the column the guard never fired for in 220 windows.

**Nine contiguous filings is two years and a quarter of unbroken disclosure and it is the widest
reach in `V2-P3-009`..`013`.** `domain/factor.py::lookback_periods` names it as such. What it
costs is measured rather than asserted and it is not the same cost `TRAILING_TWELVE_MONTH_PERIODS`
prices: five filings excludes a recent listing, and nine excludes a recent listing *and* any name
with a single missed filing anywhere in nine quarters. On the same probe the reach costs **10 of
the 230** pairs the five-period rate scores, **4.3%** -- smaller than the shape of the requirement
suggests, because an A-share issuer that files at all files every quarter, and larger at the
earlier of the two `as_of` days (8 of 114) than at the later (2 of 116), which is what a reach
measured against listing dates rather than against gaps looks like.
`tests/integration/panel/test_growth_family.py::
test_the_nine_period_reach_and_the_five_period_reach_answer_differently_for_the_same_security` is
that difference on one partition, inside one family, rather than in prose.
"""

REVENUE_YOY_ACCELERATION_NOTE: Final[FactorNote] = FactorNote(
    subject=REVENUE_YOY_ACCELERATION.qualified_key,
    summary=(
        "Revenue year-on-year acceleration: income.total_revenue's year-on-year at the newest "
        "report period knowable at as_of, less the same column's year-on-year four quarters "
        "earlier -- window[-1] over window[-5], less window[-5] over window[-9], off nine "
        "contiguous report periods. A DIFFERENCE of two rates and not a year-on-year of a "
        "year-on-year: the quotient reading (1 + g_now) / (1 + g_then) - 1 has 1 + g_then in its "
        "denominator, which crosses zero at a 100% contraction and is sign-inverted past it, "
        "while a difference is defined wherever both rates are and is in the units both are in. "
        "The two rates are measured a YEAR apart rather than a quarter apart, which is what makes "
        "the reach nine rather than six: on cumulative figures YoY(Q3) is a nine-month growth and "
        "YoY(H1) a six-month one, so differencing adjacent periods mixes acceleration with a "
        "change of horizon, and differencing the same season cancels both. Revenue and not net "
        "profit, because this construction needs TWO positive bases where the plain year-on-year "
        "needs one: on V2-P3-011's live probe -- two disjoint 60-security samples of the listed "
        "universe at two as_of days -- a profit acceleration is undefined_value for 61 of the 220 "
        "nine-period windows that form, 27.7%, against 0 of 220 on total_revenue. A revenue line "
        "is positive for almost every going concern, which is sales_yield_ttm's own observation, "
        "so the construction that compounds the guard is put on the column the guard never fired "
        "for. Nine contiguous filings is two years "
        "and a quarter of unbroken disclosure and is the widest reach in V2-P3-009..013; it "
        "excludes a recent listing AND any name with one missed filing in nine quarters, where "
        "the five-period reach beside it excludes only the first, and the same probe prices that "
        "at 10 of the 230 pairs the five-period rate scores (4.3%) -- 2 of 116 at the later as_of "
        "and 8 of 114 at the earlier one."
        + _GROWTH_BASE_PROSE
        + _GROWTH_HORIZON_PROSE
        + _GROWTH_DIRECTION_PROSE
    ),
)
"""`REVENUE_YOY_ACCELERATION`'s prose, out of `factor_id`."""


def _revenue_yoy_acceleration(window: FactorWindow) -> float | None:
    """`REVENUE_YOY_ACCELERATION`: the newest revenue year-on-year less the year-earlier one.

    `None` -- hence `undefined_value` -- when **either** rate is, which is the guard compounding
    this definition's docstring prices. Written as two calls to `_year_on_year` rather than as one
    expression over four cells, so that the factor is visibly the difference of the two rates the
    other two members of this family compute and cannot drift into being something else.
    """
    recent = _year_on_year(
        window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN, index=NEWEST_PERIOD
    )
    earlier = _year_on_year(
        window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN, index=YEAR_EARLIER_PERIOD
    )
    if recent is None or earlier is None:
        return None
    return recent - earlier


FACTOR_DEFINITIONS: Final[FactorRegistry] = FactorRegistry(
    (
        REVERSAL_1D,
        MOMENTUM_20_SESSIONS,
        MOMENTUM_60_SESSIONS,
        MOMENTUM_120_SESSIONS,
        REVERSAL_5_SESSIONS,
        RETURN_VOL_60,
        DOWNSIDE_VOL_60,
        TURNOVER_60,
        AMIHUD_60,
        EARNINGS_YIELD_TTM,
        BOOK_TO_PRICE,
        SALES_YIELD_TTM,
        DEDUCTED_EARNINGS_YIELD_TTM,
        RETURN_ON_EQUITY_TTM,
        RETURN_ON_CAPITAL_TTM,
        GROSS_MARGIN_STABILITY,
        ACCRUALS_TTM,
        REVENUE_YOY,
        NET_PROFIT_YOY,
        REVENUE_YOY_ACCELERATION,
    ),
    notes=(
        REVERSAL_1D_NOTE,
        MOMENTUM_20_SESSIONS_NOTE,
        MOMENTUM_60_SESSIONS_NOTE,
        MOMENTUM_120_SESSIONS_NOTE,
        REVERSAL_5_SESSIONS_NOTE,
        RETURN_VOL_60_NOTE,
        DOWNSIDE_VOL_60_NOTE,
        TURNOVER_60_NOTE,
        AMIHUD_60_NOTE,
        EARNINGS_YIELD_TTM_NOTE,
        BOOK_TO_PRICE_NOTE,
        SALES_YIELD_TTM_NOTE,
        DEDUCTED_EARNINGS_YIELD_TTM_NOTE,
        RETURN_ON_EQUITY_TTM_NOTE,
        RETURN_ON_CAPITAL_TTM_NOTE,
        GROSS_MARGIN_STABILITY_NOTE,
        ACCRUALS_TTM_NOTE,
        REVENUE_YOY_NOTE,
        NET_PROFIT_YOY_NOTE,
        REVENUE_YOY_ACCELERATION_NOTE,
    ),
)
"""Every factor this build declares, and the prose about it. All five families are in it."""

FACTOR_EVALUATORS: Final[Mapping[str, FactorEvaluator]] = MappingProxyType(
    {
        REVERSAL_1D.qualified_key: _reversal_1d,
        MOMENTUM_20_SESSIONS.qualified_key: _momentum_sessions,
        MOMENTUM_60_SESSIONS.qualified_key: _momentum_sessions,
        MOMENTUM_120_SESSIONS.qualified_key: _momentum_sessions,
        REVERSAL_5_SESSIONS.qualified_key: _reversal_5_sessions,
        RETURN_VOL_60.qualified_key: _return_vol_60,
        DOWNSIDE_VOL_60.qualified_key: _downside_vol_60,
        TURNOVER_60.qualified_key: _turnover_60,
        AMIHUD_60.qualified_key: _amihud_60,
        EARNINGS_YIELD_TTM.qualified_key: _earnings_yield_ttm,
        BOOK_TO_PRICE.qualified_key: _book_to_price,
        SALES_YIELD_TTM.qualified_key: _sales_yield_ttm,
        DEDUCTED_EARNINGS_YIELD_TTM.qualified_key: _deducted_earnings_yield_ttm,
        RETURN_ON_EQUITY_TTM.qualified_key: _return_on_equity_ttm,
        RETURN_ON_CAPITAL_TTM.qualified_key: _return_on_capital_ttm,
        GROSS_MARGIN_STABILITY.qualified_key: _gross_margin_stability,
        ACCRUALS_TTM.qualified_key: _accruals_ttm,
        REVENUE_YOY.qualified_key: _revenue_yoy,
        NET_PROFIT_YOY.qualified_key: _net_profit_yoy,
        REVENUE_YOY_ACCELERATION.qualified_key: _revenue_yoy_acceleration,
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
    clock, deliberately out (see `FactorPanel`); and `requirements` decides whether a read is
    *permitted* rather than what it returns -- the part of it that does decide (`years`) arrives
    in the identity as `manifest.inputs`.

    **`evaluators` was a fourth exemption until `V2-P3-019` and is now a determinant**, which is
    worth recording because the exemption was true about the wrong noun. It read "a substitution
    seam whose production value is this module's own table, which `code_commit` stands for. A
    callable cannot be canonically hashed" -- all of which holds of the *callable* and none of
    which ever held of its **output**. `manifest.observation_digest` addresses the answers, so an
    evaluator that computes different numbers from the same rows moves `manifest_id`. That was the
    last place in this contract where "decides the answers" and "reaches the identity" came apart.
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
    listed = set(universe)
    # The answers are computed *before* the manifest, and the ordering is forced rather than
    # stylistic: `observation_digest` is a field of `FactorBuildManifest`, so the manifest cannot
    # exist until the cross section does -- while every observation carries the `manifest_id` the
    # manifest does not have yet. `_UNSEALED_MANIFEST_ID` breaks that circle for exactly the
    # length of this function: the digest is taken over `(subject, coverage, value)`, which is
    # the one part of an observation that does not mention the identity, so the placeholder
    # cannot reach it, and `_seal` then re-stamps every row with the real address. Nothing built
    # under the placeholder escapes -- the tuple it produced is local and is replaced whole.
    unsealed = tuple(
        _classify(
            definition,
            subject=subject,
            as_of=as_of,
            in_universe=subject in listed,
            readings=readings,
            panel_sessions=panel_sessions,
            evaluator=evaluator,
            manifest_id=_UNSEALED_MANIFEST_ID,
        )
        for subject in ordered_subjects
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
        observation_digest=observation_digest(unsealed),
        inputs=_canonical_inputs(inputs),
    )
    # Read once, outside the loop. `manifest_id` is a pydantic `computed_field`, and
    # `domain/panel_batch.py` measured what that means on a hot path: a computed field is *not*
    # cached, so `ProviderBatch.payload_digest` cost 10.5 ms on its first access and 10.2 ms on
    # its second. Leaving `manifest.manifest_id` inside the comprehension would re-canonicalise
    # and re-hash the whole manifest once per security -- 5,534 times for a whole-market cross
    # section, for a value that cannot change while this loop runs.
    manifest_id = manifest.manifest_id
    observations = tuple(replace(observation, manifest_id=manifest_id) for observation in unsealed)
    return FactorPanel(
        definition=definition,
        manifest=manifest,
        observations=observations,
        built_at=built_at,
        input_provenance=tuple(provenance),
    )


def _canonical_inputs(refs: Sequence[FactorInputRef]) -> tuple[FactorInputRef, ...]:
    """This build's input partitions in the one order both sides of the round trip agree on.

    `FactorBuildManifest.inputs` is a **tuple**, so its order is inside `manifest_id`, and the two
    ends of the round trip were producing two different orders. `compute_factor` collected refs in
    `FactorDefinition.datasets` order -- income before balancesheet, because that is the order the
    fields are declared in -- while `_manifest_from_rows` has always reassembled them sorted by
    `(dataset, year)`, because a Parquet scan has no order to preserve. For a one-dataset factor
    the two agree and nothing showed; for `V2-P3-009`..`011`'s statement factors they do not, and
    the consequence was that **`load_factor_manifests` could not read back a multi-dataset build at
    all** -- it reassembled a manifest whose ID was not the one the rows were filed under and
    raised, on a partition it had just written itself.

    It stayed invisible because nothing on a read path called `load_factor_manifests` for such a
    factor: `write_factor_panels`' drop guard reads the catalog's subject list rather than
    decoding, and no face read a build back. `V2-P3-019` put that read on
    `load_factor_observations`, which is what surfaced it -- three integration tests over the
    quality and value families went red on the first run.

    Sorting here rather than un-sorting the decoder, because the decoder is the side that has no
    choice. The key is `(dataset, year)` with an **integer** year on both sides, so the two
    orderings are equal by construction rather than by every year happening to be four digits.
    """
    return tuple(sorted(refs, key=lambda ref: (ref.dataset, ref.year)))


@dataclass(frozen=True, slots=True)
class _DatasetReading:
    """One dataset's visible rows, indexed on that dataset's own axis.

    `points_by_subject` and `values` are keyed by a session for an ordinary dataset and by a
    report period for one of `PERIOD_INDEXED_DATASETS`; `axis` says which, so `_classify` never
    has to consult a dataset name. One reading type rather than two, because everything after the
    read is the same arithmetic on a different index -- and two types would mean two copies of
    `_stored_rows`, `_complete_series` and the window formation.

    `ambiguous_points_by_subject` is `V2-P3-018`'s carrier: the points at which this read found a
    filing the publisher stated twice and disagreed with itself about, in the columns this factor
    asked for. It is **always empty on the session axis** -- there is no collapse there and a
    second row of one `(subject, session)` still raises in `_read_dataset` -- so it is a
    period-axis fact travelling on the one reading type, rather than a second type.

    Keyed by subject rather than held as a set of `(subject, point)` pairs so that `_classify`'s
    lookup is one dictionary hit per security instead of a scan: that loop runs 5,534 times for a
    whole-market cross section, and the ambiguous set on a real statement partition is not small
    (8.51% of `income`'s filings and 17.11% of `cashflow`'s, measured in `V2-P3-010`'s probe).
    """

    points_by_subject: Mapping[str, tuple[date, ...]]
    values: Mapping[tuple[str, date], tuple[float | None, ...]]
    columns: tuple[str, ...]
    axis: FactorAxis
    ambiguous_points_by_subject: Mapping[str, frozenset[date]]


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

    - **Session axis, unchanged.** Two rows for one `(subject, session)` raise, equal or not. A
      dataset with several versions of one observation needs a reducer chosen for it before a
      factor may read it, and nothing here chooses one.
    - **Period axis.** Two rows for one `(subject, period)` announced on **different** days are an
      ordinary point-in-time restatement, and the later announcement is the one a reader standing
      at `as_of` would have -- exactly `financial_statements.StatementHistory.filing_for`'s rule,
      which takes `max(announced_on)` among the filings visible on the day. Two rows announced on
      the **same** day are the case no column in these datasets orders: `fina_indicator` carries
      more than one row for 81.7% of its `(ts_code, end_date, ann_date)` keys, has no
      `update_flag` and no `f_ann_date`, and `providers/tushare.py::_announcement_timeline`
      deliberately gives such rows byte-equal four-clock timelines. Those raise **when they
      disagree in the columns this factor asked for**, and collapse when they do not; see below.

    ## Collapsing what is provably one fact, on the period axis only (`V2-P3-009`)

    `domain/financial_statements.py` states the rule this engine now applies: *collapse what is
    provably one fact and refuse what is provably two*. `build_statement_history` folds two rows
    of one key whose stored values agree, and `ReportFiling.value_of` then raises **per field**,
    on the fields the survivors disagree about, so a `cashflow` pair that differs only in
    `free_cashflow` still answers `n_cashflow_act`.

    This function used to refuse on multiplicity alone, which made it strictly stricter than the
    domain it says it restates -- and the gap is most of the duplication rather than an edge of
    it. Of the duplicate keys that probe found, **372 of `income`'s 633, 1,166 of
    `balancesheet`'s 1,244 and 2,194 of `fina_indicator`'s 2,671** carry rows that are identical
    in every field the endpoint serves. Under the old rule every one of those refused a whole
    build; `ReportFiling.value_of` answers all of them. The audit that exists to hold the two
    implementations together could not see it, because its corpus has no key whose duplicate rows
    agree -- the shape `test_the_engines_period_selection_is_the_domains_filing_for`'s docstring
    calls "only what its corpus varies".

    So a second row of a `(subject, period, announcement)` triple whose **projected** cells equal
    the first's is dropped, and one that differs marks that `(subject, period)` ambiguous. The
    projection is the factor's own `required_fields`, which is what makes this `value_of`'s
    answer on the filing a reader at `as_of` would read rather than an approximation of it:
    `value_of(field)` returns a value iff every surviving version agrees on that field, and the
    survivors are distinguished by the *whole* stored projection plus `f_ann_date` -- so "all
    rows agree about the columns this factor reads" is the same predicate, reached by a narrower
    read. `_columns_two_versions_disagree_about` states the one corner where the two still part,
    and why that corner is deliberate.
    `tests/integration/panel/test_value_family.py::
    test_the_engine_answers_a_column_the_duplicate_rows_agree_about_and_codes_one_they_do_not`
    drives both halves over one partition built from two real rows.

    ## The ambiguity is that security's answer and not the build's (`V2-P3-018`)

    Until `V2-P3-018` the disagreement raised, so **one** ambiguous filing anywhere in the cross
    section refused the whole build. That is not an edge: the rows that genuinely disagree are
    8.15% of `income`'s 3,201 filings as recorded, 8.2% / 8.7% on `V2-P3-009`'s two independent
    samples, 8.51% / 0.95% / **17.11%** / 11.80% on `V2-P3-010`'s 185-security probe of the four
    endpoints -- and `accruals_ttm` reads three of them over five contiguous periods while
    `gross_margin_stability` reads eight. So none of `V2-P3-009`..`011`'s six factors could be
    built over a real whole-market statement partition at all.

    Now the disagreement is recorded against `(subject, period)` in
    `_DatasetReading.ambiguous_points_by_subject` and `_classify` turns it into
    `ambiguous_filing` **for the securities whose window contains that period**, leaving every
    other security in the cross section computed. The two properties the refusal had are kept
    rather than traded away:

    - **Order-independence.** The mark is decided by comparing the two projections, which is
      symmetric, and it is recorded whichever of the pair the scan reaches first. `values[key]`
      may still hold either row's cells, and nothing reads it: `_classify` returns the code
      before `_complete_series` is called. `tests/integration/panel/test_value_family.py::
      test_the_disagreement_code_does_not_depend_on_the_order_the_partition_returns_its_rows`
      drives both write orders.
    - **Fail-closed.** No version is chosen. `domain/financial_statements.py` measured what
      choosing one costs: `income.ebit` comes back as -7,579,086 on one row and +3,427,524 on
      the other, `cashflow.free_cashflow` as +316,026,934 against -294,173,456,
      `fina_indicator.fcff` as +843,920,834 against -966,053,502 -- opposite signs, so a rule
      that picked a row would invert this security's place in the cross section rather than
      nudge it.

    **What is lost and is not stored anywhere** is the *column list*: the refusal named the
    columns the two rows disagreed about and an observation has no place to put them. A caller
    who needs that detail has `statement_histories_from_panel_rows` and
    `ReportFiling.disagreeing_fields` one plane down, over the same rows; the stored observation
    carries the period window the ambiguity sits in (`input_period_first` / `input_period_last`)
    and the factor's `required_fields` are resolvable from `factor_id`, so the filing is
    locatable from the partition and the columns are not.

    The session axis keeps the strict rule and that asymmetry is deliberate. The period axis has
    a *measured* dataset property behind its collapse -- two versions of one filing under one
    announcement, which `domain/financial_statements.py` counts -- and no session-indexed dataset
    here has anything of the kind: `daily` and `daily_basic` serve one row per security per
    session, so a second one is a fault rather than a version and dropping it silently would hide
    the fault.

    So the refusal that fired before this axis existed still fires **on the session axis**
    wherever a second row appears at all, equal or not. What stopped raising on the period axis
    is first the case that was never a duplicate -- an annual and a Q1 disclosed on one day, two
    periods under one `event_time`, which is why an ordinary `income` input could not be read
    before -- and then, in `V2-P3-018`, the genuine disagreement, which became that security's
    coverage code instead.
    `tests/integration/panel/test_factor_report_periods.py` measures both directions.

    **The ambiguity is decided on the triple and not on the row the scan happens to reach first**,
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
    stated: dict[tuple[str, date, date], tuple[float | None, ...]] = {}
    ambiguous: dict[str, set[date]] = {}
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
            cells = tuple(
                _numeric(value, dataset=dataset, column=name, subject=subject, point=point)
                for name, value in zip(columns, row[offset:], strict=True)
            )
            filing = (subject, point, announcement)
            if period_indexed:
                # The period axis keeps every version's projected cells, because "one fact stated
                # twice" is decided by comparing them. The session axis keeps only the key: no
                # session-indexed dataset here has versions, so a second row is a fault and the
                # values would be a per-row cost with no reader -- at 675,148 rows for one
                # whole-market `daily` year, which is the scale this loop is measured at.
                previous = stated.get(filing)
                if previous is not None:
                    if _columns_two_versions_disagree_about(previous, cells, columns=columns):
                        ambiguous.setdefault(subject, set()).add(point)
                    continue
                stated[filing] = cells
            elif filing in filed:
                raise FactorEngineError(
                    f"{dataset} carries more than one row for {subject} on "
                    f"{point.isoformat()}; this engine reads one row per security per "
                    f"{axis}, so a dataset with several versions of one observation needs a "
                    "reducer chosen for it before a factor may read it"
                )
            else:
                filed.add(filing)
            key = (subject, point)
            if key in values:
                if announcement < announced[key]:
                    continue
            else:
                points.setdefault(subject, []).append(point)
            announced[key] = announcement
            values[key] = cells
    return (
        _DatasetReading(
            MappingProxyType({name: tuple(sorted(days)) for name, days in points.items()}),
            MappingProxyType(values),
            columns,
            axis,
            MappingProxyType({name: frozenset(days) for name, days in ambiguous.items()}),
        ),
        tuple(references),
        tuple(provenance),
    )


def _columns_two_versions_disagree_about(
    stated: tuple[float | None, ...],
    cells: tuple[float | None, ...],
    *,
    columns: tuple[str, ...],
) -> tuple[str, ...]:
    """The **period** axis's second-row rule: `()` if the two say the same thing, else the columns.

    An empty answer means the pair collapses; a non-empty one means this `(subject, period)` is
    `ambiguous_filing` for every security whose window reaches it.

    `build_statement_history`'s rule reached through a narrower read. Two rows of one
    `(subject, period, announcement)` key whose *projected* cells are equal are one fact stated
    twice, and two that are not are the case no column of these datasets orders. The session axis
    is not routed here at all; `_read_dataset` argues why its second row raises equal or not.

    **The counts quoted for this rule are the projection's, not the whole row's**, and the two are
    different numbers that `domain/financial_statements.py` is careful to separate. 372 of
    `income`'s 633 duplicate keys, 1,166 of `balancesheet`'s 1,244 and 2,194 of
    `fina_indicator`'s 2,671 agree in **every field the endpoint serves** -- all 85 / 152 / 108 of
    them. The predicate here is coarser and can only fold at least as much: that module's
    end-to-end table records **372 / 1,205 / 2,223** rows folded under the stored projection,
    which is the criterion this function applies, and only `income` comes out the same on both.
    `_read_dataset`'s own summary of the defect is stated on the served-field counts and says so;
    this docstring used to quote those counts under the word "projected", which is the one place
    the two were run together.

    ## How exactly this lines up with `ReportFiling.value_of`, including where it does not

    `value_of(field)` answers when every surviving version agrees on that field, and the survivors
    are distinguished by the whole stored projection plus `f_ann_date`. "Every row of this filing
    agrees about the columns this factor reads" is the same predicate reached with fewer columns,
    so **on the filing a reader standing at `as_of` would read, the two give the same answer.**

    They part on a filing that reader would *not* read: a same-day pair that disagrees under an
    announcement some **later** announcement of the same period supersedes. `filing_for` takes the
    later announcement and never consults the superseded one; this marks the period ambiguous
    anyway. That is deliberate and it predates `V2-P3-018` -- the same-day check was moved onto
    the triple precisely so a superseded pair could not be silently discarded, because whether it
    *was* discarded depended on the order the scan returned the rows in. Measured then, on one
    corpus of three rows in three write orders: raised, computed, computed. Fail-closed and
    order-independent is the pair worth having; strictly-equal-to-`value_of` and order-dependent
    is not. What `V2-P3-018` changed is the *price* of the divergence and not the divergence: it
    costs one security a coverage code where it used to cost the whole cross section a build.
    `tests/integration/panel/test_value_family.py::
    test_a_superseded_ambiguous_pair_still_codes_the_security_rather_than_taking_the_later_row`
    drives it.

    Comparison is on the tuple, so it is order-free in the other direction too: rows that all
    agree collapse whichever order they arrive in, and a triple with any disagreement is marked
    on whichever pair is reached first. The columns are returned in `columns`' own order rather
    than in a set's, so two runs over one triple name them identically.

    `None` is compared like any other cell, deliberately: an upstream cell empty on both rows is
    an agreement about a non-answer, which is exactly what `value_of` returns `None` for. The
    `nan` hazard `domain/financial_statements.py::_require_finite_values` names -- two rows
    identical apart from a `nan` in one column, which `==` would call a disagreement -- is closed
    one step earlier, in `_numeric`, and *has* to be: `domain/panel_batch.py` encodes the
    non-finite floats on purpose, so a partition can hold one.
    """
    if stated == cells:
        return ()
    return tuple(
        name for name, before, after in zip(columns, stated, cells, strict=True) if before != after
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

    **A `nan` or an infinity is refused rather than passed through**, and that is
    `domain/financial_statements.py::_require_finite_values` applied at the engine's own read, for
    the reason that function gives. `domain/panel_batch.py::_encode_column` encodes the non-finite
    floats **on purpose** -- "a missing or non-finite panel observation is ordinary data here" --
    so a partition may hold one, and the period axis's collapse is built on `==`. Two rows of one
    filing that are byte-identical apart from carrying `nan` in the same column would compare
    unequal and be reported as a disagreement the publisher never stated, which is a build refusal
    naming a fault that does not exist.

    **On the session axis the same refusal is a judgement rather than a measurement, and it
    tightens behaviour.** There is no collapse there, so nothing forces it; the argument is that a
    non-finite cell is poison for every window statistic and that this is the last place a message
    can still name the row it came from. What it costs if that argument is wrong is stated rather
    than absorbed: before this guard, `nan` reached the evaluator, came back out, and
    `FactorEvaluator`'s rule made it `undefined_value` for **that one security**; now it refuses
    the whole cross section. Fail-closed in a direction nothing measured -- see this module's
    docstring section "The value family", which records it as a behaviour change rather than as a
    detail of this function. Nothing this repository writes can produce one --
    `providers/tushare.py::_finite_number` refuses a non-finite cell at the boundary -- which is
    why the guard is driven directly in `tests/unit/test_factor_value_family.py::
    test_a_non_finite_stored_cell_is_refused_because_the_collapse_is_built_on_equality`.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FactorEngineError(
            f"{dataset}.{column} holds {type(value).__name__} for {subject} on "
            f"{point.isoformat()}; a factor input must be a stored number, and this column "
            "cannot be one of this factor's required_fields"
        )
    number = float(value)
    if not math.isfinite(number):
        raise FactorEngineError(
            f"{dataset}.{column} holds {number!r} for {subject} on {point.isoformat()}; a factor "
            "input must be a finite number, and two rows of one filing carrying this in the same "
            "column would never compare equal, so the period axis would report a disagreement "
            "the publisher never stated"
        )
    return number


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
    *and should not*; history before ambiguity, because a window that cannot be formed has no
    filing to be ambiguous about; ambiguity before nullity, because both are answers about the
    inputs and only one of them is repaired by a fetch -- a reader told `input_missing` about a
    security whose filing is *also* contradictory would re-fetch and get the same two rows back;
    nullity before arithmetic, because an evaluator is only ever handed a complete window.

    **`ambiguous_filing` is scoped to the window and not to the security** (`V2-P3-018`). A
    filing the publisher contradicted itself about at a period this factor's window does not
    reach did not enter this number, and coding the security for it would report a defect in an
    answer that does not depend on it -- the same argument `not_in_universe` rests on, one axis
    over. So the mark is `window & ambiguous`, pooled across the period-axis datasets exactly the
    way `_points_held` pools their points: dataset A's ambiguity at period P and dataset B's row
    at P are one window slot, and the slot is unanswerable if either side of it is.

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
    if _ambiguous_points(subject, readings=readings).intersection(periods):
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="ambiguous_filing",
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


def _ambiguous_points(subject: str, *, readings: Mapping[str, _DatasetReading]) -> frozenset[date]:
    """Every report period at which some dataset served this security two filings that disagree.

    `_points_held`'s shape for `V2-P3-018`'s mark, and pooled across datasets for its reason: a
    factor reading `income` and `balancesheet` over one period window has one window, so an
    ambiguity in either endpoint at a period in it makes that slot unanswerable. No axis argument,
    because `_read_dataset` only ever records these on the period axis -- the session axis still
    refuses a second row outright -- and an argument that has one legal value is a parameter
    nothing can vary.
    """
    marked: set[date] = set()
    for reading in readings.values():
        marked.update(reading.ambiguous_points_by_subject.get(subject, frozenset()))
    return frozenset(marked)


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
        "observation_digest": [manifest.observation_digest] * len(inputs),
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

    ## Every build this read returns is checked against the manifest that describes it

    `V2-P3-019`. The manifest partition is read too, and `_refuse_rows_that_are_not_the_answers_
    their_manifest_addresses` holds the two against each other in both directions before a single
    observation is handed back. Without it, `factor_obs_<key>_v<n>` was an ordinary Parquet file
    that anything could edit -- a half-finished sync, an exploratory script writing to the wrong
    path, silent disk corruption -- and the edit surfaced as *a factor report that looks normal*
    rather than as an error: measured, sixteen flipped signs moved `mean_ic` from `+1.0` to
    `-1.0` under a byte-identical `experiment_id` at exit code 0.

    **The extra read is one partition per year and it is not optional.** The manifest partition
    holds one row per `(build, input partition)` -- three orders of magnitude smaller than the
    observations it describes -- and making it conditional would mean a store missing it read
    faster than a store that has it, which is a fail-open with a performance argument in front of
    it. A caller that wants the rows without the check does not exist: `read_visible_at` is what
    the raw partition is for, and this function's whole contract is that what it returns is what
    the build wrote.
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
    _refuse_rows_that_are_not_the_answers_their_manifest_addresses(
        found,
        dataset=dataset,
        build_of=lambda row: row.manifest_id,
        addressed={
            manifest.manifest_id: manifest.observation_digest
            for manifest in load_factor_manifests(store, definition, years=years, as_of=as_of)
        },
        digest_of=observation_digest,
    )
    return tuple(found)


_Row = TypeVar("_Row")


def _refuse_rows_that_are_not_the_answers_their_manifest_addresses(
    rows: Sequence[_Row],
    *,
    dataset: str,
    build_of: Callable[[_Row], str],
    addressed: Mapping[str, str],
    digest_of: Callable[[Sequence[_Row]], str],
) -> None:
    """Hold a stored cross section against the build manifest that addresses it, both ways.

    Three refusals, and each one is a state the store can actually be in rather than a defensive
    triple. All three were measured on a real store in `V2-P3-019` before this function existed;
    two of the five tampers tried there are caught one layer down by
    `partition_row_count_mismatch` and `partition_file_unreadable`, and the three below are the
    three that were not caught by anything at all.

    - **A build whose rows do not hash to its `observation_digest`** is the flipped sign, the
      edited cell and the restated coverage code. The digest is over `(subject, coverage, value)`
      -- see `domain/factor.py::FactorBuildManifest.observation_digest` for what that does and
      does not reach -- and it is a *hashed* manifest field, so editing the stored digest to
      match a tampered partition moves `manifest_id` and is refused by `_manifest_from_rows`
      instead.
    - **A build the manifest partition describes and the observation partition does not hold** is
      a whole cross section deleted. It is caught here rather than only by the storage plane's
      row-count check, because that check compares the file against a catalog row and this one
      compares it against the build's own account of itself.
    - **Rows filed under a `manifest_id` no visible build claims** is the mirror image: an
      `as_of`'s worth of answers with no build behind them. Nothing that reads this partition can
      say what parameters produced them, which is the state `V2-P3-002`'s manifest exists to make
      impossible.

    The comparison is by build rather than over the whole read, and that is what makes it usable
    at all: `read_visible_at` filters on `available_time`, every row of one build carries that
    build's own `as_of` in all four clocks, so a build is either wholly visible or wholly absent
    and its stored rows are exactly the cross section its digest was taken over. A read narrowed
    to some years simply sees fewer builds; it never sees half of one.
    """
    by_build: dict[str, list[_Row]] = {}
    for row in rows:
        by_build.setdefault(build_of(row), []).append(row)
    orphans = sorted(set(by_build) - set(addressed))
    if orphans:
        raise FactorEngineError(
            f"{dataset} holds rows filed under build(s) {orphans[:5]}"
            f"{'...' if len(orphans) > 5 else ''} that no visible manifest claims; the answers "
            "are there and nothing stored says what computed them"
        )
    for build, declared in sorted(addressed.items()):
        held = by_build.get(build)
        if held is None:
            raise FactorEngineError(
                f"{dataset} is missing every observation of build {build}, which its manifest "
                "partition still describes; a build is written with its cross section, and a "
                "partition holding one without the other was changed behind the store"
            )
        stored = digest_of(held)
        if stored != declared:
            raise FactorEngineError(
                f"{dataset} build {build} addresses the cross section {declared!r} and the "
                f"stored rows hash to {stored!r}; these {len(held)} row(s) are not the answers "
                "that build produced. A factor report computed from them would be a number "
                "nothing in this store stands behind -- rebuild over this as_of rather than "
                "reading it"
            )


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
        observation_digest=str(head["observation_digest"]),
        inputs=tuple(
            FactorInputRef(
                dataset=str(item["input_dataset"]),
                year=int(str(item["input_year"])),
                partition_content_hash=str(item["input_partition_hash"]),
                visible_row_count=int(str(item["input_visible_rows"])),
                withheld_row_count=int(str(item["input_withheld_rows"])),
            )
            for item in sorted(
                rows,
                key=lambda cells: (str(cells["input_dataset"]), int(str(cells["input_year"]))),
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

SOURCE_CENSUS_COLUMN_PREFIX: Final[str] = "source_census_"

SOURCE_CENSUS_COLUMNS: Final[tuple[str, ...]] = tuple(
    f"{SOURCE_CENSUS_COLUMN_PREFIX}{code}" for code in FACTOR_COVERAGE_ORDER
)
"""One stored count per **raw** coverage code the transform consumed, derived from the vocabulary.

`PROCESSED_CENSUS_COLUMNS`' argument applied to the axis it does not cover. That tuple's own
reason is that a transform whose cross section was too thin "is byte-indistinguishable in storage
from one that ran and imputed nothing -- unless the counts are stored", and the same sentence is
true one column over and was not acted on: `census_source_not_computed` is a single cell in front
of five raw codes, so a partition written over a cross section in which one security's filing
contradicted itself is byte-identical to one in which that security's arithmetic was undefined.
`V2-P3-018`'s entire argument -- that an ambiguity is not a hole and that re-fetching does not
repair it -- therefore survived on the raw plane and died on this one, and a reader of
`factor_procmn_*` could not answer "how many securities here have no value because the publisher
contradicted itself".

**A separate prefix rather than `FACTOR_CENSUS_COLUMN_PREFIX`**, which is the one place this
family departs from the two censuses above sharing one. Those two live in different *datasets*, so
one prefix collides with nothing; these two live in the **same row**, and
`census_input_missing` beside `source_census_input_missing` would be two different questions one
substring apart. The prefix names the axis rather than the dataset, which is what a reader
scanning one manifest row needs.

Six columns and not the thirty of a cross-tab, because the missing-value policy is a function
from raw code to action and `MISSING_VALUE_COLUMNS` already stores that function on the same row;
see `ProcessedFactorPanel.source_coverage_census`.
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
    "processed_observation_digest",
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
    *SOURCE_CENSUS_COLUMNS,
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

- **The eleven head columns** are `FactorTransformManifest`'s own fields (minus `schema_version`),
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

_TRANSFORM_MANIFEST_HEAD_COLUMNS: Final[tuple[str, ...]] = TRANSFORM_MANIFEST_DATA_COLUMNS[:11]
"""The eleven columns `FactorTransformManifest` is reassembled from -- an audit handle, not a
run-time one.

**Nothing in `src/` reads this.** `_transform_manifest_from_row` zips
`TRANSFORM_MANIFEST_PANEL_COLUMNS` against the row and addresses every cell by name, so the
hashed fields could sit anywhere in the tuple and the decoder would not notice. Its one consumer
is `tests/unit/test_factor_transform_rules.py::
test_the_stored_head_columns_are_exactly_the_hashed_manifests_own_fields`, which reconciles the
slice against `FactorTransformManifest`'s own field set -- so a twelfth manifest field, or a
hashed field that stopped being stored, fails there instead of at the first read-back. That is
what keeps `11` from being a number somebody has to remember; it is not a claim that the column
*order* is load-bearing. It has already earned its keep once: `V2-P3-019` added
`processed_observation_digest` and this slice went red until the column list followed.

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
        "processed_observation_digest": "string",
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
        **_kinds(SOURCE_CENSUS_COLUMNS, "integer"),
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
        ambiguous_filing="exclude",
        input_missing="fill_cross_sectional_median",
        undefined_value="exclude",
    ),
    min_cross_section=100,
)
"""The single registered transform, and every one of its settings is a stated judgement.

**`ambiguous_filing="exclude"` is `V2-P3-018`'s declaration and the only one of the four legal
actions this spec could take.** `fill_cross_sectional_median` is what the neighbouring
`input_missing` declares and is right there: a null cell is a hole and the median of the peers is
what a fill is for. This code is not a hole -- the publisher stated the number twice, and
`domain/financial_statements.py` measured pairs whose two candidates straddle zero
(`income.ebit` -7,579,086 against +3,427,524, `cashflow.free_cashflow` +316,026,934 against
-294,173,456) -- so a median is a third number the publisher did not state, on one side of a sign
boundary the data does not settle. `fill_neutral` is the same objection with the neutral point
substituted for the median.

`refuse` is the one that has to be ruled out by measurement rather than by argument, and it is:
`apply_factor_transform` raises when the cross section contains a code declared `refuse`, so at
the ambiguity rates `V2-P3-010`'s probe recorded -- 8.51% of `income`'s filings, 0.95% of
`balancesheet`'s, **17.11%** of `cashflow`'s, 11.80% of `fina_indicator`'s -- a `refuse` here
would reproduce the whole-build refusal `V2-P3-018` exists to remove, one plane down, on
essentially every real whole-market cross section. The raw tier would answer per security and the
processed tier would then throw the answer away.
`tests/unit/test_factor_transform_rules.py::
test_the_shipped_policy_would_refuse_a_whole_cross_section_for_one_ambiguous_filing_if_it_said_so`
drives that counterfactual rather than leaving it as a claim about a number.

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
        "score a listing on data it does not have. A security whose filing the publisher stated "
        "twice and disagreed with itself about is excluded rather than filled or refused: a "
        "median is a third number nobody stated -- the measured pairs straddle zero -- and "
        "refusing would put the whole-build refusal V2-P3-018 removed back one plane down, on "
        "every cross section holding one of the 8.51% to 17.11% of filings that are ambiguous. A "
        "null input is filled with the median of "
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

Word for word what the spec's own `summary` field carried when `V2-P3-014`'s prerequisite moved
it here, so that diff showed a relocation rather than an edit. It has been edited once since, by
`V2-P3-018`, which gave the policy a fifth field and therefore gave this prose a fifth sentence;
a note that still described a four-field policy would be the drift `validate_notes` cannot see.
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
    degeneracy is `stdev == 0`, a `stdev` that is not finite, or a cross section whose sums are
    not representable at all, and floating-point overflow makes all three reachable on values that
    are very much not equal -- `[1e308, -1e308]` sums a variance to `inf`, and
    `[1.7e308, 1.7e308, 1.0]` cannot even be added up.
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


def _fsum_or_none(values: Iterable[float]) -> float | None:
    """`math.fsum`, with a sum that is not representable reported rather than raised.

    **`math.fsum` raises where `sum` returns `inf`**, which is the same trap `delta * delta` is
    chosen over `delta ** 2` to avoid one line down and, for four releases, the same trap only
    half-avoided. `fsum` is exact: it keeps the partial sums it has not yet been able to fold, and
    when one of *those* leaves the double range it raises `OverflowError("intermediate overflow in
    fsum")` rather than saturating. `math.fsum([1e308, 1e308])` raises; `math.fsum([inf, inf])`
    returns `inf`.

    So the guard below it -- `not math.isfinite(scale)` -- could only ever catch the second shape,
    and the first reached callers as a bare builtin out of an arithmetic helper. Both of these are
    contract-admissible cross sections (`validate_factor_observation` admits any finite float, and
    `sys.float_info.max` is finite), and both reach `_standardize_zscore` on the shipped
    `cross_section_standard/v1`, which standardizes by `zscore`:

    - `[1.7e308, 1.7e308, 1.0]` overflows on the **mean**, before any variance exists.
    - `[1e154, -1e154, 1e154, -1e154]` has a mean of exactly zero and four squared deviations of
      `1e308`, so it overflows on the **variance** -- past the `delta * delta` choice, which only
      ever governed a single product.

    `None` is the answer rather than an exception because a cross section too wide for a z-score is
    the `degenerate_cross_section` this standardizer exists to report: `_Standardizer` already
    defines `None` as "this method found nothing to order", and there is no reading under which the
    other answer is better, because a z-score needs a location and a scale and neither one of these
    cross sections has a representable pair. See
    `tests/unit/test_factor_transform_rules.py::
    test_a_z_score_whose_mean_overflows_is_degenerate_rather_than_an_overflowerror`.
    """
    try:
        return math.fsum(values)
    except OverflowError:
        return None


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

    **Both sums go through `_fsum_or_none` for that same sentence's sake**, because the
    `delta * delta` choice governed one product and `math.fsum` raises on its own account -- see
    that function for the two cross sections that escaped this one as bare `OverflowError`s.
    """
    count = len(values)
    total = _fsum_or_none(values)
    if total is None:
        return None
    mean = total / count
    squares = _fsum_or_none((value - mean) * (value - mean) for value in values)
    if squares is None:
        return None
    scale = math.sqrt(squares / count)
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

        **This census alone cannot answer why a security has no processed value**, and that is not
        a defect of the counting but of the axis: `source_not_computed` is one cell standing in
        front of the *five* raw codes `FactorCoverage` spends five members drawing apart, so two
        cross sections differing only in whether a security was `ambiguous_filing` or
        `undefined_value` produce byte-identical counts here. `source_coverage_census()` is the
        other axis and the two are reported side by side rather than crossed; see it for why.
        """
        census: dict[str, int] = dict.fromkeys(PROCESSED_COVERAGE_ORDER, 0)
        for observation in self.observations:
            census[observation.coverage] += 1
        return MappingProxyType(census)

    def source_coverage_census(self) -> Mapping[str, int]:
        """How many rows came from each **raw** coverage code, including the zeros.

        The second axis of one panel, and it exists because the first one loses the distinction
        `V2-P3-018`'s whole argument rests on. `ambiguous_filing` is not a hole -- the publisher
        stated the number and contradicted itself, so **re-fetching returns the same two rows** --
        while `input_missing` is a hole a fetch repairs and `undefined_value` is a definition
        question. `ProcessedFactorObservation.source_coverage` keeps all five on the row, and
        `ProcessedCoverage.source_not_computed` is a single cell in front of them, so a census over
        the processed vocabulary alone reports the three as one number. Measured: two cross
        sections of five securities differing only in one security's raw code are identical across
        every cell of `coverage_census()` and across every column of the stored transform manifest,
        which is `PROCESSED_CENSUS_COLUMNS`' own stated failure -- "byte-indistinguishable in
        storage ... unless the counts are stored" -- holding for this axis and not yet applied to
        it.

        **Two censuses of six and five cells rather than one cross-tab of thirty**, and the cross
        product is recoverable rather than sacrificed: the missing-value policy is a *function*
        from raw code to action, and `MISSING_VALUE_COLUMNS` stores that function beside these
        counts on the same manifest row. So a reader holding one row knows both which raw codes
        the cross section carried and what this build did with each, and the thirty-cell table is
        an arithmetic away. What no pair of marginals can recover is the joint distribution under
        a whole-panel code -- `insufficient_cross_section` and `degenerate_cross_section` are
        decided for every security at once, so under either the processed census is a single cell
        of `len(observations)` and this one is still the raw cross section's own shape, which is
        exactly the reading a reader wants there.

        Keyed by `FACTOR_COVERAGE_ORDER` and therefore including `computed`, which is not padding:
        `census_processed` counts the rows that came through the pipeline and this counts the rows
        that had a measurement to bring, and the two differ by exactly the measured rows a
        whole-panel refusal swallowed.
        """
        census: dict[str, int] = dict.fromkeys(FACTOR_COVERAGE_ORDER, 0)
        for observation in self.observations:
            census[observation.source_coverage] += 1
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
        processed_observation_digest=_UNSEALED_PROCESSED_DIGEST,
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
        return _seal_processed_panel(
            _uniform_processed_panel(
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
        return _seal_processed_panel(
            _uniform_processed_panel(
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
    return _seal_processed_panel(
        ProcessedFactorPanel(
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
    )


_UNSEALED_PROCESSED_DIGEST: Final[str] = "prc_unsealed"
"""What a draft transform manifest carries until `_seal_processed_panel` addresses its output.

`_UNSEALED_MANIFEST_ID`'s twin one tier up, for the identical circularity: `processed_
observation_digest` is a field of `FactorTransformManifest`, every processed row carries the
`transform_manifest_id` that field moves, and `apply_factor_transform` has three exits that each
build a whole panel. Rather than restructure all three to compute their rows before their
manifest, each returns a *draft* -- a real panel whose manifest addresses everything except its
own answers -- and the single seal below closes it. A placeholder that no digest function can
produce, so a draft that escaped would be recognisable rather than plausible.
"""


def _seal_processed_panel(draft: ProcessedFactorPanel) -> ProcessedFactorPanel:
    """Close a draft transform: address its answers, then re-stamp its rows with that address.

    The transform plane's half of `V2-P3-019`. `FactorTransformManifest` already hashed
    `source_observation_digest` -- what the *raw* rows it consumed were -- and had nothing at all
    to say about the rows it produced, so `factor_proc_<key>_v<n>` was in exactly the position
    `factor_obs_<key>_v<n>` was measured in: an ordinary Parquet file whose values could be
    edited without moving `transform_manifest_id`, `experiment_id`, or anything a reader sees.
    The processed tier is one of the three `openalpha factor run` reports on, so an unsealed one
    is a third of the answer that nothing stands behind.

    Both halves are here rather than at the three exits because a seal applied in three places is
    a seal that can be forgotten in one, which is the argument `write_factor_panels` makes about
    running every guard before the first write.
    """
    # Reconstructed rather than `model_copy(update=...)`, which skips validation: the sealed
    # manifest is what every downstream identity is derived from, so it goes through the same
    # constructor a first build does.
    manifest = FactorTransformManifest(
        **draft.manifest.model_dump(
            exclude={"transform_manifest_id", "processed_observation_digest"}
        ),
        processed_observation_digest=processed_observation_digest(draft.observations),
    )
    manifest_id = manifest.transform_manifest_id
    return replace(
        draft,
        manifest=manifest,
        observations=tuple(
            replace(row, transform_manifest_id=manifest_id) for row in draft.observations
        ),
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

    **Two censuses and not one**, on two axes of the same rows: `PROCESSED_CENSUS_COLUMNS` counts
    what this transform produced and `SOURCE_CENSUS_COLUMNS` counts what it was given. The second
    is the same argument as the first applied where it had not been -- `source_not_computed` is
    one cell in front of five raw codes, so without it a cross section narrowed by a
    self-contradictory filing and one narrowed by an undefined arithmetic write identical rows.

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
    source_census = panel.source_coverage_census()
    columns: dict[str, list[object]] = {
        "transform_id": [manifest.transform_id],
        "transform_key": [manifest.transform_key],
        "transform_version": [manifest.transform_version],
        "source_factor_id": [manifest.source_factor_id],
        "source_factor_key": [manifest.source_factor_key],
        "source_factor_version": [manifest.source_factor_version],
        "source_manifest_id": [manifest.source_manifest_id],
        "source_observation_digest": [manifest.source_observation_digest],
        "processed_observation_digest": [manifest.processed_observation_digest],
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
        **{
            f"{SOURCE_CENSUS_COLUMN_PREFIX}{code}": [source_census[code]]
            for code in FACTOR_COVERAGE_ORDER
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
    _refuse_rows_that_are_not_the_answers_their_manifest_addresses(
        found,
        dataset=dataset,
        build_of=lambda row: row.transform_manifest_id,
        addressed={
            manifest.transform_manifest_id: manifest.processed_observation_digest
            for manifest in load_factor_transform_manifests(
                store, definition, years=years, as_of=as_of
            )
            if manifest.transform_id == spec.transform_id
        },
        digest_of=processed_observation_digest,
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
        processed_observation_digest=str(cells["processed_observation_digest"]),
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
