"""`scripts/p7_register.py`: the `V2-P7-006b` registration's body and writer.

The registration is written once, by hand, after the smoke's phase 1; the body is frozen here as
literals so that a change to a default in `src/` cannot move the registered numbers without this
file going red. The writer's refusals are run against throwaway repositories built the way
`test_p7_smoke_env.py` builds its source checkout: every git call drops the inherited `GIT_*`
variables and pins the commit time.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from dataclasses import replace
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
from openalpha_cn.domain.survivor_book import SurvivorBookConfig  # noqa: E402

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
        "bootstrap_samples": 100_000,
        "random_seed": 20_260_926,
        "excess_benchmark": "equal_weight_all_a_held",
        "sessions_per_year": 244,
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
    assert "18" in criteria["interim"]
    assert "停止" in criteria["interim"]
    assert "描述性均值" in criteria["interim"]
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
    entry_veto: str | None = None,
    parallel_of: str | None = None,
) -> list[str]:
    arguments = ["--epoch", epoch.isoformat(), "--smoke-commit", smoke, "--path", path]
    if dry_run:
        arguments.append("--dry-run")
    if supersedes is not None:
        arguments.extend(["--supersedes", supersedes])
    if entry_veto is not None:
        arguments.extend(["--entry-veto", entry_veto])
    if parallel_of is not None:
        arguments.extend(["--parallel-of", parallel_of])
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


def test_no_git_call_asks_for_staging_or_a_commit() -> None:
    tree = ast.parse((ROOT / "scripts/p7_register.py").read_text(encoding="utf-8"))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
        if name not in {"_git", "_git_process", "run"}:
            continue
        for argument in node.args:
            values: list[ast.expr]
            if isinstance(argument, ast.List | ast.Tuple):
                values = list(argument.elts)
            else:
                values = [argument]
            for value in values:
                if isinstance(value, ast.Constant) and value.value in {"add", "commit"}:
                    offenders.append(str(value.value))
    assert offenders == []


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

    uncommitted = "docs/research/p7-uncommitted-registration.json"
    (repo / uncommitted).write_text("{}\n", encoding="utf-8")
    never_committed = _run(capsys, *_arguments(smoke=smoke, supersedes=uncommitted))
    assert never_committed[0] == 2
    assert uncommitted in never_committed[2]
    assert "committed history" in never_committed[2]

    (repo / FIRST).write_text("{}\n", encoding="utf-8")
    edited = _run(capsys, *_arguments(smoke=smoke, supersedes=FIRST))
    assert edited[0] == 2
    assert FIRST in edited[2]
    assert "HEAD holds" in edited[2]
    assert not (repo / REGISTRATION).exists()


def test_a_digest_mismatch_removes_the_file_it_just_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(register, "_document", lambda *arguments, **keywords: b"{}\n")

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo)))

    assert code == 2
    assert "digest" in err
    assert not (repo / REGISTRATION).exists()
    assert _status(repo) == ""


def test_another_registration_error_is_its_own_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)

    def refuse(*arguments: Any, **keywords: Any) -> str:
        raise registry.RegistrationError("the registry said no")

    monkeypatch.setattr(registry, "register", refuse)

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo)))

    assert code == 2
    assert "the registry said no" in err
    assert "the registry refused the registration" in err
    assert "already holds" not in err
    assert not (repo / REGISTRATION).exists()


@pytest.mark.parametrize(
    ("epoch", "accepted"),
    [
        (date(2026, 10, 27), True),
        (date(2026, 10, 26), False),
    ],
)
def test_an_epoch_sixteen_days_old_is_accepted_and_seventeen_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    epoch: date,
    accepted: bool,
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)  # today = 2026-11-12, a Thursday; both epochs are weekdays
    monkeypatch.chdir(repo)

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo), epoch=epoch))

    if accepted:
        assert code == 0, err
        assert (repo / REGISTRATION).is_file()
    else:
        assert code == 2
        assert "16" in err
        assert not (repo / REGISTRATION).exists()


def _commit_a_smoke_registration(repo: Path) -> str:
    """A committed registration that carries `settings.smoke_run`: never part of the chain (U6)."""
    path = "docs/research/p7-smoke-registration.json"
    config, criteria, settings = register.registration_body(VALID_EPOCH)
    settings["smoke_run"] = {"issue": "V2-P7-019", "note": "not evidence"}
    registry.register(
        config, criteria, repo / path, code_commit=research_repo.head(repo), settings=settings
    )
    research_repo.commit_file(repo, repo / path, "a smoke registration", at=tds.COMMITTED)
    return path


def test_superseding_a_smoke_registration_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    smoke = _commit_a_smoke_registration(repo)

    code, _, err = _run(capsys, *_arguments(smoke=research_repo.head(repo), supersedes=smoke))

    assert code == 2
    assert smoke in err
    assert "committed survivor registration" in err
    assert not (repo / REGISTRATION).exists()


# --- Task 5: A's parallel registration, outside the 006b chain -----------------------------------

VETO: Final[str] = "market_bearish_5/v1"
"""Plan A's rule, the only entry veto a parallel registration may carry (`V2-P7-027`)."""
PARALLEL_KEY: Final[str] = "survivor_parallel_of"
"""The settings key the plan names for the base registration a parallel book runs beside (R-N5)."""


def test_the_parallel_key_is_the_plans_spelling() -> None:
    """One spelling: the daily command's (its launchd printer reads it), which the writer takes
    rather than spelling again, and the plan's literal."""
    assert tds.daily.SURVIVOR_PARALLEL_OF_KEY == PARALLEL_KEY
    assert register._PARALLEL_KEY == tds.daily.SURVIVOR_PARALLEL_OF_KEY


def test_the_parallel_body_is_a_v2_book_beside_its_base() -> None:
    """`registration_body(epoch, book=..., parallel_of=...)`: a v2 configuration carrying plan A's
    veto and the block naming its base, round-tripped by the reader the daily command uses."""
    base_config, base_criteria, base_settings = register.registration_body(VALID_EPOCH)
    book = SurvivorBookConfig(
        **{**base_config["survivor_book"], "schema_version": "survivor-book-config/v2"},
        entry_veto=VETO,
    )
    block = {"path": FIRST, "sha256": "ab" * 32}

    config, criteria, settings = register.registration_body(
        VALID_EPOCH, book=book, parallel_of=block
    )

    assert config["survivor_book"]["schema_version"] == "survivor-book-config/v2"
    assert config["survivor_book"]["entry_veto"] == VETO
    assert config["survivor_book"]["seed"] == base_config["survivor_book"]["seed"]
    assert criteria == base_criteria
    assert settings[PARALLEL_KEY] == block
    assert "survivor_continuation" not in settings
    assert {key: value for key, value in settings.items() if key != PARALLEL_KEY} == base_settings
    survivor = strategy_survivor.read_survivor_registration(config, seed=settings["random_seed"])
    assert survivor.book.entry_veto == VETO
    assert grid.config_id(config) != grid.config_id(base_config)


def _commit_a_parallel_registration(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    """The base's parallel registration, written by the writer and committed (a second run needs
    a committed one to be refused)."""
    code, _, err = _run(
        capsys,
        *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=FIRST),
    )
    assert code == 0, err
    research_repo.commit_file(
        repo, repo / REGISTRATION, "the parallel registration", at=tds.COMMITTED
    )
    written: dict[str, Any] = json.loads((repo / REGISTRATION).read_text(encoding="utf-8"))
    return written


def test_the_writer_writes_a_parallel_registration_outside_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The base is committed and the parallel body is a v2 book with the veto, naming that base
    by path and by the sha256 its committed bytes hold; the base's configuration is untouched and
    the parallel `config_id` is not the base's (two books, two identities)."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    base_bytes = (repo / FIRST).read_bytes()
    base = json.loads(base_bytes)

    code, _out, err = _run(
        capsys, *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=FIRST)
    )

    assert code == 0, err
    written = json.loads((repo / REGISTRATION).read_text(encoding="utf-8"))
    assert written["config"]["survivor_book"]["schema_version"] == "survivor-book-config/v2"
    assert written["config"]["survivor_book"]["entry_veto"] == VETO
    assert written["settings"][PARALLEL_KEY] == {
        "path": FIRST,
        "sha256": hashlib.sha256(base_bytes).hexdigest(),
    }
    assert "survivor_continuation" not in written["settings"]
    assert written["config_id"] != base["config_id"]
    surviving = strategy_survivor.read_survivor_registration(
        written["config"], seed=written["settings"]["random_seed"]
    )
    assert surviving.book.entry_veto == VETO
    assert (repo / FIRST).read_bytes() == base_bytes
    assert _status(repo).strip() == f"?? {REGISTRATION}"


def test_the_writer_refuses_one_half_of_the_parallel_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--entry-veto` and `--parallel-of` are one registration's pair: a rule with no base, or a
    base with no rule, is refused by name and nothing is written."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    smoke = research_repo.head(repo)

    veto_only = _run(capsys, *_arguments(smoke=smoke, entry_veto=VETO))
    base_only = _run(capsys, *_arguments(smoke=smoke, parallel_of=FIRST))

    assert veto_only[0] == 2
    assert "--entry-veto" in veto_only[2] and "--parallel-of" in veto_only[2]
    assert base_only[0] == 2
    assert "--entry-veto" in base_only[2] and "--parallel-of" in base_only[2]
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_parallel_registration_that_names_a_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-N5: a parallel registration is never a continuation. Naming a base to run beside and a
    predecessor to continue is refused by name, and nothing is written."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)

    code, _, err = _run(
        capsys,
        *_arguments(
            smoke=research_repo.head(repo),
            supersedes=FIRST,
            entry_veto=VETO,
            parallel_of=FIRST,
        ),
    )

    assert code == 2
    assert "--parallel-of" in err and "--supersedes" in err
    assert "continuation" in err
    assert "R-N5" in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_base_registration_that_is_not_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base registration is a committed file: an untracked one has no sha256 a registration
    could record, so the run is refused naming the path and the committed history."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    uncommitted = "docs/research/p7-uncommitted-registration.json"
    (repo / uncommitted).parent.mkdir(parents=True, exist_ok=True)
    (repo / uncommitted).write_text("{}\n", encoding="utf-8")

    code, _, err = _run(
        capsys,
        *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=uncommitted),
    )

    assert code == 2
    assert uncommitted in err
    assert "committed history" in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_base_registration_edited_after_its_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An edit after the commit is bytes HEAD does not hold: the recorded sha256 would name a file
    that no longer exists in that form, so it is refused rather than registered."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    (repo / FIRST).write_text("{}\n", encoding="utf-8")

    code, _, err = _run(
        capsys, *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=FIRST)
    )

    assert code == 2
    assert FIRST in err
    assert "HEAD holds" in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_parallel_block_whose_sha256_the_base_does_not_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The block's sha256 is read back against the committed base before anything is written: a
    block naming another digest is refused by name, not registered with a dead pointer."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    real = register._parallel_base

    def corrupted(*arguments: Any, **keywords: Any) -> Any:
        return replace(real(*arguments, **keywords), sha256="0" * 64)

    monkeypatch.setattr(register, "_parallel_base", corrupted)

    code, _, err = _run(
        capsys, *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=FIRST)
    )

    assert code == 2
    assert "sha256" in err
    assert FIRST in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_entry_veto_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The base book is the no-veto book (R-N4); a parallel book registered with `none` would be
    the base configuration over again, so it is refused by name."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)

    code, _, err = _run(
        capsys,
        *_arguments(smoke=research_repo.head(repo), entry_veto="none", parallel_of=FIRST),
    )

    assert code == 2
    assert "none" in err
    assert VETO in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_parallel_registration_that_would_copy_its_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base whose book is already the same v2 veto configuration (same epoch) gives the same
    `config_id`; registering it again as a parallel book would be a second copy of one book."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    book = SurvivorBookConfig(
        **{**BOOK, "schema_version": "survivor-book-config/v2"}, entry_veto=VETO
    )
    config, criteria, settings = register.registration_body(VALID_EPOCH, book=book)
    registry.register(
        config, criteria, repo / FIRST, code_commit=research_repo.head(repo), settings=settings
    )
    research_repo.commit_file(repo, repo / FIRST, "a v2 veto base", at=tds.COMMITTED)

    code, _, err = _run(
        capsys, *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=FIRST)
    )

    assert code == 2
    assert FIRST in err
    assert "same configuration" in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_second_parallel_registration_of_the_same_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base book has one parallel book: the second registration of the same configuration is
    refused by name, and the first file stays exactly as it was."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    _commit_a_parallel_registration(repo, capsys)
    first = (repo / REGISTRATION).read_bytes()

    code, _, err = _run(
        capsys, *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=FIRST)
    )

    assert code == 2
    assert REGISTRATION in err
    assert "parallel" in err
    assert (repo / REGISTRATION).read_bytes() == first
    assert _status(repo) == ""


def test_a_parallel_registration_is_not_a_chain_root() -> None:
    """R-N5's chain half, structurally: a registration carrying `survivor_parallel_of` is not a
    root and not a link, so a base beside its parallel registration is still one rooted chain
    (without the exclusion it would count as a second root)."""
    base = _survivor_body()
    parallel = _survivor_body(
        survivor_parallel_of={"path": "docs/research/a.json", "sha256": "0" * 64}
    )
    continuation = _survivor_body(survivor_continuation=_continuation("docs/research/a.json"))

    assert (
        register.chain_problems(
            {
                "docs/research/a.json": base,
                "docs/research/b.json": continuation,
                "docs/research/p.json": parallel,
            }
        )
        == []
    )


def test_a_parallel_registration_that_also_names_a_continuation_is_refused() -> None:
    """R-N5: a parallel registration is never a continuation; both blocks in one body is refused
    by name."""
    both = _survivor_body(
        survivor_parallel_of={"path": "docs/research/a.json", "sha256": "0" * 64},
        survivor_continuation=_continuation("docs/research/a.json"),
    )

    problems = register.chain_problems(
        {"docs/research/a.json": _survivor_body(), "docs/research/b.json": both}
    )

    assert problems
    assert any("b.json" in problem and "survivor_parallel_of" in problem for problem in problems)


def test_a_parallel_registration_naming_no_committed_base_is_refused() -> None:
    """The base a parallel registration names is a member of the committed set: a path outside it
    (a missing file, a smoke registration, itself) is refused by name."""
    problems = register.chain_problems(
        {
            "docs/research/p.json": _survivor_body(
                survivor_parallel_of={"path": "docs/research/missing.json", "sha256": "0" * 64}
            )
        }
    )

    assert problems
    assert any("p.json" in problem and "missing.json" in problem for problem in problems)


def test_a_parallel_registration_naming_itself_is_refused() -> None:
    problems = register.chain_problems(
        {
            "docs/research/p.json": _survivor_body(
                survivor_parallel_of={"path": "docs/research/p.json", "sha256": "0" * 64}
            )
        }
    )

    assert problems
    assert any("p.json" in problem and "itself" in problem for problem in problems)


def _commit_a_parallel_registration_with_rule(
    repo: Path, capsys: pytest.CaptureFixture[str], rule: str
) -> str:
    """A committed parallel registration of the same base under `rule` -- fabricated past the
    writer for an R-N7 rule this build does not register yet, so the writer's second-parallel
    check can be pinned on the rule it keys on."""
    base_config, criteria, settings = register.registration_body(VALID_EPOCH)
    block = {
        "path": FIRST,
        "sha256": hashlib.sha256((repo / FIRST).read_bytes()).hexdigest(),
    }
    config = {
        **base_config,
        "survivor_book": {
            **base_config["survivor_book"],
            "schema_version": "survivor-book-config/v2",
            "entry_veto": rule,
        },
    }
    path = repo / "docs/research/p7-parallel-by-another-rule.json"
    registry.register(
        config,
        criteria,
        path,
        code_commit=research_repo.head(repo),
        settings={**settings, PARALLEL_KEY: block},
    )
    research_repo.commit_file(
        repo, path, "a parallel registration by another rule", at=tds.COMMITTED
    )
    return path.relative_to(repo).as_posix()


def test_a_parallel_registration_by_another_rule_beside_the_same_base_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One base book has one parallel book **per entry-veto rule**: a different rule is a
    different book (R-N7's LLM veto will need one beside 006b), so it is not refused. The same
    base and the same rule is (`test_the_writer_refuses_a_second_parallel_registration_...`)."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    _commit_a_parallel_registration_with_rule(repo, capsys, "llm_bearish/v1")

    code, _, err = _run(
        capsys, *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=FIRST)
    )

    assert code == 0, err
    written = json.loads((repo / REGISTRATION).read_text(encoding="utf-8"))
    assert written["settings"][PARALLEL_KEY]["path"] == FIRST
    assert written["config"]["survivor_book"]["entry_veto"] == VETO


def test_the_writer_refuses_an_unknown_entry_veto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A value that is not a rule this build registers is refused by name -- not written into a
    v2 book the daily command would refuse -- and nothing is written."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)

    code, _, err = _run(
        capsys,
        *_arguments(
            smoke=research_repo.head(repo),
            entry_veto="llm_bearish/v1",
            parallel_of=FIRST,
        ),
    )

    assert code == 2
    assert "llm_bearish/v1" in err
    assert VETO in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_parallel_registration_beside_a_parallel_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A parallel book runs beside a base book; another parallel registration is not a base, so
    it is refused by name (`_parallel_base`'s "is itself a parallel registration")."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    _commit_a_parallel_registration(repo, capsys)
    beside = "docs/research/p7-a-second-parallel.json"

    code, _, err = _run(
        capsys,
        *_arguments(
            smoke=research_repo.head(repo),
            entry_veto=VETO,
            parallel_of=REGISTRATION,
            path=beside,
        ),
    )

    assert code == 2
    assert REGISTRATION in err
    assert "parallel" in err
    assert not (repo / beside).exists()


def test_the_writer_refuses_a_parallel_registration_beside_a_smoke_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A parallel book runs beside the registered book, never beside a smoke run: a smoke
    registration is refused by name, and nothing is written."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    smoke = _commit_a_smoke_registration(repo)

    code, _, err = _run(
        capsys,
        *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=smoke),
    )

    assert code == 2
    assert smoke in err
    assert "smoke" in err
    assert not (repo / REGISTRATION).exists()


def test_the_writer_refuses_a_parallel_registration_beside_a_non_survivor_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The base is a **survivor** book's registration (the 006b one): a committed registration of
    another kind -- the P6 book's -- is refused by name and nothing is written."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    p6 = "docs/research/p6-registration.json"
    (repo / p6).parent.mkdir(parents=True, exist_ok=True)
    registry.register(
        {"start": "2026-01-12"},
        {"primary": "the P6 book's"},
        repo / p6,
        code_commit=research_repo.head(repo),
        settings={},
    )
    research_repo.commit_file(repo, repo / p6, "the P6 registration", at=tds.COMMITTED)

    code, _, err = _run(
        capsys,
        *_arguments(smoke=research_repo.head(repo), entry_veto=VETO, parallel_of=p6),
    )

    assert code == 2
    assert p6 in err
    assert "not a survivor registration" in err
    assert not (repo / REGISTRATION).exists()


def test_a_continuation_naming_a_parallel_registration_is_refused() -> None:
    """R-N5's sample half: a parallel book's records never enter the chain's confirmatory sample,
    so a registration whose `survivor_continuation` names a parallel registration is refused by
    name -- the chain is one line of base registrations, and a parallel book is outside it."""
    parallel = _survivor_body(
        survivor_parallel_of={"path": "docs/research/a.json", "sha256": "0" * 64}
    )
    successor = _survivor_body(survivor_continuation=_continuation("docs/research/p.json"))

    problems = register.chain_problems(
        {
            "docs/research/a.json": _survivor_body(),
            "docs/research/p.json": parallel,
            "docs/research/s.json": successor,
        }
    )

    assert problems
    assert any(
        "s.json" in problem and "parallel" in problem and "p.json" in problem
        for problem in problems
    )


def test_the_writer_refuses_superseding_a_parallel_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The writer's own end of the same rule (R-N5): `--supersedes` naming the parallel book is
    refused by name before anything is written, so a successor can never continue it."""
    repo = _repository(tmp_path)
    _pin_the_clock(monkeypatch)
    monkeypatch.chdir(repo)
    _commit_a_survivor_registration(repo)
    _commit_a_parallel_registration(repo, capsys)
    successor = "docs/research/p7-successor-registration.json"

    code, _, err = _run(
        capsys,
        *_arguments(smoke=research_repo.head(repo), supersedes=REGISTRATION, path=successor),
    )

    assert code == 2
    assert REGISTRATION in err
    assert "parallel" in err
    assert not (repo / successor).exists()
