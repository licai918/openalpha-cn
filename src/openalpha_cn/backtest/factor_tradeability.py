"""Turnover, coverage and capacity for a quantile study (`V2-P3-007`).

`V2-P3-006` answers "what would holding the ordering have paid, net". This answers the question a
reader asks straight afterwards and that a net return cannot: **could it have been held at all,
and with how much money.** The issue's one-line brief is the acceptance criterion -- *make the
signals that are statistically attractive and not implementable visible* -- so every number here
is chosen for its ability to separate two factors whose IC is identical.

It stores nothing, reads no partition and computes no return. Its three inputs are all things
`V2-P3-005` and `V2-P3-006` already produced -- an `ICCrossSection`, a `PeriodPortfolio` and the
`QuantilePortfolioSpec` that measured it -- plus one new caller-supplied fact, a session's
turnover in yuan, which is the only quantity a capacity model can be built on and which no
contract in `backtest/` holds.

## Coverage: a four-step funnel whose steps have four different owners

"Coverage" is ambiguous until it is decomposed, and the decomposition is the finding. Every
security offered to a cross section passes four gates before it is a position, and **each gate
belongs to a different contract**:

    universe   -> valued       the factor engine        (FactorCoverage / the tier's transform)
    valued     -> admissible   the tier's own policy    (TIER_ADMITTED_CODES)
    admissible -> scored       domain/labels.py         (halt, limit lock, unpublished band, ...)
    scored     -> held         AShareExecutionPolicy    (suspension, lot, T+1, derived band)

`CoverageFunnel` carries all five counts and the four rates between them, and
`implementable_rate` is `held / universe` -- the product of the four, up to the rounding of four
divisions. The two middle steps are the ones a report of a single "coverage" number loses:
**a factor that scores the whole market and whose names cannot be labelled is a different defect
from one that scores nobody**, and the remedies are not the same.

The two tier tables are *imported* from `factor_ic` rather than restated, and both are
load-bearing rather than one being decoration. `ICCensus.excluded_by_coverage` is keyed by the
codes the tier does **not** admit, so recovering `valued_count` from it means walking the tier's
whole vocabulary and keeping the cells that carry a value and are not admitted -- which is
`TIER_VALUE_CODES` and `TIER_ADMITTED_CODES` respectively, and neither can be dropped. They
differ in exactly one cell across the three tiers (`processed`'s `imputed` carries a value and is
not admitted), which is why the separating fixture is a processed cross section and why
`tests/unit/backtest/test_factor_tradeability.py::
test_an_imputed_processed_value_is_valued_and_is_not_admissible` is written on that tier alone.

**The per-group decomposition is the acceptance criterion.** A factor whose long group is exactly
the set of names the market refuses has a high IC and no portfolio, and the two are told apart by
`top_group_execution_shortfall` -- the overall execution rate less the long group's own.
**One** cross section against two markets that refuse opposite ends of it -- so the IC is not
merely equal but the same object's, and the period-level funnel is identical too -- is what
`test_the_same_ic_and_the_same_funnel_are_told_apart_by_the_top_groups_execution_rate` drives.

Per-group counts are recovered as *assigned minus rejected* rather than read off
`PeriodPortfolio.groups`, and that is not a stylistic choice: under `unfillable_after_execution`
-- the code `V2-P3-006` says this issue exists to surface -- `groups` is empty and the per-security
verdicts are all that survives. Deriving the counts from `rejections` makes the decomposition
available under exactly the code that needs it, and under `measured` the two readings are both
available and are **required to agree**, which is what makes the derivation checkable rather than
merely convenient.

## Turnover: a rolling portfolio, and the schedule is measured rather than declared

`V2-P3-006` holds one round trip per `as_of` and carries
`every_period_is_an_independent_round_trip_so_turnover_is_total` to say what that costs: turnover
is 100% per period by construction and its fees are an **upper** bound. This module is that
limitation's stated solution and it is built as a holdings state over the long group.

**Nobody here invents a rebalance frequency.** It is already declared: it is the spacing of the
`as_of`s the caller offered, and this module *measures* it rather than taking it as a parameter.
What it does refuse is a schedule on which a holdings state does not exist. At horizon `h`, two
prediction days one session apart produce windows sharing `h` of the `h + 1` sessions each spans
(`domain/labels.py::overlapping_windows`), and a name "carried across" two overlapping windows is
a name held twice, not a name held once. So consecutive measured periods must satisfy
`entry_day(k) >= exit_day(k - 1)`, and a series that does not is `overlapping_schedule` -- a
coverage code and not an exception, because a caller measuring IC daily over a year is doing
something correct for an IC and impossible for a turnover, and should be told which.

Turnover itself is reported **twice**, on names and on money, because the two answer different
questions and A-share lot rounding makes them differ:

    name_turnover  = (entered + exited)            / (from_count + to_count)
    money_turnover = (sold_value + bought_value)   / (from_value  + to_value)

Both are the symmetric (Sorensen) reading over the two ends of the rebalance rather than
`entered / to_count`, and the reason is that the two ends are **not the same size** -- the market
empties the long group by different amounts on different days, and a one-sided denominator turns
a portfolio that shrank into a portfolio that turned over. Both are in `[0, 1]` by construction.

What the rebalance buys is stated as money and it is taken from fills that already exist: for
each retained name, `V2-P3-006` charged an exit fee at period `k - 1` and an entry fee at period
`k`, and a rolling portfolio pays neither. `avoided_cost` is the sum of exactly those legs. **No
new order is simulated and no price is invented anywhere in this module.** Each leg can be
avoided at most once across the whole series -- period `k`'s entry leg belongs to the rebalance
into `k` and its exit leg to the rebalance out of `k` -- so `avoided_cost <= round_trip_cost`,
which `TurnoverSeries` requires rather than assumes.

That gives the bracket the limitation asked for:

    round_trip_cost                   V2-P3-006's fee, an upper bound (100% turnover)
    round_trip_cost - avoided_cost    a lower bound (`rolling_cost`)

and it is a **lower** bound rather than the answer because a retained position's share count is
not the same at `k` as at `k - 1`: `position_quantity` is recomputed against `k`'s entry close, so
a real rolling portfolio trims or adds to the declared capital and pays a fee on the difference.
The direction is signed, bounded by the trade it does not make, and carried as
`the_avoided_cost_is_an_upper_bound_on_what_a_rebalance_saves`.

**This module publishes no rolling return and cannot.** Between `exit_day(k - 1)` and
`entry_day(k)` a retained position is held across sessions no label window covers, so its return
over the gap is not a quantity any contract here holds -- and compounding the periods that *are*
covered is what `V2-P3-006` already refuses to do. Turnover and cost are what a holdings state
can answer honestly; a net asset value is not.

## Capacity: a declared participation cap, and everything else is arithmetic

**Capacity is a modelling judgement and not a measurement**, so the judgement is one declared
number and the rest is forced:

1. **`participation_cap` is declared with no default**, in `(0, 1]`. It is the largest fraction of
   one session's turnover this study is willing to be. It is a *constraint*, not an impact
   function: this repository holds no dataset from which a price-impact coefficient could be
   estimated, and `CostSchedule` has no size term at all, so a model that priced impact would be
   pricing a number nobody measured. `capacity_is_a_declared_participation_cap_and_not_a_market
   _impact_model` says so by name.
2. **One session, one fill**, because that is what `AShareExecutionPolicy` does -- an order fills
   at one bar's close or is rejected. Spreading a position over several sessions would be a second
   execution model beside the one this repository has.
3. **The session's turnover in yuan is the caller's**, dated on or before the entry day and
   refused after it. `daily.amount` is published in **thousands of yuan**, which is measured and
   not read off a field list: over eleven real rows spanning 2001-01-02 to 2026-06-15,
   `amount * 1000 / (vol * 100)` is the only reading of that column pair whose implied VWAP falls
   inside the session's own low-to-high range, on **11 of 11**, and the other three readings fall
   outside it on all eleven. `liquidity_from_amount` is that conversion and
   `CNY_PER_TURNOVER_UNIT` is the constant; `panel_factors.CNY_PER_AMOUNT_UNIT` is the same number
   one plane over and may not be imported here, so the two are pinned against each other by
   `test_the_turnover_unit_is_the_panel_engines_own_amount_unit`.
4. **`min` binds, and that is forced by `V2-P3-006`'s sizing rather than chosen here.** Every
   position gets the same declared `position_capital`, so the portfolio is implementable at that
   capital only if the *least* liquid held name is -- `equal_weight_capacity` is
   `binding_capacity * held_count` and `capital_multiple` is `binding_capacity /
   position_capital`, which is below `1.0` exactly when the declared study is already over
   capacity.
5. **The alternative sizing is reported as the bound it is.** `liquidity_weighted_capacity` is
   `participation_cap * sum(turnover)` -- what the same names would absorb if the budget were
   allowed to follow liquidity, which nothing in this repository builds. `concentration` is the
   ratio of the two and is the number that says how much of the group's capacity one name is
   destroying.

The size of that effect is not hypothetical. Two of the eleven real rows above are `000001.SZ` on
2026-06-12 (¥2,263,042,930.57 of turnover) and `000569.SZ` on 2001-01-02 (¥6,579,577.80): a group
holding both has its capacity set by the second, **344x** below the first, and at a 1%
participation cap that name absorbs **¥65,795.78** -- so a study declaring `V2-P3-006`'s own test
capital of ¥100,000 per position is already at a `capital_multiple` of **0.658**, over capacity
before a single fee is counted.

## The long group, and why capacity and turnover are measured on it alone

Coverage is reported per group; turnover and capacity are reported for the **long** group only --
`PeriodPortfolio.top_group_index`, so the factor's own `direction` decides it and not a parameter.
That is the portfolio this repository can execute. There is no short one, and the reason is
contract-level and already measured:
`KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS.the_long_short_spread_is_not_a_shortable_portfolio` records
that `AShareExecutionPolicy` has no short side and that a short round trip is exactly the pair of
verdicts the policy and the label contract are known to disagree on. Sizing a portfolio nobody
could open would be quoting a capacity for a trade that does not exist.

## Narrow samples, which this repository has taken eight Critical findings on

Both statistics here degrade on a small group and **neither degradation is hidden**:

- A turnover over two groups of `n` names counts in units of `1 / (from_count + to_count)`, so a
  three-name group's turnover has seven attainable values and no more. `Rebalance.resolution` is
  that unit and is reported beside the ratio.
- A capacity is a `min`, so a group's `binding_capacity` is **one security's** number however many
  it holds. `binding_subject` names it, `held_count` sits beside it, and `concentration` is how
  far that one name is from the rest.

**No second floor is declared for either.** `QuantilePortfolioSpec.min_securities_per_group` is
already the declared floor under a group's size, it has no default, and
`KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS.the_reported_returns_move_with_the_declared_position
_capital` already records what declaring `1` buys. A second floor here would be a second place to
declare the same thing and a second place for the two to disagree.

## Layering, and the numerical stack

`backtest/` over `domain/`, standard library plus this package's own `backtest/factor_ic.py` and
`backtest/factor_portfolio.py` -- the same edges `factor_portfolio` already has, and no new
dependency. The whole per-period workload is one `rank_groups` (a `sorted()`), one pass over the
pairs and one `min` over the held names; a series adds one set intersection per rebalance. ADR-0003
carries the measurement rather than this docstring restating it. **Runtime dependencies remain
nine.**
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIERS,
    TIER_ADMITTED_CODES,
    TIER_COVERAGE_ORDER,
    TIER_VALUE_CODES,
    FactorTier,
    ICCensus,
    ICCrossSection,
)
from openalpha_cn.backtest.factor_portfolio import (
    HOLDING_OUTCOME_ORDER,
    MINIMUM_PORTFOLIO_GROUPS,
    PRE_EXECUTION_COVERAGE,
    PeriodCoverage,
    PeriodPortfolio,
    QuantilePortfolioSpec,
    rank_groups,
)
from openalpha_cn.domain.factor import FactorDirection
from openalpha_cn.domain.time import ensure_aware

__all__ = [
    "CAPACITY_COVERAGE_CODES",
    "CNY_PER_TURNOVER_UNIT",
    "EXCLUDED_OUTCOME_ORDER",
    "KNOWN_TRADEABILITY_LIMITATIONS",
    "MAXIMUM_REBALANCES",
    "MINIMUM_REBALANCES",
    "TRADEABILITY_LIMITATION_CODES",
    "TURNOVER_COVERAGE_CODES",
    "TURNOVER_COVERAGE_ORDER",
    "CapacityCoverage",
    "CoverageFunnel",
    "FactorTradeabilityError",
    "GroupCapacity",
    "GroupCoverage",
    "PeriodTradeability",
    "Rebalance",
    "SessionLiquidity",
    "TradeabilityLimitation",
    "TradeabilitySpec",
    "TradeabilityStudy",
    "TurnoverCoverage",
    "TurnoverSeries",
    "liquidity_from_amount",
]

_ZERO: Final[Decimal] = Decimal("0.00")

EXCLUDED_OUTCOME_ORDER: Final[tuple[str, ...]] = tuple(
    code for code in HOLDING_OUTCOME_ORDER if code != "held"
)
"""`HOLDING_OUTCOME_ORDER` without `held`: the cells a group's exclusion table is keyed by.

Derived from `V2-P3-006`'s own tuple rather than restated, so a fifth `HoldingOutcome` arrives
here without anybody remembering to add it -- `PortfolioCensus.__post_init__` builds the same
tuple inline and `tests/unit/backtest/test_factor_tradeability.py::
test_a_group_exclusion_table_is_keyed_by_the_quantile_studys_own_outcomes` holds the two against
each other so the census and this table cannot disagree about which codes exist or in what order.

Annotated `tuple[str, ...]` and not `tuple[HoldingOutcome, ...]` deliberately: the counters below
are keyed by `str` because `dict` is invariant in its key type, and a table annotated with the
`Literal` could not be handed to code that takes the wider one -- `TIER_COVERAGE_ORDER`'s measured
reason, one module over.
"""


class FactorTradeabilityError(ValueError):
    """Raised for a malformed tradeability question -- never for a fact about the market.

    `FactorPortfolioError`'s argument one plane along, and the list of things that are *not* this
    is the same list: a period the market emptied, a schedule whose windows overlap, a group with
    no priced holding and a factor whose long group could not be traded at all are each a coverage
    code, because a report that showed only the days that worked would be a report of a different
    factor.
    """


TurnoverCoverage = Literal["measured", "overlapping_schedule", "insufficient_rebalances"]
"""Whether a series of periods produced a rolling portfolio's turnover, and if not, **why**.

Read in the order `TradeabilityStudy.turnover` decides them, which is the order declared:

- **`overlapping_schedule`** -- two consecutive measured periods where the later one enters before
  the earlier one exits. Decided **first**, because a rebalance is a transition of a holdings
  state and on an overlapping schedule that state does not exist: a name "carried across" is held
  in two windows at once. Counting transitions of a state that is not there would be counting
  something else and calling it thin.
- **`insufficient_rebalances`** -- the schedule rolls and there are fewer than
  `TradeabilitySpec.min_rebalances` transitions between `measured` periods. A count.
- **`measured`** -- and only then does `rebalances` carry anything.

`tests/unit/backtest/test_factor_tradeability.py::
test_an_overlapping_series_that_is_also_short_reports_the_schedule_rather_than_the_count` drives
the one fixture on which both conditions hold and pins the declared order.
"""

TURNOVER_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(TurnoverCoverage))

TURNOVER_COVERAGE_ORDER: Final[tuple[TurnoverCoverage, ...]] = get_args(TurnoverCoverage)

CapacityCoverage = Literal["measured", "no_holdings", "unpriced_holdings"]
"""Whether one period's long group could be sized, and if not, why.

- **`no_holdings`** -- the long group holds nothing. Either no order was placed at all (one of
  `PRE_EXECUTION_COVERAGE`) or the market refused every member, which is what
  `unfillable_after_execution` can mean. Both are "there is no position to size", and they are
  told apart by `PeriodTradeability.period_coverage`, which is carried rather than re-derived.
- **`unpriced_holdings`** -- at least one held name has no `SessionLiquidity` for the declared
  session. Refused rather than averaged over the priced remainder, which is
  `panel_factors._amihud_60`'s decision at this plane: a capacity taken over whichever names
  happened to have a turnover row is a number whose sample size is a function of the data, and
  this repository has taken eight Critical findings on quantities calibrated over a sample nobody
  declared. A missing row is also not a neutral omission -- a security with no turnover on a
  session is a security that did not trade it, which is the end of the distribution a `min` is
  looking for.
- **`measured`** -- and only then is `capacity` not `None`.
"""

CAPACITY_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(CapacityCoverage))

CNY_PER_TURNOVER_UNIT: Final[Decimal] = Decimal(1000)
"""Yuan per unit of `daily.amount`, restated here because `backtest/` may not reach the panel.

`panel_factors.CNY_PER_AMOUNT_UNIT` is the same number and carries the measurement: across eleven
real `daily` rows spanning 2001-01-02 to 2026-06-15, `amount * 1000 / (vol * 100)` is the only
reading of that column pair whose implied VWAP falls inside the session's own low-to-high range,
on all eleven, and the other three readings fall outside it on all eleven by factors of ten to a
thousand. That module is a top-level module over the panel plane and an edge from `backtest/` to
it would reach DuckDB through `panel/store.py`, so the constant is restated on
`RAW_COVERAGE_ORDER`'s terms and the two are held together by
`tests/unit/backtest/test_factor_tradeability.py::
test_the_turnover_unit_is_the_panel_engines_own_amount_unit` rather than by anyone remembering.

`Decimal` rather than `float` because it multiplies money that is then compared against a
`position_capital` this repository keeps in `Decimal` for `backtest/execution.py`'s reason.
"""

MINIMUM_REBALANCES: Final[int] = 1
"""The floor under `TradeabilitySpec.min_rebalances`, and it is arithmetic rather than taste.

A rebalance is a transition between two consecutive measured periods, so a series of `n` of them
has `n - 1` and one is the first count at which a turnover exists at all. It is a *floor* on the
declaration and not a recommendation: a mean over one rebalance is that rebalance, which is what
`a_turnover_over_a_short_series_is_the_schedule_rather_than_the_factor` records.
"""

MAXIMUM_REBALANCES: Final[int] = 10_000
"""The range check on the declared floor, `MAXIMUM_PORTFOLIO_PERIODS`' bound and for its reason:
roughly forty A-share years of daily rebalances."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TradeabilityLimitation:
    """One named boundary on what a stored tradeability report can be trusted to answer."""

    code: str
    detail: str


KNOWN_TRADEABILITY_LIMITATIONS: Final[tuple[TradeabilityLimitation, ...]] = (
    TradeabilityLimitation(
        code="capacity_is_a_declared_participation_cap_and_not_a_market_impact_model",
        detail=(
            "The whole capacity model is one declared number, TradeabilitySpec.participation_cap, "
            "and everything else is arithmetic on it. It is a CONSTRAINT -- 'this study will not "
            "be more than p of one session's turnover' -- and not an estimate of what trading "
            "that much would cost. No price-impact coefficient is estimated, applied or implied "
            "anywhere: CostSchedule has commission, transfer fee and stamp duty and not one term "
            "that depends on order size, so a reported net return is the same net return at every "
            "capital the capacity permits. This repository holds no dataset an impact model could "
            "be fitted on -- no intraday book, no trade prints, no queue -- and a coefficient "
            "invented here would be a number nobody measured reaching every report that quoted "
            "it. What the cap does buy is falsifiable in the right direction: capital_multiple "
            "below 1.0 says the declared position_capital is already outside the cap, which is a "
            "statement about the declaration rather than about the market."
        ),
    ),
    TradeabilityLimitation(
        code="capacity_is_estimated_on_the_entry_leg_only",
        detail=(
            "One SessionLiquidity per security is asked for and it is the session the position is "
            "OPENED into. The exit is a real trade at a real session and its turnover is not an "
            "input here, so a name that is liquid on the entry day and thin on the exit day is "
            "sized as though it were liquid on both. THE DIRECTION OF THAT ERROR IS NOT CLAIMED, "
            "and the refusal to claim one is deliberate: naming a bias would require knowing how "
            "a security's entry-session turnover relates to its exit-session turnover, and "
            "nothing in this repository measures that joint distribution. A reported capacity is "
            "therefore an estimate on one leg rather than a bound in either direction, and a "
            "report that called it an upper or a lower bound would be asserting a safety property "
            "nobody measured -- which is the shape of every Critical finding this repository has "
            "taken. Asking for both legs was the alternative and was declined because it doubles "
            "the caller's input for a quantity the same declared cap already governs; a caller "
            "who holds the exit session's turnover can run this module twice and compare the two, "
            "which is exactly what accepting any session at or before the entry day leaves open."
        ),
    ),
    TradeabilityLimitation(
        code="the_binding_capacity_of_a_group_is_one_securitys_turnover",
        detail=(
            "V2-P3-006 gives every position the same declared position_capital, so a group is "
            "implementable at that capital only if its LEAST liquid member is: binding_capacity "
            "is a min over the held names and equal_weight_capacity is that one number times the "
            "held count. It is one security's turnover however many the group holds, and no "
            "amount of breadth elsewhere in the group moves it. That is not hidden and not "
            "smoothed: binding_subject names the security, held_count sits beside it, and "
            "concentration -- equal_weight_capacity over liquidity_weighted_capacity -- is how "
            "far that one name sits from the rest. The size of the effect is measured rather than "
            "argued: on two of the eleven real daily rows this repository stores, 000001.SZ's "
            "2026-06-12 session turned over 2,263,042,930.57 yuan and 000569.SZ's 2001-01-02 "
            "session 6,579,577.80, a factor of 344, so a group holding both is capped by the "
            "second at 65,795.78 yuan a position under a 1% cap. No second sample floor is "
            "declared here because QuantilePortfolioSpec.min_securities_per_group already is one, "
            "has no default, and a second place to declare the same thing is a second place for "
            "the two to disagree."
        ),
    ),
    TradeabilityLimitation(
        code="a_rolling_portfolio_is_only_constructible_on_a_non_overlapping_schedule",
        detail=(
            "A rebalance is a transition of a holdings state, and a holdings state only exists "
            "when the periods do not overlap. At horizon h two prediction days one session apart "
            "produce windows sharing h of the h + 1 sessions each spans, which "
            "domain/labels.py::overlapping_windows measures -- five of six at 5d, nine of ten at "
            "10d -- and a name 'carried across' two such windows is a name held twice rather than "
            "a name held once. So consecutive measured periods must satisfy entry_day(k) >= "
            "exit_day(k - 1) and a series that does not is reported as overlapping_schedule. It "
            "is a coverage code rather than an exception because a caller measuring IC daily over "
            "a year is doing something correct for an IC and impossible for a turnover, and the "
            "report should say which. This module invents NO rebalance frequency: the schedule is "
            "the spacing of the as_ofs the caller already offered, and it is measured here rather "
            "than declared, which is the one decision V2-P3-006 explicitly declined to make."
        ),
    ),
    TradeabilityLimitation(
        code="the_rolling_portfolio_is_a_turnover_and_cost_model_and_not_a_return_series",
        detail=(
            "TurnoverSeries publishes counts, two turnover ratios and a cost bracket, and "
            "deliberately publishes NO rolling return, net asset value or equity curve. Two "
            "separate facts forbid it and only one of them is V2-P3-006's. First, a retained "
            "position between exit_day(k - 1) and entry_day(k) is held across sessions no label "
            "window covers, so its return over the gap is not a quantity any contract in this "
            "repository holds -- the label contract measures a declared window and there is "
            "nothing to ask for the space between two of them. Second, even on a schedule with no "
            "gap, chaining the periods that ARE covered is the compounding "
            "KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS.overlapping_periods_are_not_compounded_into_a "
            "_cumulative_curve already refuses. What a holdings state can answer honestly is how "
            "much of itself it replaced and what that saved, and that is what is published."
        ),
    ),
    TradeabilityLimitation(
        code="the_avoided_cost_is_an_upper_bound_on_what_a_rebalance_saves",
        detail=(
            "avoided_cost is the sum of the fee legs V2-P3-006 charged and a rolling portfolio "
            "would not place: for each retained name, period k-1's exit fee and period k's entry "
            "fee, taken from ExecutionResults that already exist. No order is simulated and no "
            "price is invented. What it overstates is the trim: position_quantity is recomputed "
            "against period k's entry close, so a real rolling portfolio holding a name at a "
            "declared position_capital buys or sells the SHARE DIFFERENCE at each rebalance and "
            "pays a fee on it. That fee is not charged here, so avoided_cost is an upper bound on "
            "the saving and rolling_cost -- round_trip_cost minus it -- is a LOWER bound on what "
            "a rolling portfolio pays. The truth lies between rolling_cost and round_trip_cost, "
            "and the second is the number V2-P3-006 already reports, so the pair is a bracket "
            "rather than a replacement. The bound is signed and bounded by the trade it declines "
            "to make: the untraded difference is at most one position, so the missing fee is at "
            "most the fee on one position."
        ),
    ),
    TradeabilityLimitation(
        code="turnover_is_counted_on_names_and_on_money_and_the_two_are_not_the_same_number",
        detail=(
            "name_turnover counts securities and money_turnover counts yuan, both as the "
            "symmetric ratio over the two ends of the rebalance, and they disagree because "
            "V2-P3-006's positions are equal in BUDGET and not in fill. position_quantity floors "
            "the budget to whole lots off STAR, so a position's realised notional is above "
            "position_capital - 100 * close and at or below position_capital: at a 100-yuan close "
            "and a 100,000-yuan budget the truncation reaches 10% of one position. A rebalance "
            "that replaces one expensive, badly-truncated name therefore moves a different "
            "fraction of the money than of the count. Neither is derivable from the other and "
            "neither is the 'real' one; both are published with their four components so a reader "
            "can see which. The denominators are the two ends' own values -- the earlier group's "
            "gross_value and the later group's entry_notional -- so both ratios are in [0, 1] by "
            "construction rather than by convention."
        ),
    ),
    TradeabilityLimitation(
        code="a_turnover_over_a_short_series_is_the_schedule_rather_than_the_factor",
        detail=(
            "A turnover ratio over two groups of n names counts in units of 1 / (from_count + "
            "to_count), so a three-name group's name_turnover has seven attainable values and no "
            "more, and its mean over a handful of rebalances is a statistic about the sample "
            "rather than about the factor. Rebalance.resolution is that unit and is reported "
            "beside every ratio; retained_count, entered_count and exited_count are carried so "
            "the numerator is reconstructible. min_rebalances is declared with no default and its "
            "floor is 1 for the arithmetic reason that a series of n measured periods has n - 1 "
            "transitions. No SECOND sample floor is imposed on the group's width because "
            "QuantilePortfolioSpec.min_securities_per_group already is one and has no default; "
            "declaring 1 there is legal, produces a long group of one name, and makes every "
            "name_turnover here exactly 0.0 or 1.0."
        ),
    ),
    TradeabilityLimitation(
        code="the_liquidity_session_is_the_callers_and_may_be_the_entry_session_itself",
        detail=(
            "Every SessionLiquidity offered for one period must share one trade_date and it must "
            "be at or before the period's entry_day; a later session is refused outright, because "
            "sizing an order on turnover that had not happened when the order was placed is "
            "look-ahead in the sizing. The entry session ITSELF is admitted and is the default a "
            "caller will reach for, and it is worth being plain about what that reading is: the "
            "order fills at that session's close, so the session's own turnover is realised by "
            "the time of the fill, but it was not known when the position was sized. That makes "
            "the entry-day reading a diagnostic -- 'could this have been traded on that day' -- "
            "and a strictly earlier session the point-in-time one -- 'would we have believed it "
            "could'. The two are different questions and this module refuses to choose: "
            "PeriodTradeability carries liquidity_day beside entry_day so a reader can see which "
            "was asked, and a report that quoted a capacity without the pair would be quoting a "
            "number whose meaning it had not shown."
        ),
    ),
    TradeabilityLimitation(
        code="every_rate_here_is_conditioned_on_the_universe_the_caller_offered",
        detail=(
            "The funnel's first denominator is ICCensus.subject_count, which is the securities "
            "the CALLER put into the cross section and not the A-share market. A study run over "
            "one index's constituents reports a value_rate about that index, and a study whose "
            "caller pre-filtered to the names it already had factor values for reports a "
            "value_rate of 1.0 that measures the filter rather than the engine. Nothing in this "
            "module can tell those apart, because ICCrossSection carries a census of what it was "
            "offered and not a claim about what existed. universe_count is therefore reported as "
            "a count beside every rate rather than only as a denominator, and "
            "KNOWN_UNIVERSE_LIMITATIONS.a_listed_only_registry_is_invisible_to_every_downstream"
            "_check is the plane-level version of the same fact: a universe that quietly excluded "
            "the delisted names produces a coverage report that looks better than the market it "
            "claims to describe."
        ),
    ),
)
"""What a stored tradeability report does not answer, as a closed registry rather than as prose.

Every entry is bound to the suite by `tests/unit/test_known_limitation_registries.py`, which
requires each `code` to appear as a string literal in *executable* test code -- the P2 review
measured that a code named only in docstrings can be renamed with the whole suite staying green.
"""

TRADEABILITY_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_TRADEABILITY_LIMITATIONS
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionLiquidity:
    """One security's traded value on one session, in yuan.

    A plain carrier -- `ICObservationPair`'s precedent -- whose invariants are the two that would
    otherwise be silent lies in a capacity: a named subject and a strictly positive turnover. Zero
    is refused rather than admitted as "nothing traded", because a session with no turnover gives
    a capacity of zero for the whole group under the `min`, and that is a statement a caller
    should make by not offering the row (which is `unpriced_holdings`) rather than one this
    contract should manufacture.

    The unit is in the field name for `AMIHUD_60`'s reason: it is load-bearing and invisible
    downstream. Build it with `liquidity_from_amount` rather than converting by hand.
    """

    subject: str
    trade_date: date
    turnover_yuan: Decimal

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise FactorTradeabilityError("a session liquidity must name a subject")
        if self.turnover_yuan <= 0:
            raise FactorTradeabilityError(
                f"{self.subject} reports {self.turnover_yuan} yuan of turnover on "
                f"{self.trade_date.isoformat()}; a capacity is a fraction of a traded value, and "
                "a security that traded nothing has no capacity rather than a capacity of zero"
            )


def liquidity_from_amount(*, subject: str, trade_date: date, amount: float) -> SessionLiquidity:
    """One session's turnover from `daily.amount`, carried from thousands of yuan into yuan.

    `Decimal(str(amount))` and not `Decimal(amount)` for `published_limit_fields`' measured
    reason: `Decimal(0.1)` is the binary double `0.1000000000000000055511151231257827...` carried
    into a type whose whole point is that it does not do that, while `Decimal(str(0.1))` is
    exactly `0.1`. The `amount` is the panel plane's `float` and the capacity is this module's
    money, so something has to bridge them and the obvious spelling is the wrong one.

    Offered as a function rather than left to the caller because the unit is the one thing about
    this input that is easy to get wrong and impossible to notice downstream: a capacity out by a
    factor of 1,000 reaches every report that quotes it, and a rank, a z-score and a ratio of two
    capacities are all unchanged by it.
    """
    return SessionLiquidity(
        subject=subject,
        trade_date=trade_date,
        turnover_yuan=Decimal(str(amount)) * CNY_PER_TURNOVER_UNIT,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageFunnel:
    """Five counts from the universe to the position, and the four rates between them.

    Every step belongs to a different contract and the whole point of publishing five numbers
    instead of one is that the remedies differ. Counts are stored and rates are derived, so there
    is no stored rate that can drift from the counts it came from -- `GroupReturn`'s argument at
    this module's own scale.

    The counts are monotone by construction and this constructor requires it: a funnel whose
    `held_count` exceeds its `scored_count` has lost the direction it is a funnel in.
    """

    universe_count: int
    """`ICCensus.subject_count`: the securities the **caller** offered. See
    `every_rate_here_is_conditioned_on_the_universe_the_caller_offered`."""
    valued_count: int
    """Securities for which the factor produced a number at this tier, whether or not the tier
    admits it. `TIER_VALUE_CODES` is what decides; the two tables differ on `processed`'s
    `imputed` alone."""
    admissible_count: int
    """Securities whose value the tier admits (`TIER_ADMITTED_CODES`), before any label is
    asked for."""
    scored_count: int
    """`ICCensus.admitted_count`: admissible **and** labelled, which is the set an IC is measured
    over and the set a portfolio is offered."""
    held_count: int
    """`PortfolioCensus.held_count`: both legs filled."""

    def __post_init__(self) -> None:
        counts = (
            ("universe_count", self.universe_count),
            ("valued_count", self.valued_count),
            ("admissible_count", self.admissible_count),
            ("scored_count", self.scored_count),
            ("held_count", self.held_count),
        )
        for name, value in counts:
            if value < 0:
                raise FactorTradeabilityError(f"{name} cannot be negative")
        for (wider, outer), (narrower, inner) in pairwise(counts):
            if inner > outer:
                raise FactorTradeabilityError(
                    f"{narrower} is {inner} against {wider} {outer}; every security that reaches "
                    "a stage reached the one before it, and a funnel that widens has lost the "
                    "direction it is a funnel in"
                )

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float | None:
        """`None` rather than a zero on an empty denominator: a rate over nothing does not exist,
        and a zero would read as "none of them made it" -- the opposite claim."""
        if denominator == 0:
            return None
        return numerator / denominator

    @property
    def value_rate(self) -> float | None:
        """The **factor engine's** own coverage: how much of the offered universe it scored."""
        return self._rate(self.valued_count, self.universe_count)

    @property
    def admission_rate(self) -> float | None:
        """The **tier's** own policy: how much of what it valued it also admits.

        Exactly `1.0` on the raw and neutralised tiers, whose value and admitted vocabularies are
        the same frozenset, and below it on `processed` exactly when an `imputed` value is
        present. That is one cell of one table and it is the separable fixture."""
        return self._rate(self.admissible_count, self.valued_count)

    @property
    def label_rate(self) -> float | None:
        """**`domain/labels.py`'s** verdict: how much of the admissible set could be labelled at
        all. Halts, limit locks, unpublished bands and the three registry verdicts land here."""
        return self._rate(self.scored_count, self.admissible_count)

    @property
    def execution_rate(self) -> float | None:
        """**`AShareExecutionPolicy`'s** verdict: how much of the scored set became a position."""
        return self._rate(self.held_count, self.scored_count)

    @property
    def implementable_rate(self) -> float | None:
        """`held / universe`: the whole funnel in one number.

        The product of the four rates above, to within the rounding of four divisions -- computed
        directly rather than multiplied, so the four can each be `None` without this one being.
        `tests/unit/backtest/test_factor_tradeability.py::
        test_the_implementable_rate_is_the_product_of_the_four_stages` measures the agreement.
        """
        return self._rate(self.held_count, self.universe_count)


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupCoverage:
    """One quantile's own execution funnel: offered by the cut, and how many survived it.

    The per-group table is the acceptance criterion's instrument. A factor whose long group is the
    set of names the market refuses reports a high IC and a `held_count` far below its
    `scored_count`, and nothing above the group level can see that -- the period's overall
    `execution_rate` averages the refused group with the ones that filled.

    `excluded_by_outcome` carries **every** non-held outcome in `HOLDING_OUTCOME_ORDER`, including
    the ones that did not occur, for `PortfolioCensus.excluded_by_outcome`'s reason: a code missing
    from the tuple and a code present with a zero are different claims.
    """

    group: int
    scored_count: int
    """How many of the cross section's pairs the cut assigned to this group."""
    held_count: int
    excluded_by_outcome: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if self.group < 0:
            raise FactorTradeabilityError(f"a group coverage is filed under group {self.group}")
        if self.scored_count <= 0:
            raise FactorTradeabilityError(
                f"group {self.group} was offered {self.scored_count} securities; a group the cut "
                "never filled is a coverage code on the period and not a row in this table"
            )
        if self.held_count < 0:
            raise FactorTradeabilityError("held_count cannot be negative")
        expected = EXCLUDED_OUTCOME_ORDER
        if tuple(code for code, _count in self.excluded_by_outcome) != expected:
            raise FactorTradeabilityError(
                f"excluded_by_outcome is keyed by "
                f"{[code for code, _count in self.excluded_by_outcome]} and the declared "
                f"exclusions are {list(expected)} in that order; a table missing a code cannot be "
                "told from one whose count is zero"
            )
        if any(count < 0 for _code, count in self.excluded_by_outcome):
            raise FactorTradeabilityError("an excluded-outcome count cannot be negative")
        total = self.held_count + sum(count for _code, count in self.excluded_by_outcome)
        if total != self.scored_count:
            raise FactorTradeabilityError(
                f"group {self.group} accounts for {total} securities and the cut assigned it "
                f"{self.scored_count}; every assigned security is held or excluded under exactly "
                "one outcome, and a group that does not add up has lost one of them"
            )

    @property
    def execution_rate(self) -> float:
        """`held / scored` within this group. A plain `float` and not `float | None`, because
        `scored_count` is required positive above -- a `None` branch here would be one no input
        can reach, and this repository has measured what an unreachable branch is worth."""
        return self.held_count / self.scored_count


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupCapacity:
    """How much money one group could have absorbed at the declared participation cap.

    Two facts and everything else derived: the binding name's own capacity and the group's total.
    Every reading a report wants is a property over those, so no stored number can drift from the
    turnover it came from.

    **This is a model and its whole judgement is one declared number.** See this module's docstring
    for the five assumptions and `capacity_is_a_declared_participation_cap_and_not_a_market_impact
    _model` for the one that is easiest to mistake for a measurement.
    """

    group: int
    held_count: int
    binding_subject: str
    """The least liquid held name: the one security whose turnover sets the whole group's
    capacity under `V2-P3-006`'s equal-budget sizing."""
    binding_capacity: Decimal
    """`participation_cap * turnover_yuan` of `binding_subject`, in yuan -- the largest position
    this study may take in the name that binds."""
    total_capacity: Decimal
    """`participation_cap * sum(turnover_yuan)` over the held names, in yuan."""
    position_capital: Decimal
    """The declared budget per position this capacity is being compared against, carried so the
    ratio below cannot be recomputed against a different one."""

    def __post_init__(self) -> None:
        if self.group < 0:
            raise FactorTradeabilityError(f"a group capacity is filed under group {self.group}")
        if self.held_count <= 0:
            raise FactorTradeabilityError(
                f"group {self.group} holds {self.held_count} positions; a group that holds "
                "nothing has no capacity rather than a capacity of zero, which is the "
                "no_holdings code"
            )
        if not self.binding_subject.strip():
            raise FactorTradeabilityError("a group capacity must name the security that binds it")
        if self.binding_capacity <= 0 or self.position_capital <= 0:
            raise FactorTradeabilityError(
                f"group {self.group} reports a binding capacity of {self.binding_capacity} "
                f"against a position capital of {self.position_capital}; both are money and both "
                "are positive"
            )
        if self.total_capacity < self.binding_capacity * self.held_count:
            raise FactorTradeabilityError(
                f"group {self.group} totals {self.total_capacity} against {self.held_count} "
                f"positions of at least {self.binding_capacity}; the binding capacity is a "
                "minimum over the same names the total sums, so the total cannot be below the "
                "product"
            )

    @property
    def equal_weight_capacity(self) -> Decimal:
        """What the whole group absorbs under `V2-P3-006`'s equal-budget sizing: the binding
        name's capacity in every position."""
        return self.binding_capacity * self.held_count

    @property
    def liquidity_weighted_capacity(self) -> Decimal:
        """What the same names would absorb if the budget followed liquidity. **Nothing in this
        repository builds that portfolio**; it is published as the bound the equal-budget reading
        sits under, and the gap between the two is `concentration`."""
        return self.total_capacity

    @property
    def concentration(self) -> float:
        """`equal_weight_capacity / liquidity_weighted_capacity`, in `(0, 1]`.

        `1.0` when every held name has the same turnover and approaching zero as one name gets
        thinner than the rest. It is how much of the group's capacity the equal-budget constraint
        is destroying, which is a different question from how much capacity there is.
        """
        return float(self.equal_weight_capacity) / float(self.liquidity_weighted_capacity)

    @property
    def capital_multiple(self) -> float:
        """`binding_capacity / position_capital`: the headline, and it is falsifiable.

        **Below `1.0` means the study as declared is already over capacity** -- the binding name
        cannot take one position of the size the spec declares, so the returns `V2-P3-006`
        reported are returns of a portfolio nobody could have opened at that size. Measured on
        real rows: `000569.SZ`'s 2001-01-02 session turned over ¥6,579,577.80, so at a 1% cap it
        absorbs ¥65,795.78 and a ¥100,000 position is a multiple of **0.658**.
        """
        return float(self.binding_capacity) / float(self.position_capital)


@dataclass(frozen=True, slots=True, kw_only=True)
class PeriodTradeability:
    """One `as_of`'s coverage funnel, per-group execution and long-group capacity.

    Built by `TradeabilityStudy.measure`; this constructor re-derives none of the arithmetic,
    following `PeriodPortfolio`'s precedent. What it does enforce is the relationship between the
    three coverage codes it carries and the tables beside them, so a report that claimed a
    capacity under a code that placed no order is not constructible.
    """

    as_of: datetime
    tier: FactorTier
    factor_id: str
    direction: FactorDirection
    group_count: int
    period_coverage: PeriodCoverage
    """`V2-P3-006`'s own code for this period, carried rather than re-derived.

    It is what tells `no_holdings` apart into its two meanings: under one of
    `PRE_EXECUTION_COVERAGE` no order was ever placed, and under `unfillable_after_execution` the
    orders were placed and the market refused them. The remedies are not the same.
    """
    entry_day: date
    liquidity_day: date | None
    """The session every `SessionLiquidity` was dated on, or `None` when none was offered.

    Carried beside `entry_day` rather than folded into it: the two being equal is the diagnostic
    reading and the two differing is the point-in-time one, and a capacity quoted without the pair
    is a number whose meaning has not been shown. See
    `the_liquidity_session_is_the_callers_and_may_be_the_entry_session_itself`.
    """
    funnel: CoverageFunnel
    by_group: tuple[GroupCoverage, ...]
    """One row per group, ascending in the **raw** factor value, or `()`.

    Empty under exactly the three `PRE_EXECUTION_COVERAGE` codes, where the cut never happened
    and there is nothing to decompose. Present under `measured` **and** under
    `unfillable_after_execution`, which is the whole reason the counts are derived from the
    rejections rather than read off `PeriodPortfolio.groups`.
    """
    capacity_coverage: CapacityCoverage
    capacity: GroupCapacity | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", ensure_aware(self.as_of))
        if self.tier not in FACTOR_TIERS:
            raise FactorTradeabilityError(
                f"{self.tier!r} is not a declared tier; expected one of {sorted(FACTOR_TIERS)}"
            )
        if self.capacity_coverage not in CAPACITY_COVERAGE_CODES:
            raise FactorTradeabilityError(
                f"{self.capacity_coverage!r} is not a declared capacity code; expected one of "
                f"{sorted(CAPACITY_COVERAGE_CODES)}"
            )
        if (self.capacity is None) == (self.capacity_coverage == "measured"):
            raise FactorTradeabilityError(
                f"capacity coverage {self.capacity_coverage!r} carries "
                f"{'a capacity' if self.capacity is not None else 'none'}; exactly the 'measured' "
                "code carries one, and a refusal beside a sized group would be a capacity for a "
                "portfolio the report has just said it could not size"
            )
        attempted = self.period_coverage not in PRE_EXECUTION_COVERAGE
        indices = tuple(row.group for row in self.by_group)
        if attempted != (indices == tuple(range(self.group_count))):
            raise FactorTradeabilityError(
                f"period coverage {self.period_coverage!r} carries groups {list(indices)} against "
                f"a cut of {self.group_count}; exactly the codes outside "
                f"{sorted(PRE_EXECUTION_COVERAGE)} placed orders and therefore carry one row per "
                "group in ascending order, and the three that did not carry none"
            )
        if self.capacity is not None and self.capacity.group != self.top_group_index:
            raise FactorTradeabilityError(
                f"the capacity is filed under group {self.capacity.group} and this factor's "
                f"declared direction makes group {self.top_group_index} the long one; a capacity "
                "for a group nobody would hold is a size for a trade that was never going to be "
                "placed"
            )
        grouped = sum(row.held_count for row in self.by_group)
        if self.by_group and grouped != self.funnel.held_count:
            raise FactorTradeabilityError(
                f"the group table holds {grouped} positions and the funnel reports "
                f"{self.funnel.held_count}; the two are the same positions counted two ways and "
                "cannot be a second source of truth for each other"
            )
        if self.liquidity_day is not None and self.liquidity_day > self.entry_day:
            raise FactorTradeabilityError(
                f"this report carries a liquidity session of {self.liquidity_day.isoformat()} "
                f"against an entry session of {self.entry_day.isoformat()}; a capacity sized on "
                "turnover that had not happened when the order was placed is a look-ahead this "
                "contract will not render"
            )

    @property
    def top_group_index(self) -> int:
        """The group this factor's declaration says should perform best, and the only one this
        repository can hold. `PeriodPortfolio.top_group_index`'s rule, unchanged."""
        return self.group_count - 1 if self.direction == "higher_is_better" else 0

    @property
    def top_group_execution_rate(self) -> float | None:
        """What fraction of the long group's assigned names became positions."""
        if not self.by_group:
            return None
        return self.by_group[self.top_group_index].execution_rate

    @property
    def top_group_execution_shortfall(self) -> float | None:
        """**The acceptance criterion in one number.** The period's overall execution rate less
        the long group's own.

        Positive means the group this factor asks a portfolio to hold is harder to trade than the
        cross section it was picked from, which is exactly the shape of a signal that scores well
        and cannot be implemented. Zero means the long group is no worse than the market it came
        out of, and negative means it is easier to trade.

        `None` when no order was placed. It is a **difference and not a ratio**, so a group whose
        execution rate is zero is representable rather than dividing by it.
        """
        overall = self.funnel.execution_rate
        top = self.top_group_execution_rate
        if overall is None or top is None:
            return None
        return overall - top


@dataclass(frozen=True, slots=True, kw_only=True)
class Rebalance:
    """One transition of the long group's holdings between two consecutive measured periods.

    Four counts, four money totals and nothing derived stored. Both turnover ratios and the
    resolution are properties, so a stored ratio cannot disagree with the counts under it --
    `GroupReturn`'s argument, and it matters more here because the two ratios are *deliberately*
    different numbers and a reader has to be able to see which components made them differ.
    """

    from_as_of: datetime
    to_as_of: datetime
    group: int
    retained_count: int
    entered_count: int
    exited_count: int
    avoided_cost: Decimal
    """The fee legs `V2-P3-006` charged and a rolling portfolio would not place: for each retained
    name, the earlier period's exit fee plus the later period's entry fee. An **upper** bound on
    the saving; see `the_avoided_cost_is_an_upper_bound_on_what_a_rebalance_saves`."""
    sold_value: Decimal
    """The exited names' `gross_value` at the earlier period's exit."""
    bought_value: Decimal
    """The entered names' `entry_notional` at the later period's entry."""
    from_value: Decimal
    """The whole earlier group's `gross_value`."""
    to_value: Decimal
    """The whole later group's `entry_notional`."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "from_as_of", ensure_aware(self.from_as_of))
        object.__setattr__(self, "to_as_of", ensure_aware(self.to_as_of))
        if self.group < 0:
            raise FactorTradeabilityError(f"a rebalance is filed under group {self.group}")
        if self.from_as_of >= self.to_as_of:
            raise FactorTradeabilityError(
                f"a rebalance runs from {self.from_as_of.isoformat()} to "
                f"{self.to_as_of.isoformat()}; a transition into a period at or before the one it "
                "leaves is not a transition"
            )
        for name in ("retained_count", "entered_count", "exited_count"):
            if int(getattr(self, name)) < 0:
                raise FactorTradeabilityError(f"{name} cannot be negative")
        for name in ("avoided_cost", "sold_value", "bought_value"):
            if Decimal(getattr(self, name)) < 0:
                raise FactorTradeabilityError(f"{name} cannot be negative")
        if self.from_count <= 0 or self.to_count <= 0:
            raise FactorTradeabilityError(
                f"a rebalance runs between groups of {self.from_count} and {self.to_count} "
                "names; a measured period's group is never empty, so an end with no name is a "
                "transition to or from a portfolio that does not exist"
            )
        if self.from_value <= 0 or self.to_value <= 0:
            raise FactorTradeabilityError(
                f"a rebalance runs between portfolios worth {self.from_value} and {self.to_value}; "
                "a group holding a position is worth money, and a zero denominator makes the "
                "money turnover undefined rather than total"
            )
        if self.sold_value > self.from_value or self.bought_value > self.to_value:
            raise FactorTradeabilityError(
                f"a rebalance sells {self.sold_value} of {self.from_value} and buys "
                f"{self.bought_value} of {self.to_value}; the traded side is a subset of the "
                "portfolio it is a subset of, and a ratio above one is not a turnover"
            )
        if self.retained_count == 0 and self.avoided_cost != 0:
            raise FactorTradeabilityError(
                f"a rebalance retaining nothing avoided {self.avoided_cost}; every avoided fee "
                "is a leg some retained name did not have to trade"
            )
        if self.entered_count == 0 and self.bought_value != 0:
            raise FactorTradeabilityError("a rebalance entering nothing bought nothing")
        if self.exited_count == 0 and self.sold_value != 0:
            raise FactorTradeabilityError("a rebalance exiting nothing sold nothing")

    @property
    def from_count(self) -> int:
        """How many names the earlier period's long group held."""
        return self.retained_count + self.exited_count

    @property
    def to_count(self) -> int:
        """How many names the later period's long group held."""
        return self.retained_count + self.entered_count

    @property
    def name_turnover(self) -> float:
        """`(entered + exited) / (from_count + to_count)`, in `[0, 1]`.

        The symmetric reading over both ends rather than `entered / to_count`, because the two
        ends are not the same size: the market empties the long group by different amounts on
        different days, and a one-sided denominator reports a portfolio that merely shrank as one
        that turned over. `0.0` exactly when the two groups hold the same names and `1.0` exactly
        when they share none.
        """
        return (self.entered_count + self.exited_count) / (self.from_count + self.to_count)

    @property
    def money_turnover(self) -> float:
        """`(sold + bought) / (from_value + to_value)`, in `[0, 1]`.

        Not derivable from `name_turnover` and not the same number: `V2-P3-006`'s positions are
        equal in **budget** and not in fill, because `position_quantity` floors the budget to
        whole lots. See
        `turnover_is_counted_on_names_and_on_money_and_the_two_are_not_the_same_number`.
        """
        traded = self.sold_value + self.bought_value
        return float(traded) / float(self.from_value + self.to_value)

    @property
    def resolution(self) -> float:
        """`1 / (from_count + to_count)`: the unit `name_turnover` counts in.

        The numerator is an integer count of securities, so the ratio is an integer multiple of
        this. Reported so that a turnover over a narrow group is read as the coarse statistic it
        is rather than as a precise one -- a three-name group against a three-name group resolves
        to sixths and has seven attainable values in all.
        """
        return 1.0 / (self.from_count + self.to_count)


class TradeabilitySpec(BaseModel):
    """The declared policy a tradeability report applies: how much of a session, and how many
    rebalances before a turnover is a summary.

    Two fields and **neither has a default**, for `QuantilePortfolioSpec`'s reason. Between them
    they are the entire modelling surface of this module: `participation_cap` is the one
    judgement the capacity rests on and everything else about a capacity is arithmetic on it, and
    `min_rebalances` is the floor under a statistic this repository has taken eight Critical
    findings on the narrow-sample version of.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    participation_cap: Decimal = Field(gt=0, le=1)
    """The largest fraction of one session's turnover this study is willing to be.

    A constraint and not an impact estimate: nothing here prices what trading that fraction would
    cost, because `CostSchedule` has no size term and this repository holds no dataset an impact
    coefficient could be fitted on. `le=1` because a study that is more than the whole session's
    turnover is not a study that traded at that session's prices.
    """
    min_rebalances: int = Field(ge=MINIMUM_REBALANCES, le=MAXIMUM_REBALANCES)
    """The fewest transitions a series needs before this study calls a turnover a summary."""


class TurnoverSeries(BaseModel):
    """A rolling portfolio's holdings churn over a series of periods, and what it saved.

    A pydantic model rather than a dataclass, unlike the per-rebalance carrier above, and
    `QuantilePortfolioSummary`'s cost argument decides it the same way: there is one of these per
    study rather than one per transition, so what is wanted at that scale is the validator.

    **No return, no curve, no annualisation** -- see
    `the_rolling_portfolio_is_a_turnover_and_cost_model_and_not_a_return_series`. What is
    published is how much of itself the portfolio replaced and the bracket that replacement puts
    around `V2-P3-006`'s fee.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tier: FactorTier
    factor_id: str = Field(min_length=1, max_length=64)
    direction: FactorDirection
    group_count: int = Field(ge=MINIMUM_PORTFOLIO_GROUPS)
    horizon_sessions: int = Field(ge=1)
    group: int = Field(ge=0)
    """The long group this series follows, decided by `direction` rather than by a parameter."""
    coverage: TurnoverCoverage
    as_ofs: tuple[datetime, ...]
    """Every `as_of` offered, ascending -- not only the measured ones, for `ICSummary.as_ofs`'
    reason: two series over disjoint years share a count and nothing else."""
    measured_count: int = Field(ge=0)
    rebalances: tuple[Rebalance, ...]
    mean_name_turnover: float | None
    mean_money_turnover: float | None
    round_trip_cost: Decimal = Field(ge=0)
    """What `V2-P3-006` charged the long group across the measured periods, both legs. The
    **upper** bound of the bracket: it is the fee of a portfolio that sells everything every
    period."""
    avoided_cost: Decimal = Field(ge=0)
    """The sum of the rebalances' avoided legs. Each leg is avoidable at most once across a
    series -- a period's entry leg belongs to the rebalance into it and its exit leg to the
    rebalance out of it -- so this cannot exceed `round_trip_cost`, which is required below rather
    than assumed."""

    @field_validator("mean_name_turnover", "mean_money_turnover")
    @classmethod
    def refuse_a_non_finite_statistic(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{value!r} is not a finite statistic")
        return value

    @model_validator(mode="after")
    def validate_the_statistics_match_the_coverage(self) -> Self:
        measured = self.coverage == "measured"
        for name in ("mean_name_turnover", "mean_money_turnover"):
            if (getattr(self, name) is None) == measured:
                raise ValueError(
                    f"coverage {self.coverage!r} carries {name} {getattr(self, name)!r}; exactly "
                    "the 'measured' code carries the statistics"
                )
        if measured != bool(self.rebalances):
            raise ValueError(
                f"coverage {self.coverage!r} carries {len(self.rebalances)} rebalance(s); exactly "
                "the 'measured' code carries them, and a refusal beside a partial set would be a "
                "mean over transitions the series has just said it could not follow"
            )
        if self.measured_count > len(self.as_ofs):
            raise ValueError(
                f"{self.measured_count} periods were measured and {len(self.as_ofs)} as_ofs were "
                "offered; a series cannot measure an as_of it was not given"
            )
        if measured and len(self.rebalances) != self.measured_count - 1:
            raise ValueError(
                f"{len(self.rebalances)} rebalance(s) were followed across {self.measured_count} "
                "measured period(s); a rolling portfolio transitions once between each "
                "consecutive pair, so a measured series has exactly one fewer transition than it "
                "has periods"
            )
        if any(item.group != self.group for item in self.rebalances):
            raise ValueError(
                f"a rebalance is filed under another group than the series' own {self.group}; one "
                "series follows one portfolio"
            )
        if sum((item.avoided_cost for item in self.rebalances), _ZERO) != self.avoided_cost:
            raise ValueError(
                "avoided_cost is not the sum of the rebalances' own; the total is the arithmetic "
                "over the same legs and cannot be a second source of truth for them"
            )
        if self.avoided_cost > self.round_trip_cost:
            raise ValueError(
                f"a rolling portfolio avoided {self.avoided_cost} of a {self.round_trip_cost} fee; "
                "every avoided leg is one of the legs the round-trip total already counted, and "
                "each is avoidable at most once, so the saving cannot exceed the fee"
            )
        if len(set(self.as_ofs)) != len(self.as_ofs) or list(self.as_ofs) != sorted(self.as_ofs):
            raise ValueError(
                "as_ofs must be distinct and ascending; a repeated as_of is one period counted "
                "twice, and an unordered tuple makes two identical series compare unequal"
            )
        return self

    @property
    def rolling_cost(self) -> Decimal:
        """`round_trip_cost - avoided_cost`: a **lower** bound on what a rolling portfolio pays.

        Lower rather than exact because a retained position's share count is recomputed against
        each period's entry close, so a real rolling portfolio trims to the declared capital and
        pays a fee on the difference that is not charged here. The truth lies between this and
        `round_trip_cost`, and `round_trip_cost` is the number `V2-P3-006` already reports -- so
        the pair is the bracket that limitation asked for rather than a replacement for it.
        """
        return self.round_trip_cost - self.avoided_cost

    @property
    def cost_reduction(self) -> float | None:
        """`avoided_cost / round_trip_cost`: at most this fraction of `V2-P3-006`'s fee is
        recoverable by rolling. `None` when the fee is zero, which a `CostSchedule` of zero rates
        makes constructible."""
        if self.round_trip_cost == 0:
            return None
        return float(self.avoided_cost) / float(self.round_trip_cost)


class TradeabilityStudy:
    """Measure one period's coverage and capacity, and follow a series' rolling turnover.

    A class holding a `TradeabilitySpec` and the `QuantilePortfolioSpec` that produced the periods,
    so the declared participation cap and -- above all -- the `position_capital` every capacity is
    compared against are fixed once for a study instead of being passed at each call with a chance
    to differ. `QuantilePortfolioStudy`'s precedent.

    **The quantile spec is a required argument and is not defaulted.** It carries the capital, the
    cut and the factor whose `direction` decides which group is long, and every one of those moves
    the numbers reported here; `V2-P3-005` established that a decision which moves the answers is
    a decision the caller records making.
    """

    def __init__(self, spec: TradeabilitySpec, *, portfolio: QuantilePortfolioSpec) -> None:
        self._spec = spec
        self._portfolio = portfolio

    @property
    def spec(self) -> TradeabilitySpec:
        return self._spec

    @property
    def portfolio(self) -> QuantilePortfolioSpec:
        return self._portfolio

    def measure(
        self,
        period: PeriodPortfolio,
        *,
        cross_section: ICCrossSection,
        liquidity: Mapping[str, SessionLiquidity],
    ) -> PeriodTradeability:
        """One `as_of`'s coverage funnel, per-group execution rates and long-group capacity.

        `period` is `V2-P3-006`'s own and `cross_section` is the **same** one that produced it --
        `QuantilePortfolioStudy.measure`'s argument, kept by the caller. Both are required because
        neither alone carries the join: the period knows which securities were rejected and not
        which group each was assigned to, and the cross section knows the scores and nothing about
        the orders. `_refuse_a_cross_section_that_is_not_this_periods` checks the pairing on the
        census the period already carries rather than trusting the caller.

        `liquidity` is `subject -> SessionLiquidity`, keyed by **`cross_section.pairs`** for
        `QuantilePortfolioStudy.measure`'s reason -- a key with no admitted pair is refused rather
        than ignored, because a caller who read its two sides from two different universes wants
        to be told. A missing key is a different matter: it is `unpriced_holdings` if the security
        was held and nothing at all if it was not.

        Never raises for a property of the market. A period the cut refused, one the market
        emptied and a group whose turnover the caller did not supply are all answers.
        """
        self._refuse_a_period_that_is_not_this_study(period)
        self._refuse_a_cross_section_that_is_not_this_periods(period, cross_section)
        liquidity_day = self._refuse_liquidity_that_is_not_this_periods(
            period, cross_section, liquidity
        )

        attempted = period.coverage not in PRE_EXECUTION_COVERAGE
        by_group: tuple[GroupCoverage, ...] = ()
        held_subjects: tuple[tuple[str, ...], ...] = ()
        if attempted:
            by_group, held_subjects = self._decompose(period, cross_section)

        capacity_coverage: CapacityCoverage = "no_holdings"
        capacity: GroupCapacity | None = None
        top = period.top_group_index
        if held_subjects and held_subjects[top]:
            capacity_coverage, capacity = self._size(top, held_subjects[top], liquidity)

        return PeriodTradeability(
            as_of=period.as_of,
            tier=period.tier,
            factor_id=period.factor_id,
            direction=period.direction,
            group_count=period.group_count,
            period_coverage=period.coverage,
            entry_day=period.entry_day,
            liquidity_day=liquidity_day,
            funnel=_funnel(period),
            by_group=by_group,
            capacity_coverage=capacity_coverage,
            capacity=capacity,
        )

    def turnover(self, periods: Iterable[PeriodPortfolio]) -> TurnoverSeries:
        """The long group's rolling holdings churn across a series, or the code that says why not.

        The rebalance schedule is **not** a parameter: it is the spacing of the `as_of`s offered
        here, and this method measures it. What it refuses is a schedule on which a holdings state
        does not exist -- see
        `a_rolling_portfolio_is_only_constructible_on_a_non_overlapping_schedule`.

        Non-`measured` periods are not transitions and are not zeros. They are counted by
        `measured_count` against `len(as_ofs)` and dropped, which is `QuantilePortfolioSummary`'s
        treatment of the same fact: a period the market emptied did not turn its portfolio over,
        it had no portfolio.
        """
        ordered = sorted(periods, key=lambda period: period.as_of)
        if not ordered:
            raise FactorTradeabilityError(
                "a turnover series needs at least one period; an empty series satisfies every "
                "per-period check vacuously and would report a coverage code about nothing"
            )
        for period in ordered:
            self._refuse_a_period_that_is_not_this_study(period)
        self._refuse_periods_that_are_not_one_study(ordered)

        head = ordered[0]
        as_ofs = tuple(period.as_of for period in ordered)
        measured = [period for period in ordered if period.coverage == "measured"]
        group = head.top_group_index
        round_trip = sum(
            (
                period.groups[group].entry_cost + period.groups[group].exit_cost
                for period in measured
            ),
            _ZERO,
        )

        coverage: TurnoverCoverage = "measured"
        rebalances: tuple[Rebalance, ...] = ()
        if any(later.entry_day < earlier.exit_day for earlier, later in pairwise(measured)):
            coverage = "overlapping_schedule"
        elif len(measured) - 1 < self._spec.min_rebalances:
            coverage = "insufficient_rebalances"
        else:
            rebalances = tuple(
                _rebalance(earlier, later, group=group) for earlier, later in pairwise(measured)
            )
        return TurnoverSeries(
            tier=head.tier,
            factor_id=head.factor_id,
            direction=head.direction,
            group_count=head.group_count,
            horizon_sessions=head.horizon_sessions,
            group=group,
            coverage=coverage,
            as_ofs=as_ofs,
            measured_count=len(measured),
            rebalances=rebalances,
            mean_name_turnover=(
                None
                if not rebalances
                else math.fsum(item.name_turnover for item in rebalances) / len(rebalances)
            ),
            mean_money_turnover=(
                None
                if not rebalances
                else math.fsum(item.money_turnover for item in rebalances) / len(rebalances)
            ),
            round_trip_cost=round_trip,
            avoided_cost=sum((item.avoided_cost for item in rebalances), _ZERO),
        )

    def _decompose(
        self, period: PeriodPortfolio, cross_section: ICCrossSection
    ) -> tuple[tuple[GroupCoverage, ...], tuple[tuple[str, ...], ...]]:
        """Per-group execution counts, derived from the cut and the rejections rather than read.

        **Assigned minus rejected, and that is the load-bearing choice of this method.** Under
        `unfillable_after_execution` -- the code `V2-P3-006` says this issue exists to surface --
        `PeriodPortfolio.groups` is empty and the per-security verdicts are all that survives, so
        a decomposition that read the group tables would be blind on exactly the period a reader
        opened the report for. Under `measured` both readings exist and this method requires them
        to agree, which is what makes the derivation checkable rather than merely convenient.
        """
        assignment = rank_groups(cross_section.scores, group_count=period.group_count)
        outcomes = {item.subject: item.outcome for item in period.rejections}
        scored = [0] * period.group_count
        held: list[list[str]] = [[] for _ in range(period.group_count)]
        excluded: list[dict[str, int]] = [
            dict.fromkeys(EXCLUDED_OUTCOME_ORDER, 0) for _ in range(period.group_count)
        ]
        for pair, group in zip(cross_section.pairs, assignment, strict=True):
            scored[group] += 1
            outcome = outcomes.get(pair.subject)
            if outcome is None:
                held[group].append(pair.subject)
            else:
                excluded[group][outcome] += 1
        if period.coverage == "measured":
            for row in period.groups:
                filed = sorted(item.subject for item in row.holdings)
                if filed != sorted(held[row.group]):
                    raise FactorTradeabilityError(
                        f"group {row.group} holds {filed} in the period and the cut re-derived "
                        f"{sorted(held[row.group])}; the group table and the rejection list are "
                        "the same securities counted two ways, and a cross section that produces "
                        "a different cut is not the one this period was measured from"
                    )
        return (
            tuple(
                GroupCoverage(
                    group=index,
                    scored_count=scored[index],
                    held_count=len(held[index]),
                    excluded_by_outcome=tuple(excluded[index].items()),
                )
                for index in range(period.group_count)
            ),
            tuple(tuple(names) for names in held),
        )

    def _size(
        self,
        group: int,
        subjects: Sequence[str],
        liquidity: Mapping[str, SessionLiquidity],
    ) -> tuple[CapacityCoverage, GroupCapacity | None]:
        """The long group's capacity at the declared cap, or the code that says why there is none.

        The `min` is over **every** held name and an unpriced one refuses the whole group rather
        than being skipped. That is `panel_factors._amihud_60`'s decision arriving at this plane:
        a capacity taken over whichever names happened to have a turnover row is a number whose
        sample size is a function of the data. And the omission is not neutral in the way a
        missing observation usually is -- a security with no turnover on a session is one that did
        not trade it, which is the end of the distribution this `min` exists to find.
        """
        turnovers = [liquidity.get(subject) for subject in subjects]
        if any(item is None for item in turnovers):
            return "unpriced_holdings", None
        priced = [item for item in turnovers if item is not None]
        binding = min(priced, key=lambda item: (item.turnover_yuan, item.subject))
        cap = self._spec.participation_cap
        return "measured", GroupCapacity(
            group=group,
            held_count=len(priced),
            binding_subject=binding.subject,
            binding_capacity=binding.turnover_yuan * cap,
            total_capacity=sum((item.turnover_yuan for item in priced), _ZERO) * cap,
            position_capital=self._portfolio.position_capital,
        )

    def _refuse_a_period_that_is_not_this_study(self, period: PeriodPortfolio) -> None:
        """Refuse a period measured under another quantile spec than the one declared here.

        The capacity is compared against `position_capital` and the long group is chosen by
        `direction`, so a period from another study would be sized against a budget it was not
        built with -- a `capital_multiple` computed from two different declarations, which is the
        one number in this module that is a statement about a declaration rather than a market.
        """
        mismatched = [
            f"{name}={getattr(period, name)!r} against {expected!r}"
            for name, expected in (
                ("factor_id", self._portfolio.factor_id),
                ("direction", self._portfolio.direction),
                ("group_count", self._portfolio.group_count),
            )
            if getattr(period, name) != expected
        ]
        if mismatched:
            raise FactorTradeabilityError(
                f"the period at {period.as_of.isoformat()} reports {mismatched}; a tradeability "
                "report is measured against the quantile spec that produced the period, and a "
                "capacity compared against another study's declared capital is a ratio of two "
                "different declarations"
            )

    @staticmethod
    def _refuse_a_cross_section_that_is_not_this_periods(
        period: PeriodPortfolio, cross_section: ICCrossSection
    ) -> None:
        """Refuse a cross section that did not produce this period.

        `PeriodPortfolio` carries the cross section's own `ICCensus` as `source_census`, so the
        pairing is checkable rather than trusted: a census is a tier plus four counts plus one
        cell per excluded code, and two different cross sections at one `as_of` would have to
        agree on all of it. The rejection subjects are checked against the pairs as well, because
        the census is a count and a count cannot say that these are the same securities.
        """
        for name, mine, theirs in (
            ("as_of", period.as_of, cross_section.as_of),
            ("tier", period.tier, cross_section.tier),
            ("entry_day", period.entry_day, cross_section.entry_day),
            ("exit_day", period.exit_day, cross_section.exit_day),
        ):
            if mine != theirs:
                raise FactorTradeabilityError(
                    f"the period reports {name}={mine!r} and the cross section {theirs!r}; a "
                    "coverage funnel joins one period to the cross section it was measured from, "
                    "and a report built from two of them decomposes one period's rejections "
                    "against another's cut"
                )
        if period.source_census != cross_section.census:
            raise FactorTradeabilityError(
                "the period's source census and the cross section's own do not agree; the period "
                "carries the census it was built from, so a mismatch means this is not that cross "
                "section however well the stamps line up"
            )
        subjects = {pair.subject for pair in cross_section.pairs}
        stray = sorted({item.subject for item in period.rejections} - subjects)
        if stray:
            raise FactorTradeabilityError(
                f"{stray} was rejected by the period and is not an admitted pair of this cross "
                "section; a rejection this cut cannot place has no group, and filing it under one "
                "would attribute a refusal to a quantile that never offered the security"
            )

    @staticmethod
    def _refuse_liquidity_that_is_not_this_periods(
        period: PeriodPortfolio,
        cross_section: ICCrossSection,
        liquidity: Mapping[str, SessionLiquidity],
    ) -> date | None:
        """Refuse a turnover for the wrong security, the wrong session, or an unadmitted one.

        Returns the one session every row shares, or `None` when none was offered. **One session
        for the whole period**, rather than a date per row: `PeriodTradeability` reports
        `liquidity_day` beside `entry_day` so a reader can see whether the capacity is the
        diagnostic reading or the point-in-time one, and a mapping whose rows came from different
        sessions has no such day to report.
        """
        if not liquidity:
            return None
        subjects = {pair.subject for pair in cross_section.pairs}
        unknown = sorted(set(liquidity) - subjects)
        if unknown:
            raise FactorTradeabilityError(
                f"{unknown} carries a session turnover and no admitted, labelled pair at "
                f"{period.as_of.isoformat()}; either the cross section never scored it or its "
                "coverage code was not admitted, and this boundary cannot tell those apart -- key "
                "the liquidity from cross_section.pairs. Ignoring the extra keys would hide a "
                "caller that read its two sides from two different universes"
            )
        days = set()
        for subject in sorted(liquidity):
            observed = liquidity[subject]
            if observed.subject != subject:
                raise FactorTradeabilityError(
                    f"the turnover filed under {subject} names {observed.subject}; a position "
                    "sized from another security's session is a capacity from the wrong rows"
                )
            days.add(observed.trade_date)
        if len(days) != 1:
            raise FactorTradeabilityError(
                f"the offered turnovers span {sorted(day.isoformat() for day in days)}; one "
                "period is sized against one session, and a mapping mixing sessions has no "
                "liquidity_day to report beside the entry day"
            )
        day = days.pop()
        if day > period.entry_day:
            raise FactorTradeabilityError(
                f"the turnovers are dated {day.isoformat()} and this period enters on "
                f"{period.entry_day.isoformat()}; sizing an order on turnover that had not "
                "happened when the order was placed is look-ahead in the sizing"
            )
        return day

    @staticmethod
    def _refuse_periods_that_are_not_one_study(ordered: Sequence[PeriodPortfolio]) -> None:
        """Refuse a series mixing tiers, cuts, horizons or `as_of`s.

        `factor_id`, `direction` and `group_count` are already checked against the declared spec
        one caller up, so what is left here is the pair a spec cannot see: the **tier** a period
        was measured on and the **horizon** its window spans. A turnover averaged across two
        horizons is an average of two different holding periods, which is exactly the quantity the
        ratio is meant to be about.
        """
        head = ordered[0]
        for period in ordered:
            mismatched = [
                f"{name}={getattr(period, name)!r} against {getattr(head, name)!r}"
                for name in ("tier", "horizon_sessions")
                if getattr(period, name) != getattr(head, name)
            ]
            if mismatched:
                raise FactorTradeabilityError(
                    f"the period at {period.as_of.isoformat()} reports {mismatched}; a turnover "
                    "series is one factor on one tier at one horizon, and a mean over two of "
                    "either is the average of two different holding periods"
                )
        stamps = [period.as_of for period in ordered]
        if len(set(stamps)) != len(stamps):
            duplicates = sorted({stamp.isoformat() for stamp in stamps if stamps.count(stamp) > 1})
            raise FactorTradeabilityError(
                f"{duplicates} appears more than once in this series; one as_of is one period, "
                "and counting it twice weights a day by how often a caller appended it"
            )


def _funnel(period: PeriodPortfolio) -> CoverageFunnel:
    """The four-stage funnel, read off the two censuses the period already carries.

    `valued_count` is the one line that needs both tier tables, and both are load-bearing rather
    than one being decoration. `ICCensus.excluded_by_coverage` is keyed by the codes the tier does
    **not** admit, so it holds no cell for `processed`; walking `TIER_COVERAGE_ORDER` and keeping
    the codes that carry a value and are not admitted is what selects `imputed` -- the one cell in
    which the two tables differ across all three tiers -- and dropping either filter breaks it in
    a different direction, one silently and one with a `KeyError`.
    """
    census: ICCensus = period.source_census
    admissible = census.admitted_count + census.unlabelled_count + census.unmatched_count
    excluded = dict(census.excluded_by_coverage)
    valued_and_not_admitted = sum(
        excluded[code]
        for code in TIER_COVERAGE_ORDER[census.tier]
        if code in TIER_VALUE_CODES[census.tier] and code not in TIER_ADMITTED_CODES[census.tier]
    )
    return CoverageFunnel(
        universe_count=census.subject_count,
        valued_count=admissible + valued_and_not_admitted,
        admissible_count=admissible,
        scored_count=census.admitted_count,
        held_count=period.census.held_count,
    )


def _rebalance(earlier: PeriodPortfolio, later: PeriodPortfolio, *, group: int) -> Rebalance:
    """One transition, priced entirely from fills `V2-P3-006` already produced.

    **No order is simulated here and no price is invented.** A retained name's avoided fee is
    literally the earlier period's `exit_fill.total_cost` plus the later period's
    `entry_fill.total_cost`, both `ExecutionResult`s the quantile study already obtained from
    `AShareExecutionPolicy`. The money denominators are the two ends' own values -- the earlier
    group at its exit and the later group at its entry -- which is what puts `money_turnover` in
    `[0, 1]` by construction rather than by convention.
    """
    from_holdings = {item.subject: item for item in earlier.groups[group].holdings}
    to_holdings = {item.subject: item for item in later.groups[group].holdings}
    retained = sorted(set(from_holdings) & set(to_holdings))
    exited = sorted(set(from_holdings) - set(to_holdings))
    entered = sorted(set(to_holdings) - set(from_holdings))
    return Rebalance(
        from_as_of=earlier.as_of,
        to_as_of=later.as_of,
        group=group,
        retained_count=len(retained),
        entered_count=len(entered),
        exited_count=len(exited),
        avoided_cost=sum(
            (
                from_holdings[subject].exit_fill.total_cost
                + to_holdings[subject].entry_fill.total_cost
                for subject in retained
            ),
            _ZERO,
        ),
        sold_value=sum((from_holdings[subject].gross_value for subject in exited), _ZERO),
        bought_value=sum((to_holdings[subject].entry_notional for subject in entered), _ZERO),
        from_value=earlier.groups[group].gross_value,
        to_value=later.groups[group].entry_notional,
    )
