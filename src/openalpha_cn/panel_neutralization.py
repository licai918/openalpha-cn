"""The cross-sectional neutralisation (`V2-P3-004`): the third tier of D8, on the panel plane.

`V2-P3-003` turns a raw factor cross section into a winsorized, standardized,
missing-value-resolved one. This issue takes *that* and removes what an industry membership and a
market capitalisation explain, storing the residual beside both of them and over neither. D8's
`raw / processed / neutralized` is therefore three record types in three pairs of datasets, and
this module owns the third pair.

## Why this is its own module, when `V2-P3-003` argued for staying in `panel_factors.py`

`panel_factors.py`'s docstring took that trade for the processed plane and then named this issue
as the point to re-take it: *"`V2-P3-004`'s neutralisation is the next issue to add to this file
and the point at which the trade should be re-taken with a third transform's worth of evidence
rather than a second's."* The evidence arrived and it points the other way, on one argument the
`003` review did not have and two it did:

- **The new one, and it is the deciding one: this is the first thing on the factor plane that
  reads a *foreign* dataset.** `compute_factor` and `apply_factor_transform` read the factor's own
  inputs and its own output; a neutralisation needs `index_member_all` and `daily_basic`, so its
  store-side half calls `panel_ingest.load_industry_histories` and
  `panel_ingest.load_daily_valuations`. Putting that in `panel_factors.py` would widen the
  *factor engine's* reach to two datasets it has no business knowing about -- and
  `tests/unit/test_panel_ingest_import_isolation.py` records dependencies at **package**
  granularity (`openalpha_cn.panel_ingest` is already in `_ALLOWED_FACTOR_DEPENDENCIES`), so that
  widening would be **invisible to the audit that exists to see widenings**. A separate module
  turns it into a new row in `PANEL_MODULE_DEPENDENCIES` that a reviewer has to read and approve.
  That is the repository's own preference, stated in that file: a new row must be argued for in
  the module's own docstring, which is what this paragraph is.

  **The exact strength of that, because it is easy to overstate.** What the split bought is one
  reviewed row -- a human read, once. It did **not** buy a continuing detector, because the new
  row is package-granular too: `openalpha_cn.panel_ingest` now sits in
  `_ALLOWED_NEUTRALIZATION_DEPENDENCIES` as well, so a later edit that adds
  `load_daily_bars` or `load_index_weights` to *this* module widens its dataset reach with nothing
  going red. The audit sees **modules**, never datasets. So the honest claim is that the widening
  was made visible **at the moment it happened**, not that it is policed from here on.

  **That gap is closed and the paragraph above is kept as the record of why.** The dataset-level
  allowlist it says does not exist now does: `tests/unit/test_panel_ingest_import_isolation.py`'s
  `RESEARCH_PLANE_SEAM_IMPORTS` records the twelve names this module takes across the seam so a
  thirteenth is a diff, and its `RESEARCH_PLANE_DATASETS` records that this module names **no**
  upstream dataset in its own source and reaches exactly `daily_basic` and `index_member_all`
  through `load_daily_valuations` and `load_industry_histories`. The widening this paragraph
  describes as unpoliced is driven on a mutated copy of this file by
  `tests/unit/test_panel_ingest_import_isolation.py::
  test_a_loader_added_to_the_neutralisation_turns_both_tables_red`, and the difference between the
  two fields -- which is this issue's entire claim -- by
  `tests/unit/test_panel_ingest_import_isolation.py::
  test_the_neutralisation_reaches_its_two_foreign_datasets_only_across_the_seam`. Nothing in this
  module declares anything for that audit's benefit: it reads the `*_DATASET` constants `domain/`
  already binds and the imports this file already writes.
- **The size, which is now indefensible rather than merely large.** `panel_factors.py` is 3,912
  lines and already the largest module in `src/`; this issue is ~1,000 more. A 4,900-line module
  is not a seam anybody re-takes later.
- **The seam the `003` review actually found is the one this split falls on.** That review
  measured which of the processed half's functions call a store: the contracts, the estimators,
  `apply_factor_transform` and both batch builders call none, and only the two loaders take the
  filtered read. This module has exactly that shape -- one store-free engine, two batch builders,
  one writer, two filtered readers and one assembler -- so splitting it out puts the whole
  store-touching group behind one reviewed allowlist entry rather than threading it through a
  module that is mostly arithmetic.

**Two allowlists are extended and neither is a guard relaxed.** `FILTERED_READ_CALLERS` gains
`panel_neutralization.py` because reading a neutralised partition back has the raw plane's
mid-year problem (a row's `available_time` is the `as_of` it was computed at, so a year of daily
cross sections has a `max_available_time` in December and `read_if_ready` would refuse it at every
`as_of` inside the year). `PANEL_MODULE_DEPENDENCIES` gains a row naming
`openalpha_cn.domain`, `openalpha_cn.panel`, `openalpha_cn.panel_ingest` and
`openalpha_cn.panel_factors`. Both constants' own docstrings say that adding a name is a
deliberate act with a review attached, "which is the property this test exists to create".

## The dataset-name budget, computed rather than assumed

`MAX_IDENTIFIER_LENGTH` is 63 and `MAX_FACTOR_KEY_LENGTH` is 40, so the longer of this module's
two prefixes plus the longest declarable factor key plus `"_v999"` is
`len("factor_neutmn_") + 40 + len("_v999") = 14 + 40 + 5 = **59**`, four characters clear. That is
the *same* 59 the processed plane's `factor_procmn_` reaches, and it is not a coincidence: both
prefixes were chosen to be 14 characters because that is what the budget leaves.

The consequence is the processed plane's, unchanged: **the factor is the partition axis and the
neutralisation is a column.** A name carrying both keys would need at least
`6 + 40 + 5 + 1 + 5 = 57` characters before the neutralisation key got a single letter. So one
`factor_neut_<key>_v<n>` partition holds *every* neutralisation of that factor, and the two costs
`load_processed_factor_observations` documents apply verbatim -- a read of one neutralisation
opens the rows of all of them and filters in Python, and a year of one factor's residuals has to
reach `write_neutralized_factor_panels` in one call across every neutralisation of it.

## The regression, and why it is fifteen lines of arithmetic rather than a solver

The model is the factor value on a **complete set of industry dummies plus one market-cap
regressor, with no global intercept**. `domain/factor_neutralization.py` argues the identification
choice; what this module owns is that the residual of that model has a closed form, by the
Frisch-Waugh-Lovell theorem: subtract each industry's own mean from `y` and from `x`, fit one
slope through the origin on the demeaned pair, and the residual is
`(y - mean_y_g) - beta * (x - mean_x_g)`.

That is `O(n)` in two passes and it is what `_neutralize` does. **It was verified against a dense
least-squares reference rather than trusted**, and the reference is in the test file rather than
in `src/` because it is an instrument and not a product:
`tests/unit/test_factor_neutralization_rules.py::_dense_residuals` forms the design matrix, the
Gram matrix and the right-hand side, solves by Gaussian elimination with partial pivoting, and
subtracts the fit. On a 5,534-name cross section over 31 industries the two agree to **8.88e-16**,
and so do the two identification choices (`31 dummies` and `intercept + 30 dummies`).

**Two-pass with `math.fsum`, for `_standardize_zscore`'s measured reason.** The group means and
the slope's two sums are accumulated with `math.fsum` rather than `sum`, because a one-pass
`E[xy] - E[x]E[y]` form cancels catastrophically on a regressor whose values are large and close
together -- and `level` market caps in 10k CNY are exactly that shape, spanning 1e3 to 1e8 with
industry means in the same range.

**The normal equations are never formed, and the reason is measured.** On the level-capitalisation
design of the `_panel(7)` probe that comparison test drives, the Gram matrix of
`31 dummies + total_mv` has a diagonal spanning `151` to `3.55e17`, a ratio of **2.35e15** --
within a factor of ten of double precision's own epsilon. (`_panel(19)`, the probe the *other*
tests use, gives `149` to `2.05e17` and `1.37e15`; the seed is named because the number is a
property of it.) Under `log` the same ratio is 6.3e3. The closed form has no such matrix: its only
division is by the within-industry sum of squared deviations, which is one positive number that
`degenerate_design` tests directly. What is *not* claimed is that this rescued anything
measurable: the dense reference, solved with pivoting and `fsum`, still agreed to 4.44e-16 on the
raw-cap design, because a dummy block is orthogonal by construction and the effective conditioning
is far better than the diagonal suggests. So the closed form is chosen for its `O(n)` cost and its
absent matrix, and the conditioning is a reason to prefer it rather than a defect it was observed
to avoid.

## No numpy, no pandas -- and this is the workload ADR-0003 named

ADR-0003's Context said the factor layer "needs cross-sectional regression", its 2026-08-11
update said `V2-P3-004` is where that arrives, and its 2026-08-12 update said the same again. It
arrived. Measured at ADR-0002's stated panel scale -- 5,534 participants over 31 industries, one
`as_of`:

| step | time |
|---|---|
| the closed form (two passes, group means, one slope, 5,534 residuals) | **1.6 ms** |
| a dense least-squares solve of the same design, 31 dummies + cap | 143.5 ms |
| the same, intercept + 30 dummies + cap | 152.7 ms |

The closed form is **90x** the dense solve it reproduces to 8.88e-16, and 1.6 ms is **0.07%** of
the 2.24 s `compute_factor` that has to run before it, 4% of the 36.7 ms
`apply_factor_transform` between them, and 0.003% of the smallest of ADR-0003's five
`write_panel_batch` measurements after it. A numpy implementation could at best remove a quantity
three orders of magnitude below the step before it, in exchange for two runtime dependencies, an
explicit `float(...)`/`cast(...)` at every public boundary in the layer, the `NPY`/`PD`/`S` ruff
evaluation and the thread-count pinning ADR-0003's Consequence 6 requires. The runtime dependency
set stays at nine.

**The honest bound on that**: this is the regression D8 asks for and not every regression. A
multi-factor risk model with `k` correlated continuous regressors has no such closed form and
would need a real solve; ADR-0003's question would be genuinely open again there, and this
module's answer does not carry over to it.

## The visibility property, and exactly how it survives

`apply_factor_transform` takes a `FactorPanel` and **no store**, which is what makes its
point-in-time claim structural. That property is the thing a neutralisation most obviously
breaks: it needs two more panel datasets, and the obvious implementation gives the engine a
`PanelStore`.

It is not broken here, and the shape is stated at the strength it has:

- **`apply_factor_neutralization` takes no store either.** Its parameters are a
  `ProcessedFactorPanel`, a spec, an `IndustryMarketCapCrossSection`, and two provenance
  arguments. No `PanelStore`, no `as_of`, no universe, no `ReadinessRequirement` --
  `test_the_neutralisation_takes_no_store_and_therefore_no_second_visibility_rule` reads the
  signature and its annotations, the same instrument `V2-P3-003` used.
- **The second cross section is a value stamped with the instant it was read at**, and the engine
  refuses one whose `as_of` is not the panel's own. A cross section assembled later cannot be
  joined to an earlier panel by accident.
- **The engine refuses a cross section that does not cover the panel.** Every participant must
  appear in exactly one of the characteristic cross section's three collections, so a cross
  section assembled for a different universe -- or a different day -- is a refusal rather than a
  silently narrower regression. That is the guard the coverage codes would otherwise hide: a name
  the second cross section never heard of would look exactly like a name it had no industry for.
- **The store-side half opens no new door of its own, and `V2-P4-026` moved one of the two it
  uses.** `load_industry_market_cap_cross_section` is the only builder in `src/`, and it reads its
  two foreign datasets through `panel_ingest.load_industry_histories` and
  `panel_ingest.load_daily_valuations`. The first still takes `PanelStore.read_if_ready`, the
  **unfiltered** door that refuses a partition whose newest row post-dates `as_of` rather than
  filtering it; the second now takes the filtered door one session at a time, through
  `panel_ingest._read_visible_price_session`, under `panel_ingest.py`'s own
  `FILTERED_READ_CALLERS` grant. This module's entry is still spent entirely on reading its
  **own** output partitions back.

**What is not claimed.** A caller can hand-build an `IndustryMarketCapCrossSection`, stamp it with
the right `as_of` and populate it from rows that were not knowable then -- exactly as a caller can
hand-build a `FactorPanel` whose observations were never computed. Nothing at the arithmetic layer
can tell the two apart, because at that layer there is no store to re-derive them from. The
obstacle is the ordinary one and it is named rather than dressed up as impossibility.

## What is deliberately not here

**No factor family and no IC.** `V2-P3-009`..`013` and `V2-P3-005` own those. This module's
input is a `ProcessedFactorPanel` whatever produced it.

**No universe.** `load_industry_market_cap_cross_section` takes the securities to ask about as an
argument, `compute_factor`'s arrangement and for its reason: `stock_basic` has the same mid-year
readiness problem, and answering it here would put a second policy decision inside an engine.

**No `DATASET_CADENCE` entry.** The two datasets here are derived rather than fetched, so "how
fresh should this be" is a question about the build schedule.
`tests/unit/test_factor_neutralization_rules.py::
test_the_neutralised_planes_datasets_are_derived_and_therefore_have_no_cadence` pins it against a
live registry, so a factor added by `V2-P3-009`..`013` is covered without anybody extending a
list.

**No caller.** Nothing in `cli.py` and nothing in `scripts/` imports this module, so `V2-P3-004`
ships a library and **has never produced a real partition** -- every residual this repository has
seen was computed from a generated fixture. Wiring it is `V2-P3-014`'s (the three-tier report's)
work and the split is deliberate, but the consequence belongs here rather than in a commit
message: the guards below are argued from their inputs and driven from tests, and no operator has
run one against `index_member_all` and `daily_basic` as they actually arrive.

## The one thing a reader of a stored residual has to know before using it

**This section said "a residual is invisible for the whole of the year it covers" and `V2-P4-026`
retracted it.** Every clock on a stored row is still the *build's* `as_of`
(`neutralized_observation_batch`) -- that is right for a derived row and is unchanged. What is no
longer true is the second half: the build's `as_of` used to be unable to precede the `daily_basic`
partition's newest row, which is the year's last session, so a row about any day in year Y was
only visible at an `as_of` after Y had closed. `load_daily_valuations` now reads one session at a
time through `panel_ingest._read_visible_price_session`, so a residual can be built at a mid-year
`as_of` and `load_neutralized_factor_observations` returns it at that `as_of`.

**What a reader still has to know is weaker and is about the schedule, not the storage.** A
residual is visible from the instant its own build was run and not before -- that is the
point-in-time rule, and an `as_of` earlier than any build still reads **empty rather than an
error**, because this loader filters rows rather than refusing partitions. So a series built once
at year end is still one December instant, and nothing in the stored rows says which schedule
produced them. One refusal outside this module still bounds which schedules are reachable:
`index_member_all` is read whole partition, so a build cannot be run inside a membership year
whose newest assignment post-dates the `as_of`. That is
`KNOWN_NEUTRALIZATION_LIMITATIONS
.the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused`, it is not
fixable from this module, and it is filed as `V2-P4-027`.
"""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Final, Protocol

from openalpha_cn.domain.daily_prices import DailyValuation
from openalpha_cn.domain.factor import FactorDefinition, FactorNote
from openalpha_cn.domain.factor_neutralization import (
    INDUSTRY_LEVELS,
    MARKET_CAP_MEASURES,
    MARKET_CAP_SCALES,
    NEUTRALIZED_COVERAGE_CODES,
    NEUTRALIZED_COVERAGE_ORDER,
    PARTICIPATING_PROCESSED_CODES,
    PARTICIPATION_RULES,
    FactorNeutralizationManifest,
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
    FactorNeutralizationStatistics,
    IndustryMarketCapCrossSection,
    MarketCapMeasure,
    MarketCapScale,
    NeutralizedCoverage,
    NeutralizedFactorObservation,
    SecurityCharacteristic,
    build_industry_market_cap_cross_section,
    characteristic_digest,
    industry_code_of,
    industry_group_sizes,
    neutralized_observation_digest,
    processed_observation_digest,
    validate_neutralized_factor_observation,
)
from openalpha_cn.domain.factor_transform import (
    PROCESSED_COVERAGE_ORDER,
    ProcessedCoverage,
    ProcessedFactorObservation,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    IndustryAssignment,
    IndustryHorizonError,
    SecurityIndustryHistory,
)
from openalpha_cn.domain.panel_batch import (
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import PanelStore, PartitionRef
from openalpha_cn.panel_factors import (
    EVENT_TIME_COLUMN,
    FACTOR_CENSUS_COLUMN_PREFIX,
    FACTOR_PROVIDER_ID,
    FactorEngineError,
    ProcessedFactorPanel,
    _refuse_a_merge_that_lost_a_stored_build,
    _refuse_rows_that_are_not_the_answers_their_manifest_addresses,
    _refuse_to_drop_a_stored_build,
    appended_to_the_stored_year,
)
from openalpha_cn.panel_ingest import (
    load_daily_valuations,
    load_industry_histories,
    merge_panel_batches,
    split_panel_batch_by_year,
    write_panel_batch,
)

# --- the two datasets a neutralisation writes -----------------------------------------------------


FACTOR_NEUTRALIZED_DATASET_PREFIX: Final[str] = "factor_neut_"
FACTOR_NEUTRALIZATION_MANIFEST_DATASET_PREFIX: Final[str] = "factor_neutmn_"
"""The neutralised plane's two dataset-name prefixes, one pair per **factor**.

Deliberately short, and the shortness is the budget this module's docstring computes rather than
terseness for its own sake: the longer of the two plus `MAX_FACTOR_KEY_LENGTH` plus `"_v999"` is
`14 + 40 + 5 = 59` against `MAX_IDENTIFIER_LENGTH`'s 63.
`tests/unit/test_factor_neutralization_rules.py::
test_the_longest_legal_factor_key_still_names_a_legal_neutralised_dataset` builds that worst case
out of the constants rather than restating the arithmetic, so widening either one fails there
instead of at the first write.
"""

FACTOR_NEUTRALIZED_KIND: Final[str] = "neutralized_factor_observation"
FACTOR_NEUTRALIZATION_MANIFEST_KIND: Final[str] = "factor_neutralization_manifest"


class NeutralizationEngineError(RuntimeError):
    """Raised when the store or the wiring, rather than a value, refuses a neutralisation.

    A `RuntimeError` for `FactorEngineError`'s reason and along the seam
    `FactorTransformError`'s docstring draws: computing a neutralisation touches no store, so
    every refusal `apply_factor_neutralization` can raise is about a *value* and is a
    `FactorNeutralizationError`; writing one, reading one back, or assembling its second cross
    section out of two partitions is a store operation, and those refusals are this.

    **Subclassing `FactorEngineError` rather than `RuntimeError` directly**, so that a caller
    already writing `except FactorEngineError` around a factor build keeps catching a
    neutralisation's storage refusals unchanged -- the same courtesy `FactorTransformError` pays
    by subclassing `ValueError`. The two planes' write guards are literally shared
    (`_refuse_to_drop_a_stored_build` is `panel_factors`'), so a reader who had to catch two
    unrelated types for one write would be paying for a distinction that does not exist.
    """


def neutralized_factor_dataset(definition: FactorDefinition) -> str:
    """The panel dataset one factor's **neutralised** observations are filed under.

    Keyed by the factor and not by the neutralisation; see this module's docstring for the
    name-length arithmetic that forces it and the two costs that follow.
    """
    return f"{FACTOR_NEUTRALIZED_DATASET_PREFIX}{definition.key}_v{definition.version}"


def factor_neutralization_manifest_dataset(definition: FactorDefinition) -> str:
    """The panel dataset one factor's neutralisation manifests are filed under."""
    return f"{FACTOR_NEUTRALIZATION_MANIFEST_DATASET_PREFIX}{definition.key}_v{definition.version}"


NEUTRALIZED_OBSERVATION_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "neutralization_id",
    "neutralization_key",
    "neutralization_version",
    "value",
    "coverage",
    "industry_code",
    "neutralization_manifest_id",
    "source_factor_id",
    "source_transform_id",
    "source_transform_manifest_id",
    "source_coverage",
)
"""One stored neutralised observation, column by column.

`subject` and the four clocks come from `ColumnarPanelBatch`, exactly as they do for a raw and a
processed observation, and for a derived row every clock is the build's `as_of`.

The four `source_*` columns are what makes D8's "报告可比较 raw / processed / neutralized ... 而不
覆盖源观测" answerable from a stored row, and here the chain is two links rather than one:

- **`source_transform_manifest_id`** with the row's own `subject` and `as_of` is the exact key of
  the processed observation in `factor_proc_<key>_v<n>`, and *that* row's `source_manifest_id`
  with the same `subject` and `as_of` is the exact key of the raw observation. Both hops are
  performed as joins in `tests/integration/panel/test_factor_neutralizations.py` rather than
  described.
- **`source_transform_id`** is the transform's own identity, so a reader of this partition alone
  knows which of several transforms of one factor was neutralised without opening the processed
  partition.
- **`source_factor_id`** is the raw definition, so the same reader knows which factor.
- **`source_coverage`** is the processed row's own five-code marker, carried so that
  `not_a_participant` does not collapse "the factor had no value" into "the value was imputed and
  this build's participation rule excluded it".

**`industry_code` is the column that makes a stored residual interpretable at all**, and it is
not derivable from anything else in this partition: the number in `value` is a deviation from a
group mean, and the group is a fact about a *different* dataset on a day whose classification is
backfilled. A reader who cannot see the group is holding a residual whose meaning is a join away.

`neutralization_key` and `neutralization_version` sit beside the opaque `neutralization_id` for
the reason `factor_key` and `transform_key` sit beside theirs: a reader querying the partition
directly would otherwise need this build's registry to know what the rows are about.
"""

NEUTRALIZED_OBSERVATION_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *NEUTRALIZED_OBSERVATION_DATA_COLUMNS,
)

_NEUTRALIZED_COLUMN_KINDS: Final[Mapping[str, PanelColumnKind]] = MappingProxyType(
    {
        "neutralization_id": "string",
        "neutralization_key": "string",
        "neutralization_version": "integer",
        "value": "float",
        "coverage": "string",
        "industry_code": "string",
        "neutralization_manifest_id": "string",
        "source_factor_id": "string",
        "source_transform_id": "string",
        "source_transform_manifest_id": "string",
        "source_coverage": "string",
    }
)

NEUTRALIZED_CENSUS_COLUMNS: Final[tuple[str, ...]] = tuple(
    f"{FACTOR_CENSUS_COLUMN_PREFIX}{code}" for code in NEUTRALIZED_COVERAGE_ORDER
)
"""One stored count per declared neutralised coverage code, derived from the vocabulary.

`PROCESSED_CENSUS_COLUMNS`' argument one tier down, and sharper here because this plane has three
distinct ways to have no answer that all look identical in storage: a build whose cross section
was too thin, one whose entire market had no industry, and one whose industries were all below
the member floor each write a partition full of nulls. Only the counts tell them apart.

Sharing `FACTOR_CENSUS_COLUMN_PREFIX` with the two planes above is deliberate: these columns live
in a different dataset from both, so there is no collision, and one prefix is one fewer string
for a reader to learn.
"""

NEUTRALIZATION_MANIFEST_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "neutralization_id",
    "neutralization_key",
    "neutralization_version",
    "source_factor_id",
    "source_factor_key",
    "source_factor_version",
    "source_transform_id",
    "source_transform_manifest_id",
    "source_processed_digest",
    "characteristic_digest",
    "neutralized_observation_digest",
    "as_of_time",
    "code_commit",
    "industry_level",
    "market_cap_measure",
    "market_cap_scale",
    "participation",
    "min_industry_members",
    "min_cross_section",
    "industry_taxonomy",
    *NEUTRALIZED_CENSUS_COLUMNS,
    "participant_count",
    "industry_count",
    "smallest_industry_size",
    "largest_industry_size",
    "backfilled_industry_count",
    "market_cap_slope",
    "market_cap_dispersion",
    "residual_dispersion",
)
"""One row per neutralisation build. Three families, and only the first is in the content address.

- **The thirteen head columns** are `FactorNeutralizationManifest`'s own fields (minus
  `schema_version`), so `_neutralization_manifest_from_row` reassembles a build from them and
  checks that the identity it reproduces is the one the row was stored under. "Head" is a
  description of this literal rather than a requirement on it -- that decoder addresses cells by
  column *name* -- and what the grouping buys is that `_NEUTRALIZATION_MANIFEST_HEAD_COLUMNS` can
  be a slice instead of a second list.
- **The declared policy plus the taxonomy** -- the industry level, the capitalisation and its
  scale, the participation rule and the two floors -- is a projection of `neutralization_id`,
  stored for `TRANSFORM_MANIFEST_DATA_COLUMNS`' reason and a sharper one: a residual column is
  *uninterpretable* without knowing which size variable was removed, because a residual against
  `log(total_mv)` and one against `total_mv` differ by a fifth of a standard deviation and look
  identical in storage. `industry_taxonomy` is here rather than in the identity because it is a
  property of the data and is hashed inside `characteristic_digest`; see
  `FactorNeutralizationManifest` for that argument.
- **The statistics** are `FactorNeutralizationStatistics`: outputs, recorded and deliberately out
  of the content address. `smallest_industry_size` is the pair-member that makes
  `min_industry_members` *falsifiable* on a stored partition, and `market_cap_dispersion` is the
  slope's own denominator, so a build sitting near `degenerate_design` is visible before it falls
  over the edge.

Flat rather than nested for `FACTOR_MANIFEST_DATA_COLUMNS`' reason: a partition is a rectangle,
and the alternatives are a JSON blob in one column (which the panel plane exists to stop) or a
third dataset.
"""

NEUTRALIZATION_MANIFEST_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *NEUTRALIZATION_MANIFEST_DATA_COLUMNS,
)

_NEUTRALIZATION_MANIFEST_HEAD_COLUMNS: Final[tuple[str, ...]] = (
    NEUTRALIZATION_MANIFEST_DATA_COLUMNS[:13]
)
"""The thirteen columns `FactorNeutralizationManifest` is reassembled from -- an audit handle.

**Nothing in `src/` reads this.** `_neutralization_manifest_from_row` zips
`NEUTRALIZATION_MANIFEST_PANEL_COLUMNS` against the row and addresses every cell by name, so the
hashed fields could sit anywhere in the tuple and the decoder would not notice. Its one consumer
is `tests/unit/test_factor_neutralization_rules.py::
test_the_stored_head_columns_are_exactly_the_hashed_manifests_own_fields`, which reconciles the
slice against `FactorNeutralizationManifest`'s own field set -- so a fourteenth manifest field,
or a hashed field that stopped being stored, fails there instead of at the first read-back. It has
already earned its keep once: `V2-P3-019` added `neutralized_observation_digest` and this slice
went red until the column list followed.

A slice of the tuple above rather than a second list, so the two cannot drift.
"""


def _kinds(names: Sequence[str], kind: PanelColumnKind) -> dict[str, PanelColumnKind]:
    """One SQL kind for a family of derived column names, keeping the family and its type paired.

    `panel_factors._kinds`' twin, restated rather than imported: it is three lines, it is private
    there, and importing a private helper across a module seam to save three lines is a coupling
    with no buyer. `dict.fromkeys(names, kind)` infers `dict[str, str]` under mypy strict, so the
    alternative is a `cast` between a derived column list and its declared type.
    """
    return dict.fromkeys(names, kind)


_NEUTRALIZATION_MANIFEST_COLUMN_KINDS: Final[Mapping[str, PanelColumnKind]] = MappingProxyType(
    {
        "neutralization_id": "string",
        "neutralization_key": "string",
        "neutralization_version": "integer",
        "source_factor_id": "string",
        "source_factor_key": "string",
        "source_factor_version": "integer",
        "source_transform_id": "string",
        "source_transform_manifest_id": "string",
        "source_processed_digest": "string",
        "characteristic_digest": "string",
        "neutralized_observation_digest": "string",
        "as_of_time": "timestamp",
        "code_commit": "string",
        "industry_level": "string",
        "market_cap_measure": "string",
        "market_cap_scale": "string",
        "participation": "string",
        "min_industry_members": "integer",
        "min_cross_section": "integer",
        "industry_taxonomy": "string",
        **_kinds(NEUTRALIZED_CENSUS_COLUMNS, "integer"),
        "participant_count": "integer",
        "industry_count": "integer",
        "smallest_industry_size": "integer",
        "largest_industry_size": "integer",
        "backfilled_industry_count": "integer",
        "market_cap_slope": "float",
        "market_cap_dispersion": "float",
        "residual_dispersion": "float",
    }
)


# --- the neutralisation that ships ----------------------------------------------------------------


INDUSTRY_AND_SIZE: Final[FactorNeutralizationSpec] = FactorNeutralizationSpec(
    key="industry_and_size",
    version=1,
    industry_level="L1",
    market_cap_measure="total_mv",
    market_cap_scale="log",
    participation="measured_only",
    min_industry_members=2,
    min_cross_section=100,
)
"""The single registered neutralisation, and every one of its settings is a stated judgement.

It is not a default in the sense of "what you get if you say nothing": `apply_factor_neutralization`
takes the spec as a mandatory argument, and this is the one this build ships so that the shipped
configuration is exercised end to end rather than only probe specs invented by tests.

`min_industry_members = 2` is the only number here that is *forced* rather than chosen, and the
contract forces it: `FactorNeutralizationSpec` will not accept 1, because a one-member industry's
residual is exactly `0.0` for every factor and every slope. See that field's docstring for the
measurement, which also shows that excluding such a name leaves the slope bit-identical and every
other residual unmoved.
"""

INDUSTRY_AND_SIZE_NOTE: Final[FactorNote] = FactorNote(
    subject=INDUSTRY_AND_SIZE.qualified_key,
    summary=(
        "The conventional cross-sectional neutralisation: remove each SW2021 level-one "
        "industry's own mean and the part of what is left that the log of total market "
        "capitalisation explains, and store the residual. L1 because its 31 nodes give a mean "
        "group of about 178 names on a whole-market cross section, where L2's 134 nodes give 41 "
        "and L3's 346 give 16 -- and a group mean estimated from 16 names is mostly the names. "
        "total_mv rather than circ_mv because the whole company is what an industry peer group "
        "is compared on, and because the restricted-share fraction circ_mv excludes is a "
        "governance fact rather than a size one; the two are a different size variable rather "
        "than the same one twice, so this is a choice and not a formality. log rather than level "
        "because A-share capitalisations span four orders of magnitude inside one industry, and "
        "a level regressor makes the fit a statement about the largest handful. Both of those "
        "swaps move residuals by an amount this repository asserts as a floor rather than as a "
        "figure, because the only probe it can run offline is synthetic and the amount varies by "
        "an order of magnitude across its seed. measured_only because an imputed value is a "
        "number this repository made up and a group mean estimated partly from its own fills "
        "moves with the coverage rate. min_industry_members is 2, the contract's own floor: at 1 "
        "the residual is exactly 0.0 by construction and would be stored as though it were "
        "measured. min_cross_section is 100, matching the transform that must run first, because "
        "the eligible set here is strictly narrower -- a name needs a processed value and an "
        "industry and a capitalisation -- and a floor below the transform's would let a build "
        "produce numbers on a cross section the transform itself declined to standardize."
    ),
)
"""`INDUSTRY_AND_SIZE`'s prose, out of `neutralization_id`.

Word for word what the spec's own `summary` field carried until this change, so the diff shows a
relocation rather than an edit -- **including the retraction `V2-P3-004`'s review had to make
against the contract's own rule.** That field's docstring recorded the exception and gave it an
expiry (`V2-P3-014`); the expiry is spent here instead, by removing the field the rule was about.
Editing this string moves no identity at all now, which is what makes the rule unnecessary rather
than waived -- see `domain/factor.py::FactorNote`.
"""

FACTOR_NEUTRALIZATIONS: Final[FactorNeutralizationRegistry] = FactorNeutralizationRegistry(
    (INDUSTRY_AND_SIZE,), notes=(INDUSTRY_AND_SIZE_NOTE,)
)
"""Every neutralisation this build declares, and the prose about it.

`V2-P3-014`'s three-tier report extends both.
"""


# --- the market-cap regressor ---------------------------------------------------------------------


class _Regressor(Protocol):
    """The size half of the design: one positive capitalisation in, one regressor value out.

    A `Protocol` over a table rather than two `if` branches so that
    `_refuse_neutralization_table_drift` has something to reconcile against `MARKET_CAP_SCALES` at
    import -- `_WINSORIZERS`' arrangement, and for its measured reason: a declared scale with no
    entry raises `KeyError` from a dict lookup at the first cross section that uses it, in
    production, with a message that names neither the scale nor the spec.
    """

    def __call__(self, market_cap: float) -> float: ...


def _level_regressor(market_cap: float) -> float:
    """The capitalisation itself, in the units `daily_basic` serves it in (10k CNY)."""
    return market_cap


def _log_regressor(market_cap: float) -> float:
    """The natural log of the capitalisation.

    No guard for a non-positive argument, and the reason is now the true one rather than the one
    this docstring used to give. It used to say that `build_industry_market_cap_cross_section`
    refuses a capitalisation that is not finite and strictly positive, so `math.log` here has no
    domain error to have, and that a second check would be a branch nothing can reach. The builder
    does refuse one -- but it is not the only door. `SecurityCharacteristic` and
    `IndustryMarketCapCrossSection` both say in their own docstrings that they validate nothing, so
    a hand-assembled cross section reaches this function past the builder, and `math.log(0.0)`
    raised `ValueError("math domain error")` from inside a list comprehension in
    `apply_factor_neutralization` -- a bare builtin out of a public boundary.

    The guard is `_refuse_a_capitalisation_that_is_not_one`, and it is at the engine rather than
    here because *this* function has no subject to name and no reason to fire under
    `market_cap_scale="level"`, where the same row is the same fault. See that function, and
    `tests/integration/panel/test_factor_neutralizations.py::
    test_a_hand_built_capitalisation_of_zero_is_refused_by_name_under_both_scales`.
    """
    return math.log(market_cap)


_REGRESSORS: Final[Mapping[MarketCapScale, _Regressor]] = MappingProxyType(
    {
        "level": _level_regressor,
        "log": _log_regressor,
    }
)


class _CapReader(Protocol):
    """The measure half: one stored `daily_basic` row in, the declared capitalisation out.

    `None` means the row does not carry this measure. It cannot happen on a row the write path
    accepted -- `domain/daily_prices.py` refuses a null `total_mv` or `circ_mv`, in that module's
    own words because "a null one silently drops a name from a regression" -- so this return type
    exists for the row shape a hand-written partition can still produce, and the engine turns it
    into `market_cap_missing` rather than into a `TypeError` several frames away.
    """

    def __call__(self, valuation: DailyValuation) -> float | None: ...


def _total_market_cap(valuation: DailyValuation) -> float | None:
    return valuation.total_mv


def _circulating_market_cap(valuation: DailyValuation) -> float | None:
    return valuation.circ_mv


_MARKET_CAP_READERS: Final[Mapping[MarketCapMeasure, _CapReader]] = MappingProxyType(
    {
        "total_mv": _total_market_cap,
        "circ_mv": _circulating_market_cap,
    }
)


def _refuse_neutralization_table_drift(
    regressors: Mapping[MarketCapScale, _Regressor],
    readers: Mapping[MarketCapMeasure, _CapReader],
    participation: Mapping[str, frozenset[str]],
    coverage_order: Sequence[str],
    levels: Collection[str],
) -> None:
    """Refuse a vocabulary with a member no branch implements, and a branch with no member.

    `panel_factors._refuse_transform_table_drift`'s argument applied to this plane's four closed
    sets, with the same measured failure behind it: `PANEL_BUILD_TARGETS` gained keys whose
    branches did not exist and the command answered exit 0 with an empty partition list. The shape
    here would be worse than an empty success -- a declared `MarketCapScale` with no regressor
    raises `KeyError` at the first cross section that uses it.

    Five checks rather than one, because they fail differently and a reader has to know which: a
    scale with no implementation or an implementation with no declared scale; a capitalisation
    measure with no reader; a participation rule with no entry; a census order that has drifted
    from the vocabulary it restates; and an industry level with no membership column behind it.

    Every input is an argument rather than a module global, so all five failure directions are
    drivable from a test. An audit whose only call site is the one that passes is an audit nobody
    has seen fail, which is the shape `_refuse_table_drift` earned a third test for.
    """
    # The implemented set is widened to `set[str]` rather than left as a set of the `Literal` it
    # is keyed by: mypy reads `frozenset[str] != set[Literal[...]]` as a non-overlapping
    # comparison and refuses it, which would make the audit's own equality unwritable.
    implemented = {str(key) for key in regressors}
    if implemented != MARKET_CAP_SCALES:
        raise FactorEngineError(
            f"the market cap scale vocabulary and its table disagree: "
            f"{sorted(MARKET_CAP_SCALES - implemented)} are declared with no entry and "
            f"{sorted(implemented - MARKET_CAP_SCALES)} are implemented with nothing declaring "
            "them. A declared scale with no branch behind it fails at the first cross section "
            "that asks for it"
        )
    measures = {str(key) for key in readers}
    if measures != MARKET_CAP_MEASURES:
        raise FactorEngineError(
            f"the market cap measure vocabulary and its reader table disagree: "
            f"{sorted(MARKET_CAP_MEASURES - measures)} are declared with no reader and "
            f"{sorted(measures - MARKET_CAP_MEASURES)} are read with nothing declaring them. A "
            "declared measure with no reader would put every security into market_cap_missing on "
            "a partition that carries the column"
        )
    rules = {str(key) for key in participation}
    if rules != PARTICIPATION_RULES:
        raise FactorEngineError(
            f"the participation rules are {sorted(PARTICIPATION_RULES)} and the table names "
            f"{sorted(rules)}; a rule with no entry admits nothing and would silently produce an "
            "empty regression"
        )
    if set(coverage_order) != NEUTRALIZED_COVERAGE_CODES or len(set(coverage_order)) != len(
        coverage_order
    ):
        raise FactorEngineError(
            f"the neutralised census order is {list(coverage_order)} and the declared codes are "
            f"{sorted(NEUTRALIZED_COVERAGE_CODES)}; the order decides a census key order and a "
            "stored column list, and two copies of a closed set drift"
        )
    if set(levels) != INDUSTRY_LEVELS:
        raise FactorEngineError(
            f"the declared industry levels are {sorted(INDUSTRY_LEVELS)} and this build can read "
            f"{sorted(set(levels))} off a membership row; a level with no column behind it is a "
            "spec that validates and then cannot be applied"
        )


_refuse_neutralization_table_drift(
    _REGRESSORS,
    _MARKET_CAP_READERS,
    PARTICIPATING_PROCESSED_CODES,
    NEUTRALIZED_COVERAGE_ORDER,
    INDUSTRY_LEVELS,
)


# --- the arithmetic -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Fit:
    """One solved regression: the residual per subject, and the three numbers that describe it."""

    residuals: Mapping[str, float]
    slope: float
    dispersion: float
    """The within-industry sum of squared deviations of the regressor -- the slope's denominator."""


def _population_stdev(values: Sequence[float]) -> float:
    """The population standard deviation, two-pass with `math.fsum`.

    Restated rather than imported from `panel_factors`, where it is a module-private helper
    written for a different reason (it exists there so a test can pin the z-score's estimator).
    Two-pass and `fsum` for that function's measured reason: the one-pass `E[x^2] - E[x]^2` form
    cancels catastrophically on values that are large and close together, and comes out negative
    as often as not. Population rather than sample, because the cross section at one `as_of` **is**
    the population; a `ddof=1` correction would compensate for a sampling that did not happen.
    """
    if not values:
        raise FactorEngineError(
            "the standard deviation of an empty residual set has no value; a build with no "
            "residuals reports residual_dispersion as None rather than asking for one"
        )
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - mean) * (value - mean) for value in values) / len(values))


def _neutralize(
    subjects: Sequence[str],
    groups: Sequence[str],
    regressor: Sequence[float],
    values: Sequence[float],
) -> _Fit | None:
    """The residual of `values ~ industry dummies + regressor`, in closed form, or `None`.

    ## The closed form, and why it is the OLS residual rather than an approximation of one

    By the Frisch-Waugh-Lovell theorem, the residual of a regression on `[D, x]` -- where `D` is a
    complete set of group indicators -- equals the residual of the *demeaned* `y` on the demeaned
    `x`. So:

    1. `mean_y[g]`, `mean_x[g]` -- each group's own means. One pass.
    2. `dy = y - mean_y[g]`, `dx = x - mean_x[g]`. The industry dummies are now fully absorbed.
    3. `beta = sum(dx * dy) / sum(dx * dx)`. One slope through the origin.
    4. `residual = dy - beta * dx`.

    That is `O(n)` with no matrix anywhere. Verified against a dense least-squares solve of the
    same design in `tests/unit/test_factor_neutralization_rules.py`: agreement to **8.88e-16** on
    a 5,534-name cross section over 31 industries, and the dense solve takes 143.5 ms against this
    function's 1.6 ms.

    ## `None` means the design is degenerate, and it is returned by the arithmetic

    `sum(dx * dx)` is zero exactly when the regressor has no within-industry variation -- every
    member of every group shares one capitalisation -- and then the slope is `0 / 0`. It is
    returned as `None` rather than decided by the caller from a `min == max` test, which is
    `_Standardizer`'s distinction between a rule and a guess: the degeneracy is a property of the
    *demeaned* regressor, and a cross section whose raw capitalisations are all distinct can still
    have zero within-industry dispersion (two industries of two names each, both pairs sharing a
    value). A non-finite dispersion counts as degenerate too, for `_standardize_zscore`'s reason:
    floating-point overflow makes `inf` reachable on values that are very much not equal.

    ## `math.fsum`, three times

    The group means and both of the slope's sums are accumulated with `math.fsum` rather than
    `sum`, because `level` capitalisations in 10k CNY span 1e3 to 1e8 within one industry and a
    naive accumulation of their squared deviations loses the low bits of every early term.
    """
    grouped_y: dict[str, list[float]] = {}
    grouped_x: dict[str, list[float]] = {}
    for group, x_value, y_value in zip(groups, regressor, values, strict=True):
        grouped_y.setdefault(group, []).append(y_value)
        grouped_x.setdefault(group, []).append(x_value)
    mean_y = {key: math.fsum(items) / len(items) for key, items in grouped_y.items()}
    mean_x = {key: math.fsum(items) / len(items) for key, items in grouped_x.items()}
    deviations_y = [value - mean_y[group] for group, value in zip(groups, values, strict=True)]
    deviations_x = [value - mean_x[group] for group, value in zip(groups, regressor, strict=True)]
    dispersion = math.fsum(item * item for item in deviations_x)
    if dispersion <= 0.0 or not math.isfinite(dispersion):
        return None
    covariance = math.fsum(
        left * right for left, right in zip(deviations_x, deviations_y, strict=True)
    )
    slope = covariance / dispersion
    if not math.isfinite(slope):
        return None
    residuals = {
        subject: dy - slope * dx
        for subject, dy, dx in zip(subjects, deviations_y, deviations_x, strict=True)
    }
    if any(not math.isfinite(value) for value in residuals.values()):
        return None
    return _Fit(residuals=MappingProxyType(residuals), slope=slope, dispersion=dispersion)


# --- the neutralised result -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class NeutralizedFactorPanel:
    """One neutralisation of one transform of one factor at one `as_of`.

    `ProcessedFactorPanel`'s shape one tier down, and `built_at` is here rather than on
    `FactorNeutralizationManifest` for the same reason it is there rather than on
    `FactorTransformManifest`: the wall clock is recorded (it becomes the partition's
    `ColumnarPanelBatch.fetched_at`) and kept out of the content address, so re-applying the same
    neutralisation to the same processed build reproduces its `neutralization_manifest_id` --
    which is what makes a rebuild writable past `_refuse_to_drop_a_stored_build` at all.
    """

    definition: FactorDefinition
    spec: FactorNeutralizationSpec
    manifest: FactorNeutralizationManifest
    observations: tuple[NeutralizedFactorObservation, ...]
    statistics: FactorNeutralizationStatistics
    industry_taxonomy: str
    built_at: datetime

    @property
    def as_of(self) -> datetime:
        return self.manifest.as_of

    def coverage_census(self) -> Mapping[str, int]:
        """How many rows carry each neutralised coverage code, including the zeros.

        Every declared code is present with a count, `ProcessedFactorPanel.coverage_census()`'s
        reason: a report reads "0 thin_industry" rather than inferring it from an absent key.
        """
        census: dict[str, int] = dict.fromkeys(NEUTRALIZED_COVERAGE_ORDER, 0)
        for observation in self.observations:
            census[observation.coverage] += 1
        return MappingProxyType(census)

    def values(self) -> Mapping[str, float]:
        """Every subject that has a residual.

        One method rather than `ProcessedFactorPanel`'s two, and the asymmetry is the vocabulary's:
        this plane imputes nothing, so there is no `imputed`/`measured` distinction to expose and
        `values()` is already the measured set. A caller that wants the *reasons* the others have
        no number reads `coverage_census()`.
        """
        return MappingProxyType(
            {
                observation.subject: observation.value
                for observation in self.observations
                if observation.value is not None
            }
        )

    def industries(self) -> Mapping[str, str]:
        """Every subject that was regressed, and the group whose mean was removed from it."""
        return MappingProxyType(
            {
                observation.subject: observation.industry_code
                for observation in self.observations
                if observation.industry_code is not None
            }
        )


# --- applying -------------------------------------------------------------------------------------


def apply_factor_neutralization(
    panel: ProcessedFactorPanel,
    spec: FactorNeutralizationSpec,
    characteristics: IndustryMarketCapCrossSection,
    *,
    code_commit: str,
    built_at: datetime,
) -> NeutralizedFactorPanel:
    """Regress one processed cross section on industry and size, and keep the residual.

    ## The five arguments, and the one that is not here

    - **`panel`** is the factor half of the input: a `ProcessedFactorPanel`, so the values being
      neutralised are the ones D8 says come third.
    - **`spec`** is mandatory and has no default, `apply_factor_transform`'s rule: a defaulted
      neutralisation policy is one whose stored numbers depend on a decision nobody recorded.
    - **`characteristics`** is the industry and size half, as a **value**. It is not a store, and
      the difference is this function's whole point-in-time argument -- see this module's
      docstring and `IndustryMarketCapCrossSection`. Its `as_of` must equal the panel's, and every
      participant must appear in exactly one of its three collections.
    - **`code_commit`** is provenance the panel plane cannot resolve for itself (no top-level
      `panel_*` module may import `runtime`, where `resolve_code_commit()` lives), and it has no
      default for the reason `V2-P0B-009` deleted `"development"`.
    - **`built_at`** is the wall clock, deliberately out of the content address.

    **There is no `store`, no `as_of` and no `date_timezone` parameter**, and all three absences
    are claims. The first two are `apply_factor_transform`'s, unchanged: the securities that
    participate in a cross-sectional statistic are exactly the ones the two input cross sections
    carry, and the `as_of` is the panel's own. The third is that this function resolves no date --
    `load_industry_market_cap_cross_section` already did, when it turned an instant into the
    session it read `daily_basic` for.

    ## The order of operations, and why each step is where it is

    1. **Refuse a panel that does not own its rows, and a cross section that does not cover it.**
       Before anything else, because both are statements about *which securities this is even
       about*, and a coverage code computed against the wrong universe is worse than a refusal.
    2. **Select the participants**: the processed rows whose coverage the declared participation
       rule admits. Every other row is `not_a_participant` and carries the processed code it came
       from.
    3. **Split the participants three ways** -- complete, `industry_missing`, `market_cap_missing`
       -- from the characteristic cross section's own three collections. A security is never
       inferred into one of these: the cross section states it.
    4. **Drop the industries below `min_industry_members`**, coding their members `thin_industry`.
       Before the floor check and before the regression, because a group that cannot be
       neutralised is not an eligible participant, and counting it toward `min_cross_section`
       would let a build clear a floor on names it was about to code.
    5. **Refuse a cross section thinner than `spec.min_cross_section`**, as a whole-panel
       `insufficient_cross_section` rather than as an exception: a thin cross section at some
       historical `as_of` is an answer about the market, and a build that raised on it could not
       backfill a year that contains one.
    6. **Fit and subtract.** One pass for the group means, one for the slope, one for the
       residuals. A degenerate design is the whole-panel `degenerate_design` code.

    **Step 4 before step 5 is load-bearing and is measured rather than argued.**
    `test_a_thin_industrys_members_do_not_count_toward_the_cross_section_floor` builds a cross
    section that clears `min_cross_section` only by counting names in one-member industries and
    asserts the build reports `insufficient_cross_section` -- the opposite order would produce
    residuals for a handful of names and report a full cross section.

    ## What determines the answers, and therefore what `neutralization_manifest_id` has to cover

    Every argument is either represented in `manifest.neutralization_manifest_id` or exempt with a
    written reason, and `tests/integration/panel/test_factor_neutralizations.py::
    test_every_determinant_is_either_in_the_identity_or_exempted_by_name`
    reads this function's own signature and fails on a parameter in neither list. A sixth
    parameter fails the audit until somebody classifies it.

    `panel` reaches the identity three times over -- through `source_transform_id`, through
    `source_transform_manifest_id`, and through `source_processed_digest`, which says what the
    numbers in it were. `characteristics` reaches it through `characteristic_digest`, which hashes
    the taxonomy, the level, the measure, every complete `(subject, industry, cap, backfilled)`
    tuple **and both residue lists** -- so a build whose industries moved, or whose residue grew,
    gets a new identity even when every parameter is unchanged.
    """
    observations = panel.observations
    if not observations:
        raise FactorEngineError(
            "apply_factor_neutralization needs a processed panel with at least one observation; "
            "an empty cross section produces an empty neutralised panel that is "
            "indistinguishable from one where nothing could be neutralised"
        )
    _refuse_a_processed_panel_that_does_not_own_its_observations(panel)
    _refuse_a_cross_section_that_is_not_this_panels(panel, spec, characteristics)
    _refuse_a_capitalisation_that_is_not_one(characteristics)
    manifest = FactorNeutralizationManifest(
        neutralization_id=spec.neutralization_id,
        neutralization_key=spec.key,
        neutralization_version=spec.version,
        source_factor_id=panel.definition.factor_id,
        source_factor_key=panel.definition.key,
        source_factor_version=panel.definition.version,
        source_transform_id=panel.manifest.transform_id,
        source_transform_manifest_id=panel.manifest.transform_manifest_id,
        source_processed_digest=processed_observation_digest(observations),
        characteristic_digest=characteristic_digest(characteristics),
        neutralized_observation_digest=_UNSEALED_NEUTRALIZED_DIGEST,
        as_of=panel.as_of,
        code_commit=code_commit,
    )
    # Both identities are read once, outside every loop below, and this is the third time this
    # repository has had to do it rather than the first. `neutralization_id` and
    # `neutralization_manifest_id` are pydantic `computed_field`s and are **not cached**: ADR-0003
    # records `compute_factor` re-hashing a build manifest 5,534 times inside a per-security loop,
    # and `V2-P3-003`'s review records the same trap arriving again inside the fix for something
    # else (24.4 ms against 0.19 ms hoisted). It arrived a third time here and was measured:
    # `_neutralized_row` read `spec.neutralization_id` per row, which is 5,534 `stable_model_id`
    # calls -- 31 ms of a 148 ms build. Passing both ids in is the fix.
    manifest_id = manifest.neutralization_manifest_id
    neutralization_id = spec.neutralization_id
    taxonomy = characteristics.taxonomy

    # Indexed once rather than searched per security, and that is the second half of the same
    # measurement. `IndustryMarketCapCrossSection.get` is a linear scan over the complete
    # characteristics and `without_industry` / `without_market_cap` are tuples, so the loop below
    # was `O(n^2)` in the cross section: 322 ms of a 394 ms profiled build at 5,534 names, 82% of
    # the whole call, against 1.6 ms for the regression it exists to feed. The public accessors
    # keep their linear form -- they are for a caller asking about one security -- and the engine
    # builds the index it needs.
    complete = {item.subject: item for item in characteristics.characteristics}
    no_industry = frozenset(characteristics.without_industry)
    no_market_cap = frozenset(characteristics.without_market_cap)

    eligible: list[tuple[ProcessedFactorObservation, SecurityCharacteristic]] = []
    coded: dict[str, NeutralizedCoverage] = {}
    for observation in observations:
        if not spec.admits(observation.coverage):
            coded[observation.subject] = "not_a_participant"
            continue
        if observation.subject in no_industry:
            coded[observation.subject] = "industry_missing"
            continue
        if observation.subject in no_market_cap:
            coded[observation.subject] = "market_cap_missing"
            continue
        found = complete.get(observation.subject)
        if found is None:
            raise FactorEngineError(
                f"{observation.subject} participates in this cross section and the characteristic "
                "cross section places it in none of its three collections; "
                "_refuse_a_cross_section_that_is_not_this_panels runs before this loop, so this "
                "state is unreachable through the contract and was produced by mutating one of "
                "the two panels after it"
            )
        eligible.append((observation, found))

    sizes = industry_group_sizes([item.industry_code for _, item in eligible])
    admitted = [
        (observation, found)
        for observation, found in eligible
        if sizes[found.industry_code] >= spec.min_industry_members
    ]
    for observation, found in eligible:
        if sizes[found.industry_code] < spec.min_industry_members:
            coded[observation.subject] = "thin_industry"

    if len(admitted) < spec.min_cross_section:
        return _seal_neutralized_panel(
            _uniform_neutralized_panel(
                panel,
                spec,
                manifest=manifest,
                manifest_id=manifest_id,
                taxonomy=taxonomy,
                coverage="insufficient_cross_section",
                statistics=_empty_statistics(len(admitted)),
                built_at=built_at,
            )
        )

    subjects = [observation.subject for observation, _ in admitted]
    groups = [found.industry_code for _, found in admitted]
    regressor = [_REGRESSORS[spec.market_cap_scale](found.market_cap) for _, found in admitted]
    # `value` is not None on every admitted row: the participation rule only ever admits
    # `PROCESSED_VALUE_CODES`, and `validate_processed_factor_observation` refuses one of those
    # carrying None at both of its call sites.
    values = [observation.value for observation, _ in admitted if observation.value is not None]
    if len(values) != len(admitted):
        raise FactorEngineError(
            f"{len(admitted) - len(values)} of this panel's admitted observations carry no value; "
            "the participation rule admits only the processed codes that carry one, so such a row "
            "reached the panel past both of validate_processed_factor_observation's call sites -- "
            "through a subclass that overrode __post_init__"
        )

    fit = _neutralize(subjects, groups, regressor, values)
    if fit is None:
        return _seal_neutralized_panel(
            _uniform_neutralized_panel(
                panel,
                spec,
                manifest=manifest,
                manifest_id=manifest_id,
                taxonomy=taxonomy,
                coverage="degenerate_design",
                statistics=_empty_statistics(len(admitted)),
                built_at=built_at,
            )
        )

    industries = dict(zip(subjects, groups, strict=True))
    rows = tuple(
        _neutralized_row(
            observation,
            neutralization_id=neutralization_id,
            manifest_id=manifest_id,
            value=fit.residuals.get(observation.subject),
            coverage=coded.get(observation.subject, "neutralized"),
            industry_code=industries.get(observation.subject),
        )
        for observation in observations
    )
    admitted_sizes = industry_group_sizes(groups)
    return _seal_neutralized_panel(
        NeutralizedFactorPanel(
            definition=panel.definition,
            spec=spec,
            manifest=manifest,
            observations=rows,
            statistics=FactorNeutralizationStatistics(
                participant_count=len(admitted),
                industry_count=len(admitted_sizes),
                smallest_industry_size=min(admitted_sizes.values()),
                largest_industry_size=max(admitted_sizes.values()),
                backfilled_industry_count=sum(1 for _, found in admitted if found.is_backfilled),
                market_cap_slope=fit.slope,
                market_cap_dispersion=fit.dispersion,
                residual_dispersion=_population_stdev(list(fit.residuals.values())),
            ),
            industry_taxonomy=taxonomy,
            built_at=built_at,
        )
    )


_UNSEALED_NEUTRALIZED_DIGEST: Final[str] = "nrs_unsealed"
"""What a draft neutralisation manifest carries until `_seal_neutralized_panel` addresses it.

`panel_factors._UNSEALED_PROCESSED_DIGEST` one tier up, for the identical circularity and with
the identical remedy: `neutralized_observation_digest` is a field of
`FactorNeutralizationManifest`, every residual row carries the `neutralization_manifest_id` that
field moves, and `apply_factor_neutralization` has several exits that each build a whole panel.
A placeholder no digest function can produce, so a draft that escaped would be recognisable.
"""


def _seal_neutralized_panel(draft: NeutralizedFactorPanel) -> NeutralizedFactorPanel:
    """Close a draft neutralisation: address its residuals, then re-stamp its rows with that
    address.

    `panel_factors._seal_processed_panel` at the top of the chain. Reconstructed rather than
    `model_copy(update=...)`, which skips validation: the sealed manifest is what every downstream
    identity is derived from, so it goes through the same constructor a first build does.
    """
    manifest = FactorNeutralizationManifest(
        **draft.manifest.model_dump(
            exclude={"neutralization_manifest_id", "neutralized_observation_digest"}
        ),
        neutralized_observation_digest=neutralized_observation_digest(draft.observations),
    )
    manifest_id = manifest.neutralization_manifest_id
    return replace(
        draft,
        manifest=manifest,
        observations=tuple(
            replace(row, neutralization_manifest_id=manifest_id) for row in draft.observations
        ),
    )


def _empty_statistics(participant_count: int) -> FactorNeutralizationStatistics:
    """The statistics of a build that produced no residual, with the count it did reach.

    `participant_count` is kept and everything else is zero or `None`, which is the same
    arrangement `apply_factor_transform` uses for its two whole-panel codes and for its reason: a
    build that found 4 eligible names under a floor of 100 and one that found 99 are different
    facts about the market, and a stored `0` for both would erase the one number a reader needs to
    tell them apart.
    """
    return FactorNeutralizationStatistics(
        participant_count=participant_count,
        industry_count=0,
        smallest_industry_size=0,
        largest_industry_size=0,
        backfilled_industry_count=0,
        market_cap_slope=None,
        market_cap_dispersion=None,
        residual_dispersion=None,
    )


def _neutralized_row(
    observation: ProcessedFactorObservation,
    *,
    neutralization_id: str,
    manifest_id: str,
    value: float | None,
    coverage: NeutralizedCoverage,
    industry_code: str | None,
) -> NeutralizedFactorObservation:
    """One neutralised row, carrying both pointers back to the tiers it came from.

    **Takes the two identities as strings rather than the `spec` and `manifest` they come off**,
    and that signature is the fix for a measured defect rather than a preference. Both are pydantic
    `computed_field`s and neither is cached, so a version of this function that read
    `spec.neutralization_id` re-canonicalised and re-hashed the spec once per security -- 5,534
    `stable_model_id` calls for a 5,534-name cross section, 31 ms of a 148 ms build. Requiring the
    caller to have hoisted them is what makes the trap unrepeatable here: there is no attribute on
    this function's parameters to read one from.
    """
    return NeutralizedFactorObservation(
        subject=observation.subject,
        as_of=observation.as_of,
        value=value,
        coverage=coverage,
        neutralization_id=neutralization_id,
        neutralization_manifest_id=manifest_id,
        source_factor_id=observation.source_factor_id,
        source_transform_id=observation.transform_id,
        source_transform_manifest_id=observation.transform_manifest_id,
        source_coverage=observation.coverage,
        industry_code=industry_code,
    )


def _uniform_neutralized_panel(
    panel: ProcessedFactorPanel,
    spec: FactorNeutralizationSpec,
    *,
    manifest: FactorNeutralizationManifest,
    manifest_id: str,
    taxonomy: str,
    coverage: NeutralizedCoverage,
    statistics: FactorNeutralizationStatistics,
    built_at: datetime,
) -> NeutralizedFactorPanel:
    """One row per source observation, all carrying a whole-panel code, no value and no group.

    `_uniform_processed_panel`'s judgement one tier down, unchanged: both whole-panel codes reach
    every observation, **including the ones that were not participants and the ones whose industry
    was missing**, because "there is no neutralised cross section at this `as_of`" is the dominant
    fact. Reporting `industry_missing` for some names while others said `degenerate_design` would
    suggest the first group could have been neutralised and merely lacked a classification, which
    is false -- nothing was neutralised. The reason each individual name had no processed value is
    not lost: `source_coverage` is on every row.
    """
    neutralization_id = spec.neutralization_id
    return NeutralizedFactorPanel(
        definition=panel.definition,
        spec=spec,
        manifest=manifest,
        observations=tuple(
            _neutralized_row(
                observation,
                neutralization_id=neutralization_id,
                manifest_id=manifest_id,
                value=None,
                coverage=coverage,
                industry_code=None,
            )
            for observation in panel.observations
        ),
        statistics=statistics,
        industry_taxonomy=taxonomy,
        built_at=built_at,
    )


def _refuse_a_processed_panel_that_does_not_own_its_observations(
    panel: ProcessedFactorPanel,
) -> None:
    """Refuse a processed panel whose rows do not all belong to the build its manifest describes.

    The **input-side** guard, and it is the third instance of one shape rather than a new idea:
    `panel_factors._refuse_a_source_panel_that_does_not_own_its_observations` is the same check on
    a `FactorPanel`, and `panel_factors._refuse_a_processed_panel_that_does_not_own_its_rows` is
    the output-side one on this very type. Written here rather than imported across the seam
    because that one is module-private, checks the *spec* as well (which this function has no
    business asserting about its input), and the failure it raises names the write boundary rather
    than the read.

    What it makes true: `(source_transform_manifest_id, subject, as_of)` on every neutralised row
    is a **proved** key of the processed partition rather than an assumed one.
    `apply_factor_transform` stamps one manifest on every row it produces, so this never fires on
    its output -- but this function's input is a public frozen dataclass anybody can construct,
    and a hand-assembled one with a mismatched row would store a residual whose provenance pointer
    names a processed build that does not hold it. That is a dangling reference written as a fact,
    which is worse than a missing one.
    """
    if panel.manifest.source_factor_id != panel.definition.factor_id:
        raise FactorEngineError(
            f"this processed panel's manifest describes factor "
            f"{panel.manifest.source_factor_id!r} and its definition is "
            f"{panel.definition.factor_id!r}; the two are produced together by "
            "apply_factor_transform and a panel where they disagree cannot be neutralised"
        )
    manifest_id = panel.manifest.transform_manifest_id
    transform_id = panel.manifest.transform_id
    as_of = panel.as_of
    stray = sorted(
        {
            item.subject
            for item in panel.observations
            if item.transform_manifest_id != manifest_id
            or item.transform_id != transform_id
            or item.as_of != as_of
        }
    )
    if stray:
        raise FactorEngineError(
            f"{stray[:5]}{'...' if len(stray) > 5 else ''} carry a transform, a build or an as_of "
            f"that is not this panel's ({transform_id}, {manifest_id}, {as_of.isoformat()}); "
            "every neutralised row points at its source with (source_transform_manifest_id, "
            "subject, as_of), and a row from another build would make that pointer name a build "
            "that does not hold it"
        )


def _refuse_a_cross_section_that_is_not_this_panels(
    panel: ProcessedFactorPanel,
    spec: FactorNeutralizationSpec,
    characteristics: IndustryMarketCapCrossSection,
) -> None:
    """Refuse a characteristic cross section that is not this build's, three ways.

    **This is the guard that keeps the coverage vocabulary honest**, and each of the three
    failures it catches would otherwise arrive as a *code* rather than as an error:

    - **A different `as_of`.** The industries and capitalisations of one day, joined to the factor
      values of another, produce residuals nothing is wrong with and everything is wrong about.
      Because both are stamped, this is an exact comparison rather than a tolerance.
    - **A different level or measure than the spec declares.** The cross section carries the level
      and the measure it was *assembled* for, and the spec declares the ones the build is *about*.
      A build declaring `L1` and handed L3 codes would file 346 groups under a manifest column
      saying 31, and every group mean would be right for a question nobody asked.
    - **A participant the cross section says nothing about.** This is the one with no other
      detector. `characteristics.get` returns `None` for a name with no industry, for a name with
      no capitalisation, and for a name it never heard of -- and the first two are declared codes
      while the third is a cross section assembled for a different universe. Without this check
      the third would be reported as `industry_missing`, so a cross section that covered half the
      market would look exactly like a market that was half unclassified.

    The membership test is over the participants rather than over every observation, because a
    non-participant is `not_a_participant` regardless of what any characteristic says about it --
    and requiring the cross section to cover names the build will never regress would force every
    caller to assemble industries for securities that had no factor value.
    """
    if characteristics.as_of != panel.as_of:
        raise FactorEngineError(
            f"this processed panel is at {panel.as_of.isoformat()} and the characteristic cross "
            f"section at {characteristics.as_of.isoformat()}; industries and market caps of one "
            "day joined to factor values of another produce residuals that look like answers, so "
            "the two instants must be the same one"
        )
    if characteristics.industry_level != spec.industry_level:
        raise FactorEngineError(
            f"this build declares industry_level {spec.industry_level!r} and the characteristic "
            f"cross section carries {characteristics.industry_level!r}; the stored manifest "
            "column comes off the spec and the group codes come off the cross section, so a "
            "mismatch files one taxonomy level's groups under another's name"
        )
    if characteristics.market_cap_measure != spec.market_cap_measure:
        raise FactorEngineError(
            f"this build declares market_cap_measure {spec.market_cap_measure!r} and the "
            f"characteristic cross section carries {characteristics.market_cap_measure!r}; "
            "total_mv is the whole company and circ_mv excludes the restricted shares, so the "
            "two are different size variables and the residuals against them are different "
            "numbers under one stored manifest column"
        )
    covered = set(characteristics.subjects())
    missing = sorted(
        observation.subject
        for observation in panel.observations
        if spec.admits(observation.coverage) and observation.subject not in covered
    )
    if missing:
        raise FactorEngineError(
            f"{missing[:5]}{'...' if len(missing) > 5 else ''} have a processed value this build "
            "would regress and the characteristic cross section places them in none of its three "
            f"collections ({len(covered)} securities). A name it never heard of is "
            "indistinguishable from one it has no industry for, so a cross section assembled for "
            "a different universe would be stored as a market that was partly unclassified"
        )


def _refuse_a_capitalisation_that_is_not_one(
    characteristics: IndustryMarketCapCrossSection,
) -> None:
    """Refuse a cross section carrying a capitalisation a listed company cannot have.

    **This is the check `_log_regressor` said could not be reached, and two of this module's own
    sentences said it could.** `_log_regressor` argued that `build_industry_market_cap_cross_
    section` refuses a non-positive capitalisation, so `math.log` has no domain error to have and a
    guard beside it would be a branch nothing can enter. The builder does refuse one. But
    `SecurityCharacteristic` is "a plain carrier with no validation of its own" and
    `IndustryMarketCapCrossSection`'s constructor "is not a boundary and validates nothing", both
    by their own docstrings, and `characteristic_digest` already carries a refusal written for
    exactly this path -- a hand-assembled cross section reaching the arithmetic past the builder.
    Two claims about one path, and the measurement settled it: `SecurityCharacteristic(...,
    market_cap=0.0)` constructs, and `_log_regressor(0.0)` raised a bare `ValueError("math domain
    error")` out of a list comprehension in `apply_factor_neutralization`, where no caller's
    `except FactorEngineError` could see it.

    **The rule is the builder's, restated where a value can arrive without it, and it is checked
    for both scales rather than only for `log`.** A guard that fired only under
    `market_cap_scale="log"` would make the reachability of a fault depend on a knob that is about
    output *shape*, which is the arrangement `_standardize_rank`'s docstring rejects one plane
    down; and `level` regressing on a zero or a negative capitalisation is not saved by being
    defined -- it is the same fault wearing a number, and it moves a slope and a dispersion that
    the manifest then stores as facts.

    Named subjects rather than a count, `characteristic_digest`'s precedent and for its stated
    reason: the reader is holding thousands of rows and needs the one. Truncated at five, the
    length rule the missing-participant refusal above already uses.
    """
    offending = sorted(
        item.subject
        for item in characteristics.characteristics
        if not math.isfinite(item.market_cap) or item.market_cap <= 0.0
    )
    if offending:
        raise FactorEngineError(
            f"{offending[:5]}{'...' if len(offending) > 5 else ''} carry a market capitalisation "
            "that is not finite and strictly positive; build_industry_market_cap_cross_section "
            "refuses such a row, so this cross section was assembled around it, and the log scale "
            "has no value at zero or below while the level scale would regress on a number that "
            "is not a capitalisation"
        )


# --- storing --------------------------------------------------------------------------------------


def _refuse_a_neutralized_panel_that_does_not_own_its_rows(panel: NeutralizedFactorPanel) -> None:
    """Refuse a neutralised panel whose four parts do not describe **one** application.

    The **output-side** mirror, and it exists because `V2-P3-003`'s review found the input-side
    guard alone was not enough one tier up -- a defect worth restating here because the shape is
    identical. The two batch builders below read one row's worth of facts off three different
    fields: `neutralization_manifest_batch` takes its thirteen head columns off `manifest` and its
    seven policy columns off `spec`, `neutralized_observation_batch` takes
    `neutralization_key`/`neutralization_version` off `spec` and everything else off the rows, and
    both take the dataset name off `definition`. So `dataclasses.replace(result, spec=other)` is
    accepted by every other guard and stores a manifest row whose `neutralization_id` names one
    build and whose `market_cap_scale` names another -- over a partition of the first one's
    residuals. `_neutralization_manifest_from_row`'s identity self-check cannot see it: the thirteen
    head columns it reassembles are internally consistent, and the policy columns are not among
    them.

    `industry_taxonomy` is checked too, and it is the field with the least obvious failure: it is
    stored as a manifest column and hashed only inside `characteristic_digest`, so a panel whose
    taxonomy label was replaced after the fact stores a vintage claim no identity contradicts.

    **Every identity is read once, above the row loop**, which is not style. `neutralization_id`
    and `neutralization_manifest_id` are pydantic `computed_field`s and are not cached, so reading
    one inside the comprehension re-hashes the model per security -- ADR-0003's recorded defect,
    which arrived a second time inside the fix for something else and cost 24.4 ms against 0.19 ms
    on a 5,534-name cross section.
    """
    spec = panel.spec
    manifest = panel.manifest
    definition = panel.definition
    neutralization_id = spec.neutralization_id
    manifest_id = manifest.neutralization_manifest_id
    as_of = manifest.as_of
    mismatched = [
        f"{column} is {stored!r} on the manifest and {declared!r} on the {origin}"
        for column, stored, declared, origin in (
            ("neutralization_id", manifest.neutralization_id, neutralization_id, "spec"),
            ("neutralization_key", manifest.neutralization_key, spec.key, "spec"),
            (
                "neutralization_version",
                str(manifest.neutralization_version),
                str(spec.version),
                "spec",
            ),
            ("source_factor_id", manifest.source_factor_id, definition.factor_id, "definition"),
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
            "this neutralised panel's manifest does not describe the neutralisation and factor it "
            f"carries ({'; '.join(mismatched)}); the stored identity columns come off the manifest "
            "and the stored policy columns come off the spec, so a panel where the two are not one "
            "application files one build's residuals under another's neutralization_id with a "
            "policy that never produced them -- and the head columns reassemble consistently, so "
            "no reader could tell. Apply the neutralisation rather than assembling its result"
        )
    if not panel.industry_taxonomy.strip():
        raise FactorEngineError(
            f"this neutralised panel carries industry_taxonomy {panel.industry_taxonomy!r}; the "
            "vintage is a stored manifest column and a blank one tells a reader of the partition "
            "nothing about which classification the group codes belong to"
        )
    stray = sorted(
        {
            row.subject
            for row in panel.observations
            if row.neutralization_id != neutralization_id
            or row.neutralization_manifest_id != manifest_id
            or row.source_transform_manifest_id != manifest.source_transform_manifest_id
            or row.source_transform_id != manifest.source_transform_id
            or row.as_of != as_of
        }
    )
    if stray:
        raise FactorEngineError(
            f"{stray[:5]}{'...' if len(stray) > 5 else ''} carry a neutralisation, a source build "
            f"or an as_of that is not this panel's ({neutralization_id}, {manifest_id}, "
            f"{manifest.source_transform_manifest_id}, {as_of.isoformat()}); every neutralised row "
            "is filed under the manifest row this same call writes, and a row naming another one "
            "is a pointer at a build this partition does not hold"
        )


def neutralized_observation_batch(panel: NeutralizedFactorPanel) -> ColumnarPanelBatch:
    """One neutralisation's residual rows as a columnar batch, ready for the store.

    Every clock on every row is the build's `as_of`, `processed_observation_batch`'s argument
    unchanged: a neutralised observation is a statement made *at* `as_of` out of information
    knowable *at* `as_of`, it became knowable at that instant, and it has no revision. The wall
    clock is the batch's `fetched_at`.

    **Every row is re-validated here**, which is the second call site
    `domain/factor_neutralization.py::validate_neutralized_factor_observation` exists for: a
    `__post_init__` is a method, a frozen dataclass with `slots=True` is still subclassable, and
    the write boundary is the last place a row that skipped the constructor's rules can be stopped
    before it is a column in a Parquet file. The rule that matters most here is the one that keeps
    the third tier as honest as the second: a `neutralized` row must come from a processed code
    that carried a value, and a coded row must carry no group.
    """
    _refuse_a_neutralized_panel_that_does_not_own_its_rows(panel)
    observations = panel.observations
    for observation in observations:
        validate_neutralized_factor_observation(observation)
    instants = tuple(observation.as_of for observation in observations)
    columns: dict[str, list[object]] = {
        "neutralization_id": [item.neutralization_id for item in observations],
        "neutralization_key": [panel.spec.key] * len(observations),
        "neutralization_version": [panel.spec.version] * len(observations),
        "value": [item.value for item in observations],
        "coverage": [item.coverage for item in observations],
        "industry_code": [item.industry_code for item in observations],
        "neutralization_manifest_id": [item.neutralization_manifest_id for item in observations],
        "source_factor_id": [item.source_factor_id for item in observations],
        "source_transform_id": [item.source_transform_id for item in observations],
        "source_transform_manifest_id": [
            item.source_transform_manifest_id for item in observations
        ],
        "source_coverage": [item.source_coverage for item in observations],
    }
    return ColumnarPanelBatch(
        provider_id=FACTOR_PROVIDER_ID,
        dataset=neutralized_factor_dataset(panel.definition),
        kind=FACTOR_NEUTRALIZED_KIND,
        as_of=panel.as_of,
        fetched_at=panel.built_at,
        status="success",
        subjects=tuple(item.subject for item in observations),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=instants,
            revision_time=instants,
        ),
        columns=tuple(
            PanelColumn(name, _NEUTRALIZED_COLUMN_KINDS[name], tuple(values))
            for name, values in columns.items()
        ),
        source_uri=None,
    )


def neutralization_manifest_batch(panel: NeutralizedFactorPanel) -> ColumnarPanelBatch:
    """One neutralisation build as a single row, keyed by `neutralization_manifest_id`.

    One row rather than one per input partition, because a neutralisation has exactly two inputs
    and both are addressed by a digest. The `subject` is the build, `trade_cal`'s precedent --
    `subject` is the entity the row is about -- and it buys the same write guard, since
    `PartitionCoverage.subjects` is then the set of neutralisation builds a partition holds and
    `_refuse_to_drop_a_stored_build` can see an overwrite that would destroy one without reading a
    single row.

    The census and the statistics are here for `factor_manifest_batch`'s reason: a build that
    neutralised nobody is otherwise indistinguishable in storage from one that regressed the whole
    market, and the objects that could say so speak only to a caller that thinks to ask.
    """
    _refuse_a_neutralized_panel_that_does_not_own_its_rows(panel)
    manifest = panel.manifest
    spec = panel.spec
    statistics = panel.statistics
    census = panel.coverage_census()
    columns: dict[str, list[object]] = {
        "neutralization_id": [manifest.neutralization_id],
        "neutralization_key": [manifest.neutralization_key],
        "neutralization_version": [manifest.neutralization_version],
        "source_factor_id": [manifest.source_factor_id],
        "source_factor_key": [manifest.source_factor_key],
        "source_factor_version": [manifest.source_factor_version],
        "source_transform_id": [manifest.source_transform_id],
        "source_transform_manifest_id": [manifest.source_transform_manifest_id],
        "source_processed_digest": [manifest.source_processed_digest],
        "characteristic_digest": [manifest.characteristic_digest],
        "neutralized_observation_digest": [manifest.neutralized_observation_digest],
        "as_of_time": [manifest.as_of],
        "code_commit": [manifest.code_commit],
        "industry_level": [spec.industry_level],
        "market_cap_measure": [spec.market_cap_measure],
        "market_cap_scale": [spec.market_cap_scale],
        "participation": [spec.participation],
        "min_industry_members": [spec.min_industry_members],
        "min_cross_section": [spec.min_cross_section],
        "industry_taxonomy": [panel.industry_taxonomy],
        **{
            f"{FACTOR_CENSUS_COLUMN_PREFIX}{code}": [census[code]]
            for code in NEUTRALIZED_COVERAGE_ORDER
        },
        "participant_count": [statistics.participant_count],
        "industry_count": [statistics.industry_count],
        "smallest_industry_size": [statistics.smallest_industry_size],
        "largest_industry_size": [statistics.largest_industry_size],
        "backfilled_industry_count": [statistics.backfilled_industry_count],
        "market_cap_slope": [statistics.market_cap_slope],
        "market_cap_dispersion": [statistics.market_cap_dispersion],
        "residual_dispersion": [statistics.residual_dispersion],
    }
    return ColumnarPanelBatch(
        provider_id=FACTOR_PROVIDER_ID,
        dataset=factor_neutralization_manifest_dataset(panel.definition),
        kind=FACTOR_NEUTRALIZATION_MANIFEST_KIND,
        as_of=manifest.as_of,
        fetched_at=panel.built_at,
        status="success",
        subjects=(manifest.neutralization_manifest_id,),
        timeline=TimelineColumns(
            event_time=(manifest.as_of,),
            available_time=(manifest.as_of,),
            ingested_time=(manifest.as_of,),
            revision_time=(manifest.as_of,),
        ),
        columns=tuple(
            PanelColumn(name, _NEUTRALIZATION_MANIFEST_COLUMN_KINDS[name], tuple(values))
            for name, values in columns.items()
        ),
        source_uri=None,
    )


def write_neutralized_factor_panels(
    store: PanelStore,
    panels: Sequence[NeutralizedFactorPanel],
    *,
    supersedes: Collection[str] = (),
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Write every neutralised panel and its manifest, merged into one partition per year.

    `write_processed_factor_panels`' shape, and every one of its arguments applies unchanged: a
    partition is replaced whole and has no append, so everything belonging to one `(dataset, year)`
    has to reach the store in one call; `supersedes` names the `neutralization_manifest_id`s this
    call is deliberately replacing; a `supersedes` entry no partition holds is refused rather than
    ignored, because a typo would silently turn the drop guard off for the write it arrived with;
    and every guard runs before the first write, so a refusal changes nothing at all.

    **Both drop guards are `panel_factors`' own, imported rather than restated**:
    `_refuse_to_drop_a_stored_build` for the catalog's stored build list, and
    `_refuse_a_merge_that_lost_a_stored_build` for the merge that `appended_to_the_stored_year`
    performed, which is the one that reaches the *observation* partition on this plane as on the
    other two (`V2-P4-073`). The alternative was a second copy of each that differs from the first
    in nothing but the file it lives in, and this repository's own rule against two copies of a
    closed set applies at least as strongly to two copies of a refusal: they would drift, and the
    direction they drift in is that one plane stops protecting a partition.
    `tests/unit/test_factor_neutralization_rules.py::
    test_the_drop_guard_is_the_same_object_the_factor_plane_uses` asserts object identity for
    both, so a later rename cannot fork them silently. What makes the sharing correct rather than
    merely convenient is that the facts enforced are identical -- a manifest partition's subject
    is a build id on all three planes, and an observation partition's builds are in its own
    `*_manifest_id` column on all three -- which those functions' own docstrings say.

    **One partition holds every neutralisation of one factor**, so the call that writes a year has
    to carry every `(neutralisation, as_of)` pair belonging to it. That follows from the
    dataset-name arithmetic in this module's docstring rather than from a choice, and it is
    *enforced* rather than documented: a second neutralisation written on its own would drop the
    first's build and the guard refuses it.
    """
    if not panels:
        raise FactorEngineError(
            "write_neutralized_factor_panels needs at least one panel; an empty write would be a "
            "call that reports success and stores nothing"
        )
    _refuse_two_neutralizations_of_one_build_at_one_as_of(panels)
    planned = [
        (
            year,
            appended_to_the_stored_year(
                store,
                yearly,
                year,
                build_column=(
                    SUBJECT_COLUMN_NAME
                    if yearly.kind == FACTOR_NEUTRALIZATION_MANIFEST_KIND
                    else "neutralization_manifest_id"
                ),
                identity_columns=("neutralization_id", EVENT_TIME_COLUMN),
                superseded=supersedes,
            ),
        )
        for batches in _neutralized_batches_by_dataset(panels)
        for year, yearly in split_panel_batch_by_year(
            merge_panel_batches(batches), date_timezone=date_timezone
        )
    ]
    superseded = set(supersedes)
    stored: list[tuple[ColumnarPanelBatch, int, frozenset[str]]] = []
    for year, appended in planned:
        if appended.batch.kind != FACTOR_NEUTRALIZATION_MANIFEST_KIND:
            continue
        existing = store.read_coverage(appended.batch.dataset, year)
        stored.append(
            (
                appended.batch,
                year,
                frozenset() if existing is None else frozenset(existing.subjects),
            )
        )
    unmatched = sorted(superseded - {build for _, _, builds in stored for build in builds})
    if unmatched:
        raise FactorEngineError(
            f"supersedes names {unmatched}, which no partition this write touches holds; a "
            "neutralization_manifest_id that matches nothing is a typo, and letting it through "
            "would turn the drop guard off for the write it arrived with"
        )
    for yearly, year, builds in stored:
        _refuse_to_drop_a_stored_build(yearly, year, builds=builds, superseded=superseded)
    for year, appended in planned:
        _refuse_a_merge_that_lost_a_stored_build(appended, year)
    return tuple(
        write_panel_batch(store, appended.batch, year=year, date_timezone=date_timezone)
        for year, appended in planned
    )


def _neutralized_batches_by_dataset(
    panels: Sequence[NeutralizedFactorPanel],
) -> tuple[tuple[ColumnarPanelBatch, ...], ...]:
    """One group of same-dataset batches per factor and per kind, observations before manifests.

    `_processed_batches_by_dataset`' shape, grouped by dataset **name** rather than by definition,
    so the grouping key is the same string the store files the partition under -- and here that
    matters for its reason, since several *neutralisations* of one factor legitimately produce the
    same dataset name and have to merge into one partition rather than race for it.
    """
    grouped: dict[str, list[ColumnarPanelBatch]] = {}
    for build in (neutralized_observation_batch, neutralization_manifest_batch):
        for panel in panels:
            batch = build(panel)
            grouped.setdefault(batch.dataset, []).append(batch)
    return tuple(tuple(batches) for batches in grouped.values())


def _refuse_two_neutralizations_of_one_build_at_one_as_of(
    panels: Sequence[NeutralizedFactorPanel],
) -> None:
    """Refuse a call that answers one `(neutralisation, source build, as_of)` question twice.

    Keyed by the three rather than by `neutralization_manifest_id`, and **every component is read
    off `panel.manifest`** -- which is `_refuse_two_applications_of_one_transform_at_one_as_of`'s
    own correction applied at birth rather than after a defect. That function first keyed off
    `panel.spec`, a *declaration*, while the rows a call stores carry the observations' own ids;
    two panels sharing a manifest and carrying two specs therefore keyed apart and both were
    written, storing two manifest rows under one id and twice as many observation rows as the
    cross section had names.

    The source build rather than the factor is the second component here, and that is a
    strengthening rather than a copy: two neutralisations of *different transforms* of one factor
    at one `as_of` are two legitimate rows, and keying on the factor alone would refuse them. Two
    of the *same* transform are a reader left to choose.
    """
    seen: dict[tuple[str, str, datetime], int] = {}
    for panel in panels:
        manifest = panel.manifest
        key = (
            manifest.neutralization_id,
            manifest.source_transform_manifest_id,
            manifest.as_of,
        )
        seen[key] = seen.get(key, 0) + 1
    repeated = sorted(
        f"{neutralization_id} of {build} at {as_of.isoformat()}"
        for (neutralization_id, build, as_of), count in seen.items()
        if count > 1
    )
    if repeated:
        raise FactorEngineError(
            f"this write carries more than one application of {repeated}; a second answer to one "
            "cross-section question would store two rows for every (subject, as_of, "
            "neutralisation) and leave a reader to choose between them. Supersede the earlier "
            "build instead"
        )


# --- reading the neutralised plane back --------------------------------------------------------


def neutralized_factor_requirement(
    definition: FactorDefinition, *, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What a neutralised partition must satisfy before its residuals may be read back.

    The same three waivers `processed_factor_requirement` takes and for exactly the same reasons:
    the dates here are the `as_of`s somebody chose to compute rather than the sessions an exchange
    was open, the subjects are the cross section the read is *for*, and a derived partition has no
    upstream to be stale against, so a bound invented here would refuse a sound historical
    backfill. `required_fields` is not waived: the eleven stored columns plus the subject are
    exactly what `load_neutralized_factor_observations` decodes, and a partition missing one would
    fail as a binder error rather than as a readiness verdict.
    """
    return ReadinessRequirement(
        dataset=neutralized_factor_dataset(definition),
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=NEUTRALIZED_OBSERVATION_PANEL_COLUMNS,
        max_staleness=None,
    )


def neutralization_manifest_requirement(
    definition: FactorDefinition, *, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What a neutralisation-manifest partition must satisfy before its builds may be read back."""
    return ReadinessRequirement(
        dataset=factor_neutralization_manifest_dataset(definition),
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=NEUTRALIZATION_MANIFEST_PANEL_COLUMNS,
        max_staleness=None,
    )


def load_neutralized_factor_observations(
    store: PanelStore,
    definition: FactorDefinition,
    spec: FactorNeutralizationSpec,
    *,
    years: Sequence[int],
    as_of: datetime,
) -> tuple[NeutralizedFactorObservation, ...]:
    """One neutralisation's stored residual rows, filtered to what was knowable at `as_of`.

    Through `read_visible_at` rather than `read_if_ready` for
    `load_processed_factor_observations`' reason, and it is the entirety of this module's claim on
    `FILTERED_READ_CALLERS`: a neutralised row's `available_time` is the `as_of` it was computed
    at, so a year partition holding a year of daily cross sections has a `max_available_time` in
    December and `read_if_ready` would refuse it at every `as_of` inside the year -- including the
    ones whose own rows are sitting in it.

    **What this module does about shortness, which is what the allowlist's docstring demands a new
    caller answer.** `PanelVisibleReadOutcome.withheld_row_count` is a number this loader could
    surface and deliberately does not, because it has no honest reading here: the rows withheld
    are *later `as_of`s of this same factor*, which is the point of the filter rather than a hole
    in the answer. The three domain rebuilders the allowlist warns about
    (`build_index_membership`, `load_industry_histories`, `build_stock_universe`) cannot tell a
    withheld row from an absent one because each reassembles an interval or a sequence; this
    loader reassembles nothing -- every row is independent, decoded on its own, and a missing
    `as_of` is a build that was never run rather than a gap in a structure.
    `load_factor_neutralization_manifests` is how a caller discovers which `as_of`s a year holds.

    **The neutralisation is a filter here and the factor is the dataset**, forced by the
    dataset-name arithmetic. The cost is the processed plane's, measured there: a read of one
    neutralisation opens the rows of every neutralisation of the factor and drops the others in
    Python, because `read_visible_at` projects columns and takes no predicate -- 4.9x at eight
    variants for the same answer on a 2,000-name cross section.
    """
    requirement = neutralized_factor_requirement(definition, years=years, as_of=as_of)
    dataset = requirement.dataset
    found: list[NeutralizedFactorObservation] = []
    for year in sorted(set(years)):
        outcome = store.read_visible_at(
            requirement,
            year=year,
            columns=(EVENT_TIME_COLUMN, *NEUTRALIZED_OBSERVATION_PANEL_COLUMNS),
        )
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.blocking_issues]}"
            )
        found.extend(
            row
            for row in (
                _neutralized_observation_from_row(cells, dataset=dataset) for cells in outcome.rows
            )
            if row.neutralization_id == spec.neutralization_id
        )
    _refuse_rows_that_are_not_the_answers_their_manifest_addresses(
        found,
        dataset=dataset,
        build_of=lambda row: row.neutralization_manifest_id,
        addressed={
            manifest.neutralization_manifest_id: manifest.neutralized_observation_digest
            for manifest in load_factor_neutralization_manifests(
                store, definition, years=years, as_of=as_of
            )
            if manifest.neutralization_id == spec.neutralization_id
        },
        digest_of=neutralized_observation_digest,
    )
    return tuple(found)


def load_factor_neutralization_manifests(
    store: PanelStore,
    definition: FactorDefinition,
    *,
    years: Sequence[int],
    as_of: datetime,
) -> tuple[FactorNeutralizationManifest, ...]:
    """Every neutralisation build of one factor stored in `years` and knowable at `as_of`.

    The read `write_neutralized_factor_panels`' refusal points at, and the reason it can point
    anywhere: a partition is replaced whole and a stored build may not be dropped, so a caller who
    wants to add an `as_of` -- or a second neutralisation -- to a year has to know what the year
    already holds. It also carries the discovery half `load_neutralized_factor_observations`
    needs, since a partition holds every neutralisation of the factor and a caller has to name one.

    **The policy, taxonomy and statistic columns are stored and are deliberately not reassembled
    here.** They are not fields of `FactorNeutralizationManifest`, so putting them back on one
    would either not fit or would move the identity of the manifest this function returns -- and
    the whole point of this read is that a reassembled build reproduces the identity it was stored
    under. `FACTOR_NEUTRALIZATIONS.by_id` is how a caller turns a `neutralization_id` back into
    the policy this build declares.
    """
    requirement = neutralization_manifest_requirement(definition, years=years, as_of=as_of)
    dataset = requirement.dataset
    found: list[FactorNeutralizationManifest] = []
    for year in sorted(set(years)):
        outcome = store.read_visible_at(
            requirement, year=year, columns=NEUTRALIZATION_MANIFEST_PANEL_COLUMNS
        )
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.blocking_issues]}"
            )
        found.extend(
            _neutralization_manifest_from_row(row, dataset=dataset) for row in outcome.rows
        )
    return tuple(found)


def _neutralized_observation_from_row(
    row: Sequence[object], *, dataset: str
) -> NeutralizedFactorObservation:
    """Rebuild one neutralised row from `(event_time, *NEUTRALIZED_OBSERVATION_PANEL_COLUMNS)`.

    Refuses the wrong width rather than unpacking positionally into whatever fits, and decodes
    both coverage columns *from* their vocabularies rather than casting -- `_processed_code` for
    the source's five and `_neutralized_code` for this plane's seven. A partition written by a
    build that knows an eighth of either would otherwise decode into a dataclass whose fields the
    type system believes are closed sets and are not.
    """
    expected = 1 + len(NEUTRALIZED_OBSERVATION_PANEL_COLUMNS)
    if len(row) != expected:
        raise FactorEngineError(
            f"a {dataset} row has {len(row)} values, expected {expected} "
            f"({EVENT_TIME_COLUMN}, {', '.join(NEUTRALIZED_OBSERVATION_PANEL_COLUMNS)})"
        )
    cells = dict(zip((EVENT_TIME_COLUMN, *NEUTRALIZED_OBSERVATION_PANEL_COLUMNS), row, strict=True))
    as_of = cells[EVENT_TIME_COLUMN]
    if not isinstance(as_of, datetime):
        raise FactorEngineError(
            f"a {dataset} row carries {type(as_of).__name__} for {EVENT_TIME_COLUMN}, "
            "not a datetime"
        )
    industry = cells["industry_code"]
    return NeutralizedFactorObservation(
        subject=str(cells[SUBJECT_COLUMN_NAME]),
        as_of=as_of,
        value=_stored_residual(cells["value"], dataset=dataset),
        coverage=_neutralized_code(cells["coverage"], dataset=dataset),
        neutralization_id=str(cells["neutralization_id"]),
        neutralization_manifest_id=str(cells["neutralization_manifest_id"]),
        source_factor_id=str(cells["source_factor_id"]),
        source_transform_id=str(cells["source_transform_id"]),
        source_transform_manifest_id=str(cells["source_transform_manifest_id"]),
        source_coverage=_processed_code(cells["source_coverage"], dataset=dataset),
        industry_code=None if industry is None else str(industry),
    )


def _stored_residual(value: object, *, dataset: str) -> float | None:
    """A stored residual cell as a finite float or `None`, or a refusal.

    `_stored_processed_value`'s rule for this column, written out rather than delegated because
    the message has to name the right remedy: this plane has **no** code that carries a non-finite
    number -- there is no `undefined_value` to send one to and no imputation to stand in for it --
    so an infinity here is a row no declared coverage code describes at all.
    """
    if value is None:
        return None
    parsed = float(str(value))
    if not math.isfinite(parsed):
        raise FactorEngineError(
            f"a {dataset} row carries value {value!r}, which is not a finite number; no declared "
            "neutralised coverage code carries one, and a residual holding it poisons every mean, "
            "rank and correlation built on the column"
        )
    return parsed


def _neutralized_code(value: object, *, dataset: str) -> NeutralizedCoverage:
    """A stored `coverage` cell as one of the seven declared codes, or a refusal.

    Matched against `NEUTRALIZED_COVERAGE_ORDER` and returned *from* it rather than cast, so a
    partition written by a build that knows an eighth code is refused where the dataset can be
    named -- `panel_factors._coverage_code`'s form on this plane's vocabulary.
    """
    text = str(value)
    for code in NEUTRALIZED_COVERAGE_ORDER:
        if code == text:
            return code
    raise FactorEngineError(
        f"a {dataset} row carries coverage {text!r}, which this build does not declare "
        f"({list(NEUTRALIZED_COVERAGE_ORDER)}); it was written by a build that knows a code this "
        "one does not"
    )


def _processed_code(value: object, *, dataset: str) -> ProcessedCoverage:
    """A stored `source_coverage` cell as one of the processed plane's five codes, or a refusal.

    The same instrument pointed at the neighbouring vocabulary, and it is separate from
    `_neutralized_code` rather than parameterised because a shared decoder would have to take the
    order tuple as an argument and would then be unable to name, in its refusal, *which* plane's
    vocabulary the stored code failed against -- which is the whole of what makes the message
    actionable.
    """
    text = str(value)
    for code in PROCESSED_COVERAGE_ORDER:
        if code == text:
            return code
    raise FactorEngineError(
        f"a {dataset} row carries source_coverage {text!r}, which the processed vocabulary does "
        f"not declare ({list(PROCESSED_COVERAGE_ORDER)}); it was written by a build that knows a "
        "code this one does not"
    )


def _neutralization_manifest_from_row(
    row: Sequence[object], *, dataset: str
) -> FactorNeutralizationManifest:
    """Rebuild one neutralisation manifest from its row, and prove it is the one it was filed under.

    `_transform_manifest_from_row`'s argument: a neutralisation build reads exactly two inputs and
    both are addressed by a digest, so it is one row.

    The reassembled `neutralization_manifest_id` is checked against the id the row was filed
    under, and that is not belt and braces: every field this reads is one the identity was
    computed from, so a decoder that dropped or mistyped one would hand back a build nobody ever
    ran, under the id a caller then uses to name it in `supersedes`.
    """
    if len(row) != len(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS):
        raise FactorEngineError(
            f"a {dataset} row has {len(row)} values, expected "
            f"{len(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS)} "
            f"({', '.join(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS)})"
        )
    cells = dict(zip(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS, row, strict=True))
    as_of = cells["as_of_time"]
    if not isinstance(as_of, datetime):
        raise FactorEngineError(
            f"a {dataset} row carries {type(as_of).__name__} for as_of_time, not a datetime"
        )
    manifest = FactorNeutralizationManifest(
        neutralization_id=str(cells["neutralization_id"]),
        neutralization_key=str(cells["neutralization_key"]),
        neutralization_version=int(str(cells["neutralization_version"])),
        source_factor_id=str(cells["source_factor_id"]),
        source_factor_key=str(cells["source_factor_key"]),
        source_factor_version=int(str(cells["source_factor_version"])),
        source_transform_id=str(cells["source_transform_id"]),
        source_transform_manifest_id=str(cells["source_transform_manifest_id"]),
        source_processed_digest=str(cells["source_processed_digest"]),
        characteristic_digest=str(cells["characteristic_digest"]),
        neutralized_observation_digest=str(cells["neutralized_observation_digest"]),
        as_of=as_of,
        code_commit=str(cells["code_commit"]),
    )
    stored = str(cells[SUBJECT_COLUMN_NAME])
    if manifest.neutralization_manifest_id != stored:
        raise FactorEngineError(
            f"a {dataset} build stored under {stored!r} reassembles to "
            f"{manifest.neutralization_manifest_id!r}; the row and the identity it was filed "
            "under disagree, so this partition was written by a build whose manifest contract is "
            "not this one's"
        )
    return manifest


# --- assembling the second cross section out of two stored datasets -------------------------------


def load_industry_market_cap_cross_section(
    store: PanelStore,
    spec: FactorNeutralizationSpec,
    *,
    subjects: Sequence[str],
    day: date,
    as_of: datetime,
    calendar: TradingCalendar,
    membership_years: Sequence[int],
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> IndustryMarketCapCrossSection:
    """Assemble the industry-and-size cross section from the two stored panel datasets.

    **The only builder of this value in `src/`, and the reason the arithmetic layer can stay
    store-free.** `apply_factor_neutralization` takes the result as a value; this is where a store
    is touched, once, in a function that computes no residual.

    ## Which door each read takes, and why they are no longer the same one

    Both reads go through `panel_ingest`, and **since `V2-P4-026` the two take different doors**.

    `load_industry_histories` still takes `PanelStore.read_if_ready`, the **unfiltered** door,
    which refuses a partition whose newest row post-dates `as_of` (`not_yet_knowable` is decided
    on a partition's `max_available_time`) rather than filtering the offending rows out. That is
    not conservatism for its own sake: a row predicate over `index_member_all` cannot be told from
    an absent row, which is the whole reason `SecurityIndustryHistory.answerable_through` exists,
    and `tests/unit/panel/test_visible_read_callers.py` makes every caller of the filtered door
    answer that question before taking it.

    `load_daily_valuations` takes the filtered door, one session at a time, through
    `panel_ingest._read_visible_price_session`. It can, because `daily_basic`'s shape answers the
    same question the other way: every row of one session carries one `available_time`, so a
    session read is all-or-nothing and a withheld session arrives as a **named refusal** rather
    than as a short cross section. That is `V2-P4-026`, and it is why a mid-year `as_of` no longer
    refuses this whole function. This module's own `FILTERED_READ_CALLERS` entry is still spent
    entirely on reading its own output partitions back; the new grant is `panel_ingest.py`'s.

    Two consequences of the industry read's strictness are costs rather than benefits and are
    stated:

    - **A membership year whose latest assignment starts after `as_of` blocks the whole read.**
      `providers/tushare.py` dates a membership row's availability at the day it is about, so a
      reclassification effective next month makes its partition unreadable today -- and
      `membership_years` is how a caller narrows the read to the years it needs, exactly as
      `load_industry_histories`' own callers do. Narrowing has its own cost:
      `SecurityIndustryHistory.answerable_through` then refuses a day past the last year read,
      which is the fail-open direction `KNOWN_INDUSTRY_LIMITATIONS
      .a_partial_year_read_cannot_see_an_interval_close` closed.
    - **No cross section before 2021-12-13 is assemblable at all**, because every membership row's
      `available_time` is floored at the SW2021 taxonomy's effective date. That is a refusal of
      the whole build rather than a thinning of it, and it is
      `KNOWN_NEUTRALIZATION_LIMITATIONS.no_cross_section_is_neutralisable_before_2021_12_13`.
      **It is the outermost of the two bounds and `V2-P4-026` made it the binding one**: with
      `daily_basic` no longer refusing an in-year `as_of`, the earliest instant at which anything
      here can answer is this floor, and the finest granularity reachable inside the era is what
      the bullet above allows.

    ## What it does with a security it cannot answer for

    Nothing is dropped and nothing is guessed. A security with no assignment covering `day` --
    including one inside the 49 measured coverage holes, and one whose history this read cannot
    speak for -- goes to `without_industry`; one that has an industry and no `daily_basic` row
    goes to `without_market_cap`. Both are carried into the returned value, hashed into
    `characteristic_digest`, and turned into their own coverage codes by the engine. That is the
    whole reason this function returns a three-part value instead of a mapping: a mapping would
    make "no industry", "no capitalisation" and "never asked about" one absence, and the third of
    those is a bug while the first two are the market.

    `subjects` is an argument rather than a universe this function derives, `compute_factor`'s
    arrangement and for its reason: `stock_basic` has the same mid-year readiness problem, and
    answering it here would put a second policy decision inside a builder. The natural argument is
    the processed panel's own subject list.
    """
    if not subjects:
        raise FactorEngineError(
            "load_industry_market_cap_cross_section needs at least one subject; an empty cross "
            "section would satisfy every per-security check vacuously and would then be refused "
            "by the engine's coverage guard with a message about the wrong thing"
        )
    histories = load_industry_histories(
        store, years=membership_years, as_of=as_of, max_staleness=max_staleness
    )
    valuations = load_daily_valuations(
        store,
        day=day,
        calendar=calendar,
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )
    complete: list[SecurityCharacteristic] = []
    without_industry: list[str] = []
    without_market_cap: list[str] = []
    for subject in sorted(set(subjects)):
        answer = _industry_answer(histories, subject=subject, day=day)
        if answer is None:
            without_industry.append(subject)
            continue
        assignment, backfilled = answer
        valuation = valuations.get(subject)
        market_cap = None if valuation is None else _market_cap(valuation, spec.market_cap_measure)
        if market_cap is None:
            without_market_cap.append(subject)
            continue
        complete.append(
            SecurityCharacteristic(
                subject=subject,
                industry_code=industry_code_of(assignment, spec.industry_level),
                market_cap=market_cap,
                is_backfilled=backfilled,
            )
        )
    return build_industry_market_cap_cross_section(
        as_of=as_of,
        taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
        industry_level=spec.industry_level,
        market_cap_measure=spec.market_cap_measure,
        characteristics=complete,
        without_industry=without_industry,
        without_market_cap=without_market_cap,
    )


def _industry_answer(
    histories: Mapping[str, SecurityIndustryHistory], *, subject: str, day: date
) -> tuple[IndustryAssignment, bool] | None:
    """One security's assignment on `day` and whether the label is backfilled, or `None`.

    Three "no answer" states are folded into one `None` here, deliberately, and the fold is the
    one place this module treats an `IndustryHorizonError` as data:

    - the corpus has no history for this code at all (a name `index_member_all` never carried);
    - it has one and no assignment covers `day` -- before the first, after a closed last, or
      inside one of the 49 measured coverage holes;
    - it has one and this read cannot speak for `day`'s year, because a stored membership year was
      left unread and an assignment's close is filed in its own year.

    All three mean "this build has no industry for this security on this day", which is exactly
    what `industry_missing` says, and none of them is a fault in *this* module. The third is the
    one worth naming: it is fail-closed by construction (`answerable_through` refuses rather than
    answering from a stale open interval), so folding it in here turns a refusal into a counted
    code rather than into a silent answer.

    `is_backfilled` comes off `IndustryAnswer` rather than being recomputed, so the one definition
    of "this label predates its taxonomy" lives in `domain/industry_classification.py`.
    """
    history = histories.get(subject)
    if history is None:
        return None
    try:
        answer = history.industry_on(day)
    except IndustryHorizonError:
        return None
    return answer.assignment, answer.is_backfilled


def _market_cap(valuation: DailyValuation, measure: MarketCapMeasure) -> float | None:
    """The declared capitalisation off one `daily_basic` row.

    Resolved through `_MARKET_CAP_READERS` rather than through two branches, so
    `_refuse_neutralization_table_drift`'s vocabulary check has a table to reconcile and a third
    measure cannot arrive with no reader. `None` is returned rather than raised for a null cell --
    `domain/daily_prices.py` refuses a null `total_mv`/`circ_mv` at the write, so a `None` here
    means the security had no `daily_basic` row of the shape this reader wants, which is the
    `market_cap_missing` code's own case.
    """
    return _MARKET_CAP_READERS[measure](valuation)
