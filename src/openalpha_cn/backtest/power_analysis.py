"""The power of a one-sided test of a mean over non-overlapping periods, and what surrounds it.

`V2-P7-004` (the calculator) feeds `V2-P7-018` (the weekly report's power section), `V2-P7-024` and
`V2-P7-006a`. The P7 route was decided on one piece of arithmetic -- *a per-period sd of 0.15 and
a one-sided test at 0.05 need about 56 periods to detect a mean of 0.05 with probability 0.8* --
and that arithmetic is a function of five numbers that nothing in the repository computed. This
module computes it, with the standard library only (`statistics.NormalDist`, `math.lgamma`;
`backtest-no-numeric-stack-or-panel-plane` forbids scipy), so the weekly report in `src/` and the
diagnostics tooling in `scripts/research/` quote the same number from the same place.

## What a power is, and is not

`POWER_BASIS` is the sentence every printed power carries: *the probability, under the plan's
assumptions, that a one-sided test of the mean at the test level gives a p-value at or below it*.
It is a property of the **design** -- a sample size, a planning sd, a test level, a true mean the
planner chose to ask about. It is not a result, and two readings of it are refused by the shape of
this module rather than by a warning:

* **Not "observed power".** Every function takes the planning `sd` as an argument and has no
  parameter for a measured one. A window's own standard deviation is described elsewhere and is
  never fed back in here: observed power is a monotone function of the p-value and says nothing
  the p-value did not.
* **Not "no effect".** A one-sided p-value above the test level reads "not detectable at this
  design" (`NOT_DETECTABLE_WORDING`): a conclusion category of the design. A mean smaller than
  `smallest_mean_at_power` is one the design was unlikely to detect even if it were there.

## Two critical values, both approximations

The statistic is the standardised mean of `periods` values normally distributed with the planning
sd, so under the alternative it is shifted by `sqrt(periods) * mean / sd`:

    power = Phi(sqrt(periods) * mean / sd - c)

* `kind="normal"` takes `c = z_(1 - level)`: the normal approximation. It is the arithmetic the
  P7 route was decided on (K = 56 periods for 0.8 at a mean of 0.05).
* `kind="t"` takes `c = t_(1 - level, periods - 1)`: **a t-adjusted normal approximation** -- the
  critical value of a t test, the noncentrality of a normal one. It is not the exact noncentral-t
  power. At K = 12 it gives 0.261 where the normal one gives 0.312 (Part E's "about 25%"), and the
  80% at a mean of 0.05 needs 57 periods, not 56.

The registered test is a sign-flip randomisation test. Its small-sample behaviour is closer to a t
test's than to a normal one's, so the `t` convention is the more conservative of the two at small
K, and the two converge as K grows. Both are approximations; neither is the registered test's
exact power, and this module does not claim it is.

## Determinism

No random numbers anywhere. The t distribution has no closed-form quantile in the standard
library, so it is computed from fixed grids: Simpson's rule over `_T_PANELS` panels summed with
`math.fsum`, inverted by `_T_BISECTIONS` bisections of a fixed interval. The same inputs give the
same float on every run and every platform, which is what lets a printed table be diffed.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Sequence
from statistics import NormalDist
from typing import Final, Literal

from openalpha_cn.backtest.multiple_testing import harmonic_number

CriticalValue = Literal["normal", "t"]

NOT_DETECTABLE_WORDING: Final = (
    'a one-sided p-value above the test level reads "not detectable at this design": a '
    'conclusion category of the design, never "no effect"'
)
POWER_BASIS: Final = (
    "the probability, under the plan's assumptions (period values normally distributed with the "
    "planning sd, independent unless a serial correlation is stated), that a one-sided test of "
    "the mean at the test level gives a p-value at or below it; a property of the design, not a "
    "result"
)

_STANDARD: Final = NormalDist()
_T_PANELS: Final[int] = 4096  # Simpson panels on [0, |x|]
_T_BISECTIONS: Final[int] = 80
_T_UPPER: Final[float] = 64.0
_FUTILITY_PANELS: Final[int] = 4000
_FUTILITY_SPAN: Final[float] = 24.0
# The inner step of the futility integral is `sqrt(1 - k/K)` wide; the grid is 0.006 wide. Measured
# against an independent 12-point Gauss-Legendre composite the grid is within 2e-10 at a step of
# 0.01 and 2e-7 at 0.007, and off by 5e-5 at 0.003, so it stops at 0.01.
_FUTILITY_MIN_STEP: Final[float] = 0.01
_MAX_PERIODS: Final[int] = 100_000


class PowerError(ValueError):
    """A question the design has no answer to, refused by name rather than answered wrongly."""


def _finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise PowerError(f"{name} must be finite, got {value!r}")
    return value


def _check_sd(sd: float) -> None:
    if not math.isfinite(sd) or sd <= 0:
        raise PowerError(f"the planning sd must be positive and finite, got {sd!r}")


def _check_periods(periods: float) -> None:
    if not math.isfinite(periods) or periods < 2:
        raise PowerError(
            f"periods must be at least 2 (a t test has periods - 1 degrees of freedom), "
            f"got {periods!r}"
        )


def _check_test_level(test_level: float) -> None:
    if not math.isfinite(test_level) or not 0 < test_level < 0.5:
        raise PowerError(f"the test level must lie strictly between 0 and 0.5, got {test_level!r}")
    if 1 - test_level >= 1:
        raise PowerError(f"the test level {test_level!r} is below the resolution of 1 - level")


def _check_power(power: float) -> None:
    if not math.isfinite(power) or not 0 < power < 1:
        raise PowerError(f"power must lie strictly between 0 and 1, got {power!r}")


def _t_density(x: float, df: float, log_c: float) -> float:
    return math.exp(log_c - (df + 1) / 2 * math.log1p(x * x / df))


def _t_cdf(x: float, df: float) -> float:
    if x == 0:
        return 0.5
    # the normalising constant is the same at every node: once per integral, not once per node
    log_c = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
    width = abs(x) / _T_PANELS
    terms = (
        (1 if index in (0, _T_PANELS) else 4 if index % 2 else 2)
        * _t_density(index * width, df, log_c)
        for index in range(_T_PANELS + 1)
    )
    area = math.fsum(terms) * width / 3
    return 0.5 + area if x > 0 else 0.5 - area


@functools.lru_cache(maxsize=512)
def t_quantile(probability: float, df: float) -> float:
    """The `probability` quantile of Student's t with `df` degrees of freedom (`0.5 <= p < 1`)."""
    if not 0.5 <= probability < 1:
        raise PowerError(
            f"the t quantile is defined here for a probability in [0.5, 1), got {probability!r}"
        )
    if not math.isfinite(df) or df < 1:
        raise PowerError(f"df must be finite and at least 1, got {df!r}")
    if probability == 0.5:
        return 0.0
    if _t_cdf(_T_UPPER, df) < probability:
        raise PowerError(
            f"the t quantile of probability {probability!r} on {df!r} df lies beyond the search "
            f"range (|t| <= {_T_UPPER:g}); refused rather than answered with the range's edge"
        )
    low, high = 0.0, _T_UPPER
    for _ in range(_T_BISECTIONS):
        middle = (low + high) / 2
        if _t_cdf(middle, df) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def critical_value(*, test_level: float, periods: float, kind: CriticalValue) -> float:
    """The one-sided critical value at `test_level`: normal, or t on `periods - 1` df."""
    _check_test_level(test_level)
    _check_periods(periods)
    if kind == "normal":
        return _STANDARD.inv_cdf(1 - test_level)
    if kind == "t":
        return t_quantile(1 - test_level, periods - 1)
    raise PowerError(f"the critical value kind must be 'normal' or 't', got {kind!r}")


def power_of_mean_test(
    *, mean: float, sd: float, periods: float, test_level: float, kind: CriticalValue
) -> float:
    """`Phi(sqrt(periods) * mean / sd - c)`: the probability of a p at or below `test_level`."""
    _finite("mean", mean)
    _check_sd(sd)
    critical = critical_value(test_level=test_level, periods=periods, kind=kind)
    return _STANDARD.cdf(math.sqrt(periods) * mean / sd - critical)


def smallest_mean_at_power(
    *, power: float, sd: float, periods: float, test_level: float, kind: CriticalValue
) -> float:
    """The mean detected with probability `power`: `(c + z_power) * sd / sqrt(periods)`."""
    _check_power(power)
    _check_sd(sd)
    critical = critical_value(test_level=test_level, periods=periods, kind=kind)
    return (critical + _STANDARD.inv_cdf(power)) * sd / math.sqrt(periods)


def periods_for_power(
    *, mean: float, power: float, sd: float, test_level: float, kind: CriticalValue
) -> int:
    """The fewest periods with probability at least `power` of a p at or below `test_level`.

    Normal: `max(2, ceil(((z_(1 - level) + z_power) * sd / mean) ** 2))`, rounded **up** -- a
    design that falls short of the target by a rounding is not a design that meets it. The sum
    is floored at 0: a target below the test level is already met at any count, and squaring a
    negative sum would ask for periods the design does not need. t: the normal answer is a floor
    (the t critical value is the larger), so it is the starting point and the count goes up by one
    until the t-adjusted power reaches the target.

    More than 100,000 periods is refused, for either kind: it is not a plan, and the arithmetic
    that produces it overflows long before it stops being a number.
    """
    _finite("mean", mean)
    if mean <= 0:
        raise PowerError(f"the mean to detect must be positive, got {mean!r}")
    _check_power(power)
    _check_sd(sd)
    _check_test_level(test_level)
    if kind not in ("normal", "t"):
        raise PowerError(f"the critical value kind must be 'normal' or 't', got {kind!r}")
    z_sum = max(0.0, _STANDARD.inv_cdf(1 - test_level) + _STANDARD.inv_cdf(power))
    ratio = z_sum * sd / mean
    if not ratio <= math.sqrt(_MAX_PERIODS):
        raise PowerError(f"more than {_MAX_PERIODS} periods are needed; refused")
    periods = max(2, math.ceil(ratio * ratio))
    if kind == "normal":
        return periods
    while (
        power_of_mean_test(mean=mean, sd=sd, periods=periods, test_level=test_level, kind="t")
        < power
    ):
        periods += 1
        if periods > _MAX_PERIODS:
            raise PowerError(f"more than {_MAX_PERIODS} periods are needed; refused")
    return periods


def autocorrelation_equivalent_periods(*, periods: float, lag1: float) -> float:
    """`periods * (1 - lag1) / (1 + lag1)`: the independent periods a lag-1 correlation leaves.

    The first-order autoregressive inflation of the variance of a mean. The result can fall below
    2, and then `power_of_mean_test` refuses it: too few independent periods is not a design.
    """
    _check_periods(periods)
    if not math.isfinite(lag1) or not -1 < lag1 < 1:
        raise PowerError(f"the lag-1 autocorrelation must lie between -1 and 1, got {lag1!r}")
    return periods * (1 - lag1) / (1 + lag1)


def by_first_step_level(*, q: float, family_size: int) -> float:
    """`q / (m * H_m)`: the level a Benjamini-Yekutieli family tests its best p-value at.

    The first step of the step-up is the smallest p-value against `q / (m * H_m)`, the lowest line
    on the staircase, so a secondary powered at this level is powered at a lower bound of what it
    will actually face.
    """
    if not math.isfinite(q) or not 0 < q < 1:
        raise PowerError(f"q must lie strictly between 0 and 1, got {q!r}")
    if not isinstance(family_size, int) or family_size < 1:
        raise PowerError(f"the family size must be an integer of at least 1, got {family_size!r}")
    return q / (family_size * harmonic_number(family_size))


def futility_stop_probability(
    *, mean: float, sd: float, interim_periods: int, threshold: float
) -> float:
    """`Phi((threshold - mean) / (sd / sqrt(k)))`: the chance the mean of k periods is at or below
    `threshold`, which is the chance a rule that stops there stops."""
    _finite("mean", mean)
    _finite("threshold", threshold)
    _check_sd(sd)
    if not isinstance(interim_periods, int) or interim_periods < 1:
        raise PowerError(f"the interim look needs at least 1 period, got {interim_periods!r}")
    return _STANDARD.cdf((threshold - mean) / (sd / math.sqrt(interim_periods)))


def power_with_futility(
    *,
    mean: float,
    sd: float,
    periods: int,
    interim_periods: int,
    threshold: float,
    test_level: float,
    kind: CriticalValue,
) -> float:
    """P(interim mean > threshold and final mean > the critical mean), the two standardised
    means bivariate normal with correlation sqrt(k / K): one fixed-grid Simpson integral."""
    _finite("mean", mean)
    _finite("threshold", threshold)
    _check_sd(sd)
    _check_periods(periods)
    if not isinstance(interim_periods, int) or not 1 <= interim_periods < periods:
        raise PowerError(
            f"the interim look must satisfy 1 <= interim_periods < periods, got "
            f"{interim_periods!r} of {periods!r}"
        )
    r = math.sqrt(interim_periods / periods)
    scale = math.sqrt(1 - r * r)
    if scale < _FUTILITY_MIN_STEP:
        raise PowerError(
            f"the interim look at {interim_periods} of {periods} periods is too close to the "
            f"end for the fixed integration grid (sqrt(1 - k/K) must be at least "
            f"{_FUTILITY_MIN_STEP:g})"
        )
    c1 = (threshold - mean) / (sd / math.sqrt(interim_periods))
    c2 = (
        critical_value(test_level=test_level, periods=periods, kind=kind)
        - math.sqrt(periods) * mean / sd
    )
    lower = max(c1, -_FUTILITY_SPAN / 2)
    width = _FUTILITY_SPAN / _FUTILITY_PANELS
    terms = (
        (1 if index in (0, _FUTILITY_PANELS) else 4 if index % 2 else 2)
        * _STANDARD.pdf(lower + index * width)
        * (1 - _STANDARD.cdf((c2 - r * (lower + index * width)) / scale))
        for index in range(_FUTILITY_PANELS + 1)
    )
    return math.fsum(terms) * width / 3


def power_table_lines(
    *,
    periods: int,
    sd: float,
    test_level: float,
    means: Sequence[float],
    target_power: float,
    kind: CriticalValue,
) -> tuple[str, ...]:
    """The power of the design at each stated true mean, as lines a report can print."""
    critical = critical_value(test_level=test_level, periods=periods, kind=kind)
    basis = f"{kind} critical value {critical:.4f}"
    if kind == "t":
        basis += f" on {periods - 1} degrees of freedom"
    lines = [
        f"power of a one-sided test of a mean over {periods} non-overlapping periods "
        f"(test level {test_level:g}, per-period sd {sd:g}, {basis}; a property of the design, "
        "not a result):"
    ]
    for mean in means:
        probability = power_of_mean_test(
            mean=mean, sd=sd, periods=periods, test_level=test_level, kind=kind
        )
        needed = periods_for_power(
            mean=mean, power=target_power, sd=sd, test_level=test_level, kind=kind
        )
        lines.append(
            f"  a true mean of {mean:g}: probability {probability:.3f} of a one-sided p at or "
            f"below {test_level:g}; {needed} periods for {target_power:g}"
        )
    smallest = smallest_mean_at_power(
        power=target_power, sd=sd, periods=periods, test_level=test_level, kind=kind
    )
    lines.append(f"  the smallest mean with probability {target_power:g}: {smallest:.4f}")
    lines.append(f"  {NOT_DETECTABLE_WORDING}")
    return tuple(lines)
