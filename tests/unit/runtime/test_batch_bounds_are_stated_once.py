"""`V2-P4-058`. `MAX_BATCH_ITEMS` and `MAX_BATCH_WORKERS`, held to "stated once".

Both constants' docstrings in `batch_contracts.py` open with that promise -- *"Stated once here;
`api/app.py` reads it, not a copy"* -- and nothing measured it. Each value does appear exactly
once as a batch constant today, which is the whole problem: the property was true, so no test was
missed at review time, and a second copy could arrive green. Measured: `_PROBE_SECOND_CAP =
10_000` and `_PROBE_SECOND_WORKERS = 8` added to `runtime/batch.py` left `pytest tests/unit -k
batch` at **93 passed**.

Why "stated once" is worth a test rather than a convention. `MAX_BATCH_WORKERS` was **lowered
from 32 to 8** by `V2-P4-019` on the strength of a throughput measurement, and `MAX_BATCH_ITEMS`
was **raised from 1,000 to 10,000** in the same row because the old cap made a whole-market batch
inexpressible. Both are numbers somebody will move again. A second literal is not a duplicate
that is merely untidy: it is a bound that will not move with the first, and the two faces that
disagree about a cap are the API rejecting a request the service would have run, or the reverse.

Checked in both directions, because either alone is satisfiable by the wrong thing:

* **Syntactically**, no second literal of either value appears anywhere on the batch surface --
  which catches a hard-coded `max_length=10_000` on a new request model, the form the duplication
  would actually take.
* **By binding**, every module that uses a bound gets the name from an `import` of
  `batch_contracts` and never assigns it, which catches a copy that spells its value differently
  and so leaves no matching literal at all -- `MAX_BATCH_WORKERS = 4 * 2`.

The second check reads the **import statement**, not the runtime value, and that is not a
stylistic choice. Object identity was tried first and is worthless here: `MAX_BATCH_WORKERS` is
`8`, CPython interns every small integer, and `4 * 2 is 8` is `True` -- so an `is` comparison
called a re-declared constant the same object and the probe went green. "Reads it, not a copy"
is a claim about how the name is bound, and how a name is bound is a fact about the source.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from openalpha_cn.batch_contracts import MAX_BATCH_ITEMS, MAX_BATCH_WORKERS

PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "openalpha_cn"

DECLARING_MODULE: Final[str] = "batch_contracts.py"
"""The one module allowed to write either bound. Both docstrings say "stated once here"."""

READING_MODULES: Final[tuple[str, ...]] = ("runtime/batch.py", "api/app.py")
"""The modules that use a bound and must therefore import it.

`runtime/batch.py` re-exports both in `__all__`; `api/app.py` applies both to its own request
model and is the module both docstrings name by name as the reader.
"""

BOUND_SOURCES: Final[frozenset[str]] = frozenset(
    {"openalpha_cn.batch_contracts", "openalpha_cn.runtime.batch"}
)
"""The imports from which a reading module may take a bound: the declaration, or its re-export.

`api/app.py` does not import `batch_contracts` directly -- it takes both names from
`runtime.batch`, which lists them in `__all__` -- so requiring the declaration itself would be
requiring a different import graph than the one this repository has. Allowing the re-export does
not weaken the check, because `runtime/batch.py` is itself a reading module and cannot import
itself: the only source available to it here is `batch_contracts`, so the chain is pinned at
both ends rather than trusted in the middle.
"""

BATCH_SURFACE: Final[tuple[str, ...]] = (
    "batch_contracts.py",
    "runtime/batch.py",
    "storage/batch.py",
    "api/app.py",
    "sdk.py",
)
"""Every module that declares, re-exports or enforces a batch bound.

`batch_contracts.py` declares the pair; `runtime/batch.py` re-exports them in `__all__`;
`api/app.py` applies both to its own request model; `storage/batch.py` and `sdk.py` are the other
two modules on the batch path and are included so that a bound copied *near* the surface rather
than onto it is still found. Listed rather than globbed: a whole-package sweep for the integer
`8` would be a different test with a different false-positive profile, and this one is about the
handful of files where the number means "the batch cap".
"""

BATCH_BOUNDS: Final[dict[str, int]] = {
    "MAX_BATCH_ITEMS": MAX_BATCH_ITEMS,
    "MAX_BATCH_WORKERS": MAX_BATCH_WORKERS,
}


def _integer_literals(relative: str) -> list[tuple[int, int]]:
    """Every integer literal in the module, as (value, line number). `True`/`False` excluded."""
    path = PACKAGE_ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.value, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ]


def test_each_batch_bound_is_written_as_a_literal_exactly_once() -> None:
    """One literal per bound across the whole batch surface, and it is the declaration.

    Measured on the clean tree before this test was written: across all five modules the integers
    `10_000` and `8` occur **twice in total**, and both are the defining assignments in
    `batch_contracts.py`. So the rule is exact rather than approximate -- there is no incidental
    `8` anywhere on this surface being tolerated by a threshold.

    That measurement is also the honest limit of the check. It holds because these five files
    happen not to use either integer for anything else; if a legitimate one arrives, the right
    move is to narrow `BATCH_SURFACE` or to say why that occurrence is not a bound, not to raise
    a count until the test passes again.
    """
    for name, value in BATCH_BOUNDS.items():
        sites = [
            (module, lineno)
            for module in BATCH_SURFACE
            for literal, lineno in _integer_literals(module)
            if literal == value
        ]

        assert len(sites) == 1, (
            f"{name} is {value}, and the batch surface writes that literal at "
            f"{sorted(sites)} -- {len(sites)} times, not once. Its docstring promises 'stated "
            "once here', and a second copy is a bound that will not move when the first does: "
            f"{name} has already been changed once by V2-P4-019. Import the constant instead"
        )
        module, _ = sites[0]
        assert module == DECLARING_MODULE, (
            f"{name}'s only literal is in {module}, not {DECLARING_MODULE}. The declaration is "
            "the one place the value may be written, because that is where the docstring "
            "explaining the number lives"
        )


def _bound_names(relative: str) -> tuple[set[str], set[str]]:
    """(names imported from a `BOUND_SOURCES` module, names assigned at module level)."""
    tree = ast.parse((PACKAGE_ROOT / relative).read_text(encoding="utf-8"))

    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in BOUND_SOURCES
        for alias in node.names
    }
    assigned: set[str] = set()
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        assigned |= {target.id for target in targets if isinstance(target, ast.Name)}
    return imported, assigned


def test_every_module_that_uses_a_batch_bound_imports_it_and_never_assigns_it() -> None:
    """Each docstring's "reads it, not a copy", checked on the binding rather than the value.

    Object identity was the obvious check and is useless for these two constants.
    `MAX_BATCH_WORKERS` is `8`; CPython interns small integers, so a module that re-declares it
    as `4 * 2` binds *the same object* and `is` reports no difference at all -- measured, the
    probe went green. A re-declared bound also leaves no second literal for the scan above to
    find, so without this test that form of the copy is invisible to the whole file.

    What "reads it" actually means is that the name arrives by import and is never rebound, and
    both halves are asserted: importing it and then shadowing it with an assignment would satisfy
    the first alone.
    """
    for name in BATCH_BOUNDS:
        for relative in READING_MODULES:
            imported, assigned = _bound_names(relative)

            assert name in imported, (
                f"{relative} uses {name} but does not import it from any of "
                f"{sorted(BOUND_SOURCES)}; it gets the name some other way. Both bounds are "
                "documented as stated once and read everywhere else, and a module that does not "
                "import the declaration is a cap that stays behind when the declaration moves"
            )
            assert name not in assigned, (
                f"{relative} assigns {name} at module level, shadowing the imported "
                "declaration. That is the copy this file exists to refuse -- and note it can be "
                "spelled so that no literal appears (`4 * 2`), which is why the literal scan "
                "above cannot be the only check"
            )
