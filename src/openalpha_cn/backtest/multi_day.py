"""Deterministic multi-day portfolio backtest and exposure attribution.

## One step is one trading session holding the whole book (`V2-P5-003`)

`PortfolioBacktestStep` carried one `order` and one `market` until this row, and a K-name book
therefore cost K steps. That was not only verbose; it was wrong in three measurable ways, all
three reproduced on the pre-`V2-P5-003` runner with a two-name book:

1. **The equity curve was not a daily series.** Two names bought on 2026-07-24 produced two
   `EquityPoint`s both dated `2026-07-24`, the first of them mid-session, after one order and
   before the other. A consumer plotting the curve saw a sawtooth on repeated dates.
2. **A book could not be told what a name it holds closed at unless it traded that name.** The
   only way in for a price was to attach a bar to an *order*, so a name held and not traded kept
   yesterday's `PositionMark` forever. Measured: a book of 1,100 AAA and 1,000 BBB with BBB
   closing at 20.00 reported a market value of `21000.00` against a true `31000`.
3. **The risk clamps were therefore checked against stale prices.** `max_gross_exposure` read
   `0.210032` on that same book where the truth was `~0.31`, and because `_buy` compares
   `after.market_value / after.equity` against `max_total_exposure`, a buy that breaches the cap
   on today's prices was admitted on yesterday's.

So a step now carries the session's date, the session's bars, and the orders placed into them.
The runner **marks the book to the session's closes before it executes that session's orders**,
which is what makes (3) go away: the cap sees today's prices. `tests/unit/backtest/
test_multi_day.py::test_the_days_re_mark_lands_before_the_days_orders_so_the_cap_sees_todays
_price` is that ordering as two different answers on one fixture -- refused when marked first,
filled when marked afterwards.

**A held name with no bar on the session keeps its mark, and is named for keeping it.** An
A-share halt serves no daily row, so a missing bar is a real shape and refusing it would make a
halt unrepresentable; what is refused is carrying the stale price *quietly*. Every carry lands
in `PortfolioBacktestReport.carried_marks` with the session and the number of consecutive
sessions it has now been carried, because one session stale is a halt and forty is a corpse.

**A bar for a name the book neither holds nor trades is ignored, deliberately.** A caller handing
the session its whole universe is the expected shape, and marking a name into a book that does
not hold it would put a `PositionMark` on the state for a position that is not there -- which
`PortfolioState.validate_identity_and_marks` does not forbid but `_sell` is careful to avoid.

## What this step still cannot supply, and therefore still does not enforce

`PortfolioLimits.max_industry_weight` remains what `V2-P5-002` measured it to be: a **named
refusal**, not a cap this runner quietly holds. The session's inputs are `MarketBar`s and
`MarketBar` carries no industry, so nothing here can classify a name -- which is exactly the
structural reason `LIMITS_ENFORCED_BY_THE_SIMULATOR` omits it. Holding a book rather than one
name does not change that: K bars carry no more industry than one does. `turnover_budget` is
likewise unmoved; it is the construction policy's, because a budget is a property of a plan and
this runner executes instructions that already exist.
"""

from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioSimulator,
    PortfolioState,
    PortfolioTransition,
)
from openalpha_cn.domain.portfolio import PositionMark

_SIX = Decimal("0.000001")
_CENT = Decimal("0.01")


class PortfolioLedger(Protocol):
    """Extension contract for durable portfolio-transition storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives on the
    consumer side (`backtest/`), not in `storage/`. `PortfolioBacktestRunner` is a pure
    algorithmic module and only ever calls `append` on its optional ledger -- it never
    reads transitions back, so `SQLitePortfolioLedger.get`/`list` (used elsewhere, by
    `sdk.py` and `api/app.py` directly on the concrete store) are deliberately not part
    of this Protocol.
    """

    def append(self, transition: PortfolioTransition) -> None:
        """Append idempotently or reject conflicting reuse of an order ID."""


class PortfolioBacktestStep(BaseModel):
    """One trading session: its date, its bars, the orders placed into them, one benchmark close.

    Every field is about the *session* rather than about one name, which is the whole of
    `V2-P5-003`. Two shapes the single-subject step could not refuse become unrepresentable
    here: K contradictory benchmark closes for one date (there is one benchmark per session
    because there is one session per step), and K independently dated bars nothing held together
    (the step owns the date and every bar must agree with it).

    `orders` may be empty. A session on which the book holds and trades nothing is a real
    session, it moves equity, and the old model had no way to express it -- the only way to hand
    the runner a day was to trade on it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_date: date
    bars: tuple[MarketBar, ...] = Field(min_length=1)
    orders: tuple[PortfolioOrder, ...] = ()
    benchmark_close: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_session(self) -> Self:
        if any(item.trade_date != self.trade_date for item in self.bars):
            raise ValueError("backtest bars must all fall on the step's trade date")
        subjects = tuple(item.subject for item in self.bars)
        if len(subjects) != len(set(subjects)):
            raise ValueError("backtest bars must have unique subjects")
        order_ids = tuple(order.order_id for order in self.orders)
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("backtest orders must have unique order IDs within a session")
        unpriced = sorted({order.subject for order in self.orders} - set(subjects))
        if unpriced:
            raise ValueError(f"ordered subjects have no bar on this session: {unpriced}")
        return self

    def prices(self) -> dict[str, MarketBar]:
        """This session's bars keyed by subject; `validate_session` proved the keys unique.

        A mapping rather than a `bar(subject)` scan because the runner needs two different
        lookups per session -- every held name, and every ordered name -- and because
        `validate_session` has already established that an ordered subject is present, which
        makes `prices()[order.subject]` a total lookup rather than an `Optional` the caller has
        to re-prove with an `assert` the interpreter drops under `-O`.
        """
        return {item.subject: item for item in self.bars}


class EquityPoint(BaseModel):
    """Portfolio and benchmark values at the close of one session.

    One per step since `V2-P5-003`, and therefore one per date. It used to be one per
    *transition*, so a K-name session produced K points sharing a date, K-1 of them mid-session.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_date: str
    equity: Decimal
    benchmark_close: Decimal
    gross_exposure: Decimal


class CarriedMark(BaseModel):
    """One session on which a held name had no bar and kept the price it already had.

    Reported rather than raised because an A-share halt serves no daily row: refusing the
    session would make a halted holding unrepresentable, and marking it to nothing would be
    worse. `sessions_carried` counts consecutive sessions within this run, so a reader can tell
    a one-day halt from a name whose price stopped arriving.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_date: str
    subject: str
    price: Decimal
    sessions_carried: int = Field(gt=0)


class SubjectAttribution(BaseModel):
    """Realized PnL attributed to one traded subject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    pnl: Decimal


class PortfolioBacktestReport(BaseModel):
    """Reconciled multi-day portfolio performance report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_state: PortfolioState
    final_state: PortfolioState
    transitions: tuple[PortfolioTransition, ...]
    equity_curve: tuple[EquityPoint, ...]
    total_return: Decimal
    benchmark_return: Decimal
    active_return: Decimal
    turnover: Decimal
    max_order_notional: Decimal
    max_gross_exposure: Decimal
    attribution: tuple[SubjectAttribution, ...]
    carried_marks: tuple[CarriedMark, ...] = ()


class PortfolioBacktestRunner:
    """Execute ordered daily sessions through the production portfolio core."""

    def __init__(
        self,
        *,
        limits: PortfolioLimits | None = None,
        ledger: PortfolioLedger | None = None,
    ) -> None:
        self.simulator = PortfolioSimulator(limits=limits)
        self.ledger = ledger

    def run(
        self,
        *,
        initial: PortfolioState,
        steps: tuple[PortfolioBacktestStep, ...],
    ) -> PortfolioBacktestReport:
        """Advance `initial` through every session and reconcile the whole run.

        Each session is marked to its own closes and *then* traded, so a cap is judged on the
        prices of the day the order was placed. See this module's docstring for the measurement
        that ordering answers.
        """
        self._validate_series(initial=initial, steps=steps)
        state = initial
        transitions: list[PortfolioTransition] = []
        points: list[EquityPoint] = []
        carried: list[CarriedMark] = []
        carry_streak: Counter[str] = Counter()
        pnl: dict[str, Decimal] = {}
        turnover_notional = Decimal(0)
        max_order_notional = Decimal(0)
        max_exposure = Decimal(0)
        for step in steps:
            prices = step.prices()
            state = self._mark_to_market(
                state=state,
                step=step,
                prices=prices,
                carried=carried,
                carry_streak=carry_streak,
            )
            for order in step.orders:
                transition = self.simulator.execute_order(
                    state=state,
                    order=order,
                    market=prices[order.subject],
                )
                transitions.append(transition)
                if self.ledger is not None:
                    self.ledger.append(transition)
                state = transition.after
                if transition.execution is not None and transition.status == "filled":
                    turnover_notional += transition.execution.notional
                    max_order_notional = max(max_order_notional, transition.execution.notional)
                    pnl[order.subject] = (
                        pnl.get(order.subject, Decimal(0)) + transition.realized_pnl_delta
                    )
            exposure = Decimal(0) if state.equity == 0 else state.market_value / state.equity
            max_exposure = max(max_exposure, exposure)
            points.append(
                EquityPoint(
                    trade_date=step.trade_date.isoformat(),
                    equity=state.equity,
                    benchmark_close=step.benchmark_close,
                    gross_exposure=exposure.quantize(_SIX),
                )
            )
        total_return = (state.equity / initial.equity - 1).quantize(_SIX)
        benchmark_return = (steps[-1].benchmark_close / steps[0].benchmark_close - 1).quantize(_SIX)
        average_equity = (initial.equity + state.equity) / 2
        turnover = (
            Decimal(0)
            if average_equity == 0
            else (turnover_notional / average_equity).quantize(_SIX)
        )
        return PortfolioBacktestReport(
            initial_state=initial,
            final_state=state,
            transitions=tuple(transitions),
            equity_curve=tuple(points),
            total_return=total_return,
            benchmark_return=benchmark_return,
            active_return=(total_return - benchmark_return).quantize(_SIX),
            turnover=turnover,
            max_order_notional=max_order_notional.quantize(_CENT, rounding=ROUND_HALF_UP),
            max_gross_exposure=max_exposure.quantize(_SIX),
            attribution=tuple(
                SubjectAttribution(subject=subject, pnl=pnl[subject].quantize(_CENT))
                for subject in sorted(pnl)
            ),
            carried_marks=tuple(carried),
        )

    @staticmethod
    def _validate_series(
        *,
        initial: PortfolioState,
        steps: tuple[PortfolioBacktestStep, ...],
    ) -> None:
        """Refuse a mis-ordered series up front instead of one order at a time.

        The single-subject runner had no series to check, so a backwards day arrived at
        `PortfolioSimulator.execute_order`, which rejected each of its orders with `market date
        precedes portfolio state` -- leaving a report whose equity curve ran backwards in time
        while every transition on those dates read `rejected`. That is a malformed input
        reported as a risk verdict, and the two are not the same answer.

        **Strictly ascending, so two sessions cannot share a date**, which is the defect the
        single-subject step had by construction and would otherwise have re-entered by the back
        door. The *first* session may land on `initial.as_of` itself: a book funded on Monday
        may trade on Monday. `backtest/paper.py` is stricter and requires every session to move
        the book forward, because re-advancing a paper book through a session it has lived would
        double every fill on it.

        **A zero-equity opening book is refused by name.** `total_return` divides by
        `initial.equity`, and until this row a book opened with no cash and no positions --
        constructible, since `PortfolioState.cash` is only `ge=0` -- reached that division and
        came back as `decimal.DivisionByZero` from inside a report builder. The refusal is what
        makes the two remaining zero-guards below (`state.equity == 0` for exposure and
        `average_equity == 0` for turnover) defensive rather than load-bearing: equity cannot
        fall to exactly zero from a positive opening, because every mark is `gt=0` and every
        fill price is positive, so a position always carries value and cash never goes negative.

        `V2-P5-003`'s mutation sweep over this module and `paper.py` ran **66 mutants, 64
        killed**, and the two survivors are both the turnover guard -- its fallback `Decimal(0)`
        and its `== 0`. Both are **equivalent, measured rather than labelled**: the fallback is
        returned only when `average_equity` is zero, which the refusal above makes unreachable;
        and flipping the comparison to `== 1` needs a book whose opening and closing equity
        average exactly one *and* which filled something, where the smallest fillable order is
        a hundred-share board lot costing more than a book worth one yuan has. The exposure
        guard's own comparison is not in that list because it *is* separable, on a book worth
        exactly one yuan that holds a position -- see
        `test_a_fully_invested_book_reports_an_exposure_of_one[a-book-worth-exactly-one]`.
        """
        if not steps:
            raise ValueError("multi-day backtest requires at least one step")
        dates = [step.trade_date for step in steps]
        if any(later <= earlier for earlier, later in pairwise(dates)):
            raise ValueError("multi-day backtest sessions must be strictly ascending by date")
        if dates[0] < initial.as_of:
            raise ValueError("first backtest session precedes the initial portfolio state")
        if initial.equity == 0:
            raise ValueError("multi-day backtest requires an opening book with equity")

    @staticmethod
    def _mark_to_market(
        *,
        state: PortfolioState,
        step: PortfolioBacktestStep,
        prices: dict[str, MarketBar],
        carried: list[CarriedMark],
        carry_streak: Counter[str],
    ) -> PortfolioState:
        """Re-price every held name at this session's close before the session trades.

        Only *held* names are marked: a bar for a name the book does not hold is this session's
        universe passing through, and writing a `PositionMark` for it would leave the state
        carrying valuation marks for positions that do not exist -- the exact residue
        `PortfolioSimulator._sell` pops when a position closes.
        """
        marks = {mark.subject: mark.price for mark in state.marks}
        for position in state.positions:
            bar = prices.get(position.subject)
            if bar is None:
                carry_streak[position.subject] += 1
                carried.append(
                    CarriedMark(
                        trade_date=step.trade_date.isoformat(),
                        subject=position.subject,
                        price=marks[position.subject],
                        sessions_carried=carry_streak[position.subject],
                    )
                )
                continue
            carry_streak.pop(position.subject, None)
            marks[position.subject] = bar.close
        return PortfolioState(
            as_of=step.trade_date,
            cash=state.cash,
            positions=state.positions,
            marks=tuple(
                PositionMark(subject=subject, price=marks[subject]) for subject in sorted(marks)
            ),
            realized_pnl=state.realized_pnl,
            fees_paid=state.fees_paid,
        )
