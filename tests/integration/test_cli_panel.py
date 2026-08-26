"""`panel build` / `panel doctor` / `data-check` driven end to end through `CliRunner`.

`V2-P1-015`. Every test here invokes the real Typer app -- no test calls a command's body as a
plain function. The roadmap's acceptance for this issue is "integration: CLI", and the lesson
the twelve prior Criticals share is that a command's *claim* and its *behaviour* diverge exactly
where nobody drove the command itself: an exit code is not observable from inside the function
that raises it, and a `typer.Exit` swallowed by a bare `except Exception` is invisible to a unit
test of the same body.

Nothing here touches the network. `panel build` goes through the real `TushareProvider` with a
scripted `TushareTransport` injected at `cli._panel_transport`, so the provider's own decoding,
point-in-time filtering and projection all run -- the seam is the HTTP call and nothing above
it. The panels the two read-side commands examine come from `tests/panel_fixtures.py`, whose
`write_generated_panel` drives the real `panel_ingest` writers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from panel_fixtures import AS_OF, EXCHANGE, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import CLICK_USAGE_EXIT_CODE, PanelExit, app
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore

runner = CliRunner()

SECRET_TOKEN = "sk-panel-build-token-must-not-leak-40311"
"""Deliberately distinct from `tests/unit/test_cli.py`'s, so a leak assertion here cannot pass
because some other test happened to scrub that one."""

# --- the built panel's frame ------------------------------------------------------------------
#
# Three sessions and two securities, and both counts are the smallest that let every write-time
# guard in `write_daily_panel` actually run: the session census needs the calendar to report
# more than one open day inside the window, and `_refuse_close_disagreement` needs two rows to
# disagree about.

BUILD_YEAR: int = 2026
BUILD_SESSIONS: tuple[date, ...] = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))
BUILD_CLOCK: datetime = datetime(2026, 1, 8, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on Thursday 2026-01-08.

`_session_census` bounds what a partition must hold at `fetched_at`'s local date minus one day,
so this clock makes 2026-01-07 the last session `panel build` has to fetch and the last one the
writers require. Frozen rather than real: with `datetime.now()` the set of sessions this test
demands would change every day it is run.
"""
BUILD_SECURITIES: tuple[str, ...] = ("000001.SZ", "600000.SH")
BUILD_EXCHANGE: str = "SSE"
BUILD_CLOSES: Mapping[str, float] = {"000001.SZ": 10.0, "600000.SH": 20.0}
HALT_SESSION: date = date(2026, 1, 6)
HALT_SECURITY: str = "000001.SZ"

CALENDAR_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
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
VALUATION_EXTRA_FIELDS = [
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
VALUATION_FIELDS = ["ts_code", "trade_date", "close", *VALUATION_EXTRA_FIELDS]
HALT_FIELDS = ["ts_code", "trade_date", "suspend_type", "suspend_timing"]
LIMIT_FIELDS = ["ts_code", "trade_date", "up_limit", "down_limit"]


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _response(
    fields: Sequence[str], items: Sequence[Sequence[Any]], *, has_more: bool = False
) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(fields),
            "items": [list(item) for item in items],
            "has_more": has_more,
        },
    }


def _calendar_items(exchange: str) -> list[list[Any]]:
    items: list[list[Any]] = []
    previous: str | None = None
    day = date(BUILD_YEAR, 1, 1)
    while day <= date(BUILD_YEAR, 12, 31):
        is_open = day in BUILD_SESSIONS
        items.append([exchange, _compact(day), 1 if is_open else 0, previous])
        if is_open:
            previous = _compact(day)
        day += timedelta(days=1)
    return items


def _session_of(params: Mapping[str, str]) -> date:
    return datetime.strptime(params["trade_date"], "%Y%m%d").date()


class ScriptedTushareTransport:
    """A `TushareTransport` that answers from this module's frame and records every payload.

    Records the payloads so the credential test can assert on what the CLI *sent* as well as on
    what it printed: a token that never reaches the transport and a token that is never echoed
    are two different claims, and only the second one is visible in `result.output`.
    """

    def __init__(self, *, closes: Mapping[str, Mapping[date, float]] | None = None) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._closes = dict(closes or {})

    def close_of(self, dataset: str, code: str, day: date) -> float:
        override = self._closes.get(dataset, {})
        if isinstance(override, Mapping) and (code, day) in override:  # type: ignore[comparison-overlap]
            return float(override[(code, day)])  # type: ignore[index]
        return BUILD_CLOSES[code]

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api_name = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api_name == TRADING_CALENDAR_DATASET:
            return _response(CALENDAR_FIELDS, _calendar_items(params["exchange"]))
        if api_name == STOCK_BASIC_DATASET:
            return _response(
                REGISTRY_FIELDS,
                [
                    [code, code, BUILD_EXCHANGE, "主板", "L", "20260102", None]
                    for code in BUILD_SECURITIES
                ],
            )
        day = _session_of(params)
        if api_name == ADJ_FACTOR_DATASET:
            return _response(
                FACTOR_FIELDS, [[code, _compact(day), 1.0] for code in BUILD_SECURITIES]
            )
        if api_name == DAILY_DATASET:
            return _response(
                BAR_FIELDS,
                [
                    [
                        code,
                        _compact(day),
                        *([self.close_of(DAILY_DATASET, code, day)] * 5),
                        0.0,
                        1000.0,
                        10000.0,
                    ]
                    for code in BUILD_SECURITIES
                ],
            )
        if api_name == DAILY_BASIC_DATASET:
            return _response(
                VALUATION_FIELDS,
                [
                    [
                        code,
                        _compact(day),
                        self.close_of(DAILY_BASIC_DATASET, code, day),
                        *([1.0] * len(VALUATION_EXTRA_FIELDS)),
                    ]
                    for code in BUILD_SECURITIES
                ],
            )
        if api_name == SUSPENSION_DATASET:
            if day != HALT_SESSION:
                return _response(HALT_FIELDS, [])
            return _response(HALT_FIELDS, [[HALT_SECURITY, _compact(day), "R", None]])
        if api_name == PRICE_LIMIT_DATASET:
            return _response(
                LIMIT_FIELDS,
                [
                    [
                        code,
                        _compact(day),
                        BUILD_CLOSES[code] * 1.1,
                        BUILD_CLOSES[code] * 0.9,
                    ]
                    for code in BUILD_SECURITIES
                ],
            )
        raise AssertionError(f"the CLI asked for an unscripted dataset: {api_name}")


class YearAwareCalendarTransport(ScriptedTushareTransport):
    """A `trade_cal` that answers for whatever year the *request* named.

    `ScriptedTushareTransport` answers `BUILD_YEAR` whatever it is asked, which is what most of
    this module wants and is exactly what makes "did `--year` reach the fetch, or did the clock?"
    unobservable: both produce a 2026 partition. This one honours `start_date`, so the two
    answers differ.
    """

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload["api_name"]) != TRADING_CALENDAR_DATASET:
            return super().post(payload)
        self.payloads.append(payload)
        params: Mapping[str, str] = payload["params"]
        year = int(str(params["start_date"])[:4])
        items: list[list[Any]] = []
        previous: str | None = None
        day = date(year, 1, 1)
        while day <= date(year, 12, 31):
            is_open = day.weekday() < 5
            items.append([params["exchange"], _compact(day), 1 if is_open else 0, previous])
            if is_open:
                previous = _compact(day)
            day += timedelta(days=1)
        return _response(CALENDAR_FIELDS, items)


class StaleHaltTransport(ScriptedTushareTransport):
    """A `suspend_d` whose rows are dated in the year *before* the one being built.

    Not a contrived shape: the endpoint is asked for one session and answers with whatever rows
    it has, and a partition's year comes from the rows' own dates rather than from `--year`.
    """

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload["api_name"]) != SUSPENSION_DATASET:
            return super().post(payload)
        self.payloads.append(payload)
        if _session_of(payload["params"]) != HALT_SESSION:
            return _response(HALT_FIELDS, [])
        stale = date(BUILD_YEAR - 1, 12, 31)
        return _response(HALT_FIELDS, [[HALT_SECURITY, _compact(stale), "R", None]])


class EmptyLimitSessionTransport(ScriptedTushareTransport):
    """A `stk_limit` that serves no rows for one session the calendar reports open."""

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload["api_name"]) != PRICE_LIMIT_DATASET:
            return super().post(payload)
        if _session_of(payload["params"]) == BUILD_SESSIONS[1]:
            self.payloads.append(payload)
            return _response(LIMIT_FIELDS, [])
        return super().post(payload)


class RefusingTushareTransport:
    """A transport whose response carries a token-bearing error message.

    Tushare reports a bad credential as `code == -2001` and puts the reason in `msg`, which
    `TushareProvider` carries verbatim onto the `ProviderFailure` it raises. This double
    therefore reproduces the one path where a credential can reach the CLI inside an exception
    rather than inside a config value -- exactly what `_probe_report`'s existing comment says
    must never be echoed.
    """

    def __init__(self, token_bearing_message: str) -> None:
        self.message = token_bearing_message

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"code": -2001, "msg": self.message, "data": None}


@pytest.fixture
def scripted_build(monkeypatch: pytest.MonkeyPatch) -> ScriptedTushareTransport:
    """Freeze `panel build`'s clock and point its transport at this module's frame."""
    transport = ScriptedTushareTransport()
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)
    return transport


def build(runtime_dir: Path, *targets: str, extra: Sequence[str] = ()) -> Any:
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir), "--year", str(BUILD_YEAR)]
    for target in targets:
        arguments.extend(["--dataset", target])
    arguments.extend(extra)
    return runner.invoke(app, arguments)


EVERY_BUILD_TARGET: tuple[str, ...] = (
    "trade_cal",
    "stock_basic",
    "adj_factor",
    "price",
    "stk_limit",
)
"""The five targets this module's scripted transport answers for.

Not `cli.PANEL_BUILD_TARGETS` any more: the P3 prerequisite added eight, whose fetch shapes are
one request per security, per index-month and per industry slice, and driving those through this
module's two-security frame would make every test here a fixture-maintenance exercise. They are
exercised in `tests/integration/test_cli_panel_extra_targets.py`, which carries a transport
shaped for them.

`tests/e2e/` still builds these five and only these five. Extending that suite is deliberately
not part of this change -- its build is already half an hour and the eight new targets add a
whole-market statement sweep measured at 2h55m per dataset-year -- so what stands behind the new
targets against the real endpoint is the manual live run recorded in this issue's ledger notes,
not an automated one.
"""

UNBUILT_TARGET: str = "moneyflow"
"""A Tushare dataset name this repository has no descriptor, no writer and no branch for.

The mutation two tests below inject, and the name every "unknown target" assertion here drives.
Both used to be real dataset names -- the mutation was `namechange` under a comment reading
"which is what a future issue wiring `namechange` will do first", and the refusal cases were
`income` because it was a dataset this repository could write and this command could not build.
That issue has now landed, so both names are real targets with real branches and both tests
would have quietly stopped testing what they say. A name the whole repository does not know
cannot acquire an implementation by accident.
"""


# --- panel build ------------------------------------------------------------------------------


def test_panel_build_writes_every_target_through_the_real_writers(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """The load-bearing one: if a complete, well-formed fetch could not be stored, every
    refusal test below would pass for the wrong reason."""
    result = build(tmp_path, *EVERY_BUILD_TARGET, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    payload = json.loads(result.stdout)
    written = {entry["dataset"]: entry["row_count"] for entry in payload["partitions"]}
    assert written == {
        TRADING_CALENDAR_DATASET: 365,
        STOCK_BASIC_DATASET: len(BUILD_SECURITIES),
        # Compressed, not dropped: `write_adjustment_factors` stores a step function, so a
        # flat series over three sessions keeps the two rows that bound it per security.
        ADJ_FACTOR_DATASET: 2 * len(BUILD_SECURITIES),
        SUSPENSION_DATASET: 1,
        DAILY_DATASET: len(BUILD_SESSIONS) * len(BUILD_SECURITIES),
        DAILY_BASIC_DATASET: len(BUILD_SESSIONS) * len(BUILD_SECURITIES),
        PRICE_LIMIT_DATASET: len(BUILD_SESSIONS) * len(BUILD_SECURITIES),
    }
    assert payload["sessions"] == {
        "first": "2026-01-05",
        "last": "2026-01-07",
        "count": len(BUILD_SESSIONS),
    }
    assert payload["halts"] == "corroborated"


def test_panel_build_fetches_only_the_sessions_the_write_time_census_will_require(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """The loop bound is not a guess. `_session_census` requires every session the calendar
    reports open between 1 January and `fetched_at`'s local date minus one, so a build that
    fetched fewer would be refused by its own writer and one that fetched more would be asking
    for sessions that had not published."""
    assert build(tmp_path, *EVERY_BUILD_TARGET).exit_code == PanelExit.ok

    requested = [
        _session_of(entry["params"])
        for entry in scripted_build.payloads
        if entry["api_name"] == DAILY_DATASET
    ]
    assert requested == list(BUILD_SESSIONS)


def test_panel_build_runs_the_targets_in_dependency_order_and_not_in_flag_order(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """`PANEL_BUILD_TARGETS` is iterated in its own declared order, not in the order the flags
    arrived: `write_adjustment_factors` and `write_daily_panel` both read the calendar out of
    the store, so a fresh store needs `trade_cal` written first whatever the caller typed."""
    result = build(tmp_path, *reversed(EVERY_BUILD_TARGET), extra=["--json"])

    assert result.exit_code == PanelExit.ok
    assert [entry["dataset"] for entry in json.loads(result.stdout)["partitions"]] == [
        TRADING_CALENDAR_DATASET,
        STOCK_BASIC_DATASET,
        ADJ_FACTOR_DATASET,
        SUSPENSION_DATASET,
        DAILY_DATASET,
        DAILY_BASIC_DATASET,
        PRICE_LIMIT_DATASET,
    ]
    assert scripted_build.payloads[0]["api_name"] == TRADING_CALENDAR_DATASET


def test_panel_build_refuses_a_bare_daily_because_the_price_writer_couples_the_pair(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """`write_daily_panel` takes both datasets and there is no supported way to write one
    without the other, so `--dataset daily` cannot mean anything. It is refused by name, with
    the coupling as the reason, rather than being accepted and quietly building the pair."""
    result = build(tmp_path, "daily")

    assert result.exit_code == PanelExit.bad_request
    assert "price" in result.output
    assert "daily_basic" in result.output
    assert not (tmp_path / "panel").exists()


def test_panel_build_refuses_an_unknown_target_by_naming_the_closed_table(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """The refusal names the whole table, so a caller learns what *is* buildable.

    The name driven here used to be `income`, which was chosen because it was a dataset this
    repository could write and this command could not build -- the hole three acceptance passes
    reported and the P3 prerequisite closed. It is a real target now, so the unknown name has to
    be one nothing in the repository knows.
    """
    result = build(tmp_path, UNBUILT_TARGET)

    assert result.exit_code == PanelExit.bad_request
    for target in EVERY_BUILD_TARGET:
        assert target in result.output


def test_a_write_time_guard_refusal_is_reported_and_leaves_no_partition_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`daily_basic` republishes `close`, and `_refuse_close_disagreement` refuses two years
    that contradict each other. The CLI must surface that refusal, and -- because every guard
    in `write_daily_panel` runs before either partition is written -- must leave the store with
    no `daily` partition at all rather than half a price panel."""
    transport = ScriptedTushareTransport(
        closes={DAILY_BASIC_DATASET: {(BUILD_SECURITIES[0], BUILD_SESSIONS[1]): 11.5}}
    )
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)

    assert build(tmp_path, "trade_cal", "stock_basic").exit_code == PanelExit.ok
    result = build(tmp_path, "price")

    assert result.exit_code == PanelExit.unhealthy
    assert "close" in result.output
    store = PanelStore(tmp_path / "panel")
    assert store.registered_years(DAILY_DATASET) == ()
    assert store.registered_years(DAILY_BASIC_DATASET) == ()


def test_panel_build_reports_a_provider_failure_without_echoing_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_probe_report`'s rule, at the second boundary that can reach a credential: only the
    closed-`Literal` category, the provider id and the dataset name are safe to print, because
    `ProviderFailure.message` can carry the token or the URL query string it was sent in."""
    leaking = f"Tushare rejected token={SECRET_TOKEN} for api daily"
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: RefusingTushareTransport(leaking))
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)

    result = build(tmp_path, "trade_cal")

    assert result.exit_code == PanelExit.provider_failure
    assert SECRET_TOKEN not in result.output
    assert "authentication" in result.output
    assert TRADING_CALENDAR_DATASET in result.output


def test_panel_build_never_prints_the_token_on_the_path_that_succeeds(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """Both halves. The CLI never reads `TUSHARE_TOKEN` itself -- `TushareProvider` does -- so
    the token must reach the transport (or nothing would be authenticated) and must appear
    nowhere in the CLI's own output, in either rendering."""
    human = build(tmp_path, *EVERY_BUILD_TARGET)
    machine = build(tmp_path, *EVERY_BUILD_TARGET, extra=["--json"])

    assert human.exit_code == PanelExit.ok
    assert machine.exit_code == PanelExit.ok
    assert SECRET_TOKEN not in human.output
    assert SECRET_TOKEN not in machine.output
    assert {entry["token"] for entry in scripted_build.payloads} == {SECRET_TOKEN}


def test_panel_build_refuses_a_price_year_with_no_halts_unless_the_waiver_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`write_daily_panel(halts=...)` has no default because a waiver that is a default is an
    accident. The CLI keeps that property: a year whose halt corpus is empty stops the build and
    names the flag, instead of quietly passing `None` and skipping the strongest guard."""

    class _NoHalts(ScriptedTushareTransport):
        def post(self, payload: dict[str, Any]) -> dict[str, Any]:
            if payload["api_name"] == SUSPENSION_DATASET:
                self.payloads.append(payload)
                return _response(HALT_FIELDS, [])
            return super().post(payload)

    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: _NoHalts())
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)

    assert build(tmp_path, "trade_cal", "stock_basic").exit_code == PanelExit.ok
    refused = build(tmp_path, "price")
    waived = build(tmp_path, "price", extra=["--no-halts", "--json"])

    assert refused.exit_code == PanelExit.unhealthy
    assert "--no-halts" in refused.output
    assert waived.exit_code == PanelExit.ok
    assert json.loads(waived.stdout)["halts"] == "waived"


def test_a_halt_corpus_the_domain_refuses_exits_unhealthy_with_its_message_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `SuspensionError` is a verdict about the data, so it must not be `internal_error`.

    Measured on a real build before this was fixed: `panel build --dataset price --year 2026`
    fetched for 22m46s, hit `SuspensionError` out of `load_suspensions`, and exited **5** --

        `panel build` did not finish: it raised an unhandled SuspensionError. This is a defect
        in the command, not a verdict about the panel ... The exception's own message is
        withheld

    -- because `SuspensionError` was not in `cli._PANEL_WRITE_REFUSALS` while
    `panel_doctor._LOAD_FAILURES` had listed it all along. Three things were wrong at once: the
    exit code told a scheduled job to file a bug about the command rather than to look at the
    data; the diagnosis was suppressed on the grounds that an unanticipated failure might carry
    a credential, when this one carries a ticker and a session; and two modules disagreed about
    what counts as a data fact. `test_the_write_refusals_and_the_doctors_load_failures_are_one
    _set` pins the last of those; this pins what the caller sees.

    The transport serves one security an untimed `S` and a timed `S` on the same session --
    the one multi-row shape `build_suspension_day` still refuses, and the one the live corpus
    has never served, so no real fetch is being simulated as broken here.
    """

    class _ContradictoryHalts(ScriptedTushareTransport):
        def post(self, payload: dict[str, Any]) -> dict[str, Any]:
            if str(payload["api_name"]) != SUSPENSION_DATASET:
                return super().post(payload)
            self.payloads.append(payload)
            day = _compact(_session_of(payload["params"]))
            return _response(
                HALT_FIELDS,
                [
                    [HALT_SECURITY, day, "S", None],
                    [HALT_SECURITY, day, "S", "09:30-09:40"],
                ],
            )

    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: _ContradictoryHalts())
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)

    assert build(tmp_path, "trade_cal", "stock_basic").exit_code == PanelExit.ok
    result = build(tmp_path, "price")

    assert result.exit_code == PanelExit.unhealthy
    assert result.exit_code != PanelExit.internal_error
    assert "is both halted and interrupted" in result.output
    assert HALT_SECURITY in result.output
    assert "defect in the command" not in result.output
    assert SECRET_TOKEN not in result.output


def test_panel_build_refuses_a_calendar_dependent_target_before_the_calendar_exists(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """`write_adjustment_factors` and `write_daily_panel` both refuse a year missing a session
    the calendar reports open, so the calendar has to be in the store before either runs. The
    remedy this command names has to be its own -- `panel build` has no `--no-calendar`, because
    the writers it drives take a `TradingCalendar` and there is nothing to run without one."""
    result = build(tmp_path, "adj_factor")

    assert result.exit_code == PanelExit.unhealthy
    assert "--dataset trade_cal" in result.output
    assert "--no-calendar" not in result.output
    assert scripted_build.payloads == []


def test_panel_build_refuses_a_year_whose_first_session_has_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop bound is `min(31 December, the fetch's local date - 1)`. Below 1 January there
    is no session to fetch at all, and a build that proceeded would hand its writers an empty
    batch list -- a plumbing error standing in for a plain fact about the clock."""
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", ScriptedTushareTransport)
    monkeypatch.setattr(cli, "_panel_clock", lambda: datetime(2026, 1, 1, 4, 0, tzinfo=UTC))

    assert build(tmp_path, "trade_cal").exit_code == PanelExit.ok
    result = build(tmp_path, "adj_factor")

    assert result.exit_code == PanelExit.bad_request
    assert "nothing to build yet" in result.output


def test_a_target_the_table_names_and_no_branch_builds_cannot_exit_zero(
    tmp_path: Path, scripted_build: ScriptedTushareTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact shape this whole issue exists to make unavailable, one layer further in.

    `_build_targets` accepts any key `PANEL_BUILD_TARGETS` holds, and `_build_panel` dispatches
    on an `if` chain. Add a fourteenth entry and forget the branch, and the command fetches
    nothing, writes nothing, and reports `exit 0` with `"partitions": []`: an empty success in
    the one place a CI job has only the exit code to read.

    Neither of this module's two existing table tests can see it.
    `test_panel_build_runs_the_targets_in_dependency_order_and_not_in_flag_order` asserts the
    order of the known targets' writes, and
    `test_the_build_targets_are_a_closed_table_in_dependency_order` compares the table against a
    literal that whoever adds the next entry updates in the same edit -- correctly, because
    that is what that test is for. So the check has to be in the command: every requested target
    produced at least one partition, or it did not build what it was asked for.

    `internal_error` rather than `unhealthy`: the table and the branches are both this module's
    own, and a caller re-fetching data would be chasing a defect in this file.
    """
    monkeypatch.setattr(
        cli,
        "PANEL_BUILD_TARGETS",
        MappingProxyType({**cli.PANEL_BUILD_TARGETS, UNBUILT_TARGET: (UNBUILT_TARGET,)}),
    )

    result = build(tmp_path, UNBUILT_TARGET, extra=["--json"])

    assert result.exit_code == PanelExit.internal_error
    assert UNBUILT_TARGET in result.stderr
    assert "PANEL_BUILD_TARGETS" in result.stderr
    assert scripted_build.payloads == []


def test_a_span_target_the_table_names_and_no_branch_builds_cannot_exit_zero_either(
    tmp_path: Path, scripted_build: ScriptedTushareTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same mutation aimed at the second build phase, which the first test cannot reach.

    `PANEL_BUILD_SPAN_TARGETS` runs its targets outside the year loop, so an entry that lands in
    that set skips `_build_panel` entirely and its audit with it. Adding a phase without
    extending the audit would have reopened the exit-0-with-no-partitions hole in the half of the
    command that builds the industry corpus and the financial indicators -- the datasets P3's
    neutralisation and its value/quality/growth families are specified against.

    So `_audit_written_partitions` is called for the span phase too, with `year=None` because
    there is no `--year` a span partition could be compared against.
    """
    monkeypatch.setattr(
        cli,
        "PANEL_BUILD_TARGETS",
        MappingProxyType({**cli.PANEL_BUILD_TARGETS, UNBUILT_TARGET: (UNBUILT_TARGET,)}),
    )
    monkeypatch.setattr(
        cli, "PANEL_BUILD_SPAN_TARGETS", cli.PANEL_BUILD_SPAN_TARGETS | {UNBUILT_TARGET}
    )

    result = build(tmp_path, UNBUILT_TARGET, extra=["--json"])

    assert result.exit_code == PanelExit.internal_error
    assert UNBUILT_TARGET in result.stderr
    assert "PANEL_BUILD_TARGETS" in result.stderr
    assert scripted_build.payloads == []


def test_a_refusal_names_the_partitions_it_had_already_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build is a sequence of whole-partition writes with no transaction around them.

    `write_daily_panel` is all-or-nothing and `_refuse_close_disagreement` stops it before
    either of its partitions is written -- but `write_suspensions` ran first and its partition
    is on disk, and `--dataset trade_cal` in the same invocation would have left that one too.
    An earlier version of this message said "nothing partial was stored", which was true of the
    writer that raised and false of the build. The message now states the partitions it can see,
    because a caller deciding whether to re-run or to clear the runtime directory needs the
    difference.
    """
    transport = ScriptedTushareTransport(
        closes={DAILY_BASIC_DATASET: {(BUILD_SECURITIES[0], BUILD_SESSIONS[1]): 11.5}}
    )
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)

    result = build(tmp_path, "trade_cal", "stock_basic", "price")

    assert result.exit_code == PanelExit.unhealthy
    assert "nothing partial was stored" not in result.output
    assert "written before this build stopped and are still stored" in result.stderr
    for stored in (TRADING_CALENDAR_DATASET, STOCK_BASIC_DATASET, SUSPENSION_DATASET):
        assert f"{stored}:{BUILD_YEAR}" in result.stderr
    # And the claim is checkable against the store rather than only against the sentence.
    store = PanelStore(tmp_path / "panel")
    assert store.registered_years(SUSPENSION_DATASET) == (BUILD_YEAR,)
    assert store.registered_years(TRADING_CALENDAR_DATASET) == (BUILD_YEAR,)
    assert store.registered_years(DAILY_DATASET) == ()


def test_a_partition_filed_under_a_year_nobody_asked_for_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partition's year comes from the rows, `--year` only bounds what is fetched and read.

    `write_suspensions` files by `panel_partition_year`, which reads the dates in the rows the
    provider served; `_build_price_panel` then reads the corpus back with
    `load_suspensions(years=(year,))`. When the fetch serves last year's rows those two are not
    the same year, and nothing below can notice: the write succeeds into 2025, the read finds
    the 2026 partition an *earlier* run left behind, and the build reports
    `halts: corroborated` about a corpus this fetch did not contribute a row to -- plus a 2025
    partition the caller never asked for.

    `max_staleness=None` cannot be the check that catches it, and is not a shortcut:
    `suspend_d`'s freshness is measured in event time, and the most recent halt event can
    legitimately be days older than `as_of`, so any finite bound refuses honest corpora. Waiving
    it is what makes "this year's corpus" and "a five-year-old one" the same observation to that
    call. The partition year is the check that survives.
    """
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)
    monkeypatch.setattr(cli, "_panel_transport", ScriptedTushareTransport)
    assert build(tmp_path, "trade_cal", "stock_basic", "price").exit_code == PanelExit.ok

    monkeypatch.setattr(cli, "_panel_transport", StaleHaltTransport)
    result = build(tmp_path, "price", extra=["--json"])

    assert result.exit_code == PanelExit.unhealthy
    assert f"--year {BUILD_YEAR} was asked for" in result.stderr
    assert f"{SUSPENSION_DATASET}:{BUILD_YEAR - 1}" in result.stderr
    assert PanelStore(tmp_path / "panel").registered_years(SUSPENSION_DATASET) == (
        BUILD_YEAR - 1,
        BUILD_YEAR,
    )


def test_panel_build_asks_the_year_it_was_given_rather_than_the_year_of_its_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_year_as_of(year)`, not `_panel_clock()`, is what `trade_cal` is asked at.

    `_trade_cal_params` derives the fetched year from `as_of`'s Asia/Shanghai year, so the two
    differ for every `--year` that is not the clock's. Unobservable against
    `ScriptedTushareTransport`, which answers `BUILD_YEAR` whatever it is asked -- so this one
    honours `start_date`, and then both directions are visible: the request names the year that
    was asked for, and the partition that comes back is filed under it.
    """
    transport = YearAwareCalendarTransport()
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)

    result = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--runtime-dir",
            str(tmp_path),
            "--year",
            str(BUILD_YEAR - 1),
            "--dataset",
            "trade_cal",
            "--json",
        ],
    )

    assert result.exit_code == PanelExit.ok
    assert transport.payloads[0]["params"]["start_date"] == f"{BUILD_YEAR - 1}0101"
    assert [entry["year"] for entry in json.loads(result.stdout)["partitions"]] == [BUILD_YEAR - 1]


def test_an_empty_session_is_ordinary_only_for_the_halt_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_EMPTY_SESSION_IS_ORDINARY` holds exactly `suspend_d`, and the tolerance must not widen.

    A session on which nothing was halted serves zero `suspend_d` rows, so an absent session and
    an empty one are indistinguishable there by construction. Every other dataset publishes on
    every open session, so its `no_data` batch is handed to the writer *unchanged* and refused
    by `merge_panel_batches` -- "the guard that knows what a missing session costs is the one
    that should say so".

    Widening the skip to every dataset keeps the exit code, which is why this asserts the
    refusal and not just the code: the empty session would be silently dropped and the calendar
    census downstream would refuse the year for a different, later reason, having lost the fact
    that the provider explicitly said "no data" for a day it publishes on.
    """
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_clock", lambda: BUILD_CLOCK)
    monkeypatch.setattr(cli, "_panel_transport", ScriptedTushareTransport)
    assert build(tmp_path, "trade_cal", "stock_basic").exit_code == PanelExit.ok

    monkeypatch.setattr(cli, "_panel_transport", EmptyLimitSessionTransport)
    result = build(tmp_path, "stk_limit")

    assert result.exit_code == PanelExit.unhealthy
    assert "cannot merge a 'no_data' batch" in result.output
    assert PanelStore(tmp_path / "panel").registered_years(PRICE_LIMIT_DATASET) == ()


def test_a_refusal_leaves_stdout_parseable_and_says_why_on_stderr(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """`_panel_fail` keeps the prose on stderr, and that is load-bearing rather than convention.

    `--json` promises a machine-readable stdout. A refusal *sentence* printed there would put
    prose in front of the JSON a caller is piping into a parser, on exactly the runs where it
    most wants a structured answer -- so a consumer would have to strip prose before parsing,
    which means guessing where it ends. Asserted here because the argument lives in a docstring
    and nothing pinned it.

    **What this asserted before `V2-P5-047` was `result.stdout == ""`, and that was the defect
    rather than the property.** "Parseable" was being satisfied vacuously: a machine caller who
    asked for data got a bare exit code and no reason at all, which is the thing the final
    product acceptance found. The property was always "stdout carries exactly one JSON document
    and no prose", and an empty stdout is only one way to not violate it -- the useless way. So
    the assertion is now the property itself: stdout parses whole, in one call, and the sentence
    a human reads is the same sentence the document carries.
    """
    result = build(tmp_path, UNBUILT_TARGET, extra=["--json"])

    assert result.exit_code == PanelExit.bad_request
    payload = json.loads(result.stdout)  # whole of stdout, one document, no prose to strip
    assert payload["status"] == "refused"
    assert payload["exit_code"] == int(PanelExit.bad_request)
    assert UNBUILT_TARGET in payload["detail"]
    assert UNBUILT_TARGET in result.stderr
    assert payload["detail"] in result.stderr


def test_a_refusal_without_json_still_leaves_stdout_completely_empty(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """The other half of `V2-P5-047`: a terminal caller sees no change at all.

    The structured document is written **only** when `--json` was asked for. Without this, the
    same command would start printing a JSON blob at somebody reading a terminal, which is the
    mirror image of the fault being fixed. Same invocation as the test above minus the flag, so
    the two differ in exactly the thing under test.
    """
    result = build(tmp_path, UNBUILT_TARGET)

    assert result.exit_code == PanelExit.bad_request
    assert result.stdout == ""
    assert UNBUILT_TARGET in result.stderr


def test_a_command_that_breaks_is_not_reported_as_an_unhealthy_panel(tmp_path: Path) -> None:
    """A defect in the CLI and a panel that failed its check must not be the same exit code.

    Reachable, not hypothetical: `--runtime-dir` naming a regular file raises
    `NotADirectoryError` out of `PanelStore`, which no branch here anticipates. Without
    `_panel_command` that reached Typer's own handler, which printed a traceback and exited 1 --
    `PanelExit.unhealthy`, i.e. "the panel is at fault, re-fetch it" -- for a situation in which
    nothing was checked at all. The message names the exception's *type* and not its message,
    for `_fetch_panel`'s reason: an unanticipated failure carries whatever the frame it escaped
    was holding.
    """
    not_a_directory = tmp_path / "runtime"
    not_a_directory.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "data-check",
            "--runtime-dir",
            str(not_a_directory),
            "--year",
            str(YEAR),
            "--dataset",
            DAILY_DATASET,
        ],
    )

    assert result.exit_code == PanelExit.internal_error
    assert result.exit_code != PanelExit.unhealthy
    assert "NotADirectoryError" in result.stderr
    assert "Traceback" not in result.output


def test_the_build_help_names_every_target_it_will_accept(tmp_path: Path) -> None:
    """The design is "refuse by name", so the names have to be discoverable without triggering a
    refusal to read them. They were in the error message and not in `--help`.

    Asserted against `cli.PANEL_BUILD_TARGETS` rather than this module's five, because the point
    is that the *whole* table is discoverable: the eight the P3 prerequisite added are exactly
    the ones a caller had no way to learn about, having been told for two phases that `income`
    "is not one of this command's build targets".
    """
    result = runner.invoke(app, ["panel", "build", "--help"])
    rendered = " ".join(result.stdout.split())

    assert result.exit_code == 0
    for target in cli.PANEL_BUILD_TARGETS:
        assert target in rendered, f"{target} is a build target and --help does not name it"


# --- the panels the read side is pointed at ----------------------------------------------------

FIXTURE_DATASETS: tuple[str, ...] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
)
"""The six datasets `panel_health_report` needs in scope for all three of its session-scoped
cross-checks to run; `panel_gate.SESSION_SCOPED_CROSS_CHECKS` is specified against them."""

FIXTURE_SESSION: date = generate_panel().sessions[-1]
"""The newest session the generated panel carries, read off the generator rather than restated.

`AS_OF` sits before 16:30 on the following day, so this session has published and the price
datasets' read-side requirement covers it. Taking it from `generate_panel()` means a change to
the generator's window moves this with it instead of leaving a hard-coded date that agrees with
the frame only until someone edits it.
"""


def seed_fixture_panel(runtime_dir: Path, *shapes: str) -> PanelStore:
    """A `tests/panel_fixtures.py` panel written into the directory the CLI reads."""
    store = PanelStore(runtime_dir / "panel")
    write_generated_panel(store, generate_panel(shapes=shapes))
    return store


def read_side(
    command: Sequence[str],
    runtime_dir: Path,
    *,
    datasets: Sequence[str] = FIXTURE_DATASETS,
    sessions: Sequence[date] = (FIXTURE_SESSION,),
    extra: Sequence[str] = (),
) -> Any:
    arguments = [
        *command,
        "--runtime-dir",
        str(runtime_dir),
        "--year",
        str(YEAR),
        "--as-of",
        AS_OF.isoformat(),
        "--exchange",
        # Read off the generator with `YEAR`, `AS_OF` and `FIXTURE_SESSION` rather than restated:
        # every other coordinate of this frame comes from `panel_fixtures`, and a literal here
        # would agree with the panel the fixture writes only until someone edits the generator.
        EXCHANGE,
    ]
    for name in datasets:
        arguments.extend(["--dataset", name])
    for day in sessions:
        arguments.extend(["--session", day.isoformat()])
    arguments.extend(extra)
    return runner.invoke(app, arguments)


# --- panel doctor ------------------------------------------------------------------------------


def test_panel_doctor_reports_a_clean_fixture_panel_and_exits_zero(tmp_path: Path) -> None:
    seed_fixture_panel(tmp_path)

    result = read_side(["panel", "doctor"], tmp_path, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    payload = json.loads(result.stdout)
    assert payload["is_clean"] is True
    assert payload["counts_by_severity"] == {"blocking": 0, "warning": 0, "notice": 0}
    assert [entry["dataset"] for entry in payload["datasets"]] == list(FIXTURE_DATASETS)
    assert {check["name"] for check in payload["cross_checks"] if check["ran"]} >= {
        "close_agreement",
        "unpriced_explained",
        "return_paths",
    }


def test_panel_doctor_keeps_the_existing_top_level_doctor_untouched(tmp_path: Path) -> None:
    """`doctor` is the provider-credential check and `panel doctor` is the panel's health
    report. Two different commands, and the second must not have shadowed the first."""
    provider_doctor = runner.invoke(app, ["doctor", "--json"])

    assert provider_doctor.exit_code == 0
    payload = json.loads(provider_doctor.stdout)
    assert set(payload) == {"status", "checks", "providers", "warnings"}
    assert "tushare.pro" in payload["providers"]


def test_a_notice_only_panel_stays_clean_and_exits_zero(tmp_path: Path) -> None:
    """The measurement is the argument. `ambiguous_filing` fires on 8.15% / 1.29% / 15.80% /
    13.70% of the four statement endpoints' real filings, so a `panel doctor` that returned
    non-zero on a notice would fail on every honest financial panel and be `|| true`-d away.
    The finding still has to be *reported*: a clean exit code is not silence."""
    seed_fixture_panel(tmp_path, "financials.same_day_duplicate_versions")

    result = read_side(
        ["panel", "doctor"], tmp_path, datasets=("income",), sessions=(), extra=["--json"]
    )

    assert result.exit_code == PanelExit.ok
    payload = json.loads(result.stdout)
    assert payload["is_clean"] is True
    assert payload["counts_by_severity"]["blocking"] == 0
    assert payload["counts_by_severity"]["warning"] == 0
    assert payload["counts_by_severity"]["notice"] > 0
    assert {finding["code"] for finding in payload["findings"]} <= {
        "ambiguous_filing",
        "duplicate_versions",
        "revised_rows",
    }


def test_panel_doctor_exits_non_zero_on_a_blocking_finding(tmp_path: Path) -> None:
    seed_fixture_panel(tmp_path)

    result = read_side(
        ["panel", "doctor"], tmp_path, extra=["--year", str(YEAR - 1), "--no-calendar", "--json"]
    )

    assert result.exit_code == PanelExit.unhealthy
    payload = json.loads(result.stdout)
    assert payload["is_clean"] is False
    assert "partition_missing" in {finding["code"] for finding in payload["findings"]}


def test_panel_doctor_human_output_names_every_dataset_and_its_verdict(tmp_path: Path) -> None:
    seed_fixture_panel(tmp_path)

    result = read_side(["panel", "doctor"], tmp_path)

    assert result.exit_code == PanelExit.ok
    for name in FIXTURE_DATASETS:
        assert f"READY {name}" in result.stdout


def test_panel_doctor_human_output_omits_the_limitation_line_for_a_dataset_with_none(
    tmp_path: Path,
) -> None:
    """`namechange` is the only one of the fifteen declared datasets whose module registers no
    structural limitation, so the count line has to be absent rather than reading
    "INFO 0 known limitation(s)" -- and the branch that decides has to be reachable.

    It reads `READY` rather than `BLOCKED` since `V2-P2-000` gave the generator a `namechange`
    partition, and that is the stronger form of the same assertion: a blocked dataset could
    have been silent about limitations because the report never got as far as looking them up.

    **The second half is new and is the point of the split.** Since P2's product acceptance the
    report also carries the *storage plane's* own boundaries -- that `PanelStore.query()` passes
    no point-in-time gate, and that an edit changing values in place leaves the census intact --
    and those hold for `namechange` exactly as they hold for `daily`. They are rendered on their
    own line with their own wording, so a reader of this command is not told that `namechange`
    has limitations when what has them is the plane underneath it, and so that the dataset
    branch above stays reachable in both directions.
    """
    seed_fixture_panel(tmp_path)

    result = read_side(["panel", "doctor"], tmp_path, datasets=("namechange",), sessions=())

    assert result.exit_code == PanelExit.ok
    assert "known limitation" not in result.stdout
    assert "structural boundary(ies) of the panel store itself" in result.stdout
    assert "READY namechange" in result.stdout


def test_panel_doctor_refuses_a_dataset_with_no_declared_cadence_as_a_bad_request(
    tmp_path: Path,
) -> None:
    """`panel_health_report` raises `PanelDoctorError` for a dataset it has no cadence for.
    That is a fault of the *request*, not of the panel, and the two must not share an exit
    code -- one is fixed by editing the command line, the other by re-fetching data."""
    seed_fixture_panel(tmp_path)

    result = read_side(["panel", "doctor"], tmp_path, datasets=("not_a_dataset",), sessions=())

    assert result.exit_code == PanelExit.bad_request
    assert "not_a_dataset" in result.output


# --- data-check --------------------------------------------------------------------------------


def test_data_check_clears_a_healthy_panel_and_states_the_width_of_the_permission(
    tmp_path: Path,
) -> None:
    """A clearance is not a list of names. `cleared` hands back `ClearedDataset` records, and
    the CLI has to carry the years, the corroborated sessions and the caveats with each one --
    a bare name reads as a whole year, which is the shape `V2-P1-013`'s review found Task 29's
    wrong number reachable through."""
    seed_fixture_panel(tmp_path)

    result = read_side(["data-check"], tmp_path, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    payload = json.loads(result.stdout)
    assert payload["is_blocked"] is False
    assert payload["blocks"] == []
    cleared = {entry["dataset"]: entry for entry in payload["cleared"]}
    assert set(cleared) == set(FIXTURE_DATASETS)
    assert cleared[ADJ_FACTOR_DATASET]["years"] == [YEAR]
    assert cleared[ADJ_FACTOR_DATASET]["corroborated_sessions"] == [FIXTURE_SESSION.isoformat()]
    assert cleared[ADJ_FACTOR_DATASET]["caveats"] == ["unverified_daily_coverage"]
    assert cleared[DAILY_DATASET]["caveats"] == []


def test_data_check_blocks_with_a_non_zero_exit_when_no_session_corroborates_the_factors(
    tmp_path: Path,
) -> None:
    """The fail-closed gate's whole point, on the command line. `adj_factor` publishes daily
    and its requirement waives `required_dates`, so with no session named nothing in the report
    can see a hole in it -- and a command that ran the gate, was refused, and still exited 0
    would be no gate at all in CI."""
    seed_fixture_panel(tmp_path)

    result = read_side(["data-check"], tmp_path, sessions=(), extra=["--json"])

    assert result.exit_code == PanelExit.unhealthy
    payload = json.loads(result.stdout)
    assert payload["is_blocked"] is True
    assert payload["cleared"] is None
    assert [block["code"] for block in payload["blocks"]] == ["unverified_daily_coverage"]
    assert payload["blocked_datasets"] == [ADJ_FACTOR_DATASET]


def test_the_doctor_and_the_gate_disagree_on_the_same_panel_and_both_are_right(
    tmp_path: Path,
) -> None:
    """`panel doctor` answers "is this panel sick"; `data-check` answers "may *this request*
    read it". The gate's own refusal, `unverified_daily_coverage`, is not a health code, so a
    panel with nothing wrong with it can still refuse a request that named no session. Pinned
    together because the temptation is to make one command's exit code the other's."""
    seed_fixture_panel(tmp_path)

    doctor = read_side(["panel", "doctor"], tmp_path, sessions=(), extra=["--json"])
    gate = read_side(["data-check"], tmp_path, sessions=(), extra=["--json"])

    assert doctor.exit_code == PanelExit.ok
    assert json.loads(doctor.stdout)["is_clean"] is True
    assert gate.exit_code == PanelExit.unhealthy
    assert json.loads(gate.stdout)["is_blocked"] is True


def test_data_check_carries_the_notices_on_a_clearance_it_granted(tmp_path: Path) -> None:
    seed_fixture_panel(tmp_path, "financials.same_day_duplicate_versions")

    result = read_side(
        ["data-check"], tmp_path, datasets=("income",), sessions=(), extra=["--json"]
    )

    human = read_side(["data-check"], tmp_path, datasets=("income",), sessions=())

    assert result.exit_code == PanelExit.ok
    payload = json.loads(result.stdout)
    assert payload["is_blocked"] is False
    assert [entry["dataset"] for entry in payload["cleared"]] == ["income"]
    assert {notice["code"] for notice in payload["notices"]}
    assert all(notice["severity"] == "notice" for notice in payload["notices"])
    assert human.exit_code == PanelExit.ok
    assert "CLEARED income" in human.stdout
    for code in {notice["code"] for notice in payload["notices"]}:
        assert f"NOTICE income {code}" in human.stdout


def test_data_check_human_output_carries_the_caveats_that_bound_a_clearance(
    tmp_path: Path,
) -> None:
    """The width of a permission has to survive the rendering a human actually reads.

    `adj_factor` clears here with `unverified_daily_coverage` still open outside the one session
    a cross-check corroborated -- a cleared dataset that is *not* cleared for the whole year. The
    JSON carries that, and only the JSON was asserted; a human line that dropped it to `-` would
    pass every test in this module while telling the reader the one thing a bare name always
    tells them, which is that the year is covered. That assumption is precisely how
    `V2-P1-013`'s review found Task 29's wrong number reachable through a *cleared* gate, so the
    human rendering is where it matters most.
    """
    seed_fixture_panel(tmp_path)

    result = read_side(["data-check"], tmp_path)

    assert result.exit_code == PanelExit.ok
    assert (
        f"CLEARED {ADJ_FACTOR_DATASET} years={YEAR} "
        f"corroborated_sessions={FIXTURE_SESSION.isoformat()} "
        "caveats=unverified_daily_coverage"
    ) in result.stdout
    assert f"CLEARED {DAILY_DATASET} years={YEAR} " in result.stdout


def test_data_check_human_output_names_each_block_and_its_code(tmp_path: Path) -> None:
    seed_fixture_panel(tmp_path)

    result = read_side(["data-check"], tmp_path, sessions=())

    assert result.exit_code == PanelExit.unhealthy
    assert f"BLOCKED {ADJ_FACTOR_DATASET} unverified_daily_coverage" in result.stdout


def test_data_check_refuses_a_gate_usage_error_as_a_bad_request(tmp_path: Path) -> None:
    """`require_datasets` raises `PanelGateError` for a request naming no dataset. Typer's own
    required-option check catches that one first, so the reachable instance is the other: an
    `as-of` that is not an instant at all."""
    seed_fixture_panel(tmp_path)

    result = read_side(["data-check"], tmp_path, extra=["--as-of", "the-day-before-yesterday"])

    assert result.exit_code == PanelExit.bad_request
    assert "--as-of" in result.output


def test_a_naive_as_of_is_refused_rather_than_localised(tmp_path: Path) -> None:
    """A point-in-time question answered in a guessed timezone is wrong by up to a session --
    `panel_gate`'s own `date_timezone` exclusion records one store where `Asia/Shanghai` reports
    `date_gap` and `is_clean=False` while `UTC` reports no code at all. So an offset-less
    instant is a bad request, not an invitation to pick a zone."""
    seed_fixture_panel(tmp_path)

    result = read_side(["data-check"], tmp_path, extra=["--as-of", "2026-01-17T04:00:00"])

    assert result.exit_code == PanelExit.bad_request
    assert "offset" in result.output


def test_an_unparseable_session_is_refused_before_anything_is_read(tmp_path: Path) -> None:
    seed_fixture_panel(tmp_path)

    result = read_side(
        ["panel", "doctor"], tmp_path, sessions=(), extra=["--session", "13/01/2026"]
    )

    assert result.exit_code == PanelExit.bad_request
    assert "--session" in result.output


def test_a_missing_calendar_stops_the_read_side_and_names_both_ways_out(tmp_path: Path) -> None:
    """`--as-of` is deliberately omitted: its default is the wall clock, and this is the one
    path that exercises that default end to end."""
    result = runner.invoke(
        app,
        [
            "data-check",
            "--runtime-dir",
            str(tmp_path),
            "--year",
            str(YEAR),
            "--dataset",
            DAILY_DATASET,
        ],
    )

    assert result.exit_code == PanelExit.unhealthy
    assert "--dataset trade_cal" in result.output
    assert "--no-calendar" in result.output


# --- the exit-code table -----------------------------------------------------------------------


def test_every_situation_maps_to_the_exit_code_the_table_declares(
    tmp_path: Path, scripted_build: ScriptedTushareTransport
) -> None:
    """One table, asserted whole. Each row is a situation actually produced by invoking the
    real app, so this fails both when a code changes and when a situation stops producing the
    code it is supposed to -- which a per-test assertion cannot show, because the interesting
    property is that the four codes stay *distinct* from each other and from click's own 2."""
    panel_dir = tmp_path / "read"
    panel_dir.mkdir()
    seed_fixture_panel(panel_dir)
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("", encoding="utf-8")

    observed = {
        "build.written": build(tmp_path / "built", *EVERY_BUILD_TARGET).exit_code,
        "build.coupled_dataset": build(tmp_path / "built", "daily").exit_code,
        "build.unknown_target": build(tmp_path / "built", UNBUILT_TARGET).exit_code,
        "doctor.clean": read_side(["panel", "doctor"], panel_dir).exit_code,
        "doctor.blocking": read_side(
            ["panel", "doctor"], panel_dir, extra=["--year", str(YEAR - 1), "--no-calendar"]
        ).exit_code,
        "doctor.unknown_dataset": read_side(
            ["panel", "doctor"], panel_dir, datasets=("nope",), sessions=()
        ).exit_code,
        "data-check.cleared": read_side(["data-check"], panel_dir).exit_code,
        "data-check.blocked": read_side(["data-check"], panel_dir, sessions=()).exit_code,
        "data-check.bad_as_of": read_side(
            ["data-check"], panel_dir, extra=["--as-of", "not-an-instant"]
        ).exit_code,
        # The `except (PanelGateError, PanelDoctorError)` around `require_datasets`. Added by
        # this issue's review: the only test that named that branch reached the `--as-of` guard
        # in `_panel_as_of` instead and said so in its own docstring, so the branch was reachable,
        # correct and unasserted -- collapsing it into `unhealthy` changed no test.
        "data-check.unknown_dataset": read_side(
            ["data-check"], panel_dir, datasets=("nope",), sessions=()
        ).exit_code,
        "data-check.runtime_dir_is_a_file": runner.invoke(
            app,
            [
                "data-check",
                "--runtime-dir",
                str(not_a_directory),
                "--year",
                str(YEAR),
                "--dataset",
                DAILY_DATASET,
            ],
        ).exit_code,
        "click.unknown_flag": runner.invoke(app, ["data-check", "--nonsense"]).exit_code,
    }

    assert observed == {
        "build.written": PanelExit.ok,
        "build.coupled_dataset": PanelExit.bad_request,
        "build.unknown_target": PanelExit.bad_request,
        "doctor.clean": PanelExit.ok,
        "doctor.blocking": PanelExit.unhealthy,
        "doctor.unknown_dataset": PanelExit.bad_request,
        "data-check.cleared": PanelExit.ok,
        "data-check.blocked": PanelExit.unhealthy,
        "data-check.bad_as_of": PanelExit.bad_request,
        "data-check.unknown_dataset": PanelExit.bad_request,
        "data-check.runtime_dir_is_a_file": PanelExit.internal_error,
        "click.unknown_flag": CLICK_USAGE_EXIT_CODE,
    }


# --- a refusal names what fixes it -------------------------------------------------------------
#
# `V2-P5-045` / `V2-P5-046` / `V2-P5-047`. Three faults measured during the final product
# acceptance, all three on this command and all three the same failure of the repository's own
# rule: a refusal must name the flag, the record or the command that fixes it. Each is driven
# here rather than against a helper, because in every case the *report* was already correct and
# what was missing was in its delivery.


def test_a_date_gap_from_a_defaulted_as_of_names_the_clock_that_decided_it(
    tmp_path: Path,
) -> None:
    """`V2-P5-045`. `--as-of` defaults to "now", so a January panel fails when asked in August.

    Measured: `daily cannot be read at <now>: ['date_gap']; 157 required date(s) are absent
    from daily, starting at 2026-01-19`. Accurate, and it names nothing the caller can do --
    the panel is not broken, the *question* is dated today. Adding `--as-of` made the identical
    command succeed, which is why the refusal has to name it.

    The clock is not passed here at all, deliberately: `read_side` supplies `AS_OF` and that is
    exactly the argument whose absence produces this fault. A fixture that passed a clock could
    not separate the two answers -- with `AS_OF` the panel is clean and there is no `date_gap`
    to inspect.
    """
    seed_fixture_panel(tmp_path)

    result = runner.invoke(
        app,
        [
            "panel",
            "doctor",
            "--runtime-dir",
            str(tmp_path),
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--dataset",
            DAILY_DATASET,
        ],
    )

    assert result.exit_code == PanelExit.unhealthy
    gaps = [line for line in result.stdout.splitlines() if "date_gap" in line]
    assert gaps, result.stdout + result.stderr
    assert all("--as-of" in line for line in gaps), gaps


def test_a_date_gap_finding_carries_its_remedy_over_json_too(tmp_path: Path) -> None:
    """The same sentence on the same finding, so the two faces cannot drift about the remedy."""
    seed_fixture_panel(tmp_path)

    result = runner.invoke(
        app,
        [
            "panel",
            "doctor",
            "--json",
            "--runtime-dir",
            str(tmp_path),
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--dataset",
            DAILY_DATASET,
        ],
    )

    assert result.exit_code == PanelExit.unhealthy
    payload = json.loads(result.stdout)
    gaps = [item for item in payload["findings"] if item["code"] == "date_gap"]
    assert gaps, payload["findings"]
    assert all("--as-of" in item["detail"] for item in gaps)


def test_a_missing_calendar_subject_names_the_exchange_the_store_actually_holds(
    tmp_path: Path,
) -> None:
    """`V2-P5-046`. `subject_missing` printed a count, and both suggested remedies were wrong.

    Measured: `the SSE calendar cannot be read at ...: ['subject_missing']; 1 required
    subject(s) are absent from trade_cal. Build it first (...), or state on the record that
    this run has no calendar (--no-calendar ...)`. But `trade_cal` **was** built and is
    healthy -- it holds `SZSE`. Rebuilding would fetch SSE (paid, slow) and `--no-calendar`
    discards the check; the actual fix is `--exchange SZSE`, which the same command accepts.
    `missing_items` carried the answer server-side and the human output printed only its
    length.

    `EXCHANGE` is read off the generator rather than restated, and the request deliberately
    omits `--exchange` so the default (`SSE`) is what the store is asked for -- the fixture is
    only discriminating because the two differ.
    """
    seed_fixture_panel(tmp_path)

    result = runner.invoke(
        app,
        [
            "panel",
            "doctor",
            "--runtime-dir",
            str(tmp_path),
            "--year",
            str(YEAR),
            "--as-of",
            AS_OF.isoformat(),
            "--dataset",
            DAILY_DATASET,
        ],
    )

    assert result.exit_code != PanelExit.ok
    message = result.stderr
    assert "subject_missing" in message
    assert EXCHANGE in message, f"the stored exchange is not named in: {message}"
    assert "--exchange" in message, f"the flag that fixes this is not named in: {message}"

    # And the remedy the refusal names actually resolves it, on the same command.
    fixed = runner.invoke(
        app,
        [
            "panel",
            "doctor",
            "--runtime-dir",
            str(tmp_path),
            "--year",
            str(YEAR),
            "--as-of",
            AS_OF.isoformat(),
            "--dataset",
            DAILY_DATASET,
            "--exchange",
            EXCHANGE,
        ],
    )
    assert fixed.exit_code == PanelExit.ok, fixed.stdout + fixed.stderr
    assert f"READY {DAILY_DATASET}" in fixed.stdout


def test_json_on_a_refusal_path_is_json_and_not_nothing(tmp_path: Path) -> None:
    """`V2-P5-047`. `--json` emitted **zero bytes** on the refusal path: rc=1, no JSON at all.

    A machine caller who asked for data got a bare exit code, which is precisely when it most
    needs the structured reason -- `_panel_fail`'s own docstring already says so ("--json
    output has to stay parseable on stdout even when the command is on its way to a non-zero
    exit, which is precisely when a caller most needs the structured reasons") and then wrote
    the message to stderr and nothing to stdout.

    Driven on the calendar refusal because that is the one the acceptance measured; the shape
    is held for every `--json` command by
    `tests/unit/test_cli_panel_rules.py::test_every_json_command_answers_a_refusal_with_json`.
    """
    seed_fixture_panel(tmp_path)

    result = runner.invoke(
        app,
        [
            "panel",
            "doctor",
            "--json",
            "--runtime-dir",
            str(tmp_path),
            "--year",
            str(YEAR),
            "--as-of",
            AS_OF.isoformat(),
            "--dataset",
            DAILY_DATASET,
        ],
    )

    assert result.exit_code != PanelExit.ok
    assert result.stdout.strip(), "--json wrote nothing at all on the refusal path"
    payload = json.loads(result.stdout)
    assert payload["status"] == "refused"
    assert payload["exit_code"] == int(result.exit_code)
    assert EXCHANGE in payload["detail"]
    assert "--exchange" in payload["detail"]
    # The human channel keeps the same sentence, so neither face is the only one that says it.
    assert payload["detail"] in result.stderr
