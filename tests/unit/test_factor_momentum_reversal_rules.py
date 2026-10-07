"""`V2-P3-012`'s momentum-and-reversal family: its declarations and its arithmetic.

The engine is not under test here -- `tests/integration/panel/test_factor_momentum_reversal.py`
drives it against real partitions. What is under test is everything that is decided *before* a
store is involved: the four reaches, the four span bounds, the two directions, and the one
function that turns a window of prices into a number.

## The three things this file is built to catch

- **A number chosen to make a fixture pass.** `MAX_HALTED_SESSIONS_IN_FIVE` sets every momentum
  factor's `max_window_sessions` by arithmetic, and the ladder that arithmetic has to satisfy is
  asserted rather than described: no momentum factor's worst-case span may reach the reach of the
  next factor up, or a halted name's 20-session momentum would cover the interval the 60-session
  one is *defined* over. Widening the tolerance fails here before it changes a single value.
- **Two factors that give one answer.** `V2-P3-004`'s review found two assertions that could not
  separate two answers because its fixture had one industry group. The same shape is available to
  a family of four windows over one price path -- so every pair is asserted apart, on one path,
  with a floor under the gap.
- **The wrong return path.** `domain/daily_prices.py` measured three ways to compute one session's
  return and found that the naive `close[t] / close[t-1] - 1` gets the **sign** wrong across an
  ex-rights morning. A momentum is that quantity compounded. The disagreement is driven here on
  that module's own published numbers rather than restated as a warning.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from itertools import pairwise
from types import MappingProxyType
from typing import Final

import pytest

from openalpha_cn.domain.daily_prices import CLOSE_COLUMN, DAILY_DATASET, PRE_CLOSE_COLUMN
from openalpha_cn.domain.factor import FactorDefinition, FactorField
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_EVALUATORS,
    MAX_HALTED_SESSIONS_IN_FIVE,
    MOMENTUM_20_SESSIONS,
    MOMENTUM_60_SESSIONS,
    MOMENTUM_120_SESSIONS,
    REVERSAL_5_SESSIONS,
    SHORT_REVERSAL_SESSIONS,
    FactorEvaluator,
    FactorWindow,
    _compounded_session_return,
    _momentum_sessions,
    _reversal_5_sessions,
)

AS_OF: Final[datetime] = datetime(2026, 6, 30, 4, 0, tzinfo=UTC)

FAMILY: Final[tuple[tuple[FactorDefinition, int], ...]] = (
    (MOMENTUM_20_SESSIONS, 20),
    (MOMENTUM_60_SESSIONS, 60),
    (MOMENTUM_120_SESSIONS, 120),
    (REVERSAL_5_SESSIONS, 5),
)
"""The four definitions `V2-P3-012` ships, each with the number of sessions it *compounds*.

The nominal horizon is deliberately carried beside the definition rather than parsed out of the
key: `momentum_20_sessions` declares a reach of 25 and reads 20 of them, and a test that derived
"20" from the name would be asserting that the name and the reach agree with each other rather
than that either is right.
"""

MOMENTA: Final[tuple[FactorDefinition, ...]] = (
    MOMENTUM_20_SESSIONS,
    MOMENTUM_60_SESSIONS,
    MOMENTUM_120_SESSIONS,
)
"""The three factors that skip and tolerate a halt. `REVERSAL_5_SESSIONS` does neither."""

SHORTEST_MEASURED_A_SHARE_YEAR: Final[int] = 242
"""The fewest sessions any full A-share year in this repository's measurements holds.

`domain/daily_prices.py::MIN_SESSION_ROW_SHARE` carries the census: 2015 ran 244 sessions, 2018
243 and 2024 242. Restated here as the smallest of the three, because the check it backs is an
upper bound on `MOMENTUM_120_SESSIONS`' worst-case span and the *smallest* year is the one that
bound has to clear.
"""

SESSIONS: Final[int] = 130
"""Long enough to form `MOMENTUM_120_SESSIONS`' 125-session window with room to spare."""

GROWTH: Final[tuple[float, ...]] = tuple(
    1.0 + 0.004 * (((index * 7) % 13) - 6) for index in range(SESSIONS)
)
"""One session's growth factor, `close / pre_close`, for each session of the probe path.

Deterministic and mixed in sign -- the multiplier walks `-6..+6` in units of 0.004, so the path
rises and falls rather than compounding one way. That matters for the separability assertions
below: on a monotone path the four windows would be ordered by construction and "no two agree"
would be arithmetic rather than a measurement of these four definitions.
"""


def _prices(growth: tuple[float, ...] = GROWTH) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """`(closes, pre_closes)` for a path with no corporate action: `pre_close[t] == close[t-1]`.

    The first session's `pre_close` is derived from its own growth factor rather than left equal
    to its close, so every session of the path -- including the first -- carries a real return.
    """
    closes: list[float] = []
    pre_closes: list[float] = []
    price = 10.0
    for factor in growth:
        pre_closes.append(price)
        price *= factor
        closes.append(price)
    return tuple(closes), tuple(pre_closes)


def _window(closes: tuple[float, ...], pre_closes: tuple[float, ...]) -> FactorWindow:
    """A complete window over both price columns, exactly as `_complete_series` would build it."""
    sessions = tuple(date(2026, 1, 1) for _ in closes)
    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=sessions,
        periods=(),
        values=MappingProxyType(
            {
                (DAILY_DATASET, CLOSE_COLUMN): closes,
                (DAILY_DATASET, PRE_CLOSE_COLUMN): pre_closes,
            }
        ),
    )


def _evaluate(definition: FactorDefinition, evaluator: FactorEvaluator) -> float:
    """This factor's value over the tail of the probe path its own reach selects.

    The window the engine hands an evaluator is the most recent `lookback_sessions` of the
    security's own sessions, so slicing the path here is the engine's own selection restated --
    which is what makes these four numbers comparable to each other.
    """
    closes, pre_closes = _prices()
    reach = definition.lookback_sessions
    assert reach is not None
    value = evaluator(_window(closes[-reach:], pre_closes[-reach:]))
    assert value is not None
    return value


def _product(growth: tuple[float, ...]) -> float:
    """`prod(growth) - 1`, accumulated left to right exactly as the evaluator does."""
    total = 1.0
    for factor in growth:
        total *= factor
    return total - 1.0


# --- what the four definitions declare --------------------------------------------------------


def test_the_family_is_exactly_the_four_factors_this_issue_owns() -> None:
    """Every shipped `momentum_reversal` factor is one of these four -- or `reversal_1d`.

    Read off the live registry rather than off this file's own tuple, so a fifth momentum factor
    added without a note here fails rather than going unexamined. `reversal_1d` is named as the
    exception because it carries the same family label and is the engine's verification subject
    rather than one of this issue's deliverables; `REVERSAL_5_SESSIONS`' docstring says why it is
    not that factor with a wider window.
    """
    shipped = {
        definition.qualified_key
        for definition in FACTOR_DEFINITIONS.definitions
        if definition.family == "momentum_reversal"
    }

    assert shipped == {definition.qualified_key for definition, _ in FAMILY} | {"reversal_1d/v1"}
    assert all(definition.qualified_key in FACTOR_EVALUATORS for definition, _ in FAMILY)


@pytest.mark.parametrize(("definition", "compounded"), FAMILY)
def test_every_factor_here_reads_both_price_columns_and_declares_no_filing_reach(
    definition: FactorDefinition, compounded: int
) -> None:
    """The session axis and nothing else, and **both** halves of the published return.

    `pre_close` being a declared input rather than a convenience is the whole of the return-path
    choice: a factor that declared only `close` could not compute `close / pre_close` at all, and
    the only remaining arithmetic is the one `domain/daily_prices.py` measures as wrong. The
    period reach is `None` on both fields because `FactorDefinition` requires each axis to be
    declared exactly when `required_fields` puts the factor on it.
    """
    assert definition.required_fields == (
        FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
        FactorField(dataset=DAILY_DATASET, column=PRE_CLOSE_COLUMN),
    )
    assert definition.session_datasets == (DAILY_DATASET,)
    assert definition.period_datasets == ()
    assert definition.lookback_periods is None
    assert definition.max_window_periods is None
    assert definition.lookback_sessions == compounded + (
        0 if definition is REVERSAL_5_SESSIONS else SHORT_REVERSAL_SESSIONS
    )


def test_the_momentum_skip_is_the_reversals_own_reach_and_not_a_second_number() -> None:
    """One constant with two readers, which is what makes the two windows disjoint by arithmetic.

    A second constant equal to this one by coincidence would let somebody widen the reversal to
    ten sessions and leave the momentum skip at five, and the two families of windows would start
    overlapping with nothing going red.
    """
    assert REVERSAL_5_SESSIONS.lookback_sessions == SHORT_REVERSAL_SESSIONS
    for definition in MOMENTA:
        assert definition.lookback_sessions is not None
        assert definition.lookback_sessions % SHORT_REVERSAL_SESSIONS == 0


def test_every_momentum_span_is_the_declared_halt_tolerance_of_its_own_reach() -> None:
    """`max_window_sessions == lookback + lookback * MAX_HALTED_SESSIONS_IN_FIVE // 5`, exactly.

    The three numbers 30, 78 and 150 are not three judgements; they are one judgement applied
    three times, and this is where that is true rather than merely stated. Changing any one of
    them alone fails here, and changing the constant fails the ladder test below as well.
    """
    for definition in MOMENTA:
        reach = definition.lookback_sessions
        assert reach is not None
        assert definition.max_window_sessions == reach + reach * MAX_HALTED_SESSIONS_IN_FIVE // 5
        assert definition.max_window_sessions is not None
        assert definition.max_window_sessions > reach


def test_the_reversal_is_at_equality_and_that_is_the_opposite_setting() -> None:
    """A five-session reversal tolerates no halt at all, and the asymmetry is the judgement.

    Asserted beside the momentum rule rather than in a test of its own, because what has to be
    visible is that the family carries **two** settings deliberately: a short reversal whose whole
    content is an unbroken recent interval, and three momenta that would refuse most of the 2015
    market under the same rule.
    """
    assert REVERSAL_5_SESSIONS.max_window_sessions == REVERSAL_5_SESSIONS.lookback_sessions
    assert all(
        definition.max_window_sessions != definition.lookback_sessions for definition in MOMENTA
    )


def test_no_momentum_windows_worst_case_span_reaches_the_next_rungs_own_reach() -> None:
    """The ladder that turns "one session in five" from a free parameter into a bounded one.

    A 20-session momentum whose window is allowed to span 65 panel sessions covers exactly the
    interval `MOMENTUM_60_SESSIONS` is defined over, and the two factors then differ only in which
    securities they refuse. The tolerance therefore has to keep every rung's worst case under the
    next rung's *nominal* reach, and the top rung -- which has no next -- under a trading year.
    """
    reaches = [definition.lookback_sessions for definition in MOMENTA]
    spans = [definition.max_window_sessions for definition in MOMENTA]

    assert reaches == sorted(reaches) and len(set(reaches)) == len(reaches)
    for span, next_reach in zip(spans, reaches[1:], strict=False):
        assert span is not None and next_reach is not None and span < next_reach
    assert spans[-1] is not None and spans[-1] < SHORTEST_MEASURED_A_SHARE_YEAR


def test_the_directions_are_the_familys_two_priors_and_are_declared_not_inferred() -> None:
    """Momentum is `higher_is_better` and the short reversal is `lower_is_better`.

    Both are the family's conventional prior and neither is a finding: nothing in this repository
    has measured an information coefficient for any factor, and each note says so in the words
    `REVERSAL_1D_NOTE` established. What this test holds is that the two are *opposite*, which is
    the one thing about them that is not a prior -- a family that declared its momentum and its
    reversal the same way round would be claiming the same cross-sectional ordering twice.
    """
    assert {definition.direction for definition in MOMENTA} == {"higher_is_better"}
    assert REVERSAL_5_SESSIONS.direction == "lower_is_better"


# --- the arithmetic ---------------------------------------------------------------------------


def test_each_evaluator_is_the_product_of_its_own_windows_published_returns() -> None:
    """The value, as a number, derived from the path rather than compared to itself.

    `_evaluate` slices the probe path the way the engine slices a security's sessions, and the
    expectation is built from `GROWTH` directly -- so an evaluator that read the wrong end of its
    window, or dropped the wrong count, produces a different number here rather than a `None`.
    """
    assert _evaluate(MOMENTUM_20_SESSIONS, _momentum_sessions) == pytest.approx(
        _product(GROWTH[-25:-5]), rel=1e-12
    )
    assert _evaluate(MOMENTUM_60_SESSIONS, _momentum_sessions) == pytest.approx(
        _product(GROWTH[-65:-5]), rel=1e-12
    )
    assert _evaluate(MOMENTUM_120_SESSIONS, _momentum_sessions) == pytest.approx(
        _product(GROWTH[-125:-5]), rel=1e-12
    )
    assert _evaluate(REVERSAL_5_SESSIONS, _reversal_5_sessions) == pytest.approx(
        _product(GROWTH[-5:]), rel=1e-12
    )


def test_a_momentum_reads_the_oldest_sessions_of_its_window_and_not_the_newest() -> None:
    """The skip, driven from both sides on one path.

    Moving the five sessions a momentum declares and declines to read must not move its value;
    moving one it does read must. A single-sided test passes for an evaluator that reads the whole
    window, and the other side passes for one that reads nothing at all.
    """
    closes, pre_closes = _prices()
    reach = 25
    base = _momentum_sessions(_window(closes[-reach:], pre_closes[-reach:]))
    assert base is not None

    disturbed_growth = list(GROWTH)
    for index in range(len(disturbed_growth) - SHORT_REVERSAL_SESSIONS, len(disturbed_growth)):
        disturbed_growth[index] *= 1.5
    newest_moved, newest_pre = _prices(tuple(disturbed_growth))

    older_growth = list(GROWTH)
    older_growth[-reach] *= 1.5
    oldest_moved, oldest_pre = _prices(tuple(older_growth))

    assert base == pytest.approx(
        _momentum_sessions(_window(newest_moved[-reach:], newest_pre[-reach:])), rel=1e-12
    )
    shifted = _momentum_sessions(_window(oldest_moved[-reach:], oldest_pre[-reach:]))
    assert shifted is not None and abs(shifted - base) > 0.1


def test_an_unskipped_momentum_is_its_reversal_times_the_rest_of_its_own_window() -> None:
    """Why the skip is five and not a matter of taste, as an identity rather than an argument.

    Both factors are products of the same per-session growth factors, so a 20-session momentum
    that did **not** skip would satisfy `1 + m20 == (1 + m15) * (1 + r5)` to floating point --
    the reversal would be an exact algebraic factor of it, and `V2-P3-008`'s redundancy analysis
    would be reporting arithmetic. The shipped definitions do skip, so the identity holds for the
    unskipped variant this test constructs and for nothing that ships.
    """
    closes, pre_closes = _prices()
    unskipped = _window(closes[-20:], pre_closes[-20:])

    whole = _compounded_session_return(unskipped, skip=0)
    older = _compounded_session_return(unskipped, skip=SHORT_REVERSAL_SESSIONS)
    recent = _reversal_5_sessions(_window(closes[-5:], pre_closes[-5:]))

    assert whole is not None and older is not None and recent is not None
    assert 1.0 + whole == pytest.approx((1.0 + older) * (1.0 + recent), rel=1e-12)
    assert abs(whole - older) > 1e-3


def test_no_two_of_these_factors_answer_with_the_same_number_on_one_price_path() -> None:
    """The lesson `V2-P3-004`'s review had to learn twice, applied to four windows over one path.

    A fixture on which two of the four coincide would let a test assert both and separate neither,
    which is exactly what a single-industry neutralisation fixture did. The floor is a hundredth
    of a return, well above any rounding, so the separation is a property of the definitions
    rather than of the last bits.
    """
    values = {
        MOMENTUM_20_SESSIONS.key: _evaluate(MOMENTUM_20_SESSIONS, _momentum_sessions),
        MOMENTUM_60_SESSIONS.key: _evaluate(MOMENTUM_60_SESSIONS, _momentum_sessions),
        MOMENTUM_120_SESSIONS.key: _evaluate(MOMENTUM_120_SESSIONS, _momentum_sessions),
        REVERSAL_5_SESSIONS.key: _evaluate(REVERSAL_5_SESSIONS, _reversal_5_sessions),
    }

    assert len(values) == 4
    ordered = sorted(values.values())
    gaps = [right - left for left, right in pairwise(ordered)]
    assert min(gaps) > 0.01, values


# --- the return path -----------------------------------------------------------------------------

EX_RIGHTS_PREVIOUS_CLOSE: Final[float] = 11.30
EX_RIGHTS_CLOSE: Final[float] = 11.24
EX_RIGHTS_PRE_CLOSE: Final[float] = 10.94
"""`000001.SZ` across its 2026-06-12 ex-dividend date, from `domain/daily_prices.py`'s own table.

The 11th closed at 11.30; the 12th closed at 11.24 with a published `pre_close` of 10.94, because
`pre_close` is the previous close **restated for that morning's corporate action**. Those three
numbers are what make the two return paths answer with different signs, and they are quoted from
the module that measured them rather than invented here.
"""

PUBLISHED_RETURN: Final[float] = 0.027422303
CLOSE_TO_CLOSE_RETURN: Final[float] = -0.005309735
"""`close / pre_close - 1` and `close / prev_close - 1` on the row above: +2.7422% and -0.5310%.

`domain/daily_prices.py` prints both and calls the second "wrong, and the *sign* is wrong". A
momentum is this quantity compounded, so a family built on the second path compounds the error.
"""


def test_the_published_return_path_and_the_close_to_close_path_disagree_in_sign() -> None:
    """The measurement `V2-P3-012` had to get right, driven on the family's own evaluator.

    `_reversal_5_sessions` over a window whose last session is `000001.SZ`'s real ex-dividend day
    answers `+2.7422%`. The arithmetic `REVERSAL_1D` performs over the same two closes answers
    `-0.5310%`. They are not close and they are not the same sign, so this is not a tolerance
    question -- it is which of two quantities the family is defined as.
    """
    flat = (EX_RIGHTS_PREVIOUS_CLOSE,) * 4
    closes = (*flat, EX_RIGHTS_CLOSE)
    pre_closes = (*flat, EX_RIGHTS_PRE_CLOSE)

    published = _reversal_5_sessions(_window(closes, pre_closes))
    naive = closes[-1] / closes[-2] - 1.0

    assert published == pytest.approx(PUBLISHED_RETURN, abs=1e-8)
    assert naive == pytest.approx(CLOSE_TO_CLOSE_RETURN, abs=1e-8)
    assert published > 0.0 > naive
    assert abs(published - naive) > 0.03


def test_a_zero_pre_close_anywhere_in_the_window_is_undefined_rather_than_a_division() -> None:
    """`undefined_value` has to be a branch that runs, and it has to run from anywhere.

    The guard is on the denominator of *every* session of the window rather than on the newest,
    which is the difference between a 120-session factor with one check and one with a hundred and
    twenty. Driven at both ends and in the middle, because a guard written as `previous[-1] == 0`
    passes a test that only ever zeroes the last one.
    """
    closes, pre_closes = _prices()
    reach = 25
    for position in (0, 12, reach - SHORT_REVERSAL_SESSIONS - 1):
        holed = list(pre_closes[-reach:])
        holed[position] = 0.0
        assert _momentum_sessions(_window(closes[-reach:], tuple(holed))) is None

    inside_the_skip = list(pre_closes[-reach:])
    inside_the_skip[-1] = 0.0
    assert _momentum_sessions(_window(closes[-reach:], tuple(inside_the_skip))) is not None
