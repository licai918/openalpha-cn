"""Deterministic research-cycle contracts: request/result models and the conflict error.

Split out of `runtime/engine.py` (V2-P0B-001) so that modules which only need
`ResearchRunRequest`/`ResearchRunResult`/`RunConflictError` are not forced to transitively
depend on `ResearchEngine`'s SQLite storage (`storage.recovery`, `storage.sqlite`). This
module must stay free of any import of `runtime.engine` or `openalpha_cn.storage`.

`AgentResult` is imported from `domain.agent_result`, not `agents.base`, since V2-P0B-012:
`agents.base` re-exports the identical object (see that module and
`domain/agent_result.py`'s docstring), but importing it from `agents.base` here used to mean
that any module reaching this file -- including `openalpha_cn.batch_contracts`, which needs
`ResearchRunRequest` for `BatchTaskItem.request` -- transitively pulled in the whole
`agents`/`models` subsystem (`agents/__init__.py` eagerly imports `agents.baseline`/
`agents.model`, which import `models.base`/`models/__init__.py` in turn) just to obtain one
plain data type. Routing through `domain.agent_result` instead removes that edge entirely
with no change in behavior (same class object either way).
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.agent_result import AgentResult
from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import ensure_aware


class RunConflictError(RuntimeError):
    """Raised when a run ID is reused with different immutable inputs."""


class ResearchRunRequest(BaseModel):
    """All deterministic inputs to one research cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    mode: Literal["live", "replay", "backtest"]
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    evidence: tuple[EvidenceSnapshot, ...]
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    random_seed: int

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if any(item.subject != self.subject for item in self.evidence):
            raise ValueError("all evidence must match the requested subject")
        if any(not item.visible_at(self.as_of) for item in self.evidence):
            raise ValueError("evidence is not visible at request as_of")
        return self


class ResearchRunResult(BaseModel):
    """Signal, decision, manifest, and agent outputs from one cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: SignalFrame
    decision: DecisionLedger
    manifest: RunManifest
    agent_results: tuple[AgentResult, ...]
