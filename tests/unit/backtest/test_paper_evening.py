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

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import pytest

from openalpha_cn.backtest.execution import MarketBar, security_board
from openalpha_cn.backtest.multi_day import PortfolioLedger
from openalpha_cn.backtest.paper import PaperPortfolio
from openalpha_cn.backtest.paper_evening import (
    CatchUpResult,
    DryRunLedger,
    EveningResult,
    missing_sessions,
    open_book,
    run_catch_up,
    run_evening,
)
from openalpha_cn.backtest.paper_settlement import WRITE_OFF_REASON, write_off_id
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
    SurvivorBookConfig,
    survivor_config_digest,
)
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown
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
    prices: Mapping[str, str],
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


class CutAtRow:
    """The paper ledger, with the power cut as the evening's `at`-th row is appended."""

    def __init__(self, inner: SQLitePortfolioLedger, at: int) -> None:
        self.inner, self.at, self.appended = inner, at, 0

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


def test_opening_the_same_book_again_writes_nothing(tmp_path: Path) -> None:
    store = opened(tmp_path)
    before = documents(store)

    again = open_book(
        store=store, session=E0, session_index=0, cash=D("100000.00"), config_digest=DIGEST
    )

    assert again == load_snapshot(store, E0)
    assert documents(store) == before


@pytest.mark.parametrize(
    ("session", "cash", "digest", "refusal"),
    [
        (E0, "100000.01", DIGEST, r"2021-03-01 is already settled as pbs_"),
        (E0, "100000.00", OTHER_DIGEST, r"2021-03-01 is already settled as pbs_"),
        (
            S1,
            "100000.00",
            DIGEST,
            r"2021-03-02 is an opening snapshot and this store already holds the book's "
            r"history through 2021-03-01 \(session index 0\)",
        ),
        (
            S1,
            "100000.00",
            OTHER_DIGEST,
            r"2021-03-02 is an opening snapshot and this store already holds",
        ),
    ],
    ids=["other cash", "other configuration", "a later opening", "a second book"],
)
def test_an_opening_never_replaces_or_restarts_a_book_already_held(
    tmp_path: Path, session: date, cash: str, digest: str, refusal: str
) -> None:
    """One paper book per runtime directory, opened once: another opening on the same day, or a
    restart on a later one, is refused by name and the store keeps its bytes."""
    store = opened(tmp_path)
    before = documents(store)

    with pytest.raises(PaperStateError, match=refusal):
        open_book(
            store=store,
            session=session,
            session_index=INDEX[session],
            cash=D(cash),
            config_digest=digest,
        )
    assert documents(store) == before


@pytest.mark.parametrize(
    ("cash", "index", "refusal"),
    [
        ("0.00", 0, r"opening cash must be a positive amount: 0\.00"),
        ("-1.00", 0, r"opening cash must be a positive amount: -1\.00"),
        ("NaN", 0, r"opening cash must be a positive amount: NaN"),
        ("100000.00", -1, r"session_index"),
    ],
    ids=["no cash", "negative cash", "not a number", "before the calendar's first ordinal"],
)
def test_an_opening_the_book_could_never_trade_from_is_refused_before_anything_is_written(
    tmp_path: Path, cash: str, index: int, refusal: str
) -> None:
    """A book is opened once per runtime directory, so an opening nothing could be decided from
    -- the kernel sizes from a positive equity -- would be a directory spoiled for good. An
    ordinal below 0 is a date before the epoch the calendar counts from."""
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
    with pytest.raises(
        PaperStateError,
        match=r"2021-03-04 is an opening snapshot and this store already holds the book's "
        r"history through 2021-03-03 \(session index 2\)",
    ):
        open_book(
            store=book.store, session=S3, session_index=3, cash=D("1.00"), config_digest=DIGEST
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


def test_a_rerun_of_a_completed_evening_writes_nothing_and_answers_with_the_held_snapshot(
    tmp_path: Path,
) -> None:
    """Once the snapshot is saved the session is settled: running the evening again -- the next
    morning, or with inputs read again -- touches neither the ledger nor the store and answers
    with what was settled. It has no plan to give: the evening's list is the caller's to keep
    when the evening is first `created`."""
    book = through_s2(tmp_path)
    first = tonight(book)
    before = (ledger_files(book), documents(book.store))

    again = tonight(book, observed_on=S4)
    otherwise = tonight(book, prices={E: "22.00", F: "10.50", G: "15.00", H: "8.00"})

    for rerun in (again, otherwise):
        assert rerun.outcome == "unchanged"
        assert rerun.snapshot == first.snapshot
        assert (rerun.trade_list, rerun.decision, rerun.settlement) == (None, None, None)
    assert (ledger_files(book), documents(book.store)) == before


def test_a_rerun_with_other_inputs_after_a_partial_commit_is_refused_by_the_ledger(
    tmp_path: Path,
) -> None:
    """The power is cut after the sale and the buy are in. Run again on a market read
    differently -- 600010.SH now opening at 10.05 -- the sale is another transition under the
    same attempt id, and the ledger refuses it: nothing more is appended and no snapshot is
    saved. (The daily entry pins the inputs -- the facts' and the market's `as_of`, the delisted
    set -- in its journal, so a run again reads what the first read.)"""
    book = through_s2(tmp_path)
    sell_a = due_order(book, "sell", A)
    with pytest.raises(OSError, match="row 3 of the evening"):
        tonight(book, ledger=CutAtRow(book.ledger, 3))
    partial = rows(book.ledger)
    moved = market(
        bar(A, S3, "10.00", "10.05", "10.20", "9.90", "10.10", "11.00", "9.00"),
        quiet(B, S3, "11.50"),
        bar(C, S3, "10.00", "10.00", "10.20", "9.95", "10.10", "11.00", "9.00"),
        limit_up(E, S3, "20.00"),
    )

    conflict = rf"portfolio order_id conflicts: {attempt_id(sell_a, S3)}"
    with pytest.raises(ValueError, match=conflict):
        tonight(book, quotes=moved)

    assert rows(book.ledger) == partial
    assert book.store.sessions() == (E0, S1, S2)


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


def test_a_catch_up_of_a_settled_session_writes_nothing_and_another_configuration_is_refused(
    tmp_path: Path,
) -> None:
    """A session is settled once, by its evening or by its catch-up: either run again answers
    `unchanged` with the snapshot held and writes nothing. A catch-up under another
    configuration is refused by name before anything is written, as an evening is."""
    book = through_s2(tmp_path)
    before = (ledger_files(book), documents(book.store))

    with pytest.raises(PaperStateError, match=rf"and this settlement runs under {OTHER_DIGEST}"):
        catch_up(book, S3, MARKET_S3, delisted={DL}, config=OTHER_CONFIG)
    assert (ledger_files(book), documents(book.store)) == before

    settled = tonight(book)
    after = (ledger_files(book), documents(book.store))
    again = catch_up(book, S3, MARKET_S3, delisted={DL})
    assert (again.outcome, again.snapshot, again.settlement) == (
        "unchanged",
        settled.snapshot,
        None,
    )
    assert (ledger_files(book), documents(book.store)) == after

    quiet_s4 = market(quiet(B, S4, "11.50"), quiet(C, S4, "10.10"))
    mended = catch_up(book, S4, quiet_s4)
    after = (ledger_files(book), documents(book.store))
    for rerun in (catch_up(book, S4, quiet_s4), evening(book, S4, quiet_s4, SCREEN_S3, PRICES_S3)):
        assert (rerun.outcome, rerun.snapshot) == ("unchanged", mended.snapshot)
    assert (ledger_files(book), documents(book.store)) == after


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
