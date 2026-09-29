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

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Protocol, cast
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.strategy_backtest import StrategyBacktestError, WalkForwardFit
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
from openalpha_cn.panel_ingest import RowDigestCache, stored_rows_digest
from openalpha_cn.strategy_view import (
    REGISTRATION_CUTOFF,
    SignalDay,
    StrategyRequest,
    StrategyViewError,
    input_datasets,
    registration_deadline,
    score_day,
)

__all__ = [
    "COMPOSITE_MODEL_NAME",
    "REGISTRATION_CUTOFF",
    "UNVERIFIABLE",
    "HeldRecordLookup",
    "InputPartition",
    "InputProvenance",
    "RecordCheck",
    "RegisteredConfiguration",
    "Schedule",
    "StrategyRegistrationError",
    "VerdictCache",
    "WitnessedDay",
    "batch_digest",
    "book_period_end",
    "input_provenance",
    "late_record_check",
    "provenance_changes",
    "record_is_bound",
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


UNVERIFIABLE: Final[str] = "unverifiable_inputs_corrected_after_filing"
"""A late, bound record whose scores no longer recompute because an input it read was corrected
after it was filed. Priced -- it is an on-time record in the append-only store, and the book
must stay what was recommended -- and flagged and counted, never silently."""


@dataclass(frozen=True, slots=True, kw_only=True)
class InputPartition:
    """One stored partition a day's scoring could read, as it stood when the record was filed."""

    dataset: str
    year: int
    content_hash: str
    rows_digest: str
    """`panel_ingest.stored_rows_digest` through the record's day."""


@dataclass(frozen=True, slots=True, kw_only=True)
class InputProvenance:
    """What a daily run read before filing a day's record (`V2-P6-011` round 11).

    Written by the daily command before step 7 registers, content-addressed and never
    rewritten. `batch_digest` names the batch it was written for -- the whole batch, its
    `predicted_at` included, so another writer's record of the same model and day is not this
    run's. `inputs` fingerprints every partition `strategy_view.input_datasets` names in the
    years the day's lookback reaches.
    """

    registration_sha256: str
    config_id: str
    session: date
    batch_digest: str
    recorded_at: datetime
    inputs: tuple[InputPartition, ...]

    def document(self) -> dict[str, object]:
        return {
            "schema": INPUT_PROVENANCE_SCHEMA,
            "registration_sha256": self.registration_sha256,
            "config_id": self.config_id,
            "session": self.session.isoformat(),
            "batch_digest": self.batch_digest,
            "recorded_at": self.recorded_at.isoformat(),
            "inputs": [
                [item.dataset, item.year, item.content_hash, item.rows_digest]
                for item in self.inputs
            ],
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.document(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_document(cls, body: Mapping[str, object]) -> InputProvenance:
        if body.get("schema") != INPUT_PROVENANCE_SCHEMA:
            raise StrategyRegistrationError(f"not a {INPUT_PROVENANCE_SCHEMA} document")
        inputs = cast(Sequence[Sequence[object]], body["inputs"])
        return cls(
            registration_sha256=str(body["registration_sha256"]),
            config_id=str(body["config_id"]),
            session=date.fromisoformat(str(body["session"])),
            batch_digest=str(body["batch_digest"]),
            recorded_at=datetime.fromisoformat(str(body["recorded_at"])),
            inputs=tuple(
                InputPartition(
                    dataset=str(item[0]),
                    year=int(cast(int, item[1])),
                    content_hash=str(item[2]),
                    rows_digest=str(item[3]),
                )
                for item in inputs
            ),
        )


INPUT_PROVENANCE_SCHEMA: Final[str] = "daily-selection-input-provenance/v1"

ProvenanceLookup = Callable[[PredictionRecord], "InputProvenance | None"]
"""The provenance a daily run wrote for a record's batch, or `None`."""


def batch_digest(batch: PredictionBatch) -> str:
    """The whole batch, `predicted_at` included: which filing a provenance was written for."""
    return hashlib.sha256(batch.model_dump_json().encode("utf-8")).hexdigest()


def _input_years(request: StrategyRequest, day: date) -> tuple[int, ...]:
    """The years a day's scoring can read: its own, and as far back as its lookback reaches
    (a session is at most ~1.5 calendar days across a year; a month is added for holidays)."""
    source = request.source
    sessions = 1
    if source.trailing_ic is not None:
        sessions = source.trailing_ic.ic_window_sessions + source.trailing_ic.horizon_sessions
    elif source.walk_forward is not None:
        spec = source.walk_forward
        sessions = spec.train_sessions + spec.embargo_sessions + spec.horizon_sessions
    first = day - timedelta(days=int(sessions * 1.5) + 31)
    return tuple(range(first.year, day.year + 1))


def input_provenance(
    store: PanelStore,
    request: StrategyRequest,
    registered: RegisteredConfiguration,
    *,
    day: date,
    batch: PredictionBatch,
    recorded_at: datetime,
) -> InputProvenance:
    """Fingerprint what scoring `day` under `request` could read, for the record of `batch`."""
    inputs = []
    for dataset in input_datasets(request):
        for year in _input_years(request, day):
            coverage = store.read_coverage(dataset, year)
            digest = stored_rows_digest(store, dataset, year=year, through=day)
            if coverage is None or digest is None:
                continue
            inputs.append(
                InputPartition(
                    dataset=dataset,
                    year=year,
                    content_hash=coverage.partition_content_hash or "",
                    rows_digest=digest,
                )
            )
    return InputProvenance(
        registration_sha256=registered.registration_sha256,
        config_id=registered.config_id,
        session=day,
        batch_digest=batch_digest(batch),
        recorded_at=recorded_at,
        inputs=tuple(inputs),
    )


def provenance_changes(
    store: PanelStore,
    provenance: InputProvenance,
    *,
    request: StrategyRequest,
    cache: RowDigestCache | None = None,
) -> tuple[str, ...]:
    """The inputs a record read that the store no longer holds as they were: a partition whose
    rows through the record's day hash otherwise, or one gone, or one now there that was not.

    A partition whose content hash has not moved is not re-read. One that moved only by rows
    after the day -- a daily update's append -- is not a change.
    """
    day = provenance.session
    recorded = {(item.dataset, item.year): item for item in provenance.inputs}
    changed: list[str] = []
    for dataset in input_datasets(request):
        for year in _input_years(request, day):
            item = recorded.get((dataset, year))
            coverage = store.read_coverage(dataset, year)
            if item is not None and coverage is not None:
                if coverage.partition_content_hash == item.content_hash:
                    continue
                now = stored_rows_digest(store, dataset, year=year, through=day, cache=cache)
                if now == item.rows_digest:
                    continue
                changed.append(f"{dataset}:{year}")
            elif item is not None:
                changed.append(f"{dataset}:{year} (no longer stored)")
            elif coverage is not None and stored_rows_digest(
                store, dataset, year=year, through=day, cache=cache
            ):
                changed.append(f"{dataset}:{year} (stored since)")
    return tuple(changed)


def record_is_bound(
    record: PredictionRecord,
    *,
    request: StrategyRequest,
    registered: RegisteredConfiguration,
    provenance: InputProvenance | None,
) -> bool:
    """Whether `record` is this registration's: its declaration is the registered one, and --
    for a walk-forward source, whose declaration is the model's and names no registration -- a
    provenance this registration's daily run wrote names its batch. Another writer's record of
    the same model (`model daily-run`) is therefore not bound (`V2-P6-011` round 11)."""
    if record.batch.artifact.declaration != registered_declaration(request, registered):
        return False
    if request.source.walk_forward is None:
        return True
    return (
        provenance is not None
        and provenance.registration_sha256 == registered.registration_sha256
        and provenance.batch_digest == batch_digest(record.batch)
    )


class VerdictCache(Protocol):
    """Where a record's `verified` verdict is kept, keyed by record id and provenance digest."""

    def verified(self, record_id: str, provenance_digest: str) -> bool: ...
    def keep(self, record_id: str, provenance_digest: str) -> None: ...


class RecordCheck:
    """The check a backtest holds a record registered after its signal instant to
    (`backtest_strategy(verify_late=...)`); calling it returns `None` to admit, or the refusal.

    1. **Bound** (`record_is_bound`), or refused.
    2. **Recomputed at its own filing time**: `request_for(day, registered_at(record))` scored
       through `score_day` from the stored builds (no request) and put by `signal_day_batch`;
       equal in instant, artifact and every score, it is `verified`. Reading the store as the
       record's writer could keep what was stored after it out of the question.
    3. Otherwise, if its provenance shows an input corrected after it was filed, it is
       `UNVERIFIABLE`: admitted -- it is an on-time record in the append-only store -- and listed
       in `unverifiable`, never silently. With no correction to point to, it is refused.

    A `verified` verdict is kept in `verdicts` under the record id and its provenance digest,
    and served from there while no input has changed since; `fit_cache` shares walk-forward
    fits between the days a report re-scores.
    """

    def __init__(
        self,
        store: PanelStore,
        registered: RegisteredConfiguration,
        *,
        request_for: Callable[[date, datetime], StrategyRequest],
        anchor: date,
        provenance_for: ProvenanceLookup | None = None,
        verdicts: VerdictCache | None = None,
        fit_cache: dict[tuple[date, datetime, object], WalkForwardFit] | None = None,
    ) -> None:
        self._store = store
        self._registered = registered
        self._request_for = request_for
        self._anchor = anchor
        self._provenance_for = provenance_for or (lambda _record: None)
        self._verdicts = verdicts
        self._fit_cache = {} if fit_cache is None else fit_cache
        self._digests: RowDigestCache = {}
        self.verified: list[str] = []
        self.unverifiable: list[tuple[str, tuple[str, ...]]] = []

    def __call__(self, record: PredictionRecord) -> str | None:
        batch = record.batch
        day = batch.as_of.astimezone(SHANGHAI).date()
        request = self._request_for(day, registered_at(record))
        provenance = self._provenance_for(record)
        if not record_is_bound(
            record, request=request, registered=self._registered, provenance=provenance
        ):
            return (
                f"{record.record_id} is not declared under the registered configuration "
                f"({batch.artifact.declaration.name}, feature_version "
                f"{batch.artifact.declaration.feature_version}) or no provenance of this "
                "registration names its batch; a record registered after its signal instant is "
                "read only when it is bound to the registration"
            )
        if (
            provenance is not None
            and self._verdicts is not None
            and self._verdicts.verified(record.record_id, provenance.digest)
            and not provenance_changes(
                self._store, provenance, request=request, cache=self._digests
            )
        ):
            self.verified.append(record.record_id)
            return None
        try:
            again = signal_day_batch(
                score_day(
                    self._store,
                    request,
                    day=day,
                    anchor=self._anchor,
                    fit_cache=self._fit_cache,
                ),
                request,
                self._registered,
                predicted_at=batch.predicted_at,
            )
            differs = again is None or (again.as_of, again.artifact, again.predictions) != (
                batch.as_of,
                batch.artifact,
                batch.predictions,
            )
            failure = (
                f"the registered configuration holds on {day.isoformat()}"
                if again is None
                else "they are not the registered configuration's scored again from the stored "
                "builds at its filing time"
            )
        except (StrategyViewError, StrategyBacktestError, StrategyRegistrationError) as error:
            differs, failure = True, f"they cannot be recomputed from the stored builds: {error}"
        if not differs:
            self.verified.append(record.record_id)
            if provenance is not None and self._verdicts is not None:
                self._verdicts.keep(record.record_id, provenance.digest)
            return None
        changes = (
            ()
            if provenance is None
            else provenance_changes(self._store, provenance, request=request, cache=self._digests)
        )
        if changes:
            self.unverifiable.append((record.record_id, changes))
            return None
        return (
            f"{record.record_id}'s scores for {day.isoformat()} were registered after the signal "
            f"instant and {failure}; no input it read has been corrected since, so it could "
            "carry what arrived after the signal instant, and it is refused"
        )


def late_record_check(
    store: PanelStore,
    registered: RegisteredConfiguration,
    *,
    request_for: Callable[[date, datetime], StrategyRequest],
    anchor: date,
    provenance_for: ProvenanceLookup | None = None,
    verdicts: VerdictCache | None = None,
) -> RecordCheck:
    """A `RecordCheck` for `registered`; see it."""
    return RecordCheck(
        store,
        registered,
        request_for=request_for,
        anchor=anchor,
        provenance_for=provenance_for,
        verdicts=verdicts,
    )


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
    request_for: Callable[[date, datetime], StrategyRequest],
    as_of: datetime,
    anchor: date,
    calendar: TradingCalendar,
    through: date,
    journalled_holds: Set[date] = frozenset(),
    provenance_for: ProvenanceLookup | None = None,
    fit_cache: dict[tuple[date, datetime, object], WalkForwardFit] | None = None,
) -> tuple[WitnessedDay, ...]:
    """The command's decisions from its first record through `through`, re-derived from the
    store; the schedule is counted from the configuration's `anchor`.

    **Where it starts is the store's**: the earliest session carrying an on-time record bound to
    the registration (`record_is_bound`). A hold before it leaves no record and cannot be told
    from a day the command never ran, so none is counted. From there, a session with one
    on-time bound record is a ranked day. One without is a held day only when the journal says
    the command completed it holding **and** the registered configuration, scored again at
    `as_of` (no request), ranks nothing there; any other session is not a completed day -- a
    missed or refused run, whose rebalance the next run caught up. `Schedule` over the completed
    days decides as the command does. Two bound records for one session are refused; a record
    filed at or after its registration cutoff is not a registration.
    """
    lookup = provenance_for or (lambda _record: None)
    request = request_for(anchor, as_of)
    every = request.spec.rebalance_every_sessions
    on_time: dict[date, list[str]] = {}
    for record_id in records.list_ids():
        record = records.get(record_id)
        if record is None:
            continue
        if not record_is_bound(
            record, request=request, registered=registered, provenance=lookup(record)
        ):
            continue
        day = record.batch.as_of.astimezone(SHANGHAI).date()
        if not anchor <= day <= through:
            continue
        if registered_at(record) >= registration_cutoff(calendar, day):
            continue
        on_time.setdefault(day, []).append(record_id)
    if not on_time:
        return ()
    cache = {} if fit_cache is None else fit_cache
    days: list[WitnessedDay] = []
    previous: date | None = None
    for session in calendar.trading_days_between(min(on_time), through):
        held = on_time.get(session, [])
        if len(held) > 1:
            raise StrategyRegistrationError(
                f"{session.isoformat()} carries {len(held)} records bound to the registration "
                f"({sorted(held)}); a session has one"
            )
        if held:
            ranked = True
        elif session in journalled_holds:
            try:
                signal = score_day(
                    store, request_for(session, as_of), day=session, anchor=anchor, fit_cache=cache
                )
            except (StrategyViewError, StrategyBacktestError):
                continue
            if signal.scores.ranked is not None:
                continue
            ranked = False
        else:
            continue
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
