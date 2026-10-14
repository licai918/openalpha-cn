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
- `survivors_minus_ew` (the survivors less the equal weight), `ew_minus_worst_decile` (the
  equal weight less decile 0) and `top_minus_bottom` (decile 9 less decile 0);
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

## The report's document (`survivor_forward_document`, `survivor_forward_lines`; task E4)

The weekly report script (`scripts/forward_report.py`) reads everything and hands it in; this
module puts it into one `survivor_forward_report/v1` document and prints it. What the document
does on its own:

- **The journals may not contradict the store** (`check_journals_against_the_store`, design
  section 2(a)): a daily journal whose `prediction` stage names a record must name the one the
  store witnesses for its session, and one that filed none must have none; a disagreement
  refuses the report, naming the session.
- **Where the report runs through** (`report_through`): the newest session whose record can no
  longer be filed -- its registration cutoff, 09:15 of the next session, is at or before the
  report's `as_of` -- or a newer session that already carries a witnessed record. A session after
  it may still get its record, so it is neither missing nor a gap yet; every session from ordinal
  1 through it with no witnessed record is listed as missing.
- **One analysis, at the primary window.** The window is the primary horizon's headline sample:
  until it has K matured periods, no layer's test is printed -- a secondary horizon whose own
  sample matures faster (h=5 against h=20) is told "a secondary is tested in the one analysis at
  the primary window" -- and the secondary family is not corrected. Before the window: counts and
  descriptive means only, each labelled `DESCRIPTIVE_WORDING`, and no p-value anywhere.
- **Compromised** (design decision 9): a record the late-record check refused whose session falls
  in the sample a test is made over -- the sampled sessions up to the K-th matured period -- prints
  that test "compromised (n refused)". The test is still shown; nothing about it is hidden.
- **The secondary family** -- the protocol's secondary metrics at the primary horizon and its
  primary metric at each secondary horizon -- is corrected once the window is satisfied, over the
  one-sided p-values that exist, by Benjamini-Yekutieli under arbitrary dependence at the
  protocol's `q` (`backtest.multiple_testing.control_false_discovery_rate`, the repository's own;
  a secondary without a p-value still counts in the declared family size). A secondary result
  never changes the candidate wording.
- **The paper ledger and the relative return** are `strategy_survivor_paper_report.paper_record`'s
  two blocks as the script hands them in -- or, when that record was refused, the refusal in both
  (`paper_refusal`): a fault of the paper book never costs the prediction layer. The relative
  return's windows are the headline's sampled periods at the primary horizon
  (`sampled_benchmarks`); a period whose equal weight or CSI 500 is missing is named as left out.
- **What cannot be evaluated yet** (design section 6): each item is listed while it holds -- no
  survivor record yet, no outcome matured, the window not satisfied -- and the standing ones on
  every report.
- **The late-record check's results**: counts by verdict and by scope, every UNVERIFIABLE record
  with its changes and every refused one with its reason, and the UNVERIFIABLE and refused shares
  over every checked record and over the primary sample's. How many verdicts a run served from
  the verdict cache is the run's, not the report's, and is not in the document: a rerun at the
  same `as_of` serves them all and writes the same bytes.

Every section carries `SURVIVOR_WORDING`; the prediction layer adds `PREDICTION_LAYER_WORDING`;
the document ends with them and `NO_VERDICT_WORDING`, then the caller's integrity statement
(P6's `daily_selection.INTEGRITY`). No verdict word is printed, before the window or after it.
"""

from __future__ import annotations

import dataclasses
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal, Protocol, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openalpha_cn.backtest.factor_ic import average_ranks
from openalpha_cn.backtest.multiple_testing import (
    HypothesisTest,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
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
from openalpha_cn.strategy_survivor_registration import (
    SURVIVOR_FAMILY,
    survivor_record_is_bound,
)
from openalpha_cn.strategy_survivor_summary import SURVIVOR_WORDING

__all__ = [
    "DESCRIBED_QUANTITIES",
    "DESCRIPTIVE_WORDING",
    "FORWARD_METRICS",
    "NO_VERDICT_WORDING",
    "PREDICTION_LAYER_WORDING",
    "SURVIVOR_FORWARD_PROTOCOL_KEY",
    "SURVIVOR_FORWARD_PROTOCOL_SCHEMA",
    "SURVIVOR_FORWARD_REPORT_KIND",
    "CheckedVerdict",
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
    "SampledBenchmark",
    "SampledPeriod",
    "SampledSessions",
    "ScopedName",
    "SurvivorForwardError",
    "SurvivorForwardProtocol",
    "VerdictLike",
    "WitnessedRecord",
    "check_journals_against_the_store",
    "paper_refusal",
    "prediction_layer",
    "read_forward_protocol",
    "record_outcome",
    "record_scope",
    "report_through",
    "sampled_benchmarks",
    "sampled_sessions",
    "survivor_forward_document",
    "survivor_forward_lines",
    "witnessed_survivor_records",
]

SURVIVOR_FORWARD_PROTOCOL_KEY: Final = "survivor_forward_protocol"
"""The key of the registration's `settings` that holds the forward protocol."""
SURVIVOR_FORWARD_PROTOCOL_SCHEMA: Final = "survivor-forward-protocol/v1"

PREDICTION_LAYER_WORDING: Final = (
    "candidate forecast of a registered research configuration; not a performance claim"
)
DESCRIPTIVE_WORDING: Final = "descriptive, not a test"

ForwardMetric = Literal[
    "rank_ic", "survivors_minus_ew", "ew_minus_worst_decile", "top_minus_bottom"
]
"""A quantity the protocol may test: each is centred on zero under the null of no forecast, and
each is oriented the same way -- **greater than zero supports the screen** (the scores order the
labels, the survivors beat the equal weight, the worst decile falls behind it, the top decile
beats the worst). That is why one one-sided p, for "mean > 0", serves every one of them: the
equal weight's lead over the worst decile is tested, never the worst decile's excess over the
equal weight, whose support for the screen would be a mean below zero."""
FORWARD_METRICS: Final[tuple[ForwardMetric, ...]] = get_args(ForwardMetric)
DescribedQuantity = Literal[
    "rank_ic",
    "investable_ew",
    "survivors_minus_ew",
    "ew_minus_worst_decile",
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


def check_journals_against_the_store(
    witnessed: Sequence[WitnessedRecord],
    journals: Mapping[date, Mapping[str, Any]],
    *,
    through: date,
) -> None:
    """The store is the witness, and the journals may not contradict it (task E4).

    Every journal of a session on or before `through` whose `prediction` stage says its evening
    filed a record must name the record the store witnesses for that session -- bound, on time --
    and one whose stage says it filed none must have none. A journal without a `prediction`
    stage claims nothing, and a witnessed session without a journal is the store's word alone.
    `journals` are the daily command's survivor journals by session, read by the caller. Refuses
    the first session that disagrees, as P6's schedule does: neither side is preferred."""
    held = {item.session: item.record_id for item in witnessed}
    for session in sorted(journals):
        if session > through:
            continue
        stage = journals[session].get("prediction")
        if not isinstance(stage, Mapping):
            continue
        named = stage.get("record_id") if stage.get("registered") is True else None
        if named != held.get(session):
            raise SurvivorForwardError(
                f"the journal of {session.isoformat()} says its evening filed "
                f"{named or 'no record'} and the store witnesses {held.get(session) or 'none'} "
                "for that session (bound and on time): the store is the witness, and a journal "
                "that disagrees with it is not a record to report on"
            )


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
    ew_minus_worst_decile: float | None
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
            "ew_minus_worst_decile": self.ew_minus_worst_decile,
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
        ew_minus_worst_decile=None,
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
        ew_minus_worst_decile=measured(
            "ew_minus_worst_decile",
            _less(equal_weight, worst),
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
    `one_sided_p_value` rule). "Mean > 0" is the direction that supports the screen for every
    `ForwardMetric`, so the rule holds whichever metric is tested. Neither is a verdict: no
    threshold is applied here."""

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


# --- the report's document (task E4) -------------------------------------------------------------

SURVIVOR_FORWARD_REPORT_KIND: Final = "survivor_forward_report/v1"
NO_VERDICT_WORDING: Final = "no verdict before the pre-registered window"
_POINT_ESTIMATE: Final = "a point estimate with no standard error"
_SIGN_FLIP: Final = "one-sided sign-flip randomization (outcome_statistics.sign_flip_test)"
_BENJAMINI_YEKUTIELI: Final = (
    "Benjamini-Yekutieli, arbitrary dependence (backtest.multiple_testing)"
)
_DEPENDENCE: Final = "arbitrary"
_HEADLINE_BASIS: Final = (
    "the headline counts verified and UNVERIFIABLE records (P6's convention), the sensitivity "
    "excludes the UNVERIFIABLE ones, and refused records are in neither: they are excluded and "
    "listed"
)
_FAMILY_BASIS: Final = (
    "the secondary metrics at the primary horizon and the primary metric at each secondary "
    "horizon; corrected once the pre-registered window is satisfied, over the one-sided p-values "
    "that exist, a secondary without one still counted in the family; a secondary result never "
    "changes the candidate wording"
)
_SCOPES: Final[tuple[str, ...]] = (
    "not_bound",
    "no_provenance",
    "recompute_differs",
    "recompute_failed",
)
"""The late-record check's scopes (`strategy_survivor_check.VerdictScope`), counted by name."""
_BENCHMARKS: Final[tuple[DescribedQuantity, ...]] = ("investable_ew", "csi500")


class CheckedVerdict(VerdictLike, Protocol):
    """What the document reads of a late-record check's verdict (`SurvivorRecordVerdict`)."""

    @property
    def record_id(self) -> str: ...

    @property
    def session(self) -> date: ...

    @property
    def scope(self) -> str | None: ...

    @property
    def reason(self) -> str | None: ...

    def document(self) -> dict[str, object]: ...


def report_through(
    calendar: TradingCalendar,
    ordinals: SessionOrdinals,
    *,
    as_of: datetime,
    witnessed: Sequence[date],
) -> date:
    """The session the report's sample and its missing sessions run through (module docstring):
    the newest whose registration cutoff is at or before `as_of`, or the newest `witnessed`
    session when that is later; the epoch when no session after it has closed."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SurvivorForwardError(f"the report's as_of {as_of.isoformat()} carries no timezone")
    reported = as_of.astimezone(SHANGHAI).date()
    closed = ordinals.epoch
    for session in reversed(calendar.trading_days):
        if session > reported:
            continue
        if session <= ordinals.epoch:
            break
        try:
            cutoff = registration_cutoff(calendar, session)
        except TradingCalendarError:
            continue
        if cutoff <= as_of:
            closed = session
            break
    return max(closed, *witnessed) if witnessed else closed


@dataclass(frozen=True, slots=True, kw_only=True)
class SampledBenchmark:
    """One of the headline's sampled periods at the primary horizon, as the paper record's
    relative return reads it (`strategy_survivor_paper_report.BenchmarkWindow`): the record's
    entry and exit closes, its investable equal weight and the CSI 500 over the same closes."""

    record_id: str
    entry: date
    exit: date
    investable_ew: float
    csi500: float


def sampled_benchmarks(
    layer: PredictionLayer, outcomes: Sequence[RecordOutcome]
) -> tuple[SampledBenchmark, ...]:
    """The headline's sampled periods of `layer` with both benchmarks, each from its outcome in
    `outcomes`; a period missing either is left out (the document names it)."""
    by_id = {outcome.record_id: outcome for outcome in outcomes}
    picked: list[SampledBenchmark] = []
    for period in layer.headline.non_overlapping.periods:
        outcome = by_id.get(period.record_id)
        if outcome is None or outcome.horizon != layer.horizon:
            raise SurvivorForwardError(
                f"{period.record_id} is a sampled period of the h={layer.horizon} layer and no "
                "outcome of it at that horizon was handed in"
            )
        entry, exit_day = outcome.entry_day, outcome.exit_day
        ew, csi = outcome.investable_ew, outcome.csi500
        if entry is None or exit_day is None or ew is None or csi is None:
            continue
        picked.append(
            SampledBenchmark(
                record_id=outcome.record_id,
                entry=entry,
                exit=exit_day,
                investable_ew=ew,
                csi500=csi,
            )
        )
    return tuple(picked)


def paper_refusal(reason: str) -> dict[str, Any]:
    """The paper record's two sections when it was refused: the refusal, said in both."""
    return {
        "paper_ledger": {"wording": SURVIVOR_WORDING, "refused": reason},
        "relative": {"wording": SURVIVOR_WORDING, "refused": reason},
    }


def survivor_forward_document(
    *,
    as_of: datetime,
    config_id: str,
    registration_sha256: str,
    protocol: SurvivorForwardProtocol,
    ordinals: SessionOrdinals,
    through: date,
    witnessed: Sequence[date],
    verdicts: Mapping[str, CheckedVerdict],
    layers: Sequence[PredictionLayer],
    paper: Mapping[str, Any],
    integrity: str,
) -> dict[str, Any]:
    """The `survivor_forward_report/v1` document (module docstring).

    `witnessed` are the sessions of the witnessed records, `verdicts` the late-record check's by
    record id, `layers` one `prediction_layer` per horizon of the protocol (the primary and every
    secondary), `paper` `paper_record`'s `{"paper_ledger", "relative"}` or `paper_refusal`'s, and
    `integrity` the threat model every forward report states. JSON-safe, with no set iterated
    unsorted: the same inputs give the same document."""
    horizons = (protocol.primary_horizon, *protocol.secondary_horizons)
    by_horizon = {layer.horizon: layer for layer in layers}
    if len(by_horizon) != len(layers) or sorted(by_horizon) != sorted(horizons):
        raise SurvivorForwardError(
            f"the document reads one layer per horizon of the protocol ({list(horizons)}), and "
            f"was handed the horizons {[layer.horizon for layer in layers]}"
        )
    for section in ("paper_ledger", "relative"):
        if not isinstance(paper.get(section), Mapping):
            raise SurvivorForwardError(
                f"the paper record hands in no {section} section: it is paper_record's two "
                "sections or paper_refusal's"
            )
    primary = by_horizon[protocol.primary_horizon]
    analysis = primary.headline.non_overlapping
    sessions = tuple(sorted(witnessed))
    document: dict[str, Any] = {
        "kind": SURVIVOR_FORWARD_REPORT_KIND,
        "as_of": as_of.isoformat(),
        "config_id": config_id,
        "registration_sha256": registration_sha256,
        "family": SURVIVOR_FAMILY,
        "protocol": {
            "wording": SURVIVOR_WORDING,
            **protocol.model_dump(mode="json"),
            "periods_matured": analysis.matured,
            "window_satisfied": analysis.window_satisfied,
        },
        "records": _records_section(
            sessions, verdicts, ordinals=ordinals, through=through, primary=primary
        ),
        "prediction_layer": {
            "wording": SURVIVOR_WORDING,
            "forecast_wording": PREDICTION_LAYER_WORDING,
            **{
                f"h{horizon}": _layer_section(by_horizon[horizon], analysis=analysis)
                for horizon in horizons
            },
            "secondary_family": _secondary_family(by_horizon, protocol=protocol, analysis=analysis),
        },
        "paper_ledger": dict(paper["paper_ledger"]),
        "relative": _relative_section(paper["relative"], primary),
        "cannot_be_evaluated_yet": _cannot_be_evaluated_yet(
            sessions, primary=primary, protocol=protocol
        ),
        "wording": [SURVIVOR_WORDING, PREDICTION_LAYER_WORDING, NO_VERDICT_WORDING],
        "integrity": integrity,
    }
    return document


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _records_section(
    sessions: Sequence[date],
    verdicts: Mapping[str, CheckedVerdict],
    *,
    ordinals: SessionOrdinals,
    through: date,
    primary: PredictionLayer,
) -> dict[str, Any]:
    checked = sorted(verdicts.values(), key=lambda item: (item.session, item.record_id))
    counts = Counter(item.verdict for item in checked)
    scopes = Counter(item.scope for item in checked if item.scope is not None)
    sampled = set(primary.headline.non_overlapping.sampled)
    held = set(sessions)
    try:
        last = ordinals.index_of(through)
    except SessionOrdinalError as error:
        raise SurvivorForwardError(
            f"the report runs through {through.isoformat()}, which has no session ordinal: {error}"
        ) from error
    missing = [
        session.isoformat()
        for session in (ordinals.day_of(ordinal) for ordinal in range(1, last + 1))
        if session not in held
    ]
    return {
        "wording": SURVIVOR_WORDING,
        "witnessed": len(sessions),
        "first_session": _day(sessions[0]) if sessions else None,
        "newest_session": _day(sessions[-1]) if sessions else None,
        "through": through.isoformat(),
        "missing_sessions": missing,
        "checked": len(checked),
        "by_verdict": {
            "verified": counts["verified"],
            "unverifiable": counts["unverifiable"],
            "refused": counts["refused"],
            "unchecked": dict(primary.excluded).get("unchecked", 0),
        },
        "by_scope": {scope: scopes.get(scope, 0) for scope in sorted({*_SCOPES, *scopes})},
        "unverifiable": [item.document() for item in checked if item.verdict == "unverifiable"],
        "refused": [item.document() for item in checked if item.verdict == "refused"],
        "shares": {
            "overall": _shares(checked),
            "in_sample": _shares([item for item in checked if item.session in sampled]),
            "sample": (
                f"the h={primary.horizon} non-overlapping sample's sessions that carry a record"
            ),
        },
        "headline_basis": _HEADLINE_BASIS,
    }


def _shares(items: Sequence[CheckedVerdict]) -> dict[str, Any]:
    checked = len(items)
    unverifiable = sum(1 for item in items if item.verdict == "unverifiable")
    refused = sum(1 for item in items if item.verdict == "refused")
    return {
        "checked": checked,
        "unverifiable": unverifiable,
        "refused": refused,
        "unverifiable_share": unverifiable / checked if checked else None,
        "refused_share": refused / checked if checked else None,
    }


def _layer_section(layer: PredictionLayer, *, analysis: NonOverlappingSample) -> dict[str, Any]:
    excluded = dict(layer.excluded)
    return {
        "wording": SURVIVOR_WORDING,
        "forecast_wording": PREDICTION_LAYER_WORDING,
        "horizon": layer.horizon,
        "role": layer.role,
        "label_as_of": None if layer.label_as_of is None else layer.label_as_of.isoformat(),
        "witnessed": layer.witnessed,
        "mature": layer.mature,
        "immature": layer.immature,
        "excluded": {
            "refused": excluded.get("refused", 0),
            "unchecked": excluded.get("unchecked", 0),
        },
        **_reading_section(layer.headline, analysis=analysis),
        "excluding_unverifiable": _reading_section(layer.excluding_unverifiable, analysis=analysis),
    }


def _means(means: Sequence[DescriptiveMean]) -> dict[str, Any]:
    return {
        "wording": DESCRIPTIVE_WORDING,
        "estimate": _POINT_ESTIMATE,
        "means": {mean.quantity: {"n": mean.n, "mean": mean.mean} for mean in means},
    }


def _reading_section(reading: LayerReading, *, analysis: NonOverlappingSample) -> dict[str, Any]:
    sample = reading.non_overlapping
    refused = _refused_in_sample(sample)
    test: list[dict[str, Any]] | None = None
    if sample.window_satisfied and analysis.window_satisfied:
        test = [_test(item) for item in sample.tests]
        status = (
            sample.status if not refused else f"compromised ({refused} refused): {sample.status}"
        )
    elif sample.window_satisfied:
        status = (
            "not tested: a secondary is tested in the one analysis at the primary window "
            f"({analysis.matured} of {analysis.window_periods} primary periods matured)"
        )
    else:
        status = sample.status
    return {
        "included": reading.included,
        "scope_by_decile": list(reading.scope_by_decile),
        "unlabelled_by_decile": list(reading.unlabelled_by_decile),
        "unlabelled_by_code": dict(reading.unlabelled_by_code),
        "descriptive": _means(reading.descriptive),
        "non_overlapping": {
            "anchor_ordinal": sample.anchor_ordinal,
            "horizon": sample.horizon,
            "sampled": [session.isoformat() for session in sample.sampled],
            "gaps": [
                {"session": session.isoformat(), "reason": reason}
                for session, reason in sample.gaps
            ],
            "pending": [session.isoformat() for session in sample.pending],
            "values": [
                {
                    "session": period.session.isoformat(),
                    "record_id": period.record_id,
                    **dict(period.values),
                }
                for period in sample.periods
            ],
            "matured": sample.matured,
            "window_periods": sample.window_periods,
            "window_satisfied": sample.window_satisfied,
            "descriptive": _means(sample.means),
            "refused_in_sample": refused,
            "test": test,
            "status": status,
        },
    }


def _refused_in_sample(sample: NonOverlappingSample) -> int:
    """Refused records among the sampled sessions a test is made over: up to the K-th matured
    period once the window is satisfied, every one sampled so far before it."""
    last = sample.periods[sample.window_periods - 1].session if sample.window_satisfied else None
    return sum(
        1
        for session, reason in sample.gaps
        if reason == "refused" and (last is None or session <= last)
    )


def _test(test: MetricTest) -> dict[str, Any]:
    drawn = test.test
    return {
        "metric": test.metric,
        "role": test.role,
        "values": test.values,
        "mean": test.mean,
        "procedure": _SIGN_FLIP,
        "null_hypothesis": None if drawn is None else drawn.null_hypothesis,
        "two_sided_p_value": None if drawn is None else drawn.p_value,
        "one_sided_p_value": test.one_sided_p_value,
        "exact": None if drawn is None else drawn.exact,
        "sign_patterns": None if drawn is None else drawn.sign_patterns,
        "random_seed": None if drawn is None else drawn.random_seed,
        "absence": test.absence,
    }


def _secondary_family(
    by_horizon: Mapping[int, PredictionLayer],
    *,
    protocol: SurvivorForwardProtocol,
    analysis: NonOverlappingSample,
) -> dict[str, Any]:
    members: list[tuple[str, int, ForwardMetric]] = [
        (f"h{protocol.primary_horizon}.{metric}", protocol.primary_horizon, metric)
        for metric in protocol.secondary_metrics
    ]
    members += [
        (f"h{horizon}.{protocol.primary_metric}", horizon, protocol.primary_metric)
        for horizon in protocol.secondary_horizons
    ]

    def corrected(pick: Callable[[PredictionLayer], LayerReading]) -> dict[str, Any]:
        if not members:
            return {
                "status": "not applied: the protocol registers no secondary hypothesis",
                "tests": [],
            }
        if not analysis.window_satisfied:
            return {
                "status": "not applied: before the pre-registered window there is no p-value to "
                f"correct ({analysis.matured} of {analysis.window_periods} primary periods "
                "matured)",
                "tests": [],
            }
        p_values: dict[str, float] = {}
        for name, horizon, metric in members:
            for item in pick(by_horizon[horizon]).non_overlapping.tests:
                tested = (item.metric, item.role) == (metric, "secondary")
                if tested and item.one_sided_p_value is not None:
                    p_values[name] = item.one_sided_p_value
        if not p_values:
            return {"status": "not applied: no secondary hypothesis carries a p-value", "tests": []}
        report = control_false_discovery_rate(
            MultipleTestingRequest(
                tests=tuple(
                    HypothesisTest(hypothesis_id=name, p_value=p_value, test=_SIGN_FLIP)
                    for name, p_value in p_values.items()
                ),
                family_size=len(members),
                false_discovery_rate=protocol.q,
                dependence=_DEPENDENCE,
            )
        )
        tests: list[dict[str, Any]] = []
        for name, _horizon, _metric in members:
            verdict = report.verdict_for(name)
            if verdict is not None:
                tests.append(
                    {
                        "hypothesis_id": name,
                        "one_sided_p_value": p_values[name],
                        "q_value": verdict.q_value,
                        "rejected": verdict.rejected,
                    }
                )
        withheld = len(members) - len(p_values)
        return {
            "status": f"applied over {len(p_values)} of {len(members)} secondary hypotheses"
            + (f"; {withheld} without a p-value count in the family" if withheld else ""),
            "tests": tests,
        }

    return {
        "wording": SURVIVOR_WORDING,
        "procedure": _BENJAMINI_YEKUTIELI,
        "dependence": _DEPENDENCE,
        "q": protocol.q,
        "family": [name for name, _horizon, _metric in members],
        "family_size": len(members),
        "basis": _FAMILY_BASIS,
        "headline": corrected(lambda layer: layer.headline),
        "excluding_unverifiable": corrected(lambda layer: layer.excluding_unverifiable),
    }


def _relative_section(relative: Mapping[str, Any], primary: PredictionLayer) -> dict[str, Any]:
    if "refused" in relative:
        return dict(relative)
    left_out = [
        {
            "record_id": period.record_id,
            "session": period.session.isoformat(),
            "missing": [quantity for quantity in _BENCHMARKS if period.value(quantity) is None],
        }
        for period in primary.headline.non_overlapping.periods
        if any(period.value(quantity) is None for quantity in _BENCHMARKS)
    ]
    return {**relative, "windows_left_out": left_out}


def _cannot_be_evaluated_yet(
    sessions: Sequence[date], *, primary: PredictionLayer, protocol: SurvivorForwardProtocol
) -> list[dict[str, str]]:
    horizon = protocol.primary_horizon
    analysis = primary.headline.non_overlapping
    items: list[tuple[str, str]] = []
    if not sessions:
        items.append(
            (
                "no_survivor_record_yet",
                "no survivor record exists yet: daily registration has been paused since "
                "2026-10-08, and the configuration is registered once at V2-P7-006, after "
                "Parts D and E",
            )
        )
    if primary.mature == 0:
        items.append(
            (
                "first_outcome_not_matured",
                f"the first h={horizon} outcome matures about {horizon + 1} sessions after the "
                "first evening; no record has matured yet",
            )
        )
    if not analysis.window_satisfied:
        items.append(
            (
                "window_not_satisfied",
                f"the pre-registered window needs {analysis.window_periods} non-overlapping "
                f"{horizon}-session periods: {analysis.matured} of {analysis.window_periods} "
                "have matured",
            )
        )
    items += [
        (
            "g0_needs_research_and_validation_conclusions",
            "G-0 needs conclusions on the research and validation windows first",
        ),
        (
            "spent_window_2024_01_02_to_2026_09_24",
            "2024-01-02..2026-09-24 is spent: nothing from that window is evidence here",
        ),
        ("paper_equity_is_not_evidence", "paper equity is not evidence"),
        (
            "t8_expected_numbers_are_optimistic",
            "T8's expected numbers are about 0.9 pp a year optimistic (T9) and must be "
            "re-measured; no expected number is printed beside a forward result",
        ),
        ("ic_2021", "2021's IC was +0.037 (exploratory, not evidence)"),
    ]
    items += [
        (
            f"h{secondary}_ic_dispersion_unmeasured",
            f"the dispersion of the h={secondary} IC has never been measured",
        )
        for secondary in protocol.secondary_horizons
    ]
    items.append(("no_null_signal_control", "there is no null-signal control yet (V2-P7-002)"))
    return [{"code": code, "statement": statement} for code, statement in items]


# --- the report's lines ---------------------------------------------------------------------------


def survivor_forward_lines(document: Mapping[str, Any]) -> list[str]:
    """`document` as the lines the report prints: the records and their refusals first, then
    the prediction layer, the paper ledger, the relative return, what cannot be evaluated yet,
    the wording and the integrity statement. Read from the document alone, so a document read
    back from its file prints the same lines."""
    protocol = document["protocol"]
    records = document["records"]
    horizons = (protocol["primary_horizon"], *protocol["secondary_horizons"])
    lines = [
        f"as_of              {document['as_of']}",
        f"config_id          {document['config_id']}",
        f"report             {document['kind']}, family {document['family']}, registration "
        f"{document['registration_sha256'][:16]}",
        f"protocol           {protocol['schema_version']}: anchor ordinal "
        f"{protocol['anchor_ordinal']}, primary h={protocol['primary_horizon']} "
        f"{protocol['primary_metric']}, secondary horizons {protocol['secondary_horizons']}, "
        f"window {protocol['window_periods']} periods, {protocol['periods_matured']} matured",
    ]
    lines += _records_lines(records)
    layer = document["prediction_layer"]
    lines.append(f"prediction layer -- {layer['forecast_wording']}")
    for horizon in horizons:
        lines += _layer_lines(layer[f"h{horizon}"])
    lines += _family_lines(layer["secondary_family"])
    lines += _ledger_lines(document["paper_ledger"])
    lines += _relative_lines(document["relative"])
    lines.append("cannot be evaluated yet:")
    lines += [f"  - {item['statement']}" for item in document["cannot_be_evaluated_yet"]]
    lines += [f"wording: {text}" for text in document["wording"]]
    lines.append(document["integrity"])
    return lines


def _number(value: object) -> str:
    return "n/a" if value is None else f"{value:.6f}"


def _records_lines(records: Mapping[str, Any]) -> list[str]:
    counts = records["by_verdict"]
    span = (
        f"{records['first_session']}..{records['newest_session']}"
        if records["witnessed"]
        else "none"
    )
    lines = [
        f"records: {records['witnessed']} witnessed ({span}) through {records['through']}, "
        f"{len(records['missing_sessions'])} missing; checked {records['checked']}: verified "
        f"{counts['verified']}, UNVERIFIABLE {counts['unverifiable']}, refused "
        f"{counts['refused']}, unchecked {counts['unchecked']}"
    ]
    lines += [
        f"  REFUSED {item['record_id']} ({item['session']}, {item['scope']}): {item['reason']}"
        for item in records["refused"]
    ]
    lines += [
        f"  UNVERIFIABLE {item['record_id']} ({item['session']}, {item['scope']}): changes "
        f"{'; '.join(item['changes'])} -- counted in the headline, excluded in the sensitivity"
        for item in records["unverifiable"]
    ]
    if records["missing_sessions"]:
        lines.append(f"  missing sessions: {', '.join(records['missing_sessions'])}")
    overall, sampled = records["shares"]["overall"], records["shares"]["in_sample"]
    lines.append(
        f"  shares: UNVERIFIABLE {overall['unverifiable']} of {overall['checked']} checked, "
        f"{sampled['unverifiable']} of {sampled['checked']} in the sample; refused "
        f"{overall['refused']} of {overall['checked']}, {sampled['refused']} of "
        f"{sampled['checked']} in the sample; {records['headline_basis']}"
    )
    return lines


def _layer_lines(layer: Mapping[str, Any]) -> list[str]:
    excluded = layer["excluded"]
    lines = [
        f"  h{layer['horizon']} ({layer['role']}): witnessed {layer['witnessed']}, mature "
        f"{layer['mature']}, immature {layer['immature']}; labels as of {layer['label_as_of']}; "
        f"excluded refused {excluded['refused']}, unchecked {excluded['unchecked']}"
    ]
    lines += _reading_lines(layer, indent="    ")
    lines.append("    sensitivity, UNVERIFIABLE records excluded:")
    lines += _reading_lines(layer["excluding_unverifiable"], indent="      ")
    return lines


def _means_text(described: Mapping[str, Any]) -> str:
    means = described["means"]
    return ", ".join(
        f"{quantity} {_number(means[quantity]['mean'])} (n {means[quantity]['n']})"
        for quantity in DESCRIBED_QUANTITIES
        if quantity in means
    )


def _reading_lines(reading: Mapping[str, Any], *, indent: str) -> list[str]:
    sample = reading["non_overlapping"]
    described = reading["descriptive"]
    codes = reading["unlabelled_by_code"]
    lines = [
        f"{indent}{described['wording']} ({described['estimate']}), over {reading['included']} "
        f"mature daily records: {_means_text(described)}",
        f"{indent}unlabelled by code: "
        + (", ".join(f"{code} {count}" for code, count in sorted(codes.items())) or "none"),
        f"{indent}non-overlapping sample (anchor ordinal {sample['anchor_ordinal']}, every "
        f"{sample['horizon']} sessions): {len(sample['sampled'])} sampled, {sample['matured']} "
        f"matured, {len(sample['gaps'])} gaps, {len(sample['pending'])} pending",
    ]
    if sample["gaps"]:
        lines.append(
            f"{indent}gaps: "
            + ", ".join(f"{gap['session']} ({gap['reason']})" for gap in sample["gaps"])
        )
    if sample["values"]:
        lines.append(
            f"{indent}{sample['descriptive']['wording']}, over the matured periods: "
            f"{_means_text(sample['descriptive'])}"
        )
    lines.append(f"{indent}{sample['status']}")
    for test in sample["test"] or ():
        head = f"{indent}{test['metric']} ({test['role']}): {test['values']} periods"
        if test["absence"] is not None:
            lines.append(f"{head}, not tested -- {test['absence']}")
            continue
        drawn = (
            "exact"
            if test["exact"]
            else f"{test['sign_patterns']} sign patterns, seed {test['random_seed']}"
        )
        lines.append(
            f"{head}, mean {_number(test['mean'])}, one-sided p "
            f"{_number(test['one_sided_p_value'])} (two-sided {_number(test['two_sided_p_value'])}"
            f", {drawn})"
        )
    return lines


def _corrected_lines(tests: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        f"    {item['hypothesis_id']}: one-sided p {_number(item['one_sided_p_value'])}, "
        f"q {_number(item['q_value'])}, rejected {item['rejected']}"
        for item in tests
    ]


def _family_lines(family: Mapping[str, Any]) -> list[str]:
    headline, sensitivity = family["headline"], family["excluding_unverifiable"]
    return [
        f"  secondary family ({family['procedure']}, q {family['q']}; "
        f"{', '.join(family['family']) or 'none'}): {headline['status']}",
        *_corrected_lines(headline["tests"]),
        f"    sensitivity, UNVERIFIABLE records excluded: {sensitivity['status']}",
        *_corrected_lines(sensitivity["tests"]),
    ]


def _ledger_lines(ledger: Mapping[str, Any]) -> list[str]:
    if "refused" in ledger:
        return [f"paper ledger -- refused: {ledger['refused']}; the prediction layer above stands"]
    coverage, equity = ledger["coverage"], ledger["equity"]
    holdings, turnover = ledger["holdings"], ledger["turnover"]
    costs, write_offs = ledger["costs"], ledger["write_offs"]
    return [
        "paper ledger -- a record, never tested; ledger equity is not evidence",
        f"  coverage: {coverage['settled_sessions']} settled sessions "
        f"({coverage['first_settled']}..{coverage['last_settled']}): evenings "
        f"{coverage['evenings']}, settle-only {coverage['settle_only_days']}, caught up "
        f"{coverage['caught_up_sessions']}, refused days {len(coverage['refused_days'])}",
        f"  equity (ledger units): opening {equity['opening']['equity']}, final "
        f"{equity['final_equity']}, mean {equity['mean_equity']}, minimum "
        f"{equity['minimum_equity']}",
        f"  holdings: mean held {holdings['mean_held']} of {holdings['holding_count']}, "
        f"sessions below N {holdings['sessions_below_n']}, mean cash share "
        f"{holdings['mean_cash_share']}",
        f"  turnover: one-sided {turnover['one_sided_turnover']}, annualised "
        f"{turnover['annualised']}; costs {costs['total']['total']} (annualised share "
        f"{costs['total']['annualised_share_of_mean_equity']}); write-offs {write_offs['count']}",
        f"  {ledger['status']}",
    ]


def _relative_lines(relative: Mapping[str, Any]) -> list[str]:
    if "refused" in relative:
        return [f"relative return -- refused: {relative['refused']}"]
    lines = [
        f"relative return -- {relative['label']}",
        f"  windows: {relative['windows_computed']} computed of {relative['windows_given']} "
        f"given, {len(relative['chains'])} chains, {len(relative['gaps'])} gaps; "
        f"{relative['status']}",
    ]
    lines += [
        f"  chain {chain['entry']}..{chain['exit']} ({chain['windows']} windows): book "
        f"{chain['book']}, investable EW {chain['investable_ew']}, CSI 500 {chain['csi500']}"
        for chain in relative["chains"]
    ]
    lines += [f"  gap: {gap['reason']}" for gap in relative["gaps"]]
    lines += [
        f"  left out: {item['record_id']} ({item['session']}), no {', '.join(item['missing'])}"
        for item in relative.get("windows_left_out", ())
    ]
    return lines
