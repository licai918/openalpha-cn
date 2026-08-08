"""SQLite WAL storage for durable batch tasks and progress events."""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from openalpha_cn.batch_contracts import (
    BATCH_PROGRESS_EVENT_VERSIONS,
    BATCH_RESEARCH_TASK_VERSIONS,
    BatchProgressEvent,
    BatchResearchTask,
)
from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.storage.connection import open_state_connection

logger = logging.getLogger(__name__)


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

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    def save(self, task: BatchResearchTask) -> None:
        """Insert or atomically replace the latest state of one batch."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO batch_tasks (batch_id, status, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload
                """,
                (
                    task.batch_id,
                    task.status,
                    task.model_dump_json(exclude_computed_fields=True),
                ),
            )

    def get(self, batch_id: str) -> BatchResearchTask | None:
        """Return the latest batch state."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM batch_tasks WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return None if row is None else read_versioned(BATCH_RESEARCH_TASK_VERSIONS, row[0])

    def list(self) -> tuple[BatchResearchTask, ...]:
        """Return all batches in stable ID order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload FROM batch_tasks ORDER BY batch_id"
            ).fetchall()
        return tuple(read_versioned(BATCH_RESEARCH_TASK_VERSIONS, row[0]) for row in rows)

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
