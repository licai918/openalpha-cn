"""The paper book's settlement inputs off the panel, and its session ordinals (`V2-P7-009`, C1).

`settlement_market` reads one session for the names a settlement asks about -- their bars with
the exchange's published band, their adjustment factors, and the benchmark's close -- and
`SessionOrdinals` turns a session into the integer `TradeOrder` counts and back. Every panel here
is generated at test time and written through the real `panel_ingest` writers (`AGENTS.md` hard
rule 6), from the repository's own generators rather than a copy of them:

- `CORPUS` is `panel_fixtures.generate_panel`'s ten-session, eight-security panel (2026-01-05 ..
  2026-01-16) with the shapes this reader has to tell apart, plus
  `strategy_fixtures`' index levels (000300.SH and the protocol's 000905.SH), read at the
  panel's own `AS_OF`, after every session;
- the evening pair is `strategy_fixtures.write_strategy_corpus_published_daily`, whose
  `adj_factor` and `suspend_d` are published session by session the way the real store grows:
  one store as of Friday 2026-01-09's evening, one grown through 2026-01-16.

The securities each shape lands on are `panel_fixtures`' own (`_timed_key`, `_halted_key`,
`UNBANDED_*`, `TRUNCATED_FACTOR_*`, `EX_RIGHTS_*`), imported rather than restated.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import pytest
from import_linter_containment import contained_lint_imports
from panel_fixtures import (
    EX_RIGHTS_FACTOR,
    EX_RIGHTS_INDEX,
    SECURITIES,
    TRUNCATED_FACTOR_SECURITY_INDEX,
    TRUNCATED_FACTOR_THROUGH_INDEX,
    UNBANDED_SECURITY_INDEX,
    UNBANDED_SESSION_INDEX,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from panel_fixtures import _close_time as close_time
from panel_fixtures import _halted_key as halted_key
from panel_fixtures import _published_time as published_time
from panel_fixtures import _timed_key as timed_key
from scratch_package import copy_package, lint_copy
from strategy_fixtures import INDEX_LEVELS, write_strategy_corpus_published_daily
from strategy_fixtures import _index_batch as index_batch

from openalpha_cn import strategy_survivor_market, strategy_view
from openalpha_cn.backtest.execution import at_the_open
from openalpha_cn.backtest.paper import PaperPortfolio
from openalpha_cn.backtest.paper_step import NO_BAR_REASON, attempt_id, build_paper_step
from openalpha_cn.backtest.portfolio import PortfolioLimits, PortfolioState
from openalpha_cn.domain.adjustment import FactorObservation, build_adjustment_history
from openalpha_cn.domain.panel_batch import PanelColumn, TimelineColumns
from openalpha_cn.domain.portfolio import PortfolioPosition, PositionLot, PositionMark
from openalpha_cn.domain.price_limits import SUSPENSION_DATASET
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import PartitionNotYetKnowableError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant, write_index_prices
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.strategy_survivor_market import (
    PROTOCOL_BENCHMARK,
    MalformedNameError,
    MissingBenchmarkError,
    SessionMarket,
    SessionOrdinalError,
    SessionOrdinals,
    SettlementMarketError,
    UnbandedBarError,
    settlement_market,
)

D = Decimal
ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SHAPES: Final[tuple[str, ...]] = (
    "daily.close_moves_between_sessions",
    "daily.ex_rights_session",
    "suspension.timed_interruption",
    "price_limits.bar_without_a_published_band",
    "adjustment.factor_series_stops_inside_the_window",
)
PANEL: Final[GeneratedPanel] = generate_panel(shapes=SHAPES)
SESSIONS: Final[tuple[date, ...]] = PANEL.sessions
"""2026-01-05 .. 2026-01-16, every weekday open."""
CALENDAR: Final[TradingCalendar] = PANEL.calendar()
"""The generated 2026 calendar: every weekday open but 1 and 2 January."""
TIMED, TIMED_ON = timed_key(SESSIONS, SECURITIES)
"""`000001.SZ`, halted 13:00-15:00 on 2026-01-07: it has a bar and cannot trade."""
HALTED, HALTED_ON = halted_key(SESSIONS, SECURITIES)
"""`601318.SH`, halted all of 2026-01-09: no bar, and a factor row (Tushare serves one)."""
UNBANDED: Final[str] = SECURITIES[UNBANDED_SECURITY_INDEX]
UNBANDED_ON: Final[date] = SESSIONS[UNBANDED_SESSION_INDEX]
"""`600000.SH` on 2026-01-08: a bar and no published band."""
TRUNCATED: Final[str] = SECURITIES[TRUNCATED_FACTOR_SECURITY_INDEX]
"""`688981.SH`: its factor series stops at 2026-01-12 while its bars run on."""
EX_RIGHTS: Final[str] = SECURITIES[0]
"""`000001.SZ` again: its factor steps from 1.0 to 1.05 on 2026-01-08."""
ORDINARY: Final[str] = SECURITIES[1]
"""`000002.SZ`: none of the shapes lands on it."""
FULL: Final[PortfolioLimits] = PortfolioLimits(
    max_position_weight=D(1), max_total_exposure=D(1), min_cash_weight=D(0)
)


def evening(session: date) -> datetime:
    """18:30 Asia/Shanghai on `session`: two hours after its 16:30 publication."""
    return session_publication_instant(session) + timedelta(hours=2)


def as_bytes(market: SessionMarket) -> bytes:
    """Every value of `market`, exactly: each `Decimal` by its own digits and exponent."""
    return json.dumps(
        {
            "bars": {code: bar.model_dump(mode="json") for code, bar in market.bars.items()},
            "adj_factor": {code: str(value) for code, value in market.adj_factor.items()},
            "benchmark_close": str(market.benchmark_close),
            "order": [list(market.bars), list(market.adj_factor)],
        },
        sort_keys=True,
    ).encode()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    store = PanelStore(tmp_path_factory.mktemp("market") / "panel")
    write_generated_panel(store, PANEL)
    write_index_prices(store, [index_batch(SESSIONS)])
    return store


def read(store: PanelStore, session: date, *names: str, **overrides: Any) -> SessionMarket:
    ask: dict[str, Any] = {
        "session": session,
        "as_of": PANEL.as_of,
        "names": names or SECURITIES,
        "calendar": CALENDAR,
    }
    ask.update(overrides)
    return settlement_market(store, **ask)


# --- session ordinals ----------------------------------------------------------------------------


def _weekdays_of(year: int, closed: frozenset[date] = frozenset()) -> tuple[CalendarDay, ...]:
    first = date(year, 1, 1)
    days = (first + timedelta(days=offset) for offset in range((date(year + 1, 1, 1) - first).days))
    return tuple(
        CalendarDay(calendar_date=day, is_trading=day.weekday() < 5 and day not in closed)
        for day in days
    )


BACKFILLED: Final[TradingCalendar] = build_trading_calendar(
    CALENDAR.exchange,
    (*_weekdays_of(2025, frozenset({date(2025, 1, 1)})), *PANEL.calendar_days),
)
"""`CALENDAR` with 2025 stored in front of it, as a backfill of an earlier year leaves it."""


def test_the_epoch_is_session_zero_and_every_open_day_after_it_counts_one() -> None:
    ordinals = SessionOrdinals(CALENDAR, date(2026, 1, 5))

    assert ordinals.epoch == date(2026, 1, 5)
    assert [ordinals.index_of(day) for day in SESSIONS] == list(range(10))
    # Friday 2026-01-09 is 4 and Monday 2026-01-12 is 5: the weekend is not counted.
    assert (ordinals.index_of(date(2026, 1, 9)), ordinals.index_of(date(2026, 1, 12))) == (4, 5)
    assert [ordinals.day_of(index) for index in range(10)] == list(SESSIONS)


def test_a_holiday_and_the_turn_of_the_year_are_skipped_rather_than_counted() -> None:
    """2026-01-01 and 2026-01-02 are closed and fall on a Thursday and a Friday."""
    from_new_years_eve = SessionOrdinals(BACKFILLED, date(2025, 12, 31))
    from_monday = SessionOrdinals(BACKFILLED, date(2025, 12, 29))

    assert from_new_years_eve.index_of(date(2026, 1, 5)) == 1
    assert [
        from_monday.index_of(day)
        for day in (date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 5), date(2026, 1, 6))
    ] == [1, 2, 3, 4]
    assert from_monday.day_of(3) == date(2026, 1, 5)


@pytest.mark.parametrize(
    ("day", "named"),
    [
        (date(2026, 1, 10), "2026-01-10 is not an open SZSE session"),
        (date(2026, 1, 1), "2026-01-01 is not an open SZSE session"),
        (date(2026, 1, 5), "2026-01-05 is before the epoch session 2026-01-06"),
        (
            date(2027, 1, 4),
            "2027-01-04 is outside the SZSE calendar's range 2026-01-01..2026-12-31",
        ),
    ],
)
def test_a_day_that_is_not_an_open_session_at_or_after_the_epoch_has_no_ordinal(
    day: date, named: str
) -> None:
    ordinals = SessionOrdinals(CALENDAR, date(2026, 1, 6))

    with pytest.raises(SessionOrdinalError, match=re.escape(named)):
        ordinals.index_of(day)


def test_an_ordinal_below_zero_or_past_the_calendar_has_no_session() -> None:
    ordinals = SessionOrdinals(CALENDAR, date(2026, 12, 30))

    assert ordinals.day_of(1) == date(2026, 12, 31)
    with pytest.raises(SessionOrdinalError, match="ordinal -1 is before the epoch"):
        ordinals.day_of(-1)
    with pytest.raises(SessionOrdinalError, match=r"ordinal 2 .* after 2026-12-31"):
        ordinals.day_of(2)


@pytest.mark.parametrize(
    ("epoch", "named"),
    [
        (date(2026, 1, 3), "2026-01-03 is not an open SZSE session"),
        (date(2026, 1, 2), "2026-01-02 is not an open SZSE session"),
        (date(2025, 12, 31), "2025-12-31 is outside the SZSE calendar's range"),
    ],
)
def test_an_epoch_that_is_not_an_open_session_is_refused(epoch: date, named: str) -> None:
    with pytest.raises(SessionOrdinalError, match=re.escape(named)):
        SessionOrdinals(CALENDAR, epoch)


def test_backfilling_an_earlier_year_into_the_calendar_moves_no_ordinal() -> None:
    """The ordinal depends on the epoch and the calendar from it on, never on how far back the
    stored calendar reaches, so a snapshot written before a backfill still names its sessions."""
    epoch = date(2026, 1, 5)
    stored = SessionOrdinals(CALENDAR, epoch)
    backfilled = SessionOrdinals(BACKFILLED, epoch)
    year = CALENDAR.trading_days

    assert [backfilled.index_of(day) for day in year] == [stored.index_of(day) for day in year]
    assert [backfilled.day_of(n) for n in range(len(year))] == [
        stored.day_of(n) for n in range(len(year))
    ]
    assert BACKFILLED.trading_day_count > CALENDAR.trading_day_count


# --- the session market ---------------------------------------------------------------------------


def test_a_session_market_is_the_named_securities_bars_factors_and_the_benchmark_close(
    corpus: PanelStore,
) -> None:
    """2026-01-07: every name has a bar and a band, and `TIMED` is halted through the close.

    Closes are the generator's `10 + j + 0.5 t` (`j` the security's position, `t` the session's),
    so `000002.SZ` closes at 12.0 on the third session; the benchmark is 000905.SH's 6090.0.
    """
    market = read(corpus, TIMED_ON)

    assert list(market.bars) == sorted(SECURITIES)
    assert list(market.adj_factor) == sorted(SECURITIES)
    assert market.bars[ORDINARY].close == D("12.0")
    assert market.bars[ORDINARY].trade_date == TIMED_ON
    assert all(bar.has_published_limits and not bar.is_st for bar in market.bars.values())
    assert [code for code, bar in market.bars.items() if bar.suspended] == [TIMED]
    assert market.adj_factor[ORDINARY] == D("1.0")
    assert market.benchmark_close == D(str(INDEX_LEVELS["000905.SH"][2])) == D("6090.0")
    assert read(corpus, TIMED_ON, benchmark="000300.SH").benchmark_close == D("4020.0")


def test_a_name_asked_for_with_a_daily_row_and_no_published_band_is_refused_by_name(
    corpus: PanelStore,
) -> None:
    """Absent from `bars` means "no daily row" to the settlement -- an order not attempted, a
    delisted holding written off at its last mark -- so a priced name with no band is refused
    rather than left out to look unpriced. With its band it is in `bars`; with no daily row it is
    absent and nothing is refused; and a band-less bar refuses only a read that asks for it."""
    with pytest.raises(UnbandedBarError, match=r"600000\.SH has a daily row on 2026-01-08"):
        read(corpus, UNBANDED_ON, UNBANDED, ORDINARY)

    the_day_before = read(corpus, SESSIONS[UNBANDED_SESSION_INDEX - 1], UNBANDED, ORDINARY)
    not_asked = read(corpus, UNBANDED_ON, ORDINARY)
    no_daily_row = read(corpus, HALTED_ON, HALTED, ORDINARY)

    assert list(the_day_before.bars) == sorted((UNBANDED, ORDINARY))
    assert the_day_before.bars[UNBANDED].has_published_limits
    assert list(not_asked.bars) == [ORDINARY]
    assert list(no_daily_row.bars) == [ORDINARY]
    assert list(no_daily_row.adj_factor) == sorted((HALTED, ORDINARY))


def test_a_name_with_no_factor_on_the_session_is_left_out_of_the_factors_and_keeps_its_bar(
    corpus: PanelStore,
) -> None:
    """The caller refuses a holding without a ratio by name; this reader never invents one."""
    last_factor = SESSIONS[TRUNCATED_FACTOR_THROUGH_INDEX]
    after = SESSIONS[TRUNCATED_FACTOR_THROUGH_INDEX + 1]

    assert read(corpus, last_factor, TRUNCATED).adj_factor == {TRUNCATED: D("1.0")}
    beyond = read(corpus, after, TRUNCATED, ORDINARY)
    assert list(beyond.adj_factor) == [ORDINARY]
    assert list(beyond.bars) == sorted((TRUNCATED, ORDINARY))


def test_the_factor_is_the_one_in_force_on_the_session_and_the_bar_is_unscaled(
    corpus: PanelStore,
) -> None:
    """Scaling a held name's bar by its ratio is `build_paper_step`'s; this hands it raw."""
    before = read(corpus, SESSIONS[EX_RIGHTS_INDEX - 1], EX_RIGHTS)
    on = read(corpus, SESSIONS[EX_RIGHTS_INDEX], EX_RIGHTS)

    assert before.adj_factor[EX_RIGHTS] == D("1.0")
    assert on.adj_factor[EX_RIGHTS] == D(str(EX_RIGHTS_FACTOR)) == D("1.05")
    # 10 + 0 + 0.5 x 3: the generated close, not the close times 1.05.
    assert on.bars[EX_RIGHTS].close == D("11.5")


def test_a_bar_halted_through_the_session_is_suspended_and_the_book_refuses_its_order_at_the_open(
    corpus: PanelStore, tmp_path: Path
) -> None:
    """With `build_paper_step` and `PaperPortfolio`: the open collapse keeps `suspended`, so the
    book refuses the whole order and writes the refusal down, as T5 section 4 measured."""
    ordinals = SessionOrdinals(CALENDAR, SESSIONS[0])
    index = ordinals.index_of(TIMED_ON)
    market = read(corpus, TIMED_ON, TIMED, ORDINARY)
    order = TradeOrder(
        signal_session=index - 1,
        execute_session=index,
        expires_session=index + 2,
        side="buy",
        ts_code=TIMED,
        quantity=100,
        reference_price=D("12.00"),
        reason="ENTRY_REPLACEMENT",
    )
    plan = build_paper_step(
        session=TIMED_ON,
        session_index=index,
        orders=(order,),
        bars=market.bars,
        adjustment_ratio={},
        benchmark_close=market.benchmark_close,
    )
    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")

    result = PaperPortfolio(ledger=ledger, limits=FULL).advance(
        state=PortfolioState(as_of=ordinals.day_of(index - 1), cash=D("100000.00")),
        session=plan.step,
        observed_on=TIMED_ON,
    )

    assert market.bars[TIMED].suspended is True
    assert at_the_open(market.bars[TIMED]) in plan.step.bars
    assert [(t.order.order_id, t.status, t.reason) for t in result.transitions] == [
        (attempt_id(order, TIMED_ON), "rejected", "security is suspended")
    ]
    assert result.after.cash == D("100000.00")


def _with_a_morning_halt(panel: GeneratedPanel, code: str, day: date) -> GeneratedPanel:
    """`panel` with one more `suspend_d` row: `code` halted 09:30-10:30 on `day`, bar kept."""
    halts = panel.batch(SUSPENSION_DATASET)
    row: dict[str, object] = {
        "trade_date": day.isoformat(),
        "suspend_type": "S",
        "suspend_timing": "09:30-10:30",
    }
    timeline = halts.timeline
    appended = dataclasses.replace(
        halts,
        subjects=(*halts.subjects, code),
        timeline=TimelineColumns(
            event_time=(*timeline.event_time, close_time(day)),
            available_time=(*timeline.available_time, published_time(day)),
            ingested_time=(*timeline.ingested_time, published_time(day)),
            revision_time=(*timeline.revision_time, published_time(day)),
        ),
        columns=tuple(
            PanelColumn(column.name, column.kind, (*column.values, row[column.name]))
            for column in halts.columns
        ),
    )
    return dataclasses.replace(
        panel, batches=MappingProxyType({**panel.batches, SUSPENSION_DATASET: appended})
    )


def test_a_name_halted_only_in_the_morning_meets_a_suspended_bar_at_the_open(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The book fills at the open, so the halt collapse is the P6 book's open one: any timed
    interruption makes the session's bar `suspended`, though this one ended at 10:30 and the name
    traded at the close -- the fail-closed direction `strategy_backtest` records."""
    store = PanelStore(tmp_path_factory.mktemp("morning") / "panel")
    write_generated_panel(store, _with_a_morning_halt(PANEL, ORDINARY, TIMED_ON))
    write_index_prices(store, [index_batch(SESSIONS)])

    market = read(store, TIMED_ON, ORDINARY, "600519.SH")

    assert market.bars[ORDINARY].suspended is True
    assert market.bars["600519.SH"].suspended is False
    assert market.bars[ORDINARY].close == D("12.0")


def test_a_whole_day_halt_has_no_bar_and_its_factor_so_a_sale_of_it_is_not_attempted(
    corpus: PanelStore, tmp_path: Path
) -> None:
    """C1's question, on the fixture: Tushare serves `adj_factor` on a whole-day halt (543 of
    543 main-board whole-day halts on the research window's year-edge sessions), and so does the
    generator, so a held name halted all day with a sale due has its ratio and the night goes
    on: the step records the sale as not attempted and carries the holding's mark."""
    ordinals = SessionOrdinals(CALENDAR, SESSIONS[0])
    index = ordinals.index_of(HALTED_ON)
    market = read(corpus, HALTED_ON, HALTED, ORDINARY)
    entry_adj = D("1.0")
    order = TradeOrder(
        signal_session=index - 1,
        execute_session=index,
        side="sell",
        ts_code=HALTED,
        quantity=100,
        reference_price=D("20.00"),
        reason="EXIT_SURVIVOR_LOST",
    )
    plan = build_paper_step(
        session=HALTED_ON,
        session_index=index,
        orders=(order,),
        bars=market.bars,
        adjustment_ratio={HALTED: market.adj_factor[HALTED] / entry_adj},
        benchmark_close=market.benchmark_close,
    )
    held = PortfolioState(
        as_of=ordinals.day_of(index - 1),
        cash=D("1000.00"),
        positions=(
            PortfolioPosition(
                subject=HALTED,
                lots=(PositionLot(open_date=SESSIONS[0], quantity=100, cost_basis=D("1700.00")),),
            ),
        ),
        marks=(PositionMark(subject=HALTED, price=D("18.50")),),
    )
    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")

    result = PaperPortfolio(ledger=ledger, limits=FULL).advance(
        state=held, session=plan.step, observed_on=HALTED_ON
    )

    assert list(market.bars) == [ORDINARY]
    assert market.adj_factor == {HALTED: D("1.0"), ORDINARY: D("1.0")}
    assert [(a.outcome, a.reason) for a in plan.attempts] == [("not_attempted", NO_BAR_REASON)]
    assert ledger.list() == ()
    assert result.after.positions == held.positions
    assert [(m.subject, m.price) for m in result.carried_marks] == [(HALTED, D("18.50"))]


def test_only_the_names_asked_for_are_built_and_a_name_the_panel_never_heard_of_is_absent(
    corpus: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole market is five thousand bars; a settlement asks about its holdings and orders."""
    built: list[str] = []
    assemble = strategy_survivor_market.stored_market_bar

    def counted(*args: Any, **kwargs: Any) -> Any:
        bar = assemble(*args, **kwargs)
        built.append(bar.subject)
        return bar

    monkeypatch.setattr(strategy_survivor_market, "stored_market_bar", counted)

    market = read(corpus, TIMED_ON, "600519.SH", ORDINARY, "000999.SZ")

    assert built == [ORDINARY, "600519.SH"]
    assert list(market.bars) == [ORDINARY, "600519.SH"]
    assert list(market.adj_factor) == [ORDINARY, "600519.SH"]
    empty = settlement_market(
        corpus, session=TIMED_ON, as_of=PANEL.as_of, names=(), calendar=CALENDAR
    )
    assert (dict(empty.bars), dict(empty.adj_factor)) == ({}, {})
    assert empty.benchmark_close == D("6090.0")


def test_the_default_benchmark_is_the_one_the_p6_book_reports_first() -> None:
    """Two spellings of one protocol choice, held equal rather than trusted to stay so."""
    assert PROTOCOL_BENCHMARK == strategy_view.PROTOCOL_BENCHMARKS[0] == "000905.SH"


def test_the_market_is_read_only_and_the_close_keeps_the_stored_digits(
    corpus: PanelStore,
) -> None:
    """The maps cannot be edited by a caller, and the close is `Decimal(str(close))`: 6090.0,
    not the 6090 `Decimal(float)` would write into a log or a snapshot."""
    market = read(corpus, TIMED_ON, ORDINARY)

    assert str(market.benchmark_close) == "6090.0"
    assert str(market.adj_factor[ORDINARY]) == "1.0"
    with pytest.raises(TypeError):
        market.bars[ORDINARY] = market.bars[ORDINARY]  # type: ignore[index]
    with pytest.raises(TypeError):
        market.adj_factor[ORDINARY] = D(1)  # type: ignore[index]


def test_a_name_whose_factor_row_on_the_session_is_missing_is_left_out_tonight_and_carried_later(
    corpus: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The factor horizon is per security (`AdjustmentHistory.covered_through`). With no row on
    the session and none after it yet, the evening read leaves the name out; once a later row is
    stored, `bisect` answers the session with the value in force before it -- the one exception,
    besides an upstream restatement, to "a later read gives the evening's bytes"."""
    loaded = strategy_survivor_market.load_adjustment_histories
    rows = [FactorObservation(ts_code=ORDINARY, observed_on=date(2026, 1, 6), factor=2.0)]

    def with_ordinarys_rows(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        return {**loaded(*args, **kwargs), ORDINARY: build_adjustment_history(ORDINARY, rows)}

    monkeypatch.setattr(strategy_survivor_market, "load_adjustment_histories", with_ordinarys_rows)
    tonight = read(corpus, TIMED_ON, ORDINARY, "600519.SH")
    rows.append(FactorObservation(ts_code=ORDINARY, observed_on=date(2026, 1, 8), factor=3.0))
    later = read(corpus, TIMED_ON, ORDINARY, "600519.SH")

    assert list(tonight.adj_factor) == ["600519.SH"]
    assert later.adj_factor[ORDINARY] == D("2.0")


def test_a_benchmark_the_store_does_not_hold_is_refused_by_name(corpus: PanelStore) -> None:
    with pytest.raises(MissingBenchmarkError, match=r"000852\.SH .*2026-01-07"):
        read(corpus, TIMED_ON, benchmark="000852.SH")


def test_a_benchmark_with_no_close_on_the_session_is_refused_rather_than_carried(
    corpus: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session before has a close; it is not this session's, and it is not used."""
    levels = strategy_survivor_market.load_index_prices

    def without_the_session(*args: Any, **kwargs: Any) -> Mapping[str, tuple[Any, ...]]:
        return {
            code: tuple(
                bar for bar in bars if not (code == "000905.SH" and bar.trade_date == TIMED_ON)
            )
            for code, bars in levels(*args, **kwargs).items()
        }

    monkeypatch.setattr(strategy_survivor_market, "load_index_prices", without_the_session)

    with pytest.raises(MissingBenchmarkError, match=r"000905\.SH has no close on 2026-01-07"):
        read(corpus, TIMED_ON)


def test_a_read_before_the_sessions_publication_is_refused_before_anything_is_read(
    corpus: PanelStore,
) -> None:
    """The instant itself is admitted: see the look-ahead test below, which reads at it."""
    published = session_publication_instant(TIMED_ON)

    with pytest.raises(SettlementMarketError, match=r"publishes at 2026-01-07T16:30:00\+08:00"):
        read(corpus, TIMED_ON, as_of=published - timedelta(microseconds=1))


@pytest.mark.parametrize(
    ("session", "named"),
    [
        (date(2026, 1, 10), "2026-01-10 is not an open SZSE session"),
        (date(2027, 1, 4), "2027-01-04 is not an open SZSE session"),
    ],
)
def test_a_day_that_is_not_an_open_session_has_no_market(
    corpus: PanelStore, session: date, named: str
) -> None:
    with pytest.raises(SettlementMarketError, match=re.escape(named)):
        read(corpus, session, as_of=datetime(2027, 2, 1, tzinfo=UTC))


def test_one_string_is_refused_as_the_names_rather_than_read_as_its_characters(
    corpus: PanelStore,
) -> None:
    with pytest.raises(SettlementMarketError, match=r"not the one string '000002\.SZ'"):
        settlement_market(
            corpus, session=TIMED_ON, as_of=PANEL.as_of, names=ORDINARY, calendar=CALENDAR
        )


def test_a_name_not_spelled_as_a_security_code_is_refused_by_name_before_anything_is_read(
    corpus: PanelStore,
) -> None:
    """A misspelled code matches no daily row, so an order for it would be not attempted on
    every session -- and a sale never expires. The facts adapter's spelling, matched whole."""
    misspelled = ("600000.sh", "60000.SH", "000002.SZ\n", "000002.XSHE")

    with pytest.raises(MalformedNameError) as refused:
        read(corpus, TIMED_ON, ORDINARY, *misspelled)
    with pytest.raises(MalformedNameError, match=re.escape("600000")):
        read(corpus, TIMED_ON, ORDINARY, 600000)  # type: ignore[arg-type]

    assert all(repr(code) in str(refused.value) for code in misspelled)
    assert repr(ORDINARY) not in str(refused.value)
    assert issubclass(MalformedNameError, SettlementMarketError)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("strategy_survivor_market", "settlement_market"),
        ("strategy_survivor_facts", "build_facts_day"),
        ("strategy_survivor", "read_survivor_registration"),
        ("strategy_survivor_book", "catch_up"),
        ("strategy_survivor_registration", "survivor_batch"),
    ],
)
def test_no_backtest_module_may_import_the_survivor_books_panel_readers(
    tmp_path: Path, module: str, name: str
) -> None:
    """Both read the panel, so `backtest/` -- `paper_step` and the settlement core included --
    takes a `SessionMarket` or a `FactsDay` as a plain input and never imports either. The
    contract names each as a forbidden target of its own, so a probe is refused for importing the
    module and not only for what the module reaches; a copy of the package is linted, the real
    one is never written."""
    assert (
        contained_lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("backtest-no-numeric-stack-or-panel-plane",),
        )
        == 0
    )
    package = copy_package(tmp_path)
    (package / "backtest" / "_panel_reader_probe.py").write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        f"from openalpha_cn.{module} import {name}\n\n"
        f'__all__ = ["{name}"]\n',
        encoding="utf-8",
    )

    broken = lint_copy(package, "backtest-no-numeric-stack-or-panel-plane")

    assert broken.exit_code == 1, broken.report
    assert (f"openalpha_cn.backtest is not allowed to import openalpha_cn.{module}:") in " ".join(
        broken.report.split()
    ), broken.report


# --- the look-ahead guard -----------------------------------------------------------------------


def test_the_evening_read_is_the_later_read_byte_for_byte_and_a_later_session_is_never_read(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Friday 2026-01-09, the whole-day halt's session, read three ways.

    At its evening off a store grown through it; the next morning off the same store, before
    Monday publishes; and after 2026-01-16 off a store grown through that. All three are the same
    bytes. The store grown past the session cannot be read at the session's evening at all: the
    adjustment factors and the index levels are read whole-year, and a year holding a later
    session refuses the earlier instant (`PartitionNotYetKnowableError`) rather than answering
    from it.
    """
    session = HALTED_ON
    evening_root = tmp_path_factory.mktemp("evening")
    grown_root = tmp_path_factory.mktemp("grown")
    write_strategy_corpus_published_daily(evening_root, through=session, builds_through=SESSIONS[1])
    grown = write_strategy_corpus_published_daily(grown_root, builds_through=SESSIONS[1])
    at_evening_store = PanelStore(evening_root / "panel")
    grown_store = PanelStore(grown_root / "panel")
    next_morning = datetime(2026, 1, 12, 0, 0, tzinfo=UTC)  # 08:00 Monday, before 16:30

    tonight = settlement_market(
        at_evening_store,
        session=session,
        as_of=evening(session),
        names=SECURITIES,
        calendar=CALENDAR,
    )
    at_publication = settlement_market(
        at_evening_store,
        session=session,
        as_of=session_publication_instant(session),
        names=SECURITIES,
        calendar=CALENDAR,
    )
    morning = settlement_market(
        at_evening_store, session=session, as_of=next_morning, names=SECURITIES, calendar=CALENDAR
    )
    later = settlement_market(
        grown_store, session=session, as_of=grown.as_of, names=SECURITIES, calendar=CALENDAR
    )

    assert as_bytes(tonight) == as_bytes(at_publication) == as_bytes(morning) == as_bytes(later)
    assert HALTED not in tonight.bars
    assert tonight.adj_factor[HALTED] == D("1.0")
    assert tonight.benchmark_close == D(str(INDEX_LEVELS["000905.SH"][4]))
    with pytest.raises(PartitionNotYetKnowableError, match="not_yet_knowable"):
        settlement_market(
            grown_store,
            session=session,
            as_of=evening(session),
            names=SECURITIES,
            calendar=CALENDAR,
        )
