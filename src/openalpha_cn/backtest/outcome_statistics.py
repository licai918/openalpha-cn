"""Gross beside net, cost drag in its own column, an interval that says what it assumed.

`V2-P5-008` asks for four things over a set of validated outcomes: gross and net side by side,
the cost drag as its own column, confidence intervals, and sample counts. Three of those are
arithmetic over numbers `ValidationResult` already carries and are exact. The fourth is not
arithmetic at all -- **a confidence interval implies a sampling model** -- and this module is
mostly about what may honestly be said there.

## The four columns, and the fifth that `V2-P5-006` earned

Per cohort, each a mean over that cohort's results:

    gross_active_return    realized_return - benchmark_return          what the position made
    cost_drag              -transaction_cost                           its own column
    net_active_return      realized - benchmark - cost                 what was kept
    unexplained_return     ValidationResult.unexplained_return         what nothing attributed
    sample_size            how many results the mean is over

`cost_drag` is a column rather than a subtraction the reader performs, which is the roadmap's
own reason for the row: *只报 gross/net 会让成本来源不可归因*. Gross and net differ by exactly
one thing and a report that shows only the two makes the reader derive it -- and derive it
wrongly the day a second cost enters.

The fifth column is not in the row and is here because leaving it out would repeat, one level up,
exactly the defect `V2-P5-006` closed. That issue found `unexplained_return` computed and then
dropped on the way to the product surface. A cohort report that aggregates realized, benchmark
and cost and silently drops the residual would put the same number back in the same hole, so it
is aggregated and printed beside the others.

**The three return columns reconcile exactly on inputs that are exactly representable.**
`gross + cost_drag == net` holds to the last bit on the closed-form corpus in
`tests/unit/backtest/test_outcome_statistics.py`, because each is `math.fsum`-correctly-rounded
over dyadic rationals. It is three independent means and not one derived from the other two --
see `gross_net_and_cost_drag_are_three_means_and_the_identity_is_only_as_exact_as_the_inputs`.

## The interval, and why it is this interval

ADR-0003 fixes nine runtime dependencies and no numerical stack. A Student-t interval needs an
inverse-CDF quantile that is not in the standard library, so the honest choices were: a
normal-approximation interval computed from `math.erf` (which assumes a sampling distribution
nobody here has grounds to declare at n = 3), a distribution-free resampling interval, or a
refusal.

This module takes the second and **says what it costs**: a percentile bootstrap over
`bootstrap_samples` resamples from a stated `random_seed`, reported as
`ConfidenceInterval.method = "percentile-bootstrap"` with the resample count and the seed on the
face of it, so the interval is reproducible rather than merely plausible. The percentile
convention -- `int((alpha / 2) * (samples - 1))` and its mirror -- is `backtest/event_study.py`'s,
verbatim, and `test_the_interval_is_bit_identical_to_the_one_event_study_publishes` holds the two
together on one corpus, because two intervals in one product that are computed two ways are two
different intervals wearing one name.

What a bootstrap cannot do is invent observations. Its endpoints are order statistics of resample
means and every resample mean is a convex combination of the sample, so **the interval can never
reach outside `[min, max]` of the data**. At small `n` the resample means take few distinct
values, so a "95%" interval is a coarse grid rather than a smooth one; `distinct_bootstrap_means`
reports the size of that grid beside every interval, which is the number that makes the
limitation legible instead of merely stated. A cohort of three identical returns gets an interval
of zero width, and that is not a precise estimate.

**Below `MINIMUM_INTERVAL_SAMPLE_SIZE` there is no interval and no test, and the absence is
named.** At `n = 1` every resample is the sample, so `lower == upper` for *any* confidence level
whatever -- a zero-width 95% interval that is pure notation. Publishing it would be the
statistical form of the invented 20/30/50 split `V2-P5-005` deleted, so `interval` and `test` are
`None` and `absence_reason` says why in a sentence a report can print.

## The p-value, and the family it belongs to

`V2-P5-007` needs p-values and refuses to compute any; this is the module that supplies them, so
that BH has a caller rather than being a library nothing reaches.

The test is a **sign-flip randomization test** against the null that a cohort's net active
returns are symmetric about zero. It is distribution-free, it is standard-library, and it is
*exact* at small `n`: at or below `EXACT_SIGN_FLIP_LIMIT` observations every one of the `2**n`
sign patterns is enumerated and the p-value is a rational with denominator `2**n` -- which is why
the closed-form corpus can assert `0.25` and `0.75` with `==` rather than approximately. Above
that limit the patterns are sampled from the declared seed and the p-value takes Phipson and
Smyth's `(1 + hits) / (1 + draws)` form, which cannot report zero for a finite sample.
`RandomizationTest` carries `exact`, the pattern count and the seed, so which of the two ran is
on the face of the answer.

The family handed to `control_false_discovery_rate` is the cohorts that produced a p-value, and
`family_size` is the caller's declaration of how many cohorts were **tested** -- not how many are
reported here. A cohort too small to test is not a tested hypothesis: it is listed by name in
`untested_cohorts` and is outside the family.

A standard-library leaf over `domain/validation.py`, on both `backtest-studies-*` contracts'
source lists. It stores nothing and reads no partition.
"""

import math
import random
import statistics
from dataclasses import dataclass
from itertools import product
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.multiple_testing import (
    KNOWN_MULTIPLE_TESTING_LIMITATIONS,
    DependenceAssumption,
    HypothesisTest,
    HypothesisVerdict,
    MultipleTestingReport,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.domain.validation import ValidationResult

MINIMUM_INTERVAL_SAMPLE_SIZE: Final[int] = 2
"""Below this, no interval and no p-value are published and the absence is named.

At `n = 1` every bootstrap resample is the single observation, so the percentile interval has
`lower == upper` at every confidence level, and every sign-flip pattern gives `abs(sum)` equal to
the observed one, so the p-value is `1.0` whatever the return was. Both are notation rather than
measurement.

**Two is where either can distinguish anything at all, and what that is worth is stated rather
than implied.** An exact sign-flip test over `n` observations has `2**n` patterns and the
observed one and its negation are always hits, so the smallest attainable p-value is `2**(1-n)`:
at `n = 2` that is `0.5`, and **a two-observation cohort can therefore never be a discovery at
any conventional rate**, whatever it earned. It is admitted because its columns, its interval
and its p-value are all honest, not because any of them is useful, and `sample_size` sits on
every row so a reader can tell the two apart.
`test_a_cohort_of_two_can_never_be_a_discovery_at_a_conventional_rate` drives it.
"""

EXACT_SIGN_FLIP_LIMIT: Final[int] = 12
"""At or below this many observations the randomization test enumerates all `2**n` patterns.

`2**12` is 4,096 sums per cohort, which is cheap, and exactness is worth far more than the
saving: an enumerated p-value is a rational with denominator `2**n`, so a control corpus can
assert it with `==` and a sampled one could only be asserted approximately. Above the limit the
patterns are drawn from `random_seed` and `RandomizationTest.exact` reads `False`.
"""

SIGN_FLIP_NULL: Final[str] = "the cohort's net active returns are symmetric about zero"
"""The null hypothesis every p-value in this module is a p-value *for*.

Written down and carried on each `RandomizationTest` because a p-value detached from its null is
the same defect as a q-value detached from its family size, which is the whole of `V2-P5-007`.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class OutcomeStatisticsLimitation:
    """One named thing this module's cohort report does not claim to have measured."""

    code: str
    detail: str


KNOWN_OUTCOME_STATISTICS_LIMITATIONS: Final[tuple[OutcomeStatisticsLimitation, ...]] = (
    OutcomeStatisticsLimitation(
        code="the_percentile_bootstrap_interval_cannot_reach_outside_the_observed_sample",
        detail=(
            "Every resample mean is a convex combination of the observations, so both "
            "endpoints lie inside [min, max] of the cohort. At small n the resample means take "
            "few distinct values and a '95%' interval is a coarse grid: three identical "
            "returns give distinct_bootstrap_means = 1 and an interval of zero width, which is "
            "not a precise estimate but an absent one wearing an interval's clothes. "
            "distinct_bootstrap_means is reported beside every interval so the grid's size is "
            "visible rather than inferred. The percentile bootstrap's coverage is asymptotic; "
            "at n = 3 it is not 95% and nothing here claims it is."
        ),
    ),
    OutcomeStatisticsLimitation(
        code="no_interval_is_published_below_two_observations_because_every_resample_is_the_sample",
        detail=(
            "At n = 1 the bootstrap gives lower == upper for any confidence level and the "
            "sign-flip test gives p = 1.0 for any return, so both are notation. The cohort is "
            "reported with its sample_size and its return columns, interval and test are None, "
            "absence_reason says why, and the cohort is left out of the multiple-testing "
            "family -- an untested hypothesis is not a hypothesis whose q-value was large. "
            "What the floor buys immediately above itself is small and is stated rather than "
            "implied: an exact sign-flip test over n observations cannot return a p-value below "
            "2**(1 - n), because the observed pattern and its negation are always hits, so a "
            "cohort of two is capped at 0.5 and can never be a discovery at any rate anyone "
            "would set. Two identical returns and two spectacular ones get the same p-value."
        ),
    ),
    OutcomeStatisticsLimitation(
        code="the_sign_flip_test_assumes_symmetry_about_zero_and_nothing_here_checks_it",
        detail=(
            "The randomization test is exact under the null that the net active returns are "
            "exchangeable in sign, which is symmetry about zero. Realised returns are "
            "routinely skewed -- a portfolio of capped losses and uncapped gains is not "
            "symmetric -- and under skew the test's level is not the level it reports. "
            "SIGN_FLIP_NULL is carried on every RandomizationTest so the assumption travels "
            "with the number, and nothing here tests the cohort for symmetry."
        ),
    ),
    OutcomeStatisticsLimitation(
        code="the_observations_are_resampled_as_though_they_were_independent_draws",
        detail=(
            "Both the bootstrap and the sign-flip test treat a cohort's results as independent. "
            "Two ValidationResults over overlapping observation windows on correlated names are "
            "not, and this module can see the windows but does not model the correlation: a "
            "cohort of twenty decisions all taken in one week is nearer one observation than "
            "twenty, and its interval will be far too narrow. This is the same objection "
            "backtest/factor_redundancy.py raises when it declines to publish a p-value against "
            "n = 5,534 cross-sectional pairs, arriving in the time dimension instead."
        ),
    ),
    OutcomeStatisticsLimitation(
        code="a_cohort_is_whatever_the_caller_grouped_and_the_grouping_is_itself_untested",
        detail=(
            "Cohorts arrive already formed. Regrouping the same ValidationResults by a "
            "different key is a different family with different p-values, and family_size "
            "records the grouping that was tested and not the groupings that were tried and "
            "discarded. A caller who cuts the same results twenty ways and reports the cut "
            "that rejected is doing exactly what BH exists to prevent, and declaring "
            "family_size = the number of cohorts in the winning cut conceals it."
        ),
    ),
    OutcomeStatisticsLimitation(
        code="gross_net_and_cost_drag_are_three_means_and_the_identity_is_only_as_exact_as_the_inputs",
        detail=(
            "Each column is its own math.fsum over its own values, correctly rounded once. On "
            "dyadic inputs gross + cost_drag == net holds to the last bit and the closed-form "
            "corpus asserts it with ==; on arbitrary floats the three roundings need not "
            "cancel and the identity holds only to rounding. They are computed independently "
            "rather than deriving one from the other two, because a derived column cannot "
            "disagree with its parents and therefore cannot detect anything -- the free "
            "variable V2-P5-005 removed from the attribution, kept out of the aggregate."
        ),
    ),
    OutcomeStatisticsLimitation(
        code="the_attribution_terms_are_not_aggregated_because_two_results_need_not_carry_the_same_ones",
        detail=(
            "OutcomeValidator emits one term for a held decision and two for a flat one, so a "
            "cohort mixing the two arms has no common term set to average over, and a mean "
            "taken per term name would divide by different denominators in one table. The "
            "residual is aggregated because every result carries exactly one, whatever its "
            "arm; the terms are left on the individual results, where V2-P5-005's four "
            "attribution limitations already say what they may claim."
        ),
    ),
)
"""What the report below does not claim, stated where the report is computed.

Four are about the inference (the interval's reach, its floor, the test's assumption, and the
independence neither of them has), one is about the grouping the caller chose, and two are about
the arithmetic -- which columns are exact and which aggregate is deliberately not taken.
"""


class OutcomeStatisticsError(ValueError):
    """A family that cannot be controlled, or a declared size smaller than the family."""


class OutcomeCohort(BaseModel):
    """One named group of validated outcomes, treated as one hypothesis."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    cohort_id: str = Field(min_length=1, max_length=128)
    results: tuple[ValidationResult, ...] = Field(min_length=1)


class OutcomeStatisticsRequest(BaseModel):
    """A family of cohorts, its declared size, and every policy that decides how permissive
    the answer is."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cohorts: tuple[OutcomeCohort, ...] = Field(min_length=1)
    family_size: int = Field(ge=1)
    """How many cohorts the study that produced these actually tested (`V2-P5-007`)."""
    false_discovery_rate: float = Field(gt=0, lt=1)
    dependence: DependenceAssumption
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1)
    bootstrap_samples: int = Field(default=1000, ge=100, le=100_000)
    """`EventStudyRequest`'s bounds, because it is the same interval by the same convention."""
    random_seed: int = 0

    @model_validator(mode="after")
    def validate_the_cohorts_are_distinct(self) -> Self:
        identifiers = {cohort.cohort_id for cohort in self.cohorts}
        if len(identifiers) != len(self.cohorts):
            raise ValueError("every cohort_id in one family must be distinct")
        return self


class ConfidenceInterval(BaseModel):
    """A percentile-bootstrap interval, with everything needed to reproduce it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["percentile-bootstrap"] = "percentile-bootstrap"
    confidence_level: float = Field(gt=0.5, lt=1)
    lower: float
    upper: float
    bootstrap_samples: int = Field(ge=100)
    distinct_bootstrap_means: int = Field(ge=1)
    """How many distinct values the resample means took -- the interval's actual resolution."""
    random_seed: int

    @model_validator(mode="after")
    def validate_the_endpoints_are_ordered(self) -> Self:
        if self.upper < self.lower:
            raise ValueError("a confidence interval's upper endpoint cannot be below its lower")
        return self


class RandomizationTest(BaseModel):
    """A sign-flip p-value, its null, and whether it was enumerated or sampled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["sign-flip-randomization"] = "sign-flip-randomization"
    null_hypothesis: str = Field(min_length=1)
    p_value: float = Field(gt=0, le=1)
    exact: bool
    sign_patterns: int = Field(ge=2)
    """`2**n` when exact, otherwise how many patterns were drawn."""
    random_seed: int | None
    """`None` when exact, because an enumeration consumes no randomness."""


class CohortStatistics(BaseModel):
    """One cohort's five columns, its sample count, and its inference or the absence of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort_id: str = Field(min_length=1, max_length=128)
    sample_size: int = Field(ge=1)
    gross_active_return: float
    cost_drag: float
    """Mean `-transaction_cost`: negative or zero, never positive, and its own column."""
    net_active_return: float
    unexplained_return: float
    interval: ConfidenceInterval | None
    test: RandomizationTest | None
    absence_reason: str | None

    @model_validator(mode="after")
    def validate_the_inference_is_present_or_named_absent(self) -> Self:
        """Either both halves of the inference are here, or the absence carries a reason.

        `report_outcome_statistics` never violates this, so nothing that drives the function
        reaches these branches -- a mutation sweep measured that deleting the last one changed
        no test. They are reachable from a *document*, which is the path a stored report is read
        back through, and a row silently carrying three nulls reads as "we looked and found
        nothing" when what it means is "we did not look".
        """
        has_inference = self.interval is not None and self.test is not None
        if has_inference and self.absence_reason is not None:
            raise ValueError("a cohort with an interval and a test states no absence")
        if not has_inference:
            if self.interval is not None or self.test is not None:
                raise ValueError("an interval and a randomization test stand or fall together")
            if self.absence_reason is None:
                raise ValueError("a cohort with no inference must say why it has none")
        return self

    @property
    def was_tested(self) -> bool:
        """Whether this cohort entered the multiple-testing family."""
        return self.test is not None


class OutcomeStatisticsReport(BaseModel):
    """Every cohort's columns, the cohorts too small to test, and the controlled family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cohorts: tuple[CohortStatistics, ...] = Field(min_length=1)
    tested_hypotheses: int = Field(ge=1)
    untested_cohorts: tuple[str, ...] = ()
    multiple_testing: MultipleTestingReport
    confidence_level: float = Field(gt=0.5, lt=1)
    bootstrap_samples: int = Field(ge=100)
    random_seed: int
    minimum_sample_size: int = Field(ge=1)
    """The floor below which no inference is published, stored so a report says its own rule."""

    @model_validator(mode="after")
    def validate_the_family_is_the_tested_cohorts(self) -> Self:
        """The join between the return columns and the control, as a rule rather than a habit.

        **The count check and the identifier check are not one check.** Cohort identifiers are
        unique on a `OutcomeStatisticsRequest` and nothing makes them unique on a report parsed
        back off disk, so two rows called `alpha` have an identifier *set* of size one: the
        identifier comparison passes and only the count says that two tested cohorts are being
        answered by one q-value. A mutation sweep found the count check unreachable from every
        test that drove `report_outcome_statistics`, which is how that case was written down.
        """
        tested = tuple(cohort.cohort_id for cohort in self.cohorts if cohort.was_tested)
        untested = tuple(cohort.cohort_id for cohort in self.cohorts if not cohort.was_tested)
        if self.tested_hypotheses != len(tested):
            raise ValueError("tested_hypotheses must be the number of cohorts carrying a test")
        if self.untested_cohorts != untested:
            raise ValueError("untested_cohorts must name exactly the cohorts carrying no test")
        if self.multiple_testing.reported_hypotheses != len(tested):
            raise ValueError("the controlled family must hold exactly the tested cohorts")
        if {verdict.hypothesis_id for verdict in self.multiple_testing.verdicts} != set(tested):
            raise ValueError("the controlled family must name exactly the tested cohorts")
        return self

    def verdict_for(self, cohort_id: str) -> HypothesisVerdict | None:
        """The multiple-testing verdict for one cohort, or `None` when it was not tested.

        `None` for a cohort this report never tested **and** for one it never carried, which
        are the same answer to a caller and different facts: `untested_cohorts` is what tells
        the two apart, and it is on the report rather than left to be inferred from this.
        """
        return self.multiple_testing.verdict_for(cohort_id)


def percentile_bootstrap_interval(
    values: tuple[float, ...],
    *,
    confidence_level: float,
    bootstrap_samples: int,
    random_seed: int,
) -> ConfidenceInterval:
    """`backtest/event_study.py`'s interval, over an arbitrary sample rather than over CARs.

    Bit-for-bit the same construction: one `random.Random(seed)`, `bootstrap_samples` resample
    means each taken with `statistics.fmean` over `len(values)` draws with replacement, sorted,
    and indexed at `int((alpha / 2) * (samples - 1))` and its mirror.
    `test_the_interval_is_bit_identical_to_the_one_event_study_publishes` drives that identity
    on one corpus, so the two faces of this repository cannot drift into two conventions.
    """
    generator = random.Random(random_seed)
    means = sorted(
        statistics.fmean(generator.choice(values) for _item in values)
        for _sample in range(bootstrap_samples)
    )
    alpha = 1 - confidence_level
    lower_index = int((alpha / 2) * (len(means) - 1))
    upper_index = int((1 - alpha / 2) * (len(means) - 1))
    return ConfidenceInterval(
        confidence_level=confidence_level,
        lower=means[lower_index],
        upper=means[upper_index],
        bootstrap_samples=bootstrap_samples,
        distinct_bootstrap_means=len(set(means)),
        random_seed=random_seed,
    )


def sign_flip_test(
    values: tuple[float, ...],
    *,
    bootstrap_samples: int,
    random_seed: int,
) -> RandomizationTest:
    """A two-sided randomization p-value for `mean(values) == 0` under sign symmetry.

    The statistic is `abs(sum(values))`, which orders identically to `abs(mean(values))` for a
    fixed sample size and avoids one division per pattern.

    At or below `EXACT_SIGN_FLIP_LIMIT` observations every sign pattern is enumerated, the
    p-value is `hits / 2**n` exactly, and it can never be zero because the observed pattern and
    its negation are both hits. Above the limit `bootstrap_samples` patterns are drawn from
    `random_seed` and the estimate is `(1 + hits) / (1 + draws)`, Phipson and Smyth's form,
    which also cannot be zero -- a sampled p-value of `0.0` would claim a certainty a finite
    number of draws cannot support.
    """
    observed = abs(math.fsum(values))
    count = len(values)
    if count <= EXACT_SIGN_FLIP_LIMIT:
        patterns = 2**count
        hits = sum(
            1
            for signs in product((1.0, -1.0), repeat=count)
            if abs(math.fsum(sign * value for sign, value in zip(signs, values, strict=True)))
            >= observed
        )
        return RandomizationTest(
            null_hypothesis=SIGN_FLIP_NULL,
            p_value=hits / patterns,
            exact=True,
            sign_patterns=patterns,
            random_seed=None,
        )
    generator = random.Random(random_seed)
    hits = 0
    for _draw in range(bootstrap_samples):
        flipped = math.fsum(generator.choice((1.0, -1.0)) * value for value in values)
        if abs(flipped) >= observed:
            hits += 1
    return RandomizationTest(
        null_hypothesis=SIGN_FLIP_NULL,
        p_value=(1 + hits) / (1 + bootstrap_samples),
        exact=False,
        sign_patterns=bootstrap_samples,
        random_seed=random_seed,
    )


def _cohort_statistics(
    cohort: OutcomeCohort,
    *,
    confidence_level: float,
    bootstrap_samples: int,
    random_seed: int,
) -> CohortStatistics:
    """One cohort's five columns, and its inference when the sample supports one."""
    results = cohort.results
    gross = tuple(item.realized_return - item.benchmark_return for item in results)
    drag = tuple(-item.transaction_cost for item in results)
    net = tuple(item.net_active_return for item in results)
    residual = tuple(item.unexplained_return for item in results)

    if len(results) < MINIMUM_INTERVAL_SAMPLE_SIZE:
        absence = (
            f"{len(results)} observation(s) is below MINIMUM_INTERVAL_SAMPLE_SIZE "
            f"({MINIMUM_INTERVAL_SAMPLE_SIZE}): every bootstrap resample would be the sample "
            "itself, so the interval would have zero width at any confidence level, and every "
            "sign pattern would give the observed statistic, so the p-value would be 1.0 "
            "whatever the return was"
        )
        interval = None
        test = None
    else:
        absence = None
        interval = percentile_bootstrap_interval(
            net,
            confidence_level=confidence_level,
            bootstrap_samples=bootstrap_samples,
            random_seed=random_seed,
        )
        test = sign_flip_test(net, bootstrap_samples=bootstrap_samples, random_seed=random_seed)

    return CohortStatistics(
        cohort_id=cohort.cohort_id,
        sample_size=len(results),
        gross_active_return=statistics.fmean(gross),
        cost_drag=statistics.fmean(drag),
        net_active_return=statistics.fmean(net),
        unexplained_return=statistics.fmean(residual),
        interval=interval,
        test=test,
        absence_reason=absence,
    )


def report_outcome_statistics(request: OutcomeStatisticsRequest) -> OutcomeStatisticsReport:
    """Measure every cohort, then control the family of the ones that could be tested.

    The two halves are separate on purpose. A cohort below `MINIMUM_INTERVAL_SAMPLE_SIZE` still
    gets its five return columns and its sample count -- those are exact at `n = 1` -- and gets
    no interval, no p-value, and a named absence. It is then **outside** the family, because a
    hypothesis that was never tested is not a hypothesis that failed to reject: it would be
    handed a q-value computed from evidence nobody has, and `withheld_hypotheses` would count a
    row carrying no test as one of the rows the search reported.

    **What it would *not* do is make the control stricter, and that was measured rather than
    assumed.** An earlier version of this sentence said an untested row would raise every other
    cohort's q-value; it is false in both directions. A stand-in `p = 1.0` can never clear its
    own line -- every critical value is `rank * rate / (family_size * penalty)` with
    `rank <= family_size` and `rate < 1`, so it is strictly below one -- and it can never lower
    an unclamped q-value above it either, because that would need `family_size < reported + 1`
    while adding the row requires `family_size >= reported + 1`.
    `test_a_stand_in_p_value_of_one_can_never_clear_its_own_line` drives the first half. So the
    objection to including them is not arithmetic; it is that the report would say something
    about a cohort it measured nothing on.

    `family_size` is the caller's and is checked in the one direction that can be: it may not be
    below the number of cohorts actually tested. A family with nothing testable in it is refused
    outright rather than answered with an empty control.
    """
    measured = tuple(
        _cohort_statistics(
            cohort,
            confidence_level=request.confidence_level,
            bootstrap_samples=request.bootstrap_samples,
            random_seed=request.random_seed,
        )
        for cohort in request.cohorts
    )
    tested = tuple(cohort for cohort in measured if cohort.was_tested)
    if not tested:
        raise OutcomeStatisticsError(
            f"no cohort reached MINIMUM_INTERVAL_SAMPLE_SIZE ({MINIMUM_INTERVAL_SAMPLE_SIZE}) "
            f"observations, so there is no hypothesis to control across; "
            f"{len(measured)} cohort(s) were measured"
        )
    if request.family_size < len(tested):
        raise OutcomeStatisticsError(
            f"family_size {request.family_size} is smaller than the {len(tested)} cohort(s) "
            "actually tested here; a family cannot be smaller than the hypotheses reported "
            "out of it"
        )

    controlled = control_false_discovery_rate(
        MultipleTestingRequest(
            tests=tuple(
                HypothesisTest(
                    hypothesis_id=cohort.cohort_id,
                    p_value=cohort.test.p_value,  # type: ignore[union-attr]
                    test=(
                        f"{cohort.test.method} against {cohort.test.null_hypothesis}"  # type: ignore[union-attr]
                    ),
                )
                for cohort in tested
            ),
            family_size=request.family_size,
            false_discovery_rate=request.false_discovery_rate,
            dependence=request.dependence,
        )
    )
    return OutcomeStatisticsReport(
        cohorts=measured,
        tested_hypotheses=len(tested),
        untested_cohorts=tuple(cohort.cohort_id for cohort in measured if not cohort.was_tested),
        multiple_testing=controlled,
        confidence_level=request.confidence_level,
        bootstrap_samples=request.bootstrap_samples,
        random_seed=request.random_seed,
        minimum_sample_size=MINIMUM_INTERVAL_SAMPLE_SIZE,
    )


def outcome_statistics_view(report: OutcomeStatisticsReport) -> dict[str, Any]:
    """One report as data, for whichever face is handing it out.

    `construction_view`'s argument for existing, unchanged: the CLI's `--json` and
    `OpenAlphaSDK.outcome_statistics_view` emit these bytes and not two shapes that agree today.

    Three things are on the face of it that a bare table of returns would have left implicit, and
    each is a number this repository has been bitten by the absence of: `family_size` beside
    `reported_hypotheses`, so a q-value is reproducible (`V2-P5-007`); `dependence` and
    `dependence_penalty`, so the correction says what it assumed; and `minimum_sample_size`
    beside every named absence, so a missing interval reads as a rule rather than as a gap.

    The verdict columns are joined onto their cohort here rather than stored on it, so the
    report holds one copy of each q-value and the face that prints a table still gets a row.
    A cohort the family never tested carries `null` in all three, which is a different answer
    from a large q-value and is rendered as one.
    """
    verdicts = {verdict.hypothesis_id: verdict for verdict in report.multiple_testing.verdicts}
    return {
        "cohorts": [
            {
                "cohort_id": cohort.cohort_id,
                "sample_size": cohort.sample_size,
                "gross_active_return": cohort.gross_active_return,
                "cost_drag": cohort.cost_drag,
                "net_active_return": cohort.net_active_return,
                "unexplained_return": cohort.unexplained_return,
                "interval": None if cohort.interval is None else cohort.interval.model_dump(),
                "test": None if cohort.test is None else cohort.test.model_dump(),
                "absence_reason": cohort.absence_reason,
                "q_value": (
                    None if cohort.cohort_id not in verdicts else verdicts[cohort.cohort_id].q_value
                ),
                "critical_value": (
                    None
                    if cohort.cohort_id not in verdicts
                    else verdicts[cohort.cohort_id].critical_value
                ),
                "rejected": (
                    None
                    if cohort.cohort_id not in verdicts
                    else verdicts[cohort.cohort_id].rejected
                ),
            }
            for cohort in report.cohorts
        ],
        "family": {
            "family_size": report.multiple_testing.family_size,
            "reported_hypotheses": report.multiple_testing.reported_hypotheses,
            "withheld_hypotheses": report.multiple_testing.withheld_hypotheses,
            "family_is_complete": report.multiple_testing.family_is_complete,
            "false_discovery_rate": report.multiple_testing.false_discovery_rate,
            "dependence": report.multiple_testing.dependence,
            "dependence_penalty": report.multiple_testing.dependence_penalty,
            "discoveries": report.multiple_testing.discoveries,
            "largest_rejected_rank": report.multiple_testing.largest_rejected_rank,
        },
        "tested_hypotheses": report.tested_hypotheses,
        "untested_cohorts": list(report.untested_cohorts),
        "confidence_level": report.confidence_level,
        "bootstrap_samples": report.bootstrap_samples,
        "random_seed": report.random_seed,
        "minimum_sample_size": report.minimum_sample_size,
        "limitations": [
            {"code": limitation.code, "detail": limitation.detail}
            for limitation in KNOWN_OUTCOME_STATISTICS_LIMITATIONS
        ]
        + [
            {"code": limitation.code, "detail": limitation.detail}
            for limitation in KNOWN_MULTIPLE_TESTING_LIMITATIONS
        ],
    }
