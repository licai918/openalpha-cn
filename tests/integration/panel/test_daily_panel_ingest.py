"""`daily` + `daily_basic` end to end: Tushare -> session batches -> two partitions -> answers.

The whole chain runs against a real `PanelStore` on a real DuckDB catalog with real Parquet
files. Only the HTTP transport is doubled, and what it serves is real market data captured live
on 2026-08-08/09.

Four acceptances live here.

**The returns one.** `test_the_stored_panel_answers_the_ex_dividend_day_on_both_correct_paths`
recomputes 2026-06-12 for `000001.SZ` out of the store: `close/pre_close` gives +2.742230%,
`adj_factor` gives +2.742251%, and close-to-close gives **-0.530973%** with the sign reversed.
The first two agree; the third is the defect `V2-P1-006` and `V2-P1-007` exist to remove, and
this is the cross-validation that exercises both datasets against each other.

**The join one.** A cross section is `daily` joined onto the listed universe and its factors.
The 49-name gap between `adj_factor` (5,387 rows) and `daily` (5,338) on 2024-06-28 splits into
26 halted names and 23 already-delisted ones, and the tests below pin that each half lands
somewhere explicit rather than being dropped.

**The two-dataset one.** `daily_basic` republishes `close`, so the two fetches a session needs
cross-check each other for free. The write refuses a disagreement, and tolerates the one
direction that is real (a Beijing-board bar with no valuation row).

**The session-census one.** A year that is missing a session the calendar reports open is
refused at write time, and -- unlike `adj_factor`'s, which pays for a waived read-side check --
it is refused *again* on every read, because a price partition is stored uncompressed and its
date census survives.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_BASIC_PANEL_COLUMNS,
    DAILY_DATASET,
    DAILY_PANEL_COLUMNS,
    PriceDataError,
    close_disagreements,
    close_index,
    priced_cross_section,
    session_returns,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.stock_universe import SecurityLifecycle, build_stock_universe
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    CalendarHorizonError,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    daily_basic_requirement,
    daily_requirement,
    load_adjustment_histories,
    load_daily_bars,
    load_daily_valuations,
    write_adjustment_factors,
    write_daily_panel,
)
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TUSHARE_RESPONSE_TRUNCATION_FLAG, TushareProvider

PING_AN = "000001.SZ"
MAOTAI = "600519.SH"

DAILY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
]
DAILY_BASIC_FIELDS = [
    "ts_code",
    "trade_date",
    "close",
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
]
FACTOR_FIELDS = ["ts_code", "trade_date", "adj_factor"]


# Real rows, four consecutive sessions across `000001.SZ`'s 2026-06-12 ex-dividend date.
# Kept unformatted: one row per line is what makes these readable as market data, and the
# formatter's one-value-per-line expansion turns eight bars into two hundred lines.
# fmt: off
BARS: dict[str, list[list[Any]]] = {
    "20260610": [
        [PING_AN, "20260610", 11.07, 11.35, 11.07, 11.32, 11.13, 0.19, 1.7071,
         1543176.39, 1738056.71203],
        [MAOTAI, "20260610", 1252.08, 1282.0, 1250.21, 1275.88, 1256.0, 19.88, 1.5828,
         39244.14, 4991686.419],
    ],
    "20260611": [
        [PING_AN, "20260611", 11.32, 11.39, 11.25, 11.3, 11.32, -0.02, -0.1767,
         1156222.22, 1308133.97213],
        [MAOTAI, "20260611", 1272.12, 1282.88, 1266.91, 1279.0, 1275.88, 3.12, 0.2445,
         25351.98, 3230008.22],
    ],
    "20260612": [
        [PING_AN, "20260612", 11.0, 11.25, 10.88, 11.24, 10.94, 0.3, 2.7422,
         2032355.46, 2263042.93057],
        [MAOTAI, "20260612", 1271.18, 1295.0, 1265.01, 1291.91, 1279.0, 12.91, 1.0094,
         50494.78, 6477910.214],
    ],
    "20260615": [
        [PING_AN, "20260615", 11.21, 11.21, 10.98, 11.06, 11.24, -0.18, -1.6014,
         1541304.95, 1711561.28657],
        [MAOTAI, "20260615", 1292.7, 1292.7, 1270.1, 1271.1, 1291.91, -20.81, -1.6108,
         41585.56, 5303656.129],
    ],
}

VALUATIONS: dict[str, list[list[Any]]] = {
    "20260610": [
        [PING_AN, "20260610", 11.32, 0.7952, 1.891, 1.56, 5.1527, 5.1016, 0.4734, 1.6713,
         1.6516, 5.2828, 5.2828, 1940591.8198, 1940560.0653, 816048.1215, 21967499.4001,
         21967139.9392],
        [MAOTAI, "20260610", 1275.88, 0.3139, 0.7255, 1.12, 19.375, 19.2825, 5.9537, 9.4466,
         9.2651, 4.0548, 4.0548, 125008.1601, 125008.1601, 54094.8978, 159495411.3084,
         159495411.3084],
    ],
    "20260611": [
        [PING_AN, "20260611", 11.3, 0.5958, 1.4169, 1.02, 5.1436, 5.0926, 0.4725, 1.6683,
         1.6486, 5.2922, 5.2922, 1940591.8198, 1940560.0653, 816048.1215, 21928687.5637,
         21928328.7379],
        [MAOTAI, "20260611", 1279.0, 0.2028, 0.4687, 0.78, 19.4224, 19.3297, 5.9682, 9.4697,
         9.2878, 4.0449, 4.0449, 125008.1601, 125008.1601, 54094.8978, 159885436.7679,
         159885436.7679],
    ],
    "20260612": [
        [PING_AN, "20260612", 11.24, 1.0473, 2.4905, 1.71, 5.1163, 5.0655, 0.47, 1.6595,
         1.6399, 5.3204, 5.3026, 1940591.8198, 1940560.0653, 816048.1215, 21812252.0568,
         21811895.1868],
        [MAOTAI, "20260612", 1291.91, 0.4039, 0.9334, 1.63, 19.6185, 19.5248, 5.9617, 9.5653,
         9.3815, 4.0045, 4.0045, 125008.1601, 125008.1601, 54094.8978, 161499291.9856,
         161499291.9856],
    ],
    "20260615": [
        [PING_AN, "20260615", 11.06, 0.7943, 1.8887, 1.11, 5.0344, 4.9844, 0.4625, 1.6329,
         1.6136, 5.407, 5.3889, 1940591.8198, 1940560.0653, 816048.1215, 21462945.5292,
         21462594.3742],
        [MAOTAI, "20260615", 1271.1, 0.3327, 0.7688, 1.2, 19.3024, 19.2103, 5.8657, 9.4113,
         9.2304, 4.07, 4.07, 125008.1601, 125008.1601, 54094.8978, 158897872.176,
         158897872.176],
    ],
}
# fmt: on


# Two real 2017 sessions, both carrying a null `free_share` -- the shape that made 2017 and
# 2018 unstorable. `300290.SZ` has one on 74 consecutive sessions from 2017-09-20 through
# 2018-01-16, and 7 of 2017-10-09's 3,180 valuation rows have one. `600637.SH` is a second
# name with the same hole on the same sessions.
# fmt: off
SPARSE_BARS_2017: dict[str, list[list[Any]]] = {
    "20171009": [
        ["300290.SZ", "20171009", 11.41, 11.51, 11.24, 11.31, 11.38, -0.07, -0.62,
         51316.6, 58264.782],
        ["600637.SH", "20171009", 20.44, 20.53, 20.35, 20.38, 20.29, 0.09, 0.44,
         59734.36, 122173.68],
    ],
    "20171010": [
        ["300290.SZ", "20171010", 11.33, 12.28, 11.3, 12.1, 11.31, 0.79, 6.99,
         148291.47, 176366.432],
        ["600637.SH", "20171010", 20.38, 20.83, 20.38, 20.81, 20.38, 0.43, 2.11,
         94144.81, 194441.862],
    ],
}
SPARSE_VALUATIONS_2017: dict[str, list[list[Any]]] = {
    "20171009": [
        ["300290.SZ", "20171009", 11.31, 3.949, 3.9468, 0.59, 112.2115, 103.744, 5.1388,
         7.233, 7.9402, 0.3095, 0.1857, 32142.9652, 12994.6866, None, 363536.9364,
         146969.9054],
        ["600637.SH", "20171009", 20.38, 0.3729, 0.35, 1.26, 18.3498, 18.7005, 2.0073,
         2.7687, 2.6469, 1.1221, 1.6683, 264173.5216, 160184.7117, None, 5383856.3702,
         3264564.4244],
    ],
    "20171010": [
        ["300290.SZ", "20171010", 12.1, 11.4117, 11.4053, 2.14, 120.0494, 110.9905, 5.4977,
         7.7382, 8.4949, 0.2893, 0.1736, 32142.9652, 12994.6866, None, 388929.8789,
         157235.7079],
        ["600637.SH", "20171010", 20.81, 0.5877, 0.5517, 2.1, 18.737, 19.0951, 2.0497,
         2.8271, 2.7028, 1.0989, 1.6338, 264173.5216, 160184.7117, None, 5497450.9845,
         3333443.8505],
    ],
}
# fmt: on

SESSIONS_2017: tuple[str, ...] = ("20171009", "20171010")
FETCHED_AT_2017 = datetime(2017, 10, 11, 12, 0, tzinfo=UTC)
AS_OF_2017 = datetime(2017, 10, 11, 12, 0, tzinfo=UTC)

FACTORS: dict[str, list[list[Any]]] = {
    "20260610": [[PING_AN, "20260610", 134.5794], [MAOTAI, "20260610", 8.4464]],
    "20260611": [[PING_AN, "20260611", 134.5794], [MAOTAI, "20260611", 8.4464]],
    "20260612": [[PING_AN, "20260612", 139.008], [MAOTAI, "20260612", 8.4464]],
    "20260615": [[PING_AN, "20260615", 139.008], [MAOTAI, "20260615", 8.4464]],
}

SESSIONS: tuple[str, ...] = ("20260610", "20260611", "20260612", "20260615")

FETCHED_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
AS_OF = FETCHED_AT

JUNE_10 = date(2026, 6, 10)
JUNE_11 = date(2026, 6, 11)
JUNE_12 = date(2026, 6, 12)
JUNE_15 = date(2026, 6, 15)

PUBLISHED_RETURN = 0.02742230347349195
ADJUSTED_RETURN = 0.027422506154573423
UNADJUSTED_RETURN = -0.005309734513274433


class _Transport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.response


def _response(fields: list[str], items: list[list[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(fields),
            "items": items,
            "count": 0,
            TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
        },
    }


def _as_of(day: str) -> datetime:
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 12, 0, tzinfo=UTC)


def _batch(dataset: str, fields: list[str], items: list[list[Any]], day: str, fetched_at: datetime):
    provider = TushareProvider(
        token="secret-token",
        transport=_Transport(_response(fields, items)),
        clock=lambda: fetched_at,
    )
    return provider.fetch_panel(ProviderRequest(dataset=dataset, as_of=_as_of(day)))


def _bar_batches(days: Sequence[str] = SESSIONS, *, fetched_at: datetime = FETCHED_AT):
    return [_batch(DAILY_DATASET, DAILY_FIELDS, BARS[day], day, fetched_at) for day in days]


def _valuation_batches(days: Sequence[str] = SESSIONS, *, fetched_at: datetime = FETCHED_AT):
    return [
        _batch(DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, VALUATIONS[day], day, fetched_at)
        for day in days
    ]


def _factor_batches(days: Sequence[str] = SESSIONS, *, fetched_at: datetime = FETCHED_AT):
    return [
        _batch(ADJ_FACTOR_DATASET, FACTOR_FIELDS, FACTORS[day], day, fetched_at) for day in days
    ]


CALENDAR_FIRST_DAY = date(2026, 1, 1)
CALENDAR_LAST_DAY = date(2026, 12, 31)


def _calendar(
    open_days: Iterable[str] = SESSIONS, *, last_day: date = CALENDAR_LAST_DAY
) -> TradingCalendar:
    """A real `TradingCalendar` over 2026 whose only open sessions are `open_days`."""
    opens = {date(int(day[:4]), int(day[4:6]), int(day[6:])) for day in open_days}
    span = (last_day - CALENDAR_FIRST_DAY).days + 1
    return build_trading_calendar(
        "SZSE",
        [
            CalendarDay(
                calendar_date=CALENDAR_FIRST_DAY + timedelta(days=offset),
                is_trading=CALENDAR_FIRST_DAY + timedelta(days=offset) in opens,
            )
            for offset in range(span)
        ],
    )


def _calendar_2017() -> TradingCalendar:
    """A real `TradingCalendar` over 2017 whose only open sessions are `SESSIONS_2017`."""
    first, last = date(2017, 1, 1), date(2017, 12, 31)
    opens = {date(int(day[:4]), int(day[4:6]), int(day[6:])) for day in SESSIONS_2017}
    return build_trading_calendar(
        "SZSE",
        [
            CalendarDay(
                calendar_date=first + timedelta(days=offset),
                is_trading=first + timedelta(days=offset) in opens,
            )
            for offset in range((last - first).days + 1)
        ],
    )


# A synthetic **breadth** fixture, and the only manufactured data in this file. The prices are
# `000001.SZ`'s real ones; what is fabricated is the *number of securities*, because a cross
# section has to be wide enough for "this session came back short" to be distinguishable from
# "these securities were halted", and the real two-name fixture above cannot be.
_WIDE_MARKET = 40


def _wide_code(index: int) -> str:
    return f"{600000 + index}.SH"


def _wide_rows(template: list[Any], count: int) -> list[list[Any]]:
    return [[_wide_code(index), *template[1:]] for index in range(count)]


def _wide_batches(sizes: Mapping[str, int]):
    """One `daily` and one `daily_basic` batch per session, each `sizes[day]` securities wide."""
    bars = [
        _batch(DAILY_DATASET, DAILY_FIELDS, _wide_rows(BARS[day][0], sizes[day]), day, FETCHED_AT)
        for day in SESSIONS
    ]
    fundamentals = [
        _batch(
            DAILY_BASIC_DATASET,
            DAILY_BASIC_FIELDS,
            _wide_rows(VALUATIONS[day][0], sizes[day]),
            day,
            FETCHED_AT,
        )
        for day in SESSIONS
    ]
    return bars, fundamentals


def _universe(codes: tuple[str, ...] = (PING_AN, MAOTAI)):
    return build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=[
            SecurityLifecycle(
                ts_code=ts_code, exchange="SZSE", listed_on=date(1991, 4, 3), delisted_on=None
            )
            for ts_code in codes
        ],
        years_read=(1991,),
    )


def _store(tmp_path: Path) -> PanelStore:
    return PanelStore(tmp_path / "panel")


def _seeded(tmp_path: Path, *, calendar: TradingCalendar | None = None) -> PanelStore:
    store = _store(tmp_path)
    real = calendar or _calendar()
    write_daily_panel(store, bars=_bar_batches(), fundamentals=_valuation_batches(), calendar=real)
    write_adjustment_factors(store, _factor_batches(), calendar=real)
    return store


# --- the returns acceptance ---------------------------------------------------------------


def test_the_stored_panel_answers_the_ex_dividend_day_on_both_correct_paths(
    tmp_path: Path,
) -> None:
    store = _seeded(tmp_path)
    calendar = _calendar()
    bars = load_daily_bars(store, day=JUNE_12, calendar=calendar, as_of=AS_OF, max_staleness=None)
    previous = load_daily_bars(
        store, day=JUNE_11, calendar=calendar, as_of=AS_OF, max_staleness=None
    )
    histories = load_adjustment_histories(store, years=(2026,), as_of=AS_OF, max_staleness=None)

    returns = session_returns(
        bars[PING_AN],
        previous_close=previous[PING_AN].close,
        previous_day=JUNE_11,
        factors=histories[PING_AN],
    )

    assert returns.published == pytest.approx(PUBLISHED_RETURN, abs=1e-12)
    assert returns.adjusted == pytest.approx(ADJUSTED_RETURN, abs=1e-12)
    assert returns.unadjusted == pytest.approx(UNADJUSTED_RETURN, abs=1e-12)
    assert returns.unadjusted < 0 < returns.published
    assert returns.disagreement < 0.01
    # Tushare's own pct_chg, stored as data and not recomputed.
    assert bars[PING_AN].pct_chg == 2.7422


def test_a_factor_partition_with_the_ex_dividend_session_missing_makes_the_read_refuse(
    tmp_path: Path,
) -> None:
    """The two datasets check each other, and this is the direction that matters.

    `V2-P1-006`'s review ended on a factor partition with a session hole answering
    `factor_on(2026-06-12)` with 134.5794 instead of 139.008 -- silently, from a partition that
    reported `ready=True`. `write_adjustment_factors`' census now refuses to *create* one; this
    test builds one anyway, by writing the factor year through a calendar that does not know
    the 12th is a session, and shows that `session_returns` then refuses the answer rather than
    returning the unadjusted number.
    """
    store = _store(tmp_path)
    real = _calendar()
    write_daily_panel(store, bars=_bar_batches(), fundamentals=_valuation_batches(), calendar=real)
    blind = _calendar(("20260610", "20260611", "20260615"))
    write_adjustment_factors(
        store, _factor_batches(("20260610", "20260611", "20260615")), calendar=blind
    )

    bars = load_daily_bars(store, day=JUNE_12, calendar=real, as_of=AS_OF, max_staleness=None)
    histories = load_adjustment_histories(store, years=(2026,), as_of=AS_OF, max_staleness=None)

    assert histories[PING_AN].factor_on(JUNE_12) == 134.5794  # the truth is 139.008
    with pytest.raises(PriceDataError, match="implied pre_close"):
        session_returns(
            bars[PING_AN], previous_close=11.3, previous_day=JUNE_11, factors=histories[PING_AN]
        )


# --- the join acceptance -------------------------------------------------------------------


def test_a_stored_session_joins_onto_the_universe_and_its_factors(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    calendar = _calendar()
    section = priced_cross_section(
        load_daily_bars(store, day=JUNE_12, calendar=calendar, as_of=AS_OF, max_staleness=None),
        load_adjustment_histories(store, years=(2026,), as_of=AS_OF, max_staleness=None),
        universe=_universe(),
        calendar=calendar,
        day=JUNE_12,
    )

    assert [entry.ts_code for entry in section.priced] == [PING_AN, MAOTAI]
    assert section.priced[0].adjusted_close == pytest.approx(11.24 * 139.008)
    assert section.unpriced == ()
    assert section.unlisted_bars == ()
    assert section.listed_count == 2


def test_a_halted_security_is_named_in_unpriced_rather_than_dropped(tmp_path: Path) -> None:
    """The 26 of the 49. `adj_factor` served 5,387 names on 2024-06-28 and `daily` 5,338; 26 of
    the difference were listed and halted (all 26 appear in that session's 28-row `suspend_d`).
    Here `600519.SH` has a factor and no bar, and it lands in `unpriced` by name."""
    store = _store(tmp_path)
    calendar = _calendar()
    thin = {day: [row for row in BARS[day] if row[0] == PING_AN] for day in SESSIONS}
    write_daily_panel(
        store,
        bars=[_batch(DAILY_DATASET, DAILY_FIELDS, thin[day], day, FETCHED_AT) for day in SESSIONS],
        fundamentals=[
            _batch(
                DAILY_BASIC_DATASET,
                DAILY_BASIC_FIELDS,
                [row for row in VALUATIONS[day] if row[0] == PING_AN],
                day,
                FETCHED_AT,
            )
            for day in SESSIONS
        ],
        calendar=calendar,
    )
    write_adjustment_factors(store, _factor_batches(), calendar=calendar)

    section = priced_cross_section(
        load_daily_bars(store, day=JUNE_12, calendar=calendar, as_of=AS_OF, max_staleness=None),
        load_adjustment_histories(store, years=(2026,), as_of=AS_OF, max_staleness=None),
        universe=_universe(),
        calendar=calendar,
        day=JUNE_12,
    )
    assert [entry.ts_code for entry in section.priced] == [PING_AN]
    assert section.unpriced == (MAOTAI,)


def test_an_already_delisted_security_never_enters_the_cross_section_at_all(
    tmp_path: Path,
) -> None:
    """The other 23. They carry factor rows for years after their last bar
    (`delisted_securities_carry_unstable_factors`), and `universe.listed_on` is what keeps them
    out -- so they are not mistaken for halts, and no `suspend_d` is needed to tell the two
    populations apart."""
    store = _seeded(tmp_path)
    calendar = _calendar()
    dead = build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=[
            SecurityLifecycle(
                ts_code=PING_AN, exchange="SZSE", listed_on=date(1991, 4, 3), delisted_on=None
            ),
            SecurityLifecycle(
                ts_code=MAOTAI,
                exchange="SZSE",
                listed_on=date(2001, 8, 27),
                delisted_on=date(2026, 1, 5),
            ),
        ],
        years_read=(1991,),
    )
    section = priced_cross_section(
        load_daily_bars(store, day=JUNE_12, calendar=calendar, as_of=AS_OF, max_staleness=None),
        load_adjustment_histories(store, years=(2026,), as_of=AS_OF, max_staleness=None),
        universe=dead,
        calendar=calendar,
        day=JUNE_12,
    )
    assert [entry.ts_code for entry in section.priced] == [PING_AN]
    assert section.unpriced == ()
    assert section.unlisted_bars == (MAOTAI,)
    assert section.listed_count == 1


# --- the two-dataset acceptance -------------------------------------------------------------


def test_the_two_datasets_are_stored_as_separate_partitions_of_the_same_sessions(
    tmp_path: Path,
) -> None:
    store = _seeded(tmp_path)
    bar_ref, valuation_ref = write_daily_panel(
        store, bars=_bar_batches(), fundamentals=_valuation_batches(), calendar=_calendar()
    )
    assert bar_ref.row_count == valuation_ref.row_count == len(SESSIONS) * 2

    bar_coverage = store.read_coverage(DAILY_DATASET, 2026)
    valuation_coverage = store.read_coverage(DAILY_BASIC_DATASET, 2026)
    assert bar_coverage is not None
    assert valuation_coverage is not None
    assert [entry.event_date for entry in bar_coverage.dates] == [
        JUNE_10,
        JUNE_11,
        JUNE_12,
        JUNE_15,
    ]
    stored = [entry.name for entry in valuation_coverage.fields]
    assert stored[0] == "subject"
    assert stored[-17:] == list(DAILY_BASIC_PANEL_COLUMNS[1:])
    assert {entry.name for entry in bar_coverage.fields} >= set(DAILY_PANEL_COLUMNS)


def test_a_close_that_disagrees_between_the_two_datasets_is_refused_at_write_time(
    tmp_path: Path,
) -> None:
    """The free data-quality check, made a precondition of storage rather than a report.

    `daily_basic` republishes `close`, so the two fetches a session already needs cross-check
    each other with no extra request; measured across five sessions from 2023 to 2026, zero
    disagreements in 24,188 shared rows. Making it a write guard is what stops a partition that
    contradicts its sibling from existing at all -- and both partitions are vetted before either
    is written, so the refusal itself is not destructive.
    """
    store = _store(tmp_path)
    drifted = {day: [list(row) for row in VALUATIONS[day]] for day in SESSIONS}
    drifted["20260612"][0][DAILY_BASIC_FIELDS.index("close")] = 11.25
    with pytest.raises(PanelBatchError, match=r"000001\.SZ on 2026-06-12.*11\.24.*11\.25"):
        write_daily_panel(
            store,
            bars=_bar_batches(),
            fundamentals=[
                _batch(DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, drifted[day], day, FETCHED_AT)
                for day in SESSIONS
            ],
            calendar=_calendar(),
        )
    assert store.read_coverage(DAILY_DATASET, 2026) is None
    assert store.read_coverage(DAILY_BASIC_DATASET, 2026) is None


def test_a_valuation_for_a_security_with_no_bar_is_refused_as_the_impossible_direction(
    tmp_path: Path,
) -> None:
    """`daily_basic` was a subset of `daily` on every session probed and never a superset."""
    store = _store(tmp_path)
    thin = {day: [row for row in BARS[day] if row[0] == PING_AN] for day in SESSIONS}
    with pytest.raises(PanelBatchError, match=r"600519\.SH on 2026-06-10 has a daily_basic row"):
        write_daily_panel(
            store,
            bars=[
                _batch(DAILY_DATASET, DAILY_FIELDS, thin[day], day, FETCHED_AT) for day in SESSIONS
            ],
            fundamentals=_valuation_batches(),
            calendar=_calendar(),
        )


def test_a_bar_with_no_valuation_is_written_because_that_is_the_measured_shape(
    tmp_path: Path,
) -> None:
    """On 2020-03-02 `daily` served 3,843 rows and `daily_basic` 3,783, and all 60 of the
    difference were Beijing-exchange codes; the same one-directional gap appears on 2015-07-08,
    2018-01-02 and 2022-04-25. Refusing it would refuse every pre-2024 year."""
    store = _store(tmp_path)
    thin = {day: [row for row in VALUATIONS[day] if row[0] == PING_AN] for day in SESSIONS}
    bar_ref, valuation_ref = write_daily_panel(
        store,
        bars=_bar_batches(),
        fundamentals=[
            _batch(DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, thin[day], day, FETCHED_AT)
            for day in SESSIONS
        ],
        calendar=_calendar(),
    )
    assert bar_ref.row_count == 8
    assert valuation_ref.row_count == 4

    bars = load_daily_bars(
        store, day=JUNE_12, calendar=_calendar(), as_of=AS_OF, max_staleness=None
    )
    valuations = load_daily_valuations(
        store, day=JUNE_12, calendar=_calendar(), as_of=AS_OF, max_staleness=None
    )
    assert sorted(bars) == [PING_AN, MAOTAI]
    assert sorted(valuations) == [PING_AN]
    assert close_disagreements(close_index(bars), close_index(valuations)) == ()


def test_the_stored_valuations_carry_the_neutralisation_and_leaderboard_inputs(
    tmp_path: Path,
) -> None:
    """`total_mv` / `circ_mv` / `turnover_rate` are what P3's neutralisation and P4's
    leaderboards read; this is the round trip that proves they survive storage as real
    numbers rather than as text."""
    store = _seeded(tmp_path)
    valuations = load_daily_valuations(
        store, day=JUNE_12, calendar=_calendar(), as_of=AS_OF, max_staleness=None
    )
    assert valuations[PING_AN].total_mv == 21812252.0568
    assert valuations[PING_AN].circ_mv == 21811895.1868
    assert valuations[PING_AN].turnover_rate == 1.0473
    assert valuations[MAOTAI].pe_ttm == 19.5248
    assert valuations[MAOTAI].pb == 5.9617


# --- the session census ---------------------------------------------------------------------


def test_a_year_missing_a_session_the_calendar_reports_open_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(
        PanelBatchError,
        match=(
            r"daily year=2026 is missing 1 session\(s\) the SZSE calendar reports open "
            r"between 2026-01-01 and 2026-08-07: \['2026-06-12'\]"
        ),
    ):
        write_daily_panel(
            store,
            bars=_bar_batches(("20260610", "20260611", "20260615")),
            fundamentals=_valuation_batches(("20260610", "20260611", "20260615")),
            calendar=_calendar(),
        )


def test_the_census_runs_on_the_valuations_too_and_names_that_dataset(tmp_path: Path) -> None:
    """Both datasets are censused and both before either is written, so a `daily` year that is
    whole cannot be stored beside a `daily_basic` year that is not."""
    store = _store(tmp_path)
    with pytest.raises(PanelBatchError, match=r"daily_basic year=2026 is missing 1 session"):
        write_daily_panel(
            store,
            bars=_bar_batches(),
            fundamentals=_valuation_batches(("20260610", "20260611", "20260615")),
            calendar=_calendar(),
        )
    assert store.read_coverage(DAILY_DATASET, 2026) is None


def test_a_session_the_calendar_does_not_know_is_tolerated_rather_than_refused(
    tmp_path: Path,
) -> None:
    """Same direction rule as `adj_factor`'s census: an extra observation can add information
    and cannot remove any, so only the missing direction is refused."""
    store = _store(tmp_path)
    reference, _ = write_daily_panel(
        store,
        bars=_bar_batches(),
        fundamentals=_valuation_batches(),
        calendar=_calendar(("20260610", "20260611", "20260612")),
    )
    assert reference.row_count == 8


def test_a_calendar_that_does_not_reach_across_the_year_refuses_rather_than_under_checking(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(CalendarHorizonError, match=r"2026-01-01\.\.2026-08-07 leaves it"):
        write_daily_panel(
            store,
            bars=_bar_batches(),
            fundamentals=_valuation_batches(),
            calendar=_calendar(last_day=date(2026, 6, 30)),
        )


def test_the_census_stops_at_the_day_before_the_fetch(tmp_path: Path) -> None:
    """The upper bound is the day before `fetched_at`, in `date_timezone`, exactly as
    `adj_factor`'s is and for the same reason: a session publishes at 16:30 local, so a fetch
    earlier that day cannot hold it and requiring it would be a false alarm on every intraday
    run. Here the calendar declares a session on the fetch day itself, and the write succeeds
    without it."""
    store = _store(tmp_path)
    fetched_at = datetime(2026, 6, 16, 2, 0, tzinfo=UTC)  # 10:00 Asia/Shanghai on the 16th
    reference, _ = write_daily_panel(
        store,
        bars=_bar_batches(fetched_at=fetched_at),
        fundamentals=_valuation_batches(fetched_at=fetched_at),
        calendar=_calendar((*SESSIONS, "20260616")),
    )
    assert reference.row_count == 8


def test_a_rewrite_that_would_drop_a_security_is_refused(tmp_path: Path) -> None:
    """A partition is replaced whole, so a year rewritten from a narrower cross section
    destroys the securities it omits and reports success. Both sides are narrowed here, so the
    close cross-check has nothing to say and this guard is what stops the write."""
    store = _seeded(tmp_path)
    thin_bars = {day: [row for row in BARS[day] if row[0] == PING_AN] for day in SESSIONS}
    thin_valuations = {
        day: [row for row in VALUATIONS[day] if row[0] == PING_AN] for day in SESSIONS
    }
    with pytest.raises(PanelBatchError, match="writing it would drop"):
        write_daily_panel(
            store,
            bars=[
                _batch(DAILY_DATASET, DAILY_FIELDS, thin_bars[day], day, FETCHED_AT)
                for day in SESSIONS
            ],
            fundamentals=[
                _batch(
                    DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, thin_valuations[day], day, FETCHED_AT
                )
                for day in SESSIONS
            ],
            calendar=_calendar(),
        )


def test_a_rewrite_that_drops_a_security_from_the_valuations_alone_is_refused(
    tmp_path: Path,
) -> None:
    """The half of that guard the close cross-check provably cannot stand in for.

    `_refuse_close_disagreement` tolerates a bar with no valuation by design -- that is the
    measured shape of every pre-2024 year, where `daily_basic` omits the Beijing board -- so a
    `daily_basic` rewrite that silently loses a security is exactly the direction it waves
    through. The first half of this test shows it waving: the same narrow fundamentals write
    cleanly onto an empty store. The second half is the guard that stops it from replacing a
    partition that already had the name.
    """
    thin_valuations = {
        day: [row for row in VALUATIONS[day] if row[0] == PING_AN] for day in SESSIONS
    }
    narrow = [
        _batch(DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, thin_valuations[day], day, FETCHED_AT)
        for day in SESSIONS
    ]
    fresh = _store(tmp_path / "fresh")
    _, valuation_ref = write_daily_panel(
        fresh, bars=_bar_batches(), fundamentals=narrow, calendar=_calendar()
    )
    assert valuation_ref.row_count == 4  # accepted: the cross-check is blind to this direction

    seeded = _seeded(tmp_path / "seeded")
    with pytest.raises(
        PanelBatchError,
        match=r"daily_basic year=2026 already holds .*writing it would drop \['600519\.SH'\]",
    ):
        write_daily_panel(
            seeded,
            bars=_bar_batches(),
            fundamentals=[
                _batch(
                    DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, thin_valuations[day], day, FETCHED_AT
                )
                for day in SESSIONS
            ],
            calendar=_calendar(),
        )
    stored = store_valuations = load_daily_valuations(
        seeded, day=JUNE_12, calendar=_calendar(), as_of=AS_OF, max_staleness=None
    )
    assert sorted(stored) == [PING_AN, MAOTAI]
    assert store_valuations[MAOTAI].total_mv == 161499291.9856


def test_the_same_session_merged_into_a_year_twice_is_refused_rather_than_stored(
    tmp_path: Path,
) -> None:
    """A caller assembles a year from ~244 per-session batches; passing one of them twice is a
    loop bug, not an exotic input.

    Stored, it is worse than a wrong number: the year holds more rows than sessions, the write
    reports success, and every later read of that session fails in `daily_bars_from_panel_rows`
    with "appears twice" -- fail-closed, but unrepairable except by rewriting the year. The
    `close` cross-check could never catch it either, because its index is keyed by
    `(subject, session)` and a duplicate collapses into one entry.
    """
    store = _store(tmp_path)
    doubled = (*SESSIONS, "20260612")
    with pytest.raises(PanelBatchError, match=r"daily carries 000001\.SZ twice on 2026-06-12"):
        write_daily_panel(
            store,
            bars=_bar_batches(doubled),
            fundamentals=_valuation_batches(doubled),
            calendar=_calendar(),
        )
    assert store.read_coverage(DAILY_DATASET, 2026) is None
    assert store.read_coverage(DAILY_BASIC_DATASET, 2026) is None


# --- the thin-session census ------------------------------------------------------------------


def test_a_session_that_came_back_short_is_refused_even_though_the_year_looks_whole(
    tmp_path: Path,
) -> None:
    """The failure the *date* census is blind to, and the reason a row-count floor was added.

    One of four sessions returns 3 of 40 securities. Every other guard passes: the date census
    carries the session, the year's subject set is complete because the other three sessions
    supply all 40 names, and `_refuse_to_drop_stored_subjects` compares year subject sets and
    sees nothing. Left alone the partition stores 123 rows where 160 belong, `assess_readiness`
    reports ready with no issues, `load_daily_bars` on that day returns 3 bars with no error,
    and `priced_cross_section` reports 92.5% of the listed market as `unpriced` -- which is
    indistinguishable from a day on which 37 names were halted.
    """
    store = _store(tmp_path)
    sizes = dict.fromkeys(SESSIONS, _WIDE_MARKET)
    sizes["20260612"] = 3
    bars, fundamentals = _wide_batches(sizes)
    with pytest.raises(
        PanelBatchError,
        match=(
            r"daily carries 1 session\(s\) with fewer than 50% of this partition's median "
            r"cross section \(40 rows\): \['2026-06-12'\]\. 2026-06-12 has 3 row\(s\)"
        ),
    ):
        write_daily_panel(store, bars=bars, fundamentals=fundamentals, calendar=_calendar())
    assert store.read_coverage(DAILY_DATASET, 2026) is None
    assert store.read_coverage(DAILY_BASIC_DATASET, 2026) is None


def test_the_thin_session_census_runs_on_the_valuations_too(tmp_path: Path) -> None:
    """Both datasets, like the date census -- and it names which one is short."""
    store = _store(tmp_path)
    bars, _ = _wide_batches(dict.fromkeys(SESSIONS, _WIDE_MARKET))
    short_fundamentals = dict.fromkeys(SESSIONS, _WIDE_MARKET)
    short_fundamentals["20260615"] = 4
    _, fundamentals = _wide_batches(short_fundamentals)
    with pytest.raises(PanelBatchError, match=r"daily_basic carries 1 session\(s\) with fewer"):
        write_daily_panel(store, bars=bars, fundamentals=fundamentals, calendar=_calendar())


def test_a_session_the_market_merely_thinned_on_is_written_because_2015_happened(
    tmp_path: Path,
) -> None:
    """The residue, stated as an accepted write rather than only in prose.

    The floor is a share of the partition's own median because the market grew from 1,022 names
    in 2001 to 5,535 in 2026, and it is set *below* the worst real session because July 2015
    was real: on 2015-07-09 `daily` served 1,363 rows against that year's median of 2,359, a
    ratio of 0.578, when more than a third of the market was suspended at once. A check that
    refused that would refuse a true partition. So a session at 60% of its siblings is stored,
    and `suspend_d` (`V2-P1-008`) remains what settles a thin session from a halted one.
    """
    store = _store(tmp_path)
    sizes = dict.fromkeys(SESSIONS, _WIDE_MARKET)
    sizes["20260612"] = 24  # 0.60 of the median, inside 2015's measured 0.578
    bars, fundamentals = _wide_batches(sizes)
    bar_ref, valuation_ref = write_daily_panel(
        store, bars=bars, fundamentals=fundamentals, calendar=_calendar()
    )
    assert bar_ref.row_count == 3 * _WIDE_MARKET + 24
    assert valuation_ref.row_count == 3 * _WIDE_MARKET + 24


# --- the year the first nullability sample could not store -------------------------------------


def test_a_2017_year_whose_free_share_is_null_is_stored_and_read_back(tmp_path: Path) -> None:
    """The regression `DAILY_BASIC_NULLABLE_COLUMNS` was widened for, end to end.

    `300290.SZ` and `600637.SH` have a null `free_share` on both real 2017 sessions here.
    Refusing that null failed the whole cross section at the provider, so the session could not
    be built, the year was then missing a session the calendar reports open, and
    `write_daily_panel` refused **both** partitions -- 2017 and 2018 were unstorable outright.
    The six columns that stay non-nullable are read back as real numbers on the same rows.
    """
    store = _store(tmp_path)
    calendar = _calendar_2017()
    bar_ref, valuation_ref = write_daily_panel(
        store,
        bars=[
            _batch(DAILY_DATASET, DAILY_FIELDS, SPARSE_BARS_2017[day], day, FETCHED_AT_2017)
            for day in SESSIONS_2017
        ],
        fundamentals=[
            _batch(
                DAILY_BASIC_DATASET,
                DAILY_BASIC_FIELDS,
                SPARSE_VALUATIONS_2017[day],
                day,
                FETCHED_AT_2017,
            )
            for day in SESSIONS_2017
        ],
        calendar=calendar,
    )
    assert bar_ref.row_count == 4
    assert valuation_ref.row_count == 4

    valuations = load_daily_valuations(
        store, day=date(2017, 10, 9), calendar=calendar, as_of=AS_OF_2017, max_staleness=None
    )
    assert sorted(valuations) == ["300290.SZ", "600637.SH"]
    assert valuations["300290.SZ"].free_share is None
    assert valuations["300290.SZ"].total_mv == 363536.9364
    assert valuations["300290.SZ"].circ_mv == 146969.9054
    assert valuations["300290.SZ"].total_share == 32142.9652
    assert valuations["300290.SZ"].float_share == 12994.6866
    assert valuations["300290.SZ"].turnover_rate == 3.949
    assert valuations["600637.SH"].free_share is None

    bars = load_daily_bars(
        store, day=date(2017, 10, 9), calendar=calendar, as_of=AS_OF_2017, max_staleness=None
    )
    assert bars["300290.SZ"].close == 11.31
    assert close_disagreements(close_index(bars), close_index(valuations)) == ()


def test_a_write_that_straddles_two_years_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    december = {
        "20251231": [[PING_AN, "20251231", 11.0, 11.0, 11.0, 11.0, 11.0, 0.0, 0.0, 1.0, 1.0]]
    }
    with pytest.raises(PanelBatchError, match=r"spans \[2025, 2026\]"):
        write_daily_panel(
            store,
            bars=[
                *_bar_batches(),
                _batch(DAILY_DATASET, DAILY_FIELDS, december["20251231"], "20251231", FETCHED_AT),
            ],
            fundamentals=_valuation_batches(),
            calendar=_calendar(),
        )


def test_writing_the_wrong_dataset_on_either_side_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(PanelBatchError, match=r"expected the 'daily' dataset"):
        write_daily_panel(
            store,
            bars=_valuation_batches(),
            fundamentals=_valuation_batches(),
            calendar=_calendar(),
        )
    with pytest.raises(PanelBatchError, match=r"expected the 'daily_basic' dataset"):
        write_daily_panel(
            store, bars=_bar_batches(), fundamentals=_bar_batches(), calendar=_calendar()
        )


# --- the read-side requirement ---------------------------------------------------------------


def test_the_requirement_does_not_waive_date_coverage_and_that_is_the_difference_from_factors(
    tmp_path: Path,
) -> None:
    """The homomorph's one real divergence from `adj_factor`'s.

    `adjustment_requirement` **waives** `required_dates`, because compression leaves the
    partition holding only load-bearing sessions -- so the write-time census is *paying for*
    that waiver and nothing re-checks a partition written before the census existed. A price
    partition is stored uncompressed, so its date census survives and the same expectation can
    be stated on the read side, from the same calendar. Every read re-checks it, including of a
    partition this process never wrote.
    """
    store = _seeded(tmp_path)
    requirement = daily_requirement(_calendar(), years=(2026,), as_of=AS_OF, max_staleness=None)
    assert requirement.required_dates == (JUNE_10, JUNE_11, JUNE_12, JUNE_15)
    assert requirement.required_subjects is None
    assert requirement.dataset == DAILY_DATASET

    verdict = store.assess_readiness(requirement)
    assert verdict.is_ready
    assert set(verdict.checks_waived) == {"required_subjects", "max_staleness"}
    assert "required_dates" not in verdict.checks_waived

    basic = daily_basic_requirement(_calendar(), years=(2026,), as_of=AS_OF, max_staleness=None)
    assert basic.dataset == DAILY_BASIC_DATASET
    assert basic.required_dates == requirement.required_dates


def test_a_partition_with_a_session_hole_is_blocked_on_the_read_side_too(tmp_path: Path) -> None:
    """What the read-side expectation buys that the write-time census cannot: this partition was
    written through a calendar that did not know the 12th was a session, so the write guard had
    nothing to say. The read, holding the real calendar, blocks it."""
    store = _store(tmp_path)
    blind = _calendar(("20260610", "20260611", "20260615"))
    write_daily_panel(
        store,
        bars=_bar_batches(("20260610", "20260611", "20260615")),
        fundamentals=_valuation_batches(("20260610", "20260611", "20260615")),
        calendar=blind,
    )
    with pytest.raises(PanelStorageError, match="date_gap"):
        load_daily_bars(store, day=JUNE_11, calendar=_calendar(), as_of=AS_OF, max_staleness=None)


def test_the_required_range_stops_at_the_last_session_that_had_published(tmp_path: Path) -> None:
    """The read-side bound is the same publication instant the provider dates availability at:
    16:30 Asia/Shanghai. At 10:00 on 2026-06-15 the 15th's bars do not exist yet, so requiring
    them would report a permanent, entirely invented `date_gap`."""
    morning = datetime(2026, 6, 15, 2, 0, tzinfo=UTC)  # 10:00 Asia/Shanghai
    evening = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)  # 17:00 Asia/Shanghai
    assert daily_requirement(
        _calendar(), years=(2026,), as_of=morning, max_staleness=None
    ).required_dates == (JUNE_10, JUNE_11, JUNE_12)
    assert daily_requirement(
        _calendar(), years=(2026,), as_of=evening, max_staleness=None
    ).required_dates == (JUNE_10, JUNE_11, JUNE_12, JUNE_15)


def test_a_year_that_has_not_begun_is_refused_rather_than_answered_with_an_empty_expectation(
    tmp_path: Path,
) -> None:
    with pytest.raises(Exception, match="has not begun at as_of"):
        daily_requirement(_calendar(), years=(2027,), as_of=AS_OF, max_staleness=None)


def test_a_day_the_exchange_was_shut_is_refused_before_any_partition_is_read(
    tmp_path: Path,
) -> None:
    store = _seeded(tmp_path)
    with pytest.raises(PriceDataError, match="not an open session"):
        load_daily_bars(
            store, day=date(2026, 6, 13), calendar=_calendar(), as_of=AS_OF, max_staleness=None
        )


def test_a_stale_partition_blocks_when_the_caller_states_a_bound(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    with pytest.raises(PanelStorageError, match="'stale'"):
        load_daily_bars(
            store,
            day=JUNE_12,
            calendar=_calendar(),
            as_of=AS_OF,
            max_staleness=timedelta(days=30),
        )


def test_a_year_that_was_never_ingested_blocks_rather_than_answering_an_empty_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(PanelStorageError, match="partition_missing"):
        load_daily_bars(store, day=JUNE_12, calendar=_calendar(), as_of=AS_OF, max_staleness=None)


def test_a_year_fetched_on_its_own_first_day_is_written_and_the_gap_is_named(
    tmp_path: Path,
) -> None:
    """The one residue of the census's upper bound, exercised rather than only described. A
    batch fetched on 1 January of its own year has nothing the calendar can require -- no
    session of that year had closed when it ran -- exactly as `adj_factor`'s census has."""
    store = _store(tmp_path)
    new_year = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)  # 20:00 Asia/Shanghai, after the 16:30
    bar = [PING_AN, "20260101", 11.0, 11.0, 11.0, 11.0, 11.0, 0.0, 0.0, 1.0, 1.0]
    valuation = [
        PING_AN,
        "20260101",
        11.0,
        1.0,
        1.0,
        1.0,
        5.0,
        5.0,
        0.4,
        1.6,
        1.6,
        5.0,
        5.0,
        1940591.8198,
        1940560.0653,
        816048.1215,
        21812252.0568,
        21811895.1868,
    ]
    reference, _ = write_daily_panel(
        store,
        bars=[_batch(DAILY_DATASET, DAILY_FIELDS, [bar], "20260101", new_year)],
        fundamentals=[
            _batch(DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, [valuation], "20260101", new_year)
        ],
        calendar=_calendar(("20260101", *SESSIONS)),
    )
    assert reference.row_count == 1


def test_a_null_close_on_either_side_is_refused_before_the_cross_check_can_be_skipped(
    tmp_path: Path,
) -> None:
    """The cross-check compares `close` columns, so a null one is not a disagreement it can
    resolve -- it is the check being unable to run. Refusing beats passing vacuously, which is
    what `bar_close is None or valuation_close is None -> equal` would have done."""
    store = _store(tmp_path)
    holed = {day: [list(row) for row in VALUATIONS[day]] for day in SESSIONS}
    batches = _valuation_batches()
    first = batches[0]
    close_column = next(column for column in first.columns if column.name == "close")
    blanked = type(first)(
        provider_id=first.provider_id,
        dataset=first.dataset,
        kind=first.kind,
        as_of=first.as_of,
        fetched_at=first.fetched_at,
        status="success",
        subjects=first.subjects,
        timeline=first.timeline,
        columns=tuple(
            type(close_column)(column.name, column.kind, (None, close_column.values[1]))
            if column.name == "close"
            else column
            for column in first.columns
        ),
    )
    assert holed  # the fixture rows are untouched; only the batch's column is blanked
    with pytest.raises(PanelBatchError, match=r"000001\.SZ on 2026-06-10 has a null close"):
        write_daily_panel(
            store,
            bars=_bar_batches(),
            fundamentals=[blanked, *batches[1:]],
            calendar=_calendar(),
        )


def test_two_sides_that_belong_to_different_years_are_refused(tmp_path: Path) -> None:
    """A `daily` year cannot be stored beside a `daily_basic` year from another calendar year:
    the two partitions of one set of sessions would then disagree about which sessions exist."""
    store = _store(tmp_path)
    december = [
        [PING_AN, "20251231", 11.0, 11.0, 11.0, 11.0, 11.0, 0.0, 0.0, 1.0, 1.0],
        [MAOTAI, "20251231", 11.0, 11.0, 11.0, 11.0, 11.0, 0.0, 0.0, 1.0, 1.0],
    ]
    with pytest.raises(PanelBatchError, match="are year 2025 and the daily_basic ones are year"):
        write_daily_panel(
            store,
            bars=[_batch(DAILY_DATASET, DAILY_FIELDS, december, "20251231", FETCHED_AT)],
            fundamentals=_valuation_batches(),
            calendar=_calendar(),
        )
