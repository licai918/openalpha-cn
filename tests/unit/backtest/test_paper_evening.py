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

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from openalpha_cn.backtest.paper_evening import open_book
from openalpha_cn.backtest.paper_state import (
    PaperBookSnapshot,
    PaperStateError,
    load_snapshot,
    snapshot_payload,
)
from openalpha_cn.domain.portfolio import PortfolioState
from openalpha_cn.domain.survivor_book import SurvivorBookConfig, survivor_config_digest
from openalpha_cn.storage.paper_state import FilePaperStateStore

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


# ---- fixtures ----------------------------------------------------------------------------------


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
