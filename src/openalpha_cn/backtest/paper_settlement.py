"""One session of the paper book settled: fills, order lifecycle, cool-downs, entry factors
(`V2-P7-009`).

The evening runs facts -> `screen` -> **settle** -> `decide_book` -> `build_trade_list` -> save the
snapshot. Settling comes after the screen because the order lifecycle needs the session's
survivors, and before the decision because the decision needs the book the session's fills left
(plan 1's simulation, `tests/unit/domain/test_survivor_book_lifecycle.py`, pins that order).
`settle_session` takes the snapshot of the session before (`paper_state.latest_snapshot`) and the
session's market, and does, in this order:

1. **Write-offs (D13, the user's decision).** A held name the facts day reports `delisted` that
   has no bar on the session is gone from the market: no price will come again, and a sell for it
   can never fill. It is written off -- see *The write-off* below. Its live orders (only a sell
   can exist) are withdrawn, not handed to the step, so the kernel is never asked to sell a
   holding that has no price. A name reported `delisted` that still has a bar is priced and
   traded like any holding. Only `delisted` is written off -- never a code the registry merely
   does not list yet: a write-off is an append to an append-only ledger.
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

Nothing is stored and the snapshot settled from is not changed. The session's own figures are
`Settlement.book`, `equity`, `gross_exposure` and `carried_marks` (after the close and the
write-offs); `Settlement.runner_record` is the runner's trace from before both, and
`Settlement.transitions` everything appended to the ledger, in order. `Settlement.with_plan`
folds the evening's new orders in as untried pending orders and returns the snapshot to save;
`book_state` is what the kernel is told about the book.

**A session on which nothing has a bar settles without a step.** A name absent from
`market.bars` has **no daily row** on the session -- a whole-day halt, or a name gone from the
market; the session's market refuses by name a held or ordered name that has a daily row and no
published band, so absence never stands for a band-less row. When no holding the session keeps
and no due order has a bar -- an opening book that holds nothing and has no order, a book whose
every holding is halted, a book whose only holding is written off -- there is no bar to build a
`PortfolioBacktestStep` from (it needs one) and nothing it could trade. Such a session is settled
by moving the book to the session -- cash, positions and marks unchanged, every holding carried --
and each due order is recorded as `not_attempted` under its attempt id with `NO_BAR_REASON`,
exactly as `build_paper_step` records an order with no bar; its `runner_record` is the one the
runner gives for a session with no order and no bar for any held name
(`test_a_write_off_with_nothing_else_to_settle_records_what_the_runner_would_have` compares the
two). Write-offs still follow. Every other session goes through the runner.

## The write-off

The paper ledger stores `PortfolioTransition`s, whose `status` is `filled` or `rejected` and
nothing else; a third status would be a change to a stored contract (hard rule 3). A `filled`
transition need not carry an execution, though, and a write-off is stored as exactly that -- a
**price-less** filled transition, **labelled** so that no reader can take it for a trade:

- `status="filled"`, `execution=None` -- no price, no notional, no fee: nothing was traded --
  and `reason="delisting_write_off"`; order id `write_off_id(code, session)`
  (`delisting_write_off:<code>@<date>`, outside the `tor_...@<date>` attempt-id space);
- the whole holding is removed and, **by convention, exactly zero is recovered**: cash is
  unchanged and the realised loss is the holding's whole cost basis (`realized_pnl_delta =
  -cost_basis`), so equity falls by exactly what the holding was marked at;
- no cool-down (the name cannot be bought again anyway);
- `before` and `after` are both as of the session, after the close.

**What a reader of the paper ledger must do:** treat a transition with `reason ==
"delisting_write_off"` as a write-off, not a sale. Having no execution, it is outside every
reading that goes through one -- the runner counts turnover only for a filled transition with an
execution -- but a reader that assumes every `filled` transition has an `execution` must check.

**The rule is conservative, and it is wrong whenever a delisting pays holders something:** a
delisting by merger (absorption with a share swap) pays them in the acquirer's shares, a cash
tender offer and a voluntary delisting with a cash-exit option pay them cash, and this rule books
each as a total loss. By reasoning, not measured: such a target is usually halted before it is
delisted, and the kernel leaves a halted holding alone (`FREEZE_COMPATIBLE_REASONS`), so that is
the likelier way a held name reaches "delisted while held"; a delisting through the delisting
period reaches it only if the forced exit (a delisting-period name is special treatment) fails on
every session of that period.

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
from openalpha_cn.backtest.paper_step import (
    NO_BAR_REASON,
    AttemptedOrder,
    attempt_id,
    build_paper_step,
    remark_at_close,
)
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
    "WRITE_OFF_REASON",
    "SessionMarketLike",
    "Settlement",
    "WriteOff",
    "book_state",
    "ledger_reference_prices",
    "settle_session",
    "write_off_id",
]

WRITE_OFF_REASON: Final = "delisting_write_off"
"""The label on a write-off's transition and execution; see the module docstring."""

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

    `book` is as of `session`, marked at the close and after the write-offs: the book the
    session ends with, and `equity`, `gross_exposure` and `carried_marks` are its figures -- **the
    session's own, the ones a daily log reports**. `live` is every order still pending, each with
    its tries; `filled`, `expired` and `cancelled` are what `advance_orders` made of the due
    orders. `attempts` is every due order's try on the session, in execution order.
    `first_decision_session` is the snapshot settled from's: the session whose evening first
    planned the book, or `None` while no plan has been made (an opening book, or one only caught
    up since). `initialized` -- what `BookState.initialized` asks: has the first build been
    decided? -- is whether it is set. Having settled a session is not having decided one.

    `runner_record` is `PaperPortfolio.advance`'s record of the session, **before the close
    re-mark and before the write-offs**: a name bought on the session is still at the open it was
    bought at, and a name written off is still held, carried at its last mark. It is the trace of
    what the runner did, not the session's result; reading its `equity` overstates a write-off
    day and shows the loss a session late. `transitions` is everything the session appends to
    the ledger, in the order it is appended: the runner's, then the write-offs.
    """

    config_digest: str
    session: date
    session_index: int
    first_decision_session: int | None
    book: PortfolioState
    entry_factors: tuple[EntryFactor, ...]
    live: tuple[PendingOrder, ...]
    cooldowns: tuple[Cooldown, ...]
    filled: tuple[TradeOrder, ...]
    expired: tuple[TradeOrder, ...]
    cancelled: tuple[TradeOrder, ...]
    written_off: tuple[WriteOff, ...]
    attempts: tuple[AttemptedOrder, ...]
    runner_record: PaperSessionResult

    @property
    def initialized(self) -> bool:
        """Whether the book's first build had been decided before this session."""
        return self.first_decision_session is not None

    @property
    def equity(self) -> Decimal:
        """Cash plus every holding at its mark, after the close and the write-offs."""
        return self.book.equity

    @property
    def gross_exposure(self) -> Decimal:
        """Market value over equity after the close and the write-offs, to six places -- the
        runner's measure, taken on the session's final book."""
        return _gross_exposure(self.book)

    @property
    def carried_marks(self) -> tuple[CarriedMark, ...]:
        """The holdings the session left at their earlier mark for want of a bar -- the runner's
        carried marks less the names written off. `sessions_carried` is always 1 here: each
        `PaperPortfolio.advance` is a one-session run, so the length of a halt is not in it."""
        gone = {item.ts_code for item in self.written_off}
        return tuple(mark for mark in self.runner_record.carried_marks if mark.subject not in gone)

    @property
    def transitions(self) -> tuple[PortfolioTransition, ...]:
        """Every transition the session appends to the ledger, in the order appended: the
        runner's (in `PaperPortfolio.advance`), then the write-offs (after the close)."""
        return (
            *self.runner_record.transitions,
            *(item.transition for item in self.written_off),
        )

    def with_plan(self, trade_list: TradeList) -> PaperBookSnapshot:
        """The evening's snapshot: this settlement, with `trade_list`'s new orders pending, untried.

        `trade_list` must be the plan of this session, and its `carried` orders must be exactly the
        orders this settlement left live; a disagreement is refused by name either way, because a
        plan built from another book would put orders in the snapshot that this book never held.
        A plan is a decision, so the book's first decision is this session when it had none (even a
        plan that places no order), and is carried unchanged when it had one.
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
            first_decision_session=(
                self.session_index
                if self.first_decision_session is None
                else self.first_decision_session
            ),
        )

    def without_plan(self) -> PaperBookSnapshot:
        """The snapshot of a session settled without a decision -- a missed evening caught up:
        the live orders carried, no new one, and the book's first decision carried as it was
        (`None` stays `None`, so the next decision is still the initial build)."""
        return PaperBookSnapshot(
            config_digest=self.config_digest,
            session_index=self.session_index,
            session=self.session,
            book=self.book,
            entry_factors=self.entry_factors,
            pending_orders=self.live,
            cooldowns=self.cooldowns,
            last_settled_session=self.session_index,
            first_decision_session=self.first_decision_session,
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
    delisted: Collection[str],
    portfolio: PaperPortfolio,
    config: SurvivorBookConfig,
    observed_on: date,
) -> Settlement:
    """Settle `session` from `previous`, the snapshot of the session before it.

    `session_index` is `session`'s ordinal, `previous.session_index + 1`. `survivors` is the
    session's survivor set (a live buy for a name outside it is cancelled). `delisted` is every
    held code the session's facts day reports `delisted` (`FactsDay.held_status`); one with no
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
        "codes reported delisted that the book does not hold (the facts day's held_status "
        "describes the book's holdings)",
        set(delisted) - held,
    )
    written = sorted(code for code in set(delisted) if code not in market.bars)
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

    if not any(code in market.bars for code in (*kept, *(order.ts_code for order in due))):
        result = _advance_without_a_step(previous.book, session=session, observed_on=observed_on)
        attempts: tuple[AttemptedOrder, ...] = tuple(
            AttemptedOrder(
                order=order,
                attempt_id=attempt_id(order, session),
                outcome="not_attempted",
                reason=NO_BAR_REASON,
            )
            for order in _in_execution_order(due)
        )
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
        first_decision_session=previous.first_decision_session,
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
        runner_record=result,
    )


def ledger_reference_prices(settlement: Settlement) -> dict[str, Decimal]:
    """Every holding the session ends with, at its mark in the ledger's units -- the reference
    prices `build_trade_list` needs for the held names.

    The ledger keeps the shares a buy filled with and meets later prices in those units
    (`paper_step`), so a held name's reference price is its close times its adjustment ratio --
    which is its mark after the close re-mark -- or, for a name with no bar on the session, the
    mark it carried. A real close would misprice a held name across an adjustment event: after a
    10-for-10 bonus issue the ledger's shares at the real close are worth half the holding, the
    evening's sell is estimated to raise half of what it will, and the buys it funds are sized
    short. Entrants (names not held) take their real close from the facts, at ratio 1.
    """
    held = {position.subject for position in settlement.book.positions}
    return {mark.subject: mark.price for mark in settlement.book.marks if mark.subject in held}


def book_state(
    settlement: Settlement,
    *,
    config: SurvivorBookConfig,
    equity: Decimal | None = None,
) -> BookState:
    """What the kernel is told about the book `settlement` leaves, settled under `config`.

    Holdings are each held name's share count (one lot each, in the ledger's units, the units a
    sell is placed in); `pending_buys` and `pending_sells` are the names of the live orders;
    `equity` is cash plus every holding at its mark -- the session's close in the ledger's units,
    or the carried mark of a name with no bar -- unless the caller gives another; `initialized`
    is whether the book's first decision had been made before the session.

    **Read from a settlement only.** An evening's snapshot holds that evening's new orders as
    pending, so a state read from it is one no decision ever saw, and reproducing a decision
    from it would count tonight's buys twice; a snapshot is refused by name. An opening book is
    no exception: its first evening settles it (without a step) and the kernel is told about that
    settlement.
    """
    if not isinstance(settlement, Settlement):
        raise TypeError(
            f"book_state reads a settlement, not a {type(settlement).__name__}: a snapshot holds "
            "its evening's new orders as pending, so a decision is never reproduced from one"
        )
    digest = survivor_config_digest(config)
    if settlement.config_digest != digest:
        raise ValueError(
            f"the book is settled under configuration digest {settlement.config_digest} and the "
            f"kernel runs under {digest}: another configuration is another book"
        )
    orders = [item.order for item in settlement.live]
    return BookState(
        session=settlement.session_index,
        initialized=settlement.initialized,
        equity=settlement.book.equity if equity is None else equity,
        holdings=tuple(
            Holding(ts_code=position.subject, quantity=position.quantity)
            for position in settlement.book.positions
        ),
        pending_buys=tuple(sorted(order.ts_code for order in orders if order.side == "buy")),
        pending_sells=tuple(sorted(order.ts_code for order in orders if order.side == "sell")),
        cooldowns=settlement.cooldowns,
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
        gross_exposure=_gross_exposure(moved),
    )


def _in_execution_order(orders: Iterable[TradeOrder]) -> tuple[TradeOrder, ...]:
    """`paper_step.build_paper_step`'s order: sells by code then order id, then buys by signal
    session, keeping the order given within one."""
    listed = list(orders)
    sells = sorted((o for o in listed if o.side == "sell"), key=lambda o: (o.ts_code, o.order_id))
    buys = sorted((o for o in listed if o.side == "buy"), key=lambda o: o.signal_session)
    return (*sells, *buys)


def _gross_exposure(state: PortfolioState) -> Decimal:
    """`multi_day`'s measure and rounding, as `PaperSessionResult.gross_exposure` carries it."""
    exposure = Decimal(0) if state.equity == 0 else state.market_value / state.equity
    return exposure.quantize(_SIX).quantize(_SIX)


def _write_off(state: PortfolioState, *, ts_code: str, session: date) -> PortfolioTransition:
    """The labelled, price-less transition that removes `ts_code` from `state`, recovering
    nothing: cash unchanged, the whole cost basis realised as a loss."""
    position = state.position(ts_code)
    realized = _money(-position.cost_basis)
    after = PortfolioState(
        as_of=session,
        cash=state.cash,
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
        execution=None,
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
