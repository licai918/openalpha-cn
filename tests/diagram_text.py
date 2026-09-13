"""The words a diagram generator draws, read back from its source for the tests that hold prose.

`README.md` embeds ten SVG diagrams. `scripts/generate_brain_diagrams.py` and
`scripts/generate_api_relationship_diagrams.py` write them, and
`tests/unit/test_repository_assets.py::test_the_committed_diagrams_are_what_their_generators_write`
holds every committed SVG byte-equal to what its generator writes. A generator's string literals
are therefore the diagrams' words. This module reads them from the generator's syntax tree, never
by running it, at two grains:

- `diagram_strings` returns each string literal on its own: one line of a panel, a pill, a label.
  A range and the word beside it (a worker range and 并发, or CONCURRENCY) sit in one literal.
- `diagram_units` returns what one drawing call, or one row of a data table, draws together --
  a panel's title, label and lines -- joined with "，" and split by `prose_clauses.clauses`, so a
  sentence end inside a subtitle still ends a clause. A panel's lines are judged with its title:
  in brain-03, "模型治理边界" is the only word in its box that names a model.

A unit is every string argument of one call, searched through tuples and lists but never into a
nested call, which is a unit of its own; and every tuple literal that is no call's argument and
sits inside no such tuple, such as one row of the table a loop later draws.

What neither can see: text computed at run time -- an f-string's formatted values, a string built
from pieces -- and the grouping a loop gives rows it draws into one panel. A module, class or
function docstring is never read: it is not drawn.
"""

from __future__ import annotations

import ast
from typing import Final

from prose_clauses import Clause, clauses

UNIT_JOINER: Final[str] = "，"
"""What joins a unit's strings: a comma, which never ends a clause."""


def _docstring_ids(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def diagram_strings(source: str) -> list[Clause]:
    """Every string literal a generator's source holds, one each, with its line; no docstring."""
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    return sorted(
        (
            Clause(node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ),
        key=lambda clause: clause.line,
    )


def _strings_in(node: ast.AST) -> list[str]:
    """The string literals in `node`, through tuples and lists, never into a call."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List):
        return [string for element in node.elts for string in _strings_in(element)]
    return []


def diagram_units(source: str) -> list[Clause]:
    """What each drawing call or data row draws together, as clauses, each with its first line."""
    tree = ast.parse(source)
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    in_a_call: set[int] = set()
    units: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            for argument in arguments:
                in_a_call.update(id(inner) for inner in ast.walk(argument))
            strings = [string for argument in arguments for string in _strings_in(argument)]
            if strings:
                units.append((node.lineno, strings))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Tuple)
            and id(node) not in in_a_call
            and not isinstance(parents.get(id(node)), ast.Tuple)
        ):
            strings = _strings_in(node)
            if strings:
                units.append((node.lineno, strings))
    return [
        Clause(line, clause.text)
        for line, strings in sorted(units)
        for clause in clauses(UNIT_JOINER.join(strings))
    ]
