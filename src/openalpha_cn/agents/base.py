"""Stable contracts for research agents."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.signal import SignalFrame


class AgentContext(BaseModel):
    """Point-in-time evidence and run context supplied to an agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    evidence: tuple[EvidenceSnapshot, ...]


class AgentResult(BaseModel):
    """One agent's validated signal and rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=128)
    signal: SignalFrame
    rationale: str = Field(min_length=1, max_length=4000)


class ResearchAgent(Protocol):
    """Extension contract for deterministic or model-backed agents."""

    agent_id: str
    evidence_families: frozenset[str]

    def analyze(self, context: AgentContext) -> AgentResult:
        """Return one schema-validated research result."""
