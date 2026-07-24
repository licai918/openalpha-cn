from datetime import UTC, datetime
from pathlib import Path

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.engine import ResearchEngine, ResearchRunRequest
from openalpha_cn.runtime.memory import InMemoryResearchMemory
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
            "family": {
                "limit_up": "market_event",
                "theme": "theme",
                "capital": "capital",
            }[kind],
            "facts": facts,
            "quality_flags": [],
        },
    )


def request(items: tuple[EvidenceSnapshot, ...]) -> ResearchRunRequest:
    return ResearchRunRequest(
        run_id="run_golden_20260724",
        mode="replay",
        subject="000001.SZ",
        as_of=NOW,
        evidence=items,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
    )


def test_multi_agent_cycle_persists_evidence_linked_decision_idempotently(
    tmp_path: Path,
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
    engine = ResearchEngine(repository=repository, memory=memory, clock=lambda: NOW)

    first = engine.run_cycle(request(items))
    second = engine.run_cycle(request(items))

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


def test_cycle_abstains_explicitly_when_evidence_is_insufficient(tmp_path: Path) -> None:
    engine = ResearchEngine(
        repository=SQLiteRunRepository(tmp_path / "state.sqlite3"),
        memory=InMemoryResearchMemory(),
        clock=lambda: NOW,
    )

    result = engine.run_cycle(request(()))

    assert result.signal.direction == "abstain"
    assert result.signal.abstention_reason == "No supported point-in-time evidence was available."
    assert result.decision.final_action == "abstain"
    assert result.decision.evidence_ids == ()
    assert result.manifest.status == "succeeded"


def test_cycle_rejects_evidence_that_was_not_visible_at_as_of(tmp_path: Path) -> None:
    future = evidence(
        kind="limit_up",
        facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
    ).model_copy(
        update={
            "timeline": Timeline(
                event_time=NOW,
                available_time=datetime(2026, 7, 24, 11, 0, tzinfo=UTC),
                ingested_time=datetime(2026, 7, 24, 11, 1, tzinfo=UTC),
                revision_time=datetime(2026, 7, 24, 11, 0, tzinfo=UTC),
            )
        }
    )
    engine = ResearchEngine(
        repository=SQLiteRunRepository(tmp_path / "state.sqlite3"),
        memory=InMemoryResearchMemory(),
        clock=lambda: NOW,
    )

    try:
        engine.run_cycle(request((future,)))
    except ValueError as error:
        assert "not visible" in str(error)
    else:
        raise AssertionError("future evidence must be rejected")
