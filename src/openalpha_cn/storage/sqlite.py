"""SQLite WAL repository for append-only runs, decisions, and checkpoints."""

import sqlite3
from contextlib import closing
from pathlib import Path

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import CheckpointRecord, RunManifest


class DuplicateRecordError(ValueError):
    """Raised when an append would replace an existing immutable record."""


class SQLiteRunRepository:
    """Persist immutable research records in a local SQLite WAL database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                """
            )

    def journal_mode(self) -> str:
        """Return the active SQLite journal mode."""
        with closing(self._connect()) as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        if row is None:
            raise RuntimeError("SQLite did not return a journal mode")
        return str(row[0]).lower()

    def append_run(self, manifest: RunManifest) -> None:
        """Append a run manifest without replacing an existing run."""
        payload = manifest.model_dump_json(exclude_computed_fields=True)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO runs (run_id, payload) VALUES (?, ?)",
                    (manifest.run_id, payload),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicateRecordError(f"run already exists: {manifest.run_id}") from error

    def get_run(self, run_id: str) -> RunManifest | None:
        """Load a run manifest by ID."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else RunManifest.model_validate_json(row[0])

    def append_decision(self, decision: DecisionLedger) -> None:
        """Append a decision linked to an existing run."""
        if self.get_run(decision.run_id) is None:
            raise ValueError(f"unknown run_id: {decision.run_id}")
        payload = decision.model_dump_json(exclude_computed_fields=True)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO decisions (decision_id, run_id, payload)
                    VALUES (?, ?, ?)
                    """,
                    (decision.decision_id, decision.run_id, payload),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicateRecordError(
                f"decision already exists: {decision.decision_id}"
            ) from error

    def get_decision(self, decision_id: str) -> DecisionLedger | None:
        """Load a decision by its content-derived ID."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return None if row is None else DecisionLedger.model_validate_json(row[0])

    def append_checkpoint(self, *, run_id: str, checkpoint: CheckpointRecord) -> None:
        """Append a checkpoint to an existing run."""
        if self.get_run(run_id) is None:
            raise ValueError(f"unknown run_id: {run_id}")
        payload = checkpoint.model_dump_json()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO checkpoints (run_id, payload) VALUES (?, ?)",
                (run_id, payload),
            )

    def list_checkpoints(self, *, run_id: str) -> tuple[CheckpointRecord, ...]:
        """Return checkpoints in append order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM checkpoints
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return tuple(CheckpointRecord.model_validate_json(row[0]) for row in rows)
