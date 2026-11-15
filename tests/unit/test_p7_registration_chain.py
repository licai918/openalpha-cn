"""The committed survivor registration chain (`V2-P7-006b`, U1): one root, every later link named.

The chain test is written before the registration exists, on purpose: it asserts on the real
repository's committed registrations, so the day the registration is committed it is covered
without editing this file (no `skip`, no empty parametrisation -- plan section 4, Task 3). Today
the set is empty, and an empty set is one rooted chain; the loop below never assumed otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_SCRIPTS: Final[Path] = ROOT / "scripts"
for _directory in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

import daily_selection  # noqa: E402
import grid  # noqa: E402
import p7_smoke_env  # noqa: E402
import registry  # noqa: E402
from p7_register import (  # noqa: E402
    chain_problems,
    committed_survivor_registrations,
    registration_body,
)

from openalpha_cn.domain.agent_forward import AGENT_LLM_FAMILY  # noqa: E402
from openalpha_cn.domain.survivor_veto import VETO_RULE_ID  # noqa: E402
from openalpha_cn.strategy_agent_evaluation import (  # noqa: E402
    ADOPTED_INTERIM_SESSION,
    ADOPTED_LEVEL,
    ADOPTED_MIN_MEASURABLE_SESSIONS,
    ADOPTED_MIN_SCORED,
    ADOPTED_Q,
    ADOPTED_WINDOW_SESSIONS,
    read_agent_forward_anchor,
    read_agent_forward_power,
    read_agent_forward_protocol_document,
    read_agent_forward_step0,
    read_agent_protocol,
)

_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_PARALLEL: Final[str] = daily_selection.SURVIVOR_PARALLEL_OF_KEY
"""The `settings` key of a parallel registration (`V2-P7-027`, R-N5), read from the daily command's
own spelling: the writer takes it from there, and so does this test."""


def _rebuilt_parallel_body(
    registrations: dict[str, dict[str, Any]], path: str, *, root: Path
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """`(config, criteria, settings)` rebuilt for `path`: its base's stored book as the parallel
    v2 book carrying `path`'s rule, its own epoch, and its own `survivor_parallel_of` block.

    The base is a committed member of the set and must not itself be a parallel registration; the
    sha256 the block records must be the committed base's bytes. A parallel registration whose
    body differs from this rebuild -- a hand-edited criteria, settings, adapter or book parameter
    -- is not the one the writer would have produced from that base."""
    body = registrations[path]
    block = body["settings"][_PARALLEL]
    base = block["path"]
    assert base in registrations, f"{path} names {base}, which is not a committed registration"
    base_body = registrations[base]
    assert _PARALLEL not in base_body["settings"], (
        f"{path} names {base}, which is itself a parallel registration; a parallel book runs "
        "beside a base registration, never beside another parallel book"
    )
    assert "survivor_continuation" not in body["settings"], path
    committed = _git_bytes(root, "show", f"HEAD:{base}")
    assert block["sha256"] == hashlib.sha256(committed).hexdigest(), path
    # The rule is read from the registration being checked, not hard-coded: a future R-N7 rule
    # beside the same base must rebuild from the body it actually stores.
    rule = body["config"]["survivor_book"]["entry_veto"]
    book = p7_smoke_env.veto_book(rule, base_body["config"]["survivor_book"])
    epoch = date.fromisoformat(body["config"]["adapter"]["epoch_session"])
    return registration_body(epoch, book=book, parallel_of=block)


def _assert_a_parallel_registration(
    registrations: dict[str, dict[str, Any]], path: str, *, root: Path
) -> None:
    """`path`'s whole body is the one rebuilt from the base it names (`V2-P7-027`, R-N5)."""
    body = registrations[path]
    if _PARALLEL not in body["settings"]:
        return
    config, criteria, settings = _rebuilt_parallel_body(registrations, path, root=root)
    assert body["config"] == config, path
    assert body["criteria"] == criteria, path
    assert body["settings"] == settings, path
    assert body["config_id"] == grid.config_id(config), path
    assert body["config_id"] != registrations[settings[_PARALLEL]["path"]]["config_id"], path


def test_every_committed_survivor_registration_is_frozen_and_chained() -> None:
    registrations = committed_survivor_registrations(ROOT)

    assert chain_problems(registrations) == []
    chain = {
        path: body for path, body in registrations.items() if _PARALLEL not in body["settings"]
    }
    roots = [
        path for path, body in chain.items() if "survivor_continuation" not in body["settings"]
    ]
    assert len(roots) <= 1
    for path, body in registrations.items():
        assert _FULL_COMMIT.fullmatch(body["code_commit"]), path
        assert len(body["config_id"]) == 64, path
    for path in roots:
        body = registrations[path]
        epoch = date.fromisoformat(body["config"]["adapter"]["epoch_session"])
        config, criteria, settings = registration_body(epoch)
        assert body["config"] == config, path
        assert body["criteria"] == criteria, path
        assert body["settings"] == settings, path
        assert body["config_id"] == grid.config_id(config), path
    # R-N5: a parallel registration is outside the chain, names a committed base registration
    # whose committed bytes are the sha256 it records, and its whole body is that base's rebuilt.
    for path in registrations:
        _assert_a_parallel_registration(registrations, path, root=ROOT)


def test_a_committed_parallel_registration_rebuilds_from_its_base(tmp_path: Path) -> None:
    """The parallel assertions above only run on real committed data, which does not exist yet.
    This commits a base and a parallel registration in a throwaway repository and runs them,
    so the check itself is pinned (a hand-edited parallel body fails here)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--template=", "-b", "main")
    (repo / "README.md").write_text("a registration repository\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    base = "docs/research/survivor.json"
    parallel = "docs/research/parallel.json"
    epoch = date(2026, 12, 18)
    config, criteria, settings = registration_body(epoch)
    base_path = repo / base
    base_path.parent.mkdir(parents=True)
    registry.register(
        config, criteria, base_path, code_commit=_git(repo, "rev-parse", "HEAD"), settings=settings
    )
    _git(repo, "add", "--", base)
    _git(repo, "commit", "-q", "-m", "the base registrarion")
    block = {
        "path": base,
        "sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
    }
    book = p7_smoke_env.veto_book(VETO_RULE_ID, config["survivor_book"])
    parallel_config, parallel_criteria, parallel_settings = registration_body(
        epoch, book=book, parallel_of=block
    )
    registry.register(
        parallel_config,
        parallel_criteria,
        repo / parallel,
        code_commit=_git(repo, "rev-parse", "HEAD"),
        settings=parallel_settings,
    )
    _git(repo, "add", "--", parallel)
    _git(repo, "commit", "-q", "-m", "the parallel registration")

    registrations = committed_survivor_registrations(repo)

    assert set(registrations) == {base, parallel}
    assert chain_problems(registrations) == []
    roots = [path for path, body in registrations.items() if _PARALLEL not in body["settings"]]
    assert roots == [base]
    for path in registrations:
        _assert_a_parallel_registration(registrations, path, root=repo)


PROTOCOL: Final[Path] = ROOT / "docs/research/p7-protocol.md"
"""The protocol document the registration is read against (plan section 3)."""


def test_the_protocol_states_the_frozen_numbers() -> None:
    assert PROTOCOL.is_file(), f"{PROTOCOL} is not written yet"
    text = PROTOCOL.read_text(encoding="utf-8")

    for token in (
        "0.02",
        "K = 36",
        "18",
        "20260926",
        "seeded_hash/v1",
        "350,000",
        "±2.9pp",
        "不是证据",
        "候选",
        "result.txt:23",
        "σ = 0.18",  # noqa: RUF001
        "49%",
        "推算，不是证据",
        "中期无效性停止规则",
    ):
        assert token in text, f"the protocol does not state {token!r}"
    for forbidden in ("已验证", "证实有效", "proven"):
        assert forbidden not in text, f"the protocol claims {forbidden!r}"


GIT: Final[tuple[str, ...]] = (
    "-c",
    "user.name=Audit",
    "-c",
    "user.email=audit@example.invalid",
    "-c",
    "commit.gpgsign=false",
)


def _git(repo: Path, *arguments: str) -> str:
    """git in `repo`, hermetic: no inherited `GIT_*`, no global or system configuration."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    finished = subprocess.run(
        ["git", *GIT, *arguments],
        cwd=repo,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout.strip()


def _git_bytes(repo: Path, *arguments: str) -> bytes:
    """git in `repo`, hermetic, with stdout left as the bytes `sha256` is taken over."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    finished = subprocess.run(
        ["git", *GIT, *arguments],
        cwd=repo,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr.decode(errors="replace")
    return finished.stdout


def _registration(**keywords: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"schema": "openalpha-research-registration/v1"}
    body.update(keywords)
    return body


def test_only_non_smoke_survivor_registrations_are_chain_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--template=", "-b", "main")
    files = {
        "docs/research/survivor.json": _registration(
            config={"kind": "survivor_book/v1"}, settings={}
        ),
        "docs/research/smoke.json": _registration(
            config={"kind": "survivor_book/v1"}, settings={"smoke_run": {"issue": "V2-P7-019"}}
        ),
        "docs/research/p6.json": _registration(config={"start": "2026-01-12"}, settings={}),
    }
    for relative, body in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "registrations")

    selected = committed_survivor_registrations(repo)

    assert set(selected) == {"docs/research/survivor.json"}
    assert selected["docs/research/survivor.json"]["config"]["kind"] == "survivor_book/v1"


# --- C's registration (`V2-P7-013` task 3.4): bound to Step 0, never a member of its chain ------

AGENT_FORWARD_KIND: Final[str] = "agent_forward/v1"
C_REGISTRATION: Final[Path] = ROOT / "docs/research/p7-c-registration.json"
C_REGISTRATION_RELATIVE: Final[str] = "docs/research/p7-c-registration.json"


def test_an_agent_forward_registration_is_not_a_survivor_chain_member(tmp_path: Path) -> None:
    """C's kind is filtered out structurally: it can never become a link of Step 0's chain."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--template=", "-b", "main")
    files = {
        "docs/research/survivor.json": _registration(
            config={"kind": "survivor_book/v1"}, settings={}
        ),
        "docs/research/p7-c-registration.json": _registration(
            config={"kind": AGENT_FORWARD_KIND},
            settings={"random_seed": 20_260_926},
        ),
    }
    for relative, body in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "registrations")

    selected = committed_survivor_registrations(repo)

    assert set(selected) == {"docs/research/survivor.json"}
    assert chain_problems(selected) == []


def _real_c_registration() -> dict[str, Any]:
    """C's registration, or a skip: it is the user's L5 commit, made after this batch."""
    if not C_REGISTRATION.is_file():
        pytest.skip(
            "C's registration is not written yet (it is the user's L5 commit, "
            "docs/research/p7-c-registration.json)"
        )
    body: dict[str, Any] = json.loads(C_REGISTRATION.read_text(encoding="utf-8"))
    return body


def test_the_real_c_registration_is_not_a_survivor_chain_member() -> None:
    document = _real_c_registration()

    assert document["config"]["kind"] == AGENT_FORWARD_KIND
    committed = committed_survivor_registrations(ROOT)
    assert C_REGISTRATION_RELATIVE not in committed
    assert chain_problems(committed) == []


def test_the_real_c_registration_freezes_the_adopted_protocol() -> None:
    """D2/D3/D5's values are the evaluator's adopted constants, read through its own readers."""
    settings = _real_c_registration()["settings"]

    protocol = read_agent_protocol(settings)
    assert protocol.primary_family == AGENT_LLM_FAMILY
    assert protocol.primary_set == "primary"
    assert protocol.primary_horizon == 1
    assert protocol.window_sessions == ADOPTED_WINDOW_SESSIONS
    assert protocol.interim_session == ADOPTED_INTERIM_SESSION
    assert protocol.min_scored == ADOPTED_MIN_SCORED
    assert protocol.min_measurable_sessions == ADOPTED_MIN_MEASURABLE_SESSIONS
    assert protocol.level == ADOPTED_LEVEL
    assert protocol.q == ADOPTED_Q

    step0 = read_agent_forward_step0(settings)
    read_agent_forward_power(settings)
    anchor = read_agent_forward_anchor(settings)
    assert (ROOT / step0.path).is_file(), step0.path
    assert anchor.session.weekday() < 5, anchor.session


def test_the_real_c_registrations_power_block_agrees_with_the_body() -> None:
    """D1's frozen choice is one the writer's body rules could have made (D1's own branches)."""
    document = _real_c_registration()
    settings = document["settings"]
    config = document["config"]

    power = read_agent_forward_power(settings)
    if power.sample == "n300":
        assert config["primary_size"] == 300
    else:
        assert config["primary_size"] == 10**6
    assert power.register_untestable == (power.verdict == "not_testable")
    if power.sample == "census":
        assert power.verdict == "testable"
        assert power.register_untestable is False


PROTOCOL_DOCUMENT: Final[str] = "docs/research/p7-c-protocol.md"


def test_the_real_c_registration_freezes_the_committed_protocol_document() -> None:
    """F7: the registration names the committed protocol text by path and sha256."""
    settings = _real_c_registration()["settings"]

    block = read_agent_forward_protocol_document(settings)
    assert block.path == PROTOCOL_DOCUMENT
    committed = _git_bytes(ROOT, "show", f"HEAD:{PROTOCOL_DOCUMENT}")
    assert block.sha256 == hashlib.sha256(committed).hexdigest()
    assert (ROOT / PROTOCOL_DOCUMENT).read_bytes() == committed


def test_a_parallel_registration_whose_base_is_another_parallel_registration_is_refused(
    tmp_path: Path,
) -> None:
    """R-N5's negative half: a parallel registration runs beside a **base** registration, never
    beside another parallel book.

    The writer-side refusal (a parallel body's base read back) is tested elsewhere; this pins the
    chain-check helper's own assertion, which a committed chain will exercise: a root, then a
    parallel registration P naming it, then a parallel registration Q naming P. Q is a
    registration `committed_survivor_registrations` will list, and `_rebuilt_parallel_body` must
    refuse it by name -- Q's base is itself a parallel registration, so there is no base book for
    Q's body to have been rebuilt from.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--template=", "-b", "main")
    (repo / "README.md").write_text("a registration repository\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    root = "docs/research/survivor.json"
    first_parallel = "docs/research/parallel-one.json"
    second_parallel = "docs/research/parallel-two.json"
    epoch = date(2026, 12, 18)

    config, criteria, settings = registration_body(epoch)
    root_path = repo / root
    root_path.parent.mkdir(parents=True)
    registry.register(
        config,
        criteria,
        root_path,
        code_commit=_git(repo, "rev-parse", "HEAD"),
        settings=settings,
    )
    _git(repo, "add", "--", root)
    _git(repo, "commit", "-q", "-m", "the base registration")

    def commit_parallel(relative: str, base: str) -> None:
        """A parallel registration at `relative`, its block naming the committed `base`."""
        base_path = repo / base
        base_body = json.loads(base_path.read_text(encoding="utf-8"))
        block = {
            "path": base,
            "sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
        }
        book = p7_smoke_env.veto_book(VETO_RULE_ID, base_body["config"]["survivor_book"])
        parallel_config, parallel_criteria, parallel_settings = registration_body(
            epoch, book=book, parallel_of=block
        )
        registry.register(
            parallel_config,
            parallel_criteria,
            repo / relative,
            code_commit=_git(repo, "rev-parse", "HEAD"),
            settings=parallel_settings,
        )
        _git(repo, "add", "--", relative)
        _git(repo, "commit", "-q", "-m", f"the parallel registration {relative}")

    commit_parallel(first_parallel, root)
    commit_parallel(second_parallel, first_parallel)

    registrations = committed_survivor_registrations(repo)

    assert set(registrations) == {root, first_parallel, second_parallel}
    assert registrations[second_parallel]["settings"][_PARALLEL]["path"] == first_parallel
    with pytest.raises(AssertionError, match="itself a parallel registration"):
        _rebuilt_parallel_body(registrations, second_parallel, root=repo)
