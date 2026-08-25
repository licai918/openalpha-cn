"""What `OutcomeValidator` may claim about a net active return, and what it must refuse to.

`V2-P5-005` deleted the invented split this module used to assert: 20% of the net active
return booked to `rule`, 30% to `factor`, and the remaining 50% apportioned across the agents
by `abs(signal.strength)` with the *last* agent absorbing whatever was left over. Every one of
those five numbers was chosen rather than measured, and the last-term absorption is what made
`ValidationResult.validate_window_and_attribution` pass **by construction** -- a reconciliation
check with a free variable in it can never fail, and therefore never measured anything.

What replaces it is one principle: **a decision's payoff against the benchmark is
`realized_return - benchmark_return`, and it may be attributed only when the run holds what
determines it.**

- When no position was taken, `realized_return` is identically `0.0`, so that payoff *is*
  `-benchmark_return` -- exactly, arithmetically, with one claimant. The decision rule chose
  to stand flat and standing flat is what cost the benchmark's move. It is attributed.
- When a position was taken, the payoff turns on the security's own move, and nothing in a
  finished `ResearchRunResult` says how much of that move any rule, factor, agent or model
  accounted for. It becomes `unexplained_return` (`V2-P5-006`), named and carried rather than
  apportioned into whichever term happened to be last.

`transaction_cost` is attributed in both arms because it is measured, exact, and caused by the
policy that decided to trade.

## The control is closed-form and it has two arms, on purpose

`V2-P4-022`'s lesson, applied here. Every figure below is a dyadic rational, so each identity
holds to the last bit and is asserted with `==` rather than `pytest.approx`: `125/100 - 1` is
`0.25`, the benchmark is `2**-4` and the cost is `2**-7`, so the held arm's net active return is
`0.1796875` and the flat arm's is `-0.0703125` with no rounding anywhere in either.

One arm separates nothing. A "put everything in the residual" implementation passes the held
arm and fails the flat one; an implementation that keeps any invented split fails the held arm;
an implementation that never emits a cost term fails both. Only the two together pin the rule.
"""

import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from openalpha_cn.backtest.validation import (
    KNOWN_ATTRIBUTION_LIMITATIONS,
    OutcomeObservation,
    OutcomeValidator,
)
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

START_PRICE: Final[float] = 100.0
END_PRICE: Final[float] = 125.0
REALIZED: Final[float] = 0.25
"""`125/100 - 1`, exact in binary."""

BENCHMARK: Final[float] = 0.0625
"""`2**-4`."""

COST: Final[float] = 0.0078125
"""`2**-7`."""

HELD_NET_ACTIVE: Final[float] = 0.1796875
"""`0.25 - 0.0625 - 0.0078125`, exact."""

HELD_RESIDUAL: Final[float] = 0.1875
"""`0.25 - 0.0625`: the whole selection return of the held arm, which nothing can attribute."""

FLAT_NET_ACTIVE: Final[float] = -0.0703125
"""`0.0 - 0.0625 - 0.0078125`, exact."""


@pytest.fixture
def research_result(frozen_now: datetime):
    def _make(tmp_path: Path):
        evidence = EvidenceSnapshot(
            subject="000001.SZ",
            kind="limit_up",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="synthetic",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Synthetic limit-up.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "market_event",
                "facts": {"close": 10.0, "pct_change": 10.0, "board_count": 1},
                "quality_flags": [],
            },
        )
        return ResearchEngine(
            repository=SQLiteRunRepository(tmp_path / "state.sqlite3"),
            memory=InMemoryResearchMemory(),
            clock=lambda: frozen_now,
            recovery_store=SQLiteRecoveryStore(tmp_path / "state.sqlite3"),
        ).run_cycle(
            ResearchRunRequest(
                run_id="run_validation",
                mode="backtest",
                subject="000001.SZ",
                as_of=frozen_now,
                evidence=(evidence,),
                code_commit="0123456789abcdef",
                config_digest="c" * 64,
                random_seed=7,
            )
        )

    return _make


def _observation(
    frozen_now: datetime, *, cost: float = COST, benchmark: float = BENCHMARK
) -> OutcomeObservation:
    return OutcomeObservation(
        observation_start=frozen_now,
        observation_end=frozen_now + timedelta(days=5),
        start_price=START_PRICE,
        end_price=END_PRICE,
        benchmark_return=benchmark,
        transaction_cost=cost,
        data_quality_notes=("Synthetic outcome.",),
    )


def _flat(research: ResearchRunResult, action: str) -> ResearchRunResult:
    """The same run with its final action changed; `decision_id` re-derives from content."""
    return research.model_copy(
        update={"decision": research.decision.model_copy(update={"final_action": action})}
    )


def test_a_held_position_leaves_its_whole_selection_return_unexplained(
    tmp_path: Path,
    research_result,
    frozen_now: datetime,
) -> None:
    """The held arm of the closed-form control.

    `0.1875` is `realized - benchmark` to the last bit, and it is the number the deleted
    implementation split 20/30/50 across a rule, a factor and two agents on no evidence at all.
    Nothing in a finished `ResearchRunResult` says how much of a security's move a rule, a
    factor, an agent or a model accounted for, so the honest answer is a residual with a name.

    The one thing that *is* attributed is the cost, because it was measured rather than
    guessed: exactly `-2**-7`, caused by the policy that decided to trade.
    """
    research = research_result(tmp_path)
    assert research.decision.final_action == "watch"
    assert research.agent_results

    result = OutcomeValidator().validate(research=research, observation=_observation(frozen_now))

    assert result.realized_return == REALIZED
    assert result.net_active_return == HELD_NET_ACTIVE
    assert result.unexplained_return == HELD_RESIDUAL
    assert [(item.category, item.name, item.contribution) for item in result.attribution] == [
        ("rule", "transaction-cost", -COST)
    ]
    # Exact, not approximate: every figure in this control is a dyadic rational.
    assert sum(item.contribution for item in result.attribution) + result.unexplained_return == (
        result.net_active_return
    )


@pytest.mark.parametrize("action", ["avoid", "abstain"])
def test_a_decision_that_took_no_position_is_fully_explained_by_the_benchmark_it_forwent(
    tmp_path: Path,
    research_result,
    frozen_now: datetime,
    action: str,
) -> None:
    """The flat arm, and the reason the held arm's residual is not simply "everything".

    `realized_return` is identically `0.0` here, so the decision's payoff against the
    benchmark is `-benchmark_return` exactly, with exactly one claimant -- the rule that chose
    to stand flat. There is nothing left over, and `0.0` is the *measured* residual rather
    than a default nobody looked at.

    An implementation that routed the whole active return to `unexplained_return` would pass
    the held arm and fail here, which is why one arm could not have pinned this.
    """
    research = _flat(research_result(tmp_path), action)

    result = OutcomeValidator().validate(research=research, observation=_observation(frozen_now))

    assert result.realized_return == 0.0
    assert result.net_active_return == FLAT_NET_ACTIVE
    assert result.unexplained_return == 0.0
    assert [(item.category, item.name, item.contribution) for item in result.attribution] == [
        ("rule", "no-position-versus-benchmark", -BENCHMARK),
        ("rule", "transaction-cost", -COST),
    ]
    assert sum(item.contribution for item in result.attribution) + result.unexplained_return == (
        result.net_active_return
    )


def test_no_contribution_is_a_fraction_of_the_net_active_return(
    tmp_path: Path,
    research_result,
    frozen_now: datetime,
) -> None:
    """The deleted split, named by its own five constants so its return would be visible.

    `0.2`, `0.3` and the residual `0.5` were the shares; `abs(signal.strength)` was the weight
    the last of them was divided by. On this control the first two are worth
    `0.035937500000000004` and `0.053906249999999996` -- neither is any term's contribution,
    in either arm, and no term is proportional to the net at all.
    """
    research = research_result(tmp_path)
    held = OutcomeValidator().validate(research=research, observation=_observation(frozen_now))
    flat = OutcomeValidator().validate(
        research=_flat(research, "avoid"), observation=_observation(frozen_now)
    )

    for result in (held, flat):
        invented = {result.net_active_return * share for share in (0.2, 0.3, 0.5, 0.8, 0.7)}
        assert not invented & {item.contribution for item in result.attribution}
    # No term is named after an agent, so no weight can be applied to one.
    assert {item.category for item in held.attribution} == {"rule"}
    assert {item.category for item in flat.attribution} == {"rule"}


def test_dropping_every_agent_result_moves_no_term_and_no_residual(
    tmp_path: Path,
    research_result,
    frozen_now: datetime,
) -> None:
    """The residual is a leftover, never a pot apportioned among whoever is present.

    The deleted implementation branched on `research.agent_results`: with agents it split
    20/30/50, without them it split 50/50, so emptying the tuple rewrote every number. Nothing
    here claims an agent contribution, so emptying it must change nothing at all -- and that
    is the assertion, over both arms, on the terms *and* on the residual.
    """
    research = research_result(tmp_path)
    validator = OutcomeValidator()

    for arm in (research, _flat(research, "avoid")):
        with_agents = validator.validate(research=arm, observation=_observation(frozen_now))
        without = validator.validate(
            research=arm.model_copy(update={"agent_results": ()}),
            observation=_observation(frozen_now),
        )

        assert without.attribution == with_agents.attribution
        assert without.unexplained_return == with_agents.unexplained_return


def test_the_cost_term_is_emitted_even_when_the_cost_is_zero(
    tmp_path: Path,
    research_result,
    frozen_now: datetime,
) -> None:
    """A term that vanishes at zero cannot say "no cost" apart from "cost not modelled"."""
    result = OutcomeValidator().validate(
        research=research_result(tmp_path),
        observation=_observation(frozen_now, cost=0.0),
    )

    assert [item.name for item in result.attribution] == ["transaction-cost"]
    assert result.attribution[0].contribution == 0.0
    assert result.net_active_return == REALIZED - BENCHMARK
    assert result.unexplained_return == HELD_RESIDUAL


def test_the_flat_term_is_the_payoff_and_not_a_restatement_of_the_benchmark(
    tmp_path: Path,
    research_result,
    frozen_now: datetime,
) -> None:
    """`realized_return - benchmark_return` and `-benchmark_return` are not the same expression.

    They agree on every value the flat branch can produce -- `realized_return` is `0.0` there
    by construction -- except one, and the exception is not cosmetic. At
    `benchmark_return == 0.0` the honest expression is `0.0 - 0.0`, which is **positive** zero,
    while `-benchmark_return` is `-0.0`; canonical JSON writes the sign, `validation_id` hashes
    that JSON, and so the two spellings give one result two different content addresses. This
    was a live mutation-sweep survivor until it was measured: `val_dba127649bf529e77e53d6aa`
    against `val_470895b1ba7335601a265760` on a fixture of exactly this shape.

    The second half of the test is what makes the first half matter, and it is driven rather
    than asserted in prose: the same result with `-0.0` substituted really does address
    differently. (The cost term beside it *is* `-0.0` at zero cost, because it is
    `-transaction_cost` and there is no payoff to subtract it from; that is stable and
    deterministic, so it moves no address.)
    """
    research = _flat(research_result(tmp_path), "abstain")

    result = OutcomeValidator().validate(
        research=research,
        observation=_observation(frozen_now, cost=0.0, benchmark=0.0),
    )

    forgone = result.attribution[0]
    assert forgone.name == "no-position-versus-benchmark"
    assert forgone.contribution == 0.0
    assert math.copysign(1.0, forgone.contribution) == 1.0
    assert result.net_active_return == 0.0
    assert result.unexplained_return == 0.0

    negated = result.model_copy(
        update={
            "attribution": (
                forgone.model_copy(update={"contribution": -0.0}),
                *result.attribution[1:],
            )
        }
    )
    assert negated.validation_id != result.validation_id


def test_every_attribution_limitation_code_is_declared_here() -> None:
    """The binding `tests/unit/test_known_limitation_registries.py` puts on every registry."""
    assert {item.code for item in KNOWN_ATTRIBUTION_LIMITATIONS} == {
        "a_held_position_leaves_its_whole_selection_return_unexplained",
        "an_agent_contribution_would_need_a_counterfactual_a_finished_run_cannot_supply",
        "neither_a_factor_nor_a_model_term_is_ever_produced_here",
        "a_cost_is_booked_against_a_position_that_was_never_taken",
    }
