"""Stable contracts for research agents."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.domain.agent_result import AgentResult
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import AgentProvenance

__all__ = ["AgentContext", "AgentProvenance", "AgentResult", "ResearchAgent"]


class AgentContext(BaseModel):
    """Point-in-time evidence and run context supplied to an agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    evidence: tuple[EvidenceSnapshot, ...]


class ResearchAgent(Protocol):
    """Extension contract for deterministic or model-backed agents."""

    agent_id: str
    evidence_families: frozenset[str]
    provenance: AgentProvenance
    """What this agent is, for the run manifest (`V2-P4-010`, S40).

    Required rather than optional, and declared by the agent rather than inferred from its
    type. `ResearchEngine` could distinguish this repository's own `StructuredSignalAgent`
    from its own `MarketAgent` with an `isinstance` check, and would then record every *other*
    `ModelProvider`-backed agent as deterministic -- a silent wrong answer about the one fact
    S40 asks a manifest to carry, arrived at by a mechanism that looks like it works. An agent
    that omits this fails structurally at the point it is handed to the engine instead.
    """

    def analyze(self, context: AgentContext) -> AgentResult:
        """Return one schema-validated research result."""
