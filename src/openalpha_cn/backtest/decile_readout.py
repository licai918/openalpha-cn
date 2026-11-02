"""One cross section's deciles, tails and rank IC, and one dated series' dispersion (`V2-P7-004`).

`read_cross_section` reads a scored scope against the labels some of its names earned, with the
**forward report's own arithmetic** (`strategy_survivor_forward.record_scope`, `record_outcome` and
`_rank_ic`), so a number read here on a historical session and the number the weekly report prints
for a live record are the same quantity:

- the scope is every name that was scored, ordered **worst first by `(score, ts_code)`** -- the
  code breaks a tie, so the cut never depends on the order the names arrive in -- and the name at
  position `p` of `n` is in decile `floor(10 p / n)`; **decile 0 is the worst, decile 9 the best**.
  The exploratory scripts (`main_board_remeasure.py`) number the other way (their D1 is the best);
  the two conventions are not to be mixed, and this module only ever uses the report's;
- the deciles and the survivors are cut over the **whole scope, before any outcome** is looked at.
  A name without a label keeps the decile it was cut into and is counted as `unlabelled`; it never
  enters a mean, a rank or a difference (it is not read as zero). `labels` holds only the names
  that earned one;
- every mean is over the labelled names, in worst-first order, by `statistics.fmean`, which is what
  makes the numbers equal the report's bit for bit;
- a tail `k` drops the worst `n * k // 10` names of the whole scope (rounded down, as the report's
  survivors are) and reads the labelled rest against the labelled equal weight. The survivor
  difference is the tail whose `k` is the book's `loser_decile_count`;
- every difference is oriented so that **greater than zero supports the screen**: survivors less
  equal weight, equal weight less the worst decile, best decile less worst decile, and the rank IC
  of score against label. A reading that is not defined is `None`, never zero;
- the rank IC is the tie-aware Spearman correlation: `factor_ic.average_ranks` of both sides, then
  `statistics.correlation`, with the report's three absence codes in the report's order.

`summarize_series` reads one dated series (a rank IC per session, a tail excess per session) for
its dispersion: mean, sample standard deviation (`ddof = 1`), standard error, t, the share above
zero, the first-order autocorrelation and the mean by calendar year. The autocorrelation is why
the sessions of a series must be spaced by at least the label horizon before its t is read as
anything: overlapping labels make neighbouring values the same observation.

This module reads no panel and no store, has no random component and imports only the standard
library and `factor_ic.average_ranks`. It describes what happened on the sessions it was handed;
it is exploratory arithmetic, not evidence that a screen works.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from openalpha_cn.backtest.factor_ic import average_ranks

DECILES: Final[int] = 10
"""Ten groups, as the forward report cuts them; `strategy_survivor_forward._DECILES`."""

RankIcAbsence = Literal["fewer_than_two_labelled", "scores_tied", "labels_tied"]
SeriesAbsence = Literal["no_values", "fewer_than_two_values", "no_dispersion"]


class DecileReadoutError(ValueError):
    """The input cannot be read as a cross section or a series; the message names why."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CrossSectionReadout:
    """One scored scope read against its labels; see the module docstring."""

    scope: int
    """Names that were scored."""
    labelled: int
    unlabelled: int
    scope_by_decile: tuple[int, ...]
    """Ten counts, decile 0 (worst) first, cut over the whole scope before any outcome."""
    labelled_by_decile: tuple[int, ...]
    rank_ic: float | None
    rank_ic_absence: RankIcAbsence | None
    equal_weight: float | None
    """The mean label over the labelled names."""
    decile_means: tuple[float | None, ...]
    """Ten means over the labelled names of each decile; decile 0 is the worst."""
    decile_excess: tuple[float | None, ...]
    """`decile_means` less `equal_weight`."""
    tail_excess: tuple[tuple[int, float | None], ...]
    """`(k, mean over the labelled names left after dropping the worst n * k // 10 of the scope,
    less equal_weight)`, in the order `tails` was given."""
    survivors_minus_ew: float | None
    """The `tail_excess` whose `k` is `loser_decile_count`."""
    ew_minus_worst_decile: float | None
    top_minus_bottom: float | None


def read_cross_section(
    scored: Sequence[tuple[str, float]],
    labels: Mapping[str, float],
    *,
    loser_decile_count: int,
    tails: Sequence[int] = (1, 2, 3),
) -> CrossSectionReadout:
    """`scored`'s names read against `labels`, which holds only the names that earned one."""
    _require_readable(scored, labels, loser_decile_count=loser_decile_count, tails=tails)
    worst_first = sorted((score, code) for code, score in scored)
    count = len(worst_first)
    decile_of = {
        code: DECILES * position // count for position, (_, code) in enumerate(worst_first)
    }
    labelled = [(score, code) for score, code in worst_first if code in labels]

    equal_weight = _mean(labels[code] for _, code in labelled)
    decile_means = tuple(
        _mean(labels[code] for _, code in labelled if decile_of[code] == decile)
        for decile in range(DECILES)
    )

    def tail_excess(k: int) -> float | None:
        kept = {code for _, code in worst_first[count * k // DECILES :]}
        return _less(_mean(labels[code] for _, code in labelled if code in kept), equal_weight)

    rank_ic, rank_ic_absence = _rank_ic(
        [score for score, _ in labelled], [labels[code] for _, code in labelled]
    )
    return CrossSectionReadout(
        scope=count,
        labelled=len(labelled),
        unlabelled=count - len(labelled),
        scope_by_decile=_per_decile(decile_of.values()),
        labelled_by_decile=_per_decile(decile_of[code] for _, code in labelled),
        rank_ic=rank_ic,
        rank_ic_absence=rank_ic_absence,
        equal_weight=equal_weight,
        decile_means=decile_means,
        decile_excess=tuple(_less(mean, equal_weight) for mean in decile_means),
        tail_excess=tuple((k, tail_excess(k)) for k in tails),
        survivors_minus_ew=tail_excess(loser_decile_count),
        ew_minus_worst_decile=_less(equal_weight, decile_means[0]),
        top_minus_bottom=_less(decile_means[DECILES - 1], decile_means[0]),
    )


def _require_readable(
    scored: Sequence[tuple[str, float]],
    labels: Mapping[str, float],
    *,
    loser_decile_count: int,
    tails: Sequence[int],
) -> None:
    if not scored:
        raise DecileReadoutError("the scope is empty; there is no cross section to read")
    codes = [code for code, _ in scored]
    repeated = sorted(code for code, seen in Counter(codes).items() if seen > 1)
    if repeated:
        raise DecileReadoutError(f"{repeated} is scored more than once")
    unfinite = sorted(code for code, score in scored if not math.isfinite(score))
    if unfinite:
        raise DecileReadoutError(f"the score of {unfinite} is not finite")
    unscored = sorted(set(labels) - set(codes))
    if unscored:
        raise DecileReadoutError(f"{unscored} carries a label and is not scored")
    unfinite = sorted(code for code, label in labels.items() if not math.isfinite(label))
    if unfinite:
        raise DecileReadoutError(f"the label of {unfinite} is not finite")
    if not 0 <= loser_decile_count < DECILES:
        raise DecileReadoutError(
            f"loser_decile_count {loser_decile_count} is outside 0..{DECILES - 1}"
        )
    if any(not 1 <= k < DECILES for k in tails) or len(set(tails)) != len(tails):
        raise DecileReadoutError(
            f"each tail is a distinct number of deciles in 1..{DECILES - 1}; got {list(tails)}"
        )


def _rank_ic(
    scores: Sequence[float], labels: Sequence[float]
) -> tuple[float | None, RankIcAbsence | None]:
    """`strategy_survivor_forward._rank_ic`'s three checks and its two calls, in its order."""
    if len(scores) < 2:
        return None, "fewer_than_two_labelled"
    if len(set(scores)) == 1:
        return None, "scores_tied"
    if len(set(labels)) == 1:
        return None, "labels_tied"
    return statistics.correlation(average_ranks(scores), average_ranks(labels)), None


def _per_decile(deciles: Iterable[int]) -> tuple[int, ...]:
    counts = Counter(deciles)
    return tuple(counts.get(decile, 0) for decile in range(DECILES))


def _mean(values: Iterable[float]) -> float | None:
    present = list(values)
    return statistics.fmean(present) if present else None


def _less(value: float | None, base: float | None) -> float | None:
    return None if value is None or base is None else value - base


@dataclass(frozen=True, slots=True, kw_only=True)
class SeriesSummary:
    """One dated series' dispersion; see the module docstring."""

    n: int
    mean: float | None
    sd: float | None
    """The sample standard deviation, `ddof = 1`."""
    se: float | None
    """`sd / sqrt(n)`."""
    t: float | None
    """`mean / se`; absent when the series has no dispersion."""
    share_positive: float | None
    """The share of values above zero."""
    lag1_autocorrelation: float | None
    """Needs three values and some dispersion."""
    by_year: tuple[tuple[int, int, float], ...]
    """`(calendar year, n, mean)`, ascending."""
    absence: SeriesAbsence | None


def summarize_series(values: Sequence[tuple[date, float]]) -> SeriesSummary:
    """`values`, one finite number per session in strictly ascending date order, summarised."""
    for (earlier, _), (later, _) in itertools.pairwise(values):
        if later <= earlier:
            raise DecileReadoutError(
                f"the dates must be strictly ascending; {later.isoformat()} follows "
                f"{earlier.isoformat()}"
            )
    unfinite = [day.isoformat() for day, value in values if not math.isfinite(value)]
    if unfinite:
        raise DecileReadoutError(f"the value on {unfinite} is not finite")
    if not values:
        return SeriesSummary(
            n=0,
            mean=None,
            sd=None,
            se=None,
            t=None,
            share_positive=None,
            lag1_autocorrelation=None,
            by_year=(),
            absence="no_values",
        )
    numbers = [value for _, value in values]
    n = len(numbers)
    mean = statistics.fmean(numbers)
    years: dict[int, list[float]] = {}
    for day, value in values:
        years.setdefault(day.year, []).append(value)
    by_year = tuple(
        (year, len(found), statistics.fmean(found)) for year, found in sorted(years.items())
    )
    share_positive = sum(1 for number in numbers if number > 0) / n
    if n < 2:
        return SeriesSummary(
            n=n,
            mean=mean,
            sd=None,
            se=None,
            t=None,
            share_positive=share_positive,
            lag1_autocorrelation=None,
            by_year=by_year,
            absence="fewer_than_two_values",
        )
    sd = statistics.stdev(numbers)
    se = sd / math.sqrt(n)
    dispersed = sd > 0
    return SeriesSummary(
        n=n,
        mean=mean,
        sd=sd,
        se=se,
        t=mean / se if dispersed else None,
        share_positive=share_positive,
        lag1_autocorrelation=_lag1_autocorrelation(numbers, mean) if dispersed else None,
        by_year=by_year,
        absence=None if dispersed else "no_dispersion",
    )


def _lag1_autocorrelation(numbers: Sequence[float], mean: float) -> float | None:
    """The sum of neighbouring centred products over the sum of squares; three values at least."""
    if len(numbers) < 3:
        return None
    centred = [number - mean for number in numbers]
    denominator = math.fsum(value * value for value in centred)
    if denominator <= 0:
        return None
    numerator = math.fsum(earlier * later for earlier, later in itertools.pairwise(centred))
    return numerator / denominator
