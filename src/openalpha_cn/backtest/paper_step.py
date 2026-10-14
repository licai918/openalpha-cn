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
   prices meet it in those units. The *entry session* is the session on which the BUY FILLED,
   not the one that planned it: an ex-rights date between the signal and the fill would make the
   signal-day factor wrong. Unscaled, a 1.15 event marks the holding down by
   1 - 1 / 1.15 = 13% on a session nobody lost anything.

   **What the convention does not cover** (disclosed, not fixed):

   - The ledger's share count is the count at purchase, not what an account holds after a bonus
     issue or a split. For those two the notional, fees and cash agree with the account to the
     fen (1,000 ledger shares sold at 20.00 on the ex-date of a 10-for-10 issue are 2,000 real
     shares at 10.00), and anything that shows shares to a person converts.
   - A **cash dividend** does not agree with the account. The convention reinvests the dividend
     into the name: it reaches cash only when the name is sold, no dividend tax is charged
     (the A-share tax is 20% / 10% / 0% by holding period), and commission and stamp duty on
     the sale are charged on the dividend-inclusive notional, where an account sells at the
     ex-dividend price and has received the dividend separately.
   - Across an adjustment event the ledger's prices are in the ledger's own basis. A name held
     through one is sold at `open x ratio` and marked at `close x ratio`: a `filled_price` or a
     mark that nobody traded at, often a 28-significant-digit `Decimal`
     (`10.11707317073170731707317073`). The notional is quantised to the cent, so cash is exact;
     a report that reads a `filled_price` or a mark of such a name must convert it (divide by
     the ratio) or say that it is in ledger units.

Order of execution: **sells first** (by code, then order id), so a sale's cash is there for the
session's buys; then **buys, the earliest-planned first** (by `signal_session`; within one
signal session, in the order the caller hands them in). `build_trade_list` sized today's buys
after reserving cash for the buys still live from earlier sessions, so those are the ones the
cash was kept for. The caller's order within one signal session is the selection rule's draw
order on a buy's *first* attempt only: from the second attempt on, `advance_orders` has
re-sorted the live orders by `(side, ts_code, order_id)`, so the buys of one signal day come
back in code order and the draw order is lost. The effect is small, because the cash for every
carried buy was reserved in full when it was planned, but it is not nil: when cash covers only
some of them, which of the same-day buys fills follows that order.

What the caller owes, and what the builder refuses by name:

- `orders` are the orders due on `session` or carried to it from an earlier one. `session_index`
  is `session`'s ordinal in the trading calendar, the ordinal `TradeOrder` counts
  `signal_session`, `execute_session` and `expires_session` in. An order that executes after
  it (planned after today's close, executing tomorrow) is *not due yet* and a buy whose last
  session is before it has *expired*: both are refused, because handed to today's step the
  first would fill at the open of the session that produced it. An order carried from an
  earlier session (`execute_session <= session_index`) is accepted.
- `bars` is every bar of the session it has (the runner ignores names it neither holds nor
  trades), each dated `session` and carrying the exchange's published band.
- `adjustment_ratio` names **every held name that matters for the session, with an explicit 1
  for a name whose factor has not moved.** In `build_paper_step` that is every name with a SELL
  order, whether or not it has a bar, since a sell is for a held name; in `remark_at_close` it
  is every held name that has a bar that session, including a name bought on that session
  (held now; its entry factor is today's, so its ratio is 1). An entry that is missing is
  refused by name and never read as 1: 1 says "the factor has not moved", and a forgotten entry
  would sell the ledger's shares at the unscaled price and write the wrong cash into an
  append-only ledger (1,000 shares bought at 20.00 and sold on the ex-date of a 10-for-10 bonus
  issue realise -10,010.10 with no entry, not -16.20). A BUY of a name not held needs no entry
  (it is at ratio 1 on the session it fills; an entry other than 1 is refused), and a held name
  with no bar is carried unchanged and needs none. `build_paper_step` does not see the book, so
  a held name that is neither sold nor bought is checked only by `remark_at_close`.

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
    session_index: int,
    orders: Sequence[TradeOrder],
    bars: Mapping[str, MarketBar],
    adjustment_ratio: Mapping[str, Decimal],
    benchmark_close: Decimal,
) -> PaperStepPlan:
    """The step for `session` and the attempt of every order in `orders`; see the module doc.

    `session_index` is `session`'s ordinal in the trading calendar: an order that executes after
    it is refused as not due yet, and a buy whose last session is before it as expired.
    `bars` must all be dated `session` and carry the exchange's published band.
    `adjustment_ratio` must name the code of every SELL order, with an explicit 1 where its
    factor has not moved; a missing entry is refused by name, not read as 1. A buy of a name not
    held needs no entry. `benchmark_close` goes into the step unchanged (the paper book does not
    keep it).
    """
    _check_bars(session, bars)
    ratios = _checked_ratios(adjustment_ratio)
    planned = _in_execution_order(orders)
    _check_due(session_index, planned)
    _refuse(
        "sells for names with no adjustment ratio (a sell is for a held name, so the table "
        "names it, with an explicit 1 where its factor has not moved)",
        {order.ts_code for order in planned if order.side == "sell"} - ratios.keys(),
    )
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

    Pass the `bars` the session's step was built from, and an `adjustment_ratio` naming every
    held name that has a bar that session -- an explicit 1 where its factor has not moved, and
    for a name bought on this session; a missing entry is refused by name, not read as 1. A held
    name with no bar keeps its mark and needs no entry; cash, positions, fees and realized
    profit are untouched.
    """
    _check_bars(state.as_of, bars)
    ratios = _checked_ratios(adjustment_ratio)
    _refuse(
        "held names with a bar and no adjustment ratio (the table names every held name that "
        "has a bar, with an explicit 1 where its factor has not moved)",
        {position.subject for position in state.positions if position.subject in bars}
        - ratios.keys(),
    )
    marks = {mark.subject: mark.price for mark in state.marks}
    for position in state.positions:
        bar = bars.get(position.subject)
        if bar is not None:
            marks[position.subject] = bar.close * ratios[position.subject]
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


def _check_due(session_index: int, orders: Iterable[TradeOrder]) -> None:
    """Refuse, by name, an order that is not for this session: the look-ahead guard for orders.

    An order planned after today's close executes at tomorrow's open; handed to today's step it
    would fill at the open of the session that produced it. A buy past its last session has
    expired. An order carried from an earlier session (`execute_session <= session_index`) is due.
    """
    _refuse(
        f"orders not due yet on session {session_index}",
        (
            f"{order.order_id} ({order.ts_code}) executes on session {order.execute_session}"
            for order in orders
            if order.execute_session > session_index
        ),
    )
    _refuse(
        f"buys expired before session {session_index}",
        (
            f"{order.order_id} ({order.ts_code}) expired after session {order.expires_session}"
            for order in orders
            if order.expires_session is not None and order.expires_session < session_index
        ),
    )


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
