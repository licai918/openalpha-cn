"""`V2-P6-013`: upstream defects are recorded and handled by named rules, not a refused year.

Every test here is offline. `panel build` is driven through the real Typer app and the real
`TushareProvider`, with a scripted transport injected at `cli._panel_transport`, so the decoder,
the point-in-time filter, the targeted re-fetch and every write-time guard run for real; the
fixtures are generated here at test time.

The frame reproduces the three measured shapes (live, 2026-09-26) on a five-session, twenty-security
2013 calendar:

- `000022.SZ` has a `daily_basic` row and no `daily` bar on 2013-11-14 (`valuation_without_bar`);
- `002357.SZ` closes at 6.8 in `daily` and 6.62 in `daily_basic` on 2013-11-13, and 6.62 is its
  previous session's close while the next session's `pre_close` is 6.8
  (`valuation_contradicts_corroborated_bar`, the stale shape);
- `000509.SZ` is halted all of 2013-11-13, has no bar, and `stk_limit` publishes 0.0/0.0 for it
  (`limit_placeholder_on_halt`);
- `600001.SH` on 2013-11-13 closes at 10.0 in `daily` and 10.03 in `daily_basic`, a close equal
  to neither the bar nor the previous session (2020-10-23's shape, the controller's correction),
  while the next session's `pre_close` corroborates the bar.

The twenty securities are there so a one-row difference is not a thin session: the explained-share
guard refuses a session under 85% of its neighbours, and two dropped rows of three would be.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import typer
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_BASIC_PANEL_COLUMNS,
    DAILY_DATASET,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    PRICE_LIMIT_PANEL_COLUMNS,
    SUSPENSION_DATASET,
)
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    build_trading_calendar,
)
from openalpha_cn.domain.upstream_defects import (
    UpstreamDefect,
    close_disagreement_kind,
    limit_placeholder_kind,
    repeats_previous_close,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import panel_health_report
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
    load_upstream_defects,
    reconcile_price_disagreements,
    write_price_limits,
    write_upstream_defects,
)
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import TushareProvider

runner = CliRunner()

SECRET_TOKEN = "sk-upstream-defects-token-must-not-leak-61013"
YEAR = 2013
SESSIONS: tuple[date, ...] = tuple(date(2013, 11, day) for day in (11, 12, 13, 14, 15))
AS_OF = "2026-09-26T12:00:00+08:00"
CLOCK = datetime(2026, 9, 26, 4, 0, tzinfo=UTC)
AS_OF_INSTANT = datetime(2026, 9, 26, 4, 0, tzinfo=UTC)

NO_BAR = "000022.SZ"
NO_BAR_DAY = date(2013, 11, 14)
STALE = "002357.SZ"
STALE_DAY = date(2013, 11, 13)
HALTED = "000509.SZ"
HALT_DAY = date(2013, 11, 13)
RESUMED = "600006.SH"
FILLERS: tuple[str, ...] = tuple(f"{600000 + index}.SH" for index in range(17))
SECURITIES: tuple[str, ...] = (NO_BAR, HALTED, STALE, *FILLERS)

# `V2-P6-013` round 2: `920476.BJ` traded on another venue years before it listed (2022-10-14),
# and the upstream back-maps those trades onto today's code -- sparse bars, the first with a null
# `pre_close` and `pct_chg`. Here: a null-field bar on the 13th and an ordinary one on the 14th.
PRELISTED = "920476.BJ"
PRELISTED_DAYS: tuple[date, ...] = (date(2013, 11, 13), date(2013, 11, 14))
LIST_DATE_BY_SCENARIO: Mapping[str, str | None] = {
    "before": "20221014",
    "after": "20131101",
    "absent": None,
}
REGISTRY_FIELDS = [
    "ts_code",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
]
FACTOR_FIELDS = ["ts_code", "trade_date", "adj_factor"]

# `002357.SZ`'s own measured shape, moved onto this frame's sessions: 6.62 through the 12th, a
# 2.72% day to 6.8 on the 13th, and 6.8 after it.
STALE_CLOSES: Mapping[date, float] = {
    SESSIONS[0]: 6.62,
    SESSIONS[1]: 6.62,
    SESSIONS[2]: 6.8,
    SESSIONS[3]: 6.8,
    SESSIONS[4]: 6.8,
}
STALE_PRE_CLOSES: Mapping[date, float] = {
    SESSIONS[0]: 6.62,
    SESSIONS[1]: 6.62,
    SESSIONS[2]: 6.62,
    SESSIONS[3]: 6.8,
    SESSIONS[4]: 6.8,
}

CALENDAR_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
BAR_FIELDS = [
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
VALUATION_EXTRA = [
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
VALUATION_FIELDS = ["ts_code", "trade_date", "close", *VALUATION_EXTRA]
HALT_FIELDS = ["ts_code", "trade_date", "suspend_type", "suspend_timing"]
LIMIT_FIELDS = ["ts_code", "trade_date", "up_limit", "down_limit"]


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _response(fields: Sequence[str], items: Sequence[Sequence[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(fields),
            "items": [list(item) for item in items],
            "has_more": False,
        },
    }


@dataclass
class Frame:
    """Which of the measured defects the scripted upstream publishes, and how."""

    valuation_without_bar: bool = False
    stale_valuation: bool = False
    limit_placeholder: bool = False
    halted_on_placeholder_day: bool = True
    upper_limit_only_zero: bool = False
    contradicted_valuation: bool = False
    refetch_differs: bool = False
    uncorroborated_mismatch: bool = False
    pre_listing: str | None = None
    """`None` for no `920476.BJ` at all, else a key of `LIST_DATE_BY_SCENARIO`."""
    prelisted_nulls: tuple[str, ...] = ("pre_close", "pct_chg")
    sessions: tuple[date, ...] = SESSIONS
    """The calendar's open sessions; a later build may add one after `SESSIONS[-1]`."""
    mismatch_day: date | None = None
    """`FILLERS[2]` closes at 10.0 in `daily` and 10.07 in `daily_basic` on this session."""
    next_pre_close_disagrees: bool = False
    """`FILLERS[2]`'s bar on the session after `mismatch_day` has a `pre_close` of 10.5."""
    second_no_bar: bool = False
    """`FILLERS[3]` has no bar on `NO_BAR_DAY` either, so that session re-fetches whole."""
    session_refetch: str | None = None
    """How a whole-session `daily` re-fetch answers: `None` (in full), `empty` or `short`."""
    mismatch_code: str = FILLERS[2]
    """The security `mismatch_day` and `next_pre_close_disagrees` are about."""
    halted_into_year_end: bool = False
    also_absent_days: tuple[date, ...] = ()
    """More sessions on which `FILLERS[5]` has no bar and no valuation."""
    also_halted_days: tuple[date, ...] = ()
    """More sessions on which `suspend_d` has `FILLERS[5]` halted all day."""
    delisted_code: str | None = None
    """A security the registry has delisted on 2013-12-31, with no bar after 2013."""
    """`FILLERS[5]` is halted all of `SESSIONS[-1]`, so its last 2013 bar is on `SESSIONS[-2]`."""


class ScriptedUpstream:
    """A `TushareTransport` answering from a `Frame`, recording every payload it was sent."""

    def __init__(self, frame: Frame) -> None:
        self.frame = frame
        self.payloads: list[dict[str, Any]] = []
        self.served: set[tuple[str, str]] = set()

    def refetches(self) -> list[tuple[str, str, str]]:
        """Every per-security request, as `(api_name, ts_code, trade_date)`."""
        return [
            (str(p["api_name"]), str(p["params"]["ts_code"]), str(p["params"]["trade_date"]))
            for p in self.payloads
            if "ts_code" in p["params"]
        ]

    def _close(self, code: str, day: date) -> float:
        if code == STALE:
            return STALE_CLOSES.get(day, 6.8)
        return 13.75 if code == NO_BAR else 10.0

    def _pre_close(self, code: str, day: date) -> float:
        if self.frame.uncorroborated_mismatch and code == FILLERS[0] and day == SESSIONS[3]:
            return 10.5
        mismatch = self.frame.mismatch_day
        if (
            self.frame.next_pre_close_disagrees
            and mismatch is not None
            and code == self.frame.mismatch_code
            and day > mismatch
            and day
            == min(
                later
                for later in self.frame.sessions
                if later > mismatch and self._traded(code, later)
            )
        ):
            return 10.5
        return STALE_PRE_CLOSES.get(day, 6.8) if code == STALE else self._close(code, day)

    def _traded(self, code: str, day: date) -> bool:
        if self.frame.halted_into_year_end and code == FILLERS[5] and day == SESSIONS[-1]:
            return False
        if code == FILLERS[5] and day in self.frame.also_absent_days:
            return False
        if code == self.frame.delisted_code and day.year > YEAR:
            return False
        return not (self.frame.limit_placeholder and code == HALTED and day == HALT_DAY)

    def _prelisted(self, day: date) -> bool:
        return self.frame.pre_listing is not None and day in PRELISTED_DAYS

    def _bars(self, day: date, *, refetch: bool) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if self._prelisted(day):
            first = day == PRELISTED_DAYS[0]
            nulls = self.frame.prelisted_nulls if first else ()
            pre_close = None if "pre_close" in nulls else 18.0
            pct_chg = None if "pct_chg" in nulls else 0.0
            rows.append(
                [PRELISTED, _compact(day), 18.0, 18.0, 18.0, 18.0, pre_close, pct_chg, 300.0, 540.0]
            )
        for code in SECURITIES:
            if not self._traded(code, day):
                continue
            absent = self.frame.valuation_without_bar and code == NO_BAR and day == NO_BAR_DAY
            absent = absent or (
                self.frame.second_no_bar and code == FILLERS[3] and day == NO_BAR_DAY
            )
            if absent and not (refetch and self.frame.refetch_differs):
                continue
            if refetch and self.frame.session_refetch == "short" and code == FILLERS[4]:
                continue
            close, pre_close = self._close(code, day), self._pre_close(code, day)
            pct_chg = round((close / pre_close - 1) * 100, 2)
            rows.append(
                [code, _compact(day), close, close, close, close, pre_close, pct_chg, 10.0, 100.0]
            )
        return rows

    def _valuation_close(self, code: str, day: date, *, refetch: bool) -> float:
        if self.frame.stale_valuation and code == STALE and day == STALE_DAY:
            return 6.63 if refetch and self.frame.refetch_differs else 6.62
        if self.frame.uncorroborated_mismatch and code == FILLERS[0] and day == STALE_DAY:
            return 11.5
        if self.frame.contradicted_valuation and code == FILLERS[1] and day == STALE_DAY:
            return 10.03
        if code == self.frame.mismatch_code and day == self.frame.mismatch_day:
            return 10.07
        return self._close(code, day)

    def _valuations(self, day: date, *, refetch: bool) -> list[list[Any]]:
        return [
            [
                code,
                _compact(day),
                self._valuation_close(code, day, refetch=refetch),
                *([1.0] * len(VALUATION_EXTRA)),
            ]
            for code in SECURITIES
            if self._traded(code, day)
        ]

    def _halts(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if day == min(open_day for open_day in self.frame.sessions if open_day.year == day.year):
            rows.append([RESUMED, _compact(day), "R", None])
        if self.frame.halted_into_year_end and day == SESSIONS[-1]:
            rows.append([FILLERS[5], _compact(day), "S", None])
        if day in self.frame.also_halted_days:
            rows.append([FILLERS[5], _compact(day), "S", None])
        if (
            self.frame.limit_placeholder
            and self.frame.halted_on_placeholder_day
            and day == HALT_DAY
        ):
            rows.append([HALTED, _compact(day), "S", None])
        return rows

    def _factors(self, day: date) -> list[list[Any]]:
        rows = [[code, _compact(day), 1.0] for code in SECURITIES]
        if self._prelisted(day):
            rows.append([PRELISTED, _compact(day), 1.0])
        return rows

    def _registry(self) -> list[list[Any]]:
        rows = [
            [code, code, "SSE", "主板", "D", "20100104", "20131231"]
            if code == self.frame.delisted_code
            else [code, code, "SSE", "主板", "L", "20100104", None]
            for code in SECURITIES
        ]
        listed = LIST_DATE_BY_SCENARIO.get(self.frame.pre_listing or "")
        if listed is not None:
            rows.append([PRELISTED, PRELISTED, "BSE", "北交所", "L", listed, None])
        return rows

    def _limits(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if self._prelisted(day):
            rows.append([PRELISTED, _compact(day), 19.8, 16.2])
        for code in SECURITIES:
            close = self._pre_close(code, day)
            band = [round(close * 1.1, 2), round(close * 0.9, 2)]
            if self.frame.limit_placeholder and code == HALTED and day == HALT_DAY:
                band = [0.0, 9.0] if self.frame.upper_limit_only_zero else [0.0, 0.0]
            rows.append([code, _compact(day), *band])
        return rows

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api_name = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api_name == TRADING_CALENDAR_DATASET:
            items: list[list[Any]] = []
            previous: str | None = None
            year = int(str(params["start_date"])[:4])
            day = date(year, 1, 1)
            while day <= date(year, 12, 31):
                is_open = day in self.frame.sessions
                items.append([params["exchange"], _compact(day), 1 if is_open else 0, previous])
                if is_open:
                    previous = _compact(day)
                day += timedelta(days=1)
            return _response(CALENDAR_FIELDS, items)
        if api_name == "stock_basic":
            return _response(REGISTRY_FIELDS, self._registry())
        day = datetime.strptime(params["trade_date"], "%Y%m%d").date()
        # A second request for the same (dataset, session) is a re-fetch, whether it names a
        # security or asks for the whole session again.
        refetch = "ts_code" in params or (api_name, params["trade_date"]) in self.served
        self.served.add((api_name, params["trade_date"]))
        if api_name == DAILY_DATASET:
            rows = self._bars(day, refetch=refetch)
            if refetch and "ts_code" not in params and self.frame.session_refetch == "empty":
                rows = []
            fields = BAR_FIELDS
        elif api_name == DAILY_BASIC_DATASET:
            rows = self._valuations(day, refetch=refetch)
            fields = VALUATION_FIELDS
        elif api_name == SUSPENSION_DATASET:
            rows = self._halts(day)
            fields = HALT_FIELDS
        elif api_name == PRICE_LIMIT_DATASET:
            rows = self._limits(day)
            fields = LIMIT_FIELDS
        elif api_name == "adj_factor":
            rows = self._factors(day)
            fields = FACTOR_FIELDS
        else:
            raise AssertionError(f"unscripted dataset {api_name}")
        if "ts_code" in params:
            rows = [row for row in rows if row[0] == params["ts_code"]]
        return _response(fields, rows)


def _build(
    runtime_dir: Path,
    frame: Frame,
    monkeypatch: pytest.MonkeyPatch,
    *targets: str,
    year: int = YEAR,
    as_of: str = AS_OF,
) -> tuple[Any, ScriptedUpstream]:
    upstream = ScriptedUpstream(frame)
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: upstream)
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK)
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir), "--year", str(year)]
    arguments += ["--as-of", as_of, "--json"]
    for target in (TRADING_CALENDAR_DATASET, *targets):
        arguments += ["--dataset", target]
    return runner.invoke(app, arguments), upstream


def _store(runtime_dir: Path) -> PanelStore:
    return PanelStore(runtime_dir / "panel")


def _defects(runtime_dir: Path) -> tuple[UpstreamDefect, ...]:
    return load_upstream_defects(_store(runtime_dir), years=(YEAR,), as_of=AS_OF_INSTANT)


def _stored_keys(runtime_dir: Path, dataset: str, columns: tuple[str, ...]) -> set[tuple[str, str]]:
    rows = _store(runtime_dir).query(dataset, year=YEAR, columns=columns[:2])
    return {(str(row[0]), str(row[1])) for row in rows}


# --- the three named rules ---------------------------------------------------------------------


def test_a_valuation_the_bar_never_had_is_dropped_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, upstream = _build(tmp_path, Frame(valuation_without_bar=True), monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert _defects(tmp_path) == (
        UpstreamDefect(
            ts_code=NO_BAR,
            trade_date=NO_BAR_DAY,
            source_dataset=DAILY_BASIC_DATASET,
            kind="valuation_without_bar",
            valuation_close=13.75,
        ),
    )
    assert (NO_BAR, NO_BAR_DAY.isoformat()) not in _stored_keys(
        tmp_path, DAILY_BASIC_DATASET, DAILY_BASIC_PANEL_COLUMNS
    )
    # The re-fetch is one request per dataset for the disagreeing (security, session), no more.
    assert sorted(upstream.refetches()) == [
        (DAILY_DATASET, NO_BAR, _compact(NO_BAR_DAY)),
        (DAILY_BASIC_DATASET, NO_BAR, _compact(NO_BAR_DAY)),
    ]
    build = json.loads(result.stdout)["builds"][0]
    assert build["defects"] == [
        {
            "ts_code": NO_BAR,
            "trade_date": NO_BAR_DAY.isoformat(),
            "source_dataset": DAILY_BASIC_DATASET,
            "kind": "valuation_without_bar",
            "bar_close": None,
            "valuation_close": 13.75,
            "previous_bar_close": None,
            "up_limit": None,
            "down_limit": None,
            "valuation_repeats_previous_close": None,
            "list_date": None,
        }
    ]
    assert {"dataset": UPSTREAM_DEFECTS_DATASET, "year": YEAR, "row_count": 1} in build[
        "partitions"
    ]


def test_a_stale_valuation_contradicting_a_corroborated_bar_is_dropped_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = _build(tmp_path, Frame(stale_valuation=True), monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert _defects(tmp_path) == (
        UpstreamDefect(
            ts_code=STALE,
            trade_date=STALE_DAY,
            source_dataset=DAILY_BASIC_DATASET,
            kind="valuation_contradicts_corroborated_bar",
            bar_close=6.8,
            valuation_close=6.62,
            previous_bar_close=6.62,
            valuation_repeats_previous_close=True,
        ),
    )
    stored = _stored_keys(tmp_path, DAILY_BASIC_DATASET, DAILY_BASIC_PANEL_COLUMNS)
    assert (STALE, STALE_DAY.isoformat()) not in stored
    # The bar is never edited: daily still holds 6.8 for that session.
    bar = _store(tmp_path).query(
        DAILY_DATASET, year=YEAR, columns=("subject", "close"), filters={"trade_date": "2013-11-13"}
    )
    assert (STALE, 6.8) in {(str(row[0]), row[1]) for row in bar}


def test_a_valuation_equal_to_neither_close_beside_a_corroborated_bar_is_dropped_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The controller's correction, from 2020-10-23: ten securities a cent or a few apart, the
    next session's `pre_close` equal to the `daily` close, the `daily_basic` close equal to
    neither. The stale shape is not required; whether it held is recorded.

    Two disputed securities on one session also pin the re-fetch's cost: the whole session is
    asked for again, two requests, rather than two per security."""
    frame = Frame(stale_valuation=True, contradicted_valuation=True)
    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert _defects(tmp_path) == (
        UpstreamDefect(
            ts_code=STALE,
            trade_date=STALE_DAY,
            source_dataset=DAILY_BASIC_DATASET,
            kind="valuation_contradicts_corroborated_bar",
            bar_close=6.8,
            valuation_close=6.62,
            previous_bar_close=6.62,
            valuation_repeats_previous_close=True,
        ),
        UpstreamDefect(
            ts_code=FILLERS[1],
            trade_date=STALE_DAY,
            source_dataset=DAILY_BASIC_DATASET,
            kind="valuation_contradicts_corroborated_bar",
            bar_close=10.0,
            valuation_close=10.03,
            previous_bar_close=10.0,
            valuation_repeats_previous_close=False,
        ),
    )
    assert upstream.refetches() == []  # no per-security request
    on_the_day = [
        str(payload["api_name"])
        for payload in upstream.payloads
        if payload["params"].get("trade_date") == _compact(STALE_DAY)
    ]
    assert on_the_day.count(DAILY_DATASET) == 2  # the year's fetch and one whole-session re-fetch
    assert on_the_day.count(DAILY_BASIC_DATASET) == 2


def test_a_zero_zero_band_on_a_whole_day_halt_is_dropped_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = _build(tmp_path, Frame(limit_placeholder=True), monkeypatch, "price", "stk_limit")

    assert result.exit_code == PanelExit.ok, result.output
    assert _defects(tmp_path) == (
        UpstreamDefect(
            ts_code=HALTED,
            trade_date=HALT_DAY,
            source_dataset=PRICE_LIMIT_DATASET,
            kind="limit_placeholder_on_halt",
            up_limit=0.0,
            down_limit=0.0,
        ),
    )
    assert (HALTED, HALT_DAY.isoformat()) not in _stored_keys(
        tmp_path, PRICE_LIMIT_DATASET, PRICE_LIMIT_PANEL_COLUMNS
    )


def test_both_sources_share_one_year_partition_without_overwriting_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(valuation_without_bar=True, stale_valuation=True, limit_placeholder=True)
    result, _ = _build(tmp_path, frame, monkeypatch, "price", "stk_limit")

    assert result.exit_code == PanelExit.ok, result.output
    assert [(d.ts_code, d.trade_date, d.kind) for d in _defects(tmp_path)] == [
        (HALTED, HALT_DAY, "limit_placeholder_on_halt"),
        (STALE, STALE_DAY, "valuation_contradicts_corroborated_bar"),
        (NO_BAR, NO_BAR_DAY, "valuation_without_bar"),
    ]
    assert "DEFECT" not in result.stdout  # --json keeps stdout parseable


# --- what still refuses ------------------------------------------------------------------------


def test_a_re_fetch_that_differs_from_the_first_fetch_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for frame in (
        Frame(valuation_without_bar=True, refetch_differs=True),
        Frame(stale_valuation=True, refetch_differs=True),
    ):
        runtime = tmp_path / str(frame.valuation_without_bar)
        result, _ = _build(runtime, frame, monkeypatch, "price")

        assert result.exit_code == PanelExit.unhealthy, result.output
        assert "re-fetch" in result.output
        assert "partial" in result.output
        assert _store(runtime).registered_years(DAILY_BASIC_DATASET) == ()
        assert _store(runtime).registered_years(UPSTREAM_DEFECTS_DATASET) == ()


def test_a_mismatch_beside_a_bar_nothing_corroborates_still_refuses_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bar closes at 10.0 and its next session's `pre_close` is 10.5, so nothing but the
    bar itself says 10.0 is right -- the disagreement is refused, naming both closes."""
    result, _ = _build(tmp_path, Frame(uncorroborated_mismatch=True), monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    for fragment in (FILLERS[0], STALE_DAY.isoformat(), "10.0", "11.5", "not corroborated"):
        assert fragment in result.output
    assert _store(tmp_path).registered_years(DAILY_BASIC_DATASET) == ()


def test_a_zero_zero_band_on_a_session_the_security_traded_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(limit_placeholder=True, halted_on_placeholder_day=False)
    result, _ = _build(tmp_path, frame, monkeypatch, "price", "stk_limit")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert HALTED in result.output
    assert HALT_DAY.isoformat() in result.output
    assert "not halted" in result.output
    assert _store(tmp_path).registered_years(PRICE_LIMIT_DATASET) == ()


def test_a_band_with_only_its_upper_limit_zero_is_refused_even_on_a_halt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(limit_placeholder=True, upper_limit_only_zero=True)
    result, _ = _build(tmp_path, frame, monkeypatch, "price", "stk_limit")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert HALTED in result.output
    assert "up_limit" in result.output
    assert _store(tmp_path).registered_years(PRICE_LIMIT_DATASET) == ()


def test_the_credential_never_reaches_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(valuation_without_bar=True, refetch_differs=True)
    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert SECRET_TOKEN not in result.output
    assert all(payload["token"] == SECRET_TOKEN for payload in upstream.payloads)


# --- the record itself -------------------------------------------------------------------------


def test_the_defects_partition_round_trips_and_its_hash_is_stable_across_identical_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(valuation_without_bar=True, stale_valuation=True, limit_placeholder=True)
    hashes = []
    for name in ("first", "second"):
        result, _ = _build(tmp_path / name, frame, monkeypatch, "price", "stk_limit")
        assert result.exit_code == PanelExit.ok, result.output
        coverage = _store(tmp_path / name).read_coverage(UPSTREAM_DEFECTS_DATASET, YEAR)
        assert coverage is not None
        hashes.append(coverage.partition_content_hash)
        assert len(_defects(tmp_path / name)) == 3

    assert hashes[0] is not None
    assert hashes[0] == hashes[1]


def test_a_defect_the_upstream_has_corrected_is_gone_from_the_rebuilt_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is rebuilt from the drops the current build performs, so it always describes
    exactly the data stored: a corrected defect is simply not in it, its row is stored again, and
    another target's rows survive the rewrite. With nothing left the partition is removed."""
    both = Frame(valuation_without_bar=True, limit_placeholder=True)
    result, _ = _build(tmp_path, both, monkeypatch, "price", "stk_limit")
    assert result.exit_code == PanelExit.ok, result.output
    assert {d.kind for d in _defects(tmp_path)} == {
        "valuation_without_bar",
        "limit_placeholder_on_halt",
    }

    corrected = Frame(limit_placeholder=True)  # the upstream now publishes 000022.SZ's bar
    result, _ = _build(tmp_path, corrected, monkeypatch, "price")
    assert result.exit_code == PanelExit.ok, result.output
    assert [d.kind for d in _defects(tmp_path)] == ["limit_placeholder_on_halt"]
    assert (NO_BAR, NO_BAR_DAY.isoformat()) in _stored_keys(
        tmp_path, DAILY_BASIC_DATASET, DAILY_BASIC_PANEL_COLUMNS
    )

    alone = tmp_path / "alone"
    result, _ = _build(alone, Frame(valuation_without_bar=True), monkeypatch, "price")
    assert result.exit_code == PanelExit.ok, result.output
    assert _store(alone).registered_years(UPSTREAM_DEFECTS_DATASET) == (YEAR,)
    result, _ = _build(alone, Frame(), monkeypatch, "price")
    assert result.exit_code == PanelExit.ok, result.output
    assert _store(alone).registered_years(UPSTREAM_DEFECTS_DATASET) == ()
    assert _store(alone).read_coverage(UPSTREAM_DEFECTS_DATASET, YEAR) is None
    # A target that recorded nothing and finds nothing of its own is a no-op.
    assert (
        write_upstream_defects(
            _store(alone), None, year=YEAR, source_datasets=frozenset({DAILY_BASIC_DATASET})
        )
        is None
    )


# --- the last session, and the whole-session re-fetch (round 3) ---------------------------------


def test_a_mismatch_on_the_builds_last_session_is_recorded_unconfirmed_and_re_judged_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the last session the build requested there is no next session to corroborate the bar,
    and its own `pct_chg` corroborates nothing. The valuation is dropped and the defect recorded
    as unconfirmed; the next build that holds the following session judges it again."""
    last = SESSIONS[-1]
    result, _ = _build(tmp_path, Frame(mismatch_day=last), monkeypatch, "price")
    assert result.exit_code == PanelExit.ok, result.output
    assert _defects(tmp_path) == (
        UpstreamDefect(
            ts_code=FILLERS[2],
            trade_date=last,
            source_dataset=DAILY_BASIC_DATASET,
            kind="valuation_contradicts_unconfirmed_bar",
            bar_close=10.0,
            valuation_close=10.07,
            previous_bar_close=10.0,
            valuation_repeats_previous_close=False,
        ),
    )

    later = (*SESSIONS, date(2013, 11, 18))
    result, _ = _build(tmp_path, Frame(mismatch_day=last, sessions=later), monkeypatch, "price")
    assert result.exit_code == PanelExit.ok, result.output
    assert [d.kind for d in _defects(tmp_path)] == ["valuation_contradicts_corroborated_bar"]

    refused = tmp_path / "refused"
    result, _ = _build(refused, Frame(mismatch_day=last), monkeypatch, "price")
    assert result.exit_code == PanelExit.ok, result.output
    frame = Frame(mismatch_day=last, sessions=later, next_pre_close_disagrees=True)
    result, _ = _build(refused, frame, monkeypatch, "price")
    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "not corroborated" in result.output


def test_last_session_treatment_comes_from_the_requested_sessions_not_the_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A year whose final session is missing from the bars does not hand its *previous* session
    the last-session rule: `last_session` is what the build asked for, from the calendar."""
    frame = Frame(mismatch_day=SESSIONS[3])
    provider = TushareProvider(
        token=SECRET_TOKEN, transport=ScriptedUpstream(frame), clock=lambda: CLOCK
    )

    def fetch(dataset: str, day: date, subjects: tuple[str, ...] = ()) -> Any:
        instant = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
        return provider.fetch_panel(
            ProviderRequest(dataset=dataset, as_of=instant, subjects=subjects)
        )

    held = SESSIONS[:4]  # the bars stop at the 14th; the build asked through the 15th
    bars = [fetch(DAILY_DATASET, day) for day in held]
    valuations = [fetch(DAILY_BASIC_DATASET, day) for day in held]

    def refetch(day: date, codes: tuple[str, ...]) -> tuple[Any, Any]:
        subjects = codes if len(codes) == 1 else ()
        return fetch(DAILY_DATASET, day, subjects), fetch(DAILY_BASIC_DATASET, day, subjects)

    with pytest.raises(PanelBatchError, match="not corroborated"):
        reconcile_price_disagreements(bars, valuations, refetch=refetch, sessions=SESSIONS)
    unconfirmed = reconcile_price_disagreements(
        bars, valuations, refetch=refetch, sessions=SESSIONS[:4]
    )
    assert [d.kind for d in unconfirmed.defects] == ["valuation_contradicts_unconfirmed_bar"]


@pytest.mark.parametrize("shape", ["empty", "short"])
def test_a_whole_session_re_fetch_that_is_empty_or_short_confirms_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """Two valuations without a bar on one session re-fetch the whole session. If that answer is
    empty -- or short -- it carries no row for either disputed security, which is exactly what
    "no bar" looks like; so the re-fetch must itself be the whole session the year held."""
    frame = Frame(valuation_without_bar=True, second_no_bar=True, session_refetch=shape)
    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "whole-session re-fetch" in result.output
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()
    assert _store(tmp_path).registered_years(DAILY_BASIC_DATASET) == ()


def test_a_whole_session_re_fetch_that_is_whole_confirms_every_absence_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(valuation_without_bar=True, second_no_bar=True)
    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert sorted(d.ts_code for d in _defects(tmp_path)) == sorted([NO_BAR, FILLERS[3]])
    assert upstream.refetches() == []


# --- bar_before_listing (round 2) --------------------------------------------------------------


def test_rows_before_the_registrys_list_date_are_dropped_and_recorded_null_or_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`920476.BJ`'s 2013 rows -- a null-`pre_close` bar, an ordinary bar, their bands and their
    factors -- all precede its 2022-10-14 listing in the stored registry, so every one of them is
    outside the universe and is dropped with the list date recorded."""
    frame = Frame(pre_listing="before")
    result, _ = _build(
        tmp_path, frame, monkeypatch, "stock_basic", "adj_factor", "price", "stk_limit"
    )

    assert result.exit_code == PanelExit.ok, result.output
    recorded = [
        (d.source_dataset, d.trade_date, d.kind, d.list_date, d.bar_close)
        for d in _defects(tmp_path)
    ]
    listed = date(2022, 10, 14)
    assert sorted(recorded) == sorted(
        [
            (dataset, day, "bar_before_listing", listed, 18.0 if dataset == DAILY_DATASET else None)
            for dataset in (DAILY_DATASET, "adj_factor", PRICE_LIMIT_DATASET)
            for day in PRELISTED_DAYS
        ]
    )
    for dataset, columns in (
        (DAILY_DATASET, ("subject", "trade_date")),
        (PRICE_LIMIT_DATASET, PRICE_LIMIT_PANEL_COLUMNS),
    ):
        assert PRELISTED not in {key[0] for key in _stored_keys(tmp_path, dataset, columns)}
    assert all(
        entry["kind"] == "bar_before_listing"
        for entry in json.loads(result.stdout)["builds"][0]["defects"]
    )


@pytest.mark.parametrize("scenario", ["after", "absent"])
def test_a_null_field_bar_the_registry_does_not_place_before_listing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """On or after its list date, or for a security the registry does not know, a bar with no
    `pre_close` is what it always was: a malformed row. It is refused by name and never stored."""
    result, _ = _build(tmp_path, Frame(pre_listing=scenario), monkeypatch, "stock_basic", "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert PRELISTED in result.output
    assert PRELISTED_DAYS[0].isoformat() in result.output
    assert "with no pct_chg and no pre_close" in result.output
    assert _store(tmp_path).registered_years(DAILY_DATASET) == ()


def test_the_refusal_names_the_column_that_is_actually_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(pre_listing="after", prelisted_nulls=("pct_chg",))
    result, _ = _build(tmp_path, frame, monkeypatch, "stock_basic", "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "with no pct_chg (" in result.output
    assert "no pre_close" not in result.output


# --- round 4: the pre-listing re-fetch, and the year-end witness ---------------------------------

NEXT_YEAR: tuple[date, ...] = (date(2014, 1, 2), date(2014, 1, 3))


def test_a_whole_session_re_fetch_is_filtered_by_the_listing_rule_before_it_is_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's reproduction: two disputed securities on a session that also carries a
    back-mapped pre-listing bar. The year's batches no longer hold `920476.BJ`; the raw
    whole-session re-fetch does, and must be filtered the same way before it is compared."""
    frame = Frame(pre_listing="before", valuation_without_bar=True, second_no_bar=True)
    result, _ = _build(tmp_path, frame, monkeypatch, "stock_basic", "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert sorted(
        d.ts_code for d in _defects(tmp_path) if d.kind == "valuation_without_bar"
    ) == sorted([NO_BAR, FILLERS[3]])


def _next_year_stored(runtime: Path, frame: Frame, monkeypatch: pytest.MonkeyPatch, *targets: str):
    result, _ = _build(runtime, frame, monkeypatch, *targets, year=YEAR + 1)
    assert result.exit_code == PanelExit.ok, result.output


def test_a_year_end_mismatch_is_corroborated_by_the_stored_next_years_first_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(mismatch_day=SESSIONS[-1], sessions=(*SESSIONS, *NEXT_YEAR))
    _next_year_stored(tmp_path, frame, monkeypatch, "price")

    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [d.kind for d in _defects(tmp_path)] == ["valuation_contradicts_corroborated_bar"]
    assert upstream.refetches() == [
        (DAILY_DATASET, FILLERS[2], _compact(SESSIONS[-1])),
        (DAILY_BASIC_DATASET, FILLERS[2], _compact(SESSIONS[-1])),
    ]  # the re-fetch only: the witness came out of the store


def test_a_year_end_mismatch_is_corroborated_by_one_targeted_fetch_of_the_next_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(mismatch_day=SESSIONS[-1], sessions=(*SESSIONS, *NEXT_YEAR))
    _next_year_stored(tmp_path, frame, monkeypatch)  # the calendar only

    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [d.kind for d in _defects(tmp_path)] == ["valuation_contradicts_corroborated_bar"]
    assert (DAILY_DATASET, FILLERS[2], _compact(NEXT_YEAR[0])) in upstream.refetches()


def test_a_year_end_mismatch_the_next_years_first_bar_contradicts_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(
        mismatch_day=SESSIONS[-1], sessions=(*SESSIONS, *NEXT_YEAR), next_pre_close_disagrees=True
    )
    _next_year_stored(tmp_path, frame, monkeypatch)

    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "not corroborated" in result.output
    assert _store(tmp_path).registered_years(DAILY_BASIC_DATASET) == ()


def test_the_witness_answers_not_yet_before_the_next_years_first_session_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a following year that has not published leaves the row unconfirmed."""
    frame = Frame(sessions=(*SESSIONS, *NEXT_YEAR))
    _next_year_stored(tmp_path, frame, monkeypatch)
    upstream = ScriptedUpstream(frame)
    provider = TushareProvider(token=SECRET_TOKEN, transport=upstream, clock=lambda: CLOCK)

    before = datetime(2014, 1, 2, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    after = datetime(2014, 1, 2, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    early = cli._year_end_witness(
        _store(tmp_path), provider, year=YEAR, exchange="SSE", now=before, delistings={}
    )
    late = cli._year_end_witness(
        _store(tmp_path), provider, year=YEAR, exchange="SSE", now=after, delistings={}
    )

    assert early(FILLERS[2]) is None
    assert upstream.payloads == []
    assert late(FILLERS[2]) == 10.0


def test_a_security_halted_into_year_end_is_corroborated_by_its_first_next_year_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its last 2013 bar is on the 14th and it is halted on the 15th, so no 2013 bar follows it
    -- before round 4 that was a refusal. Its first 2014 bar's pre_close is the witness."""
    frame = Frame(
        halted_into_year_end=True,
        mismatch_code=FILLERS[5],
        mismatch_day=SESSIONS[-2],
        sessions=(*SESSIONS, *NEXT_YEAR),
    )
    _next_year_stored(tmp_path, frame, monkeypatch, "price")

    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [(d.ts_code, d.kind) for d in _defects(tmp_path)] == [
        (FILLERS[5], "valuation_contradicts_corroborated_bar")
    ]


def test_a_price_build_with_no_session_refuses_before_it_fetches_anything(tmp_path: Path) -> None:
    """`last_session` is `sessions[-1]`; an empty session list is refused by name first."""
    upstream = ScriptedUpstream(Frame())
    provider = TushareProvider(token=SECRET_TOKEN, transport=upstream, clock=lambda: CLOCK)
    calendar = build_trading_calendar(
        "SSE",
        [
            CalendarDay(
                calendar_date=date(YEAR, 1, 1) + timedelta(days=offset), is_trading=offset == 1
            )
            for offset in range(365)
        ],
    )
    with pytest.raises(typer.Exit):
        cli._build_price_panel(
            _store(tmp_path),
            provider,
            written=[],
            sessions=(),
            calendar=calendar,
            year=YEAR,
            now=CLOCK,
            halts=False,
            listings=None,
            delistings={},
            exchange="SSE",
        )
    assert upstream.payloads == []


# --- round 5: an explained absence is unconfirmed, an unexplained one refuses ------------------

HALTED_ACROSS = Frame(
    halted_into_year_end=True,
    also_absent_days=(NEXT_YEAR[0],),
    also_halted_days=(NEXT_YEAR[0],),
    mismatch_code=FILLERS[5],
    mismatch_day=SESSIONS[-2],
    sessions=(*SESSIONS, *NEXT_YEAR),
)
"""The reviewer's probe: halted from the 15th of November into 2014-01-02, trading again on the
3rd. Its disputed close on the 14th has no later bar in 2013 and none on 2014's first session."""


def test_a_security_still_halted_on_the_next_years_first_session_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _next_year_stored(tmp_path, HALTED_ACROSS, monkeypatch)  # the calendar only

    result, upstream = _build(tmp_path, HALTED_ACROSS, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [(d.ts_code, d.kind) for d in _defects(tmp_path)] == [
        (FILLERS[5], "valuation_contradicts_unconfirmed_bar")
    ]
    witnessed = [
        (str(p["api_name"]), dict(p["params"]))
        for p in upstream.payloads
        if str(p["params"].get("trade_date")) == _compact(NEXT_YEAR[0])
    ]
    # one targeted daily request, and the halts as one WHOLE-SESSION suspend_d request
    assert (SUSPENSION_DATASET, {"trade_date": _compact(NEXT_YEAR[0])}) in [
        (api, {k: v for k, v in params.items() if k == "trade_date" or k == "ts_code"})
        for api, params in witnessed
    ]
    assert all("ts_code" not in params for api, params in witnessed if api == SUSPENSION_DATASET)


def test_a_security_halted_into_the_new_year_is_found_on_the_session_it_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _next_year_stored(tmp_path, HALTED_ACROSS, monkeypatch, "price")

    result, _ = _build(tmp_path, HALTED_ACROSS, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [(d.ts_code, d.kind) for d in _defects(tmp_path)] == [
        (FILLERS[5], "valuation_contradicts_corroborated_bar")
    ]


def test_an_absence_on_the_next_years_first_session_with_no_halt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = dataclasses.replace(HALTED_ACROSS, also_halted_days=())
    _next_year_stored(tmp_path, frame, monkeypatch)

    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "no whole-day halt or delisting" in result.output


def test_a_security_delisted_at_year_end_is_unconfirmed_rather_than_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(
        delisted_code=FILLERS[5],
        mismatch_code=FILLERS[5],
        mismatch_day=SESSIONS[-1],
        sessions=(*SESSIONS, *NEXT_YEAR),
    )
    _next_year_stored(tmp_path, frame, monkeypatch)

    result, _ = _build(tmp_path, frame, monkeypatch, "stock_basic", "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [(d.ts_code, d.kind) for d in _defects(tmp_path)] == [
        (FILLERS[5], "valuation_contradicts_unconfirmed_bar")
    ]


def test_a_year_in_progress_halted_through_its_last_session_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build stops at the 14th (the 15th has not published); the security's disputed close is
    on the 13th and it is halted all of the 14th, so nothing after it can corroborate it yet."""
    frame = Frame(
        also_absent_days=(SESSIONS[3],),
        also_halted_days=(SESSIONS[3],),
        mismatch_code=FILLERS[5],
        mismatch_day=SESSIONS[2],
    )
    result, _ = _build(tmp_path, frame, monkeypatch, "price", as_of="2013-11-14T20:00:00+08:00")

    assert result.exit_code == PanelExit.ok, result.output
    defects = load_upstream_defects(
        _store(tmp_path), years=(YEAR,), as_of=datetime(2013, 11, 14, 12, 0, tzinfo=UTC)
    )
    assert [(d.ts_code, d.kind) for d in defects] == [
        (FILLERS[5], "valuation_contradicts_unconfirmed_bar")
    ]

    unhalted = dataclasses.replace(frame, also_halted_days=())
    refused = tmp_path / "refused"
    result, _ = _build(refused, unhalted, monkeypatch, "price", as_of="2013-11-14T20:00:00+08:00")
    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "no whole-day halt or delisting" in result.output


def test_a_stored_next_year_that_cannot_be_read_is_refused_by_name_not_replaced_by_a_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(mismatch_day=SESSIONS[-1], sessions=(*SESSIONS, *NEXT_YEAR))
    _next_year_stored(tmp_path, frame, monkeypatch, "price")
    stored = _store(tmp_path).read_coverage(DAILY_DATASET, YEAR + 1)
    assert stored is not None
    (tmp_path / "panel" / DAILY_DATASET / str(YEAR + 1) / "data.parquet").unlink()

    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert f"{DAILY_DATASET} year={YEAR + 1} partition could not be read" in result.output
    assert (DAILY_DATASET, FILLERS[2], _compact(NEXT_YEAR[0])) not in upstream.refetches()


def test_a_re_fetch_that_was_all_pre_listing_comes_back_empty_not_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = Frame(pre_listing="before")
    provider = TushareProvider(
        token=SECRET_TOKEN, transport=ScriptedUpstream(frame), clock=lambda: CLOCK
    )
    day = PRELISTED_DAYS[0]
    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=DAILY_DATASET,
            as_of=datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC),
            subjects=(PRELISTED,),
        )
    )
    assert batch.subjects == (PRELISTED,)

    empty = cli._listed_only(batch, {PRELISTED: date(2022, 10, 14)})

    assert empty.status == "no_data"
    assert empty.dataset == DAILY_DATASET
    assert empty.subjects == ()


def test_panel_doctor_answers_for_the_defects_record_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`upstream_defects` is written by `panel_ingest` and fetched from nowhere, so it has no
    publication cadence; the report gives it the `derived` one and its own requirement."""
    result, _ = _build(tmp_path, Frame(valuation_without_bar=True), monkeypatch, "price")
    assert result.exit_code == PanelExit.ok, result.output

    report = panel_health_report(
        _store(tmp_path), as_of=AS_OF_INSTANT, datasets=(UPSTREAM_DEFECTS_DATASET,), years=(YEAR,)
    )
    health = report.dataset(UPSTREAM_DEFECTS_DATASET)
    assert health.freshness.cadence == "derived"
    assert health.is_ready
    assert report.is_clean

    # A year with no partition is a year whose build dropped nothing, not a missing one.
    quiet = panel_health_report(
        _store(tmp_path),
        as_of=AS_OF_INSTANT,
        datasets=(UPSTREAM_DEFECTS_DATASET,),
        years=(YEAR, YEAR - 1),
    )
    assert quiet.dataset(UPSTREAM_DEFECTS_DATASET).is_ready
    assert "partition_missing" not in quiet.codes()


# --- the decoder -------------------------------------------------------------------------------


class _OneResponse:
    def __init__(self, items: list[list[Any]]) -> None:
        self.items = items

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _response(LIMIT_FIELDS, self.items)


def _decode_limits(items: list[list[Any]]) -> Any:
    provider = TushareProvider(
        token=SECRET_TOKEN, transport=_OneResponse(items), clock=lambda: CLOCK
    )
    return provider.fetch_panel(
        ProviderRequest(dataset=PRICE_LIMIT_DATASET, as_of=datetime(2014, 1, 9, 12, 0, tzinfo=UTC))
    )


def test_the_decoder_admits_a_zero_upper_limit_and_still_refuses_a_negative_one() -> None:
    batch = _decode_limits([["000509.SZ", "20140109", 0.0, 0.0]])
    assert batch.status == "success"

    with pytest.raises(ProviderFailure, match="up_limit"):
        _decode_limits([["000509.SZ", "20140109", -0.01, 0.0]])


def test_the_limit_writer_refuses_a_zero_upper_limit_nobody_reconciled(tmp_path: Path) -> None:
    """The decoder admits `0.0` so the reconciliation can see the row; a caller that skips the
    reconciliation must still not be able to store it."""
    calendar = build_trading_calendar(
        "SSE",
        [
            CalendarDay(calendar_date=date(2014, 1, 1) + timedelta(days=offset), is_trading=True)
            for offset in range(365)
        ],
    )
    with pytest.raises(PanelBatchError, match=r"up_limit 0\.0"):
        write_price_limits(
            PanelStore(tmp_path / "panel"),
            [_decode_limits([["000509.SZ", "20140109", 0.0, 0.0]])],
            calendar=calendar,
        )


# --- the pure rules ----------------------------------------------------------------------------


def test_a_bar_with_no_next_session_is_unconfirmed_only_on_the_last_requested_session() -> None:
    assert (
        close_disagreement_kind(bar_close=6.8, next_bar_pre_close=None, is_last_session=True)
        == "valuation_contradicts_unconfirmed_bar"
    )
    assert (
        close_disagreement_kind(bar_close=6.8, next_bar_pre_close=None, is_last_session=False)
        is None
    )


def test_a_bar_its_next_session_disagrees_with_is_not_corroborated() -> None:
    assert (
        close_disagreement_kind(bar_close=6.8, next_bar_pre_close=6.7, is_last_session=False)
        is None
    )
    assert (
        close_disagreement_kind(bar_close=6.8, next_bar_pre_close=6.8, is_last_session=False)
        == "valuation_contradicts_corroborated_bar"
    )
    assert (
        close_disagreement_kind(bar_close=6.8, next_bar_pre_close=6.7, is_last_session=True) is None
    )
    assert close_disagreement_kind(
        bar_close=None, next_bar_pre_close=None, is_last_session=False
    ) == ("valuation_without_bar")


def test_the_stale_shape_is_recorded_rather_than_required() -> None:
    assert repeats_previous_close(valuation_close=6.62, previous_bar_close=6.62) is True
    assert repeats_previous_close(valuation_close=31.05, previous_bar_close=30.0) is False
    assert repeats_previous_close(valuation_close=6.62, previous_bar_close=None) is None


def test_only_a_zero_zero_band_on_a_halt_is_a_placeholder() -> None:
    assert limit_placeholder_kind(up_limit=0.0, down_limit=0.0, halted=True) == (
        "limit_placeholder_on_halt"
    )
    assert limit_placeholder_kind(up_limit=0.0, down_limit=0.0, halted=False) is None
    assert limit_placeholder_kind(up_limit=0.0, down_limit=9.0, halted=True) is None
