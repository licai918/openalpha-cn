"""Structured research signal contracts."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.time import ensure_aware


class SignalFrame(BaseModel):
    """An immutable, evidence-linked research conclusion or abstention."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["signal-frame/v1"] = "signal-frame/v1"
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    direction: Literal["bullish", "bearish", "neutral", "abstain"]
    strength: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    horizon: str = Field(min_length=1, max_length=64)
    evidence_ids: tuple[str, ...] = ()
    confirmation_conditions: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    abstention_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_conclusion(self) -> Self:
        if self.direction == "abstain":
            if self.abstention_reason is None:
                raise ValueError("abstention_reason is required when direction is abstain")
            if self.strength != 0:
                raise ValueError("strength must be zero when direction is abstain")
        else:
            if not self.evidence_ids:
                raise ValueError("directional signal requires evidence")
            if self.abstention_reason is not None:
                raise ValueError("abstention_reason is only valid when direction is abstain")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def signal_id(self) -> str:
        """Return the stable content-derived signal identifier."""
        return stable_model_id(prefix="sig", model=self)
