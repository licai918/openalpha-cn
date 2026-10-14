"""One evening of the paper book as a crash-safe transaction, the book's opening, and the
catch-up of a missed session (`V2-P7-009`).

Every fixture is hand-written with invented prices on the Shanghai main board, and every number
asserted is worked out in the comment beside it. The default `CostSchedule` applies (commission
0.03% with a 5.00 floor, transfer fee 0.001%, sell stamp duty 0.05%, each rounded half-up to the
fen) and `PortfolioLimits` are 1 / 1 / 0, the paper book's setting (D15). The book opens on the
epoch E0 2021-03-01 (ordinal 0); S1 ... S6 are the sessions after it (ordinals 1 ... 6). The
stores are the real ones -- `FilePaperStateStore` and `SQLitePortfolioLedger` in `tmp_path` --
and the kernel is the real one: `decide_book` and `build_trade_list` run inside every evening.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import random
import socket
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import pytest

from openalpha_cn.backtest.execution import MarketBar, security_board
from openalpha_cn.backtest.multi_day import PortfolioLedger
from openalpha_cn.backtest.paper import PaperPortfolio, PaperPortfolioReachedOutward
from openalpha_cn.backtest.paper_evening import (
    CatchUpResult,
    DryRunLedger,
    EveningResult,
    PaperLedgerConflictError,
    missing_sessions,
    open_book,
    run_catch_up,
    run_evening,
)
from openalpha_cn.backtest.paper_settlement import WRITE_OFF_REASON, Settlement, write_off_id
from openalpha_cn.backtest.paper_state import (
    EntryFactor,
    PaperBookSnapshot,
    PaperStateError,
    load_snapshot,
    snapshot_payload,
)
from openalpha_cn.backtest.paper_step import attempt_id
from openalpha_cn.backtest.portfolio import PortfolioLimits
from openalpha_cn.domain.portfolio import (
    PortfolioOrder,
    PortfolioState,
    PortfolioTransition,
    PositionLot,
)
from openalpha_cn.domain.survivor_book import (
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SecurityFacts,
    SurvivorBookConfig,
    survivor_config_digest,
)
from openalpha_cn.domain.survivor_orders import TradeList, TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown
from openalpha_cn.domain.survivor_screen import screen as run_screen
from openalpha_cn.storage.paper_state import FilePaperStateStore, PaperStateStoreError
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

D = Decimal
E0, S1, S2, S3, S4, S5, S6 = (
    date(2021, 3, 1),
    date(2021, 3, 2),
    date(2021, 3, 3),
    date(2021, 3, 4),
    date(2021, 3, 5),
    date(2021, 3, 8),
    date(2021, 3, 9),
)
INDEX = {E0: 0, S1: 1, S2: 2, S3: 3, S4: 4, S5: 5, S6: 6}
CONFIG = SurvivorBookConfig(seed=7)
DIGEST = survivor_config_digest(CONFIG)
OTHER_CONFIG = SurvivorBookConfig(seed=7, holding_count=9)
OTHER_DIGEST = survivor_config_digest(OTHER_CONFIG)
FULL = PortfolioLimits(max_position_weight=D(1), max_total_exposure=D(1), min_cash_weight=D(0))
BENCHMARK = D("6000.00")
CENT = D("0.01")
A, B, C, DL, E, F, G, H = (
    "600010.SH",
    "600020.SH",
    "600030.SH",
    "600040.SH",
    "600050.SH",
    "600060.SH",
    "600070.SH",
    "600080.SH",
)
"""`DL` is the name delisted while held."""


# ---- fixtures ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Market:
    """A session's market as C1 delivers it; the evening reads it structurally."""

    bars: Mapping[str, MarketBar]
    adj_factor: Mapping[str, Decimal]
    benchmark_close: Decimal = BENCHMARK


NOTHING = Market(bars={}, adj_factor={})
"""A session on which the book holds nothing and has nothing due: no name to ask about."""


def bar(
    code: str, day: date, prev: str, o: str, h: str, lo: str, c: str, up: str, down: str
) -> MarketBar:
    """A main-board bar with the exchange's published band."""
    return MarketBar(
        subject=code,
        trade_date=day,
        board=security_board(code),
        previous_close=D(prev),
        open=D(o),
        high=D(h),
        low=D(lo),
        close=D(c),
        suspended=False,
        is_st=False,
        up_limit=D(up),
        down_limit=D(down),
    )


def quiet(code: str, day: date, price: str) -> MarketBar:
    """A bar that opens, trades and closes at `price`, inside a band of 10% either way."""
    up = (D(price) * D("1.1")).quantize(CENT, rounding=ROUND_HALF_UP)
    down = (D(price) * D("0.9")).quantize(CENT, rounding=ROUND_HALF_UP)
    return bar(code, day, price, price, price, price, price, str(up), str(down))


def limit_up(code: str, day: date, prev: str) -> MarketBar:
    """A one-price bar locked at the limit-up price all day: a buy at the open cannot fill."""
    up = (D(prev) * D("1.1")).quantize(CENT, rounding=ROUND_HALF_UP)
    down = (D(prev) * D("0.9")).quantize(CENT, rounding=ROUND_HALF_UP)
    return bar(code, day, prev, str(up), str(up), str(up), str(up), str(up), str(down))


def market(*quotes: MarketBar, factors: Collection[str] = ()) -> Market:
    """The session's bars, with an adjustment factor of 1.000 for every quoted name and for each
    of `factors` (a held name with no bar still needs one)."""
    codes = {quote.subject for quote in quotes} | set(factors)
    return Market(
        bars={quote.subject: quote for quote in quotes},
        adj_factor={code: D("1.000") for code in codes},
    )


def screened(*survivors: tuple[str, str], dropped: Collection[str] = ()) -> ScreenResult:
    """`survivors` best first, each `(code, close)`, composites 0.99, 0.98, ...; `dropped` are
    eligible names below the survivors (a holding among them has lost its place)."""
    ranked = tuple(
        RankedName(ts_code=code, composite=1 - rank / 100, rank=rank, close=D(close))
        for rank, (code, close) in enumerate(survivors, start=1)
    )
    verdicts = [EligibilityVerdict(ts_code=n.ts_code, composite=n.composite) for n in ranked]
    verdicts += [EligibilityVerdict(ts_code=code, composite=0.01) for code in dropped]
    return ScreenResult(
        verdicts=tuple(sorted(verdicts, key=lambda verdict: verdict.ts_code)), survivors=ranked
    )


def make_portfolio(ledger: PortfolioLedger) -> PaperPortfolio:
    """What the daily entry's factory does, with the default costs: the book on `ledger`."""
    return PaperPortfolio(ledger=ledger, limits=FULL)


@dataclass(frozen=True)
class Book:
    """One runtime directory's paper book: its snapshot store and its own ledger."""

    root: Path
    store: FilePaperStateStore
    ledger: SQLitePortfolioLedger


def book_at(root: Path, cash: str = "100000.00") -> Book:
    """A book opened on E0 with `cash`, nothing settled yet."""
    return Book(
        root=root,
        store=opened(root, cash),
        ledger=SQLitePortfolioLedger(root / "ledger.sqlite3"),
    )


def evening(
    book: Book,
    day: date,
    quotes: Market,
    screen: ScreenResult,
    prices: Mapping[str, str | Decimal],
    *,
    delisted: Collection[str] = (),
    config: SurvivorBookConfig = CONFIG,
    index: int | None = None,
    observed_on: date | None = None,
    store: object = None,
    ledger: object = None,
    factory: Callable[[PortfolioLedger], PaperPortfolio] = make_portfolio,
) -> EveningResult:
    return run_evening(
        store=book.store if store is None else store,  # type: ignore[arg-type]
        ledger=book.ledger if ledger is None else ledger,  # type: ignore[arg-type]
        make_portfolio=factory,
        config=config,
        session=day,
        session_index=INDEX.get(day, -1) if index is None else index,
        market=quotes,
        screen=screen,
        entrant_prices={code: D(price) for code, price in prices.items()},
        delisted=delisted,
        observed_on=day if observed_on is None else observed_on,
    )


def catch_up(
    book: Book,
    day: date,
    quotes: Market,
    *,
    delisted: Collection[str] = (),
    config: SurvivorBookConfig = CONFIG,
    observed_on: date | None = None,
) -> CatchUpResult:
    """Settle the missed session `day` -- observed later, by default the day after S6."""
    return run_catch_up(
        store=book.store,
        ledger=book.ledger,
        make_portfolio=make_portfolio,
        config=config,
        session=day,
        session_index=INDEX[day],
        market=quotes,
        delisted=delisted,
        observed_on=date(2021, 3, 10) if observed_on is None else observed_on,
    )


def read_on(settlement: Settlement | None, day: date) -> Settlement:
    """`settlement` as a run that read its inputs on `day` records it: the runner's record
    notes the observation date, and nothing else in a settlement depends on it."""
    assert settlement is not None
    record = settlement.runner_record.model_copy(update={"observed_on": day.isoformat()})
    return dataclasses.replace(settlement, runner_record=record)


def rows(ledger: SQLitePortfolioLedger) -> list[str]:
    """The ledger's rows as stored, in the order appended."""
    return [row.model_dump_json() for row in ledger.list()]


def outcomes(ledger: SQLitePortfolioLedger) -> list[tuple[str, str, str | None]]:
    return [(row.order.order_id, row.status, row.reason) for row in ledger.list()]


def ledger_files(book: Book) -> dict[str, bytes]:
    """The bytes of every file SQLite keeps for the ledger (the database, and a WAL if any)."""
    files = {path.name: path.read_bytes() for path in sorted(book.root.glob("ledger.sqlite3*"))}
    assert "ledger.sqlite3" in files  # never a comparison of two empty listings
    return files


def snapshot_bytes(store: FilePaperStateStore) -> dict[str, bytes]:
    return {name: content for name, (content, _, _) in documents(store).items()}


class CutAsTheSnapshotIsWritten:
    """The snapshot store, with the power cut as the evening's snapshot is written."""

    def __init__(self, inner: FilePaperStateStore) -> None:
        self.inner = inner

    def put(self, *, session: date, snapshot_id: str, payload: str) -> Literal["created"]:
        raise OSError("the power was cut as the snapshot was written")

    def get(self, session: date) -> tuple[str, str] | None:
        return self.inner.get(session)

    def sessions(self) -> tuple[date, ...]:
        return self.inner.sessions()


class Watched:
    """The book's snapshot store and ledger, counting every write either is asked to make."""

    def __init__(self, book: Book) -> None:
        self.book, self.puts, self.appends = book, 0, 0

    def put(
        self, *, session: date, snapshot_id: str, payload: str
    ) -> Literal["created", "unchanged"]:
        self.puts += 1
        return self.book.store.put(session=session, snapshot_id=snapshot_id, payload=payload)

    def get(self, session: date) -> tuple[str, str] | None:
        return self.book.store.get(session)

    def sessions(self) -> tuple[date, ...]:
        return self.book.store.sessions()

    def append(self, transition: PortfolioTransition) -> None:
        self.appends += 1
        self.book.ledger.append(transition)

    def list(self) -> tuple[PortfolioTransition, ...]:
        return self.book.ledger.list()


class Interloper:
    """The paper ledger, with another writer appending `row` just before the evening's first
    append -- after the evening has read the ledger and found nothing in its way."""

    def __init__(self, inner: SQLitePortfolioLedger, row: PortfolioTransition) -> None:
        self.inner, self.row = inner, row
        self.interfered = False

    def append(self, transition: PortfolioTransition) -> None:
        if not self.interfered:
            self.interfered = True
            self.inner.append(self.row)
        self.inner.append(transition)

    def list(self) -> tuple[PortfolioTransition, ...]:
        return self.inner.list()


class CutAtRow:
    """The paper ledger, with the power cut as the evening's `at`-th row is appended."""

    def __init__(self, inner: SQLitePortfolioLedger, at: int) -> None:
        self.inner, self.at, self.appended = inner, at, 0

    def list(self) -> tuple[PortfolioTransition, ...]:
        return self.inner.list()

    def append(self, transition: PortfolioTransition) -> None:
        self.appended += 1
        if self.appended == self.at:
            raise OSError(f"the power was cut as row {self.at} of the evening was appended")
        self.inner.append(transition)


# The book the evening of S3 settles. Opened with 100,000.00 on E0.
#
# S1 is the initial build: 600010.SH, 600020.SH and 600040.SH are the only survivors and all three
# are drawn. The equal share 100,000.00 / 10 x 0.995 = 9,950.00 buys 900 at 10.00, 800 at 11.50
# and 900 at 10.00.
SCREEN_S1 = screened((A, "10.00"), (B, "11.50"), (DL, "10.00"))
PRICES_S1 = {A: "10.00", B: "11.50", DL: "10.00"}
# S2: the three buys fill at their opens -- 9,000.00 + 5.00 + 0.09 = 9,005.09, 9,200.00 + 5.00 +
# 0.09 = 9,205.09 and 9,005.09 -- leaving cash 72,784.73; at the closes the book is worth
# 72,784.73 + 27,200.00 = 99,984.73. 600010.SH is still eligible but no longer a survivor, so it
# is sold; of the entrants 600030.SH and 600050.SH (the pool is the top 4 - floor(4 x 0.25) = 3
# survivors less the held) both are drawn, two being the cap: the equal share 99,984.73 / 10 x
# 0.995 = 9,948.48 buys 900 at 10.00 and 400 at 20.00.
MARKET_S2 = market(quiet(A, S2, "10.00"), quiet(B, S2, "11.50"), quiet(DL, S2, "10.00"))
SCREEN_S2 = screened((B, "11.50"), (C, "10.00"), (E, "20.00"), (DL, "10.00"), dropped=(A,))
PRICES_S2 = {C: "10.00", E: "20.00"}
# S3 -- the evening under test. 600010.SH sells at its 10.00 open, 600030.SH is bought at its
# 10.00 open, 600050.SH opens locked at its 22.00 limit-up and is refused, 600020.SH trades
# quietly, and 600040.SH is delisted: no bar, no factor, no price anywhere.
MARKET_S3 = market(
    bar(A, S3, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
    quiet(B, S3, "11.50"),
    bar(C, S3, "10.00", "10.00", "10.20", "9.95", "10.10", "11.00", "9.00"),
    limit_up(E, S3, "20.00"),
)
SCREEN_S3 = screened(
    (F, "10.00"), (G, "15.00"), (B, "11.50"), (C, "10.10"), (E, "22.00"), (H, "8.00")
)
PRICES_S3 = {E: "22.00", F: "10.00", G: "15.00", H: "8.00"}
"""Real closes of the names not held; 600040.SH, delisted, is in neither the market nor here."""


def through_s2(root: Path) -> Book:
    book = book_at(root)
    evening(book, S1, NOTHING, SCREEN_S1, PRICES_S1)
    evening(book, S2, MARKET_S2, SCREEN_S2, PRICES_S2)
    return book


def tonight(book: Book, **changes: object) -> EveningResult:
    """The evening of S3, as the fixture above describes it, unless `changes` says otherwise."""
    arguments: dict[str, object] = {
        "quotes": MARKET_S3,
        "screen": SCREEN_S3,
        "prices": PRICES_S3,
        "delisted": {DL},
    }
    arguments.update(changes)
    return evening(book, S3, **arguments)  # type: ignore[arg-type]


def due_order(book: Book, side: str, code: str) -> TradeOrder:
    """The order for `code` the book's latest snapshot holds pending."""
    head = load_snapshot(book.store, book.store.sessions()[-1])
    return next(
        item.order
        for item in head.pending_orders
        if item.order.side == side and item.order.ts_code == code
    )


def documents(store: FilePaperStateStore) -> dict[str, tuple[bytes, int, int]]:
    """Every file in the store's directory: its bytes, inode and modification time."""
    if not store.path.is_dir():
        return {}
    return {
        entry.name: (entry.read_bytes(), entry.stat().st_ino, entry.stat().st_mtime_ns)
        for entry in sorted(store.path.iterdir())
    }


def opened(root: Path, cash: str = "100000.00") -> FilePaperStateStore:
    store = FilePaperStateStore(root / "snapshots")
    open_book(store=store, session=E0, session_index=0, cash=D(cash), config_digest=DIGEST)
    return store


# ---- the opening -------------------------------------------------------------------------------


def test_opening_a_book_saves_an_opening_snapshot_of_cash_alone(tmp_path: Path) -> None:
    """The opening book is the epoch's snapshot: cash as given, nothing held, nothing settled,
    nothing decided -- so the first evening after it is the initial build."""
    store = FilePaperStateStore(tmp_path / "snapshots")

    opening = open_book(
        store=store, session=E0, session_index=0, cash=D("100000.00"), config_digest=DIGEST
    )

    assert opening == PaperBookSnapshot(
        config_digest=DIGEST,
        session_index=0,
        session=E0,
        book=PortfolioState(as_of=E0, cash=D("100000.00")),
        last_settled_session=None,
        first_decision_session=None,
    )
    assert store.sessions() == (E0,)
    assert load_snapshot(store, E0) == opening
    (name,) = documents(store)
    assert name == f"2021-03-01.{opening.snapshot_id}.json"
    assert (store.path / name).read_text(encoding="utf-8") == snapshot_payload(opening)


@pytest.mark.parametrize("cash", ["100000.00", "100000", "100000.0"])
def test_opening_the_same_book_again_writes_nothing_however_its_cash_is_spelled(
    tmp_path: Path, cash: str
) -> None:
    """The daily entry opens the book on every run, with the registration's epoch, capital and
    digest: the same opening again is a no-op. The cash is held to the fen, so 100000,
    100000.0 and 100000.00 open one and the same book."""
    store = opened(tmp_path, cash="100000")
    before = documents(store)

    again = open_book(store=store, session=E0, session_index=0, cash=D(cash), config_digest=DIGEST)

    assert again == load_snapshot(store, E0)
    assert '"cash":"100000.00"' in snapshot_payload(again)
    assert documents(store) == before


OPENED = (
    rf"a paper book is already open in this runtime directory -- opened on 2021-03-01 with "
    rf"cash 100000\.00 under configuration digest {DIGEST} as pbs_[0-9a-f]{{24}} -- and this "
    r"opening differs in "
)


@pytest.mark.parametrize(
    ("session", "cash", "digest", "differs"),
    [
        (E0, "100000.01", DIGEST, r"its cash \(100000\.01\)"),
        (E0, "100000.00", OTHER_DIGEST, rf"its configuration digest \({OTHER_DIGEST}\)"),
        (S1, "100000.00", DIGEST, r"its epoch \(2021-03-02\)"),
        (
            S1,
            "1.00",
            OTHER_DIGEST,
            rf"its epoch \(2021-03-02\), its cash \(1\.00\), its configuration digest "
            rf"\({OTHER_DIGEST}\)",
        ),
    ],
    ids=["other cash", "other configuration", "another epoch", "a second book"],
)
def test_an_opening_never_replaces_or_restarts_a_book_already_held(
    tmp_path: Path, session: date, cash: str, digest: str, differs: str
) -> None:
    """One paper book per runtime directory, opened once: an opening that is not the book's own
    is refused, naming the book that is open and every way the opening differs from it, and the
    store keeps its bytes."""
    store = opened(tmp_path)
    before = documents(store)

    with pytest.raises(PaperStateError, match=OPENED + differs + ": one paper book"):
        open_book(store=store, session=session, session_index=0, cash=D(cash), config_digest=digest)
    assert documents(store) == before


@pytest.mark.parametrize(
    ("cash", "index", "refusal"),
    [
        ("0.00", 0, r"opening cash must be a positive amount: 0\.00"),
        ("-1.00", 0, r"opening cash must be a positive amount: -1\.00"),
        ("NaN", 0, r"opening cash must be a positive amount: NaN"),
        ("100000.005", 0, r"opening cash must be a whole number of fen: 100000\.005"),
        ("100000.00", -1, r"opens on its epoch, ordinal 0 by definition: given ordinal -1"),
        ("100000.00", 3, r"opens on its epoch, ordinal 0 by definition: given ordinal 3"),
    ],
    ids=[
        "no cash",
        "negative cash",
        "not a number",
        "part of a fen",
        "before the epoch",
        "not the epoch",
    ],
)
def test_an_opening_the_book_could_never_trade_from_is_refused_before_anything_is_written(
    tmp_path: Path, cash: str, index: int, refusal: str
) -> None:
    """A book is opened once per runtime directory, so an opening nothing could be decided from
    -- the kernel sizes from a positive equity -- would be a directory spoiled for good, and so
    would an amount the ledger cannot hold to the fen. The epoch is ordinal 0 by definition: a
    book opened on any other ordinal numbers its sessions from somewhere else."""
    store = FilePaperStateStore(tmp_path / "snapshots")

    with pytest.raises(ValueError, match=refusal):
        open_book(store=store, session=E0, session_index=index, cash=D(cash), config_digest=DIGEST)
    assert store.sessions() == ()
    assert not store.path.exists()


def test_once_evenings_are_settled_the_same_opening_is_still_a_no_op_and_another_is_refused(
    tmp_path: Path,
) -> None:
    book = through_s2(tmp_path)
    before = documents(book.store)

    again = open_book(
        store=book.store, session=E0, session_index=0, cash=D("100000.00"), config_digest=DIGEST
    )

    assert again == load_snapshot(book.store, E0)
    with pytest.raises(PaperStateError, match=OPENED + r"its epoch \(2021-03-04\), its cash"):
        open_book(
            store=book.store, session=S3, session_index=0, cash=D("1.00"), config_digest=DIGEST
        )
    assert documents(book.store) == before


# ---- one evening -------------------------------------------------------------------------------


def test_the_first_evening_after_the_opening_is_the_initial_build_of_up_to_ten_names(
    tmp_path: Path,
) -> None:
    """The opening book holds nothing and has decided nothing, so the first evening settles
    without a step (no name to ask about, no ledger row) and the kernel builds the book at once:
    ten entries, not the two a day of an initialised book. Sixteen survivors at 10.00: the pool
    is the top 16 - floor(16 x 0.25) = 12, and ten are drawn. The equal share 350,000.00 / 10 x
    0.995 = 34,825.00 buys 3,400 at 10.00 = 34,000.00; after nine buys 44,000.00 x 0.995 still
    covers 4,300, so the tenth is funded too."""
    book = book_at(tmp_path, cash="350000.00")
    names = [f"6001{n:02d}.SH" for n in range(16)]

    result = evening(
        book,
        S1,
        NOTHING,
        screened(*((name, "10.00") for name in names)),
        dict.fromkeys(names, "10.00"),
    )

    assert result.outcome == "created"
    assert result.settlement is not None and result.decision is not None
    assert result.trade_list is not None
    assert result.settlement.initialized is False
    assert [entry.reason for entry in result.decision.entries] == ["INITIAL_BUILD"] * 10
    assert {entry.ts_code for entry in result.decision.entries} <= set(names[:12])
    assert [
        (o.side, o.quantity, o.reference_price, o.reason, o.execute_session, o.expires_session)
        for o in result.trade_list.orders
    ] == [("buy", 3400, D("10.00"), "INITIAL_BUILD", 2, 4)] * 10
    assert result.trade_list.unfunded == ()
    snapshot = result.snapshot
    assert (snapshot.session, snapshot.last_settled_session, snapshot.first_decision_session) == (
        S1,
        1,
        1,
    )
    assert snapshot.book == PortfolioState(as_of=S1, cash=D("350000.00"))
    assert [item.attempts for item in snapshot.pending_orders] == [0] * 10
    assert load_snapshot(book.store, S1) == snapshot
    assert book.ledger.list() == ()


def test_the_book_the_crash_tests_settle_is_the_one_the_fixture_describes(tmp_path: Path) -> None:
    book = through_s2(tmp_path)
    s2 = load_snapshot(book.store, S2)

    assert s2.book.cash == D("72784.73")
    assert [(p.subject, p.lots) for p in s2.book.positions] == [
        (A, (PositionLot(open_date=S2, quantity=900, cost_basis=D("9005.09")),)),
        (B, (PositionLot(open_date=S2, quantity=800, cost_basis=D("9205.09")),)),
        (DL, (PositionLot(open_date=S2, quantity=900, cost_basis=D("9005.09")),)),
    ]
    assert s2.book.equity == D("99984.73")
    assert [
        (p.order.side, p.order.ts_code, p.order.quantity, p.order.reference_price, p.attempts)
        for p in s2.pending_orders
    ] == [
        ("buy", C, 900, D("10.00"), 0),
        ("buy", E, 400, D("20.00"), 0),
        ("sell", A, 900, D("10.00"), 0),
    ]
    assert s2.first_decision_session == 1
    assert len(book.ledger.list()) == 3


def test_an_evening_settles_the_session_writes_off_the_delisted_holding_then_decides(
    tmp_path: Path,
) -> None:
    """The evening of S3, end to end, in the order facts -> screen -> settle -> decide -> plan.

    Settled first: 600010.SH's 900 sell at 10.00 -- 9,000.00 less 5.00 + 0.09 + 4.50 =
    8,990.41, realised 8,990.41 - 9,005.09 = -14.68, cash 81,775.14; then 600030.SH's 900 buy at
    10.00 for 9,005.09, cash 72,770.05; 600050.SH's buy is refused at its limit-up open and stays
    live, tried once. Then the write-off: 600040.SH, delisted with no bar, is removed with no
    price and nothing recovered -- cash unchanged, realised -9,005.09 (-9,019.77 in all) -- so the
    kernel is never asked to sell a name that has no price, and the evening never asks for one
    (it is in neither the market nor the entrants' prices). At the closes: 72,770.05 + 800 x 11.50
    + 900 x 10.10 = 91,060.05; fees 3 x 5.09 + 9.59 + 5.09 = 29.95.

    Then decided, on that settled book: 600020.SH and 600030.SH held, 600050.SH's buy live,
    600010.SH cooling down from 3. Of the survivors' pool (the top 6 - floor(6 x 0.25) = 5)
    600060.SH and 600070.SH are free, and two is the cap: the equal share 91,060.05 / 10 x 0.995 =
    9,060.47 buys 900 at 10.00 and 600 at 15.00."""
    book = through_s2(tmp_path)
    sell_a = due_order(book, "sell", A)
    buy_c = due_order(book, "buy", C)
    buy_e = due_order(book, "buy", E)

    result = tonight(book)

    assert result.outcome == "created"
    settlement, decision, plan = result.settlement, result.decision, result.trade_list
    assert settlement is not None and decision is not None and plan is not None
    assert outcomes(book.ledger)[3:] == [
        (attempt_id(sell_a, S3), "filled", None),
        (attempt_id(buy_c, S3), "filled", None),
        (attempt_id(buy_e, S3), "rejected", "buy cannot fill on a one-price limit-up bar"),
        (write_off_id(DL, S3), "filled", WRITE_OFF_REASON),
    ]
    assert list(book.ledger.list())[3:] == list(settlement.transitions)
    (written,) = settlement.written_off
    assert written.transition.execution is None
    assert written.transition.realized_pnl_delta == D("-9005.09")
    assert written.transition.after.cash == written.transition.before.cash == D("72770.05")
    assert settlement.book.cash == D("72770.05")
    assert settlement.book.realized_pnl == D("-9019.77")
    assert settlement.book.fees_paid == D("29.95")
    assert settlement.equity == D("91060.05")

    assert decision.exits == ()
    assert sorted((entry.ts_code, entry.reason) for entry in decision.entries) == [
        (F, "ENTRY_REPLACEMENT"),
        (G, "ENTRY_REPLACEMENT"),
    ]
    assert sorted((o.side, o.ts_code, o.quantity, o.reference_price) for o in plan.orders) == [
        ("buy", F, 900, D("10.00")),
        ("buy", G, 600, D("15.00")),
    ]
    assert plan.carried == (buy_e,)

    snapshot = result.snapshot
    assert load_snapshot(book.store, S3) == snapshot  # its checks held as it was saved and read
    assert [(p.subject, p.lots) for p in snapshot.book.positions] == [
        (B, (PositionLot(open_date=S2, quantity=800, cost_basis=D("9205.09")),)),
        (C, (PositionLot(open_date=S3, quantity=900, cost_basis=D("9005.09")),)),
    ]
    assert snapshot.entry_factors == (
        EntryFactor(ts_code=B, entry_adj=D("1.000")),
        EntryFactor(ts_code=C, entry_adj=D("1.000")),
    )
    assert [(p.order.ts_code, p.attempts) for p in snapshot.pending_orders] == [
        (E, 1),
        (F, 0),
        (G, 0),
    ]
    assert snapshot.cooldowns == (Cooldown(ts_code=A, since_session=3),)
    assert (snapshot.last_settled_session, snapshot.first_decision_session) == (3, 1)


def test_a_buy_whose_name_left_tonights_survivors_is_cancelled_by_tonights_settlement(
    tmp_path: Path,
) -> None:
    """The settlement reads tonight's screen: 600050.SH's buy, refused at the open, is not
    carried once its name has dropped out of the survivors -- cancelled, with no cool-down."""
    book = through_s2(tmp_path)
    buy_e = due_order(book, "buy", E)
    without_e = screened((F, "10.00"), (G, "15.00"), (B, "11.50"), (C, "10.10"), (H, "8.00"))

    result = tonight(book, screen=without_e)

    assert result.settlement is not None
    assert result.settlement.cancelled == (buy_e,)
    assert [p.order.ts_code for p in result.snapshot.pending_orders] == [F, G]
    assert result.snapshot.cooldowns == (Cooldown(ts_code=A, since_session=3),)


TRIO = SurvivorBookConfig(seed=7, holding_count=3)
"""Three holdings: a book whose equal share is a third of it, so one fill moves its cash a lot."""


def trio_through_s2(root: Path) -> Book:
    """Opened with 30,000.00 under `TRIO`. S1 builds 600010.SH, 600020.SH and 600040.SH: the
    equal share 30,000.00 / 3 x 0.995 = 9,950.00 buys 900 of each at 10.00. On S2 600010.SH and
    600040.SH fill at their 10.00 opens (9,000.00 + 5.00 + 0.09 = 9,005.09 each, cash
    11,989.82) and 600020.SH opens locked at its 11.00 limit-up: refused, carried. Two held and
    one buy live fill the three places, so S2 plans nothing."""
    store = FilePaperStateStore(root / "snapshots")
    open_book(
        store=store,
        session=E0,
        session_index=0,
        cash=D("30000.00"),
        config_digest=survivor_config_digest(TRIO),
    )
    book = Book(root=root, store=store, ledger=SQLitePortfolioLedger(root / "ledger.sqlite3"))
    trio = screened((A, "10.00"), (B, "10.00"), (DL, "10.00"))
    evening(book, S1, NOTHING, trio, {A: "10.00", B: "10.00", DL: "10.00"}, config=TRIO)
    s2 = market(quiet(A, S2, "10.00"), limit_up(B, S2, "10.00"), quiet(DL, S2, "10.00"))
    planned = evening(
        book,
        S2,
        s2,
        screened((A, "10.00"), (B, "11.00"), (DL, "10.00")),
        {B: "11.00"},
        config=TRIO,
    )
    assert planned.trade_list.orders == ()
    assert planned.snapshot.book.cash == D("11989.82")
    return book


B_FILLS_S3 = bar(B, S3, "11.00", "10.00", "10.00", "10.00", "10.00", "12.10", "9.90")
"""600020.SH opens at 10.00 on S3, off the limit: its carried buy of 900 fills for 9,005.09."""


def test_tonights_buys_are_sized_from_the_cash_tonights_fills_left(tmp_path: Path) -> None:
    """The book held 11,989.82 last night. Tonight 600020.SH's carried buy fills for 9,005.09,
    leaving 2,984.73, and 600040.SH -- delisted, no bar -- is written off, freeing a place that
    600060.SH is drawn for. The equal share, 20,984.73 / 3 x 0.995 = 6,960.94, would buy 600;
    the cash, 2,984.73 x 0.995 = 2,969.81, buys 200. Planned from last night's cash the book
    would order 600 it cannot pay for."""
    book = trio_through_s2(tmp_path)

    result = evening(
        book,
        S3,
        market(quiet(A, S3, "10.00"), B_FILLS_S3),
        screened((A, "10.00"), (B, "10.00"), (F, "10.00")),
        {F: "10.00"},
        delisted={DL},
        config=TRIO,
    )

    assert result.settlement.book.cash == D("2984.73")
    assert result.settlement.equity == D("20984.73")
    assert [
        (o.side, o.ts_code, o.quantity, o.reference_price) for o in result.trade_list.orders
    ] == [("buy", F, 200, D("10.00"))]
    assert result.trade_list.unfunded == ()


def test_a_held_name_is_sold_at_its_ledger_mark_whatever_the_entrants_prices_say(
    tmp_path: Path,
) -> None:
    """600010.SH goes ex-rights 1 for 1 on S3: its factor doubles to 2.000 and its real prices
    halve -- it closes at 5.05, so the ledger, which keeps the 900 shares it bought at a factor
    of 1.000, marks it at 5.05 x 2 = 10.10. It has lost its place among the survivors, so it is
    sold, and the sale is estimated at that mark: 900 x 10.10 = 9,090.00, the holding's value.
    The real close, 5.05, is in the entrants' prices too (a caller passing every close); a held
    name's price comes from the ledger, whatever they say -- at 5.05 the sale would be estimated
    at half of what it will raise."""
    book = trio_through_s2(tmp_path)
    ex_rights = bar(A, S3, "5.00", "5.00", "5.10", "4.95", "5.05", "5.50", "4.50")

    result = evening(
        book,
        S3,
        Market(
            bars={A: ex_rights, B: B_FILLS_S3, DL: quiet(DL, S3, "10.00")},
            adj_factor={A: D("2.000"), B: D("1.000"), DL: D("1.000")},
        ),
        screened((B, "10.00"), (DL, "10.00"), (F, "10.00"), dropped=(A,)),
        {A: "5.05", F: "10.00"},
        config=TRIO,
    )

    (sale,) = [order for order in result.trade_list.orders if order.side == "sell"]
    assert (sale.ts_code, sale.quantity, sale.reason) == (A, 900, "EXIT_SURVIVOR_LOST")
    assert sale.reference_price == D("10.10") == result.settlement.book.mark(A)


# ---- the crash matrix --------------------------------------------------------------------------


def test_a_crash_as_the_snapshot_is_written_reruns_to_the_same_bytes_with_each_row_once(
    tmp_path: Path,
) -> None:
    """The ledger rows are appended before the snapshot is saved, so a crash at the save leaves
    every row of the evening in the ledger and no snapshot. The session is not settled: the next
    evening is refused, naming the gap, and the evening run again appends nothing new (each row
    is the same transition under the same id) and saves the very bytes of an uninterrupted run."""
    clean = through_s2(tmp_path / "clean")
    tonight(clean)
    book = through_s2(tmp_path / "crashed")

    with pytest.raises(OSError, match="the power was cut as the snapshot was written"):
        tonight(book, store=CutAsTheSnapshotIsWritten(book.store))

    assert rows(book.ledger) == rows(clean.ledger)
    assert book.store.sessions() == (E0, S1, S2)
    with pytest.raises(
        PaperStateError,
        match=r"the snapshot for session index 3 is missing: the latest is 2 \(2021-03-03\)",
    ):
        evening(book, S4, NOTHING, SCREEN_S3, PRICES_S3)
    again = tonight(book)
    assert again.outcome == "created"
    assert rows(book.ledger) == rows(clean.ledger)
    assert snapshot_bytes(book.store) == snapshot_bytes(clean.store)


@pytest.mark.parametrize("cut_at", [1, 2, 3, 4], ids=lambda k: f"cut-at-row-{k}")
def test_a_crash_between_ledger_rows_reruns_to_the_same_bytes_with_each_row_once(
    tmp_path: Path, cut_at: int
) -> None:
    """The evening appends four rows (a sale, a buy, a refused buy, a write-off); the power is
    cut as row k is appended, so rows 1 ... k-1 are in and there is no snapshot. Nothing is
    settled -- the next evening is refused by name -- and the evening run again converges: the
    same rows, each once, and the same snapshot bytes as an uninterrupted run."""
    clean = through_s2(tmp_path / "clean")
    tonight(clean)
    book = through_s2(tmp_path / "crashed")
    settled_before = len(rows(book.ledger))

    with pytest.raises(OSError, match=rf"row {cut_at} of the evening"):
        tonight(book, ledger=CutAtRow(book.ledger, cut_at))

    assert rows(book.ledger) == rows(clean.ledger)[: settled_before + cut_at - 1]
    assert book.store.sessions() == (E0, S1, S2)
    with pytest.raises(PaperStateError, match=r"the snapshot for session index 3 is missing"):
        evening(book, S4, NOTHING, SCREEN_S3, PRICES_S3)
    again = tonight(book)
    assert again.outcome == "created"
    assert rows(book.ledger) == rows(clean.ledger)
    assert len(rows(clean.ledger)) == settled_before + 4
    assert len({row.order.order_id for row in book.ledger.list()}) == settled_before + 4
    assert snapshot_bytes(book.store) == snapshot_bytes(clean.store)


ALL_IN = SurvivorBookConfig(seed=7, holding_count=1, cost_reserve_fraction=D("0"))
"""One holding and no cost reserve: a book that can spend every fen it has."""


def _written_off_to_nothing(root: Path) -> Book:
    """A one-name book that spent every fen: opened with 9,005.09 under `ALL_IN`, it buys 1,000
    of 600090.SH at 9.00 on S2 for 9,000.00 + 5.00 + 0.09 = 9,005.09 and holds no cash."""
    store = FilePaperStateStore(root / "snapshots")
    open_book(
        store=store,
        session=E0,
        session_index=0,
        cash=D("9005.09"),
        config_digest=survivor_config_digest(ALL_IN),
    )
    book = Book(root=root, store=store, ledger=SQLitePortfolioLedger(root / "ledger.sqlite3"))
    only = screened(("600090.SH", "9.00"))
    evening(book, S1, NOTHING, only, {"600090.SH": "9.00"}, config=ALL_IN)
    evening(book, S2, market(quiet("600090.SH", S2, "9.00")), only, {}, config=ALL_IN)
    assert load_snapshot(book.store, S2).book.cash == D("0.00")
    return book


@pytest.mark.parametrize(
    "case",
    ["an entrant with no reference price", "a book written off to nothing"],
)
def test_a_refusal_in_the_decision_leaves_the_ledger_and_the_store_byte_identical(
    tmp_path: Path, case: str
) -> None:
    """Every refusal the evening can meet after settling happens before the real ledger is
    touched: the session is settled against a dry-run ledger, decided and planned, and only then
    committed. Both evenings here appended rows in the dry run -- S3 its sale, buy, refused buy
    and write-off; the one-name book the write-off that leaves it worth nothing, which the
    kernel refuses to size from -- and the real ledger and the store keep every byte."""
    if case == "an entrant with no reference price":
        book = through_s2(tmp_path)
        before = (ledger_files(book), documents(book.store))
        with pytest.raises(ValueError, match=r"no reference price for 600070\.SH"):
            tonight(book, prices={E: "22.00", F: "10.00", H: "8.00"})
    else:
        book = _written_off_to_nothing(tmp_path)
        before = (ledger_files(book), documents(book.store))
        with pytest.raises(ValueError, match="equity"):
            evening(
                book,
                S3,
                NOTHING,
                screened(("600091.SH", "9.00")),
                {"600091.SH": "9.00"},
                delisted={"600090.SH"},
                config=ALL_IN,
            )
    assert (ledger_files(book), documents(book.store)) == before
    assert book.store.sessions()[-1] == S2


def test_a_rerun_after_the_snapshot_was_saved_writes_nothing_and_gives_the_evening_back(
    tmp_path: Path,
) -> None:
    """The daily entry crashed after the snapshot was saved and before its journal was written.
    The evening run again -- the next morning, on the inputs the journal pinned -- finds the
    session settled: it writes nothing (no row appended, no snapshot put, every byte as it was)
    and derives the evening again from the snapshot of S2, giving the journal the same
    settlement, decision and trade list as the first run."""
    book = through_s2(tmp_path)
    first = tonight(book)
    before = (ledger_files(book), documents(book.store))
    watched = Watched(book)

    again = tonight(book, observed_on=S5, store=watched, ledger=watched)

    assert again.outcome == "unchanged"
    assert again.snapshot == first.snapshot == load_snapshot(book.store, S3)
    assert (again.trade_list, again.decision) == (first.trade_list, first.decision)
    assert again.trade_list.trade_list_id == first.trade_list.trade_list_id
    # The same settlement, but for the date its inputs were read on, which the runner notes.
    assert again.settlement == read_on(first.settlement, S5)
    assert (watched.puts, watched.appends) == (0, 0)
    assert (ledger_files(book), documents(book.store)) == before


@pytest.mark.parametrize(
    "change",
    [
        {"screen": screened((G, "15.00"), (F, "10.00"), (B, "11.50"), (C, "10.10"), (H, "8.00"))},
        {"prices": {E: "22.00", F: "10.50", G: "15.00", H: "8.00"}},
    ],
    ids=["another screen", "another entrant price"],
)
def test_a_rerun_after_the_snapshot_was_saved_on_other_inputs_is_refused_writing_nothing(
    tmp_path: Path, change: dict[str, object]
) -> None:
    """Derived again on inputs other than the ones the session was settled with, the evening is
    not the held one -- here the screen drops 600050.SH, so its live buy is cancelled, or
    600060.SH is priced at 10.50 and 800 are planned, not 900. With the inputs pinned by the
    daily entry's journal that is a fault, and it is refused by name; nothing is written."""
    book = through_s2(tmp_path)
    first = tonight(book)
    before = (ledger_files(book), documents(book.store))
    watched = Watched(book)

    with pytest.raises(
        PaperStateError,
        match=rf"2021-03-04 is settled as {first.snapshot.snapshot_id}, and this evening derives "
        r"pbs_[0-9a-f]{24} from the snapshot before it: its inputs differ",
    ):
        tonight(book, store=watched, ledger=watched, **change)
    assert (watched.puts, watched.appends) == (0, 0)
    assert (ledger_files(book), documents(book.store)) == before


def _partially_committed(root: Path) -> Book:
    """The book through S2, with S3's evening cut after its first two rows (the sale of
    600010.SH and the buy of 600030.SH) are in the ledger and before anything else."""
    book = through_s2(root)
    with pytest.raises(OSError, match="row 3 of the evening"):
        tonight(book, ledger=CutAtRow(book.ledger, 3))
    assert len(book.ledger.list()) == 3 + 2
    return book


def test_a_rerun_with_other_inputs_after_a_partial_commit_is_refused_before_any_append(
    tmp_path: Path,
) -> None:
    """Run again on a market read differently -- 600010.SH now opening at 10.05 -- the sale is
    another transition under an attempt id the ledger already holds for S3, and so is the buy
    after it (the cash it starts from differs). The evening reads the session's rows before it
    appends one and refuses, naming the class Part D maps to exit 1, the session, both ids and
    the remedy: nothing is appended, no snapshot is saved."""
    book = _partially_committed(tmp_path)
    sell_a = attempt_id(due_order(book, "sell", A), S3)
    buy_c = attempt_id(due_order(book, "buy", C), S3)
    both = ", ".join(f"'{order_id}'" for order_id in sorted([sell_a, buy_c]))
    moved = market(
        bar(A, S3, "10.00", "10.05", "10.20", "9.90", "10.10", "11.00", "9.00"),
        quiet(B, S3, "11.50"),
        bar(C, S3, "10.00", "10.00", "10.20", "9.95", "10.10", "11.00", "9.00"),
        limit_up(E, S3, "20.00"),
    )
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(
        PaperLedgerConflictError,
        match=rf"2021-03-04.*rows this run would write otherwise: \[{both}\].*journal-pinned",
    ):
        tonight(book, quotes=moved)

    assert (ledger_files(book), documents(book.store)) == before
    assert book.store.sessions() == (E0, S1, S2)


def test_a_rerun_that_would_leave_a_row_of_the_first_run_behind_is_refused_before_any_append(
    tmp_path: Path,
) -> None:
    """The review's probe. The first run is cut after the sale of 600010.SH and the buy of
    600030.SH; run again on a market in which 600030.SH has no bar, the buy is not attempted --
    the run would write no row under that attempt id, and the fill the first run wrote would
    stay in the ledger beside a snapshot that keeps the buy pending, to fill a second time the
    next session. The row the run does not produce is named and nothing is written."""
    book = _partially_committed(tmp_path)
    buy_c = attempt_id(due_order(book, "buy", C), S3)
    without_c = market(
        bar(A, S3, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
        quiet(B, S3, "11.50"),
        limit_up(E, S3, "20.00"),
    )
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(
        PaperLedgerConflictError,
        match=rf"2021-03-04.*rows this run would not write: \['{buy_c}'\].*journal-pinned",
    ):
        tonight(book, quotes=without_c)

    assert (ledger_files(book), documents(book.store)) == before


def test_a_catch_up_of_a_half_committed_evening_writes_the_rest_of_the_same_rows(
    tmp_path: Path,
) -> None:
    """A half-committed evening's session settles the same whether it is run again as an
    evening or caught up (settling does not depend on the decision): the rows already in are
    the catch-up's own, and it appends the rest."""
    clean = through_s2(tmp_path / "clean")
    tonight(clean)
    book = _partially_committed(tmp_path / "half")

    mended = catch_up(book, S3, MARKET_S3, delisted={DL})

    assert mended.outcome == "created"
    assert rows(book.ledger) == rows(clean.ledger)


def test_a_writer_between_the_evenings_check_and_its_commit_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """What the read before the first append cannot see: another writer appending, between the
    read and the append, a different row under one of the evening's ids. The ledger refuses the
    evening's row, and the evening reports it as the named class -- the session, the id, how
    many of its rows were in, the remedy -- never as the ledger's bare `ValueError`."""
    clean = through_s2(tmp_path / "clean")
    first = tonight(clean).settlement.transitions[0]
    book = through_s2(tmp_path / "book")
    other = first.model_copy(update={"realized_pnl_delta": D("0.01")})

    with pytest.raises(
        PaperLedgerConflictError,
        match=rf"2021-03-04.*{first.order.order_id}.*0 of the session's 4 rows.*journal-pinned",
    ) as refused:
        tonight(book, ledger=Interloper(book.ledger, other))

    assert isinstance(refused.value, PaperStateError)
    assert isinstance(refused.value.__cause__, ValueError)
    assert book.store.sessions() == (E0, S1, S2)


def test_a_settled_session_whose_rows_the_ledger_no_longer_holds_is_refused_on_a_rerun(
    tmp_path: Path,
) -> None:
    """A run after the snapshot is saved derives the session again, and the ledger must hold
    exactly the rows it derives -- the same ids and payloads, none missing, none extra. A row
    deleted from the ledger's database behind the book's back is named; nothing is written."""
    book = through_s2(tmp_path)
    buy_c = attempt_id(due_order(book, "buy", C), S3)
    tonight(book)
    with closing(sqlite3.connect(book.root / "ledger.sqlite3")) as connection, connection:
        connection.execute("DELETE FROM portfolio_transitions WHERE order_id = ?", (buy_c,))
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(
        PaperLedgerConflictError,
        match=rf"2021-03-04.*rows the session wrote that the ledger does not hold: \['{buy_c}'\]",
    ):
        tonight(book)

    assert (ledger_files(book), documents(book.store)) == before


# ---- what an evening refuses before anything is written ----------------------------------------


def test_an_evening_under_another_configuration_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    book = through_s2(tmp_path)
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(
        PaperStateError,
        match=rf"settled under configuration digest {DIGEST} through 2021-03-03, and this "
        rf"settlement runs under {OTHER_DIGEST}",
    ):
        tonight(book, config=OTHER_CONFIG)
    assert (ledger_files(book), documents(book.store)) == before

    tonight(book)
    settled = (ledger_files(book), documents(book.store))
    with pytest.raises(
        PaperStateError,
        match=rf"2021-03-04 is settled under configuration digest {DIGEST} and this evening "
        rf"runs under {OTHER_DIGEST}",
    ):
        tonight(book, config=OTHER_CONFIG)
    with pytest.raises(PaperStateError, match=r"2021-03-04 is settled as session index 3, not 7"):
        tonight(book, index=7)
    assert (ledger_files(book), documents(book.store)) == settled


def test_an_evening_of_a_book_never_opened_on_its_epoch_or_before_it_is_refused(
    tmp_path: Path,
) -> None:
    never = Book(
        root=tmp_path / "never",
        store=FilePaperStateStore(tmp_path / "never" / "snapshots"),
        ledger=SQLitePortfolioLedger(tmp_path / "never" / "ledger.sqlite3"),
    )
    with pytest.raises(PaperStateError, match="no paper book snapshot is held at all"):
        evening(never, S1, NOTHING, SCREEN_S1, PRICES_S1)

    book = book_at(tmp_path / "opened")
    before = (ledger_files(book), documents(book.store))
    with pytest.raises(
        PaperStateError,
        match=r"2021-03-01 is the opening of the book, which no evening settles; its first "
        r"evening is the session after it",
    ):
        evening(book, E0, NOTHING, SCREEN_S1, PRICES_S1)
    with pytest.raises(
        PaperStateError, match=r"snapshots dated after 2021-02-26, the session being settled"
    ):
        evening(book, date(2021, 2, 26), NOTHING, SCREEN_S1, PRICES_S1)
    assert (ledger_files(book), documents(book.store)) == before


def test_a_portfolio_that_does_not_record_into_the_dry_run_is_refused_before_it_settles(
    tmp_path: Path,
) -> None:
    """The evening hands the factory a dry-run ledger and settles on what it builds; a factory
    that builds the book on another ledger -- the real one it closes over -- would append the
    session there before anything is decided. Refused by name, before the settlement runs."""
    book = through_s2(tmp_path)
    before = (ledger_files(book), documents(book.store))

    def on_the_real_ledger(_: PortfolioLedger) -> PaperPortfolio:
        return make_portfolio(book.ledger)

    with pytest.raises(ValueError, match="the dry run's ledger"):
        tonight(book, factory=on_the_real_ledger)
    assert (ledger_files(book), documents(book.store)) == before


def test_a_portfolio_whose_runner_records_elsewhere_is_refused_before_the_commit(
    tmp_path: Path,
) -> None:
    """The identity check sees the ledger a `PaperPortfolio` names, not the one its runner was
    built with. A book whose runner records somewhere else -- its `ledger` set to the dry run's
    after it was built -- gives the dry run only the write-off, never the runner's rows; the
    evening compares what the dry run received with the settlement's transitions and refuses,
    by name, before anything real is touched."""
    book = through_s2(tmp_path)
    before = (ledger_files(book), documents(book.store))

    def recording_elsewhere(dry: PortfolioLedger) -> PaperPortfolio:
        portfolio = make_portfolio(DryRunLedger())
        portfolio.ledger = dry
        return portfolio

    with pytest.raises(ValueError, match="did not receive the settlement's transitions"):
        tonight(book, factory=recording_elsewhere)
    assert (ledger_files(book), documents(book.store)) == before


class ReachingOutward:
    """The paper ledger, opening a socket as it is read or appended to: a broker client handed
    in as a ledger. A local (`AF_UNIX`) socket, which the test suite's own offline guard leaves
    alone, so only the paper book's refusal can stop it."""

    def __init__(self, inner: SQLitePortfolioLedger, *, when: str) -> None:
        self.inner, self.when, self.reached = inner, when, 0

    def _reach(self, what: str) -> None:
        if what == self.when:
            self.reached += 1
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close()

    def append(self, transition: PortfolioTransition) -> None:
        self._reach("append")
        self.inner.append(transition)

    def list(self) -> tuple[PortfolioTransition, ...]:
        self._reach("list")
        return self.inner.list()


@pytest.mark.parametrize("when", ["list", "append"])
def test_the_real_ledger_is_read_and_appended_to_only_inside_the_refusal_of_outward_calls(
    tmp_path: Path, when: str
) -> None:
    """The evening touches the real ledger -- the one object here a caller hands in that could
    be anything -- only inside `refusing_outward_calls()`, as `PaperPortfolio.advance` does: a
    ledger that opens a socket as it is read or appended to is refused at once, by the paper
    book's own error, and nothing is written."""
    book = through_s2(tmp_path)
    reaching = ReachingOutward(book.ledger, when=when)
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(PaperPortfolioReachedOutward, match=r"socket\.__new__"):
        tonight(book, ledger=reaching)

    assert reaching.reached == 1
    assert (ledger_files(book), documents(book.store)) == before


def test_a_store_holding_two_snapshots_for_a_session_refuses_the_evening_as_the_stores_error(
    tmp_path: Path,
) -> None:
    """What a caller catches besides `PaperStateError`: the store's own fault, here two documents
    for S2, arrives as `PaperStateStoreError` (a class `backtest/` cannot import), before the
    ledger is touched."""
    book = through_s2(tmp_path)
    (document,) = book.store.path.glob("2021-03-03.pbs_*.json")
    held = load_snapshot(book.store, S2)
    other = held.model_copy(update={"book": held.book.model_copy(update={"cash": D("1.00")})})
    document.with_name(f"2021-03-03.{other.snapshot_id}.json").write_text(
        snapshot_payload(other), encoding="utf-8"
    )
    before = ledger_files(book)

    with pytest.raises(PaperStateStoreError, match="2021-03-03 is held under 2 snapshots"):
        tonight(book)
    assert ledger_files(book) == before


# ---- the ledgers' behaviour --------------------------------------------------------------------


def _row(order_id: str, cash: str) -> PortfolioTransition:
    state = PortfolioState(as_of=S1, cash=D(cash))
    return PortfolioTransition(
        status="rejected",
        order=PortfolioOrder(order_id=order_id, subject=A, side="buy", quantity=100),
        before=state,
        after=state,
        reason="insufficient cash",
    )


def test_both_ledgers_take_the_same_row_again_as_a_no_op_and_refuse_another_under_its_id(
    tmp_path: Path,
) -> None:
    """Measured, and what the crash-safety rests on: `SQLitePortfolioLedger.append` compares the
    stored payload with the new one, writes nothing when they are equal and refuses a different
    payload under the same order id. The dry run's in-memory ledger does exactly the same."""
    same, other = _row("tor_x@2021-03-02", "1000.00"), _row("tor_x@2021-03-02", "1000.01")
    real, dry = SQLitePortfolioLedger(tmp_path / "ledger.sqlite3"), DryRunLedger()

    for ledger in (real, dry):
        ledger.append(same)
        ledger.append(same)
        with pytest.raises(ValueError, match=r"portfolio order_id conflicts: tor_x@2021-03-02"):
            ledger.append(other)

    assert real.list() == (same,)
    assert dry.transitions == (same,)


# ---- a missed evening, caught up ---------------------------------------------------------------


SIXTEEN = [f"6001{n:02d}.SH" for n in range(16)]
SCREEN_SIXTEEN = screened(*((name, "10.00") for name in SIXTEEN))


def test_a_catch_up_from_the_opening_leaves_the_next_evening_the_initial_build(
    tmp_path: Path,
) -> None:
    """S1's evening never ran. Caught up, S1 is settled without a decision: the book is moved to
    the session and nothing is planned, so the book has still decided nothing -- and the evening
    of S2 is the initial build, ten entries, not the two a day of an initialised book."""
    book = book_at(tmp_path, cash="350000.00")

    caught = catch_up(book, S1, NOTHING)

    assert caught.outcome == "created"
    assert caught.snapshot == PaperBookSnapshot(
        config_digest=DIGEST,
        session_index=1,
        session=S1,
        book=PortfolioState(as_of=S1, cash=D("350000.00")),
        last_settled_session=1,
        first_decision_session=None,
    )
    assert load_snapshot(book.store, S1) == caught.snapshot
    result = evening(book, S2, NOTHING, SCREEN_SIXTEEN, dict.fromkeys(SIXTEEN, "10.00"))
    assert result.decision is not None and result.trade_list is not None
    assert [entry.reason for entry in result.decision.entries] == ["INITIAL_BUILD"] * 10
    assert [(o.signal_session, o.quantity) for o in result.trade_list.orders] == [(2, 3400)] * 10
    assert result.snapshot.first_decision_session == 2


# S1's evening plans 600030.SH 900 at 10.00 and 600050.SH 400 at 20.00 (the equal share 9,950.00
# of 100,000.00; both entrants are drawn), each due on S2 and live through S4. The evenings of S2,
# S3 and S4 do not run. 600030.SH trades quietly at 10.00; 600050.SH opens locked limit-up on all
# three sessions.
SCREEN_C_E = screened((C, "10.00"), (E, "20.00"))
MISSED = {
    S2: market(quiet(C, S2, "10.00"), limit_up(E, S2, "20.00")),
    S3: market(quiet(C, S3, "10.00"), limit_up(E, S3, "22.00")),
    S4: market(quiet(C, S4, "10.00"), limit_up(E, S4, "24.20")),
}


def test_three_missed_sessions_caught_up_in_order_walk_the_orders_through_without_a_screen(
    tmp_path: Path,
) -> None:
    """Nobody screened S2, S3 or S4, so nothing is cancelled for want of a survivor: the
    catch-up takes the book's pending buys as the session's survivors. The orders still live
    their lives -- 600030.SH's buy fills on S2 (9,000.00 + 5.00 + 0.09 = 9,005.09, cash
    90,994.91); 600050.SH's is refused on S2 and S3, tried once and twice, and on S4, its last
    session, it expires and cools down from 4. No session plans anything, and the book's first
    decision stays S1's. The evening of S5 then decides an initialised book: 600050.SH cooling,
    600030.SH held, so of the pool (the top 4 - floor(4 x 0.25) = 3) only 600060.SH is free --
    one replacement entry, sized from 90,994.91 + 9,000.00 = 99,994.91: 9,949.49 buys 900."""
    book = book_at(tmp_path)
    first = evening(book, S1, NOTHING, SCREEN_C_E, {C: "10.00", E: "20.00"})
    assert first.trade_list is not None
    buy_c, buy_e = due_order(book, "buy", C), due_order(book, "buy", E)
    assert {(o.ts_code, o.quantity) for o in first.trade_list.orders} == {(C, 900), (E, 400)}

    assert missing_sessions(book.store, config_digest=DIGEST, through_index=4) == (2, 3, 4)
    caught = [catch_up(book, day, MISSED[day]) for day in (S2, S3, S4)]

    assert [result.outcome for result in caught] == ["created"] * 3
    assert [
        [(p.order.ts_code, p.attempts) for p in result.snapshot.pending_orders] for result in caught
    ] == [[(E, 1)], [(E, 2)], []]
    s2, s3, s4 = (result.settlement for result in caught)
    assert s2 is not None and s3 is not None and s4 is not None
    assert (s2.filled, s2.cancelled, s3.cancelled, s4.expired) == ((buy_c,), (), (), (buy_e,))
    assert caught[-1].snapshot.cooldowns == (Cooldown(ts_code=E, since_session=4),)
    assert [result.snapshot.first_decision_session for result in caught] == [1, 1, 1]
    assert caught[-1].snapshot.book.cash == D("90994.91")
    assert outcomes(book.ledger) == [
        (attempt_id(buy_c, S2), "filled", None),
        (attempt_id(buy_e, S2), "rejected", "buy cannot fill on a one-price limit-up bar"),
        (attempt_id(buy_e, S3), "rejected", "buy cannot fill on a one-price limit-up bar"),
        (attempt_id(buy_e, S4), "rejected", "buy cannot fill on a one-price limit-up bar"),
    ]
    assert missing_sessions(book.store, config_digest=DIGEST, through_index=4) == ()

    tonight_s5 = evening(
        book,
        S5,
        market(quiet(C, S5, "10.00")),
        screened((C, "10.00"), (E, "26.62"), (F, "10.00"), (G, "15.00")),
        {E: "26.62", F: "10.00", G: "15.00"},
    )
    assert tonight_s5.decision is not None and tonight_s5.trade_list is not None
    assert [(e.ts_code, e.reason) for e in tonight_s5.decision.entries] == [
        (F, "ENTRY_REPLACEMENT")
    ]
    assert [(o.ts_code, o.quantity) for o in tonight_s5.trade_list.orders] == [(F, 900)]


def test_a_catch_up_out_of_order_is_refused_naming_the_missing_session(tmp_path: Path) -> None:
    book = book_at(tmp_path)
    evening(book, S1, NOTHING, SCREEN_C_E, {C: "10.00", E: "20.00"})
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(
        PaperStateError,
        match=r"the snapshot for session index 2 is missing: the latest is 1 \(2021-03-02\)",
    ):
        catch_up(book, S3, MISSED[S3])
    assert (ledger_files(book), documents(book.store)) == before


def test_an_evening_that_stopped_before_its_ledger_rows_is_mended_by_a_catch_up(
    tmp_path: Path,
) -> None:
    """S3's evening stops in its decision (a drawn entrant has no price), so nothing of it is
    written. The book is stuck -- S4's evening is refused, naming S3 -- until S3 is caught up.
    The catch-up appends exactly what the evening would have (its sale, buy, refused buy and
    write-off: settling does not depend on the decision) and plans nothing; S4's evening then
    runs. There 600050.SH's live buy fills at 22.00 -- 8,800.00 + 5.00 + 0.09 = 8,805.09, cash
    72,770.05 - 8,805.09 = 63,964.96 -- and the kernel draws 600060.SH and 600070.SH."""
    clean = through_s2(tmp_path / "clean")
    tonight(clean)
    book = through_s2(tmp_path / "stopped")
    with pytest.raises(ValueError, match=r"no reference price for 600070\.SH"):
        tonight(book, prices={E: "22.00", F: "10.00", H: "8.00"})
    market_s4 = market(quiet(B, S4, "11.50"), quiet(C, S4, "10.10"), quiet(E, S4, "22.00"))
    prices_s4 = {F: "10.00", G: "15.00", H: "8.00"}
    with pytest.raises(PaperStateError, match=r"the snapshot for session index 3 is missing"):
        evening(book, S4, market_s4, SCREEN_S3, prices_s4)

    mended = catch_up(book, S3, MARKET_S3, delisted={DL})

    assert rows(book.ledger) == rows(clean.ledger)
    assert mended.settlement is not None
    assert [item.ts_code for item in mended.settlement.written_off] == [DL]
    assert [(p.order.ts_code, p.attempts) for p in mended.snapshot.pending_orders] == [(E, 1)]
    assert mended.snapshot.book == load_snapshot(clean.store, S3).book
    s4 = evening(book, S4, market_s4, SCREEN_S3, prices_s4)
    assert s4.outcome == "created" and s4.decision is not None and s4.settlement is not None
    assert s4.settlement.book.cash == D("63964.96")
    assert sorted(entry.ts_code for entry in s4.decision.entries) == [F, G]


def test_a_settled_session_is_caught_up_again_as_a_no_op_and_never_settled_the_other_way(
    tmp_path: Path,
) -> None:
    """A catch-up under another configuration is refused by name before anything is written,
    as an evening is. A session is settled once: a catch-up run again answers `unchanged`,
    writing nothing, with its settlement derived again. A session settled by its evening (which
    planned 600060.SH and 600070.SH) is not caught up -- the catch-up derives a book without
    tonight's plan -- and a session caught up is not given an evening that would plan
    something (600080.SH, drawn first here): each is refused by name, writing nothing."""
    book = through_s2(tmp_path)
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(PaperStateError, match=rf"and this settlement runs under {OTHER_DIGEST}"):
        catch_up(book, S3, MARKET_S3, delisted={DL}, config=OTHER_CONFIG)
    assert (ledger_files(book), documents(book.store)) == before

    tonight(book)
    settled = (ledger_files(book), documents(book.store))
    with pytest.raises(
        PaperStateError, match=r"this catch-up derives .* or the session was settled by an evening"
    ):
        catch_up(book, S3, MARKET_S3, delisted={DL})
    assert (ledger_files(book), documents(book.store)) == settled

    quiet_s4 = market(quiet(B, S4, "11.50"), quiet(C, S4, "10.10"))
    mended = catch_up(book, S4, quiet_s4)
    caught = (ledger_files(book), documents(book.store))
    watched = Watched(book)
    rerun = run_catch_up(
        store=watched,
        ledger=watched,
        make_portfolio=make_portfolio,
        config=CONFIG,
        session=S4,
        session_index=4,
        market=quiet_s4,
        delisted=(),
        observed_on=date(2021, 3, 11),
    )
    assert (rerun.outcome, rerun.snapshot, rerun.settlement) == (
        "unchanged",
        mended.snapshot,
        read_on(mended.settlement, date(2021, 3, 11)),
    )
    assert (watched.puts, watched.appends) == (0, 0)
    h_first = screened(
        (H, "8.00"), (F, "10.00"), (G, "15.00"), (B, "11.50"), (C, "10.10"), (E, "22.00")
    )
    with pytest.raises(
        PaperStateError, match=r"this evening derives .* or the session was settled by a catch-up"
    ):
        evening(book, S4, quiet_s4, h_first, PRICES_S3)
    assert (ledger_files(book), documents(book.store)) == caught


def test_missing_sessions_are_the_ordinals_after_the_head_through_the_one_asked_about(
    tmp_path: Path,
) -> None:
    """The history grows only at its head, so every session after it up to the one asked about
    is missing, and none before it is. A store never opened is refused by name -- there is no
    book to be missing sessions of -- and so is a book of another configuration."""
    never = FilePaperStateStore(tmp_path / "never")
    with pytest.raises(PaperStateError, match="no paper book snapshot is held: the book was never"):
        missing_sessions(never, config_digest=DIGEST, through_index=3)

    book = book_at(tmp_path / "book")
    assert missing_sessions(book.store, config_digest=DIGEST, through_index=0) == ()
    assert missing_sessions(book.store, config_digest=DIGEST, through_index=3) == (1, 2, 3)
    evening(book, S1, NOTHING, SCREEN_S1, PRICES_S1)
    evening(book, S2, MARKET_S2, SCREEN_S2, PRICES_S2)
    assert missing_sessions(book.store, config_digest=DIGEST, through_index=5) == (3, 4, 5)
    assert missing_sessions(book.store, config_digest=DIGEST, through_index=2) == ()
    assert missing_sessions(book.store, config_digest=DIGEST, through_index=1) == ()
    with pytest.raises(
        PaperStateError,
        match=rf"settled under configuration digest {DIGEST} through 2021-03-03, and the "
        rf"sessions asked about are of {OTHER_DIGEST}",
    ):
        missing_sessions(book.store, config_digest=OTHER_DIGEST, through_index=5)


# ---- many evenings: the ledger replays to every snapshot -----------------------------------------

LONG = tuple(date(2021, 4, 1) + timedelta(days=n) for n in range(26))
"""The opening (ordinal 0) and 25 evenings after it: the book needs ascending dates, not
weekdays."""


def run_book(
    root: Path,
    config: SurvivorBookConfig,
    inputs: Callable[
        [int, PaperBookSnapshot, TradeList | None],
        tuple[Sequence[SecurityFacts], Market, Mapping[str, Decimal], Collection[str]],
    ],
) -> tuple[Book, list[EveningResult]]:
    """Open a book with `config.capital` on `LONG[0]` and run the 25 evenings after it, each on
    `inputs(ordinal, the book's head, the last evening's trade list)` -> (facts, market, entrant
    prices, delisted), screened by the real `screen`."""
    store = FilePaperStateStore(root / "snapshots")
    open_book(
        store=store,
        session=LONG[0],
        session_index=0,
        cash=config.capital,
        config_digest=survivor_config_digest(config),
    )
    book = Book(root=root, store=store, ledger=SQLitePortfolioLedger(root / "ledger.sqlite3"))
    results: list[EveningResult] = []
    for n in range(1, len(LONG)):
        head = load_snapshot(store, store.sessions()[-1])
        facts, quotes, prices, delisted = inputs(
            n, head, results[-1].trade_list if results else None
        )
        results.append(
            evening(
                book,
                LONG[n],
                quotes,
                run_screen(facts, config),
                prices,
                delisted=delisted,
                config=config,
                index=n,
            )
        )
    return book, results


def assert_the_ledger_replays_to_every_snapshot(
    book: Book, opening_cash: Decimal, results: Sequence[EveningResult]
) -> None:
    """Replay the ledger from the opening book with nothing but its rows' own figures -- a
    filled buy takes its notional and costs from cash and opens a lot at that cost, a filled
    sell (always of the whole holding) returns its notional less costs and realises the
    difference from the lot's cost, a write-off removes the holding and realises its cost as a
    loss, a refusal changes nothing -- and land, at the close of every session, on that session's
    snapshot as stored: cash, every lot (open date, shares, cost), realised profit and fees. Each
    snapshot is read back, so its checks hold on every one; and no planned order fills twice."""
    sessions = book.store.sessions()
    assert len(sessions) == len(results) + 1
    rows = book.ledger.list()
    assert len({row.order.order_id for row in rows}) == len(rows)
    by_session: dict[date, list[PortfolioTransition]] = defaultdict(list)
    for row in rows:
        by_session[row.after.as_of].append(row)
    cash, realized, fees = opening_cash, D("0.00"), D("0.00")
    lots: dict[str, tuple[date, int, Decimal]] = {}
    for day in sessions:
        snapshot = load_snapshot(book.store, day)
        for row in by_session.pop(day, []):
            code = row.order.subject
            if row.reason == WRITE_OFF_REASON:
                assert (row.status, row.execution) == ("filled", None)
                realized -= lots.pop(code)[2]
            elif row.status == "filled":
                execution = row.execution
                assert execution is not None
                fees += execution.total_cost
                if row.order.side == "buy":
                    cash -= execution.notional + execution.total_cost
                    lots[code] = (
                        day,
                        row.order.quantity,
                        execution.notional + execution.total_cost,
                    )
                else:
                    _, shares, basis = lots.pop(code)
                    assert row.order.quantity == shares
                    cash += execution.notional - execution.total_cost
                    realized += execution.notional - execution.total_cost - basis
        assert snapshot.book.cash == cash, day
        assert snapshot.book.realized_pnl == realized, day
        assert snapshot.book.fees_paid == fees, day
        assert {
            position.subject: (lot.open_date, lot.quantity, lot.cost_basis)
            for position in snapshot.book.positions
            for lot in position.lots
        } == lots, day
    assert not by_session  # every row belongs to a settled session
    planned = {
        attempt.attempt_id: attempt.order.order_id
        for result in results
        for attempt in result.settlement.attempts
    }
    fills = Counter(
        planned[row.order.order_id]
        for row in rows
        if row.status == "filled" and row.reason != WRITE_OFF_REASON
    )
    assert fills and max(fills.values()) == 1


def _plan_one() -> ModuleType:
    """Plan 1's simulation, `tests/unit/domain/test_survivor_book_lifecycle.py`, loaded from its
    file (as that file's own subprocess test loads it), with its ordinals counted from 1.

    The paper book's ordinal 0 is its opening and its first decision is ordinal 1; plan 1's first
    decision is ordinal 0. The selection rule's draw is salted with the ordinal, so the two
    decide the same facts differently unless they count alike: every ordinal plan 1 hands the
    kernel -- the state's session, the session orders are settled on and cool-downs start on --
    is moved on by one. Nothing else of the simulation is touched.
    """
    path = Path(__file__).resolve().parents[1] / "domain" / "test_survivor_book_lifecycle.py"
    spec = importlib.util.spec_from_file_location("plan_one_simulation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state, advance, cool = module.BookState, module.advance_orders, module.update_cooldowns
    module.BookState = lambda **kw: state(**{**kw, "session": kw["session"] + 1})
    module.advance_orders = lambda orders, **kw: advance(
        orders, **{**kw, "session": kw["session"] + 1}
    )
    cooled: list[tuple[Cooldown, ...]] = []

    def cooling(cooldowns: Any, **kw: Any) -> tuple[Cooldown, ...]:
        started = cool(cooldowns, **{**kw, "session": kw["session"] + 1})
        cooled.append(started)
        return started

    module.update_cooldowns = cooling
    module.COOLED = cooled
    return module


def _band(previous: Decimal) -> tuple[Decimal, Decimal]:
    return (
        (previous * D("1.1")).quantize(CENT, rounding=ROUND_HALF_UP),
        (previous * D("0.9")).quantize(CENT, rounding=ROUND_HALF_UP),
    )


@pytest.mark.parametrize("market_seed", range(6))
def test_over_25_evenings_the_paper_book_holds_and_cools_down_what_plan_ones_simulation_does(
    tmp_path: Path, market_seed: int
) -> None:
    """The real kernel, end to end, against plan 1's simulation of it, on plan 1's own markets.

    Each evening n is fed plan 1's facts of its session n - 1; the session's bars open at the
    previous close -- plan 1 fills an order at the reference price it was planned at, the
    previous close -- and a name suspended in the facts has no bar, which plan 1 reads as
    "cannot fill" and the paper book as "not attempted" (plan 1's halts are kept: every one of
    these markets freezes a halted holding). With no limit move, no adjustment event and the
    cash to spare, the two books then hold the same names and the same cool-downs on every one
    of the 25 sessions, though the paper book pays costs and plan 1 does not.

    The equality is not general, and is not claimed to be: the paper book's equity is lower by
    its costs, so a buy is now and then a lot smaller and the cash it leaves differs, and once a
    vacancy is decided on cash that tight the two books part -- measured on plan 1's market 1 at
    ordinal 85, where plan 1 holds 2,981.00 and leaves 600029.SH unfunded and the paper book holds
    6,294.62 and buys 100. What holds on every market and every session is the replay invariant,
    asserted here too and on a harsher market below."""
    simulation = _plan_one()
    config = simulation.config()
    log = simulation.run(market_seed, config)
    days = simulation.walk(market_seed)

    def inputs(
        n: int, head: PaperBookSnapshot, _: TradeList | None
    ) -> tuple[Sequence[SecurityFacts], Market, Mapping[str, Decimal], Collection[str]]:
        facts = days[n - 1]
        previous = {fact.ts_code: fact.close for fact in days[max(n - 2, 0)]}
        bars = {}
        for fact in facts:
            if fact.suspended:
                continue
            opened = previous[fact.ts_code]
            up, down = _band(opened)
            close = min(max(fact.close, down), up)
            bars[fact.ts_code] = MarketBar(
                subject=fact.ts_code,
                trade_date=LONG[n],
                board=security_board(fact.ts_code),
                previous_close=opened,
                open=opened,
                high=max(opened, close),
                low=min(opened, close),
                close=close,
                suspended=False,
                is_st=False,
                up_limit=up,
                down_limit=down,
            )
        held = {position.subject for position in head.book.positions}
        return (
            facts,
            Market(bars=bars, adj_factor={fact.ts_code: D("1.000") for fact in facts}),
            {fact.ts_code: fact.close for fact in facts if fact.ts_code not in held},
            (),
        )

    book, results = run_book(tmp_path, config, inputs)

    for n, result in enumerate(results, start=1):
        assert sorted(p.subject for p in result.snapshot.book.positions) == log[n - 1]["held"], n
        assert result.snapshot.cooldowns == simulation.COOLED[n - 1], n
    assert_the_ledger_replays_to_every_snapshot(book, config.capital, results)
    assert any(result.decision.frozen for result in results)  # halts are in it, and held alike


ROUGH_CODES = tuple(f"{600200 + n:06d}.SH" for n in range(40))


class RoughMarket:
    """Forty main-board names over 25 sessions -- AR(1) scores, 2% daily price noise -- and the
    events a paper book has to come through, each placed on the book as it stands that evening
    (`events` names the code each landed on):

    - session 2, the initial build's fill: every name opens 7% above its last close, so the
      build, sized at that close, runs out of cash and its last buys are refused for it and
      carried (to fill, or expire, as the cash allows);
    - the first buy planned on evening 5 or later: its name opens locked limit-up for three
      sessions -- refused three times, expired, cooling down;
    - the first sell planned on evening 7 or later: its name opens locked limit-down -- refused,
      carried, sold the session after;
    - session 12: the first free holding goes ex-rights 1 for 1 -- its factor doubles and its
      raw prices halve, and the book carries it in the ledger's units;
    - sessions 15 and 16: the next free holding is halted -- no bar, suspended in the facts;
    - session 18: the next free holding is delisted -- no bar and no facts from then on, written
      off that evening.
    """

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.score = {code: self.rng.gauss(0, 1) for code in ROUGH_CODES}
        self.close = {code: D(f"{self.rng.uniform(5, 40):.2f}") for code in ROUGH_CODES}
        self.amount = {code: D(f"{self.rng.uniform(1, 100):.2f}") for code in ROUGH_CODES}
        self.factor = dict.fromkeys(ROUGH_CODES, D("1.000"))
        self.locked_up: dict[str, int] = {}
        self.locked_down: set[str] = set()
        self.halted: dict[str, int] = {}
        self.gone: set[str] = set()
        self.events: dict[str, str] = {}

    def _place(self, n: int, head: PaperBookSnapshot, planned: TradeList | None) -> str | None:
        """Place tonight's events on the book as it stands; the ex-rights code, if tonight's."""
        if planned is not None and n >= 6 and "locked up" not in self.events:
            buys = [order.ts_code for order in planned.orders if order.side == "buy"]
            if buys:
                self.locked_up[buys[0]] = 3
                self.events["locked up"] = buys[0]
        if planned is not None and n >= 8 and "locked down" not in self.events:
            sells = [order.ts_code for order in planned.orders if order.side == "sell"]
            if sells:
                self.locked_down.add(sells[0])
                self.events["locked down"] = sells[0]
        selling = {item.order.ts_code for item in head.pending_orders if item.order.side == "sell"}
        placed = set(self.events.values())
        free = sorted(p.subject for p in head.book.positions if p.subject not in placed | selling)
        if not free or n not in (12, 15, 18):
            return None
        if n == 15:
            self.halted[free[0]] = 2
            self.events["halted"] = free[0]
        elif n == 18:
            self.gone.add(free[0])
            self.events["delisted"] = free[0]
        else:
            self.factor[free[0]] *= 2
            self.events["ex-rights"] = free[0]
            return free[0]
        return None

    def __call__(
        self, n: int, head: PaperBookSnapshot, planned: TradeList | None
    ) -> tuple[Sequence[SecurityFacts], Market, Mapping[str, Decimal], Collection[str]]:
        ex_rights = self._place(n, head, planned)
        facts: list[SecurityFacts] = []
        bars: dict[str, MarketBar] = {}
        for code in ROUGH_CODES:
            if code in self.gone:
                continue
            self.score[code] = 0.9 * self.score[code] + self.rng.gauss(0, 0.5)
            noise = (D(f"{self.rng.gauss(0, 0.005):.6f}"), D(f"{self.rng.gauss(0, 0.02):.6f}"))
            halted = self.halted.get(code, 0) > 0
            if halted:
                self.halted[code] -= 1
            else:
                bars[code] = self._bar(code, n, ex_rights == code, noise)
            facts.append(
                SecurityFacts(
                    ts_code=code,
                    board="main",
                    listed_sessions=400,
                    special_treatment=False,
                    suspended=halted,
                    close=self.close[code],
                    avg_amount=self.amount[code],
                    oriented_values=(self.score[code],) * 4,
                )
            )
        held = {position.subject for position in head.book.positions}
        delisted = {code for code in held if code in self.gone}
        return (
            facts,
            Market(
                bars=bars,
                adj_factor={
                    code: self.factor[code] for code in ROUGH_CODES if code not in self.gone
                },
            ),
            {fact.ts_code: fact.close for fact in facts if fact.ts_code not in held},
            delisted,
        )

    def _bar(self, code: str, n: int, ex_rights: bool, noise: tuple[Decimal, Decimal]) -> MarketBar:
        previous = self.close[code]
        if ex_rights:
            previous = (previous / 2).quantize(CENT, rounding=ROUND_HALF_UP)
        up, down = _band(previous)
        if self.locked_up.get(code, 0) > 0:
            self.locked_up[code] -= 1
            opened = high = low = close = up
        elif code in self.locked_down:
            self.locked_down.discard(code)
            opened = high = low = close = down
        else:
            gap = D("0.07") if n == 2 else noise[0]
            opened = min(
                max((previous * (1 + gap)).quantize(CENT, rounding=ROUND_HALF_UP), down + CENT),
                up - CENT,
            )
            close = min(
                max((previous * (1 + noise[1])).quantize(CENT, rounding=ROUND_HALF_UP), down), up
            )
            high, low = max(opened, close), min(opened, close)
        self.close[code] = close
        return MarketBar(
            subject=code,
            trade_date=LONG[n],
            board="main",
            previous_close=previous,
            open=opened,
            high=high,
            low=low,
            close=close,
            suspended=False,
            is_st=False,
            up_limit=up,
            down_limit=down,
        )


ROUGH_CONFIG = SurvivorBookConfig(seed=11)


def test_over_25_rough_evenings_the_ledger_replays_to_every_snapshot(tmp_path: Path) -> None:
    """The replay invariant on a market that makes the book do everything it can: refusals for
    cash, at a limit-up and at a limit-down, an expiry into a cool-down, an ex-rights holding
    carried in the ledger's units, a halted holding left alone, and a delisting written off.
    Each event is asserted to have happened, so the invariant is not checked on a quiet book."""
    rough = RoughMarket(seed=5)

    book, results = run_book(tmp_path, ROUGH_CONFIG, rough)

    assert_the_ledger_replays_to_every_snapshot(book, ROUGH_CONFIG.capital, results)
    assert set(rough.events) == {
        "locked up",
        "locked down",
        "ex-rights",
        "halted",
        "delisted",
    }, rough.events
    refused = Counter(
        (row.order.subject, row.reason) for row in book.ledger.list() if row.status == "rejected"
    )
    assert any(reason == "insufficient cash" for _, reason in refused)
    assert refused[(rough.events["locked up"], "buy cannot fill on a one-price limit-up bar")] == 3
    assert (
        refused[(rough.events["locked down"], "sell cannot fill on a one-price limit-down bar")]
        == 1
    )
    expired = {order.ts_code for result in results for order in result.settlement.expired}
    assert rough.events["locked up"] in expired
    sold = {
        row.order.subject
        for row in book.ledger.list()
        if row.status == "filled" and row.order.side == "sell" and row.reason is None
    }
    assert rough.events["locked down"] in sold
    s12 = results[11].snapshot
    ex_rights = rough.events["ex-rights"]
    assert s12.entry_factors_by_code()[ex_rights] == D("1.000")
    assert rough.factor[ex_rights] == D("2.000")
    assert any(rough.events["halted"] in result.decision.frozen for result in results[14:16])
    (written,) = results[17].settlement.written_off
    assert written.ts_code == rough.events["delisted"]
