"""Structured research signal contracts."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.horizon import HORIZON_PATTERN
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions


class SignalFrame(BaseModel):
    """An immutable, evidence-linked research conclusion or abstention."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["signal-frame/v1"] = "signal-frame/v1"
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    direction: Literal["bullish", "bearish", "neutral", "abstain"]
    strength: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    horizon: str = Field(pattern=HORIZON_PATTERN)
    """How far ahead this conclusion reaches, as `<count><unit>` -- see `domain/horizon.py`.

    Constrained rather than normalised, and it stays a `str` on purpose. `signal_id` hashes the
    canonical JSON of these fields, so replacing the type or rewriting an accepted value would
    move the identity of every stored signal; restricting the *domain* moves none, because
    every value that was already well formed serialises to the bytes it always did. The
    previous `min_length=1, max_length=64` admitted `'whenever'`, which nothing downstream
    could turn into a return window -- which is the gap `V2-P1-017` closes.
    """
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


SIGNAL_FRAME_VERSIONS: ContractVersions[SignalFrame] = ContractVersions(
    name="signal-frame",
    current_version="signal-frame/v1",
    versions={"signal-frame/v1": SignalFrame},
)
