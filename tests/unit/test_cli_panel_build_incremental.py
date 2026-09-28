"""`V2-P6-003`: `panel build --incremental` equals a full rebuild at the same `--as-of`.

Every test here is offline. `panel build` is driven through the real Typer app and the real
`TushareProvider`, with a scripted transport injected at `cli._panel_transport`, so the decoder,
the point-in-time filter, the targeted re-fetch, the `V2-P6-013` reconciliations and every
write-time guard run for real; the fixtures are generated here at test time.

The frame is January 2026: twelve open sessions (5 to 20 January), twenty-four securities, and
two build instants. `T1` stops the year at `SESSIONS[5]` (12 January) and `T2` at `SESSIONS[10]`
(19 January), so an incremental build at `T2` over a store built at `T1` fetches `SESSIONS[5:11]`:
the previously-last session again (the one-session overlap) and the five after it.

The corpus carries every upstream defect `V2-P6-013` names, placed where an incremental build can
get them wrong:

- **in the carried sessions** (not re-fetched): a valuation with no bar on `SESSIONS[1]`, a
  zero/zero band on a whole-day halt on `SESSIONS[2]`, and a security whose first two sessions of
  bars, bands and factors predate its listing;
- **in the overlap session** `SESSIONS[5]`: a valuation with no bar, and a valuation contradicting
  its bar -- `valuation_contradicts_unconfirmed_bar` at `T1`, where it is the last session, and
  corroborated by the next session's `pre_close` at `T2`;
- **on `T2`'s last session**: another contradiction, unconfirmed in both builds.

`Corpus(placeholders=True)` adds `V2-P6-017`'s null-close `daily_basic` placeholders with no bar:
one halted all day on a carried session, one with no halt row on the overlap session.
"""

from __future__ import annotations

import json
import shlex
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openalpha_cn import cli, panel_ingest
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET, INDEX_WEIGHT_INDEX_CODES
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import panel_health_report
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
    WITHDRAWN_CONFIRMED_AT_COLUMN,
    WITHDRAWN_ORIGINAL_INGESTED_TIME_COLUMN,
    WITHDRAWN_ROWS_DATASETS,
    carry_stored_sessions_forward,
    load_upstream_defects,
)

runner = CliRunner()

SECRET_TOKEN = "sk-incremental-panel-token-must-not-leak-60003"
YEAR = 2026
SESSIONS: tuple[date, ...] = tuple(
    date(2026, 1, day) for day in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20)
)
T1_LAST = SESSIONS[5]
T2_LAST = SESSIONS[10]
CLOCK = datetime(2026, 9, 26, 4, 0, tzinfo=UTC)
"""The wall clock: long after every session here, so it never binds what a fetch may know."""


def _as_of(last: date) -> str:
    """Midday on the day after `last`: that session has published and the next has not."""
    return f"{(last + timedelta(days=1)).isoformat()}T12:00:00+08:00"


T1 = _as_of(T1_LAST)
T2 = _as_of(T2_LAST)
T2_INSTANT = datetime.fromisoformat(T2)

SESSION_TARGETS: tuple[str, ...] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    "price",
    PRICE_LIMIT_DATASET,
)
SESSION_APIS: tuple[str, ...] = (
    ADJ_FACTOR_DATASET,
    SUSPENSION_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    PRICE_LIMIT_DATASET,
)
COMPARED: tuple[str, ...] = (
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
    ADJ_FACTOR_DATASET,
    PRICE_LIMIT_DATASET,
    UPSTREAM_DEFECTS_DATASET,
)

WITHDRAWN_ROWS: tuple[str, ...] = tuple(WITHDRAWN_ROWS_DATASETS.values())
"""The `V2-P6-016` datasets that keep withdrawn rows whole, compared beside `COMPARED`."""

FILLERS: tuple[str, ...] = tuple(f"{600000 + index}.SH" for index in range(20))
NO_BAR_EARLY = "000022.SZ"
NO_BAR_OVERLAP = "000023.SZ"
HALTED = "000509.SZ"
PRELISTED = "920476.BJ"
PRELISTED_LISTING = SESSIONS[3]
SECURITIES: tuple[str, ...] = (NO_BAR_EARLY, NO_BAR_OVERLAP, HALTED, *FILLERS)
MISMATCH_OVERLAP = FILLERS[2]
MISMATCH_LAST = FILLERS[3]
ADJUSTED = FILLERS[4]
RESUMING = FILLERS[6]
HALTED_ACROSS = FILLERS[7]
# `V2-P6-017`: `daily_basic` placeholders with a null close and no bar -- one halted all day on a
# carried session, one with no halt row on the overlap session.
PLACEHOLDER_CARRIED = "000029.SZ"
PLACEHOLDER_OVERLAP = "200011.SZ"
PLACEHOLDERS: tuple[tuple[str, date], ...] = (
    (PLACEHOLDER_CARRIED, SESSIONS[1]),
    (PLACEHOLDER_OVERLAP, T1_LAST),
)

CALENDAR_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
REGISTRY_FIELDS = ["ts_code", "name", "exchange", "market", "list_status", "list_date"]
REGISTRY_FIELDS += ["delist_date"]
BAR_FIELDS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg"]
BAR_FIELDS += ["vol", "amount"]
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
FACTOR_FIELDS = ["ts_code", "trade_date", "adj_factor"]
INDEX_WEIGHT_FIELDS = ["index_code", "con_code", "trade_date", "weight"]


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _response(fields: Sequence[str], items: Sequence[Sequence[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": list(fields), "items": [list(i) for i in items], "has_more": False},
    }


@dataclass(frozen=True)
class Corpus:
    """What the scripted upstream publishes. The defaults are the equivalence corpus."""

    defects: bool = True
    """Every `V2-P6-013` defect the module docstring lists."""
    closed: tuple[date, ...] = ()
    """Sessions the calendar reports closed, and on which nothing is published."""
    list_date: date = PRELISTED_LISTING
    """`PRELISTED`'s listing in the registry."""
    halted_across: bool = False
    """`HALTED_ACROSS` contradicts its bar on `SESSIONS[3]`, is halted all of `SESSIONS[4:6]`,
    and resumes on `SESSIONS[6]` with a `pre_close` that corroborates the disputed bar."""
    weights_through: tuple[tuple[str, int], ...] = ()
    """`(index_code, month)`: that index publishes no weighting after `month` (0: none at all)."""
    placeholders: bool = False
    """`PLACEHOLDERS`: a null-close `daily_basic` row and no bar on that session (`V2-P6-017`)."""
    once: tuple[tuple[str, str], ...] = ()
    """`(api_name, code)`: `code` has a row in that dataset on `T1_LAST` and on no other session
    -- the live shape of `V2-P6-016`, three funds with one `stk_limit` row each in the year."""
    withdrawn: tuple[tuple[str, str, date], ...] = ()
    """`(api_name, code, session)`: rows the upstream served once and no longer serves."""
    republished_on_refetch: bool = False
    """The second whole-market request for a withdrawn row's session serves it again."""
    traded: tuple[tuple[str, date], ...] = ()
    """`(code, session)`: an extra `daily` bar and nothing else -- a security halted part of the
    session, whose `suspend_d` row sits beside a bar."""
    served_only: tuple[tuple[str, date, int], ...] = ()
    """`(api_name, session, n)`: every whole-market answer for it carries only its first `n` rows
    -- an outage (`n == 0`) or a short answer, the same on every request."""
    stepped: tuple[str, ...] = ()
    """Securities whose `adj_factor` steps from 1.0 to 1.5 on `T1_LAST`, so the compressed
    partition keeps that session as a change row."""
    restepped: tuple[str, ...] = ()
    """As `stepped`, and a second step to 2.0 on `SESSIONS[6]`, the session after `T1_LAST`, so
    the compressed partition keeps that next session's row too."""


class ScriptedUpstream:
    """A `TushareTransport` answering from a `Corpus`, recording every payload it was sent."""

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.payloads: list[dict[str, Any]] = []
        self.asked: dict[tuple[str, date], int] = {}

    # -- what was asked ------------------------------------------------------------------------

    def session_requests(self) -> list[tuple[str, date]]:
        """Every whole-market session request, as `(api_name, session)`, in order."""
        return [
            (str(p["api_name"]), datetime.strptime(p["params"]["trade_date"], "%Y%m%d").date())
            for p in self.payloads
            if "trade_date" in p["params"] and "ts_code" not in p["params"]
        ]

    def sessions_requested(self, api_name: str) -> list[date]:
        """The distinct sessions `api_name` was asked for whole-market, ascending.

        Distinct, because a session with two or more disputed securities is re-fetched whole
        (`V2-P6-013`), which is a second request for a session already in the slice.
        """
        return sorted({day for name, day in self.session_requests() if name == api_name})

    # -- the corpus ----------------------------------------------------------------------------

    def _open(self) -> tuple[date, ...]:
        return tuple(day for day in SESSIONS if day not in self.corpus.closed)

    def _codes(self) -> tuple[str, ...]:
        if self.corpus.placeholders:
            return (*SECURITIES, *(code for code, _ in PLACEHOLDERS))
        return SECURITIES

    def _placeholder(self, code: str, day: date) -> bool:
        return self.corpus.placeholders and (code, day) in PLACEHOLDERS

    def _traded(self, code: str, day: date) -> bool:
        if self._placeholder(code, day):
            return False
        if self.corpus.defects and code == HALTED and day == SESSIONS[2]:
            return False
        return not (self.corpus.halted_across and code == HALTED_ACROSS and day in SESSIONS[4:6])

    def _close(self, code: str, day: date) -> float:
        base = 10.0 + self._codes().index(code) / 10
        return round(base + 0.1 * self._open().index(day), 2) if code == RESUMING else base

    def _pre_close(self, code: str, day: date) -> float:
        if code != RESUMING:
            return self._close(code, day)
        position = self._open().index(day)
        return self._close(code, self._open()[position - 1]) if position else self._close(code, day)

    def _bars(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if self.corpus.defects and day in SESSIONS[:2]:
            first = day == SESSIONS[0]
            pre_close, pct_chg = (None, None) if first else (18.0, 0.0)
            rows.append(
                [PRELISTED, _compact(day), 18.0, 18.0, 18.0, 18.0, pre_close, pct_chg, 300.0, 540.0]
            )
        if self.corpus.defects and day >= PRELISTED_LISTING:
            rows.append([PRELISTED, _compact(day), 20.0, 20.0, 20.0, 20.0, 20.0, 0.0, 30.0, 60.0])
        for code in self._codes():
            if not self._traded(code, day):
                continue
            if self.corpus.defects and (code, day) in (
                (NO_BAR_EARLY, SESSIONS[1]),
                (NO_BAR_OVERLAP, T1_LAST),
            ):
                continue
            close, pre_close = self._close(code, day), self._pre_close(code, day)
            pct_chg = round((close / pre_close - 1) * 100, 2)
            rows.append(
                [code, _compact(day), close, close, close, close, pre_close, pct_chg, 10.0, 100.0]
            )
        for code in self._once(DAILY_DATASET, day):
            rows.append([code, _compact(day), 5.0, 5.0, 5.0, 5.0, 5.0, 0.0, 1.0, 5.0])
        for code, traded_on in self.corpus.traded:
            if traded_on == day:
                rows.append([code, _compact(day), 7.0, 7.0, 7.0, 7.0, 7.0, 0.0, 1.0, 7.0])
        return rows

    def _once(self, api_name: str, day: date) -> list[str]:
        return [code for name, code in self.corpus.once if name == api_name and day == T1_LAST]

    def _valuation_close(self, code: str, day: date) -> float:
        if self.corpus.defects and (code, day) in (
            (MISMATCH_OVERLAP, T1_LAST),
            (MISMATCH_LAST, T2_LAST),
        ):
            return round(self._close(code, day) + 0.07, 2)
        if self.corpus.halted_across and (code, day) == (HALTED_ACROSS, SESSIONS[3]):
            return round(self._close(code, day) + 0.07, 2)
        return self._close(code, day)

    def _valuations(self, day: date) -> list[list[Any]]:
        rows = [
            [code, _compact(day), self._valuation_close(code, day), *([1.0] * len(VALUATION_EXTRA))]
            for code in self._codes()
            if self._traded(code, day)
        ]
        for code in self._codes():
            if self._placeholder(code, day):
                extra = [0.87 if name == "volume_ratio" else None for name in VALUATION_EXTRA]
                rows.append([code, _compact(day), None, *extra])
        if self.corpus.defects and day >= PRELISTED_LISTING:
            rows.append([PRELISTED, _compact(day), 20.0, *([1.0] * len(VALUATION_EXTRA))])
        for code in self._once(DAILY_BASIC_DATASET, day):
            rows.append([code, _compact(day), 5.0, *([1.0] * len(VALUATION_EXTRA))])
        return rows

    def _halts(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if day == self._open()[0]:
            rows.append([RESUMING, _compact(day), "R", None])
        if self.corpus.defects and day == SESSIONS[2]:
            rows.append([HALTED, _compact(day), "S", None])
        if self.corpus.halted_across and day in SESSIONS[4:6]:
            rows.append([HALTED_ACROSS, _compact(day), "S", None])
        if self._placeholder(PLACEHOLDER_CARRIED, day):
            rows.append([PLACEHOLDER_CARRIED, _compact(day), "S", None])
        for code in self._once(SUSPENSION_DATASET, day):
            rows.append([code, _compact(day), "S", None])
        return rows

    def _limits(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if self.corpus.defects and day in SESSIONS[:2]:
            rows.append([PRELISTED, _compact(day), 19.8, 16.2])
        if self.corpus.defects and day >= PRELISTED_LISTING:
            rows.append([PRELISTED, _compact(day), 22.0, 18.0])
        for code in self._codes():
            if self._placeholder(code, day) and code == PLACEHOLDER_OVERLAP:
                continue
            close = self._pre_close(code, day)
            band = [round(close * 1.1, 2), round(close * 0.9, 2)]
            if self.corpus.defects and code == HALTED and day == SESSIONS[2]:
                band = [0.0, 0.0]
            rows.append([code, _compact(day), *band])
        for code in self._once(PRICE_LIMIT_DATASET, day):
            rows.append([code, _compact(day), 1.1, 0.9])
        return rows

    def _factors(self, day: date) -> list[list[Any]]:
        def factor(code: str) -> float:
            if code in self.corpus.stepped:
                return 1.5 if day >= T1_LAST else 1.0
            if code in self.corpus.restepped:
                return 2.0 if day >= SESSIONS[6] else 1.5 if day >= T1_LAST else 1.0
            if code != ADJUSTED:
                return 1.0
            return 1.2 if day >= SESSIONS[8] else 1.1 if day >= SESSIONS[3] else 1.0

        rows = [[code, _compact(day), factor(code)] for code in self._codes()]
        if self.corpus.defects and (day in SESSIONS[:2] or day >= PRELISTED_LISTING):
            rows.append([PRELISTED, _compact(day), 1.0])
        for code in self._once(ADJ_FACTOR_DATASET, day):
            rows.append([code, _compact(day), 1.0])
        return rows

    def _registry(self) -> list[list[Any]]:
        rows = [[code, code, "SSE", "主板", "L", "20100104", None] for code in self._codes()]
        if self.corpus.defects:
            rows.append(
                [PRELISTED, PRELISTED, "BSE", "北交所", "L", _compact(self.corpus.list_date), None]
            )
        return rows

    def _index_weights(self, params: Mapping[str, str]) -> list[list[Any]]:
        start = datetime.strptime(str(params["start_date"]), "%Y%m%d").date()
        last = dict(self.corpus.weights_through).get(str(params["index_code"]), 12)
        if start.month > last:
            return []
        day = _compact(start.replace(day=28))
        return [
            [params["index_code"], FILLERS[0], day, 60.0 + start.month],
            [params["index_code"], FILLERS[1], day, 40.0 - start.month],
        ]

    # -- the transport -------------------------------------------------------------------------

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api_name = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api_name == TRADING_CALENDAR_DATASET:
            items: list[list[Any]] = []
            previous: str | None = None
            day = date(YEAR, 1, 1)
            while day <= date(YEAR, 12, 31):
                is_open = day in self._open()
                items.append([params["exchange"], _compact(day), 1 if is_open else 0, previous])
                if is_open:
                    previous = _compact(day)
                day += timedelta(days=1)
            return _response(CALENDAR_FIELDS, items)
        if api_name == STOCK_BASIC_DATASET:
            return _response(REGISTRY_FIELDS, self._registry())
        if api_name == INDEX_WEIGHT_DATASET:
            return _response(INDEX_WEIGHT_FIELDS, self._index_weights(params))
        day = datetime.strptime(params["trade_date"], "%Y%m%d").date()
        answers = {
            DAILY_DATASET: (BAR_FIELDS, self._bars),
            DAILY_BASIC_DATASET: (VALUATION_FIELDS, self._valuations),
            SUSPENSION_DATASET: (HALT_FIELDS, self._halts),
            PRICE_LIMIT_DATASET: (LIMIT_FIELDS, self._limits),
            ADJ_FACTOR_DATASET: (FACTOR_FIELDS, self._factors),
        }
        if api_name not in answers:
            raise AssertionError(f"unscripted dataset {api_name}")
        fields, answer = answers[api_name]
        rows = answer(day) if day in self._open() else []
        if "ts_code" in params:
            rows = [row for row in rows if row[0] == params["ts_code"]]
        else:
            self.asked[(api_name, day)] = self.asked.get((api_name, day), 0) + 1
        again = self.corpus.republished_on_refetch and self.asked.get((api_name, day), 0) > 1
        if not again:
            rows = [row for row in rows if (api_name, row[0], day) not in self.corpus.withdrawn]
        if "ts_code" not in params:
            for name, short_on, keep in self.corpus.served_only:
                if (name, short_on) == (api_name, day):
                    rows = rows[:keep]
        return _response(fields, rows)


@dataclass
class Build:
    exit_code: int
    output: str
    stdout: str
    upstream: ScriptedUpstream
    hashes: dict[str, str | None] = field(default_factory=dict)


def _store(runtime_dir: Path) -> PanelStore:
    return PanelStore(runtime_dir / "panel")


def _hashes(runtime_dir: Path, datasets: Sequence[str] = COMPARED) -> dict[str, str | None]:
    store = _store(runtime_dir)
    hashes: dict[str, str | None] = {}
    for dataset in datasets:
        coverage = store.read_coverage(dataset, YEAR)
        hashes[dataset] = None if coverage is None else coverage.partition_content_hash
    return hashes


def run_build(
    runtime_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    as_of: str,
    incremental: bool,
    corpus: Corpus | None = None,
    targets: Sequence[str] = SESSION_TARGETS,
) -> Build:
    upstream = ScriptedUpstream(corpus or Corpus())
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: upstream)
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK)
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir), "--year", str(YEAR)]
    arguments += ["--as-of", as_of, "--json"]
    if incremental:
        arguments.append("--incremental")
    for target in targets:
        arguments += ["--dataset", target]
    result = runner.invoke(app, arguments)
    assert SECRET_TOKEN not in result.output
    return Build(
        exit_code=result.exit_code,
        output=result.output,
        stdout=result.stdout,
        upstream=upstream,
        hashes=_hashes(runtime_dir, (*COMPARED, *WITHDRAWN_ROWS)),
    )


def _defects(runtime_dir: Path) -> list[tuple[str, str, date, str]]:
    return [
        (d.ts_code, d.source_dataset, d.trade_date, d.kind)
        for d in load_upstream_defects(_store(runtime_dir), years=(YEAR,), as_of=T2_INSTANT)
    ]


def _between(first: date, last: date) -> list[date]:
    return [day for day in SESSIONS if first <= day <= last]


# --- the equivalence --------------------------------------------------------------------------


def test_incremental_equals_full_rebuild_at_the_same_as_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = run_build(tmp_path / "full", monkeypatch, as_of=T2, incremental=False)
    assert full.exit_code == PanelExit.ok, full.output
    first = run_build(tmp_path / "inc", monkeypatch, as_of=T1, incremental=False)
    assert first.exit_code == PanelExit.ok, first.output
    assert (
        MISMATCH_OVERLAP,
        DAILY_BASIC_DATASET,
        T1_LAST,
        "valuation_contradicts_unconfirmed_bar",
    ) in (_defects(tmp_path / "inc"))

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert all(full.hashes[name] is not None for name in COMPARED)
    for target in COMPARED:
        assert inc.hashes[target] == full.hashes[target], target
    # The overlap session's contradiction was judged again against the new next session.
    assert _defects(tmp_path / "inc") == _defects(tmp_path / "full")
    assert (
        MISMATCH_OVERLAP,
        DAILY_BASIC_DATASET,
        T1_LAST,
        "valuation_contradicts_corroborated_bar",
    ) in _defects(tmp_path / "inc")
    assert {
        (NO_BAR_EARLY, DAILY_BASIC_DATASET, SESSIONS[1], "valuation_without_bar"),
        (HALTED, PRICE_LIMIT_DATASET, SESSIONS[2], "limit_placeholder_on_halt"),
        (PRELISTED, ADJ_FACTOR_DATASET, SESSIONS[0], "bar_before_listing"),
        (PRELISTED, DAILY_DATASET, SESSIONS[1], "bar_before_listing"),
        (NO_BAR_OVERLAP, DAILY_BASIC_DATASET, T1_LAST, "valuation_without_bar"),
        (MISMATCH_LAST, DAILY_BASIC_DATASET, T2_LAST, "valuation_contradicts_unconfirmed_bar"),
    } <= set(_defects(tmp_path / "inc"))


def test_incremental_equals_full_rebuild_with_valuation_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P6-017`: a placeholder on a carried session is carried with its record, and one on
    the overlap session is fetched, re-fetched and recorded again -- the same bytes as a full
    rebuild at the same `--as-of`."""
    corpus = Corpus(placeholders=True)
    full = run_build(tmp_path / "full", monkeypatch, as_of=T2, incremental=False, corpus=corpus)
    assert full.exit_code == PanelExit.ok, full.output
    first = run_build(tmp_path / "inc", monkeypatch, as_of=T1, incremental=False, corpus=corpus)
    assert first.exit_code == PanelExit.ok, first.output

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=corpus)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert all(full.hashes[name] is not None for name in COMPARED)
    for target in COMPARED:
        assert inc.hashes[target] == full.hashes[target], target
    assert _defects(tmp_path / "inc") == _defects(tmp_path / "full")
    assert {
        (PLACEHOLDER_CARRIED, DAILY_BASIC_DATASET, SESSIONS[1], "valuation_placeholder_on_halt"),
        (PLACEHOLDER_OVERLAP, DAILY_BASIC_DATASET, T1_LAST, "valuation_placeholder_without_bar"),
    } <= set(_defects(tmp_path / "inc"))
    assert inc.upstream.sessions_requested(DAILY_BASIC_DATASET) == _between(T1_LAST, T2_LAST)


def test_incremental_fetches_only_sessions_after_the_stored_horizon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = Corpus(defects=False)
    first = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=clean)
    assert first.exit_code == PanelExit.ok, first.output
    assert first.upstream.sessions_requested(DAILY_DATASET) == _between(SESSIONS[0], T1_LAST)

    inc = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True, corpus=clean)

    assert inc.exit_code == PanelExit.ok, inc.output
    # The previously-last session again (the overlap), and every session after it, once each.
    expected = _between(T1_LAST, T2_LAST)
    for api_name in SESSION_APIS:
        assert inc.upstream.sessions_requested(api_name) == expected, api_name
    assert len(inc.upstream.session_requests()) == len(SESSION_APIS) * len(expected)


def test_an_incremental_build_over_an_empty_store_is_the_full_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = run_build(tmp_path / "full", monkeypatch, as_of=T2, incremental=False)
    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert inc.hashes == full.hashes
    assert inc.upstream.sessions_requested(DAILY_DATASET) == _between(SESSIONS[0], T2_LAST)


def test_an_unconfirmed_defect_before_the_last_session_moves_the_overlap_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A security halted from the day after its disputed close through `T1` was recorded
    unconfirmed with every absence explained; the full build at `T2` judges it against the bar it
    resumes on, so the incremental build has to fetch again from the disputed session."""
    corpus = Corpus(halted_across=True)
    full = run_build(tmp_path / "full", monkeypatch, as_of=T2, incremental=False, corpus=corpus)
    assert full.exit_code == PanelExit.ok, full.output
    first = run_build(tmp_path / "inc", monkeypatch, as_of=T1, incremental=False, corpus=corpus)
    assert first.exit_code == PanelExit.ok, first.output
    assert (
        HALTED_ACROSS,
        DAILY_BASIC_DATASET,
        SESSIONS[3],
        "valuation_contradicts_unconfirmed_bar",
    ) in _defects(tmp_path / "inc")

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=corpus)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert inc.hashes == full.hashes
    assert inc.upstream.sessions_requested(DAILY_DATASET) == _between(SESSIONS[3], T2_LAST)
    assert inc.upstream.sessions_requested(PRICE_LIMIT_DATASET) == _between(T1_LAST, T2_LAST)
    assert (
        HALTED_ACROSS,
        DAILY_BASIC_DATASET,
        SESSIONS[3],
        "valuation_contradicts_corroborated_bar",
    ) in _defects(tmp_path / "inc")


# --- what an incremental build refuses rather than bridges ------------------------------------


def _offered_command(output: str) -> list[str]:
    """The full-rebuild command a refusal offers, as argv after `openalpha`."""
    start = output.index("openalpha panel build")
    command = output[start:].split("`")[0]
    return shlex.split(command)[1:]


def test_incremental_refuses_a_gap_rather_than_bridging_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored partitions were built against a calendar that called `SESSIONS[2]` closed; the
    calendar now reports it open, so the stored year has a hole the incremental slice would not
    fill. Refused by name, before any session is fetched, with a full rebuild that works."""
    gap = SESSIONS[2]
    first = run_build(
        tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=Corpus(closed=(gap,))
    )
    assert first.exit_code == PanelExit.ok, first.output

    inc = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True)

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert gap.isoformat() in inc.output
    assert inc.upstream.session_requests() == []
    argv = _offered_command(inc.output)
    assert "--incremental" not in argv
    assert argv[argv.index("--as-of") + 1] == T2
    assert argv[argv.index("--runtime-dir") + 1] == str(tmp_path)

    rebuilt = runner.invoke(app, argv)
    assert rebuilt.exit_code == PanelExit.ok, rebuilt.output


def test_incremental_refuses_to_shorten_a_stored_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run_build(tmp_path, monkeypatch, as_of=T2, incremental=False)
    assert first.exit_code == PanelExit.ok, first.output

    earlier = run_build(tmp_path, monkeypatch, as_of=T1, incremental=True)

    assert earlier.exit_code == PanelExit.unhealthy, earlier.output
    assert T2_LAST.isoformat() in earlier.output
    assert "cannot shorten" in earlier.output
    assert earlier.upstream.session_requests() == []


def test_incremental_refuses_a_carried_listing_the_registry_no_longer_supports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`PRELISTED`'s first two sessions were dropped as `bar_before_listing` at `T1`. If the
    registry now lists it before them, a full rebuild stores those rows, and a slice that never
    fetched them cannot -- so the incremental build refuses, naming the security."""
    first = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False)
    assert first.exit_code == PanelExit.ok, first.output

    moved = replace(Corpus(), list_date=date(2026, 1, 2))
    inc = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True, corpus=moved)

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert PRELISTED in inc.output
    assert "bar_before_listing" in inc.output
    assert "openalpha panel build" in inc.output


# --- index_weight: one publication a month ---------------------------------------------------


def test_incremental_index_weight_fetches_from_the_last_stored_month_and_equals_a_full_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    march, may = "2026-03-15T12:00:00+08:00", "2026-05-15T12:00:00+08:00"
    targets = (INDEX_WEIGHT_DATASET,)
    full = run_build(tmp_path / "full", monkeypatch, as_of=may, incremental=False, targets=targets)
    assert full.exit_code == PanelExit.ok, full.output
    first = run_build(
        tmp_path / "inc", monkeypatch, as_of=march, incremental=False, targets=targets
    )
    assert first.exit_code == PanelExit.ok, first.output

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=may, incremental=True, targets=targets)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert _hashes(tmp_path / "inc", (INDEX_WEIGHT_DATASET,)) == _hashes(
        tmp_path / "full", (INDEX_WEIGHT_DATASET,)
    )
    months = sorted(
        (str(p["params"]["index_code"]), int(str(p["params"]["start_date"])[4:6]))
        for p in inc.upstream.payloads
    )
    assert months == sorted(
        (code, month) for code in INDEX_WEIGHT_INDEX_CODES for month in (2, 3, 4, 5)
    )


@pytest.mark.parametrize(
    ("lagging", "stored_through"),
    [
        pytest.param(INDEX_WEIGHT_INDEX_CODES[0], 2, id="an-index-stored-two-months-short"),
        pytest.param(INDEX_WEIGHT_INDEX_CODES[2], 0, id="an-index-absent-from-the-stored-year"),
    ],
)
def test_incremental_index_weight_resumes_each_index_from_its_own_stored_months(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lagging: str, stored_through: int
) -> None:
    """The stored year holds every index through April except `lagging`, which the first build
    found only through `stored_through` (an end gap, legitimate then). By July it has published
    every month, so a full build stores all of them -- and so must the incremental one, rather
    than resuming `lagging` from the partition's newest month and skipping what lies between."""
    may, july = "2026-05-15T12:00:00+08:00", "2026-07-15T12:00:00+08:00"
    targets = (INDEX_WEIGHT_DATASET,)
    full = run_build(tmp_path / "full", monkeypatch, as_of=july, incremental=False, targets=targets)
    assert full.exit_code == PanelExit.ok, full.output
    short = Corpus(weights_through=((lagging, stored_through),))
    first = run_build(
        tmp_path / "inc", monkeypatch, as_of=may, incremental=False, targets=targets, corpus=short
    )
    assert first.exit_code == PanelExit.ok, first.output

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=july, incremental=True, targets=targets)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert _hashes(tmp_path / "inc", (INDEX_WEIGHT_DATASET,)) == _hashes(
        tmp_path / "full", (INDEX_WEIGHT_DATASET,)
    )
    asked = {
        (str(p["params"]["index_code"]), int(str(p["params"]["start_date"])[4:6]))
        for p in inc.upstream.payloads
    }
    assert {month for code, month in asked if code == lagging} == set(
        range(max(stored_through, 1), 8)
    )


def test_a_carry_refuses_a_stored_row_the_build_could_not_have_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The carried rows are answered with, so they must have been knowable at the build's
    instant: a store built through `T2` carried at `T1` would hand back sessions after `T1`."""
    stored = run_build(tmp_path, monkeypatch, as_of=T2, incremental=False)
    assert stored.exit_code == PanelExit.ok, stored.output
    store = _store(tmp_path)

    at_t1 = datetime.fromisoformat(T1)
    carried = carry_stored_sessions_forward(
        store, (), dataset=DAILY_DATASET, year=YEAR, before=T1_LAST, observed_at=at_t1
    )
    assert len(carried) == 1
    with pytest.raises(PanelBatchError, match="knowable only at"):
        carry_stored_sessions_forward(
            store, (), dataset=DAILY_DATASET, year=YEAR, before=T2_LAST, observed_at=at_t1
        )


# --- a row the upstream withdrew after publication (`V2-P6-016`) ------------------------------

WITHDRAWN_LIMIT = "158008.SZ"
"""A fund with one `stk_limit` row in the year, on `T1_LAST` -- the live shape."""
WITHDRAWN_PRICE = "600999.SH"
"""A security with one `daily`, `daily_basic` and `adj_factor` row in the year, on `T1_LAST`."""
WITHDRAWN_HALT = "561730.SH"
"""A security with one `suspend_d` row in the year, on `T1_LAST`."""
ONCE: tuple[tuple[str, str], ...] = (
    (PRICE_LIMIT_DATASET, WITHDRAWN_LIMIT),
    (DAILY_DATASET, WITHDRAWN_PRICE),
    (DAILY_BASIC_DATASET, WITHDRAWN_PRICE),
    (ADJ_FACTOR_DATASET, WITHDRAWN_PRICE),
    (SUSPENSION_DATASET, WITHDRAWN_HALT),
)
PARTLY_WITHDRAWN = FILLERS[11]
"""A security whose `stk_limit` row on `T1_LAST` is withdrawn while every other one stays: no
subject guard sees it, and it is recorded all the same."""
WITHDRAWN: tuple[tuple[str, str, date], ...] = (
    *((api_name, code, T1_LAST) for api_name, code in ONCE),
    (PRICE_LIMIT_DATASET, PARTLY_WITHDRAWN, T1_LAST),
)
TRADED: tuple[tuple[str, date], ...] = ((WITHDRAWN_HALT, T1_LAST),)
"""`WITHDRAWN_HALT` has a bar beside its halt row: a halt is withdrawn only against a bar."""
PUBLISHED = Corpus(once=ONCE, traded=TRADED)
"""What the upstream served at `T1`."""
WITHDRAWN_NOW = replace(PUBLISHED, withdrawn=WITHDRAWN)
"""What it serves from then on: the same corpus without the five rows on `T1_LAST`."""
T3 = _as_of(SESSIONS[11])


def _withdrawals(runtime_dir: Path, as_of: datetime = T2_INSTANT) -> set[tuple[str, str, date]]:
    if YEAR not in _store(runtime_dir).registered_years(UPSTREAM_DEFECTS_DATASET):
        return set()
    return {
        (d.ts_code, d.source_dataset, d.trade_date)
        for d in load_upstream_defects(_store(runtime_dir), years=(YEAR,), as_of=as_of)
        if d.kind == "withdrawn_after_publication"
    }


def _stored_at_t1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """One store built at `T1` from what was published then, copied to each of `names`."""
    base = run_build(tmp_path / "base", monkeypatch, as_of=T1, incremental=False, corpus=PUBLISHED)
    assert base.exit_code == PanelExit.ok, base.output
    for name in names:
        shutil.copytree(tmp_path / "base", tmp_path / name)


def test_a_withdrawal_on_the_overlap_session_is_recorded_and_equals_a_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live refusal of 2026-09-28: rows served for the stored horizon session are no longer
    served. Both builds of the same starting store succeed, record one withdrawal per row, and
    store the same bytes."""
    _stored_at_t1(tmp_path, monkeypatch, "inc", "full")

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=WITHDRAWN_NOW)
    full = run_build(
        tmp_path / "full", monkeypatch, as_of=T2, incremental=False, corpus=WITHDRAWN_NOW
    )

    assert inc.exit_code == PanelExit.ok, inc.output
    assert full.exit_code == PanelExit.ok, full.output
    expected = {(code, api_name, day) for api_name, code, day in WITHDRAWN}
    assert _withdrawals(tmp_path / "inc") == expected
    assert _withdrawals(tmp_path / "full") == expected
    kinds = [
        d.kind
        for d in load_upstream_defects(_store(tmp_path / "inc"), years=(YEAR,), as_of=T2_INSTANT)
    ]
    assert kinds.count("withdrawn_after_publication") == len(WITHDRAWN)
    for name in (*COMPARED, *WITHDRAWN_ROWS):
        assert inc.hashes[name] is not None, name
        assert inc.hashes[name] == full.hashes[name], name
    for build in (inc, full):
        (year_report,) = json.loads(build.stdout)["builds"]
        assert year_report["withdrawals"]["count"] == len(WITHDRAWN)
        assert year_report["withdrawals"]["subjects"] == sorted(
            {WITHDRAWN_LIMIT, WITHDRAWN_PRICE, WITHDRAWN_HALT, PARTLY_WITHDRAWN}
        )


def test_the_withdrawal_record_carries_the_stored_rows_clocks_and_its_confirmation_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored_at_t1(tmp_path, monkeypatch, "inc")
    columns = ("subject", "event_time", "available_time", "ingested_time")
    (stored,) = [
        row[1:]
        for row in _store(tmp_path / "inc").query(PRICE_LIMIT_DATASET, year=YEAR, columns=columns)
        if row[0] == WITHDRAWN_LIMIT
    ]

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=WITHDRAWN_NOW)

    assert inc.exit_code == PanelExit.ok, inc.output
    (record,) = [
        row[1:-1]
        for row in _store(tmp_path / "inc").query(
            UPSTREAM_DEFECTS_DATASET,
            year=YEAR,
            columns=(*columns, "revision_time", "source_dataset"),
        )
        if row[0] == WITHDRAWN_LIMIT and row[-1] == PRICE_LIMIT_DATASET
    ]
    assert record[:3] == stored
    # Confirmed by this build, at its own stamp, and so not knowable before it.
    assert record[3] == T2_INSTANT
    assert WITHDRAWN_LIMIT not in {
        d.ts_code
        for d in load_upstream_defects(
            _store(tmp_path / "inc"), years=(YEAR,), as_of=datetime.fromisoformat(T1)
        )
    }


def test_the_confirming_refetch_is_one_request_per_session_and_is_budgeted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `V2-P6-013` defect here, whose own whole-session re-fetch would ask again too."""
    clean = Corpus(defects=False, once=ONCE, traded=TRADED)
    stored = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=clean)
    assert stored.exit_code == PanelExit.ok, stored.output

    inc = run_build(
        tmp_path,
        monkeypatch,
        as_of=T2,
        incremental=True,
        corpus=replace(clean, withdrawn=WITHDRAWN),
    )

    assert inc.exit_code == PanelExit.ok, inc.output
    for api_name in SESSION_APIS:
        # The overlap session twice (the slice's fetch, then the confirmation), every other once.
        assert inc.upstream.asked[(api_name, T1_LAST)] == 2, api_name
        assert inc.upstream.asked[(api_name, SESSIONS[6])] == 1, api_name
        assert f"BUDGET withdrawal-confirmation {api_name} 1 requests" in inc.output, api_name


def test_an_absence_the_second_fetch_contradicts_is_refused_naming_both_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first fetch lacks the row and the second serves it: that is what a partial fetch looks
    like, so nothing is written and the refusal says what each answer held."""
    _stored_at_t1(tmp_path, monkeypatch, "inc")
    before = _hashes(tmp_path / "inc")
    flaky = replace(WITHDRAWN_NOW, republished_on_refetch=True)

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=flaky)

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    # `adj_factor` is the first target to confirm, and the refusal stops the build there.
    assert WITHDRAWN_PRICE in inc.output
    assert ADJ_FACTOR_DATASET in inc.output
    assert "first fetch" in inc.output
    assert "second fetch" in inc.output
    assert _hashes(tmp_path / "inc") == before


def test_a_row_missing_from_a_carried_session_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The withdrawal rule releases only rows on the sessions this build fetched again. A
    security whose carried rows go missing -- here the carry is made to lose them -- still trips
    the subject guard, whatever the upstream withdrew on the overlap."""
    once = ((PRICE_LIMIT_DATASET, WITHDRAWN_LIMIT),)
    stored = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=Corpus(once=once))
    assert stored.exit_code == PanelExit.ok, stored.output
    lost = FILLERS[9]
    real = cli.carry_stored_sessions_forward

    def lossy(*args: Any, **kwargs: Any) -> list[Any]:
        carried = real(*args, **kwargs)
        if kwargs.get("dataset") != PRICE_LIMIT_DATASET or not carried:
            return carried
        first = carried[0]
        kept = [index for index, subject in enumerate(first.subjects) if subject != lost]
        return [panel_ingest._select_rows(first, kept), *carried[1:]]

    monkeypatch.setattr(cli, "carry_stored_sessions_forward", lossy)
    withdrawn = Corpus(
        once=once,
        withdrawn=(
            (PRICE_LIMIT_DATASET, WITHDRAWN_LIMIT, T1_LAST),
            *((PRICE_LIMIT_DATASET, lost, day) for day in _between(T1_LAST, T2_LAST)),
        ),
    )

    inc = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True, corpus=withdrawn)

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert "would drop" in inc.output
    assert lost in inc.output
    # The withdrawals it confirmed are not on the record: the write they belonged to was refused.
    assert _withdrawals(tmp_path) == set()
    assert _store(tmp_path).registered_years(WITHDRAWN_ROWS_DATASETS[PRICE_LIMIT_DATASET]) == ()
    assert "WITHDRAWN" not in inc.output


def test_a_withdrawal_is_carried_forward_and_retired_when_the_row_is_served_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored_at_t1(tmp_path, monkeypatch, "store")
    confirmed = run_build(
        tmp_path / "store", monkeypatch, as_of=T2, incremental=True, corpus=WITHDRAWN_NOW
    )
    assert confirmed.exit_code == PanelExit.ok, confirmed.output
    shutil.copytree(tmp_path / "store", tmp_path / "full")
    shutil.copytree(tmp_path / "store", tmp_path / "republished")
    t3 = datetime.fromisoformat(T3)
    expected = {(code, api_name, day) for api_name, code, day in WITHDRAWN}

    # Carried by every later build, full or incremental, keeping its confirmation instant.
    inc = run_build(
        tmp_path / "store", monkeypatch, as_of=T3, incremental=True, corpus=WITHDRAWN_NOW
    )
    full = run_build(
        tmp_path / "full", monkeypatch, as_of=T3, incremental=False, corpus=WITHDRAWN_NOW
    )
    for build in (inc, full):
        assert build.exit_code == PanelExit.ok, build.output
        assert json.loads(build.stdout)["builds"][0]["withdrawals"]["count"] == 0
    for name in (*COMPARED, *WITHDRAWN_ROWS):
        assert inc.hashes[name] is not None, name
        assert inc.hashes[name] == full.hashes[name], name
    for name in ("store", "full"):
        assert _withdrawals(tmp_path / name, t3) == expected
        confirmed_at = {
            row[1]
            for row in _store(tmp_path / name).query(
                UPSTREAM_DEFECTS_DATASET, year=YEAR, columns=("defect_kind", "revision_time")
            )
            if row[0] == "withdrawn_after_publication"
        }
        assert confirmed_at == {T2_INSTANT}

    # Served again: the record is retired and the row is stored again.
    again = run_build(
        tmp_path / "republished", monkeypatch, as_of=T3, incremental=False, corpus=PUBLISHED
    )
    assert again.exit_code == PanelExit.ok, again.output
    assert _withdrawals(tmp_path / "republished", t3) == set()
    for dataset in WITHDRAWN_ROWS:
        assert _store(tmp_path / "republished").registered_years(dataset) == (), dataset
    subjects = {
        row[0]
        for row in _store(tmp_path / "republished").query(
            PRICE_LIMIT_DATASET, year=YEAR, columns=("subject",)
        )
    }
    assert WITHDRAWN_LIMIT in subjects


def test_an_incremental_build_refuses_a_withdrawn_closing_factor_the_slice_cannot_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adj_factor` is stored compressed, so its row on the overlap session is a security's
    closing anchor. When the upstream withdraws it and serves nothing later for the security, the
    full rebuild closes the step function on the previous session -- a row the compressed carry
    never kept -- so the incremental build is refused with the full rebuild, which records it."""
    gone = FILLERS[10]
    _stored_at_t1(tmp_path, monkeypatch, "inc")
    stopped = replace(
        PUBLISHED,
        withdrawn=tuple((ADJ_FACTOR_DATASET, gone, day) for day in _between(T1_LAST, T2_LAST)),
    )

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=stopped)

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert gone in inc.output
    assert "closing observation" in inc.output
    assert "WITHDRAWN" not in inc.output
    assert _withdrawals(tmp_path / "inc") == set()
    rebuilt = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=False, corpus=stopped)
    assert rebuilt.exit_code == PanelExit.ok, rebuilt.output
    assert (gone, ADJ_FACTOR_DATASET, T1_LAST) in _withdrawals(tmp_path / "inc")


# --- review round 1 (`V2-P6-016`) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("served", "shape"),
    [
        pytest.param(0, "answered no_data", id="an-empty-answer-twice"),
        pytest.param(2, "served 2 row(s)", id="a-thin-answer-twice"),
    ],
)
def test_an_empty_or_thin_session_confirms_no_withdrawal_however_often_it_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: int, shape: str
) -> None:
    """An outage answers the second request as it answered the first. Twenty-five stored bands
    are not withdrawn because `stk_limit` served none (or two) of them twice: refused by name,
    before any confirming request, with nothing recorded."""
    clean = Corpus(defects=False)
    stored = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=clean)
    assert stored.exit_code == PanelExit.ok, stored.output
    before = _hashes(tmp_path, (*COMPARED, *WITHDRAWN_ROWS))

    inc = run_build(
        tmp_path,
        monkeypatch,
        as_of=T2,
        incremental=True,
        corpus=replace(clean, served_only=((PRICE_LIMIT_DATASET, T1_LAST, served),)),
    )

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert shape in inc.output
    assert "MIN_SESSION_ROW_SHARE" in inc.output
    assert inc.upstream.asked[(PRICE_LIMIT_DATASET, T1_LAST)] == 1
    assert "withdrawal-confirmation" not in inc.output
    # `stk_limit` is the last target, so only its partitions are sure to be untouched.
    untouched = (PRICE_LIMIT_DATASET, UPSTREAM_DEFECTS_DATASET, *WITHDRAWN_ROWS)
    assert _hashes(tmp_path, untouched) == {name: before[name] for name in untouched}
    assert _withdrawals(tmp_path) == set()


HALTED_WITHOUT_A_BAR = replace(PUBLISHED, traded=())
"""`WITHDRAWN_HALT` halted all of `T1_LAST`, with no bar."""


@pytest.mark.parametrize(
    "corpus",
    [
        pytest.param(
            replace(HALTED_WITHOUT_A_BAR, served_only=((SUSPENSION_DATASET, T1_LAST, 0),)),
            id="an-empty-halt-answer-twice",
        ),
        pytest.param(
            replace(HALTED_WITHOUT_A_BAR, withdrawn=WITHDRAWN), id="a-halt-withdrawn-with-no-bar"
        ),
    ],
)
def test_a_halt_is_withdrawn_only_against_the_securitys_own_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpus: Corpus
) -> None:
    """`suspend_d` publishes an empty session whenever nothing is halted, so an empty answer --
    twice -- says nothing about the halts stored there. A withdrawn halt needs the security's bar
    on that session; without it the year is refused, naming the security."""
    stored = run_build(
        tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=HALTED_WITHOUT_A_BAR
    )
    assert stored.exit_code == PanelExit.ok, stored.output
    halts = (SUSPENSION_DATASET, WITHDRAWN_ROWS_DATASETS[SUSPENSION_DATASET])
    before = _hashes(tmp_path, halts)

    inc = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True, corpus=corpus)

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert WITHDRAWN_HALT in inc.output
    assert "no daily bar" in inc.output
    assert _hashes(tmp_path, halts) == before
    assert not any(code == WITHDRAWN_HALT for code, _, _ in _withdrawals(tmp_path))


def test_a_record_a_refused_build_left_behind_is_not_duplicated_or_carried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state the first cut of this issue could leave: the record written, then the writer
    refused, so the row is still stored beside a record of its withdrawal. The next build that
    confirms the withdrawal keeps exactly one record, and the stale one is not carried."""
    once = ((PRICE_LIMIT_DATASET, WITHDRAWN_LIMIT),)
    clean = Corpus(defects=False, once=once)
    stored = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=clean)
    assert stored.exit_code == PanelExit.ok, stored.output
    gone = replace(clean, withdrawn=((PRICE_LIMIT_DATASET, WITHDRAWN_LIMIT, T1_LAST),))
    real = cli.write_price_limits

    def refused_after_its_record(*args: Any, **kwargs: Any) -> Any:
        kwargs["before_write"]()
        raise PanelBatchError("refused after the record was written")

    monkeypatch.setattr(cli, "write_price_limits", refused_after_its_record)
    broken = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True, corpus=gone)
    assert broken.exit_code == PanelExit.unhealthy, broken.output
    assert _withdrawals(tmp_path) == {(WITHDRAWN_LIMIT, PRICE_LIMIT_DATASET, T1_LAST)}
    limits = _store(tmp_path).query(PRICE_LIMIT_DATASET, year=YEAR, columns=("subject",))
    assert (WITHDRAWN_LIMIT,) in limits  # the row the record calls withdrawn is still stored
    monkeypatch.setattr(cli, "write_price_limits", real)

    again = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True, corpus=gone)

    assert again.exit_code == PanelExit.ok, again.output
    records = [
        row
        for row in _store(tmp_path).query(
            UPSTREAM_DEFECTS_DATASET, year=YEAR, columns=("subject", "defect_kind")
        )
        if row == (WITHDRAWN_LIMIT, "withdrawn_after_publication")
    ]
    assert len(records) == 1
    kept = _store(tmp_path).query(
        WITHDRAWN_ROWS_DATASETS[PRICE_LIMIT_DATASET], year=YEAR, columns=("subject",)
    )
    assert kept == [(WITHDRAWN_LIMIT,)]


def test_a_republication_on_a_carried_session_is_seen_by_an_incremental_build_as_by_a_full_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The withdrawals sit on `T1_LAST`, before `T3`'s slice. The incremental build asks those
    `(dataset, session)` pairs again -- one request each, budgeted -- so it retires the records
    and stores the rows again exactly as the full build from the same store does."""
    _stored_at_t1(tmp_path, monkeypatch, "store")
    confirmed = run_build(
        tmp_path / "store", monkeypatch, as_of=T2, incremental=True, corpus=WITHDRAWN_NOW
    )
    assert confirmed.exit_code == PanelExit.ok, confirmed.output
    shutil.copytree(tmp_path / "store", tmp_path / "full")
    t3 = datetime.fromisoformat(T3)

    inc = run_build(tmp_path / "store", monkeypatch, as_of=T3, incremental=True, corpus=PUBLISHED)
    full = run_build(tmp_path / "full", monkeypatch, as_of=T3, incremental=False, corpus=PUBLISHED)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert full.exit_code == PanelExit.ok, full.output
    for name in (*COMPARED, *WITHDRAWN_ROWS):
        assert inc.hashes[name] == full.hashes[name], name
    assert _withdrawals(tmp_path / "store", t3) == set()
    for api_name in SESSION_APIS:
        # Asked again once, and then exactly as the full build asks it: `daily` and `daily_basic`
        # are asked a second time by the `V2-P6-013` re-fetch of that session's disputes.
        assert f"BUDGET withdrawal-recheck {api_name} 1 requests" in inc.output, api_name
        assert inc.upstream.asked[(api_name, T1_LAST)] == full.upstream.asked[(api_name, T1_LAST)]
    assert inc.upstream.asked[(PRICE_LIMIT_DATASET, T1_LAST)] == 1
    subjects = {
        row[0]
        for row in _store(tmp_path / "store").query(
            PRICE_LIMIT_DATASET, year=YEAR, columns=("subject",)
        )
    }
    assert {WITHDRAWN_LIMIT, PARTLY_WITHDRAWN} <= subjects


def test_more_carried_withdrawals_than_the_daily_budget_allows_are_refused_with_the_full_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored_at_t1(tmp_path, monkeypatch, "store")
    confirmed = run_build(
        tmp_path / "store", monkeypatch, as_of=T2, incremental=True, corpus=WITHDRAWN_NOW
    )
    assert confirmed.exit_code == PanelExit.ok, confirmed.output
    monkeypatch.setattr(cli, "WITHDRAWAL_RECHECK_LIMIT", 4)

    inc = run_build(
        tmp_path / "store", monkeypatch, as_of=T3, incremental=True, corpus=WITHDRAWN_NOW
    )

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert "WITHDRAWAL_RECHECK_LIMIT" in inc.output
    assert "openalpha panel build" in inc.output
    assert inc.upstream.session_requests() == []


def test_each_withdrawn_row_is_kept_whole_with_its_clocks_and_confirmation_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored_at_t1(tmp_path, monkeypatch, "inc")
    store = _store(tmp_path / "inc")
    clocks = ("event_time", "available_time", "ingested_time", "revision_time")
    shapes = {
        PRICE_LIMIT_DATASET: (WITHDRAWN_LIMIT, ("trade_date", "up_limit", "down_limit")),
        ADJ_FACTOR_DATASET: (WITHDRAWN_PRICE, ("factor_date", "adj_factor")),
        SUSPENSION_DATASET: (WITHDRAWN_HALT, ("trade_date", "suspend_type", "suspend_timing")),
        DAILY_DATASET: (WITHDRAWN_PRICE, ("trade_date", "open", "close", "pre_close", "amount")),
        DAILY_BASIC_DATASET: (WITHDRAWN_PRICE, ("trade_date", "close", "total_mv")),
    }
    before = {
        source: [
            row[1:]
            for row in store.query(source, year=YEAR, columns=("subject", *columns, *clocks))
            if row[0] == code
        ]
        for source, (code, columns) in shapes.items()
    }

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=WITHDRAWN_NOW)

    assert inc.exit_code == PanelExit.ok, inc.output
    extra = (WITHDRAWN_CONFIRMED_AT_COLUMN, WITHDRAWN_ORIGINAL_INGESTED_TIME_COLUMN)

    def kept(source: str, code: str, columns: tuple[str, ...]) -> list[tuple[object, ...]]:
        return [
            row[1:]
            for row in store.query(
                WITHDRAWN_ROWS_DATASETS[source],
                year=YEAR,
                columns=("subject", *columns, *clocks, *extra),
            )
            if row[0] == code
        ]

    ingested = clocks.index("ingested_time") - len(clocks) - len(extra)
    for source, (code, columns) in shapes.items():
        rows = kept(source, code, columns)
        assert [row[: -len(extra)] for row in rows] == before[source], source
        assert [row[-2] for row in rows] == [T2_INSTANT], source
        assert [row[-1] for row in rows] == [row[ingested] for row in rows], source

    # Carried by a later build: the ingested_time clock is restamped (`V2-P6-003`), and the
    # original is kept verbatim beside it.
    later = run_build(
        tmp_path / "inc", monkeypatch, as_of=T3, incremental=True, corpus=WITHDRAWN_NOW
    )
    assert later.exit_code == PanelExit.ok, later.output
    for source, (code, columns) in shapes.items():
        (first,) = before[source]
        (row,) = kept(source, code, columns)
        assert row[-1] == first[ingested + len(extra)], source
        assert row[-2] == T2_INSTANT, source


def test_a_build_with_no_withdrawal_stores_and_asks_exactly_what_it_did_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pinned against the build before `V2-P6-016` (`d5176c5`), run on this corpus: the same
    partition bytes and the same request count, at `T1` and incrementally at `T2`."""
    first = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False)
    assert first.exit_code == PanelExit.ok, first.output
    assert len(first.upstream.payloads) == 36
    assert {name: first.hashes[name] for name in COMPARED} == BEFORE_V2_P6_016_AT_T1
    inc = run_build(tmp_path, monkeypatch, as_of=T2, incremental=True)
    assert inc.exit_code == PanelExit.ok, inc.output
    assert len(inc.upstream.payloads) == 36
    assert {name: inc.hashes[name] for name in COMPARED} == BEFORE_V2_P6_016_AT_T2
    assert all(inc.hashes[name] is None for name in WITHDRAWN_ROWS)
    assert "withdrawal-" not in inc.output


BEFORE_V2_P6_016_AT_T1: dict[str, str | None] = {
    DAILY_DATASET: "f6c5c73c2512c5a2bb0ab38b06ff1178da0b993a272e10f843bc180cc7c14543",
    DAILY_BASIC_DATASET: "b5a2be96311ed5c2f31354eb69391f7c9968acb261599f7b8297823cb0b3faa9",
    SUSPENSION_DATASET: "8d5fa9199cc14fb0ecddbf9def87a7d5856383f2b1d04d0e07bd6fefbaf0c285",
    ADJ_FACTOR_DATASET: "2a6465b7be21478e733f61e1a2d91aebcd6f0914a198d31ce1f848139b38183c",
    PRICE_LIMIT_DATASET: "65542ca3da0f6226dfc0ea355cb7a43819fecd17697e54776279ccbacee77b62",
    UPSTREAM_DEFECTS_DATASET: "30240f6ba29b758756ec1d2bf67a12c2784ee2dd0a09e14ea7c7027c2345bdfc",
}
"""Measured by running this module's `run_build` on the base commit `d5176c5`."""
BEFORE_V2_P6_016_AT_T2: dict[str, str | None] = {
    DAILY_DATASET: "feae26489df535ee1d5c2fd8bf64166b0761ee4e0e00a10169261b67d5c343be",
    DAILY_BASIC_DATASET: "094f7ea0b479e48e8b8276c6abc5cb8a0fd8e80e46dd8e402f9a34e8c1852fd2",
    SUSPENSION_DATASET: "4b956b47a2d6c8fa2250f99b5d51e3ba916ac0c1f1e05dddc8955acfd18f81e0",
    ADJ_FACTOR_DATASET: "0117628270cf9e6d49209028e45c53fe002a56a3579f39a0fff25145912e0bc8",
    PRICE_LIMIT_DATASET: "40d0618aafcebcbf633e98bed5fdb7e07b3174c1028643d1acf43041dbc19ffd",
    UPSTREAM_DEFECTS_DATASET: "baae6017b11eae62b693fc490113fa55acfc83a866b519f8e8112837bb89cc10",
}


def test_panel_doctor_answers_for_the_withdrawn_rows_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored_at_t1(tmp_path, monkeypatch, "inc")
    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=WITHDRAWN_NOW)
    assert inc.exit_code == PanelExit.ok, inc.output

    report = panel_health_report(
        _store(tmp_path / "inc"), as_of=T2_INSTANT, datasets=WITHDRAWN_ROWS, years=(YEAR, YEAR - 1)
    )

    for dataset in WITHDRAWN_ROWS:
        health = report.dataset(dataset)
        assert health.freshness.cadence == "derived", dataset
        assert health.is_ready, dataset
    # A year with no partition is a year with no withdrawal, not a missing one.
    assert "partition_missing" not in report.codes()


# --- review round 2 (`V2-P6-016`) --------------------------------------------------------------


def test_an_empty_halt_answer_twice_does_not_withdraw_a_resumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RESUMING`'s `R` is the only `suspend_d` row of `SESSIONS[0]`. A resumption has a bar
    whether or not its row is still published, so a bar contradicts nothing and two empty
    answers withdraw nothing: the full build is refused by name and writes nothing of it.
    (`HALTED`'s whole-day halt on `SESSIONS[2]` keeps the year's halt corpus non-empty.)"""
    clean = Corpus()
    stored = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=clean)
    assert stored.exit_code == PanelExit.ok, stored.output
    halts = (SUSPENSION_DATASET, WITHDRAWN_ROWS_DATASETS[SUSPENSION_DATASET])
    before = _hashes(tmp_path, halts)

    full = run_build(
        tmp_path,
        monkeypatch,
        as_of=T2,
        incremental=False,
        corpus=replace(clean, served_only=((SUSPENSION_DATASET, SESSIONS[0], 0),)),
    )

    assert full.exit_code == PanelExit.unhealthy, full.output
    assert RESUMING in full.output
    assert "whole-day" in full.output
    assert "keep a copy of this suspend_d partition" in full.output
    assert _hashes(tmp_path, halts) == before
    assert _withdrawals(tmp_path) == set()
    assert "WITHDRAWN" not in full.output


def test_a_withdrawn_factor_step_before_the_slice_refuses_the_incremental_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`T1_LAST` holds a carried withdrawal, so `T3`'s incremental build asks its `adj_factor`
    again -- and finds `STEPPED`'s change row there withdrawn too. The full rebuild places the
    step on the next session that has one, a row the compressed partition never kept, so the
    incremental build is refused with the full rebuild, which records the withdrawal."""
    stepped = FILLERS[12]
    published = replace(PUBLISHED, stepped=(stepped,))
    base = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=published)
    assert base.exit_code == PanelExit.ok, base.output
    confirmed = run_build(
        tmp_path,
        monkeypatch,
        as_of=T2,
        incremental=True,
        corpus=replace(published, withdrawn=WITHDRAWN),
    )
    assert confirmed.exit_code == PanelExit.ok, confirmed.output
    shutil.copytree(tmp_path, tmp_path.parent / "full")
    step_withdrawn = replace(
        published, withdrawn=(*WITHDRAWN, (ADJ_FACTOR_DATASET, stepped, T1_LAST))
    )

    inc = run_build(tmp_path, monkeypatch, as_of=T3, incremental=True, corpus=step_withdrawn)

    assert inc.exit_code == PanelExit.unhealthy, inc.output
    assert stepped in inc.output
    assert "openalpha panel build" in inc.output
    full = run_build(
        tmp_path.parent / "full", monkeypatch, as_of=T3, incremental=False, corpus=step_withdrawn
    )
    assert full.exit_code == PanelExit.ok, full.output
    assert (stepped, ADJ_FACTOR_DATASET, T1_LAST) in _withdrawals(
        tmp_path.parent / "full", datetime.fromisoformat(T3)
    )


# --- rebased onto `V2-P6-017` (placeholders) and the approving review's minors ----------------


def test_a_withdrawn_factor_step_whose_next_session_is_held_equals_the_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact form of the pre-slice rule. `RESTEPPED` steps on `T1_LAST` and again on the
    next session, so the compressed partition holds that next session's row; with the step on
    `T1_LAST` withdrawn, both builds decide from the same rows and store the same bytes."""
    restepped = FILLERS[13]
    published = replace(PUBLISHED, restepped=(restepped,))
    base = run_build(tmp_path / "inc", monkeypatch, as_of=T1, incremental=False, corpus=published)
    assert base.exit_code == PanelExit.ok, base.output
    confirmed = run_build(
        tmp_path / "inc",
        monkeypatch,
        as_of=T2,
        incremental=True,
        corpus=replace(published, withdrawn=WITHDRAWN),
    )
    assert confirmed.exit_code == PanelExit.ok, confirmed.output
    shutil.copytree(tmp_path / "inc", tmp_path / "full")
    step_withdrawn = replace(
        published, withdrawn=(*WITHDRAWN, (ADJ_FACTOR_DATASET, restepped, T1_LAST))
    )

    inc = run_build(
        tmp_path / "inc", monkeypatch, as_of=T3, incremental=True, corpus=step_withdrawn
    )
    full = run_build(
        tmp_path / "full", monkeypatch, as_of=T3, incremental=False, corpus=step_withdrawn
    )

    assert inc.exit_code == PanelExit.ok, inc.output
    assert full.exit_code == PanelExit.ok, full.output
    for name in (*COMPARED, *WITHDRAWN_ROWS):
        assert inc.hashes[name] == full.hashes[name], name
    assert (restepped, ADJ_FACTOR_DATASET, T1_LAST) in _withdrawals(
        tmp_path / "inc", datetime.fromisoformat(T3)
    )


def test_a_placeholder_and_a_withdrawal_in_one_year_equal_the_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P6-017`'s placeholders and this issue's withdrawals together: `PLACEHOLDER_OVERLAP`
    sits on `T1_LAST`, the session that also loses five stored rows. Incremental equals full at
    `T2`, and again at `T3` when those rows are re-published on what is then a carried session
    -- asked again, so the placeholder there is re-derived rather than carried twice."""
    published = replace(PUBLISHED, placeholders=True)
    withdrawn = replace(published, withdrawn=WITHDRAWN)
    base = run_build(tmp_path / "base", monkeypatch, as_of=T1, incremental=False, corpus=published)
    assert base.exit_code == PanelExit.ok, base.output
    for name in ("inc", "full"):
        shutil.copytree(tmp_path / "base", tmp_path / name)

    inc = run_build(tmp_path / "inc", monkeypatch, as_of=T2, incremental=True, corpus=withdrawn)
    full = run_build(tmp_path / "full", monkeypatch, as_of=T2, incremental=False, corpus=withdrawn)

    assert inc.exit_code == PanelExit.ok, inc.output
    assert full.exit_code == PanelExit.ok, full.output
    for name in (*COMPARED, *WITHDRAWN_ROWS):
        assert inc.hashes[name] is not None, name
        assert inc.hashes[name] == full.hashes[name], name
    kinds = {kind for _, _, _, kind in _defects(tmp_path / "inc")}
    assert {
        "valuation_placeholder_on_halt",
        "valuation_placeholder_without_bar",
        "withdrawn_after_publication",
    } <= kinds
    assert _defects(tmp_path / "inc") == _defects(tmp_path / "full")

    shutil.rmtree(tmp_path / "full")
    shutil.copytree(tmp_path / "inc", tmp_path / "full")
    t3 = datetime.fromisoformat(T3)
    again = run_build(tmp_path / "inc", monkeypatch, as_of=T3, incremental=True, corpus=published)
    whole = run_build(tmp_path / "full", monkeypatch, as_of=T3, incremental=False, corpus=published)
    assert again.exit_code == PanelExit.ok, again.output
    assert whole.exit_code == PanelExit.ok, whole.output
    for name in (*COMPARED, *WITHDRAWN_ROWS):
        assert again.hashes[name] == whole.hashes[name], name
    assert _withdrawals(tmp_path / "inc", t3) == set()
    placeholders = [
        entry
        for entry in load_upstream_defects(_store(tmp_path / "inc"), years=(YEAR,), as_of=t3)
        if entry.kind.startswith("valuation_placeholder")
    ]
    assert sorted((entry.ts_code, entry.trade_date) for entry in placeholders) == sorted(
        PLACEHOLDERS
    )


def test_a_placeholder_is_not_recorded_by_a_price_write_that_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P6-017`'s records go through `write_daily_panel`'s `before_write` like every other:
    a price write refused by its own guards leaves no placeholder on the record."""
    clean = Corpus(defects=False)
    stored = run_build(tmp_path, monkeypatch, as_of=T1, incremental=False, corpus=clean)
    assert stored.exit_code == PanelExit.ok, stored.output

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise PanelBatchError("write_daily_panel refused by its own guards")

    monkeypatch.setattr(cli, "write_daily_panel", refused)
    build = run_build(
        tmp_path,
        monkeypatch,
        as_of=T2,
        incremental=False,
        corpus=replace(clean, placeholders=True),
    )

    assert build.exit_code == PanelExit.unhealthy, build.output
    assert _store(tmp_path).registered_years(UPSTREAM_DEFECTS_DATASET) == ()
