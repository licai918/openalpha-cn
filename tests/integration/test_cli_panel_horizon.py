"""`panel build`'s session horizon: pinning it, refusing to split it, and saying it out loud.

`V2-P1-018`'s R6. `cli._build_sessions` bounds the loop at the fetch clock's Asia/Shanghai date
minus one day, and the clock is read **once per invocation**. The five build targets are five
invocations, so a build that starts before local midnight and ends after it runs some targets
against one horizon and the rest against another. `tests/e2e/test_panel_chain_online.py`
measured exactly that on this repository's first live build:

    daily / daily_basic / adj_factor   144 sessions, last 2026-08-07
    stk_limit                          145 sessions, last 2026-08-10

The panel that lands cannot be assessed clean at **any** `as_of`. Earlier than the newest
partition's last row, `panel doctor` reports `stk_limit not_yet_knowable`; at or after it, the
calendar requires that session of the price panel and it reports `daily date_gap`. Both
refusals are correct, there is no third instant, and the only remedy is re-fetching the lagging
targets -- 386s and 2,374s on that run.

Two things close it and this module drives both through the real CLI: `--as-of`, which lets one
horizon be shared across invocations, and `_refuse_split_horizon`, which refuses a build whose
horizon disagrees with a partition already stored -- **before the first session is fetched**, so
being wrong costs one re-run rather than forty-five minutes.

Only the HTTP transport is doubled. Most of the scenarios use `adj_factor` and `stk_limit`, one
from each end of `PANEL_BUILD_TARGETS`' order and both in `cli.SESSION_SCOPED_DATASETS`, so the
split horizon can be expressed in two cheap targets; the `price` target is scripted as well
because the one dataset that is *not* session-scoped, `suspend_d`, can only be shown to be
correctly excluded by writing a real halt corpus through `write_daily_panel`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel_view import panel_store

runner = CliRunner()

TOKEN = "sk-panel-horizon-token-must-not-leak-51877"
YEAR = 2026
EXCHANGE = "SSE"
SECURITIES: tuple[str, ...] = ("000001.SZ", "600000.SH")

SESSIONS: tuple[date, ...] = tuple(
    date(YEAR, 1, day) for day in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20)
)
"""Twelve open sessions, 2026-01-05..2026-01-20.

More than ten on purpose: `cli.PANEL_PROGRESS_EVERY` is ten, so an eleven-session build
exercises both the periodic line and the final one, which a three-session frame cannot.
"""

EARLY_CLOCK = datetime(YEAR, 1, 20, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2026-01-20, so the loop runs to 2026-01-19: eleven sessions."""

LATE_CLOCK = datetime(YEAR, 1, 21, 4, 0, tzinfo=UTC)
"""The next day. Twelve sessions -- the horizon a build started before local midnight and
finished after it would give its remaining targets."""

EARLY_SESSIONS = 11
LATE_SESSIONS = 12

CLOSE_CLOCK = datetime(YEAR, 1, 20, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on 2026-01-20 -- an open session, half an hour after it published.

The only clock in this module above `DAILY_AVAILABILITY_TIME` (16:30), and `V2-P4-063` is what
its absence cost: `EARLY_CLOCK` and `LATE_CLOCK` are both noon, and below 16:30 the build bound
and the read bound agree exactly, so no fixture here could separate them.
"""

CLOSE_SESSION: date = SESSIONS[LATE_SESSIONS - 1]
"""2026-01-20 -- the session `CLOSE_CLOCK` falls on, and the one a build at that instant owes."""

HALT_SESSION: date = SESSIONS[2]
"""The only session the scripted `suspend_d` serves a row for -- 2026-01-07, well before either
horizon. That is the ordinary shape of the dataset and the reason it is excluded from
`cli.SESSION_SCOPED_DATASETS`; see the test that drives it."""

CALENDAR_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
FACTOR_FIELDS = ["ts_code", "trade_date", "adj_factor"]
LIMIT_FIELDS = ["ts_code", "trade_date", "up_limit", "down_limit"]
HALT_FIELDS = ["ts_code", "trade_date", "suspend_type", "suspend_timing"]
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
CLOSES: Mapping[str, float] = {"000001.SZ": 10.0, "600000.SH": 20.0}


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


class HorizonTransport:
    """Answers every dataset `panel build` can ask for, over this module's twelve-session frame.

    `suspend_d` is the one with a shape rather than a value: it serves a row on exactly one
    session and nothing on the others, which is what a real halt corpus looks like and what
    `test_a_year_whose_last_halt_predates_the_horizon_is_not_a_split_horizon` needs.
    """

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def datasets_asked(self) -> list[str]:
        return [str(payload["api_name"]) for payload in self.payloads]

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api_name = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api_name == TRADING_CALENDAR_DATASET:
            items: list[list[Any]] = []
            previous: str | None = None
            day = date(YEAR, 1, 1)
            while day <= date(YEAR, 12, 31):
                is_open = day in SESSIONS
                items.append([params["exchange"], _compact(day), 1 if is_open else 0, previous])
                if is_open:
                    previous = _compact(day)
                day += timedelta(days=1)
            return _response(CALENDAR_FIELDS, items)
        session = datetime.strptime(params["trade_date"], "%Y%m%d").date()
        if api_name == ADJ_FACTOR_DATASET:
            return _response(FACTOR_FIELDS, [[code, _compact(session), 1.0] for code in SECURITIES])
        if api_name == PRICE_LIMIT_DATASET:
            return _response(
                LIMIT_FIELDS,
                [[code, _compact(session), 12.03, 9.85] for code in SECURITIES],
            )
        if api_name == SUSPENSION_DATASET:
            if session != HALT_SESSION:
                return _response(HALT_FIELDS, [])
            return _response(HALT_FIELDS, [[SECURITIES[0], _compact(session), "R", None]])
        if api_name == DAILY_DATASET:
            return _response(
                BAR_FIELDS,
                [
                    [code, _compact(session), *([CLOSES[code]] * 5), 0.0, 1000.0, 10000.0]
                    for code in SECURITIES
                ],
            )
        if api_name == DAILY_BASIC_DATASET:
            return _response(
                VALUATION_FIELDS,
                [
                    [code, _compact(session), CLOSES[code], *([1.0] * len(VALUATION_EXTRA_FIELDS))]
                    for code in SECURITIES
                ],
            )
        raise AssertionError(f"the CLI asked for an unscripted dataset: {api_name}")


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> HorizonTransport:
    scripted = HorizonTransport()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: EARLY_CLOCK)
    return scripted


def _at(monkeypatch: pytest.MonkeyPatch, clock: datetime) -> None:
    monkeypatch.setattr(cli, "_panel_clock", lambda: clock)


def _build(runtime_dir: Path, target: str, *extra: str) -> Any:
    return runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            target,
            "--year",
            str(YEAR),
            "--runtime-dir",
            str(runtime_dir),
            "--exchange",
            EXCHANGE,
            *extra,
        ],
    )


def _payload(result: Any) -> Any:
    return json.loads(result.stdout)


def _seeded(runtime_dir: Path, *extra: str) -> None:
    """`trade_cal` in the store, which every session-scoped target needs to have been written
    first. Built at `EARLY_CLOCK`; a calendar is a whole year and carries no horizon of its own,
    which is why `trade_cal` is not in `cli.SESSION_SCOPED_DATASETS`."""
    assert _build(runtime_dir, TRADING_CALENDAR_DATASET, "--json", *extra).exit_code == PanelExit.ok


# --- the horizon is reported, and can be pinned ----------------------------------------------


def test_the_build_reports_the_instant_its_horizon_came_from(
    tmp_path: Path, transport: HorizonTransport
) -> None:
    """A value a caller can only get by being told. `sessions.last` names the last *session*,
    not the instant that bounded the loop, and the two are not derivable from each other -- any
    instant on the following non-session day gives the same last session. Re-fetching one target
    of this panel later means passing this back, so it is reported in both renderings."""
    _seeded(tmp_path)

    machine = _build(tmp_path, ADJ_FACTOR_DATASET, "--json")
    human = _build(tmp_path, ADJ_FACTOR_DATASET)

    assert machine.exit_code == PanelExit.ok, machine.stdout
    assert _payload(machine)["as_of"] == EARLY_CLOCK.isoformat()
    assert _payload(machine)["sessions"]["count"] == EARLY_SESSIONS
    assert f"AS-OF {EARLY_CLOCK.isoformat()}" in human.stdout


def test_the_as_of_flag_pins_the_horizon_against_a_clock_that_has_moved(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix, stated at its narrowest: the same `--as-of` gives the same horizon whatever the
    wall clock says, so five invocations of a build that crosses midnight can agree."""
    _seeded(tmp_path)
    first = _build(tmp_path, ADJ_FACTOR_DATASET, "--as-of", EARLY_CLOCK.isoformat(), "--json")

    _at(monkeypatch, LATE_CLOCK)
    second = _build(tmp_path, PRICE_LIMIT_DATASET, "--as-of", EARLY_CLOCK.isoformat(), "--json")

    assert first.exit_code == PanelExit.ok, first.stdout
    assert second.exit_code == PanelExit.ok, second.stdout
    assert _payload(first)["sessions"] == _payload(second)["sessions"]
    assert _payload(second)["sessions"]["count"] == EARLY_SESSIONS


def test_an_unpinned_second_build_would_have_run_to_a_later_horizon(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The premise the two tests below rest on, asserted separately so their refusal cannot be
    mistaken for a frame in which nothing would have differed anyway. On a fresh store the late
    clock reaches twelve sessions where the early clock reaches eleven."""
    _seeded(tmp_path)
    _at(monkeypatch, LATE_CLOCK)

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--json")

    assert result.exit_code == PanelExit.ok, result.stdout
    assert _payload(result)["sessions"]["count"] == LATE_SESSIONS


# --- the horizon may be pinned; the point-in-time filter may not ------------------------------

FUTURE_PIN = "2026-01-22T12:00:00+08:00"
"""Two days past `EARLY_CLOCK`. `_build_sessions` bounds the loop at this instant's local date
minus one, so it reaches 2026-01-20 -- a session whose 16:30 cross section has not published at
the wall clock these tests run on."""


def test_an_as_of_ahead_of_the_wall_clock_cannot_store_a_session_that_has_not_published(
    tmp_path: Path, transport: HorizonTransport
) -> None:
    """The defect `--as-of` introduced, and the reason it is not merely a horizon flag.

    `TushareProvider._decode_panel_rows` keeps a row only if it was knowable **both** at the
    request's `as_of` and at the instant the fetch ran. `V2-P1-018` passed the resolved
    `--as-of` in as the provider's `clock`, so both halves read the same caller-supplied value
    and the second one became true by construction. Measured against `90beba8` with exactly
    this frame: `exit 0`, a stored `adj_factor` partition reaching 2026-01-20, and a
    `max_available_time` of 16:30 Asia/Shanghai -- four and a half hours after the wall clock
    the build ran on. Look-ahead, written to disk, by a flag whose whole job was alignment.

    The fetch instant is now `clock`, which no flag reaches, so the session is dropped, the
    writer refuses the year that is missing it, and nothing lands.
    """
    _seeded(tmp_path, "--as-of", FUTURE_PIN)

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--as-of", FUTURE_PIN)

    assert result.exit_code == PanelExit.unhealthy, result.stdout
    assert "none of which was yet knowable" in result.stderr
    assert "or at the instant of the fetch" in result.stderr
    assert panel_store(tmp_path).registered_years(ADJ_FACTOR_DATASET) == ()


def test_the_same_pin_builds_that_session_once_the_wall_clock_has_reached_it(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The premise the refusal above rests on, asserted separately.

    Without this, that test would pass just as well if `--as-of` had been broken outright, or
    if this module's scripted frame simply could not produce a twelfth session. Same pin, same
    transport, wall clock moved past the session -- and the build lands all twelve.
    """
    _seeded(tmp_path, "--as-of", FUTURE_PIN)
    _at(monkeypatch, LATE_CLOCK)

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--as-of", FUTURE_PIN, "--json")

    assert result.exit_code == PanelExit.ok, result.stderr
    assert _payload(result)["sessions"]["count"] == LATE_SESSIONS
    assert _payload(result)["sessions"]["last"] == "2026-01-20"


def test_a_rebuild_at_the_same_as_of_rewrites_nothing_even_after_the_clock_moves(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What separating the stamp from the clock had to preserve, and the reason `--as-of` is
    not simply refused when it runs ahead of the wall clock.

    `ingested_time` is one of the four clock columns every partition stores, so it is inside
    `PanelStore._content_hash`. Read it from the wall clock and a re-fetch of an unchanged year
    is a *different* partition every time -- new hash, new `written_at`, new Parquet file.
    Pinning it makes the rebuild the no-op it should always have been, which is what
    `PanelStore._reusable_partition` was built to detect, and this asserts all three facts
    rather than only the hash.
    """
    _seeded(tmp_path)
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--as-of", EARLY_CLOCK.isoformat()).exit_code == 0
    store = panel_store(tmp_path)
    parquet = tmp_path / "panel" / ADJ_FACTOR_DATASET / str(YEAR) / "data.parquet"
    first = store.read_coverage(ADJ_FACTOR_DATASET, YEAR)
    assert first is not None
    before = (first.partition_content_hash, parquet.stat().st_mtime_ns, parquet.stat().st_size)

    _at(monkeypatch, LATE_CLOCK)
    again = _build(tmp_path, ADJ_FACTOR_DATASET, "--as-of", EARLY_CLOCK.isoformat(), "--json")

    assert again.exit_code == PanelExit.ok, again.stderr
    assert _payload(again)["sessions"]["count"] == EARLY_SESSIONS
    second = store.read_coverage(ADJ_FACTOR_DATASET, YEAR)
    assert second is not None
    assert before == (
        second.partition_content_hash,
        parquet.stat().st_mtime_ns,
        parquet.stat().st_size,
    )


# --- the split horizon is refused ------------------------------------------------------------


def test_a_build_that_would_split_the_horizon_is_refused_before_it_fetches_anything(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The e2e failure, reproduced offline: one target built before local midnight, the next
    after it.

    Two things are asserted and the second is the one that makes this worth having. The build is
    refused -- and **no `stk_limit` request was sent**, because the check runs after the calendar
    load and before the session loop. The live equivalent of getting this wrong was 2,374s of
    fetching that had to be thrown away.
    """
    _seeded(tmp_path)
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--json").exit_code == PanelExit.ok
    _at(monkeypatch, LATE_CLOCK)
    before = transport.datasets_asked().count(PRICE_LIMIT_DATASET)

    result = _build(tmp_path, PRICE_LIMIT_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "cannot be assessed clean at any as_of" in result.stderr
    assert f"{ADJ_FACTOR_DATASET} stops at 2026-01-19" in result.stderr
    assert transport.datasets_asked().count(PRICE_LIMIT_DATASET) == before == 0


def test_the_refusal_names_an_as_of_that_actually_resolves_it(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remedy in a message is a claim until something runs it.

    The instant is parsed out of the refusal and handed straight back to the same command, and
    the build then lands on the stored horizon. Nothing here computes the expected instant
    independently -- that would test this module's arithmetic against itself; what is checked is
    that the sentence a user reads is a command that works.
    """
    _seeded(tmp_path)
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--json").exit_code == PanelExit.ok
    _at(monkeypatch, LATE_CLOCK)
    refusal = _build(tmp_path, PRICE_LIMIT_DATASET)
    assert refusal.exit_code == PanelExit.unhealthy
    # The line, not the rest of stderr: `panel_build`'s `except Exception` adds
    # `_stored_so_far`'s sentence below the refusal on its way out.
    suggested = refusal.stderr.split("--as-of ", 1)[1].splitlines()[0].strip()

    retried = _build(tmp_path, PRICE_LIMIT_DATASET, "--as-of", suggested, "--json")

    assert retried.exit_code == PanelExit.ok, retried.stderr
    assert _payload(retried)["sessions"]["count"] == EARLY_SESSIONS
    assert _payload(retried)["sessions"]["last"] == "2026-01-19"


def test_building_the_session_scoped_targets_together_moves_the_horizon_atomically(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other remedy, and the reason the refusal is not simply "never change the horizon".

    A partition is replaced **whole** -- `PanelStore` has no append -- so extending a stored year
    means rebuilding every session-scoped partition of it, and doing that one target at a time
    passes through the incoherent state this refuses. One invocation reads one clock, so the two
    partitions move together and neither is ever the odd one out.
    """
    _seeded(tmp_path)
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--json").exit_code == PanelExit.ok
    assert _build(tmp_path, PRICE_LIMIT_DATASET, "--json").exit_code == PanelExit.ok
    _at(monkeypatch, LATE_CLOCK)

    result = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            ADJ_FACTOR_DATASET,
            "--dataset",
            PRICE_LIMIT_DATASET,
            "--year",
            str(YEAR),
            "--runtime-dir",
            str(tmp_path),
            "--exchange",
            EXCHANGE,
            "--json",
        ],
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert _payload(result)["sessions"]["count"] == LATE_SESSIONS
    assert {entry["dataset"] for entry in _payload(result)["partitions"]} == {
        ADJ_FACTOR_DATASET,
        PRICE_LIMIT_DATASET,
    }


def test_a_rebuild_at_the_same_horizon_is_not_refused(
    tmp_path: Path, transport: HorizonTransport
) -> None:
    """The half of a fail-closed guard that is easy to leave untested. A guard that refused
    every second build would pass every test written about its refusals."""
    _seeded(tmp_path)
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--json").exit_code == PanelExit.ok

    again = _build(tmp_path, ADJ_FACTOR_DATASET, "--json")

    assert again.exit_code == PanelExit.ok, again.stderr
    assert _payload(again)["sessions"]["count"] == EARLY_SESSIONS


# --- progress ---------------------------------------------------------------------------------


def test_the_session_loop_reports_progress_on_stderr_without_disturbing_the_json(
    tmp_path: Path, transport: HorizonTransport
) -> None:
    """`V2-P1-018`'s R13, cheapest item first. A measured live build ran 49m30s and printed
    **nothing**, so a caller could not tell a live fetch from a wedged one.

    stderr rather than stdout, and the `--json` document is parsed here to prove it: a progress
    line interleaved into stdout would break every scripted caller. Eleven sessions against a
    ten-session cadence gives the periodic line and the final one, which is what makes the
    `index == len(sessions)` branch reachable rather than incidental.
    """
    _seeded(tmp_path)

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--json")

    assert result.exit_code == PanelExit.ok, result.stderr
    assert _payload(result)["sessions"]["count"] == EARLY_SESSIONS
    lines = [line for line in result.stderr.splitlines() if line.startswith("FETCHING")]
    assert len(lines) == 2
    assert lines[0].startswith(f"FETCHING {ADJ_FACTOR_DATASET} 10/{EARLY_SESSIONS} sessions")
    assert lines[1].startswith(f"FETCHING {ADJ_FACTOR_DATASET} 11/{EARLY_SESSIONS} sessions")
    assert "eta=" in lines[0]


def test_a_store_whose_siblings_already_disagree_is_not_offered_an_as_of_that_cannot_work(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remedy printed in a refusal is a promise, and this is the case where it cannot be kept.

    A store written before this guard existed can already hold session-scoped partitions that
    stop on different days -- that is exactly the panel the e2e suite measured. No single
    `--as-of` reproduces all of them, so naming the oldest one's would hand the operator a
    command the *next* sibling refuses. Constructed here by disabling the guard for the two
    builds that create the split, which is the only way to reach a state the guard's whole
    purpose is to prevent.
    """
    _seeded(tmp_path)
    monkeypatch.setattr(cli, "_refuse_split_horizon", lambda *a, **k: None)
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--json").exit_code == PanelExit.ok
    _at(monkeypatch, LATE_CLOCK)
    assert _build(tmp_path, PRICE_LIMIT_DATASET, "--json").exit_code == PanelExit.ok
    monkeypatch.undo()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)
    _at(monkeypatch, LATE_CLOCK)

    # A third target at a third horizon. `price` rewrites daily/daily_basic/suspend_d and so
    # replaces neither stored partition, and an `--as-of` reaching 2026-01-16 agrees with
    # neither 2026-01-19 nor 2026-01-20 -- so both are in the refusal and no single instant
    # reproduces the pair. (The transport scripts no price dataset, which is the point: the
    # guard refuses before the first fetch, so it never gets asked.)
    result = _build(tmp_path, "price", "--as-of", "2026-01-19T12:00:00+08:00")

    assert result.exit_code == PanelExit.unhealthy
    assert "do not agree with each other either" in result.stderr
    assert "--as-of 20" not in result.stderr


def test_a_year_whose_last_halt_predates_the_horizon_is_not_a_split_horizon(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why `suspend_d` is excluded from `cli.SESSION_SCOPED_DATASETS`, asserted rather than
    asserted-in-a-comment.

    The other four publish on every open session, so their last covered date **is** the horizon
    the build that wrote them ran to. `suspend_d` does not: a session on which nothing was
    halted and nothing resumed serves zero rows (`cli._EMPTY_SESSION_IS_ORDINARY` says so one
    layer down), so its last covered date is a fact about the market. Including it would refuse
    every build of a year whose most recent halt is not on the newest session -- which is most
    years, most days.

    Constructed exactly that way: the scripted halt corpus carries one halt, three sessions
    before the horizon, and the build that follows must be accepted.
    """
    _seeded(tmp_path)
    price = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            "price",
            "--year",
            str(YEAR),
            "--runtime-dir",
            str(tmp_path),
            "--exchange",
            EXCHANGE,
            "--no-halts",
            "--json",
        ],
    )
    assert price.exit_code == PanelExit.ok, price.stderr
    stored = panel_store(tmp_path).read_coverage(SUSPENSION_DATASET, YEAR)
    assert stored is not None
    assert max(entry.event_date for entry in stored.dates) == HALT_SESSION
    assert SESSIONS[EARLY_SESSIONS - 1] > HALT_SESSION

    later = _build(tmp_path, ADJ_FACTOR_DATASET, "--json")

    assert later.exit_code == PanelExit.ok, later.stderr
    assert _payload(later)["sessions"]["count"] == EARLY_SESSIONS


# --- the horizon build runs to is the one doctor requires (`V2-P4-063`) -----------------------


def test_a_panel_built_after_the_close_is_clean_at_the_very_instant_that_built_it(
    tmp_path: Path, transport: HorizonTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `--as-of` through `panel build` and then through `panel doctor`, and it must be clean.

    `V2-P4-063` measured the product contradicting itself on its own output at its own instant:
    a build at `2026-02-10T09:00Z` produced a panel that a health check at the same literal
    instant called `BLOCKING ... date_gap`, exit 1. Two rules disagreed by one session.
    `panel_ingest._sessions_published_through` -- which every *read* of the price plane uses,
    which `_price_requirement` clamps `required_dates` at, and which `newest_published_session`
    resolves a shortlist's pricing session through -- says a session becomes knowable at
    `DAILY_AVAILABILITY_TIME` (16:30 Asia/Shanghai) on its own day. `cli._build_sessions`
    subtracted a day unconditionally.

    `CLOSE_CLOCK` is 17:00 Asia/Shanghai on 2026-01-20, an open session, thirty minutes after
    that session published. Every other clock in this module is noon, which is why none of them
    could separate the two rules: below 16:30 the two agree exactly, and the whole defect lives
    in the half-day above it.

    The measurement here reads `stk_limit` rather than `adj_factor` because `adj_factor` waives
    `required_dates` (`panel_doctor`'s own docstring calls it "the gap"), so a session missing
    from it is invisible to a health check -- an assertion on that dataset would be green under
    both rules and would separate nothing.
    """
    _at(monkeypatch, CLOSE_CLOCK)
    _seeded(tmp_path)

    built = _build(tmp_path, PRICE_LIMIT_DATASET, "--json")
    assert built.exit_code == PanelExit.ok, built.stderr
    assert _payload(built)["sessions"]["count"] == LATE_SESSIONS
    assert _payload(built)["sessions"]["last"] == CLOSE_SESSION.isoformat()

    report = runner.invoke(
        app,
        [
            "panel",
            "doctor",
            "--dataset",
            PRICE_LIMIT_DATASET,
            "--year",
            str(YEAR),
            "--runtime-dir",
            str(tmp_path),
            "--exchange",
            EXCHANGE,
            "--as-of",
            CLOSE_CLOCK.isoformat(),
            "--json",
        ],
    )

    assert report.exit_code == PanelExit.ok, report.stdout + report.stderr
    codes = [finding["code"] for finding in json.loads(report.stdout)["findings"]]
    assert "date_gap" not in codes, codes
