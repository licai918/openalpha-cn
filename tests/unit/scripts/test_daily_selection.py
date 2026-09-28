"""`scripts/daily_selection.py`: the one daily selection command (`V2-P6-011`).

Two halves.

**The command, end to end.** A throwaway git repository holds a committed registration; a panel
is built through the real `openalpha panel build` against a scripted Tushare transport generated
here (twelve securities whose closes move every session, every weekday of 2026 open); then the
daily command runs every step for real: the incremental build through the same transport, the
doctor and the dependency gate, the factor build, the scoring, the book's rule, and the prediction
store. Nothing reaches the network (`tests/conftest.py` refuses outbound connections).

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
from panel_fixtures import EXCHANGE
from research_repo import commit_file, git, head
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus

from openalpha_cn import cli
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_view import backtest_strategy, score_day, strategy_request

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str, directory: Path) -> ModuleType:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


registry = _script("registry", ROOT / "scripts" / "research")
daily = _script("daily_selection", ROOT / "scripts")

SECRET_TOKEN: Final[str] = "sk-daily-selection-token-must-not-leak-60011"
YEAR: Final[int] = 2026
OPEN_DAYS: Final[tuple[date, ...]] = tuple(
    day
    for day in (date(YEAR, 1, 5) + timedelta(days=offset) for offset in range(361))
    if day.weekday() < 5 and day.year == YEAR
)
"""Every weekday of 2026 from 5 January: the published calendar, so a label window can be placed
past the newest session with data."""
SECURITIES: Final[tuple[str, ...]] = tuple(f"{600100 + index}.SH" for index in range(12))
DISPUTED_CODE: Final[str] = SECURITIES[0]
DELISTED_ROW: Final[tuple[Any, ...]] = (
    "600199.SH",
    "600199.SH",
    "SSE",
    "主板",
    "D",
    "20100104",
    "20260102",
)
"""A security delisted on 2 January 2026, before the first session here: the registry's
partitions are keyed by lifecycle year, and a real registry has a 2026 one for the same reason."""

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
"""What the scripted upstream publishes; the command's `--dataset` in these tests."""

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
CRITERIA: Final[dict[str, object]] = {"annualized_net_excess_above": "0"}
BOUND: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
    "scripts/daily_selection.py",
)
COMMITTED: Final[datetime] = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)

CALENDAR_FIELDS: Final = ["exchange", "cal_date", "is_open", "pretrade_date"]
REGISTRY_FIELDS: Final = [
    "ts_code",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
]
BAR_FIELDS: Final = [
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
VALUATION_EXTRA: Final = [
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
VALUATION_FIELDS: Final = ["ts_code", "trade_date", "close", *VALUATION_EXTRA]
HALT_FIELDS: Final = ["ts_code", "trade_date", "suspend_type", "suspend_timing"]
LIMIT_FIELDS: Final = ["ts_code", "trade_date", "up_limit", "down_limit"]
FACTOR_FIELDS: Final = ["ts_code", "trade_date", "adj_factor"]


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _response(fields: Sequence[str], items: Sequence[Sequence[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": list(fields), "items": [list(i) for i in items], "has_more": False},
    }


class Market:
    """A scripted Tushare transport over a generated market; it records what it was asked.

    Closes follow a deterministic walk of at most 2% a session, so a one-session reversal ranks
    the market differently every day. `disputed` makes `DISPUTED_CODE`'s adjustment factor step
    on that session while its published `pre_close` does not: the doctor's `return_paths` check
    reports it and the panel is not clean.
    """

    def __init__(self, *, disputed: date | None = None) -> None:
        self.disputed = disputed
        self.payloads: list[str] = []
        closes: dict[str, list[float]] = {}
        for index, code in enumerate(SECURITIES):
            path, previous = [], 10.0 + index
            for position in range(len(OPEN_DAYS)):
                step = ((index * 37 + position * 101) % 17 - 8) / 400
                previous = round(previous * (1 + step), 2)
                path.append(previous)
            closes[code] = path
        self._closes = closes

    def _close(self, code: str, day: date) -> float:
        return self._closes[code][OPEN_DAYS.index(day)]

    def _pre_close(self, code: str, day: date) -> float:
        position = OPEN_DAYS.index(day)
        return self._closes[code][position - 1] if position else 10.0 + SECURITIES.index(code)

    def _factor(self, code: str, day: date) -> float:
        return 1.1 if code == DISPUTED_CODE and self.disputed and day >= self.disputed else 1.0

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_name = str(payload["api_name"])
        self.payloads.append(api_name)
        params: Mapping[str, str] = payload["params"]
        if api_name == TRADING_CALENDAR_DATASET:
            items: list[list[Any]] = []
            previous: str | None = None
            day = date(YEAR, 1, 1)
            while day.year == YEAR:
                is_open = day in OPEN_DAYS
                items.append([params["exchange"], _compact(day), 1 if is_open else 0, previous])
                if is_open:
                    previous = _compact(day)
                day += timedelta(days=1)
            return _response(CALENDAR_FIELDS, items)
        if api_name == STOCK_BASIC_DATASET:
            listed = [[code, code, "SSE", "主板", "L", "20100104", None] for code in SECURITIES]
            return _response(REGISTRY_FIELDS, [*listed, DELISTED_ROW])
        day = datetime.strptime(params["trade_date"], "%Y%m%d").date()
        rows: list[list[Any]] = []
        if day in OPEN_DAYS:
            if api_name == DAILY_DATASET:
                for code in SECURITIES:
                    close, pre_close = self._close(code, day), self._pre_close(code, day)
                    pct_chg = round((close / pre_close - 1) * 100, 2)
                    prices = [close, close, close, close, pre_close, pct_chg]
                    rows.append([code, _compact(day), *prices, 1000.0, 50000.0])
                fields = BAR_FIELDS
            elif api_name == DAILY_BASIC_DATASET:
                rows = [
                    [code, _compact(day), self._close(code, day), *([1.0] * len(VALUATION_EXTRA))]
                    for code in SECURITIES
                ]
                fields = VALUATION_FIELDS
            elif api_name == SUSPENSION_DATASET:
                if day == OPEN_DAYS[0]:
                    rows = [[SECURITIES[-1], _compact(day), "R", None]]
                fields = HALT_FIELDS
            elif api_name == PRICE_LIMIT_DATASET:
                rows = [
                    [
                        code,
                        _compact(day),
                        round(self._pre_close(code, day) * 1.1, 2),
                        round(self._pre_close(code, day) * 0.9, 2),
                    ]
                    for code in SECURITIES
                ]
                fields = LIMIT_FIELDS
            elif api_name == ADJ_FACTOR_DATASET:
                rows = [[code, _compact(day), self._factor(code, day)] for code in SECURITIES]
                fields = FACTOR_FIELDS
            else:
                raise AssertionError(f"unscripted dataset {api_name}")
        else:
            fields = {
                DAILY_DATASET: BAR_FIELDS,
                DAILY_BASIC_DATASET: VALUATION_FIELDS,
                SUSPENSION_DATASET: HALT_FIELDS,
                PRICE_LIMIT_DATASET: LIMIT_FIELDS,
                ADJ_FACTOR_DATASET: FACTOR_FIELDS,
            }[api_name]
        if "ts_code" in params:
            rows = [row for row in rows if row[0] == params["ts_code"]]
        return _response(fields, rows)


@dataclass
class World:
    repo: Path
    registration: Path
    runtime: Path
    market: Market


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, market: Market) -> World:
    """A committed registration, and a panel seeded through the real `panel build` to 12 Jan."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for name in BOUND:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "initial", at=COMMITTED - timedelta(days=1))
    registration = repo / "docs" / "research" / "p6-registration.json"
    registry.register(CONFIG, CRITERIA, registration, code_commit=head(repo))
    commit_file(repo, registration, "register", at=COMMITTED)
    package = repo / "src" / "openalpha_cn" / "__init__.py"
    research = repo / "scripts" / "research"
    monkeypatch.setattr(registry, "_imported_package", lambda: package)
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: market)
    runtime = tmp_path / "runtime"
    arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(YEAR)]
    arguments += ["--as-of", SEEDED_AS_OF, "--json"]
    for target in TARGETS:
        arguments += ["--dataset", target]
    seeded = daily.invoke(arguments)
    assert seeded.exit_code == 0, seeded.reason()
    market.payloads.clear()
    return World(repo=repo, registration=registration, runtime=runtime, market=market)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return _world(tmp_path, monkeypatch, Market())


def _run(
    world: World,
    capsys: pytest.CaptureFixture[str],
    *,
    as_of: datetime = DAY_AS_OF,
    clock: datetime = RUN_CLOCK,
    json_output: bool = True,
) -> tuple[int, Any, str]:
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", as_of.isoformat()]
    for target in TARGETS:
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
    assert targets["decision"] == "rebalanced"  # no book yet, so the first day builds one
    assert list(targets["weights"]) == sorted(row["ts_code"] for row in listed["candidates"][:3])
    assert set(targets["weights"].values()) == {"0.3333333333"}
    prediction = result["prediction"]
    assert prediction["registered"] is True
    assert prediction["standing"] == "forward"
    assert prediction["recorded_at"] < prediction["outcome_known_at"]
    assert prediction["scored"] == len(SECURITIES)
    assert _records(world.runtime) == (prediction["record_id"],)


def test_the_terminal_summary_names_the_day_the_list_the_book_the_record_and_the_requests(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, err = _run(world, capsys, json_output=False)

    assert code == 0, err
    for label in (
        "session            2026-01-19",
        "candidate list     dsl_",
        "target weights     rebalanced, 3 name(s)",
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
    world.market.payloads.clear()

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
    world.market.payloads.clear()

    code, again, err = _run(world, capsys, as_of=DAY_AS_OF + timedelta(hours=2))

    assert code == 0, err
    assert world.market.payloads == []
    assert again["as_of"] == first["as_of"]  # the journalled clock, not this run's
    assert again["prediction"]["record_id"] == first["prediction"]["record_id"]


def test_the_next_session_rebalances_from_the_book_the_day_before_held(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """20 January is a rebalance on the schedule from 12 January; 21 January is not, so its
    book is the 20th's whatever the ranking says."""
    code, first, err = _run(world, capsys)
    assert code == 0, err
    held = first["targets"]["weights"]
    code, second, err = _run(
        world,
        capsys,
        as_of=DAY_AS_OF + timedelta(days=1),
        clock=RUN_CLOCK + timedelta(days=1),
    )
    assert code == 0, err
    assert second["targets"]["decision"] == "rebalanced"
    assert second["targets"]["previous_session"] == DAY.isoformat()
    ranked = [row["ts_code"] for row in second["candidates"]["candidates"]]
    kept = {name for name in held if name in ranked[:4]}
    assert kept <= set(second["targets"]["weights"])
    assert len(second["targets"]["weights"]) == 3

    code, third, err = _run(
        world,
        capsys,
        as_of=DAY_AS_OF + timedelta(days=2),
        clock=RUN_CLOCK + timedelta(days=2),
    )
    assert code == 0, err
    assert third["targets"]["decision"] == "not a rebalance day"
    assert third["targets"]["weights"] == second["targets"]["weights"]
    assert third["targets"]["turnover"] == "0.0000000000"
    assert len(_records(world.runtime)) == 3


def test_a_panel_the_doctor_does_not_clear_stops_the_run_before_any_factor_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = _world(tmp_path, monkeypatch, Market(disputed=DAY))

    code, _text, err = _run(world, capsys)

    assert code == 1
    assert "step 3 (panel doctor) failed" in err
    # The doctor's own verdict, named: the gate would refuse this panel too, and a run that
    # stopped only there would have skipped the check this step is named for.
    assert "`panel doctor` is not clean (exit 1)" in err
    assert f"return_path_disagreement: {DISPUTED_CODE} on {DAY.isoformat()}" in err
    store = PanelStore(world.runtime / "panel")
    assert store.registered_years("factor_obs_reversal_1d_v1") == ()
    assert _records(world.runtime) == ()
    assert "result" not in json.loads(_journal(world, DAY).read_text(encoding="utf-8"))


def test_a_day_whose_outcome_is_already_knowable_is_not_registered(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run a month late, the day's scores would be a backfill: nothing is filed."""
    code, _text, err = _run(world, capsys, clock=RUN_CLOCK + timedelta(days=30))

    assert code == 1
    assert "step 7 (prediction) failed" in err
    assert "backfill" in err
    assert _records(world.runtime) == ()


def test_the_doctor_is_asked_about_the_days_market_and_the_index_codes_the_panel_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default update's datasets, less the registry (keyed by lifecycle year) and the
    industry targets; with the index datasets, the three index codes the panel builds -- without
    them the doctor reports `check_unavailable` and blocks."""
    asked: list[list[str]] = []

    def invoked(arguments: Sequence[str]) -> Any:
        asked.append(list(arguments))
        return daily.Invocation(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(daily, "invoke", invoked)
    datasets = daily.doctor_datasets(daily.DAILY_TARGETS + daily.INDUSTRY_TARGETS)
    daily.check_panel(tmp_path, datasets=datasets, session=DAY, as_of=DAY_AS_OF, exchange="SSE")

    assert STOCK_BASIC_DATASET not in datasets
    assert {"index_classify", "index_member_all"}.isdisjoint(datasets)
    assert {DAILY_DATASET, DAILY_BASIC_DATASET, SUSPENSION_DATASET, "income"} <= set(datasets)
    assert [call[:2] for call in asked] == [["panel", "doctor"], ["data-check", "--runtime-dir"]]
    for call in asked:
        codes = [call[index + 1] for index, flag in enumerate(call) if flag == "--index-code"]
        assert codes == ["000300.SH", "000905.SH", "000852.SH"]
        assert call[call.index("--session") + 1] == DAY.isoformat()


def test_the_launchd_job_is_printed_for_weekdays_at_1830_and_nothing_is_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = daily.main(
        [
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--launchd-plist",
            str(tmp_path / "logs"),
        ]
    )
    out, _err = capsys.readouterr()

    assert code == 0
    assert out.count("<key>Hour</key><integer>18</integer>") == 5
    assert out.count("<key>Minute</key><integer>30</integer>") == 5
    assert "scripts/daily_selection.py" in out
    assert "--env-file .env" in out
    assert not (tmp_path / "runtime").exists()
    assert not (tmp_path / "logs").exists()


# --- the registered score is the backtest's ------------------------------------------------------


def _registration(config: Mapping[str, object]) -> Any:
    return daily.Registration(
        path=Path("p6-registration.json"),
        sha256="0" * 64,
        commit="1" * 40,
        code_commit="2" * 40,
        config=config,
        config_id="3" * 64,
        seed=20260926,
    )


def _evening(day: date) -> datetime:
    """When the equality tests read the corpus: `READ_AT`, after its last session.

    `write_strategy_corpus` stamps its calendar and registry as ingested on 17 January, so they
    are not readable at an earlier evening. Reading later changes nothing a day's score depends
    on -- each build is chosen by the day's own signal instant and each bar by its session -- and
    the records are still registered at each day's signal instant, which is what a backtest
    reading them checks.
    """
    return max(READ_AT, session_publication_instant(day) + timedelta(hours=2))


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
        batch = daily.prediction_batch(signal, request, _registration({}), predicted_at=instant)
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


def _base(panel: Any, **overrides: Any) -> dict[str, Any]:
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
    configured = _base(panel, **source)
    step = configured["rebalance_every_sessions"]
    signal_days = [sessions[index] for index in range(first, len(sessions) - 1, step)]

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=_evening(day)
        )

    identifiers = _register_days(tmp_path, request_for, signal_days, start)
    by_records = backtest_strategy(
        store,
        strategy_request(
            **_base(panel, rebalance_every_sessions=step),
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
        panel,
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
    previous: dict[str, str] = {}
    for period in result.periods:
        request = strategy_request(
            **configured,
            start=period.start - timedelta(days=1),
            end=period.start,
            as_of=_evening(period.start),
        )
        signal = score_day(store, request, day=period.start, anchor=sessions[1])
        targets = daily.target_weights(signal, request, previous)
        assert targets["decision"] == "rebalanced"
        assert tuple(targets["weights"]) == period.holdings
        previous = targets["weights"]


def test_a_walk_forward_day_between_refits_uses_the_fit_the_schedule_from_the_anchor_made(
    tmp_path: Path,
) -> None:
    """With refits every second session from s1, s6's fit in use is s5's, trained on the window
    ending at s5 -- the fit a backtest from s1 would carry into s6."""
    panel = write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    sessions = panel.sessions
    configured = _base(panel, **WALK_FORWARD)
    request = strategy_request(
        **configured, start=sessions[5], end=sessions[6], as_of=_evening(sessions[6])
    )

    signal = score_day(store, request, day=sessions[6], anchor=sessions[1])

    assert signal.refit_day == sessions[5]
    assert signal.fit is not None and signal.fit.refit_day == sessions[5]
    assert signal.model_batch is not None
    assert signal.scores.ranked is not None
    scored = {row.ts_code: row.score for row in signal.model_batch.predictions}
    assert {name: scored[name] for name in signal.scores.ranked} == pytest.approx(
        {name: scored[name] for name in scored if scored[name] is not None}
    )
