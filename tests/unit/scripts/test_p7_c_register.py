"""`scripts/p7_c_register.py`: the `V2-P7-013` agent-forward (C) registration's writer.

The C registration is written once, by hand, at L5; it is never committed by the writer (the user
commits it). This suite freezes the body it writes as literals, so a changed default in `src/` or
in the evaluator's adopted constants cannot move C's registered numbers without this file going
red, and it runs every refusal against a throwaway repository built the way
`test_p7_register.py` builds Step 0's: hermetic git, pinned commit times, the two C scripts and one
file under every path the registry binds.

The endpoint is deliberately *not* filled in `src/`: `DEEPSEEK_V4_1_FLASH.verified_on` is `None`
until the gated L2 step, so every success path here monkeypatches the writer's own module-level
constant with `dataclasses.replace(verified_on=...)` for the duration of the test and the shipped
constant stays untouched.
"""

from __future__ import annotations

import ast
import dataclasses
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
    for root in (directory, directory / "research"):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    return importlib.import_module(name)


register = _script("p7_c_register")
register_step0 = _script("p7_register")
agent_evening = _script("agent_evening")
grid = _script("grid")
registry = _script("registry")

from openalpha_cn import strategy_agent_evaluation as evaluation  # noqa: E402
from openalpha_cn.models.deepseek import DEEPSEEK_V4_1_FLASH  # noqa: E402

TODAY: Final[date] = date(2026, 11, 12)
"""A Thursday: the writer's "today", pinned so the anchor bounds do not read the wall clock."""
VALID_ANCHOR: Final[date] = date(2026, 11, 12)
SALT: Final[str] = "c-plan-2026-sample"
STEP0: Final[str] = "docs/research/p7-registration.json"
C_REGISTRATION: Final[str] = "docs/research/p7-c-registration.json"
STEP0_SEED: Final[int] = 20_260_926

CRITERIA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "primary",
        "futility",
        "interim",
        "not_measurable",
        "exploratory",
        "evidence_roster",
        "evidence",
        "power",
        "dry_week",
        "report",
        "model",
        "forward_only",
        "wording",
    }
)
"""The criteria section names every decision the registration freezes (D1/D4/D5/D7/D8)."""

FORBIDDEN_WORDING: Final[tuple[str, ...]] = ("proven", "验证有效", "已证实", "已验证", "证实有效")

PROTOCOL_BLOCK: Final[dict[str, Any]] = {
    "schema_version": "agent-forward-protocol/v1",
    "primary_family": "agent_llm_structured/v1",
    "primary_set": "primary",
    "primary_horizon": 1,
    "secondary": [
        {"family": "agent_rule_baseline/v1", "scoring_set": "primary", "horizon": 1},
        {"family": "agent_llm_structured/v1", "scoring_set": "primary", "horizon": 5},
        {"family": "agent_llm_structured/v1", "scoring_set": "coverage", "horizon": 1},
    ],
    "window_sessions": 120,
    "min_scored": 10,
    "min_measurable_sessions": 60,
    "interim_session": 60,
    "level": 0.05,
    "q": 0.1,
    "bootstrap_samples": 100_000,
    "random_seed": 20_260_926,
}
"""The `agent-forward-protocol/v1` block D2/D3/D5 register, written out by hand."""


def _step0(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "path": STEP0,
        "registration_sha256": "a" * 64,
        "config_id": "b" * 64,
        "seed": STEP0_SEED,
    }
    fields.update(overrides)
    return register.Step0Registration(**fields)


def _power(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "sample": "n300",
        "verdict": "testable",
        "register_untestable": False,
        "report_sha256": "c" * 64,
        "measurable_sessions": 72,
        "detectable_mean": 0.031,
    }
    fields.update(overrides)
    return register.PowerDecision(**fields)


def _step0_block(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "schema_version": "agent-step0-registration/v1",
        "path": STEP0,
        "registration_sha256": "a" * 64,
        "config_id": "b" * 64,
        "seed": STEP0_SEED,
    }
    block.update(overrides)
    return block


def _power_block(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "schema_version": "agent-forward-power/v1",
        "sample": "n300",
        "verdict": "testable",
        "register_untestable": False,
        "report_sha256": "c" * 64,
        "measurable_sessions": 72,
        "detectable_mean": 0.031,
    }
    block.update(overrides)
    return block


def _anchor_block() -> dict[str, Any]:
    return {"schema_version": "agent-forward-anchor/v1", "session": VALID_ANCHOR.isoformat()}


# --- Task 3.3 (body): the frozen registration body and its companion blocks ----------------------


def test_the_registration_body_is_the_frozen_one() -> None:
    config, criteria, settings = register.c_registration_body(
        salt=SALT, step0_registration=_step0(), power=_power(), anchor=VALID_ANCHOR
    )

    assert config == {
        "kind": "agent_forward/v1",
        "salt": SALT,
        "primary_size": register.PRIMARY_SIZE,
    }
    assert register.AGENT_REGISTRATION_KIND == "agent_forward/v1"
    assert register.PRIMARY_SIZE == 300
    assert set(criteria) == CRITERIA_KEYS
    assert settings == {
        **grid.protocol_settings(),
        evaluation.AGENT_FORWARD_PROTOCOL_KEY: PROTOCOL_BLOCK,
        evaluation.AGENT_FORWARD_POWER_KEY: _power_block(),
        evaluation.AGENT_FORWARD_STEP0_KEY: _step0_block(),
        evaluation.AGENT_FORWARD_ANCHOR_KEY: _anchor_block(),
    }
    assert settings["random_seed"] == settings[evaluation.AGENT_FORWARD_PROTOCOL_KEY]["random_seed"]
    assert (
        settings["bootstrap_samples"]
        == settings[evaluation.AGENT_FORWARD_PROTOCOL_KEY]["bootstrap_samples"]
    )
    assert settings["random_seed"] == STEP0_SEED
    assert "smoke_run" not in settings
    assert "survivor_continuation" not in settings


def test_the_body_draws_the_census_when_the_power_decision_did() -> None:
    config, _, settings = register.c_registration_body(
        salt=SALT,
        step0_registration=_step0(),
        power=_power(sample="census", measurable_sessions=118, detectable_mean=0.02),
        anchor=VALID_ANCHOR,
    )

    assert register.CENSUS_DRAW_SIZE == 10**6
    assert config["primary_size"] == register.CENSUS_DRAW_SIZE
    power = settings[evaluation.AGENT_FORWARD_POWER_KEY]
    assert power["sample"] == "census"
    assert power["verdict"] == "testable"
    assert power["register_untestable"] is False
    assert power["measurable_sessions"] == 118
    assert power["detectable_mean"] == 0.02


def test_the_body_records_an_untestable_decision_and_the_users_flag() -> None:
    config, _, settings = register.c_registration_body(
        salt=SALT,
        step0_registration=_step0(),
        power=_power(verdict="not_testable", register_untestable=True, detectable_mean=None),
        anchor=VALID_ANCHOR,
    )

    assert config["primary_size"] == register.PRIMARY_SIZE
    power = settings[evaluation.AGENT_FORWARD_POWER_KEY]
    assert power["sample"] == "n300"
    assert power["verdict"] == "not_testable"
    assert power["register_untestable"] is True
    assert power["detectable_mean"] is None


def test_the_body_is_fresh_state_not_the_frozen_literals() -> None:
    _, criteria, _ = register.c_registration_body(
        salt=SALT, step0_registration=_step0(), power=_power(), anchor=VALID_ANCHOR
    )
    criteria["wording"] = "changed"

    _, criteria_again, _ = register.c_registration_body(
        salt=SALT, step0_registration=_step0(), power=_power(), anchor=VALID_ANCHOR
    )
    assert criteria_again["wording"] != "changed"


def test_the_criteria_freeze_every_decision_and_claim_no_verdict() -> None:
    _, criteria, _ = register.c_registration_body(
        salt=SALT, step0_registration=_step0(), power=_power(), anchor=VALID_ANCHOR
    )

    assert set(criteria) == CRITERIA_KEYS
    for key, value in criteria.items():
        assert isinstance(value, str) and value.strip(), key
        for forbidden in FORBIDDEN_WORDING:
            assert forbidden not in value, (key, forbidden)
    assert "120" in criteria["primary"]
    assert "h=1" in criteria["primary"]
    assert "alpha=0.05" in criteria["primary"]
    assert "候选" in criteria["primary"]
    assert "60" in criteria["futility"] and "sticky" in criteria["futility"]
    assert "60" in criteria["interim"]
    assert "10" in criteria["not_measurable"] and "60" in criteria["not_measurable"]
    assert "q=0.10" in criteria["exploratory"]
    assert "limit_up" in criteria["evidence_roster"]
    assert "consecutive_board" in criteria["evidence_roster"]
    assert "broken_board" in criteria["evidence_roster"]
    assert "moneyflow" in criteria["evidence_roster"]
    assert "n300" in criteria["power"] and "census" in criteria["power"]
    assert "--register-untestable" in criteria["power"]
    assert "5" in criteria["dry_week"] and "600" in criteria["dry_week"]
    assert "agent_report.py" in criteria["report"]
    assert "forward_report.py" in criteria["report"]
    assert "verified_on" in criteria["model"] and "None" in criteria["model"]
    assert "deepseek-flash" in criteria["model"]
    assert "72" in criteria["forward_only"]
    assert "候选" in criteria["wording"]


# --- Task 3.3 (writer): a throwaway repository and the command line ------------------------------

BOUND_FILES: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
    "scripts/agent_evening.py",
    "scripts/agent_report.py",
)
"""One file under every bound path (`registry.REGISTERED_PATHS` plus C's two scripts)."""


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
    _commit_step_zero(repo)
    return repo


def _commit_step_zero(repo: Path) -> None:
    """Step 0's committed survivor registration, written through `p7_register`'s own body."""
    config, criteria, settings = register_step0.registration_body(date(2026, 11, 11))
    target = repo / STEP0
    registry.register(
        config, criteria, target, code_commit=research_repo.head(repo), settings=settings
    )
    research_repo.commit_file(repo, target, "Step 0's registration", at=tds.COMMITTED)


def _commit_another_kind(repo: Path) -> str:
    """A committed registration whose `config.kind` is not the survivor book's."""
    path = "docs/research/p7-agent-only-registration.json"
    target = repo / path
    registry.register(
        {"kind": "agent_forward/v1", "salt": SALT, "primary_size": 300},
        {"wording": "候选"},
        target,
        code_commit=research_repo.head(repo),
        settings={"random_seed": STEP0_SEED},
    )
    research_repo.commit_file(repo, target, "not Step 0's registration", at=tds.COMMITTED)
    return path


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sample": "n300",
        "sample_size": 300,
        "projected_scored_names": 18,
        "observed_measurable_sessions": 70,
        "measurable_sessions": 72,
        "periods": 72,
        "detectable_mean": 0.031,
        "verdict": "testable",
    }
    row.update(overrides)
    return row


def _census_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = _row(
        sample="census",
        sample_size=None,
        projected_scored_names=118,
        measurable_sessions=118,
        periods=118,
        detectable_mean=0.02,
    )
    row.update(overrides)
    return row


def _power_report(
    path: Path, *, rows: dict[str, Any] | None = None, schema: str = "openalpha-p7-c-power/v1"
) -> Path:
    if rows is None:
        rows = {"n300": _row(), "census": _census_row()}
    document = {
        "schema": schema,
        "wording": ["候选", "no labels and no returns were read", "not evidence"],
        "first": "2026-01-05",
        "last": "2026-06-05",
        "sessions_observed": 100,
        "planning": {
            "window_sessions": 120,
            "primary_horizon": 1,
            "between_day_sd": 0.15,
            "test_level": 0.05,
            "target_power": 0.8,
            "primary_sample_size": 300,
            "min_scored_names": 10,
            "min_measurable_sessions": 60,
        },
        "source": None,
        "rows": rows,
    }
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    return path


def _pin_world(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    *,
    today: date = TODAY,
    verified: bool = True,
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(register, "_today", lambda: today)
    if verified:
        monkeypatch.setattr(
            register,
            "DEEPSEEK_V4_1_FLASH",
            dataclasses.replace(DEEPSEEK_V4_1_FLASH, verified_on=today),
        )


def _arguments(
    *,
    power: Path,
    salt: str = SALT,
    step0: str = STEP0,
    anchor: date = VALID_ANCHOR,
    path: str = C_REGISTRATION,
    dry_run: bool = False,
    register_untestable: bool = False,
) -> list[str]:
    arguments = [
        "--salt",
        salt,
        "--step0-registration",
        step0,
        "--power-report",
        str(power),
        "--anchor",
        anchor.isoformat(),
        "--path",
        path,
    ]
    if dry_run:
        arguments.append("--dry-run")
    if register_untestable:
        arguments.append("--register-untestable")
    return arguments


def _run(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, str, str]:
    code = register.main(list(arguments))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _printed_digest(text: str) -> str:
    """The digest the command printed, read from its own `sha256 <digest>` line."""
    digest = text.rsplit("sha256 ", 1)[-1].split()[0]
    assert len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)
    return digest


def _status(repo: Path) -> str:
    return research_repo.git(repo, "status", "--porcelain", "--untracked-files=all")


def _files(repo: Path) -> list[str]:
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


def _written(repo: Path) -> dict[str, Any]:
    return json.loads((repo / C_REGISTRATION).read_text(encoding="utf-8"))


def _success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    rows: dict[str, Any] | None = None,
    register_untestable: bool = False,
) -> tuple[Path, dict[str, Any]]:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json", rows=rows)
    code, _out, err = _run(
        capsys, *_arguments(power=power, register_untestable=register_untestable)
    )
    assert code == 0, err
    return repo, _written(repo)


def test_dry_run_writes_nothing_and_prints_the_canonical_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")
    before_status, before_files = _status(repo), _files(repo)

    code, out, err = _run(capsys, *_arguments(power=power, dry_run=True))

    assert code == 0, err
    document = json.loads(out.split("config_id ")[0])
    assert document["config"] == {
        "kind": "agent_forward/v1",
        "salt": SALT,
        "primary_size": 300,
    }
    assert document["config_id"] == grid.config_id(document["config"])
    assert document["code_commit"] == research_repo.head(repo)
    assert document["schema"] == registry.REGISTRATION_SCHEMA
    assert document["config_id"] in out
    assert _printed_digest(out) == hashlib.sha256(out.split("config_id ")[0].encode()).hexdigest()
    assert "未写入" in out
    assert _status(repo) == before_status
    assert _files(repo) == before_files
    assert not (repo / C_REGISTRATION).exists()


def test_a_relative_power_report_resolves_against_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    _power_report(repo / "docs/research/p7-c-power.json")

    code, _, err = _run(capsys, *_arguments(power=Path("docs/research/p7-c-power.json")))

    assert code == 0, err
    assert (repo / C_REGISTRATION).is_file()


def test_writes_once_never_commits_and_the_bytes_are_the_printed_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")
    commits = research_repo.git(repo, "rev-list", "--count", "HEAD").strip()
    before = _status(repo)

    dry = _run(capsys, *_arguments(power=power, dry_run=True))
    assert dry[0] == 0, dry[2]
    digest = _printed_digest(dry[1])

    code, out, err = _run(capsys, *_arguments(power=power))
    assert code == 0, err
    content = (repo / C_REGISTRATION).read_bytes()
    assert hashlib.sha256(content).hexdigest() == digest
    assert digest in out

    after = _status(repo)
    assert set(after.splitlines()) - set(before.splitlines()) == {f"?? {C_REGISTRATION}"}
    assert research_repo.git(repo, "rev-list", "--count", "HEAD").strip() == commits

    again = _run(capsys, *_arguments(power=power))
    assert again[0] == 0, again[2]
    assert digest in again[1]
    assert (repo / C_REGISTRATION).read_bytes() == content


def test_the_written_document_reads_back_with_every_companion_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, document = _success(tmp_path, monkeypatch, capsys)
    content = (repo / C_REGISTRATION).read_bytes()
    settings = document["settings"]
    body = {
        "config": document["config"],
        "config_id": document["config_id"],
        "code_commit": document["code_commit"],
        "settings": settings,
    }

    registration = agent_evening.read_agent_registration(
        body, content=content, commit=document["code_commit"]
    )
    assert registration.salt == SALT
    assert registration.primary_size == register.PRIMARY_SIZE
    assert registration.registration_sha256 == hashlib.sha256(content).hexdigest()

    protocol = evaluation.read_agent_protocol(settings)
    assert protocol.primary_family == "agent_llm_structured/v1"
    assert protocol.primary_set == "primary"
    assert protocol.primary_horizon == 1
    assert protocol.window_sessions == evaluation.ADOPTED_WINDOW_SESSIONS == 120
    assert protocol.interim_session == evaluation.ADOPTED_INTERIM_SESSION == 60
    assert protocol.min_scored == evaluation.ADOPTED_MIN_SCORED == 10
    assert protocol.min_measurable_sessions == evaluation.ADOPTED_MIN_MEASURABLE_SESSIONS == 60
    assert protocol.level == evaluation.ADOPTED_LEVEL == 0.05
    assert protocol.q == evaluation.ADOPTED_Q == 0.1
    assert [(arm.family, arm.scoring_set, arm.horizon) for arm in protocol.secondary] == [
        tuple(arm) for arm in evaluation.ADOPTED_SECONDARY_ARMS
    ]

    power = evaluation.read_agent_forward_power(settings)
    assert power.sample == "n300"
    assert power.verdict == "testable"
    assert power.register_untestable is False

    step0 = evaluation.read_agent_forward_step0(settings)
    assert step0.path == STEP0
    assert step0.registration_sha256 == hashlib.sha256((repo / STEP0).read_bytes()).hexdigest()
    assert step0.config_id == json.loads((repo / STEP0).read_text(encoding="utf-8"))["config_id"]
    assert step0.seed == STEP0_SEED

    anchor = evaluation.read_agent_forward_anchor(settings)
    assert anchor.session == VALID_ANCHOR


def test_code_commit_is_head_passed_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")
    captures: list[str | None] = []
    original = registry.register

    def recording(config: Any, criteria: Any, path: Path, **keywords: Any) -> str:
        captures.append(keywords.get("code_commit"))
        return original(config, criteria, path, **keywords)

    monkeypatch.setattr(registry, "register", recording)

    code, _, err = _run(capsys, *_arguments(power=power))

    assert code == 0, err
    assert captures == [research_repo.head(repo)]
    assert _written(repo)["code_commit"] == research_repo.head(repo)


# --- Task 3.3: the D1 power selection -----------------------------------------------------------


def test_selects_n300_when_it_is_testable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    power_path = tmp_path / "p7-c-power.json"
    rows = {
        "n300": _row(verdict="testable", measurable_sessions=72, detectable_mean=0.031),
        "census": _census_row(verdict="testable", measurable_sessions=118, detectable_mean=0.02),
    }
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    _power_report(power_path, rows=rows)

    code, _, err = _run(capsys, *_arguments(power=power_path))

    assert code == 0, err
    document = _written(repo)
    assert document["config"]["primary_size"] == 300
    block = document["settings"][evaluation.AGENT_FORWARD_POWER_KEY]
    assert block["sample"] == "n300"
    assert block["verdict"] == "testable"
    assert block["register_untestable"] is False
    assert block["measurable_sessions"] == 72
    assert block["detectable_mean"] == 0.031
    assert block["report_sha256"] == hashlib.sha256(power_path.read_bytes()).hexdigest()


def test_falls_back_to_the_census_when_only_it_is_testable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    power_path = tmp_path / "p7-c-power.json"
    rows = {
        "n300": _row(verdict="not_testable", measurable_sessions=12, detectable_mean=None),
        "census": _census_row(verdict="testable", measurable_sessions=118, detectable_mean=0.02),
    }
    _repo, document = _success(tmp_path, monkeypatch, capsys, rows=rows)

    assert document["config"]["primary_size"] == register.CENSUS_DRAW_SIZE
    block = document["settings"][evaluation.AGENT_FORWARD_POWER_KEY]
    assert block["sample"] == "census"
    assert block["verdict"] == "testable"
    assert block["register_untestable"] is False
    assert block["measurable_sessions"] == 118
    assert block["detectable_mean"] == 0.02
    assert block["report_sha256"] == hashlib.sha256(power_path.read_bytes()).hexdigest()


def test_both_untestable_refuses_and_names_the_users_unmade_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(
        tmp_path / "p7-c-power.json",
        rows={
            "n300": _row(verdict="not_testable", detectable_mean=None),
            "census": _census_row(verdict="not_testable", detectable_mean=None),
        },
    )

    code, _, err = _run(capsys, *_arguments(power=power))

    assert code == 2
    assert "not_testable" in err
    assert "--register-untestable" in err
    assert "L5" in err or "user" in err
    assert not (repo / C_REGISTRATION).exists()


def test_register_untestable_writes_a_not_testable_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = {
        "n300": _row(verdict="not_testable", measurable_sessions=12, detectable_mean=None),
        "census": _census_row(verdict="not_testable", detectable_mean=None),
    }
    _repo, document = _success(tmp_path, monkeypatch, capsys, rows=rows, register_untestable=True)

    assert document["config"]["primary_size"] == register.PRIMARY_SIZE
    block = document["settings"][evaluation.AGENT_FORWARD_POWER_KEY]
    assert block["sample"] == "n300"
    assert block["verdict"] == "not_testable"
    assert block["register_untestable"] is True
    assert block["measurable_sessions"] == 12
    assert block["detectable_mean"] is None


@pytest.mark.parametrize(
    "rows",
    [
        {"n300": _row(verdict="testable", detectable_mean=0.031), "census": _census_row()},
        {
            "n300": _row(verdict="not_testable", detectable_mean=None),
            "census": _census_row(verdict="testable", detectable_mean=0.02),
        },
    ],
)
def test_register_untestable_is_refused_when_the_chosen_set_is_testable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rows: dict[str, Any],
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json", rows=rows)

    code, _, err = _run(capsys, *_arguments(power=power, register_untestable=True))

    assert code == 2
    assert "testable" in err
    assert not (repo / C_REGISTRATION).exists()


def test_a_missing_or_malformed_power_report_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    missing = tmp_path / "missing.json"
    not_json = tmp_path / "not-json.json"
    not_json.write_text("{", encoding="utf-8")
    wrong_schema = _power_report(tmp_path / "schema.json", schema="openalpha-other/v1")
    rows_not_a_mapping = tmp_path / "rows-list.json"
    rows_not_a_mapping.write_text(
        json.dumps({"schema": "openalpha-p7-c-power/v1", "rows": []}), encoding="utf-8"
    )
    missing_census = _power_report(tmp_path / "census.json", rows={"n300": _row()})
    bad_verdict = _power_report(
        tmp_path / "verdict.json", rows={"n300": _row(verdict="maybe"), "census": _census_row()}
    )
    bad_count = _power_report(
        tmp_path / "count.json",
        rows={"n300": _row(measurable_sessions="seventy"), "census": _census_row()},
    )
    bad_mean = _power_report(
        tmp_path / "mean.json", rows={"n300": _row(detectable_mean="low"), "census": _census_row()}
    )

    for path in (
        missing,
        not_json,
        wrong_schema,
        rows_not_a_mapping,
        missing_census,
        bad_verdict,
        bad_count,
        bad_mean,
    ):
        code, _, err = _run(capsys, *_arguments(power=path))
        assert code == 2, path
        assert "power report" in err.lower(), (path, err)
        assert not (repo / C_REGISTRATION).exists()


# --- Task 3.3: the refusals ---------------------------------------------------------------------


def test_refuses_an_unverified_deepseek_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo, verified=False)
    power = _power_report(tmp_path / "p7-c-power.json")

    code, _, err = _run(capsys, *_arguments(power=power))

    assert code == 2
    assert "verified_on" in err
    assert "DEEPSEEK_V4_1_FLASH" in err or "DeepSeek" in err
    assert "L2" in err
    assert not (repo / C_REGISTRATION).exists()


@pytest.mark.parametrize("salt", ["2026-09-26", "salt-20260926-derived"])
def test_refuses_a_salt_tied_to_the_step_zero_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    salt: str,
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")

    code, _, err = _run(capsys, *_arguments(power=power, salt=salt))

    assert code == 2
    assert "seed" in err
    assert not (repo / C_REGISTRATION).exists()


def test_refuses_a_step_zero_registration_that_is_absent_uncommitted_or_edited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    power = _power_report(tmp_path / "p7-c-power.json")

    absent = _repository(tmp_path / "absent")
    _pin_world(monkeypatch, absent)
    code, _, err = _run(capsys, *_arguments(power=power, step0="docs/research/not-there.json"))
    assert code == 2
    assert "not-there.json" in err
    assert not (absent / C_REGISTRATION).exists()

    uncommitted = _repository(tmp_path / "uncommitted")
    _pin_world(monkeypatch, uncommitted)
    stray = "docs/research/p7-uncommitted-registration.json"
    (uncommitted / stray).write_text("{}\n", encoding="utf-8")
    code, _, err = _run(capsys, *_arguments(power=power, step0=stray))
    assert code == 2
    assert stray in err
    assert not (uncommitted / C_REGISTRATION).exists()

    edited = _repository(tmp_path / "edited")
    _pin_world(monkeypatch, edited)
    (edited / STEP0).write_text("{}\n", encoding="utf-8")
    code, _, err = _run(capsys, *_arguments(power=power))
    assert code == 2
    assert STEP0 in err
    assert not (edited / C_REGISTRATION).exists()


def test_refuses_a_step_zero_registration_of_another_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    other = _commit_another_kind(repo)
    power = _power_report(tmp_path / "p7-c-power.json")

    code, _, err = _run(capsys, *_arguments(power=power, step0=other))

    assert code == 2
    assert "survivor_book/v1" in err or "kind" in err
    assert not (repo / C_REGISTRATION).exists()


@pytest.mark.parametrize(
    ("anchor", "reason"),
    [
        (date(2026, 11, 13), "future"),
        (date(2026, 11, 14), "weekday"),
        (date(2026, 10, 1), "16"),
    ],
)
def test_refuses_an_anchor_that_is_a_weekend_in_the_future_or_too_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    anchor: date,
    reason: str,
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")

    code, _, err = _run(capsys, *_arguments(power=power, anchor=anchor))

    assert code == 2
    assert reason in err
    assert not (repo / C_REGISTRATION).exists()


def test_refuses_a_path_outside_docs_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")

    outside = _run(capsys, *_arguments(power=power, path="docs/p7-c-registration.json"))
    suffix = _run(capsys, *_arguments(power=power, path="docs/research/p7-c-registration.txt"))

    assert outside[0] == 2
    assert "docs/research" in outside[2] and ".json" in outside[2]
    assert suffix[0] == 2
    assert "docs/research" in suffix[2] and ".json" in suffix[2]
    assert not (repo / "docs/p7-c-registration.json").exists()
    assert not (repo / "docs/research/p7-c-registration.txt").exists()


def test_refuses_an_existing_path_with_other_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    (repo / C_REGISTRATION).parent.mkdir(parents=True, exist_ok=True)
    (repo / C_REGISTRATION).write_text("{}\n", encoding="utf-8")
    power = _power_report(tmp_path / "p7-c-power.json")

    code, _, err = _run(capsys, *_arguments(power=power))

    assert code == 2
    assert "different registration" in err
    assert (repo / C_REGISTRATION).read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize(
    "changed",
    ["src/openalpha_cn/strategy.py", "scripts/agent_evening.py", "scripts/agent_report.py"],
)
def test_refuses_a_dirty_bound_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed: str,
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    (repo / changed).write_text("RULES = 2\n", encoding="utf-8")
    power = _power_report(tmp_path / "p7-c-power.json")

    code, _, err = _run(capsys, *_arguments(power=power))

    assert code == 2
    assert "uncommitted" in err
    assert not (repo / C_REGISTRATION).exists()


def test_a_digest_mismatch_removes_the_file_it_just_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")
    monkeypatch.setattr(register, "_document", lambda *arguments, **keywords: b"{}\n")

    code, _, err = _run(capsys, *_arguments(power=power))

    assert code == 2
    assert "digest" in err
    assert not (repo / C_REGISTRATION).exists()
    assert _status(repo) == ""


def test_another_registration_error_is_its_own_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path)
    _pin_world(monkeypatch, repo)
    power = _power_report(tmp_path / "p7-c-power.json")

    def refuse(*arguments: Any, **keywords: Any) -> str:
        raise registry.RegistrationError("the registry said no")

    monkeypatch.setattr(registry, "register", refuse)

    code, _, err = _run(capsys, *_arguments(power=power))

    assert code == 2
    assert "the registry said no" in err
    assert "the registry refused the registration" in err
    assert "different registration" not in err
    assert not (repo / C_REGISTRATION).exists()


def test_the_writer_never_invokes_a_staging_or_committing_step() -> None:
    source = (ROOT / "scripts/p7_c_register.py").read_text(encoding="utf-8")

    assert "git add" not in source
    assert "git commit" not in source


def test_no_git_call_asks_for_staging_or_a_commit() -> None:
    tree = ast.parse((ROOT / "scripts/p7_c_register.py").read_text(encoding="utf-8"))

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


def test_the_c_registration_is_not_a_survivor_chain_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, document = _success(tmp_path, monkeypatch, capsys)
    research_repo.commit_file(repo, repo / C_REGISTRATION, "C's registration", at=tds.COMMITTED)

    assert document["config"]["kind"] == "agent_forward/v1"
    committed = register_step0.committed_survivor_registrations(repo)
    assert C_REGISTRATION not in committed
    assert set(committed) == {STEP0}
    assert register_step0.chain_problems(committed) == []


def test_a_write_in_a_throwaway_repository_creates_no_file_in_the_real_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = ROOT / C_REGISTRATION
    before = real.exists()

    _success(tmp_path, monkeypatch, capsys)

    assert real.exists() == before, f"{real} was written by a test"


# --- Task 3.3: the read-back constructor --------------------------------------------------------


def _committed_step_zero(repo: Path) -> Any:
    content = (repo / STEP0).read_bytes()
    body = json.loads(content)
    return register.Step0Registration(
        path=STEP0,
        registration_sha256=hashlib.sha256(content).hexdigest(),
        config_id=body["config_id"],
        seed=body["settings"]["random_seed"],
    )


def test_c_read_back_accepts_the_body(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    step0 = _committed_step_zero(repo)
    config, criteria, settings = register.c_registration_body(
        salt=SALT, step0_registration=step0, power=_power(), anchor=VALID_ANCHOR
    )

    assert (
        register.c_read_back(
            config,
            criteria,
            settings,
            code_commit=research_repo.head(repo),
            step0_registration=step0,
            repo=repo,
        )
        is None
    )


def test_c_read_back_refuses_a_step0_block_that_names_a_different_registration(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    step0 = _committed_step_zero(repo)
    config, criteria, settings = register.c_registration_body(
        salt=SALT, step0_registration=step0, power=_power(), anchor=VALID_ANCHOR
    )
    settings[evaluation.AGENT_FORWARD_STEP0_KEY] = _step0_block(config_id="0" * 64)

    with pytest.raises(register.Step0RefusedError):
        register.c_read_back(
            config,
            criteria,
            settings,
            code_commit=research_repo.head(repo),
            step0_registration=step0,
            repo=repo,
        )


def test_c_read_back_refuses_a_salt_tied_to_the_step_zero_seed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    step0 = _committed_step_zero(repo)
    config, criteria, settings = register.c_registration_body(
        salt="2026-09-26", step0_registration=step0, power=_power(), anchor=VALID_ANCHOR
    )

    with pytest.raises(register.SaltRefusedError):
        register.c_read_back(
            config,
            criteria,
            settings,
            code_commit=research_repo.head(repo),
            step0_registration=step0,
            repo=repo,
        )


def test_c_read_back_refuses_criteria_that_claim_a_verdict(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    step0 = _committed_step_zero(repo)
    config, criteria, settings = register.c_registration_body(
        salt=SALT, step0_registration=step0, power=_power(), anchor=VALID_ANCHOR
    )
    criteria["wording"] = "已验证"

    with pytest.raises(register.CriteriaRefusedError):
        register.c_read_back(
            config,
            criteria,
            settings,
            code_commit=research_repo.head(repo),
            step0_registration=step0,
            repo=repo,
        )


def test_c_read_back_refuses_a_primary_size_that_does_not_match_the_power_decision(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    step0 = _committed_step_zero(repo)
    config, criteria, settings = register.c_registration_body(
        salt=SALT,
        step0_registration=step0,
        power=_power(sample="census", measurable_sessions=118, detectable_mean=0.02),
        anchor=VALID_ANCHOR,
    )
    config["primary_size"] = register.PRIMARY_SIZE

    with pytest.raises(register.RegistrationWriterError):
        register.c_read_back(
            config,
            criteria,
            settings,
            code_commit=research_repo.head(repo),
            step0_registration=step0,
            repo=repo,
        )
