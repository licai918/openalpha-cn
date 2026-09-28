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
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
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
    session_publication_instant,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import RegisteredConfiguration, signal_day_batch
from openalpha_cn.strategy_view import (
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


def _repository(root: Path, config: Mapping[str, object]) -> tuple[Path, Path]:
    """A throwaway repository holding one file under each bound path and a committed
    registration of `config`."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "--template=")
    for name in BOUND:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
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


def test_a_refused_panel_update_stops_the_day_counts_its_requests_and_pins_its_clock(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The live check's own refusal, reproduced: a band the upstream served for the stored
    horizon's session (12 January) and no longer serves. The overlap re-fetch of that session
    drops it and the writer refuses, as it must; the command stops at step 2, says so with
    the requests it spent, and a retry asks the same question at the same pinned `--as-of`."""
    store = PanelStore(world.runtime / "panel")
    arguments = ["panel", "build", "--runtime-dir", str(world.runtime), "--year", str(YEAR)]
    arguments += ["--as-of", SEEDED_AS_OF, "--dataset", PRICE_LIMIT_DATASET]
    world.market.bands_only = {date(2026, 1, 12): ("159999.SZ",)}
    reseeded = daily.invoke(arguments)
    assert reseeded.exit_code == 0, reseeded.reason()
    world.market.bands_only = {}
    world.market.clear()

    code, _text, err = _run(world, capsys)

    assert code == 1
    assert "step 2 (panel update) failed" in err
    assert "would drop ['159999.SZ']" in err
    assert "tushare requests   32 this run" in err
    journal = json.loads(_journal(world, DAY).read_text(encoding="utf-8"))
    assert (journal["as_of"], "panel" in journal) == (DAY_AS_OF.isoformat(), False)
    assert store.registered_years("factor_obs_reversal_1d_v1") == ()

    code, _text, err = _run(world, capsys, as_of=DAY_AS_OF + timedelta(hours=3))

    assert code == 1
    assert "would drop ['159999.SZ']" in err
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
    # new one) of the five price APIs plus the calendar and the registry -- and, on a rebalance
    # day, the tree (two vintages) and the memberships (two states per level-one industry).
    industries = 2 + 2 * len(L1_CODES)
    assert counts["cadence"] == [2 + 5 * 6 + industries, 12 + industries, 12, 12 + industries, 12]
    assert counts["daily"] == [2 + 5 * 6 + industries] + [12 + industries] * 4


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
    dirty worktree."""
    repo, registration = _repository(tmp_path, CONFIG)
    registered_at = head(repo)
    source = repo / "src" / "openalpha_cn" / "strategy.py"
    source.write_text("RULES = 2\n", encoding="utf-8")
    commit_file(repo, source, "development moves on", at=COMMITTED + timedelta(days=3))
    pinned = tmp_path / 'pinned daily $HOME "x"'

    code = daily.main(
        ["--pin-worktree", str(pinned), "--registration", str(registration), "--repo", str(repo)]
    )
    out, err = capsys.readouterr()

    assert code == 0, err
    assert out.strip().endswith(registered_at)
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
    env_file, venv, uv = odd / ".env", odd / ".venv", odd / "bin" / "uv"

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
            "--venv",
            str(venv),
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
        "--active",
        "--env-file",
        str(env_file),
        "python",
        str(worktree / "scripts" / "daily_selection.py"),
        "--runtime-dir",
        str(runtime),
        "--repo",
        str(worktree),
        "--registration",
        str(worktree / "docs" / "research" / "p6-registration.json"),
    ]
    assert job["WorkingDirectory"] == str(worktree)
    assert job["EnvironmentVariables"]["PYTHONPATH"] == str(worktree / "src")
    assert job["EnvironmentVariables"]["VIRTUAL_ENV"] == str(venv)
    assert job["StartCalendarInterval"] == [
        {"Weekday": weekday, "Hour": 18, "Minute": 30} for weekday in range(1, 6)
    ]
    assert job["RunAtLoad"] is False
    assert not odd.exists()


# --- the registered score is the backtest's ------------------------------------------------------


REGISTERED: Final = RegisteredConfiguration(
    config_id="3" * 64, registration_sha256="0" * 64, code_commit="2" * 40, seed=20260926
)


def _read_at(day: date) -> datetime:
    """When the equality tests read the corpus: `READ_AT`, after its last session.

    `write_strategy_corpus` stamps its calendar and registry as ingested on 17 January, so they
    are not readable at an earlier evening. Reading later changes nothing a day's score depends
    on -- each build is chosen by the day's own signal instant and each bar by its session -- and
    the records are still registered at each day's signal instant, which is what a backtest
    reading them checks.
    """
    return max(READ_AT, _evening(day))


def _register_days(
    root: Path, request_for: Callable[[date], Any], days: Sequence[date], anchor: date
) -> list[str]:
    """Each day's scores registered through the command's path at that day's signal instant --
    the clock at which a backtest reading them may use them."""
    store = PanelStore(root / "panel")
    identifiers: list[str] = []
    for day in days:
        request = request_for(day)
        signal = score_day(store, request, day=day, anchor=anchor)
        instant = session_publication_instant(day)
        batch = signal_day_batch(signal, request, REGISTERED, predicted_at=instant)
        assert batch is not None
        calendar = daily._outcome_calendar(store, request.exchange, day, request.as_of)
        record, outcome = daily.register_prediction(
            root, batch, calendar=calendar, clock=lambda instant=instant: instant
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
    command's path; a backtest of the records, then, holds, fills and returns exactly what the
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
