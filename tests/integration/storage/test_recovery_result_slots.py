"""`V2-P4-020`'s storage half: the slot table, its two refusals, and the shape it replaced.

`tests/integration/test_recovery_write_amplification.py` measures the cost. This file covers
what the cheaper write must not lose, which is the part a performance change is most likely to
break quietly: `V2-P4-019`'s own audit exists because a split that drops one row or reorders
two still reads back as a perfectly valid document reporting the wrong work done.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openalpha_cn.agents.base import AgentProvenance, AgentResult
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import (
    MigrationFailedError,
    UnmigratableHorizonError,
    run_migrations,
)
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore
from openalpha_cn.storage.recovery import (
    RecoveryConflictError,
    RunRecoveryState,
    SQLiteRecoveryStore,
)
from openalpha_cn.storage.sqlite import SQLiteRunRepository

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
DIGEST = "a" * 64
SIGNATURE = "b" * 64
PROVENANCE = AgentProvenance(kind="deterministic")


def _result(index: int, *, horizon: str = "5d") -> AgentResult:
    return AgentResult(
        agent_id=f"agent-{index}",
        signal=SignalFrame(
            subject="000001.SZ",
            as_of=NOW,
            direction="bullish",
            strength=0.5,
            confidence=0.7,
            horizon=horizon,
            evidence_ids=("ev-1",),
        ),
        rationale=f"agent-{index} completed.",
    )


def _state(*, completed: int, agents: int = 3, **overrides: object) -> RunRecoveryState:
    fields: dict[str, object] = {
        "run_id": "run-slots",
        "request_digest": DIGEST,
        "graph_signature": SIGNATURE,
        "agent_ids": tuple(f"agent-{index}" for index in range(agents)),
        "completed_results": tuple(_result(index) for index in range(completed)),
        "next_agent_index": completed,
        "started_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    return RunRecoveryState.model_validate(fields)


def test_a_run_saved_and_advanced_reads_back_as_the_state_it_was_built_from(
    tmp_path: Path,
) -> None:
    """The round trip, whole-model rather than field-by-field.

    Equality of the reassembled state against one built directly is the check, for the reason
    `_audit_batch_item_split` gives: a graph whose slots came back in a different order, or
    short by one, has the right count and the wrong answer.
    """
    store = SQLiteRecoveryStore(tmp_path / "state.sqlite3")
    store.save(_state(completed=0))

    for index in range(3):
        store.append_result("run-slots", position=index, result=_result(index), updated_at=NOW)

    assert SQLiteRecoveryStore(tmp_path / "state.sqlite3").get("run-slots") == _state(completed=3)


def test_the_header_row_no_longer_carries_the_results_or_the_graph(tmp_path: Path) -> None:
    """The property the whole cost argument rests on, asserted against the stored bytes.

    If the header still carried `completed_results`, every `save()` would still serialise
    every result and the amplification measurement would be passing on a tree that had only
    moved the work. The three names come from `DERIVED_FROM_RESULT_ROWS`, and the point of
    checking them here rather than importing the set is that this is the file where they are
    read back off disk.
    """
    path = tmp_path / "state.sqlite3"
    store = SQLiteRecoveryStore(path)
    store.save(_state(completed=0))
    store.append_result("run-slots", position=0, result=_result(0), updated_at=NOW)

    with sqlite3.connect(path) as connection:
        header = connection.execute(
            "SELECT payload FROM run_recovery WHERE run_id = ?", ("run-slots",)
        ).fetchone()[0]
        slots = connection.execute(
            "SELECT position, agent_id, payload FROM run_recovery_results "
            "WHERE run_id = ? ORDER BY position",
            ("run-slots",),
        ).fetchall()

    document = json.loads(header)
    assert "completed_results" not in document
    assert "agent_ids" not in document
    assert "next_agent_index" not in document
    assert [(position, agent_id) for position, agent_id, _ in slots] == [
        (0, "agent-0"),
        (1, "agent-1"),
        (2, "agent-2"),
    ]
    assert slots[0][2] is not None
    assert [payload for _, _, payload in slots[1:]] == [None, None]


def test_appending_into_another_agents_slot_is_refused(tmp_path: Path) -> None:
    """The first of the two `WHERE` guards, which is `validate_progress`' prefix rule
    evaluated at the one write that could break it.

    Without it the result would land in the row and the run would read back as a state whose
    completed results are not a prefix of its graph -- a failure discovered at the next `get()`
    as a validation error about the graph, with nothing left saying which write caused it.
    """
    store = SQLiteRecoveryStore(tmp_path / "state.sqlite3")
    store.save(_state(completed=0))

    with pytest.raises(RecoveryConflictError, match="no unwritten recovery slot"):
        store.append_result("run-slots", position=1, result=_result(0), updated_at=NOW)


def test_a_completed_slot_cannot_be_written_twice(tmp_path: Path) -> None:
    """The second guard, `payload IS NULL`, which is what makes a run's stored prefix
    append-only -- and therefore what licenses `append_result` touching no other row."""
    store = SQLiteRecoveryStore(tmp_path / "state.sqlite3")
    store.save(_state(completed=0))
    store.append_result("run-slots", position=0, result=_result(0), updated_at=NOW)

    with pytest.raises(RecoveryConflictError, match="no unwritten recovery slot"):
        store.append_result("run-slots", position=0, result=_result(0), updated_at=NOW)


def test_a_row_written_before_the_split_still_reads_and_then_converts_itself(
    tmp_path: Path,
) -> None:
    """The reason `V2-P4-020` ships no migration, driven rather than argued.

    A pre-split row is the whole state in one payload with no slot rows. `get()` has to answer
    it unchanged -- `_load_or_start_recovery` returns a `running` state without saving, so a
    process that crashed mid-run and restarted meets exactly this row -- and the next
    `append_result` has to convert it rather than refuse, because there is no slot to claim.
    Both halves are here because only the pair makes the no-migration decision safe.
    """
    path = tmp_path / "state.sqlite3"
    SQLiteRecoveryStore(path)
    legacy = _state(completed=1)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO run_recovery (run_id, request_digest, graph_signature, status, payload)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                legacy.run_id,
                legacy.request_digest,
                legacy.graph_signature,
                legacy.status,
                legacy.model_dump_json(exclude_computed_fields=True),
            ),
        )

    store = SQLiteRecoveryStore(path)
    assert store.get("run-slots") == legacy

    store.append_result("run-slots", position=1, result=_result(1), updated_at=NOW)

    assert store.get("run-slots") == _state(completed=2)
    with sqlite3.connect(path) as connection:
        header = connection.execute(
            "SELECT payload FROM run_recovery WHERE run_id = ?", ("run-slots",)
        ).fetchone()[0]
    assert "completed_results" not in json.loads(header)


def test_clearing_a_run_takes_its_slots_with_it(tmp_path: Path) -> None:
    """`ON DELETE CASCADE` plus `open_state_connection`'s `PRAGMA foreign_keys = ON`.

    Asserted rather than assumed because the pragma is per-connection and defaults off --
    `storage/connection.py` exists for exactly that reason -- and orphaned slot rows would be
    invisible until a later run reused the ID and found its graph already populated.
    """
    path = tmp_path / "state.sqlite3"
    store = SQLiteRecoveryStore(path)
    store.save(_state(completed=0))
    store.append_result("run-slots", position=0, result=_result(0), updated_at=NOW)

    assert store.clear("run-slots") is True

    with sqlite3.connect(path) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM run_recovery_results WHERE run_id = ?", ("run-slots",)
        ).fetchone()[0]
    assert remaining == 0
    assert store.get("run-slots") is None


def test_the_horizon_migration_still_sees_signals_now_that_they_live_in_slot_rows(
    tmp_path: Path,
    migration_clock,
) -> None:
    """`_refuse_uncountable_stored_horizons` reads the recovery plane, and the plane moved.

    That pass exists because the recovery tables are the only place a whole `SignalFrame` is
    stored, so a horizon `SignalFrame` no longer admits would make the rewrite produce rows
    that cannot be read back. It looked in `run_recovery.payload` alone. After the split a
    completed result is in `run_recovery_results`, and a pass still reading only the payload
    would have reported no offenders on every database written from now on -- passing, and
    inspecting nothing.

    Driven by writing the offending horizon straight into a slot row, because
    `SignalFrame` refuses to construct one: the situation this guards is a database written
    by an older version of the code, which is not a state the current models can produce.

    The other stores are constructed for `_build_pre_p4_database`'s reason: the pass under
    test runs inside `rewrite_contract_identities`, which defers until the tables the
    migrations before it require exist, and a deferred migration inspects nothing.
    """
    path = tmp_path / "state.sqlite3"
    for build in (
        SQLiteRunRepository,
        SQLiteResearchMemory,
        SQLiteReportStore,
        SQLiteBatchTaskStore,
        SQLitePortfolioLedger,
    ):
        build(path)
    store = SQLiteRecoveryStore(path)
    store.save(_state(completed=0))
    store.append_result("run-slots", position=0, result=_result(0), updated_at=NOW)
    payload = json.loads(_result(0).model_dump_json(exclude_computed_fields=True))
    payload["signal"]["horizon"] = "1m"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE run_recovery_results SET payload = ? WHERE run_id = ? AND position = 0",
            (json.dumps(payload), "run-slots"),
        )

    with pytest.raises(MigrationFailedError) as error:
        run_migrations(path, clock=migration_clock)

    cause = error.value.__cause__
    assert isinstance(cause, UnmigratableHorizonError)
    assert "run-slots" in str(cause)
    assert "1m" in str(cause)
