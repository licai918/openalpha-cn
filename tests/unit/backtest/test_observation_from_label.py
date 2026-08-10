"""The bridge from a label to an `OutcomeObservation` (`V2-P1-017`).

`OutcomeValidator.validate` computes `observation.end_price / observation.start_price - 1`, and
`OutcomeObservation` takes those two as free positive floats. That expression is **correct** if
and only if both prices are on one adjustment scale, and it is the measured wrong path --
`-0.530973%` where the truth is `+2.742230%` -- if they are two raw closes across an ex-rights
day. Nothing in the observation says which it was given.

`observation_from_label` is the constructor that can answer for its prices: it takes them from
`WindowReturn`'s 后复权 closes, so the validator's own arithmetic reproduces the label exactly.
These tests pin that identity rather than the resemblance -- `==`, not `approx`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.backtest.validation import (
    LABEL_PROVENANCE_NOTE,
    OutcomeObservation,
    observation_from_label,
)
from openalpha_cn.domain.adjustment import FactorObservation, build_adjustment_history
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    LabelError,
    OutcomeLabel,
    build_label_window,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.stock_universe import SecurityLifecycle, StockUniverse
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar

CODE = "000001.SZ"
SHANGHAI = ZoneInfo("Asia/Shanghai")
ENTRY = date(2026, 6, 11)
EXIT = date(2026, 6, 12)
ADJUSTED_RETURN = 0.027422506154573423
UNADJUSTED_RETURN = -0.005309734513274433


def _bar(day: date, close: float, pre_close: float) -> DailyBar:
    return DailyBar(
        ts_code=CODE,
        trade_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1000.0,
        amount=10000.0,
    )


def _label(*, halted: bool = False) -> OutcomeLabel:
    calendar = build_trading_calendar(
        "SZSE",
        [
            CalendarDay(calendar_date=date(2026, 6, day), is_trading=day not in (6, 7, 13, 14))
            for day in range(1, 16)
        ],
    )
    window = build_label_window(
        as_of=datetime(2026, 6, 10, 8, 30, tzinfo=UTC),
        zone=SHANGHAI,
        horizon=parse_horizon("1d"),
        calendar=calendar,
    )
    bars = {ENTRY: _bar(ENTRY, 11.30, 11.32), EXIT: _bar(EXIT, 11.24, 10.94)}
    if halted:
        del bars[EXIT]
    return label_outcome(
        window,
        ts_code=CODE,
        bars=bars,
        factors=build_adjustment_history(
            CODE,
            [
                FactorObservation(ts_code=CODE, observed_on=ENTRY, factor=134.5794),
                FactorObservation(ts_code=CODE, observed_on=EXIT, factor=139.008),
            ],
        ),
        limits={
            day: PriceLimit(ts_code=CODE, trade_date=day, up_limit=1000.0, down_limit=0.01)
            for day in window.sessions
        },
        halts={},
        universe=StockUniverse(
            snapshot_date=date(2026, 6, 30),
            securities=(
                SecurityLifecycle(ts_code=CODE, exchange="SZSE", listed_on=date(1991, 4, 3)),
            ),
        ),
    )


def test_the_validators_own_arithmetic_reproduces_the_label_exactly() -> None:
    """`end_price / start_price - 1` is what `OutcomeValidator` runs. On this observation it is
    the label's number to the last bit, and on the raw closes it would be `-0.53%`.
    """
    observation = observation_from_label(_label(), benchmark_return=0.01, transaction_cost=0.002)

    assert observation.end_price / observation.start_price - 1 == ADJUSTED_RETURN
    assert observation.end_price / observation.start_price - 1 != pytest.approx(UNADJUSTED_RETURN)


def test_the_two_prices_are_the_backward_adjusted_closes_and_not_the_raw_ones() -> None:
    observation = observation_from_label(_label(), benchmark_return=0.0, transaction_cost=0.0)

    assert observation.start_price == 11.30 * 134.5794
    assert observation.end_price == 11.24 * 139.008


def test_the_window_is_dated_at_the_two_session_closes_in_the_exchanges_timezone() -> None:
    observation = observation_from_label(_label(), benchmark_return=0.0, transaction_cost=0.0)

    assert observation.observation_start == datetime(2026, 6, 11, 7, 0, tzinfo=UTC)
    assert observation.observation_end == datetime(2026, 6, 12, 7, 0, tzinfo=UTC)


def test_the_provenance_of_the_two_prices_travels_with_the_observation() -> None:
    """`data_quality_notes` is persisted on `ValidationResult`, so the note is what a stored
    result carries to say which of the three return paths its two prices came from.
    """
    observation = observation_from_label(
        _label(),
        benchmark_return=0.0,
        transaction_cost=0.0,
        data_quality_notes=("Synthetic outcome.",),
    )

    assert observation.data_quality_notes[0].startswith(LABEL_PROVENANCE_NOTE)
    assert "2026-06-11..2026-06-12" in observation.data_quality_notes[0]
    assert "1d" in observation.data_quality_notes[0]
    assert observation.data_quality_notes[1:] == ("Synthetic outcome.",)


def test_a_refused_label_cannot_be_turned_into_an_observation() -> None:
    with pytest.raises(LabelError, match="carries 1 refusal"):
        observation_from_label(_label(halted=True), benchmark_return=0.0, transaction_cost=0.0)


def test_the_free_float_constructor_is_still_there_and_still_says_nothing_about_its_prices() -> (
    None
):
    """The hazard this bridge exists beside, stated as a test rather than as a comment.

    `OutcomeObservation` accepts any two positive floats, so a caller passing raw closes across
    an ex-rights day builds a perfectly valid observation whose realized return has the wrong
    sign. That is why `observation_from_label` exists; it is not why the plain constructor was
    removed, because the replay corpus and every existing caller build one directly.
    """
    raw = OutcomeObservation(
        observation_start=datetime(2026, 6, 11, 7, 0, tzinfo=UTC),
        observation_end=datetime(2026, 6, 12, 7, 0, tzinfo=UTC),
        start_price=11.30,
        end_price=11.24,
        benchmark_return=0.0,
        transaction_cost=0.0,
    )

    assert raw.end_price / raw.start_price - 1 == pytest.approx(UNADJUSTED_RETURN)
    assert raw.data_quality_notes == ()
