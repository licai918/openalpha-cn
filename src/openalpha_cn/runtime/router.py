"""Evidence-aware deterministic agent routing."""

from collections.abc import Mapping, Sequence

from openalpha_cn.agents.base import ResearchAgent
from openalpha_cn.domain.evidence import EvidenceSnapshot


class AgentRouter:
    """Select agents whose declared evidence families are present."""

    def route(
        self,
        *,
        agents: Sequence[ResearchAgent],
        evidence: tuple[EvidenceSnapshot, ...],
    ) -> tuple[ResearchAgent, ...]:
        """Return selected agents in the configured stable order."""
        families: set[str] = set()
        for item in evidence:
            if isinstance(item.payload, Mapping):
                families.add(str(item.payload.get("family", "")))
        return tuple(agent for agent in agents if agent.evidence_families & families)
