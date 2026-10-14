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
    carried_since,
    catch_up_entry,
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


FORBIDDEN_PHRASES: Final[tuple[str, ...]] = (
    "recommend",
    "signal to buy",
    "buy signal",
    "forecast",
    "expected return",
)
"""What a candidate of a registered configuration is never called: nothing here is advice, a
signal, a forecast or an expectation of a return."""


def test_no_string_of_a_document_or_a_summary_recommends_or_forecasts_anything(
    s3: WrittenOff, frame: Frame, settle_only: SettleOnly, s2: Day
) -> None:
    documents = [
        document_of(s3),
        document_of(s2),
        frame_document(frame),
        settle_only_document(settle_only),
    ]
    printed = [line for document in documents for line in summary_lines(document)]
    for text in (*(item for document in documents for item in strings(document)), *printed):
        lowered = text.lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in lowered, f"{phrase!r} in {text!r}"


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
    assert witness["inert"] is True  # 2026-04-10 is on or after the date
    assert "no witness of special treatment" in witness["note"]
    assert "2025-07-07" in witness["note"]


def test_the_band_is_a_witness_on_a_session_before_2025_07_07(s3: WrittenOff) -> None:
    witness = document_of(s3)["facts"]["band_witness"]  # S3 is 2021-03-04

    assert witness["inert"] is False
    assert witness["inert_from"] == "2025-07-07"


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
            "ledger_mark": "11.50",
            "ledger_ratio": None,
            "halted": False,
            "flags": [],
            "carried_since": None,
        },
        {
            "ts_code": C,
            "ledger_quantity": 900,
            "real_close": "10.10",
            "ledger_mark": "10.10",
            "ledger_ratio": None,
            "halted": False,
            "flags": [],
            "carried_since": None,
        },
    ]


def test_a_holding_through_an_adjustment_keeps_its_real_close_and_says_the_ratio_to_its_mark(
    s3_adjusted: Day,
) -> None:
    holdings = document_of(s3_adjusted)["paper"]["book"]["holdings"]

    adjusted, plain = holdings
    assert adjusted["ts_code"] == B
    assert adjusted["ledger_quantity"] == 800
    assert adjusted["real_close"] == "5.75"  # the facts day's, not the ledger's mark
    assert adjusted["ledger_mark"] == "11.50"  # 5.75 x the ratio of 2: the ledger's units
    assert adjusted["ledger_ratio"] == "2.0000"
    assert plain["ts_code"] == C
    assert (plain["real_close"], plain["ledger_mark"], plain["ledger_ratio"]) == (
        "10.10",
        "10.10",
        None,
    )


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
    assert [o["suspended"] for o in orders] == [False, False, False]
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


def test_a_sale_of_a_held_name_the_screen_never_assessed_says_so(s3: WrittenOff) -> None:
    """The kernel sells a holding it holds no verdict for -- `decide_book` forces an exit for a
    code that is not in the screen -- and the sale says that is why."""
    kept = evenings.screened((F, "10.00"), (G, "15.00"), (C, "10.10"), (E, "22.00"), (H, "8.00"))
    assert B not in {verdict.ts_code for verdict in kept.verdicts}
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

    document = document_of(s3, evening=result, screen=kept)

    assert document["paper"]["open_orders"][0]["reasons"] == ["not_assessed"]
    assert (
        f"  SELL ENTIRE POSITION {B} (ledger qty 800; real close 11.50) EXIT_ELIGIBILITY_LOST "
        "(not_assessed)"
    ) in summary_lines(document)


def test_a_trade_list_carrying_an_order_the_settlement_does_not_hold_live_is_refused(
    s3: WrittenOff,
) -> None:
    """The plan and the settlement are one evening's: an order the plan carries and the book does
    not hold live is another evening's, and its attempt count is not known."""
    plan = s3.result.trade_list.model_copy(
        update={"carried": (*s3.result.trade_list.carried, s3.sell_a)}
    )
    result = dataclasses.replace(s3.result, trade_list=plan)

    with pytest.raises(ValueError, match="does not hold it live"):
        document_of(s3, evening=result)


def test_a_sale_of_a_suspended_name_says_its_close_may_be_an_earlier_sessions(
    s3_halted: Day,
) -> None:
    """The facts day carries a halted name on the last close it had, so its line says so as the
    holdings line does."""
    sale = s3_halted.result.trade_list.orders[0].model_copy(
        update={
            "side": "sell",
            "ts_code": B,
            "quantity": 800,
            "reason": "EXIT_SURVIVOR_LOST",
            "expires_session": None,
        }
    )
    plan = s3_halted.result.trade_list.model_copy(update={"orders": (sale,)})
    result = dataclasses.replace(s3_halted.result, trade_list=plan)

    document = document_of(s3_halted, evening=result)

    assert document["paper"]["open_orders"][0]["suspended"] is True
    assert (
        f"  SELL ENTIRE POSITION {B} (ledger qty 800; real close 11.50) {SUSPENDED_CLOSE} "
        "EXIT_SURVIVOR_LOST"
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


def test_a_day_with_no_evening_and_no_reason_is_refused_by_name(frame: Frame) -> None:
    # A caller that forgot why the evening did not run would otherwise file a document that says
    # "no reason was given" for a day the journal cannot explain.
    with pytest.raises(ValueError, match="no evening was given and no paper_skipped"):
        frame_document(frame, paper_skipped=None)
    with pytest.raises(ValueError, match="no evening was given and no paper_skipped"):
        frame_document(frame, paper_skipped="")


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


COUNTED: Final[FactsNotes] = FactsNotes.tally(
    reasons=(
        *["st_by_name"] * 2,
        *["st_pending"] * 3,
        *["st_by_band"] * 4,
        *["delisting"] * 17,
        *["unknown_no_record"] * 5,
        *["unknown_before_first"] * 6,
    ),
    suspended_without_bar=13,
    no_listing_row=12,
    no_bar_within_carry=14,
    not_listed_with_bar=15,
    unrecognised_board_codes=("303002.SZ", "303001.SZ"),
)
"""Every count of the notes a number of its own, so a line that shows the wrong one -- or none --
is a wrong number in this table."""
PRINTED_AS: Final[Mapping[str, str]] = {
    "delisting": "delisting 17",
    "name_unknown_assumed_ordinary": "name unknown -> ordinary 11",
    "name_unknown_before_first": "before first record 6",
    "name_unknown_no_record": "never renamed 5",
    "no_bar_within_carry": "no_bar_within_carry 14",
    "no_listing_row": "no_listing_row 12",
    "not_listed_with_bar": "not_listed_with_bar 15",
    "st": "ST 9 ",
    "st_by_band": "by band 4",
    "st_by_name": "by name 2",
    "st_pending": "pending 3",
    "suspended_without_bar": "suspended_without_bar 13",
    "unrecognised_board": "unrecognised_board 2 ",
    "unrecognised_board_codes": "[303001.SZ, 303002.SZ]",
}
"""Where each field of `FactsNotes` is printed: the fragment of the facts lines that carries its
number. The keys are the adapter's own fields, so a field added to the notes is red here until the
summary prints it and this table says where."""


def test_every_count_the_adapter_noted_is_printed_with_its_own_number(s3: WrittenOff) -> None:
    day = s3_facts(notes=COUNTED)

    lines = summary_lines(document_of(s3, facts=day))

    assert set(PRINTED_AS) == {field.name for field in dataclasses.fields(FactsNotes)}
    text = "\n".join(lines)
    for name, fragment in PRINTED_AS.items():
        assert fragment in text, f"{name} is not printed as {fragment!r}"
    assert "other notes" not in text  # nothing the summary does not know


def test_a_count_the_summary_does_not_know_is_printed_not_dropped(s3: WrittenOff) -> None:
    """A document from a newer adapter has a count this summary has no line for: its journal is
    the same, and the summary says so instead of leaving the number out."""
    document = document_of(s3)
    document["facts"]["notes"]["a_count_added_later"] = 7
    document["facts"]["notes"]["another"] = 3

    lines = summary_lines(document)

    assert "  other notes a_count_added_later 7, another 3" in lines  # sorted by name


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


def test_a_position_after_an_adjustment_prints_the_real_close_and_the_ratio_to_reconcile(
    s3_adjusted: Day,
) -> None:
    lines = summary_lines(document_of(s3_adjusted))

    adjusted = next(line for line in lines if line.startswith(f"  {B}  ledger qty"))
    plain = next(line for line in lines if line.startswith(f"  {C}  ledger qty"))
    assert adjusted == f"  {B}  ledger qty 800  real close 5.75  ledger/real ratio 2.0000"
    # The ledger's mark of 11.50 is not a price on any screen; the line never says it.
    assert "11.50" not in adjusted
    assert plain == f"  {C}  ledger qty 900  real close 10.10"
    # The ratio reconciles the ledger's units with the real close; it is no share count (D5 joint
    # review, Important 2: a cash dividend moves it too).
    assert "account shares" not in line_with(lines, "positions")


POSITIONS_HEADER: Final[str] = (
    "(ledger quantity in ledger units; real close from the facts; a ledger/real ratio is the "
    "adjustment since the buy, cash dividends included, and no share count: the account's share "
    "count differs from the ledger's only after a share event -- a bonus issue or a split -- and "
    "every sale is SELL ENTIRE POSITION)"
)


def test_a_ratio_moved_by_a_cash_dividend_derives_no_share_count(s3: WrittenOff) -> None:
    """A cash dividend moves Tushare's `adj_factor` as a bonus issue does, so a holding's ledger
    mark over its real close moves too -- and the account still holds the shares it bought, plus
    the cash. 600020.SH's 11.50 mark over a real close of 11.27 (0.23 paid out) is 1.0204; and the
    review's case, 1000 shares bought at 25.00 with 0.50 paid out (real close 24.50), is 1.0204 as
    well, where "ledger qty x ratio" would say 1020.4 shares. No line derives a count from it."""
    paid = document_of(s3, facts=s3_facts({**S3_CLOSES, B: "11.27"}))
    (holding,) = [item for item in paid["paper"]["book"]["holdings"] if item["ts_code"] == B]
    assert (holding["ledger_quantity"], holding["ledger_mark"], holding["real_close"]) == (
        800,
        "11.50",
        "11.27",
    )
    assert holding["ledger_ratio"] == "1.0204"
    assert (D("25.00") / D("24.50")).quantize(D("0.0001")) == D("1.0204")
    review = json.loads(json.dumps(paid))
    review["paper"]["book"]["holdings"] = [
        {**holding, "ledger_quantity": 1000, "ledger_mark": "25.00", "real_close": "24.50"}
        if item["ts_code"] == B
        else item
        for item in review["paper"]["book"]["holdings"]
    ]

    for document, quantity, close, derived in (
        (paid, 800, "11.27", "816"),
        (review, 1000, "24.50", "1020"),
    ):
        lines = summary_lines(document)

        assert line_with(lines, "positions") == row("positions", f"2 holdings {POSITIONS_HEADER}")
        position = f"  {B}  ledger qty {quantity}  real close {close}  ledger/real ratio 1.0204"
        assert position in lines
        assert not any(derived in line for line in lines)
        assert not any("account shares" in line for line in lines)


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


def orders_header(day: Day, when: str, outcome: str = "created") -> str:
    """The ORDERS line: the open the orders are for, the trade list they are from and whether this
    run committed the evening or derived one that was already settled."""
    states = {
        "created": "evening created",
        "unchanged": "evening unchanged (settled earlier; derived again, nothing written)",
    }
    return (
        f"ORDERS FOR THE OPEN OF D+1 (call auction 09:15), {when} -- trade list "
        f"{day.result.trade_list.trade_list_id}, {states[outcome]}"
    )


REFERENCE_NOTE: Final[str] = (
    "  real closes are references, not limit prices; the paper book fills at the open"
)


def test_the_orders_for_the_open_name_the_day_the_call_auction_and_the_trade_list(
    s2: Day,
) -> None:
    lines = summary_lines(document_of(s2))

    assert orders_header(s2, "2021-03-04") in lines
    # Said once, under the header, and not as a price to place an order at.
    assert lines[lines.index(orders_header(s2, "2021-03-04")) + 1] == REFERENCE_NOTE


def test_a_rerun_of_a_settled_evening_prints_a_header_that_says_it_was_not_committed_again(
    s3_twice: tuple[Day, Day],
) -> None:
    first, again = s3_twice

    created = summary_lines(document_of(first))
    unchanged = summary_lines(document_of(again))

    assert orders_header(first, "2021-03-05") in created
    assert orders_header(again, "2021-03-05", "unchanged") in unchanged
    # The same plan, so the same id: what tells the two runs apart is the header's last words.
    assert first.result.trade_list.trade_list_id == again.result.trade_list.trade_list_id
    assert orders_header(first, "2021-03-05") not in unchanged


def test_the_open_the_orders_are_for_is_an_ordinal_when_the_calendar_has_not_reached_it(
    s2: Day,
) -> None:
    lines = summary_lines(document_of(s2, ordinals=ENDS_ON_S2))

    assert orders_header(s2, "session 3") in lines


def test_orders_printed_after_the_call_auction_they_were_for_say_so_under_the_header(
    s2: Day, frame: Frame
) -> None:
    """The daily command says, in `this_run`, when the run that prints a plan was made at or after
    the call auction the plan is for (D5 joint review, Important 1): the header is then followed
    by the line that says so, with the session's date -- the clock's fact, never the document's,
    so nothing filed carries it. A day with no evening has no plan to say it of."""
    passed = {"session": "2021-03-04", "call_auction": "2021-03-04T09:15:00+08:00"}
    run = {"requests": {}, "request_count": 0, "wrote": False, "after_the_call_auction": passed}
    document = document_of(s2)

    lines = summary_lines({**document, "this_run": run})

    header = lines.index(orders_header(s2, "2021-03-04"))
    assert lines[header + 1] == (
        "  the call auction of 2021-03-04 has passed (it began 2021-03-04T09:15:00+08:00): these "
        "orders were for it, and this run was made after it -- the paper book fills them at that "
        "open whether or not they were placed"
    )
    assert lines[header + 2] == REFERENCE_NOTE
    before = summary_lines({**document, "this_run": {**run, "after_the_call_auction": None}})
    assert not any("has passed" in line for line in before)
    assert not any("has passed" in line for line in summary_lines(document))
    assert not any(
        "has passed" in line for line in summary_lines({**frame_document(frame), "this_run": run})
    )


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


def test_a_buy_names_the_quantity_the_reference_and_the_date_it_is_live_through(s2: Day) -> None:
    lines = summary_lines(document_of(s2))

    assert (
        f"  BUY {C} 900 reference: real close 10.00 ENTRY_REPLACEMENT, live through 2021-03-08"
    ) in lines
    assert (
        f"  BUY {E} 400 reference: real close 20.00 ENTRY_REPLACEMENT, live through 2021-03-08"
    ) in lines
    # A close is not a limit price: no line puts one after an "@", the way a limit order would.
    assert not any("@" in line for line in lines if line.strip().startswith(("BUY", "SELL")))


def test_a_buy_whose_last_session_is_past_the_calendar_says_the_ordinal(s2: Day) -> None:
    lines = summary_lines(document_of(s2, ordinals=SHORT_ORDINALS))

    assert (
        f"  BUY {C} 900 reference: real close 10.00 ENTRY_REPLACEMENT, live through session 5"
    ) in lines
    assert orders_header(s2, "2021-03-04") in lines


def test_a_carried_order_is_listed_with_the_attempt_it_is_on(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))

    assert "1 live order from earlier sessions" in line_with(lines, "carried orders")
    assert (
        f"  BUY {E} 400 (planned on session 2 at 20.00; real close today 22.00) "
        "ENTRY_REPLACEMENT, live through 2021-03-08, attempt 2 of 3 "
        "(one per session; a session with no bar counts)"
    ) in lines
    # A carried order is not one of tonight's: tonight's two buys are listed above it.
    tonight = next(i for i, line in enumerate(lines) if f"BUY {F} 900" in line)
    assert tonight < lines.index(line_with(lines, "carried orders"))


def test_an_evening_that_plans_nothing_says_so_and_points_at_the_orders_carried_below(
    s3_halted: Day,
) -> None:
    plan = s3_halted.result.trade_list
    quiet = dataclasses.replace(s3_halted.result, trade_list=plan.model_copy(update={"orders": ()}))
    live = s3_halted.result.settlement.live
    emptied = dataclasses.replace(
        s3_halted.result,
        trade_list=plan.model_copy(update={"orders": (), "carried": ()}),
        settlement=dataclasses.replace(s3_halted.result.settlement, live=()),
    )

    carrying = summary_lines(document_of(s3_halted, evening=quiet))
    nothing = summary_lines(document_of(s3_halted, evening=emptied))

    assert len(live) == 1  # 600050.SH's buy, refused at the open, still live
    header = next(i for i, line in enumerate(carrying) if line.startswith("ORDERS FOR THE OPEN"))
    assert carrying[header + 1] == REFERENCE_NOTE
    # An empty plan above a live order must not read as "nothing is live".
    assert carrying[header + 2] == "  no new order planned tonight; 1 carried order below"
    assert "1 live order from earlier sessions" in line_with(carrying, "carried orders")
    header = next(i for i, line in enumerate(nothing) if line.startswith("ORDERS FOR THE OPEN"))
    assert nothing[header + 2] == "  no new order planned tonight"
    assert line_with(nothing, "carried orders") == row("carried orders", "none")


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
    # "None planned tonight" is not "nothing is live": an earlier evening's orders -- a carried
    # sale above all, which never expires -- are still in the book and are retried at the next
    # open once the session is settled. The module cannot list them, so it says it does not.
    assert (
        "ORDERS FOR THE OPEN OF D+1 (call auction 09:15): NONE PLANNED tonight -- no evening was "
        f"run ({SKIPPED}); orders still live in the paper book from earlier evenings are not "
        "settled tonight and are not listed here (a sale never expires)"
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
    # The orders it settled were an earlier evening's, and are still live: nothing is "planned".
    plan = line_with(lines, "ORDERS FOR THE OPEN OF D+1")
    assert "NONE PLANNED tonight -- no evening was run" in plan
    assert "are not listed here (a sale never expires)" in plan


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


# --- what the daily command adds to a document (`V2-P7-008`, D5b) ------------------------------
#
# The daily command journals each session a catch-up settles as it commits (D3 review, rule 6),
# so a run again after a stop holds some of the day's catch-ups only as the journal filed them; it
# reads the facts day again when the catch-up changed the holdings the facts were read with (rule
# 8); it reads a halt's start off the documents of the sessions before; and it merges its panel
# and factor blocks into the document it prints.


def test_a_catch_up_entry_is_the_documents_own_form_of_a_catch_up(caught_up: CaughtUp) -> None:
    document = document_of(caught_up.evening, catch_ups=caught_up.caught)

    assert [catch_up_entry(item) for item in caught_up.caught] == document["paper"]["catch_ups"]
    assert json.loads(json.dumps(catch_up_entry(caught_up.caught[0]))) == catch_up_entry(
        caught_up.caught[0]
    )
    assert [entry["rederived"] for entry in document["paper"]["catch_ups"]] == [False] * 3


def test_a_catch_up_re_derived_after_an_interrupted_journal_is_named_in_the_summary(
    caught_up: CaughtUp,
) -> None:
    """D5 joint review, Important 3: a session an earlier run of the day committed, and stopped
    before journalling, is derived again by the next run (`unchanged`, nothing written) and filed
    then. Its entry says so, and the catch-up line names it: its fills and write-offs are listed
    once, under it, and nowhere else."""
    first, second, third = caught_up.caught
    filed = [
        catch_up_entry(first),
        catch_up_entry(second, rederived=True),
        catch_up_entry(third, rederived=True),
    ]

    document = document_of(caught_up.evening, filed_catch_ups=filed)
    lines = summary_lines(document)

    days = document["paper"]["catch_ups"]
    assert [day["rederived"] for day in days] == [False, True, True]
    assert {**days[1], "rederived": False} == catch_up_entry(second)
    assert line_with(lines, "catch-up") == row(
        "catch-up",
        "3 sessions settled without a decision, oldest first; re-derived after an interrupted "
        "journal: 2021-03-04, 2021-03-05 (committed by an earlier run of the day, which stopped "
        "before journalling them; derived again now, nothing written)",
    )
    assert line_with(
        summary_lines(document_of(caught_up.evening, catch_ups=caught_up.caught)), "catch-up"
    ) == row("catch-up", "3 sessions settled without a decision, oldest first")


def test_the_catch_ups_an_earlier_run_filed_come_first_and_are_checked_with_tonights(
    caught_up: CaughtUp,
) -> None:
    first, second, third = caught_up.caught
    filed = [json.loads(json.dumps(catch_up_entry(first)))]  # as the journal reads it back

    document = document_of(caught_up.evening, catch_ups=(second, third), filed_catch_ups=filed)

    assert document == document_of(caught_up.evening, catch_ups=caught_up.caught)
    assert document_of(
        caught_up.evening, filed_catch_ups=[catch_up_entry(item) for item in caught_up.caught]
    ) == document_of(caught_up.evening, catch_ups=caught_up.caught)
    with pytest.raises(ValueError, match="oldest first"):
        document_of(caught_up.evening, catch_ups=(first,), filed_catch_ups=filed)
    with pytest.raises(ValueError, match="oldest first"):
        document_of(caught_up.evening, catch_ups=(first,), filed_catch_ups=[catch_up_entry(third)])
    with pytest.raises(ValueError, match="filed catch-up"):
        document_of(caught_up.evening, filed_catch_ups=[{"session": "2021-03-03"}])


def test_a_day_with_no_evening_lists_the_catch_ups_an_earlier_run_filed(
    settle_only: SettleOnly,
) -> None:
    filed = [catch_up_entry(settle_only.caught)]

    document = settle_only_document(settle_only, catch_ups=(), filed_catch_ups=filed)

    assert document == settle_only_document(settle_only)
    assert document["paper_skipped"]["catch_ups"] == filed


def test_an_evening_on_a_day_read_again_documents_its_paper_on_the_facts_it_ran_on(
    s3: WrittenOff,
) -> None:
    """The prediction's facts were read with the holdings pinned before the catch-up -- none,
    here, and with no row for 600020.SH, which tonight's book holds -- and the paper book read the
    day again with the holdings it found: the facts and screen sections stay the prediction's,
    and the paper section is the second read's, which it carries whole."""
    pinned = facts_of(S3, {code: close for code, close in S3_CLOSES.items() if code != B})
    plain = document_of(s3)

    document = document_of(s3, facts=pinned, read_again=(s3.facts, s3.screen))

    assert document["facts"] == document_of(s3, facts=pinned)["facts"]
    assert document["facts"]["held"] == []
    assert document["screen"] == plain["screen"]
    paper = document["paper"]
    assert list(paper)[-1] == "facts_read_again"
    assert paper["facts_read_again"] == plain["facts"]
    assert paper["facts_read_again"]["held"] == [A, B, DL]
    assert {key: value for key, value in paper.items() if key != "facts_read_again"} == plain[
        "paper"
    ]
    assert paper["book"]["holdings"][0]["ts_code"] == B
    assert paper["book"]["holdings"][0]["real_close"] == "11.50"
    assert document_of(s3, facts=pinned)["paper"]["book"]["holdings"][0]["real_close"] is None
    assert summary_lines(json.loads(json.dumps(document))) == summary_lines(document)


def test_only_an_evening_of_the_session_reads_its_facts_day_again(
    s3: WrittenOff, frame: Frame
) -> None:
    with pytest.raises(ValueError, match="read again"):
        frame_document(frame, read_again=(frame.facts, frame.screen))
    with pytest.raises(ValueError, match="read again"):
        document_of(s3, read_again=(facts_of(S2, S2_CLOSES), s3.screen))


def halted_on(*days: tuple[date, Sequence[str], Sequence[str]]) -> list[tuple[date, Any]]:
    """Earlier documents, newest first, as far as `carried_since` reads them: each session's
    holdings, and which of them were carried (no bar) that evening."""
    return [
        (
            day,
            {
                "paper": {
                    "book": {
                        "holdings": [
                            {"ts_code": code, "flags": ["carried"] if code in carried else []}
                            for code in held
                        ]
                    }
                }
            },
        )
        for day, held, carried in days
    ]


def test_a_halt_began_on_the_session_after_the_last_evening_that_had_a_bar() -> None:
    earlier = halted_on(
        (date(2021, 3, 3), (B, C), (B,)),
        (date(2021, 3, 2), (B, C), (B,)),
        (date(2021, 3, 1), (B, C), ()),
        (date(2021, 2, 26), (B, C), (B,)),  # an earlier halt, ended before: not this one
    )

    since = carried_since({B, C}, session=S3, earlier=earlier)

    # 600020.SH is carried tonight and was on the two evenings before, not on the one before
    # those; 600030.SH, carried tonight, had its bar yesterday: its halt begins tonight.
    assert since == {B: date(2021, 3, 2), C: S3}
    assert carried_since((), session=S3, earlier=earlier) == {}


def test_a_halt_from_the_first_session_after_the_opening_began_on_that_session() -> None:
    opening: list[tuple[date, Any]] = [(E0, {"session_index": 0, "opening": {"cash": "1"}})]

    assert carried_since({B}, session=S3, earlier=halted_on((S2, (B,), (B,)), (S1, (B,), ()))) == {
        B: S2
    }
    assert carried_since({B}, session=S2, earlier=[*halted_on((S1, (B,), (B,))), *opening]) == {
        B: S1
    }


@pytest.mark.parametrize(
    "unknown",
    [
        pytest.param(None, id="no journal or an unreadable one"),
        pytest.param({"paper": None, "paper_skipped": {"reason": "settled only"}}, id="no evening"),
        pytest.param({"paper": {"book": {}}}, id="no holdings"),
        pytest.param({"paper": {"book": {"holdings": [{"ts_code": B}]}}}, id="no flags"),
        pytest.param("not a document", id="not a mapping"),
    ],
)
def test_a_halt_whose_start_no_document_says_is_left_out_never_guessed(unknown: Any) -> None:
    earlier = [*halted_on((S2, (B, C), (B,))), (S1, unknown)]

    since = carried_since({B, C}, session=S3, earlier=earlier)

    # 600030.SH had its bar on S2, so its halt begins tonight whatever came before; the start of
    # 600020.SH's is in the session nobody can read, so it has no date -- never "1 session".
    assert since == {C: S3}


def test_a_history_that_ends_before_the_halt_does_leaves_the_halt_undated() -> None:
    earlier = halted_on((S2, (B,), (B,)), (S1, (B,), (B,)))

    assert carried_since({B}, session=S3, earlier=earlier) == {}


def test_the_history_is_read_only_as_far_back_as_a_halt_reaches() -> None:
    def history() -> Iterator[tuple[date, Any]]:
        yield from halted_on((S2, (B,), ()))
        raise AssertionError("read past the session that ended every halt")

    assert carried_since({B}, session=S3, earlier=history()) == {B: S3}


def test_the_panel_and_factor_catch_ups_the_caller_merged_are_printed_before_the_requests(
    s3: WrittenOff,
) -> None:
    document = {
        **document_of(s3),
        "panel": {"caught_up": ["2021-03-02", "2021-03-03"]},
        "factors": {"caught_up": ["2021-03-03"]},
        "this_run": {"requests": {}, "request_count": 0, "wrote": True},
    }

    lines = summary_lines(document)

    assert line_with(lines, "panel catch-up") == row("panel catch-up", "2021-03-02, 2021-03-03")
    assert line_with(lines, "factor catch-up") == row("factor catch-up", "2021-03-03")
    order = [
        next(i for i, line in enumerate(lines) if line.startswith(label))
        for label in ("prediction", "panel catch-up", "factor catch-up", "tushare requests")
    ]
    assert order == sorted(order)
    for nothing in ({}, {"panel": {"caught_up": []}, "factors": {"caught_up": []}}):
        quiet = summary_lines({**document_of(s3), **nothing})
        assert not any(line.startswith(("panel catch-up", "factor catch-up")) for line in quiet)
