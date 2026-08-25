"""Heuristic long-only target weights over a ranked candidate list (`V2-P5-001`).

The first module of P5. It turns one `as_of`'s ordered candidate list into a set of target
weights by three declared, arithmetic steps -- a tiered cut on rank, a bounded cap-trimming
iteration, and a turnover budget that moves the book part of the way -- and it says so on the
answer: `PortfolioConstruction.method` is `Literal["heuristic, not optimized"]` and every face
renders it. That label is the row, not decoration. Nothing here maximises anything, nothing here
is fitted to anything, and there is no objective function to be near-optimal *for*; a reader who
takes these weights for an optimiser's output is reading a claim this module never made.

## Why there is no optimiser, and why that is a decision rather than a shortfall

ADR-0003 fixes the runtime dependency set at nine and ships no numerical stack -- no numpy, no
pandas, no scipy, and the `backtest-no-numeric-stack-or-panel-plane` import contract makes that
structural for this package. A mean-variance or risk-parity construction needs a covariance
estimate and a solver, so it is not a thing this repository can ship without changing that
decision. The PRD makes the same call in the other direction -- its disposition table decides
against `cvxpy` for the optimiser rows S53/S54 -- and attaches one condition to it: the report
must declare itself a heuristic. So the
label and the absence of an optimiser are one decision recorded twice.

## What the three steps are, exactly

**Tiered ranking.** The candidates are sorted by `rank` (1-based, dense, no gaps -- enforced) and
cut into `len(policy.tier_weights)` contiguous blocks. Tier `i` receives `tier_weights[i]` of the
invested book and splits it **equally** among its members. Sizes are as equal as the count allows
and the remainder goes to the earlier (higher-ranked) tiers, so the cut is a function of the count
alone and two runs over one list cut it the same way.

**Cap trimming.** Every name is clamped to `limits.max_position_weight`; every industry over
`limits.max_industry_weight` is scaled down proportionally; the freed weight is offered back to
the names with headroom, pro rata to that headroom, and the pass ends with another clamp so the
returned weights satisfy every cap unconditionally. The loop is bounded
(`_MAXIMUM_REDISTRIBUTION_PASSES`) and whatever it cannot place **becomes cash** and is reported
as `unallocated_weight`. Nothing is pushed onto a last name to make a column add up: that trick is
what `V2-P5-005` exists to delete out of `backtest/validation.py`, and it is not reintroduced here
one phase earlier.

**Turnover budget.** `turnover` is the total absolute change in security weights, **both sides
counted** -- selling one 5% name and buying another is `0.10`, not `0.05`. Stated here once
because a budget under one convention and a measurement under the other differ by a factor of two
and both readings are common. If the move exceeds `limits.turnover_budget` the whole move is
scaled by `budget / turnover`, which is the standard partial-rebalance heuristic: the book lands
between where it is and where the policy wants it.

## The three things a reader has to be told and are therefore on the answer

`unallocated_weight` -- weight the caps refused and cash absorbed. `turnover_before_budget`
beside `turnover` -- the move that was asked for beside the move that was made. And
`caps_breached_after_turnover_damping`, which is the honest cost of the third step: damping is a
convex move from the *previous* book toward a compliant target, so it is compliant whenever the
previous book was, and can be over a cap when the previous book already was. Trimming again would
break the budget that was just enforced, so this module reports the breach by name and changes
nothing. Silence there would be a book over its own limits with a green report on top.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.candidate_ranking import CandidateRanking
from openalpha_cn.backtest.portfolio import PortfolioLimits

__all__ = [
    "CONSTRUCTION_LIMITATION_CODES",
    "CONSTRUCTION_METHOD",
    "KNOWN_CONSTRUCTION_LIMITATIONS",
    "LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY",
    "WEIGHT_QUANTUM",
    "ConstructionCandidate",
    "ConstructionLimitation",
    "PortfolioConstruction",
    "PortfolioConstructionError",
    "PortfolioConstructionPolicy",
    "TargetWeight",
    "candidates_from_ranking",
    "candidates_from_shortlist_answer",
    "construct_portfolio",
    "construction_view",
]

CONSTRUCTION_METHOD: Final[str] = "heuristic, not optimized"
"""The label `V2-P5-001` requires the report to carry, spelled once.

`PortfolioConstruction.method` is `Literal["heuristic, not optimized"]` and defaults to this
value, so the only legal answer is this sentence and a build that stopped saying it would not
validate. `construction_view` puts it on every rendered body and the CLI prints it on the
terminal rendering, because a caveat that lives only in a docstring is a caveat the reader of the
numbers never sees.
"""

WEIGHT_QUANTUM: Final[Decimal] = Decimal("0.000001")
"""The grid every returned weight sits on: one part per million of the book.

Quantisation is toward zero, never toward a neighbour, so a rounded set of weights can only be
*under* what the caps allow. The dust that rounding leaves is reported as `unallocated_weight`
rather than being added to whichever name is last, for this module's docstring's reason.
"""

_MAXIMUM_REDISTRIBUTION_PASSES: Final[int] = 8
"""How many times the trimming loop offers freed weight back before giving the rest to cash.

A bound rather than a convergence test, so the function terminates on every input including one
whose caps admit no fixed point. Eight is enough for every shape this build can produce -- a pass
either places weight or ends the loop -- and the untouched remainder is reported, so the bound
cannot hide anything: an input that needed a ninth pass shows up as `unallocated_weight`, not as
a wrong number.
"""

_ZERO: Final[Decimal] = Decimal(0)
_ONE: Final[Decimal] = Decimal(1)


class PortfolioConstructionError(ValueError):
    """Raised for a construction that cannot be *put*, never for a fact about the market.

    A candidate list whose caps leave weight unplaced is not this: it is an answer, and it
    carries `unallocated_weight`. A ranked list with two names at rank 3, a tier weight vector
    that does not sum to one, a declared industry cap over candidates that carry no industry --
    those are requests with no answer, and they are refused here by name.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstructionLimitation:
    """One named boundary on what a set of target weights can be trusted to mean."""

    code: str
    detail: str


KNOWN_CONSTRUCTION_LIMITATIONS: Final[tuple[ConstructionLimitation, ...]] = (
    ConstructionLimitation(
        code="the_policy_is_a_heuristic_and_optimises_nothing",
        detail=(
            "There is no objective function, no covariance estimate and no solver. The weights "
            "are a declared arithmetic function of rank and of the caps -- a tiered cut, a "
            "bounded trim, a proportional damping -- and 'better' is undefined for them because "
            "nothing was being maximised. ADR-0003 is why: this repository ships nine runtime "
            "dependencies and no numerical stack, so an optimiser is not a thing it can ship. "
            "PortfolioConstruction.method carries `heuristic, not optimized` on every answer and "
            "every face renders it."
        ),
    ),
    ConstructionLimitation(
        code="the_tiers_cut_on_rank_and_the_scores_decide_nothing_inside_one",
        detail=(
            "Weight is equal within a tier, so the name ranked 1 and the name ranked 10 hold the "
            "same weight whenever one tier contains both, and a score that is twice another's "
            "buys nothing. The scores are carried onto TargetWeight for the reader and are never "
            "read as a magnitude -- deliberately, because "
            "KNOWN_CROSS_SECTION_LIMITATIONS.the_shortlist_is_not_a_ranking_of_expected_return "
            "says those numbers are fitted to nothing, and multiplying capital by an unfitted "
            "number is how a composite weight comes to look like a forecast. Every caveat on "
            "the incoming order therefore applies here unchanged and none is repaired."
        ),
    ),
    ConstructionLimitation(
        code="an_industry_cap_is_unenforceable_on_the_shipped_shortlist_face",
        detail=(
            "Measured, not inferred: `shortlist_view` calls `rank_candidates(exposures=None)`, "
            "so `RankedCandidate.exposure` is None on every candidate the shipped path produces, "
            "and the stored shortlist answer renders no industry at all. A declared "
            "`max_industry_weight` over candidates that carry no `industry_code` is therefore "
            "refused by name here rather than silently satisfied -- a cap that cannot see an "
            "industry is not a cap that every industry passes. "
            "KNOWN_SHORTLIST_VIEW_LIMITATIONS.a_neutralized_tier_screen_needs_exposures_this_face"
            "_does_not_load is the other half of the same fact."
        ),
    ),
    ConstructionLimitation(
        code="the_turnover_budget_can_leave_a_cap_breached_and_says_so_instead_of_retrimming",
        detail=(
            "Damping is a convex move from the previous book toward a target that satisfies "
            "every cap, so the result satisfies them whenever the previous book did. When the "
            "previous book was already over a cap, the damped book can be too. Trimming again "
            "would break the budget that was just enforced, so `caps_breached_after_turnover_"
            "damping` names the position, industry or exposure that is over and the weights are "
            "left where the budget put them."
        ),
    ),
    ConstructionLimitation(
        code="the_cash_floor_is_the_exposure_ceiling_restated_and_not_a_second_constraint",
        detail=(
            "Under long-only accounting with no leverage, `equity == cash + market_value`, so "
            "`cash / equity >= min_cash_weight` and `market_value / equity <= 1 - "
            "min_cash_weight` are the same inequality. `PortfolioLimits` carries both because "
            "the roadmap row asks for a cash floor and because declaring intent as a floor is "
            "legible, and the binding constraint is simply the tighter of the two. A caller who "
            "sets both is not adding a constraint; the rejection reason names which one bound."
        ),
    ),
    ConstructionLimitation(
        code="no_capacity_liquidity_or_cost_term_enters_a_weight",
        detail=(
            "Nothing here reads a volume, a turnover value, a spread or a fee schedule, so a "
            "weight this module returns is unaware of whether the position is placeable at all. "
            "`AShareExecutionPolicy` prices a fill and `CandidateRanking` carries its verdict, "
            "and neither reaches these weights. Capacity and market impact are S56 and the "
            "buffered comparison is V2-P5-024; until those land, a large `max_position_weight` "
            "over a thin name is arithmetic, not a tradeable instruction."
        ),
    ),
    ConstructionLimitation(
        code="the_previous_book_is_declared_by_the_caller_and_never_read_from_a_ledger",
        detail=(
            "`construct_portfolio(previous=...)` takes weights the caller states. This module "
            "reaches no store -- `backtest-studies-touch-no-store` makes that structural -- so "
            "it cannot check the declaration against `SQLitePortfolioLedger`, which is where "
            "this installation's actual transitions live. A caller who declares a stale book "
            "gets a turnover number about a book that no longer exists, and every number derived "
            "from it (`turnover`, the damping factor, the breach list) is about that same "
            "fiction."
        ),
    ),
)

CONSTRUCTION_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_CONSTRUCTION_LIMITATIONS
)

LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY: Final[frozenset[str]] = frozenset(
    {
        "max_position_weight",
        "max_total_exposure",
        "min_cash_weight",
        "max_industry_weight",
        "turnover_budget",
    }
)
"""Which `PortfolioLimits` fields this module reads, as a set an audit can compare.

`backtest/portfolio.py::LIMITS_ENFORCED_BY_THE_SIMULATOR` is the other half, and
`tests/unit/backtest/test_portfolio_policy.py::
test_every_declared_limit_is_enforced_by_the_simulator_or_by_the_construction_policy` holds their
union equal to `PortfolioLimits.model_fields`. That equality is the guard `V2-P5-002` needed: a
limit added to the contract that no consumer reads is a fail-open dressed as a feature, and this
package has been burned by exactly that shape before (`V2-P4-030` found four risk flags that
every gate answered `pass` on). The sets overlap on purpose -- three fields are checked in both
places, once against a plan and once against a fill -- so this is a covering, not a partition.
"""


class ConstructionCandidate(BaseModel):
    """One ranked name as this module needs it: an order, a score to show, and maybe an industry.

    Deliberately not `RankedCandidate`. That contract carries both planes' whole answer about a
    security -- a `SignalFrame`, an `ExecutionResult`, a `run_manifest_id` -- and a construction
    policy reads four of its fields. Taking the narrow record means the stored shortlist answer,
    which carries no `SignalFrame` at all, can be constructed from by the same function that
    serves an in-process `CandidateRanking`; `candidates_from_ranking` and
    `candidates_from_shortlist_answer` are the two adapters, and they are here rather than at
    each face so the two cannot come to disagree about what a candidate is.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    rank: int = Field(ge=1)
    score: float
    industry_code: str | None = Field(default=None, min_length=1, max_length=64)


class PortfolioConstructionPolicy(BaseModel):
    """The whole declaration: how the tiers are cut and which limits bound the result.

    Carried onto the answer verbatim rather than summarised, `CandidateRankingManifest`'s
    reason: a report that says what it did but not what it was asked to do cannot be compared
    with the next one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["portfolio-construction-policy/v1"] = "portfolio-construction-policy/v1"
    tier_weights: tuple[Decimal, ...]
    limits: PortfolioLimits = PortfolioLimits()

    @model_validator(mode="after")
    def validate_tier_weights(self) -> Self:
        if not self.tier_weights:
            raise ValueError(
                "a construction policy declares at least one tier; a policy with none has no "
                "rule for where the capital goes"
            )
        if any(weight <= _ZERO for weight in self.tier_weights):
            raise ValueError(
                f"tier weights must each be positive; got {[str(w) for w in self.tier_weights]}. "
                "A zero tier is a tier whose members are ranked, reported and unfunded, which is "
                "a shorter candidate list said in a way that hides it"
            )
        total = sum(self.tier_weights, start=_ZERO)
        if total != _ONE:
            raise ValueError(
                f"tier weights must sum to exactly 1; {[str(w) for w in self.tier_weights]} sums "
                f"to {total}. They are shares of the invested book, and a vector that sums to "
                "less than one is a second, undeclared cash position"
            )
        return self

    @property
    def invested_weight(self) -> Decimal:
        """The share of equity the policy funds before any cap has trimmed anything.

        The tighter of the exposure ceiling and the complement of the cash floor. They are the
        same inequality (`the_cash_floor_is_the_exposure_ceiling_restated_and_not_a_second_
        constraint`), so `min` is not a composition of two constraints -- it is the one
        constraint read off whichever field states it more strictly.
        """
        return min(self.limits.max_total_exposure, _ONE - self.limits.min_cash_weight)


class TargetWeight(BaseModel):
    """One name's share of equity, with the tier it came from and what trimmed it."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    tier: int = Field(ge=1)
    rank: int = Field(ge=1)
    score: float
    industry_code: str | None = None
    weight: Decimal = Field(ge=0, le=1)
    untrimmed_weight: Decimal = Field(ge=0, le=1)

    @property
    def was_adjusted(self) -> bool:
        """Whether the cap machinery moved this name off the weight its tier alone would give it.

        `was_adjusted` and not `was_trimmed`, because the move goes both ways: a name under a cap
        that nothing binds *rises* when a capped name's freed weight is redistributed onto it. A
        boolean named for one direction would have been read as one, and half its true cases are
        the other.
        """
        return self.weight != self.untrimmed_weight


class PortfolioConstruction(BaseModel):
    """One `as_of`'s target weights, the declaration behind them, and every number withheld.

    `method` is the row: `Literal["heuristic, not optimized"]`, defaulted, so it cannot be
    omitted and cannot be softened.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["portfolio-construction/v1"] = "portfolio-construction/v1"
    method: Literal["heuristic, not optimized"] = "heuristic, not optimized"
    policy: PortfolioConstructionPolicy
    targets: tuple[TargetWeight, ...]
    cash_weight: Decimal = Field(ge=0, le=1)
    unallocated_weight: Decimal = Field(ge=0, le=1)
    turnover: Decimal = Field(ge=0)
    turnover_before_budget: Decimal = Field(ge=0)
    turnover_budget: Decimal | None = None
    turnover_damping: Decimal | None = None
    caps_breached_after_turnover_damping: tuple[str, ...] = ()

    @property
    def invested_weight(self) -> Decimal:
        """What the targets actually add up to, which is not what the policy asked for."""
        return sum((target.weight for target in self.targets), start=_ZERO)


def candidates_from_ranking(ranking: CandidateRanking) -> tuple[ConstructionCandidate, ...]:
    """The in-process adapter: a `CandidateRanking`'s candidates, narrowed, in rank order.

    `CandidateExposure.industry_code` is carried when the ranking was built with an exposure
    cross section and is `None` otherwise, which is the state every shipped face produces today
    (`an_industry_cap_is_unenforceable_on_the_shipped_shortlist_face`). It is passed through
    rather than defaulted, so the day `V2-P5-015` loads exposures the cap starts working with no
    change here.
    """
    return tuple(
        ConstructionCandidate(
            subject=candidate.subject,
            rank=candidate.rank,
            score=candidate.score,
            industry_code=None if candidate.exposure is None else candidate.exposure.industry_code,
        )
        for candidate in sorted(ranking.candidates, key=lambda item: item.rank)
    )


def candidates_from_shortlist_answer(
    answer: Mapping[str, object],
) -> tuple[ConstructionCandidate, ...]:
    """The stored-document adapter: the `admitted` array of a `shortlist_view` answer.

    A `Mapping` and not a `ShortlistRunResult`, because `backtest-no-numeric-stack-or-panel-plane`
    forbids every module under `backtest/` to import `openalpha_cn.shortlist_view`. The face
    reopens the document and hands the answer down; this reads it.

    **`admitted is None` is refused and is the whole reason this function exists.** That is the
    shortlist gate's *refusal* -- `null` for a refused list, `[]` for an admitted empty one, two
    answers `V2-P4-032` separated on purpose -- and a portfolio built out of a list the gate
    turned down would launder the refusal into a set of weights. An admitted but empty list is
    refused too, with a different sentence, because there is nothing to weight either way.
    """
    if "admitted" not in answer:
        raise PortfolioConstructionError(
            "a shortlist answer carries an `admitted` key; this payload has none, so it is not "
            "one -- `openalpha shortlist run --json` and `GET /api/v1/shortlists/{id}` both emit "
            "the shape this reads"
        )
    admitted = answer["admitted"]
    if admitted is None:
        raise PortfolioConstructionError(
            "this shortlist was refused by the gate (`admitted` is null), so it has no admitted "
            "names to weight; construct a portfolio out of a list the gate admitted, or fix what "
            "`blocks` says the list is short of"
        )
    if not isinstance(admitted, Sequence) or isinstance(admitted, str | bytes):
        raise PortfolioConstructionError(
            f"a shortlist answer's `admitted` is an array or null; got {type(admitted).__name__}"
        )
    if not admitted:
        raise PortfolioConstructionError(
            "this shortlist was admitted and holds no names, so there is nothing to weight; an "
            "empty admitted list is a gate that ran and passed over nobody, not a refusal"
        )
    return tuple(_candidate_from_admitted_row(index, row) for index, row in enumerate(admitted))


def _candidate_from_admitted_row(index: int, row: object) -> ConstructionCandidate:
    if not isinstance(row, Mapping):
        raise PortfolioConstructionError(
            f"`admitted[{index}]` is an object carrying `subject`, `rank` and `score`; got "
            f"{type(row).__name__}"
        )
    missing = tuple(key for key in ("subject", "rank", "score") if key not in row)
    if missing:
        raise PortfolioConstructionError(
            f"`admitted[{index}]` is missing {list(missing)}; a construction reads the order and "
            "the name, and a row without them cannot be placed in a tier"
        )
    try:
        return ConstructionCandidate(
            subject=str(row["subject"]),
            rank=int(str(row["rank"])),
            score=float(str(row["score"])),
            industry_code=None,
        )
    except (TypeError, ValueError) as error:
        raise PortfolioConstructionError(
            f"`admitted[{index}]` does not read as a candidate: {error}"
        ) from error


def construct_portfolio(
    *,
    candidates: Sequence[ConstructionCandidate],
    policy: PortfolioConstructionPolicy,
    previous: Mapping[str, Decimal] | None = None,
) -> PortfolioConstruction:
    """Turn one ranked list into target weights under `policy`, reporting everything withheld.

    The three steps are this module's docstring's three steps, in that order, and the order is
    load-bearing: trimming a book that has already been damped would spend turnover the budget
    just refused, so the budget is applied last and its cost is reported rather than repaired.
    """
    ordered = _ordered_candidates(candidates, policy=policy)
    industries = {
        candidate.subject: candidate.industry_code
        for _, candidate in ordered
        if candidate.industry_code is not None
    }
    untrimmed = _tiered_weights(ordered, policy=policy)
    trimmed = _trim_to_caps(untrimmed, industries=industries, policy=policy)
    quantised = {
        subject: weight.quantize(WEIGHT_QUANTUM, rounding=ROUND_DOWN)
        for subject, weight in trimmed.items()
    }
    prior = _declared_previous(previous)
    requested_turnover = _turnover(quantised, prior)
    budget = policy.limits.turnover_budget
    damping: Decimal | None = None
    final = quantised
    if budget is not None and requested_turnover > budget:
        damping = (budget / requested_turnover).quantize(WEIGHT_QUANTUM, rounding=ROUND_DOWN)
        final = _damped(quantised, prior, damping=damping)
    invested = sum(final.values(), start=_ZERO)
    tiers = {candidate.subject: tier for tier, candidate in ordered}
    return PortfolioConstruction(
        policy=policy,
        targets=tuple(
            TargetWeight(
                subject=candidate.subject,
                tier=tiers[candidate.subject],
                rank=candidate.rank,
                score=candidate.score,
                industry_code=candidate.industry_code,
                weight=final[candidate.subject],
                untrimmed_weight=untrimmed[candidate.subject].quantize(
                    WEIGHT_QUANTUM, rounding=ROUND_DOWN
                ),
            )
            for _, candidate in ordered
        ),
        cash_weight=_ONE - invested,
        unallocated_weight=max(_ZERO, policy.invested_weight - invested),
        turnover=_turnover(final, prior),
        turnover_before_budget=requested_turnover,
        turnover_budget=budget,
        turnover_damping=damping,
        caps_breached_after_turnover_damping=(
            () if damping is None else _breaches(final, ordered=ordered, policy=policy)
        ),
    )


def construction_view(construction: PortfolioConstruction) -> dict[str, Any]:
    """One construction as data, for whichever face is handing it out.

    `shortlist_view`'s argument for existing, unchanged: the CLI's `--json` and
    `OpenAlphaSDK.construction_view` emit these bytes and not two shapes that agree today.
    Decimals are rendered as strings so a JSON reader cannot silently take a weight through a
    float, which is the one conversion that would make `sum(weights) == invested_weight` stop
    being exactly true.
    """
    return {
        "schema_version": construction.schema_version,
        "method": construction.method,
        "policy": {
            "schema_version": construction.policy.schema_version,
            "tier_weights": [str(weight) for weight in construction.policy.tier_weights],
            "limits": {
                key: None if value is None else str(value)
                for key, value in construction.policy.limits.model_dump().items()
            },
        },
        "targets": [
            {
                "subject": target.subject,
                "tier": target.tier,
                "rank": target.rank,
                "score": target.score,
                "industry_code": target.industry_code,
                "weight": str(target.weight),
                "untrimmed_weight": str(target.untrimmed_weight),
                "was_adjusted": target.was_adjusted,
            }
            for target in construction.targets
        ],
        "invested_weight": str(construction.invested_weight),
        "cash_weight": str(construction.cash_weight),
        "unallocated_weight": str(construction.unallocated_weight),
        "turnover": str(construction.turnover),
        "turnover_before_budget": str(construction.turnover_before_budget),
        "turnover_budget": (
            None if construction.turnover_budget is None else str(construction.turnover_budget)
        ),
        "turnover_damping": (
            None if construction.turnover_damping is None else str(construction.turnover_damping)
        ),
        "caps_breached_after_turnover_damping": list(
            construction.caps_breached_after_turnover_damping
        ),
        "limitations": [
            {"code": limitation.code, "detail": limitation.detail}
            for limitation in KNOWN_CONSTRUCTION_LIMITATIONS
        ],
    }


# --- the three steps -----------------------------------------------------------------------------


def _ordered_candidates(
    candidates: Sequence[ConstructionCandidate],
    *,
    policy: PortfolioConstructionPolicy,
) -> tuple[tuple[int, ConstructionCandidate], ...]:
    """The candidates in rank order, paired with their 1-based tier, or a named refusal.

    Every refusal here is about a list that cannot be cut rather than about a market: duplicate
    subjects, ranks that are not `1..n`, fewer candidates than tiers, and an industry cap over
    names that carry no industry.
    """
    if not candidates:
        raise PortfolioConstructionError(
            "a construction needs at least one candidate; an empty list has no weights, which is "
            "not the same answer as a book that is all cash"
        )
    subjects = [candidate.subject for candidate in candidates]
    if len(set(subjects)) != len(subjects):
        raise PortfolioConstructionError(
            "a candidate list names each security once; this one repeats a subject, and two rows "
            "about one name would each be funded"
        )
    ordered = sorted(candidates, key=lambda item: item.rank)
    expected = tuple(range(1, len(ordered) + 1))
    if tuple(candidate.rank for candidate in ordered) != expected:
        raise PortfolioConstructionError(
            f"candidate ranks must be exactly 1..{len(ordered)} with no gap and no tie; got "
            f"{[candidate.rank for candidate in ordered]}. A tier is a contiguous block of ranks, "
            "so a gap moves a boundary and a tie makes the cut depend on iteration order"
        )
    tier_count = len(policy.tier_weights)
    if len(ordered) < tier_count:
        raise PortfolioConstructionError(
            f"{len(ordered)} candidates cannot fill {tier_count} tiers; an empty tier's weight "
            "would be redistributed silently, so the policy and the list have to agree first"
        )
    if policy.limits.max_industry_weight is not None:
        blind = tuple(candidate.subject for candidate in ordered if candidate.industry_code is None)
        if blind:
            raise PortfolioConstructionError(
                f"`max_industry_weight` is declared and {list(blind)} carry no `industry_code`; "
                "a cap that cannot see an industry is not a cap every industry passes. The "
                "shipped shortlist face loads no exposures at all, so this is expected there -- "
                "drop the cap, or build the ranking with an exposure cross section"
            )
    sizes = _tier_sizes(len(ordered), tier_count)
    paired: list[tuple[int, ConstructionCandidate]] = []
    position = 0
    for index, size in enumerate(sizes):
        for candidate in ordered[position : position + size]:
            paired.append((index + 1, candidate))
        position += size
    return tuple(paired)


def _tier_sizes(count: int, tiers: int) -> tuple[int, ...]:
    """Contiguous block sizes, as equal as `count` allows, remainder to the earlier tiers.

    A function of the two counts and nothing else, so the cut does not depend on the scores, on
    the subjects or on the order the caller happened to pass.
    """
    base, remainder = divmod(count, tiers)
    return tuple(base + (1 if index < remainder else 0) for index in range(tiers))


def _tiered_weights(
    ordered: Sequence[tuple[int, ConstructionCandidate]],
    *,
    policy: PortfolioConstructionPolicy,
) -> dict[str, Decimal]:
    """Each tier's share of the invested book, split equally inside the tier."""
    members: dict[int, list[str]] = {}
    for tier, candidate in ordered:
        members.setdefault(tier, []).append(candidate.subject)
    weights: dict[str, Decimal] = {}
    for tier, subjects in members.items():
        share = policy.invested_weight * policy.tier_weights[tier - 1] / Decimal(len(subjects))
        for subject in subjects:
            weights[subject] = share
    return weights


def _trim_to_caps(
    weights: Mapping[str, Decimal],
    *,
    industries: Mapping[str, str],
    policy: PortfolioConstructionPolicy,
) -> dict[str, Decimal]:
    """Clamp to every cap, offer the freed weight back, and clamp again. Bounded.

    The last operation of the last pass is always a clamp, so what this returns satisfies every
    cap unconditionally -- including on the input that exhausts the pass budget, where the
    unplaced remainder simply stays unplaced and is reported as cash by the caller.
    """
    current = dict(weights)
    for _ in range(_MAXIMUM_REDISTRIBUTION_PASSES):
        current = _clamped(current, industries=industries, policy=policy)
        residue = policy.invested_weight - sum(current.values(), start=_ZERO)
        if residue <= _ZERO:
            break
        headroom = _headroom(current, industries=industries, policy=policy)
        total = sum(headroom.values(), start=_ZERO)
        if total <= _ZERO:
            break
        for subject, room in headroom.items():
            current[subject] += residue * room / total
    return _clamped(current, industries=industries, policy=policy)


def _clamped(
    weights: Mapping[str, Decimal],
    *,
    industries: Mapping[str, str],
    policy: PortfolioConstructionPolicy,
) -> dict[str, Decimal]:
    """Every name at or under its position cap, every industry at or under the industry cap."""
    capped = {
        subject: min(weight, policy.limits.max_position_weight)
        for subject, weight in weights.items()
    }
    industry_cap = policy.limits.max_industry_weight
    if industry_cap is None:
        return capped
    totals: dict[str, Decimal] = {}
    for subject, weight in capped.items():
        code = industries.get(subject)
        if code is not None:
            totals[code] = totals.get(code, _ZERO) + weight
    for code, total in totals.items():
        if total <= industry_cap:
            continue
        scale = industry_cap / total
        for subject in capped:
            if industries.get(subject) == code:
                capped[subject] *= scale
    return capped


def _headroom(
    weights: Mapping[str, Decimal],
    *,
    industries: Mapping[str, str],
    policy: PortfolioConstructionPolicy,
) -> dict[str, Decimal]:
    """How much more each name could take, bounded by its own cap and by its industry's."""
    industry_cap = policy.limits.max_industry_weight
    totals: dict[str, Decimal] = {}
    if industry_cap is not None:
        for subject, weight in weights.items():
            code = industries.get(subject)
            if code is not None:
                totals[code] = totals.get(code, _ZERO) + weight
    room: dict[str, Decimal] = {}
    for subject, weight in weights.items():
        available = policy.limits.max_position_weight - weight
        code = industries.get(subject)
        if industry_cap is not None and code is not None:
            available = min(available, industry_cap - totals[code])
        room[subject] = max(_ZERO, available)
    return room


def _declared_previous(previous: Mapping[str, Decimal] | None) -> dict[str, Decimal]:
    """The caller's book, on this module's quantum, or a named refusal.

    Quantised on entry so `previous + damping * delta` lands on the grid the targets are on;
    otherwise a book declared to nine places would produce weights nothing could reproduce.
    """
    if previous is None:
        return {}
    book: dict[str, Decimal] = {}
    for subject, weight in previous.items():
        if weight < _ZERO:
            raise PortfolioConstructionError(
                f"the declared previous weight of {subject!r} is {weight}; this construction is "
                "long-only and a negative previous weight describes a book it cannot move from"
            )
        book[subject] = weight.quantize(WEIGHT_QUANTUM, rounding=ROUND_HALF_EVEN)
    total = sum(book.values(), start=_ZERO)
    if total > _ONE:
        raise PortfolioConstructionError(
            f"the declared previous weights sum to {total}, which is more than the whole book; "
            "weights are shares of equity, so a book over 1 is leveraged and nothing here "
            "accounts for the borrowing"
        )
    return book


def _turnover(target: Mapping[str, Decimal], previous: Mapping[str, Decimal]) -> Decimal:
    """Total absolute weight change across the union, **both sides counted**.

    Stated in this module's docstring and again here because the halved convention is equally
    common: selling one 5% name and buying another is `0.10` under this definition.
    """
    return sum(
        (
            abs(target.get(subject, _ZERO) - previous.get(subject, _ZERO))
            for subject in set(target) | set(previous)
        ),
        start=_ZERO,
    )


def _damped(
    target: Mapping[str, Decimal],
    previous: Mapping[str, Decimal],
    *,
    damping: Decimal,
) -> dict[str, Decimal]:
    """Move each held or wanted name `damping` of the way from `previous` to `target`.

    Only names the target names come back: a previously-held name the target drops is moved
    toward zero and, unless the damping is 1, is still held -- but this module returns *target
    weights for the ranked list*, and a name that is not on the list has no row to sit in. The
    residual holding it leaves is real and is exactly what
    `the_previous_book_is_declared_by_the_caller_and_never_read_from_a_ledger` and V2-P5-003's
    multi-day execution have to reconcile; it is not silently folded into these weights.

    The step is quantised toward zero so the realised move can only be smaller than the budget
    allowed, never larger.
    """
    moved: dict[str, Decimal] = {}
    for subject, wanted in target.items():
        start = previous.get(subject, _ZERO)
        step = ((wanted - start) * damping).quantize(WEIGHT_QUANTUM, rounding=ROUND_DOWN)
        moved[subject] = max(_ZERO, start + step)
    return moved


def _breaches(
    weights: Mapping[str, Decimal],
    *,
    ordered: Sequence[tuple[int, ConstructionCandidate]],
    policy: PortfolioConstructionPolicy,
) -> tuple[str, ...]:
    """Which caps the damped book is over, named, in a deterministic order.

    Reported and never repaired; see this module's docstring and
    `the_turnover_budget_can_leave_a_cap_breached_and_says_so_instead_of_retrimming`.
    """
    found: list[str] = sorted(
        f"position:{subject}"
        for subject, weight in weights.items()
        if weight > policy.limits.max_position_weight
    )
    industry_cap = policy.limits.max_industry_weight
    if industry_cap is not None:
        totals: dict[str, Decimal] = {}
        for _, candidate in ordered:
            code = candidate.industry_code
            if code is not None:
                totals[code] = totals.get(code, _ZERO) + weights.get(candidate.subject, _ZERO)
        found.extend(
            sorted(f"industry:{code}" for code, total in totals.items() if total > industry_cap)
        )
    if sum(weights.values(), start=_ZERO) > policy.invested_weight:
        found.append("total_exposure")
    return tuple(found)
