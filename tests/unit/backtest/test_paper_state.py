"""The paper book's daily state snapshot and its ledger of its own (`V2-P7-017`).

Every fixture is hand-written with invented prices on the Shanghai and Shenzhen main boards, and
every number asserted is worked out in the comment beside it. Sessions: S0 2021-03-01 is the
session the book opens on (ordinal 100); S1 ... S5 are the five sessions after it.

The replay test drives a book through five sessions with `_settle`, a minimal pure function that
does cash-and-holdings bookkeeping only -- no execution rules, no `PaperPortfolio` -- because the
property under test is the snapshot's, not the simulator's: a snapshot written to disk and read
back carries everything the next session needs, so replaying from it reaches the same book as
never having stopped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.backtest.paper_state import (
    PAPER_BOOK_SNAPSHOT_PREFIX,
    EntryFactor,
    PaperBookSnapshot,
    PaperStateError,
    PendingOrder,
    latest_snapshot,
    load_snapshot,
    open_snapshot,
    save_snapshot,
    snapshot_payload,
)
from openalpha_cn.domain._identity import CONTENT_ADDRESS_PATTERN
from openalpha_cn.domain.portfolio import (
    PortfolioOrder,
    PortfolioPosition,
    PortfolioState,
    PortfolioTransition,
    PositionLot,
    PositionMark,
)
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.paper_state import FilePaperStateStore, PaperStateStoreError
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

D = Decimal
CENT = D("0.01")
A, B, C, X, Y = "600000.SH", "600036.SH", "000001.SZ", "600519.SH", "600104.SH"
S0, S1, S2, S3, S4, S5 = (
    date(2021, 3, 1),
    date(2021, 3, 2),
    date(2021, 3, 3),
    date(2021, 3, 4),
    date(2021, 3, 5),
    date(2021, 3, 8),
)
INDEX = {S0: 100, S1: 101, S2: 102, S3: 103, S4: 104, S5: 105}


def buy(code: str, quantity: int, *, signal: int, expires: int, price: str) -> TradeOrder:
    return TradeOrder(
        signal_session=signal,
        execute_session=signal + 1,
        expires_session=expires,
        side="buy",
        ts_code=code,
        quantity=quantity,
        reference_price=D(price),
        reason="INITIAL_BUILD" if signal == 100 else "ENTRY_REPLACEMENT",
    )


def sell(code: str, quantity: int, *, signal: int, price: str) -> TradeOrder:
    return TradeOrder(
        signal_session=signal,
        execute_session=signal + 1,
        side="sell",
        ts_code=code,
        quantity=quantity,
        reference_price=D(price),
        reason="EXIT_SURVIVOR_LOST",
    )


def lot(code: str, quantity: int, basis: str, *, bought: date = S1) -> PortfolioPosition:
    return PortfolioPosition(
        subject=code,
        lots=(PositionLot(open_date=bought, quantity=quantity, cost_basis=D(basis)),),
    )


def held_snapshot(**overrides: Any) -> PaperBookSnapshot:
    """S2, settled: A (1,000 at 10,005.00, entry 2.000) and B (2,000 at 40,805.00, entry 1.000)
    held; a sell of A and a buy of C live; X cooling down since S1."""
    fields: dict[str, Any] = {
        "session_index": 102,
        "session": S2,
        "book": PortfolioState(
            as_of=S2,
            cash=D("49190.00"),
            positions=(lot(A, 1_000, "10005.00"), lot(B, 2_000, "40805.00")),
            marks=(
                PositionMark(subject=A, price=D("10.58")),
                PositionMark(subject=B, price=D("20.80")),
            ),
            fees_paid=D("10.00"),
        ),
        "entry_factors": (
            EntryFactor(ts_code=A, entry_adj=D("2.000")),
            EntryFactor(ts_code=B, entry_adj=D("1.000")),
        ),
        "pending_orders": (
            PendingOrder(order=sell(A, 1_000, signal=102, price="10.58"), attempts=0),
            PendingOrder(order=buy(C, 500, signal=102, expires=106, price="12.00"), attempts=0),
        ),
        "cooldowns": (Cooldown(ts_code=X, since_session=101),),
        "last_settled_session": 102,
    }
    return PaperBookSnapshot(**{**fields, **overrides})


def opening_snapshot() -> PaperBookSnapshot:
    """S0, opened with 100,000.00 and the first build's three buys planned on it; nothing settled.

    X's buy expires on S1, the session it executes, so if it does not fill there it is gone.
    """
    return PaperBookSnapshot(
        session_index=100,
        session=S0,
        book=PortfolioState(as_of=S0, cash=D("100000.00")),
        pending_orders=(
            PendingOrder(order=buy(A, 1_000, signal=100, expires=103, price="10.00"), attempts=0),
            PendingOrder(order=buy(B, 2_000, signal=100, expires=103, price="20.00"), attempts=0),
            PendingOrder(order=buy(X, 300, signal=100, expires=101, price="30.00"), attempts=0),
        ),
        last_settled_session=None,
    )


# --- the contract -------------------------------------------------------------------------


def test_the_snapshot_is_addressed_by_the_one_hash_function_under_its_own_prefix() -> None:
    snapshot = held_snapshot()

    assert PAPER_BOOK_SNAPSHOT_PREFIX == "pbs"
    assert snapshot.snapshot_id.startswith("pbs_")
    assert re.fullmatch(CONTENT_ADDRESS_PATTERN, snapshot.snapshot_id)
    assert held_snapshot().snapshot_id == snapshot.snapshot_id


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "entry_factors": (
                EntryFactor(ts_code=A, entry_adj=D("2.001")),
                EntryFactor(ts_code=B, entry_adj=D("1.000")),
            )
        },
        {"cooldowns": (Cooldown(ts_code=X, since_session=100),)},
        {
            "pending_orders": (
                PendingOrder(order=sell(A, 1_000, signal=102, price="10.58"), attempts=0),
                PendingOrder(order=buy(C, 500, signal=101, expires=106, price="12.00"), attempts=1),
            )
        },
    ],
    ids=["an entry factor", "a cool-down", "a carried order"],
)
def test_every_part_of_the_state_moves_the_address(overrides: dict[str, Any]) -> None:
    assert held_snapshot(**overrides).snapshot_id != held_snapshot().snapshot_id


def test_the_parts_are_held_in_one_canonical_order_whatever_order_they_arrive_in() -> None:
    """Pending orders by `(side, ts_code, order_id)` -- `advance_orders`' own order, so buys
    first -- and entry factors and cool-downs by code; the address does not see arrival order."""
    snapshot = held_snapshot()  # its two orders arrive sell first
    cooling = (Cooldown(ts_code=X, since_session=101), Cooldown(ts_code=Y, since_session=99))
    shuffled = held_snapshot(
        entry_factors=tuple(reversed(snapshot.entry_factors)),
        pending_orders=tuple(reversed(snapshot.pending_orders)),
        cooldowns=cooling,
    )
    reordered = held_snapshot(cooldowns=cooling[::-1])

    assert [p.order.side for p in snapshot.pending_orders] == ["buy", "sell"]
    assert [e.ts_code for e in shuffled.entry_factors] == [A, B]
    assert [c.ts_code for c in shuffled.cooldowns] == [Y, X]  # 600104 before 600519
    assert shuffled == reordered
    assert shuffled.snapshot_id == reordered.snapshot_id
    assert snapshot_payload(shuffled) == snapshot_payload(reordered)


def test_the_entry_factors_are_the_held_names_and_nothing_else() -> None:
    assert held_snapshot().entry_factors_by_code() == {A: D("2.000"), B: D("1.000")}


def test_a_held_name_without_an_entry_factor_is_refused_by_name() -> None:
    """`build_paper_step` refuses a sell with no adjustment ratio and the snapshot is the ratio's
    only source, so a holding whose entry factor is missing is refused here -- never read as 1."""
    with pytest.raises(
        ValueError, match=r"held names with no entry adjustment factor: \['600036\.SH'\]"
    ):
        held_snapshot(entry_factors=(EntryFactor(ts_code=A, entry_adj=D("2.000")),))


def test_an_entry_factor_for_a_name_not_held_is_refused_by_name() -> None:
    extra = (*held_snapshot().entry_factors, EntryFactor(ts_code=C, entry_adj=D("1.500")))

    with pytest.raises(
        ValueError, match=r"entry adjustment factors for names not held: \['000001\.SZ'\]"
    ):
        held_snapshot(entry_factors=extra)


def test_an_entry_factor_is_positive_and_given_once() -> None:
    with pytest.raises(ValueError):
        EntryFactor(ts_code=A, entry_adj=D("0"))
    twice = (*held_snapshot().entry_factors, EntryFactor(ts_code=A, entry_adj=D("2.000")))
    with pytest.raises(
        ValueError, match=r"entry adjustment factors given more than once: \['600000\.SH'\]"
    ):
        held_snapshot(entry_factors=twice)


@pytest.mark.parametrize(
    ("pending", "refusal"),
    [
        (
            (PendingOrder(order=buy(A, 100, signal=102, expires=105, price="10.58"), attempts=0),),
            r"pending buys for names already held: \['600000\.SH'\]",
        ),
        (
            (PendingOrder(order=sell(C, 500, signal=102, price="12.00"), attempts=0),),
            r"pending sells for names not held: \['000001\.SZ'\]",
        ),
        (
            (PendingOrder(order=sell(A, 1_100, signal=102, price="10.58"), attempts=0),),
            r"pending sells for more shares than are held: \['600000\.SH'\]",
        ),
        (
            (
                PendingOrder(order=buy(C, 500, signal=102, expires=106, price="12.00"), attempts=0),
                PendingOrder(order=buy(C, 400, signal=101, expires=105, price="12.00"), attempts=1),
            ),
            r"names with more than one pending buy: \['000001\.SZ'\]",
        ),
        (
            (
                PendingOrder(order=sell(A, 1_000, signal=102, price="10.58"), attempts=0),
                PendingOrder(order=sell(A, 1_000, signal=101, price="10.40"), attempts=1),
            ),
            r"names with more than one pending sell: \['600000\.SH'\]",
        ),
        (
            (
                PendingOrder(order=sell(A, 1_000, signal=102, price="10.58"), attempts=0),
                PendingOrder(order=sell(A, 1_000, signal=102, price="10.58"), attempts=0),
            ),
            r"orders pending more than once: \['tor_",
        ),
    ],
    ids=["buy-held", "sell-unheld", "sell-oversize", "two-buys", "two-sells", "repeated"],
)
def test_pending_orders_that_no_book_could_hold_are_refused_by_name(
    pending: tuple[PendingOrder, ...], refusal: str
) -> None:
    with pytest.raises(ValueError, match=refusal):
        held_snapshot(pending_orders=pending)


def test_an_order_planned_after_the_snapshot_session_is_refused_as_look_ahead() -> None:
    from_tomorrow = PendingOrder(
        order=buy(C, 500, signal=103, expires=106, price="12.00"), attempts=0
    )

    with pytest.raises(ValueError, match=r"orders planned after session 102: \['tor_"):
        held_snapshot(pending_orders=(from_tomorrow,))


def test_a_buy_left_pending_past_its_last_session_is_refused() -> None:
    """`advance_orders` expires a buy once the settled session reaches `expires_session`, so a
    settled book holding one has skipped its settlement."""
    stale = PendingOrder(order=buy(C, 500, signal=100, expires=102, price="12.00"), attempts=2)

    with pytest.raises(
        ValueError, match=r"buys still pending after their last session 102: \['tor_"
    ):
        held_snapshot(pending_orders=(stale,))


def test_an_order_has_been_tried_on_exactly_the_sessions_settled_since_it_was_due() -> None:
    """Every due order is handed to the step on every settled session, and a session with no
    bar still counts as a try, so the count is fixed by the order and the book: planned on 101,
    due on 102, settled through 102 -- tried once, no more and no fewer. An order not due yet
    (planned on 102, due on 103) has been tried on none."""
    due_once = PendingOrder(order=buy(C, 500, signal=101, expires=106, price="12.00"), attempts=1)
    held_snapshot(pending_orders=(due_once,))  # the one count that is possible

    for attempts in (0, 2):
        with pytest.raises(
            ValueError,
            match=rf"orders not tried on every session settled since they were due: "
            rf"\['tor_[0-9a-f]{{24}} \(buy 000001\.SZ\) tried {attempts}, due on 1 settled "
            r"session'\]",
        ):
            held_snapshot(pending_orders=(due_once.model_copy(update={"attempts": attempts}),))
    not_due = PendingOrder(order=buy(C, 500, signal=102, expires=106, price="12.00"), attempts=1)
    with pytest.raises(
        ValueError, match=r"orders not tried on every session settled since they were due"
    ):
        held_snapshot(pending_orders=(not_due,))


def test_an_opening_book_holds_no_order_due_on_a_session_it_will_never_settle() -> None:
    """An opening book has settled nothing and its first settlement is the session after it, so
    an order due on or before the opening session would be tried late -- and its count could no
    longer equal the sessions settled since it was due. Refused at the opening, by name."""
    overdue = PendingOrder(order=buy(A, 1_000, signal=99, expires=103, price="10.00"), attempts=0)

    with pytest.raises(
        ValueError,
        match=r"orders an opening book has not settled the due session of: \['tor_",
    ):
        PaperBookSnapshot(
            session_index=100,
            session=S0,
            book=PortfolioState(as_of=S0, cash=D("100000.00")),
            pending_orders=(overdue,),
            last_settled_session=None,
        )


def test_the_address_commits_to_the_attempt_count_alone() -> None:
    """Two snapshots the contract accepts cannot differ in an attempt count alone (the count is
    fixed by the order and the book), so the isolated case is a copy that skips validation: the
    address still moves. The same edit made on disk is refused by name
    (`test_an_attempt_count_edited_on_disk_is_refused_by_name`)."""
    snapshot = held_snapshot()
    (buy_c, sell_a) = snapshot.pending_orders
    recounted = snapshot.model_copy(
        update={"pending_orders": (buy_c.model_copy(update={"attempts": 1}), sell_a)}
    )

    assert recounted.pending_orders[0].order == snapshot.pending_orders[0].order
    assert recounted.snapshot_id != snapshot.snapshot_id


def test_a_cool_down_from_after_the_snapshot_or_given_twice_is_refused() -> None:
    with pytest.raises(
        ValueError, match=r"cool-downs starting after session 102: \['600519\.SH'\]"
    ):
        held_snapshot(cooldowns=(Cooldown(ts_code=X, since_session=103),))
    with pytest.raises(ValueError, match=r"cool-downs given more than once: \['600519\.SH'\]"):
        held_snapshot(
            cooldowns=(
                Cooldown(ts_code=X, since_session=101),
                Cooldown(ts_code=X, since_session=100),
            )
        )


def test_a_cool_down_for_a_name_held_is_refused_by_name() -> None:
    """`update_cooldowns` starts a cool-down when a sale fills or a buy expires -- the name is
    not held either way -- and the policy plans no buy for a name until its cool-down has run
    out, which is the session `update_cooldowns` drops it on. So no settled book holds a name
    that is cooling down."""
    with pytest.raises(ValueError, match=r"cool-downs for names held: \['600000\.SH'\]"):
        held_snapshot(cooldowns=(Cooldown(ts_code=A, since_session=101),))


def test_a_pending_buy_for_a_name_cooling_down_is_carried_not_refused() -> None:
    """With `cooldown_sessions=0`, which `SurvivorBookConfig` allows, a name whose sale filled on
    S2 is free again that evening (`session - since_session < 0` never holds), so the policy can
    plan its buy while the cool-down `update_cooldowns` started on S2 is still listed. A shape
    the kernel produces is carried."""
    again = PendingOrder(order=buy(X, 300, signal=102, expires=105, price="30.00"), attempts=0)
    cooling = (Cooldown(ts_code=X, since_session=102),)

    snapshot = held_snapshot(pending_orders=(again,), cooldowns=cooling)

    assert [p.order.ts_code for p in snapshot.pending_orders] == [X]
    assert snapshot.cooldowns == cooling


def test_a_holding_is_one_lot_opened_on_or_before_the_snapshot_session() -> None:
    """The survivor book never adds to a held name, so a holding is the one lot its buy opened;
    and a lot opened after the snapshot's session is the look-ahead guard for the book itself."""
    two_lots = PortfolioPosition(
        subject=A,
        lots=(
            PositionLot(open_date=S1, quantity=500, cost_basis=D("5002.50")),
            PositionLot(open_date=S2, quantity=500, cost_basis=D("5002.50")),
        ),
    )
    book = held_snapshot().book
    with pytest.raises(ValueError, match=r"held names not in exactly one lot: \['600000\.SH'\]"):
        held_snapshot(book=book.model_copy(update={"positions": (two_lots, book.positions[1])}))
    with pytest.raises(ValueError, match=r"lots opened after session 2021-03-03: \['600036\.SH'\]"):
        held_snapshot(
            book=book.model_copy(
                update={"positions": (book.positions[0], lot(B, 2_000, "40805.00", bought=S3))}
            )
        )


def test_the_book_is_as_of_the_snapshot_session_and_settled_through_it_or_not_at_all() -> None:
    with pytest.raises(
        ValueError, match="the book is as of 2021-03-02, the snapshot is for 2021-03-03"
    ):
        held_snapshot(book=held_snapshot().book.model_copy(update={"as_of": S1}))
    with pytest.raises(ValueError, match="settled through session 101 but is as of session 102"):
        held_snapshot(last_settled_session=101)
    opening = opening_snapshot()
    assert opening.last_settled_session is None


# --- on disk ------------------------------------------------------------------------------


def _document(store: FilePaperStateStore, session: date) -> Path:
    (path,) = store.path.glob(f"{session.isoformat()}.pbs_*.json")
    return path


def test_a_saved_snapshot_reads_back_byte_for_byte(tmp_path: Path) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")
    snapshot = held_snapshot()

    assert save_snapshot(store, snapshot) == "created"

    document = _document(store, S2)
    assert document.name == f"2021-03-03.{snapshot.snapshot_id}.json"
    assert document.read_text(encoding="utf-8") == snapshot_payload(snapshot)
    loaded = load_snapshot(FilePaperStateStore(tmp_path / "snapshots"), S2)
    assert loaded == snapshot
    assert loaded.snapshot_id == snapshot.snapshot_id
    assert snapshot_payload(loaded) == document.read_text(encoding="utf-8")
    # The ids the book's orders carry survive the trip, so a carried order keeps its attempts' id.
    assert [p.order.order_id for p in loaded.pending_orders] == [
        p.order.order_id for p in snapshot.pending_orders
    ]


def test_saving_a_session_again_leaves_its_file_untouched(tmp_path: Path) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")
    save_snapshot(store, held_snapshot())
    document = _document(store, S2)
    before = (document.stat().st_ino, document.stat().st_mtime_ns, document.read_bytes())

    assert save_snapshot(store, held_snapshot()) == "unchanged"

    assert (document.stat().st_ino, document.stat().st_mtime_ns, document.read_bytes()) == before
    assert sorted(entry.name for entry in store.path.iterdir()) == [document.name]


def test_a_second_different_snapshot_for_a_settled_session_is_refused_by_name(
    tmp_path: Path,
) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")
    first = held_snapshot()
    other = held_snapshot(book=first.book.model_copy(update={"cash": D("49190.01")}))
    save_snapshot(store, first)
    before = _document(store, S2).read_bytes()

    with pytest.raises(
        PaperStateError,
        match=rf"2021-03-03 is already settled as {first.snapshot_id}.*{other.snapshot_id}",
    ):
        save_snapshot(store, other)
    with pytest.raises(
        PaperStateStoreError, match=rf"2021-03-03 is already held as {first.snapshot_id}"
    ):
        store.put(session=S2, snapshot_id=other.snapshot_id, payload=snapshot_payload(other))

    assert _document(store, S2).read_bytes() == before
    assert len(list(store.path.iterdir())) == 1


def test_a_snapshot_dated_before_the_latest_held_is_refused_and_the_history_grows_at_its_head(
    tmp_path: Path,
) -> None:
    """Every snapshot after the opening one is settled from the one before it, and
    `latest_snapshot` refuses whenever a later-dated snapshot is held, so a write behind the
    head can only come from a writer that skipped that read -- and a snapshot slipped in behind
    the head is not the book the later ones were built from. Refused, naming the head. The same
    snapshot again for a session already held is still `unchanged` and writes nothing."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    s0, s1 = _settled_through(1)
    save_snapshot(store, s0)
    save_snapshot(store, held_snapshot())  # S2: the head

    with pytest.raises(
        PaperStateError,
        match=r"2021-03-02 is dated before the latest snapshot held, 2021-03-03",
    ):
        save_snapshot(store, s1)
    assert store.sessions() == (S0, S2)
    assert save_snapshot(store, s0) == "unchanged"
    assert store.sessions() == (S0, S2)


def test_two_documents_held_for_one_session_are_refused_rather_than_one_picked(
    tmp_path: Path,
) -> None:
    """`put` never writes a second document for a session, so two found there were put there by
    something else; answering with whichever sorts first would make the book depend on a
    filename. Every reader of that session refuses, naming both addresses."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    first = held_snapshot()
    other = held_snapshot(book=first.book.model_copy(update={"cash": D("49190.01")}))
    save_snapshot(store, first)
    _document(store, S2).with_name(f"2021-03-03.{other.snapshot_id}.json").write_text(
        snapshot_payload(other), encoding="utf-8"
    )
    named = (
        rf"2021-03-03 is held under 2 snapshots \(\[.*{min(first.snapshot_id, other.snapshot_id)}"
    )

    assert store.sessions() == (S2,)
    with pytest.raises(PaperStateStoreError, match=named):
        store.get(S2)
    with pytest.raises(PaperStateStoreError, match=named):
        load_snapshot(store, S2)
    with pytest.raises(PaperStateStoreError, match=named):
        store.put(session=S2, snapshot_id=first.snapshot_id, payload=snapshot_payload(first))


def test_an_interrupted_write_leaves_no_snapshot_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes go to a temporary file and reach the document's name in one `os.replace`; a
    write that dies before the rename leaves nothing a reader can mistake for a snapshot."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    snapshot = held_snapshot()

    def interrupted(src: object, dst: object) -> None:
        raise OSError("the disk filled up")

    monkeypatch.setattr("openalpha_cn.storage.paper_state.os.replace", interrupted)
    with pytest.raises(OSError, match="the disk filled up"):
        save_snapshot(store, snapshot)
    monkeypatch.undo()

    assert store.sessions() == ()
    assert store.get(S2) is None
    assert [entry.name for entry in store.path.iterdir()] == [
        f"2021-03-03.{snapshot.snapshot_id}.json.partial"
    ]
    with pytest.raises(PaperStateError, match="no paper book snapshot is held for 2021-03-03"):
        load_snapshot(store, S2)

    assert save_snapshot(store, snapshot) == "created"
    assert load_snapshot(store, S2) == snapshot
    assert [entry.name for entry in store.path.iterdir()] == [_document(store, S2).name]


def _record_the_write(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Every `os.fsync` (of a file or of a directory, by inode) and `os.replace`, in order."""
    events: list[tuple[str, ...]] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(descriptor: int) -> None:
        status = os.fstat(descriptor)
        kind = "directory" if stat.S_ISDIR(status.st_mode) else "file"
        events.append(("fsync", kind, str(status.st_ino)))
        real_fsync(descriptor)

    def replace(source: Any, target: Any) -> None:
        events.append(("replace",))
        real_replace(source, target)

    monkeypatch.setattr("openalpha_cn.storage.paper_state.os.fsync", fsync)
    monkeypatch.setattr("openalpha_cn.storage.paper_state.os.replace", replace)
    return events


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot open a directory to fsync it")
def test_the_rename_is_made_durable_by_syncing_the_directory_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file's bytes are synced before the rename and the directory after it: without the
    second, a power loss can lose the rename while the paper ledger keeps that session's rows,
    and the next settlement would find the session missing."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    store.path.mkdir()
    events = _record_the_write(monkeypatch)

    save_snapshot(store, held_snapshot())

    directory = str(store.path.stat().st_ino)
    assert [event[:2] for event in events] == [
        ("fsync", "file"),
        ("replace",),
        ("fsync", "directory"),
    ]
    assert events[-1] == ("fsync", "directory", directory)
    events.clear()
    assert save_snapshot(store, held_snapshot()) == "unchanged"
    assert events == []  # the same snapshot again writes, and so syncs, nothing


def test_where_a_directory_cannot_be_synced_the_rename_is_left_to_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows branch, exercised on every platform: no directory is opened, and the write
    is otherwise the same -- file synced, then renamed."""
    monkeypatch.setattr("openalpha_cn.storage.paper_state._SYNC_DIRECTORIES", False)
    store = FilePaperStateStore(tmp_path / "snapshots")
    events = _record_the_write(monkeypatch)

    assert save_snapshot(store, held_snapshot()) == "created"

    assert [event[:2] for event in events] == [("fsync", "file"), ("replace",)]


def test_a_snapshot_document_that_is_not_text_is_refused_as_a_store_error_not_a_value_error(
    tmp_path: Path,
) -> None:
    """Two bytes that are not UTF-8 make `read_text` raise `UnicodeDecodeError`, a `ValueError`
    -- the exception `PaperStateError` is a `RuntimeError` to keep out of a caller's
    `except ValueError`. The store names the document instead (`V2-P4-096`'s lesson: a fault in
    the stored bytes is the store's to name, not an escape)."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    save_snapshot(store, held_snapshot())
    document = _document(store, S2)
    document.write_bytes(b"\xff\xfe")
    named = rf"the snapshot document {re.escape(document.name)} held for 2021-03-03 is not UTF-8"

    for read in (
        lambda: store.get(S2),
        lambda: load_snapshot(store, S2),
        lambda: save_snapshot(store, held_snapshot()),
    ):
        with pytest.raises(PaperStateStoreError, match=named) as refused:
            read()
        assert not isinstance(refused.value, ValueError)
        assert isinstance(refused.value.__cause__, UnicodeDecodeError)
    assert document.read_bytes() == b"\xff\xfe"  # refused, not overwritten


def test_a_missing_snapshot_is_refused_by_name_and_never_read_as_an_empty_book(
    tmp_path: Path,
) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")

    with pytest.raises(PaperStateError, match="no paper book snapshot is held at all"):
        latest_snapshot(store, settling_session=S3, settling_index=103)
    save_snapshot(store, held_snapshot())
    with pytest.raises(PaperStateError, match="no paper book snapshot is held for 2021-03-04"):
        load_snapshot(store, S3)


def _settled_through(stop: int) -> tuple[PaperBookSnapshot, ...]:
    """The opening book and the books of `DAYS[:stop]`, settled one after the other."""
    books = [opening_snapshot()]
    for day in DAYS[:stop]:
        books.append(_settle(books[-1], day))
    return tuple(books)


def test_the_book_a_session_settles_from_is_the_snapshot_of_the_session_before_it(
    tmp_path: Path,
) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")
    s0, s1 = _settled_through(1)
    save_snapshot(store, s0)
    save_snapshot(store, s1)

    assert latest_snapshot(store, settling_session=S2, settling_index=102) == s1
    assert latest_snapshot(store, settling_session=S2, settling_index=102).session == S1


def test_a_missing_snapshot_in_the_middle_is_refused_by_name_not_skipped(tmp_path: Path) -> None:
    """S0 and S1 settled and S2's evening crashed after its ledger rows but before its snapshot:
    settling S3 from S1's book would hand S2's due orders to S3 under new attempt ids, so the
    ledger would hold two fills of the same orders. Refused, naming the missing ordinal."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    s0, s1 = _settled_through(1)
    save_snapshot(store, s0)
    save_snapshot(store, s1)

    with pytest.raises(
        PaperStateError,
        match=r"snapshot for session index 102 is missing: the latest is 101 \(2021-03-02\)",
    ):
        latest_snapshot(store, settling_session=S3, settling_index=103)


def test_a_snapshot_dated_after_the_session_being_settled_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """The book is never continued from a snapshot that knows a later session: that is the
    look-ahead rule, held at the one reader that hands a settlement its book."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    save_snapshot(store, opening_snapshot())
    save_snapshot(store, held_snapshot())  # S2

    with pytest.raises(
        PaperStateError,
        match=r"snapshots dated after 2021-03-02, the session being settled, are held: "
        r"\['2021-03-03'\]",
    ):
        latest_snapshot(store, settling_session=S1, settling_index=101)


def test_a_session_whose_snapshot_is_held_is_reported_as_already_settled(tmp_path: Path) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")
    for book in _settled_through(2):
        save_snapshot(store, book)

    with pytest.raises(PaperStateError, match=r"2021-03-02 is already settled"):
        latest_snapshot(store, settling_session=S1, settling_index=101)
    with pytest.raises(PaperStateError, match=r"2021-03-03 is already settled"):
        latest_snapshot(store, settling_session=S2, settling_index=102)


@pytest.mark.parametrize(
    ("damage", "refusal"),
    [
        (
            lambda text: text[: len(text) // 2],
            r"snapshot for 2021-03-03 \(pbs_[0-9a-f]{24}\) cannot be read",
        ),
        (lambda text: "", r"snapshot for 2021-03-03 \(pbs_[0-9a-f]{24}\) cannot be read"),
        (
            lambda text: text.replace('"49190.00"', '"59190.00"'),
            r"snapshot for 2021-03-03 is filed as pbs_[0-9a-f]{24} "
            r"and its content is pbs_[0-9a-f]{24}: it was edited, or written by a build whose "
            r"contracts differ",
        ),
        (
            lambda text: json.dumps(json.loads(text), indent=2),
            r"snapshot for 2021-03-03 \(pbs_[0-9a-f]{24}\) is not in its canonical bytes: it "
            r"was edited, or written by a build whose contracts differ",
        ),
        (
            lambda text: text.replace('"session":"2021-03-03"', '"session":"2021-03-02"').replace(
                '"as_of":"2021-03-03"', '"as_of":"2021-03-02"'
            ),
            r"the snapshot filed under 2021-03-03 is for 2021-03-02",
        ),
    ],
    ids=["truncated", "emptied", "edited", "re-indented", "re-dated"],
)
def test_a_damaged_snapshot_is_refused_by_name_and_never_read_as_an_empty_book(
    tmp_path: Path, damage: Callable[[str], str], refusal: str
) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")
    save_snapshot(store, held_snapshot())
    document = _document(store, S2)
    document.write_text(damage(document.read_text(encoding="utf-8")), encoding="utf-8")

    with pytest.raises(PaperStateError, match=refusal):
        load_snapshot(store, S2)
    with pytest.raises(PaperStateError, match=refusal):
        latest_snapshot(store, settling_session=S3, settling_index=103)
    with pytest.raises(PaperStateError, match=refusal):
        save_snapshot(store, held_snapshot())  # an intact copy does not paper over the damage


def test_a_held_name_whose_entry_factor_is_missing_on_disk_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """The requirement read back rather than built: a stored snapshot that lost a holding's entry
    factor names the holding, so the next session's adjustment ratio is never taken as 1."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    snapshot = held_snapshot()
    save_snapshot(store, snapshot)
    document = _document(store, S2)
    body = json.loads(document.read_text(encoding="utf-8"))
    body["entry_factors"] = [item for item in body["entry_factors"] if item["ts_code"] != B]
    document.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(
        PaperStateError, match=r"held names with no entry adjustment factor: \['600036\.SH'\]"
    ):
        load_snapshot(store, S2)


def test_an_attempt_count_edited_on_disk_is_refused_by_name(tmp_path: Path) -> None:
    """The one byte that changes is the count of C's buy (planned tonight, so tried on none),
    and the refusal names the order and the count rather than an address mismatch."""
    store = FilePaperStateStore(tmp_path / "snapshots")
    save_snapshot(store, held_snapshot())
    document = _document(store, S2)
    text = document.read_text(encoding="utf-8")
    assert text.count('"attempts":0') == 2
    document.write_text(text.replace('"attempts":0', '"attempts":1', 1), encoding="utf-8")

    with pytest.raises(
        PaperStateError,
        match=r"orders not tried on every session settled since they were due: "
        r"\['tor_[0-9a-f]{24} \(buy 000001\.SZ\) tried 1, due on 0 settled sessions'\]",
    ):
        load_snapshot(store, S2)


def test_the_store_refuses_a_key_that_is_not_a_snapshot_address(tmp_path: Path) -> None:
    store = FilePaperStateStore(tmp_path / "snapshots")

    for unusable in (
        "../../escape",
        "pbs_" + "0" * 23,
        "pbs_" + "0" * 24 + "\n",
        "tor_" + "0" * 24,
    ):
        with pytest.raises(PaperStateStoreError, match="is not a snapshot_id"):
            store.put(session=S2, snapshot_id=unusable, payload="{}")
    assert not store.path.exists()


def test_open_snapshot_is_the_one_reader(tmp_path: Path) -> None:
    snapshot = held_snapshot()
    payload = snapshot_payload(snapshot)

    assert open_snapshot(payload, snapshot_id=snapshot.snapshot_id, session=S2) == snapshot
    with pytest.raises(PaperStateError, match=r"is filed as pbs_0{24} and its content is"):
        open_snapshot(payload, snapshot_id="pbs_" + "0" * 24, session=S2)


def _without_descriptions(schema: object) -> object:
    """The schema minus its prose: a docstring is not a change to the stored bytes."""
    if isinstance(schema, dict):
        return {
            key: _without_descriptions(value)
            for key, value in schema.items()
            if key != "description"
        }
    if isinstance(schema, list):
        return [_without_descriptions(item) for item in schema]
    return schema


PAPER_BOOK_SNAPSHOT_SCHEMA_DIGEST = (
    "b3759468e158958772b056a486e72cfd2eb8f3f5aa6325caedb23ae2072ec389"
)
"""sha256 of `PaperBookSnapshot.model_json_schema()` without its descriptions, keys in the order
pydantic gives them -- which is the fields' declaration order, the order `model_dump_json`
writes and `open_snapshot`'s canonical-bytes check compares."""


def test_the_stored_shape_of_a_snapshot_is_pinned_so_a_contract_change_needs_a_migration() -> None:
    """A snapshot embeds `PortfolioState`, `PortfolioPosition`, `PositionLot`, `PositionMark`,
    `TradeOrder` and `Cooldown`. A field added to any of them -- even with a default -- or two
    fields swapped makes every stored snapshot unreadable: `extra="forbid"` refuses the old bytes
    or `open_snapshot` finds them no longer canonical. So the shape is pinned here, and a change
    goes red in CI with what it means rather than in a settlement run against stored books.

    Keys are *not* sorted before hashing, on purpose: the order is part of the canonical bytes.
    Descriptions are dropped, so a docstring edit does not trip it. A pydantic upgrade that only
    re-renders the schema will trip it too; that is the one case where re-pinning is the answer,
    after checking that a stored snapshot still reads back."""
    schema = _without_descriptions(PaperBookSnapshot.model_json_schema())
    rendered = json.dumps(schema, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    assert digest == PAPER_BOOK_SNAPSHOT_SCHEMA_DIGEST, (
        f"the stored shape of a paper-book snapshot changed (schema digest {digest}): a field of "
        "`PaperBookSnapshot` or of a contract it embeds was added, removed, retyped or reordered. "
        "Every snapshot already stored would stop reading -- refused as unreadable or as not "
        "canonical -- so this change needs a migration of stored snapshots (a new "
        "`paper-book-snapshot/v2` and an upgrade of the v1 documents), not a new digest here"
    )


# --- replay -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Day:
    """One observed session for `_settle`: today's adjustment factors and closes (real prices),
    the fills of the orders that filled (real price, fee), and the orders planned tonight."""

    on: date
    factors: Mapping[str, str]
    closes: Mapping[str, str]
    fills: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    planned: tuple[TradeOrder, ...] = ()


def _settle(snapshot: PaperBookSnapshot, day: Day) -> PaperBookSnapshot:
    """Cash-and-holdings bookkeeping for one session, from the snapshot alone.

    A buy that fills opens a one-lot position at its cost plus fee and records today's factor as
    the entry factor; a sell that fills sells at `real price x today's factor / entry factor` (the
    ledger's units) and starts a cool-down; an unfilled buy on or past its last session expires
    into a cool-down; every other due order stays pending with one more attempt. Holdings are
    marked at `close x today's factor / entry factor`.
    """
    session = INDEX[day.on]
    assert session == snapshot.session_index + 1
    entry = snapshot.entry_factors_by_code()
    positions = {position.subject: position for position in snapshot.book.positions}
    cash, realized, fees = snapshot.book.cash, snapshot.book.realized_pnl, snapshot.book.fees_paid
    cooldowns = {cooldown.ts_code: cooldown for cooldown in snapshot.cooldowns}
    pending: list[PendingOrder] = []
    for item in snapshot.pending_orders:
        order, code = item.order, item.order.ts_code
        if order.execute_session > session:
            pending.append(item)
        elif code in day.fills:
            price, fee = (D(value) for value in day.fills[code])
            fees += fee
            if order.side == "buy":
                cost = price * order.quantity + fee
                cash -= cost
                positions[code] = lot(code, order.quantity, str(cost), bought=day.on)
                entry[code] = D(day.factors[code])
            else:
                notional = (
                    price * D(day.factors[code]) / entry.pop(code) * order.quantity
                ).quantize(CENT)
                proceeds = notional - fee
                cash += proceeds
                realized += proceeds - positions.pop(code).cost_basis
                cooldowns[code] = Cooldown(ts_code=code, since_session=session)
        elif (
            order.side == "buy"
            and order.expires_session is not None
            and session >= order.expires_session
        ):
            cooldowns[code] = Cooldown(ts_code=code, since_session=session)
        else:
            pending.append(PendingOrder(order=order, attempts=item.attempts + 1))
    return PaperBookSnapshot(
        session_index=session,
        session=day.on,
        book=PortfolioState(
            as_of=day.on,
            cash=cash,
            positions=tuple(positions[code] for code in sorted(positions)),
            marks=tuple(
                PositionMark(
                    subject=code, price=D(day.closes[code]) * D(day.factors[code]) / entry[code]
                )
                for code in sorted(positions)
            ),
            realized_pnl=realized,
            fees_paid=fees,
        ),
        entry_factors=tuple(EntryFactor(ts_code=code, entry_adj=entry[code]) for code in entry),
        pending_orders=(
            *pending,
            *(PendingOrder(order=order, attempts=0) for order in day.planned),
        ),
        cooldowns=tuple(cooldowns.values()),
        last_settled_session=session,
    )


DAYS: tuple[Day, ...] = (
    # S1: A fills at 10.00 (+5.00) and B at 20.40 (+5.00): cash 100,000.00 - 10,005.00
    # - 40,805.00 = 49,190.00. X's buy does not fill on its last session and cools down from 101.
    Day(
        S1,
        {A: "2.000", B: "1.000"},
        {A: "10.50", B: "20.50"},
        {A: ("10.00", "5.00"), B: ("20.40", "5.00")},
    ),
    # S2: A's factor moves 2.000 -> 2.300, a 1.15 event: A marks at 9.20 x 1.15 = 10.58. Tonight
    # A is to be sold and C bought (its last session is 106).
    Day(
        S2,
        {A: "2.300", B: "1.000"},
        {A: "9.20", B: "20.80"},
        planned=(
            sell(A, 1_000, signal=102, price="10.58"),
            buy(C, 500, signal=102, expires=106, price="12.00"),
        ),
    ),
    # S3: neither fills (A limit-down, C limit-up): one attempt each. B's factor moves 1.000 ->
    # 1.200: B marks at 17.50 x 1.2 = 21.00.
    Day(S3, {A: "2.300", B: "1.200"}, {A: "8.80", B: "17.50"}),
    # S4: A sells at 8.90, i.e. 8.90 x 1.15 = 10.235 in the ledger's units: 10,235.00 - 10.00
    # = 10,225.00 into cash (59,415.00), realised 10,225.00 - 10,005.00 = 220.00; A cools down
    # from 104. C's second attempt fails.
    Day(S4, {A: "2.300", B: "1.200"}, {B: "18.00"}, {A: ("8.90", "10.00")}),
    # S5: C's third attempt fails (it lives to 106). B marks at 18.10 x 1.2 = 21.72.
    Day(S5, {B: "1.200"}, {B: "18.10"}),
)


def test_the_replay_fixture_reaches_the_hand_computed_book() -> None:
    book = opening_snapshot()
    for day in DAYS:
        book = _settle(book, day)

    assert book.session_index == 105
    assert book.book.cash == D("59415.00")
    assert book.book.realized_pnl == D("220.00")
    assert book.book.fees_paid == D("20.00")  # 5.00 + 5.00 + 10.00
    assert [(p.subject, p.quantity, p.cost_basis) for p in book.book.positions] == [
        (B, 2_000, D("40805.00"))
    ]
    assert book.book.mark(B) == D("21.72")
    assert book.entry_factors_by_code() == {B: D("1.000")}
    assert [(p.order.ts_code, p.order.side, p.attempts) for p in book.pending_orders] == [
        (C, "buy", 3)
    ]
    assert [(c.ts_code, c.since_session) for c in book.cooldowns] == [(A, 104), (X, 101)]


@pytest.mark.parametrize("stop", range(len(DAYS)), ids=lambda stop: f"stopped-after-S{stop}")
def test_an_old_snapshot_and_the_sessions_after_it_replay_to_the_snapshot_reached_directly(
    tmp_path: Path, stop: int
) -> None:
    """Read a snapshot back from disk at every session in turn, replay the rest, and land on the
    very bytes of a book that never stopped -- so nothing the next session needs (an entry factor,
    an attempt count, a cool-down, a decimal's places) is lost on the way through the file."""
    direct = opening_snapshot()
    for day in DAYS:
        direct = _settle(direct, day)

    store = FilePaperStateStore(tmp_path / "snapshots")
    book = opening_snapshot()
    save_snapshot(store, book)
    for day in DAYS[:stop]:
        book = _settle(book, day)
        save_snapshot(store, book)
    resumed = latest_snapshot(
        FilePaperStateStore(tmp_path / "snapshots"),
        settling_session=DAYS[stop].on,
        settling_index=INDEX[DAYS[stop].on],
    )
    assert resumed.session == ([S0, *[day.on for day in DAYS]])[stop]
    for day in DAYS[stop:]:
        resumed = _settle(resumed, day)

    assert resumed == direct
    assert resumed.snapshot_id == direct.snapshot_id
    assert snapshot_payload(resumed) == snapshot_payload(direct)


# --- the ledger of the paper book's own ---------------------------------------------------


def _transition(order_id: str, cash: str) -> PortfolioTransition:
    state = PortfolioState(as_of=S1, cash=D(cash))
    return PortfolioTransition(
        status="rejected",
        order=PortfolioOrder(order_id=order_id, subject=A, side="buy", quantity=100),
        before=state,
        after=state,
        reason="insufficient cash",
    )


def test_the_paper_book_keeps_a_ledger_the_backtest_ledger_cannot_see(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Two instances, two files: the same attempt id carries two different payloads, one in each,
    and neither ledger refuses it as a conflict or lists the other's row."""
    runtime_dir = tmp_path / "runtime"
    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)
    paper_row = _transition("tor_attempt@2021-03-02", "1000.00")
    backtest_row = _transition("tor_attempt@2021-03-02", "2000.00")

    storage.paper_ledger.append(paper_row)
    storage.portfolio_ledger.append(backtest_row)

    assert isinstance(storage.paper_ledger, SQLitePortfolioLedger)
    assert storage.paper_ledger.path == runtime_dir / "paper" / "ledger.sqlite3"
    assert storage.portfolio_ledger.path == runtime_dir / "state.sqlite3"
    assert storage.paper_ledger.list() == (paper_row,)
    assert storage.portfolio_ledger.list() == (backtest_row,)
    reopened = build_storage(runtime_dir=runtime_dir, clock=migration_clock)
    assert reopened.paper_ledger.list() == (paper_row,)
    assert reopened.portfolio_ledger.list() == (backtest_row,)


def test_the_composition_root_wires_the_paper_snapshot_store(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    runtime_dir = tmp_path / "runtime"
    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    assert isinstance(storage.paper_state_store, FilePaperStateStore)
    assert storage.paper_state_store.path == runtime_dir / "paper" / "snapshots"
    save_snapshot(storage.paper_state_store, held_snapshot())
    reopened = build_storage(runtime_dir=runtime_dir, clock=migration_clock)
    assert (
        latest_snapshot(reopened.paper_state_store, settling_session=S3, settling_index=103)
        == held_snapshot()
    )
    assert os.path.isfile(_document(reopened.paper_state_store, S2))
