"""`scripts/daily_selection.py`: a survivor day's order across sessions (`V2-P7-008`, the
whole-branch seams review's findings 3 and 8).

- **An evening committed and not documented is completed first** (finding 3). An evening whose
  snapshot was saved and whose journal's `result` was not -- the known window between the two --
  can be completed by a rerun only until the next session's run moves the book past it; after
  that its plan and its decision are documented nowhere. So the next session's run refuses, by
  name, under the paper book's lock, before its pin and any request, with the command that
  completes the evening.
- **A committed day completed at a later read says why its paper book can refuse** (finding 8).
  Read after the next session's update, a committed day's facts are read at a later instant, at
  which a name-change announcement dated on or before the session but fetched after the evening
  is visible; the paper book then refuses a derivation that differs from what it committed. The
  refusal names that cause.

The days run through `main` on `test_daily_selection_survivor_day.py`'s scripted market, seeded
once for this module.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as sd

# The survivor day's seeded panel, built once for this module (pytest takes a fixture by name).
from test_daily_selection_survivor_day import seeded  # noqa: F401

from openalpha_cn import strategy_survivor_book
from openalpha_cn.panel_ingest import session_publication_instant

daily = tds.daily
DAY: Final[date] = sd.DAY
NEXT: Final[date] = sd.NEXT
THIRD: Final[date] = sd.THIRD
NO_REQUEST: Final[str] = sd.NO_REQUEST


# --- an evening committed and not documented is completed first (finding 3) ----------------------


def _printed_command(lines: Sequence[str]) -> list[str]:
    """The daily command a refusal prints on a line of its own, as the arguments `main` takes:
    the first one -- the refusal's own, "rerun <D> first" -- since the block that follows the
    requests line (what the refusal means for the user) prints a rerun command of the current
    day too."""
    line = next(line for line in lines if "scripts/daily_selection.py" in line)
    words = shlex.split(line)
    return words[words.index("scripts/daily_selection.py") + 1 :]


@pytest.mark.parametrize(
    ("cut", "planned"),
    [
        pytest.param(DAY, True, id="an evening that planned ten buys"),
        pytest.param(NEXT, False, id="an evening that planned nothing"),
    ],
)
def test_a_day_after_an_evening_committed_without_its_result_is_refused_until_it_is_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    cut: date,
    planned: bool,
) -> None:
    """An evening commits and the power is cut as its journal's `result` is written: 6 April's,
    which planned ten buys, or 7 April's, which planned nothing (its snapshot is the one a
    catch-up would leave, and no later run has reached the book). The next session's run is
    refused by name before its pin and before any request: rerun that evening first, with the
    exact command -- the day's own options and the evening's pinned `as_of`. That command, run as
    printed, completes the evening, writing nothing to the paper book; then the next session
    runs."""
    world = sd._world(tmp_path, monkeypatch, seeded)
    if cut == NEXT:
        sd._completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        sd._cut_at_the_result(patch)
        sd._day(world, capsys, cut)
    journal = sd._journal(world, cut)
    assert "result" not in journal and "prediction" in journal
    snapshot = sd._snapshot(world, cut)
    assert strategy_survivor_book.decided_by_an_evening(snapshot) is planned
    after = cut + timedelta(days=1)
    before = sd._files(world.runtime)
    world.market.clear()

    code, out, err = sd._day(world, capsys, after)

    assert code == 1 and out == ""
    assert err[0].startswith("step 5 (paper head) failed:")
    assert (
        f"rerun {cut.isoformat()} first: its evening is committed but its journal is "
        "incomplete" in err[0]
    )
    assert snapshot.snapshot_id in err[0]
    pinned = datetime.fromisoformat(journal["as_of"])
    command = _printed_command(err)
    assert command[command.index("--as-of") + 1] == pinned.astimezone(daily.SHANGHAI).isoformat()
    assert command[command.index("--runtime-dir") + 1] == str(world.runtime)
    assert command[command.index("--registration") + 1] == str(world.registration)
    assert "then rerun this command" in err
    assert NO_REQUEST in err
    assert world.market.payloads == []
    assert sd._files(world.runtime) == before
    assert not sd._journal_path(world, after).exists()
    paper = sd._files(world.runtime / "paper")

    late = sd.evening_of(after) + timedelta(minutes=1)
    code = daily.main([*command, "--json"], clock=lambda: late)

    completed, err_text = capsys.readouterr()
    assert code == 0, err_text
    assert '"outcome": "unchanged"' in completed
    assert "result" in sd._journal(world, cut)
    assert sd._files(world.runtime / "paper") == paper

    code, _out, err = sd._day(world, capsys, after)

    assert code == 0, err


def test_an_evening_that_planned_nothing_is_left_to_the_later_run_whose_journal_is_there(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """7 April's evening planned nothing (its book held what it bought on 6 April), so its
    snapshot is the one a catch-up of it would leave. With a journal of 8 April there -- a later
    run that reached the book, whose own rerun is the remedy -- the snapshot is not taken for an
    undocumented evening, and 9 April is not refused by this check."""
    world = sd._world(tmp_path, monkeypatch, seeded)
    sd._completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        sd._cut_at_the_result(patch)
        sd._day(world, capsys, NEXT)
    code, _out, err = sd._day(world, capsys, THIRD)
    assert code == 1 and f"rerun {NEXT.isoformat()} first" in err[0], err
    third = sd._journal_path(world, THIRD)
    third.write_text(
        sd._journal_path(world, NEXT)
        .read_text(encoding="utf-8")
        .replace(NEXT.isoformat(), THIRD.isoformat()),
        encoding="utf-8",
    )

    code, _out, err = sd._day(world, capsys, sd.FOURTH)

    assert not any("first: its evening is committed" in line for line in err), err


def test_an_evening_completed_and_documented_lets_the_next_session_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """The check reads the book's head and that session's journal only: a documented evening, and
    the epoch's opening before the first evening, refuse nothing."""
    world = sd._world(tmp_path, monkeypatch, seeded)

    sd._completed(world, capsys, DAY)
    sd._completed(world, capsys, NEXT)


# --- a committed day completed at a later read (finding 8) ---------------------------------------


def test_a_committed_day_completed_at_a_later_read_names_why_its_paper_book_can_refuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """7 April's evening is committed without its `result`, and 8 April's panel is stored since.
    The rerun of 7 April reads its facts at 8 April's publication, not at its pinned clock; the
    paper book refuses its derivation (here by injection). The refusal keeps its first words and
    names the cause: a later read sees a name change dated on or before 7 April that was fetched
    after its evening, and the book refuses a derivation that differs from what it committed."""
    world = sd._world(tmp_path, monkeypatch, seeded)
    sd._completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        sd._cut_at_the_result(patch)
        sd._day(world, capsys, NEXT)
    sd._panel_updated_for(world, THIRD)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "evening", sd._refused_evening)
        code, _out, err = sd._day(world, capsys, NEXT, clock=sd.after_the_auction(THIRD))

    assert code == 1
    assert err[0].startswith(sd.INJECTED)
    later = session_publication_instant(THIRD).astimezone(daily.SHANGHAI).isoformat()
    assert f"read at {later}" in err[0]
    assert f"dated on or before {NEXT.isoformat()}" in err[0]
    assert "fetched after the evening" in err[0]
    assert "differs from what it committed" in err[0]


def test_a_refusal_of_a_day_read_at_its_own_clock_names_no_later_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    world = sd._world(tmp_path, monkeypatch, seeded)
    sd._completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        sd._cut_at_the_result(patch)
        sd._day(world, capsys, NEXT)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "evening", sd._refused_evening)
        code, _out, err = sd._day(world, capsys, NEXT, clock=sd.after_the_auction(NEXT))

    assert code == 1
    assert err[0] == sd.INJECTED
