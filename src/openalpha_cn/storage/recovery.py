"""Durable operational state for node-level research recovery."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.agents.base import AgentResult
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, read_versioned


class RecoveryConflictError(ValueError):
    """Raised when a run ID is reused with incompatible recovery inputs."""


class RunRecoveryState(BaseModel):
    """A validated prefix of completed agent work for one immutable run."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["run-recovery/v1"] = "run-recovery/v1"
    run_id: str = Field(min_length=1, max_length=128)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_ids: tuple[str, ...]
    completed_results: tuple[AgentResult, ...] = ()
    next_agent_index: int = Field(ge=0)
    attempt_count: int = Field(default=1, ge=1)
    status: Literal["running", "failed", "succeeded"] = "running"
    started_at: datetime
    updated_at: datetime
    error_type: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("started_at", "updated_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if self.next_agent_index != len(self.completed_results):
            raise ValueError("next_agent_index must equal the completed result count")
        if self.next_agent_index > len(self.agent_ids):
            raise ValueError("completed result count exceeds graph size")
        completed_ids = tuple(item.agent_id for item in self.completed_results)
        if completed_ids != self.agent_ids[: self.next_agent_index]:
            raise ValueError("completed results must match the graph prefix")
        if self.status == "failed" and self.error_type is None:
            raise ValueError("failed recovery state requires error_type")
        if self.status != "failed" and self.error_type is not None:
            raise ValueError("only failed recovery state may contain error_type")
        if self.status == "succeeded" and self.next_agent_index != len(self.agent_ids):
            raise ValueError("succeeded recovery state requires the full graph")
        if self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        return self


RUN_RECOVERY_STATE_VERSIONS: ContractVersions[RunRecoveryState] = ContractVersions(
    name="run-recovery",
    current_version="run-recovery/v1",
    versions={"run-recovery/v1": RunRecoveryState},
)


class SQLiteRecoveryStore:
    """Persist the latest validated recovery state for each run ID."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS run_recovery (
                    run_id TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    graph_signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get(self, run_id: str) -> RunRecoveryState | None:
        """Load the latest recovery state for a run."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM run_recovery WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else read_versioned(RUN_RECOVERY_STATE_VERSIONS, row[0])

    def save(self, state: RunRecoveryState) -> None:
        """Atomically insert or advance a compatible recovery state."""
        payload = state.model_dump_json(exclude_computed_fields=True)
        # SQLite UPSERT contract: https://www.sqlite.org/lang_upsert.html
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO run_recovery (
                    run_id, request_digest, graph_signature, status, payload
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload
                WHERE run_recovery.request_digest = excluded.request_digest
                  AND run_recovery.graph_signature = excluded.graph_signature
                """,
                (
                    state.run_id,
                    state.request_digest,
                    state.graph_signature,
                    state.status,
                    payload,
                ),
            )
            if cursor.rowcount != 1:
                raise RecoveryConflictError(
                    f"run recovery conflicts with immutable inputs: {state.run_id}"
                )

    def clear(self, run_id: str) -> bool:
        """Delete operational recovery state without touching immutable ledgers."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM run_recovery WHERE run_id = ?",
                (run_id,),
            )
        return cursor.rowcount == 1
