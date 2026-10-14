"""The paper book's state at the close of one settled session (`V2-P7-017`).

T5 (`docs/research/p7-step0-validation/T5-paper-step-builder.md` section 6) is why this exists. The
paper ledger stores transitions and nothing else, and a session on which the book trades nothing
writes no row (`multi_day.PortfolioBacktestRunner.run`), so neither the book's `as_of` nor its
marks can be rebuilt from the ledger; and the two things the next session needs that no ledger
row carries at all -- each holding's **entry adjustment factor** and the **orders still live** --
exist nowhere else. A `PaperBookSnapshot` is all of it, for one session:

- `session_index` and `session`: the session in the trading calendar's ordinals (the ones
  `TradeOrder` counts in) and its date;
- `book`: the `PortfolioState` `PaperPortfolio.advance` continues from -- cash, every holding's
  lots (share count and cost basis in the ledger's units, and each lot's `open_date`, which T+1
  and FIFO read), a mark for every holding, realised profit and fees -- with `book.as_of` equal to
  `session`, which is what `advance` checks the next session against;
- `entry_factors`: one `EntryFactor` per held name, the `adj_factor` of the session its **buy
  filled** (`paper_step`'s convention: the ledger keeps the shares it bought, and every later
  price of that name is scaled by `adj_factor(session) / entry_adj`). `build_paper_step` and
  `remark_at_close` refuse a held name with no ratio rather than read it as 1, and this snapshot
  is the ratio's only source, so **a held name without an entry factor is refused here, by name,
  including when a stored snapshot is read back** -- a forgotten entry never becomes a 1;
- `pending_orders`: every live order, buys and sells, each with the number of sessions it has
  been tried on, held in `advance_orders`' own order `(side, ts_code, order_id)`;
- `cooldowns`: the `Cooldown`s by code;
- `last_settled_session`: the session whose fills and order lifecycle are in this book --
  `session_index` itself, or `None` for an opening book that has settled nothing.

## What is refused, by name

A snapshot is a book some sequence of settled sessions could have produced, so the shapes no
such sequence produces are refused rather than carried: a holding in more than one lot (the
survivor book never adds to a held name) and a lot opened after the snapshot's session (the
look-ahead guard for the book); a holding with no entry factor and a factor for a name not held;
`BookState`'s own invariants on the live orders (one buy per name, no buy for a held name, a sell
only for a held name, and no more shares than are held); an order planned after the snapshot's
session (the look-ahead guard for orders); in an opening book, an order due on or before its own
session, which the book never settles; a buy still live past its last session once that session
has settled (`advance_orders` would have expired it); an attempt count other than the number of
sessions settled since the order was due (every due order is handed to the step on every settled
session, a session with no bar included, so the count is exact, not a bound); a cool-down
starting after the snapshot's session, and one for a name held (a cool-down starts when a sale
fills or a buy expires, and is dropped before the policy may buy the name again). A pending buy
for a name still cooling down is **not** refused: with `cooldown_sessions=0` the policy may plan
it on the evening the cool-down started. The parts are sorted on the way in, so a snapshot's
address does not depend on the order a caller assembled them in.

## Identity, bytes, and reading back

`snapshot_id` is `stable_model_id(prefix="pbs", ...)` -- the one canonicalisation, under a prefix
the census in `tests/unit/domain/test_manifest_component_provenance.py::live_prefixes` showed
free. `snapshot_payload` is the snapshot's canonical bytes, and `open_snapshot` is the one reader:
it parses the payload, refuses a document filed under another session, re-derives the address
and refuses one that does not match the name it was filed under, and refuses bytes that are not
the canonical ones. So a snapshot reads back byte for byte or not at all, and a missing, damaged
or edited snapshot is refused by name -- **never taken for an empty book**. A book is opened by
saving an opening snapshot (`last_settled_session=None`) on purpose; no reader starts one.

Those two refusals cannot tell an edit from a change of contract: `model_dump_json` writes the
fields in declaration order, so a field added to `PortfolioState`, `PositionLot`, `TradeOrder` or
`Cooldown` (even with a default), or two of their fields swapped, makes every stored snapshot
unreadable or non-canonical. The messages say both causes, and
`tests/unit/backtest/test_paper_state.py` pins the digest of `PaperBookSnapshot`'s JSON schema so
such a change goes red in CI as "needs a migration of stored snapshots" first.

A settlement asks for its book with `latest_snapshot(store, settling_session=..., settling_index=
...)`, which answers only with the snapshot of the session just before the one being settled:
an empty store, a session already settled, a snapshot dated after it, and a missing session in
between are each refused by name. The history therefore only grows at its head, and
`save_snapshot` holds the writing side of that: a snapshot dated before the latest one held is
refused (the same snapshot again for a held session still writes nothing).

The bytes are kept by a store this module does not import: `backtest-studies-touch-no-store`
keeps every module here away from `openalpha_cn.storage`, so `PaperStateDocumentStore` is the
`Protocol` declared beside its consumer and `storage/paper_state.FilePaperStateStore` satisfies
it structurally, written once per session and atomically. The composition root wires that store
and the paper book's own ledger.

## What this module does not decide

- **When in the evening a snapshot is written**, and so whether its `pending_orders` include the
  orders planned that evening, is the daily settlement's (`V2-P7-009`); the contract holds
  either. `BookState.initialized` is not stored: it is derivable from whether the book has
  settled a session, and that derivation belongs with the write timing.
- **The orders' identity.** A snapshot embeds `TradeOrder` (`trade-order/v1`), whose identity is
  still an open item (P7 section 9, the prerequisites of plan 2, item 4). Nothing in this module
  writes a snapshot on a schedule, so nothing is on disk yet; the first real snapshot must follow
  that decision, or a change to `TradeOrder` becomes a migration of stored snapshots.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Final, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.portfolio import PortfolioState
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown

__all__ = [
    "PAPER_BOOK_SNAPSHOT_PREFIX",
    "EntryFactor",
    "PaperBookSnapshot",
    "PaperStateDocumentStore",
    "PaperStateError",
    "PendingOrder",
    "latest_snapshot",
    "load_snapshot",
    "open_snapshot",
    "save_snapshot",
    "snapshot_payload",
]

PAPER_BOOK_SNAPSHOT_PREFIX: Final[str] = "pbs"
"""The content-address prefix of a paper-book snapshot; no other contract stamps it."""


class PaperStateError(RuntimeError):
    """A snapshot that is missing, cannot be read, does not match its address, or would be a
    second answer for a settled session.

    A `RuntimeError` rather than a `ValueError`: a contract that refuses to be built raises
    `ValueError`, and a caller's `except ValueError` around one must not swallow "the book's
    state is gone".
    """


class EntryFactor(BaseModel):
    """The `adj_factor` of the session a holding's buy filled: the base of its ratio."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    entry_adj: Decimal = Field(gt=0)


class PendingOrder(BaseModel):
    """A live order and the number of sessions it has been tried on.

    A try is one session on which the order was due and handed to the step builder, whether it
    was submitted to the paper book or not attempted for want of a bar; each try is recorded in
    the ledger, if at all, under its own `paper_step.attempt_id`. The count is therefore exactly
    the number of settled sessions since the order was due, and `PaperBookSnapshot` refuses any
    other: stored rather than derived so the document says it without the rule, and so that
    loosening the rule later would be a change of check, not a change of shape.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    order: TradeOrder
    attempts: int = Field(ge=0)


class PaperBookSnapshot(BaseModel):
    """The paper book at the close of one session; see the module docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["paper-book-snapshot/v1"] = "paper-book-snapshot/v1"
    session_index: int = Field(ge=0)
    session: date
    book: PortfolioState
    entry_factors: tuple[EntryFactor, ...] = ()
    pending_orders: tuple[PendingOrder, ...] = ()
    cooldowns: tuple[Cooldown, ...] = ()
    last_settled_session: int | None = Field(ge=0)
    """`session_index` once the session's fills and order lifecycle are in the book; `None` for
    an opening book that has settled nothing."""

    @field_validator("entry_factors")
    @classmethod
    def _entry_factors_by_code(cls, value: tuple[EntryFactor, ...]) -> tuple[EntryFactor, ...]:
        return tuple(sorted(value, key=lambda item: item.ts_code))

    @field_validator("pending_orders")
    @classmethod
    def _pending_orders_in_lifecycle_order(
        cls, value: tuple[PendingOrder, ...]
    ) -> tuple[PendingOrder, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (item.order.side, item.order.ts_code, item.order.order_id),
            )
        )

    @field_validator("cooldowns")
    @classmethod
    def _cooldowns_by_code(cls, value: tuple[Cooldown, ...]) -> tuple[Cooldown, ...]:
        return tuple(sorted(value, key=lambda item: item.ts_code))

    @model_validator(mode="after")
    def _a_book_settled_sessions_could_have_produced(self) -> Self:
        if self.book.as_of != self.session:
            raise ValueError(
                f"the book is as of {self.book.as_of.isoformat()}, the snapshot is for "
                f"{self.session.isoformat()}"
            )
        if self.last_settled_session not in (None, self.session_index):
            raise ValueError(
                f"the book is settled through session {self.last_settled_session} but is as of "
                f"session {self.session_index}; a snapshot is written once its session has "
                "settled, or opens a book that has settled nothing"
            )
        self._check_positions()
        self._check_entry_factors()
        self._check_pending_orders()
        self._check_cooldowns()
        return self

    def _check_entry_factors(self) -> None:
        codes = [item.ts_code for item in self.entry_factors]
        _refuse("entry adjustment factors given more than once", _repeated(codes))
        held = {position.subject for position in self.book.positions}
        _refuse("held names with no entry adjustment factor", held - set(codes))
        _refuse("entry adjustment factors for names not held", set(codes) - held)

    def _check_pending_orders(self) -> None:
        orders = [item.order for item in self.pending_orders]
        repeated = _repeated(order.order_id for order in orders)
        _refuse(
            "orders pending more than once",
            (_named(order) for order in orders if order.order_id in repeated),
        )
        _refuse(
            f"orders planned after session {self.session_index}",
            (_named(order) for order in orders if order.signal_session > self.session_index),
        )
        held = {position.subject: position.quantity for position in self.book.positions}
        buys = [order for order in orders if order.side == "buy"]
        sells = [order for order in orders if order.side == "sell"]
        _refuse("names with more than one pending buy", _repeated(o.ts_code for o in buys))
        _refuse("names with more than one pending sell", _repeated(o.ts_code for o in sells))
        _refuse(
            "pending buys for names already held", (o.ts_code for o in buys if o.ts_code in held)
        )
        _refuse(
            "pending sells for names not held", (o.ts_code for o in sells if o.ts_code not in held)
        )
        _refuse(
            "pending sells for more shares than are held",
            (o.ts_code for o in sells if o.ts_code in held and o.quantity > held[o.ts_code]),
        )
        settled = self.last_settled_session
        if settled is None:
            _refuse(
                "orders an opening book has not settled the due session of",
                (_named(order) for order in orders if order.execute_session <= self.session_index),
            )
        else:
            _refuse(
                f"buys still pending after their last session {settled}",
                (
                    _named(order)
                    for order in buys
                    if order.expires_session is not None and order.expires_session <= settled
                ),
            )
        _refuse(
            "orders not tried on every session settled since they were due",
            (
                f"{_named(item.order)} tried {item.attempts}, due on "
                f"{_sessions(_tries(item.order, settled))}"
                for item in self.pending_orders
                if item.attempts != _tries(item.order, settled)
            ),
        )

    def _check_positions(self) -> None:
        positions = self.book.positions
        _refuse(
            "held names not in exactly one lot",
            (position.subject for position in positions if len(position.lots) != 1),
        )
        _refuse(
            f"lots opened after session {self.session.isoformat()}",
            (
                position.subject
                for position in positions
                if any(lot.open_date > self.session for lot in position.lots)
            ),
        )

    def _check_cooldowns(self) -> None:
        codes = [item.ts_code for item in self.cooldowns]
        _refuse("cool-downs given more than once", _repeated(codes))
        _refuse(
            f"cool-downs starting after session {self.session_index}",
            (item.ts_code for item in self.cooldowns if item.since_session > self.session_index),
        )
        held = {position.subject for position in self.book.positions}
        _refuse("cool-downs for names held", set(codes) & held)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def snapshot_id(self) -> str:
        return stable_model_id(prefix=PAPER_BOOK_SNAPSHOT_PREFIX, model=self)

    def entry_factors_by_code(self) -> dict[str, Decimal]:
        """Each held name's entry factor -- the base of `paper_step`'s adjustment ratios."""
        return {item.ts_code: item.entry_adj for item in self.entry_factors}


class PaperStateDocumentStore(Protocol):
    """What `save_snapshot`/`load_snapshot` need from a store: bytes per session, written once.

    `storage/paper_state.FilePaperStateStore` satisfies it with no import in either direction.
    """

    def put(
        self, *, session: date, snapshot_id: str, payload: str
    ) -> Literal["created", "unchanged"]:
        """Hold `payload` for `session` unless it is held; refuse a different id for it."""

    def get(self, session: date) -> tuple[str, str] | None:
        """`(snapshot_id, payload)` held for `session`, or `None`."""

    def sessions(self) -> tuple[date, ...]:
        """Every session held, ascending."""


def snapshot_payload(snapshot: PaperBookSnapshot) -> str:
    """The snapshot's canonical bytes: what is written, and what a read must find again.

    Computed fields (the snapshot's and the orders' addresses, the book's equity) are left out --
    a reader re-derives them, which is what lets `open_snapshot` notice an edit.
    """
    return snapshot.model_dump_json(exclude_computed_fields=True)


def open_snapshot(payload: str, *, snapshot_id: str, session: date) -> PaperBookSnapshot:
    """The snapshot `payload` holds, filed as `snapshot_id` for `session`, or a refusal by name."""
    try:
        snapshot = PaperBookSnapshot.model_validate_json(payload)
    except ValidationError as error:
        raise PaperStateError(
            f"the paper book's snapshot for {session.isoformat()} ({snapshot_id}) cannot be read, "
            f"and a snapshot that cannot be read is refused rather than taken for an empty book: "
            f"{error}"
        ) from error
    if snapshot.session != session:
        raise PaperStateError(
            f"the snapshot filed under {session.isoformat()} is for {snapshot.session.isoformat()}"
        )
    if snapshot.snapshot_id != snapshot_id:
        raise PaperStateError(
            f"the paper book's snapshot for {session.isoformat()} is filed as {snapshot_id} and "
            f"its content is {snapshot.snapshot_id}: it was edited, or written by a build whose "
            "contracts differ from this one's (a change to an embedded contract needs a "
            "migration of stored snapshots)"
        )
    if snapshot_payload(snapshot) != payload:
        raise PaperStateError(
            f"the paper book's snapshot for {session.isoformat()} ({snapshot_id}) is not in its "
            "canonical bytes: it was edited, or written by a build whose contracts differ from "
            "this one's (a change to an embedded contract needs a migration of stored snapshots)"
        )
    return snapshot


def save_snapshot(
    store: PaperStateDocumentStore, snapshot: PaperBookSnapshot
) -> Literal["created", "unchanged"]:
    """Write `snapshot` as its session's, once; the same snapshot again writes nothing.

    A different snapshot for a session already held is refused, naming both addresses, and a held
    copy that no longer reads back is refused rather than papered over by an intact newcomer.

    **The history grows only at its head.** A snapshot dated before the latest one held is
    refused, by design rather than by accident: every snapshot after the opening one is settled
    from the one before it, and `latest_snapshot` refuses to answer while a later-dated snapshot
    is held, so a write behind the head can only come from a writer that skipped that read, and
    the snapshot it slips in is not the book the later ones were built from. Accepting it would
    make a missing session look repaired after the fact. A gap is mended by settling forward from
    the last good snapshot, never by back-filling.
    """
    held = store.get(snapshot.session)
    if held is not None:
        held_id, held_payload = held
        if held_id != snapshot.snapshot_id:
            raise PaperStateError(
                f"{snapshot.session.isoformat()} is already settled as {held_id} and this "
                f"snapshot is {snapshot.snapshot_id}; a session settles once, so a second answer "
                "for it is a run that did not reproduce"
            )
        open_snapshot(held_payload, snapshot_id=held_id, session=snapshot.session)
        return "unchanged"
    sessions = store.sessions()
    if sessions and sessions[-1] > snapshot.session:
        raise PaperStateError(
            f"{snapshot.session.isoformat()} is dated before the latest snapshot held, "
            f"{sessions[-1].isoformat()}; the book's history grows only at its head, so a "
            "snapshot written behind it is refused rather than back-filled"
        )
    return store.put(
        session=snapshot.session,
        snapshot_id=snapshot.snapshot_id,
        payload=snapshot_payload(snapshot),
    )


def load_snapshot(store: PaperStateDocumentStore, session: date) -> PaperBookSnapshot:
    """The snapshot of `session`, refused by name when it is missing, damaged or edited."""
    held = store.get(session)
    if held is None:
        raise PaperStateError(
            f"no paper book snapshot is held for {session.isoformat()}; a missing snapshot is "
            "refused rather than taken for an empty book"
        )
    held_id, payload = held
    return open_snapshot(payload, snapshot_id=held_id, session=session)


def latest_snapshot(
    store: PaperStateDocumentStore, *, settling_session: date, settling_index: int
) -> PaperBookSnapshot:
    """The book `settling_session` settles from: the snapshot of the session just before it.

    `settling_session` is the session about to be settled and `settling_index` its ordinal in
    the trading calendar. The answer is the latest snapshot dated before `settling_session`,
    and it must be the snapshot of ordinal `settling_index - 1`; everything else is refused by
    name, because each wrong answer settles the book from a state it was not in:

    - **an empty store** -- a paper book is opened by saving an opening snapshot on purpose,
      never started from zero by whoever reads it next;
    - **a snapshot dated on or after `settling_session`** -- one equal to it means the session
      is already settled (a second settlement would write its fills twice), and one after it
      knows a later session, which is the look-ahead rule broken at the book's own state;
    - **a missing session in between** -- if the evening run of N-1 stopped after its ledger
      rows and before its snapshot, settling N from N-2's book would hand the orders due on
      N-1 to N's step under new attempt ids, and the ledger would hold two fills of the same
      orders. The latest earlier snapshot must be ordinal `settling_index - 1`, so a gap is
      named, never skipped.
    """
    sessions = store.sessions()
    if not sessions:
        raise PaperStateError(
            "no paper book snapshot is held at all: a paper book is opened by saving an opening "
            "snapshot, never started from zero by a reader"
        )
    settling = settling_session.isoformat()
    if settling_session in sessions:
        raise PaperStateError(
            f"{settling} is already settled: its snapshot is held, and a session settles once"
        )
    later = [held.isoformat() for held in sessions if held > settling_session]
    if later:
        raise PaperStateError(
            f"snapshots dated after {settling}, the session being settled, are held: {later}; "
            "a book is never settled from a state that knows a later session"
        )
    snapshot = load_snapshot(store, sessions[-1])
    if snapshot.session_index != settling_index - 1:
        raise PaperStateError(
            f"the snapshot for session index {settling_index - 1} is missing: the latest is "
            f"{snapshot.session_index} ({snapshot.session.isoformat()}), and settling "
            f"{settling} (session index {settling_index}) from it would skip the sessions "
            "between them rather than settle them"
        )
    return snapshot


def _tries(order: TradeOrder, settled: int | None) -> int:
    """How many sessions a live order has been tried on: every settled one from its execution on.

    Exact rather than a bound, because nothing a book does lets a due order miss a settled
    session: every due order is handed to the step, a session with no bar for it is still a try
    (`paper_step`'s `not_attempted`), `latest_snapshot` refuses to settle across a missing
    session, and an opening book holds no order due on or before its own session.
    """
    if settled is None:
        return 0
    return max(0, settled - order.execute_session + 1)


def _sessions(count: int) -> str:
    return f"{count} settled session" if count == 1 else f"{count} settled sessions"


def _named(order: TradeOrder) -> str:
    return f"{order.order_id} ({order.side} {order.ts_code})"


def _repeated(items: Iterable[str]) -> set[str]:
    return {item for item, count in Counter(items).items() if count > 1}


def _refuse(what: str, offenders: Iterable[str]) -> None:
    named = sorted(set(offenders))
    if named:
        raise ValueError(f"{what}: {named}")
