"""`backtest/power_analysis.py` (`V2-P7-004`): the power of the one-sided test of a mean over
non-overlapping periods, held to the arithmetic the P7 route was decided on (sd 0.15, one-sided
0.05) and to published t quantiles."""

from __future__ import annotations

import math
import re

import pytest

from openalpha_cn.backtest.power_analysis import (
    NOT_DETECTABLE_WORDING,
    PowerError,
    autocorrelation_equivalent_periods,
    by_first_step_level,
    critical_value,
    futility_stop_probability,
    periods_for_power,
    power_of_mean_test,
    power_table_lines,
    power_with_futility,
    smallest_mean_at_power,
    t_quantile,
)

SD = 0.15
LEVEL = 0.05
FORBIDDEN = re.compile(
    r"\b(significant|effective|confirmed|alpha|outperform|passed|failed|proven)", re.IGNORECASE
)


def power(mean: float, periods: float, kind: str = "normal", level: float = LEVEL) -> float:
    return power_of_mean_test(mean=mean, sd=SD, periods=periods, test_level=level, kind=kind)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("periods", "mean", "expected"),
    [
        (36, 0.05, 0.6387600313123356),
        (36, 0.037, 0.43452959661329793),
        (36, 0.10, 0.9907422946265046),
        (56, 0.05, 0.8022219701621711),
        (12, 0.05, 0.3120127868184547),
        (12, 0.10, 0.746830004913777),
    ],
)
def test_the_normal_power_is_the_routes_arithmetic(
    periods: int, mean: float, expected: float
) -> None:
    """Phi(sqrt(K) * mean / sd - z_0.95): K=36 at 0.05 is Phi(2 - 1.6448536) = 0.639."""
    assert power(mean, periods) == pytest.approx(expected, abs=1e-12)


def test_the_normal_answers_to_the_routes_questions() -> None:
    assert critical_value(test_level=LEVEL, periods=36, kind="normal") == pytest.approx(
        1.6448536269514715, abs=1e-12
    )
    assert smallest_mean_at_power(
        power=0.8, sd=SD, periods=36, test_level=LEVEL, kind="normal"
    ) == pytest.approx(0.062161871513109646, abs=1e-12)
    needed = {
        mean: periods_for_power(mean=mean, power=0.8, sd=SD, test_level=LEVEL, kind="normal")
        for mean in (0.037, 0.05, 0.07, 0.10)
    }
    assert needed == {0.037: 102, 0.05: 56, 0.07: 29, 0.10: 14}


@pytest.mark.parametrize(
    ("probability", "df", "expected", "tolerance"),
    [
        (0.95, 1, 6.313752, 1e-4),
        (0.95, 11, 1.795885, 1e-5),
        (0.95, 23, 1.713872, 1e-5),
        (0.95, 35, 1.689572, 1e-5),
        (0.95, 55, 1.673034, 1e-5),
        (0.80, 11, 0.875530, 1e-5),
    ],
)
def test_t_quantiles_are_the_published_ones(
    probability: float, df: int, expected: float, tolerance: float
) -> None:
    assert t_quantile(probability, df) == pytest.approx(expected, abs=tolerance)


def test_the_t_convention_needs_one_more_period_than_the_normal_one_at_ic_005() -> None:
    """With t_{0.95, K-1}: K=12 gives 0.261 (Part E's "about 25%"), K=36 0.622, K=56 0.794 and
    K=57 0.801 -- so 80% at IC 0.05 needs 57 periods, not 56."""
    assert power(0.05, 12, "t") == pytest.approx(0.260701, abs=1e-6)
    assert power(0.05, 36, "t") == pytest.approx(0.621882, abs=1e-6)
    assert power(0.037, 36, "t") == pytest.approx(0.417001, abs=1e-6)
    assert power(0.05, 56, "t") == pytest.approx(0.794292, abs=1e-6)
    assert periods_for_power(mean=0.05, power=0.8, sd=SD, test_level=LEVEL, kind="t") == 57


def test_serial_correlation_shrinks_the_independent_periods() -> None:
    assert autocorrelation_equivalent_periods(periods=36, lag1=0.1) == pytest.approx(
        36 * 0.9 / 1.1, abs=1e-12
    )
    assert autocorrelation_equivalent_periods(periods=36, lag1=0.0) == 36


def test_a_secondary_is_powered_at_the_first_benjamini_yekutieli_step() -> None:
    """q / (m H_m) with m = 3: 0.10 / (3 * 11/6) = 0.0181818..., where K=36 at 0.05 has 0.463."""
    level = by_first_step_level(q=0.10, family_size=3)
    assert level == pytest.approx(0.10 / (3 * (1 + 1 / 2 + 1 / 3)), abs=1e-15)
    assert power(0.05, 36, level=level) == pytest.approx(0.4630162109014593, abs=1e-12)


@pytest.mark.parametrize(
    ("mean", "periods", "threshold", "expected"),
    [
        (0.05, 18, 0.0, 0.07864960352514258),
        (0.0, 18, 0.0, 0.5),
        (0.037, 18, 0.0, 0.1476609610726271),
        (0.0, 24, 0.02, 0.7431854433034379),
        (0.037, 24, 0.02, 0.28937289034228963),
        (0.05, 24, 0.02, 0.16359343889515288),
        (0.10, 24, 0.02, 0.004490335620949417),
    ],
)
def test_the_stop_probability_of_a_rule_on_the_mean_is_hand_computed(
    mean: float, periods: int, threshold: float, expected: float
) -> None:
    """Phi((threshold - mean) / (sd / sqrt(k))): the second block is a G-0-shaped rule (mean IC
    at or below 0.02 over about 24 periods) at four true means."""
    assert futility_stop_probability(
        mean=mean, sd=SD, interim_periods=periods, threshold=threshold
    ) == pytest.approx(expected, abs=1e-12)


def test_an_interim_futility_look_costs_less_than_a_point_of_power_at_ic_005() -> None:
    """One look at K/2 = 18 stopping when the mean is at or below 0: P(continue and the final
    test at K=36 has p <= 0.05). A seeded 200,000-draw simulation gave 0.6312 (not run here)."""
    with_look = power_with_futility(
        mean=0.05,
        sd=SD,
        periods=36,
        interim_periods=18,
        threshold=0.0,
        test_level=LEVEL,
        kind="normal",
    )
    assert with_look == pytest.approx(0.6308415396468372, abs=1e-6)
    assert power_with_futility(
        mean=0.0,
        sd=SD,
        periods=36,
        interim_periods=18,
        threshold=0.0,
        test_level=LEVEL,
        kind="normal",
    ) == pytest.approx(0.04875, abs=1e-6)
    assert power_with_futility(
        mean=0.05,
        sd=SD,
        periods=36,
        interim_periods=18,
        threshold=-10.0,
        test_level=LEVEL,
        kind="normal",
    ) == pytest.approx(power(0.05, 36), abs=1e-6)


def test_the_power_lines_say_not_detectable_is_a_category_of_the_design() -> None:
    lines = power_table_lines(
        periods=36,
        sd=SD,
        test_level=LEVEL,
        means=(0.037, 0.05, 0.1),
        target_power=0.8,
        kind="normal",
    )

    assert lines == (
        "power of a one-sided test of a mean over 36 non-overlapping periods (test level 0.05, "
        "per-period sd 0.15, normal critical value 1.6449; a property of the design, not a "
        "result):",
        "  a true mean of 0.037: probability 0.435 of a one-sided p at or below 0.05; 102 "
        "periods for 0.8",
        "  a true mean of 0.05: probability 0.639 of a one-sided p at or below 0.05; 56 periods "
        "for 0.8",
        "  a true mean of 0.1: probability 0.991 of a one-sided p at or below 0.05; 14 periods "
        "for 0.8",
        "  the smallest mean with probability 0.8: 0.0622",
        f"  {NOT_DETECTABLE_WORDING}",
    )
    assert 'never "no effect"' in NOT_DETECTABLE_WORDING
    assert FORBIDDEN.search("\n".join(lines)) is None


@pytest.mark.parametrize(
    ("call", "said"),
    [
        (lambda: power(0.05, 1), "periods"),
        (
            lambda: power_of_mean_test(
                mean=0.05, sd=0.0, periods=36, test_level=LEVEL, kind="normal"
            ),
            "sd",
        ),
        (lambda: power(0.05, 36, level=0.5), "test level"),
        (lambda: power(0.05, 36, kind="z"), "critical"),
        (lambda: power(math.nan, 36), "finite"),
        (
            lambda: periods_for_power(mean=0.0, power=0.8, sd=SD, test_level=LEVEL, kind="normal"),
            "mean",
        ),
        (
            lambda: smallest_mean_at_power(
                power=1.0, sd=SD, periods=36, test_level=LEVEL, kind="normal"
            ),
            "power",
        ),
        (lambda: autocorrelation_equivalent_periods(periods=36, lag1=1.0), "lag"),
        (
            lambda: power_with_futility(
                mean=0.05,
                sd=SD,
                periods=36,
                interim_periods=36,
                threshold=0.0,
                test_level=LEVEL,
                kind="normal",
            ),
            "interim",
        ),
        (lambda: t_quantile(0.4, 10), "probability"),
        (lambda: t_quantile(0.95, 0.5), "df"),
        (lambda: by_first_step_level(q=0.1, family_size=0), "family"),
    ],
)
def test_a_question_without_an_answer_is_refused_by_name(call: object, said: str) -> None:
    with pytest.raises(PowerError, match=said):
        call()  # type: ignore[operator]


# --- beyond the brief's fixed points: the answers a design must not get wrong silently -----------


def test_a_t_quantile_beyond_the_search_range_is_refused_and_never_clamped() -> None:
    """t_{0.999, 1} is 318.3; a search interval that stops at 64 must say so, not return 64."""
    assert t_quantile(0.995, 1) == pytest.approx(63.656741, abs=1e-3)
    assert t_quantile(0.9999, 3) == pytest.approx(22.2037423, abs=1e-5)
    with pytest.raises(PowerError, match="range"):
        t_quantile(0.999, 1)
    with pytest.raises(PowerError, match="range"):
        critical_value(test_level=1e-9, periods=2, kind="t")


def test_a_test_level_below_float_resolution_is_refused_by_name() -> None:
    with pytest.raises(PowerError, match="test level"):
        critical_value(test_level=1e-17, periods=36, kind="normal")


@pytest.mark.parametrize("kind", ["normal", "t"])
def test_the_periods_for_power_are_the_fewest_that_reach_the_target(kind: str) -> None:
    """Reached at n, not yet reached at n - 1 -- over means and targets, a target below the test
    level included (any n suffices there, so the normal answer is the two-period floor). The
    low-target pairs are the large means only: the t convention climbs one period at a time from
    the floor, and each period is a fresh t quantile."""
    pairs = [(m, t) for m in (0.037, 0.05, 0.10, 0.20) for t in (0.5, 0.8, 0.9)]
    pairs += [(0.10, 0.01), (0.20, 0.01)]
    for mean, target in pairs:
        needed = periods_for_power(
            mean=mean,
            power=target,
            sd=SD,
            test_level=LEVEL,
            kind=kind,  # type: ignore[arg-type]
        )
        assert needed >= 2
        assert power(mean, needed, kind) >= target
        assert needed == 2 or power(mean, needed - 1, kind) < target
    assert periods_for_power(mean=0.05, power=0.01, sd=SD, test_level=LEVEL, kind="normal") == 2


def test_a_count_of_periods_too_large_to_plan_is_refused_not_overflowed() -> None:
    assert periods_for_power(mean=0.002, power=0.8, sd=SD, test_level=LEVEL, kind="normal") == 34777
    assert periods_for_power(mean=0.002, power=0.8, sd=SD, test_level=LEVEL, kind="t") == 34779
    for kind in ("normal", "t"):
        for mean in (0.001, 1e-4, 1e-300):
            with pytest.raises(PowerError, match="periods"):
                periods_for_power(
                    mean=mean,
                    power=0.8,
                    sd=SD,
                    test_level=LEVEL,
                    kind=kind,  # type: ignore[arg-type]
                )


def test_the_futility_integral_is_held_to_an_independent_integration() -> None:
    """A 12-point Gauss-Legendre composite on 2,400 + 3,000 intervals (not run here) gave
    0.9093185375111449 at K=2000 / k=1999 -- the nearest look to the end the fixed grid resolves --
    and 0.6147694087845947 for the t convention at K=36 / k=18."""
    assert power_with_futility(
        mean=0.01,
        sd=SD,
        periods=2000,
        interim_periods=1999,
        threshold=0.0,
        test_level=LEVEL,
        kind="normal",
    ) == pytest.approx(0.9093185375111449, abs=1e-9)
    assert power_with_futility(
        mean=0.05,
        sd=SD,
        periods=36,
        interim_periods=18,
        threshold=0.0,
        test_level=LEVEL,
        kind="t",
    ) == pytest.approx(0.6147694087845947, abs=1e-9)
    # a stop can only remove successes: the look never adds power
    for mean in (0.0, 0.037, 0.05, 0.10):
        for kind in ("normal", "t"):
            assert (
                power_with_futility(
                    mean=mean,
                    sd=SD,
                    periods=36,
                    interim_periods=12,
                    threshold=0.02,
                    test_level=LEVEL,
                    kind=kind,  # type: ignore[arg-type]
                )
                <= power(mean, 36, kind) + 1e-9
            )


def test_a_look_the_integration_grid_cannot_resolve_is_refused_by_name() -> None:
    """Within sqrt(1 - k/K) < 0.01 of the end the inner step is narrower than the grid's: at
    K = 100,000 / k = 99,999 the grid is off by 5e-5, which is not an answer to quote."""
    with pytest.raises(PowerError, match="interim"):
        power_with_futility(
            mean=0.001,
            sd=SD,
            periods=100_000,
            interim_periods=99_999,
            threshold=0.0,
            test_level=LEVEL,
            kind="normal",
        )


def test_a_family_of_one_is_tested_at_q_itself() -> None:
    assert by_first_step_level(q=0.1, family_size=1) == 0.1


def test_the_t_power_lines_name_the_degrees_of_freedom_they_were_computed_on() -> None:
    lines = power_table_lines(
        periods=36, sd=SD, test_level=LEVEL, means=(0.037, 0.05, 0.1), target_power=0.8, kind="t"
    )

    assert lines[:5] == (
        "power of a one-sided test of a mean over 36 non-overlapping periods (test level 0.05, "
        "per-period sd 0.15, t critical value 1.6896 on 35 degrees of freedom; a property of the "
        "design, not a result):",
        "  a true mean of 0.037: probability 0.417 of a one-sided p at or below 0.05; 103 "
        "periods for 0.8",
        "  a true mean of 0.05: probability 0.622 of a one-sided p at or below 0.05; 57 periods "
        "for 0.8",
        "  a true mean of 0.1: probability 0.990 of a one-sided p at or below 0.05; 16 periods "
        "for 0.8",
        "  the smallest mean with probability 0.8: 0.0633",
    )
    assert FORBIDDEN.search("\n".join(lines)) is None
