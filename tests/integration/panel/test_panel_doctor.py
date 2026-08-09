"""The panel health report against a real store (`V2-P1-012`).

The roadmap's acceptance for this issue is one sentence -- "an injected defect can be
reported" -- so this file is organised around injections. One healthy panel is built from
nine datasets, asserted to produce a report with **no findings at all**, and then each defect
is injected into a fresh copy of it and the report is asserted to name that defect and,
where it matters, to name only that defect.

The clean case is not a formality. A health report that fires on a healthy panel is one every
reader learns to switch off, so "nothing to report" has to be a reachable state and is pinned
first.

Everything on disk here is built at run time: `scripts/verify_publication.py` blocks
`.parquet`/`.duckdb` from the tree, and a fixture that had to be committed would be a fixture
that goes stale silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.store import ColumnSpec, PanelStore
from openalpha_cn.panel_doctor import (
    FreshnessPolicy,
    PanelDoctorError,
    dataset_health,
    panel_health_report,
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
SECURITIES = ("000001.SZ", "000002.SZ", "600000.SH")
INDEX_CODE = "000300.SH"
INCOME_DATASET = "income"
LISTED_ON = date(2026, 1, 2)
FILING_ANNOUNCED = date(2026, 1, 5)
REPORT_PERIOD = date(2025, 12, 31)

# 12:00 Asia/Shanghai on 2026-01-17, a Saturday: `_sessions_published_through` therefore
# stops at Friday 2026-01-16, the last session written below.
AS_OF = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
FETCHED_AT = AS_OF
# 12:00 Asia/Shanghai on 2026-01-08: an ingest run that stopped a week before `AS_OF`. Both
# write-time session censuses bound their expectation at `fetched_at - 1 day`, so a series
# that stops on the 7th is *complete for this fetch* and reaches the store -- which is what a
# panel whose ingest quietly stopped actually looks like, and the only shape `stale` can be
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


def _calendar_days() -> tuple[date, ...]:
    span = (LAST_DAY - FIRST_DAY).days + 1
    return tuple(FIRST_DAY + timedelta(days=offset) for offset in range(span))


NEW_YEAR_HOLIDAY = (date(2026, 1, 1), date(2026, 1, 2))


def _open_days() -> tuple[date, ...]:
    """Every weekday of 2026 except the New Year recess.

    The recess is not decoration. `_session_census` bounds both write-time censuses at **1
    January of the partition's year**, not at the batch's own first row -- a partition that
    begins in March is the defect it exists for -- so a fixture whose first stored session is
    the fifth would be refused for two missing sessions unless the calendar says the exchange
    was shut on them, which in 2026 it was.
    """
    closed = set(NEW_YEAR_HOLIDAY)
    return tuple(day for day in _calendar_days() if day.weekday() < 5 and day not in closed)


SESSIONS = tuple(day for day in _open_days() if date(2026, 1, 5) <= day <= date(2026, 1, 16))
LAST_SESSION = SESSIONS[-1]
PREVIOUS_SESSION = SESSIONS[-2]
HALTED_SESSION = SESSIONS[4]
HALTED_SECURITY = SECURITIES[2]


def calendar() -> TradingCalendar:
    opens = set(_open_days())
    return build_trading_calendar(
        EXCHANGE,
        [CalendarDay(calendar_date=day, is_trading=day in opens) for day in _calendar_days()],
    )


# --- batch builders -------------------------------------------------------------------------
#
# Built directly rather than through `TushareProvider`: these tests are about the doctor, and
# the exact column names, kinds and clock conventions below are the ones the provider emits
# (verified against `providers/tushare.py`'s own contract tests).


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


def universe_batch(securities: Sequence[str] = SECURITIES) -> ColumnarPanelBatch:
    return _batch(
        STOCK_BASIC_DATASET,
        subjects=list(securities),
        columns=[
            PanelColumn("lifecycle_event", "string", tuple("listing" for _ in securities)),
            PanelColumn(
                "lifecycle_date", "string", tuple(LISTED_ON.isoformat() for _ in securities)
            ),
            PanelColumn("exchange", "string", tuple(EXCHANGE for _ in securities)),
        ],
        event_time=[_midnight_shanghai(LISTED_ON)] * len(securities),
        available_time=[_midnight_shanghai(LISTED_ON)] * len(securities),
    )


def _grid(
    days: Sequence[date], securities: Sequence[str]
) -> tuple[tuple[date, ...], tuple[str, ...]]:
    pairs = [(day, code) for day in days for code in securities]
    return tuple(day for day, _ in pairs), tuple(code for _, code in pairs)


def factor_batch(
    days: Sequence[date] = SESSIONS,
    securities: Sequence[str] = SECURITIES,
    *,
    factors: Mapping[tuple[str, date], float] | None = None,
    fetched_at: datetime = FETCHED_AT,
) -> ColumnarPanelBatch:
    grid_days, grid_codes = _grid(days, securities)
    values = tuple(
        (factors or {}).get((code, day), 1.0)
        for day, code in zip(grid_days, grid_codes, strict=True)
    )
    return _batch(
        ADJ_FACTOR_DATASET,
        subjects=grid_codes,
        columns=[
            PanelColumn("factor_date", "string", tuple(day.isoformat() for day in grid_days)),
            PanelColumn("adj_factor", "float", values),
        ],
        event_time=[_close_time(day) for day in grid_days],
        available_time=[_published_time(day) for day in grid_days],
        fetched_at=fetched_at,
    )


_CLOSE = 10.0
# Half a yuan a session. Deliberately far more than one tick: `pre_close_tolerance` allows a
# tick of `pre_close` plus a tick of each `adj_factor`, so a one-tick drift would be inside the
# tolerance and a cross-check that read the wrong session's close would still agree.
_SESSION_STEP = 0.5


def _bar_price(code: str, day: date) -> float:
    """A close that moves session by session as well as name by name.

    Deliberately not a per-security constant. With a flat series every session's `pre_close`
    equals its own `close`, so a cross-check that compared a bar against *itself* instead of
    against the previous session would agree perfectly and the fixture would prove nothing.
    """
    return _CLOSE + SECURITIES.index(code) + _SESSION_STEP * SESSIONS.index(day)


def _previous_close(code: str, day: date) -> float:
    """The published `pre_close`: the prior session's close, which is what the adjustment
    factors imply when nothing went ex-rights between the two."""
    position = SESSIONS.index(day)
    if position == 0:
        return _bar_price(code, day) - _SESSION_STEP
    return _bar_price(code, SESSIONS[position - 1])


def bar_batch(
    days: Sequence[date] = SESSIONS,
    securities: Sequence[str] = SECURITIES,
    *,
    omit: Sequence[tuple[str, date]] = (),
    closes: Mapping[tuple[str, date], float] | None = None,
) -> ColumnarPanelBatch:
    pairs = [(day, code) for day in days for code in securities if (code, day) not in set(omit)]
    grid_days = tuple(day for day, _ in pairs)
    grid_codes = tuple(code for _, code in pairs)
    close = tuple(
        (closes or {}).get((code, day), _bar_price(code, day))
        for day, code in zip(grid_days, grid_codes, strict=True)
    )
    previous = tuple(
        _previous_close(code, day) for day, code in zip(grid_days, grid_codes, strict=True)
    )
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
                "float",
                tuple(
                    (value / before - 1.0) * 100.0
                    for value, before in zip(close, previous, strict=True)
                ),
            ),
            PanelColumn("vol", "float", tuple(1000.0 for _ in close)),
            PanelColumn("amount", "float", tuple(10000.0 for _ in close)),
        ],
        event_time=[_close_time(day) for day in grid_days],
        available_time=[_published_time(day) for day in grid_days],
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


def valuation_batch(
    days: Sequence[date] = SESSIONS,
    securities: Sequence[str] = SECURITIES,
    *,
    omit: Sequence[tuple[str, date]] = (),
    closes: Mapping[tuple[str, date], float] | None = None,
) -> ColumnarPanelBatch:
    pairs = [(day, code) for day in days for code in securities if (code, day) not in set(omit)]
    grid_days = tuple(day for day, _ in pairs)
    grid_codes = tuple(code for _, code in pairs)
    close = tuple(
        (closes or {}).get((code, day), _bar_price(code, day))
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
    grid_days, grid_codes = _grid(days, securities)
    return _batch(
        PRICE_LIMIT_DATASET,
        subjects=grid_codes,
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day in grid_days)),
            PanelColumn(
                "up_limit",
                "float",
                tuple(
                    _bar_price(code, day) * 1.1
                    for day, code in zip(grid_days, grid_codes, strict=True)
                ),
            ),
            PanelColumn(
                "down_limit",
                "float",
                tuple(
                    _bar_price(code, day) * 0.9
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


def income_batch(
    securities: Sequence[str] = SECURITIES,
    *,
    duplicate: Sequence[str] = (),
    revised: Sequence[str] = (),
) -> ColumnarPanelBatch:
    """One filing per security, plus a second irreconcilable version for each of `duplicate`.

    `revised` names the securities whose row carries a `revision_time` after its
    `available_time` -- the only shape `PartitionCoverage.revised_row_count` can see.
    """
    codes = [*securities, *duplicate]
    flags = [*("1" for _ in securities), *("0" for _ in duplicate)]
    revenues = [
        *(100.0 + index for index, _ in enumerate(securities)),
        *(999.0 for _ in duplicate),
    ]
    announced = _midnight_shanghai(FILING_ANNOUNCED)
    revision = [
        announced + timedelta(days=1) if code in set(revised) else announced for code in codes
    ]
    return _batch(
        INCOME_DATASET,
        subjects=codes,
        columns=[
            PanelColumn("report_period", "string", tuple(REPORT_PERIOD.isoformat() for _ in codes)),
            PanelColumn("ann_date", "string", tuple(FILING_ANNOUNCED.isoformat() for _ in codes)),
            PanelColumn("f_ann_date", "string", tuple(FILING_ANNOUNCED.isoformat() for _ in codes)),
            PanelColumn("update_flag", "string", tuple(flags)),
            PanelColumn("total_revenue", "float", tuple(revenues)),
            *(
                PanelColumn(name, "float", tuple(1.0 for _ in codes))
                for name in INCOME_VALUE_COLUMNS
                if name != "total_revenue"
            ),
        ],
        event_time=[announced] * len(codes),
        available_time=[announced] * len(codes),
        revision_time=revision,
    )


# --- the healthy panel ------------------------------------------------------------------------


def seed(
    tmp_path: Path,
    *,
    bars: ColumnarPanelBatch | None = None,
    valuations: ColumnarPanelBatch | None = None,
    factors: ColumnarPanelBatch | None = None,
    income: ColumnarPanelBatch | None = None,
    halts: bool = True,
) -> PanelStore:
    store = PanelStore(tmp_path / "panel")
    real = calendar()
    write_trading_calendar(store, calendar_batch())
    write_stock_universe(store, universe_batch())
    write_adjustment_factors(store, [factors or factor_batch()], calendar=real)
    suspensions = None
    if halts:
        write_suspensions(store, [suspension_batch()])
        suspensions = _loaded_halts(store, real)
    write_daily_panel(
        store,
        bars=[bars or bar_batch(omit=((HALTED_SECURITY, HALTED_SESSION),) if halts else ())],
        fundamentals=[
            valuations
            or valuation_batch(omit=((HALTED_SECURITY, HALTED_SESSION),) if halts else ())
        ],
        calendar=real,
        halts=suspensions,
    )
    write_price_limits(store, [limit_batch()], calendar=real)
    write_index_weights(store, [index_weight_batch()])
    write_financial_statements(store, [income or income_batch()])
    return store


def _loaded_halts(store: PanelStore, real: TradingCalendar) -> Mapping[date, object]:
    from openalpha_cn.panel_ingest import load_suspensions

    return load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)


def report_for(store: PanelStore, **overrides: object) -> object:
    arguments: dict[str, object] = {
        "as_of": AS_OF,
        "datasets": DATASETS,
        "years": (YEAR,),
        "calendar": calendar(),
        "index_codes": (INDEX_CODE,),
        "cross_section_days": (LAST_SESSION,),
    }
    arguments.update(overrides)
    return panel_health_report(store, **arguments)  # type: ignore[arg-type]


# --- the clean case -----------------------------------------------------------------------


def test_a_healthy_panel_produces_a_report_with_nothing_to_report(tmp_path: Path) -> None:
    """The load-bearing one. Every other test in this file injects a defect; if this one could
    not be made to pass, none of the others would mean anything, because a report that always
    finds something is a report nobody reads twice."""
    report = report_for(seed(tmp_path))

    assert report.findings == ()
    assert report.is_clean
    assert [health.readiness.state for health in report.datasets] == ["ready"] * len(DATASETS)


def test_the_clean_report_still_says_which_checks_it_ran(tmp_path: Path) -> None:
    """ "No findings" is only meaningful alongside "and here is what was looked at". Silence
    from a check that never ran is indistinguishable from silence from one that passed."""
    report = report_for(seed(tmp_path))

    assert report.cross_checks
    assert all(check.ran for check in report.cross_checks), [
        (check.name, check.skipped_reason) for check in report.cross_checks if not check.ran
    ]
    assert {check.name for check in report.cross_checks} >= {
        "subject_containment",
        "close_agreement",
        "unpriced_explained",
        "return_paths",
        "statement_ambiguity",
    }


def test_the_clean_report_still_carries_the_datasets_inherent_limitations(tmp_path: Path) -> None:
    """The separation this report exists for: a structural boundary of the dataset is
    disclosed as a limitation, never as a finding of this fetch."""
    report = report_for(seed(tmp_path))

    codes = {item.code for item in report.limitations}
    assert "silent_truncation_at_the_response_cap" in codes
    assert "daily_basic_omits_the_beijing_board_before_2024" in codes
    assert report.findings == ()


def test_freshness_is_judged_per_cadence_rather_than_by_one_shared_threshold(
    tmp_path: Path,
) -> None:
    report = report_for(seed(tmp_path))

    policies = {health.dataset: health.freshness for health in report.datasets}
    assert policies[DAILY_DATASET].cadence == "daily"
    assert policies[INDEX_WEIGHT_DATASET].cadence == "monthly"
    assert policies[INCOME_DATASET].cadence == "quarterly"
    assert policies[STOCK_BASIC_DATASET].cadence == "event_driven"
    bounds = {
        name: policy.max_staleness for name, policy in policies.items() if policy.max_staleness
    }
    assert bounds[DAILY_DATASET] < bounds[INDEX_WEIGHT_DATASET] < bounds[INCOME_DATASET], bounds


# --- injection: a missing partition ---------------------------------------------------------


def test_a_year_that_was_never_ingested_is_reported_as_a_missing_partition(tmp_path: Path) -> None:
    report = report_for(seed(tmp_path), years=(YEAR - 1, YEAR))

    missing = report.findings_with_code("partition_missing")
    assert {finding.datasets[0] for finding in missing} == set(DATASETS)
    assert {finding.year for finding in missing} == {YEAR - 1}
    assert all(finding.category == "missing" for finding in missing)
    assert all(finding.severity == "blocking" for finding in missing)


def test_deleting_a_partitions_parquet_file_is_reported_separately_from_a_missing_one(
    tmp_path: Path,
) -> None:
    store = seed(tmp_path)
    parquet = next((store.root / DAILY_DATASET / str(YEAR)).glob("*.parquet"))
    parquet.unlink()

    report = report_for(store)

    (finding,) = report.findings_with_code("partition_file_missing")
    assert finding.datasets == (DAILY_DATASET,)
    assert not report.is_clean


# --- injection: a hole in the date coverage --------------------------------------------------


def test_a_session_missing_from_a_stored_year_is_reported_as_a_date_gap(tmp_path: Path) -> None:
    """`write_daily_panel` refuses this at write time, so the hole is injected the only way it
    can reach a real store: through the generic writer, which is what a partition written by
    an older build looks like."""
    store = seed(tmp_path)
    kept = tuple(day for day in SESSIONS if day != SESSIONS[3])
    holed = bar_batch(days=kept, omit=((HALTED_SECURITY, HALTED_SESSION),))
    write_panel_batch(store, holed, year=YEAR)

    report = report_for(store)

    (finding,) = report.findings_with_code("date_gap")
    assert finding.datasets == (DAILY_DATASET,)
    assert finding.dates == (SESSIONS[3],)
    assert finding.category == "missing"


# --- injection: data that has fallen behind --------------------------------------------------


def test_a_dataset_that_stops_short_of_its_cadence_is_reported_as_stale(tmp_path: Path) -> None:
    """`adj_factor` is the dataset this can be shown on cleanly: it waives `required_dates`
    (its partition is a compressed step function), so a short series produces `stale` and not
    also `date_gap` -- which is exactly why one shared threshold would be wrong."""
    store = seed(tmp_path, factors=factor_batch(days=SESSIONS[:3], fetched_at=STALE_FETCHED_AT))

    report = report_for(store)

    (finding,) = report.findings_with_code("stale")
    assert finding.datasets == (ADJ_FACTOR_DATASET,)
    assert finding.category == "stale"
    health = report.dataset(ADJ_FACTOR_DATASET)
    assert health.event_age is not None
    assert health.freshness.max_staleness is not None
    assert health.event_age > health.freshness.max_staleness


def test_the_same_shortfall_is_not_stale_for_a_dataset_that_publishes_quarterly(
    tmp_path: Path,
) -> None:
    """The point of a per-cadence threshold, stated as a comparison rather than as prose: the
    filing is older than the factor series and is not reported, because a quarterly dataset is
    supposed to be that old."""
    store = seed(tmp_path, factors=factor_batch(days=SESSIONS[:3], fetched_at=STALE_FETCHED_AT))

    report = report_for(store)

    factors = report.dataset(ADJ_FACTOR_DATASET)
    filings = report.dataset(INCOME_DATASET)
    assert factors.event_age is not None and filings.event_age is not None
    assert filings.event_age > factors.event_age
    assert [finding.datasets[0] for finding in report.findings_with_code("stale")] == [
        ADJ_FACTOR_DATASET
    ]


# --- injection: coverage that no longer describes the partition -------------------------------


def test_overwriting_a_partition_without_reprofiling_it_is_reported_as_stale_coverage(
    tmp_path: Path,
) -> None:
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
        rows=[(SECURITIES[0], LAST_SESSION.isoformat(), 11.0, 9.0)],
    )

    report = report_for(store)

    (finding,) = report.findings_with_code("coverage_stale")
    assert finding.datasets == (PRICE_LIMIT_DATASET,)
    assert finding.category == "stale"


# --- injection: a file that is not a Parquet file ---------------------------------------------


def test_a_truncated_parquet_file_is_reported_as_unreadable(tmp_path: Path) -> None:
    store = seed(tmp_path)
    parquet = next((store.root / SUSPENSION_DATASET / str(YEAR)).glob("*.parquet"))
    parquet.write_bytes(b"PAR")

    report = report_for(store)

    (finding,) = report.findings_with_code("partition_file_unreadable")
    assert finding.datasets == (SUSPENSION_DATASET,)


# --- the defect that never reaches the doctor -------------------------------------------------


def test_a_thin_cross_section_is_refused_at_write_time_and_so_never_reaches_the_report(
    tmp_path: Path,
) -> None:
    """`V2-P1-007`'s row-count floor already stops this shape from being stored, so the health
    report has no code for it. Pinned here so a future relaxation of that floor shows up as a
    hole in this file rather than as a silently unreported defect."""
    thin = bar_batch(omit=tuple((code, SESSIONS[2]) for code in SECURITIES[1:]))

    with pytest.raises(PanelBatchError, match=r"fewer than 50% of this partition's median"):
        seed(tmp_path, bars=thin, halts=False)


# --- injection: cross-dataset composition ------------------------------------------------------


def test_a_priced_security_with_no_adjustment_factor_is_reported_across_the_two_datasets(
    tmp_path: Path,
) -> None:
    store = seed(tmp_path, factors=factor_batch(securities=SECURITIES[:2]))

    report = report_for(store)

    (finding,) = report.findings_with_code("subject_set_disagreement")
    assert finding.datasets == (DAILY_DATASET, ADJ_FACTOR_DATASET)
    assert finding.items == (SECURITIES[2],)
    assert finding.category == "inconsistent"
    # No dataset is *blocked* by this -- `adjustment_requirement` waives `required_subjects`,
    # because naming the securities would be circular -- so the report is unclean on the
    # strength of the warning alone. A verdict that only counted blocking findings would call
    # this panel healthy while a third of its cross section cannot be adjusted.
    assert report.blocked_datasets == ()
    assert not report.is_clean


def test_a_wider_factor_corpus_than_the_price_panel_is_not_reported(tmp_path: Path) -> None:
    """The measured, ordinary shape -- 5,338 `daily` names against 5,387 `adj_factor` -- must
    not produce a finding, or every real panel reports one."""
    extra = (*SECURITIES, "000004.SZ")
    store = seed(tmp_path, factors=factor_batch(securities=extra))

    report = report_for(store)

    assert report.findings_with_code("subject_set_disagreement") == ()


# --- injection: the two datasets that republish the same close ----------------------------------


def test_a_valuation_close_that_contradicts_the_bar_is_reported(tmp_path: Path) -> None:
    """`write_daily_panel` refuses this too, so it is injected through the generic writer."""
    store = seed(tmp_path)
    contradicted = valuation_batch(
        omit=((HALTED_SECURITY, HALTED_SESSION),),
        closes={(SECURITIES[0], LAST_SESSION): 99.0},
    )
    write_panel_batch(store, contradicted, year=YEAR)

    report = report_for(store)

    (finding,) = report.findings_with_code("close_disagreement")
    assert finding.datasets == (DAILY_DATASET, DAILY_BASIC_DATASET)
    assert finding.count == 1
    assert finding.items == (SECURITIES[0],)


# --- injection: a missing bar with no halt behind it --------------------------------------------


def test_an_unpriced_security_with_no_halt_behind_it_is_reported(tmp_path: Path) -> None:
    """`V2-P1-008`'s explained-session floor refuses this at write time too -- a name that
    vanishes from a three-security session leaves 67% explained against a floor of 85% -- so
    the defect is injected through the generic writer, the same way the date hole is."""
    store = seed(tmp_path)
    gone = ((HALTED_SECURITY, HALTED_SESSION), (SECURITIES[0], LAST_SESSION))
    write_panel_batch(store, bar_batch(omit=gone), year=YEAR)
    write_panel_batch(store, valuation_batch(omit=gone), year=YEAR)

    report = report_for(store)

    (finding,) = report.findings_with_code("unexplained_unpriced")
    assert finding.items == (SECURITIES[0],)
    assert finding.category == "missing"
    assert finding.dates == (LAST_SESSION,)
    # 2 of 3 listed names are accounted for, under the 85% floor the same corpus fixed at
    # write time, so this is a warning rather than the notice a mostly-whole session earns.
    assert finding.severity == "warning"


def test_an_unpriced_security_a_halt_explains_is_not_reported(tmp_path: Path) -> None:
    """The whole point of `explain_unpriced`: the halted name in the clean fixture is absent
    from the cross section on its session and produces no finding."""
    report = report_for(seed(tmp_path), cross_section_days=(HALTED_SESSION,))

    assert report.findings_with_code("unexplained_unpriced") == ()


# --- injection: the two return paths disagreeing --------------------------------------------------


def test_a_factor_step_the_price_panel_does_not_corroborate_is_reported(tmp_path: Path) -> None:
    """`session_returns` raises on this rather than answering; the doctor's job is to turn
    that refusal into a line in a report instead of an exception out of a health check."""
    stepped = factor_batch(
        factors={(SECURITIES[0], LAST_SESSION): 2.0},
    )
    store = seed(tmp_path, factors=stepped)

    report = report_for(store)

    (finding,) = report.findings_with_code("return_path_disagreement")
    assert finding.datasets == (DAILY_DATASET, ADJ_FACTOR_DATASET)
    assert SECURITIES[0] in finding.detail


# --- injection: an ambiguous filing -----------------------------------------------------------


def test_two_irreconcilable_versions_of_one_filing_are_counted(tmp_path: Path) -> None:
    store = seed(tmp_path, income=income_batch(duplicate=(SECURITIES[0],)))

    report = report_for(store)

    (finding,) = report.findings_with_code("ambiguous_filing")
    assert finding.datasets == (INCOME_DATASET,)
    assert finding.count == 1
    assert finding.category == "duplicate"
    assert finding.severity == "notice"


def test_the_duplicate_versions_show_up_in_the_partitions_own_label_census_too(
    tmp_path: Path,
) -> None:
    """Two facets of the same duplication, and they are not redundant: the label census is
    read from the catalog without loading a single filing, so it still answers on a dataset
    whose histories cannot be built."""
    store = seed(tmp_path, income=income_batch(duplicate=(SECURITIES[0],)))

    report = report_for(store)

    (finding,) = report.findings_with_code("duplicate_versions")
    assert finding.datasets == (INCOME_DATASET,)
    assert finding.items == ("0", "1")
    assert report.dataset(INCOME_DATASET).revision_labels == (("0", 1), ("1", 3))


# --- injection: rows that were revised, and the correction the clock cannot see -----------------


def test_a_row_revised_after_it_became_available_is_reported_as_such(tmp_path: Path) -> None:
    store = seed(tmp_path, income=income_batch(revised=(SECURITIES[1],)))

    report = report_for(store)

    (finding,) = report.findings_with_code("revised_rows")
    assert finding.datasets == (INCOME_DATASET,)
    assert finding.count == 1
    assert finding.category == "revised"
    assert finding.severity == "notice"
    assert report.dataset(INCOME_DATASET).revised_row_count == 1


def test_a_same_day_correction_is_invisible_to_the_clock_and_the_report_says_so(
    tmp_path: Path,
) -> None:
    """`V2-P1-011` measured this: a Tushare restatement shares both announcement dates with the
    row it corrects, so `revised_row_count` reads 0 on exactly the data the revision facet
    exists for. The report must not present that 0 as "nothing was revised"."""
    store = seed(tmp_path, income=income_batch(duplicate=(SECURITIES[0],)))

    report = report_for(store)

    assert report.dataset(INCOME_DATASET).revised_row_count == 0
    assert report.findings_with_code("revised_rows") == ()
    assert report.findings_with_code("duplicate_versions")
    disclosed = {item.code for item in report.limitations}
    assert "a_correction_carries_no_instant_of_its_own" in disclosed


# --- the report says what it could not check ------------------------------------------------


def test_a_cross_check_that_cannot_run_is_named_rather_than_silently_skipped(
    tmp_path: Path,
) -> None:
    """A doctor that crashed on a sick panel would be useless, and one that quietly dropped a
    check would be worse: the reader would read the absence of a finding as a pass."""
    store = seed(tmp_path)
    parquet = next((store.root / ADJ_FACTOR_DATASET / str(YEAR)).glob("*.parquet"))
    parquet.unlink()

    report = report_for(store)

    skipped = [check for check in report.cross_checks if not check.ran]
    assert {check.name for check in skipped} >= {"return_paths"}
    assert all(check.skipped_reason for check in skipped)
    assert report.findings_with_code("check_unavailable")


# --- shape ------------------------------------------------------------------------------------


def test_every_code_the_report_emits_is_in_the_declared_closed_set(tmp_path: Path) -> None:
    """`V2-P1-013` branches on these and `V2-P1-016` serialises them; a code emitted but never
    declared is invisible to both."""
    from openalpha_cn.panel_doctor import PANEL_HEALTH_CODES

    store = seed(
        tmp_path, income=income_batch(duplicate=(SECURITIES[0],), revised=(SECURITIES[1],))
    )
    parquet = next((store.root / ADJ_FACTOR_DATASET / str(YEAR)).glob("*.parquet"))
    parquet.unlink()

    report = report_for(store, years=(YEAR - 1, YEAR))

    assert report.codes()
    assert report.codes() <= PANEL_HEALTH_CODES


# --- the accessors a gate and a REST face will use ---------------------------------------------


def test_the_report_answers_by_code_by_category_and_by_dataset(tmp_path: Path) -> None:
    """The three cuts `V2-P1-013` and `V2-P1-016` need, and all three refuse an undeclared
    argument rather than answering an empty tuple: a gate that asked about `parition_missing`
    and got `()` would report the panel healthy."""
    report = report_for(seed(tmp_path), years=(YEAR - 1, YEAR))

    assert report.findings_in_category("missing")
    assert report.blocked_datasets == DATASETS
    assert not report.dataset(DAILY_DATASET).is_ready
    with pytest.raises(PanelDoctorError, match=r"'parition_missing' is not one of the declared"):
        report.findings_with_code("parition_missing")
    with pytest.raises(PanelDoctorError, match=r"'broken' is not one of the declared categories"):
        report.findings_in_category("broken")
    with pytest.raises(PanelDoctorError, match=r"'namechange' was not one of the datasets"):
        report.dataset("namechange")


def test_a_dataset_the_store_has_never_heard_of_is_blocked_rather_than_skipped(
    tmp_path: Path,
) -> None:
    """The four datasets the healthy fixture does not write still get a verdict, and it is
    `partition_missing` -- a report that quietly omitted a dataset it was asked about would be
    the fail-open `years` exists to prevent."""
    report = panel_health_report(
        PanelStore(tmp_path / "empty"),
        as_of=AS_OF,
        datasets=(
            "namechange",
            "index_member_all",
            "index_classify",
            "fina_indicator",
            DAILY_DATASET,
        ),
        years=(YEAR,),
        calendar=calendar(),
    )

    assert report.blocked_datasets == (
        "namechange",
        "index_member_all",
        "index_classify",
        "fina_indicator",
        DAILY_DATASET,
    )
    assert {finding.code for finding in report.findings} >= {"partition_missing"}


def test_a_dataset_this_report_has_no_cadence_for_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(PanelDoctorError, match=r"'prices_daily' has no declared publication"):
        panel_health_report(
            PanelStore(tmp_path / "empty"),
            as_of=AS_OF,
            datasets=("prices_daily",),
            years=(YEAR,),
        )


def test_without_a_calendar_the_date_check_is_waived_on_the_record(tmp_path: Path) -> None:
    """Not silently skipped: the waiver lands in `checks_waived` and the report also says, as a
    finding, that hole detection did not run."""
    report = panel_health_report(
        seed(tmp_path), as_of=AS_OF, datasets=(DAILY_DATASET,), years=(YEAR,)
    )

    health = report.dataset(DAILY_DATASET)
    assert "required_dates" in health.readiness.checks_waived
    assert health.freshness.max_staleness is None
    (finding,) = report.findings_with_code("check_unavailable")
    assert "no trading calendar was supplied" in finding.detail


def test_a_calendar_that_does_not_reach_a_requested_year_is_reported_not_raised(
    tmp_path: Path,
) -> None:
    report = report_for(seed(tmp_path), years=(YEAR - 1, YEAR))

    reasons = [
        finding.detail
        for finding in report.findings_with_code("check_unavailable")
        if finding.datasets == (DAILY_DATASET,)
    ]
    assert reasons
    assert "required_dates could not be derived from the calendar" in reasons[0]


def test_the_index_weight_requirement_names_every_index_the_caller_asked_about(
    tmp_path: Path,
) -> None:
    """One index code goes through `index_weight_requirement` unchanged; several are named
    together, because readiness is per dataset and a partition holding 沪深300 but not
    中证1000 has to block for the second."""
    report = report_for(seed(tmp_path), index_codes=(INDEX_CODE, "000905.SH"))

    (finding,) = report.findings_with_code("subject_missing")
    assert finding.datasets == (INDEX_WEIGHT_DATASET,)
    assert finding.items == ("000905.SH",)


def test_naming_no_index_waives_the_subject_check_and_says_so(tmp_path: Path) -> None:
    report = report_for(seed(tmp_path), index_codes=())

    health = report.dataset(INDEX_WEIGHT_DATASET)
    assert "required_subjects" in health.readiness.checks_waived
    assert any(
        "no index code was named" in finding.detail
        for finding in report.findings_with_code("check_unavailable")
    )


def test_a_caller_supplied_freshness_bound_replaces_the_derived_one_and_records_that(
    tmp_path: Path,
) -> None:
    report = report_for(seed(tmp_path), freshness_overrides={DAILY_DATASET: timedelta(hours=1)})

    health = report.dataset(DAILY_DATASET)
    assert health.freshness.max_staleness == timedelta(hours=1)
    assert health.freshness.basis.startswith("stated by the caller")
    assert [finding.datasets for finding in report.findings_with_code("stale")] == [
        (DAILY_DATASET,)
    ]


def test_the_fetch_clock_is_reported_for_the_datasets_that_have_no_event_schedule(
    tmp_path: Path,
) -> None:
    """`stock_basic` is a snapshot with no as_of of its own, so "how old is the newest event"
    is not the question -- "how old is the fetch" is, and it is given as a number with no
    verdict attached rather than as a threshold nobody could justify."""
    report = report_for(seed(tmp_path))

    health = report.dataset(STOCK_BASIC_DATASET)
    assert health.freshness.max_staleness is None
    assert health.fetch_age == AS_OF - FETCHED_AT
    assert health.event_age == AS_OF - _midnight_shanghai(LISTED_ON)
    assert report.findings_with_code("stale") == ()


def test_a_report_asked_about_no_year_at_all_blocks_rather_than_reporting_ready(
    tmp_path: Path,
) -> None:
    """`evaluate_readiness`'s fail-closed rule reaching the report: a check that inspected
    nothing must not read as a pass, and with no year in scope there is no coverage record to
    take a subject set from either, so the containment check reports that it did not run."""
    report = report_for(seed(tmp_path), years=())

    assert report.blocked_datasets == DATASETS
    assert {finding.code for finding in report.findings} >= {"no_years_requested"}
    (containment,) = [check for check in report.cross_checks if check.name == "subject_containment"]
    assert not containment.ran
    assert containment.skipped_reason


def test_the_calendar_dataset_is_still_assessed_without_a_calendar_to_name_its_exchange(
    tmp_path: Path,
) -> None:
    report = panel_health_report(
        seed(tmp_path), as_of=AS_OF, datasets=(TRADING_CALENDAR_DATASET,), years=(YEAR,)
    )

    health = report.dataset(TRADING_CALENDAR_DATASET)
    assert health.is_ready
    assert "required_subjects" in health.readiness.checks_waived
    assert health.freshness.cadence == "published_in_advance"


def test_a_dataset_health_built_with_a_hand_made_policy_still_refuses_an_unknown_dataset(
    tmp_path: Path,
) -> None:
    """`dataset_health` takes a `freshness` argument, which skips the cadence lookup that
    would otherwise catch this; the requirement dispatch has to refuse it too, or an unknown
    dataset would be assessed against no requirement at all."""
    policy = FreshnessPolicy(
        dataset="prices_daily", cadence="daily", max_staleness=None, basis="invented"
    )

    with pytest.raises(PanelDoctorError, match=r"'prices_daily' is not a dataset this report"):
        dataset_health(
            PanelStore(tmp_path / "empty"),
            dataset="prices_daily",
            as_of=AS_OF,
            years=(YEAR,),
            freshness=policy,
        )


def test_a_bar_with_no_valuation_row_is_the_ordinary_direction_and_is_not_reported(
    tmp_path: Path,
) -> None:
    """The asymmetry `close_disagreements` measured and this report inherits: `daily_basic`
    was a subset of `daily` on every session probed and never a superset, and all 60 of
    2020-03-02's missing names are Beijing-board codes. Reporting this direction would report
    every pre-2024 year."""
    store = seed(tmp_path)
    write_panel_batch(
        store,
        valuation_batch(omit=((HALTED_SECURITY, HALTED_SESSION), (SECURITIES[0], LAST_SESSION))),
        year=YEAR,
    )

    report = report_for(store)

    assert report.findings_with_code("close_disagreement") == ()


def test_a_valuation_row_with_no_bar_behind_it_is_a_contradiction_and_is_reported(
    tmp_path: Path,
) -> None:
    """The other direction, which has never been observed and therefore cannot be sparse
    data: `daily_basic` priced a security on a session `daily` has no bar for."""
    store = seed(tmp_path)
    write_panel_batch(
        store,
        bar_batch(omit=((HALTED_SECURITY, HALTED_SESSION), (SECURITIES[0], LAST_SESSION))),
        year=YEAR,
    )

    report = report_for(store)

    (finding,) = report.findings_with_code("close_disagreement")
    assert finding.items == (SECURITIES[0],)
    assert finding.dates == (LAST_SESSION,)


def test_a_coverage_record_that_no_longer_describes_the_partition_is_not_compared(
    tmp_path: Path,
) -> None:
    """A stale coverage record still *has* a subject list, and comparing it would manufacture
    a cross-dataset disagreement that is really just the record being out of date. Here the
    `adj_factor` record says two securities while the partition on disk holds three; the
    report says `coverage_stale` and says nothing about composition."""
    store = seed(tmp_path)
    write_panel_batch(store, factor_batch(securities=SECURITIES[:2]), year=YEAR)
    store.write_partition(
        ADJ_FACTOR_DATASET,
        year=YEAR,
        columns=(
            ColumnSpec("subject", "VARCHAR"),
            ColumnSpec("factor_date", "VARCHAR"),
            ColumnSpec("adj_factor", "DOUBLE"),
        ),
        rows=[(code, LAST_SESSION.isoformat(), 1.0) for code in SECURITIES],
    )

    report = report_for(store)

    assert [finding.datasets for finding in report.findings_with_code("coverage_stale")] == [
        (ADJ_FACTOR_DATASET,)
    ]
    assert report.findings_with_code("subject_set_disagreement") == ()
