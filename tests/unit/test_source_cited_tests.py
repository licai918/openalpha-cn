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

## The commoner form is a backticked name with no path at all (`V2-P5-040`)

Path-qualified citations are the minority. The form this repository actually writes most often is
a bare backticked identifier -- ``the defect `test_the_refused_merge_leaves_the_partition_exactly_
as_it_found_it` caught`` -- and for as long as this audit read only the path-qualified form, **it
was checking 60-odd citations and ignoring 109**. That is the same shape as the defect it was
written to catch: a correspondence maintained by hand, audited on the instances someone happened
to spell one way. Two of those 109 were already stale when the second form was added.

A bare name has no path to resolve, so it is held against **every** member name declared anywhere
under `tests/`. That is weaker than the path-qualified check -- a citation naming a real test in
the wrong module still resolves -- and it is the strongest thing available without a path.

`test_day_count`, `test_set` and `test_sections` are the reason the rule is not simply "a
backticked identifier beginning with `test_`". They are fields of shipped models
(`backtest/walk_forward.py`, `domain/alpha_model.py`), and prose naming a field is naming a field.
So a bare name that is **declared in `src/` itself** is not read as a citation. Both sides of that
are derived from the tree; neither is a list anyone maintains, which is the property this file
exists to have.
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

BARE_CITATION: Final[re.Pattern[str]] = re.compile(r"`(test_[A-Za-z0-9_]*)`")
"""A backticked identifier beginning with `test_` and carrying no path.

Backticks and not bare words, because `test_` is an ordinary English-plus-underscore prefix and
unquoted prose about "the test set" should not be parsed as a citation. A name wrapped across a
line loses its closing backtick on the first fragment and is therefore not extracted at all --
the same conservative choice `CONTINUATION` makes, and for the same reason: a rule that guessed
at the join would resolve some citations against prose and hide the rest.
"""


def _source_symbols() -> set[str]:
    """Every name `src/openalpha_cn` declares -- functions, classes, fields, arguments.

    Subtracted from the bare citations before they are checked. A backticked `test_day_count` in
    `backtest/walk_forward.py` is naming that module's own field, and prose naming a field is not
    a citation that could resolve against the test tree.

    Function, class and assignment targets only. An earlier version also collected `ast.arg`,
    and a mutation sweep showed that branch was dead: all three real cases (`test_day_count`,
    `test_set`, `test_sections`) are *fields*, and no bare citation in the package resolves to a
    parameter name and nothing else. It is gone rather than kept as a widening nothing needs.
    """
    symbols: set[str] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                symbols.add(node.name)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                symbols.add(node.target.id)
            elif isinstance(node, ast.Assign):
                symbols.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return symbols


def _bare_citations() -> list[tuple[Path, str]]:
    """Every `(source file, cited name)` written without a path.

    The path-qualified form is **not** stripped first, and that is measured rather than assumed:
    the two patterns cannot both match one span, because a path-qualified name is preceded by
    the `:` of its `::` and `BARE_CITATION` requires a backtick there. Over the whole package,
    stripping changes the extraction by zero occurrences, so the strip would be a step that
    looks like it is doing something and is not --
    `test_the_two_citation_forms_cannot_both_match_one_span` holds that reason down.
    """
    declared_in_source = _source_symbols()
    found: list[tuple[Path, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in BARE_CITATION.finditer(text):
            if match.group(1) not in declared_in_source:
                found.append((path, match.group(1)))
    return found


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


def test_every_bare_test_name_a_source_docstring_cites_exists_somewhere_under_tests() -> None:
    """The other 109 citations, which this audit read past for as long as it read only paths.

    Weaker than the path-qualified audit by exactly one thing -- it cannot tell a test that moved
    from one that did not -- and stronger than nothing by exactly the thing that matters: a
    citation naming an identifier that no longer exists anywhere is a sentence pointing at a
    guarantee no test measures. Both of the two it found on the day it was written were that.
    """
    declared = set().union(*_cited_names().values())
    unresolved = sorted(
        f"{source.relative_to(REPO_ROOT)} cites {name}"
        for source, name in _bare_citations()
        if name not in declared
    )

    assert unresolved == [], (
        "a source docstring cites a test name that exists nowhere under tests/; correct the "
        "name, and if the sentence is naming a field of this module rather than a test, it is "
        "the field that needs renaming out of the `test_` prefix"
    )


def test_the_bare_audit_is_not_vacuous_and_does_not_read_fields_as_citations() -> None:
    """Three properties, each of which failing would make the audit above green and empty.

    The third is the one with a fixture rather than a count: `test_day_count` is a real field of
    a shipped model and a real backticked mention in the same repository, so a rule that dropped
    the `src/` subtraction would report it as a stale citation and the audit would be red for a
    sentence that is correct.
    """
    citations = _bare_citations()
    names = {name for _, name in citations}

    assert len(citations) > 100
    assert "test_three_axes_are_one_family_and_not_three" in names
    assert "test_day_count" in _source_symbols()
    assert "test_day_count" not in names


def test_the_two_citation_forms_cannot_both_match_one_span() -> None:
    """Why `_bare_citations` does not strip the path-qualified form: there is nothing to strip.

    The first version of this file stripped it, on the reasoning that a `tests/x.py::test_y`
    would otherwise be audited twice -- once properly and once under the weaker rule, which would
    let a citation naming a real test in the wrong module resolve. **Measured over the whole
    package, the strip removes zero occurrences**, and this is the reason: a path-qualified name
    is preceded by the second `:` of its `::`, and `BARE_CITATION` requires a backtick there.

    Both wrappings are driven, because the interesting one is the wrapped citation -- an
    inline-code span whose *contents* begin `tests/` and whose closing backtick sits immediately
    after the identifier, which is the nearest thing in the tree to a span the bare pattern could
    mistake for its own.

    The property is per occurrence and not per name, and asserting the latter is what the first
    version of this test got wrong:
    `test_the_engines_period_selection_is_the_domains_filing_for` is written path-qualified at
    `domain/factor.py:673` and bare at `panel_factors.py:4926`, so it is legitimately in both
    sets and there is no name-level partition to assert.
    """
    inline = "see `tests/unit/test_cli.py::test_a_name` for this"
    wrapped = "see `tests/unit/test_cli.py::\n    test_a_name` for this"

    assert BARE_CITATION.findall(inline) == []
    assert BARE_CITATION.findall(wrapped) == []
    assert BARE_CITATION.findall("see `test_a_name` for this") == ["test_a_name"]
    assert CITATION.findall(CONTINUATION.sub("::", wrapped)) == ["test_a_name"]


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
