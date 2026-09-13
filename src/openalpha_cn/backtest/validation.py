"""Outcome validation, and the part of an active return this build declines to attribute."""

from dataclasses import dataclass
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


TRANSACTION_COST_TERM: Final[str] = "transaction-cost"
"""The one term `OutcomeValidator` emits for every decision, held or flat.

Emitted even at `transaction_cost == 0.0`, because a term that disappears at zero cannot tell
a reader "this run cost nothing to trade" apart from "this build does not model cost".
"""

FORGONE_BENCHMARK_TERM: Final[str] = "no-position-versus-benchmark"
"""The term a decision that took no position earns, and the only payoff term this build claims.

Its contribution is `realized_return - benchmark_return`, which for `"avoid"` and `"abstain"`
is `-benchmark_return` exactly. A `"watch"` earns the same quantity and does *not* get this
term: there it is the security's move that decides it, and no claimant in the run can be shown
to have earned any share of that.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class AttributionLimitation:
    """One named thing `OutcomeValidator`'s attribution does not claim to have measured."""

    code: str
    detail: str


KNOWN_ATTRIBUTION_LIMITATIONS: Final[tuple[AttributionLimitation, ...]] = (
    AttributionLimitation(
        code="a_held_position_leaves_its_whole_selection_return_unexplained",
        detail=(
            "When final_action is 'watch', realized_return - benchmark_return is the entire "
            "payoff of the decision and none of it is attributed. A ResearchRunResult carries "
            "a direction, a strength in [-1, 1], a confidence and a set of evidence IDs, and "
            "not one of those is a return, so there is nothing to divide the payoff by. On the "
            "closed-form control in tests/unit/backtest/test_validation.py that residual is "
            "0.1875 against a 0.1796875 net active return -- more than the whole of it, "
            "because the cost term is negative -- and it is reported as unexplained_return. "
            "The predecessor V2-P5-005 deleted split the same number 20/30/50 across a rule, a "
            "factor and the agents and let the last agent absorb the remainder, which is why "
            "ValidationResult.validate_window_and_attribution had never once failed."
        ),
    ),
    AttributionLimitation(
        code="an_agent_contribution_would_need_a_counterfactual_a_finished_run_cannot_supply",
        detail=(
            "AgentResult.signal.strength is a conviction and AgentDecision.recommendation is "
            "support/oppose/abstain; neither is money. Converting one into a contribution "
            "means asking what the committee would have decided without that agent, which "
            "requires re-running the committee -- and OutcomeValidator.validate is handed only "
            "the finished ResearchRunResult, never the engine that produced it. So the 'agent' "
            "category is never emitted here although AttributionTerm admits it, and adding a "
            "second agent to a run changes no term and no residual, which "
            "test_dropping_every_agent_result_moves_no_term_and_no_residual drives."
        ),
    ),
    AttributionLimitation(
        code="neither_a_factor_nor_a_model_term_is_ever_produced_here",
        detail=(
            "A factor term needs an exposure and that factor's own return over the same "
            "window; a model term needs the fitted predictor's forecast for this subject. A "
            "ResearchRunResult carries DecisionLedger.model_versions -- version strings -- and "
            "no exposure, no factor return and no prediction, so both categories stay empty "
            "even though V2-P4-001 added 'model' precisely so a model-driven ranking would not "
            "have to book its result as an agent's. The deleted implementation emitted a "
            "factor term named 'benchmark-and-cost' worth 30% of the net active return, naming "
            "two quantities that net_active_return had already subtracted out."
        ),
    ),
    AttributionLimitation(
        code="a_cost_is_booked_against_a_position_that_was_never_taken",
        detail=(
            "'avoid' and 'abstain' realize 0.0, yet OutcomeObservation.transaction_cost is an "
            "independent field a caller may set positive beside either, and the flat arm then "
            "reports a rule term charging a cost for a trade that did not happen. It is booked "
            "rather than refused because the observation is built without reference to the "
            "decision -- observation_from_label takes benchmark_return and transaction_cost "
            "from its caller -- so this module cannot tell a modelled cost from a mis-supplied "
            "one, and dropping it silently would break the reconciliation it is part of."
        ),
    ),
)
"""What the attribution below does not claim, stated where the attribution is computed.

Every entry is a category this build can name but cannot measure from what a finished run
holds. None of them is closed by a better fixture: three are missing *inputs* (a factor return,
a model forecast, a counterfactual re-run) and the fourth is an input this contract has no
standing to second-guess.
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
    """Evaluate a decision, attribute what the run measured, and name what it did not.

    The attribution here is deliberately thin, and `V2-P5-005` is the issue that made it so.
    What stood before claimed the entire net active return across a rule, a factor and every
    agent in fixed 20/30/50 proportions that nothing had measured, which is this repository's
    signature defect -- a number implying more than it knows -- in its purest form. Two terms
    survive, both exact; everything else is `unexplained_return`. See
    `KNOWN_ATTRIBUTION_LIMITATIONS` for the four things that are therefore never claimed.
    """

    def validate(
        self,
        *,
        research: ResearchRunResult,
        observation: OutcomeObservation,
    ) -> ValidationResult:
        """Return a validated result whose terms **and residual** reconcile to net active return.

        The residual is the part `_attribute` refused to claim, and it is why the sum in
        `ValidationResult.validate_window_and_attribution` is now a check rather than an
        identity: nothing here adjusts a term to make the equation close.

        `realized_return` is the **position's** return and not the security's, which is why it
        stays `0.0` unless `final_action` is `"watch"`: `"avoid"` and `"abstain"` take no
        position, so nothing was realized, and `net_active_return` then reports what standing
        flat cost against the benchmark. `V2-P1-017`'s `OutcomeLabel.realized_return` is the
        other quantity -- what the security did over the window, whatever the decision was --
        and the two are deliberately not merged: a factor is fitted against the security's
        forward return, and a decision is scored against the position it actually took.

        One consequence reads as a contradiction and is not one. When the observation came from
        `observation_from_label`, `LABEL_PROVENANCE_NOTE`'s text quotes the security's move, and
        that note travels onto the stored `ValidationResult` whatever the action was -- so an
        `"avoid"` or `"abstain"` result carries `realized_return=0.0` beside a note saying the
        name did +2.74%. Both are right, about different questions. Stripping the note for those
        two actions would remove the only record of which of the three return paths the two
        prices came from, which is the one thing the note exists for.
        """
        took_position = research.decision.final_action == "watch"
        realized_return = 0.0
        if took_position:
            realized_return = observation.end_price / observation.start_price - 1
        attribution, unexplained_return = self._attribute(
            observation=observation,
            realized_return=realized_return,
            took_position=took_position,
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
            unexplained_return=unexplained_return,
            confidence=research.signal.confidence,
            data_quality_notes=observation.data_quality_notes,
        )

    @staticmethod
    def _attribute(
        *,
        observation: OutcomeObservation,
        realized_return: float,
        took_position: bool,
    ) -> tuple[tuple[AttributionTerm, ...], float]:
        """Split the net active return into what this run measured and what it did not.

        One quantity decides the shape: `payoff`, the decision's return against the benchmark.
        It is the *same* number in both branches and it is either attributed in full or
        admitted in full -- it is never divided, because there is nothing here to divide it by.

        **Flat.** `"avoid"` and `"abstain"` took no position, so `realized_return` is
        identically `0.0` and `payoff` is `-benchmark_return` exactly, with exactly one
        claimant: the policy that chose to stand flat is the whole reason the benchmark's move
        was forgone. Nothing is left over and the residual is `0.0` -- measured, not defaulted.

        **Held.** `"watch"` took a position, so `payoff` turns on what the security did, and a
        finished `ResearchRunResult` holds nothing that says how much of that move any rule,
        factor, agent or model accounted for: a `strength` is a conviction, `model_versions`
        are version strings, and separating one agent's effect needs the committee re-run
        without it, which this method cannot do because it is handed the result and not the
        engine. So `payoff` becomes `unexplained_return` in full. See
        `KNOWN_ATTRIBUTION_LIMITATIONS`, whose four entries are exactly the categories that
        stay empty and why.

        `transaction_cost` is attributed in both branches: it is a measured number, exact to
        the last bit, and the policy that decided to trade is what incurred it.

        What this replaces (`V2-P5-005`) claimed all of it. It booked 20% of the net to a
        `rule` named `decision-policy`, 30% to a `factor` named `benchmark-and-cost` -- two
        quantities `net_active_return` has already subtracted -- and apportioned the remaining
        50% across the agents by `abs(strength)`, with the last agent taking
        `agent_total - allocated` so that the sum closed. That last step is why
        `ValidationResult.validate_window_and_attribution` had never failed on a computed
        result: a reconciliation with a free variable in it cannot fail, and so had never
        measured anything. Nothing below has a free variable; the residual is a *stated*
        number the check tests rather than a plug that absorbs whatever is left.

        The arithmetic is exact rather than merely close. `a - b - c` is `(a - b) - c`, and
        subtracting is adding the negation, so `(realized - benchmark) + (-cost)` is
        bit-identical to `net_active_return` -- which is why
        `tests/unit/backtest/test_validation.py` asserts both arms with `==`.
        """
        payoff = realized_return - observation.benchmark_return
        cost = AttributionTerm(
            category="rule",
            name=TRANSACTION_COST_TERM,
            contribution=-observation.transaction_cost,
        )
        if took_position:
            return (cost,), payoff
        return (
            AttributionTerm(
                category="rule",
                name=FORGONE_BENCHMARK_TERM,
                contribution=payoff,
            ),
            cost,
        ), 0.0
