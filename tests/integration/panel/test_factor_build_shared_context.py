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
import shlex
import shutil
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
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
from openalpha_cn.domain.factor_transform import FactorTransformRegistry, FactorTransformSpec
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
from openalpha_cn.domain.upstream_defects import UPSTREAM_DEFECT_DATA_COLUMNS
from openalpha_cn.factor_view import (
    FactorBuildReport,
    FactorPanelUnreadableError,
    FactorRequestError,
    build_factor_panels,
    build_view,
    factor_build_request,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    FACTOR_DEFINITIONS,
    ExcludedReportPeriod,
    UnknowableReturnSession,
    load_factor_manifests,
    load_factor_observations,
    load_factor_transform_manifests,
)
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
    split_panel_batch_by_year,
    write_panel_batch,
    write_upstream_defects,
)
from openalpha_cn.panel_neutralization import load_factor_neutralization_manifests
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


def _statement_batch(
    dataset: str, *, stubs: Sequence[tuple[str, date, date]] = ()
) -> ColumnarPanelBatch:
    """Every stored column of one endpoint; positive and growing, so every ratio is defined.

    `stubs` adds `(security, period, announced)` rows beside the quarterly ones, each carrying
    every column at a value no quarterly row has -- the stub filings `V2-P6-019` excludes.
    """
    rows: list[tuple[str, datetime, datetime, datetime]] = []
    cells: dict[str, list[object]] = {name: [] for name in statement_panel_columns(dataset)}
    for code, period, announced in stubs:
        stamp = _midnight(announced)
        rows.append((code, stamp, stamp, stamp))
        cells[REPORT_PERIOD_COLUMN].append(period.isoformat())
        cells[ANNOUNCEMENT_DATE_COLUMN].append(announced.isoformat())
        if FIRST_ANNOUNCEMENT_COLUMN in cells:
            cells[FIRST_ANNOUNCEMENT_COLUMN].append(announced.isoformat())
            cells[REVISION_LABEL_COLUMN].append("0")
        for name in STATEMENT_DATA_COLUMNS[dataset]:
            cells[name].append(98765.0)
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
        for dataset, year, content_hash, _coverage in store.partition_stamps()
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


STORED_BY_SINGLE_FACTOR_BUILDS_AT_8A18532: Final[dict[str, str]] = {
    "factor_manifest_accruals_ttm_v1@2026": (
        "b758c03ccc137945be56e50da33e0fd20a282f2335c1f66a769d9619d538cd3b"
    ),
    "factor_manifest_amihud_60_v1@2026": (
        "22eecc0505fdeb3cddeb5cd1b1e978683886b0509f05d6962b90aed24df91df7"
    ),
    "factor_manifest_book_to_price_v1@2026": (
        "472a2aa0021f31e68fd471c1d99722ffaa6bef3508caf141a83fb640229efe95"
    ),
    "factor_manifest_deducted_earnings_yield_ttm_v1@2026": (
        "faa6b2f880ed91fa6c0d2af6467c44476e462c9b46d3299efc4db13be531b9c6"
    ),
    "factor_manifest_downside_vol_60_v1@2026": (
        "417f25bf2d1568fe8a8ef563b2be4f4e495c54b4a4fa05c3d930cbb03d094363"
    ),
    "factor_manifest_earnings_yield_ttm_v1@2026": (
        "ee7c25f5bfce3a1dea7f2d1498a98c8aa537996b81ebe99d2ba81561ece70858"
    ),
    "factor_manifest_gross_margin_stability_v1@2026": (
        "52822d4546820f0581b570c6a853816eda6fef7ae4979ad92f8eb2756dc1851a"
    ),
    "factor_manifest_momentum_120_sessions_v1@2026": (
        "45595c236fed7b5c2ba72ec6d303e943c156f287be1e641dc28b335483a90e70"
    ),
    "factor_manifest_momentum_20_sessions_v1@2026": (
        "a4f3b7cbc3662b42c9c9c6c53a33e523b8f5bb4f7bf158e9cd82142d77d7e249"
    ),
    "factor_manifest_momentum_60_sessions_v1@2026": (
        "0fb8d1708cd2dcb80c1350005c3c639040193e5ecf33b3e1a7fc4085b90a4eb5"
    ),
    "factor_manifest_net_profit_yoy_v1@2026": (
        "6a80a1ca208371afba276e1bcb45814509d194f24afc181e97145170d060aed0"
    ),
    "factor_manifest_residual_vol_60_v1@2026": (
        "de78496523371c947efa2510af25f7a923611fea762ff5d9918e264422fff8fa"
    ),
    "factor_manifest_return_on_capital_ttm_v1@2026": (
        "0699ffafd1f787b28d4f8781aeccb6c8ec27025cd2ff9057f4f0ec242ad5d7e9"
    ),
    "factor_manifest_return_on_equity_ttm_v1@2026": (
        "01cf7a43e56dd40aa6533e842da69a51cd9df195867ad719054e160a0899606a"
    ),
    "factor_manifest_return_vol_60_v1@2026": (
        "b6a718289f80cf44050872855c1196fa3d856f8b387256af75eae9a043e48f6b"
    ),
    "factor_manifest_revenue_yoy_acceleration_v1@2026": (
        "db03dbc8bc2ab2526e4e6a84850a3fc9f1fd2423da40fccbd5374f01eb3c930c"
    ),
    "factor_manifest_revenue_yoy_v1@2026": (
        "8e7666af5312868ebd29ef9f62707441c793545c1eac3609ff93c01687eec320"
    ),
    "factor_manifest_reversal_1d_v1@2026": (
        "3eac72afc27057e9167b8b536ad41d7cb6068684726c5a64eb306e431596cfe3"
    ),
    "factor_manifest_reversal_5_sessions_v1@2026": (
        "0caa24e880ada5b9538e50b4b37bc2ecc09127cc6d3380ae8a514485162cfdf3"
    ),
    "factor_manifest_sales_yield_ttm_v1@2026": (
        "b2a6d7824f7e699a4015a4c0d6d2d607d45923fd49376cc2a5c50f12711a7a14"
    ),
    "factor_manifest_turnover_60_v1@2026": (
        "794494fb60087f07e1fffc769038733a56e4def436794ea8e875146c924f7e8b"
    ),
    "factor_neut_accruals_ttm_v1@2026": (
        "b96ba8104a709c2d7e0a430a340ffe2c93a0734b32769af015d1cb08bb15ea7f"
    ),
    "factor_neut_amihud_60_v1@2026": (
        "21eb305ffe77d8a3f31fd972d842184a845a2e1af6a26bc38f4dfd93a1935176"
    ),
    "factor_neut_book_to_price_v1@2026": (
        "f206c572c5faa72db51c4eda8170c2cacd6915eda650fed349ab64f1078c1e5f"
    ),
    "factor_neut_deducted_earnings_yield_ttm_v1@2026": (
        "0c556ae6efa6c482d4276f8cef0f1fc938cdffcf5edcf058b91d3d2a27d3a39d"
    ),
    "factor_neut_downside_vol_60_v1@2026": (
        "8968e636d05937f78f5d407b35cdac7f3f322014118e9ce4a0e79f7e667b7c11"
    ),
    "factor_neut_earnings_yield_ttm_v1@2026": (
        "c89b0b9858de4f0b8801669b9c589449f2b1d5b24be003555433b81fec5c2f1e"
    ),
    "factor_neut_gross_margin_stability_v1@2026": (
        "58d72f79f03d9c2816d9e7e63540718c940a5d7a8f8158ef8d2b9603f4812ae2"
    ),
    "factor_neut_momentum_120_sessions_v1@2026": (
        "e46eb468ed4a32e62ec679e53c744a8cf0b2a75a82ccfb592287e80aaaf6ea71"
    ),
    "factor_neut_momentum_20_sessions_v1@2026": (
        "98ea5c5815d756e2f661ab7d4066acd90a70d1a4783520347a001d4029ba9305"
    ),
    "factor_neut_momentum_60_sessions_v1@2026": (
        "39cea2ceeea783eb830753ece291f0da0d175e1f110dab2171e1bc0f6165c1ac"
    ),
    "factor_neut_net_profit_yoy_v1@2026": (
        "7f2ea3543fba4a57c87c5f52c537ecc4f0ee5561dc1526a13decf9404dd3f690"
    ),
    "factor_neut_residual_vol_60_v1@2026": (
        "921e835f47ea7901cf3c8a35ea981d93e55af1959229593a84c8bb1d7b29f681"
    ),
    "factor_neut_return_on_capital_ttm_v1@2026": (
        "8296f88dc3901789fc1e828dfc9974eb762e7681274558f83cc3df3de31e7776"
    ),
    "factor_neut_return_on_equity_ttm_v1@2026": (
        "d197f1ea463a079616b5b23ccf0311be52b7fb45694d021accbb91303852a5a6"
    ),
    "factor_neut_return_vol_60_v1@2026": (
        "1134ecd087d8f0a6dff2b841e0df94755ea7e51e4aa1509f685eb5490d47987b"
    ),
    "factor_neut_revenue_yoy_acceleration_v1@2026": (
        "cba8dde57dffb6d0894b19611a0cad23a5ef1939c28196328ec45073099a2e76"
    ),
    "factor_neut_revenue_yoy_v1@2026": (
        "4f69ebe80dc6605af049589f7a0358ec08de94e5d21396ea56edcd173f7885f0"
    ),
    "factor_neut_reversal_1d_v1@2026": (
        "90b67d84bb66d711ceb5beb949b263a2b72e6e118e240e2e9af913ccc2dcc31e"
    ),
    "factor_neut_reversal_5_sessions_v1@2026": (
        "4a560e85b267c98d0080f3cb32e23300659af43351034380cc781a21765d09f4"
    ),
    "factor_neut_sales_yield_ttm_v1@2026": (
        "3b87eedfe08a8960cca08ad0e3cd3d536248819a49aa6fc29d77b95f9319c810"
    ),
    "factor_neut_turnover_60_v1@2026": (
        "a2b1e663e3943019bcb6074ef8479ce5da8d3b310bffbdbfd800e755d202f4db"
    ),
    "factor_neutmn_accruals_ttm_v1@2026": (
        "7a3f826b5f7a9ee3a82cc0b6b7899d85f44e339ce9d636efef1b424f33c50206"
    ),
    "factor_neutmn_amihud_60_v1@2026": (
        "f7dabcabec8355ec0c37860acca102b6607a5b93c7f83870a3078cdfede5fa04"
    ),
    "factor_neutmn_book_to_price_v1@2026": (
        "cec3c7d2ff5428c61e9ef1e570f9b8b958d90e93aa3f1251f2daa320af0b0ccb"
    ),
    "factor_neutmn_deducted_earnings_yield_ttm_v1@2026": (
        "fe876af4f736e5e7066d2647fc3c3926964c0291bc747bcb90dca93898db2e54"
    ),
    "factor_neutmn_downside_vol_60_v1@2026": (
        "d388acf1a34e517b8eadf71b17d7e08f49b93568593d78f2b52df8a5ece5e3ac"
    ),
    "factor_neutmn_earnings_yield_ttm_v1@2026": (
        "985816bb18079a5590e8117ecb05d4790b6a15031b44be5e530ec949ddfd4b18"
    ),
    "factor_neutmn_gross_margin_stability_v1@2026": (
        "126fcfa55ecd8c5a9c47a7660f38bd5e02c2586eead1c60745349e01b4be8c1f"
    ),
    "factor_neutmn_momentum_120_sessions_v1@2026": (
        "410b6b1c33031159778c22bdc3080b41c186a4d11ba5b47f41f107dd773f54ca"
    ),
    "factor_neutmn_momentum_20_sessions_v1@2026": (
        "b27c3e70b6dd900dceef223e5a29611223c26d806d42403105dba43cd576470b"
    ),
    "factor_neutmn_momentum_60_sessions_v1@2026": (
        "e99a0cb5ade6be8773553e5f7a4d8359fe6b087c0fd04af666086c57f9ad64b3"
    ),
    "factor_neutmn_net_profit_yoy_v1@2026": (
        "f92d49578597e1b80e60714553a6a1edc0d39b6bc332bb1e7fcc6e5f515c4f9c"
    ),
    "factor_neutmn_residual_vol_60_v1@2026": (
        "f03a2b79680502713d88651304e7f8f8a6b670f2436d0cd9edf0c598c98802aa"
    ),
    "factor_neutmn_return_on_capital_ttm_v1@2026": (
        "9ee814b0114a98a33933be82369061d1e01bab73b93f2d57309aa0b146fae370"
    ),
    "factor_neutmn_return_on_equity_ttm_v1@2026": (
        "84039ea9dfdefd93554e660a1ec20485082fd5aa5fda9d1dc6b40517aca74882"
    ),
    "factor_neutmn_return_vol_60_v1@2026": (
        "c6e1f2b2a0f725c36bba272d725813442db465fbe4c2a09d430a47cff106969a"
    ),
    "factor_neutmn_revenue_yoy_acceleration_v1@2026": (
        "1d655797ce11218317d23ce241c80e781a478b9042b14d6c6a3ce989f7a6acdc"
    ),
    "factor_neutmn_revenue_yoy_v1@2026": (
        "5754290e8f8193e92fe1ab2635d086e855193f74d4e78f0e86ce39163cb96ac2"
    ),
    "factor_neutmn_reversal_1d_v1@2026": (
        "dffc37f6804167d5bb18dd7e9b94ad65fdbf5b04a3b76824923edf415f3d958c"
    ),
    "factor_neutmn_reversal_5_sessions_v1@2026": (
        "449bbae274f5fd753bf5b15d044bbd0dc8aab61e3089dc56c77df66d622e3906"
    ),
    "factor_neutmn_sales_yield_ttm_v1@2026": (
        "c3e8e6c906ea7e2430954ed494ae1f1d7e705476dde22f5ddc66fba3e75c21ee"
    ),
    "factor_neutmn_turnover_60_v1@2026": (
        "06f73dcda1d4da97fe868a45f05b8fec3c72f76a4b7e68e4619e17be403198de"
    ),
    "factor_obs_accruals_ttm_v1@2026": (
        "508077a2a87905dbdf24ad47fdcf54c1fd03a78099afab652d2f6a1594baa900"
    ),
    "factor_obs_amihud_60_v1@2026": (
        "c9406d3233abde424d5164be03bac6f55d3c40dfa496f088dcecbfa4a93005fb"
    ),
    "factor_obs_book_to_price_v1@2026": (
        "bcf0793e84638a36b1a25032eabc10eeb58228d9782b5e9127c14bcbfc1d940e"
    ),
    "factor_obs_deducted_earnings_yield_ttm_v1@2026": (
        "85041d3c68580cc79bf4820c7027b28b2e09e7f330baa91006ee5d856690c089"
    ),
    "factor_obs_downside_vol_60_v1@2026": (
        "78363d44d99c0bff5970a1297d270a6bda3c9e019bdd4c4928f82b8bc5c17252"
    ),
    "factor_obs_earnings_yield_ttm_v1@2026": (
        "b3bbb97605fef08e87e63c60a4bf86a67bdd8f861bb70cf6e36d9ef1462d0cae"
    ),
    "factor_obs_gross_margin_stability_v1@2026": (
        "d6e43db03a1bc1ea623a22ddd4a22b8e8f46a5da646452aed68d5192d2a70ce4"
    ),
    "factor_obs_momentum_120_sessions_v1@2026": (
        "cbaf472d443b40657abba3b051c3af624bdb1d679dbaeb3b68629adec9933efc"
    ),
    "factor_obs_momentum_20_sessions_v1@2026": (
        "66b12bbdc0a97aef30ca882efcd9e696981c4ff2a68f0c0374d56bdb67be3b8a"
    ),
    "factor_obs_momentum_60_sessions_v1@2026": (
        "883639ead3215082121b8a8b42ef3282c39f0cde715b9e15d13bd2658214d2e8"
    ),
    "factor_obs_net_profit_yoy_v1@2026": (
        "6dc290d4c765407b74e26a9de91eee108f663a1a5b122eb2934730bcf789c8cb"
    ),
    "factor_obs_residual_vol_60_v1@2026": (
        "6c9ed0b439ff7100dff39084b52bc213e5747d82e6a683aa86228ed1cb100baf"
    ),
    "factor_obs_return_on_capital_ttm_v1@2026": (
        "06cedff61c9c5874dfabccbdc444c12cbbe6ff252d84f7cd010df195a04b155d"
    ),
    "factor_obs_return_on_equity_ttm_v1@2026": (
        "353bbf3e9db88dad46d69eb84cb12acbc80e2b0ae3a03172d95497e0e789b206"
    ),
    "factor_obs_return_vol_60_v1@2026": (
        "1d5e29eccf99cef262f9bc87dcb695b8bead952ae16a97b57760d4ca3319802e"
    ),
    "factor_obs_revenue_yoy_acceleration_v1@2026": (
        "6339aa3cb2ef37a6c9d640016e981167f49daff76dbd9ce9f2e6f675156485f8"
    ),
    "factor_obs_revenue_yoy_v1@2026": (
        "0a52418db7ed9d46a1d70a342540b9df7d6f89c20c325ed2d42b41daf19c6911"
    ),
    "factor_obs_reversal_1d_v1@2026": (
        "ecd5dd42c03b80cbddf2699cc4b2debf5db81f3a16b283bfff97e5ed5f6e468e"
    ),
    "factor_obs_reversal_5_sessions_v1@2026": (
        "f4d61ebcf1418adb3b6a77d96791c0db193b70e42c5e1981e9a5cbf210f18070"
    ),
    "factor_obs_sales_yield_ttm_v1@2026": (
        "e3f54b421b4d1d1ebfcaaf24a224a91d3567eb75f5babee16994dd25b30279f4"
    ),
    "factor_obs_turnover_60_v1@2026": (
        "3a57d5314c9534f2fb038d5ca4f378ebc34c4caa674f40163dccf2e14b819708"
    ),
    "factor_proc_accruals_ttm_v1@2026": (
        "d4152bbe515862f5c837e1917e14dd7eca55f7f9022bcb228793855d2b7172a3"
    ),
    "factor_proc_amihud_60_v1@2026": (
        "a019568cd1f1b516f0015e612c6ad13ed0d2bee955180756043dace57384a129"
    ),
    "factor_proc_book_to_price_v1@2026": (
        "e6d8f812e01f98697c0a3867f9513f581582386352df2ab2eb3f7d720ce04204"
    ),
    "factor_proc_deducted_earnings_yield_ttm_v1@2026": (
        "f3e413a85c25add9985b75b5c4eb6b1e764fed1dd69cfcd5bf0422f6d054207b"
    ),
    "factor_proc_downside_vol_60_v1@2026": (
        "3e5db1dfd65020b5ff3c01b5a3a4803f8f269a45cd21a712516fe1983afb8ce7"
    ),
    "factor_proc_earnings_yield_ttm_v1@2026": (
        "618854e1c655fc85624c9c08b0d556ea83648273e0b3b93d4993f12b1d3765e4"
    ),
    "factor_proc_gross_margin_stability_v1@2026": (
        "5fc50383549c18e57b46fe05fe90f2131352f785e45db2132280fa7bc8fb892d"
    ),
    "factor_proc_momentum_120_sessions_v1@2026": (
        "e03d4b611c773af7b02a898767498be0f7ce584b3c0a23d1386aa9ebc5775cf9"
    ),
    "factor_proc_momentum_20_sessions_v1@2026": (
        "03c6706ba51f45636133acae7c6e627d4fd115f17493705c2c11a28a391fb5f5"
    ),
    "factor_proc_momentum_60_sessions_v1@2026": (
        "231490cd59a79e5d9b1a27c47f5eef9f1a0fd1da2178574d0cc6d2bc6c9f6a33"
    ),
    "factor_proc_net_profit_yoy_v1@2026": (
        "eb92bba9314a00964ec88f015e8f42b45c6404677dac0f5c0ebfbe603e3fcb2f"
    ),
    "factor_proc_residual_vol_60_v1@2026": (
        "c98e909d83f1a49c2ae05d52173ec48e5a25a17dc7d3dfbf1328480c0095321c"
    ),
    "factor_proc_return_on_capital_ttm_v1@2026": (
        "02c85a1e5f8a4eaf98b6d7e36e67a604d27452070631f2ceca64ed688669d780"
    ),
    "factor_proc_return_on_equity_ttm_v1@2026": (
        "b7fd9fb60ba04f6b5af28cb718a25c0acdc881d33222914ff15fc97b162d7e0a"
    ),
    "factor_proc_return_vol_60_v1@2026": (
        "a97320567217bb55331452edf99abfbaf67f69555c94c94973f82469ff030f71"
    ),
    "factor_proc_revenue_yoy_acceleration_v1@2026": (
        "bb13a99b88e39bedb1f59f1dc82172f7777a01010f2e01f0866b93caffdbea5a"
    ),
    "factor_proc_revenue_yoy_v1@2026": (
        "3c36e68af074e2b4b87208b6597a2e8937de98e51e46964ab7ed09e470372d01"
    ),
    "factor_proc_reversal_1d_v1@2026": (
        "308e37b509035c5563fe8e0bc3c311b97616f74449c8aa1790285530c99c3689"
    ),
    "factor_proc_reversal_5_sessions_v1@2026": (
        "da6c0164717353672a65d15d6213b1da1231711fe838a5cc0041ca3d20557cc4"
    ),
    "factor_proc_sales_yield_ttm_v1@2026": (
        "d0b5ff8ea8f52f7c1c741f8e209929c3f43d0b31bc8967f5f3463ba8c9f3725d"
    ),
    "factor_proc_turnover_60_v1@2026": (
        "d0fa48188413ec5428f59dc64d14104917b934af1a713f155d1a136a12441be6"
    ),
    "factor_procmn_accruals_ttm_v1@2026": (
        "350b75aa592808a523ecfadae2fb8f2473cb3e7204a45c68d51c60889025f1d5"
    ),
    "factor_procmn_amihud_60_v1@2026": (
        "4d417d9e9333a550e2531f37467aed1d993acf322a67d912d63a5137655f3cbd"
    ),
    "factor_procmn_book_to_price_v1@2026": (
        "3eb18985cc589b5371359113590866c44700cd1c4c2211b8166a642753aa1f05"
    ),
    "factor_procmn_deducted_earnings_yield_ttm_v1@2026": (
        "ac549c54a57c5329cabfb9cc09b4acfb231977ec4fb47afa2aecd083a0dc5b06"
    ),
    "factor_procmn_downside_vol_60_v1@2026": (
        "32a5af921853c4246a3107a44ea69deb9d38206f32045f09aa0f784859ef33fa"
    ),
    "factor_procmn_earnings_yield_ttm_v1@2026": (
        "665967a90f75ef8e5a7a9a9f249e99991d1679045cbd730a6f8a84773e40afde"
    ),
    "factor_procmn_gross_margin_stability_v1@2026": (
        "923cf2ecd2a28c5dd0bc49b3bcfed711ffdfbd34611d725669a40c4026c16d53"
    ),
    "factor_procmn_momentum_120_sessions_v1@2026": (
        "991a4e8949ed6a9823c55a94f61c65597b4411bfc8bfbb474cd49e6abf138602"
    ),
    "factor_procmn_momentum_20_sessions_v1@2026": (
        "492e7d950ac2aa73010be107ca095968b1715ccaf044beda54f1ee1b4564a491"
    ),
    "factor_procmn_momentum_60_sessions_v1@2026": (
        "28be53c78f5e04ecf282286dbbc31006e84cf8046eb6790e91e529a25c2c551a"
    ),
    "factor_procmn_net_profit_yoy_v1@2026": (
        "f3d023bcb4059da60d4fc3fea4e2d41f9e76f5aa886353536a1b58a5ca34df4c"
    ),
    "factor_procmn_residual_vol_60_v1@2026": (
        "f99b32fb9b7e4e2bbcd175d1a1c61e90cf7835233bac30e7086597c29915dff5"
    ),
    "factor_procmn_return_on_capital_ttm_v1@2026": (
        "20ba6442f59297eda34ed8c95418082ac439f11c16e531290e69bfe07cba13e2"
    ),
    "factor_procmn_return_on_equity_ttm_v1@2026": (
        "e63d5dae5dfb640a5d4dba7fcb084ffb21babb97f2596c766032e2f46d3d1aa0"
    ),
    "factor_procmn_return_vol_60_v1@2026": (
        "860d3a4c6e3182fe3b2974fe1213cd426ff5a5abeb504398a6a8b3ba4ab906fc"
    ),
    "factor_procmn_revenue_yoy_acceleration_v1@2026": (
        "653160e5be1cadde8aa259741cd3474d878df68b914df19a1194c03546330d5e"
    ),
    "factor_procmn_revenue_yoy_v1@2026": (
        "a81bcfed9241f2932185744c720eb58eb1fce2086e59f807236b190ea16f6cab"
    ),
    "factor_procmn_reversal_1d_v1@2026": (
        "ebdd7a5d25d7e5e230cd71f860df87d5eecb57f60856a466e78909de450891d2"
    ),
    "factor_procmn_reversal_5_sessions_v1@2026": (
        "f3f1df331591debcc813384c0d59594e8632fc5ee6e84909f0111de042de4e0b"
    ),
    "factor_procmn_sales_yield_ttm_v1@2026": (
        "57bf2b2b1e5c606a4c01edb71129dff06cdb1550ab683ca1f6f225b0a774db07"
    ),
    "factor_procmn_turnover_60_v1@2026": (
        "d6f34fc19454beab055a4d6113f40bb1bb53786fa7d81a46be52ec007e9025b9"
    ),
}
"""Every partition the 21 single-factor builds of this file's corpus stored **before**
`V2-P6-006`: `build_factor_panels` at `8a18532`, one fresh corpus copy per factor, `_parameters()`
and `BUILT_AT`, read off the catalog as `dataset@year -> content_hash`.

Pinned rather than recomputed, for `test_factor_read_path_equivalence.py`'s reason: a reference
computed by the code under test is that code agreeing with itself, and since `V2-P6-006` the
single-factor build runs through the same `FactorBuildContext` the shared one does. These are what
the build wrote before the context existed. Reproduced twice, independently (the review's probe and
the implementer's run of it on an extracted `8a18532` tree), 126 of 126 equal. A change to the
corpus generator above moves them and must re-pin them from `8a18532` the same way.
"""


def test_every_partition_a_shared_build_writes_is_the_one_its_single_factor_build_writes(
    corpus: Path, tmp_path: Path
) -> None:
    """All 21 factors x 3 tiers x 3 instants, by `content_hash`, against 21 separate builds.

    Held to the partitions the single-factor build stored before this change
    (`STORED_BY_SINGLE_FACTOR_BUILDS_AT_8A18532`), and to today's single-factor builds, each in a
    fresh copy of the corpus -- the arrangement the shared build replaces; the shared one runs all
    21 in one call over one copy. Every report is equal too, so the universe sizes and coverage
    censuses the faces print did not move either.
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

    assert len(STORED_BY_SINGLE_FACTOR_BUILDS_AT_8A18532) == 6 * len(keys)
    assert together == STORED_BY_SINGLE_FACTOR_BUILDS_AT_8A18532
    assert alone == STORED_BY_SINGLE_FACTOR_BUILDS_AT_8A18532
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


@pytest.mark.parametrize("change", ["header", "census"])
def test_a_coverage_re_profiled_under_a_clock_that_does_not_move_is_still_noticed(
    scratch: PanelStore, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A different coverage record under the same `recorded_at` is a different catalog state.

    The review's probe: a store whose injected clock does not advance re-records a registry
    year's coverage, and the record's `recorded_at` stays exactly what it was. Two changes, one
    per half of the stamp: another `batch_digest` in the coverage row itself (the provenance a
    manifest cites), and one more code in its subject census with the row untouched. Stamped by
    `recorded_at` alone, the second factor was served the first factor's answers; stamped by the
    whole record and its census, it reloads.
    """
    held = scratch.read_coverage(STOCK_BASIC_DATASET, REWRITTEN_YEAR)
    assert held is not None
    frozen = PanelStore(scratch.root, clock=lambda: held.recorded_at)
    changed = (
        replace(held, batch_digest="sha256:" + "0" * 64)
        if change == "header"
        else replace(held, subjects=tuple(sorted((*held.subjects, ADDED_SECURITY))))
    )

    def re_profile() -> None:
        frozen.record_coverage(changed)

    counts = _counting(monkeypatch, *SHARED_LOADERS)
    _after_the_first_factor_writes(monkeypatch, re_profile)
    _shared(scratch, THREE[:2])

    after = scratch.read_coverage(STOCK_BASIC_DATASET, REWRITTEN_YEAR)
    assert after is not None
    assert after.recorded_at == held.recorded_at
    assert after != held
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


# --- V2-P6-019: a stub filing off the quarter grid, at the build's faces ------------------------

STUB_SECURITY: Final[str] = SECURITIES[4]
STUB_PERIOD: Final[date] = date(2025, 11, 30)
"""Between 2025-09-30 and 2025-12-31, the shape of `920185.BJ`'s stored `2014-05-31`.

Announced on 2025-12-10, after that security's 2025-09-30 filing and before its 2025 annual
(which `_announced` puts in March 2026), so at `INSTANTS[0]` a reader that rounded it down would
restate the September quarter and one that rounded it up would file a December quarter nobody
had filed yet -- either way the TTM numerator moves.
"""

STUB_LISTED: Final[str] = (
    f"excluded   1 statement row(s) off the fiscal quarter grid: "
    f"income {STUB_SECURITY} {STUB_PERIOD.isoformat()}"
)
"""The line the terminal face prints for the stub, with the dataset, the security and the period."""


def _with_a_stub(store: PanelStore) -> None:
    """Rewrite the stub's announcement year of `income` with one stub row beside its quarters."""
    batch = _statement_batch(
        INCOME_DATASET, stubs=((STUB_SECURITY, STUB_PERIOD, date(2025, 12, 10)),)
    )
    _write(store, batch, only=2025)


def _raw_at_january(**overrides: Any) -> dict[str, Any]:
    return _parameters(
        tier="raw", transform="", neutralization="", as_ofs=(INSTANTS[0],), **overrides
    )


def _stored_answers(
    store: PanelStore, factor: str, *, as_of: datetime = INSTANTS[0]
) -> dict[str, tuple[str, float | None]]:
    observations = load_factor_observations(
        store, FACTOR_DEFINITIONS.get(factor), years=(2026,), as_of=as_of
    )
    return {
        item.subject: (item.coverage, item.value) for item in observations if item.as_of == as_of
    }


def test_a_stub_filing_off_the_quarter_grid_builds_and_every_build_face_lists_it(
    corpus: Path, tmp_path: Path
) -> None:
    """`V2-P6-019` at the faces: the build that refused at `earnings_yield_ttm/v1` now stores, the
    answers are the ones the store without the stub stores, and the report, the `--json` body and
    the terminal line all name the excluded row.

    The engine-level measurement -- rounding in either direction moves the answer, a carried read
    lists what a fresh one lists, a security with only a stub is a security with no filing -- is
    `tests/integration/panel/test_factor_report_periods.py`'s. This is the part only a build can
    show: the exclusion reaches the report a caller reads, and it is reported rather than absorbed.
    A factor that reads no statement lists nothing, which is the zero the terminal line prints.
    """
    factor = "earnings_yield_ttm/v1"
    clean = _copy(corpus, tmp_path / "clean")
    stubbed = _copy(corpus, tmp_path / "stubbed")
    _with_a_stub(stubbed)

    _single(clean, factor, **_raw_at_january())
    reports = _shared(stubbed, (factor, "reversal_1d/v1"), **_raw_at_january())

    listed = (
        ExcludedReportPeriod(
            dataset=INCOME_DATASET, subject=STUB_SECURITY, report_period=STUB_PERIOD
        ),
    )
    assert reports[0].excluded_report_periods == listed
    assert reports[1].excluded_report_periods == ()
    assert _stored_answers(stubbed, factor) == _stored_answers(clean, factor)
    assert _stored_answers(stubbed, factor)[STUB_SECURITY][0] == "computed"
    assert build_view(reports[0])["excluded_report_periods"] == [
        {
            "dataset": INCOME_DATASET,
            "subject": STUB_SECURITY,
            "report_period": STUB_PERIOD.isoformat(),
        }
    ]
    assert build_view(reports[1])["excluded_report_periods"] == []

    runtime = tmp_path / "cli"
    _with_a_stub(_copy(corpus, runtime / "panel"))
    arguments = ["factor", "build", "--runtime-dir", str(runtime), "--factor", factor]
    arguments.extend(["--factor", "reversal_1d/v1", "--tier", "raw"])
    arguments.extend(["--as-of", INSTANTS[0].isoformat(), "--year", "2025", "--year", "2026"])
    arguments.extend(["--exchange", EXCHANGE, "--max-staleness-days", str(STALENESS_DAYS)])
    arguments.extend(["--code-commit", COMMIT])

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert [line for line in lines if line.startswith("excluded ")] == [
        f"{STUB_LISTED} "
        "(KNOWN_FACTOR_RUN_LIMITATIONS."
        "a_report_period_off_the_quarter_grid_is_excluded_and_listed_rather_than_rounded)",
        "excluded   0 statement row(s) off the fiscal quarter grid",
    ]


# --- V2-P6-020: a return factor abstains on an unknowable session, and every face counts it ----

UNKNOWABLE_INDEX: Final[int] = 6
UNKNOWABLE_SECURITY: Final[str] = SECURITIES[UNKNOWABLE_INDEX]


def _with_an_unknowable_session(store: PanelStore) -> date:
    """Record `UNKNOWABLE_SECURITY`'s newest session at `INSTANTS[0]` as one whose return no
    witness decides, judged on the two stored closes, as the `stk_limit` target writes one."""
    (session,) = _with_decisions(store, (UNKNOWABLE_INDEX, "pre_close_contradicts_adj_factor"))
    return session


def _with_decisions(
    store: PanelStore, *decisions: tuple[int, str] | tuple[int, str, datetime]
) -> tuple[date, ...]:
    """Record each `(security index, kind[, instant])` about that security's newest session at
    the instant (`INSTANTS[0]` by default), judged on the two stored closes with an implied
    `pre_close` half the published one, replacing every `stk_limit` record of the year -- as a
    re-judgement does."""
    rows: list[tuple[str, date, dict[str, object]]] = []
    for index, kind, *at in decisions:
        security = SECURITIES[index]
        path = _path(index, security)
        instant = at[0] if at else INSTANTS[0]
        held = [day for day in path if day <= instant.astimezone(SHANGHAI).date()]
        session, previous = held[-1], held[-2]
        close, pre_close = path[session]
        rows.append(
            (
                security,
                session,
                {
                    "trade_date": session.isoformat(),
                    "source_dataset": "stk_limit",
                    "defect_kind": kind,
                    "bar_close": close,
                    "valuation_close": pre_close * 0.5,
                    "previous_bar_close": path[previous][0],
                    "up_limit": None,
                    "down_limit": None,
                    "valuation_repeats_previous_close": None,
                    "list_date": None,
                },
            )
        )
    kinds = {"valuation_repeats_previous_close": "boolean"}
    (year,) = {session.year for _security, session, _values in rows}
    write_upstream_defects(
        store,
        ColumnarPanelBatch(
            provider_id="openalpha-cn/tests",
            dataset=UPSTREAM_DEFECTS_DATASET,
            kind=UPSTREAM_DEFECTS_DATASET,
            as_of=FETCHED_AT,
            fetched_at=FETCHED_AT,
            status="success",
            subjects=tuple(security for security, _session, _values in rows),
            timeline=TimelineColumns(
                event_time=tuple(_at(session, SESSION_CLOSE_TIME) for _s, session, _v in rows),
                available_time=tuple(
                    _at(session, DAILY_AVAILABILITY_TIME) for _s, session, _v in rows
                ),
                ingested_time=tuple(
                    _at(session, DAILY_AVAILABILITY_TIME) for _s, session, _v in rows
                ),
                revision_time=tuple(
                    _at(session, DAILY_AVAILABILITY_TIME) for _s, session, _v in rows
                ),
            ),
            columns=tuple(
                PanelColumn(
                    name,
                    kinds.get(name, "string" if isinstance(rows[0][2][name], str) else "float"),
                    tuple(values[name] for _security, _session, values in rows),
                )
                for name in UPSTREAM_DEFECT_DATA_COLUMNS
            ),
        ),
        year=year,
        source_datasets=frozenset({"stk_limit"}),
    )
    return tuple(session for _security, session, _values in rows)


def test_an_unknowable_session_a_return_factor_crosses_is_counted_on_every_build_face(
    corpus: Path, tmp_path: Path
) -> None:
    """Review round 1 of `V2-P6-020`: the factor engine follows the record the labels and the book
    follow. `reversal_1d` abstains on the one security whose newest link is unknowable -- an
    `undefined_value` rather than the naive return across it -- and the report, the `--json` body
    and the terminal line say so; a factor that reads no session return lists nothing."""
    factor = "earnings_yield_ttm/v1"
    decided = _copy(corpus, tmp_path / "decided")
    session = _with_an_unknowable_session(decided)

    reports = _shared(decided, (factor, "reversal_1d/v1"), **_raw_at_january())

    crossed = (UnknowableReturnSession(subject=UNKNOWABLE_SECURITY, session=session),)
    assert reports[0].unknowable_return_sessions == ()
    assert reports[1].unknowable_return_sessions == crossed
    assert _stored_answers(decided, "reversal_1d/v1")[UNKNOWABLE_SECURITY] == (
        "undefined_value",
        None,
    )
    assert build_view(reports[1])["unknowable_return_sessions"] == [
        {"subject": UNKNOWABLE_SECURITY, "session": session.isoformat()}
    ]
    assert build_view(reports[0])["unknowable_return_sessions"] == []

    runtime = tmp_path / "cli"
    _with_an_unknowable_session(_copy(corpus, runtime / "panel"))
    arguments = ["factor", "build", "--runtime-dir", str(runtime), "--factor", "reversal_1d/v1"]
    arguments.extend(["--tier", "raw", "--as-of", INSTANTS[0].isoformat()])
    arguments.extend(["--year", "2025", "--year", "2026", "--exchange", EXCHANGE])
    arguments.extend(["--max-staleness-days", str(STALENESS_DAYS), "--code-commit", COMMIT])

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 0, result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("unknowable ")] == [
        f"unknowable 1 security-session(s) abstained on, whose return upstream_defects records as "
        f"unknowable: {UNKNOWABLE_SECURITY} {session.isoformat()}"
    ]


# --- review round 2, N1: the stored builds a decision made stale, found and repaired --------------


def _detect(store: PanelStore) -> tuple[tuple[factor_view.StaleReturnPathBuild, ...], list[int]]:
    calls: list[int] = []
    found = factor_view.stale_return_path_builds(
        store,
        exchange=EXCHANGE,
        max_staleness_days=STALENESS_DAYS,
        as_of=FETCHED_AT,
        code_commit=COMMIT,
        budget=calls.append,
    )
    return found, calls


def _detector_arguments(runtime: Path) -> list[str]:
    arguments = ["factor", "stale-return-paths", "--runtime-dir", str(runtime)]
    arguments += ["--exchange", EXCHANGE, "--max-staleness-days", str(STALENESS_DAYS)]
    return [*arguments, "--code-commit", COMMIT, "--as-of", FETCHED_AT.isoformat()]


def _raw_only(store: PanelStore, factor: str, **overrides: Any) -> FactorBuildReport:
    return _single(store, factor, tier="raw", transform="", neutralization="", **overrides)


def test_the_detector_lists_every_build_made_before_a_decision_and_nothing_after_its_repair(
    corpus: Path, tmp_path: Path
) -> None:
    """A store whose factor builds were made before any return-path decision existed holds
    values on the published path. A decision is not a manifest input, so nothing else notices.
    `factor stale-return-paths` asks the engine again about every stored observation whose own
    window reads a decision that can move it, lists each build whose stored answer the engine no
    longer gives -- raw, and the processed and neutralized builds made from it at that instant --
    with the `factor build ... --supersedes-*` commands that repair them, and exits 1. Running
    the printed commands leaves nothing to list, and the command exits 0.

    Three decisions, about three securities' newest session at `INSTANTS[0]`: unknowable,
    published, and the factor path; and a published one about a fourth security's session the
    next day. Review round 3 narrowed what is asked (I-A), and the stated budget is the count:

    - `reversal_1d` at `INSTANTS[0]`: asked, for the unknowable session -- it moves. The published
      decision moves nothing and the factor path moves no close-to-close return: not asked.
    - `reversal_5_sessions` at `INSTANTS[0]`: asked, for the unknowable session and the factor
      path of a return it reads on its own row -- both move.
    - `reversal_1d` the next day: the unknowable session is the first of its window, whose return
      into it is not read, and the only session it does read carries a published decision, which
      moves nothing. Not asked.
    - `momentum_20_sessions` at `INSTANTS[0]`: every decided session is the newest, inside the
      five sessions momentum skips. Not asked.
    - `earnings_yield_ttm` reads no session return. Not asked.

    After the repair the two builds are asked again -- each now holds an abstention with a
    decision in its window -- and the engine gives the stored answers, so nothing is listed."""
    runtime = tmp_path / "research"
    store = _copy(corpus, runtime / "panel")
    next_day = INSTANTS[0] + timedelta(days=1)
    _single(store, "reversal_1d/v1", as_ofs=(INSTANTS[0], next_day))
    _raw_only(store, "reversal_5_sessions/v1", as_ofs=(INSTANTS[0],))
    _raw_only(store, "momentum_20_sessions/v1", as_ofs=(INSTANTS[0],))
    _single(store, "earnings_yield_ttm/v1", as_ofs=(INSTANTS[0],))
    session, _published, _adjusted, _next = _with_decisions(
        store,
        (UNKNOWABLE_INDEX, "pre_close_contradicts_adj_factor"),
        (UNKNOWABLE_INDEX + 2, "pre_close_corroborated_over_adj_factor"),
        (UNKNOWABLE_INDEX + 4, "adj_factor_corroborated_over_pre_close"),
        (UNKNOWABLE_INDEX + 6, "pre_close_corroborated_over_adj_factor", next_day),
    )
    definition = FACTOR_DEFINITIONS.get("reversal_1d/v1")
    raw = [
        item
        for item in load_factor_manifests(store, definition, years=(2026,), as_of=FETCHED_AT)
        if item.as_of == INSTANTS[0]
    ]
    processed = [
        item
        for item in load_factor_transform_manifests(
            store, definition, years=(2026,), as_of=FETCHED_AT
        )
        if item.source_manifest_id == raw[0].manifest_id
    ]
    neutralized = [
        item
        for item in load_factor_neutralization_manifests(
            store, definition, years=(2026,), as_of=FETCHED_AT
        )
        if item.source_transform_manifest_id == processed[0].transform_manifest_id
    ]
    kept = _stored_answers(store, "reversal_1d/v1", as_of=next_day)[UNKNOWABLE_SECURITY]
    assert kept[0] == "computed"
    momentum = _stored_answers(store, "momentum_20_sessions/v1")

    stale, calls = _detect(store)

    assert calls == [2]
    assert sorted(
        (item.factor, item.subject, item.kind, item.stored_coverage, item.engine_coverage)
        for item in stale
    ) == [
        (
            "reversal_1d/v1",
            UNKNOWABLE_SECURITY,
            "pre_close_contradicts_adj_factor",
            "computed",
            "undefined_value",
        ),
        (
            "reversal_5_sessions/v1",
            UNKNOWABLE_SECURITY,
            "pre_close_contradicts_adj_factor",
            "computed",
            "undefined_value",
        ),
        (
            "reversal_5_sessions/v1",
            SECURITIES[UNKNOWABLE_INDEX + 4],
            "adj_factor_corroborated_over_pre_close",
            "computed",
            "computed",
        ),
    ]
    (reversal,) = [item for item in stale if item.factor == "reversal_1d/v1"]
    assert (reversal.as_of, reversal.session) == (INSTANTS[0], session)
    assert [(build.tier, build.year, build.manifest_id) for build in reversal.builds] == [
        ("raw", 2026, raw[0].manifest_id),
        ("processed", 2026, processed[0].transform_manifest_id),
        ("neutralized", 2026, neutralized[0].neutralization_manifest_id),
    ]
    (command,) = reversal.commands
    assert command[:6] == ("factor", "build", "--factor", "reversal_1d/v1", "--tier", "neutralized")
    for flag, value in (
        ("--supersedes-raw", raw[0].manifest_id),
        ("--supersedes-processed", processed[0].transform_manifest_id),
        ("--supersedes-neutralized", neutralized[0].neutralization_manifest_id),
        ("--as-of", INSTANTS[0].isoformat()),
    ):
        assert command[command.index(flag) + 1] == value
    (short,) = {item.commands for item in stale if item.factor == "reversal_5_sessions/v1"}
    assert [line[:6] for line in short] == [
        ("factor", "build", "--factor", "reversal_5_sessions/v1", "--tier", "raw")
    ]

    found = CliRunner().invoke(app, _detector_arguments(runtime))

    assert found.exit_code == 1, found.output
    assert "BUDGET stale-return-path-recompute 2 compute_factor calls" in found.stderr
    assert (
        f"  {UNKNOWABLE_SECURITY}: stored computed {reversal.stored_value} differs from the "
        "engine's current result undefined_value None; decision "
        f"pre_close_contradicts_adj_factor on {session.isoformat()} is inside its window"
    ) in found.stdout.splitlines()
    printed = [line for line in found.stdout.splitlines() if line.startswith("  openalpha ")]
    suffix = f"--runtime-dir {shlex.quote(str(runtime))}"
    assert printed == [
        f"  openalpha {shlex.join(command)} {suffix}",
        f"  openalpha {shlex.join(short[0])} {suffix}",
    ]

    for line in (command, short[0]):
        repaired = CliRunner().invoke(app, [*line, "--runtime-dir", str(runtime)])
        assert repaired.exit_code == 0, repaired.stderr

    assert _detect(store) == ((), [2])
    clean = CliRunner().invoke(app, _detector_arguments(runtime))
    assert clean.exit_code == 0, clean.output
    assert "stale return-path builds: none" in clean.stdout
    assert _stored_answers(store, "reversal_1d/v1")[UNKNOWABLE_SECURITY] == (
        "undefined_value",
        None,
    )
    assert _stored_answers(store, "reversal_1d/v1", as_of=next_day)[UNKNOWABLE_SECURITY] == kept
    assert _stored_answers(store, "momentum_20_sessions/v1") == momentum


def test_an_abstention_whose_decision_was_judged_again_is_listed_and_repaired(
    corpus: Path, tmp_path: Path
) -> None:
    """Review round 3, Minors 1 and 2. A build made while a session was unknowable stores
    `undefined_value` for it. Re-judged later -- a rebuilt band now corroborates the published
    `pre_close` -- the record says `published`, and the engine now computes a number. The stored
    abstention is a candidate because its window holds a decision of any path, so the detector
    lists it; after the printed rebuild it is `computed` under a published decision, which moves
    nothing, and nobody is asked again."""
    runtime = tmp_path / "research"
    store = _copy(corpus, runtime / "panel")
    _with_an_unknowable_session(store)
    _raw_only(store, "reversal_1d/v1", as_ofs=(INSTANTS[0],))
    assert _stored_answers(store, "reversal_1d/v1")[UNKNOWABLE_SECURITY] == (
        "undefined_value",
        None,
    )
    assert _detect(store) == ((), [1])

    (session,) = _with_decisions(
        store, (UNKNOWABLE_INDEX, "pre_close_corroborated_over_adj_factor")
    )
    (stale,), calls = _detect(store)

    assert calls == [1]
    assert (stale.subject, stale.session, stale.kind) == (
        UNKNOWABLE_SECURITY,
        session,
        "pre_close_corroborated_over_adj_factor",
    )
    assert (stale.stored_coverage, stale.engine_coverage) == ("undefined_value", "computed")
    (command,) = stale.commands
    repaired = CliRunner().invoke(app, [*command, "--runtime-dir", str(runtime)])
    assert repaired.exit_code == 0, repaired.stderr
    assert _detect(store) == ((), [0])
    assert _stored_answers(store, "reversal_1d/v1")[UNKNOWABLE_SECURITY] == (
        "computed",
        stale.engine_value,
    )


ALTERNATIVE_TRANSFORM: Final[FactorTransformSpec] = FactorTransformSpec.model_validate(
    {
        **CROSS_SECTION_STANDARD.model_dump(include=set(FactorTransformSpec.model_fields)),
        "key": "cross_section_alternative",
    }
)


def test_a_raw_build_under_two_transforms_is_repaired_by_one_command_per_transform(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 3, Minor 3. One raw build under two transforms, the first also neutralized --
    a neutralization answers one question per instant, so a second neutralized build of the same
    policy at the same instant cannot be stored beside it: four builds to supersede and one
    command per transform. The raw build is superseded once, by the first command; each command
    supersedes its own processed build, and the neutralized one rides with the transform it was
    made from. Run in the printed order they repair the store, and nothing is listed
    afterwards.

    Only one transform is registered, so the second is a test registry's, handed to the command
    line's own default -- the commands run are the printed ones, byte for byte."""
    registry = FactorTransformRegistry((CROSS_SECTION_STANDARD, ALTERNATIVE_TRANSFORM))
    monkeypatch.setitem(factor_view.factor_build_requests.__kwdefaults__, "transforms", registry)
    monkeypatch.setitem(factor_view.factor_build_request.__kwdefaults__, "transforms", registry)
    runtime = tmp_path / "research"
    store = _copy(corpus, runtime / "panel")
    alternative = ALTERNATIVE_TRANSFORM.qualified_key
    _single(store, "reversal_1d/v1", as_ofs=(INSTANTS[0],))
    _single(
        store,
        "reversal_1d/v1",
        as_ofs=(INSTANTS[0],),
        tier="processed",
        transform=alternative,
        neutralization="",
    )
    _with_an_unknowable_session(store)
    definition = FACTOR_DEFINITIONS.get("reversal_1d/v1")
    (raw,) = load_factor_manifests(store, definition, years=(2026,), as_of=FETCHED_AT)
    processed = {
        f"{item.transform_key}/v{item.transform_version}": item.transform_manifest_id
        for item in load_factor_transform_manifests(
            store, definition, years=(2026,), as_of=FETCHED_AT
        )
    }
    neutralized = {
        item.source_transform_manifest_id: item.neutralization_manifest_id
        for item in load_factor_neutralization_manifests(
            store, definition, years=(2026,), as_of=FETCHED_AT
        )
    }

    (stale,), calls = _detect(store)

    assert calls == [1]
    assert sorted((build.tier, build.manifest_id) for build in stale.builds) == sorted(
        [
            ("raw", raw.manifest_id),
            *(("processed", identifier) for identifier in processed.values()),
            ("neutralized", neutralized[processed[TRANSFORM]]),
        ]
    )

    def supersedes(command: tuple[str, ...]) -> dict[str, list[str]]:
        named: dict[str, list[str]] = {}
        for flag, value in pairwise(command):
            if flag.startswith("--supersedes-"):
                named.setdefault(flag, []).append(value)
        return named

    first, second = stale.commands
    by_transform = {
        command[command.index("--transform") + 1]: supersedes(command)
        for command in (first, second)
    }
    assert set(by_transform) == {TRANSFORM, alternative}
    for transform, named in by_transform.items():
        assert named["--supersedes-processed"] == [processed[transform]]
    assert by_transform[TRANSFORM]["--supersedes-neutralized"] == [
        neutralized[processed[TRANSFORM]]
    ]
    assert "--supersedes-neutralized" not in by_transform[alternative]
    assert supersedes(first)["--supersedes-raw"] == [raw.manifest_id]
    assert "--supersedes-raw" not in supersedes(second)
    tiers = {command[command.index("--transform") + 1]: command[5] for command in (first, second)}
    assert tiers == {TRANSFORM: "neutralized", alternative: "processed"}

    for command in (first, second):
        repaired = CliRunner().invoke(app, [*command, "--runtime-dir", str(runtime)])
        assert repaired.exit_code == 0, repaired.stderr
    assert _detect(store) == ((), [1])
    assert _stored_answers(store, "reversal_1d/v1")[UNKNOWABLE_SECURITY] == (
        "undefined_value",
        None,
    )
