"""`panel build` across more than one year: naming them, refusing an ambiguous scope, resuming.

The P1 gate is `panel build --start 2015 --end 2026` **可完整跑通并断点续传**, and until
`V2-P1-019` none of that existed. `--year` was a single `int`, which Click resolves by keeping
the last value and discarding the rest without a word -- `--year 2025 --year 2026` printed
`WROTE trade_cal year=2026` and 2025 simply never happened. A dropped argument is
indistinguishable from an argument the caller never passed, so nothing downstream could tell the
two apart; that is the first thing this module pins.

The second is the range form, and the third is `--resume`, which is **year-granular and
evidence-based**: a target is skipped for a year when every session-scoped dataset it writes
already reaches the last session this build would fetch, which is what the write-time censuses
have already made a statement about the whole year. There is no progress file and no second
on-disk format. Intra-year resumption is deliberately not implemented and
`cli._resumable_targets` carries the argument.

Only the HTTP transport is doubled. `adj_factor` carries most of the scenarios -- one dataset,
session scoped, the cheapest thing in `cli.SESSION_SCOPED_DATASETS` to script across two years
-- and `price` carries the one that needs a target writing three datasets of which only two are
evidence.
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
from openalpha_cn.domain.price_limits import SUSPENSION_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel_view import panel_store
from openalpha_cn.providers.tushare import TUSHARE_CREDENTIAL_CODE

runner = CliRunner()

TOKEN = "sk-panel-years-token-must-not-leak-40219"
EXCHANGE = "SSE"
EARLIER, LATER = 2025, 2026
SECURITIES: tuple[str, ...] = ("000001.SZ", "600000.SH")

SESSIONS: Mapping[int, tuple[date, ...]] = {
    EARLIER: (date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)),
    LATER: (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
}
"""Three open sessions in each year, both in January so the whole of `EARLIER` is behind the
clock below and `LATER` is a year in progress -- the two shapes a range spans."""

WALL = datetime(2026, 1, 20, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2026-01-20. `_build_sessions` reaches 2025-12-31 for `EARLIER` and
2026-01-19 for `LATER`, so both years' scripted sessions are complete."""

CALENDAR_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
FACTOR_FIELDS = ["ts_code", "trade_date", "adj_factor"]
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

HALTS: Mapping[int, date] = {EARLIER: SESSIONS[EARLIER][0], LATER: SESSIONS[LATER][0]}
"""The one session of each year on which anything was halted -- the *first*, so `suspend_d`'s
last covered date is well behind the horizon. That is the ordinary shape of the dataset and the
reason it is excluded from `cli.SESSION_SCOPED_DATASETS` and from `--resume`'s evidence."""


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


class YearsTransport:
    """Every dataset the targets used here can ask for, over both scripted years.

    `suspend_d` serves a row on exactly one session of each year and nothing on the others,
    which is the ordinary shape of the dataset and what the `price` resume case needs.

    `blank` names sessions the `adj_factor` endpoint answers with zero rows, which is how this
    module produces a build that is refused **in its second year** without the first one being
    wrong. `rejects` names years the endpoint rejects outright, which is how it produces the
    same shape through the other exit out of the year loop.
    """

    def __init__(
        self, blank: frozenset[date] = frozenset(), rejects: frozenset[int] = frozenset()
    ) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.blank = blank
        self.rejects = rejects

    def asked(self, dataset: str) -> int:
        return sum(1 for payload in self.payloads if payload["api_name"] == dataset)

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api_name = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api_name == TRADING_CALENDAR_DATASET:
            year = int(params["start_date"][:4])
            open_days = set(SESSIONS.get(year, ()))
            items: list[list[Any]] = []
            previous: str | None = None
            day = date(year, 1, 1)
            while day <= date(year, 12, 31):
                items.append(
                    [params["exchange"], _compact(day), 1 if day in open_days else 0, previous]
                )
                if day in open_days:
                    previous = _compact(day)
                day += timedelta(days=1)
            return _response(CALENDAR_FIELDS, items)
        session = datetime.strptime(params["trade_date"], "%Y%m%d").date()
        if session.year in self.rejects:
            return {
                "code": TUSHARE_CREDENTIAL_CODE,
                "msg": "no permission for this interface",
                "data": None,
            }
        if api_name == ADJ_FACTOR_DATASET:
            if session in self.blank:
                return _response(FACTOR_FIELDS, [])
            return _response(FACTOR_FIELDS, [[code, _compact(session), 1.0] for code in SECURITIES])
        if api_name == SUSPENSION_DATASET:
            if HALTS.get(session.year) != session:
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
def transport(monkeypatch: pytest.MonkeyPatch) -> YearsTransport:
    scripted = YearsTransport()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL)
    return scripted


def _build(runtime_dir: Path, target: str, *extra: str) -> Any:
    return runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            target,
            "--runtime-dir",
            str(runtime_dir),
            "--exchange",
            EXCHANGE,
            *extra,
        ],
    )


def _payload(result: Any) -> Any:
    return json.loads(result.stdout)


def _calendars(runtime_dir: Path, *extra: str) -> None:
    result = _build(runtime_dir, TRADING_CALENDAR_DATASET, "--json", *extra)
    assert result.exit_code == PanelExit.ok, result.stderr


# --- the silent drop ---------------------------------------------------------------------------


def test_a_repeated_year_builds_every_year_it_names(
    tmp_path: Path, transport: YearsTransport
) -> None:
    """The defect, closed. Against `90beba8` this exact invocation printed one `WROTE` line for
    2026 and none for 2025, exit 0, with nothing anywhere saying a year had been discarded."""
    result = _build(
        tmp_path, TRADING_CALENDAR_DATASET, "--year", str(EARLIER), "--year", str(LATER), "--json"
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert _payload(result)["years"] == [EARLIER, LATER]
    assert [entry["year"] for entry in _payload(result)["partitions"]] == [EARLIER, LATER]
    assert panel_store(tmp_path).registered_years(TRADING_CALENDAR_DATASET) == (EARLIER, LATER)


def test_a_repeated_year_is_de_duplicated_rather_than_built_twice(
    tmp_path: Path, transport: YearsTransport
) -> None:
    """`--year 2026 --year 2026` is one year asked for twice, not two years. Building it twice
    would be a second fetch of the same sessions against a quota this command does not own."""
    result = _build(
        tmp_path, TRADING_CALENDAR_DATASET, "--year", str(LATER), "--year", str(LATER), "--json"
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert _payload(result)["years"] == [LATER]
    assert transport.asked(TRADING_CALENDAR_DATASET) == 1


def test_years_run_oldest_first_whatever_order_they_were_named_in(
    tmp_path: Path, transport: YearsTransport
) -> None:
    """Ascending order is what makes `--resume`'s "carry on from here" a single range rather
    than a list of holes, so it is a property of the command and not of the caller's typing."""
    result = _build(
        tmp_path, TRADING_CALENDAR_DATASET, "--year", str(LATER), "--year", str(EARLIER), "--json"
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert _payload(result)["years"] == [EARLIER, LATER]
    assert [entry["year"] for entry in _payload(result)["builds"]] == [EARLIER, LATER]


# --- the range form ----------------------------------------------------------------------------


def test_a_closed_range_builds_every_year_in_it(tmp_path: Path, transport: YearsTransport) -> None:
    """The gate's own sentence, in miniature: `--start`/`--end` rather than one flag per year."""
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))

    result = _build(
        tmp_path, ADJ_FACTOR_DATASET, "--start", str(EARLIER), "--end", str(LATER), "--json"
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert _payload(result)["years"] == [EARLIER, LATER]
    assert {entry["year"] for entry in _payload(result)["partitions"]} == {EARLIER, LATER}
    assert _payload(result)["sessions"]["count"] == len(SESSIONS[EARLIER]) + len(SESSIONS[LATER])
    assert _payload(result)["sessions"]["first"] == SESSIONS[EARLIER][0].isoformat()
    assert _payload(result)["sessions"]["last"] == SESSIONS[LATER][-1].isoformat()


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (("--year", "2026", "--start", "2015", "--end", "2026"), "cannot be scoped two ways"),
        ((), "no year was given"),
        (("--start", "2015"), "--start and --end are a closed range"),
        (("--end", "2026"), "--start and --end are a closed range"),
        (("--start", "2026", "--end", "2015"), "is after --end"),
        (("--year", "99999"), "not a year this calendar can represent"),
    ],
)
def test_an_ambiguous_or_impossible_scope_is_refused_before_anything_is_fetched(
    tmp_path: Path, transport: YearsTransport, extra: tuple[str, ...], expected: str
) -> None:
    """Six ways to name no years, or two sets of them, and none is resolved by guessing.

    The last case is the one a caller reaches by typo rather than by ambiguity: `datetime`
    cannot represent year 99999, so without this it surfaced as an unhandled `ValueError` and
    `_panel_command` reported `internal_error` -- a defect in the command, about an argument.
    """
    result = _build(tmp_path, TRADING_CALENDAR_DATASET, *extra)

    assert result.exit_code == PanelExit.bad_request, result.stdout
    assert expected in result.stderr
    assert transport.payloads == []


# --- resuming ----------------------------------------------------------------------------------


def test_resume_skips_a_year_the_store_already_covers_and_fetches_the_one_it_does_not(
    tmp_path: Path, transport: YearsTransport
) -> None:
    """The gate's second clause, at the granularity this command can honestly offer it.

    `EARLIER` is built alone, then the same range is asked for with `--resume`: the sessions of
    `EARLIER` are not fetched again and the sessions of `LATER` are. The evidence is the request
    count, not the report -- a `--resume` that skipped the *writes* while still spending the
    quota would satisfy every assertion about partitions and none of the reason the flag exists.
    """
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--year", str(EARLIER), "--json").exit_code == 0
    fetched_before = transport.asked(ADJ_FACTOR_DATASET)
    assert fetched_before == len(SESSIONS[EARLIER])

    result = _build(
        tmp_path,
        ADJ_FACTOR_DATASET,
        "--start",
        str(EARLIER),
        "--end",
        str(LATER),
        "--resume",
        "--json",
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert transport.asked(ADJ_FACTOR_DATASET) - fetched_before == len(SESSIONS[LATER])
    builds = {entry["year"]: entry for entry in _payload(result)["builds"]}
    assert builds[EARLIER]["resumed"] == [ADJ_FACTOR_DATASET]
    assert builds[LATER]["resumed"] == []
    assert panel_store(tmp_path).registered_years(ADJ_FACTOR_DATASET) == (EARLIER, LATER)


def test_without_resume_the_same_range_fetches_the_year_it_already_holds(
    tmp_path: Path, transport: YearsTransport
) -> None:
    """The premise the test above rests on, and the reason `--resume` is opt-in: this command's
    ordinary job is to replace what is there, and a rebuild that quietly fetched nothing would
    be the wrong default for it."""
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))
    assert _build(tmp_path, ADJ_FACTOR_DATASET, "--year", str(EARLIER), "--json").exit_code == 0
    fetched_before = transport.asked(ADJ_FACTOR_DATASET)

    result = _build(
        tmp_path, ADJ_FACTOR_DATASET, "--start", str(EARLIER), "--end", str(LATER), "--json"
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert transport.asked(ADJ_FACTOR_DATASET) - fetched_before == (
        len(SESSIONS[EARLIER]) + len(SESSIONS[LATER])
    )


def test_a_year_stored_short_of_this_builds_horizon_is_not_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half of `--resume` that decides what "already covered" means.

    The stored partition holds two of `LATER`'s three sessions -- built at a clock a day earlier
    -- so its census is a strict subset of what this build would fetch. Skipping it would leave
    a year permanently one session short while every report said the range was done, which is
    precisely the "looks complete and is not" failure a resume cache invites. The comparison is
    equality against the census, so it is refused.
    """
    scripted = YearsTransport()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: datetime(2026, 1, 7, 4, 0, tzinfo=UTC))
    _calendars(tmp_path, "--year", str(LATER))
    short = _build(tmp_path, ADJ_FACTOR_DATASET, "--year", str(LATER), "--json")
    assert short.exit_code == PanelExit.ok, short.stderr
    assert json.loads(short.stdout)["sessions"]["count"] == 2
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL)
    fetched_before = scripted.asked(ADJ_FACTOR_DATASET)

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--year", str(LATER), "--resume", "--json")

    assert result.exit_code == PanelExit.ok, result.stderr
    assert scripted.asked(ADJ_FACTOR_DATASET) - fetched_before == len(SESSIONS[LATER])
    assert json.loads(result.stdout)["builds"][0]["resumed"] == []


def test_the_price_target_resumes_on_its_bars_rather_than_on_its_halt_corpus(
    tmp_path: Path, transport: YearsTransport
) -> None:
    """`price` is the expensive target and the one `--resume` exists for -- ~35 minutes a year
    against `adj_factor`'s ~6 -- so its skip decision is the one that has to be right.

    It writes three datasets and only two of them are evidence. `suspend_d` cannot be: a session
    on which nothing was halted serves zero rows, so its last covered date is a fact about the
    market, and requiring it to reach the horizon would refuse to resume most years of most
    panels. Scripted exactly that way -- the only halt of each year is on its *first* session --
    and the year is still recognised as complete. A complete `daily` partition is the stronger
    witness anyway: `write_daily_panel` refuses a session whose missing bars nothing accounts
    for, so it exists only because the corpus was read.
    """
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))
    assert _build(tmp_path, "price", "--year", str(EARLIER), "--json").exit_code == 0
    store = panel_store(tmp_path)
    halts = store.read_coverage(SUSPENSION_DATASET, EARLIER)
    assert halts is not None
    assert max(entry.event_date for entry in halts.dates) == HALTS[EARLIER]
    assert HALTS[EARLIER] < SESSIONS[EARLIER][-1]
    fetched_before = transport.asked(DAILY_DATASET)

    result = _build(
        tmp_path, "price", "--start", str(EARLIER), "--end", str(LATER), "--resume", "--json"
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert transport.asked(DAILY_DATASET) - fetched_before == len(SESSIONS[LATER])
    builds = {entry["year"]: entry for entry in _payload(result)["builds"]}
    assert builds[EARLIER]["resumed"] == ["price"]
    assert builds[EARLIER]["halts"] == "resumed"
    assert builds[LATER]["halts"] == "corroborated"
    assert _payload(result)["halts"] == "mixed"


def test_resume_never_skips_the_two_targets_that_cost_one_request_each(
    tmp_path: Path, transport: YearsTransport
) -> None:
    """`trade_cal` is one request a year and `stock_basic` is one request full stop, so
    skipping either would save nothing measurable while making a resumed build read a calendar
    it did not verify -- and the calendar is what every other target's session census, horizon
    and date-gap check is measured against.

    It is also the one exclusion `--resume`'s own shape does not enforce for free: neither
    dataset is in `cli.SESSION_SCOPED_DATASETS`, so the `all(...)` over the evidence is
    vacuously true for both and they would be skipped *every* time if the candidate set were
    not restricted first. Asserted on the request count, because the report of a skipped
    calendar and the report of a re-fetched one differ only in a field nobody reads.
    """
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))
    fetched_before = transport.asked(TRADING_CALENDAR_DATASET)
    assert fetched_before == 2

    result = _build(
        tmp_path,
        TRADING_CALENDAR_DATASET,
        "--start",
        str(EARLIER),
        "--end",
        str(LATER),
        "--resume",
        "--json",
    )

    assert result.exit_code == PanelExit.ok, result.stderr
    assert transport.asked(TRADING_CALENDAR_DATASET) - fetched_before == 2
    assert [entry["resumed"] for entry in _payload(result)["builds"]] == [[], []]
    assert {entry["year"] for entry in _payload(result)["partitions"]} == {EARLIER, LATER}


def test_a_range_refused_in_its_second_year_names_the_years_that_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build across twelve years that dies in the fourth has eight left, and "re-run it" is
    not a remedy when the first three cost hours.

    The refusal already named the partitions on disk (`cli._stored_so_far`); it now also names
    which years got through and the exact `--start/--end/--resume` that carries on. Constructed
    by blanking one of `LATER`'s sessions, so `EARLIER` is correct and complete and the second
    year is refused by its own writer.
    """
    scripted = YearsTransport(blank=frozenset({SESSIONS[LATER][1]}))
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL)
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--start", str(EARLIER), "--end", str(LATER))

    assert result.exit_code == PanelExit.unhealthy, result.stdout
    assert f"Years [{EARLIER}] finished; {LATER} is the one that stopped" in result.stderr
    assert f"--start {LATER} --end {LATER} --resume" in result.stderr
    # `_stored_so_far` reaches back across the years too: the year that finished is on disk and
    # the `written` mapping of the year that stopped is empty, so a sentence built from that
    # mapping alone would say nothing was written.
    assert f"{ADJ_FACTOR_DATASET}:{EARLIER}(" in result.stderr
    assert panel_store(tmp_path).registered_years(ADJ_FACTOR_DATASET) == (EARLIER,)


def test_a_provider_failure_in_a_later_year_still_names_what_the_earlier_ones_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other exit out of the year loop, and the one a long build actually meets.

    A writer's refusal is `_PANEL_WRITE_REFUSALS`; everything else -- a rejected credential, a
    quota that outlasted the retries, a transport that never came back -- leaves through
    `panel_build`'s bare `except`, which echoes the same two sentences on stderr and re-raises
    untouched. Both had to learn to look past the current year, and only one of them is on the
    path the test above takes. Driven with Tushare's measured credential rejection (`code`
    40101), which `_rejection_semantics` classifies `authentication` and does **not** retry, so
    the build stops in `LATER` rather than backing off four times first.
    """
    scripted = YearsTransport(rejects=frozenset({LATER}))
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL)
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--start", str(EARLIER), "--end", str(LATER))

    assert result.exit_code == PanelExit.provider_failure, result.stdout
    assert f"{ADJ_FACTOR_DATASET}:{EARLIER}(" in result.stderr
    assert f"Years [{EARLIER}] finished; {LATER} is the one that stopped" in result.stderr
    assert TOKEN not in result.stderr
    assert panel_store(tmp_path).registered_years(ADJ_FACTOR_DATASET) == (EARLIER,)


def test_a_range_refused_in_its_first_year_does_not_claim_a_list_of_finished_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Years [] finished` is a true sentence and a bad one. The range is still offered -- it is
    the same range this build was given, which is exactly the right thing to re-run."""
    scripted = YearsTransport(blank=frozenset({SESSIONS[EARLIER][1]}))
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL)
    _calendars(tmp_path, "--start", str(EARLIER), "--end", str(LATER))

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--start", str(EARLIER), "--end", str(LATER))

    assert result.exit_code == PanelExit.unhealthy, result.stdout
    assert "finished" not in result.stderr
    assert f"{EARLIER} is the one that stopped" in result.stderr
    assert f"--start {EARLIER} --end {LATER} --resume" in result.stderr
    assert panel_store(tmp_path).registered_years(ADJ_FACTOR_DATASET) == ()


def test_a_single_year_refusal_does_not_offer_a_range_it_has_nothing_to_carry_on_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of `_years_left`: one year has no years left, and a sentence that said so
    anyway would be noise on every ordinary refusal this command already prints."""
    scripted = YearsTransport(blank=frozenset({SESSIONS[LATER][1]}))
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL)
    _calendars(tmp_path, "--year", str(LATER))

    result = _build(tmp_path, ADJ_FACTOR_DATASET, "--year", str(LATER))

    assert result.exit_code == PanelExit.unhealthy, result.stdout
    assert "finished;" not in result.stderr
    assert "--resume" not in result.stderr
