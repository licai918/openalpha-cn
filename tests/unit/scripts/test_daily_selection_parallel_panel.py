"""`scripts/daily_selection.py`: A's parallel book reads the base book's panel read-only
(`V2-P7-027`, the shared-panel plan of 2026-11-15).

A parallel registration -- one whose `settings` carry `survivor_parallel_of` -- runs from its own
runtime directory but must **not** build a second panel: it reads the base book's `panel/`
through `--panel-runtime-dir`, makes zero Tushare requests, never writes that panel and never
clones it. Read-only is a property of the registration, not a flag (R-S1): a base registration
refuses the option so its command line, journal bytes and plist stay exactly what they were
(R-N4), and a parallel registration always runs read-only. The evening replaces step 2's fetch
and step 4's factor builds with a readiness check against the stored panel at the pinned
`as_of` (R-S3). A refusal says the base book's evening has not completed and names the base
runtime directory.

The panels here are generated: `test_daily_selection_survivor_day.py`'s scripted 120-security
market and its seeded panel, which the base-book fixtures extend through 6 April exactly as a
base evening's steps 2 and 4 would leave it -- the panel fetched through the session and the
four composite factors built at its 16:30. No request reaches the network: every parallel run's
`cli._panel_transport` is a tripwire that raises on any request, so a zero-request run is
measured rather than assumed. The registration is the design book's v1 configuration: this file
measures the panel-sharing mechanics, not A's veto (whose window is `test_strategy_survivor_book`
and the survivor-day tests' subject); a v1 book reads no veto at all, so a zero-request evening
is the panel read alone.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as sd

# `test_daily_selection_survivor_day.py`'s seeded panel, built once for that module (pytest takes
# a fixture by name); this module extends it instead of paying for a second seed.
from test_daily_selection_survivor_day import seeded  # noqa: F401  (a module fixture, reused)

from openalpha_cn import cli
from openalpha_cn.backtest.paper_state import held_head
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.storage.paper_state import FilePaperStateStore

daily = tds.daily
DAY: Final[date] = sd.DAY
"""6 April 2026: the parallel book's first evening after its epoch (3 April)."""
DAY_AS_OF: Final[datetime] = sd.DAY_AS_OF
"""The base book's evening and the parallel book's pinned clock for `DAY`: 18:30 Shanghai."""
RUN_CLOCK: Final[datetime] = sd.RUN_CLOCK
"""The command's own clock on the evening: five minutes after the pinned one."""
PARALLEL_SETTINGS: Final[dict[str, object]] = {
    **tds.grid.protocol_settings(),
    daily.SURVIVOR_PARALLEL_OF_KEY: {
        "path": "docs/research/p7-registration.json",
        "sha256": "0" * 64,
    },
}
"""A parallel registration's measurement settings: the protocol's, plus the block naming the
base book. The day reads that block only to know this is the parallel book; the panel location
is the operational `--panel-runtime-dir`, never registered content (R-S7)."""


class Tripwire:
    """A transport that records and refuses every request: a run that asks for data is red, not
    silently serviced by the scripted market."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(str(payload.get("api_name")))
        raise AssertionError(f"the parallel book made a Tushare request: {payload.get('api_name')}")


def _tree(root: Path) -> dict[str, str]:
    """Every file under `root`, by path, with the sha256 of its bytes."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _bind(monkeypatch: pytest.MonkeyPatch, repo: Path, market: Any, transport: Any) -> None:
    """`test_daily_selection._bind`'s bindings, with the transport a tripwire."""
    tds._bind(monkeypatch, repo, market)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)


def _parallel_repository(root: Path, config: Mapping[str, Any]) -> tuple[Path, Path]:
    """A committed survivor registration of `config` carrying `survivor_parallel_of`, in a
    throwaway repository built by `test_daily_selection._repository`."""
    repo, base = tds._repository(root, config)
    registration = repo / "docs" / "research" / "p7-parallel-registration.json"
    tds.registry.register(
        config,
        tds.CRITERIA,
        registration,
        code_commit=tds.head(repo),
        settings=PARALLEL_SETTINGS,
    )
    tds.commit_file(repo, registration, "register the parallel book", at=tds.COMMITTED)
    assert base.is_file()
    return repo, registration


def _parallel_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tds.World:
    """The parallel book's world: its own (empty) runtime directory, its own registration, and
    the scripted market bound where `test_daily_selection` binds it."""
    repo, registration = _parallel_repository(tmp_path, sd.CONFIG)
    market = sd.SurvivorMarket(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, repo, market)
    return tds.World(
        repo=repo, registration=registration, runtime=tmp_path / "runtime", market=market
    )


def _invoke(arguments: Sequence[str], *, clock: datetime) -> tuple[int, str, str]:
    """`daily.main` on `arguments`, its stdout and stderr captured rather than printed."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = daily.main(arguments, clock=lambda: clock)
    return code, out.getvalue(), err.getvalue()


def _day_arguments(
    world: tds.World, *, panel_runtime_dir: Path | None, as_of: datetime
) -> list[str]:
    arguments = [
        "--runtime-dir",
        str(world.runtime),
        "--registration",
        str(world.registration),
        "--repo",
        str(world.repo),
        "--as-of",
        as_of.isoformat(),
        "--json",
    ]
    if panel_runtime_dir is not None:
        arguments += ["--panel-runtime-dir", str(panel_runtime_dir)]
    return arguments


def _config_id(world: tds.World) -> str:
    return str(json.loads(world.registration.read_text(encoding="utf-8"))["config_id"])


def _journal_path(world: tds.World) -> Path:
    return world.runtime / "daily_survivor" / _config_id(world)[:16] / f"{DAY.isoformat()}.json"


def _journal(world: tds.World) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(_journal_path(world).read_text(encoding="utf-8"))
    return body


def _targets() -> tuple[str, ...]:
    return daily.targets_for_survivor(daily.survivor_builds(sd._survivor()))


# --- the base book's completed evening, and its morning (once per module) ------------------------


def _panel_build(runtime: Path, market: Any, *, as_of: datetime, datasets: Sequence[str]) -> None:
    """`panel build --incremental` as the base book's evening and morning run it: through the
    scripted transport and the tests' wall clock."""
    market.today = as_of.astimezone(daily.SHANGHAI).date()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", "2026"]
        arguments += ["--as-of", as_of.isoformat(), "--exchange", "SSE", "--incremental", "--json"]
        for dataset in datasets:
            arguments += ["--dataset", dataset]
        built = daily.invoke(arguments)
    assert built.exit_code == 0, built.reason()


@pytest.fixture(scope="module")
def base_panel(
    tmp_path_factory: pytest.TempPathFactory,
    seeded: Path,  # noqa: F811  (the reused fixture)
) -> Path:
    """The base book's panel as its 6 April evening leaves it: fetched through the session and
    the four composite factors built at the session's 16:30 (`_refuse_unready_panel` reads both,
    and `panel doctor` reads the datasets)."""
    runtime = tmp_path_factory.mktemp("parallel-base") / "runtime"
    shutil.copytree(seeded, runtime)
    market = sd.SurvivorMarket(open_days=tds.OPEN_2026)
    _panel_build(runtime, market, as_of=DAY_AS_OF, datasets=_targets())
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        arguments = ["factor", "build", "--runtime-dir", str(runtime)]
        for factor in sd.BOOK.composite_factors:
            arguments += ["--factor", factor]
        arguments += [
            "--tier",
            "processed",
            "--transform",
            str(sd.CONFIG["adapter"]["transform"]),
            "--year",
            "2026",
            "--exchange",
            "SSE",
            "--max-staleness-days",
            "30",
            "--as-of",
            session_publication_instant(DAY).isoformat(),
            "--json",
        ]
        built = daily.invoke(arguments)
    assert built.exit_code == 0, built.reason()
    return runtime


@dataclass(frozen=True)
class ParallelEvening:
    """The parallel book's first evening, run once: its world, its printed result, the base
    runtime directory it read, and the measurements taken around the run."""

    world: tds.World
    base_runtime: Path
    result: dict[str, Any]
    base_before: dict[str, str]
    base_after: dict[str, str]
    calls: list[str]


@pytest.fixture(scope="module")
def parallel_evening(tmp_path_factory: pytest.TempPathFactory, base_panel: Path) -> ParallelEvening:
    """6 April's evening for the parallel book: its own empty runtime directory, the base book's
    completed panel as `--panel-runtime-dir`, and a tripwire where its provider would be."""
    root = tmp_path_factory.mktemp("parallel-evening")
    tripwire = Tripwire()
    market = sd.SurvivorMarket(open_days=tds.OPEN_2026)
    with pytest.MonkeyPatch.context() as patch:
        repo, registration = _parallel_repository(root, sd.CONFIG)
        _bind(patch, repo, market, tripwire)
        world = tds.World(
            repo=repo, registration=registration, runtime=root / "runtime", market=market
        )
        before = _tree(base_panel)
        code, out, err = _invoke(
            _day_arguments(world, panel_runtime_dir=base_panel, as_of=DAY_AS_OF), clock=RUN_CLOCK
        )
        after = _tree(base_panel)
    assert code == 0, err
    return ParallelEvening(
        world=world,
        base_runtime=base_panel,
        result=json.loads(out),
        base_before=before,
        base_after=after,
        calls=tripwire.calls,
    )


# --- R-S2: the option is the parallel book's, and required --------------------------------------


def test_a_base_registration_refuses_the_panel_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-S2/R-N4: a survivor registration without `survivor_parallel_of` is the base book, whose
    panel is its own `--runtime-dir`. `--panel-runtime-dir` is refused by name, exit 2, before
    any request, write or directory."""
    world = sd._world(tmp_path, monkeypatch, None, config=sd.CONFIG)
    base_elsewhere = tmp_path / "base-elsewhere"

    code, out, err = _invoke(
        _day_arguments(world, panel_runtime_dir=base_elsewhere, as_of=DAY_AS_OF),
        clock=RUN_CLOCK,
    )

    assert code == int(daily.DailyExit.no_registration) == 2
    assert out == ""
    assert "--panel-runtime-dir" in err
    assert "parallel book's option" in err
    assert world.market.payloads == []
    assert not world.runtime.exists()
    assert not base_elsewhere.exists()


def test_a_parallel_registration_requires_the_panel_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-S2: the parallel book reads another book's panel, so it must be told which one: without
    `--panel-runtime-dir` it exits 2 by name, before any request or directory."""
    world = _parallel_world(tmp_path, monkeypatch)
    settings = json.loads(world.registration.read_text(encoding="utf-8"))["settings"]
    assert daily.SURVIVOR_PARALLEL_OF_KEY in settings

    code, out, err = _invoke(
        _day_arguments(world, panel_runtime_dir=None, as_of=DAY_AS_OF), clock=RUN_CLOCK
    )

    assert code == int(daily.DailyExit.no_registration) == 2
    assert out == ""
    assert "requires --panel-runtime-dir" in err
    assert world.market.payloads == []
    assert not world.runtime.exists()


def test_the_parallel_panel_must_not_be_the_books_own_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-S2: the paper book, journals and `state.sqlite3` stay in `--runtime-dir`; the panel is
    read from another directory, so the same path is refused, exit 2, by name."""
    world = _parallel_world(tmp_path, monkeypatch)

    code, out, err = _invoke(
        _day_arguments(world, panel_runtime_dir=world.runtime, as_of=DAY_AS_OF),
        clock=RUN_CLOCK,
    )

    assert code == int(daily.DailyExit.no_registration) == 2
    assert out == ""
    assert "is this run's own --runtime-dir" in err
    assert world.market.payloads == []
    assert not world.runtime.exists()


def test_a_p6_registration_refuses_the_panel_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-S1/R-S2: read-only is not a switch and the panel location is not a P6 option -- a P6
    registration (the paused book) refuses `--panel-runtime-dir` by name, exit 2, before any
    request or directory."""
    repo, registration = tds._repository(tmp_path, tds.CONFIG)
    market = tds.Market(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, repo, market)
    world = tds.World(
        repo=repo, registration=registration, runtime=tmp_path / "runtime", market=market
    )

    code, out, err = _invoke(
        _day_arguments(world, panel_runtime_dir=tmp_path / "base-runtime", as_of=DAY_AS_OF),
        clock=RUN_CLOCK,
    )

    assert code == int(daily.DailyExit.no_registration) == 2
    assert out == ""
    assert "parallel book's option" in err
    assert world.market.payloads == []
    assert not world.runtime.exists()


def test_the_modes_that_read_no_other_book_panel_refuse_the_option() -> None:
    """R-S1/R-S2: `--factor-gaps` reads its own runtime's panel and `--pin-worktree` reads no
    panel at all, so the parallel book's panel location is a usage error there (exit 2), never a
    silently ignored option."""
    for mode in (["--factor-gaps", "2026-01-05"], ["--pin-worktree", "/nonexistent/pinned"]):
        with pytest.raises(SystemExit) as refused:
            daily.main(
                [
                    "--runtime-dir",
                    "/nonexistent/runtime",
                    "--registration",
                    "/nonexistent/registration.json",
                    "--panel-runtime-dir",
                    "/nonexistent/base",
                    *mode,
                ]
            )
        assert refused.value.code == 2


def test_there_is_no_read_only_switch() -> None:
    """R-S1: read-only is the registration's property, and there is no `--panel-readonly` flag a
    script could forget or apply to the base book -- the command refuses the name outright."""
    with pytest.raises(SystemExit) as refused:
        daily.main(
            [
                "--runtime-dir",
                "/nonexistent/runtime",
                "--registration",
                "/nonexistent/registration.json",
                "--panel-readonly",
            ]
        )

    assert refused.value.code == 2


# --- R-S3/R-S5: the read-only evening ----------------------------------------------------------


def test_a_parallel_evening_reads_the_base_panel_and_writes_nothing_there(
    parallel_evening: ParallelEvening,
) -> None:
    """R-S3/R-S5: with the base book's evening completed for 6 April the parallel evening runs,
    makes zero provider calls, leaves the base panel byte-for-byte unchanged, writes its paper
    book in its own runtime directory -- which holds no panel at all -- and journals
    `panel_mode: "read_only"` with the panel directory it read."""
    world = parallel_evening.world
    result = parallel_evening.result

    assert parallel_evening.calls == []
    assert result["this_run"]["requests"] == {}
    assert result["this_run"]["request_count"] == 0
    assert result["panel"]["read_only"] is True
    assert result["panel"]["targets"] == list(_targets())
    assert result["panel"]["requests"] == {}
    assert result["factors"]["built"] == []
    assert sorted(result["factors"]["already_built"]) == sorted(
        f"{build.factor}@{build.tier}" for build in daily.survivor_builds(sd._survivor())
    )
    # The base book's panel is untouched, and the parallel runtime holds no panel of its own:
    # no second panel, no `cp -c` clone.
    assert parallel_evening.base_after == parallel_evening.base_before
    assert not (world.runtime / "panel").exists()
    # The paper book is this book's, in its own runtime directory.
    marker = json.loads((world.runtime / "paper" / "registration.json").read_text("utf-8"))
    assert marker["config_id"] == _config_id(world)
    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None and head.session == DAY
    # The journal, beside the book, records the mode and the panel directory (R-S5); a base
    # journal carries neither key, whose bytes an existing test pins.
    journal = _journal(world)
    assert journal["panel_mode"] == daily.PANEL_MODE_READ_ONLY
    assert journal["panel_runtime_dir"] == str(parallel_evening.base_runtime)
    assert journal["panel"]["read_only"] is True
    assert journal["panel"]["requests"] == {}
    assert journal["factors"]["built"] == []
    assert "result" in journal


def test_a_parallel_evening_refuses_a_base_panel_missing_the_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: Path,  # noqa: F811
) -> None:
    """R-S3: a base panel that has not fetched the session refuses by name, exit 1 -- naming the
    missing dataset and the base runtime directory -- before the paper book's lock is even taken,
    so the parallel runtime directory does not exist and the base panel is unchanged."""
    tripwire = Tripwire()
    world = _parallel_world(tmp_path, monkeypatch)
    _bind(monkeypatch, world.repo, world.market, tripwire)
    before = _tree(seeded)

    code, _out, err = _invoke(
        _day_arguments(world, panel_runtime_dir=seeded, as_of=DAY_AS_OF), clock=RUN_CLOCK
    )

    assert code == int(daily.DailyExit.step_failed) == 1
    assert "the base book's evening for the session 2026-04-06 has not completed" in err
    assert "daily" in err  # the dataset the doctor names that the session's data would fill
    assert str(seeded) in err
    assert tripwire.calls == []
    assert not world.runtime.exists()
    assert _tree(seeded) == before


def test_a_parallel_evening_refuses_a_base_panel_missing_a_factor_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_panel: Path
) -> None:
    """R-S3: a base panel whose datasets cover the session but which holds no factor build at
    the session's 16:30 refuses by name, exit 1, naming the missing tier and instant and the
    base runtime directory; nothing is written in the parallel runtime directory."""
    panel = tmp_path / "base-without-factors"
    shutil.copytree(base_panel, panel)
    for directory in (panel / "panel").iterdir():
        if directory.is_dir() and directory.name.startswith("factor_"):
            shutil.rmtree(directory)
    tripwire = Tripwire()
    world = _parallel_world(tmp_path, monkeypatch)
    _bind(monkeypatch, world.repo, world.market, tripwire)

    code, _out, err = _invoke(
        _day_arguments(world, panel_runtime_dir=panel, as_of=DAY_AS_OF), clock=RUN_CLOCK
    )

    assert code == int(daily.DailyExit.step_failed) == 1
    assert "the base book's evening for the session 2026-04-06 has not completed" in err
    assert "residual_vol_60/v1@processed" in err
    assert session_publication_instant(DAY).isoformat() in err
    assert str(panel) in err
    assert tripwire.calls == []
    assert not world.runtime.exists()
