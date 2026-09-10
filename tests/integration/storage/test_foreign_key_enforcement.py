"""Tests for `PRAGMA foreign_keys` enforcement across every `state.sqlite3` store (task 21).

`PRAGMA foreign_keys` is per-*connection* and defaults to off (SQLite's own default). Before
task 21, exactly one of the eight stores that shared `state.sqlite3` at the time
(`SQLiteRunRepository`) turned it on; the other seven did not, so `checkpoints.run_id ->
runs.run_id` and `decisions.run_id -> runs.run_id` -- the two foreign keys this schema declares
(`storage/sqlite.py`) -- were enforced only by whichever connection happened to write them,
not by the schema itself. `storage/connection.py#open_state_connection` is now the single
place every store's `_connect()` opens a connection through, so this file verifies the
property store by store (not just once) plus a real orphan-row rejection that bypasses the
application-level `get_run(...) is None` guards `append_decision`/`append_checkpoint` already
had -- proving SQLite itself, not just those two call sites, now refuses the write.

Task 5 found that "store by store" had quietly become "8 of the 10 stores": `SQLiteJobStore`
(`jobs.py`) and `SQLiteModelUsageStore` (`models.py`) both existed and both opened connections
through `open_state_connection` already, but neither was in `_STORE_FACTORIES` below, so
neither was ever actually checked here. Measured (not assumed): adding both to the registry
made their parametrized cases pass immediately -- both already enforced foreign keys, so this
was a coverage gap, not a live defect, and completing the registry was the whole fix.
`test_store_factories_registry_has_no_undiscovered_state_sqlite3_stores` below exists so the
next store to arrive fails loudly, by class name, instead of leaving the count wrong a third
time.

A follow-up review of that guard found it was keyed on the wrong property -- "the class defines
its own `_connect` and that method's bytecode names `open_state_connection`" -- which five
synthetic store shapes could dodge four different ways (missing `_connect` entirely, an
inherited one, one that delegates instead of calling `open_state_connection` itself, and one
that lives in an undiscovered submodule). `_state_sqlite3_store_classes` below is now keyed on
this package's own `SQLite`-prefixed naming convention instead, which none of those four shapes
can dodge because none of them change the class's name; its docstring has the full account of
the five attacks and exactly what the new property does and does not catch.

The gap that gets read past, stated here instead of only in a function docstring further down
this file: the naming-prefix property that `_state_sqlite3_store_classes` is keyed on verifies
only that a class is named the way this package's ten stores happen to be named today. A future
`state.sqlite3`-backed store whose class does not start with `SQLite` -- copied from some other
convention, or just misspelled -- is invisible to every discovery-based check in this file: not
caught, not warned about, not even counted as "unregistered." `_state_sqlite3_store_classes`'s
own docstring below has the full account of what this naming property does and does not catch;
this paragraph exists so a reader who stops at the top of this file still meets the boundary
instead of believing it is closed.

A later review attacked a different assumption: `inspect.getmembers(module, inspect.isclass)`
can only see classes bound as an attribute of the module object itself, so a `SQLite`-prefixed
class defined *inside a function* and never assigned at module scope -- built by a factory, say
-- never becomes one, and was invisible regardless of its name or how honestly its `_connect`
called `open_state_connection`. `_sqlite_prefixed_class_definitions_in_source` below closes that
hole by parsing source text with `ast` instead of inspecting runtime module attributes, which
does not depend on module-scope binding at all; see its docstring and the test built on it for
the measured attack this closes.

A fourth, independent audit, `_bare_sqlite3_connect_call_sites`, does not look at classes or
their names at all. It parses every module under this package for a bare `sqlite3.connect(`
call outside `connection.py` itself, per function rather than per file, which is what actually
tests that module's own docstring claim that every store's `_connect()` goes through
`open_state_connection` -- and it catches a bypass no matter what the offending class or
function is called, which is the one gap the naming-prefix property above admits by design that
it cannot close.
"""

import ast
import importlib
import inspect
import pkgutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import openalpha_cn.storage as storage_package
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.connection import open_state_connection
from openalpha_cn.storage.jobs import SQLiteJobStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.models import SQLiteModelUsageStore
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore, SQLiteWatchlistStore
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository
from openalpha_cn.storage.validation import SQLiteValidationStore

# Every store that opens a connection to the shared `state.sqlite3` file, by name, so a
# failure names exactly which store regressed instead of "some store, somewhere." Task 5
# found this registry covered only 8 of the 10 (`SQLiteJobStore`, `SQLiteModelUsageStore`
# were missing); `test_store_factories_registry_has_no_undiscovered_state_sqlite3_stores`
# below now fails by name if a future eleventh store is added here and not to this dict.
_STORE_FACTORIES = {
    "SQLiteRunRepository": SQLiteRunRepository,
    "SQLiteResearchMemory": SQLiteResearchMemory,
    "SQLitePortfolioLedger": SQLitePortfolioLedger,
    "SQLiteWatchlistStore": SQLiteWatchlistStore,
    "SQLiteReportStore": SQLiteReportStore,
    "SQLiteRecoveryStore": SQLiteRecoveryStore,
    "SQLiteBatchTaskStore": SQLiteBatchTaskStore,
    "SQLiteValidationStore": SQLiteValidationStore,
    "SQLiteJobStore": SQLiteJobStore,
    "SQLiteModelUsageStore": SQLiteModelUsageStore,
}

# Classes in `openalpha_cn.storage` whose name matches the `SQLite`-prefix naming convention
# every real `state.sqlite3` store in this package uses, but that are deliberately not one of
# the ten stores `_STORE_FACTORIES` tracks -- named individually, with a reason. A review found
# that sentence was aspirational rather than measured: an entry with reason `""`, and an entry
# naming a class that has never existed in this package, both left every other test in this file
# green (see task-5-report.md's second follow-up section). The two tests immediately after
# `_state_sqlite3_store_classes` below -- `test_not_a_state_sqlite3_store_exemptions_carry_a_
# real_reason` and `test_not_a_state_sqlite3_store_exemptions_name_a_class_discovery_can_still_
# see` -- are what makes both halves of that sentence true now instead of merely stated.
# Empty today: no `SQLite`-prefixed class in this package is anything other than a real
# state.sqlite3 store (verified by `_state_sqlite3_store_classes` returning exactly the ten
# `_STORE_FACTORIES` names above, no more and no fewer -- see task-5-report.md's first
# follow-up section). The dict stays here, empty, rather than not existing, so the day a
# `SQLite`-prefixed non-store class is added, naming it here is the obvious next step instead
# of a new blind spot.
_NOT_A_STATE_SQLITE3_STORE: dict[str, str] = {}


def _iter_storage_submodules() -> Iterator[ModuleType]:
    """Import and yield every actual submodule of `openalpha_cn.storage`, recursively.

    The one place every discovery helper in this file gets its module list from, so there is
    exactly one answer to "what counts as this package's own submodules" --
    `pkgutil.walk_packages`, recursive unlike `iter_modules` (see attack 4 in
    `_state_sqlite3_store_classes` below for why that distinction was the fix for one of its
    five attacks).
    """
    for module_info in pkgutil.walk_packages(
        storage_package.__path__, prefix=f"{storage_package.__name__}."
    ):
        yield importlib.import_module(module_info.name)


def _sqlite_prefixed_module_level_classes() -> dict[str, type]:
    """Every `SQLite`-prefixed class bound at module scope in `openalpha_cn.storage`, before
    `_NOT_A_STATE_SQLITE3_STORE` removes anything -- i.e. exactly what `_state_sqlite3_store_
    classes` below would return if that dict were empty.

    Exists so an exemption can be checked against "does discovery still see a class of this
    name at all" without asking the already-exemption-filtered result, which would make every
    exemption look stale by definition -- the whole point of an entry is to make its own name
    disappear from that result -- and so could never catch a genuinely stale one. See
    `test_not_a_state_sqlite3_store_exemptions_name_a_class_discovery_can_still_see` below.
    """
    discovered: dict[str, type] = {}
    for module in _iter_storage_submodules():
        for name, candidate in inspect.getmembers(module, inspect.isclass):
            if candidate.__module__ != module.__name__:
                continue  # imported into this module, not defined in it -- credited elsewhere
            if not name.startswith("SQLite"):
                continue
            discovered[name] = candidate
    return discovered


def _sqlite_prefixed_class_definitions_in_source() -> set[str]:
    """Every class *defined* anywhere in `openalpha_cn.storage`'s own source, at any nesting
    depth, whose name starts with `SQLite` -- found by parsing source text with `ast`, not by
    inspecting runtime module attributes.

    `_sqlite_prefixed_module_level_classes` above (and the `inspect.getmembers(module,
    inspect.isclass)` it is built on) can only see classes bound as an attribute of the module
    object itself. A class defined *inside a function* -- built and returned by a factory, say
    -- is invisible to it no matter what the class is named or how honestly its `_connect` is
    written, because such a class is never a module attribute at all: it exists, if it ever
    does, only as a local value inside that function's own call frame, created the moment the
    function runs and reachable from nowhere else afterwards. `ast.walk` has no such blind
    spot, because it never runs the code -- it finds every `class` statement in the source text
    itself, at whatever nesting depth, including one written inside a function or a class body.

    Measured: `test_no_sqlite_prefixed_class_escapes_discovery_by_hiding_outside_module_scope`
    below builds exactly this shape as a real file under `openalpha_cn/storage/` -- a
    module-level factory function that defines and returns a class named
    `SQLiteFactoryProbeStore`, with an honest `_connect` calling `open_state_connection`, never
    assigned to a module-level name. Before this function existed,
    `test_store_factories_registry_has_no_undiscovered_state_sqlite3_stores` said nothing about
    it at all, not even "unregistered": `_sqlite_prefixed_module_level_classes` never saw it,
    so it never appeared on either side of that comparison. This function does see it, because
    the `class SQLiteFactoryProbeStore:` statement is in the source regardless of where it is
    nested.
    """
    names: set[str] = set()
    for module in _iter_storage_submodules():
        if module.__file__ is None:
            continue
        source_path = Path(module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.startswith("SQLite"):
                names.add(node.name)
    return names


class _BareSqlite3ConnectVisitor(ast.NodeVisitor):
    """Collect every bare `sqlite3.connect(...)` call in a module's AST, each tagged with the
    dotted name of its innermost enclosing function (or method, qualified by its class) --
    `<module>` for a call sitting at module scope, outside any function at all.

    A plain `ast.walk` cannot report *which function* a call sits inside, since `ast` nodes
    carry no parent pointer; this visitor tracks that by pushing the name of every `def` (or
    `class`) it descends into onto a stack and popping it back off on the way out, so a call
    found while the stack is `["Migrations", "run_migrations"]` is correctly attributed to
    `Migrations.run_migrations`, not to the module or to some other function defined earlier in
    the same file.
    """

    def __init__(self) -> None:
        self.call_sites: list[tuple[str, int]] = []
        self._scope_stack: list[str] = []

    def _qualname(self) -> str:
        return ".".join(self._scope_stack) if self._scope_stack else "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        is_bare_sqlite3_connect = (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Name)
            and func.value.id == "sqlite3"
        )
        if is_bare_sqlite3_connect:
            self.call_sites.append((self._qualname(), node.lineno))
        self.generic_visit(node)


def _bare_sqlite3_connect_call_sites() -> dict[str, list[int]]:
    """`{"<module qualname>::<function qualname>": [line numbers]}` for every bare
    `sqlite3.connect(...)` call anywhere under `openalpha_cn.storage`, excluding
    `connection.py` itself -- the one file `connection.py`'s own module docstring names as the
    single place this package calls `sqlite3.connect()` directly.

    `storage/connection.py:44` states plainly: "Every store's `_connect()` in this package
    calls this instead of `sqlite3.connect()`." Nothing tested that claim until this function
    and the audit test built on it -- and unlike `_sqlite_prefixed_module_level_classes` above,
    this one does not look at class names at all, so it catches a bypass regardless of what the
    offending class (or free function) is called, which is exactly the case the naming-prefix
    predicate admits by design that it cannot see (its own docstring says so).

    Grouped by function, not by file, deliberately: a file-level pass here would let a bare
    call inside a *different* function of the same file through unnoticed the moment one
    function in that file is exempted, which is the exact mistake `_NOT_A_STATE_SQLITE3_STORE`
    was rewritten to stop making (see task-5-report.md's second follow-up section).

    What this does not catch, stated rather than assumed: it matches only the literal call
    shape `sqlite3.connect(...)` (an `ast.Attribute` named `connect` on an `ast.Name` named
    `sqlite3`). An aliased import (`import sqlite3 as db`, then `db.connect(...)`) or a
    `from sqlite3 import connect` (then a bare `connect(...)`) would not be matched. Checked,
    not assumed: `grep -rn -e "import sqlite3 as " -e "from sqlite3 import"
    src/openalpha_cn/storage/` returns nothing -- every one of this package's eleven `sqlite3`
    imports (`connection.py` included) spells it `import sqlite3`, so this boundary is real but
    not exercised by anything in this package today.
    """
    sites: dict[str, list[int]] = {}
    for module in _iter_storage_submodules():
        if module.__name__ == "openalpha_cn.storage.connection":
            continue
        if module.__file__ is None:
            continue
        source_path = Path(module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        visitor = _BareSqlite3ConnectVisitor()
        visitor.visit(tree)
        for function_qualname, lineno in visitor.call_sites:
            key = f"{module.__name__}::{function_qualname}"
            sites.setdefault(key, []).append(lineno)
    return sites


# Per-function exemptions for `_bare_sqlite3_connect_call_sites` above, each requiring a
# non-blank reason -- the same discipline `_NOT_A_STATE_SQLITE3_STORE` now enforces, and for
# the same reason: a blank or stale entry here would be exactly as much of a hiding place as
# one there was. `test_sqlite3_connect_bypass_exemptions_carry_a_real_reason` and
# `test_sqlite3_connect_bypass_exemptions_name_a_call_site_that_still_exists` below enforce
# both halves; see them, not this comment, for what is actually checked.
_SQLITE3_CONNECT_BYPASS_EXEMPTIONS: dict[str, str] = {
    "openalpha_cn.storage.migrations::_take_backup": (
        "Uses SQLite's page-level `.backup()` API (source.backup(destination)), not SQL DML on "
        "either connection -- `PRAGMA foreign_keys` has no write for it to apply to."
    ),
    "openalpha_cn.storage.migrations::read_status": (
        "Read-only by its own docstring ('never takes a backup, never opens a write "
        "transaction'); `PRAGMA foreign_keys` only changes the outcome of a write."
    ),
    "openalpha_cn.storage.migrations::run_migrations": (
        "Needs isolation_level=None for its own manual BEGIN IMMEDIATE / COMMIT / ROLLBACK, "
        "which open_state_connection(path, *, timeout=10) does not accept."
    ),
}


def _state_sqlite3_store_classes() -> dict[str, type]:
    """Discover, from the package itself, every class that is a `state.sqlite3` store.

    A follow-up review attacked this function's previous version -- "a class counts as a store
    if it defines its own `_connect` in `__dict__` and that method's bytecode names
    `open_state_connection`" -- with five synthetic store shapes placed as real files under
    `openalpha_cn/storage/`, run through this function, and deleted again. Measured, in order:

    1. A store with **no** `_connect` at all, opening `sqlite3.connect()` directly with foreign
       keys left off -- the case that matters most, because it is exactly what
       `storage/connection.py`'s own docstring warns this package is one copy-paste away from:
       an author who never knew `_connect`/`open_state_connection` was a convention to follow
       in the first place. Old result: **not discovered** -- nothing in `__dict__` named
       `_connect` to inspect.
    2. A store whose `_connect` is inherited from a base class rather than defined on the store
       itself. Old result: **not discovered** -- `candidate.__dict__.get("_connect")` looks at
       the class's own `__dict__` only, deliberately not the resolved (MRO-walked) attribute,
       so the subclass came back empty-handed; only the *base* class showed up, under its own
       name, when the base itself also happened to satisfy the check.
    3. A store whose `_connect` calls `self._helper()` (or a module-level function), which is
       what actually calls `open_state_connection`. Old result: **not discovered** --
       `open_state_connection` never appears in `_connect`'s own `co_names`, only its helper's,
       and the old check never looked past the one method.
    4. A store defined in a submodule (`storage/foo/bar.py`) and re-exported from
       `foo/__init__.py`. Old result: **not discovered** -- `pkgutil.iter_modules` lists only
       one level of `storage/`'s own path, so `foo/bar.py` was never visited as a module in its
       own right, and the re-export visible on `foo/__init__` was correctly filtered out by the
       `candidate.__module__ != module.__name__` check below (it truthfully is not defined
       there), leaving no module under which it was ever credited.
    5. A store with `_connect = lambda self: open_state_connection(self.path)`. Old result:
       **discovered correctly** -- a lambda is a plain function object with a `__code__`, found
       in its own class's `__dict__`, so this shape was never the problem.

    The property this function is keyed on now does not read `_connect` at all, so none of the
    first four holes apply to it: **the class's name starts with the literal prefix `SQLite`.**
    Every one of the ten current stores is named that way (`SQLiteRunRepository`,
    `SQLiteJobStore`, ...); every class in this package that is not one of the ten --
    the file/DuckDB-backed stores (`FileExperimentStore`, `FilePredictionStore`,
    `FileShortlistStore`, `ParquetEvidenceStore`) and every exception, dataclass or result type
    the package also defines (`JobStoreError`, `RunRecoveryState`, `Migration`, ...) -- is not.
    `_NOT_A_STATE_SQLITE3_STORE` above is the named, individual escape hatch for the day a
    `SQLite`-prefixed class exists here that is deliberately not one of the ten; there is no
    such class today, which is why the dict above is empty rather than pre-populated.

    Told just as plainly, what this still does **not** catch: a future `state.sqlite3`-backed
    store whose class is named without the `SQLite` prefix -- a plain `JobStore`, say, copied in
    without following this package's own convention. None of the five attacks above exercise
    that gap (all five keep the prefix, varying only how `_connect` is shaped), so re-keying
    discovery this way closes every hole the review measured without claiming to close a sixth,
    untested one. The convention itself is a matter for code review, not for this function.

    Mechanically: walks `openalpha_cn.storage`'s actual submodules with `pkgutil.walk_packages`
    -- recursive, unlike `iter_modules`, so attack 4's submodule is now visited as a module in
    its own right -- and, per module, keeps a class only when `candidate.__module__` equals the
    module being inspected, so a class merely imported into a module (a re-export, or an
    unrelated import like `pathlib.Path`) is credited to the module that actually defines it,
    never double-counted and never dropped between two modules that both decline to claim it.
    """
    return {
        name: candidate
        for name, candidate in _sqlite_prefixed_module_level_classes().items()
        if name not in _NOT_A_STATE_SQLITE3_STORE
    }


def test_store_factories_registry_has_no_undiscovered_state_sqlite3_stores() -> None:
    """Fail, naming names, the moment a store class exists that `_STORE_FACTORIES` does not.

    This is the guard task 5 asks for: completing `_STORE_FACTORIES` by hand (adding
    `SQLiteJobStore` and `SQLiteModelUsageStore`) closes today's gap but leaves the eleventh
    store's author with nothing to tell them the same list needs a new entry. This test is
    that something -- it discovers the real set of store classes from the package
    (`_state_sqlite3_store_classes`, above) and compares it against the registry, so an
    eleventh store missing from `_STORE_FACTORIES` fails *this* test by class name instead of
    leaving `test_every_store_connection_enforces_foreign_keys` silently short one case.

    A follow-up review found the original version of `_state_sqlite3_store_classes` missed most
    ways an eleventh store could actually be written; see that function's docstring for the
    five measured attacks and the naming-convention property discovery is keyed on now instead.
    """
    missing = sorted(set(_state_sqlite3_store_classes()) - set(_STORE_FACTORIES))
    assert not missing, (
        "these openalpha_cn.storage classes open a state.sqlite3 connection through "
        "open_state_connection() but are missing from _STORE_FACTORIES above, so "
        f"test_every_store_connection_enforces_foreign_keys never checks them: {missing}. "
        "Add each one to _STORE_FACTORIES."
    )


def test_store_factories_registry_has_no_entries_that_are_no_longer_state_sqlite3_stores() -> None:
    """Fail, naming names, if a `_STORE_FACTORIES` entry no longer matches a discovered class.

    The complementary direction to the test above: `discovered - registered` (above) catches an
    undiscovered store missing from the registry; `registered - discovered` (here) catches a
    registry entry that no longer corresponds to anything `_state_sqlite3_store_classes` still
    finds -- for example, a store renamed away from the `SQLite` prefix, or moved into
    `_NOT_A_STATE_SQLITE3_STORE`, without its `_STORE_FACTORIES` entry being removed to match.

    What this does **not** cover, on purpose, measured rather than assumed: a registered store
    whose connection behavior regresses without its name changing. Temporarily editing
    `SQLiteJobStore._connect` to bypass `open_state_connection` (a bare, foreign-keys-off
    `sqlite3.connect()` instead) leaves both this test and the one above green, because naming
    is the only property either direction of this comparison looks at now -- neither one
    touches `_connect` at all. `test_every_store_connection_enforces_foreign_keys[SQLiteJobStore]`
    is what catches that regression: it opens a real connection through the registered factory
    and asserts `PRAGMA foreign_keys` itself, independent of how discovery works. Deleting a
    store class outright is covered even more bluntly, and needs no test of its own: this file's
    own module-level `from openalpha_cn.storage.jobs import SQLiteJobStore` (and the other nine)
    fails at import/collection time, before any test in this file runs at all.
    """
    extra = sorted(set(_STORE_FACTORIES) - set(_state_sqlite3_store_classes()))
    assert not extra, (
        "these _STORE_FACTORIES entries no longer match any state.sqlite3 store class "
        f"discovered in openalpha_cn.storage: {extra}. Either the class was renamed away from "
        "the `SQLite` prefix, or it was added to _NOT_A_STATE_SQLITE3_STORE -- update "
        "_STORE_FACTORIES (or the class) to match."
    )


def test_not_a_state_sqlite3_store_exemptions_carry_a_real_reason() -> None:
    """Fail, naming names, if any `_NOT_A_STATE_SQLITE3_STORE` reason is blank.

    Measured before this test existed: temporarily giving an entry the reason `""` left every
    other test in this file green -- the comment above the dict claimed each entry is "named
    individually, with a reason," but nothing checked that the reason said anything. The dict
    is empty today, so this test is exercised with a temporary entry rather than anything
    currently in it; see task-5-report.md's second follow-up section for the measured red this
    test now produces, and its later green once the entry is removed again.
    """
    blank = sorted(
        name for name, reason in _NOT_A_STATE_SQLITE3_STORE.items() if not reason.strip()
    )
    assert not blank, (
        f"these _NOT_A_STATE_SQLITE3_STORE entries have a blank (or whitespace-only) reason, "
        f"which is the same silent catch-all the comment above the dict says this cannot "
        f"become: {blank}. Give each one an actual reason."
    )


def test_not_a_state_sqlite3_store_exemptions_name_a_class_discovery_can_still_see() -> None:
    """Fail, naming names, if any `_NOT_A_STATE_SQLITE3_STORE` key is not a class the naming
    predicate actually finds today.

    Measured before this test existed: temporarily exempting a class name that has never
    existed in this package, with a non-blank filler reason, also left every other test in this
    file green -- a stale exemption like that is exactly the hiding place
    `_NOT_A_STATE_SQLITE3_STORE` was added to name individually instead of allowing. Compared
    against `_sqlite_prefixed_module_level_classes` (before exemptions), not against
    `_state_sqlite3_store_classes` (after exemptions) -- the latter would make every entry look
    stale by definition, since making its own name disappear from that result is what an
    exemption does.
    """
    stale = sorted(set(_NOT_A_STATE_SQLITE3_STORE) - set(_sqlite_prefixed_module_level_classes()))
    assert not stale, (
        f"these _NOT_A_STATE_SQLITE3_STORE keys do not name any `SQLite`-prefixed class "
        f"currently defined at module scope in openalpha_cn.storage: {stale}. A stale "
        f"exemption is a hiding place -- remove the entry, or fix the class name to match."
    )


def test_no_sqlite_prefixed_class_escapes_discovery_by_hiding_outside_module_scope() -> None:
    """Fail, naming names, if a `SQLite`-prefixed class is defined in this package's source but
    is not visible at module scope to `_sqlite_prefixed_module_level_classes`.

    A follow-up review's sixth attack: `inspect.getmembers(module, inspect.isclass)` (what
    every check above is ultimately built on) can only see classes bound as an attribute of the
    module object -- a class defined inside a factory function, `SQLite` prefix and honest
    `_connect` included, is invisible to it regardless. Measured directly: with such a class
    placed as a real file under `openalpha_cn/storage/`,
    `test_store_factories_registry_has_no_undiscovered_state_sqlite3_stores` passed -- said
    nothing at all, not even "unregistered" -- because the class never reached either side of
    that test's comparison. This test closes that hole with a second, independent discovery
    pass keyed on the same source text `ast` sees regardless of nesting; see
    `_sqlite_prefixed_class_definitions_in_source` above for the full account and the measured
    red this test produced before the probe file was removed again.
    """
    invisible = sorted(
        _sqlite_prefixed_class_definitions_in_source()
        - set(_sqlite_prefixed_module_level_classes())
    )
    assert not invisible, (
        f"these `SQLite`-prefixed classes are defined somewhere in openalpha_cn/storage's own "
        f"source but are not visible as a module-level attribute to "
        f"_sqlite_prefixed_module_level_classes, so no test in this file ever checks them: "
        f"{invisible}. Bind the class at module scope (the fix every real store already "
        f"follows), or -- if it is deliberately not a store -- rename it away from the "
        f"`SQLite` prefix."
    )


def test_no_bare_sqlite3_connect_call_outside_connection_py_without_a_named_exemption() -> None:
    """Fail, naming names, if any function under `openalpha_cn.storage` (other than
    `connection.py` itself) calls `sqlite3.connect()` directly instead of
    `open_state_connection()`, unless that function is named in
    `_SQLITE3_CONNECT_BYPASS_EXEMPTIONS` above.

    This is the test that actually checks `storage/connection.py:44`'s claim -- "Every store's
    `_connect()` in this package calls this instead of `sqlite3.connect()`" -- which nothing
    tested before this file existed. It is broader than that sentence in one direction (it
    audits every function in the package, not only stores' `_connect()` methods) and narrower
    in another (it does not care what the calling class or function is named, only whether it
    bypasses the shared opener) -- which is exactly why it catches what
    `_sqlite_prefixed_module_level_classes` and `_sqlite_prefixed_class_definitions_in_source`
    above admit by design that they cannot: a store that bypasses `open_state_connection`
    regardless of what its class is called.

    Today's four real bare calls, all in `migrations.py`, were each read and confirmed
    legitimate (see `_SQLITE3_CONNECT_BYPASS_EXEMPTIONS` above for why): `_take_backup` (two
    calls, SQLite's own page-level backup API), `read_status` (read-only), and `run_migrations`
    (needs `isolation_level=None` for manual transaction control). Before that dict held those
    three entries, this test failed naming exactly those three functions -- see
    task-5-report.md's second follow-up section for the measured red.
    """
    sites = _bare_sqlite3_connect_call_sites()
    unexempted = sorted(set(sites) - set(_SQLITE3_CONNECT_BYPASS_EXEMPTIONS))
    assert not unexempted, (
        f"these functions call sqlite3.connect() directly instead of "
        f"openalpha_cn.storage.connection.open_state_connection(): {unexempted}. Either switch "
        f"them to open_state_connection, or -- if there is a specific reason this call must "
        f"bypass it, as with the migrations.py functions already named above -- add a named, "
        f"reasoned entry to _SQLITE3_CONNECT_BYPASS_EXEMPTIONS."
    )


def test_sqlite3_connect_bypass_exemptions_carry_a_real_reason() -> None:
    """Fail, naming names, if any `_SQLITE3_CONNECT_BYPASS_EXEMPTIONS` reason is blank.

    Same discipline as `test_not_a_state_sqlite3_store_exemptions_carry_a_real_reason` above,
    applied to this file's other exemption dict so it cannot quietly repeat the defect that
    test exists to close.
    """
    blank = sorted(
        name for name, reason in _SQLITE3_CONNECT_BYPASS_EXEMPTIONS.items() if not reason.strip()
    )
    assert not blank, (
        f"these _SQLITE3_CONNECT_BYPASS_EXEMPTIONS entries have a blank (or whitespace-only) "
        f"reason: {blank}. Give each one an actual reason."
    )


def test_sqlite3_connect_bypass_exemptions_name_a_call_site_that_still_exists() -> None:
    """Fail, naming names, if any `_SQLITE3_CONNECT_BYPASS_EXEMPTIONS` key no longer names a
    function `_bare_sqlite3_connect_call_sites` still finds a bare call in -- for example, one
    whose bare call was since switched to `open_state_connection`, leaving a now-pointless
    entry sitting in the dict as a hiding place for the next real bypass in that same function.

    Same discipline as `test_not_a_state_sqlite3_store_exemptions_name_a_class_discovery_can_
    still_see` above, applied to this file's other exemption dict.
    """
    stale = sorted(
        set(_SQLITE3_CONNECT_BYPASS_EXEMPTIONS) - set(_bare_sqlite3_connect_call_sites())
    )
    assert not stale, (
        f"these _SQLITE3_CONNECT_BYPASS_EXEMPTIONS keys no longer name a function with a bare "
        f"sqlite3.connect() call: {stale}. Remove the entry -- its bypass was fixed, or never "
        f"existed at this name."
    )


def test_open_state_connection_turns_foreign_keys_on(tmp_path: Path) -> None:
    connection = open_state_connection(tmp_path / "state.sqlite3")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_a_fresh_sqlite3_connection_defaults_foreign_keys_off(tmp_path: Path) -> None:
    """Ground truth for why every store must opt in explicitly: SQLite itself does not."""
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("store_name", sorted(_STORE_FACTORIES))
def test_every_store_connection_enforces_foreign_keys(store_name: str, tmp_path: Path) -> None:
    """Verify each of the ten stores individually -- not just one -- per the brief:
    a fix that only proved `SQLiteRunRepository` (the one store that already had it) would
    have missed exactly the gap this task closes. (Task 5: two of the ten -- `SQLiteJobStore`
    and `SQLiteModelUsageStore` -- were missing from `_STORE_FACTORIES` and so never ran as
    part of this parametrization at all; see the module docstring.)"""
    factory = _STORE_FACTORIES[store_name]
    store = factory(tmp_path / "state.sqlite3")
    connection = store._connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_writing_an_orphan_checkpoint_directly_is_rejected_by_sqlite(tmp_path: Path) -> None:
    """A raw SQL write to `checkpoints` for a `run_id` that was never inserted into `runs`
    must fail at the database layer, not merely be caught by
    `SQLiteRunRepository.append_checkpoint`'s own `get_run(...) is None` guard (which this
    test bypasses on purpose, going straight through `_connect()`)."""
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    connection = repository._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute(
                "INSERT INTO checkpoints (run_id, payload) VALUES (?, ?)",
                ("orphan_run_never_inserted", "{}"),
            )
    finally:
        connection.close()


def test_writing_an_orphan_decision_directly_is_rejected_by_sqlite(tmp_path: Path) -> None:
    """Same proof as above for the other declared foreign key, `decisions.run_id`."""
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    connection = repository._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute(
                "INSERT INTO decisions (decision_id, run_id, payload) VALUES (?, ?, ?)",
                ("dec_orphan", "orphan_run_never_inserted", "{}"),
            )
    finally:
        connection.close()
