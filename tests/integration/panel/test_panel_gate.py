"""The fail-closed dependency gate against a real store (`V2-P1-013`).

The roadmap's acceptance for this issue is one sentence -- "assert blocking, not an empty
success" -- so every test here is a **pair**: a defect is injected, the gate is asserted to
refuse, and the downstream read is asserted to be unable to come back with an empty result
that a caller would read as "there was nothing there". The clean case is paired the other way:
the gate clears and the downstream gets real rows, and a *true* empty answer (a session on
which nothing was halted) still comes back empty, because a gate that could not tell those
two apart would be the ambiguity this issue exists to remove.

## Why this fixture is not `test_panel_doctor.py`'s

That one has no corporate action in it, and a corporate action is the shape this issue turns
on. `000001.SZ`'s real 2026-06-11/06-12 pair is reproduced here row for row -- close 11.30
then 11.24, a published `pre_close` of 10.94 rather than 11.30, and `adj_factor` stepping
134.5794 -> 139.008 -- because that pair is where a missing factor row stops being a smaller
answer and becomes a **wrong** one: -0.530973% against a true +2.742251%, with the sign
reversed. The sessions are relocated into January so that the stored partition starts at the
year's first session (`_session_census` bounds its expectation at 1 January of the partition's
own year); the prices, the factors and the arithmetic are the measured ones.

Everything on disk is built at run time: `scripts/verify_publication.py` blocks `.parquet` and
`.duckdb` from the tree, and a committed fixture is one that goes stale in silence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from openalpha_cn.domain.adjustment import AdjustmentHistory
from openalpha_cn.domain.daily_prices import session_returns
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore
from openalpha_cn.panel_doctor import DATASET_CADENCE, PanelDoctorError, panel_health_report
from openalpha_cn.panel_gate import (
    SESSION_SCOPED_CROSS_CHECKS,
    UNVERIFIED_DAILY_COVERAGE,
    DependencyClearance,
    DependencyRequest,
    PanelGateError,
    require_datasets,
)
from openalpha_cn.panel_ingest import (
    ADJ_FACTOR_DATASET,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    INDEX_WEIGHT_DATASET,
    PRICE_LIMIT_DATASET,
    STOCK_BASIC_DATASET,
    SUSPENSION_DATASET,
    TRADING_CALENDAR_DATASET,
    load_adjustment_histories,
    load_daily_bars,
    load_stock_universe,
    load_suspensions,
    write_adjustment_factors,
    write_daily_panel,
    write_financial_statements,
    write_index_weights,
    write_panel_batch,
    write_price_limits,
    write_stock_universe,
    write_suspensions,
    write_trading_calendar,
)

EXCHANGE = "SZSE"
YEAR = 2026
FIRST_DAY = date(2026, 1, 1)
LAST_DAY = date(2026, 12, 31)
NEW_YEAR_HOLIDAY = (date(2026, 1, 1), date(2026, 1, 2))

PING_AN = "000001.SZ"
VANKE = "000002.SZ"
SPDB = "600000.SH"
SECURITIES = (PING_AN, VANKE, SPDB)
INDEX_CODE = "000300.SH"
INCOME_DATASET = "income"
CASHFLOW_DATASET = "cashflow"
LISTED_ON = date(2026, 1, 2)
TERMINATED_SECURITY = "000004.SZ"
TERMINATED_ON = date(2026, 1, 5)
"""A fourth name that left the market on the first stored session; `delist_date` is exclusive.

So it is listed on 2026-01-02..01-04 and on no session this panel holds, which is what lets the
registry carry a termination without any price dataset having to carry a fourth security.
"""
FILING_ANNOUNCED = date(2026, 1, 5)
REPORT_PERIOD = date(2025, 12, 31)

# 12:00 Asia/Shanghai on Saturday 2026-01-17, so `_sessions_published_through` stops at Friday
# 2026-01-16, the last session written below.
AS_OF = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
FETCHED_AT = AS_OF
# 12:00 Asia/Shanghai on 2026-01-08: an ingest run that stopped nine days before `AS_OF`. Both
# write-time censuses bound their expectation at `fetched_at - 1 day`, so a series stopping on
# the 7th is complete *for this fetch* and reaches the store -- the only shape `stale` can be
# injected in without also injecting a hole.
STALE_FETCHED_AT = datetime(2026, 1, 8, 4, 0, tzinfo=UTC)

DATASETS = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
    PRICE_LIMIT_DATASET,
    INDEX_WEIGHT_DATASET,
    INCOME_DATASET,
    CASHFLOW_DATASET,
)

INCOME_VALUE_COLUMNS = (
    "total_revenue",
    "revenue",
    "oper_cost",
    "operate_profit",
    "total_profit",
    "income_tax",
    "n_income",
    "n_income_attr_p",
    "basic_eps",
    "ebit",
)

CASHFLOW_VALUE_COLUMNS = (
    "n_cashflow_act",
    "n_cashflow_inv_act",
    "n_cash_flows_fnc_act",
    "c_fr_sale_sg",
    "free_cashflow",
)

_DAILY_BASIC_EXTRA = (
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)


def _calendar_days() -> tuple[date, ...]:
    span = (LAST_DAY - FIRST_DAY).days + 1
    return tuple(FIRST_DAY + timedelta(days=offset) for offset in range(span))


def _open_days() -> tuple[date, ...]:
    """Every weekday of 2026 except the New Year recess, which is why the first stored session
    is the fifth and the write-time census still passes."""
    closed = set(NEW_YEAR_HOLIDAY)
    return tuple(day for day in _calendar_days() if day.weekday() < 5 and day not in closed)


SESSIONS = tuple(day for day in _open_days() if date(2026, 1, 5) <= day <= date(2026, 1, 16))
LAST_SESSION = SESSIONS[-1]
HALTED_SECURITY = SPDB
HALTED_SESSION = SESSIONS[2]
EX_RIGHTS_SESSION = SESSIONS[6]
PRE_EX_SESSION = SESSIONS[5]

# `000001.SZ`'s real series around its 2026-06-12 ex-rights day, relocated onto these sessions.
# Index 4 -> 11.32, 5 -> 11.30 and 6 -> 11.24 are the three published closes; the fourth
# published number, `pre_close` = 10.94 on the ex-rights session, is what makes the day a
# corporate action rather than a fall.
_CLOSES: Mapping[str, tuple[float, ...]] = {
    PING_AN: (11.15, 11.21, 11.18, 11.27, 11.32, 11.30, 11.24, 11.06, 11.12, 11.19),
    VANKE: (9.10, 9.04, 9.12, 9.19, 9.15, 9.22, 9.28, 9.21, 9.30, 9.36),
    SPDB: (7.55, 7.61, 7.58, 7.64, 7.70, 7.66, 7.73, 7.79, 7.75, 7.82),
}
_OPENING_PRE_CLOSE: Mapping[str, float] = {PING_AN: 11.10, VANKE: 9.16, SPDB: 7.50}
EX_RIGHTS_PRE_CLOSE = 10.94
FACTOR_BEFORE_EX_RIGHTS = 134.5794
FACTOR_AFTER_EX_RIGHTS = 139.008
_FLAT_FACTOR: Mapping[str, float] = {VANKE: 21.4562, SPDB: 8.7719}

# `(11.24 * 139.008) / (11.30 * 134.5794) - 1`, the factor-adjusted return across the ex-rights
# session, and `11.24 / 11.30 - 1`, what the same call answers when the factor step is missing.
ADJUSTED_RETURN = 0.02742251
UNADJUSTED_RETURN = -0.005309734513274336


def calendar() -> TradingCalendar:
    opens = set(_open_days())
    return build_trading_calendar(
        EXCHANGE,
        [CalendarDay(calendar_date=day, is_trading=day in opens) for day in _calendar_days()],
    )


def _close(code: str, day: date) -> float:
    return _CLOSES[code][SESSIONS.index(day)]


def _pre_close(code: str, day: date) -> float:
    """The published previous close, which on an ex-rights session is *not* the previous
    session's close: 10.94 against 11.30 on `000001.SZ`'s real day."""
    if code == PING_AN and day == EX_RIGHTS_SESSION:
        return EX_RIGHTS_PRE_CLOSE
    position = SESSIONS.index(day)
    if position == 0:
        return _OPENING_PRE_CLOSE[code]
    return _CLOSES[code][position - 1]


def _factor(code: str, day: date) -> float:
    if code != PING_AN:
        return _FLAT_FACTOR[code]
    return FACTOR_AFTER_EX_RIGHTS if day >= EX_RIGHTS_SESSION else FACTOR_BEFORE_EX_RIGHTS


def _midnight_shanghai(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=8)


def _close_time(day: date) -> datetime:
    """15:00 Asia/Shanghai, the session's event instant."""
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)


def _published_time(day: date) -> datetime:
    """16:30 Asia/Shanghai, when the session became knowable."""
    return datetime(day.year, day.month, day.day, 8, 30, tzinfo=UTC)


def _batch(
    dataset: str,
    *,
    subjects: Sequence[str],
    columns: Sequence[PanelColumn],
    event_time: Sequence[datetime],
    available_time: Sequence[datetime],
    revision_time: Sequence[datetime] | None = None,
    fetched_at: datetime = FETCHED_AT,
) -> ColumnarPanelBatch:
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=dataset,
        kind=dataset,
        as_of=fetched_at,
        fetched_at=fetched_at,
        status="success",
        subjects=tuple(subjects),
        timeline=TimelineColumns(
            event_time=tuple(event_time),
            available_time=tuple(available_time),
            ingested_time=tuple(available_time),
            revision_time=tuple(revision_time if revision_time is not None else available_time),
        ),
        columns=tuple(columns),
    )


def calendar_batch() -> ColumnarPanelBatch:
    days = _calendar_days()
    opens = set(_open_days())
    previous: list[str | None] = []
    seen: date | None = None
    for day in days:
        previous.append(seen.isoformat() if seen is not None else None)
        if day in opens:
            seen = day
    return _batch(
        TRADING_CALENDAR_DATASET,
        subjects=[EXCHANGE] * len(days),
        columns=[
            PanelColumn("cal_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("is_open", "boolean", tuple(day in opens for day in days)),
            PanelColumn("pretrade_date", "string", tuple(previous)),
        ],
        event_time=[_midnight_shanghai(day) for day in days],
        available_time=[_midnight_shanghai(FIRST_DAY)] * len(days),
    )


def universe_batch(
    securities: Sequence[str] = SECURITIES, *, terminated: bool = False
) -> ColumnarPanelBatch:
    """The registry, optionally carrying the delisted half the live fetch asks for.

    `terminated=False` is the `list_status='L'` shape -- 5,539 rows against `'L,D'`'s 5,878 on
    the real endpoint -- and it is what every other test here uses, because it is what a
    listed-only registry looks like: entirely well formed. The extra name is deliberately not
    one of `SECURITIES` and its termination lands on the first stored session, so it is in no
    cross section and the price datasets are untouched.
    """
    codes = list(securities)
    events = ["listing"] * len(codes)
    days = [LISTED_ON] * len(codes)
    if terminated:
        codes.extend((TERMINATED_SECURITY, TERMINATED_SECURITY))
        events.extend(("listing", "delisting"))
        days.extend((LISTED_ON, TERMINATED_ON))
    return _batch(
        STOCK_BASIC_DATASET,
        subjects=codes,
        columns=[
            PanelColumn("lifecycle_event", "string", tuple(events)),
            PanelColumn("lifecycle_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("exchange", "string", tuple(EXCHANGE for _ in codes)),
        ],
        event_time=[_midnight_shanghai(day) for day in days],
        available_time=[_midnight_shanghai(day) for day in days],
    )


def factor_batch(
    days: Sequence[date] = SESSIONS,
    securities: Sequence[str] = SECURITIES,
    *,
    omit: Sequence[tuple[str, date]] = (),
    fetched_at: datetime = FETCHED_AT,
) -> ColumnarPanelBatch:
    dropped = set(omit)
    pairs = [(day, code) for day in days for code in securities if (code, day) not in dropped]
    grid_days = tuple(day for day, _ in pairs)
    grid_codes = tuple(code for _, code in pairs)
    return _batch(
        ADJ_FACTOR_DATASET,
        subjects=grid_codes,
        columns=[
            PanelColumn("factor_date", "string", tuple(day.isoformat() for day in grid_days)),
            PanelColumn(
                "adj_factor",
                "float",
                tuple(_factor(code, day) for day, code in zip(grid_days, grid_codes, strict=True)),
            ),
        ],
        event_time=[_close_time(day) for day in grid_days],
        available_time=[_published_time(day) for day in grid_days],
        fetched_at=fetched_at,
    )


def bar_batch(
    days: Sequence[date] = SESSIONS,
    securities: Sequence[str] = SECURITIES,
    *,
    omit: Sequence[tuple[str, date]] = (),
    closes: Mapping[tuple[str, date], float] | None = None,
) -> ColumnarPanelBatch:
    dropped = set(omit)
    pairs = [(day, code) for day in days for code in securities if (code, day) not in dropped]
    grid_days = tuple(day for day, _ in pairs)
    grid_codes = tuple(code for _, code in pairs)
    close = tuple(
        (closes or {}).get((code, day), _close(code, day))
        for day, code in zip(grid_days, grid_codes, strict=True)
    )
    previous = tuple(_pre_close(code, day) for day, code in zip(grid_days, grid_codes, strict=True))
    return _batch(
        DAILY_DATASET,
        subjects=grid_codes,
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day in grid_days)),
            PanelColumn("open", "float", close),
            PanelColumn("high", "float", close),
            PanelColumn("low", "float", close),
            PanelColumn("close", "float", close),
            PanelColumn("pre_close", "float", previous),
            PanelColumn(
                "pct_chg",
                # Rounded to the four decimals Tushare publishes, so the stored number is the
                # provider's rather than one recomputed at full float precision.
                "float",
                tuple(
                    round((value / before - 1.0) * 100.0, 4)
                    for value, before in zip(close, previous, strict=True)
                ),
            ),
            PanelColumn("vol", "float", tuple(1000.0 for _ in close)),
            PanelColumn("amount", "float", tuple(10000.0 for _ in close)),
        ],
        event_time=[_close_time(day) for day in grid_days],
        available_time=[_published_time(day) for day in grid_days],
    )


def valuation_batch(
    days: Sequence[date] = SESSIONS,
    securities: Sequence[str] = SECURITIES,
    *,
    omit: Sequence[tuple[str, date]] = (),
    closes: Mapping[tuple[str, date], float] | None = None,
) -> ColumnarPanelBatch:
    dropped = set(omit)
    pairs = [(day, code) for day in days for code in securities if (code, day) not in dropped]
    grid_days = tuple(day for day, _ in pairs)
    grid_codes = tuple(code for _, code in pairs)
    close = tuple(
        (closes or {}).get((code, day), _close(code, day))
        for day, code in zip(grid_days, grid_codes, strict=True)
    )
    return _batch(
        DAILY_BASIC_DATASET,
        subjects=grid_codes,
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day in grid_days)),
            PanelColumn("close", "float", close),
            *(PanelColumn(name, "float", tuple(1.0 for _ in close)) for name in _DAILY_BASIC_EXTRA),
        ],
        event_time=[_close_time(day) for day in grid_days],
        available_time=[_published_time(day) for day in grid_days],
    )


def suspension_batch(
    halts: Sequence[tuple[str, date]] = ((HALTED_SECURITY, HALTED_SESSION),),
) -> ColumnarPanelBatch:
    return _batch(
        SUSPENSION_DATASET,
        subjects=[code for code, _ in halts],
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for _, day in halts)),
            PanelColumn("suspend_type", "string", tuple("S" for _ in halts)),
            PanelColumn("suspend_timing", "string", tuple(None for _ in halts)),
        ],
        event_time=[_close_time(day) for _, day in halts],
        available_time=[_published_time(day) for _, day in halts],
    )


def limit_batch(
    days: Sequence[date] = SESSIONS, securities: Sequence[str] = SECURITIES
) -> ColumnarPanelBatch:
    pairs = [(day, code) for day in days for code in securities]
    grid_days = tuple(day for day, _ in pairs)
    grid_codes = tuple(code for _, code in pairs)
    return _batch(
        PRICE_LIMIT_DATASET,
        subjects=grid_codes,
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day in grid_days)),
            PanelColumn(
                "up_limit",
                "float",
                tuple(
                    round(_pre_close(code, day) * 1.1, 2)
                    for day, code in zip(grid_days, grid_codes, strict=True)
                ),
            ),
            PanelColumn(
                "down_limit",
                "float",
                tuple(
                    round(_pre_close(code, day) * 0.9, 2)
                    for day, code in zip(grid_days, grid_codes, strict=True)
                ),
            ),
        ],
        event_time=[_close_time(day) for day in grid_days],
        available_time=[_published_time(day) for day in grid_days],
    )


def index_weight_batch(
    constituents: Sequence[str] = SECURITIES[:2], *, day: date = LAST_SESSION
) -> ColumnarPanelBatch:
    return _batch(
        INDEX_WEIGHT_DATASET,
        subjects=[INDEX_CODE] * len(constituents),
        columns=[
            PanelColumn("publication_date", "string", tuple(day.isoformat() for _ in constituents)),
            PanelColumn("con_code", "string", tuple(constituents)),
            PanelColumn("weight", "float", tuple(100.0 / len(constituents) for _ in constituents)),
        ],
        event_time=[_close_time(day)] * len(constituents),
        available_time=[_published_time(day)] * len(constituents),
    )


def _statement_batch(
    dataset: str,
    value_columns: Sequence[str],
    securities: Sequence[str],
    *,
    duplicate: Sequence[str],
    revised: Sequence[str],
) -> ColumnarPanelBatch:
    """One filing per security, plus a second irreconcilable version for each of `duplicate`.

    `revised` names the securities whose row carries a `revision_time` after its
    `available_time`, the only shape `PartitionCoverage.revised_row_count` can see.
    """
    codes = [*securities, *duplicate]
    flags = [*("1" for _ in securities), *("0" for _ in duplicate)]
    disagreeing, *rest = value_columns
    values = [
        *(100.0 + index for index, _ in enumerate(securities)),
        *(999.0 for _ in duplicate),
    ]
    announced = _midnight_shanghai(FILING_ANNOUNCED)
    revision = [
        announced + timedelta(days=1) if code in set(revised) else announced for code in codes
    ]
    return _batch(
        dataset,
        subjects=codes,
        columns=[
            PanelColumn("report_period", "string", tuple(REPORT_PERIOD.isoformat() for _ in codes)),
            PanelColumn("ann_date", "string", tuple(FILING_ANNOUNCED.isoformat() for _ in codes)),
            PanelColumn("f_ann_date", "string", tuple(FILING_ANNOUNCED.isoformat() for _ in codes)),
            PanelColumn("update_flag", "string", tuple(flags)),
            PanelColumn(disagreeing, "float", tuple(values)),
            *(PanelColumn(name, "float", tuple(1.0 for _ in codes)) for name in rest),
        ],
        event_time=[announced] * len(codes),
        available_time=[announced] * len(codes),
        revision_time=revision,
    )


def income_batch(
    securities: Sequence[str] = SECURITIES,
    *,
    duplicate: Sequence[str] = (),
    revised: Sequence[str] = (),
) -> ColumnarPanelBatch:
    return _statement_batch(
        INCOME_DATASET, INCOME_VALUE_COLUMNS, securities, duplicate=duplicate, revised=revised
    )


def cashflow_batch(
    securities: Sequence[str] = SECURITIES,
    *,
    duplicate: Sequence[str] = (),
    revised: Sequence[str] = (),
) -> ColumnarPanelBatch:
    return _statement_batch(
        CASHFLOW_DATASET, CASHFLOW_VALUE_COLUMNS, securities, duplicate=duplicate, revised=revised
    )


# --- the healthy panel ------------------------------------------------------------------------


def seed(
    tmp_path: Path,
    *,
    securities: Sequence[str] = SECURITIES,
    bars: ColumnarPanelBatch | None = None,
    valuations: ColumnarPanelBatch | None = None,
    factors: ColumnarPanelBatch | None = None,
    income: ColumnarPanelBatch | None = None,
    cashflow: ColumnarPanelBatch | None = None,
    universe: ColumnarPanelBatch | None = None,
) -> PanelStore:
    store = PanelStore(tmp_path / "panel")
    real = calendar()
    halted = ((HALTED_SECURITY, HALTED_SESSION),)
    write_trading_calendar(store, calendar_batch())
    write_stock_universe(store, universe or universe_batch(securities))
    write_adjustment_factors(store, [factors or factor_batch(securities=securities)], calendar=real)
    write_suspensions(store, [suspension_batch(halted)])
    write_daily_panel(
        store,
        bars=[bars or bar_batch(securities=securities, omit=halted)],
        fundamentals=[valuations or valuation_batch(securities=securities, omit=halted)],
        calendar=real,
        halts=load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None),
    )
    write_price_limits(store, [limit_batch(securities=securities)], calendar=real)
    write_index_weights(store, [index_weight_batch()])
    write_financial_statements(store, [income or income_batch(securities)])
    write_financial_statements(store, [cashflow or cashflow_batch(securities)])
    return store


def request_for(**overrides: object) -> DependencyRequest:
    arguments: dict[str, object] = {
        "datasets": DATASETS,
        "as_of": AS_OF,
        "years": (YEAR,),
        "sessions": (EX_RIGHTS_SESSION,),
        "calendar": calendar(),
        "index_codes": (INDEX_CODE,),
    }
    arguments.update(overrides)
    return DependencyRequest(**arguments)  # type: ignore[arg-type]


def _factor_history(store: PanelStore) -> AdjustmentHistory:
    """The factor history a downstream would build, with every bound waived.

    Deliberately not gated: this is the call a caller makes when it has *not* asked the gate,
    and its answer is what the gate exists to stop being acted on.
    """
    return load_adjustment_histories(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)[PING_AN]


def _cleared_names(clearance: DependencyClearance) -> tuple[str, ...]:
    """The dataset names off a clearance, for the assertions that are only about *which*.

    Spelled out at each call site rather than offered by the gate as an accessor: an accessor
    that answered "which datasets" without the scope would be the bare tuple `cleared` used to
    be, and the review found Task 29's wrong number reachable through exactly that shape.
    """
    return tuple(entry.dataset for entry in clearance.cleared)


# --- the clean case, and the empty answer that is not a refusal -------------------------------


def test_a_healthy_panel_clears_the_gate_and_the_downstream_read_returns_real_rows(
    tmp_path: Path,
) -> None:
    """The load-bearing one. Every other test refuses something; if a healthy panel could not
    be cleared, a gate that refused everything would pass them all."""
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for())

    assert clearance.blocks == ()
    assert _cleared_names(clearance) == DATASETS
    assert clearance.is_blocked is False
    bars = load_daily_bars(
        store,
        day=EX_RIGHTS_SESSION,
        calendar=calendar(),
        as_of=AS_OF,
        max_staleness=clearance.report.dataset(DAILY_DATASET).freshness.max_staleness,
    )
    assert sorted(bars) == sorted(SECURITIES)
    assert bars[PING_AN].close == 11.24


def test_a_registry_missing_its_delisted_half_clears_exactly_as_a_whole_one_does(
    tmp_path: Path,
) -> None:
    """`V2-P2-008`'s residue, stated as what the gate **cannot** see rather than pretended away.

    The delisted half of `stock_basic` staying in a historical universe is structural rather
    than special-cased -- `listed_on` is `listed_on <= day < delisted_on` over whatever rows
    are stored, so the delisted rows need no branch -- and the unit, integration and Parquet
    round trips all cover it. What none of them covers is the shape where the *fetch* asked for
    `list_status='L'` and the delisted rows were never stored at all, because that registry is
    not malformed: it is smaller, and it is self-consistent on every cross section.

    This is the assertion that says so. Both panels are put through the whole gate and the two
    verdicts are compared field by field -- cleared datasets, their year and session scope,
    their caveats, the blocks and the notices -- and they are **identical**. `SubjectContainment`
    cannot see it either: it asks that `daily`'s subjects be a subset of `stock_basic`'s, and
    dropping the delisted half shrinks the superset without breaking the containment.

    The second half is the cost, and it is why this is disclosure rather than a shrug: on a day
    inside the terminated name's own listing the two registries return different markets. At
    live scale that difference was measured at 227 securities on 2019-06-28, every one of them
    with a `delist_date` after that day, against a whole registry of 5,878. The defence is at
    fetch time (`providers/tushare.py::STOCK_BASIC_LIST_STATUS` is `'L,D'`), which is a request
    parameter no read-side check can audit. See
    `KNOWN_UNIVERSE_LIMITATIONS.a_listed_only_registry_is_invisible_to_every_downstream_check`.
    """
    listed_only = seed(tmp_path / "listed-only")
    whole = seed(tmp_path / "whole", universe=universe_batch(terminated=True))

    narrow = require_datasets(listed_only, request_for())
    wide = require_datasets(whole, request_for())

    assert narrow.blocks == () and wide.blocks == ()
    assert narrow.cleared == wide.cleared
    assert [notice.code for notice in narrow.notices] == [notice.code for notice in wide.notices]
    assert STOCK_BASIC_DATASET in _cleared_names(narrow)

    # And the two panels do not hold the same market.
    universes = {
        name: load_stock_universe(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)
        for name, store in (("narrow", listed_only), ("wide", whole))
    }
    inside_its_listing = date(2026, 1, 3)

    assert set(universes["wide"].listed_on(inside_its_listing)) - set(
        universes["narrow"].listed_on(inside_its_listing)
    ) == {TERMINATED_SECURITY}
    # After the termination the two agree again, which is why no cross section can catch it.
    assert universes["wide"].listed_on(LAST_SESSION) == universes["narrow"].listed_on(LAST_SESSION)


def test_the_session_scoped_cross_checks_this_gate_leans_on_all_ran(tmp_path: Path) -> None:
    """`SESSION_SCOPED_CROSS_CHECKS` names three `panel_doctor` checks by string. A rename
    upstream would silently turn the corroboration rule into "nothing ever corroborates",
    which blocks everything, or -- if the rule were written the other way round -- into
    "everything corroborates", which blocks nothing. Pinned against a real report."""
    clearance = require_datasets(seed(tmp_path), request_for())

    ran = {check.name for check in clearance.report.cross_checks if check.ran}

    assert ran >= SESSION_SCOPED_CROSS_CHECKS


def test_a_cleared_gate_still_lets_a_genuinely_empty_answer_through(tmp_path: Path) -> None:
    """ "Ready but nothing matched" must stay reachable and must stay distinguishable from
    "blocked". `suspend_d` holds one halt, on a session that is not the one asked about, so the
    honest answer for the asked-about session is *no halts* -- and that is an empty answer the
    caller may act on, unlike the one in the next test."""
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for(sessions=(LAST_SESSION,)))
    halts = load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)

    assert clearance.is_blocked is False
    assert HALTED_SESSION in halts
    assert LAST_SESSION not in halts


def test_the_same_empty_answer_becomes_a_refusal_once_the_partition_is_gone(
    tmp_path: Path,
) -> None:
    """The pair of the test above, and the whole acceptance in one place: with `suspend_d`'s
    file deleted the truth is "unknown", and neither the gate nor the loader is willing to
    express that as "no halts"."""
    store = seed(tmp_path)
    next((store.root / SUSPENSION_DATASET / str(YEAR)).glob("*.parquet")).unlink()

    clearance = require_datasets(store, request_for(sessions=(LAST_SESSION,)))

    assert clearance.is_blocked is True
    assert "partition_file_missing" in clearance.blocking_codes()
    assert [block.dataset for block in clearance.blocks_with_code("partition_file_missing")] == [
        SUSPENSION_DATASET
    ]
    with pytest.raises(PanelStorageError, match=r"suspend_d"):
        load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)


# --- Task 29's scenario: the factor step that is not there -------------------------------------


def test_the_healthy_panel_answers_the_ex_rights_session_with_the_adjusted_return(
    tmp_path: Path,
) -> None:
    """The number the gate is protecting. `000001.SZ` fell 11.30 -> 11.24 across a session it
    paid a dividend on, and the correct return is **+2.742251%**, not the -0.53% the two closes
    alone say."""
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for())
    history = _factor_history(store)

    assert clearance.is_blocked is False
    assert history.adjusted_return(
        start=PRE_EX_SESSION, end=EX_RIGHTS_SESSION, start_price=11.30, end_price=11.24
    ) == pytest.approx(ADJUSTED_RETURN, abs=1e-8)


def test_a_missing_factor_session_blocks_instead_of_answering_minus_zero_point_53_percent(
    tmp_path: Path,
) -> None:
    """Task 29's Critical, reproduced and then refused.

    The `adj_factor` row for the ex-rights session is removed. Readiness still says **ready**
    with no issues -- `adjustment_requirement` waives `required_dates`, so there is no session
    census for the hole to show up in -- and the loader loads. What the gate sees instead is
    `return_path_disagreement`: the published `pre_close` of 10.94 and the 11.30 the surviving
    factors imply cannot both be right, a gap of 0.36 against a tolerance of about 0.010.

    The counterfactual is asserted alongside, because "the gate blocks" is only interesting
    next to what the ungated call answers: **-0.530973%**, wrong by 3.3 points with the sign
    reversed.
    """
    store = seed(tmp_path)
    write_panel_batch(store, factor_batch(omit=((PING_AN, EX_RIGHTS_SESSION),)), year=YEAR)

    clearance = require_datasets(store, request_for())

    assert clearance.blocking_codes() == frozenset({"return_path_disagreement"})
    (block,) = clearance.blocks_with_code("return_path_disagreement")
    assert block.datasets == (DAILY_DATASET, ADJ_FACTOR_DATASET)
    assert PING_AN in block.detail
    assert clearance.report.dataset(ADJ_FACTOR_DATASET).readiness.state == "ready"
    with pytest.raises(PanelGateError, match=r"blocked by \['return_path_disagreement'\]"):
        _ = clearance.cleared

    ungated = _factor_history(store).adjusted_return(
        start=PRE_EX_SESSION, end=EX_RIGHTS_SESSION, start_price=11.30, end_price=11.24
    )
    assert ungated == pytest.approx(UNADJUSTED_RETURN, abs=1e-12)
    assert round(ungated * 100, 6) == -0.530973


def test_the_gated_read_of_the_same_session_refuses_rather_than_answering_either_number(
    tmp_path: Path,
) -> None:
    """The other half of the refusal: `session_returns` is the call a downstream that *does*
    cross-check makes, and on the holed panel it raises rather than returning the wrong
    number. The gate and the read agree; neither invents a value."""
    store = seed(tmp_path)
    write_panel_batch(store, factor_batch(omit=((PING_AN, EX_RIGHTS_SESSION),)), year=YEAR)
    bars = load_daily_bars(
        store, day=EX_RIGHTS_SESSION, calendar=calendar(), as_of=AS_OF, max_staleness=None
    )

    with pytest.raises(ValueError, match=r"two datasets disagree about that session"):
        session_returns(
            bars[PING_AN],
            previous_close=11.30,
            previous_day=PRE_EX_SESSION,
            factors=_factor_history(store),
        )


def test_a_factor_hole_on_a_session_the_factor_did_not_step_on_changes_no_answer(
    tmp_path: Path,
) -> None:
    """The residue, named rather than left to be discovered.

    `000002.SZ`'s factor is flat across the whole window, so removing one of its rows removes
    no information: `factor_on` answers the same number by carrying the previous step forward,
    which is what the step function *means*. The gate clears, and it is right to -- the
    adjusted return is bit-for-bit what the intact panel answers. A hole that changes an answer
    is a hole across a step, and that is the one the previous test refuses.
    """
    store = seed(tmp_path)
    intact = _factor_history(store).adjusted_return(
        start=PRE_EX_SESSION, end=EX_RIGHTS_SESSION, start_price=11.30, end_price=11.24
    )
    write_panel_batch(store, factor_batch(omit=((VANKE, SESSIONS[3]),)), year=YEAR)

    clearance = require_datasets(store, request_for())

    assert clearance.is_blocked is False
    assert _factor_history(store).adjusted_return(
        start=PRE_EX_SESSION, end=EX_RIGHTS_SESSION, start_price=11.30, end_price=11.24
    ) == pytest.approx(intact, abs=0.0)


# --- the corroboration rule: a request the gate cannot answer -----------------------------------


def test_asking_only_for_the_factors_is_refused_because_nothing_could_check_them(
    tmp_path: Path,
) -> None:
    """`adj_factor` is the one dataset that is on the `daily` cadence and waives
    `required_dates`, so a request naming it alone gets a verdict built from registration,
    files, fields and freshness -- every one of which the panel in the Task 29 test passed
    while carrying a hole. The gate says so instead of clearing."""
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for(datasets=(ADJ_FACTOR_DATASET,)))

    assert clearance.blocking_codes() == frozenset({UNVERIFIED_DAILY_COVERAGE})
    (block,) = clearance.blocks_with_code(UNVERIFIED_DAILY_COVERAGE)
    assert block.dataset == ADJ_FACTOR_DATASET
    assert "required_dates" in block.detail


def test_naming_the_price_panel_but_no_session_is_refused_for_the_same_reason(
    tmp_path: Path,
) -> None:
    """The cross-check that would corroborate the factors needs a session to run on, and
    `panel_health_report` does not invent one. A request that names no session therefore gets
    the same refusal -- the promise is scoped to sessions the caller names."""
    store = seed(tmp_path)

    clearance = require_datasets(
        store, request_for(datasets=(DAILY_DATASET, ADJ_FACTOR_DATASET), sessions=())
    )

    assert UNVERIFIED_DAILY_COVERAGE in clearance.blocking_codes()


def test_naming_the_price_panel_and_a_session_clears_the_same_two_datasets(
    tmp_path: Path,
) -> None:
    """The pair of the two above: nothing about `adj_factor` changed, the request did."""
    store = seed(tmp_path)

    clearance = require_datasets(
        store,
        request_for(datasets=(DAILY_DATASET, ADJ_FACTOR_DATASET), sessions=(EX_RIGHTS_SESSION,)),
    )

    assert _cleared_names(clearance) == (DAILY_DATASET, ADJ_FACTOR_DATASET)


def test_exactly_one_declared_dataset_is_daily_and_waives_its_date_check(tmp_path: Path) -> None:
    """The measurement the corroboration rule is built on, driven end to end rather than read
    off the requirement builders' docstrings.

    The rule is scoped to "daily cadence **and** `required_dates` waived" on the strength of
    that set having exactly one member today. Nothing was stopping it from acquiring a second:
    a new upstream dataset in the same shape would either be blocked forever, because none of
    the three cross-checks names it and so nothing can ever corroborate it, or -- worse -- be
    corroborated by accident, because `unpriced_explained` lists `stock_basic` and `suspend_d`
    among its datasets while checking neither one's session census. Both outcomes were silent;
    this assertion is what makes the second dataset arrive as a red test.
    """
    report = panel_health_report(
        seed(tmp_path),
        as_of=AS_OF,
        datasets=tuple(DATASET_CADENCE),
        years=(YEAR,),
        calendar=calendar(),
        index_codes=(INDEX_CODE,),
    )

    waiving = {
        health.dataset
        for health in report.datasets
        if "required_dates" in health.readiness.checks_waived
    }

    assert len(report.datasets) == 15
    assert len(waiving) == 12
    assert {
        health.dataset
        for health in report.datasets
        if health.freshness.cadence == "daily"
        and "required_dates" in health.readiness.checks_waived
    } == {ADJ_FACTOR_DATASET}


# --- the width of the clearance: what a cleared gate does *not* say ------------------------------


def test_a_session_after_the_factor_hole_clears_and_the_clearance_says_how_narrowly(
    tmp_path: Path,
) -> None:
    """The `V2-P1-013` review's Critical, reproduced and then made visible.

    The same hole as the Task 29 test above -- `000001.SZ`'s `adj_factor` row for the ex-rights
    session, written through `write_adjustment_factors` with the calendar, which accepts one
    security's missing row on a session the others still carry. Naming the ex-rights session
    blocks. Naming `2026-01-15`, two sessions later, **clears**, with `report.is_clean` and no
    notice, because `return_paths` recomputes the sessions it is given and nothing else.

    That much is unavoidable from the read side: `panel_ingest` records that this residue
    "cannot be closed from the read side", and the gate's own import set puts the session
    census out of reach. What was avoidable is `cleared` handing back a bare `'adj_factor'`
    granted over 2026, which reads as a whole year. It now carries the one session that was
    corroborated, the code still open outside it, and a `corroborates` that answers `False` for
    the two sessions the wrong number is computed across.
    """
    store = seed(tmp_path, factors=factor_batch(omit=((PING_AN, EX_RIGHTS_SESSION),)))
    unnamed = SESSIONS[8]

    clearance = require_datasets(store, request_for(sessions=(unnamed,)))
    factors = clearance.cleared_for(ADJ_FACTOR_DATASET)

    assert clearance.is_blocked is False
    assert factors.corroborated_sessions == (unnamed,)
    assert factors.caveats == (UNVERIFIED_DAILY_COVERAGE,)
    assert factors.years == (YEAR,)
    assert factors.corroborates(unnamed) is True
    assert factors.corroborates(PRE_EX_SESSION) is False
    assert factors.corroborates(EX_RIGHTS_SESSION) is False
    assert clearance.caveat_codes() == frozenset({UNVERIFIED_DAILY_COVERAGE})
    assert [
        entry.dataset for entry in clearance.cleared_with_caveat(UNVERIFIED_DAILY_COVERAGE)
    ] == [ADJ_FACTOR_DATASET]

    ungated = _factor_history(store).adjusted_return(
        start=PRE_EX_SESSION, end=EX_RIGHTS_SESSION, start_price=11.30, end_price=11.24
    )
    assert round(ungated * 100, 6) == -0.530973


def test_naming_the_session_after_the_hole_still_blocks_and_the_window_is_two_sessions_wide(
    tmp_path: Path,
) -> None:
    """The pair, and the measurement of how narrow the escape window is. The hole is at index
    6; `return_paths` compares each named session against its predecessor, so naming 6 or 7
    blocks and every later session clears. Pinned so that a change to the rule shows up as a
    change to *which* sessions block rather than as prose about coverage."""
    store = seed(tmp_path, factors=factor_batch(omit=((PING_AN, EX_RIGHTS_SESSION),)))

    verdicts = {
        index: require_datasets(store, request_for(sessions=(SESSIONS[index],))).is_blocked
        for index in (5, 6, 7, 8, 9)
    }

    assert verdicts == {5: False, 6: True, 7: True, 8: False, 9: False}


def test_a_close_disagreement_on_an_unnamed_session_clears_and_the_record_says_it_was_unread(
    tmp_path: Path,
) -> None:
    """The review's "not only `adj_factor`" case. All three session-scoped checks run on
    `cross_section_days`, so a `daily_basic` close `daily` contradicts on `2026-01-15` is
    invisible to a request that named `2026-01-13` -- and `daily`'s own date census, which
    *does* cover the year, says nothing about it. The record separates the two: the year for
    the census, the corroborated sessions for the cross-check."""
    store = seed(tmp_path)
    unnamed = SESSIONS[8]
    write_panel_batch(
        store,
        valuation_batch(
            omit=((HALTED_SECURITY, HALTED_SESSION),), closes={(PING_AN, unnamed): 99.0}
        ),
        year=YEAR,
    )

    cleared = require_datasets(store, request_for(sessions=(EX_RIGHTS_SESSION,)))
    refused = require_datasets(store, request_for(sessions=(unnamed,)))
    prices = cleared.cleared_for(DAILY_BASIC_DATASET)

    assert cleared.is_blocked is False
    assert prices.years == (YEAR,)
    assert prices.corroborated_sessions == (EX_RIGHTS_SESSION,)
    assert prices.corroborates(unnamed) is False
    assert refused.blocking_codes() == frozenset({"close_disagreement"})


def test_an_unexplained_unpriced_name_on_an_unnamed_session_clears_the_same_way(
    tmp_path: Path,
) -> None:
    """The third of the same shape: `000002.SZ` loses its bar on `2026-01-15` with no halt to
    account for it, and a request naming `2026-01-13` clears. `suspend_d` is one of the
    datasets `unpriced_explained` reads, so its record carries the sessions too."""
    store = seed(tmp_path)
    unnamed = SESSIONS[8]
    gone = ((HALTED_SECURITY, HALTED_SESSION), (VANKE, unnamed))
    write_panel_batch(store, bar_batch(omit=gone), year=YEAR)
    write_panel_batch(store, valuation_batch(omit=gone), year=YEAR)

    cleared = require_datasets(store, request_for(sessions=(EX_RIGHTS_SESSION,)))
    refused = require_datasets(store, request_for(sessions=(unnamed,)))

    assert cleared.is_blocked is False
    assert cleared.cleared_for(SUSPENSION_DATASET).corroborated_sessions == (EX_RIGHTS_SESSION,)
    assert cleared.cleared_for(SUSPENSION_DATASET).corroborates(unnamed) is False
    assert refused.blocking_codes() == frozenset({"unexplained_unpriced"})


def test_a_dataset_no_session_scoped_check_reads_records_no_session_at_all(
    tmp_path: Path,
) -> None:
    """The other direction, so that "every dataset carries the named sessions" cannot pass for
    this rule. `income` is read by `statement_ambiguity`, which opens no session, so its record
    carries the years and an empty session tuple -- and `corroborates` answers `False` for the
    very session the request named, because no cross-dataset check looked at `income` on it."""
    clearance = require_datasets(seed(tmp_path), request_for())

    statements = clearance.cleared_for(INCOME_DATASET)

    assert statements.years == (YEAR,)
    assert statements.corroborated_sessions == ()
    assert statements.caveats == ()
    assert statements.corroborates(EX_RIGHTS_SESSION) is False


def test_both_sides_of_a_cross_dataset_block_are_reachable_from_the_clearance(
    tmp_path: Path,
) -> None:
    """`GateBlock.dataset` is the name the finding is *filed* under, and a caller polling
    dataset by dataset was told `()` for the other side.

    Two real injections, each with the block filed under `daily`: a `close_disagreement` whose
    wrong close is `daily_basic`'s, and a `subject_set_disagreement` whose short subject list is
    `adj_factor`'s. Both used to answer "nothing wrong there" for the dataset that actually
    carried the defect.
    """
    store = seed(tmp_path)
    write_panel_batch(
        store,
        valuation_batch(
            omit=((HALTED_SECURITY, HALTED_SESSION),), closes={(PING_AN, EX_RIGHTS_SESSION): 99.0}
        ),
        year=YEAR,
    )

    disagreement = require_datasets(store, request_for())
    containment = require_datasets(
        seed(tmp_path / "second", factors=factor_batch(securities=SECURITIES[:2])), request_for()
    )

    assert disagreement.blocked_datasets == (DAILY_DATASET, DAILY_BASIC_DATASET)
    assert [block.code for block in disagreement.blocks_for(DAILY_BASIC_DATASET)] == [
        "close_disagreement"
    ]
    assert ADJ_FACTOR_DATASET in containment.blocked_datasets
    assert {block.code for block in containment.blocks_for(ADJ_FACTOR_DATASET)} == {
        "subject_set_disagreement",
        "check_unavailable",
    }
    assert containment.blocks_for(INCOME_DATASET) == ()


# --- injections that must block, each paired with the downstream that must not succeed ---------


def test_a_year_that_was_never_ingested_blocks_every_dataset_it_was_asked_of(
    tmp_path: Path,
) -> None:
    """The request asserts what *should* be there over two years and one of them was never
    ingested, so every dataset is refused. The paired downstream read is the one that spans
    the same two years -- `load_adjustment_histories`, which refuses rather than answering the
    one year it found, because a factor history with a year missing from the middle is not a
    shorter answer but a wrong one.
    """
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for(years=(YEAR - 1, YEAR)))

    assert "partition_missing" in clearance.blocking_codes()
    assert set(clearance.blocked_datasets) == set(DATASETS)
    with pytest.raises(PanelStorageError, match=r"partition_missing"):
        load_adjustment_histories(store, years=(YEAR - 1, YEAR), as_of=AS_OF, max_staleness=None)


def test_a_deleted_parquet_file_blocks_and_the_loader_refuses_rather_than_answering_nothing(
    tmp_path: Path,
) -> None:
    store = seed(tmp_path)
    next((store.root / DAILY_DATASET / str(YEAR)).glob("*.parquet")).unlink()

    clearance = require_datasets(store, request_for())

    assert "partition_file_missing" in clearance.blocking_codes()
    with pytest.raises(PanelStorageError, match=r"partition_file_missing"):
        load_daily_bars(
            store, day=EX_RIGHTS_SESSION, calendar=calendar(), as_of=AS_OF, max_staleness=None
        )


def test_a_truncated_parquet_file_blocks_separately_from_a_deleted_one(tmp_path: Path) -> None:
    """A present file that is not a Parquet file. The catalog still advertises the partition,
    so a gate that only checked registration would clear it."""
    store = seed(tmp_path)
    next((store.root / INDEX_WEIGHT_DATASET / str(YEAR)).glob("*.parquet")).write_bytes(b"PAR")

    clearance = require_datasets(store, request_for())

    assert "partition_file_unreadable" in clearance.blocking_codes()
    assert [block.dataset for block in clearance.blocks_with_code("partition_file_unreadable")] == [
        INDEX_WEIGHT_DATASET
    ]


def test_a_request_naming_no_year_blocks_rather_than_finding_nothing_to_complain_about(
    tmp_path: Path,
) -> None:
    """`evaluate_readiness`'s fail-closed rule arriving at the gate. With no year in scope
    there is nothing to inspect, so every dataset reports `no_years_requested` -- and a gate
    that cleared here would be reporting the absence of a question as the absence of a
    problem, which is the empty success in its purest form."""
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for(years=()))

    assert "no_years_requested" in clearance.blocking_codes()
    assert set(clearance.blocked_datasets) == set(DATASETS)


def test_a_session_missing_from_the_price_panel_blocks(tmp_path: Path) -> None:
    """`daily` is one of the three datasets that *is* asked which sessions it should hold, so
    this one is a `date_gap` rather than the invisible hole `adj_factor` carries."""
    store = seed(tmp_path)
    kept = tuple(day for day in SESSIONS if day != SESSIONS[3])
    write_panel_batch(
        store, bar_batch(days=kept, omit=((HALTED_SECURITY, HALTED_SESSION),)), year=YEAR
    )

    clearance = require_datasets(store, request_for())

    assert "date_gap" in clearance.blocking_codes()
    (block,) = clearance.blocks_with_code("date_gap")
    assert block.finding is not None
    assert block.finding.dates == (SESSIONS[3],)


def test_a_dataset_that_has_fallen_behind_its_own_cadence_blocks(tmp_path: Path) -> None:
    store = seed(tmp_path, factors=factor_batch(days=SESSIONS[:3], fetched_at=STALE_FETCHED_AT))

    clearance = require_datasets(store, request_for())

    assert "stale" in clearance.blocking_codes()
    assert ADJ_FACTOR_DATASET in clearance.blocked_datasets


def test_the_same_absolute_age_does_not_block_a_dataset_that_publishes_quarterly(
    tmp_path: Path,
) -> None:
    """The roadmap says "failed *daily* datasets block downstream", and this is where the word
    daily does its work: not by scoping which failures block, but by scoping what counts as a
    failure. The filings are older than the factor series and clear, because a quarterly
    dataset is allowed to be 182 days behind and a daily one is allowed about four.
    """
    store = seed(tmp_path, factors=factor_batch(days=SESSIONS[:3], fetched_at=STALE_FETCHED_AT))

    clearance = require_datasets(store, request_for())

    assert [block.dataset for block in clearance.blocks_with_code("stale")] == [ADJ_FACTOR_DATASET]
    factors = clearance.report.dataset(ADJ_FACTOR_DATASET)
    filings = clearance.report.dataset(INCOME_DATASET)
    assert factors.event_age is not None and filings.event_age is not None
    assert filings.event_age > factors.event_age


def test_a_coverage_record_that_no_longer_describes_its_partition_blocks(tmp_path: Path) -> None:
    store = seed(tmp_path)
    store.write_partition(
        PRICE_LIMIT_DATASET,
        year=YEAR,
        columns=(
            ColumnSpec("subject", "VARCHAR"),
            ColumnSpec("trade_date", "VARCHAR"),
            ColumnSpec("up_limit", "DOUBLE"),
            ColumnSpec("down_limit", "DOUBLE"),
        ),
        rows=[(PING_AN, LAST_SESSION.isoformat(), 11.0, 9.0)],
    )

    clearance = require_datasets(store, request_for())

    assert "coverage_stale" in clearance.blocking_codes()


def test_two_datasets_that_republish_the_same_close_and_disagree_block(tmp_path: Path) -> None:
    store = seed(tmp_path)
    write_panel_batch(
        store,
        valuation_batch(
            omit=((HALTED_SECURITY, HALTED_SESSION),),
            closes={(PING_AN, EX_RIGHTS_SESSION): 99.0},
        ),
        year=YEAR,
    )

    clearance = require_datasets(store, request_for())

    assert "close_disagreement" in clearance.blocking_codes()
    assert clearance.report.dataset(DAILY_BASIC_DATASET).readiness.state == "ready"


def test_a_listed_security_with_no_bar_and_no_halt_blocks(tmp_path: Path) -> None:
    """Task 30's shape: the cross section silently loses a name. Every dataset is `ready`; the
    only thing that sees it is a cross-dataset warning, and the gate blocks on warnings."""
    store = seed(tmp_path)
    gone = ((HALTED_SECURITY, HALTED_SESSION), (VANKE, EX_RIGHTS_SESSION))
    write_panel_batch(store, bar_batch(omit=gone), year=YEAR)
    write_panel_batch(store, valuation_batch(omit=gone), year=YEAR)

    clearance = require_datasets(store, request_for())

    assert "unexplained_unpriced" in clearance.blocking_codes()
    assert clearance.report.blocked_datasets == ()


def test_a_priced_security_with_no_adjustment_factor_blocks(tmp_path: Path) -> None:
    """Also every-dataset-`ready`: `adjustment_requirement` waives `required_subjects`, so a
    factor corpus narrower than the price panel is invisible to readiness and visible only to
    the containment check."""
    store = seed(tmp_path, factors=factor_batch(securities=SECURITIES[:2]))

    clearance = require_datasets(store, request_for())

    assert "subject_set_disagreement" in clearance.blocking_codes()
    (block,) = clearance.blocks_with_code("subject_set_disagreement")
    assert block.finding is not None
    assert block.finding.items == (SPDB,)


def test_a_request_with_no_calendar_blocks_because_the_report_could_not_look(
    tmp_path: Path,
) -> None:
    """ "I could not check" is not "I checked and it was fine". Without a calendar the sessions
    the price datasets should hold cannot be stated at all, so hole detection does not run --
    and the gate refuses rather than clearing on the checks that remain."""
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for(calendar=None))

    assert "check_unavailable" in clearance.blocking_codes()
    assert UNVERIFIED_DAILY_COVERAGE in clearance.blocking_codes()


def test_a_panel_read_before_its_rows_became_knowable_blocks(tmp_path: Path) -> None:
    """The point-in-time direction: the same store is cleared at one `as_of` and refused at an
    earlier one, with no change on disk."""
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for(as_of=datetime(2026, 1, 10, 4, 0, tzinfo=UTC)))

    assert "not_yet_knowable" in clearance.blocking_codes()


def test_an_index_the_partition_does_not_carry_blocks(tmp_path: Path) -> None:
    store = seed(tmp_path)

    clearance = require_datasets(store, request_for(index_codes=(INDEX_CODE, "000905.SH")))

    assert "subject_missing" in clearance.blocking_codes()
    assert INDEX_WEIGHT_DATASET in clearance.blocked_datasets


def test_a_dataset_can_be_asked_about_its_own_years_and_blocks_on_them_alone(
    tmp_path: Path,
) -> None:
    """A caller backfilling one dataset states the years *it* should now hold without
    re-asserting them for the other nine, and the refusal lands on that dataset only."""
    store = seed(tmp_path)

    clearance = require_datasets(
        store, request_for(years_by_dataset={ADJ_FACTOR_DATASET: (YEAR - 1, YEAR)})
    )

    assert [block.dataset for block in clearance.blocks_with_code("partition_missing")] == [
        ADJ_FACTOR_DATASET
    ]
    assert clearance.report.dataset(DAILY_DATASET).years_requested == (YEAR,)


def test_a_caller_supplied_freshness_bound_reaches_the_gate_s_verdict(tmp_path: Path) -> None:
    """The derived daily bound is this calendar's longest closure plus a day, which a panel a
    few hours old is comfortably inside. A caller that needs a tighter one states it, and the
    gate refuses on the caller's number rather than on the derived one."""
    store = seed(tmp_path)

    clearance = require_datasets(
        store, request_for(freshness_overrides={DAILY_DATASET: timedelta(hours=1)})
    )

    assert [block.dataset for block in clearance.blocks_with_code("stale")] == [DAILY_DATASET]
    assert clearance.report.dataset(DAILY_DATASET).freshness.basis.startswith("stated by the")


# --- what does not block ------------------------------------------------------------------------


def test_a_panel_whose_only_findings_are_notices_still_clears(tmp_path: Path) -> None:
    """The other direction of the same table, and the one that decides whether this gate is
    usable at all. `V2-P1-011` measured 81.7% of `fina_indicator`'s keys carrying more than one
    row; a gate that blocked on duplication would refuse every real financial panel, be
    switched off, and protect nothing. The notices are carried on the clearance instead, and
    the read of a disagreeing field is refused where that is decidable -- in
    `financial_ambiguity_report`, not here.
    """
    store = seed(tmp_path, income=income_batch(duplicate=(PING_AN,), revised=(VANKE,)))

    clearance = require_datasets(store, request_for())

    assert clearance.is_blocked is False
    assert _cleared_names(clearance) == DATASETS
    assert {notice.code for notice in clearance.notices} == {
        "ambiguous_filing",
        "duplicate_versions",
        "revised_rows",
    }


def test_a_blocked_clearance_keeps_its_notices_and_its_blocks_apart(tmp_path: Path) -> None:
    """The same panel plus one real defect. `notices` must stay the three ordinary facts and
    must not quietly acquire the finding that did the blocking -- a caller shown a `date_gap`
    under the heading "nothing to worry about" is worse off than one shown nothing.
    """
    store = seed(tmp_path, income=income_batch(duplicate=(PING_AN,), revised=(VANKE,)))
    kept = tuple(day for day in SESSIONS if day != SESSIONS[3])
    write_panel_batch(
        store, bar_batch(days=kept, omit=((HALTED_SECURITY, HALTED_SESSION),)), year=YEAR
    )

    clearance = require_datasets(store, request_for())

    assert "date_gap" in clearance.blocking_codes()
    assert {notice.code for notice in clearance.notices} == {
        "ambiguous_filing",
        "duplicate_versions",
        "revised_rows",
    }
    assert not (clearance.blocking_codes() & {notice.code for notice in clearance.notices})


def test_a_hole_in_the_price_panel_takes_every_session_scoped_cross_check_down_with_it(
    tmp_path: Path,
) -> None:
    """Worth pinning because it is how the two halves of this gate interact. A `date_gap` in
    `daily` blocks `daily` -- and every cross-check that has to load `daily` then reports that
    it could not run, which leaves `adj_factor` with nothing to corroborate its own waived date
    check. One injected defect, three separate reasons to refuse, none of them a silence.
    """
    store = seed(tmp_path)
    kept = tuple(day for day in SESSIONS if day != SESSIONS[3])
    write_panel_batch(
        store, bar_batch(days=kept, omit=((HALTED_SECURITY, HALTED_SESSION),)), year=YEAR
    )

    clearance = require_datasets(store, request_for())

    assert clearance.blocking_codes() == frozenset(
        {"date_gap", "check_unavailable", UNVERIFIED_DAILY_COVERAGE}
    )
    assert not any(
        check.ran
        for check in clearance.report.cross_checks
        if check.name in SESSION_SCOPED_CROSS_CHECKS
    )


def test_a_cleared_clearance_still_names_the_checks_that_never_ran(tmp_path: Path) -> None:
    """Cleared is not "everything was checked". Twelve of the fifteen datasets waive
    `required_dates` structurally, and the clearance says which -- measured here rather than
    asserted from the requirement builders' docstrings."""
    clearance = require_datasets(seed(tmp_path), request_for())

    waived_dates = {
        dataset for dataset, checks in clearance.unverified_checks if "required_dates" in checks
    }

    assert clearance.is_blocked is False
    assert waived_dates == {
        TRADING_CALENDAR_DATASET,
        STOCK_BASIC_DATASET,
        ADJ_FACTOR_DATASET,
        SUSPENSION_DATASET,
        INDEX_WEIGHT_DATASET,
        INCOME_DATASET,
        CASHFLOW_DATASET,
    }
    assert clearance.unverified(DAILY_DATASET) == ("required_subjects",)


# --- the shape of the refusal --------------------------------------------------------------------


def test_the_ways_people_actually_write_this_all_raise_on_a_real_blocked_clearance(
    tmp_path: Path,
) -> None:
    """`PanelReadOutcome`'s lesson, applied one layer up and against a real refusal rather than
    a hand-built one: two values are only half a fix, because `if not x:` and `x or []` merge
    them again at run time while type-checking clean."""
    store = seed(tmp_path)
    next((store.root / DAILY_DATASET / str(YEAR)).glob("*.parquet")).unlink()
    clearance = require_datasets(store, request_for())

    with pytest.raises(PanelGateError, match=r"not a collection"):
        _ = not clearance
    with pytest.raises(PanelGateError, match=r"not a collection"):
        _ = clearance or []
    with pytest.raises(PanelGateError, match=r"not a collection"):
        _ = len(clearance)
    with pytest.raises(PanelGateError, match=r"blocked by"):
        _ = clearance.cleared
    assert clearance.cleared_or_none is None


def test_a_year_that_has_begun_but_published_no_session_blocks_with_an_empty_requirement(
    tmp_path: Path,
) -> None:
    """`empty_requirement` reached through `panel_health_report`, which an earlier note here
    called impossible.

    It is not: at 17:00 Asia/Shanghai on 2 January 2026 the year has begun and neither of its
    first two days was a session, so `_price_requirement` states `required_dates=()` -- a
    declared-but-empty expectation, which `evaluate_readiness` blocks on because an empty set
    difference is empty and would otherwise report `ready` for a partition holding nothing.
    This happens on the real calendar every January, and the gate must refuse rather than
    clearing a dataset whose date check was configured to look at no day at all.
    """
    store = seed(tmp_path)

    clearance = require_datasets(
        store,
        request_for(
            datasets=(DAILY_DATASET,), as_of=datetime(2026, 1, 2, 9, 0, tzinfo=UTC), sessions=()
        ),
    )

    assert "empty_requirement" in clearance.blocking_codes()
    assert [block.dataset for block in clearance.blocks_with_code("empty_requirement")] == [
        DAILY_DATASET
    ]
    with pytest.raises(PanelGateError, match=r"blocked by \[.*'empty_requirement'"):
        _ = clearance.cleared


def test_the_date_timezone_this_gate_pins_changes_the_verdict_rather_than_nothing(
    tmp_path: Path,
) -> None:
    """Why `date_timezone` is fixed at `DEFAULT_DATE_TIMEZONE` instead of being a request field.

    Not because no input can tell the difference -- one store, one `as_of`, two zones, two
    different verdicts. At 17:00 Asia/Shanghai on 2026-01-16 that session has published and its
    absence is a `date_gap`; read as UTC the same instant is 09:00, before the 16:30
    availability time, so the session is not required and the report is clean. Exposing the
    knob would let a request judge a partition against a session boundary the partition was
    never written to, which is the one-day disagreement `PartitionCoverage.date_timezone`
    exists to record.
    """
    store = seed(tmp_path)
    write_panel_batch(
        store,
        bar_batch(days=SESSIONS[:-1], omit=((HALTED_SECURITY, HALTED_SESSION),)),
        year=YEAR,
    )
    argument: dict[str, object] = {
        "as_of": datetime(2026, 1, 16, 9, 0, tzinfo=UTC),
        "datasets": (DAILY_DATASET,),
        "years": (YEAR,),
        "calendar": calendar(),
    }

    shanghai = panel_health_report(store, date_timezone="Asia/Shanghai", **argument)  # type: ignore[arg-type]
    utc = panel_health_report(store, date_timezone="UTC", **argument)  # type: ignore[arg-type]

    assert sorted(shanghai.codes()) == ["date_gap"]
    assert shanghai.is_clean is False
    assert utc.codes() == frozenset()
    assert utc.is_clean is True


def test_a_per_dataset_override_naming_a_dataset_outside_the_request_is_a_usage_error(
    tmp_path: Path,
) -> None:
    """Both mappings are resolved against the datasets the report was handed, so a key outside
    them is dropped rather than applied. A caller narrowing `fina_indicator`'s backfill window
    while asking about the price panel got no feedback at all and a verdict built on the
    request's defaults."""
    store = seed(tmp_path)

    with pytest.raises(PanelGateError, match=r"years_by_dataset names \['fina_indicator'\]"):
        require_datasets(store, request_for(years_by_dataset={"fina_indicator": (1999,)}))
    with pytest.raises(PanelGateError, match=r"freshness_overrides names \['namechange'\]"):
        require_datasets(store, request_for(freshness_overrides={"namechange": None}))


def test_a_gate_asked_about_no_dataset_at_all_is_a_usage_error(tmp_path: Path) -> None:
    """`no_years_requested`'s rule one layer up: a gate that inspected nothing must not answer
    "cleared". Raised rather than blocked, because what is malformed is the request."""
    store = seed(tmp_path)

    with pytest.raises(PanelGateError, match=r"asked about no dataset"):
        require_datasets(store, request_for(datasets=()))


def test_a_dataset_the_report_has_no_cadence_for_reaches_the_caller_by_name(
    tmp_path: Path,
) -> None:
    """The gate adds no dataset vocabulary of its own; `panel_doctor`'s refusal is the one the
    caller sees, rather than being laundered into a block that would read as a panel defect."""
    store = seed(tmp_path)

    with pytest.raises(PanelDoctorError, match=r"'prices_daily' has no declared publication"):
        require_datasets(store, request_for(datasets=("prices_daily",)))
