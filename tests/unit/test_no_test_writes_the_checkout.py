"""No test writes the checkout it runs from: the reading half, and proof the measuring half is live.

D13 I-D. Four tests rewrote tracked modules under `src/` -- `backtest/candidate_ranking.py`,
`shortlist_gate.py`, `factor_experiment.py` and `factor_ic.py` -- with one more import, ran the
import linter, and wrote the original back with `Path.write_text`. On Windows that writes CRLF, and
both Windows legs of CI printed `CRLF will be replaced by LF the next time Git touches it` for
exactly those four files after every green run; on Linux and macOS the bytes came back and
`git status` said nothing. Six more tests created a probe module under `src/` and deleted it in
`finally`, and thirty `grimp.build_graph` calls left `.grimp_cache/` at the root. It is M9's class
-- the publication gate's probes written at the root -- one directory further in, and worse: a run
killed between the write and its `finally` leaves the planted import in `src/`, and anything
importing from the checkout meanwhile sees it. Those tests now plant their violation in a copy
under `tmp_path` (`tests/scratch_package.py`) and build their graphs with `cache_dir=None`.

Two halves keep it that way.

**The reading** (`_checkout_writes`): every `*.py` under `tests/`, `tests/e2e/` included, is parsed
and each call of the kinds below is checked against where its target comes from. A target is
*in the checkout* when it derives from `__file__`, a module's `__file__` or `__path__`,
`Path.cwd()`/`os.getcwd()`, `importlib.resources.files`, or a relative path literal -- the last
three because pytest runs from the root -- through names bound at module and function level, `/`,
`.parent` and `.parents[...]`, `resolve()` and the other methods that return a path, `str()`,
`os.path.join`, containers and loops over them, a helper's parameter a call in the same module
hands one to, a helper's or a fixture's return value, and a name imported from another module under
`tests/` that is one there. The writes it knows: `write_text`, `write_bytes`, `touch`, `mkdir`,
`rmdir`, `unlink`, `chmod`, `symlink_to`, `hardlink_to`, and `rename`/`replace` with one argument
(`Path`'s, not `str`'s), called on a path or through the `Path` class unbound; `open`, `Path.open`,
`io.open`, `io.FileIO`, `codecs.open`, the `gzip`, `bz2` and `lzma` openers and file classes,
`tarfile.open` and `zipfile.ZipFile` in any mode that writes, and `os.open` with a flag that writes;
`shutil`'s copies, `move` and `rmtree`; `os`'s removals, renames, links and `makedirs`;
`tempfile`'s constructors given `dir=`; `logging`'s file handlers; `numpy.save` and its siblings; a
table's own writers (`to_csv`, `to_parquet`, `write_csv` and the rest); and `sqlite3.connect` and
`duckdb.connect`, which create what they open. And two caches a library keeps in the *working
directory*: `grimp.build_graph` without `cache_dir=None`, and the import linter without
`no_cache=True`. The first is how `.grimp_cache/` came to stand at the root. D14 review m-3 measured
eight writers unread while this paragraph said "every call that can write a file": the `gzip`,
`zipfile` and `tarfile` openers, `io.FileIO`, `os.open`, `logging.FileHandler`, the unbound
`Path.write_text` and `DataFrame.to_csv`.

What the reading cannot see, stated: a writer that is not in that list -- another library's own
save function, a `COPY ... TO` or a `VACUUM INTO` inside an SQL string, `os.write` on a descriptor
opened somewhere it does not read, a method reached through `getattr`; a path assembled from pieces
it does not follow -- an attribute of an object handed in, a method's return value, a string built
at run time; a write made by a subprocess or by the code under test (`subprocess.run(...,
cwd=ROOT)` is not read as a write, because nearly all of them only read); and a call that forwards
`**kwargs`, which is checked where the keywords are spelled instead. It is flow-insensitive, so a
name bound once to a checkout path and once to `tmp_path` reads as the first everywhere, and it
reads a relative literal as the root's even after a test changed its working directory. Both err
towards a finding.

**The measuring** (`tests/checkout_guard.py`, wired into `tests/conftest.py`): a snapshot of the
checkout when a session starts, when collection finishes and when it ends, and a failed run for any
difference -- which a subprocess, a computed path and a library's cache all reach. Its limits are in
its own docstring. The tests at the end of this file drive those hooks in a child pytest whose root
is a scratch directory, so proving that they fire writes nothing here.
"""

from __future__ import annotations

import ast
import itertools
import os
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

import pytest
from checkout_guard import changes, snapshot

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
TESTS_ROOT: Final[Path] = ROOT / "tests"

# --- where a path comes from ----------------------------------------------------------------------

SEED_ATTRIBUTES: Final[frozenset[str]] = frozenset({"__file__", "__path__"})
SEEDING_CALLS: Final[frozenset[str]] = frozenset(
    {
        "importlib.resources.files",
        "inspect.getfile",
        "inspect.getsourcefile",
        "os.getcwd",
        "pathlib.Path.cwd",
    }
)
PATH_ATTRIBUTES: Final[frozenset[str]] = frozenset({"parent", "parents"})
PATH_METHODS: Final[frozenset[str]] = frozenset(
    {
        "absolute",
        "expanduser",
        "glob",
        "items",
        "iterdir",
        "joinpath",
        "resolve",
        "rglob",
        "values",
        "walk",
        "with_name",
        "with_stem",
        "with_suffix",
    }
)
"""Called on a path, or on a container of paths, each returns paths from the same place."""

PATH_CLASSES: Final[frozenset[str]] = frozenset(
    {"Path", "PosixPath", "PurePath", "PurePosixPath", "PureWindowsPath", "WindowsPath"}
)
FIRST_ARGUMENT_CARRIES: Final[frozenset[str]] = frozenset(
    {
        "enumerate",
        "frozenset",
        "iter",
        "list",
        "next",
        "os.fspath",
        "os.path.abspath",
        "os.path.dirname",
        "os.path.expanduser",
        "os.path.join",
        "os.path.normpath",
        "os.path.realpath",
        "reversed",
        "set",
        "sorted",
        "str",
        "tuple",
    }
)
"""Calls whose result is where their first argument is. Only the first: `os.path.join(tmp_path,
"x")` is in `tmp_path` although `"x"` alone would be read as the root's."""

ANY_ARGUMENT_CARRIES: Final[frozenset[str]] = frozenset({"max", "min", "zip"})

# --- what writes --------------------------------------------------------------------------------

RECEIVER_WRITES: Final[frozenset[str]] = frozenset(
    {
        "chmod",
        "hardlink_to",
        "lchmod",
        "mkdir",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
RECEIVER_MOVES: Final[frozenset[str]] = frozenset({"rename", "replace"})
"""`Path.rename`/`Path.replace` take one argument; `str.replace` takes two or three."""

OPENERS: Final[Mapping[str, int]] = {
    "open": 1,
    "io.open": 1,
    "codecs.open": 1,
    "io.FileIO": 1,
    "gzip.open": 1,
    "gzip.GzipFile": 1,
    "bz2.open": 1,
    "bz2.BZ2File": 1,
    "lzma.open": 1,
    "lzma.LZMAFile": 1,
    "tarfile.open": 1,
    "tarfile.TarFile": 1,
    "zipfile.ZipFile": 1,
}
"""Each call that opens a file by name and takes a `mode`, and the position its `mode` takes.

Every one of them reads when no mode is given, so a call naming none is not a write."""

DESCRIPTOR_OPENERS: Final[frozenset[str]] = frozenset({"os.open"})
WRITING_FLAGS: Final[frozenset[str]] = frozenset(
    {"O_APPEND", "O_CREAT", "O_EXCL", "O_RDWR", "O_TRUNC", "O_WRONLY"}
)
"""The `os.open` flags that write, or make a file that was not there."""


def _flags_can_write(flags: ast.expr | None) -> bool:
    """Whether `os.open`'s flags can write.

    An expression naming no flag this knows, such as a variable or a bare number, counts as writing.
    """
    if flags is None:
        return False
    named = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(flags)
        if isinstance(node, ast.Attribute | ast.Name)
    }
    return bool(named & WRITING_FLAGS) or not named & (WRITING_FLAGS | {"O_RDONLY"})


TABLE_WRITERS: Final[frozenset[str]] = frozenset(
    {
        "to_csv",
        "to_excel",
        "to_feather",
        "to_hdf",
        "to_html",
        "to_json",
        "to_latex",
        "to_markdown",
        "to_parquet",
        "to_pickle",
        "to_stata",
        "to_xml",
        "write_avro",
        "write_csv",
        "write_excel",
        "write_ipc",
        "write_json",
        "write_ndjson",
        "write_parquet",
    }
)
"""A table's own writers -- pandas', polars' and a DuckDB relation's spellings -- called on it with
a destination first."""

FIRST_ARGUMENT_WRITES: Final[frozenset[str]] = frozenset(
    {
        "duckdb.connect",
        "logging.FileHandler",
        "logging.handlers.RotatingFileHandler",
        "logging.handlers.TimedRotatingFileHandler",
        "logging.handlers.WatchedFileHandler",
        "numpy.save",
        "numpy.savetxt",
        "numpy.savez",
        "numpy.savez_compressed",
        "os.chmod",
        "os.makedirs",
        "os.mkdir",
        "os.remove",
        "os.removedirs",
        "os.rmdir",
        "os.truncate",
        "os.unlink",
        "os.utime",
        "shutil.rmtree",
        "sqlite3.connect",
    }
)
SECOND_ARGUMENT_WRITES: Final[frozenset[str]] = frozenset(
    {
        "os.link",
        "os.symlink",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
    }
)
EITHER_ARGUMENT_WRITES: Final[frozenset[str]] = frozenset(
    {"os.rename", "os.renames", "os.replace", "shutil.move"}
)
"""A move writes where it takes from as well as where it puts."""

TEMPORARY_FILES: Final[frozenset[str]] = frozenset(
    {
        "tempfile.NamedTemporaryFile",
        "tempfile.SpooledTemporaryFile",
        "tempfile.TemporaryDirectory",
        "tempfile.TemporaryFile",
        "tempfile.mkdtemp",
        "tempfile.mkstemp",
    }
)
GRAPH_BUILDERS: Final[frozenset[str]] = frozenset({"grimp.build_graph"})
LINTERS: Final[frozenset[str]] = frozenset(
    {
        "import_linter_containment.contained_lint_imports",
        "import_linter_containment.raw_lint_imports_disables",
        "importlinter.cli.lint_imports",
    }
)


@dataclass(frozen=True, order=True)
class CheckoutWrite:
    """One call under `tests/` that can write inside the checkout, and where it is."""

    path: str
    function: str
    line: int
    operation: str
    target: str


@dataclass(frozen=True)
class _Element:
    """An item of `of`, the way a loop or a comprehension binds one."""

    of: ast.AST


@dataclass(frozen=True)
class _Imported:
    """`name` from another module under `tests/`, bound here by `from <module> import <name>`."""

    module: str
    name: str


@dataclass(frozen=True, eq=False)
class _Definition:
    """A name bound by `def` (`node` is the definition) or by `class`/`import` (`node` is None)."""

    node: ast.FunctionDef | ast.AsyncFunctionDef | None


class _Scope:
    """A module, class, function or lambda body: the names it binds, and to what."""

    def __init__(self, node: ast.AST, parent: _Scope | None, name: str) -> None:
        self.node = node
        self.parent = parent
        self.name = name if parent is None or parent.parent is None else f"{parent.name}.{name}"
        self.bindings: dict[str, list[tuple[object, _Scope]]] = {}
        self.parameters: set[str] = set()
        self.globals: set[str] = set()
        self.rooted: set[str] = set()

    def owner(self, name: str) -> _Scope | None:
        """The scope `name` resolves to from here: the nearest that binds it, skipping classes."""
        scope: _Scope | None = self
        if name in self.globals:
            while scope is not None and scope.parent is not None:
                scope = scope.parent
            return scope
        while scope is not None:
            if name in scope.bindings and (
                scope is self or not isinstance(scope.node, ast.ClassDef)
            ):
                return scope
            scope = scope.parent
        return None


class _Module:
    """One file's scopes, bindings and calls, and which of its names are in the checkout."""

    def __init__(self, relative: str, source: str, audit: _Audit) -> None:
        self.relative = relative
        self.stem = PurePosixPath(relative).stem
        self.audit = audit
        self.aliases: dict[str, str] = {}
        self.scopes: list[_Scope] = []
        self.scope_of: dict[ast.AST, _Scope] = {}
        self.calls: list[tuple[ast.Call, _Scope]] = []
        self.returns: dict[ast.AST, list[tuple[object, _Scope]]] = {}
        self.attributes: dict[str, list[tuple[object, _Scope]]] = {}
        self.rooted_attributes: set[str] = set()
        self.returning_rooted: set[ast.AST] = set()
        tree = ast.parse(source, filename=relative)
        self.module_scope = self._open(tree, None, "<module>")
        for statement in tree.body:
            self._visit(statement, self.module_scope)
        self._bind_what_local_calls_hand_over()

    # -- collection --

    def _open(self, node: ast.AST, parent: _Scope | None, name: str) -> _Scope:
        scope = _Scope(node, parent, name)
        self.scopes.append(scope)
        self.scope_of[node] = scope
        return scope

    def _visit(self, node: ast.AST, scope: _Scope) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            self._bind(scope, node.name, _Definition(node))
            for decorator in node.decorator_list:
                self._visit(decorator, scope)
            inner = self._open(node, scope, node.name)
            self.returns.setdefault(node, [])
            self._enter(node.args, inner, scope)
            for statement in node.body:
                self._visit(statement, inner)
            return
        if isinstance(node, ast.Lambda):
            inner = self._open(node, scope, "<lambda>")
            self._enter(node.args, inner, scope)
            self._visit(node.body, inner)
            return
        if isinstance(node, ast.ClassDef):
            self._bind(scope, node.name, _Definition(None))
            for expression in [
                *node.decorator_list,
                *node.bases,
                *(k.value for k in node.keywords),
            ]:
                self._visit(expression, scope)
            inner = self._open(node, scope, node.name)
            for statement in node.body:
                self._visit(statement, inner)
            return
        self._record(node, scope)
        for child in ast.iter_child_nodes(node):
            self._visit(child, scope)

    def _enter(self, arguments: ast.arguments, inner: _Scope, outer: _Scope) -> None:
        """Bind a function's parameters, and its defaults as evaluated where it is defined."""
        positional = [*arguments.posonlyargs, *arguments.args]
        for argument in [*positional, *arguments.kwonlyargs, arguments.vararg, arguments.kwarg]:
            if argument is not None:
                inner.parameters.add(argument.arg)
                inner.bindings.setdefault(argument.arg, [])
        defaulted = positional[len(positional) - len(arguments.defaults) :]
        pairs = [
            *zip(defaulted, arguments.defaults, strict=True),
            *zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True),
        ]
        for argument, default in pairs:
            if default is not None:
                inner.bindings[argument.arg].append((default, outer))
                self._visit(default, outer)

    def _record(self, node: ast.AST, scope: _Scope) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._bind_target(target, node.value, scope)
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
            self._bind_target(node.target, node.value, scope)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            self._bind_target(node.target, _Element(node.iter), scope)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            self._bind_target(node.optional_vars, node.context_expr, scope)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                self.aliases[local] = alias.name if alias.asname else local
                self._bind(scope, local, _Definition(None))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                local = alias.asname or alias.name
                self.aliases[local] = f"{node.module}.{alias.name}"
                self._bind(scope, local, _Imported(node.module, alias.name))
        elif isinstance(node, ast.Global):
            scope.globals.update(node.names)
        elif isinstance(node, ast.Call):
            self.calls.append((node, scope))
        elif (
            isinstance(node, ast.Return | ast.Yield | ast.YieldFrom)
            and node.value is not None
            and isinstance(scope.node, ast.FunctionDef | ast.AsyncFunctionDef)
        ):
            self.returns[scope.node].append((node.value, scope))

    def _bind(self, scope: _Scope, name: str, value: object) -> None:
        owner = self.module_scope if name in scope.globals else scope
        owner.bindings.setdefault(name, []).append((value, scope))

    def _bind_target(self, target: ast.AST, value: object, scope: _Scope) -> None:
        if isinstance(target, ast.Name):
            self._bind(scope, target.id, value)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, value, scope)
        elif isinstance(target, ast.Tuple | ast.List):
            if (
                isinstance(value, ast.Tuple | ast.List)
                and len(value.elts) == len(target.elts)
                and not any(isinstance(e, ast.Starred) for e in (*value.elts, *target.elts))
            ):
                for part, item in zip(target.elts, value.elts, strict=True):
                    self._bind_target(part, item, scope)
            else:
                for part in target.elts:
                    self._bind_target(part, value, scope)
        elif isinstance(target, ast.Attribute):
            self.attributes.setdefault(target.attr, []).append((value, scope))

    def _bind_what_local_calls_hand_over(self) -> None:
        """A helper's parameter holds whatever any call in this module passes it."""
        for call, scope in self.calls:
            for definition in self._definitions(call.func, scope):
                callee = self.scope_of[definition]
                names = [a.arg for a in (*definition.args.posonlyargs, *definition.args.args)]
                handed = itertools.takewhile(lambda a: not isinstance(a, ast.Starred), call.args)
                for name, value in zip(names, handed, strict=False):
                    callee.bindings[name].append((value, scope))
                for keyword in call.keywords:
                    if keyword.arg is not None and keyword.arg in callee.parameters:
                        callee.bindings[keyword.arg].append((keyword.value, scope))

    def _definitions(
        self, function: ast.AST, scope: _Scope
    ) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
        if isinstance(function, ast.Name) and (owner := scope.owner(function.id)) is not None:
            for bound, _ in owner.bindings.get(function.id, []):
                if isinstance(bound, _Definition) and bound.node is not None:
                    yield bound.node

    # -- inference --

    def settle(self) -> None:
        """Grow every set of names in the checkout until nothing more follows from them."""
        grew = True
        while grew:
            grew = False
            for scope in self.scopes:
                for name, bound in scope.bindings.items():
                    if name not in scope.rooted and (
                        (name in scope.parameters and name in self.audit.rooted_fixtures)
                        or any(self.rooted(value, where) for value, where in bound)
                    ):
                        scope.rooted.add(name)
                        grew = True
            for attribute, bound in self.attributes.items():
                if attribute not in self.rooted_attributes and any(
                    self.rooted(value, where) for value, where in bound
                ):
                    self.rooted_attributes.add(attribute)
                    grew = True
            for function, values in self.returns.items():
                if function not in self.returning_rooted and any(
                    self.rooted(value, where) for value, where in values
                ):
                    self.returning_rooted.add(function)
                    grew = True

    def exported(self) -> set[str]:
        """What another module importing from this one gets in the checkout."""
        return set(self.module_scope.rooted) | {
            function.name
            for function in self.returning_rooted
            if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
            and self.scope_of[function].parent is self.module_scope
        }

    def rooted_fixtures(self) -> set[str]:
        """Names of this module's fixtures that return or yield a path in the checkout."""
        found: set[str] = set()
        for function in self.returning_rooted:
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in function.decorator_list:
                called = decorator.func if isinstance(decorator, ast.Call) else decorator
                if self.qualified(called) != "pytest.fixture":
                    continue
                named = [
                    keyword.value.value
                    for keyword in (decorator.keywords if isinstance(decorator, ast.Call) else [])
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
                ]
                found.add(str(named[0]) if named else function.name)
        return found

    def qualified(self, node: ast.AST) -> str | None:
        """`node` as a dotted name, with this module's imports resolved."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.qualified(node.value)
            return None if base is None else f"{base}.{node.attr}"
        return None

    def rooted(self, node: object, scope: _Scope) -> bool:
        """Whether `node`, evaluated in `scope`, is a path inside the checkout."""
        if isinstance(node, _Element):
            return self.rooted(node.of, scope)
        if isinstance(node, _Imported):
            return node.name in self.audit.exports.get(node.module, set())
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return True
            owner = scope.owner(node.id)
            return owner is not None and node.id in owner.rooted
        if isinstance(node, ast.Constant):
            return _relative_path(node.value)
        if isinstance(node, ast.Attribute):
            if node.attr in SEED_ATTRIBUTES:
                return True
            if node.attr in PATH_ATTRIBUTES:
                return self.rooted(node.value, scope)
            return node.attr in self.rooted_attributes
        if isinstance(node, ast.Subscript | ast.Starred | ast.NamedExpr):
            return self.rooted(node.value, scope)
        if isinstance(node, ast.BinOp):
            return isinstance(node.op, ast.Div) and self.rooted(node.left, scope)
        if isinstance(node, ast.IfExp):
            return self.rooted(node.body, scope) or self.rooted(node.orelse, scope)
        if isinstance(node, ast.BoolOp):
            return any(self.rooted(value, scope) for value in node.values)
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return any(self.rooted(element, scope) for element in node.elts)
        if isinstance(node, ast.Dict):
            return any(self.rooted(value, scope) for value in node.values)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self.rooted(node.elt, scope)
        if isinstance(node, ast.DictComp):
            return self.rooted(node.value, scope)
        if isinstance(node, ast.JoinedStr):
            first = node.values[0] if node.values else None
            if isinstance(first, ast.FormattedValue):
                return self.rooted(first.value, scope)
            return isinstance(first, ast.Constant) and _relative_path(first.value)
        if isinstance(node, ast.Call):
            return self._returns_rooted(node, scope)
        return False

    def _returns_rooted(self, call: ast.Call, scope: _Scope) -> bool:
        name = self.qualified(call.func)
        if name in SEEDING_CALLS:
            return True
        if isinstance(call.func, ast.Attribute) and call.func.attr in PATH_METHODS:
            return self.rooted(call.func.value, scope)
        first = call.args[0] if call.args else None
        if name is not None and name.rsplit(".", 1)[-1] in PATH_CLASSES:
            return self.rooted(first, scope)
        if name in FIRST_ARGUMENT_CARRIES:
            return self.rooted(first, scope)
        if name in ANY_ARGUMENT_CARRIES:
            return any(self.rooted(argument, scope) for argument in call.args)
        if any(d in self.returning_rooted for d in self._definitions(call.func, scope)):
            return True
        if isinstance(call.func, ast.Name) and (owner := scope.owner(call.func.id)) is not None:
            return any(
                isinstance(bound, _Imported) and self.rooted(bound, where)
                for bound, where in owner.bindings.get(call.func.id, [])
            )
        return False

    # -- the writes --

    def writes(self) -> Iterator[CheckoutWrite]:
        for call, scope in self.calls:
            found = self._write(call, scope)
            if found is not None:
                yield CheckoutWrite(self.relative, scope.name, call.lineno, *found)

    def _is_a_path_class(self, node: ast.AST) -> bool:
        name = self.qualified(node)
        return name is not None and name.rsplit(".", 1)[-1] in PATH_CLASSES

    def _write(self, call: ast.Call, scope: _Scope) -> tuple[str, str] | None:
        function = call.func
        name = self.qualified(function)
        if isinstance(function, ast.Attribute) and name not in OPENERS:
            receiver = function.value
            if function.attr in RECEIVER_WRITES and self.rooted(receiver, scope):
                return function.attr, ast.unparse(receiver)
            if (
                function.attr in RECEIVER_WRITES
                and self._is_a_path_class(receiver)
                and call.args
                and self.rooted(call.args[0], scope)
            ):
                return f"{ast.unparse(function)}, unbound", ast.unparse(call.args[0])
            if (
                function.attr in RECEIVER_MOVES
                and len(call.args) == 1
                and not call.keywords
                and (self.rooted(receiver, scope) or self.rooted(call.args[0], scope))
            ):
                return function.attr, f"{ast.unparse(receiver)} -> {ast.unparse(call.args[0])}"
            if function.attr == "open" and self.rooted(receiver, scope) and _writing(call, 0):
                return "open for writing", ast.unparse(receiver)
            if function.attr in TABLE_WRITERS:
                target = _argument(call, 0, "path_or_buf", "path", "excel_writer", "file")
                if target is not None and self.rooted(target, scope):
                    return function.attr, ast.unparse(target)
        if name in OPENERS:
            target = _argument(call, 0, "file", "filename", "name")
            if target is not None and self.rooted(target, scope) and _writing(call, OPENERS[name]):
                return "open for writing", ast.unparse(target)
        if name in DESCRIPTOR_OPENERS:
            target = _argument(call, 0, "path")
            if (
                target is not None
                and self.rooted(target, scope)
                and _flags_can_write(_argument(call, 1, "flags"))
            ):
                return "open for writing", ast.unparse(target)
        targets: list[ast.expr | None] = []
        if name in FIRST_ARGUMENT_WRITES:
            targets = [_argument(call, 0, "path", "database", "filename", "file")]
        elif name in SECOND_ARGUMENT_WRITES:
            targets = [_argument(call, 1, "dst")]
        elif name in EITHER_ARGUMENT_WRITES:
            targets = [_argument(call, 0, "src"), _argument(call, 1, "dst")]
        elif name in TEMPORARY_FILES:
            targets = [_argument(call, 99, "dir")]
        for target in targets:
            if target is not None and self.rooted(target, scope):
                return str(name), ast.unparse(target)
        if _forwards(call):
            return None
        if name in GRAPH_BUILDERS:
            cache = _argument(call, 99, "cache_dir")
            if cache is None or (not _is(cache, None) and self.rooted(cache, scope)):
                return f"{name} caching into .grimp_cache/ in the working directory", ""
        if name in LINTERS and not _is(_argument(call, 99, "no_cache"), True):
            cache = _argument(call, 99, "cache_dir")
            if cache is None or _is(cache, None) or self.rooted(cache, scope):
                return f"{name} caching into .import_linter_cache/ in the working directory", ""
        return None


class _Audit:
    """Every module read together, so a name imported from another test module is followed."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.exports: dict[str, set[str]] = {}
        self.rooted_fixtures: set[str] = set()
        self.modules = [_Module(relative, source, self) for relative, source in sources.items()]
        while True:
            for module in self.modules:
                module.settle()
            exports: dict[str, set[str]] = {}
            for module in self.modules:
                exports.setdefault(module.stem, set()).update(module.exported())
            fixtures = {name for module in self.modules for name in module.rooted_fixtures()}
            if exports == self.exports and fixtures == self.rooted_fixtures:
                return
            self.exports, self.rooted_fixtures = exports, fixtures


def _argument(call: ast.Call, position: int, *keywords: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg in keywords:
            return keyword.value
    positional = list(itertools.takewhile(lambda a: not isinstance(a, ast.Starred), call.args))
    return positional[position] if position < len(positional) else None


def _writing(call: ast.Call, position: int) -> bool:
    """Whether an `open` call's mode can write: a mode it cannot read is taken as one that can."""
    mode = _argument(call, position, "mode")
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(flag in mode.value for flag in "wax+")
    return True


def _forwards(call: ast.Call) -> bool:
    return any(keyword.arg is None for keyword in call.keywords)


def _is(node: ast.expr | None, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _relative_path(value: object) -> bool:
    """A string that, taken as a path, resolves against the working directory -- the root."""
    return (
        isinstance(value, str)
        and value != ""
        and not value.startswith(":")
        and not PurePosixPath(value).is_absolute()
        and not PureWindowsPath(value).is_absolute()
    )


def _checkout_writes(sources: Mapping[str, str]) -> list[CheckoutWrite]:
    """Every write in `sources` (relative path -> text) whose target is inside the checkout."""
    audit = _Audit(sources)
    return sorted(write for module in audit.modules for write in module.writes())


def _the_test_tree() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(TESTS_ROOT.rglob("*.py"))
    }


ALLOWED_CHECKOUT_WRITES: Final[dict[tuple[str, str], str]] = {}
"""`(file, function)` -> why that function may write inside the checkout.

Empty. Asserted **equal** to what the reading finds rather than covering it, so an entry cannot
outlive the write it excused, and a new one is a line somebody writes here with a reason."""


def test_no_test_writes_inside_the_checkout_it_runs_from() -> None:
    """The reading, over the whole test tree. Each line it prints is a call to aim at `tmp_path`."""
    found = _checkout_writes(_the_test_tree())

    assert {(write.path, write.function) for write in found} == set(ALLOWED_CHECKOUT_WRITES), (
        "these calls write inside the checkout; point them at a copy under tmp_path instead "
        "(tests/scratch_package.py copies src/openalpha_cn and lints the copy):\n"
        + "\n".join(
            f"  {write.path}:{write.line} {write.function}: {write.operation} {write.target}"
            for write in found
        )
    )


SUPPORT_PROBE: Final[str] = textwrap.dedent(
    """
    from pathlib import Path

    CHECKOUT = Path(__file__).resolve().parents[1]


    def checkout_file(name):
        return CHECKOUT / name
    """
)
"""A helper module at the tests root, exporting a checkout path and a function returning one."""

READING_PROBE: Final[str] = textwrap.dedent(
    """
    import os
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    import grimp
    import pytest
    from import_linter_containment import contained_lint_imports
    from probe_support import CHECKOUT, checkout_file

    import openalpha_cn

    ROOT = Path(__file__).resolve().parents[2]
    MODULE = ROOT / "src" / "openalpha_cn" / "module.py"


    def writes_a_module_constant():
        MODULE.write_text("x = 1\\n", encoding="utf-8")


    def writes_through_a_local_name():
        probe = ROOT / "src" / "openalpha_cn" / "_probe.py"
        probe.write_bytes(b"")


    def deletes_what_a_loop_found():
        for path in sorted(ROOT.glob("*.tmp")):
            path.unlink()


    def opens_a_joined_path_for_writing():
        with open(os.path.join(ROOT, "out.txt"), "w", encoding="utf-8") as handle:
            handle.write("x")


    def opens_a_path_for_appending():
        with (ROOT / "log.txt").open("a", encoding="utf-8") as handle:
            handle.write("x")


    def copies_into_the_checkout(tmp_path):
        shutil.copy2(tmp_path / "a", ROOT / "b")


    def moves_out_of_the_checkout(tmp_path):
        shutil.move(str(ROOT / "a"), str(tmp_path / "a"))


    def renames_a_path_away(tmp_path):
        (ROOT / "a").rename(tmp_path / "a")


    def removes_a_tree():
        shutil.rmtree(ROOT / "build")


    def makes_a_directory_from_an_f_string():
        os.makedirs(f"{ROOT}/made", exist_ok=True)


    def writes_a_relative_path():
        Path("relative.txt").write_text("x", encoding="utf-8")


    def makes_a_temporary_file_there():
        tempfile.mkstemp(dir=ROOT)


    def opens_a_database_there():
        sqlite3.connect(ROOT / "state.sqlite3")


    def writes_through_the_package_file():
        (Path(openalpha_cn.__file__).parent / "x.py").write_text("", encoding="utf-8")


    def writes_the_working_directory():
        (Path.cwd() / "x.txt").touch()


    def _writes_under(directory):
        (directory / "helper.txt").write_text("x", encoding="utf-8")


    def hands_a_helper_the_root():
        _writes_under(ROOT)


    def _module_path():
        return ROOT / "src" / "module.py"


    def writes_what_a_helper_returned():
        _module_path().write_text("x", encoding="utf-8")


    @pytest.fixture
    def checkout():
        return ROOT


    def takes_the_fixture(checkout):
        (checkout / "fixture").mkdir()


    def writes_an_imported_path():
        (CHECKOUT / "imported.txt").write_text("x", encoding="utf-8")


    def writes_what_an_imported_helper_returned():
        checkout_file("imported.txt").write_text("x", encoding="utf-8")


    def builds_a_cached_graph():
        grimp.build_graph("openalpha_cn")


    def lints_with_the_cache_on():
        contained_lint_imports(config_filename=str(ROOT / "pyproject.toml"))


    def rewrites_a_document():
        (ROOT / "docs" / "notes.md").write_text("x", encoding="utf-8")


    def writes_a_gzip_file():
        import gzip

        with gzip.open(ROOT / "x.gz", "wt", encoding="utf-8") as handle:
            handle.write("x")


    def writes_a_zip_archive():
        import zipfile

        with zipfile.ZipFile(ROOT / "x.zip", "w") as archive:
            archive.writestr("x", "x")


    def writes_a_tar_archive():
        import tarfile

        with tarfile.open(ROOT / "x.tar", "w") as archive:
            archive.add(__file__)


    def logs_to_a_file_there():
        import logging

        logging.getLogger("probe").addHandler(logging.FileHandler(ROOT / "probe.log"))


    def opens_a_descriptor_for_writing():
        os.close(os.open(ROOT / "x.bin", os.O_WRONLY | os.O_CREAT))


    def writes_through_a_raw_file_object():
        import io

        with io.FileIO(ROOT / "x.raw", "w") as handle:
            handle.write(b"x")


    def writes_through_the_unbound_method():
        Path.write_text(ROOT / "x.txt", "x", encoding="utf-8")


    def writes_a_table_there(frame):
        frame.to_csv(ROOT / "frame.csv")


    def saves_an_array_there(array):
        import numpy

        numpy.save(ROOT / "array.npy", array)


    def writes_only_its_tmp_path(tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
        text = MODULE.read_text(encoding="utf-8")
        (tmp_path / "b.txt").write_text(text.replace("a", "b"), encoding="utf-8")
        os.makedirs(os.path.join(tmp_path, "made"))
        with open(ROOT / "pyproject.toml", encoding="utf-8") as handle:
            handle.read()
        with (ROOT / "pyproject.toml").open("rb") as handle:
            handle.read()
        sqlite3.connect(":memory:")


    def reads_archives_and_descriptors_and_writes_only_its_tmp_path(tmp_path, frame):
        import gzip
        import logging
        import zipfile

        with gzip.open(ROOT / "x.gz", "rt", encoding="utf-8") as handle:
            handle.read()
        with zipfile.ZipFile(ROOT / "x.zip") as archive:
            archive.namelist()
        os.close(os.open(ROOT / "x.bin", os.O_RDONLY))
        logging.getLogger("probe").addHandler(logging.FileHandler(tmp_path / "probe.log"))
        frame.to_csv(tmp_path / "frame.csv")
        Path.write_text(tmp_path / "x.txt", "x", encoding="utf-8")


    def builds_a_graph_without_a_cache():
        grimp.build_graph("openalpha_cn", cache_dir=None)


    def lints_without_a_cache():
        contained_lint_imports(config_filename=str(ROOT / "pyproject.toml"), no_cache=True)
    """
)
"""One function per way the reading says it follows a path or recognises a write, and controls."""


def test_the_reading_sees_every_write_it_names_and_none_into_tmp_path() -> None:
    """The reading's own test: without it, the audit above passing would mean nothing.

    Every function in `READING_PROBE` whose name does not say `tmp_path`, `without` or `hands`
    writes inside the checkout in one of the shapes the module docstring lists, and must be found;
    the controls write only `tmp_path`, only read the checkout -- an archive opened for reading, a
    descriptor opened `O_RDONLY` -- or turn the cache off, and must not be.
    `hands_a_helper_the_root` writes nothing itself: the finding is the helper's.
    `rewrites_a_document` is the case `tests/checkout_guard.py`'s docstring points at, a write under
    `docs/` the snapshot cannot see.
    """
    found = _checkout_writes(
        {"tests/probe_support.py": SUPPORT_PROBE, "tests/unit/test_probe.py": READING_PROBE}
    )

    assert {write.path for write in found} == {"tests/unit/test_probe.py"}
    assert {write.function for write in found} == {
        "writes_a_module_constant",
        "writes_through_a_local_name",
        "deletes_what_a_loop_found",
        "opens_a_joined_path_for_writing",
        "opens_a_path_for_appending",
        "copies_into_the_checkout",
        "moves_out_of_the_checkout",
        "renames_a_path_away",
        "removes_a_tree",
        "makes_a_directory_from_an_f_string",
        "writes_a_relative_path",
        "makes_a_temporary_file_there",
        "opens_a_database_there",
        "writes_through_the_package_file",
        "writes_the_working_directory",
        "_writes_under",
        "writes_what_a_helper_returned",
        "takes_the_fixture",
        "writes_an_imported_path",
        "writes_what_an_imported_helper_returned",
        "builds_a_cached_graph",
        "lints_with_the_cache_on",
        "rewrites_a_document",
        "writes_a_gzip_file",
        "writes_a_zip_archive",
        "writes_a_tar_archive",
        "logs_to_a_file_there",
        "opens_a_descriptor_for_writing",
        "writes_through_a_raw_file_object",
        "writes_through_the_unbound_method",
        "writes_a_table_there",
        "saves_an_array_there",
    }


# --- the measuring half ---------------------------------------------------------------------------


def _aged(*paths: Path) -> None:
    """Move each path's modification time an hour back, so the next write moves it visibly.

    A write lands on the clock's current tick, and on a coarse clock that can be the tick a
    snapshot was taken on; an hour back takes the clock's resolution out of the question.
    """
    for path in paths:
        past = path.stat().st_mtime_ns - 3_600_000_000_000
        os.utime(path, ns=(past, past))


def _scratch_checkout(root: Path) -> Path:
    """`root` laid out like a checkout -- one module under `src/`, a `tests/` -- all of it aged.

    Laid out around whatever is already there, so a caller can put a stale directory in first.
    """
    module = root / "src" / "package" / "module.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_bytes(b"x = 1\n")
    (root / "tests").mkdir(exist_ok=True)
    _aged(module, module.parent, root / "src", root / "tests")
    return module


def _windows_text_mode(
    self: Path,
    data: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> int:
    """What Windows text mode does to `Path.write_text`: each "\\n" is written as "\\r\\n"."""
    if newline is None:
        text = data.replace("\n", "\r\n")
    elif newline in ("", "\n"):
        text = data
    else:
        text = data.replace("\n", newline)
    self.write_bytes(text.encode(encoding or "utf-8", errors or "strict"))
    return len(data)


def test_the_scratch_checkout_holds_the_same_bytes_whatever_text_mode_the_platform_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D14 fix review I-1: the module was written with `write_text`, so as CRLF on Windows.

    Seven bytes there and six everywhere else, so the same-size rewrite below was not the same size
    on Windows, and its test failed its own precondition before it reached its Windows branch -- on
    both Windows legs of CI. A fixture whose bytes or length a test measures is written as bytes;
    this holds the one every snapshot test starts from to that, under Windows' translation.
    """
    monkeypatch.setattr(Path, "write_text", _windows_text_mode)

    assert _scratch_checkout(tmp_path).read_bytes() == b"x = 1\n"


def test_a_write_that_put_back_the_bytes_it_found_is_still_a_write(tmp_path: Path) -> None:
    """The Linux and macOS half of D13 I-D: `write_text(original)` changed no byte there."""
    module = _scratch_checkout(tmp_path)
    before = snapshot(tmp_path)

    module.write_bytes(module.read_bytes())

    assert changes(before, snapshot(tmp_path)) == ["rewrote src/package/module.py"]


def test_a_module_created_and_deleted_between_two_snapshots_is_still_seen(tmp_path: Path) -> None:
    """The probe modules' shape: gone again by the time anybody looks, except to the directory."""
    module = _scratch_checkout(tmp_path)
    before = snapshot(tmp_path)

    probe = module.parent / "_layering_gate_probe.py"
    probe.write_text("import sqlite3\n", encoding="utf-8")
    probe.unlink()

    assert changes(before, snapshot(tmp_path)) == [
        "created and removed something in src/package/ -- its modification time moved and its "
        "listing did not"
    ]


def test_bytecode_and_the_runner_s_own_output_are_not_changes_and_a_cache_is(
    tmp_path: Path,
) -> None:
    """What the interpreter and pytest write is ignored by name; `.grimp_cache` is not among it."""
    module = _scratch_checkout(tmp_path)
    before = snapshot(tmp_path)

    bytecode = module.parent / "__pycache__"
    bytecode.mkdir()
    (bytecode / "module.cpython-311.pyc").write_bytes(b"")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".coverage").write_bytes(b"")
    (tmp_path / ".coverage.host.1234.567890").write_bytes(b"")

    assert changes(before, snapshot(tmp_path)) == []

    (tmp_path / ".grimp_cache").mkdir()

    assert changes(before, snapshot(tmp_path)) == ["created .grimp_cache at the checkout root"]


PUT_BACK: Final[str] = (
    "changed src/package/module.py -- its size and modification time are what they were, its "
    "change time or inode is not"
)
"""What a snapshot says of a file whose size and modification time came back and nothing else."""


def test_a_same_size_rewrite_with_its_modification_time_put_back_is_seen_on_posix(
    tmp_path: Path,
) -> None:
    """D14 review m-2: `x = 1` became `x = 9`, and `os.utime` put the old time back.

    Size and modification time were all a snapshot held, so the review measured the module left
    changed for good and the run green. No user-space call sets a change time back -- a write, a
    `utime` or a rename each moves it -- so on POSIX a snapshot holds it, with the inode. On
    Windows `st_ctime` is when the file was created and is not read, and this stays a blind spot
    the module docstring states; asserted both ways, so the day either side changes, this does.
    """
    module = _scratch_checkout(tmp_path)
    held = module.stat()
    before = snapshot(tmp_path)

    module.write_bytes(b"x = 9\n")
    os.utime(module, ns=(held.st_atime_ns, held.st_mtime_ns))

    assert (module.stat().st_size, module.stat().st_mtime_ns) == (held.st_size, held.st_mtime_ns)
    assert changes(before, snapshot(tmp_path)) == ([] if os.name == "nt" else [PUT_BACK])


def test_a_module_copied_back_with_copy2_is_seen_on_posix(tmp_path: Path) -> None:
    """The review's second route to the same blind spot: plant an import, `copy2` the original back.

    `shutil.copy2` restores the modification time with the bytes, so a probe that set the module
    aside, planted one import in it and copied it back left nothing a size and a modification time
    could see. On POSIX the change time has moved; on Windows this is not seen.
    """
    module = _scratch_checkout(tmp_path)
    aside = tmp_path / "module.py.aside"
    shutil.copy2(module, aside)
    before = snapshot(tmp_path)

    module.write_text(
        "from openalpha_cn.domain.portfolio import PortfolioOrder\n", encoding="utf-8"
    )
    shutil.copy2(aside, module)

    assert changes(before, snapshot(tmp_path)) == ([] if os.name == "nt" else [PUT_BACK])


def test_a_sourceless_pyc_beside_the_sources_is_a_write(tmp_path: Path) -> None:
    """D14 review m-2: a `.pyc` was ignored wherever it stood, so a planted one went unseen.

    The review put `src/package/evil.pyc` there and imported it after the run. CPython writes into
    `__pycache__` and nowhere else, which is now the only place a `.pyc` is ignored.
    """
    module = _scratch_checkout(tmp_path)
    before = snapshot(tmp_path)

    (module.parent / "evil.pyc").write_bytes(b"planted")

    assert changes(before, snapshot(tmp_path)) == ["created src/package/evil.pyc"]


def test_any_listing_change_in_the_window_hides_a_module_created_and_removed_there(
    tmp_path: Path,
) -> None:
    """A stated blind spot, measured: not only a `__pycache__` appearing masks a probe.

    A directory's modification time is read as a created-and-removed entry only while its listing
    is what it was, and a `.DS_Store` appearing -- or any other entry -- changes the listing.
    """
    module = _scratch_checkout(tmp_path)
    before = snapshot(tmp_path)

    (module.parent / ".DS_Store").write_bytes(b"")
    probe = module.parent / "_layering_gate_probe.py"
    probe.write_text("import sqlite3\n", encoding="utf-8")
    probe.unlink()

    assert changes(before, snapshot(tmp_path)) == []


def test_a_file_directly_under_the_root_is_watched_by_its_name_only(tmp_path: Path) -> None:
    """A stated blind spot, measured: the root's listing is held, not its files' contents."""
    _scratch_checkout(tmp_path)
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("[project]\n", encoding="utf-8")
    _aged(manifest)
    before = snapshot(tmp_path)

    manifest.write_text("[project]\nname = 'rewritten'\n", encoding="utf-8")

    assert changes(before, snapshot(tmp_path)) == []


WRITING_PROBE: Final[str] = textwrap.dedent(
    """
    from pathlib import Path

    CHECKOUT = Path(__file__).resolve().parents[1]
    (CHECKOUT / "tests" / "written-while-collecting.txt").write_text("x", encoding="utf-8")


    def test_puts_back_the_bytes_it_found():
        module = CHECKOUT / "src" / "package" / "module.py"
        module.write_bytes(module.read_bytes())


    def test_plants_a_probe_module_and_deletes_it():
        probe = CHECKOUT / "src" / "package" / "_layering_gate_probe.py"
        probe.write_text("import sqlite3\\n", encoding="utf-8")
        probe.unlink()


    def test_leaves_a_cache_at_the_root():
        (CHECKOUT / ".grimp_cache").mkdir()
    """
)
"""A test module writing the checkout it runs in, in the shapes D13 I-D found, and all passing."""

TMP_PATH_PROBE: Final[str] = textwrap.dedent(
    """
    def test_writes_only_its_own_tmp_path(tmp_path):
        (tmp_path / "written.txt").write_text("x", encoding="utf-8")
    """
)


GUARD_MODE_VARIABLE: Final[str] = "OPENALPHA_CHECKOUT_GUARD"
"""The one switch the guard has, spelled here as a developer types it rather than imported."""


def _run_the_hooks_over(
    tmp_path: Path,
    probe: str,
    *options: str,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `probe` in a child pytest whose root is a scratch checkout, with the real conftest.

    `-p conftest` with `tests/` on `PYTHONPATH` loads this repository's own `tests/conftest.py`
    -- `tests/unit/test_offline_suite.py::_run_scope_probe`'s arrangement -- and `--rootdir` makes
    the scratch checkout the root its hooks measure. Unless `options` name one, `--basetemp` sits
    beside the checkout, not in it, so a probe's own `tmp_path` is not a write to what is measured.

    The child gets none of this process's coverage variables and none of its
    `OPENALPHA_CHECKOUT_GUARD`, and `PY_COLORS=0`: a developer running this file under `--cov`,
    in `report` mode or with colour forced must not change what the child is asked to prove.
    `environment` is applied last. Anything a test puts under `tmp_path / "checkout"` first --
    a stale basetemp -- is kept, because the checkout is laid out around it.
    """
    checkout = tmp_path / "checkout"
    _scratch_checkout(checkout)
    (checkout / "tests" / "test_probe.py").write_text(probe, encoding="utf-8")
    child = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COV_CORE_", "COVERAGE_"))
        and name not in {"PYTEST_ADDOPTS", GUARD_MODE_VARIABLE}
    }
    child.update({"PYTHONPATH": str(TESTS_ROOT), "PYTHONDONTWRITEBYTECODE": "1", "PY_COLORS": "0"})
    child.update(environment or {})
    basetemp = (
        []
        if any(option.startswith("--basetemp") for option in options)
        else [f"--basetemp={tmp_path / 'basetemp'}"]
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(checkout / "tests"),
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "--rootdir",
            str(checkout),
            "--confcutdir",
            str(checkout),
            *basetemp,
            *options,
        ],
        cwd=checkout,
        env=child,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _reported(output: str) -> list[str]:
    """The guard's own lines out of a child's output: each change, with its window."""
    return [
        line
        for line in output.splitlines()
        if line.startswith(("while collecting, ", "while the tests ran, ", "before collection"))
    ]


def test_a_run_that_writes_the_checkout_fails_although_every_test_in_it_passed(
    tmp_path: Path,
) -> None:
    """The conftest's hooks, driven: each shape is named, with the window it happened in."""
    finished = _run_the_hooks_over(tmp_path, WRITING_PROBE)
    output = finished.stdout + finished.stderr

    assert "3 passed" in output, output
    assert finished.returncode == 1, output
    assert "this run wrote the checkout it ran from" in output
    for line in (
        "while collecting, created tests/written-while-collecting.txt",
        "while the tests ran, rewrote src/package/module.py",
        "while the tests ran, created and removed something in src/package/",
        "while the tests ran, created .grimp_cache at the checkout root",
    ):
        assert line in output, output


def test_a_run_that_writes_only_its_tmp_path_passes(tmp_path: Path) -> None:
    """The hooks' non-vacuity in the other direction: the same child, nothing written, green."""
    finished = _run_the_hooks_over(tmp_path, TMP_PATH_PROBE)
    output = finished.stdout + finished.stderr

    assert finished.returncode == 0, output
    assert "1 passed" in output, output
    assert "this run wrote the checkout" not in output


RUN_OUTPUT_OPTIONS: Final[tuple[str, ...]] = (
    "--cov=tests",
    "--cov-report=html",
    "--cov-report=xml",
    "--cov-report=json",
    "--cov-report=lcov",
    "--junitxml=report.xml",
)
"""Every file report a run can be asked for, each at its default place: the root it measures.

`htmlcov/`, `coverage.xml`, `coverage.json` and `coverage.lcov` are coverage.py's defaults, and
pytest-cov writes them as its `pytest_runtestloop` wrapper finishes; the junit plugin writes in its
own `pytest_sessionfinish`, which pluggy calls before this conftest's. All of it lands before the
last snapshot is taken."""

REPORTS_THE_OPTIONS_WRITE: Final[tuple[str, ...]] = (
    "htmlcov/index.html",
    "coverage.xml",
    "coverage.json",
    "coverage.lcov",
    "report.xml",
)


@pytest.mark.parametrize("basetemp", ["bt", "tests/bt"])
def test_a_run_s_own_reports_and_basetemp_are_not_writes_to_the_checkout(
    tmp_path: Path, basetemp: str
) -> None:
    """I-1: each of these used to fail a run whose every test had passed.

    Measured on the code before this, one option at a time, each over a probe that writes only its
    `tmp_path`: `--cov-report=html` gave `created htmlcov at the checkout root` and exit 1, and
    `--cov-report=xml`, `--junitxml=report.xml` and a first `--basetemp=bt` did the same for their
    own targets. `--basetemp=tests/bt` failed on *every* run, because pytest empties a given
    basetemp and makes it again: the second run named `tests/` created-and-removed and the old
    basetemp's file rewritten. So the basetemp is made stale here before the run, which is the
    second run's shape, and every report is asserted to exist afterwards -- the green is about
    leaving them out, not about their never having been written.
    """
    checkout = tmp_path / "checkout"
    (checkout / basetemp / "stale").mkdir(parents=True)

    finished = _run_the_hooks_over(
        tmp_path, TMP_PATH_PROBE, *RUN_OUTPUT_OPTIONS, f"--basetemp={basetemp}"
    )
    output = finished.stdout + finished.stderr

    assert finished.returncode == 0, output
    assert "1 passed" in output, output
    assert _reported(output) == [], output
    assert [path for path in REPORTS_THE_OPTIONS_WRITE if not (checkout / path).is_file()] == []
    assert not (checkout / basetemp / "stale").exists(), "pytest did not empty the given basetemp"


def test_leaving_out_a_run_s_own_output_hides_nothing_a_test_wrote(tmp_path: Path) -> None:
    """The exclusion's non-vacuity: the same options over a probe that does write the checkout.

    Every write the probe makes is still named, in its window, and nothing the run wrote for its
    own options is -- a basetemp emptied and made again under `tests/` included, which the probe's
    fourth test, writing only its `tmp_path`, is there to cause.
    """
    (tmp_path / "checkout" / "tests" / "bt" / "stale").mkdir(parents=True)

    finished = _run_the_hooks_over(
        tmp_path, WRITING_PROBE + TMP_PATH_PROBE, *RUN_OUTPUT_OPTIONS, "--basetemp=tests/bt"
    )
    output = finished.stdout + finished.stderr

    assert finished.returncode == 1, output
    assert "4 passed" in output, output
    assert _reported(output) == [
        "while collecting, created tests/written-while-collecting.txt",
        "while the tests ran, rewrote src/package/module.py",
        "while the tests ran, created and removed something in src/package/ -- its modification "
        "time moved and its listing did not",
        "while the tests ran, created .grimp_cache at the checkout root",
    ], output


FAILING_TEST: Final[str] = textwrap.dedent(
    """


    def test_fails_on_its_own_account():
        raise AssertionError("a failure that is the test's own")
    """
)


@pytest.mark.parametrize(
    ("probe", "exit_code", "counts"),
    [(WRITING_PROBE, 0, "3 passed"), (WRITING_PROBE + FAILING_TEST, 1, "1 failed, 3 passed")],
    ids=["every-test-passes", "a-test-fails"],
)
def test_report_mode_prints_the_same_writes_and_leaves_the_exit_status_to_the_tests(
    tmp_path: Path, probe: str, exit_code: int, counts: str
) -> None:
    """`OPENALPHA_CHECKOUT_GUARD=report` is a downgrade, not an off switch.

    The section is printed as in the default `fail` mode, and the exit status is what the tests
    made it -- in both directions, so a mode that turned every run green could not pass here.
    """
    finished = _run_the_hooks_over(tmp_path, probe, environment={GUARD_MODE_VARIABLE: "report"})
    output = finished.stdout + finished.stderr

    assert finished.returncode == exit_code, output
    assert counts in output, output
    assert "this run wrote the checkout it ran from" in output, output
    assert "while the tests ran, rewrote src/package/module.py" in _reported(output), output
    assert f"{GUARD_MODE_VARIABLE}=report: reported only" in output, output


def test_an_unknown_guard_mode_is_refused_before_anything_runs(tmp_path: Path) -> None:
    """A mistyped mode is a usage error, rather than a silent `fail` or a silent `report`."""
    finished = _run_the_hooks_over(
        tmp_path, TMP_PATH_PROBE, environment={GUARD_MODE_VARIABLE: "warn"}
    )
    output = finished.stdout + finished.stderr

    assert finished.returncode == pytest.ExitCode.USAGE_ERROR, output
    assert all(word in output for word in (GUARD_MODE_VARIABLE, "'fail'", "'report'")), output
    assert "passed" not in output, output
