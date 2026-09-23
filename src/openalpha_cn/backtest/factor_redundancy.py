"""Correlation and redundancy (`V2-P3-008`): what two factors share, and why they share it.

Nineteen factors ship, so there are **171** pairs, and the question this module exists to answer
is not "which pairs correlate" -- that is a sort -- but **"which pairs correlate for a reason a
reader should act on"**. Three sources of a high correlation are separated here rather than
pooled, because the remedy differs for each and a single number cannot tell them apart:

1. **Arithmetic.** The two values are bound by an exact algebraic relation. The correlation is a
   consequence of the definitions and carries no information about the market at all. Reported as
   `arithmetic`, and only ever after the relation has been **evaluated on the data in hand**.
2. **Shared inputs.** The two read some of the same panel columns. That is a structural fact,
   computable from `FactorDefinition.required_fields` with no data whatsoever, and it is neither
   necessary nor sufficient for a correlation -- see below, where both directions are measured on
   the shipped twenty.
3. **The market.** Everything else. This is the only one of the three that is a finding.

`backtest/event_study.py` is the shape and `backtest/factor_ic.py` is the neighbour: a
standard-library leaf over `domain/`, storing nothing, reading no partition, with the *rules* as
the deliverable. It re-uses `factor_ic`'s `average_ranks`, `_pearson` and `TIER_ADMITTED_CODES`
rather than restating them; see "Layering" below for why that is an import here and was two
implementations there.

## The three things one could correlate, and why all three are here

`V2-P3-008` names "redundancy" and the word has three readings, which mean different things and
have different remedies. All three are computed, and a caller picks:

- **Factor values, cross-sectionally** (`correlate_cross_section` with `method="pearson"`). Do the
  two factors assign proportional scores to the market on one day? Sensitive to the scale of
  both, so a single name at ten times everybody's value moves it.
- **Factor ranks, cross-sectionally** (`method="spearman"`). Do the two order the market the same
  way? This is the reading that matters for anything that *selects* on a factor, because a
  long/short book is built from an ordering and not from a level -- and it is invariant to the
  monotone transforms `factor_transform.py` applies, so a raw pair and a rank-standardized pair
  give the same answer.
- **IC series, over time** (`correlate_ic_series`). Do the two factors *work on the same days*?
  This is a different question from either of the above. Two factors can be cross-sectionally
  near-orthogonal and still have IC series that rise and fall together -- both long the same
  market state -- and a book holding both would then be undiversified in exactly the period it
  mattered. That direction is measured on a fixture whose cross-sectional rank correlation is
  **exactly 0** and whose IC series correlate above 0.9.

  **The one implication that runs the other way is exact rather than "high implies high".** If two
  factors' rank vectors are *identical* at an `as_of` -- which is what `undeclared_lockstep` under
  `spearman` reports -- then their rank ICs at that `as_of` are identical whatever the returns
  did, because a rank IC is a function of the ranks alone. Nothing weaker than identical ranks is
  claimed: a small reordering among the extremes can move an IC materially, so "almost the same
  ordering" implies nothing about the ICs and this module does not say it does.
  `test_two_factors_in_rank_lockstep_have_the_same_rank_ic_whatever_the_returns_did` drives the
  exact version with `==`.

Pearson and Spearman are both offered, as `factor_ic.ICMethod` -- the same closed two-member set,
imported rather than re-spelled, so a report can put an IC and a redundancy figure in one table
under one column header. `method` is declared on the spec with no default, for
`FactorICSpec.method`'s reason: it moves the answers.

## The sign is oriented by both declarations, and the magnitude is what redundancy is judged on

`FactorICSpec.orient` reads one factor's `direction`; a pair reads two, and the product decides:

    oriented = raw * s(left) * s(right)      s = +1 higher_is_better, -1 lower_is_better

`raw_correlation` is the correlation as computed. `oriented_correlation` is that number signed so
that **positive means the two factors make the same bet**. `return_vol_60` is `lower_is_better`
and `momentum_20_sessions` is `higher_is_better`, so a raw `-0.3` between them is an oriented
`+0.3`: the low-volatility names are the high-momentum names, and the two agree. Without the
orientation a report comparing a `lower_is_better` pair against a mixed pair would be comparing
two different conventions, which is the mistake `FactorICSpec.orient` exists to prevent one plane
down, arriving here with two declarations instead of one.

**Redundancy is judged on the magnitude**, which the orientation does not move, so the verdict is
direction-free and the sign is informative rather than load-bearing. Two factors at oriented
`-0.95` are as redundant as two at `+0.95` -- one of them is the other one backwards -- and the
sign is what says which.

## What "redundant" means here: a declared threshold, and one boundary that is not declared

**`RedundancySpec.redundancy_threshold` has no default and this module refuses to choose one.**
That is `FactorICSpec.min_securities`' rule and it is here for a sharper reason: the number that
would make nineteen factors look well-separated is a number chosen after seeing them, and this
repository has taken a Critical finding on exactly that shape. What is bounded is the
*declaration*: `gt=0` because a threshold of zero calls every pair redundant (`|r| >= 0` always),
`le=1` because 1.0 is attainable and is the strictest legal declaration -- "only call a pair
redundant when it is arithmetically indistinguishable".

**The lockstep boundary is not declared, because it is not a choice.** `LOCKSTEP_DECIMAL_PLACES`
is 15 and `_is_lockstep` asks `round(abs(r), 15) == 1.0`, which is `factor_ic`'s own measured
rule for "this correlation is one, at the last bit" -- that module records eight of ten random
two-name pairs coming out `0.9999999999999998`. Measured again here in both directions, because a
boundary that only fires is a boundary nobody has seen decline:

- 200 random vectors and their exact affine images: `abs(r) == 1.0` exactly on **143** of 200 and
  `round(abs(r), 15) == 1.0` on **200** of 200. The same 200 against their exact *monotone* images
  through `x**3 + x`: rank correlation exactly 1.0 on **150** of 200, at 15 places on **200** of
  200. (Both counts were 149 and 153 until `V2-P5-062` made `_pearson` exactly rounded; the two
  interpreters this repository supports disagreed on them before that, which is how it surfaced.)
  So plain `== 1.0` misses a quarter of the identities it exists to find, which is why the
  boundary is a rounding and not an equality.
- 200 pairs of a vector against itself plus `N(0, 0.001)` noise on a unit scale -- two genuinely
  different numbers that agree to six figures -- reach a largest `abs(r)` of
  `0.9999998220801101` and clear the boundary **0** times.

Every one of those five numbers is asserted by value in
`tests/unit/backtest/test_factor_redundancy.py::
test_the_lockstep_boundary_separates_an_exact_image_from_a_very_close_one`, off a declared seed,
so this paragraph fails with the code rather than drifting away from it.

So `undeclared_lockstep` fires when two factors are one number in a monotone disguise and nothing
declared says why. That is the audit this module is for: `V2-P3-012` skipped five sessions
precisely so that a shipping pair would not be bound by an identity, and this is the check that
fails closed if some later pair is.

## Arithmetic identity: declared, and then measured against the data

**A shared column is computable and an identity is not**, and the two halves need different
machinery.

`SharedInputs` is computed from `FactorDefinition.required_fields` alone -- no data, no
partition, no `as_of`. It reports the shared qualified columns, the shared datasets and one of
four codes. The shipped twenty, measured:

| over the 190 pairs | count |
|---|---|
| share at least one qualified column (`daily.close`, `daily_basic.total_mv`, ...) | **45** |
| share at least one *dataset* and no column | 31 (76 share a dataset) |
| declare **identical** `required_fields` | **16** |

`V2-P3-017`'s twentieth factor moved the first two and not the third, which is the reading worth
keeping: EPcut shares `daily_basic.total_mv` with the rest of the value family and shares its
numerator column with nobody, so it is three more `overlapping_inputs` pairs and no new identical
one. A factor that had merely renamed `earnings_yield_ttm` would have shown up in the last row.

Two of those figures decide how much weight a shared column can carry, and they run in opposite
directions:

- **Shared inputs are not sufficient.** `momentum_120_sessions` and `reversal_5_sessions` declare
  *identical* `required_fields` -- both read `daily.close` and `daily.pre_close` and nothing else
  -- and `V2-P3-012` built them so that the sessions each actually multiplies are **disjoint**.
  The strongest structural signal available is carried by a pair the repository deliberately
  separated, so "these two share a column" can never be the conclusion.
  `test_identical_declared_inputs_are_not_evidence_that_two_factors_agree` puts **two** pairs
  under that one code -- a shipped momentum against the reversal at `distinct`, and an exact
  monotone image of the reversal at `undeclared_lockstep` -- so the code is measured not to decide
  the verdict rather than merely observed not to.
- **Shared inputs are not necessary.** `return_on_equity_ttm` and `accruals_ttm` share **no**
  qualified column at all: the first reads `income.n_income_attr_p` and the second
  `income.n_income`, two different profit lines that `V2-P3-011` measured giving different growth
  rates on 139 of 181 comparable pairs. They share `income` and `balancesheet` as *datasets*,
  which is why the dataset overlap is reported beside the column overlap and is not confused with
  it -- 72 pairs against 42, so the coarser reading nearly doubles the count and would call the
  value and quality families related by construction when what they share is a filing.

`FactorIdentity` is the other half. An identity is **declared** -- a code, the factor keys it
relates, a residual that is exactly zero when it holds, and its own tolerance -- and this module
**evaluates it on the values in hand** before any pair is called `arithmetic`. `verify_identity`
answers `verified`, `refuted` or `unevaluable` and always reports `max_abs_residual`, so a
declaration the data contradicts is a loud finding rather than a silent pass. That is the whole
of the defence against the failure mode this repository has taken thirteen Critical findings on:
a declared safety property that nothing measures.

**The identity `V2-P3-012` named is driven here, in both directions.** `1 + m20 == (1 + m15) *
(1 + r5)` holds for an *unskipped* 20-session momentum and is why the shipped one skips five
sessions. `tests/unit/backtest/test_factor_redundancy.py::
test_an_unskipped_momentums_identity_is_verified_and_the_shipped_pair_refutes_it` declares that
identity once and evaluates it twice: against the unskipped variant it is `verified` at a residual
of the last bits, and against the **shipped** `momentum_20_sessions` and `reversal_5_sessions` on
the same price paths it is `refuted` by orders of magnitude, and the pair falls through to the
empirical ladder. So this module reports 012's judgement as a measurement rather than inheriting
it as prose.

**No identity ships.** `RedundancyStudy` takes its `identities` as a required keyword argument
with no default, so declaring none is a declaration and not an omission, and a module-level
registry that could quietly empty itself out -- `FactorRegistry`'s own refused shape -- does not
exist. Among the nineteen there is no exact two-member relation to declare: the one the growth
family comes closest to, `revenue_yoy_acceleration == revenue_yoy - YoY(P-4)`, has a third term
that is not a shipped factor.

## Cross-tier is the same machinery, and the number it produces has a reading

A pair is two `FactorVector`s and each carries its own tier, so the left and right sides may be
the **same factor on two tiers**. `correlate_cross_section(raw_vector, neutralized_vector)` is
then "how much of this factor survived the neutralisation": both sides carry one `direction`, so
the orientation cancels and `oriented_correlation == raw_correlation` exactly, and the number is
the rank agreement between the factor and its residual after industry and size were regressed
out. Near 1.0 means the neutralisation removed almost nothing; a lower number is the size of what
it removed, on the cross section it was removed from.

`_refuse_a_pair_that_is_neither_two_factors_nor_two_tiers` refuses the one shape that is not a
question: the same factor on the same tier against itself, whose correlation is 1.0 by
construction and would be an `undeclared_lockstep` finding about nothing.

## No significance, on three separate grounds

`factor_ic` publishes no t-statistic and no confidence interval because a daily IC series over
overlapping windows is not a series of independent draws. **The same question has to be asked
again here and the answer is no three times over, for three different reasons**, which is why
there are three limitation codes rather than one borrowed:

- **A cross-sectional correlation's `n` is not a sample size.** The securities in one cross
  section are not independent draws -- they share a market factor, an industry and a size
  exposure, which is precisely what `panel_neutralization` regresses out. A p-value computed
  against `n = 5,534` would be astronomically small for any pair whatever, and would be measuring
  the market's own commonality.
- **A series of cross-sectional correlations is autocorrelated.** Factor exposures are
  persistent: `momentum_120_sessions` moves 1/125th of its window per session. So `stdev_
  correlation` understates the sampling error of `mean_correlation` for the same structural
  reason `factor_ic`'s does, one plane over.
- **An IC-series correlation inherits the overlapping windows exactly.** Both sides are
  `ICPoint.ic` series over the same as_ofs at one horizon, and `KNOWN_IC_LIMITATIONS`'
  `an_ic_series_over_overlapping_windows_is_autocorrelated` applies to both of them at once.

What is published instead is what `factor_ic` publishes: the number, the sample it was taken
over, and the census of what did not enter.

## The sample floor is four, and the four is arithmetic

`MINIMUM_REDUNDANCY_SECURITIES` is **4**, one above `MINIMUM_IC_SECURITIES`, and the extra one is
a property of the arithmetic rather than a caution. At `n = 3` a rank correlation of two untied
cross sections can only be `+-0.5` or `+-1`: there are six permutations of three ranks and
`1 - 6 * sum(d**2) / 24` takes four values over them. So **no threshold at or below 0.5 can
distinguish anything at `n = 3`** -- every pair is redundant -- and every threshold above 0.5
admits only the perfectly ordered pair. `n = 4` is the first size at which a rank correlation of
exactly 0 is attainable, and `tests/unit/backtest/test_factor_redundancy.py::
test_three_names_cannot_rank_correlate_below_a_half_and_four_names_can_reach_zero` enumerates all
of both rather than sampling them.

Pearson attains 0 at `n = 3` (`(0, 1, 2)` against `(0, 1, 0)`), so the floor is decided by the
weaker of the two methods this module offers -- a floor that held for one of them would be a
floor that does not hold.

`min_as_ofs` is `MINIMUM_IC_AS_OFS`, imported rather than re-chosen: a sample standard deviation
of one number does not exist, and a summary over one `as_of` would be a dispersion about nothing.

## The neutralised tier's timestamps, restated because it is read here too

A neutralised residual is stamped at the instant its **build** was run, which `V2-P4-026` freed
from the year end without changing the rule itself. `factor_ic` states the whole of it under
`a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule` and nothing about it is
different here, but a cross-tier correlation is *more* exposed to it than an IC is: the raw side
of such a pair is stamped at its input session's publication and the neutralised side at its
build, so wherever the two schedules differ the two sides of one number were stamped under two
different rules. `KNOWN_REDUNDANCY_LIMITATIONS` carries that as its own entry rather than
pointing at the neighbour's, because the finding is about the pairing and not about either side.

## Layering, and why there is no numpy here

`backtest/` over `domain/`, standard library only, and ADR-0003's `V2-P3-005` section explicitly
declined to let its measurement carry over to "a rolling covariance matrix across `V2-P3-008`'s
whole factor set". So it was measured again, at ADR-0002's whole-market cross section of 5,534
securities and the then-shipped 19 factors' 171 pairs: **0.860 s** for the whole matrix at
`spearman`
and 0.398 s at `pearson`, against one pair at 4.97 ms through this module and 3.49 ms of bare
arithmetic (which reproduces that section's 3.56 ms).

The measurement found one thing worth carrying into the design. Ranking each factor **once** and
correlating the stored ranks is 0.137 s, 6.3x faster -- and it is **wrong** whenever two factors
are admitted for different subjects. A rank is a position within a set, so the ranks of a subset
are not the subset of the ranks; restricting a full-market rank vector to a 25-of-40 intersection
and correlating disagreed with the honest answer on **200 of 200** random trials, by as much as
0.100. This module therefore ranks inside each pair's own intersection and pays the 0.860 s, which
is the number ADR-0003's update section records -- along with the honest comparison that a year of
daily whole-market matrices is 210 s, which is **not** the 630x headroom `V2-P3-005` reported.

`average_ranks` and `_pearson` are **imported** from `factor_ic` rather than reimplemented. That
module keeps a second copy of `panel_factors._standardize_rank`'s tie rule because the edge from
`backtest/` into `panel_factors` would reach DuckDB; there is no such boundary between two
modules of `backtest/`, so a second copy here would be unforced duplication of a rule this
repository has already paid to pin element-wise.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIERS,
    MINIMUM_IC_AS_OFS,
    TIER_ADMITTED_CODES,
    TIER_COVERAGE_ORDER,
    FactorTier,
    ICMethod,
    ICPoint,
    _pearson,
    average_ranks,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorDirection
from openalpha_cn.domain.time import ensure_aware


class FactorRedundancyError(ValueError):
    """Raised for a malformed pair, vector, identity or series.

    A `ValueError` subclass for `FactorICError`'s reason: every call site that already writes
    `except ValueError` keeps catching it unchanged. It is deliberately **not** what a thin
    intersection or an all-tied side produces -- those are facts about the day, they are reported
    as `PairCoverage` codes, and a loop over a year of as_ofs has to keep going past them.
    """


RedundancyVerdict = Literal[
    "arithmetic",
    "undeclared_lockstep",
    "redundant",
    "distinct",
]
"""Why a pair's correlation is what it is, in the order `_verdict` decides them.

- **`arithmetic`** -- a declared `FactorIdentity` naming both factors was **evaluated on these
  values** and its residual came in within its own tolerance. The correlation is a consequence of
  the two definitions and is not evidence about the market. Decided first, because the questions
  below are questions about a finding and this is not one.
- **`undeclared_lockstep`** -- `round(abs(r), 15) == 1.0` and no identity explains it. Two
  factors that are one number in a monotone disguise, with nothing declared saying why. This is
  the code that fails closed on the hazard `V2-P3-012` avoided by construction.
- **`redundant`** -- `abs(r)` is at or above the declared `redundancy_threshold`.
- **`distinct`** -- below it.

Closed rather than a float plus a boolean, because `V2-P3-014`'s report groups by it and a fifth
spelling of "the same" would silently become a group of one.
"""

REDUNDANCY_VERDICT_CODES: Final[frozenset[str]] = frozenset(get_args(RedundancyVerdict))

REDUNDANCY_VERDICT_ORDER: Final[tuple[RedundancyVerdict, ...]] = get_args(RedundancyVerdict)
"""The declared order, which is the order a census lays its cells out in."""

PairCoverage = Literal[
    "measured",
    "insufficient_sample",
    "degenerate_left",
    "degenerate_right",
]
"""Whether a pair produced a correlation at this `as_of`, and if not, **why**.

`factor_ic.ICCoverage`'s shape with the two degeneracies renamed for the two sides of a pair,
and split for that module's reason: which side collapsed is a different finding with a different
remedy, and one `degenerate` code would make a factor that scored the whole market with one value
indistinguishable from its partner doing so. The left side is checked first, and the precedence
is stated rather than incidental -- a pair in which both sides tie is two defects, and naming the
left one puts the report on the side the caller passed first.
"""

PAIR_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(PairCoverage))

PAIR_COVERAGE_ORDER: Final[tuple[PairCoverage, ...]] = get_args(PairCoverage)

SharedInputCode = Literal[
    "identical_inputs",
    "overlapping_inputs",
    "shared_dataset_only",
    "disjoint_inputs",
]
"""How two factors' declared `required_fields` relate, as a structural fact and nothing more.

- **`identical_inputs`** -- the same set of qualified columns. Sixteen of the shipped 171 pairs,
  including `momentum_120_sessions` against `reversal_5_sessions`, which `V2-P3-012` built to be
  disjoint in the sessions each multiplies. So this code is the loudest structural signal
  available and it is **not** evidence of redundancy; see this module's docstring.
- **`overlapping_inputs`** -- a non-empty proper intersection of the qualified columns. The three
  value factors against each other are here: all three divide by `daily_basic.total_mv`.
- **`shared_dataset_only`** -- no column in common, but at least one dataset. Weaker and worth its
  own code rather than being folded into `disjoint_inputs`: two factors reading one filing
  partition inherit the same point-in-time behaviour and the same refusals, so their *coverage*
  moves together even when their values do not. `return_on_equity_ttm` and `accruals_ttm` are
  here -- they share `income` and `balancesheet` and no column at all.
- **`disjoint_inputs`** -- nothing in common. `turnover_60` against every filing factor.

Computed by `shared_inputs` from the definitions alone. There is no `as_of` in it, no partition
behind it and no float in it, which is exactly what makes it usable as the thing an empirical
correlation is read *against*.
"""

SHARED_INPUT_CODES: Final[frozenset[str]] = frozenset(get_args(SharedInputCode))

SHARED_INPUT_ORDER: Final[tuple[SharedInputCode, ...]] = get_args(SharedInputCode)

IdentityCoverage = Literal["verified", "refuted", "unevaluable"]
"""What happened when a declared identity was evaluated against the values in hand.

- **`verified`** -- every subject carrying all of the identity's members produced a residual
  within the identity's declared tolerance.
- **`refuted`** -- at least one did not. A declaration the data contradicts, reported with its
  `max_abs_residual` so the size of the contradiction is visible rather than only its existence.
- **`unevaluable`** -- no subject carried all of the members, so nothing was evaluated. Its own
  code and never folded into `verified`: an identity nobody could check is not an identity that
  held, and this is the exact vacuity `FactorRegistry` refuses an empty registry for.
"""

IDENTITY_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(IdentityCoverage))

IDENTITY_COVERAGE_ORDER: Final[tuple[IdentityCoverage, ...]] = get_args(IdentityCoverage)

SummaryCoverage = Literal["measured", "insufficient_as_ofs"]
"""Whether a series of points produced summary statistics. `ICStabilityCoverage`'s two members
for its reason: the per-`as_of` degeneracies were decided one level down and arrive here as
points that are simply not `measured`, so what is left is a count."""

SUMMARY_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(SummaryCoverage))

MINIMUM_REDUNDANCY_SECURITIES: Final[int] = 4
"""The floor under `RedundancySpec.min_securities`, and it is arithmetic rather than taste.

One above `factor_ic.MINIMUM_IC_SECURITIES`, and the extra one is what a *threshold* needs that a
correlation does not. At `n = 3` there are six permutations of three ranks and
`1 - 6 * sum(d ** 2) / 24` takes exactly four values over them, so an untied rank correlation of
three securities is `+-0.5` or `+-1` and **nothing else**. Every threshold at or below 0.5 then
calls every pair redundant and every threshold above it admits only the perfectly ordered pair --
in neither case is the declaration deciding anything about the factors. `n = 4` is the first size
at which a rank correlation of exactly 0 is attainable.

Enumerated rather than sampled: `tests/unit/backtest/test_factor_redundancy.py::
test_three_names_cannot_rank_correlate_below_a_half_and_four_names_can_reach_zero` walks all six
permutations at `n = 3` and all twenty-four at `n = 4`.

The binding method is `spearman`. Pearson reaches 0 at `n = 3` -- `(0, 1, 2)` against `(0, 1, 0)`
-- and a floor that held for one of the two methods this module offers would be no floor at all.
"""

MAXIMUM_REDUNDANCY_SECURITIES: Final[int] = 10_000
"""The range check on the declared floor, `factor_ic.MAXIMUM_IC_SECURITIES`' own bound and for
its stated reason: a floor a caller can never reach is a declarable and auditable choice, while
refusing it would hard-code today's listing count into a contract."""

MAXIMUM_REDUNDANCY_AS_OFS: Final[int] = 10_000
"""The range check on `min_as_ofs`, `factor_ic.MAXIMUM_IC_AS_OFS`' bound unchanged."""

LOCKSTEP_DECIMAL_PLACES: Final[int] = 15
"""How many places `abs(r)` is rounded to before being compared with 1.0. Not a threshold.

`factor_ic.MINIMUM_IC_SECURITIES` measured that a correlation that is one *by construction* comes
out `0.9999999999999998` on eight of ten random two-name pairs, and states its own claim as
`round(abs(r), 15) == 1.0` for exactly that reason. This is that rule, re-used rather than
re-chosen, and re-measured on this plane in both directions -- 200 of 200 exact affine and exact
monotone images clear it, 0 of 200 vectors perturbed by `N(0, 0.001)` noise do. See this module's
docstring for the figures and `tests/unit/backtest/test_factor_redundancy.py::
test_the_lockstep_boundary_separates_an_exact_image_from_a_very_close_one` for the drive.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class RedundancyLimitation:
    """One named boundary on what a stored redundancy figure can be trusted to answer."""

    code: str
    detail: str


KNOWN_REDUNDANCY_LIMITATIONS: Final[tuple[RedundancyLimitation, ...]] = (
    RedundancyLimitation(
        code="a_cross_sections_security_count_is_not_a_sample_size",
        detail=(
            "This module publishes no p-value and no confidence interval for a cross-sectional "
            "correlation, and the reason is not the one factor_ic gives for its ICs. The "
            "securities in one cross section are not independent draws: they share a market "
            "factor, an industry and a size exposure, which is exactly what "
            "panel_neutralization regresses out and what neutralised_observation_batch exists "
            "for. A significance test against n = 5,534 would report an astronomically small "
            "p-value for any pair whatever and would be measuring the market's commonality "
            "rather than the pair's. What is published instead is the correlation, the "
            "sample_size it was taken over, and the census of what did not enter it."
        ),
    ),
    RedundancyLimitation(
        code="a_series_of_cross_sectional_correlations_is_autocorrelated",
        detail=(
            "Factor exposures are persistent -- momentum_120_sessions rolls one of its 125 "
            "declared sessions per day, and a filing factor's value does not move at all between "
            "two announcements -- so two adjacent as_ofs produce two nearly identical cross "
            "sections and therefore two nearly identical correlations. stdev_correlation is "
            "computed as a sample standard deviation over those, and it understates the sampling "
            "error of mean_correlation for the same structural reason factor_ic's stdev_ic "
            "understates its own. No t-statistic and no interval is published on top of it. "
            "De-overlapping would need a purge and a purge needs a train/test split, which "
            "nothing in this repository defines yet."
        ),
    ),
    RedundancyLimitation(
        code="an_ic_series_correlation_inherits_the_overlapping_windows_whole",
        detail=(
            "correlate_ic_series correlates two ICPoint.ic series measured at one horizon over "
            "one set of as_ofs. At horizon h two prediction days one session apart share h of "
            "the h + 1 sessions each window spans, so BOTH series are autocorrelated and "
            "KNOWN_IC_LIMITATIONS' an_ic_series_over_overlapping_windows_is_autocorrelated "
            "applies to each of them at once. The correlation between them is still the honest "
            "answer to 'did these two factors work on the same days'; what is unavailable is any "
            "statement about how surprising the number is."
        ),
    ),
    RedundancyLimitation(
        code="a_cross_tier_pair_correlates_one_point_in_time_side_against_one_snapshot_side",
        detail=(
            "THE CLAUSE 'AT OR AFTER THE YEAR'S LAST SESSION' WAS RETRACTED BY V2-P4-026 AND THE "
            "ENTRY IS NARROWER THAN IT WAS. factor_ic's "
            "a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule records that "
            "panel_neutralization.neutralized_observation_batch stamps all four clocks of every "
            "residual with the BUILD's as_of; what it no longer records, because it is no longer "
            "true, is that the build's as_of is forced to the year end. A single-tier "
            "correlation reads both sides under one rule and inherits that disclosure unchanged. "
            "A CROSS-TIER pair does not, and the exposure survives the retraction in a weaker "
            "form: correlate_cross_section of a raw vector against a neutralised one puts a side "
            "stamped at the instant its input session published against a side stamped at the "
            "instant its BUILD was run, and those two are the same only if the neutralisation "
            "was built on the same schedule the raw tier was. Where it was not -- a residual "
            "series built once at year end, which is what every artifact predating V2-P4-026 "
            "is -- 'how much of this factor survived the neutralisation' is answered at the "
            "build instant rather than at the one the caller asked for, and nothing in the "
            "stored rows distinguishes the two schedules. The residuals' CONTENT is clean either "
            "way -- each was regressed against the industry, the market capitalisation and the "
            "processed value of its own day -- so the number is not forward-contaminated; what "
            "can be lost is the reader's ability to date it."
        ),
    ),
    RedundancyLimitation(
        code="a_shared_column_is_neither_necessary_nor_sufficient_for_a_correlation",
        detail=(
            "SharedInputs is computed from FactorDefinition.required_fields and is a structural "
            "fact rather than a prediction, and both directions of its failure are measured on "
            "the shipped twenty. NOT SUFFICIENT: momentum_120_sessions and reversal_5_sessions "
            "declare identical required_fields -- 16 of the 190 pairs do -- and V2-P3-012 built "
            "them so the sessions each multiplies are disjoint. NOT NECESSARY: "
            "return_on_equity_ttm and accruals_ttm share no qualified column at all "
            "(income.n_income_attr_p against income.n_income), and V2-P3-011 measured those two "
            "columns giving different growth rates on 139 of 181 comparable pairs. A shared "
            "column therefore explains nothing on its own; it is the structure an empirical "
            "correlation is read against, and the arithmetic half of that reading is "
            "FactorIdentity, which required_fields cannot see."
        ),
    ),
    RedundancyLimitation(
        code="an_identity_is_declared_by_a_caller_and_only_its_refutation_is_automatic",
        detail=(
            "verify_identity evaluates a declared residual on the values in hand and reports "
            "verified, refuted or unevaluable with the max_abs_residual in every case, so a "
            "declaration the data contradicts fails loudly. What no part of this module can do "
            "is DISCOVER an identity nobody declared: there is no symbolic reading of an "
            "evaluator here, and 1 + m20 == (1 + m15) * (1 + r5) is not visible in either "
            "factor's required_fields. undeclared_lockstep is the backstop rather than the "
            "detector -- it fires when two factors are numerically one number, which every exact "
            "monotone identity implies, and stays silent for an identity that binds two factors "
            "loosely enough to leave their orderings apart."
        ),
    ),
)
"""What a stored redundancy figure does not answer, as a closed registry rather than as prose.

Every entry is bound to the suite by `tests/unit/test_known_limitation_registries.py`, which
requires each `code` to appear as a string literal in *executable* test code.
"""

REDUNDANCY_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_REDUNDANCY_LIMITATIONS
)


def _direction_sign(direction: FactorDirection) -> float:
    """`+1.0` for `higher_is_better` and `-1.0` for `lower_is_better`.

    Exact under IEEE multiplication, so `RedundancyPoint`'s validator asserts the orientation
    with `==` rather than with a tolerance -- `FactorICSpec.orient`'s property, needed twice here
    because a pair carries two declarations.
    """
    return 1.0 if direction == "higher_is_better" else -1.0


def _is_lockstep(correlation: float) -> bool:
    """Whether `correlation` is `+-1` at the last bit. See `LOCKSTEP_DECIMAL_PLACES`."""
    return round(abs(correlation), LOCKSTEP_DECIMAL_PLACES) == 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class SharedInputs:
    """What two `FactorDefinition`s declare in common, computed from `required_fields` alone.

    No `as_of`, no partition, no float. That is the point: this is the *structure* an empirical
    correlation is read against, and a fact that needed data to establish could not play that
    role -- it would be a second measurement to correlate with the first rather than a frame for
    it.

    `columns` is the intersection of the qualified names (`"daily.close"`), sorted, and
    `datasets` is the intersection of the dataset names. Both are carried rather than only the
    code, because "which column" is the actionable half: a reader told that two value factors
    overlap wants to know it is the denominator.
    """

    left_key: str
    right_key: str
    code: SharedInputCode
    columns: tuple[str, ...]
    datasets: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.code not in SHARED_INPUT_CODES:
            raise FactorRedundancyError(
                f"{self.code!r} is not a declared shared-input code; expected one of "
                f"{sorted(SHARED_INPUT_CODES)}"
            )
        if self.columns and not self.datasets:
            raise FactorRedundancyError(
                f"{self.left_key} and {self.right_key} share the columns {list(self.columns)} and "
                "no dataset; a qualified column names its own dataset, so this pair has lost one "
                "of them"
            )


def shared_inputs(left: FactorDefinition, right: FactorDefinition) -> SharedInputs:
    """The structural overlap of two factors' declared inputs.

    Computed at the **qualified column** and not at the dataset, because the dataset is too
    coarse to carry a finding: 72 of the shipped 171 pairs share a dataset and only 42 share a
    column, so a dataset reading would call the value family and the quality family related by
    construction when what they have in common is that both read a filing. The dataset
    intersection is still reported, under `shared_dataset_only`, because two factors over one
    filing partition inherit one set of refusals and their *coverage* moves together whatever
    their values do.

    Refuses a pair of one definition against itself, which has nothing to say: every column is
    shared and the answer is `identical_inputs` for a reason that is not about either factor.
    """
    if left.factor_id == right.factor_id:
        raise FactorRedundancyError(
            f"{left.qualified_key} was offered against itself; a factor declares every one of its "
            "own columns, so the overlap is total for a reason that is about neither factor"
        )
    left_columns = {item.qualified_name for item in left.required_fields}
    right_columns = {item.qualified_name for item in right.required_fields}
    columns = tuple(sorted(left_columns & right_columns))
    datasets = tuple(sorted(set(left.datasets) & set(right.datasets)))
    if left_columns == right_columns:
        code: SharedInputCode = "identical_inputs"
    elif columns:
        code = "overlapping_inputs"
    elif datasets:
        code = "shared_dataset_only"
    else:
        code = "disjoint_inputs"
    return SharedInputs(
        left_key=left.qualified_key,
        right_key=right.qualified_key,
        code=code,
        columns=columns,
        datasets=datasets,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorIdentity:
    """A declared exact algebraic relation between two or more factors' values.

    `ICLimitation`'s shape with a callable: a `code`, the factor **keys** it relates, the
    tolerance its own arithmetic needs, prose, and a `residual` that is exactly zero for a
    subject at which the identity holds.

    `residual` takes one subject's `{factor key: value}` mapping and returns a float. Every member
    is guaranteed present when it is called -- `verify_identity` restricts to the subjects that
    carry all of them -- so a residual never has to handle a missing key, and a `KeyError` from
    one is a bug in the declaration rather than a fact about the data.

    **The tolerance is declared with the identity and not with the study**, because it is a
    property of the arithmetic being claimed rather than of the analysis. `1 + m20 == (1 + m15) *
    (1 + r5)` on returns of order 1 needs the last few bits of a double; an identity between two
    market capitalisations in 万元 needs a tolerance nine orders of magnitude larger for the same
    claim. A study-level tolerance would be one number deciding both.

    Keys and not `factor_id`s, and that is a deliberate weakening: an identity is a property of
    what a factor *computes*, and a restatement that bumps `version` without changing the
    arithmetic keeps it. The cost is that a version that *did* change the arithmetic keeps the
    declaration too -- which is what `refuted` is for, and why no identity is ever believed
    without being evaluated.
    """

    code: str
    members: tuple[str, ...]
    tolerance: float
    detail: str
    residual: Callable[[Mapping[str, float]], float]

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise FactorRedundancyError("an identity must carry a code")
        if len(self.members) < 2:
            raise FactorRedundancyError(
                f"identity {self.code!r} relates {list(self.members)}; an identity over fewer "
                "than two factors is a statement about one factor's own arithmetic and belongs "
                "in its evaluator"
            )
        if len(set(self.members)) != len(self.members):
            raise FactorRedundancyError(
                f"identity {self.code!r} names {list(self.members)} with a repeat; one factor "
                "contributes one value to one subject's residual"
            )
        if not math.isfinite(self.tolerance) or self.tolerance <= 0.0:
            raise FactorRedundancyError(
                f"identity {self.code!r} declares tolerance {self.tolerance!r}; a residual is "
                "compared with `<=`, so a non-positive tolerance would refuse the exact case and "
                "a non-finite one would accept everything"
            )

    def relates(self, left_key: str, right_key: str) -> bool:
        """Whether this identity names **both** of a pair's factors.

        Both rather than either, and the asymmetry is the whole reading: an identity relating
        three factors explains the correlation of any two of them, and an identity naming one of
        a pair says nothing at all about that pair.

        A pair of one key with itself relates to nothing, and the `!=` says so structurally
        rather than by convention: `members` are distinct by validator, so no declared identity
        can bind a factor to itself, and without the guard `relates(k, k)` collapses to
        `k in members` -- true for *every* identity that names `k` alongside somebody else. That
        is the cross-tier self-pair `correlate_cross_section` documents as a supported reading,
        and it would have been handed an identity about two different factors with only one of
        them supplied. Found by `V2-P3-014`, which reached for that reading and could not use it.
        """
        return left_key != right_key and left_key in self.members and right_key in self.members


class IdentityCheck(BaseModel):
    """What one declared identity did when it was evaluated on one `as_of`'s values.

    `max_abs_residual` is reported under **every** code, including `refuted`, because "the
    declaration is wrong" and "the declaration is wrong by 4e-4" are different findings and only
    the second says whether a tolerance was mis-declared or an evaluator changed. It is `None`
    only under `unevaluable`, where nothing was computed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    code: str = Field(min_length=1)
    members: tuple[str, ...] = Field(min_length=2)
    coverage: IdentityCoverage
    tolerance: float = Field(gt=0)
    subject_count: int = Field(ge=0)
    """How many subjects carried a value for **every** member and were therefore evaluated."""
    max_abs_residual: float | None

    @field_validator("max_abs_residual")
    @classmethod
    def refuse_a_residual_that_is_not_a_finite_magnitude(cls, value: float | None) -> float | None:
        """Finite **and** non-negative, and the second half is not decoration.

        This field is a maximum of absolute values, so a negative one is not a small residual --
        it is a number that cannot have come from `verify_identity`. It also passes the
        `<= tolerance` comparison below for *every* positive tolerance, so a stored row carrying
        one would read `verified` whatever the arithmetic did, which is the one reading this
        contract exists to make unavailable.
        """
        if value is not None and not math.isfinite(value):
            raise ValueError(
                f"{value!r} is not a finite residual; a residual that is not a number says the "
                "declared arithmetic broke down rather than that the identity failed"
            )
        if value is not None and value < 0.0:
            raise ValueError(
                f"{value!r} is a negative residual and this field is a maximum of absolute "
                "values; a negative one clears every tolerance and would read as verified "
                "whatever the arithmetic did"
            )
        return value

    @model_validator(mode="after")
    def validate_the_residual_matches_the_coverage(self) -> Self:
        evaluated = self.coverage != "unevaluable"
        if (self.max_abs_residual is None) == evaluated:
            raise ValueError(
                f"coverage {self.coverage!r} carries max_abs_residual {self.max_abs_residual!r}; "
                "verified and refuted both carry the residual they were decided by, and "
                "unevaluable carries none because nothing was computed"
            )
        if (self.subject_count == 0) != (self.coverage == "unevaluable"):
            raise ValueError(
                f"coverage {self.coverage!r} was decided over {self.subject_count} subject(s); "
                "unevaluable is exactly the case in which no subject carried every member"
            )
        if self.max_abs_residual is not None:
            within = self.max_abs_residual <= self.tolerance
            if within != (self.coverage == "verified"):
                raise ValueError(
                    f"coverage {self.coverage!r} reports max_abs_residual "
                    f"{self.max_abs_residual!r} against tolerance {self.tolerance!r}; verified is "
                    "exactly the case in which the largest residual is within the declared "
                    "tolerance, and a code that says otherwise makes the stored verdict unreadable"
                )
        return self


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorVector:
    """One factor's admitted values on one tier at one `as_of`, and what it cost to build.

    Built by `factor_vector`; this constructor re-derives nothing, following `ICCrossSection`'s
    precedent. `definition` rather than a bare key because a pair needs both `direction`s to
    orient its correlation and both `required_fields` to compute its structural overlap, and a
    vector carrying only a name would make both of those a second argument a caller could get
    wrong.

    The census arithmetic is `ICCensus`': `len(values)` plus every excluded cell totals
    `subject_count`, and `__post_init__` requires it. A vector that quietly dropped a security
    fails its own arithmetic rather than reporting a plausible correlation over a shorter sample.
    """

    as_of: datetime
    tier: FactorTier
    definition: FactorDefinition
    values: Mapping[str, float]
    """Admitted subjects only, in the order the rows were offered. Read through
    `_common_subjects`, which sorts, so no correlation depends on this order."""
    excluded_by_coverage: tuple[tuple[str, int], ...]
    """Every non-admitted code of the tier's vocabulary in that vocabulary's declared order,
    including the ones that did not occur -- `ICCensus`' rule, for its reason: a code missing from
    the tuple and a code present with a zero are different claims."""
    subject_count: int

    def __post_init__(self) -> None:
        if self.tier not in FACTOR_TIERS:
            raise FactorRedundancyError(
                f"{self.tier!r} is not a declared tier; expected one of {sorted(FACTOR_TIERS)}"
            )
        expected = tuple(
            code
            for code in TIER_COVERAGE_ORDER[self.tier]
            if code not in TIER_ADMITTED_CODES[self.tier]
        )
        if tuple(code for code, _count in self.excluded_by_coverage) != expected:
            raise FactorRedundancyError(
                f"excluded_by_coverage is keyed by "
                f"{[code for code, _count in self.excluded_by_coverage]} and the {self.tier} tier "
                f"excludes {list(expected)} in that order; a census missing a code cannot be told "
                "from one whose count is zero"
            )
        if any(count < 0 for _code, count in self.excluded_by_coverage):
            raise FactorRedundancyError("an excluded-coverage count cannot be negative")
        total = len(self.values) + sum(count for _code, count in self.excluded_by_coverage)
        if total != self.subject_count:
            raise FactorRedundancyError(
                f"the census accounts for {total} securities and {self.subject_count} were "
                f"offered to {self.definition.qualified_key}; every subject is admitted or "
                "excluded by its coverage code, and a census that does not add up has lost one"
            )

    @property
    def key(self) -> str:
        return self.definition.key

    @property
    def factor_id(self) -> str:
        return self.definition.factor_id

    @property
    def direction(self) -> FactorDirection:
        return self.definition.direction


def factor_vector(
    *,
    as_of: datetime,
    tier: FactorTier,
    definition: FactorDefinition,
    rows: Sequence[tuple[str, float | None, str]],
) -> FactorVector:
    """Admit one tier's rows under `factor_ic.TIER_ADMITTED_CODES` and count everything else.

    `rows` is `(subject, value, coverage)` -- the three columns all three observation contracts
    carry under different names, and the same projection `factor_ic.ic_cross_section` takes, so a
    caller holding `FactorObservation`s for an IC holds them for this too.

    **The admission table is imported and not restated.** `imputed` carries a number and is not
    admitted, for `factor_ic`'s stated reason: an imputed value is a cross-sectional median or a
    standardization's neutral point, which is a number no security produced, and a correlation
    that consumed it on both sides would be measuring the two fill rates against each other. That
    table is reconciled against the three contracts at `factor_ic`'s import, so this module
    inherits the audit rather than acquiring a second copy of it to drift.

    Every refusal here is a malformed *question* and not a fact about the market: a duplicated
    subject, a coverage code the tier does not declare, and an admitted code carrying no value.
    A thin or all-tied vector is a fact, and `correlate_cross_section` reports it as a code.
    """
    if tier not in FACTOR_TIERS:
        raise FactorRedundancyError(
            f"{tier!r} is not a declared tier; expected one of {sorted(FACTOR_TIERS)}"
        )
    instant = ensure_aware(as_of)
    vocabulary = set(TIER_COVERAGE_ORDER[tier])
    admitted_codes = TIER_ADMITTED_CODES[tier]
    subjects = [subject for subject, _value, _coverage in rows]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise FactorRedundancyError(
            f"{duplicates} appears more than once in {definition.qualified_key}'s {tier} vector "
            f"at {instant.isoformat()}; one security has one value at one as_of, and two would "
            "put one security into a correlation twice"
        )
    excluded = {code: 0 for code in TIER_COVERAGE_ORDER[tier] if code not in admitted_codes}
    values: dict[str, float] = {}
    for subject, value, coverage in rows:
        if coverage not in vocabulary:
            raise FactorRedundancyError(
                f"{subject} at {instant.isoformat()} carries coverage {coverage!r}, which the "
                f"{tier} tier does not declare; its vocabulary is {sorted(vocabulary)}"
            )
        if coverage not in admitted_codes:
            excluded[coverage] += 1
            continue
        if value is None:
            raise FactorRedundancyError(
                f"{subject} at {instant.isoformat()} carries the admitted coverage code "
                f"{coverage!r} with no value; the observation contracts make exactly the "
                "value-carrying codes carry a number, so this row skipped its own constructor"
            )
        if not math.isfinite(value):
            raise FactorRedundancyError(
                f"{subject}'s {definition.qualified_key} value at {instant.isoformat()} is "
                f"{value!r}, which is not a finite number; a non-finite term poisons every mean, "
                "rank and correlation built on it"
            )
        values[subject] = float(value)
    return FactorVector(
        as_of=instant,
        tier=tier,
        definition=definition,
        values=MappingProxyType(values),
        excluded_by_coverage=tuple(
            (code, excluded[code])
            for code in TIER_COVERAGE_ORDER[tier]
            if code not in admitted_codes
        ),
        subject_count=len(rows),
    )


def verify_identity(identity: FactorIdentity, vectors: Mapping[str, FactorVector]) -> IdentityCheck:
    """Evaluate a declared identity on the values in hand and report what happened.

    `vectors` is keyed by factor **key**, and every member of the identity must have one: a
    member with no vector is a question the caller did not finish asking, and answering it as
    `unevaluable` would make a missing read indistinguishable from a market in which no security
    carried all the members.

    The subjects are the intersection across every member's vector, **sorted**, so the residual
    that comes out largest does not depend on the order the rows were offered in.

    A residual that is not finite is refused rather than reported: it says the declared arithmetic
    broke down at that subject -- a division by a zero the declaration did not guard -- which is a
    defect in the declaration and not a refutation of it.
    """
    missing = sorted(set(identity.members) - set(vectors))
    if missing:
        raise FactorRedundancyError(
            f"identity {identity.code!r} relates {list(identity.members)} and no vector was "
            f"offered for {missing}; an identity evaluated over the members that happened to be "
            "present is a different identity"
        )
    subjects = sorted(
        set.intersection(*(set(vectors[member].values) for member in identity.members))
    )
    if not subjects:
        return IdentityCheck(
            code=identity.code,
            members=identity.members,
            coverage="unevaluable",
            tolerance=identity.tolerance,
            subject_count=0,
            max_abs_residual=None,
        )
    largest = 0.0
    for subject in subjects:
        residual = float(
            identity.residual(
                {member: vectors[member].values[subject] for member in identity.members}
            )
        )
        if not math.isfinite(residual):
            raise FactorRedundancyError(
                f"identity {identity.code!r} produced the residual {residual!r} for {subject}; a "
                "residual that is not a number says the declared arithmetic broke down at that "
                "security rather than that the identity failed there"
            )
        largest = max(largest, abs(residual))
    return IdentityCheck(
        code=identity.code,
        members=identity.members,
        coverage="verified" if largest <= identity.tolerance else "refuted",
        tolerance=identity.tolerance,
        subject_count=len(subjects),
        max_abs_residual=largest,
    )


class RedundancySpec(BaseModel):
    """The declared policy a redundancy study applies: which correlation, which floors, which line.

    Three fields with no defaults, each for `FactorTransformSpec.min_cross_section`'s reason --
    a decision that moves the answers is a decision somebody has to record making.

    `redundancy_threshold` is the one this issue turns on. See this module's docstring: the
    module refuses to choose it, bounds only the declaration, and reports every pair's magnitude
    beside its verdict so a reader can see what a different line would have done.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    method: ICMethod
    min_securities: int = Field(ge=MINIMUM_REDUNDANCY_SECURITIES, le=MAXIMUM_REDUNDANCY_SECURITIES)
    """The fewest **common** subjects this study will correlate at all. See
    `MINIMUM_REDUNDANCY_SECURITIES` for why the floor under it is four rather than three."""
    min_as_ofs: int = Field(ge=MINIMUM_IC_AS_OFS, le=MAXIMUM_REDUNDANCY_AS_OFS)
    """The fewest *measured* points a series needs before this study will summarise it.
    `MINIMUM_IC_AS_OFS` is imported rather than re-chosen: a sample standard deviation of one
    number does not exist."""
    redundancy_threshold: float = Field(gt=0.0, le=1.0)
    """The `abs(correlation)` at or above which a pair is called `redundant`.

    No default, and the bounds are the two arithmetic ones rather than a taste: at 0 every pair
    is redundant because `abs(r) >= 0` always, and 1.0 is both attainable (`_pearson` clamps to
    the closed range) and the strictest declaration there is."""


class RedundancyPoint(BaseModel):
    """One `as_of`'s answer for one pair: the correlation, its orientation, and why it is that.

    A pydantic model rather than a dataclass, for `ICPoint`'s reason: there is one of these per
    `(pair, as_of)` rather than one per security, so the validator is affordable and it is what
    is wanted.

    The validator is the contract, and it holds three relationships an unchecked report could
    contradict: the correlation and the verdict are present under exactly the `measured` code;
    `oriented_correlation` is `raw_correlation` under the two declared directions, exactly; and
    an `arithmetic` verdict is carried only by a point whose identity check actually came out
    `verified`. That last one is the whole issue in one assertion -- without it a report could
    call a pair arithmetic on the strength of a declaration nothing evaluated.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    as_of: datetime
    method: ICMethod
    left_factor_id: str = Field(min_length=1, max_length=64)
    right_factor_id: str = Field(min_length=1, max_length=64)
    left_key: str = Field(min_length=1, max_length=64)
    right_key: str = Field(min_length=1, max_length=64)
    left_tier: FactorTier
    right_tier: FactorTier
    left_direction: FactorDirection
    right_direction: FactorDirection
    coverage: PairCoverage
    sample_size: int = Field(ge=0)
    """How many **common** subjects the correlation was taken over -- reported on every code,
    including the ones that produced no number."""
    left_only_count: int = Field(ge=0)
    """Subjects admitted on the left and not on the right. Separate from `right_only_count` for
    `ICCensus.unmatched_count`'s reason: which factor lost the security is the actionable half,
    and one combined number would hide a factor that scored a tenth of the market."""
    right_only_count: int = Field(ge=0)
    raw_correlation: float | None
    """The correlation as computed, no interpretation."""
    oriented_correlation: float | None
    """`raw_correlation` signed by both declared directions. Positive means the two factors make
    the same bet; see this module's docstring."""
    verdict: RedundancyVerdict | None
    shared_input_code: SharedInputCode
    """The structural overlap of the two definitions, carried on every point including the ones
    that measured nothing -- it is a property of the declarations and not of the day."""
    shared_columns: tuple[str, ...]
    """*Which* qualified columns the two declare in common, sorted. Carried beside the code
    because "which column" is the actionable half: a reader told that two value factors overlap
    wants to know that it is the denominator."""
    identity: IdentityCheck | None
    """The one declared identity naming both factors, evaluated at this `as_of`, or `None` when
    none was declared."""

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @field_validator("raw_correlation", "oriented_correlation")
    @classmethod
    def refuse_a_non_finite_correlation(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(
                f"{value!r} is not a finite correlation; a degenerate side is what the "
                "degenerate_left and degenerate_right codes exist to carry"
            )
        return value

    @model_validator(mode="after")
    def validate_the_orientation_and_the_verdict_are_the_declared_ones(self) -> Self:
        measured = self.coverage == "measured"
        for name in ("raw_correlation", "oriented_correlation", "verdict"):
            if (getattr(self, name) is None) == measured:
                raise ValueError(
                    f"coverage {self.coverage!r} carries {name} {getattr(self, name)!r}; exactly "
                    "the 'measured' code carries a correlation and a verdict, and every other "
                    "code carries neither"
                )
        if self.raw_correlation is not None and not -1.0 <= self.raw_correlation <= 1.0:
            raise ValueError(
                f"raw_correlation {self.raw_correlation!r} is outside [-1, 1] and is not a "
                "correlation"
            )
        if self.raw_correlation is not None and self.oriented_correlation is not None:
            expected = (
                self.raw_correlation
                * _direction_sign(self.left_direction)
                * _direction_sign(self.right_direction)
            )
            if self.oriented_correlation != expected:
                raise ValueError(
                    f"this point declares directions {self.left_direction!r} and "
                    f"{self.right_direction!r} and reports raw_correlation "
                    f"{self.raw_correlation!r} as {self.oriented_correlation!r}; a pair's "
                    "correlation is signed by both declarations so that positive means the two "
                    "factors make the same bet"
                )
        if self.verdict == "arithmetic" and (
            self.identity is None or self.identity.coverage != "verified"
        ):
            raise ValueError(
                "this point is filed as arithmetic and carries "
                f"{None if self.identity is None else self.identity.coverage!r} for its identity; "
                "a pair is arithmetic only when a declared identity was EVALUATED on these values "
                "and came out verified -- a declaration nothing measured is prose"
            )
        if self.left_factor_id == self.right_factor_id and self.left_tier == self.right_tier:
            raise ValueError(
                f"{self.left_key} on the {self.left_tier} tier was correlated against itself; "
                "that number is 1.0 by construction and would be an undeclared_lockstep finding "
                "about nothing"
            )
        return self


class RedundancySummary(BaseModel):
    """A pair's points over a series of as_ofs, reduced to statistics and a census of verdicts.

    `mean_correlation` is over the **oriented** correlations and `mean_abs_correlation` over
    their magnitudes, and both are here because they answer different questions and can disagree
    sharply. A pair whose oriented correlation is `+0.9` on half the as_ofs and `-0.9` on the
    other half has a mean near zero and a mean magnitude of 0.9: it is not two unrelated factors,
    it is one relationship whose *sign* is unstable, which is a finding a mean alone erases.

    `verdict_counts` carries **every** verdict in `REDUNDANCY_VERDICT_ORDER`, including the ones
    that did not occur, and totals `measured_count`. `ICCensus`' rule, for its reason.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    method: ICMethod
    left_factor_id: str = Field(min_length=1, max_length=64)
    right_factor_id: str = Field(min_length=1, max_length=64)
    left_key: str = Field(min_length=1, max_length=64)
    right_key: str = Field(min_length=1, max_length=64)
    left_tier: FactorTier
    right_tier: FactorTier
    coverage: SummaryCoverage
    as_ofs: tuple[datetime, ...]
    """Every `as_of` offered, ascending and distinct -- not only the measured ones."""
    measured_count: int = Field(ge=0)
    mean_correlation: float | None
    mean_abs_correlation: float | None
    stdev_correlation: float | None
    """The **sample** standard deviation (`n - 1`) of the oriented correlations. See
    `KNOWN_REDUNDANCY_LIMITATIONS` for why nothing is built on top of it."""
    verdict_counts: tuple[tuple[str, int], ...]
    verdict: RedundancyVerdict | None
    shared_input_code: SharedInputCode
    shared_columns: tuple[str, ...]

    @field_validator("mean_correlation", "mean_abs_correlation", "stdev_correlation")
    @classmethod
    def refuse_a_non_finite_statistic(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{value!r} is not a finite statistic")
        return value

    @model_validator(mode="after")
    def validate_the_statistics_match_the_coverage(self) -> Self:
        measured = self.coverage == "measured"
        for name in ("mean_correlation", "mean_abs_correlation", "stdev_correlation", "verdict"):
            if (getattr(self, name) is None) == measured:
                raise ValueError(
                    f"coverage {self.coverage!r} carries {name} {getattr(self, name)!r}; exactly "
                    "the 'measured' code carries the statistics and the verdict"
                )
        if self.measured_count > len(self.as_ofs):
            raise ValueError(
                f"{self.measured_count} points were measured and {len(self.as_ofs)} as_ofs were "
                "offered; a series cannot measure an as_of it was not given"
            )
        if tuple(code for code, _count in self.verdict_counts) != REDUNDANCY_VERDICT_ORDER:
            raise ValueError(
                f"verdict_counts is keyed by {[code for code, _c in self.verdict_counts]} and the "
                f"declared verdicts are {list(REDUNDANCY_VERDICT_ORDER)}; a census missing a "
                "verdict cannot be told from one whose count is zero"
            )
        if sum(count for _code, count in self.verdict_counts) != self.measured_count:
            raise ValueError(
                f"the verdict census totals {sum(c for _v, c in self.verdict_counts)} and "
                f"{self.measured_count} points were measured; every measured point carries "
                "exactly one verdict"
            )
        if len(set(self.as_ofs)) != len(self.as_ofs) or list(self.as_ofs) != sorted(self.as_ofs):
            raise ValueError(
                "as_ofs must be distinct and ascending; a repeated as_of is one cross section "
                "counted twice, and an unordered tuple makes two identical studies compare unequal"
            )
        if self.mean_abs_correlation is not None and not 0.0 <= self.mean_abs_correlation <= 1.0:
            raise ValueError(
                f"mean_abs_correlation {self.mean_abs_correlation!r} is outside [0, 1]; it is a "
                "mean of magnitudes of correlations"
            )
        return self


class ICSeriesCorrelation(BaseModel):
    """How much two factors' information coefficients moved together over one set of as_ofs.

    A different question from a cross-sectional correlation and not implied by one in the
    direction that matters: two factors can rank the market almost independently and still earn
    their ICs on the same days, which is the redundancy a book holding both would discover in the
    period it mattered.

    **There is one correlation here and not a raw/oriented pair**, and the asymmetry with
    `RedundancyPoint` is deliberate rather than an omission: `ICPoint.ic` is *already* oriented by
    `FactorICSpec.orient`, so both series arrive on the convention "positive means the factor
    worked" and a second orientation would negate a `lower_is_better` pair twice and land back on
    the raw sign with nothing saying so.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    method: ICMethod
    left_factor_id: str = Field(min_length=1, max_length=64)
    right_factor_id: str = Field(min_length=1, max_length=64)
    """`ICPoint` carries a `factor_id` and no human key, so neither does this. That is not a
    cosmetic gap: an identity is looked up by *key*, and a contract that could not name one is a
    contract from which an `arithmetic` verdict is unreachable -- which is exactly the rule the
    validator below states outright."""
    left_tier: FactorTier
    right_tier: FactorTier
    horizon_sessions: int = Field(ge=1)
    coverage: PairCoverage
    offered_as_of_count: int = Field(ge=0)
    """How many as_ofs both series were offered. `offered - sample_size` is the attrition, and it
    is what separates two factors that disagree from two whose measured days barely overlap."""
    sample_size: int = Field(ge=0)
    """How many as_ofs both series **measured** an IC at."""
    correlation: float | None
    verdict: RedundancyVerdict | None

    @field_validator("correlation")
    @classmethod
    def refuse_a_non_finite_correlation(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{value!r} is not a finite correlation")
        return value

    @model_validator(mode="after")
    def validate_the_statistics_match_the_coverage(self) -> Self:
        measured = self.coverage == "measured"
        for name in ("correlation", "verdict"):
            if (getattr(self, name) is None) == measured:
                raise ValueError(
                    f"coverage {self.coverage!r} carries {name} {getattr(self, name)!r}; exactly "
                    "the 'measured' code carries a correlation and a verdict"
                )
        if self.correlation is not None and not -1.0 <= self.correlation <= 1.0:
            raise ValueError(
                f"correlation {self.correlation!r} is outside [-1, 1] and is not a correlation"
            )
        if self.verdict == "arithmetic":
            raise ValueError(
                "an IC-series correlation cannot be arithmetic; an identity is a relation between "
                "two factors' VALUES, and two ICs agreeing is a statement about the market even "
                "when the values that produced them are algebraically bound"
            )
        if self.sample_size > self.offered_as_of_count:
            raise ValueError(
                f"{self.sample_size} as_ofs were measured and {self.offered_as_of_count} were "
                "offered; a series cannot measure an as_of it was not given"
            )
        return self


def _common_subjects(left: FactorVector, right: FactorVector) -> tuple[str, ...]:
    """The subjects both vectors admitted, **sorted**.

    Sorted rather than taken in either side's offered order, and the reason is arithmetic. A
    Pearson sum is order-dependent in its last bits, so an intersection walked in the left
    vector's order would make `correlate_cross_section(a, b)` and `correlate_cross_section(b, a)`
    differ -- two spellings of one symmetric question with two answers, and a report that
    tabulated a triangular matrix would silently depend on which triangle it filled.
    `test_a_pairs_correlation_does_not_depend_on_which_side_is_offered_first` drives the equality
    with `==` rather than with a tolerance, which is only available because of this sort.
    """
    return tuple(sorted(set(left.values) & set(right.values)))


def _correlate(method: ICMethod, xs: Sequence[float], ys: Sequence[float]) -> float:
    """`_pearson` of the two vectors, or of their average ranks under `spearman`.

    The ranks are taken **inside this pair's own intersection** and never restricted from a
    whole-market rank vector, and that is a correctness requirement rather than a cost the module
    declined to optimise away. A rank is a position within a set, so the ranks of a subset are not
    the subset of the ranks: restricting a 40-name rank vector to a 25-name intersection and
    correlating disagreed with the honest answer on 200 of 200 random trials, by as much as
    0.100. `test_ranking_the_whole_market_and_restricting_is_not_ranking_the_intersection` is the
    drive, and ADR-0003's update section records the 6.3x this costs.
    """
    if method == "spearman":
        return _pearson(average_ranks(xs), average_ranks(ys))
    return _pearson(xs, ys)


def _degenerate_side(xs: Sequence[float], ys: Sequence[float]) -> PairCoverage | None:
    """Which side of the pair has nothing to order, or `None` when both do.

    `min == max` is `factor_ic._degenerate_side`'s test and `panel_factors._standardize_rank`'s,
    and on this plane it is again exactly the condition under which the correlation's denominator
    vanishes -- for `pearson` because every deviation is zero and for `spearman` because every
    average rank is `(n + 1) / 2`. One test covers both methods, which is why the code does not
    depend on the method.
    """
    if min(xs) == max(xs):
        return "degenerate_left"
    if min(ys) == max(ys):
        return "degenerate_right"
    return None


def _verdict(
    *, correlation: float, identity: IdentityCheck | None, threshold: float
) -> RedundancyVerdict:
    """The ladder in `RedundancyVerdict`'s declared order, applied to one magnitude.

    `arithmetic` is decided first and only on a `verified` check, so a declared identity the data
    refuted leaves the pair on the empirical ladder rather than excusing it -- which is the case
    the shipped `momentum_20_sessions`/`reversal_5_sessions` pair lands in when
    `V2-P3-012`'s identity is declared over it.
    """
    if identity is not None and identity.coverage == "verified":
        return "arithmetic"
    magnitude = abs(correlation)
    if _is_lockstep(magnitude):
        return "undeclared_lockstep"
    if magnitude >= threshold:
        return "redundant"
    return "distinct"


def _refuse_a_pair_that_is_neither_two_factors_nor_two_tiers(
    left: FactorVector, right: FactorVector
) -> None:
    """Refuse one factor on one tier against itself, and refuse two as_ofs in one pair.

    The self-pair is refused because its correlation is 1.0 by construction, so it would be an
    `undeclared_lockstep` finding about nothing and would put a diagonal into any matrix built
    from these points. The *cross-tier* self-pair is the opposite and is the point of this
    signature: `raw` against `neutralized` for one factor is how much of it survived the
    neutralisation.

    Two as_ofs in one pair is a factor value from one day against a factor value from another,
    which is `factor_ic._rows`' refusal transposed -- and it is refused rather than filtered,
    because a caller who read the two sides from two partitions wants to be told.
    """
    if left.as_of != right.as_of:
        raise FactorRedundancyError(
            f"{left.definition.qualified_key} is stamped at {left.as_of.isoformat()} and "
            f"{right.definition.qualified_key} at {right.as_of.isoformat()}; a cross-sectional "
            "correlation compares two readings of one day, and two days is a plausible number "
            "from the wrong rows"
        )
    if left.factor_id == right.factor_id and left.tier == right.tier:
        raise FactorRedundancyError(
            f"{left.definition.qualified_key} on the {left.tier} tier was offered against itself; "
            "that correlation is 1.0 by construction. A cross-TIER pair of one factor is the "
            "supported reading and says how much of it survived the transform"
        )


def correlate_cross_section(
    *,
    left: FactorVector,
    right: FactorVector,
    spec: RedundancySpec,
    identity: IdentityCheck | None = None,
) -> RedundancyPoint:
    """One `as_of`'s correlation for one pair, with the verdict that says why it is what it is.

    Never raises for a property of the market: an intersection thinner than the declared floor and
    a side with nothing to order are both answers, because a loop over a year of as_ofs has to
    keep going past them and a report that showed only the days that separated would be a report
    of a different pair.

    `identity` is the already-evaluated `IdentityCheck` for this `as_of`, or `None` when the study
    declared no identity naming both factors. It arrives evaluated rather than as a
    `FactorIdentity` to be evaluated here, because an identity may relate **three** factors and
    this function holds two: `RedundancyStudy.measure` is where the vectors for every member are
    in scope.
    """
    _refuse_a_pair_that_is_neither_two_factors_nor_two_tiers(left, right)
    if identity is not None and not (
        left.key in identity.members and right.key in identity.members
    ):
        raise FactorRedundancyError(
            f"identity {identity.code!r} relates {list(identity.members)} and this pair is "
            f"({left.key}, {right.key}); an identity that does not name both factors explains "
            "nothing about their correlation and would put an arithmetic verdict on a finding"
        )
    common = _common_subjects(left, right)
    cross_tier = left.factor_id == right.factor_id
    overlap = None if cross_tier else shared_inputs(left.definition, right.definition)
    shared_code: SharedInputCode = "identical_inputs" if overlap is None else overlap.code
    shared_columns = (
        tuple(sorted(item.qualified_name for item in left.definition.required_fields))
        if overlap is None
        else overlap.columns
    )
    xs = [left.values[subject] for subject in common]
    ys = [right.values[subject] for subject in common]
    coverage: PairCoverage = "measured"
    raw: float | None = None
    if len(common) < spec.min_securities:
        coverage = "insufficient_sample"
    else:
        degenerate = _degenerate_side(xs, ys)
        if degenerate is not None:
            coverage = degenerate
        else:
            raw = _correlate(spec.method, xs, ys)
    oriented = (
        None
        if raw is None
        else raw * _direction_sign(left.direction) * _direction_sign(right.direction)
    )
    return RedundancyPoint(
        as_of=left.as_of,
        method=spec.method,
        left_factor_id=left.factor_id,
        right_factor_id=right.factor_id,
        left_key=left.key,
        right_key=right.key,
        left_tier=left.tier,
        right_tier=right.tier,
        left_direction=left.direction,
        right_direction=right.direction,
        coverage=coverage,
        sample_size=len(common),
        left_only_count=len(set(left.values) - set(right.values)),
        right_only_count=len(set(right.values) - set(left.values)),
        raw_correlation=raw,
        oriented_correlation=oriented,
        verdict=(
            None
            if raw is None
            else _verdict(correlation=raw, identity=identity, threshold=spec.redundancy_threshold)
        ),
        shared_input_code=shared_code,
        shared_columns=shared_columns,
        identity=identity,
    )


class RedundancyStudy:
    """Measure one pair at one `as_of`, summarise a series, correlate two IC series.

    A class holding a `RedundancySpec` and the declared identities, for `FactorICStudy`'s reason:
    the correlation, the floors and the line a pair is called redundant at are fixed once for a
    study rather than passed at each call with a chance to differ.

    **`identities` is a required keyword argument with no default.** Declaring none is then a
    declaration -- `()` -- rather than an omission, and there is no module-level registry that
    could quietly empty itself out while every per-identity assertion passed vacuously. That is
    `FactorRegistry`'s own refused shape, and the reason it matters more here than there is that
    an empty identity registry does not merely check nothing: it makes every arithmetic pair in
    the build look empirical.
    """

    def __init__(self, spec: RedundancySpec, *, identities: Sequence[FactorIdentity]) -> None:
        codes = [item.code for item in identities]
        if len(set(codes)) != len(codes):
            duplicates = sorted({code for code in codes if codes.count(code) > 1})
            raise FactorRedundancyError(
                f"{duplicates} is declared more than once; two identities answering to one code "
                "make the check a pair receives depend on iteration order"
            )
        self._spec = spec
        self._identities = tuple(identities)

    @property
    def spec(self) -> RedundancySpec:
        return self._spec

    @property
    def identities(self) -> tuple[FactorIdentity, ...]:
        return self._identities

    def identity_for(self, left_key: str, right_key: str) -> FactorIdentity | None:
        """The one declared identity naming both factors, or `None`.

        Refuses two, rather than taking the first: two identities relating one pair are two
        claims about one arithmetic, and a verdict that depended on which was found first would
        be a verdict decided by the order a caller assembled a tuple.
        """
        found = [item for item in self._identities if item.relates(left_key, right_key)]
        if len(found) > 1:
            raise FactorRedundancyError(
                f"{sorted(item.code for item in found)} all relate ({left_key}, {right_key}); two "
                "declared identities over one pair are two claims about one arithmetic, and "
                "picking one would make the verdict depend on tuple order"
            )
        return found[0] if found else None

    def measure(
        self,
        *,
        left: FactorVector,
        right: FactorVector,
        vectors: Mapping[str, FactorVector] | None = None,
    ) -> RedundancyPoint:
        """One pair's point, with its declared identity evaluated first if there is one.

        `vectors` supplies the members an identity needs beyond the pair itself, keyed by factor
        key; `left` and `right` are added to it here, so a two-member identity needs no `vectors`
        at all and a three-member one needs only the third. Passing a vector for `left`'s or
        `right`'s own key under a *different* vector is refused, because two readings of one
        factor at one `as_of` would let the residual and the correlation be computed from
        different numbers.

        That collection runs **only when an identity was declared for the pair**, and the scope
        is what makes the cross-tier self-pair `correlate_cross_section` documents usable: one
        factor's raw and neutralized readings are two different vectors under one key, which is
        exactly the shape the refusal above describes, and it is also the shape that has no
        residual for two readings to disagree about -- `relates` refuses a key against itself,
        so `declared` is `None` and nothing is looked up by key at all. `V2-P3-014` reached for
        that reading, hit the refusal, and worked around it; this is the fix rather than the
        workaround.
        """
        declared = self.identity_for(left.key, right.key)
        check = None
        if declared is not None:
            supplied = dict(vectors or {})
            for side in (left, right):
                existing = supplied.get(side.key)
                if existing is not None and existing is not side:
                    raise FactorRedundancyError(
                        f"a second vector was offered for {side.key}; the residual and the "
                        "correlation would then be computed from two different readings of one "
                        "factor"
                    )
                supplied[side.key] = side
            check = verify_identity(declared, supplied)
        return correlate_cross_section(left=left, right=right, spec=self._spec, identity=check)

    def summarize(self, points: Iterable[RedundancyPoint]) -> RedundancySummary:
        """A pair's series reduced to two means, a dispersion and a census of verdicts.

        Refuses a series that is not one pair -- a point about another pair, another tier,
        another method, or two points at one `as_of`. Each of those is a malformed question
        rather than a thin sample: a mean over two pairs is the average of two different
        quantities, and one `as_of` counted twice weights a day by how often a caller appended it.

        The summary's own verdict is the same ladder one scope up, and its top rung is stricter
        than the per-point one in the direction that matters: `arithmetic` requires **every**
        measured point to have been arithmetic, because an identity that held on some of the
        offered days and not others is not an identity.
        """
        ordered = sorted(points, key=lambda point: point.as_of)
        if not ordered:
            raise FactorRedundancyError(
                "a redundancy summary needs at least one point; an empty series satisfies every "
                "per-point check vacuously and would report a coverage code about nothing"
            )
        self._refuse_points_that_are_not_one_pair(ordered)
        head = ordered[0]
        as_ofs = tuple(point.as_of for point in ordered)
        measured = [point for point in ordered if point.oriented_correlation is not None]
        counts = tuple(
            (verdict, sum(1 for point in measured if point.verdict == verdict))
            for verdict in REDUNDANCY_VERDICT_ORDER
        )
        oriented = [
            value for point in measured if (value := point.oriented_correlation) is not None
        ]
        thin = len(measured) < self._spec.min_as_ofs
        mean_abs = None if thin else statistics.fmean(abs(value) for value in oriented)
        verdict: RedundancyVerdict | None = None
        if mean_abs is not None:
            arithmetic = sum(count for name, count in counts if name == "arithmetic")
            if arithmetic == len(measured):
                verdict = "arithmetic"
            elif _is_lockstep(mean_abs):
                verdict = "undeclared_lockstep"
            elif mean_abs >= self._spec.redundancy_threshold:
                verdict = "redundant"
            else:
                verdict = "distinct"
        return RedundancySummary(
            method=head.method,
            left_factor_id=head.left_factor_id,
            right_factor_id=head.right_factor_id,
            left_key=head.left_key,
            right_key=head.right_key,
            left_tier=head.left_tier,
            right_tier=head.right_tier,
            coverage="insufficient_as_ofs" if thin else "measured",
            as_ofs=as_ofs,
            measured_count=len(measured),
            mean_correlation=None if thin else statistics.fmean(oriented),
            mean_abs_correlation=mean_abs,
            stdev_correlation=None if thin else statistics.stdev(oriented),
            verdict_counts=counts,
            verdict=verdict,
            shared_input_code=head.shared_input_code,
            shared_columns=head.shared_columns,
        )

    def correlate_ic_series(
        self, left: Sequence[ICPoint], right: Sequence[ICPoint]
    ) -> ICSeriesCorrelation:
        """How much two factors' oriented ICs moved together, over the as_ofs both measured.

        Both series must be at one horizon and under one `ICMethod`, and that method must be this
        study's: an IC series computed as a rank correlation and one computed as a Pearson
        correlation are two different quantities, and correlating them would answer a question
        nobody asked.

        The two sides are aligned on `as_of` and restricted to the as_ofs **both** measured, so a
        day one factor could not score contributes to neither side rather than to one. `offered_
        as_of_count` is the size of the offered intersection and `sample_size` the measured one,
        so the attrition is visible instead of being confounded with disagreement.
        """
        left_points = self._one_ic_series(left, side="left")
        right_points = self._one_ic_series(right, side="right")
        if left_points[0].horizon_sessions != right_points[0].horizon_sessions:
            raise FactorRedundancyError(
                f"the left series is at {left_points[0].horizon_sessions} sessions and the right "
                f"at {right_points[0].horizon_sessions}; an IC at five sessions and one at sixty "
                "are two different quantities and their correlation has no reading"
            )
        head, other = left_points[0], right_points[0]
        if head.factor_id == other.factor_id and head.tier == other.tier:
            raise FactorRedundancyError(
                f"{head.factor_id} on the {head.tier} tier was correlated against its own IC "
                "series; that number is 1.0 by construction"
            )
        by_left = {point.as_of: point for point in left_points}
        by_right = {point.as_of: point for point in right_points}
        offered = sorted(set(by_left) & set(by_right))
        pairs = [
            (left_ic, right_ic)
            for stamp in offered
            if (left_ic := by_left[stamp].ic) is not None
            and (right_ic := by_right[stamp].ic) is not None
        ]
        xs = [value for value, _other in pairs]
        ys = [other for _value, other in pairs]
        coverage: PairCoverage = "measured"
        correlation: float | None = None
        if len(pairs) < self._spec.min_as_ofs:
            coverage = "insufficient_sample"
        else:
            degenerate = _degenerate_side(xs, ys)
            if degenerate is not None:
                coverage = degenerate
            else:
                correlation = _correlate(self._spec.method, xs, ys)
        return ICSeriesCorrelation(
            method=self._spec.method,
            left_factor_id=head.factor_id,
            right_factor_id=other.factor_id,
            left_tier=head.tier,
            right_tier=other.tier,
            horizon_sessions=head.horizon_sessions,
            coverage=coverage,
            offered_as_of_count=len(offered),
            sample_size=len(pairs),
            correlation=correlation,
            verdict=(
                None
                if correlation is None
                else _verdict(
                    correlation=correlation,
                    identity=None,
                    threshold=self._spec.redundancy_threshold,
                )
            ),
        )

    def _one_ic_series(self, points: Sequence[ICPoint], *, side: str) -> tuple[ICPoint, ...]:
        """Refuse a series mixing factors, tiers, methods, horizons or as_ofs, and order it."""
        if not points:
            raise FactorRedundancyError(
                f"the {side} IC series is empty; an empty series satisfies every per-point check "
                "vacuously and would report a coverage code about nothing"
            )
        ordered = tuple(sorted(points, key=lambda point: point.as_of))
        head = ordered[0]
        if head.method != self._spec.method:
            raise FactorRedundancyError(
                f"the {side} IC series was measured under {head.method!r} and this study declares "
                f"{self._spec.method!r}; a rank IC and a Pearson IC are two different quantities"
            )
        for point in ordered:
            mismatched = [
                f"{name}={getattr(point, name)!r} against {getattr(head, name)!r}"
                for name in ("tier", "method", "direction", "factor_id", "horizon_sessions")
                if getattr(point, name) != getattr(head, name)
            ]
            if mismatched:
                raise FactorRedundancyError(
                    f"the {side} point at {point.as_of.isoformat()} reports {mismatched}; one "
                    "side of an IC-series correlation is one factor on one tier under one "
                    "correlation at one horizon"
                )
        stamps = [point.as_of for point in ordered]
        if len(set(stamps)) != len(stamps):
            duplicates = sorted({stamp.isoformat() for stamp in stamps if stamps.count(stamp) > 1})
            raise FactorRedundancyError(
                f"{duplicates} appears more than once in the {side} series; one as_of is one "
                "cross section, and counting it twice weights a day by how often it was appended"
            )
        return ordered

    def _refuse_points_that_are_not_one_pair(self, ordered: Sequence[RedundancyPoint]) -> None:
        """Refuse a series mixing pairs, tiers, methods, structures or as_ofs."""
        head = ordered[0]
        for point in ordered:
            mismatched = [
                f"{name}={getattr(point, name)!r} against {getattr(head, name)!r}"
                for name in (
                    "method",
                    "left_factor_id",
                    "right_factor_id",
                    "left_tier",
                    "right_tier",
                    "shared_input_code",
                )
                if getattr(point, name) != getattr(head, name)
            ]
            if mismatched:
                raise FactorRedundancyError(
                    f"the point at {point.as_of.isoformat()} reports {mismatched}; a redundancy "
                    "summary is one pair of factors on one pair of tiers under one correlation, "
                    "and a mean over two of any of them is the average of two different quantities"
                )
            if point.method != self._spec.method:
                raise FactorRedundancyError(
                    f"the point at {point.as_of.isoformat()} was measured under {point.method!r} "
                    f"and this study declares {self._spec.method!r}"
                )
        stamps = [point.as_of for point in ordered]
        if len(set(stamps)) != len(stamps):
            duplicates = sorted({stamp.isoformat() for stamp in stamps if stamps.count(stamp) > 1})
            raise FactorRedundancyError(
                f"{duplicates} appears more than once in this series; one as_of is one cross "
                "section, and counting it twice weights a day by how often a caller appended it"
            )
