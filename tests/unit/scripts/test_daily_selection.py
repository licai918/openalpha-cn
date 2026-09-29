"""`scripts/daily_selection.py`: the one daily selection command (`V2-P6-011`).

Two halves.

**The command, end to end.** A throwaway git repository holds a committed registration; a panel
is built through the real `openalpha panel build` against a scripted Tushare transport generated
at test time (`daily_market.Market`); then the daily command runs every step for real: the
incremental build through the same transport, the doctor and the dependency gate, the factor
build, the scoring, the book's rule, and the prediction store. Nothing reaches the network
(`tests/conftest.py` refuses outbound connections). Two frames: January 2026, every weekday of
the year open; and 14 December 2026 to 15 January 2027, across New Year.

**The registered score is the backtest's.** On the strategy corpus the view tests use, each
signal day's scores are registered through the command's own path and a backtest that reads
those records is run beside the backtest of the configuration itself: every period's fills,
holdings and returns must be equal, for a static, a trailing-IC and a walk-forward source. That
is what "the score the backtest would have used on that day" means, measured.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import plistlib
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import duckdb
import pytest
from daily_market import DISPUTED_CODE as DISPUTED
from daily_market import L1_CODES, SECURITIES, Market, weekdays
from panel_fixtures import EXCHANGE
from research_repo import commit_file, git, head
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus

from openalpha_cn import cli
from openalpha_cn.backtest.strategy_backtest import EQUAL_WEIGHT_ALL_A
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATASET,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_TREE_DATASET,
)
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_industry_cross_section,
    load_trading_calendar,
    load_upstream_defects,
    session_publication_instant,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import RegisteredConfiguration, signal_day_batch
from openalpha_cn.strategy_view import (
    StrategyRunBlockedError,
    backtest_strategy,
    load_strategy_inputs,
    score_day,
    strategy_request,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str, directory: Path) -> ModuleType:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


registry = _script("registry", ROOT / "scripts" / "research")
daily = _script("daily_selection", ROOT / "scripts")

SECRET_TOKEN: Final[str] = "sk-daily-selection-token-must-not-leak-60011"
YEAR: Final[int] = 2026
OPEN_2026: Final[tuple[date, ...]] = weekdays(date(2026, 1, 5), date(2026, 12, 31))
"""The January frame's calendar: every weekday of 2026 from 5 January, so a label window can be
placed past the newest session with data."""
NEW_YEAR: Final[tuple[date, ...]] = weekdays(
    date(2026, 12, 14), date(2027, 1, 15), closed=(date(2027, 1, 1),)
)
"""The New Year frame's calendar: 2026 opens on 14 December; 1 January 2027 is a holiday."""

SEEDED_AS_OF: Final[str] = "2026-01-13T12:00:00+08:00"
"""The seed build: 12 January has published, 13 January has not."""
DAY: Final[date] = date(2026, 1, 19)
DAY_AS_OF: Final[datetime] = datetime.fromisoformat("2026-01-19T18:30:00+08:00")
RUN_CLOCK: Final[datetime] = datetime.fromisoformat("2026-01-19T18:35:00+08:00")
"""The command's own clock on the day: after the close, long before any outcome."""
TARGETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    "price",
    PRICE_LIMIT_DATASET,
)
"""The price targets; the command's `--dataset` in most tests here."""

CONFIG: Final[dict[str, object]] = {
    "start": date(2026, 1, 12),
    "end": date(2026, 6, 30),
    "combine": "zscore_sum",
    "components": [("reversal_1d/v1", "raw", Decimal("1"))],
    "exchange": "SSE",
    "rebalance_every_sessions": 2,
    "holding_count": 3,
    "buffer_rank": 4,
    "max_industry_weight": None,
}
"""A registered configuration as the research registers one: `run_strategy_backtest`'s keywords.
From 12 January every second session rebalances, so 19 January (the fifth after) does not and 20
January does."""
CAPPED: Final[dict[str, object]] = {
    **CONFIG,
    "buffer_rank": None,
    "max_industry_weight": Decimal("0.34"),
}
"""The same with an industry cap of one name per industry (0.34 x 3 names, rounded down)."""
CRITERIA: Final[dict[str, object]] = {"annualized_net_excess_above": "0"}
BOUND: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
    "scripts/daily_selection.py",
)
LOCK: Final[str] = """version = 1
requires-python = ">=3.11"

[[package]]
name = "openalpha-cn"
version = "1.0.0"
source = { editable = "." }

[[package]]
name = "numpy"
version = "2.3.1"
source = { registry = "https://pypi.org/simple" }
"""
"""The throwaway repository's `uv.lock`: the project and one third-party package."""
LOCKED: Final[dict[str, str]] = {"openalpha-cn": "1.0.0", "numpy": "2.3.1"}
"""The environment `_bind` reports as installed: exactly the lock."""
RUNNING_PYTHON: Final[str] = f"{sys.version_info.major}.{sys.version_info.minor}"
"""The throwaway checkout's `.python-version`: the interpreter running the tests, so every test
that does not ask otherwise is admitted."""
FORKED_LOCK: Final[str] = """version = 1
requires-python = ">=3.11"
resolution-markers = [
    "python_full_version >= '3.12' and sys_platform == 'win32'",
    "python_full_version < '3.12' and sys_platform == 'win32'",
    "python_full_version >= '3.12' and sys_platform != 'win32'",
    "python_full_version < '3.12' and sys_platform != 'win32'",
]

[[package]]
name = "openalpha-cn"
version = "1.0.0"
source = { editable = "." }
dependencies = [
    { name = "numpy", version = "2.4.6", marker = "python_full_version < '3.12'" },
    { name = "numpy", version = "2.5.1", marker = "python_full_version >= '3.12'" },
    { name = "colorama", marker = "sys_platform == 'win32'" },
]

[[package]]
name = "numpy"
version = "2.4.6"
source = { registry = "https://pypi.org/simple" }
resolution-markers = [
    "python_full_version < '3.12' and sys_platform == 'win32'",
    "python_full_version < '3.12' and sys_platform != 'win32'",
]

[[package]]
name = "numpy"
version = "2.5.1"
source = { registry = "https://pypi.org/simple" }
resolution-markers = [
    "python_full_version >= '3.12' and sys_platform == 'win32'",
    "python_full_version >= '3.12' and sys_platform != 'win32'",
]

[[package]]
name = "colorama"
version = "0.4.6"
source = { registry = "https://pypi.org/simple" }
"""
"""A lock with two forks, as uv writes one: numpy 2.4.6 for Pythons before 3.12 and 2.5.1 from
3.12 -- the shape of this repository's own `uv.lock`."""
COMMITTED: Final[datetime] = datetime(2025, 12, 10, 12, 0, tzinfo=UTC)
WALL_CLOCK: Final[datetime] = datetime(2027, 6, 1, 4, 0, tzinfo=UTC)
"""The provider's wall clock: after both frames, so it never binds what a fetch may know."""
INDUSTRY_APIS: Final[frozenset[str]] = frozenset(
    {INDUSTRY_TREE_DATASET, INDUSTRY_MEMBERSHIP_DATASET}
)


@dataclass
class World:
    repo: Path
    registration: Path
    runtime: Path
    market: Market


def _repository(
    root: Path,
    config: Mapping[str, object],
    *,
    lock: str = LOCK,
    python_version: str = RUNNING_PYTHON,
) -> tuple[Path, Path]:
    """A throwaway repository holding one file under each bound path, a `.python-version`, the
    `.venv/` ignore rule the real repository has, and a committed registration of `config`."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "--template=")
    for name in BOUND:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(lock if name == "uv.lock" else "RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    (repo / ".python-version").write_text(f"{python_version}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    git(repo, "add", ".python-version", ".gitignore")
    git(repo, "commit", "-q", "-m", "initial", at=COMMITTED - timedelta(days=1))
    registration = repo / "docs" / "research" / "p6-registration.json"
    registry.register(config, CRITERIA, registration, code_commit=head(repo))
    commit_file(repo, registration, "register", at=COMMITTED)
    return repo, registration


def _bind(monkeypatch: pytest.MonkeyPatch, repo: Path, market: Market) -> None:
    """Point the registry's import checks at `repo` and the panel's transport at `market`."""
    package = repo / "src" / "openalpha_cn" / "__init__.py"
    research = repo / "scripts" / "research"
    monkeypatch.setattr(registry, "_imported_package", lambda: package)
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: market)
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL_CLOCK)
    monkeypatch.setattr(daily, "installed_distributions", lambda: dict(LOCKED))


def _world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    market: Market,
    *,
    config: Mapping[str, object] = CONFIG,
    seeded_as_of: str = SEEDED_AS_OF,
    year: int = YEAR,
) -> World:
    """A committed registration, and a panel seeded through the real `panel build`."""
    repo, registration = _repository(tmp_path, config)
    _bind(monkeypatch, repo, market)
    market.today = datetime.fromisoformat(seeded_as_of).date()
    runtime = tmp_path / "runtime"
    arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(year)]
    arguments += ["--as-of", seeded_as_of, "--json"]
    for target in TARGETS:
        arguments += ["--dataset", target]
    seeded = daily.invoke(arguments)
    assert seeded.exit_code == 0, seeded.reason()
    market.clear()
    return World(repo=repo, registration=registration, runtime=runtime, market=market)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return _world(tmp_path, monkeypatch, Market(open_days=OPEN_2026))


def _run(
    world: World,
    capsys: pytest.CaptureFixture[str],
    *,
    as_of: datetime = DAY_AS_OF,
    clock: datetime = RUN_CLOCK,
    json_output: bool = True,
    targets: Sequence[str] | None = TARGETS,
    extra: Sequence[str] = (),
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[int, Any, str]:
    if monkeypatch is not None:
        _bind(monkeypatch, world.repo, world.market)
    world.market.today = as_of.astimezone(daily.SHANGHAI).date()
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", as_of.isoformat(), *extra]
    for target in targets or ():
        arguments += ["--dataset", target]
    if json_output:
        arguments.append("--json")
    code = daily.main(arguments, clock=lambda: clock)
    out, err = capsys.readouterr()
    assert SECRET_TOKEN not in out + err
    body = json.loads(out) if json_output and code == 0 else out
    return code, body, err


def _catalog(runtime: Path) -> list[tuple[Any, ...]]:
    """Every registered partition with its content hash and write time."""
    with duckdb.connect(str(runtime / "panel" / "catalog.duckdb"), read_only=True) as connection:
        return sorted(
            connection.execute(
                "SELECT dataset, year, content_hash, written_at FROM panel_partitions"
            ).fetchall()
        )


def _files(runtime: Path) -> dict[str, str]:
    """Every file under the runtime directory, by path, with the sha256 of its bytes."""
    return {
        path.relative_to(runtime).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(runtime.rglob("*"))
        if path.is_file()
    }


def _records(runtime: Path) -> tuple[str, ...]:
    return FilePredictionStore(runtime / "predictions", clock=lambda: RUN_CLOCK).list_ids()


def _journal(world: World, day: date) -> Path:
    config_id = json.loads(world.registration.read_text(encoding="utf-8"))["config_id"]
    return world.runtime / "daily_selection" / config_id[:16] / f"{day.isoformat()}.json"


def _evening(day: date, *, hours: int = 2) -> datetime:
    return session_publication_instant(day) + timedelta(hours=hours)


# --- the registration --------------------------------------------------------------------------


def test_a_missing_registration_exits_2_and_names_the_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = daily.main(
        [
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--registration",
            str(tmp_path / "docs" / "research" / "p6-registration.json"),
            "--repo",
            str(tmp_path),
        ]
    )
    _out, err = capsys.readouterr()

    assert code == 2
    assert "step 1 (registration) failed" in err
    assert not (tmp_path / "runtime").exists()


def test_code_that_moved_on_from_the_registered_commit_is_refused_before_any_request(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The daily command binds the registration's code, this script included."""
    script = world.repo / "scripts" / "daily_selection.py"
    script.write_text("RULES = 2\n", encoding="utf-8")
    commit_file(world.repo, script, "change the daily command", at=COMMITTED + timedelta(days=1))
    before = _files(world.runtime)

    code, _body, err = _run(world, capsys)

    assert code == 3
    assert "step 1 (registration) failed: SourceChangedError" in err
    assert "scripts/daily_selection.py" in err
    assert world.market.payloads == []
    assert _files(world.runtime) == before


# --- one day, end to end -----------------------------------------------------------------------


def test_the_day_runs_every_step_and_registers_a_forward_prediction(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, result, err = _run(world, capsys)

    assert code == 0, err
    assert result["session"] == DAY.isoformat()
    # Step 2: 12 January again (the overlap) and the five sessions after it, five APIs each,
    # plus the calendar and the registry.
    assert result["this_run"]["request_count"] == 2 + 5 * 6
    assert sorted(result["this_run"]["requests"]) == sorted(
        [
            ADJ_FACTOR_DATASET,
            DAILY_DATASET,
            DAILY_BASIC_DATASET,
            PRICE_LIMIT_DATASET,
            STOCK_BASIC_DATASET,
            SUSPENSION_DATASET,
            TRADING_CALENDAR_DATASET,
        ]
    )
    assert result["factors"]["built"] == ["reversal_1d/v1@raw"]
    listed = result["candidates"]
    assert listed["held"] is False
    assert listed["candidate_list_id"].startswith("dsl_")
    assert len(listed["candidates"]) == 4  # the buffer band
    targets = result["targets"]
    assert (targets["decision"], targets["reason"]) == ("rebalanced", "the first day: no book yet")
    assert list(targets["weights"]) == sorted(row["ts_code"] for row in listed["candidates"][:3])
    assert set(targets["weights"].values()) == {"0.3333333333"}
    prediction = result["prediction"]
    assert prediction["registered"] is True
    assert prediction["standing"] == "forward"
    assert prediction["recorded_at"] < prediction["outcome_known_at"]
    assert prediction["scored"] == len(SECURITIES)
    assert _records(world.runtime) == (prediction["record_id"],)


def test_the_scores_filed_after_the_close_are_the_days_scores_verbatim(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The record filed at 18:35 carries exactly `SignalDayScores.scores` for the day: scored
    here again, from the store the command left, at the day's journalled clock."""
    code, result, err = _run(world, capsys)
    assert code == 0, err

    request = daily.day_request(CONFIG, day=DAY, as_of=datetime.fromisoformat(result["as_of"]))
    signal = score_day(
        PanelStore(world.runtime / "panel"), request, day=DAY, anchor=date(2026, 1, 12)
    )
    record = FilePredictionStore(world.runtime / "predictions", clock=lambda: RUN_CLOCK).get(
        result["prediction"]["record_id"]
    )

    assert record is not None
    assert record.recorded_at == RUN_CLOCK
    assert {row.ts_code: row.score for row in record.batch.scored} == dict(signal.scores.scores)


def test_the_terminal_summary_names_the_day_the_list_the_book_the_record_and_the_requests(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, err = _run(world, capsys, json_output=False)

    assert code == 0, err
    for label in (
        "session            2026-01-19",
        "candidate list     dsl_",
        "target weights     rebalanced (the first day: no book yet), 3 name(s)",
        "prediction         prd_",
        "forward",
        "tushare requests   32 this run",
        "not an order",
    ):
        assert label in text


def test_a_second_run_of_the_same_day_writes_nothing_and_asks_the_network_nothing(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, first, err = _run(world, capsys)
    assert code == 0, err
    files = _files(world.runtime)
    world.market.clear()

    code, second, err = _run(world, capsys, clock=RUN_CLOCK + timedelta(hours=1))

    assert code == 0, err
    assert world.market.payloads == []
    assert second["this_run"] == {"requests": {}, "request_count": 0, "wrote": False}
    assert _files(world.runtime) == files
    for key in ("candidates", "targets", "prediction"):
        assert second[key] == first[key]


def test_a_rerun_after_the_journal_was_lost_files_no_new_partition_list_or_record(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every step is idempotent on its own, not only through the journal: the same `--as-of`
    rebuilds byte-equal partitions, the factor tier is already built at the instant, and the
    prediction registered for the day under the same declaration is the day's record."""
    code, first, err = _run(world, capsys)
    assert code == 0, err
    catalog, records = _catalog(world.runtime), _records(world.runtime)
    _journal(world, DAY).unlink()

    code, again, err = _run(world, capsys, clock=RUN_CLOCK + timedelta(hours=1))

    assert code == 0, err
    # The store's horizon is the day now, so the rebuild asks for that one session again.
    assert again["this_run"]["request_count"] == 2 + 5
    assert first["this_run"]["request_count"] == 2 + 5 * 6
    assert _catalog(world.runtime) == catalog
    assert _records(world.runtime) == records
    assert again["prediction"]["outcome"] == "unchanged"
    assert again["factors"]["built"] == []
    assert again["factors"]["already_built"] == ["reversal_1d/v1@raw"]
    assert again["candidates"] == first["candidates"]


def test_a_day_whose_panel_step_is_journalled_resumes_after_it(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, first, err = _run(world, capsys)
    assert code == 0, err
    journal = _journal(world, DAY)
    body = json.loads(journal.read_text(encoding="utf-8"))
    del body["result"]
    journal.write_text(json.dumps(body), encoding="utf-8")
    world.market.clear()

    code, again, err = _run(world, capsys, as_of=DAY_AS_OF + timedelta(hours=2))

    assert code == 0, err
    assert world.market.payloads == []
    assert again["as_of"] == first["as_of"]  # the journalled clock, not this run's
    assert again["prediction"]["record_id"] == first["prediction"]["record_id"]


def _next(world: World, capsys: pytest.CaptureFixture[str], days: int) -> Any:
    code, result, err = _run(
        world,
        capsys,
        as_of=DAY_AS_OF + timedelta(days=days),
        clock=RUN_CLOCK + timedelta(days=days),
    )
    assert code == 0, err
    return result


def test_the_next_session_rebalances_from_the_book_the_day_before_held(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """20 January is a rebalance on the schedule from 12 January; 21 January is not, so its
    book is the 20th's whatever the ranking says."""
    first = _next(world, capsys, 0)
    held = first["targets"]["weights"]
    second = _next(world, capsys, 1)
    assert (second["targets"]["decision"], second["targets"]["reason"]) == (
        "rebalanced",
        "scheduled",
    )
    assert second["targets"]["previous_session"] == DAY.isoformat()
    ranked = [row["ts_code"] for row in second["candidates"]["candidates"]]
    kept = {name for name in held if name in ranked[:4]}
    assert kept <= set(second["targets"]["weights"])
    assert len(second["targets"]["weights"]) == 3

    third = _next(world, capsys, 2)
    assert third["targets"]["decision"] == "not a rebalance day"
    assert third["targets"]["weights"] == second["targets"]["weights"]
    assert third["targets"]["turnover"] == "0.0000000000"
    assert len(_records(world.runtime)) == 3


def test_a_scheduled_rebalance_the_command_missed_is_made_at_its_next_run(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """No run on 20 January, a scheduled rebalance: 21 January (not scheduled) rebalances in its
    place, rather than holding 19 January's book until 22 January. 22 January then rebalances on
    schedule, and 23 January holds."""
    _next(world, capsys, 0)
    caught_up = _next(world, capsys, 2)
    assert (caught_up["targets"]["decision"], caught_up["targets"]["reason"]) == (
        "rebalanced",
        "catching up the rebalance scheduled on 2026-01-20",
    )
    assert caught_up["targets"]["previous_session"] == DAY.isoformat()
    on_schedule = _next(world, capsys, 3)
    assert on_schedule["targets"]["reason"] == "scheduled"
    assert _next(world, capsys, 4)["targets"]["decision"] == "not a rebalance day"


def test_a_panel_the_doctor_does_not_clear_stops_the_run_before_any_factor_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = _world(tmp_path, monkeypatch, Market(open_days=OPEN_2026, disputed=DAY))

    code, _text, err = _run(world, capsys)

    assert code == 1
    assert "step 3 (panel doctor) failed" in err
    # The doctor's own verdict, named: the gate would refuse this panel too, and a run that
    # stopped only there would have skipped the check this step is named for.
    assert "`panel doctor` is not clean (exit 1)" in err
    assert f"return_path_disagreement: {DISPUTED} on {DAY.isoformat()}" in err
    store = PanelStore(world.runtime / "panel")
    assert store.registered_years("factor_obs_reversal_1d_v1") == ()
    assert _records(world.runtime) == ()
    assert "result" not in json.loads(_journal(world, DAY).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "clock",
    [
        pytest.param(RUN_CLOCK + timedelta(days=30), id="a month late"),
        pytest.param(
            datetime.fromisoformat("2026-01-20T09:15:00+08:00"), id="as the call auction starts"
        ),
    ],
)
def test_scores_registered_once_the_next_call_auction_has_started_are_refused(
    world: World, capsys: pytest.CaptureFixture[str], clock: datetime
) -> None:
    """The book trades the day's scores at the next open, whose price the call auction from 09:15
    fixes: from then on nothing is filed, even while the store would still call it forward."""
    code, _text, err = _run(world, capsys, clock=clock)

    assert code == 1
    assert "step 7 (prediction) failed" in err
    assert "2026-01-20T09:15:00+08:00" in err
    assert "Nothing was filed" in err
    assert _records(world.runtime) == ()


def test_scores_registered_before_the_next_call_auction_are_forward(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, result, err = _run(
        world, capsys, clock=datetime.fromisoformat("2026-01-20T09:14:00+08:00")
    )

    assert code == 0, err
    assert result["prediction"]["standing"] == "forward"


def _band_withdrawn_after_the_seed(world: World) -> None:
    """The live check's shape: a band the upstream served for the stored horizon's session (12
    January) and no longer serves."""
    arguments = ["panel", "build", "--runtime-dir", str(world.runtime), "--year", str(YEAR)]
    arguments += ["--as-of", SEEDED_AS_OF, "--dataset", PRICE_LIMIT_DATASET]
    world.market.bands_only = {date(2026, 1, 12): ("159999.SZ",)}
    reseeded = daily.invoke(arguments)
    assert reseeded.exit_code == 0, reseeded.reason()
    world.market.bands_only = {}
    world.market.clear()


def test_a_band_the_upstream_withdrew_is_recorded_and_the_day_clears(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """What stopped the live check at step 2 is, on V2-P6-016, a recorded withdrawal: the overlap
    re-fetch of 12 January lacks the stored band, a second whole-session answer lacks it too, and
    the band is kept whole in `withdrawn_stk_limit` and indexed in `upstream_defects` while the
    day goes on. The second answer is one request more."""
    _band_withdrawn_after_the_seed(world)

    code, result, err = _run(world, capsys)

    assert code == 0, err
    assert result["this_run"]["requests"][PRICE_LIMIT_DATASET] == 6 + 1
    store = PanelStore(world.runtime / "panel")
    kept = store.read_coverage("withdrawn_stk_limit", YEAR)
    assert kept is not None and kept.subjects == ("159999.SZ",)
    (record,) = load_upstream_defects(store, years=(YEAR,), as_of=DAY_AS_OF)
    assert (record.ts_code, record.kind) == ("159999.SZ", "withdrawn_after_publication")


def test_a_refused_panel_update_stops_the_day_counts_its_requests_and_pins_its_clock(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused step 2 on V2-P6-016: the band's absence is not confirmed -- the second answer
    for 12 January is not the first -- so nothing is recorded or written. The command stops at
    step 2, says so with the requests it spent (the second answer included), and a retry asks the
    same question at the same pinned `--as-of`."""
    store = PanelStore(world.runtime / "panel")
    _band_withdrawn_after_the_seed(world)
    world.market.unsteady_bands = {date(2026, 1, 12): ("159998.SZ",)}

    code, _text, err = _run(world, capsys)

    assert code == 1
    assert "step 2 (panel update) failed" in err
    assert "The two answers disagree" in err
    assert "tushare requests   33 this run" in err
    journal = json.loads(_journal(world, DAY).read_text(encoding="utf-8"))
    assert (journal["as_of"], "panel" in journal) == (DAY_AS_OF.isoformat(), False)
    assert store.registered_years("factor_obs_reversal_1d_v1") == ()

    code, _text, err = _run(world, capsys, as_of=DAY_AS_OF + timedelta(hours=3))

    assert code == 1
    assert "The two answers disagree" in err
    assert json.loads(_journal(world, DAY).read_text(encoding="utf-8"))["as_of"] == (
        DAY_AS_OF.isoformat()
    )


def test_a_fault_no_step_anticipated_is_that_steps_refusal_with_its_requests(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("a library fault")

    monkeypatch.setattr(daily, "build_factors", broken)

    code, _text, err = _run(world, capsys)

    assert code == 1
    assert "step 4 (factor build) failed: an unexpected fault: RuntimeError: a library fault" in err
    assert "tushare requests   32 this run" in err
    assert _records(world.runtime) == ()


def test_the_doctor_is_asked_about_the_days_market_and_the_index_codes_the_panel_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The update's datasets, less the registry (keyed by lifecycle year) and the industry
    targets; with the index datasets, the three index codes the panel builds -- without them the
    doctor reports `check_unavailable` and blocks."""
    asked: list[list[str]] = []

    def invoked(arguments: Sequence[str]) -> Any:
        asked.append(list(arguments))
        return daily.Invocation(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(daily, "invoke", invoked)
    datasets = daily.doctor_datasets(daily.FULL_UPDATE_TARGETS + daily.INDUSTRY_TARGETS)
    daily.check_panel(tmp_path, datasets=datasets, session=DAY, as_of=DAY_AS_OF, exchange="SSE")

    assert STOCK_BASIC_DATASET not in datasets
    assert {"index_classify", "index_member_all"}.isdisjoint(datasets)
    assert {DAILY_DATASET, DAILY_BASIC_DATASET, SUSPENSION_DATASET, "income"} <= set(datasets)
    assert [call[:2] for call in asked] == [["panel", "doctor"], ["data-check", "--runtime-dir"]]
    for call in asked:
        codes = [call[index + 1] for index, flag in enumerate(call) if flag == "--index-code"]
        assert codes == ["000300.SH", "000905.SH", "000852.SH"]
        assert call[call.index("--session") + 1] == DAY.isoformat()


# --- what a day fetches (R2) ------------------------------------------------------------------


def _probe(config: Mapping[str, object]) -> Any:
    registered = json.loads(json.dumps(config, default=str))
    return daily.day_request(registered, day=DAY, as_of=DAY_AS_OF)


@pytest.mark.parametrize(
    ("components", "statements"),
    [
        pytest.param([("reversal_1d/v1", "raw", "1")], (), id="price only"),
        pytest.param([("book_to_price/v1", "raw", "1")], ("balancesheet",), id="book to price"),
        pytest.param(
            [("accruals_ttm/v1", "raw", "1"), ("turnover_60/v1", "raw", "1")],
            ("income", "balancesheet", "cashflow"),
            id="accruals and turnover",
        ),
    ],
)
def test_a_day_fetches_the_price_base_and_only_the_statements_its_factors_read(
    components: list[tuple[str, str, str]], statements: tuple[str, ...]
) -> None:
    probe = _probe({**CONFIG, "components": components})
    targets = daily.targets_for(daily.tiers_read(probe))

    assert set(targets) == set(daily.BASE_TARGETS) | set(statements)
    assert "namechange" not in targets
    assert "index_weight" not in targets
    assert set(daily.INDUSTRY_TARGETS).isdisjoint(targets)


def test_industries_are_read_every_day_for_a_neutralized_tier_and_on_rebalance_days_for_a_cap() -> (
    None
):
    schedule = daily.Schedule(
        session=DAY, position=5, scheduled=date(2026, 1, 16), previous=date(2026, 1, 16)
    )
    due = daily.Schedule(session=DAY, position=6, scheduled=DAY, previous=date(2026, 1, 16))
    neutral = (
        daily.TierBuild(
            factor="reversal_1d/v1", tier="neutralized", transform="t", neutralization="n"
        ),
    )
    raw = (
        daily.TierBuild(factor="reversal_1d/v1", tier="raw", transform=None, neutralization=None),
    )

    assert not schedule.due and due.due
    assert daily.industry_day(_probe(CONFIG), neutral, schedule)
    assert not daily.industry_day(_probe(CONFIG), raw, due)
    assert not daily.industry_day(_probe(CAPPED), raw, schedule)
    assert daily.industry_day(_probe(CAPPED), raw, due)


def _industries(world: World, instant: datetime) -> dict[str, str]:
    """Each security's level-one industry at `instant`, as the world's store answers it."""
    store = PanelStore(world.runtime / "panel")
    return {
        code: row.l1_code
        for code, row in load_industry_cross_section(
            store,
            day=instant.astimezone(daily.SHANGHAI).date(),
            years=store.registered_years(INDUSTRY_MEMBERSHIP_DATASET),
            as_of=instant,
            max_staleness=None,
        ).items()
    }


def test_an_industry_cap_refreshed_on_rebalance_days_scores_as_a_daily_full_refresh_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refresh rule, held against the rule it replaces.

    Two runtimes run the capped configuration over 19--23 January. One refreshes the industry
    targets only on a day whose scoring reads industries (a rebalance day); the other refreshes
    them every day, as a daily full refresh would. On 21 January the upstream reclassifies one
    security and gives another its first membership -- on a day that is not a rebalance, so the
    first runtime does not fetch them and its store is stale that day.

    Every day's candidates, targets and registered record are identical across the two; on the
    stale day the first runtime's scoring read no membership at all; on 22 January (a
    rebalance) both stores answer the same industry for every security, the change included.
    The budget per day is asserted from the requests counted.
    """
    changes = {
        "reclassified": ((SECURITIES[2], date(2026, 1, 21), L1_CODES[1]),),
        "first_assigned": ((SECURITIES[5], date(2026, 1, 21)),),
    }
    cadence = _world(
        tmp_path / "cadence",
        monkeypatch,
        Market(open_days=OPEN_2026, **changes),
        config=CAPPED,
    )
    daily_refresh = _world(
        tmp_path / "daily",
        monkeypatch,
        Market(open_days=OPEN_2026, **changes),
        config=CAPPED,
    )
    every_day = (*TARGETS, *daily.INDUSTRY_TARGETS)
    counts: dict[str, list[int]] = {"cadence": [], "daily": []}
    answers: dict[date, dict[str, dict[str, str]]] = {}
    for offset in range(5):
        day = DAY + timedelta(days=offset)
        run = {
            "as_of": DAY_AS_OF + timedelta(days=offset),
            "clock": RUN_CLOCK + timedelta(days=offset),
        }
        code, one, err = _run(cadence, capsys, monkeypatch=monkeypatch, **run)
        assert code == 0, err
        asked_one = set(cadence.market.payloads)
        code, other, err = _run(
            daily_refresh, capsys, monkeypatch=monkeypatch, targets=every_day, **run
        )
        assert code == 0, err
        for key in ("candidates", "targets", "prediction"):
            assert one[key] == other[key], (day, key)
        due = one["targets"]["decision"] == "rebalanced"
        assert (bool(asked_one & INDUSTRY_APIS)) == due, day
        if offset:
            # A stored corpus to check against: the two whole-market states, not the slices.
            assert daily_refresh.market.asked(INDUSTRY_MEMBERSHIP_DATASET) == [
                {"is_new": "Y"},
                {"is_new": "N"},
            ]
            assert other["panel"]["industry_sweep"] == "whole-market states"
        else:
            assert other["panel"]["industry_sweep"].startswith("the l1_code slices (4 requests)")
            assert "no stored membership corpus" in other["panel"]["industry_sweep"]
        assert set(daily_refresh.market.payloads) >= INDUSTRY_APIS
        assert (one["candidates"]["industries_read"] > 0) == due
        answers[day] = {
            name: _industries(world, session_publication_instant(day))
            for name, world in (("cadence", cadence), ("daily", daily_refresh))
        }
        counts["cadence"].append(one["this_run"]["request_count"])
        counts["daily"].append(other["this_run"]["request_count"])
        cadence.market.clear()
        daily_refresh.market.clear()

    stale, fresh = date(2026, 1, 21), date(2026, 1, 22)
    # 21 January: the change is published and only the daily refresh stored it -- the cadence
    # store is stale, and (above) nothing that day read it.
    assert answers[stale]["cadence"] != answers[stale]["daily"]
    assert answers[stale]["daily"][SECURITIES[2]] == L1_CODES[1]
    assert SECURITIES[5] not in answers[stale]["cadence"]
    # 22 January, a rebalance: both stores answer the same industry for every security.
    assert answers[fresh]["cadence"] == answers[fresh]["daily"]
    assert answers[fresh]["cadence"][SECURITIES[2]] == L1_CODES[1]
    assert SECURITIES[5] in answers[fresh]["cadence"]
    # Budget: the first day fetches six sessions; after it, two sessions (the overlap and the
    # new one) of the five price APIs plus the calendar and the registry -- and, on a day that
    # reads industries, the tree (two vintages) and the memberships: two states per level-one
    # industry the first time, with no stored corpus to check a whole-market answer against,
    # and the two whole-market states after that.
    first, later = 2 + 2 * len(L1_CODES), 2 + 2
    assert counts["cadence"] == [2 + 5 * 6 + first, 12 + later, 12, 12 + later, 12]
    assert counts["daily"] == [2 + 5 * 6 + first] + [12 + later] * 4


# Every request the whole-market attempt makes before the slices, per fault: both states (a
# lost or doubled row fails the check afterwards); the refused one-shot, page one and the
# overlapping page two; the one-shot, page one and four attempts at a page two that raises.
WHOLE_MARKET_ATTEMPTS: Final[dict[str, int]] = {"lost": 2, "doubled": 2, "overlap": 3, "raises": 6}


@pytest.mark.parametrize("fault", sorted(WHOLE_MARKET_ATTEMPTS))
def test_a_whole_market_answer_that_fails_the_self_check_is_fetched_again_as_slices(
    fault: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The self-check costs budget, never correctness.

    Day one stores the corpus from the slices. On day two the whole-market current answer loses
    a row (the page gap a close between two pages leaves), serves one twice (the overlap an
    arrival leaves), or -- paged past the cap -- has a second page that overlaps the first or
    whose transport raises on every attempt; every slice stays right. The day still clears, the
    membership store answers what day one's did for every security, the `BUDGET` line says why
    the slices were asked for, and the printed summary says the sweep fell back."""
    world = _world(tmp_path, monkeypatch, Market(open_days=OPEN_2026, whole_market_fault=fault))
    # The provider's retry backoff, not waited out: four attempts at the raising page.
    monkeypatch.setattr(cli, "TushareProvider", partial(cli.TushareProvider, sleep=lambda _s: None))
    every_day = (*TARGETS, *daily.INDUSTRY_TARGETS)

    code, _first, err = _run(world, capsys, targets=every_day)
    assert code == 0, err
    assert all("l1_code" in one for one in world.market.asked(INDUSTRY_MEMBERSHIP_DATASET))
    before = _industries(world, session_publication_instant(DAY))
    world.market.clear()

    code, second, err = _run(
        world,
        capsys,
        as_of=DAY_AS_OF + timedelta(days=1),
        clock=RUN_CLOCK + timedelta(days=1),
        targets=every_day,
        monkeypatch=monkeypatch,
    )

    assert code == 0, err
    asked = world.market.asked(INDUSTRY_MEMBERSHIP_DATASET)
    attempts = WHOLE_MARKET_ATTEMPTS[fault]
    assert all("l1_code" not in one for one in asked[:attempts])
    assert sorted((one["l1_code"], one["is_new"]) for one in asked[attempts:]) == sorted(
        (level_one, state) for level_one in L1_CODES for state in ("Y", "N")
    )
    (fallback,) = [line for line in second["panel"]["budget"] if "self-check" in line]
    assert fallback.startswith(f"BUDGET {INDUSTRY_MEMBERSHIP_DATASET} {2 * len(L1_CODES)} ")
    assert second["panel"]["industry_sweep"].startswith("FELL BACK to the l1_code slices")
    printed = daily.summary_lines(second, top=3)
    assert any(line.startswith("industry sweep     FELL BACK") for line in printed)
    after = _industries(world, session_publication_instant(DAY + timedelta(days=1)))
    assert before and after == before


# --- fina_indicator: announcement years assembled from report-period years it did not sweep ------


def _build_financial_indicator(
    world: World, periods: Sequence[int], as_of: str, *, registry: bool = False
) -> None:
    """`panel build --dataset fina_indicator` over `periods`, non-incremental: the backfill a real
    store was filled by -- with the registry at the same `as_of` when `registry`."""
    arguments = ["panel", "build", "--runtime-dir", str(world.runtime), "--as-of", as_of]
    for period in periods:
        arguments += ["--year", str(period)]
    if registry:
        arguments += ["--dataset", STOCK_BASIC_DATASET]
    built = daily.invoke([*arguments, "--dataset", FINANCIAL_INDICATOR_DATASET, "--json"])
    assert built.exit_code == 0, built.reason()


def _indicator_partitions(world: World) -> dict[int, Any]:
    return {
        year: content_hash
        for dataset, year, content_hash, _written in _catalog(world.runtime)
        if dataset == FINANCIAL_INDICATOR_DATASET
    }


def test_the_days_indicator_sweep_keeps_what_the_periods_it_did_not_sweep_filed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A backfilled store's announcement year 2025 holds 2025's interim reports **and** 2024's
    annual reports, announced in April 2025. On 19 January 2026 the day sweeps report-period year
    2025 alone (2026's first period has not ended), whose rows are announced in 2025 and 2026.
    Written whole, announcement year 2025 would lose 2024's annuals and the panel refuses the
    shrink, so the day would stop at step 2 every day of the year.

    The stored rows of the period years not swept are carried into the partition, and the
    result is the partition a full build over both period years writes at the same `as_of`,
    hash for hash."""
    world = _world(tmp_path / "daily", monkeypatch, Market(open_days=OPEN_2026))
    _build_financial_indicator(world, (2024, 2025), SEEDED_AS_OF)
    assert set(_indicator_partitions(world)) >= {2025}

    code, result, err = _run(world, capsys, targets=(*TARGETS, FINANCIAL_INDICATOR_DATASET))

    assert code == 0, err
    assert result["this_run"]["requests"]["fina_indicator_vip"] == 4  # period year 2025 only
    daily_hashes = _indicator_partitions(world)
    full = _world(tmp_path / "full", monkeypatch, Market(open_days=OPEN_2026))
    full.market.today = DAY
    _build_financial_indicator(full, (2024, 2025), DAY_AS_OF.isoformat())
    assert daily_hashes == _indicator_partitions(full)


def test_the_first_session_of_a_year_keeps_last_years_annuals_of_the_year_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """4 January 2027 sweeps report-period year 2026 alone. Announcement year 2026 holds 2026's
    interims and 2025's annuals (announced April 2026, report-period year 2025): the carry keeps
    the latter, and the partition is a full build's over 2025 and 2026 at the same `as_of`."""
    world = _new_year(tmp_path / "daily", monkeypatch)
    _build_financial_indicator(world, (2025, 2026), "2026-12-24T12:00:00+08:00")
    targets = (*TARGETS, FINANCIAL_INDICATOR_DATASET)
    for as_of in ("2026-12-31T18:30:00+08:00", "2027-01-04T18:30:00+08:00"):
        code, result, err = _run(
            world,
            capsys,
            as_of=_at(as_of),
            clock=_at(as_of) + timedelta(minutes=5),
            targets=targets,
            monkeypatch=monkeypatch,
        )
        assert code == 0, (as_of, err)
    assert result["session"] == "2027-01-04"

    full = _new_year(tmp_path / "full", monkeypatch)
    full.market.today = date(2027, 1, 4)
    _build_financial_indicator(full, (2025, 2026), "2027-01-04T18:30:00+08:00", registry=True)
    assert _indicator_partitions(world)[2026] == _indicator_partitions(full)[2026]


def test_january_re_sweeps_last_decembers_statements_and_records_the_new_year_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`V2-P6-018` through the daily command. On 4 January 2027 the statement sweep is
    incremental: 2026's trailing months are its November and December, so January also builds
    2026 -- re-sweeping December -- while 2027, in which nothing has been announced yet, is swept
    whole and recorded empty rather than refused. The doctor and the gate clear both years."""
    world = _new_year(tmp_path / "daily", monkeypatch)
    seeded = daily.invoke(
        [
            "panel",
            "build",
            "--runtime-dir",
            str(world.runtime),
            "--year",
            "2026",
            "--as-of",
            "2026-12-24T12:00:00+08:00",
            "--dataset",
            INCOME_DATASET,
            "--json",
        ]
    )
    assert seeded.exit_code == 0, seeded.reason()
    targets = (*TARGETS, INCOME_DATASET)
    for as_of in ("2026-12-31T18:30:00+08:00", "2027-01-04T18:30:00+08:00"):
        world.market.clear()
        code, result, err = _run(
            world,
            capsys,
            as_of=_at(as_of),
            clock=_at(as_of) + timedelta(minutes=5),
            targets=targets,
            monkeypatch=monkeypatch,
        )
        assert code == 0, (as_of, err)

    assert result["session"] == "2027-01-04"
    months = sorted({str(p["start_date"])[:6] for p in world.market.asked(f"{INCOME_DATASET}_vip")})
    # 2026: its trailing November and December, and the rotation groups of Friday 1 January
    # (May, October) and Monday 4 January (January, June, November) -- every weekday since the
    # partition's last build on 31 December. 2027: swept whole, its January.
    assert months == ["202601", "202605", "202606", "202610", "202611", "202612", "202701"]
    store = PanelStore(world.runtime / "panel")
    empty = store.read_coverage(INCOME_DATASET, 2027)
    assert empty is not None and empty.row_count == 0
    kept = store.read_coverage(INCOME_DATASET, 2026)
    assert kept is not None and kept.row_count


def test_new_years_day_updates_the_31_december_session_and_builds_no_year_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1 January 2027 is a holiday: the day is 31 December 2026. The January re-sweep of last
    December is keyed on the **session's** month, so this run builds 2026's statements and not
    2025's -- keyed on the clock, it asked for a year two before the one it was updating.

    The registry here records an event on 1 January: a statement build reads the registry through
    the year of its clock, and a registry with no event in the new year yet is refused (the
    runbook's known limitation 6). That limitation only matters when 31 December's own run was
    missed -- run on the day, its journal is complete and New Year's Day asks for nothing."""
    world = _world(
        tmp_path / "daily",
        monkeypatch,
        Market(open_days=NEW_YEAR, delisted=((SECURITIES[-2], date(2027, 1, 1)),)),
        config=NEW_YEAR_CONFIG,
        seeded_as_of="2026-12-24T12:00:00+08:00",
    )
    seeded = daily.invoke(
        [
            "panel",
            "build",
            "--runtime-dir",
            str(world.runtime),
            "--year",
            "2026",
            "--as-of",
            "2026-12-24T12:00:00+08:00",
            "--dataset",
            INCOME_DATASET,
            "--json",
        ]
    )
    assert seeded.exit_code == 0, seeded.reason()
    world.market.clear()

    code, result, err = _run(
        world,
        capsys,
        as_of=_at("2027-01-01T18:30:00+08:00"),
        clock=_at("2027-01-01T18:35:00+08:00"),
        targets=(*TARGETS, INCOME_DATASET),
        monkeypatch=monkeypatch,
    )

    assert code == 0, err
    assert result["session"] == "2026-12-31"
    years = {str(p["start_date"])[:4] for p in world.market.asked(f"{INCOME_DATASET}_vip")}
    assert years == {"2026"}


# --- across New Year -----------------------------------------------------------------------------


NEW_YEAR_CONFIG: Final[dict[str, object]] = {**CONFIG, "start": date(2026, 12, 14)}


def _new_year(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return _world(
        tmp_path,
        monkeypatch,
        # A delisting on 2027's first session: a registry holds an event in every year, and the
        # universe a factor build reads covers every lifecycle year up to the session's.
        Market(open_days=NEW_YEAR, delisted=((SECURITIES[-2], date(2027, 1, 4)),)),
        config=NEW_YEAR_CONFIG,
        seeded_as_of="2026-12-24T12:00:00+08:00",
    )


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_a_window_crossing_new_year_fetches_next_years_calendar_and_is_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """24 December's window (two sessions after the next) ends in 2026: no 2027 calendar is
    asked for. 30 December's crosses: the next session is 31 December and the window's last close
    is 5 January 2027, so step 2 builds 2027's calendar and the record is placed on it."""
    world = _new_year(tmp_path, monkeypatch)

    code, before, err = _run(
        world,
        capsys,
        as_of=_at("2026-12-24T18:30:00+08:00"),
        clock=_at("2026-12-24T18:35:00+08:00"),
    )
    assert code == 0, err
    assert before["panel"]["next_year_calendar"] is False
    assert [params["start_date"][:4] for params in world.market.asked("trade_cal")] == ["2026"]

    world.market.clear()
    code, crossing, err = _run(
        world,
        capsys,
        as_of=_at("2026-12-30T18:30:00+08:00"),
        clock=_at("2026-12-30T18:35:00+08:00"),
    )

    assert code == 0, err
    assert crossing["panel"]["next_year_calendar"] is True
    assert [params["start_date"][:4] for params in world.market.asked("trade_cal")] == [
        "2026",
        "2027",
    ]
    assert crossing["prediction"]["standing"] == "forward"
    assert _at(crossing["prediction"]["outcome_known_at"]) == _at("2027-01-05T15:00:00+08:00")


def test_the_last_session_of_the_year_is_registered_before_the_first_of_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """31 December: every session of its window is in 2027, and its registration cutoff is the
    call auction of 4 January."""
    world = _new_year(tmp_path, monkeypatch)
    code, result, err = _run(
        world,
        capsys,
        as_of=_at("2026-12-31T18:30:00+08:00"),
        clock=_at("2027-01-04T09:14:00+08:00"),
    )

    assert code == 0, err
    assert result["session"] == "2026-12-31"
    assert result["prediction"]["standing"] == "forward"
    assert _at(result["prediction"]["outcome_known_at"]) == _at("2027-01-06T15:00:00+08:00")

    late = _new_year(tmp_path / "late", monkeypatch)
    code, _text, err = _run(
        late, capsys, as_of=_at("2026-12-31T18:30:00+08:00"), clock=_at("2027-01-04T09:15:00+08:00")
    )
    assert code == 1
    assert "2027-01-04T09:15:00+08:00" in err


def test_a_run_on_new_years_day_finds_the_last_session_and_the_year_turns_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1 January 2027 is a holiday and no 2027 calendar is stored yet: the day is 31 December,
    found on 2026's calendar. 4 January before the close is still 31 December (on both years'
    calendars now), already complete. 4 January after the close is 2027's first session: a new
    year's panel, factors built across the two years, and the book carried from 31 December."""
    world = _new_year(tmp_path, monkeypatch)
    store = PanelStore(world.runtime / "panel")
    assert store.registered_years(TRADING_CALENDAR_DATASET) == (2026,)

    code, holiday, err = _run(
        world,
        capsys,
        as_of=_at("2027-01-01T18:30:00+08:00"),
        clock=_at("2027-01-01T18:35:00+08:00"),
    )
    assert code == 0, err
    assert holiday["session"] == "2026-12-31"
    assert holiday["prediction"]["standing"] == "forward"
    assert store.registered_years(TRADING_CALENDAR_DATASET) == (2026, 2027)

    world.market.clear()
    code, morning, err = _run(
        world,
        capsys,
        as_of=_at("2027-01-04T08:00:00+08:00"),
        clock=_at("2027-01-04T08:05:00+08:00"),
    )
    assert code == 0, err
    assert (morning["session"], morning["this_run"]["request_count"]) == ("2026-12-31", 0)

    code, first, err = _run(
        world,
        capsys,
        as_of=_at("2027-01-04T18:30:00+08:00"),
        clock=_at("2027-01-04T18:35:00+08:00"),
    )
    assert code == 0, err
    assert first["session"] == "2027-01-04"
    assert first["panel"]["year"] == 2027
    assert first["targets"]["previous_session"] == "2026-12-31"
    assert first["targets"]["reason"] == "scheduled"
    assert first["prediction"]["standing"] == "forward"


# --- the default path, statements included ------------------------------------------------------


def test_a_full_update_with_statements_runs_every_step_and_states_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--full-update` on an ordinary day -- the panel stored through the session before -- with a
    configuration that reads a statement (`book_to_price/v1`: `balancesheet` and `daily_basic`).

    Every target is fetched at a coherent `as_of` (20 February 2026, 18:30: January and
    February's announcement windows have begun), the doctor and the gate clear all of them, the
    factor is built off the swept balance sheets and the day is registered. The requests are the
    code's own `BUDGET` lines plus the one-request-per-year targets; the runbook's steady-state
    counts are these, with the live market's window counts (`V2-P6-003`'s measurement)."""
    world = _world(
        tmp_path,
        monkeypatch,
        Market(open_days=OPEN_2026),
        config={**CONFIG, "components": [("book_to_price/v1", "raw", Decimal("1"))]},
        seeded_as_of="2026-02-20T12:00:00+08:00",
    )

    code, result, err = _run(
        world,
        capsys,
        as_of=_at("2026-02-20T18:30:00+08:00"),
        clock=_at("2026-02-20T18:35:00+08:00"),
        targets=None,
        extra=["--full-update"],
    )

    assert code == 0, err
    assert result["panel"]["targets"] == list(daily.FULL_UPDATE_TARGETS)
    assert result["factors"]["built"] == ["book_to_price/v1@raw"]
    assert result["prediction"]["standing"] == "forward"
    requests = result["this_run"]["requests"]
    assert requests == {
        "trade_cal": 1,
        "stock_basic": 1,
        "namechange": 1,
        "adj_factor": 2,
        "suspend_d": 2,
        "daily": 2,
        "daily_basic": 2,
        "stk_limit": 2,
        "index_daily": 3,
        "index_weight": 6,
        f"{INCOME_DATASET}_vip": 2,
        f"{BALANCE_SHEET_DATASET}_vip": 2,
        f"{CASH_FLOW_DATASET}_vip": 2,
        # The report-period year before the session's: its four quarter ends. The session's own
        # year has no period that has ended on 20 February, so it is not asked about.
        "fina_indicator_vip": 4,
    }
    assert result["panel"]["budget"]
    assert all(line.startswith("BUDGET ") for line in result["panel"]["budget"])


# --- the scheduled run: a checkout pinned at the registration -----------------------------------


def test_the_scheduled_checkout_is_pinned_at_the_registration_whatever_development_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """After the registration, development commits a change to the bound code. The development
    checkout is refused (exit 3) -- that forward day would be lost -- while the worktree the helper
    pins at the registration's commit is admitted; the helper is idempotent and never touches a
    dirty worktree.

    The worktree gets its own environment: `uv sync --frozen --offline` from its own `uv.lock`
    into `<worktree>/.venv`, on the interpreter its `.python-version` names and with every extra
    (the research environment's `uv sync --all-extras --dev`), with the development checkout's
    `VIRTUAL_ENV` dropped. Asserted on the command; uv itself is never run here."""
    repo, registration = _repository(tmp_path, CONFIG)
    registered_at = head(repo)
    source = repo / "src" / "openalpha_cn" / "strategy.py"
    source.write_text("RULES = 2\n", encoding="utf-8")
    commit_file(repo, source, "development moves on", at=COMMITTED + timedelta(days=3))
    pinned = tmp_path / 'pinned daily $HOME "x"'
    uv = tmp_path / "bin" / "uv"
    synced: list[tuple[list[str], dict[str, str], Path]] = []

    def sync(
        command: Sequence[str], *, environment: Mapping[str, str], cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        synced.append((list(command), dict(environment), cwd))
        _made_environment(Path(environment["UV_PROJECT_ENVIRONMENT"]), f"{RUNNING_PYTHON}.7")
        return subprocess.CompletedProcess(list(command), 0, b"", b"")

    monkeypatch.setattr(daily, "_run_sync", sync)
    monkeypatch.setenv("VIRTUAL_ENV", str(repo / ".venv"))
    code = daily.main(
        [
            "--pin-worktree",
            str(pinned),
            "--registration",
            str(registration),
            "--repo",
            str(repo),
            "--uv",
            str(uv),
        ]
    )
    out, err = capsys.readouterr()

    assert code == 0, err
    where = pinned.resolve()
    assert out.strip() == (
        f"pinned {where} at {registered_at}; environment {where / '.venv'} synced offline"
    )
    ((command, environment, cwd),) = synced
    assert command == [
        str(uv.resolve()),
        "sync",
        "--frozen",
        "--offline",
        "--no-python-downloads",
        "--all-extras",
        "--python",
        RUNNING_PYTHON,
        "--project",
        str(where),
    ]
    assert environment["UV_PROJECT_ENVIRONMENT"] == str(where / ".venv")
    assert "VIRTUAL_ENV" not in environment and "PYTHONPATH" not in environment
    assert cwd == where
    assert git(pinned, "rev-parse", "HEAD").strip() == registered_at
    assert (pinned / "src" / "openalpha_cn" / "strategy.py").read_text() == "RULES = 1\n"
    assert daily.pin_worktree(registration, repo, pinned) == registered_at

    _bind(monkeypatch, pinned, Market(open_days=OPEN_2026))
    admitted = daily.admit_registration(
        pinned / "docs" / "research" / "p6-registration.json", pinned
    )
    assert admitted.commit == registered_at
    _bind(monkeypatch, repo, Market(open_days=OPEN_2026))
    with pytest.raises(daily.StepFailedError, match="SourceChangedError") as refused:
        daily.admit_registration(registration, repo)
    assert refused.value.exit_code == 3

    (pinned / "scratch.txt").write_text("edited\n", encoding="utf-8")
    with pytest.raises(daily.StepFailedError, match="uncommitted changes"):
        daily.pin_worktree(registration, repo, pinned)


def test_a_registration_committed_with_a_change_to_bound_code_has_no_checkout_to_pin(
    tmp_path: Path,
) -> None:
    repo, registration = _repository(tmp_path, CONFIG)
    source = repo / "src" / "openalpha_cn" / "strategy.py"
    source.write_text("RULES = 2\n", encoding="utf-8")
    registration.write_text(registration.read_text() + " ", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "registration and code together", at=COMMITTED)

    with pytest.raises(registry.SourceChangedError, match="no checkout"):
        registry.registered_checkout(registration, repo)


def test_the_launchd_job_runs_the_pinned_checkout_with_no_shell_and_is_not_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every path is one argument of the vector launchd hands to exec, so a space, a `"` or a `$`
    in any of them arrives verbatim; nothing is installed and no directory is created."""
    odd = tmp_path / 'my "daily" $HOME dir'
    worktree, runtime, logs = odd / "pinned", odd / "runtime", odd / "logs"
    env_file, uv = odd / ".env", odd / "bin" / "uv"

    code = daily.main(
        [
            "--runtime-dir",
            str(runtime),
            "--launchd-plist",
            str(logs),
            "--worktree",
            str(worktree),
            "--env-file",
            str(env_file),
            "--uv",
            str(uv),
        ]
    )
    out, _err = capsys.readouterr()
    job = plistlib.loads(out.encode("utf-8"))

    assert code == 0
    assert job["Label"] == "com.openalpha.daily-selection"
    assert job["ProgramArguments"] == [
        str(uv),
        "run",
        "--no-sync",
        "--env-file",
        str(env_file),
        str(worktree / ".venv" / "bin" / "python"),
        str(worktree / "scripts" / "daily_selection.py"),
        "--runtime-dir",
        str(runtime),
        "--repo",
        str(worktree),
        "--registration",
        str(worktree / "docs" / "research" / "p6-registration.json"),
    ]
    assert job["WorkingDirectory"] == str(worktree)
    # The pinned checkout's own environment, and nothing that points anywhere else.
    assert job["EnvironmentVariables"] == {
        "UV_PROJECT_ENVIRONMENT": str(worktree / ".venv"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    assert job["StartCalendarInterval"] == [
        {"Weekday": weekday, "Hour": 18, "Minute": 30} for weekday in range(1, 6)
    ]
    assert job["RunAtLoad"] is False
    assert not odd.exists()


def test_a_pinned_environment_uv_cannot_build_offline_is_refused_by_package_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--offline` never downloads: a package uv's cache lacks is named, with the one online
    command that fills the cache -- which the daily command never runs itself."""
    uncached = (
        b"error: Failed to prepare distributions\n"
        b"  Caused by: Failed to download `numpy==2.3.1`\n"
        b"  Caused by: Network connectivity is disabled, but the requested data wasn't found in "
        b"the cache for: `https://files.pythonhosted.org/packages/numpy-2.3.1.whl`\n"
    )
    monkeypatch.setattr(
        daily,
        "_run_sync",
        lambda command, *, environment, cwd: subprocess.CompletedProcess(command, 2, b"", uncached),
    )
    worktree = tmp_path / "pinned $HOME"
    worktree.mkdir()
    (worktree / ".python-version").write_text("3.11\n", encoding="utf-8")

    with pytest.raises(daily.StepFailedError) as refused:
        daily.sync_pinned_environment(worktree, uv=Path("/opt/uv"))

    message = str(refused.value)
    assert "uv's cache holds no copy of numpy==2.3.1" in message
    assert "Nothing was downloaded" in message
    assert (
        f"/opt/uv sync --frozen --all-extras --python 3.11 --project {shlex.quote(str(worktree))}"
        in message
    )


def _made_environment(environment: Path, version_info: str) -> None:
    """What `uv sync` leaves behind that says which interpreter it used: `pyvenv.cfg`."""
    environment.mkdir(parents=True, exist_ok=True)
    (environment / "pyvenv.cfg").write_text(
        f"home = /opt/python/bin\nimplementation = CPython\nversion_info = {version_info}\n",
        encoding="utf-8",
    )


def test_a_pinned_environment_on_another_interpreter_is_refused_after_the_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sync names the interpreter, and what it built is read back: an environment on a
    Python whose major.minor is not the checkout's `.python-version` is refused by both versions
    -- the forward run would compute on an interpreter the research did not."""
    worktree = tmp_path / "pinned $HOME"
    worktree.mkdir()
    (worktree / ".python-version").write_text("3.11\n", encoding="utf-8")
    commands: list[list[str]] = []

    def sync(
        command: Sequence[str], *, environment: Mapping[str, str], cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(list(command))
        _made_environment(Path(environment["UV_PROJECT_ENVIRONMENT"]), "3.12.4")
        return subprocess.CompletedProcess(list(command), 0, b"", b"")

    monkeypatch.setattr(daily, "_run_sync", sync)
    with pytest.raises(daily.StepFailedError) as refused:
        daily.sync_pinned_environment(worktree, uv=Path("/opt/uv"))

    assert commands[0][commands[0].index("--python") + 1] == "3.11"
    assert "runs Python 3.12.4" in str(refused.value)
    assert ".python-version pins 3.11" in str(refused.value)

    monkeypatch.setattr(
        daily,
        "_run_sync",
        lambda command, *, environment, cwd: (
            _made_environment(Path(environment["UV_PROJECT_ENVIRONMENT"]), "3.11.14"),
            subprocess.CompletedProcess(list(command), 0, b"", b""),
        )[1],
    )
    assert daily.sync_pinned_environment(worktree, uv=Path("/opt/uv")) == worktree / ".venv"


def test_a_pinned_checkout_without_a_python_version_is_not_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `.python-version`, no interpreter to pin: refused before uv runs."""
    ran: list[object] = []
    monkeypatch.setattr(daily, "_run_sync", lambda *a, **k: ran.append(a))

    with pytest.raises(daily.StepFailedError, match=r"\.python-version"):
        daily.sync_pinned_environment(tmp_path, uv=Path("/opt/uv"))
    assert ran == []


def test_an_interpreter_whose_packages_are_not_the_lock_is_refused_at_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 1 compares the running interpreter's distributions with the checkout's `uv.lock`,
    which the bound-code check holds to the registration's commit: a different version, or a
    package the lock does not name, refuses the run (exit 3) by name."""
    repo, registration = _repository(tmp_path, CONFIG)
    _bind(monkeypatch, repo, Market(open_days=OPEN_2026))
    assert daily.admit_registration(registration, repo).code_commit

    monkeypatch.setattr(
        daily, "installed_distributions", lambda: {**LOCKED, "numpy": "2.3.2", "left-pad": "1.0"}
    )
    with pytest.raises(daily.StepFailedError) as refused:
        daily.admit_registration(registration, repo)

    assert refused.value.exit_code == 3
    assert "numpy 2.3.2 is installed and uv.lock pins 2.3.1" in str(refused.value)
    assert "left-pad 1.0 is installed and uv.lock does not lock it" in str(refused.value)


def test_the_environment_comparison_normalises_names_and_ignores_locked_extras() -> None:
    """`uv.lock` spells names in PEP 503's normal form; a locked package that is not installed --
    an extra, another platform's wheel -- is not a difference."""
    locked = daily.locked_versions(f'{LOCK}\n[[package]]\nname = "akshare"\nversion = "1.18.38"\n')

    assert daily._distribution_name("Typing_Extensions.Backport") == "typing-extensions-backport"
    assert daily.environment_differences(locked, LOCKED) == []
    assert daily.environment_differences(locked, {**LOCKED, "numpy": "2.3.1.post1"}) == [
        "numpy 2.3.1.post1 is installed and uv.lock pins 2.3.1"
    ]


def _marker_environment(python_full_version: str, sys_platform: str = "darwin") -> dict[str, str]:
    """PEP 508's marker environment for an interpreter, as `marker_environment` reads one."""
    return {
        "implementation_name": "cpython",
        "implementation_version": python_full_version,
        "os_name": "nt" if sys_platform == "win32" else "posix",
        "platform_machine": "arm64",
        "platform_python_implementation": "CPython",
        "platform_release": "25.5.0",
        "platform_system": "Windows" if sys_platform == "win32" else "Darwin",
        "platform_version": "Darwin Kernel Version 25.5.0",
        "python_full_version": python_full_version,
        "python_version": ".".join(python_full_version.split(".")[:2]),
        "sys_platform": sys_platform,
    }


def test_a_forked_lock_locks_the_version_whose_markers_hold_for_the_interpreter() -> None:
    """`uv.lock` lists a package once per fork, each with the `resolution-markers` it applies
    under. Only the entry whose markers hold for the running interpreter counts: on 3.11 the lock
    answers numpy 2.4.6 -- the main checkout's environment is the lock's, not a drift from it --
    and on 3.12, 2.5.1."""
    on_311 = daily.locked_versions(FORKED_LOCK, environment=_marker_environment("3.11.14"))
    on_312 = daily.locked_versions(FORKED_LOCK, environment=_marker_environment("3.12.4"))
    on_windows = daily.locked_versions(
        FORKED_LOCK, environment=_marker_environment("3.11.9", "win32")
    )

    assert on_311["numpy"] == on_windows["numpy"] == "2.4.6"
    assert on_312["numpy"] == "2.5.1"
    assert on_311["colorama"] == "0.4.6"
    installed = {"openalpha-cn": "1.0.0", "numpy": "2.4.6"}
    assert daily.environment_differences(on_311, installed) == []
    assert daily.environment_differences(on_312, installed) == [
        "numpy 2.4.6 is installed and uv.lock pins 2.5.1"
    ]
    assert daily.environment_differences(on_311, {**installed, "numpy": "2.5.1"}) == [
        "numpy 2.5.1 is installed and uv.lock pins 2.4.6"
    ]


def test_admission_takes_the_lock_entry_for_the_running_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 1 end to end on a two-fork lock: a 3.11 interpreter with numpy 2.4.6 is admitted,
    the same interpreter with 2.5.1 is refused by name (exit 3)."""
    repo, registration = _repository(tmp_path, CONFIG, lock=FORKED_LOCK)
    _bind(monkeypatch, repo, Market(open_days=OPEN_2026))
    monkeypatch.setattr(daily, "marker_environment", lambda: _marker_environment("3.11.14"))
    installed = {"openalpha-cn": "1.0.0", "numpy": "2.4.6", "colorama": "0.4.6"}
    monkeypatch.setattr(daily, "installed_distributions", lambda: installed)

    assert daily.admit_registration(registration, repo).code_commit

    monkeypatch.setattr(daily, "installed_distributions", lambda: {**installed, "numpy": "2.5.1"})
    with pytest.raises(daily.StepFailedError) as refused:
        daily.admit_registration(registration, repo)
    assert refused.value.exit_code == 3
    assert "numpy 2.5.1 is installed and uv.lock pins 2.4.6" in str(refused.value)


@pytest.mark.parametrize(
    ("marker", "holds"),
    [
        ("python_full_version < '3.12'", True),
        ("python_full_version >= '3.12' and python_full_version < '3.14'", False),
        ("python_full_version == '3.11.*'", True),
        ("python_full_version != '3.11.*'", False),
        ("python_full_version == '3.1.*'", False),
        ("python_version == '3.11'", True),
        ("python_version ~= '3.10'", True),
        ("python_version ~= '3.12'", False),
        ("python_version > '3.9'", True),
        ("python_version <= '3.10'", False),
        ("sys_platform == 'win32' or sys_platform == 'darwin'", True),
        ("sys_platform != 'emscripten' and sys_platform != 'win32'", True),
        ("(sys_platform == 'win32' or python_version < '3.12') and os_name == 'posix'", True),
        ("sys_platform == 'darwin' or python_version >= '3.12' and os_name == 'nt'", True),
        ("'arm' in platform_machine", True),
        ("'x86' not in platform_machine", True),
        ("implementation_name == 'cpython' and platform_python_implementation == 'CPython'", True),
        ('platform_system == "Darwin"', True),
    ],
)
def test_the_lock_marker_grammar(marker: str, holds: bool) -> None:
    """The subset of PEP 508 markers `uv.lock` writes, evaluated with the standard library alone
    (`packaging` is only a test dependency): `and` binds tighter than `or`, `==`/`!=` take a
    `.*` prefix, versions compare as release numbers and everything else as text."""
    assert daily.marker_holds(marker, _marker_environment("3.11.14")) is holds


@pytest.mark.parametrize(
    "marker",
    [
        "extra == 'akshare'",
        "python_full_version < '3.12",
        "python_full_version <",
        "sys_platform == 'win32' and",
        "python_version < 'three'",
        "sys_platform < 'win32'",
        "(python_version < '3.12'",
    ],
)
def test_a_marker_the_grammar_cannot_read_is_an_error_not_a_guess(marker: str) -> None:
    """A marker this evaluator does not understand never counts as holding or not holding."""
    with pytest.raises(ValueError, match="marker"):
        daily.marker_holds(marker, _marker_environment("3.11.14"))


def test_a_lock_whose_markers_cannot_be_read_refuses_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = FORKED_LOCK.replace("python_full_version < '3.12' and sys_platform == 'win32'", "foo ~")
    repo, registration = _repository(tmp_path, CONFIG, lock=lock)
    _bind(monkeypatch, repo, Market(open_days=OPEN_2026))
    monkeypatch.setattr(daily, "marker_environment", lambda: _marker_environment("3.11.14"))

    with pytest.raises(daily.StepFailedError) as refused:
        daily.admit_registration(registration, repo)
    assert refused.value.exit_code == 3
    assert "cannot be compared" in str(refused.value)


def test_an_interpreter_other_than_the_registered_python_version_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registered checkout's `.python-version` (read at the registration's `code_commit`,
    so an edit to the working tree does not move it) names the interpreter the research ran on;
    an interpreter of another major.minor is refused (exit 3), whatever its packages."""
    other = "3.9" if RUNNING_PYTHON != "3.9" else "3.10"
    repo, registration = _repository(tmp_path, CONFIG, python_version=other)
    _bind(monkeypatch, repo, Market(open_days=OPEN_2026))

    with pytest.raises(daily.StepFailedError) as refused:
        daily.admit_registration(registration, repo)
    assert refused.value.exit_code == 3
    assert f"Python {RUNNING_PYTHON}" in str(refused.value)
    assert f".python-version pins {other}" in str(refused.value)

    (repo / ".python-version").write_text(f"{RUNNING_PYTHON}\n", encoding="utf-8")
    with pytest.raises(daily.StepFailedError, match=rf"\.python-version pins {other}"):
        daily.admit_registration(registration, repo)


# --- the registered score is the backtest's ------------------------------------------------------


REGISTERED: Final = RegisteredConfiguration(
    config_id="3" * 64, registration_sha256="0" * 64, code_commit="2" * 40, seed=20260926
)


def _read_at(day: date) -> datetime:
    """When the equality tests read the corpus: `READ_AT`, after its last session.

    `write_strategy_corpus` stamps its calendar and registry as ingested on 17 January, so they
    are not readable at an earlier evening. Reading later changes nothing a day's score depends
    on: each build is chosen by the day's own signal instant and each bar by its session.
    """
    return max(READ_AT, _evening(day))


def _after_the_close(day: date, following: date) -> datetime:
    """18:30 on the day: when the scheduled daily command files it."""
    return _evening(day)


def _before_the_auction(day: date, following: date) -> datetime:
    """09:14 on the next session, the last minute before its call auction."""
    return datetime.combine(following, time(9, 14), daily.SHANGHAI)


def _at_the_auction(day: date, following: date) -> datetime:
    """09:15 on the next session, as its call auction starts."""
    return datetime.combine(following, time(9, 15), daily.SHANGHAI)


def _register_days(
    root: Path,
    request_for: Callable[[date], Any],
    days: Sequence[date],
    anchor: date,
    *,
    filed_at: Callable[[date, date], datetime] = _after_the_close,
) -> list[str]:
    """Each day's scores registered through the command's path at `filed_at(day, next session)`
    -- 18:30 by default, the scheduled command's evening."""
    store = PanelStore(root / "panel")
    identifiers: list[str] = []
    for day in days:
        request = request_for(day)
        signal = score_day(store, request, day=day, anchor=anchor)
        calendar = daily._outcome_calendar(store, request.exchange, day, request.as_of)
        filed = filed_at(day, calendar.next_trading_day(day))
        batch = signal_day_batch(signal, request, REGISTERED, predicted_at=filed)
        assert batch is not None
        record, outcome = daily.register_prediction(
            root, batch, calendar=calendar, clock=lambda filed=filed: filed
        )
        assert (outcome, record.standing) == ("created", "forward")
        identifiers.append(record.record_id)
    return identifiers


def _traded(result: Any) -> list[tuple[Any, ...]]:
    return [
        (
            period.start,
            period.end,
            period.holdings,
            tuple(fill.model_dump_json() for fill in period.fills),
            period.net_return,
            period.cost,
            period.held,
        )
        for period in result.periods
    ]


def _base(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "components": (),
        "combine": "zscore_sum",
        "transform": None,
        "neutralization": None,
        "exchange": EXCHANGE,
        "rebalance_every_sessions": 2,
        "holding_count": 3,
        "buffer_rank": None,
        "max_industry_weight": None,
    }
    return {**arguments, **overrides}


STATIC: Final[dict[str, Any]] = {
    "components": ((REVERSAL.qualified_key, "raw", Decimal("1")),),
    "rebalance_every_sessions": 3,
}
TRAILING: Final[dict[str, Any]] = {
    "trailing_ic": {
        "components": ((REVERSAL.qualified_key, "raw"),),
        "ic_window_sessions": 5,
        "min_ic_observations": 1,
        "ic_method": "spearman",
        "horizon_sessions": 1,
        "negative_ic": "keep_sign",
        "min_ic_securities": 3,
    }
}
WALK_FORWARD: Final[dict[str, Any]] = {
    "walk_forward": {
        "family": "cross_sectional_rank",
        "features": (f"{REVERSAL.qualified_key}@raw",),
        "seed": 0,
        "code_commit": "abcdef1234567",
        "train_sessions": 5,
        "refit_every_sessions": 2,
        "embargo_sessions": 1,
        "horizon_sessions": 1,
    }
}


@pytest.mark.parametrize(
    ("source", "first"),
    [
        pytest.param(STATIC, 1, id="static"),
        pytest.param(TRAILING, 3, id="trailing_ic"),
        pytest.param(WALK_FORWARD, 5, id="walk_forward"),
    ],
)
def test_a_backtest_reading_the_registered_records_trades_as_the_configuration_would(
    tmp_path: Path, source: dict[str, Any], first: int
) -> None:
    """Every signal day of a backtest from `first`, scored and registered through the daily
    command's path at 18:30 -- when the scheduled command files it, two hours after the 16:30
    signal instant; a backtest of the records, then, holds, fills and returns exactly what the
    configuration's own backtest does, period by period.

    From `first`: the trailing source answers from s3 (s1 knows no IC) and the walk-forward one
    from s5 (s1 and s3 have no admissible fit), and a source of registered records cannot hold,
    so both backtests start where the source first answers.
    """
    panel = write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    sessions = panel.sessions
    start, end = sessions[first], sessions[-1]
    configured = _base(**source)
    step = configured["rebalance_every_sessions"]
    signal_days = [sessions[index] for index in range(first, len(sessions) - 1, step)]

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=_read_at(day)
        )

    identifiers = _register_days(tmp_path, request_for, signal_days, start)
    by_records = backtest_strategy(
        store,
        strategy_request(
            **_base(rebalance_every_sessions=step),
            prediction_ids=identifiers,
            start=start,
            end=end,
            as_of=READ_AT,
        ),
        predictions=FilePredictionStore(tmp_path / "predictions", clock=lambda: READ_AT).get,
    )
    by_configuration = backtest_strategy(
        store, strategy_request(**configured, start=start, end=end, as_of=READ_AT)
    )

    assert not any(period.held for period in by_configuration.periods)
    assert any(period.fills for period in by_configuration.periods)
    assert _traded(by_records) == _traded(by_configuration)


def _static_records(
    tmp_path: Path, filed_at: Callable[[date, date], datetime]
) -> tuple[Any, list[date], list[str], dict[str, Any]]:
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = _base(**STATIC)
    days = [sessions[index] for index in range(1, len(sessions) - 1, 3)]

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=_read_at(day)
        )

    identifiers = _register_days(tmp_path, request_for, days, sessions[1], filed_at=filed_at)
    return panel, days, identifiers, configured


def _by_records(tmp_path: Path, panel: Any, identifiers: Sequence[str]) -> Any:
    return backtest_strategy(
        PanelStore(tmp_path / "panel"),
        strategy_request(
            **_base(rebalance_every_sessions=3),
            prediction_ids=identifiers,
            start=panel.sessions[1],
            end=panel.sessions[-1],
            as_of=READ_AT,
        ),
        predictions=FilePredictionStore(tmp_path / "predictions", clock=lambda: READ_AT).get,
    )


def test_records_filed_before_the_next_call_auction_trade_as_the_configuration_would(
    tmp_path: Path,
) -> None:
    """The two clocks a record is read on (`V2-P6-011`): its information instant -- the batch's
    `as_of`, the 16:30 signal instant its inputs were read at -- at or before the signal
    instant, and its registration before the call auction of the session that trades it. Filed
    at 09:14 the next morning, every record is read and the book is the configuration's own."""
    panel, _days, identifiers, configured = _static_records(tmp_path, _before_the_auction)

    by_records = _by_records(tmp_path, panel, identifiers)
    by_configuration = backtest_strategy(
        PanelStore(tmp_path / "panel"),
        strategy_request(
            **configured, start=panel.sessions[1], end=panel.sessions[-1], as_of=READ_AT
        ),
    )

    assert _traded(by_records) == _traded(by_configuration)


def test_a_configuration_and_its_records_rebalance_alike_on_days_off_the_grid(
    tmp_path: Path,
) -> None:
    """`rebalance_days` replaces the grid for every source: the configuration's own factor source
    scored on the days named -- here s1, s2 and s5, a grid of 3 caught up and held off -- and the
    records filed on those days at 18:30 trade the same book."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = _base(**STATIC)
    days = [sessions[1], sessions[2], sessions[5]]

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=_read_at(day)
        )

    identifiers = _register_days(tmp_path, request_for, days, sessions[1])
    store = PanelStore(tmp_path / "panel")
    by_records = backtest_strategy(
        store,
        strategy_request(
            **_base(rebalance_every_sessions=3),
            prediction_ids=identifiers,
            rebalance_days=days,
            start=sessions[1],
            end=sessions[-1],
            as_of=READ_AT,
        ),
        predictions=FilePredictionStore(tmp_path / "predictions", clock=lambda: READ_AT).get,
    )
    by_configuration = backtest_strategy(
        store,
        strategy_request(
            **configured,
            rebalance_days=days,
            start=sessions[1],
            end=sessions[-1],
            as_of=READ_AT,
        ),
    )

    assert [period.start for period in by_configuration.periods] == days
    assert _traded(by_records) == _traded(by_configuration)


def test_a_record_filed_as_the_next_call_auction_starts_refuses_the_book(tmp_path: Path) -> None:
    """Filed at 09:15 -- which `register_prediction` refuses, so it is put in the store directly
    -- a record could have seen the auction that prices the book's first trade on it. The book
    reading it is refused, naming the cutoff; the other records alone are not enough."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = _base(**STATIC)
    day = sessions[1]
    request = strategy_request(
        **configured, start=day - timedelta(days=1), end=day, as_of=_read_at(day)
    )
    store = PanelStore(tmp_path / "panel")
    signal = score_day(store, request, day=day, anchor=day)
    calendar = daily._outcome_calendar(store, request.exchange, day, request.as_of)
    late = _at_the_auction(day, calendar.next_trading_day(day))
    batch = signal_day_batch(signal, request, REGISTERED, predicted_at=late)
    assert batch is not None
    written = FilePredictionStore(tmp_path / "predictions", clock=lambda: late).put(
        batch=batch, calendar=calendar, zone=daily.SHANGHAI
    )
    assert written.record.standing == "forward"

    with pytest.raises(StrategyRunBlockedError) as refused:
        _by_records(tmp_path, panel, [written.record.record_id])

    assert late.isoformat() in str(refused.value)
    assert "call auction" in str(refused.value)


def test_the_days_targets_are_the_book_the_backtest_holds_after_that_rebalance(
    tmp_path: Path,
) -> None:
    """Scored on each signal day with the previous day's targets as the book, the command's
    targets are the holdings the backtest carries out of that rebalance.

    The book here is sized so every order fills whole -- 5,000 yuan a position and the whole of
    the signal session's turnover available -- because the command's targets are the book's
    decision and a backtest's holdings are its decision less what the market refused: at the
    protocol's 1% participation this eight-name corpus sells part of a position and buys nothing.
    The buffer keeps s1's third name at s4 (ranked fourth) and drops it at s7 (ranked seventh).
    """
    panel = write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    sessions = panel.sessions
    configured = _base(
        **STATIC,
        buffer_rank=4,
        position_capital=Decimal("5000"),
        participation_cap=Decimal("1"),
    )
    result = backtest_strategy(
        store, strategy_request(**configured, start=sessions[1], end=sessions[-1], as_of=READ_AT)
    )
    assert all(not period.rejections and not period.capped_orders for period in result.periods)
    assert [len(period.holdings) for period in result.periods] == [3, 3, 3]
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(2026,), as_of=READ_AT)
    previous: dict[str, str] = {}
    previous_day: date | None = None
    for period in result.periods:
        request = strategy_request(
            **configured,
            start=period.start - timedelta(days=1),
            end=period.start,
            as_of=_read_at(period.start),
        )
        signal = score_day(store, request, day=period.start, anchor=sessions[1])
        schedule = daily.schedule_of(
            calendar, anchor=sessions[1], session=period.start, every=3, previous=previous_day
        )
        targets = daily.target_weights(signal, request, previous, schedule=schedule)
        assert targets["decision"] == "rebalanced"
        assert tuple(targets["weights"]) == period.holdings
        previous, previous_day = targets["weights"], period.start


def test_a_walk_forward_day_between_refits_uses_the_fit_the_schedule_from_the_anchor_made(
    tmp_path: Path,
) -> None:
    """With refits every second session from s1, s6's fit in use is s5's -- the fit a backtest
    from s1 carries into s6. Held against that backtest itself (signalling every session, so s6
    is one of its signal days): the same artifact, and the same score for every security."""
    panel = write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    sessions = panel.sessions
    configured = _base(**WALK_FORWARD)
    request = strategy_request(
        **configured, start=sessions[5], end=sessions[6], as_of=_read_at(sessions[6])
    )

    signal = score_day(store, request, day=sessions[6], anchor=sessions[1])
    backtest = load_strategy_inputs(
        store,
        strategy_request(
            **{**configured, "rebalance_every_sessions": 1},
            start=sessions[1],
            end=sessions[-1],
            as_of=READ_AT,
        ),
    )
    in_use = backtest.fit_for_day[sessions[6]]

    assert signal.refit_day == sessions[5]
    assert in_use.refit_day == sessions[5]
    assert signal.fit is not None and signal.fit.artifact == in_use.artifact
    assert signal.model_batch is not None
    assert {
        row.ts_code: row.score for row in signal.model_batch.predictions if row.score is not None
    } == {row.subject: row.value for row in backtest.scores if row.signal_day == sessions[6]}


# --- the book the command actually recommended (V2-P6-011, for V2-P6-012) ----------------------


FILLED: Final[dict[str, object]] = {
    **CONFIG,
    "position_capital": Decimal("1000000"),
    "participation_cap": Decimal("1"),
}
"""`CONFIG` with a book sized so every order fills whole: the command's targets are the book's
decision, and a backtest's holdings are that decision less what the market refused."""


def test_a_book_on_the_journals_rebalance_days_holds_what_the_command_recommended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command ran on 19, 21, 22 and 23 January and missed the 20th, a scheduled rebalance:
    it rebalanced on the 19th (its first day), caught the 20th up on the 21st, rebalanced on the
    22nd on schedule and held on the 23rd. `journalled_rebalances` gives those three sessions and
    the record each used; a backtest of those records on those days -- not the fixed grid, which
    would rebalance on the 20th, a day with no recommendation -- holds exactly the targets the
    command printed, every rebalance. Every record was filed at 18:35."""
    world = _world(tmp_path, monkeypatch, Market(open_days=OPEN_2026), config=FILLED)
    results = {days: _next(world, capsys, days) for days in (0, 2, 3, 4)}
    directory = _journal(world, DAY).parent

    journalled = daily.journalled_days(directory)
    rebalances = daily.journalled_rebalances(directory)

    assert [(day.session.day, day.decision) for day in journalled] == [
        (19, "rebalanced"),
        (21, "rebalanced"),
        (22, "rebalanced"),
        (23, "not a rebalance day"),
    ]
    assert [(session.day, record_id) for session, record_id in rebalances] == [
        (19 + days, results[days]["prediction"]["record_id"]) for days in (0, 2, 3)
    ]
    last = DAY + timedelta(days=4)
    book = backtest_strategy(
        PanelStore(world.runtime / "panel"),
        strategy_request(
            **{
                **daily.strategy_arguments(FILLED),
                "components": (),
                "prediction_ids": [record_id for _, record_id in rebalances],
                "rebalance_days": [session for session, _ in rebalances],
                "benchmarks": (EQUAL_WEIGHT_ALL_A,),
            },
            start=DAY,
            end=last,
            as_of=_evening(last),
        ),
        predictions=FilePredictionStore(world.runtime / "predictions", clock=lambda: RUN_CLOCK).get,
    )

    assert [period.start for period in book.periods] == [session for session, _ in rebalances]
    assert all(not period.rejections for period in book.periods)
    assert [period.holdings for period in book.periods] == [
        tuple(results[days]["targets"]["weights"]) for days in (0, 2, 3)
    ]
    assert tuple(results[4]["targets"]["weights"]) == book.periods[-1].holdings


def _journalled(
    directory: Path, day: date, decision: str, record_id: str | None, *, complete: bool = True
) -> None:
    body: dict[str, Any] = {
        "schema": daily.DAILY_SELECTION_SCHEMA,
        "session": day.isoformat(),
        "registration": {"sha256": "0" * 64},
    }
    if complete:
        body["result"] = {
            "session": day.isoformat(),
            "candidates": {"held": decision == "held"},
            "targets": {"decision": decision, "reason": "why", "weights": {}},
            "prediction": (
                {"registered": False, "reason": "the source held today"}
                if record_id is None
                else {"registered": True, "record_id": record_id}
            ),
        }
    daily.write_journal(directory / f"{day.isoformat()}.json", body)


def test_the_journal_says_which_days_held_and_which_rebalanced_on_which_record(
    tmp_path: Path,
) -> None:
    """A day the source held is journalled as held, with no record: it is evidence the book did
    not rebalance there, not a gap. A day whose run stopped before its summary has no result and
    is not a day the command recommended anything on. A rebalance without a record is refused."""
    first, held, stopped, caught_up = (date(2026, 1, day) for day in (19, 20, 21, 22))
    _journalled(tmp_path, first, "rebalanced", "prd_a")
    _journalled(tmp_path, held, "held", None)
    _journalled(tmp_path, stopped, "rebalanced", "prd_x", complete=False)
    _journalled(tmp_path, caught_up, "rebalanced", "prd_b")

    days = daily.journalled_days(tmp_path)

    assert [(day.session, day.decision, day.source_held, day.record_id) for day in days] == [
        (first, "rebalanced", False, "prd_a"),
        (held, "held", True, None),
        (caught_up, "rebalanced", False, "prd_b"),
    ]
    assert daily.journalled_rebalances(tmp_path) == ((first, "prd_a"), (caught_up, "prd_b"))

    _journalled(tmp_path, date(2026, 1, 23), "rebalanced", None)
    with pytest.raises(daily.StepFailedError, match="no record"):
        daily.journalled_rebalances(tmp_path)
