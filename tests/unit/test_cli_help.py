"""`tests/cli_help.py`, guarded the way `test_panel_fixtures.py` guards its own helper.

The defect this helper closes (`V2-P5-057`) was invisible for as long as it was: five assertions
about `--help` passed on every developer's machine and had never run anywhere that colours the
output. So the first test below does not check that the helper strips *something* -- it renders
the same command twice, once with colour forced on, and holds the two answers equal. That is the
property the callers actually depend on, and it is the one the environment can take away.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest
from cli_help import ANSI_ESCAPE, rendered_help
from typer.testing import CliRunner

from openalpha_cn.cli import app

TESTS_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
HELPER_MODULE: Final[Path] = TESTS_ROOT / "cli_help.py"

PROBE: Final[tuple[str, ...]] = ("factor", "run")
"""A command whose `--help` carries a wrapped option table, so the box rule is exercised."""


def test_the_rendering_is_the_same_whether_or_not_the_terminal_takes_colour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`V2-P5-057` itself: the two answers that differed on CI and nowhere else.

    `FORCE_COLOR` is what CI sets and a developer's shell does not, and it is read by Rich when
    the console is built rather than at import, so setting it here reaches the same code path.
    """
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    plain = rendered_help(*PROBE)

    monkeypatch.setenv("FORCE_COLOR", "1")
    coloured_raw = CliRunner().invoke(app, [*PROBE, "--help"], env={"COLUMNS": "200"}).output
    coloured = rendered_help(*PROBE)

    assert "\x1b" in coloured_raw, (
        "this test's premise is that FORCE_COLOR makes Rich emit escapes; it did not, so the "
        "equality below would hold for the wrong reason"
    )
    assert "\x1b" not in coloured
    assert coloured == plain


def test_the_option_tables_box_rule_does_not_survive_into_the_text() -> None:
    """The other half of "undo the presentation", and the half that predates this issue."""
    assert "│" not in rendered_help(*PROBE)


def test_a_cursor_sequence_is_removed_and_not_only_a_colour_one() -> None:
    """`ANSI_ESCAPE` is deliberately wider than the sequences Rich emits today.

    Asserted directly on the pattern because no `--help` in this repository emits one: the point
    of the wider pattern is the sequence nobody expected, and a test that could only use today's
    output would be asserting the narrower claim.
    """
    assert ANSI_ESCAPE.sub("", "a\x1b[2Kb\x1b[1;33mc\x1b[0m") == "abc"


def test_a_help_that_did_not_exit_zero_is_refused_rather_than_matched_against() -> None:
    """A caller that matched a sentence against an empty output would pass for the wrong reason."""
    with pytest.raises(AssertionError, match="no-such-command"):
        rendered_help("no-such-command")


def _modules_spelling_the_help_flag() -> set[Path]:
    """Every collected test module with `"--help"` as a string literal of its own."""
    found: set[Path] = set()
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path == HELPER_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "--help":
                found.add(path.relative_to(TESTS_ROOT))
    return found


def test_no_other_test_module_renders_help_for_itself() -> None:
    """What stops the seventh spelling being written (`test_run_mode.py`'s shape, here).

    Six modules had six ways of undoing Rich's rendering and none of them had ANSI. Holding the
    literal to one module is what makes an added caller inherit the fix rather than reintroduce
    the defect, and it reads the source tree rather than a hand-kept list for the same reason
    that audit gives: a list is what goes stale.

    `test_cli_help.py` itself is the one exemption, and it is granted by *this* file being the
    only other place that may spell the flag -- it has to, to render the coloured comparison
    above without going through the helper it is testing.
    """
    assert _modules_spelling_the_help_flag() == {Path("unit") / "test_cli_help.py"}


def test_the_audit_above_would_see_a_new_caller(tmp_path: Path) -> None:
    """The audit, on a module it should reject -- because `tests/` no longer contains one."""
    intruder = tmp_path / "test_intruder.py"
    intruder.write_text('x = runner.invoke(app, ["panel", "build", "--help"])\n', encoding="utf-8")
    tree = ast.parse(intruder.read_text(encoding="utf-8"), filename=str(intruder))

    assert any(isinstance(node, ast.Constant) and node.value == "--help" for node in ast.walk(tree))


def test_the_helper_collapses_nothing_so_a_caller_may_choose_its_own_join() -> None:
    """Why `rendered_help` returns wrapped text rather than a single line.

    `test_model_interfaces.py` matches a limitation code with `"".join(...)` and its neighbours
    match prose with `" ".join(...)`. A helper that collapsed for everybody would have made one
    of those two impossible, so the whitespace it leaves behind is a contract, not an oversight.
    """
    rendered = rendered_help(*PROBE)

    assert "\n" in rendered
    assert re.sub(r"\s+", " ", rendered) != rendered
