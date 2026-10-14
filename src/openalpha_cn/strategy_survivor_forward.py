"""The survivor forward report's prediction layer (`V2-P7-018`, Part E).

The survivor configuration's only evidence is its forward record: one `survivor_book_composite/v1`
batch a session, filed before the next session's call auction, scoring every eligible name with
its composite (`strategy_survivor_registration`). This module turns those records into the
prediction layer of the weekly report -- the one layer of it that can ever be evidence -- and
nothing else: the paper ledger is a record layer (`strategy_survivor_paper_report`), and the
document and its printed lines are the report script's (`survivor_forward_document`,
`survivor_forward_lines`, Task E4). It reads nothing itself; every read is the caller's, handed
in as a label reader, a prediction store and the index levels.

Every number here is a **candidate forecast of a registered research configuration; not a
performance claim** (`PREDICTION_LAYER_WORDING`). Before the pre-registered window has matured
the layer gives counts and descriptive means only, each labelled `DESCRIPTIVE_WORDING`, no
p-value and no verdict word -- "not tested: k of K pre-registered periods matured".

## The protocol (`read_forward_protocol`)

Read from the registration's `settings["survivor_forward_protocol"]` -- the configuration's own
top-level keys are closed -- as a `survivor-forward-protocol/v1` block, `extra="forbid"`, with
**no default for any field**: the values are frozen at the `V2-P7-006` checkpoint, and a default
here would be a decision taken by code. A missing or invalid block refuses the report by name
(`SurvivorForwardError`). Integers are strict (a JSON `true` is not a count). How the fields are
read:

- `anchor_ordinal` is the first sampled session's ordinal in the paper book's count
  (`SessionOrdinals`; ordinal 0 is the registered epoch, which carries no record, so it is at
  least 1);
- `primary_horizon` and `secondary_horizons` are session counts. The record is the same record at
  every horizon: a secondary horizon reads it with a shorter (or longer) label window;
- `primary_metric` is tested at the primary horizon; `secondary_metrics` are the other metrics
  tested at the primary horizon, and at each secondary horizon only `primary_metric` is tested.
  Those two, together, are the secondary family -- `q` is its false-discovery rate, which the
  report's document applies across the horizons (this module tests one horizon at a time);
- `window_periods` (K) is the pre-registered number of non-overlapping periods; the test is made
  once, over the first K that matured, and never before K have;
- `bootstrap_samples` and `random_seed` are `outcome_statistics.sign_flip_test`'s.

## The witnessed records (`witnessed_survivor_records`)

The prediction store is the witness. A record counts when it is **bound** to the registered
configuration (`survivor_record_is_bound`: its declaration is the one `survivor_declaration`
gives), was **registered before its session's cutoff** (`registered_at` before
`registration_cutoff`, 09:15 of the next session, P6's rule), and its session has ordinal 1 or
more (after the epoch) and is not after `through`. A late record is not a registration and is
skipped, as P6's `witnessed_days` skips one; two on-time bound records for one session refuse the
report -- a session has one record.

## One record's outcome (`record_scope`, `record_outcome`)

**The scope and its deciles come before any outcome.** The scope is the record's predictions --
every eligible name, scored by its composite. Ordered worst first by `(score, ts_code)`, the
kernel's own order (`domain/survivor_screen.screen`), the name at position `p` of `n` is in decile
`floor(10 p / n)`; the survivors are the names left after the worst `n * loser_decile_count // 10`
are dropped, which is exactly how `screen` cuts them, so a record rebuilds the day's
`screen().survivors` from its own scores.

**The label is the shared reader's** (`model_view.OutcomeLabels`, through the structural
`LabelSource`): the window opens at the close of the session after the record's (D+1) and exits
at the close `h` sessions later (D+1+h) -- `build_label_window`'s rule, so at h=20 its last close
is the record's own `outcome_known_at`, and at h=5 the same record is read over five sessions.
**A record is mature only when the report's `as_of` is at or after the exit day's 16:30
publication** (`session_publication_instant`). An immature record is counted and never labelled:
no label of it is asked for, so no price after `as_of` can enter the report. A window that runs
past the stored calendar is immature when the calendar reaches the report's own date (the exit is
then after it), and refuses the report otherwise -- the calendar has to be built forward first.

What one mature record gives, every number gross and over the **labelled** scope only (an
unlabelled name is left out of every number, and counted per decile and per refusal code --
`domain/labels`' codes, a name with two counted under both, and `no_adjustment_history` for a name
the reader has no factor series for):

- `rank_ic`: the tie-aware Spearman correlation of score and label -- `factor_ic.average_ranks`
  on both, then `statistics.correlation` -- absent with a code when a side is degenerate
  (`fewer_than_two_labelled`, `scores_tied`, `labels_tied`);
- `investable_ew`: the mean label, an equal-weight buy-and-hold of the investable scope;
- `survivors_minus_ew`, `worst_decile_excess` (decile 0 less the equal weight) and
  `top_minus_bottom` (decile 9 less decile 0);
- `csi500`: `close(exit) / close(entry) - 1` of the protocol's benchmark (`000905.SH`), over the
  same two closes; context only, and absent with a code when either close is missing.

## The sample and the test (`sampled_sessions`, `prediction_layer`)

Overlapping h-session labels of consecutive days are not independent, so the test is made on a
**non-overlapping** sample (the repository's `grid.non_overlapping` rule, not Newey-West): the
sessions `anchor + h k`, counted on the **calendar's** sessions. A sampled session with no
witnessed record, or with one the late-record check refused or never checked, is a **gap**: it
is named, and it does not shift the later picks -- the sample is a property of the calendar, not
of which days produced a record. A sampled record not yet mature is pending, neither a period
nor a gap. Every included mature daily record also enters a descriptive mean, printed as a point
estimate with no standard error.

The headline includes the records the check verified and the ones it found UNVERIFIABLE (P6's
convention); `excluding_unverifiable` is the same layer with every UNVERIFIABLE record's session
turned into a gap.
"""

from __future__ import annotations

import dataclasses
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Final, Literal, Protocol, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openalpha_cn.backtest.factor_ic import average_ranks
from openalpha_cn.backtest.outcome_statistics import (
    MINIMUM_INTERVAL_SAMPLE_SIZE,
    RandomizationTest,
    sign_flip_test,
)
from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.horizon import MAX_HORIZON_COUNT, HorizonUnit, ResearchHorizon
from openalpha_cn.domain.index_prices import IndexBar
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import (
    CalendarHorizonError,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_registration import (
    SHANGHAI,
    HeldRecordLookup,
    RegisteredConfiguration,
    StrategyRegistrationError,
    registered_at,
    registration_cutoff,
)
from openalpha_cn.strategy_survivor_market import (
    PROTOCOL_BENCHMARK,
    SessionOrdinalError,
    SessionOrdinals,
)
from openalpha_cn.strategy_survivor_registration import survivor_record_is_bound

__all__ = [
    "DESCRIBED_QUANTITIES",
    "DESCRIPTIVE_WORDING",
    "FORWARD_METRICS",
    "PREDICTION_LAYER_WORDING",
    "SURVIVOR_FORWARD_PROTOCOL_KEY",
    "SURVIVOR_FORWARD_PROTOCOL_SCHEMA",
    "DescribedQuantity",
    "DescriptiveMean",
    "ForwardMetric",
    "LabelSource",
    "LayerReading",
    "MetricTest",
    "NonOverlappingSample",
    "PredictionLayer",
    "RecordOutcome",
    "RecordScope",
    "SampledPeriod",
    "SampledSessions",
    "ScopedName",
    "SurvivorForwardError",
    "SurvivorForwardProtocol",
    "VerdictLike",
    "WitnessedRecord",
    "prediction_layer",
    "read_forward_protocol",
    "record_outcome",
    "record_scope",
    "sampled_sessions",
    "witnessed_survivor_records",
]

SURVIVOR_FORWARD_PROTOCOL_KEY: Final = "survivor_forward_protocol"
"""The key of the registration's `settings` that holds the forward protocol."""
SURVIVOR_FORWARD_PROTOCOL_SCHEMA: Final = "survivor-forward-protocol/v1"

PREDICTION_LAYER_WORDING: Final = (
    "candidate forecast of a registered research configuration; not a performance claim"
)
DESCRIPTIVE_WORDING: Final = "descriptive, not a test"

ForwardMetric = Literal["rank_ic", "survivors_minus_ew", "worst_decile_excess", "top_minus_bottom"]
"""A quantity the protocol may test: each is centred on zero under the null of no forecast."""
FORWARD_METRICS: Final[tuple[ForwardMetric, ...]] = get_args(ForwardMetric)
DescribedQuantity = Literal[
    "rank_ic",
    "investable_ew",
    "survivors_minus_ew",
    "worst_decile_excess",
    "top_minus_bottom",
    "csi500",
]
"""Every quantity one record reports, in the order a layer describes them. The equal weight and
the benchmark are levels, not excesses, so they are described and never tested."""
DESCRIBED_QUANTITIES: Final[tuple[DescribedQuantity, ...]] = get_args(DescribedQuantity)
Role = Literal["primary", "secondary"]
Standing = Literal["verified", "unverifiable", "refused", "unchecked"]
GapReason = Literal["no_record", "refused", "unchecked", "unverifiable"]

NO_ADJUSTMENT_HISTORY: Final = "no_adjustment_history"
"""The unlabelled code of a name the reader has no factor series for (it answers `None`)."""
_VERDICTS: Final[Mapping[str, Standing]] = {
    "verified": "verified",
    "unverifiable": "unverifiable",
    "refused": "refused",
}
"""A check's verdict as the layer's standing of its record (a record with none is unchecked)."""
_GAP_REASONS: Final[Mapping[str, GapReason]] = {
    "refused": "refused",
    "unchecked": "unchecked",
    "unverifiable": "unverifiable",
}
_DECILES: Final[int] = 10


class SurvivorForwardError(StrategyRegistrationError):
    """The survivor forward report's prediction layer cannot be produced as asked: no protocol
    or an invalid one, two records for one session, a sample or a window that cannot be drawn.

    A `StrategyRegistrationError`, so the report script's existing refusal of a registration
    error holds this one too."""


# --- the protocol --------------------------------------------------------------------------------

_Count = Annotated[int, Field(strict=True, ge=1)]
_Horizon = Annotated[int, Field(strict=True, ge=1, le=MAX_HORIZON_COUNT)]


class SurvivorForwardProtocol(BaseModel):
    """The pre-registered forward protocol; see the module docstring. No field has a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-forward-protocol/v1"]
    anchor_ordinal: _Count
    primary_horizon: _Horizon
    secondary_horizons: tuple[_Horizon, ...]
    primary_metric: ForwardMetric
    secondary_metrics: tuple[ForwardMetric, ...]
    window_periods: _Count
    bootstrap_samples: Annotated[int, Field(strict=True, ge=100, le=100_000)]
    """`outcome_statistics.OutcomeStatisticsRequest`'s bounds: the same test by the same rule."""
    random_seed: Annotated[int, Field(strict=True, ge=0)]
    q: Annotated[float, Field(strict=True, gt=0, lt=1)]

    @model_validator(mode="after")
    def _the_secondaries_are_distinct_from_each_other_and_the_primary(self) -> Self:
        horizons = self.secondary_horizons
        if len(set(horizons)) != len(horizons) or self.primary_horizon in horizons:
            raise ValueError("secondary_horizons must not repeat a horizon or the primary_horizon")
        metrics = self.secondary_metrics
        if len(set(metrics)) != len(metrics) or self.primary_metric in metrics:
            raise ValueError("secondary_metrics must not repeat a metric or the primary_metric")
        return self

    def tested_metrics(self, horizon: int) -> tuple[tuple[ForwardMetric, Role], ...]:
        """What is tested at `horizon`, and in which role; a horizon not in the protocol refuses."""
        if horizon == self.primary_horizon:
            return (
                (self.primary_metric, "primary"),
                *((metric, "secondary") for metric in self.secondary_metrics),
            )
        if horizon in self.secondary_horizons:
            return ((self.primary_metric, "secondary"),)
        raise SurvivorForwardError(
            f"the protocol reads the horizons {self.primary_horizon} (primary) and "
            f"{list(self.secondary_horizons)} (secondary), not horizon {horizon}; a readout at a "
            "horizon nobody pre-registered is not part of the report"
        )


def read_forward_protocol(settings: Mapping[str, object]) -> SurvivorForwardProtocol:
    """The protocol in a registration's `settings`, or `SurvivorForwardError` naming why not."""
    if not isinstance(settings, Mapping):
        raise SurvivorForwardError(
            f"the registration's settings are {type(settings).__name__}, not an object, so they "
            f"carry no {SURVIVOR_FORWARD_PROTOCOL_KEY} block"
        )
    if SURVIVOR_FORWARD_PROTOCOL_KEY not in settings:
        raise SurvivorForwardError(
            f"the registration's settings carry no {SURVIVOR_FORWARD_PROTOCOL_KEY} block; the "
            "survivor forward report reads its window, its horizons, its metrics and its test "
            "from the protocol frozen at V2-P7-006, and is not produced without one"
        )
    block = settings[SURVIVOR_FORWARD_PROTOCOL_KEY]
    if not isinstance(block, Mapping):
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_PROTOCOL_KEY} is {type(block).__name__}, not "
            f"a {SURVIVOR_FORWARD_PROTOCOL_SCHEMA} object"
        )
    try:
        return SurvivorForwardProtocol.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'the block'}: {item['msg']}"
            for item in error.errors()
        )
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_PROTOCOL_KEY} is not a valid "
            f"{SURVIVOR_FORWARD_PROTOCOL_SCHEMA} block: {problems}"
        ) from error


# --- the witnessed records -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class WitnessedRecord:
    """One session's record, as the prediction store witnesses it."""

    session: date
    ordinal: int
    """The session's ordinal in the paper book's count (`SessionOrdinals`): 1 or more."""
    record_id: str
    record: PredictionRecord


def witnessed_survivor_records(
    records: HeldRecordLookup,
    *,
    book: SurvivorBookConfig,
    registered: RegisteredConfiguration,
    calendar: TradingCalendar,
    ordinals: SessionOrdinals,
    through: date,
) -> tuple[WitnessedRecord, ...]:
    """Every session's one bound, on-time record from ordinal 1 through `through`, by session.

    See the module docstring for the rules. Refuses two on-time bound records of one session.
    """
    on_time: dict[date, list[tuple[str, PredictionRecord]]] = {}
    for record_id in records.list_ids():
        record = records.get(record_id)
        if record is None or not survivor_record_is_bound(record, book, registered):
            continue
        session = record.batch.as_of.astimezone(SHANGHAI).date()
        if session <= ordinals.epoch or session > through:
            continue
        try:
            cutoff = registration_cutoff(calendar, session)
        except TradingCalendarError as error:
            raise SurvivorForwardError(
                f"the registration cutoff of {session.isoformat()} (09:15 of the next session) "
                f"cannot be read off the {calendar.exchange} calendar: {error}"
            ) from error
        if registered_at(record) >= cutoff:
            continue
        on_time.setdefault(session, []).append((record_id, record))
    witnessed: list[WitnessedRecord] = []
    for session in sorted(on_time):
        held = on_time[session]
        if len(held) > 1:
            raise SurvivorForwardError(
                f"{session.isoformat()} carries {len(held)} records bound to the registration, "
                f"each registered before its cutoff ({sorted(record_id for record_id, _ in held)})"
                "; a session has one record, and the report does not choose between them"
            )
        record_id, record = held[0]
        try:
            ordinal = ordinals.index_of(session)
        except SessionOrdinalError as error:
            raise SurvivorForwardError(
                f"{record_id} is bound to the registration and dated {session.isoformat()}, "
                f"which has no session ordinal: {error}"
            ) from error
        witnessed.append(
            WitnessedRecord(session=session, ordinal=ordinal, record_id=record_id, record=record)
        )
    return tuple(witnessed)


# --- the sampled sessions ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SampledSessions:
    """The non-overlapping sample at one horizon: `anchor + horizon * k` through `through`."""

    anchor_ordinal: int
    horizon: int
    through: date
    sessions: tuple[date, ...]


def sampled_sessions(
    ordinals: SessionOrdinals, *, anchor_ordinal: int, through: date, horizon: int
) -> SampledSessions:
    """The sessions with ordinal `anchor_ordinal + horizon * k` on or before `through`.

    Counted on the calendar's sessions (`ordinals`), never on the sessions that carry a record:
    a session with no record is a gap in the sample, not a reason to move the next pick.
    """
    if type(anchor_ordinal) is not int or anchor_ordinal < 1:
        raise SurvivorForwardError(
            f"the sample's anchor ordinal is {anchor_ordinal!r}; ordinal 0 is the registered "
            "epoch, which carries no record, so the anchor is a whole number of at least 1"
        )
    _require_horizon(horizon)
    try:
        last = ordinals.index_of(through)
    except SessionOrdinalError as error:
        raise SurvivorForwardError(
            f"the sample cannot be drawn through {through.isoformat()}: {error}"
        ) from error
    return SampledSessions(
        anchor_ordinal=anchor_ordinal,
        horizon=horizon,
        through=through,
        sessions=tuple(
            ordinals.day_of(ordinal) for ordinal in range(anchor_ordinal, last + 1, horizon)
        ),
    )


def _require_horizon(horizon: int) -> None:
    if type(horizon) is not int or not 1 <= horizon <= MAX_HORIZON_COUNT:
        raise SurvivorForwardError(
            f"the horizon is {horizon!r}; a label window spans a whole number of sessions from "
            f"1 to {MAX_HORIZON_COUNT}"
        )


# --- one record's scope --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopedName:
    """One name of a record's scope: its score, its worst-first position and its decile."""

    ts_code: str
    score: float
    position: int
    decile: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordScope:
    """A record's scope cut before any outcome: worst first, and the survivors best first."""

    names: tuple[ScopedName, ...]
    """Worst first by `(score, ts_code)`; `decile = floor(10 * position / n)`."""
    survivors: tuple[str, ...]
    """The scope less its worst `n * loser_decile_count // 10`, best first by `(-score,
    ts_code)` -- `screen().survivors`' codes, in their order."""


def record_scope(batch: PredictionBatch, *, book: SurvivorBookConfig) -> RecordScope:
    """The deciles and the survivors of a survivor record's scope; see the module docstring."""
    scored: list[tuple[float, str]] = []
    for prediction in batch.predictions:
        if prediction.score is None:
            raise SurvivorForwardError(
                f"{prediction.ts_code} abstains in the batch of {batch.as_of.isoformat()}; a "
                "survivor record scores every eligible name and abstains for none, so this is "
                "not one"
            )
        scored.append((prediction.score, prediction.ts_code))
    worst_first = sorted(scored)
    count = len(worst_first)
    dropped = count * book.loser_decile_count // _DECILES
    return RecordScope(
        names=tuple(
            ScopedName(
                ts_code=code, score=score, position=position, decile=_DECILES * position // count
            )
            for position, (score, code) in enumerate(worst_first)
        ),
        survivors=tuple(
            code
            for score, code in sorted(worst_first[dropped:], key=lambda pair: (-pair[0], pair[1]))
        ),
    )


# --- one record's outcome ------------------------------------------------------------------------


class LabelSource(Protocol):
    """What the outcome of one record needs of a label reader: `model_view.OutcomeLabels`."""

    @property
    def calendar(self) -> TradingCalendar: ...

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow: ...

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordOutcome:
    """One witnessed record read at one horizon; see the module docstring for every number.

    An immature record carries its scope and nothing measured: every number is `None`, and no
    name of it was labelled."""

    record_id: str
    session: date
    ordinal: int
    horizon: int
    label_as_of: datetime
    """The report's `as_of`, which every label of the report is read at."""
    entry_day: date | None
    exit_day: date | None
    """`None` only when the window runs past the stored calendar's end (an immature record)."""
    mature: bool
    scope: int
    survivors: int
    labelled: int
    unlabelled: int
    unlabelled_by_code: tuple[tuple[str, int], ...]
    """Names per refusal code, ascending by code; a name with two codes counts under both."""
    scope_by_decile: tuple[int, ...]
    unlabelled_by_decile: tuple[int, ...]
    rank_ic: float | None
    investable_ew: float | None
    survivors_minus_ew: float | None
    worst_decile_excess: float | None
    top_minus_bottom: float | None
    csi500: float | None
    absent: tuple[tuple[str, str], ...]
    """`(quantity, code)` for every number a mature record could not put a value on."""

    def value(self, quantity: DescribedQuantity) -> float | None:
        """The record's number for `quantity` (one of `DESCRIBED_QUANTITIES`)."""
        values: dict[str, float | None] = {
            "rank_ic": self.rank_ic,
            "investable_ew": self.investable_ew,
            "survivors_minus_ew": self.survivors_minus_ew,
            "worst_decile_excess": self.worst_decile_excess,
            "top_minus_bottom": self.top_minus_bottom,
            "csi500": self.csi500,
        }
        return values[quantity]


def record_outcome(
    labels: LabelSource,
    witnessed: WitnessedRecord,
    *,
    horizon: int,
    book: SurvivorBookConfig,
    csi500: Sequence[IndexBar],
    as_of: datetime,
) -> RecordOutcome:
    """`witnessed`'s record labelled over `horizon` sessions by `labels`, as of `as_of`.

    `csi500` is the protocol benchmark's level series (`panel_ingest.load_index_prices(...)
    [PROTOCOL_BENCHMARK]`). No label is read for a record that is not mature at `as_of`; the
    reader's own refusals propagate unchanged. See the module docstring.
    """
    _require_horizon(horizon)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SurvivorForwardError(f"the report's as_of {as_of.isoformat()} carries no timezone")
    foreign = sorted({bar.ts_code for bar in csi500 if bar.ts_code != PROTOCOL_BENCHMARK})
    if foreign:
        raise SurvivorForwardError(
            f"the benchmark levels handed in are {foreign}'s; the report's benchmark is "
            f"{PROTOCOL_BENCHMARK}, and another index's closes would be printed under its name"
        )
    record = witnessed.record
    scope = record_scope(record.batch, book=book)
    count = len(scope.names)
    scope_by_decile = _per_decile(scope.names)
    entry, window = _label_window(labels, record.batch.as_of, horizon=horizon, as_of=as_of)
    immature = RecordOutcome(
        record_id=witnessed.record_id,
        session=witnessed.session,
        ordinal=witnessed.ordinal,
        horizon=horizon,
        label_as_of=as_of,
        entry_day=entry,
        exit_day=None if window is None else window.exit_day,
        mature=False,
        scope=count,
        survivors=len(scope.survivors),
        labelled=0,
        unlabelled=0,
        unlabelled_by_code=(),
        scope_by_decile=scope_by_decile,
        unlabelled_by_decile=(0,) * _DECILES,
        rank_ic=None,
        investable_ew=None,
        survivors_minus_ew=None,
        worst_decile_excess=None,
        top_minus_bottom=None,
        csi500=None,
        absent=(),
    )
    if window is None or as_of < session_publication_instant(window.exit_day):
        return immature
    values: dict[str, float] = {}
    codes: Counter[str] = Counter()
    unlabelled = [
        name for name in scope.names if not _labelled(labels, name, window, values, codes)
    ]
    labelled = [name for name in scope.names if name.ts_code in values]
    absent: list[tuple[str, str]] = []

    def measured(quantity: str, value: float | None, code: str) -> float | None:
        if value is None:
            absent.append((quantity, code))
        return value

    rank_ic, rank_ic_code = _rank_ic(labelled, values)
    equal_weight = _mean(values[name.ts_code] for name in labelled)
    survivors = set(scope.survivors)
    survivor_mean = _mean(values[name.ts_code] for name in labelled if name.ts_code in survivors)
    worst = _mean(values[name.ts_code] for name in labelled if name.decile == 0)
    top = _mean(values[name.ts_code] for name in labelled if name.decile == _DECILES - 1)
    return dataclasses.replace(
        immature,
        mature=True,
        labelled=len(labelled),
        unlabelled=len(unlabelled),
        unlabelled_by_code=tuple(sorted(codes.items())),
        unlabelled_by_decile=_per_decile(unlabelled),
        rank_ic=measured("rank_ic", rank_ic, rank_ic_code),
        investable_ew=measured("investable_ew", equal_weight, "no_labelled_name"),
        survivors_minus_ew=measured(
            "survivors_minus_ew",
            _less(survivor_mean, equal_weight),
            "no_labelled_survivor",
        ),
        worst_decile_excess=measured(
            "worst_decile_excess",
            _less(worst, equal_weight),
            "no_labelled_name_in_the_worst_decile",
        ),
        top_minus_bottom=measured(
            "top_minus_bottom",
            _less(top, worst),
            "no_labelled_name_in_the_top_decile"
            if top is None
            else "no_labelled_name_in_the_worst_decile",
        ),
        csi500=measured(
            "csi500", _close_ratio(csi500, entry, window.exit_day), "benchmark_close_missing"
        ),
        absent=tuple(absent),
    )


def _label_window(
    labels: LabelSource, instant: datetime, *, horizon: int, as_of: datetime
) -> tuple[date | None, LabelWindow | None]:
    """The record's entry session and label window, or `None` for a window past the calendar.

    The reach is checked on the reader's own calendar before the reader is asked, so a window
    that runs past the calendar's end is told apart from every other refusal: immature when the
    calendar reaches the report's date (its exit is then after it), refused otherwise. A record
    whose session the calendar does not hold at all is refused: it is unknown, not immature.
    """
    calendar = labels.calendar
    day = instant.astimezone(SHANGHAI).date()
    reach = calendar.horizon
    if not reach.covers(day):
        raise SurvivorForwardError(
            f"the record of {day.isoformat()} is outside the {calendar.exchange} calendar the "
            f"labels are read on ({reach.first_date.isoformat()}..{reach.last_date.isoformat()}); "
            "read the labels over the years the records span"
        )
    entry: date | None = None
    try:
        entry = calendar.next_trading_day(day)
        calendar.shift(entry, horizon)
    except CalendarHorizonError as error:
        reported = as_of.astimezone(SHANGHAI).date()
        if reported <= reach.last_date:
            return entry, None
        raise SurvivorForwardError(
            f"the {horizon}-session label window of {day.isoformat()} leaves the "
            f"{calendar.exchange} calendar, which stops at "
            f"{calendar.horizon.last_date.isoformat()}, before the report's as_of "
            f"{reported.isoformat()}: whether the record has matured is unknown until the "
            f"calendar is built forward ({error})"
        ) from error
    window = labels.window(
        instant, horizon=ResearchHorizon(count=horizon, unit=HorizonUnit.trading_days)
    )
    return window.entry_day, window


def _labelled(
    labels: LabelSource,
    name: ScopedName,
    window: LabelWindow,
    values: dict[str, float],
    codes: Counter[str],
) -> bool:
    """Label one name into `values`, or count its refusal codes into `codes`; whether it was."""
    label = labels.label(name.ts_code, window)
    if label is None:
        codes[NO_ADJUSTMENT_HISTORY] += 1
        return False
    if not label.is_labelled:
        codes.update(sorted({refusal.code for refusal in label.refusals}))
        return False
    values[name.ts_code] = label.realized_return
    return True


def _rank_ic(
    labelled: Sequence[ScopedName], values: Mapping[str, float]
) -> tuple[float | None, str]:
    """The tie-aware Spearman correlation of score and label over the labelled names."""
    if len(labelled) < 2:
        return None, "fewer_than_two_labelled"
    scores = [name.score for name in labelled]
    returns = [values[name.ts_code] for name in labelled]
    if len(set(scores)) == 1:
        return None, "scores_tied"
    if len(set(returns)) == 1:
        return None, "labels_tied"
    return statistics.correlation(average_ranks(scores), average_ranks(returns)), ""


def _close_ratio(levels: Sequence[IndexBar], entry: date | None, exit_day: date) -> float | None:
    closes = {bar.trade_date: bar.close for bar in levels}
    opened = None if entry is None else closes.get(entry)
    closed = closes.get(exit_day)
    if opened is None or closed is None or opened <= 0:
        return None
    return closed / opened - 1.0


def _per_decile(names: Sequence[ScopedName]) -> tuple[int, ...]:
    counts = Counter(name.decile for name in names)
    return tuple(counts.get(decile, 0) for decile in range(_DECILES))


def _mean(values: Iterable[float]) -> float | None:
    present = list(values)
    return statistics.fmean(present) if present else None


def _less(value: float | None, base: float | None) -> float | None:
    return None if value is None or base is None else value - base


# --- the prediction layer ------------------------------------------------------------------------


class VerdictLike(Protocol):
    """What the layer reads of a late-record check's verdict (`SurvivorRecordVerdict`)."""

    @property
    def verdict(self) -> str:
        """`verified`, `unverifiable` or `refused`."""
        ...

    @property
    def changes(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class DescriptiveMean:
    """A point estimate with no standard error: a mean over the records it names, described."""

    quantity: DescribedQuantity
    n: int
    mean: float | None
    wording: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SampledPeriod:
    """One sampled session whose record is included and mature: its numbers at this horizon."""

    session: date
    record_id: str
    values: tuple[tuple[DescribedQuantity, float | None], ...]

    def value(self, quantity: DescribedQuantity) -> float | None:
        return dict(self.values)[quantity]


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricTest:
    """The one test of one metric over the first `window_periods` matured periods.

    `test` is `outcome_statistics.sign_flip_test`'s two-sided answer for "mean == 0";
    `one_sided_p_value` is the protocol's one-sided p for "mean > 0" from it -- half of it when
    the mean is positive, one less half of it otherwise (`scripts/research/grid.py`'s
    `one_sided_p_value` rule). Neither is a verdict: no threshold is applied here."""

    metric: ForwardMetric
    role: Role
    values: int
    mean: float | None
    test: RandomizationTest | None
    one_sided_p_value: float | None
    absence: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class NonOverlappingSample:
    """The sample at one horizon, its gaps and pending sessions, and the test once K matured."""

    anchor_ordinal: int
    horizon: int
    sampled: tuple[date, ...]
    gaps: tuple[tuple[date, GapReason], ...]
    pending: tuple[date, ...]
    """Sampled sessions whose included record has not matured: neither a period nor a gap."""
    periods: tuple[SampledPeriod, ...]
    matured: int
    """k: the sampled periods whose included record matured."""
    window_periods: int
    """K, the pre-registered window."""
    window_satisfied: bool
    means: tuple[DescriptiveMean, ...]
    """Every described quantity over all `periods`, descriptive."""
    tests: tuple[MetricTest, ...]
    """Empty until `window_satisfied`; then one per tested metric of the horizon."""
    status: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LayerReading:
    """The layer over one set of included records (the headline, or the sensitivity)."""

    included: int
    """Mature records the reading includes."""
    unlabelled_by_code: tuple[tuple[str, int], ...]
    unlabelled_by_decile: tuple[int, ...]
    scope_by_decile: tuple[int, ...]
    descriptive: tuple[DescriptiveMean, ...]
    """Every described quantity over every included mature daily record (overlapping)."""
    non_overlapping: NonOverlappingSample


@dataclass(frozen=True, slots=True, kw_only=True)
class PredictionLayer:
    """The prediction layer at one horizon; see the module docstring."""

    horizon: int
    role: Role
    label_as_of: datetime | None
    witnessed: int
    mature: int
    immature: int
    excluded: tuple[tuple[str, int], ...]
    """Witnessed records left out of every reading, by reason (`refused`, `unchecked`)."""
    headline: LayerReading
    """Verified and UNVERIFIABLE records (P6's convention)."""
    excluding_unverifiable: LayerReading
    """The same with every UNVERIFIABLE record's sampled session a gap."""
    wording: str


def prediction_layer(
    outcomes: Sequence[RecordOutcome],
    verdicts: Mapping[str, VerdictLike],
    *,
    protocol: SurvivorForwardProtocol,
    sampled: SampledSessions,
) -> PredictionLayer:
    """The prediction layer of `outcomes` -- one horizon's, `sampled.horizon` -- under `protocol`.

    `verdicts` are the late-record check's, by record id; a record without one is unchecked and
    left out. The sign-flip test is made only once K (`window_periods`) sampled periods have
    matured, over the first K of them. See the module docstring.
    """
    horizon = sampled.horizon
    metrics = protocol.tested_metrics(horizon)
    if sampled.anchor_ordinal != protocol.anchor_ordinal:
        raise SurvivorForwardError(
            f"the sample is anchored at ordinal {sampled.anchor_ordinal} and the protocol at "
            f"{protocol.anchor_ordinal}; the sample is the protocol's or it is not the test's"
        )
    by_session: dict[date, RecordOutcome] = {}
    for outcome in outcomes:
        if outcome.horizon != horizon:
            raise SurvivorForwardError(
                f"{outcome.record_id} is read at horizon {outcome.horizon} and the sample is "
                f"drawn at horizon {horizon}; a layer is one horizon's"
            )
        if outcome.session in by_session:
            raise SurvivorForwardError(
                f"{outcome.session.isoformat()} has two outcomes "
                f"({by_session[outcome.session].record_id}, {outcome.record_id}); a session has "
                "one record"
            )
        by_session[outcome.session] = outcome
    instants = {outcome.label_as_of for outcome in outcomes}
    if len(instants) > 1:
        raise SurvivorForwardError(
            f"the outcomes were labelled at {len(instants)} instants; a report reads every label "
            "at its one as_of"
        )
    standing = {session: _standing(outcome, verdicts) for session, outcome in by_session.items()}
    excluded = Counter(state for state in standing.values() if state in ("refused", "unchecked"))

    def reading(left_out: frozenset[str]) -> LayerReading:
        return _reading(
            by_session,
            standing,
            left_out=left_out,
            sampled=sampled,
            protocol=protocol,
            metrics=metrics,
        )

    return PredictionLayer(
        horizon=horizon,
        role=metrics[0][1],
        label_as_of=next(iter(instants), None),
        witnessed=len(by_session),
        mature=sum(1 for outcome in by_session.values() if outcome.mature),
        immature=sum(1 for outcome in by_session.values() if not outcome.mature),
        excluded=tuple(sorted(excluded.items())),
        headline=reading(frozenset({"refused", "unchecked"})),
        excluding_unverifiable=reading(frozenset({"refused", "unchecked", "unverifiable"})),
        wording=PREDICTION_LAYER_WORDING,
    )


def _standing(outcome: RecordOutcome, verdicts: Mapping[str, VerdictLike]) -> Standing:
    verdict = verdicts.get(outcome.record_id)
    if verdict is None:
        return "unchecked"
    named = _VERDICTS.get(verdict.verdict)
    if named is None:
        raise SurvivorForwardError(
            f"{outcome.record_id}'s late-record verdict is {verdict.verdict!r}, none of "
            f"{sorted(_VERDICTS)}"
        )
    return named


def _reading(
    by_session: Mapping[date, RecordOutcome],
    standing: Mapping[date, Standing],
    *,
    left_out: frozenset[str],
    sampled: SampledSessions,
    protocol: SurvivorForwardProtocol,
    metrics: tuple[tuple[ForwardMetric, Role], ...],
) -> LayerReading:
    included = [
        outcome
        for session, outcome in sorted(by_session.items())
        if outcome.mature and standing[session] not in left_out
    ]
    codes: Counter[str] = Counter()
    for outcome in included:
        codes.update(dict(outcome.unlabelled_by_code))
    return LayerReading(
        included=len(included),
        unlabelled_by_code=tuple(sorted(codes.items())),
        unlabelled_by_decile=_summed(outcome.unlabelled_by_decile for outcome in included),
        scope_by_decile=_summed(outcome.scope_by_decile for outcome in included),
        descriptive=_described(lambda quantity: [outcome.value(quantity) for outcome in included]),
        non_overlapping=_sample(
            by_session,
            standing,
            left_out=left_out,
            sampled=sampled,
            protocol=protocol,
            metrics=metrics,
        ),
    )


def _sample(
    by_session: Mapping[date, RecordOutcome],
    standing: Mapping[date, Standing],
    *,
    left_out: frozenset[str],
    sampled: SampledSessions,
    protocol: SurvivorForwardProtocol,
    metrics: tuple[tuple[ForwardMetric, Role], ...],
) -> NonOverlappingSample:
    gaps: list[tuple[date, GapReason]] = []
    pending: list[date] = []
    periods: list[SampledPeriod] = []
    for session in sampled.sessions:
        outcome = by_session.get(session)
        if outcome is None:
            gaps.append((session, "no_record"))
            continue
        state = standing[session]
        if state in left_out:
            gaps.append((session, _GAP_REASONS[state]))
            continue
        if not outcome.mature:
            pending.append(session)
            continue
        periods.append(
            SampledPeriod(
                session=session,
                record_id=outcome.record_id,
                values=tuple(
                    (quantity, outcome.value(quantity)) for quantity in DESCRIBED_QUANTITIES
                ),
            )
        )
    matured = len(periods)
    window = protocol.window_periods
    satisfied = matured >= window
    tests: tuple[MetricTest, ...] = ()
    if satisfied:
        tested = periods[:window]
        tests = tuple(_tested(metric, role, tested, protocol) for metric, role in metrics)
        status = f"tested once, over the first {window} of {matured} matured periods"
    else:
        status = f"not tested: {matured} of {window} pre-registered periods matured"
    return NonOverlappingSample(
        anchor_ordinal=sampled.anchor_ordinal,
        horizon=sampled.horizon,
        sampled=sampled.sessions,
        gaps=tuple(gaps),
        pending=tuple(pending),
        periods=tuple(periods),
        matured=matured,
        window_periods=window,
        window_satisfied=satisfied,
        means=_described(lambda quantity: [period.value(quantity) for period in periods]),
        tests=tests,
        status=status,
    )


def _tested(
    metric: ForwardMetric,
    role: Role,
    periods: Sequence[SampledPeriod],
    protocol: SurvivorForwardProtocol,
) -> MetricTest:
    values = tuple(value for period in periods if (value := period.value(metric)) is not None)
    if len(values) < MINIMUM_INTERVAL_SAMPLE_SIZE:
        return MetricTest(
            metric=metric,
            role=role,
            values=len(values),
            mean=statistics.fmean(values) if values else None,
            test=None,
            one_sided_p_value=None,
            absence=(
                f"{len(values)} of the {len(periods)} periods carry a {metric}; a sign-flip test "
                f"needs at least {MINIMUM_INTERVAL_SAMPLE_SIZE}"
            ),
        )
    test = sign_flip_test(
        values, bootstrap_samples=protocol.bootstrap_samples, random_seed=protocol.random_seed
    )
    mean = statistics.fmean(values)
    return MetricTest(
        metric=metric,
        role=role,
        values=len(values),
        mean=mean,
        test=test,
        one_sided_p_value=test.p_value / 2 if mean > 0 else 1 - test.p_value / 2,
        absence=None,
    )


def _described(
    values_of: Callable[[DescribedQuantity], Sequence[float | None]],
) -> tuple[DescriptiveMean, ...]:
    described: list[DescriptiveMean] = []
    for quantity in DESCRIBED_QUANTITIES:
        present = [value for value in values_of(quantity) if value is not None]
        described.append(
            DescriptiveMean(
                quantity=quantity,
                n=len(present),
                mean=statistics.fmean(present) if present else None,
                wording=DESCRIPTIVE_WORDING,
            )
        )
    return tuple(described)


def _summed(rows: Iterable[Sequence[int]]) -> tuple[int, ...]:
    totals = [0] * _DECILES
    for row in rows:
        for decile, count in enumerate(row):
            totals[decile] += count
    return tuple(totals)
