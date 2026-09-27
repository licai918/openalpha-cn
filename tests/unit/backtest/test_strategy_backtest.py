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

from openalpha_cn.backtest.execution import CostSchedule, MarketBar
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A,
    KNOWN_STRATEGY_BACKTEST_LIMITATIONS,
    STRATEGY_BACKTEST_LIMITATION_CODES,
    PeriodResult,
    ScoreRow,
    ScoreSource,
    SessionQuote,
    StrategyBacktestError,
    StrategyInputs,
    StrategySpec,
    run_strategy_backtest,
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
                turnover_yuan=turnover[subject],
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
    result = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC)

    assert result.limitations == tuple(item.code for item in KNOWN_STRATEGY_BACKTEST_LIMITATIONS)
    assert (
        set(result.limitations)
        == STRATEGY_BACKTEST_LIMITATION_CODES
        == {
            "fills_are_at_the_open_print_judged_as_a_one_price_bar",
            "slippage_is_a_flat_rate_and_not_a_market_impact_model",
            "the_participation_cap_reads_the_signal_sessions_turnover",
            "a_rejected_buy_leaves_its_slot_in_cash_until_the_next_rebalance",
            "a_retained_position_is_not_resized_to_equal_weight",
            "the_exit_leg_is_priced_on_the_entry_share_count",
            "dividends_are_reinvested_through_the_adjustment_factor",
            "the_industry_cap_counts_names_and_is_applied_at_the_signal",
            "a_holding_that_cannot_trade_is_marked_at_its_last_close",
            "the_last_period_may_be_shorter_than_the_rebalance_interval",
            "an_intraday_halt_makes_the_whole_session_untradeable_at_the_open",
            "the_equal_weight_benchmark_is_every_priced_name_and_is_not_investable",
            "a_passing_backtest_is_not_evidence_that_a_signal_is_real",
        }
    )
