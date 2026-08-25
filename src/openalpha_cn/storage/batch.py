"""SQLite WAL storage for durable batch tasks and progress events.

**Why item state lives in its own table** (`V2-P4-019`). `batch_tasks.payload` holds the
batch header only -- the JSON of one `BatchResearchTask` with its `items` key *removed* --
and `batch_task_items` holds one row per item. That split is not tidiness; it is what makes
a whole-market batch finish.

`BatchResearchService` persists every item transition, and there are 2N of them. When the
whole task lived in one JSON blob, each transition cost a full `model_dump_json` of every
item plus a full re-parse and pydantic re-validation of every item on the way back in --
O(N) work per transition, so O(N^2) per batch. Measured on this repository at
`5e18791`, with a runner that does nothing at all: 0.86s at N=100, 4.7s at N=250, 16.5s at
N=500, 64.8s at N=1,000 -- quadratic, extrapolating to roughly **33 minutes** of pure
bookkeeping for `V2-P4-004`'s measured market of 5,545 listed names. The 1,000-item cap in
`batch_contracts.py` was hiding it.

One transition at N=5,545, measured three ways before this was written:

    whole-task get + save (what it did)   ~300ms   ->  ~28 min per batch
    json_set on the stored 5.01MB blob     25.3ms  ->  ~4.7 min per batch
    UPDATE one row in batch_task_items      0.72ms ->  ~8s per batch

The middle row is why the payload shape had to change rather than the write technique:
patching the item in place with SQLite's own JSON functions keeps the schema untouched and
moves the O(N) rewrite from Python into C, which buys 12x and stays quadratic. Splitting
the items out is the only one of the three that makes a transition O(1).

`save()` is still O(N) and is still the right call at the coarse points that genuinely
change every item (`submit`, `cancel`, `recover_interrupted`); `update_item()` is what the
per-item hot path uses instead.
"""

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from openalpha_cn.batch_contracts import (
    BATCH_PROGRESS_EVENT_VERSIONS,
    BATCH_RESEARCH_TASK_VERSIONS,
    BATCH_TASK_ITEM_VERSIONS,
    BatchItemCensus,
    BatchProgressEvent,
    BatchResearchTask,
    BatchTaskItem,
    BatchTaskSummary,
)
from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.storage.connection import open_state_connection

logger = logging.getLogger(__name__)

BATCH_TASK_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS batch_task_items (
    batch_id TEXT NOT NULL REFERENCES batch_tasks(batch_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (batch_id, position)
)
"""
"""The per-item table's DDL, shared with `storage/migrations.py::split_batch_task_items`.

One statement, one owner: the migration that back-fills this table and the constructor that
creates it on a fresh database must not be able to disagree about its shape. Same reasoning
as `storage/sqlite.py::ensure_runs_mode_projection` (`V2-P4-002`), and the same reason that
one is a shared callable rather than two copies of the DDL.
"""


def split_task_payload(task: BatchResearchTask) -> tuple[str, tuple[str, ...]]:
    """Split one task into its header JSON and its per-item JSON payloads.

    Shared with the migration so that "what the header looks like without its items" is
    stated once. The header keeps every other field verbatim, so a header written here and
    a header the migration produced from an old blob are byte-identical for the same task.
    """
    document: dict[str, Any] = json.loads(task.model_dump_json(exclude_computed_fields=True))
    del document["items"]
    items = tuple(item.model_dump_json(exclude_computed_fields=True) for item in task.items)
    return json.dumps(document), items


class SQLiteBatchTaskStore:
    """Persist latest batch state plus an append-only progress stream."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS batch_tasks (
                    batch_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS batch_events_batch_idx
                ON batch_events(batch_id, sequence);
                """
            )
            connection.execute(BATCH_TASK_ITEMS_DDL)

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    def save(self, task: BatchResearchTask) -> None:
        """Insert or atomically replace the latest state of one batch, items included.

        O(N) by construction -- it rewrites every item row -- which is why the per-item hot
        path calls `update_item()` instead. The three callers that legitimately need it all
        change the whole task at once: `submit` (every item is new), `cancel` (every pending
        item moves), and `recover_interrupted` (every running item moves).
        """
        header, items = split_task_payload(task)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO batch_tasks (batch_id, status, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload
                """,
                (task.batch_id, task.status, header),
            )
            connection.execute("DELETE FROM batch_task_items WHERE batch_id = ?", (task.batch_id,))
            connection.executemany(
                "INSERT INTO batch_task_items (batch_id, position, payload) VALUES (?, ?, ?)",
                [(task.batch_id, position, item) for position, item in enumerate(items)],
            )

    def update_item(
        self,
        *,
        batch_id: str,
        index: int,
        item: BatchTaskItem,
        updated_at: datetime,
    ) -> None:
        """Persist one item's transition without touching the other N-1. O(1).

        `updated_at` is patched onto the header with `json_set` rather than by reading,
        parsing and rewriting it, so two threads transitioning two different items cannot
        lose each other's timestamp through a read-modify-write. The header is a few hundred
        bytes with the items gone, so this is the cheap case for `json_set` -- see the module
        docstring for the measurement that ruled it out against the *un*split payload.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE batch_task_items SET payload = ? WHERE batch_id = ? AND position = ?",
                (item.model_dump_json(exclude_computed_fields=True), batch_id, index),
            )
            connection.execute(
                "UPDATE batch_tasks SET payload = json_set(payload, '$.updated_at', ?) "
                "WHERE batch_id = ?",
                (updated_at.isoformat(), batch_id),
            )

    def update_status(self, *, batch_id: str, status: str, updated_at: datetime) -> None:
        """Move one batch's aggregate status without rewriting its items. O(1).

        The `status` column and the header payload's `$.status` are two spellings of one
        fact, and this is the only writer that moves it, so they move together in one
        statement rather than in two that could be interleaved.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE batch_tasks SET status = ?, "
                "payload = json_set(payload, '$.status', ?, '$.updated_at', ?) "
                "WHERE batch_id = ?",
                (status, status, updated_at.isoformat(), batch_id),
            )

    def get_item(self, *, batch_id: str, index: int) -> BatchTaskItem | None:
        """Return one item's latest state. O(1)."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM batch_task_items WHERE batch_id = ? AND position = ?",
                (batch_id, index),
            ).fetchone()
        return None if row is None else read_versioned(BATCH_TASK_ITEM_VERSIONS, row[0])

    def is_cancellation_requested(self, batch_id: str) -> bool:
        """Return whether cooperative cancellation was requested. O(1).

        Deliberately narrower than `get()`: this is read once per item transition, and it is
        the only field of the header the per-item path needs, so it must not pull 5,545
        items through pydantic to answer a boolean. `json_extract` yields SQLite's 1/0 for a
        JSON boolean, and a batch that does not exist is reported as not cancelled -- the
        caller has already established the batch exists, and inventing a `KeyError` here
        would make an absent row and an uncancelled row two different kinds of answer for a
        question that only has one.
        """
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT json_extract(payload, '$.cancellation_requested') "
                "FROM batch_tasks WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return bool(row[0]) if row is not None else False

    def get(self, batch_id: str) -> BatchResearchTask | None:
        """Return the latest batch state, items reassembled from their own rows."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM batch_tasks WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            items = connection.execute(
                "SELECT payload FROM batch_task_items WHERE batch_id = ? ORDER BY position",
                (batch_id,),
            ).fetchall()
        return reassemble_task(row[0], [item[0] for item in items])

    def count_batches(self) -> int:
        """How many batches this store holds. O(1) in their items."""
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT COUNT(*) FROM batch_tasks").fetchone()
        return int(row[0])

    def list_summaries(self, *, limit: int, offset: int) -> tuple[BatchTaskSummary, ...]:
        """One page of batches, counted rather than reassembled (`V2-P4-040`).

        Narrower than `list()` for the reason `is_cancellation_requested` is narrower than
        `get()`: the listing needs each batch's bookkeeping and a per-status tally, and pulling
        115,355 items through pydantic to produce five integers per batch is the cost that made
        `GET /api/v1/research/batches` a 36.9 MB, 2.35s answer. The tally is a
        `GROUP BY json_extract(payload, '$.status')` inside SQLite, so the item rows are still
        visited -- there is no stored counter to read instead -- but they are visited in C and
        nothing is materialised per item.

        The page is taken on `batch_tasks` **before** the items are touched, so the `IN` list is
        bounded by `limit` and a deployment holding a thousand batches pays for the fifty it
        asked for.

        A header that still carries its own inline `items` is counted from those instead. That
        is `reassemble_task`'s pre-split row shape and the same one route reaches it -- a
        database whose `split_batch_task_items` migration has not run -- and a listing that read
        only `batch_task_items` would report every such batch as empty, which is a wrong answer
        rather than a refused one.
        """
        with closing(self._connect()) as connection:
            headers = connection.execute(
                "SELECT batch_id, payload FROM batch_tasks ORDER BY batch_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            if not headers:
                return ()
            batch_ids = [batch_id for batch_id, _ in headers]
            placeholders = ",".join("?" * len(batch_ids))
            tallies = connection.execute(
                "SELECT batch_id, json_extract(payload, '$.status') AS item_status, COUNT(*) "
                f"FROM batch_task_items WHERE batch_id IN ({placeholders}) "
                "GROUP BY batch_id, item_status",
                batch_ids,
            ).fetchall()
        counted: dict[str, dict[str, int]] = defaultdict(dict)
        for batch_id, item_status, count in tallies:
            counted[batch_id][item_status] = int(count)
        return tuple(summarize_task(header, counted[batch_id]) for batch_id, header in headers)

    def list(self) -> tuple[BatchResearchTask, ...]:
        """Return all batches in stable ID order."""
        with closing(self._connect()) as connection:
            headers = connection.execute(
                "SELECT batch_id, payload FROM batch_tasks ORDER BY batch_id"
            ).fetchall()
            rows = connection.execute(
                "SELECT batch_id, payload FROM batch_task_items ORDER BY batch_id, position"
            ).fetchall()
        grouped: dict[str, list[str]] = defaultdict(list)
        for batch_id, payload in rows:
            grouped[batch_id].append(payload)
        return tuple(reassemble_task(header, grouped[batch_id]) for batch_id, header in headers)

    def append_event(
        self,
        *,
        batch_id: str,
        kind: str,
        occurred_at: datetime,
        run_id: str | None = None,
        detail: str | None = None,
    ) -> BatchProgressEvent:
        """Append and return one monotonic progress event."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO batch_events (batch_id, kind, payload) VALUES (?, ?, '')",
                (batch_id, kind),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a batch event sequence")
            sequence = cursor.lastrowid
            event = BatchProgressEvent(
                sequence=sequence,
                batch_id=batch_id,
                kind=kind,  # type: ignore[arg-type]
                occurred_at=occurred_at,
                run_id=run_id,
                detail=detail,
            )
            connection.execute(
                "UPDATE batch_events SET payload = ? WHERE sequence = ?",
                (event.model_dump_json(), sequence),
            )
        return event

    def list_events(self, batch_id: str) -> tuple[BatchProgressEvent, ...]:
        """Return progress events in append order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM batch_events
                WHERE batch_id = ?
                ORDER BY sequence
                """,
                (batch_id,),
            ).fetchall()
        return tuple(read_versioned(BATCH_PROGRESS_EVENT_VERSIONS, row[0]) for row in rows)

    def recover_interrupted(self, *, now: datetime) -> tuple[str, ...]:
        """Requeue process-interrupted running items without losing terminal work."""
        recovered: list[str] = []
        for task in self.list():
            if task.status != "running":
                continue
            items = tuple(
                item.model_copy(update={"status": "queued"}) if item.status == "running" else item
                for item in task.items
            )
            updated = task.model_copy(
                update={"items": items, "status": "queued", "updated_at": now}
            )
            self.save(updated)
            self.append_event(
                batch_id=task.batch_id,
                kind="recovered",
                occurred_at=now,
                detail="interrupted items requeued",
            )
            logger.info("batch_recovered", extra={"batch_id": task.batch_id})
            recovered.append(task.batch_id)
        return tuple(recovered)


def load_task_row(
    connection: sqlite3.Connection, *, batch_id: str, header: str
) -> BatchResearchTask:
    """Read one batch from a raw `batch_tasks` row, in either shape that row can be in.

    Exists for `storage/migrations.py`, which reaches into these tables with its own
    connection and, unlike the store, can legitimately meet a row from *before*
    `split_batch_task_items` ran -- `rewrite_contract_identities` (version 5) is ordered
    before it and re-points the `BatchResultRef` nested inside each item. A header that
    still carries its own `items` key is read as-is; one that does not has its items
    fetched from `batch_task_items`.

    Tolerating both is what makes the version-5 rewrite idempotent across the split rather
    than only in the one order the registry happens to declare today. Without it, running
    that rewrite against an already-split database raises `items: Field required` -- which
    `tests/integration/storage/test_identity_rewrite.py::
    test_the_rewrite_is_idempotent_and_a_second_run_changes_nothing` reproduces exactly.
    """
    document: dict[str, Any] = json.loads(header)
    if "items" in document:
        return read_versioned(BATCH_RESEARCH_TASK_VERSIONS, header)
    rows = connection.execute(
        "SELECT payload FROM batch_task_items WHERE batch_id = ? ORDER BY position",
        (batch_id,),
    ).fetchall()
    return reassemble_task(header, [row[0] for row in rows])


def store_task_row(connection: sqlite3.Connection, task: BatchResearchTask, *, split: bool) -> None:
    """Write one batch back into a raw `batch_tasks` row, in the shape it was read from.

    `split` is passed rather than sniffed so the caller states which shape it is writing:
    a migration that read a pre-split row and wrote a post-split one would be doing the
    split as an undeclared side effect of a rewrite that is not about storage layout.
    """
    if not split:
        connection.execute(
            "UPDATE batch_tasks SET payload = ? WHERE batch_id = ?",
            (task.model_dump_json(exclude_computed_fields=True), task.batch_id),
        )
        return
    header, items = split_task_payload(task)
    connection.execute(
        "UPDATE batch_tasks SET payload = ? WHERE batch_id = ?", (header, task.batch_id)
    )
    connection.executemany(
        "UPDATE batch_task_items SET payload = ? WHERE batch_id = ? AND position = ?",
        [(item, task.batch_id, position) for position, item in enumerate(items)],
    )


def summarize_task(header: str, counts: Mapping[str, int]) -> BatchTaskSummary:
    """Build one listing summary from a stored header and its per-status tally.

    The header is read as plain JSON rather than through `read_versioned`, because a header on
    its own is *not* a `BatchResearchTask`: `items` carries `min_length=1`, so validating one
    would fail on every batch. `BatchTaskSummary` does its own validation of what is extracted
    -- including that the census sums to `item_count` -- so the fields are still checked, by the
    contract that describes this shape rather than by one that describes a different one.
    """
    document: dict[str, Any] = json.loads(header)
    inline = document.get("items")
    if inline is not None:
        counts = Counter(str(item.get("status", "queued")) for item in inline)
    census = BatchItemCensus.from_counts(counts)
    return BatchTaskSummary(
        batch_id=document["batch_id"],
        status=document["status"],
        max_concurrency=document["max_concurrency"],
        cancellation_requested=bool(document.get("cancellation_requested", False)),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        item_count=census.total,
        items_by_status=census,
    )


def reassemble_task(header: str, items: list[str]) -> BatchResearchTask:
    """Put a header's items back into it and validate the whole thing as one task.

    The items go back in as parsed JSON and the document is re-encoded so the read still
    goes through `read_versioned` -- the single entry point every stored-row read in this
    package uses, and the only thing that knows how to dispatch on a `schema_version` a
    newer build wrote (see `domain/versioning.py`). Re-encoding costs one `json.dumps` of
    the assembled document, which is O(N) and therefore belongs here, on `get()`, and not
    on the per-item write path that used to pay it 2N times.

    A header that still carries its own `items` is returned as it stands, and `items` is
    ignored. That is the pre-split row shape, and it is reachable by exactly one route: a
    database whose `split_batch_task_items` migration has not run yet because an
    *earlier*-ordered migration deferred on a table this database happens not to have. What
    that route must not produce is the confusing failure -- a bare
    `items: Tuple should have at least 1 item` out of a store read, on a batch that plainly
    has items -- so the one rule for reading either shape lives here, where `get()`,
    `list()` and `load_task_row` all share it rather than each deciding separately.
    """
    document: dict[str, Any] = json.loads(header)
    if "items" in document:
        return read_versioned(BATCH_RESEARCH_TASK_VERSIONS, header)
    document["items"] = [json.loads(item) for item in items]
    return read_versioned(BATCH_RESEARCH_TASK_VERSIONS, json.dumps(document))
