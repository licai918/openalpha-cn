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

## Two datasets, because a manifest is not an observation

`factor_observations` holds one row per `(factor, security, as_of)`. `factor_build_manifests`
holds one row per `(build, input partition)`, keyed by `FactorBuildManifest.manifest_id`, which
every observation carries as a column. They are separate partitions because the second is
per-build rather than per-security -- denormalising it onto the observations would repeat the
same provenance 5,534 times per as_of, and could not represent a factor with a variable number
of input partitions at all.

The manifest dataset's `subject` column holds a `manifest_id` rather than a security.
`trade_cal` sets the precedent (its subject is an exchange): `subject` is the entity the row is
about, and for a manifest row that is the build. It also buys the write guard --
`PartitionCoverage.subjects` is then the set of builds a partition holds, so
`_refuse_to_drop_stored_rows` can see an overwrite that would destroy one **without reading a
single row**.

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

**The alternative was measured, not assumed.** The cost of *not* filtering is a panel rebuilt
once per `as_of` (P2's technical acceptance put it at 120x a single annual build); the cost of
computing only on year boundaries is that `V2-P3-005`'s IC decay and `V2-P4-013`'s
walk-forward have one observation per year to work with, which is not a research programme.

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
and evaluation together -- takes **1.95 s** cold and 1.91 s warm, about 2.9 us/row. The same
partition costs 288 s to *write*, which is where a performance problem on this plane actually
is. ADR-0003 carries the same table and the one defect the measurement found (a `computed_field`
read inside the per-security loop, which re-hashed the build manifest 5,534 times).

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

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from openalpha_cn.domain.factor import (
    FactorBuildManifest,
    FactorCoverage,
    FactorDefinition,
    FactorField,
    FactorInputRef,
    FactorObservation,
    FactorRegistry,
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

FACTOR_OBSERVATION_DATASET: Final[str] = "factor_observations"
FACTOR_MANIFEST_DATASET: Final[str] = "factor_build_manifests"

FACTOR_OBSERVATION_KIND: Final[str] = "factor_observation"
FACTOR_MANIFEST_KIND: Final[str] = "factor_build_manifest"

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
`manifest_id`, resolvable in `factor_build_manifests`.

`factor_key` and `factor_version` are stored beside `factor_id` even though the ID determines
them, because `factor_id` is opaque: a reader querying the partition directly (which is what
`V2-P3-002` exists to make possible) would otherwise need this build's registry to know what
the rows are about.
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

FACTOR_MANIFEST_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "factor_id",
    "factor_key",
    "factor_version",
    "as_of_time",
    "date_timezone",
    "code_commit",
    "lookback_sessions",
    "subject_count",
    "universe_count",
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
"""

FACTOR_MANIFEST_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *FACTOR_MANIFEST_DATA_COLUMNS,
)

_MANIFEST_COLUMN_KINDS: Final[Mapping[str, PanelColumnKind]] = MappingProxyType(
    {
        "factor_id": "string",
        "factor_key": "string",
        "factor_version": "integer",
        "as_of_time": "timestamp",
        "date_timezone": "string",
        "code_commit": "string",
        "lookback_sessions": "integer",
        "subject_count": "integer",
        "universe_count": "integer",
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
    summary=(
        "The engine's verification factor: one session's close-to-close simple return, "
        "close[t] / close[t-1] - 1, over the two most recent sessions knowable at as_of. It "
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
    visible in the census rather than in the numbers.
    """
    table = FACTOR_EVALUATORS if evaluators is None else evaluators
    evaluator = _resolve_evaluator(definition, table)
    ordered_subjects = _validated_subjects(subjects)
    _validate_requirements(definition, requirements, as_of=as_of)
    zone = _resolve_timezone(date_timezone)

    readings: dict[str, _DatasetReading] = {}
    inputs: list[FactorInputRef] = []
    for dataset in definition.datasets:
        reading, refs = _read_dataset(
            store,
            dataset=dataset,
            columns=definition.columns_of(dataset),
            requirement=requirements[dataset],
            zone=zone,
        )
        readings[dataset] = reading
        inputs.extend(refs)

    manifest = FactorBuildManifest(
        factor_id=definition.factor_id,
        factor_key=definition.key,
        factor_version=definition.version,
        as_of=as_of,
        date_timezone=date_timezone,
        code_commit=code_commit,
        lookback_sessions=definition.lookback_sessions,
        subject_count=len(ordered_subjects),
        universe_count=len(set(universe)),
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
    )


@dataclass(frozen=True, slots=True)
class _DatasetReading:
    """One dataset's visible rows, indexed the two ways `_classify` asks about them."""

    sessions_by_subject: Mapping[str, tuple[date, ...]]
    values: Mapping[tuple[str, date], tuple[float | None, ...]]
    columns: tuple[str, ...]


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
) -> tuple[_DatasetReading, tuple[FactorInputRef, ...]]:
    """Every visible row of every requested year of one dataset, plus its input references.

    One `read_visible_at` per year, matching `load_daily_bars`' shape (readiness is assessed per
    call, on catalog metadata rather than Parquet). The projection is
    `(subject, event_time, *columns)` -- `available_time` is deliberately not projected, because
    the predicate is applied in SQL and a caller-side re-filter would be a second copy of the
    rule that can disagree with the first.
    """
    projection = (SUBJECT_COLUMN_NAME, EVENT_TIME_COLUMN, *columns)
    sessions: dict[str, list[date]] = {}
    values: dict[tuple[str, date], tuple[float | None, ...]] = {}
    references: list[FactorInputRef] = []
    for year in sorted(set(requirement.years)):
        outcome = store.read_visible_at(requirement, year=year, columns=projection)
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{dataset} year={year} cannot be read at {requirement.as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
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
                batch_digest=coverage.batch_digest,
                partition_content_hash=coverage.partition_content_hash,
                visible_row_count=outcome.visible_row_count,
                withheld_row_count=outcome.withheld_row_count,
            )
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
    evaluator: FactorEvaluator,
    manifest_id: str,
) -> FactorObservation:
    """One security's coverage code and, if there is one, its value.

    The order of the checks is the order of `FactorCoverage`'s own argument and it is not
    arbitrary: universe before history, because a name that had not listed yet has no history
    *and should not*; history before nullity, because a window that cannot be formed has no
    cells to check; nullity before arithmetic, because an evaluator is only ever handed a
    complete window.
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
    series = _complete_series(subject, window=window, readings=readings)
    row_count = _stored_rows(subject, sessions=window, readings=readings)
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
    """
    observations = panel.observations
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
        dataset=FACTOR_OBSERVATION_DATASET,
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


def factor_manifest_batch(panel: FactorPanel) -> ColumnarPanelBatch:
    """One panel's build manifest, one row per input partition, keyed by `manifest_id`."""
    manifest = panel.manifest
    inputs = manifest.inputs
    instants = tuple(manifest.as_of for _ in inputs)
    columns: dict[str, list[object]] = {
        "factor_id": [manifest.factor_id] * len(inputs),
        "factor_key": [manifest.factor_key] * len(inputs),
        "factor_version": [manifest.factor_version] * len(inputs),
        "as_of_time": [manifest.as_of] * len(inputs),
        "date_timezone": [manifest.date_timezone] * len(inputs),
        "code_commit": [manifest.code_commit] * len(inputs),
        "lookback_sessions": [manifest.lookback_sessions] * len(inputs),
        "subject_count": [manifest.subject_count] * len(inputs),
        "universe_count": [manifest.universe_count] * len(inputs),
        "input_dataset": [item.dataset for item in inputs],
        "input_year": [item.year for item in inputs],
        "input_batch_digest": [item.batch_digest for item in inputs],
        "input_partition_hash": [item.partition_content_hash for item in inputs],
        "input_visible_rows": [item.visible_row_count for item in inputs],
        "input_withheld_rows": [item.withheld_row_count for item in inputs],
    }
    return ColumnarPanelBatch(
        provider_id=FACTOR_PROVIDER_ID,
        dataset=FACTOR_MANIFEST_DATASET,
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
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Write every panel's observations and manifests, merged into one partition per year.

    Takes a **sequence** for the reason `write_daily_panel` and `write_adjustment_factors` do:
    `PanelStore.write_partition` replaces a partition whole and has no append, so a caller
    writing one `as_of` at a time would destroy the year each time. Every `as_of` (and every
    factor) whose observations belong to a partition year has to reach the store in one call.

    That is a real constraint rather than an implementation detail, so it is guarded rather than
    documented: `_refuse_to_drop_stored_rows` reads each target partition's stored subject list
    off the catalog -- `manifest_id`s for the manifests, securities for the observations, at no
    row-read cost -- and refuses a write that would drop any of them. A rebuild that supersedes
    an earlier build must name it, which is the same shape
    `panel_ingest._refuse_to_drop_stored_subjects` gives the calendar and the registry.

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
    planned = [
        (year, yearly)
        for batches in (
            [factor_observation_batch(item) for item in panels],
            [factor_manifest_batch(item) for item in panels],
        )
        for year, yearly in split_panel_batch_by_year(
            merge_panel_batches(batches), date_timezone=date_timezone
        )
    ]
    # Guards first, writes second -- see this function's docstring for what that ordering is
    # worth and what it is not.
    for year, yearly in planned:
        _refuse_to_drop_stored_rows(store, yearly, year)
    return tuple(
        write_panel_batch(store, yearly, year=year, date_timezone=date_timezone)
        for year, yearly in planned
    )


def _refuse_to_drop_stored_rows(store: PanelStore, batch: ColumnarPanelBatch, year: int) -> None:
    """Block a write that would remove a subject the partition already holds.

    `panel_ingest._refuse_to_drop_stored_subjects` for the two factor datasets, and it is one
    function for both because the failure is one failure: a partition is replaced whole, so a
    batch missing something the stored partition had destroys data and reports success. What a
    subject *is* differs -- a `manifest_id` in `factor_build_manifests`, a security in
    `factor_observations` -- and both readings are useful. Dropping a build means an `as_of`
    somebody computed is gone; dropping a security means a name is gone from every `as_of` in
    the year.

    Reads `PartitionCoverage.subjects`: one catalog row, no partition scan. A partition with no
    coverage record is not protected, for the reason the ingest version gives -- there is
    nothing to read the stored subjects from, that state is an interrupted write which readiness
    already blocks as `coverage_missing`, and refusing the overwrite would leave the store with
    no way back.
    """
    existing = store.read_coverage(batch.dataset, year)
    if existing is None:
        return
    dropped = sorted(set(existing.subjects) - set(batch.subjects))
    if dropped:
        raise FactorEngineError(
            f"{batch.dataset} year={year} already holds {len(existing.subjects)} subject(s) and "
            f"this write carries {len(set(batch.subjects))}; it would drop {dropped[:5]}"
            f"{'...' if len(dropped) > 5 else ''}. A partition is replaced whole, so everything "
            "belonging to this year has to be written in one call -- recompute the superseded "
            "builds and pass them all to write_factor_panels"
        )


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# --- reading back --------------------------------------------------------------------------------


def factor_observation_requirement(
    *, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What the observation partition must satisfy before factor values may be read back.

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
        dataset=FACTOR_OBSERVATION_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=FACTOR_OBSERVATION_PANEL_COLUMNS,
        max_staleness=None,
    )


def load_factor_observations(
    store: PanelStore,
    *,
    years: Sequence[int],
    as_of: datetime,
    factor_id: str | None = None,
) -> tuple[FactorObservation, ...]:
    """Read stored observations back, filtered to what was knowable at `as_of`.

    Through `read_visible_at` rather than `read_if_ready`, and for the same reason the inputs
    are: an observation's `available_time` is the `as_of` it was computed at, so a year
    partition holding a year of daily cross sections has a `max_available_time` in December and
    `read_if_ready` would refuse it at every `as_of` inside the year -- including the ones whose
    own observations are sitting in it.

    `factor_id` narrows the read to one factor **in SQL**, as an equality filter on the stored
    column, so a partition holding twenty factors does not materialise nineteen of them.
    """
    requirement = factor_observation_requirement(years=years, as_of=as_of)
    found: list[FactorObservation] = []
    for year in sorted(set(years)):
        outcome = store.read_visible_at(
            requirement,
            year=year,
            columns=(EVENT_TIME_COLUMN, *FACTOR_OBSERVATION_PANEL_COLUMNS),
            filters=None if factor_id is None else {"factor_id": factor_id},
        )
        if outcome.is_blocked:
            raise FactorEngineError(
                f"{FACTOR_OBSERVATION_DATASET} year={year} cannot be read at "
                f"{as_of.isoformat()}: {[issue.code for issue in outcome.readiness.issues]}"
            )
        found.extend(_observation_from_row(row) for row in outcome.rows)
    return tuple(found)


def _observation_from_row(row: Sequence[object]) -> FactorObservation:
    """Rebuild one observation from a row shaped `(event_time, *FACTOR_OBSERVATION_PANEL_COLUMNS)`.

    Refuses a row of the wrong width rather than unpacking it positionally into whatever fits:
    a partition written by a build with a different column list would otherwise decode into
    plausible values in the wrong fields.
    """
    expected = 1 + len(FACTOR_OBSERVATION_PANEL_COLUMNS)
    if len(row) != expected:
        raise FactorEngineError(
            f"a {FACTOR_OBSERVATION_DATASET} row has {len(row)} values, expected {expected} "
            f"({EVENT_TIME_COLUMN}, {', '.join(FACTOR_OBSERVATION_PANEL_COLUMNS)})"
        )
    cells = dict(zip((EVENT_TIME_COLUMN, *FACTOR_OBSERVATION_PANEL_COLUMNS), row, strict=True))
    as_of = cells[EVENT_TIME_COLUMN]
    if not isinstance(as_of, datetime):
        raise FactorEngineError(
            f"a {FACTOR_OBSERVATION_DATASET} row carries {type(as_of).__name__} for "
            f"{EVENT_TIME_COLUMN}, not a datetime"
        )
    value = cells["value"]
    first = cells["input_session_first"]
    last = cells["input_session_last"]
    return FactorObservation(
        subject=str(cells[SUBJECT_COLUMN_NAME]),
        as_of=as_of,
        value=None if value is None else float(str(value)),
        coverage=_coverage_code(cells["coverage"]),
        factor_id=str(cells["factor_id"]),
        manifest_id=str(cells["manifest_id"]),
        input_row_count=int(str(cells["input_row_count"])),
        input_session_first=None if first is None else date.fromisoformat(str(first)),
        input_session_last=None if last is None else date.fromisoformat(str(last)),
    )


def _coverage_code(value: object) -> FactorCoverage:
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
        f"a {FACTOR_OBSERVATION_DATASET} row carries coverage {text!r}, which this build does "
        f"not declare ({list(FACTOR_COVERAGE_ORDER)}); it was written by a build that knows a "
        "code this one does not"
    )
