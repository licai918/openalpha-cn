"""`V2-P6-027`: a factor build reads every announcement year its report-period reach needs.

The P6 holdout was refused at 2025-02-05 because `revenue_yoy_acceleration/v1@processed` had no
cross section: 5,375 of 5,395 listed names were `insufficient_history` in raw. Every stored
research build had named `--year` as `as_of`'s year and the two before it, and a statement
partition is filed by **announcement** year. From January to April the newest report every issuer
owes is the previous year's third quarter, so a nine-period window reaches the third quarter three
calendar years back -- announced that October, in a partition the build never read. Every shorter
reach fits inside the three years all year round, which is why no other factor showed it.

`factor_view._requirements` now adds, beneath the years a build names, every stored announcement
year `panel_factors.period_reach_years` says the factor's reach can need at that instant, and
`compute_factor` refuses a requirement set that leaves one out -- so the shape cannot come back
through a caller that builds its own requirements.

## The corpus

Generated at test time (`AGENTS.md` rule 6). 120 securities -- above the 100-name floors of the
shipped transform and neutralisation -- with three calendar years of sessions (2024-2026), the
registry, the industry membership, and the four statement endpoints from 2021Q1 to 2026Q3, each
report announced inside its statutory deadline, so announcement years 2021-2026 are stored. Four
names carry the shapes that decide which windows were computable before:

- `EARLY` announces its 2025 annual on 2026-01-20, so in February its window ends at 2025Q4 and
  starts at 2023Q4, announced in 2024.
- `BACKFILLED` lists in January 2024 and publishes every period through 2023Q3 then -- the shape
  of most of the 20 names the research store did compute on 2025-02-05.
- `RESTATED` restates its 2023Q3 in June 2025: the later announcement wins, and it sits in a year
  the old read did see.
- `LATE` announces its 2025 annual and 2026Q1 on 8 May 2026, after the 30 April deadline. On
  6 May its window still reaches 2023Q3, which the statute does not make anybody owe -- the one
  bound `period_reach_years` states rather than removes.

## The differential

"Values that were already correct are unchanged" is measured rather than argued. Every build here
names `--year 2024 --year 2025 --year 2026`, the stored builds' span for 2026. The same build over
a copy of the corpus with no statement partition before 2024 is the build before this change,
exactly: there is nothing beneath the named years to add and nothing to refuse, so it reads the
rows the old code read. All 21 factors, all three tiers, at five instants from February to
November, are compared between the two stores.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn import factor_view
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
from openalpha_cn.factor_view import factor_build_requests
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorEngineError,
    compute_factor,
    load_factor_manifests,
    load_factor_observations,
    load_processed_factor_observations,
    period_reach_years,
)
from openalpha_cn.panel_ingest import (
    financial_statement_requirement,
    split_panel_batch_by_year,
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
EARLY, BACKFILLED, RESTATED, LATE = SECURITIES[30:34]
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
            values = [
                (50.0 if name == "total_assets" else 1.0)
                * (100.0 + 37.0 * offset)
                * (1.0 + index / 50.0)
                * (1.0 + 0.03 * position + 0.002 * ((index + 1) * position * position % 11))
                + (index * position) % 7
                for offset, name in enumerate(STATEMENT_DATA_COLUMNS[dataset])
            ]
            filing(code, period, _announced(index, code, period), values)
            if code == RESTATED and period == RESTATED_PERIOD:
                filing(code, period, RESTATED_ON, [value * 1.5 for value in values])
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


def _build(store: PanelStore, factors: Sequence[str], instants: Sequence[datetime]) -> None:
    requests = factor_build_requests(
        factors=factors,
        tier="neutralized",
        transform=TRANSFORM,
        neutralization=NEUTRALIZATION,
        as_ofs=instants,
        years=BUILD_YEARS,
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


def _income_years(store: PanelStore, factor: str) -> dict[datetime, tuple[int, ...]]:
    """The `income` announcement years each stored build of `factor` read, by instant."""
    return {
        manifest.as_of: tuple(
            sorted(ref.year for ref in manifest.inputs if ref.dataset == "income")
        )
        for manifest in load_factor_manifests(
            store, FACTOR_DEFINITIONS.get(factor), years=(2026,), as_of=FETCHED_AT
        )
    }


# --- the defect, reproduced and closed ------------------------------------------------------------


def test_a_nine_period_window_reaching_three_announcement_years_back_is_computed(
    corpus: Path, tmp_path: Path
) -> None:
    """The holdout's refusal in miniature, through the face every stored build was made with.

    `--year 2024 --year 2025 --year 2026` at 2026-02-05: the window's oldest filing is 2023Q3,
    announced in October 2023. The build reads 2023 beneath the years it names, and every name
    with nine filings is computed -- including the ones the old read could already answer.
    """
    store = _copy(corpus, tmp_path / "panel")

    _build(store, [ACCELERATION], [FEBRUARY])

    answers = _raw(store, ACCELERATION)[FEBRUARY]
    coverage = {subject: code for subject, (code, _value) in answers.items()}
    listed = {subject for subject, code in coverage.items() if code != "not_in_universe"}
    assert _income_years(store, ACCELERATION) == {FEBRUARY: (2023, 2024, 2025, 2026)}
    assert {coverage[name] for name in listed} == {"computed"}
    assert {EARLY, BACKFILLED, RESTATED, LATE} <= listed
    assert listed == set(SECURITIES) - {YOUNG}  # it lists in May


def test_a_filer_later_than_its_deadline_is_the_one_bound_the_reach_states(
    corpus: Path, tmp_path: Path
) -> None:
    """`LATE` owes its 2025 annual and 2026Q1 by 30 April and announces them on 8 May.

    On 6 May every on-time issuer's window starts at 2024Q1, so nothing beneath 2024 is read and
    `LATE`, whose window still starts at 2023Q3, is `insufficient_history` -- exactly as before
    this change. By June its reports are in and it is computed.
    """
    store = _copy(corpus, tmp_path / "panel")

    _build(store, [ACCELERATION], [MAY, JUNE])

    answers = _raw(store, ACCELERATION)
    assert _income_years(store, ACCELERATION) == {MAY: BUILD_YEARS, JUNE: BUILD_YEARS}
    assert answers[MAY][LATE][0] == "insufficient_history"
    assert answers[MAY][EARLY][0] == "computed"
    assert answers[JUNE][LATE][0] == "computed"


def test_the_engine_refuses_a_requirement_that_cuts_a_stored_reach_short(corpus: Path) -> None:
    """A caller that builds its own requirements cannot bring the shape back.

    The years named are the stored builds' own, and 2023 is stored: a read that leaves it out
    answers `insufficient_history` for nearly everybody, which is a fault in the request -- so it
    is refused, by name, rather than stored as coverage.
    """
    store = PanelStore(corpus)
    definition = FACTOR_DEFINITIONS.get(ACCELERATION)
    requirements = {
        INCOME_DATASET: financial_statement_requirement(
            dataset=INCOME_DATASET,
            years=BUILD_YEARS,
            as_of=FEBRUARY,
            max_staleness=timedelta(days=STALENESS_DAYS),
        )
    }

    with pytest.raises(FactorEngineError, match=r"income .*\[2023\]"):
        compute_factor(
            store,
            definition,
            as_of=FEBRUARY,
            subjects=SECURITIES,
            universe=SECURITIES,
            requirements=requirements,
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )


def test_a_reach_beneath_the_first_stored_year_is_not_refused(truncated: Path) -> None:
    """Nothing older is stored, so there is nothing to name: the read answers from what exists,
    and a window that needs a filing the store does not hold is `insufficient_history`."""
    store = PanelStore(truncated)
    definition = FACTOR_DEFINITIONS.get(ACCELERATION)
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
        definition,
        as_of=FEBRUARY,
        subjects=SECURITIES,
        universe=SECURITIES,
        requirements=requirements,
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )

    assert period_reach_years(definition, as_of=FEBRUARY)[0] == 2023
    coverage = {item.subject: item.coverage for item in panel.observations}
    assert {name for name, code in coverage.items() if code == "computed"} == {
        EARLY,
        BACKFILLED,
        RESTATED,
    }


# --- the differential: what was already right did not move ----------------------------------------


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


def test_the_reads_change_only_where_a_reach_was_cut_short(
    both_builds: tuple[PanelStore, PanelStore],
) -> None:
    """Every factor's `income` read is the old one except the nine-period reach in February and on
    30 April -- so every other build reads exactly the rows the old code read."""
    full, before = both_builds
    moved = {
        (key, instant)
        for key in FACTOR_DEFINITIONS.qualified_keys
        if "income" in FACTOR_DEFINITIONS.get(key).datasets
        for instant, years in _income_years(full, key).items()
        if years != _income_years(before, key)[instant]
    }

    assert moved == {(ACCELERATION, instant) for instant in REACH_BEYOND_THE_BUILD}
    assert {
        _income_years(full, ACCELERATION)[instant][0] for instant in REACH_BEYOND_THE_BUILD
    } == {2023}


@pytest.mark.parametrize("tier", ["raw", "processed", "neutralized"])
def test_every_answer_that_was_already_computable_is_unchanged(
    both_builds: tuple[PanelStore, PanelStore], tier: str
) -> None:
    """All 21 factors, every instant, one tier per case: every answer is the old build's, except
    the nine-period reach where it was cut short -- and there every security the old build
    computed has the same value, and the rest of the cross section is now computed beside it."""
    full, before = both_builds
    read = {"raw": _raw, "processed": _processed, "neutralized": _neutralized}[tier]
    for key in FACTOR_DEFINITIONS.qualified_keys:
        now, then = read(full, key), read(before, key)
        assert set(now) == set(INSTANTS), key
        for instant in INSTANTS:
            if key == ACCELERATION and instant in REACH_BEYOND_THE_BUILD:
                continue
            assert now[instant] == then[instant], (key, tier, instant)

    raw_now, raw_then = _raw(full, ACCELERATION), _raw(before, ACCELERATION)
    computed_then: dict[datetime, set[str]] = {}
    computed_now: dict[datetime, set[str]] = {}
    for instant in REACH_BEYOND_THE_BUILD:
        then = {subject for subject, answer in raw_then[instant].items() if answer[0] == "computed"}
        assert {subject: raw_now[instant][subject] for subject in then} == {
            subject: raw_then[instant][subject] for subject in then
        }, instant
        computed_then[instant] = then
        computed_now[instant] = {
            subject for subject, answer in raw_now[instant].items() if answer[0] == "computed"
        }
    listed = set(SECURITIES) - {YOUNG}
    # February: the old read answered only the three names whose oldest filing sat in a later year.
    assert computed_then[FEBRUARY] == {EARLY, BACKFILLED, RESTATED}
    assert computed_now[FEBRUARY] == listed
    # 30 April: every on-time issuer has filed its annual and its 2026Q1 here, so the old read
    # already answered them; `LATE` is not late yet, still owes nothing past 2025Q3, and only the
    # reach reads the 2023Q3 its window starts at.
    assert computed_then[DEADLINE_DAY] == listed - {DELISTED, LATE}
    assert computed_now[DEADLINE_DAY] == listed - {DELISTED}
