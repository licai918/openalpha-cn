"""A smoke registration's every output says so (`V2-P7-019`, task 1).

A registration whose `settings` carry `daily_selection.SMOKE_SETTINGS_KEY` is a real-data smoke
registration: a one-off, committed only in a throwaway clone (`scripts/p7_smoke_env.py`), never
the `V2-P7-006` registration. The daily command, its morning re-check and the weekly report print
`SMOKE_BANNER` as the first and the last line of stderr, and as the first and the last line of the
text they print on stdout -- or as the `smoke` key of a JSON document -- so an order list from it
cannot be read without the banner. The marker is read from the registration file at the print
boundary; it never enters the configuration (its `config_id` is unchanged), the journal, the
morning file or the report the weekly command writes. A registration without the marker prints what
it always printed: every other test of the two scripts runs one (`test_daily_selection_survivor_day
.py` asserts `err == []` for a completed evening, for instance).

The world is `test_daily_selection_survivor_day.py`'s: its scripted market, its seeded panel (3
April) and its configuration (epoch 3 April), with `test_forward_report_survivor.py`'s registration
helper so that the settings carry the test protocol and the marker. Nothing reaches the network.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import shutil
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
import test_forward_report_survivor as survivor_report
from research_repo import commit_file, git
from test_daily_selection_survivor_day import seeded  # noqa: F401  (a module fixture, reused)

from openalpha_cn import strategy_survivor_book

daily = tds.daily
forward_report = survivor_report.forward_report

MARKER: Final[dict[str, str]] = {"issue": "V2-P7-019", "note": "a test's smoke registration"}
SMOKE_SETTINGS: Final[dict[str, Any]] = {**survivor_report.settings_with(), "smoke_run": MARKER}
"""The settings of a smoke registration: the P6 measurement settings, the test protocol and the
marker -- its key spelled here, so that renaming `SMOKE_SETTINGS_KEY` turns this file red."""
MORNING_AS_OF: Final[datetime] = datetime(2026, 4, 7, 8, 30, tzinfo=daily.SHANGHAI)
"""Before 7 April's call auction: the morning re-check of 6 April's plan."""
REPORT_AS_OF: Final[datetime] = datetime(2026, 4, 7, 10, 0, tzinfo=daily.SHANGHAI)
"""After 6 April's registration cutoff (09:15 on the 7th): the weekly report reads its record."""


def _invoke(
    entry: Callable[..., int], arguments: Sequence[str], *, clock: Callable[[], datetime]
) -> tuple[int, str, list[str]]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = entry(list(arguments), clock=clock)
    assert tds.SECRET_TOKEN not in out.getvalue() + err.getvalue()
    return code, out.getvalue(), err.getvalue().splitlines()


def _day_arguments(world: tds.World, as_of: datetime, *extra: str) -> list[str]:
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    return [*arguments, "--repo", str(world.repo), "--as-of", as_of.isoformat(), *extra]


def _smoke_world(root: Path, seeded_runtime: Path) -> tds.World:
    repo, registration = survivor_report._repository(
        root, survivor_day.CONFIG, settings=SMOKE_SETTINGS
    )
    runtime = root / "runtime"
    shutil.copytree(seeded_runtime, runtime)
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    return tds.World(repo=repo, registration=registration, runtime=runtime, market=market)


@dataclasses.dataclass(frozen=True)
class Evening:
    world: tds.World
    code: int
    out: str
    err: list[str]


@pytest.fixture(scope="module")
def smoke_evening(
    tmp_path_factory: pytest.TempPathFactory,
    seeded: Path,  # noqa: F811
) -> Evening:
    """A smoke registration's first evening (6 April), run once through `main` with `--json`."""
    world = _smoke_world(tmp_path_factory.mktemp("smoke-evening"), seeded)
    with pytest.MonkeyPatch.context() as patch:
        tds._bind(patch, world.repo, world.market)
        world.market.today = survivor_day.DAY
        code, out, err = _invoke(
            daily.main,
            _day_arguments(world, survivor_day.DAY_AS_OF, "--json"),
            clock=lambda: survivor_day.RUN_CLOCK,
        )
    return Evening(world=world, code=code, out=out, err=err)


@pytest.fixture
def world(smoke_evening: Evening, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tds.World:
    """A copy of the smoke evening's runtime directory, under its registration."""
    runtime = tmp_path / "runtime"
    shutil.copytree(smoke_evening.world.runtime, runtime)
    tds._bind(monkeypatch, smoke_evening.world.repo, smoke_evening.world.market)
    return dataclasses.replace(smoke_evening.world, runtime=runtime)


# --- the marker ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "marked"),
    [
        ({"settings": {"smoke_run": {"issue": "V2-P7-019"}}}, True),
        ({"settings": {"smoke_run": None}}, True),
        ({"settings": {"smoke_run": False}}, True),
        ({"settings": {"random_seed": 3}}, False),
        ({"config": {"smoke_run": True}, "settings": {}}, False),
        ({"settings": "smoke_run"}, False),
        ([1, 2], False),
    ],
)
def test_any_value_under_the_settings_key_marks_a_smoke_registration(
    tmp_path: Path, body: object, marked: bool
) -> None:
    """Under `settings`, the key alone marks the file -- a malformed marker too: the banner fails
    on, never off. A key of the configuration is not the marker (the configuration is closed)."""
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    assert daily.smoke_banner(path) == (daily.SMOKE_BANNER if marked else None)


def test_a_registration_that_cannot_be_read_carries_no_banner(tmp_path: Path) -> None:
    """Its admission refuses it at step 1 and prints no order, so there is nothing to banner."""
    broken = tmp_path / "broken.json"
    broken.write_bytes(b"\xff not json")

    assert daily.smoke_banner(tmp_path / "missing.json") is None
    assert daily.smoke_banner(broken) is None


def test_the_banner_says_do_not_place_these_orders() -> None:
    assert daily.SMOKE_SETTINGS_KEY == "smoke_run"
    assert daily.SMOKE_DOCUMENT_KEY == "smoke"
    assert daily.SMOKE_BANNER.startswith("smoke: do not place these orders")
    assert daily.SMOKE_BANNER.isascii()
    assert daily.bannered(["a"], None) == ["a"]
    assert daily.bannered(["a"], "B") == ["B", "a", "B"]
    assert daily.with_smoke({"a": 1}, None) == {"a": 1}
    assert daily.with_smoke({"a": 1}, "B") == {"a": 1, "smoke": "B"}


def test_the_marker_lives_in_the_settings_so_the_configuration_identity_is_unchanged(
    tmp_path: Path,
) -> None:
    plain, smoke = tmp_path / "plain.json", tmp_path / "smoke.json"
    commit = "0" * 40
    tds.registry.register(
        survivor_day.CONFIG,
        tds.CRITERIA,
        plain,
        code_commit=commit,
        settings=survivor_report.settings_with(),
    )
    tds.registry.register(
        survivor_day.CONFIG, tds.CRITERIA, smoke, code_commit=commit, settings=SMOKE_SETTINGS
    )

    bodies = [json.loads(path.read_text(encoding="utf-8")) for path in (plain, smoke)]
    assert bodies[0]["config_id"] == bodies[1]["config_id"]
    assert plain.read_bytes() != smoke.read_bytes()


# --- what reading the marker never does ----------------------------------------------------------

NESTED: Final[bytes] = b"[" * 100_000
"""A registration body nested past the interpreter's recursion limit: `json.loads` raises
`RecursionError` on it, which is not a `ValueError`."""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO is a POSIX file type")
def test_a_registration_that_is_not_a_regular_file_is_never_opened(tmp_path: Path) -> None:
    """A FIFO with no writer blocks a read for ever, and `/dev/zero` never ends one. The marker is
    read from a regular file only -- admission's own first test -- so such a `--registration` is
    refused by admission at once (exit 2), as it was before the banner."""
    fifo = tmp_path / "registration.json"
    os.mkfifo(fifo)
    found: list[str | None] = []
    reader = threading.Thread(target=lambda: found.append(daily.smoke_banner(fifo)), daemon=True)

    reader.start()
    reader.join(timeout=5)
    blocked = reader.is_alive()
    if blocked:  # a writer that opens and closes gives the blocked reader its end of file
        with contextlib.suppress(OSError):
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        reader.join(timeout=5)

    assert not blocked, "smoke_banner blocked on a FIFO with no writer"
    assert found == [None]


def test_a_registration_nested_past_the_recursion_limit_carries_no_banner(tmp_path: Path) -> None:
    nested = tmp_path / "registration.json"
    nested.write_bytes(NESTED)

    assert daily.smoke_banner(nested) is None


def test_a_committed_registration_nested_past_the_recursion_limit_is_step_1_s_refusal(
    tmp_path: Path,
) -> None:
    """Through `main`, as before the banner: admission's own parse fails, and step 1 reports the
    fault as its refusal (exit 1) -- never a traceback from the marker's read, and no banner."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    registration = repo / "registration.json"
    registration.write_bytes(NESTED)
    commit_file(repo, registration, "register", at=tds.COMMITTED)
    arguments = ["--runtime-dir", str(tmp_path / "runtime"), "--registration", str(registration)]

    code, out, err = _invoke(
        daily.main, [*arguments, "--repo", str(repo)], clock=lambda: survivor_day.RUN_CLOCK
    )

    assert code == int(daily.DailyExit.step_failed)
    assert out == ""
    assert err[0].startswith("step 1 (registration) failed: an unexpected fault: RecursionError")
    assert daily.SMOKE_BANNER not in err


# --- the evening ---------------------------------------------------------------------------------


def test_a_smoke_evening_in_json_carries_the_banner_as_a_key_and_on_stderr_first_and_last(
    smoke_evening: Evening,
) -> None:
    assert smoke_evening.code == 0, smoke_evening.err
    shown = json.loads(smoke_evening.out)
    assert shown["smoke"] == daily.SMOKE_BANNER
    assert shown["paper"]["outcome"] == "created"
    assert smoke_evening.err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]


def test_the_banner_is_printed_never_filed(smoke_evening: Evening) -> None:
    journal = survivor_day._journal(smoke_evening.world)
    assert "smoke" not in journal["result"]
    text = survivor_day._journal_path(smoke_evening.world).read_text(encoding="utf-8")
    assert "smoke:" not in text


def test_a_smoke_evening_printed_again_as_text_is_bannered_first_and_last(
    world: tds.World,
) -> None:
    """The completed day printed again (no request): the same print path as the evening's."""
    code, out, err = _invoke(
        daily.main,
        _day_arguments(world, survivor_day.DAY_AS_OF),
        clock=lambda: survivor_day.RUN_CLOCK,
    )

    lines = out.splitlines()
    assert code == 0, err
    assert lines[0] == lines[-1] == daily.SMOKE_BANNER
    assert lines[-2].startswith("wording")
    assert any("ORDERS FOR THE OPEN OF D+1" in line for line in lines[1:-1])
    assert sum(line.startswith("  BUY ") for line in lines) == 10
    assert err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]


def test_a_refused_smoke_evening_is_bannered_around_its_refusal_block_and_its_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: Path,  # noqa: F811
) -> None:
    """Step 8 refused: the document as far as it got on stdout, and on stderr the refusal, its
    requests and what it means for the account -- the orders the book still holds -- all inside
    the banner."""
    smoke = _smoke_world(tmp_path, seeded)
    tds._bind(monkeypatch, smoke.repo, smoke.market)
    monkeypatch.setattr(strategy_survivor_book, "evening", survivor_day._refused_evening)
    smoke.market.today = survivor_day.DAY

    code, out, err = _invoke(
        daily.main,
        _day_arguments(smoke, survivor_day.DAY_AS_OF),
        clock=lambda: survivor_day.RUN_CLOCK,
    )

    assert code == 1
    assert err[0] == err[-1] == daily.SMOKE_BANNER
    assert err[1] == survivor_day.INJECTED
    assert survivor_day.NO_NEW_ORDERS in err[1:-1]
    lines = out.splitlines()
    assert lines[0] == lines[-1] == daily.SMOKE_BANNER
    assert any("NONE PLANNED tonight" in line for line in lines)


# --- the morning and the week --------------------------------------------------------------------


@pytest.mark.parametrize("json_output", [False, True])
def test_a_smoke_morning_recheck_is_bannered(world: tds.World, json_output: bool) -> None:
    world.market.today = MORNING_AS_OF.date()
    flags = ["--morning-recheck", *(["--json"] if json_output else [])]

    code, out, err = _invoke(
        daily.main,
        _day_arguments(world, MORNING_AS_OF, *flags),
        clock=lambda: MORNING_AS_OF + timedelta(minutes=5),
    )

    assert code == 0, err
    assert err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]
    if json_output:
        assert json.loads(out)["smoke"] == daily.SMOKE_BANNER
    else:
        lines = out.splitlines()
        assert lines[0] == lines[-1] == daily.SMOKE_BANNER
        assert lines[1].startswith("morning re-check")
    morning = survivor_day._journal_path(world).with_name("2026-04-06.morning.json")
    assert "smoke" not in json.loads(morning.read_text(encoding="utf-8"))


@pytest.mark.parametrize("json_output", [False, True])
def test_a_smoke_weekly_report_is_bannered_and_the_report_it_writes_is_not(
    world: tds.World, json_output: bool
) -> None:
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", REPORT_AS_OF.isoformat()]
    arguments += ["--json"] if json_output else []

    code, out, err = _invoke(forward_report.main, arguments, clock=lambda: REPORT_AS_OF)

    assert code == 0, err
    assert err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]
    written = world.runtime.resolve() / "reports" / survivor_report.report_name(REPORT_AS_OF)
    assert "smoke" not in json.loads(written.read_text(encoding="utf-8"))
    if json_output:
        assert json.loads(out)["smoke"] == daily.SMOKE_BANNER
    else:
        lines = out.splitlines()
        assert lines[0] == lines[-1] == daily.SMOKE_BANNER
        assert lines[-2] == f"written to {written}"


# --- every other print path ----------------------------------------------------------------------

P6_SMOKE_SETTINGS: Final[dict[str, Any]] = {**tds.grid.protocol_settings(), "smoke_run": MARKER}
"""A P6 registration's measurement settings with the marker: a smoke registration of the P6
configuration, whose day prints the P6 summary (`daily_selection.summary_lines`)."""


def _injected_fault(*_arguments: Any, **_keywords: Any) -> Any:
    raise RuntimeError("an injected fault that no step anticipated")


def _marked_file(root: Path) -> Path:
    """A file that carries the marker and nothing else: enough for the banner, which is read
    before anything is admitted."""
    path = root / "registration.json"
    path.write_text(json.dumps({"settings": {"smoke_run": MARKER}}), encoding="utf-8")
    return path


def test_a_refused_smoke_evening_in_json_carries_the_banner_under_its_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: Path,  # noqa: F811
) -> None:
    """Step 8 refused with `--json`: the document as far as it got carries the banner, as the
    refusal block does in text."""
    smoke = _smoke_world(tmp_path, seeded)
    tds._bind(monkeypatch, smoke.repo, smoke.market)
    monkeypatch.setattr(strategy_survivor_book, "evening", survivor_day._refused_evening)
    smoke.market.today = survivor_day.DAY

    code, out, err = _invoke(
        daily.main,
        _day_arguments(smoke, survivor_day.DAY_AS_OF, "--json"),
        clock=lambda: survivor_day.RUN_CLOCK,
    )

    assert code == 1
    assert err[0] == err[-1] == daily.SMOKE_BANNER
    assert err[1] == survivor_day.INJECTED
    shown = json.loads(out)
    assert shown["smoke"] == daily.SMOKE_BANNER
    assert shown["paper_skipped"]["reason"] == survivor_day.INJECTED


def test_a_smoke_registration_of_a_p6_configuration_banners_the_p6_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`test_daily_selection`'s January world -- its configuration, its seeded panel -- under a
    smoke registration: the P6 day's text is bannered first and last, as a survivor day's is."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            tds,
            "_repository",
            lambda root, config: survivor_report._repository(
                root, config, settings=P6_SMOKE_SETTINGS
            ),
        )
        p6 = tds._world(tmp_path, monkeypatch, tds.Market(open_days=tds.OPEN_2026))
    p6.market.today = tds.DAY
    targets = [argument for target in tds.TARGETS for argument in ("--dataset", target)]

    code, out, err = _invoke(
        daily.main, _day_arguments(p6, tds.DAY_AS_OF, *targets), clock=lambda: tds.RUN_CLOCK
    )

    lines = out.splitlines()
    assert code == 0, err
    assert lines[0] == lines[-1] == daily.SMOKE_BANNER
    assert lines[1].startswith("session            2026-01-19")
    assert lines[-2].startswith("wording")
    assert err[0] == err[-1] == daily.SMOKE_BANNER


@pytest.mark.parametrize("morning", [False, True])
def test_a_fault_no_step_anticipated_still_closes_the_daily_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, morning: bool
) -> None:
    """The closing banner is printed in a `finally`: a fault that escapes `main` still leaves
    both banners on stderr (its traceback, printed by the interpreter, comes after them)."""
    registration = _marked_file(tmp_path)
    monkeypatch.setattr(daily, "run_daily_selection", _injected_fault)
    monkeypatch.setattr(daily, "run_morning_recheck", _injected_fault)
    arguments = ["--runtime-dir", str(tmp_path / "runtime"), "--registration", str(registration)]
    arguments += ["--repo", str(tmp_path), *(["--morning-recheck"] if morning else [])]
    out, err = io.StringIO(), io.StringIO()

    with (
        pytest.raises(RuntimeError, match="no step anticipated"),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        daily.main(arguments, clock=lambda: MORNING_AS_OF)

    assert err.getvalue().splitlines() == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]
    assert out.getvalue() == ""


def test_a_fault_no_refusal_anticipated_still_closes_the_weekly_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registration = _marked_file(tmp_path)
    monkeypatch.setattr(forward_report, "forward_report", _injected_fault)
    arguments = ["--runtime-dir", str(tmp_path / "runtime"), "--registration", str(registration)]
    arguments += ["--repo", str(tmp_path), "--as-of", REPORT_AS_OF.isoformat()]
    out, err = io.StringIO(), io.StringIO()

    with (
        pytest.raises(RuntimeError, match="no step anticipated"),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        forward_report.main(arguments, clock=lambda: REPORT_AS_OF)

    assert err.getvalue().splitlines() == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]
    assert out.getvalue() == ""
