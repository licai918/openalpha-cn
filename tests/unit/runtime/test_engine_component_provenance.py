"""`V2-P4-010`: how `ResearchEngine` turns agent declarations into manifest slots.

`tests/integration/test_manifest_model_provenance.py` drives the whole cycle and is the test
that matters; these three exercise the two properties that cycle cannot separate, because every
run there has exactly one agent in it. A one-agent fixture cannot tell a lookup keyed by
`agent_id` from a positional zip, cannot tell a de-duplicated model list from a list that never
had a duplicate, and never reaches the guard on a result whose agent declared nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import pytest

from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult
from openalpha_cn.domain.run import VersionRef
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.runtime.engine import ResearchEngine

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)


class _Agent:
    """The smallest thing that satisfies `ResearchAgent`, so the declaration is the variable."""

    def __init__(self, agent_id: str, provenance: AgentProvenance) -> None:
        self.agent_id = agent_id
        self.evidence_families = frozenset({"market_event"})
        self.feature_dependencies: frozenset[str] = frozenset()
        self.provenance = provenance

    def analyze(self, context: AgentContext) -> AgentResult:  # pragma: no cover - never called
        raise NotImplementedError


def _result(agent_id: str) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        signal=SignalFrame(
            subject="000001.SZ",
            as_of=NOW,
            direction="bullish",
            strength=0.4,
            confidence=0.6,
            horizon="5d",
            evidence_ids=("ev_golden",),
        ),
        rationale="Price and volume confirm the event.",
    )


def _llm(model: str) -> AgentProvenance:
    return AgentProvenance(
        kind="llm_backed", model=VersionRef(component="openai-compatible", version=model)
    )


def test_each_result_is_paired_with_its_own_agents_declaration_not_its_neighbours() -> None:
    """The property a positional zip satisfies by luck and gets wrong under recovery.

    `results` is assembled from two sources on a resumed run -- `state.completed_results` plus
    whatever ran after `next_agent_index` -- so "results are in `selected` order" is an
    invariant of one code path rather than a property of the arguments. The two agents here are
    presented in *opposite* orders, so a zip attaches the LLM declaration to the deterministic
    agent and the assertion below fails; a lookup keyed by `agent_id` cannot.
    """
    deterministic = _Agent("market-agent", AgentProvenance(kind="deterministic"))
    llm_backed = _Agent("llm-agent", _llm("qwen-max"))

    versions = ResearchEngine._agent_versions(
        selected=(deterministic, llm_backed),
        results=(_result("llm-agent"), _result("market-agent")),
    )

    assert [item.model_dump() for item in versions] == [
        {"agent_id": "llm-agent", "kind": "llm_backed"},
        {"agent_id": "market-agent", "kind": "deterministic"},
    ]


def test_two_agents_on_one_model_record_one_model_version_and_two_models_record_two() -> None:
    """A manifest records what a run depended on, not how many times it depended on it.

    Both directions in one test on purpose: de-duplication that also collapsed *distinct*
    models would satisfy the first assertion alone, and this repository has shipped a
    "correct" de-duplication that erased a real difference before. Order is asserted as
    first-call rather than sorted, because `"deepseek-chat"` sorts before `"qwen-max"` and a
    `sorted()` implementation would therefore pass an order-blind assertion.
    """
    same = ResearchEngine._model_versions(
        selected=(_Agent("a", _llm("qwen-max")), _Agent("b", _llm("qwen-max"))),
        results=(_result("a"), _result("b")),
    )
    different = ResearchEngine._model_versions(
        selected=(_Agent("a", _llm("qwen-max")), _Agent("b", _llm("deepseek-chat"))),
        results=(_result("a"), _result("b")),
    )

    assert [item.version for item in same] == ["qwen-max"]
    assert [item.version for item in different] == ["qwen-max", "deepseek-chat"]


def test_a_deterministic_agent_contributes_nothing_to_the_model_plane() -> None:
    """The slot is empty for a run with no model in it -- the answer the old engine could not
    give, because it wrote one `model_versions` entry per agent unconditionally."""
    versions = ResearchEngine._model_versions(
        selected=(_Agent("market-agent", AgentProvenance(kind="deterministic")),),
        results=(_result("market-agent"),),
    )

    assert versions == ()


def test_a_result_from_an_agent_that_declared_nothing_is_refused_rather_than_dropped() -> None:
    """Fail closed on a roster the manifest cannot describe.

    `graph_signature` makes this unreachable through `run_cycle` today -- a recovery state whose
    agent ids differ from the current panel is refused as a `RunConflictError` before any of
    this runs -- so the guard is driven directly rather than through a cycle contrived to reach
    it. Skipping the result instead would produce a manifest that under-reports the roster,
    which is a run declaration that does not describe its run, and nothing downstream could
    tell.
    """
    with pytest.raises(ValueError, match="no declared provenance"):
        ResearchEngine._agent_versions(
            selected=(_Agent("market-agent", AgentProvenance(kind="deterministic")),),
            results=(_result("market-agent"), _result("ghost-agent")),
        )
