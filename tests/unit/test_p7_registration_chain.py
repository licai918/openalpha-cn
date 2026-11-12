"""The committed survivor registration chain (`V2-P7-006b`, U1): one root, every later link named.

The chain test is written before the registration exists, on purpose: it asserts on the real
repository's committed registrations, so the day the registration is committed it is covered
without editing this file (no `skip`, no empty parametrisation -- plan section 4, Task 3). Today
the set is empty, and an empty set is one rooted chain; the loop below never assumed otherwise.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Final

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
