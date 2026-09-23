"""The words a diagram generator draws, read back from its source for the tests that hold prose.

`README.md` embeds ten SVG diagrams. `scripts/generate_brain_diagrams.py` and
`scripts/generate_api_relationship_diagrams.py` write them, and
`tests/unit/test_repository_assets.py::test_the_committed_diagrams_are_what_their_generators_write`
holds every committed SVG byte-equal to what its generator writes. A generator's string literals
are therefore the diagrams' words. This module reads them from the generator's syntax tree, never
by running it, at two grains:

- `diagram_strings` returns each string literal on its own: one line of a panel, a pill, a label,
  a literal part of an f-string. A range and the word beside it (a worker range and 并发, or
  CONCURRENCY) sit in one literal.
- `diagram_units` returns what one drawing call, or one row of a data table, draws together --
  a panel's title, label and lines -- joined with "，" and split by `prose_clauses.clauses`, so a
  sentence end inside a subtitle still ends a clause. A panel's lines are judged with its title,
  so a line that names no model is read beside a title that does.

A call's unit is the text of its arguments, read through tuples and lists, an f-string's literal
parts (joined, its formatted values left out), both branches of a conditional expression, and the
template and literal arguments of a `.format` call on a literal. Any other nested call is a unit
of its own, and so is a `.format` call. A tuple literal that no call takes is a unit, and so is
each tuple in a list literal that no call takes, one row of a table a loop later draws; a tuple
whose elements are all tuples or lists is such a table too, and each of its rows is a unit.

Both readers take the generator's path as `filename`, which `ast.parse` writes into any
SyntaxError or warning the source raises. The tests read the two generators one after the other,
so a report without it said `<unknown>` and hid which generator it came from. A caller that
leaves `filename` out still gets `<unknown>`.

What `diagram_units` cannot see: an f-string's formatted values; a string built with `+` or `%`;
a name, even one bound to a literal, because the generators pass their colours as module-level
names and reading those would put a colour into every panel's text; an attribute or a subscript;
a list's elements that are not tuples, drawn one by one in a loop; and the grouping a loop gives
the rows it draws into one panel. `diagram_strings` reads every literal in all of these, each
alone. A module, class or function docstring is never read: it is not drawn.
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


def diagram_strings(source: str, *, filename: str = "<unknown>") -> list[Clause]:
    """Every string literal a generator's source holds, one each, with its line; no docstring.

    `filename` is the generator's path, which `ast.parse` names in a SyntaxError or warning.
    """
    tree = ast.parse(source, filename=filename)
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


def _formats_a_literal(node: ast.AST) -> bool:
    """Whether `node` is a `.format` call on a string literal."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    )


def _strings_in(node: ast.AST) -> list[str]:
    """The strings an argument draws, read through what the module docstring lists."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List):
        return [string for element in node.elts for string in _strings_in(element)]
    if isinstance(node, ast.JoinedStr):
        literal = "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        return [literal] if literal else []
    if isinstance(node, ast.IfExp):
        return _strings_in(node.body) + _strings_in(node.orelse)
    if _formats_a_literal(node):
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        assert isinstance(node.func.value, ast.Constant)
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        return [
            str(node.func.value.value),
            *(string for argument in arguments for string in _strings_in(argument)),
        ]
    return []


def diagram_units(source: str, *, filename: str = "<unknown>") -> list[Clause]:
    """What each drawing call or data row draws together, as clauses, each with its first line.

    `filename` is the generator's path, which `ast.parse` names in a SyntaxError or warning.
    """
    tree = ast.parse(source, filename=filename)
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
        if not isinstance(node, ast.Tuple) or id(node) in in_a_call:
            continue
        parent = parents.get(id(node))
        if isinstance(parent, ast.Tuple):
            continue
        rows: list[ast.expr] = [node]
        if (
            not isinstance(parent, ast.List)
            and node.elts
            and all(isinstance(row, ast.Tuple | ast.List) for row in node.elts)
        ):
            rows = list(node.elts)
        for row in rows:
            strings = _strings_in(row)
            if strings:
                units.append((row.lineno, strings))
    return [
        Clause(line, clause.text)
        for line, strings in sorted(units)
        for clause in clauses(UNIT_JOINER.join(strings))
    ]
