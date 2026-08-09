"""`suspend_d` + `stk_limit` end to end: Tushare -> session batches -> partitions -> answers.

The whole chain runs against a real `PanelStore` on a real DuckDB catalog with real Parquet
files. Only the HTTP transport is doubled, and what it serves is real market data captured live
on 2026-08-09.

Three acceptances live here.

**The explanation one.** `V2-P1-007` recorded, as
`KNOWN_PRICE_LIMITATIONS.a_partial_cross_section_is_invisible_without_suspend_d`, that nothing
in `daily` separates "this security was halted" from "this fetch was short". A stored halt
corpus separates them, and `test_a_stored_halt_corpus_separates_a_halt_from_a_short_fetch` is
that answer running out of the store.

**The write-guard one.** The same fact, moved to write time.
`_refuse_unexplained_thin_sessions` refuses a session that came back with three quarters of the
market and no halts to show for it -- which `_refuse_thin_price_sessions`' 0.5 floor cannot,
because 2015-07-09 legitimately served 0.578 of its year's median.

**The execution one.** A band read back out of the store, handed to `AShareExecutionPolicy`,
changes a verdict: `920924.BJ` locked at its published 9.51 limit is a limit-up bar, and the
derived 30% rule computes 9.52 and fills the order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
    published_limit_fields,
)
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PricedCrossSection,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    TradingState,
    explain_unpriced,
    limit_touch,
)
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_daily_bars,
    load_price_limits,
    load_suspensions,
    write_daily_panel,
    write_price_limits,
    write_suspensions,
)
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TUSHARE_RESPONSE_TRUNCATION_FLAG, TushareProvider

SUSPEND_FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
LIMIT_FIELDS = ["trade_date", "ts_code", "up_limit", "down_limit"]
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

SESSIONS: tuple[str, ...] = ("20240626", "20240627", "20240628")
JUNE_28 = date(2024, 6, 28)
FETCHED_AT = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
AS_OF = FETCHED_AT

# Verbatim rows of suspend_d(trade_date=...) for the three sessions. 2024-06-28 served 28 rows
# in total -- 26 `S` and 2 `R` -- and the four `S` names below are the first four of the 26.
# fmt: off
HALTS: dict[str, list[list[Any]]] = {
    "20240626": [["000040.SZ", "20240626", None, "S"], ["000413.SZ", "20240626", None, "S"]],
    "20240627": [["000040.SZ", "20240627", None, "S"], ["000413.SZ", "20240627", None, "S"]],
    "20240628": [
        ["603050.SH", "20240628", None, "R"],
        ["000615.SZ", "20240628", None, "R"],
        ["000040.SZ", "20240628", None, "S"],
        ["000413.SZ", "20240628", None, "S"],
    ],
}

# Verbatim rows of stk_limit(trade_date=...). `920924.BJ` is the inward-rounding case and
# `603381.SH` -- listed 2024-06-26 -- is the limit-free one.
BANDS: dict[str, list[list[Any]]] = {
    "20240626": [
        ["20240626", "000001.SZ", 12.03, 9.85],
        ["20240626", "920924.BJ", 9.51, 5.13],
        ["20240626", "603381.SH", 99999.999, 0.01],
    ],
    "20240627": [
        ["20240627", "000001.SZ", 12.03, 9.85],
        ["20240627", "920924.BJ", 9.51, 5.13],
        ["20240627", "603381.SH", 99999.999, 0.01],
    ],
    "20240628": [
        ["20240628", "000001.SZ", 12.03, 9.85],
        ["20240628", "920924.BJ", 9.51, 5.13],
        ["20240628", "603381.SH", 99999.999, 0.01],
    ],
}

# One real bar per session for the two securities the execution acceptance needs. `920924.BJ`'s
# is constructed to be locked at the published limit; `000001.SZ`'s is its real 2024-06-28 bar.
BARS: dict[str, list[list[Any]]] = {
    day: [
        ["000001.SZ", day, 10.95, 11.10, 10.90, 11.05, 10.94, 0.11, 1.0, 100.0, 1000.0],
        ["920924.BJ", day, 9.51, 9.51, 9.51, 9.51, 7.32, 2.19, 29.9, 100.0, 1000.0],
    ]
    for day in SESSIONS
}
VALUATIONS: dict[str, list[list[Any]]] = {
    day: [
        ["000001.SZ", day, 11.05, 0.79, 1.89, 1.56, 5.15, 5.10, 0.47, 1.67, 1.65, 5.28, 5.28,
         1940591.8, 1940560.0, 816048.1, 21967499.4, 21967139.9],
        ["920924.BJ", day, 9.51, 0.31, 0.72, 1.12, 19.37, 19.28, 5.95, 9.44, 9.26, 4.05, 4.05,
         125008.1, 125008.1, 54094.8, 159495411.3, 159495411.3],
    ]
    for day in SESSIONS
}
# fmt: on


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


def _batch(dataset: str, fields: list[str], items: list[list[Any]], day: str) -> Any:
    provider = TushareProvider(
        token="secret-token",
        transport=_Transport(_response(fields, items)),
        clock=lambda: FETCHED_AT,
    )
    return provider.fetch_panel(ProviderRequest(dataset=dataset, as_of=_as_of(day)))


def _halt_batches(days: Sequence[str] = SESSIONS) -> list[Any]:
    return [_batch(SUSPENSION_DATASET, SUSPEND_FIELDS, HALTS[day], day) for day in days]


def _band_batches(days: Sequence[str] = SESSIONS) -> list[Any]:
    return [_batch(PRICE_LIMIT_DATASET, LIMIT_FIELDS, BANDS[day], day) for day in days]


def _bar_batches(days: Sequence[str] = SESSIONS) -> list[Any]:
    return [_batch(DAILY_DATASET, DAILY_FIELDS, BARS[day], day) for day in days]


def _valuation_batches(days: Sequence[str] = SESSIONS) -> list[Any]:
    return [_batch(DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, VALUATIONS[day], day) for day in days]


def _calendar(open_days: Iterable[str] = SESSIONS) -> TradingCalendar:
    first, last = date(2024, 1, 1), date(2024, 12, 31)
    opens = {date(int(day[:4]), int(day[4:6]), int(day[6:])) for day in open_days}
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


def _store(tmp_path: Path) -> PanelStore:
    return PanelStore(tmp_path / "panel")


def _seeded(tmp_path: Path) -> PanelStore:
    store = _store(tmp_path)
    calendar = _calendar()
    write_suspensions(store, _halt_batches())
    write_price_limits(store, _band_batches(), calendar=calendar)
    write_daily_panel(
        store, bars=_bar_batches(), fundamentals=_valuation_batches(), calendar=calendar
    )
    return store


# --- the explanation acceptance -------------------------------------------------------------


def test_a_stored_halt_corpus_separates_a_halt_from_a_short_fetch(tmp_path: Path) -> None:
    """The answer to `a_partial_cross_section_is_invisible_without_suspend_d`, run out of the
    store. `000040.SZ` has a whole-day halt on 2024-06-28 and `600519.SH` does not, so the two
    absences land in different tuples instead of both being `unpriced`."""
    store = _seeded(tmp_path)
    days = load_suspensions(store, years=(2024,), as_of=AS_OF, max_staleness=None)
    cross_section = PricedCrossSection(
        day=JUNE_28,
        exchange="SZSE",
        priced=(),
        unpriced=("000040.SZ", "600519.SH"),
        unlisted_bars=(),
    )

    explained = explain_unpriced(cross_section, days[JUNE_28])

    assert explained.halted == ("000040.SZ",)
    assert explained.unexplained == ("600519.SH",)
    assert explained.is_fully_explained is False


def test_the_two_resumptions_survive_the_round_trip_without_becoming_halts(
    tmp_path: Path,
) -> None:
    store = _seeded(tmp_path)
    days = load_suspensions(store, years=(2024,), as_of=AS_OF, max_staleness=None)

    assert days[JUNE_28].resumed == ("000615.SZ", "603050.SH")
    assert days[JUNE_28].halted == ("000040.SZ", "000413.SZ")
    assert days[JUNE_28].state_of("603050.SH") is TradingState.resumed


def test_the_halt_corpus_covers_every_session_it_was_fetched_for(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    days = load_suspensions(store, years=(2024,), as_of=AS_OF, max_staleness=None)

    assert sorted(days) == [date(2024, 6, 26), date(2024, 6, 27), JUNE_28]


def test_a_year_that_was_never_ingested_blocks_rather_than_answering_no_halts(
    tmp_path: Path,
) -> None:
    """The failure mode this reader is fail-closed about: a skipped year answers "nothing was
    ever halted" for every session in it, which is plausible, silent and completely wrong."""
    store = _seeded(tmp_path)

    with pytest.raises(PanelStorageError, match="partition_missing"):
        load_suspensions(store, years=(2023,), as_of=AS_OF, max_staleness=None)


# --- the write-guard acceptance -------------------------------------------------------------

_WIDE_MARKET = 40


def _wide_rows(template: list[Any], count: int, day: str) -> list[list[Any]]:
    return [[f"{600000 + index}.SH", day, *template[2:]] for index in range(count)]


def _wide_price_batches(sizes: Mapping[str, int]) -> tuple[list[Any], list[Any]]:
    bars = [
        _batch(DAILY_DATASET, DAILY_FIELDS, _wide_rows(BARS[day][0], sizes[day], day), day)
        for day in SESSIONS
    ]
    fundamentals = [
        _batch(
            DAILY_BASIC_DATASET,
            DAILY_BASIC_FIELDS,
            _wide_rows(VALUATIONS[day][0], sizes[day], day),
            day,
        )
        for day in SESSIONS
    ]
    return bars, fundamentals


def _wide_halt_batches(counts: Mapping[str, int]) -> list[Any]:
    """One `suspend_d` batch per session, halting the *last* `counts[day]` of the wide market.

    Halting the tail rather than the head is what makes the halted names disjoint from the ones
    that still have bars in the short session below -- which is the shape a real halt has.
    """
    batches = []
    for day in SESSIONS:
        rows = [
            [f"{600000 + _WIDE_MARKET - 1 - index}.SH", day, None, "S"]
            for index in range(counts[day])
        ]
        if not rows:
            rows = [[f"{600000 + _WIDE_MARKET - 1}.SH", day, None, "R"]]
        batches.append(_batch(SUSPENSION_DATASET, SUSPEND_FIELDS, rows, day))
    return batches


def test_a_session_that_came_back_three_quarters_full_is_refused_when_no_halt_explains_it(
    tmp_path: Path,
) -> None:
    """`_refuse_thin_price_sessions`' 0.5 floor passes this session (30 of a 40-name median is
    0.75) and has to, because 2015-07-09 really did serve 0.578 of its year's median. With the
    halts joined on there is no such session in the way, so the floor can sit at
    `MIN_EXPLAINED_SESSION_SHARE` and this becomes visible."""
    store = _store(tmp_path)
    sizes = {"20240626": 40, "20240627": 40, "20240628": 30}
    bars, fundamentals = _wide_price_batches(sizes)
    halts = _wide_halt_batches({day: 0 for day in SESSIONS})
    write_suspensions(store, halts)
    days = load_suspensions(store, years=(2024,), as_of=AS_OF, max_staleness=None)

    with pytest.raises(PanelBatchError, match="bars and halts together fall under"):
        write_daily_panel(
            store,
            bars=bars,
            fundamentals=fundamentals,
            calendar=_calendar(),
            halts=days,
        )


def test_the_same_session_is_accepted_once_the_halts_account_for_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sizes = {"20240626": 40, "20240627": 40, "20240628": 30}
    bars, fundamentals = _wide_price_batches(sizes)
    write_suspensions(store, _wide_halt_batches({"20240626": 0, "20240627": 0, "20240628": 10}))
    days = load_suspensions(store, years=(2024,), as_of=AS_OF, max_staleness=None)

    bar_ref, _ = write_daily_panel(
        store, bars=bars, fundamentals=fundamentals, calendar=_calendar(), halts=days
    )

    assert bar_ref.row_count == 110


def test_omitting_the_halts_leaves_the_earlier_behaviour_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    """The fail-open this issue accepts, pinned rather than left implicit: the same short
    session that guard 6 refuses is stored when `halts` is not supplied, because guard 3's floor
    is 0.5 and 30/40 clears it."""
    store = _store(tmp_path)
    bars, fundamentals = _wide_price_batches({"20240626": 40, "20240627": 40, "20240628": 30})

    bar_ref, _ = write_daily_panel(
        store, bars=bars, fundamentals=fundamentals, calendar=_calendar()
    )

    assert bar_ref.row_count == 110


def test_an_intraday_halt_does_not_excuse_a_missing_bar_at_write_time(tmp_path: Path) -> None:
    """`TradingState.interrupted` means the security traded. Counting it here would let a
    session with 1,300 intraday halts explain away 1,300 absent bars."""
    store = _store(tmp_path)
    bars, fundamentals = _wide_price_batches({"20240626": 40, "20240627": 40, "20240628": 30})
    timed = [
        [f"{600000 + _WIDE_MARKET - 1 - index}.SH", "20240628", "13:00-15:00", "S"]
        for index in range(10)
    ]
    batches = [
        _batch(SUSPENSION_DATASET, SUSPEND_FIELDS, [[f"{600039}.SH", day, None, "R"]], day)
        for day in SESSIONS[:2]
    ]
    batches.append(_batch(SUSPENSION_DATASET, SUSPEND_FIELDS, timed, "20240628"))
    write_suspensions(store, batches)
    days = load_suspensions(store, years=(2024,), as_of=AS_OF, max_staleness=None)

    assert len(days[JUNE_28].interrupted) == 10
    with pytest.raises(PanelBatchError, match="bars and halts together fall under"):
        write_daily_panel(
            store, bars=bars, fundamentals=fundamentals, calendar=_calendar(), halts=days
        )


def test_a_band_year_missing_a_session_the_calendar_reports_open_is_refused(
    tmp_path: Path,
) -> None:
    """The same census the price panel runs, and it is what refuses a pre-2007 year outright:
    `stk_limit` serves no rows at all before 2007-01-04."""
    store = _store(tmp_path)

    with pytest.raises(PanelBatchError, match="is missing 1 session"):
        write_price_limits(store, _band_batches(SESSIONS[:2]), calendar=_calendar())


def test_a_band_rewrite_that_drops_a_security_is_refused(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    narrowed = [_batch(PRICE_LIMIT_DATASET, LIMIT_FIELDS, BANDS[day][:2], day) for day in SESSIONS]

    with pytest.raises(PanelBatchError, match="would drop"):
        write_price_limits(store, narrowed, calendar=_calendar())


# --- the execution acceptance ---------------------------------------------------------------


def test_a_band_read_back_out_of_the_store_changes_an_execution_verdict(
    tmp_path: Path,
) -> None:
    """The whole chain: a `stk_limit` row stored as a partition, read back as a `PriceLimit`,
    converted to `MarketBar` fields, and handed to the policy. `920924.BJ` traded at exactly
    9.51 all session -- its published limit -- and the derived 30% rule computes 9.52, so
    without the band the buy fills at a price no buyer could have got."""
    store = _seeded(tmp_path)
    calendar = _calendar()
    bars = load_daily_bars(store, day=JUNE_28, calendar=calendar, as_of=AS_OF, max_staleness=None)
    limits = load_price_limits(
        store, day=JUNE_28, calendar=calendar, as_of=AS_OF, max_staleness=None
    )
    bar, limit = bars["920924.BJ"], limits["920924.BJ"]

    touch = limit_touch(bar, limit)
    assert touch.one_price_up is True
    assert touch.closed_at_up is True

    shared: dict[str, Any] = {
        "subject": bar.ts_code,
        "trade_date": bar.trade_date,
        "board": "bse",
        "previous_close": Decimal(str(bar.pre_close)),
        "open": Decimal(str(bar.open)),
        "high": Decimal(str(bar.high)),
        "low": Decimal(str(bar.low)),
        "close": Decimal(str(bar.close)),
        "suspended": False,
        "is_st": False,
    }
    policy = AShareExecutionPolicy()
    buy = ExecutionRequest(side="buy", quantity=100)

    derived = policy.execute(buy, MarketBar.model_validate(shared))
    published = policy.execute(
        buy, MarketBar.model_validate({**shared, **published_limit_fields(limit)})
    )

    assert derived.status == "filled"
    assert published.status == "rejected"
    assert published.reason == "buy cannot fill on a one-price limit-up bar"


def test_the_limit_free_row_survives_storage_as_a_sentinel_rather_than_a_null(
    tmp_path: Path,
) -> None:
    """`603381.SH` listed 2024-06-26 and had no band for its first five sessions. Nulling the
    sentinel anywhere in the chain would destroy the only statement that it was unbounded."""
    store = _seeded(tmp_path)
    limits = load_price_limits(
        store, day=JUNE_28, calendar=_calendar(), as_of=AS_OF, max_staleness=None
    )

    assert limits["603381.SH"].up_limit == 99999.999
    assert limits["603381.SH"].down_limit == 0.01
    assert limits["603381.SH"].is_bounded(29.70) is False
