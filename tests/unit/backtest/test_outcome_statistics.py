"""Gross beside net, the cost between them, and an interval that admits what it is.

`V2-P5-008` asks for gross and net side by side, cost drag as its own column, confidence
intervals and sample counts. The first three are exact arithmetic and are asserted with `==`; the
fourth is not arithmetic at all, and most of this module is about the difference.

## The corpus is closed-form and every arm separates something

Every figure is a dyadic rational, so `math.fsum` is correctly rounded onto an exactly
representable value and each identity below holds to the last bit.

- **`alpha`** -- three held decisions, returns `0.25`, `0.125`, `0.375` against a benchmark of
  `2**-4` and a cost of `2**-7` apiece. Gross `0.1875`, drag `-0.0078125`, net `0.1796875`, and
  `gross + drag == net` exactly. The three net returns are in arithmetic progression with step
  `0.125`, which is why the bootstrap's resample means take **exactly seven** distinct values:
  the sums run from `3 * 0.0546875` to `3 * 0.3046875` in steps of `0.125`, and `0.75 / 0.125`
  is six.
- **`beta`** -- three decisions whose *gross* returns are `0.0625`, `-0.0625` and `0.125`, and
  whose net returns are those less the same `2**-7` cost. Its exact sign-flip p-value is `6/8`,
  against `alpha`'s `2/8`, and under a family of two at rate `0.5`
  that is precisely the boundary case: `alpha`'s q-value is `0.5` and is rejected on `<=`, while
  `beta`'s is `0.75` and is not.
- **`constant`** -- three identical returns. Its interval has **zero width** and
  `distinct_bootstrap_means` is `1`. That is not a precise estimate; it is
  `the_percentile_bootstrap_interval_cannot_reach_outside_the_observed_sample` made visible, and
  it is here so the limitation is a measurement rather than a sentence.
- **`solitary`** -- one decision. Its five return columns are exact and it has no interval, no
  p-value and a named absence, and it is **outside** the family: a hypothesis nobody tested is
  not a hypothesis that failed to reject.
- **`unrounded`** -- the one arm that is deliberately *not* dyadic, and the only one that can
  tell a measured `net` column from one derived as `gross + drag`. On exact inputs the two are
  bit-identical, so every other arm here is blind to the difference; on these ordinary
  four-decimal returns the three roundings fail to cancel and the two differ by one unit in the
  last place.
- **`GRANULAR_VALUES`** -- five geometric points, used only for the interval. `alpha`'s three
  are an arithmetic progression whose resample means collapse onto seven values, so consecutive
  order statistics around the 2.5th percentile coincide and the percentile *index* is invisible
  to every assertion over that corpus. A mutation sweep measured exactly that, and these five
  are what makes `means[24]` and `means[25]` different numbers.

One arm separates nothing on its own. `alpha` alone passes for an implementation that derives net
from gross and drag rather than measuring it; `constant` alone passes for one that publishes an
interval at any sample size; `solitary` alone passes for one that never controls anything.
"""

import math
import statistics
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.event_study import (
    EventReturnWindow,
    EventStudy,
    EventStudyRequest,
)
from openalpha_cn.backtest.outcome_statistics import (
    EXACT_SIGN_FLIP_LIMIT,
    KNOWN_OUTCOME_STATISTICS_LIMITATIONS,
    MINIMUM_INTERVAL_SAMPLE_SIZE,
    CohortStatistics,
    OutcomeCohort,
    OutcomeStatisticsError,
    OutcomeStatisticsRequest,
    percentile_bootstrap_interval,
    report_outcome_statistics,
    sign_flip_test,
)
from openalpha_cn.domain.validation import AttributionTerm, ValidationResult

BENCHMARK: Final[float] = 0.0625
"""`2**-4`, the same benchmark `tests/unit/backtest/test_validation.py` uses."""

COST: Final[float] = 0.0078125
"""`2**-7`, likewise."""

ALPHA_REALIZED: Final[tuple[float, float, float]] = (0.25, 0.125, 0.375)
"""`2**-2`, `2**-3`, `3 * 2**-3`."""

ALPHA_GROSS: Final[float] = 0.1875
"""`(0.1875 + 0.0625 + 0.3125) / 3`, and `0.5625 / 3` is exact."""

ALPHA_DRAG: Final[float] = -0.0078125
"""Every decision paid the same cost, so the mean drag is that cost negated."""

ALPHA_NET: Final[float] = 0.1796875
"""`(0.1796875 + 0.0546875 + 0.3046875) / 3`, and `0.5390625 / 3` is exact."""

ALPHA_NET_VALUES: Final[tuple[float, float, float]] = (0.1796875, 0.0546875, 0.3046875)
"""In arithmetic progression with step `0.125`, which fixes the resample grid at seven values."""

ALPHA_P_VALUE: Final[float] = 0.25
"""`2/8`: of the eight sign patterns only the observed one and its negation reach `abs(sum)`."""

BETA_REALIZED: Final[tuple[float, float, float]] = (0.125, 0.0, 0.1875)
"""Against the same benchmark these give *gross* `0.0625`, `-0.0625` and `0.125` -- one loser
among three, where `alpha` has none."""

BETA_NET_VALUES: Final[tuple[float, float, float]] = (0.0546875, -0.0703125, 0.1171875)
"""`BETA_REALIZED` less `BENCHMARK` and `COST`, exact in binary at every step."""

BETA_P_VALUE: Final[float] = 0.75
"""`6/8`. Recomputed below from the corpus rather than trusted, in
`test_the_exact_sign_flip_p_value_is_a_rational_over_two_to_the_n`."""

CONSTANT_REALIZED: Final[tuple[float, float, float]] = (0.25, 0.25, 0.25)

GRANULAR_VALUES: Final[tuple[float, ...]] = (0.25, 0.125, 0.0625, 0.03125, 0.015625)
"""`2**-2` down to `2**-6`: five points whose resample sums do **not** collide.

`ALPHA_NET_VALUES` is an arithmetic progression, so its thousand resample means take only seven
distinct values and consecutive order statistics near the 2.5th percentile are equal -- which
makes the percentile *index* invisible to any assertion over it. These five are geometric, the
resample means take 56 distinct values, and `means[24]` and `means[25]` are different numbers.
"""

RATE: Final[float] = 0.5
"""`2**-1`. Absurd as a false-discovery rate and chosen so `1 * 0.5 / 2` is `0.25` exactly."""

START: Final[datetime] = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def _result(
    *,
    cohort: str,
    index: int,
    realized: float,
    benchmark: float = BENCHMARK,
    cost: float = COST,
) -> ValidationResult:
    """One held-arm result: the cost term, and the whole selection return unexplained.

    Exactly the shape `OutcomeValidator._attribute` produces for `final_action == "watch"`
    since `V2-P5-005` -- one measured term and a residual -- so this corpus is a corpus of
    results the shipped validator can actually emit.
    """
    return ValidationResult(
        signal_id=cohort,
        decision_id=f"{cohort}-{index}",
        observation_start=START,
        observation_end=START + timedelta(days=5),
        realized_return=realized,
        benchmark_return=benchmark,
        transaction_cost=cost,
        attribution=(
            AttributionTerm(category="rule", name="transaction-cost", contribution=-cost),
        ),
        unexplained_return=realized - benchmark,
        confidence=0.5,
    )


def _cohort(cohort_id: str, realized: tuple[float, ...]) -> OutcomeCohort:
    return OutcomeCohort(
        cohort_id=cohort_id,
        results=tuple(
            _result(cohort=cohort_id, index=index, realized=value)
            for index, value in enumerate(realized, start=1)
        ),
    )


def _request(
    cohorts: tuple[OutcomeCohort, ...],
    *,
    family_size: int,
    false_discovery_rate: float = RATE,
    dependence: str = "independent-or-positively-dependent",
) -> OutcomeStatisticsRequest:
    return OutcomeStatisticsRequest(
        cohorts=cohorts,
        family_size=family_size,
        false_discovery_rate=false_discovery_rate,
        dependence=dependence,  # type: ignore[arg-type]
    )


def _named(report, cohort_id: str):
    return next(cohort for cohort in report.cohorts if cohort.cohort_id == cohort_id)


# --- gross, net, and the cost that is the whole difference between them ------------------------


def test_gross_net_and_cost_drag_are_three_columns_that_reconcile_exactly() -> None:
    """The row's first two asks, and the identity that makes the third column checkable.

    `cost_drag` is its own column rather than a subtraction the reader performs. It is asserted
    against `gross + drag == net` **exactly**, which is only meaningful because all three are
    measured independently: a `net` derived as `gross + drag` would satisfy this for any
    arithmetic whatever, which is the free variable `V2-P5-005` took out of the attribution.
    """
    report = report_outcome_statistics(_request((_cohort("alpha", ALPHA_REALIZED),), family_size=1))
    alpha = _named(report, "alpha")

    assert alpha.gross_active_return == ALPHA_GROSS
    assert alpha.cost_drag == ALPHA_DRAG
    assert alpha.net_active_return == ALPHA_NET
    assert alpha.gross_active_return + alpha.cost_drag == alpha.net_active_return
    assert alpha.sample_size == 3


def test_the_cost_drag_column_moves_when_the_cost_does_and_the_gross_column_does_not() -> None:
    """A column that never moves is a column that measures nothing.

    Doubling every transaction cost must leave `gross_active_return` bit-identical and move
    `cost_drag` and `net_active_return` by exactly the same amount. An implementation that
    reported the cost inside gross, or that reported `cost_drag` as a constant, fails one of
    the three assertions here.
    """
    dearer = OutcomeCohort(
        cohort_id="alpha",
        results=tuple(
            _result(cohort="alpha", index=index, realized=value, cost=COST * 2)
            for index, value in enumerate(ALPHA_REALIZED, start=1)
        ),
    )
    report = report_outcome_statistics(_request((dearer,), family_size=1))
    alpha = _named(report, "alpha")

    assert alpha.gross_active_return == ALPHA_GROSS
    assert alpha.cost_drag == ALPHA_DRAG * 2
    assert alpha.net_active_return == ALPHA_NET - COST


def test_the_unexplained_residual_survives_aggregation_rather_than_being_dropped() -> None:
    """`V2-P5-006`'s defect, one level up, refused.

    That issue found `unexplained_return` computed per decision and then dropped on the way to
    the product surface. A cohort report that aggregated realized, benchmark and cost and left
    the residual behind would put the same number back in the same hole. On the held arm the
    residual **is** the whole selection return, so it equals `gross_active_return` here and
    exceeds `net_active_return` -- which is exactly the reading `KNOWN_ATTRIBUTION_LIMITATIONS`
    records for one decision, arriving over three.
    """
    report = report_outcome_statistics(_request((_cohort("alpha", ALPHA_REALIZED),), family_size=1))
    alpha = _named(report, "alpha")

    assert alpha.unexplained_return == ALPHA_GROSS
    assert alpha.unexplained_return > alpha.net_active_return


# --- the interval says what it assumed, and what it cannot do ---------------------------------


def test_the_interval_is_a_named_percentile_bootstrap_with_its_seed_and_sample_count() -> None:
    """An interval nobody can reproduce is not evidence.

    ADR-0003 rules out a t-quantile, so the model is declared instead of assumed: `method`,
    `confidence_level`, `bootstrap_samples`, `random_seed` and the resolution the resampling
    actually achieved are all on the face of the interval.
    """
    report = report_outcome_statistics(_request((_cohort("alpha", ALPHA_REALIZED),), family_size=1))
    interval = _named(report, "alpha").interval
    assert interval is not None

    assert interval.method == "percentile-bootstrap"
    assert interval.confidence_level == 0.95
    assert interval.bootstrap_samples == 1000
    assert interval.random_seed == 0
    assert interval.lower <= ALPHA_NET <= interval.upper


def test_the_interval_never_reaches_outside_the_observed_returns() -> None:
    """Every resample mean is a convex combination, so both endpoints are inside `[min, max]`.

    On this corpus the lower endpoint **is** the sample minimum, which is the coarseness at
    `n = 3` showing itself rather than a coincidence: the resample means take seven values and
    the 2.5th percentile of a thousand draws lands on the smallest of them.
    """
    report = report_outcome_statistics(_request((_cohort("alpha", ALPHA_REALIZED),), family_size=1))
    interval = _named(report, "alpha").interval
    assert interval is not None

    assert interval.lower == min(ALPHA_NET_VALUES)
    assert interval.upper <= max(ALPHA_NET_VALUES)
    assert interval.distinct_bootstrap_means == 7


def test_three_identical_returns_get_an_interval_of_zero_width_and_it_says_so() -> None:
    """`the_percentile_bootstrap_interval_cannot_reach_outside_the_observed_sample`, measured.

    Every resample of a constant sample is that constant, so the "95%" interval is a point and
    `distinct_bootstrap_means` is `1`. Nothing here pretends otherwise, and the column that
    reports the grid size is what lets a reader see it without recomputing the bootstrap.
    """
    report = report_outcome_statistics(
        _request((_cohort("constant", CONSTANT_REALIZED),), family_size=1)
    )
    interval = _named(report, "constant").interval
    assert interval is not None

    assert interval.lower == interval.upper
    assert interval.distinct_bootstrap_means == 1


@pytest.mark.parametrize("values", [ALPHA_NET_VALUES, GRANULAR_VALUES])
def test_the_interval_is_bit_identical_to_the_one_event_study_publishes(
    values: tuple[float, ...],
) -> None:
    """One repository, one percentile convention, held together by an executable identity.

    `backtest/event_study.py` has published a deterministic percentile bootstrap since P0. A
    second one computed slightly differently would be a second kind of interval wearing one
    name, so this drives the same sample through both faces and requires the endpoints to agree
    **exactly** -- same seed, same draw order, same index arithmetic.

    **`GRANULAR_VALUES` is here because `ALPHA_NET_VALUES` cannot see the index arithmetic at
    all**, which a mutation sweep measured rather than argued. `int((alpha / 2) * samples)` and
    `int((alpha / 2) * (samples - 1))` are `25` and `24`, and on a three-point sample whose
    resample means take seven values the 25th and the 24th order statistics are the *same
    number*, so both conventions publish the same interval and this test stays green under
    either. Five points spread over `2**-2 .. 2**-6` give 56 distinct resample means, and there
    `means[24]` is `0.03125` against `means[25]`'s `0.034375`.
    """
    study = EventStudy().analyze(
        EventStudyRequest(
            windows=tuple(
                EventReturnWindow(
                    event_id=f"e{index}",
                    asset_returns=(value,),
                    benchmark_returns=(0.0,),
                )
                for index, value in enumerate(values, start=1)
            ),
            bootstrap_samples=1000,
            confidence_level=0.95,
            random_seed=0,
        )
    )
    mine = percentile_bootstrap_interval(
        values, confidence_level=0.95, bootstrap_samples=1000, random_seed=0
    )

    assert (mine.lower, mine.upper) == (study.bootstrap_lower, study.bootstrap_upper)


def test_the_lower_endpoint_is_the_order_statistic_the_percentile_convention_names() -> None:
    """The index itself, on a sample fine-grained enough for one step to be visible.

    A convention nobody can restate is a convention that drifts. `0.03125` is `means[24]` --
    `int(0.025 * 999)` -- and `0.034375` is `means[25]`, the answer the other reading of the
    same sentence gives.
    """
    interval = percentile_bootstrap_interval(
        GRANULAR_VALUES, confidence_level=0.95, bootstrap_samples=1000, random_seed=0
    )

    assert interval.lower == 0.03125
    assert interval.distinct_bootstrap_means == 56


def test_a_cohort_of_one_gets_its_columns_a_named_absence_and_no_place_in_the_family() -> None:
    """The refusal `V2-P5-008` needs, because a zero-width interval is not an interval.

    The five return columns are exact at `n = 1` and are published. The interval and the test
    are `None`, `absence_reason` says why in a sentence, and the cohort is **outside** the
    controlled family -- so the two testable cohorts beside it are controlled against a family
    of two rather than three.
    """
    report = report_outcome_statistics(
        _request(
            (
                _cohort("alpha", ALPHA_REALIZED),
                _cohort("beta", BETA_REALIZED),
                _cohort("solitary", (0.25,)),
            ),
            family_size=2,
        )
    )
    solitary = _named(report, "solitary")

    assert solitary.sample_size == 1
    assert solitary.net_active_return == ALPHA_NET_VALUES[0]
    assert solitary.interval is None
    assert solitary.test is None
    assert solitary.absence_reason is not None
    assert str(MINIMUM_INTERVAL_SAMPLE_SIZE) in solitary.absence_reason
    assert report.untested_cohorts == ("solitary",)
    assert report.tested_hypotheses == 2
    assert report.multiple_testing.reported_hypotheses == 2
    assert report.verdict_for("solitary") is None


def test_a_family_with_nothing_testable_in_it_is_refused_rather_than_controlled() -> None:
    """An empty control is a report that says nothing while looking like one that says little."""
    with pytest.raises(OutcomeStatisticsError) as refusal:
        report_outcome_statistics(_request((_cohort("solitary", (0.25,)),), family_size=1))

    assert str(MINIMUM_INTERVAL_SAMPLE_SIZE) in str(refusal.value)


def test_a_cohort_of_two_can_never_be_a_discovery_at_a_conventional_rate() -> None:
    """What the floor of two buys, measured rather than implied.

    An exact sign-flip test over `n` observations has `2**n` patterns and the observed pattern
    and its negation are always hits, so the smallest p-value attainable is `2**(1 - n)`. At
    `n = 2` that is `0.5` -- so the two most one-sided returns imaginable produce the same
    p-value as two that cancel, and no false-discovery rate anyone would set can reject either.
    The cohort is still reported, with its columns, its interval and its sample count, because
    every one of those is honest; what would not be honest is letting `sample_size` off the row.
    """
    stellar = report_outcome_statistics(_request((_cohort("pair", (2.0, 3.0)),), family_size=1))
    hopeless = report_outcome_statistics(
        _request((_cohort("pair", (0.0625, 0.0625)),), family_size=1)
    )

    for report in (stellar, hopeless):
        cohort = _named(report, "pair")
        assert cohort.sample_size == MINIMUM_INTERVAL_SAMPLE_SIZE
        assert cohort.test is not None
        assert cohort.test.p_value == 2 ** (1 - MINIMUM_INTERVAL_SAMPLE_SIZE)
        assert cohort.interval is not None

    assert _named(stellar, "pair").net_active_return > _named(hopeless, "pair").net_active_return
    for rate in (0.01, 0.05, 0.10, 0.25):
        controlled = report_outcome_statistics(
            _request((_cohort("pair", (2.0, 3.0)),), family_size=1, false_discovery_rate=rate)
        )
        assert controlled.multiple_testing.discoveries == 0


# --- the p-value, and the family it is corrected in -------------------------------------------


def test_the_exact_sign_flip_p_value_is_a_rational_over_two_to_the_n() -> None:
    """Enumerated rather than sampled at this size, so the corpus asserts it with `==`.

    Recomputed here from the sign patterns rather than quoted, so the constant above is a
    prediction this test either confirms or falsifies.
    """
    for values, expected in ((ALPHA_NET_VALUES, ALPHA_P_VALUE), (BETA_NET_VALUES, BETA_P_VALUE)):
        observed = abs(math.fsum(values))
        hits = sum(
            1
            for mask in range(2 ** len(values))
            if abs(
                math.fsum(
                    (-value if (mask >> position) & 1 else value)
                    for position, value in enumerate(values)
                )
            )
            >= observed
        )
        assert hits / 2 ** len(values) == expected

        measured = sign_flip_test(values, bootstrap_samples=1000, random_seed=0)
        assert measured.p_value == expected
        assert measured.exact is True
        assert measured.sign_patterns == 2 ** len(values)
        assert measured.random_seed is None


def test_a_sample_above_the_enumeration_limit_is_sampled_and_says_so() -> None:
    """Above `EXACT_SIGN_FLIP_LIMIT` the patterns are drawn, and the p-value cannot read zero.

    Phipson and Smyth's `(1 + hits) / (1 + draws)`: the observed pattern is not guaranteed to be
    among the draws, and a sampled `0.0` would claim a certainty a finite number of draws cannot
    support. The floor `1 / 1001` is what that guarantee looks like.
    """
    values = tuple(0.0625 * (index + 1) for index in range(EXACT_SIGN_FLIP_LIMIT + 1))
    measured = sign_flip_test(values, bootstrap_samples=1000, random_seed=0)

    assert measured.exact is False
    assert measured.sign_patterns == 1000
    assert measured.random_seed == 0
    assert measured.p_value >= 1 / 1001


def test_the_boundary_family_rejects_alpha_on_the_inclusive_comparison_and_not_beta() -> None:
    """The two cohorts' exact p-values land the first one *on* its critical value.

    `1 * 0.5 / 2` is `0.25` and `alpha`'s p-value is `0.25`, so BH's `<=` rejects it and a `<`
    would not; `beta`'s `0.75` is above its own `0.5` either way. The q-values are `0.5` and
    `0.75`, both exact.
    """
    report = report_outcome_statistics(
        _request((_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED)), family_size=2)
    )

    alpha_verdict = report.verdict_for("alpha")
    beta_verdict = report.verdict_for("beta")
    assert alpha_verdict is not None and beta_verdict is not None
    assert (alpha_verdict.p_value, beta_verdict.p_value) == (ALPHA_P_VALUE, BETA_P_VALUE)
    assert alpha_verdict.critical_value == 0.25
    assert (alpha_verdict.q_value, beta_verdict.q_value) == (0.5, 0.75)
    assert (alpha_verdict.rejected, beta_verdict.rejected) == (True, False)
    assert report.multiple_testing.family_size == 2
    assert report.multiple_testing.discoveries == 1


def test_declaring_a_larger_family_withdraws_the_only_discovery() -> None:
    """`V2-P5-007`'s second half reaching the product this report is: same data, same cohorts.

    Six further cohorts were tested and are not reported. Nothing about `alpha` moved, and it
    is no longer a discovery: its critical value falls from `0.25` to `0.0625` and its q-value
    rises from `0.5` to `1.0`. A report that recovered `m` by counting its own rows could not
    produce this answer at all.
    """
    cohorts = (_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED))
    narrow = report_outcome_statistics(_request(cohorts, family_size=2))
    broad = report_outcome_statistics(_request(cohorts, family_size=8))

    assert narrow.multiple_testing.discoveries == 1
    assert broad.multiple_testing.discoveries == 0
    assert broad.multiple_testing.family_size == 8
    assert broad.multiple_testing.withheld_hypotheses == 6
    assert broad.multiple_testing.family_is_complete is False

    for cohort_id in ("alpha", "beta"):
        assert (
            _named(narrow, cohort_id).net_active_return
            == _named(broad, cohort_id).net_active_return
        )

    alpha_broad = broad.verdict_for("alpha")
    assert alpha_broad is not None
    assert alpha_broad.critical_value == 0.0625
    assert alpha_broad.q_value == 1.0
    assert alpha_broad.rejected is False


def test_a_declared_family_below_the_cohorts_actually_tested_is_refused() -> None:
    """The one direction a declaration can be checked in, checked at this face too."""
    with pytest.raises(OutcomeStatisticsError) as refusal:
        report_outcome_statistics(
            _request(
                (_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED)),
                family_size=1,
            )
        )

    assert "1" in str(refusal.value)
    assert "2" in str(refusal.value)


def test_the_p_value_carried_into_the_family_names_the_test_that_produced_it() -> None:
    """`every_p_value_here_is_the_callers_and_none_is_computed_or_checked` needs a name, and
    this is the module that supplies one: the null travels onto the verdict."""
    report = report_outcome_statistics(_request((_cohort("alpha", ALPHA_REALIZED),), family_size=1))
    verdict = report.verdict_for("alpha")
    assert verdict is not None

    assert "sign-flip-randomization" in verdict.test
    assert "symmetric about zero" in verdict.test


def test_a_net_column_derived_from_the_other_two_is_not_the_net_column_this_reports() -> None:
    """The one arm of this corpus that is deliberately **not** dyadic, and why it has to exist.

    Everywhere else here the figures are exact, so `fmean(gross) + fmean(drag)` and
    `fmean(net)` are bit-identical and an implementation that *derived* the net column from
    the other two would pass every assertion in this file. That is the shape of defect this
    repository keeps finding: an assertion that exists and cannot separate the two answers.

    These returns are ordinary four-decimal figures and they are chosen because the three
    roundings do **not** cancel: the derived value is `-0.1282` and the measured one is
    `-0.12819999999999998`, one unit in the last place apart. So the assertion below fails for
    a derived column and holds for a measured one, and
    `gross_net_and_cost_drag_are_three_means_and_the_identity_is_only_as_exact_as_the_inputs`
    stops being a sentence and becomes a measurement.
    """
    realized = (0.2344, -0.3247, -0.1573)
    benchmark = (-0.1637, 0.1239, 0.0774)
    cost = (0.0021, 0.0491, 0.0482)
    cohort = OutcomeCohort(
        cohort_id="unrounded",
        results=tuple(
            _result(
                cohort="unrounded",
                index=index,
                realized=realized[index],
                benchmark=benchmark[index],
                cost=cost[index],
            )
            for index in range(3)
        ),
    )
    report = report_outcome_statistics(_request((cohort,), family_size=1))
    measured = _named(report, "unrounded")

    derived = measured.gross_active_return + measured.cost_drag
    assert derived == -0.1282
    assert measured.net_active_return == -0.12819999999999998
    assert measured.net_active_return != derived
    assert measured.net_active_return == statistics.fmean(
        item.net_active_return for item in cohort.results
    )


# --- determinism, refusals and the registry ---------------------------------------------------


def test_two_runs_of_one_request_agree_to_the_last_bit() -> None:
    """A seeded bootstrap that is not reproducible is a number nobody can check."""
    request = _request(
        (_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED)), family_size=2
    )

    assert (
        report_outcome_statistics(request).model_dump_json()
        == report_outcome_statistics(request).model_dump_json()
    )


def test_the_cohort_order_does_not_move_any_cohorts_interval() -> None:
    """One generator per cohort per purpose, so a reordered request is the same report.

    A single generator threaded through the cohorts would make each cohort's interval depend on
    what preceded it, and a report whose numbers move when the caller sorts its input is not
    reproducible in the sense `random_seed` is there to promise.
    """
    forward = report_outcome_statistics(
        _request((_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED)), family_size=2)
    )
    reversed_ = report_outcome_statistics(
        _request((_cohort("beta", BETA_REALIZED), _cohort("alpha", ALPHA_REALIZED)), family_size=2)
    )

    assert _named(forward, "alpha").interval == _named(reversed_, "alpha").interval
    assert _named(forward, "beta").interval == _named(reversed_, "beta").interval
    assert forward.verdict_for("alpha") == reversed_.verdict_for("alpha")


def test_two_cohorts_under_one_identifier_are_refused() -> None:
    with pytest.raises(ValidationError):
        _request((_cohort("alpha", ALPHA_REALIZED), _cohort("alpha", BETA_REALIZED)), family_size=2)


def test_the_dependence_assumption_has_no_default_at_this_face_either() -> None:
    """The permissive reading must not be the cheapest one to ask for, at every face."""
    with pytest.raises(ValidationError):
        OutcomeStatisticsRequest(
            cohorts=(_cohort("alpha", ALPHA_REALIZED),),
            family_size=1,
            false_discovery_rate=RATE,
        )  # type: ignore[call-arg]


def test_a_report_whose_family_disagrees_with_its_cohorts_cannot_be_constructed() -> None:
    """The join between the columns and the control is a validator, not a convention.

    A document in which a cohort carries a test but the controlled family never named it is
    a report whose q-values belong to a different set of hypotheses than its rows do.
    """
    report = report_outcome_statistics(
        _request((_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED)), family_size=2)
    )
    document = report.model_dump()
    document["multiple_testing"]["verdicts"] = document["multiple_testing"]["verdicts"][:1]
    document["multiple_testing"]["reported_hypotheses"] = 1
    document["multiple_testing"]["withheld_hypotheses"] = 1

    with pytest.raises(ValidationError):
        type(report).model_validate(document)


def test_an_arbitrary_dependence_declaration_changes_the_answer_at_this_face_too() -> None:
    """`H_2 = 1.5`, so the critical values halve and the boundary discovery is withdrawn.

    `alpha`'s p-value is `0.25` and its independent critical value is `0.25` -- exactly on the
    line. Under arbitrary dependence the line is `0.25 / 1.5`, which `0.25` is above, so the
    report's single discovery disappears. That is the assumption doing arithmetic rather than
    being recorded beside it.
    """
    cohorts = (_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED))
    independent = report_outcome_statistics(_request(cohorts, family_size=2))
    arbitrary = report_outcome_statistics(_request(cohorts, family_size=2, dependence="arbitrary"))

    assert independent.multiple_testing.discoveries == 1
    assert arbitrary.multiple_testing.discoveries == 0
    assert arbitrary.multiple_testing.dependence_penalty == 1.5
    assert arbitrary.multiple_testing.dependence == "arbitrary"


def test_a_cohort_with_no_inference_and_no_reason_cannot_be_constructed() -> None:
    """The absence has to be *named*, and that is a contract rule rather than a convention.

    `report_outcome_statistics` always fills `absence_reason` when it withholds an inference, so
    nothing driving the function reaches this branch -- a mutation sweep measured that deleting
    the guard changed no test. It is reachable from a document, which is the path a stored
    report is read back through, and a row that silently carries three nulls is a row that reads
    as "we looked and found nothing" when it means "we did not look".
    """
    with pytest.raises(ValidationError):
        CohortStatistics(
            cohort_id="silent",
            sample_size=1,
            gross_active_return=0.0,
            cost_drag=0.0,
            net_active_return=0.0,
            unexplained_return=0.0,
            interval=None,
            test=None,
            absence_reason=None,
        )


def test_two_cohorts_under_one_identifier_cannot_share_one_verdict() -> None:
    """The count check the id-set check does not subsume, on the document that separates them.

    Cohort identifiers are unique on a *request* and nothing makes them unique on a report read
    back off disk. Two rows called `alpha`, both tested, against a family holding one verdict
    called `alpha`: the id sets are equal -- `{"alpha"} == {"alpha", "alpha"}` -- and only the
    count says that two tested cohorts are being answered by one q-value. A mutation sweep found
    this guard unreachable from every other test in this file, which is how the case was found.
    """
    report = report_outcome_statistics(
        _request((_cohort("alpha", ALPHA_REALIZED), _cohort("beta", BETA_REALIZED)), family_size=2)
    )
    document = report.model_dump()
    document["cohorts"][1]["cohort_id"] = "alpha"
    document["multiple_testing"]["verdicts"] = document["multiple_testing"]["verdicts"][:1]
    document["multiple_testing"]["reported_hypotheses"] = 1
    document["multiple_testing"]["withheld_hypotheses"] = 1

    assert document["multiple_testing"]["verdicts"][0]["hypothesis_id"] == "alpha"
    with pytest.raises(ValidationError):
        type(report).model_validate(document)


def test_the_registry_names_every_limitation_this_module_declares() -> None:
    """Equality rather than membership, the form every registry in this repository has."""
    assert {entry.code for entry in KNOWN_OUTCOME_STATISTICS_LIMITATIONS} == {
        "the_percentile_bootstrap_interval_cannot_reach_outside_the_observed_sample",
        "no_interval_is_published_below_two_observations_because_every_resample_is_the_sample",
        "the_sign_flip_test_assumes_symmetry_about_zero_and_nothing_here_checks_it",
        "the_observations_are_resampled_as_though_they_were_independent_draws",
        "a_cohort_is_whatever_the_caller_grouped_and_the_grouping_is_itself_untested",
        "gross_net_and_cost_drag_are_three_means_and_the_identity_is_only_as_exact_as_the_inputs",
        "the_attribution_terms_are_not_aggregated_because_two_results_need_not_carry_the_same_ones",
    }


def test_the_three_columns_are_measured_independently_rather_than_derived() -> None:
    """`gross_net_and_cost_drag_are_three_means_and_the_identity_is_only_as_exact_as_the_inputs`.

    Each column is its own `fmean` over its own values, so each equals the mean a reader would
    take by hand off the individual results. A `net` derived as `gross + drag` would agree with
    the first assertion and could not agree with the third.
    """
    cohort = _cohort("alpha", ALPHA_REALIZED)
    report = report_outcome_statistics(_request((cohort,), family_size=1))
    alpha = _named(report, "alpha")

    assert alpha.gross_active_return == statistics.fmean(
        item.realized_return - item.benchmark_return for item in cohort.results
    )
    assert alpha.cost_drag == statistics.fmean(-item.transaction_cost for item in cohort.results)
    assert alpha.net_active_return == statistics.fmean(
        item.net_active_return for item in cohort.results
    )
