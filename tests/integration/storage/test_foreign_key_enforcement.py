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


def _state_sqlite3_store_classes() -> dict[str, type]:
    """Discover, from the package itself, every class that opens `state.sqlite3` connections.

    Task 5: `_STORE_FACTORIES` above is a hand-written list, and it had silently fallen behind
    -- `SQLiteJobStore` (`jobs.py`) and `SQLiteModelUsageStore` (`models.py`) both opened
    connections this file never checked. Re-typing a second hand-written list of class names
    to check the first one against would only move the drift, not close it, so this instead
    walks `openalpha_cn.storage`'s actual submodules with `pkgutil.iter_modules` -- the same
    set `import`ing the package for real would see -- and inspects what it finds.

    A class counts as a "state.sqlite3 store" here if it defines its own `_connect` method
    (not inherited) whose body calls `open_state_connection`, the one function every such
    connection in this package is required to go through (`storage/connection.py`). That is
    the property this whole test file exists to check, so keying discovery on it -- rather
    than on some incidental shape -- is what makes the two outliers `storage/connection.py`'s
    own docstring calls out come out right instead of getting misclassified:

    - `SQLiteValidationStore.__init__` opens **no** connection at all (its table is created
      by a `storage/migrations.py` migration, not the constructor) -- a guard keyed on "the
      constructor opens a connection" would miss it entirely.
    - `SQLiteJobStore.__init__` does open one, but through a different idiom than the other
      constructor-opening stores: a single `with closing(self._connect()) as connection:`
      block ending in an explicit `connection.commit()`, rather than the
      `with closing(self._connect()) as connection, connection:` double-context most others
      use, whose own `__exit__` commits automatically -- a guard keyed on that specific shape
      would miss it too.

    Both still define the one-line `_connect(self) -> sqlite3.Connection: return
    open_state_connection(self.path)` every other store does, so keying on `_connect`'s body
    finds all ten uniformly, `storage/product.py`'s two classes included (counting classes,
    not files, is what makes those two show up separately rather than as one).
    """
    discovered: dict[str, type] = {}
    for module_info in pkgutil.iter_modules(
        storage_package.__path__, prefix=f"{storage_package.__name__}."
    ):
        module = importlib.import_module(module_info.name)
        for name, candidate in inspect.getmembers(module, inspect.isclass):
            if candidate.__module__ != module.__name__:
                continue  # imported into this module, not defined in it -- already seen there
            connect = candidate.__dict__.get("_connect")
            if connect is None:
                continue
            if "open_state_connection" not in connect.__code__.co_names:
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
    """
    missing = sorted(set(_state_sqlite3_store_classes()) - set(_STORE_FACTORIES))
    assert not missing, (
        "these openalpha_cn.storage classes open a state.sqlite3 connection through "
        "open_state_connection() but are missing from _STORE_FACTORIES above, so "
        f"test_every_store_connection_enforces_foreign_keys never checks them: {missing}. "
        "Add each one to _STORE_FACTORIES."
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
