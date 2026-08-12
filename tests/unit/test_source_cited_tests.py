"""Every `tests/…::name` a source docstring cites, held against the tests that exist.

This repository documents its guarantees by naming the test that measures each one. That is a
table, and this repository's own lesson about tables is that only a run of them catches drift:
`panel_factors._refuse_table_drift`, `test_the_contract_and_the_store_agree_on_every_rejected_
dataset` and `test_the_registry_table_is_every_known_registry_in_the_source_tree` all exist
because a hand-maintained correspondence went stale while everything stayed green.

Citations had no such check and had already gone stale twice by the time this was written -- once
in the change that added the report-period axis (a citation naming `test_factor_engine.py` for a
test that lives in `test_factor_report_periods.py`) and once before it
(`panel_neutralization.py` naming `test_every_determinant_of_this_neutralisation_is_either_in_
the_identity_or_exempted_by_name`, whose real name has no `_of_this_neutralisation` in it). Both
were found by a reviewer reading them one at a time, which is the method this file replaces.

## What a citation has to look like to be checkable

A citation is `tests/<path>.py` optionally followed by `::<name>`, and the audit resolves the
file against the tree and the name against that file's own AST. Prose wraps, so the one form the
extractor accepts across a line break is a break **immediately after the `::`**:

    `tests/integration/panel/test_factor_report_periods.py::
    test_the_engines_period_selection_is_the_domains_filing_for`

A break inside the identifier itself cannot be told from a citation of a shorter name followed by
a sentence, so it is not accepted -- and this is deliberate rather than a limitation worked
around: three of the four citations this audit first refused were wrapped that way, and one of
those three was *also* naming the wrong file. A rule that guessed at the join would have resolved
two of them and hidden the third.

A bare `tests/<path>.py` with no `::` is checked as a file and nothing more. Those are references
to a whole module ("`tests/unit/test_cli.py` covers this"), which is a different and weaker claim.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SOURCE_ROOT: Final[Path] = REPO_ROOT / "src" / "openalpha_cn"
TEST_ROOT: Final[Path] = REPO_ROOT / "tests"

CITATION: Final[re.Pattern[str]] = re.compile(r"tests/[\w./-]+\.py(?:::(\w+))?")
"""A test-file path, optionally naming one member of it.

`[\\w./-]` and not `\\S`, so a citation that runs into a closing backtick or a comma stops where
the path stops rather than swallowing punctuation into a filename that then cannot exist.
"""

CONTINUATION: Final[re.Pattern[str]] = re.compile(r"::\s*\n\s*")
"""The one line break a citation may contain: the one straight after `::`; see this module's
docstring for why no other is accepted."""


def _cited_names() -> dict[Path, set[str]]:
    """Every member name declared in every test module, keyed by path.

    Classes and functions both, because a citation may name a helper (`test_factor_neutralization
    _rules.py::_dense_residuals`) or a fixture rather than a test. What is being audited is that
    the thing named exists, not that it is a test.
    """
    declared: dict[Path, set[str]] = {}
    for path in sorted(TEST_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared[path] = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
    return declared


def _citations() -> list[tuple[Path, str, str | None]]:
    """Every `(source file, cited path, cited name)` in the package, line breaks rejoined."""
    found: list[tuple[Path, str, str | None]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = CONTINUATION.sub("::", path.read_text(encoding="utf-8"))
        for match in CITATION.finditer(text):
            found.append((path, match.group(0).split("::")[0], match.group(1)))
    return found


def test_every_test_a_source_docstring_cites_exists_under_that_name() -> None:
    """The audit. A citation that does not resolve is a sentence pointing at nothing.

    The failure lists the unresolved citations with the file each was written in, because a count
    tells a reader nothing about which guarantee stopped being documented -- and because the two
    repairs are different: a moved test needs its citation's path corrected, and a renamed one
    needs its name corrected.
    """
    declared = _cited_names()
    unresolved = [
        f"{source.relative_to(REPO_ROOT)} cites {cited}" + (f"::{name}" if name is not None else "")
        for source, cited, name in _citations()
        if REPO_ROOT / cited not in declared
        or (name is not None and name not in declared[REPO_ROOT / cited])
    ]

    assert unresolved == [], (
        "a source docstring cites a test that does not exist under that name; correct the "
        "citation, and if it wraps, break the line straight after the `::`"
    )


def test_the_audit_is_not_vacuous_and_covers_the_wrapped_form() -> None:
    """The extractor's own test, without which the audit above could pass by finding nothing.

    Three properties, each of which failing would make the audit green and empty: it finds
    citations at all, it finds them across the wrapped form (the majority of the real ones), and
    it resolves a name it finds rather than only a path. Asserted against citations this
    repository actually carries, so the fixture cannot drift away from the thing being extracted.
    """
    citations = _citations()
    resolved = {(cited, name) for _, cited, name in citations}

    assert len(citations) > 50
    assert any(name is not None for _, _, name in citations)
    assert (
        "tests/integration/panel/test_factor_report_periods.py",
        "test_the_engines_period_selection_is_the_domains_filing_for",
    ) in resolved
    assert ("tests/unit/test_import_layering.py", None) in resolved


def test_a_citation_wrapped_inside_its_identifier_does_not_resolve() -> None:
    """The rule the module docstring states, driven rather than described.

    If `CONTINUATION` ever grew to rejoin a break inside the identifier, a citation naming a test
    that no longer exists would start resolving against whatever prose followed it -- which is
    precisely how the two stale citations that motivated this file survived review. The sentinel
    is built here rather than taken from the tree, because the tree is now clean of the form.
    """
    wrapped = "`tests/unit/test_source_cited_tests.py::test_the_audit_is_not_vacuous_and_\ncovers"
    (name,) = CITATION.findall(CONTINUATION.sub("::", wrapped))

    assert name == "test_the_audit_is_not_vacuous_and_"
    assert name not in _cited_names()[Path(__file__).resolve()]
