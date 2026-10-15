"""`scripts/daily_selection.py --morning-recheck`: the survivor day's morning re-check, end to end
(`V2-P7-008`, task D6).

Before the next call auction the command reads again the one thing that can change overnight and
matters for tomorrow's open: the name corpus -- a new ST or delisting-risk announcement published
after the evening's read. It is advice only (decision, user-adopted): the paper book follows the
evening's plan, and a divergence is recorded in `<session>.morning.json` and printed, never
committed to the book. It writes that file and the `namechange` partition, nothing else.

The evenings it re-checks are run for real, once per module, through `main` on the survivor day's
own scripted market (`test_daily_selection_survivor_day.py`: 120 main-board securities over every
weekday of 2026, the panel seeded through the real `panel build`) -- the first evening (6 April,
ten planned buys, no holding), the second (7 April, ten holdings, ten watch-list names, its pinned
`held` still empty) and the third (8 April, its pinned `held` the ten names, one of them halted
that session) -- and each test works on a copy of the runtime directory, so the evenings are paid
for once. The overnight announcement is the market's: `Overnight` serves a rename that was
announced on the evening and published only the next morning, so the evening's read does not have
it and the morning's does -- and one announced on the morning itself, dated after the session,
which the morning's fetch brings and its facts read never screens (a notice). Nothing reaches the
network; every request is the scripted market's, counted.

**The listing-row class cannot be made by the morning's own fetch.** A code priced before its
registry listing row has a row aged 0 at an evening read and none at a later one -- but the morning
fetches `namechange` alone, and the evening's `stock_basic` fetch drops a row dated after its own
clock when it writes (measured: the stored 2026 partition's newest availability is its January one),
so the store the morning reads holds no such row. The class is therefore made here by the facts
adapter's answer, which the one test of it wraps (the adapter's own pair of reads of the class is
`test_strategy_survivor_morning.py`'s, on a panel written with the row), and the rest is the
command's plumbing around it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as sd
from daily_market import Market, compact

from openalpha_cn import cli, strategy_survivor_book, strategy_survivor_morning
from openalpha_cn.backtest.paper_state import held_head
from openalpha_cn.domain.survivor_book import SecurityFacts
from openalpha_cn.storage.paper_state import FilePaperStateStore
from openalpha_cn.strategy_survivor_facts import FactsDay

daily = tds.daily
ROOT: Final[Path] = Path(__file__).resolve().parents[3]

DAY: Final[date] = sd.DAY
"""6 April 2026, the first evening after the epoch: ten buys planned, nothing held."""
NEXT: Final[date] = sd.NEXT
"""7 April: the first evening's buys filled at its open; ten holdings, a watch list of ten."""
THIRD: Final[date] = sd.THIRD
"""8 April: the evening that pins the ten holdings the book came into it with."""
SUFFIX: Final[str] = ".morning.json"
NO_REQUEST: Final[str] = "tushare requests   0 this run {}"
UNSCOPED: Final[str] = "no overnight change"
"""The advice line before review Important 2, which an announcement dated after the session made
false: it must never be printed again."""
LAST_OF_2026: Final[date] = date(2026, 12, 31)
FIRST_OF_2027: Final[date] = sd.FIRST_2027
"""4 January 2027: the first session after the new year, the next one after 31 December."""


def no_change(day: date) -> str:
    """The advice line of a morning that found no difference from `day`'s plan: its scope is in
    the line itself (review Important 2), and the plan is named by its session -- at 08:30 it is
    not "tonight's" (whole-branch review M4)."""
    return (
        f"{'advice':<18} no change to the plan of {day.isoformat()} among announcements dated "
        f"through {day.isoformat()}"
    )


# --- the markets ----------------------------------------------------------------------------------


@dataclass
class Overnight(sd.SurvivorMarket):
    """The survivor market with renames announced on an evening and published the next morning.

    `renames` are `(code, announced, effective)`: the code is renamed `*ST<code>` from `effective`,
    announced on `announced` -- but the corpus carries the rename only once `today` is past
    `announced`, as an upstream that publishes late does. The evening's fetch (today = the
    announcement day) has the code's one standing name; the morning's has it closed on the
    announcement day and the ST name opened on `effective`. `early` are the same, published on
    the announcement day itself (once `today` is that day): a rename announced before the open of
    the morning, which is dated after the evening's session. `late` are `(code, announced,
    effective, published)`: announced on `announced` and published only once `today` reaches
    `published` -- the whole-branch review's ST announcement dated on a Friday and published on
    the Sunday (C1)."""

    renames: tuple[tuple[str, date, date], ...] = ()
    early: tuple[tuple[str, date, date], ...] = ()
    late: tuple[tuple[str, date, date, date], ...] = ()

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        today = self.today or self.open_days[-1]
        published = (
            *(item for item in self.renames if today > item[1]),
            *(item for item in self.early if today >= item[1]),
            *((code, on, effective) for code, on, effective, out in self.late if today >= out),
        )
        found: list[list[Any]] = []
        for row in super()._names(params):
            code = row[0]
            rename = next((item for item in published if item[0] == code), None)
            if rename is None:
                found.append(row)
                continue
            _code, announced, effective = rename
            found.append([code, code, row[2], compact(announced), row[2], "改名"])
            found.append([code, f"*ST{code}", compact(effective), None, compact(announced), "ST"])
        return found


@dataclass
class PricedBeforeListing(sd.SurvivorMarket):
    """The survivor market, with the registry dating one code's listing the day after the evening
    that prices it -- T3 3.2's shape, a bar before the registered listing date. The registry row
    is published at that day's midnight, so the evening has never heard of the code (a row aged 0,
    counted in `no_listing_row`)."""

    late: str = sd.SECURITIES[7]
    listed_on: date = NEXT

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        answer = super().post(payload)
        if payload["api_name"] == "stock_basic":
            for item in answer["data"]["items"]:
                if item[0] == self.late:
                    item[5] = compact(self.listed_on)
        return answer


# --- the evenings, once per module ----------------------------------------------------------------


def _seed(runtime: Path, market: Market, builds: Sequence[tuple[int, Sequence[str], str]]) -> None:
    """The market's panel through the real `panel build`: `(year, targets, as_of)` per build, with
    the scripted transport and the tests' wall clock bound for the duration."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        for year, targets, as_of in builds:
            arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(year)]
            arguments += ["--as-of", as_of, "--exchange", "SSE", "--json"]
            for target in targets:
                arguments += ["--dataset", target]
            built = daily.invoke(arguments)
            assert built.exit_code == 0, built.reason()


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The survivor market's panel through 3 April, built once
    (`test_daily_selection_survivor_day.seeded`, whose scope this module does not share)."""
    runtime = tmp_path_factory.mktemp("morning-seed") / "runtime"
    market = sd.SurvivorMarket(open_days=tds.OPEN_2026)
    market.today = datetime.fromisoformat(sd.SEEDED_AS_OF).date()
    _seed(runtime, market, [(2026, sd.SEED_TARGETS, sd.SEEDED_AS_OF)])
    return runtime


class Source(NamedTuple):
    """A committed registration and a runtime directory an evening left: what a test copies."""

    repo: Path
    registration: Path
    runtime: Path


def _evening(world: tds.World, day: date, *, at: datetime | None = None) -> None:
    """`day`'s evening through `main`, pinned at its 18:30 (or `at`) on a clock five minutes
    later, output swallowed: the run must succeed."""
    pinned = at or sd.evening_of(day)
    world.market.today = pinned.astimezone(daily.SHANGHAI).date()
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", pinned.isoformat(), "--json"]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = daily.main(arguments, clock=lambda: pinned + timedelta(minutes=5))
    assert code == 0, f"the evening of {day} failed"


class Evenings(NamedTuple):
    """The runtime directory after each of the first three evenings of one registration, and the
    holding halted on the third."""

    first: Source
    second: Source
    third: Source
    halted: str


@pytest.fixture(scope="module")
def evenings(seeded: Path, tmp_path_factory: pytest.TempPathFactory) -> Evenings:
    base = tmp_path_factory.mktemp("morning-evenings")
    with pytest.MonkeyPatch.context() as patch:
        repo, registration = tds._repository(base, sd.CONFIG)
        market = sd.Halting(open_days=tds.OPEN_2026)
        tds._bind(patch, repo, market)
        runtime = base / "runtime"
        shutil.copytree(seeded, runtime)
        world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
        _evening(world, DAY)
        first = base / "after-first"
        shutil.copytree(runtime, first)
        _evening(world, NEXT)
        second = base / "after-second"
        shutil.copytree(runtime, second)
        halted = sd._journal(world, NEXT)["result"]["paper"]["book"]["holdings"][0]["ts_code"]
        market.halted = {THIRD: (halted,)}
        _evening(world, THIRD)
    return Evenings(
        Source(repo, registration, first),
        Source(repo, registration, second),
        Source(repo, registration, runtime),
        halted,
    )


class Listing(NamedTuple):
    """The runtime directory after the evening of a market with a code priced before its
    registry listing row, and that code."""

    source: Source
    late: str


@pytest.fixture(scope="module")
def listing_evening(tmp_path_factory: pytest.TempPathFactory) -> Listing:
    base = tmp_path_factory.mktemp("morning-listing")
    market = PricedBeforeListing(open_days=tds.OPEN_2026)
    market.today = datetime.fromisoformat(sd.SEEDED_AS_OF).date()
    runtime = base / "runtime"
    _seed(runtime, market, [(2026, sd.SEED_TARGETS, sd.SEEDED_AS_OF)])
    with pytest.MonkeyPatch.context() as patch:
        repo, registration = tds._repository(base, sd.CONFIG)
        tds._bind(patch, repo, market)
        world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
        _evening(world, DAY)
    return Listing(Source(repo, registration, runtime), market.late)


@pytest.fixture(scope="module")
def new_year_evening(tmp_path_factory: pytest.TempPathFactory) -> Source:
    """The runtime directory after the evening of 31 December 2026, the last session of the year,
    with 2027's calendar stored as that evening leaves it."""
    base = tmp_path_factory.mktemp("morning-new-year")
    market = sd.NewYearMarket(open_days=sd.NEW_YEAR_DAYS)
    market.today = LAST_OF_2026
    runtime = base / "runtime"
    _seed(
        runtime,
        market,
        [
            (2026, (*sd.SEED_TARGETS, "namechange"), "2026-12-31T12:00:00+08:00"),
            (2027, ("trade_cal",), "2027-01-04T12:00:00+08:00"),
        ],
    )
    config = sd.survivor_config(epoch=date(2026, 12, 30))
    with pytest.MonkeyPatch.context() as patch:
        repo, registration = tds._repository(base, config)
        tds._bind(patch, repo, market)
        world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
        _evening(world, LAST_OF_2026)
    return Source(repo, registration, runtime)


FRIDAY: Final[date] = date(2026, 4, 10)
"""The evening of the whole-branch review's C1: its plan is for the open of Monday 13 April."""
SATURDAY: Final[date] = date(2026, 4, 11)
SUNDAY: Final[date] = date(2026, 4, 12)
MONDAY: Final[date] = date(2026, 4, 13)


@pytest.fixture(scope="module")
def friday_evening(seeded: Path, tmp_path_factory: pytest.TempPathFactory) -> Source:
    """The runtime directory after the evening of Friday 10 April, the first after an epoch of
    Thursday 9 April: ten buys planned for Monday's open, nothing held. Its panel update fetches
    the sessions since the seed (6 to 10 April) -- within the registered bound."""
    base = tmp_path_factory.mktemp("morning-friday")
    with pytest.MonkeyPatch.context() as patch:
        repo, registration = tds._repository(base, sd.survivor_config(epoch=date(2026, 4, 9)))
        market = sd.SurvivorMarket(open_days=tds.OPEN_2026)
        tds._bind(patch, repo, market)
        runtime = base / "runtime"
        shutil.copytree(seeded, runtime)
        world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
        _evening(world, FRIDAY)
    return Source(repo, registration, runtime)


def _world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Source,
    market: Market | None = None,
) -> tds.World:
    """A copy of the source's runtime directory, its registration, and `market` (the overnight
    market unless given) as the panel's transport."""
    market = market or Overnight(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, source.repo, market)
    copy = tmp_path / "runtime"
    shutil.copytree(source.runtime, copy)
    return tds.World(
        repo=source.repo, registration=source.registration, runtime=copy, market=market
    )


# --- running the morning --------------------------------------------------------------------------


def morning_of(day: date, at: time = time(8, 30)) -> datetime:
    """The morning after `day`'s evening, `at` Shanghai time (08:30 unless given)."""
    return datetime.combine(day + timedelta(days=1), at, tzinfo=daily.SHANGHAI)


def _arguments(runtime: Path, registration: Path, repo: Path, at: datetime) -> list[str]:
    arguments = ["--runtime-dir", str(runtime), "--registration", str(registration)]
    return [*arguments, "--repo", str(repo), "--as-of", at.isoformat()]


def _morning(
    world: tds.World,
    capsys: pytest.CaptureFixture[str],
    day: date,
    *,
    as_of: datetime | None = None,
    clock: datetime | None = None,
    json_output: bool = True,
    extra: Sequence[str] = (),
) -> tuple[int, str, list[str]]:
    """The morning re-check of `day`'s evening through `main`: pinned at `as_of` (08:30 the next
    day unless given) on a clock five minutes later unless given. Returns the exit code, stdout
    and stderr's lines."""
    at = as_of or morning_of(day)
    world.market.today = at.astimezone(daily.SHANGHAI).date()
    flags = ["--morning-recheck", *extra, *(["--json"] if json_output else [])]
    arguments = _arguments(world.runtime, world.registration, world.repo, at)
    code = daily.main([*arguments, *flags], clock=lambda: clock or at + timedelta(minutes=5))
    out, err = capsys.readouterr()
    assert tds.SECRET_TOKEN not in out + err
    return code, out, err.splitlines()


def _morning_path(world: tds.World, day: date) -> Path:
    """`day`'s first morning check: `<session>.morning.json`."""
    return sd._journal_path(world, day).with_name(f"{day.isoformat()}{SUFFIX}")


def _generation_path(world: tds.World, day: date, read_on: date) -> Path:
    """`day`'s check read on a later calendar date than its first: `<session>.morning.<date>.json`
    beside the first (whole-branch review C1)."""
    return sd._journal_path(world, day).with_name(f"{day.isoformat()}.morning.{read_on}.json")


def _shanghai(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=daily.SHANGHAI)


def _checks(world: tds.World, day: date) -> list[str]:
    """The names of `day`'s morning files, ascending."""
    directory = sd._journal_path(world, day).parent
    return sorted(path.name for path in directory.glob(f"{day.isoformat()}.morning*"))


def _filed(world: tds.World, day: date) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(_morning_path(world, day).read_text(encoding="utf-8"))
    return body


def _relative(world: tds.World, path: Path) -> str:
    return path.relative_to(world.runtime).as_posix()


def _changed(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """Every file new, gone or with other bytes between two `_files` maps, ascending."""
    return sorted(path for path in {*before, *after} if before.get(path) != after.get(path))


def _under(files: Mapping[str, str], directory: str) -> dict[str, str]:
    return {path: digest for path, digest in files.items() if path.startswith(f"{directory}/")}


def _plan(world: tds.World, day: date) -> dict[str, Any]:
    result: dict[str, Any] = sd._journal(world, day)["result"]
    return result


def _bought(world: tds.World, day: date = DAY) -> list[str]:
    """The codes of the buys `day`'s evening planned tonight, in the plan's order."""
    orders = _plan(world, day)["paper"]["open_orders"]
    return [order["ts_code"] for order in orders if not order["carried"]]


def _holdings(world: tds.World, day: date = NEXT) -> list[str]:
    return [item["ts_code"] for item in _plan(world, day)["paper"]["book"]["holdings"]]


def _texts(document: Mapping[str, Any]) -> list[str]:
    return [item["text"] for item in document["differences"]]


def _reads(monkeypatch: pytest.MonkeyPatch, world: tds.World) -> list[dict[str, Any]]:
    """Every facts-day read of the paper book's adapter from here on, by its keywords and the
    number of requests the market had seen when it was made; the read goes through."""
    original = strategy_survivor_book.facts_day
    calls: list[dict[str, Any]] = []

    def recorded(**keywords: Any) -> Any:
        calls.append({**keywords, "requests_so_far": len(world.market.requests)})
        return original(**keywords)

    monkeypatch.setattr(strategy_survivor_book, "facts_day", recorded)
    return calls


def _without_requests(lines: Sequence[str]) -> list[str]:
    return [line for line in lines if not line.startswith("tushare requests")]


# --- what the overnight announcement changes -----------------------------------------------------


def test_a_planned_buy_that_turned_special_treatment_overnight_is_not_to_be_placed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """The evening of 6 April plans ten buys. An ST rename of one of them is announced that evening
    and published only the next morning: the morning's fetch has it -- one request -- and the
    advice says not to place that buy, and only that one. It is printed with its wording
    ("candidate", "no order was placed") and filed in the morning file, with the pin the check was
    read under."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    named, kept = _bought(world)[:2]
    world.market.renames = ((named, DAY, NEXT),)
    snapshot = _plan(world, DAY)["paper"]["snapshot_id"]

    code, out, err = _morning(world, capsys, DAY, json_output=False)

    assert code == 0, err
    assert err == []
    assert world.market.payloads == ["namechange"]
    lines = out.splitlines()
    assert f"  do not place BUY {named}: special_treatment" in lines
    assert not any(line.startswith("  do not place BUY") and kept in line for line in lines)
    document = _filed(world, DAY)
    assert (
        f"{'advice':<18} {len(document['differences'])} difference(s) from the plan of "
        f"{DAY.isoformat()} among announcements dated through {DAY.isoformat()}"
    ) in lines
    assert not any(UNSCOPED in line for line in lines)
    assert any(line.startswith("tushare requests   1 this run") for line in lines)
    wording = next(line for line in lines if line.startswith("wording"))
    assert "candidate" in wording and "no order was placed" in wording
    assert document["schema"] == "openalpha-daily-survivor-morning/v1"
    assert document["session"] == DAY.isoformat()
    assert document["as_of"] == morning_of(DAY).isoformat()
    assert document["held"] == sd._journal(world, DAY)["held"] == []
    assert document["evening_snapshot_id"] == snapshot
    assert document["requests"] == {"namechange": 1}
    assert document["tolerated"] == []
    # The rename is dated on the session itself: screened, so advice -- never also a notice.
    assert document["notices"] == []
    assert f"do not place BUY {named}: special_treatment" in _texts(document)


def test_a_holding_that_turned_special_treatment_overnight_is_sold_at_the_next_evening_not_now(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """The evening of 7 April holds ten names. One is renamed ST overnight: the advice says it
    turned, and that the paper book sells it at the next evening -- and the book is not changed:
    its snapshots, ledger and marker are the bytes the evening left, the head is still the
    evening's snapshot, and the evening's journal is as it was filed."""
    world = _world(tmp_path, monkeypatch, evenings.second)
    turned = _holdings(world)[3]
    world.market.renames = ((turned, NEXT, NEXT + timedelta(days=1)),)
    paper = _under(sd._files(world.runtime), "paper")
    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None and len(head.book.positions) == 10
    journal = sd._journal(world, NEXT)

    code, out, err = _morning(world, capsys, NEXT)

    assert code == 0, err
    document = json.loads(out)
    assert (
        f"holding {turned} turned special_treatment: the paper book sells it at the next evening"
        in _texts(document)
    )
    assert [
        item["ts_codes"] for item in document["differences"] if item["kind"] == "holding_turned"
    ] == [[turned]]
    # The file's `held` is the evening's pin, before its settlement; the holdings compared are the
    # book after it (review Minor 7).
    assert document["held"] == journal["held"] == []
    assert document["holdings_compared"] == sorted(_holdings(world)) and turned in _holdings(world)
    assert _under(sd._files(world.runtime), "paper") == paper
    after = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert after is not None and after.snapshot_id == head.snapshot_id
    assert sd._journal(world, NEXT) == journal


def test_nothing_announced_overnight_is_no_change_among_the_announcements_screened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """Nothing renamed: the morning's fetch brings the corpus the evening read, and the advice says
    so plainly -- with its scope, the announcements dated through the session, in the line."""
    world = _world(tmp_path, monkeypatch, evenings.first)

    code, out, err = _morning(world, capsys, DAY, json_output=False)

    assert code == 0, err
    lines = out.splitlines()
    assert no_change(DAY) in lines
    assert not any(UNSCOPED in line for line in lines)
    document = _filed(world, DAY)
    assert document["differences"] == [] and document["tolerated"] == []
    assert document["notices"] == []
    assert document["requests"] == {"namechange": 1}
    assert world.market.payloads == ["namechange"]


def test_an_st_rename_announced_after_the_session_is_a_notice_and_changes_no_advice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """The reviewer's probe (review Important 2): an ST rename of a planned buy announced on the
    morning after the evening -- dated after the session, published before the open. The morning's
    one request brings it into the stored corpus, and the facts adapter, which screens what was
    announced by the session, does not see it: the advice is the same as with no rename, its line
    says how far it reaches, and the rename is listed under "announced after the session, not
    screened" -- a notice, never advice, never an order."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    named = _bought(world)[0]
    world.market.early = ((named, NEXT, NEXT + timedelta(days=1)),)

    code, out, err = _morning(world, capsys, DAY, json_output=False)

    assert code == 0, err
    assert world.market.payloads == ["namechange"]
    lines = out.splitlines()
    assert no_change(DAY) in lines
    assert not any(UNSCOPED in line or "do not place" in line for line in lines)
    start = next(index for index, line in enumerate(lines) if line.startswith("notices"))
    assert f"announced after {DAY.isoformat()}" in lines[start]
    assert "not screened" in lines[start] and "not advice" in lines[start]
    notice = f"{named} *ST{named} {NEXT.isoformat()} ST"
    assert lines[start + 1] == f"  {notice}"
    for line in lines[start : start + 2]:
        assert not any(word in line for word in ("BUY", "SELL", "place", "order")), line
    document = _filed(world, DAY)
    assert document["differences"] == [] and document["tolerated"] == []
    assert document["notices"] == [
        {
            "ts_code": named,
            "name": f"*ST{named}",
            "ann_date": NEXT.isoformat(),
            "change_reason": "ST",
            "text": notice,
        }
    ]


def test_a_name_outside_the_plan_that_turned_special_treatment_moves_only_the_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """A security the evening's plan never named turns ST overnight: no buy is withdrawn and no
    holding is told about -- the screen's counts moved, and that is information, printed on a line
    of its own and never counted as a difference (whole-branch review M4)."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    outsider = next(code for code in sd.SECURITIES if code not in _bought(world))
    world.market.renames = ((outsider, DAY, NEXT),)

    code, out, err = _morning(world, capsys, DAY, json_output=False)

    assert code == 0, err
    document = _filed(world, DAY)
    assert {item["kind"] for item in document["differences"]} <= {"watch_list_changed"}
    assert any(item.startswith("ST ") for item in document["counts_changed"])
    lines = out.splitlines()
    (counts,) = (line for line in lines if line.startswith("counts "))
    assert "information" in counts and "not advice" in counts
    if not document["differences"]:
        assert no_change(DAY) in lines


# --- the one benign class -------------------------------------------------------------------------


def _without_row(facts: FactsDay, code: str) -> FactsDay:
    """`facts` as a read after the registry's row for `code` is published reads it: no row, and the
    code counted in `not_listed_with_bar` where it had been counted in `no_listing_row`."""
    row: SecurityFacts = next(item for item in facts.facts if item.ts_code == code)
    rows = tuple(item for item in facts.facts if item.ts_code != code)
    scored = row.board in facts.boards and None not in row.oriented_values and not row.suspended
    return dataclasses.replace(
        facts,
        facts=rows,
        universe_size=facts.universe_size - (row.board in facts.boards),
        scored_size=facts.scored_size - scored,
        notes=dataclasses.replace(
            facts.notes,
            no_listing_row=facts.notes.no_listing_row - 1,
            not_listed_with_bar=facts.notes.not_listed_with_bar + 1,
        ),
    )


def test_a_code_priced_before_its_listing_row_is_tolerated_never_advice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    listing_evening: Listing,
) -> None:
    """The evening gave the code a row aged 0; a morning read after the registry's row is published
    has none, and counts it in `not_listed_with_bar` instead -- the facts adapter's own account of
    the class. The code is listed under `tolerated`, with the counts that account for it; the
    advice is "no change". (The module docstring says why the answer is wrapped.)"""
    world = _world(tmp_path, monkeypatch, listing_evening.source)
    late = listing_evening.late
    evening = sd._journal(world, DAY)["result"]["facts"]["notes"]
    assert evening["no_listing_row"] == 1 and evening["not_listed_with_bar"] == 0
    morning = morning_of(DAY)
    read = strategy_survivor_book.facts_day

    def published(**keywords: Any) -> Any:
        facts = read(**keywords)
        return _without_row(facts, late) if keywords["as_of"] == morning else facts

    monkeypatch.setattr(strategy_survivor_book, "facts_day", published)

    code, out, err = _morning(world, capsys, DAY, json_output=False)

    assert code == 0, err
    lines = out.splitlines()
    assert no_change(DAY) in lines
    assert any(line.startswith("tolerated") and late in line for line in lines)
    document = _filed(world, DAY)
    assert document["tolerated"] == [late] and document["differences"] == []


# --- the file, the fetch and what is written ------------------------------------------------------


def test_the_morning_writes_its_file_and_the_name_partition_and_nothing_else(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """Every file of the runtime directory but the morning file and the panel's `namechange`
    partition keeps its bytes: the prediction store, the ledger, the snapshots and the marker, the
    evening's journal and the provenance files among them."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    world.market.renames = ((_bought(world)[0], DAY, NEXT),)
    before = sd._files(world.runtime)

    code, _out, err = _morning(world, capsys, DAY)

    assert code == 0, err
    after = sd._files(world.runtime)
    morning = _relative(world, _morning_path(world, DAY))
    changed = _changed(before, after)
    assert morning in changed
    partition = [path for path in changed if path != morning]
    assert "panel/namechange/2026/data.parquet" in partition
    # The panel's own catalog records the write; no other dataset's partition is touched.
    assert set(partition) <= {"panel/catalog.duckdb", "panel/namechange/2026/data.parquet"}, changed
    assert [path for path in after if path not in before] == [morning]
    for directory in ("predictions", "paper", "daily_survivor_provenance"):
        assert _under(after, directory) == _under(before, directory)
        assert _under(before, directory), f"{directory} is empty: the probe sees nothing"
    evening = _relative(world, sd._journal_path(world, DAY))
    assert after[evening] == before[evening]


def test_a_morning_run_again_asks_nothing_writes_nothing_and_prints_the_same(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """A finished morning file is the check: run again it is printed from the file, with no request
    and no write; with `--json` it is the file's own document, byte for byte."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    world.market.renames = ((_bought(world)[0], DAY, NEXT),)
    _code, first, _err = _morning(world, capsys, DAY, json_output=False)
    document = _filed(world, DAY)
    before = sd._files(world.runtime)
    world.market.clear()

    code, again, err = _morning(world, capsys, DAY, json_output=False)

    assert (code, err) == (0, [])
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before
    assert _without_requests(again.splitlines()) == _without_requests(first.splitlines())
    assert (
        "tushare requests   0 this run {} -- the morning check was already complete; nothing "
        "was run" in again.splitlines()
    )

    code, shown, err = _morning(world, capsys, DAY)

    assert (code, err) == (0, [])
    assert json.loads(shown) == document
    assert shown == json.dumps(document, sort_keys=True, ensure_ascii=False) + "\n"
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_the_mornings_clock_is_pinned_in_its_file_before_the_fetch_and_a_rerun_keeps_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """The first request of the morning fails. Its file already holds the pin -- the morning's
    `as_of`, the evening's pinned holdings and snapshot, and the evening's facts read again, which
    the fetch is what makes unreadable -- and neither the advice nor the requests. A run again, on
    another clock and another `--as-of`, once the fetch works, uses the pinned clock and the filed
    read and finishes the file."""
    market = sd.FirstRequestFails(open_days=tds.OPEN_2026)
    world = _world(tmp_path, monkeypatch, evenings.first, market)
    market.journal = _morning_path(world, DAY)
    monkeypatch.setattr(cli, "TushareProvider", sd._without_backoff(cli.TushareProvider))

    code, _out, err = _morning(world, capsys, DAY)

    assert code == 1
    assert err[0].startswith("step 3 (panel update) failed:")
    assert sum(sd._requests(err[-1]).values()) >= 1
    assert market.seen != "no journal"
    assert market.seen["as_of"] == morning_of(DAY).isoformat()
    assert market.seen["held"] == []
    assert market.seen["evening_snapshot_id"] == _plan(world, DAY)["paper"]["snapshot_id"]
    assert "evening_read" in market.seen
    assert "differences" not in market.seen and "requests" not in market.seen
    pinned = _filed(world, DAY)
    assert "differences" not in pinned

    tds._bind(monkeypatch, world.repo, Overnight(open_days=tds.OPEN_2026))
    built = _builds(monkeypatch)
    code, out, err = _morning(
        world, capsys, DAY, as_of=morning_of(DAY, time(8, 50)), clock=morning_of(DAY, time(8, 55))
    )

    assert code == 0, err
    document = json.loads(out)
    assert document["as_of"] == morning_of(DAY).isoformat() == pinned["as_of"]
    assert document["evening_read"] == pinned["evening_read"]
    assert document["requests"] == {"namechange": 1}
    # The resumed fetch is made at the pinned instant too, never at this run's `--as-of`.
    assert built == [(DAY.year, morning_of(DAY))]


def _builds(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, datetime]]:
    """Every `panel build` the command runs from here on, by its year and its `--as-of`; the
    build goes through."""
    original = daily._panel_build
    calls: list[tuple[Any, datetime]] = []

    def recorded(runtime_dir: Path, **keywords: Any) -> Any:
        calls.append((keywords["year"], keywords["as_of"]))
        return original(runtime_dir, **keywords)

    monkeypatch.setattr(daily, "_panel_build", recorded)
    return calls


def test_the_evening_is_read_again_before_the_fetch_with_its_pinned_clock_and_holdings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """Two facts days are read, with the holdings the evening's journal pinned and never the
    book's now: the evening's, at the evening's own clock and before the request is made, and the
    morning's, at the morning's clock after it. The book now holds ten names; the journal pinned
    none."""
    world = _world(tmp_path, monkeypatch, evenings.second)
    reads = _reads(monkeypatch, world)
    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None and len(head.book.positions) == 10
    pinned = sd._journal(world, NEXT)

    code, out, err = _morning(world, capsys, NEXT)

    assert code == 0, err
    assert [(call["session"], call["as_of"]) for call in reads] == [
        (NEXT, datetime.fromisoformat(pinned["as_of"])),
        (NEXT, morning_of(NEXT)),
    ]
    assert [call["requests_so_far"] for call in reads] == [0, 1]
    assert [tuple(call["held"]) for call in reads] == [()] * 2 == [tuple(pinned["held"])] * 2
    assert json.loads(out)["held"] == pinned["held"]


def test_an_evening_that_pinned_holdings_is_read_with_them_and_its_halted_holding_keeps_its_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """The third evening (8 April) pinned the ten names the book came into it with, one of them
    halted that whole session: the evening's facts gave the halted holding its carried row
    (suspended, its last close) and froze it. Both of the morning's reads are made with that pin,
    the file's `held` is it, the evening's read holds the halted holding's verdict, and a holding
    frozen at both reads is no difference."""
    world = _world(tmp_path, monkeypatch, evenings.third)
    reads = _reads(monkeypatch, world)
    pinned = sd._journal(world, THIRD)
    halted = evenings.halted
    assert len(pinned["held"]) == 10 and halted in pinned["held"]

    code, out, err = _morning(world, capsys, THIRD, json_output=False)

    assert code == 0, err
    assert [(call["session"], tuple(call["held"])) for call in reads] == [
        (THIRD, tuple(pinned["held"]))
    ] * 2
    document = _filed(world, THIRD)
    assert document["held"] == pinned["held"]
    assert "suspended" in document["evening_read"]["verdicts"][halted]["reasons"]
    assert not any(halted in item["ts_codes"] for item in document["differences"])
    assert document["holdings_compared"] == sorted(_holdings(world, THIRD))
    (line,) = (line for line in out.splitlines() if line.startswith("holdings compared"))
    assert ", ".join(sorted(_holdings(world, THIRD))) in line


def test_a_store_that_no_longer_gives_the_evenings_facts_is_refused_before_the_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """The evening's facts read again must equal the evening's own document: one that does not
    means something other than the morning's fetch changed the store, and advice over it would
    compare unlike days. Refused by name, having fetched nothing and written nothing."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    read = strategy_survivor_book.facts_day

    def drifted(**keywords: Any) -> Any:
        facts = read(**keywords)
        rows = tuple(
            row.model_copy(update={"special_treatment": True})
            if row.ts_code == sd.SECURITIES[0]
            else row
            for row in facts.facts
        )
        return dataclasses.replace(facts, facts=rows)

    monkeypatch.setattr(strategy_survivor_book, "facts_day", drifted)
    before = sd._files(world.runtime)

    code, _out, err = _morning(world, capsys, DAY)

    assert code == 1
    assert err[0].startswith("step 4 (facts) failed:")
    assert "no longer gives the evening's facts" in err[0]
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_a_fault_writing_the_pin_is_its_steps_refusal_having_asked_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """The pin is written before the fetch. A fault writing it -- a full disk, a permission -- is
    the refusal of the step that writes it, printed with the requests line, never a traceback
    (review Minor 3; D1 review rule 2): nothing was fetched and no morning file is left."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    write = daily.write_journal

    def failing(path: Path, body: Mapping[str, Any]) -> None:
        if path.name.endswith(SUFFIX):
            raise OSError(28, "No space left on device")
        write(path, body)

    monkeypatch.setattr(daily, "write_journal", failing)
    before = sd._files(world.runtime)

    code, out, err = _morning(world, capsys, DAY)

    assert code == 1 and out == ""
    assert err[0].startswith("step 3 (panel update) failed: an unexpected fault: OSError")
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_a_store_that_cannot_be_opened_is_its_steps_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    world = _world(tmp_path, monkeypatch, evenings.first)

    def unopenable(_runtime: Path) -> Any:
        raise OSError("the panel's catalog cannot be opened")

    monkeypatch.setattr(daily, "panel_store", unopenable)

    code, out, err = _morning(world, capsys, DAY)

    assert code == 1 and out == ""
    assert err[0].startswith("step 2 (preconditions) failed: an unexpected fault: OSError")
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []


def test_an_evening_journal_whose_pinned_holdings_are_not_a_list_is_its_steps_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    world = _world(tmp_path, monkeypatch, evenings.first)
    path = sd._journal_path(world, DAY)
    journal = json.loads(path.read_text(encoding="utf-8"))
    journal["held"] = 10
    path.write_text(json.dumps(journal, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    before = sd._files(world.runtime)

    code, out, err = _morning(world, capsys, DAY)

    assert code == 1 and out == ""
    assert err[0].startswith("step 2 (preconditions) failed: an unexpected fault: TypeError")
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_a_fault_printing_the_advice_is_the_summary_steps_refusal_with_the_requests_spent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    world = _world(tmp_path, monkeypatch, evenings.first)

    def broken(*_arguments: Any, **_keywords: Any) -> list[str]:
        raise KeyError("text")

    monkeypatch.setattr(strategy_survivor_morning, "morning_lines", broken)

    code, out, err = _morning(world, capsys, DAY, json_output=False)

    assert code == 1 and out == ""
    assert err[0].startswith("step 5 (summary) failed: an unexpected fault: KeyError")
    assert sd._requests(err[-1]) == {"namechange": 1}


@pytest.mark.parametrize("edit", ["tolerated", "a difference's text"])
def test_a_finished_morning_file_edited_by_hand_is_refused_by_name_and_left_as_it_is(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
    edit: str,
) -> None:
    """A finished file is printed again from its own keys. One edited by hand -- a key gone, a
    difference with no line -- is not of the finished shape: refused by name, before any request,
    and left as it is (review Minor 3), never printed half and never resumed over."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    world.market.renames = ((_bought(world)[0], DAY, NEXT),)
    _morning(world, capsys, DAY)
    path = _morning_path(world, DAY)
    document = json.loads(path.read_text(encoding="utf-8"))
    if edit == "tolerated":
        del document["tolerated"]
    else:
        del document["differences"][0]["text"]
    path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    before = sd._files(world.runtime)
    world.market.clear()

    code, out, err = _morning(world, capsys, DAY, json_output=False)

    assert code == 1 and out == ""
    assert err[0].startswith("step 2 (preconditions) failed:")
    assert "is not this evening's morning file" in err[0]
    assert ("tolerated" if edit == "tolerated" else "differences") in err[0]
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


# --- one check per calendar date (whole-branch review C1) -----------------------------------------


def test_a_check_read_on_saturday_is_read_anew_on_monday_and_finds_what_sunday_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    friday_evening: Source,
) -> None:
    """The whole-branch review's C1, its own scenario. An ST rename of one of Friday's planned
    buys is dated Friday and published on Sunday. A check read on Saturday at 08:35 finds no
    change -- the announcement is not published yet -- and files the session's first check. On
    Monday at 08:30, before the call auction the plan is for, a check filed on another calendar
    date is not printed again: it is read anew -- a new pin, one request, a second file beside the
    first, which is kept as it was -- and it advises not to place that buy. With `--json` the
    newest check is the one printed."""
    world = _world(tmp_path, monkeypatch, friday_evening)
    named = _bought(world, FRIDAY)[0]
    world.market.late = ((named, FRIDAY, MONDAY, SUNDAY),)
    saturday = _shanghai(SATURDAY, time(8, 35))

    code, out, err = _morning(world, capsys, FRIDAY, as_of=saturday, json_output=False)

    assert code == 0, err
    assert no_change(FRIDAY) in out.splitlines()
    first = _morning_path(world, FRIDAY)
    kept = first.read_bytes()
    world.market.clear()
    monday = _shanghai(MONDAY, time(8, 30))

    code, out, err = _morning(world, capsys, FRIDAY, as_of=monday, json_output=False)

    assert code == 0, err
    assert world.market.payloads == ["namechange"]
    lines = out.splitlines()
    assert f"  do not place BUY {named}: special_treatment" in lines
    assert not any(line.startswith(no_change(FRIDAY)) for line in lines)
    (header,) = (line for line in lines if line.startswith("morning re-check"))
    assert f"the plan of {FRIDAY.isoformat()}, read again on {MONDAY.isoformat()}" in header
    (earlier,) = (line for line in lines if line.startswith("earlier check"))
    assert first.name in earlier and SATURDAY.isoformat() in earlier
    assert any(line.startswith("tushare requests   1 this run") for line in lines)
    assert not any("already complete" in line for line in lines)
    assert first.read_bytes() == kept
    assert _checks(world, FRIDAY) == sorted(
        [first.name, _generation_path(world, FRIDAY, MONDAY).name]
    )
    document = json.loads(_generation_path(world, FRIDAY, MONDAY).read_text(encoding="utf-8"))
    assert document["as_of"] == monday.isoformat()
    assert document["requests"] == {"namechange": 1}
    assert document["carried_from"] == {"file": first.name, "as_of": saturday.isoformat()}
    assert document["evening_read"] == json.loads(kept)["evening_read"]
    assert f"do not place BUY {named}: special_treatment" in _texts(document)
    world.market.clear()

    code, shown, err = _morning(world, capsys, FRIDAY, as_of=monday + timedelta(minutes=10))

    assert (code, err) == (0, [])
    assert json.loads(shown) == document
    assert world.market.payloads == []


def test_a_check_read_again_on_the_same_calendar_date_is_printed_again_asking_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    friday_evening: Source,
) -> None:
    """Saturday 08:35, then Saturday 22:00 on another `--as-of`: one calendar date, one check --
    printed again from its file with no request and no write, whatever the clock says."""
    world = _world(tmp_path, monkeypatch, friday_evening)
    world.market.late = ((_bought(world, FRIDAY)[0], FRIDAY, MONDAY, SUNDAY),)
    _code, first, _err = _morning(
        world, capsys, FRIDAY, as_of=_shanghai(SATURDAY, time(8, 35)), json_output=False
    )
    before = sd._files(world.runtime)
    world.market.clear()

    code, again, err = _morning(
        world, capsys, FRIDAY, as_of=_shanghai(SATURDAY, time(22, 0)), json_output=False
    )

    assert (code, err) == (0, [])
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before
    assert _without_requests(again.splitlines()) == _without_requests(first.splitlines())
    assert (
        "tushare requests   0 this run {} -- the morning check was already complete; nothing "
        "was run" in again.splitlines()
    )
    assert _checks(world, FRIDAY) == [_morning_path(world, FRIDAY).name]


def test_a_check_read_twice_on_monday_is_one_check_printed_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    friday_evening: Source,
) -> None:
    """With no Saturday check, Monday's is the session's first: it finds Sunday's announcement,
    and read again at 09:00 the same Monday it is printed from its file, asking nothing."""
    world = _world(tmp_path, monkeypatch, friday_evening)
    named = _bought(world, FRIDAY)[0]
    world.market.late = ((named, FRIDAY, MONDAY, SUNDAY),)

    code, _out, err = _morning(world, capsys, FRIDAY, as_of=_shanghai(MONDAY, time(8, 30)))

    assert code == 0, err
    document = _filed(world, FRIDAY)
    assert f"do not place BUY {named}: special_treatment" in _texts(document)
    assert document["carried_from"] is None
    before = sd._files(world.runtime)
    world.market.clear()

    code, shown, err = _morning(world, capsys, FRIDAY, as_of=_shanghai(MONDAY, time(9, 0)))

    assert (code, err) == (0, [])
    assert json.loads(shown) == document
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before
    assert _checks(world, FRIDAY) == [_morning_path(world, FRIDAY).name]


def test_monday_still_advises_what_saturday_found_and_compares_with_saturdays_evening_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    friday_evening: Source,
) -> None:
    """Friday's rename published on Saturday morning: Saturday's check finds it. Monday's is read
    anew, and its fetch has nothing new to bring -- but the store it reads at Friday's own clock
    now holds Saturday's row, which is dated Friday: read again there, it is not the evening's
    facts. So Monday compares with the evening read Saturday's check filed before its own fetch
    -- and advises what Saturday did, never refusing the store as changed under the evening."""
    world = _world(tmp_path, monkeypatch, friday_evening)
    named = _bought(world, FRIDAY)[0]
    world.market.renames = ((named, FRIDAY, MONDAY),)
    reads = _reads(monkeypatch, world)

    code, _out, err = _morning(world, capsys, FRIDAY, as_of=_shanghai(SATURDAY, time(8, 35)))

    assert code == 0, err
    saturday = _filed(world, FRIDAY)
    assert f"do not place BUY {named}: special_treatment" in _texts(saturday)
    reads.clear()

    code, out, err = _morning(world, capsys, FRIDAY, as_of=_shanghai(MONDAY, time(8, 30)))

    assert code == 0, err
    monday = json.loads(out)
    assert f"do not place BUY {named}: special_treatment" in _texts(monday)
    assert monday["evening_read"] == saturday["evening_read"]
    assert monday["carried_from"]["file"] == _morning_path(world, FRIDAY).name
    # One facts read only, the morning's: the evening's comes from Saturday's file.
    assert [call["as_of"] for call in reads] == [_shanghai(MONDAY, time(8, 30))]


def test_a_check_read_on_an_earlier_date_than_one_already_filed_is_refused_asking_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    friday_evening: Source,
) -> None:
    """Monday's check is filed; an `--as-of` on Saturday afterwards would file a check under an
    earlier date than one already made: refused by name, nothing fetched or written."""
    world = _world(tmp_path, monkeypatch, friday_evening)
    monday = _shanghai(MONDAY, time(8, 30))
    _morning(world, capsys, FRIDAY, as_of=monday)
    before = sd._files(world.runtime)
    world.market.clear()

    code, out, err = _morning(
        world, capsys, FRIDAY, as_of=_shanghai(SATURDAY, time(8, 35)), clock=monday
    )

    assert code == 1 and out == ""
    assert err[0].startswith("step 2 (preconditions) failed:")
    assert MONDAY.isoformat() in err[0] and SATURDAY.isoformat() in err[0]
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_the_journal_readers_take_neither_check_for_a_sessions_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    friday_evening: Source,
) -> None:
    """Both checks lie beside the evening's journal: the journals' glob (`????-??-??.json`) and
    the forward report's reader (a stem that is a session) find the evening's journal alone."""
    world = _world(tmp_path, monkeypatch, friday_evening)
    _morning(world, capsys, FRIDAY, as_of=_shanghai(SATURDAY, time(8, 35)))
    _morning(world, capsys, FRIDAY, as_of=_shanghai(MONDAY, time(8, 30)))
    assert len(_checks(world, FRIDAY)) == 2
    directory = sd._journal_path(world, FRIDAY).parent

    assert sorted(path.name for path in directory.glob("????-??-??.json")) == [
        f"{FRIDAY.isoformat()}.json"
    ]
    report = tds._script("forward_report", ROOT / "scripts")
    admitted = daily.admit_registration(world.registration, world.repo)
    assert list(report._survivor_journals(world.runtime, admitted)) == [FRIDAY]


# --- New Year -------------------------------------------------------------------------------------


@dataclass
class NewYearRename(sd.NewYearMarket):
    """The New Year market, its 2027 name corpus holding an ST rename of `renamed` announced on 2
    January 2027 -- the reviewer's probe of the second request the morning made at New Year
    (review Important 1)."""

    renamed: str = ""

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        rows = super()._names(params)
        if str(params["start_date"])[:4] != "2027" or not self.renamed:
            return rows
        return [*rows, [self.renamed, f"*ST{self.renamed}", "20270105", None, "20270102", "ST"]]


@pytest.mark.parametrize("day", [date(2027, 1, 2), FIRST_OF_2027], ids=lambda day: day.isoformat())
def test_a_morning_in_the_new_year_asks_the_sessions_year_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    new_year_evening: Source,
    day: date,
) -> None:
    """The evening of 31 December is in 2026 and its morning in 2027 -- on 2 January, when no
    announcement of 2027 exists yet, or at 08:30 of 4 January, before the call auction of the first
    session. The facts adapter reads the name corpus through the session's year, as announced by
    the session, so the new year's corpus can change no advice: the morning asks the session's
    year alone -- one request, as every morning -- and an empty new year refuses nothing (review
    Important 1). An ST rename of a planned buy announced on 2 January 2027 is therefore neither
    screened nor a notice: the new year's corpus is not fetched, and the advice line says how far
    it reaches."""
    market = NewYearRename(open_days=sd.NEW_YEAR_DAYS)
    world = _world(tmp_path, monkeypatch, new_year_evening, market)
    market.renamed = _bought(world, LAST_OF_2026)[0]
    at = datetime.combine(day, time(8, 30), tzinfo=daily.SHANGHAI)

    code, out, err = _morning(world, capsys, LAST_OF_2026, as_of=at, json_output=False)

    assert code == 0, err
    assert world.market.payloads == ["namechange"]
    assert [params["start_date"][:4] for params in world.market.asked("namechange")] == ["2026"]
    assert no_change(LAST_OF_2026) in out.splitlines()
    assert not any("known limitation" in line for line in [*out.splitlines(), *err])
    document = _filed(world, LAST_OF_2026)
    assert document["requests"] == {"namechange": 1}
    assert document["session"] == LAST_OF_2026.isoformat()
    assert document["differences"] == [] and document["notices"] == []


# --- the preconditions ----------------------------------------------------------------------------


def test_a_morning_read_at_or_before_the_evenings_own_clock_is_refused_having_asked_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """`--as-of` 17:00 of the session, while the evening was pinned at 18:30: the morning would
    rewrite the name partition under an instant earlier than the evening's own knowledge and read
    the morning's facts at it (review Minor 1). Refused, and so is the evening's instant itself:
    the morning is read after the evening, or not at all."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    before = sd._files(world.runtime)
    evening = datetime.fromisoformat(sd._journal(world, DAY)["as_of"])

    for at in (datetime.combine(DAY, time(17, 0), tzinfo=daily.SHANGHAI), evening):
        code, out, err = _morning(world, capsys, DAY, as_of=at)

        assert code == 1 and out == ""
        assert err[0].startswith("step 2 (preconditions) failed:")
        assert at.isoformat() in err[0] and evening.isoformat() in err[0]
        assert err[-1] == NO_REQUEST
        assert world.market.payloads == []
        assert sd._files(world.runtime) == before


def test_a_morning_at_or_after_the_next_call_auction_is_refused_having_asked_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """09:15 is when the auction that fixes tomorrow's open begins: a check pinned at or after it,
    or run when the clock is, is no advice before it -- either one, whatever the other says. Exit
    1, nothing fetched, nothing written."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    before = sd._files(world.runtime)

    for at, now in (
        (morning_of(DAY, time(9, 15)), morning_of(DAY, time(9, 15))),
        (morning_of(DAY, time(9, 20)), morning_of(DAY, time(9, 20))),
        (morning_of(DAY, time(8, 30)), morning_of(DAY, time(9, 20))),
        (morning_of(DAY, time(9, 20)), morning_of(DAY, time(9, 0))),
    ):
        code, out, err = _morning(world, capsys, DAY, as_of=at, clock=now)

        assert code == 1 and out == ""
        assert err[0].startswith("step 2 (preconditions) failed:")
        assert "09:15" in err[0]
        assert err[-1] == NO_REQUEST
        assert world.market.payloads == []
        assert sd._files(world.runtime) == before


def test_a_morning_one_minute_before_the_call_auction_is_still_advice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    world = _world(tmp_path, monkeypatch, evenings.first)
    at = morning_of(DAY, time(9, 14))

    code, _out, err = _morning(world, capsys, DAY, as_of=at, clock=at)

    assert code == 0, err


def test_a_morning_with_no_evening_journal_is_refused_having_asked_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
    seeded: Path,
) -> None:
    """Nothing ran on 6 April: there is no plan to re-check, and nothing is made for it."""
    source = Source(evenings.first.repo, evenings.first.registration, seeded)
    world = _world(tmp_path, monkeypatch, source)
    before = sd._files(world.runtime)

    code, out, err = _morning(world, capsys, DAY)

    assert code == 1 and out == ""
    assert err[0].startswith("step 2 (preconditions) failed:")
    assert "no evening journal" in err[0] and DAY.isoformat() in err[0]
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_an_evening_that_did_not_complete_is_refused_having_asked_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """A journal with no `result` is a day refused before its paper step, or cut: it plans nothing
    yet, and the evening's own rerun is the remedy."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    path = sd._journal_path(world, DAY)
    journal = json.loads(path.read_text(encoding="utf-8"))
    del journal["result"]
    path.write_text(json.dumps(journal, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    before = sd._files(world.runtime)

    code, _out, err = _morning(world, capsys, DAY)

    assert code == 1
    assert err[0].startswith("step 2 (preconditions) failed:")
    assert "not complete" in err[0]
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_a_day_that_only_settled_has_nothing_to_re_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
    seeded: Path,
) -> None:
    """The evening run after 09:15 of the next session settles only and plans nothing: refused by
    name."""
    source = Source(evenings.first.repo, evenings.first.registration, seeded)
    world = _world(tmp_path, monkeypatch, source)
    _evening(world, DAY, at=datetime.combine(NEXT, time(10, 0), tzinfo=daily.SHANGHAI))
    assert _plan(world, DAY)["paper"] is None
    before = sd._files(world.runtime)
    world.market.clear()

    code, _out, err = _morning(world, capsys, DAY)

    assert code == 1
    assert err[0].startswith("step 2 (preconditions) failed:")
    assert "settled only" in err[0] and "no evening" in err[0]
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_the_epoch_day_has_nothing_to_re_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A day on the epoch only opens the paper book: no facts, no plan."""
    world = sd._world(tmp_path, monkeypatch, seeded, config=sd.survivor_config(epoch=DAY))
    sd._run(world, capsys)
    assert "opening" in sd._journal(world)["result"]
    before = sd._files(world.runtime)
    world.market.clear()

    code, _out, err = _morning(world, capsys, DAY)

    assert code == 1
    assert err[0].startswith("step 2 (preconditions) failed:")
    assert "opens the paper book" in err[0]
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_a_morning_file_of_another_evening_is_refused_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    world = _world(tmp_path, monkeypatch, evenings.first)
    _morning(world, capsys, DAY)
    path = _morning_path(world, DAY)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["evening_snapshot_id"] = "snapshot_of_another_evening"
    path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    before = sd._files(world.runtime)
    world.market.clear()

    code, _out, err = _morning(world, capsys, DAY)

    assert code == 1
    assert err[0].startswith("step 2 (preconditions) failed:")
    assert "snapshot_of_another_evening" in err[0]
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


def test_a_second_run_while_the_paper_book_is_held_is_refused_having_asked_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """One writer per paper book: the morning takes its lock too, and a run while another holds it
    is refused by name before it fetches or pins anything."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    before = sd._files(world.runtime)

    with strategy_survivor_book.paper_book_lock(world.runtime / "paper"):
        code, _out, err = _morning(world, capsys, DAY)

    assert code == 1
    assert err[0].startswith("step 3 (panel update) failed: another run holds the paper book's")
    assert err[-1] == NO_REQUEST
    assert world.market.payloads == []
    assert not _morning_path(world, DAY).exists()
    assert sd._files(world.runtime) == before


# --- the registration's kind and the P6 day's options ---------------------------------------------


def test_a_p6_registration_with_the_flag_exits_two_with_no_request_and_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The P6 book has no morning plan: refused at step 1 as a registration the flag does not apply
    to (exit 2), before any request and before the runtime directory exists."""
    repo, registration = tds._repository(tmp_path, tds.CONFIG)
    market = Market(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, repo, market)
    runtime = tmp_path / "runtime"
    at = morning_of(DAY)

    code = daily.main(
        [*_arguments(runtime, registration, repo, at), "--morning-recheck", "--json"],
        clock=lambda: at,
    )

    out, err = capsys.readouterr()
    assert code == 2 == daily.DailyExit.no_registration
    assert out == ""
    assert err.splitlines()[0].startswith("step 1 (registration) failed:")
    assert "survivor" in err and "P6" in err
    assert err.splitlines()[-1] == NO_REQUEST
    assert market.payloads == []
    assert not runtime.exists()


@pytest.mark.parametrize(
    "option", [("--full-update",), ("--top", "3"), ("--dataset", "price")], ids=lambda o: o[0]
)
def test_an_option_of_the_p6_day_is_refused_before_anything_is_made(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
    option: tuple[str, ...],
) -> None:
    """The morning fetches the name corpus and nothing else: an option that names what a day
    fetches, or what it prints, has no meaning there. Exit 2, no request, no write."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    before = sd._files(world.runtime)

    code, _out, err = _morning(world, capsys, DAY, extra=option)

    assert code == 2
    assert err[0].startswith("step 1 (registration) failed:")
    assert option[0] in err[0]
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before


@pytest.mark.parametrize(
    "other",
    [("--factor-gaps", "2026-04-01"), ("--pin-worktree", "pinned"), ("--launchd-plist", "logs")],
)
def test_the_flag_does_not_combine_with_the_commands_that_run_no_day(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], other: tuple[str, ...]
) -> None:
    arguments = ["--runtime-dir", str(tmp_path), "--morning-recheck", *other]

    with pytest.raises(SystemExit) as stopped:
        daily.main(arguments)

    assert stopped.value.code == 2
    assert f"--morning-recheck cannot be combined with {other[0]}" in capsys.readouterr().err


# --- a P6 day never loads what the survivor day does ----------------------------------------------

_IN_A_FRESH_INTERPRETER: Final[str] = (
    "import contextlib, io, json, sys\n"
    "from pathlib import Path\n"
    "import pytest\n"
    "import test_daily_selection as tds\n"
    "from daily_market import Market\n"
    "root = Path(sys.argv[1])\n"
    "watched = ('openalpha_cn.backtest.paper', 'openalpha_cn.strategy_survivor', "
    "'openalpha_cn.strategy_survivor_morning')\n"
    "before = [name for name in watched if name in sys.modules]\n"
    "patch = pytest.MonkeyPatch()\n"
    "market = Market(open_days=tds.OPEN_2026)\n"
    "repo, registration = tds._repository(root, tds.CONFIG)\n"
    "tds._bind(patch, repo, market)\n"
    "err = io.StringIO()\n"
    "with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):\n"
    "    code = tds.daily.main(['--runtime-dir', str(root / 'runtime'), '--registration', "
    "str(registration), '--repo', str(repo), '--morning-recheck'])\n"
    "patch.undo()\n"
    "print(json.dumps({'exit': code, 'before': before, "
    "'after': [name for name in watched if name in sys.modules], 'stderr': err.getvalue()}))\n"
)


def test_a_p6_registration_with_the_flag_loads_neither_the_paper_book_nor_the_morning_module(
    tmp_path: Path,
) -> None:
    """Refused at step 1 in a process that had loaded none of them -- and it still has not: the
    survivor modules are imported only for a registration that names a `kind`."""
    path = os.pathsep.join(
        part
        for part in (
            str(ROOT / "src"),
            str(ROOT / "tests"),
            str(ROOT / "tests" / "unit" / "scripts"),
            os.environ.get("PYTHONPATH"),
        )
        if part
    )
    completed = subprocess.run(
        [sys.executable, "-c", _IN_A_FRESH_INTERPRETER, str(tmp_path / "p6")],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["exit"] == 2, report["stderr"]
    assert report["before"] == [] and report["after"] == []


# --- the stored shapes the readers depend on (whole-branch seams review, finding 7) --------------

MIGRATION_NEEDED: Final[str] = (
    "a change here makes stored documents unreadable: a migration is needed"
)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    for kind, name in (
        (int, "int"),
        (float, "float"),
        (str, "str"),
        (list, "list"),
        (dict, "dict"),
    ):
        if isinstance(value, kind):
            return name
    raise AssertionError(f"{value!r} is not a JSON value")


def _at(document: Any, path: str) -> list[Any]:
    """Every value at `path` in `document`: `.`-separated keys, `[]` each item of a list, `*` each
    value of a mapping. A key a mapping does not hold, or a step into a value of another type,
    reaches nothing there."""
    values = [document]
    for step in path.split("."):
        found: list[Any] = []
        for value in values:
            key, each = (step[:-2], True) if step.endswith("[]") else (step, False)
            if key == "*":
                children = list(value.values()) if isinstance(value, dict) else []
            elif isinstance(value, dict) and key in value:
                children = [value[key]]
            else:
                children = []
            for child in children:
                if each:
                    found += child if isinstance(child, list) else []
                else:
                    found.append(child)
        values = found
    return values


def _types_at(documents: Sequence[Any], path: str) -> set[str]:
    return {_json_type(value) for document in documents for value in _at(document, path)}


MORNING_FILE_SHAPE: Final[dict[str, frozenset[str]]] = {
    "": frozenset(
        {
            "schema",
            "session",
            "as_of",
            "held",
            "evening_snapshot_id",
            "registration",
            "evening_read",
            "carried_from",
            "requests",
            "differences",
            "tolerated",
            "notices",
            "holdings_compared",
            "counts_changed",
            "wording",
        }
    ),
    "registration": frozenset({"path", "sha256", "commit", "config_id"}),
    "evening_read": frozenset({"summary", "aged_zero", "verdicts"}),
    "evening_read.summary": frozenset(
        {
            "rows",
            "universe_size",
            "scored_size",
            "assessed",
            "eligible",
            "survivors",
            "reasons",
            "notes",
        }
    ),
    "evening_read.verdicts.*": frozenset({"reasons", "survivor"}),
    "carried_from": frozenset({"file", "as_of"}),
    "differences[]": frozenset({"kind", "ts_codes", "reasons", "text"}),
    "notices[]": frozenset({"ts_code", "name", "ann_date", "change_reason", "text"}),
}
"""`openalpha-daily-survivor-morning/v1`: every mapping of a finished morning file and its keys,
exactly. `morning_complete` refuses a finished file missing any of the advice stage's keys, and
`check_morning_file`, `FactsRead.from_json` and `morning_lines` read the rest by name, so a key
added or renamed under `/v1` once real mornings are filed makes every earlier file unreadable."""
MORNING_NOTES_READ: Final[frozenset[str]] = frozenset(
    {"no_listing_row", "not_listed_with_bar", "st", "delisting"}
)
"""The notes of the filed evening read the comparison reads by name (`_vanished_rows`,
`_counts`); the others are compared as a whole (`check_evening_read`)."""


def test_the_morning_files_shape_is_pinned_so_that_a_change_needs_a_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evenings: Evenings,
) -> None:
    """Two real checks of the first evening -- the first at 22:00 that evening, the second the
    next morning, with a rename to advise on and one to notice -- hold every mapping of the file
    with exactly its pinned keys."""
    world = _world(tmp_path, monkeypatch, evenings.first)
    bought = _bought(world)
    world.market.renames = ((bought[0], DAY, NEXT),)
    world.market.early = ((bought[1], NEXT, NEXT + timedelta(days=1)),)
    _morning(world, capsys, DAY, as_of=_shanghai(DAY, time(22, 0)))
    _morning(world, capsys, DAY, as_of=morning_of(DAY))
    documents = [
        json.loads(_morning_path(world, DAY).read_text(encoding="utf-8")),
        json.loads(_generation_path(world, DAY, NEXT).read_text(encoding="utf-8")),
    ]

    for path, keys in MORNING_FILE_SHAPE.items():
        found = [item for document in documents for item in _at(document, path)] if path else []
        mappings = [item for item in (found if path else documents) if isinstance(item, dict)]
        assert mappings, f"no morning file here holds {path!r}: the pin is not witnessed"
        for item in mappings:
            assert set(item) == keys, (
                f"the morning file's {path or 'top level'} holds {sorted(item)}, pinned "
                f"{sorted(keys)}: {MIGRATION_NEEDED}"
            )
    for document in documents:
        assert set(document["evening_read"]["summary"]["notes"]) >= MORNING_NOTES_READ, (
            MIGRATION_NEEDED
        )
        assert document["schema"] == "openalpha-daily-survivor-morning/v1"


SURVIVOR_JOURNAL_STAGES: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "session",
        "as_of",
        "held",
        "held_from",
        "registration",
        "panel",
        "factors",
        "prediction",
        "paper",
        "result",
    }
)
"""The stages of a completed evening's `openalpha-daily-survivor/v1` journal, exactly (a day that
caught a session up files `catch_ups` too)."""
SURVIVOR_JOURNAL_READ: Final[dict[str, frozenset[str]]] = {
    # The day itself, its rerun and the morning re-check: the pin.
    "schema": frozenset({"str"}),
    "session": frozenset({"str"}),
    "as_of": frozenset({"str"}),
    "held": frozenset({"list"}),
    "held_from": frozenset({"dict", "null"}),
    "held_from.session": frozenset({"str"}),
    "held_from.session_index": frozenset({"int"}),
    "held_from.snapshot_id": frozenset({"str"}),
    "registration.path": frozenset({"str"}),
    "registration.sha256": frozenset({"str"}),
    "registration.commit": frozenset({"str"}),
    "registration.config_id": frozenset({"str"}),
    # The day's resumed stages.
    "panel.targets": frozenset({"list"}),
    "panel.requests": frozenset({"dict"}),
    "factors": frozenset({"dict"}),
    # The forward report (`_journals_at`, `check_journals_against_the_store`).
    "prediction.registered": frozenset({"bool"}),
    "prediction.record_id": frozenset({"str"}),
    # The paper record (`strategy_survivor_paper_report`): the paper stage and the result.
    "paper.snapshot_id": frozenset({"str", "null"}),
    "result.kind": frozenset({"str"}),
    "result.session": frozenset({"str"}),
    "result.session_index": frozenset({"int"}),
    "result.paper.snapshot_id": frozenset({"str"}),
    "result.paper.catch_ups": frozenset({"list"}),
    "result.paper.illiquid_exits": frozenset({"list"}),
    "result.paper.trade_list.unfunded": frozenset({"list"}),
    "result.paper.decision.exits": frozenset({"list"}),
    "result.paper.decision.unaffordable": frozenset({"list"}),
    "result.paper.decision.frozen": frozenset({"list"}),
    "result.paper.decision.near_exit": frozenset({"list"}),
    "result.paper.decision.deferred_exits": frozenset({"list"}),
    "result.paper.settled.attempts[].status": frozenset({"str"}),
    "result.paper.settled.attempts[].attempt_id": frozenset({"str"}),
    "result.paper.settled.expired": frozenset({"list"}),
    "result.paper.settled.cancelled": frozenset({"list"}),
    "result.paper.settled.written_off": frozenset({"list"}),
    # The morning re-check (`plan_names`, `check_evening_read`) and the halt's start
    # (`strategy_survivor_summary.carried_since`).
    "result.paper.book.holdings[].ts_code": frozenset({"str"}),
    "result.paper.book.holdings[].flags": frozenset({"list"}),
    "result.paper.open_orders[].side": frozenset({"str"}),
    "result.paper.open_orders[].ts_code": frozenset({"str"}),
    "result.paper.open_orders[].carried": frozenset({"bool"}),
    "result.paper.decision.watch_list": frozenset({"list"}),
    "result.facts.rows": frozenset({"int"}),
    "result.facts.universe_size": frozenset({"int"}),
    "result.facts.scored_size": frozenset({"int"}),
    "result.facts.notes": frozenset({"dict"}),
    "result.screen.assessed": frozenset({"int"}),
    "result.screen.eligible": frozenset({"int"}),
    "result.screen.survivors": frozenset({"int"}),
    "result.screen.reasons": frozenset({"dict"}),
}
"""Every key path of a completed evening's journal a reader depends on, with its JSON type: the
day's own pin and stages, the forward report's prediction stage, the paper record's paper stage
and result, the morning's plan and the halt's start. The readers read by name and tolerate a key
added; a key renamed, removed or retyped under `/v1` once real days are journalled makes every
earlier journal unreadable to the reader that needs it. Not witnessed by these three evenings,
and so not pinned here: a journal's own `catch_ups` entries, an epoch day's `opening`, a
settle-only day's `paper_skipped`."""


def test_the_survivor_journals_read_shape_is_pinned_so_that_a_change_needs_a_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evenings: Evenings
) -> None:
    """The three real evenings' journals -- ten buys planned; ten holdings and the fills; a halted
    holding -- hold every pinned stage and every key path a reader depends on, of its type."""
    world = _world(tmp_path, monkeypatch, evenings.third)
    journals = [sd._journal(world, day) for day in (DAY, NEXT, THIRD)]

    for journal in journals:
        assert set(journal) == SURVIVOR_JOURNAL_STAGES, (
            f"a completed evening's journal holds the stages {sorted(journal)}, pinned "
            f"{sorted(SURVIVOR_JOURNAL_STAGES)}: {MIGRATION_NEEDED}"
        )
        assert journal["schema"] == "openalpha-daily-survivor/v1"
        assert journal["result"]["kind"] == "survivor_book/v1"
    for path, types in SURVIVOR_JOURNAL_READ.items():
        found = _types_at(journals, path)
        assert found, f"no journal here holds {path!r}: the pin is not witnessed"
        assert found <= types, (
            f"the journals' {path} is {sorted(found)}, pinned {sorted(types)}: {MIGRATION_NEEDED}"
        )
