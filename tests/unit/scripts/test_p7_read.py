"""`scripts/research/p7_read.py` (`V2-P7-024`): the command for the one read.

`register [--dry-run]` writes the adopted stop rule and the exact number of periods the plan
expects, after the panel's own calendar makes that number; `read` checks the same number before it
claims anything and then calls `p7_reading.read_once`. Every repository is a throwaway under
`tmp_path`, every panel is a calendar written at test time, and the measure is a stand-in, so no
outcome data is read here.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import _batch as batch
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from research_repo import commit_file, git, head

from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import write_trading_calendar

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


registry = _research_module("registry")
p7_reading = _research_module("p7_reading")
p7_windows = _research_module("p7_windows")
COMMITTED: Final[datetime] = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
BOUND: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
)
AS_OF_REASON: Final[str] = (
    "the 2026-10-09 08:00 panel is the newest snapshot before the code freeze, and every "
    "revision published before it is part of what the reading may know"
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for name in BOUND:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "initial", at=COMMITTED - timedelta(days=1))
    research = repo / "scripts" / "research"
    monkeypatch.setattr(
        registry, "_imported_package", lambda: repo / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )
    monkeypatch.setattr(
        p7_reading,
        "_imported_p7_scripts",
        lambda: tuple(
            research / f"{name}.py" for name in ("p7_windows", "p7_diagnostics", "p7_reading")
        ),
    )
    return repo


@pytest.fixture(scope="module")
def calendar_panel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A panel whose only dataset is `trade_cal` 2021..2023, every weekday a session: the plan's
    stride 20 and h=20 make exactly 25 non-overlapping periods over the validation window."""
    root = tmp_path_factory.mktemp("calendar")
    store = PanelStore(root / "panel")
    for year in (2021, 2022, 2023):
        write_trading_calendar(store, _calendar_batch(year))
    return root


@pytest.fixture(scope="module")
def reduced_calendar_panel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same calendar with one session in twenty closed, every twentieth from the twenty-first:
    one non-overlapping period fewer than the registration's prior (24, not 25), so a read must
    stop at the count -- a one-period difference, not a gross one."""
    root = tmp_path_factory.mktemp("reduced-calendar")
    store = PanelStore(root / "panel")
    closed = frozenset(_sessions(date(2022, 1, 4), date(2023, 12, 29))[20::20])
    for year in (2021, 2022, 2023):
        write_trading_calendar(store, _calendar_batch(year, closed=closed))
    return root


def _sessions(first: date, last: date) -> list[date]:
    days: list[date] = []
    day = first
    while day <= last:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _calendar_batch(year: int, *, closed: frozenset[date] = frozenset()) -> Any:
    days = [date(year, 1, 1) + timedelta(days=offset) for offset in range(366)]
    days = [day for day in days if day.year == year]
    is_open = [day.weekday() < 5 and day not in closed for day in days]
    previous: list[str | None] = []
    seen: date | None = None
    for day, open_that_day in zip(days, is_open, strict=True):
        previous.append(None if seen is None else seen.isoformat())
        if open_that_day:
            seen = day
    published = midnight_shanghai(date(year, 1, 1))
    return batch(
        TRADING_CALENDAR_DATASET,
        subjects=["SSE"] * len(days),
        columns=[
            PanelColumn("cal_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("is_open", "boolean", tuple(is_open)),
            PanelColumn("pretrade_date", "string", tuple(previous)),
        ],
        event_time=[midnight_shanghai(day) for day in days],
        available_time=[published] * len(days),
        fetched_at=published,
    )


def _command(*argv: str) -> int:
    return _research_module("p7_read").main(list(argv))


def _register_arguments(
    repo: Path,
    runtime_dir: Path,
    *,
    window: str = "validation",
    expected: int | None = 25,
    reading: str | None = None,
) -> list[str]:
    arguments = [
        "register",
        "--repo",
        str(repo),
        "--runtime-dir",
        str(runtime_dir),
        "--window",
        window,
        "--as-of",
        "2026-10-09T00:00:00+08:00",
        "--as-of-reason",
        AS_OF_REASON,
        "--stride",
        "20",
        "--horizons",
        "20",
        "--score",
        "composite",
        "--transform",
        "cross_section_standard/v1",
        "--tails",
        "1",
        "2",
        "3",
        "--book",
        '{"seed": 0}',
        "--min-coverage",
        "0.5",
        "--max-carry-sessions",
        "250",
        "--exchange",
        "SSE",
    ]
    if expected is not None:
        arguments += ["--expected-periods", str(expected)]
    if reading is not None:
        arguments += ["--reading", reading]
    return arguments


def _without(arguments: list[str], flag: str) -> list[str]:
    """`arguments` with `flag` and the value(s) after it removed, until the next option."""
    kept: list[str] = []
    skipping = False
    for token in arguments:
        if skipping:
            if token.startswith("--"):
                skipping = False
            else:
                continue
        if token == flag:
            skipping = True
            continue
        kept.append(token)
    return kept


def _read_arguments(repo: Path, runtime_dir: Path, registration: Path) -> list[str]:
    return [
        "read",
        "--repo",
        str(repo),
        "--runtime-dir",
        str(runtime_dir),
        "--registration",
        str(registration),
    ]


def _document(*, n: int = 25, ic: float = 0.05, spread: float = 0.01) -> dict[str, Any]:
    return {
        "kind": "p7-diagnostic-read-once/v1",
        "summary": {
            "composite": {
                "h20": {
                    "rank_ic": {"n": n, "mean": ic},
                    "ew_minus_worst_decile": {"n": n, "mean": spread},
                }
            }
        },
    }


class Measure:
    """A stand-in measure: records the windows it was called for."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = _document() if result is None else result
        self.calls: list[str] = []

    def __call__(
        self, body: Mapping[str, Any], window: Any, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(window.name)
        return {**self.result, "binding": dict(binding)}


def test_the_command_registers_the_adopted_rule_and_the_expected_count(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _command(*_register_arguments(repo, calendar_panel))

    assert code == 0, capsys.readouterr().err
    path = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["expected_periods"] == 25
    assert body["stop_rule"]["horizon"] == 20
    assert body["stop_rule"]["minimum_periods"] == 25
    assert [clause["metric"] for clause in body["stop_rule"]["any_of"]] == [
        "rank_ic",
        "ew_minus_worst_decile",
    ]
    assert "exactly 25" in capsys.readouterr().out


def test_the_command_stops_when_the_calendar_does_not_hold_the_expected_periods(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The plan's prior is 24 (484 real sessions); this fixture's calendar makes 25. A number
    that is not the prior stops the procedure: nothing is written, nothing is claimed, and the
    operator is told to report it -- never to edit 24 to match."""
    code = _command(*_register_arguments(repo, calendar_panel, expected=24))

    said = capsys.readouterr()
    assert code == 4
    assert "exactly 25" in said.err and "expects 24" in said.err
    assert "stop and report" in said.err
    assert not (repo / "docs" / "research" / "p7-readings").exists()


def test_the_dry_run_prints_the_count_and_writes_nothing(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _command(*_register_arguments(repo, calendar_panel), "--dry-run") == 0

    said = capsys.readouterr()
    assert "exactly 25" in said.out and "expects 25" in said.out
    assert not (repo / "docs" / "research" / "p7-readings").exists()

    assert _command(*_register_arguments(repo, calendar_panel, expected=24), "--dry-run") == 4
    assert "stop and report" in capsys.readouterr().err
    assert not (repo / "docs" / "research" / "p7-readings").exists()


def test_the_command_reads_the_validation_window_once_and_writes_the_verdict(
    repo: Path,
    calendar_panel: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _command(*_register_arguments(repo, calendar_panel)) == 0
    registration = (
        repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    )
    commit_file(repo, registration, "the registration", at=COMMITTED)

    measure = Measure()
    monkeypatch.setattr(p7_reading, "_panel_measure", lambda runtime_dir: measure)
    monkeypatch.setattr(p7_reading, "preflight_problems", lambda *a, **k: ())

    code = _command(*_read_arguments(repo, calendar_panel, registration))

    out = capsys.readouterr().out
    assert code == 0, out
    assert "PRE-REGISTERED READ-ONCE" in out and "stop rule not triggered" in out
    assert measure.calls == ["validation"]
    assert registration.with_name("v2-p7-024-validation.result.json").exists()
    assert registration.with_name("validation.window-claim.json").exists()
    assert _command(*_read_arguments(repo, calendar_panel, registration)) == 3


def test_the_command_refuses_second_use_before_the_validation_claim_is_committed(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _command(
        *_register_arguments(
            repo, calendar_panel, window="second_use", expected=None, reading="v2-p7-024-second-use"
        )
    )
    assert code == 0, capsys.readouterr().err
    registration = (
        repo / "docs" / "research" / "p7-readings" / "v2-p7-024-second-use.registration.json"
    )
    commit_file(repo, registration, "the registration", at=COMMITTED)

    assert _command(*_read_arguments(repo, calendar_panel, registration)) == 3
    assert "validation window" in capsys.readouterr().err


def test_the_command_stops_before_the_claim_when_the_read_panel_makes_another_count(
    repo: Path,
    calendar_panel: Path,
    reduced_calendar_panel: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The count is checked again on the panel the read actually opens, not trusted from the
    registration: a panel whose calendar makes another number stops the read before any claim,
    with a code of its own (4)."""
    assert _command(*_register_arguments(repo, calendar_panel)) == 0
    registration = (
        repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    )
    commit_file(repo, registration, "the registration", at=COMMITTED)

    code = _command(*_read_arguments(repo, reduced_calendar_panel, registration))

    said = capsys.readouterr()
    assert code == 4
    assert "exactly 24" in said.err and "expects 25" in said.err
    assert "stop and report" in said.err
    assert not registration.with_name("validation.window-claim.json").exists()
    assert not registration.with_name("v2-p7-024-validation.claim.json").exists()
    assert not registration.with_name("v2-p7-024-validation.result.json").exists()


def test_the_command_refuses_the_research_window_by_name(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _command(*_register_arguments(repo, calendar_panel, window="research", expected=None))

    assert code == 2
    assert "explore" in capsys.readouterr().err
    assert not (repo / "docs" / "research" / "p7-readings").exists()


def test_the_command_surfaces_a_shallow_repository_as_a_refusal(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _command(*_register_arguments(repo, calendar_panel)) == 0
    registration = (
        repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    )
    commit_file(repo, registration, "the registration", at=COMMITTED)
    (repo / ".git" / "shallow").write_text(head(repo) + "\n", encoding="utf-8")

    assert _command(*_read_arguments(repo, calendar_panel, registration)) == 3
    assert "git fetch --unshallow" in capsys.readouterr().err


def test_the_command_refuses_a_missing_registration_file_as_usage(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path that is not there is a usage error (2), never a traceback and exit 1."""
    missing = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"

    code = _command(*_read_arguments(repo, calendar_panel, missing))

    said = capsys.readouterr()
    assert code == 2
    assert str(missing) in said.err and "No such file" in said.err
    assert "Traceback" not in said.err
    assert not missing.exists()


def test_the_command_refuses_a_repository_that_is_not_there_as_usage(
    repo: Path,
    calendar_panel: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A `--repo` that does not exist is a usage error (2) on both subcommands, not exit 1."""
    missing_repo = tmp_path / "nowhere"

    assert _command(*_register_arguments(missing_repo, calendar_panel)) == 2
    said = capsys.readouterr()
    assert str(missing_repo) in said.err and "Traceback" not in said.err

    assert _command(*_register_arguments(repo, calendar_panel)) == 0
    registration = (
        repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    )
    commit_file(repo, registration, "the registration", at=COMMITTED)
    capsys.readouterr()

    assert _command(*_read_arguments(missing_repo, calendar_panel, registration)) == 2
    said = capsys.readouterr()
    assert str(missing_repo) in said.err and "Traceback" not in said.err


@pytest.mark.parametrize(
    "flag",
    ["--tails", "--book", "--min-coverage", "--max-carry-sessions", "--exchange"],
)
def test_the_command_requires_every_request_and_config_item(
    repo: Path, calendar_panel: Path, flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """No request or configuration value comes from a code default: a missing one is a usage
    error, so the registration cannot silently carry a value the user never approved."""
    with pytest.raises(SystemExit) as exited:
        _command(*_without(_register_arguments(repo, calendar_panel), flag))

    assert exited.value.code == 2
    assert "required" in capsys.readouterr().err
    assert not (repo / "docs" / "research" / "p7-readings").exists()


def test_the_command_stops_on_a_mismatch_even_when_the_registration_already_exists(
    repo: Path,
    calendar_panel: Path,
    reduced_calendar_panel: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An identical registration at the same path is returned without a panel check by
    `register_reading`; the command checks the count before that, so a panel that now makes
    another number still stops with 4 -- and the registration already on disk is left as it was."""
    assert _command(*_register_arguments(repo, calendar_panel)) == 0
    registration = (
        repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    )
    before = registration.read_bytes()
    capsys.readouterr()

    code = _command(*_register_arguments(repo, reduced_calendar_panel))

    said = capsys.readouterr()
    assert code == 4
    assert "exactly 24" in said.err and "expects 25" in said.err
    assert registration.read_bytes() == before


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--transform", "no_such/v1"), ("--score", "momentum")],
)
def test_a_dry_run_validates_the_registration_a_secondary_reading_would_write(
    repo: Path,
    calendar_panel: Path,
    flag: str,
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A secondary reading has no stop rule, so nothing else here would read its request and
    configuration; the dry run validates the body register_reading would write and exits 2."""
    arguments = _register_arguments(repo, calendar_panel, window="second_use", expected=None)
    arguments[arguments.index(flag) + 1] = value

    assert _command(*arguments, "--dry-run") == 2
    said = capsys.readouterr()
    assert "Traceback" not in said.err
    assert not (repo / "docs" / "research" / "p7-readings").exists()
