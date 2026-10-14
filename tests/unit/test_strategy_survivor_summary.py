"""The survivor evening's record and its human summary (`V2-P7-008`, task D4).

`strategy_survivor_summary` turns what one survivor evening produced -- the facts day, its screen,
the paper book's evening and the sessions it caught up -- into the JSON document the daily
journal files as its `result`, and that document into the lines the daily command prints. It is
pure: everything arrives as an argument, nothing is read, written, fetched or timed.

## Where the fixtures come from

Nothing here is a second frame.

- **The paper book** is `tests/unit/backtest/test_paper_evening.py`'s own: its helpers, its
  prices and its screens, run through the real `run_evening` and `run_catch_up` on a real
  snapshot store and ledger in `tmp_path`. The book opens on E0 2021-03-01 (ordinal 0); S1 ...
  S6 are the sessions after it. The evening of S2 plans a sale and two buys; the evening of S3
  settles a sale, a fill, a refusal at the limit-up open and a write-off, and carries a buy; the
  S3 variants hold a name through a 2-for-1 adjustment (the real close halves, the ledger's
  units do not), hold a name with no bar (carried at its last mark) and cancel the carried buy;
  the settle-only day is S4's catch-up; the catch-up day is that file's three missed sessions
  followed by the evening of S5.
- **The facts day** of those evenings is hand-built from the closes the evening's own prices say
  (`FactsDay` is a plain value), because a fact row is all the summary reads of it. The one
  place the facts adapter's real output matters -- the notes, and the screen's liquidity
  population -- is the facts-panel frame's (`tests/unit/test_survivor_facts_panel.py`),
  imported, not copied: its `EXTENDED` panel read at the signal day, whose notes count a code
  priced before its listing and two codes on a board the adapter does not recognise, and whose
  screen is the real kernel's.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from test_survivor_facts_panel import (
    CONFIG as FRAME_CONFIG,
)
from test_survivor_facts_panel import (
    EXTENDED,
    HALTED,
    SESSIONS,
    SIGNAL,
    SIGNAL_INDEX,
    UNRECOGNISED_PREFIX,
    read_day,
    write_panel,
)
from test_survivor_facts_panel import (
    evening as frame_evening,
)

from openalpha_cn import strategy_survivor_summary
from openalpha_cn.backtest.paper_evening import CatchUpResult, EveningResult
from openalpha_cn.backtest.paper_state import PendingOrder
from openalpha_cn.backtest.paper_step import NO_BAR_REASON
from openalpha_cn.domain.survivor_book import (
    EligibilityVerdict,
    ScreenResult,
    SecurityFacts,
    SurvivorBookConfig,
)
from openalpha_cn.domain.survivor_policy import ExitDecision
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.strategy_survivor_facts import FactsDay, FactsNotes, HeldStatus
from openalpha_cn.strategy_survivor_market import SessionOrdinals
from openalpha_cn.strategy_survivor_summary import (
    SURVIVOR_WORDING,
    evening_document,
    illiquid_rank,
    summary_lines,
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
BOOK: Final[SurvivorBookConfig] = evenings.CONFIG
LIMIT_UP_REFUSAL: Final[str] = "buy cannot fill on a one-price limit-up bar"
BAND_WITNESS_ENDED: Final[str] = "band = 0: no band witness since 2025-07-07"
CAVEAT: Final[str] = "today's draw, not a promise for tomorrow"
SUSPENDED_CLOSE: Final[str] = "(suspended: the close may be an earlier session's)"
WORDING: Final[str] = (
    "candidates of a registered research configuration; no order was placed; ledger equity is "
    "not evidence; with N=10, single-year results are mostly luck"
)
SKIPPED: Final[str] = "the run began after 09:15 of the next session"


def row(label: str, text: str) -> str:
    """A labelled line as the summary prints it: the label padded to 18, a space, the text."""
    return f"{label:<18} {text}"


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
"""The book's count of sessions through S6 (2021-03-09, ordinal 6)."""
SHORT_ORDINALS: Final[SessionOrdinals] = SessionOrdinals(weekdays(E0, S3), E0)
"""The same count, with a calendar that ends on S3: an order's last session may lie beyond it."""
ENDS_ON_S2: Final[SessionOrdinals] = SessionOrdinals(weekdays(E0, S2), E0)
"""And one that ends on S2: the open the evening of S2 plans for, ordinal 3, is not in it."""


# --- facts days ---------------------------------------------------------------------------------

NO_NOTES: Final[FactsNotes] = FactsNotes.tally(
    reasons=(),
    suspended_without_bar=0,
    no_listing_row=0,
    no_bar_within_carry=0,
    not_listed_with_bar=0,
    unrecognised_board_codes=(),
)


def fact(
    code: str, close: str, *, suspended: bool = False, amount: str = "5000000"
) -> SecurityFacts:
    return SecurityFacts(
        ts_code=code,
        board="main",
        listed_sessions=900,
        special_treatment=False,
        suspended=suspended,
        close=D(close),
        avg_amount=D(amount),
        oriented_values=(0.1, 0.2, 0.3, 0.4),
    )


def facts_of(
    session: date,
    closes: Mapping[str, str],
    *,
    held: Sequence[str] = (),
    no_row: Mapping[str, str] | None = None,
    suspended: Sequence[str] = (),
    notes: FactsNotes = NO_NOTES,
) -> FactsDay:
    """A facts day of one row per code in `closes` (ascending), and for each code of `held` what
    became of it: a row, or -- for a code in `no_row` -- the status that says why it has none."""
    rows = tuple(
        fact(code, close, suspended=code in suspended) for code, close in sorted(closes.items())
    )
    gone = dict(no_row or {})
    return FactsDay(
        session=session,
        facts=rows,
        notes=notes,
        boards=("main",),
        universe_size=len(rows),
        scored_size=len(rows),
        held=tuple(sorted(held)),
        held_status=tuple(
            HeldStatus(ts_code=code, status=gone.get(code, "row"))  # type: ignore[arg-type]
            for code in sorted(held)
        ),
    )


S2_CLOSES: Final[Mapping[str, str]] = {A: "10.00", B: "11.50", C: "10.00", E: "20.00", DL: "10.00"}
S3_CLOSES: Final[Mapping[str, str]] = {
    A: "10.10",
    B: "11.50",
    C: "10.10",
    E: "22.00",
    F: "10.00",
    G: "15.00",
    H: "8.00",
}
"""The facts day of S3's evening: the real closes its fixture's bars and entrant prices say. The
delisted 600040.SH has none."""
AS_OF_S2: Final[datetime] = datetime(2021, 3, 3, 18, 30, tzinfo=SHANGHAI)
AS_OF_S3: Final[datetime] = datetime(2021, 3, 4, 18, 30, tzinfo=SHANGHAI)
AS_OF_S4: Final[datetime] = datetime(2021, 3, 5, 18, 30, tzinfo=SHANGHAI)
AS_OF_S5: Final[datetime] = datetime(2021, 3, 8, 18, 30, tzinfo=SHANGHAI)


def s3_facts(closes: Mapping[str, str] = S3_CLOSES, **changes: Any) -> FactsDay:
    """S3's facts day: the book held 600010.SH, 600020.SH and 600040.SH when it was read, and
    the registry says the last was delisted."""
    arguments: dict[str, Any] = {"held": (A, B, DL), "no_row": {DL: "delisted"}}
    arguments.update(changes)
    return facts_of(S3, closes, **arguments)


# --- the evenings -------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Day:
    """One day of the daily command as a run left it: the paper book's answer, the facts day and
    screen it ran on, and the session they are of."""

    result: Any
    facts: FactsDay
    screen: ScreenResult
    session: date
    index: int
    as_of: datetime


@pytest.fixture(scope="module")
def s2(tmp_path_factory: pytest.TempPathFactory) -> Day:
    """S2's evening: 600010.SH is sold -- no longer a survivor -- and two names are bought,
    each live through ordinal 5 (2021-03-08)."""
    book = evenings.book_at(tmp_path_factory.mktemp("s2"))
    evenings.evening(book, S1, evenings.NOTHING, evenings.SCREEN_S1, evenings.PRICES_S1)
    result = evenings.evening(book, S2, evenings.MARKET_S2, evenings.SCREEN_S2, evenings.PRICES_S2)
    return Day(
        result,
        facts_of(S2, S2_CLOSES, held=(A, B, DL)),
        evenings.SCREEN_S2,
        S2,
        2,
        AS_OF_S2,
    )


@dataclasses.dataclass(frozen=True)
class WrittenOff(Day):
    """S3's evening, with the orders its settlement was due to try."""

    sell_a: Any
    buy_c: Any
    buy_e: Any


@pytest.fixture(scope="module")
def s3(tmp_path_factory: pytest.TempPathFactory) -> WrittenOff:
    """S3's evening, `test_paper_evening`'s own: 600010.SH's sale and 600030.SH's buy fill,
    600050.SH's buy is refused at its limit-up open and stays live, 600040.SH is delisted while
    held and written off, and two names are bought."""
    book = evenings.through_s2(tmp_path_factory.mktemp("s3"))
    sell_a = evenings.due_order(book, "sell", A)
    buy_c = evenings.due_order(book, "buy", C)
    buy_e = evenings.due_order(book, "buy", E)
    result = evenings.tonight(book)
    return WrittenOff(result, s3_facts(), evenings.SCREEN_S3, S3, 3, AS_OF_S3, sell_a, buy_c, buy_e)


@pytest.fixture(scope="module")
def s3_adjusted(tmp_path_factory: pytest.TempPathFactory) -> Day:
    """S3's evening with 600020.SH through a 2-for-1 adjustment and out of the survivors: its
    real close is 5.75 -- half of 11.50 -- while the ledger's 800 shares are still marked at 11.50
    in the ledger's units, so the sale the kernel plans is 800 at a reference price of 11.50
    that is not a price on any exchange's screen."""
    book = evenings.through_s2(tmp_path_factory.mktemp("s3adjusted"))
    quotes = evenings.Market(
        bars={
            A: evenings.bar(A, S3, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
            B: evenings.quiet(B, S3, "5.75"),
            C: evenings.bar(C, S3, "10.00", "10.00", "10.20", "9.95", "10.10", "11.00", "9.00"),
            E: evenings.limit_up(E, S3, "20.00"),
        },
        adj_factor={A: D("1.000"), B: D("2.000"), C: D("1.000"), E: D("1.000")},
    )
    cut = evenings.screened(
        (F, "10.00"), (G, "15.00"), (C, "10.10"), (E, "22.00"), (H, "8.00"), dropped=(B,)
    )
    result = evenings.tonight(book, quotes=quotes, screen=cut)
    return Day(result, s3_facts({**S3_CLOSES, B: "5.75"}), cut, S3, 3, AS_OF_S3)


@pytest.fixture(scope="module")
def s3_halted(tmp_path_factory: pytest.TempPathFactory) -> Day:
    """S3's evening with 600020.SH halted: no bar, a factor, carried at its S2 mark of 11.50."""
    book = evenings.through_s2(tmp_path_factory.mktemp("s3halted"))
    quotes = evenings.market(
        evenings.bar(A, S3, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
        evenings.bar(C, S3, "10.00", "10.00", "10.20", "9.95", "10.10", "11.00", "9.00"),
        evenings.limit_up(E, S3, "20.00"),
        factors=[B],
    )
    result = evenings.tonight(book, quotes=quotes)
    return Day(result, s3_facts(suspended=(B,)), evenings.SCREEN_S3, S3, 3, AS_OF_S3)


@pytest.fixture(scope="module")
def s3_cancelled(tmp_path_factory: pytest.TempPathFactory) -> WrittenOff:
    """S3's evening with 600050.SH out of the survivors: its buy is refused at the open, as
    before, and is then cancelled rather than carried."""
    book = evenings.through_s2(tmp_path_factory.mktemp("s3cancelled"))
    sell_a = evenings.due_order(book, "sell", A)
    buy_c = evenings.due_order(book, "buy", C)
    buy_e = evenings.due_order(book, "buy", E)
    cut = evenings.screened((F, "10.00"), (G, "15.00"), (B, "11.50"), (C, "10.10"), (H, "8.00"))
    result = evenings.tonight(book, screen=cut)
    closes = {code: close for code, close in S3_CLOSES.items() if code != E}
    return WrittenOff(result, s3_facts(closes), cut, S3, 3, AS_OF_S3, sell_a, buy_c, buy_e)


@pytest.fixture(scope="module")
def s3_twice(tmp_path_factory: pytest.TempPathFactory) -> tuple[Day, Day]:
    """S3's evening run, and run again on the same inputs after it was committed: the second
    writes nothing and gives the evening back, derived again, as `unchanged`."""
    book = evenings.through_s2(tmp_path_factory.mktemp("s3twice"))
    first = evenings.tonight(book)
    again = evenings.tonight(book)
    return (
        Day(first, s3_facts(), evenings.SCREEN_S3, S3, 3, AS_OF_S3),
        Day(again, s3_facts(), evenings.SCREEN_S3, S3, 3, AS_OF_S3),
    )


@dataclasses.dataclass(frozen=True)
class SettleOnly:
    """S4, missed after S3's evening and settled without a decision: the run that found it after
    09:15 of the next session settles and decides nothing."""

    caught: CatchUpResult
    facts: FactsDay
    screen: ScreenResult
    session: date
    index: int
    as_of: datetime


@pytest.fixture(scope="module")
def settle_only(tmp_path_factory: pytest.TempPathFactory) -> SettleOnly:
    """After S3's evening, 600050.SH, 600060.SH and 600070.SH have a buy each and no bar on S4:
    the three are recorded as not attempted, and nothing is planned."""
    book = evenings.through_s2(tmp_path_factory.mktemp("settleonly"))
    evenings.tonight(book)
    caught = evenings.catch_up(
        book, S4, evenings.market(evenings.quiet(B, S4, "11.50"), evenings.quiet(C, S4, "10.10"))
    )
    return SettleOnly(
        caught,
        facts_of(S4, {B: "11.50", C: "10.10"}, held=(B, C)),
        evenings.screened((B, "11.50"), (C, "10.10")),
        S4,
        4,
        AS_OF_S4,
    )


@pytest.fixture(scope="module")
def written_off_catch_up(tmp_path_factory: pytest.TempPathFactory) -> SettleOnly:
    """S3 caught up instead of run -- `test_paper_evening`'s own write-off catch-up: 600010.SH's
    sale and 600030.SH's buy fill, 600050.SH's buy is refused at the limit-up open, and 600040.SH,
    delisted while held, is written off. A settle-only day for the session itself."""
    book = evenings.through_s2(tmp_path_factory.mktemp("s3catchup"))
    caught = evenings.catch_up(book, S3, evenings.MARKET_S3, delisted={DL})
    return SettleOnly(caught, s3_facts(), evenings.SCREEN_S3, S3, 3, AS_OF_S3)


@dataclasses.dataclass(frozen=True)
class CaughtUp:
    """S5's evening after S2, S3 and S4 were missed and settled without a decision."""

    evening: Day
    caught: tuple[CatchUpResult, ...]
    buy_c: Any
    buy_e: Any


@pytest.fixture(scope="module")
def caught_up(tmp_path_factory: pytest.TempPathFactory) -> CaughtUp:
    """`test_paper_evening`'s three missed sessions: 600030.SH's buy fills on S2, 600050.SH's is
    refused at its limit-up open on S2, S3 and S4 and expires on S4 (its last session)."""
    book = evenings.book_at(tmp_path_factory.mktemp("caught"))
    evenings.evening(book, S1, evenings.NOTHING, evenings.SCREEN_C_E, {C: "10.00", E: "20.00"})
    buy_c, buy_e = evenings.due_order(book, "buy", C), evenings.due_order(book, "buy", E)
    caught = tuple(evenings.catch_up(book, day, evenings.MISSED[day]) for day in (S2, S3, S4))
    cut = evenings.screened((C, "10.00"), (E, "26.62"), (F, "10.00"), (G, "15.00"))
    result = evenings.evening(
        book,
        S5,
        evenings.market(evenings.quiet(C, S5, "10.00")),
        cut,
        {E: "26.62", F: "10.00", G: "15.00"},
    )
    facts = facts_of(S5, {C: "10.00", E: "26.62", F: "10.00", G: "15.00"}, held=(C,))
    return CaughtUp(Day(result, facts, cut, S5, 5, AS_OF_S5), caught, buy_c, buy_e)


@dataclasses.dataclass(frozen=True)
class Frame:
    """The facts-panel frame's signal day, read at 18:30, and the real kernel's screen of it."""

    facts: FactsDay
    screen: ScreenResult
    ordinals: SessionOrdinals
    index: int


@pytest.fixture(scope="module")
def frame(tmp_path_factory: pytest.TempPathFactory) -> Frame:
    """`EXTENDED`'s signal day (Friday 2026-04-10) with three holdings: a halted one, an ordinary
    one and one on a board the adapter does not recognise."""
    store = write_panel(tmp_path_factory.mktemp("frame"), EXTENDED)
    day = read_day(store, held=(UNRECOGNISED_PREFIX, HALTED, "600010.SH"))
    ordinals = SessionOrdinals(weekdays(SESSIONS[0], SESSIONS[-1]), SESSIONS[60])
    return Frame(day, screen(day.facts, FRAME_CONFIG), ordinals, ordinals.index_of(SIGNAL))


# --- building a document ------------------------------------------------------------------------


def document_of(day: Day, **changes: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "session": day.session,
        "session_index": day.index,
        "as_of": day.as_of,
        "facts": day.facts,
        "screen": day.screen,
        "evening": day.result,
        "catch_ups": (),
        "ordinals": ORDINALS,
        "book": BOOK,
    }
    arguments.update(changes)
    return evening_document(**arguments)


def settle_only_document(day: SettleOnly, **changes: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "session": day.session,
        "session_index": day.index,
        "as_of": day.as_of,
        "facts": day.facts,
        "screen": day.screen,
        "evening": None,
        "catch_ups": (day.caught,),
        "ordinals": ORDINALS,
        "book": BOOK,
        "paper_skipped": SKIPPED,
    }
    arguments.update(changes)
    return evening_document(**arguments)


def frame_document(day: Frame, **changes: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "session": SIGNAL,
        "session_index": day.index,
        "as_of": frame_evening(SIGNAL_INDEX),
        "facts": day.facts,
        "screen": day.screen,
        "evening": None,
        "catch_ups": (),
        "ordinals": day.ordinals,
        "book": FRAME_CONFIG,
        "paper_skipped": SKIPPED,
    }
    arguments.update(changes)
    return evening_document(**arguments)


def with_decision(result: EveningResult, **changes: Any) -> EveningResult:
    """`result` with its decision's fields changed: the document reads what a decision lists, and
    nothing else of the paper book depends on it once the evening is committed."""
    return dataclasses.replace(result, decision=result.decision.model_copy(update=changes))


def strings(value: Any) -> Iterator[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from strings(item)


def line_with(lines: Sequence[str], start: str) -> str:
    """The one line that begins with `start`."""
    found = [line for line in lines if line.startswith(start)]
    assert len(found) == 1, f"{len(found)} lines begin with {start!r}: {found}"
    return found[0]


SECTIONS: Final[tuple[str, ...]] = (
    "session",
    "book",
    "facts",
    "screen",
    "settled",
    "positions",
    "ORDERS FOR THE OPEN OF D+1 (call auction 09:15)",
    "carried orders",
    "unfunded",
    "unaffordable",
    "deferred exits",
    "near exit",
    "frozen",
    "watch list",
    "illiquid exits",
    "catch-up",
    "prediction",
    "tushare requests",
    "units",
    "wording",
)
"""The summary's sections, in the order they are printed."""


# --- the wording --------------------------------------------------------------------------------


def test_the_wording_is_the_registered_disclosure_and_names_candidates() -> None:
    assert SURVIVOR_WORDING == WORDING


def test_no_string_of_a_document_or_a_summary_recommends_anything(
    s3: WrittenOff, frame: Frame
) -> None:
    documents = [document_of(s3), frame_document(frame)]
    printed = [line for document in documents for line in summary_lines(document)]
    for text in (*(item for document in documents for item in strings(document)), *printed):
        lowered = text.lower()
        assert "recommend" not in lowered
        assert "signal to buy" not in lowered
        assert "buy signal" not in lowered


# --- the document -------------------------------------------------------------------------------


def test_the_document_has_its_keys_in_the_order_the_journal_files_them(s3: WrittenOff) -> None:
    document = document_of(s3)

    assert list(document) == [
        "kind",
        "session",
        "session_index",
        "as_of",
        "facts",
        "screen",
        "paper",
        "wording",
    ]
    assert document["kind"] == "survivor_book/v1"
    assert document["session"] == "2021-03-04"
    assert document["session_index"] == 3
    assert document["as_of"] == "2021-03-04T18:30:00+08:00"
    assert document["wording"] == SURVIVOR_WORDING
    assert list(document["paper"]) == [
        "trade_list",
        "decision",
        "snapshot_id",
        "settled",
        "book",
        "catch_ups",
        "outcome",
        "open_session",
        "open_orders",
        "illiquid_exits",
    ]
    assert list(document["paper"]["book"]) == [
        "equity",
        "cash",
        "gross_exposure",
        "carried_marks",
        "holdings",
    ]


def test_the_facts_section_carries_every_count_the_adapter_noted(frame: Frame) -> None:
    section = frame_document(frame)["facts"]
    notes = dataclasses.asdict(frame.facts.notes)
    notes["unrecognised_board_codes"] = list(notes["unrecognised_board_codes"])

    assert section["universe_size"] == 38
    assert section["scored_size"] == 36
    assert section["coverage"] == "0.947368"  # 36 / 38, a ratio spelled to six places
    assert section["rows"] == len(frame.facts.facts) == 43
    assert section["boards"] == ["main"]
    assert section["notes"] == notes
    assert list(section["notes"]) == sorted(notes)
    # The two counts the plan names, with the codes behind the second.
    assert section["notes"]["not_listed_with_bar"] == 1
    assert section["notes"]["unrecognised_board"] == 2
    assert section["notes"]["unrecognised_board_codes"] == ["303001.SZ", "600001.SZ"]
    assert section["held"] == ["303001.SZ", "600010.SH", "600605.SH"]
    assert section["held_status"] == [
        {"ts_code": "303001.SZ", "status": "board_unrecognised"},
        {"ts_code": "600010.SH", "status": "row"},
        {"ts_code": "600605.SH", "status": "row"},
    ]


def test_every_field_of_the_notes_is_in_the_document(frame: Frame) -> None:
    named = set(frame_document(frame)["facts"]["notes"])

    assert named == {field.name for field in dataclasses.fields(FactsNotes)}


def test_the_facts_section_says_the_five_percent_band_is_no_witness_after_2025_07_07(
    frame: Frame,
) -> None:
    witness = frame_document(frame)["facts"]["band_witness"]

    assert witness["st_by_band"] == frame.facts.notes.st_by_band == 1
    assert witness["inert_from"] == "2025-07-07"
    assert "no witness of special treatment" in witness["note"]
    assert "2025-07-07" in witness["note"]


def test_a_day_with_no_row_on_the_traded_boards_has_no_coverage_to_state(frame: Frame) -> None:
    empty = dataclasses.replace(
        frame.facts, facts=(), held=(), held_status=(), universe_size=0, scored_size=0
    )

    document = frame_document(frame, facts=empty, screen=ScreenResult(verdicts=(), survivors=()))

    section = document["facts"]
    assert section["coverage"] is None
    assert (section["universe_size"], section["scored_size"], section["rows"]) == (0, 0, 0)
    assert "coverage n/a" in line_with(summary_lines(document), "facts")


def test_the_screen_section_counts_what_the_kernel_decided(frame: Frame) -> None:
    section = frame_document(frame)["screen"]
    verdicts = frame.screen.verdicts
    named = Counter(reason for verdict in verdicts for reason in verdict.reasons)

    assert section["assessed"] == len(verdicts) == 43
    assert section["eligible"] == sum(verdict.eligible for verdict in verdicts) == 21
    assert section["survivors"] == len(frame.screen.survivors) == 17
    assert section["reasons"] == {
        "board_not_allowed": 5,
        "illiquid": 9,
        "listing_too_recent": 3,
        "missing_factor_value": 1,
        "special_treatment": 4,
        "suspended": 2,
    }
    assert section["reasons"] == {reason: named[reason] for reason in sorted(named)}
    assert list(section["reasons"]) == sorted(section["reasons"])


def test_the_screen_section_names_every_reason_even_when_none_has_a_verdict(
    s3: WrittenOff,
) -> None:
    assert document_of(s3)["screen"] == {
        "assessed": 6,
        "eligible": 6,
        "survivors": 6,
        "reasons": {
            "board_not_allowed": 0,
            "illiquid": 0,
            "listing_too_recent": 0,
            "missing_factor_value": 0,
            "special_treatment": 0,
            "suspended": 0,
        },
    }


def test_the_paper_section_files_the_plan_the_decision_and_the_snapshot(s3: WrittenOff) -> None:
    paper = document_of(s3)["paper"]
    result = s3.result

    assert paper["trade_list"] == result.trade_list.model_dump(mode="json")
    assert paper["trade_list"]["trade_list_id"] == result.trade_list.trade_list_id
    assert paper["trade_list"]["unfunded"] == []
    assert [item["order_id"] for item in paper["trade_list"]["orders"]] == [
        order.order_id for order in result.trade_list.orders
    ]
    assert [item["order_id"] for item in paper["trade_list"]["carried"]] == [s3.buy_e.order_id]
    assert paper["decision"] == result.decision.model_dump(mode="json")
    assert paper["snapshot_id"] == result.snapshot.snapshot_id
    assert paper["outcome"] == "created"
    assert paper["catch_ups"] == []


def test_the_unfunded_names_are_the_trade_lists_own(s3: WrittenOff) -> None:
    plan = s3.result.trade_list.model_copy(update={"unfunded": ("600099.SH", "600098.SH")})
    result = dataclasses.replace(s3.result, trade_list=plan)

    paper = document_of(s3, evening=result)["paper"]

    assert paper["trade_list"]["unfunded"] == ["600099.SH", "600098.SH"]
    assert paper["trade_list"]["trade_list_id"] == plan.trade_list_id
    assert plan.trade_list_id != s3.result.trade_list.trade_list_id


def test_a_rerun_of_a_settled_evening_is_the_same_document_marked_unchanged(
    s3_twice: tuple[Day, Day],
) -> None:
    first, again = s3_twice
    assert (first.result.outcome, again.result.outcome) == ("created", "unchanged")

    created, unchanged = document_of(first), document_of(again)

    assert created["paper"]["outcome"] == "created"
    assert unchanged["paper"]["outcome"] == "unchanged"
    # Nothing else of the document moves: the evening derived again is the evening committed.
    assert {**unchanged, "paper": {**unchanged["paper"], "outcome": "created"}} == created


def test_the_settled_section_names_what_filled_expired_cancelled_and_every_attempt(
    s3: WrittenOff,
) -> None:
    settled = document_of(s3)["paper"]["settled"]

    assert sorted(settled["filled"]) == sorted([s3.sell_a.order_id, s3.buy_c.order_id])
    assert settled["expired"] == []
    assert settled["cancelled"] == []
    assert settled["orders"][s3.sell_a.order_id] == {"ts_code": A, "side": "sell", "quantity": 900}
    assert [
        (item["ts_code"], item["side"], item["quantity"], item["status"], item["reason"])
        for item in settled["attempts"]
    ] == [
        (A, "sell", 900, "filled", None),
        (C, "buy", 900, "filled", None),
        (E, "buy", 400, "rejected", LIMIT_UP_REFUSAL),
    ]
    assert [item["order_id"] for item in settled["attempts"]] == [
        s3.sell_a.order_id,
        s3.buy_c.order_id,
        s3.buy_e.order_id,
    ]
    assert [item["attempt_id"] for item in settled["attempts"]] == [
        f"{item['order_id']}@2021-03-04" for item in settled["attempts"]
    ]
    assert list(settled["orders"]) == sorted(settled["orders"])


def test_a_buy_the_screen_dropped_is_listed_as_cancelled(s3_cancelled: WrittenOff) -> None:
    settled = document_of(s3_cancelled)["paper"]["settled"]

    assert settled["cancelled"] == [s3_cancelled.buy_e.order_id]
    assert settled["orders"][s3_cancelled.buy_e.order_id] == {
        "ts_code": E,
        "side": "buy",
        "quantity": 400,
    }
    assert f"cancelled {E} buy 400" in "\n".join(summary_lines(document_of(s3_cancelled)))


def test_a_write_off_names_the_code_and_its_whole_cost_basis_and_no_price(
    s3: WrittenOff,
) -> None:
    settled = document_of(s3)["paper"]["settled"]

    assert settled["written_off"] == [
        {
            "ts_code": DL,
            "order_id": f"delisting_write_off:{DL}@2021-03-04",
            "ledger_quantity": 900,
            "cost_basis": "9005.09",  # = -realized_pnl_delta; cash is unchanged
        }
    ]
    (written,) = s3.result.settlement.written_off
    assert D(settled["written_off"][0]["cost_basis"]) == -written.transition.realized_pnl_delta
    assert written.transition.execution is None


def test_a_catch_ups_settled_section_is_the_sessions_own(caught_up: CaughtUp) -> None:
    last = caught_up.caught[2]  # S4: 600050.SH's last session, refused again and expired

    day = document_of(caught_up.evening, catch_ups=(last,))["paper"]["catch_ups"][0]

    assert [(a["ts_code"], a["status"], a["reason"]) for a in day["settled"]["attempts"]] == [
        (E, "rejected", LIMIT_UP_REFUSAL)
    ]
    assert day["settled"]["expired"] == [caught_up.buy_e.order_id]
    assert day["settled"]["written_off"] == []


def test_an_order_with_no_bar_is_recorded_as_not_attempted_with_the_steps_reason(
    settle_only: SettleOnly,
) -> None:
    settled = settle_only_document(settle_only)["paper_skipped"]["catch_ups"][0]["settled"]

    assert [(a["ts_code"], a["side"], a["status"], a["reason"]) for a in settled["attempts"]] == [
        (E, "buy", "not_attempted", NO_BAR_REASON),
        (F, "buy", "not_attempted", NO_BAR_REASON),
        (G, "buy", "not_attempted", NO_BAR_REASON),
    ]
    assert settled["filled"] == [] and settled["cancelled"] == [] and settled["expired"] == []


def test_a_catch_ups_equity_is_the_settlements_never_the_runners_record(
    written_off_catch_up: SettleOnly,
) -> None:
    settlement = written_off_catch_up.caught.settlement
    record = settlement.runner_record
    # On a write-off day the runner's record is from before the write-off and still holds 600040.SH
    # at its mark of 10.00 (900 shares): its equity overstates the session by the whole of it.
    assert record.equity == D("99970.05")
    assert settlement.equity == D("91060.05")
    document = settle_only_document(written_off_catch_up)

    (day,) = document["paper_skipped"]["catch_ups"]

    assert (day["session"], day["session_index"]) == ("2021-03-04", 3)
    assert day["equity"] == "91060.05"
    assert day["equity"] != str(record.equity)
    assert day["cash"] == "72770.05"
    assert day["settled"]["written_off"][0]["cost_basis"] == "9005.09"


def test_a_printed_catch_up_line_carries_the_settlements_equity(
    written_off_catch_up: SettleOnly,
) -> None:
    lines = summary_lines(settle_only_document(written_off_catch_up))

    line = next(line for line in lines if line.startswith("  2021-03-04 (index 3):"))
    assert line.endswith("written off 1; equity 91060.05")
    assert "99970.05" not in "\n".join(lines)


def test_the_book_comes_from_the_settlement_never_from_the_runners_record(
    s3: WrittenOff,
) -> None:
    book = document_of(s3)["paper"]["book"]
    settlement = s3.result.settlement
    record = settlement.runner_record

    # The runner's record is the trace from before the close re-mark and the write-off: it
    # still holds 600040.SH at its mark of 10.00 (900 shares), and its equity says so.
    assert record.equity == D("99970.05")
    assert [mark.subject for mark in record.carried_marks] == [DL]
    # The session's own figures are the settlement's.
    assert book["equity"] == "91060.05" == str(settlement.equity)
    assert book["equity"] != str(record.equity)
    assert book["cash"] == "72770.05"
    market_value = D("800") * D("11.50") + D("900") * D("10.10")
    assert book["gross_exposure"] == str((market_value / D("91060.05")).quantize(D("0.000001")))
    assert book["gross_exposure"] == str(settlement.gross_exposure)
    assert book["carried_marks"] == []  # the written-off name is not a carried mark


def test_a_halted_holding_is_a_carried_mark_at_its_last_price_and_not_a_halt_length(
    s3_halted: Day,
) -> None:
    book = document_of(s3_halted)["paper"]["book"]

    assert book["carried_marks"] == [{"ts_code": B, "trade_date": "2021-03-04", "price": "11.50"}]
    # `CarriedMark.sessions_carried` is 1 on every run, so the document does not carry it.
    assert "sessions_carried" not in json.dumps(book)
    assert [holding["ts_code"] for holding in book["holdings"]] == [B, C]
    halted = book["holdings"][0]
    assert halted["flags"] == ["carried"]
    assert halted["carried_since"] is None
    assert halted["halted"] is True


def test_a_halt_that_began_earlier_is_recorded_only_when_the_caller_says_when(
    s3_halted: Day,
) -> None:
    said = document_of(s3_halted, carried_since={B: date(2021, 2, 25), C: date(2020, 1, 2)})
    held = said["paper"]["book"]["holdings"]

    assert held[0]["carried_since"] == "2021-02-25"
    # A name that is not carried tonight has no halt to date: the entry is not recorded.
    assert held[1]["carried_since"] is None
    assert "2020-01-02" not in json.dumps(said)


def test_the_holdings_are_the_settled_book_with_the_real_close_of_the_facts(
    s3: WrittenOff,
) -> None:
    holdings = document_of(s3)["paper"]["book"]["holdings"]

    assert holdings == [
        {
            "ts_code": B,
            "ledger_quantity": 800,
            "real_close": "11.50",
            "halted": False,
            "flags": [],
            "carried_since": None,
        },
        {
            "ts_code": C,
            "ledger_quantity": 900,
            "real_close": "10.10",
            "halted": False,
            "flags": [],
            "carried_since": None,
        },
    ]


def test_a_holding_the_decision_froze_or_calls_near_exit_says_so(s3: WrittenOff) -> None:
    result = with_decision(s3.result, frozen=(B,), near_exit=(B, C))

    holdings = document_of(s3, evening=result)["paper"]["book"]["holdings"]

    assert [(item["ts_code"], item["flags"]) for item in holdings] == [
        (B, ["frozen", "near_exit"]),
        (C, ["near_exit"]),
    ]


def test_the_orders_for_the_open_carry_the_real_close_and_the_last_session_as_a_date(
    s2: Day,
) -> None:
    paper = document_of(s2)["paper"]
    orders = paper["open_orders"]

    assert paper["open_session"] == {"index": 3, "date": "2021-03-04"}
    assert [(o["side"], o["ts_code"], o["quantity"], o["reason"]) for o in orders] == [
        ("sell", A, 900, "EXIT_SURVIVOR_LOST"),
        ("buy", C, 900, "ENTRY_REPLACEMENT"),
        ("buy", E, 400, "ENTRY_REPLACEMENT"),
    ]
    sell, buy_c, buy_e = orders
    assert [o["execute_session"] for o in orders] == [3, 3, 3]
    assert [o["execute_on"] for o in orders] == ["2021-03-04"] * 3
    assert sell["real_close"] == "10.00"
    assert sell["expires_session"] is None and sell["expires_on"] is None
    assert (buy_c["real_close"], buy_e["real_close"]) == ("10.00", "20.00")
    assert (buy_c["expires_session"], buy_c["expires_on"]) == (5, "2021-03-08")
    assert [o["carried"] for o in orders] == [False, False, False]
    assert [o["order_id"] for o in orders] == [o.order_id for o in s2.result.trade_list.orders]


def test_an_orders_last_session_beyond_the_calendar_is_the_ordinal(s2: Day) -> None:
    # The calendar ends on S3, ordinal 3: the buys' last session, ordinal 5, has no date yet.
    paper = document_of(s2, ordinals=SHORT_ORDINALS)["paper"]
    orders = paper["open_orders"]

    assert (orders[1]["expires_session"], orders[1]["expires_on"]) == (5, None)
    assert orders[1]["execute_on"] == "2021-03-04"  # ordinal 3 is the calendar's last day
    assert paper["open_session"] == {"index": 3, "date": "2021-03-04"}


def test_an_execution_day_beyond_the_calendar_is_the_ordinal_too(caught_up: CaughtUp) -> None:
    # S5's evening plans for ordinal 6; the calendar ends on ordinal 3.
    paper = document_of(caught_up.evening, ordinals=SHORT_ORDINALS)["paper"]

    assert paper["open_session"] == {"index": 6, "date": None}
    assert [(o["execute_session"], o["execute_on"]) for o in paper["open_orders"]] == [(6, None)]


def test_a_sale_of_a_name_through_an_adjustment_is_whole_and_priced_at_the_real_close(
    s3_adjusted: Day,
) -> None:
    orders = document_of(s3_adjusted)["paper"]["open_orders"]

    (sell,) = (o for o in orders if o["side"] == "sell")
    assert sell["ts_code"] == B
    assert sell["quantity"] == 800  # the ledger's share count, in the ledger's units
    assert sell["reference_price"] == "11.50"  # the ledger's mark: no price on any screen
    assert sell["real_close"] == "5.75"  # the facts day's
    assert sell["reasons"] == []  # still eligible: only its survivor place was lost


def test_a_sale_names_the_reasons_the_screen_gave_for_the_loss(s3: WrittenOff) -> None:
    verdict = EligibilityVerdict(ts_code=B, reasons=("special_treatment", "suspended"))
    kept = evenings.screened((F, "10.00"), (G, "15.00"), (C, "10.10"), (E, "22.00"), (H, "8.00"))
    cut = ScreenResult(
        verdicts=tuple(sorted((*kept.verdicts, verdict), key=lambda v: v.ts_code)),
        survivors=kept.survivors,
    )
    sale = s3.result.trade_list.orders[0].model_copy(
        update={
            "side": "sell",
            "ts_code": B,
            "quantity": 800,
            "reason": "EXIT_ELIGIBILITY_LOST",
            "expires_session": None,
        }
    )
    plan = s3.result.trade_list.model_copy(update={"orders": (sale,)})
    result = dataclasses.replace(s3.result, trade_list=plan)

    document = document_of(s3, evening=result, screen=cut)

    (order,) = document["paper"]["open_orders"][:1]
    assert order["reasons"] == ["special_treatment", "suspended"]
    assert (
        f"  SELL ENTIRE POSITION {B} (ledger qty 800; real close 11.50) EXIT_ELIGIBILITY_LOST "
        "(special_treatment, suspended)"
    ) in summary_lines(document)


def test_a_carried_order_says_which_attempt_tomorrows_open_will_be(s3: WrittenOff) -> None:
    orders = document_of(s3)["paper"]["open_orders"]
    live = {item.order.order_id: item for item in s3.result.settlement.live}

    assert [(o["side"], o["ts_code"], o["carried"]) for o in orders] == [
        ("buy", F, False),
        ("buy", G, False),
        ("buy", E, True),
    ]
    carried = orders[-1]
    assert live[s3.buy_e.order_id].attempts == 1  # tried once, tonight, and refused
    assert carried["order_id"] == s3.buy_e.order_id
    assert carried["signal_session"] == 2
    assert (carried["attempt"], carried["attempts_allowed"]) == (2, 3)
    assert (carried["expires_session"], carried["expires_on"]) == (5, "2021-03-08")
    assert carried["reference_price"] == "20.00"  # what the order was sized at, on S2
    assert carried["real_close"] == "22.00"  # what the facts day says tonight
    assert [o["attempt"] for o in orders[:2]] == [None, None]
    assert [o["attempts_allowed"] for o in orders[:2]] == [3, 3]


def test_a_sale_carried_from_an_earlier_session_is_still_the_whole_position(
    s3: WrittenOff,
) -> None:
    """A sale refused at its limit-down open stays live: carried, not re-planned, never expiring."""
    result = s3.result
    live = (*result.settlement.live, PendingOrder(order=s3.sell_a, attempts=1))
    kept = dataclasses.replace(result.settlement, live=live)
    plan = result.trade_list.model_copy(update={"carried": (*result.trade_list.carried, s3.sell_a)})
    carried = dataclasses.replace(result, settlement=kept, trade_list=plan)

    document = document_of(s3, evening=carried)
    lines = summary_lines(document)

    sale = next(o for o in document["paper"]["open_orders"] if o["side"] == "sell")
    assert (sale["carried"], sale["attempt"], sale["attempts_allowed"]) == (True, 2, None)
    assert (sale["expires_session"], sale["expires_on"]) == (None, None)
    assert "2 live orders from earlier sessions" in line_with(lines, "carried orders")
    assert (
        f"  SELL ENTIRE POSITION {A} (ledger qty 900; real close 10.10) EXIT_SURVIVOR_LOST, "
        "planned on session 2, attempt 2, a sale never expires"
    ) in lines


# --- the illiquid rank --------------------------------------------------------------------------


def population_of(day: Frame) -> list[str]:
    """The names the liquidity cut is taken over -- those with no reason but, perhaps, `illiquid`
    -- thinnest first, ties by code: worked out here from the facts and the verdicts."""
    amount = {item.ts_code: item.avg_amount for item in day.facts.facts}
    names = [v.ts_code for v in day.screen.verdicts if set(v.reasons) <= {"illiquid"}]
    return sorted(names, key=lambda code: (amount[code], code))


def test_the_rank_is_the_place_among_the_names_that_passed_every_static_reason(
    frame: Frame,
) -> None:
    population = population_of(frame)
    illiquid = [v.ts_code for v in frame.screen.verdicts if "illiquid" in v.reasons]

    assert len(population) == 30
    ranks = {code: illiquid_rank(frame.facts, frame.screen, code) for code in population}
    assert [ranks[code] for code in population] == [(place, 30) for place in range(1, 31)]
    # The kernel drops the thinnest floor(30 x 0.30) = 9, which are exactly ranks 1 .. 9.
    assert int(D(30) * FRAME_CONFIG.liquidity_drop_fraction) == 9
    assert sorted(ranks[code][0] for code in illiquid) == list(range(1, 10))


def test_a_rank_breaks_a_tie_in_amount_by_code_as_the_screen_does(frame: Frame) -> None:
    first, second = sorted(population_of(frame))[:2]
    tied = tuple(
        item.model_copy(update={"avg_amount": D("1")}) if item.ts_code in {first, second} else item
        for item in frame.facts.facts
    )
    facts = dataclasses.replace(frame.facts, facts=tied, held=(), held_status=())
    again = screen(facts.facts, FRAME_CONFIG)

    assert illiquid_rank(facts, again, first) == (1, 30)
    assert illiquid_rank(facts, again, second) == (2, 30)


def test_a_code_outside_the_liquidity_population_has_no_rank(frame: Frame) -> None:
    static = next(v.ts_code for v in frame.screen.verdicts if "suspended" in v.reasons)

    with pytest.raises(ValueError, match=re.escape(static)):
        illiquid_rank(frame.facts, frame.screen, static)
    with pytest.raises(ValueError, match=re.escape("999999.SH")):
        illiquid_rank(frame.facts, frame.screen, "999999.SH")


def test_a_screen_that_is_not_the_facts_days_has_no_rank_to_give() -> None:
    # SCREEN_S1 assesses 600010.SH, 600020.SH and 600040.SH; the facts day has one row.
    other = facts_of(S3, {A: "10.00"})

    with pytest.raises(ValueError, match=re.escape(B)):
        illiquid_rank(other, evenings.SCREEN_S1, A)


@dataclasses.dataclass(frozen=True)
class Illiquid:
    """An evening whose decision exits a name the frame's screen found illiquid."""

    document: dict[str, Any]
    code: str
    rank: int
    population: int


@pytest.fixture(scope="module")
def illiquid(frame: Frame, caught_up: CaughtUp) -> Illiquid:
    """S5's evening, its decision swapped for one that exits the thinnest illiquid name of the
    frame's day: the document reads the decision's exits, the screen's verdicts and the facts'
    amounts, and nothing of the paper book decides which exit is illiquid."""
    thin = next(v.ts_code for v in frame.screen.verdicts if v.reasons == ("illiquid",))
    exits = (ExitDecision(ts_code=thin, reason="EXIT_ELIGIBILITY_LOST"),)
    day = caught_up.evening
    # The frame's rows and amounts, filed under S5: a facts day is of the session it is filed under.
    facts = dataclasses.replace(frame.facts, session=day.session)
    document = evening_document(
        session=day.session,
        session_index=day.index,
        as_of=day.as_of,
        facts=facts,
        screen=frame.screen,
        evening=with_decision(day.result, exits=exits),
        catch_ups=(),
        ordinals=ORDINALS,
        book=BOOK,
    )
    rank, size = illiquid_rank(frame.facts, frame.screen, thin)
    return Illiquid(document, thin, rank, size)


def test_every_illiquid_exit_names_its_amount_rank_in_the_liquidity_population(
    illiquid: Illiquid,
) -> None:
    assert illiquid.document["paper"]["illiquid_exits"] == [
        {
            "ts_code": illiquid.code,
            "rank": illiquid.rank,
            "population": illiquid.population,
            "cut": 9,
        }
    ]
    assert illiquid.population == 30


def test_an_exit_that_is_not_illiquid_has_no_rank_to_report(s3_adjusted: Day) -> None:
    assert document_of(s3_adjusted)["paper"]["illiquid_exits"] == []


# --- the day that ran no evening, and the day that caught up -----------------------------------


def test_a_day_with_no_evening_has_a_null_paper_section_and_the_reason(frame: Frame) -> None:
    document = frame_document(frame)

    assert document["paper"] is None
    assert document["paper_skipped"] == {"reason": SKIPPED, "catch_ups": []}
    assert list(document)[-3:] == ["paper", "paper_skipped", "wording"]
    assert document["facts"]["universe_size"] == 38
    assert document["screen"]["survivors"] == 17


def test_a_day_with_no_evening_and_no_reason_says_so(frame: Frame) -> None:
    document = frame_document(frame, paper_skipped=None)

    assert document["paper"] is None
    assert document["paper_skipped"] == {"reason": None, "catch_ups": []}


def test_a_settle_only_day_keeps_the_sessions_it_settled(settle_only: SettleOnly) -> None:
    document = settle_only_document(settle_only)

    assert document["paper"] is None
    skipped = document["paper_skipped"]
    assert skipped["reason"] == SKIPPED
    assert [(c["session"], c["session_index"], c["outcome"]) for c in skipped["catch_ups"]] == [
        ("2021-03-05", 4, "created")
    ]
    assert skipped["catch_ups"][0]["snapshot_id"] == settle_only.caught.snapshot.snapshot_id


def test_a_catch_up_day_records_each_session_it_settled_and_what_it_settled(
    caught_up: CaughtUp,
) -> None:
    document = document_of(caught_up.evening, catch_ups=caught_up.caught)
    days = document["paper"]["catch_ups"]

    assert [(d["session"], d["session_index"], d["outcome"]) for d in days] == [
        ("2021-03-03", 2, "created"),
        ("2021-03-04", 3, "created"),
        ("2021-03-05", 4, "created"),
    ]
    assert [d["snapshot_id"] for d in days] == [c.snapshot.snapshot_id for c in caught_up.caught]
    first, second, third = (d["settled"] for d in days)
    assert first["filled"] == [caught_up.buy_c.order_id]
    assert [(a["ts_code"], a["status"]) for a in first["attempts"]] == [
        (C, "filled"),
        (E, "rejected"),
    ]
    assert (second["filled"], second["expired"]) == ([], [])
    assert third["expired"] == [caught_up.buy_e.order_id]
    assert third["orders"][caught_up.buy_e.order_id] == {
        "ts_code": E,
        "side": "buy",
        "quantity": 400,
    }
    assert [d["equity"] for d in days] == [str(c.settlement.equity) for c in caught_up.caught]
    assert days[0]["cash"] == "90994.91"


def test_the_sessions_a_document_describes_are_the_ones_its_arguments_name(
    s3: WrittenOff, caught_up: CaughtUp
) -> None:
    with pytest.raises(ValueError, match="session"):
        document_of(s3, session=S2)
    with pytest.raises(ValueError, match="session index"):
        document_of(s3, session_index=4)
    with pytest.raises(ValueError, match="paper_skipped"):
        document_of(s3, paper_skipped="not needed")
    with pytest.raises(ValueError, match="oldest first"):
        document_of(caught_up.evening, catch_ups=tuple(reversed(caught_up.caught)))
    with pytest.raises(ValueError, match="the facts day is of session"):
        document_of(s3, facts=facts_of(S2, S2_CLOSES))
    # Settled sessions 2 .. 4 of an evening on 3: 4 is after it, and 3 is the evening's own.
    with pytest.raises(ValueError, match="after the session"):
        document_of(s3, catch_ups=(caught_up.caught[2],))
    with pytest.raises(ValueError, match="the evening settles"):
        document_of(s3, catch_ups=caught_up.caught[:2])


# --- the document is JSON, and the same for the same inputs -----------------------------------


def test_the_document_survives_a_round_trip_through_json_unchanged(
    s3: WrittenOff, caught_up: CaughtUp, s2: Day, frame: Frame, settle_only: SettleOnly
) -> None:
    documents = [
        document_of(s3),
        document_of(s2),
        document_of(caught_up.evening, catch_ups=caught_up.caught),
        frame_document(frame),
        settle_only_document(settle_only),
    ]
    for document in documents:
        text = json.dumps(document, sort_keys=False)
        assert json.loads(text) == document
        assert json.dumps(json.loads(text), sort_keys=False) == text  # key order survives too


def test_the_same_inputs_give_the_same_bytes(s3: WrittenOff) -> None:
    first = json.dumps(document_of(s3), sort_keys=False)
    second = json.dumps(document_of(s3), sort_keys=False)

    assert first == second
    assert summary_lines(json.loads(first)) == summary_lines(json.loads(second))


def day_text(directory: str) -> str:
    """The subprocess half of the hash-seed test: S3's evening off a fresh book in `directory`,
    its document and its printed lines, as one string."""
    book = evenings.through_s2(Path(directory))
    result = evenings.tonight(book)
    day = Day(result, s3_facts(), evenings.SCREEN_S3, S3, 3, AS_OF_S3)
    document = document_of(day, carried_since={B: date(2021, 2, 25)})
    return json.dumps({"document": document, "lines": summary_lines(document)}, sort_keys=False)


def test_processes_with_different_hash_seeds_build_the_same_document_and_lines(
    tmp_path: Path,
) -> None:
    here = Path(__file__).resolve()
    program = (
        "import hashlib, importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('survivor_summary', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec);"
        "sys.modules['survivor_summary'] = module;"
        "spec.loader.exec_module(module);"
        "print(hashlib.sha256(module.day_text(sys.argv[2]).encode()).hexdigest())"
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
    reference = hashlib.sha256(day_text(str(tmp_path / "here")).encode()).hexdigest()

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
    tree = ast.parse(inspect.getsource(strategy_survivor_summary))
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
        {"os", "pathlib", "sqlite3", "socket", "subprocess", "time", "urllib", "http", "shutil"}
    )
    assert called.isdisjoint({"open", "now", "today", "utcnow", "read_text", "write_text", "mkdir"})


# --- the summary --------------------------------------------------------------------------------


def test_every_section_of_the_summary_is_printed_in_its_place(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))

    firsts = []
    for label in SECTIONS:
        starting = [i for i, line in enumerate(lines) if line.startswith(label)]
        assert starting, f"no line begins with {label!r}"
        firsts.append(starting[0])
    assert firsts == sorted(firsts)
    assert len(set(firsts)) == len(SECTIONS)


def test_the_session_and_book_lines_say_what_the_ledger_is_not(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))

    assert line_with(lines, "session") == row(
        "session", "2021-03-04 (index 3; as of 2021-03-04T18:30:00+08:00)"
    )
    book = line_with(lines, "book")
    assert "equity 91060.05" in book
    assert "cash 72770.05" in book
    assert "gross exposure 0.200856" in book
    assert "2 holdings" in book
    assert "ledger equity is not evidence" in book
    assert "99970.05" not in "\n".join(lines)  # the runner's pre-write-off equity is nowhere


def test_the_facts_lines_state_every_count_and_name_the_unrecognised_codes(frame: Frame) -> None:
    lines = summary_lines(frame_document(frame))
    text = "\n".join(lines)

    assert "rows 43, universe on main 38, scored 36 (coverage 0.947368)" in line_with(
        lines, "facts"
    )
    assert "held 3" in line_with(lines, "facts")
    assert "no_bar_within_carry 1" in text
    assert "not_listed_with_bar 1" in text
    assert "unrecognised_board 2 [303001.SZ, 600001.SZ]" in text
    assert "ST 3 (by name 1, pending 1, by band 1)" in text
    assert "name unknown -> ordinary 25 (never renamed 24, before first record 1)" in text
    assert "303001.SZ board_unrecognised" in text
    # One band in the synthetic frame: the band is answering there, so nothing says it is not.
    assert BAND_WITNESS_ENDED not in text


def test_a_zero_band_count_after_2025_07_07_says_the_band_no_longer_witnesses(
    frame: Frame,
) -> None:
    notes = dataclasses.replace(frame.facts.notes, st=2, st_by_band=0)
    day = dataclasses.replace(frame.facts, notes=notes)

    lines = summary_lines(frame_document(frame, facts=day))

    inert = [line for line in lines if line.strip().startswith(BAND_WITNESS_ENDED)]
    assert len(inert) == 1
    assert "ST 2 (by name 1, pending 1, by band 0)" in "\n".join(lines)


def test_a_zero_band_count_before_2025_07_07_is_just_a_count(s3: WrittenOff) -> None:
    text = "\n".join(summary_lines(document_of(s3)))  # S3 is 2021-03-04: the band still spoke

    assert "ST 0 (by name 0, pending 0, by band 0)" in text
    assert BAND_WITNESS_ENDED not in text


def test_the_screen_lines_count_the_reasons(frame: Frame) -> None:
    lines = summary_lines(frame_document(frame))

    assert "assessed 43, eligible 21, survivors 17" in line_with(lines, "screen")
    reasons = next(line for line in lines if line.strip().startswith("reasons"))
    assert (
        "board_not_allowed 5, illiquid 9, listing_too_recent 3, missing_factor_value 1, "
        "special_treatment 4, suspended 2" in reasons
    )


def test_the_settled_lines_count_and_explain_each_outcome(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))
    settled = line_with(lines, "settled")

    assert "filled 2, expired 0, cancelled 0, rejected 1, not attempted 0, written off 1" in settled
    text = "\n".join(lines)
    assert f"{A} sell (ledger qty 900): filled" in text
    assert f"{C} buy 900: filled" in text
    assert f"{E} buy 400: rejected -- {LIMIT_UP_REFUSAL}" in text
    assert f"{DL} written off (ledger qty 900): whole cost basis 9005.09, no price" in text


def test_an_order_with_no_bar_is_listed_as_not_attempted_with_the_reason(
    settle_only: SettleOnly,
) -> None:
    lines = summary_lines(settle_only_document(settle_only))
    text = "\n".join(lines)

    assert f"{E} buy 400: not attempted -- {NO_BAR_REASON}" in text
    assert f"{G} buy 600: not attempted -- {NO_BAR_REASON}" in text
    assert "not attempted 3" in text


def test_the_settled_lines_name_the_orders_that_expired(caught_up: CaughtUp) -> None:
    lines = summary_lines(document_of(caught_up.evening, catch_ups=caught_up.caught))

    assert f"expired {E} buy 400" in "\n".join(lines)


def test_the_positions_print_the_ledger_quantity_beside_the_real_close(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))

    assert "2 holdings" in line_with(lines, "positions")
    assert f"  {B}  ledger qty 800  real close 11.50" in lines
    assert f"  {C}  ledger qty 900  real close 10.10" in lines


def test_a_holding_says_whether_it_is_frozen_near_exit_or_carried(s3: WrittenOff) -> None:
    result = with_decision(s3.result, frozen=(B,), near_exit=(B, C))

    lines = summary_lines(document_of(s3, evening=result))

    assert f"  {B}  ledger qty 800  real close 11.50  frozen; near exit" in lines
    assert f"  {C}  ledger qty 900  real close 10.10  near exit" in lines


def test_a_carried_holding_says_since_when_only_if_the_caller_supplied_it(s3_halted: Day) -> None:
    unsaid = summary_lines(document_of(s3_halted))
    said = summary_lines(document_of(s3_halted, carried_since={B: date(2021, 2, 25)}))
    halted = f"  {B}  ledger qty 800  real close 11.50 {SUSPENDED_CLOSE}"

    assert f"{halted}  no bar tonight" in unsaid
    assert "carried since" not in "\n".join(unsaid)
    assert f"{halted}  carried since 2021-02-25" in said


def test_the_orders_for_the_open_name_the_day_and_the_call_auction(s2: Day) -> None:
    lines = summary_lines(document_of(s2))

    assert "ORDERS FOR THE OPEN OF D+1 (call auction 09:15), 2021-03-04" in lines


def test_the_open_the_orders_are_for_is_an_ordinal_when_the_calendar_has_not_reached_it(
    s2: Day,
) -> None:
    lines = summary_lines(document_of(s2, ordinals=ENDS_ON_S2))

    assert "ORDERS FOR THE OPEN OF D+1 (call auction 09:15), session 3" in lines


def test_a_sale_is_the_whole_position_with_the_ledger_quantity_beside_the_real_close(
    s2: Day,
) -> None:
    lines = summary_lines(document_of(s2))

    assert (
        f"  SELL ENTIRE POSITION {A} (ledger qty 900; real close 10.00) EXIT_SURVIVOR_LOST" in lines
    )


def test_a_sale_through_an_adjustment_prints_the_real_close_and_no_share_count_to_sell(
    s3_adjusted: Day,
) -> None:
    lines = summary_lines(document_of(s3_adjusted))

    sales = [line for line in lines if line.strip().startswith("SELL")]
    assert sales == [
        f"  SELL ENTIRE POSITION {B} (ledger qty 800; real close 5.75) EXIT_SURVIVOR_LOST"
    ]
    # The ledger's mark of 11.50 is no price anyone can place an order at, and 800 is not the
    # account's share count after the adjustment (it holds 800 x the ratio): neither is an
    # instruction, and the only 800 on the sale's line is the ledger's own, labelled.
    assert "11.50" not in sales[0]
    pattern = re.compile(r"^\s*SELL ENTIRE POSITION \S+ \(ledger qty \d+; real close [\d.]+\) ")
    assert all(pattern.match(line) for line in sales)
    assert not any(re.match(r"^\s*SELL\s+(\S+\s+)?\d", line) for line in lines)
    assert not any(re.search(r"SELL .*@", line) for line in lines)


def test_a_buy_names_the_quantity_the_real_close_and_the_date_it_is_live_through(s2: Day) -> None:
    lines = summary_lines(document_of(s2))

    assert f"  BUY {C} 900 @ real close 10.00 ENTRY_REPLACEMENT, live through 2021-03-08" in lines
    assert f"  BUY {E} 400 @ real close 20.00 ENTRY_REPLACEMENT, live through 2021-03-08" in lines


def test_a_buy_whose_last_session_is_past_the_calendar_says_the_ordinal(s2: Day) -> None:
    lines = summary_lines(document_of(s2, ordinals=SHORT_ORDINALS))

    assert f"  BUY {C} 900 @ real close 10.00 ENTRY_REPLACEMENT, live through session 5" in lines
    assert "ORDERS FOR THE OPEN OF D+1 (call auction 09:15), 2021-03-04" in lines


def test_a_carried_order_is_listed_with_the_attempt_it_is_on(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))

    assert "1 live order from earlier sessions" in line_with(lines, "carried orders")
    assert (
        f"  BUY {E} 400 (planned on session 2 at 20.00; real close today 22.00) "
        "ENTRY_REPLACEMENT, live through 2021-03-08, attempt 2 of 3"
    ) in lines
    # A carried order is not one of tonight's: tonight's two buys are listed above it.
    tonight = next(i for i, line in enumerate(lines) if f"BUY {F} 900" in line)
    assert tonight < lines.index(line_with(lines, "carried orders"))


def test_an_evening_that_plans_nothing_says_so_under_the_header(s3_halted: Day) -> None:
    quiet = dataclasses.replace(
        s3_halted.result, trade_list=s3_halted.result.trade_list.model_copy(update={"orders": ()})
    )

    lines = summary_lines(document_of(s3_halted, evening=quiet))

    header = lines.index("ORDERS FOR THE OPEN OF D+1 (call auction 09:15), 2021-03-05")
    assert lines[header + 1] == "  no new order planned tonight"


def test_the_names_the_draw_left_out_are_listed_with_the_draws_caveat(s3: WrittenOff) -> None:
    result = with_decision(
        s3.result,
        near_exit=(C,),
        frozen=(B,),
        watch_list=(H, G),
        unaffordable=("600088.SH",),
        deferred_exits=("600077.SH",),
    )
    plan = result.trade_list.model_copy(update={"unfunded": ("600099.SH",)})
    result = dataclasses.replace(result, trade_list=plan)

    lines = summary_lines(document_of(s3, evening=result))

    assert line_with(lines, "unfunded") == row("unfunded", f"600099.SH ({CAVEAT})")
    assert line_with(lines, "unaffordable") == row("unaffordable", f"600088.SH ({CAVEAT})")
    assert line_with(lines, "watch list") == row("watch list", f"{H}, {G} ({CAVEAT})")
    assert line_with(lines, "near exit") == row("near exit", C)
    assert line_with(lines, "frozen") == row("frozen", B)
    assert line_with(lines, "deferred exits") == row("deferred exits", "600077.SH")


def test_an_empty_list_prints_as_none_so_a_missing_line_never_means_nothing(s2: Day) -> None:
    lines = summary_lines(document_of(s2))

    for label in (
        "unfunded",
        "unaffordable",
        "deferred exits",
        "near exit",
        "frozen",
        "watch list",
    ):
        assert line_with(lines, label) == row(label, "none")
    assert line_with(lines, "illiquid exits") == row("illiquid exits", "none")
    assert line_with(lines, "catch-up") == row("catch-up", "none")
    assert line_with(lines, "carried orders") == row("carried orders", "none")


def test_an_illiquid_exit_prints_its_amount_rank_and_the_population(illiquid: Illiquid) -> None:
    lines = summary_lines(illiquid.document)

    assert line_with(lines, "illiquid exits") == row("illiquid exits", "1 exit cut for thin amount")
    assert (
        f"  {illiquid.code}: amount rank {illiquid.rank} of population {illiquid.population} "
        "(1 = thinnest; the bottom 9 are cut)"
    ) in lines


def test_the_catch_up_lines_list_each_settled_session(caught_up: CaughtUp) -> None:
    lines = summary_lines(document_of(caught_up.evening, catch_ups=caught_up.caught))
    first, _, last = (c.settlement for c in caught_up.caught)

    assert line_with(lines, "catch-up") == row(
        "catch-up", "3 sessions settled without a decision, oldest first"
    )
    assert (
        "  2021-03-03 (index 2): filled 1, expired 0, cancelled 0, rejected 1, not attempted 0, "
        f"written off 0; equity {first.equity}"
    ) in lines
    assert (
        "  2021-03-05 (index 4): filled 0, expired 1, cancelled 0, rejected 1, not attempted 0, "
        f"written off 0; equity {last.equity}"
    ) in lines


def test_a_day_with_no_evening_prints_that_no_order_was_planned_and_why(frame: Frame) -> None:
    lines = summary_lines(frame_document(frame))

    assert line_with(lines, "book") == row("book", f"no evening was run: {SKIPPED}")
    assert (
        f"ORDERS FOR THE OPEN OF D+1 (call auction 09:15): NONE PLANNED -- no evening was run "
        f"({SKIPPED})"
    ) in lines
    assert not any(line.strip().startswith(("SELL", "BUY")) for line in lines)
    assert "rows 43, universe on main 38" in line_with(lines, "facts")
    for label in ("session", "facts", "screen", "catch-up", "prediction", "units", "wording"):
        line_with(lines, label)
    assert line_with(lines, "tushare requests")
    # What was never decided is not printed as an empty decision.
    assert not any(line.startswith(("unfunded", "watch list", "positions")) for line in lines)


def test_a_settle_only_day_lists_what_it_settled(settle_only: SettleOnly) -> None:
    lines = summary_lines(settle_only_document(settle_only))

    assert line_with(lines, "catch-up") == row(
        "catch-up", "1 session settled without a decision, oldest first"
    )
    assert any(line.startswith("  2021-03-05 (index 4)") for line in lines)
    assert line_with(lines, "book") == row("book", f"no evening was run: {SKIPPED}")


def test_the_prediction_block_is_rendered_as_the_caller_filled_it(s3: WrittenOff) -> None:
    document = document_of(s3)
    registered = {
        "registered": True,
        "record_id": "prd_0123456789abcdef",
        "family": "survivor_book_composite/v1",
        "horizon": "20d",
        "scored": 1740,
        "outcome": "created",
        "standing": "forward",
        "outcome_known_at": "2021-04-02T15:00:00+08:00",
    }

    absent = line_with(summary_lines(document), "prediction")
    full = line_with(summary_lines({**document, "prediction": registered}), "prediction")
    refused = line_with(
        summary_lines({**document, "prediction": {"registered": False, "reason": "after 09:15"}}),
        "prediction",
    )
    bare = line_with(
        summary_lines({**document, "prediction": {"registered": True, "record_id": "prd_x"}}),
        "prediction",
    )

    assert absent == row("prediction", "not in this document")
    assert full == row(
        "prediction",
        "prd_0123456789abcdef survivor_book_composite/v1 20d, 1740 scored, forward "
        "(created; outcome knowable 2021-04-02T15:00:00+08:00)",
    )
    assert refused == row("prediction", "none: after 09:15")
    assert bare == row("prediction", "prd_x")


def test_the_requests_line_says_what_the_run_spent_and_whether_it_ran(s3: WrittenOff) -> None:
    document = document_of(s3)
    spent = {"requests": {"daily": 2, "trade_cal": 1}, "request_count": 3, "wrote": True}
    reprint = {"requests": {}, "request_count": 0, "wrote": False}

    assert line_with(summary_lines(document), "tushare requests") == row(
        "tushare requests", "not recorded in this document"
    )
    assert line_with(summary_lines({**document, "this_run": spent}), "tushare requests") == row(
        "tushare requests", '3 this run {"daily": 2, "trade_cal": 1}'
    )
    assert line_with(summary_lines({**document, "this_run": reprint}), "tushare requests") == row(
        "tushare requests", "0 this run {} -- the day was already complete; nothing was run"
    )


def test_the_units_line_says_how_to_read_a_ledger_figure(s3: WrittenOff) -> None:
    units = line_with(summary_lines(document_of(s3)), "units")

    assert "fees after an adjustment event are charged on the scaled notional" in units
    assert "filled_price and marks are in ledger units" in units
    assert "transfer_fee is slippage" in units


def test_the_last_line_is_the_wording_verbatim(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))

    assert lines[-1] == row("wording", SURVIVOR_WORDING)


def test_the_lines_come_from_the_document_alone(s3: WrittenOff) -> None:
    document = document_of(s3)

    # A journal read back from disk: nothing of the run's objects survives, and nothing is needed.
    assert summary_lines(json.loads(json.dumps(document))) == summary_lines(document)
