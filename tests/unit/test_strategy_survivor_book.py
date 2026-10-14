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

import importlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import _batch as batch
from panel_fixtures import _close_time as close_time
from panel_fixtures import _published_time as published_time
from test_survivor_facts_panel import (
    CONFIG,
    DELISTED,
    EXTENDED,
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

from openalpha_cn.backtest.multi_day import PortfolioLedger
from openalpha_cn.backtest.paper import PaperPortfolio
from openalpha_cn.backtest.paper_evening import CatchUpResult, PaperLedgerConflictError
from openalpha_cn.backtest.paper_settlement import WRITE_OFF_REASON
from openalpha_cn.backtest.paper_state import PaperStateError
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.portfolio import PortfolioOrder, PortfolioState, PortfolioTransition
from openalpha_cn.domain.survivor_book import (
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SurvivorBookConfig,
)
from openalpha_cn.domain.survivor_screen import screen as survivor_screen
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import write_adjustment_factors, write_index_prices
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
    PaperHead,
    SurvivorDayError,
    SurvivorEvening,
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
from openalpha_cn.strategy_survivor_facts import FactsCoverageError, FactsDay
from openalpha_cn.strategy_survivor_market import SessionOrdinals

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


def adapter(**changes: object) -> SurvivorAdapterConfig:
    """The registered adapter block, as `V2-P7-006` writes it, with `changes`."""
    block: dict[str, object] = {
        "schema_version": "survivor-adapter/v1",
        "epoch_session": EPOCH.isoformat(),
        "exchange": "SZSE",
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
    return SurvivorAdapterConfig.model_validate(block)


def book_config(**changes: object) -> SurvivorBookConfig:
    """The facts frame's book (seed 7, its four factors, a 60-session listing minimum), changed."""
    stored = CONFIG.model_dump(mode="json", exclude_computed_fields=True)
    return SurvivorBookConfig.model_validate({**stored, **changes})


REGISTRATION: Final[SurvivorRegistration] = SurvivorRegistration(book=CONFIG, adapter=adapter())
OTHER_BOOK: Final[SurvivorRegistration] = SurvivorRegistration(
    book=book_config(holding_count=9), adapter=adapter()
)
"""Another book block under the same adapter: another paper book."""


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
    """One runtime directory's paper book: its snapshot store and its own ledger."""

    root: Path
    store: FilePaperStateStore
    ledger: SQLitePortfolioLedger


@pytest.fixture
def paper(tmp_path: Path) -> Paper:
    root = tmp_path / "paper"
    return Paper(
        root=root,
        store=FilePaperStateStore(root / "snapshots"),
        ledger=open_paper_ledger(root / "ledger.sqlite3"),
    )


def files(paper: Paper) -> dict[str, bytes]:
    """The bytes of every file under the paper directory -- snapshots, ledger, marker."""
    found = {
        path.relative_to(paper.root).as_posix(): path.read_bytes()
        for path in sorted(paper.root.rglob("*"))
        if path.is_file()
    }
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
) -> PaperHead:
    return paper_head(
        state_store=paper.store,
        registration=registration,
        ordinals=ordinals,
        session=SESSIONS[index],
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

    message = str(refused.value)
    assert refused.value.step == "facts"
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

    assert refused.value.step == "facts"
    assert UNRECOGNISED_PREFIX in str(refused.value)
    assert "board_unrecognised" in str(refused.value)
    assert "MAIN_BOARD_PREFIXES" in str(refused.value)


def test_every_holding_refused_is_named_at_once_and_a_delisted_one_is_not(
    panel: PanelStore,
) -> None:
    facts = read_day(panel, held=(NEVER_TRADED, UNREGISTERED, DELISTED), as_of=AS_OF)

    with pytest.raises(SurvivorDayError) as refused:
        delisted_or_refuse(facts)

    message = str(refused.value)
    assert NEVER_TRADED in message and UNREGISTERED in message
    assert DELISTED not in message


# --- the marker: one paper book per runtime directory --------------------------------------------


def bind(directory: Path, **changes: object) -> None:
    arguments: dict[str, Any] = {
        "config_id": CONFIG_ID,
        "config_digest": REGISTRATION.digest,
        "epoch_session": EPOCH,
    }
    arguments.update(changes)
    bind_paper_book(directory, **arguments)


def test_the_paper_book_is_bound_to_its_registration_once_and_the_same_binding_writes_nothing(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "paper"
    bind(directory)
    marker = directory / PAPER_BOOK_MARKER
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "schema_version": "survivor-paper-binding/v1",
        "config_id": CONFIG_ID,
        "config_digest": REGISTRATION.digest,
        "epoch_session": EPOCH.isoformat(),
    }
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


@pytest.mark.parametrize("change", ["capital", "epoch"])
def test_a_run_whose_opening_differs_from_the_book_held_is_refused_naming_it(
    panel: PanelStore, paper: Paper, change: str
) -> None:
    run_day(panel, paper, 61)
    before = files(paper)
    if change == "capital":
        registration = SurvivorRegistration(
            book=book_config(capital="300000"), adapter=REGISTRATION.adapter
        )
        ordinals, named = ORDINALS, "cash"
    else:
        registration = SurvivorRegistration(
            book=CONFIG, adapter=adapter(epoch_session=SESSIONS[59].isoformat())
        )
        ordinals, named = SessionOrdinals(CALENDAR, SESSIONS[59]), "epoch"

    with pytest.raises(SurvivorDayError) as refused:
        head_of(paper, 62, registration, ordinals)

    assert refused.value.step == "paper head"
    assert isinstance(refused.value.__cause__, PaperStateError)
    assert named in str(refused.value)
    assert files(paper) == before


def test_ordinals_counted_from_another_epoch_than_the_registered_one_are_refused(
    panel: PanelStore, paper: Paper
) -> None:
    other = SessionOrdinals(CALENDAR, SESSIONS[59])

    with pytest.raises(SurvivorDayError) as at_head:
        head_of(paper, 61, REGISTRATION, other)

    assert at_head.value.step == "paper head"
    assert EPOCH.isoformat() in str(at_head.value)
    assert paper.store.sessions() == ()
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
    assert head_of(paper, 61, tight).session_index == 1
    assert head_of(paper, 64, tight).session_index == 4  # an open book catches up what it missed


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


def test_settle_only_settles_a_session_with_no_snapshot_and_refuses_one_already_held(
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)

    settled = settle_only(
        panel=panel,
        state_store=paper.store,
        ledger=paper.ledger,
        registration=REGISTRATION,
        ordinals=ORDINALS,
        calendar=CALENDAR,
        session=SESSIONS[62],
        as_of=AS_OF,
        make_portfolio=make_portfolio,
        transform=IDENTITY,
    )

    assert [item.snapshot.session for item in settled] == [SESSIONS[62]]
    assert settled[0].snapshot.first_decision_session == 1
    before = files(paper)
    for index in (61, 62):
        with pytest.raises(SurvivorDayError) as refused:
            settle_only(
                panel=panel,
                state_store=paper.store,
                ledger=paper.ledger,
                registration=REGISTRATION,
                ordinals=ORDINALS,
                calendar=CALENDAR,
                session=SESSIONS[index],
                as_of=AS_OF,
                make_portfolio=make_portfolio,
                transform=IDENTITY,
            )
        assert refused.value.step == "paper book"
        assert "evening" in str(refused.value)
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
    assert at_catch_up.value.step == at_evening.value.step == "paper book"
    for refused in (at_head, at_catch_up, at_evening):
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
    catch_up_through(panel, paper, 62)  # bought at 62's open; nothing held while it settled
    before = files(paper)

    with pytest.raises(SurvivorDayError) as refused:
        catch_up_through(panel, paper, 63)

    assert refused.value.step == "facts"
    assert UNREGISTERED in str(refused.value) and "not_in_registry" in str(refused.value)
    assert files(paper) == before


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
    panel: PanelStore, paper: Paper
) -> None:
    run_day(panel, paper, 61)
    holding, release = threading.Event(), threading.Event()
    first: list[Day] = []
    faults: list[BaseException] = []

    def first_run() -> None:
        try:
            with paper_book_lock(paper.root):
                holding.set()
                release.wait(timeout=60)
                first.append(run_day(panel, paper, 62))
        except BaseException as error:  # reported below, never swallowed
            faults.append(error)
            holding.set()

    thread = threading.Thread(target=first_run)
    thread.start()
    try:
        assert holding.wait(timeout=60)
        before = files(paper)
        with pytest.raises(SurvivorDayError) as refused, paper_book_lock(paper.root):
            run_day(panel, paper, 62)
        assert files(paper) == before
    finally:
        release.set()
        thread.join(timeout=120)

    assert faults == []
    assert refused.value.step == "paper head"
    assert PAPER_BOOK_LOCK in str(refused.value)
    assert [day.evening.result.outcome for day in first] == ["created"]
    with paper_book_lock(paper.root):  # released with its holder
        assert head_of(paper, 62).settled is True


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
        with pytest.raises(SurvivorDayError) as refused, paper_book_lock(directory):
            pass
        assert refused.value.step == "paper head"
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=120)

    assert holder.returncode == 0
    with paper_book_lock(directory):
        assert (directory / PAPER_BOOK_LOCK).exists()
