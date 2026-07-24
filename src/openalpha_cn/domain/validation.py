"""Outcome validation and attribution contracts."""

import math
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.time import ensure_aware


class AttributionTerm(BaseModel):
    """One additive contribution to a validated net active return."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    category: Literal["rule", "factor", "agent"]
    name: str = Field(min_length=1, max_length=128)
    contribution: float


class ValidationResult(BaseModel):
    """An immutable outcome and attribution record."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["validation-result/v1"] = "validation-result/v1"
    signal_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    observation_start: datetime
    observation_end: datetime
    realized_return: float
    benchmark_return: float
    transaction_cost: float = Field(ge=0)
    attribution: tuple[AttributionTerm, ...]
    confidence: float = Field(ge=0, le=1)
    data_quality_notes: tuple[str, ...] = ()

    @field_validator("observation_start", "observation_end")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_window_and_attribution(self) -> Self:
        if self.observation_end <= self.observation_start:
            raise ValueError("observation_end must be after observation_start")
        contribution = sum(item.contribution for item in self.attribution)
        if not math.isclose(contribution, self.net_active_return, abs_tol=1e-9):
            raise ValueError("attribution does not reconcile with net_active_return")
        return self

    @computed_field(return_type=float)  # type: ignore[prop-decorator]
    @property
    def net_active_return(self) -> float:
        """Return realized return less benchmark return and transaction cost."""
        return self.realized_return - self.benchmark_return - self.transaction_cost

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def validation_id(self) -> str:
        """Return the stable content-derived validation identifier."""
        return stable_model_id(prefix="val", model=self)
