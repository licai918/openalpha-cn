"""Quantile portfolio returns with real A-share costs (`V2-P3-006`).

A quantile study sorts one `as_of`'s cross section by factor value, cuts it into groups, holds
each group over the label's own window, and reports what each group returned **after the fees an
A-share round trip actually pays**. This module computes one such period, aggregates a series of
them, and states -- as codes and counts rather than as prose -- everything the market refused to
let the study do.

It stores nothing and reads no partition. `backtest/factor_ic.py` is the precedent and this is
its sibling one level along: `V2-P3-005` answered "did the ordering predict the return", and this
answers "what would holding the ordering have paid, net". The two are deliberately driven from
**the same object** -- `ICCrossSection` -- so an IC and a spread that disagree cannot be explained
away by different samples.

## The two contracts this module joins, and the one thing each is allowed to decide

`AShareExecutionPolicy` is per-order: it judges **one order against one bar** and returns a
`Decimal` fee breakdown. A group return is per-portfolio. The join is not an averaging trick, it
is a division of authority, and it is the whole design of this module:

    the gross return of a position    comes from domain/labels.py  (OutcomeLabel.realized_return)
    the fees of a position            come from AShareExecutionPolicy (two ExecutionResults)
    whether a position exists at all  comes from BOTH, label first

**The return never comes from the two fills, and that is measured rather than stylistic.** A fill
is at `MarketBar.close`, which is the *published* close, and a round trip priced from two
published closes is `close_exit / close_entry - 1` -- the third path `domain/daily_prices.py`
measured at **`-0.530973%`** on `000001.SZ`'s 2026-06-12 session where the truth is `+2.742230%`
(`close/pre_close`) and `+2.742251%` (`adj_factor`). Wrong by percentage points with the sign
reversed, on exactly the securities that had a corporate action. So a position's gross return is
`OutcomeLabel.realized_return` -- `WindowReturn.adjusted` -- and no argument on any function here
lets a caller pass a self-computed one.

What the two fills *are* used for is the money: `ExecutionResult.notional` sizes the position and
`ExecutionResult.total_cost` is what the round trip paid. One position's arithmetic, in full:

    entry_notional = buy.notional                       q shares at the entry bar's close
    entry_outlay   = entry_notional + buy.total_cost    what left the account
    gross_value    = entry_notional * (1 + realized)    the position at exit, adjusted
    net_proceeds   = gross_value - sell.total_cost      what came back
    net_return     = net_proceeds / entry_outlay - 1

**On a window with no corporate action `gross_value` and `sell.notional` are the same number**,
because a constant adjustment factor makes `realized` exactly `close_exit / close_entry - 1`. That
is a claim about arithmetic rather than algebra -- there is a `float` round trip and two cent
quantizations between the two -- so it is measured: **0 of 200,000** random
`(quantity, entry, exit)` combinations disagree, and
`tests/unit/backtest/test_factor_portfolio.py::
test_a_window_with_no_corporate_action_prices_the_exit_leg_at_the_published_close` drives 2,000 of
them in the suite. They part company on an ex-rights window, and the direction is the right one: a
cash dividend arrives as cash and pays no stamp duty, so charging the sell-side fee on
`sell.notional` -- the published close -- is the *correct* treatment and not an approximation of
one. What is genuinely approximate is a **share** change; see
`KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS`' `the_exit_leg_is_priced_on_the_entry_share_count`.

## Rejected fills: what a refused order is worth in a group return

Nothing. It is never a zero.

A zero is a return, and a group in which every locked-limit name reads flat is a group whose
return is partly a measurement of the lock rate -- the same argument `factor_ic` makes for
`ICCensus.unlabelled_count`, one plane along. Every position that could not be opened or closed
**leaves the group and is counted by its own `HoldingOutcome`**, and `PortfolioCensus` requires
the held count, every excluded cell and the unattempted count to total the securities the cross
section offered. A census that lost one fails its own arithmetic.

The gate runs **label first, policy second**, and the order is load-bearing rather than
incidental. `domain/labels.py` refuses a halted session, a session locked at either limit, an
unpublished band, a missing bar and all three registry verdicts, across **every** session of the
window; `AShareExecutionPolicy` sees one bar and knows none of that
(`KNOWN_EXECUTION_LIMITATIONS.the_registry_verdict_is_not_an_input`). Running the label first
means the contract that can see the **whole window** decides first, and a name it refused never
reaches an order. Neither contract dominates the other -- that is what
`KNOWN_EXECUTION_LIMITATIONS` is a registry of -- and what the policy adds on top is not nothing:

- **the board's minimum quantity** -- 100-share lots off STAR, 200 shares on it. The label
  contract has no concept of a share count, so this is the policy's alone and it is the reason a
  quantile study needs a declared capital at all.
- **a band the caller did not publish.** `MarketBar.up_limit=None` means "not supplied" and
  `_price_band` then derives one, which disagrees with the exchange's on **159 of the 5,338
  priced names of 2024-06-28**. The label already refused every name whose band was unpublished
  in *its* corpus, so what is left is a name the exchange did band and whose `MarketBar` did not
  carry it -- judged by the derived rule. Counted as `unpublished_band_legs` rather than left to
  be discovered, and named in the limitation registry.
- **T+1**, which cannot bind here and is stated so it is not mistaken for a live rule: the exit
  session is `calendar.shift(entry_day, horizon.sessions)` with `sessions >= 1`, so
  `exit_day > entry_day` for every constructible window.

## Grouping: declared, tie-safe, and the empty group is a code

`group_count` has **no default**. This repository has taken eight Critical findings on statistics
calibrated over narrow samples, and "ten deciles of seven securities" is that shape exactly. So
is `min_securities_per_group`, and the two together decide `insufficient_sample` before anything
else is asked.

Membership is by **average rank**, `factor_ic.average_ranks`, and not by sort position:

    group(security) = int((rank - 0.5) * group_count / n)

Two securities with the same factor value share a rank and therefore share a group, whatever
order the caller passed them in. The alternative -- sorting and slicing -- puts tied securities
in different groups according to the sort's tie-break, which makes the group boundary a function
of the caller's row order. A discretised factor ties constantly, so that is the ordinary case
rather than the corner one.

Ties then make groups unequal, and at the limit they make one **empty**. That is reported as a
code and never smoothed:

- `degenerate_scores` -- every value in the cross section ties. A defect in the factor at this
  `as_of`, decided before the group arithmetic for `factor_ic._degenerate_side`'s reason.
- `unfillable_groups` -- the values order, and the tie blocks still leave some group below
  `min_securities_per_group`. A fact about the factor's granularity against the declared cut.
- `unfillable_after_execution` -- the grouping worked and the *market* emptied a group: enough
  members were refused an entry or an exit that one fell below the floor. This one is the finding
  `V2-P3-007` exists for, which is why it is not folded into the code above it.

There is no `degenerate_returns` here, and the omission is deliberate rather than an oversight: a
correlation of a flat cross section is `0 / 0`, but a *portfolio* of one is a portfolio that
returned the same thing in every group. The spread is zero and the zero is true.

## Weighting: equal budget, lot-rounded, and the group return is the portfolio's own

Every position gets the same declared `position_capital`, and the group return is

    sum(net_proceeds) / sum(entry_outlay) - 1

rather than the mean of the members' returns. That is not a weighting *scheme* -- it is what the
group actually returned, and it is the only reading under which A-share lot rounding is visible
instead of assumed away. Equal *notional* is not constructible: a `¥300` security's smallest
position is `¥30,000` and a `¥3` one's is `¥300`, so a portfolio of both is equal in budget and
equal in fill only when the budget happens to be a whole number of lots of each. The realised
weights are therefore an output of the sizing rather than an input to it, which is what
`GroupReturn.entry_notional` beside `held_count` lets a reader see.

**And the declared capital moves the answer, which is why it is declared and measured rather than
defaulted.** `CostSchedule.minimum_commission` is a `¥5` floor under a 3bp rate, so it binds below
`¥16,666.67` of notional. Measured on this repository's default schedule, a round trip's total
drag is **0.1120%** at `¥20,000` and above -- 6bp commission, 0.2bp transfer, 5bp stamp -- and
**0.1520%** at `¥10,000`, **35.7% more for the same trade**. A study that quoted a net return
without its `position_capital` would be quoting a number whose scale it had not shown.

## `direction`: the sign is decided once, and both readings are kept

`V2-P3-005` chose to orient and to carry both numbers (`raw_ic` beside `ic`), and this module is
**consistent with it** -- with one difference that is forced by the arithmetic and is worth
stating, because "consistent" would otherwise be a claim nobody could check.

Group indices are always ascending in the **raw** factor value: group `0` holds the lowest values
and group `group_count - 1` the highest, whatever the factor declares. That is the measurement,
uninterpreted, and it is what makes a stored group table diffable against the values it came
from. Orientation then acts on **which group is long**:

    higher_is_better    top = group_count - 1    bottom = 0
    lower_is_better     top = 0                  bottom = group_count - 1

`long_short_spread` is `top - bottom` and `raw_spread` is `highest - lowest`, so the two are
exact negations of each other under `lower_is_better` -- the same relationship `ICPoint`'s
validator asserts between `ic` and `raw_ic`, and exact for the same reason (IEEE subtraction is
exactly rounded, so `a - b == -(b - a)`). What differs from `005` is only *where* the sign lives:
a correlation can be negated, a portfolio cannot, so the declaration reaches the group **choice**
instead of the number. `FactorDefinition` rather than a bare `FactorDirection` for
`FactorICSpec.definition`'s reason: the direction that decides which group is long has to be the
factor's declared one, and a parameter is something a caller can pass the other value to.

## The long-short spread is reported and is not a portfolio

`long_short_spread` is **a difference between two long portfolios' net returns**. It is not the
return of anything this repository can execute, and the field is not renamed into one:

1. `AShareExecutionPolicy` has no short side. `ExecutionRequest.side` is `buy | sell`, where
   `sell` closes a long: it charges stamp duty, and there is no borrow fee, no margin, no
   maintenance call and no locate.
2. A-share 融券 is restricted to a published list of eligible securities, at a broker-specific
   rate, with inventory that is not always there. None of those three is a dataset this
   repository holds, and inventing a borrow cost would be a number nobody measured.
3. **The contract-level reason is `execution.py`'s own.**
   `KNOWN_EXECUTION_LIMITATIONS.a_one_price_session_refuses_one_side_here_and_both_ends_there`
   records that the policy and the label agree on "the buy side of the entry and the sell side of
   the exit, which is the long round trip a label assumes", and not on the other two. A short
   round trip is **exactly** the other two -- a sell into the entry bar and a buy into the exit
   bar -- so the short leg is the combination on which the two contracts are known to disagree,
   with the policy fail-open (it fills a sell into a limit-up lock) and the label refusing.
   Shorting here would mean building a portfolio out of the one pair of verdicts this repository
   has already measured as not interchangeable.

So the number is published, because a reader wants it, and it is published under a name and a
limitation entry that say what it is.

## No compounding, no cumulative curve

A series of periods is summarised by mean, dispersion, an information-ratio-shaped quotient and a
hit rate -- and **no cumulative return, no equity curve and no annualisation**. The reason is
`factor_ic`'s reason for publishing no t-statistic, arriving one plane along: at horizon `h`, two
prediction days one session apart produce windows sharing `h` of the `h + 1` sessions each spans,
which `domain/labels.py::overlapping_windows` measures. Compounding overlapping periods counts the
same session's move several times, and the curve that comes out grows with the *sampling
frequency* rather than with the factor. A non-overlapping schedule is the precondition that would
make a cumulative number meaningful, and nothing in this module can tell that it was used.

`icir`'s divergence from `EventStudy` is inherited deliberately: `spread_ir` is `None` when the
dispersion is zero, never `math.inf`, because `inf` discards the sign of a constant negative
spread and is a value this repository's own storage validators refuse one plane over.

## The one product constraint a reader of a neutralised group return has to be told

**This section said "a neutralised residual is read at a year-end snapshot, not at the `as_of` a
caller asked for" and `V2-P4-026` retracted it.** `load_daily_valuations` no longer takes
`read_if_ready`, so a build is no longer forced to an `as_of` at or after its year's last
session. What is unchanged is the stamping rule --
`panel_neutralization.neutralized_observation_batch` puts the build's own `as_of` on all four
clocks of every row -- so a neutralised quantile series is exactly as point-in-time as the
schedule its residuals were built on, and a tier built once at year end still reads as one
December instant. The residuals' *content* is clean either way: each was regressed against the
industry, the market capitalisation and the processed value of its own day, so a neutralised
group return was never forward-contaminated by this. Carried as
`a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule`, the same code `V2-P3-005`
carries, because it is the same fact and renaming it per module would make it two.

## Layering, and why there is no numpy here

`backtest/` over `domain/`, standard library plus this package's own `backtest/execution.py` --
the edge `backtest/portfolio.py` already has. ADR-0003's Context named two workloads that would
re-open the numerical-stack question and both are now answered; this is a third and it was
measured rather than assumed to inherit them. A whole-market period is one `sorted()` (the ranks)
plus `2n` `Decimal` order simulations: **35.9 ms for 5,534 securities' round trips**, best of
seven, measured on the machine this issue was built on. That is the same order as
`apply_factor_transform`'s 35.9-37.6 ms over the same 5,534 participants and **62x** smaller than
the 2.24 s `compute_factor` read that has to precede it. The arithmetic is `Decimal` and not
float precisely because it is money, and `Decimal` is a thing numpy does not vectorise.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
)
from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIERS,
    FactorTier,
    ICCensus,
    ICCrossSection,
    ICObservationPair,
    average_ranks,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorDirection
from openalpha_cn.domain.time import ensure_aware

__all__ = [
    "BOARD_MINIMUM_QUANTITY",
    "HOLDING_OUTCOME_CODES",
    "HOLDING_OUTCOME_ORDER",
    "KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS",
    "MAXIMUM_GROUP_SECURITIES",
    "MAXIMUM_PORTFOLIO_GROUPS",
    "MAXIMUM_PORTFOLIO_PERIODS",
    "MINIMUM_GROUP_SECURITIES",
    "MINIMUM_PORTFOLIO_GROUPS",
    "MINIMUM_PORTFOLIO_PERIODS",
    "PERIOD_COVERAGE_CODES",
    "PERIOD_COVERAGE_ORDER",
    "PRE_EXECUTION_COVERAGE",
    "QUANTILE_PORTFOLIO_LIMITATION_CODES",
    "SERIES_COVERAGE_CODES",
    "SHARE_LOT",
    "FactorPortfolioError",
    "GroupReturn",
    "HoldingOutcome",
    "PeriodCoverage",
    "PeriodPortfolio",
    "PortfolioCensus",
    "PositionRejection",
    "QuantilePortfolioLimitation",
    "QuantilePortfolioSpec",
    "QuantilePortfolioStudy",
    "QuantilePortfolioSummary",
    "RoundTrip",
    "SeriesCoverage",
    "position_quantity",
    "rank_groups",
]

_CENT: Final[Decimal] = Decimal("0.01")
_ONE: Final[Decimal] = Decimal(1)


class FactorPortfolioError(ValueError):
    """Raised for a malformed quantile study -- never for a fact about the market.

    A `ValueError` subclass for `FactorICError`'s reason: every call site already writing
    `except ValueError` keeps catching it unchanged. A thin cross section, an all-tied one, a
    group the market emptied and a security nobody could trade are **not** this: each is a
    `PeriodCoverage` or a `HoldingOutcome`, because a loop over a year of `as_of`s has to be able
    to keep going past them and a report that showed only the days that worked would be a report
    of a different factor.
    """


PeriodCoverage = Literal[
    "measured",
    "insufficient_sample",
    "degenerate_scores",
    "unfillable_groups",
    "unfillable_after_execution",
]
"""Whether one `as_of` produced group returns, and if not, **why** -- never a bare `None`.

Read in the order `QuantilePortfolioStudy.measure` decides them, which is the order declared:

- **`insufficient_sample`** -- fewer admitted, labelled securities than
  `group_count * min_securities_per_group`. Decided first because every question below is a
  question about a cut, and there is nothing here worth cutting.
- **`degenerate_scores`** -- enough securities and every factor value ties. A defect in the factor
  at this `as_of`; `factor_ic._degenerate_side` names the same fact under the same code.
- **`unfillable_groups`** -- the values order, and the tie blocks still leave a group below
  `min_securities_per_group`. A fact about the factor's granularity against the declared cut.
- **`unfillable_after_execution`** -- the cut worked and the market emptied a group: entries and
  exits were refused until one fell below the floor. Separate from the code above it because the
  remedy is different -- a coarser cut fixes one and nothing fixes the other -- and because this
  is the reading `V2-P3-007` exists to surface.
- **`measured`** -- and only then does `groups` carry anything.

Closed for `ICCoverage`'s reason: `V2-P3-014`'s report groups by it, and a sixth spelling of "no
answer" would silently become a group of one.
"""

PERIOD_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(PeriodCoverage))

PERIOD_COVERAGE_ORDER: Final[tuple[PeriodCoverage, ...]] = get_args(PeriodCoverage)

PRE_EXECUTION_COVERAGE: Final[frozenset[str]] = frozenset(
    {"insufficient_sample", "degenerate_scores", "unfillable_groups"}
)
"""The codes decided **before** any order is placed, declared rather than inferred from the code.

The distinction is what keeps `PortfolioCensus` honest on a refused period. Under one of these
three no security was ever offered to `AShareExecutionPolicy`, so calling them all `unbarred` --
or `rejected_entry`, or anything else in `HoldingOutcome` -- would be a per-security verdict about
an order nobody placed. They are counted as `unattempted_count` instead, and
`PeriodPortfolio.__post_init__` requires that count to be **all** of the offered securities under
exactly these three codes and **none** of them under the other two, so the census cannot report a
period as both refused and attempted.

`unfillable_after_execution` is deliberately not here: under that code the orders really were
placed and their verdicts really are per-security facts, and throwing them away because the cut
came out short would discard the measurement that produced the code.
"""

SeriesCoverage = Literal["measured", "insufficient_periods"]
"""Whether a series of periods produced summary statistics, and if not, why.

Two members rather than five: every per-`as_of` refusal has already been decided one level down
and arrives here as a period that is simply not `measured`. What is left is a count.
"""

SERIES_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(SeriesCoverage))

HoldingOutcome = Literal[
    "held",
    "unbarred",
    "below_board_minimum",
    "rejected_entry",
    "rejected_exit",
]
"""What became of one security the cross section admitted, as a closed set.

- **`held`** -- both legs filled, and the position is in a group's arithmetic.
- **`unbarred`** -- the caller offered no `(entry, exit)` bar pair for it. Its own code rather
  than folded into a rejection, for `ICCensus.unmatched_count`'s reason: a short read looks
  exactly like a refusal until the two are counted apart.
- **`below_board_minimum`** -- `position_capital` does not reach one lot (100 shares) off STAR, or
  200 shares on it. There is no order to place: `ExecutionRequest.quantity` is `gt=0`, so this is
  decided before the policy rather than by it.
- **`rejected_entry`** -- `AShareExecutionPolicy` refused the buy against the entry bar.
- **`rejected_exit`** -- it refused the sell against the exit bar, with the position already open.

The last two carry the policy's own `reason` string on a `PositionRejection` rather than being
re-derived here, because this module is not a second authority on why an order does not fill.
"""

HOLDING_OUTCOME_CODES: Final[frozenset[str]] = frozenset(get_args(HoldingOutcome))

HOLDING_OUTCOME_ORDER: Final[tuple[HoldingOutcome, ...]] = get_args(HoldingOutcome)
"""The declared order, which is the order `PortfolioCensus` lays its excluded cells out in."""

SHARE_LOT: Final[int] = 100
"""The board lot every A-share market except STAR quotes buys in.

`AShareExecutionPolicy._rejection_reason` is the authority (`quantity % 100 != 0`); this constant
is what `position_quantity` sizes *to* so that the sizer and the policy cannot disagree about what
a legal order looks like. It is not applied on STAR, where the rule is a floor and not a multiple.
"""

BOARD_MINIMUM_QUANTITY: Final[Mapping[str, int]] = MappingProxyType(
    {"main": SHARE_LOT, "star": 200, "growth": SHARE_LOT, "bse": SHARE_LOT}
)
"""The fewest shares each board will accept on a buy, keyed by `MarketBar.board`.

Restated from `AShareExecutionPolicy._rejection_reason` rather than imported, because that rule is
expressed as two branches inside a private function and there is nothing to import. The two are
held together by `tests/unit/backtest/test_factor_portfolio.py::
test_every_board_minimum_is_the_quantity_the_execution_policy_itself_accepts`, which drives the
real policy at each board's minimum and at one share below it and requires a fill and a rejection
respectively -- so a board whose rule moved in `execution.py` fails here rather than silently
sizing orders that get refused.
"""

MINIMUM_PORTFOLIO_GROUPS: Final[int] = 2
"""The floor under `QuantilePortfolioSpec.group_count`, and it is arithmetic rather than taste.

A "grouped" return over one group is the return of the whole cross section, and the spread it is
read for is `top - bottom` over a single group -- identically zero for every factor, every day.
Two is the first count at which a spread can be anything.
"""

MAXIMUM_PORTFOLIO_GROUPS: Final[int] = 1_000
"""The range check on the declared cut. Comfortably above ADR-0002's ~5,500-name whole-market
cross section divided by any usable group size, and refusing a caller who typed a share count into
the field. A cut a cross section can never fill is still declarable -- every `as_of` reports
`unfillable_groups`, with the counts stored -- for `MAXIMUM_IC_SECURITIES`' stated reason.
"""

MINIMUM_GROUP_SECURITIES: Final[int] = 1
"""The floor under `QuantilePortfolioSpec.min_securities_per_group`, and it is arithmetic.

A group with no member has no return: `sum(entry_outlay)` is zero and the quotient does not exist.
One is the first size at which a group return is defined at all -- and it is a *floor*, not a
recommendation. A quantile portfolio of one security per group is a portfolio whose return is that
one security's, which `KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS` records by name for the reason this
repository has taken eight Critical findings on narrow-sample calibration and none of them was
prevented by a constant somebody buried in an engine.
"""

MAXIMUM_GROUP_SECURITIES: Final[int] = 10_000
"""The range check on the stored floor, `FactorICSpec.min_securities`' own bound and for its
reason."""

MINIMUM_PORTFOLIO_PERIODS: Final[int] = 2
"""The floor under `QuantilePortfolioSpec.min_periods`. A sample standard deviation of one number
does not exist, so a spread information ratio over a single period is undefined rather than merely
weak -- `MINIMUM_IC_AS_OFS`' argument, unchanged."""

MAXIMUM_PORTFOLIO_PERIODS: Final[int] = 10_000
"""The range check on the stored floor. Roughly forty A-share years of daily periods."""


@dataclass(frozen=True, slots=True, kw_only=True)
class QuantilePortfolioLimitation:
    """One named boundary on what a stored quantile study can be trusted to answer."""

    code: str
    detail: str


KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS: Final[tuple[QuantilePortfolioLimitation, ...]] = (
    QuantilePortfolioLimitation(
        code="a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule",
        detail=(
            "THIS ENTRY WAS NAMED neutralised_residuals_are_read_at_a_year_end_snapshot AND THE "
            "RENAME IS THE RETRACTION. It used to say that a neutralised group return is stamped "
            "at the year end whatever as_of a caller asked for, because load_daily_valuations "
            "took read_if_ready and no build could therefore be run inside the year its "
            "daily_basic partition covers. V2-P4-026 removed that: panel_ingest."
            "_read_visible_price_session reads one session under a WHERE available_time <= as_of "
            "predicate, so a residual can be built at a mid-year as_of and is read back at it. "
            "What survives is the stamping rule -- neutralized_observation_batch still puts the "
            "BUILD's as_of on all four clocks of every row, which is right for a derived row -- "
            "so a quantile series over neutralised residuals is exactly as point-in-time as the "
            "schedule those residuals were built on, and a tier built once at year end still "
            "collapses to one December instant with nothing in the stored rows saying so. Two "
            "refusals outside this module still bound the schedule: index_member_all is read "
            "whole partition (V2-P4-027) and no cross section before 2021-12-13 is assemblable. "
            "The residuals' CONTENT was clean before and after -- each was regressed against the "
            "industry, the market capitalisation and the processed value of its own day -- so "
            "the returns were never forward-contaminated by this. KNOWN_IC_LIMITATIONS carries "
            "this same code for the same fact; it is one limitation of the plane, not two of two "
            "modules."
        ),
    ),
    QuantilePortfolioLimitation(
        code="the_long_short_spread_is_not_a_shortable_portfolio",
        detail=(
            "long_short_spread is the difference between two LONG portfolios' net returns and is "
            "not the return of anything this repository can execute. AShareExecutionPolicy has no "
            "short side at all -- ExecutionRequest.side is buy|sell where sell closes a long, so "
            "there is no borrow fee, no margin and no locate anywhere in the fee breakdown. "
            "A-share 融券 is restricted to a published eligible list at a broker-specific rate "
            "with inventory that is not always there, and none of those three is a dataset this "
            "repository holds. The contract-level reason is execution.py's own: "
            "KNOWN_EXECUTION_LIMITATIONS.a_one_price_session_refuses_one_side_here_and_both_ends"
            "_there records that the policy and domain/labels.py agree on the buy side of the "
            "entry and the sell side of the exit and NOT on the other two -- and a short round "
            "trip is exactly the other two, with the policy fail-open (it fills a sell into a "
            "one-price limit-up bar) where the label refuses."
        ),
    ),
    QuantilePortfolioLimitation(
        code="a_group_return_is_conditioned_on_the_names_that_could_be_traded",
        detail=(
            "Every security the label refused or the policy rejected leaves its group and is "
            "counted; it is never carried at a zero return. That is the right treatment of a "
            "number nobody could have earned, and it biases the surviving group in a direction "
            "worth naming: domain/labels.py's REFUSAL_LOCKED_AT_LIMIT and "
            "REFUSAL_HALTED_INTO_THE_CLOSE fire on exactly the sessions where a security moved "
            "hardest or stopped moving at all, so a group return is conditioned on tradeability "
            "and reads better than a portfolio that had to hold the untradeable ones. "
            "PortfolioCensus.held_count against offered_count is how large that conditioning was "
            "on any one period; nothing in this module can undo it, because the counterfactual "
            "return of a position that could not be opened is not a quantity any contract here "
            "holds."
        ),
    ),
    QuantilePortfolioLimitation(
        code="the_exit_leg_is_priced_on_the_entry_share_count",
        detail=(
            "The sell order is placed for the same quantity the buy filled, so a window "
            "containing a bonus issue or a split charges the sell-side fee on the wrong share "
            "count. It is not fixable from what this repository stores: Tushare's adj_factor is "
            "one cumulative number that moves on a cash dividend and on a share change alike "
            "(domain/adjustment.py measured 000001.SZ's running 1.0 -> 139.008 over 8,627 rows), "
            "so the factor ratio across a window cannot say which of the two happened and the "
            "exit share count is not reconstructible. The direction and the size are both "
            "bounded and both measured: only the sell leg is affected, all three of its "
            "components scale with the notional, and on the default schedule the whole sell-side "
            "fee is 8.1bp of it (3bp commission + 0.1bp transfer + 5bp stamp) against 3.1bp on "
            "the buy. A 10-for-10 bonus doubles the share count and roughly halves the price, so "
            "the sell leg is charged on half the shares actually sold and the round trip's cost "
            "is understated by about 4.05bp of the position -- the missing half of 8.1bp. A CASH "
            "dividend is not affected at all and this is not an approximation of it: the dividend "
            "arrives as cash, pays no stamp duty, and charging the fee on the published exit "
            "close is the correct treatment rather than a convenient one."
        ),
    ),
    QuantilePortfolioLimitation(
        code="overlapping_periods_are_not_compounded_into_a_cumulative_curve",
        detail=(
            "QuantilePortfolioSummary reports mean, dispersion, an information-ratio-shaped "
            "quotient and a hit rate, and deliberately publishes NO cumulative return, equity "
            "curve or annualisation. At horizon h two prediction days one session apart produce "
            "windows sharing h of the h + 1 sessions each spans -- five of six at 5d, nine of ten "
            "at 10d, which domain/labels.py::overlapping_windows measures and reports. "
            "Compounding those counts one session's move several times, and the curve that comes "
            "out grows with the sampling frequency rather than with the factor. A non-overlapping "
            "schedule is the precondition that would make a cumulative number mean something, and "
            "this module cannot tell from a series of periods that one was used. It is the same "
            "independence claim KNOWN_IC_LIMITATIONS refuses to make with a t-statistic, arriving "
            "one plane along."
        ),
    ),
    QuantilePortfolioLimitation(
        code="every_period_is_an_independent_round_trip_so_turnover_is_total",
        detail=(
            "A period buys its whole group at the entry session and sells all of it at the exit "
            "session. Nothing is carried across periods, so turnover is 100% per period by "
            "construction and the fees reported here are an UPPER bound relative to a rebalanced "
            "portfolio that keeps a name it still ranks highly. That is a deliberate scope line "
            "rather than a simplification nobody noticed: a rolling portfolio needs a holdings "
            "state, a rebalance schedule and a turnover measurement, which is V2-P3-007. What is "
            "measurable from this module today is the per-period cost drag "
            "(GroupReturn.cost_drag), which is exactly the quantity a turnover model would scale "
            "down."
        ),
    ),
    QuantilePortfolioLimitation(
        code="an_unpublished_band_on_a_bar_is_judged_by_the_derived_one",
        detail=(
            "MarketBar.up_limit=None means 'the caller supplied no band', and _price_band then "
            "derives one from board and is_st -- which disagrees with the exchange's published "
            "band on 159 of the 5,338 priced names of 2024-06-28, for the four reasons "
            "backtest/execution.py's docstring lists. domain/labels.py refuses an unpublished "
            "band outright (REFUSAL_UNPUBLISHED_BAND), so a name that reaches an order here had a "
            "band in the LABEL's corpus; what is not guaranteed is that the caller put it on the "
            "MarketBar. Those legs are counted rather than assumed away -- "
            "PeriodPortfolio.unpublished_band_legs -- and the count is over held positions only, "
            "so a period reporting zero is a period every one of whose fills was judged against "
            "the exchange's own numbers. Build the pair with published_limit_fields."
        ),
    ),
    QuantilePortfolioLimitation(
        code="the_reported_returns_move_with_the_declared_position_capital",
        detail=(
            "position_capital is not a presentation choice. CostSchedule.minimum_commission is a "
            "¥5 floor under a 3bp rate, so it binds below ¥16,666.67 of notional, and lot "
            "rounding truncates the budget differently for a ¥3 security and a ¥300 one. Measured "
            "on the default schedule, a round trip's total drag is 0.1120% at ¥20,000 of notional "
            "and above (6bp commission + 0.2bp transfer + 5bp stamp) and 0.1520% at ¥10,000 -- "
            "35.7% more cost for the same trade. A declared min_securities_per_group of 1 is legal "
            "for MINIMUM_GROUP_SECURITIES' arithmetic reason and produces a group return that is "
            "one security's, which compounds with this. The contract records which capital was "
            "chosen and refuses to choose one; a report quoting a net return without it is "
            "quoting a number whose scale it has not shown."
        ),
    ),
)
"""What a stored quantile study does not answer, as a closed registry rather than as prose.

Every entry is bound to the suite by `tests/unit/test_known_limitation_registries.py`, which
requires each `code` to appear as a string literal in *executable* test code -- the P2 review
measured that a code named only in docstrings can be renamed with the whole suite staying green.
"""

QUANTILE_PORTFOLIO_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS
)


def rank_groups(scores: Sequence[float], *, group_count: int) -> tuple[int, ...]:
    """Each security's group index, ascending in the **raw** factor value, ties kept together.

    `int((rank - 0.5) * group_count / n)` over `factor_ic.average_ranks`. Two securities with the
    same value share a rank and therefore share a group, whatever order they arrived in -- which
    is the property a sort-and-slice implementation does not have, because it puts tied values on
    either side of a boundary according to the sort's tie-break. A discretised factor ties
    constantly, so that is the ordinary case and not the corner one.

    **The index cannot reach `group_count` and there is no clamp**, deliberately: `rank <= n` for
    every security, so the largest value the expression can take is `(n - 0.5) * group_count / n`,
    which is short of `group_count` by `group_count / (2n) > 0`. A `min(...)` guard here would be a
    branch no input reaches, and this repository has measured what an unreachable branch is worth
    as evidence. `tests/unit/backtest/test_factor_portfolio.py::
    test_no_cross_section_can_be_ranked_into_a_group_that_does_not_exist` drives the property over
    random shapes instead.

    Groups may be **empty** and that is not this function's business to prevent: emptiness is a
    fact about the factor's ties against the declared cut, and `QuantilePortfolioStudy.measure`
    reports it as `unfillable_groups`. Refusing here would make a coverage code unreachable.
    """
    if group_count < MINIMUM_PORTFOLIO_GROUPS:
        raise FactorPortfolioError(
            f"a quantile cut needs at least {MINIMUM_PORTFOLIO_GROUPS} groups and {group_count} "
            "was offered; a spread over one group is identically zero for every factor"
        )
    if not scores:
        raise FactorPortfolioError(
            "an empty cross section has no ranks to cut; a thin one is reported as "
            "insufficient_sample and an empty one is a question nobody asked"
        )
    size = len(scores)
    return tuple(int((rank - 0.5) * group_count / size) for rank in average_ranks(scores))


def position_quantity(*, capital: Decimal, market: MarketBar) -> int:
    """How many shares `capital` buys of `market`, in a size the board will accept, or `0`.

    Off STAR the rule is a **multiple**: `AShareExecutionPolicy` refuses `quantity % 100 != 0`, so
    the budget is floored to whole lots and the remainder stays unspent. On STAR the rule is a
    **floor** -- at least 200 shares, any count above it -- so the whole budget is used. Sizing to
    100-share lots there as well would leave up to 99 shares' worth of the declared capital idle on
    every STAR position, which would make the reported drag a function of which board a name is on.

    **There is no second floor check off STAR, and the omission is the same one `rank_groups`
    makes.** Those three boards' minimum *is* one lot (`BOARD_MINIMUM_QUANTITY[b] == SHARE_LOT`,
    pinned by `tests/unit/backtest/test_factor_portfolio.py::
    test_every_board_minimum_is_the_quantity_the_execution_policy_itself_accepts`), so "did the
    budget reach the minimum" and "did it round down to zero lots" are the same question and a
    `quantity >= minimum` comparison here would be a branch no input can take either way. A branch
    no input reaches is not evidence of anything, so it is not written.

    `capital` bounds the **notional**. Commission, transfer fee and stamp duty are paid on top and
    are not reserved out of it, which is why `entry_outlay` can exceed `position_capital` by the
    buy-side fee and why the two are reported separately.

    Returns `0` when the budget does not reach the board's minimum. That is not an order this
    module declines to place -- `ExecutionRequest.quantity` is `gt=0`, so there is no order to
    place at all, which is why `below_board_minimum` is decided here and not by the policy.
    """
    if capital <= 0:
        raise FactorPortfolioError(
            f"position capital {capital!r} is not positive; a budget that buys nothing would "
            "report every security as below_board_minimum and call it a market fact"
        )
    if market.board == "star":
        shares = int(capital // market.close)
        return shares if shares >= BOARD_MINIMUM_QUANTITY["star"] else 0
    return int(capital // (market.close * SHARE_LOT)) * SHARE_LOT


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionRejection:
    """One security the cross section admitted and the portfolio could not hold.

    A plain carrier -- `LabelRefusal`'s precedent -- whose whole job is to keep the *reason*
    beside the count. `PortfolioCensus` says how many names were `rejected_entry`; this says which
    ones and what `AShareExecutionPolicy` called it, so a period whose bottom group emptied is a
    finding somebody can act on rather than an integer.
    """

    subject: str
    outcome: HoldingOutcome
    reason: str

    def __post_init__(self) -> None:
        if self.outcome == "held":
            raise FactorPortfolioError(
                f"{self.subject} is recorded as a rejection under the 'held' outcome; a position "
                "that was held is in a group's arithmetic and is not a reason for anything"
            )
        if self.outcome not in HOLDING_OUTCOME_CODES:
            raise FactorPortfolioError(
                f"{self.outcome!r} is not a declared holding outcome; expected one of "
                f"{sorted(HOLDING_OUTCOME_CODES)}"
            )
        if not self.subject.strip() or not self.reason.strip():
            raise FactorPortfolioError("a rejection must name a subject and carry a reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class RoundTrip:
    """One security bought at the entry session's close and sold at the exit session's close.

    The join this module exists to make, at the level of one position: two `ExecutionResult`s from
    `AShareExecutionPolicy` supply the money and the fees, and `gross_return` -- which is
    `OutcomeLabel.realized_return`, `WindowReturn.adjusted` -- supplies the move. Nothing here
    recomputes a return from the two fills; see this module's docstring for the measured reason.

    Every money property is derived rather than stored, so a stored field cannot disagree with the
    fills it came from. That is `factor_ic`'s argument against publishing a t-statistic beside the
    two fields it is computed from, applied to a carrier instead of to a report.
    """

    subject: str
    group: int
    quantity: int
    entry_fill: ExecutionResult
    exit_fill: ExecutionResult
    gross_return: float
    """`OutcomeLabel.realized_return` over this window: the adjusted, entry-to-exit return."""
    unpublished_band_legs: int
    """How many of this round trip's two bars carried no published band (`0`, `1` or `2`)."""

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise FactorPortfolioError("a round trip must name a subject")
        if self.group < 0:
            raise FactorPortfolioError(f"{self.subject} is filed in group {self.group}")
        if not 0 <= self.unpublished_band_legs <= 2:
            raise FactorPortfolioError(
                f"{self.subject} reports {self.unpublished_band_legs} unpublished band leg(s) and "
                "a round trip has two"
            )
        for name, fill, side in (
            ("entry_fill", self.entry_fill, "buy"),
            ("exit_fill", self.exit_fill, "sell"),
        ):
            if fill.status != "filled" or fill.side != side:
                raise FactorPortfolioError(
                    f"{self.subject}'s {name} is a {fill.status} {fill.side}; a round trip is a "
                    "filled buy and a filled sell, and a rejected leg is a PositionRejection"
                )
            if fill.quantity != self.quantity:
                raise FactorPortfolioError(
                    f"{self.subject}'s {name} filled {fill.quantity} shares against a position of "
                    f"{self.quantity}; the two legs are one position and a mismatch would leave "
                    "shares nothing accounts for"
                )
        if not math.isfinite(self.gross_return) or self.gross_return <= -1.0:
            raise FactorPortfolioError(
                f"{self.subject}'s gross return is {self.gross_return!r}; a window return is a "
                "ratio of two positive closes minus one, so it is finite and strictly above -1"
            )

    @property
    def entry_notional(self) -> Decimal:
        """`quantity * entry close`: the money put to work, before the buy-side fee."""
        return self.entry_fill.notional

    @property
    def entry_outlay(self) -> Decimal:
        """What left the account: the notional plus commission and transfer fee."""
        return self.entry_fill.notional + self.entry_fill.total_cost

    @property
    def gross_value(self) -> Decimal:
        """The position at the exit session, on the label's adjusted scale.

        `Decimal(str(...))` and not `Decimal(...)` for `published_limit_fields`' measured reason:
        `Decimal(0.1)` is `0.1000000000000000055511151231257827...`, the binary double carried
        into a type whose whole point is that it does not do that, while `Decimal(str(0.1))` is
        exactly `0.1`. The float is the label contract's and the money is this contract's, so
        *something* has to bridge them, and the obvious spelling is the wrong one.
        """
        return (self.entry_notional * (_ONE + Decimal(str(self.gross_return)))).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )

    @property
    def net_proceeds(self) -> Decimal:
        """What came back: the position's adjusted value less the sell-side fee."""
        return self.gross_value - self.exit_fill.total_cost

    @property
    def net_return(self) -> float:
        """`net_proceeds / entry_outlay - 1`, this position's cost-inclusive return."""
        return float(self.net_proceeds) / float(self.entry_outlay) - 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupReturn:
    """One quantile's holdings and what they returned, gross and net of the fees they paid.

    **Two stored fields and everything else derived.** The group index and the positions are the
    only facts; `gross_return`, `net_return` and `cost_drag` are properties over the positions, so
    there is no stored number that can drift from the fills it was computed from. `factor_ic`
    declines to publish a t-statistic beside the `icir` and `measured_count` it is a function of
    for the same reason, and this is that argument taken one step further.

    The group return is `sum(net_proceeds) / sum(entry_outlay) - 1` and not the mean of the
    members' `net_return`s. That is not a weighting scheme: it is what the group actually returned
    on the money it actually spent, and it is the only reading under which A-share lot rounding is
    visible rather than assumed away.
    """

    group: int
    holdings: tuple[RoundTrip, ...]

    def __post_init__(self) -> None:
        if not self.holdings:
            raise FactorPortfolioError(
                f"group {self.group} holds nothing; a group with no member has no return at all "
                "(the denominator is zero), which is a coverage code and not a GroupReturn"
            )
        misfiled = sorted(item.subject for item in self.holdings if item.group != self.group)
        if misfiled:
            raise FactorPortfolioError(
                f"{misfiled} is filed under group {self.group} and reports another; a position "
                "counted in the wrong quantile is a return attributed to the wrong signal"
            )
        subjects = [item.subject for item in self.holdings]
        if len(set(subjects)) != len(subjects):
            duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
            raise FactorPortfolioError(
                f"{duplicates} is held twice in group {self.group}; one security is one position "
                "at one as_of, and counting it twice weights it by how often it was appended"
            )

    @property
    def held_count(self) -> int:
        return len(self.holdings)

    @property
    def entry_notional(self) -> Decimal:
        return sum((item.entry_notional for item in self.holdings), Decimal("0.00"))

    @property
    def entry_cost(self) -> Decimal:
        """Buy-side commission and transfer fee across the group."""
        return sum((item.entry_fill.total_cost for item in self.holdings), Decimal("0.00"))

    @property
    def exit_cost(self) -> Decimal:
        """Sell-side commission, transfer fee and stamp duty across the group."""
        return sum((item.exit_fill.total_cost for item in self.holdings), Decimal("0.00"))

    @property
    def gross_value(self) -> Decimal:
        return sum((item.gross_value for item in self.holdings), Decimal("0.00"))

    @property
    def entry_outlay(self) -> Decimal:
        return self.entry_notional + self.entry_cost

    @property
    def net_proceeds(self) -> Decimal:
        return self.gross_value - self.exit_cost

    @property
    def gross_return(self) -> float:
        """What the same holdings would have returned with no fee anywhere."""
        return float(self.gross_value) / float(self.entry_notional) - 1.0

    @property
    def net_return(self) -> float:
        """The group's return on the money it spent, fees on both legs included."""
        return float(self.net_proceeds) / float(self.entry_outlay) - 1.0

    @property
    def cost_drag(self) -> float:
        """`gross_return - net_return`: what the round trip's fees took, in return space."""
        return self.gross_return - self.net_return

    @property
    def unpublished_band_legs(self) -> int:
        return sum(item.unpublished_band_legs for item in self.holdings)


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioCensus:
    """What became of every security the cross section admitted, as numbers that add up.

    `ICCensus`' instrument pointed one plane along. That census ends at "admitted and labelled";
    this one starts there and asks what the *market* then allowed, and the two together are the
    whole funnel from the subjects a build offered to the positions a group actually held.

    `excluded_by_outcome` carries **every** non-held outcome in `HOLDING_OUTCOME_ORDER`, including
    the ones that did not occur, for `ICCensus.excluded_by_coverage`'s reason: a code missing from
    the tuple and a code present with a zero are different claims -- "nobody was `unbarred`"
    against "nothing looked" -- and a report that dropped the zero rows would collapse them.
    """

    offered_count: int
    """How many securities the cross section admitted and labelled, i.e. `ICCensus.admitted_count`.
    """
    held_count: int
    excluded_by_outcome: tuple[tuple[str, int], ...]
    unattempted_count: int
    """Securities for which **no order was placed at all**, because the period was refused before
    the cut. Its own scalar rather than a sixth `HoldingOutcome`, because every member of that
    vocabulary is a verdict some contract reached about a security, and "nobody asked" is the one
    thing none of them can say. See `PRE_EXECUTION_COVERAGE`."""

    def __post_init__(self) -> None:
        for name in ("offered_count", "held_count", "unattempted_count"):
            if int(getattr(self, name)) < 0:
                raise FactorPortfolioError(f"{name} cannot be negative")
        expected = tuple(code for code in HOLDING_OUTCOME_ORDER if code != "held")
        if tuple(code for code, _count in self.excluded_by_outcome) != expected:
            raise FactorPortfolioError(
                f"excluded_by_outcome is keyed by "
                f"{[code for code, _count in self.excluded_by_outcome]} and the declared "
                f"exclusions are {list(expected)} in that order; a census missing a code cannot be "
                "told from one whose count is zero"
            )
        if any(count < 0 for _code, count in self.excluded_by_outcome):
            raise FactorPortfolioError("an excluded-outcome count cannot be negative")
        total = (
            self.held_count
            + self.unattempted_count
            + sum(count for _code, count in self.excluded_by_outcome)
        )
        if total != self.offered_count:
            raise FactorPortfolioError(
                f"the census accounts for {total} securities and {self.offered_count} were "
                "offered; every admitted security is held, excluded under exactly one outcome, or "
                "never asked about, and a census that does not add up has lost one of them"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class PeriodPortfolio:
    """One `as_of`'s quantile portfolios: what each group held, what it returned, and what it cost.

    Built by `QuantilePortfolioStudy.measure`; this constructor re-derives none of the arithmetic,
    following `ICCrossSection`'s precedent. What it does enforce is the relationship between the
    coverage code and the groups, so a period that reports a code and a set of returns -- or a
    `measured` one missing a quantile -- is not constructible.
    """

    as_of: datetime
    tier: FactorTier
    factor_id: str
    direction: FactorDirection
    group_count: int
    horizon_sessions: int
    entry_day: date
    exit_day: date
    coverage: PeriodCoverage
    groups: tuple[GroupReturn, ...]
    """Every group's holdings, indexed **ascending in the raw factor value**, or `()`.

    Group `0` is the lowest factor value and group `group_count - 1` the highest, whatever the
    factor's `direction` declares. The declaration decides which of those two is *long* -- see
    `top_group_index` -- and never which end of the table a group is written at, so a stored group
    table stays diffable against the values it was cut from.
    """
    census: PortfolioCensus
    """What became of the securities the cross section admitted.

    `census.held_count` equals the positions in `groups` under the `measured` code and this
    constructor requires that. It is deliberately **not** required under
    `unfillable_after_execution`: there the orders really were placed and really did fill, and the
    cut came out short *afterwards*. A census that zeroed itself to match an empty `groups` would
    erase the measurement that produced the code. Under the three `PRE_EXECUTION_COVERAGE` codes
    the held count is zero because nothing was ever submitted, which `unattempted_count` is what
    says.
    """
    source_census: ICCensus
    """The cross section's own census, carried so the funnel closes: `census.offered_count` is
    `source_census.admitted_count` and this constructor requires it."""
    rejections: tuple[PositionRejection, ...]
    """One entry per security that was offered an order and did not end up held, with the reason
    `AShareExecutionPolicy` gave. Empty under every `PRE_EXECUTION_COVERAGE` code, because a
    reason is something a contract said about an order and there were none."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", ensure_aware(self.as_of))
        if self.tier not in FACTOR_TIERS:
            raise FactorPortfolioError(
                f"{self.tier!r} is not a declared tier; expected one of {sorted(FACTOR_TIERS)}"
            )
        if self.coverage not in PERIOD_COVERAGE_CODES:
            raise FactorPortfolioError(
                f"{self.coverage!r} is not a declared coverage code; expected one of "
                f"{sorted(PERIOD_COVERAGE_CODES)}"
            )
        if self.group_count < MINIMUM_PORTFOLIO_GROUPS:
            raise FactorPortfolioError(
                f"a period is cut into at least {MINIMUM_PORTFOLIO_GROUPS} groups and this one "
                f"reports {self.group_count}"
            )
        measured = self.coverage == "measured"
        indices = tuple(item.group for item in self.groups)
        if measured != (indices == tuple(range(self.group_count))):
            raise FactorPortfolioError(
                f"coverage {self.coverage!r} carries groups {list(indices)} against a cut of "
                f"{self.group_count}; exactly the 'measured' code carries every group once in "
                "ascending order, and every other code carries none -- a period that reported a "
                "refusal beside a partial set of returns would be a spread over quantiles that "
                "were not all filled"
            )
        if self.census.offered_count != self.source_census.admitted_count:
            raise FactorPortfolioError(
                f"the portfolio census was offered {self.census.offered_count} securities and the "
                f"cross section admitted {self.source_census.admitted_count}; the two censuses are "
                "one funnel and a step that loses a security between them cannot be audited"
            )
        unattempted = self.census.offered_count if self.coverage in PRE_EXECUTION_COVERAGE else 0
        if self.census.unattempted_count != unattempted:
            raise FactorPortfolioError(
                f"coverage {self.coverage!r} reports {self.census.unattempted_count} unattempted "
                f"of {self.census.offered_count} offered and requires {unattempted}; exactly the "
                f"{sorted(PRE_EXECUTION_COVERAGE)} codes are decided before any order is placed, "
                "and a census that mixed the two would attribute a per-security verdict to an "
                "order nobody submitted"
            )
        grouped = sum(item.held_count for item in self.groups)
        if measured and self.census.held_count != grouped:
            raise FactorPortfolioError(
                f"the census reports {self.census.held_count} held and the groups hold {grouped}; "
                "the census is the arithmetic over the same positions and cannot be a second "
                "source of truth for them"
            )
        if self.entry_day >= self.exit_day:
            raise FactorPortfolioError(
                f"this period enters on {self.entry_day.isoformat()} and exits on "
                f"{self.exit_day.isoformat()}; a round trip that closes at or before it opens is "
                "not a holding period, and T+1 forbids the degenerate case outright"
            )

    @property
    def top_group_index(self) -> int:
        """The group this factor's declaration says should perform best."""
        return self.group_count - 1 if self.direction == "higher_is_better" else 0

    @property
    def bottom_group_index(self) -> int:
        """The group this factor's declaration says should perform worst."""
        return 0 if self.direction == "higher_is_better" else self.group_count - 1

    @property
    def group_net_returns(self) -> tuple[float, ...]:
        return tuple(item.net_return for item in self.groups)

    @property
    def group_gross_returns(self) -> tuple[float, ...]:
        return tuple(item.gross_return for item in self.groups)

    @property
    def raw_spread(self) -> float | None:
        """`highest value group - lowest value group`, net, with no interpretation applied."""
        if not self.groups:
            return None
        return self.groups[-1].net_return - self.groups[0].net_return

    @property
    def long_short_spread(self) -> float | None:
        """`top - bottom`, net, oriented so that **positive means the factor worked**.

        Exactly `raw_spread` under `higher_is_better` and exactly its negation under
        `lower_is_better`, which holds bit for bit rather than to a tolerance: IEEE subtraction is
        exactly rounded, so `a - b` and `-(b - a)` are the same double.

        **Not the return of a portfolio.** See
        `KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS.the_long_short_spread_is_not_a_shortable_portfolio`:
        there is no short side in `AShareExecutionPolicy`, no borrow cost anywhere in this
        repository, and the short round trip is precisely the pair of verdicts on which the
        execution policy and the label contract are already measured to disagree.
        """
        if not self.groups:
            return None
        return (
            self.groups[self.top_group_index].net_return
            - self.groups[self.bottom_group_index].net_return
        )

    @property
    def gross_long_short_spread(self) -> float | None:
        """`long_short_spread` before any fee, so the spread's own cost drag is readable."""
        if not self.groups:
            return None
        return (
            self.groups[self.top_group_index].gross_return
            - self.groups[self.bottom_group_index].gross_return
        )

    @property
    def unpublished_band_legs(self) -> int:
        """Held legs judged against a derived band because the bar carried no published one."""
        return sum(item.unpublished_band_legs for item in self.groups)


class QuantilePortfolioSpec(BaseModel):
    """The declared policy a quantile study applies: which factor, which cut, how much money.

    `definition` rather than a bare `direction`, for `FactorICSpec`'s reason and it is the whole
    argument of this contract in one field: which group is long is decided by a *declared*
    property of the factor, and a study taking the direction as its own argument is one a caller
    can hand the other value to -- which is exactly how a factor comes to be reported as working
    in whichever direction it happened to come out.

    Nothing here has a default. Each of the four moves the answers, and a default is a decision
    nobody recorded making; `position_capital` is the sharpest of them, because
    `CostSchedule.minimum_commission`'s ¥5 floor makes the reported drag a function of it below
    ¥16,666.67 of notional.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    definition: FactorDefinition
    group_count: int = Field(ge=MINIMUM_PORTFOLIO_GROUPS, le=MAXIMUM_PORTFOLIO_GROUPS)
    """How many quantiles the cross section is cut into. See `MINIMUM_PORTFOLIO_GROUPS` for why the
    floor is two and this module's docstring for why there is no default."""
    min_securities_per_group: int = Field(ge=MINIMUM_GROUP_SECURITIES, le=MAXIMUM_GROUP_SECURITIES)
    """The fewest members every group needs before this study will report a return at all."""
    position_capital: Decimal = Field(gt=0)
    """The notional budget for one position, in yuan. Fees are paid on top of it, not out of it."""
    min_periods: int = Field(ge=MINIMUM_PORTFOLIO_PERIODS, le=MAXIMUM_PORTFOLIO_PERIODS)
    """The fewest *measured* periods a series needs before this study calls anything a summary."""

    @property
    def direction(self) -> FactorDirection:
        return self.definition.direction

    @property
    def factor_id(self) -> str:
        return self.definition.factor_id

    @property
    def minimum_cross_section(self) -> int:
        """`group_count * min_securities_per_group`: the floor under a period's sample."""
        return self.group_count * self.min_securities_per_group


class QuantilePortfolioSummary(BaseModel):
    """A series of periods at one tier and horizon, reduced to per-group and spread statistics.

    A pydantic model rather than a dataclass, unlike the per-position carriers above, and
    `ICSummary`'s cost argument decides it the same way: there is one of these per study rather
    than one per `(security, as_of)`, so what is wanted at that scale is the validator.

    Every statistic is over the **measured** periods only. Non-measured periods contribute nothing
    and are not zeros; `measured_count` beside `len(as_ofs)` is what says how many were dropped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tier: FactorTier
    factor_id: str = Field(min_length=1, max_length=64)
    direction: FactorDirection
    group_count: int = Field(ge=MINIMUM_PORTFOLIO_GROUPS)
    horizon_sessions: int = Field(ge=1)
    coverage: SeriesCoverage
    as_ofs: tuple[datetime, ...]
    """Every `as_of` offered to this summary, in ascending order -- not only the measured ones.

    Carried rather than counted for `ICSummary.as_ofs`' reason: two series over disjoint years
    share a count and nothing else, so a count cannot say that two summaries are comparable.
    """
    measured_count: int = Field(ge=0)
    group_mean_net_returns: tuple[float, ...]
    """Each group's mean per-period **net** return, in raw-value ascending order, or `()`."""
    group_mean_gross_returns: tuple[float, ...]
    """The same means before any fee, so the elementwise difference is the mean cost drag."""
    mean_spread: float | None
    """The mean **oriented** `long_short_spread`. Positive means the factor worked."""
    stdev_spread: float | None
    """The **sample** standard deviation (`n - 1`) of the oriented spreads, for `ICSummary`'s
    reason: the measured periods are a sample of the periods this factor might be used in."""
    spread_ir: float | None
    """`mean_spread / stdev_spread`, or `None` when there is no dispersion to divide by.

    `None` rather than `math.inf`, which is `factor_ic`'s deliberate divergence from
    `EventStudy.t_statistic` and is inherited here: `inf` discards the sign of a constant negative
    spread, and a non-finite number is what this repository's stored-observation validators refuse
    one plane over. It is **not** a t-statistic and no confidence interval is published beside it;
    see `overlapping_periods_are_not_compounded_into_a_cumulative_curve`.
    """
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    zero_count: int = Field(ge=0)
    hit_rate: float | None
    """`positive_count / measured_count`. A zero spread is in the denominator and in neither
    numerator bucket, because a zero is not evidence for the factor."""

    @field_validator("mean_spread", "stdev_spread", "spread_ir", "hit_rate")
    @classmethod
    def refuse_a_non_finite_statistic(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{value!r} is not a finite statistic")
        return value

    @model_validator(mode="after")
    def validate_the_statistics_match_the_coverage(self) -> Self:
        measured = self.coverage == "measured"
        for name in ("mean_spread", "stdev_spread", "hit_rate"):
            if (getattr(self, name) is None) == measured:
                raise ValueError(
                    f"coverage {self.coverage!r} carries {name} {getattr(self, name)!r}; exactly "
                    "the 'measured' code carries the statistics, and spread_ir is the one "
                    "exception -- it is None when the dispersion is zero"
                )
        if self.spread_ir is not None and not measured:
            raise ValueError(f"coverage {self.coverage!r} cannot carry a spread_ir")
        for name in ("group_mean_net_returns", "group_mean_gross_returns"):
            values: tuple[float, ...] = getattr(self, name)
            if measured != (len(values) == self.group_count):
                raise ValueError(
                    f"coverage {self.coverage!r} carries {len(values)} value(s) in {name} against "
                    f"a cut of {self.group_count}; exactly the 'measured' code carries one mean "
                    "per group, and a partial row would be a curve missing a quantile"
                )
            if any(not math.isfinite(value) for value in values):
                raise ValueError(f"{name} carries a non-finite mean")
        if self.measured_count > len(self.as_ofs):
            raise ValueError(
                f"{self.measured_count} periods were measured and {len(self.as_ofs)} as_ofs were "
                "offered; a series cannot measure an as_of it was not given"
            )
        signed = self.positive_count + self.negative_count + self.zero_count
        if signed != self.measured_count:
            raise ValueError(
                f"{signed} measured spreads were signed and {self.measured_count} were measured; "
                "every measured spread is positive, negative or zero"
            )
        if len(set(self.as_ofs)) != len(self.as_ofs) or list(self.as_ofs) != sorted(self.as_ofs):
            raise ValueError(
                "as_ofs must be distinct and ascending; a repeated as_of is one period counted "
                "twice, and an unordered tuple makes two identical studies compare unequal"
            )
        return self

    @property
    def group_mean_cost_drag(self) -> tuple[float, ...]:
        """Each group's mean gross return less its mean net return."""
        return tuple(
            gross - net
            for gross, net in zip(
                self.group_mean_gross_returns, self.group_mean_net_returns, strict=True
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Attempt:
    """What one pass of the execution policy over a cross section produced.

    Three results of one loop, returned together rather than accumulated into caller-owned
    mutable arguments, so that "no orders were placed" is representable as the absence of one of
    these rather than as three collections a reader has to check are all still empty.
    """

    held: dict[int, list[RoundTrip]]
    rejections: list[PositionRejection]
    counts: dict[str, int]

    @property
    def held_count(self) -> int:
        return sum(len(positions) for positions in self.held.values())


class QuantilePortfolioStudy:
    """Measure one period's grouped portfolios, and summarise a series of them.

    A class holding a `QuantilePortfolioSpec` and an `AShareExecutionPolicy`, so that the declared
    cut, the capital and -- above all -- the factor whose `direction` decides which group is long
    are fixed once for a study instead of being passed at each call with a chance to differ.
    `FactorICStudy`'s precedent.

    **The execution policy is a required argument and is not defaulted**, unlike
    `PortfolioSimulator`'s. It carries the `CostSchedule`, every rate of which moves every number
    this study reports, and `V2-P3-005` established that a decision which moves the answers is a
    decision the caller records making.
    """

    def __init__(self, spec: QuantilePortfolioSpec, *, execution: AShareExecutionPolicy) -> None:
        self._spec = spec
        self._execution = execution

    @property
    def spec(self) -> QuantilePortfolioSpec:
        return self._spec

    @property
    def execution(self) -> AShareExecutionPolicy:
        return self._execution

    def measure(
        self,
        cross_section: ICCrossSection,
        *,
        bars: Mapping[str, tuple[MarketBar, MarketBar]],
    ) -> PeriodPortfolio:
        """One `as_of`'s group returns, or the code that says why there are none.

        `cross_section` is `factor_ic`'s own -- build it with `raw_cross_section`,
        `processed_cross_section` or `neutralized_cross_section`. Sharing the input type is the
        point rather than a convenience: an IC and a spread computed over the same admitted set
        cannot be reconciled by pointing at different samples, and every rule about which coverage
        codes are admitted, what a refused label costs and how the census has to add up already
        exists there and is not restated here.

        `bars` is `subject -> (entry bar, exit bar)`, keyed by **`cross_section.pairs`** and not by
        the universe the cross section was built from -- a key with no admitted pair is refused
        rather than ignored, for the reason `_refuse_bars_that_are_not_this_periods` states. A
        missing key is a different matter and is the `unbarred` outcome, not a refusal. Both bars
        are the caller's, because this module reads no partition, and both are checked against the
        window's own `entry_day` and `exit_day`.

        Never raises for a property of the market. A cross section thinner than the declared cut,
        one whose values all tie, one whose ties leave a group empty and one the market emptied are
        all answers, because a loop over a year of `as_of`s has to keep going past them.
        """
        spec = self._spec
        pairs = cross_section.pairs
        self._refuse_bars_that_are_not_this_periods(cross_section, bars)

        coverage: PeriodCoverage = "measured"
        attempt: _Attempt | None = None
        groups: tuple[GroupReturn, ...] = ()

        scores = cross_section.scores
        if len(pairs) < spec.minimum_cross_section:
            coverage = "insufficient_sample"
        elif min(scores) == max(scores):
            coverage = "degenerate_scores"
        else:
            assignment = rank_groups(scores, group_count=spec.group_count)
            sizes = [assignment.count(index) for index in range(spec.group_count)]
            if min(sizes) < spec.min_securities_per_group:
                coverage = "unfillable_groups"
            else:
                attempt = self._hold(
                    pairs=pairs,
                    assignment=assignment,
                    bars=bars,
                    entry_day=cross_section.entry_day,
                )
                filled = [len(attempt.held[index]) for index in range(spec.group_count)]
                if min(filled) < spec.min_securities_per_group:
                    coverage = "unfillable_after_execution"
                else:
                    groups = tuple(
                        GroupReturn(group=index, holdings=tuple(attempt.held[index]))
                        for index in range(spec.group_count)
                    )
        return PeriodPortfolio(
            as_of=cross_section.as_of,
            tier=cross_section.tier,
            factor_id=spec.factor_id,
            direction=spec.direction,
            group_count=spec.group_count,
            horizon_sessions=cross_section.horizon.sessions,
            entry_day=cross_section.entry_day,
            exit_day=cross_section.exit_day,
            coverage=coverage,
            groups=groups,
            census=PortfolioCensus(
                offered_count=len(pairs),
                held_count=0 if attempt is None else attempt.held_count,
                excluded_by_outcome=tuple(
                    (code, 0 if attempt is None else attempt.counts[code])
                    for code in HOLDING_OUTCOME_ORDER
                    if code != "held"
                ),
                unattempted_count=len(pairs) if attempt is None else 0,
            ),
            source_census=cross_section.census,
            rejections=() if attempt is None else tuple(attempt.rejections),
        )

    def summarize(self, periods: Iterable[PeriodPortfolio]) -> QuantilePortfolioSummary:
        """A series' per-group means and the oriented spread's mean, dispersion, IR and hit rate.

        Refuses a series that is not one study -- a period from another factor, tier, direction,
        cut or horizon, or two periods at one `as_of`. Each of those is a malformed question rather
        than a thin sample: a "mean group return" over two cuts is the average of two different
        quantities, and one `as_of` counted twice weights a day by how many times a caller
        appended it.
        """
        ordered = sorted(periods, key=lambda period: period.as_of)
        if not ordered:
            raise FactorPortfolioError(
                "a quantile summary needs at least one period; an empty series satisfies every "
                "per-period check vacuously and would report a coverage code about nothing"
            )
        self._refuse_periods_that_are_not_one_study(ordered)
        head = ordered[0]
        as_ofs = tuple(period.as_of for period in ordered)
        measured = [period for period in ordered if period.coverage == "measured"]
        spreads = [
            spread
            for spread in (period.long_short_spread for period in ordered)
            if spread is not None
        ]
        positive = sum(1 for value in spreads if value > 0.0)
        negative = sum(1 for value in spreads if value < 0.0)
        zero = len(spreads) - positive - negative
        if len(measured) < self._spec.min_periods:
            return QuantilePortfolioSummary(
                tier=head.tier,
                factor_id=head.factor_id,
                direction=head.direction,
                group_count=head.group_count,
                horizon_sessions=head.horizon_sessions,
                coverage="insufficient_periods",
                as_ofs=as_ofs,
                measured_count=len(measured),
                group_mean_net_returns=(),
                group_mean_gross_returns=(),
                mean_spread=None,
                stdev_spread=None,
                spread_ir=None,
                positive_count=positive,
                negative_count=negative,
                zero_count=zero,
                hit_rate=None,
            )
        mean = statistics.fmean(spreads)
        deviation = statistics.stdev(spreads)
        return QuantilePortfolioSummary(
            tier=head.tier,
            factor_id=head.factor_id,
            direction=head.direction,
            group_count=head.group_count,
            horizon_sessions=head.horizon_sessions,
            coverage="measured",
            as_ofs=as_ofs,
            measured_count=len(measured),
            group_mean_net_returns=tuple(
                statistics.fmean([period.group_net_returns[index] for period in measured])
                for index in range(head.group_count)
            ),
            group_mean_gross_returns=tuple(
                statistics.fmean([period.group_gross_returns[index] for period in measured])
                for index in range(head.group_count)
            ),
            mean_spread=mean,
            stdev_spread=deviation,
            spread_ir=None if deviation == 0.0 else mean / deviation,
            positive_count=positive,
            negative_count=negative,
            zero_count=zero,
            hit_rate=positive / len(spreads),
        )

    def _hold(
        self,
        *,
        pairs: Sequence[ICObservationPair],
        assignment: Sequence[int],
        bars: Mapping[str, tuple[MarketBar, MarketBar]],
        entry_day: date,
    ) -> _Attempt:
        """Run both legs of every admitted security through the policy, counting what did not fill.

        The order is buy then sell and it is not interchangeable: a sell whose buy was rejected is
        a position that never existed, and asking the policy about it would produce a verdict about
        an order nobody would have placed. `position_open_date` is the entry session, which is what
        makes the T+1 rule a live input rather than a defaulted one -- it cannot bind, because
        every constructible window exits at least one session after it enters, and it is passed so
        that a window which ever could would be refused rather than filled.
        """
        held: dict[int, list[RoundTrip]] = {index: [] for index in range(self._spec.group_count)}
        rejections: list[PositionRejection] = []
        counts: dict[str, int] = {code: 0 for code in HOLDING_OUTCOME_ORDER if code != "held"}
        for pair, group in zip(pairs, assignment, strict=True):
            subject = pair.subject
            offered = bars.get(subject)
            if offered is None:
                counts["unbarred"] += 1
                rejections.append(
                    PositionRejection(
                        subject=subject,
                        outcome="unbarred",
                        reason=(
                            "no entry/exit bar pair was offered for this security, so no order "
                            "could be judged against one"
                        ),
                    )
                )
                continue
            entry_bar, exit_bar = offered
            quantity = position_quantity(capital=self._spec.position_capital, market=entry_bar)
            if quantity == 0:
                counts["below_board_minimum"] += 1
                rejections.append(
                    PositionRejection(
                        subject=subject,
                        outcome="below_board_minimum",
                        reason=(
                            f"a position capital of {self._spec.position_capital} does not reach "
                            f"the {entry_bar.board} board's minimum of "
                            f"{BOARD_MINIMUM_QUANTITY[entry_bar.board]} shares at a close of "
                            f"{entry_bar.close}"
                        ),
                    )
                )
                continue
            entry_fill = self._execution.execute(
                ExecutionRequest(side="buy", quantity=quantity), entry_bar
            )
            if entry_fill.status == "rejected":
                counts["rejected_entry"] += 1
                rejections.append(
                    PositionRejection(
                        subject=subject,
                        outcome="rejected_entry",
                        reason=entry_fill.reason or "the execution policy rejected the buy",
                    )
                )
                continue
            exit_fill = self._execution.execute(
                ExecutionRequest(side="sell", quantity=quantity, position_open_date=entry_day),
                exit_bar,
            )
            if exit_fill.status == "rejected":
                counts["rejected_exit"] += 1
                rejections.append(
                    PositionRejection(
                        subject=subject,
                        outcome="rejected_exit",
                        reason=exit_fill.reason or "the execution policy rejected the sell",
                    )
                )
                continue
            held[group].append(
                RoundTrip(
                    subject=subject,
                    group=group,
                    quantity=quantity,
                    entry_fill=entry_fill,
                    exit_fill=exit_fill,
                    gross_return=pair.forward_return,
                    unpublished_band_legs=sum(
                        0 if bar.has_published_limits else 1 for bar in (entry_bar, exit_bar)
                    ),
                )
            )
        return _Attempt(held=held, rejections=rejections, counts=counts)

    def _refuse_bars_that_are_not_this_periods(
        self,
        cross_section: ICCrossSection,
        bars: Mapping[str, tuple[MarketBar, MarketBar]],
    ) -> None:
        """Refuse a bar for the wrong session, the wrong security, or one with no admitted pair.

        `factor_ic._rows`' rule at this module's own boundary: a bar from another session against
        this window's label is a plausible number from the wrong rows, and it is refused rather
        than filtered because a caller who passed the wrong partition wants to be told rather than
        to get a shorter answer.

        **`bars` is keyed to `ICCrossSection.pairs` and not to the universe the cross section was
        built from**, and the message says so rather than guessing why a key is extra.
        `ICCrossSection` carries the admitted pairs and a *census* of everything else, not the
        excluded subjects themselves, so a security the tier's coverage code declined and a
        security nobody ever scored are indistinguishable here -- and a message that named the
        second would be prose pointing the wrong way on half its occurrences. The rule a caller
        follows is one sentence: build the bar pairs from `cross_section.pairs`.
        """
        subjects = {pair.subject for pair in cross_section.pairs}
        unknown = sorted(set(bars) - subjects)
        if unknown:
            raise FactorPortfolioError(
                f"{unknown} carries a bar pair and no admitted, labelled pair at "
                f"{cross_section.as_of.isoformat()}; either the cross section never scored it or "
                "its coverage code was not admitted, and this boundary cannot tell those apart -- "
                "key the bars from cross_section.pairs. Ignoring the extra keys would hide a "
                "caller that read its two sides from two different universes"
            )
        for subject in sorted(subjects & set(bars)):
            entry_bar, exit_bar = bars[subject]
            for bar, day, leg in (
                (entry_bar, cross_section.entry_day, "entry"),
                (exit_bar, cross_section.exit_day, "exit"),
            ):
                if bar.subject != subject:
                    raise FactorPortfolioError(
                        f"the {leg} bar filed under {subject} names {bar.subject}; a position "
                        "priced from another security's bar is a number from the wrong rows"
                    )
                if bar.trade_date != day:
                    raise FactorPortfolioError(
                        f"{subject}'s {leg} bar is dated {bar.trade_date.isoformat()} and this "
                        f"window's {leg} session is {day.isoformat()}; a fill at another "
                        "session's close prices a move the label never measured"
                    )

    def _refuse_periods_that_are_not_one_study(self, ordered: Sequence[PeriodPortfolio]) -> None:
        """Refuse a series mixing factors, tiers, directions, cuts, horizons or `as_of`s."""
        head = ordered[0]
        for period in ordered:
            mismatched = [
                f"{name}={getattr(period, name)!r} against {getattr(head, name)!r}"
                for name in ("tier", "direction", "factor_id", "group_count", "horizon_sessions")
                if getattr(period, name) != getattr(head, name)
            ]
            if mismatched:
                raise FactorPortfolioError(
                    f"the period at {period.as_of.isoformat()} reports {mismatched}; a quantile "
                    "summary is one factor on one tier under one cut at one horizon, and a mean "
                    "over two of any of them is the average of two different quantities"
                )
            if period.factor_id != self._spec.factor_id:
                raise FactorPortfolioError(
                    f"the period at {period.as_of.isoformat()} was measured for factor "
                    f"{period.factor_id!r} and this study declares {self._spec.factor_id!r}"
                )
        stamps = [period.as_of for period in ordered]
        if len(set(stamps)) != len(stamps):
            duplicates = sorted({stamp.isoformat() for stamp in stamps if stamps.count(stamp) > 1})
            raise FactorPortfolioError(
                f"{duplicates} appears more than once in this series; one as_of is one period, "
                "and counting it twice weights a day by how often a caller appended it"
            )
