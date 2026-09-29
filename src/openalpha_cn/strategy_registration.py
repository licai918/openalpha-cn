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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol
from zoneinfo import ZoneInfo

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
from openalpha_cn.strategy_view import (
    REGISTRATION_CUTOFF,
    SignalDay,
    StrategyRequest,
    registration_deadline,
)

__all__ = [
    "COMPOSITE_MODEL_NAME",
    "REGISTRATION_CUTOFF",
    "HeldRecordLookup",
    "RegisteredConfiguration",
    "StrategyRegistrationError",
    "book_period_end",
    "registration_cutoff",
    "session_record",
    "signal_day_batch",
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
    declaration = AlphaModelDeclaration(
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
    artifact = AlphaModelArtifact(
        declaration=declaration,
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
