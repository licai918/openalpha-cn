from datetime import UTC, datetime
from pathlib import Path

import pytest

from openalpha_cn.agents.base import AgentContext, AgentResult
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest, RunConflictError
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
DIGEST = "b" * 64


def evidence(*, kind: str, facts: dict[str, object]) -> EvidenceSnapshot:
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
            "family": "market_event",
            "facts": facts,
            "quality_flags": [],
        },
    )


class RecoverableAgent:
    evidence_families = frozenset({"market_event"})

    def __init__(self, agent_id: str, *, fail_once: bool = False) -> None:
        self.agent_id = agent_id
        self.fail_once = fail_once
        self.calls = 0

    def analyze(self, context: AgentContext) -> AgentResult:
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated provider interruption")
        item = context.evidence[0]
        return AgentResult(
            agent_id=self.agent_id,
            signal=SignalFrame(
                subject=context.subject,
                as_of=context.as_of,
                direction="bullish",
                strength=0.5,
                confidence=0.7,
                horizon="5d",
                evidence_ids=(item.evidence_id,),
            ),
            rationale=f"{self.agent_id} completed.",
        )


def research_request(*, config_digest: str = DIGEST) -> ResearchRunRequest:
    item = evidence(
        kind="limit_up",
        facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
    )
    return ResearchRunRequest(
        run_id="run_recovery_20260724",
        mode="replay",
        subject="000001.SZ",
        as_of=NOW,
        evidence=(item,),
        code_commit="0123456789abcdef",
        config_digest=config_digest,
        random_seed=7,
    )


def test_research_memory_survives_process_restart_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    entry = MemoryEntry(
        run_id="run-memory",
        subject="000001.SZ",
        created_at=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
        decision_id="dec-memory",
        signal_id="sig-memory",
        summary="watch: bullish at confidence 0.70",
    )

    SQLiteResearchMemory(path).append(entry)
    reopened = SQLiteResearchMemory(path)
    reopened.append(entry)

    assert reopened.list(subject="000001.SZ") == (entry,)
    with pytest.raises(ValueError, match="decision_id conflicts"):
        reopened.append(entry.model_copy(update={"summary": "conflicting memory"}))


def test_cycle_resumes_after_last_completed_agent_without_repeating_work(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    repository = SQLiteRunRepository(path)
    memory = SQLiteResearchMemory(path)
    first = RecoverableAgent("first-agent")
    second = RecoverableAgent("second-agent", fail_once=True)
    engine = ResearchEngine(
        repository=repository,
        memory=memory,
        clock=lambda: NOW,
        agents=(first, second),
    )

    with pytest.raises(RuntimeError, match="simulated provider interruption"):
        engine.run_cycle(research_request())

    interrupted = SQLiteRecoveryStore(path).get("run_recovery_20260724")
    assert interrupted is not None
    assert interrupted.status == "failed"
    assert interrupted.next_agent_index == 1
    assert tuple(item.agent_id for item in interrupted.completed_results) == ("first-agent",)

    result = engine.run_cycle(research_request())

    assert first.calls == 1
    assert second.calls == 2
    assert result.decision.routing_path == ("first-agent", "second-agent", "risk-gate")
    completed = SQLiteRecoveryStore(path).get("run_recovery_20260724")
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.next_agent_index == 2
    assert len(memory.list(subject="000001.SZ")) == 1


def test_recovery_isolated_by_immutable_request_and_graph_signature(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    first = RecoverableAgent("first-agent")
    second = RecoverableAgent("second-agent", fail_once=True)
    engine = ResearchEngine(
        repository=SQLiteRunRepository(path),
        memory=SQLiteResearchMemory(path),
        clock=lambda: NOW,
        agents=(first, second),
    )
    with pytest.raises(RuntimeError):
        engine.run_cycle(research_request())

    with pytest.raises(RunConflictError, match="immutable request"):
        engine.run_cycle(research_request(config_digest="c" * 64))

    changed_graph = ResearchEngine(
        repository=SQLiteRunRepository(path),
        memory=SQLiteResearchMemory(path),
        clock=lambda: NOW,
        agents=(first, RecoverableAgent("replacement-agent")),
    )
    with pytest.raises(RunConflictError, match="graph signature"):
        changed_graph.run_cycle(research_request())
