"""`V2-P6-005`: the factor build's read path is faster, and every partition it writes is the same.

A performance change to a point-in-time read is only acceptable if it cannot move a byte of what
the build stores, so this file holds both halves together: the **equivalence** -- all 21 declared
factors, three instants each, all three tiers, stored and hashed -- and the **speed**, on a
generated whole-year panel.

## The corpus, and the shapes that decide whether a faster read is the same read

Generated at test time (`AGENTS.md` rule 6: a checked-in `.parquet` fails CI): `daily`,
`daily_basic` and `index_daily` from May 2025 to June 2026, and the four statement endpoints over
announcement years 2024-2026, for 120 securities -- enough that the shipped transform and
neutralisation, whose floors are 100 names, answer rather than code the cross section away. Most
securities are plain. The rest carry the shapes under which a read that returns *fewer rows* could
answer differently from one that returns them all -- which is the question this issue has to answer
"no" to:

- **`HALTED_LONG`** stops trading on 2026-03-02, so at the two later instants its newest sessions
  are months old. The engine forms a window from a security's own last sessions and bounds only
  its *span*, so a five-session reversal is still `computed` from February. A read cut off at "the
  sessions the lookback needs" would not see those rows at all.
- **`YOUNG`** lists on 2026-05-25 and **`DELISTED`** stops on 2025-11-28: `insufficient_history`
  stores the count of every row the security holds, so the whole history is part of the answer.
- **`GAPPY`** misses every seventh 2026 session, which moves the span bounds; **`NULL_CLOSE`** and
  **`VALUATION_HOLE`** are `input_missing` inside one window and not another.
- **`REVISED`**'s 2026Q1 income filing was re-announced on 2026-05-15, so it is withheld at the
  2026-05-06 instant and visible at the 2026-06-15 one -- the one row whose visibility is decided
  by the revision clock rather than by availability.
- **`RESTATED`**, **`AMBIGUOUS`** and **`AGREEING`** are the three period-axis multiplicity rules:
  a later announcement wins, a same-day disagreement codes `ambiguous_filing`, a same-day agreement
  collapses.

The first instant, 2026-01-08, needs 125 sessions for `momentum_120_sessions` and nine report
periods for `revenue_yoy_acceleration`, so both of its windows reach back into the 2025 and 2024
partitions: the read crosses a year boundary.
"""

from __future__ import annotations

import cProfile
import hashlib
import math
import pstats
import shutil
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final, cast
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_AVAILABILITY_TIME,
    DAILY_DATASET,
    PRE_CLOSE_COLUMN,
    PRICE_DATE_COLUMN,
    SESSION_CLOSE_TIME,
)
from openalpha_cn.domain.factor import PERIOD_INDEXED_DATASETS, FactorDefinition
from openalpha_cn.domain.factor_neutralization import (
    IndustryMarketCapCrossSection,
    SecurityCharacteristic,
    build_industry_market_cap_cross_section,
)
from openalpha_cn.domain.financial_statements import ANNOUNCEMENT_DATE_COLUMN, REPORT_PERIOD_COLUMN
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET, MARKET_INDEX_CODE
from openalpha_cn.domain.industry_classification import INDUSTRY_MEMBERSHIP_TAXONOMY
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    FACTOR_DEFINITIONS,
    FactorPanel,
    apply_factor_transform,
    compute_factor,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import split_panel_batch_by_year, write_panel_batch
from openalpha_cn.panel_neutralization import (
    INDUSTRY_AND_SIZE,
    apply_factor_neutralization,
    write_neutralized_factor_panels,
)

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
COMMIT: Final[str] = "0123456789abcdef"
BUILT_AT: Final[datetime] = datetime(2026, 9, 26, tzinfo=UTC)
FETCHED_AT: Final[datetime] = datetime(2026, 7, 1, tzinfo=UTC)
YEARS: Final[tuple[int, ...]] = (2024, 2025, 2026)
"""The statement partitions' announcement years. The session datasets start in May 2025."""
SESSION_YEARS: Final[tuple[int, ...]] = (2025, 2026)

INSTANTS: Final[tuple[datetime, ...]] = (
    datetime(2026, 1, 8, 9, 0, tzinfo=UTC),
    datetime(2026, 5, 6, 9, 0, tzinfo=UTC),
    datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
)
"""Start of year (both reaches cross into 2025 and 2024), inside `REVISED`'s revision window, and
mid-year after it. 17:00 Asia/Shanghai, so each day's own session has published."""

STALENESS: Final[timedelta] = timedelta(days=120)
"""Wide enough for the statement endpoints, whose newest filing at 2026-01-08 is October's."""

SECURITIES: Final[tuple[str, ...]] = tuple(
    f"{600000 + index:06d}.SH" if index % 2 else f"{index + 1:06d}.SZ" for index in range(120)
)
REVISED, RESTATED, AMBIGUOUS, AGREEING = (
    SECURITIES[10],
    SECURITIES[11],
    SECURITIES[12],
    SECURITIES[13],
)
YOUNG, HALTED_LONG, GAPPY, NULL_CLOSE, VALUATION_HOLE, DELISTED = SECURITIES[100:106]
OUTSIDE: Final[str] = SECURITIES[-1]
UNIVERSE: Final[frozenset[str]] = frozenset(SECURITIES) - {OUTSIDE}

YOUNG_FROM: Final[date] = date(2026, 5, 25)
HALTED_FROM: Final[date] = date(2026, 3, 2)
DELISTED_AFTER: Final[date] = date(2025, 11, 28)
NULL_CLOSE_ON: Final[date] = date(2026, 6, 10)
VALUATION_HOLE_ON: Final[date] = date(2026, 6, 12)
REVISED_ON: Final[date] = date(2026, 5, 15)

HOLIDAYS: Final[frozenset[date]] = frozenset(
    {
        date(2024, 1, 1),
        *(date(2024, 2, day) for day in range(12, 17)),
        date(2025, 1, 1),
        *(date(2025, 2, day) for day in range(3, 7)),
        date(2026, 1, 1),
        date(2026, 1, 2),
        *(date(2026, 2, day) for day in range(16, 21)),
    }
)
FIRST_SESSION: Final[date] = date(2025, 5, 5)
LAST_SESSION: Final[date] = date(2026, 6, 30)


def _sessions(first: date = FIRST_SESSION, last: date = LAST_SESSION) -> tuple[date, ...]:
    days = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
    return tuple(day for day in days if day.weekday() < 5 and day not in HOLIDAYS)


SESSIONS: Final[tuple[date, ...]] = _sessions()


def _close_instant(day: date) -> datetime:
    return datetime.combine(day, SESSION_CLOSE_TIME, tzinfo=SHANGHAI)


def _published_instant(day: date) -> datetime:
    return datetime.combine(day, DAILY_AVAILABILITY_TIME, tzinfo=SHANGHAI)


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=SHANGHAI)


def _traded(index: int, code: str, day: date) -> bool:
    if code == YOUNG:
        return day >= YOUNG_FROM
    if code == HALTED_LONG:
        return day < HALTED_FROM
    if code == DELISTED:
        return day <= DELISTED_AFTER
    if code == GAPPY and day.year == 2026:
        return SESSIONS.index(day) % 7 != 3
    return True


def _batch(
    dataset: str,
    rows: Sequence[tuple[str, datetime, datetime, datetime]],
    columns: Sequence[PanelColumn],
    *,
    fetched_at: datetime = FETCHED_AT,
) -> ColumnarPanelBatch:
    """`rows` is `(subject, event_time, available_time, revision_time)` per row."""
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=dataset,
        kind=dataset,
        as_of=fetched_at,
        fetched_at=fetched_at,
        status="success",
        subjects=tuple(row[0] for row in rows),
        timeline=TimelineColumns(
            event_time=tuple(row[1] for row in rows),
            available_time=tuple(row[2] for row in rows),
            ingested_time=tuple(row[2] for row in rows),
            revision_time=tuple(row[3] for row in rows),
        ),
        columns=tuple(columns),
    )


def _price_path(index: int, code: str) -> dict[date, tuple[float | None, float]]:
    """`(close, pre_close)` per traded session, `pre_close` the previous traded close."""
    path: dict[date, tuple[float | None, float]] = {}
    price = 10.0 + index * 0.25
    position = 0
    for day in SESSIONS:
        if not _traded(index, code, day):
            continue
        previous = price
        price *= 1.0 + 0.02 * math.sin(index * 1.7 + position * 0.9)
        position += 1
        close: float | None = price
        if code == NULL_CLOSE and day == NULL_CLOSE_ON:
            close = None
        path[day] = (close, previous)
    return path


def _session_batches() -> tuple[ColumnarPanelBatch, ...]:
    bar_rows: list[tuple[str, datetime, datetime, datetime]] = []
    bar_cells: list[tuple[str, float | None, float, float]] = []
    basic_rows: list[tuple[str, datetime, datetime, datetime]] = []
    basic_cells: list[tuple[str, float, float]] = []
    for index, code in enumerate(SECURITIES):
        for position, (day, (close, pre_close)) in enumerate(_price_path(index, code).items()):
            clocks = (code, _close_instant(day), _published_instant(day), _published_instant(day))
            bar_rows.append(clocks)
            bar_cells.append(
                (day.isoformat(), close, pre_close, 10_000.0 + index * 10 + (position % 13) * 100)
            )
            if code == VALUATION_HOLE and day == VALUATION_HOLE_ON:
                continue
            basic_rows.append(clocks)
            basic_cells.append(
                (
                    day.isoformat(),
                    0.5 + ((index + position) % 17) * 0.1,
                    1_000_000.0 + index * 10_000.0 + position * 10.0,
                )
            )
    index_rows = [
        (MARKET_INDEX_CODE, _close_instant(day), _published_instant(day), _published_instant(day))
        for day in SESSIONS
    ]
    index_path = _price_path(7, MARKET_INDEX_CODE)
    return (
        _batch(
            DAILY_DATASET,
            bar_rows,
            (
                PanelColumn(PRICE_DATE_COLUMN, "string", tuple(cell[0] for cell in bar_cells)),
                PanelColumn(CLOSE_COLUMN, "float", tuple(cell[1] for cell in bar_cells)),
                PanelColumn(PRE_CLOSE_COLUMN, "float", tuple(cell[2] for cell in bar_cells)),
                PanelColumn("amount", "float", tuple(cell[3] for cell in bar_cells)),
            ),
        ),
        _batch(
            "daily_basic",
            basic_rows,
            (
                PanelColumn(PRICE_DATE_COLUMN, "string", tuple(cell[0] for cell in basic_cells)),
                PanelColumn("turnover_rate", "float", tuple(cell[1] for cell in basic_cells)),
                PanelColumn("total_mv", "float", tuple(cell[2] for cell in basic_cells)),
            ),
        ),
        _batch(
            INDEX_DAILY_DATASET,
            index_rows,
            (
                PanelColumn(
                    PRICE_DATE_COLUMN, "string", tuple(day.isoformat() for day in SESSIONS)
                ),
                PanelColumn(CLOSE_COLUMN, "float", tuple(index_path[day][0] for day in SESSIONS)),
                PanelColumn(
                    PRE_CLOSE_COLUMN, "float", tuple(index_path[day][1] for day in SESSIONS)
                ),
            ),
        ),
    )


PERIODS: Final[tuple[date, ...]] = tuple(
    date(year, month, day)
    for year in (2023, 2024, 2025, 2026)
    for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))
    if date(2023, 6, 30) <= date(year, month, day) <= date(2026, 3, 31)
)

STATEMENT_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "income": ("n_income_attr_p", "total_revenue", "oper_cost", "n_income"),
    "balancesheet": ("total_hldr_eqy_exc_min_int", "total_assets", "total_cur_liab"),
    "cashflow": ("n_cashflow_act",),
    "fina_indicator": ("profit_dedt",),
}


def _announced(index: int, period: date) -> date:
    """When `period` was filed: the ordinary deadline, except 2023's two filed late in 2024."""
    if period < date(2023, 12, 31):
        return date(2024, 1, 3) + timedelta(days=index % 3)
    deadline = {
        3: date(period.year, 4, 20),
        6: date(period.year, 8, 20),
        9: date(period.year, 10, 20),
        12: date(period.year + 1, 3, 20),
    }[period.month]
    return deadline + timedelta(days=index % 5)


def _statement_values(dataset: str, index: int, position: int) -> tuple[float, ...]:
    """Positive, growing and security-specific, so every ratio and growth rate is defined."""
    values = {
        "income": (
            100.0 + index + 3 * position,
            1_000.0 + 10 * index + 20 * position,
            600.0 + 5 * index + 11 * position,
            110.0 + index + 3 * position,
        ),
        "balancesheet": (
            5_000.0 + 50 * index + 10 * position,
            20_000.0 + 100 * index + 30 * position,
            6_000.0 + 20 * index + 7 * position,
        ),
        "cashflow": (90.0 + index + 2 * position,),
        "fina_indicator": (95.0 + index + 3 * position,),
    }
    return values[dataset]


def _statement_batch(dataset: str) -> ColumnarPanelBatch:
    rows: list[tuple[str, datetime, datetime, datetime]] = []
    cells: list[tuple[str, str, tuple[float, ...]]] = []

    def filing(
        code: str,
        period: date,
        announced: date,
        values: tuple[float, ...],
        *,
        revised: date | None = None,
    ) -> None:
        stamp = _midnight(announced)
        rows.append((code, stamp, stamp, stamp if revised is None else _midnight(revised)))
        cells.append((period.isoformat(), announced.isoformat(), values))

    for index, code in enumerate(SECURITIES):
        for position, period in enumerate(PERIODS):
            values = _statement_values(dataset, index, position)
            announced = _announced(index, period)
            revised = (
                REVISED_ON
                if (dataset == "income" and code == REVISED and period == PERIODS[-1])
                else None
            )
            filing(code, period, announced, values, revised=revised)
            if dataset == "income" and code == RESTATED and period == date(2025, 9, 30):
                filing(code, period, date(2025, 12, 15), tuple(value * 1.5 for value in values))
            if dataset == "income" and code == AMBIGUOUS and period == date(2025, 12, 31):
                filing(code, period, announced, (values[0] + 7.0, *values[1:]))
            if dataset == "balancesheet" and code == AGREEING and period == date(2025, 12, 31):
                filing(code, period, announced, values)
    width = len(STATEMENT_COLUMNS[dataset])
    return _batch(
        dataset,
        rows,
        (
            PanelColumn(REPORT_PERIOD_COLUMN, "string", tuple(cell[0] for cell in cells)),
            PanelColumn(ANNOUNCEMENT_DATE_COLUMN, "string", tuple(cell[1] for cell in cells)),
            *(
                PanelColumn(name, "float", tuple(cell[2][column] for cell in cells))
                for column, name in zip(range(width), STATEMENT_COLUMNS[dataset], strict=True)
            ),
        ),
    )


def _write_corpus(root: Path) -> None:
    store = PanelStore(root)
    batches = (*_session_batches(), *(_statement_batch(name) for name in STATEMENT_COLUMNS))
    for batch in batches:
        for year, part in split_panel_batch_by_year(batch):
            write_panel_batch(store, part, year=year)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The input partitions, written once. Every test copies them, because a build writes."""
    root = tmp_path_factory.mktemp("read_path_corpus") / "panel"
    _write_corpus(root)
    return root


def _requirements(definition: FactorDefinition, as_of: datetime) -> dict[str, ReadinessRequirement]:
    requirements: dict[str, ReadinessRequirement] = {}
    for dataset in definition.datasets:
        fields = definition.columns_of(dataset)
        if dataset in PERIOD_INDEXED_DATASETS:
            fields = (*fields, REPORT_PERIOD_COLUMN)
        requirements[dataset] = ReadinessRequirement(
            dataset=dataset,
            as_of=as_of,
            years=YEARS if dataset in PERIOD_INDEXED_DATASETS else SESSION_YEARS,
            required_dates=None,
            required_subjects=None,
            required_fields=fields,
            max_staleness=STALENESS,
        )
    return requirements


INDUSTRIES: Final[tuple[str, ...]] = ("801080.SI", "801150.SI", "801010.SI")


def _characteristics(as_of: datetime, subjects: Sequence[str]) -> IndustryMarketCapCrossSection:
    """The neutralisation's second cross section as a value; it reads no factor input."""
    return build_industry_market_cap_cross_section(
        as_of=as_of,
        taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
        industry_level=INDUSTRY_AND_SIZE.industry_level,
        market_cap_measure=INDUSTRY_AND_SIZE.market_cap_measure,
        characteristics=tuple(
            SecurityCharacteristic(
                subject=code,
                industry_code=INDUSTRIES[SECURITIES.index(code) % len(INDUSTRIES)],
                market_cap=2_000_000.0 + 640_000.0 * SECURITIES.index(code),
                is_backfilled=False,
            )
            for code in subjects
        ),
    )


def _raw_panels(store: PanelStore, definition: FactorDefinition) -> list[FactorPanel]:
    """One raw cross section per instant, in ascending order."""
    return [
        compute_factor(
            store,
            definition,
            as_of=as_of,
            subjects=SECURITIES,
            universe=UNIVERSE,
            requirements=_requirements(definition, as_of),
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )
        for as_of in INSTANTS
    ]


def _stored_digest(root: Path, definition: FactorDefinition) -> str:
    """Build all three tiers at every instant, store them, and hash the partitions written."""
    store = PanelStore(root)
    raw = _raw_panels(store, definition)
    processed = [
        apply_factor_transform(panel, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT)
        for panel in raw
    ]
    neutralized = [
        apply_factor_neutralization(
            panel,
            INDUSTRY_AND_SIZE,
            _characteristics(panel.as_of, [item.subject for item in panel.observations]),
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )
        for panel in processed
    ]
    written = (
        *write_factor_panels(store, raw),
        *write_processed_factor_panels(store, processed),
        *write_neutralized_factor_panels(store, neutralized),
    )
    lines = sorted(f"{ref.dataset}@{ref.year}={ref.content_hash}" for ref in written)
    assert len(lines) == 6, lines
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@pytest.fixture
def scratch(corpus: Path, tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "panel"
    shutil.copytree(corpus, root)
    yield root


STORED_BEFORE_THIS_CHANGE: Final[dict[str, str]] = {
    "reversal_1d/v1": ("53a7211b10ed288907995cc7b0dde8506ebf4c83fe4cd69e982c933ce1e15ec4"),
    "momentum_20_sessions/v1": ("93e6e57e0f7f267fce68a3b9abe607371685439308bce189de208aca6991dbf4"),
    "momentum_60_sessions/v1": ("ff7608e199af7179ac913e3308f2bbc3cb38c6e1c15786c024856f28f0a3c35e"),
    "momentum_120_sessions/v1": (
        "7c367ba08c15822b97d0acb8a4aad1a2403ce3aeb649b2da4a503cf9a8b1fd08"
    ),
    "reversal_5_sessions/v1": ("94acbe7c1063f41b42ad697baa1e474caaf67000054120786c0d0194b89b08d9"),
    "return_vol_60/v1": ("72d5f88b633674fa213348f067c4f94f70efd1456caa4546ca0ed2d801d4be27"),
    "downside_vol_60/v1": ("2fbb2c4725234aa926a02988ca4262c3191920c9e5c05f582858ee8c53963513"),
    "turnover_60/v1": ("8300ffb73f8f701a9d816afad0b60e43464ecbfb6779d3a8ae41106199f4d590"),
    "amihud_60/v1": ("82fa72a1d92c6cf3819d5f7bdea5cc024eee4161613fbcd532dd489e6b355c22"),
    "residual_vol_60/v1": ("c37d4203d1d145251de98b28cd811adf9737d654af8d7d6c07dab34d6d81149f"),
    "earnings_yield_ttm/v1": ("c1414839e02165dc8445d1ad91473db153ed8685ed74fe245cfefba42371f4f3"),
    "book_to_price/v1": ("72cc5e25c428a3178df41c25d97f655cf74761a72097298c974be41bc60e7f98"),
    "sales_yield_ttm/v1": ("0c48e5d3ae9df10c4e5aa10e8b07bd5952b657845e3d6b0bf378462fb2d223b8"),
    "deducted_earnings_yield_ttm/v1": (
        "d99204552566bde0eec9ea39528d859343051679376b68721ce0b3dd28a04d86"
    ),
    "return_on_equity_ttm/v1": ("018ec6f6a0c8f0c17be35c1067e9e92488eb9422c9225b5915f6033dbca88e62"),
    "return_on_capital_ttm/v1": (
        "d0527c4b85a9e90f80a840d895e4653df47d726d75dc8c2f44976cf55e2f1660"
    ),
    "gross_margin_stability/v1": (
        "ad06fd863ccbd435698ae932d2970970f79bec7b78f95777ecb55d6c574c71dc"
    ),
    "accruals_ttm/v1": ("1e799db543cd07fc43398c22a1d9b89102f0ed6ebe64f42e90ca8e3df0c449ae"),
    "revenue_yoy/v1": ("ffa00f8f60f81d14477436608c7519ba8bdc25fabc547a1092e36a60024d9175"),
    "net_profit_yoy/v1": ("d5c83c27bd48c59d4d279f4cb49c761de8af33de07ddb01eee04a44fa3379001"),
    "revenue_yoy_acceleration/v1": (
        "ea36c0a708ce1b7ef0451908d4f8c2c8d90012e55d76ee506e8348be18a94336"
    ),
}
"""Per factor, the SHA-256 of its six written partitions' `content_hash`es, measured at `cf7ba05`.

The commit before `V2-P6-005` touched the read path, running exactly `_stored_digest` over this
file's corpus. Pinned rather than recomputed because a reference computed by the code under test
is that code agreeing with itself; these are what the old read path wrote.
"""


@pytest.mark.parametrize("key", FACTOR_DEFINITIONS.qualified_keys)
def test_every_factor_stores_the_partitions_it_stored_before_the_read_path_changed(
    key: str, scratch: Path
) -> None:
    assert _stored_digest(scratch, FACTOR_DEFINITIONS.get(key)) == STORED_BEFORE_THIS_CHANGE[key]


def test_the_pinned_table_names_every_declared_factor_and_nothing_else() -> None:
    assert set(STORED_BEFORE_THIS_CHANGE) == set(FACTOR_DEFINITIONS.qualified_keys)


# --- speed ---------------------------------------------------------------------------------------

WIDE_SECURITIES: Final[tuple[str, ...]] = tuple(f"{600000 + index:06d}.SH" for index in range(600))
WIDE_SESSIONS: Final[tuple[date, ...]] = _sessions(date(2026, 1, 5), date(2026, 12, 31))[:250]
WIDE_FIELDS: Final[tuple[str, ...]] = (CLOSE_COLUMN, PRE_CLOSE_COLUMN)
REVERSAL_5: Final[FactorDefinition] = FACTOR_DEFINITIONS.get("reversal_5_sessions/v1")


def _after_close(day: date) -> datetime:
    return _published_instant(day) + timedelta(hours=1)


@pytest.fixture(scope="module")
def wide(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    """600 securities x 250 sessions of `daily`: 150,000 rows, the size of the speed budget."""
    store = PanelStore(tmp_path_factory.mktemp("read_path_wide") / "panel")
    rows = [(code, day) for day in WIDE_SESSIONS for code in WIDE_SECURITIES]
    closes = [10.0 + WIDE_SESSIONS.index(day) * 0.01 for _, day in rows]
    write_panel_batch(
        store,
        _batch(
            DAILY_DATASET,
            [
                (code, _close_instant(day), _published_instant(day), _published_instant(day))
                for code, day in rows
            ],
            (
                PanelColumn(PRICE_DATE_COLUMN, "string", tuple(day.isoformat() for _, day in rows)),
                PanelColumn(CLOSE_COLUMN, "float", tuple(closes)),
                PanelColumn(PRE_CLOSE_COLUMN, "float", tuple(value - 0.005 for value in closes)),
            ),
            fetched_at=datetime(2027, 1, 1, tzinfo=UTC),
        ),
        year=2026,
    )
    return store


def _wide_cross_section(store: PanelStore, as_of: datetime) -> FactorPanel:
    return compute_factor(
        store,
        REVERSAL_5,
        as_of=as_of,
        subjects=WIDE_SECURITIES,
        universe=WIDE_SECURITIES,
        requirements={
            DAILY_DATASET: ReadinessRequirement(
                dataset=DAILY_DATASET,
                as_of=as_of,
                years=(2026,),
                required_dates=None,
                required_subjects=None,
                required_fields=WIDE_FIELDS,
                max_staleness=timedelta(days=5),
            )
        },
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )


def _fastest(attempts: int, run: Callable[[], object]) -> float:
    """The best of `attempts` wall-clock timings: noise on a shared machine only ever adds."""
    timings = []
    for _ in range(attempts):
        start = time.perf_counter()
        run()
        timings.append(time.perf_counter() - start)
    return min(timings)


def _calls(run: Callable[[], object], module: str, function: str) -> int:
    """How many times `run` called `module`'s `function`, from Python or from C, per cProfile."""
    profiler = cProfile.Profile()
    profiler.runcall(run)
    stats = cast(dict[tuple[str, int, str], tuple[int, ...]], vars(pstats.Stats(profiler))["stats"])
    return sum(
        entry[1]
        for (filename, _line, name), entry in stats.items()
        if name == function and filename.replace("\\", "/").endswith(module)
    )


def test_a_single_instant_resolves_each_event_instant_once_rather_than_once_per_row(
    wide: PanelStore,
) -> None:
    """150,000 visible rows over 250 distinct `event_time` instants: 250 resolutions, not 150,000.

    The first of the two per-row constants the 2026-09-26 profile found: the engine converted each
    row's instant to a session date although the answer changes once per session.
    """
    as_of = _after_close(WIDE_SESSIONS[-1])

    resolved = _calls(
        lambda: _wide_cross_section(wide, as_of), "openalpha_cn/panel_factors.py", "_session_date"
    )

    assert resolved == len(WIDE_SESSIONS)


def test_a_single_instant_makes_no_time_zone_lookup_per_row(wide: PanelStore) -> None:
    """The second: DuckDB built each `event_time` cell as an aware datetime in the host's zone.

    Two `pytz.timezone` lookups, a `localize` and a `fromutc` per cell, inside the fetch -- 3.78 M
    lookups over two whole-market instants in the profile. The read now takes `event_time` as UTC
    wall time, so the only lookups left are the handful of aware values the readiness census
    returns, however many rows there are.
    """
    as_of = _after_close(WIDE_SESSIONS[-1])

    lookups = _calls(lambda: _wide_cross_section(wide, as_of), "pytz/__init__.py", "timezone")

    assert lookups < 100


def test_a_finite_stored_float_is_taken_as_it_is_without_a_call_per_cell(wide: PanelStore) -> None:
    """`_numeric` returns a finite float unchanged, so it is called only for a cell that is not one.

    Every cell of this corpus is a finite float, so nothing reaches it; before `V2-P6-005` all
    300,000 did. The refusals it owns are still driven, cell by cell, by
    `tests/unit/test_factor_value_family.py`.
    """
    as_of = _after_close(WIDE_SESSIONS[-1])

    called = _calls(
        lambda: _wide_cross_section(wide, as_of), "openalpha_cn/panel_factors.py", "_numeric"
    )

    assert called == 0


def test_one_raw_cross_section_over_a_year_of_600_securities_is_built_in_under_0_3_seconds(
    wide: PanelStore,
) -> None:
    """One factor, one `as_of`, all 150,000 visible rows read: the single-instant budget.

    Measured at 0.65-0.85 s before `V2-P6-005` on a machine at load average ~15. The three tests
    above are the deterministic half of this one, for the reason
    `tests/integration/panel/test_readiness_assessment_cost.py` gives: a stopwatch alone is not
    a gate on a shared machine. This is the budget the issue states, taken as the best of five.
    """
    as_of = _after_close(WIDE_SESSIONS[-1])

    assert dict(_wide_cross_section(wide, as_of).coverage_census())["computed"] == 600
    assert _fastest(5, lambda: _wide_cross_section(wide, as_of)) < 0.3
