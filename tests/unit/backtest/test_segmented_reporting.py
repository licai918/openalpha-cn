"""Four cuts of one result set are one family, and a bucket too small says what it cannot show.

`V2-P5-009` asks for segmented reporting, a multi-regime walk-forward, and the three baselines
side by side. The arithmetic below is closed form and asserted with `==`; the parts that are
not arithmetic -- what a bucket could ever have shown, whether a run spans two regimes, whether
a benchmark can be paired -- are what most of this file separates.

## The corpora, and what each one alone cannot tell apart

- **`WIDE`** -- eight results cut three ways: `industry` into two buckets of four,
  `market_capitalisation` into three of `(3, 3, 2)`, and `market_regime` into two of four.
  Three axes, **seven buckets**, and that ratio is the whole point of the module: an
  implementation that ran `report_outcome_statistics` once per axis reports three families of
  two or three, and `test_three_axes_are_one_family_and_not_three` counts seven in one.
- **The rate is `2**-3` and it is chosen, not idiomatic.** With seven reported hypotheses in a
  declared family of seven the most permissive critical value is `7 * 0.125 / 7`, exactly
  `0.125`, which is exactly the attainable floor of a four-observation bucket. So `WIDE` holds
  all three cases at once: the four-observation buckets sit **on** the line (and are admitted,
  because the comparison is `<=`), the three- and two-observation buckets are strictly above it
  and could not have rejected on any data whatsoever. A rate of `0.5` would have put every
  bucket inside the line and separated nothing.
- **`PAIRED`** -- four strategy results and four benchmark results on the same windows, built
  so the strategy beat the benchmark by exactly `0.125` on **every** pairing. Alone, neither
  arm is anything: the strategy's own sign-flip p-value is `0.5` and the benchmark's is `1.0`.
  Paired, the difference is the most extreme a four-observation sample admits, `0.125`. This is
  the arm that shows why the paired column is published at all, and it is also the arm that
  shows why the *mean* is not the reason -- `fmean(a - b)` and `fmean(a) - fmean(b)` are
  bit-identical here, as they are on any paired dyadic sample. The column is redundant and the
  inference is not.
- **`SOLITARY`** -- an axis one of whose buckets holds a single result. That bucket keeps its
  return columns, gets a named absence, `can_ever_reject = False`, and is **outside** the
  family, so `reported_hypotheses` is one below the bucket count.
- **`ONE_REGIME`** -- every result labelled `bull`. The report runs, the folds are whatever the
  caller ran, and `spans_multiple_regimes` is `False`.

One arm separates nothing on its own. `WIDE` alone passes for an implementation that never
pairs a benchmark; `PAIRED` alone passes for one that reports each axis as its own family;
`ONE_REGIME` alone passes for one that never publishes a regime row at all.
"""

import random
import statistics
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.outcome_statistics import (
    EXACT_SIGN_FLIP_LIMIT,
    MINIMUM_INTERVAL_SAMPLE_SIZE,
    sign_flip_test,
)
from openalpha_cn.backtest.segmented_reporting import (
    INDUSTRY_AXIS,
    KNOWN_SEGMENTED_REPORTING_LIMITATIONS,
    LIQUIDITY_AXIS,
    MARKET_CAPITALISATION_AXIS,
    MARKET_REGIME_AXIS,
    SEGMENT_AXES,
    BenchmarkCohort,
    SegmentCapability,
    SegmentedReportingError,
    SegmentedReportRequest,
    SegmentLabelling,
    report_segmented_outcomes,
    segmented_report_view,
    smallest_attainable_p_value,
)
from openalpha_cn.domain.validation import AttributionTerm, ValidationResult

BENCHMARK: Final[float] = 0.0625
"""`2**-4`, the benchmark `tests/unit/backtest/test_outcome_statistics.py` uses."""

COST: Final[float] = 0.0078125
"""`2**-7`, likewise."""

NET_OFFSET: Final[float] = 0.0703125
"""`BENCHMARK + COST`, so `realized = net + NET_OFFSET` and every net below is exact."""

RATE: Final[float] = 0.125
"""`2**-3`. Chosen so `7 * 0.125 / 7` is exactly `0.125`, the floor of a four-result bucket.

At `0.5` every bucket in `WIDE` would sit inside the line and `can_ever_reject` would be `True`
everywhere, which separates nothing. At `0.125` the four-observation buckets land exactly *on*
the line and the smaller ones strictly above it.
"""

WIDE_NETS: Final[tuple[float, ...]] = (
    0.25,
    -0.125,
    0.375,
    -0.0625,
    0.1875,
    -0.03125,
    0.4375,
    -0.09375,
)
"""Eight dyadic net active returns, signs alternating so no bucket is trivially one-sided."""

WIDE_INDUSTRY: Final[tuple[str, ...]] = (
    "banks",
    "banks",
    "banks",
    "banks",
    "tech",
    "tech",
    "tech",
    "tech",
)
"""Two buckets of four. Four observations is where the floor equals the line at `RATE`."""

WIDE_CAPITALISATION: Final[tuple[str, ...]] = (
    "small",
    "small",
    "small",
    "mid",
    "mid",
    "mid",
    "large",
    "large",
)
"""Three buckets of `(3, 3, 2)` -- every one of them strictly above the line.

**Declared `small, mid, large` and not `large, mid, small`, and that is the whole of what
`test_the_label_order_is_the_callers_and_not_alphabetised` can measure.** The obvious ordinal
spelling runs large-to-small, which is *already* alphabetical, so an implementation that sorted
its buckets would produce the identical table and the test would pass while measuring nothing.
A mutation sweep found exactly that: `sorted(buckets)` survived against the first version of
this constant. Reversed, the caller's order and the sorted order differ in every position.
"""

WIDE_REGIME: Final[tuple[str, ...]] = (
    "bull",
    "bull",
    "bull",
    "bull",
    "bear",
    "bear",
    "bear",
    "bear",
)
"""Two buckets of four, so `WIDE` genuinely spans two regimes."""

WIDE_BUCKETS: Final[int] = 7
"""`2 + 3 + 2`. Three axes, seven hypotheses -- the number the module exists to get right."""

PAIRED_STRATEGY_NETS: Final[tuple[float, ...]] = (0.25, -0.125, 0.375, -0.0625)
"""Sign-flip p-value `0.5` on its own: two of the four are losses and the sum is unremarkable."""

PAIRED_BENCHMARK_NETS: Final[tuple[float, ...]] = (0.125, -0.25, 0.25, -0.1875)
"""Sign-flip p-value `1.0` on its own."""

PAIRED_DIFFERENCE: Final[float] = 0.125
"""The strategy beat the benchmark by exactly this on **every** pairing, so the difference
series is four identical numbers and its p-value is `2**-3`, the floor at four observations."""

START: Final[datetime] = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def _result(*, index: int, net: float, day: int, signal: str = "sig") -> ValidationResult:
    """One held-arm result whose `net_active_return` is exactly `net`.

    The same shape `OutcomeValidator._attribute` emits for a held decision since `V2-P5-005`:
    one measured cost term and the whole selection return unexplained.
    """
    realized = net + NET_OFFSET
    return ValidationResult(
        signal_id=signal,
        decision_id=f"{signal}-{index}",
        observation_start=START + timedelta(days=day),
        observation_end=START + timedelta(days=day + 5),
        realized_return=realized,
        benchmark_return=BENCHMARK,
        transaction_cost=COST,
        attribution=(
            AttributionTerm(category="rule", name="transaction-cost", contribution=-COST),
        ),
        unexplained_return=realized - BENCHMARK,
        confidence=0.5,
    )


def _results(nets: tuple[float, ...], *, signal: str = "sig") -> tuple[ValidationResult, ...]:
    """One result per net, each on its own five-day window so the windows are distinct."""
    return tuple(
        _result(index=index, net=net, day=index, signal=signal) for index, net in enumerate(nets)
    )


def _axis(axis_id: str, labels: tuple[str, ...]) -> SegmentLabelling:
    return SegmentLabelling(
        axis_id=axis_id,
        definition=f"the caller's declared {axis_id} cut, boundaries fixed before the run",
        source="declared by the caller",
        labels=labels,
    )


def _request(
    *,
    nets: tuple[float, ...] = WIDE_NETS,
    axes: tuple[SegmentLabelling, ...] | None = None,
    benchmarks: tuple[BenchmarkCohort, ...] = (),
    declared_family_size: int = WIDE_BUCKETS,
    false_discovery_rate: float = RATE,
    dependence: str = "independent-or-positively-dependent",
) -> SegmentedReportRequest:
    return SegmentedReportRequest(
        results=_results(nets),
        axes=axes
        or (
            _axis(INDUSTRY_AXIS, WIDE_INDUSTRY),
            _axis(MARKET_CAPITALISATION_AXIS, WIDE_CAPITALISATION),
            _axis(MARKET_REGIME_AXIS, WIDE_REGIME),
        ),
        benchmarks=benchmarks,
        declared_family_size=declared_family_size,
        false_discovery_rate=false_discovery_rate,
        dependence=dependence,  # type: ignore[arg-type]
    )


def _segment(report, axis_id: str, label: str):
    """The one bucket on `axis_id` carrying `label`, or a failure naming what was there."""
    axis = next(item for item in report.axes if item.axis_id == axis_id)
    return next(item for item in axis.segments if item.label == label)


# --- three cuts of one result set are one family ----------------------------------------------


def test_three_axes_are_one_family_and_not_three() -> None:
    """The defect the module exists to prevent, counted rather than described.

    Three axes over eight results produce seven buckets. An implementation that reported each
    axis as its own family would put two or three hypotheses in each of three
    `MultipleTestingReport`s; this asserts one report holding all seven.
    """
    report = report_segmented_outcomes(_request())

    assert [axis.axis_id for axis in report.axes] == [
        INDUSTRY_AXIS,
        MARKET_CAPITALISATION_AXIS,
        MARKET_REGIME_AXIS,
    ]
    assert [len(axis.segments) for axis in report.axes] == [2, 3, 2]
    assert report.segment_hypotheses == WIDE_BUCKETS
    assert report.statistics.multiple_testing.reported_hypotheses == WIDE_BUCKETS
    assert report.statistics.multiple_testing.family_size == WIDE_BUCKETS
    assert report.tested_hypotheses == WIDE_BUCKETS


def test_a_family_declared_before_the_cut_is_refused_by_the_bucket_count() -> None:
    """Declaring one hypothesis per axis is the mistake; the message names both numbers."""
    with pytest.raises(SegmentedReportingError) as error:
        report_segmented_outcomes(_request(declared_family_size=3))

    message = str(error.value)
    assert "3" in message
    assert str(WIDE_BUCKETS) in message
    assert "multiplies the hypotheses tested" in message


def test_every_bucket_is_a_row_in_the_one_family_and_carries_its_own_verdict() -> None:
    """Each bucket's q-value comes from the single family, keyed by `axis:label`."""
    report = report_segmented_outcomes(_request())

    identifiers = {cohort.cohort_id for cohort in report.statistics.cohorts}
    assert identifiers == {
        f"{INDUSTRY_AXIS}:banks",
        f"{INDUSTRY_AXIS}:tech",
        f"{MARKET_CAPITALISATION_AXIS}:large",
        f"{MARKET_CAPITALISATION_AXIS}:mid",
        f"{MARKET_CAPITALISATION_AXIS}:small",
        f"{MARKET_REGIME_AXIS}:bull",
        f"{MARKET_REGIME_AXIS}:bear",
    }
    for axis in report.axes:
        for segment in axis.segments:
            assert report.statistics.verdict_for(segment.cohort_id) is not None


def test_the_label_order_is_the_callers_and_is_not_alphabetised() -> None:
    """`large, mid, small` is an ordinal cut and sorting it reads as a different one."""
    report = report_segmented_outcomes(_request())
    axis = next(item for item in report.axes if item.axis_id == MARKET_CAPITALISATION_AXIS)

    assert [segment.label for segment in axis.segments] == ["small", "mid", "large"]
    assert [segment.label for segment in axis.segments] != sorted(
        segment.label for segment in axis.segments
    )


# --- what a bucket could ever have shown -------------------------------------------------------


def test_the_attainable_floor_is_attained_and_never_undercut() -> None:
    """`2**(1 - n)` is measured against the shipped test, not asserted from the algebra.

    Twelve thousand random samples across three sizes. The floor is reached (the identical
    same-sign sample attains it) and no sample of any shape falls below it.
    """
    generator = random.Random(7)
    for count in (3, 5, 8):
        floor = smallest_attainable_p_value(count, bootstrap_samples=1000)
        assert floor == 2.0 ** (1 - count)

        identical = tuple([0.25] * count)
        attained = sign_flip_test(identical, bootstrap_samples=1000, random_seed=0)
        assert attained.p_value == floor

        lowest = min(
            sign_flip_test(
                tuple(generator.uniform(-1, 1) for _ in range(count)),
                bootstrap_samples=1000,
                random_seed=0,
            ).p_value
            for _draw in range(4000)
        )
        assert lowest == floor


def test_above_the_exact_limit_the_floor_is_the_sampled_one_and_not_the_enumerated_one() -> None:
    """A sampled test cannot claim the exactness it does not have."""
    count = EXACT_SIGN_FLIP_LIMIT + 1
    floor = smallest_attainable_p_value(count, bootstrap_samples=1000)

    assert floor == 1 / 1001
    assert floor > 2.0 ** (1 - count)
    assert sign_flip_test(tuple([0.25] * count), bootstrap_samples=1000, random_seed=0).p_value == (
        floor
    )


def test_a_bucket_whose_floor_is_above_the_line_could_never_have_rejected() -> None:
    """Three observations cannot reach `0.125`, so its q-value measures resolution.

    The two- and three-observation buckets of `market_capitalisation` are the case, and the
    message says so in the words a reader needs rather than leaving a large q-value to imply
    an absence of skill.
    """
    report = report_segmented_outcomes(_request())

    for label, size in (("small", 3), ("mid", 3), ("large", 2)):
        segment = _segment(report, MARKET_CAPITALISATION_AXIS, label)
        assert segment.capability.sample_size == size
        assert segment.capability.smallest_attainable_p_value == 2.0 ** (1 - size)
        assert segment.capability.most_permissive_critical_value == RATE
        assert segment.capability.can_ever_reject is False
        assert "could not have been a discovery on any data whatsoever" in segment.capability.reason


def test_a_bucket_whose_floor_lands_exactly_on_the_line_is_admitted_and_not_missed() -> None:
    """`<=` and not `<`, the boundary `V2-P5-007`'s step-up makes everywhere else.

    Four observations floor at `2**-3` and the most permissive critical value here is exactly
    `7 * 0.125 / 7`. An implementation comparing with `<` calls this bucket incapable.
    """
    report = report_segmented_outcomes(_request())

    for axis_id, label in ((INDUSTRY_AXIS, "banks"), (MARKET_REGIME_AXIS, "bull")):
        segment = _segment(report, axis_id, label)
        assert segment.capability.sample_size == 4
        assert segment.capability.smallest_attainable_p_value == 0.125
        assert segment.capability.most_permissive_critical_value == 0.125
        assert segment.capability.can_ever_reject is True


def test_the_report_counts_how_many_of_its_rows_could_ever_have_rejected() -> None:
    """Four of seven, so a reader sees the study's resolution beside its verdicts."""
    report = report_segmented_outcomes(_request())

    assert report.hypotheses_that_could_ever_reject == 4
    assert sum(axis.segments_that_could_ever_reject for axis in report.axes) == 4


def test_the_capability_verdict_is_the_comparison_and_never_a_supplied_flag() -> None:
    """Reachable from a document, which is how a stored report is read back."""
    with pytest.raises(ValidationError):
        SegmentCapability(
            sample_size=2,
            smallest_attainable_p_value=0.5,
            most_permissive_critical_value=0.125,
            can_ever_reject=True,
            reason="a flag that disagrees with its own two numbers",
        )


def test_a_sample_size_below_one_has_no_attainable_floor_to_report() -> None:
    with pytest.raises(SegmentedReportingError):
        smallest_attainable_p_value(0, bootstrap_samples=1000)


# --- a bucket too small to test is a named absence, not a number -------------------------------


def test_a_bucket_of_one_keeps_its_columns_gets_a_named_absence_and_leaves_the_family() -> None:
    """`V2-P5-008`'s floor arriving one level up, where the buckets are smaller.

    The bucket is still reported on its axis -- dropping it would make the axis look like a cut
    that never produced it -- and it is not a hypothesis, so `reported_hypotheses` is one below
    the bucket count.
    """
    axes = (_axis(LIQUIDITY_AXIS, ("thin",) + ("liquid",) * 7),)
    report = report_segmented_outcomes(_request(axes=axes, declared_family_size=2))

    thin = _segment(report, LIQUIDITY_AXIS, "thin")
    assert thin.statistics.sample_size == 1
    assert thin.statistics.net_active_return == WIDE_NETS[0]
    assert thin.statistics.interval is None
    assert thin.statistics.test is None
    assert thin.statistics.absence_reason is not None
    assert thin.capability.can_ever_reject is False
    assert thin.capability.smallest_attainable_p_value is None
    assert str(MINIMUM_INTERVAL_SAMPLE_SIZE) in thin.capability.reason

    assert len(report.axes[0].segments) == 2
    assert report.segment_hypotheses == 2
    assert report.statistics.multiple_testing.reported_hypotheses == 1
    assert report.statistics.untested_cohorts == (f"{LIQUIDITY_AXIS}:thin",)
    assert report.axes[0].testable_segments == 1


# --- one regime is not multiple regimes --------------------------------------------------------


def test_a_run_whose_evidence_lies_in_one_regime_does_not_claim_to_span_two() -> None:
    """The claim `V2-P5-009` asks for is about the evidence, so it is measured."""
    axes = (_axis(MARKET_REGIME_AXIS, ("bull",) * 8),)
    report = report_segmented_outcomes(_request(axes=axes, declared_family_size=1))

    coverage = report.regime_coverage
    assert coverage.declared is True
    assert coverage.regimes_observed == 1
    assert coverage.regimes_testable == 1
    assert coverage.spans_multiple_regimes is False
    assert "cannot support a claim of regime robustness" in coverage.reason


def test_a_run_across_two_regimes_says_it_spans_them() -> None:
    report = report_segmented_outcomes(_request())

    coverage = report.regime_coverage
    assert coverage.regimes_observed == 2
    assert coverage.regimes_testable == 2
    assert coverage.spans_multiple_regimes is True


def test_a_regime_whose_bucket_is_too_small_to_test_does_not_count_as_spanned() -> None:
    """A regime contributing no out-of-sample test contributes no regime evidence."""
    axes = (_axis(MARKET_REGIME_AXIS, ("bear",) + ("bull",) * 7),)
    report = report_segmented_outcomes(_request(axes=axes, declared_family_size=2))

    coverage = report.regime_coverage
    assert coverage.regimes_observed == 2
    assert coverage.regimes_testable == 1
    assert coverage.spans_multiple_regimes is False


def test_a_report_with_no_regime_axis_says_it_measured_no_regimes() -> None:
    """Silence would let a fold count imply regime robustness nobody measured."""
    axes = (_axis(INDUSTRY_AXIS, WIDE_INDUSTRY),)
    report = report_segmented_outcomes(_request(axes=axes, declared_family_size=2))

    coverage = report.regime_coverage
    assert coverage.declared is False
    assert coverage.regimes_observed == 0
    assert coverage.spans_multiple_regimes is False
    assert "never infers one" in coverage.reason


# --- the three baselines, side by side ---------------------------------------------------------


def _benchmark(kind: str, nets: tuple[float, ...], *, identifier: str) -> BenchmarkCohort:
    return BenchmarkCohort(
        benchmark_id=identifier,
        kind=kind,  # type: ignore[arg-type]
        definition=f"the caller's declared {kind}, same universe and same rebalance days",
        results=_results(nets, signal=identifier),
    )


def test_the_three_baselines_the_row_names_are_all_reportable_side_by_side() -> None:
    """`等权基线、naive factor、v1 基线三者并列` -- all three, in one family."""
    assert set(SEGMENT_AXES) == {
        INDUSTRY_AXIS,
        MARKET_CAPITALISATION_AXIS,
        LIQUIDITY_AXIS,
        MARKET_REGIME_AXIS,
    }
    benchmarks = (
        _benchmark("equal-weight-baseline", PAIRED_BENCHMARK_NETS, identifier="equal-weight"),
        _benchmark("naive-factor", PAIRED_BENCHMARK_NETS, identifier="naive"),
        _benchmark("v1-baseline", PAIRED_BENCHMARK_NETS, identifier="v1"),
    )
    report = report_segmented_outcomes(
        _request(
            nets=PAIRED_STRATEGY_NETS,
            axes=(_axis(INDUSTRY_AXIS, ("banks",) * 4),),
            benchmarks=benchmarks,
            declared_family_size=7,
        )
    )

    assert [item.kind for item in report.benchmarks] == [
        "equal-weight-baseline",
        "naive-factor",
        "v1-baseline",
    ]
    assert report.benchmark_hypotheses == 6
    assert report.statistics.multiple_testing.reported_hypotheses == 7


def test_a_paired_benchmark_publishes_an_inference_neither_arm_could_have_given() -> None:
    """The column is redundant and the inference is not, and both halves are asserted.

    `fmean(a - b)` equals `fmean(a) - fmean(b)` to the last bit on any paired dyadic sample, so
    the difference *column* tells a reader nothing the two columns beside it did not. The
    difference *test* is a different number entirely: each arm alone is unremarkable at `0.5`
    and `1.0`, and the paired difference is `0.125`, the most a four-observation sample admits.
    """
    benchmark = _benchmark(
        "equal-weight-baseline", PAIRED_BENCHMARK_NETS, identifier="equal-weight"
    )
    report = report_segmented_outcomes(
        _request(
            nets=PAIRED_STRATEGY_NETS,
            axes=(_axis(INDUSTRY_AXIS, ("banks",) * 4),),
            benchmarks=(benchmark,),
            declared_family_size=3,
        )
    )
    comparison = report.benchmarks[0]
    assert comparison.comparison_absence_reason is None
    assert comparison.difference is not None
    assert comparison.difference_capability is not None

    strategy_mean = statistics.fmean(PAIRED_STRATEGY_NETS)
    benchmark_mean = statistics.fmean(PAIRED_BENCHMARK_NETS)
    assert comparison.difference.net_active_return == PAIRED_DIFFERENCE
    assert comparison.difference.net_active_return == strategy_mean - benchmark_mean

    strategy = _segment(report, INDUSTRY_AXIS, "banks")
    assert strategy.statistics.test is not None
    assert comparison.statistics.test is not None
    assert strategy.statistics.test.p_value == 0.5
    assert comparison.statistics.test.p_value == 1.0
    assert comparison.difference.test is not None
    assert comparison.difference.test.p_value == 0.125
    assert comparison.difference_capability.can_ever_reject is True


def test_an_unpairable_benchmark_publishes_no_difference_and_says_why() -> None:
    """Two equally long lists are not a pairing, and the windows are what decide it."""
    benchmark = BenchmarkCohort(
        benchmark_id="equal-weight",
        kind="equal-weight-baseline",
        definition="the same baseline measured over windows that do not line up",
        results=tuple(
            _result(index=index, net=net, day=index + 50, signal="equal-weight")
            for index, net in enumerate(PAIRED_BENCHMARK_NETS)
        ),
    )
    report = report_segmented_outcomes(
        _request(
            nets=PAIRED_STRATEGY_NETS,
            axes=(_axis(INDUSTRY_AXIS, ("banks",) * 4),),
            benchmarks=(benchmark,),
            declared_family_size=2,
        )
    )
    comparison = report.benchmarks[0]

    assert comparison.difference is None
    assert comparison.difference_cohort_id is None
    assert comparison.difference_capability is None
    assert comparison.comparison_absence_reason is not None
    assert "not the mean of the differences" in comparison.comparison_absence_reason
    assert report.benchmark_hypotheses == 1


def test_a_benchmark_of_a_different_length_is_not_truncated_into_a_pairing() -> None:
    """A difference over the rows that happened to line up is a different study."""
    benchmark = _benchmark("naive-factor", PAIRED_BENCHMARK_NETS[:3], identifier="naive")
    report = report_segmented_outcomes(
        _request(
            nets=PAIRED_STRATEGY_NETS,
            axes=(_axis(INDUSTRY_AXIS, ("banks",) * 4),),
            benchmarks=(benchmark,),
            declared_family_size=2,
        )
    )

    assert report.benchmarks[0].difference is None
    assert report.benchmarks[0].comparison_absence_reason is not None


# --- refusals, determinism and the registry ----------------------------------------------------


def test_a_separator_inside_a_label_is_refused_rather_than_allowed_to_collide() -> None:
    """`industry:a` and label `b` would otherwise share a cohort id with axis `industry` and
    label `a:b`, and two buckets would answer to one q-value."""
    with pytest.raises(ValidationError):
        _axis(INDUSTRY_AXIS, ("banks:large",) * 8)
    with pytest.raises(ValidationError):
        _axis("industry:cut", ("banks",) * 8)


def test_an_empty_label_is_refused_rather_than_reported_as_a_bucket() -> None:
    with pytest.raises(ValidationError):
        _axis(INDUSTRY_AXIS, ("",) + ("banks",) * 7)


def test_an_axis_that_labels_only_some_of_the_results_is_refused() -> None:
    """A short label list would silently drop the unlabelled results out of that cut."""
    with pytest.raises(ValidationError) as error:
        _request(axes=(_axis(INDUSTRY_AXIS, ("banks",) * 5),))

    assert "silently drops the rest" in str(error.value)


def test_two_axes_with_one_identifier_are_refused() -> None:
    with pytest.raises(ValidationError):
        _request(
            axes=(_axis(INDUSTRY_AXIS, WIDE_INDUSTRY), _axis(INDUSTRY_AXIS, WIDE_REGIME)),
        )


def test_a_benchmark_that_repeats_an_axis_identifier_is_refused() -> None:
    """They share one cohort identifier space and one of them would overwrite the other."""
    with pytest.raises(ValidationError):
        _request(
            axes=(_axis(INDUSTRY_AXIS, WIDE_INDUSTRY),),
            benchmarks=(
                _benchmark("v1-baseline", PAIRED_BENCHMARK_NETS, identifier=INDUSTRY_AXIS),
            ),
        )


def test_the_same_request_reports_the_same_numbers_twice() -> None:
    """One seed, one answer -- the bootstrap is the only randomness and it is declared."""
    first = report_segmented_outcomes(_request())
    second = report_segmented_outcomes(_request())

    assert segmented_report_view(first) == segmented_report_view(second)


def test_the_view_carries_the_one_family_the_capability_and_the_label_definitions() -> None:
    """The three things a bare table of segment returns would have left implicit."""
    benchmark = _benchmark(
        "equal-weight-baseline", PAIRED_BENCHMARK_NETS, identifier="equal-weight"
    )
    report = report_segmented_outcomes(
        _request(
            nets=PAIRED_STRATEGY_NETS,
            axes=(_axis(INDUSTRY_AXIS, ("banks",) * 4),),
            benchmarks=(benchmark,),
            declared_family_size=3,
        )
    )
    view = segmented_report_view(report)

    assert view["family"]["family_size"] == 3
    assert view["family"]["reported_hypotheses"] == 3
    assert view["declared_family_size"] == 3
    assert view["axes"][0]["definition"].startswith("the caller's declared industry cut")
    assert view["axes"][0]["source"] == "declared by the caller"
    assert view["axes"][0]["segments"][0]["capability"]["can_ever_reject"] is True
    assert view["benchmarks"][0]["kind"] == "equal-weight-baseline"
    assert view["benchmarks"][0]["difference"]["net_active_return"] == PAIRED_DIFFERENCE
    assert view["regime_coverage"]["declared"] is False
    assert len(view["limitations"]) == len(KNOWN_SEGMENTED_REPORTING_LIMITATIONS)


def test_the_registry_names_every_limitation_this_module_declares() -> None:
    """Equality rather than membership, the form every registry in this repository has."""
    assert {limitation.code for limitation in KNOWN_SEGMENTED_REPORTING_LIMITATIONS} == {
        "every_segment_label_is_declared_by_the_caller_and_nothing_here_can_check_one",
        "a_segment_axis_is_whatever_the_caller_declared_and_the_cuts_not_shown_are_invisible",
        "the_buckets_of_different_axes_hold_the_same_results_and_are_not_independent_tests",
        "can_ever_reject_is_about_this_familys_resolution_and_not_about_the_strategy",
        "the_regime_coverage_row_counts_declared_labels_and_never_validates_the_calendar",
        "a_benchmark_is_paired_on_its_windows_and_two_results_can_share_a_window_by_accident",
        "the_paired_difference_inherits_every_assumption_the_sign_flip_test_already_made",
    }
