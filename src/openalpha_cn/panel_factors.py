"""The panel feature computation engine (`V2-P3-002`): factor observations, on the panel plane.

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

## Two datasets per factor, because a manifest is not an observation and a year is a partition

`factor_obs_<key>_v<n>` holds one row per `(security, as_of)`. `factor_manifest_<key>_v<n>`
holds one row per `(build, input partition)`, keyed by `FactorBuildManifest.manifest_id`, which
every observation carries as a column. They are separate datasets because the second is
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
`tests/integration/panel/test_factor_engine.py::test_a_declared_freshness_bound_survives_the_
cross_year_window_it_has_to_allow` for both directions of it.

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

## Coverage is a code, never a bool

`FactorCoverage` has five members and `domain/factor.py` argues each one. The short version:
"could not compute" is not one fact. A security that had not listed yet (`not_in_universe`)
should have no value and reporting a data fault for it would put a permanent false defect on
every historical cross section; a security that listed nine sessions ago
(`insufficient_history`) is a correct answer to a 120-session window; a null column
(`input_missing`) is a fetch problem; a zero denominator (`undefined_value`) is a definition
problem. `V2-P3-005` has to exclude all four from a correlation rather than treat them as
zeros, and only a code set lets it.

## No numpy, no pandas -- and that is a measurement rather than a preference

ADR-0003 permits both. This module needs neither, and adding them would take the runtime
dependency set from nine to eleven and pull in ADR-0003's recorded mypy consequence
(`follow_imports=skip` plus `warn_return_any` makes every function returning a pandas
expression an error). What the engine actually does is: group rows by `(subject, session)`,
take the last `lookback_sessions` of each subject's sessions, and call one scalar function per
subject. There is no matrix, no broadcast, no linear algebra and no cross-sectional regression
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

## What is deliberately not here

**No concrete factor family.** `V2-P3-009`..`013` own those. One definition ships, `reversal_1d`,
and it exists to exercise the engine end to end against a `daily`-only input -- see
`REVERSAL_1D`'s own docstring for what it does and does not claim.

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
from typing import Final, Protocol, cast
from zoneinfo import ZoneInfo

from openalpha_cn.domain.factor import (
    FACTOR_DIRECTIONS,
    FactorBuildManifest,
    FactorCoverage,
    FactorDefinition,
    FactorDirection,
    FactorField,
    FactorInputProvenance,
    FactorInputRef,
    FactorObservation,
    FactorRegistry,
    set_digest,
    validate_factor_observation,
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
)
"""One stored observation, column by column, answering `V2-P3-002`'s six-part acceptance.

`subject` (the security) and the four clocks are added by `ColumnarPanelBatch` itself, so the
six the acceptance names land as: **subject** -> `subject`; **as-of** -> `event_time` /
`available_time`, which for a derived row are both the `as_of` the build was made at;
**value** -> `value`, null unless `coverage` is `computed`; **coverage marker** -> `coverage`,
one of five codes; **input reference** -> `input_row_count` / `input_session_first` /
`input_session_last` for the rows, and `manifest_id` for the partitions; **build manifest** ->
`manifest_id`, resolvable in this factor's own `factor_manifest_<key>_v<n>`.

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
    """

    subject: str
    as_of: datetime
    sessions: tuple[date, ...]
    values: Mapping[tuple[str, str], tuple[float, ...]]

    def series(self, dataset: str, column: str) -> tuple[float, ...]:
        """`dataset.column` over `sessions`, aligned index for index.

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
"""The single registered factor, chosen to depend on `daily` and nothing else.

Three properties made it the right verification subject and each was a choice: it reads one
column, so a coverage check has exactly one way to fail and the test that provokes
`input_missing` can be pointed at it; its lookback is 2, the smallest window for which
`insufficient_history` is reachable at all (a 1-session window is satisfied by any security
with one row); and its formula has a denominator, so `undefined_value` is reachable rather than
declared and never emitted. A factor with no possible undefined result would have made that
code a table entry with no branch behind it -- which is the exact drift `V2-P0A-001`'s AST
validation and `panel build`'s `_audit_written_partitions` were both added to close.
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


FACTOR_DEFINITIONS: Final[FactorRegistry] = FactorRegistry((REVERSAL_1D,))
"""Every factor this build declares. `V2-P3-009`..`013` extend it."""

FACTOR_EVALUATORS: Final[Mapping[str, FactorEvaluator]] = MappingProxyType(
    {REVERSAL_1D.qualified_key: _reversal_1d}
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

    panel_sessions = _panel_sessions(readings)
    _refuse_a_panel_narrower_than_the_lookback(
        definition, panel_sessions=panel_sessions, requirements=requirements
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
    """One dataset's visible rows, indexed the two ways `_classify` asks about them."""

    sessions_by_subject: Mapping[str, tuple[date, ...]]
    values: Mapping[tuple[str, date], tuple[float | None, ...]]
    columns: tuple[str, ...]


def _panel_sessions(readings: Mapping[str, _DatasetReading]) -> tuple[date, ...]:
    """Every session the visible read returned, across every dataset and every security.

    The engine's own calendar, and the *only* one it has: `compute_factor` is not given a
    `TradingCalendar` and must not build one, for the reason it is not given a universe -- a
    second source for "which days were open" is a second thing that can disagree with the
    partition it is reading.

    Two checks read it, and both are exact rather than heuristic because a security's own
    sessions are always a subset of this: whether any security could satisfy the lookback at all
    (`_refuse_a_panel_narrower_than_the_lookback`), and how many sessions a formed window spans
    (`_window_span`).
    """
    sessions: set[date] = set()
    for reading in readings.values():
        for days in reading.sessions_by_subject.values():
            sessions.update(days)
    return tuple(sorted(sessions))


def _refuse_a_panel_narrower_than_the_lookback(
    definition: FactorDefinition,
    *,
    panel_sessions: tuple[date, ...],
    requirements: Mapping[str, ReadinessRequirement],
) -> None:
    """Refuse a build whose visible panel cannot satisfy the lookback for **anybody**.

    Every security's session set is a subset of the panel's, so a panel holding fewer sessions
    than `lookback_sessions` makes `insufficient_history` for the whole cross section a matter of
    arithmetic. That is `FactorEngineError`'s own category -- "this build has no answer for
    anybody" -- and a panel of `insufficient_history` returned for it is the fail-open dressed as
    coverage that class exists to name.

    The message leads with the years, because that is where the fault is: the sessions a factor
    can see are the ones in `requirement.years`, and a 120-session window evaluated in January
    needs the previous year in that tuple or nothing qualifies. Nothing else in this engine's
    eight mandatory arguments is as easy to get wrong or as quiet when it is.

    What this does **not** refuse is a wide-enough panel over securities that are individually
    too young. That is a real answer and `insufficient_history` is the code for it.
    """
    if len(panel_sessions) >= definition.lookback_sessions:
        return
    years = sorted({year for requirement in requirements.values() for year in requirement.years})
    raise FactorEngineError(
        f"{definition.qualified_key} needs {definition.lookback_sessions} sessions and the "
        f"visible panel over year(s) {years} holds {len(panel_sessions)}, so no security in any "
        "cross section could qualify and every observation would be insufficient_history. That "
        "is a fault in the request rather than an answer about the data: widen the `years` of "
        "the requirements this factor reads -- a window that spans a year boundary needs the "
        "earlier year named too -- or evaluate at a later as_of"
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
        missing = sorted(set(definition.columns_of(dataset)) - set(requirement.required_fields))
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
    """
    projection = (SUBJECT_COLUMN_NAME, EVENT_TIME_COLUMN, *columns)
    sessions: dict[str, list[date]] = {}
    values: dict[tuple[str, date], tuple[float | None, ...]] = {}
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
            session = _session_date(row[1], dataset=dataset, zone=zone)
            key = (subject, session)
            if key in values:
                raise FactorEngineError(
                    f"{dataset} carries more than one row for {subject} on "
                    f"{session.isoformat()}; this engine reads one row per security per "
                    "session, so a dataset with several versions of one observation needs a "
                    "reducer chosen for it before a factor may read it"
                )
            values[key] = tuple(
                _numeric(value, dataset=dataset, column=name, subject=subject, session=session)
                for name, value in zip(columns, row[2:], strict=True)
            )
            sessions.setdefault(subject, []).append(session)
    return (
        _DatasetReading(
            MappingProxyType({name: tuple(sorted(days)) for name, days in sessions.items()}),
            MappingProxyType(values),
            columns,
        ),
        tuple(references),
        tuple(provenance),
    )


def _session_date(value: object, *, dataset: str, zone: ZoneInfo) -> date:
    if not isinstance(value, datetime):
        raise FactorEngineError(
            f"{dataset}.{EVENT_TIME_COLUMN} read back as {type(value).__name__}, not a "
            "datetime; the engine resolves a session date from it and cannot from anything else"
        )
    return value.astimezone(zone).date()


def _numeric(
    value: object, *, dataset: str, column: str, subject: str, session: date
) -> float | None:
    """A stored cell as a float, `None` for a missing observation, or a refusal.

    A refusal rather than a coverage code for a non-numeric column, because that is a property
    of the *definition* (a factor declaring `daily.trade_date` as an input) and not of this
    security's data: reporting `input_missing` would tell a reader to re-fetch, which would
    never fix it. `bool` is refused explicitly because it is an `int` in Python and `True`
    would otherwise arrive as `1.0`.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FactorEngineError(
            f"{dataset}.{column} holds {type(value).__name__} for {subject} on "
            f"{session.isoformat()}; a factor input must be a stored number, and this column "
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

    "History" is two questions rather than one, and both are `insufficient_history`: whether the
    security has `lookback_sessions` of its own sessions at all, and whether the most recent
    `lookback_sessions` of them fit inside `max_window_sessions` panel sessions. The two are
    distinguishable on the stored row without a sixth code -- the first carries no window
    (there was none to record) and the second carries the window it was refused for.
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
        )
    available: set[date] = set()
    for reading in readings.values():
        available.update(reading.sessions_by_subject.get(subject, ()))
    ordered = sorted(available)
    if len(ordered) < definition.lookback_sessions:
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="insufficient_history",
            factor_id=definition.factor_id,
            manifest_id=manifest_id,
            input_row_count=_stored_rows(subject, sessions=tuple(ordered), readings=readings),
            input_session_first=None,
            input_session_last=None,
        )
    window = tuple(ordered[-definition.lookback_sessions :])
    row_count = _stored_rows(subject, sessions=window, readings=readings)
    if _window_span(window, panel_sessions=panel_sessions) > definition.max_window_sessions:
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="insufficient_history",
            factor_id=definition.factor_id,
            manifest_id=manifest_id,
            input_row_count=row_count,
            input_session_first=window[0],
            input_session_last=window[-1],
        )
    series = _complete_series(subject, window=window, readings=readings)
    if series is None:
        return FactorObservation(
            subject=subject,
            as_of=as_of,
            value=None,
            coverage="input_missing",
            factor_id=definition.factor_id,
            manifest_id=manifest_id,
            input_row_count=row_count,
            input_session_first=window[0],
            input_session_last=window[-1],
        )
    computed = evaluator(FactorWindow(subject=subject, as_of=as_of, sessions=window, values=series))
    usable = computed is not None and math.isfinite(computed)
    return FactorObservation(
        subject=subject,
        as_of=as_of,
        value=float(computed) if usable and computed is not None else None,
        coverage="computed" if usable else "undefined_value",
        factor_id=definition.factor_id,
        manifest_id=manifest_id,
        input_row_count=row_count,
        input_session_first=window[0],
        input_session_last=window[-1],
    )


def _window_span(window: tuple[date, ...], *, panel_sessions: tuple[date, ...]) -> int:
    """How many **panel** sessions the window reaches across, first and last included.

    Equal to `len(window)` for a security that traded every session in it, and larger by exactly
    the number it missed. Counted against the panel's own session set rather than in calendar
    days, because a calendar-day bound would be a second calendar for the engine to disagree
    with the partition it is reading, and because "halted for three weeks" is a number of
    sessions rather than a number of days.

    `panel_sessions` is sorted, so this is two binary searches rather than a scan -- the check
    runs once per security per build (5,534 times for a whole-market cross section).
    """
    left = bisect.bisect_left(panel_sessions, window[0])
    right = bisect.bisect_right(panel_sessions, window[-1])
    return right - left


def _stored_rows(
    subject: str, *, sessions: tuple[date, ...], readings: Mapping[str, _DatasetReading]
) -> int:
    """How many input rows this security actually has over `sessions`, across every dataset.

    Counted rather than derived as `len(sessions) * len(readings)`, which is only right when
    every dataset covers every session and is exactly wrong on the two observations where the
    number matters most: an `input_missing` row is one where a cell is absent, and an
    `insufficient_history` row is one whose datasets disagree about how much history there is.
    A count that over-reported on precisely those two would be a provenance field that is
    accurate only when nobody needs it.
    """
    return sum(
        1
        for reading in readings.values()
        for session in sessions
        if (subject, session) in reading.values
    )


def _complete_series(
    subject: str,
    *,
    window: tuple[date, ...],
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
    series: dict[tuple[str, str], list[float]] = {}
    for dataset, reading in readings.items():
        for column in reading.columns:
            series[(dataset, column)] = []
        for session in window:
            cells = reading.values.get((subject, session))
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
        lookback_sessions=int(str(head["lookback_sessions"])),
        max_window_sessions=int(str(head["max_window_sessions"])),
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
    first = cells["input_session_first"]
    last = cells["input_session_last"]
    return FactorObservation(
        subject=str(cells[SUBJECT_COLUMN_NAME]),
        as_of=as_of,
        value=_stored_value(cells["value"], dataset=dataset),
        coverage=_coverage_code(cells["coverage"], dataset=dataset),
        factor_id=str(cells["factor_id"]),
        manifest_id=str(cells["manifest_id"]),
        input_row_count=int(str(cells["input_row_count"])),
        input_session_first=None if first is None else date.fromisoformat(str(first)),
        input_session_last=None if last is None else date.fromisoformat(str(last)),
    )


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
