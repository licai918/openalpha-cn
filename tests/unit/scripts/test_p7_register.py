"""`scripts/p7_register.py`: the `V2-P7-006b` registration's body and writer.

The registration is written once, by hand, after the smoke's phase 1; the body is frozen here as
literals so that a change to a default in `src/` cannot move the registered numbers without this
file going red. The writer's refusals are run against throwaway repositories built the way
`test_p7_smoke_env.py` builds its source checkout: every git call drops the inherited `GIT_*`
variables and pins the commit time.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest
import research_repo
import test_daily_selection as tds

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


register = _script("p7_register")
env = _script("p7_smoke_env")
grid = _script("grid")
registry = _script("registry")

from openalpha_cn import (  # noqa: E402
    strategy_survivor,
    strategy_survivor_continuation,
    strategy_survivor_forward,
)

EPOCH: Final[date] = date(2026, 12, 18)
REGISTRATION: Final[str] = "docs/research/p7-registration.json"
READING: Final[Path] = ROOT / "docs/research/p7-readings/v2-p7-024-validation.registration.json"

BOOK: Final[dict[str, Any]] = {
    "schema_version": "survivor-book-config/v1",
    "holding_count": 10,
    "capital": "350000",
    "boards": ["main"],
    "liquidity_drop_fraction": "0.30",
    "min_listing_sessions": 250,
    "loser_decile_count": 2,
    "entry_floor_fraction": "0.25",
    "near_exit_fraction": "0.10",
    "cooldown_sessions": 20,
    "max_replacements_per_session": 2,
    "buy_order_expiry_sessions": 3,
    "lot_size": 100,
    "cost_reserve_fraction": "0.005",
    "selection_rule": "seeded_hash/v1",
    "seed": 20_260_926,
    "composite_factors": [
        "residual_vol_60/v1",
        "return_vol_60/v1",
        "turnover_60/v1",
        "downside_vol_60/v1",
    ],
}
"""`SurvivorBookConfig(seed=20260926)` in its stored form (plan section 1.1), written out by hand:
a changed default in `src/` must fail here rather than move the registered book."""

ADAPTER: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
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
    "epoch_session": "2026-12-18",
}
"""The design's adapter block with the planned epoch (plan section 1.1)."""

PROTOCOL: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-protocol/v1",
    "anchor_ordinal": 1,
    "primary_horizon": 20,
    "secondary_horizons": [5],
    "primary_metric": "rank_ic",
    "secondary_metrics": ["survivors_minus_ew", "ew_minus_worst_decile"],
    "window_periods": 36,
    "bootstrap_samples": 100_000,
    "random_seed": 20_260_926,
    "q": 0.1,
}
"""Part E's recommended protocol (plan section 1.2), written out by hand."""

POWER: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-power/v1",
    "test_level": 0.05,
    "target_power": 0.80,
    "critical_value": "t",
    "assumed_lag1_autocorrelation": 0.0,
    "minimum_periods": 30,
    "planned": [
        {
            "horizon": 20,
            "metric": "rank_ic",
            "planning_sd": 0.18,
            "planning_means": [0.037, 0.05, 0.10],
            "sd_source": (
                "研究期约 0.14 与验证读观测 0.182 取较大者向下取两位；单次观测不是规划值"  # noqa: RUF001
            ),
        }
    ],
}
"""The power plan with the user's D-1/D-2 values (plan section 1.2), written out by hand."""

STOP_RULE: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-stop-rule/v1",
    "metric": "rank_ic",
    "horizon": 20,
    "mean_at_or_below": 0.02,
    "tail_metric": "ew_minus_worst_decile",
    "tail_mean_at_or_below": 0.0,
    "interim_periods": 18,
    "interim_mean_at_or_below": 0.0,
}
"""`adopted_forward_stop_rule()` (plan section 1.2), written out by hand."""

CRITERIA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "primary",
        "stop_rule",
        "interim",
        "secondary_family",
        "evidence",
        "run_in",
        "eligibility",
        "wording",
    }
)
"""Plan section 1.3's eight criteria keys."""

FORBIDDEN_WORDING: Final[tuple[str, ...]] = ("proven", "验证有效", "已证实", "已验证", "证实有效")


# --- Task 1: the body ---------------------------------------------------------------------------


def test_the_registration_body_is_the_frozen_one() -> None:
    config, criteria, settings = register.registration_body(EPOCH)

    assert config == env.smoke_configuration(EPOCH)
    assert config["kind"] == "survivor_book/v1"
    assert config["survivor_book"] == BOOK
    assert config["adapter"] == ADAPTER
    assert settings == {
        **grid.protocol_settings(),
        "survivor_forward_protocol": PROTOCOL,
        "survivor_forward_power": POWER,
        "survivor_forward_stop_rule": STOP_RULE,
    }
    assert settings["survivor_forward_protocol"] == PROTOCOL
    assert settings["survivor_forward_power"] == POWER
    assert settings["survivor_forward_stop_rule"] == (
        strategy_survivor_forward.adopted_forward_stop_rule()
    )
    assert settings["survivor_forward_stop_rule"] == STOP_RULE
    assert (
        settings["random_seed"]
        == settings["survivor_forward_protocol"]["random_seed"]
        == config["survivor_book"]["seed"]
        == 20_260_926
    )
    assert "smoke_run" not in settings
    assert "survivor_continuation" not in settings
    assert set(criteria) == CRITERIA_KEYS


def test_the_body_reads_back() -> None:
    config, _, settings = register.registration_body(EPOCH)
    seed = settings["random_seed"]
    assert isinstance(seed, int)

    strategy_survivor.read_survivor_registration(config, seed=seed)
    protocol = strategy_survivor_forward.read_forward_protocol(settings)
    assert strategy_survivor_forward.read_forward_power(settings, protocol=protocol) is not None
    assert strategy_survivor_forward.read_forward_stop_rule(settings, protocol=protocol) is not None
    assert strategy_survivor_continuation.read_survivor_continuation(settings) is None


def test_the_scoring_definition_is_the_one_read_once() -> None:
    reading = json.loads(READING.read_text(encoding="utf-8"))
    config, _, _ = register.registration_body(EPOCH)
    adapter = config["adapter"]
    book = config["survivor_book"]

    assert adapter["exchange"] == reading["config"]["exchange"] == "SSE"
    assert adapter["transform"] == reading["config"]["transform"] == "cross_section_standard/v1"
    assert adapter["min_coverage"] == reading["config"]["min_coverage"] == 0.5
    assert adapter["max_carry_sessions"] == reading["config"]["max_carry_sessions"] == 250
    assert book["loser_decile_count"] == reading["request"]["loser_decile_count"] == 2


def test_the_criteria_name_the_run_in_and_the_unknown_rule() -> None:
    _, criteria, _ = register.registration_body(EPOCH)

    assert set(criteria) == CRITERIA_KEYS
    assert "22" in criteria["run_in"]
    assert "unknown" in criteria["eligibility"]
    for value in criteria.values():
        for forbidden in FORBIDDEN_WORDING:
            assert forbidden not in value
    assert "候选" in criteria["wording"]


# --- Task 2: the writer's command line and its refusals -----------------------------------------

TODAY: Final[date] = date(2026, 11, 12)
"""A Thursday. The write runs in a throwaway repository, and its "today" is pinned so the epoch
bounds do not depend on the wall clock."""
VALID_EPOCH: Final[date] = date(2026, 11, 11)
BOUND_FILES: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
    "scripts/daily_selection.py",
    "scripts/forward_report.py",
)
"""One file under each bound path (`registry.REGISTERED_PATHS` and the two daily scripts)."""


def _repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    research_repo.git(repo, "init", "-q", "--template=", "-b", "main")
    for name in BOUND_FILES:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(tds.LOCK if name == "uv.lock" else "RULES = 1\n", encoding="utf-8")
    (repo / ".python-version").write_text(f"{tds.RUNNING_PYTHON}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n.env\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    research_repo.git(repo, "add", "-A")
    research_repo.git(repo, "commit", "-q", "-m", "initial", at=tds.COMMITTED)
    return repo


def _commit_a_bound_change(repo: Path) -> str:
    """A second commit that changes a bound file; the first commit is its "smoke" commit."""
    smoke = research_repo.head(repo)
    path = repo / "scripts/research/grid.py"
    path.write_text("RULES = 2\n", encoding="utf-8")
    research_repo.git(repo, "add", path.relative_to(repo).as_posix())
    research_repo.git(repo, "commit", "-q", "-m", "change", at=tds.COMMITTED)
    return smoke


def _pin_the_clock(monkeypatch: pytest.MonkeyPatch, today: date = TODAY) -> None:
    monkeypatch.setattr(register, "_today", lambda: today)


def _run(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, str, str]:
    code = register.main(list(arguments))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _arguments(
    *,
    epoch: date = VALID_EPOCH,
    smoke: str,
    path: str = REGISTRATION,
    dry_run: bool = False,
    supersedes: str | None = None,
) -> list[str]:
    arguments = ["--epoch", epoch.isoformat(), "--smoke-commit", smoke, "--path", path]
    if dry_run:
        arguments.append("--dry-run")
    if supersedes is not None:
        arguments.extend(["--supersedes", supersedes])
    return arguments


def _status(repo: Path) -> str:
    return research_repo.git(repo, "status", "--porcelain", "--untracked-files=all")


def _files(repo: Path) -> list[str]:
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


def test_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    config, _, _ = register.registration_body(VALID_EPOCH)
    config_id = grid.config_id(config)
    before_status, before_files = _status(repo), _files(repo)

    code, out, err = _run(capsys, *_arguments(smoke=research_repo.head(repo), dry_run=True))

    assert code == 0, err
    assert config_id in out
    assert any(
        len(token) == 64 and all(character in "0123456789abcdef" for character in token)
        for token in out.split()
    )
    assert "未写入" in out
    assert _status(repo) == before_status
    assert _files(repo) == before_files
    assert not (repo / REGISTRATION).exists()


def test_refuses_a_dirty_bound_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    (repo / "src/openalpha_cn/strategy.py").write_text("RULES = 2\n", encoding="utf-8")

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo)))

    assert code == 2
    assert "uncommitted" in err
    assert not (repo / REGISTRATION).exists()


def test_refuses_a_head_not_on_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    research_repo.git(repo, "checkout", "-q", "-b", "topic")
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
    research_repo.git(repo, "add", "notes.txt")
    research_repo.git(repo, "commit", "-q", "-m", "topic", at=tds.COMMITTED)

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo)))

    assert code == 2
    assert "main" in err
    assert not (repo / REGISTRATION).exists()


def test_refuses_a_smoke_commit_whose_bound_code_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    smoke = _commit_a_bound_change(repo)

    code, _, err = _run(capsys, *_arguments(smoke=smoke))

    assert code == 2
    assert "bound code" in err or "differs" in err
    assert not (repo / REGISTRATION).exists()


def test_refuses_a_path_outside_docs_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    smoke = research_repo.head(repo)

    outside = _run(capsys, *_arguments(smoke=smoke, path="docs/p7-registration.json"))
    suffix = _run(capsys, *_arguments(smoke=smoke, path="docs/research/p7-registration.txt"))

    assert outside[0] == 2
    assert "docs/research" in outside[2] and ".json" in outside[2]
    assert suffix[0] == 2
    assert "docs/research" in suffix[2] and ".json" in suffix[2]
    assert not (repo / "docs/p7-registration.json").exists()
    assert not (repo / "docs/research/p7-registration.txt").exists()


@pytest.mark.parametrize(
    ("today", "epoch", "reason"),
    [
        (TODAY, date(2026, 11, 13), "future"),
        (TODAY, date(2026, 11, 7), "weekday"),
        (TODAY, date(2026, 10, 1), "16"),
    ],
)
def test_refuses_an_epoch_in_the_future_on_a_weekend_or_too_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    today: date,
    epoch: date,
    reason: str,
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch, today)
    monkeypatch.chdir(repo)

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo), epoch=epoch))

    assert code == 2
    assert reason in err
    assert not (repo / REGISTRATION).exists()


def test_refuses_an_existing_path_with_other_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    (repo / REGISTRATION).parent.mkdir(parents=True)
    (repo / REGISTRATION).write_text("{}\n", encoding="utf-8")

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo)))

    assert code == 2
    assert "different registration" in err
    assert (repo / REGISTRATION).read_text(encoding="utf-8") == "{}\n"


def test_writes_once_and_never_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    smoke = research_repo.head(repo)
    commits = research_repo.git(repo, "rev-list", "--count", "HEAD").strip()
    before = _status(repo)

    dry = _run(capsys, *_arguments(smoke=smoke, dry_run=True))
    assert dry[0] == 0
    dry_digest = dry[1].split("sha256 ")[1].split()[0]

    code, out, err = _run(capsys, *_arguments(smoke=smoke))
    assert code == 0, err
    content = (repo / REGISTRATION).read_bytes()
    assert hashlib.sha256(content).hexdigest() == dry_digest
    assert dry_digest in out

    after = _status(repo)
    assert set(after.splitlines()) - set(before.splitlines()) == {f"?? {REGISTRATION}"}
    assert research_repo.git(repo, "rev-list", "--count", "HEAD").strip() == commits

    again = _run(capsys, *_arguments(smoke=smoke))
    assert again[0] == 0
    assert dry_digest in again[1]
    assert (repo / REGISTRATION).read_bytes() == content


def test_code_commit_is_head_passed_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    captures: list[str | None] = []
    original = registry.register

    def recording(config: Any, criteria: Any, path: Path, **keywords: Any) -> str:
        captures.append(keywords.get("code_commit"))
        return original(config, criteria, path, **keywords)

    monkeypatch.setattr(registry, "register", recording)
    head = research_repo.head(repo)

    code, _, err = _run(capsys, *_arguments(smoke=head))

    assert code == 0, err
    assert captures == [head]
    written = json.loads((repo / REGISTRATION).read_text(encoding="utf-8"))
    assert written["code_commit"] == head


# --- Task 3: U1 -- a successor names the registration it supersedes ------------------------------

FIRST: Final[str] = "docs/research/p7-first-registration.json"
"""The first committed survivor registration in the throwaway repository, when a test needs one."""


def _survivor_body(**settings: Any) -> dict[str, Any]:
    return {
        "config": {"kind": "survivor_book/v1", "survivor_book": {}, "adapter": {}},
        "settings": dict(settings),
    }


def _continuation(path: str) -> dict[str, str]:
    return strategy_survivor_continuation.continuation_block(
        path=path, sha256="0" * 64, config_id="config-id"
    )


def test_an_empty_registration_set_has_no_chain_problems() -> None:
    assert register.chain_problems({}) == []


def test_one_chain_root_has_no_chain_problems() -> None:
    assert register.chain_problems({"docs/research/a.json": _survivor_body()}) == []
    assert (
        register.chain_problems(
            {
                "docs/research/a.json": _survivor_body(),
                "docs/research/b.json": _survivor_body(
                    survivor_continuation=_continuation("docs/research/a.json")
                ),
            }
        )
        == []
    )


def test_two_chain_roots_are_refused_naming_both() -> None:
    problems = register.chain_problems(
        {
            "docs/research/a.json": _survivor_body(),
            "docs/research/b.json": _survivor_body(),
        }
    )

    assert problems
    assert any("a.json" in problem and "b.json" in problem for problem in problems)


def test_a_successor_naming_a_path_outside_the_set_is_refused() -> None:
    problems = register.chain_problems(
        {
            "docs/research/a.json": _survivor_body(),
            "docs/research/b.json": _survivor_body(
                survivor_continuation=_continuation("docs/research/missing.json")
            ),
        }
    )

    assert problems
    assert any("b.json" in problem and "missing.json" in problem for problem in problems)


def test_two_successors_naming_one_predecessor_are_refused() -> None:
    problems = register.chain_problems(
        {
            "docs/research/a.json": _survivor_body(),
            "docs/research/b.json": _survivor_body(
                survivor_continuation=_continuation("docs/research/a.json")
            ),
            "docs/research/c.json": _survivor_body(
                survivor_continuation=_continuation("docs/research/a.json")
            ),
        }
    )

    assert problems
    assert any(
        "a.json" in problem and "b.json" in problem and "c.json" in problem for problem in problems
    )


def _commit_a_survivor_registration(repo: Path, path: str = FIRST) -> str:
    """A committed, non-smoke survivor registration at `path`, built by the writer's own body."""
    config, criteria, settings = register.registration_body(VALID_EPOCH)
    target = repo / path
    registry.register(
        config, criteria, target, code_commit=research_repo.head(repo), settings=settings
    )
    research_repo.commit_file(repo, target, "the first registration", at=tds.COMMITTED)
    return research_repo.head(repo)


def test_the_writer_refuses_a_chain_root_without_a_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo)))

    assert code == 2
    assert FIRST in err
    assert "--supersedes" in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_names_the_predecessor_it_supersedes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    predecessor_bytes = (repo / FIRST).read_bytes()
    predecessor = json.loads(predecessor_bytes)

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo), supersedes=FIRST))

    assert code == 0, err
    written = json.loads((repo / REGISTRATION).read_text(encoding="utf-8"))
    assert written["settings"]["survivor_continuation"] == {
        "schema_version": "survivor-continuation/v1",
        "supersedes_path": FIRST,
        "supersedes_sha256": hashlib.sha256(predecessor_bytes).hexdigest(),
        "supersedes_config_id": predecessor["config_id"],
    }
    assert register.chain_problems(register.committed_survivor_registrations(repo)) == []
    assert _status(repo).strip() == f"?? {REGISTRATION}"


def test_the_writer_never_invokes_a_staging_or_committing_step() -> None:
    source = (ROOT / "scripts/p7_register.py").read_text(encoding="utf-8")

    assert "git add" not in source
    assert "git commit" not in source


def test_refuses_an_uncommitted_or_edited_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    smoke = research_repo.head(repo)

    missing = _run(
        capsys,
        *_arguments(smoke=smoke, supersedes="docs/research/not-a-registration.json"),
    )
    assert missing[0] == 2
    assert "not-a-registration.json" in missing[2]

    (repo / FIRST).write_text("{}\n", encoding="utf-8")
    edited = _run(capsys, *_arguments(smoke=smoke, supersedes=FIRST))
    assert edited[0] == 2
    assert FIRST in edited[2]
    assert "HEAD holds" in edited[2]
    assert not (repo / REGISTRATION).exists()
