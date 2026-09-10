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
"""

import importlib
import inspect
import pkgutil
import sqlite3
from pathlib import Path

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
# the ten stores `_STORE_FACTORIES` tracks -- named individually, with a reason, so this dict
# cannot become a silent catch-all the way the discovery predicate it patches around used to be.
# Empty today: no `SQLite`-prefixed class in this package is anything other than a real
# state.sqlite3 store (verified by `_state_sqlite3_store_classes` returning exactly the ten
# `_STORE_FACTORIES` names above, no more and no fewer -- see task-5-report.md's follow-up
# section). The dict stays here, empty, rather than not existing, so the day a `SQLite`-prefixed
# non-store class is added, naming it here is the obvious next step instead of a new blind spot.
_NOT_A_STATE_SQLITE3_STORE: dict[str, str] = {}


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
    discovered: dict[str, type] = {}
    for module_info in pkgutil.walk_packages(
        storage_package.__path__, prefix=f"{storage_package.__name__}."
    ):
        module = importlib.import_module(module_info.name)
        for name, candidate in inspect.getmembers(module, inspect.isclass):
            if candidate.__module__ != module.__name__:
                continue  # imported into this module, not defined in it -- credited elsewhere
            if not name.startswith("SQLite"):
                continue
            if name in _NOT_A_STATE_SQLITE3_STORE:
                continue
            discovered[name] = candidate
    return discovered


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
