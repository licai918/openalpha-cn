"""The information coefficient (`V2-P3-005`): IC, rank IC, IC decay, and stability.

An IC is the cross-sectional correlation between a factor's values at one `as_of` and the
securities' realised forward returns from that `as_of`. This module computes one, aggregates a
series of them, and lays a series out against a horizon axis. It stores nothing and reads no
partition: `backtest/event_study.py` is the precedent, and it is the right one -- inference over
numbers somebody else assembled, in the standard library, with the *rules* rather than the
plumbing as the deliverable.

## The forward return is `domain/labels.py`'s, and this module never computes one

The measured hazard first, because it is the reason the input type is what it is.
`domain/daily_prices.py` put `000001.SZ`'s 2026-06-12 session at `+2.742230%` on the
`close/pre_close` path and `+2.742251%` on the `adj_factor` path, and at **`-0.530973%`** on
`close[t]/close[t-1]` -- wrong by percentage points, with the sign reversed, and right on every
other day of that month. A rank correlation computed against that third path is not a weaker IC;
it is an IC of the wrong sign on the securities that had a corporate action.

So `ic_cross_section` takes `OutcomeLabel`s and not floats. There is no argument on any function
in this module that a caller could pass a self-computed return through, and
`OutcomeLabel.realized_return` is `WindowReturn.adjusted` -- the path
`backtest/validation.py::observation_from_label` already binds a stored `ValidationResult` to.
Everything `V2-P1-017` refuses travels with it: a halted session, a session locked at its limit,
an unpublished band, a delisting inside the window. Each of those makes `is_labelled` false, and
this module excludes that security from the correlation and **counts it** (`ICCensus
.unlabelled_count`) rather than substituting a zero -- a zero is a return, and a cross section in
which every halted name reads flat is one whose IC is partly a measurement of the halt rate.

Two properties of the window are checked here rather than assumed of the caller:

- **Every label at one `as_of` is over one window.** Same `prediction_day`, `entry_day`,
  `exit_day` and horizon for all of them. A cross section correlated against windows that are not
  the same window is not a cross-sectional statistic at all -- half the names would be scored on
  five sessions and half on sixty.
- **The window's entry session is strictly after the `as_of`'s own session**, dated in the
  window's own zone, which is the same operation `build_label_window` performs to get
  `prediction_day` from an instant. A window whose entry is at or before the `as_of` prices a
  return that had already happened when the factor value was stamped. What this check cannot see
  is *which* zone the window was dated in; see `KNOWN_IC_LIMITATIONS`.

## Which observations enter a correlation, and what happens to the rest

Coverage is not a boolean on any of the three tiers -- six codes raw, five processed, seven
neutralised -- and the answer has to be a declaration rather than a filter written inline:

    raw          admits {computed}
    processed    admits {processed}      and NOT {imputed}
    neutralised  admits {neutralized}

`TIER_ADMITTED_CODES` is that table and `TIER_VALUE_CODES` is the set of codes that carry a
number at all, reconciled against the three contracts' own constants at **import** by
`_refuse_a_tier_table_that_disagrees_with_its_own_contract`. The gap between them is one code,
`imputed`, and excluding it is the load-bearing judgement of this section rather than an
oversight: an imputed value is a cross-sectional median or a standardization's neutral point,
which is a number no security produced, and a correlation that consumed it would be measuring the
fill rate as much as the factor. That is not this module's invention --
`domain/factor_neutralization.py::ParticipationRule` already names `measured_only` as "the
reading `V2-P3-005`'s information coefficient wants", and this is the consumer arriving. It is
fixed rather than a knob for the same reason `direction` is not one: a statistic whose sample can
be widened to include manufactured values is a statistic with a free parameter that moves it.

Everything not admitted is **counted by its own code**, in the tier's declared order, with a zero
row for the codes that did not occur -- so "no security was `input_missing`" and "nobody looked"
are different readings of the census. `ICCensus.__post_init__` requires the three scalars and
every cell of the excluded tuple to total `subject_count`, which is what makes any one of them
un-fudgeable: a census that dropped a security silently, or counted one under two headings, fails
its own arithmetic rather than reporting a plausible total.

## The two ICs, and where they agree with the transform plane

- **`pearson`** is the ordinary product-moment correlation of value against forward return.
- **`spearman`** -- the *rank IC* -- is the Pearson correlation of the two **average-rank**
  vectors. Ties share their average rank, so `(1.0, 3.0, 3.0, 7.0)` ranks `(1.0, 2.5, 2.5, 4.0)`.

That is the same tie rule `panel_factors._standardize_rank` applies, and the alignment is
deliberate and pinned rather than coincidental: `average_ranks` here and `_average_ranks` there
are two implementations of one rule, and `tests/unit/backtest/test_factor_ic.py::
test_the_rank_this_module_assigns_is_the_rank_the_transform_plane_assigns` drives both over the
same inputs and compares them element-wise. They are two implementations because this module is a
standard-library leaf and that one is a top-level module over the panel plane; a shared helper
would put an edge from `backtest/` into `panel_factors` and through it into DuckDB.

**The degeneracy rule is aligned too, and split in two.** `_standardize_rank` answers `None` for
an all-tied cross section rather than a vector of zeros, because a cross section with nothing to
order has nothing to say. The same fact makes a correlation `0 / 0`, and this module reports it as
a coverage code -- but as **two** codes, `degenerate_scores` and `degenerate_returns`, because the
two are different findings with different remedies. A factor that produced one value for the whole
market is a defect in the factor; a market that moved by exactly the same amount for every name is
a fact about the day (a whole-market halt, or a one-name cross section repeated). One
`degenerate` code would make those two indistinguishable on a stored report, which is the shape
`FactorCoverage` spent six members refusing.

## The sample floor is declared, and its own lower bound is arithmetic

`FactorICSpec.min_securities` has no default. What is *not* the caller's is the floor under the
floor: `MINIMUM_IC_SECURITIES` is 3, and the reason is a property of the arithmetic rather than a
taste. There is one line through two points, so a correlation of two securities that tie on
neither axis has magnitude one whatever the two securities did -- its sign is the sign of a single
difference and its magnitude carries no information at all. `n = 3` is the first size at which
`|r| < 1` is attainable.

**Measured, and the measurement is the reason this paragraph does not say "exactly 1.0".** Ten
random two-name pairs through `_pearson` returned `1.0` twice and `0.9999999999999998` eight
times, on both methods; the last bit is the rounding `_pearson`'s clamp exists for. So the claim
this module makes and tests is `abs(r)` rounded to 15 places `== 1.0`, not an identity -- an
identity would have been an assertion that fails on eight of ten inputs.
`tests/unit/backtest/test_factor_ic.py::
test_a_two_name_cross_section_correlates_perfectly_whatever_the_two_names_did` drives it over
random pairs rather than over one.

Three is a *floor*, not a recommendation, and the contract's job is to record which floor was
chosen rather than to choose. `min_securities=3` is declarable and produces an IC that is almost
entirely sampling noise; `KNOWN_IC_LIMITATIONS` says so by name, because this repository has
taken eight Critical findings on statistics calibrated over narrow samples and none of them was
prevented by a constant somebody buried in an engine.

`min_as_ofs` is the same shape one level up, and its arithmetic floor is 2: a standard deviation
of one number does not exist, so an ICIR over one `as_of` is not a weak claim but an undefined
one.

## Stability: four numbers, each defined here rather than named

Over a series of `ICPoint`s at one tier, one method and one horizon:

- **`mean_ic`** -- `statistics.fmean` of the **oriented** ICs of the measured points. Non-measured
  points contribute nothing and are not zeros; `measured_count` beside `as_of_count` is what says
  how many were dropped.
- **`stdev_ic`** -- `statistics.stdev`, the **sample** standard deviation (`n - 1`). Sample rather
  than population because the measured as_ofs are a sample of the periods this factor might be
  used in, which is also why `min_as_ofs` cannot be 1.
- **`icir`** -- `mean_ic / stdev_ic`, and **`None` when `stdev_ic` is zero**. This is a deliberate
  divergence from `backtest/event_study.py`, which answers `math.inf` for its t-statistic in the
  same case. `math.inf` is wrong in two ways here: it discards the sign of a constant negative IC
  (`-0.2` every day is `-inf`, not `+inf`), and a non-finite number is exactly what
  `validate_factor_observation`, `validate_processed_factor_observation` and
  `validate_neutralized_factor_observation` all refuse to let onto a stored column -- so an
  `inf` here would be a number this repository's own storage rules would reject one plane over.
  `None` says "there is no dispersion to divide by", which is the fact.
- **`sign_consistency`** -- `positive_count / measured_count`, computed on the **oriented** IC, so
  "positive" means "in the direction the factor declared". A zero IC counts in neither the
  positive nor the negative bucket and stays in the denominator, because a zero is not evidence
  for the factor; `zero_count` is reported so a reader can see the denominator it eroded.

There is no t-statistic here and the omission is deliberate rather than an oversight: it is
`icir * sqrt(measured_count)`, both factors are fields, and a second field computed from two
others is a second thing that can disagree with them. What a t-statistic would *additionally*
claim -- that the measured ICs are independent draws -- is false by construction for a daily
series, and `KNOWN_IC_LIMITATIONS` says so.

## Decay: the axis is the horizon in sessions, and the sample is held fixed

`ICDecayCurve` is a tuple of rungs, each an `ICSummary` at one horizon, ordered by
`horizon_sessions` -- `ResearchHorizon.sessions`, which is defined for `d` horizons and refuses a
calendar one, so a `3m` rung is not constructible and this axis inherits `domain/horizon.py`'s
countability rule rather than restating it.

**The forward return at rung `h` is cumulative from one entry, not the marginal return of session
`h`.** That is what `labels.py` produces -- `WindowReturn` is entry-to-exit -- and it is the
honest reading: the question a decay curve answers is "how long does a position opened on this
signal keep working", not "does session 7 alone still correlate". A marginal curve would need a
label contract that does not exist.

**Every rung must be measured over the same offered `as_of`s**, and `_refuse_rungs_over_different
_samples` enforces it. Without that rule a curve that fell from 0.05 to 0.01 could be a factor
decaying or could be a longer horizon whose windows ran past the calendar and lost two thirds of
its as_ofs, and nothing on the curve would separate them. The offered set is held identical and
each rung's `measured_count` is reported, so attrition is visible instead of being confounded
with decay.

## `direction` decides the sign, and it arrives as a `FactorDefinition`

`FactorDefinition.direction` had no consumer when `V2-P3-001` shipped and its docstring named
this issue as the one that would have it. Here it is:

    ic = raw_ic          for higher_is_better
    ic = -raw_ic         for lower_is_better

Both numbers are carried. `raw_ic` is the correlation as computed -- factor value against forward
return, no interpretation -- and `ic` is that number oriented so that **positive always means the
factor worked**. `ICPoint`'s own validator enforces the relationship, so an oriented IC that
contradicts the direction it was built under is not constructible.

The alternative was to report `raw_ic` alone and leave orientation to each consumer, and it is
rejected on a measurable ground rather than a stylistic one: every aggregate downstream is a
*comparison across factors*. `V2-P3-008` groups by family for a redundancy analysis and
`V2-P3-014` reports three tiers per family; averaging a `higher_is_better` factor's `+0.03` with a
`lower_is_better` factor's `-0.03` gives zero for two factors that both worked. Orienting once,
at the only place that holds the declaration, is one rule in one place; orienting at each consumer
is the same rule in N places, and this repository has measured what that costs.

**And it is `FactorDefinition` rather than a bare `direction` argument** for the reason
`FactorBuildManifest` carries the field at all: the direction that must decide the sign is the
factor's declared one, and a function taking a `FactorDirection` parameter is one a caller can
pass the other value to. `FactorICSpec.definition` makes the wrong direction unreachable rather
than discouraged, and `factor_id` travels onto every point and summary so a stored IC names the
definition whose sign convention produced it.

## The one product constraint a reader of a neutralised IC has to be told

**A neutralised residual is read at a year-end snapshot, not at the `as_of` a caller asked for**,
and this module cannot fix it. `panel_neutralization.neutralized_observation_batch` stamps all
four clocks of every row with the build's own `as_of`, and that `as_of` is at or after the
`max_available_time` of the year's `daily_basic` partition -- the year's last session. Reads go
through `PanelStore.read_visible_at`, so any `as_of` inside the covered year returns **nothing**
rather than raising, and the rows a caller does get back are the ones stamped at the year end.

`V2-P3-004`'s review measured this and judged it non-blocking for this issue, and the judgement
is worth restating because it is narrower than it looks: the residuals' *content* is clean -- each
was computed from the industry, the market capitalisation and the processed value **of its own
day** -- so an IC or a decay curve built from them is not forward-contaminated. What was lost is
the honesty of the timestamps, which means a neutralised IC series cannot claim to be a
point-in-time series the way a raw or processed one can. `V2-P4-026` is the issue that gives
`daily_basic` an as-of-sensitive session-level read, and it is a hard precondition of `V2-P4-013`.
`KNOWN_IC_LIMITATIONS` carries it as
`neutralised_residuals_are_read_at_a_year_end_snapshot`.

## Layering, and why there is no numpy here

`backtest/` over `domain/`, standard library only. ADR-0003 named "rank correlation (-> a float
information coefficient)" as one of the two workloads that would re-open the numerical-stack
question; this is that workload arriving, and its Update section for this issue carries the
measurement. A rank IC is a sort and two passes: `O(n log n)` at `n = 5,534`, which is ADR-0002's
whole-market cross section. Measured before the judgement rather than after it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.factor import (
    FACTOR_COVERAGE_CODES,
    FactorCoverage,
    FactorDefinition,
    FactorDirection,
    FactorObservation,
)
from openalpha_cn.domain.factor_neutralization import (
    NEUTRALIZED_COVERAGE_CODES,
    NEUTRALIZED_COVERAGE_ORDER,
    NEUTRALIZED_VALUE_CODES,
    NeutralizedFactorObservation,
)
from openalpha_cn.domain.factor_transform import (
    IMPUTING_PROCESSED_CODES,
    MISSING_VALUE_COVERAGE_ORDER,
    PROCESSED_COVERAGE_CODES,
    PROCESSED_COVERAGE_ORDER,
    PROCESSED_VALUE_CODES,
    ProcessedFactorObservation,
)
from openalpha_cn.domain.horizon import ResearchHorizon
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel
from openalpha_cn.domain.time import ensure_aware


class FactorICError(ValueError):
    """Raised for a malformed IC cross section, series or decay curve.

    A `ValueError` subclass for `FactorError`'s reason: every call site that already writes
    `except ValueError` keeps catching it unchanged. It is deliberately *not* what a thin cross
    section or an all-tied one produces -- those are facts about the market at that `as_of`, they
    are reported as `ICCoverage` codes, and a loop over a year of as_ofs has to be able to keep
    going past them.
    """


ICMethod = Literal["pearson", "spearman"]
"""Which correlation an IC is, as a closed set rather than a boolean `rank=True`.

- **`pearson`** -- the product-moment correlation of value against forward return. Sensitive to
  the tails on both sides, which is the point when the factor's scale is meaningful and the
  hazard when one name's return is ten times everybody's.
- **`spearman`** -- the *rank IC*: the same correlation computed on average ranks. Invariant to
  any monotone transform of either side, so it answers "did the ordering hold" rather than "did
  the magnitudes line up".

Closed because `V2-P3-014`'s report shows both side by side and a third spelling of "rank" would
silently become a column of one.
"""

IC_METHODS: Final[frozenset[str]] = frozenset(get_args(ICMethod))

IC_METHOD_ORDER: Final[tuple[ICMethod, ...]] = get_args(ICMethod)
"""The declared order, which is the order a report lays the two out in."""

FactorTier = Literal["raw", "processed", "neutralized"]
"""Which of the three stored planes an IC was measured on.

D8 asks a report to compare raw, processed and neutralised performance, so the tier is a field on
every point and summary rather than something a caller keeps track of alongside them. The three
spellings are the three planes' own: `factor_obs_*`, `factor_proc_*`, `factor_neut*`.
"""

FACTOR_TIERS: Final[frozenset[str]] = frozenset(get_args(FactorTier))

FACTOR_TIER_ORDER: Final[tuple[FactorTier, ...]] = get_args(FactorTier)
"""Raw, then processed, then neutralised -- the order the planes derive in."""

RAW_COVERAGE_ORDER: Final[tuple[FactorCoverage, ...]] = ("computed", *MISSING_VALUE_COVERAGE_ORDER)
"""The five raw coverage codes in reporting order, derived rather than restated.

`panel_factors.FACTOR_COVERAGE_ORDER` is the same tuple and this module may not import it -- that
is a top-level module over the panel plane, and an edge from `backtest/` to it would reach DuckDB
through `panel/store.py`. So it is built from `domain/factor_transform.MISSING_VALUE_COVERAGE
_ORDER`, which is itself reconciled against `FACTOR_COVERAGE_CODES` at that module's import, and
`tests/unit/backtest/test_factor_ic.py::test_the_raw_census_order_is_the_engines_census_order`
pins the two tuples against each other so the report and the census cannot disagree about column
order.
"""

TIER_COVERAGE_ORDER: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "raw": RAW_COVERAGE_ORDER,
        "processed": PROCESSED_COVERAGE_ORDER,
        "neutralized": NEUTRALIZED_COVERAGE_ORDER,
    }
)
"""Each tier's whole coverage vocabulary, in the order its own contract declares.

Keyed by `str` rather than by `FactorTier` for `PARTICIPATING_PROCESSED_CODES`' measured reason:
`Mapping` is invariant in its key type, so a table annotated with the `Literal` cannot be handed
to an audit whose parameter is keyed by `str` -- and the audit must take `str`, or the failure
direction it exists to catch (a key that is not a declared tier) would be unwritable in a test.
"""

TIER_VALUE_CODES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "raw": frozenset({"computed"}),
        "processed": PROCESSED_VALUE_CODES,
        "neutralized": NEUTRALIZED_VALUE_CODES,
    }
)
"""Which of each tier's codes carry a number at all. Not the same table as the one below."""

TIER_IMPUTING_CODES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "raw": frozenset(),
        "processed": IMPUTING_PROCESSED_CODES,
        "neutralized": frozenset(),
    }
)
"""Which of each tier's **value** codes carries a number no security produced.

The table `TIER_ADMITTED_CODES` is `TIER_VALUE_CODES` minus, and it is here as data so that
`_refuse_an_admitted_table_that_admits_a_number_nobody_measured` has something to derive the
admitted table from at import instead of a literal nobody can see move. Two of the three cells are
empty and each emptiness is a claim about its own contract rather than a default:

- **`raw`** -- `FactorCoverage`'s invariant is "exactly `computed` carries a value" and the
  vocabulary has no fill code at all, so every raw number is a measurement by construction. A
  security the engine could not value gets one of five codes and never a substitute.
- **`processed`** -- the one non-empty cell, and it is `domain/factor_transform.py`'s own
  `IMPUTING_PROCESSED_CODES` rather than a restatement of it. That module derives the set by
  driving `validate_processed_factor_observation` and refuses to load if the two disagree, so the
  cell below moves when the row contract does.
- **`neutralized`** -- a residual is a number the regression *produced*, and
  `NEUTRALIZED_VALUE_CODES`' own docstring says the plane imputes nothing: a security that cannot
  be regressed gets a code, never a substitute residual. This is emphatically **not** the claim
  that a residual's input was measured -- under `participation="measured_and_imputed"` a residual
  may be regressed off an imputed processed value, which `NeutralizedFactorObservation
  .source_coverage` carries and which is a property of the declared neutralisation rather than of
  this tier's vocabulary.
"""

TIER_ADMITTED_CODES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "raw": frozenset({"computed"}),
        "processed": frozenset({"processed"}),
        "neutralized": frozenset({"neutralized"}),
    }
)
"""Which of each tier's codes enter a correlation. See this module's docstring.

The two tables differ in exactly one cell -- `processed` carries a value under `imputed` and this
one does not admit it -- and `_refuse_a_tier_table_that_disagrees_with_its_own_contract` asserts
that at import in both directions, so a sixth processed code that carried a value would fail the
module's load rather than being dropped from every IC with nothing able to say so.

**That audit bounds this table and does not determine it**, which is the gap
`_refuse_an_admitted_table_that_admits_a_number_nobody_measured` closes beside it: `admitted <=
value` admits an empty row (a tier that admits nothing, so every cross section on it reports
`insufficient_sample` and no census column says why) and admits `processed` taking `imputed` in
(a made-up number inside every processed IC, spread and funnel). Both were reachable with the
whole suite's import intact, and both are now import failures.
"""

ICCoverage = Literal[
    "measured",
    "insufficient_sample",
    "degenerate_scores",
    "degenerate_returns",
]
"""Whether this `as_of` produced an IC, and if not, **why** -- never a `None` on its own.

Read in the order `FactorICStudy.measure` decides them, which is also the order they are
declared in:

- **`insufficient_sample`** -- fewer admitted, labelled pairs than `FactorICSpec.min_securities`.
  Decided first because the other two are questions about a cross section, and there is no cross
  section here worth asking them of.
- **`degenerate_scores`** -- enough pairs and every factor value ties, so the correlation's
  denominator is zero on the factor side. A defect in the factor at this `as_of`.
- **`degenerate_returns`** -- enough pairs, the scores order, and every forward return ties. A
  fact about the market rather than about the factor, which is why it is not one code with the
  line above.
- **`measured`** -- and only then are `raw_ic` and `ic` not `None`.

Closed for `FactorCoverage`'s reason: `V2-P3-014`'s report groups by it, and a fifth spelling of
"no answer" would silently become a group of one.
"""

IC_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(ICCoverage))

IC_COVERAGE_ORDER: Final[tuple[ICCoverage, ...]] = get_args(ICCoverage)

ICStabilityCoverage = Literal["measured", "insufficient_as_ofs"]
"""Whether a series of points produced stability statistics, and if not, why.

Two members rather than four: the per-`as_of` degeneracies have already been decided one level
down and arrive here as points that are simply not `measured`. What is left is a count.
"""

IC_STABILITY_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(ICStabilityCoverage))

MINIMUM_IC_SECURITIES: Final[int] = 3
"""The floor under `FactorICSpec.min_securities`, and it is arithmetic rather than taste.

Two points that tie on neither axis lie on one line, so a correlation of them has magnitude one
whatever the two securities did -- a number whose magnitude carries no information at all, and
whose sign is the sign of one difference. Three is the first cross-section size at which
`|r| < 1` is attainable. Measured rather than asserted, and the measurement is why "magnitude
one" is not written here as "exactly 1.0" -- eight of ten random pairs come out
`0.9999999999999998`; see this module's docstring.

This is the bound on the *declaration*, not a recommendation: `min_securities=3` is legal and
`KNOWN_IC_LIMITATIONS` records what it buys.
"""

MAXIMUM_IC_SECURITIES: Final[int] = 10_000
"""The range check on the stored floor, `FactorTransformSpec.min_cross_section`'s own bound.

Above ADR-0002's ~5,500-name whole-market cross section on purpose, and for that contract's
stated reason: a floor a caller can never reach is a declarable and auditable choice (every
`as_of` reports `insufficient_sample`, with the sample size stored), while refusing it would
hard-code today's listing count into a contract. What the bound buys is that a caller who typed a
share count into the field is refused here rather than at the first cross section.
"""

MINIMUM_IC_AS_OFS: Final[int] = 2
"""The floor under `FactorICSpec.min_as_ofs`. A sample standard deviation of one number does not
exist, so an ICIR over a single `as_of` is undefined rather than merely weak."""

MAXIMUM_IC_AS_OFS: Final[int] = 10_000
"""The range check on the stored floor. Roughly forty A-share years of daily as_ofs, so no
horizon this repository can label argues with it, and a caller who typed a row count is refused
at the contract."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ICLimitation:
    """One named boundary on what a stored IC can be trusted to answer."""

    code: str
    detail: str


KNOWN_IC_LIMITATIONS: Final[tuple[ICLimitation, ...]] = (
    ICLimitation(
        code="neutralised_residuals_are_read_at_a_year_end_snapshot",
        detail=(
            "A neutralised IC is not a point-in-time series the way a raw or processed one is. "
            "panel_neutralization.neutralized_observation_batch stamps all four clocks of every "
            "residual row with the build's own as_of, and that as_of is at or after the "
            "max_available_time of the year's daily_basic partition -- the year's last session. "
            "Reads go through PanelStore.read_visible_at, so an as_of inside the covered year "
            "returns nothing rather than raising, and what a caller does read back was stamped "
            "at the year end. V2-P3-004's review measured this and judged it non-blocking here "
            "because the residuals' CONTENT is clean: each was regressed against the industry, "
            "the market capitalisation and the processed value of its own day, so neither the IC "
            "nor the decay curve is forward-contaminated. What is lost is the honesty of the "
            "timestamps. V2-P4-026 (an as-of-sensitive session-level read of daily_basic) is the "
            "fix and is a hard precondition of V2-P4-013."
        ),
    ),
    ICLimitation(
        code="the_forward_return_is_cumulative_rather_than_marginal",
        detail=(
            "Every rung of a decay curve reads WindowReturn.adjusted, which is entry-to-exit: "
            "the rung at 10 sessions is the return of a position opened once and held ten "
            "sessions, not the return of session ten alone. A curve that flattens therefore says "
            "'holding longer stops adding' and not 'the factor stopped predicting'. The marginal "
            "reading would need a label contract that measures one session inside a window, and "
            "domain/labels.py deliberately has none -- its window is the unit a supervised "
            "target is defined over."
        ),
    ),
    ICLimitation(
        code="an_ic_series_over_overlapping_windows_is_autocorrelated",
        detail=(
            "At horizon h, two prediction days one session apart produce windows sharing h of "
            "the h + 1 sessions each spans -- five of six at 5d, nine of ten at 10d, which "
            "domain/labels.py measures and reports through overlapping_windows. So the ICs in a "
            "daily series are not independent draws, and stdev_ic understates the sampling "
            "error of mean_ic. This module reports mean, dispersion, ICIR and sign consistency "
            "and deliberately publishes NO t-statistic or confidence interval, because both "
            "would carry an independence claim that is false by construction. De-overlapping "
            "needs a purge, a purge needs a train/test split, and nothing in this repository "
            "defines one yet (KNOWN_LABEL_LIMITATIONS says the same about S28)."
        ),
    ),
    ICLimitation(
        code="a_declared_sample_floor_of_three_is_legal_and_is_almost_all_noise",
        detail=(
            "MINIMUM_IC_SECURITIES is 3 because two points always correlate perfectly, not "
            "because three securities make a cross section. A spec declaring min_securities=3 "
            "constructs, and every IC it produces is dominated by sampling variation -- the same "
            "shape FactorTransformSpec.min_cross_section records for a 1% winsorization of three "
            "names, where the fraction actually clipped is max(1/n, q). The contract records "
            "which floor was chosen and refuses to choose one; a report that quotes an IC "
            "without its sample_size is quoting a number whose scale it has not shown."
        ),
    ),
    ICLimitation(
        code="the_windows_dating_zone_is_the_callers_and_is_not_checked_against_the_exchange",
        detail=(
            "ic_cross_section refuses a window whose entry session is at or before the as_of's "
            "own session, dated in the window's own zone -- the same operation "
            "build_label_window performs. What it cannot check is which zone that was. "
            "domain/labels.py's MINIMUM_LABEL_ZONE_OFFSET already refuses a zone far enough west "
            "to date a look-ahead, so the remaining freedom is bounded rather than open, but a "
            "window dated in UTC stamps its close instants three hours after the real Shanghai "
            "close and this module cannot tell that from an exchange-dated one. "
            "panel/catalog.py's DEFAULT_DATE_TIMEZONE is the value this repository passes."
        ),
    ),
)
"""What a stored IC does not answer, as a closed registry rather than as prose.

Every entry is bound to the suite by `tests/unit/test_known_limitation_registries.py`, which
requires each `code` to appear as a string literal in *executable* test code -- the P2 review
measured that a code named only in docstrings can be renamed with the whole suite staying green.
"""

IC_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_IC_LIMITATIONS
)


def _refuse_a_tier_table_that_disagrees_with_its_own_contract(
    tiers: Sequence[str],
    coverage_order: Mapping[str, tuple[str, ...]],
    value_codes: Mapping[str, frozenset[str]],
    admitted_codes: Mapping[str, frozenset[str]],
) -> None:
    """Refuse this module at **import** unless the four tables agree with the three contracts.

    The direction a per-function test cannot reach, and `panel_factors._refuse_table_drift`'s
    shape: a sixth processed coverage code, or a seventh raw one, arrives in `domain/` and this
    module would otherwise go on filtering against a set that no longer describes the vocabulary
    -- dropping every row carrying the new code from every correlation, silently, with no census
    column able to say so. A module that refuses to load is a failure a caller cannot route
    around; a test is a failure only if somebody runs it.

    Four properties, each of which has failed somewhere in this repository already:

    1. Every tier has a row in all three tables, and no table has a row that is not a tier.
    2. Each tier's declared order is exactly its contract's code set -- no duplicates, nothing
       missing, nothing extra.
    3. Each tier's value codes are a subset of its vocabulary.
    4. Each tier's admitted codes are a subset of its value codes. Admitting a code that carries
       no number would put a `None` into a correlation.

    Takes its inputs as arguments rather than reading this module's own globals, so that every
    failure direction is drivable from a test. An audit whose only call site is the one that
    passes is an audit nobody has seen fail.
    """
    declared = set(tiers)
    for name, table in (
        ("TIER_COVERAGE_ORDER", coverage_order),
        ("TIER_VALUE_CODES", value_codes),
        ("TIER_ADMITTED_CODES", admitted_codes),
    ):
        if set(table) != declared:
            raise FactorICError(
                f"{name} is keyed by {sorted(table)} and the declared tiers are "
                f"{sorted(declared)}; a tier with no row would have no vocabulary to filter "
                "against, and a row that is no tier is a table nothing can reach"
            )
    vocabularies: Mapping[str, frozenset[str]] = MappingProxyType(
        {
            "raw": FACTOR_COVERAGE_CODES,
            "processed": PROCESSED_COVERAGE_CODES,
            "neutralized": NEUTRALIZED_COVERAGE_CODES,
        }
    )
    if set(vocabularies) != declared:
        raise FactorICError(
            f"the contract vocabularies cover {sorted(vocabularies)} and the declared tiers are "
            f"{sorted(declared)}"
        )
    for tier in sorted(declared):
        order = coverage_order[tier]
        vocabulary = vocabularies[tier]
        if len(set(order)) != len(order) or set(order) != set(vocabulary):
            raise FactorICError(
                f"TIER_COVERAGE_ORDER[{tier!r}] is {list(order)} and the contract declares "
                f"{sorted(vocabulary)}; the census key order has to be a permutation of the "
                "vocabulary, or a report would carry a column for a code nothing produces or "
                "drop one for a code something does"
            )
        if not value_codes[tier] <= set(vocabulary):
            raise FactorICError(
                f"TIER_VALUE_CODES[{tier!r}] names {sorted(value_codes[tier] - set(vocabulary))}, "
                f"which the {tier} contract does not declare"
            )
        if not admitted_codes[tier] <= value_codes[tier]:
            raise FactorICError(
                f"TIER_ADMITTED_CODES[{tier!r}] admits "
                f"{sorted(admitted_codes[tier] - value_codes[tier])}, which carries no value; a "
                "correlation cannot consume a None"
            )


def _refuse_an_admitted_table_that_admits_a_number_nobody_measured(
    tiers: Sequence[str],
    value_codes: Mapping[str, frozenset[str]],
    admitted_codes: Mapping[str, frozenset[str]],
    imputing_codes: Mapping[str, frozenset[str]],
) -> None:
    """Refuse this module at **import** unless each tier admits exactly its measured value codes.

    A second audit rather than two more branches inside the first, because the two answer
    different questions and the first one's four properties are all *bounds*: they say the tables
    are drawn from the right vocabularies. This one says the admitted table is the **right** one,
    and it exists because the bound leaves two shapes reachable that the four do not catch. Both
    were measured on this build with every module importing and the whole suite collecting:

    1. **A tier that admits nothing.** `frozenset() <= anything`, so three empty rows satisfy the
       first audit. Every cross section then reports `insufficient_sample` at every `as_of`, on
       every tier, with `ICCensus.admitted_count` at zero and no code anywhere saying that the
       *table* rather than the market emptied it.
    2. **`processed` admitting `imputed`.** `{processed, imputed} <= {processed, imputed}`
       satisfies the first audit too, and it puts a number this repository made up inside every
       processed information coefficient, every quantile portfolio built on one, and
       `CoverageFunnel.admission_rate` -- which is the one cell in which the two tier tables
       differ and therefore the one number that would silently become `1.0` everywhere.

    Neither is caught by pairwise disjointness of the three admitted sets, which is the assertion
    an audit of this module first proposed: the three sets stay pairwise disjoint under both
    mutations (∅ is disjoint from ∅, and `{processed, imputed}` is disjoint from `{computed}` and
    from `{neutralized}`), and disjointness is in any case *already implied* by the first audit --
    each tier's admitted set is inside its own value set, the only code two vocabularies share is
    `insufficient_cross_section` (processed and neutralised), and that code carries a value in
    neither. An assertion nothing can make fail is an assertion nobody has seen fail.

    Three properties, in the order a reader needs them:

    1. Every tier has a row in the imputing table and no row of it is not a tier.
    2. A tier's imputing codes are among the codes it values. A code that imputes and carries no
       value is a contradiction, and subtracting it below would silently do nothing.
    3. **`admitted == value - imputing`.** Equality, in both directions: a tier that admits less
       is dropping measured rows from every statistic, and a tier that admits more is admitting a
       fill.

    Takes its tables as arguments for `_refuse_a_tier_table_that_disagrees_with_its_own_contract`'s
    reason, so every failure direction is drivable;
    `tests/unit/backtest/test_factor_ic.py::
    test_the_admitted_table_audit_refuses_an_empty_row_and_an_admitted_imputation` is where they
    are driven.
    """
    declared = set(tiers)
    if set(imputing_codes) != declared:
        raise FactorICError(
            f"TIER_IMPUTING_CODES is keyed by {sorted(imputing_codes)} and the declared tiers are "
            f"{sorted(declared)}; a tier with no row would have nothing to subtract, and a row "
            "that is no tier is a table nothing can reach"
        )
    for tier in sorted(declared):
        if not imputing_codes[tier] <= value_codes[tier]:
            raise FactorICError(
                f"TIER_IMPUTING_CODES[{tier!r}] names "
                f"{sorted(imputing_codes[tier] - value_codes[tier])}, which carries no value at "
                "that tier; a code that imputes nothing to impute is a cell that subtracts nothing"
            )
        measured = value_codes[tier] - imputing_codes[tier]
        if admitted_codes[tier] != measured:
            raise FactorICError(
                f"TIER_ADMITTED_CODES[{tier!r}] admits {sorted(admitted_codes[tier])} and the "
                f"{tier} contract's measured value codes are {sorted(measured)}; a tier admits "
                "exactly the codes whose number a security produced, so admitting fewer drops "
                "measured rows out of every statistic and admitting more puts a number this "
                "repository made up into one"
            )


_refuse_a_tier_table_that_disagrees_with_its_own_contract(
    FACTOR_TIER_ORDER, TIER_COVERAGE_ORDER, TIER_VALUE_CODES, TIER_ADMITTED_CODES
)

_refuse_an_admitted_table_that_admits_a_number_nobody_measured(
    FACTOR_TIER_ORDER, TIER_VALUE_CODES, TIER_ADMITTED_CODES, TIER_IMPUTING_CODES
)


def average_ranks(values: Sequence[float]) -> tuple[float, ...]:
    """One-based ranks in the argument's own order, with tied values sharing their average rank.

    `(1.0, 3.0, 3.0, 7.0)` ranks as `(1.0, 2.5, 2.5, 4.0)`: the two tied values would have taken
    ranks 2 and 3, and averaging keeps the rank sum equal to `n (n + 1) / 2` whatever the ties
    are -- which is what makes a rank correlation of a tied cross section still a correlation of
    two centred vectors rather than of two vectors with different means.

    The same rule `panel_factors._average_ranks` applies for `_standardize_rank`, and the two are
    two implementations rather than one shared helper because this module is a standard-library
    leaf and that one sits over the panel plane; see this module's docstring for the test that
    holds them equal element-wise. Public rather than private for exactly that reason: a private
    name in two modules is two rules, and the pin has to be able to call this one directly.

    Ties are found by **exact** float equality, which is `_standardize_rank`'s own bound and is
    the honest one: a tolerance would need a scale to be relative to, and the only scale on offer
    is the cross section's own dispersion -- the quantity a rank IC exists to avoid depending on.
    """
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + end) / 2.0 + 1.0
        for position in range(start, end + 1):
            ranks[order[position]] = average
        start = end + 1
    return tuple(ranks)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """The product-moment correlation of two non-degenerate vectors, clamped to `[-1, 1]`.

    Both deviation vectors are divided by their own largest absolute deviation before the sums of
    squares are taken. The scaling cancels exactly out of a correlation and makes the sums `O(n)`
    numbers of order one, so the result is finite for every finite input.

    **The failure it prevents is measured, and it is a wrong number rather than an error.**
    Unscaled, `sum(d * d)` reaches `inf` once a deviation passes `sqrt(float_info.max / n)`, which
    is `1.80e152` at `n = 5,534`. The numerator stays finite, so the quotient is not a `nan` that
    `ICPoint` would refuse -- it is `finite / inf`, which is **`0.0`**: a perfectly ordered cross
    section reported as an IC of exactly zero. Driven on a five-name cross section at `1e200`,
    where the unscaled expression gives `0.0` and this one gives `0.48`.

    **What this is not.** The earlier wording said "5,534 values whose scale is a market
    capitalisation", and that was wrong by 143 orders of magnitude: a whole-market `total_mv` in
    万元 reaches about `1e9`, whose squares sum to `5.5e21`. Nothing this repository fetches gets
    near the threshold. What the guard is for is that `validate_factor_observation` and its two
    siblings admit *any* finite float, `sys.float_info.max` is finite, and a factor whose
    definition divides by a near-zero denominator can produce one -- so the input is admissible by
    contract even though no provider has served one. `_standardize_zscore` made the same call for
    the same reason one plane down.

    Clamped because rounding can put the last bit outside the range -- `1.0000000000000002` for
    two vectors that are exact affine images of each other -- and an IC of more than one is a
    number no reader can interpret and no bounded field can store. The caller has already ruled
    out a zero denominator on either side; this function is not the place that decides
    degeneracy, because "which side collapsed" is a coverage code and not an arithmetic result.
    """
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    scale_x = max(abs(value) for value in dx)
    scale_y = max(abs(value) for value in dy)
    ux = [value / scale_x for value in dx]
    uy = [value / scale_y for value in dy]
    covariance = sum(a * b for a, b in zip(ux, uy, strict=True))
    dispersion = math.sqrt(sum(a * a for a in ux)) * math.sqrt(sum(b * b for b in uy))
    return max(-1.0, min(1.0, covariance / dispersion))


def _degenerate_side(scores: Sequence[float], returns: Sequence[float]) -> ICCoverage | None:
    """Which side of the correlation has nothing to order, or `None` when both do.

    `min == max` is the same test `_standardize_rank` uses for "everything ties", and on this
    plane it is also exactly the condition under which the correlation's denominator is zero --
    for `pearson` because the deviations all vanish, and for `spearman` because every average
    rank is `(n + 1) / 2`. One test therefore covers both methods, which is why the coverage code
    does not depend on the method.

    The scores are checked before the returns, and the precedence is stated rather than
    incidental: a cross section in which both sides tie is a defect in the factor *and* an
    unusable day, and naming the factor first puts the report on the half somebody can act on.
    """
    if min(scores) == max(scores):
        return "degenerate_scores"
    if min(returns) == max(returns):
        return "degenerate_returns"
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class ICObservationPair:
    """One security's `(factor value, realised forward return)` at one `as_of`.

    A plain carrier -- `LabelRefusal`'s precedent -- with two invariants that would otherwise be
    silent lies downstream: a named subject, and two finite numbers. The finiteness check is here
    as well as on the three observation contracts because this is a *different* boundary: the
    forward return arrives from `OutcomeLabel.realized_return`, which no factor contract has ever
    seen, and a `nan` on either side poisons a mean, a rank and a correlation identically.
    """

    subject: str
    score: float
    forward_return: float

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise FactorICError("an IC pair must name a subject")
        for name in ("score", "forward_return"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise FactorICError(
                    f"{self.subject}'s {name} is {value!r}, which is not a finite number; a "
                    "non-finite term poisons every mean, rank and correlation built on it"
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class ICCensus:
    """What became of every security offered to one cross section, as numbers that add up.

    `FactorTransformStatistics`' instrument pointed at this plane's own failure. A correlation
    reports one float, and a build in which nine tenths of the market was dropped produces a float
    that looks exactly like one in which none of it was -- the same shape `coverage_census()`
    exists for one plane down, where a build that scored nobody reached Parquet looking like one
    that had scored the whole market.

    Three scalars and one cell per excluded code, and `__post_init__` requires all of them to
    total `subject_count`. That is what makes any one of them un-fudgeable: a census that quietly
    dropped a security, or double-counted one under two headings, fails its own arithmetic rather
    than reporting a plausible total.

    `excluded_by_coverage` carries **every** non-admitted code of the tier's vocabulary, in that
    vocabulary's declared order, including the ones that did not occur. A code missing from the
    tuple and a code present with a zero are different claims -- "nobody was `input_missing`"
    against "nothing looked" -- and a report that dropped the zero rows would collapse them.
    """

    tier: FactorTier
    subject_count: int
    admitted_count: int
    excluded_by_coverage: tuple[tuple[str, int], ...]
    unlabelled_count: int
    """Securities whose value was admitted and whose `OutcomeLabel` carried refusals -- halted,
    locked at a limit, no published band, delisted inside the window. Counted rather than filled
    with a zero return, which would teach every downstream statistic that halts are flat."""
    unmatched_count: int
    """Securities whose value was admitted and for which the caller offered no label at all.
    Separate from `unlabelled_count` for `explain_unpriced`'s reason: a refused label and a label
    that was never fetched are different findings, and a short read looks exactly like the
    second."""

    def __post_init__(self) -> None:
        if self.tier not in FACTOR_TIERS:
            raise FactorICError(
                f"{self.tier!r} is not a declared tier; expected one of {sorted(FACTOR_TIERS)}"
            )
        for name in ("subject_count", "admitted_count", "unlabelled_count", "unmatched_count"):
            if int(getattr(self, name)) < 0:
                raise FactorICError(f"{name} cannot be negative")
        expected = tuple(
            code
            for code in TIER_COVERAGE_ORDER[self.tier]
            if code not in TIER_ADMITTED_CODES[self.tier]
        )
        if tuple(code for code, _count in self.excluded_by_coverage) != expected:
            raise FactorICError(
                f"excluded_by_coverage is keyed by "
                f"{[code for code, _count in self.excluded_by_coverage]} and the {self.tier} tier "
                f"excludes {list(expected)} in that order; a census missing a code cannot be told "
                "from one whose count is zero"
            )
        if any(count < 0 for _code, count in self.excluded_by_coverage):
            raise FactorICError("an excluded-coverage count cannot be negative")
        total = (
            self.admitted_count
            + self.unlabelled_count
            + self.unmatched_count
            + sum(count for _code, count in self.excluded_by_coverage)
        )
        if total != self.subject_count:
            raise FactorICError(
                f"the census accounts for {total} securities and {self.subject_count} were "
                "offered; every subject is admitted, excluded by its coverage code, unlabelled "
                "or unmatched, and a census that does not add up has lost one of them"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ICCrossSection:
    """One `as_of`'s scored, labelled cross section on one tier, and what it cost to build.

    Built by `ic_cross_section` and its three tier-specific wrappers; this constructor is not a
    boundary and re-derives nothing, following `LabelWindow`'s precedent -- every field is what
    that function established, so `census.admitted_count == len(pairs)` holds by construction.

    The window facts travel with the pairs rather than beside them because they are what makes
    the number interpretable: an IC of 0.04 at 5 sessions and one at 60 are not comparable, and
    `horizon` is the axis `ICDecayCurve` lays them out on.
    """

    as_of: datetime
    tier: FactorTier
    pairs: tuple[ICObservationPair, ...]
    census: ICCensus
    horizon: ResearchHorizon
    prediction_day: date
    entry_day: date
    exit_day: date

    @property
    def scores(self) -> tuple[float, ...]:
        return tuple(pair.score for pair in self.pairs)

    @property
    def forward_returns(self) -> tuple[float, ...]:
        return tuple(pair.forward_return for pair in self.pairs)


class FactorICSpec(BaseModel):
    """The declared policy an IC study applies: which factor, which correlation, which floors.

    `definition` rather than a bare `direction`, and that is the whole argument of this contract
    in one field. The sign of an IC is decided by a *declared* property of the factor
    (`FactorDefinition.direction`), and a study that took the direction as its own argument would
    be one a caller can hand the other value to -- which is precisely how a factor comes to be
    reported as working in whichever direction it happened to come out. Carrying the definition
    makes the wrong direction unreachable rather than discouraged, and puts `factor_id` on every
    point and summary so a stored IC names the definition whose sign convention produced it.

    `method` and the two floors have no defaults, for `FactorTransformSpec.min_cross_section`'s
    reason: each is a decision that moves the answers, and a default is a decision nobody
    recorded making.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    definition: FactorDefinition
    method: ICMethod
    min_securities: int = Field(ge=MINIMUM_IC_SECURITIES, le=MAXIMUM_IC_SECURITIES)
    """The fewest admitted, labelled pairs this study will correlate at all.

    See `MINIMUM_IC_SECURITIES` for why the floor under it is three and why three is a floor
    rather than a recommendation."""
    min_as_ofs: int = Field(ge=MINIMUM_IC_AS_OFS, le=MAXIMUM_IC_AS_OFS)
    """The fewest *measured* points a series needs before this study will call anything
    stability. See `MINIMUM_IC_AS_OFS`."""

    @property
    def direction(self) -> FactorDirection:
        return self.definition.direction

    @property
    def factor_id(self) -> str:
        return self.definition.factor_id

    def orient(self, value: float) -> float:
        """`value` signed so that positive means "the factor worked".

        The one place the declaration reaches the arithmetic. A `higher_is_better` factor's IC is
        reported as measured; a `lower_is_better` factor's is negated, because a rank correlation
        of `-0.03` is evidence *for* it. Exact under IEEE negation, so `ICPoint`'s validator can
        assert the relationship with `==` rather than a tolerance.
        """
        return value if self.direction == "higher_is_better" else -value


class ICPoint(BaseModel):
    """One `as_of`'s answer: the correlation as measured, the same number oriented, or a code.

    A pydantic model rather than a dataclass, unlike the per-security carriers above, and the
    cost argument that decides those runs the other way here: there is one of these per `as_of`
    rather than one per `(security, as_of)`, so a year is 244 and not 1.35e6, and what is wanted
    at that scale is the validator.

    The validator is the contract. `raw_ic` and `ic` are present under exactly the `measured`
    code, and `ic` is `raw_ic` under the direction this point was built with -- so an oriented IC
    that contradicts its own declared direction is **not constructible**. Without that rule,
    `direction="lower_is_better", raw_ic=0.04, ic=0.04` builds, and a report reading `ic` would
    say the factor worked when the measurement says it did not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    as_of: datetime
    tier: FactorTier
    method: ICMethod
    direction: FactorDirection
    factor_id: str = Field(min_length=1, max_length=64)
    horizon_sessions: int = Field(ge=1)
    coverage: ICCoverage
    sample_size: int = Field(ge=0)
    """How many admitted, labelled pairs the correlation was taken over -- reported on every
    code, including the ones that produced no number. An IC quoted without it is a number whose
    scale has not been shown, which is what `KNOWN_IC_LIMITATIONS`' sample-floor entry is about."""
    raw_ic: float | None
    """The correlation as computed: factor value against forward return, no interpretation."""
    ic: float | None
    """`raw_ic` oriented by `direction`. Positive always means the factor worked."""

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @field_validator("raw_ic", "ic")
    @classmethod
    def refuse_a_non_finite_correlation(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(
                f"{value!r} is not a finite correlation; a degenerate cross section is what the "
                "degenerate_scores and degenerate_returns codes exist to carry"
            )
        return value

    @model_validator(mode="after")
    def validate_the_orientation_is_the_declared_one(self) -> Self:
        measured = self.coverage == "measured"
        if (self.raw_ic is None) == measured or (self.ic is None) == measured:
            raise ValueError(
                f"coverage {self.coverage!r} carries raw_ic {self.raw_ic!r} and ic {self.ic!r}; "
                "exactly the 'measured' code carries both, and every other code carries neither"
            )
        if self.raw_ic is not None and not -1.0 <= self.raw_ic <= 1.0:
            raise ValueError(f"raw_ic {self.raw_ic!r} is outside [-1, 1] and is not a correlation")
        if self.raw_ic is not None and self.ic is not None:
            expected = self.raw_ic if self.direction == "higher_is_better" else -self.raw_ic
            if self.ic != expected:
                raise ValueError(
                    f"this point declares direction {self.direction!r} and reports raw_ic "
                    f"{self.raw_ic!r} as ic {self.ic!r}; a lower_is_better factor's IC is negated "
                    "so that positive means the factor worked, and an orientation that "
                    "contradicts the declaration makes the stored sign unreadable"
                )
        return self


class ICSummary(BaseModel):
    """A series of points at one tier, method and horizon, reduced to stability statistics.

    Every field's definition is in this module's docstring, and the two worth restating at the
    contract are the two that are `None` for a reason rather than for a missing value:
    `icir` when the dispersion is zero, and every statistic when the series did not clear
    `min_as_ofs`. Both are stated as codes and counts rather than as absent fields.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tier: FactorTier
    method: ICMethod
    direction: FactorDirection
    factor_id: str = Field(min_length=1, max_length=64)
    horizon_sessions: int = Field(ge=1)
    coverage: ICStabilityCoverage
    as_ofs: tuple[datetime, ...]
    """Every `as_of` offered to this summary, in ascending order -- not only the measured ones.

    Carried rather than counted because `ICDecayCurve` requires its rungs to be over the *same*
    offered sample, and a count cannot say that: two 60-point series over disjoint years share a
    count and nothing else. This is `FactorBuildManifest.subject_digest`'s argument on a smaller
    object, and the tuple is carried rather than digested because a summary is one object per
    study rather than one per row."""
    measured_count: int = Field(ge=0)
    """How many of `as_ofs` produced an IC. `len(as_ofs) - measured_count` is the attrition, and
    it is what separates a decaying factor from a shrinking sample on a decay curve."""
    mean_ic: float | None
    stdev_ic: float | None
    """The **sample** standard deviation (`n - 1`) of the measured, oriented ICs."""
    icir: float | None
    """`mean_ic / stdev_ic`, or `None` when there is no dispersion to divide by. See this
    module's docstring for why this is `None` and not `math.inf`."""
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    zero_count: int = Field(ge=0)
    sign_consistency: float | None
    """`positive_count / measured_count` on the oriented IC. A zero IC is in the denominator and
    in neither numerator bucket, because a zero is not evidence for the factor."""

    @field_validator("mean_ic", "stdev_ic", "icir", "sign_consistency")
    @classmethod
    def refuse_a_non_finite_statistic(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{value!r} is not a finite statistic")
        return value

    @model_validator(mode="after")
    def validate_the_statistics_match_the_coverage(self) -> Self:
        measured = self.coverage == "measured"
        for name in ("mean_ic", "stdev_ic", "sign_consistency"):
            if (getattr(self, name) is None) == measured:
                raise ValueError(
                    f"coverage {self.coverage!r} carries {name} {getattr(self, name)!r}; exactly "
                    "the 'measured' code carries the statistics, and icir is the one exception "
                    "-- it is None when the dispersion is zero"
                )
        if self.icir is not None and self.coverage != "measured":
            raise ValueError(f"coverage {self.coverage!r} cannot carry an icir")
        if self.measured_count > len(self.as_ofs):
            raise ValueError(
                f"{self.measured_count} points were measured and {len(self.as_ofs)} as_ofs were "
                "offered; a series cannot measure an as_of it was not given"
            )
        signed = self.positive_count + self.negative_count + self.zero_count
        if signed != self.measured_count:
            raise ValueError(
                f"{signed} measured ICs were signed and {self.measured_count} were measured; "
                "every measured IC is positive, negative or zero"
            )
        if len(set(self.as_ofs)) != len(self.as_ofs) or list(self.as_ofs) != sorted(self.as_ofs):
            raise ValueError(
                "as_ofs must be distinct and ascending; a repeated as_of is one cross section "
                "counted twice, and an unordered tuple makes two identical studies compare unequal"
            )
        return self


class ICDecayRung(BaseModel):
    """One horizon's summary on a decay curve."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon_sessions: int = Field(ge=1)
    summary: ICSummary

    @model_validator(mode="after")
    def validate_the_rung_names_its_summarys_horizon(self) -> Self:
        if self.horizon_sessions != self.summary.horizon_sessions:
            raise ValueError(
                f"this rung is filed at {self.horizon_sessions} sessions and summarises "
                f"{self.summary.horizon_sessions}; the axis a curve is read against cannot be a "
                "second source of truth for the window the numbers came from"
            )
        return self


class ICDecayCurve(BaseModel):
    """How one factor's IC behaves as the forward window lengthens, over one fixed sample.

    See this module's docstring for what the axis is, why the return at each rung is cumulative
    rather than marginal, and why every rung is held to the same offered `as_of`s.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tier: FactorTier
    method: ICMethod
    direction: FactorDirection
    factor_id: str = Field(min_length=1, max_length=64)
    rungs: tuple[ICDecayRung, ...] = Field(min_length=2)
    """At least two, because one point is not a curve and "decay" is a statement about a
    difference between horizons."""

    @model_validator(mode="after")
    def validate_the_rungs_are_one_study_over_one_sample(self) -> Self:
        horizons = [rung.horizon_sessions for rung in self.rungs]
        if horizons != sorted(set(horizons)):
            raise ValueError(
                f"the rungs are filed at {horizons}; a decay curve is read left to right, so the "
                "horizons must be strictly increasing -- a repeated one is two answers to one "
                "question and an unordered one makes the curve's shape an artefact of call order"
            )
        for rung in self.rungs:
            summary = rung.summary
            mismatched = [
                f"{name}={getattr(summary, name)!r} against {getattr(self, name)!r}"
                for name in ("tier", "method", "direction", "factor_id")
                if getattr(summary, name) != getattr(self, name)
            ]
            if mismatched:
                raise ValueError(
                    f"the rung at {rung.horizon_sessions} sessions reports {mismatched}; a decay "
                    "curve is one factor on one tier under one correlation, and a rung that is "
                    "not makes the curve a comparison of two different things"
                )
        _refuse_rungs_over_different_samples(self.rungs)
        return self

    @property
    def horizons(self) -> tuple[int, ...]:
        return tuple(rung.horizon_sessions for rung in self.rungs)

    @property
    def mean_ics(self) -> tuple[float | None, ...]:
        """Each rung's `mean_ic`, in horizon order -- the curve itself."""
        return tuple(rung.summary.mean_ic for rung in self.rungs)


def _refuse_rungs_over_different_samples(rungs: Sequence[ICDecayRung]) -> None:
    """Refuse a curve whose rungs were measured over different offered `as_of`s.

    The rule this contract exists for. A curve falling from 0.05 to 0.01 is a decaying factor if
    the two rungs were asked about the same days, and is nothing at all if the longer horizon's
    windows ran off the end of the calendar and it was asked about a third of them. Nothing on
    the curve separates those two readings, so the sample is held identical and the *attrition*
    is reported instead: each rung's `measured_count` against the common `as_ofs`.

    Offered rather than measured, deliberately. A longer horizon legitimately loses as_ofs to
    refused labels -- a window spanning more sessions meets more halts -- and requiring the
    measured sets to match would refuse the ordinary case. What is refused is being *asked* a
    different question.
    """
    first = rungs[0].summary.as_ofs
    for rung in rungs[1:]:
        if rung.summary.as_ofs != first:
            raise ValueError(
                f"the rung at {rung.horizon_sessions} sessions was offered "
                f"{len(rung.summary.as_ofs)} as_of(s) and the rung at "
                f"{rungs[0].horizon_sessions} was offered {len(first)}; every rung of a decay "
                "curve has to be asked about the same days, or a fall in IC cannot be told from "
                "a sample that shrank underneath it"
            )


def _refuse_a_window_that_is_not_forward_of(as_of: datetime, window: LabelWindow) -> None:
    """Refuse a label whose entry session is at or before the `as_of`'s own session.

    The entry session's close is one of the two prices the return is measured from, so a window
    entering on the `as_of`'s own session -- or an earlier one -- prices a move that had already
    happened when the factor value was stamped. That is not a weak IC; it is a correlation
    against the past wearing the label of a forward return.

    Dated in the **window's own zone**, which is the operation `build_label_window` performs to
    get `prediction_day` from an instant, so the two agree about which session an instant belongs
    to. What this cannot check is which zone that was; see `KNOWN_IC_LIMITATIONS`.
    """
    session = ensure_aware(as_of).astimezone(window.zone).date()
    if window.entry_day <= session:
        raise FactorICError(
            f"the label window enters on {window.entry_day.isoformat()} and the factor value is "
            f"stamped on {session.isoformat()} in the window's own zone ({window.zone}); the "
            "entry session's close is one of the two prices the return is measured from, so a "
            "window entering at or before the as_of scores a move that had already happened"
        )


def _refuse_labels_over_more_than_one_window(labels: Mapping[str, OutcomeLabel]) -> LabelWindow:
    """The one window every label in a cross section is over, or a refusal naming the disagreement.

    A cross-sectional correlation compares one number per security against another number per
    security, and both have to mean the same thing for every security. Half a cross section
    labelled over five sessions and half over sixty is not a weak IC either -- the two halves are
    measuring different quantities and the correlation between them has no reading.

    Compared on `(prediction_day, entry_day, exit_day, horizon)` rather than on the whole
    `LabelWindow`, because `sessions` is derived from the three dates and the calendar, and
    `zone` is a `tzinfo` whose equality is by identity for some implementations -- two labels
    built with two `ZoneInfo("Asia/Shanghai")` instances are one window in every sense this rule
    cares about.
    """
    windows = [label.window for label in labels.values()]
    if not windows:
        raise FactorICError(
            "no labels were offered, so there is no window to correlate against; an IC over an "
            "empty label set would be an insufficient_sample verdict about a question nobody asked"
        )
    keys = {
        (
            window.prediction_day,
            window.entry_day,
            window.exit_day,
            window.horizon.text,
        )
        for window in windows
    }
    if len(keys) != 1:
        rendered = sorted(
            f"{prediction.isoformat()}->{entry.isoformat()}..{exit_day.isoformat()} ({text})"
            for prediction, entry, exit_day, text in keys
        )
        raise FactorICError(
            f"the labels offered at one as_of span {len(keys)} different windows ({rendered}); a "
            "cross-sectional correlation compares one quantity across securities, and half a "
            "cross section measured over five sessions against half measured over sixty is two "
            "quantities"
        )
    return windows[0]


def ic_cross_section(
    *,
    as_of: datetime,
    tier: FactorTier,
    rows: Sequence[tuple[str, float | None, str]],
    labels: Mapping[str, OutcomeLabel],
) -> ICCrossSection:
    """Pair one tier's observations against their labels, and count everything that did not pair.

    `rows` is `(subject, value, coverage)` -- the three columns every one of the three
    observation contracts carries under different names. The three public wrappers below project
    their own dataclass into it, so the *rules* below (which codes are admitted, what a refused
    label costs, what a missing one costs, how the census has to add up) exist once rather than
    three times, while each tier's vocabulary stays its own contract's.

    Every refusal here is a malformed *question* rather than a fact about the market, which is
    `HaltCorpus.require_coverage`'s distinction: a duplicated subject, an observation stamped at
    another `as_of`, a coverage code the tier does not declare, a label for a security nobody
    scored, labels over more than one window, and a window that is not forward of the `as_of`. A
    thin or an all-tied cross section is a fact and is reported by `FactorICStudy.measure` as a
    code.
    """
    if tier not in FACTOR_TIERS:
        raise FactorICError(
            f"{tier!r} is not a declared tier; expected one of {sorted(FACTOR_TIERS)}"
        )
    instant = ensure_aware(as_of)
    vocabulary = set(TIER_COVERAGE_ORDER[tier])
    admitted_codes = TIER_ADMITTED_CODES[tier]
    subjects = [subject for subject, _value, _coverage in rows]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise FactorICError(
            f"{duplicates} appears more than once in the {tier} cross section at "
            f"{instant.isoformat()}; one security has one value at one as_of, and two would be "
            "two rows of one correlation with one label between them"
        )
    unknown = sorted(set(labels) - set(subjects))
    if unknown:
        raise FactorICError(
            f"{unknown} carries a label and no {tier} observation at {instant.isoformat()}; a "
            "label for a security the cross section never scored is a forward return with "
            "nothing to correlate it against, and silently ignoring it would hide a caller that "
            "read its two sides from two different universes"
        )
    window = _refuse_labels_over_more_than_one_window(labels)
    _refuse_a_window_that_is_not_forward_of(instant, window)

    excluded = {code: 0 for code in TIER_COVERAGE_ORDER[tier] if code not in admitted_codes}
    pairs: list[ICObservationPair] = []
    unlabelled = 0
    unmatched = 0
    for subject, value, coverage in rows:
        if coverage not in vocabulary:
            raise FactorICError(
                f"{subject} at {instant.isoformat()} carries coverage {coverage!r}, which the "
                f"{tier} tier does not declare; its vocabulary is {sorted(vocabulary)}"
            )
        if coverage not in admitted_codes:
            excluded[coverage] += 1
            continue
        if value is None:
            raise FactorICError(
                f"{subject} at {instant.isoformat()} carries the admitted coverage code "
                f"{coverage!r} with no value; the observation contracts make exactly the "
                "value-carrying codes carry a number, so this row skipped its own constructor"
            )
        label = labels.get(subject)
        if label is None:
            unmatched += 1
            continue
        if not label.is_labelled:
            unlabelled += 1
            continue
        pairs.append(
            ICObservationPair(subject=subject, score=value, forward_return=label.realized_return)
        )
    return ICCrossSection(
        as_of=instant,
        tier=tier,
        pairs=tuple(pairs),
        census=ICCensus(
            tier=tier,
            subject_count=len(rows),
            admitted_count=len(pairs),
            excluded_by_coverage=tuple(
                (code, excluded[code])
                for code in TIER_COVERAGE_ORDER[tier]
                if code not in admitted_codes
            ),
            unlabelled_count=unlabelled,
            unmatched_count=unmatched,
        ),
        horizon=window.horizon,
        prediction_day=window.prediction_day,
        entry_day=window.entry_day,
        exit_day=window.exit_day,
    )


def raw_cross_section(
    *,
    as_of: datetime,
    observations: Sequence[FactorObservation],
    labels: Mapping[str, OutcomeLabel],
) -> ICCrossSection:
    """The raw tier's cross section: `factor_obs_*` rows, admitting `computed` alone.

    Refuses an observation stamped at another `as_of` rather than filtering it out. The three
    tiers all normalise `as_of` through `ensure_aware` in their own `__post_init__` -- a stored
    instant read back out of DuckDB arrives tagged with the session's timezone rather than UTC --
    so this comparison is between two normalised instants and not between two labels for one.
    """
    return ic_cross_section(
        as_of=as_of,
        tier="raw",
        rows=_rows(
            as_of,
            tier="raw",
            items=[(item.subject, item.value, item.coverage, item.as_of) for item in observations],
        ),
        labels=labels,
    )


def processed_cross_section(
    *,
    as_of: datetime,
    observations: Sequence[ProcessedFactorObservation],
    labels: Mapping[str, OutcomeLabel],
) -> ICCrossSection:
    """The processed tier's cross section: `factor_proc_*` rows, admitting `processed` alone.

    `imputed` rows carry a number and are **not** admitted; they are counted under their own code
    in the census. See this module's docstring for why that is a rule rather than a knob.
    """
    return ic_cross_section(
        as_of=as_of,
        tier="processed",
        rows=_rows(
            as_of,
            tier="processed",
            items=[(item.subject, item.value, item.coverage, item.as_of) for item in observations],
        ),
        labels=labels,
    )


def neutralized_cross_section(
    *,
    as_of: datetime,
    observations: Sequence[NeutralizedFactorObservation],
    labels: Mapping[str, OutcomeLabel],
) -> ICCrossSection:
    """The neutralised tier's cross section: `factor_neut*` residuals, admitting `neutralized`.

    Read `KNOWN_IC_LIMITATIONS`' `neutralised_residuals_are_read_at_a_year_end_snapshot` before
    reading a series built from these: the residuals' content is clean and their timestamps are
    not, so a neutralised IC series is not a point-in-time series the way the other two are.
    """
    return ic_cross_section(
        as_of=as_of,
        tier="neutralized",
        rows=_rows(
            as_of,
            tier="neutralized",
            items=[(item.subject, item.value, item.coverage, item.as_of) for item in observations],
        ),
        labels=labels,
    )


def _rows(
    as_of: datetime,
    *,
    tier: FactorTier,
    items: Sequence[tuple[str, float | None, str, datetime]],
) -> tuple[tuple[str, float | None, str], ...]:
    """Project one tier's observations into `(subject, value, coverage)`, refusing a stray `as_of`.

    One function rather than three copies of a loop, and the check it carries is the reason it is
    a function at all: an observation stamped at a different instant is a row from another cross
    section, and correlating it against this one's labels would put a factor value from one day
    against a forward return from another. Refused rather than filtered, because a caller who
    passed the wrong partition wants to be told rather than to get a shorter answer.
    """
    instant = ensure_aware(as_of)
    stray = sorted(
        f"{subject}@{stamped.isoformat()}"
        for subject, _value, _coverage, stamped in items
        if ensure_aware(stamped) != instant
    )
    if stray:
        raise FactorICError(
            f"{stray} carries a {tier} observation stamped at another instant and this cross "
            f"section is {instant.isoformat()}; a factor value from one day against a forward "
            "return from another is a plausible number from the wrong rows"
        )
    return tuple((subject, value, coverage) for subject, value, coverage, _stamped in items)


class FactorICStudy:
    """Measure one cross section, summarise a series, lay a series out against a horizon axis.

    A class holding a `FactorICSpec` rather than three functions each taking one, so that the
    declared floors, the correlation and -- above all -- the factor whose `direction` decides
    every sign are fixed once for a study instead of being passed three times with three chances
    to differ. `EventStudy`'s precedent, with the request's fixed half hoisted into the object.
    """

    def __init__(self, spec: FactorICSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> FactorICSpec:
        return self._spec

    def measure(self, cross_section: ICCrossSection) -> ICPoint:
        """One `as_of`'s IC, or the code that says why there is none.

        Never raises for a property of the market: a cross section thinner than the declared
        floor and one with nothing to order are both answers, because a loop over a year of
        as_ofs has to keep going past them and because a report that showed only the days that
        worked would be a report of a different factor.
        """
        scores = cross_section.scores
        returns = cross_section.forward_returns
        coverage: ICCoverage = "measured"
        raw: float | None = None
        if len(scores) < self._spec.min_securities:
            coverage = "insufficient_sample"
        else:
            degenerate = _degenerate_side(scores, returns)
            if degenerate is not None:
                coverage = degenerate
            elif self._spec.method == "spearman":
                raw = _pearson(average_ranks(scores), average_ranks(returns))
            else:
                raw = _pearson(scores, returns)
        return ICPoint(
            as_of=cross_section.as_of,
            tier=cross_section.tier,
            method=self._spec.method,
            direction=self._spec.direction,
            factor_id=self._spec.factor_id,
            horizon_sessions=cross_section.horizon.sessions,
            coverage=coverage,
            sample_size=len(scores),
            raw_ic=raw,
            ic=None if raw is None else self._spec.orient(raw),
        )

    def summarize(self, points: Iterable[ICPoint]) -> ICSummary:
        """A series' stability: mean, dispersion, ICIR and sign consistency of the oriented ICs.

        Refuses a series that is not one study -- a point from another factor, tier, method,
        direction or horizon, or two points at one `as_of`. Each of those is a malformed
        question rather than a thin sample: a "mean IC" over two horizons is the average of two
        different quantities, and one `as_of` counted twice weights a day by how many times a
        caller appended it.
        """
        ordered = sorted(points, key=lambda point: point.as_of)
        if not ordered:
            raise FactorICError(
                "a stability summary needs at least one point; an empty series satisfies every "
                "per-point check vacuously and would report a coverage code about nothing"
            )
        self._refuse_points_that_are_not_one_study(ordered)
        as_ofs = tuple(point.as_of for point in ordered)
        measured = [point.ic for point in ordered if point.ic is not None]
        positive = sum(1 for value in measured if value > 0.0)
        negative = sum(1 for value in measured if value < 0.0)
        zero = len(measured) - positive - negative
        head = ordered[0]
        if len(measured) < self._spec.min_as_ofs:
            return ICSummary(
                tier=head.tier,
                method=head.method,
                direction=head.direction,
                factor_id=head.factor_id,
                horizon_sessions=head.horizon_sessions,
                coverage="insufficient_as_ofs",
                as_ofs=as_ofs,
                measured_count=len(measured),
                mean_ic=None,
                stdev_ic=None,
                icir=None,
                positive_count=positive,
                negative_count=negative,
                zero_count=zero,
                sign_consistency=None,
            )
        mean = statistics.fmean(measured)
        deviation = statistics.stdev(measured)
        return ICSummary(
            tier=head.tier,
            method=head.method,
            direction=head.direction,
            factor_id=head.factor_id,
            horizon_sessions=head.horizon_sessions,
            coverage="measured",
            as_ofs=as_ofs,
            measured_count=len(measured),
            mean_ic=mean,
            stdev_ic=deviation,
            icir=None if deviation == 0.0 else mean / deviation,
            positive_count=positive,
            negative_count=negative,
            zero_count=zero,
            sign_consistency=positive / len(measured),
        )

    def decay(self, series: Sequence[Sequence[ICPoint]]) -> ICDecayCurve:
        """One curve out of one series per horizon, ordered by horizon and over one sample.

        Each element of `series` is a whole `as_of` series at one horizon; `summarize` reduces
        each and `ICDecayCurve` refuses the ones that are not comparable. The horizons are read
        off the points rather than taken as an argument, so a rung cannot be filed under a
        horizon its own numbers did not come from.
        """
        if len(series) < 2:
            raise FactorICError(
                f"a decay curve needs at least two horizons and {len(series)} was offered; one "
                "point is not a curve, and 'decay' is a statement about a difference between "
                "horizons"
            )
        summaries = [self.summarize(points) for points in series]
        summaries.sort(key=lambda summary: summary.horizon_sessions)
        head = summaries[0]
        return ICDecayCurve(
            tier=head.tier,
            method=head.method,
            direction=head.direction,
            factor_id=head.factor_id,
            rungs=tuple(
                ICDecayRung(horizon_sessions=summary.horizon_sessions, summary=summary)
                for summary in summaries
            ),
        )

    def _refuse_points_that_are_not_one_study(self, ordered: Sequence[ICPoint]) -> None:
        """Refuse a series mixing factors, tiers, methods, directions, horizons or as_ofs."""
        head = ordered[0]
        for point in ordered:
            mismatched = [
                f"{name}={getattr(point, name)!r} against {getattr(head, name)!r}"
                for name in ("tier", "method", "direction", "factor_id", "horizon_sessions")
                if getattr(point, name) != getattr(head, name)
            ]
            if mismatched:
                raise FactorICError(
                    f"the point at {point.as_of.isoformat()} reports {mismatched}; a stability "
                    "summary is one factor on one tier under one correlation at one horizon, and "
                    "a mean over two of any of them is the average of two different quantities"
                )
            if point.factor_id != self._spec.factor_id:
                raise FactorICError(
                    f"the point at {point.as_of.isoformat()} was measured for factor "
                    f"{point.factor_id!r} and this study declares {self._spec.factor_id!r}"
                )
        stamps = [point.as_of for point in ordered]
        if len(set(stamps)) != len(stamps):
            duplicates = sorted({stamp.isoformat() for stamp in stamps if stamps.count(stamp) > 1})
            raise FactorICError(
                f"{duplicates} appears more than once in this series; one as_of is one cross "
                "section, and counting it twice weights a day by how often a caller appended it"
            )
