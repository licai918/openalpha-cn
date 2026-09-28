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
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET, INDEX_WEIGHT_INDEX_CODES
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
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


class ScriptedUpstream:
    """A `TushareTransport` answering from a `Corpus`, recording every payload it was sent."""

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.payloads: list[dict[str, Any]] = []

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

    def _traded(self, code: str, day: date) -> bool:
        if self.corpus.defects and code == HALTED and day == SESSIONS[2]:
            return False
        return not (self.corpus.halted_across and code == HALTED_ACROSS and day in SESSIONS[4:6])

    def _close(self, code: str, day: date) -> float:
        base = 10.0 + SECURITIES.index(code) / 10
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
        for code in SECURITIES:
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
        return rows

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
            for code in SECURITIES
            if self._traded(code, day)
        ]
        if self.corpus.defects and day >= PRELISTED_LISTING:
            rows.append([PRELISTED, _compact(day), 20.0, *([1.0] * len(VALUATION_EXTRA))])
        return rows

    def _halts(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if day == self._open()[0]:
            rows.append([RESUMING, _compact(day), "R", None])
        if self.corpus.defects and day == SESSIONS[2]:
            rows.append([HALTED, _compact(day), "S", None])
        if self.corpus.halted_across and day in SESSIONS[4:6]:
            rows.append([HALTED_ACROSS, _compact(day), "S", None])
        return rows

    def _limits(self, day: date) -> list[list[Any]]:
        rows: list[list[Any]] = []
        if self.corpus.defects and day in SESSIONS[:2]:
            rows.append([PRELISTED, _compact(day), 19.8, 16.2])
        if self.corpus.defects and day >= PRELISTED_LISTING:
            rows.append([PRELISTED, _compact(day), 22.0, 18.0])
        for code in SECURITIES:
            close = self._pre_close(code, day)
            band = [round(close * 1.1, 2), round(close * 0.9, 2)]
            if self.corpus.defects and code == HALTED and day == SESSIONS[2]:
                band = [0.0, 0.0]
            rows.append([code, _compact(day), *band])
        return rows

    def _factors(self, day: date) -> list[list[Any]]:
        def factor(code: str) -> float:
            if code != ADJUSTED:
                return 1.0
            return 1.2 if day >= SESSIONS[8] else 1.1 if day >= SESSIONS[3] else 1.0

        rows = [[code, _compact(day), factor(code)] for code in SECURITIES]
        if self.corpus.defects and (day in SESSIONS[:2] or day >= PRELISTED_LISTING):
            rows.append([PRELISTED, _compact(day), 1.0])
        return rows

    def _registry(self) -> list[list[Any]]:
        rows = [[code, code, "SSE", "主板", "L", "20100104", None] for code in SECURITIES]
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
        hashes=_hashes(runtime_dir),
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
