"""A registered strategy's daily scores as prediction records (`V2-P6-011`, for `V2-P6-012`).

`strategy_view.score_day` scores one session exactly as a backtest of the registered source
would. This module turns that answer into the `PredictionBatch` the daily command files, finds
the record a session already has, and states the instant after which a session's scores may no
longer be registered. The daily command (`scripts/daily_selection.py`) and the forward report
(`V2-P6-012`) import them from here, so the two read one definition of "today's record".

It reads no panel partition and writes nothing: the store it searches is handed in.

## What a record declares

**Walk-forward source:** the fitted model's own batch -- the one whose scores became the book's
rows -- restamped with the instant it is registered at. Every field of it is the model plane's.

**Static or trailing-IC source:** there is no fitted model, so the batch declares the composite:
`name` is `COMPOSITE_MODEL_NAME`, `family` is `strategy_<kind>` (not a `MODEL_FAMILIES` row --
nothing fits it), `horizon` the rebalance interval the scores are traded over, `feature_version`
the registered `config_id`, `seed` and `code_commit` the registration's, and the hyperparameters
name the combine rule and the registration's digest. The artifact's measured fields are measured
off the day's inputs, not typed: `feature_ids` are the component keys, `parameters` the weights
the composite was taken under, `training_cutoff` the newest instant any score row or counted IC
it read became knowable (at or before the signal instant), and `training_example_count` how many
rows and ICs that was. A reader of such a record must take those two fields in that sense. Every
ranked security carries its composite -- the number the book ordered the market by -- and a
security carrying some component but not all abstains with `ABSTAIN_INCOMPLETE_FEATURES`.

## A record's outcome window and the book period it traded are one session apart

A composite record declares `horizon = Rd`, and its `outcome_known_at` is the label's:
`build_label_window` enters at the close of the session after the signal day and measures `R`
sessions from there, so the window closes `R + 1` sessions after the signal day. The book prices
the same scores from the next session's **open** to the close of the next rebalance -- on the
grid `R` sessions after the signal day -- so the book period ends one session before the record's
outcome window does. Both are true of the record, and they answer different questions: the
record's `outcome_known_at` is when the label it is judged against exists, and it stays the
label's (declaring `R - 1` sessions would move every stored record to a second declaration, and
`0d` is no horizon at all). A reader that needs the book's -- the forward report deciding which
periods are complete -- asks `book_period_end`.

## A record registered after the signal instant is checked, not trusted (`V2-P6-011` round 10)

`batch.as_of` is what the writer **declares** the scores were computed from; nothing in a record
witnesses it. A record registered at or before the signal instant needs no witness -- nothing
later existed yet -- and a backtest reads it on its custody stamp, as it always did. One
registered after it (the daily command files at about 18:30) could carry what arrived between
16:30 and the next morning's call auction. `late_record_check` admits such a record only when
it is **bound** to the registration -- its declaration is the one the registered configuration
declares (`registered_declaration`: for a composite, `feature_version` the `config_id`, the
registration's digest in the hyperparameters, its `code_commit` and seed) -- and its scores
**recompute equal**: the registered configuration scored again for that day through
`strategy_view.score_day` from the stored builds (no request), and put as a batch by the same
`signal_day_batch`. A walk-forward day recomputes the same deterministic refit. Anything else is
refused by name.

## Which days the book rebalanced on is witnessed by the store, not by a journal

The daily command's journal is a local file, and an edit to it could choose trading days among
real, on-time records. `witnessed_days` re-derives the command's decisions from the append-only
prediction store and the stored builds: a completed **ranked** day is a session carrying an
on-time record bound to the registration (the command files one on every ranked day, rebalance
or not); a completed **held** day is a session without one on which the registered configuration,
scored again, ranks nothing -- the configuration's own hold rule, a deterministic function of the
stored builds, so a hold is as witnessed as a rebalance and no marker record (and no contract
change) is needed. Every other session was not completed (missed or refused). `Schedule` over
the completed days gives the decisions, exactly as the command takes them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.strategy_backtest import StrategyBacktestError
from openalpha_cn.domain.alpha_model import (
    ABSTAIN_INCOMPLETE_FEATURES,
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
from openalpha_cn.domain.daily_prices import SESSION_CLOSE_TIME
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.strategy_view import (
    REGISTRATION_CUTOFF,
    SignalDay,
    StrategyRequest,
    StrategyViewError,
    registration_deadline,
    score_day,
)

__all__ = [
    "COMPOSITE_MODEL_NAME",
    "REGISTRATION_CUTOFF",
    "HeldRecordLookup",
    "RegisteredConfiguration",
    "Schedule",
    "StrategyRegistrationError",
    "WitnessedDay",
    "book_period_end",
    "late_record_check",
    "registered_at",
    "registered_declaration",
    "registration_cutoff",
    "schedule_of",
    "session_record",
    "signal_day_batch",
    "witnessed_days",
]

SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)

COMPOSITE_MODEL_NAME: Final[str] = "daily_selection"
"""The declared name of every composite batch the daily command registers."""


class StrategyRegistrationError(RuntimeError):
    """A session's scores cannot be put as a record, or conflict with the one it already has."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisteredConfiguration:
    """What a composite record declares about the registration it was produced under."""

    config_id: str
    registration_sha256: str
    code_commit: str
    seed: int


class HeldRecordLookup(Protocol):
    """What finding a session's record needs of a prediction store: `FilePredictionStore`."""

    def list_ids(self) -> tuple[str, ...]: ...
    def get(self, record_id: str) -> PredictionRecord | None: ...


def signal_day_batch(
    signal: SignalDay,
    request: StrategyRequest,
    registered: RegisteredConfiguration,
    *,
    predicted_at: datetime,
) -> PredictionBatch | None:
    """The day's scores as a `PredictionBatch`, or `None` when the source held (no ranking).

    See the module docstring for what each kind's batch declares. The scores are
    `signal.scores.scores` verbatim for a composite and the fit's batch verbatim for a model.
    """
    scores = signal.scores
    if scores.ranked is None:
        return None
    source = request.source
    if source.walk_forward is not None:
        batch = signal.model_batch
        if batch is None:
            raise StrategyRegistrationError(
                f"the fit in use on {signal.day.isoformat()} ranked the market and scored no batch"
            )
        return PredictionBatch(
            as_of=batch.as_of,
            predicted_at=predicted_at,
            artifact=batch.artifact,
            predictions=batch.predictions,
        )
    if signal.knowable_through is None or signal.values_consumed < 1:
        raise StrategyRegistrationError(
            f"the composite of {signal.day.isoformat()} ranked the market and read no dated input"
        )
    artifact = AlphaModelArtifact(
        declaration=registered_declaration(request, registered),
        feature_ids=tuple(sorted(source.component_keys)),
        training_cutoff=signal.knowable_through,
        training_example_count=signal.values_consumed,
        parameters=tuple(sorted((key, float(value)) for key, value in scores.weights.items())),
    )
    rows = [Prediction(ts_code=name, score=scores.scores[name]) for name in scores.ranked]
    rows += [
        Prediction(ts_code=name, abstention=ABSTAIN_INCOMPLETE_FEATURES)
        for name in scores.incomplete
    ]
    return PredictionBatch(
        as_of=signal.instant,
        predicted_at=predicted_at,
        artifact=artifact,
        predictions=tuple(sorted(rows, key=lambda row: row.ts_code)),
    )


def registered_declaration(
    request: StrategyRequest, registered: RegisteredConfiguration
) -> AlphaModelDeclaration:
    """The declaration every record of the registered configuration carries.

    A walk-forward source's is its model's (every field from the registered configuration); a
    composite's is `COMPOSITE_MODEL_NAME` under the registration: `feature_version` the
    `config_id`, `seed` and `code_commit` the registration's, and its digest in the
    hyperparameters beside the combine rule.
    """
    source = request.source
    if source.walk_forward is not None:
        if request.model is None:
            raise StrategyRegistrationError("a walk-forward request carries no model to declare")
        return request.model.declaration
    return AlphaModelDeclaration(
        name=COMPOSITE_MODEL_NAME,
        family=f"strategy_{source.kind}",
        horizon=f"{request.spec.rebalance_every_sessions}d",
        feature_version=registered.config_id,
        seed=registered.seed,
        code_commit=registered.code_commit,
        hyperparameters=(
            ("combine", source.combine),
            ("registration_sha256", registered.registration_sha256),
        ),
    )


def registered_at(record: PredictionRecord) -> datetime:
    """When a record's numbers were fixed: the later of its batch's and the store's stamps."""
    return max(record.batch.predicted_at, record.recorded_at)


def late_record_check(
    store: PanelStore,
    registered: RegisteredConfiguration,
    *,
    request_for: Callable[[date], StrategyRequest],
    anchor: date,
) -> Callable[[PredictionRecord], str | None]:
    """The check a backtest holds a record registered after its signal instant to; `None` admits.

    `request_for(day)` is the registered configuration's request for one day (the daily command's
    `day_request`) and `anchor` its first day. The record must be bound to the registration
    (`registered_declaration`) and its batch -- instant, artifact and every score -- must equal
    the registered configuration's, scored again for its day from the stored builds (no
    request). See the module docstring.
    """

    def check(record: PredictionRecord) -> str | None:
        batch = record.batch
        day = batch.as_of.astimezone(SHANGHAI).date()
        request = request_for(day)
        if batch.artifact.declaration != registered_declaration(request, registered):
            return (
                f"{record.record_id} is not declared under the registered configuration "
                f"({batch.artifact.declaration.name}, feature_version "
                f"{batch.artifact.declaration.feature_version}); a record registered after its "
                "signal instant is read only when it is bound to the registration"
            )
        try:
            again = signal_day_batch(
                score_day(store, request, day=day, anchor=anchor),
                request,
                registered,
                predicted_at=batch.predicted_at,
            )
        except (StrategyViewError, StrategyBacktestError, StrategyRegistrationError) as error:
            return (
                f"{record.record_id}'s scores for {day.isoformat()} cannot be recomputed from the "
                f"stored builds, so a record registered after its signal instant is refused: "
                f"{error}"
            )
        if again is None:
            return (
                f"the registered configuration holds on {day.isoformat()}, so {record.record_id} "
                "cannot be its scores"
            )
        if (again.as_of, again.artifact, again.predictions) != (
            batch.as_of,
            batch.artifact,
            batch.predictions,
        ):
            return (
                f"{record.record_id}'s scores for {day.isoformat()} are not the registered "
                "configuration's scored again from the stored builds; registered after the "
                "signal instant, it could carry what arrived after it, and it is refused"
            )
        return None

    return check


@dataclass(frozen=True, slots=True, kw_only=True)
class Schedule:
    """Where the session sits on the configuration's rebalance schedule, and what the book held.

    `position` counts sessions from the configuration's first; `scheduled` is the newest
    scheduled rebalance on or before the session; `previous` the newest completed day before it.
    A rebalance is due when there is no book yet (the first day), or when a scheduled rebalance
    has come since the book was set -- today's, or one a missed or refused day never made, which
    is then made today rather than left for the next one.
    """

    session: date
    position: int
    scheduled: date
    previous: date | None

    @property
    def due(self) -> bool:
        return self.previous is None or self.scheduled > self.previous

    @property
    def reason(self) -> str:
        if self.previous is None:
            return "the first day: no book yet"
        if not self.due:
            return f"the book set on {self.previous.isoformat()} holds until the next rebalance"
        if self.scheduled == self.session:
            return "scheduled"
        return f"catching up the rebalance scheduled on {self.scheduled.isoformat()}"


def schedule_of(
    calendar: TradingCalendar,
    *,
    anchor: date,
    session: date,
    every: int,
    previous: date | None,
) -> Schedule:
    """The session's place on the rebalance schedule counted from `anchor`."""
    days = calendar.trading_days_between(anchor, session)
    if not days or days[-1] != session:
        raise StrategyRegistrationError(
            f"{session.isoformat()} is not a session on or after {anchor}"
        )
    position = len(days) - 1
    return Schedule(
        session=session,
        position=position,
        scheduled=days[position - position % every],
        previous=previous,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class WitnessedDay:
    """One completed day as the store witnesses it: its decision, and its record (none on a
    held day)."""

    session: date
    decision: str
    record_id: str | None


def witnessed_days(
    store: PanelStore,
    records: HeldRecordLookup,
    registered: RegisteredConfiguration,
    *,
    request_for: Callable[[date], StrategyRequest],
    anchor: date,
    calendar: TradingCalendar,
    start: date,
    through: date,
) -> tuple[WitnessedDay, ...]:
    """The command's decisions from `start` -- the first day it ran -- through `through`,
    re-derived from the store; the schedule is counted from the configuration's `anchor`.

    A session with one on-time record bound to the registration is a ranked day; one without is
    re-scored (`score_day`, no request), and is a held day when the configuration ranks nothing
    there, and not a completed day otherwise -- ranked with no record is a missed or refused run,
    and a session the stored builds cannot score was not run. `Schedule` over the completed days
    decides `rebalanced`, `not a rebalance day` or `held`, as the command does. Two bound records
    for one session are refused; a record filed at or after its registration cutoff is not a
    registration.
    """
    if start < anchor:
        raise StrategyRegistrationError(
            f"the command cannot have run on {start.isoformat()}, before the configuration's "
            f"first day {anchor.isoformat()}"
        )
    sessions = calendar.trading_days_between(start, through)
    declaration = registered_declaration(request_for(anchor), registered)
    every = request_for(anchor).spec.rebalance_every_sessions
    on_time: dict[date, list[str]] = {}
    admitted = frozenset(sessions)
    for record_id in records.list_ids():
        record = records.get(record_id)
        if record is None or record.batch.artifact.declaration != declaration:
            continue
        day = record.batch.as_of.astimezone(SHANGHAI).date()
        if day not in admitted or registered_at(record) >= registration_cutoff(calendar, day):
            continue
        on_time.setdefault(day, []).append(record_id)
    days: list[WitnessedDay] = []
    previous: date | None = None
    for session in sessions:
        held = on_time.get(session, [])
        if len(held) > 1:
            raise StrategyRegistrationError(
                f"{session.isoformat()} carries {len(held)} records bound to the registration "
                f"({sorted(held)}); a session has one"
            )
        if held:
            ranked = True
        else:
            try:
                signal = score_day(store, request_for(session), day=session, anchor=anchor)
            except (StrategyViewError, StrategyBacktestError):
                continue
            if signal.scores.ranked is not None:
                continue
            ranked = False
        schedule = schedule_of(
            calendar, anchor=anchor, session=session, every=every, previous=previous
        )
        decision = "held" if not ranked else "rebalanced" if schedule.due else "not a rebalance day"
        days.append(
            WitnessedDay(session=session, decision=decision, record_id=held[0] if held else None)
        )
        previous = session
    return tuple(days)


def session_record(store: HeldRecordLookup, batch: PredictionBatch) -> PredictionRecord | None:
    """The record `store` already holds for `batch`'s session under `batch`'s declaration.

    `None` when there is none. Refuses (`StrategyRegistrationError`) when there is one whose
    artifact or scores differ: a second answer to one session would be a revision, which the
    prediction store exists to make impossible, so the registered record stands. A record is
    "the session's" when it stands at the same instant under the same declaration; its
    `predicted_at` and custody stamp are not compared -- they are when, not what.
    """
    for record_id in store.list_ids():
        held = store.get(record_id)
        if held is None or held.batch.as_of != batch.as_of:
            continue
        if held.batch.artifact.declaration != batch.artifact.declaration:
            continue
        if (held.batch.artifact, held.batch.predictions) != (batch.artifact, batch.predictions):
            raise StrategyRegistrationError(
                f"{record_id} already registers {batch.as_of.isoformat()} under this "
                "declaration with other scores; the registered record stands and nothing was "
                "filed. The panel or a factor build changed after it was registered"
            )
        return held
    return None


def registration_cutoff(calendar: TradingCalendar, session: date) -> datetime:
    """The instant after which `session`'s scores may no longer be registered: 09:15 Shanghai on
    the next open session, when the call auction that fixes the book's execution price begins
    (`strategy_view.REGISTRATION_CUTOFF`; a backtest reading the record holds it to the same
    instant)."""
    return registration_deadline(calendar.next_trading_day(session))


def book_period_end(
    record: PredictionRecord,
    *,
    calendar: TradingCalendar,
    rebalance_every_sessions: int | None = None,
    next_rebalance: date | None = None,
) -> datetime:
    """The close that ends the book period `record`'s scores were traded over (`V2-P6-012`).

    The book trades a signal day's scores from the next session's open to the close of the next
    rebalance: `next_rebalance` when the caller has it -- a forward book on the days its daily
    command rebalanced -- or, on the fixed grid, the session `rebalance_every_sessions` after the
    record's day. Exactly one of the two. The record's own `outcome_known_at` is the label
    window's close, one session later; see the module docstring for why both stand.
    """
    if (rebalance_every_sessions is None) == (next_rebalance is None):
        raise StrategyRegistrationError(
            "a book period ends at the next rebalance: name it, or the grid's interval -- one"
        )
    day = record.batch.as_of.astimezone(SHANGHAI).date()
    if next_rebalance is not None:
        if next_rebalance <= day:
            raise StrategyRegistrationError(
                f"the next rebalance {next_rebalance.isoformat()} is not after the record's day "
                f"{day.isoformat()}"
            )
        end = next_rebalance
    else:
        assert rebalance_every_sessions is not None
        if rebalance_every_sessions < 1:
            raise StrategyRegistrationError("a rebalance interval is at least one session")
        end = calendar.shift(day, rebalance_every_sessions)
    return datetime.combine(end, SESSION_CLOSE_TIME, SHANGHAI)
