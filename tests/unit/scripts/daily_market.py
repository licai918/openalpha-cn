"""A scripted Tushare transport over a generated market, for the daily command's tests
(`V2-P6-011`).

Beside `research_repo.py` for its reason: the daily command's test modules share it, and a copy in
each would let two of them test against two different markets. Everything is generated at test
time; nothing is checked in and nothing reaches the network.

The market is `SECURITIES` (plus one security delisted before the frame, so the registry holds a
partition in the frame's year, as a real one does) over `open_days`: closes follow a deterministic
walk of at most 2% a session, so a one-session reversal ranks the market differently every day.
The transport answers every target the daily command asks for -- the price targets per session,
the calendar per year, the registry, the three index series, the index weights, the name
history, the four statement sweeps (`*_vip`) and the SW2021 industry tree and memberships -- and
records each request's `api_name` and parameters. What it publishes can be moved on:

- `disputed`: `DISPUTED_CODE`'s adjustment factor steps on that session while its published
  `pre_close` does not, so the doctor's `return_paths` check reports it;
- `bands_only`: codes that are published a band on that session and nothing else (the shape of
  the funds Tushare withdrew from a stored session in the live check);
- `today` with `reclassified` and `first_assigned`: industry memberships that change on a day --
  published only once `today` has reached it, as an upstream publishes a reclassification.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.financial_statements import (
    FINANCIAL_INDICATOR_DATASET,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET, INDEX_PRICE_INDEX_CODES
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_TREE_DATASET,
)
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET

SECURITIES: Final[tuple[str, ...]] = tuple(f"{600100 + index}.SH" for index in range(12))
DISPUTED_CODE: Final[str] = SECURITIES[0]
DELISTED_ROW: Final[tuple[Any, ...]] = (
    "600199.SH",
    "600199.SH",
    "SSE",
    "主板",
    "D",
    "20100104",
    "20260102",
)
"""Delisted on 2 January 2026, before either frame: the registry's partitions are keyed by
lifecycle year, and a real registry has a partition for the current year for the same reason."""
RETIRED_ROWS: Final[tuple[tuple[Any, ...], ...]] = tuple(
    (
        f"6002{year % 100:02d}.SH",
        f"6002{year % 100:02d}.SH",
        "SSE",
        "主板",
        "D",
        "20100104",
        f"{year}0601",
    )
    for year in range(2011, 2026)
)
"""One security delisted in each year from 2011 to 2025. A registry read covers every lifecycle
year from its oldest listing on, and a year with no event is a year the reader cannot tell from
a missing one -- a real registry has events every year."""
UNREGISTERED_FILER: Final[str] = "900009.SH"
"""Files in every statement window and is in no registry: a closed whole-market window that
answers nothing is refused, and a real market has no such window. The sweep drops it."""

L1_CODES: Final[tuple[str, ...]] = ("801010.SI", "801030.SI")
"""Two level-one industries, so a membership sweep here is four requests (the live tree's 31
make it 62): the count is two per level-one code, whatever the code count is."""

ASSIGNED_FROM: Final[str] = "20220104"
"""Every standing membership's `in_date`: inside the SW2021 era."""
SW2021_FROM: Final[str] = "20211213"
SW2021_UNTIL: Final[str] = "20220103"
"""The closed assignment every standing one replaced."""

CALENDAR_FIELDS: Final = ["exchange", "cal_date", "is_open", "pretrade_date"]
REGISTRY_FIELDS: Final = [
    "ts_code",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
]
BAR_FIELDS: Final = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "vol",
    "amount",
]
VALUATION_EXTRA: Final = [
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
VALUATION_FIELDS: Final = ["ts_code", "trade_date", "close", *VALUATION_EXTRA]
HALT_FIELDS: Final = ["ts_code", "trade_date", "suspend_type", "suspend_timing"]
LIMIT_FIELDS: Final = ["ts_code", "trade_date", "up_limit", "down_limit"]
FACTOR_FIELDS: Final = ["ts_code", "trade_date", "adj_factor"]
INDEX_DAILY_FIELDS: Final = [
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
INDEX_WEIGHT_FIELDS: Final = ["index_code", "con_code", "trade_date", "weight"]
NAMECHANGE_FIELDS: Final = [
    "ts_code",
    "name",
    "start_date",
    "end_date",
    "ann_date",
    "change_reason",
]
INDEX_CLASSIFY_FIELDS: Final = [
    "index_code",
    "industry_name",
    "level",
    "industry_code",
    "is_pub",
    "parent_code",
    "src",
]
INDEX_MEMBER_FIELDS: Final = ["ts_code", "l1_code", "l2_code", "l3_code", "in_date", "out_date"]
SWEEP: Final[str] = "_vip"


def weekdays(first: date, last: date, *, closed: Sequence[date] = ()) -> tuple[date, ...]:
    """Every weekday from `first` through `last`, less `closed`."""
    days: list[date] = []
    day = first
    while day <= last:
        if day.weekday() < 5 and day not in closed:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _parse(text: object) -> date:
    return datetime.strptime(str(text), "%Y%m%d").date()


def response(
    fields: Sequence[str], items: Sequence[Sequence[Any]], *, has_more: bool = False
) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": list(fields), "items": [list(i) for i in items], "has_more": has_more},
    }


def statement_fields(dataset: str) -> list[str]:
    """One statement endpoint's response shape, off the domain's own column list."""
    keys = ["ts_code", "end_date", "ann_date"]
    if dataset != FINANCIAL_INDICATOR_DATASET:
        keys.extend(["f_ann_date", "update_flag"])
    return [*keys, *STATEMENT_DATA_COLUMNS[dataset]]


def _tree_items(taxonomy: str) -> list[list[Any]]:
    """One vintage's tree: each level-one industry with an L2 and an L3 beneath it."""
    items: list[list[Any]] = []
    for position, level_one in enumerate(L1_CODES, start=1):
        root = f"{position}10000"
        items.append([level_one, f"industry{position}", "L1", root, "1", "0", taxonomy])
        items.append(
            [
                f"{level_one[:5]}1.SI",
                f"industry{position}-2",
                "L2",
                f"{root[:2]}1000",
                "1",
                root,
                taxonomy,
            ]
        )
        items.append(
            [
                f"{level_one[:5]}2.SI",
                f"industry{position}-3",
                "L3",
                f"{root[:2]}1100",
                "1",
                f"{root[:2]}1000",
                taxonomy,
            ]
        )
    return items


@dataclass
class Market:
    """A scripted `TushareTransport`; see the module docstring."""

    open_days: tuple[date, ...]
    disputed: date | None = None
    bands_only: dict[date, tuple[str, ...]] = field(default_factory=dict)
    today: date | None = None
    reclassified: tuple[tuple[str, date, str], ...] = ()
    """`(code, day, new level-one code)`: from `day` the code sits in the new industry."""
    first_assigned: tuple[tuple[str, date], ...] = ()
    delisted: tuple[tuple[str, date], ...] = ()
    """`(code, day)`: the code's last bar is the session before `day`, and from `today` on `day`
    the registry reports it delisted on `day`."""
    """`(code, day)`: the code has no membership until `day`, then the first level-one code."""
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        closes: dict[str, list[float]] = {}
        for index, code in enumerate(SECURITIES):
            path, previous = [], 10.0 + index
            for position in range(len(self.open_days)):
                step = ((index * 37 + position * 101) % 17 - 8) / 400
                previous = round(previous * (1 + step), 2)
                path.append(previous)
            closes[code] = path
        self._closes = closes

    # -- what was asked -----------------------------------------------------------------------

    @property
    def payloads(self) -> list[str]:
        """Every request's `api_name`, in order."""
        return [name for name, _params in self.requests]

    def asked(self, api_name: str) -> list[dict[str, Any]]:
        return [params for name, params in self.requests if name == api_name]

    def clear(self) -> None:
        self.requests.clear()

    # -- prices -------------------------------------------------------------------------------

    def _close(self, code: str, day: date) -> float:
        return self._closes[code][self.open_days.index(day)]

    def _pre_close(self, code: str, day: date) -> float:
        position = self.open_days.index(day)
        return self._closes[code][position - 1] if position else 10.0 + SECURITIES.index(code)

    def _factor(self, code: str, day: date) -> float:
        return 1.1 if code == DISPUTED_CODE and self.disputed and day >= self.disputed else 1.0

    def _trading(self, day: date) -> tuple[str, ...]:
        return tuple(
            code
            for code in SECURITIES
            if not any(code == gone and day >= on for gone, on in self.delisted)
        )

    def _session_rows(self, api_name: str, day: date) -> tuple[list[str], list[list[Any]]]:
        rows: list[list[Any]] = []
        rows_for = self._trading(day)
        if api_name == DAILY_DATASET:
            for code in rows_for:
                close, pre_close = self._close(code, day), self._pre_close(code, day)
                pct_chg = round((close / pre_close - 1) * 100, 2)
                prices = [close, close, close, close, pre_close, pct_chg]
                rows.append([code, compact(day), *prices, 1000.0, 50000.0])
            return BAR_FIELDS, rows
        if api_name == DAILY_BASIC_DATASET:
            return VALUATION_FIELDS, [
                [code, compact(day), self._close(code, day), *([1.0] * len(VALUATION_EXTRA))]
                for code in rows_for
            ]
        if api_name == SUSPENSION_DATASET:
            # A resumption on each year's first session: a year of `suspend_d` with no row at all
            # is not a partition, and a real market has none.
            if day == next(open_day for open_day in self.open_days if open_day.year == day.year):
                rows = [[SECURITIES[-1], compact(day), "R", None]]
            return HALT_FIELDS, rows
        if api_name == PRICE_LIMIT_DATASET:
            rows = [
                [
                    code,
                    compact(day),
                    round(self._pre_close(code, day) * 1.1, 2),
                    round(self._pre_close(code, day) * 0.9, 2),
                ]
                for code in rows_for
            ]
            rows += [[code, compact(day), 1.1, 0.9] for code in self.bands_only.get(day, ())]
            return LIMIT_FIELDS, rows
        if api_name == ADJ_FACTOR_DATASET:
            return FACTOR_FIELDS, [
                [code, compact(day), self._factor(code, day)] for code in rows_for
            ]
        raise AssertionError(f"unscripted session dataset {api_name}")

    # -- the rest -----------------------------------------------------------------------------

    def _calendar(self, params: Mapping[str, Any]) -> list[list[Any]]:
        year = int(str(params["start_date"])[:4])
        items: list[list[Any]] = []
        earlier = [day for day in self.open_days if day.year < year]
        previous: str | None = compact(earlier[-1]) if earlier else None
        day = date(year, 1, 1)
        while day.year == year:
            is_open = day in self.open_days
            items.append([params["exchange"], compact(day), 1 if is_open else 0, previous])
            if is_open:
                previous = compact(day)
            day += timedelta(days=1)
        return items

    def _index_levels(self, params: Mapping[str, Any]) -> list[list[Any]]:
        code = str(params["ts_code"])
        first, last = _parse(params["start_date"]), _parse(params["end_date"])
        base = 4000.0 + 100.0 * INDEX_PRICE_INDEX_CODES.index(code)
        rows = []
        for position, day in enumerate(self.open_days):
            if first <= day <= last:
                level = base + position
                bar = [level, level + 5.0, level - 5.0, level + 1.0, level, 1.0]
                change = round(1.0 / level * 100.0, 4)
                rows.append([code, compact(day), *bar, change, 300000.0, 900000.0])
        return rows

    def _index_weights(self, params: Mapping[str, Any]) -> list[list[Any]]:
        first, last = _parse(params["start_date"]), _parse(params["end_date"])
        month = [day for day in self.open_days if first <= day <= last]
        if not month:
            return []
        day = compact(month[-1])
        return [
            [params["index_code"], SECURITIES[0], day, 60.0],
            [params["index_code"], SECURITIES[1], day, 40.0],
        ]

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        start = self.open_days[0]
        if str(params["start_date"])[:4] != str(start.year):
            return []
        return [[code, code, compact(start), None, compact(start), "改名"] for code in SECURITIES]

    def _filings(self, dataset: str) -> list[tuple[str, str, str, list[float]]]:
        """Each registered security's filings: the previous year's three interim reports,
        announced that year, its annual report, announced on 15 January, and the frame year's
        first quarter, announced at the end of April. `(code, period end, announcement,
        values)`."""
        year = self.open_days[-1].year
        width = len(STATEMENT_DATA_COLUMNS[dataset])
        filings = []
        for index, code in enumerate(SECURITIES):
            values = [1.0e8 * (index + 1) + column for column in range(width)]
            for period, announced in (("0331", "0428"), ("0630", "0828"), ("0930", "1028")):
                filings.append((code, f"{year - 1}{period}", f"{year - 1}{announced}", values))
            # Announced every other day from 15 January, so the partition's newest row is
            # weeks, not months, behind a February clock -- as a real market's always is.
            announced = date(year, 1, 15) + timedelta(days=2 * index)
            filings.append((code, f"{year - 1}1231", compact(announced), values))
            filings.append((code, f"{year}0331", f"{year}0428", values))
        return filings

    def _sweep(self, dataset: str, params: Mapping[str, Any]) -> list[list[Any]]:
        rows: list[list[Any]] = []
        for code, period, announced, values in self._filings(dataset):
            inside = (
                period == str(params["period"])
                if "period" in params
                else str(params["start_date"]) <= announced <= str(params["end_date"])
            )
            if inside:
                keys: list[Any] = [code, period, announced]
                if dataset != FINANCIAL_INDICATOR_DATASET:
                    keys.extend([announced, "1"])
                rows.append([*keys, *values])
        if "period" in params:
            period = str(params["period"])
            filed = compact(_parse(period) + timedelta(days=1))
        else:
            period, filed = f"{str(params['start_date'])[:4]}0101", str(params["start_date"])
        keys = [UNREGISTERED_FILER, period, filed]
        if dataset != FINANCIAL_INDICATOR_DATASET:
            keys.extend([filed, "1"])
        return [*rows, [*keys, *([1.0] * len(STATEMENT_DATA_COLUMNS[dataset]))]]

    def _memberships(self, params: Mapping[str, Any]) -> list[list[Any]]:
        level_one, state = str(params["l1_code"]), str(params["is_new"])
        today = self.today or self.open_days[-1]
        changes = {code: (day, new) for code, day, new in self.reclassified if day <= today}
        unassigned = {code for code, day in self.first_assigned if day > today}
        rows: list[list[Any]] = []
        for index, code in enumerate(SECURITIES):
            if code in unassigned:
                continue
            first_day = next((day for c, day in self.first_assigned if c == code), None)
            standing = L1_CODES[index % 2] if first_day is None else L1_CODES[0]
            since = ASSIGNED_FROM if first_day is None else compact(first_day)
            change = changes.get(code)
            chain = f"{level_one[:5]}"
            # The SW2021 assignment each security held before its standing one: a closed history
            # in the other industry, as every real membership corpus has one.
            earlier = L1_CODES[(L1_CODES.index(standing) + 1) % 2]
            if state == "N" and earlier == level_one and first_day is None:
                rows.append(
                    [code, level_one, f"{chain}1.SI", f"{chain}2.SI", SW2021_FROM, SW2021_UNTIL]
                )
            if change is None:
                if state == "Y" and standing == level_one:
                    rows.append([code, level_one, f"{chain}1.SI", f"{chain}2.SI", since, ""])
                continue
            day, new = change
            if state == "Y" and new == level_one:
                rows.append([code, level_one, f"{chain}1.SI", f"{chain}2.SI", compact(day), ""])
            if state == "N" and standing == level_one:
                closed = compact(day - timedelta(days=1))
                rows.append([code, level_one, f"{chain}1.SI", f"{chain}2.SI", since, closed])
        return rows

    # -- the transport ------------------------------------------------------------------------

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_name = str(payload["api_name"])
        params: Mapping[str, Any] = payload["params"]
        self.requests.append((api_name, dict(params)))
        if api_name == TRADING_CALENDAR_DATASET:
            return response(CALENDAR_FIELDS, self._calendar(params))
        if api_name == STOCK_BASIC_DATASET:
            today = self.today or self.open_days[-1]
            gone = {code: day for code, day in self.delisted if day <= today}
            listed = [
                [code, code, "SSE", "主板", "D", "20100104", compact(gone[code])]
                if code in gone
                else [code, code, "SSE", "主板", "L", "20100104", None]
                for code in SECURITIES
            ]
            retired = [list(row) for row in RETIRED_ROWS]
            return response(REGISTRY_FIELDS, [*listed, *retired, list(DELISTED_ROW)])
        if api_name == INDEX_DAILY_DATASET:
            return response(INDEX_DAILY_FIELDS, self._index_levels(params))
        if api_name == INDEX_WEIGHT_DATASET:
            return response(INDEX_WEIGHT_FIELDS, self._index_weights(params))
        if api_name == NAMECHANGE_DATASET:
            return response(NAMECHANGE_FIELDS, self._names(params))
        if api_name == INDUSTRY_TREE_DATASET:
            return response(INDEX_CLASSIFY_FIELDS, _tree_items(str(params["src"])))
        if api_name == INDUSTRY_MEMBERSHIP_DATASET:
            return response(INDEX_MEMBER_FIELDS, self._memberships(params))
        if api_name.removesuffix(SWEEP) in STATEMENT_DATA_COLUMNS and api_name.endswith(SWEEP):
            dataset = api_name.removesuffix(SWEEP)
            return response(statement_fields(dataset), self._sweep(dataset, params))
        day = _parse(params["trade_date"])
        fields, rows = (
            self._session_rows(api_name, day)
            if day in self.open_days
            else (
                self._session_rows(api_name, self.open_days[0])[0],
                [],
            )
        )
        if "ts_code" in params:
            rows = [row for row in rows if row[0] == params["ts_code"]]
        return response(fields, rows)
