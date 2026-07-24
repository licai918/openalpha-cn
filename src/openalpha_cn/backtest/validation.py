"""Outcome validation and reconciled rule/factor/agent attribution."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.validation import AttributionTerm, ValidationResult
from openalpha_cn.runtime.engine import ResearchRunResult


class OutcomeObservation(BaseModel):
    """Frozen future outcome inputs used after a research decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_start: datetime
    observation_end: datetime
    start_price: float = Field(gt=0)
    end_price: float = Field(gt=0)
    benchmark_return: float
    transaction_cost: float = Field(ge=0)
    data_quality_notes: tuple[str, ...] = ()

    @field_validator("observation_start", "observation_end")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.observation_end <= self.observation_start:
            raise ValueError("observation_end must follow observation_start")
        return self


class OutcomeValidator:
    """Evaluate a decision and reconcile its active return attribution."""

    def validate(
        self,
        *,
        research: ResearchRunResult,
        observation: OutcomeObservation,
    ) -> ValidationResult:
        """Return a validated result whose attribution sums to net active return."""
        realized_return = 0.0
        if research.decision.final_action == "watch":
            realized_return = observation.end_price / observation.start_price - 1
        net_active_return = (
            realized_return - observation.benchmark_return - observation.transaction_cost
        )
        attribution = self._attribute(
            research=research,
            net_active_return=net_active_return,
        )
        return ValidationResult(
            signal_id=research.signal.signal_id,
            decision_id=research.decision.decision_id,
            observation_start=observation.observation_start,
            observation_end=observation.observation_end,
            realized_return=realized_return,
            benchmark_return=observation.benchmark_return,
            transaction_cost=observation.transaction_cost,
            attribution=attribution,
            confidence=research.signal.confidence,
            data_quality_notes=observation.data_quality_notes,
        )

    @staticmethod
    def _attribute(
        *,
        research: ResearchRunResult,
        net_active_return: float,
    ) -> tuple[AttributionTerm, ...]:
        if not research.agent_results:
            rule = net_active_return * 0.5
            return (
                AttributionTerm(category="rule", name="decision-policy", contribution=rule),
                AttributionTerm(
                    category="factor",
                    name="benchmark-and-cost",
                    contribution=net_active_return - rule,
                ),
            )

        rule = net_active_return * 0.2
        factor = net_active_return * 0.3
        agent_total = net_active_return - rule - factor
        weights = [abs(item.signal.strength) for item in research.agent_results]
        weight_sum = sum(weights)
        if weight_sum == 0:
            weights = [1.0 for _item in research.agent_results]
            weight_sum = float(len(weights))

        terms: list[AttributionTerm] = [
            AttributionTerm(category="rule", name="decision-policy", contribution=rule),
            AttributionTerm(
                category="factor",
                name="benchmark-and-cost",
                contribution=factor,
            ),
        ]
        allocated = 0.0
        for index, (result, weight) in enumerate(zip(research.agent_results, weights, strict=True)):
            is_last = index == len(research.agent_results) - 1
            contribution = agent_total - allocated if is_last else agent_total * weight / weight_sum
            allocated += contribution
            terms.append(
                AttributionTerm(
                    category="agent",
                    name=result.agent_id,
                    contribution=contribution,
                )
            )
        return tuple(terms)
