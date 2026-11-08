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
from decimal import ROUND_HALF_UP, Decimal
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
from openalpha_cn.backtest import execution
from openalpha_cn.backtest.paper_evening import CatchUpResult, EveningResult
from openalpha_cn.backtest.paper_reconciliation import (
    NOT_PLACED,
    Divergence,
    Reconciliation,
)
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
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE
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
AS_OF_S1: Final[datetime] = datetime(2021, 3, 2, 18, 30, tzinfo=SHANGHAI)
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
    "ORDERS FOR THE OPEN OF D+1 (call auction 09:15)",
    "carried orders",
    "unfunded",
    "unaffordable",
    "deferred exits",
    "near exit",
    "frozen",
    "watch list",
    "illiquid exits",
    "positions",
    "book",
    "settled",
    "catch-up",
    "diagnostics",
    "ids",
    "facts",
    "screen",
    "prediction",
    "tushare requests",
    "units",
    "wording",
)
"""The summary's sections, in the order they are printed (V2-P7-008 final fix, M1): what the user
acts on first -- the orders, what the draw left out, the book -- and the diagnostics of how the
run read its data after a `diagnostics` header, the wording last."""


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
        "funding",
        "reconciliation",
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
    # ST on 2021-03-05, before 2025-07-07: the 5% band, 11.50 x 0.95 = 10.925, half up (I2).
    assert (
        f"  SELL ENTIRE POSITION {B} (ledger qty 800; real close 11.50) EXIT_ELIGIBILITY_LOST "
        "(special_treatment, suspended); limit-down 10.93"
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
        "(not_assessed); no limit computed: the screen holds no verdict for it, so whether it is "
        "ST is not known"
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
        "EXIT_SURVIVOR_LOST; limit-down 10.35"
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
    # Planned on S2's evening, printed by its date (D5 joint review, Minor 7).
    assert (carried["signal_session"], carried["signal_on"]) == (2, "2021-03-03")
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
    # I3: repeated in the ORDERS block for the next open, as the carried buy is. S3's screen holds
    # no verdict for 600010.SH, so whether it is ST is not known and no limit is computed (I2).
    assert (
        f"  REPEAT SELL ENTIRE POSITION {A} (ledger qty 900; real close 10.10) EXIT_SURVIVOR_LOST, "
        "planned on 2021-03-03, attempt 2, a sale never expires -- not filled in the paper book: "
        "re-place it for the open of 2021-03-05 unless your account sold it; no limit computed: "
        "the screen holds no verdict for it, so whether it is ST is not known"
    ) in lines
    assert SELL_NOTE in lines


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

    # The ordinal is a diagnostic (M1): it is on the `ids` line, not the session's.
    assert line_with(lines, "session") == row(
        "session", "2021-03-04 (as of 2021-03-04T18:30:00+08:00)"
    )
    assert line_with(lines, "ids").startswith(row("ids", "session index 3; trade list trl_"))
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
    # The pinned holdings the facts were read with, which are the book's before tonight's fills
    # -- not the book's holdings after them (M1: "held 0" while the book held 9).
    assert "book holdings before tonight's fills 3" in line_with(lines, "facts")
    assert "held 3" not in line_with(lines, "facts")
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
    # I4: a buy the paper book rejected may have filled in the account, which then holds a name
    # the book never sells.
    assert f"  {E} buy 400: rejected -- {LIMIT_UP_REFUSAL}; {NOT_HELD}" in lines
    assert f"{C} buy 900: filled;" not in text
    # M8: the write-off is the paper book's; the account may still hold the shares.
    assert (
        f"  {DL} written off (ledger qty 900): whole cost basis 9005.09, no price -- the paper "
        "book wrote it off; your account may still hold the shares, and no SELL will be printed "
        "for them again"
    ) in lines


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

    # I4: an expired buy is one the paper book never filled, whatever the account did.
    assert f"    expired {E} buy 400; {NOT_HELD}" in lines


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
    "(ledger qty is in the ledger's units and a ledger/real ratio is the adjustment since the "
    "buy, cash dividends included -- neither is a share count; every sale is SELL ENTIRE "
    "POSITION)"
)
"""The positions header, shortened from the 300-character note (M1) and keeping what the D5 joint
review (Important 2) made it say: no share count is derived from the ratio."""


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

    # "carried since" collided with "carried orders" (M6): a halt says it has had no bar.
    assert f"{halted}  no bar tonight (marked at its last price)" in unsaid
    assert "since" not in "\n".join(line for line in unsaid if line.startswith(f"  {B}"))
    assert f"{halted}  no bar since 2021-02-25 (marked at its last price)" in said
    assert not any("carried since" in line for line in (*said, *unsaid))


def orders_header(day: Day, when: str, outcome: str = "created") -> str:
    """The ORDERS line: the open the orders are for and whether this run committed the evening or
    derived one that was already settled. The trade list's id is a diagnostic (M1): it is on the
    `ids` line, which ties the printed plan to its journal entry."""
    states = {
        "created": "evening created",
        "unchanged": "evening unchanged (settled earlier; derived again, nothing written)",
    }
    return f"ORDERS FOR THE OPEN OF D+1 (call auction 09:15), {when} -- {states[outcome]}"


REFERENCE_NOTE: Final[str] = (
    "  real closes are references, not limit prices; the paper book fills at the open"
)
LIMIT_NOTE: Final[str] = (
    "  at the 09:15 call auction an order carries a limit price: this system assumes that a buy "
    "placed at its limit-up price and a sell placed at its limit-down price take the single "
    "opening price -- confirm with your broker. Each limit below is computed from the real close "
    "(main board 10%; ST 5% before 2025-07-07, 10% from it); the exchange's published limit "
    "governs"
)
"""I2: the price an order is placed at, said once under the header as the system's assumption."""
ASSUMED: Final[str] = (
    "  the paper book assumes every order above is placed and fills at the open; your account "
    "follows the book only if you did exactly that"
)
"""I4: printed every evening after the orders."""
SELL_NOTE: Final[str] = (
    "  a SELL above of a name your account does not hold (you did not buy it -- for example, you "
    "followed this morning's re-check): ignore that SELL"
)
"""I4: printed after the orders of an evening that plans or repeats a sale."""
NOT_HELD: Final[str] = (
    "if your real order did fill, the paper book does not hold it and will never print a SELL "
    "for it"
)
"""I4: what a buy the paper book rejected or let expire says of a real order that filled."""
UNCOVERED: Final[str] = (
    "    is not covered by the cash at 09:15 (the plan asks for more than the account holds; do "
    "not place it)"
)
"""I1: the mark under a buy that, at its limit-up price, is past the cash at 09:15."""


def test_the_orders_for_the_open_name_the_day_the_call_auction_and_the_trade_list(
    s2: Day,
) -> None:
    lines = summary_lines(document_of(s2))

    header = lines.index(orders_header(s2, "2021-03-04"))
    # Said once, under the header, and not as a price to place an order at; then the limit price
    # the order is placed at, as the system's assumption (I2).
    assert lines[header + 1] == REFERENCE_NOTE
    assert lines[header + 2] == LIMIT_NOTE
    # The trade list is named on the ids line, the run's diagnostics (M1), not in the header.
    assert s2.result.trade_list.trade_list_id not in lines[header]
    assert s2.result.trade_list.trade_list_id in line_with(lines, "ids")


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
        f"  SELL ENTIRE POSITION {A} (ledger qty 900; real close 10.00) EXIT_SURVIVOR_LOST; "
        "limit-down 9.00"
    ) in lines


def test_a_sale_through_an_adjustment_prints_the_real_close_and_no_share_count_to_sell(
    s3_adjusted: Day,
) -> None:
    lines = summary_lines(document_of(s3_adjusted))

    sales = [line for line in lines if line.strip().startswith("SELL")]
    # The limit is the real close's (5.75 x 0.90 = 5.175, half up), never the ledger mark's.
    assert sales == [
        f"  SELL ENTIRE POSITION {B} (ledger qty 800; real close 5.75) EXIT_SURVIVOR_LOST; "
        "limit-down 5.18"
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

    # M2: shares, the amount at the reference price; I2: the limit-up price and what the buy
    # needs at it -- 900 x 11.00 and 400 x 22.00.
    assert (
        f"  BUY {C} 900 shares, about 9,000 yuan -- reference: real close 10.00 "
        "ENTRY_REPLACEMENT, live through 2021-03-08; limit-up 11.00, 9,900.00 yuan at it"
    ) in lines
    assert (
        f"  BUY {E} 400 shares, about 8,000 yuan -- reference: real close 20.00 "
        "ENTRY_REPLACEMENT, live through 2021-03-08; limit-up 22.00, 8,800.00 yuan at it"
    ) in lines
    # A close is not a limit price: no line puts one after an "@", the way a limit order would.
    assert not any("@" in line for line in lines if line.strip().startswith(("BUY", "SELL")))


def test_a_buy_whose_last_session_is_past_the_calendar_says_the_ordinal(s2: Day) -> None:
    lines = summary_lines(document_of(s2, ordinals=SHORT_ORDINALS))

    assert (
        f"  BUY {C} 900 shares, about 9,000 yuan -- reference: real close 10.00 "
        "ENTRY_REPLACEMENT, live through session 5; limit-up 11.00, 9,900.00 yuan at it"
    ) in lines
    assert orders_header(s2, "2021-03-04") in lines


REPEATED_BUY: Final[str] = (
    f"  REPEAT BUY {E} 400 shares, about 8,000 yuan (planned on 2021-03-03 at 20.00; real close "
    "today 22.00) ENTRY_REPLACEMENT, live through 2021-03-08, attempt 2 of 3 (one per session; a "
    "session with no bar counts) -- not filled in the paper book: re-place it for the open of "
    "2021-03-05 unless your account filled it; limit-up 24.20, 9,680.00 yuan at it"
)
"""I3: S3's carried buy, inside the ORDERS block -- a broker's order is a day order, and the paper
book tries it again at the next open. Its limit is today's real close's (22.00 x 1.10)."""


def test_a_carried_order_is_listed_with_the_attempt_it_is_on(s3: WrittenOff) -> None:
    lines = summary_lines(document_of(s3))

    assert line_with(lines, "carried orders") == row(
        "carried orders", "1 live order from earlier sessions, repeated in ORDERS above"
    )
    assert REPEATED_BUY in lines
    # Inside the ORDERS block, before tonight's buys: the kernel funds a carried buy first.
    header = lines.index(line_with(lines, "ORDERS FOR THE OPEN OF D+1"))
    repeated = lines.index(REPEATED_BUY)
    tonight = next(i for i, line in enumerate(lines) if line.startswith(f"  BUY {F} 900 shares"))
    assert header < repeated < tonight < lines.index(line_with(lines, "carried orders"))
    # And nowhere else: the carried section says how many, and adds no second copy.
    assert sum(f"{E} 400" in line for line in lines if "BUY" in line) == 1


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
    assert carrying[header + 1 : header + 3] == [REFERENCE_NOTE, LIMIT_NOTE]
    # An empty plan beside a live order must not read as "nothing is live": the carried buy is
    # repeated in the block (I3), and the line says the plan of tonight adds nothing to it.
    assert carrying[header + 3].startswith(f"  REPEAT BUY {E} 400 shares")
    assert carrying[header + 4] == (
        "  no new order planned tonight; 1 carried order repeated above"
    )
    assert "1 live order from earlier sessions" in line_with(carrying, "carried orders")
    header = next(i for i, line in enumerate(nothing) if line.startswith("ORDERS FOR THE OPEN"))
    assert nothing[header + 3] == "  no new order planned tonight"
    assert line_with(nothing, "carried orders") == row("carried orders", "none")
    # With no order at all there is no cash to compare, nothing assumed placed, no SELL to ignore.
    assert not any(line.startswith("funding") for line in nothing)
    assert ASSUMED not in nothing and SELL_NOTE not in nothing


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
    # M6: each of the three says, in one clause, what it means for the account -- above all that
    # a deferred exit is not a sale to make tonight.
    assert line_with(lines, "near exit") == row(
        "near exit",
        f"{C} (held and still a survivor, near the survivor cut: information only, no order)",
    )
    assert line_with(lines, "frozen") == row(
        "frozen", f"{B} (held and halted: left alone until it trades again, no order)"
    )
    assert line_with(lines, "deferred exits") == row(
        "deferred exits",
        "600077.SH (left the survivors; kept tonight because tonight's sales reached the cap: no "
        "order -- do not sell it; the paper book sells it at a later evening)",
    )


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
    # It did settle tonight's session (D5 joint review, Minor 6): never "not settled tonight".
    assert plan == (
        "ORDERS FOR THE OPEN OF D+1 (call auction 09:15): NONE PLANNED tonight -- no evening was "
        f"run ({SKIPPED}); the session was settled tonight without a decision (catch-up below), "
        "and the orders from earlier evenings it left live are retried at the next open and are "
        "not listed here (a sale never expires)"
    )


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


# --- what the user does with the lines (V2-P7-008 final fix, the user-view review) --------------


def open_order(
    side: str,
    code: str,
    quantity: int,
    price: str,
    *,
    carried: bool = False,
    reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """One entry of a document's `open_orders`, as `evening_document` files it: planned on S3 for
    the open of S4 (a carried one on S2), its real close its reference price."""
    signal = 2 if carried else 3
    return {
        "order_id": f"tor_{side}_{code}",
        "side": side,
        "ts_code": code,
        "quantity": quantity,
        "reference_price": price,
        "real_close": price,
        "suspended": False,
        "reason": "EXIT_SURVIVOR_LOST" if side == "sell" else "ENTRY_REPLACEMENT",
        "reasons": list(reasons),
        "signal_session": signal,
        "signal_on": ORDINALS.day_of(signal).isoformat(),
        "execute_session": signal + 1,
        "execute_on": ORDINALS.day_of(signal + 1).isoformat(),
        "expires_session": None if side == "sell" else signal + 3,
        "expires_on": None if side == "sell" else ORDINALS.day_of(signal + 3).isoformat(),
        "carried": carried,
        "attempt": 2 if carried else None,
        "attempts_allowed": None if side == "sell" else 3,
    }


def with_orders(
    s3: WrittenOff, *, cash: str, orders: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """S3's document with the book's cash and the open orders replaced, as a journal could hold
    them: `summary_lines` reads nothing but the document."""
    document = json.loads(json.dumps(document_of(s3)))
    document["paper"]["book"]["cash"] = cash
    document["paper"]["open_orders"] = [dict(order) for order in orders]
    return document


def lines_after(lines: Sequence[str], start: str) -> list[str]:
    """The lines after the one that begins with `start`."""
    index = lines.index(line_with(lines, start))
    return list(lines[index + 1 :])


def test_the_orders_block_reports_the_freeze_and_names_a_short_buy(s3: WrittenOff) -> None:
    document = with_orders(
        s3,
        cash="13400.00",
        orders=[open_order("buy", "600199.SH", 1200, "10.00")],
    )
    document["paper"]["funding"] = {
        "cash": "13400.00",
        "sale_proceeds_pending": "0.00",
        "reserved_for_carried": "0.00",
        "frozen": "13200.00",
        "freeze_ratio": "1.10",
        "shortfalls": [
            {
                "ts_code": "600199.SH",
                "equal_share_quantity": 3400,
                "placed_quantity": 1200,
                "missing_amount": "24200.00",
            }
        ],
    }

    lines = summary_lines(document)

    assert (
        row(
            "funding",
            "cash at 09:15 13,400.00 yuan; buys freeze 13,200.00 yuan at their limit-up prices "
            "(1.10x the reference, the main board's band); sells tonight release about 0.00 yuan "
            "after the auction, not before it",
        )
        in lines
    )
    assert (
        "  600199.SH: 1,200 of the 3,400 shares the equal share asks; 24,200.00 yuan short at "
        "the limit-up price"
    ) in lines


def test_the_initial_build_of_ten_equal_weight_buys_names_the_one_the_limit_up_freezes_short(
    tmp_path: Path,
) -> None:
    """The whole chain on a real evening: 350,000.00 of cash and sixteen survivors at 10.00, so
    the equal share 350,000.00 / 10 x 0.995 = 34,825.00 asks for 3,400 shares of each. Nine buys
    freeze 37,400.00 at their 11.00 limit-up, 336,600.00 in all, and the 13,400.00 left freezes
    1,200 shares of the tenth -- 24,200.00 short of the equal share. The ORDERS block prints the
    cash at 09:15 and the frozen total it recomputes from the plan as a cross-check, names the
    tenth by code from the evening's own funding report, and keeps the paper book's assumption
    that every printed order is placed; nothing here is a real run, and the wording stays
    "candidate"."""
    names = [f"6001{n:02d}.SH" for n in range(16)]
    screen_ = evenings.screened(*((name, "10.00") for name in names))
    prices = dict.fromkeys(names, "10.00")
    result = evenings.evening(
        evenings.book_at(tmp_path, cash="350000.00"), S1, evenings.NOTHING, screen_, prices
    )
    day = Day(
        result,
        facts_of(S1, dict.fromkeys(names, D("10.00"))),
        screen_,
        S1,
        1,
        AS_OF_S1,
    )

    lines = summary_lines(document_of(day))

    assert line_with(lines, "funding").startswith(
        row("funding", "cash at 09:15 350,000.00 yuan; buys freeze 349,800.00 yuan")
    )
    short = result.decision.entries[-1].ts_code
    assert (
        f"  {short}: 1,200 of the 3,400 shares the equal share asks; 24,200.00 yuan short at "
        "the limit-up price"
    ) in lines
    assert "  the paper book assumes every order above is placed and fills at the open" in (
        " ".join(lines)
    )


def test_a_document_filed_before_the_funding_block_says_the_shortfalls_were_not_recorded(
    s2: Day,
) -> None:
    document = document_of(s2)
    document["paper"].pop("funding", None)

    assert line_with(summary_lines(document), "funding").endswith(
        "; shortfalls not recorded (a plan filed before the funding block)"
    )


@pytest.mark.parametrize(
    ("cash", "sells", "buys", "marked"),
    [
        pytest.param(
            "42209.79",
            [("601058.SH", 500, "66.16"), ("601113.SH", 200, "126.99")],
            [("601051.SH", 500, "61.71"), ("601092.SH", 300, "97.70")],
            ["601092.SH"],
            id="4-13: 33,940 at the limit-up fits, 33,940 + 32,241 does not",
        ),
        pytest.param(
            "15110.99",
            [("601114.SH", 200, "117.01")],
            [("601081.SH", 200, "138.34")],
            ["601081.SH"],
            id="4-15: 30,434 against 15,110.99",
        ),
        pytest.param(
            "12245.88",
            [("601046.SH", 600, "52.85")],
            [("601097.SH", 400, "74.90")],
            ["601097.SH"],
            id="5-20: 32,956 against 12,245.88",
        ),
    ],
)
def test_a_buy_the_cash_at_0915_does_not_cover_says_it_is_not_covered(
    s3: WrittenOff,
    cash: str,
    sells: Sequence[tuple[str, int, str]],
    buys: Sequence[tuple[str, int, str]],
    marked: Sequence[str],
) -> None:
    """I1, the reviewer's three cases, recomputed at the limit-up: the kernel sizes and freezes
    tonight's buys on the cash at 09:15 alone (tonight's sales fill in the same auction, after
    the buys are frozen), so the buys are summed in the order the kernel funds them at the
    prices they freeze at, and each whose running total passes the cash says it is not covered.
    The block says what the cash is, what the buys freeze and what the sales release after the
    auction."""
    orders = [*(open_order("sell", *sell) for sell in sells)]
    orders += [open_order("buy", *buy) for buy in buys]

    lines = summary_lines(with_orders(s3, cash=cash, orders=orders))

    for code, _quantity, _price in buys:
        line = next(line for line in lines if line.startswith(f"  BUY {code} "))
        after = lines[lines.index(line) + 1]
        assert (after == UNCOVERED) is (code in marked), (code, after)
    released = sum(D(price) * quantity for _code, quantity, price in sells)
    at_limit = sum(
        (D(price) * D("1.10")).quantize(D("0.01"), rounding=ROUND_HALF_UP) * quantity
        for _code, quantity, price in buys
    )
    assert (
        row(
            "funding",
            f"cash at 09:15 {D(cash):,.2f} yuan; buys freeze {at_limit:,.2f} yuan at their "
            "limit-up prices (1.10x the reference, the main board's band); sells tonight release "
            f"about {released:,.2f} yuan after the auction, not before it",
        )
    ) in lines


def test_the_running_total_funds_the_carried_buys_first_and_says_tonights_is_not_covered(
    s3: WrittenOff,
) -> None:
    """The kernel reserves the buys carried from earlier sessions before tonight's see any cash
    (`domain.survivor_orders`): 8,800 of a REPEAT (800 at its 11.00 limit-up) fits 10,000, and
    tonight's 5,500 (500 at 11.00, after it) does not."""
    orders = [
        open_order("buy", "600071.SH", 800, "10.00", carried=True),
        open_order("buy", "600072.SH", 500, "10.00"),
    ]
    lines = summary_lines(with_orders(s3, cash="10000.00", orders=orders))

    repeated = next(line for line in lines if line.startswith("  REPEAT BUY 600071.SH"))
    tonight = next(line for line in lines if line.startswith("  BUY 600072.SH"))
    assert lines.index(repeated) < lines.index(tonight)
    assert lines[lines.index(repeated) + 1] != UNCOVERED
    assert lines[lines.index(tonight) + 1] == UNCOVERED
    assert (
        row(
            "funding",
            "cash at 09:15 10,000.00 yuan; buys freeze 14,300.00 yuan at their limit-up prices "
            "(1.10x the reference, the main board's band); sells tonight release about 0.00 yuan "
            "after the auction, not before it",
        )
    ) in lines


def test_a_limit_is_the_paper_books_own_rule_on_the_real_close_to_the_cent(s2: Day) -> None:
    """I2: the next session's limits are computed from the real close by the function the paper
    book's execution derives a band with (`backtest.execution.derived_price_band`, which
    `_price_band` calls): 10% on the main board, rounded half up to the cent."""
    document = json.loads(json.dumps(document_of(s2)))
    buy_e = next(o for o in document["paper"]["open_orders"] if o["ts_code"] == E)
    buy_e["real_close"] = buy_e["reference_price"] = "87.82"

    lines = summary_lines(document)

    # 87.82 x 1.10 = 96.602: 96.60, and 400 x 96.60 at it.
    assert next(line for line in lines if line.startswith(f"  BUY {E} ")).endswith(
        "; limit-up 96.60, 38,640.00 yuan at it"
    )
    derived = execution.derived_price_band
    assert derived(D("87.82"), board="main", is_st=False) == (D("96.60"), D("79.04"))
    assert derived(D("10.10"), board="main", is_st=True) == (D("10.61"), D("9.60"))
    for close, is_st in ((D("87.82"), False), (D("10.10"), True), (D("5.75"), False)):
        bar = execution.MarketBar(
            subject=E,
            trade_date=S3,
            board="main",
            previous_close=close,
            open=close,
            high=close,
            low=close,
            close=close,
            suspended=False,
            is_st=is_st,
        )
        # The one derivation: the paper book's execution reaches the same band through it.
        assert execution._price_band(bar) == derived(close, board="main", is_st=is_st)


def test_a_special_treatment_sale_is_limited_at_five_percent_before_2025_07_07_and_ten_from_it(
    s3: WrittenOff,
) -> None:
    """The 5% band of a main-board ST name ended on 2025-07-07 (`BAND_WITNESS_ENDS`): from that
    session the exchange's band is 10% for it as for any main-board name, so the limit printed
    for the open is 10% then -- the exchange's published limit governs, and the line says so."""
    sale = open_order("sell", B, 800, "11.50", reasons=("special_treatment",))
    before = with_orders(s3, cash="0.00", orders=[sale])
    after = json.loads(json.dumps(before))
    after["paper"]["open_session"]["date"] = "2026-04-14"

    sale_line = f"  SELL ENTIRE POSITION {B} "
    early = next(line for line in summary_lines(before) if line.startswith(sale_line))
    late = next(line for line in summary_lines(after) if line.startswith(sale_line))

    assert early.endswith("; limit-down 10.93")  # 11.50 x 0.95 = 10.925, half up
    assert late.endswith("; limit-down 10.35")  # 11.50 x 0.90


def test_an_order_whose_limit_cannot_be_computed_says_so_and_prints_no_number(s2: Day) -> None:
    document = json.loads(json.dumps(document_of(s2)))
    sale = next(o for o in document["paper"]["open_orders"] if o["side"] == "sell")
    sale["real_close"] = None

    line = next(line for line in summary_lines(document) if line.startswith("  SELL ENTIRE"))

    assert line == (
        f"  SELL ENTIRE POSITION {A} (ledger qty 900; real close n/a) EXIT_SURVIVOR_LOST; no "
        "limit computed: no real close tonight"
    )


def test_every_evening_says_the_account_follows_the_book_only_if_it_placed_every_order(
    s2: Day, s3: WrittenOff
) -> None:
    """I4: the paper book fills what it planned whether or not the account placed it. Said after
    the orders every evening that has any; a SELL of a name the account does not hold is to be
    ignored, said when there is a sale."""
    with_a_sale = summary_lines(document_of(s2))
    buys_only = summary_lines(document_of(s3))

    for lines in (with_a_sale, buys_only):
        orders = [
            i for i, line in enumerate(lines) if line.lstrip().startswith(("BUY", "SELL", "REPEAT"))
        ]
        assert lines.index(ASSUMED) > max(orders)
        assert lines.index(ASSUMED) < lines.index(line_with(lines, "carried orders"))
    assert SELL_NOTE in with_a_sale
    assert SELL_NOTE not in buys_only


def test_the_diagnostics_come_after_what_the_user_acts_on(s2: Day, frame: Frame) -> None:
    """M1: the run's diagnostics -- the ids, the facts and screen counters, the record, the
    requests and the units -- follow a `diagnostics` header, after the orders and the book."""
    lines = summary_lines(document_of(s2))
    skipped = summary_lines(frame_document(frame))

    assert line_with(lines, "diagnostics") == row(
        "diagnostics", "how the run read its data -- nothing below is an order"
    )
    assert line_with(lines, "ids") == row(
        "ids",
        f"session index 2; trade list {s2.result.trade_list.trade_list_id}; paper snapshot "
        f"{s2.result.snapshot.snapshot_id}",
    )
    assert line_with(skipped, "ids") == row("ids", f"session index {frame.index}")
    after = lines_after(lines, "diagnostics")
    for label in ("facts", "screen", "prediction", "tushare requests", "units"):
        assert any(line.startswith(label) for line in after), label
    assert not any(line.lstrip().startswith(("BUY", "SELL", "REPEAT")) for line in after)


def test_every_instant_the_user_reads_is_a_shanghai_time(s3: WrittenOff) -> None:
    """M3: a document whose instants were filed in UTC -- a clock, a record's outcome, the call
    auction -- prints them in Asia/Shanghai."""
    document = {
        **document_of(s3),
        "as_of": "2021-03-04T10:30:00+00:00",
        "prediction": {
            "registered": True,
            "record_id": "prd_x",
            "outcome_known_at": "2021-04-02T07:00:00+00:00",
        },
        "this_run": {
            "requests": {},
            "request_count": 0,
            "wrote": False,
            "after_the_call_auction": {
                "session": "2021-03-05",
                "call_auction": "2021-03-05T01:15:00+00:00",
            },
        },
    }

    lines = summary_lines(document)
    text = "\n".join(lines)

    assert line_with(lines, "session") == row(
        "session", "2021-03-04 (as of 2021-03-04T18:30:00+08:00)"
    )
    assert "outcome knowable 2021-04-02T15:00:00+08:00" in line_with(lines, "prediction")
    assert "it began 2021-03-05T09:15:00+08:00" in text
    assert "+00:00" not in text
    assert strategy_survivor_summary._SHANGHAI.key == DEFAULT_DATE_TIMEZONE


def test_a_note_of_the_run_on_why_this_session_is_the_day_is_printed_under_it(
    s3: WrittenOff,
) -> None:
    """M5: a weekend run, or one before the day's data is published at 16:30, prints the last
    complete session's result; the run's note says why, under the session line."""
    note = "today, 2021-03-06, is not a trading day: 2021-03-04 is the last trading day"
    run = {"requests": {}, "request_count": 0, "wrote": False, "session_note": note}

    lines = summary_lines({**document_of(s3), "this_run": run})

    assert lines[lines.index(line_with(lines, "session")) + 1] == f"  {note}"
    assert not any(note in line for line in summary_lines(document_of(s3)))


# --- a refused day's block (I5) -------------------------------------------------------------------

RERUN: Final[str] = "UV_PROJECT_ENVIRONMENT=... scripts/daily_selection.py --runtime-dir RT"
"""The command a refusal block names; the daily command builds it (`daily_selection`)."""
CUTOFF_S4: Final[datetime] = datetime(2021, 3, 5, 9, 15, tzinfo=SHANGHAI)
"""09:15 of S4, the call auction the evening of S3 plans for."""


def refusal_of(**changes: Any) -> list[str]:
    arguments: dict[str, Any] = {
        "session": S3,
        "cutoff": CUTOFF_S4,
        "now": datetime(2021, 3, 4, 19, 0, tzinfo=SHANGHAI),
        "head": None,
        "decided": False,
        "unread": None,
        "ordinals": ORDINALS,
        "rerun": RERUN,
    }
    arguments.update(changes)
    return strategy_survivor_summary.refusal_lines(**arguments)


def test_a_refused_evening_lists_the_orders_still_live_and_the_rerun_before_the_call_auction(
    s2: Day,
) -> None:
    """The evening of S3 refused before its paper book: the book's head is S2's evening, whose
    plan was for S3's open. Nothing new is planned; the paper book settles S3's open with those
    orders and keeps each one that does not fill live for S4's open; the run can be made again
    until S4's call auction, and after it the next run settles S3 without a decision."""
    lines = refusal_of(head=s2.result.snapshot)

    assert lines == [
        "no new orders were produced tonight",
        "orders the paper book holds live from its last settled session, 2021-03-03 (it settles "
        "the open of 2021-03-04 with them first): place again for the open of 2021-03-05 each one "
        "your account did not fill:",
        f"  SELL ENTIRE POSITION {A} (ledger qty 900) EXIT_SURVIVOR_LOST, planned on 2021-03-03; "
        "a sale never expires",
        f"  BUY {C} 900 shares ENTRY_REPLACEMENT, planned on 2021-03-03 at 10.00, live through "
        "2021-03-08",
        f"  BUY {E} 400 shares ENTRY_REPLACEMENT, planned on 2021-03-03 at 20.00, live through "
        "2021-03-08",
        "rerun before 2021-03-05T09:15:00+08:00, the call auction of 2021-03-05, with:",
        f"  {RERUN}",
        "after that, the next run settles 2021-03-04 without a decision",
    ]


def test_a_refusal_after_the_call_auction_names_no_rerun(s2: Day) -> None:
    lines = refusal_of(head=s2.result.snapshot, now=datetime(2021, 3, 5, 10, 0, tzinfo=SHANGHAI))

    assert lines[-1] == (
        "the call auction of 2021-03-05 began 2021-03-05T09:15:00+08:00: a rerun can no longer "
        "decide 2021-03-04, and the next run settles it without a decision"
    )
    assert not any(RERUN in line for line in lines)


def test_a_buy_that_expires_before_the_next_open_is_not_listed_as_live(s2: Day) -> None:
    """S2's buys are live through ordinal 5 (2021-03-08); refused on S5, the next open is ordinal
    6, which only the sale reaches."""
    lines = refusal_of(
        head=s2.result.snapshot, session=S5, cutoff=datetime(2021, 3, 9, 9, 15, tzinfo=SHANGHAI)
    )

    assert lines[1] == (
        "orders the paper book holds live from its last settled session, 2021-03-03 (it settles "
        "the opens from 2021-03-04 through 2021-03-08 with them first): place again for the open "
        "of 2021-03-09 each one your account did not fill:"
    )
    assert [line for line in lines if line.startswith("  ") and RERUN not in line] == [
        f"  SELL ENTIRE POSITION {A} (ledger qty 900) EXIT_SURVIVOR_LOST, planned on 2021-03-03; "
        "a sale never expires"
    ]


def test_an_evening_the_book_committed_before_the_refusal_stands_and_is_listed(
    s3: WrittenOff,
) -> None:
    """The book holds the evening of S3 already -- a run committed it and this one was refused
    finishing it: its plan for S4 is not "no new orders", and the paper book fills it at S4's
    open whether or not it is placed."""
    head = s3.result.snapshot

    lines = refusal_of(head=head, decided=True)

    assert lines[0] == (
        f"the paper book committed the evening of 2021-03-04 before this refusal "
        f"({head.snapshot_id}): its plan for the open of 2021-03-05 stands, and the paper book "
        "fills it at that open whether or not it is placed:"
    )
    assert "no new orders were produced tonight" not in lines
    planned = [item.order.ts_code for item in head.pending_orders if item.order.side == "buy"]
    assert sorted(planned) == sorted([F, G, E])
    assert [line.split()[1] for line in lines if line.startswith("  BUY ")] == planned


def test_a_session_settled_tonight_without_a_decision_lists_what_it_left_live(
    settle_only: SettleOnly,
) -> None:
    head = settle_only.caught.snapshot

    lines = refusal_of(head=head, session=S4, cutoff=datetime(2021, 3, 8, 9, 15, tzinfo=SHANGHAI))

    assert lines[1] == (
        "orders the paper book holds live after settling 2021-03-05: place again for the open of "
        "2021-03-08 each one your account did not fill:"
    )
    assert {line.split()[1] for line in lines if line.startswith("  BUY ")} == {E, F, G}


def test_a_refusal_that_could_not_read_the_book_or_found_none_says_so(s2: Day) -> None:
    unread = refusal_of(unread="PaperStateError: the snapshot of 2021-03-03 is damaged")
    none = refusal_of()
    unknown = refusal_of(session=None, cutoff=None, ordinals=None, head=s2.result.snapshot)

    assert unread[1] == (
        "the paper book's live orders could not be read (PaperStateError: the snapshot of "
        "2021-03-03 is damaged); they are the orders the last evening printed, less any your "
        "account filled"
    )
    assert none[1] == "the paper book holds no live order: it has settled no session yet"
    # No session found (the calendar could not say): the orders by ordinal, a generic deadline.
    assert unknown[1] == (
        "orders the paper book holds live from its last settled session, 2021-03-03: place again "
        "for the next open each one your account did not fill:"
    )
    assert (
        f"  BUY {C} 900 shares ENTRY_REPLACEMENT, planned on session 2 at 10.00, live through "
        "session 5"
    ) in unknown
    assert unknown[-3:] == [
        "rerun before 09:15 of the next session, its call auction, with:",
        f"  {RERUN}",
        "after that, the next run settles the session without a decision",
    ]


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


# --- the account reconciliation's two printed forms (V2-P7-006a) ---------------------------------

UNVERIFIED: Final[str] = (
    "the paper book's assumption that every printed order was placed stands unverified"
)
"""The sentence a day with no record must print: no record is not agreement."""


def _divergence() -> Divergence:
    return Divergence(
        order_id="tor_an_order_no_evening_placed",
        ts_code=C,
        side="buy",
        kind=NOT_PLACED,
        book={"status": "filled", "filled_quantity": "900", "filled_price": "10.00"},
        record={"placed": "false", "filled_quantity": "0", "filled_price": ""},
    )


def _reconciliation(session: date, *, orders: int = 2, reconciled: int = 1) -> Reconciliation:
    return Reconciliation(session=session, orders=orders, divergences=(_divergence(),))


def test_an_evening_with_no_account_record_says_the_books_assumption_stands_unverified(
    s3: Day,
) -> None:
    """The evening path, no record: the ORDERS block's assumption is not checked, and the summary
    says so rather than printing zeros."""
    line = line_with(summary_lines(document_of(s3)), "reconciliation")

    assert f"no account record for {S3.isoformat()}" in line
    assert UNVERIFIED in line


def test_an_evening_with_a_record_names_its_reconciliation_and_every_divergence(s3: Day) -> None:
    """The evening path, with a record: the count reconciled and each divergence run through the
    same `line()` the module gives the journal, so the text and the document cannot drift."""
    document = document_of(s3)
    document["paper"]["reconciliation"] = _reconciliation(S3).document()

    lines = summary_lines(document)

    assert line_with(lines, "reconciliation").endswith(
        "reconciled 1 of 2 orders against the account record"
    )
    assert f"  {_divergence().line()}" in lines


def test_a_settle_only_day_with_no_account_record_says_the_books_assumption_stands_unverified(
    settle_only: SettleOnly,
) -> None:
    """A settle-only day settles its own session as a catch-up, so its reconciliation lives in the
    catch-up block -- and with no record it must say the assumption is unverified there."""
    lines = summary_lines(settle_only_document(settle_only))

    assert any(
        f"no account record for {settle_only.session.isoformat()}" in line and UNVERIFIED in line
        for line in lines
    )


def test_a_settle_only_day_with_a_record_names_its_reconciliation_and_every_divergence(
    settle_only: SettleOnly,
) -> None:
    """A settle-only day with a record: the day's own catch-up entry prints it, by the same
    `line()`, because there is no evening block to carry it."""
    caught = dataclasses.replace(
        settle_only.caught, reconciliation=_reconciliation(settle_only.session)
    )

    lines = summary_lines(settle_only_document(settle_only, catch_ups=(caught,)))

    assert any(
        line.endswith("reconciled 1 of 2 orders against the account record") for line in lines
    )
    assert f"      {_divergence().line()}" in lines


def test_an_earlier_catch_up_says_nothing_about_the_account(caught_up: CaughtUp) -> None:
    """Only the day's own session's entry carries the reconciliation; the earlier sessions it
    caught up are silent, so one record is never printed against another session."""
    lines = summary_lines(document_of(caught_up.evening, catch_ups=caught_up.caught))

    said = [line for line in lines if "no account record for" in line]
    assert len(said) == 1
    assert f"no account record for {S5.isoformat()}" in said[0]
    assert not any(item.settlement.session.isoformat() in said[0] for item in caught_up.caught)
