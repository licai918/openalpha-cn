"""One session of the paper book settled: fills, order lifecycle, cool-downs, entry factors
(`V2-P7-009`).

The evening runs facts -> `screen` -> **settle** -> `decide_book` -> `build_trade_list` -> save the
snapshot. Settling comes after the screen because the order lifecycle needs the session's
survivors, and before the decision because the decision needs the book the session's fills left
(plan 1's simulation, `tests/unit/domain/test_survivor_book_lifecycle.py`, pins that order).
`settle_session` takes the snapshot of the session before (`paper_state.latest_snapshot`) and the
session's market, and does, in this order:

1. **Write-offs (D13, the user's decision).** A held name the facts day reports `not_listed` that
   has no bar on the session is gone from the market: no price will come again, and a sell for it
   can never fill. It is written off -- see *The write-off* below. Its live orders (only a sell
   can exist) are withdrawn, not handed to the step, so the kernel is never asked to sell a
   holding that has no price. A name reported `not_listed` that still has a bar is priced and
   traded like any holding (`FactsNotes.not_listed_with_bar` counts those).
2. **The due orders** -- every pending order with `execute_session <= session_index`, carried ones
   included -- all go to `paper_step.build_paper_step`, which takes out and records as
   `not_attempted` the ones whose name has no bar. An order not due yet stays pending, untried.
3. **The ratio table** is `adj_factor(session) / entry_adj` for every held name, an explicit 1
   where it has not moved. A held name with no factor on the session refuses the session by name:
   never 1, never yesterday's factor. A buy with a bar needs the session's factor too, since that
   is its entry factor if it fills. All refusals come before anything is written to the ledger.
4. **`PaperPortfolio.advance`** on the injected portfolio (its ledger is the one side effect; the
   evening transaction that makes it crash-safe is the caller's), then
   `paper_step.remark_at_close`: the day ends at the close, in the ledger's units.
5. **The lifecycle**: the attempts that filled are mapped back to their orders by lookup (never by
   parsing the attempt id), and `survivor_lifecycle.advance_orders` sorts the due orders into
   filled, live, expired and cancelled with the session's survivors. Every due order still live
   has one more try (`PaperBookSnapshot` checks that count exactly).
6. **Entry factors**: a holding keeps the factor of the session its buy filled; a name bought
   today takes today's; a name no longer held has none.
7. **Cool-downs** start on the **settlement session** -- the session a sale filled or a buy
   expired on -- not the signal session that planned the order (`update_cooldowns`).

Nothing is stored and the snapshot settled from is not changed. `Settlement.with_plan` folds the
evening's new orders in as untried pending orders and returns the snapshot to save; `book_state`
is what the kernel is told about the book.

**An opening book settles without a step.** A book that holds nothing and has no due order has no
name to ask the market about, and a `PortfolioBacktestStep` needs at least one bar. Such a session
is settled by moving the book to the session -- cash, positions and marks unchanged -- and the
session's `PaperSessionResult` is the one the runner gives for a session with no order and no bar
for any held name (`test_a_write_off_with_nothing_else_to_settle_records_what_the_runner_would_have`
compares the two). Any other session goes through the runner, so a book that holds names none of
which has a bar is refused by `build_paper_step` as a data failure.

## The write-off

The paper ledger stores `PortfolioTransition`s, whose `status` is `filled` or `rejected` and
nothing else; a third status would be a change to a stored contract (hard rule 3). So a write-off
is the ledger's one accepted shape, **labelled** so that no reader can take it for a trade:

- `status="filled"`, `reason="delisting_write_off"` on the transition **and** on its
  `ExecutionResult`, order id `write_off_id(code, session)` (`delisting_write_off:<code>@<date>`,
  outside the `tor_...@<date>` attempt-id space);
- the whole holding at the **convention price `WRITE_OFF_PRICE` = 0.01**, the smallest positive
  price the ledger can represent, standing for "nothing recovered" -- **a convention, not a price
  anybody traded at**; the holding's ledger shares x 0.01 reach cash, and the realised loss is that
  less the cost basis;
- **no fees**, and no cool-down (the name cannot be bought again anyway);
- `before` and `after` are both as of the session, after the close.

**What a reader of the paper ledger must do:** treat a transition with `reason ==
"delisting_write_off"` as a write-off -- not turnover, not a fill price. The notional (shares x
0.01) is not zero, so it adds a few yuan to cash and to any sum of filled notionals.

**The rule is conservative, and it is wrong for one kind of delisting:** a delisting by merger
(absorption with a share swap) pays holders in the acquirer's shares, and this rule books it as a
total loss. By reasoning, not measured: a merger target is halted before it is delisted, and the
kernel leaves a halted holding alone (`FREEZE_COMPATIBLE_REASONS`), so that is the likelier way
a held name reaches "delisted while held"; a delisting through the delisting period reaches it
only if the forced exit (a delisting-period name is special treatment) fails on every session
of that period.

## Units carried over from `paper_step`

- After an adjustment event the paper book charges fees on the scaled notional, where the P6 book
  charges on the raw notional; the two agree to the fen only at a ratio of 1
  (`paper.slippage_folded_execution`). A daily log must say so.
- A holding's `filled_price` and mark are in the ledger's units, `real price x ratio`; a report
  that reads them converts.
- With the protocol's costs folded in, the ledger's `transfer_fee` is slippage.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Protocol

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.multi_day import CarriedMark
from openalpha_cn.backtest.paper import PaperPortfolio, PaperSessionResult, refusing_outward_calls
from openalpha_cn.backtest.paper_state import EntryFactor, PaperBookSnapshot, PendingOrder
from openalpha_cn.backtest.paper_step import AttemptedOrder, build_paper_step, remark_at_close
from openalpha_cn.domain.execution import ExecutionResult
from openalpha_cn.domain.portfolio import (
    PortfolioOrder,
    PortfolioState,
    PortfolioTransition,
    PositionMark,
)
from openalpha_cn.domain.survivor_book import SurvivorBookConfig, survivor_config_digest
from openalpha_cn.domain.survivor_lifecycle import advance_orders, update_cooldowns
from openalpha_cn.domain.survivor_orders import TradeList, TradeOrder
from openalpha_cn.domain.survivor_policy import BookState, Cooldown, Holding

__all__ = [
    "WRITE_OFF_PRICE",
    "WRITE_OFF_REASON",
    "SessionMarketLike",
    "Settlement",
    "WriteOff",
    "book_state",
    "settle_session",
    "write_off_id",
]

WRITE_OFF_REASON: Final = "delisting_write_off"
"""The label on a write-off's transition and execution; see the module docstring."""

WRITE_OFF_PRICE: Final = Decimal("0.01")
"""The convention price of a write-off: the smallest positive price, standing for nothing
recovered. Not a price anybody traded at."""

_ONE: Final = Decimal(1)
_CENT: Final = Decimal("0.01")
_SIX: Final = Decimal("0.000001")


class SessionMarketLike(Protocol):
    """What a settlement reads of its session's market: C1's `SessionMarket` satisfies it with no
    import in either direction.

    `bars` holds a bar, with the exchange's published band, for every asked-for name that has one
    on the session; `adj_factor` the session's adjustment factor of every asked-for name that has
    one; `benchmark_close` the benchmark's close on the session.
    """

    @property
    def bars(self) -> Mapping[str, MarketBar]: ...

    @property
    def adj_factor(self) -> Mapping[str, Decimal]: ...

    @property
    def benchmark_close(self) -> Decimal: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteOff:
    """A holding written off on delisting: its labelled ledger transition, and the live orders for
    it (a sell) withdrawn because there is nothing left to sell."""

    ts_code: str
    transition: PortfolioTransition
    withdrawn: tuple[TradeOrder, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Settlement:
    """One settled session of the paper book; see the module docstring.

    `book` is as of `session` and marked at the close. `live` is every order still pending, each
    with its tries; `filled`, `expired` and `cancelled` are what `advance_orders` made of the due
    orders. `attempts` is every due order's try on the session, in execution order, and `result`
    the paper book's record of the session (before any write-off). `initialized` is whether the
    snapshot settled from had settled a session -- i.e. whether the book's first build has been
    decided, which is what `BookState.initialized` asks.
    """

    config_digest: str
    session: date
    session_index: int
    initialized: bool
    book: PortfolioState
    entry_factors: tuple[EntryFactor, ...]
    live: tuple[PendingOrder, ...]
    cooldowns: tuple[Cooldown, ...]
    filled: tuple[TradeOrder, ...]
    expired: tuple[TradeOrder, ...]
    cancelled: tuple[TradeOrder, ...]
    written_off: tuple[WriteOff, ...]
    attempts: tuple[AttemptedOrder, ...]
    result: PaperSessionResult

    def with_plan(self, trade_list: TradeList) -> PaperBookSnapshot:
        """The evening's snapshot: this settlement, with `trade_list`'s new orders pending, untried.

        `trade_list` must be the plan of this session, and its `carried` orders must be exactly the
        orders this settlement left live; a disagreement is refused by name either way, because a
        plan built from another book would put orders in the snapshot that this book never held.
        """
        if trade_list.signal_session != self.session_index:
            raise ValueError(
                f"the trade list was planned on session {trade_list.signal_session} and this "
                f"settlement is of session {self.session_index}"
            )
        live = {item.order.order_id for item in self.live}
        carried = {order.order_id for order in trade_list.carried}
        _refuse(
            "orders the trade list carries that this settlement does not hold live",
            (_named(order) for order in trade_list.carried if order.order_id not in live),
        )
        _refuse(
            "live orders the trade list does not carry",
            (_named(item.order) for item in self.live if item.order.order_id not in carried),
        )
        return PaperBookSnapshot(
            config_digest=self.config_digest,
            session_index=self.session_index,
            session=self.session,
            book=self.book,
            entry_factors=self.entry_factors,
            pending_orders=(
                *self.live,
                *(PendingOrder(order=order, attempts=0) for order in trade_list.orders),
            ),
            cooldowns=self.cooldowns,
            last_settled_session=self.session_index,
        )


def write_off_id(ts_code: str, session: date) -> str:
    """The ledger id of the write-off of `ts_code` on `session`."""
    return f"{WRITE_OFF_REASON}:{ts_code}@{session.isoformat()}"


def settle_session(
    *,
    previous: PaperBookSnapshot,
    session: date,
    session_index: int,
    market: SessionMarketLike,
    survivors: Collection[str],
    not_listed: Collection[str],
    portfolio: PaperPortfolio,
    config: SurvivorBookConfig,
    observed_on: date,
) -> Settlement:
    """Settle `session` from `previous`, the snapshot of the session before it.

    `session_index` is `session`'s ordinal, `previous.session_index + 1`. `survivors` is the
    session's survivor set (a live buy for a name outside it is cancelled). `not_listed` is every
    held code the session's facts day reports `not_listed` (`FactsDay.held_status`); one with no
    bar is written off. `portfolio` is the paper book, built by the caller with the paper ledger,
    `PortfolioLimits(1, 1, 0)` and the protocol's costs (D15); `config` is the configuration the
    book is settled under. `observed_on` is the date the session's market was observed.
    """
    digest = survivor_config_digest(config)
    _check_continuation(
        previous,
        session=session,
        session_index=session_index,
        digest=digest,
        observed_on=observed_on,
    )
    held = {position.subject for position in previous.book.positions}
    _refuse(
        "codes reported not listed that the book does not hold (the facts day's held_status "
        "describes the book's holdings)",
        set(not_listed) - held,
    )
    written = sorted(code for code in set(not_listed) if code not in market.bars)
    pending = previous.pending_orders
    due = tuple(
        item.order
        for item in pending
        if item.order.execute_session <= session_index and item.order.ts_code not in written
    )
    waiting = tuple(
        item
        for item in pending
        if item.order.execute_session > session_index and item.order.ts_code not in written
    )
    kept = sorted(held - set(written))
    buys_with_a_bar = sorted(
        {order.ts_code for order in due if order.side == "buy" and order.ts_code in market.bars}
    )
    _check_factors(market.adj_factor, session=session, held=kept, bought=buys_with_a_bar)
    entry = previous.entry_factors_by_code()
    ratios = {code: market.adj_factor[code] / entry[code] for code in kept}

    if not kept and not due:
        result = _advance_without_a_step(previous.book, session=session, observed_on=observed_on)
        attempts: tuple[AttemptedOrder, ...] = ()
        closed = result.after
    else:
        plan = build_paper_step(
            session=session,
            session_index=session_index,
            orders=due,
            bars=market.bars,
            adjustment_ratio=ratios,
            benchmark_close=market.benchmark_close,
        )
        result = portfolio.advance(state=previous.book, session=plan.step, observed_on=observed_on)
        bought = {
            transition.order.subject
            for transition in result.transitions
            if transition.status == "filled" and transition.order.side == "buy"
        }
        closed = remark_at_close(
            result.after,
            bars=market.bars,
            adjustment_ratio={**ratios, **dict.fromkeys(bought, _ONE)},
        )
        attempts = plan.attempts

    planned = {item.attempt_id: item.order.order_id for item in attempts}
    lifecycle = advance_orders(
        due,
        session=session_index,
        filled_order_ids={
            planned[transition.order.order_id]
            for transition in result.transitions
            if transition.status == "filled"
        },
        survivors=survivors,
    )
    tries = {item.order.order_id: item.attempts for item in pending}
    live = (
        *(
            PendingOrder(order=order, attempts=tries[order.order_id] + 1)
            for order in lifecycle.live
        ),
        *waiting,
    )
    cooldowns = update_cooldowns(
        previous.cooldowns,
        session=session_index,
        sold={order.ts_code for order in lifecycle.filled if order.side == "sell"},
        expired_buys={order.ts_code for order in lifecycle.expired},
        config=config,
    )

    book = closed
    write_offs: list[WriteOff] = []
    with refusing_outward_calls():
        for code in written:
            transition = _write_off(book, ts_code=code, session=session)
            portfolio.ledger.append(transition)
            book = transition.after
            write_offs.append(
                WriteOff(
                    ts_code=code,
                    transition=transition,
                    withdrawn=tuple(item.order for item in pending if item.order.ts_code == code),
                )
            )

    return Settlement(
        config_digest=previous.config_digest,
        session=session,
        session_index=session_index,
        initialized=previous.last_settled_session is not None,
        book=book,
        entry_factors=tuple(
            EntryFactor(
                ts_code=position.subject,
                entry_adj=(
                    entry[position.subject]
                    if position.subject in entry
                    else market.adj_factor[position.subject]
                ),
            )
            for position in book.positions
        ),
        live=tuple(
            sorted(
                live, key=lambda item: (item.order.side, item.order.ts_code, item.order.order_id)
            )
        ),
        cooldowns=cooldowns,
        filled=lifecycle.filled,
        expired=lifecycle.expired,
        cancelled=lifecycle.cancelled,
        written_off=tuple(write_offs),
        attempts=attempts,
        result=result,
    )


def book_state(
    source: Settlement | PaperBookSnapshot,
    *,
    config: SurvivorBookConfig,
    equity: Decimal | None = None,
) -> BookState:
    """What the kernel is told about the book of `source`, settled under `config`.

    Holdings are each held name's share count (one lot each, in the ledger's units, the units a
    sell is placed in); `pending_buys` and `pending_sells` are the names of the live orders;
    `equity` is cash plus every holding at its mark -- the session's close in the ledger's units,
    or the carried mark of a name with no bar -- unless the caller gives another. `initialized`:
    for a settlement, whether the snapshot it settled from had settled a session; for a snapshot,
    whether it has settled one (its evening's decision is in it).
    """
    digest = survivor_config_digest(config)
    if source.config_digest != digest:
        raise ValueError(
            f"the book is settled under configuration digest {source.config_digest} and the "
            f"kernel runs under {digest}: another configuration is another book"
        )
    if isinstance(source, Settlement):
        initialized = source.initialized
        orders = [item.order for item in source.live]
    else:
        initialized = source.last_settled_session is not None
        orders = [item.order for item in source.pending_orders]
    return BookState(
        session=source.session_index,
        initialized=initialized,
        equity=source.book.equity if equity is None else equity,
        holdings=tuple(
            Holding(ts_code=position.subject, quantity=position.quantity)
            for position in source.book.positions
        ),
        pending_buys=tuple(sorted(order.ts_code for order in orders if order.side == "buy")),
        pending_sells=tuple(sorted(order.ts_code for order in orders if order.side == "sell")),
        cooldowns=source.cooldowns,
    )


def _check_continuation(
    previous: PaperBookSnapshot,
    *,
    session: date,
    session_index: int,
    digest: str,
    observed_on: date,
) -> None:
    """Refuse a settlement that does not continue `previous`, before anything is written."""
    if previous.config_digest != digest:
        raise ValueError(
            f"the book is settled under configuration digest {previous.config_digest} and this "
            f"settlement runs under {digest}: another configuration is another book"
        )
    if session_index != previous.session_index + 1:
        raise ValueError(
            f"session index {session_index} does not follow the snapshot's "
            f"{previous.session_index}: a settlement settles the session after its snapshot's"
        )
    if session <= previous.session:
        raise ValueError(
            f"session {session.isoformat()} does not move the book forward from "
            f"{previous.session.isoformat()}"
        )
    if session > observed_on:
        raise ValueError(
            f"session {session.isoformat()} has not been observed as of {observed_on.isoformat()}"
        )


def _check_factors(
    factors: Mapping[str, Decimal],
    *,
    session: date,
    held: Iterable[str],
    bought: Iterable[str],
) -> None:
    held, bought = list(held), list(bought)
    _refuse(
        f"held names with no adjustment factor on {session.isoformat()} (every holding is valued "
        "at adj_factor(session) / entry_adj; a missing factor is never read as 1 or as an "
        "earlier one)",
        (code for code in held if code not in factors),
    )
    _refuse(
        f"buys with a bar and no adjustment factor on {session.isoformat()} (it would be the "
        "holding's entry factor if the buy fills)",
        (code for code in bought if code not in factors),
    )
    _refuse(
        "adjustment factors that are not positive",
        (f"{code}={factors[code]}" for code in (*held, *bought) if factors[code] <= 0),
    )


def _advance_without_a_step(
    state: PortfolioState, *, session: date, observed_on: date
) -> PaperSessionResult:
    """The record `PaperPortfolio.advance` gives for a session with no order and no bar for any
    held name: the book moved to `session`, every holding carried at its mark."""
    prices = {mark.subject: mark.price for mark in state.marks}
    moved = PortfolioState(
        as_of=session,
        cash=state.cash,
        positions=state.positions,
        marks=tuple(PositionMark(subject=code, price=prices[code]) for code in sorted(prices)),
        realized_pnl=state.realized_pnl,
        fees_paid=state.fees_paid,
    )
    exposure = Decimal(0) if moved.equity == 0 else moved.market_value / moved.equity
    return PaperSessionResult(
        trade_date=session.isoformat(),
        observed_on=observed_on.isoformat(),
        before=state,
        after=moved,
        transitions=(),
        carried_marks=tuple(
            CarriedMark(
                trade_date=session.isoformat(),
                subject=position.subject,
                price=prices[position.subject],
                sessions_carried=1,
            )
            for position in state.positions
        ),
        equity=moved.equity,
        gross_exposure=exposure.quantize(_SIX).quantize(_SIX),
    )


def _write_off(state: PortfolioState, *, ts_code: str, session: date) -> PortfolioTransition:
    """The labelled transition that removes `ts_code` from `state` at the convention price."""
    position = state.position(ts_code)
    notional = _money(WRITE_OFF_PRICE * position.quantity)
    realized = _money(notional - position.cost_basis)
    after = PortfolioState(
        as_of=session,
        cash=_money(state.cash + notional),
        positions=tuple(item for item in state.positions if item.subject != ts_code),
        marks=tuple(mark for mark in state.marks if mark.subject != ts_code),
        realized_pnl=_money(state.realized_pnl + realized),
        fees_paid=state.fees_paid,
    )
    return PortfolioTransition(
        status="filled",
        order=PortfolioOrder(
            order_id=write_off_id(ts_code, session),
            subject=ts_code,
            side="sell",
            quantity=position.quantity,
        ),
        before=state,
        after=after,
        execution=ExecutionResult(
            status="filled",
            side="sell",
            quantity=position.quantity,
            reason=WRITE_OFF_REASON,
            filled_price=WRITE_OFF_PRICE,
            notional=notional,
        ),
        reason=WRITE_OFF_REASON,
        realized_pnl_delta=realized,
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _named(order: TradeOrder) -> str:
    return f"{order.order_id} ({order.side} {order.ts_code})"


def _refuse(what: str, offenders: Iterable[str]) -> None:
    named = sorted(set(offenders))
    if named:
        raise ValueError(f"{what}: {named}")
