"""The committed survivor registration chain (`V2-P7-006b`, U1): one root, every later link named.

The chain test is written before the registration exists, on purpose: it asserts on the real
repository's committed registrations, so the day the registration is committed it is covered
without editing this file (no `skip`, no empty parametrisation -- plan section 4, Task 3). Today
the set is empty, and an empty set is one rooted chain; the loop below never assumed otherwise.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_SCRIPTS: Final[Path] = ROOT / "scripts"
for _directory in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

import grid  # noqa: E402
from p7_register import (  # noqa: E402
    chain_problems,
    committed_survivor_registrations,
    registration_body,
)

_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")


def test_every_committed_survivor_registration_is_frozen_and_chained() -> None:
    registrations = committed_survivor_registrations(ROOT)

    assert chain_problems(registrations) == []
    roots = [
        path
        for path, body in registrations.items()
        if "survivor_continuation" not in body["settings"]
    ]
    assert len(roots) <= 1
    for path, body in registrations.items():
        assert _FULL_COMMIT.fullmatch(body["code_commit"]), path
        assert len(body["config_id"]) == 64, path
        if path not in roots:
            continue
        epoch = date.fromisoformat(body["config"]["adapter"]["epoch_session"])
        config, criteria, settings = registration_body(epoch)
        assert body["config"] == config, path
        assert body["criteria"] == criteria, path
        assert body["settings"] == settings, path
        assert body["config_id"] == grid.config_id(config), path


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
