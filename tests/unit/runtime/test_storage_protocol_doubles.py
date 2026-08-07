"""Prove the storage Protocols (V2-P0B-003) are real abstractions, not decoration.

ADR-0001 claims a storage interface exists that would permit a later PostgreSQL
implementation. Before this task, `ResearchEngine` was typed directly against
`SQLiteRunRepository` and `SQLiteRecoveryStore` (`runtime/engine.py:30,36`), and even
constructed its own `SQLiteRecoveryStore` from `repository.path` (`runtime/engine.py:44`)
-- so nothing but a real SQLite file could ever satisfy it. `runtime.memory.ResearchMemory`
was the sole exception.

This module is the only convincing acceptance test for the fix: in-memory doubles for
`RunRepository` (`runtime.repository`) and `RecoveryStore` (`runtime.recovery`) that touch
no disk and import no `sqlite3` anywhere in this file, driving `ResearchEngine.run_cycle`
through a complete cycle -- run once, then replayed idempotently, then probed for the
run-conflict path -- exactly as the SQLite-backed integration tests in
`tests/integration/test_research_cycle.py` and `test_recovery_and_memory.py` do. If a
Protocol had leaked an implementation detail (e.g. a `.path` attribute, or a SQLite-specific
upsert method), one of these doubles would be unable to satisfy it and this file would fail
to even construct `ResearchEngine`.
"""

from datetime import UTC, datetime

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest, RunConflictError
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.runtime.recovery import RecoveryStore
from openalpha_cn.runtime.repository import RunRepository
from openalpha_cn.storage.recovery import RunRecoveryState

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
DIGEST = "b" * 64


class InMemoryRunRepository:
    """A `RunRepository` double backed by plain dicts.

    Deliberately independent of `storage.sqlite.SQLiteRunRepository`: no shared base
    class, no delegation to a real connection, no `import sqlite3` anywhere in this file.
    """

    def __init__(self) -> None:
        self._runs: dict[str, RunManifest] = {}
        self._decisions_by_run: dict[str, DecisionLedger] = {}

    def append_run(self, manifest: RunManifest) -> None:
        self._runs[manifest.run_id] = manifest

    def get_run(self, run_id: str) -> RunManifest | None:
        return self._runs.get(run_id)

    def append_decision(self, decision: DecisionLedger) -> None:
        self._decisions_by_run[decision.run_id] = decision

    def get_decision_for_run(self, run_id: str) -> DecisionLedger | None:
        return self._decisions_by_run.get(run_id)


class InMemoryRecoveryStore:
    """A `RecoveryStore` double backed by a plain dict.

    Deliberately independent of `storage.recovery.SQLiteRecoveryStore`: no shared base
    class, no delegation to a real connection, no `import sqlite3` anywhere in this file.
    """

    def __init__(self) -> None:
        self._states: dict[str, RunRecoveryState] = {}

    def get(self, run_id: str) -> RunRecoveryState | None:
        return self._states.get(run_id)

    def save(self, state: RunRecoveryState) -> None:
        self._states[state.run_id] = state


def _evidence(*, kind: str, facts: dict[str, object]) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind=kind,
        timeline=Timeline(
            event_time=NOW,
            available_time=NOW,
            ingested_time=NOW,
            revision_time=NOW,
        ),
        source_id="synthetic.a-share",
        source_uri=f"fixture://{kind}/000001.SZ",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary=f"Synthetic {kind}.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": {
                "limit_up": "market_event",
                "theme": "theme",
                "capital": "capital",
            }[kind],
            "facts": facts,
            "quality_flags": [],
        },
    )


def _request(*, run_id: str, config_digest: str = DIGEST) -> ResearchRunRequest:
    items = (
        _evidence(
            kind="limit_up",
            facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
        ),
        _evidence(kind="theme", facts={"theme": "机器人", "score": 0.82}),
        _evidence(kind="capital", facts={"net_inflow": 1_200_000, "unit": "CNY"}),
    )
    return ResearchRunRequest(
        run_id=run_id,
        mode="replay",
        subject="000001.SZ",
        as_of=NOW,
        evidence=items,
        code_commit="0123456789abcdef",
        config_digest=config_digest,
        random_seed=7,
    )


def _engine(*, repository: RunRepository, recovery_store: RecoveryStore) -> ResearchEngine:
    return ResearchEngine(
        repository=repository,
        memory=InMemoryResearchMemory(),
        clock=lambda: NOW,
        recovery_store=recovery_store,
    )


def test_in_memory_doubles_drive_a_full_research_cycle_without_sqlite() -> None:
    """Pure-Python doubles for `RunRepository`/`RecoveryStore` (plus the pre-existing
    `InMemoryResearchMemory`) must be sufficient, on their own, to run a full research
    cycle end to end -- evidence in, signal/decision/manifest out, all three stores
    updated -- exactly like a real SQLite-backed engine."""
    repository = InMemoryRunRepository()
    recovery_store = InMemoryRecoveryStore()
    memory = InMemoryResearchMemory()
    engine = ResearchEngine(
        repository=repository,
        memory=memory,
        clock=lambda: NOW,
        recovery_store=recovery_store,
    )

    result = engine.run_cycle(_request(run_id="run_double_20260724"))

    assert result.manifest.status == "succeeded"
    assert result.signal.direction == "bullish"
    assert result.decision.final_action == "watch"
    assert repository.get_run(result.manifest.run_id) == result.manifest
    assert repository.get_decision_for_run(result.manifest.run_id) == result.decision
    recovered = recovery_store.get(result.manifest.run_id)
    assert recovered is not None
    assert recovered.status == "succeeded"
    assert len(memory.list(subject="000001.SZ")) == 1


def test_in_memory_doubles_make_a_replay_idempotent() -> None:
    """Running the identical request twice through the double-backed engine must return
    byte-for-byte the same result, exactly as the SQLite-backed
    `test_multi_agent_cycle_persists_evidence_linked_decision_idempotently` integration
    test proves for the real stores."""
    engine = _engine(repository=InMemoryRunRepository(), recovery_store=InMemoryRecoveryStore())
    request = _request(run_id="run_double_idempotent")

    first = engine.run_cycle(request)
    second = engine.run_cycle(request)

    assert first == second


def test_in_memory_doubles_reject_a_conflicting_reuse_of_the_same_run_id() -> None:
    """Reusing a run_id with different immutable inputs against the double-backed engine
    must raise `RunConflictError`, exactly as it does against the real SQLite stores."""
    engine = _engine(repository=InMemoryRunRepository(), recovery_store=InMemoryRecoveryStore())
    engine.run_cycle(_request(run_id="run_double_conflict"))

    try:
        engine.run_cycle(_request(run_id="run_double_conflict", config_digest="c" * 64))
    except RunConflictError as error:
        assert "immutable request" in str(error)
    else:
        raise AssertionError("conflicting run_id reuse must be rejected")
