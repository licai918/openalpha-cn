"""Correlation and redundancy (`V2-P3-008`): the arithmetic, the structure and the market.

Five properties this file exists to hold, each of which is a place a redundancy report silently
turns into something else:

1. **An arithmetic identity is reported as arithmetic and never as a finding.** `V2-P3-012` named
   this issue by name when it skipped five sessions so that `1 + m20 == (1 + m15) * (1 + r5)`
   would not bind a shipping pair. That identity is declared here **once** and evaluated twice --
   against an unskipped momentum, where it is `verified` at the last bits, and against the
   *shipped* `momentum_20_sessions` on the same price paths, where it is `refuted` and the pair
   falls through to the empirical ladder. Both directions, on the real evaluators.
2. **A structural fact is not an empirical one, in both directions.** The strongest structural
   signal available -- identical `required_fields` -- is carried by 16 of the 171 shipped pairs
   including one `V2-P3-012` built to be disjoint, and the pair the task brief called "shares TTM
   net profit" shares no qualified column at all. Both are asserted against the live registry, so
   neither can drift into prose.
3. **The threshold is the caller's and the lockstep boundary is not a threshold.** A spec with no
   `redundancy_threshold` does not construct, and the same point comes out `redundant` and
   `distinct` under two declarations of it. `round(abs(r), 15) == 1.0` is measured to fire on 200
   of 200 exact images and 0 of 200 near-perfect ones, so it is a rounding boundary rather than a
   line somebody chose.
4. **Every number reported has a fixture that separates it from its neighbour.** `V2-P3-005`'s
   review found seven fields whose per-field tampering nothing noticed at 100% line coverage, so
   the census cells, the two means, the two counts and the two correlations are each asserted on
   a fixture where no two of them share a value.
5. **A declaration nothing evaluated is refused rather than believed.** `RedundancyPoint` cannot
   be constructed as `arithmetic` over an identity that came out `refuted` or `unevaluable`, and
   the refusal is driven rather than described.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Final

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.factor_ic import (
    MINIMUM_IC_AS_OFS,
    MINIMUM_IC_SECURITIES,
    TIER_ADMITTED_CODES,
    TIER_COVERAGE_ORDER,
    FactorICSpec,
    FactorTier,
    ICMethod,
    ICPoint,
    _pearson,
    average_ranks,
)
from openalpha_cn.backtest.factor_redundancy import (
    IDENTITY_COVERAGE_CODES,
    IDENTITY_COVERAGE_ORDER,
    KNOWN_REDUNDANCY_LIMITATIONS,
    LOCKSTEP_DECIMAL_PLACES,
    MAXIMUM_REDUNDANCY_AS_OFS,
    MAXIMUM_REDUNDANCY_SECURITIES,
    MINIMUM_REDUNDANCY_SECURITIES,
    PAIR_COVERAGE_CODES,
    PAIR_COVERAGE_ORDER,
    REDUNDANCY_LIMITATION_CODES,
    REDUNDANCY_VERDICT_CODES,
    REDUNDANCY_VERDICT_ORDER,
    SHARED_INPUT_CODES,
    SHARED_INPUT_ORDER,
    SUMMARY_COVERAGE_CODES,
    FactorIdentity,
    FactorRedundancyError,
    FactorVector,
    ICSeriesCorrelation,
    IdentityCheck,
    RedundancyPoint,
    RedundancySpec,
    RedundancyStudy,
    RedundancySummary,
    SharedInputs,
    correlate_cross_section,
    factor_vector,
    shared_inputs,
    verify_identity,
)
from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PRE_CLOSE_COLUMN,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorDirection, FactorField
from openalpha_cn.panel_factors import (
    ACCRUALS_TTM,
    BOOK_TO_PRICE,
    DOWNSIDE_VOL_60,
    EARNINGS_YIELD_TTM,
    FACTOR_DEFINITIONS,
    MOMENTUM_20_SESSIONS,
    MOMENTUM_120_SESSIONS,
    RETURN_ON_EQUITY_TTM,
    RETURN_VOL_60,
    REVENUE_YOY,
    REVENUE_YOY_ACCELERATION,
    REVERSAL_5_SESSIONS,
    SHORT_REVERSAL_SESSIONS,
    TURNOVER_60,
    FactorWindow,
    _compounded_session_return,
    _momentum_sessions,
    _reversal_5_sessions,
)

AS_OF: Final[datetime] = datetime(2026, 6, 30, 8, 0, tzinfo=UTC)

SPEC: Final[RedundancySpec] = RedundancySpec(
    method="spearman", min_securities=4, min_as_ofs=2, redundancy_threshold=0.8
)
"""The declaration every test that does not vary one of these four fields is run under.

`0.8` is not a calibration and nothing in this repository claims it is: it is the value the
tests below declare so that `test_the_verdict_is_decided_by_the_declared_threshold_and_nothing
_else` can move it and watch the verdict move. See `RedundancySpec.redundancy_threshold`.
"""


def _definition(
    key: str,
    *,
    direction: FactorDirection = "higher_is_better",
    dataset: str = DAILY_DATASET,
    column: str = CLOSE_COLUMN,
) -> FactorDefinition:
    """A one-column session factor, so a test can vary a key, a direction or an input alone."""
    return FactorDefinition(
        key=key,
        version=1,
        family="momentum_reversal",
        direction=direction,
        required_fields=(FactorField(dataset=dataset, column=column),),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )


UP: Final[FactorDefinition] = _definition("probe_up")
DOWN: Final[FactorDefinition] = _definition("probe_down", direction="lower_is_better")
OTHER_UP: Final[FactorDefinition] = _definition(
    "probe_other_up", dataset=DAILY_BASIC_DATASET, column="turnover_rate"
)
OTHER_DOWN: Final[FactorDefinition] = _definition(
    "probe_other_down",
    direction="lower_is_better",
    dataset=DAILY_BASIC_DATASET,
    column="turnover_rate",
)
THIRD: Final[FactorDefinition] = _definition("probe_third", column=PRE_CLOSE_COLUMN)
"""Five probe definitions, and the *inputs* are laid out so the structural codes are separable.

`UP` and `DOWN` read `daily.close`; `OTHER_UP` and `OTHER_DOWN` read
`daily_basic.turnover_rate`; `THIRD` reads `daily.pre_close`. So `(UP, OTHER_UP)` is
`disjoint_inputs`, `(UP, THIRD)` is `shared_dataset_only`, and `(UP, DOWN)` is
`identical_inputs` -- three different structural readings over one set of probes, which is what
lets a test assert that the code and the verdict move independently.
"""

ADMITTED_CODE: Final[Mapping[FactorTier, str]] = MappingProxyType(
    {"raw": "computed", "processed": "processed", "neutralized": "neutralized"}
)
"""The one code each tier admits, restated here so a helper can build a vector on any tier.

Not imported from `TIER_ADMITTED_CODES` even though that table holds exactly these three: a
fixture that read the table under test would relabel itself the moment the table changed, and the
whole point of `factor_vector` inheriting that table is that this file can disagree with it.
`test_the_probe_codes_are_the_codes_each_tier_admits` reconciles the two.
"""


def _vector(
    definition: FactorDefinition,
    values: Mapping[str, float],
    *,
    tier: FactorTier = "raw",
    as_of: datetime = AS_OF,
    extra_rows: Sequence[tuple[str, float | None, str]] = (),
) -> FactorVector:
    """A vector of admitted rows on `tier`, plus any non-admitted rows a test wants counted."""
    rows = [(subject, value, ADMITTED_CODE[tier]) for subject, value in values.items()]
    return factor_vector(as_of=as_of, tier=tier, definition=definition, rows=[*rows, *extra_rows])


def _subjects(count: int) -> tuple[str, ...]:
    return tuple(f"{index:06d}.SZ" for index in range(count))


# --- the two arithmetic boundaries ------------------------------------------------------------


def test_three_names_cannot_rank_correlate_below_a_half_and_four_names_can_reach_zero() -> None:
    """Why `MINIMUM_REDUNDANCY_SECURITIES` is four where `MINIMUM_IC_SECURITIES` is three.

    Enumerated over every permutation rather than sampled, because the claim is about the whole
    attainable set and a sample can only ever say "we did not see one". At `n = 3` a rank
    correlation of two untied cross sections takes exactly four values, so **no** declaration of
    `redundancy_threshold` decides anything there: at or below 0.5 every pair is redundant, above
    it only the perfectly ordered pair is.

    Pearson is enumerated too, in the one direction that matters -- it reaches 0 at `n = 3`, so
    the floor is decided by the weaker of the two methods this module offers rather than by both.
    """
    attainable = {
        size: {
            round(_pearson(average_ranks(list(range(size))), average_ranks(list(order))), 12)
            for order in itertools.permutations(range(size))
        }
        for size in (3, 4)
    }

    assert attainable[3] == {-1.0, -0.5, 0.5, 1.0}
    assert min(abs(value) for value in attainable[3]) == 0.5
    assert 0.0 in attainable[4]
    assert _pearson([0.0, 1.0, 2.0], [0.0, 1.0, 0.0]) == 0.0
    assert MINIMUM_REDUNDANCY_SECURITIES == MINIMUM_IC_SECURITIES + 1 == 4


def test_the_lockstep_boundary_separates_an_exact_image_from_a_very_close_one() -> None:
    """`round(abs(r), 15) == 1.0` fires on every exact image and on nothing merely close.

    Both halves are needed and only together. A boundary that fires on the exact case might be
    firing on everything, and `factor_ic` already measured that plain `== 1.0` does **not** fire
    on the exact case often enough to be usable -- which is asserted here rather than quoted, so
    the day it stops being true this file goes red rather than the prose going stale.

    The near case is a vector against itself plus `N(0, 0.001)` noise on a unit scale: two
    genuinely different numbers that agree to six figures. If the boundary fired on that, every
    pair of a factor and its own winsorization would be reported as an undeclared identity.

    All five counts are asserted **by value** off a declared seed rather than as inequalities,
    because "fewer than 200 cleared plain equality" is satisfied by 199 and by 0, and those two
    are the opposite findings.
    """
    generator = random.Random(20260813)
    exact_affine: list[float] = []
    exact_monotone: list[float] = []
    nearly: list[float] = []
    for _trial in range(200):
        size = generator.randint(5, 60)
        xs = [generator.uniform(-1.0, 1.0) for _ in range(size)]
        exact_affine.append(abs(_pearson(xs, [3.5 * value - 0.25 for value in xs])))
        cubed = [value**3 + value for value in xs]
        exact_monotone.append(abs(_pearson(average_ranks(xs), average_ranks(cubed))))
        noisy = [value + generator.gauss(0.0, 0.001) for value in xs]
        nearly.append(abs(_pearson(xs, noisy)))

    assert LOCKSTEP_DECIMAL_PLACES == 15
    assert sum(round(value, LOCKSTEP_DECIMAL_PLACES) == 1.0 for value in exact_affine) == 200
    assert sum(round(value, LOCKSTEP_DECIMAL_PLACES) == 1.0 for value in exact_monotone) == 200
    assert sum(value == 1.0 for value in exact_affine) == 149
    assert sum(value == 1.0 for value in exact_monotone) == 153
    assert sum(round(value, LOCKSTEP_DECIMAL_PLACES) == 1.0 for value in nearly) == 0
    assert max(nearly) == 0.9999998220801101


def test_ranking_the_whole_market_and_restricting_is_not_ranking_the_intersection() -> None:
    """Why `_correlate` ranks inside each pair's own intersection and pays 3.8x for it.

    Ranking each factor once and correlating the stored ranks is the obvious optimisation and it
    is **wrong** the moment two factors are admitted for different subjects: a rank is a position
    within a set, so the ranks of a subset are not the subset of the ranks. Asserted on every one
    of 200 trials rather than on one, and with a floor under the size of the disagreement -- a
    shortcut that was wrong in the last bits would be a rounding question and not a defect.
    """
    generator = random.Random(20260814)
    disagreements = 0
    largest = 0.0
    for _trial in range(200):
        size = 40
        xs = [generator.uniform(-1.0, 1.0) for _ in range(size)]
        ys = [generator.uniform(-1.0, 1.0) for _ in range(size)]
        keep = sorted(generator.sample(range(size), 25))
        honest = _pearson(
            average_ranks([xs[index] for index in keep]),
            average_ranks([ys[index] for index in keep]),
        )
        whole_x, whole_y = average_ranks(xs), average_ranks(ys)
        shortcut = _pearson([whole_x[index] for index in keep], [whole_y[index] for index in keep])
        disagreements += honest != shortcut
        largest = max(largest, abs(honest - shortcut))

    assert disagreements == 200
    assert largest > 0.05


# --- the identity `V2-P3-012` named ------------------------------------------------------------

SESSIONS: Final[int] = 25
"""`MOMENTUM_20_SESSIONS.lookback_sessions` -- the widest window these paths have to form."""


def _growth_path(seed: int) -> tuple[float, ...]:
    """One security's per-session `close / pre_close`, drawn independently and seeded.

    Independent across securities **and across sessions**, which is what makes this fixture say
    something rather than restate itself. `test_factor_momentum_reversal_rules.GROWTH` is one
    deterministic sawtooth every security shares, and on a shared path every window correlates
    with every other for a reason that is about the path. With independent session returns two
    windows that multiply **disjoint** sessions are independent and two that overlap are not, so
    the difference `V2-P3-012`'s five-session skip makes shows up in the correlation instead of
    being drowned by the fixture.
    """
    generator = random.Random(70_000 + seed)
    return tuple(1.0 + generator.gauss(0.0, 0.02) for _index in range(SESSIONS))


def _closes(growth: tuple[float, ...]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    closes: list[float] = []
    pre_closes: list[float] = []
    price = 10.0
    for factor in growth:
        pre_closes.append(price)
        price *= factor
        closes.append(price)
    return tuple(closes), tuple(pre_closes)


def _price_window(closes: tuple[float, ...], pre_closes: tuple[float, ...]) -> FactorWindow:
    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=tuple(date(2026, 1, 1) for _ in closes),
        periods=(),
        values=MappingProxyType(
            {
                (DAILY_DATASET, CLOSE_COLUMN): closes,
                (DAILY_DATASET, PRE_CLOSE_COLUMN): pre_closes,
            }
        ),
    )


def _momentum_family(count: int) -> dict[str, dict[str, float]]:
    """Four numbers per security off one price path: `012`'s three, and the shipped momentum.

    - `unskipped_m20` -- `prod(close / pre_close) - 1` over the newest 20 sessions, which is what
      `MOMENTUM_20_SESSIONS` would compute if it did not skip.
    - `m15` -- the same over those 20 sessions less their newest five.
    - `reversal_5_sessions` -- the shipped evaluator over the newest five sessions.
    - `momentum_20_sessions` -- the **shipped** evaluator over its own declared 25-session window,
      which skips the newest five.

    All four come from the shipped functions in `panel_factors` rather than from arithmetic
    restated here, so a change to either evaluator moves these numbers.
    """
    values: dict[str, dict[str, float]] = {
        "unskipped_m20": {},
        "m15": {},
        "reversal_5_sessions": {},
        "momentum_20_sessions": {},
    }
    for index, subject in enumerate(_subjects(count)):
        closes, pre_closes = _closes(_growth_path(index))
        twenty = _price_window(closes[-20:], pre_closes[-20:])
        five = _price_window(closes[-5:], pre_closes[-5:])
        twenty_five = _price_window(closes, pre_closes)
        whole = _compounded_session_return(twenty, skip=0)
        older = _compounded_session_return(twenty, skip=SHORT_REVERSAL_SESSIONS)
        recent = _reversal_5_sessions(five)
        shipped = _momentum_sessions(twenty_five)
        assert whole is not None and older is not None
        assert recent is not None and shipped is not None
        values["unskipped_m20"][subject] = whole
        values["m15"][subject] = older
        values["reversal_5_sessions"][subject] = recent
        values["momentum_20_sessions"][subject] = shipped
    return values


UNSKIPPED_M20: Final[FactorDefinition] = _definition("unskipped_m20")
M15: Final[FactorDefinition] = _definition("m15")
SHIPPED_M20: Final[FactorDefinition] = _definition("momentum_20_sessions")
R5: Final[FactorDefinition] = _definition("reversal_5_sessions", direction="lower_is_better")

COMPOUNDING_IDENTITY: Final[FactorIdentity] = FactorIdentity(
    code="an_unskipped_momentum_is_its_reversal_times_the_rest_of_its_own_window",
    members=("momentum_20_sessions", "m15", "reversal_5_sessions"),
    tolerance=1e-12,
    detail=(
        "1 + m20 == (1 + m15) * (1 + r5): both factors are products of the same per-session "
        "growth factors, so an unskipped 20-session momentum has the 5-session reversal as an "
        "exact algebraic factor. V2-P3-012 skipped five sessions precisely so that no SHIPPING "
        "pair satisfies this."
    ),
    residual=lambda values: (
        (1.0 + values["momentum_20_sessions"])
        - (1.0 + values["m15"]) * (1.0 + values["reversal_5_sessions"])
    ),
)
"""`V2-P3-012`'s identity, declared once and evaluated against two different `momentum_20_sessions`.

Declared under the *shipped* key so that the same declaration reaches both readings: the
unskipped variant is filed under that key in one study and the shipped evaluator's output under
it in the other, and nothing but the numbers differs between the two evaluations.
"""


def test_an_unskipped_momentums_identity_is_verified_and_the_shipped_pair_refutes_it() -> None:
    """The core of `V2-P3-008`: arithmetic reported as arithmetic, on the real evaluators.

    One declaration, two evaluations, and the whole issue in the difference between them:

    - Against an **unskipped** momentum the identity is `verified` at a residual of `4.4e-16`, and
      the pair `(momentum_20_sessions, reversal_5_sessions)` is filed `arithmetic`. Its rank
      correlation on this fixture is `+0.26` -- a number a report would otherwise have printed
      beside eighteen empirical ones with nothing distinguishing it.
    - Against the **shipped** momentum, on the same price paths and under the same declaration, it
      is `refuted` at `1.7e-01`, fifteen orders of magnitude larger, and the pair falls through to
      the empirical ladder at a rank correlation of `-0.03`.

    Both magnitudes are asserted with floors rather than only their codes. A refutation that came
    in at 1e-11 would say the tolerance was mis-declared rather than that the skip did anything,
    and a correlation gap that was not there would say the fixture had made the two windows
    dependent by construction -- the session returns are drawn independently so that they are not.
    """
    family = _momentum_family(40)
    unskipped = {
        "momentum_20_sessions": _vector(SHIPPED_M20, family["unskipped_m20"]),
        "m15": _vector(M15, family["m15"]),
        "reversal_5_sessions": _vector(R5, family["reversal_5_sessions"]),
    }
    shipped = {
        **unskipped,
        "momentum_20_sessions": _vector(SHIPPED_M20, family["momentum_20_sessions"]),
    }
    study = RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY])

    verified = verify_identity(COMPOUNDING_IDENTITY, unskipped)
    refuted = verify_identity(COMPOUNDING_IDENTITY, shipped)
    arithmetic = study.measure(
        left=unskipped["momentum_20_sessions"],
        right=unskipped["reversal_5_sessions"],
        vectors=unskipped,
    )
    empirical = study.measure(
        left=shipped["momentum_20_sessions"],
        right=shipped["reversal_5_sessions"],
        vectors=shipped,
    )

    assert (verified.coverage, refuted.coverage) == ("verified", "refuted")
    assert verified.code == refuted.code == COMPOUNDING_IDENTITY.code
    assert verified.members == refuted.members == COMPOUNDING_IDENTITY.members
    assert verified.tolerance == COMPOUNDING_IDENTITY.tolerance
    assert verified.subject_count == refuted.subject_count == 40
    assert (arithmetic.left_key, arithmetic.right_key) == (SHIPPED_M20.key, R5.key)
    assert (empirical.left_key, empirical.right_key) == (SHIPPED_M20.key, R5.key)
    assert arithmetic.identity is not None and arithmetic.identity.code == COMPOUNDING_IDENTITY.code
    assert verified.max_abs_residual is not None
    assert 0.0 <= verified.max_abs_residual < 1e-14
    assert refuted.max_abs_residual is not None
    assert 1e-3 < refuted.max_abs_residual < 1.0
    assert refuted.max_abs_residual > verified.max_abs_residual * 1e8
    assert arithmetic.verdict == "arithmetic"
    assert empirical.verdict != "arithmetic"
    assert empirical.identity is not None and empirical.identity.coverage == "refuted"
    assert arithmetic.raw_correlation is not None and empirical.raw_correlation is not None
    assert abs(arithmetic.raw_correlation) > 0.2
    assert abs(empirical.raw_correlation) < 0.1
    assert arithmetic.shared_input_code == empirical.shared_input_code == "identical_inputs"


DIVERGING_SUBJECT: Final[tuple[int, ...]] = (0, 1, 2)
"""Which of three subjects carries the identity's only non-zero residual, as a parameter.

The whole point of the parameter: a fixture in which the outlier is always last cannot tell
`max(largest, ...)` from "the last one wins", and one in which every residual is equal cannot tell
`max` from `min`. Three positions over three subjects is the smallest table that separates all
three readings, and `verify_identity` sorts its subjects, so position here is position there.
"""

DIVERGENCE: Final[float] = 0.25
"""The one non-zero residual, exact in binary so the assertion can be `==` rather than an interval.

`0.25` is `2**-2`, and the residual is a plain subtraction of two exactly-representable values, so
`max_abs_residual` is this number and not a neighbourhood of it. A magnitude rather than a bound is
the whole lesson of the finding this test answers: the suite already asserted `1e-3 < residual <
1.0`, which is true of the maximum, of the minimum and of the mean.
"""


def _difference_identity(tolerance: float) -> FactorIdentity:
    """`a - b`, declared over two probe keys, with the tolerance the caller wants to test."""
    return FactorIdentity(
        code="the_two_probes_agree_exactly",
        members=(UP.key, OTHER_UP.key),
        tolerance=tolerance,
        detail=(
            "A probe identity whose residual is one subtraction, so the residual at each subject "
            "is chosen by the fixture rather than emerging from an evaluator. Declared here and "
            "not shipped: COMPOUNDING_IDENTITY is the real one, and its residuals are the ~1e-16 "
            "noise of a compounding round-trip, which cannot place a known number at a known "
            "subject."
        ),
        residual=lambda values: values[UP.key] - values[OTHER_UP.key],
    )


@pytest.mark.parametrize("diverging", DIVERGING_SUBJECT)
def test_the_reported_residual_is_the_largest_one_and_not_whichever_came_last(
    diverging: int,
) -> None:
    """`max_abs_residual` is a **maximum**, driven where the three plausible readings differ.

    This module's whole defence against "a declared safety property that nothing measures" is
    `verify_identity`, and the number it reports is the one a reader uses to tell "the declaration
    is wrong" from "the declaration is wrong by 4e-4" -- `IdentityCheck`'s own docstring says so.
    Until this test, no fixture in the suite produced two residuals that a reader could tell apart:
    every assertion was an interval or a ratio over 40 independently-seeded securities, so an
    identity that broke on **one** security could be stamped `verified` on the strength of the
    other 39. Replacing the accumulator with `min` left the whole suite green.

    Two subjects sit exactly on the identity and one is off it by `DIVERGENCE`, and the parameter
    moves which. Under `min` the answer is `0.0` at every position; under "the last one wins" it is
    right at position 2 and `0.0` at the other two; only a maximum is right at all three. The
    coverage moves with it, so this is also the statement that a break at one security refutes the
    identity for the cross section rather than being averaged away.
    """
    subjects = _subjects(3)
    left = dict.fromkeys(subjects, 1.0)
    right = {
        name: 1.0 - (DIVERGENCE if index == diverging else 0.0)
        for index, name in enumerate(subjects)
    }

    check = verify_identity(
        _difference_identity(1e-9),
        {UP.key: _vector(UP, left), OTHER_UP.key: _vector(OTHER_UP, right)},
    )

    assert check.subject_count == 3
    assert check.max_abs_residual == DIVERGENCE
    assert check.coverage == "refuted"


def test_the_declared_tolerance_is_inclusive_at_exactly_the_residual_it_names() -> None:
    """`<=` and not `<`, on a residual that lands **on** the tolerance rather than near it.

    `FactorIdentity.__post_init__` refuses a tolerance of zero in these words: "a residual is
    compared with `<=`, so a non-positive tolerance would refuse the exact case". That sentence
    makes the inclusiveness load-bearing -- it is the reason a whole declaration shape is
    unavailable -- and no fixture reached it: the suite's residuals are `1e-16` noise against a
    `1e-12` tolerance, four orders of magnitude clear of the line in a direction both comparisons
    agree on.

    `DIVERGENCE` is exact in binary, so declaring it as the tolerance is a residual exactly equal
    to it. `math.nextafter` towards zero is the smallest possible step to the other side, so the
    pair of assertions is the comparison itself rather than a neighbourhood of it.

    The `IdentityCheck` contract's own twin of the comparison is exercised by the same call: the
    model refuses a `verified` code whose residual exceeds its tolerance, so a `<` there would make
    the first branch raise instead of returning.
    """
    subjects = _subjects(3)
    left = dict.fromkeys(subjects, 1.0)
    right = {**dict.fromkeys(subjects, 1.0), subjects[1]: 1.0 - DIVERGENCE}
    vectors = {UP.key: _vector(UP, left), OTHER_UP.key: _vector(OTHER_UP, right)}

    exact = verify_identity(_difference_identity(DIVERGENCE), vectors)
    inside = verify_identity(_difference_identity(math.nextafter(DIVERGENCE, 0.0)), vectors)

    assert exact.max_abs_residual == inside.max_abs_residual == DIVERGENCE
    assert exact.coverage == "verified"
    assert inside.coverage == "refuted"


def test_a_verdict_of_arithmetic_needs_an_identity_that_was_evaluated_and_held() -> None:
    """The rule the point contract enforces, driven on all three identity codes.

    Without it a report could file a pair as arithmetic on the strength of a declaration nothing
    measured, which is the exact shape of the thirteen Critical findings this repository has
    taken. `unevaluable` is included because it is the quiet one: an identity over a set of
    securities none of which carried every member has *not* failed, and treating "nothing
    contradicted it" as "it held" is how a vacuous check passes.
    """
    family = _momentum_family(40)
    disjoint = {
        "momentum_20_sessions": _vector(SHIPPED_M20, family["unskipped_m20"]),
        "m15": _vector(M15, {"999999.SZ": 0.1, "999998.SZ": 0.2}),
        "reversal_5_sessions": _vector(R5, family["reversal_5_sessions"]),
    }

    check = verify_identity(COMPOUNDING_IDENTITY, disjoint)
    point = correlate_cross_section(
        left=disjoint["momentum_20_sessions"],
        right=disjoint["reversal_5_sessions"],
        spec=SPEC,
        identity=check,
    )
    payload = point.model_dump()

    assert check.coverage == "unevaluable"
    assert check.subject_count == 0 and check.max_abs_residual is None
    assert point.verdict != "arithmetic"
    for identity in (
        {**check.model_dump(), "coverage": "unevaluable"},
        {**check.model_dump(), "coverage": "refuted", "subject_count": 40, "max_abs_residual": 1.0},
    ):
        with pytest.raises(ValidationError, match="a pair is arithmetic only when a declared"):
            RedundancyPoint.model_validate(
                {**payload, "verdict": "arithmetic", "identity": identity}
            )
    assert (
        RedundancyPoint.model_validate(
            {
                **payload,
                "verdict": "arithmetic",
                "identity": {
                    **check.model_dump(),
                    "coverage": "verified",
                    "subject_count": 40,
                    "max_abs_residual": 1e-15,
                },
            }
        ).verdict
        == "arithmetic"
    )


def test_a_point_cannot_be_arithmetic_with_no_identity_at_all() -> None:
    """The fourth case the check above cannot reach: `identity=None` beside an arithmetic verdict.

    Separate from the loop above because the interesting failure is the one where a caller
    supplies no identity at all, which is what every pair with no declaration looks like.
    """
    family = _momentum_family(40)
    point = correlate_cross_section(
        left=_vector(SHIPPED_M20, family["unskipped_m20"]),
        right=_vector(R5, family["reversal_5_sessions"]),
        spec=SPEC,
    )

    assert point.identity is None and point.verdict != "arithmetic"
    with pytest.raises(ValidationError, match="a pair is arithmetic only when a declared"):
        RedundancyPoint.model_validate({**point.model_dump(), "verdict": "arithmetic"})


def test_verify_identity_refuses_an_unsupplied_member_and_a_residual_that_is_not_a_number() -> None:
    """Two malformed *questions*, each refused rather than answered with a plausible code.

    A member with no vector would otherwise evaluate as `unevaluable`, which reads as "the market
    had nobody" when the truth is "the caller did not finish asking". A residual that comes out
    `nan` says the declared arithmetic broke down at that security -- a division by a zero the
    declaration did not guard -- which is a defect in the declaration and not a refutation of it,
    and reporting it as `refuted` would send a reader looking at the evaluator.
    """
    family = _momentum_family(40)
    two_of_three = {
        "momentum_20_sessions": _vector(SHIPPED_M20, family["unskipped_m20"]),
        "reversal_5_sessions": _vector(R5, family["reversal_5_sessions"]),
    }
    broken = FactorIdentity(
        code="a_residual_that_reaches_a_nan_the_declaration_did_not_guard",
        members=("probe_up", "probe_down"),
        tolerance=1e-9,
        detail="0.0 * inf is a nan, which is what an unguarded declaration reaches",
        residual=lambda values: values["probe_up"] * math.inf * values["probe_down"],
    )
    zeroed = {
        "probe_up": _vector(UP, dict(zip(_subjects(4), (1.0, 2.0, 3.0, 4.0), strict=True))),
        "probe_down": _vector(DOWN, dict(zip(_subjects(4), (1.0, 2.0, 0.0, 4.0), strict=True))),
    }

    with pytest.raises(FactorRedundancyError, match=r"no vector was offered for \['m15'\]"):
        verify_identity(COMPOUNDING_IDENTITY, two_of_three)
    with pytest.raises(FactorRedundancyError, match="the declared arithmetic broke down"):
        verify_identity(broken, zeroed)


def test_an_identity_must_name_both_of_a_pair_and_a_study_refuses_two_over_one_pair() -> None:
    """`relates` is `both` and not `either`, and two declarations over one pair are refused.

    An identity naming one of a pair says nothing about that pair, so attaching it would put an
    `arithmetic` verdict on a finding. Two identities naming one pair are two claims about one
    arithmetic, and taking the first would make the verdict depend on the order a caller
    assembled a tuple -- the same "table order decides an answer" shape `FactorRegistry` refuses
    a duplicate key for.
    """
    family = _momentum_family(40)
    left = _vector(SHIPPED_M20, family["unskipped_m20"])
    right = _vector(R5, family["reversal_5_sessions"])
    partial = FactorIdentity(
        code="names_only_one_side",
        members=("momentum_20_sessions", "m15"),
        tolerance=1e-12,
        detail="relates the momentum to a factor that is not the pair's other side",
        residual=lambda values: values["momentum_20_sessions"] - values["m15"],
    )
    study = RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY, partial])

    assert COMPOUNDING_IDENTITY.relates("momentum_20_sessions", "reversal_5_sessions")
    assert not partial.relates("momentum_20_sessions", "reversal_5_sessions")
    assert study.identity_for("momentum_20_sessions", "reversal_5_sessions") is not None
    assert study.identity_for("m15", "probe_up") is None
    with pytest.raises(FactorRedundancyError, match="explains nothing about their correlation"):
        correlate_cross_section(
            left=left,
            right=right,
            spec=SPEC,
            identity=verify_identity(
                partial,
                {
                    "momentum_20_sessions": left,
                    "m15": _vector(M15, family["m15"]),
                },
            ),
        )
    with pytest.raises(FactorRedundancyError, match="two claims about one arithmetic"):
        RedundancyStudy(
            SPEC,
            identities=[
                COMPOUNDING_IDENTITY,
                FactorIdentity(
                    code="a_second_claim_over_the_same_pair",
                    members=("momentum_20_sessions", "reversal_5_sessions"),
                    tolerance=1e-12,
                    detail="a rival claim",
                    residual=lambda values: values["momentum_20_sessions"],
                ),
            ],
        ).identity_for("momentum_20_sessions", "reversal_5_sessions")


def test_a_declared_identity_refuses_its_own_malformed_shapes() -> None:
    """One member, a repeat, a non-positive tolerance and a blank code, each named apart."""
    common = {"detail": "probe", "residual": lambda values: 0.0, "tolerance": 1e-9}

    with pytest.raises(FactorRedundancyError, match="an identity must carry a code"):
        FactorIdentity(code="  ", members=("a", "b"), **common)  # type: ignore[arg-type]
    with pytest.raises(FactorRedundancyError, match="fewer than two factors"):
        FactorIdentity(code="one", members=("a",), **common)  # type: ignore[arg-type]
    with pytest.raises(FactorRedundancyError, match="with a repeat"):
        FactorIdentity(code="dup", members=("a", "a"), **common)  # type: ignore[arg-type]
    with pytest.raises(FactorRedundancyError, match="a non-positive tolerance"):
        FactorIdentity(
            code="zero",
            members=("a", "b"),
            tolerance=0.0,
            detail="probe",
            residual=lambda values: 0.0,
        )
    with pytest.raises(FactorRedundancyError, match="a non-finite one would accept everything"):
        FactorIdentity(
            code="inf",
            members=("a", "b"),
            tolerance=math.inf,
            detail="probe",
            residual=lambda values: 0.0,
        )


def test_a_study_refuses_a_repeated_identity_code_and_a_second_reading_of_one_factor() -> None:
    """Two declarations under one code, and two vectors for one factor inside one measurement.

    The second is the subtler one: if the identity's residual were evaluated from one reading of
    `momentum_20_sessions` and the correlation from another, the point would report an
    `arithmetic` verdict about numbers the correlation never saw.
    """
    family = _momentum_family(40)
    left = _vector(SHIPPED_M20, family["unskipped_m20"])
    duplicate = FactorIdentity(
        code=COMPOUNDING_IDENTITY.code,
        members=("m15", "reversal_5_sessions"),
        tolerance=1e-12,
        detail="a second declaration under one code",
        residual=lambda values: values["m15"] - values["reversal_5_sessions"],
    )

    with pytest.raises(FactorRedundancyError, match="is declared more than once"):
        RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY, duplicate])
    with pytest.raises(FactorRedundancyError, match="two different readings of one factor"):
        RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY]).measure(
            left=left,
            right=_vector(R5, family["reversal_5_sessions"]),
            vectors={
                "momentum_20_sessions": _vector(SHIPPED_M20, family["momentum_20_sessions"]),
                "m15": _vector(M15, family["m15"]),
            },
        )


# --- the structural half: what `required_fields` can and cannot say ---------------------------


def test_the_shared_input_table_over_every_shipped_definition() -> None:
    """The whole 210-pair structure, computed off the live registry rather than tabulated.

    This is the table-versus-implementation drift `V2-P3-002`'s lesson names: a hand-written list
    of "which factors share which columns" would go stale the first time a factor's
    `required_fields` moved, with nothing able to say so. Every number here is derived from
    `FACTOR_DEFINITIONS`, so a twenty-first factor changes them and this test is where that shows
    -- which is exactly what happened when `V2-P3-017` shipped the twentieth: 171 pairs became
    190 and every count below moved with it.

    The two totals are asserted together on purpose: 53 pairs share a **column** and 84 share a
    **dataset**, so a dataset-granularity reading would report 58% more overlap than there is and
    would call the value family and the quality family related by construction.

    `identical_inputs` did **not** move, and that is the interesting half of the twentieth
    factor's arrival: EPcut shares `daily_basic.total_mv` with the other three value factors and
    shares its numerator column with nobody, so it adds three `overlapping_inputs` pairs and no
    identical one. A factor that had merely been a rename of `earnings_yield_ttm` would have
    raised the first count instead.

    `V2-P3-016`'s twenty-first repeats that shape on a wider base and the arithmetic is worth
    stating rather than left to be inferred. `residual_vol_60` shares `daily.close` and
    `daily.pre_close` with the five momentum-and-reversal factors and with three of its own
    family, so it adds **eight** `overlapping_inputs` pairs (45 -> 53, and 76 -> 84 non-disjoint)
    while `identical_inputs` stays at 16 and `shared_dataset_only` stays at 31. It could not
    have been identical to anything: `index_daily.close` and `index_daily.pre_close` are declared
    by no other factor, which is what makes the market series a *new* input rather than a second
    reading of one already here. The count is out of this test's name for the reason
    `V2-P3-017` gave for inverting a set assertion elsewhere -- a name that has to be edited
    every time a factor ships makes the edit routine.
    """
    definitions = list(FACTOR_DEFINITIONS.definitions)
    pairs = list(itertools.combinations(definitions, 2))
    codes = [shared_inputs(left, right).code for left, right in pairs]
    counted = {code: codes.count(code) for code in SHARED_INPUT_ORDER}

    assert len(definitions) == 21 and len(pairs) == 210
    assert counted["identical_inputs"] == 16
    assert counted["identical_inputs"] + counted["overlapping_inputs"] == 53
    assert 210 - counted["disjoint_inputs"] == 84
    assert sum(counted.values()) == 210
    assert all(count > 0 for count in counted.values())


def test_identical_declared_inputs_are_not_evidence_that_two_factors_agree() -> None:
    """The direction that makes a structural fact unusable as a conclusion, measured.

    `momentum_120_sessions` and `reversal_5_sessions` declare the **same two columns** and nothing
    else, which is the loudest structural signal this module can emit -- and `V2-P3-012` built
    them so the sessions each multiplies are disjoint.

    The demonstration is two pairs under **one** structural code, so the code is measured not to
    decide the verdict rather than merely observed not to. Same fixture, same reversal, same
    `identical_inputs`: the shipped momentum comes out `distinct` and an exact monotone image of
    the reversal comes out `undeclared_lockstep`. A structural code that carried information
    about the verdict could not do that.
    """
    overlap = shared_inputs(MOMENTUM_120_SESSIONS, REVERSAL_5_SESSIONS)
    family = _momentum_family(40)
    reversal = _vector(R5, family["reversal_5_sessions"])
    disguised = _vector(
        M15, {name: value**3 + value for name, value in family["reversal_5_sessions"].items()}
    )

    apart = correlate_cross_section(
        left=_vector(SHIPPED_M20, family["momentum_20_sessions"]), right=reversal, spec=SPEC
    )
    together = correlate_cross_section(left=disguised, right=reversal, spec=SPEC)

    assert overlap.code == "identical_inputs"
    assert overlap.columns == ("daily.close", "daily.pre_close")
    assert apart.shared_input_code == "identical_inputs"
    assert apart.verdict == "distinct"
    assert apart.raw_correlation is not None and abs(apart.raw_correlation) < 0.1
    assert together.shared_input_code == "identical_inputs"
    assert together.verdict == "undeclared_lockstep"


def test_two_factors_that_share_no_column_can_still_be_redundant() -> None:
    """The other direction, and the correction of a claim about this pair that is not true.

    `return_on_equity_ttm` and `accruals_ttm` are described as sharing TTM net profit. They do
    **not** share a qualified column: the first reads `income.n_income_attr_p` and the second
    `income.n_income`, two profit lines `V2-P3-011` measured giving different growth rates on 139
    of 181 comparable pairs. What they share is the `income` and `balancesheet` *datasets*, which
    is `shared_dataset_only` and is why that code exists rather than being folded into
    `disjoint_inputs`.

    And a `disjoint_inputs` pair is shown to reach a `redundant` verdict on real numbers, so the
    structural code is measured to be uninformative about the verdict in this direction too.
    """
    quality = shared_inputs(RETURN_ON_EQUITY_TTM, ACCRUALS_TTM)
    subjects = _subjects(12)
    ordered = dict(zip(subjects, (float(index) for index in range(12)), strict=True))
    almost = dict(
        zip(subjects, (0.0, 2.0, 1.0, 3.0, 4.0, 5.0, 7.0, 6.0, 8.0, 9.0, 11.0, 10.0), strict=True)
    )
    point = correlate_cross_section(
        left=_vector(UP, ordered), right=_vector(OTHER_UP, almost), spec=SPEC
    )

    assert quality.code == "shared_dataset_only"
    assert quality.columns == ()
    assert quality.datasets == ("balancesheet", "income")
    assert shared_inputs(TURNOVER_60, REVENUE_YOY).code == "disjoint_inputs"
    assert point.shared_input_code == "disjoint_inputs"
    assert point.verdict == "redundant"
    assert point.raw_correlation is not None
    assert SPEC.redundancy_threshold <= point.raw_correlation < 1.0


def test_the_shared_columns_a_pair_reports_are_the_columns_and_not_the_datasets() -> None:
    """The actionable half: *which* column, so a reader knows it is the denominator.

    The four value factors are the case that makes this worth carrying -- they overlap in
    `daily_basic.total_mv` and in nothing else, and the finding "these four share their
    denominator" is a different sentence from "these four both read `daily_basic`". `V2-P3-017`'s
    EPcut sharpened it rather than diluted it: it reads a *third* statement dataset, so the
    family's only common column is still the one this assertion names.
    """
    subjects = _subjects(8)
    value = shared_inputs(EARNINGS_YIELD_TTM, BOOK_TO_PRICE)
    growth = shared_inputs(REVENUE_YOY, REVENUE_YOY_ACCELERATION)
    volatility = shared_inputs(RETURN_VOL_60, DOWNSIDE_VOL_60)
    point = correlate_cross_section(
        left=_vector(UP, {name: float(index) for index, name in enumerate(subjects)}),
        right=_vector(THIRD, {name: float(index) * 3.0 for index, name in enumerate(subjects)}),
        spec=SPEC,
    )

    assert value.code == "overlapping_inputs"
    assert value.columns == ("daily_basic.total_mv",)
    assert value.datasets == ("daily_basic",)
    assert growth.code == "identical_inputs"
    assert growth.columns == ("income.total_revenue",)
    assert volatility.code == "identical_inputs"
    assert volatility.columns == ("daily.close", "daily.pre_close")
    assert shared_inputs(UP, THIRD).datasets == ("daily",)
    assert point.shared_input_code == "shared_dataset_only"
    assert point.shared_columns == ()


def test_shared_inputs_refuses_a_definition_against_itself() -> None:
    """A factor declares every one of its own columns, so the answer would be about neither."""
    with pytest.raises(FactorRedundancyError, match="was offered against itself"):
        shared_inputs(MOMENTUM_20_SESSIONS, MOMENTUM_20_SESSIONS)


def test_a_shared_input_carrier_refuses_a_code_it_does_not_declare_and_a_lost_dataset() -> None:
    """The carrier's two invariants, unreachable through `shared_inputs` and reachable directly."""
    with pytest.raises(FactorRedundancyError, match="not a declared shared-input code"):
        SharedInputs(
            left_key="a/v1",
            right_key="b/v1",
            code="mostly",
            columns=(),
            datasets=(),  # type: ignore[arg-type]
        )
    with pytest.raises(FactorRedundancyError, match="has lost one of them"):
        SharedInputs(
            left_key="a/v1",
            right_key="b/v1",
            code="overlapping_inputs",
            columns=("daily.close",),
            datasets=(),
        )


# --- the correlation itself --------------------------------------------------------------------


def test_a_pairs_correlation_does_not_depend_on_which_side_is_offered_first() -> None:
    """`==` and not `approx`, which is only available because `_common_subjects` sorts.

    A Pearson sum is order-dependent in its last bits, so an intersection walked in the left
    vector's own order would give a triangular matrix that depended on which triangle a report
    filled.

    **The two sides' rows are offered in different orders**, which is the whole of what this test
    needs and is easy to leave out: two dicts built from one `_subjects()` tuple have the same
    insertion order, `left`-order and sorted order coincide, and an implementation that walked the
    left vector's own order would pass. The right side is shuffled so the two orders disagree, and
    the securities are then also asserted to intersect fully so the shuffle cannot be doing its
    work by dropping names.
    """
    generator = random.Random(20260815)
    for method in ("pearson", "spearman"):
        spec = RedundancySpec(
            method=method,  # type: ignore[arg-type]
            min_securities=4,
            min_as_ofs=2,
            redundancy_threshold=0.8,
        )
        for _trial in range(50):
            subjects = list(_subjects(generator.randint(6, 30)))
            xs = {name: generator.uniform(-5.0, 5.0) for name in subjects}
            shuffled = subjects[:]
            generator.shuffle(shuffled)
            ys = {name: generator.uniform(-5.0, 5.0) for name in shuffled}
            left = _vector(UP, xs)
            right = _vector(OTHER_UP, ys)

            forward = correlate_cross_section(left=left, right=right, spec=spec)
            backward = correlate_cross_section(left=right, right=left, spec=spec)

            assert list(left.values) != list(right.values)
            assert set(left.values) == set(right.values)
            assert forward.sample_size == len(subjects)
            assert forward.raw_correlation == backward.raw_correlation


def test_the_declared_threshold_is_inclusive_at_exactly_the_correlation_it_names() -> None:
    """`>=` and not `>`, driven on a pair that lands **on** the line rather than near it.

    A rank correlation over five securities takes twenty-one values and `(0, 2, 1, 4, 3)` against
    `(0, 1, 2, 3, 4)` produces exactly `0.7999999999999998`. Declaring that float as the threshold
    puts the pair on the boundary, where `>=` says `redundant` and `>` says `distinct` -- so the
    comparison's inclusiveness is a decidable question rather than one no fixture can reach.

    The odd-looking literal is the point: `0.8` is not the number `_pearson` produces for this
    cross section, and a test that declared `0.8` would sit a last bit below the line and pass for
    either comparison.
    """
    subjects = _subjects(5)
    boundary = _pearson(average_ranks([0, 1, 2, 3, 4]), average_ranks([0, 2, 1, 4, 3]))
    spec = RedundancySpec(
        method="spearman", min_securities=4, min_as_ofs=2, redundancy_threshold=boundary
    )
    left = _vector(UP, dict(zip(subjects, (0.0, 1.0, 2.0, 3.0, 4.0), strict=True)))
    right = _vector(OTHER_UP, dict(zip(subjects, (0.0, 2.0, 1.0, 4.0, 3.0), strict=True)))

    point = correlate_cross_section(left=left, right=right, spec=spec)

    assert boundary == 0.7999999999999998
    assert point.raw_correlation == boundary
    assert point.verdict == "redundant"
    assert (
        correlate_cross_section(
            left=left,
            right=right,
            spec=RedundancySpec(
                method="spearman",
                min_securities=4,
                min_as_ofs=2,
                redundancy_threshold=math.nextafter(boundary, 1.0),
            ),
        ).verdict
        == "distinct"
    )


def test_the_declared_directions_decide_the_sign_and_the_magnitude_is_direction_free() -> None:
    """Four direction combinations over one pair of value vectors, each separated from the others.

    The raw correlation is one number across all four and the oriented one takes both signs, so a
    fixture on which the two agreed would let every assertion here pass while the orientation did
    nothing. The verdict is asserted identical across all four, which is the claim that redundancy
    is judged on the magnitude.
    """
    subjects = _subjects(10)
    xs = dict(zip(subjects, (3.0, 1.0, 4.0, 1.5, 5.0, 9.0, 2.0, 6.0, 5.5, 3.5), strict=True))
    ys = dict(zip(subjects, (2.0, 7.0, 1.0, 8.0, 2.8, 1.8, 2.8, 4.5, 9.0, 0.5), strict=True))
    combinations = {
        (left.direction, right.direction): correlate_cross_section(
            left=_vector(left, xs), right=_vector(right, ys), spec=SPEC
        )
        for left, right in ((UP, OTHER_UP), (UP, OTHER_DOWN), (DOWN, OTHER_UP), (DOWN, OTHER_DOWN))
    }
    raws = {point.raw_correlation for point in combinations.values()}
    oriented = {
        directions: point.oriented_correlation for directions, point in combinations.items()
    }

    assert len(raws) == 1
    raw = raws.pop()
    assert raw is not None and raw != 0.0
    assert oriented[("higher_is_better", "higher_is_better")] == raw
    assert oriented[("lower_is_better", "lower_is_better")] == raw
    assert oriented[("higher_is_better", "lower_is_better")] == -raw
    assert oriented[("lower_is_better", "higher_is_better")] == -raw
    assert len({point.verdict for point in combinations.values()}) == 1


def test_the_verdict_is_decided_by_the_declared_threshold_and_nothing_else() -> None:
    """One cross section, two declarations, two verdicts -- and the number that separates them.

    The magnitude is asserted to sit strictly between the two thresholds, so the test cannot pass
    on a fixture where either declaration would have given the same answer. That is the shape
    `V2-P3-005`'s review found seven fields failing: an assertion that holds for both answers.
    """
    subjects = _subjects(10)
    xs = dict(zip(subjects, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0), strict=True))
    ys = dict(zip(subjects, (1.0, 3.0, 2.0, 4.0, 6.0, 5.0, 7.0, 9.0, 8.0, 10.0), strict=True))
    strict = RedundancySpec(
        method="spearman", min_securities=4, min_as_ofs=2, redundancy_threshold=0.99
    )
    loose = RedundancySpec(
        method="spearman", min_securities=4, min_as_ofs=2, redundancy_threshold=0.5
    )
    left, right = _vector(UP, xs), _vector(OTHER_UP, ys)

    tight_point = correlate_cross_section(left=left, right=right, spec=strict)
    loose_point = correlate_cross_section(left=left, right=right, spec=loose)

    assert tight_point.raw_correlation == loose_point.raw_correlation
    assert loose_point.raw_correlation is not None
    assert 0.5 <= abs(loose_point.raw_correlation) < 0.99
    assert tight_point.verdict == "distinct"
    assert loose_point.verdict == "redundant"


def test_two_factors_in_lockstep_with_nothing_declared_are_reported_as_such() -> None:
    """The audit that fails closed on the hazard `V2-P3-012` avoided by construction.

    A factor and an exact monotone image of it -- which is what an undeclared identity produces --
    is `undeclared_lockstep` under `spearman` at any threshold, because the code is decided before
    the threshold is consulted. Asserted under the strictest legal threshold and under a middling
    one, so the verdict is shown not to be the threshold's doing.
    """
    subjects = _subjects(12)
    xs = {name: float(index) - 5.0 for index, name in enumerate(subjects)}
    image = {name: value**3 + value for name, value in xs.items()}
    for threshold in (1.0, 0.5):
        spec = RedundancySpec(
            method="spearman",
            min_securities=4,
            min_as_ofs=2,
            redundancy_threshold=threshold,
        )

        point = correlate_cross_section(
            left=_vector(UP, xs), right=_vector(OTHER_UP, image), spec=spec
        )

        assert point.verdict == "undeclared_lockstep"
        assert point.raw_correlation is not None
        assert round(abs(point.raw_correlation), LOCKSTEP_DECIMAL_PLACES) == 1.0


def test_the_two_methods_answer_differently_on_one_cross_section() -> None:
    """A pair on which `pearson` and `spearman` disagree, so a report cannot substitute one.

    One security carries an outlying value on the left side and the ordering is otherwise the
    same, which is exactly the case the two methods exist to separate: the ranks agree perfectly
    and the products do not. The gap is floored well above rounding.
    """
    subjects = _subjects(10)
    xs = dict(zip(subjects, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 400.0), strict=True))
    ys = dict(zip(subjects, (10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0), strict=True))
    left, right = _vector(UP, xs), _vector(OTHER_UP, ys)
    rank_spec = RedundancySpec(
        method="spearman", min_securities=4, min_as_ofs=2, redundancy_threshold=0.8
    )
    value_spec = RedundancySpec(
        method="pearson", min_securities=4, min_as_ofs=2, redundancy_threshold=0.8
    )

    ranked = correlate_cross_section(left=left, right=right, spec=rank_spec)
    valued = correlate_cross_section(left=left, right=right, spec=value_spec)

    assert ranked.raw_correlation == -1.0
    assert valued.raw_correlation is not None
    assert abs(ranked.raw_correlation - valued.raw_correlation) > 0.3
    assert ranked.verdict == "undeclared_lockstep"
    assert valued.verdict == "distinct"


def test_a_thin_or_all_tied_pair_reports_a_code_rather_than_raising() -> None:
    """Three facts about the day, each with its own code, and the left side decided first.

    A loop over a year of as_ofs has to keep going past all three, and which side collapsed is the
    actionable half -- one `degenerate` code would make "this factor scored the whole market with
    one value" indistinguishable from "the other one did".
    """
    subjects = _subjects(10)
    ordered = {name: float(index) for index, name in enumerate(subjects)}
    flat = dict.fromkeys(subjects, 7.0)
    thin_left = _vector(UP, {name: ordered[name] for name in subjects[:3]})
    thin_right = _vector(OTHER_UP, {name: ordered[name] for name in subjects[:3]})

    thin = correlate_cross_section(left=thin_left, right=thin_right, spec=SPEC)
    left_flat = correlate_cross_section(
        left=_vector(UP, flat), right=_vector(OTHER_UP, ordered), spec=SPEC
    )
    right_flat = correlate_cross_section(
        left=_vector(UP, ordered), right=_vector(OTHER_UP, flat), spec=SPEC
    )
    both_flat = correlate_cross_section(
        left=_vector(UP, flat), right=_vector(OTHER_UP, flat), spec=SPEC
    )

    assert thin.coverage == "insufficient_sample" and thin.sample_size == 3
    assert left_flat.coverage == "degenerate_left"
    assert right_flat.coverage == "degenerate_right"
    assert both_flat.coverage == "degenerate_left"
    for point in (thin, left_flat, right_flat, both_flat):
        assert point.raw_correlation is None
        assert point.oriented_correlation is None
        assert point.verdict is None


def test_the_pair_census_accounts_for_every_admitted_subject_on_both_sides() -> None:
    """`sample_size`, `left_only_count` and `right_only_count` add up on each side separately.

    Three cells that no two of which share a value on this fixture, because `V2-P3-004`'s review
    found a census asserted on a fixture where two answers were equal. Six left-only, two
    right-only and eight common are three different numbers.
    """
    common = _subjects(8)
    left_only = tuple(f"L{index:05d}.SZ" for index in range(6))
    right_only = tuple(f"R{index:05d}.SZ" for index in range(2))
    left_values = {name: float(index) for index, name in enumerate([*common, *left_only])}
    right_values = {name: float(index) * 1.7 for index, name in enumerate([*common, *right_only])}

    point = correlate_cross_section(
        left=_vector(UP, left_values), right=_vector(OTHER_UP, right_values), spec=SPEC
    )

    assert (point.sample_size, point.left_only_count, point.right_only_count) == (8, 6, 2)
    assert point.sample_size + point.left_only_count == len(left_values)
    assert point.sample_size + point.right_only_count == len(right_values)


def test_a_pair_refuses_two_as_ofs_and_one_factor_on_one_tier_against_itself() -> None:
    """Two malformed questions, and the one shape that looks like the second and is supported."""
    subjects = _subjects(8)
    values = {name: float(index) for index, name in enumerate(subjects)}
    raw = _vector(UP, values, tier="raw")
    tomorrow = _vector(OTHER_UP, values, as_of=AS_OF + timedelta(days=1))

    with pytest.raises(FactorRedundancyError, match="a plausible number from the wrong rows"):
        correlate_cross_section(left=raw, right=tomorrow, spec=SPEC)
    with pytest.raises(FactorRedundancyError, match=r"that correlation is 1\.0 by construction"):
        correlate_cross_section(left=raw, right=_vector(UP, values, tier="raw"), spec=SPEC)
    cross_tier = correlate_cross_section(
        left=raw, right=_vector(UP, values, tier="neutralized"), spec=SPEC
    )

    assert cross_tier.left_tier == "raw" and cross_tier.right_tier == "neutralized"
    with pytest.raises(ValidationError, match="was correlated against itself"):
        RedundancyPoint.model_validate({**cross_tier.model_dump(), "right_tier": "raw"})


def test_a_point_refuses_an_orientation_that_contradicts_its_two_declarations() -> None:
    """`ICPoint`'s rule with two directions instead of one, driven on a stored payload.

    Without it `left_direction="lower_is_better", raw=0.9, oriented=0.9` builds, and a report
    reading `oriented_correlation` would say two factors make the same bet when the measurement
    says the opposite.
    """
    subjects = _subjects(8)
    point = correlate_cross_section(
        left=_vector(DOWN, {name: float(index) for index, name in enumerate(subjects)}),
        right=_vector(OTHER_UP, {name: float(index) * 2.0 for index, name in enumerate(subjects)}),
        spec=SPEC,
    )

    assert point.raw_correlation is not None
    assert point.oriented_correlation == -point.raw_correlation
    with pytest.raises(ValidationError, match="signed by both declarations"):
        RedundancyPoint.model_validate(
            {**point.model_dump(), "oriented_correlation": point.raw_correlation}
        )
    with pytest.raises(ValidationError, match="exactly the 'measured' code carries"):
        RedundancyPoint.model_validate({**point.model_dump(), "raw_correlation": None})


# --- the vectors and their census --------------------------------------------------------------


def test_an_imputed_value_never_enters_a_correlation_and_admitting_it_would_have_moved_it() -> None:
    """`V2-P3-005`'s lesson: the exclusion is live, not merely counted.

    A test that only asserted the census cell would pass for an implementation that admitted the
    fill anyway, so the same cross section is correlated twice -- once with the imputed rows under
    their own code and once with the identical numbers relabelled `processed` -- and the two
    correlations are asserted **apart**. The fill is placed to move the ordering rather than to
    extend it, which is what a cross-sectional median actually does.
    """
    subjects = _subjects(10)
    measured = {name: float(index) for index, name in enumerate(subjects)}
    partner = {name: float(index) for index, name in enumerate(subjects)}
    filled = tuple(f"F{index:05d}.SZ" for index in range(10))
    rows_excluded = [(name, 4.5, "imputed") for name in filled]
    rows_admitted = [(name, 4.5, "processed") for name in filled]
    partner_rows = [(name, -1.0 - index, "processed") for index, name in enumerate(filled)]

    left_excluded = factor_vector(
        as_of=AS_OF,
        tier="processed",
        definition=UP,
        rows=[*[(name, value, "processed") for name, value in measured.items()], *rows_excluded],
    )
    left_admitted = factor_vector(
        as_of=AS_OF,
        tier="processed",
        definition=UP,
        rows=[*[(name, value, "processed") for name, value in measured.items()], *rows_admitted],
    )
    right = factor_vector(
        as_of=AS_OF,
        tier="processed",
        definition=OTHER_UP,
        rows=[
            *[(name, value, "processed") for name, value in partner.items()],
            *partner_rows,
        ],
    )
    without = correlate_cross_section(left=left_excluded, right=right, spec=SPEC)
    with_fill = correlate_cross_section(left=left_admitted, right=right, spec=SPEC)

    assert dict(left_excluded.excluded_by_coverage)["imputed"] == 10
    assert dict(left_admitted.excluded_by_coverage)["imputed"] == 0
    assert (without.sample_size, with_fill.sample_size) == (10, 20)
    assert without.raw_correlation == 1.0
    assert with_fill.raw_correlation is not None
    assert abs(without.raw_correlation - with_fill.raw_correlation) > 0.3


def test_a_vectors_census_adds_up_and_carries_every_excluded_code_including_the_zeros() -> None:
    """The arithmetic that makes a shorter correlation impossible to hide.

    Every non-admitted code of the tier appears, in the tier's declared order, whether or not it
    occurred -- "nobody was `input_missing`" and "nothing looked" are different readings. The
    counts are chosen so no two cells share a value.
    """
    admitted = {name: float(index) for index, name in enumerate(_subjects(9))}
    extras: list[tuple[str, float | None, str]] = [
        (f"M{index}.SZ", None, "input_missing") for index in range(2)
    ]
    extras += [(f"H{index}.SZ", None, "insufficient_history") for index in range(3)]
    extras += [(f"U{index}.SZ", None, "undefined_value") for index in range(4)]
    extras += [(f"N{index}.SZ", None, "not_in_universe") for index in range(5)]
    extras += [(f"A{index}.SZ", None, "ambiguous_filing") for index in range(6)]

    vector = _vector(UP, admitted, extra_rows=extras)

    assert tuple(code for code, _count in vector.excluded_by_coverage) == tuple(
        code for code in TIER_COVERAGE_ORDER["raw"] if code != "computed"
    )
    assert dict(vector.excluded_by_coverage) == {
        "not_in_universe": 5,
        "insufficient_history": 3,
        "ambiguous_filing": 6,
        "input_missing": 2,
        "undefined_value": 4,
    }
    assert vector.subject_count == 29
    assert len(vector.values) == 9
    assert len(vector.values) + sum(count for _c, count in vector.excluded_by_coverage) == 29
    with pytest.raises(FactorRedundancyError, match="a census that does not add up"):
        FactorVector(
            as_of=AS_OF,
            tier="raw",
            definition=UP,
            values=MappingProxyType(dict(admitted)),
            excluded_by_coverage=vector.excluded_by_coverage,
            subject_count=28,
        )


def test_factor_vector_refuses_the_four_shapes_that_are_malformed_questions() -> None:
    """A duplicate subject, an undeclared code, an admitted row with no value, and a `nan`.

    Each `match=` is narrow enough to say which rule refused, because "a ValueError was raised"
    would pass for any of the four.
    """
    with pytest.raises(FactorRedundancyError, match="appears more than once"):
        factor_vector(
            as_of=AS_OF,
            tier="raw",
            definition=UP,
            rows=[("000001.SZ", 1.0, "computed"), ("000001.SZ", 2.0, "computed")],
        )
    with pytest.raises(FactorRedundancyError, match="which the raw tier does not declare"):
        factor_vector(
            as_of=AS_OF, tier="raw", definition=UP, rows=[("000001.SZ", 1.0, "processed")]
        )
    with pytest.raises(FactorRedundancyError, match="this row skipped its own constructor"):
        factor_vector(
            as_of=AS_OF, tier="raw", definition=UP, rows=[("000001.SZ", None, "computed")]
        )
    with pytest.raises(FactorRedundancyError, match="poisons every mean, rank and correlation"):
        factor_vector(
            as_of=AS_OF, tier="raw", definition=UP, rows=[("000001.SZ", math.nan, "computed")]
        )
    with pytest.raises(FactorRedundancyError, match="is not a declared tier"):
        factor_vector(
            as_of=AS_OF,
            tier="rank",  # type: ignore[arg-type]
            definition=UP,
            rows=[("000001.SZ", 1.0, "computed")],
        )
    with pytest.raises(FactorRedundancyError, match="is not a declared tier"):
        FactorVector(
            as_of=AS_OF,
            tier="rank",  # type: ignore[arg-type]
            definition=UP,
            values=MappingProxyType({}),
            excluded_by_coverage=(),
            subject_count=0,
        )


def test_a_vector_refuses_a_census_keyed_by_the_wrong_codes_or_a_negative_count() -> None:
    """The two shapes `factor_vector` cannot produce and a stored row could carry."""
    with pytest.raises(FactorRedundancyError, match="cannot be told from one whose count is zero"):
        FactorVector(
            as_of=AS_OF,
            tier="raw",
            definition=UP,
            values=MappingProxyType({}),
            excluded_by_coverage=(("input_missing", 0),),
            subject_count=0,
        )
    with pytest.raises(FactorRedundancyError, match="cannot be negative"):
        FactorVector(
            as_of=AS_OF,
            tier="raw",
            definition=UP,
            values=MappingProxyType({}),
            excluded_by_coverage=tuple(
                (code, -1 if code == "input_missing" else 0)
                for code in TIER_COVERAGE_ORDER["raw"]
                if code != "computed"
            ),
            subject_count=0,
        )


# --- cross-tier: what the neutralisation removed -----------------------------------------------


def test_the_same_factor_on_two_tiers_says_how_much_of_it_survived_the_transform() -> None:
    """The cross-tier reading, and the one place the orientation is proved to cancel.

    Both sides carry one `direction`, so `oriented_correlation == raw_correlation` **exactly** --
    driven on a `lower_is_better` factor, where a single application of the sign would flip it and
    a report would read the residual as the opposite of its own factor.

    The magnitude is asserted strictly inside `(threshold, 1)`: a residual that still ranked the
    market identically would come out in lockstep and say the neutralisation removed nothing, and
    a fixture on which it did would let this test pass while measuring nothing.
    """
    subjects = _subjects(12)
    raw_values = {name: float(index) for index, name in enumerate(subjects)}
    residuals = {
        name: float(index) + (2.5 if index % 4 == 0 else -0.5)
        for index, name in enumerate(subjects)
    }

    point = correlate_cross_section(
        left=_vector(DOWN, raw_values, tier="raw"),
        right=factor_vector(
            as_of=AS_OF,
            tier="neutralized",
            definition=DOWN,
            rows=[(name, value, "neutralized") for name, value in residuals.items()],
        ),
        spec=SPEC,
    )

    assert point.left_tier == "raw" and point.right_tier == "neutralized"
    assert point.left_factor_id == point.right_factor_id
    assert point.oriented_correlation == point.raw_correlation
    assert point.raw_correlation is not None
    assert SPEC.redundancy_threshold < point.raw_correlation < 1.0
    assert point.verdict == "redundant"
    assert point.shared_input_code == "identical_inputs"
    assert point.shared_columns == ("daily.close",)


# --- a series of points -------------------------------------------------------------------------


def _series(
    pairs: Sequence[tuple[Mapping[str, float], Mapping[str, float]]],
) -> list[RedundancyPoint]:
    return [
        correlate_cross_section(
            left=_vector(UP, xs, as_of=AS_OF + timedelta(days=index)),
            right=_vector(OTHER_UP, ys, as_of=AS_OF + timedelta(days=index)),
            spec=SPEC,
        )
        for index, (xs, ys) in enumerate(pairs)
    ]


def test_a_summary_separates_a_stable_relationship_from_one_whose_sign_flips() -> None:
    """Why both a mean and a mean of magnitudes are reported, on a fixture that separates them.

    Four as_ofs, two agreeing almost perfectly and two disagreeing almost perfectly. The mean is
    near zero and the mean magnitude is near one: two factors whose *relationship* is strong and
    whose *sign* is not, which a mean alone reports as "unrelated" and a mean magnitude alone
    reports as "redundant". Neither number is the answer on its own, which is why both are fields.
    """
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    falling = {name: -float(index) for index, name in enumerate(subjects)}
    study = RedundancyStudy(SPEC, identities=[])

    summary = study.summarize(
        _series([(rising, rising), (rising, falling), (rising, rising), (rising, falling)])
    )

    assert summary.coverage == "measured" and summary.measured_count == 4
    assert summary.mean_correlation is not None and abs(summary.mean_correlation) < 1e-12
    assert summary.mean_abs_correlation == 1.0
    assert summary.stdev_correlation is not None and summary.stdev_correlation > 1.0
    assert summary.verdict == "undeclared_lockstep"
    assert dict(summary.verdict_counts)["undeclared_lockstep"] == 4
    assert sum(count for _v, count in summary.verdict_counts) == 4
    assert (summary.left_key, summary.right_key) == (UP.key, OTHER_UP.key)
    assert (summary.left_factor_id, summary.right_factor_id) == (UP.factor_id, OTHER_UP.factor_id)
    assert summary.left_factor_id != summary.right_factor_id


def test_a_summarys_verdict_and_its_per_day_census_are_allowed_to_disagree() -> None:
    """Why both the census and the summary verdict are fields, on a fixture where they differ.

    Three as_ofs: two at `+0.964` and one at `-0.127`. The per-day census says **two of three days
    were redundant**, and the summary verdict says `distinct`, because the mean magnitude is
    `0.685` and the declared line is `0.8`. Neither is wrong and neither implies the other -- one
    answers "were they redundant that day" and the other "are they redundant over this sample" --
    and a report carrying only one of them would be answering a question its reader did not ask.

    The counts are chosen so no two cells share a value, and the mean is asserted strictly inside
    the interval that makes the disagreement real rather than a rounding accident.
    """
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    near = dict(zip(subjects, (0.0, 2.0, 1.0, 3.0, 4.0, 5.0, 7.0, 6.0, 8.0, 9.0), strict=True))
    scattered = dict(zip(subjects, (5.0, 1.0, 9.0, 3.0, 7.0, 2.0, 8.0, 4.0, 6.0, 0.0), strict=True))
    study = RedundancyStudy(SPEC, identities=[])

    summary = study.summarize(_series([(rising, near), (rising, near), (rising, scattered)]))

    assert tuple(code for code, _c in summary.verdict_counts) == REDUNDANCY_VERDICT_ORDER
    assert dict(summary.verdict_counts) == {
        "arithmetic": 0,
        "undeclared_lockstep": 0,
        "redundant": 2,
        "distinct": 1,
    }
    assert summary.measured_count == 3
    assert summary.mean_abs_correlation is not None
    assert 0.5 < summary.mean_abs_correlation < SPEC.redundancy_threshold
    assert summary.mean_correlation is not None
    assert summary.mean_correlation < summary.mean_abs_correlation
    assert summary.verdict == "distinct"


def test_a_summary_is_arithmetic_only_when_every_measured_point_was() -> None:
    """An identity that held on three days of four is not an identity, and is not reported as one.

    Stricter than the per-point ladder in the direction that matters. The fourth day's identity is
    `unevaluable` -- the third member scored nobody that day -- so nothing was contradicted, and
    "nothing contradicted it" is exactly what must not be promoted to "it held".
    """
    family = _momentum_family(40)
    study = RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY])
    points = []
    for index in range(4):
        stamp = AS_OF + timedelta(days=index)
        third = {} if index == 3 else family["m15"]
        vectors = {
            "momentum_20_sessions": _vector(SHIPPED_M20, family["unskipped_m20"], as_of=stamp),
            "m15": _vector(M15, third, as_of=stamp),
            "reversal_5_sessions": _vector(R5, family["reversal_5_sessions"], as_of=stamp),
        }
        points.append(
            study.measure(
                left=vectors["momentum_20_sessions"],
                right=vectors["reversal_5_sessions"],
                vectors=vectors,
            )
        )
    whole = study.summarize(points[:3])
    mixed = study.summarize(points)

    assert [point.verdict for point in points[:3]] == ["arithmetic"] * 3
    assert points[3].verdict != "arithmetic"
    assert whole.verdict == "arithmetic"
    assert mixed.verdict != "arithmetic"
    assert dict(mixed.verdict_counts)["arithmetic"] == 3


def test_a_series_thinner_than_the_declared_floor_reports_a_code_and_no_statistics() -> None:
    """`insufficient_as_ofs` carries the as_ofs and the counts and none of the four statistics."""
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    flat = dict.fromkeys(subjects, 1.0)
    study = RedundancyStudy(
        RedundancySpec(method="spearman", min_securities=4, min_as_ofs=3, redundancy_threshold=0.8),
        identities=[],
    )

    summary = study.summarize(_series([(rising, rising), (flat, rising), (flat, rising)]))

    assert summary.coverage == "insufficient_as_ofs"
    assert summary.measured_count == 1 and len(summary.as_ofs) == 3
    assert summary.mean_correlation is None
    assert summary.mean_abs_correlation is None
    assert summary.stdev_correlation is None
    assert summary.verdict is None
    assert dict(summary.verdict_counts)["undeclared_lockstep"] == 1


def test_summarize_refuses_a_series_that_is_not_one_pair() -> None:
    """An empty series, a mixed pair, a mixed method and a repeated `as_of`, each named apart."""
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    study = RedundancyStudy(SPEC, identities=[])
    points = _series([(rising, rising), (rising, rising)])
    other = correlate_cross_section(
        left=_vector(UP, rising, as_of=AS_OF + timedelta(days=5)),
        right=_vector(THIRD, rising, as_of=AS_OF + timedelta(days=5)),
        spec=SPEC,
    )
    pearson_study = RedundancyStudy(
        RedundancySpec(method="pearson", min_securities=4, min_as_ofs=2, redundancy_threshold=0.8),
        identities=[],
    )

    with pytest.raises(FactorRedundancyError, match="needs at least one point"):
        study.summarize([])
    with pytest.raises(FactorRedundancyError, match="one pair of factors on one pair of tiers"):
        study.summarize([*points, other])
    with pytest.raises(FactorRedundancyError, match="and this study declares 'pearson'"):
        pearson_study.summarize(points)
    with pytest.raises(FactorRedundancyError, match="appears more than once in this series"):
        study.summarize([points[0], points[0]])


def test_a_summary_refuses_a_verdict_census_that_does_not_match_its_measured_count() -> None:
    """The stored-payload direction: a census that lost a point, and one keyed by wrong codes."""
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    summary = RedundancyStudy(SPEC, identities=[]).summarize(
        _series([(rising, rising), (rising, rising)])
    )
    payload = summary.model_dump()

    with pytest.raises(ValidationError, match="every measured point carries"):
        type(summary).model_validate({**payload, "measured_count": 1})
    with pytest.raises(ValidationError, match="cannot be told from one whose count is zero"):
        type(summary).model_validate(
            {**payload, "verdict_counts": (("redundant", 2), ("distinct", 0))}
        )
    with pytest.raises(ValidationError, match="distinct and ascending"):
        type(summary).model_validate({**payload, "as_ofs": tuple(reversed(payload["as_ofs"]))})


# --- the IC series ------------------------------------------------------------------------------


def _ic_series(
    definition: FactorDefinition,
    values: Sequence[float | None],
    *,
    tier: FactorTier = "raw",
    horizon_sessions: int = 5,
    method: ICMethod = "spearman",
) -> list[ICPoint]:
    """A series of `ICPoint`s built through `FactorICStudy`'s own orientation rule.

    `FactorICSpec.orient` is what puts the sign on `ic`, so building these by hand would be a
    second implementation of the convention the test below exists to check is applied once.
    """
    spec = FactorICSpec(definition=definition, method=method, min_securities=4, min_as_ofs=2)
    return [
        ICPoint(
            as_of=AS_OF + timedelta(days=index),
            tier=tier,
            method=method,
            direction=definition.direction,
            factor_id=definition.factor_id,
            horizon_sessions=horizon_sessions,
            coverage="measured" if raw is not None else "insufficient_sample",
            sample_size=100 if raw is not None else 2,
            raw_ic=raw,
            ic=None if raw is None else spec.orient(raw),
        )
        for index, raw in enumerate(values)
    ]


def test_two_factors_can_rank_the_market_apart_and_still_earn_their_ics_on_the_same_days() -> None:
    """Why the IC-series reading is a third question and not a restatement of the first two.

    The two factors' cross-sectional rank correlation is near zero on the fixture -- they order
    the market almost independently -- and their oriented IC series move together at over 0.9. A
    book holding both would be diversified in exposure and not in outcome, which is a redundancy
    neither cross-sectional number reports.
    """
    subjects = _subjects(12)
    xs = dict(zip(subjects, range(12), strict=True))
    ys = dict(zip(subjects, (11, 5, 0, 9, 1, 6, 3, 4, 10, 7, 2, 8), strict=True))
    study = RedundancyStudy(SPEC, identities=[])
    ics = (0.05, -0.02, 0.08, -0.06, 0.03, 0.09, -0.04, 0.02)

    cross_section = correlate_cross_section(
        left=_vector(UP, {name: float(value) for name, value in xs.items()}),
        right=_vector(OTHER_UP, {name: float(value) for name, value in ys.items()}),
        spec=SPEC,
    )
    series = study.correlate_ic_series(
        _ic_series(UP, ics), _ic_series(OTHER_UP, [value * 1.4 + 0.005 for value in ics])
    )

    assert cross_section.raw_correlation == 0.0
    assert cross_section.verdict == "distinct"
    assert series.coverage == "measured" and series.sample_size == 8
    assert series.correlation is not None and series.correlation > 0.9
    assert series.verdict in {"redundant", "undeclared_lockstep"}
    assert (series.left_factor_id, series.right_factor_id) == (UP.factor_id, OTHER_UP.factor_id)
    assert series.left_factor_id != series.right_factor_id
    assert series.horizon_sessions == 5


def test_two_factors_in_rank_lockstep_have_the_same_rank_ic_whatever_the_returns_did() -> None:
    """The one implication from the cross section to the IC series, at the strength it has.

    A rank IC is a function of the two **rank** vectors, so two factors whose ranks are identical
    -- which is what `undeclared_lockstep` under `spearman` reports -- produce the identical rank
    IC at that `as_of` for **every** return vector, not merely a similar one. Driven with `==`
    over 100 random forward-return vectors rather than with a tolerance.

    The **weaker** reading is refused in the same test, because it is the one a reader would
    assume: **one** swap of two names twelve rank positions apart leaves the two factors at a rank
    correlation of 0.936 and moves the rank IC by 0.149 on one of the hundred return vectors. So
    "almost the same ordering" bounds nothing about the ICs, and this module's docstring says so
    rather than claiming the comfortable version.
    """
    generator = random.Random(20260816)
    values = [generator.uniform(-1.0, 1.0) for _ in range(30)]
    image = [value**3 + value for value in values]
    order = sorted(range(30), key=lambda index: values[index])
    nearly = list(values)
    low, high = order[7], order[19]
    nearly[low], nearly[high] = nearly[high], nearly[low]
    gaps: list[float] = []
    for _trial in range(100):
        returns = average_ranks([generator.gauss(0.0, 0.02) for _ in range(30)])

        assert _pearson(average_ranks(values), returns) == _pearson(average_ranks(image), returns)
        gaps.append(
            abs(_pearson(average_ranks(values), returns) - _pearson(average_ranks(nearly), returns))
        )

    assert average_ranks(values) == average_ranks(image)
    assert _pearson(average_ranks(values), average_ranks(nearly)) > 0.93
    assert max(gaps) > 0.13


def test_the_ic_series_correlation_reads_the_already_oriented_ic_and_does_not_orient_it_twice() -> (
    None
):
    """`ICPoint.ic` arrives oriented, so this module applies no second sign. Driven on the case
    where a second application would be invisible in the code and visible only in the number.

    A `lower_is_better` factor's `ic` is the negation of its `raw_ic`. Correlating the two series'
    `ic` fields and correlating their `raw_ic` fields therefore differ in sign for a mixed pair,
    and the assertion pins which of the two this module reports.
    """
    raws = (0.05, -0.02, 0.08, -0.06, 0.03, 0.09, -0.04, 0.02)
    partner = tuple(value * 1.3 + 0.004 for value in raws)
    study = RedundancyStudy(SPEC, identities=[])

    mixed = study.correlate_ic_series(_ic_series(UP, raws), _ic_series(OTHER_DOWN, partner))
    aligned = study.correlate_ic_series(_ic_series(UP, raws), _ic_series(OTHER_UP, partner))
    on_raw = _pearson(average_ranks(list(raws)), average_ranks(list(partner)))

    assert aligned.correlation is not None and aligned.correlation == pytest.approx(on_raw)
    assert mixed.correlation is not None
    assert mixed.correlation == pytest.approx(-on_raw)
    assert mixed.correlation != aligned.correlation


def test_an_ic_series_correlation_counts_the_days_only_one_side_measured() -> None:
    """`offered_as_of_count` against `sample_size`: attrition visible rather than confounded.

    A day one factor could not score contributes to neither side, and a report that only carried
    the measured count could not tell two factors that disagree from two whose measured days
    barely overlap.
    """
    study = RedundancyStudy(SPEC, identities=[])
    left = _ic_series(UP, (0.05, None, 0.08, -0.06, 0.03, None, 0.02))
    right = _ic_series(OTHER_UP, (0.06, 0.01, None, -0.05, 0.04, 0.07, 0.03))

    series = study.correlate_ic_series(left, right)

    assert series.offered_as_of_count == 7
    assert series.sample_size == 4
    assert series.coverage == "measured"


def test_correlate_ic_series_refuses_two_horizons_two_methods_and_a_series_against_itself() -> None:
    """Four malformed questions, each with its own refusal."""
    study = RedundancyStudy(SPEC, identities=[])
    base = _ic_series(UP, (0.05, -0.02, 0.08, -0.06))

    with pytest.raises(FactorRedundancyError, match="two different quantities and their"):
        study.correlate_ic_series(
            base, _ic_series(OTHER_UP, (0.1, 0.2, 0.3, 0.4), horizon_sessions=60)
        )
    with pytest.raises(FactorRedundancyError, match="a rank IC and a Pearson IC"):
        study.correlate_ic_series(_ic_series(UP, (0.05, 0.1), method="pearson"), base)
    with pytest.raises(FactorRedundancyError, match=r"that number is 1\.0 by construction"):
        study.correlate_ic_series(base, _ic_series(UP, (0.05, -0.02, 0.08, -0.06)))
    with pytest.raises(FactorRedundancyError, match="the left IC series is empty"):
        study.correlate_ic_series([], base)
    with pytest.raises(FactorRedundancyError, match="one side of an IC-series correlation"):
        study.correlate_ic_series(
            [*base, *_ic_series(OTHER_UP, (0.4,))],
            _ic_series(THIRD, (0.05, -0.02, 0.08, -0.06, 0.01)),
        )
    with pytest.raises(FactorRedundancyError, match="appears more than once in the right series"):
        study.correlate_ic_series(base, [_ic_series(OTHER_UP, (0.1,))[0]] * 2)


def test_an_ic_series_at_exactly_the_sample_floor_is_measured_rather_than_thin() -> None:
    """The floor is a floor: `min_as_ofs` days are enough, and one fewer is not.

    The suite drove this comparison from two away -- one measured pair against a floor of two --
    so `<` and `<=` agreed on every fixture and the boundary was undecidable. The direction that
    matters is this one: a `<=` would silently discard the smallest legal sample and report
    `insufficient_sample` for a pair that had exactly the days the spec asked for, which reads
    downstream as "these two factors could not be compared" rather than as "they were".

    Both sides are asserted on one call each, and `sample_size` is asserted beside the coverage so
    that the fixture cannot drift into a different number of pairs while the coverage assertion
    keeps passing.
    """
    study = RedundancyStudy(SPEC, identities=[])
    right = _ic_series(OTHER_UP, (0.06, 0.01, 0.02, 0.03))

    at_the_floor = study.correlate_ic_series(_ic_series(UP, (0.05, -0.02, None, None)), right)
    below = study.correlate_ic_series(_ic_series(UP, (0.05, None, None, None)), right)

    assert SPEC.min_as_ofs == 2
    assert at_the_floor.sample_size == 2
    assert at_the_floor.coverage == "measured"
    assert at_the_floor.correlation is not None
    assert below.sample_size == 1
    assert below.coverage == "insufficient_sample"
    assert below.correlation is None


def test_a_thin_or_flat_ic_series_pair_reports_a_code_and_cannot_be_arithmetic() -> None:
    """The three codes, and the one verdict an IC-series correlation may never carry.

    An identity relates two factors' **values**; two ICs agreeing is a statement about the market
    even when the values behind them are algebraically bound, so `arithmetic` is refused at the
    contract rather than merely never produced.
    """
    study = RedundancyStudy(SPEC, identities=[])
    flat = _ic_series(OTHER_UP, (0.05, 0.05, 0.05, 0.05))
    varied = _ic_series(UP, (0.05, -0.02, 0.08, -0.06))

    thin = study.correlate_ic_series(
        _ic_series(UP, (0.05, None, None, None)), _ic_series(OTHER_UP, (0.06, 0.01, 0.02, 0.03))
    )
    left_flat = study.correlate_ic_series(_ic_series(UP, (0.05,) * 4), flat)
    right_flat = study.correlate_ic_series(varied, flat)

    assert thin.coverage == "insufficient_sample" and thin.correlation is None
    assert left_flat.coverage == "degenerate_left"
    assert right_flat.coverage == "degenerate_right"
    with pytest.raises(ValidationError, match="cannot be arithmetic"):
        ICSeriesCorrelation.model_validate(
            {
                **study.correlate_ic_series(
                    varied, _ic_series(OTHER_UP, (0.1, 0.2, 0.3, 0.4))
                ).model_dump(),
                "verdict": "arithmetic",
            }
        )


# --- the contracts, the registry and the declared tables ----------------------------------------


def test_the_spec_refuses_a_threshold_at_zero_and_a_sample_floor_below_four() -> None:
    """The declaration's own bounds, each of which is arithmetic rather than a taste.

    A threshold of zero calls every pair redundant because `abs(r) >= 0` always; a sample floor of
    three puts every rank correlation at `+-0.5` or `+-1`. Both are refused at the contract, and
    the four fields have no defaults so a spec cannot be built without stating them.
    """
    valid = {
        "method": "spearman",
        "min_securities": MINIMUM_REDUNDANCY_SECURITIES,
        "min_as_ofs": MINIMUM_IC_AS_OFS,
        "redundancy_threshold": 0.8,
    }

    assert RedundancySpec(**valid).redundancy_threshold == 0.8  # type: ignore[arg-type]
    assert (
        RedundancySpec(**{**valid, "redundancy_threshold": 1.0}).redundancy_threshold  # type: ignore[arg-type]
        == 1.0
    )
    for update, pattern in (
        ({"redundancy_threshold": 0.0}, "greater than 0"),
        ({"redundancy_threshold": 1.5}, "less than or equal to 1"),
        ({"min_securities": MINIMUM_REDUNDANCY_SECURITIES - 1}, "greater than or equal to 4"),
        ({"min_securities": MAXIMUM_REDUNDANCY_SECURITIES + 1}, "less than or equal to 10000"),
        ({"min_as_ofs": MINIMUM_IC_AS_OFS - 1}, "greater than or equal to 2"),
        ({"min_as_ofs": MAXIMUM_REDUNDANCY_AS_OFS + 1}, "less than or equal to 10000"),
        ({"method": "kendall"}, "Input should be"),
    ):
        with pytest.raises(ValidationError, match=pattern):
            RedundancySpec(**{**valid, **update})  # type: ignore[arg-type]
    for missing in ("method", "min_securities", "min_as_ofs", "redundancy_threshold"):
        with pytest.raises(ValidationError, match="Field required"):
            RedundancySpec(**{name: value for name, value in valid.items() if name != missing})  # type: ignore[arg-type]


def test_an_identity_check_refuses_a_verdict_its_own_residual_contradicts() -> None:
    """The stored-payload direction on `IdentityCheck`: three relationships, each driven.

    A check reporting `verified` beside a residual larger than its tolerance is the one shape that
    would let a mis-stored row promote a refutation into an arithmetic verdict downstream.
    """
    valid = {
        "code": "probe",
        "members": ("a", "b"),
        "coverage": "verified",
        "tolerance": 1e-9,
        "subject_count": 5,
        "max_abs_residual": 1e-12,
    }

    assert IdentityCheck(**valid).coverage == "verified"  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="verified is exactly the case"):
        IdentityCheck(**{**valid, "max_abs_residual": 1.0})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="unevaluable carries none"):
        IdentityCheck(**{**valid, "max_abs_residual": None})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="unevaluable is exactly the case"):
        IdentityCheck(**{**valid, "subject_count": 0})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="is not a finite residual"):
        IdentityCheck(**{**valid, "max_abs_residual": math.inf})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="is a negative residual"):
        IdentityCheck(**{**valid, "max_abs_residual": -1e-12})  # type: ignore[arg-type]


def test_the_known_redundancy_limitations_are_exactly_these_six_codes() -> None:
    """The registry, bound to the suite by a set literal compared for equality.

    Equality rather than membership, for `tests/unit/test_known_limitation_registries.py`'s
    reason: a membership assertion can see a code that was renamed and never one that was
    removed.
    """
    declared = {
        "a_cross_sections_security_count_is_not_a_sample_size",
        "a_series_of_cross_sectional_correlations_is_autocorrelated",
        "an_ic_series_correlation_inherits_the_overlapping_windows_whole",
        "a_cross_tier_pair_correlates_one_point_in_time_side_against_one_snapshot_side",
        "a_shared_column_is_neither_necessary_nor_sufficient_for_a_correlation",
        "an_identity_is_declared_by_a_caller_and_only_its_refutation_is_automatic",
    }

    assert declared == REDUNDANCY_LIMITATION_CODES
    assert len(KNOWN_REDUNDANCY_LIMITATIONS) == len(REDUNDANCY_LIMITATION_CODES) == 6
    assert all(limitation.detail.strip() for limitation in KNOWN_REDUNDANCY_LIMITATIONS)


def test_every_declared_order_is_a_permutation_of_its_own_code_set() -> None:
    """The four closed vocabularies, each held against the tuple a report lays it out in.

    `V2-P3-005`'s `RAW_COVERAGE_ORDER` pin one plane over: an order that gained a member the code
    set does not have would put a column in a report that nothing produces, and one that lost a
    member would drop a column for something that does.
    """
    for order, codes in (
        (REDUNDANCY_VERDICT_ORDER, REDUNDANCY_VERDICT_CODES),
        (PAIR_COVERAGE_ORDER, PAIR_COVERAGE_CODES),
        (SHARED_INPUT_ORDER, SHARED_INPUT_CODES),
        (IDENTITY_COVERAGE_ORDER, IDENTITY_COVERAGE_CODES),
    ):
        assert set(order) == codes
        assert len(set(order)) == len(order)
    assert set(SUMMARY_COVERAGE_CODES) == {"measured", "insufficient_as_ofs"}
    assert REDUNDANCY_VERDICT_ORDER[0] == "arithmetic"
    assert PAIR_COVERAGE_ORDER[0] == "measured"


def test_the_summary_verdict_is_decided_by_the_magnitude_and_not_by_the_signed_mean() -> None:
    """Three days at `+0.964`, `+0.964`, `-0.964`: mean `+0.321`, mean magnitude `0.964`.

    The verdict is `redundant`, because a relationship that reverses is still a relationship and
    the two factors still carry one piece of information between them. A summary that read the
    signed mean would answer `distinct` here and would be reporting the *stability of the sign* as
    if it were the strength of the relationship -- which is the same conflation `mean_correlation`
    and `mean_abs_correlation` exist as two fields to prevent one scope down.

    The two means are asserted on opposite sides of the declared line, so no fixture where they
    agree can satisfy this.
    """
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    near = dict(zip(subjects, (0.0, 2.0, 1.0, 3.0, 4.0, 5.0, 7.0, 6.0, 8.0, 9.0), strict=True))
    flipped = {name: -value for name, value in near.items()}

    summary = RedundancyStudy(SPEC, identities=[]).summarize(
        _series([(rising, near), (rising, near), (rising, flipped)])
    )

    assert summary.mean_correlation is not None and summary.mean_abs_correlation is not None
    assert summary.mean_correlation < SPEC.redundancy_threshold
    assert summary.mean_abs_correlation >= SPEC.redundancy_threshold
    assert summary.verdict == "redundant"
    assert dict(summary.verdict_counts)["redundant"] == 3


def test_the_summarys_threshold_is_inclusive_at_exactly_the_mean_magnitude_it_names() -> None:
    """`test_the_declared_threshold_is_inclusive_at_exactly_the_correlation_it_names`, one scope up.

    The same rule is written twice -- `magnitude >= threshold` per point and `mean_abs >=
    threshold` per series -- and only the first was decidable by any fixture. The series-scope
    tests all sit well inside the line (`0.964...` against `0.8`), so `>=` and `>` agree on every
    one of them, and the sequence scope is the one a report actually shows: `TierReport.survival`
    carries the summary, not the points.

    Built on the pointwise test's own boundary rather than a new one, for the reason that test
    states: `0.7999999999999998` is the float a five-name Spearman actually produces, and `0.8`
    would sit a last bit below the line and pass either way. Two identical points make
    `statistics.fmean` return that float back unchanged, so the *mean* lands on the line too.

    The two points are also exactly `min_as_ofs`, which makes this the boundary test for the
    sequence-scope sample floor as well: at the floor the series is summarised rather than coded
    `insufficient_as_ofs`.
    """
    subjects = _subjects(5)
    boundary = _pearson(average_ranks([0, 1, 2, 3, 4]), average_ranks([0, 2, 1, 4, 3]))
    xs = dict(zip(subjects, (0.0, 1.0, 2.0, 3.0, 4.0), strict=True))
    ys = dict(zip(subjects, (0.0, 2.0, 1.0, 4.0, 3.0), strict=True))
    points = _series([(xs, ys), (xs, ys)])

    def _summarize(threshold: float) -> RedundancySummary:
        spec = RedundancySpec(
            method="spearman", min_securities=4, min_as_ofs=2, redundancy_threshold=threshold
        )
        return RedundancyStudy(spec, identities=[]).summarize(points)

    on_the_line = _summarize(boundary)
    just_above = _summarize(math.nextafter(boundary, 1.0))

    assert boundary == 0.7999999999999998
    assert len(points) == SPEC.min_as_ofs == 2
    assert on_the_line.coverage == "measured"
    assert on_the_line.mean_abs_correlation == boundary
    assert on_the_line.verdict == "redundant"
    assert just_above.mean_abs_correlation == boundary
    assert just_above.verdict == "distinct"


def test_a_summary_over_days_that_all_cleared_the_line_is_redundant() -> None:
    """The rung between `undeclared_lockstep` and `distinct`, which the disagreement test skips.

    Two as_ofs at `+0.964`: above the declared 0.8 and below the lockstep boundary, so the summary
    verdict is `redundant` and is asserted to be neither of its neighbours. A ladder whose middle
    rung nothing reaches is a ladder with an unreachable branch.
    """
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    near = dict(zip(subjects, (0.0, 2.0, 1.0, 3.0, 4.0, 5.0, 7.0, 6.0, 8.0, 9.0), strict=True))

    summary = RedundancyStudy(SPEC, identities=[]).summarize(
        _series([(rising, near), (rising, near)])
    )

    assert summary.verdict == "redundant"
    assert summary.mean_abs_correlation is not None
    assert SPEC.redundancy_threshold <= summary.mean_abs_correlation < 1.0
    assert round(summary.mean_abs_correlation, LOCKSTEP_DECIMAL_PLACES) != 1.0
    assert dict(summary.verdict_counts) == {
        "arithmetic": 0,
        "undeclared_lockstep": 0,
        "redundant": 2,
        "distinct": 0,
    }


def test_every_stored_contract_refuses_the_shapes_its_own_validators_name() -> None:
    """The stored-payload direction on all three models, one shape per branch.

    None of these is reachable through this module's own constructors -- they are what a row read
    back out of a store, or hand-assembled by a report, can carry. A validator whose failure
    nobody has ever seen is a validator this repository has measured the worth of twice.
    """
    subjects = _subjects(10)
    rising = {name: float(index) for index, name in enumerate(subjects)}
    near = dict(zip(subjects, (0.0, 2.0, 1.0, 3.0, 4.0, 5.0, 7.0, 6.0, 8.0, 9.0), strict=True))
    study = RedundancyStudy(SPEC, identities=[])
    points = _series([(rising, near), (rising, near)])
    point = points[0].model_dump()
    summary = study.summarize(points).model_dump()
    series = study.correlate_ic_series(
        _ic_series(UP, (0.05, -0.02, 0.08, -0.06)),
        _ic_series(OTHER_UP, (0.06, 0.01, 0.09, -0.05)),
    ).model_dump()

    for model, payload, update, pattern in (
        (RedundancyPoint, point, {"raw_correlation": math.nan}, "not a finite correlation"),
        (RedundancyPoint, point, {"raw_correlation": 1.5}, r"outside \[-1, 1\]"),
        (
            type(study.summarize(points)),
            summary,
            {"mean_correlation": math.inf},
            "not a finite statistic",
        ),
        (
            type(study.summarize(points)),
            summary,
            {"mean_correlation": None},
            "the 'measured' code carries the statistics",
        ),
        (
            type(study.summarize(points)),
            summary,
            {"measured_count": 3, "verdict_counts": (("redundant", 3),)},
            "cannot measure an as_of it was not given",
        ),
        (
            type(study.summarize(points)),
            summary,
            {"mean_abs_correlation": 1.5},
            r"outside \[0, 1\]",
        ),
        (ICSeriesCorrelation, series, {"correlation": math.nan}, "not a finite correlation"),
        (
            ICSeriesCorrelation,
            series,
            {"correlation": None},
            "the 'measured' code carries a correlation",
        ),
        (ICSeriesCorrelation, series, {"correlation": -1.5}, r"outside \[-1, 1\]"),
        (
            ICSeriesCorrelation,
            series,
            {"offered_as_of_count": 1},
            "cannot measure an as_of it was not given",
        ),
    ):
        with pytest.raises(ValidationError, match=pattern):
            model.model_validate({**payload, **update})


def test_the_probe_codes_are_the_codes_each_tier_admits() -> None:
    """`ADMITTED_CODE` against `factor_ic.TIER_ADMITTED_CODES`, so the fixture cannot follow it.

    Every vector in this file is built from `ADMITTED_CODE`, which is written out by hand rather
    than read off the table under test. That is deliberate -- a fixture derived from the table
    would follow it wherever it went and this file would keep passing -- and it only works if the
    two are reconciled somewhere, which is here.
    """
    assert {tier: {code} for tier, code in ADMITTED_CODE.items()} == dict(TIER_ADMITTED_CODES)
    assert set(ADMITTED_CODE) == set(TIER_COVERAGE_ORDER)
    assert all(
        ADMITTED_CODE[tier] in TIER_COVERAGE_ORDER[tier]  # type: ignore[index]
        for tier in ADMITTED_CODE
    )


def test_the_study_exposes_the_declaration_it_was_built_with() -> None:
    """A study's spec and identities are readable, so a stored report can name what produced it."""
    study = RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY])

    assert study.spec is SPEC
    assert study.identities == (COMPOUNDING_IDENTITY,)
    assert RedundancyStudy(SPEC, identities=[]).identities == ()


# --- the cross-tier self-pair `V2-P3-014` reached for and could not use ------------------------


def test_a_factor_is_related_to_nothing_by_an_identity_naming_it_beside_another() -> None:
    """`relates` takes a *pair*, and a key against itself is not one.

    Without the `!=`, `relates(k, k)` collapses to `k in members` -- true for every identity that
    names `k` alongside anybody. `members` are distinct by validator, so no declared identity can
    ever bind a factor to itself; the guard states that structurally instead of leaving it to be
    inferred, and `identity_for` therefore answers `None` for a self-pair rather than handing back
    an identity about two different factors with only one of them supplied.
    """
    assert COMPOUNDING_IDENTITY.relates("momentum_20_sessions", "reversal_5_sessions") is True
    assert COMPOUNDING_IDENTITY.relates("momentum_20_sessions", "momentum_20_sessions") is False
    assert COMPOUNDING_IDENTITY.relates("m15", "m15") is False

    study = RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY])
    assert study.identity_for("momentum_20_sessions", "reversal_5_sessions") is COMPOUNDING_IDENTITY
    assert study.identity_for("momentum_20_sessions", "momentum_20_sessions") is None


def test_one_factors_two_tiers_can_be_measured_against_each_other_under_a_live_identity() -> None:
    """The reading `correlate_cross_section` documents, measured through `measure`.

    One factor's raw and neutralized readings are two different vectors under one key -- exactly
    the shape the "a second vector was offered" refusal describes, and exactly the shape that has
    no residual for two readings to disagree about. The study here *does* declare an identity
    naming this factor, so the test separates "no identity was declared" from "no identity binds
    a factor to itself": the first would make the pass vacuous, the second is the property.

    `V2-P3-014` needed this to report how much of a factor survived its own neutralisation, hit
    the refusal, and routed around it through `correlate_cross_section`.
    """
    subjects = _subjects(4)
    study = RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY])
    definition = _definition("momentum_20_sessions")
    raw = _vector(definition, dict(zip(subjects, (1.0, 2.0, 3.0, 4.0), strict=True)), tier="raw")
    neutralized = _vector(
        definition,
        dict(zip(subjects, (4.0, 3.0, 2.0, 1.0), strict=True)),
        tier="neutralized",
    )

    point = study.measure(left=raw, right=neutralized)

    assert point.identity is None
    assert point.verdict != "arithmetic"
    assert point.raw_correlation == -1.0
    assert point.oriented_correlation == -1.0
    assert point.left_key == point.right_key == "momentum_20_sessions"
    assert point.left_tier == "raw"
    assert point.right_tier == "neutralized"


def test_a_second_reading_of_one_factor_is_refused_where_an_identity_reads_it_by_key() -> None:
    """The refusal the scoping above narrowed, still firing where it means something.

    `test_a_study_refuses_a_repeated_identity_code_and_a_second_reading_of_one_factor` already
    drove it; this one pins the half the narrowing could have taken away. The guard exists to
    protect `verify_identity`, which looks members up *by key*, so it must survive exactly where
    an identity is declared for the pair -- two vectors under one key would let the residual and
    the correlation be computed from different numbers. Moving the collection inside the
    `declared is not None` branch keeps that and drops it only where nothing reads `supplied`.
    """
    subjects = _subjects(4)
    study = RedundancyStudy(SPEC, identities=[COMPOUNDING_IDENTITY])
    m20 = _definition("momentum_20_sessions")
    r5 = _definition("reversal_5_sessions")
    m15 = _definition("m15")
    values = dict(zip(subjects, (1.0, 2.0, 3.0, 4.0), strict=True))
    other = dict(zip(subjects, (9.0, 8.0, 7.0, 6.0), strict=True))

    with pytest.raises(FactorRedundancyError, match="a second vector was offered"):
        study.measure(
            left=_vector(m20, values),
            right=_vector(r5, values),
            vectors={
                "m15": _vector(m15, values),
                "momentum_20_sessions": _vector(m20, other),
            },
        )
