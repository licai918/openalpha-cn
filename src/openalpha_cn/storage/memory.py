"""Durable SQLite-backed research memory."""

import sqlite3
from contextlib import closing
from pathlib import Path

from openalpha_cn.domain.memory import MEMORY_ENTRY_VERSIONS, MemoryEntry
from openalpha_cn.domain.versioning import read_versioned


class SQLiteResearchMemory:
    """Persist compact decision-linked memory across processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_memory (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS research_memory_subject_sequence_idx
                ON research_memory(subject, sequence)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def append(self, entry: MemoryEntry) -> None:
        """Append once per decision ID and reject conflicting replacement."""
        payload = entry.model_dump_json()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO research_memory (decision_id, subject, payload)
                    VALUES (?, ?, ?)
                    """,
                    (entry.decision_id, entry.subject, payload),
                )
        except sqlite3.IntegrityError as error:
            existing = self._get(entry.decision_id)
            if existing == entry:
                return
            raise ValueError(
                f"decision_id conflicts with existing research memory: {entry.decision_id}"
            ) from error

    def _get(self, decision_id: str) -> MemoryEntry | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM research_memory WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return None if row is None else read_versioned(MEMORY_ENTRY_VERSIONS, row[0])

    def list(self, *, subject: str) -> tuple[MemoryEntry, ...]:
        """Return durable subject memory in append order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM research_memory
                WHERE subject = ?
                ORDER BY sequence
                """,
                (subject,),
            ).fetchall()
        return tuple(read_versioned(MEMORY_ENTRY_VERSIONS, row[0]) for row in rows)
