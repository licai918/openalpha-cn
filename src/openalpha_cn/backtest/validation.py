"""Outcome validation and reconciled rule/factor/agent attribution."""

from datetime import datetime
from typing import Final, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.labels import LabelError, OutcomeLabel
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.validation import AttributionTerm, ValidationResult
from openalpha_cn.runtime.contracts import ResearchRunResult

LABEL_PROVENANCE_NOTE: Final[str] = "prices are 后复权 closes from V2-P1-017's label contract"
"""The prefix of the note `observation_from_label` puts on the observations it builds.

`data_quality_notes` is carried through onto the stored `ValidationResult`, so this is what a
persisted result has to say for its two prices. A note is not a type, and it is not offered as
one -- what makes the prices trustworthy is that they came from `WindowReturn`, whose two paths
were cross-checked against each other; the note is how that survives serialization.
"""


class ValidationStore(Protocol):
    """Extension contract for durable outcome-validation storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives on the
    consumer side (`backtest/`), not in `storage/`. `OutcomeValidator` (this module) is a
    pure computation -- it never persists anything itself -- so unlike `RunRepository`/
    `RecoveryStore` (consumed only by `ResearchEngine`) there is no intermediate "runner"
    here: `sdk.py` and `api/app.py` call this Protocol's methods directly, the same
    relationship `WatchlistStore`/`ReportStore` (`product/research.py`) have to their
    consumers. Its method set is exactly what those two callers need: append the result
    `OutcomeValidator.validate()` just computed, then list it back by `decision_id` or by
    `signal_id` -- "how did this past decision turn out" is the query this whole feature
    exists to answer (V2-P0B-010). There is no `get(validation_id)` here because neither
    caller ever looks one up by that ID alone.
    """

    def append(self, result: ValidationResult) -> None:
        """Append idempotently by validation ID; reject a conflicting reuse of the ID."""

    def list_by_decision(self, decision_id: str) -> tuple[ValidationResult, ...]:
        """List validation results for one decision, in append order."""

    def list_by_signal(self, signal_id: str) -> tuple[ValidationResult, ...]:
        """List validation results for one signal, in append order."""


class OutcomeObservation(BaseModel):
    """Frozen future outcome inputs used after a research decision.

    `start_price` and `end_price` are two free positive floats and this contract cannot say
    where either came from. That matters because `OutcomeValidator.validate` computes
    `end_price / start_price - 1`, which is **correct** when both are on one adjustment scale
    and is the measured wrong path when they are two raw closes across a corporate action:
    `domain/daily_prices.py` puts `000001.SZ`'s 2026-06-12 session at `-0.530973%` that way
    against a true `+2.742230%`, with the sign reversed and no error anywhere.

    `observation_from_label` is the constructor that *can* answer for its prices -- it takes
    them from a cross-checked `WindowReturn` -- and is what `V2-P1-017` added. This constructor
    stays open because the replay corpus and every existing caller build one directly from
    figures that never leave a synthetic fixture; the window is still there for a caller who
    supplies raw closes, and `KNOWN_LABEL_LIMITATIONS` is not the place for it because it is a
    property of this contract rather than of a label.
    """

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


def observation_from_label(
    label: OutcomeLabel,
    *,
    benchmark_return: float,
    transaction_cost: float,
    data_quality_notes: tuple[str, ...] = (),
) -> OutcomeObservation:
    """Build an observation whose two prices provably come from one return path.

    The prices are `WindowReturn`'s 后复权 closes, `close * adj_factor` at each end of the
    window, so `end_price / start_price - 1` -- the expression `OutcomeValidator.validate`
    actually runs -- **is** `WindowReturn.adjusted` rather than resembling it. Both were
    computed by `AdjustmentHistory` from the same two factors, so the identity is exact to the
    last bit and `tests/unit/backtest/test_observation_from_label.py` asserts it with `==`.

    The instants are the two sessions' 15:00 closes in the window's own timezone, so a stored
    `ValidationResult` reads as close-to-close over named sessions rather than as two
    timestamps whose relationship a reader has to reconstruct.

    Raises `LabelError` for a refused label instead of substituting `0.0`: a zero is a return,
    and a supervised target that quietly reads zero for every halted or limit-locked name
    teaches the model that those sessions were flat.
    """
    computed = label.window_return
    if computed is None:
        raise LabelError(f"{label.refusal_summary}, and therefore no observation")
    provenance = (
        f"{LABEL_PROVENANCE_NOTE}: {label.ts_code} over "
        f"{computed.entry_day.isoformat()}..{computed.exit_day.isoformat()} "
        f"({label.window.session_count} sessions, horizon {label.window.horizon.text}); the "
        f"chained close/pre_close path gives {computed.published!r} against this path's "
        f"{computed.adjusted!r}, a relative gap of {computed.disagreement!r} where "
        f"{computed.tolerance!r} is allowed"
    )
    return OutcomeObservation(
        observation_start=label.window.close_instant(computed.entry_day),
        observation_end=label.window.close_instant(computed.exit_day),
        start_price=computed.entry_adjusted_close,
        end_price=computed.exit_adjusted_close,
        benchmark_return=benchmark_return,
        transaction_cost=transaction_cost,
        data_quality_notes=(provenance, *data_quality_notes),
    )


class OutcomeValidator:
    """Evaluate a decision and reconcile its active return attribution."""

    def validate(
        self,
        *,
        research: ResearchRunResult,
        observation: OutcomeObservation,
    ) -> ValidationResult:
        """Return a validated result whose attribution sums to net active return.

        `realized_return` is the **position's** return and not the security's, which is why it
        stays `0.0` unless `final_action` is `"watch"`: `"avoid"` and `"abstain"` take no
        position, so nothing was realized, and `net_active_return` then reports what standing
        flat cost against the benchmark. `V2-P1-017`'s `OutcomeLabel.realized_return` is the
        other quantity -- what the security did over the window, whatever the decision was --
        and the two are deliberately not merged: a factor is fitted against the security's
        forward return, and a decision is scored against the position it actually took.
        """
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
