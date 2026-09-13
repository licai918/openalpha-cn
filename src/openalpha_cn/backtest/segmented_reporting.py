"""Cutting one cohort six ways is six hypotheses, and a bucket too small to test says so.

`V2-P5-009` asks for three things: segmented reporting across industry, market
capitalisation, liquidity and market regime; a walk-forward that spans more than one regime;
and benchmark comparisons against an equal-weight baseline, a naive factor and the v1 baseline,
**三者并列** -- all three reported side by side, not whichever one flatters. All three are
built on `backtest/outcome_statistics.py`, and the whole of this module is about the two
things that go wrong when its cohort report is cut into segments.

## The segment label is a declared input, because a `ValidationResult` names no security

`ValidationResult` carries `signal_id`, `decision_id`, an observation window, three returns,
an attribution and a confidence. **It does not carry a ticker.** So this module cannot look up
an industry, a market capitalisation or a twenty-day turnover for a result even in principle --
not because the data is missing from the repository (`domain/daily_prices.py` has `total_mv`,
`circ_mv` and `turnover_rate`) but because the record being aggregated does not say which
security to look them up for.

That makes all four axes the same kind of thing, which is worth saying plainly because it is
easy to assume only the last one is: **industry, market capitalisation, liquidity and market
regime are every one of them declared by the caller**, and `SegmentLabelling` requires a
`definition` and a `source` beside the labels so a stored report says whose classification it
was and how the boundaries were drawn. A market-capitalisation cut at 10 billion yuan and one
at the 70th percentile of the day's cross-section are different studies, and a report that
prints `large` without saying which one has published a number nobody can reproduce.

Nothing here invents a boundary. There is no default regime classifier, no default size
break and no default liquidity screen, because every one of them would be a threshold this
module made up and then reported as though it had been measured.

## Cutting the same results four ways is not four hypotheses, it is however many buckets result

This is the defect the module exists to prevent, and it is `V2-P5-007`'s `family_size` arriving
in the segment dimension. A cohort that was one hypothesis becomes six when it is cut by
industry; cut it again by size, again by liquidity and again by regime and one study is
testing twenty-two. Reporting each axis as its own family -- four separate
`report_outcome_statistics` calls -- gives four chances to find a rejection at the price of
one, and every q-value in all four is computed against a family that excludes the other three.

So **every bucket of every axis, and every benchmark, enters one family**. There is exactly one
`OutcomeStatisticsRequest` built here and exactly one `MultipleTestingReport` on the answer.
`AxisReport` is a view onto that single family and never a family of its own;
`test_three_axes_are_one_family_and_not_three` drives it by counting `family_size` on the answer
against the buckets the axes produced.

`declared_family_size` is still the caller's, checked in the one direction that can be: it may
not be below the number of hypotheses this report actually tests. A caller who tried nine cuts
and reports the four that looked best is doing what BH exists to prevent, and only they know
the nine -- `a_segment_axis_is_whatever_the_caller_declared_and_the_cuts_not_shown_are_invisible`
says so where the number is computed. That direction is why the number the report *uses* is
the declared one and never `len(cohorts)`: the two agree on every request that declares
exactly its buckets, so
`test_a_family_declared_above_the_bucket_count_is_the_number_the_report_uses` declares more
than it publishes and separates them at both places the number is read.

## A bucket that could never have rejected is a different fact from one that did not

Segmenting pushes on both sides of the same inequality at once. Every cut makes the buckets
smaller, which raises the smallest p-value a bucket can attain, and makes the family larger,
which lowers the line that p-value has to clear. Both moves are in the direction of "no
discovery", and a table of twenty-two q-values none of which rejected hides the reason.

Both quantities are exact and neither is estimated:

- **The floor.** An exact sign-flip test over `n` observations has `2**n` patterns, and the
  observed pattern and its negation are always hits, so its smallest attainable p-value is
  `2**(1 - n)` -- attained exactly when no other pattern reaches the observed statistic.
  Measured, not asserted: `test_the_attainable_floor_is_attained_and_never_undercut` runs the
  shipped `sign_flip_test` over twelve thousand random samples and no p-value falls below it.
  Above `EXACT_SIGN_FLIP_LIMIT` the test is sampled and the floor is Phipson and Smyth's
  `1 / (1 + draws)` instead, which is the larger of the two at every admissible draw count.
- **The line.** The most permissive critical value any hypothesis in the family can face is the
  one at the largest rank, `reported * rate / (family_size * penalty)`, because BH's line rises
  with rank. A bucket whose floor is above *that* could not have rejected at any data
  whatsoever, not merely at the data it had.

`SegmentCapability.can_ever_reject` is that comparison, and it is deliberately conservative in
the direction that makes a `False` mean something: it grants the bucket the best rank in the
family and the most extreme sample its size permits, and still finds the line out of reach. A
report where nineteen of twenty-two buckets carry `can_ever_reject = False` has not measured
nineteen absences of skill; it has measured its own resolution, and says which it did.

## One regime is not multiple regimes, and a walk-forward over one says so

`V2-P5-009` asks for a *multi-regime* walk-forward. Whether a run is multi-regime is a fact
about the evidence and not about the intention, so it is measured: `RegimeCoverage` counts the
distinct regime labels the results actually span and how many of those buckets were large
enough to test. A study whose results all fall in one regime cannot support any claim about
regime robustness however many folds it ran, and `regime_coverage.spans_multiple_regimes`
reads `False` rather than the report staying silent and letting the fold count imply otherwise.

The regime axis is identified by `axis_id == MARKET_REGIME_AXIS`, which is a name the caller
opts into. There is no inference from the label values: an axis called `market_regime` gets the
coverage row and an axis called anything else does not.

## A benchmark is compared by pairing, or the comparison is named absent

`V2-P5-008` gave `cost_drag` its own column rather than making the reader subtract gross from
net, on the grounds that a difference the reader computes is a difference computed wrongly the
day a second term enters. The same argument applies to a benchmark printed beside a strategy:
two tables side by side invite a subtraction of two means that is not the mean of the
differences unless the samples are paired.

So the difference is computed here when -- and only when -- it can be. A benchmark whose
results pair one-to-one with the strategy's on `(observation_start, observation_end)` yields a
paired difference cohort, which is a one-sample problem the existing sign-flip test handles
exactly, and that cohort enters the family like any other. A benchmark that does not pair is
reported beside with its own columns and `comparison_absence_reason` saying why no difference
was published. The pairing is *verified* against the windows rather than assumed from the
argument order, because two equally long lists are not a pairing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.multiple_testing import DependenceAssumption
from openalpha_cn.backtest.outcome_statistics import (
    EXACT_SIGN_FLIP_LIMIT,
    MINIMUM_INTERVAL_SAMPLE_SIZE,
    CohortStatistics,
    OutcomeCohort,
    OutcomeStatisticsReport,
    OutcomeStatisticsRequest,
    report_outcome_statistics,
)
from openalpha_cn.domain.validation import ValidationResult

INDUSTRY_AXIS: Final[str] = "industry"
MARKET_CAPITALISATION_AXIS: Final[str] = "market_capitalisation"
LIQUIDITY_AXIS: Final[str] = "liquidity"
MARKET_REGIME_AXIS: Final[str] = "market_regime"

SEGMENT_AXES: Final[tuple[str, ...]] = (
    INDUSTRY_AXIS,
    MARKET_CAPITALISATION_AXIS,
    LIQUIDITY_AXIS,
    MARKET_REGIME_AXIS,
)
"""The four cuts `V2-P5-009` names, as constants rather than as a closed enumeration.

`SegmentLabelling.axis_id` is a free string deliberately. A study that cuts by listing venue or
by holding period is doing the same thing to the same family arithmetic, and a closed set would
make it either misdeclare its axis as one of these four or go unreported. What the four buy is
that `MARKET_REGIME_AXIS` is spelled once and `RegimeCoverage` keys off that spelling.
"""

AXIS_SEPARATOR: Final[str] = ":"
"""Joins an axis to a label to make the cohort identifier the single family is keyed by.

Refused inside `axis_id` and inside every label, because `industry:a` and `b` would otherwise
be able to collide with `industry` and `a:b` and two buckets would share one q-value.
`test_a_separator_inside_a_label_is_refused_rather_than_allowed_to_collide` drives it.
"""


class SegmentedReportingError(ValueError):
    """A segmentation that cannot be reported, or a family smaller than the buckets in it."""


class SegmentedReportingLimitation(BaseModel):
    """One named thing this module's segmented report does not claim to have measured."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    detail: str


KNOWN_SEGMENTED_REPORTING_LIMITATIONS: Final[tuple[SegmentedReportingLimitation, ...]] = (
    SegmentedReportingLimitation(
        code="every_segment_label_is_declared_by_the_caller_and_nothing_here_can_check_one",
        detail=(
            "A ValidationResult carries signal_id, decision_id, a window, three returns, an "
            "attribution and a confidence, and no ticker. This module therefore cannot derive "
            "an industry, a market capitalisation, a liquidity bucket or a regime for a result "
            "even though domain/daily_prices.py holds total_mv, circ_mv and turnover_rate -- "
            "there is no key to join on. All four axes are declared inputs, SegmentLabelling "
            "requires a definition and a source beside the labels, and a mislabelled result is "
            "reported in the wrong bucket with no sign that anything is wrong."
        ),
    ),
    SegmentedReportingLimitation(
        code="a_segment_axis_is_whatever_the_caller_declared_and_the_cuts_not_shown_are_invisible",
        detail=(
            "declared_family_size may not be below the hypotheses this report tests, and that "
            "is the only direction checkable here. A caller who cut the same results nine ways "
            "and passed the four that looked best declares a family of twenty-two while the "
            "search that produced it ran over far more, and every q-value below is too small "
            "by a factor only that caller knows. This is outcome_statistics' own "
            "a_cohort_is_whatever_the_caller_grouped limitation, arriving one level up where "
            "the multiplication is larger."
        ),
    ),
    SegmentedReportingLimitation(
        code="the_buckets_of_different_axes_hold_the_same_results_and_are_not_independent_tests",
        detail=(
            "Four axes over one result set put every result in four buckets, so the twenty-two "
            "hypotheses are four overlapping partitions of the same evidence rather than "
            "twenty-two independent tests. Benjamini-Hochberg controls the false discovery "
            "rate under independence or positive regression dependence and re-cuts of one "
            "sample are plausibly in the latter, but nothing here establishes that; a caller "
            "who wants a guarantee that survives arbitrary dependence declares "
            "dependence='arbitrary' and pays the Benjamini-Yekutieli harmonic penalty. The "
            "module reports the assumption and does not choose it."
        ),
    ),
    SegmentedReportingLimitation(
        code="can_ever_reject_is_about_this_familys_resolution_and_not_about_the_strategy",
        detail=(
            "can_ever_reject compares a bucket's attainable p-value floor with the most "
            "permissive critical value in the family. False means the bucket could not have "
            "rejected on any data, which is a statement about sample size and family size and "
            "says nothing whatever about whether the segment earned a return. It is "
            "conservative on purpose -- it grants the bucket the largest rank and the most "
            "extreme sample its size allows -- so a True is not a prediction that the bucket "
            "will reject, only that the arithmetic does not forbid it."
        ),
    ),
    SegmentedReportingLimitation(
        code="the_regime_coverage_row_counts_declared_labels_and_never_validates_the_calendar",
        detail=(
            "spans_multiple_regimes counts distinct labels on the market_regime axis. It does "
            "not check that the labels partition time contiguously, that a regime the caller "
            "named actually occurred in the observation windows, or that two runs used the "
            "same classifier. A caller who labels alternate decisions bull and bear produces a "
            "report that spans two regimes and means nothing, and this module cannot tell that "
            "apart from a genuine multi-regime study."
        ),
    ),
    SegmentedReportingLimitation(
        code="a_benchmark_is_paired_on_its_windows_and_two_results_can_share_a_window_by_accident",
        detail=(
            "The paired difference is published when the benchmark's observation windows are a "
            "multiset match for the strategy's. Pairing within a repeated window is by the "
            "order the caller supplied, because the windows alone cannot order two results "
            "that share one; a caller whose benchmark results are in a different order inside "
            "a repeated window gets a difference series that pairs the wrong rows. The "
            "windows are checked because that is checkable; the correspondence inside a tie "
            "is the caller's and is stated rather than verified."
        ),
    ),
    SegmentedReportingLimitation(
        code="the_paired_difference_inherits_every_assumption_the_sign_flip_test_already_made",
        detail=(
            "A difference cohort is tested by the same sign-flip randomization as any other, "
            "so it assumes the differences are symmetric about zero under the null and that "
            "they are independent draws. Two strategies run over the same overlapping windows "
            "on correlated names produce differences that are neither, and the interval on the "
            "difference will be too narrow for the same reason outcome_statistics' "
            "the_observations_are_resampled_as_though_they_were_independent_draws gives."
        ),
    ),
)
"""What the segmented report does not claim, stated where the segmentation is computed.

Two are about the labels being declared rather than derived, one about the family the caller
declared, one about what `can_ever_reject` is and is not, one about the regime row, and two
about the benchmark pairing.
"""


def smallest_attainable_p_value(sample_size: int, *, bootstrap_samples: int) -> float:
    """The lowest p-value `sign_flip_test` can return for a sample this size.

    Below `EXACT_SIGN_FLIP_LIMIT` the test enumerates, the observed sign pattern and its
    negation are always hits, and the floor is `2 * 2**-n`. Above it the test draws
    `bootstrap_samples` patterns and reports `(1 + hits) / (1 + draws)`, whose floor is
    `1 / (1 + draws)` at zero hits -- and that is the larger of the two at every draw count
    `OutcomeStatisticsRequest` admits, so the sampled arm is never given credit for an
    exactness it does not have.

    Not an estimate and not a bound taken on faith:
    `test_the_attainable_floor_is_attained_and_never_undercut` drives the shipped
    `sign_flip_test` over twelve thousand random samples at three sizes and finds the floor
    attained and never beaten.
    """
    if sample_size < 1:
        raise SegmentedReportingError("a sample size is at least one observation")
    if sample_size <= EXACT_SIGN_FLIP_LIMIT:
        return 2.0 ** (1 - sample_size)
    return 1.0 / (1 + bootstrap_samples)


class SegmentLabelling(BaseModel):
    """One axis: what it means, who assigned it, and a label for every result in order.

    `definition` and `source` are required and non-empty. A stored report that says a bucket
    was `large` without saying that large meant the top three deciles of `circ_mv` on the
    prediction day, assigned by the caller's own screen, has published a number nobody can
    reproduce and cannot be compared with next quarter's.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    axis_id: str = Field(min_length=1, max_length=64)
    definition: str = Field(min_length=1, max_length=1024)
    """How a result got its label: the boundaries, the field they were measured on, the day."""
    source: str = Field(min_length=1, max_length=256)
    """Who or what assigned the labels -- a vendor scheme, a screen, a hand classification."""
    labels: tuple[str, ...] = Field(min_length=1)
    """One label per result, positionally, in the order the results were supplied."""

    @model_validator(mode="after")
    def validate_the_axis_and_labels_cannot_collide(self) -> Self:
        if AXIS_SEPARATOR in self.axis_id:
            raise ValueError(
                f"an axis_id may not contain {AXIS_SEPARATOR!r}; the cohort identifier is "
                f"axis{AXIS_SEPARATOR}label and an axis carrying the separator could collide "
                "with another axis's bucket and share its q-value"
            )
        for label in self.labels:
            if not label:
                raise ValueError(
                    "every result needs a label on a declared axis; an empty label is an "
                    "unclassified result being reported as though it were classified"
                )
            if AXIS_SEPARATOR in label:
                raise ValueError(
                    f"a segment label may not contain {AXIS_SEPARATOR!r}; {label!r} would make "
                    "two different buckets share one cohort identifier"
                )
        return self


class BenchmarkCohort(BaseModel):
    """One comparison strategy's outcomes, what it is, and how it was built."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    benchmark_id: str = Field(min_length=1, max_length=64)
    kind: Literal["equal-weight-baseline", "naive-factor", "v1-baseline"]
    """The three `V2-P5-009` names, closed because the row names exactly these three.

    The row's own 说明 column says why the first is not optional: *等权基线是最容易被跳过也最能
    证伪的对照* -- the equal-weight baseline is the one most often skipped and the one most able
    to falsify. A ranked book that cannot beat equal weights over the same universe has not
    shown that its ranking did anything, and that comparison is cheap enough that omitting it
    is a choice rather than an oversight.
    """
    definition: str = Field(min_length=1, max_length=1024)
    """What the baseline actually did -- which universe, which weights, which rebalance."""
    results: tuple[ValidationResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_the_identifier_cannot_collide(self) -> Self:
        if AXIS_SEPARATOR in self.benchmark_id:
            raise ValueError(
                f"a benchmark_id may not contain {AXIS_SEPARATOR!r}; it is joined into the "
                "same cohort identifier space as the segment buckets"
            )
        return self


class SignalAxisDeclaration(BaseModel):
    """One axis declared per *group* rather than per result, which is how a face declares one.

    `SegmentLabelling` wants a label for every result in order, which is the right shape for the
    arithmetic and an impossible shape for a human to write down: a caller with four hundred
    stored outcomes is not going to hand-order four hundred labels. What a caller actually knows
    is that *this signal* trades banks and *that one* trades tech, so this declares the label per
    group key and `expand` turns it into the positional form.

    The refusal is the reason this is a type and not a dict comprehension at the face. A group
    with no label on a declared axis cannot be reported -- it is neither in a bucket nor
    honestly out of one -- so `expand` names it rather than defaulting it to `unknown`, which
    would invent a segment and then publish statistics for it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    axis_id: str = Field(min_length=1, max_length=64)
    definition: str = Field(min_length=1, max_length=1024)
    source: str = Field(min_length=1, max_length=256)
    labels: dict[str, str] = Field(min_length=1)
    """Group key -- a signal id at every face this repository ships -- to segment label."""

    def expand(self, keys: Sequence[str]) -> SegmentLabelling:
        """The positional labelling for `keys`, or a refusal naming every unlabelled group."""
        missing = sorted({key for key in keys if key not in self.labels})
        if missing:
            raise SegmentedReportingError(
                f"axis {self.axis_id!r} has no label for {', '.join(missing)}; every group in a "
                "segmented report needs a label on every declared axis, and a default label "
                "would invent a segment and then publish statistics for it"
            )
        return SegmentLabelling(
            axis_id=self.axis_id,
            definition=self.definition,
            source=self.source,
            labels=tuple(self.labels[key] for key in keys),
        )


class BenchmarkDeclaration(BaseModel):
    """One baseline named by the signal whose stored outcomes are its results.

    A benchmark's returns are outcomes like any other, so a face declares *which signal is the
    baseline* and the results are read back from the same store the strategy's came from. That
    is what makes the comparison a comparison: both arms went through `OutcomeValidator`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    benchmark_id: str = Field(min_length=1, max_length=64)
    kind: Literal["equal-weight-baseline", "naive-factor", "v1-baseline"]
    definition: str = Field(min_length=1, max_length=1024)
    signal_id: str = Field(min_length=1, max_length=128)
    """The stored signal whose validation results are this baseline's outcomes."""


class SegmentationPlan(BaseModel):
    """Every cut and every baseline a caller wants, in the shape a file or a body can carry.

    The unit `openalpha validation segmented --plan` parses and `OpenAlphaSDK.segmented_outcomes`
    takes. It holds declarations only -- no results, no store, no positional labels -- so one
    plan can be written once, checked into a repository beside the study, and replayed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    axes: tuple[SignalAxisDeclaration, ...] = Field(min_length=1)
    benchmarks: tuple[BenchmarkDeclaration, ...] = ()

    @model_validator(mode="after")
    def validate_the_declarations_are_distinct(self) -> Self:
        axis_ids = {axis.axis_id for axis in self.axes}
        if len(axis_ids) != len(self.axes):
            raise ValueError("every axis_id in one segmentation plan must be distinct")
        benchmark_ids = {benchmark.benchmark_id for benchmark in self.benchmarks}
        if len(benchmark_ids) != len(self.benchmarks):
            raise ValueError("every benchmark_id in one segmentation plan must be distinct")
        return self


class SegmentedReportRequest(BaseModel):
    """One result set, every cut of it, the benchmarks, and the family they all share."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    results: tuple[ValidationResult, ...] = Field(min_length=1)
    axes: tuple[SegmentLabelling, ...] = Field(min_length=1)
    benchmarks: tuple[BenchmarkCohort, ...] = ()
    declared_family_size: int = Field(ge=1)
    """How many hypotheses the study that produced this actually tested (`V2-P5-007`).

    Named `declared_` rather than `family_size` because the number this report *uses* is on the
    answer and a reader comparing the two is the point: the gap between them is the cuts the
    caller made and did not show.
    """
    false_discovery_rate: float = Field(gt=0, lt=1)
    dependence: DependenceAssumption
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1)
    bootstrap_samples: int = Field(default=1000, ge=100, le=100_000)
    random_seed: int = 0

    @model_validator(mode="after")
    def validate_every_axis_labels_every_result(self) -> Self:
        identifiers = {axis.axis_id for axis in self.axes}
        if len(identifiers) != len(self.axes):
            raise ValueError("every axis_id in one segmented report must be distinct")
        for axis in self.axes:
            if len(axis.labels) != len(self.results):
                raise ValueError(
                    f"axis {axis.axis_id!r} carries {len(axis.labels)} label(s) for "
                    f"{len(self.results)} result(s); a cut that labels only some of the "
                    "results silently drops the rest out of every bucket on that axis"
                )
        benchmark_ids = {benchmark.benchmark_id for benchmark in self.benchmarks}
        if len(benchmark_ids) != len(self.benchmarks):
            raise ValueError("every benchmark_id in one segmented report must be distinct")
        if benchmark_ids & identifiers:
            raise ValueError(
                "a benchmark_id may not repeat an axis_id; they share one identifier space"
            )
        return self


class SegmentCapability(BaseModel):
    """What a bucket this size could and could not have shown, as arithmetic.

    Attached to every bucket including the ones that were tested, because a bucket that *was*
    tested and could never have rejected is the case a table of q-values hides most completely.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_size: int = Field(ge=0)
    smallest_attainable_p_value: float | None = Field(default=None, gt=0, le=1)
    """`None` below `MINIMUM_INTERVAL_SAMPLE_SIZE`, where no test is run at all."""
    most_permissive_critical_value: float = Field(ge=0)
    """The line at the largest rank in the family -- the easiest any hypothesis here can face."""
    can_ever_reject: bool
    """Whether the floor is at or below that line. `False` is a fact about resolution."""
    reason: str

    @model_validator(mode="after")
    def validate_the_verdict_follows_from_the_two_numbers(self) -> Self:
        """`can_ever_reject` is the comparison and never an independently supplied flag.

        Reachable from a *document* rather than from `report_segmented_outcomes`, which is the
        path a stored report is read back through -- the same argument
        `CohortStatistics.validate_the_inference_is_present_or_named_absent` makes.
        """
        floor = self.smallest_attainable_p_value
        expected = floor is not None and floor <= self.most_permissive_critical_value
        if self.can_ever_reject != expected:
            raise ValueError(
                "can_ever_reject must be whether the attainable floor is at or below the most "
                "permissive critical value in the family"
            )
        return self


class SegmentStatistics(BaseModel):
    """One bucket: its axis, its label, its columns from `V2-P5-008`, and what it could show."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    axis_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    cohort_id: str = Field(min_length=1, max_length=128)
    statistics: CohortStatistics
    capability: SegmentCapability


class AxisReport(BaseModel):
    """One cut: what it declared, its buckets, and how many of them could show anything.

    A view onto the single family and never a family of its own. There is no `family_size` and
    no `MultipleTestingReport` here, because an axis that carried one would be the four-families
    defect this module exists to prevent, wearing a field name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    axis_id: str = Field(min_length=1, max_length=64)
    definition: str = Field(min_length=1, max_length=1024)
    source: str = Field(min_length=1, max_length=256)
    segments: tuple[SegmentStatistics, ...] = Field(min_length=1)

    @property
    def testable_segments(self) -> int:
        """Buckets that reached `MINIMUM_INTERVAL_SAMPLE_SIZE` and carry a test."""
        return sum(1 for segment in self.segments if segment.statistics.was_tested)

    @property
    def segments_that_could_ever_reject(self) -> int:
        """Buckets whose attainable floor is inside the family's most permissive line."""
        return sum(1 for segment in self.segments if segment.capability.can_ever_reject)


class RegimeCoverage(BaseModel):
    """How many market regimes the evidence actually spans, measured rather than intended."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    declared: bool
    """Whether a `market_regime` axis was supplied at all."""
    regimes_observed: int = Field(ge=0)
    regimes_testable: int = Field(ge=0)
    """Of those, how many reached `MINIMUM_INTERVAL_SAMPLE_SIZE`."""
    spans_multiple_regimes: bool
    reason: str

    @model_validator(mode="after")
    def validate_the_span_follows_from_the_count(self) -> Self:
        if self.spans_multiple_regimes != (self.regimes_testable > 1):
            raise ValueError(
                "spans_multiple_regimes must be whether more than one regime was testable; a "
                "regime with no testable bucket contributes no out-of-sample evidence"
            )
        if not self.declared and self.regimes_observed:
            raise ValueError("a report with no market_regime axis observes no regimes")
        return self


class BenchmarkComparison(BaseModel):
    """One benchmark's own columns and, when the windows pair, the paired difference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_id: str = Field(min_length=1, max_length=64)
    kind: Literal["equal-weight-baseline", "naive-factor", "v1-baseline"]
    definition: str = Field(min_length=1, max_length=1024)
    cohort_id: str = Field(min_length=1, max_length=128)
    statistics: CohortStatistics
    capability: SegmentCapability
    difference_cohort_id: str | None = None
    difference: CohortStatistics | None = None
    """Mean of `strategy_net - benchmark_net` over paired results, tested as one sample."""
    difference_capability: SegmentCapability | None = None
    comparison_absence_reason: str | None = None

    @model_validator(mode="after")
    def validate_the_difference_is_present_or_named_absent(self) -> Self:
        """Either the paired difference is here whole, or something says why it is not."""
        parts = (self.difference_cohort_id, self.difference, self.difference_capability)
        present = [part is not None for part in parts]
        if any(present) and not all(present):
            raise ValueError(
                "a paired difference carries its identifier, its statistics and its capability "
                "together or not at all"
            )
        if all(present) and self.comparison_absence_reason is not None:
            raise ValueError("a benchmark with a paired difference states no absence")
        if not any(present) and self.comparison_absence_reason is None:
            raise ValueError("a benchmark with no paired difference must say why it has none")
        return self


class SegmentedReport(BaseModel):
    """Every bucket of every axis, every benchmark, and the one family they were tested in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    axes: tuple[AxisReport, ...] = Field(min_length=1)
    benchmarks: tuple[BenchmarkComparison, ...] = ()
    regime_coverage: RegimeCoverage
    statistics: OutcomeStatisticsReport
    """The single `V2-P5-008` report every bucket above is a row of."""
    declared_family_size: int = Field(ge=1)
    segment_hypotheses: int = Field(ge=0)
    benchmark_hypotheses: int = Field(ge=0)
    minimum_sample_size: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_there_is_exactly_one_family(self) -> Self:
        """The join between the buckets and the control, as a rule rather than a habit.

        The count and the identifier check are separate for
        `OutcomeStatisticsReport.validate_the_family_is_the_tested_cohorts`' reason: identifiers
        are unique when built here and nothing makes them unique on a report parsed off disk,
        so two buckets called `industry:banks` have an identifier set of size one and only the
        count says that two tested buckets are answered by one q-value.
        """
        rows = tuple(segment.cohort_id for axis in self.axes for segment in axis.segments) + tuple(
            identifier
            for benchmark in self.benchmarks
            for identifier in (benchmark.benchmark_id and benchmark.cohort_id,)
        )
        difference_rows = tuple(
            benchmark.difference_cohort_id
            for benchmark in self.benchmarks
            if benchmark.difference_cohort_id is not None
        )
        expected = rows + difference_rows
        reported = tuple(cohort.cohort_id for cohort in self.statistics.cohorts)
        if len(expected) != len(reported):
            raise ValueError(
                "the family must hold exactly one row per bucket, benchmark and paired difference"
            )
        if set(expected) != set(reported):
            raise ValueError("the family must name exactly the buckets and benchmarks reported")
        if self.statistics.multiple_testing.family_size != self.declared_family_size:
            raise ValueError("the controlled family must be the family this report declared")
        return self

    @property
    def tested_hypotheses(self) -> int:
        """How many rows across every axis and benchmark actually carried a test."""
        return self.statistics.tested_hypotheses

    @property
    def hypotheses_that_could_ever_reject(self) -> int:
        """Rows whose attainable floor is inside the family's most permissive line."""
        return (
            sum(axis.segments_that_could_ever_reject for axis in self.axes)
            + sum(1 for benchmark in self.benchmarks if benchmark.capability.can_ever_reject)
            + sum(
                1
                for benchmark in self.benchmarks
                if benchmark.difference_capability is not None
                and benchmark.difference_capability.can_ever_reject
            )
        )


def _bucket(results: tuple[ValidationResult, ...], labels: tuple[str, ...]) -> dict[str, list[int]]:
    """Label -> the indices carrying it, in first-appearance order.

    First-appearance rather than sorted so a caller's own ordering of an ordinal axis --
    `small`, `mid`, `large` -- survives into the report instead of being alphabetised into
    `large`, `mid`, `small`, which reads as a different cut.
    """
    buckets: dict[str, list[int]] = {}
    for index, label in enumerate(labels):
        buckets.setdefault(label, []).append(index)
    return buckets


def _paired_difference(
    strategy: tuple[ValidationResult, ...],
    benchmark: tuple[ValidationResult, ...],
) -> tuple[ValidationResult, ...] | None:
    """Difference results when the two sets' observation windows are a multiset match.

    Returns `None` -- never a partial pairing and never a truncation to the shorter list --
    when the windows do not match, because a difference over the rows that happened to line up
    is a different study from the one the caller asked for and would be reported as though it
    were the same one.

    The pairing inside a repeated window is by supplied order; see
    `a_benchmark_is_paired_on_its_windows_and_two_results_can_share_a_window_by_accident`.

    **There is no separate length check and there was one until a mutation sweep removed it.**
    Equal `Counter`s have equal totals, so the window comparison already refuses two lists of
    different lengths and the guard above it could not fail on any input -- deleting it changed
    no test. `test_a_benchmark_of_a_different_length_is_not_truncated_into_a_pairing` still
    drives the behaviour, through the comparison that actually decides it.
    """
    windows = Counter((item.observation_start, item.observation_end) for item in strategy)
    if windows != Counter((item.observation_start, item.observation_end) for item in benchmark):
        return None

    remaining: dict[tuple[Any, Any], list[ValidationResult]] = {}
    for item in benchmark:
        remaining.setdefault((item.observation_start, item.observation_end), []).append(item)

    paired: list[ValidationResult] = []
    for index, item in enumerate(strategy, start=1):
        other = remaining[(item.observation_start, item.observation_end)].pop(0)
        difference = item.net_active_return - other.net_active_return
        paired.append(
            ValidationResult(
                signal_id=item.signal_id,
                decision_id=f"paired-{index}",
                observation_start=item.observation_start,
                observation_end=item.observation_end,
                realized_return=difference,
                benchmark_return=0.0,
                transaction_cost=0.0,
                attribution=(),
                unexplained_return=difference,
                confidence=min(item.confidence, other.confidence),
            )
        )
    return tuple(paired)


def _capability(
    sample_size: int,
    *,
    most_permissive_critical_value: float,
    bootstrap_samples: int,
) -> SegmentCapability:
    """What a bucket this size could have shown against this family's easiest line."""
    if sample_size < MINIMUM_INTERVAL_SAMPLE_SIZE:
        return SegmentCapability(
            sample_size=sample_size,
            smallest_attainable_p_value=None,
            most_permissive_critical_value=most_permissive_critical_value,
            can_ever_reject=False,
            reason=(
                f"{sample_size} observation(s) is below MINIMUM_INTERVAL_SAMPLE_SIZE "
                f"({MINIMUM_INTERVAL_SAMPLE_SIZE}), so no test is run and the bucket is not in "
                "the family at all"
            ),
        )
    floor = smallest_attainable_p_value(sample_size, bootstrap_samples=bootstrap_samples)
    can = floor <= most_permissive_critical_value
    if can:
        reason = (
            f"an exact sign-flip test over {sample_size} observation(s) can reach p = {floor}, "
            f"which is at or below the family's most permissive critical value "
            f"{most_permissive_critical_value}; the arithmetic does not forbid a rejection"
        )
    else:
        reason = (
            f"an exact sign-flip test over {sample_size} observation(s) cannot return a "
            f"p-value below {floor}, and the most permissive critical value anywhere in this "
            f"family is {most_permissive_critical_value}; this bucket could not have been a "
            "discovery on any data whatsoever, and its q-value measures the study's resolution "
            "rather than the segment's skill"
        )
    return SegmentCapability(
        sample_size=sample_size,
        smallest_attainable_p_value=floor,
        most_permissive_critical_value=most_permissive_critical_value,
        can_ever_reject=can,
        reason=reason,
    )


def report_segmented_outcomes(request: SegmentedReportRequest) -> SegmentedReport:
    """Cut the results every declared way, test every bucket in one family, say what each cannot.

    Three steps, and the order matters. The buckets and the benchmarks are formed first, then
    **one** `report_outcome_statistics` runs over all of them together, and only then is the
    capability of each bucket computed -- because the most permissive critical value is a
    property of the assembled family and cannot be known while the family is still being built.

    A bucket below `MINIMUM_INTERVAL_SAMPLE_SIZE` is carried with its return columns, a named
    absence and `can_ever_reject = False`, exactly as `V2-P5-008` carries a one-result cohort,
    and it is outside the family for the same reason: an untested hypothesis is not a hypothesis
    whose q-value was large.
    """
    cohorts: list[OutcomeCohort] = []
    index_of: dict[str, tuple[str, str]] = {}

    for axis in request.axes:
        for label, indices in _bucket(request.results, axis.labels).items():
            cohort_id = f"{axis.axis_id}{AXIS_SEPARATOR}{label}"
            cohorts.append(
                OutcomeCohort(
                    cohort_id=cohort_id,
                    results=tuple(request.results[position] for position in indices),
                )
            )
            index_of[cohort_id] = (axis.axis_id, label)

    segment_hypotheses = len(cohorts)

    differences: dict[str, tuple[ValidationResult, ...] | None] = {}
    for benchmark in request.benchmarks:
        cohorts.append(OutcomeCohort(cohort_id=benchmark.benchmark_id, results=benchmark.results))
        paired = _paired_difference(request.results, benchmark.results)
        differences[benchmark.benchmark_id] = paired
        if paired is not None:
            cohorts.append(
                OutcomeCohort(
                    cohort_id=f"{benchmark.benchmark_id}{AXIS_SEPARATOR}difference",
                    results=paired,
                )
            )

    benchmark_hypotheses = len(cohorts) - segment_hypotheses

    if request.declared_family_size < len(cohorts):
        raise SegmentedReportingError(
            f"declared_family_size {request.declared_family_size} is smaller than the "
            f"{len(cohorts)} hypothesis/hypotheses this segmented report tests "
            f"({segment_hypotheses} segment bucket(s) across {len(request.axes)} axis/axes and "
            f"{benchmark_hypotheses} benchmark row(s)); cutting one cohort into buckets "
            "multiplies the hypotheses tested and a family declared before the cut publishes "
            "several chances to look skilful at the price of one"
        )

    statistics = report_outcome_statistics(
        OutcomeStatisticsRequest(
            cohorts=tuple(cohorts),
            family_size=request.declared_family_size,
            false_discovery_rate=request.false_discovery_rate,
            dependence=request.dependence,
            confidence_level=request.confidence_level,
            bootstrap_samples=request.bootstrap_samples,
            random_seed=request.random_seed,
        )
    )

    penalty = statistics.multiple_testing.dependence_penalty
    most_permissive = (
        statistics.multiple_testing.reported_hypotheses
        * request.false_discovery_rate
        / (request.declared_family_size * penalty)
    )
    measured = {cohort.cohort_id: cohort for cohort in statistics.cohorts}

    axes: list[AxisReport] = []
    for axis in request.axes:
        segments: list[SegmentStatistics] = []
        for label in _bucket(request.results, axis.labels):
            cohort_id = f"{axis.axis_id}{AXIS_SEPARATOR}{label}"
            row = measured[cohort_id]
            segments.append(
                SegmentStatistics(
                    axis_id=axis.axis_id,
                    label=label,
                    cohort_id=cohort_id,
                    statistics=row,
                    capability=_capability(
                        row.sample_size,
                        most_permissive_critical_value=most_permissive,
                        bootstrap_samples=request.bootstrap_samples,
                    ),
                )
            )
        axes.append(
            AxisReport(
                axis_id=axis.axis_id,
                definition=axis.definition,
                source=axis.source,
                segments=tuple(segments),
            )
        )

    comparisons: list[BenchmarkComparison] = []
    for benchmark in request.benchmarks:
        row = measured[benchmark.benchmark_id]
        paired = differences[benchmark.benchmark_id]
        if paired is None:
            difference_id = None
            difference = None
            difference_capability = None
            absence = (
                f"benchmark {benchmark.benchmark_id!r} carries {len(benchmark.results)} "
                f"result(s) against the strategy's {len(request.results)} and their observation "
                "windows are not a multiset match, so the results cannot be paired; the "
                "difference of two unpaired means is not the mean of the differences and none "
                "is published"
            )
        else:
            difference_id = f"{benchmark.benchmark_id}{AXIS_SEPARATOR}difference"
            difference = measured[difference_id]
            difference_capability = _capability(
                difference.sample_size,
                most_permissive_critical_value=most_permissive,
                bootstrap_samples=request.bootstrap_samples,
            )
            absence = None
        comparisons.append(
            BenchmarkComparison(
                benchmark_id=benchmark.benchmark_id,
                kind=benchmark.kind,
                definition=benchmark.definition,
                cohort_id=benchmark.benchmark_id,
                statistics=row,
                capability=_capability(
                    row.sample_size,
                    most_permissive_critical_value=most_permissive,
                    bootstrap_samples=request.bootstrap_samples,
                ),
                difference_cohort_id=difference_id,
                difference=difference,
                difference_capability=difference_capability,
                comparison_absence_reason=absence,
            )
        )

    regime = next((axis for axis in axes if axis.axis_id == MARKET_REGIME_AXIS), None)
    if regime is None:
        coverage = RegimeCoverage(
            declared=False,
            regimes_observed=0,
            regimes_testable=0,
            spans_multiple_regimes=False,
            reason=(
                f"no axis called {MARKET_REGIME_AXIS!r} was declared, so this report spans no "
                "measured regimes and supports no claim about regime robustness; a market "
                "regime is a classification the caller defines and this module never infers one"
            ),
        )
    else:
        observed = len(regime.segments)
        testable = regime.testable_segments
        if testable > 1:
            reason = (
                f"{testable} of {observed} declared regime(s) reached "
                f"MINIMUM_INTERVAL_SAMPLE_SIZE ({MINIMUM_INTERVAL_SAMPLE_SIZE}), so the "
                "out-of-sample evidence spans more than one regime"
            )
        else:
            reason = (
                f"only {testable} of {observed} declared regime(s) reached "
                f"MINIMUM_INTERVAL_SAMPLE_SIZE ({MINIMUM_INTERVAL_SAMPLE_SIZE}); a walk-forward "
                "whose testable evidence lies in one regime cannot support a claim of regime "
                "robustness however many folds it ran"
            )
        coverage = RegimeCoverage(
            declared=True,
            regimes_observed=observed,
            regimes_testable=testable,
            spans_multiple_regimes=testable > 1,
            reason=reason,
        )

    return SegmentedReport(
        axes=tuple(axes),
        benchmarks=tuple(comparisons),
        regime_coverage=coverage,
        statistics=statistics,
        declared_family_size=request.declared_family_size,
        segment_hypotheses=segment_hypotheses,
        benchmark_hypotheses=benchmark_hypotheses,
        minimum_sample_size=MINIMUM_INTERVAL_SAMPLE_SIZE,
    )


def segmented_report_view(report: SegmentedReport) -> dict[str, Any]:
    """One segmented report as data, for whichever face is handing it out.

    `outcome_statistics_view`'s argument for existing, unchanged: the CLI's `--json` and
    `OpenAlphaSDK.segmented_report_view` emit these bytes and not two shapes that agree today.

    Three things are on the face that a bare table of segment returns would have left implicit.
    `family` is the *one* family every bucket was tested in, so a reader can see that four axes
    produced one control and not four. Every bucket carries its `capability`, so a q-value that
    could never have been small reads as resolution rather than as evidence. And every axis
    carries the `definition` and `source` of its labels, so a bucket called `large` says what
    large meant and who said so.
    """
    verdicts = {
        verdict.hypothesis_id: verdict for verdict in report.statistics.multiple_testing.verdicts
    }

    def _row(
        cohort_id: str, statistics: CohortStatistics, capability: SegmentCapability
    ) -> dict[str, Any]:
        verdict = verdicts.get(cohort_id)
        return {
            "cohort_id": cohort_id,
            "sample_size": statistics.sample_size,
            "gross_active_return": statistics.gross_active_return,
            "cost_drag": statistics.cost_drag,
            "net_active_return": statistics.net_active_return,
            "unexplained_return": statistics.unexplained_return,
            "interval": None if statistics.interval is None else statistics.interval.model_dump(),
            "test": None if statistics.test is None else statistics.test.model_dump(),
            "absence_reason": statistics.absence_reason,
            "q_value": None if verdict is None else verdict.q_value,
            "critical_value": None if verdict is None else verdict.critical_value,
            "rejected": None if verdict is None else verdict.rejected,
            "capability": capability.model_dump(),
        }

    return {
        "axes": [
            {
                "axis_id": axis.axis_id,
                "definition": axis.definition,
                "source": axis.source,
                "testable_segments": axis.testable_segments,
                "segments_that_could_ever_reject": axis.segments_that_could_ever_reject,
                "segments": [
                    {"label": segment.label}
                    | _row(segment.cohort_id, segment.statistics, segment.capability)
                    for segment in axis.segments
                ],
            }
            for axis in report.axes
        ],
        "benchmarks": [
            {
                "benchmark_id": benchmark.benchmark_id,
                "kind": benchmark.kind,
                "definition": benchmark.definition,
                "comparison_absence_reason": benchmark.comparison_absence_reason,
                "benchmark": _row(benchmark.cohort_id, benchmark.statistics, benchmark.capability),
                "difference": (
                    None
                    if benchmark.difference is None or benchmark.difference_capability is None
                    else _row(
                        benchmark.difference_cohort_id or "",
                        benchmark.difference,
                        benchmark.difference_capability,
                    )
                ),
            }
            for benchmark in report.benchmarks
        ],
        "regime_coverage": report.regime_coverage.model_dump(),
        "family": {
            "family_size": report.statistics.multiple_testing.family_size,
            "reported_hypotheses": report.statistics.multiple_testing.reported_hypotheses,
            "withheld_hypotheses": report.statistics.multiple_testing.withheld_hypotheses,
            "family_is_complete": report.statistics.multiple_testing.family_is_complete,
            "false_discovery_rate": report.statistics.multiple_testing.false_discovery_rate,
            "dependence": report.statistics.multiple_testing.dependence,
            "dependence_penalty": report.statistics.multiple_testing.dependence_penalty,
            "discoveries": report.statistics.multiple_testing.discoveries,
            "largest_rejected_rank": report.statistics.multiple_testing.largest_rejected_rank,
        },
        "declared_family_size": report.declared_family_size,
        "segment_hypotheses": report.segment_hypotheses,
        "benchmark_hypotheses": report.benchmark_hypotheses,
        "tested_hypotheses": report.tested_hypotheses,
        "hypotheses_that_could_ever_reject": report.hypotheses_that_could_ever_reject,
        "untested_cohorts": list(report.statistics.untested_cohorts),
        "confidence_level": report.statistics.confidence_level,
        "bootstrap_samples": report.statistics.bootstrap_samples,
        "random_seed": report.statistics.random_seed,
        "minimum_sample_size": report.minimum_sample_size,
        "limitations": [
            {"code": limitation.code, "detail": limitation.detail}
            for limitation in KNOWN_SEGMENTED_REPORTING_LIMITATIONS
        ],
    }
