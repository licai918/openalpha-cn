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
  while the next session's `pre_close` corroborates the bar;
- (`V2-P6-017`, measured 2026-09-28) `000029.SZ`, halted, and `200011.SZ`, a B share with no halt
  row, have no bar on 2013-11-12 and a `daily_basic` row whose every field but `volume_ratio` is
  null (`valuation_placeholder_on_halt` and `valuation_placeholder_without_bar`).

The twenty securities are there so a one-row difference is not a thin session: the explained-share
guard refuses a session under 85% of its neighbours, and two dropped rows of three would be.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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
    RecordedReturnPath,
)
from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    TimelineColumns,
)
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
    RETURN_PATH_KINDS,
    UpstreamDefect,
    UpstreamDefectError,
    close_disagreement_kind,
    limit_placeholder_kind,
    recorded_return_paths,
    repeats_previous_close,
    return_path_kind,
    upstream_defects_from_panel_rows,
    valuation_placeholder_kind,
    withdrawn_subjects,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import panel_health_report
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
    load_upstream_defects,
    reconcile_price_disagreements,
    reconcile_return_paths,
    write_daily_panel,
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
RETURN_PATH_CODE = FILLERS[6]
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
# `V2-P6-017`: 2020-09-18's measured `daily_basic` placeholders (live, 2026-09-28), moved onto
# this frame's second session. Every field is null except `volume_ratio`. `000029.SZ` has no bar,
# an untimed `S` in `suspend_d` and a `stk_limit` band; `200011.SZ`, a B share, has no bar, no
# `suspend_d` row and no band.
PLACEHOLDER_DAY = SESSIONS[1]
PLACEHOLDER_HALTED = "000029.SZ"
PLACEHOLDER_ABSENT = "200011.SZ"
PLACEHOLDER_CODES: tuple[str, ...] = (PLACEHOLDER_HALTED, PLACEHOLDER_ABSENT)
PLACEHOLDER_VOLUME_RATIO = 0.87
PLACEHOLDER_KINDS: tuple[tuple[str, str], ...] = (
    (PLACEHOLDER_HALTED, "valuation_placeholder_on_halt"),
    (PLACEHOLDER_ABSENT, "valuation_placeholder_without_bar"),
)

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
    next_year_barless: bool = False
    """`mismatch_code` has no bar and no valuation on any session after 2013."""
    next_year_halted_days: tuple[date, ...] = ()
    """Sessions on which `suspend_d` has `mismatch_code` halted all day."""
    valuation_placeholders: bool = False
    """`V2-P6-017`: on `PLACEHOLDER_DAY` both `PLACEHOLDER_CODES` have no bar and a `daily_basic`
    placeholder row with a null close; both trade on every other session."""
    placeholder_beside_bar: bool = False
    """`PLACEHOLDER_HALTED` has a real bar on `PLACEHOLDER_DAY` beside its placeholder."""
    placeholder_refetch_closes: bool = False
    """The re-fetch answers `PLACEHOLDER_ABSENT`'s placeholder with a real close."""
    placeholder_codes: tuple[str, ...] = PLACEHOLDER_CODES
    """Which of `PLACEHOLDER_CODES` publish the placeholder; the others trade that day."""
    no_halt_rows: bool = False
    """`suspend_d` serves nothing on any session."""
    delisted_code: str | None = None
    """A security the registry has delisted on 2013-12-31, with no bar after 2013."""
    return_path: str | None = None
    """`V2-P6-020`: `RETURN_PATH_CODE`'s `pre_close` and `adj_factor` disagree on `SESSIONS[2]`.
    `published`: the factor steps 1.0 -> 1.1 on that session alone and back on the next, with
    `pre_close` equal to the previous close and the band centred on it (`000998.SZ`'s shape);
    `unknowable`: the factor steps to 1.1 for good and the session closes at 12.0, outside the
    band centred on its published 10.0 (`000010.SZ`'s shape)."""
    return_path_refetch_factor: float | None = None
    """What a per-security `adj_factor` re-fetch answers for `RETURN_PATH_CODE` on
    `SESSIONS[2]`; `None`: what the whole-market fetch served."""
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
        if self.frame.return_path == "unknowable" and code == RETURN_PATH_CODE:
            return 12.0 if day >= SESSIONS[2] else 10.0
        return 13.75 if code == NO_BAR else 10.0

    def _pre_close(self, code: str, day: date) -> float:
        if self.frame.return_path == "unknowable" and (code, day) == (
            RETURN_PATH_CODE,
            SESSIONS[2],
        ):
            return 10.0
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

    def _codes(self) -> tuple[str, ...]:
        if self.frame.valuation_placeholders:
            return (*SECURITIES, *PLACEHOLDER_CODES)
        return SECURITIES

    def _placeholder(self, code: str, day: date) -> bool:
        return (
            self.frame.valuation_placeholders
            and code in self.frame.placeholder_codes
            and day == PLACEHOLDER_DAY
        )

    def _traded(self, code: str, day: date) -> bool:
        if self._placeholder(code, day):
            return self.frame.placeholder_beside_bar and code == PLACEHOLDER_HALTED
        if self.frame.halted_into_year_end and code == FILLERS[5] and day == SESSIONS[-1]:
            return False
        if code == FILLERS[5] and day in self.frame.also_absent_days:
            return False
        if code == self.frame.delisted_code and day.year > YEAR:
            return False
        if self.frame.next_year_barless and code == self.frame.mismatch_code and day.year > YEAR:
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
        for code in self._codes():
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
        rows: list[list[Any]] = []
        for code in self._codes():
            republished = (
                refetch and self.frame.placeholder_refetch_closes and code == PLACEHOLDER_ABSENT
            )
            if self._placeholder(code, day) and not republished:
                # The measured shape: every field null but `volume_ratio`.
                extra = [
                    PLACEHOLDER_VOLUME_RATIO if name == "volume_ratio" else None
                    for name in VALUATION_EXTRA
                ]
                rows.append([code, _compact(day), None, *extra])
            elif self._traded(code, day) or republished:
                close = self._valuation_close(code, day, refetch=refetch)
                rows.append([code, _compact(day), close, *([1.0] * len(VALUATION_EXTRA))])
        return rows

    def _halts(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if self.frame.no_halt_rows:
            return rows
        if day == min(open_day for open_day in self.frame.sessions if open_day.year == day.year):
            rows.append([RESUMED, _compact(day), "R", None])
        if self.frame.halted_into_year_end and day == SESSIONS[-1]:
            rows.append([FILLERS[5], _compact(day), "S", None])
        if day in self.frame.also_halted_days:
            rows.append([FILLERS[5], _compact(day), "S", None])
        if day in self.frame.next_year_halted_days:
            rows.append([self.frame.mismatch_code, _compact(day), "S", None])
        if self._placeholder(PLACEHOLDER_HALTED, day):
            rows.append([PLACEHOLDER_HALTED, _compact(day), "S", None])
        if (
            self.frame.limit_placeholder
            and self.frame.halted_on_placeholder_day
            and day == HALT_DAY
        ):
            rows.append([HALTED, _compact(day), "S", None])
        return rows

    def _factor(self, code: str, day: date, *, refetch: bool) -> float:
        if code != RETURN_PATH_CODE or self.frame.return_path is None:
            return 1.0
        if refetch and day == SESSIONS[2] and self.frame.return_path_refetch_factor is not None:
            return self.frame.return_path_refetch_factor
        if self.frame.return_path == "published":
            return 1.1 if day == SESSIONS[2] else 1.0
        return 1.1 if day >= SESSIONS[2] else 1.0

    def _factors(self, day: date, *, refetch: bool = False) -> list[list[Any]]:
        rows = [
            [code, _compact(day), self._factor(code, day, refetch=refetch)]
            for code in self._codes()
        ]
        if self._prelisted(day):
            rows.append([PRELISTED, _compact(day), 1.0])
        return rows

    def _registry(self) -> list[list[Any]]:
        rows = [
            [code, code, "SSE", "主板", "D", "20100104", "20131231"]
            if code == self.frame.delisted_code
            else [code, code, "SSE", "主板", "L", "20100104", None]
            for code in self._codes()
        ]
        listed = LIST_DATE_BY_SCENARIO.get(self.frame.pre_listing or "")
        if listed is not None:
            rows.append([PRELISTED, PRELISTED, "BSE", "北交所", "L", listed, None])
        return rows

    def _limits(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if self._prelisted(day):
            rows.append([PRELISTED, _compact(day), 19.8, 16.2])
        for code in self._codes():
            if self._placeholder(code, day) and code == PLACEHOLDER_ABSENT:
                continue  # measured: no band for the B share either
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
            rows = self._factors(day, refetch="ts_code" in params)
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
    no_halts: bool = False,
    extra: tuple[str, ...] = (),
) -> tuple[Any, ScriptedUpstream]:
    upstream = ScriptedUpstream(frame)
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: upstream)
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK)
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir), "--year", str(year)]
    arguments += ["--as-of", as_of, "--json"]
    if no_halts:
        arguments.append("--no-halts")
    arguments += list(extra)
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


# --- round 6: the stored next year, judged session by session within its own horizon ---------


def _barless_next_year(**changes: Any) -> Frame:
    return Frame(
        mismatch_day=SESSIONS[-1],
        sessions=(*SESSIONS, *NEXT_YEAR),
        next_year_barless=True,
        **changes,
    )


def test_every_barless_session_of_the_stored_next_year_must_be_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's probe: no 2014 bar at all, halted on 2014-01-02 only. 2014-01-03 is a
    published, stored session with no bar and nothing to explain it, so the year is refused by
    that session -- the year+1 build does not refuse a per-security hole, so this has to."""
    frame = _barless_next_year(next_year_halted_days=(NEXT_YEAR[0],))
    _next_year_stored(tmp_path, frame, monkeypatch, "price")

    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert f"no bar for it on {NEXT_YEAR[1].isoformat()}" in result.output


def test_a_security_halted_on_every_stored_next_year_session_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _barless_next_year(next_year_halted_days=NEXT_YEAR)
    _next_year_stored(tmp_path, frame, monkeypatch, "price")

    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [d.kind for d in _defects(tmp_path)] == ["valuation_contradicts_unconfirmed_bar"]
    assert not [p for p in upstream.payloads if str(p["params"].get("trade_date", "")) > "2014"]


def test_a_stored_next_year_daily_without_its_halt_corpus_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _barless_next_year(next_year_halted_days=NEXT_YEAR)
    _next_year_stored(tmp_path, frame, monkeypatch, "price")
    assert _store(tmp_path).remove_partition(SUSPENSION_DATASET, YEAR + 1)

    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert f"no stored {SUSPENSION_DATASET} year={YEAR + 1}" in result.output


def test_a_stored_next_year_that_merely_lags_is_judged_within_its_own_horizon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's second probe: 2014 was built on the evening of 2014-01-02 and holds that
    session only. Read at 2026 it lags, which is not damage: its 2014-01-02 bar corroborates."""
    frame = Frame(mismatch_day=SESSIONS[-1], sessions=(*SESSIONS, *NEXT_YEAR))
    result, _ = _build(
        tmp_path, frame, monkeypatch, "price", year=YEAR + 1, as_of="2014-01-02T18:00:00+08:00"
    )
    assert result.exit_code == PanelExit.ok, result.output

    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert [d.kind for d in _defects(tmp_path)] == ["valuation_contradicts_corroborated_bar"]
    assert (DAILY_DATASET, FILLERS[2], _compact(NEXT_YEAR[0])) not in upstream.refetches()


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


# --- valuation placeholders: a null close and no bar (`V2-P6-017`) -----------------------------


def test_valuation_placeholders_with_no_bar_are_dropped_and_recorded_one_per_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2020-09-18's shape: `daily_basic` rows whose close -- and everything else but
    `volume_ratio` -- is null, for a halted A share and a B share with no halt row, and no bar for
    either. The session builds; each placeholder is dropped and recorded once, after the re-fetch
    published it again -- under the kind that says whether `suspend_d` explains the missing bar."""
    result, upstream = _build(
        tmp_path, Frame(valuation_placeholders=True), monkeypatch, "price", "stk_limit"
    )

    assert result.exit_code == PanelExit.ok, result.output
    assert _defects(tmp_path) == tuple(
        UpstreamDefect(
            ts_code=code,
            trade_date=PLACEHOLDER_DAY,
            source_dataset=DAILY_BASIC_DATASET,
            kind=kind,
        )
        for code, kind in PLACEHOLDER_KINDS
    )
    stored = _stored_keys(tmp_path, DAILY_BASIC_DATASET, DAILY_BASIC_PANEL_COLUMNS)
    for code in PLACEHOLDER_CODES:
        assert (code, PLACEHOLDER_DAY.isoformat()) not in stored
        assert (code, SESSIONS[2].isoformat()) in stored  # its real rows are untouched
    # Two placeholders on one session: that session is re-fetched whole, once per dataset.
    asked = [
        str(p["api_name"])
        for p in upstream.payloads
        if p["params"].get("trade_date") == _compact(PLACEHOLDER_DAY)
    ]
    assert asked.count(DAILY_DATASET) == 2
    assert asked.count(DAILY_BASIC_DATASET) == 2
    assert upstream.refetches() == []
    reported = json.loads(result.stdout)["builds"][0]["defects"]
    assert [(entry["ts_code"], entry["kind"]) for entry in reported] == list(PLACEHOLDER_KINDS)


def test_a_lone_valuation_placeholder_is_re_fetched_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One placeholder on a session takes the targeted path: `ts_code`-filtered requests."""
    frame = Frame(valuation_placeholders=True, placeholder_codes=(PLACEHOLDER_ABSENT,))
    result, upstream = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.ok, result.output
    assert sorted(upstream.refetches()) == [
        (DAILY_DATASET, PLACEHOLDER_ABSENT, _compact(PLACEHOLDER_DAY)),
        (DAILY_BASIC_DATASET, PLACEHOLDER_ABSENT, _compact(PLACEHOLDER_DAY)),
    ]
    assert [(d.ts_code, d.kind) for d in _defects(tmp_path)] == [
        (PLACEHOLDER_ABSENT, "valuation_placeholder_without_bar")
    ]
    assert (PLACEHOLDER_HALTED, PLACEHOLDER_DAY.isoformat()) in _stored_keys(
        tmp_path, DAILY_BASIC_DATASET, DAILY_BASIC_PANEL_COLUMNS
    )


def test_without_the_halt_guard_the_stored_halt_corpus_still_names_the_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-halts` waives the explained-share guard, not the halt question: the build reads the
    `suspend_d` partition it has just stored, and records the same two kinds."""
    frame = Frame(valuation_placeholders=True)
    result, _ = _build(tmp_path, frame, monkeypatch, "price", no_halts=True)

    assert result.exit_code == PanelExit.ok, result.output
    assert [(d.ts_code, d.kind) for d in _defects(tmp_path)] == list(PLACEHOLDER_KINDS)


def test_a_placeholder_with_no_halt_corpus_to_ask_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Not halted' cannot be told from 'never checked': with no `suspend_d` stored for the year,
    a placeholder is not defaulted to either kind."""
    frame = Frame(valuation_placeholders=True, no_halt_rows=True)
    result, _ = _build(tmp_path, frame, monkeypatch, "price", no_halts=True)

    assert result.exit_code == PanelExit.unhealthy, result.output
    for fragment in (PLACEHOLDER_HALTED, PLACEHOLDER_DAY.isoformat(), "suspend_d", "never checked"):
        assert fragment in result.output
    assert _store(tmp_path).registered_years(DAILY_BASIC_DATASET) == ()
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()


def test_a_valuation_placeholder_beside_a_real_bar_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not observed: no rule is invented for it. A bar says the security traded, and a
    `daily_basic` row with no close says nothing about that session -- the two contradict."""
    frame = Frame(valuation_placeholders=True, placeholder_beside_bar=True)
    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    for fragment in (
        PLACEHOLDER_HALTED,
        PLACEHOLDER_DAY.isoformat(),
        "null close",
        "contradicts the bar",
    ):
        assert fragment in result.output
    assert _store(tmp_path).registered_years(DAILY_BASIC_DATASET) == ()
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()


def test_a_valuation_placeholder_the_re_fetch_does_not_reproduce_refuses_the_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P6-013`'s rule for a defect the re-fetch does not reproduce: the upstream did not
    publish the same row twice, so it is a partial fetch, and the year is refused -- nothing is
    recorded and nothing is stored."""
    frame = Frame(valuation_placeholders=True, placeholder_refetch_closes=True)
    result, _ = _build(tmp_path, frame, monkeypatch, "price")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert PLACEHOLDER_ABSENT in result.output
    assert "re-fetch" in result.output
    assert "partial" in result.output
    assert _store(tmp_path).registered_years(DAILY_BASIC_DATASET) == ()
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()


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


class _Answer:
    def __init__(self, fields: Sequence[str], items: list[list[Any]]) -> None:
        self.fields = fields
        self.items = items

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _response(self.fields, self.items)


def _decode_session(dataset: str, fields: Sequence[str], items: list[list[Any]]) -> Any:
    provider = TushareProvider(
        token=SECRET_TOKEN, transport=_Answer(fields, items), clock=lambda: CLOCK
    )
    return provider.fetch_panel(
        ProviderRequest(dataset=dataset, as_of=datetime(2020, 9, 18, 12, tzinfo=UTC))
    )


def _decode_valuations(items: list[list[Any]]) -> Any:
    return _decode_session(DAILY_BASIC_DATASET, VALUATION_FIELDS, items)


def _valuation_row(code: str, close: float | None, *nulls: str) -> list[Any]:
    return [code, "20200918", close, *(None if name in nulls else 1.0 for name in VALUATION_EXTRA)]


def _placeholder_row(code: str) -> list[Any]:
    extra = [
        PLACEHOLDER_VOLUME_RATIO if name == "volume_ratio" else None for name in VALUATION_EXTRA
    ]
    return [code, "20200918", None, *extra]


def test_the_decoder_carries_a_null_close_placeholder_and_still_refuses_a_partial_valuation() -> (
    None
):
    """Only the measured placeholder -- every column null but `volume_ratio` -- is let through,
    as nulls, for `reconcile_price_disagreements` to judge beside the bar.
    A null close beside a real market value, or a real close beside a null one, is malformed."""
    batch = _decode_valuations([_valuation_row("000001.SZ", 10.0), _placeholder_row("000029.SZ")])
    assert batch.status == "success"
    columns = {column.name: column.values for column in batch.columns}
    at = batch.subjects.index("000029.SZ")
    assert columns["close"][at] is None
    assert columns["total_mv"][at] is None
    assert columns["volume_ratio"][at] == PLACEHOLDER_VOLUME_RATIO
    assert columns["close"][batch.subjects.index("000001.SZ")] == 10.0

    with pytest.raises(ProviderFailure, match="close"):
        _decode_valuations([_valuation_row("000029.SZ", None)])  # a market value, no close
    # All six required columns null but a `pe`: not the measured shape, so a human judges it.
    new_shape = _placeholder_row("000029.SZ")
    new_shape[3 + VALUATION_EXTRA.index("pe")] = 12.5
    with pytest.raises(ProviderFailure, match="close"):
        _decode_valuations([new_shape])
    # `volume_ratio` null as well is still the placeholder: it is nullable in every row.
    quiet = _placeholder_row("000029.SZ")
    quiet[3 + VALUATION_EXTRA.index("volume_ratio")] = None
    assert _decode_valuations([quiet]).status == "success"
    with pytest.raises(ProviderFailure, match="total_mv"):
        _decode_valuations([_valuation_row("000029.SZ", 10.0, "total_mv")])


def test_the_price_writer_refuses_a_placeholder_nobody_reconciled(tmp_path: Path) -> None:
    """The decoder carries the placeholder so the reconciliation can see it beside the bar; a
    caller that skips the reconciliation must still not be able to store a null close."""
    day = date(2020, 9, 18)
    first = date(2020, 1, 1)
    calendar = build_trading_calendar(
        "SSE",
        [
            CalendarDay(
                calendar_date=first + timedelta(days=offset),
                is_trading=first + timedelta(days=offset) == day,
            )
            for offset in range(366)
        ],
    )
    bar = ["000001.SZ", "20200918", 10.0, 10.0, 10.0, 10.0, 10.0, 0.0, 10.0, 100.0]
    valuations = [_valuation_row("000001.SZ", 10.0), _placeholder_row("000029.SZ")]
    with pytest.raises(PanelBatchError, match="null close"):
        write_daily_panel(
            PanelStore(tmp_path / "panel"),
            bars=[_decode_session(DAILY_DATASET, BAR_FIELDS, [bar])],
            fundamentals=[_decode_valuations(valuations)],
            calendar=calendar,
            halts=None,
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


def test_a_valuation_placeholder_is_a_defect_only_where_there_is_no_bar() -> None:
    assert valuation_placeholder_kind(has_bar=False, halted=True) == (
        "valuation_placeholder_on_halt"
    )
    assert valuation_placeholder_kind(has_bar=False, halted=False) == (
        "valuation_placeholder_without_bar"
    )
    assert valuation_placeholder_kind(has_bar=True, halted=True) is None
    assert valuation_placeholder_kind(has_bar=True, halted=False) is None


def test_a_withdrawal_needs_two_answers_that_agree_and_both_lack_the_row() -> None:
    """`V2-P6-016`'s rule: absent from the first answer, absent from a second that is otherwise
    the first. Any disagreement between the two answers is `None`, never a guess."""
    stored, first = {"A", "B", "C"}, {"A", "B"}
    assert withdrawn_subjects(stored=stored, first=first, second={"A", "B"}) == {"C"}
    assert withdrawn_subjects(stored=stored, first=first, second={"A", "B", "C"}) is None
    assert withdrawn_subjects(stored=stored, first=first, second={"A"}) is None
    assert withdrawn_subjects(stored=stored, first=first, second={"A", "B", "D"}) is None
    assert withdrawn_subjects(stored={"A"}, first={"A"}, second={"A"}) == frozenset()


def test_the_withdrawal_kind_and_a_halt_source_read_back() -> None:
    (defect,) = upstream_defects_from_panel_rows(
        [
            (
                "561730.SH",
                "2026-08-28",
                SUSPENSION_DATASET,
                "withdrawn_after_publication",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        ]
    )
    assert defect.kind == "withdrawn_after_publication"
    assert defect.source_dataset == SUSPENSION_DATASET


# --- V2-P6-020: a pre_close / adj_factor disagreement, decided by the day's own price -----------


def test_the_return_path_kind_names_the_corroborated_statement_or_neither() -> None:
    """The measured shapes: `000998.SZ` 2020-01-02 (band centred on the published 14.71, close on
    its upper edge), `000010.SZ` 2013-07-19 (close 7.0 outside a band centred on 23.87), a band
    centred on the factor path's statement, and no band at all."""
    assert (
        return_path_kind(
            published_pre_close=14.71,
            implied_pre_close=14.71 * 11.267 / 10.97,
            close=16.18,
            up_limit=16.18,
            down_limit=13.24,
        )
        == "pre_close_corroborated_over_adj_factor"
    )
    assert (
        return_path_kind(
            published_pre_close=10.5,
            implied_pre_close=10.0,
            close=10.2,
            up_limit=11.0,
            down_limit=9.0,
        )
        == "adj_factor_corroborated_over_pre_close"
    )
    for up, down, close in ((26.26, 21.48, 7.0), (None, None, 7.0)):
        assert (
            return_path_kind(
                published_pre_close=23.87,
                implied_pre_close=23.87 * 2.694 / 10.775,
                close=close,
                up_limit=up,
                down_limit=down,
            )
            == "pre_close_contradicts_adj_factor"
        )


def _return_path_row(kind: str, **values: Any) -> tuple[object, ...]:
    return (
        values.get("ts_code", "000998.SZ"),
        values.get("day", "2020-01-02"),
        values.get("source", PRICE_LIMIT_DATASET),
        kind,
        values.get("bar_close", 16.18),
        values.get("implied_pre_close", 15.1083),
        values.get("previous_bar_close", 14.71),
        values.get("up_limit", 16.18),
        values.get("down_limit", 13.24),
        None,
        None,
    )


def test_the_three_return_path_kinds_read_back_as_the_decisions_a_reader_follows() -> None:
    defects = upstream_defects_from_panel_rows(
        [
            _return_path_row("pre_close_corroborated_over_adj_factor"),
            _return_path_row(
                "adj_factor_corroborated_over_pre_close", ts_code="600000.SH", bar_close=10.2
            ),
            _return_path_row(
                "pre_close_contradicts_adj_factor",
                ts_code="000010.SZ",
                day="2013-07-19",
                bar_close=7.0,
                implied_pre_close=5.9681,
                previous_bar_close=23.87,
                up_limit=None,
                down_limit=None,
            ),
            # Any other kind is not a return-path decision and is not read as one.
            _return_path_row("limit_placeholder_on_halt", ts_code="000509.SZ"),
        ]
    )

    decided = recorded_return_paths(defects)

    assert decided == {
        ("000998.SZ", date(2020, 1, 2)): RecordedReturnPath(
            ts_code="000998.SZ",
            day=date(2020, 1, 2),
            close=16.18,
            previous_close=14.71,
            implied_pre_close=15.1083,
            path="published",
        ),
        ("600000.SH", date(2020, 1, 2)): RecordedReturnPath(
            ts_code="600000.SH",
            day=date(2020, 1, 2),
            close=10.2,
            previous_close=14.71,
            implied_pre_close=15.1083,
            path="adjusted",
        ),
        ("000010.SZ", date(2013, 7, 19)): RecordedReturnPath(
            ts_code="000010.SZ",
            day=date(2013, 7, 19),
            close=7.0,
            previous_close=23.87,
            implied_pre_close=5.9681,
            path=None,
        ),
    }
    assert set(RETURN_PATH_KINDS) == {
        "pre_close_corroborated_over_adj_factor",
        "adj_factor_corroborated_over_pre_close",
        "pre_close_contradicts_adj_factor",
    }


def test_a_return_path_record_without_the_two_closes_it_was_judged_on_is_refused() -> None:
    """A decision a reader cannot match against the rows in hand is not a decision about them."""
    (defect,) = upstream_defects_from_panel_rows(
        [_return_path_row("pre_close_contradicts_adj_factor", previous_bar_close=None)]
    )
    with pytest.raises(UpstreamDefectError, match="previous_bar_close"):
        recorded_return_paths([defect])
    # The adjustment factor's own statement of the session's reference price travels with the
    # decision (`valuation_close` for these kinds), so a reader can price both paths without a
    # second session's bar; a record without it is refused the same way.
    (unpriced,) = upstream_defects_from_panel_rows(
        [_return_path_row("pre_close_contradicts_adj_factor", implied_pre_close=None)]
    )
    with pytest.raises(UpstreamDefectError, match="implied pre_close"):
        recorded_return_paths([unpriced])
    twice = upstream_defects_from_panel_rows(
        [
            _return_path_row("pre_close_contradicts_adj_factor"),
            _return_path_row("pre_close_corroborated_over_adj_factor"),
        ]
    )
    with pytest.raises(UpstreamDefectError, match="two return-path records"):
        recorded_return_paths(twice)


def _return_path_defects(runtime_dir: Path) -> list[UpstreamDefect]:
    return [d for d in _defects(runtime_dir) if d.kind in RETURN_PATH_KINDS]


RETURN_PATH_REFETCHES: list[tuple[str, str, str]] = [
    (DAILY_DATASET, RETURN_PATH_CODE, _compact(SESSIONS[1])),
    (DAILY_DATASET, RETURN_PATH_CODE, _compact(SESSIONS[2])),
    ("adj_factor", RETURN_PATH_CODE, _compact(SESSIONS[1])),
    ("adj_factor", RETURN_PATH_CODE, _compact(SESSIONS[2])),
]


def test_a_disagreement_the_band_decides_is_recorded_by_the_limit_target_with_its_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`000998.SZ`'s shape: the factor steps on a session whose `pre_close` is the previous
    close, and steps back on the next. Both sessions disagree; both bands are centred on the
    published statement with the close inside; both are recorded as the published path, with
    the two closes they were judged on and the band that decided them."""
    frame = Frame(return_path="published")
    result, upstream = _build(tmp_path, frame, monkeypatch, "adj_factor", "price", "stk_limit")

    assert result.exit_code == PanelExit.ok, result.output
    assert _return_path_defects(tmp_path) == [
        UpstreamDefect(
            ts_code=RETURN_PATH_CODE,
            trade_date=day,
            source_dataset=PRICE_LIMIT_DATASET,
            kind="pre_close_corroborated_over_adj_factor",
            bar_close=10.0,
            valuation_close=implied,
            previous_bar_close=10.0,
            up_limit=11.0,
            down_limit=9.0,
        )
        for day, implied in ((SESSIONS[2], 10.0 * 1.0 / 1.1), (SESSIONS[3], 10.0 * 1.1 / 1.0))
    ]
    # Reproduced before it was recorded: both bars and both factors, one security at a time.
    assert upstream.refetches() == [
        *RETURN_PATH_REFETCHES,
        (DAILY_DATASET, RETURN_PATH_CODE, _compact(SESSIONS[2])),
        (DAILY_DATASET, RETURN_PATH_CODE, _compact(SESSIONS[3])),
        ("adj_factor", RETURN_PATH_CODE, _compact(SESSIONS[2])),
        ("adj_factor", RETURN_PATH_CODE, _compact(SESSIONS[3])),
    ]
    assert "pre_close/adj_factor disagreement" in result.output
    assert "BUDGET return-path-reproduction 8 requests" in result.output
    # Review round 2, Minor 5: a decision drops nothing, and the value is the implied pre_close.
    (entry,) = [
        item
        for item in json.loads(result.stdout)["builds"][0]["defects"]
        if item["kind"] == "pre_close_corroborated_over_adj_factor"
        and item["trade_date"] == SESSIONS[2].isoformat()
    ]
    assert entry["implied_pre_close"] == pytest.approx(10.0 / 1.1)
    assert "valuation_close" not in entry
    assert "dropped" not in cli._defect_line(entry)
    assert "implied_pre_close=" in cli._defect_line(entry)


def test_a_disagreement_no_band_decides_is_recorded_as_unknowable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`000010.SZ`'s shape: the band is centred on the published 10.0, and the session closes at
    12.0 outside it, so the band did not govern that session and corroborates nothing."""
    frame = Frame(return_path="unknowable")
    result, upstream = _build(tmp_path, frame, monkeypatch, "adj_factor", "price", "stk_limit")

    assert result.exit_code == PanelExit.ok, result.output
    assert _return_path_defects(tmp_path) == [
        UpstreamDefect(
            ts_code=RETURN_PATH_CODE,
            trade_date=SESSIONS[2],
            source_dataset=PRICE_LIMIT_DATASET,
            kind="pre_close_contradicts_adj_factor",
            bar_close=12.0,
            valuation_close=10.0 * 1.0 / 1.1,
            previous_bar_close=10.0,
            up_limit=11.0,
            down_limit=9.0,
        )
    ]
    assert upstream.refetches() == RETURN_PATH_REFETCHES
    (decided,) = recorded_return_paths(_defects(tmp_path)).values()
    assert decided.path is None


def test_a_disagreement_a_re_fetch_does_not_reproduce_refuses_the_limit_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disagreement is recorded only once the upstream publishes it twice. A per-security
    re-fetch answering another factor says the stored `adj_factor` is not what the upstream now
    serves, and the `stk_limit` year -- whose record would decide from it -- is refused."""
    frame = Frame(return_path="published", return_path_refetch_factor=1.2)
    result, _ = _build(tmp_path, frame, monkeypatch, "adj_factor", "price", "stk_limit")

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "a re-fetch of adj_factor on 2013-11-13 answered (1.2,)" in result.output
    assert _store(tmp_path).registered_years(PRICE_LIMIT_DATASET) == ()
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()


def test_a_limit_year_with_no_stored_price_year_judges_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is the `stk_limit` target's, and it decides from the stored `daily` and
    `adj_factor` years. With neither stored there is nothing to judge and nothing is asked."""
    frame = Frame(return_path="published")
    result, upstream = _build(tmp_path, frame, monkeypatch, "stk_limit")

    assert result.exit_code == PanelExit.ok, result.output
    assert upstream.refetches() == []
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()


# --- review round 1: the decisions from the stored bands, with no band fetched again ----------

STORE_ONLY: tuple[str, ...] = ("--return-paths-from-store",)


def _judge_from_store(runtime: Path, frame: Frame, monkeypatch: pytest.MonkeyPatch) -> Any:
    upstream = ScriptedUpstream(frame)
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: upstream)
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK)
    arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(YEAR)]
    arguments += ["--as-of", AS_OF, "--json", *STORE_ONLY, "--dataset", PRICE_LIMIT_DATASET]
    return runner.invoke(app, arguments), upstream


def test_the_decisions_are_judged_from_the_stored_bands_without_fetching_a_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 1, Minor 5: a store whose years were built before `V2-P6-020` holds every
    band the decision needs. `--return-paths-from-store` judges from the stored `daily`,
    `adj_factor` and `stk_limit`, spends only the four-request reproduction per disputed pair --
    no whole-market request of any dataset -- and replaces only the return-path rows: the
    `stk_limit` target's other records (the zero/zero band on a halt here) stay as stored."""
    frame = Frame(return_path="published", limit_placeholder=True)
    built, _ = _build(tmp_path, frame, monkeypatch, "adj_factor", "price", "stk_limit")
    assert built.exit_code == PanelExit.ok, built.output
    full = _defects(tmp_path)
    store = _store(tmp_path)
    kept = [d for d in full if d.kind not in RETURN_PATH_KINDS]
    assert kept and len(kept) < len(full)
    # The store as a build before `V2-P6-020` left it: the same partitions, no decision.
    write_upstream_defects(
        store,
        None,
        year=YEAR,
        source_datasets=frozenset({PRICE_LIMIT_DATASET}),
        kinds=frozenset(RETURN_PATH_KINDS),
    )
    assert list(_defects(tmp_path)) == kept

    result, upstream = _judge_from_store(tmp_path, frame, monkeypatch)

    assert result.exit_code == PanelExit.ok, result.output
    assert _defects(tmp_path) == full
    assert all("ts_code" in payload["params"] for payload in upstream.payloads)
    assert len(upstream.payloads) == 8  # two disputed pairs, four requests each
    # Review round 2, Minor 4: the reproduction states its size before it sends anything.
    assert "BUDGET return-path-reproduction 8 requests" in result.output
    assert "RETURN-PATHS year=2013: 2 recorded from the stored stk_limit" in result.output
    # Review round 2, N1: the stored factor builds these decisions may have made stale.
    assert "openalpha factor stale-return-paths" in result.output


def test_judging_from_the_store_is_the_limit_target_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = ScriptedUpstream(Frame())
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: upstream)
    arguments = ["panel", "build", "--runtime-dir", str(tmp_path), "--year", str(YEAR)]
    arguments += [*STORE_ONLY, "--dataset", PRICE_LIMIT_DATASET, "--dataset", "price"]

    result = runner.invoke(app, arguments)

    assert result.exit_code == PanelExit.bad_request, result.output
    assert "--return-paths-from-store" in result.output
    assert upstream.payloads == []


def test_judging_from_a_store_with_no_band_year_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame(return_path="published")
    built, _ = _build(tmp_path, frame, monkeypatch, "adj_factor", "price")
    assert built.exit_code == PanelExit.ok, built.output

    result, upstream = _judge_from_store(tmp_path, frame, monkeypatch)

    assert result.exit_code == PanelExit.unhealthy, result.output
    assert "no stored stk_limit year=2013" in result.output
    assert upstream.payloads == []


# --- review round 1, Minor 4: the year before is read only where a pair reaches into it -----------


def _session_batch(dataset: str, rows: Sequence[tuple[str, date, tuple[object, ...]]]) -> Any:
    names = {
        DAILY_DATASET: (("trade_date", "string"), ("close", "float"), ("pre_close", "float")),
        "adj_factor": (("factor_date", "string"), ("adj_factor", "float")),
        PRICE_LIMIT_DATASET: (
            ("trade_date", "string"),
            ("up_limit", "float"),
            ("down_limit", "float"),
        ),
    }[dataset]
    instants = tuple(datetime.combine(day, time(8, 30), tzinfo=UTC) for _, day, _ in rows)
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=dataset,
        kind=dataset,
        as_of=CLOCK,
        fetched_at=CLOCK,
        status="success",
        subjects=tuple(code for code, _, _ in rows),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=instants,
            revision_time=instants,
        ),
        columns=tuple(
            PanelColumn(
                name,
                kind,  # type: ignore[arg-type]
                tuple(
                    day.isoformat() if position == 0 else values[position - 1]
                    for _, day, values in rows
                ),
            )
            for position, (name, kind) in enumerate(names)
        ),
    )


def test_the_year_before_is_read_only_for_a_first_pair_on_a_fresh_session() -> None:
    """`600000.SH` trades all three sessions; `600001.SH` first trades on the last. A full build
    reaches back for both; an incremental one whose slice is the last session reaches back only
    for the security whose first pair of the year lands on it; a slice that holds no one's first
    session does not read the year before at all."""
    first, second, third = SESSIONS[:3]
    bars = _session_batch(
        DAILY_DATASET,
        [
            ("600000.SH", first, (10.0, 10.0)),
            ("600000.SH", second, (10.0, 10.0)),
            ("600000.SH", third, (10.0, 10.0)),
            ("600001.SH", third, (10.0, 10.0)),
        ],
    )
    factors = _session_batch(
        "adj_factor",
        [(code, day, (1.0,)) for code in ("600000.SH", "600001.SH") for day in (first, third)],
    )
    asked: list[frozenset[str]] = []

    def earlier(subjects: frozenset[str]) -> tuple[Sequence[Any], Sequence[Any]]:
        asked.append(subjects)
        return (), ()

    def judge(fresh: Any) -> None:
        reconcile_return_paths(
            [_session_batch(PRICE_LIMIT_DATASET, [("600000.SH", third, (11.0, 9.0))])],
            bars=[bars],
            factors=[factors],
            earlier=earlier,
            answerable_through=third,
            refetch=lambda *args: pytest.fail("nothing disagrees here"),
            fresh=fresh,
        )

    judge(None)
    judge(lambda day: day >= third)
    judge(lambda day: day == second)

    assert asked == [frozenset({"600000.SH", "600001.SH"}), frozenset({"600001.SH"})]


def test_a_first_pair_is_judged_again_when_the_year_before_under_it_changed() -> None:
    """Review round 2, Minor 3. `600000.SH`'s first pair of the year is on a session this build
    did not fetch, and a decision for it is stored -- judged on a previous close of 12.0. The
    stored year before now says 10.0. A decision is a function of both closes, the `pre_close`
    and both factors, so the pair is judged again from the year before, re-fetched, and recorded
    as the rows now say rather than written again as stored."""
    first, second, third = SESSIONS[:3]
    before = date(2013, 11, 8)
    bars = _session_batch(
        DAILY_DATASET,
        [
            ("600000.SH", first, (10.0, 10.0)),
            ("600000.SH", second, (10.0, 10.0)),
            ("600000.SH", third, (10.0, 10.0)),
        ],
    )
    factors = _session_batch("adj_factor", [("600000.SH", day, (1.1,)) for day in (first, third)])
    earlier_bars = _session_batch(DAILY_DATASET, [("600000.SH", before, (10.0, 10.0))])
    earlier_factors = _session_batch("adj_factor", [("600000.SH", before, (1.0,))])
    stale = UpstreamDefect(
        ts_code="600000.SH",
        trade_date=first,
        source_dataset=PRICE_LIMIT_DATASET,
        kind="pre_close_corroborated_over_adj_factor",
        bar_close=10.0,
        valuation_close=12.0 * 1.0 / 1.1,
        previous_bar_close=12.0,
        up_limit=11.0,
        down_limit=9.0,
    )
    asked: list[frozenset[str]] = []
    refetched: list[tuple[str, date, date]] = []

    def earlier(subjects: frozenset[str]) -> tuple[Sequence[Any], Sequence[Any]]:
        asked.append(subjects)
        return [earlier_bars], [earlier_factors]

    def refetch(code: str, previous_day: date, day: date) -> tuple[list[Any], list[Any]]:
        refetched.append((code, previous_day, day))
        return [earlier_bars, bars], [earlier_factors, factors]

    reconciled = reconcile_return_paths(
        [_session_batch(PRICE_LIMIT_DATASET, [("600000.SH", first, (11.0, 9.0))])],
        bars=[bars],
        factors=[factors],
        earlier=earlier,
        answerable_through=third,
        refetch=refetch,
        fresh=lambda day: day >= third,
        stored_decisions={("600000.SH", first): stale},
    )

    assert asked == [frozenset({"600000.SH"})]
    assert refetched == [("600000.SH", before, first)]
    (decision,) = reconciled.defects
    assert decision.previous_bar_close == 10.0
    assert decision.valuation_close == pytest.approx(10.0 / 1.1)


def test_a_decision_the_stored_rows_no_longer_support_is_named_as_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 3, Minor 2. A session judged unknowable, then its `adj_factor` and `daily`
    rebuilt so the two statements agree: re-judging records nothing for it, and the stored
    decision is replaced by none. A factor build that abstained on it is stale and
    `factor stale-return-paths` cannot see it -- neither the store nor the manifest keeps the
    decision -- so the re-judgement names it on a `RETIRED-RETURN-PATH` line, the one moment it
    is known."""
    unknowable = Frame(return_path="unknowable")
    built, _ = _build(tmp_path, unknowable, monkeypatch, "adj_factor", "price", "stk_limit")
    assert built.exit_code == PanelExit.ok, built.output
    assert [d.kind for d in _return_path_defects(tmp_path)] == ["pre_close_contradicts_adj_factor"]
    # Judged again on the same rows, the decision is the same one: nothing is retired.
    again, _ = _judge_from_store(tmp_path, unknowable, monkeypatch)
    assert again.exit_code == PanelExit.ok, again.output
    assert "RETIRED-RETURN-PATH" not in again.output
    agreeing = Frame()
    rebuilt, _ = _build(tmp_path, agreeing, monkeypatch, "adj_factor", "price")
    assert rebuilt.exit_code == PanelExit.ok, rebuilt.output

    result, upstream = _judge_from_store(tmp_path, agreeing, monkeypatch)

    assert result.exit_code == PanelExit.ok, result.output
    # The year's only record was the retired decision, so nothing is left to store.
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()
    assert upstream.payloads == []
    assert (
        f"RETIRED-RETURN-PATH {RETURN_PATH_CODE} {SESSIONS[2].isoformat()} was "
        "pre_close_contradicts_adj_factor"
    ) in result.output
