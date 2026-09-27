"""`scripts/research/registry.py`: the pre-registration and the one-shot holdout guard.

The holdout may run exactly once, after a registration committed to git (`V2-P6-008`).
`assert_holdout_allowed` refuses four ways and each has its own exception class, so a test here
fails when the check it names is removed even though another check might still refuse the same
situation:

* the registration is not in git's committed history (or the bytes on disk are not the committed
  ones) -- `RegistrationNotCommittedError`;
* the registration's commit is later than a holdout row the ledger already holds --
  `RegistrationAfterHoldoutError`;
* the ledger already holds a holdout row, a claim included -- `HoldoutAlreadyRanError`;
* `src/` at `HEAD` or in the working tree is not `src/` at the registration's code commit --
  `SourceChangedError`.

`run_holdout` adds two more before it measures: a configuration outside the holdout window or not
the registered one (`HoldoutConfigurationError`), and measurement settings that are not the
registered ones (`HoldoutSettingsError`). It writes its claim before it measures, so a crash is
still the one run.

Every git repository here is a throwaway under `tmp_path`, built with no global or system
configuration and every `GIT_*` variable dropped, and each commit's time is pinned through
`GIT_COMMITTER_DATE` so the ordering the guard judges is the one the test wrote.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

from openalpha_cn.strategy_view import StrategyRequestError

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
registry = _research_module("registry")

COMMITTED: Final[datetime] = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
CODE_COMMIT: Final[str] = "0123456789abcdef0123456789abcdef01234567"
CONFIG: Final[dict[str, object]] = {
    "start": date(2024, 1, 2),
    "end": date(2026, 6, 30),
    "holding_count": 50,
    "rebalance_every_sessions": 20,
}
CRITERIA: Final[dict[str, object]] = {
    "annualized_net_excess_above": "0",
    "one_sided_sign_flip_p_below": "0.05",
    "max_relative_drawdown_multiple_of_validation": "2",
}


def _git(repo: Path, *args: str, at: datetime | None = None) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Research",
            "GIT_AUTHOR_EMAIL": "research@example.invalid",
            "GIT_COMMITTER_NAME": "Research",
            "GIT_COMMITTER_EMAIL": "research@example.invalid",
        }
    )
    if at is not None:
        env["GIT_COMMITTER_DATE"] = at.isoformat()
        env["GIT_AUTHOR_DATE"] = at.isoformat()
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


SOURCE: Final[str] = "src/openalpha_cn/strategy.py"


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src" / "openalpha_cn").mkdir(parents=True)
    _git(repo, "init", "-q", "--template=")
    (repo / SOURCE).write_text("RULES = 1\n", encoding="utf-8")
    _git(repo, "add", SOURCE)
    _git(repo, "commit", "-q", "-m", "initial", at=COMMITTED - timedelta(days=1))
    return repo


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _registered(repo: Path, *, commit_at: datetime | None) -> Path:
    path = repo / "docs" / "research" / "p6-registration.json"
    registry.register(CONFIG, CRITERIA, path, code_commit=_head(repo))
    if commit_at is not None:
        _git(repo, "add", path.relative_to(repo).as_posix())
        _git(repo, "commit", "-q", "-m", "register the holdout", at=commit_at)
    return path


class _Measure:
    """A measure with settings, as `run_holdout` requires; records what it was asked."""

    def __init__(self, settings: Mapping[str, object] | None = None, *, fail: bool = False):
        self.settings = dict(grid.protocol_settings() if settings is None else settings)
        self.seen: list[Mapping[str, object]] = []
        self.fail = fail
        self.ledger: Path | None = None
        self.rows_at_measure: list[dict[str, Any]] = []

    def __call__(self, config: Mapping[str, object]) -> Mapping[str, object]:
        self.seen.append(config)
        if self.ledger is not None:
            self.rows_at_measure = _rows(self.ledger)
        if self.fail:
            raise RuntimeError("the process died mid-measurement")
        return {"p_excess": 0.04}


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _claim(ledger: Path, at: datetime) -> None:
    grid._append_holdout(
        ledger,
        "holdout_claim",
        CONFIG,
        {"registration_sha256": "0" * 64, "registration_commit": CODE_COMMIT},
        recorded_at=at,
    )


def _run(registration: Path, ledger: Path, repo: Path, measure: _Measure, *, hours: int = 1):
    return registry.run_holdout(
        registration,
        ledger,
        repo,
        CONFIG,
        measure,
        clock=lambda: COMMITTED + timedelta(hours=hours),
    )


# --- register ----------------------------------------------------------------------------------


def test_register_writes_the_file_and_returns_the_sha256_of_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "registration.json"

    digest = registry.register(CONFIG, CRITERIA, path, code_commit=CODE_COMMIT)

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["schema"] == registry.REGISTRATION_SCHEMA
    assert body["config"] == grid.to_json_value(CONFIG)
    assert body["config_id"] == grid.config_id(CONFIG)
    assert body["criteria"] == CRITERIA
    assert body["code_commit"] == CODE_COMMIT
    assert body["settings"] == {
        "bootstrap_samples": 100_000,
        "random_seed": 20_260_926,
        "excess_benchmark": "equal_weight_all_a",
        "sessions_per_year": 244,
    }
    assert registry.register(CONFIG, CRITERIA, path, code_commit=CODE_COMMIT) == digest


def test_register_refuses_to_replace_a_different_registration(tmp_path: Path) -> None:
    path = tmp_path / "registration.json"
    registry.register(CONFIG, CRITERIA, path, code_commit=CODE_COMMIT)

    with pytest.raises(registry.RegistrationError, match="already"):
        registry.register({**CONFIG, "holding_count": 30}, CRITERIA, path, code_commit=CODE_COMMIT)


@pytest.mark.parametrize("commit", ["unknown", f"{CODE_COMMIT}-dirty", ""])
def test_register_refuses_a_code_commit_nobody_can_check_out(tmp_path: Path, commit: str) -> None:
    with pytest.raises(registry.RegistrationError, match="commit"):
        registry.register(CONFIG, CRITERIA, tmp_path / "r.json", code_commit=commit)


# --- the guard's refusals ----------------------------------------------------------------------


def test_holdout_is_refused_without_a_committed_registration(tmp_git_repo: Path) -> None:
    registration = _registered(tmp_git_repo, commit_at=None)

    with pytest.raises(registry.RegistrationNotCommittedError, match="not in"):
        registry.assert_holdout_allowed(registration, tmp_git_repo / "ledger.jsonl", tmp_git_repo)


def test_holdout_is_refused_when_the_registration_on_disk_is_not_the_committed_one(
    tmp_git_repo: Path,
) -> None:
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    registration.write_text(registration.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(registry.RegistrationNotCommittedError, match="bytes"):
        registry.assert_holdout_allowed(registration, tmp_git_repo / "ledger.jsonl", tmp_git_repo)


def test_holdout_is_refused_for_a_registration_that_does_not_exist(tmp_git_repo: Path) -> None:
    with pytest.raises(registry.RegistrationNotCommittedError, match="not a file"):
        registry.assert_holdout_allowed(
            tmp_git_repo / "absent.json", tmp_git_repo / "ledger.jsonl", tmp_git_repo
        )


def test_holdout_is_refused_for_a_registration_outside_the_repository(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.json"
    registry.register(CONFIG, CRITERIA, outside, code_commit=CODE_COMMIT)

    with pytest.raises(registry.RegistrationNotCommittedError, match="outside"):
        registry.assert_holdout_allowed(outside, tmp_git_repo / "ledger.jsonl", tmp_git_repo)


def test_holdout_is_refused_when_the_registration_was_committed_after_a_holdout_row(
    tmp_git_repo: Path,
) -> None:
    """A holdout row that predates the registration's commit means the holdout was looked at
    before the choice was fixed: the registration is a description of a result, not a plan."""
    ledger = tmp_git_repo / "ledger.jsonl"
    _claim(ledger, COMMITTED)
    registration = _registered(tmp_git_repo, commit_at=COMMITTED + timedelta(hours=1))

    with pytest.raises(registry.RegistrationAfterHoldoutError, match="after"):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_a_registration_committed_in_the_same_second_as_a_holdout_row_is_refused(
    tmp_git_repo: Path,
) -> None:
    """Git keeps a commit time to the second; a row recorded within that second cannot be shown to
    come after the commit, so the tie goes against the holdout."""
    ledger = tmp_git_repo / "ledger.jsonl"
    _claim(ledger, COMMITTED + timedelta(microseconds=900_000))
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    with pytest.raises(registry.RegistrationAfterHoldoutError):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_holdout_is_refused_the_second_time(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)
    _run(registration, ledger, tmp_git_repo, _Measure())

    with pytest.raises(registry.HoldoutAlreadyRanError, match="measurement; the holdout runs once"):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)
    with pytest.raises(registry.HoldoutAlreadyRanError):
        _run(registration, ledger, tmp_git_repo, _Measure(), hours=2)
    assert grid.stage_family(ledger, grid.HOLDOUT_STAGE) == 1


def test_a_committed_source_change_after_the_registration_refuses_the_holdout(
    tmp_git_repo: Path,
) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    (tmp_git_repo / SOURCE).write_text("RULES = 2\n", encoding="utf-8")
    _git(tmp_git_repo, "add", SOURCE)
    _git(tmp_git_repo, "commit", "-q", "-m", "tune", at=COMMITTED + timedelta(minutes=5))

    with pytest.raises(registry.SourceChangedError, match="src"):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_an_uncommitted_source_change_refuses_the_holdout(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    (tmp_git_repo / "src" / "openalpha_cn" / "new.py").write_text("X = 1\n", encoding="utf-8")

    with pytest.raises(registry.SourceChangedError, match="src"):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_a_committed_registration_before_any_holdout_row_is_allowed(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    grid.append_ledger(ledger, "validation", CONFIG, {"p_excess": 0.2}, recorded_at=COMMITTED)
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


# --- run_holdout ---------------------------------------------------------------------------------


def test_the_holdout_runs_the_registered_configuration_and_records_the_registration(
    tmp_git_repo: Path,
) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    measure = _Measure()

    _run(registration, ledger, tmp_git_repo, measure)

    assert measure.seen == [CONFIG]
    claim, row = [row for row in _rows(ledger) if row["stage"] == grid.HOLDOUT_STAGE]
    assert (claim["kind"], row["kind"]) == ("holdout_claim", "measurement")
    digest = hashlib.sha256(registration.read_bytes()).hexdigest()
    for written in (claim, row):
        assert written["config"] == grid.to_json_value(CONFIG)
        assert written["result"]["registration_sha256"] == digest
        assert written["result"]["registration_commit"] == _head(tmp_git_repo)
        assert written["result"]["settings"] == grid.protocol_settings()
    assert row["result"]["p_excess"] == 0.04
    assert row["result"]["started_at"] == "2026-09-26T13:00:00+00:00"


def test_the_claim_is_written_before_the_measurement_runs(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    measure = _Measure()
    measure.ledger = ledger

    _run(registration, ledger, tmp_git_repo, measure)

    assert [row["kind"] for row in measure.rows_at_measure] == ["holdout_claim"]


def test_a_holdout_run_that_crashed_is_still_the_one_run(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    with pytest.raises(RuntimeError, match="died"):
        _run(registration, ledger, tmp_git_repo, _Measure(fail=True))

    assert [row["kind"] for row in _rows(ledger)] == ["holdout_claim"]
    with pytest.raises(registry.HoldoutAlreadyRanError, match="claimed"):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_a_refused_holdout_measurement_is_recorded_as_its_error(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    class Refusing(_Measure):
        def __call__(self, config: Mapping[str, object]) -> Mapping[str, object]:
            raise StrategyRequestError("no cross section on the signal day")

    _run(registration, ledger, tmp_git_repo, Refusing())

    row = _rows(ledger)[-1]
    assert row["kind"] == "measurement"
    assert row["result"]["error"] == "StrategyRequestError: no cross section on the signal day"


@pytest.mark.parametrize(
    "setting",
    [
        {"random_seed": 1},
        {"bootstrap_samples": 1_000},
        {"excess_benchmark": "000905.SH"},
        {"sessions_per_year": 252},
    ],
)
def test_measurement_settings_that_are_not_the_registered_ones_refuse_the_holdout(
    tmp_git_repo: Path, setting: dict[str, object]
) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    measure = _Measure({**grid.protocol_settings(), **setting})

    with pytest.raises(registry.HoldoutSettingsError, match="settings"):
        _run(registration, ledger, tmp_git_repo, measure)
    assert measure.seen == []
    assert _rows(ledger) == []


def test_the_holdout_refuses_a_configuration_that_is_not_the_registered_one(
    tmp_git_repo: Path,
) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    with pytest.raises(registry.HoldoutConfigurationError, match="registered"):
        registry.run_holdout(
            registration,
            ledger,
            tmp_git_repo,
            {**CONFIG, "holding_count": 30},
            _Measure(),
            clock=lambda: COMMITTED + timedelta(hours=1),
        )
    assert _rows(ledger) == []


def test_the_holdout_refuses_a_registered_configuration_outside_the_holdout_window(
    tmp_git_repo: Path,
) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    early = {**CONFIG, "start": date(2023, 12, 1)}
    path = tmp_git_repo / "docs" / "research" / "p6-registration.json"
    registry.register(early, CRITERIA, path, code_commit=_head(tmp_git_repo))
    _git(tmp_git_repo, "add", path.relative_to(tmp_git_repo).as_posix())
    _git(tmp_git_repo, "commit", "-q", "-m", "register", at=COMMITTED)

    with pytest.raises(registry.HoldoutConfigurationError, match="window"):
        registry.run_holdout(path, ledger, tmp_git_repo, early, _Measure(), clock=lambda: COMMITTED)
    assert _rows(ledger) == []


def test_the_forward_stage_starts_after_the_registration_commit_date_in_shanghai(
    tmp_git_repo: Path,
) -> None:
    """17:00 UTC on 26 September is 01:00 on the 27th in Shanghai, the calendar the sessions use."""
    registration = _registered(tmp_git_repo, commit_at=datetime(2026, 9, 26, 17, 0, tzinfo=UTC))

    assert registry.forward_after(registration, tmp_git_repo) == date(2026, 9, 27)


def test_the_guard_reads_git_without_an_inherited_git_variable(
    tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook's `GIT_DIR` pointing elsewhere must not make the guard read another repository."""
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git(elsewhere, "init", "-q", "--template=")
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))

    registry.assert_holdout_allowed(registration, tmp_git_repo / "ledger.jsonl", tmp_git_repo)
