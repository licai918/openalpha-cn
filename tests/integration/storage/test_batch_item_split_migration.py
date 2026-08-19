"""`split_batch_task_items` (V2-P4-019), against a database written in the pre-split shape.

The fixture writes `batch_tasks.payload` by hand, with every item inside it, because today's
`SQLiteBatchTaskStore` can no longer produce that shape -- which is the whole point: the
migration has to be tested against the rows it exists to move, not against rows already in
the destination shape.

Why the shape moved is `storage/batch.py`'s module docstring: with every item in one blob,
recording one item's transition re-serialized and re-validated every item in the batch, so a
batch cost O(N^2) and a whole-market batch never finished.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from openalpha_cn.batch_contracts import BatchResearchTask, BatchResultRef, BatchTaskItem
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.migrations import (
    SPLIT_BATCH_TASK_ITEMS_VERSION,
    BatchItemSplitError,
    MigrationNotYetApplicable,
    _split_batch_task_items,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
DIGEST: Final[str] = "a" * 64


def _item(index: int, *, succeeded: bool) -> BatchTaskItem:
    request = ResearchRunRequest(
        run_id=f"run_pre_split_{index}",
        mode="replay",
        subject=f"{index:06d}.SZ",
        as_of=NOW,
        evidence=(),
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
    )
    if not succeeded:
        return BatchTaskItem(request=request, status="queued")
    return BatchTaskItem(
        request=request,
        status="succeeded",
        result=BatchResultRef(
            decision_id=f"dec_{index}", signal_id=f"sig_{index}", final_action="watch"
        ),
    )


def _pre_split_task(batch_id: str, *, count: int) -> BatchResearchTask:
    return BatchResearchTask(
        batch_id=batch_id,
        items=tuple(_item(index, succeeded=index % 2 == 0) for index in range(count)),
        status="partial",
        max_concurrency=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _write_pre_split(path: Path, tasks: tuple[BatchResearchTask, ...]) -> None:
    """Create `batch_tasks` in its original shape and write whole-task payloads into it.

    Deliberately does not construct `SQLiteBatchTaskStore`: that constructor would create
    `batch_task_items` as a side effect and write the modern shape, and the fixture would
    then be testing the migration against a database that never needed it. The three stores
    it *does* construct own the tables the earlier table-altering migrations require -- the
    executor stops at the first deferral and does not skip ahead, so without them
    `split_batch_task_items` is never reached at all and every assertion here would be
    checking an untouched database.
    """
    SQLiteRunRepository(path)
    SQLitePortfolioLedger(path)
    SQLiteReportStore(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_tasks (
                batch_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        for task in tasks:
            connection.execute(
                "INSERT INTO batch_tasks (batch_id, status, payload) VALUES (?, ?, ?)",
                (
                    task.batch_id,
                    task.status,
                    task.model_dump_json(exclude_computed_fields=True),
                ),
            )


def test_a_pre_split_batch_reads_back_unchanged_through_the_store(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Every item, in order, with its own status and result, survives the move.

    Two batches and a mix of succeeded/queued items, because the failure this migration
    could plausibly have -- items landing under the wrong `batch_id`, or in insertion order
    rather than `position` order -- is invisible to a fixture with one batch of identical
    items. Whole-model equality is the assertion, not a count.
    """
    path = tmp_path / "state.sqlite3"
    first = _pre_split_task("batch_one", count=5)
    second = _pre_split_task("batch_two", count=3)
    _write_pre_split(path, (first, second))

    run_migrations(path, clock=migration_clock)

    store = SQLiteBatchTaskStore(path)
    assert store.get("batch_one") == first
    assert store.get("batch_two") == second
    assert store.list() == (first, second)


def test_the_split_empties_the_items_key_out_of_the_header_row(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The items must exist in exactly one place afterwards, not two.

    A migration that copied the items into their new table and left the originals behind
    would pass the round-trip test above -- `get()` reads the rows and never looks at the
    header's copy -- while leaving a stale duplicate that the next `update_item()` silently
    diverges from. That is the failure this asserts against directly.
    """
    path = tmp_path / "state.sqlite3"
    task = _pre_split_task("batch_one", count=4)
    _write_pre_split(path, (task,))

    run_migrations(path, clock=migration_clock)

    with closing(sqlite3.connect(path)) as connection:
        header = connection.execute(
            "SELECT payload FROM batch_tasks WHERE batch_id = 'batch_one'"
        ).fetchone()[0]
        positions = connection.execute(
            "SELECT position FROM batch_task_items WHERE batch_id = 'batch_one' ORDER BY position"
        ).fetchall()
    assert "items" not in json.loads(header)
    assert [row[0] for row in positions] == [0, 1, 2, 3]
    assert read_status(path).current_version == SPLIT_BATCH_TASK_ITEMS_VERSION


def test_running_the_split_twice_changes_nothing(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Applied to an already-split database it is a no-op, not a wipe.

    This is the fresh-install path, not a hypothetical: on a brand-new database the
    migration defers (no `batch_tasks` yet), the store's constructor creates both tables in
    the modern shape, and the migration then applies for real on the *next* open -- against
    rows that have already been split.
    """
    path = tmp_path / "state.sqlite3"
    task = _pre_split_task("batch_one", count=4)
    _write_pre_split(path, (task,))
    run_migrations(path, clock=migration_clock)

    with closing(sqlite3.connect(path)) as connection, connection:
        _split_batch_task_items(connection)

    assert SQLiteBatchTaskStore(path).get("batch_one") == task


def test_the_store_still_reads_a_row_whose_split_has_not_run_yet(tmp_path: Path) -> None:
    """A pre-split row read before its migration lands comes back whole, not half.

    Reachable without any migration being broken: the executor stops at the first migration
    that defers, so a database missing a table an *earlier* migration requires never reaches
    `split_batch_task_items`, while `SQLiteBatchTaskStore` is constructed regardless and
    starts serving reads. Splicing an empty item list into that header would raise
    `items: Tuple should have at least 1 item` on a batch that visibly has four -- an error
    about the wrong thing entirely.
    """
    path = tmp_path / "state.sqlite3"
    task = _pre_split_task("batch_one", count=4)
    _write_pre_split(path, (task,))

    store = SQLiteBatchTaskStore(path)

    assert read_status(path).current_version < SPLIT_BATCH_TASK_ITEMS_VERSION
    assert store.get("batch_one") == task
    assert store.list() == (task,)


def test_the_split_defers_on_a_database_that_has_no_batch_tasks_table(
    tmp_path: Path,
) -> None:
    """Not precondition-free, and it has to say so rather than crash a fresh install.

    Migrations run before any store is constructed, so on a new database `batch_tasks` does
    not exist yet. `MigrationNotYetApplicable` is how the executor is told to stop and retry
    next time; a bare `sqlite3.OperationalError` here would surface as a
    `MigrationFailedError` out of `build_storage()` and take down every new installation.
    """
    with (
        closing(sqlite3.connect(tmp_path / "empty.sqlite3")) as connection,
        pytest.raises(MigrationNotYetApplicable),
    ):
        _split_batch_task_items(connection)


def test_the_audit_refuses_a_split_that_lost_an_item(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The audit is what stands between a silent partial move and a committed one.

    Simulated by deleting one item row after the split and re-running the audit's own
    reassembly, which is the shape of every way this could go wrong -- a batch short by one
    item still reads back as a perfectly valid `BatchResearchTask` describing less work than
    was actually queued, and nothing else in the system would notice.
    """
    path = tmp_path / "state.sqlite3"
    task = _pre_split_task("batch_one", count=4)
    _write_pre_split(path, (task,))
    run_migrations(path, clock=migration_clock)

    from openalpha_cn.storage.migrations import _audit_batch_item_split

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "DELETE FROM batch_task_items WHERE batch_id = 'batch_one' AND position = 2"
        )
        with pytest.raises(BatchItemSplitError, match="does not read back as the task"):
            _audit_batch_item_split(connection, {"batch_one": task})
