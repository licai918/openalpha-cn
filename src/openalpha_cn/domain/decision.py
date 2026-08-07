"""Append-only decision record contracts."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.run import VersionRef
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions


class AgentDecision(BaseModel):
    """One agent's structured contribution to a decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=128)
    signal_id: str = Field(min_length=1, max_length=128)
    recommendation: Literal["support", "oppose", "abstain"]
    rationale: str = Field(min_length=1, max_length=4000)


class DecisionLedger(BaseModel):
    """An immutable record intended to be appended to the decision ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["decision-ledger/v1"] = "decision-ledger/v1"
    run_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    agent_outputs: tuple[AgentDecision, ...] = ()
    routing_path: tuple[str, ...] = ()
    risk_decision: Literal["pass", "reduce", "block"]
    final_action: Literal["watch", "avoid", "abstain"]
    evidence_ids: tuple[str, ...] = ()
    signal_ids: tuple[str, ...] = ()
    code_commit: str = Field(min_length=7, max_length=64)
    model_versions: tuple[VersionRef, ...] = ()
    prompt_versions: tuple[VersionRef, ...] = ()

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.final_action != "abstain" and (not self.evidence_ids or not self.signal_ids):
            raise ValueError("non-abstaining decision requires evidence_ids and signal_ids")
        output_signal_ids = {item.signal_id for item in self.agent_outputs}
        if not output_signal_ids.issubset(set(self.signal_ids)):
            raise ValueError("agent output signal_id must be listed in signal_ids")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def decision_id(self) -> str:
        """Return the stable content-derived decision identifier."""
        return stable_model_id(prefix="dec", model=self)


DECISION_LEDGER_VERSIONS: ContractVersions[DecisionLedger] = ContractVersions(
    name="decision-ledger",
    current_version="decision-ledger/v1",
    versions={"decision-ledger/v1": DecisionLedger},
)
