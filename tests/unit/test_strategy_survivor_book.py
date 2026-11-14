"""The survivor day's paper book, wired for the daily entry (`V2-P7-008`, task D3).

`strategy_survivor_book` is the paper head (step 5), the held statuses of the facts day and the
paper book (step 8) of a survivor day as functions with plain inputs, so that the daily command
(D5) is glue. These tests drive it the way D5 will -- open the head, read the session's facts
with the head's holdings, catch the missed sessions up, run the evening -- against the real
stores: `FilePaperStateStore` and the paper book's own `SQLitePortfolioLedger` in `tmp_path`, the
real kernel inside every evening, and the paper book D15 builds (`make_paper_portfolio`).

## The frame

`tests/unit/test_survivor_facts_panel.py`'s frame, imported rather than copied, written by its
own `write_panel` with a processed factor build at every session 61..70 (`BOOK`) and with
`reversal_1d` undefined for 18 main-board names on session 67, so that day's facts are refused at
the registered coverage. The frame holds no adjustment factors and no index levels -- the facts
adapter reads neither -- so this module adds both through the real writers: a factor of 1 for
every security on every session but `EX_RIGHTS` (`600005.SH`), whose factor doubles from session
64 while its close stays where it was (so its mark in the ledger's units is twice its real close);
and 000300.SH and 000905.SH levels on every session.

The paper book opens on session 60 (2026-03-30, ordinal 0), so a session's ordinal is its frame
index less 60. Every read is at `AS_OF` -- 02:00 Saturday 2026-04-18 in Shanghai, after every
stored session's publication -- because the adjustment factors and index levels are read a year
at a time and a year holding a later session refuses an earlier instant: each run here settles
its sessions from a later read, as a run that missed its evenings does. In UTC that instant is
still Friday, so a settlement's `observed_on` shows which date it was taken from: the Shanghai
one. The facts are read under the frame's identity transform (the shipped one needs 100 names;
the frame has 44), which is what the functions' `transform` argument is for.
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import importlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import DAILY_BASIC_EXTRA_COLUMNS, EXCHANGE
from panel_fixtures import _batch as batch
from panel_fixtures import _close_time as close_time
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from panel_fixtures import _published_time as published_time
from test_survivor_facts_panel import (
    CONFIG,
    DELISTED,
    EXTENDED,
    HALTED,
    IDENTITY,
    LATER,
    LISTED_LATER,
    NEVER_TRADED,
    ORDINARY,
    SESSIONS,
    UNRECOGNISED_PREFIX,
    UNREGISTERED,
    Variant,
    build_panel,
    fingerprint,
    read_day,
    stored_securities,
    write_panel,
)

from openalpha_cn import panel_limit_events, strategy_survivor_book
from openalpha_cn.backtest.multi_day import PortfolioLedger
from openalpha_cn.backtest.paper import PaperPortfolio
from openalpha_cn.backtest.paper_evening import CatchUpResult, PaperLedgerConflictError
from openalpha_cn.backtest.paper_settlement import WRITE_OFF_REASON
from openalpha_cn.backtest.paper_state import PaperStateError, load_snapshot
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.portfolio import PortfolioOrder, PortfolioState, PortfolioTransition
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.survivor_book import (
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SurvivorBookConfig,
)
from openalpha_cn.domain.survivor_screen import screen as survivor_screen
from openalpha_cn.domain.survivor_veto import VETO_RULE_ID, vetoed_codes
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    load_trading_calendar,
    session_publication_instant,
    write_adjustment_factors,
    write_daily_panel,
    write_index_prices,
    write_price_limits,
    write_trading_calendar,
)
from openalpha_cn.panel_limit_events import veto_window
from openalpha_cn.storage.paper_state import FilePaperStateStore, PaperStateStoreError
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger, open_paper_ledger
from openalpha_cn.strategy_survivor import (
    SurvivorAdapterConfig,
    SurvivorRegistration,
    make_paper_portfolio,
)
from openalpha_cn.strategy_survivor_book import (
    PAPER_BOOK_LOCK,
    PAPER_BOOK_MARKER,
    SURVIVOR_BOOK_STEPS,
    CatchUpStalledError,
    PaperBookBusyError,
    PaperBookLock,
    PaperHead,
    SurvivorDayError,
    SurvivorEvening,
    _vetoed_for,
    bind_paper_book,
    catch_up,
    delisted_or_refuse,
    evening,
    facts_day,
    held_codes,
    paper_book_lock,
    paper_head,
    settle_only,
    settlement_names,
)
from openalpha_cn.strategy_survivor_facts import FactsCoverageError, FactsDay, HeldStatus
from openalpha_cn.strategy_survivor_market import SessionOrdinals
from openalpha_cn.strategy_survivor_summary import evening_document, summary_lines

D = Decimal
ROOT: Final[Path] = Path(__file__).resolve().parents[2]

BOOK: Final[Variant] = Variant(
    builds=tuple(range(61, 71)),
    undefined=MappingProxyType({67: frozenset(ORDINARY[:18])}),
)
"""The frame, with a factor build on every session the book settles here, and session 67's
facts below the registered coverage (`reversal_1d` undefined for 18 main-board names)."""
EPOCH: Final[date] = SESSIONS[60]
"""Monday 2026-03-30: the paper book's ordinal 0."""
EX_RIGHTS: Final[str] = "600005.SH"
EX_RIGHTS_FROM: Final[int] = 64
"""`EX_RIGHTS`'s adjustment factor is 1 before session 64 and 2 from it on."""
COVERAGE_REFUSED: Final[int] = 67
AS_OF: Final[datetime] = datetime(2026, 4, 17, 18, 0, tzinfo=UTC)
"""02:00 Saturday 2026-04-18 in Shanghai: after session 74's publication, and before the frame's
`LATER` -- so nothing it reads was published after it."""
OBSERVED_ON: Final[date] = date(2026, 4, 18)
"""`AS_OF`'s Shanghai date: the `observed_on` every settlement here is recorded with. Its UTC
date is the day before."""
CALENDAR: Final[TradingCalendar] = build_panel(BOOK).calendar()
ORDINALS: Final[SessionOrdinals] = SessionOrdinals(CALENDAR, EPOCH)
CONFIG_ID: Final[str] = "rcf_5b1d0c9e8a7f6e5d4c3b2a19"
"""A registration's `config_id`, as the paper book's write-once marker records it."""
REGISTRATION_SHA256: Final[str] = "5a" * 32
"""The registration file's sha256, as the marker binds it (whole-branch seams review, finding 1)."""
REREGISTERED_SHA256: Final[str] = "c3" * 32
"""The same configuration registered again -- a code fix under a bound path, a changed `settings`:
another registration file, another sha256, the same `config_id`."""


def adapter(**changes: object) -> SurvivorAdapterConfig:
    """The registered adapter block, as `V2-P7-006` writes it, with `changes`."""
    block: dict[str, object] = {
        "schema_version": "survivor-adapter/v1",
        "epoch_session": EPOCH.isoformat(),
        "exchange": "SSE",
        "transform": "cross_section_standard/v1",
        "min_coverage": 0.5,
        "max_carry_sessions": 250,
        "max_panel_catch_up_sessions": 16,
        "benchmark": "000905.SH",
        "prediction_horizon": "20d",
        "cost_policy": "protocol/v1",
        "paper_limits": {
            "max_position_weight": "1",
            "max_total_exposure": "1",
            "min_cash_weight": "0",
        },
        "unfilled_buy": "reject_whole_and_carry/v1",
    }
    block.update(changes)
    # The registration admits only the SSE calendar; the generated panel frame carries SZSE's
    # (the same sessions, measured), so the frame is read under its own name: `model_copy`
    # does not validate, which is exactly the one deviation this fixture needs.
    return SurvivorAdapterConfig.model_validate(block).model_copy(update={"exchange": EXCHANGE})


def book_config(**changes: object) -> SurvivorBookConfig:
    """The facts frame's book (seed 7, its four factors, a 60-session listing minimum), changed."""
    stored = CONFIG.model_dump(mode="json", exclude_computed_fields=True)
    return SurvivorBookConfig.model_validate({**stored, **changes})


REGISTRATION: Final[SurvivorRegistration] = SurvivorRegistration(book=CONFIG, adapter=adapter())
OTHER_BOOK: Final[SurvivorRegistration] = SurvivorRegistration(
    book=book_config(holding_count=9), adapter=adapter()
)
"""Another book block under the same adapter: another paper book."""
V2_BOOK: Final[SurvivorBookConfig] = book_config(
    schema_version="survivor-book-config/v2", entry_veto=VETO_RULE_ID
)
"""The same parameters under the v2 contract: plan A's rule may veto its entries (`V2-P7-027`)."""
V2_REGISTRATION: Final[SurvivorRegistration] = SurvivorRegistration(book=V2_BOOK, adapter=adapter())
V2_CONFIG_ID: Final[str] = "rcf_6f1e2d3c4b5a69788796a5b4"
"""The v2 book's own `config_id`: a parallel book binds a marker of its own, never the base's."""
V2_REGISTRATION_SHA256: Final[str] = "8c" * 32


def make_portfolio(ledger: PortfolioLedger) -> PaperPortfolio:
    """D5's factory: the registered limits at the protocol's costs (D15), on `ledger`."""
    return make_paper_portfolio(ledger, REGISTRATION.adapter.paper_limits)


# --- the panel ------------------------------------------------------------------------------------


def _adjustment_factors() -> Any:
    """A factor for every stored security on every session: 1, or 2 for `EX_RIGHTS` from 64."""
    pairs = [(index, code) for index in range(len(SESSIONS)) for code in stored_securities(BOOK)]
    available = [published_time(SESSIONS[index]) for index, _ in pairs]
    return batch(
        ADJ_FACTOR_DATASET,
        subjects=[code for _, code in pairs],
        columns=[
            PanelColumn(
                "factor_date", "string", tuple(SESSIONS[index].isoformat() for index, _ in pairs)
            ),
            PanelColumn(
                "adj_factor",
                "float",
                tuple(
                    2.0 if code == EX_RIGHTS and index >= EX_RIGHTS_FROM else 1.0
                    for index, code in pairs
                ),
            ),
        ],
        event_time=[close_time(SESSIONS[index]) for index, _ in pairs],
        available_time=available,
        fetched_at=max(available),
    )


def _index_levels() -> Any:
    """000300.SH at 4000 + t and 000905.SH at 6000 + t on session t."""
    bases = {"000300.SH": 4000.0, "000905.SH": 6000.0}
    rows = [(code, index) for code in bases for index in range(len(SESSIONS))]
    levels = [bases[code] + index for code, index in rows]
    previous = [level - 1.0 for level in levels]
    available = [published_time(SESSIONS[index]) for _, index in rows]
    return batch(
        INDEX_DAILY_DATASET,
        subjects=[code for code, _ in rows],
        columns=[
            PanelColumn(
                "trade_date", "string", tuple(SESSIONS[index].isoformat() for _, index in rows)
            ),
            *(PanelColumn(name, "float", tuple(levels)) for name in ("open", "high", "low")),
            PanelColumn("close", "float", tuple(levels)),
            PanelColumn("pre_close", "float", tuple(previous)),
            PanelColumn(
                "pct_chg",
                "float",
                tuple(
                    (level / before - 1.0) * 100.0
                    for level, before in zip(levels, previous, strict=True)
                ),
            ),
            PanelColumn("vol", "float", tuple(1.0 for _ in rows)),
            PanelColumn("amount", "float", tuple(1.0 for _ in rows)),
        ],
        event_time=[close_time(SESSIONS[index]) for _, index in rows],
        available_time=available,
        fetched_at=max(available),
    )


@pytest.fixture(scope="module")
def panel(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    store = write_panel(tmp_path_factory.mktemp("book-panel"), BOOK)
    write_adjustment_factors(store, [_adjustment_factors()], calendar=CALENDAR)
    write_index_prices(store, [_index_levels()])
    return store


@pytest.fixture(scope="module")
def extended(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    """The frame's `EXTENDED` panel: a prefix no board has yet is among its securities."""
    return write_panel(tmp_path_factory.mktemp("extended"), EXTENDED)


class ReadRecordingStore(PanelStore):
    """`PanelStore`, recording the dataset of every gated read made through it -- the pattern of
    `tests/unit/scripts/test_daily_selection_survivor.py`'s store of the same name (every gated
    read passes `assess_readiness`; a catalog lookup reads no row and is not recorded)."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.read: set[str] = set()

    def assess_readiness(self, requirement: Any) -> Any:
        self.read.add(requirement.dataset)
        return super().assess_readiness(requirement)


SETTLEMENT_READS: Final[frozenset[str]] = frozenset(
    {"daily", "stk_limit", "suspend_d", "adj_factor", "index_daily"}
)
"""What `settlement_market` reads: the session's bars and bands, the halts, the factors and the
benchmark. A facts read adds the registry, the name corpus, the calendar and the factor tier."""
FACTS_ONLY_READS: Final[frozenset[str]] = frozenset({"stock_basic", "namechange", "trade_cal"})


# --- the paper book, run as the daily command runs it --------------------------------------------


@dataclass(frozen=True)
class Paper:
    """One runtime directory's paper book: its snapshot store, its own ledger, and the writer lock
    this run holds on it."""

    root: Path
    store: FilePaperStateStore
    ledger: SQLitePortfolioLedger
    lock: PaperBookLock


@contextmanager
def opened_paper(root: Path) -> Iterator[Paper]:
    """The paper book at `root`, under its writer lock for the body -- as D5 holds it."""
    with paper_book_lock(root) as lock:
        yield Paper(
            root=root,
            store=FilePaperStateStore(root / "snapshots"),
            ledger=open_paper_ledger(root / "ledger.sqlite3"),
            lock=lock,
        )


@pytest.fixture
def paper(tmp_path: Path) -> Iterator[Paper]:
    with opened_paper(tmp_path / "paper") as held:
        yield held


def tree(root: Path) -> dict[str, bytes]:
    """The bytes of every file under `root`."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def files(paper: Paper) -> dict[str, bytes]:
    """The bytes of every file under the paper directory -- snapshots, ledger, marker, lock."""
    found = tree(paper.root)
    assert "ledger.sqlite3" in found  # never a comparison of two empty listings
    return found


ScreenOf = Callable[[FactsDay], ScreenResult]


def real_screen(facts: FactsDay, registration: SurvivorRegistration = REGISTRATION) -> ScreenResult:
    return survivor_screen(facts.facts, registration.book)


def ranked(
    facts: FactsDay, survivors: Sequence[str], *, eligible: Sequence[str] = ()
) -> ScreenResult:
    """The real screen of `facts` with `survivors` as tonight's survivors, best first; the codes
    of `eligible` are made eligible first."""
    closes = {fact.ts_code: fact.close for fact in facts.facts}
    made = set(eligible)
    verdicts = tuple(
        EligibilityVerdict(ts_code=verdict.ts_code, composite=0.5)
        if verdict.ts_code in made
        else verdict
        for verdict in real_screen(facts).verdicts
    )
    return ScreenResult(
        verdicts=verdicts,
        survivors=tuple(
            RankedName(ts_code=code, composite=1 - rank / 100, rank=rank, close=closes[code])
            for rank, code in enumerate(survivors, start=1)
        ),
    )


def without(*codes: str) -> ScreenOf:
    """The real screen with `codes` taken out of the survivors (still eligible: a lost place)."""

    def screen_of(facts: FactsDay) -> ScreenResult:
        kept = [name.ts_code for name in real_screen(facts).survivors if name.ts_code not in codes]
        return ranked(facts, kept)

    return screen_of


@dataclass(frozen=True)
class Day:
    """What one run of the survivor day did with the paper book."""

    head: PaperHead
    facts: FactsDay
    screen: ScreenResult
    caught_up: tuple[CatchUpResult, ...]
    evening: SurvivorEvening


def head_of(
    paper: Paper,
    index: int,
    registration: SurvivorRegistration = REGISTRATION,
    ordinals: SessionOrdinals = ORDINALS,
    *,
    config_id: str = CONFIG_ID,
    registration_sha256: str = REGISTRATION_SHA256,
) -> PaperHead:
    return paper_head(
        state_store=paper.store,
        registration=registration,
        ordinals=ordinals,
        session=SESSIONS[index],
        paper_directory=paper.root,
        config_id=config_id,
        registration_sha256=registration_sha256,
        lock=paper.lock,
    )


def read_facts(
    panel: PanelStore,
    index: int,
    held: Sequence[str],
    registration: SurvivorRegistration = REGISTRATION,
) -> FactsDay:
    return facts_day(
        panel=panel,
        registration=registration,
        session=SESSIONS[index],
        as_of=AS_OF,
        held=held,
        transform=IDENTITY,
    )


def catch_up_through(
    panel: PanelStore,
    paper: Paper,
    through: int,
    *,
    registration: SurvivorRegistration = REGISTRATION,
    factory: Callable[[PortfolioLedger], PaperPortfolio] = make_portfolio,
    ordinals: SessionOrdinals = ORDINALS,
    on_settled: Callable[[CatchUpResult], None] | None = None,
) -> tuple[CatchUpResult, ...]:
    """Catch the book up through the session of frame index `through`."""
    return catch_up(
        panel=panel,
        state_store=paper.store,
        ledger=paper.ledger,
        registration=registration,
        ordinals=ordinals,
        calendar=CALENDAR,
        through_index=ORDINALS.index_of(SESSIONS[through]),
        as_of=AS_OF,
        make_portfolio=factory,
        lock=paper.lock,
        on_settled=on_settled,
        transform=IDENTITY,
    )


def settle_only_at(
    panel: PanelStore,
    paper: Paper,
    index: int,
    *,
    on_settled: Callable[[CatchUpResult], None] | None = None,
) -> tuple[CatchUpResult, ...]:
    """Settle the session of frame index `index` -- and the ones missed before it -- only."""
    return settle_only(
        panel=panel,
        state_store=paper.store,
        ledger=paper.ledger,
        registration=REGISTRATION,
        ordinals=ORDINALS,
        calendar=CALENDAR,
        session=SESSIONS[index],
        as_of=AS_OF,
        make_portfolio=make_portfolio,
        lock=paper.lock,
        on_settled=on_settled,
        transform=IDENTITY,
    )


def evening_of(
    panel: PanelStore,
    paper: Paper,
    index: int,
    *,
    facts: FactsDay,
    screen: ScreenResult,
    held: Sequence[str],
    registration: SurvivorRegistration = REGISTRATION,
    factory: Callable[[PortfolioLedger], PaperPortfolio] = make_portfolio,
    as_of: datetime = AS_OF,
    ordinals: SessionOrdinals = ORDINALS,
) -> SurvivorEvening:
    return evening(
        panel=panel,
        state_store=paper.store,
        ledger=paper.ledger,
        registration=registration,
        ordinals=ordinals,
        calendar=CALENDAR,
        session=SESSIONS[index],
        as_of=as_of,
        facts=facts,
        screen=screen,
        held=held,
        make_portfolio=factory,
        lock=paper.lock,
        transform=IDENTITY,
    )


def run_day(
    panel: PanelStore,
    paper: Paper,
    index: int,
    *,
    screen_of: ScreenOf = real_screen,
    registration: SurvivorRegistration = REGISTRATION,
) -> Day:
    """Steps 5, 6 and 8 of the survivor day of session `index`, in D5's order: the head (its
    `held` pinned), the session's facts read with that `held` and screened, the missed sessions
    caught up, then the evening with the pinned `held`."""
    head = head_of(paper, index, registration)
    facts = read_facts(panel, index, head.held, registration)
    screen = screen_of(facts)
    caught = catch_up_through(panel, paper, index - 1, registration=registration)
    tonight = evening_of(
        panel,
        paper,
        index,
        facts=facts,
        screen=screen,
        held=head.held,
        registration=registration,
    )
    return Day(head=head, facts=facts, screen=screen, caught_up=caught, evening=tonight)


def v2_day(
    panel: PanelStore,
    paper: Paper,
    index: int,
    *,
    as_of: datetime = AS_OF,
    screen_of: ScreenOf | None = None,
) -> Day:
    """Steps 5, 6 and 8 of the survivor day of session `index` on the v2 book (`V2-P7-027`), in
    D5's order: the head (a marker of its own, its `held` pinned), the session's facts read with
    that `held` and screened, the missed sessions caught up, then the evening with the pinned
    `held`. A rerun at another `as_of` reads the same day at the later instant."""
    head = head_of(
        paper,
        index,
        V2_REGISTRATION,
        config_id=V2_CONFIG_ID,
        registration_sha256=V2_REGISTRATION_SHA256,
    )
    facts = facts_day(
        panel=panel,
        registration=V2_REGISTRATION,
        session=SESSIONS[index],
        as_of=as_of,
        held=head.held,
        transform=IDENTITY,
    )
    screen = real_screen(facts, V2_REGISTRATION) if screen_of is None else screen_of(facts)
    caught = catch_up(
        panel=panel,
        state_store=paper.store,
        ledger=paper.ledger,
        registration=V2_REGISTRATION,
        ordinals=ORDINALS,
        calendar=CALENDAR,
        through_index=ORDINALS.index_of(SESSIONS[index]) - 1,
        as_of=as_of,
        make_portfolio=make_portfolio,
        lock=paper.lock,
        transform=IDENTITY,
    )
    tonight = evening_of(
        panel,
        paper,
        index,
        facts=facts,
        screen=screen,
        held=head.held,
        registration=V2_REGISTRATION,
        as_of=as_of,
    )
    return Day(head=head, facts=facts, screen=screen, caught_up=caught, evening=tonight)


def close_in(facts: FactsDay, code: str) -> Decimal:
    return next(fact.close for fact in facts.facts if fact.ts_code == code)


# --- the refusal -------------------------------------------------------------------------------


def _script(name: str, directory: Path) -> ModuleType:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


def test_the_book_refuses_under_the_names_the_survivor_day_numbers_its_steps_by() -> None:
    daily = _script("daily_selection", ROOT / "scripts")
    assert SURVIVOR_BOOK_STEPS == ("paper head", "facts", "paper book")
    assert set(SURVIVOR_BOOK_STEPS) <= set(daily.SURVIVOR_STEPS)
    refusal = SurvivorDayError("paper book", "the book stalls on 2026-04-02; update the panel")
    assert refusal.step == "paper book"
    assert str(refusal) == "the book stalls on 2026-04-02; update the panel"
    assert isinstance(refusal, RuntimeError) and not isinstance(refusal, ValueError)
    with pytest.raises(ValueError, match="paper boook"):
        SurvivorDayError("paper boook", "a misspelled step")
    assert issubclass(PaperBookBusyError, SurvivorDayError)
    stalled = CatchUpStalledError("paper book", "stalled", caught_up=())
    assert isinstance(stalled, SurvivorDayError) and stalled.caught_up == ()


# --- one value spelled in two places (whole-branch seams review, finding 9) ----------------------


def test_the_floor_a_settle_only_day_reads_its_facts_at_is_the_books_no_floor() -> None:
    """The daily command's `SETTLE_ONLY_COVERAGE` (a settle-only day decides nothing) and this
    module's `_NO_COVERAGE_FLOOR` (a catch-up, and the evening's second read, decide nothing the
    first read did not) are one rule, spelled twice: pinned equal."""
    daily = _script("daily_selection", ROOT / "scripts")
    no_floor = getattr(strategy_survivor_book, "_NO_COVERAGE_FLOOR")  # noqa: B009

    assert daily.SETTLE_ONLY_COVERAGE == no_floor == 0.0


def test_the_key_the_daily_command_tells_a_survivor_registration_by_is_the_registrations() -> None:
    """`daily_selection.REGISTRATION_KIND_KEY` is spelled there so that a P6 day loads no survivor
    module; it is `strategy_survivor.KIND_KEY`, pinned equal."""
    from openalpha_cn import strategy_survivor

    daily = _script("daily_selection", ROOT / "scripts")

    assert daily.REGISTRATION_KIND_KEY == strategy_survivor.KIND_KEY == "kind"


@pytest.mark.parametrize("bound", [0, 2])
def test_the_epoch_bound_is_one_where_the_daily_command_and_the_paper_head_check_it(
    panel: PanelStore, tmp_path: Path, bound: int
) -> None:
    """`1 + max_panel_catch_up_sessions` is checked twice: by the daily command before any request
    (`_admit_epoch_reach`) and by `paper_head` when it opens the book. The two admit the same
    sessions -- the last one `1 + bound` sessions after the epoch -- and refuse the next one, each
    naming the same bound."""
    daily = _script("daily_selection", ROOT / "scripts")
    registration = SurvivorRegistration(
        book=CONFIG, adapter=adapter(max_panel_catch_up_sessions=bound)
    )
    named = f"may be at most {1 + bound} sessions before the first run"
    for index, admitted in ((61 + bound, True), (62 + bound, False)):
        try:
            daily._admit_epoch_reach(
                panel, survivor=registration, session=SESSIONS[index], as_of=AS_OF, book_held=False
            )
            by_the_daily = True
        except daily.StepFailedError as refused:
            assert named in str(refused)
            by_the_daily = False
        with opened_paper(tmp_path / f"paper-{index}") as paper:
            try:
                head_of(paper, index, registration)
                by_the_head = True
            except SurvivorDayError as refused:
                assert named in str(refused)
                by_the_head = False

        assert (by_the_daily, by_the_head) == (admitted, admitted), SESSIONS[index]


# --- the head's holdings -------------------------------------------------------------------------


def test_held_codes_are_the_positions_and_settlement_names_add_the_live_orders(
    panel: PanelStore, paper: Paper
) -> None:
    planned = run_day(panel, paper, 61).evening.result.snapshot
    buys = sorted(item.order.ts_code for item in planned.pending_orders)
    assert len(buys) == 10
    assert held_codes(planned) == ()
    assert settlement_names(planned) == tuple(buys)

    (settled,) = catch_up_through(panel, paper, 62)
    assert held_codes(settled.snapshot) == tuple(buys)
    assert settlement_names(settled.snapshot) == tuple(buys)
    assert held_codes(settled.snapshot) == tuple(
        sorted(position.subject for position in settled.snapshot.book.positions)
    )


# --- the held statuses of the facts day ---------------------------------------------------------


def test_a_delisted_holding_is_handed_on_and_a_holding_with_a_row_passes(panel: PanelStore) -> None:
    facts = read_day(panel, held=(ORDINARY[0], DELISTED), as_of=AS_OF)
    assert {status.ts_code: status.status for status in facts.held_status} == {
        ORDINARY[0]: "row",
        DELISTED: "delisted",
    }

    assert delisted_or_refuse(facts) == frozenset({DELISTED})
    assert delisted_or_refuse(read_day(panel, held=(ORDINARY[0],), as_of=AS_OF)) == frozenset()
    assert delisted_or_refuse(read_day(panel, as_of=AS_OF)) == frozenset()


@pytest.mark.parametrize(
    ("code", "status", "remedy"),
    [
        (NEVER_TRADED, "no_bar_in_reach", "daily"),
        (UNREGISTERED, "not_in_registry", "stock_basic"),
        ("600999.SH", "not_in_registry", "stock_basic"),
        (LISTED_LATER, "not_yet_listed", "stock_basic"),
    ],
)
def test_a_holding_the_paper_book_cannot_settle_refuses_the_day_by_name_with_its_remedy(
    panel: PanelStore, code: str, status: str, remedy: str
) -> None:
    facts = read_day(panel, held=(ORDINARY[0], code), as_of=AS_OF)
    assert {item.ts_code: item.status for item in facts.held_status}[code] == status

    with pytest.raises(SurvivorDayError) as refused:
        delisted_or_refuse(facts)
    with pytest.raises(SurvivorDayError) as at_step_six:
        delisted_or_refuse(facts, step="facts")

    message = str(refused.value)
    assert refused.value.step == "paper book"  # the paper book's refusal, after the prediction
    assert at_step_six.value.step == "facts"
    assert code in message and status in message and remedy in message
    assert SESSIONS[69].isoformat() in message
    assert ORDINARY[0] not in message


def test_a_holding_on_a_board_the_adapter_does_not_recognise_refuses_the_day(
    extended: PanelStore,
) -> None:
    facts = read_day(extended, held=(UNRECOGNISED_PREFIX,))
    assert [item.status for item in facts.held_status] == ["board_unrecognised"]

    with pytest.raises(SurvivorDayError) as refused:
        delisted_or_refuse(facts)

    assert refused.value.step == "paper book"
    assert UNRECOGNISED_PREFIX in str(refused.value)
    assert "board_unrecognised" in str(refused.value)
    assert "MAIN_BOARD_PREFIXES" in str(refused.value)
    assert "V2-P7-006" in str(refused.value)  # a code change re-binds the registration


def test_every_holding_refused_is_named_at_once_and_a_delisted_one_is_not(
    panel: PanelStore,
) -> None:
    facts = read_day(panel, held=(NEVER_TRADED, UNREGISTERED, DELISTED), as_of=AS_OF)

    with pytest.raises(SurvivorDayError) as refused:
        delisted_or_refuse(facts)

    message = str(refused.value)
    assert NEVER_TRADED in message and UNREGISTERED in message
    assert DELISTED not in message


def test_a_held_status_this_build_does_not_know_refuses_the_day(panel: PanelStore) -> None:
    facts = read_day(panel, held=(NEVER_TRADED,), as_of=AS_OF)
    unknown = dataclasses.replace(
        facts,
        held_status=(HeldStatus.model_construct(ts_code=NEVER_TRADED, status="merged_away"),),
    )

    with pytest.raises(SurvivorDayError) as refused:
        delisted_or_refuse(unknown)

    assert NEVER_TRADED in str(refused.value) and "merged_away" in str(refused.value)
    assert "does not know" in str(refused.value)


# --- the marker: one paper book per runtime directory --------------------------------------------


def bind(directory: Path, **changes: object) -> None:
    arguments: dict[str, Any] = {
        "config_id": CONFIG_ID,
        "config_digest": REGISTRATION.digest,
        "epoch_session": EPOCH,
        "registration_sha256": REGISTRATION_SHA256,
    }
    arguments.update(changes)
    bind_paper_book(directory, **arguments)


BINDING: Final[dict[str, str]] = {
    "schema_version": "survivor-paper-binding/v1",
    "config_id": CONFIG_ID,
    "config_digest": REGISTRATION.digest,
    "epoch_session": EPOCH.isoformat(),
    "registration_sha256": REGISTRATION_SHA256,
}
"""The marker `bind` writes."""


def test_the_paper_book_is_bound_to_its_registration_once_and_the_same_binding_writes_nothing(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "paper"
    bind(directory)
    marker = directory / PAPER_BOOK_MARKER
    assert json.loads(marker.read_text(encoding="utf-8")) == BINDING
    written, stat = marker.read_bytes(), marker.stat()

    bind(directory)

    assert marker.read_bytes() == written
    assert (marker.stat().st_ino, marker.stat().st_mtime_ns) == (stat.st_ino, stat.st_mtime_ns)
    assert [path.name for path in directory.iterdir()] == [PAPER_BOOK_MARKER]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config_id", "rcf_0000000000000000000000ff"),
        ("config_digest", "ab" * 32),
        ("epoch_session", SESSIONS[59]),
        ("registration_sha256", REREGISTERED_SHA256),
    ],
)
def test_another_registration_is_refused_by_the_marker_naming_what_differs(
    tmp_path: Path, field: str, value: object
) -> None:
    directory = tmp_path / "paper"
    bind(directory)
    written = (directory / PAPER_BOOK_MARKER).read_bytes()

    with pytest.raises(SurvivorDayError) as refused:
        bind(directory, **{field: value})

    message = str(refused.value)
    assert refused.value.step == "paper head"
    assert field in message
    assert str(value.isoformat() if isinstance(value, date) else value) in message
    assert "runtime directory of its own" in message
    assert (directory / PAPER_BOOK_MARKER).read_bytes() == written


def test_a_marker_that_cannot_be_read_is_refused_and_left_as_it_is(tmp_path: Path) -> None:
    directory = tmp_path / "paper"
    directory.mkdir()
    marker = directory / PAPER_BOOK_MARKER
    marker.write_bytes(b"{not json")

    with pytest.raises(SurvivorDayError) as refused:
        bind(directory)

    assert refused.value.step == "paper head"
    assert PAPER_BOOK_MARKER in str(refused.value)
    assert marker.read_bytes() == b"{not json"


@pytest.mark.parametrize("failing", ["link", "mkstemp"])
def test_a_marker_the_filesystem_cannot_write_is_refused_by_name_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise OSError(errno.EPERM, "Operation not permitted")

    if failing == "link":
        monkeypatch.setattr(strategy_survivor_book.os, "link", refuse)
    else:
        monkeypatch.setattr(strategy_survivor_book.tempfile, "mkstemp", refuse)
    directory = tmp_path / "paper"

    with pytest.raises(SurvivorDayError) as refused:
        bind(directory)

    assert refused.value.step == "paper head"
    assert PAPER_BOOK_MARKER in str(refused.value) and "hard links" in str(refused.value)
    assert isinstance(refused.value.__cause__, OSError)
    assert list(directory.iterdir()) == []


# --- the paper head ------------------------------------------------------------------------------


def test_the_first_run_opens_the_book_on_the_registered_epoch_with_the_registered_capital(
    paper: Paper,
) -> None:
    head = head_of(paper, 61)

    assert head.snapshot.session == EPOCH
    assert head.snapshot.session_index == 0
    assert head.snapshot.book.cash == CONFIG.capital
    assert head.snapshot.config_digest == REGISTRATION.digest
    assert head.snapshot.last_settled_session is None
    assert (head.session_index, head.held, head.opened_only, head.settled) == (1, (), False, False)
    assert paper.store.sessions() == (EPOCH,)
    assert json.loads((paper.root / PAPER_BOOK_MARKER).read_text(encoding="utf-8")) == BINDING


def test_a_run_on_the_epoch_only_opens_the_book(paper: Paper) -> None:
    head = head_of(paper, 60)

    assert (head.session_index, head.opened_only, head.settled) == (0, True, False)
    assert paper.store.sessions() == (EPOCH,)


def test_every_run_opens_the_book_again_and_the_same_opening_writes_nothing(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    before = files(paper)

    head = head_of(paper, 62)

    assert files(paper) == before
    assert head.snapshot.session_index == 1
    assert head.held == ()
    assert head.settled is False


def changed(change: str) -> tuple[SurvivorRegistration, SessionOrdinals]:
    """The registration with its capital or its epoch changed, and the ordinals it counts by."""
    if change == "capital":
        return (
            SurvivorRegistration(book=book_config(capital="300000"), adapter=REGISTRATION.adapter),
            ORDINALS,
        )
    return (
        SurvivorRegistration(book=CONFIG, adapter=adapter(epoch_session=SESSIONS[59].isoformat())),
        SessionOrdinals(CALENDAR, SESSIONS[59]),
    )


@pytest.mark.parametrize(("change", "field"), [("capital", "config_digest"), ("epoch", "epoch")])
def test_a_run_whose_registration_differs_from_the_book_held_is_refused_by_its_marker(
    panel: PanelStore, paper: Paper, change: str, field: str
) -> None:
    run_day(panel, paper, 61)
    before = files(paper)
    registration, ordinals = changed(change)

    with pytest.raises(SurvivorDayError) as refused:
        head_of(paper, 62, registration, ordinals)

    assert refused.value.step == "paper head"
    assert field in str(refused.value)
    assert files(paper) == before


@pytest.mark.parametrize(("change", "named"), [("capital", "cash"), ("epoch", "epoch")])
def test_every_run_opens_the_book_and_refuses_an_opening_its_marker_does_not_catch(
    panel: PanelStore, paper: Paper, change: str, named: str
) -> None:
    run_day(panel, paper, 61)
    registration, ordinals = changed(change)
    (paper.root / PAPER_BOOK_MARKER).unlink()
    bind(  # a marker that agrees with the run and not with the book: restored from elsewhere
        paper.root,
        config_digest=registration.digest,
        epoch_session=registration.adapter.epoch_session,
    )
    before = files(paper)

    with pytest.raises(SurvivorDayError) as refused:
        head_of(paper, 62, registration, ordinals)

    assert refused.value.step == "paper head"
    assert isinstance(refused.value.__cause__, PaperStateError)
    assert named in str(refused.value)
    assert files(paper) == before


def test_a_changed_registration_over_the_same_book_is_refused_by_the_marker(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    before = files(paper)

    with pytest.raises(SurvivorDayError) as refused:
        head_of(paper, 62, config_id="rcf_another_registration_xx")

    assert refused.value.step == "paper head"
    assert "config_id" in str(refused.value)
    assert "rcf_another_registration_xx" in str(refused.value)
    assert files(paper) == before


def test_a_held_book_whose_marker_is_lost_is_refused_and_never_bound_again(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    (paper.root / PAPER_BOOK_MARKER).unlink()
    before = files(paper)

    for config_id in (CONFIG_ID, "rcf_another_registration_xx"):
        with pytest.raises(SurvivorDayError) as refused:
            head_of(paper, 62, config_id=config_id)
        assert refused.value.step == "paper head"
        assert "no binding marker" in str(refused.value) and "restore" in str(refused.value)

    assert files(paper) == before


def _bound_head(
    paper: Paper,
    *,
    config_id: str = CONFIG_ID,
    registration_sha256: str = REGISTRATION_SHA256,
    lock: object = None,
) -> Any:
    return strategy_survivor_book.require_bound(
        state_store=paper.store,
        paper_directory=paper.root,
        registration=REGISTRATION,
        config_id=config_id,
        registration_sha256=registration_sha256,
        lock=paper.lock if lock is None else lock,
    )


def test_the_same_configuration_registered_again_is_another_book_refused_naming_both(
    panel: PanelStore, paper: Paper
) -> None:
    """Whole-branch seams review, finding 1: a re-registration of the same configuration -- a code
    fix under a bound path, a changed `settings` -- keeps the `config_id`, the book's digest and
    the epoch, and has another registration file. The marker binds that file's sha256, so the
    book is refused to it by both callers -- `require_bound`, reading only, before the day's
    first request, and `paper_head` -- naming the book's registration and this run's, and saying
    that the new one is another book with a runtime directory and an epoch of its own."""
    run_day(panel, paper, 61)
    before = files(paper)

    with pytest.raises(SurvivorDayError) as reading:
        _bound_head(paper, registration_sha256=REREGISTERED_SHA256)
    with pytest.raises(SurvivorDayError) as opening:
        head_of(paper, 62, registration_sha256=REREGISTERED_SHA256)

    for refused in (reading.value, opening.value):
        message = str(refused)
        assert refused.step == "paper head"
        assert REGISTRATION_SHA256 in message and REREGISTERED_SHA256 in message
        assert message.index(REGISTRATION_SHA256) < message.index(REREGISTERED_SHA256)
        assert "another book" in message and "runtime directory of its own" in message
        assert "epoch of its own" in message
        assert "V2-P7-006" in message
    assert files(paper) == before


def test_a_marker_that_binds_no_registration_sha256_is_refused_clearly(
    panel: PanelStore, paper: Paper
) -> None:
    """A marker in the shape an earlier build wrote -- `config_id`, digest and epoch, no
    `registration_sha256` -- is not taken for this registration's: it is refused by name, before
    and at the paper head, and left as it is. (None was ever written on a real disk.)"""
    run_day(panel, paper, 61)
    marker = paper.root / PAPER_BOOK_MARKER
    earlier = {key: value for key, value in BINDING.items() if key != "registration_sha256"}
    marker.write_text(json.dumps(earlier, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = files(paper)

    with pytest.raises(SurvivorDayError) as reading:
        _bound_head(paper)
    with pytest.raises(SurvivorDayError) as opening:
        head_of(paper, 62)

    for refused in (reading.value, opening.value):
        message = str(refused)
        assert refused.step == "paper head"
        assert "binds no `registration_sha256`" in message
        assert "earlier build" in message and "runtime directory of its own" in message
    assert files(paper) == before


PAPER_BINDING_KEYS: Final[frozenset[str]] = frozenset(
    {"schema_version", "config_id", "config_digest", "epoch_session", "registration_sha256"}
)
"""`survivor-paper-binding/v1`, exactly: the marker is compared whole (`_refuse_another_binding`),
so a key added, removed or renamed makes every marker on disk another registration's. Re-pinned
once, for `registration_sha256` (whole-branch seams review, finding 1), before any marker had been
written on a real disk -- which is why that change needed no migration."""


def test_the_markers_shape_is_pinned_so_that_a_change_needs_a_migration(tmp_path: Path) -> None:
    directory = tmp_path / "paper"
    bind(directory)

    written = json.loads((directory / PAPER_BOOK_MARKER).read_text(encoding="utf-8"))

    assert set(written) == PAPER_BINDING_KEYS, (
        f"the marker holds {sorted(written)}, pinned {sorted(PAPER_BINDING_KEYS)}: a change here "
        "makes stored documents unreadable: a migration is needed"
    )
    assert written["schema_version"] == strategy_survivor_book.PAPER_BINDING_SCHEMA
    assert strategy_survivor_book.PAPER_BINDING_SCHEMA == "survivor-paper-binding/v1"
    assert all(isinstance(value, str) for value in written.values())


def test_a_directory_with_no_book_and_no_marker_is_bound_by_nobody_and_left_as_it_is(
    paper: Paper,
) -> None:
    """`require_bound` is what the daily command reads before it pins the book's holdings -- before
    any request: no book and no marker is a book yet to be opened, and nothing is written."""
    before = tree(paper.root)

    assert _bound_head(paper) is None
    assert tree(paper.root) == before
    assert not (paper.root / PAPER_BOOK_MARKER).exists()


def test_a_held_book_bound_to_this_registration_is_handed_back_as_its_head(paper: Paper) -> None:
    head_of(paper, 61)
    before = files(paper)

    head = _bound_head(paper)

    assert head == paper_head_snapshot(paper)
    assert files(paper) == before


def paper_head_snapshot(paper: Paper) -> Any:
    from openalpha_cn.backtest.paper_state import held_head

    return held_head(paper.store)


@pytest.mark.parametrize("held", [True, False], ids=["a held book", "no book"])
def test_a_marker_of_another_registration_is_refused_reading_only_with_or_without_a_book(
    paper: Paper, held: bool
) -> None:
    """A marker that differs is another registration's paper directory whether or not a book is
    held under it: refused by name, before the day pins anything, and left as it is."""
    if held:
        head_of(paper, 61, config_id="rcf_another_registration_xx")
    else:
        bind(paper.root, config_id="rcf_another_registration_xx")
    before = tree(paper.root)

    with pytest.raises(SurvivorDayError) as refused:
        _bound_head(paper)

    assert refused.value.step == "paper head"
    assert "config_id" in str(refused.value)
    assert "rcf_another_registration_xx" in str(refused.value)
    assert tree(paper.root) == before


def test_a_held_book_without_its_marker_is_refused_reading_only(paper: Paper) -> None:
    head_of(paper, 61)
    (paper.root / PAPER_BOOK_MARKER).unlink()
    before = files(paper)

    with pytest.raises(SurvivorDayError) as refused:
        _bound_head(paper)

    assert refused.value.step == "paper head"
    assert "no binding marker" in str(refused.value)
    assert files(paper) == before


def test_the_binding_is_read_only_under_this_runs_writer_lock(paper: Paper) -> None:
    with pytest.raises(SurvivorDayError, match="does not hold the paper book's writer lock"):
        _bound_head(paper, lock=object())


def test_ordinals_counted_from_another_epoch_than_the_registered_one_are_refused(
    panel: PanelStore, paper: Paper
) -> None:
    other = SessionOrdinals(CALENDAR, SESSIONS[59])

    with pytest.raises(SurvivorDayError) as at_head:
        head_of(paper, 61, REGISTRATION, other)

    assert at_head.value.step == "paper head"
    assert EPOCH.isoformat() in str(at_head.value)
    assert paper.store.sessions() == ()
    assert not (paper.root / PAPER_BOOK_MARKER).exists()
    run_day(panel, paper, 61)
    before = files(paper)
    with pytest.raises(SurvivorDayError) as at_catch_up:
        catch_up_through(panel, paper, 62, ordinals=other)
    facts = read_facts(panel, 62, ())
    with pytest.raises(SurvivorDayError) as at_evening:
        evening_of(
            panel, paper, 62, facts=facts, screen=real_screen(facts), held=(), ordinals=other
        )
    assert at_catch_up.value.step == at_evening.value.step == "paper book"
    assert EPOCH.isoformat() in str(at_catch_up.value)
    assert files(paper) == before


def test_an_epoch_further_back_than_the_panel_can_catch_up_is_refused_when_the_book_opens(
    paper: Paper,
) -> None:
    tight = SurvivorRegistration(book=CONFIG, adapter=adapter(max_panel_catch_up_sessions=0))

    with pytest.raises(SurvivorDayError) as refused:
        head_of(paper, 62, tight)

    assert refused.value.step == "paper head"
    assert "max_panel_catch_up_sessions" in str(refused.value)
    assert paper.store.sessions() == ()
    assert not (paper.root / PAPER_BOOK_MARKER).exists()
    assert head_of(paper, 61, tight).session_index == 1
    assert head_of(paper, 64, tight).session_index == 4  # an open book catches up what it missed


def test_after_an_epoch_refusal_the_corrected_registration_opens_the_book(paper: Paper) -> None:
    tight = SurvivorRegistration(book=CONFIG, adapter=adapter(max_panel_catch_up_sessions=0))
    with pytest.raises(SurvivorDayError):
        head_of(paper, 62, tight)
    corrected = SurvivorRegistration(
        book=CONFIG,
        adapter=adapter(epoch_session=SESSIONS[61].isoformat(), max_panel_catch_up_sessions=0),
    )

    head = head_of(
        paper,
        62,
        corrected,
        SessionOrdinals(CALENDAR, SESSIONS[61]),
        config_id="rcf_corrected_registration_x",
    )

    assert (head.snapshot.session, head.session_index) == (SESSIONS[61], 1)
    binding = json.loads((paper.root / PAPER_BOOK_MARKER).read_text(encoding="utf-8"))
    assert (binding["config_id"], binding["epoch_session"]) == (
        "rcf_corrected_registration_x",
        SESSIONS[61].isoformat(),
    )


# --- the evening ---------------------------------------------------------------------------------


def test_the_first_evening_after_the_book_opens_is_the_initial_build(
    panel: PanelStore, paper: Paper
) -> None:
    day = run_day(panel, paper, 61)
    result = day.evening.result

    assert day.caught_up == ()
    assert result.outcome == "created"
    assert len(result.decision.entries) == CONFIG.holding_count
    assert {entry.reason for entry in result.decision.entries} == {"INITIAL_BUILD"}
    assert [order.side for order in result.trade_list.orders] == ["buy"] * CONFIG.holding_count
    assert result.snapshot.first_decision_session == 1
    assert day.evening.held_after == ()


def test_the_evening_settles_its_session_before_it_decides_on_the_book(
    panel: PanelStore, paper: Paper
) -> None:
    first = run_day(panel, paper, 61)
    bought = tuple(sorted(order.ts_code for order in first.evening.result.trade_list.orders))

    day = run_day(panel, paper, 62)
    result = day.evening.result

    assert day.head.held == ()  # pinned before tonight's open filled anything
    assert tuple(sorted(order.ts_code for order in result.settlement.filled)) == bought
    assert day.evening.held_after == bought
    assert result.decision.target_holdings == bought  # the decision saw the book settled
    assert result.decision.entries == ()
    assert result.trade_list.orders == ()


def test_one_missed_session_is_caught_up_before_the_evening(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)

    day = run_day(panel, paper, 63)

    assert (day.head.session_index, day.head.snapshot.session_index) == (3, 1)
    assert [item.snapshot.session for item in day.caught_up] == [SESSIONS[62]]
    assert [item.outcome for item in day.caught_up] == ["created"]
    caught = day.caught_up[0].snapshot
    assert caught.first_decision_session == 1  # nothing decided on the session caught up
    assert all(item.order.signal_session < 2 for item in caught.pending_orders)
    assert day.evening.result.outcome == "created"
    assert paper.store.sessions() == SESSIONS[60:64]


def test_three_missed_sessions_are_caught_up_oldest_first_before_the_evening(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)

    day = run_day(panel, paper, 65)

    assert [item.snapshot.session for item in day.caught_up] == list(SESSIONS[62:65])
    assert [item.snapshot.session_index for item in day.caught_up] == [2, 3, 4]
    assert all(item.outcome == "created" for item in day.caught_up)
    assert day.evening.result.snapshot.session_index == 5
    assert paper.store.sessions() == SESSIONS[60:66]


def test_a_catch_up_of_a_book_holding_nothing_reads_no_facts(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)  # ten buys pending, nothing held
    empty = ReadRecordingStore(panel.root)

    catch_up_through(empty, paper, 62)

    assert empty.read == SETTLEMENT_READS
    holding = ReadRecordingStore(panel.root)
    catch_up_through(holding, paper, 63)  # the ten are held now: the day's facts are read
    assert holding.read >= FACTS_ONLY_READS


def test_a_session_whose_facts_the_registered_coverage_refuses_is_still_caught_up(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    catch_up_through(panel, paper, COVERAGE_REFUSED - 1)
    held = held_codes(head_of(paper, COVERAGE_REFUSED).snapshot)
    assert held

    with pytest.raises(SurvivorDayError) as refused:
        read_facts(panel, COVERAGE_REFUSED, held)
    assert refused.value.step == "facts"
    assert isinstance(refused.value.__cause__, FactsCoverageError)

    (caught,) = catch_up_through(panel, paper, COVERAGE_REFUSED)

    assert caught.outcome == "created"
    assert caught.snapshot.session == SESSIONS[COVERAGE_REFUSED]
    assert held_codes(caught.snapshot) == held


def test_the_evening_reads_its_facts_again_when_the_catch_up_changed_the_holdings(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)

    day = run_day(panel, paper, 63)

    caught = held_codes(day.caught_up[-1].snapshot)
    assert day.head.held == () and len(caught) == 10
    assert day.facts.held == ()
    assert day.evening.facts.held == caught
    assert fingerprint(day.evening.facts) == fingerprint(
        read_day(panel, 63, held=caught, as_of=AS_OF)
    )
    assert day.evening.screen == real_screen(day.evening.facts)


def test_the_evening_takes_the_callers_facts_and_screen_when_the_holdings_did_not_change(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    run_day(panel, paper, 62)

    day = run_day(panel, paper, 63, screen_of=without(EX_RIGHTS))

    assert day.caught_up == ()
    assert day.evening.facts is day.facts
    assert day.evening.screen is day.screen


def test_a_holding_delisted_while_held_is_written_off_before_the_decision(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(
        panel,
        paper,
        61,
        screen_of=lambda facts: ranked(facts, (DELISTED, "600002.SH", "600003.SH", "600004.SH")),
    )

    day = run_day(panel, paper, 65)  # 62..64 caught up; DELISTED has no bar from 65 on

    held_before = held_codes(day.caught_up[-1].snapshot)
    assert held_before == tuple(sorted((DELISTED, "600002.SH", "600003.SH")))
    tonight = day.evening
    assert {item.ts_code: item.status for item in tonight.facts.held_status}[DELISTED] == (
        "delisted"
    )
    assert [item.ts_code for item in tonight.result.settlement.written_off] == [DELISTED]
    assert DELISTED not in tonight.held_after
    assert DELISTED not in {item.ts_code for item in tonight.result.decision.exits}
    assert DELISTED not in {order.ts_code for order in tonight.result.trade_list.orders}
    assert DELISTED not in tonight.result.decision.target_holdings
    written = [row for row in paper.ledger.list() if row.reason == WRITE_OFF_REASON]
    assert [(row.order.subject, row.execution) for row in written] == [(DELISTED, None)]


def test_a_held_name_is_sold_at_its_mark_in_the_ledgers_units_across_an_ex_rights_event(
    panel: PanelStore, paper: Paper
) -> None:
    for index in (61, 62, 63):
        run_day(panel, paper, index)

    day = run_day(panel, paper, EX_RIGHTS_FROM, screen_of=without(EX_RIGHTS))

    result = day.evening.result
    assert EX_RIGHTS in day.head.held
    real = close_in(day.facts, EX_RIGHTS)
    sell = next(
        order
        for order in result.trade_list.orders
        if order.side == "sell" and order.ts_code == EX_RIGHTS
    )
    held = {position.subject: position.quantity for position in result.snapshot.book.positions}
    assert sell.reference_price == real * 2  # its mark in the ledger's units, not its close
    assert sell.quantity == held[EX_RIGHTS]
    assert {mark.subject: mark.price for mark in result.settlement.book.marks}[EX_RIGHTS] == (
        real * 2
    )


def test_a_name_sold_tonight_is_priced_from_the_facts_when_it_is_bought_again_tonight(
    panel: PanelStore, paper: Paper
) -> None:
    registration = SurvivorRegistration(
        book=book_config(cooldown_sessions=0), adapter=REGISTRATION.adapter
    )
    sold = "600002.SH"
    run_day(panel, paper, 61, registration=registration)
    settled = run_day(panel, paper, 62, registration=registration)
    held = settled.evening.held_after
    assert sold in held
    others = [code for code in held if code != sold]
    run_day(
        panel,
        paper,
        63,
        registration=registration,
        screen_of=lambda facts: ranked(facts, others),
    )

    day = run_day(
        panel,
        paper,
        64,
        registration=registration,
        screen_of=lambda facts: ranked(facts, (sold, *others)),
    )

    result = day.evening.result
    assert sold in day.head.held  # pinned when the run began, sold at tonight's open
    assert sold in {order.ts_code for order in result.settlement.filled}
    assert [entry.ts_code for entry in result.decision.entries] == [sold]
    (buy,) = (order for order in result.trade_list.orders if order.side == "buy")
    assert (buy.ts_code, buy.reference_price) == (sold, close_in(day.facts, sold))


def test_every_settlement_of_the_run_is_observed_on_the_as_ofs_date_in_shanghai(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)

    day = run_day(panel, paper, 63)

    assert AS_OF.astimezone(UTC).date() < OBSERVED_ON  # a day later in Shanghai than in UTC
    assert AS_OF < LATER
    records = [item.settlement.runner_record for item in day.caught_up]
    records.append(day.evening.result.settlement.runner_record)
    assert len(records) == 2
    assert {record.observed_on for record in records} == {OBSERVED_ON.isoformat()}


def test_an_evening_run_again_on_its_pinned_inputs_writes_nothing_and_gives_its_plan_back(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    first = run_day(panel, paper, 63)  # 62 caught up, so the evening read its facts again
    before = files(paper)
    assert head_of(paper, 63).settled is True
    assert files(paper) == before

    again = evening_of(
        panel, paper, 63, facts=first.facts, screen=first.screen, held=first.head.held
    )

    assert again.result.outcome == "unchanged"
    assert again.result.trade_list == first.evening.result.trade_list
    assert again.result.trade_list.trade_list_id == first.evening.result.trade_list.trade_list_id
    assert again.result.decision == first.evening.result.decision
    assert again.result.snapshot == first.evening.result.snapshot
    assert again.held_after == first.evening.held_after
    assert files(paper) == before


def test_every_session_of_one_catch_up_is_settled_from_the_book_the_one_before_left(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(
        panel,
        paper,
        61,
        screen_of=lambda facts: ranked(facts, (DELISTED, "600002.SH", "600003.SH", "600004.SH")),
    )

    day = run_day(panel, paper, 66)  # 62..65 caught up in one call; DELISTED has no bar at 65

    assert [item.snapshot.session for item in day.caught_up] == list(SESSIONS[62:66])
    delisted_on = day.caught_up[-1]
    assert [item.ts_code for item in delisted_on.settlement.written_off] == [DELISTED]
    assert DELISTED not in held_codes(delisted_on.snapshot)


def test_an_evening_run_again_reads_the_book_its_session_settled_from_not_the_head(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    first = run_day(panel, paper, 62)  # the ten buys filled at 62's open: the head holds them
    before = files(paper)

    again = evening_of(
        panel, paper, 62, facts=first.facts, screen=first.screen, held=first.head.held
    )

    assert again.result.outcome == "unchanged"
    assert again.facts.held == first.evening.facts.held == ()
    assert again.facts is first.facts
    assert files(paper) == before


def test_the_evenings_second_read_has_no_coverage_floor(panel: PanelStore, paper: Paper) -> None:
    carry = {"max_carry_sessions": 1}
    probe = SurvivorRegistration(book=CONFIG, adapter=adapter(**carry, min_coverage=0.01))
    alone, halted = (read_facts(panel, 69, held, probe) for held in ((), (HALTED,)))
    assert halted.universe_size == alone.universe_size + 1  # a held halt keeps its carried row
    assert halted.scored_size == alone.scored_size
    floor = (
        alone.scored_size / alone.universe_size + halted.scored_size / halted.universe_size
    ) / 2
    tight = SurvivorRegistration(book=CONFIG, adapter=adapter(**carry, min_coverage=floor))
    with pytest.raises(SurvivorDayError):
        read_facts(panel, 69, (HALTED,), tight)  # the registered floor would refuse it
    run_day(
        panel,
        paper,
        61,
        registration=tight,
        screen_of=lambda facts: ranked(
            facts, (HALTED, "600002.SH", "600003.SH", "600004.SH"), eligible=(HALTED,)
        ),
    )

    day = run_day(panel, paper, 69, registration=tight)  # HALTED, halted from 67, is held

    assert HALTED in day.evening.facts.held
    assert day.evening.result.outcome == "created"
    assert HALTED in day.evening.result.decision.frozen
    assert HALTED in day.evening.held_after


def test_a_second_read_whose_screen_disagrees_with_the_callers_is_refused_writing_nothing(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    head = head_of(paper, 63)
    facts = read_facts(panel, 63, head.held)
    dropped = real_screen(facts).survivors[0].ts_code
    catch_up_through(panel, paper, 62)
    before = files(paper)

    with pytest.raises(SurvivorDayError) as refused:
        evening_of(panel, paper, 63, facts=facts, screen=without(dropped)(facts), held=head.held)

    assert refused.value.step == "paper book"
    assert dropped in str(refused.value) and "survivors" in str(refused.value)
    assert files(paper) == before


def test_the_evenings_market_is_read_for_the_book_its_session_settles_from(
    panel: PanelStore, paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    sold = "600002.SH"
    run_day(panel, paper, 61)
    run_day(panel, paper, 62)
    run_day(panel, paper, 63, screen_of=without(sold))  # sold at 64's open
    asked: dict[date, tuple[str, ...]] = {}
    read = strategy_survivor_book.settlement_market

    def recording(store: PanelStore, **arguments: Any) -> Any:
        asked[arguments["session"]] = tuple(arguments["names"])
        return read(store, **arguments)

    monkeypatch.setattr(strategy_survivor_book, "settlement_market", recording)

    day = run_day(panel, paper, 65)

    settled_from = day.caught_up[-1].snapshot
    assert sold in day.head.held and sold not in held_codes(settled_from)
    assert asked[SESSIONS[65]] == settlement_names(settled_from)


def test_an_evening_given_facts_of_other_holdings_or_another_day_is_refused(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    run_day(panel, paper, 62)
    head = head_of(paper, 63)
    assert head.held
    before = files(paper)

    for facts in (read_facts(panel, 63, ()), read_facts(panel, 62, head.held)):
        with pytest.raises(SurvivorDayError) as refused:
            evening_of(panel, paper, 63, facts=facts, screen=real_screen(facts), held=head.held)
        assert refused.value.step == "paper book"
        assert "pinned" in str(refused.value)

    assert files(paper) == before


def test_an_evening_with_sessions_missing_before_it_is_refused_until_they_are_caught_up(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    facts = read_facts(panel, 63, ())
    before = files(paper)

    with pytest.raises(SurvivorDayError) as refused:
        evening_of(panel, paper, 63, facts=facts, screen=real_screen(facts), held=())

    assert refused.value.step == "paper book"
    assert "caught up first" in str(refused.value)
    assert files(paper) == before


def test_settle_only_settles_a_session_and_a_run_that_stopped_after_it_finishes_writing_nothing(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    run_day(panel, paper, 62)  # ten held from here: settling 63 reads its facts

    settled = settle_only_at(panel, paper, 63)

    assert [(item.snapshot.session, item.outcome) for item in settled] == [
        (SESSIONS[63], "created")
    ]
    assert settled[0].snapshot.first_decision_session == 1
    assert all(item.order.signal_session < 3 for item in settled[0].snapshot.pending_orders)
    before = files(paper)  # the run stopped here, before its journal: it is run again
    reported: list[CatchUpResult] = []

    again = settle_only_at(panel, paper, 63, on_settled=reported.append)

    assert [item.outcome for item in again] == ["unchanged"]
    assert again[0].snapshot == settled[0].snapshot
    assert again[0].settlement.book == settled[0].settlement.book
    assert reported == list(again)
    assert files(paper) == before


def test_settle_only_never_settles_again_a_session_its_evening_settled_with_orders(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)  # its evening planned ten buys
    before = files(paper)

    with pytest.raises(SurvivorDayError) as refused:
        settle_only_at(panel, paper, 61)
    with pytest.raises(SurvivorDayError) as epoch:
        settle_only_at(panel, paper, 60)

    assert refused.value.step == "paper book"
    assert isinstance(refused.value.__cause__, PaperStateError)
    assert "an evening" in str(refused.value)
    assert "epoch" in str(epoch.value)
    assert files(paper) == before


def catch_up_again_at(
    panel: PanelStore,
    paper: Paper,
    indices: Sequence[int],
    *,
    on_settled: Callable[[CatchUpResult], None] | None = None,
) -> tuple[CatchUpResult, ...]:
    """Derive again the sessions of ordinals `indices` a catch-up settled."""
    return strategy_survivor_book.catch_up_again(
        panel=panel,
        state_store=paper.store,
        ledger=paper.ledger,
        registration=REGISTRATION,
        ordinals=ORDINALS,
        calendar=CALENDAR,
        indices=indices,
        as_of=AS_OF,
        make_portfolio=make_portfolio,
        lock=paper.lock,
        on_settled=on_settled,
        transform=IDENTITY,
    )


def test_caught_up_sessions_whose_journal_was_lost_are_derived_again_writing_nothing(
    panel: PanelStore, paper: Paper
) -> None:
    """D5 joint review, Important 3: a catch-up committed sessions 62 and 63 and its run stopped
    before the daily journal said so. Derived again, each is what it settled -- C3 answers
    `unchanged` and writes nothing -- and is reported through `on_settled`, oldest first, as a
    session just settled is."""
    run_day(panel, paper, 61)
    settled = catch_up_through(panel, paper, 63)
    before = files(paper)
    reported: list[CatchUpResult] = []

    again = catch_up_again_at(panel, paper, (2, 3), on_settled=reported.append)

    assert [(item.snapshot.session, item.outcome) for item in again] == [
        (SESSIONS[62], "unchanged"),
        (SESSIONS[63], "unchanged"),
    ]
    assert [item.snapshot for item in again] == [item.snapshot for item in settled]
    assert [item.settlement.book for item in again] == [item.settlement.book for item in settled]
    assert [item.settlement.filled for item in again] == [
        item.settlement.filled for item in settled
    ]
    assert reported == list(again)
    assert files(paper) == before


def test_a_session_an_evening_decided_is_not_derived_again_as_a_catch_up(
    panel: PanelStore, paper: Paper
) -> None:
    """Session 61's evening planned ten buys: it is its own day's to document, and C3 would refuse
    it as a catch-up. Asked for, it is passed over -- nothing derived, nothing reported, nothing
    written -- and a caught-up session after it is still derived again."""
    run_day(panel, paper, 61)
    catch_up_through(panel, paper, 62)
    before = files(paper)
    reported: list[CatchUpResult] = []

    again = catch_up_again_at(panel, paper, (1, 2), on_settled=reported.append)

    assert [(item.snapshot.session, item.outcome) for item in again] == [
        (SESSIONS[62], "unchanged")
    ]
    assert reported == list(again)
    assert files(paper) == before


def test_a_session_with_no_snapshot_is_not_derived_again_but_refused(
    panel: PanelStore, paper: Paper
) -> None:
    """A session the book has not settled is the catch-up's to settle, never a derivation's: the
    derivation refuses it by name, as a stall of the paper book, and settles nothing."""
    run_day(panel, paper, 61)
    before = files(paper)

    with pytest.raises(CatchUpStalledError) as refused:
        catch_up_again_at(panel, paper, (2,))

    assert refused.value.step == "paper book"
    assert refused.value.caught_up == ()
    assert "not settled" in str(refused.value)
    assert files(paper) == before


def test_another_configuration_is_refused_at_every_step_before_anything_is_written(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    before = files(paper)

    with pytest.raises(SurvivorDayError) as at_head:
        head_of(paper, 62, OTHER_BOOK)
    with pytest.raises(SurvivorDayError) as at_catch_up:
        catch_up_through(panel, paper, 62, registration=OTHER_BOOK)
    facts = read_facts(panel, 62, (), OTHER_BOOK)
    with pytest.raises(SurvivorDayError) as at_evening:
        evening_of(
            panel,
            paper,
            62,
            facts=facts,
            screen=real_screen(facts, OTHER_BOOK),
            held=(),
            registration=OTHER_BOOK,
        )

    assert at_head.value.step == "paper head"
    assert "config_digest" in str(at_head.value)  # the marker refuses it before the opening
    assert at_catch_up.value.step == at_evening.value.step == "paper book"
    for refused in (at_catch_up, at_evening):
        assert isinstance(refused.value.__cause__, PaperStateError)
    assert files(paper) == before


def test_a_holding_the_book_cannot_settle_stalls_the_catch_up_by_name(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(
        panel,
        paper,
        61,
        screen_of=lambda facts: ranked(
            facts, (UNREGISTERED, "600002.SH", "600003.SH", "600004.SH"), eligible=(UNREGISTERED,)
        ),
    )
    reported: list[CatchUpResult] = []

    with pytest.raises(CatchUpStalledError) as refused:
        catch_up_through(panel, paper, 64, on_settled=reported.append)

    stalled = refused.value
    assert stalled.step == "paper book"
    assert UNREGISTERED in str(stalled) and "not_in_registry" in str(stalled)
    assert SESSIONS[63].isoformat() in str(stalled)
    # 62 settled (bought at its open; nothing held while it settled) and is kept, and said so
    assert [item.snapshot.session for item in stalled.caught_up] == [SESSIONS[62]]
    assert stalled.caught_up == tuple(reported)
    assert paper.store.sessions() == SESSIONS[60:63]


def test_each_caught_up_session_is_reported_as_it_commits_and_a_reporting_fault_undoes_nothing(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    reported: list[CatchUpResult] = []

    def report(result: CatchUpResult) -> None:
        reported.append(result)
        if len(reported) == 2:
            raise TypeError("the journal could not be written")

    with pytest.raises(TypeError, match="the journal could not be written"):
        catch_up_through(panel, paper, 64, on_settled=report)

    assert [item.snapshot.session for item in reported] == [SESSIONS[62], SESSIONS[63]]
    assert paper.store.sessions() == SESSIONS[60:64]  # 63 was committed before its report
    rest = catch_up_through(panel, paper, 64)
    assert [item.snapshot.session for item in rest] == [SESSIONS[64]]


def test_the_ledger_conflict_and_the_stores_faults_are_named_refusals(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    state = PortfolioState(as_of=SESSIONS[62], cash=D("1.00"))
    paper.ledger.append(
        PortfolioTransition(
            status="rejected",
            order=PortfolioOrder(
                order_id="tor_another_writer@2026-04-01",
                subject="600002.SH",
                side="buy",
                quantity=100,
            ),
            before=state,
            after=state,
            reason="insufficient cash",
        )
    )

    with pytest.raises(SurvivorDayError) as conflict:
        catch_up_through(panel, paper, 62)

    assert conflict.value.step == "paper book"
    assert isinstance(conflict.value.__cause__, PaperLedgerConflictError)
    held = next((paper.root / "snapshots").glob(f"{SESSIONS[61].isoformat()}.*.json"))
    held.with_name(f"{SESSIONS[61].isoformat()}.pbs_{'0' * 24}.json").write_bytes(held.read_bytes())
    with pytest.raises(SurvivorDayError) as damaged:
        head_of(paper, 62)
    assert damaged.value.step == "paper head"
    assert isinstance(damaged.value.__cause__, PaperStateStoreError)
    assert "ledger" in str(damaged.value) and "one backup" in str(damaged.value)


def test_a_programming_error_reaches_the_caller_as_itself(panel: PanelStore, paper: Paper) -> None:
    day = run_day(panel, paper, 61)

    def typed(ledger: PortfolioLedger) -> PaperPortfolio:
        raise TypeError("a bug in the factory")

    def asserted(ledger: PortfolioLedger) -> PaperPortfolio:
        raise AssertionError("an invariant broken")

    with pytest.raises(TypeError, match="a bug in the factory"):
        catch_up_through(panel, paper, 62, factory=typed)
    with pytest.raises(AssertionError, match="an invariant broken"):
        catch_up_through(panel, paper, 62, factory=asserted)
    facts = read_facts(panel, 62, day.evening.held_after)
    with pytest.raises(AssertionError, match="an invariant broken"):
        evening_of(
            panel,
            paper,
            62,
            facts=facts,
            screen=real_screen(facts),
            held=day.evening.held_after,
            factory=asserted,
        )
    with pytest.raises(TypeError, match="time zone"):
        evening_of(
            panel,
            paper,
            62,
            facts=facts,
            screen=real_screen(facts),
            held=day.evening.held_after,
            as_of=AS_OF.astimezone(UTC).replace(tzinfo=None),
        )
    assert paper.store.sessions() == SESSIONS[60:62]


# --- one writer ----------------------------------------------------------------------------------


def test_a_second_run_is_refused_while_the_first_holds_the_paper_book(
    panel: PanelStore, tmp_path: Path
) -> None:
    root = tmp_path / "paper"
    with opened_paper(root) as paper:
        run_day(panel, paper, 61)
    holding, release = threading.Event(), threading.Event()
    first: list[Day] = []
    faults: list[BaseException] = []

    def first_run() -> None:
        try:
            with opened_paper(root) as theirs:
                holding.set()
                release.wait(timeout=60)
                first.append(run_day(panel, theirs, 62))
        except BaseException as error:  # reported below, never swallowed
            faults.append(error)
            holding.set()

    thread = threading.Thread(target=first_run)
    thread.start()
    try:
        assert holding.wait(timeout=60)
        before = tree(root)
        with pytest.raises(PaperBookBusyError) as refused, opened_paper(root) as ours:
            run_day(panel, ours, 62)
        assert tree(root) == before
    finally:
        release.set()
        thread.join(timeout=120)

    assert faults == []
    assert isinstance(refused.value, SurvivorDayError)
    assert PAPER_BOOK_LOCK in str(refused.value)
    assert [day.evening.result.outcome for day in first] == ["created"]
    with opened_paper(root) as again:  # released with its holder
        assert head_of(again, 62).settled is True


def test_the_paper_book_is_run_only_under_the_writer_lock_this_run_still_holds(
    panel: PanelStore, tmp_path: Path
) -> None:
    root = tmp_path / "paper"
    with opened_paper(root) as paper:
        run_day(panel, paper, 61)
    released = paper  # its lock was let go with the block
    facts = read_facts(panel, 62, ())
    before = tree(root)
    calls: dict[str, Callable[[Paper], object]] = {
        "paper head": lambda held: head_of(held, 62),
        "catch-up": lambda held: catch_up_through(panel, held, 62),
        "settle-only": lambda held: settle_only_at(panel, held, 62),
        "evening": lambda held: evening_of(
            panel, held, 62, facts=facts, screen=real_screen(facts), held=()
        ),
    }

    for name, call in calls.items():
        with pytest.raises(SurvivorDayError) as refused:
            call(released)
        assert "writer lock" in str(refused.value), name
        with pytest.raises(SurvivorDayError):
            call(dataclasses.replace(released, lock=None))
    with opened_paper(root) as paper:
        (root / PAPER_BOOK_LOCK).unlink()  # another run may now lock a new file of that name
        (root / PAPER_BOOK_LOCK).touch()
        assert paper.lock.held is False
        with pytest.raises(SurvivorDayError) as replaced:
            head_of(paper, 62)
        assert "writer lock" in str(replaced.value)
    with opened_paper(tmp_path / "elsewhere") as elsewhere:
        with pytest.raises(SurvivorDayError) as foreign:
            head_of(dataclasses.replace(released, lock=elsewhere.lock), 62)
        assert "elsewhere" in str(foreign.value)
    assert {name: content for name, content in tree(root).items()} == before


def test_the_lock_is_held_across_processes_and_released_with_its_holder(tmp_path: Path) -> None:
    directory = tmp_path / "paper"
    program = (
        "import sys\n"
        "from pathlib import Path\n"
        "from openalpha_cn.strategy_survivor_book import paper_book_lock\n"
        "with paper_book_lock(Path(sys.argv[1])):\n"
        "    print('held', flush=True)\n"
        "    sys.stdin.read()\n"
    )
    path = os.pathsep.join(
        part for part in (str(ROOT / "src"), os.environ.get("PYTHONPATH")) if part
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", program, str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": path},
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        with pytest.raises(PaperBookBusyError), paper_book_lock(directory):
            pass
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=120)

    assert holder.returncode == 0
    with paper_book_lock(directory):
        assert (directory / PAPER_BOOK_LOCK).exists()


# --- the entry veto in the evening (`V2-P7-027`) -------------------------------------------------

VETOED: Final[str] = "600002.SH"
"""The best of the three survivors the veto tests hand the frame's book: 600002.SH."""


def veto_screen(facts: FactsDay) -> ScreenResult:
    """Three survivors best first, so a vetoed rank 1 leaves the next two to be drawn: the
    frame's real screen with `VETOED` on top."""
    return ranked(facts, (VETOED, "600003.SH", "600004.SH"))


def journal_document(
    day: Day, *, book: SurvivorBookConfig, as_of: datetime = AS_OF
) -> dict[str, Any]:
    """`day`'s evening as the daily journal files it: `evening_document`'s `result` for the book
    the day ran under, read at `as_of`."""
    return evening_document(
        session=day.facts.session,
        session_index=day.evening.result.snapshot.session_index,
        as_of=as_of,
        facts=day.facts,
        screen=day.screen,
        evening=day.evening.result,
        catch_ups=day.caught_up,
        ordinals=ORDINALS,
        book=book,
    )


def journal_result_bytes(document: Mapping[str, Any]) -> bytes:
    """The bytes `daily_selection.write_journal` writes for a `result`: sorted keys, a two-space
    indent, a trailing newline, non-ASCII unescaped -- the journal's own serialisation, so equal
    here is equal in the file."""
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


class VetoReads:
    """`panel_limit_events.vetoed_on` as `strategy_survivor_book.evening` calls it: the codes a
    session is answered with and -- for a session in `restated` -- the panel's own refusal naming
    that session. Every call is recorded, with the calendar and exchange it was asked with.

    The real rule's readings are the panel plane's (`tests/integration/panel/test_veto_window.py`
    pins that a restated session makes the window read raise by name). What these tests pin is the
    book's half: it asks the rule for the signal day once, hands the answer to `run_evening`, and
    refuses rather than deciding when the read refuses.
    """

    def __init__(self, answers: Mapping[date, frozenset[str]]) -> None:
        self.answers = dict(answers)
        self.restated: frozenset[date] = frozenset()
        self.calls: list[date] = []
        self.arguments: list[tuple[TradingCalendar, str]] = []

    def __call__(
        self,
        store: PanelStore,
        *,
        session: date,
        calendar: TradingCalendar,
        exchange: str,
    ) -> frozenset[str]:
        self.calls.append(session)
        self.arguments.append((calendar, exchange))
        if session in self.restated:
            raise PanelStorageError(
                f"{session.isoformat()} cannot be read at its own 16:30: the panel holds no row "
                "this read can see and keeps no earlier version -- the session was restated "
                "after it, so the evening's vetoed set cannot be reproduced"
            )
        return self.answers[session]


def _must_not_read(name: str) -> Callable[..., frozenset[str]]:
    def read(*_args: Any, **_keywords: Any) -> frozenset[str]:
        raise AssertionError(
            f"a book whose entry_veto is 'none' must not read the entry veto: {name} was called"
        )

    return read


BASE_JOURNAL_SHA256: Final[str] = "46bda71966547c112d217d6bd1d826a7419e387a766698dc6ac504b9ee4f7763"
"""The SHA-256 of the v1 book's journal `result` bytes, measured on the base commit `cc2b44b`."""
BASE_SUMMARY_LINES: Final[tuple[str, ...]] = (
    "session            2026-03-31 (as of 2026-04-18T02:00:00+08:00)",
    "ORDERS FOR THE OPEN OF D+1 (call auction 09:15), 2026-04-01 -- evening created",
    "  real closes are references, not limit prices; the paper book fills at the open",
    "  at the 09:15 call auction an order carries a limit price: this system assumes that a buy placed at its limit-up price and a sell placed at its limit-down price take the single opening price -- confirm with your broker. Each limit below is computed from the real close (main board 10%; ST 5% before 2025-07-07, 10% from it); the exchange's published limit governs",  # noqa: E501
    "  BUY 600002.SH 1200 shares, about 32,400 yuan -- reference: real close 27.0 INITIAL_BUILD, live through 2026-04-03; limit-up 29.70, 35,640.00 yuan at it",  # noqa: E501
    "  BUY 600009.SH 1000 shares, about 34,000 yuan -- reference: real close 34.0 INITIAL_BUILD, live through 2026-04-03; limit-up 37.40, 37,400.00 yuan at it",  # noqa: E501
    "  BUY 600005.SH 1100 shares, about 33,000 yuan -- reference: real close 30.0 INITIAL_BUILD, live through 2026-04-03; limit-up 33.00, 36,300.00 yuan at it",  # noqa: E501
    "  BUY 600006.SH 1100 shares, about 34,100 yuan -- reference: real close 31.0 INITIAL_BUILD, live through 2026-04-03; limit-up 34.10, 37,510.00 yuan at it",  # noqa: E501
    "  BUY 600012.SH 900 shares, about 33,300 yuan -- reference: real close 37.0 INITIAL_BUILD, live through 2026-04-03; limit-up 40.70, 36,630.00 yuan at it",  # noqa: E501
    "  BUY 600007.SH 1000 shares, about 32,000 yuan -- reference: real close 32.0 INITIAL_BUILD, live through 2026-04-03; limit-up 35.20, 35,200.00 yuan at it",  # noqa: E501
    "  BUY 000012.SZ 1600 shares, about 33,600 yuan -- reference: real close 21.0 INITIAL_BUILD, live through 2026-04-03; limit-up 23.10, 36,960.00 yuan at it",  # noqa: E501
    "  BUY 600004.SH 1200 shares, about 34,800 yuan -- reference: real close 29.0 INITIAL_BUILD, live through 2026-04-03; limit-up 31.90, 38,280.00 yuan at it",  # noqa: E501
    "  BUY 600003.SH 1200 shares, about 33,600 yuan -- reference: real close 28.0 INITIAL_BUILD, live through 2026-04-03; limit-up 30.80, 36,960.00 yuan at it",  # noqa: E501
    "  BUY 600014.SH 400 shares, about 15,600 yuan -- reference: real close 39.0 INITIAL_BUILD, live through 2026-04-03; limit-up 42.90, 17,160.00 yuan at it",  # noqa: E501
    "funding            cash at 09:15 350,000.00 yuan; buys freeze 348,040.00 yuan at their limit-up prices (1.10x the reference, the main board's band); sells tonight release about 0.00 yuan after the auction, not before it",  # noqa: E501
    "  600014.SH: 400 of the 800 shares the equal share asks; 17,160.00 yuan short at the limit-up price",  # noqa: E501
    "  the paper book assumes every order above is placed and fills at the open; your account follows the book only if you did exactly that",  # noqa: E501
    "reconciliation     no account record for 2026-03-31: the paper book's assumption that every printed order was placed stands unverified",  # noqa: E501
    "carried orders     none",
    "unfunded           none",
    "unaffordable       none",
    "deferred exits     none",
    "near exit          none",
    "frozen             none",
    "watch list         600008.SH, 600001.SH, 600013.SH, 600010.SH, 000013.SZ (today's draw, not a promise for tomorrow)",  # noqa: E501
    "illiquid exits     none",
    "positions          0 holdings (ledger qty is in the ledger's units and a ledger/real ratio is the adjustment since the buy, cash dividends included -- neither is a share count; every sale is SELL ENTIRE POSITION)",  # noqa: E501
    "book               equity 350000.00, cash 350000.00, gross exposure 0.000000, 0 holdings; ledger equity is not evidence",  # noqa: E501
    "settled            filled 0, expired 0, cancelled 0, rejected 0, not attempted 0, written off 0",  # noqa: E501
    "catch-up           none",
    "diagnostics        how the run read its data -- nothing below is an order",
    "ids                session index 1; trade list trl_254b82d6f05195a394368b44; paper snapshot pbs_263d12d1382dfdd05d50e0fe",  # noqa: E501
    "facts              rows 42, universe on main 39, scored 38 (coverage 0.974359); book holdings before tonight's fills 0",  # noqa: E501
    "  no_bar_within_carry 1, not_listed_with_bar 1, unrecognised_board 0, no_listing_row 1, suspended_without_bar 0, delisting 1",  # noqa: E501
    "  ST 2 (by name 1, pending 0, by band 1)",
    "  name unknown -> ordinary 24 (never renamed 24, before first record 0)",
    "screen             assessed 42, eligible 24, survivors 20",
    "  reasons board_not_allowed 3, illiquid 10, listing_too_recent 2, missing_factor_value 1, special_treatment 3, suspended 0",  # noqa: E501
    "prediction         not in this document",
    "tushare requests   not recorded in this document",
    "units              fees after an adjustment event are charged on the scaled notional; filled_price and marks are in ledger units (the real price times the adjustment ratio); transfer_fee is slippage",  # noqa: E501
    "wording            candidates of a registered research configuration; no order was placed; ledger equity is not evidence; with N=10, single-year results are mostly luck",  # noqa: E501
)
"""The v1 book's printed summary, measured on the base commit `cc2b44b`, line for line."""


def test_a_v2_books_evening_vetoes_its_top_survivor_and_buys_the_next_one(
    panel: PanelStore, paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan A in the evening: the v2 book's read of the rule names the frame's best survivor, so
    the initial build buys the next two and the journal files the rule with the code it blocked."""
    reads = VetoReads({SESSIONS[61]: frozenset({VETOED})})
    monkeypatch.setattr(strategy_survivor_book, "vetoed_on", reads)

    day = v2_day(panel, paper, 61, screen_of=veto_screen)
    result = day.evening.result

    assert result.vetoed == frozenset({VETOED})
    assert {entry.ts_code for entry in result.decision.entries} == {"600003.SH", "600004.SH"}
    assert {order.ts_code for order in result.trade_list.orders} == {"600003.SH", "600004.SH"}
    # The rule is asked once, for the signal day, with the registered calendar and exchange.
    assert reads.calls == [SESSIONS[61]]
    assert reads.arguments == [(CALENDAR, V2_REGISTRATION.adapter.exchange)]
    document = journal_document(day, book=V2_BOOK)
    assert document["paper"]["entry_veto"] == {"rule": VETO_RULE_ID, "vetoed": [VETOED]}


def test_a_rerun_without_a_restatement_reproduces_the_vetoed_evening(
    panel: PanelStore, paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-N3a's first half: the evening settled at one `as_of` is run again at a later one with the
    window un-restated. It is the `unchanged` path -- nothing written -- and the set it derives is
    the first run's, so a rerun can never change what the journal said was vetoed."""
    reads = VetoReads({SESSIONS[61]: frozenset({VETOED})})
    monkeypatch.setattr(strategy_survivor_book, "vetoed_on", reads)
    first = v2_day(panel, paper, 61, screen_of=veto_screen)
    before = files(paper)

    again = v2_day(panel, paper, 61, as_of=LATER, screen_of=veto_screen)

    assert again.evening.result.outcome == "unchanged"
    assert again.evening.result.vetoed == first.evening.result.vetoed == frozenset({VETOED})
    assert again.evening.result.snapshot == first.evening.result.snapshot
    assert files(paper) == before
    assert reads.calls == [SESSIONS[61], SESSIONS[61]]


def test_a_rerun_after_a_restatement_refuses_rather_than_changing_the_vetoed_evening(
    panel: PanelStore, paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-N3a's second half: a session inside the window restated after its own 16:30 has no
    earlier version to read, so the window read refuses by name. The book refuses under `paper
    book`, naming the session, before anything is written: the first evening's snapshot and the
    journal's `result` bytes are unchanged."""
    reads = VetoReads({SESSIONS[61]: frozenset({VETOED})})
    monkeypatch.setattr(strategy_survivor_book, "vetoed_on", reads)
    first = v2_day(panel, paper, 61, screen_of=veto_screen)
    first_journal = journal_result_bytes(journal_document(first, book=V2_BOOK))
    before = files(paper)
    reads.restated = frozenset({SESSIONS[61]})

    with pytest.raises(SurvivorDayError) as refused:
        v2_day(panel, paper, 61, as_of=LATER, screen_of=veto_screen)

    message = str(refused.value)
    assert refused.value.step == "paper book"
    assert SESSIONS[61].isoformat() in message
    assert "PanelStorageError" in message
    # Finding 2, fix round 1: a `PanelStorageError` is not established to be a restatement --
    # a missing or damaged partition refuses the same way -- so the refusal names the likely
    # cause and the others rather than asserting the one.
    assert "most likely cause is a session restated" in message
    assert "missing or damaged partition" in message
    assert files(paper) == before
    assert paper.store.sessions()[-1] == SESSIONS[61]
    assert load_snapshot(paper.store, SESSIONS[61]) == first.evening.result.snapshot
    assert journal_result_bytes(journal_document(first, book=V2_BOOK)) == first_journal


def test_the_base_book_evening_and_journal_are_unchanged(
    panel: PanelStore, paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-N4: with `entry_veto == "none"` nothing new is read, filed or printed. The panel reader
    and the rule are spies that fail if asked, `EveningResult.vetoed` is empty, the paper section
    carries no `entry_veto` key, and the journal's `result` bytes and the printed lines are the
    literal measured on the base commit `cc2b44b`, before this task's code existed."""
    monkeypatch.setattr(panel_limit_events, "limit_events_on", _must_not_read("limit_events_on"))
    monkeypatch.setattr(panel_limit_events, "vetoed_on", _must_not_read("vetoed_on"))
    monkeypatch.setattr(
        strategy_survivor_book, "vetoed_on", _must_not_read("vetoed_on"), raising=False
    )

    day = run_day(panel, paper, 61)

    assert day.evening.result.vetoed == frozenset()
    document = journal_document(day, book=REGISTRATION.book)
    assert "entry_veto" not in document["paper"]
    assert hashlib.sha256(journal_result_bytes(document)).hexdigest() == BASE_JOURNAL_SHA256
    assert summary_lines(document) == list(BASE_SUMMARY_LINES)


# --- fix round 1: the veto window across New Year (review finding 1) -----------------------------

LEGACY_EVENT: Final[str] = "600001.SH"
"""One frame security: its 2025-12-31 bar is scripted as a broken board, the window's one event."""
VETO_ACROSS_NEW_YEAR: Final[date] = SESSIONS[1]
"""2026-01-06, the second session of the frame's year. Its five-session veto window reaches
2025-12-29, which the evening's own calendar -- `range(epoch.year, session.year + 1)`, what
`daily_selection._survivor_calendar` builds -- never holds. `_vetoed_for` reads the year before
from the panel for a v2 book, and only for a v2 book."""


def two_year_panel(root: Path) -> PanelStore:
    """The frame's 2026 panel extended with 2025 through the real writers: a full calendar, and
    every open session's `daily`, `daily_basic` and `stk_limit` cross sections.

    The 2026 half is `write_panel`'s own (the frame's generated fixture). The 2025 half is built
    here because no generator carries a second partition year (`tests/panel_fixtures.py` says
    so): 1 January 2025 is closed, every other weekday is open, prices are quiet except
    `LEGACY_EVENT`'s 2025-12-31 broken board -- high at the published up limit, close below it --
    which is the window's one limit event.
    """
    store = write_panel(root / "two-year", BOOK)
    days = tuple(
        CalendarDay(
            calendar_date=day,
            is_trading=day.weekday() < 5 and day != date(2025, 1, 1),
        )
        for day in (date(2025, 1, 1) + timedelta(days=offset) for offset in range(365))
    )
    previous: list[str | None] = []
    seen: date | None = None
    for day in days:
        previous.append(seen.isoformat() if seen is not None else None)
        if day.is_trading:
            seen = day.calendar_date
    write_trading_calendar(
        store,
        batch(
            TRADING_CALENDAR_DATASET,
            subjects=[EXCHANGE] * len(days),
            columns=[
                PanelColumn(
                    "cal_date", "string", tuple(day.calendar_date.isoformat() for day in days)
                ),
                PanelColumn("is_open", "boolean", tuple(day.is_trading for day in days)),
                PanelColumn("pretrade_date", "string", tuple(previous)),
            ],
            event_time=[midnight_shanghai(day.calendar_date) for day in days],
            available_time=[midnight_shanghai(date(2025, 1, 1))] * len(days),
        ),
    )
    calendar = build_trading_calendar(EXCHANGE, days)
    open_days = tuple(day.calendar_date for day in days if day.is_trading)
    securities = stored_securities(BOOK)
    pairs = [(day, code) for day in open_days for code in securities]
    broken = (date(2025, 12, 31), LEGACY_EVENT)

    def prices(day: date, code: str) -> tuple[float, float, float, float, float]:
        if (day, code) == broken:
            return (10.0, 11.0, 9.7, 9.8, 10.0)  # open, high, low, close, pre_close
        return (10.0, 10.0, 10.0, 10.0, 10.0)

    quoted = [prices(day, code) for day, code in pairs]
    available = [published_time(day) for day, _ in pairs]
    fetched = max(available)
    write_daily_panel(
        store,
        bars=[
            batch(
                DAILY_DATASET,
                subjects=[code for _, code in pairs],
                columns=[
                    PanelColumn("trade_date", "string", tuple(day.isoformat() for day, _ in pairs)),
                    PanelColumn("open", "float", tuple(row[0] for row in quoted)),
                    PanelColumn("high", "float", tuple(row[1] for row in quoted)),
                    PanelColumn("low", "float", tuple(row[2] for row in quoted)),
                    PanelColumn("close", "float", tuple(row[3] for row in quoted)),
                    PanelColumn("pre_close", "float", tuple(row[4] for row in quoted)),
                    PanelColumn(
                        "pct_chg",
                        "float",
                        tuple((row[3] / row[4] - 1.0) * 100.0 for row in quoted),
                    ),
                    PanelColumn("vol", "float", tuple(100.0 for _ in quoted)),
                    PanelColumn("amount", "float", tuple(10000.0 for _ in quoted)),
                ],
                event_time=[close_time(day) for day, _ in pairs],
                available_time=available,
                fetched_at=fetched,
            )
        ],
        fundamentals=[
            batch(
                DAILY_BASIC_DATASET,
                subjects=[code for _, code in pairs],
                columns=[
                    PanelColumn("trade_date", "string", tuple(day.isoformat() for day, _ in pairs)),
                    PanelColumn("close", "float", tuple(row[3] for row in quoted)),
                    *(
                        PanelColumn(name, "float", tuple(1.0 for _ in quoted))
                        for name in DAILY_BASIC_EXTRA_COLUMNS
                    ),
                ],
                event_time=[close_time(day) for day, _ in pairs],
                available_time=available,
                fetched_at=fetched,
            )
        ],
        calendar=calendar,
        halts=None,
    )
    write_price_limits(
        store,
        [
            batch(
                PRICE_LIMIT_DATASET,
                subjects=[code for _, code in pairs],
                columns=[
                    PanelColumn("trade_date", "string", tuple(day.isoformat() for day, _ in pairs)),
                    PanelColumn(
                        "up_limit", "float", tuple(round(row[4] * 1.1, 2) for row in quoted)
                    ),
                    PanelColumn(
                        "down_limit", "float", tuple(round(row[4] * 0.9, 2) for row in quoted)
                    ),
                ],
                event_time=[close_time(day) for day, _ in pairs],
                available_time=available,
                fetched_at=fetched,
            )
        ],
        calendar=calendar,
    )
    return store


def test_a_v2_evenings_veto_window_reaches_the_year_before_at_the_start_of_a_year(
    tmp_path: Path,
) -> None:
    """Finding 1, fix round 1: an evening on the second session of the year reads its five
    sessions across New Year.

    The real `vetoed_on` and `veto_sessions` are driven against a real two-year panel, no
    stand-in: the set is the window's rule and equals `vetoed_codes` over the same window read
    independently. Before the fix the evening's 2026-only calendar refused the window, and a
    parallel book registered near 1 January would have lost its first four evenings.
    """
    store = two_year_panel(tmp_path)

    vetoed = _vetoed_for(store, V2_REGISTRATION, session=VETO_ACROSS_NEW_YEAR, calendar=CALENDAR)

    two_year = load_trading_calendar(
        store,
        exchange=V2_REGISTRATION.adapter.exchange,
        years=(2025, 2026),
        as_of=session_publication_instant(VETO_ACROSS_NEW_YEAR),
    )
    window = veto_window(
        store,
        session=VETO_ACROSS_NEW_YEAR,
        calendar=two_year,
        exchange=V2_REGISTRATION.adapter.exchange,
    )
    assert vetoed == vetoed_codes(window) == frozenset({LEGACY_EVENT})
    assert [events[0].trade_date for events in window if events] == [date(2025, 12, 31)]


def test_a_v2_evenings_window_reaching_a_year_the_panel_does_not_hold_refuses_by_name(
    panel: PanelStore,
) -> None:
    """Finding 1's other half: when the year before is really not stored, the evening's own
    calendar is used and the window's walk past its start is refused by name, naming the session
    -- not a different vetoed set and not a silent empty one."""
    with pytest.raises(SurvivorDayError) as refused:
        _vetoed_for(panel, V2_REGISTRATION, session=VETO_ACROSS_NEW_YEAR, calendar=CALENDAR)

    message = str(refused.value)
    assert refused.value.step == "paper book"
    assert f"5-session window ending {VETO_ACROSS_NEW_YEAR.isoformat()}" in message
    assert "start of the calendar" in message
