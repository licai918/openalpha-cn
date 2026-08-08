"""Tests for `PRAGMA foreign_keys` enforcement across every `state.sqlite3` store (task 21).

`PRAGMA foreign_keys` is per-*connection* and defaults to off (SQLite's own default). Before
this task, exactly one of the eight stores that share `state.sqlite3` (`SQLiteRunRepository`)
turned it on; the other seven did not, so `checkpoints.run_id -> runs.run_id` and
`decisions.run_id -> runs.run_id` -- the two foreign keys this schema declares
(`storage/sqlite.py`) -- were enforced only by whichever connection happened to write them,
not by the schema itself. `storage/connection.py#open_state_connection` is now the single
place every store's `_connect()` opens a connection through, so this file verifies the
property store by store (not just once) plus a real orphan-row rejection that bypasses the
application-level `get_run(...) is None` guards `append_decision`/`append_checkpoint` already
had -- proving SQLite itself, not just those two call sites, now refuses the write.
"""

import sqlite3
from pathlib import Path

import pytest

from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.connection import open_state_connection
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore, SQLiteWatchlistStore
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository
from openalpha_cn.storage.validation import SQLiteValidationStore

# Every store that opens a connection to the shared `state.sqlite3` file, by name, so a
# failure names exactly which store regressed instead of "some store, somewhere."
_STORE_FACTORIES = {
    "SQLiteRunRepository": SQLiteRunRepository,
    "SQLiteResearchMemory": SQLiteResearchMemory,
    "SQLitePortfolioLedger": SQLitePortfolioLedger,
    "SQLiteWatchlistStore": SQLiteWatchlistStore,
    "SQLiteReportStore": SQLiteReportStore,
    "SQLiteRecoveryStore": SQLiteRecoveryStore,
    "SQLiteBatchTaskStore": SQLiteBatchTaskStore,
    "SQLiteValidationStore": SQLiteValidationStore,
}


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
    """Verify each of the eight stores individually -- not just one -- per the brief:
    a fix that only proved `SQLiteRunRepository` (the one store that already had it) would
    have missed exactly the gap this task closes."""
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
