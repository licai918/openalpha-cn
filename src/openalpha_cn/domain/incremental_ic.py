"""Incremental IC over the factor score (`V2-P7-014`), as pure arithmetic.

The evaluator in `V2-P7-014` asks one question of a stored session: *does the agent's score
carry rank information about the forward label that Step 0's composite factor score does not
already carry?* The answer is the **partial rank IC**:

    partial_rank_ic = Pearson( residual(rank(agent) ~ rank(factor)),
                               residual(rank(label) ~ rank(factor)) )

The ranks are Spearman's (1-based, average ties) and the residual is an OLS fit with an
intercept, written in centred form because subtracting the means first is algebraically the same
fit and keeps the residual's own mean at zero by construction. The statistic lives here rather
than in the evaluator for the reason the repository states in its own layer rules: `domain/`
imports no numeric stack and no sibling subpackage, so the formula can be compared against an
independent slow reference without a store, a panel or a model in the way.

## What the refusals mean, and the one signature deviation

A partial IC is undefined, not zero, when the cross section cannot support it: fewer than
`MIN_NAMES` (4) names -- one residual degree of freedom left, the same floor a three-regressor
cross section needs -- mismatched lengths, a non-finite input, a factor whose rank has no
variation (nothing to residualise on), or an agent/label residual with zero variance (the exact
copy of the factor is the common case). The plan's public signature is `float | None`; the
evaluator's `SessionIC` must also record *why* a session has no value, so the refusal travels in
`partial_rank_ic_with_reason` and `partial_rank_ic` returns its first element. That is a
deliberate deviation from the plan's single-return sketch, recorded here rather than discovered
later: the extra function is additive, the named public signature is unchanged.

`incremental_ic` is the **exploratory** companion D2/D5 leave outside the registered test:
`IC(agent + factor) - IC(factor)` on the rank scale, no p-value and no gate of its own. The
combined score is the sum of the two average-rank vectors; any positive multiple gives the same
Spearman correlation, so the two scores' arbitrary units cannot enter the sum.

## Sessions, and the power number

`SessionIC` carries one session's value, the count it was measured on and its refusal reason;
`non_overlapping` picks every `horizon`-th *session* by position from the anchor ordinal -- not
every `horizon`-th calendar day, which would step over holidays and silently overlap labels. The
evaluator prints a detectable mean, and `detectable_mean` restates the survivor power block's
normal formula, `(z_(1-level) + z_power) * sd / sqrt(periods)`: `domain/` may not import
`backtest/power_analysis.py`, so a test holds the two statements equal to 1e-12 across the
designs the report will use.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist
from typing import Final


class IncrementalICError(ValueError):
    """A question the arithmetic refuses by name rather than answering wrongly."""


MIN_NAMES: Final[int] = 4  # one residual degree of freedom left on four names and three terms

REASON_TOO_FEW_NAMES: Final = "too_few_names"
REASON_LENGTH_MISMATCH: Final = "length_mismatch"
REASON_NON_FINITE_INPUT: Final = "non_finite_input"
REASON_CONSTANT_FACTOR_RANK: Final = "constant_factor_rank"
REASON_ZERO_RESIDUAL_VARIANCE: Final = "zero_residual_variance"

_STANDARD: Final = NormalDist()


def _average_ranks(values: Sequence[float]) -> tuple[float, ...]:
    """1-based ranks with average ties, the definition of Spearman's rank transform."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + 1 + end + 1) / 2
        for position in range(start, end + 1):
            ranks[order[position]] = average
        start = end + 1
    return tuple(ranks)


def _finite_values(values: Sequence[float]) -> tuple[float, ...] | None:
    """The input as floats, or `None` when any value is not finite."""
    floats = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in floats):
        return None
    return floats


def _residualise(values: Sequence[float], regressor: Sequence[float]) -> tuple[float, ...] | None:
    """OLS residuals of `values` on `regressor` with an intercept, written centred.

    Subtracting the means first is algebraically the same fit as regressing on an explicit
    intercept column, and it is the form that keeps the residual's own mean at zero by
    construction. `None` when the regressor has no variation at all: a design with no rank
    information is undefined rather than an error to shout about.
    """
    size = len(values)
    centred = [value - math.fsum(values) / size for value in values]
    deviations = [value - math.fsum(regressor) / size for value in regressor]
    variance = math.fsum(deviation * deviation for deviation in deviations)
    if variance == 0.0:
        return None
    slope = math.fsum(c * d for c, d in zip(centred, deviations, strict=True)) / variance
    return tuple(c - slope * d for c, d in zip(centred, deviations, strict=True))


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Pearson's correlation of two equal-length vectors, clamped to its mathematical range.

    `None` when either vector is constant: a correlation with a zero-variance side is undefined,
    not zero. The clamp is there because the floating-point sum can land one ulp outside
    `[-1, 1]`, which is not a statement about the data.
    """
    size = len(left)
    left_centred = [value - math.fsum(left) / size for value in left]
    right_centred = [value - math.fsum(right) / size for value in right]
    left_variance = math.fsum(value * value for value in left_centred)
    right_variance = math.fsum(value * value for value in right_centred)
    if left_variance == 0.0 or right_variance == 0.0:
        return None
    value = math.fsum(a * b for a, b in zip(left_centred, right_centred, strict=True)) / math.sqrt(
        left_variance * right_variance
    )
    return max(-1.0, min(1.0, value))


def partial_rank_ic_with_reason(
    agent: Sequence[float], factor: Sequence[float], label: Sequence[float]
) -> tuple[float | None, str | None]:
    """`partial_rank_ic` with the refusal stated: `(value, None)` or `(None, reason)`.

    The plan keeps the single statistic's signature at `float | None`; the evaluator's
    `SessionIC` also records *why* a session has no value, so the reason travels beside it here
    rather than in a second computation. The checks run in the order the reasons are listed:
    length mismatch, too few names, a non-finite input, a factor with no rank variation, then a
    residual with zero variance.
    """
    if not (len(agent) == len(factor) == len(label)):
        return None, REASON_LENGTH_MISMATCH
    if len(agent) < MIN_NAMES:
        return None, REASON_TOO_FEW_NAMES

    agent_values = _finite_values(agent)
    factor_values = _finite_values(factor)
    label_values = _finite_values(label)
    if agent_values is None or factor_values is None or label_values is None:
        return None, REASON_NON_FINITE_INPUT

    factor_ranks = _average_ranks(factor_values)
    agent_residual = _residualise(_average_ranks(agent_values), factor_ranks)
    label_residual = _residualise(_average_ranks(label_values), factor_ranks)
    if agent_residual is None or label_residual is None:
        return None, REASON_CONSTANT_FACTOR_RANK
    value = _pearson(agent_residual, label_residual)
    if value is None:
        return None, REASON_ZERO_RESIDUAL_VARIANCE
    return value, None


def partial_rank_ic(
    agent: Sequence[float], factor: Sequence[float], label: Sequence[float]
) -> float | None:
    """The rank IC of `agent` on `label` after both are residualised on `factor`, OLS.

    The three inputs are one cross section, aligned by position: `agent[i]`, `factor[i]` and
    `label[i]` describe the same security. `None` when the cross section cannot answer --
    fewer than `MIN_NAMES` names, mismatched lengths, a non-finite input, or a degenerate
    design; `partial_rank_ic_with_reason` names which.
    """
    return partial_rank_ic_with_reason(agent, factor, label)[0]


@dataclass(frozen=True)
class SessionIC:
    """One session's partial IC, the count it was measured on, and why it may have no value.

    A session with fewer than `MIN_NAMES` scored names, a degenerate cross section or a missing
    label has `value=None` and a `reason`; it is not dropped, because "not measurable" and "no
    evidence" are the two statements the evaluator must never confuse.
    """

    session: date
    n_scored: int
    value: float | None
    reason: str | None


def non_overlapping(
    sessions: Sequence[SessionIC], *, horizon: int, anchor_ordinal: int
) -> tuple[SessionIC, ...]:
    """Every `horizon`-th session from the anchor, so `horizon`-session labels never overlap.

    `anchor_ordinal` is the anchor session's `date.toordinal()` -- the evaluator knows the
    registration's date, not its position in a list, and the sessions it receives may arrive in
    any order. The sessions are sorted by date first, so the input order is not part of the
    answer; the first session at or after the anchor ordinal is the first used. The rest follow
    by **position** (`+horizon` in the sorted sequence) rather than by calendar arithmetic:
    "every fifth session" means five open sessions, which a weekday step would get wrong across
    a holiday. An anchor past every session selects nothing.
    """
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise IncrementalICError(f"the horizon must be a positive integer, got {horizon!r}")
    ordered = sorted(sessions, key=lambda item: item.session)
    for index, item in enumerate(ordered):
        if item.session.toordinal() >= anchor_ordinal:
            return tuple(ordered[index::horizon])
    return ()


def incremental_ic(
    agent: Sequence[float], factor: Sequence[float], label: Sequence[float]
) -> float | None:
    """**Exploratory only**: `IC(agent + factor) - IC(factor)` on the rank scale.

    The combined score is the sum of the two average-rank vectors -- any positive multiple gives
    the same Spearman correlation, so neither score's arbitrary units enter the comparison --
    and the IC of that combined score is the plain Spearman correlation with the label. This is
    **not** the registered primary statistic: `partial_rank_ic` is, and this is the descriptive
    companion a report may print beside it (D2/D5: the primary test is the partial IC, and this
    comparison carries no p-value and no gate of its own). `None` where either IC is undefined.
    """
    if not (len(agent) == len(factor) == len(label)) or len(agent) < MIN_NAMES:
        return None
    agent_values = _finite_values(agent)
    factor_values = _finite_values(factor)
    label_values = _finite_values(label)
    if agent_values is None or factor_values is None or label_values is None:
        return None

    factor_ranks = _average_ranks(factor_values)
    label_ranks = _average_ranks(label_values)
    factor_ic = _pearson(factor_ranks, label_ranks)
    if factor_ic is None:
        return None
    combined = [a + f for a, f in zip(_average_ranks(agent_values), factor_ranks, strict=True)]
    combined_ic = _pearson(_average_ranks(combined), label_ranks)
    if combined_ic is None:
        return None
    return combined_ic - factor_ic


def _check_design(*, sd: float, periods: int, level: float, power: float) -> None:
    """The survivor power block's own input bounds, refused by name rather than with `nan`."""
    if not math.isfinite(sd) or sd <= 0:
        raise IncrementalICError(f"the planning sd must be positive and finite, got {sd!r}")
    if not isinstance(periods, int) or isinstance(periods, bool) or periods < 2:
        raise IncrementalICError(f"periods must be an integer of at least 2, got {periods!r}")
    if not math.isfinite(level) or not 0 < level < 0.5:
        raise IncrementalICError(
            f"the one-sided test level must lie strictly between 0 and 0.5, got {level!r}"
        )
    if not math.isfinite(power) or not 0 < power < 1:
        raise IncrementalICError(f"power must lie strictly between 0 and 1, got {power!r}")


def detectable_mean(*, sd: float, periods: int, level: float, power: float) -> float:
    """The mean detected with probability `power`: `(z_(1-level) + z_power) * sd / sqrt(periods)`.

    The survivor power block's normal critical value, re-stated here because `domain/` may not
    import `backtest/`; a test holds the two statements equal to 1e-12 across the designs the
    report will print. A property of the **design**, never observed power, and a mean smaller
    than the returned value is one the design was unlikely to detect even if it were there.
    """
    _check_design(sd=sd, periods=periods, level=level, power=power)
    return (_STANDARD.inv_cdf(1 - level) + _STANDARD.inv_cdf(power)) * sd / math.sqrt(periods)
