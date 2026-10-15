"""`scripts/daily_selection.py`: a changed registration is another paper book (`V2-P7-008`,
the whole-branch seams review's finding 1).

The same configuration registered again -- a code fix under a bound path, a changed `settings` --
keeps its `config_id`, so its journals share the runtime directory's journal directory; before the
paper book's marker bound the registration file's sha256, the day went on writing the earlier
registration's book, after which every forward report of the directory was refused for good. The
marker binds it now (`strategy_survivor_book`): the day is refused at the paper head under the
paper book's lock, before its pin and any request, naming both registrations; the forward report,
unchanged, still refuses the earlier registration's journals under the new one. Whether a code fix
should instead carry a book across registrations is V2-P7-006's to decide.

The days run through `main` on `test_daily_selection_survivor_day.py`'s scripted market, seeded
once for this module.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as sd
from research_repo import commit_file, head

# The survivor day's seeded panel, built once for this module (pytest takes a fixture by name).
from test_daily_selection_survivor_day import seeded  # noqa: F401

daily = tds.daily
ROOT: Final[Path] = Path(__file__).resolve().parents[3]
DAY: Final[date] = sd.DAY
NEXT: Final[date] = sd.NEXT
NO_REQUEST: Final[str] = sd.NO_REQUEST


def _sha256(world: tds.World) -> str:
    return hashlib.sha256(world.registration.read_bytes()).hexdigest()


def _registered_again(world: tds.World) -> tds.World:
    """The world's configuration registered again after a code fix: a bound file changed and
    committed, then a second registration file of the same configuration written at the new code
    and committed -- another sha256, the same `config_id`, the same runtime directory."""
    repo = world.repo
    fixed = repo / "src" / "openalpha_cn" / "strategy.py"
    fixed.write_text("RULES = 2\n", encoding="utf-8")
    commit_file(repo, fixed, "a code fix", at=tds.COMMITTED + timedelta(days=1))
    registration = repo / "docs" / "research" / "survivor-registration-again.json"
    tds.registry.register(sd.CONFIG, tds.CRITERIA, registration, code_commit=head(repo))
    commit_file(repo, registration, "register again", at=tds.COMMITTED + timedelta(days=2))
    return dataclasses.replace(world, registration=registration)


# --- a changed registration is another book (finding 1) ------------------------------------------


def test_the_same_configuration_registered_again_is_refused_before_any_request_naming_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """6 April runs under the registration. A code fix is then registered again -- the same
    configuration, so the same `config_id` and journal directory -- and 7 April runs under it:
    refused at the paper head before the pin and any request, naming the book's registration and
    this one, and saying that the new one is another book with a runtime directory and an epoch of
    its own. Nothing is written. The forward report reads the directory as it did: under the new
    registration it still refuses the earlier one's journal."""
    world = sd._world(tmp_path, monkeypatch, seeded)
    sd._completed(world, capsys, DAY)
    again = _registered_again(world)
    first, second = _sha256(world), _sha256(again)
    assert first != second
    assert sd._config_id(again) == sd._config_id(world)
    before = sd._files(world.runtime)
    world.market.clear()

    code, out, err = sd._day(again, capsys, NEXT)

    assert code == 1 and out == ""
    assert err[0].startswith("step 5 (paper head) failed: this runtime directory's paper book")
    assert first in err[0] and second in err[0]
    assert err[0].index(first) < err[0].index(second)
    assert "another book" in err[0] and "runtime directory of its own" in err[0]
    assert "epoch of its own" in err[0]
    assert NO_REQUEST in err
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before
    assert not sd._journal_path(again, NEXT).exists()

    report = tds._script("forward_report", ROOT / "scripts")
    admitted = daily.admit_registration(again.registration, again.repo)
    with pytest.raises(report.SurvivorReportError, match="another registration"):
        report._survivor_journals(world.runtime, admitted)
