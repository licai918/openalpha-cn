"""SQLite persistence for per-request model usage accounting (V2-P0B-011)."""

import sqlite3
from contextlib import closing
from pathlib import Path

from openalpha_cn.models.governance import ModelUsageRecord
from openalpha_cn.storage.connection import open_state_connection


class SQLiteModelUsageStore:
    """Append provider usage idempotently by provider request ID."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_usage (
                    request_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    def append(self, record: ModelUsageRecord) -> None:
        payload = record.model_dump_json()
        with closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT payload FROM model_usage WHERE request_id = ?",
                (record.request_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise ValueError(f"model usage request_id conflicts: {record.request_id}")
                return
            connection.execute(
                "INSERT INTO model_usage (request_id, provider_id, payload) VALUES (?, ?, ?)",
                (record.request_id, record.provider_id, payload),
            )

    def list(self, *, provider_id: str | None = None) -> tuple[ModelUsageRecord, ...]:
        with closing(self._connect()) as connection:
            if provider_id is None:
                rows = connection.execute(
                    "SELECT payload FROM model_usage ORDER BY rowid"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload FROM model_usage
                    WHERE provider_id = ?
                    ORDER BY rowid
                    """,
                    (provider_id,),
                ).fetchall()
        return tuple(ModelUsageRecord.model_validate_json(row[0]) for row in rows)
