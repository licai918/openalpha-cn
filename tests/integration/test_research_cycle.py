from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

DIGEST = "b" * 64


@pytest.fixture
def research_request(
    frozen_now: datetime,
) -> Callable[[tuple[EvidenceSnapshot, ...]], ResearchRunRequest]:
    """Builds the `ResearchRunRequest` under test from a caller-supplied evidence tuple.

    Not named `request`: that is pytest's own reserved built-in fixture name. Not
    collapsed into a suite-wide fixture either -- this takes a pre-built evidence tuple
    with a *fixed* `run_id` (so replaying the identical request twice is idempotent by
    construction), a different parameterization axis from
    `tests/integration/test_batch_research.py`'s `request(run_id, subject)`, which varies
    `run_id`/`subject` and always builds its own single canned evidence item. See
    task-13-report.md for the full comparison.
    """

    def _make(items: tuple[EvidenceSnapshot, ...]) -> ResearchRunRequest:
        return ResearchRunRequest(
            run_id="run_golden_20260724",
            mode="replay",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=items,
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
        )

    return _make


def test_multi_agent_cycle_persists_evidence_linked_decision_idempotently(
    tmp_path: Path,
    evidence,
    research_request,
    frozen_now: datetime,
) -> None:
    items = (
        evidence(
            kind="limit_up",
            facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
        ),
        evidence(kind="theme", facts={"theme": "机器人", "score": 0.82}),
        evidence(kind="capital", facts={"net_inflow": 1_200_000, "unit": "CNY"}),
    )
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    memory = InMemoryResearchMemory()
    recovery_store = SQLiteRecoveryStore(tmp_path / "state.sqlite3")
    engine = ResearchEngine(
        repository=repository,
        memory=memory,
        clock=lambda: frozen_now,
        recovery_store=recovery_store,
    )

    first = engine.run_cycle(research_request(items))
    second = engine.run_cycle(research_request(items))

    assert first == second
    assert first.signal.direction == "bullish"
    assert set(first.signal.evidence_ids) == {item.evidence_id for item in items}
    assert first.decision.final_action == "watch"
    assert first.decision.risk_decision == "pass"
    assert first.decision.routing_path == (
        "market-agent",
        "theme-agent",
        "capital-agent",
        "risk-gate",
    )
    assert repository.get_run(first.manifest.run_id) == first.manifest
    assert repository.get_decision_for_run(first.manifest.run_id) == first.decision
    assert len(memory.list(subject="000001.SZ")) == 1


def test_cycle_abstains_explicitly_when_evidence_is_insufficient(
    tmp_path: Path,
    research_request,
    frozen_now: datetime,
) -> None:
    engine = ResearchEngine(
        repository=SQLiteRunRepository(tmp_path / "state.sqlite3"),
        memory=InMemoryResearchMemory(),
        clock=lambda: frozen_now,
        recovery_store=SQLiteRecoveryStore(tmp_path / "state.sqlite3"),
    )

    result = engine.run_cycle(research_request(()))

    assert result.signal.direction == "abstain"
    assert result.signal.abstention_reason == "No supported point-in-time evidence was available."
    assert result.decision.final_action == "abstain"
    assert result.decision.evidence_ids == ()
    assert result.manifest.status == "succeeded"
