"""A buffer is never free, and the report that shows the saving shows the price beside it.

`V2-P5-024` asks for a *buffered / turnover-controlled 对照版本* reported **beside** the
unbuffered one **by default** (默认并列出报), so that a high-turnover factor's gross edge is not
misread as executable alpha (避免把高换手因子的 gross edge 误读为可执行 alpha).

## The two arms are one answer, and there is no way to ask for one

`TurnoverVariantReport` carries both arms as required fields. There is no `buffered=False`
argument, no single-arm constructor and no default that quietly returns one book, because
"reported beside the unbuffered one **by default**" is the whole row: a caller who can ask for
the flattering arm alone will, and a report whose shape makes that impossible is the only
version of this that survives contact with a deadline.

`construct_portfolio` already damps a book that exceeds `PortfolioLimits.turnover_budget`, and
that is a different device from this one. **Damping scales every move proportionally**; it
touches all names and reaches the budget by moving everything less far. **A buffer is a
no-trade band**; it leaves small moves entirely untraded and takes the large ones in full. They
answer different questions and the budget is left exactly where `V2-P5-001` put it -- this
module reads the construction that damping produced and bands it, so a policy carrying a
turnover budget gets both devices in the order the caller declared them.

## The saving and the price are the same number, and that is the report

Turnover falls under a band by construction: every name whose move the band suppressed
contributes zero instead of something. A report of that number alone would make a buffer look
like free money, and the reason it is not free is that the book you hold is no longer the book
the ranking asked for.

The first version of this module reported that distance as its own stored column,
`tracking_deviation`, beside `turnover_reduction`. **It is provably the same number**, and the
proof is two lines: a banded weight is either the target (traded) or the previous weight
(suppressed); a traded name contributes `0` to the deviation and `|t - p| - |t - p| = 0` to the
reduction, and a suppressed name contributes `|p - t|` to the deviation and `|t - p| - 0` to the
reduction. Both sums are the total suppressed move. A search over 200,000 random book pairs
found no counterexample and `test_the_saving_and_the_distance_from_the_target_are_one_number`
drives it.

So the column is gone and the identity is the headline instead: **every unit of turnover a band
saves is a unit of distance from the book the ranking asked for, one for one.** That is a more
useful thing to tell a reader than two columns that cannot disagree, and it is exactly the
`V2-P5-005` rule -- a derived column cannot disagree with its parents and therefore cannot
detect anything. `deviation_from_intended_book` remains as a `property`, so a face that wants
to print the distance can, without a stored field implying a second measurement.

What is *not* derivable from turnover, and is therefore stored:

- **`retained_positions`** -- names the unbuffered book drops to zero and the band keeps,
  because `abs(0 - previous) <= buffer`. These are the positions a buffered run is still
  holding that its own ranking no longer admits, and they are named rather than folded into a
  weight, since `V2-P5-001`'s `_damped` had to leave the same residual undeclared and said so.
- **`position_caps_breached`** -- a retained position can sit above `max_position_weight`,
  because the band suppressed the trim that would have brought it down. Reported and never
  repaired, which is `the_turnover_budget_can_leave_a_cap_breached_and_says_so_instead_of_
  retrimming` arriving through the other device.

## The cost of turnover is a declared rate or it is a named absence

Turning turnover into a number of basis points needs a cost per unit of turnover, and this
module does not have one and will not invent one. A caller who declares
`cost_per_unit_turnover` gets `turnover_cost` on both arms and `cost_saved` between them; a
caller who does not gets `None` on all three and `cost_absence_reason` saying that the saving
is reported in turnover because no rate was declared. An invented default -- ten basis points,
say, because it is the number everyone uses -- would be a figure this repository books as a
defect, and it would be multiplied by every turnover number in the report.

Nothing here is a `backtest/execution.py` fill model. `cost_per_unit_turnover` is a linear
declared rate, it does not know about spread, impact or the size of the book, and
`the_cost_model_is_one_declared_linear_rate_and_not_an_execution_simulation` says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.portfolio_policy import (
    WEIGHT_QUANTUM,
    ConstructionCandidate,
    PortfolioConstructionPolicy,
    construct_portfolio,
)

_ZERO: Final[Decimal] = Decimal(0)

UNBUFFERED: Final[str] = "unbuffered"
BUFFERED: Final[str] = "buffered"


class TurnoverVariantError(ValueError):
    """A buffer that cannot be applied, or a cost rate that cannot be a cost rate."""


class TurnoverVariantLimitation(BaseModel):
    """One named thing this module's paired report does not claim to have measured."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    detail: str


KNOWN_TURNOVER_VARIANT_LIMITATIONS: Final[tuple[TurnoverVariantLimitation, ...]] = (
    TurnoverVariantLimitation(
        code="the_buffer_is_a_no_trade_band_and_not_the_turnover_budget_v2_p5_001_already_has",
        detail=(
            "PortfolioLimits.turnover_budget damps every move proportionally to reach a total; "
            "this band leaves each small move untraded and takes each large one whole. Two "
            "books with the same turnover under the two devices hold different names at "
            "different weights. The band is applied to the construction the budget already "
            "produced, so a policy declaring both gets the budget first and the band second, "
            "and neither is a substitute for the other."
        ),
    ),
    TurnoverVariantLimitation(
        code="the_previous_book_is_declared_by_the_caller_and_is_never_read_from_a_ledger",
        detail=(
            "Every number here is measured against the `previous` mapping the caller passed. "
            "V2-P5-001 says the same of construct_portfolio and it matters more under a band, "
            "because a band's entire behaviour is a comparison against the previous weight: a "
            "caller who passes an empty previous book gets a buffered arm identical to the "
            "unbuffered one and a turnover reduction of zero, which is arithmetically correct "
            "and says nothing about what rebalancing that book would have cost."
        ),
    ),
    TurnoverVariantLimitation(
        code="the_cost_model_is_one_declared_linear_rate_and_not_an_execution_simulation",
        detail=(
            "cost_per_unit_turnover is a single declared number multiplied by turnover. It "
            "does not model spread, market impact, participation rate, the size of the book, "
            "or the fact that the band concentrates trading into fewer and larger orders -- "
            "which is the one effect most likely to make a buffered arm's realised cost saving "
            "smaller than this arithmetic suggests. backtest/execution.py is where fills are "
            "simulated; nothing here calls it. When no rate is declared, all three cost "
            "columns are None and cost_absence_reason says so rather than a default appearing."
        ),
    ),
    TurnoverVariantLimitation(
        code="a_retained_position_is_named_but_its_future_is_not_modelled",
        detail=(
            "A name the unbuffered book drops and the band keeps is listed in "
            "retained_positions at the weight it stays at. What happens to it next -- whether "
            "the following rebalance drops it, whether it drifts above the band and is sold at "
            "a worse price -- is a path this single-rebalance report cannot see. A band "
            "evaluated one rebalance at a time will always look cheaper than the same band run "
            "over a year, and this module reports one rebalance."
        ),
    ),
    TurnoverVariantLimitation(
        code="the_band_can_leave_a_position_cap_breached_and_says_so_instead_of_retrimming",
        detail=(
            "Suppressing a trade suppresses the trim that trade was carrying, so a retained or "
            "unmoved position can sit above PortfolioLimits.max_position_weight. Those names "
            "are listed in position_caps_breached and are not repaired, because repairing them "
            "would spend the turnover the band was asked to save and the report would show a "
            "saving it did not make. This is V2-P5-001's own choice under its turnover budget, "
            "arriving through the other device."
        ),
    ),
    TurnoverVariantLimitation(
        code="the_distance_from_the_intended_book_is_a_weight_distance_and_not_a_return_difference",
        detail=(
            "deviation_from_intended_book is a sum of absolute weight differences at one "
            "instant, and it is provably equal to turnover_reduction rather than being a "
            "second measurement of it -- it is a property and not a stored column for that "
            "reason. It is not tracking error, it is not annualised, it carries no covariance "
            "and it cannot be turned into an expected return difference without a risk model "
            "this module does not have and ADR-0003's nine dependencies would not admit. A "
            "band that saves ten points of turnover moves the book ten points away from the "
            "target; whether those ten points cost or earn anything is not measured here."
        ),
    ),
)
"""What the paired report does not claim, stated where the pairing is computed."""


class TurnoverCostModel(BaseModel):
    """A declared linear cost per unit of turnover, and what it is supposed to represent."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    cost_per_unit_turnover: Decimal = Field(ge=0)
    """Multiplied by turnover on the both-sides convention `_turnover` uses.

    `ge=0` and not `gt=0`: a caller modelling a zero-cost venue is making a claim this module
    should report rather than refuse, and a zero rate produces zero cost on both arms and a
    zero saving, which is the correct answer to the question they asked.
    """
    definition: str = Field(min_length=1, max_length=512)
    """What the rate covers -- commission only, commission and stamp duty, an impact estimate."""


class TurnoverArm(BaseModel):
    """One arm of the pair: its book, what it traded, and what that trading would cost."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: Literal["unbuffered", "buffered"]
    weights: tuple[tuple[str, Decimal], ...]
    """The book as ordered pairs rather than a mapping, so the report has one stable order."""
    turnover: Decimal = Field(ge=0)
    invested_weight: Decimal = Field(ge=0)
    names_traded: int = Field(ge=0)
    """How many names moved at all. A band's saving shows up here before it shows up anywhere."""
    turnover_cost: Decimal | None = None
    """`turnover * cost_per_unit_turnover`, or `None` when no rate was declared."""


class TurnoverVariantReport(BaseModel):
    """Both arms, the saving, and the three columns that say what the saving cost.

    Both arms are required fields. A report of one arm is unrepresentable, which is `V2-P5-024`'s
    *默认并列出报* expressed as a type rather than as a convention somebody remembers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["turnover-variant-report/v1"] = "turnover-variant-report/v1"
    method: Literal["heuristic, not optimized"] = "heuristic, not optimized"
    """Carried through from `PortfolioConstruction`: banding a heuristic book leaves it one."""
    policy: PortfolioConstructionPolicy
    buffer: Decimal = Field(ge=0, le=1)
    unbuffered: TurnoverArm
    buffered: TurnoverArm
    turnover_reduction: Decimal = Field(ge=0)
    """`unbuffered.turnover - buffered.turnover`. Never negative: a band cannot add a trade.

    This is *also* the buffered book's total absolute distance from the unbuffered target; see
    `deviation_from_intended_book` and the module docstring for why that is an identity rather
    than a coincidence, and why there is no second column for it.
    """
    retained_positions: tuple[str, ...] = ()
    """Names the unbuffered book drops to zero and the band keeps."""
    position_caps_breached: tuple[str, ...] = ()
    """Names the buffered book holds above `max_position_weight`, reported and not repaired."""
    cost_model: TurnoverCostModel | None = None
    cost_saved: Decimal | None = None
    cost_absence_reason: str | None = None

    @model_validator(mode="after")
    def validate_the_pair_is_a_pair_and_the_cost_is_present_or_named_absent(self) -> Self:
        """The two arms are the two arms, and the cost columns stand or fall together.

        Reachable from a *document* rather than from `report_turnover_variants`, which is the
        path a stored report is read back through -- the argument
        `CohortStatistics.validate_the_inference_is_present_or_named_absent` makes for the same
        shape in `V2-P5-008`.
        """
        if self.unbuffered.label != UNBUFFERED or self.buffered.label != BUFFERED:
            raise ValueError(
                "the unbuffered arm is labelled unbuffered and the buffered arm buffered"
            )
        if self.turnover_reduction != self.unbuffered.turnover - self.buffered.turnover:
            raise ValueError("turnover_reduction must be the difference between the two arms")
        if self.turnover_reduction < _ZERO:
            raise ValueError(
                "a no-trade band cannot raise turnover; a negative reduction means the buffered "
                "arm traded more than the book it was banding"
            )
        costed = (self.cost_model, self.cost_saved, self.unbuffered.turnover_cost)
        if any(part is not None for part in costed) and not all(
            part is not None for part in costed
        ):
            raise ValueError(
                "a declared cost rate, both arms' turnover costs and the saving are present "
                "together or absent together"
            )
        if self.cost_model is not None and self.cost_absence_reason is not None:
            raise ValueError("a report carrying a declared cost rate states no cost absence")
        if self.cost_model is None and self.cost_absence_reason is None:
            raise ValueError(
                "a report with no declared cost rate must say why it publishes no cost"
            )
        return self

    @property
    def deviation_from_intended_book(self) -> Decimal:
        """How far the buffered book sits from the one the ranking asked for.

        Returns `turnover_reduction`, because the two are provably equal -- see the module
        docstring's proof. A `property` and not a stored field precisely so it cannot be
        mistaken for an independent measurement of the band's price: it is the same measurement
        read the other way round.
        """
        return self.turnover_reduction

    @property
    def turnover_ratio(self) -> Decimal | None:
        """`buffered / unbuffered`, or `None` when the unbuffered arm traded nothing.

        `None` rather than zero or one, because a rebalance that requested no trade at all has
        no ratio to report and any number here would be read as a saving the band produced.
        """
        if self.unbuffered.turnover == _ZERO:
            return None
        return self.buffered.turnover / self.unbuffered.turnover


def _turnover(target: Mapping[str, Decimal], previous: Mapping[str, Decimal]) -> Decimal:
    """Total absolute weight change across the union, **both sides counted**.

    Deliberately the same convention and the same arithmetic as `portfolio_policy._turnover`:
    selling one 5% name and buying another is `0.10`. A second convention in a module whose
    headline number is a turnover *comparison* would make the two halves of this repository
    disagree about what it had saved.
    """
    return sum(
        (
            abs(target.get(subject, _ZERO) - previous.get(subject, _ZERO))
            for subject in set(target) | set(previous)
        ),
        start=_ZERO,
    )


def _banded(
    target: Mapping[str, Decimal],
    previous: Mapping[str, Decimal],
    *,
    buffer: Decimal,
) -> dict[str, Decimal]:
    """Trade a name only when its requested move is strictly larger than the band.

    `<= buffer` is suppressed and `> buffer` is taken **whole**, which is what makes this a band
    rather than a damping: a name asked to move 3% under a 2% band moves the whole 3%, not 1%.
    The alternative -- moving it to the edge of the band -- is a third device again and would
    make every name trade a little, which is precisely the outcome a band exists to avoid.

    The union of both books is walked, not just the target's names, because a name the target
    drops to zero has a requested move of exactly its previous weight and the band has an
    opinion about it. That opinion is what `retained_positions` reports.
    """
    banded: dict[str, Decimal] = {}
    for subject in set(target) | set(previous):
        wanted = target.get(subject, _ZERO)
        start = previous.get(subject, _ZERO)
        banded[subject] = wanted if abs(wanted - start) > buffer else start
    return {subject: weight for subject, weight in banded.items() if weight != _ZERO}


def _declared_previous(previous: Mapping[str, Decimal] | None) -> dict[str, Decimal]:
    """The caller's previous book, quantised, with the refusals `V2-P5-001` already makes."""
    if not previous:
        return {}
    book: dict[str, Decimal] = {}
    for subject, weight in previous.items():
        if weight < _ZERO:
            raise TurnoverVariantError(
                f"previous weight for {subject!r} is {weight}; a negative previous holding is a "
                "short position and nothing in this construction plane declares one"
            )
        book[subject] = Decimal(weight).quantize(WEIGHT_QUANTUM, rounding=ROUND_DOWN)
    return book


def report_turnover_variants(
    *,
    candidates: Sequence[ConstructionCandidate],
    policy: PortfolioConstructionPolicy,
    buffer: Decimal,
    previous: Mapping[str, Decimal] | None = None,
    cost_model: TurnoverCostModel | None = None,
) -> TurnoverVariantReport:
    """Construct once, band the result, and report both books with the price of the band.

    One `construct_portfolio` call and not two. The buffered arm is the unbuffered target seen
    through a no-trade band, so both arms answer to the same tiers, the same caps and the same
    turnover budget, and every difference between them is the band. Constructing twice with
    different arguments would let a second difference in and the comparison would stop being a
    comparison of one thing.
    """
    if buffer < _ZERO or buffer > 1:
        raise TurnoverVariantError(
            f"a no-trade band of {buffer} is not a weight; the band is compared against an "
            "absolute weight change and lives in [0, 1]"
        )

    prior = _declared_previous(previous)
    construction = construct_portfolio(candidates=candidates, policy=policy, previous=prior)
    target = {item.subject: item.weight for item in construction.targets if item.weight != _ZERO}
    banded = _banded(target, prior, buffer=buffer)

    unbuffered_turnover = _turnover(target, prior)
    buffered_turnover = _turnover(banded, prior)

    rate = None if cost_model is None else cost_model.cost_per_unit_turnover
    unbuffered_cost = None if rate is None else unbuffered_turnover * rate
    buffered_cost = None if rate is None else buffered_turnover * rate

    retained = tuple(
        sorted(subject for subject in banded if subject not in target and prior.get(subject))
    )
    cap = policy.limits.max_position_weight
    breached = tuple(sorted(subject for subject, weight in banded.items() if weight > cap))

    return TurnoverVariantReport(
        policy=policy,
        buffer=buffer,
        unbuffered=TurnoverArm(
            label=UNBUFFERED,  # type: ignore[arg-type]
            weights=tuple(sorted(target.items())),
            turnover=unbuffered_turnover,
            invested_weight=sum(target.values(), start=_ZERO),
            names_traded=sum(
                1
                for subject in set(target) | set(prior)
                if target.get(subject, _ZERO) != prior.get(subject, _ZERO)
            ),
            turnover_cost=unbuffered_cost,
        ),
        buffered=TurnoverArm(
            label=BUFFERED,  # type: ignore[arg-type]
            weights=tuple(sorted(banded.items())),
            turnover=buffered_turnover,
            invested_weight=sum(banded.values(), start=_ZERO),
            names_traded=sum(
                1
                for subject in set(banded) | set(prior)
                if banded.get(subject, _ZERO) != prior.get(subject, _ZERO)
            ),
            turnover_cost=buffered_cost,
        ),
        turnover_reduction=unbuffered_turnover - buffered_turnover,
        retained_positions=retained,
        position_caps_breached=breached,
        cost_model=cost_model,
        cost_saved=(
            None
            if unbuffered_cost is None or buffered_cost is None
            else unbuffered_cost - buffered_cost
        ),
        cost_absence_reason=(
            None
            if cost_model is not None
            else (
                "no cost_per_unit_turnover was declared, so the saving is reported in turnover "
                "and not in money; a default rate would be a number this module invented and "
                "then multiplied by every turnover figure in the report"
            )
        ),
    )


def turnover_variant_view(report: TurnoverVariantReport) -> dict[str, Any]:
    """One paired report as data, for whichever face is handing it out.

    `construction_view`'s argument for existing, and its Decimal convention with it: weights,
    turnovers and costs are rendered as **strings**, so a JSON reader cannot take a weight
    through a float and make `unbuffered.turnover - buffered.turnover == turnover_reduction`
    stop holding on the way out.

    The two arms are rendered under one `arms` key in a fixed order rather than as two
    top-level objects, because a face that prints them is printing a comparison and the shape
    should not let it print one.
    """

    def _arm(arm: TurnoverArm) -> dict[str, Any]:
        return {
            "label": arm.label,
            "weights": [
                {"subject": subject, "weight": str(weight)} for subject, weight in arm.weights
            ],
            "turnover": str(arm.turnover),
            "invested_weight": str(arm.invested_weight),
            "names_traded": arm.names_traded,
            "turnover_cost": None if arm.turnover_cost is None else str(arm.turnover_cost),
        }

    return {
        "schema_version": report.schema_version,
        "method": report.method,
        "buffer": str(report.buffer),
        "arms": [_arm(report.unbuffered), _arm(report.buffered)],
        "turnover_reduction": str(report.turnover_reduction),
        "deviation_from_intended_book": str(report.deviation_from_intended_book),
        "turnover_ratio": (None if report.turnover_ratio is None else str(report.turnover_ratio)),
        "retained_positions": list(report.retained_positions),
        "position_caps_breached": list(report.position_caps_breached),
        "cost_model": (
            None
            if report.cost_model is None
            else {
                "cost_per_unit_turnover": str(report.cost_model.cost_per_unit_turnover),
                "definition": report.cost_model.definition,
            }
        ),
        "cost_saved": None if report.cost_saved is None else str(report.cost_saved),
        "cost_absence_reason": report.cost_absence_reason,
        "policy": {
            "schema_version": report.policy.schema_version,
            "tier_weights": [str(weight) for weight in report.policy.tier_weights],
            "max_position_weight": str(report.policy.limits.max_position_weight),
            "max_total_exposure": str(report.policy.limits.max_total_exposure),
            "min_cash_weight": str(report.policy.limits.min_cash_weight),
            "turnover_budget": (
                None
                if report.policy.limits.turnover_budget is None
                else str(report.policy.limits.turnover_budget)
            ),
        },
        "limitations": [
            {"code": limitation.code, "detail": limitation.detail}
            for limitation in KNOWN_TURNOVER_VARIANT_LIMITATIONS
        ],
    }
