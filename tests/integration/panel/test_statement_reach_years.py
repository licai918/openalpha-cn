"""`V2-P6-027`: a statement factor reads every stored announcement year, whatever years are named.

The P6 holdout was refused at 2025-02-05 because `revenue_yoy_acceleration/v1@processed` had no
cross section: 5,375 of 5,395 listed names were `insufficient_history` in raw. Every stored
research build had named `--year` as `as_of`'s year and the two before it, and a statement
partition is filed by **announcement** year. From January to April the newest report every issuer
owes is the previous year's third quarter, so a nine-period window reaches the third quarter three
calendar years back -- announced that October, in a partition the build never read.

A bound derived from the statutory filing deadlines (this issue's first round) closed that day
and left a late filer's answer depending on the years a caller listed. So the read is the whole
stored history: `factor_view._requirements` adds every stored announcement year beneath the years
a build names, and `compute_factor` refuses, before reading anything, a requirement that leaves
one out.

## The corpus

Generated at test time (`AGENTS.md` rule 6). 120 securities -- above the 100-name floors of the
shipped transform and neutralisation -- with three calendar years of sessions (2024-2026), the
registry, the industry membership, and the four statement endpoints from 2021Q1 to 2026Q3, each
report announced inside its statutory deadline, so announcement years 2021-2026 are stored. Five
names carry the shapes that decide which windows were computable over the old three years:

- `EARLY` announces its 2025 annual on 2026-01-20, so in February its window ends at 2025Q4 and
  starts at 2023Q4, announced in 2024.
- `BACKFILLED` lists in January 2024 and publishes every period through 2023Q3 then -- the shape
  of most of the 20 names the research store did compute on 2025-02-05.
- `RESTATED` restates its 2023Q3 in June 2025: the later announcement wins over the October 2023
  original the full read adds.
- `CONFLICTED` announced its 2023Q3 in October 2023 as two rows of one day that disagree about
  `total_revenue`, and restated it in June 2025. The old read saw only the restatement and
  computed; the full read also sees the same-day disagreement, and the engine's rule is that such
  a disagreement marks its period `ambiguous_filing` for every window that reaches it whether or
  not a later announcement exists. So reading an older year **can** turn a computed value into
  `ambiguous_filing` -- the one way it can change a value the old read computed.
- `LATE` announces its 2025 annual and 2026Q1 on 8 May 2026, after the 30 April deadline. On
  6 May its nine-period window still starts at 2023Q3, which the three named years do not hold.
- `STALE` stops filing after 2024Q3 and keeps trading, so at every instant here its newest period
  is more than one missed statutory deadline old and the recency rule codes it
  `insufficient_history` -- in both stores alike, so the differential below does not see it.
- `HALTED` stops trading after 2024-11-29 and keeps filing. A factor that reads `daily_basic` takes
  its newest session in the years a build names, so in 2026 it is valued from 2024 by a build
  naming 2024 and not by one naming only 2025 and 2026 -- the session axis, which the full
  statement read does not touch, and why the daily selection names the research builds' years.

## The differential

Every build here names `--year 2024 --year 2025 --year 2026`, the stored builds' span for 2026.
The same build over a copy of the corpus with no statement partition before 2024 is the build
before this change, exactly: there is nothing beneath the named years to add and nothing to
refuse, so it reads the rows the old code read. All 21 factors, all three tiers, at five instants
from February to November, are compared between the two stores.
"""

from __future__ import annotations

import importlib
import math
import shlex
import shutil
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import ModuleType
from typing import Final
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from openalpha_cn import factor_view, panel_factors
from openalpha_cn.cli import app
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATA_COLUMNS,
    DAILY_BASIC_DATASET,
    DAILY_DATA_COLUMNS,
    DAILY_DATASET,
    SESSION_CLOSE_TIME,
)
from openalpha_cn.domain.factor import FactorObservation
from openalpha_cn.domain.financial_statements import (
    ANNOUNCEMENT_DATE_COLUMN,
    FIRST_ANNOUNCEMENT_COLUMN,
    INCOME_DATASET,
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
    FACTOR_RUN_LIMITATION_CODES,
    STATEMENT_RECENCY_LIMITATION,
    StaleStatementBuild,
    factor_build_requests,
    merged_build_commands,
    stale_statement_builds,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorEngineError,
    compute_factor,
    load_factor_manifests,
    load_factor_observations,
    load_processed_factor_observations,
)
from openalpha_cn.panel_ingest import (
    financial_statement_requirement,
    merge_panel_batches,
    split_panel_batch_by_year,
    write_empty_announcement_year,
    write_panel_batch,
)
from openalpha_cn.panel_neutralization import (
    FACTOR_NEUTRALIZATIONS,
    load_neutralized_factor_observations,
)

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
EXCHANGE: Final[str] = "SSE"
BUILD_YEARS: Final[tuple[int, ...]] = (2024, 2025, 2026)
"""What every stored research build of a 2026 instant named: `as_of`'s year and the two before."""
FIRST_SESSION_YEAR: Final[int] = 2024
COMMIT: Final[str] = "0123456789abcdef"
BUILT_AT: Final[datetime] = datetime(2026, 12, 1, tzinfo=UTC)
FETCHED_AT: Final[datetime] = datetime(2027, 1, 1, tzinfo=UTC)
STALENESS_DAYS: Final[int] = 200
TRANSFORM: Final[str] = "cross_section_standard/v1"
NEUTRALIZATION: Final[str] = "industry_and_size/v1"
ACCELERATION: Final[str] = "revenue_yoy_acceleration/v1"


def _evening(day: date) -> datetime:
    """17:00 Asia/Shanghai: the day's own session has published, so every tier builds."""
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)


FEBRUARY: Final[datetime] = _evening(date(2026, 2, 5))
DEADLINE_DAY: Final[datetime] = _evening(date(2026, 4, 30))
MAY: Final[datetime] = _evening(date(2026, 5, 6))
JUNE: Final[datetime] = _evening(date(2026, 6, 15))
NOVEMBER: Final[datetime] = _evening(date(2026, 11, 16))
INSTANTS: Final[tuple[datetime, ...]] = (FEBRUARY, DEADLINE_DAY, MAY, JUNE, NOVEMBER)
REACH_BEYOND_THE_BUILD: Final[frozenset[datetime]] = frozenset({FEBRUARY, DEADLINE_DAY})
"""The instants at which a nine-period reach needs 2023, which the build does not name."""

SECURITIES: Final[tuple[str, ...]] = tuple(
    f"{600000 + index:06d}.SH" if index % 2 else f"{index + 1:06d}.SZ" for index in range(120)
)
EARLY, BACKFILLED, RESTATED, LATE, CONFLICTED, HALTED = SECURITIES[30:36]
HALTED_AFTER: Final[date] = date(2024, 11, 29)
STALE: Final[str] = SECURITIES[36]
STALE_THROUGH: Final[date] = date(2024, 9, 30)
"""`STALE` files nothing after 2024Q3 and keeps trading: more than one deadline behind at every
instant here."""
YOUNG, DELISTED, LISTED_IN_2025 = SECURITIES[100:103]
YOUNG_FROM: Final[date] = date(2026, 5, 25)
LISTED_IN_2025_FROM: Final[date] = date(2025, 4, 1)
"""A listing in 2025, so the registry holds a lifecycle partition for every year a build names."""
DELISTED_ON: Final[date] = date(2026, 3, 16)
BACKFILLED_FROM: Final[date] = date(2024, 1, 5)
BACKFILL_ANNOUNCED: Final[date] = date(2024, 1, 10)
EARLY_ANNUAL_ANNOUNCED: Final[date] = date(2026, 1, 20)
RESTATED_PERIOD: Final[date] = date(2023, 9, 30)
RESTATED_ON: Final[date] = date(2025, 6, 10)
LATE_ANNOUNCED: Final[date] = date(2026, 5, 8)

HOLIDAYS: Final[frozenset[date]] = frozenset(
    {
        date(2024, 1, 1),
        *(date(2024, 2, day) for day in range(12, 17)),
        *(date(2024, 10, day) for day in range(1, 8)),
        date(2025, 1, 1),
        *(date(2025, 1, day) for day in range(28, 32)),
        *(date(2025, 10, day) for day in range(1, 9)),
        date(2026, 1, 1),
        date(2026, 1, 2),
        *(date(2026, 2, day) for day in range(16, 21)),
        *(date(2026, 5, day) for day in range(1, 6)),
        *(date(2026, 10, day) for day in range(1, 8)),
    }
)
FIRST_DAY: Final[date] = date(FIRST_SESSION_YEAR, 1, 1)
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
    SECURITIES[21]: date(2026, 1, 5),
    SECURITIES[22]: date(2026, 4, 1),
    SECURITIES[23]: date(2026, 6, 1),
    SECURITIES[24]: date(2026, 8, 3),
    SECURITIES[25]: date(2026, 10, 12),
}
"""Reclassifications spread over the year, so the membership read is never older than the bound."""

PERIODS: Final[tuple[date, ...]] = tuple(
    date(year, month, day)
    for year in range(2021, 2027)
    for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))
    if date(year, month, day) <= date(2026, 9, 30)
)


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
    if code == BACKFILLED:
        return BACKFILLED_FROM
    if code == LISTED_IN_2025:
        return LISTED_IN_2025_FROM
    return date(2008 + index % 15, 1 + index % 12, 1 + index % 27)


def _traded(index: int, code: str, day: date) -> bool:
    if day < _listed_on(index, code):
        return False
    if code == HALTED:
        return day <= HALTED_AFTER
    return not (code == DELISTED and day >= DELISTED_ON)


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


def _registry_batch() -> ColumnarPanelBatch:
    events: list[tuple[str, str, date]] = []
    for index, code in enumerate(SECURITIES):
        events.append((code, LISTING_EVENT, _listed_on(index, code)))
        if code == DELISTED:
            events.append((code, DELISTING_EVENT, DELISTED_ON))
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


def _announced(index: int, code: str, period: date) -> date:
    """Inside the statutory deadline, except for the shapes the module docstring names."""
    if code == BACKFILLED and period <= RESTATED_PERIOD:
        return BACKFILL_ANNOUNCED
    if code == EARLY and period == date(2025, 12, 31):
        return EARLY_ANNUAL_ANNOUNCED
    if code == LATE and period in (date(2025, 12, 31), date(2026, 3, 31)):
        return LATE_ANNOUNCED
    deadline = {
        3: date(period.year, 4, 20),
        6: date(period.year, 8, 20),
        9: date(period.year, 10, 20),
        12: date(period.year + 1, 3, 20),
    }[period.month]
    return deadline + timedelta(days=index % 5)


def _statement_batch(dataset: str) -> ColumnarPanelBatch:
    """Every stored column of one endpoint; positive, and growing at a rate that moves, so every
    growth rate and every acceleration is defined and no two securities share one."""
    rows: list[tuple[str, datetime, datetime, datetime]] = []
    cells: dict[str, list[object]] = {name: [] for name in statement_panel_columns(dataset)}

    def filing(code: str, period: date, announced: date, values: Sequence[float]) -> None:
        stamp = _midnight(announced)
        rows.append((code, stamp, stamp, stamp))
        cells[REPORT_PERIOD_COLUMN].append(period.isoformat())
        cells[ANNOUNCEMENT_DATE_COLUMN].append(announced.isoformat())
        if FIRST_ANNOUNCEMENT_COLUMN in cells:
            cells[FIRST_ANNOUNCEMENT_COLUMN].append(announced.isoformat())
            cells[REVISION_LABEL_COLUMN].append("0")
        for name, value in zip(STATEMENT_DATA_COLUMNS[dataset], values, strict=True):
            cells[name].append(value)

    for index, code in enumerate(SECURITIES):
        for position, period in enumerate(PERIODS):
            if code == STALE and period > STALE_THROUGH:
                continue
            values = [
                (50.0 if name == "total_assets" else 1.0)
                * (100.0 + 37.0 * offset)
                * (1.0 + index / 50.0)
                * (1.0 + 0.03 * position + 0.002 * ((index + 1) * position * position % 11))
                + (index * position) % 7
                for offset, name in enumerate(STATEMENT_DATA_COLUMNS[dataset])
            ]
            filing(code, period, _announced(index, code, period), values)
            if code in (RESTATED, CONFLICTED) and period == RESTATED_PERIOD:
                filing(code, period, RESTATED_ON, [value * 1.5 for value in values])
            if code == CONFLICTED and period == RESTATED_PERIOD and dataset == INCOME_DATASET:
                disputed = list(values)
                disputed[STATEMENT_DATA_COLUMNS[dataset].index("total_revenue")] += 7.0
                filing(code, period, _announced(index, code, period), disputed)
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
        first = max(date(FIRST_SESSION_YEAR, 1, 2), _listed_on(index, code))
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


def _write_corpus(root: Path, *, first_statement_year: int) -> None:
    store = PanelStore(root)
    for batch in (_calendar_batch(), _registry_batch(), *_session_batches(), _membership_batch()):
        for year, part in split_panel_batch_by_year(batch):
            write_panel_batch(store, part, year=year)
    for dataset in STATEMENT_DATA_COLUMNS:
        for year, part in split_panel_batch_by_year(_statement_batch(dataset)):
            if year >= first_statement_year:
                write_panel_batch(store, part, year=year)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every statement announcement year, 2021-2026. Copied by each test, because a build writes."""
    root = tmp_path_factory.mktemp("reach_corpus") / "panel"
    _write_corpus(root, first_statement_year=PERIODS[0].year)
    return root


@pytest.fixture(scope="module")
def truncated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same corpus with no statement partition before the build's first named year."""
    root = tmp_path_factory.mktemp("truncated_corpus") / "panel"
    _write_corpus(root, first_statement_year=BUILD_YEARS[0])
    return root


def _copy(source: Path, target: Path) -> PanelStore:
    shutil.copytree(source, target)
    return PanelStore(target)


def _daily_selection_script() -> ModuleType:
    """`scripts/daily_selection.py`, imported the way `tests/unit/scripts` imports it."""
    directory = str(Path(__file__).resolve().parents[3] / "scripts")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module("daily_selection")


def _build(
    store: PanelStore,
    factors: Sequence[str],
    instants: Sequence[datetime],
    *,
    years: Sequence[int] = BUILD_YEARS,
    tier: str = "neutralized",
) -> None:
    requests = factor_build_requests(
        factors=factors,
        tier=tier,
        transform=TRANSFORM if tier != "raw" else "",
        neutralization=NEUTRALIZATION if tier == "neutralized" else "",
        as_ofs=instants,
        years=years,
        exchange=EXCHANGE,
        max_staleness_days=STALENESS_DAYS,
        waive_max_staleness=False,
        subjects=[],
        supersedes_raw=[],
        supersedes_processed=[],
        supersedes_neutralized=[],
        code_commit=COMMIT,
    )
    factor_view.build_factor_panel_set(store, requests, built_at=BUILT_AT)


Answers = dict[datetime, dict[str, tuple[str, float | None]]]


def _raw(store: PanelStore, factor: str) -> Answers:
    answers: Answers = {}
    for item in load_factor_observations(
        store, FACTOR_DEFINITIONS.get(factor), years=(2026,), as_of=FETCHED_AT
    ):
        answers.setdefault(item.as_of, {})[item.subject] = (item.coverage, item.value)
    return answers


def _processed(store: PanelStore, factor: str) -> Answers:
    answers: Answers = {}
    for item in load_processed_factor_observations(
        store,
        FACTOR_DEFINITIONS.get(factor),
        FACTOR_TRANSFORMS.get(TRANSFORM),
        years=(2026,),
        as_of=FETCHED_AT,
    ):
        answers.setdefault(item.as_of, {})[item.subject] = (item.coverage, item.value)
    return answers


def _neutralized(store: PanelStore, factor: str) -> Answers:
    answers: Answers = {}
    for item in load_neutralized_factor_observations(
        store,
        FACTOR_DEFINITIONS.get(factor),
        FACTOR_NEUTRALIZATIONS.get(NEUTRALIZATION),
        years=(2026,),
        as_of=FETCHED_AT,
    ):
        answers.setdefault(item.as_of, {})[item.subject] = (item.coverage, item.value)
    return answers


def _statement_years(store: PanelStore, factor: str) -> dict[datetime, dict[str, tuple[int, ...]]]:
    """The announcement years each stored build of `factor` read, by instant and statement."""
    definition = FACTOR_DEFINITIONS.get(factor)
    return {
        manifest.as_of: {
            dataset: tuple(sorted(ref.year for ref in manifest.inputs if ref.dataset == dataset))
            for dataset in definition.datasets
            if dataset in STATEMENT_DATA_COLUMNS
        }
        for manifest in load_factor_manifests(store, definition, years=(2026,), as_of=FETCHED_AT)
    }


STATEMENT_FACTORS: Final[tuple[str, ...]] = tuple(
    key
    for key in FACTOR_DEFINITIONS.qualified_keys
    if set(FACTOR_DEFINITIONS.get(key).datasets) & set(STATEMENT_DATA_COLUMNS)
)
STORED_STATEMENT_YEARS: Final[tuple[int, ...]] = tuple(range(PERIODS[0].year, 2027))
VALUATION_FACTORS: Final[tuple[str, ...]] = tuple(
    key for key in STATEMENT_FACTORS if DAILY_BASIC_DATASET in FACTOR_DEFINITIONS.get(key).datasets
)
"""The four statement factors that also read a session: each security's newest `daily_basic`
row in the years a build names, so their answer for a long-halted name depends on those years."""


# --- the defect, reproduced and closed ------------------------------------------------------------


def test_a_nine_period_window_reaching_three_announcement_years_back_is_computed(
    corpus: Path, tmp_path: Path
) -> None:
    """The holdout's refusal in miniature, through the face every stored build was made with.

    `--year 2024 --year 2025 --year 2026` at 2026-02-05: the window's oldest filing is 2023Q3,
    announced in October 2023. The build reads every stored announcement year, and every listed
    name with nine filings is computed -- except `CONFLICTED`, whose 2023Q3 the full read shows
    was stated twice on one day with two different revenues.
    """
    store = _copy(corpus, tmp_path / "panel")

    _build(store, [ACCELERATION], [FEBRUARY])

    answers = _raw(store, ACCELERATION)[FEBRUARY]
    coverage = {subject: code for subject, (code, _value) in answers.items()}
    listed = {subject for subject, code in coverage.items() if code != "not_in_universe"}
    assert _statement_years(store, ACCELERATION) == {FEBRUARY: {"income": STORED_STATEMENT_YEARS}}
    assert listed == set(SECURITIES) - {YOUNG}  # it lists in May
    assert coverage[CONFLICTED] == "ambiguous_filing"
    assert coverage[STALE] == "insufficient_history"  # more than one deadline behind
    assert {coverage[name] for name in listed - {CONFLICTED, STALE}} == {"computed"}


def test_a_late_filers_answer_does_not_depend_on_the_years_named(
    corpus: Path, tmp_path: Path
) -> None:
    """`LATE` owes its 2025 annual and 2026Q1 by 30 April and announces them on 8 May.

    On 6 May its nine-period window is 2023Q3..2025Q3. The deadline-derived bound of this issue's
    first round read nothing beneath 2024 that day and coded it `insufficient_history`; the
    stored history holds the filing and it is computed.
    """
    store = _copy(corpus, tmp_path / "panel")

    _build(store, [ACCELERATION], [MAY, JUNE])

    answers = _raw(store, ACCELERATION)
    assert answers[MAY][LATE][0] == "computed"
    assert answers[JUNE][LATE][0] == "computed"


@pytest.mark.parametrize(
    "years",
    [pytest.param((2025, 2026), id="daily-selection-span"), pytest.param((2026,), id="one-year")],
)
def test_every_statement_factor_answers_the_same_whatever_years_are_named(
    corpus: Path, tmp_path: Path, years: tuple[int, ...]
) -> None:
    """A build may name two years or one; the research builds named three. All 11 statement
    factors, in February and June, read the same announcement years either way, and the seven
    that read nothing but statements store the research span's answers. The four that also read
    `daily_basic` are held to the research span by the daily selection's own years instead
    (`test_the_daily_selection_builds_what_the_research_span_builds`)."""
    research = _copy(corpus, tmp_path / "research")
    other = _copy(corpus, tmp_path / "other")
    instants = [FEBRUARY, JUNE]

    _build(research, STATEMENT_FACTORS, instants, tier="raw")
    _build(other, STATEMENT_FACTORS, instants, tier="raw", years=years)

    assert len(STATEMENT_FACTORS) == 11
    assert len(VALUATION_FACTORS) == 4
    for key in STATEMENT_FACTORS:
        if key not in VALUATION_FACTORS:
            assert _raw(other, key) == _raw(research, key), key
        assert _statement_years(other, key) == _statement_years(research, key), key
        for read in _statement_years(other, key).values():
            assert set(read.values()) == {STORED_STATEMENT_YEARS}, key


def test_the_daily_selection_builds_what_the_research_span_builds(
    corpus: Path, tmp_path: Path
) -> None:
    """`scripts/daily_selection.py::_factor_years` names `factor_view.factor_build_years`, the
    research builds' span, so the live selection and the research store agree row for row on the
    four statement factors that read `daily_basic` -- including `HALTED`, whose newest session is
    in 2024. Naming only 2025 and 2026 would value it in research and not live."""
    daily = _daily_selection_script()
    research = _copy(corpus, tmp_path / "research")
    live = _copy(corpus, tmp_path / "live")
    narrow = _copy(corpus, tmp_path / "narrow")
    session = FEBRUARY.astimezone(SHANGHAI).date()

    live_years = daily._factor_years(live, session)
    _build(research, VALUATION_FACTORS, [FEBRUARY], tier="raw")
    _build(live, VALUATION_FACTORS, [FEBRUARY], tier="raw", years=live_years)
    _build(narrow, VALUATION_FACTORS, [FEBRUARY], tier="raw", years=(2025, 2026))

    assert live_years == factor_view.factor_build_years(2026) == BUILD_YEARS
    for key in VALUATION_FACTORS:
        assert _raw(live, key) == _raw(research, key), key
        assert _raw(research, key)[FEBRUARY][HALTED][0] == "computed", key
        assert _raw(narrow, key)[FEBRUARY][HALTED][0] == "insufficient_history", key


def test_the_engine_refuses_a_statement_read_that_skips_a_stored_year_before_reading(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that builds its own requirements cannot bring the shape back, and the refusal
    comes before a single partition is read."""
    store = PanelStore(corpus)
    definition = FACTOR_DEFINITIONS.get(ACCELERATION)
    requirements = {
        INCOME_DATASET: financial_statement_requirement(
            dataset=INCOME_DATASET,
            years=BUILD_YEARS,
            as_of=JUNE,
            max_staleness=timedelta(days=STALENESS_DAYS),
        )
    }

    def no_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a partition was read before the refusal")

    monkeypatch.setattr(PanelStore, "read_visible_at", no_read)
    with pytest.raises(
        FactorEngineError, match=r"income, which is stored for \[2021, 2022, 2023\]"
    ):
        compute_factor(
            store,
            definition,
            as_of=JUNE,
            subjects=SECURITIES,
            universe=SECURITIES,
            requirements=requirements,
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )


def test_a_store_with_nothing_beneath_the_named_years_reads_what_it_holds(
    truncated: Path,
) -> None:
    """Nothing older is stored, so there is nothing to name: the read answers from what exists,
    and a window that needs a filing the store does not hold is `insufficient_history`."""
    store = PanelStore(truncated)
    requirements = {
        INCOME_DATASET: financial_statement_requirement(
            dataset=INCOME_DATASET,
            years=BUILD_YEARS,
            as_of=FEBRUARY,
            max_staleness=timedelta(days=STALENESS_DAYS),
        )
    }

    panel = compute_factor(
        store,
        FACTOR_DEFINITIONS.get(ACCELERATION),
        as_of=FEBRUARY,
        subjects=SECURITIES,
        universe=SECURITIES,
        requirements=requirements,
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )

    coverage = {item.subject: item.coverage for item in panel.observations}
    assert {name for name, code in coverage.items() if code == "computed"} == {
        EARLY,
        BACKFILLED,
        RESTATED,
        CONFLICTED,
    }


# --- the differential: what moved, and what did not -----------------------------------------------


@pytest.fixture(scope="module")
def both_builds(
    corpus: Path, truncated: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[PanelStore, PanelStore]:
    """All 21 factors x three tiers x five instants, once over each corpus."""
    root = tmp_path_factory.mktemp("differential")
    full = _copy(corpus, root / "full")
    before = _copy(truncated, root / "before")
    keys = FACTOR_DEFINITIONS.qualified_keys
    _build(full, keys, INSTANTS)
    _build(before, keys, INSTANTS)
    return full, before


MOVED: Final[frozenset[tuple[str, datetime]]] = frozenset(
    {(ACCELERATION, FEBRUARY), (ACCELERATION, DEADLINE_DAY), (ACCELERATION, MAY)}
)
"""Where the old three-year read cut a window short: February and 30 April for every issuer that
had not yet announced its annual, 6 May for `LATE`."""


def test_every_statement_build_reads_the_whole_stored_history(
    both_builds: tuple[PanelStore, PanelStore],
) -> None:
    full, before = both_builds
    for key in STATEMENT_FACTORS:
        for read in _statement_years(full, key).values():
            assert set(read.values()) == {STORED_STATEMENT_YEARS}, key
        for read in _statement_years(before, key).values():
            assert set(read.values()) == {BUILD_YEARS}, key


@pytest.mark.parametrize("tier", ["raw", "processed", "neutralized"])
def test_every_answer_outside_a_cut_short_window_is_unchanged(
    both_builds: tuple[PanelStore, PanelStore], tier: str
) -> None:
    """All 21 factors, every instant, one tier per case: identical wherever the old read already
    held every window. Where it did not, see the next test."""
    full, before = both_builds
    read = {"raw": _raw, "processed": _processed, "neutralized": _neutralized}[tier]
    for key in FACTOR_DEFINITIONS.qualified_keys:
        now, then = read(full, key), read(before, key)
        assert set(now) == set(INSTANTS), key
        for instant in INSTANTS:
            if (key, instant) not in MOVED:
                assert now[instant] == then[instant], (key, tier, instant)


def test_where_a_window_was_cut_short_what_moved_is_exactly_the_shapes_that_explain_it(
    both_builds: tuple[PanelStore, PanelStore],
) -> None:
    """Every raw answer the old read gave at the three moved instants is unchanged, with one
    exception the engine's rule makes on purpose: `CONFLICTED`, computed from its June 2025
    restatement over the old read, is `ambiguous_filing` once the October 2023 same-day pair is
    read -- a disagreement marks its period whether or not a later announcement exists. Every
    other change is an `insufficient_history` the full read answers."""
    full, before = both_builds
    now, then = _raw(full, ACCELERATION), _raw(before, ACCELERATION)
    listed_in_spring = set(SECURITIES) - {YOUNG}
    expected_then_computed = {
        FEBRUARY: {EARLY, BACKFILLED, RESTATED, CONFLICTED},
        DEADLINE_DAY: listed_in_spring - {DELISTED, LATE, STALE},
        MAY: listed_in_spring - {DELISTED, LATE, STALE},
    }
    for instant in (FEBRUARY, DEADLINE_DAY, MAY):
        computed_then = {s for s, answer in then[instant].items() if answer[0] == "computed"}
        assert computed_then == expected_then_computed[instant], instant
        moved = {s for s in then[instant] if now[instant][s] != then[instant][s]}
        for subject in moved:
            assert then[instant][subject][0] == "insufficient_history" or subject == CONFLICTED, (
                instant,
                subject,
            )
    assert now[FEBRUARY][CONFLICTED][0] == "ambiguous_filing"
    assert then[FEBRUARY][CONFLICTED][0] == "computed"
    assert now[DEADLINE_DAY][CONFLICTED] == then[DEADLINE_DAY][CONFLICTED]
    assert {s for s, answer in now[FEBRUARY].items() if answer[0] == "computed"} == (
        listed_in_spring - {CONFLICTED, STALE}
    )
    assert now[DEADLINE_DAY][LATE][0] == now[MAY][LATE][0] == "computed"


# --- the detector: what a store built before this change has to rebuild --------------------------


def _store_built_before_this_change(
    corpus: Path, truncated: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> PanelStore:
    """Every statement factor built as the stored research builds were, then the older years
    stored.

    The builds read the three named years -- nothing older is stored yet -- under an engine with
    no recency rule, which is the stored builds' engine exactly; writing the older statement
    partitions afterwards leaves the store in the research store's shape.
    """
    store = _copy(truncated, root / "panel")
    with monkeypatch.context() as patched:
        patched.setattr(panel_factors, "oldest_admissible_newest_period", lambda _day: date.min)
        _build(store, STATEMENT_FACTORS, INSTANTS)
    for dataset in STATEMENT_DATA_COLUMNS:
        for year, part in split_panel_batch_by_year(_statement_batch(dataset)):
            if year < BUILD_YEARS[0]:
                write_panel_batch(store, part, year=year)
    assert set(store.registered_years(INCOME_DATASET)) == set(STORED_STATEMENT_YEARS)
    return store


def _detect(store: PanelStore, calls: list[int]) -> tuple[StaleStatementBuild, ...]:
    return stale_statement_builds(
        store,
        exchange=EXCHANGE,
        max_staleness_days=STALENESS_DAYS,
        as_of=FETCHED_AT,
        code_commit=COMMIT,
        budget=calls.append,
    )


BOOK_TO_PRICE: Final[str] = "book_to_price/v1"


def test_the_detector_lists_exactly_the_builds_whose_answers_moved_and_its_repair_clears_them(
    corpus: Path,
    truncated: Path,
    tmp_path: Path,
    both_builds: tuple[PanelStore, PanelStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """55 stored raw builds read fewer years than a build reads now. Three hold a security a
    newly read year can move and are asked again -- the three that moved that way. Five more
    hold `STALE`'s `book_to_price`, computed from its 2024Q3 balance sheet by an engine with no
    recency rule and `insufficient_history` now, which is decided without asking: its window is
    the stored one and its newest period is older than the floor. The printed repair is two
    commands; once they have run the detector answers none and both factors' stored answers are
    a fresh build's."""
    store = _store_built_before_this_change(corpus, truncated, tmp_path, monkeypatch)
    calls: list[int] = []

    stale = _detect(store, calls)

    assert calls == [3]
    assert [(item.factor, item.as_of) for item in stale] == [
        *((BOOK_TO_PRICE, instant) for instant in INSTANTS),
        (ACCELERATION, FEBRUARY),
        (ACCELERATION, DEADLINE_DAY),
        (ACCELERATION, MAY),
    ]
    for item in stale[:5]:
        assert (item.asked, item.decided) == (0, 1)
        assert dict(item.transitions) == {("computed", "insufficient_history"): 1}
        assert item.examples == (STALE,)
    by_instant = {item.as_of: item for item in stale[5:]}
    february = by_instant[FEBRUARY].transitions
    assert february[("computed", "ambiguous_filing")] == 1
    assert february[("insufficient_history", "computed")] == len(SECURITIES) - 1 - 4 - 1
    assert dict(by_instant[DEADLINE_DAY].transitions) == {("insufficient_history", "computed"): 1}
    assert by_instant[MAY].examples == (LATE,)
    for item in stale[5:]:
        assert item.read == {"income": BUILD_YEARS}
        assert item.reads_now == {"income": STORED_STATEMENT_YEARS}
    for item in stale:
        assert item.refusal is None
        assert [build.tier for build in item.builds] == ["raw", "processed", "neutralized"]

    commands = merged_build_commands([c for item in stale for c in item.commands])
    assert [command.count("--as-of") for command in commands] == [5, 3]
    for command in commands:
        result = CliRunner().invoke(app, [*command, "--runtime-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output

    assert _detect(store, calls) == ()
    full, _before = both_builds
    for key in (BOOK_TO_PRICE, ACCELERATION):
        assert _raw(store, key) == _raw(full, key), key
        assert _processed(store, key) == _processed(full, key), key
        assert _neutralized(store, key) == _neutralized(full, key), key


def test_an_old_announcement_year_recorded_empty_does_not_stop_the_listing(
    corpus: Path, truncated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An announcement year recorded empty holds no row to narrow with and is refused as stale
    after its first deadline. The narrowing skips it, and a build the engine then refuses to
    answer is listed with the refusal instead of ending the listing."""
    store = _store_built_before_this_change(corpus, truncated, tmp_path, monkeypatch)
    write_empty_announcement_year(
        store,
        dataset=INCOME_DATASET,
        year=2020,
        observed_at=datetime(2020, 1, 2, 1, 0, tzinfo=UTC),
    )

    stale = _detect(store, [])

    refused = [item for item in stale if item.refusal is not None]
    assert {item.factor for item in refused} == {ACCELERATION}
    assert all("2020" in str(item.refusal) for item in refused)
    assert [item.factor for item in stale if item.refusal is None] == [BOOK_TO_PRICE] * 5


def test_the_command_lists_and_exits_one_then_says_none(
    corpus: Path, truncated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store_built_before_this_change(corpus, truncated, tmp_path, monkeypatch)
    arguments = [
        "factor",
        "stale-statement-builds",
        "--runtime-dir",
        str(tmp_path),
        "--max-staleness-days",
        str(STALENESS_DAYS),
        "--code-commit",
        COMMIT,
        "--as-of",
        FETCHED_AT.isoformat(),
    ]

    listed = CliRunner().invoke(app, arguments)

    assert listed.exit_code == 1, listed.output
    assert "stale statement builds: 8" in listed.output
    assert f"STALE {ACCELERATION} neutralized 2026 {MAY.isoformat()}" in listed.output
    assert "insufficient_history->computed 1" in listed.output
    assert "computed->insufficient_history 1" in listed.output
    assert "repair, in this order (2 commands):" in listed.output
    repairs = [
        line.strip() for line in listed.output.splitlines() if line.startswith("  openalpha ")
    ]
    assert len(repairs) == 2
    for repair in repairs:
        rebuilt = CliRunner().invoke(app, shlex.split(repair)[1:])
        assert rebuilt.exit_code == 0, rebuilt.output

    cleared = CliRunner().invoke(app, arguments)
    assert cleared.exit_code == 0, cleared.output
    assert "stale statement builds: none" in cleared.output
    one = CliRunner().invoke(app, [*arguments, "--factor", BOOK_TO_PRICE])
    assert one.exit_code == 0, one.output
    assert set(store.registered_years(INCOME_DATASET)) == set(STORED_STATEMENT_YEARS)


STOPPED: Final[str] = "699999.SH"
"""A security outside the corpus that filed through 2023Q3 and never again."""


def _income_rows_of(code: str, index: int, periods: Sequence[date]) -> ColumnarPanelBatch:
    rows: list[tuple[str, datetime, datetime, datetime]] = []
    cells: dict[str, list[object]] = {name: [] for name in statement_panel_columns(INCOME_DATASET)}
    for position, period in enumerate(periods):
        announced = _announced(index, code, period)
        stamp = _midnight(announced)
        rows.append((code, stamp, stamp, stamp))
        cells[REPORT_PERIOD_COLUMN].append(period.isoformat())
        cells[ANNOUNCEMENT_DATE_COLUMN].append(announced.isoformat())
        if FIRST_ANNOUNCEMENT_COLUMN in cells:
            cells[FIRST_ANNOUNCEMENT_COLUMN].append(announced.isoformat())
            cells[REVISION_LABEL_COLUMN].append("0")
        for offset, name in enumerate(STATEMENT_DATA_COLUMNS[INCOME_DATASET]):
            cells[name].append((100.0 + 37.0 * offset) * (1.0 + 0.03 * position) + index)
    return _batch(
        INCOME_DATASET,
        rows,
        cells,
        (
            REPORT_PERIOD_COLUMN,
            ANNOUNCEMENT_DATE_COLUMN,
            FIRST_ANNOUNCEMENT_COLUMN,
            REVISION_LABEL_COLUMN,
        ),
    )


def test_a_security_that_stopped_filing_years_ago_is_not_valued_on_its_last_filings(
    tmp_path: Path,
) -> None:
    """The full history holds a security that stopped filing in 2023, and the recency rule is
    what keeps it out of a June 2026 cross section: its window 2022Q3..2023Q3 is many missed
    deadlines old, so it is `insufficient_history` with that window recorded."""
    store = PanelStore(tmp_path / "panel")
    regular, stopped = SECURITIES[0], STOPPED
    merged = merge_panel_batches(
        (
            _income_rows_of(regular, 0, PERIODS),
            _income_rows_of(
                stopped, 7, [period for period in PERIODS if period <= RESTATED_PERIOD]
            ),
        )
    )
    for year, part in split_panel_batch_by_year(merged):
        write_panel_batch(store, part, year=year)
    requirements = {
        INCOME_DATASET: financial_statement_requirement(
            dataset=INCOME_DATASET,
            years=STORED_STATEMENT_YEARS,
            as_of=JUNE,
            max_staleness=timedelta(days=STALENESS_DAYS),
        )
    }

    panel = compute_factor(
        store,
        FACTOR_DEFINITIONS.get("revenue_yoy/v1"),
        as_of=JUNE,
        subjects=(regular, stopped),
        universe=(regular, stopped),
        requirements=requirements,
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )

    answers = {item.subject: item for item in panel.observations}
    assert STATEMENT_RECENCY_LIMITATION == (
        "a_statement_window_may_end_one_missed_statutory_deadline_behind"
    )
    assert STATEMENT_RECENCY_LIMITATION in FACTOR_RUN_LIMITATION_CODES
    assert answers[stopped].coverage == "insufficient_history"
    assert answers[stopped].input_period_last == RESTATED_PERIOD
    assert answers[regular].coverage == "computed"
    assert answers[regular].input_period_last == date(2026, 3, 31)


# --- the recency rule: at most one missed statutory deadline (`V2-P6-027`, round 4) -------------


RECENCY_PERIODS: Final[tuple[date, ...]] = tuple(
    period for period in PERIODS if date(2023, 3, 31) <= period <= date(2026, 3, 31)
)


def _on_time(period: date) -> date:
    """Twenty days after the quarter for an interim, 20 March for an annual: inside every
    deadline."""
    return {
        3: date(period.year, 4, 20),
        6: date(period.year, 8, 20),
        9: date(period.year, 10, 20),
        12: date(period.year + 1, 3, 20),
    }[period.month]


def _filings(
    code: str, through: date, *, extra: Sequence[tuple[date, date]] = ()
) -> ColumnarPanelBatch:
    """`code`'s income filings of every recency period up to `through`, each on time, plus
    `(period, announced)` pairs in `extra` -- a late filing or a restatement."""
    rows: list[tuple[str, datetime, datetime, datetime]] = []
    cells: dict[str, list[object]] = {name: [] for name in statement_panel_columns(INCOME_DATASET)}
    filings = [(period, _on_time(period)) for period in RECENCY_PERIODS if period <= through]
    for position, (period, announced) in enumerate([*filings, *extra]):
        stamp = _midnight(announced)
        rows.append((code, stamp, stamp, stamp))
        cells[REPORT_PERIOD_COLUMN].append(period.isoformat())
        cells[ANNOUNCEMENT_DATE_COLUMN].append(announced.isoformat())
        if FIRST_ANNOUNCEMENT_COLUMN in cells:
            cells[FIRST_ANNOUNCEMENT_COLUMN].append(announced.isoformat())
            cells[REVISION_LABEL_COLUMN].append("0")
        index = RECENCY_PERIODS.index(period)
        for offset, name in enumerate(STATEMENT_DATA_COLUMNS[INCOME_DATASET]):
            cells[name].append((100.0 + 37.0 * offset) * (1.0 + 0.05 * index) + position % 2)
    return _batch(
        INCOME_DATASET,
        rows,
        cells,
        (
            REPORT_PERIOD_COLUMN,
            ANNOUNCEMENT_DATE_COLUMN,
            FIRST_ANNOUNCEMENT_COLUMN,
            REVISION_LABEL_COLUMN,
        ),
    )


ON_TIME, ONE_MISSED, TWO_MISSED, BOTH_APRIL, RESTATED_ONLY, CAUGHT_UP = (
    "600001.SH",
    "600002.SH",
    "600003.SH",
    "600004.SH",
    "600005.SH",
    "600006.SH",
)
RECENCY_SHAPES: Final[dict[str, ColumnarPanelBatch]] = {
    # Every report, through 2026Q1.
    ON_TIME: _filings(ON_TIME, date(2026, 3, 31)),
    # Nothing after 2025H1: on 5 February 2026 it has missed one deadline (31 October).
    ONE_MISSED: _filings(ONE_MISSED, date(2025, 6, 30)),
    # Nothing after 2025Q1: it has missed 31 August and 31 October.
    TWO_MISSED: _filings(TWO_MISSED, date(2025, 3, 31)),
    # Through 2025Q3, then neither its 2025 annual nor its 2026Q1 by 30 April 2026: one missed
    # deadline, two periods behind.
    BOTH_APRIL: _filings(BOTH_APRIL, date(2025, 9, 30)),
    # Nothing after 2025Q1, and both its last periods restated in January 2026: restating old
    # periods is a later announcement, not a newer period.
    RESTATED_ONLY: _filings(
        RESTATED_ONLY,
        date(2025, 3, 31),
        extra=((date(2024, 12, 31), date(2026, 1, 15)), (date(2025, 3, 31), date(2026, 1, 15))),
    ),
    # Nothing after 2025Q1 on time, then 2025H1 filed late on 20 January 2026.
    CAUGHT_UP: _filings(
        CAUGHT_UP, date(2025, 3, 31), extra=((date(2025, 6, 30), date(2026, 1, 20)),)
    ),
}


@pytest.fixture(scope="module")
def recency_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    store = PanelStore(tmp_path_factory.mktemp("recency") / "panel")
    merged = merge_panel_batches(tuple(RECENCY_SHAPES.values()))
    for year, part in split_panel_batch_by_year(merged):
        write_panel_batch(store, part, year=year)
    return store


def _recency(store: PanelStore, as_of: datetime) -> dict[str, FactorObservation]:
    years = tuple(store.registered_years(INCOME_DATASET))
    panel = compute_factor(
        store,
        FACTOR_DEFINITIONS.get("revenue_yoy/v1"),
        as_of=as_of,
        subjects=tuple(RECENCY_SHAPES),
        universe=tuple(RECENCY_SHAPES),
        requirements={
            INCOME_DATASET: financial_statement_requirement(
                dataset=INCOME_DATASET,
                years=years,
                as_of=as_of,
                max_staleness=timedelta(days=STALENESS_DAYS),
            )
        },
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )
    return {item.subject: item for item in panel.observations}


def _shanghai(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def test_one_missed_deadline_is_admissible_and_two_are_not(recency_store: PanelStore) -> None:
    """On 5 February 2026 the deadlines passed are 31 October (2025Q3) and, before it, 31 August
    (2025H1): a window ending at 2025H1 has missed one, one ending at 2025Q1 two. The stale one is
    `insufficient_history` with its window recorded, the code a span overrun gets."""
    answers = _recency(recency_store, FEBRUARY)

    assert answers[ON_TIME].coverage == "computed"
    assert answers[ON_TIME].input_period_last == date(2025, 9, 30)
    assert answers[ONE_MISSED].coverage == "computed"
    assert answers[TWO_MISSED].coverage == "insufficient_history"
    assert answers[TWO_MISSED].input_period_last == date(2025, 3, 31)
    assert answers[TWO_MISSED].input_period_first == date(2024, 3, 31)


def test_one_missed_april_deadline_two_periods_behind_is_admissible(
    recency_store: PanelStore,
) -> None:
    """The annual and the first quarter share 30 April, so one missed deadline can leave an
    issuer two periods behind. On 6 May 2026 `BOTH_APRIL` (through 2025Q3) is computed and
    `ONE_MISSED` (through 2025H1, so 31 October missed too) is not."""
    answers = _recency(recency_store, MAY)

    assert answers[BOTH_APRIL].coverage == "computed"
    assert answers[BOTH_APRIL].input_period_last == date(2025, 9, 30)
    assert answers[ONE_MISSED].coverage == "insufficient_history"
    assert answers[ON_TIME].input_period_last == date(2026, 3, 31)


@pytest.mark.parametrize(
    ("as_of", "admissible"),
    [
        pytest.param(_shanghai(date(2026, 4, 30), 0, 0), True, id="deadline-day-midnight"),
        pytest.param(_shanghai(date(2026, 4, 30), 16, 30), True, id="deadline-day-close"),
        pytest.param(_shanghai(date(2026, 5, 1), 0, 0), False, id="the-day-after"),
    ],
)
def test_a_deadline_day_is_not_yet_past(
    recency_store: PanelStore, as_of: datetime, admissible: bool
) -> None:
    """On 30 April, at any hour, the deadline that day is not yet past -- a report announced that
    evening is visible the next day -- so a window ending at 2025H1 has missed only 31 October.
    From 1 May it has missed 30 April too."""
    answers = _recency(recency_store, as_of)

    assert (answers[ONE_MISSED].coverage == "computed") is admissible


def test_restating_old_periods_does_not_make_a_stale_issuer_fresh(
    recency_store: PanelStore,
) -> None:
    """The rule is on the window's newest **period**, not its newest announcement: restating
    2024Q4 and 2025Q1 in January 2026 leaves `RESTATED_ONLY` two deadlines behind, while filing
    2025H1 late on 20 January 2026 brings `CAUGHT_UP` back within one."""
    answers = _recency(recency_store, FEBRUARY)

    assert answers[RESTATED_ONLY].coverage == "insufficient_history"
    assert answers[CAUGHT_UP].coverage == "computed"
    assert answers[CAUGHT_UP].input_period_last == date(2025, 6, 30)
