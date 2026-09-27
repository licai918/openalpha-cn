"""`scripts/research/registry.py`: the pre-registration and the one-shot holdout guard.

The holdout may run exactly once, after a registration committed to git. `assert_holdout_allowed`
refuses three ways and each has its own exception class, so a test here fails when the check it
names is removed even though another check might still refuse the same situation:

* the registration is not in git's committed history (or the bytes on disk are not the committed
  ones) -- `RegistrationNotCommittedError`;
* the registration's commit is later than a holdout row the ledger already holds --
  `RegistrationAfterHoldoutError`;
* the ledger already holds a holdout row -- `HoldoutAlreadyRanError`.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

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
CONFIG: Final[dict[str, object]] = {"holding_count": 50, "rebalance_every_sessions": 20}
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


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--template=")
    (repo / "README.md").write_text("research\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial", at=COMMITTED - timedelta(days=1))
    return repo


def _registered(repo: Path, *, commit_at: datetime | None) -> Path:
    path = repo / "docs" / "research" / "p6-registration.json"
    registry.register(CONFIG, CRITERIA, path, code_commit=CODE_COMMIT)
    if commit_at is not None:
        _git(repo, "add", path.relative_to(repo).as_posix())
        _git(repo, "commit", "-q", "-m", "register the holdout", at=commit_at)
    return path


def _measure(config: Mapping[str, object]) -> Mapping[str, object]:
    return {"p_excess": 0.04, "holding_count": config["holding_count"]}


# --- register ----------------------------------------------------------------------------------


def test_register_writes_the_file_and_returns_the_sha256_of_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "registration.json"

    digest = registry.register(CONFIG, CRITERIA, path, code_commit=CODE_COMMIT)

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["schema"] == registry.REGISTRATION_SCHEMA
    assert body["config"] == CONFIG
    assert body["config_id"] == grid.config_id(CONFIG)
    assert body["criteria"] == CRITERIA
    assert body["code_commit"] == CODE_COMMIT
    assert body["random_seed"] == grid.PROTOCOL_RANDOM_SEED
    assert body["bootstrap_samples"] == grid.PROTOCOL_BOOTSTRAP_SAMPLES
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


# --- the three refusals ------------------------------------------------------------------------


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
    grid.append_ledger(
        ledger, grid.HOLDOUT_STAGE, CONFIG, {"p_excess": 0.01}, recorded_at=COMMITTED
    )
    registration = _registered(tmp_git_repo, commit_at=COMMITTED + timedelta(hours=1))

    with pytest.raises(registry.RegistrationAfterHoldoutError, match="after"):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_a_registration_committed_in_the_same_second_as_a_holdout_row_is_refused(
    tmp_git_repo: Path,
) -> None:
    """Git keeps a commit time to the second; a row recorded within that second cannot be shown to
    come after the commit, so the tie goes against the holdout."""
    ledger = tmp_git_repo / "ledger.jsonl"
    grid.append_ledger(
        ledger,
        grid.HOLDOUT_STAGE,
        CONFIG,
        {"p_excess": 0.01},
        recorded_at=COMMITTED + timedelta(microseconds=900_000),
    )
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    with pytest.raises(registry.RegistrationAfterHoldoutError):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_holdout_is_refused_the_second_time(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)
    registry.run_holdout(
        registration,
        ledger,
        tmp_git_repo,
        CONFIG,
        _measure,
        clock=lambda: COMMITTED + timedelta(hours=1),
    )

    with pytest.raises(registry.HoldoutAlreadyRanError, match="once"):
        registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)
    with pytest.raises(registry.HoldoutAlreadyRanError):
        registry.run_holdout(
            registration,
            ledger,
            tmp_git_repo,
            CONFIG,
            _measure,
            clock=lambda: COMMITTED + timedelta(hours=2),
        )
    assert grid.stage_family(ledger, grid.HOLDOUT_STAGE) == 1


def test_a_committed_registration_before_any_holdout_row_is_allowed(tmp_git_repo: Path) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    grid.append_ledger(ledger, "validation", CONFIG, {"p_excess": 0.2}, recorded_at=COMMITTED)
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)

    registry.assert_holdout_allowed(registration, ledger, tmp_git_repo)


def test_the_holdout_runs_the_registered_configuration_and_records_the_registration(
    tmp_git_repo: Path,
) -> None:
    ledger = tmp_git_repo / "ledger.jsonl"
    registration = _registered(tmp_git_repo, commit_at=COMMITTED)
    seen: list[Mapping[str, object]] = []

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        seen.append(config)
        return {"p_excess": 0.04}

    registry.run_holdout(
        registration,
        ledger,
        tmp_git_repo,
        CONFIG,
        measure,
        clock=lambda: COMMITTED + timedelta(hours=1),
    )

    assert seen == [CONFIG]
    (row,) = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["stage"] == grid.HOLDOUT_STAGE
    ]
    assert row["config"] == CONFIG
    assert row["result"]["p_excess"] == 0.04
    assert (
        row["result"]["registration_sha256"]
        == hashlib.sha256(registration.read_bytes()).hexdigest()
    )
    assert row["result"]["registration_commit"] == _git(tmp_git_repo, "rev-parse", "HEAD").strip()
    assert row["result"]["started_at"] == "2026-09-26T13:00:00+00:00"


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
            _measure,
            clock=lambda: COMMITTED + timedelta(hours=1),
        )
    assert grid.stage_family(ledger, grid.HOLDOUT_STAGE) == 0


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
