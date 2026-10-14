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
3. **Commit.** First the real ledger is **read**: its rows for the session (selected by
   `after.as_of`, which a runner row carries as the step's date and a write-off as the
   session's -- never by parsing an id) must all be rows this run writes, with the same
   payloads; a row it would not write, or would write otherwise, refuses the run by name
   (`PaperLedgerConflictError`). Then the settlement's transitions (`Settlement.transitions`:
   the runner's, then the write-offs) are appended in order, **and only then** the snapshot is
   saved. The snapshot is the commit: a session is settled when, and only when, its snapshot
   is held.

**Every refusal a run on the same inputs can meet happens before its first append** -- in the
dry run, the decision or the ledger read -- so a refused run leaves the real ledger and the
store byte for byte as they were. What can still stop a run after it has appended is not a
refusal of its inputs: a crash, a fault of the store as the snapshot is saved (the session is
then unsettled, and a run again converges, below), and another writer appending to the ledger
between this run's read and its append, which the ledger refuses and the run reports as
`PaperLedgerConflictError` naming the session, the row and how many of the session's rows were
in.

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
- **A run after the snapshot is saved** -- the daily entry crashed between the save and its own
  journal write, say -- finds the session settled and answers `unchanged`, **writing nothing**,
  with the evening's settlement, decision and trade list derived again: after checking the held
  snapshot was settled under this configuration and ordinal, the same dry run and the same
  decision are made from the snapshot of the session before it (read with `load_snapshot`:
  `latest_snapshot` refuses a settled session), and the snapshot they give must be the held one,
  address and bytes, and the ledger's rows for the session must be exactly the transitions they
  give -- the same ids and payloads, none missing and none extra. Either failing is refused by
  name, and with the inputs pinned by the daily entry's journal it is a fault, never a normal
  case: the inputs differ from the ones the session was settled with, the ledger was altered,
  or the session was settled the other way -- by a catch-up where this is an evening, or by an
  evening where this is a catch-up. **That last is refused only where the two differ**: a
  session whose evening planned no order, and whose book had been decided before (a first
  decision held), is the same snapshot either way, byte for byte, so an evening run over its
  catch-up answers `unchanged` with a decision that no evening made (one that plans nothing),
  and a catch-up run over its evening answers `unchanged` too.
- **The other order would not be safe**: a snapshot saved before its rows makes a crash between
  them a settled session whose rows are missing for good -- the run again answers `unchanged`.

**A run on other inputs after a partial commit is refused before it appends anything**: a row
the ledger holds for the session that this run would not write -- an order with a bar on one
reading of the market and none on the other, a write-off on one delisted set and not the other
-- or would write with another payload -- a different fill price -- is named. What the read
cannot tell is which reading of the inputs was the right one: that is the journal's. The daily
entry (`V2-P7-008`) pins every input of an evening -- the facts' `as_of`, the market's `as_of`,
`held` and the delisted set -- so that a run again reads what the first run read; a ledger row
is never deleted or rewritten to make a run fit.

## A missed evening, caught up

An evening that does not complete -- the facts refused, the panel not updated, the machine off
-- leaves its session without a snapshot, and from then on every evening is refused by name
(`latest_snapshot` never settles across a gap): the book stops until the session is settled.
`run_catch_up` settles it **without a decision**: the same dry run and the same commit, but no
screen -- nobody screened the session -- so the session's survivors are taken to be the names of
the book's pending buys, and **no buy is cancelled** by a session nobody screened; the orders
still fill, are refused, carried, expire and cool down as the market makes them. Nothing new is
planned (`Settlement.without_plan`), and the book's first decision is carried as it was, so a
book only caught up since its opening is still built by its next evening's initial build. The
market is the session's own, read later; the caller settles the missed sessions oldest first --
`missing_sessions` names them -- and then runs tonight's evening. A catch-up out of order is
refused by `latest_snapshot`, naming the gap.

## What the caller owns

- **Only these four entry points.** The daily entry (`V2-P7-008`) calls `open_book`,
  `missing_sessions`, `run_catch_up` and `run_evening`, and never `settle_session`,
  `save_snapshot` or `book_state` directly: the transaction, the gap rule and the reproduction
  check live here, and a caller that settles or saves around them has none of the three.
- **The paper book's construction (D15).** `make_portfolio` builds a `PaperPortfolio` on the
  ledger it is given, closing over `PortfolioLimits(1, 1, 0)` and the protocol's costs; this
  module never builds one with real limits or costs (it may not import `strategy_view`, where
  the costs live). A factory that builds the book on any other ledger is refused before the
  session settles: the dry run would not be dry.
- **The two error classes of a broken store.** Every refusal about the book's state is a
  `PaperStateError`; a fault the store finds in its own documents arrives as the store's
  `PaperStateStoreError`, which `backtest/` cannot import. The daily entry catches **both**, by
  name (both are `RuntimeError`s, so neither hides in an `except ValueError`). Contract refusals
  (a missing reference price, a book that cannot be decided) are `ValueError`s.

This module lives under `backtest/` and reaches no store: the snapshot store arrives as
`PaperStateDocumentStore` and the ledger as `ReadableLedger` (`append` and `list`), the
`Protocol`s beside their consumers; `SQLitePortfolioLedger` satisfies the latter. The read is the
whole ledger, filtered to the session: a few rows a session, so years of them are cheap.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

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
    held_head,
    latest_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_payload,
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
    "CatchUpResult",
    "DryRunLedger",
    "EveningResult",
    "PaperLedgerConflictError",
    "ReadableLedger",
    "missing_sessions",
    "open_book",
    "run_catch_up",
    "run_evening",
]

Outcome = Literal["created", "unchanged"]

_REMEDY = (
    "Re-run with the journal-pinned as_of, held and delisted set, so that the run reads what the "
    "first run read; a ledger row is never deleted or rewritten to make a run fit"
)


class PaperLedgerConflictError(PaperStateError):
    """The paper ledger's rows for the session being run are not the run's own.

    Raised before any append for a row this run would not write, or would write with another
    payload, and -- for a session already settled -- for a row the session wrote that the
    ledger no longer holds; raised at the append when another writer put a different row under
    one of the run's ids after the run read the ledger. Each message names the session, the
    rows and the remedy. A `PaperStateError`, so the daily entry maps it to exit 1 with the
    book's other state faults, without reading the message.
    """


class ReadableLedger(Protocol):
    """The paper ledger as an evening needs it: appended to, and read back.

    `SQLitePortfolioLedger` satisfies it. The read is what lets a run see the rows a cut run of
    the same session left, before it appends one of its own.
    """

    def append(self, transition: PortfolioTransition) -> None:
        """Append idempotently or reject conflicting reuse of an order ID."""

    def list(self) -> Sequence[PortfolioTransition]:
        """Every transition held, in the order appended."""


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

    def list(self) -> tuple[PortfolioTransition, ...]:
        """`transitions`, read as `ReadableLedger` reads a ledger."""
        return self.transitions

    def append(self, transition: PortfolioTransition) -> None:
        """Keep `transition`; refuse another payload under an order id already held."""
        order_id = transition.order.order_id
        payload = _payload(transition)
        held = self._payloads.get(order_id)
        if held is not None:
            if held != payload:
                raise ValueError(f"portfolio order_id conflicts: {order_id}")
            return
        self._payloads[order_id] = payload
        self._transitions.append(transition)


@dataclass(frozen=True, slots=True, kw_only=True)
class EveningResult:
    """What one evening did, with the evening's settlement, decision and trade list.

    `created`: the session was settled, decided and committed tonight, and `snapshot` is the one
    saved. `unchanged`: the session was already settled and nothing was written; `snapshot` is
    the one held, and the settlement, decision and trade list are derived again from the
    snapshot before it and the inputs given -- which reproduced the held snapshot exactly, or
    the evening would have been refused.
    """

    outcome: Outcome
    snapshot: PaperBookSnapshot
    trade_list: TradeList
    decision: BookDecision
    settlement: Settlement


@dataclass(frozen=True, slots=True, kw_only=True)
class CatchUpResult:
    """What one catch-up did: `created` with the snapshot saved, or `unchanged` with the
    snapshot already held and nothing written -- in both, the session's settlement (derived
    again, and reproducing the held snapshot, when `unchanged`)."""

    outcome: Outcome
    snapshot: PaperBookSnapshot
    settlement: Settlement


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
    ledger: ReadableLedger,
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

    A session already settled is answered `unchanged`, writing nothing, with the settlement,
    decision and trade list derived again from the snapshot before it; a derivation that does
    not reproduce the held snapshot, or whose transitions are not exactly the ledger's rows for
    the session, is refused by name. A session not settled is refused before any append when the
    ledger holds a row for it that this run would not write, or would write otherwise
    (`PaperLedgerConflictError`).
    """
    digest = survivor_config_digest(config)
    held = _settled_already(store, session=session, session_index=session_index, digest=digest)
    previous = _settled_from(
        store, held=held, session=session, session_index=session_index, digest=digest
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
    if held is not None:
        _refuse_unless_reproduced(held, snapshot, run="evening", other="a catch-up")
        _refuse_rows_not_the_runs(ledger, settlement, settled=True)
        return EveningResult(
            outcome="unchanged",
            snapshot=held,
            trade_list=trade_list,
            decision=decision,
            settlement=settlement,
        )
    return EveningResult(
        outcome=_commit(store=store, ledger=ledger, settlement=settlement, snapshot=snapshot),
        snapshot=snapshot,
        trade_list=trade_list,
        decision=decision,
        settlement=settlement,
    )


def run_catch_up(
    *,
    store: PaperStateDocumentStore,
    ledger: ReadableLedger,
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    config: SurvivorBookConfig,
    session: date,
    session_index: int,
    market: SessionMarketLike,
    delisted: Collection[str],
    observed_on: date,
) -> CatchUpResult:
    """Settle the missed session `session` (ordinal `session_index`) without a decision; see the
    module docstring.

    The arguments are `run_evening`'s less the screen and the entrants' prices: the session's
    survivors are the names of the book's pending buys, so no buy is cancelled, and nothing is
    planned. The transaction is the evening's -- dry run, then the ledger rows, then the
    snapshot -- and so is every refusal, made before anything is written (the ledger read
    included), and so is the answer for a session already settled: `unchanged`, nothing
    written, the settlement derived again and refused by name unless it reproduces the held
    snapshot and the ledger's rows for the session.
    """
    digest = survivor_config_digest(config)
    held = _settled_already(store, session=session, session_index=session_index, digest=digest)
    previous = _settled_from(
        store, held=held, session=session, session_index=session_index, digest=digest
    )
    settlement = _settle_dry(
        previous=previous,
        make_portfolio=make_portfolio,
        session=session,
        session_index=session_index,
        market=market,
        survivors={
            item.order.ts_code for item in previous.pending_orders if item.order.side == "buy"
        },
        delisted=delisted,
        config=config,
        observed_on=observed_on,
    )
    snapshot = settlement.without_plan()
    if held is not None:
        _refuse_unless_reproduced(held, snapshot, run="catch-up", other="an evening")
        _refuse_rows_not_the_runs(ledger, settlement, settled=True)
        return CatchUpResult(outcome="unchanged", snapshot=held, settlement=settlement)
    return CatchUpResult(
        outcome=_commit(store=store, ledger=ledger, settlement=settlement, snapshot=snapshot),
        snapshot=snapshot,
        settlement=settlement,
    )


def missing_sessions(
    store: PaperStateDocumentStore, *, config_digest: str, through_index: int
) -> tuple[int, ...]:
    """The ordinals after the book's latest settled session, through `through_index`, that have
    no snapshot -- the sessions to catch up, oldest first, before the evening after them.

    The history grows only at its head, so these are every ordinal after the head up to
    `through_index`, and none when the head is at or past it. A store with no snapshot is
    refused by name (the book was never opened, so no session of it can be missing), and so is
    a book settled under a configuration whose digest is not `config_digest`.
    """
    head = held_head(store)
    if head is None:
        raise PaperStateError(
            "no paper book snapshot is held: the book was never opened, so it is missing no "
            "session; a paper book is opened by saving an opening snapshot (open_book)"
        )
    if head.config_digest != config_digest:
        raise PaperStateError(
            f"the paper book held in this store is settled under configuration digest "
            f"{head.config_digest} through {head.session.isoformat()}, and the sessions asked "
            f"about are of {config_digest}: another configuration is another book, with a "
            "runtime directory of its own"
        )
    return tuple(range(head.session_index + 1, through_index + 1))


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


def _settled_from(
    store: PaperStateDocumentStore,
    *,
    held: PaperBookSnapshot | None,
    session: date,
    session_index: int,
    digest: str,
) -> PaperBookSnapshot:
    """The book `session` settles from: `latest_snapshot`'s answer while the session is not
    settled, and -- when it is (`held`) -- the snapshot it was settled from, the one held for
    the session before it, read with `load_snapshot` because `latest_snapshot` refuses a
    settled session."""
    if held is None:
        return latest_snapshot(
            store, settling_session=session, settling_index=session_index, config_digest=digest
        )
    earlier = [day for day in store.sessions() if day < session]
    previous = load_snapshot(store, earlier[-1]) if earlier else None
    if previous is None or previous.session_index != session_index - 1:
        raise PaperStateError(
            f"{session.isoformat()} is settled, and the snapshot of session index "
            f"{session_index - 1} it was settled from is not the one held before it: the "
            "history was altered, and the session cannot be derived again"
        )
    return previous


def _refuse_unless_reproduced(
    held: PaperBookSnapshot, derived: PaperBookSnapshot, *, run: str, other: str
) -> None:
    """Refuse, by name, a settled session that this run does not reproduce byte for byte."""
    if derived.snapshot_id != held.snapshot_id or snapshot_payload(derived) != snapshot_payload(
        held
    ):
        raise PaperStateError(
            f"{held.session.isoformat()} is settled as {held.snapshot_id}, and this {run} "
            f"derives {derived.snapshot_id} from the snapshot before it: its inputs differ from "
            f"the ones the session was settled with, or the session was settled by {other}. "
            "A settled session is not settled again; the inputs of the run that settled it are "
            "the ones to run with"
        )


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


def _refuse_rows_not_the_runs(
    ledger: ReadableLedger, settlement: Settlement, *, settled: bool
) -> None:
    """Refuse, by name, a ledger whose rows for the session are not the settlement's own.

    The session's rows are those whose `after.as_of` is the session: a runner row carries the
    step's date, a write-off the session's. A row the settlement does not hold, or holds with
    another payload, is refused; when the session is `settled`, so is a settlement row the
    ledger does not hold -- the rows must be exactly the settlement's.
    """
    session = settlement.session
    with refusing_outward_calls():
        held = {row.order.order_id: row for row in ledger.list() if row.after.as_of == session}
    ours = {transition.order.order_id: transition for transition in settlement.transitions}
    found = []
    foreign = sorted(set(held) - set(ours))
    if foreign:
        found.append(f"rows this run would not write: {foreign}")
    changed = sorted(
        order_id
        for order_id in set(held) & set(ours)
        if _payload(held[order_id]) != _payload(ours[order_id])
    )
    if changed:
        found.append(f"rows this run would write otherwise: {changed}")
    missing = sorted(set(ours) - set(held)) if settled else []
    if missing:
        found.append(f"rows the session wrote that the ledger does not hold: {missing}")
    if found:
        raise PaperLedgerConflictError(
            f"{session.isoformat()}: the paper ledger's rows for this session are not this "
            f"run's -- {'; '.join(found)}. Nothing was written. {_REMEDY}"
        )


def _commit(
    *,
    store: PaperStateDocumentStore,
    ledger: ReadableLedger,
    settlement: Settlement,
    snapshot: PaperBookSnapshot,
) -> Outcome:
    """Read the session's rows, append the session's transitions to the real ledger in order,
    then save the snapshot."""
    _refuse_rows_not_the_runs(ledger, settlement, settled=False)
    rows = settlement.transitions
    with refusing_outward_calls():
        for count, transition in enumerate(rows):
            try:
                ledger.append(transition)
            except ValueError as error:
                raise PaperLedgerConflictError(
                    f"{settlement.session.isoformat()}: the paper ledger refused "
                    f"{transition.order.order_id}, with {count} of the session's {len(rows)} "
                    f"rows in ({error}): another payload under that id was appended after this "
                    "run read the ledger, so another writer is running this book. No snapshot "
                    f"was saved; stop the other writer. {_REMEDY}"
                ) from error
    return save_snapshot(store, snapshot)


def _payload(transition: PortfolioTransition) -> str:
    """The bytes a ledger compares two transitions by (`SQLitePortfolioLedger.append`'s)."""
    return transition.model_dump_json(exclude_computed_fields=True)
