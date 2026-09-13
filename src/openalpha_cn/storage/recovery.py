"""Durable operational state for node-level research recovery.

## Why the graph lives in its own table (`V2-P4-020`)

A run's state used to be one JSON blob, and `ResearchEngine` saved the whole accumulated
document after every agent. Persisting `N` results therefore serialised `N(N+1)/2` of them:
measured at `be262ea`, 12 agents cost 78 result serialisations and 400 agents cost 80,200 and
46.68 MB of JSON, against 0.05 MB for the 12. The curve is `storage/batch.py`'s exactly, one
plane over -- and so is the answer.

The row that holds a run keeps only the fields that do not grow with the graph, and
`run_recovery_results` holds one row per **agent slot**: `position`, the `agent_id` the graph
declares there, and a `payload` that is `NULL` until that agent completes. Three of
`RunRecoveryState`'s fields are therefore not stored in the header at all but derived from
those rows on read -- `agent_ids`, `completed_results`, and `next_agent_index` -- which is
what makes `validate_progress`' central invariant, `next_agent_index == len(completed_results)`
over a prefix of `agent_ids`, true by construction of the read rather than by agreement between
two writers.

`append_result` is the O(1) hot path that follows from that shape: one `UPDATE` keyed on the
primary key and guarded by `agent_id = ?` and `payload IS NULL`, so a write that would land on
the wrong agent or overwrite a completed slot changes no rows and is refused by name instead of
being discovered later as a state that no longer validates. `save()` is unchanged in meaning
and stays O(N) -- it rewrites every slot -- because its three callers all change the whole run
at once: starting it, resuming a failed attempt, and recording a failure.

The header is small enough that `json_set` is the cheap case for the one field `append_result`
moves (`updated_at`), which is the same judgement `SQLiteBatchTaskStore.update_item` records
and rests on the same fact: with the graph gone, the header is a few hundred bytes.

**No migration, and that is a decision rather than an omission.** A row written before this
change carries `completed_results` inside its payload and has no slot rows; `get()` recognises
that shape by the key's presence -- `_split_batch_task_items`' own discriminator -- and reads it
whole, and `append_result` splits it in place the first time it finds no slot to write into.
So an old database is correct without migrating and is converted by the next write that needs
the new shape. Recovery state is operational rather than a ledger (`migrations.py`'s own
remedy text offers deleting these rows as a supported answer), so there is nothing here a
migration would be preserving that lazy conversion does not.
"""

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.agent_result import AgentResult
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, read_versioned
from openalpha_cn.storage.connection import open_state_connection


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


RUN_RECOVERY_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS run_recovery_results (
    run_id TEXT NOT NULL REFERENCES run_recovery(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    payload TEXT,
    PRIMARY KEY (run_id, position)
)
"""
"""One row per agent slot in a run's graph; `payload` is `NULL` until that agent completes.

A slot rather than a completed result, which is what lets `append_result` be both O(1) and
self-checking: the row it must update already names the agent the graph expects at that
position, so `WHERE position = ? AND agent_id = ? AND payload IS NULL` is the whole of
`validate_progress`' prefix rule evaluated by the primary-key index at the one moment it could
be broken.
"""

DERIVED_FROM_RESULT_ROWS: frozenset[str] = frozenset(
    {"agent_ids", "completed_results", "next_agent_index"}
)
"""The `RunRecoveryState` fields the header does not store, because the slot rows are them.

Kept as a named set rather than spelled into `model_dump_json(exclude=...)` inline, because
`split_recovery_payload` and `reassemble_recovery_state` have to agree about it exactly: a
field excluded from the write and not restored on the read comes back as its default, and for
`next_agent_index` that default is a number the rest of the model would validate against
happily and wrongly.
"""


def split_recovery_payload(
    state: RunRecoveryState,
) -> tuple[str, tuple[tuple[int, str, str | None], ...]]:
    """Split one state into its header JSON and one row per agent slot.

    `model_dump_json(exclude=...)` rather than a dump-then-delete, so the results are never
    serialised on the header's account -- which is the entire point: a header that costs a
    full serialisation of the graph to write would leave the quadratic in place with a smaller
    constant, and `V2-P4-019` already measured that shape (`json_set` over a 5.01 MB blob,
    still O(N)) and rejected it.
    """
    header = state.model_dump_json(
        exclude=set(DERIVED_FROM_RESULT_ROWS), exclude_computed_fields=True
    )
    completed = state.completed_results
    slots = tuple(
        (
            position,
            agent_id,
            (
                completed[position].model_dump_json(exclude_computed_fields=True)
                if position < len(completed)
                else None
            ),
        )
        for position, agent_id in enumerate(state.agent_ids)
    )
    return header, slots


def reassemble_recovery_state(
    header: str, slots: Sequence[tuple[str, str | None]]
) -> RunRecoveryState:
    """Splice a header and its slot rows back into one validated state.

    A header carrying `completed_results` is a row written before `V2-P4-020` split the table,
    and is read whole -- `_split_batch_task_items`' discriminator, for its reason: the key's
    presence is the only thing that distinguishes the two shapes, and it cannot be present in
    a header this module wrote.

    `next_agent_index` is the count of non-`NULL` payloads rather than a stored number. A slot
    left `NULL` behind a completed one therefore produces a `completed_results` that is not the
    prefix of `agent_ids` it claims to be, and `validate_progress` refuses it by name; storing
    the index instead would have made that state validate and mean the wrong thing.
    """
    document = json.loads(header)
    if "completed_results" in document:
        return read_versioned(RUN_RECOVERY_STATE_VERSIONS, header)
    document["agent_ids"] = [agent_id for agent_id, _ in slots]
    document["completed_results"] = [
        json.loads(payload) for _, payload in slots if payload is not None
    ]
    document["next_agent_index"] = len(document["completed_results"])
    return read_versioned(RUN_RECOVERY_STATE_VERSIONS, json.dumps(document))


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
            connection.execute(RUN_RECOVERY_RESULTS_DDL)

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    def get(self, run_id: str) -> RunRecoveryState | None:
        """Load the latest recovery state for a run, its graph reassembled from its slots."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM run_recovery WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            slots = connection.execute(
                "SELECT agent_id, payload FROM run_recovery_results "
                "WHERE run_id = ? ORDER BY position",
                (run_id,),
            ).fetchall()
        return reassemble_recovery_state(row[0], [(item[0], item[1]) for item in slots])

    def save(self, state: RunRecoveryState) -> None:
        """Atomically insert or advance a compatible recovery state, slots included.

        O(N) by construction -- it rewrites every slot -- which is why the per-agent hot path
        calls `append_result()` instead. The three callers that legitimately need it all change
        the whole run at once: starting it (every slot is new), resuming a failed attempt
        (`attempt_count` and `status` move for the run, not for one agent), and recording a
        failure. `SQLiteBatchTaskStore.save` carries the identical note for the identical
        reason.
        """
        header, slots = split_recovery_payload(state)
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
                    header,
                ),
            )
            if cursor.rowcount != 1:
                raise RecoveryConflictError(
                    f"run recovery conflicts with immutable inputs: {state.run_id}"
                )
            self._write_slots(connection, state.run_id, slots)

    def append_result(
        self,
        run_id: str,
        *,
        position: int,
        result: AgentResult,
        updated_at: datetime,
    ) -> None:
        """Record one completed agent without rewriting the ones before it. O(1).

        The `UPDATE` is keyed on the primary key and guarded twice, and both guards are the
        cheap spelling of a rule `RunRecoveryState` otherwise checks by rebuilding the whole
        model. `agent_id = ?` refuses a result written into another agent's slot -- which is
        what `validate_progress`' "completed results must match the graph prefix" would have
        caught, one `get()` later, as a database that no longer reads. `payload IS NULL`
        refuses a second write to a completed slot, which makes a run's stored prefix
        append-only and is what licenses this method touching no other row.

        `updated_at` is patched onto the header with `json_set` rather than by reading,
        parsing and rewriting it -- the header is a few hundred bytes with the graph gone, so
        this is the cheap case, exactly as `SQLiteBatchTaskStore.update_item` records for the
        same statement.
        """
        payload = result.model_dump_json(exclude_computed_fields=True)
        with closing(self._connect()) as connection, connection:
            claimed = self._claim_slot(connection, run_id, position, result.agent_id, payload)
            if not claimed:
                self._split_legacy_row(connection, run_id)
                claimed = self._claim_slot(connection, run_id, position, result.agent_id, payload)
            if not claimed:
                raise RecoveryConflictError(
                    f"no unwritten recovery slot for {result.agent_id!r} at position {position} "
                    f"of run {run_id}: the graph does not declare that agent there, the slot is "
                    "already complete, or the run has no stored recovery state"
                )
            connection.execute(
                "UPDATE run_recovery SET payload = json_set(payload, '$.updated_at', ?) "
                "WHERE run_id = ?",
                (updated_at.isoformat(), run_id),
            )

    @staticmethod
    def _claim_slot(
        connection: sqlite3.Connection,
        run_id: str,
        position: int,
        agent_id: str,
        payload: str,
    ) -> bool:
        cursor = connection.execute(
            "UPDATE run_recovery_results SET payload = ? "
            "WHERE run_id = ? AND position = ? AND agent_id = ? AND payload IS NULL",
            (payload, run_id, position, agent_id),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _write_slots(
        connection: sqlite3.Connection,
        run_id: str,
        slots: Sequence[tuple[int, str, str | None]],
    ) -> None:
        connection.execute("DELETE FROM run_recovery_results WHERE run_id = ?", (run_id,))
        connection.executemany(
            "INSERT INTO run_recovery_results (run_id, position, agent_id, payload) "
            "VALUES (?, ?, ?, ?)",
            [(run_id, position, agent_id, payload) for position, agent_id, payload in slots],
        )

    def _split_legacy_row(self, connection: sqlite3.Connection, run_id: str) -> None:
        """Convert one pre-`V2-P4-020` blob row into a header and its slots, in place.

        Reached only when `append_result` found no slot to claim, so the normal path pays
        nothing for it. A run stored before the split has its whole state in the payload and
        no slot rows at all, and the engine can resume such a run without going through
        `save()` first -- `_load_or_start_recovery` returns a `running` state unchanged -- so
        this is the one path on which an old database meets the new hot write.
        """
        row = connection.execute(
            "SELECT payload FROM run_recovery WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or "completed_results" not in json.loads(row[0]):
            return
        state = read_versioned(RUN_RECOVERY_STATE_VERSIONS, row[0])
        header, slots = split_recovery_payload(state)
        connection.execute("UPDATE run_recovery SET payload = ? WHERE run_id = ?", (header, run_id))
        self._write_slots(connection, run_id, slots)

    def clear(self, run_id: str) -> bool:
        """Delete operational recovery state without touching immutable ledgers."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM run_recovery WHERE run_id = ?",
                (run_id,),
            )
        return cursor.rowcount == 1
