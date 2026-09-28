"""`V2-P6-006`: every factor of one build shares each instant's universe, calendar and industry.

`V2-P6-005` left a whole-market factor build at ~3.3 s per (factor, instant) across three tiers,
and most of it was not the factor: the registry load (~1.0 s, 77 DuckDB connections and 1,194
statements over 37 partitions) and the industry-and-size cross section the neutralisation regresses
on (~1.1-1.4 s). Both are the same for all 21 factors at one instant. This file holds the change
that stops paying them 21 times to its three obligations:

- **Equivalence.** A multi-factor build stores, for every factor, exactly the partitions -- by
  `content_hash` -- that factor's own single-factor build stores from the same inputs and the same
  clock. All 21 declared factors, all three tiers, at instants in January, June and December of the
  second of two generated calendar years.
- **Sharing, counted.** Each instant's registry, calendar and industry cross section is loaded
  once per build whatever the number of factors. Counts rather than seconds, for
  `test_factor_read_path_equivalence.py`'s measured reason: the machine's load swings threefold.
- **Staleness is noticed.** A shared answer is served again only while the partitions it was read
  from stand. A registry partition rewritten -- or merely re-profiled -- between two factors of one
  build is read afresh by the second, which then stores what its own build over the changed store
  stores.

And the multi-factor build's own refusal: it writes one factor at a time, each whole, and a factor
that is refused names itself and lists what the factors before it already stored.

## The corpus

Generated at test time (`AGENTS.md` rule 6). Two calendar years of sessions (2025-2026) for 120
securities -- above the 100-name floors of the shipped transform and neutralisation -- with the
registry, calendar, `daily`, `daily_basic`, `index_daily`, the four statement endpoints and the
industry membership all written through `write_panel_batch`. Three names carry the registry's
moving parts: `YOUNG` lists on 2026-05-25 and `DELISTED` leaves on 2026-03-16, so the listed
cross section differs between the three instants, and `HALTED` stops trading on 2026-03-02. Four
names are reclassified, one per quarter, so the industry read is different at every instant and is
never older than the freshness bound. Filings before 2024's annual report are announced in January
2025 -- a backfill -- so every statement partition is inside the two years the build names.
"""

from __future__ import annotations

import gc
import json
import math
import shutil
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from openalpha_cn import factor_view
from openalpha_cn.cli import FACTOR_EXIT, app
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATA_COLUMNS,
    DAILY_BASIC_DATASET,
    DAILY_DATA_COLUMNS,
    DAILY_DATASET,
    SESSION_CLOSE_TIME,
)
from openalpha_cn.domain.financial_statements import (
    ANNOUNCEMENT_DATE_COLUMN,
    FIRST_ANNOUNCEMENT_COLUMN,
    REPORT_PERIOD_COLUMN,
    REVISION_LABEL_COLUMN,
    STATEMENT_DATA_COLUMNS,
    statement_panel_columns,
)
from openalpha_cn.domain.index_prices import (
    INDEX_DAILY_DATA_COLUMNS,
    INDEX_DAILY_DATASET,
    MARKET_INDEX_CODE,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_FROM_COLUMN,
    INDUSTRY_L1_COLUMN,
    INDUSTRY_L2_COLUMN,
    INDUSTRY_L3_COLUMN,
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_THROUGH_COLUMN,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.stock_universe import (
    DELISTING_EVENT,
    LIFECYCLE_DATE_COLUMN,
    LIFECYCLE_EVENT_COLUMN,
    LISTING_EVENT,
    STOCK_BASIC_DATASET,
    UNIVERSE_EXCHANGE_COLUMN,
)
from openalpha_cn.domain.trading_calendar import (
    CALENDAR_DATE_COLUMN,
    CALENDAR_OPEN_COLUMN,
    CALENDAR_PRETRADE_COLUMN,
    TRADING_CALENDAR_DATASET,
)
from openalpha_cn.factor_view import (
    FactorBuildReport,
    FactorPanelUnreadableError,
    FactorRequestError,
    build_factor_panels,
    build_view,
    factor_build_request,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS
from openalpha_cn.panel_ingest import split_panel_batch_by_year, write_panel_batch
from openalpha_cn.sdk import OpenAlphaSDK

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
EXCHANGE: Final[str] = "SSE"
YEARS: Final[tuple[int, ...]] = (2025, 2026)
COMMIT: Final[str] = "0123456789abcdef"
BUILT_AT: Final[datetime] = datetime(2026, 9, 26, tzinfo=UTC)
FETCHED_AT: Final[datetime] = datetime(2027, 1, 1, tzinfo=UTC)
STALENESS_DAYS: Final[int] = 200
"""Wide enough for the statement endpoints at January, whose newest filing is October's."""

INSTANTS: Final[tuple[datetime, ...]] = (
    datetime(2026, 1, 8, 9, 0, tzinfo=UTC),
    datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
    datetime(2026, 12, 15, 9, 0, tzinfo=UTC),
)
"""January, June and December of the second year, 17:00 Asia/Shanghai: each day's own session has
published, so all three tiers build. January's windows reach back into 2025; December's hold the
most history."""

SECURITIES: Final[tuple[str, ...]] = tuple(
    f"{600000 + index:06d}.SH" if index % 2 else f"{index + 1:06d}.SZ" for index in range(120)
)
YOUNG, HALTED, DELISTED, LATE_LISTED = SECURITIES[100:104]
YOUNG_FROM: Final[date] = date(2026, 5, 25)
HALTED_FROM: Final[date] = date(2026, 3, 2)
DELISTED_ON: Final[date] = date(2026, 3, 16)
LATE_LISTED_FROM: Final[date] = date(2025, 4, 1)
ADDED_SECURITY: Final[str] = "688999.SH"
"""A listing `_rewrite_a_registry_year` adds to an old lifecycle year, and nothing else carries."""

HOLIDAYS: Final[frozenset[date]] = frozenset(
    {
        date(2025, 1, 1),
        *(date(2025, 1, day) for day in range(28, 32)),
        *(date(2025, 2, day) for day in range(3, 5)),
        *(date(2025, 10, day) for day in range(1, 9)),
        date(2026, 1, 1),
        date(2026, 1, 2),
        *(date(2026, 2, day) for day in range(16, 21)),
        *(date(2026, 10, day) for day in range(1, 8)),
    }
)
FIRST_DAY: Final[date] = date(2025, 1, 1)
LAST_DAY: Final[date] = date(2026, 12, 31)
DAYS: Final[tuple[date, ...]] = tuple(
    FIRST_DAY + timedelta(days=offset) for offset in range((LAST_DAY - FIRST_DAY).days + 1)
)
SESSIONS: Final[tuple[date, ...]] = tuple(
    day for day in DAYS if day.weekday() < 5 and day not in HOLIDAYS
)
OPEN: Final[frozenset[date]] = frozenset(SESSIONS)

INDUSTRIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("801080.SI", "801081.SI", "850811.SI"),
    ("801150.SI", "801151.SI", "851511.SI"),
    ("801010.SI", "801011.SI", "850111.SI"),
)
RECLASSIFIED: Final[dict[str, date]] = {
    SECURITIES[20]: date(2025, 10, 13),
    SECURITIES[21]: date(2026, 3, 2),
    SECURITIES[22]: date(2026, 6, 1),
    SECURITIES[23]: date(2026, 10, 12),
}
"""One reclassification per quarter, so the membership read is never older than the bound."""

PERIODS: Final[tuple[date, ...]] = tuple(
    date(year, month, day)
    for year in (2023, 2024, 2025, 2026)
    for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))
    if date(2023, 3, 31) <= date(year, month, day) <= date(2026, 9, 30)
)

TRANSFORM: Final[str] = "cross_section_standard/v1"
NEUTRALIZATION: Final[str] = "industry_and_size/v1"
FACTOR_PLANE: Final[str] = "factor_"
"""Every factor-plane dataset name starts with this; used here only to pick the stored tiers out
of a catalog listing."""


# --- the corpus ----------------------------------------------------------------------------------


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=SHANGHAI)


def _midnight(day: date) -> datetime:
    return _at(day, time(0, 0))


def _kind(name: str, strings: Sequence[str]) -> str:
    if name in strings:
        return "string"
    return "boolean" if name == CALENDAR_OPEN_COLUMN else "float"


def _batch(
    dataset: str,
    rows: Sequence[tuple[str, datetime, datetime, datetime]],
    columns: dict[str, list[object]],
    strings: Sequence[str],
) -> ColumnarPanelBatch:
    """`rows` is `(subject, event_time, available_time, revision_time)`; the rest are floats."""
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=dataset,
        kind=dataset,
        as_of=FETCHED_AT,
        fetched_at=FETCHED_AT,
        status="success",
        subjects=tuple(row[0] for row in rows),
        timeline=TimelineColumns(
            event_time=tuple(row[1] for row in rows),
            available_time=tuple(row[2] for row in rows),
            ingested_time=tuple(row[2] for row in rows),
            revision_time=tuple(row[3] for row in rows),
        ),
        columns=tuple(
            PanelColumn(name, _kind(name, strings), tuple(values))
            for name, values in columns.items()
        ),
    )


def _listed_on(index: int, code: str) -> date:
    if code == YOUNG:
        return YOUNG_FROM
    if code == LATE_LISTED:
        return LATE_LISTED_FROM
    return date(2008 + index % 15, 1 + index % 12, 1 + index % 27)


def _traded(index: int, code: str, day: date) -> bool:
    if day < _listed_on(index, code):
        return False
    if code == HALTED:
        return day < HALTED_FROM
    if code == DELISTED:
        return day < DELISTED_ON
    return True


def _calendar_batch() -> ColumnarPanelBatch:
    previous: list[object] = []
    seen: date | None = None
    for day in DAYS:
        previous.append(None if seen is None else seen.isoformat())
        if day in OPEN:
            seen = day
    published = _midnight(FIRST_DAY)
    return _batch(
        TRADING_CALENDAR_DATASET,
        [(EXCHANGE, _midnight(day), published, published) for day in DAYS],
        {
            CALENDAR_DATE_COLUMN: [day.isoformat() for day in DAYS],
            CALENDAR_OPEN_COLUMN: [day in OPEN for day in DAYS],
            CALENDAR_PRETRADE_COLUMN: previous,
        },
        (CALENDAR_DATE_COLUMN, CALENDAR_PRETRADE_COLUMN),
    )


def _registry_batch(extra: Sequence[tuple[str, date]] = ()) -> ColumnarPanelBatch:
    events: list[tuple[str, str, date]] = []
    for index, code in enumerate(SECURITIES):
        events.append((code, LISTING_EVENT, _listed_on(index, code)))
        if code == DELISTED:
            events.append((code, DELISTING_EVENT, DELISTED_ON))
    events.extend((code, LISTING_EVENT, day) for code, day in extra)
    return _batch(
        STOCK_BASIC_DATASET,
        [(code, _midnight(day), _midnight(day), _midnight(day)) for code, _, day in events],
        {
            LIFECYCLE_EVENT_COLUMN: [event for _, event, _ in events],
            LIFECYCLE_DATE_COLUMN: [day.isoformat() for _, _, day in events],
            UNIVERSE_EXCHANGE_COLUMN: [EXCHANGE for _ in events],
        },
        (LIFECYCLE_EVENT_COLUMN, LIFECYCLE_DATE_COLUMN, UNIVERSE_EXCHANGE_COLUMN),
    )


def _path(index: int, code: str) -> dict[date, tuple[float, float]]:
    """`(close, pre_close)` per traded session, `pre_close` the previous traded close."""
    path: dict[date, tuple[float, float]] = {}
    price = 10.0 + index * 0.25
    for position, day in enumerate(day for day in SESSIONS if _traded(index, code, day)):
        previous = price
        price *= 1.0 + 0.02 * math.sin(index * 1.7 + position * 0.9)
        path[day] = (price, previous)
    return path


def _bar(
    day: date, close: float, pre_close: float, *, vol: float, amount: float
) -> dict[str, object]:
    return {
        "trade_date": day.isoformat(),
        "open": pre_close,
        "high": max(close, pre_close),
        "low": min(close, pre_close),
        "close": close,
        "pre_close": pre_close,
        "pct_chg": (close / pre_close - 1.0) * 100.0,
        "vol": vol,
        "amount": amount,
    }


def _session_batches() -> tuple[ColumnarPanelBatch, ...]:
    """`daily`, `daily_basic` and `index_daily`, every declared column present."""
    bars: list[tuple[str, datetime, datetime, datetime]] = []
    bar_cells: dict[str, list[object]] = {name: [] for name in DAILY_DATA_COLUMNS}
    basic_cells: dict[str, list[object]] = {name: [] for name in DAILY_BASIC_DATA_COLUMNS}
    for index, code in enumerate(SECURITIES):
        for position, (day, (close, pre_close)) in enumerate(_path(index, code).items()):
            published = _at(day, DAILY_AVAILABILITY_TIME)
            bars.append((code, _at(day, SESSION_CLOSE_TIME), published, published))
            bar = _bar(
                day,
                close,
                pre_close,
                vol=1_000.0 + index + position % 11,
                amount=10_000.0 + index * 10 + (position % 13) * 100,
            )
            for name in DAILY_DATA_COLUMNS:
                bar_cells[name].append(bar[name])
            for offset, name in enumerate(DAILY_BASIC_DATA_COLUMNS):
                basic_cells[name].append(
                    day.isoformat()
                    if name == "trade_date"
                    else close
                    if name == "close"
                    else 1_000_000.0 + index * 10_000.0 + position * 10.0
                    if name == "total_mv"
                    else 800_000.0 + index * 9_000.0 + position * 7.0
                    if name == "circ_mv"
                    else 0.5 + ((index + position + offset) % 17) * 0.1
                )
    index_rows: list[tuple[str, datetime, datetime, datetime]] = []
    index_cells: dict[str, list[object]] = {name: [] for name in INDEX_DAILY_DATA_COLUMNS}
    for day, (close, pre_close) in _path(7, MARKET_INDEX_CODE).items():
        published = _at(day, DAILY_AVAILABILITY_TIME)
        index_rows.append((MARKET_INDEX_CODE, _at(day, SESSION_CLOSE_TIME), published, published))
        bar = _bar(day, close, pre_close, vol=1e6, amount=1e8)
        for name in INDEX_DAILY_DATA_COLUMNS:
            index_cells[name].append(bar[name])
    return (
        _batch(DAILY_DATASET, bars, bar_cells, ("trade_date",)),
        _batch(DAILY_BASIC_DATASET, bars, basic_cells, ("trade_date",)),
        _batch(INDEX_DAILY_DATASET, index_rows, index_cells, ("trade_date",)),
    )


def _announced(index: int, period: date) -> date:
    if period < date(2024, 12, 31):
        return date(2025, 1, 6) + timedelta(days=index % 3)
    deadline = {
        3: date(period.year, 4, 20),
        6: date(period.year, 8, 20),
        9: date(period.year, 10, 20),
        12: date(period.year + 1, 3, 20),
    }[period.month]
    return deadline + timedelta(days=index % 5)


def _statement_batch(dataset: str) -> ColumnarPanelBatch:
    """Every stored column of one endpoint; positive and growing, so every ratio is defined."""
    rows: list[tuple[str, datetime, datetime, datetime]] = []
    cells: dict[str, list[object]] = {name: [] for name in statement_panel_columns(dataset)}
    for index, code in enumerate(SECURITIES):
        for position, period in enumerate(PERIODS):
            announced = _announced(index, period)
            stamp = _midnight(announced)
            rows.append((code, stamp, stamp, stamp))
            cells[REPORT_PERIOD_COLUMN].append(period.isoformat())
            cells[ANNOUNCEMENT_DATE_COLUMN].append(announced.isoformat())
            if FIRST_ANNOUNCEMENT_COLUMN in cells:
                cells[FIRST_ANNOUNCEMENT_COLUMN].append(announced.isoformat())
                cells[REVISION_LABEL_COLUMN].append("0")
            for offset, name in enumerate(STATEMENT_DATA_COLUMNS[dataset]):
                scale = 50.0 if name == "total_assets" else 1.0
                cells[name].append(
                    scale * (100.0 + 37.0 * offset) * (1.0 + index / 50.0) * (1.0 + 0.03 * position)
                    + (index * position) % 7
                )
    return _batch(
        dataset,
        rows,
        cells,
        (
            REPORT_PERIOD_COLUMN,
            ANNOUNCEMENT_DATE_COLUMN,
            FIRST_ANNOUNCEMENT_COLUMN,
            REVISION_LABEL_COLUMN,
        ),
    )


def _membership_batch() -> ColumnarPanelBatch:
    """An opening row per assignment and, for a reclassified name, the close and the new opening."""
    rows: list[tuple[str, date, str, str | None, tuple[str, str, str]]] = []
    for index, code in enumerate(SECURITIES):
        first = max(date(2025, 1, 2), _listed_on(index, code))
        tree = INDUSTRIES[index % 3]
        rows.append((code, first, first.isoformat(), None, tree))
        moved = RECLASSIFIED.get(code)
        if moved is not None:
            through = moved - timedelta(days=1)
            rows.append((code, through, first.isoformat(), through.isoformat(), tree))
            rows.append((code, moved, moved.isoformat(), None, INDUSTRIES[(index + 1) % 3]))
    rows.sort(key=lambda row: (row[1], row[0]))
    return _batch(
        INDUSTRY_MEMBERSHIP_DATASET,
        [(code, _midnight(day), _midnight(day), _midnight(day)) for code, day, *_ in rows],
        {
            INDUSTRY_FROM_COLUMN: [row[2] for row in rows],
            INDUSTRY_THROUGH_COLUMN: [row[3] for row in rows],
            INDUSTRY_L1_COLUMN: [row[4][0] for row in rows],
            INDUSTRY_L2_COLUMN: [row[4][1] for row in rows],
            INDUSTRY_L3_COLUMN: [row[4][2] for row in rows],
        },
        (
            INDUSTRY_FROM_COLUMN,
            INDUSTRY_THROUGH_COLUMN,
            INDUSTRY_L1_COLUMN,
            INDUSTRY_L2_COLUMN,
            INDUSTRY_L3_COLUMN,
        ),
    )


def _write(store: PanelStore, batch: ColumnarPanelBatch, *, only: int | None = None) -> None:
    for year, part in split_panel_batch_by_year(batch):
        if only is None or year == only:
            write_panel_batch(store, part, year=year)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The input partitions, written once. Every test copies them, because a build writes."""
    root = tmp_path_factory.mktemp("shared_context_corpus") / "panel"
    store = PanelStore(root)
    for batch in (
        _calendar_batch(),
        _registry_batch(),
        *_session_batches(),
        *(_statement_batch(name) for name in STATEMENT_DATA_COLUMNS),
        _membership_batch(),
    ):
        _write(store, batch)
    return root


def _copy(corpus: Path, target: Path) -> PanelStore:
    shutil.copytree(corpus, target)
    return PanelStore(target)


@pytest.fixture
def scratch(corpus: Path, tmp_path: Path) -> Iterator[PanelStore]:
    yield _copy(corpus, tmp_path / "panel")


REWRITTEN_YEAR: Final[int] = _listed_on(0, SECURITIES[0]).year


def _rewrite_a_registry_year(store: PanelStore) -> None:
    """Replace one old lifecycle year's partition with the same rows plus one more listing."""
    added = date(REWRITTEN_YEAR, 6, 2)
    _write(store, _registry_batch(((ADDED_SECURITY, added),)), only=REWRITTEN_YEAR)


# --- the builds ----------------------------------------------------------------------------------


def _parameters(**overrides: Any) -> dict[str, Any]:
    return {
        "tier": "neutralized",
        "transform": TRANSFORM,
        "neutralization": NEUTRALIZATION,
        "as_ofs": INSTANTS,
        "years": YEARS,
        "exchange": EXCHANGE,
        "max_staleness_days": STALENESS_DAYS,
        "waive_max_staleness": False,
        "subjects": [],
        "supersedes_raw": [],
        "supersedes_processed": [],
        "supersedes_neutralized": [],
        "code_commit": COMMIT,
        **overrides,
    }


def _single(store: PanelStore, factor: str, **overrides: Any) -> FactorBuildReport:
    return build_factor_panels(
        store, factor_build_request(factor=factor, **_parameters(**overrides)), built_at=BUILT_AT
    )


def _shared(
    store: PanelStore, factors: Sequence[str], **overrides: Any
) -> tuple[FactorBuildReport, ...]:
    requests = factor_view.factor_build_requests(factors=factors, **_parameters(**overrides))
    reports: tuple[FactorBuildReport, ...] = factor_view.build_factor_panel_set(
        store, requests, built_at=BUILT_AT
    )
    return reports


def _stored(store: PanelStore) -> dict[str, str]:
    """Every factor-plane partition the store holds, as `dataset@year -> content_hash`."""
    return {
        f"{dataset}@{year}": content_hash
        for dataset, year, content_hash, _recorded, _hash in store.partition_stamps()
        if dataset.startswith(FACTOR_PLANE)
    }


def _of(stored: dict[str, str], factor: str) -> dict[str, str]:
    """The partitions of `stored` that hold `factor`'s tiers."""
    definition = FACTOR_DEFINITIONS.get(factor)
    marker = f"_{definition.key}_v{definition.version}@"
    return {name: content for name, content in stored.items() if marker in name}


def _counting(monkeypatch: pytest.MonkeyPatch, *names: str) -> Counter[str]:
    """Count every call `factor_view` makes to each named loader, the loader still running."""
    counts: Counter[str] = Counter()
    for name in names:
        original: Callable[..., Any] = getattr(factor_view, name)

        def counted(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(factor_view, name, counted)
    return counts


SHARED_LOADERS: Final[tuple[str, ...]] = (
    "load_stock_universe",
    "load_trading_calendar",
    "load_industry_market_cap_cross_section",
)

THREE: Final[tuple[str, ...]] = (
    "reversal_1d/v1",
    "momentum_20_sessions/v1",
    "earnings_yield_ttm/v1",
)
"""Two session factors and a statement factor: three different read sets over one universe."""


# --- equivalence ---------------------------------------------------------------------------------


def test_every_partition_a_shared_build_writes_is_the_one_its_single_factor_build_writes(
    corpus: Path, tmp_path: Path
) -> None:
    """All 21 factors x 3 tiers x 3 instants, by `content_hash`, against 21 separate builds.

    The single-factor builds each run in a fresh copy of the corpus, which is the arrangement the
    shared build replaces; the shared one runs all 21 in one call over one copy. Every report is
    equal too, so the universe sizes and coverage censuses the faces print did not move either.
    """
    keys = FACTOR_DEFINITIONS.qualified_keys
    alone: dict[str, str] = {}
    single_reports: list[FactorBuildReport] = []
    for position, key in enumerate(keys):
        store = _copy(corpus, tmp_path / f"single_{position}")
        single_reports.append(_single(store, key))
        alone.update(_stored(store))

    together_store = _copy(corpus, tmp_path / "together")
    together_reports = _shared(together_store, keys)
    together = _stored(together_store)

    assert len(alone) == 6 * len(keys)
    assert together == alone
    assert list(together_reports) == single_reports


# --- sharing, counted ----------------------------------------------------------------------------


def test_each_instant_is_loaded_once_for_every_factor_of_a_shared_build(
    scratch: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three factors, three instants: three loads of each shared read, not nine.

    The factor partitions each factor writes between two factors do not count as a change to
    what was shared -- a build that invalidated on its own writes would load 3 x 3 again.
    """
    counts = _counting(monkeypatch, *SHARED_LOADERS)

    reports = _shared(scratch, THREE)

    assert [report.factor for report in reports] == list(THREE)
    assert counts == {name: len(INSTANTS) for name in SHARED_LOADERS}


def test_a_single_factor_build_reads_each_instants_calendar_once(
    scratch: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raw tier and the residual both need the instant's calendar; it is read once for both.

    Before `V2-P6-006` a one-factor neutralised build read it twice per instant: once for the
    readiness requirements and again for the residual's refusal remedy.
    """
    counts = _counting(monkeypatch, *SHARED_LOADERS)

    _single(scratch, "reversal_1d/v1")

    assert counts == {name: len(INSTANTS) for name in SHARED_LOADERS}


def test_what_a_build_shares_is_its_own_instants_and_nothing_outlives_it(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Memory is bounded by the build: one entry per (shared read, instant), gone after it."""
    held: list[Any] = []
    original = factor_view.FactorBuildContext

    class Recording(original):  # type: ignore[misc, valid-type]
        __slots__ = ()

        def __init__(self, store: PanelStore) -> None:
            super().__init__(store)
            held.append(self)

    monkeypatch.setattr(factor_view, "FactorBuildContext", Recording)
    _shared(_copy(corpus, tmp_path / "recorded"), THREE)

    assert len(held) == 1
    keys = list(held[0]._entries)
    assert len(keys) == 3 * len(INSTANTS)
    assert {key[1] for key in keys} == set(INSTANTS)
    held.clear()
    monkeypatch.undo()

    _shared(_copy(corpus, tmp_path / "released"), THREE)
    gc.collect()
    assert [item for item in gc.get_objects() if isinstance(item, original)] == []


# --- staleness -----------------------------------------------------------------------------------


def _after_the_first_factor_writes(
    monkeypatch: pytest.MonkeyPatch, action: Callable[[], None]
) -> None:
    """Run `action` once, right after the first factor of a build has written its raw tier."""
    original = factor_view.write_factor_panels
    done: list[bool] = []

    def writing(*args: Any, **kwargs: Any) -> Any:
        written = original(*args, **kwargs)
        if not done:
            done.append(True)
            action()
        return written

    monkeypatch.setattr(factor_view, "write_factor_panels", writing)


def test_a_registry_year_rewritten_between_two_factors_is_read_afresh_by_the_second(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second factor stores exactly what its own build over the rewritten store stores.

    Served the first factor's universe instead, it would score one security fewer at every
    instant -- the listing added to 2008 -- and its partitions would be the pre-rewrite ones.
    """
    first, second = THREE[0], THREE[1]
    reference = _copy(corpus, tmp_path / "reference")
    _rewrite_a_registry_year(reference)
    expected = _single(reference, second)

    store = _copy(corpus, tmp_path / "shared")
    counts = _counting(monkeypatch, *SHARED_LOADERS)
    _after_the_first_factor_writes(monkeypatch, lambda: _rewrite_a_registry_year(store))
    before, after = _shared(store, (first, second))

    assert after == expected
    assert after.subject_count == before.subject_count + 1
    assert _of(_stored(store), second) == _of(_stored(reference), second)
    assert len(_of(_stored(reference), second)) == 6
    assert counts["load_stock_universe"] == 2 * len(INSTANTS)


def test_a_registry_year_re_profiled_between_two_factors_is_read_afresh(
    scratch: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coverage record re-written over unchanged bytes moves what readiness consults, so the
    shared universe is not trusted across it either.

    Nor is anything else shared: the comparison is over the whole fetched catalog rather than per
    read, so a change to any fetched partition reloads every shared answer. Coarser than it has
    to be, and in the direction that costs a load rather than serves a stale answer."""

    def re_profile() -> None:
        coverage = scratch.read_coverage(STOCK_BASIC_DATASET, REWRITTEN_YEAR)
        assert coverage is not None
        scratch.record_coverage(coverage)

    counts = _counting(monkeypatch, *SHARED_LOADERS)
    _after_the_first_factor_writes(monkeypatch, re_profile)
    _shared(scratch, THREE[:2])

    assert counts == {name: 2 * len(INSTANTS) for name in SHARED_LOADERS}


# --- the multi-factor build's own refusals -------------------------------------------------------

ONE_YEAR_SET: Final[tuple[str, ...]] = (
    "reversal_1d/v1",
    "momentum_120_sessions/v1",
    "momentum_20_sessions/v1",
)
"""Read over 2026 alone at June, the middle one needs 125 sessions and the year holds 111."""


def _one_year(**overrides: Any) -> dict[str, Any]:
    return {
        "tier": "processed",
        "neutralization": "",
        "years": (2026,),
        "as_ofs": (INSTANTS[1],),
        **overrides,
    }


def test_a_refused_factor_names_itself_and_lists_what_the_factors_before_it_stored(
    corpus: Path, tmp_path: Path
) -> None:
    """Each factor is written whole or not at all, one after another; the refusal says which.

    The first factor is stored, the refused one is not, and the one after it was never built.
    The fault keeps the refused factor's own class -- so every face envelopes it exactly as it
    enveloped the single-factor refusal -- and carries that refusal's own words.
    """
    with pytest.raises(FactorPanelUnreadableError) as alone:
        _single(_copy(corpus, tmp_path / "alone"), ONE_YEAR_SET[1], **_one_year())
    store = _copy(corpus, tmp_path / "shared")

    with pytest.raises(FactorPanelUnreadableError) as raised:
        _shared(store, ONE_YEAR_SET, **_one_year())

    message = str(raised.value)
    stored = sorted(_stored(store))
    assert stored == [
        "factor_manifest_reversal_1d_v1@2026",
        "factor_obs_reversal_1d_v1@2026",
        "factor_proc_reversal_1d_v1@2026",
        "factor_procmn_reversal_1d_v1@2026",
    ]
    assert message.startswith(f"{ONE_YEAR_SET[1]} (factor 2 of 3)")
    for name in stored:
        assert name in message
    assert f"not built: {[ONE_YEAR_SET[2]]}" in message
    # Its own refusal's words, past the store path that differs between the two copies.
    assert str(alone.value).split(": ", 1)[1] in message
    assert raised.value.disclosable != message
    assert str(store.root) not in raised.value.disclosable


def test_a_set_that_cannot_be_put_is_refused_before_anything_is_read(
    scratch: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An undeclared factor, one factor named twice (by key and by id), a supersedes list that
    could only belong to one of several factors, and no factor at all: each refused whole."""
    counts = _counting(monkeypatch, *SHARED_LOADERS)
    reversal = FACTOR_DEFINITIONS.get("reversal_1d/v1")
    for factors, overrides, words in (
        (["reversal_1d/v1", "no_such_factor/v1"], {}, "no_such_factor"),
        (["reversal_1d/v1", reversal.factor_id], {}, "twice"),
        (list(THREE[:2]), {"supersedes_raw": ["fmf_x"]}, "--supersedes-raw"),
        ([], {}, "--factor"),
    ):
        with pytest.raises(FactorRequestError, match=words):
            _shared(scratch, factors, **overrides)

    assert counts == {}
    assert _stored(scratch) == {}


# --- the two faces -------------------------------------------------------------------------------


def _cli(runtime_dir: Path, factors: Sequence[str]) -> Any:
    arguments = ["factor", "build", "--runtime-dir", str(runtime_dir), "--json"]
    for factor in factors:
        arguments.extend(["--factor", factor])
    arguments.extend(["--tier", "neutralized", "--transform", TRANSFORM])
    arguments.extend(["--neutralization", NEUTRALIZATION])
    for instant in INSTANTS:
        arguments.extend(["--as-of", instant.isoformat()])
    for year in YEARS:
        arguments.extend(["--year", str(year)])
    arguments.extend(["--exchange", EXCHANGE, "--max-staleness-days", str(STALENESS_DAYS)])
    arguments.extend(["--code-commit", COMMIT])
    return CliRunner().invoke(app, arguments)


def test_the_command_line_and_the_sdk_build_one_set_of_factors_alike(
    corpus: Path, tmp_path: Path
) -> None:
    """`--factor` is repeatable, and the command prints one `build_view` line per factor --
    one line exactly when one factor is named, so a single-factor caller reads what it read."""
    _copy(corpus, tmp_path / "cli" / "panel")
    _copy(corpus, tmp_path / "sdk" / "panel")

    result = _cli(tmp_path / "cli", THREE)
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: BUILT_AT)
    reports = sdk.build_factor_panel_set(factors=THREE, **_parameters())

    assert result.exit_code == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines == [build_view(report) for report in reports]


def test_a_refused_set_exits_with_the_refused_factors_own_code(
    corpus: Path, tmp_path: Path
) -> None:
    _copy(corpus, tmp_path / "panel")
    arguments = ["factor", "build", "--runtime-dir", str(tmp_path), "--json"]
    for factor in ONE_YEAR_SET:
        arguments.extend(["--factor", factor])
    arguments.extend(["--tier", "raw", "--as-of", INSTANTS[1].isoformat(), "--year", "2026"])
    arguments.extend(["--exchange", EXCHANGE, "--max-staleness-days", str(STALENESS_DAYS)])
    arguments.extend(["--code-commit", COMMIT])

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == int(FACTOR_EXIT[FactorPanelUnreadableError.reason])
    assert ONE_YEAR_SET[1] in result.stderr
    assert "factor_obs_reversal_1d_v1@2026" in result.stderr
