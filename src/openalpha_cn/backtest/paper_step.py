"""Planned orders and one observed session, made into a paper-book step (`V2-P7-017`).

`PaperPortfolio.advance` runs one `PortfolioBacktestStep` through the same simulator a backtest
uses, and T5 (`docs/research/p7-step0-validation/T5-paper-step-builder.md`) measured four ways
in which handing it the survivor book's orders and the session's stored bars *as they are* gives
the wrong book. This module is the thin, pure layer between the two, and each rule below is one
of those four:

1. **Fills are at the open.** The execution policy fills at a bar's close, so every *ordered*
   name's bar is collapsed to its open print with `execution.at_the_open` -- the one copy the P6
   book uses too. Left whole, a buy at a limit-up open that trades off the limit later *fills*
   at the close (T5 prototype 3(a)). A name only held keeps its whole bar, so the runner marks
   it at the close.
2. **An order with no bar is not attempted.** A whole-day halt serves no daily row (T5 counted
   99.1% of the research window's main-board and ChiNext halt rows without one), and an order
   for a name with no bar makes the step itself unconstructible (`multi_day.py`,
   `validate_session`). Such an order is taken out of the step and recorded here as
   `not_attempted`, with the P6 book's reason (`NO_BAR_REASON`); the ledger never sees it.
   Everything else -- limit-up, limit-down, a halt with a row, too little cash -- is
   `submitted`, and the paper book refuses it whole and writes the refusal down (T5 section 4).
   The builder does not pre-judge any of those.
3. **One id per attempt.** The ledger records rejected transitions too and refuses an id reused
   with a different payload, so an order carried to a second session under its planned id
   fails that whole session (T5 prototype 3(c)). Each attempt is `"{order_id}@{session}"`
   (`attempt_id`): a suffix rather than a new content address, so no new minting point. The
   attempts this plan returns map each attempt id back to its `TradeOrder`.
4. **Adjustment events are scaled away.** The panel carries `adj_factor` and no record of what
   moved it, so a cash dividend cannot be told from a bonus issue. The one usable convention is
   the P6 book's: every price of a held name's bar -- previous close, open, high, low, close and
   the published band -- is multiplied by `adjustment_ratio[code] = adj_factor(session) /
   adj_factor(entry session)`, so the ledger keeps the shares and costs it bought with and the
   prices meet it in those units. Unscaled, a 1.15 event marks the holding down by
   1 - 1 / 1.15 = 13% on a session nobody lost anything. **Disclosed cost:** the ledger's share
   count is the count at purchase, not what an account holds after a bonus issue; the notional,
   fees and cash agree with the account, and anything that shows shares to a person converts.

Order of execution: **sells first** (by code), so a sale's cash is there for the session's buys;
then **buys, the earliest-planned first** (by `signal_session`, the caller's order within one
session -- the draw order). `build_trade_list` sized today's buys after reserving cash for the
buys still live from earlier sessions, so those are the ones the cash was kept for.
`advance_orders` lists live buys before live sells; the order it hands back does not matter here.

What the caller owes, because a pure function cannot check it: `orders` are the orders due on
`session` (the caller maps `execute_session` ordinals to dates); `bars` is every bar of the
session it has (the runner ignores names it neither holds nor trades); and `adjustment_ratio`
names every held name whose factor has moved since it was bought.

`remark_at_close` finishes the session: the runner leaves an ordered name marked at the open it
was judged at, and the day ends at the close.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from openalpha_cn.backtest.execution import MarketBar, at_the_open
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep
from openalpha_cn.backtest.portfolio import PortfolioOrder, PortfolioState
from openalpha_cn.domain.portfolio import PositionMark
from openalpha_cn.domain.survivor_orders import TradeOrder

__all__ = [
    "NO_BAR_REASON",
    "AttemptedOrder",
    "PaperStepPlan",
    "attempt_id",
    "build_paper_step",
    "remark_at_close",
]

NO_BAR_REASON: Final = "no bar on the execution session"
"""Why an order was not attempted: the P6 book's words for the same event
(`strategy_backtest._sell`/`_buy`), so the two books read alike."""

_ONE: Final = Decimal(1)
_SCALED_FIELDS: Final = (
    "previous_close",
    "open",
    "high",
    "low",
    "close",
    "up_limit",
    "down_limit",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptedOrder:
    """One planned order on one session: submitted to the paper book, or not attempted."""

    order: TradeOrder
    attempt_id: str
    outcome: Literal["submitted", "not_attempted"]
    reason: str | None = None

    def __post_init__(self) -> None:
        if (self.outcome == "not_attempted") != (self.reason is not None):
            raise ValueError(
                "an order not attempted carries the reason it was not, and a submitted one "
                "carries none -- the paper book's transition holds the verdict"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperStepPlan:
    """The step `PaperPortfolio.advance` runs, and every order's attempt, in execution order."""

    step: PortfolioBacktestStep
    attempts: tuple[AttemptedOrder, ...]


def attempt_id(order: TradeOrder, session: date) -> str:
    """The id the paper book records `order` under when it is tried on `session`."""
    return f"{order.order_id}@{session.isoformat()}"


def build_paper_step(
    *,
    session: date,
    orders: Sequence[TradeOrder],
    bars: Mapping[str, MarketBar],
    adjustment_ratio: Mapping[str, Decimal],
    benchmark_close: Decimal,
) -> PaperStepPlan:
    """The step for `session` and the attempt of every order in `orders`; see the module doc.

    `bars` must all be dated `session` and carry the exchange's published band;
    `benchmark_close` goes into the step unchanged (the paper book does not keep it).
    """
    _check_bars(session, bars)
    ratios = _checked_ratios(adjustment_ratio)
    planned = _in_execution_order(orders)
    rebased = sorted(
        {order.ts_code for order in planned if order.side == "buy"}
        & {code for code, ratio in ratios.items() if ratio != _ONE}
    )
    if rebased:
        raise ValueError(
            f"buys for names whose adjustment ratio is not 1: {rebased}; a holding has one "
            "entry factor, and a buy is at ratio 1 on the session it fills"
        )
    attempts: list[AttemptedOrder] = []
    submitted: list[PortfolioOrder] = []
    for order in planned:
        attempt = attempt_id(order, session)
        if order.ts_code not in bars:
            attempts.append(
                AttemptedOrder(
                    order=order,
                    attempt_id=attempt,
                    outcome="not_attempted",
                    reason=NO_BAR_REASON,
                )
            )
            continue
        attempts.append(AttemptedOrder(order=order, attempt_id=attempt, outcome="submitted"))
        submitted.append(
            PortfolioOrder(
                order_id=attempt,
                subject=order.ts_code,
                side=order.side,
                quantity=order.quantity,
            )
        )
    traded = {order.subject for order in submitted}
    step_bars = []
    for code in sorted(bars):
        scaled = _scaled(bars[code], ratios.get(code, _ONE))
        step_bars.append(at_the_open(scaled) if code in traded else scaled)
    return PaperStepPlan(
        step=PortfolioBacktestStep(
            trade_date=session,
            bars=tuple(step_bars),
            orders=tuple(submitted),
            benchmark_close=benchmark_close,
        ),
        attempts=tuple(attempts),
    )


def remark_at_close(
    state: PortfolioState,
    *,
    bars: Mapping[str, MarketBar],
    adjustment_ratio: Mapping[str, Decimal],
) -> PortfolioState:
    """`state` with every held name that has a bar marked at its close, in the ledger's units.

    Pass the `bars` and `adjustment_ratio` the session's step was built from. A held name with
    no bar keeps its mark; cash, positions, fees and realized profit are untouched.
    """
    _check_bars(state.as_of, bars)
    ratios = _checked_ratios(adjustment_ratio)
    marks = {mark.subject: mark.price for mark in state.marks}
    for position in state.positions:
        bar = bars.get(position.subject)
        if bar is not None:
            marks[position.subject] = bar.close * ratios.get(position.subject, _ONE)
    return PortfolioState(
        as_of=state.as_of,
        cash=state.cash,
        positions=state.positions,
        marks=tuple(PositionMark(subject=code, price=marks[code]) for code in sorted(marks)),
        realized_pnl=state.realized_pnl,
        fees_paid=state.fees_paid,
    )


def _in_execution_order(orders: Sequence[TradeOrder]) -> tuple[TradeOrder, ...]:
    repeated = sorted(
        order_id for order_id, n in Counter(o.order_id for o in orders).items() if n > 1
    )
    if repeated:
        raise ValueError(f"orders given more than once: {repeated}")
    sells = sorted((o for o in orders if o.side == "sell"), key=lambda o: (o.ts_code, o.order_id))
    buys = sorted((o for o in orders if o.side == "buy"), key=lambda o: o.signal_session)
    return (*sells, *buys)


def _check_bars(session: date, bars: Mapping[str, MarketBar]) -> None:
    """Refuse, by name, every bar this module cannot use as given.

    A bar from another session is refused rather than read -- the look-ahead guard. A bar with no
    published band is refused because scaling a *derived* band is not proportional: the
    derivation rounds `previous_close x limit` to the fen, so a one-price limit-up bar at 11.03
    over a 10.03 close, scaled by 1.15, has a low of 12.6845 against a derived limit of 12.69 and
    would fill. The published band scales with the bar and keeps the verdict.
    """
    if not bars:
        raise ValueError(
            f"no bar at all for {session.isoformat()}: the step needs the session's bars, and a "
            "session with none is a data failure rather than a halt"
        )
    _refuse(
        "bars filed under another code",
        (f"{code} holds {bar.subject}" for code, bar in bars.items() if bar.subject != code),
    )
    _refuse(
        f"bars not dated the session {session.isoformat()}",
        (
            f"{code} on {bar.trade_date.isoformat()}, not {session.isoformat()}"
            for code, bar in bars.items()
            if bar.trade_date != session
        ),
    )
    _refuse(
        "bars without the exchange's published band",
        (code for code, bar in bars.items() if not bar.has_published_limits),
    )


def _checked_ratios(adjustment_ratio: Mapping[str, Decimal]) -> dict[str, Decimal]:
    _refuse(
        "adjustment ratios that are not positive",
        (f"{code}={ratio}" for code, ratio in adjustment_ratio.items() if ratio <= 0),
    )
    return dict(adjustment_ratio)


def _refuse(what: str, offenders: Iterable[str]) -> None:
    named = sorted(offenders)
    if named:
        raise ValueError(f"{what}: {named}")


def _scaled(bar: MarketBar, ratio: Decimal) -> MarketBar:
    """Every price of `bar` times `ratio`; a ratio of 1 returns the bar itself."""
    if ratio == _ONE:
        return bar
    prices = bar.model_dump(include=set(_SCALED_FIELDS))
    return MarketBar.model_validate(
        bar.model_dump() | {field: value * ratio for field, value in prices.items()}
    )
