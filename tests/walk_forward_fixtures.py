"""A synthetic panel with a **planted** leak, for `V2-P4-013`'s walk-forward tests.

`tests/alpha_model_fixtures.py` builds one label at a time from a target the caller names, which
is exactly right for `V2-P4-011`'s contract tests and exactly wrong here. A split's purge is
about *overlap*, and two windows only genuinely overlap when they are measured over one price
path -- so a corpus whose targets are each stated independently cannot tell a purged split from
an unpurged one no matter how many assertions are written against it. That is the defect this
module exists to avoid: every label here is `label_outcome`'s reading of **one** close series per
security, so two windows that share sessions share the prices those sessions printed, and the
overlap is a fact about the corpus rather than a claim about it.

## The plant

Every session's return is `coefficient(session) * offset(security)`, and the securities' offsets
are strictly increasing alongside their `momentum_20d` feature value. So one number per session
decides which direction the feature was rewarded in, and the corpus has exactly two regimes:

- **aligned** (`ALIGNED_COEFFICIENT`, large) -- a higher feature realized a higher return;
- **opposed** (`OPPOSED_COEFFICIENT`, small and negative) -- a higher feature realized a lower one.

The aligned regime runs from a chosen session to the end of the corpus and always covers the
whole test block, so the *honest* answer is always "this model has no skill and its learned
direction is the opposite of the test period's". Any fold that comes out predicting the test
period's direction did so by reading sessions it should not have.

`ALIGNED_FROM_OVERLAPPING` and `ALIGNED_FROM_ADJACENT` place the regime boundary at the two
sessions that separate the two rules:

| corpus | the leak sits in | purge removes it | embargo removes it |
| --- | --- | --- | --- |
| `ALIGNED_FROM_OVERLAPPING` | sessions the test labels also read | yes | no -- it never sees them |
| `ALIGNED_FROM_ADJACENT` | the sessions just before the block | no -- nothing is shared | yes |

Both session numbers were derived from the schedule's arithmetic before any assertion was
written against them, which is the order that matters: a corpus chosen to fit an assertion is a
corpus that cannot falsify it. `tests/unit/backtest/test_walk_forward_leak.py` re-derives the
learned direction from the fitted artifact rather than from this docstring, and
`test_the_corpus_really_did_reward_a_higher_feature_over_the_test_block` asserts the plant itself
first, so a fixture edit that flattened it fails there instead of turning the two leak tests into
tautologies.

## What is deliberately *not* here

This is not `V2-P4-022`'s corpus. That issue owns a dataset with a **known signal-to-noise ratio**
and a known-null control, which is what an evaluation needs before it may report a number; this
one has no noise model at all and its "signal" is a step function chosen to make one bit of a
reference model flip. It is a leak fixture, not a benchmark, and nothing built on it is a claim
about alpha.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.walk_forward import LabelledCrossSection, LabelledPanel, labelled_panel
from openalpha_cn.domain.adjustment import FactorObservation, build_adjustment_history
from openalpha_cn.domain.alpha_model import (
    AlphaModelDeclaration,
    FeatureCrossSection,
    FeatureRow,
)
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    OutcomeLabel,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.stock_universe import SecurityLifecycle, StockUniverse
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
EXCHANGE: Final[str] = "SZSE"
PREDICTION_TIME: Final[time] = time(9, 0)
"""When each cross section is dated -- the morning of its own prediction day.

Before the 15:00 close on purpose: a fold's first prediction is made *during* its first
prediction day, so a training label that closes on that same day had not closed when the model
was asked. `WalkForwardFold.purged` is where that costs a session, and
`test_the_purge_removes_a_session_the_shared_session_rule_alone_would_have_kept` is where it is
measured.
"""

SECURITIES: Final[tuple[str, ...]] = ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ")
OFFSETS: Final[tuple[float, ...]] = (-1.5, -0.5, 0.5, 1.5)
"""Each security's share of the session coefficient, strictly increasing alongside its feature.

No zero: a security whose return is flat in every regime carries no direction at all, and four
names split two above the feature's mean and two below it, which is the split the reference
model's learned sign is computed over.
"""

MOMENTUM: Final[str] = "momentum_20d"
VALUE: Final[str] = "value_ep"
FEATURE_IDS: Final[tuple[str, ...]] = (MOMENTUM, VALUE)
MOMENTUM_VALUES: Final[tuple[float, ...]] = (0.10, 0.20, 0.30, 0.40)
VALUE_VALUES: Final[tuple[float, ...]] = (0.05, 0.04, 0.03, 0.02)
"""A second column that carries no plant, so the corpus cannot be read one-feature-wide.

`validate_feature_ids` requires a strictly increasing list, and `momentum_20d` sorts before
`value_ep`.
"""

ALIGNED_COEFFICIENT: Final[float] = 0.04
OPPOSED_COEFFICIENT: Final[float] = -0.002
"""Twenty to one, and the ratio is what makes the plant legible rather than marginal.

A five-session window that touches one aligned session already realizes more than five opposed
sessions can offset, so the learned direction flips on *whether* the leaked sessions are in the
training set rather than on how many of them are.
"""

ENTRY_CLOSE: Final[float] = 10.0
ADJUSTMENT_FACTOR: Final[float] = 100.0
"""A constant 后复权 factor, `tests/alpha_model_fixtures.py`'s reason.

`session_returns` cross-checks the published path against the factor path and refuses a session
where the two disagree beyond its calibrated tolerance, so a fixture that moved the factor
without moving `pre_close` to match would fail inside `label_outcome` for a reason that has
nothing to do with a split.
"""

HORIZON: Final[str] = "5d"
PREDICTION_DAY_COUNT: Final[int] = 20
SESSION_COUNT: Final[int] = 32
"""Enough sessions for every window of the last prediction day to close inside the calendar."""

ALIGNED_FROM_OVERLAPPING: Final[int] = 13
"""The aligned regime starts on the first session any fold-0 test label reads.

Fold 0 tests prediction days 12..15, so its labels are measured over sessions 13..21. A
training example whose window reaches session 13 or later therefore realized part of the test
period's return, and that is the overlap purge exists for.
"""

ALIGNED_FROM_ADJACENT: Final[int] = 10
"""The aligned regime starts two sessions before fold 0's first prediction day.

Sessions 10 and 11 are read by no test label -- the earliest of those starts at session 13 --
so no rule built on shared sessions can reach a training example whose window closes there. An
embargo of two sessions can, and that is the whole of the difference between the two rules.
"""

FIRST_TEST_DAY_INDEX: Final[int] = 12
FOLDS: Final[int] = 2
TEST_DAYS_PER_FOLD: Final[int] = 4
EMBARGO_SESSIONS: Final[int] = 2
"""The schedule these fixtures are laid out for: `20 - 2 * 4 = 12` days before fold 0's block."""


def trading_calendar(*, exchange: str = EXCHANGE) -> TradingCalendar:
    """A weekday calendar wide enough that no window falls off either end."""
    start = date(2026, 1, 1)
    return build_trading_calendar(
        exchange,
        [
            CalendarDay(
                calendar_date=start + timedelta(days=offset),
                is_trading=(start + timedelta(days=offset)).weekday() < 5,
            )
            for offset in range(365)
        ],
    )


def sessions() -> tuple[date, ...]:
    """The corpus's session axis: the calendar's first `SESSION_COUNT` open days."""
    return trading_calendar().trading_days[:SESSION_COUNT]


def prediction_days() -> tuple[date, ...]:
    """The sessions this corpus asks a question on, in time order."""
    return sessions()[:PREDICTION_DAY_COUNT]


SESSION_CLOSE: Final[time] = time(15, 0)
"""The other instant a cross section can be dated at, and the one the contract admits equality on.

`V2-P4-011` argued that `as_of == training_cutoff` is legal -- "training through last night's
close and predicting as of it is what a daily production model does" -- so the purge has to
admit it too. A corpus dated at 09:00 cannot tell `>` from `>=` at that boundary, because no
label's 15:00 close ever equals a 09:00 instant;
`test_a_label_that_closed_exactly_when_the_fold_was_asked_survives_the_purge` is the corpus that
can.
"""


def as_of_for(day: date, *, at: time = PREDICTION_TIME) -> datetime:
    """The instant a cross section dated `day` is read at."""
    return datetime.combine(day, at, tzinfo=SHANGHAI)


def close_paths(*, aligned_from: int) -> dict[str, dict[date, float]]:
    """One close series per security, compounded off a single per-session coefficient."""
    axis = sessions()
    paths: dict[str, dict[date, float]] = {}
    for ts_code, offset in zip(SECURITIES, OFFSETS, strict=True):
        series: dict[date, float] = {axis[0]: ENTRY_CLOSE}
        for index in range(1, len(axis)):
            coefficient = ALIGNED_COEFFICIENT if index >= aligned_from else OPPOSED_COEFFICIENT
            series[axis[index]] = series[axis[index - 1]] * (1.0 + coefficient * offset)
        paths[ts_code] = series
    return paths


def _bars(ts_code: str, path: dict[date, float]) -> dict[date, DailyBar]:
    axis = sessions()
    bars: dict[date, DailyBar] = {}
    for index, day in enumerate(axis):
        close = path[day]
        pre_close = path[axis[index - 1]] if index else close
        bars[day] = DailyBar(
            ts_code=ts_code,
            trade_date=day,
            open=close,
            high=max(close, pre_close),
            low=min(close, pre_close),
            close=close,
            pre_close=pre_close,
            pct_chg=(close / pre_close - 1.0) * 100.0,
            vol=1000.0,
            amount=close * 1000.0,
        )
    return bars


def _universe(snapshot_on: date | None = None) -> StockUniverse:
    return StockUniverse(
        snapshot_date=sessions()[-1] if snapshot_on is None else snapshot_on,
        securities=tuple(
            SecurityLifecycle(ts_code=ts_code, exchange=EXCHANGE, listed_on=date(1991, 4, 3))
            for ts_code in SECURITIES
        ),
    )


def labels_for(
    day: date,
    *,
    aligned_from: int,
    horizon: str = HORIZON,
    snapshot_on: date | None = None,
    at: time = PREDICTION_TIME,
    exchange: str = EXCHANGE,
    zone: ZoneInfo = SHANGHAI,
) -> tuple[OutcomeLabel, ...]:
    """Every security's outcome label for one prediction day, read off the shared price path.

    `snapshot_on` moves the registry snapshot behind the window, which is how the real pipeline
    produces `beyond_registry_snapshot` -- the cheapest way to a cross section on which every
    security refuses.
    """
    calendar = trading_calendar(exchange=exchange)
    axis = sessions()
    paths = close_paths(aligned_from=aligned_from)
    window = build_label_window(
        as_of=as_of_for(day, at=at),
        zone=zone,
        horizon=parse_horizon(horizon),
        calendar=calendar,
    )
    halts = halt_corpus_for_years({}, years=(axis[0].year,))
    universe = _universe(snapshot_on)
    return tuple(
        label_outcome(
            window,
            ts_code=ts_code,
            bars=_bars(ts_code, paths[ts_code]),
            factors=build_adjustment_history(
                ts_code,
                [
                    FactorObservation(
                        ts_code=ts_code, observed_on=session, factor=ADJUSTMENT_FACTOR
                    )
                    for session in axis
                ],
            ),
            limits={
                session: PriceLimit(
                    ts_code=ts_code, trade_date=session, up_limit=10_000.0, down_limit=0.01
                )
                for session in axis
            },
            halts=halts,
            universe=universe,
        )
        for ts_code in SECURITIES
    )


def cross_section_for(day: date, *, at: time = PREDICTION_TIME) -> FeatureCrossSection:
    """One prediction day's feature rows -- the plant lives in the labels, not here."""
    return FeatureCrossSection(
        as_of=as_of_for(day, at=at),
        feature_ids=FEATURE_IDS,
        rows=tuple(
            FeatureRow(ts_code=ts_code, values=(momentum, value))
            for ts_code, momentum, value in zip(
                SECURITIES, MOMENTUM_VALUES, VALUE_VALUES, strict=True
            )
        ),
    )


def labelled_sections(
    *,
    aligned_from: int,
    days: Sequence[date] | None = None,
    horizon: str = HORIZON,
    at: time = PREDICTION_TIME,
) -> tuple[LabelledCrossSection, ...]:
    """The corpus as `labelled_panel` takes it: one dated cross section and its labels."""
    return tuple(
        LabelledCrossSection(
            cross_section=cross_section_for(day, at=at),
            labels=labels_for(day, aligned_from=aligned_from, horizon=horizon, at=at),
        )
        for day in (prediction_days() if days is None else days)
    )


def panel(
    *,
    aligned_from: int,
    days: Sequence[date] | None = None,
    horizon: str = HORIZON,
    at: time = PREDICTION_TIME,
) -> LabelledPanel:
    """The whole corpus, joined."""
    return labelled_panel(
        labelled_sections(aligned_from=aligned_from, days=days, horizon=horizon, at=at)
    )


def declaration(*, horizon: str = HORIZON) -> AlphaModelDeclaration:
    """A declaration `backtest/alpha_model.py`'s reference answers to, reading `momentum_20d`."""
    return AlphaModelDeclaration(
        name="walk_forward_reference",
        family="single_feature_reference",
        horizon=horizon,
        feature_version="features/v1",
        seed=7,
        code_commit="0123456789abcdef",
        hyperparameters=(("feature_id", MOMENTUM),),
    )
