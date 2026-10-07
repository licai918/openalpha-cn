"""Every `duckdb.connect(` call in `src/` goes through one module-private `_connect` helper
(`V2-P6-021`).

DuckDB prints an ANSI progress bar to the real stdout file descriptor -- bypassing Python's
`sys.stdout`, so `click.testing.CliRunner`'s capture never sees it and redirecting stdout at the
Python level does not help either -- for any query that runs past `progress_bar_time` (2000ms by
default), on by default. The research driver's `openalpha factor stale-return-paths`
precondition (`scripts/research/p6.py::require_clean_return_paths`) requires that command's
stdout to be exactly one line, and any `--json` command's stdout has to parse; a progress bar
landing in the middle of either corrupts it, silently and only under timing (a manual run
redirected to a file came out clean; the same command under a subprocess whose stdout is a pipe
did not -- DuckDB does not check `isatty()` here).

The fix is `panel/store.py::_connect` and `storage/parquet.py::_connect`: the only two places
`duckdb.connect` may be called directly, each `SET enable_progress_bar = false` (and
`enable_progress_bar_print`) on the connection before handing it back. `enable_progress_bar` is
a DuckDB `LOCAL`-scope setting, not a `GLOBAL` one, so it cannot be set through
`duckdb.connect(..., config=...)` -- DuckDB 1.5.5 raises `Invalid Input Error: Could not set
option "enable_progress_bar" as a global option` for that -- which is why each helper does it
with `SET` after connecting instead.

This file is the guard that keeps a future `duckdb.connect(` call from forgetting the helper: it
scans every `.py` file under `src/openalpha_cn` with `ast` (a grep would miss a call spread
across lines, and importing every module to introspect it would run code this test has no
business running) and fails if any call is not lexically inside a function named `_connect`.
`tests/unit/panel/test_query_callers.py` and `test_offline_suite.py` are the precedent for this
shape: a source scan rather than a runtime check, because the property being protected is about
where a call is written, not about what any one test run happens to exercise.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "openalpha_cn"

HELPER_FUNCTION_NAME = "_connect"
"""The one function name a `duckdb.connect(` call may be written inside, in any `src/` file."""

MODULES_THAT_MUST_DEFINE_THE_HELPER: frozenset[str] = frozenset(
    {"panel/store.py", "storage/parquet.py"}
)
"""The two modules `V2-P6-021`'s brief names as opening DuckDB connections. Asserted directly so
this guard cannot pass vacuously -- e.g. by both modules being deleted, or the helper renamed
everywhere at once -- the way an allowlist with nothing left to allow would."""


class _DuckDBConnectViolation:
    __slots__ = ("enclosing_function", "lineno", "path")

    def __init__(self, path: Path, lineno: int, enclosing_function: str | None) -> None:
        self.path = path
        self.lineno = lineno
        self.enclosing_function = enclosing_function

    def __repr__(self) -> str:
        where = self.enclosing_function or "<module scope>"
        return f"{self.path}:{self.lineno} (inside {where})"


class _ConnectCallVisitor(ast.NodeVisitor):
    """Collect every `duckdb.connect(...)` call not lexically inside `_connect`.

    Tracks the enclosing function by name with an explicit stack rather than by walking
    upward from each call, because a call can sit inside a nested function (a closure, a
    generator) defined inside `_connect` itself -- the stack answers "which function am I in
    right now" the way nested `ast.walk` cannot.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[_DuckDBConnectViolation] = []
        self.defines_helper = False
        self._function_stack: list[str] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name == HELPER_FUNCTION_NAME:
            self.defines_helper = True
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "duckdb"
        ):
            enclosing = self._function_stack[-1] if self._function_stack else None
            if enclosing != HELPER_FUNCTION_NAME:
                self.violations.append(_DuckDBConnectViolation(self.path, node.lineno, enclosing))
        self.generic_visit(node)


def _scan(path: Path, source: str) -> _ConnectCallVisitor:
    tree = ast.parse(source, filename=str(path))
    visitor = _ConnectCallVisitor(path)
    visitor.visit(tree)
    return visitor


def test_no_source_file_calls_duckdb_connect_outside_the_module_helper() -> None:
    modules = sorted(SOURCE.rglob("*.py"))
    assert modules, "expected at least one module under src/openalpha_cn"

    violations: list[_DuckDBConnectViolation] = []
    helper_defined_in: set[str] = set()
    for module in modules:
        visitor = _scan(module, module.read_text(encoding="utf-8"))
        violations.extend(visitor.violations)
        if visitor.defines_helper:
            helper_defined_in.add(module.relative_to(SOURCE).as_posix())

    assert not violations, (
        "duckdb.connect(...) called outside the module's _connect helper -- this bypasses the "
        f"progress-bar-disabling config (V2-P6-021): {violations}"
    )
    missing = MODULES_THAT_MUST_DEFINE_THE_HELPER - helper_defined_in
    assert not missing, (
        f"expected {sorted(missing)} to each define a `{HELPER_FUNCTION_NAME}` helper; a module "
        "with none is a module this guard cannot be protecting"
    )


def test_the_scanner_itself_flags_a_bypass_and_clears_the_helper() -> None:
    """The guard above is only as good as `_ConnectCallVisitor`'s own logic; this proves it can
    tell the two shapes apart, on synthetic source rather than on whatever `src/` currently
    holds -- a self-check that survives every future edit to the real modules."""
    bypass = textwrap.dedent(
        """
        import duckdb


        def read_something():
            with duckdb.connect(":memory:") as connection:
                return connection.execute("SELECT 1").fetchall()
        """
    )
    visitor = _scan(Path("<bypass>"), bypass)
    assert len(visitor.violations) == 1
    assert visitor.violations[0].enclosing_function == "read_something"
    assert not visitor.defines_helper

    routed = textwrap.dedent(
        """
        import duckdb


        def _connect(database):
            connection = duckdb.connect(database)
            connection.execute("SET enable_progress_bar = false")
            return connection


        def read_something():
            with _connect(":memory:") as connection:
                return connection.execute("SELECT 1").fetchall()
        """
    )
    visitor = _scan(Path("<routed>"), routed)
    assert visitor.violations == []
    assert visitor.defines_helper
