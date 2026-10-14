"""The paper ledger's costs: the research protocol's, injected, and the default left alone
(`V2-P7-017`).

`PaperPortfolio` charged the default `CostSchedule()` and nothing could change it, so a 30,000-yuan
buy paid 9.30 where the protocol (`docs/research/p6-protocol.md` section 3) charges 37.50 -- T5's
measurement (`docs/research/p7-step0-validation/T5-paper-step-builder.md`, prototype section 6).
The fix is a non-breaking `execution` argument, with the protocol's slippage folded into
`transfer_fee_rate` because the protocol charges no transfer fee and `CostSchedule` is embedded in
the registered `StrategySpec`, so it may not grow a field.

What "equal to the P6 book" means here is measured, not restated: the same trades run through the
real P6 book (`run_strategy_backtest`) and through the paper book on the same bars, and the fees
are compared fill by fill, to the cent, against each other and against the hand arithmetic in the
comments. Every fixture is invented and on the Shanghai main board; `q(...)` below is "quantize to
the fen, half up", which is how `AShareExecutionPolicy` rounds every cost component separately.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

import pytest

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    CostSchedule,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
    at_the_open,
)
from openalpha_cn.backtest.multi_day import PortfolioBacktestRunner, PortfolioBacktestStep
from openalpha_cn.backtest.paper import (
    PaperPortfolio,
    PaperPortfolioReachedOutward,
    PaperSessionResult,
    slippage_folded_execution,
)
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioState,
    PortfolioTransition,
)
from openalpha_cn.backtest.strategy_backtest import (
    ScoreRow,
    ScoreSource,
    SessionQuote,
    StrategyBacktest,
    StrategyInputs,
    StrategySpec,
    component_key,
    run_strategy_backtest,
)
from openalpha_cn.strategy_view import PROTOCOL_COSTS, PROTOCOL_SLIPPAGE_RATE

D = Decimal
X, Y = "600010.SH", "600011.SH"
T1, T2, T3 = date(2021, 3, 1), date(2021, 3, 2), date(2021, 3, 3)
SESSIONS: Final[tuple[date, ...]] = (T1, T2, T3)
CSI500: Final[str] = "000905.SH"
FACTOR: Final[str] = "toy/v1"
SHANGHAI: Final[timezone] = timezone(timedelta(hours=8))
TURNOVER: Final[Decimal] = D("100000000")
"""Every session's traded value: 1% of it is 1,000,000, so the P6 participation cap never binds."""
FULL = PortfolioLimits(max_position_weight=D(1), max_total_exposure=D(1), min_cash_weight=D(0))
"""The paper book's limits (T5 section 6): with them opened, cash is the only constraint a buy meets
besides the market -- the setting under which the P6 book and the paper book trade alike."""

PROTOCOL_PAPER: Final[AShareExecutionPolicy] = slippage_folded_execution(
    PROTOCOL_COSTS, slippage_rate=PROTOCOL_SLIPPAGE_RATE
)
"""The protocol's costs with its 10bp slippage in the transfer-fee slot, as T5 section 6 folds it:
commission 2.5bp with a 5.00 floor, transfer fee := slippage 0.001, stamp duty 0.5 per mille on
sells -- pinned rate by rate in
`test_the_fold_puts_the_slippage_in_the_transfer_fee_and_keeps_every_other_rate`."""


def _band(previous_close: Decimal) -> tuple[Decimal, Decimal]:
    """The main board's 10% band, as the exchange publishes it, rounded half up to the fen."""
    cent = D("0.01")
    return (
        (previous_close * D("1.1")).quantize(cent, rounding=ROUND_HALF_UP),
        (previous_close * D("0.9")).quantize(cent, rounding=ROUND_HALF_UP),
    )


def bar(code: str, day: date, previous_close: str, open_: str, close: str) -> MarketBar:
    up, down = _band(D(previous_close))
    return MarketBar(
        subject=code,
        trade_date=day,
        board="main",
        previous_close=D(previous_close),
        open=D(open_),
        high=max(D(open_), D(close)),
        low=min(D(open_), D(close)),
        close=D(close),
        suspended=False,
        is_st=False,
        up_limit=up,
        down_limit=down,
    )


class ListLedger:
    """The smallest `PortfolioLedger`: one `append`, kept in a list."""

    def __init__(self) -> None:
        self.appended: list[PortfolioTransition] = []

    def append(self, transition: PortfolioTransition) -> None:
        self.appended.append(transition)


def one_buy(
    *,
    cash: str,
    quantity: int = 2_000,
    price: str = "15.00",
    execution: AShareExecutionPolicy | None = None,
    pass_execution: bool = True,
) -> PaperSessionResult:
    """One paper session from a cash-only book at T1: buy `quantity` of X at T2's open `price`."""
    session = PortfolioBacktestStep(
        trade_date=T2,
        bars=(at_the_open(bar(X, T2, price, price, price)),),
        orders=(PortfolioOrder(order_id="buy-x@T2", subject=X, side="buy", quantity=quantity),),
        benchmark_close=D("6000.00"),
    )
    book = (
        PaperPortfolio(ledger=ListLedger(), limits=FULL, execution=execution)
        if pass_execution
        else PaperPortfolio(ledger=ListLedger(), limits=FULL)
    )
    return book.advance(
        state=PortfolioState(as_of=T1, cash=D(cash)), session=session, observed_on=T2
    )


def fill_of(result: PaperSessionResult) -> ExecutionResult:
    (transition,) = result.transitions
    assert transition.status == "filled", transition.reason
    assert transition.execution is not None
    return transition.execution


# --- the default is left alone ---------------------------------------------------------------


def test_without_a_policy_a_30000_buy_still_pays_the_default_9_30() -> None:
    """Characterisation of the default, green before this change and after it.

    2,000 x 15.00 = 30,000.00. Default `CostSchedule()`: commission q(30,000 x 0.0003) = 9.00
    (above the 5.00 floor); transfer fee q(30,000 x 0.00001) = 0.30; no stamp duty on a buy.
    Total 9.30 -- T5's number -- and cash 40,000.00 - 30,009.30 = 9,990.70.
    """
    result = one_buy(cash="40000.00", pass_execution=False)
    fill = fill_of(result)

    assert (fill.notional, fill.commission, fill.transfer_fee, fill.stamp_duty) == (
        D("30000.00"),
        D("9.00"),
        D("0.30"),
        D("0.00"),
    )
    assert fill.total_cost == D("9.30")
    assert result.after.cash == D("9990.70")


def test_passing_no_policy_is_the_same_book_as_not_passing_one() -> None:
    """`execution=None` is the default spelled out: the whole session result is identical."""
    assert one_buy(cash="40000.00", execution=None) == one_buy(
        cash="40000.00", pass_execution=False
    )


# --- the protocol's costs, against the P6 book --------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Trade:
    """One fill, by hand: what both books must record for it."""

    day: date
    subject: str
    side: Literal["buy", "sell"]
    quantity: int
    price: Decimal
    notional: Decimal
    commission: Decimal
    stamp_duty: Decimal
    slippage: Decimal
    fees: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class Case:
    """Two names over T1..T3, one held, rebalanced every session.

    `x` is X's `(previous close, open, close)` on T1, T2, T3; Y is 10.00 flat throughout. Scores
    rank X first on T1 and Y first on T2, so the P6 book buys X at T2's open, then sells X and
    buys Y at T3's open -- a buy, a sell, and a second buy, sized by the P6 book from `capital`.
    """

    capital: Decimal
    x: tuple[tuple[str, str, str], tuple[str, str, str], tuple[str, str, str]]
    trades: tuple[Trade, Trade, Trade]
    final_equity: Decimal


CASES: Final[dict[str, Case]] = {
    # T5's trade. Capital 30,100.
    # T2 buy X @15.00: floor(30,100 / 1,500) = 20 lots = 2,000; notional 30,000.00;
    #   commission q(30,000 x 0.00025) = 7.50; slippage q(30,000 x 0.001) = 30.00; fees 37.50;
    #   outlay 30,037.50 <= 30,100. Cash 62.50.
    # T3 sell X 2,000 @17.50: notional 35,000.00; commission q(8.75) = 8.75; stamp duty
    #   q(35,000 x 0.0005) = 17.50; slippage 35.00; fees 61.25. Cash 62.50 + 35,000 - 61.25
    #   = 35,001.25.
    # T3 buy Y @10.00: budget min(30,100, 35,001.25) = 30,100 -> 3,000 shares; notional 30,000.00;
    #   fees 7.50 + 30.00 = 37.50. Cash 34,963.75 - 30,000 = 4,963.75.
    # Equity at T3's close: 4,963.75 + 3,000 x 10.00 = 34,963.75.
    "t5-30000-buy-35000-sell": Case(
        capital=D("30100"),
        x=(("15.00", "15.00", "15.00"), ("15.00", "15.00", "16.00"), ("16.00", "17.50", "17.50")),
        trades=(
            Trade(
                day=T2,
                subject=X,
                side="buy",
                quantity=2_000,
                price=D("15.00"),
                notional=D("30000.00"),
                commission=D("7.50"),
                stamp_duty=D("0.00"),
                slippage=D("30.00"),
                fees=D("37.50"),
            ),
            Trade(
                day=T3,
                subject=X,
                side="sell",
                quantity=2_000,
                price=D("17.50"),
                notional=D("35000.00"),
                commission=D("8.75"),
                stamp_duty=D("17.50"),
                slippage=D("35.00"),
                fees=D("61.25"),
            ),
            Trade(
                day=T3,
                subject=Y,
                side="buy",
                quantity=3_000,
                price=D("10.00"),
                notional=D("30000.00"),
                commission=D("7.50"),
                stamp_duty=D("0.00"),
                slippage=D("30.00"),
                fees=D("37.50"),
            ),
        ),
        final_equity=D("34963.75"),
    ),
    # The P6 book's own half-up commission (its hand ledger's sell of A). Capital 100,000.
    # T2 buy X @10.00: 10,000 shares would cost 100,000 + 25.00 + 100.00 = 100,125 > 100,000, so
    #   9,900: notional 99,000.00; commission 24.75; slippage 99.00; fees 123.75. Cash 876.25.
    # T3 sell X 9,900 @11.00: notional 108,900.00; commission q(27.225) = 27.23 (half up);
    #   stamp duty q(54.45) = 54.45; slippage 108.90; fees 190.58. Cash 876.25 + 108,900
    #   - 190.58 = 109,585.67.
    # T3 buy Y @10.00: budget 100,000 -> 9,900 shares as above, fees 123.75. Cash 10,461.92.
    # Equity at T3's close: 10,461.92 + 9,900 x 10.00 = 109,461.92.
    "commission-rounds-half-up": Case(
        capital=D("100000"),
        x=(("10.00", "10.00", "10.00"), ("10.00", "10.00", "10.50"), ("10.50", "11.00", "11.00")),
        trades=(
            Trade(
                day=T2,
                subject=X,
                side="buy",
                quantity=9_900,
                price=D("10.00"),
                notional=D("99000.00"),
                commission=D("24.75"),
                stamp_duty=D("0.00"),
                slippage=D("99.00"),
                fees=D("123.75"),
            ),
            Trade(
                day=T3,
                subject=X,
                side="sell",
                quantity=9_900,
                price=D("11.00"),
                notional=D("108900.00"),
                commission=D("27.23"),
                stamp_duty=D("54.45"),
                slippage=D("108.90"),
                fees=D("190.58"),
            ),
            Trade(
                day=T3,
                subject=Y,
                side="buy",
                quantity=9_900,
                price=D("10.00"),
                notional=D("99000.00"),
                commission=D("24.75"),
                stamp_duty=D("0.00"),
                slippage=D("99.00"),
                fees=D("123.75"),
            ),
        ),
        final_equity=D("109461.92"),
    ),
    # The commission floor and a slippage that lands exactly on half a fen. Capital 1,300.
    # T2 buy X @12.35: floor(1,300 / 1,235) = 1 lot = 100; notional 1,235.00; commission
    #   q(0.30875) = 0.31 -> the 5.00 floor; slippage 1,235 x 0.001 = 1.235 exactly -> 1.24
    #   (half up); fees 6.24; outlay 1,241.24. Cash 58.76.
    # T3 sell X 100 @12.45: notional 1,245.00; commission q(0.31125) = 0.31 -> 5.00; stamp duty
    #   q(0.6225) = 0.62; slippage 1.245 exactly -> 1.25 (half up); fees 6.87. Cash 58.76
    #   + 1,245.00 - 6.87 = 1,296.89.
    # T3 buy Y @10.00: budget min(1,300, 1,296.89) = 1,296.89 -> 100 shares; notional 1,000.00;
    #   commission q(0.25) -> 5.00; slippage 1.00; fees 6.00. Cash 290.89.
    # Equity at T3's close: 290.89 + 100 x 10.00 = 1,290.89.
    "commission-floor-and-half-fen-slippage": Case(
        capital=D("1300"),
        x=(("12.35", "12.35", "12.35"), ("12.35", "12.35", "12.35"), ("12.35", "12.45", "12.45")),
        trades=(
            Trade(
                day=T2,
                subject=X,
                side="buy",
                quantity=100,
                price=D("12.35"),
                notional=D("1235.00"),
                commission=D("5.00"),
                stamp_duty=D("0.00"),
                slippage=D("1.24"),
                fees=D("6.24"),
            ),
            Trade(
                day=T3,
                subject=X,
                side="sell",
                quantity=100,
                price=D("12.45"),
                notional=D("1245.00"),
                commission=D("5.00"),
                stamp_duty=D("0.62"),
                slippage=D("1.25"),
                fees=D("6.87"),
            ),
            Trade(
                day=T3,
                subject=Y,
                side="buy",
                quantity=100,
                price=D("10.00"),
                notional=D("1000.00"),
                commission=D("5.00"),
                stamp_duty=D("0.00"),
                slippage=D("1.00"),
                fees=D("6.00"),
            ),
        ),
        final_equity=D("1290.89"),
    ),
}


def quotes(case: Case) -> dict[date, dict[str, MarketBar]]:
    return {
        day: {X: bar(X, day, *case.x[index]), Y: bar(Y, day, "10.00", "10.00", "10.00")}
        for index, day in enumerate(SESSIONS)
    }


def run_p6(case: Case) -> StrategyBacktest:
    """The P6 strategy book on the case's bars, under the protocol's costs and slippage."""

    def instant(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 16, 30, tzinfo=SHANGHAI)

    def row(day: date, subject: str, value: float) -> ScoreRow:
        return ScoreRow(
            component=component_key(FACTOR, "raw"),
            subject=subject,
            signal_day=day,
            value=value,
            available_time=instant(day),
            revision_time=instant(day),
        )

    inputs = StrategyInputs(
        source=ScoreSource(components=((FACTOR, "raw", D(1)),), combine="zscore_sum"),
        sessions=SESSIONS,
        signal_instants={day: instant(day) for day in SESSIONS},
        scores=(row(T1, X, 2.0), row(T1, Y, 1.0), row(T2, X, 1.0), row(T2, Y, 2.0)),
        quotes={
            day: {
                code: SessionQuote(bar=item, turnover_yuan=TURNOVER, adj_factor=D(1))
                for code, item in session.items()
            }
            for day, session in quotes(case).items()
        },
        benchmark_returns={CSI500: {day: D(0) for day in SESSIONS}},
    )
    spec = StrategySpec(
        rebalance_every_sessions=1,
        holding_count=1,
        buffer_rank=None,
        max_industry_weight=None,
        position_capital=case.capital,
        participation_cap=D("0.01"),
        costs=PROTOCOL_COSTS,
        slippage_rate=PROTOCOL_SLIPPAGE_RATE,
        benchmarks=(CSI500,),
    )
    return run_strategy_backtest(inputs, spec)


def run_paper(case: Case) -> tuple[list[PaperSessionResult], ListLedger]:
    """The case's hand-written trades through the paper book, at the open, with the protocol's
    costs injected -- sells before buys, as both books order a session."""
    bars = quotes(case)
    ledger = ListLedger()
    book = PaperPortfolio(ledger=ledger, limits=FULL, execution=PROTOCOL_PAPER)
    state = PortfolioState(as_of=T1, cash=case.capital)
    results: list[PaperSessionResult] = []
    for day in (T2, T3):
        result = book.advance(
            state=state,
            session=PortfolioBacktestStep(
                trade_date=day,
                bars=tuple(at_the_open(item) for item in bars[day].values()),
                orders=tuple(
                    PortfolioOrder(
                        order_id=f"{trade.side}-{trade.subject}@{day.isoformat()}",
                        subject=trade.subject,
                        side=trade.side,
                        quantity=trade.quantity,
                    )
                    for trade in case.trades
                    if trade.day == day
                ),
                benchmark_close=D("6000.00"),
            ),
            observed_on=day,
        )
        results.append(result)
        state = result.after
    return results, ledger


def paper_trade(day: str, transition: PortfolioTransition) -> Trade:
    """One paper transition read as a `Trade`; its `transfer_fee` is where the slippage rides."""
    execution = transition.execution
    assert transition.status == "filled", transition.reason
    assert execution is not None
    assert execution.filled_price is not None
    return Trade(
        day=date.fromisoformat(day),
        subject=transition.order.subject,
        side=execution.side,
        quantity=execution.quantity,
        price=execution.filled_price,
        notional=execution.notional,
        commission=execution.commission,
        stamp_duty=execution.stamp_duty,
        slippage=execution.transfer_fee,
        fees=execution.total_cost,
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_protocol_costs_charge_what_the_p6_book_charges_to_the_fen(name: str) -> None:
    """The same three fills through both books: every fee component equal, to the fen.

    The P6 book's fees are `total_cost` under `PROTOCOL_COSTS` (no transfer fee) plus slippage at
    10bp of the notional (`strategy_backtest._record_fill`); the paper book's are `total_cost`
    under the folded schedule, whose transfer fee *is* that slippage. Each component is rounded
    on its own on both sides, so equality holds for every notional and not only these -- the
    three cases are the ordinary trade, a commission rounding half up, and the commission floor
    beside a slippage of exactly half a fen.
    """
    case = CASES[name]
    p6 = run_p6(case)
    p6_fills = [fill for period in p6.periods for fill in period.fills]
    results, ledger = run_paper(case)
    paper = [
        paper_trade(result.trade_date, transition)
        for result in results
        for transition in result.transitions
    ]

    # The P6 book made exactly the trades the case describes, by hand.
    assert [
        Trade(
            day=fill.day,
            subject=fill.subject,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            notional=fill.notional,
            commission=fill.commission,
            stamp_duty=fill.stamp_duty,
            slippage=fill.slippage,
            fees=fill.fees,
        )
        for fill in p6_fills
    ] == list(case.trades)
    assert {fill.transfer_fee for fill in p6_fills} == {D("0.00")}, "the protocol charges none"

    # The paper book filled the same trades and charged the same fees, the slippage riding in the
    # transfer-fee slot -- and so the two books agree with each other fill by fill.
    assert paper == list(case.trades)
    assert [trade.fees for trade in paper] == [fill.fees for fill in p6_fills]

    # So the money agrees too: the book after T3, marked at T3's close (Y opened and closed at
    # 10.00, so the open-fill mark is the close), is the P6 book's last period end value.
    assert results[-1].equity == p6.periods[-1].end_value == case.final_equity
    assert results[-1].after.fees_paid == sum((trade.fees for trade in case.trades), D(0))
    assert len(ledger.appended) == 3


# --- what the injection leaves as it was -----------------------------------------------------


def test_the_cash_check_reads_the_injected_fees() -> None:
    """A buy is refused whole when its notional plus *its* fees exceed the cash -- the protocol's
    fees under injection, which is what makes the paper book run out of cash where P6 does.

    2,000 x 15.00 = 30,000.00 needs 30,000.00 + 37.50 = 30,037.50 under the protocol: it fills
    on exactly 30,037.50 and is refused on 30,037.49. Under the default it needs only 30,009.30,
    so the same 30,037.49 fills and leaves 28.19.
    """
    on_the_fen = one_buy(cash="30037.50", execution=PROTOCOL_PAPER)
    assert fill_of(on_the_fen).total_cost == D("37.50")
    assert on_the_fen.after.cash == D("0.00")

    short = one_buy(cash="30037.49", execution=PROTOCOL_PAPER)
    (refused,) = short.transitions
    assert (refused.status, refused.reason) == ("rejected", "insufficient cash")
    assert short.after == short.before.model_copy(update={"as_of": T2})

    default = one_buy(cash="30037.49", pass_execution=False)
    assert fill_of(default).total_cost == D("9.30")
    assert default.after.cash == D("28.19")


def test_the_board_lot_still_refuses_an_odd_lot_under_the_protocol_costs() -> None:
    """The lot rule belongs to the policy's rules, not its costs, and injection keeps them."""
    result = one_buy(cash="40000.00", quantity=150, execution=PROTOCOL_PAPER)
    (refused,) = result.transitions

    assert (refused.status, refused.reason) == (
        "rejected",
        "main-board buy quantity must be a multiple of 100",
    )
    assert result.after.cash == D("40000.00")


class BrokerReachingPolicy(AShareExecutionPolicy):
    """An injected policy that opens a socket while it executes.

    The policy is now caller-supplied, as the ledger always was, so it is a second object that runs
    inside `advance` and that this module cannot vet.
    """

    def __init__(self) -> None:
        super().__init__(PROTOCOL_PAPER.costs)
        self.attempts = 0

    def execute(self, request: ExecutionRequest, market: MarketBar) -> ExecutionResult:
        self.attempts += 1
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        return super().execute(request, market)


def test_an_injected_policy_that_reaches_outward_is_refused_while_the_session_runs() -> None:
    policy = BrokerReachingPolicy()

    with pytest.raises(PaperPortfolioReachedOutward, match=r"socket\.__new__"):
        one_buy(cash="40000.00", execution=policy)

    assert policy.attempts == 1, "the policy was reached, and refused inside its own execute"


def test_the_multi_day_runner_takes_the_same_policy() -> None:
    """`PaperPortfolio` passes the policy through `PortfolioBacktestRunner`, which takes it on its
    own too; left out, the runner charges the default exactly as before."""
    session = PortfolioBacktestStep(
        trade_date=T2,
        bars=(at_the_open(bar(X, T2, "15.00", "15.00", "15.00")),),
        orders=(PortfolioOrder(order_id="buy-x@T2", subject=X, side="buy", quantity=2_000),),
        benchmark_close=D("6000.00"),
    )
    opening = PortfolioState(as_of=T1, cash=D("40000.00"))

    def fees(runner: PortfolioBacktestRunner) -> Decimal:
        (transition,) = runner.run(initial=opening, steps=(session,)).transitions
        assert transition.execution is not None
        return transition.execution.total_cost

    assert fees(PortfolioBacktestRunner(limits=FULL, execution=PROTOCOL_PAPER)) == D("37.50")
    assert fees(PortfolioBacktestRunner(limits=FULL)) == D("9.30")


# --- the fold, made in one place -------------------------------------------------------------


def test_the_fold_puts_the_slippage_in_the_transfer_fee_and_keeps_every_other_rate() -> None:
    """T5 section 6's paper schedule: `PROTOCOL_COSTS` with `transfer_fee_rate` := 10bp."""
    folded = slippage_folded_execution(PROTOCOL_COSTS, slippage_rate=PROTOCOL_SLIPPAGE_RATE)

    assert folded.costs == CostSchedule(
        commission_rate=D("0.00025"),
        minimum_commission=D("5.00"),
        transfer_fee_rate=D("0.001"),
        sell_stamp_duty_rate=D("0.0005"),
    )
    assert PROTOCOL_COSTS.transfer_fee_rate == D("0"), "the protocol charges no transfer fee"


def test_the_fold_refuses_a_schedule_that_already_charges_a_transfer_fee() -> None:
    """Folding is exact only into an empty slot, so a schedule with a transfer fee is refused.

    The P6 book rounds the transfer fee and the slippage separately; one folded rate rounds their
    sum once. Selling 100 at 12.3456 is a notional of 1,234.56. Transfer 0.1bp and slippage
    10bp, rounded apart as the P6 book does: q(0.0123456) + q(1.23456) = 0.01 + 1.23 = 1.24.
    Folded into one rate of 0.00101: q(1.2469056) = 1.25 -- a fen the P6 book never charged.
    """
    with pytest.raises(ValueError, match="transfer fee"):
        slippage_folded_execution(CostSchedule(), slippage_rate=PROTOCOL_SLIPPAGE_RATE)

    sell = ExecutionRequest(side="sell", quantity=100)
    market = at_the_open(bar(X, T2, "12.35", "12.3456", "12.3456"))
    apart = AShareExecutionPolicy(CostSchedule()).execute(sell, market)
    assert apart.notional == D("1234.56")
    slippage = (apart.notional * PROTOCOL_SLIPPAGE_RATE).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    assert apart.transfer_fee + slippage == D("1.24")
    once = AShareExecutionPolicy(CostSchedule(transfer_fee_rate=D("0.00101"))).execute(sell, market)
    assert once.transfer_fee == D("1.25")
