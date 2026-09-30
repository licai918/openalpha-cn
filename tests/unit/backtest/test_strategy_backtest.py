"""The multi-year net-of-cost strategy backtest, driven against a ledger computed by hand
(`V2-P6-007`).

Every number asserted in `test_a_two_period_backtest_matches_the_hand_computed_ledger` is derived
in that test's docstring from the fixture below -- price times shares, commission with its 5-yuan
floor, stamp duty on the sell, slippage on both sides, the cash that is left, and the value each
holding is marked at. Nothing is computed by calling the module under test and writing down what
it said.

The fixture: four main-board securities over six sessions, two held, a signal every three
sessions, `100,000` yuan of capital per position, a 1% participation cap, and the research
protocol's measurement costs (commission 2.5bp both sides with a 5-yuan minimum, stamp duty 0.5
per mille on sells, no transfer fee, slippage 10bp per side).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

import pytest
from alpha_model_fixtures import training_example

from openalpha_cn.backtest.alpha_baseline import BASELINE_FAMILY, CrossSectionalRankModel
from openalpha_cn.backtest.execution import CostSchedule, MarketBar
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A,
    EQUAL_WEIGHT_ALL_A_HELD,
    KNOWN_STRATEGY_BACKTEST_LIMITATIONS,
    MODEL_COMPONENT,
    STRATEGY_BACKTEST_LIMITATION_CODES,
    HeldBenchmarkKey,
    HeldBenchmarkPeriod,
    ICObservation,
    PeriodResult,
    ScoreRow,
    ScoreSource,
    SessionQuote,
    StrategyBacktestError,
    StrategyInputs,
    StrategySpec,
    TrailingICWeights,
    WalkForwardFit,
    WalkForwardModel,
    held_equal_weight_period,
    limitation_codes_for,
    run_strategy_backtest,
    trailing_ic_weights,
    usable_fit,
    walk_forward_fits,
)
from openalpha_cn.domain.alpha_model import AlphaModelDeclaration, TrainingExample
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)

SHANGHAI: Final[timezone] = timezone(timedelta(hours=8))
SESSIONS: Final[tuple[date, ...]] = (
    date(2026, 3, 2),
    date(2026, 3, 3),
    date(2026, 3, 4),
    date(2026, 3, 5),
    date(2026, 3, 6),
    date(2026, 3, 9),
)
D1, D2, D3, D4, D5, D6 = SESSIONS
A, B, C, D = "600001.SH", "600002.SH", "600003.SH", "600004.SH"
CSI500: Final[str] = "000905.SH"
FACTOR: Final[str] = "reversal_1d/v1"

MEASUREMENT_COSTS: Final[CostSchedule] = CostSchedule(
    commission_rate=Decimal("0.00025"),
    minimum_commission=Decimal("5.00"),
    transfer_fee_rate=Decimal("0"),
    sell_stamp_duty_rate=Decimal("0.0005"),
)

HAND_FIXTURE_SPEC: Final[StrategySpec] = StrategySpec(
    rebalance_every_sessions=3,
    holding_count=2,
    buffer_rank=None,
    max_industry_weight=None,
    position_capital=Decimal("100000"),
    participation_cap=Decimal("0.01"),
    costs=MEASUREMENT_COSTS,
    slippage_rate=Decimal("0.001"),
    benchmarks=(CSI500, EQUAL_WEIGHT_ALL_A),
)

RAW_SOURCE: Final[ScoreSource] = ScoreSource(
    components=((FACTOR, "raw", Decimal("1")),), combine="zscore_sum"
)

# (open, close) per session; the previous close is the prior session's close.
PRICES: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    A: (
        ("9.90", "10.00"),
        ("10.00", "10.20"),
        ("10.20", "10.50"),
        ("10.50", "10.80"),
        ("11.00", "11.10"),
        ("11.10", "11.20"),
    ),
    B: (
        ("19.80", "19.80"),
        ("20.00", "20.10"),
        ("20.10", "20.30"),
        ("20.30", "20.50"),
        ("20.60", "20.70"),
        ("20.70", "21.00"),
    ),
    C: tuple(("30.00", "30.00") for _ in SESSIONS),
    D: (
        ("5.00", "5.00"),
        ("5.00", "5.00"),
        ("5.00", "4.90"),
        ("4.90", "4.95"),
        ("5.00", "5.10"),
        ("5.10", "5.20"),
    ),
}
FIRST_PREVIOUS_CLOSE: Final[dict[str, str]] = {A: "9.90", B: "19.80", C: "30.00", D: "5.00"}
TURNOVER_YUAN: Final[dict[str, Decimal]] = {
    A: Decimal("20000000"),
    B: Decimal("20000000"),
    C: Decimal("20000000"),
    D: Decimal("1500000"),
}
SCORES: Final[dict[date, dict[str, float]]] = {
    D1: {A: 4.0, B: 3.0, C: 2.0, D: 1.0},
    D4: {A: 1.0, B: 4.0, C: 3.0, D: 5.0},
}
BENCHMARK_RETURNS: Final[dict[str, dict[date, Decimal]]] = {
    CSI500: {
        D2: Decimal("0.01"),
        D3: Decimal("0"),
        D4: Decimal("-0.01"),
        D5: Decimal("0.02"),
        D6: Decimal("0"),
    },
    EQUAL_WEIGHT_ALL_A: {
        D2: Decimal("0.005"),
        D3: Decimal("0.005"),
        D4: Decimal("0"),
        D5: Decimal("0"),
        D6: Decimal("0.01"),
    },
}


def signal_instant(day: date) -> datetime:
    """16:30 Asia/Shanghai on `day`, the conservative after-close instant the plan builds at."""
    return datetime(day.year, day.month, day.day, 16, 30, tzinfo=SHANGHAI)


def _band(previous_close: Decimal) -> tuple[Decimal, Decimal]:
    cent = Decimal("0.01")
    return (
        (previous_close * Decimal("1.1")).quantize(cent, rounding=ROUND_HALF_UP),
        (previous_close * Decimal("0.9")).quantize(cent, rounding=ROUND_HALF_UP),
    )


def _bar(
    subject: str,
    day: date,
    *,
    previous_close: Decimal,
    open_: Decimal,
    close: Decimal,
    suspended: bool = False,
) -> MarketBar:
    up, down = _band(previous_close)
    return MarketBar(
        subject=subject,
        trade_date=day,
        board="star" if subject.startswith("688") else "main",
        previous_close=previous_close,
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        suspended=suspended,
        is_st=False,
        up_limit=up,
        down_limit=down,
    )


def build_quotes(
    prices: Mapping[str, Sequence[tuple[str, str]]] = PRICES,
    *,
    suspended: frozenset[tuple[str, date]] = frozenset(),
    first_previous_close: Mapping[str, str] = FIRST_PREVIOUS_CLOSE,
    turnover: Mapping[str, Decimal] = TURNOVER_YUAN,
    turnover_on: Mapping[tuple[str, date], Decimal] | None = None,
) -> dict[date, dict[str, SessionQuote]]:
    quotes: dict[date, dict[str, SessionQuote]] = {day: {} for day in SESSIONS}
    for subject, series in prices.items():
        previous = Decimal(first_previous_close[subject])
        for day, (open_text, close_text) in zip(SESSIONS, series, strict=True):
            close = Decimal(close_text)
            quotes[day][subject] = SessionQuote(
                bar=_bar(
                    subject,
                    day,
                    previous_close=previous,
                    open_=Decimal(open_text),
                    close=close,
                    suspended=(subject, day) in suspended,
                ),
                turnover_yuan=(turnover_on or {}).get((subject, day), turnover[subject]),
                adj_factor=Decimal("1"),
            )
            previous = close
    return quotes


def score_rows(
    scores: Mapping[date, Mapping[str, float]] = SCORES,
    *,
    component: str = f"{FACTOR}@raw",
    available: Mapping[date, datetime] | None = None,
    revised: Mapping[date, datetime] | None = None,
) -> tuple[ScoreRow, ...]:
    return tuple(
        ScoreRow(
            component=component,
            subject=subject,
            signal_day=day,
            value=value,
            available_time=(available or {}).get(day, signal_instant(day)),
            revision_time=(revised or {}).get(day, signal_instant(day)),
        )
        for day, cross_section in scores.items()
        for subject, value in cross_section.items()
    )


def build_inputs(
    *,
    source: ScoreSource = RAW_SOURCE,
    scores: tuple[ScoreRow, ...] | None = None,
    quotes: Mapping[date, Mapping[str, SessionQuote]] | None = None,
    industries: Mapping[date, Mapping[str, str]] | None = None,
) -> StrategyInputs:
    return StrategyInputs(
        source=source,
        sessions=SESSIONS,
        signal_instants={day: signal_instant(day) for day in SESSIONS},
        scores=score_rows() if scores is None else scores,
        quotes=build_quotes() if quotes is None else quotes,
        benchmark_returns=BENCHMARK_RETURNS,
        industries=industries or {},
    )


HAND_FIXTURE_INPUTS: Final[StrategyInputs] = build_inputs()


def _fills(period: PeriodResult) -> list[tuple[date, str, str, int, Decimal, Decimal]]:
    return [
        (fill.day, fill.subject, fill.side, fill.quantity, fill.price, fill.fees)
        for fill in period.fills
    ]


def test_a_two_period_backtest_matches_the_hand_computed_ledger() -> None:
    """Every number below, by hand.

    Signals at the close of D1 (index 0) and D4 (index 3); trades at the next session's OPEN;
    period 1 is D1 close -> D4 close, period 2 is D4 close -> D6 close (the last period is
    shorter than the interval because the range ends). Initial capital = 2 x 100,000 = 200,000.

    PERIOD 1. D1 ranks A(4) B(3) C(2) D(1): buy A and B at D2's open.
      A @ 10.00. Participation cap 1% x 20,000,000 = 200,000, so the budget is 100,000.
        10,000 shares: 100,000.00 + commission 25.00 + slippage 100.00 = 100,125.00 > 100,000
        9,900 shares: notional 99,000.00; commission 99,000 x 0.00025 = 24.75 (> 5 floor);
        slippage 99,000 x 0.001 = 99.00; fees 123.75; outlay 99,123.75. Cash 100,876.25.
      B @ 20.00. 5,000 shares -> 100,125.00 > 100,000, so 4,900 shares: notional 98,000.00;
        commission 24.50; slippage 98.00; fees 122.50; outlay 98,122.50. Cash 2,753.75.
      D4 close: A 9,900 x 10.80 = 106,920.00; B 4,900 x 20.50 = 100,450.00; + cash 2,753.75
        = 210,123.75.
      net  = (210,123.75 - 200,000) / 200,000 = 10,123.75 / 200,000 = 0.05061875
      cost = (123.75 + 122.50) / 200,000 = 246.25 / 200,000 = 0.00123125
      gross = net + cost = 0.05185
      turnover = (99,000 + 98,000) / (2 x 200,000) = 0.4925
      000905.SH: 1.01 x 1.00 x 0.99 - 1 = -0.0001; all-A equal weight: 1.005 x 1.005 x 1 - 1
        = 0.010025

    PERIOD 2. D4 ranks D(5) B(4) C(3) A(1): keep B (rank 2), sell A, buy D, at D5's open.
      Sell A 9,900 @ 11.00: notional 108,900.00; commission 108,900 x 0.00025 = 27.225 -> 27.23
        (half-up to the cent); stamp duty 108,900 x 0.0005 = 54.45; slippage 108.90;
        fees 190.58. Cash 2,753.75 + 108,900.00 - 190.58 = 111,463.17.
      Buy D @ 5.00: the cap is 1% x 1,500,000 = 15,000 < 100,000, so the budget is 15,000:
        3,000 shares, notional 15,000.00; commission 3.75 -> the 5.00 MINIMUM; slippage 15.00;
        fees 20.00; outlay 15,020.00. Cash 96,443.17. One order was capped by participation.
      D6 close: B 4,900 x 21.00 = 102,900.00; D 3,000 x 5.20 = 15,600.00; + 96,443.17
        = 214,943.17.
      net  = (214,943.17 - 210,123.75) / 210,123.75 = 4,819.42 / 210,123.75
           = 0.022936103129... -> 0.0229361031 (ten places)
      cost = (190.58 + 20.00) / 210,123.75 = 210.58 / 210,123.75 = 0.0010021713 (ten places)
      gross = 0.0229361031 + 0.0010021713 = 0.0239382744
      turnover = (108,900 + 15,000) / (2 x 210,123.75) = 0.2948262631 (ten places)
      000905.SH: 1.02 x 1.00 - 1 = 0.02; all-A equal weight: 1.00 x 1.01 - 1 = 0.01
    """
    result = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC)

    assert [p.net_return for p in result.periods] == [
        Decimal("0.0506187500"),
        Decimal("0.0229361031"),
    ]
    first, second = result.periods
    assert (first.start, first.end, first.sessions) == (D1, D4, 3)
    assert (second.start, second.end, second.sessions) == (D4, D6, 2)
    assert (first.start_value, first.end_value) == (Decimal("200000.00"), Decimal("210123.75"))
    assert (second.start_value, second.end_value) == (Decimal("210123.75"), Decimal("214943.17"))
    assert (first.cost_yuan, second.cost_yuan) == (Decimal("246.25"), Decimal("210.58"))
    assert (first.cost, second.cost) == (Decimal("0.0012312500"), Decimal("0.0010021713"))
    assert (first.gross_return, second.gross_return) == (
        Decimal("0.0518500000"),
        Decimal("0.0239382744"),
    )
    assert (first.turnover, second.turnover) == (Decimal("0.4925000000"), Decimal("0.2948262631"))
    assert dict(first.benchmark_returns) == {
        CSI500: Decimal("-0.0001000000"),
        EQUAL_WEIGHT_ALL_A: Decimal("0.0100250000"),
    }
    assert dict(second.benchmark_returns) == {
        CSI500: Decimal("0.0200000000"),
        EQUAL_WEIGHT_ALL_A: Decimal("0.0100000000"),
    }
    assert _fills(first) == [
        (D2, A, "buy", 9_900, Decimal("10.00"), Decimal("123.75")),
        (D2, B, "buy", 4_900, Decimal("20.00"), Decimal("122.50")),
    ]
    assert _fills(second) == [
        (D5, A, "sell", 9_900, Decimal("11.00"), Decimal("190.58")),
        (D5, D, "buy", 3_000, Decimal("5.00"), Decimal("20.00")),
    ]
    assert (first.rejected_orders, second.rejected_orders) == (0, 0)
    assert (first.capped_orders, second.capped_orders) == (0, 1)
    assert first.holdings == (A, B)
    assert second.holdings == (B, D)
    assert result.spec == HAND_FIXTURE_SPEC
    assert result.source == RAW_SOURCE


def test_the_commission_floor_is_what_the_small_capped_buy_paid() -> None:
    """D's 15,000-yuan buy pays 5.00 of commission where the rate alone says 3.75.

    Separated from the ledger so a mutation of the floor names what it broke: without the floor
    the buy's fees would be 3.75 + 15.00 = 18.75 rather than 20.00.
    """
    second = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC).periods[1]
    buy = next(fill for fill in second.fills if fill.subject == D)

    assert buy.notional == Decimal("15000.00")
    assert buy.commission == Decimal("5.00")
    assert buy.fees == Decimal("20.00")


def test_a_limit_up_name_is_not_bought_and_is_counted_as_rejected() -> None:
    """A opens D2 at its limit-up price, 10.00 x 1.1 = 11.00, and the buy is refused.

    The open is judged as a one-price bar at the open, so `low >= up_limit` holds and
    `AShareExecutionPolicy` refuses the buy. B still fills exactly as in the ledger, the slot
    A would have taken stays in cash (no substitution by C), and period 1 counts one rejection.
    Cash at D4 = 200,000 - 98,122.50 = 101,877.50; value = 100,450.00 + 101,877.50 = 202,327.50.
    """
    prices = dict(PRICES)
    prices[A] = (("9.90", "10.00"), ("11.00", "11.00"), *PRICES[A][2:])
    result = run_strategy_backtest(build_inputs(quotes=build_quotes(prices)), HAND_FIXTURE_SPEC)
    first = result.periods[0]

    assert first.rejected_orders == 1
    assert [(r.day, r.subject, r.side) for r in first.rejections] == [(D2, A, "buy")]
    assert "limit-up" in first.rejections[0].reason
    assert [fill.subject for fill in first.fills] == [B]
    assert first.holdings == (B,)
    assert first.end_value == Decimal("202327.50")


def test_a_name_that_opens_locked_and_trades_off_the_limit_later_is_still_refused_at_the_open() -> (
    None
):
    """A opens D2 at 11.00 (its limit-up) and closes at 10.80, so the session is NOT one-price.

    Judged on the whole day (`low` 10.80 < `up_limit` 11.00) the policy would fill it, and at
    the close. Judged at the open print -- `fills_are_at_the_open_print_judged_as_a_one_price_bar`
    -- an order placed at the open meets a locked limit and is refused.
    """
    prices = dict(PRICES)
    prices[A] = (("9.90", "10.00"), ("11.00", "10.80"), *PRICES[A][2:])
    first = run_strategy_backtest(
        build_inputs(quotes=build_quotes(prices)), HAND_FIXTURE_SPEC
    ).periods[0]

    assert [(r.day, r.subject, r.side) for r in first.rejections] == [(D2, A, "buy")]
    assert [fill.subject for fill in first.fills] == [B]


def test_a_limit_down_holding_is_not_sold_and_is_carried() -> None:
    """A opens D5 at its limit-down price, 10.80 x 0.9 = 9.72: the sell is refused and A is kept.

    With A carried, the book is still full (A and B), so D is not bought: one rejection, no fill.
    """
    prices = dict(PRICES)
    prices[A] = (*PRICES[A][:4], ("9.72", "9.72"), ("9.72", "9.72"))
    second = run_strategy_backtest(
        build_inputs(quotes=build_quotes(prices)), HAND_FIXTURE_SPEC
    ).periods[1]

    assert [(r.day, r.subject, r.side) for r in second.rejections] == [(D5, A, "sell")]
    assert "limit-down" in second.rejections[0].reason
    assert second.fills == ()
    assert second.holdings == (A, B)


def test_a_suspended_holding_is_carried_not_sold() -> None:
    """A is halted on D5, the session its sale would execute: the sale is refused and A stays.

    The book is full, so D is not bought either. A is marked at its own D6 close, 11.20:
    9,900 x 11.20 + 4,900 x 21.00 + 2,753.75 = 110,880.00 + 102,900.00 + 2,753.75 = 216,533.75.
    """
    quotes = build_quotes(suspended=frozenset({(A, D5)}))
    second = run_strategy_backtest(build_inputs(quotes=quotes), HAND_FIXTURE_SPEC).periods[1]

    assert [(r.day, r.subject, r.side) for r in second.rejections] == [(D5, A, "sell")]
    assert "suspended" in second.rejections[0].reason
    assert second.holdings == (A, B)
    assert second.end_value == Decimal("216533.75")


def test_a_holding_with_no_bar_is_marked_at_its_last_close_and_cannot_be_sold() -> None:
    """A has no bar at all on D5 and D6 (a whole-day halt the price table does not carry).

    The sale has nothing to execute against and is refused; A is marked at its D4 close:
    9,900 x 10.80 + 102,900.00 + 2,753.75 = 106,920.00 + 105,653.75 = 212,573.75.
    """
    quotes = build_quotes()
    del quotes[D5][A]
    del quotes[D6][A]
    second = run_strategy_backtest(build_inputs(quotes=quotes), HAND_FIXTURE_SPEC).periods[1]

    assert [(r.day, r.subject, r.side) for r in second.rejections] == [(D5, A, "sell")]
    assert second.holdings == (A, B)
    assert second.end_value == Decimal("212573.75")


def test_buffer_rank_keeps_a_holding_that_slipped_within_the_band() -> None:
    """D4 ranks D(5) B(4) A(3) C(1): A has slipped to rank 3.

    Without a buffer A is sold and D bought. With `buffer_rank=3` A is inside the band and kept,
    B is kept, the book is full and period 2 trades nothing at all: turnover 0, no cost.
    """
    scores = {D1: SCORES[D1], D4: {A: 3.0, B: 4.0, C: 1.0, D: 5.0}}
    inputs = build_inputs(scores=score_rows(scores))

    unbuffered = run_strategy_backtest(inputs, HAND_FIXTURE_SPEC).periods[1]
    buffered = run_strategy_backtest(
        inputs, HAND_FIXTURE_SPEC.model_copy(update={"buffer_rank": 3})
    ).periods[1]

    assert [(f.subject, f.side) for f in unbuffered.fills] == [(A, "sell"), (D, "buy")]
    assert unbuffered.holdings == (B, D)
    assert buffered.fills == ()
    assert buffered.holdings == (A, B)
    assert (buffered.turnover, buffered.cost_yuan) == (Decimal("0E-10"), Decimal("0.00"))


def test_signals_are_read_at_t_and_traded_at_t_plus_one_open_never_at_t() -> None:
    """The D1 signal fills at D2's OPEN, and the D2 close never enters the fill.

    A's D1 close is 10.00 and its D2 open is moved to 10.50: 100,000 buys 9,500 shares at 10.50
    (9,500 x 10.50 = 99,750.00; commission 24.94 (24.9375 half-up); slippage 99.75; outlay
    99,874.69 <= 100,000). Priced at the D1 close it would have been 9,900 shares. A score row
    dated D2 -- a session that is not a signal day -- is ignored rather than traded on.
    """
    prices = dict(PRICES)
    prices[A] = (("9.90", "10.00"), ("10.50", "10.50"), *PRICES[A][2:])
    rows = (*score_rows(), *score_rows({D2: {A: -9.0, B: -9.0, C: 9.0, D: 9.0}}))
    first = run_strategy_backtest(
        build_inputs(scores=rows, quotes=build_quotes(prices)), HAND_FIXTURE_SPEC
    ).periods[0]

    buy_a = first.fills[0]
    assert (buy_a.day, buy_a.subject, buy_a.quantity, buy_a.price) == (
        D2,
        A,
        9_500,
        Decimal("10.50"),
    )
    assert buy_a.fees == Decimal("124.69")
    assert all(fill.day == D2 for fill in first.fills)
    assert first.holdings == (A, B)


def test_a_score_row_not_visible_at_its_as_of_is_refused() -> None:
    """A D1 row that became available one minute after D1 16:30 is look-ahead and is refused.

    The same holds for a row whose revision was stamped after the signal instant, which is the
    shape a restated cross section takes.
    """
    late = {D1: signal_instant(D1) + timedelta(minutes=1)}

    with pytest.raises(StrategyBacktestError, match="not visible"):
        run_strategy_backtest(build_inputs(scores=score_rows(available=late)), HAND_FIXTURE_SPEC)
    with pytest.raises(StrategyBacktestError, match="not visible"):
        run_strategy_backtest(build_inputs(scores=score_rows(revised=late)), HAND_FIXTURE_SPEC)


def test_a_score_row_visible_exactly_at_the_signal_instant_is_admitted() -> None:
    """The boundary is inclusive: a row available at 16:30:00 exactly is the ordinary case."""
    exact = {D1: signal_instant(D1).astimezone(UTC)}
    result = run_strategy_backtest(
        build_inputs(scores=score_rows(available=exact, revised=exact)), HAND_FIXTURE_SPEC
    )

    assert result.periods[0].net_return == Decimal("0.0506187500")


def test_a_signal_day_with_no_cross_section_is_refused_rather_than_skipped() -> None:
    """D4 carries no score row: the rebalance has nothing to rank and the run is refused."""
    with pytest.raises(StrategyBacktestError, match=r"no .* cross section"):
        run_strategy_backtest(build_inputs(scores=score_rows({D1: SCORES[D1]})), HAND_FIXTURE_SPEC)


def test_neutralized_tier_scores_are_accepted() -> None:
    """The tier `model evaluate` refuses is an ordinary component here.

    The same values filed as a neutralized component produce the same ledger as the raw one.
    """
    source = ScoreSource(components=((FACTOR, "neutralized", Decimal("1")),), combine="zscore_sum")
    rows = score_rows(component=f"{FACTOR}@neutralized")
    result = run_strategy_backtest(build_inputs(source=source, scores=rows), HAND_FIXTURE_SPEC)

    assert result.source.components[0][1] == "neutralized"
    assert [p.net_return for p in result.periods] == [
        Decimal("0.0506187500"),
        Decimal("0.0229361031"),
    ]


def test_rank_sum_and_zscore_sum_combine_two_components_differently() -> None:
    """Two components, weights 1 and 1. Population standard deviations throughout.

    D1: x = A 10, B 0, C 1, D 2; y = A 0, B 3, C 2, D 1.
      zscore_sum. mean(x) 3.25, squared deviations 45.5625 + 10.5625 + 5.0625 + 1.5625 = 62.75,
      pstdev = sqrt(62.75 / 4) = 3.9608; mean(y) 1.5, pstdev = sqrt(5 / 4) = 1.1180.
        A 1.7042 - 1.3416 = 0.3626; B -0.8205 + 1.3416 = 0.5211; C -0.5681 + 0.4472 = -0.1209;
        D -0.3156 - 0.4472 = -0.7628 -> top two B, A.
      rank_sum (average rank / n): x ranks A 4, B 1, C 2, D 3; y ranks A 1, B 4, C 3, D 2, so
        every name scores 5/4 = 1.25; the tie is broken by code -> A, B. Same book: {A, B}.
    D4: x = A 0, B 0, C 9, D 8; y = A 5, B 6, C 0, D 4.
      zscore_sum. mean(x) 4.25, squared deviations 18.0625 x 2 + 22.5625 + 14.0625 = 72.75,
      pstdev = sqrt(18.1875) = 4.2647; mean(y) 3.75, squared deviations 1.5625 + 5.0625 +
      14.0625 + 0.0625 = 20.75, pstdev = sqrt(5.1875) = 2.2776.
        A -0.9965 + 0.5488 = -0.4477; B -0.9965 + 0.9879 = -0.0087;
        C 1.1138 - 1.6465 = -0.5327; D 0.8793 + 0.1098 = 0.9891 -> ranks D, B: keep B, buy D.
      rank_sum. x ranks A 1.5, B 1.5, C 4, D 3; y ranks A 3, B 4, C 1, D 2:
        A 4.5/4 = 1.125, B 5.5/4 = 1.375, C 5/4 = 1.25, D 5/4 = 1.25 -> B, then C (the C/D tie
        is broken by code): keep B, buy C.
    One set of rows, two combiners, two different books.
    """
    source = ScoreSource(
        components=(("x/v1", "raw", Decimal("1")), ("y/v1", "processed", Decimal("1"))),
        combine="zscore_sum",
    )
    x = {D1: {A: 10.0, B: 0.0, C: 1.0, D: 2.0}, D4: {A: 0.0, B: 0.0, C: 9.0, D: 8.0}}
    y = {D1: {A: 0.0, B: 3.0, C: 2.0, D: 1.0}, D4: {A: 5.0, B: 6.0, C: 0.0, D: 4.0}}
    rows = (
        *score_rows(x, component="x/v1@raw"),
        *score_rows(y, component="y/v1@processed"),
    )

    zscored = run_strategy_backtest(build_inputs(source=source, scores=rows), HAND_FIXTURE_SPEC)
    ranked = run_strategy_backtest(
        build_inputs(source=source.model_copy(update={"combine": "rank_sum"}), scores=rows),
        HAND_FIXTURE_SPEC,
    )

    assert zscored.periods[0].holdings == (A, B)
    assert ranked.periods[0].holdings == (A, B)
    assert zscored.periods[1].holdings == (B, D)
    assert ranked.periods[1].holdings == (B, C)


def test_the_industry_cap_skips_a_candidate_whose_industry_is_full() -> None:
    """`max_industry_weight=0.5` of two names allows one per industry.

    A and B are both `bank`; C is `energy`. D1 ranks A B C D, so the cap buys A and skips B for
    C. A candidate with no industry answer is its own bucket, never a free pass.
    """
    industries = {D1: {A: "bank", B: "bank", C: "energy", D: "energy"}}
    spec = HAND_FIXTURE_SPEC.model_copy(update={"max_industry_weight": Decimal("0.5")})
    first = run_strategy_backtest(build_inputs(industries=industries), spec).periods[0]

    assert [fill.subject for fill in first.fills] == [A, C]


def test_a_participation_cap_below_one_lot_rejects_the_buy() -> None:
    """D trades only 40,000 yuan on D1: 1% is 400 yuan, less than one 100-share lot at 5.00.

    D1 ranks D first here, so D's buy is attempted, sized to zero and counted as rejected.
    """
    turnover = dict(TURNOVER_YUAN)
    turnover[D] = Decimal("40000")
    scores = {D1: {A: 1.0, B: 3.0, C: 2.0, D: 4.0}, D4: SCORES[D4]}
    first = run_strategy_backtest(
        build_inputs(scores=score_rows(scores), quotes=build_quotes(turnover=turnover)),
        HAND_FIXTURE_SPEC,
    ).periods[0]

    assert [(r.subject, r.side) for r in first.rejections] == [(D, "buy")]
    assert "participation" in first.rejections[0].reason
    assert [fill.subject for fill in first.fills] == [B]


def test_a_star_board_buy_is_sized_in_single_shares_above_its_200_share_floor() -> None:
    """688001.SH at 30.00: 100,000 buys 3,333 shares (not a multiple of 100), then fees trim it.

    3,333 x 30.00 = 99,990.00 + commission 25.00 (24.9975 half-up) + slippage 99.99 = 100,114.99
    > 100,000, so one share less: 3,332 x 30.00 = 99,960.00, commission 24.99, slippage 99.96,
    outlay 100,084.95 > 100,000; 3,329 shares: 99,870.00 + 24.97 + 99.87 = 99,994.84 <= 100,000.
    """
    star = "688001.SH"
    prices = {A: PRICES[A], star: tuple(("30.00", "30.00") for _ in SESSIONS)}
    first_previous = {A: "9.90", star: "30.00"}
    turnover = {A: TURNOVER_YUAN[A], star: Decimal("20000000")}
    scores = {D1: {star: 2.0, A: 1.0}, D4: {star: 2.0, A: 1.0}}
    first = run_strategy_backtest(
        build_inputs(
            scores=score_rows(scores),
            quotes=build_quotes(prices, first_previous_close=first_previous, turnover=turnover),
        ),
        HAND_FIXTURE_SPEC,
    ).periods[0]

    star_buy = next(fill for fill in first.fills if fill.subject == star)
    assert star_buy.quantity == 3_329
    assert star_buy.notional + star_buy.fees == Decimal("99994.84")


def test_a_prediction_source_ranks_on_its_single_series() -> None:
    """`prediction_ids` replace components; the series is one component named `prediction`."""
    source = ScoreSource(components=(), combine="rank_sum", prediction_ids=("pred_" + "a" * 8,))
    rows = score_rows(component="prediction")
    result = run_strategy_backtest(build_inputs(source=source, scores=rows), HAND_FIXTURE_SPEC)

    assert [p.holdings for p in result.periods] == [(A, B), (B, D)]


def test_a_row_for_a_component_the_source_does_not_declare_is_refused() -> None:
    with pytest.raises(StrategyBacktestError, match="declares no component"):
        run_strategy_backtest(
            build_inputs(scores=score_rows(component="other/v1@raw")), HAND_FIXTURE_SPEC
        )


def test_a_benchmark_missing_a_session_is_refused() -> None:
    inputs = build_inputs()
    short = {CSI500: {k: v for k, v in BENCHMARK_RETURNS[CSI500].items() if k != D3}}
    broken = replace(inputs, benchmark_returns={**BENCHMARK_RETURNS, **short})

    with pytest.raises(StrategyBacktestError, match=r"000905\.SH"):
        run_strategy_backtest(broken, HAND_FIXTURE_SPEC)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"rebalance_every_sessions": 0}, "rebalance_every_sessions"),
        ({"holding_count": 0}, "holding_count"),
        ({"buffer_rank": 1}, "buffer_rank"),
        ({"max_industry_weight": Decimal("0.1")}, "max_industry_weight"),
        ({"participation_cap": Decimal("0")}, "participation_cap"),
        ({"position_capital": Decimal("0")}, "position_capital"),
        ({"benchmarks": ()}, "benchmarks"),
    ],
)
def test_a_spec_that_cannot_describe_a_portfolio_is_refused(
    update: dict[str, object], message: str
) -> None:
    fields = {**HAND_FIXTURE_SPEC.model_dump(), **update}
    with pytest.raises(ValueError, match=message):
        StrategySpec(**fields)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"components": (), "combine": "zscore_sum"}, "exactly one"),
        (
            {
                "components": ((FACTOR, "raw", Decimal("1")),),
                "combine": "zscore_sum",
                "prediction_ids": ("pred_x",),
            },
            "exactly one",
        ),
        (
            {"components": ((FACTOR, "adjusted", Decimal("1")),), "combine": "rank_sum"},
            "neutralized",
        ),
        ({"components": ((FACTOR, "raw", Decimal("0")),), "combine": "rank_sum"}, "weight"),
        (
            {
                "components": ((FACTOR, "raw", Decimal("1")), (FACTOR, "raw", Decimal("2"))),
                "combine": "rank_sum",
            },
            "twice",
        ),
    ],
)
def test_a_score_source_that_names_no_single_series_is_refused(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ScoreSource(**fields)  # type: ignore[arg-type]


def test_the_answer_carries_every_known_limitation_by_code() -> None:
    """A static source's answer carries every entry that is not about a dynamic source.

    `V2-P6-014` added eight entries that speak only for the trailing-IC and walk-forward
    kinds; `test_a_dynamic_answer_names_the_reconstruction_and_a_static_one_does_not` holds
    those. The whole registry is the first set literal below, the static answer the second.
    """
    result = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC)

    assert result.limitations == tuple(
        item.code for item in KNOWN_STRATEGY_BACKTEST_LIMITATIONS if "static" in item.applies_to
    )
    assert {
        "fills_are_at_the_open_print_judged_as_a_one_price_bar",
        "slippage_is_a_flat_rate_and_not_a_market_impact_model",
        "the_participation_cap_reads_the_signal_sessions_turnover",
        "a_rejected_buy_leaves_its_slot_in_cash_until_the_next_rebalance",
        "a_retained_position_is_not_resized_to_equal_weight",
        "the_exit_leg_is_priced_on_the_entry_share_count",
        "a_session_whose_return_is_unknowable_is_valued_by_the_adjustment_factor",
        "dividends_are_reinvested_through_the_adjustment_factor",
        "the_industry_cap_counts_names_and_is_applied_at_the_signal",
        "a_holding_that_cannot_trade_is_marked_at_its_last_close",
        "the_last_period_may_be_shorter_than_the_rebalance_interval",
        "an_intraday_halt_makes_the_whole_session_untradeable_at_the_open",
        "the_equal_weight_benchmark_is_every_priced_name_and_is_not_investable",
        "the_held_equal_weight_benchmark_asks_the_open_auction_and_not_the_money",
        "a_passing_backtest_is_not_evidence_that_a_signal_is_real",
        "a_dynamic_score_is_a_walk_forward_reconstruction_made_now",
        "the_labels_behind_a_dynamic_score_are_read_at_the_backtests_as_of",
        "a_lookback_reaching_before_the_stored_calendar_is_shorter_rather_than_refused",
        "a_signal_day_whose_source_answers_nothing_is_held_rather_than_traded",
        "a_trailing_ic_weight_is_the_mean_of_the_ics_known_at_the_signal",
        "a_walk_forward_fit_trains_on_labels_closed_before_the_embargo_deadline",
        "a_refit_the_model_refuses_leaves_the_previous_fit_in_use",
        "a_walk_forward_model_reads_no_neutralized_feature",
    } == STRATEGY_BACKTEST_LIMITATION_CODES
    assert (
        set(result.limitations)
        == set(limitation_codes_for("static"))
        == {
            "fills_are_at_the_open_print_judged_as_a_one_price_bar",
            "slippage_is_a_flat_rate_and_not_a_market_impact_model",
            "the_participation_cap_reads_the_signal_sessions_turnover",
            "a_rejected_buy_leaves_its_slot_in_cash_until_the_next_rebalance",
            "a_retained_position_is_not_resized_to_equal_weight",
            "the_exit_leg_is_priced_on_the_entry_share_count",
            "a_session_whose_return_is_unknowable_is_valued_by_the_adjustment_factor",
            "dividends_are_reinvested_through_the_adjustment_factor",
            "the_industry_cap_counts_names_and_is_applied_at_the_signal",
            "a_holding_that_cannot_trade_is_marked_at_its_last_close",
            "the_last_period_may_be_shorter_than_the_rebalance_interval",
            "an_intraday_halt_makes_the_whole_session_untradeable_at_the_open",
            "the_equal_weight_benchmark_is_every_priced_name_and_is_not_investable",
            "the_held_equal_weight_benchmark_asks_the_open_auction_and_not_the_money",
            "a_passing_backtest_is_not_evidence_that_a_signal_is_real",
        }
    )


# --- fix round 1: capped counting and board-legal sale sizes --------------------------------------

STAR: Final[str] = "688001.SH"


def _star_inputs(
    price: str,
    scores: Mapping[date, Mapping[str, float]],
    turnover_on: Mapping[tuple[str, date], Decimal],
) -> StrategyInputs:
    """A (the main-board name) beside one STAR name trading flat at `price` every session."""
    prices = {A: PRICES[A], STAR: tuple((price, price) for _ in SESSIONS)}
    return build_inputs(
        scores=score_rows(scores),
        quotes=build_quotes(
            prices,
            first_previous_close={A: "9.90", STAR: price},
            turnover={A: TURNOVER_YUAN[A], STAR: Decimal("20000000")},
            turnover_on=turnover_on,
        ),
    )


ONE_NAME: Final[StrategySpec] = HAND_FIXTURE_SPEC.model_copy(update={"holding_count": 1})


def test_a_capped_sale_the_market_then_refuses_is_a_rejection_and_not_a_capped_order() -> None:
    """A traded only 3,000,000 on D4 and opens D5 at its limit-down price, 10.80 x 0.9 = 9.72.

    The cap sizes the sale down (1% x 3,000,000 = 30,000 < 9,900 x 9.72 = 96,228) and the
    policy then refuses it at the limit. Nothing filled, so nothing was capped: one rejection,
    zero capped orders, no fill. `capped_orders` counts orders that FILLED smaller, on both
    sides.
    """
    prices = dict(PRICES)
    prices[A] = (*PRICES[A][:4], ("9.72", "9.72"), ("9.72", "9.72"))
    quotes = build_quotes(prices, turnover_on={(A, D4): Decimal("3000000")})
    second = run_strategy_backtest(build_inputs(quotes=quotes), HAND_FIXTURE_SPEC).periods[1]

    assert second.fills == ()
    assert [(r.subject, r.side) for r in second.rejections] == [(A, "sell")]
    assert (second.rejected_orders, second.capped_orders) == (1, 0)


def test_a_capped_star_sale_below_200_shares_is_refused_rather_than_sent_as_an_odd_lot() -> None:
    """STAR at 399.00: 100,000 buys 250 shares (99,750.00 + 24.94 + 99.75 = 99,874.69).

    D4 turnover 4,788,000 caps the D5 sale at 1% = 47,880 = 120 shares. A STAR sale must be at
    least 200 shares while the position holds 200 or more, so 120 (or a main-board 100) is not a
    legal order: the sale is refused, nothing fills, all 250 shares are carried.
    """
    scores = {D1: {STAR: 2.0, A: 1.0}, D4: {A: 2.0, STAR: 1.0}}
    inputs = _star_inputs("399.00", scores, {(STAR, D4): Decimal("4788000")})
    first, second = run_strategy_backtest(inputs, ONE_NAME).periods

    assert [(f.subject, f.quantity) for f in first.fills] == [(STAR, 250)]
    assert second.fills == ()
    assert [(r.subject, r.side) for r in second.rejections] == [(STAR, "sell")]
    assert "participation" in second.rejections[0].reason
    assert second.holdings == (STAR,)
    assert second.capped_orders == 0


def test_a_capped_star_sale_above_200_shares_is_sized_in_single_shares() -> None:
    """The same position with D4 turnover 8,379,000: the cap is 83,790 = 210 shares at 399.00.

    Above the 200-share floor STAR trades in single shares, so the sale is 210 -- not the
    main-board lot floor of 200 -- leaving 40 carried, and it is one capped order.
    """
    scores = {D1: {STAR: 2.0, A: 1.0}, D4: {A: 2.0, STAR: 1.0}}
    inputs = _star_inputs("399.00", scores, {(STAR, D4): Decimal("8379000")})
    second = run_strategy_backtest(inputs, ONE_NAME).periods[1]

    assert [(f.subject, f.side, f.quantity) for f in second.fills] == [(STAR, "sell", 210)]
    assert second.capped_orders == 1
    assert second.holdings == (STAR,)


def _star_remainder_inputs(d5_turnover: Decimal) -> StrategyInputs:
    """STAR at 285.00 over three rebalances (every two sessions), holding one name.

    D1 buys 350 shares (99,750.00 + 24.94 + 99.75 = 99,874.69). D3's turnover 5,700,000 caps
    the D4 sale at 57,000 = 200 shares, leaving a 150-share remainder; the book is full, so
    nothing is bought. D5 wants the remainder gone.
    """
    scores = {D1: {STAR: 2.0, A: 1.0}, D3: {A: 2.0, STAR: 1.0}, D5: {A: 2.0, STAR: 1.0}}
    return _star_inputs(
        "285.00",
        scores,
        {(STAR, D3): Decimal("5700000"), (STAR, D5): d5_turnover},
    )


def test_a_star_remainder_under_200_shares_is_sold_in_full_when_a_sale_is_due() -> None:
    spec = ONE_NAME.model_copy(update={"rebalance_every_sessions": 2})
    periods = run_strategy_backtest(_star_remainder_inputs(Decimal("20000000")), spec).periods

    assert [(f.subject, f.quantity) for f in periods[0].fills] == [(STAR, 350)]
    assert [(f.subject, f.side, f.quantity) for f in periods[1].fills] == [(STAR, "sell", 200)]
    assert periods[1].holdings == (STAR,)
    assert [(f.subject, f.side, f.quantity) for f in periods[2].fills] == [
        (STAR, "sell", 150),
        (A, "buy", 8_900),
    ]


@pytest.mark.parametrize(
    "field", ["commission_rate", "minimum_commission", "transfer_fee_rate", "sell_stamp_duty_rate"]
)
def test_a_negative_cost_is_refused_by_the_cost_schedule_itself(field: str) -> None:
    """A negative fee is a rebate nobody pays, and it would make every backtest look better."""
    with pytest.raises(ValueError, match=field):
        CostSchedule(**{field: Decimal("-0.001")})  # type: ignore[arg-type]


def test_a_spec_refuses_a_negative_cost_that_skipped_validation() -> None:
    """`model_construct` builds a `CostSchedule` without validating it; the spec checks again."""
    rebate = CostSchedule.model_construct(
        commission_rate=Decimal("-0.001"),
        minimum_commission=Decimal("5.00"),
        transfer_fee_rate=Decimal("0"),
        sell_stamp_duty_rate=Decimal("0.0005"),
    )
    with pytest.raises(ValueError, match="negative"):
        StrategySpec.model_validate({**HAND_FIXTURE_SPEC.model_dump(), "costs": rebate})


def test_a_star_remainder_under_200_shares_is_never_sold_in_part() -> None:
    """D5 turnover 2,850,000 caps the sale at 28,500 = 100 shares of the 150 held.

    A remainder under 200 shares must go in one order, so 100 is illegal and the sale is
    refused; the 150 shares are carried and the book, still full, buys nothing.
    """
    spec = ONE_NAME.model_copy(update={"rebalance_every_sessions": 2})
    last = run_strategy_backtest(_star_remainder_inputs(Decimal("2850000")), spec).periods[2]

    assert last.fills == ()
    assert [(r.subject, r.side) for r in last.rejections] == [(STAR, "sell")]
    assert last.holdings == (STAR,)


# --- V2-P6-014: the two dynamic score sources ---------------------------------------------------
#
# Both are held on the hand fixture above with a February lookback in front of it: twenty
# weekday sessions (2026-02-02 .. 2026-02-27) before D1, on one synthetic weekday calendar that
# runs to the end of March so every label window the tests build closes inside it.

FEB_MAR_CALENDAR: Final[TradingCalendar] = build_trading_calendar(
    "SZSE",
    [
        CalendarDay(calendar_date=day, is_trading=day.weekday() < 5)
        for day in (date(2026, 2, 1) + timedelta(days=offset) for offset in range(59))
    ],
)
LOOKBACK: Final[tuple[date, ...]] = tuple(day for day in FEB_MAR_CALENDAR.trading_days if day < D1)
CALENDAR: Final[tuple[date, ...]] = LOOKBACK + SESSIONS
SECOND: Final[str] = "second/v1"
F1: Final[str] = f"{FACTOR}@raw"
F2: Final[str] = f"{SECOND}@raw"
SECOND_SCORES: Final[dict[date, dict[str, float]]] = {
    D1: {A: 1.0, B: 2.0, C: 4.0, D: 3.0},
    D4: {A: 5.0, B: 1.0, C: 2.0, D: 3.0},
}
COMMIT: Final[str] = "0123456789abcdef"


def trailing(
    components: tuple[tuple[str, str], ...] = ((FACTOR, "raw"),),
    *,
    window: int = 10,
    min_obs: int = 1,
    negative: str = "keep_sign",
) -> ScoreSource:
    return ScoreSource.model_validate(
        {
            "combine": "zscore_sum",
            "trailing_ic": {
                "components": components,
                "ic_window_sessions": window,
                "min_ic_observations": min_obs,
                "ic_method": "spearman",
                "horizon_sessions": 1,
                "negative_ic": negative,
                "min_ic_securities": 3,
            },
        }
    )


def ic(
    component: str,
    day: date,
    value: float | None,
    *,
    known_on: date | None = None,
    known_at: datetime | None = None,
) -> ICObservation:
    if known_at is None:
        assert known_on is not None
        known_at = signal_instant(known_on)
    return ICObservation(component=component, prediction_day=day, known_at=known_at, ic=value)


def dynamic_inputs(
    source: ScoreSource,
    *,
    scores: tuple[ScoreRow, ...],
    ics: Sequence[ICObservation] = (),
    fits: Sequence[WalkForwardFit] = (),
    fit_for_day: Mapping[date, WalkForwardFit] | None = None,
) -> StrategyInputs:
    return StrategyInputs(
        source=source,
        sessions=SESSIONS,
        signal_instants={day: signal_instant(day) for day in CALENDAR},
        scores=scores,
        quotes=build_quotes(),
        benchmark_returns=BENCHMARK_RETURNS,
        lookback_sessions=LOOKBACK,
        ic_observations=tuple(ics),
        model_fits=tuple(fits),
        fit_for_day=fit_for_day or {},
    )


def _weights(period: PeriodResult) -> list[tuple[str, int, float | None, float]]:
    return [(w.component, w.observations, w.mean_ic, w.weight) for w in period.ic_weights]


def test_a_factor_whose_ic_is_perfect_only_on_labels_closing_after_the_signal_weighs_zero() -> None:
    """The brief's look-ahead case, on the ledger the static book was held to by hand.

    F1's two ICs (0.3) had closed by D1. F2's IC is 1.0 on every prediction day, and every one
    of those labels closes AFTER D1's 16:30 (known on D2 and D3). At D1 F2 therefore has no
    known IC and abstains -- weight 0 -- so the ranking is F1's alone and D1 buys A and B, the
    static F1 book's first period, to the hand ledger's last digit. At D4 both of F2's labels
    have closed and it weighs 1.0.
    """
    ics = [
        ic(F1, LOOKBACK[-3], 0.3, known_on=LOOKBACK[-1]),
        ic(F1, LOOKBACK[-2], 0.3, known_on=D1),
        ic(F2, LOOKBACK[-1], 1.0, known_on=D2),
        ic(F2, D1, 1.0, known_on=D3),
    ]
    source = trailing(((FACTOR, "raw"), (SECOND, "raw")))
    rows = score_rows() + score_rows(SECOND_SCORES, component=F2)
    result = run_strategy_backtest(dynamic_inputs(source, scores=rows, ics=ics), HAND_FIXTURE_SPEC)

    first, second = result.periods
    assert _weights(first) == [(F1, 2, 0.3, 0.3), (F2, 0, None, 0.0)]
    assert first.holdings == (A, B)
    assert first.net_return == Decimal("0.0506187500")
    assert _weights(second) == [(F1, 2, 0.3, 0.3), (F2, 2, 1.0, 1.0)]


def test_an_ic_known_exactly_at_the_signal_instant_counts_and_a_microsecond_later_does_not() -> (
    None
):
    """`known_at <= instant`: at-or-before, the same inequality the row look-ahead guard uses."""
    source = trailing()
    at = signal_instant(D1)
    ics = [
        ic(F1, LOOKBACK[-2], -0.2, known_at=at),
        ic(F1, LOOKBACK[-1], 0.9, known_at=at + timedelta(microseconds=1)),
    ]
    assert source.trailing_ic is not None
    weights = trailing_ic_weights(
        source.trailing_ic, ics, calendar=CALENDAR, signal_day=D1, instant=at
    )

    assert [(w.component, w.observations, w.mean_ic, w.weight) for w in weights] == [
        (F1, 1, -0.2, -0.2)
    ]


def test_the_trailing_window_counts_calendar_sessions_ending_at_the_signal_day() -> None:
    """`ic_window_sessions=2` at D1 is {2026-02-27, D1}: an IC dated 02-25 is outside it."""
    source = trailing(window=2)
    ics = [
        ic(F1, LOOKBACK[-3], 0.8, known_on=LOOKBACK[-2]),
        ic(F1, LOOKBACK[-1], 0.1, known_on=D1),
    ]
    assert source.trailing_ic is not None
    weights = trailing_ic_weights(
        source.trailing_ic, ics, calendar=CALENDAR, signal_day=D1, instant=signal_instant(D1)
    )

    assert [(w.observations, w.mean_ic) for w in weights] == [(1, 0.1)]


def test_an_unmeasured_ic_is_not_an_observation() -> None:
    """A point whose cross section was too thin or degenerate carries `ic=None` and is skipped."""
    source = trailing()
    ics = [
        ic(F1, LOOKBACK[-3], None, known_on=LOOKBACK[-1]),
        ic(F1, LOOKBACK[-2], 0.4, known_on=D1),
    ]
    assert source.trailing_ic is not None
    weights = trailing_ic_weights(
        source.trailing_ic, ics, calendar=CALENDAR, signal_day=D1, instant=signal_instant(D1)
    )

    assert [(w.observations, w.mean_ic, w.weight) for w in weights] == [(1, 0.4, 0.4)]


def test_fewer_known_ics_than_the_floor_abstains_and_the_book_holds_that_period() -> None:
    """`min_ic_observations=2`: one known IC at D1, so F1 abstains and nothing is ranked.

    The book trades nothing on D2 -- no fill, no rejection, cash untouched -- and says so with
    `held`. The second IC closes before D4, so D4 ranks and buys D and B as the static book does.
    """
    ics = [
        ic(F1, LOOKBACK[-3], 0.5, known_on=LOOKBACK[-1]),
        ic(F1, LOOKBACK[-1], 0.5, known_on=D3),
    ]
    result = run_strategy_backtest(
        dynamic_inputs(trailing(min_obs=2), scores=score_rows(), ics=ics), HAND_FIXTURE_SPEC
    )

    first, second = result.periods
    assert first.held is True
    assert (first.fills, first.rejections, first.holdings) == ((), (), ())
    assert first.net_return == Decimal("0")
    assert first.end_value == first.start_value == HAND_FIXTURE_SPEC.initial_capital
    assert _weights(first) == [(F1, 1, 0.5, 0.0)]
    assert second.held is False
    assert second.holdings == (B, D)


def test_a_trailing_source_that_never_knows_enough_ics_is_refused_saying_so() -> None:
    """One IC ever closes; the floor is two. The source cannot answer on any signal day, and the
    refusal names the floor and the most any component knew."""
    ics = [ic(F1, LOOKBACK[-3], 0.5, known_on=LOOKBACK[-1])]
    with pytest.raises(
        StrategyBacktestError, match=r"min_ic_observations=2 .*the most any component knew was 1\)"
    ):
        run_strategy_backtest(
            dynamic_inputs(trailing(min_obs=2), scores=score_rows(), ics=ics), HAND_FIXTURE_SPEC
        )


def test_a_trailing_source_whose_every_weight_is_clipped_is_a_zero_trade_answer() -> None:
    """Every known IC is negative and clip_to_zero weighs them all at zero: the source answered
    ("trade none of these") on both signal days, so the run is reported, held throughout."""
    ics = [
        ic(F1, LOOKBACK[-3], -0.3, known_on=LOOKBACK[-1]),
        ic(F1, D1, -0.1, known_on=D3),
    ]
    result = run_strategy_backtest(
        dynamic_inputs(trailing(negative="clip_to_zero"), scores=score_rows(), ics=ics),
        HAND_FIXTURE_SPEC,
    )

    assert [period.held for period in result.periods] == [True, True]
    assert [period.fills for period in result.periods] == [(), ()]
    assert [period.end_value for period in result.periods] == [
        HAND_FIXTURE_SPEC.initial_capital
    ] * 2
    assert [_weights(period)[0][1] for period in result.periods] == [1, 2]


@pytest.mark.parametrize(
    ("negative", "first_weight", "first_holdings", "held"),
    [("clip_to_zero", 0.0, (), True), ("keep_sign", -0.3, (C, D), False)],
)
def test_a_negative_trailing_ic_is_clipped_or_kept_as_declared(
    negative: str, first_weight: float, first_holdings: tuple[str, ...], held: bool
) -> None:
    """D1 knows one IC of -0.3. Clipped, F1 weighs nothing and D1 is held; kept, the ranking is
    reversed and D1 buys the two it ranks lowest, C and D. By D4 a 0.9 has closed: mean 0.3."""
    ics = [
        ic(F1, LOOKBACK[-3], -0.3, known_on=LOOKBACK[-1]),
        ic(F1, D1, 0.9, known_on=D3),
    ]
    result = run_strategy_backtest(
        dynamic_inputs(trailing(negative=negative), scores=score_rows(), ics=ics),
        HAND_FIXTURE_SPEC,
    )

    first, second = result.periods
    assert _weights(first) == [(F1, 1, -0.3, first_weight)]
    assert (first.held, first.holdings) == (held, first_holdings)
    assert _weights(second)[0][1:3] == (2, pytest.approx(0.3))


def test_a_constant_trailing_ic_is_the_static_source_with_those_weights() -> None:
    """ICs of exactly 0.5 and 0.25 on every lookback day make the same book, period for period,
    as a static source weighting the two components 0.5 and 0.25."""
    ics = [
        ic(component, day, value, known_on=CALENDAR[CALENDAR.index(day) + 2])
        for component, value in ((F1, 0.5), (F2, 0.25))
        for day in LOOKBACK[:-2]
    ]
    rows = score_rows() + score_rows(SECOND_SCORES, component=F2)
    static = ScoreSource(
        components=((FACTOR, "raw", Decimal("0.5")), (SECOND, "raw", Decimal("0.25"))),
        combine="zscore_sum",
    )
    dynamic = run_strategy_backtest(
        dynamic_inputs(trailing(((FACTOR, "raw"), (SECOND, "raw"))), scores=rows, ics=ics),
        HAND_FIXTURE_SPEC,
    )
    fixed = run_strategy_backtest(build_inputs(source=static, scores=rows), HAND_FIXTURE_SPEC)

    assert [p.model_dump(exclude={"ic_weights"}) for p in dynamic.periods] == [
        p.model_dump(exclude={"ic_weights"}) for p in fixed.periods
    ]
    assert [w.weight for w in dynamic.periods[0].ic_weights] == [0.5, 0.25]


def test_an_ic_for_a_component_the_source_does_not_declare_is_refused() -> None:
    with pytest.raises(StrategyBacktestError, match="declares no component"):
        run_strategy_backtest(
            dynamic_inputs(
                trailing(), scores=score_rows(), ics=[ic(F2, LOOKBACK[-3], 0.3, known_on=D1)]
            ),
            HAND_FIXTURE_SPEC,
        )


def wf_spec(**overrides: object) -> WalkForwardModel:
    fields: dict[str, object] = {
        "family": BASELINE_FAMILY,
        "features": (f"{FACTOR}@raw",),
        "seed": 0,
        "code_commit": COMMIT,
        "train_sessions": 10,
        "refit_every_sessions": 3,
        "embargo_sessions": 1,
        "horizon_sessions": 1,
    }
    return WalkForwardModel.model_validate({**fields, **overrides})


def wf_source(**overrides: object) -> ScoreSource:
    return ScoreSource(combine="zscore_sum", walk_forward=wf_spec(**overrides))


XS: Final[str] = "x_feature"


def wf_examples(days: Sequence[date]) -> list[TrainingExample]:
    """Four securities a day; the feature and the target both rise with the code."""
    return [
        training_example(
            ts_code=code,
            prediction_day=day,
            features=(float(position),),
            target=0.01 * (position + 1),
            calendar=FEB_MAR_CALENDAR,
        )
        for day in days
        for position, code in enumerate((A, B, C, D))
    ]


WF_EXAMPLES: Final[list[TrainingExample]] = wf_examples(CALENDAR[:23])
WF_MODEL: Final[CrossSectionalRankModel] = CrossSectionalRankModel(
    declaration=AlphaModelDeclaration(
        name="wf",
        family=BASELINE_FAMILY,
        horizon="1d",
        feature_version="features/v1",
        seed=0,
        code_commit=COMMIT,
    )
)


def fits_at(*refit_days: date, spec: WalkForwardModel | None = None) -> tuple[WalkForwardFit, ...]:
    return walk_forward_fits(
        WF_MODEL,
        WF_EXAMPLES,
        feature_ids=(XS,),
        spec=wf_spec() if spec is None else spec,
        calendar=CALENDAR,
        instants={day: signal_instant(day) for day in CALENDAR},
        refit_days=refit_days,
    )


def test_a_walk_forward_fit_trains_only_on_labels_closed_strictly_before_refit_minus_embargo() -> (
    None
):
    """Refit on D1 (calendar index 20), embargo 1, horizon 1: the deadline is 02-27's 16:30.

    A prediction day t's 1d label enters on t+1 and exits on t+2, so it is known strictly
    before 02-27's 16:30 only when t+2 <= 02-26, i.e. t <= 02-24 (index 16). The 10-session
    window ending at D1 starts at index 11 (02-17): six prediction days, 02-17 .. 02-24, four
    names each. The newest label the fit consumed exits on 02-26.
    """
    (fit,) = fits_at(D1)

    assert fit.refusal is None
    assert fit.prediction_day_count == 6
    assert fit.example_count == 24
    assert fit.labels_known_at == signal_instant(date(2026, 2, 26))
    assert fit.artifact is not None
    assert fit.artifact.training_cutoff.date() == date(2026, 2, 26)


def test_the_training_window_is_train_sessions_calendar_sessions_ending_at_the_refit() -> None:
    """`train_sessions=7` ending at D1 starts at 02-20 (index 14): 02-20, 02-23, 02-24."""
    (fit,) = fits_at(D1, spec=wf_spec(train_sessions=7))

    assert fit.prediction_day_count == 3


def test_a_refit_with_no_closed_label_in_its_window_is_a_stated_refusal_not_a_fit() -> None:
    (fit,) = fits_at(LOOKBACK[3])

    assert fit.fitted is None
    assert fit.artifact is None
    assert fit.refusal is not None
    assert "no training example" in fit.refusal


def test_the_fit_in_use_on_a_signal_day_is_the_latest_one_refitted_on_or_before_it() -> None:
    early, late = fits_at(D1, D4)

    assert usable_fit((early, late), signal_day=D3) is early
    assert usable_fit((early, late), signal_day=D4) is late
    assert usable_fit((early, late), signal_day=LOOKBACK[-1]) is None
    refused = replace(late, fitted=None, artifact=None, refusal="the fit refused")
    assert usable_fit((early, refused), signal_day=D6) is early


def model_rows(scores: Mapping[date, Mapping[str, float]] = SCORES) -> tuple[ScoreRow, ...]:
    return score_rows(scores, component=MODEL_COMPONENT)


def test_a_walk_forward_source_trades_the_models_scores_under_the_fit_in_use() -> None:
    early, late = fits_at(D1, D4)
    result = run_strategy_backtest(
        dynamic_inputs(
            wf_source(),
            scores=model_rows(),
            fits=(early, late),
            fit_for_day={D1: early, D4: late},
        ),
        HAND_FIXTURE_SPEC,
    )

    assert [p.holdings for p in result.periods] == [(A, B), (B, D)]
    assert [p.model_fit.refit_day if p.model_fit else None for p in result.periods] == [D1, D4]
    assert [fit.refit_day for fit in result.model_fits] == [D1, D4]


def test_a_fit_whose_labels_close_after_the_signal_minus_embargo_is_refused() -> None:
    """D4's fit consumed a label exiting on 03-03; used on D1 it would be look-ahead."""
    early, late = fits_at(D1, D4)

    with pytest.raises(StrategyBacktestError, match="embargo"):
        run_strategy_backtest(
            dynamic_inputs(
                wf_source(), scores=model_rows(), fits=(early, late), fit_for_day={D1: late}
            ),
            HAND_FIXTURE_SPEC,
        )


def test_a_fit_whose_newest_label_closes_exactly_at_the_embargo_deadline_is_refused() -> None:
    """Built under embargo 1, D1's fit's newest label exits on 02-26. Declared at embargo 2,
    D1's deadline is 02-26's own 16:30 -- equal, not strictly before -- so it is refused."""
    (early,) = fits_at(D1)

    with pytest.raises(StrategyBacktestError, match="embargo"):
        run_strategy_backtest(
            dynamic_inputs(
                wf_source(embargo_sessions=2, train_sessions=11),
                scores=model_rows({D1: SCORES[D1]}),
                fits=(early,),
                fit_for_day={D1: early},
            ),
            HAND_FIXTURE_SPEC,
        )


def test_no_fit_yet_is_no_scores_and_the_book_holds() -> None:
    (late,) = fits_at(D4)
    result = run_strategy_backtest(
        dynamic_inputs(
            wf_source(),
            scores=model_rows({D4: SCORES[D4]}),
            fits=(late,),
            fit_for_day={D4: late},
        ),
        HAND_FIXTURE_SPEC,
    )

    first, second = result.periods
    assert (first.held, first.model_fit, first.fills) == (True, None, ())
    assert (second.held, second.holdings) == (False, (B, D))


def test_model_scores_on_a_day_no_fit_is_named_for_are_refused() -> None:
    with pytest.raises(StrategyBacktestError, match="no fit"):
        run_strategy_backtest(
            dynamic_inputs(wf_source(), scores=model_rows(), fits=(), fit_for_day={}),
            HAND_FIXTURE_SPEC,
        )


def test_a_fit_that_abstains_on_every_name_is_a_zero_trade_answer() -> None:
    """A fit is in use on both signal days and scored nobody: held throughout, and reported."""
    early, late = fits_at(D1, D4)
    result = run_strategy_backtest(
        dynamic_inputs(
            wf_source(), scores=(), fits=(early, late), fit_for_day={D1: early, D4: late}
        ),
        HAND_FIXTURE_SPEC,
    )

    assert [period.held for period in result.periods] == [True, True]
    assert [p.model_fit.refit_day if p.model_fit else None for p in result.periods] == [D1, D4]


def test_a_walk_forward_source_with_no_admissible_fit_anywhere_is_refused_saying_so() -> None:
    """Every refit is refused (nothing closed before the embargo deadline), so no signal day has
    a fit in use; the refusal carries the refits' own reasons."""
    (refused,) = fits_at(LOOKBACK[3])
    with pytest.raises(
        StrategyBacktestError, match=r"no admissible fit .* 0 fitted; refusals: \['no training"
    ):
        run_strategy_backtest(
            dynamic_inputs(wf_source(), scores=(), fits=(refused,), fit_for_day={}),
            HAND_FIXTURE_SPEC,
        )


def test_a_dynamic_source_beside_a_static_one_is_refused() -> None:
    source = trailing()
    with pytest.raises(ValueError, match="exactly one"):
        ScoreSource.model_validate(
            {
                "combine": "zscore_sum",
                "components": ((FACTOR, "raw", Decimal("1")),),
                "trailing_ic": source.trailing_ic,
            }
        )
    with pytest.raises(ValueError, match="exactly one"):
        ScoreSource(combine="zscore_sum", trailing_ic=source.trailing_ic, walk_forward=wf_spec())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"embargo_sessions": 0}, "embargo"),
        ({"train_sessions": 4}, "train_sessions"),
        ({"features": ()}, "features"),
        ({"features": (f"{FACTOR}@raw", f"{FACTOR}@raw")}, "twice"),
        ({"horizon_sessions": 0}, "horizon_sessions"),
        ({"refit_every_sessions": 0}, "refit_every_sessions"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_a_walk_forward_model_that_cannot_be_point_in_time_is_refused(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        wf_spec(**overrides)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"components": ()}, "components"),
        ({"components": ((FACTOR, "raw"), (FACTOR, "raw"))}, "twice"),
        ({"min_ic_observations": 11}, "min_ic_observations"),
        ({"min_ic_securities": 2}, "min_ic_securities"),
        ({"ic_method": "kendall"}, "ic_method"),
        ({"negative_ic": "abs"}, "negative_ic"),
    ],
)
def test_a_trailing_ic_declaration_that_cannot_weigh_anything_is_refused(
    overrides: dict[str, object], message: str
) -> None:
    source = trailing()
    assert source.trailing_ic is not None
    with pytest.raises(ValueError, match=message):
        TrailingICWeights.model_validate({**source.trailing_ic.model_dump(), **overrides})


def test_a_dynamic_answer_names_the_reconstruction_and_a_static_one_does_not() -> None:
    ics = [ic(F1, LOOKBACK[-3], 0.3, known_on=D1)]
    dynamic = run_strategy_backtest(
        dynamic_inputs(trailing(), scores=score_rows(), ics=ics), HAND_FIXTURE_SPEC
    )
    static = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC)
    early, late = fits_at(D1, D4)
    model = run_strategy_backtest(
        dynamic_inputs(
            wf_source(), scores=model_rows(), fits=(early, late), fit_for_day={D1: early, D4: late}
        ),
        HAND_FIXTURE_SPEC,
    )

    reconstruction = "a_dynamic_score_is_a_walk_forward_reconstruction_made_now"
    trailing_rule = "a_trailing_ic_weight_is_the_mean_of_the_ics_known_at_the_signal"
    fit_rule = "a_walk_forward_fit_trains_on_labels_closed_before_the_embargo_deadline"
    assert reconstruction in dynamic.limitations
    assert reconstruction in model.limitations
    assert reconstruction not in static.limitations
    assert trailing_rule in dynamic.limitations
    assert trailing_rule not in model.limitations
    assert fit_rule in model.limitations
    assert set(static.limitations) == set(limitation_codes_for("static"))
    assert set(dynamic.limitations) == set(limitation_codes_for("trailing_ic"))
    assert set(model.limitations) == set(limitation_codes_for("walk_forward"))


# --- explicit rebalance days (V2-P6-011) ---------------------------------------------------------


def test_explicit_rebalance_days_equal_to_the_grid_are_the_grid() -> None:
    """`rebalance_days` replaces the fixed grid; given the grid's own days it is the same run,
    and without it the grid is what runs -- every existing result is unchanged."""
    grid = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC)
    explicit = run_strategy_backtest(
        replace(HAND_FIXTURE_INPUTS, rebalance_days=(D1, D4)), HAND_FIXTURE_SPEC
    )

    assert explicit.periods == grid.periods
    assert [(period.start, period.end) for period in grid.periods] == [(D1, D4), (D4, D6)]
    # The answer says which it was: a run on named days cannot pass for a grid run.
    assert (grid.rebalance_days, explicit.rebalance_days) == (None, (D1, D4))
    assert explicit != grid


def test_explicit_rebalance_days_off_the_grid_set_the_periods() -> None:
    """The days a daily command actually rebalanced on -- here D1 and D5, a rebalance caught up
    one session after the grid's D4 -- are the signal days, and each period runs to the next."""
    rows = score_rows({D1: SCORES[D1], D5: SCORES[D4]})
    result = run_strategy_backtest(
        replace(build_inputs(scores=rows), rebalance_days=(D1, D5)), HAND_FIXTURE_SPEC
    )

    assert [(period.start, period.end) for period in result.periods] == [(D1, D5), (D5, D6)]
    assert result.periods[1].fills
    assert all(fill.day == D6 for fill in result.periods[1].fills)


@pytest.mark.parametrize(
    ("days", "reason"),
    [
        pytest.param((D1, date(2026, 3, 7)), "not a session", id="a closed day"),
        pytest.param((D4, D1), "ascending", id="out of order"),
        pytest.param((D1, D4, D4), "ascending", id="twice"),
        pytest.param((D1, D6), "no session after it", id="the last session"),
        pytest.param((D2, D4), "first session", id="not the first session"),
        pytest.param((), "at least one", id="none"),
    ],
)
def test_rebalance_days_the_range_cannot_trade_are_refused(
    days: tuple[date, ...], reason: str
) -> None:
    with pytest.raises(StrategyBacktestError, match=reason):
        run_strategy_backtest(replace(HAND_FIXTURE_INPUTS, rebalance_days=days), HAND_FIXTURE_SPEC)


# --- V2-P6-020: a held position across a recorded pre_close / adj_factor disagreement -------------
#
# A's factor jumps 1.0 -> 1.1 on D3 with a `pre_close` equal to D2's close: the factor path says
# A gained 10% overnight and the published one says it did not. The hand fixture holds A from D2's
# open through D4's close and sells it at D5's open, so it is held across D3.


def _jumped_quotes(
    *,
    on: date = D3,
    recorded: str | None = None,
    prices: Mapping[str, Sequence[tuple[str, str]]] = PRICES,
) -> dict[date, dict[str, SessionQuote]]:
    quotes = build_quotes(prices)
    for day in SESSIONS:
        if day < on:
            continue
        quote = quotes[day][A]
        # The session's own two statements: the published pre_close is the previous close and
        # the factor says 1.0 -> 1.1, so the implied pre_close is pre_close / 1.1.
        quotes[day][A] = replace(
            quote,
            adj_factor=Decimal("1.1"),
            recorded_path=recorded if day == on else None,  # type: ignore[arg-type]
            path_ratio=JUMP_RATIO if day == on and recorded is not None else None,
        )
    return quotes


JUMP_RATIO: Final[Decimal] = Decimal(1) / Decimal("1.1")
"""`implied_pre_close / pre_close` on the jump session: the published path's gross return over
the factor path's."""


def _run(quotes: Mapping[date, Mapping[str, SessionQuote]]) -> tuple[PeriodResult, ...]:
    return run_strategy_backtest(build_inputs(quotes=quotes), HAND_FIXTURE_SPEC).periods


def _values(periods: Sequence[PeriodResult]) -> list[tuple[Decimal, Decimal, Decimal]]:
    return [(p.start_value, p.end_value, p.net_return) for p in periods]


def test_a_held_position_follows_a_recorded_published_path_through_the_disagreement() -> None:
    """Without the record the book marks A at 10.80 x 1.1 on D4 and sells it for 1.1 times what
    the market paid: 10% of phantom return. With the record the D3 link is the published
    `close / pre_close`, and every later mark and the sale are rescaled by the same factor -- the
    book answers exactly the hand-computed ledger, to the cent."""
    baseline = _run(build_quotes())
    unrecorded = _run(_jumped_quotes())
    recorded = _run(_jumped_quotes(recorded="published"))

    assert _values(recorded) == _values(baseline)
    assert [p.fills for p in recorded] == [p.fills for p in baseline]
    assert unrecorded[0].end_value == Decimal("220815.75")
    assert _values(unrecorded) != _values(baseline)
    assert all(p.unknowable_sessions == () for p in recorded)


def test_a_recorded_factor_path_needs_no_correction() -> None:
    assert _values(_run(_jumped_quotes(recorded="adjusted"))) == _values(_run(_jumped_quotes()))


def test_a_position_opened_on_the_disputed_session_is_not_rescaled() -> None:
    """A bought at D2's open never held D1's close into D2, so a record on D2 is not about it:
    the overnight link the two statements disagree over is not one this position crossed. D2
    opens at 10.10 against a 10.00 previous close here, so rescaling the new position through
    that link would move its value by 1%."""
    gapped = {**PRICES, A: (PRICES[A][0], ("10.10", "10.20"), *PRICES[A][2:])}
    jumped_on_entry = _jumped_quotes(on=D2, prices=gapped)
    recorded_on_entry = _jumped_quotes(on=D2, recorded="published", prices=gapped)

    assert _values(_run(recorded_on_entry)) == _values(_run(jumped_on_entry))
    assert _values(_run(recorded_on_entry)) == _values(_run(build_quotes(gapped)))


def test_an_unknowable_session_is_valued_by_the_factor_and_named_on_its_period() -> None:
    """Neither statement is corroborated, so the book keeps the valuation it gives every other
    session and every resumption -- `close x adj_factor / entry adj_factor` -- and names the
    `(security, session)` on the period it fell in. It is not excluded ex ante: the D1 signal,
    which could not know about D3, still buys A at D2's open."""
    unknowable = _run(_jumped_quotes(recorded="unknowable"))
    unrecorded = _run(_jumped_quotes())

    assert _values(unknowable) == _values(unrecorded)
    assert (D2, A, "buy") in [(f.day, f.subject, f.side) for f in unknowable[0].fills]
    assert unknowable[0].unknowable_sessions == (f"{A}@{D3.isoformat()}",)
    assert unknowable[1].unknowable_sessions == ()
    assert (
        "a_session_whose_return_is_unknowable_is_valued_by_the_adjustment_factor"
        in STRATEGY_BACKTEST_LIMITATION_CODES
    )


def test_the_correction_is_the_sessions_own_ratio_and_not_the_last_mark() -> None:
    """Minor 2 of review round 1. A traded on D3 but the book has no quote for it there -- the
    view could not price it that session -- so on D4, where the factor jumps and the record names
    the published path, its last valuation is D2's close (10.20), not the previous bar (D3,
    10.50). A correction derived from the last mark would book D4 at 10.80 x 10.20 / 10.50; the
    session's own `implied / pre_close` ratio does not depend on when the book last looked, and
    the book lands exactly where the unjumped book does."""
    unpriced = frozenset({(A, D3)})

    def without_d3(
        quotes: dict[date, dict[str, SessionQuote]],
    ) -> dict[date, dict[str, SessionQuote]]:
        for subject, day in unpriced:
            del quotes[day][subject]
        return quotes

    baseline = _run(without_d3(build_quotes()))
    recorded = _run(without_d3(_jumped_quotes(on=D4, recorded="published")))

    assert _values(recorded) == _values(baseline)


def test_every_unknowable_crossing_is_on_the_run_with_its_price_against_the_other_path() -> None:
    """I1 of review round 1: a reader must see from the run itself whether it was affected, and by
    how much. A holds 9,900 shares through D3, valued by the factor path at 10.50 x 1.1 =
    114,345.00; the published path would have booked 114,345.00 x (1/1.1 - 1) = -10,395.00 less,
    which is -5.1975% of the period's 200,000.00 starting book. Computed from the session's own
    ratio, with no second run."""
    result = run_strategy_backtest(
        build_inputs(quotes=_jumped_quotes(recorded="unknowable")), HAND_FIXTURE_SPEC
    )

    (crossing,) = result.unknowable_crossings
    assert (crossing.subject, crossing.day, crossing.period_start) == (A, D3, D1)
    assert crossing.held_value == Decimal("114345.00")
    assert crossing.valuation_difference == Decimal("-10395.00")
    assert crossing.share_of_book == Decimal("-0.0519750000")
    assert result.periods[0].unknowable_sessions == (f"{A}@{D3.isoformat()}",)
    clean = run_strategy_backtest(build_inputs(), HAND_FIXTURE_SPEC)
    assert clean.unknowable_crossings == ()


def test_a_recorded_session_must_carry_its_ratio() -> None:
    quote = build_quotes()[D3][A]
    with pytest.raises(StrategyBacktestError, match="path_ratio"):
        replace(quote, recorded_path="unknowable")


def test_an_unknowable_session_a_position_did_not_hold_through_is_not_named() -> None:
    """C is never bought. A record on its session is not the book's exposure."""
    quotes = build_quotes()
    quotes[D3][C] = replace(quotes[D3][C], recorded_path="unknowable", path_ratio=JUMP_RATIO)

    assert all(p.unknowable_sessions == () for p in _run(quotes))


def test_a_capped_sale_on_an_unknowable_session_prices_the_whole_position_once() -> None:
    """Review round 2, Minor 2. A is sold at D5's open, where its factor jumps with no decided
    path, and a thin D4 caps the sale at 4,500 of its 9,900 shares. The crossing is one entry for
    the whole position the book held into the session -- 9,900 x 11.00 x 1.1 = 119,790.00 -- not
    the 4,500 sold with the 5,400 kept never counted."""
    quotes = build_quotes(turnover_on={(A, D4): Decimal("5000000")})
    for day in (D5, D6):
        quotes[day][A] = replace(
            quotes[day][A],
            adj_factor=Decimal("1.1"),
            recorded_path="unknowable" if day == D5 else None,
            path_ratio=JUMP_RATIO if day == D5 else None,
        )

    result = run_strategy_backtest(build_inputs(quotes=quotes), HAND_FIXTURE_SPEC)

    second = result.periods[1]
    assert [(f.subject, f.side, f.quantity) for f in second.fills if f.subject == A] == [
        (A, "sell", 4_500)
    ]
    (crossing,) = result.unknowable_crossings
    assert (crossing.subject, crossing.day) == (A, D5)
    assert crossing.held_value == Decimal("119790.00")
    assert crossing.valuation_difference == Decimal("-10890.00")


# --- V2-P6-024: the all-A equal-weight benchmark the book could have bought and held ------------
#
# Six main-board names over the hand fixture's six sessions, signals on D1 and D4 (every three
# sessions), so period 1 buys at D2's open and runs to D4's close and period 2 buys at D5's open
# and runs to D6's close. Each bar is `(previous close, open, close)`; the band is +-10% of the
# previous close, as `_band` builds it. `None` is a session with no bar.
#
#   P  a plain name whose D2 open (10.00) is above its D1 close (9.80): the overnight gap is
#      not in the benchmark, exactly as it is not in a position bought at D2's open.
#   Q  opens D2 AT its limit-up (11.00) and trades down to 10.80: refused at the open, so out of
#      period 1 -- the open-auction verdict, not the close.
#   R  suspended on D2: out of period 1.
#   S  no bar on D3 (mid period 1) and none on D6 (the last session of period 2), where it keeps
#      D5's mark.
#   T  delisted after D2: marked at D2's close for the rest of period 1, no bar on D4 so not a
#      member of period 2.
#   U  a 2-for-1 split between D2 and D3: the factor goes 1 -> 2 and the price halves.

HELD_P, HELD_Q, HELD_R, HELD_S, HELD_T, HELD_U, HELD_V = (
    "600101.SH",
    "600102.SH",
    "600103.SH",
    "600104.SH",
    "600105.SH",
    "600106.SH",
    "600107.SH",
)
Bar = tuple[str, str, str] | None
HELD_BARS: Final[dict[str, tuple[Bar, ...]]] = {
    HELD_P: (
        ("9.80", "9.80", "9.80"),
        ("9.80", "10.00", "10.50"),
        ("10.50", "10.50", "11.00"),
        ("11.00", "11.00", "11.00"),
        ("11.00", "11.00", "11.00"),
        ("11.00", "11.00", "12.10"),
    ),
    HELD_Q: (
        ("10.00", "10.00", "10.00"),
        ("10.00", "11.00", "10.80"),
        ("10.80", "10.80", "11.00"),
        ("11.00", "11.00", "11.00"),
        ("11.00", "11.00", "11.55"),
        ("11.55", "11.55", "11.55"),
    ),
    HELD_R: (
        ("20.00", "20.00", "20.00"),
        ("20.00", "20.00", "20.00"),
        ("20.00", "20.00", "20.00"),
        ("20.00", "20.00", "20.00"),
        ("20.00", "20.00", "20.00"),
        ("20.00", "20.00", "19.00"),
    ),
    HELD_S: (
        ("4.90", "4.90", "4.90"),
        ("4.90", "5.00", "5.10"),
        None,
        ("5.10", "5.10", "5.20"),
        ("5.20", "5.20", "5.46"),
        None,
    ),
    HELD_T: (("8.00", "8.00", "8.00"), ("8.00", "8.00", "7.60"), None, None, None, None),
    HELD_U: (
        ("20.00", "20.00", "20.00"),
        ("20.00", "20.00", "20.00"),
        ("10.00", "10.00", "10.50"),
        ("10.50", "10.50", "11.00"),
        ("11.00", "11.00", "11.00"),
        ("11.00", "11.00", "12.10"),
    ),
}
HELD_ADJ: Final[dict[str, tuple[str, ...]]] = {HELD_U: ("1", "1", "2", "2", "2", "2")}
HELD_SUSPENDED: Final[frozenset[tuple[str, date]]] = frozenset({(HELD_R, D2)})
HELD_SPEC: Final[StrategySpec] = HAND_FIXTURE_SPEC.model_copy(
    update={"benchmarks": (EQUAL_WEIGHT_ALL_A_HELD,)}
)


def held_quotes(
    bars: Mapping[str, Sequence[Bar]] = HELD_BARS,
    *,
    adj: Mapping[str, Sequence[str]] = HELD_ADJ,
    suspended: frozenset[tuple[str, date]] = HELD_SUSPENDED,
) -> dict[date, dict[str, SessionQuote]]:
    quotes: dict[date, dict[str, SessionQuote]] = {day: {} for day in SESSIONS}
    for subject, series in bars.items():
        factors = adj.get(subject, ("1",) * len(SESSIONS))
        for day, bar, factor in zip(SESSIONS, series, factors, strict=True):
            if bar is None:
                continue
            previous, open_, close = (Decimal(text) for text in bar)
            quotes[day][subject] = SessionQuote(
                bar=_bar(
                    subject,
                    day,
                    previous_close=previous,
                    open_=open_,
                    close=close,
                    suspended=(subject, day) in suspended,
                ),
                turnover_yuan=Decimal("20000000"),
                adj_factor=Decimal(factor),
            )
    return quotes


def held_inputs(
    quotes: Mapping[date, Mapping[str, SessionQuote]] | None = None,
) -> StrategyInputs:
    """The book trades P and U on the fixture's scores; the benchmark reads every quote."""
    return StrategyInputs(
        source=RAW_SOURCE,
        sessions=SESSIONS,
        signal_instants={day: signal_instant(day) for day in SESSIONS},
        scores=score_rows({D1: {HELD_P: 2.0, HELD_U: 1.0}, D4: {HELD_P: 1.0, HELD_U: 2.0}}),
        quotes=held_quotes() if quotes is None else quotes,
        benchmark_returns={},
    )


def _held(result_periods: Sequence[PeriodResult]) -> list[Decimal]:
    return [period.benchmark_returns[EQUAL_WEIGHT_ALL_A_HELD] for period in result_periods]


def test_the_held_benchmark_matches_the_hand_computed_periods() -> None:
    """Every number by hand, each member entered at the execution session's OPEN (times its
    factor there) and marked at each session's close times its factor, keeping its last mark.

    PERIOD 1 (signal D1, bought at D2's open, marked to D4's close). The notional book starts in
    cash at D1's close, as the book does, so the 9.80 -> 10.00 overnight of P is in no position.
    Members: every name with a complete quote on D1 that the policy would buy at D2's open -- not
    Q (open at limit-up), not R (halted).
      P  11.00 / 10.00 - 1 = +0.10
      S   5.20 /  5.00 - 1 = +0.04  (no bar on D3; D4 marks it again)
      T   7.60 /  8.00 - 1 = -0.05  (delisted: D2's close is its last mark)
      U  11.00 x 2 / (20.00 x 1) - 1 = +0.10  (the split is in the factor)
      mean = 0.19 / 4 = 0.0475
    PERIOD 2 (signal D4). At D5's open the notional book sells period 1's members; every one
    opens where it closed D4 (T at its last mark), so the overnight factor is 1. It buys the new
    members -- T has no bar on D4 -- and holds them to D6's close.
      P  12.10 / 11.00 - 1 = +0.10
      Q  11.55 / 11.00 - 1 = +0.05
      R  19.00 / 20.00 - 1 = -0.05
      S   5.46 /  5.20 - 1 = +0.05  (no bar on D6: D5's mark stands)
      U  12.10 x 2 / (11.00 x 2) - 1 = +0.10
      mean = 0.25 / 5 = 0.05
    """
    result = run_strategy_backtest(held_inputs(), HELD_SPEC)

    assert _held(result.periods) == [Decimal("0.0475000000"), Decimal("0.0500000000")]
    assert all(period.benchmark_unknowable_sessions == () for period in result.periods)


def test_a_new_listing_is_a_member_only_when_its_first_open_could_be_bought() -> None:
    """V lists on D4, the signal day: it has a bar there, so it is a candidate for period 2.
    At D5 its band is 14.40 x 1.1 = 15.84. Opening AT 15.84 it is refused and period 2 is the
    five names above (0.05); opening at 15.00 and closing D6 at 16.50 it is bought, +0.10, and
    period 2 is (0.25 + 0.10) / 6."""
    first: Bar = ("10.00", "12.00", "14.40")

    def with_v(d5: Bar, d6: Bar) -> list[Decimal]:
        bars = {**HELD_BARS, HELD_V: (None, None, None, first, d5, d6)}
        return _held(run_strategy_backtest(held_inputs(held_quotes(bars)), HELD_SPEC).periods)

    locked = with_v(("14.40", "15.84", "15.84"), ("15.84", "15.84", "17.42"))
    opened = with_v(("14.40", "15.00", "15.84"), ("15.84", "15.84", "16.50"))

    assert locked == [Decimal("0.0475000000"), Decimal("0.0500000000")]
    assert opened == [Decimal("0.0475000000"), Decimal("0.0583333333")]


def test_the_held_benchmark_is_the_books_own_valuation_across_a_recorded_path() -> None:
    """P's factor jumps 1.0 -> 1.1 on D3, inside period 1 (V2-P6-020). Recorded `published`,
    the member is rescaled exactly as a holding is and the period is the unjumped 0.0475.
    Recorded `unknowable`, it is valued by the factor path -- 11.00 x 1.1 / 10.00 - 1 = +0.21,
    so (0.21 + 0.04 - 0.05 + 0.10) / 4 = 0.075 -- and the crossing is named on the period."""

    def jumped(recorded: str | None) -> dict[date, dict[str, SessionQuote]]:
        quotes = held_quotes()
        for day in (D3, D4, D5, D6):
            quotes[day][HELD_P] = replace(
                quotes[day][HELD_P],
                adj_factor=Decimal("1.1"),
                recorded_path=recorded if day == D3 else None,  # type: ignore[arg-type]
                path_ratio=JUMP_RATIO if day == D3 and recorded is not None else None,
            )
        return quotes

    published = run_strategy_backtest(held_inputs(jumped("published")), HELD_SPEC).periods
    unknowable = run_strategy_backtest(held_inputs(jumped("unknowable")), HELD_SPEC).periods

    assert _held(published) == [Decimal("0.0475000000"), Decimal("0.0500000000")]
    assert _held(unknowable) == [Decimal("0.0750000000"), Decimal("0.0500000000")]
    assert unknowable[0].benchmark_unknowable_sessions == (f"{HELD_P}@{D3.isoformat()}",)
    assert unknowable[1].benchmark_unknowable_sessions == ()
    assert published[0].benchmark_unknowable_sessions == ()


def test_a_record_on_the_execution_session_is_not_a_members_crossing() -> None:
    """Bought at D2's open, a member never held D1's close into D2, so a record on D2 is not
    about it -- the book's own rule for a position opened on the disputed session."""
    quotes = held_quotes()
    quotes[D2][HELD_P] = replace(
        quotes[D2][HELD_P], recorded_path="unknowable", path_ratio=JUMP_RATIO
    )

    periods = run_strategy_backtest(held_inputs(quotes), HELD_SPEC).periods

    assert _held(periods) == [Decimal("0.0475000000"), Decimal("0.0500000000")]
    assert all(period.benchmark_unknowable_sessions == () for period in periods)


def test_a_period_no_name_could_be_bought_into_is_refused() -> None:
    everything = frozenset((subject, D2) for subject in HELD_BARS)
    with pytest.raises(StrategyBacktestError, match="equal_weight_all_a_held"):
        run_strategy_backtest(held_inputs(held_quotes(suspended=everything)), HELD_SPEC)


def test_the_held_benchmark_changes_nothing_else_the_book_reports() -> None:
    """The book's periods are the same with and without it; only `benchmark_returns` grows,
    and the all-A equal-weight series beside it is the one the hand ledger above computes."""
    both = HAND_FIXTURE_SPEC.model_copy(
        update={"benchmarks": (CSI500, EQUAL_WEIGHT_ALL_A, EQUAL_WEIGHT_ALL_A_HELD)}
    )
    with_held = run_strategy_backtest(HAND_FIXTURE_INPUTS, both)
    without = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC)

    for held_period, plain in zip(with_held.periods, without.periods, strict=True):
        returns = dict(held_period.benchmark_returns)
        del returns[EQUAL_WEIGHT_ALL_A_HELD]
        assert returns == dict(plain.benchmark_returns)
        assert (held_period.benchmark_members, plain.benchmark_members) == (4, None)
        assert (
            held_period.model_copy(update={"benchmark_returns": returns, "benchmark_members": None})
            == plain
        )


def test_the_held_benchmark_memo_serves_what_a_fresh_run_computes() -> None:
    """A caller-owned memo is keyed by (previous signal day, previous execution session, signal
    day, execution session, period end) -- `None` twice for a period with no previous one --
    filled on the first run and read on the next; the answers equal a run with no memo at all."""
    memo: dict[HeldBenchmarkKey, HeldBenchmarkPeriod] = {}
    fresh = run_strategy_backtest(held_inputs(), HELD_SPEC)
    first = run_strategy_backtest(replace(held_inputs(), held_benchmark=memo), HELD_SPEC)

    assert set(memo) == {(None, None, D1, D2, D4), (D1, D2, D4, D5, D6)}
    assert memo[(None, None, D1, D2, D4)].members == 4
    served = run_strategy_backtest(replace(held_inputs(), held_benchmark=memo), HELD_SPEC)
    assert fresh == first == served
    assert [period.benchmark_members for period in served.periods] == [4, 5]
    # And it is read, not recomputed: a planted value comes back.
    second = (D1, D2, D4, D5, D6)
    memo[second] = replace(memo[second], value=Decimal("0.5000000000"))
    planted = run_strategy_backtest(replace(held_inputs(), held_benchmark=memo), HELD_SPEC)
    assert _held(planted.periods) == [Decimal("0.0475000000"), Decimal("0.5000000000")]


def test_a_memo_hit_is_never_a_value_computed_under_another_previous_period() -> None:
    """Round 2: a period's value depends on the members of the period before it, which the
    notional book sells at its open. Schedule A rebalances on (D1, D4), schedule B on (D1, D3,
    D4): both have the period D4 -> D6 bought at D5, after different periods -- members bought
    at D2 against members bought at D4 -- and, with P gapping up into D5, the two values differ.
    Sharing one memo, each schedule is served the value a fresh run computes. Schedule C,
    (D1, D3), runs first, so B's first period is a memo hit and its second is not: the members
    it sells at D4 are rebuilt from the quotes rather than carried."""
    quotes = held_quotes()
    quotes[D5][HELD_P] = SessionQuote(
        bar=_bar(
            HELD_P,
            D5,
            previous_close=Decimal("11.00"),
            open_=Decimal("12.00"),
            close=Decimal("12.00"),
        ),
        turnover_yuan=Decimal("20000000"),
        adj_factor=Decimal("1"),
    )
    inputs = replace(
        held_inputs(quotes),
        scores=score_rows(
            {
                D1: {HELD_P: 2.0, HELD_U: 1.0},
                D3: {HELD_P: 1.0, HELD_U: 2.0},
                D4: {HELD_P: 1.0, HELD_U: 2.0},
            }
        ),
    )

    def run(
        days: tuple[date, ...], memo: dict[HeldBenchmarkKey, HeldBenchmarkPeriod] | None = None
    ) -> list[Decimal]:
        spec_inputs = replace(inputs, rebalance_days=days, held_benchmark=memo)
        return _held(run_strategy_backtest(spec_inputs, HELD_SPEC).periods)

    fresh_a, fresh_b, fresh_c = run((D1, D4)), run((D1, D3, D4)), run((D1, D3))
    assert fresh_a[-1] != fresh_b[-1]

    memo: dict[HeldBenchmarkKey, HeldBenchmarkPeriod] = {}
    assert run((D1, D3), memo) == fresh_c
    assert run((D1, D3, D4), memo) == fresh_b
    assert run((D1, D4), memo) == fresh_a
    assert run((D1, D3, D4), memo) == fresh_b
    assert {(D1, D2, D4, D5, D6), (D3, D4, D4, D5, D6), (D1, D2, D3, D4, D4)} <= set(memo)


def test_the_reference_period_is_the_run_s_period() -> None:
    """`held_equal_weight_period` computes one period from nothing but the quotes and the two
    periods' sessions; the run carries the previous members instead of rebuilding them, and
    answers the same."""
    quotes = held_quotes()
    periods = run_strategy_backtest(held_inputs(quotes), HELD_SPEC).periods

    first = held_equal_weight_period(quotes, (D1, D2, D3, D4))
    second = held_equal_weight_period(quotes, (D4, D5, D6), previous=(D1, D2, D3, D4))

    assert [first.value, second.value] == _held(periods)
    assert [first.members, second.members] == [4, 5]


def test_a_record_on_the_execution_session_is_observed_by_the_members_sold_there() -> None:
    """P's factor jumps 1.0 -> 1.1 at D5, the session period 1's members are sold at the open
    and period 2's bought. Recorded `published`, the sold P is rescaled exactly as a retained
    holding would be -- it opens where it closed, and the period is the unjumped 0.05.
    Recorded `unknowable`, it is valued by the factor path: its D5 open mark is 11.00 x 1.1
    against 10.00 at entry, so the overnight factor is (1.21 + 1.04 + 0.95 + 1.10) /
    (1.10 + 1.04 + 0.95 + 1.10) = 4.30 / 4.19, and the period is 4.30 / 4.19 x 1.05 - 1 =
    0.0775656325; the crossing is named on period 2. The P bought at D5 enters at 11.00 x 1.1 and
    is not rescaled -- it held nothing overnight."""

    def jumped(recorded: str) -> dict[date, dict[str, SessionQuote]]:
        quotes = held_quotes()
        for day in (D5, D6):
            quotes[day][HELD_P] = replace(
                quotes[day][HELD_P],
                adj_factor=Decimal("1.1"),
                recorded_path=recorded if day == D5 else None,  # type: ignore[arg-type]
                path_ratio=JUMP_RATIO if day == D5 else None,
            )
        return quotes

    published = run_strategy_backtest(held_inputs(jumped("published")), HELD_SPEC).periods
    unknowable = run_strategy_backtest(held_inputs(jumped("unknowable")), HELD_SPEC).periods

    assert _held(published) == [Decimal("0.0475000000"), Decimal("0.0500000000")]
    assert _held(unknowable) == [Decimal("0.0475000000"), Decimal("0.0775656325")]
    assert unknowable[1].benchmark_unknowable_sessions == (f"{HELD_P}@{D5.isoformat()}",)
    assert published[1].benchmark_unknowable_sessions == ()


# --- round 2: the identity -- a frictionless book holding exactly the members earns the benchmark
#
# Three names, two held, a signal every two sessions: periods D1->D3 (bought D2), D3->D5 (D4),
# D5->D6 (D6). Zero costs and slippage, 20,000,000 yuan of turnover (a 200,000 cap), prices that
# divide 100,000 yuan into whole lots, and the score ranks exactly each period's members, so no
# rule of the book binds. Each name the book holds into a rebalance opens that session exactly
# where it was bought (value 100,000.00), so the book is equal weight at every open and fully
# invested with no idle cash -- the one arrangement under which a book that does not resize a
# retained name holds the notional book's weights. Every close in between moves freely.
#
#   X  bought D2 at 10.00 and kept to the end; its factor jumps 1.0 -> 1.1 at D4 with the
#      published path recorded (the session's return is close / pre_close), and it gaps
#      10.40 -> 10.00 into D4 and 10.60 -> 10.00 into D6.
#   Y  bought D2 at 12.50, gaps 11.36 -> 12.50 (its limit-up) into D4, where it is sold and
#      cannot be bought: out of period 2's members. No bar after D4.
#   Z  opens D2 at its limit-up, so it is not a member of period 1; bought D4 at 20.00 after a
#      21.00 -> 20.00 gap, kept, gaps 19.50 -> 20.00 into D6.

IDENTITY_BARS: Final[dict[str, tuple[Bar, ...]]] = {
    HELD_P: (
        ("10.00", "10.00", "10.00"),
        ("10.00", "10.00", "10.20"),
        ("10.20", "10.20", "10.40"),
        ("10.40", "10.00", "10.50"),
        ("10.50", "10.50", "10.60"),
        ("10.60", "10.00", "10.30"),
    ),
    HELD_Q: (
        ("12.50", "12.50", "12.50"),
        ("12.50", "12.50", "12.00"),
        ("12.00", "12.00", "11.36"),
        ("11.36", "12.50", "12.50"),
        None,
        None,
    ),
    HELD_R: (
        ("20.00", "20.00", "20.00"),
        ("20.00", "22.00", "22.00"),
        ("22.00", "22.00", "21.00"),
        ("21.00", "20.00", "19.80"),
        ("19.80", "19.80", "19.50"),
        ("19.50", "20.00", "20.80"),
    ),
}
ZERO_COSTS: Final[CostSchedule] = CostSchedule(
    commission_rate=Decimal("0"),
    minimum_commission=Decimal("0"),
    transfer_fee_rate=Decimal("0"),
    sell_stamp_duty_rate=Decimal("0"),
)
IDENTITY_SPEC: Final[StrategySpec] = StrategySpec(
    rebalance_every_sessions=2,
    holding_count=2,
    buffer_rank=None,
    max_industry_weight=None,
    position_capital=Decimal("100000"),
    participation_cap=Decimal("0.01"),
    costs=ZERO_COSTS,
    slippage_rate=Decimal("0"),
    benchmarks=(EQUAL_WEIGHT_ALL_A_HELD,),
)


def identity_inputs() -> StrategyInputs:
    quotes = held_quotes(IDENTITY_BARS, adj={HELD_P: ("1", "1", "1", "1.1", "1.1", "1.1")})
    quotes[D4][HELD_P] = replace(
        quotes[D4][HELD_P], recorded_path="published", path_ratio=JUMP_RATIO
    )
    ranked = {HELD_P: 2.0, HELD_R: 1.0}
    return StrategyInputs(
        source=RAW_SOURCE,
        sessions=SESSIONS,
        signal_instants={day: signal_instant(day) for day in SESSIONS},
        scores=score_rows({D1: {HELD_P: 2.0, HELD_Q: 1.0}, D3: ranked, D5: ranked}),
        quotes=quotes,
        benchmark_returns={},
    )


def test_a_frictionless_book_holding_exactly_the_members_earns_the_benchmark() -> None:
    """The correctness criterion, by hand and by the book.

    PERIOD 1 (D1 close -> D3 close): cash at D1's close, X and Y bought at D2's open. The book
    is 200,000.00 -> 104,000.00 + 90,880.00 = 194,880.00: -0.0256.
    PERIOD 2 (D3 -> D5): at D4's open Y is sold at 12.50 (100,000.00) and Z bought; X is kept.
    D5: X 10,000 x 10.60 = 106,000.00 and Z 5,000 x 19.50 = 97,500.00, so 203,500 / 194,880 - 1
    = 0.0442323481. The notional book: the members of period 1 open D4 at 1.00 and 1.00 of
    their entry and closed D3 at 1.04 and 0.9088, so the overnight factor is 2 / 1.9488; the new
    members earn (1.06 + 0.975) / 2 -- the same number.
    PERIOD 3 (D5 -> D6): nothing trades; 207,000 / 203,500 - 1 = 0.0171990172.

    Exact on the quantized ten-place returns. The book rounds each holding to the cent and the
    benchmark does not, and the two divide in a different order at 28 significant digits, so
    they are the same rational number computed two ways: equal after quantizing unless that
    number lies within about 1e-27 of a rounding midpoint, and here every holding is an exact
    number of cents. The overnight factor is the value-weighted one -- what the notional book,
    holding its members at their marks, is worth at the open over what it was worth at the
    close; a plain mean of each member's open / last mark would give 0.0489868296 and
    0.0189767779, and no book earns those."""
    result = run_strategy_backtest(identity_inputs(), IDENTITY_SPEC)

    gross = [period.gross_return for period in result.periods]
    assert gross == [
        Decimal("-0.0256000000"),
        Decimal("0.0442323481"),
        Decimal("0.0171990172"),
    ]
    assert _held(result.periods) == gross
    assert all(period.cost == 0 and not period.rejections for period in result.periods)
    assert [period.capped_orders for period in result.periods] == [0, 0, 0]
    assert [period.holdings for period in result.periods] == [
        (HELD_P, HELD_Q),
        (HELD_P, HELD_R),
        (HELD_P, HELD_R),
    ]
    assert [p.benchmark_members for p in result.periods] == [2, 2, 2]
    assert result.periods[1].end_value - result.periods[1].start_value != 0
    assert all(period.benchmark_unknowable_sessions == () for period in result.periods)
