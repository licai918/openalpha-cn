"""The paper book's record layer of the survivor forward report (`V2-P7-018`, task E3).

`strategy_survivor_paper_report.paper_record` turns three things the caller read -- the paper
book's snapshots, the paper ledger's transitions and the daily journals -- into the descriptive
record the forward report prints beside its one evidence layer: the equity curve, what the book
held, what it traded and paid, what it wrote off, what it could not fill, what it decided, and --
against the benchmarks the caller supplies -- the window chain of the relative return. It is
pure: it reads no store, no file, no clock.

## Where the fixtures come from

Nothing is hand-built that the paper book builds. The snapshots, the ledger rows and the journals'
settlement blocks are what the **real** evening and catch-up machinery of
`tests/unit/backtest/test_paper_evening.py` made in a tmp store and ledger -- its helpers, its
sessions, its screens -- and the journal documents are the real `evening_document` and
`catch_up_entry` of `strategy_survivor_summary` over them (the facts days alone are plain values:
a fact row is all the document reads of one). The story is `test_paper_evening`'s own, six
sessions after the book opens on E0 2021-03-01 (ordinal 0), with the one change that 600020.SH
goes ex-rights on S3:

- **S1** the initial build: 600010.SH, 600020.SH and 600040.SH are drawn; nothing is held, so
  nothing settles.
- **S2** the three buys fill (9,000.00, 9,200.00 and 9,000.00 at the opens; 5.09 each in costs)
  and 600010.SH, no longer a survivor, is sold; 600030.SH and 600050.SH are bought. 600020.SH,
  bought at its 11.50 open, closes at 11.40, so S2 marks its 800 shares at 11.40 -- not the 11.50
  S3 marks them at, so D16's "previous snapshot" is told from the one before it.
- **S3** 600010.SH's sale and 600030.SH's buy fill, 600050.SH's buy opens locked limit-up and is
  refused, **600040.SH is delisted with no bar and written off**, and 600060.SH and 600070.SH are
  bought. 600020.SH goes ex-rights 1 for 1 (its factor doubles, its real close is 5.75), so the
  ledger marks its 800 shares at 5.75 x 2 = 11.50. The runner's trace says 99,970.05 for the
  session and the book ends it at 91,060.05.
- **S4** the evening never ran (its journal holds no `result`) and S5 caught it up: 600060.SH's
  buy fills, 600070.SH has no bar (**not attempted**), 600050.SH is refused again, and
  **600020.SH is delisted, with no bar, and written off** -- after the ex-rights, so the ledger's
  last mark and the real last close are two different prices.
- **S5** 600070.SH's buy fills, 600050.SH is refused a third time and **expires**, and the kernel
  sells 600030.SH, which the screen no longer holds (`EXIT_ELIGIBILITY_LOST`).
- **S6** a settle-only day: 600030.SH is sold and 600080.SH bought, with no decision.

The book's snapshots, per session (cash, equity, fees paid, names held) are asserted below from
the arithmetic of the fixtures' own comments, never read back from the code under test.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn import strategy_survivor_paper_report
from openalpha_cn.backtest.paper_settlement import WRITE_OFF_REASON
from openalpha_cn.backtest.paper_state import PaperBookSnapshot, load_snapshot
from openalpha_cn.backtest.paper_step import NO_BAR_REASON
from openalpha_cn.domain.portfolio import PortfolioTransition
from openalpha_cn.domain.survivor_book import SecurityFacts
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.strategy_survivor_facts import FactsDay, FactsNotes, HeldStatus
from openalpha_cn.strategy_survivor_market import SessionOrdinals
from openalpha_cn.strategy_survivor_paper_report import (
    SESSIONS_PER_YEAR,
    WINDOW_LABEL,
    BenchmarkWindow,
    JournalSnapshotMismatchError,
    PaperRecordError,
    paper_record,
)
from openalpha_cn.strategy_survivor_summary import (
    SURVIVOR_WORDING,
    catch_up_entry,
    evening_document,
)

# The paper book's evening fixtures live in a test file of their own directory, which is on no
# import path unless this file puts it there; the module is the one pytest collects, imported
# under its own name, so there is one copy of every helper and of every constant.
_EVENING_TESTS: Final[Path] = Path(__file__).resolve().parent / "backtest"
if str(_EVENING_TESTS) not in sys.path:
    sys.path.insert(0, str(_EVENING_TESTS))
evenings: Any = importlib.import_module("test_paper_evening")

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
D = Decimal
E0, S1, S2, S3, S4, S5, S6 = (
    evenings.E0,
    evenings.S1,
    evenings.S2,
    evenings.S3,
    evenings.S4,
    evenings.S5,
    evenings.S6,
)
A, B, C, DL, E, F, G, H = (
    evenings.A,
    evenings.B,
    evenings.C,
    evenings.DL,
    evenings.E,
    evenings.F,
    evenings.G,
    evenings.H,
)
BOOK = evenings.CONFIG
JOURNAL_SCHEMA: Final[str] = "openalpha-daily-survivor/v1"
FOREIGN_ID: Final[str] = "pbs_0123456789abcdef01234567"
LIMIT_UP_REFUSAL: Final[str] = "buy cannot fill on a one-price limit-up bar"
LABEL: Final[str] = (
    "the book is net of the protocol's costs and cash; the benchmarks are gross buy-and-hold "
    "over the same closes; the paper book fills at the D+1 open while the labels start at the "
    "D+1 close (a one-leg timing difference)"
)
SKIPPED: Final[str] = "the run began after 09:15 of the next session"
SIX: Final[Decimal] = D("0.000001")
CENT: Final[Decimal] = D("0.01")

# What each settled session ended with: equity, cash, fees paid, names held. S2's, S3's cash and
# equity are the fixture file's own comments; the rest is the same arithmetic carried on:
#   S4  cash 72,770.05 - 9,005.09 (600060.SH, 900 at 10.00) = 63,764.96; held 600030.SH 900 at
#       10.10 = 9,090.00 and 600060.SH 900 at 10.00 = 9,000.00 -> equity 81,854.96
#   S5  cash 63,764.96 - 9,005.09 (600070.SH, 600 at 15.00) = 54,759.87; + 600070.SH 9,000.00
#       -> 81,849.87
#   S6  cash 54,759.87 + 9,090.00 - 9.64 (the sale's costs) - 8,005.08 (600080.SH, 1,000 at 8.00)
#       = 55,835.15; held 600060.SH 9,000.00, 600070.SH 9,000.00, 600080.SH 8,000.00 -> 81,835.15
BOOKED: Final[Mapping[date, tuple[str, str, str, int]]] = {
    S1: ("100000.00", "100000.00", "0.00", 0),
    S2: ("99904.73", "72784.73", "15.27", 3),
    S3: ("91060.05", "72770.05", "29.95", 2),
    S4: ("81854.96", "63764.96", "35.04", 2),
    S5: ("81849.87", "54759.87", "40.13", 3),
    S6: ("81835.15", "55835.15", "54.85", 3),
}
MEAN_EQUITY: Final[Fraction] = sum((Fraction(v[0]) for v in BOOKED.values()), Fraction(0)) / 6
"""(100,000.00 + 99,904.73 + 91,060.05 + 81,854.96 + 81,849.87 + 81,835.15) / 6."""
SESSION_COUNT: Final[int] = 6


def fixed(value: Fraction | Decimal | int, places: int = 6) -> str:
    """`value` to `places` decimals, half away from zero, as a plain string."""
    exact = Fraction(value)
    scaled = abs(exact) * 10**places
    whole = scaled.numerator // scaled.denominator
    if scaled - whole >= Fraction(1, 2):
        whole += 1
    signed = -whole if exact < 0 else whole
    return format(Decimal(signed).scaleb(-places), "f")


# --- facts days ---------------------------------------------------------------------------------

NO_NOTES: Final[FactsNotes] = FactsNotes.tally(
    reasons=(),
    suspended_without_bar=0,
    no_listing_row=0,
    no_bar_within_carry=0,
    not_listed_with_bar=0,
    unrecognised_board_codes=(),
)


def fact(code: str, close: str) -> SecurityFacts:
    return SecurityFacts(
        ts_code=code,
        board="main",
        listed_sessions=900,
        special_treatment=False,
        suspended=False,
        close=D(close),
        avg_amount=D("5000000"),
        oriented_values=(0.1, 0.2, 0.3, 0.4),
    )


def facts_of(
    session: date,
    closes: Mapping[str, str],
    *,
    held: Sequence[str] = (),
    delisted: Sequence[str] = (),
) -> FactsDay:
    """A facts day of one row per code in `closes`, and for each held code a row, or -- for a
    code in `delisted` -- the status that says why it has none."""
    rows = tuple(fact(code, close) for code, close in sorted(closes.items()))
    return FactsDay(
        session=session,
        facts=rows,
        notes=NO_NOTES,
        boards=("main",),
        universe_size=len(rows),
        scored_size=len(rows),
        held=tuple(sorted(held)),
        held_status=tuple(
            HeldStatus(ts_code=code, status="delisted" if code in delisted else "row")
            for code in sorted(held)
        ),
    )


def weekdays(first: date, last: date) -> TradingCalendar:
    """A calendar that is open on every weekday from `first` to `last`, as the evening tests'
    sessions are (E0 is a Monday; S5, 2021-03-08, follows the weekend)."""
    days: list[CalendarDay] = []
    day = first
    while day <= last:
        days.append(CalendarDay(calendar_date=day, is_trading=day.weekday() < 5))
        day += timedelta(days=1)
    return build_trading_calendar("SSE", days)


ORDINALS: Final[SessionOrdinals] = SessionOrdinals(weekdays(E0, S6), E0)


def at(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, 18, 30, tzinfo=SHANGHAI)


# --- the story ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Story:
    """The book as the caller reads it, and the evenings that made it."""

    snapshots: tuple[PaperBookSnapshot, ...]
    transitions: tuple[PortfolioTransition, ...]
    journals: tuple[dict[str, Any], ...]
    results: Mapping[str, Any]

    def snapshot(self, day: date) -> PaperBookSnapshot:
        return next(item for item in self.snapshots if item.session == day)

    def copies(self) -> list[dict[str, Any]]:
        """The journals as a test may edit them."""
        return copy.deepcopy(list(self.journals))


def journal(
    session: date,
    *,
    result: Mapping[str, Any] | None = None,
    catch_ups: Sequence[Mapping[str, Any]] = (),
    stage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A survivor journal as the daily command files it (`openalpha-daily-survivor/v1`), read back
    from its file: JSON, with the keys the day's steps filed in the order they filed them."""
    body: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "session": session.isoformat(),
        "as_of": at(session).isoformat(),
        "held": [],
        "held_from": None,
        "registration": {"config_id": "cfg_example"},
        "panel": {"caught_up": []},
    }
    if result is not None and "opening" not in result:
        body["prediction"] = {"registered": True, "record_id": f"prd_{session.isoformat()}"}
    if catch_ups:
        body["catch_ups"] = list(catch_ups)
    if stage is not None:
        body["paper"] = dict(stage)
    if result is not None:
        body["result"] = dict(result)
    return json.loads(json.dumps(body))


def build_story(root: Path) -> Story:
    """The six sessions of the module docstring, run on a fresh book in `root`."""
    book = evenings.book_at(root)
    nothing = evenings.NOTHING
    r1 = evenings.evening(book, S1, nothing, evenings.SCREEN_S1, evenings.PRICES_S1)
    # `test_paper_evening`'s S2, but for 600020.SH's close (the module docstring): it opens at
    # 11.50, where its buy fills, and closes at 11.40.
    market2 = evenings.market(
        evenings.quiet(A, S2, "10.00"),
        evenings.bar(B, S2, "11.50", "11.50", "11.60", "11.40", "11.40", "12.65", "10.35"),
        evenings.quiet(DL, S2, "10.00"),
    )
    screen2 = evenings.screened(
        (B, "11.40"), (C, "10.00"), (E, "20.00"), (DL, "10.00"), dropped=(A,)
    )
    r2 = evenings.evening(book, S2, market2, screen2, evenings.PRICES_S2)
    ex_rights = evenings.Market(
        bars={
            A: evenings.bar(A, S3, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
            B: evenings.quiet(B, S3, "5.75"),
            C: evenings.bar(C, S3, "10.00", "10.00", "10.20", "9.95", "10.10", "11.00", "9.00"),
            E: evenings.limit_up(E, S3, "20.00"),
        },
        adj_factor={A: D("1.000"), B: D("2.000"), C: D("1.000"), E: D("1.000")},
    )
    screen3 = evenings.screened(
        (F, "10.00"), (G, "15.00"), (B, "5.75"), (C, "10.10"), (E, "22.00"), (H, "8.00")
    )
    r3 = evenings.tonight(book, quotes=ex_rights, screen=screen3)
    c4 = evenings.catch_up(
        book,
        S4,
        evenings.market(
            evenings.quiet(C, S4, "10.10"),
            evenings.quiet(F, S4, "10.00"),
            evenings.limit_up(E, S4, "24.20"),
        ),
        delisted={B},
    )
    screen5 = evenings.screened((F, "10.00"), (G, "15.00"), (H, "8.00"), (E, "26.62"))
    r5 = evenings.evening(
        book,
        S5,
        evenings.market(
            evenings.quiet(C, S5, "10.10"),
            evenings.quiet(F, S5, "10.00"),
            evenings.quiet(G, S5, "15.00"),
            evenings.limit_up(E, S5, "24.20"),
        ),
        screen5,
        {E: "26.62", H: "8.00"},
    )
    c6 = evenings.catch_up(
        book,
        S6,
        evenings.market(
            evenings.quiet(C, S6, "10.10"),
            evenings.quiet(F, S6, "10.00"),
            evenings.quiet(G, S6, "15.00"),
            evenings.quiet(H, S6, "8.00"),
        ),
    )

    def document(day: date, index: int, **changes: Any) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "session": day,
            "session_index": index,
            "as_of": at(day),
            "catch_ups": (),
            "ordinals": ORDINALS,
            "book": BOOK,
        }
        arguments.update(changes)
        return json.loads(json.dumps(evening_document(**arguments)))

    def stage(mode: str, outcome: str | None, snapshot_id: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "read_at": at(S6).isoformat(),
            "outcome": outcome,
            "snapshot_id": snapshot_id,
        }

    snapshots = tuple(load_snapshot(book.store, session) for session in book.store.sessions())
    opening = snapshots[0]
    opened = {
        "kind": "survivor_book/v1",
        "session": E0.isoformat(),
        "session_index": 0,
        "as_of": at(E0).isoformat(),
        "opening": {
            "epoch_session": E0.isoformat(),
            "snapshot_id": opening.snapshot_id,
            "cash": "100000.00",
            "config_digest": opening.config_digest,
        },
        "wording": SURVIVOR_WORDING,
    }
    d1 = document(
        S1,
        1,
        facts=facts_of(S1, {A: "10.00", B: "11.50", DL: "10.00"}),
        screen=evenings.SCREEN_S1,
        evening=r1,
    )
    d2 = document(
        S2,
        2,
        facts=facts_of(
            S2, {A: "10.00", B: "11.40", C: "10.00", E: "20.00", DL: "10.00"}, held=(A, B, DL)
        ),
        screen=screen2,
        evening=r2,
    )
    d3 = document(
        S3,
        3,
        facts=facts_of(
            S3,
            {A: "10.10", B: "5.75", C: "10.10", E: "22.00", F: "10.00", G: "15.00", H: "8.00"},
            held=(A, B, DL),
            delisted=(DL,),
        ),
        screen=screen3,
        evening=r3,
    )
    d5 = document(
        S5,
        5,
        facts=facts_of(
            S5, {C: "10.10", E: "26.62", F: "10.00", G: "15.00", H: "8.00"}, held=(C, F)
        ),
        screen=screen5,
        evening=r5,
        catch_ups=(c4,),
    )
    d6 = document(
        S6,
        6,
        facts=facts_of(S6, {C: "10.10", F: "10.00", G: "15.00", H: "8.00"}, held=(C, F, G)),
        screen=evenings.screened((C, "10.10")),
        evening=None,
        catch_ups=(c6,),
        paper_skipped=SKIPPED,
    )
    refused = journal(S4)
    refused["prediction"] = {"registered": True, "record_id": "prd_2021-03-05"}
    journals = (
        journal(E0, result=opened),
        journal(S1, result=d1, stage=stage("evening", r1.outcome, r1.snapshot.snapshot_id)),
        journal(S2, result=d2, stage=stage("evening", r2.outcome, r2.snapshot.snapshot_id)),
        journal(S3, result=d3, stage=stage("evening", r3.outcome, r3.snapshot.snapshot_id)),
        refused,
        journal(
            S5,
            result=d5,
            catch_ups=[catch_up_entry(c4)],
            stage=stage("evening", r5.outcome, r5.snapshot.snapshot_id),
        ),
        journal(
            S6,
            result=d6,
            catch_ups=[catch_up_entry(c6)],
            stage=stage("settle_only", None, c6.snapshot.snapshot_id),
        ),
    )
    results = {"s1": r1, "s2": r2, "s3": r3, "c4": c4, "s5": r5, "c6": c6, "book": book}
    return Story(snapshots, tuple(book.ledger.list()), journals, results)


@pytest.fixture(scope="module")
def story(tmp_path_factory: pytest.TempPathFactory) -> Story:
    return build_story(tmp_path_factory.mktemp("story"))


def record_of(
    story: Story,
    *,
    snapshots: Sequence[PaperBookSnapshot] | None = None,
    transitions: Sequence[PortfolioTransition] | None = None,
    documents: Sequence[Mapping[str, Any]] | None = None,
    windows: Sequence[BenchmarkWindow] = (),
    holding_count: int = 3,
) -> dict[str, Any]:
    return paper_record(
        story.snapshots if snapshots is None else snapshots,
        story.transitions if transitions is None else transitions,
        story.journals if documents is None else documents,
        windows=windows,
        holding_count=holding_count,
    )


def ledger_of(story: Story, **changes: Any) -> dict[str, Any]:
    return record_of(story, **changes)["paper_ledger"]


def strings(value: Any) -> list[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for key, item in value.items() for text in (str(key), *strings(item))]
    if isinstance(value, list | tuple):
        return [text for item in value for text in strings(item)]
    return []


# --- the fixture is the story the docstring tells -----------------------------------------------


def test_the_fixture_is_the_book_the_docstring_describes(story: Story) -> None:
    assert [item.session for item in story.snapshots] == [E0, S1, S2, S3, S4, S5, S6]
    for day, (equity, cash, fees, held) in BOOKED.items():
        snapshot = story.snapshot(day)
        assert snapshot.book.equity == D(equity), day
        assert snapshot.book.cash == D(cash), day
        assert snapshot.book.fees_paid == D(fees), day
        assert len(snapshot.book.positions) == held, day
    # The runner's trace of S3 is not the book's session: it still holds the name written off.
    assert story.results["s3"].settlement.runner_record.equity == D("99970.05")
    assert story.snapshot(S3).book.equity == D("91060.05")
    # 600020.SH, ex-rights on S3: 800 ledger shares marked at 5.75 x 2.
    assert story.snapshot(S3).book.mark(B) == D("11.50")
    assert [item["session"] for item in story.journals] == [
        day.isoformat() for day in (E0, S1, S2, S3, S4, S5, S6)
    ]
    assert "result" not in story.journals[4]


# --- the equity curve, exposure and N held ------------------------------------------------------


def test_the_equity_curve_is_read_from_the_snapshots_not_from_the_runner_record(
    story: Story,
) -> None:
    """S3's `runner_record` says 99,970.05, the book ended the session at 91,060.05: the curve
    reads the snapshots. The journals carry equities of their own (the settlement's); editing every
    one of them changes nothing, because the curve is not read from them."""
    edited = story.copies()
    for body in edited:
        result = body.get("result") or {}
        paper = result.get("paper")
        if paper is not None:
            paper["book"]["equity"] = "1.00"
        for entry in body.get("catch_ups", ()):
            entry["equity"] = "1.00"

    curve = ledger_of(story, documents=edited)["equity"]["curve"]

    assert [(row["session"], row["equity"]) for row in curve] == [
        (day.isoformat(), BOOKED[day][0]) for day in BOOKED
    ]
    assert curve == ledger_of(story)["equity"]["curve"]


def test_each_settled_session_has_its_cash_share_gross_exposure_and_names_held(
    story: Story,
) -> None:
    """Cash share is cash over equity and gross exposure the held names' market value over equity,
    each to six places: S3 holds 72,770.05 in cash of 91,060.05 and so 18,290.00 (800 x 11.50 +
    900 x 10.10) in names."""
    equity = ledger_of(story)["equity"]

    assert equity["sessions"] == SESSION_COUNT
    assert (equity["first_session"], equity["last_session"]) == ("2021-03-02", "2021-03-09")
    rows = {row["session"]: row for row in equity["curve"]}
    assert list(rows) == [day.isoformat() for day in BOOKED]
    for day, (total, cash, _, held) in BOOKED.items():
        row = rows[day.isoformat()]
        assert row["session_index"] == [S1, S2, S3, S4, S5, S6].index(day) + 1
        assert (row["equity"], row["cash"], row["held"]) == (total, cash, held)
        assert row["cash_share"] == fixed(Fraction(cash) / Fraction(total))
        assert row["gross_exposure"] == fixed((Fraction(total) - Fraction(cash)) / Fraction(total))
    assert rows["2021-03-04"]["gross_exposure"] == fixed(
        Fraction("18290.00") / Fraction("91060.05")
    )
    assert equity["opening"] == {
        "session": "2021-03-01",
        "equity": "100000.00",
        "snapshot_id": story.snapshots[0].snapshot_id,
    }
    assert equity["final_equity"] == "81835.15"
    assert equity["mean_equity"] == fixed(MEAN_EQUITY, 2)
    assert equity["minimum_equity"] == "81835.15"


def test_n_held_has_its_mean_minimum_and_the_sessions_below_n(story: Story) -> None:
    """Held per session: 0, 3, 2, 2, 3, 3 -- mean 13 / 6. With N = 3 the sessions below it are S1
    (nothing yet), S3 and S4; the book first held N on S2, so after that first full session two
    sessions fell below it. With N = 10 every session is below it and the book never held N."""
    holdings = ledger_of(story, holding_count=3)["holdings"]

    assert holdings["holding_count"] == 3
    assert holdings["mean_held"] == fixed(Fraction(13, 6))
    assert (holdings["minimum_held"], holdings["maximum_held"]) == (0, 3)
    assert holdings["sessions_below_n"] == 3
    assert holdings["first_session_at_n"] == "2021-03-03"
    assert holdings["sessions_below_n_after_first_full"] == 2
    assert holdings["mean_cash_share"] == fixed(
        sum(
            (Fraction(cash) / Fraction(total) for total, cash, _, _ in BOOKED.values()), Fraction(0)
        )
        / 6
    )
    assert holdings["mean_gross_exposure"] == fixed(
        sum(
            (1 - Fraction(cash) / Fraction(total) for total, cash, _, _ in BOOKED.values()),
            Fraction(0),
        )
        / 6
    )

    full = ledger_of(story, holding_count=BOOK.holding_count)["holdings"]
    assert full["holding_count"] == 10
    assert full["sessions_below_n"] == SESSION_COUNT
    assert full["first_session_at_n"] is None
    assert full["sessions_below_n_after_first_full"] is None


# --- turnover and costs -------------------------------------------------------------------------


def test_turnover_is_one_sided_filled_notional_over_twice_mean_equity_at_244_sessions(
    story: Story,
) -> None:
    """Filled notionals -- buys 9,000.00 + 9,200.00 + 9,000.00 (S2), 9,000.00 (600030.SH), 9,000.00
    (600060.SH), 9,000.00 (600070.SH), 8,000.00 (600080.SH) = 62,200.00; sells 9,000.00 (600010.SH)
    + 9,090.00 (600030.SH) = 18,090.00; 80,290.00 in all. The two write-offs have no execution and
    add nothing: counting them at their marks would add 9,000.00 and 9,200.00. One-sided turnover
    is 80,290.00 / (2 x 89,417.46 mean equity) over the six sessions. Six sessions are too short
    to annualise (I6: fewer than 60), so nothing is: the rule's own test is below."""
    turnover = ledger_of(story)["turnover"]

    assert SESSIONS_PER_YEAR == 244
    assert turnover["filled_notional"] == {
        "buy": "62200.00",
        "sell": "18090.00",
        "total": "80290.00",
    }
    assert turnover["sessions"] == SESSION_COUNT
    assert turnover["sessions_per_year"] == 244
    assert turnover["mean_equity"] == fixed(MEAN_EQUITY, 2)
    cumulative = Fraction(80290) / (2 * MEAN_EQUITY)
    assert turnover["one_sided_turnover"] == fixed(cumulative)
    assert turnover["annualised"] is None
    assert turnover["filled_notional"]["total"] != "98490.00"


def test_costs_are_split_by_component_with_their_annualised_share_of_mean_equity(
    story: Story,
) -> None:
    """Commission: 5.00 on each of the nine fills = 45.00 (every notional is below the 16,667 at
    which the 0.03% would pass the 5.00 floor). `transfer_fee` -- slippage in the protocol's book:
    0.09 on each 9,000-9,200 fill, 0.08 on the 8,000 one = 0.80. Stamp duty on the two sales:
    4.50 + 4.55 = 9.05. In all 54.85."""
    costs = ledger_of(story)["costs"]

    assert {name: costs[name]["total"] for name in ("commission", "slippage", "stamp_duty")} == {
        "commission": "45.00",
        "slippage": "0.80",
        "stamp_duty": "9.05",
    }
    assert costs["total"]["total"] == "54.85"
    for name in ("commission", "slippage", "stamp_duty", "total"):
        # Six sessions are too short to annualise (I6); the rule's own test is below.
        assert costs[name]["annualised_share_of_mean_equity"] is None, name
    assert "transfer_fee" in costs["slippage"]["ledger_field"]


# The initial build (I6): the S1 evening was the book's first decision, so its three buys --
# 600010.SH 900 x 10.00, 600020.SH 800 x 11.50, 600040.SH 900 x 10.00, filled at the S2 open, 5.00
# commission and 0.09 slippage each -- are the one-off build; every other fill is steady state.
INITIAL_NOTIONAL: Final[Fraction] = Fraction("27200.00")
INITIAL_COSTS: Final[Mapping[str, str]] = {
    "commission": "15.00",
    "slippage": "0.27",
    "stamp_duty": "0.00",
    "total": "15.27",
}
ALL_COSTS: Final[Mapping[str, str]] = {
    "commission": "45.00",
    "slippage": "0.80",
    "stamp_duty": "9.05",
    "total": "54.85",
}


def test_the_initial_build_is_reported_on_its_own_and_nothing_is_annualised_under_60_sessions(
    story: Story,
) -> None:
    """I6. The fills of the buys the book's first decision placed (`INITIAL_BUILD`, found by
    each fill's ledger id among the orders the snapshot before its session held pending) are the
    one-off build: 27,200.00 of the 80,290.00 filled, 15.27 of the 54.85 paid. They are reported
    on their own, and the steady state is everything else. Six settled sessions are fewer than
    60, so nothing is annualised: the record says it is too short to annualise, and why."""
    ledger = ledger_of(story)
    turnover, costs = ledger["turnover"], ledger["costs"]

    assert strategy_survivor_paper_report.MINIMUM_ANNUALISED_SESSIONS == 60
    assert turnover["initial_build"] == {
        "fills": 3,
        "filled_notional": "27200.00",
        "one_sided_turnover": fixed(INITIAL_NOTIONAL / (2 * MEAN_EQUITY)),
    }
    assert turnover["steady_state"] == {
        "fills": 6,
        "filled_notional": "53090.00",
        "one_sided_turnover": fixed((80290 - INITIAL_NOTIONAL) / (2 * MEAN_EQUITY)),
    }
    assert turnover["annualised"] is None
    too_short = "too short to annualise: 6 settled sessions, fewer than 60"
    assert turnover["annualisation"] == too_short
    assert costs["annualisation"] == too_short
    for name, paid in ALL_COSTS.items():
        initial = INITIAL_COSTS[name]
        assert (costs[name]["total"], costs[name]["initial_build"]) == (paid, initial), name
        assert costs[name]["steady_state"] == fixed(Fraction(paid) - Fraction(initial), 2), name
        assert costs[name]["annualised_share_of_mean_equity"] is None, name
    rule = strategy_survivor_paper_report.ANNUALISATION_RULE
    assert turnover["annualisation_rule"] == costs["annualisation_rule"] == rule
    assert "INITIAL_BUILD" in rule and "244" in rule and "60" in rule
    assert rule in " ".join((strategy_survivor_paper_report.__doc__ or "").split())


def test_a_fill_of_no_order_the_snapshot_before_it_held_pending_is_refused(story: Story) -> None:
    """A fill is an attempt of an order the book held pending the evening before; one whose ledger
    id names no such order cannot be told to be the build's or not, and does not describe this
    book."""
    rows = list(story.transitions)
    index = next(
        n for n, row in enumerate(rows) if row.status == "filled" and row.execution is not None
    )
    stray = rows[index].order.model_copy(update={"order_id": "tor_stray@2021-03-03"})
    rows[index] = rows[index].model_copy(update={"order": stray})

    with pytest.raises(PaperRecordError, match=r"tor_stray@2021-03-03.*pending"):
        record_of(story, transitions=rows)


QUIET_SESSIONS: Final[int] = 54
"""The sessions after S6 the long book settles with nothing to trade: six plus these are 60."""


@pytest.fixture(scope="module")
def long_story(tmp_path_factory: pytest.TempPathFactory) -> Story:
    """The story, and then 54 weekdays from 2021-03-10 settled as missed sessions by the real
    catch-up, every holding (600060.SH, 600070.SH, 600080.SH) quoted at its S6 close: nothing is
    pending after S6, so nothing trades, and every one of them ends at S6's 81,835.15."""
    from openalpha_cn.backtest.paper_evening import run_catch_up

    built = build_story(tmp_path_factory.mktemp("long-story"))
    book = built.results["book"]
    day, index = S6, 6
    for _ in range(QUIET_SESSIONS):
        day += timedelta(days=3 if day.weekday() == 4 else 1)
        index += 1
        run_catch_up(
            store=book.store,
            ledger=book.ledger,
            make_portfolio=evenings.make_portfolio,
            config=BOOK,
            session=day,
            session_index=index,
            market=evenings.market(
                evenings.quiet(F, day, "10.00"),
                evenings.quiet(G, day, "15.00"),
                evenings.quiet(H, day, "8.00"),
            ),
            delisted=(),
            observed_on=day,
        )
    snapshots = tuple(load_snapshot(book.store, session) for session in book.store.sessions())
    return Story(snapshots, tuple(book.ledger.list()), built.journals, built.results)


def test_at_60_settled_sessions_only_the_steady_state_flow_is_annualised_and_at_59_nothing(
    long_story: Story,
) -> None:
    """I6, the rule (module docstring): with 60 settled sessions the steady-state flow -- every
    fill but the initial build's -- is annualised, its total over the mean equity of the settled
    sessions times 244 / 60; the initial build is never annualised. One session fewer, 59, is too
    short, and nothing is."""
    assert len(long_story.snapshots) == 1 + SESSION_COUNT + QUIET_SESSIONS == 61
    assert long_story.transitions[-1].after.as_of <= S6
    assert all(item.book.equity == D("81835.15") for item in long_story.snapshots[7:])
    mean = (
        sum((Fraction(v[0]) for v in BOOKED.values()), Fraction(0))
        + QUIET_SESSIONS * Fraction("81835.15")
    ) / 60

    ledger = ledger_of(long_story)
    turnover, costs = ledger["turnover"], ledger["costs"]

    steady = (80290 - INITIAL_NOTIONAL) / (2 * mean)
    assert turnover["sessions"] == 60
    assert turnover["steady_state"]["one_sided_turnover"] == fixed(steady)
    assert turnover["annualised"] == fixed(steady * 244 / 60)
    assert turnover["annualised"] != fixed(Fraction(80290) / (2 * mean) * 244 / 60)
    assert turnover["annualisation"] == (
        "annualised over 60 settled sessions at 244 a year, the initial build left out"
    )
    for name, paid in ALL_COSTS.items():
        steady_cost = Fraction(paid) - Fraction(INITIAL_COSTS[name])
        assert costs[name]["annualised_share_of_mean_equity"] == fixed(
            steady_cost / mean * 244 / 60
        ), name

    short = ledger_of(long_story, snapshots=long_story.snapshots[:60])
    assert short["turnover"]["sessions"] == 59
    assert short["turnover"]["annualised"] is None
    assert short["turnover"]["annualisation"] == (
        "too short to annualise: 59 settled sessions, fewer than 60"
    )
    assert short["costs"]["total"]["annualised_share_of_mean_equity"] is None


def test_the_costs_by_component_equal_the_change_in_fees_paid(story: Story) -> None:
    check = ledger_of(story)["costs"]["cross_check"]

    assert check["matches"] is True
    assert check["ledger_total_cost"] == "54.85"
    assert check["fees_paid_change"] == "54.85"
    assert check["difference"] == "0.00"
    assert check["sessions_mismatched"] == []
    assert check["rows_whose_components_do_not_sum"] == []


def test_a_ledger_row_that_disagrees_with_fees_paid_is_reported_not_hidden(
    story: Story,
) -> None:
    """The last fill's commission is made 6.00 instead of 5.00 (the row is consistent with itself:
    6.00 + 0.08 = 6.08) while S6's snapshot still says 14.72 of fees: the record says so."""
    rows = list(story.transitions)
    last = rows[-1]
    assert last.execution is not None and last.execution.total_cost == D("5.08")
    rows[-1] = last.model_copy(
        update={
            "execution": last.execution.model_copy(
                update={"commission": D("6.00"), "total_cost": D("6.08")}
            )
        }
    )

    costs = ledger_of(story, transitions=rows)["costs"]

    check = costs["cross_check"]
    assert check["matches"] is False
    assert (check["ledger_total_cost"], check["fees_paid_change"]) == ("55.85", "54.85")
    assert check["difference"] == "1.00"
    assert check["sessions_mismatched"] == [
        {"session": "2021-03-09", "ledger_total_cost": "15.72", "fees_paid_change": "14.72"}
    ]
    assert check["rows_whose_components_do_not_sum"] == []
    assert costs["commission"]["total"] == "46.00"


def test_a_row_whose_components_do_not_sum_to_its_total_cost_is_named(story: Story) -> None:
    rows = list(story.transitions)
    last = rows[-1]
    assert last.execution is not None
    rows[-1] = last.model_copy(
        update={"execution": last.execution.model_copy(update={"total_cost": D("5.00")})}
    )

    check = ledger_of(story, transitions=rows)["costs"]["cross_check"]

    assert check["rows_whose_components_do_not_sum"] == [last.order.order_id]
    assert check["matches"] is False


# --- write-offs ---------------------------------------------------------------------------------


def test_a_write_off_is_found_by_its_reason_with_no_execution_and_its_cost_basis_is_positive(
    story: Story,
) -> None:
    """Two rows have `reason == "delisting_write_off"` and no execution: 600040.SH on S3 (900
    shares, cost 9,005.09 -- `realized_pnl_delta` -9,005.09) and 600020.SH on S4 (800 shares, cost
    9,205.09). The cost basis is the realised loss with its sign turned over. What each removed
    from the book's equity is what it was marked at -- 900 x 10.00 and 800 x 11.50 -- and its
    share is of the equity just before it: S3's 91,060.05 + 9,000.00 = 100,060.05, S4's 81,854.96 +
    9,200.00 = 91,054.96."""
    written = [row for row in story.transitions if row.reason == WRITE_OFF_REASON]
    assert [(row.order.subject, row.execution) for row in written] == [(DL, None), (B, None)]
    assert [row.realized_pnl_delta for row in written] == [D("-9005.09"), D("-9205.09")]

    block = ledger_of(story)["write_offs"]

    assert block["count"] == 2
    assert block["total_cost_basis"] == "18210.18"
    assert block["total_equity_removed"] == "18200.00"
    first, second = block["items"]
    assert (first["session"], first["session_index"], first["ts_code"]) == ("2021-03-04", 3, DL)
    assert (first["ledger_quantity"], first["cost_basis"]) == (900, "9005.09")
    assert (first["equity_before"], first["equity_removed"]) == ("100060.05", "9000.00")
    assert first["share_of_equity"] == fixed(Fraction("9000.00") / Fraction("100060.05"))
    assert (second["session"], second["session_index"], second["ts_code"]) == (
        "2021-03-05",
        4,
        B,
    )
    assert (second["ledger_quantity"], second["cost_basis"]) == (800, "9205.09")
    assert (second["equity_before"], second["equity_removed"]) == ("91054.96", "9200.00")
    assert second["share_of_equity"] == fixed(Fraction("9200.00") / Fraction("91054.96"))


def test_the_d16_recovery_is_the_ledger_quantity_at_the_previous_snapshots_mark(
    story: Story,
) -> None:
    """D16's "recovered at the last mark": 600020.SH was written off on S4 with 800 ledger shares,
    and the previous snapshot (S3) marks them at 11.50 -- the real close 5.75 times the ex-rights
    ratio 2 -- so 9,200.00, **in the ledger's units**. The snapshot before that (S2) marks them at
    11.40, so 9,120.00 would be the mark of the wrong snapshot. The real last close is 5.75: 800 x
    5.75 = 4,600.00 is not a number this record has any business printing, and nothing in the
    write-off ledger row carries a price."""
    block = ledger_of(story)["write_offs"]

    first, second = block["items"]
    assert first["recovered_at_last_mark"] == {
        "value": "9000.00",
        "ledger_mark": "10.00",
        "mark_session": "2021-03-03",
        "units": "ledger",
    }
    assert second["recovered_at_last_mark"] == {
        "value": "9200.00",
        "ledger_mark": "11.50",
        "mark_session": "2021-03-04",
        "units": "ledger",
    }
    assert block["total_recovered_at_last_mark"] == "18200.00"
    assert "4600.00" not in strings(block)
    assert story.snapshot(S2).book.mark(B) == D("11.40")
    assert "9120.00" not in strings(block)
    assert "ledger" in block["sensitivity"] and "real close" in block["sensitivity"]


def test_a_row_labelled_a_write_off_that_carries_an_execution_is_refused(story: Story) -> None:
    rows = list(story.transitions)
    index = next(i for i, row in enumerate(rows) if row.reason == WRITE_OFF_REASON)
    donor = next(row for row in rows if row.execution is not None and row.status == "filled")
    rows[index] = rows[index].model_copy(update={"execution": donor.execution})

    with pytest.raises(PaperRecordError, match=r"write-off.*carries an execution"):
        record_of(story, transitions=rows)


def test_a_filled_row_with_no_execution_that_is_not_a_write_off_is_refused(story: Story) -> None:
    rows = list(story.transitions)
    index = next(i for i, row in enumerate(rows) if row.reason == WRITE_OFF_REASON)
    rows[index] = rows[index].model_copy(update={"reason": "something_else"})

    with pytest.raises(PaperRecordError, match=r"no execution.*not a write-off"):
        record_of(story, transitions=rows)


# --- what could not be filled -------------------------------------------------------------------


def test_filled_and_rejected_come_from_the_ledger_with_the_reasons(story: Story) -> None:
    """Filled: seven buys and two sells (the write-offs are not fills). Rejected: 600050.SH's buy
    on S3, S4 and S5, each at its limit-up open."""
    ledger = ledger_of(story)["attempts"]["ledger"]

    assert ledger["filled"] == {"buy": 7, "sell": 2, "total": 9}
    assert ledger["rejected"] == {"total": 3, "by_reason": {LIMIT_UP_REFUSAL: 3}}
    assert ledger["write_offs"] == 2
    assert {row.status for row in story.transitions} == {"filled", "rejected"}


def test_not_attempted_expired_and_cancelled_come_from_the_journals_only(story: Story) -> None:
    """600070.SH's buy on S4 had no bar: it is `not_attempted`, and the ledger -- which stores
    filled and rejected transitions and nothing else -- has no row for it. 600050.SH's buy
    expired on S5. Both are in the journals and nowhere else, so with the journals gone they are
    not counted at all, and the record says how many sessions it has no journal for."""
    attempts = ledger_of(story)["attempts"]

    journals = attempts["journals"]
    assert journals["not_attempted"] == {"total": 1, "by_reason": {NO_BAR_REASON: 1}}
    assert (journals["expired"], journals["cancelled"]) == (1, 0)
    assert (journals["sessions_documented"], journals["sessions_undocumented"]) == (6, 0)
    assert all(row.reason != NO_BAR_REASON for row in story.transitions)

    bare = ledger_of(story, documents=())["attempts"]
    assert bare["ledger"] == attempts["ledger"]
    assert bare["journals"]["not_attempted"] == {"total": 0, "by_reason": {}}
    assert (bare["journals"]["expired"], bare["journals"]["cancelled"]) == (0, 0)
    assert (
        bare["journals"]["sessions_documented"],
        bare["journals"]["sessions_undocumented"],
    ) == (0, 6)
    assert "journals only" in attempts["basis"]


def test_a_cancelled_buy_is_counted_from_the_journal_of_its_session(tmp_path: Path) -> None:
    """S3 with 600050.SH out of the survivors: its refused buy is cancelled, not carried. Only S3
    has a journal here, so S1 and S2 are the sessions with none."""
    book = evenings.through_s2(tmp_path)
    cut = evenings.screened((F, "10.00"), (G, "15.00"), (B, "11.50"), (C, "10.10"), (H, "8.00"))
    result = evenings.tonight(book, screen=cut)
    closes = {A: "10.10", B: "11.50", C: "10.10", F: "10.00", G: "15.00", H: "8.00"}
    document = json.loads(
        json.dumps(
            evening_document(
                session=S3,
                session_index=3,
                as_of=at(S3),
                facts=facts_of(S3, closes, held=(A, B, DL), delisted=(DL,)),
                screen=cut,
                evening=result,
                catch_ups=(),
                ordinals=ORDINALS,
                book=BOOK,
            )
        )
    )
    snapshots = [load_snapshot(book.store, day) for day in book.store.sessions()]

    ledger = paper_record(
        snapshots,
        book.ledger.list(),
        [journal(S3, result=document)],
        windows=(),
        holding_count=3,
    )["paper_ledger"]

    journals = ledger["attempts"]["journals"]
    assert (journals["cancelled"], journals["expired"]) == (1, 0)
    assert (journals["sessions_documented"], journals["sessions_undocumented"]) == (1, 2)
    assert ledger["coverage"]["settled_sessions_without_document"] == ["2021-03-02", "2021-03-03"]


def test_the_journals_attempts_are_reconciled_with_the_ledgers_rows(story: Story) -> None:
    clean = ledger_of(story)["reconciliation"]
    assert clean["reconciled"] is True
    assert clean["mismatches"] == []
    assert clean["sessions_reconciled"] == SESSION_COUNT

    dropped = next(
        row
        for row in story.transitions
        if row.after.as_of == S4 and row.status == "filled" and row.reason is None
    )
    rows = [row for row in story.transitions if row is not dropped]

    reconciliation = ledger_of(story, transitions=rows)["reconciliation"]

    assert reconciliation["reconciled"] is False
    (mismatch,) = reconciliation["mismatches"]
    assert mismatch["session"] == "2021-03-05"
    assert mismatch["only_in_journal"] == [dropped.order.order_id]
    assert mismatch["only_in_ledger"] == []


def test_ledger_rows_of_a_session_with_no_snapshot_are_left_out_and_named(story: Story) -> None:
    """A crash after the rows and before the snapshot leaves rows for an unsettled session: the
    snapshot is the commit, so the record leaves them out of every figure and names them."""
    template = story.transitions[-1]
    stray = template.model_copy(
        update={
            "order": template.order.model_copy(update={"order_id": "tor_stray@2021-03-10"}),
            "after": template.after.model_copy(update={"as_of": date(2021, 3, 10)}),
        }
    )

    ledger = ledger_of(story, transitions=[*story.transitions, stray])

    assert ledger["reconciliation"]["rows_of_unsettled_sessions"] == ["tor_stray@2021-03-10"]
    assert ledger["attempts"]["ledger"]["filled"] == {"buy": 7, "sell": 2, "total": 9}
    assert ledger["costs"]["total"]["total"] == "54.85"
    assert ledger["turnover"]["filled_notional"]["total"] == "80290.00"


# --- what was decided ---------------------------------------------------------------------------


def test_decisions_are_counted_over_the_evenings_only_not_the_catch_up_sessions(
    story: Story,
) -> None:
    """Six sessions settled, four of them by an evening (S1, S2, S3, S5): S4 was caught up and S6
    settled only, and neither made a decision. The counts are over four evenings and say so."""
    ledger = ledger_of(story)
    decisions = ledger["decisions"]

    assert decisions["evenings"] == 4
    assert ledger["coverage"]["settled_sessions"] == 6
    assert "evenings only" in decisions["basis"]
    assert "catch-up" in decisions["basis"]
    assert decisions["exits"] == {
        "total": 2,
        "by_reason": {"EXIT_ELIGIBILITY_LOST": 1, "EXIT_SURVIVOR_LOST": 1},
    }
    for name in ("unfunded", "unaffordable", "frozen", "near_exit", "deferred_exits"):
        assert decisions[name] == {
            "name_sessions": 0,
            "evenings_with_any": 0,
            "most_in_one_evening": 0,
        }, name


def test_the_decisions_lists_are_counted_per_evening(story: Story) -> None:
    """Edited into S3's and S5's evening documents (the story's own draws left every list empty):
    S3 leaves 600030.SH frozen and 600080.SH deferred; S5 leaves two names near exit, one frozen
    and 600080.SH deferred, and the trade list leaves two names unfunded and one unaffordable."""
    edited = story.copies()
    paper3 = edited[3]["result"]["paper"]
    paper3["decision"]["frozen"] = [C]
    paper3["decision"]["deferred_exits"] = [H]
    paper5 = edited[5]["result"]["paper"]
    paper5["decision"]["near_exit"] = [F, G]
    paper5["decision"]["frozen"] = [C]
    paper5["decision"]["deferred_exits"] = [H]
    paper5["decision"]["unaffordable"] = [E]
    paper5["trade_list"]["unfunded"] = [H, E]

    decisions = ledger_of(story, documents=edited)["decisions"]

    assert decisions["frozen"] == {
        "name_sessions": 2,
        "evenings_with_any": 2,
        "most_in_one_evening": 1,
    }
    assert decisions["near_exit"] == {
        "name_sessions": 2,
        "evenings_with_any": 1,
        "most_in_one_evening": 2,
    }
    assert decisions["deferred_exits"] == {
        "name_sessions": 2,
        "evenings_with_any": 2,
        "most_in_one_evening": 1,
    }
    assert decisions["unaffordable"] == {
        "name_sessions": 1,
        "evenings_with_any": 1,
        "most_in_one_evening": 1,
    }
    assert decisions["unfunded"] == {
        "name_sessions": 2,
        "evenings_with_any": 1,
        "most_in_one_evening": 2,
    }


def test_illiquid_exits_are_listed_with_their_rank_and_population(story: Story) -> None:
    """None of the story's exits was cut for thin amount; the document of S5 is given one (rank 3
    of a population of 40, the bottom 10 cut) and the record lists it, counted over the evenings."""
    assert ledger_of(story)["illiquid_exits"]["count"] == 0

    edited = story.copies()
    edited[5]["result"]["paper"]["illiquid_exits"] = [
        {"ts_code": C, "rank": 3, "population": 40, "cut": 10}
    ]

    block = ledger_of(story, documents=edited)["illiquid_exits"]

    assert block["count"] == 1
    assert block["evenings_with_any"] == 1
    assert block["exits"] == [
        {"session": "2021-03-08", "ts_code": C, "rank": 3, "population": 40, "cut": 10}
    ]
    assert "1 = thinnest" in block["rank_basis"]


# --- coverage -----------------------------------------------------------------------------------


def test_coverage_names_the_evenings_the_settle_only_days_the_catch_ups_and_the_refused_days(
    story: Story,
) -> None:
    coverage = ledger_of(story)["coverage"]

    assert coverage["settled_sessions"] == 6
    assert (coverage["opening_session"], coverage["opening_snapshot_id"]) == (
        "2021-03-01",
        story.snapshots[0].snapshot_id,
    )
    assert (coverage["first_settled"], coverage["last_settled"]) == ("2021-03-02", "2021-03-09")
    assert coverage["documents"] == 7
    assert coverage["opening_days"] == 1
    assert coverage["evenings"] == 4
    assert coverage["settle_only_days"] == 1
    assert coverage["caught_up_sessions"] == 1
    assert coverage["caught_up"] == ["2021-03-05"]
    assert coverage["settle_only"] == ["2021-03-09"]
    assert coverage["refused_days"] == [
        {"session": "2021-03-05", "stages_filed": ["panel", "prediction"]}
    ]
    assert coverage["settled_sessions_without_document"] == []


def test_a_settled_session_no_journal_describes_is_named(story: Story) -> None:
    """Without S2's journal, S2 is a session the record has a snapshot for and no document of: its
    equity is counted, its decision and its orders' fates are not, and the record says so."""
    kept = [item for item in story.journals if item["session"] != S2.isoformat()]

    ledger = ledger_of(story, documents=kept)

    assert ledger["coverage"]["settled_sessions_without_document"] == ["2021-03-03"]
    assert ledger["coverage"]["evenings"] == 3
    assert ledger["decisions"]["evenings"] == 3
    assert ledger["attempts"]["journals"]["sessions_undocumented"] == 1
    assert ledger["equity"]["sessions"] == 6
    assert ledger["reconciliation"]["sessions_reconciled"] == 5


def test_a_book_opened_and_never_settled_gives_an_empty_record(story: Story) -> None:
    ledger = ledger_of(story, snapshots=story.snapshots[:1], transitions=(), documents=())

    assert ledger["equity"]["sessions"] == 0
    assert ledger["equity"]["curve"] == []
    assert ledger["equity"]["final_equity"] is None
    assert ledger["turnover"]["one_sided_turnover"] is None
    assert ledger["turnover"]["annualised"] is None
    assert ledger["holdings"]["mean_held"] is None
    assert ledger["costs"]["total"]["annualised_share_of_mean_equity"] is None
    assert ledger["costs"]["cross_check"]["matches"] is True
    assert ledger["write_offs"]["count"] == 0
    assert ledger["coverage"]["settled_sessions"] == 0


# --- the journals are checked against the store -------------------------------------------------


def _evening_id(journals: list[dict[str, Any]]) -> None:
    journals[3]["result"]["paper"]["snapshot_id"] = FOREIGN_ID


def _stage_id(journals: list[dict[str, Any]]) -> None:
    journals[3]["paper"]["snapshot_id"] = FOREIGN_ID


def _filed_catch_up_id(journals: list[dict[str, Any]]) -> None:
    journals[5]["catch_ups"][0]["snapshot_id"] = FOREIGN_ID


def _documented_catch_up_id(journals: list[dict[str, Any]]) -> None:
    journals[5]["result"]["paper"]["catch_ups"][0]["snapshot_id"] = FOREIGN_ID


def _settle_only_id(journals: list[dict[str, Any]]) -> None:
    journals[6]["result"]["paper_skipped"]["catch_ups"][0]["snapshot_id"] = FOREIGN_ID


def _opening_id(journals: list[dict[str, Any]]) -> None:
    journals[0]["result"]["opening"]["snapshot_id"] = FOREIGN_ID


@pytest.mark.parametrize(
    ("edit", "session"),
    [
        (_evening_id, "2021-03-04"),
        (_stage_id, "2021-03-04"),
        (_filed_catch_up_id, "2021-03-05"),
        (_documented_catch_up_id, "2021-03-05"),
        (_settle_only_id, "2021-03-09"),
        (_opening_id, "2021-03-01"),
    ],
    ids=[
        "an evening's snapshot",
        "the paper stage's snapshot",
        "a filed catch-up's snapshot",
        "a documented catch-up's snapshot",
        "a settle-only day's own snapshot",
        "the opening's snapshot",
    ],
)
def test_a_journal_whose_snapshot_id_differs_from_the_stores_is_refused_by_name(
    story: Story, edit: Callable[[list[dict[str, Any]]], None], session: str
) -> None:
    """Each id a journal gives for a session is compared with the snapshot the store holds for it,
    and a difference refuses the whole record, naming the session, the journal's id and the store's
    -- never a quiet preference for one of them."""
    edited = story.copies()
    edit(edited)
    index = {day.isoformat(): n for n, day in enumerate((E0, S1, S2, S3, S4, S5, S6))}[session]
    held = story.snapshots[index].snapshot_id

    with pytest.raises(JournalSnapshotMismatchError) as refused:
        record_of(story, documents=edited)

    message = str(refused.value)
    assert session in message
    assert FOREIGN_ID in message
    assert held in message
    assert isinstance(refused.value, PaperRecordError)


def test_a_journal_that_says_it_settled_a_session_the_store_does_not_hold_is_refused(
    story: Story,
) -> None:
    with pytest.raises(JournalSnapshotMismatchError, match=r"2021-03-08.*holds no snapshot"):
        record_of(story, snapshots=story.snapshots[:5])


def test_a_session_settled_twice_by_two_journals_is_refused(story: Story) -> None:
    with pytest.raises(PaperRecordError, match=r"2021-03-04.*two journals"):
        record_of(story, documents=[*story.journals, story.journals[3]])


def test_a_session_settled_two_ways_is_refused(story: Story) -> None:
    """S3 was settled by an evening; a later journal that lists it as caught up claims the same
    session was settled a second way."""
    edited = story.copies()
    entry = copy.deepcopy(edited[5]["catch_ups"][0])
    entry.update(
        {"session": "2021-03-04", "session_index": 3, "snapshot_id": story.snapshots[3].snapshot_id}
    )
    edited[5]["catch_ups"].insert(0, entry)

    with pytest.raises(PaperRecordError, match=r"2021-03-04.*settled twice"):
        record_of(story, documents=edited)


def test_a_journal_that_is_not_a_survivor_journal_is_refused(story: Story) -> None:
    edited = story.copies()
    edited[2]["schema"] = "openalpha-daily-selection/v1"

    with pytest.raises(PaperRecordError, match=r"not a .* journal.*openalpha-daily-selection/v1"):
        record_of(story, documents=edited)


def test_the_journals_schema_is_the_one_the_daily_command_files() -> None:
    script = (ROOT / "scripts" / "daily_selection.py").read_text(encoding="utf-8")
    found = re.search(r'^SURVIVOR_JOURNAL_SCHEMA: Final\[str\] = "([^"]+)"$', script, re.MULTILINE)

    assert found is not None
    assert found.group(1) == JOURNAL_SCHEMA == strategy_survivor_paper_report.JOURNAL_SCHEMA


# --- the snapshots and the ledger are checked ---------------------------------------------------


def test_snapshots_are_taken_in_any_order_but_must_be_the_whole_history(story: Story) -> None:
    shuffled = [story.snapshots[n] for n in (3, 0, 6, 1, 5, 2, 4)]
    assert record_of(story, snapshots=shuffled) == record_of(story)

    with pytest.raises(PaperRecordError, match="not contiguous"):
        record_of(story, snapshots=[*story.snapshots[:3], *story.snapshots[4:]])
    with pytest.raises(PaperRecordError, match="twice"):
        record_of(story, snapshots=[*story.snapshots, story.snapshots[2]])
    with pytest.raises(PaperRecordError, match="opening"):
        record_of(story, snapshots=story.snapshots[1:])
    with pytest.raises(PaperRecordError, match="no snapshot"):
        record_of(story, snapshots=())


def test_snapshots_of_two_configurations_are_refused(story: Story) -> None:
    other = story.snapshots[3].model_copy(update={"config_digest": "0" * 64})

    with pytest.raises(PaperRecordError, match="configuration"):
        record_of(story, snapshots=[*story.snapshots[:3], other, *story.snapshots[4:]])


def test_a_ledger_order_id_held_twice_is_refused(story: Story) -> None:
    with pytest.raises(PaperRecordError, match="more than once"):
        record_of(story, transitions=[*story.transitions, story.transitions[0]])


def test_a_holding_count_below_one_is_refused(story: Story) -> None:
    with pytest.raises(PaperRecordError, match="holding_count"):
        record_of(story, holding_count=0)


# --- the relative return ------------------------------------------------------------------------


def window(
    record: str, entry: date, exit_: date, ew: str, csi: str, *, as_float: bool = False
) -> BenchmarkWindow:
    return BenchmarkWindow(
        record_id=record,
        entry=entry,
        exit=exit_,
        investable_ew=float(ew) if as_float else D(ew),
        csi500=float(csi) if as_float else D(csi),
    )


def test_the_window_chain_compounds_book_and_benchmarks_and_a_gap_breaks_it(
    story: Story,
) -> None:
    """Five windows. w1 S1-S2 and w2 S2-S3 are contiguous (w2 enters where w1 exits) and compound;
    no window runs from S3 to S4, so that is a gap; w3 S4-S5 and w4 S5-S6 compound again; w5 runs to
    2021-03-12, a session the book's history (it ends 2021-03-09) has no snapshot for, and cannot
    be computed.

    The book's returns are the snapshots' equity ratios: w1 99,904.73 / 100,000.00 - 1, w2
    91,060.05 / 99,904.73 - 1, w3 81,849.87 / 81,854.96 - 1, w4 81,835.15 / 81,849.87 - 1.
    Compounded along w1-w2 the ratios telescope to 91,060.05 / 100,000.00 - 1 = -0.0893995; along
    w3-w4 to 81,835.15 / 81,854.96 - 1. The benchmarks compound as (1+r1)(1+r2) - 1: the
    investable equal weight 1.01 x 0.98 - 1 = -0.0102, CSI 500 1.02 x 1.01 - 1 = 0.0302."""
    windows = [
        window("prd_w1", S1, S2, "0.010", "0.020"),
        window("prd_w2", S2, S3, "-0.020", "0.010"),
        window("prd_w3", S4, S5, "0.030", "-0.010"),
        window("prd_w4", S5, S6, "0.005", "0.015"),
        window("prd_w5", S6, date(2021, 3, 12), "0.040", "0.050"),
    ]

    relative = record_of(story, windows=windows)["relative"]

    rows = {row["record_id"]: row for row in relative["windows"]}
    assert list(rows) == ["prd_w1", "prd_w2", "prd_w3", "prd_w4", "prd_w5"]
    w1 = rows["prd_w1"]
    assert (w1["entry"], w1["exit"], w1["status"]) == ("2021-03-02", "2021-03-03", "computed")
    assert w1["book_return"] == fixed(Fraction("99904.73") / Fraction("100000.00") - 1)
    assert (w1["investable_ew"], w1["csi500"]) == ("0.010000", "0.020000")
    assert w1["book_minus_ew"] == fixed(
        Fraction("99904.73") / Fraction("100000.00") - 1 - Fraction("0.010")
    )
    assert rows["prd_w2"]["book_return"] == fixed(Fraction("91060.05") / Fraction("99904.73") - 1)
    assert rows["prd_w3"]["book_return"] == fixed(Fraction("81849.87") / Fraction("81854.96") - 1)
    assert rows["prd_w4"]["book_return"] == fixed(Fraction("81835.15") / Fraction("81849.87") - 1)
    w5 = rows["prd_w5"]
    assert w5["status"] == "unavailable"
    assert w5["book_return"] is None
    assert "2021-03-12" in w5["reason"] and "2021-03-09" in w5["reason"]

    first, second = relative["chains"]
    assert first["record_ids"] == ["prd_w1", "prd_w2"]
    assert (first["entry"], first["exit"], first["windows"]) == ("2021-03-02", "2021-03-04", 2)
    assert first["book"] == "-0.089400"  # -0.0893995, half away from zero
    assert first["book"] == fixed(Fraction("91060.05") / Fraction("100000.00") - 1)
    assert first["investable_ew"] == "-0.010200"
    assert first["csi500"] == "0.030200"
    assert first["book_minus_ew"] == fixed(Fraction("-0.0893995") + Fraction("0.0102"))
    assert first["book_minus_csi500"] == fixed(Fraction("-0.0893995") - Fraction("0.0302"))
    assert second["record_ids"] == ["prd_w3", "prd_w4"]
    assert (second["entry"], second["exit"]) == ("2021-03-05", "2021-03-09")
    assert second["book"] == fixed(Fraction("81835.15") / Fraction("81854.96") - 1)
    assert second["investable_ew"] == fixed(Fraction("1.030") * Fraction("1.005") - 1)
    assert second["csi500"] == fixed(Fraction("0.990") * Fraction("1.015") - 1)

    assert relative["gaps"] == [
        {
            "kind": "no_window",
            "after": "prd_w2",
            "before": "prd_w3",
            "from": "2021-03-04",
            "to": "2021-03-05",
            "reason": (
                "no window runs from 2021-03-04 to 2021-03-05: the chain is broken, and the "
                "returns on either side are not compounded across it"
            ),
        },
        {
            "kind": "no_snapshot",
            "record_id": "prd_w5",
            "entry": "2021-03-09",
            "exit": "2021-03-12",
            "missing": ["2021-03-12"],
            "reason": w5["reason"],
        },
    ]


def test_a_chain_says_how_much_of_its_book_return_the_write_offs_inside_it_took(
    story: Story,
) -> None:
    """I7. A chain entering on S1 and exiting on S3 holds 600040.SH's write-off on S3, which
    removed the 9,000.00 it was marked at: 9.00 points of the chain's -8.94% book return, over the
    100,000.00 it entered with. One entering on S4 -- the session 600020.SH was written off on,
    which its entry equity already holds -- and exiting on S6 holds none. One from S3 to S5 holds
    600020.SH's, 9,200.00 of S3's 91,060.05."""
    first, second = record_of(
        story,
        windows=[
            window("prd_w1", S1, S2, "0.010", "0.020"),
            window("prd_w2", S2, S3, "-0.020", "0.010"),
            window("prd_w3", S4, S5, "0.030", "-0.010"),
            window("prd_w4", S5, S6, "0.005", "0.015"),
        ],
    )["relative"]["chains"]
    (across,) = record_of(story, windows=[window("prd_x", S3, S5, "0.0", "0.0")])["relative"][
        "chains"
    ]

    assert first["write_offs"] == {
        "count": 1,
        "equity_removed": "9000.00",
        "share_of_entry_equity": fixed(Fraction("9000.00") / Fraction("100000.00")),
    }
    assert second["write_offs"] == {
        "count": 0,
        "equity_removed": "0.00",
        "share_of_entry_equity": "0.000000",
    }
    assert across["write_offs"] == {
        "count": 1,
        "equity_removed": "9200.00",
        "share_of_entry_equity": fixed(Fraction("9200.00") / Fraction("91060.05")),
    }
    relative = record_of(story, windows=[window("prd_w1", S1, S2, "0.010", "0.020")])["relative"]
    assert "entry equity" in relative["write_off_basis"]


def test_the_report_prints_money_in_yuan_returns_in_percent_and_the_initial_build_apart(
    story: Story, long_story: Story
) -> None:
    """I6 and I7, as the weekly report prints the record (`strategy_survivor_forward`'s lines of
    the two sections, read from the record alone): equity in yuan with the ledger's caveat for a
    holding after an adjustment; turnover and costs with the one-off initial build beside them
    and the steady state annualised only from 60 settled sessions on; the write-offs' cost basis
    beside their count; every chain's returns as percentages, and the points of its book return
    the write-offs inside it took."""
    from openalpha_cn.strategy_survivor_forward import _ledger_lines, _relative_lines

    record = record_of(
        story,
        windows=[
            window("prd_w1", S1, S2, "0.010", "0.020"),
            window("prd_w2", S2, S3, "-0.020", "0.010"),
            window("prd_w3", S4, S5, "0.030", "-0.010"),
            window("prd_w4", S5, S6, "0.005", "0.015"),
        ],
    )

    lines = _ledger_lines(record["paper_ledger"])

    too_short = "too short to annualise: 6 settled sessions, fewer than 60"
    assert (
        f"  equity (yuan): opening 100000.00, final 81835.15, mean {fixed(MEAN_EQUITY, 2)}, "
        "minimum 81835.15; a holding's share count and mark are the paper ledger's, which after "
        "an adjustment (a bonus issue, a cash dividend) are not the account's"
    ) in lines
    assert (
        f"  turnover: one-sided {fixed(Fraction(80290) / (2 * MEAN_EQUITY))} over 6 settled "
        f"sessions, of which the one-off initial build "
        f"{fixed(INITIAL_NOTIONAL / (2 * MEAN_EQUITY))} (3 fills); annualised, the initial "
        f"build left out: {too_short}"
    ) in lines
    assert (
        "  costs: 54.85 yuan, of which the one-off initial build 15.27 yuan; annualised share of "
        f"mean equity, the initial build left out: {too_short}"
    ) in lines
    assert (
        "  write-offs: 2, cost basis 18210.18 yuan in all (equity removed 18200.00 yuan, at "
        "their last marks)"
    ) in lines
    assert not any("ledger units)" in line for line in lines)
    assert _relative_lines(record["relative"])[2:4] == [
        "  chain 2021-03-02..2021-03-04 (2 windows): book -8.94%, investable EW -1.02%, CSI 500 "
        "3.02%; the write-offs inside it took 9.00 points of the book's return (1 written off, "
        "9000.00 yuan removed)",
        "  chain 2021-03-05..2021-03-09 (2 windows): book -0.02%, investable EW 3.52%, CSI 500 "
        "0.49%",
    ]

    long = _ledger_lines(ledger_of(long_story))
    mean = (
        sum((Fraction(v[0]) for v in BOOKED.values()), Fraction(0))
        + QUIET_SESSIONS * Fraction("81835.15")
    ) / 60
    steady = (80290 - INITIAL_NOTIONAL) / (2 * mean)
    share = (Fraction("54.85") - Fraction("15.27")) / mean * 244 / 60
    turnover = next(line for line in long if line.startswith("  turnover:"))
    costs = next(line for line in long if line.startswith("  costs:"))
    assert turnover.endswith(
        f"annualised, the initial build left out: {fixed(steady * 244 / 60)} a year over 60 "
        "settled sessions"
    )
    percent = Decimal(fixed(share)) * 100
    assert costs.endswith(
        "annualised share of mean equity, the initial build left out: "
        f"{percent.quantize(CENT, rounding=ROUND_HALF_UP)}% a year over 60 settled sessions"
    )


def test_a_window_whose_entry_is_missing_is_named_and_breaks_the_chain(story: Story) -> None:
    windows = [
        window("prd_w1", date(2021, 2, 26), S2, "0.010", "0.020"),
        window("prd_w2", S2, S3, "-0.020", "0.010"),
    ]

    relative = record_of(story, windows=windows)["relative"]

    assert [row["status"] for row in relative["windows"]] == ["unavailable", "computed"]
    assert "2021-02-26" in relative["windows"][0]["reason"]
    assert [chain["record_ids"] for chain in relative["chains"]] == [["prd_w2"]]
    assert [gap["kind"] for gap in relative["gaps"]] == ["no_snapshot"]


def test_the_chain_telescopes_to_the_equity_ratio_whatever_the_window_returns(
    story: Story,
) -> None:
    windows = [
        window("prd_w1", S1, S2, "0.1", "0.1"),
        window("prd_w2", S2, S3, "0.1", "0.1"),
        window("prd_w3", S3, S4, "0.1", "0.1"),
        window("prd_w4", S4, S5, "0.1", "0.1"),
        window("prd_w5", S5, S6, "0.1", "0.1"),
    ]

    (chain,) = record_of(story, windows=windows)["relative"]["chains"]

    assert chain["windows"] == 5
    assert chain["book"] == fixed(Fraction("81835.15") / Fraction("100000.00") - 1)
    assert chain["investable_ew"] == fixed(Fraction("1.1") ** 5 - 1)
    assert record_of(story, windows=windows)["relative"]["gaps"] == []


def test_windows_are_taken_in_any_order_and_floats_are_read_by_their_shortest_text(
    story: Story,
) -> None:
    ordered = [
        window("prd_w1", S1, S2, "0.010", "0.020"),
        window("prd_w2", S2, S3, "-0.020", "0.010"),
    ]
    floats = [
        window("prd_w2", S2, S3, "-0.020", "0.010", as_float=True),
        window("prd_w1", S1, S2, "0.010", "0.020", as_float=True),
    ]

    assert record_of(story, windows=floats) == record_of(story, windows=ordered)


def test_the_relative_return_says_what_it_compares_and_in_which_units(story: Story) -> None:
    relative = record_of(story, windows=[window("prd_w1", S1, S2, "0.010", "0.020")])["relative"]

    assert WINDOW_LABEL == LABEL
    assert relative["label"] == LABEL
    assert relative["wording"] == SURVIVOR_WORDING
    assert "ledger" in relative["units"]["book"]
    assert "real closes" in relative["units"]["benchmarks"]
    assert relative["status"].startswith("descriptive, not a test")


def test_windows_that_cannot_be_chained_or_read_are_refused(story: Story) -> None:
    with pytest.raises(PaperRecordError, match="overlap"):
        record_of(
            story,
            windows=[window("a", S1, S3, "0.01", "0.01"), window("b", S2, S4, "0.01", "0.01")],
        )
    with pytest.raises(PaperRecordError, match="more than once"):
        record_of(
            story,
            windows=[window("a", S1, S2, "0.01", "0.01"), window("a", S2, S3, "0.01", "0.01")],
        )
    with pytest.raises(PaperRecordError, match="exit"):
        BenchmarkWindow(record_id="a", entry=S2, exit=S2, investable_ew=D("0"), csi500=D("0"))
    with pytest.raises(PaperRecordError, match="record_id"):
        BenchmarkWindow(record_id="", entry=S1, exit=S2, investable_ew=D("0"), csi500=D("0"))
    with pytest.raises(PaperRecordError, match="finite"):
        BenchmarkWindow(record_id="a", entry=S1, exit=S2, investable_ew=D("NaN"), csi500=D("0"))
    with pytest.raises(PaperRecordError, match="-100%"):
        BenchmarkWindow(record_id="a", entry=S1, exit=S2, investable_ew=D("-1"), csi500=D("0"))


# --- wording, determinism and purity ------------------------------------------------------------


def test_every_block_carries_the_wording_and_says_which_units_its_numbers_are_in(
    story: Story,
) -> None:
    record = record_of(story, windows=[window("prd_w1", S1, S2, "0.010", "0.020")])

    ledger = record["paper_ledger"]
    assert ledger["wording"] == SURVIVOR_WORDING
    assert "ledger equity is not evidence" in SURVIVOR_WORDING
    assert ledger["status"].startswith("descriptive, not a test")
    blocks = [
        "coverage",
        "equity",
        "holdings",
        "turnover",
        "costs",
        "write_offs",
        "attempts",
        "decisions",
        "illiquid_exits",
        "reconciliation",
    ]
    assert set(blocks) <= set(ledger)
    for name in blocks:
        assert ledger[name]["wording"] == SURVIVOR_WORDING, name
        assert ledger[name]["units"], name
    assert "ledger" in ledger["equity"]["units"]
    assert "ledger" in ledger["costs"]["units"]
    assert "count" in ledger["decisions"]["units"]


BANNED: Final[tuple[str, ...]] = (
    "effective",
    "confirmed",
    "alpha",
    "significan",
    "outperform",
    "recommend",
    "forecast",
    "p-value",
)


def test_the_record_claims_no_performance_no_significance_and_no_verdict(story: Story) -> None:
    record = record_of(story, windows=[window("prd_w1", S1, S2, "0.010", "0.020")])

    for text in strings(record):
        lowered = text.lower()
        for word in BANNED:
            assert word not in lowered, f"{word!r} in {text!r}"


def test_the_record_is_json_and_its_keys_keep_their_order(story: Story) -> None:
    record = record_of(story, windows=[window("prd_w1", S1, S2, "0.010", "0.020")])

    text = json.dumps(record, sort_keys=False)
    assert json.loads(text) == record
    assert json.dumps(json.loads(text), sort_keys=False) == text
    assert list(record) == ["paper_ledger", "relative"]


def test_the_inputs_are_not_changed(story: Story) -> None:
    journals = story.copies()
    before = copy.deepcopy(journals)
    transitions = list(story.transitions)

    record_of(story, documents=journals, transitions=transitions)

    assert journals == before
    assert transitions == list(story.transitions)


def record_text(directory: str) -> str:
    """The subprocess half of the hash-seed test: the story off a fresh book in `directory`, its
    record with a chain and a gap, as one string."""
    built = build_story(Path(directory))
    record = paper_record(
        built.snapshots,
        built.transitions,
        built.journals,
        windows=[
            window("prd_w1", S1, S2, "0.010", "0.020"),
            window("prd_w2", S2, S3, "-0.020", "0.010"),
            window("prd_w3", S4, S5, "0.030", "-0.010"),
            window("prd_w5", S6, date(2021, 3, 12), "0.040", "0.050"),
        ],
        holding_count=BOOK.holding_count,
    )
    return json.dumps(record, sort_keys=False)


def test_processes_with_different_hash_seeds_build_the_same_record(tmp_path: Path) -> None:
    here = Path(__file__).resolve()
    program = (
        "import hashlib, importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('paper_report', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec);"
        "sys.modules['paper_report'] = module;"
        "spec.loader.exec_module(module);"
        "print(hashlib.sha256(module.record_text(sys.argv[2]).encode()).hexdigest())"
    )
    path = os.pathsep.join(
        part
        for part in (
            str(ROOT / "src"),
            str(ROOT / "tests"),
            os.fspath(here.parent),
            os.environ.get("PYTHONPATH"),
        )
        if part
    )
    reference = hashlib.sha256(record_text(str(tmp_path / "here")).encode()).hexdigest()

    for hash_seed in ("1", "424242"):
        completed = subprocess.run(
            [sys.executable, "-c", program, str(here), str(tmp_path / f"seed-{hash_seed}")],
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": path},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == reference


def test_the_module_reads_no_file_no_clock_and_no_network() -> None:
    tree = ast.parse(inspect.getsource(strategy_survivor_paper_report))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }

    assert imported.isdisjoint(
        {
            "os",
            "pathlib",
            "sqlite3",
            "socket",
            "subprocess",
            "time",
            "urllib",
            "http",
            "shutil",
            "numpy",
            "pandas",
            "duckdb",
        }
    )
    assert "openalpha_cn.storage" not in {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert called.isdisjoint({"open", "now", "today", "utcnow", "read_text", "write_text", "mkdir"})
