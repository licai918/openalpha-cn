"""The paper book's evenings: its opening, and each evening as one crash-safe transaction
(`V2-P7-009`).

## The opening

A paper book is opened once per runtime directory, on the epoch session (the ordinal the
calendar counts from), by saving an **opening snapshot**: the cash it is given, nothing held,
nothing settled (`last_settled_session=None`) and nothing decided (`first_decision_session=None`),
so the first evening after it is the kernel's initial build. `open_book` writes it through
`paper_state.save_snapshot`, which holds the chain rules: the same opening again writes nothing,
and any other opening over a held book -- another amount or configuration on the same day, or a
restart on a later one -- is refused by name. Nothing reads a book that was never opened: every
reader refuses an empty store.

## One evening

The evening runs facts -> `screen` -> **settle** -> `decide_book` -> `build_trade_list` -> save
the snapshot (the order plan 1's simulation pins: settling needs tonight's survivors, deciding
needs the settled book). The caller brings the facts day's `ScreenResult`, the session's market
and the codes the facts day reports `delisted`; `run_evening` does the rest as one transaction
in three stages:

1. **Dry run.** The book the session settles from is `paper_state.latest_snapshot`'s answer --
   the snapshot of the session just before, of this configuration, or a refusal by name. The
   session is settled (`paper_settlement.settle_session`) on a paper book the caller's factory
   builds on a `DryRunLedger`, an in-memory ledger: nothing real is touched, and the dry run
   reads nothing from the real ledger, so its answer does not depend on what a crashed run left
   there.
2. **Decide.** `book_state` -> `decide_book` -> `build_trade_list` (held names priced in the
   ledger's units, `ledger_reference_prices`; entrants at their real closes) -> `with_plan`.
   **Every refusal the evening can meet after reading its book happens in these two stages**,
   so a refused evening leaves the real ledger and the store byte for byte as they were.
3. **Commit.** The settlement's transitions (`Settlement.transitions`: the runner's, then the
   write-offs) are appended to the real ledger in order, **and only then** the snapshot is
   saved. The snapshot is the commit: a session is settled when, and only when, its snapshot is
   held.

### Why that order makes it crash-safe

Measured, and pinned by a test: `SQLitePortfolioLedger.append` compares the stored payload with
the new one, **writes nothing when they are equal**, and refuses a different payload under the
same order id (`ValueError`, "portfolio order_id conflicts"); `DryRunLedger` does the same. A
session's transitions are a function of the snapshot before it and the session's inputs (the
attempt ids carry the session's date, the write-off ids the code and the date), so:

- **A crash anywhere before the snapshot is saved** -- after k of the ledger rows, or after all of
  them -- leaves the session unsettled: no snapshot, and `latest_snapshot` refuses the next
  session by name rather than settling it from the session before. Run again, the evening
  reproduces the same transitions, the k already held are appended as no-ops, the rest are
  appended, and the snapshot saved is byte for byte the one an uninterrupted run saves. Every
  row is in the ledger exactly once.
- **A run after the snapshot is saved** finds it held and answers `unchanged`, writing nothing
  (after checking it was settled under this configuration and ordinal). It has no plan to give
  back: the evening's trade list is the caller's to keep when the evening is first `created`.
- **The other order would not be safe**: a snapshot saved before its rows makes a crash between
  them a settled session whose rows are missing for good -- the run again answers `unchanged`.

**What the transaction relies on the caller for: the same inputs on a run again.** A run with
other inputs after a partial commit is refused only where it produces another transition under
an id already held (a different fill price, say); a transition the first run appended and the
second does not produce at all -- an order with a bar on one reading of the market and none on
the other, a write-off on one delisted set and not the other -- stays in the ledger beside a
snapshot that does not know it, and nothing here can see it (`PortfolioLedger` is `append`
only). The daily entry (`V2-P7-008`) therefore pins every input of an evening in its journal --
the facts' `as_of`, the market's `as_of`, the delisted set -- so that a run again reads what the
first run read.

## What the caller owns

- **The paper book's construction (D15).** `make_portfolio` builds a `PaperPortfolio` on the
  ledger it is given, closing over `PortfolioLimits(1, 1, 0)` and the protocol's costs; this
  module never builds one with real limits or costs (it may not import `strategy_view`, where
  the costs live). A factory that builds the book on any other ledger is refused before the
  session settles: the dry run would not be dry.
- **The two error classes of a broken store.** Every refusal about the book's state is a
  `PaperStateError`; a fault the store finds in its own documents arrives as the store's
  `PaperStateStoreError`, which `backtest/` cannot import. A caller that must not run on a broken
  book catches both. Contract refusals (a missing reference price, a book that cannot be
  decided) are `ValueError`s.

This module lives under `backtest/` and reaches no store: the snapshot store arrives as
`PaperStateDocumentStore` and the ledger as `PortfolioLedger`, the `Protocol`s beside their
consumers.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from openalpha_cn.backtest.multi_day import PortfolioLedger
from openalpha_cn.backtest.paper import PaperPortfolio, refusing_outward_calls
from openalpha_cn.backtest.paper_settlement import (
    SessionMarketLike,
    Settlement,
    book_state,
    ledger_reference_prices,
    settle_session,
)
from openalpha_cn.backtest.paper_state import (
    PaperBookSnapshot,
    PaperStateDocumentStore,
    PaperStateError,
    latest_snapshot,
    load_snapshot,
    save_snapshot,
)
from openalpha_cn.domain.portfolio import PortfolioState, PortfolioTransition
from openalpha_cn.domain.survivor_book import (
    ScreenResult,
    SurvivorBookConfig,
    survivor_config_digest,
)
from openalpha_cn.domain.survivor_orders import TradeList, build_trade_list
from openalpha_cn.domain.survivor_policy import BookDecision, decide_book

__all__ = [
    "DryRunLedger",
    "EveningResult",
    "open_book",
    "run_evening",
]

Outcome = Literal["created", "unchanged"]


class DryRunLedger:
    """The in-memory ledger an evening settles into before anything real is touched.

    It keeps what it is given, in order, with `SQLitePortfolioLedger.append`'s rule: the same
    transition again under its order id is a no-op, and a different one is refused.
    """

    def __init__(self) -> None:
        self._payloads: dict[str, str] = {}
        self._transitions: list[PortfolioTransition] = []

    @property
    def transitions(self) -> tuple[PortfolioTransition, ...]:
        """Every transition appended, once each, in the order appended."""
        return tuple(self._transitions)

    def append(self, transition: PortfolioTransition) -> None:
        """Keep `transition`; refuse another payload under an order id already held."""
        order_id = transition.order.order_id
        payload = transition.model_dump_json(exclude_computed_fields=True)
        held = self._payloads.get(order_id)
        if held is not None:
            if held != payload:
                raise ValueError(f"portfolio order_id conflicts: {order_id}")
            return
        self._payloads[order_id] = payload
        self._transitions.append(transition)


@dataclass(frozen=True, slots=True, kw_only=True)
class EveningResult:
    """What one evening did.

    `created`: the session was settled, decided and committed tonight; `snapshot` is the one
    saved, and `trade_list`, `decision` and `settlement` are tonight's. `unchanged`: the session
    was already settled (by an evening or a catch-up) and nothing was written; `snapshot` is the
    one held, and the other three are `None` -- a settled session is not decided again.
    """

    outcome: Outcome
    snapshot: PaperBookSnapshot
    trade_list: TradeList | None
    decision: BookDecision | None
    settlement: Settlement | None

    def __post_init__(self) -> None:
        created = self.outcome == "created"
        tonight = (self.trade_list, self.decision, self.settlement)
        if any((part is not None) != created for part in tonight):
            raise ValueError(
                "a created evening carries its trade list, decision and settlement, and an "
                "unchanged one carries none of them"
            )


def open_book(
    *,
    store: PaperStateDocumentStore,
    session: date,
    session_index: int,
    cash: Decimal,
    config_digest: str,
) -> PaperBookSnapshot:
    """Open the paper book on `session` (ordinal `session_index`) with `cash`, under the
    configuration whose `survivor_config_digest` is `config_digest`; return the opening snapshot.

    The same opening again writes nothing and returns it -- also once evenings have been settled
    on top of it, since it changes nothing. Any other opening over a store that holds a book is
    refused by name (`save_snapshot`). An amount that is not positive is refused before anything
    is written: the kernel sizes every buy from a positive equity, so a book opened without one
    could never decide, and the directory it was opened in would be spoiled for good.
    """
    if not cash.is_finite() or cash <= 0:
        raise ValueError(
            f"opening cash must be a positive amount: {cash}; the kernel sizes every buy from "
            "the book's equity, and a book is opened once per runtime directory"
        )
    snapshot = PaperBookSnapshot(
        config_digest=config_digest,
        session_index=session_index,
        session=session,
        book=PortfolioState(as_of=session, cash=cash),
        last_settled_session=None,
        first_decision_session=None,
    )
    save_snapshot(store, snapshot)
    return snapshot


def run_evening(
    *,
    store: PaperStateDocumentStore,
    ledger: PortfolioLedger,
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    config: SurvivorBookConfig,
    session: date,
    session_index: int,
    market: SessionMarketLike,
    screen: ScreenResult,
    entrant_prices: Mapping[str, Decimal],
    delisted: Collection[str],
    observed_on: date,
) -> EveningResult:
    """Settle, decide and commit the evening of `session` (ordinal `session_index`); see the
    module docstring for the transaction and why it is crash-safe.

    `store` and `ledger` are the paper book's own (`StorageContainer.paper_state_store` and
    `.paper_ledger`); `make_portfolio` builds the paper book on the ledger it is handed (D15);
    `config` is the configuration the book was opened under. `market` is the session's bars,
    adjustment factors and benchmark close; `screen` is the kernel's screen of the session's
    facts day, whose survivors settle tonight's live buys and whose verdicts the decision reads;
    `entrant_prices` are the facts day's real closes of names the book does not hold (a held name
    is priced from the ledger, in its units, whatever this says); `delisted` is every held code
    the facts day reports `delisted`; `observed_on` is the date the inputs were read.
    """
    digest = survivor_config_digest(config)
    held = _settled_already(store, session=session, session_index=session_index, digest=digest)
    if held is not None:
        return EveningResult(
            outcome="unchanged", snapshot=held, trade_list=None, decision=None, settlement=None
        )
    previous = latest_snapshot(
        store, settling_session=session, settling_index=session_index, config_digest=digest
    )
    settlement = _settle_dry(
        previous=previous,
        make_portfolio=make_portfolio,
        session=session,
        session_index=session_index,
        market=market,
        survivors={name.ts_code for name in screen.survivors},
        delisted=delisted,
        config=config,
        observed_on=observed_on,
    )
    state = book_state(settlement, config=config)
    decision = decide_book(config=config, screen=screen, state=state)
    trade_list = build_trade_list(
        config=config,
        state=state,
        decision=decision,
        reference_prices={**entrant_prices, **ledger_reference_prices(settlement)},
        cash=settlement.book.cash,
        carried=tuple(item.order for item in settlement.live),
    )
    snapshot = settlement.with_plan(trade_list)
    outcome = _commit(store=store, ledger=ledger, settlement=settlement, snapshot=snapshot)
    return EveningResult(
        outcome=outcome,
        snapshot=snapshot,
        trade_list=trade_list,
        decision=decision,
        settlement=settlement,
    )


def _settled_already(
    store: PaperStateDocumentStore, *, session: date, session_index: int, digest: str
) -> PaperBookSnapshot | None:
    """The snapshot held for `session`, checked to be this book's at this ordinal, or `None`."""
    if session not in store.sessions():
        return None
    held = load_snapshot(store, session)
    named = session.isoformat()
    if held.last_settled_session is None:
        raise PaperStateError(
            f"{named} is the opening of the book, which no evening settles; its first evening "
            "is the session after it"
        )
    if held.config_digest != digest:
        raise PaperStateError(
            f"{named} is settled under configuration digest {held.config_digest} and this "
            f"evening runs under {digest}: another configuration is another book, with a "
            "runtime directory of its own"
        )
    if held.session_index != session_index:
        raise PaperStateError(
            f"{named} is settled as session index {held.session_index}, not {session_index}: "
            "the calendar that numbers this evening disagrees with the book's"
        )
    return held


def _settle_dry(
    *,
    previous: PaperBookSnapshot,
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    session: date,
    session_index: int,
    market: SessionMarketLike,
    survivors: Collection[str],
    delisted: Collection[str],
    config: SurvivorBookConfig,
    observed_on: date,
) -> Settlement:
    """`settle_session` on a paper book that records into a fresh `DryRunLedger`."""
    dry = DryRunLedger()
    portfolio = make_portfolio(dry)
    if portfolio.ledger is not dry:
        raise ValueError(
            "make_portfolio built the paper book on another ledger than the dry run's ledger it "
            "was handed; the session would be appended to that ledger before it is decided"
        )
    settlement = settle_session(
        previous=previous,
        session=session,
        session_index=session_index,
        market=market,
        survivors=survivors,
        delisted=delisted,
        portfolio=portfolio,
        config=config,
        observed_on=observed_on,
    )
    if dry.transitions != settlement.transitions:
        raise ValueError(
            "the dry run's ledger did not receive the settlement's transitions; the paper book "
            "make_portfolio built records somewhere else, and the evening is not committed"
        )
    return settlement


def _commit(
    *,
    store: PaperStateDocumentStore,
    ledger: PortfolioLedger,
    settlement: Settlement,
    snapshot: PaperBookSnapshot,
) -> Outcome:
    """Append the session's transitions to the real ledger, in order, then save the snapshot."""
    with refusing_outward_calls():
        for transition in settlement.transitions:
            ledger.append(transition)
    return save_snapshot(store, snapshot)
