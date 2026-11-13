"""`scripts/p7_c_power.py`: plan C's event-level power calculation (plan Task 3.1, ruling D1).

The calculation is planning arithmetic over a **stored panel**: per stored session it rebuilds
that session's survivors (`build_facts_day` then `screen`) and counts the survivors that carry a
limit event (`limit_events_on`). From those counts it projects how many forward sessions of the
registered window would be measurable for two scoring domains -- the primary random sample of
N=300 and the full survivor census -- and gives each row a `testable` / `not_testable` verdict.
It reads no label and no return, constructs no client, and sends no request.

Every reader below is scripted: the panel store is a placeholder, the calendar is a tuple of
sessions, and the builders are test doubles that record how they were called. Nothing here
touches a real panel, a network or a credential, and `main` without `--json` writes nothing.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
import research_repo

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.panel_ingest import session_publication_instant

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SOURCE: Final[Path] = ROOT / "scripts" / "p7_c_power.py"


def _script(name: str) -> Any:
    for directory in (ROOT / "scripts", ROOT / "scripts" / "research"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    return importlib.import_module(name)


power = _script("p7_c_power")

BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=0)
ADAPTER = SimpleNamespace(
    exchange="SSE",
    transform="cross_section_standard/v1",
    min_coverage=0.5,
    max_carry_sessions=250,
)
PANEL = object()


def _registration() -> Any:
    """Step 0's registration as the command's own seam hands it over."""
    return power.StepZeroRegistration(
        path=Path("docs/research/p7-registration.json"),
        config_id="caf-0000000000000001",
        registration_sha256="a" * 64,
        code_commit="b" * 40,
        survivor=SimpleNamespace(book=BOOK, adapter=ADAPTER),
    )


def _counts(*specs: tuple[date, int, int]) -> tuple[Any, ...]:
    return tuple(
        power.SessionCount(session, survivors, events) for session, survivors, events in specs
    )


class _Recording:
    """The scripted readers: every call is recorded, every answer is the test's."""

    def __init__(self, *, trading_days: tuple[date, ...], survivors: tuple[str, ...]) -> None:
        self.trading_days = trading_days
        self.survivors = survivors
        self.calendar: list[dict[str, Any]] = []
        self.builds: list[dict[str, Any]] = []
        self.days: list[Any] = []
        self.screens: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.events_by_session: dict[date, tuple[str, ...]] = {}

    def load_calendar(self, store: Any, *, exchange: str, years: Any, as_of: Any) -> Any:
        self.calendar.append(
            {"store": store, "exchange": exchange, "years": tuple(years), "as_of": as_of}
        )
        return SimpleNamespace(trading_days=self.trading_days)

    def build(self, **kwargs: Any) -> Any:
        self.builds.append(kwargs)
        day = SimpleNamespace(facts=())
        self.days.append(day)
        return day

    def screen(self, facts: Any, config: Any) -> Any:
        self.screens.append({"facts": facts, "config": config})
        return SimpleNamespace(
            survivors=tuple(SimpleNamespace(ts_code=code) for code in self.survivors)
        )

    def limit_events(self, store: Any, *, session: date, as_of: Any, exchange: str) -> Any:
        self.events.append(
            {"store": store, "session": session, "as_of": as_of, "exchange": exchange}
        )
        return tuple(
            SimpleNamespace(ts_code=code) for code in self.events_by_session.get(session, ())
        )


def _patched(
    monkeypatch: pytest.MonkeyPatch, *, trading_days: tuple[date, ...], survivors: tuple[str, ...]
) -> _Recording:
    readers = _Recording(trading_days=trading_days, survivors=survivors)
    monkeypatch.setattr(power, "load_trading_calendar", readers.load_calendar)
    monkeypatch.setattr(power, "build_facts_day", readers.build)
    monkeypatch.setattr(power, "screen", readers.screen)
    monkeypatch.setattr(power, "limit_events_on", readers.limit_events)
    return readers


def _tree(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


ADAPTER_BODY: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "epoch_session": "2020-01-02",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {
        "max_position_weight": "1",
        "max_total_exposure": "1",
        "min_cash_weight": "0",
    },
    "unfilled_buy": "reject_whole_and_carry/v1",
}


def _committed_registration(repo: Path) -> Path:
    """A throwaway git repository holding one committed Step 0 survivor registration."""
    repo.mkdir(parents=True, exist_ok=True)
    research_repo.git(repo, "init", "-q", "--template=", "-b", "main")
    book = SurvivorBookConfig(seed=0)
    body = {
        "schema": "openalpha-research-registration/v1",
        "config": {
            "kind": "survivor_book/v1",
            "survivor_book": book.model_dump(mode="json", exclude_computed_fields=True),
            "adapter": ADAPTER_BODY,
        },
        "config_id": "caf-0000000000000001",
        "criteria": {"note": "not evidence"},
        "code_commit": "a" * 40,
        "settings": {"random_seed": 0},
    }
    path = repo / "docs" / "research" / "p7-registration.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    research_repo.git(repo, "add", "-A")
    research_repo.git(repo, "commit", "-q", "-m", "Step 0's registration")
    return path


# --- the constants the protocol and the registration writer read --------------------------------


def test_the_public_constants_are_the_registered_designs() -> None:
    assert power.POWER_REPORT_SCHEMA == "openalpha-p7-c-power/v1"
    assert power.PRIMARY_SAMPLE_SIZE == 300
    assert power.WINDOW_SESSIONS == 120
    assert power.PRIMARY_HORIZON == 1
    assert power.MIN_SCORED_NAMES == 10
    assert power.MIN_MEASURABLE_SESSIONS == 60
    assert power.PLANNING_BETWEEN_DAY_SD == 0.15
    assert power.TARGET_POWER == 0.8
    assert power.LEVEL == 0.05
    assert power.VERDICT_TESTABLE == "testable"
    assert power.VERDICT_NOT_TESTABLE == "not_testable"


# --- the counts: one stored session, its survivors, its survivors with an event -----------------


def test_the_counts_join_that_sessions_survivors_with_its_limit_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, last = date(2020, 1, 2), date(2020, 1, 3)
    readers = _patched(
        monkeypatch,
        trading_days=(first, last),
        survivors=("000001.SH", "000002.SH"),
    )
    # 600519.SH carries an event but is not a survivor; 000001.SH is both.
    readers.events_by_session = {
        first: ("000001.SH", "600519.SH"),
        last: ("000002.SH",),
    }

    counts = power.historical_event_counts(
        PANEL, first=first, last=last, book=BOOK, adapter=ADAPTER
    )

    assert counts == (
        power.SessionCount(first, survivors=2, event_names=1),
        power.SessionCount(last, survivors=2, event_names=1),
    )
    assert [call["session"] for call in readers.builds] == [first, last]
    assert [call["session"] for call in readers.events] == [first, last]

    # The facts day is rebuilt the way the daily survivor day builds it: at the session's own
    # 16:30, with the registered adapter's settings and nothing held.
    build = readers.builds[0]
    assert build["as_of"] == session_publication_instant(first)
    assert build["config"] is BOOK
    assert build["factor_ids"] == BOOK.composite_factors
    assert build["transform"] is FACTOR_TRANSFORMS.get("cross_section_standard/v1")
    assert build["exchange"] == "SSE"
    assert build["min_coverage"] == 0.5
    assert build["max_carry_sessions"] == 250
    assert build["held"] == ()

    # The events are read at the same instant, on the same calendar.
    event_read = readers.events[0]
    assert event_read["as_of"] == session_publication_instant(first)
    assert event_read["exchange"] == "SSE"
    # The screen sees the facts day the builder returned, against the registration's book.
    assert readers.screens[0]["facts"] is readers.days[0].facts
    assert readers.screens[0]["config"] is BOOK


# --- the calendar iteration ---------------------------------------------------------------------


def test_the_sessions_are_the_stored_calendar_between_first_and_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    days = (
        date(2020, 1, 2),
        date(2020, 1, 3),
        date(2020, 1, 6),
        date(2020, 1, 7),
        date(2020, 1, 8),
    )
    readers = _patched(monkeypatch, trading_days=days, survivors=("000001.SH",))

    counts = power.historical_event_counts(
        PANEL, first=date(2020, 1, 3), last=date(2020, 1, 7), book=BOOK, adapter=ADAPTER
    )

    assert [count.session for count in counts] == [
        date(2020, 1, 3),
        date(2020, 1, 6),
        date(2020, 1, 7),
    ]
    assert len(readers.calendar) == 1
    calendar = readers.calendar[0]
    assert calendar["exchange"] == "SSE"
    assert calendar["years"] == (2020,)
    assert calendar["as_of"] == session_publication_instant(date(2020, 1, 7))

    with pytest.raises(power.PowerRequestError, match="2020-01-04"):
        power.historical_event_counts(
            PANEL, first=date(2020, 1, 4), last=date(2020, 1, 5), book=BOOK, adapter=ADAPTER
        )


# --- the power arithmetic ------------------------------------------------------------------------


def test_a_larger_measured_cross_section_can_detect_a_smaller_mean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = _counts(*((date(2020, 1, day), 2000, 400) for day in (2, 3, 6)))

    report = power.power_report(counts, first=date(2020, 1, 2), last=date(2020, 1, 6))

    n300, census = report["rows"]["n300"], report["rows"]["census"]
    # 400 events among 2000 survivors: about 60 of a 300-name sample carry a score.
    assert n300["detectable_mean"] > census["detectable_mean"]
    assert n300["verdict"] == "testable"
    assert census["verdict"] == "testable"

    narrow = power.event_power(counts, sample=100)
    wide = power.event_power(counts, sample=None)
    assert narrow.detectable_mean is not None
    assert wide.detectable_mean is not None
    assert narrow.detectable_mean > wide.detectable_mean


def test_a_session_with_fewer_than_ten_scored_names_is_not_measurable() -> None:
    below = power.event_power(
        (power.SessionCount(date(2020, 1, 6), survivors=1000, event_names=9),), sample=None
    )
    assert below.observed_measurable_sessions == 0
    assert below.measurable_sessions == 0
    assert below.periods == 0
    assert below.detectable_mean is None
    assert below.verdict == "not_testable"

    at_the_floor = power.event_power(
        (power.SessionCount(date(2020, 1, 6), survivors=1000, event_names=10),), sample=None
    )
    assert at_the_floor.observed_measurable_sessions == 1
    assert at_the_floor.measurable_sessions == 120
    assert at_the_floor.verdict == "testable"
    assert at_the_floor.detectable_mean is not None


def test_the_primary_sample_can_be_unmeasurable_while_the_census_is_not() -> None:
    counts = _counts((date(2020, 1, 6), 2000, 40))

    report = power.power_report(counts, first=date(2020, 1, 6), last=date(2020, 1, 6))

    # 40 events among 2000 survivors: about 6 of a 300-name sample carry a score, below 10.
    assert report["rows"]["n300"]["verdict"] == "not_testable"
    assert report["rows"]["census"]["verdict"] == "testable"


def test_the_verdict_is_not_testable_below_sixty_projected_measurable_sessions() -> None:
    sessions = tuple(date(2020, 1, day) for day in range(1, 11))
    four_of_ten = _counts(
        *((session, 1000, 12 if index < 4 else 3) for index, session in enumerate(sessions))
    )

    below = power.event_power(four_of_ten, sample=None)
    assert below.observed_measurable_sessions == 4
    assert below.measurable_sessions == round(4 / 10 * power.WINDOW_SESSIONS) == 48
    assert below.detectable_mean is not None
    assert below.verdict == "not_testable"

    five_of_ten = _counts(
        *((session, 1000, 12 if index < 5 else 3) for index, session in enumerate(sessions))
    )
    at_the_floor = power.event_power(five_of_ten, sample=None)
    assert at_the_floor.measurable_sessions == 60
    assert at_the_floor.verdict == "testable"


def test_a_horizon_longer_than_one_uses_non_overlapping_periods() -> None:
    counts = _counts(*((date(2020, 1, day), 1000, 100) for day in (2, 3, 6)))

    row = power.event_power(counts, sample=None, horizon=5)

    assert row.measurable_sessions == 120
    assert row.periods == 24


def test_event_power_refuses_a_design_it_cannot_answer() -> None:
    counts = (power.SessionCount(date(2020, 1, 6), survivors=100, event_names=100),)
    for wrong in (
        {"sessions": 0},
        {"horizon": 0},
        {"between_day_sd": 0.0},
        {"between_day_sd": float("nan")},
        {"level": 1.0},
        {"power": 1.0},
        {"sample": 0},
    ):
        with pytest.raises(power.PowerRequestError):
            power.event_power(counts, **{"sample": None, **wrong})
    with pytest.raises(power.PowerRequestError):
        power.event_power((), sample=None)
    with pytest.raises(power.PowerRequestError):
        power.event_power(
            (power.SessionCount(date(2020, 1, 6), survivors=3, event_names=4),), sample=None
        )
    with pytest.raises(power.PowerRequestError):
        power.SessionCount(date(2020, 1, 6), survivors=-1, event_names=0)


# --- the report the registration writer reads ----------------------------------------------------


def test_the_report_is_the_writer_contract_and_round_trips(tmp_path: Path) -> None:
    counts = _counts(*((date(2020, 1, day), 2000, 400) for day in (2, 3, 6)))
    first, last = date(2020, 1, 2), date(2020, 1, 6)

    report = power.power_report(
        counts,
        first=first,
        last=last,
        between_day_sd=0.2,
        level=0.10,
        power=0.9,
        source={"step0_registration": "docs/research/p7-registration.json"},
    )

    assert report["schema"] == power.POWER_REPORT_SCHEMA
    assert report["first"] == first.isoformat()
    assert report["last"] == last.isoformat()
    assert report["sessions_observed"] == 3
    planning = report["planning"]
    assert planning["window_sessions"] == power.WINDOW_SESSIONS
    assert planning["primary_horizon"] == power.PRIMARY_HORIZON
    assert planning["between_day_sd"] == 0.2
    assert planning["test_level"] == 0.10
    assert planning["target_power"] == 0.9
    assert planning["primary_sample_size"] == 300
    assert planning["min_scored_names"] == 10
    assert planning["min_measurable_sessions"] == 60
    assert report["source"] == {"step0_registration": "docs/research/p7-registration.json"}

    rows = report["rows"]
    assert set(rows) == {"n300", "census"}
    for label, size in (("n300", 300), ("census", None)):
        row = rows[label]
        assert row["sample"] == label
        assert row["sample_size"] == size
        assert row["verdict"] in {"testable", "not_testable"}
        for field in ("measurable_sessions", "periods", "detectable_mean"):
            assert field in row
        assert row["periods"] == row["measurable_sessions"]  # h=1

    path = tmp_path / "p7-c-power.json"
    assert power.write_power_report(path, report) == path
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(json.dumps(report))
    assert sorted(item.name for item in tmp_path.iterdir()) == ["p7-c-power.json"]


def test_the_report_carries_no_label_or_return() -> None:
    counts = _counts((date(2020, 1, 6), 2000, 400))
    report = power.power_report(counts, first=date(2020, 1, 6), last=date(2020, 1, 6))

    forbidden = {"label", "labels", "return", "returns", "outcome", "ic", "rank_ic", "realized"}
    keys: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(str(key).lower())
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(report)
    assert keys.isdisjoint(forbidden)
    assert not any("label" in key or "return" in key for key in keys)

    text = power.render_report(report)
    assert "no labels and no returns were read" in text
    assert "not evidence" in text
    assert "候选" in text
    assert "testable" in text
    assert "Tushare" not in text and "DeepSeek" not in text


# --- the command line ----------------------------------------------------------------------------


def test_main_without_json_prints_both_rows_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    panel = tmp_path / "panel"
    panel.mkdir()
    counts = _counts((date(2020, 1, 6), 2000, 40))
    monkeypatch.setattr(power, "_load_step_zero_registration", lambda path, repo: _registration())
    monkeypatch.setattr(power, "_open_panel", lambda path: PANEL)
    monkeypatch.setattr(power, "historical_event_counts", lambda store, **kwargs: counts)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("main wrote a report without --json")

    monkeypatch.setattr(power, "write_power_report", refuse)
    before = _tree(tmp_path)

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--step0-registration",
            "docs/research/p7-registration.json",
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-03",
            "--repo",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert "n300" in captured.out
    assert "census" in captured.out
    assert "N=300" in captured.out
    assert "verdict testable" in captured.out
    assert "verdict not testable" in captured.out
    assert "periods" in captured.out
    assert "detectable mean IC" in captured.out
    assert "projected" in captured.out
    assert "planning assumption" in captured.out
    assert "候选" in captured.out
    assert "not evidence" in captured.out
    assert _tree(tmp_path) == before


def test_main_with_json_writes_the_report_it_prints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    panel = tmp_path / "panel"
    panel.mkdir()
    counts = _counts((date(2020, 1, 6), 2000, 400))
    monkeypatch.setattr(power, "_load_step_zero_registration", lambda path, repo: _registration())
    monkeypatch.setattr(power, "_open_panel", lambda path: PANEL)
    monkeypatch.setattr(power, "historical_event_counts", lambda store, **kwargs: counts)
    report_path = tmp_path / "power.json"

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-03",
            "--repo",
            str(tmp_path),
            "--json",
            str(report_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert f"written: {report_path}" in captured.out
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["schema"] == power.POWER_REPORT_SCHEMA
    assert set(document["rows"]) == {"n300", "census"}
    assert document["rows"]["n300"]["verdict"] == "testable"


def test_a_committed_step0_registration_is_read_and_named_in_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    registration = _committed_registration(repo)
    panel = tmp_path / "panel"
    panel.mkdir()
    counts = _counts((date(2020, 1, 6), 2000, 400))
    monkeypatch.setattr(power, "_open_panel", lambda directory: PANEL)
    monkeypatch.setattr(power, "historical_event_counts", lambda store, **kwargs: counts)
    report_path = tmp_path / "power.json"

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--step0-registration",
            str(registration),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-03",
            "--repo",
            str(repo),
            "--json",
            str(report_path),
        ]
    )
    capsys.readouterr()

    assert code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["source"] == {
        "step0_registration": str(registration),
        "config_id": "caf-0000000000000001",
        "registration_sha256": hashlib.sha256(registration.read_bytes()).hexdigest(),
        "code_commit": "a" * 40,
    }


def test_main_refuses_an_unreadable_registration_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    panel = tmp_path / "panel"
    panel.mkdir()
    registration = tmp_path / "docs" / "research" / "p7-registration.json"
    report_path = tmp_path / "power.json"

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--step0-registration",
            str(registration),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-03",
            "--repo",
            str(tmp_path),
            "--json",
            str(report_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "p7-registration.json" in captured.err
    assert "refused" in captured.err
    assert not report_path.exists()
    assert not registration.exists()


def test_main_refuses_dates_out_of_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code = power.main(
        [
            "--panel-dir",
            str(tmp_path / "panel"),
            "--first",
            "2020-01-03",
            "--last",
            "2020-01-02",
            "--repo",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "2020-01-03" in captured.err
    assert "2020-01-02" in captured.err


def test_main_refuses_a_missing_panel_without_creating_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(power, "_load_step_zero_registration", lambda path, repo: _registration())
    panel = tmp_path / "absent-panel"

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-03",
            "--repo",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "absent-panel" in captured.err
    assert not panel.exists()


# --- no client, no request, no live path ---------------------------------------------------------


FORBIDDEN_MODULES: Final[tuple[str, ...]] = (
    "tushare",
    "requests",
    "urllib",
    "urllib3",
    "httpx",
    "aiohttp",
    "socket",
    "openalpha_cn.models",
    "openalpha_cn.agents",
    "openalpha_cn.model_view",
    "openalpha_cn.providers",
    "openalpha_cn.strategy_survivor_forward",
)


def test_the_module_imports_no_transport_and_has_no_live_path() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SOURCE))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    offenders = sorted(
        module
        for module in imported
        if any(module == banned or module.startswith(f"{banned}.") for banned in FORBIDDEN_MODULES)
    )
    assert offenders == []
    assert "registry" in imported  # not vacuous: the committed-registration reader is there
    assert "--live" not in source
    assert "TUSHARE_TOKEN" not in source
    assert "DEEPSEEK_API_KEY" not in source

    options = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("--")
        and "live" in node.value
    ]
    assert options == []
