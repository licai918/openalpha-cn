"""The volatility and liquidity family's own arithmetic and its declared properties (`V2-P3-013`).

Everything here runs without a store, because the four evaluators are pure functions of a window
and the four definitions are data. What needs a real partition -- that `daily.amount` and
`daily_basic.turnover_rate` are columns a written partition actually binds, that a 60-session
reach forms the window the contract says it does, and that the span bound is separable from the
count -- is in `tests/integration/panel/test_volatility_liquidity_family.py`.

## The four claims this file exists to make executable

**Why exactly one factor here is named for a residual is a fact about the code, not a sentence in
a note, and this file used to say the opposite.** The roadmap line asks for residual and
idiosyncratic volatility, which are one construct and need a market return series. Until
`V2-P3-016` two things stood in the way -- none of the fifteen declared Tushare datasets carried
an index's price, and `FactorWindow` carried one subject's rows -- and
`test_the_reason_no_residual_ships_is_a_property_of_the_panel_and_of_the_window` pinned both,
with its own docstring saying the day somebody ingested an index series was the day the
disclosure needed revisiting. That day came, so the test is **inverted** rather than deleted:
`test_exactly_one_dataset_carries_a_level_and_exactly_one_channel_reaches_it` now asserts that
exactly one dataset is a level series, exactly one window channel reaches it, exactly one index
is reachable through it and exactly one shipped factor declares it -- so widening once more and
quietly reverting are both red. Neither obstacle was ever arithmetic: the univariate regression
a residual volatility needs has a closed form and is `O(n)` in pure Python, so `ADR-0003`'s
numerical-stack question was never the one that blocked this, which is what its own 2026-08-12
correction says.

**The unit of `daily.amount` is measured, not read off a field list.** `AMIHUD_60`'s value is a
ratio whose denominator is money, so its unit is the column's unit, and a denominator wrong by a
factor of 1,000 is invisible to every check downstream of it -- a rank IC and a z-score are both
scale-free, so a scaled Amihud ranks identically and standardises identically. The measurement is
`test_the_amount_column_is_thousands_of_yuan_and_the_other_readings_are_out_of_range`, and it is a
measurement rather than an assertion because the low-high range of each session is an independent
witness the column pair has to land inside.

**The return path is the one `domain/daily_prices.py` measured, and the wrong one is driven to
show it is wrong.** All three return-based factors here are built on `close / pre_close - 1`. The
naive `close[t] / close[t-1] - 1` is wrong by up to 118.30 over the 37,602 rows that module
measured, and on `000001.SZ`'s 2026-06-12 ex-dividend morning it reverses the *sign*.
`test_the_return_path_is_close_over_pre_close_and_the_naive_path_has_the_other_sign` runs both
over the real published rows and reconciles the chosen one against the row's own `pct_chg`.

**No two of the four answer with one number, and that is asserted rather than hoped.** The
`V2-P3-004` review's finding was a column that *was* asserted, on a fixture where the assertion
could not tell two answers apart.
`test_the_four_factors_answer_four_different_numbers_on_one_window` drives all four off a single
window and pins each magnitude, and three further tests pin the specific coincidences that would be
easy to write by accident: a downside deviation divided by its own negative count rather than by
the window, an Amihud that skipped the sessions it could not divide by, and a dispersion computed
over N-1 returns because the returns came from close-to-close pairs.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Final

import pytest

from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_BASIC_DATA_COLUMNS,
    DAILY_BASIC_DATASET,
    DAILY_BASIC_NULLABLE_COLUMNS,
    DAILY_DATA_COLUMNS,
    DAILY_DATASET,
    MAX_PUBLISHED_RETURN_DISAGREEMENT,
    PRE_CLOSE_COLUMN,
)
from openalpha_cn.domain.factor import FactorField
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET
from openalpha_cn.domain.index_prices import (
    INDEX_DAILY_DATASET,
    INDEX_PRICE_INDEX_CODES,
    MARKET_INDEX_CODE,
)
from openalpha_cn.panel_factors import (
    AMIHUD_60,
    AMOUNT_COLUMN,
    CNY_PER_AMOUNT_UNIT,
    DOWNSIDE_VOL_60,
    FACTOR_DEFINITIONS,
    FACTOR_EVALUATORS,
    RETURN_VOL_60,
    SHARED_SUBJECT_DATASETS,
    TURNOVER_60,
    TURNOVER_RATE_COLUMN,
    VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS,
    VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS,
    FactorEngineError,
    FactorWindow,
    _amihud_60,
    _downside_vol_60,
    _return_vol_60,
    _sample_stdev,
    _session_returns,
    _turnover_60,
)
from openalpha_cn.providers.tushare import TUSHARE_DATASETS

AS_OF: Final[datetime] = datetime(2026, 6, 15, 4, 0, tzinfo=UTC)

FAMILY: Final[str] = "volatility_liquidity"

THE_FOUR: Final[tuple[str, ...]] = (
    "return_vol_60/v1",
    "downside_vol_60/v1",
    "turnover_60/v1",
    "amihud_60/v1",
)
"""The four `V2-P3-013` shipped, kept as its own name after the family grew to five.

Not folded into `THE_FAMILY`, because the difference between the two tuples is the whole of what
`V2-P3-016` did: these four are the ones that were built without a market return series, and
`RESIDUAL_VOL_60` is the one that could not be.
"""

THE_FAMILY: Final[tuple[str, ...]] = (*THE_FOUR, "residual_vol_60/v1")
"""Every `volatility_liquidity` member this build ships, in registry order.

`V2-P3-016` is the fifth and it is deliberately **one** factor rather than two. The roadmap line
`V2-P3-013` could not satisfy names "residual volatility" and "idiosyncratic volatility"; those
are one construct in the literature and are told apart by the right-hand side of the regression
(CAPM against a three-factor model). This panel holds one explanatory series, so a second name
would address the identical number computed by the identical evaluator --
`test_only_one_residual_ships_because_the_panel_holds_one_explanatory_series` is that argument
as an assertion rather than as a sentence in a note.
"""

# --- the real published rows the two unit measurements are taken on -------------------------------

# `(subject, session, low, high, vol, amount)` for eleven real Tushare `daily` rows spanning
# 2001-01-02 to 2026-06-15, the same rows `tests/unit/domain/test_daily_prices.py` carries as
# `BARS`, `MAOTAI_BAR_2024_06_25` and `COARSE_GRID_BARS`. Restated here as the six fields this
# module's measurement reads, because what is being measured is a property of the *feed* -- the
# unit of a column -- and the witness is that the implied VWAP has to fall inside the session's
# own traded range.
# fmt: off
PRICED_SESSIONS: Final[tuple[tuple[str, str, float, float, float, float], ...]] = (
    ("000001.SZ", "2026-06-10", 11.07, 11.35, 1543176.39, 1738056.71203),
    ("000001.SZ", "2026-06-11", 11.25, 11.39, 1156222.22, 1308133.97213),
    ("000001.SZ", "2026-06-12", 10.88, 11.25, 2032355.46, 2263042.93057),
    ("000001.SZ", "2026-06-15", 10.98, 11.21, 1541304.95, 1711561.28657),
    ("600519.SH", "2026-06-10", 1250.21, 1282.00, 39244.14, 4991686.419),
    ("600519.SH", "2026-06-11", 1266.91, 1282.88, 25351.98, 3230008.22),
    ("600519.SH", "2026-06-12", 1265.01, 1295.00, 50494.78, 6477910.214),
    ("600519.SH", "2026-06-15", 1270.10, 1292.70, 41585.56, 5303656.129),
    ("600519.SH", "2024-06-25", 1477.00, 1502.99, 42097.95, 6273919.386),
    ("002736.SZ", "2016-10-10", 16.49, 16.80, 93560.77, 155786.2351),
    ("000569.SZ", "2001-01-02", 7.15, 7.32, 9098.13, 6579.5778),
)
# fmt: on

SHARES_PER_LOT: Final[float] = 100.0
"""The other half of the unit pair: Tushare publishes `daily.vol` in lots of 100 shares.

Read off the same eleven rows by the same witness -- it is `amount * CNY_PER_AMOUNT_UNIT /
(vol * SHARES_PER_LOT)` that lands in `[low, high]`, so the two constants are measured together
and neither is separately checkable. Declared here rather than in `src/` because nothing this
repository computes divides by it: `AMIHUD_60`'s denominator is money, not shares.
"""

# `000001.SZ` and `600519.SH` on 2026-06-12, the one session this repository stores both turnover
# columns and both share counts for. `float_share` and `free_share` are in ten-thousands of shares
# and `vol` is in lots, so `vol / share_count` is already the turnover percentage.
# `(subject, turnover_rate, turnover_rate_f, vol, float_share, free_share)`.
# fmt: off
TURNOVER_ROWS: Final[tuple[tuple[str, float, float, float, float, float], ...]] = (
    ("000001.SZ", 1.0473, 2.4905, 2032355.46, 1940560.0653, 816048.1215),
    ("600519.SH", 0.4039, 0.9334, 50494.78, 125008.1601, 54094.8978),
)
# fmt: on

TURNOVER_PUBLICATION_TICK: Final[float] = 1e-4
"""One tick of a four-decimal published percentage; both columns above carry four decimals."""

# `000001.SZ` across its 2026-06-12 ex-dividend morning, verbatim from
# `domain/daily_prices.py`'s own worked example: `(session, close, pre_close, pct_chg)`.
EX_RIGHTS_WINDOW: Final[tuple[tuple[str, float, float, float], ...]] = (
    ("2026-06-11", 11.30, 11.32, -0.1767),
    ("2026-06-12", 11.24, 10.94, 2.7422),
)


# --- the window the four evaluators are driven off ------------------------------------------------


PROBE_PRE_CLOSES: Final[tuple[float, ...]] = (100.0, 100.0, 100.0, 100.0)
PROBE_CLOSES: Final[tuple[float, ...]] = (110.0, 95.0, 102.0, 92.0)
"""Four sessions whose returns are +0.10, -0.05, +0.02 and -0.08.

Two up and two down, so a downside deviation is neither zero nor equal to the total one, and the
count of negatives (2) differs from the window length (4) -- which is what lets
`test_the_downside_deviation_divides_by_the_window_and_not_by_its_own_negative_count` separate the
two divisors. A constant `pre_close` keeps the returns exact enough to hand-check while leaving
the *path* to the ex-rights test, where a constant one would prove nothing.
"""

PROBE_AMOUNTS: Final[tuple[float, ...]] = (1000.0, 2000.0, 500.0, 4000.0)
"""Thousands of yuan, four values with no two alike, so an Amihud that dropped or reordered a
session cannot land on the same mean."""

PROBE_TURNOVER: Final[tuple[float, ...]] = (1.0, 2.0, 3.0, 6.0)
"""Percent. Their mean is exactly 3.0, which no other quantity in this fixture is near."""

PROBE_RETURNS: Final[tuple[float, ...]] = (0.10, -0.05, 0.02, -0.08)

EXPECTED_RETURN_VOL: Final[float] = 0.08015609770940699
"""`sqrt(((0.1025)^2 + (0.0475)^2 + (0.0225)^2 + (0.0775)^2) / 3)` about a mean of -0.0025."""

EXPECTED_DOWNSIDE_VOL: Final[float] = 0.047169905660283014
"""`sqrt((0.05^2 + 0.08^2) / 4)` -- the divisor is the window, not the two negatives."""

EXPECTED_DOWNSIDE_VOL_OVER_ITS_NEGATIVES: Final[float] = 0.06670832032063167
"""`sqrt((0.05^2 + 0.08^2) / 2)`, the answer the other divisor gives. Never expected; asserted
against so that the two are known to be distinguishable on this fixture."""

EXPECTED_TURNOVER: Final[float] = 3.0

EXPECTED_AMIHUD: Final[float] = 4.625e-08
"""`(0.10/1e6 + 0.05/2e6 + 0.02/5e5 + 0.08/4e6) / 4`, the amounts carried into yuan."""

EXPECTED_AMIHUD_WITHOUT_THE_UNIT: Final[float] = 4.625e-05
"""The same mean with the denominator left in thousands of yuan -- 1,000 times the answer, and
the whole reason `CNY_PER_AMOUNT_UNIT` is measured rather than assumed."""


def _window(
    *,
    closes: tuple[float, ...] = PROBE_CLOSES,
    pre_closes: tuple[float, ...] = PROBE_PRE_CLOSES,
    amounts: tuple[float, ...] | None = PROBE_AMOUNTS,
    turnover: tuple[float, ...] | None = PROBE_TURNOVER,
) -> FactorWindow:
    """One security's complete window carrying every column this family declares.

    All four columns on one window on purpose: the four factors are then driven off *identical*
    inputs, which is the only arrangement in which "do any two of them answer with one number" is
    a question with a meaningful answer.
    """
    values: dict[tuple[str, str], tuple[float, ...]] = {
        (DAILY_DATASET, CLOSE_COLUMN): closes,
        (DAILY_DATASET, PRE_CLOSE_COLUMN): pre_closes,
    }
    if amounts is not None:
        values[(DAILY_DATASET, AMOUNT_COLUMN)] = amounts
    if turnover is not None:
        values[(DAILY_BASIC_DATASET, TURNOVER_RATE_COLUMN)] = turnover
    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=tuple(date(2026, 6, 8 + index) for index in range(len(closes))),
        periods=(),
        values=MappingProxyType(values),
    )


# --- the two column units, measured ---------------------------------------------------------------


def test_the_amount_column_is_thousands_of_yuan_and_the_other_readings_are_out_of_range() -> None:
    """`CNY_PER_AMOUNT_UNIT`, measured against an independent witness rather than declared.

    A session's implied VWAP has to fall between its own low and its own high. Only one reading of
    the `(vol, amount)` pair does, on all eleven rows: lots and thousands of yuan. The other three
    are each out by a factor of ten or more on every row, which is what makes the separation robust
    rather than a coincidence of one session -- `000001.SZ` on 2026-06-12 gives 11.1351 inside
    [10.88, 11.25] against 1.1135 for "shares and yuan".

    Asserted in both directions. The one-directional version -- "the chosen reading is in range" --
    would pass for a fixture whose range happened to be wide, and the range on `000569.SZ`'s
    2001-01-02 row is 2.4% of its own price.
    """
    inside: list[str] = []
    for subject, session, low, high, vol, amount in PRICED_SESSIONS:
        chosen = amount * CNY_PER_AMOUNT_UNIT / (vol * SHARES_PER_LOT)
        assert low <= chosen <= high, f"{subject} {session}: {chosen} outside [{low}, {high}]"
        inside.append(f"{subject} {session}")

        for name, rejected in (
            ("shares and yuan", amount / vol),
            ("lots and yuan", amount / (vol * SHARES_PER_LOT)),
            ("shares and thousands", amount * CNY_PER_AMOUNT_UNIT / vol),
        ):
            assert not low <= rejected <= high, f"{subject} {session}: {name} also fits"

    assert len(inside) == 11
    assert CNY_PER_AMOUNT_UNIT == 1000.0


def test_the_turnover_rate_column_is_float_share_turnover_and_the_f_column_is_free_float() -> None:
    """Which denominator each of the two candidate columns divides by, driven rather than cited.

    `TURNOVER_60` chooses between them, so knowing which is which is a precondition for the choice
    being a choice at all. Each column is reconciled against its *own* share count to within one
    published tick, and against the other's to show the reconciliation discriminates -- on both
    names the free-float measure is more than twice the float measure, so the two columns are not
    interchangeable and the pick moves every value this factor produces.
    """
    for subject, rate, rate_f, vol, float_share, free_share in TURNOVER_ROWS:
        assert vol / float_share == pytest.approx(rate, abs=TURNOVER_PUBLICATION_TICK)
        assert vol / free_share == pytest.approx(rate_f, abs=TURNOVER_PUBLICATION_TICK)

        assert vol / free_share != pytest.approx(rate, abs=TURNOVER_PUBLICATION_TICK)
        assert vol / float_share != pytest.approx(rate_f, abs=TURNOVER_PUBLICATION_TICK)
        assert rate_f > 2.0 * rate, subject


def test_the_family_reads_the_fail_closed_turnover_column_and_not_the_nullable_one() -> None:
    """The choice `TURNOVER_60_NOTE` argues, held against the contract that decides its cost.

    `turnover_rate_f` is in `DAILY_BASIC_NULLABLE_COLUMNS`, which is the writer's statement that a
    null in it is data rather than a fault -- and because the engine hands an evaluator only
    complete windows, one null session is `input_missing` for all 60 `as_of`s whose window contains
    it. `turnover_rate` is in the fail-closed complement. Asserted on the domain's own set rather
    than on prose, so moving a column between the two sets fails here.
    """
    assert TURNOVER_RATE_COLUMN not in DAILY_BASIC_NULLABLE_COLUMNS
    assert "turnover_rate_f" in DAILY_BASIC_NULLABLE_COLUMNS

    read = {
        field.column
        for item in FACTOR_DEFINITIONS.definitions
        if item.family == FAMILY
        for field in item.required_fields
    }
    assert read & DAILY_BASIC_NULLABLE_COLUMNS == set()
    assert TURNOVER_RATE_COLUMN in read

    # Scoped to this family rather than to the whole registry, and the narrowing is a judgement
    # rather than caution: which of `daily_basic`'s seventeen columns a factor may read is a
    # question each family answers for itself. This comment used to predict that `V2-P3-009`'s EP
    # and BP would read `pe` and `pb`; they read `total_mv`, which is in the fail-closed
    # complement, and that issue's own note argues the inversion down on the null rate. The
    # scoping stands on the general form of that reason rather than on the prediction: `pe`,
    # `pe_ttm`, `pb`, `ps` and `ps_ttm` are *legitimately* nullable -- a loss-making company has no
    # P/E, and `daily_prices.py` measures 1,102 of 5,338 rows null on one session -- so a
    # registry-wide rule here would be this issue's branch deciding a question that belongs to
    # whichever issue first reaches for one of them.


def test_the_columns_this_family_reads_are_columns_the_daily_contract_declares() -> None:
    """The binding a shared constant would have bought, as a check instead.

    `AMOUNT_COLUMN` and `TURNOVER_RATE_COLUMN` are spelled in `panel_factors` while
    `domain/daily_prices.py` carries them only inside its column tuples. `FactorField` validates a
    reference *syntactically* and says so, so a typo would survive until `compute_factor` bound the
    projection -- and only for a caller who happened to run that factor.
    """
    assert AMOUNT_COLUMN in DAILY_DATA_COLUMNS
    assert TURNOVER_RATE_COLUMN in DAILY_BASIC_DATA_COLUMNS
    assert CLOSE_COLUMN in DAILY_DATA_COLUMNS
    assert PRE_CLOSE_COLUMN in DAILY_DATA_COLUMNS


# --- the declared properties ----------------------------------------------------------------------


def test_the_family_is_exactly_five_definitions_and_they_are_this_builds_only_members() -> None:
    """Both directions: the five this family owns are declared, and nothing else claims it.

    The second half is what a per-factor test cannot cover. `FactorFamily` is a closed set because
    `V2-P3-008` groups by it and `V2-P3-014` reports per family, so a sixth member arriving from
    somewhere else -- a `V2-P3-012` momentum factor mis-labelled, say -- would silently join this
    family's redundancy group and its report tier.

    The fifth arrived from `V2-P3-016` and joins on purpose rather than by omission: it shares
    this family's declared horizon (`VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS` / `..._MAX_WINDOW_..`)
    precisely so that `V2-P3-008`'s redundancy analysis compares it against `return_vol_60` as a
    statement about content rather than about horizon. `THE_FOUR` is kept beside `THE_FAMILY` so
    that the prefix relation -- the four that shipped without a market series, and the one that
    could not -- is an assertion instead of an ordering nobody checks.
    """
    declared = tuple(
        item.qualified_key for item in FACTOR_DEFINITIONS.definitions if item.family == FAMILY
    )

    assert declared == THE_FAMILY
    assert THE_FAMILY[: len(THE_FOUR)] == THE_FOUR
    assert set(THE_FAMILY) <= set(FACTOR_EVALUATORS)
    assert {FACTOR_DEFINITIONS.get(handle).lookback_sessions for handle in THE_FAMILY} == {
        VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS
    }
    assert {FACTOR_DEFINITIONS.get(handle).max_window_sessions for handle in THE_FAMILY} == {
        VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS
    }


@pytest.mark.parametrize(
    ("definition", "direction", "fields"),
    [
        (
            RETURN_VOL_60,
            "lower_is_better",
            ((DAILY_DATASET, CLOSE_COLUMN), (DAILY_DATASET, PRE_CLOSE_COLUMN)),
        ),
        (
            DOWNSIDE_VOL_60,
            "lower_is_better",
            ((DAILY_DATASET, CLOSE_COLUMN), (DAILY_DATASET, PRE_CLOSE_COLUMN)),
        ),
        (TURNOVER_60, "lower_is_better", ((DAILY_BASIC_DATASET, TURNOVER_RATE_COLUMN),)),
        (
            AMIHUD_60,
            "higher_is_better",
            (
                (DAILY_DATASET, CLOSE_COLUMN),
                (DAILY_DATASET, PRE_CLOSE_COLUMN),
                (DAILY_DATASET, AMOUNT_COLUMN),
            ),
        ),
    ],
    ids=["return_vol_60", "downside_vol_60", "turnover_60", "amihud_60"],
)
def test_each_definition_declares_the_session_reach_and_no_report_period_reach(
    definition: object, direction: str, fields: tuple[tuple[str, str], ...]
) -> None:
    """Every property the engine reads, on every member, including the two absences.

    `lookback_periods is None` is a declaration and not an unset field -- `FactorDefinition`
    requires each axis to be declared exactly when `required_fields` puts the factor on it, so this
    is the contract's own statement that a quarterly dispersion reads no filing.

    The directions are the family's conventional priors and nothing here has measured any of them;
    what *is* asserted is that the two liquidity factors were not given the same sign by reflex.
    `turnover_60` is `lower_is_better` and `amihud_60` is `higher_is_better`, which is one
    economics -- less liquid is taken to be better -- written twice with the sign flipped, because
    a high Amihud and a low turnover are the same state.
    """
    assert isinstance(definition, type(RETURN_VOL_60))
    assert definition.family == FAMILY
    assert definition.direction == direction
    assert definition.required_fields == tuple(
        FactorField(dataset=dataset, column=column) for dataset, column in fields
    )
    assert definition.lookback_sessions == VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS
    assert definition.max_window_sessions == VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS
    assert definition.lookback_periods is None
    assert definition.max_window_periods is None
    assert definition.period_datasets == ()


def test_the_span_bound_allows_one_trading_month_of_halt_and_no_more() -> None:
    """The two constants' *relationship*, which is the thing a comment would let go stale.

    The slack is the number of sessions a security may have missed inside its own window, and both
    ends of it are decided: not zero, because half a percent of the market is halted on an ordinary
    session and a zero-slack quarterly factor would refuse every name that took one announcement
    halt; not more than a trading month, because 2024's 242 sessions are 20.2 to the month and 80
    panel sessions already reach across four calendar months.
    """
    slack = VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS - VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS

    assert VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS == 60
    assert slack == 20
    assert slack * 12 <= 242
    assert VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS > VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS


REQUIRED_DISCLOSURES: Final[dict[str, tuple[str, ...]]] = {
    "return_vol_60/v1": (
        "measured nothing about",
        "deliberately NOT named for a residual",
        "The second stopped being true at V2-P3-016",
        "it is TOTAL volatility",
        "close / pre_close - 1",
    ),
    "downside_vol_60/v1": (
        "measured nothing about",
        "it is not the residual of any",
        "stopped being true at V2-P3-016",
        "NOT the number of negative returns",
    ),
    "residual_vol_60/v1": (
        "measured nothing about",
        "b = cov(r, r_m) / var(r_m)",
        "ONE factor ships here and not two",
        "N-2 rather than N-1",
        "var(r_m) over the window is zero",
    ),
    "turnover_60/v1": (
        "measured nothing about",
        "It reads turnover_rate and not turnover_rate_f",
        "DAILY_BASIC_NULLABLE_COLUMNS",
    ),
    "amihud_60/v1": (
        "measured nothing about",
        "published in THOUSANDS of yuan",
        "undefined_value",
    ),
}
"""What each member's prose has to say, as a table the suite evaluates.

**Two of these rows are corrections rather than additions**, and that is the point of asserting
prose in executable code at all. `return_vol_60` and `downside_vol_60` both used to be required
to say that no residual volatility was computable in this build; `V2-P3-016` made that false, so
the required phrase is now the one that says *when* it stopped being true. A table that had only
grown a fifth row would have left two shipped factors asserting a sentence the code had refuted
-- which is the shape of every Critical finding this repository has taken.

This is the same binding `KNOWN_*` registries get from
`tests/unit/test_known_limitation_registries.py` -- a string literal asserted in *executable* test
code -- applied to a `FactorNote` instead of to a `code`. A new `KNOWN_*` registry was considered
for the residual disclosure and not added: the note is where a factor's judgements already have to
live (`test_every_shipped_contract_carries_its_prose` makes one mandatory and holds it to a length),
so a registry beside it would be a second place for the same sentence to drift from.
"""


def test_exactly_one_dataset_carries_a_level_and_exactly_one_channel_reaches_it() -> None:
    """The two blockers `V2-P3-013` measured, pinned **inverted** now that both are gone.

    This test used to be `test_the_reason_no_residual_ships_is_a_property_of_the_panel_and_of_the_
    window` and it asserted the two absences: that none of the fifteen declared datasets carried
    an index's level, and that `FactorWindow` had five fields of which none could hold a series
    belonging to another subject. Its own docstring said "the day somebody ingests an index price
    series is the day this family's disclosure needs revisiting". `V2-P3-016` is that day, so the
    test goes red by design and is turned round rather than deleted -- `V2-P3-017`'s treatment of
    `test_no_stored_statement_projection_carries_a_deducted_profit_column`, on the other blocker
    of the same shape.

    **Inverted means "exactly one", not "at least one".** Each half below names the thing that is
    now served, the thing that is deliberately still *not* served, and the count between them, so
    that widening once more and quietly reverting are both red:

    - Sixteen datasets, of which **`index_daily` is the only level series** and `index_weight` is
      still a constituent *weight* rather than a second one. A seventeenth `*_daily` dataset, or
      `index_daily` being dropped again, fails the count and the membership respectively.
    - `SHARED_SUBJECT_DATASETS` has **exactly one** entry, and its subject is **exactly one** of
      the three index codes the build fetches. The other two are stored and are unreachable from
      any evaluator, which is the scope choice `MARKET_INDEX_CODE` records: naming an index per
      factor would need a field on `FactorDefinition`, and that is the model `factor_id` is the
      content address of.
    - `FactorWindow` has **six** fields, the sixth is `shared`, and `subject` is still a single
      `str`. Folding the market series into `values` instead would have kept the field set at
      five and made `subject` false about part of its own contents -- so the field set is checked
      by equality rather than by containment, which is what makes that shortcut red too.
    - **Exactly one shipped factor declares a shared-subject dataset.** A second one would be a
      decision somebody has to write down here.

    What is *not* asserted here is the arithmetic; `_residual_vol_60`'s own tests do that. What
    is asserted is that the two structural obstacles are gone and that each is gone by exactly
    one step, which is the claim `ADR-0003`'s 2026-08-12 correction hangs on: the blocker was the
    data and the window's shape, never the numerical stack.
    """
    declared = {descriptor.dataset for descriptor in TUSHARE_DATASETS}
    levels = {name for name in declared if name.endswith("_daily")}

    assert len(declared) == 16
    assert levels == {INDEX_DAILY_DATASET}
    assert INDEX_WEIGHT_DATASET in declared and INDEX_WEIGHT_DATASET not in levels

    assert dict(SHARED_SUBJECT_DATASETS) == {INDEX_DAILY_DATASET: MARKET_INDEX_CODE}
    assert MARKET_INDEX_CODE in INDEX_PRICE_INDEX_CODES
    unreachable = set(INDEX_PRICE_INDEX_CODES) - set(SHARED_SUBJECT_DATASETS.values())
    assert len(unreachable) == 2 and MARKET_INDEX_CODE not in unreachable

    fields = {field.name for field in dataclasses.fields(FactorWindow)}
    assert fields == {"subject", "as_of", "sessions", "periods", "values", "shared"}
    assert isinstance(_window().subject, str)

    reaching = {
        definition.qualified_key
        for definition in FACTOR_DEFINITIONS.definitions
        if set(definition.datasets) & set(SHARED_SUBJECT_DATASETS)
    }
    assert reaching == {"residual_vol_60/v1"}


def test_only_one_residual_ships_because_the_panel_holds_one_explanatory_series() -> None:
    """Why the roadmap's two names became one factor, as an assertion rather than a paragraph.

    `V2-P3-013`'s line asks for "residual volatility" and "idiosyncratic volatility". They are one
    construct -- the dispersion of what a model does not explain -- and the literature tells them
    apart by the model: CAPM for the first, a three-factor model for the second. The regressor set
    this build can reach is a **singleton**, so the two would be the same number computed by the
    same evaluator under two `factor_id`s.

    That is the `V2-P3-004` review's finding in advance rather than after: a second definition
    could be declared, would be given a distinct address, would produce an identical value on
    every window, and no fixture in this repository could tell the two apart. So one ships, and
    what this test pins is the premise -- one shared dataset, one reachable subject, one factor
    reading it -- rather than the absence of a factor nobody wrote, which is unassertable.
    """
    reachable_series = {(dataset, subject) for dataset, subject in SHARED_SUBJECT_DATASETS.items()}

    assert len(reachable_series) == 1
    assert sum(1 for handle in THE_FAMILY if "residual" in handle) == 1
    assert "idiosyncratic_vol_60/v1" not in FACTOR_DEFINITIONS.qualified_keys

    note = FACTOR_DEFINITIONS.note_for("residual_vol_60/v1")
    assert note is not None
    assert "ONE factor ships here and not two" in note
    assert "two factor_ids over one answer" in note


@pytest.mark.parametrize("handle", THE_FAMILY)
def test_every_member_discloses_what_it_is_and_is_not(handle: str) -> None:
    """The honesty standard `REVERSAL_1D_NOTE` set, required of all four rather than assumed.

    The roadmap line for this issue names "residual volatility" and "idiosyncratic volatility", and
    neither is computable in this build: the panel holds no market return series, and `FactorWindow`
    carries one security's own rows so an evaluator could not read one if it did. A family that
    shipped under those names would be a declared property this repository cannot support, which is
    the shape of all thirteen Critical findings it has taken.

    Parametrised over `THE_FOUR` and keyed on an explicit per-factor table, so the assertion is the
    same strength for every member -- a shared substring checked over all four would be satisfied
    by one factor's disclosure appearing in another's note.
    """
    note = FACTOR_DEFINITIONS.note_for(handle)

    assert note is not None
    missing = [phrase for phrase in REQUIRED_DISCLOSURES[handle] if phrase not in note]
    assert missing == [], f"{handle} no longer discloses {missing}"


# --- the return path ------------------------------------------------------------------------------


def test_the_return_path_is_close_over_pre_close_and_the_naive_path_has_the_other_sign() -> None:
    """The defect that would have reached all three return-based factors at once.

    `domain/daily_prices.py` measures three ways to compute a session return and one is wrong.
    Across `000001.SZ`'s 2026-06-12 ex-dividend morning the chosen path answers +2.7422% and the
    naive close-to-close answers -0.5310%, with the sign reversed -- because `pre_close` on the
    12th is 10.94 while the 11th closed at 11.30.

    Both halves are driven: the chosen path is reconciled against the row's own `pct_chg` to within
    `MAX_PUBLISHED_RETURN_DISAGREEMENT`, which is that module's own calibrated bound, and the naive
    path is computed here and asserted to disagree in sign. A test that only checked the first half
    would pass for an implementation that also happened to be right for the wrong reason.
    """
    closes = tuple(close for _, close, _, _ in EX_RIGHTS_WINDOW)
    pre_closes = tuple(pre for _, _, pre, _ in EX_RIGHTS_WINDOW)

    returns = _session_returns(_window(closes=closes, pre_closes=pre_closes))

    assert returns is not None
    for value, (_, _, _, pct_chg) in zip(returns, EX_RIGHTS_WINDOW, strict=True):
        assert value == pytest.approx(pct_chg / 100.0, abs=MAX_PUBLISHED_RETURN_DISAGREEMENT)

    naive = closes[1] / closes[0] - 1.0
    assert returns[1] > 0.0
    assert naive < 0.0
    assert naive == pytest.approx(-0.00530973, abs=1e-8)


def test_a_window_of_n_sessions_yields_exactly_n_returns() -> None:
    """The property the whole family's sample size rests on, and the reason the path is per-row.

    Each return is computed inside its own session's row, so the count of returns is the count of
    sessions -- which is `lookback_sessions`, which is the N every divisor here uses. A
    close-to-close path would yield N-1 and put an off-by-one in the denominator of every value.
    """
    for count in (2, 4, 7):
        window = _window(closes=PROBE_CLOSES[:1] * count, pre_closes=PROBE_PRE_CLOSES[:1] * count)
        returns = _session_returns(window)

        assert returns is not None
        assert len(returns) == count == len(window.sessions)


@pytest.mark.parametrize(
    "evaluator",
    [_session_returns, _return_vol_60, _downside_vol_60, _amihud_60],
    ids=["_session_returns", "_return_vol_60", "_downside_vol_60", "_amihud_60"],
)
def test_a_zero_pre_close_is_undefined_rather_than_a_crash(evaluator: object) -> None:
    """The `undefined_value` branch, driven directly on every function that can reach it.

    Unreachable through this repository's own writers -- `DAILY_PRICE_COLUMNS` records no null and
    no non-positive value in any of the five across 58,055 bars spanning 2001 to 2026, and
    `daily_bars_from_panel_rows` refuses one -- so a guard whose only evidence was a docstring would
    be exactly the declaration this repository has been wrong about thirteen times.
    """
    window = _window(pre_closes=(100.0, 0.0, 100.0, 100.0))

    assert callable(evaluator)
    assert evaluator(window) is None


@pytest.mark.parametrize(
    "evaluator",
    [_return_vol_60, _downside_vol_60, _turnover_60, _amihud_60],
    ids=["_return_vol_60", "_downside_vol_60", "_turnover_60", "_amihud_60"],
)
def test_an_empty_window_is_undefined_on_every_member_rather_than_a_zero_division(
    evaluator: object,
) -> None:
    """The other guard the engine's window formation makes unreachable, driven on all four.

    `_form_window` returns `None` for a security short of the reach and the reach here is 60, so no
    evaluator in production is ever handed a window of length zero. Three of these four would
    divide by `len(...)` if one arrived and the fourth would take a square root of a zero-length
    mean; a guard whose only evidence is that argument is a guard nobody has run.
    """
    empty = _window(closes=(), pre_closes=(), amounts=(), turnover=())

    assert callable(evaluator)
    assert evaluator(empty) is None


def test_a_dispersion_of_fewer_than_two_returns_is_undefined_rather_than_a_zero_division() -> None:
    """`_sample_stdev`'s own guard. Unreachable at any reach this family declares -- the engine
    hands an evaluator exactly `lookback_sessions` rows and the smallest here is 60 -- so it is
    driven on the helper rather than through a factor."""
    assert _sample_stdev(()) is None
    assert _sample_stdev((0.5,)) is None
    assert _sample_stdev((0.0, 1.0)) == pytest.approx(math.sqrt(0.5))


# --- the four answers -----------------------------------------------------------------------------


def test_the_four_factors_answer_four_different_numbers_on_one_window() -> None:
    """Every magnitude pinned, and no two of them equal, off one set of inputs.

    The `V2-P3-004` review's finding was not an unasserted column; it was a column asserted on a
    fixture where the assertion could not separate two answers. Four factors of one family are
    exactly where that recurs, so the four are driven off identical inputs and each is held to a
    number a reader can recompute from `PROBE_CLOSES` by hand.
    """
    window = _window()

    answers = {
        "return_vol_60": _return_vol_60(window),
        "downside_vol_60": _downside_vol_60(window),
        "turnover_60": _turnover_60(window),
        "amihud_60": _amihud_60(window),
    }

    assert answers["return_vol_60"] == pytest.approx(EXPECTED_RETURN_VOL)
    assert answers["downside_vol_60"] == pytest.approx(EXPECTED_DOWNSIDE_VOL)
    assert answers["turnover_60"] == pytest.approx(EXPECTED_TURNOVER)
    assert answers["amihud_60"] == pytest.approx(EXPECTED_AMIHUD)

    values = [value for value in answers.values() if value is not None]
    assert len(values) == 4
    assert len({round(value, 12) for value in values}) == 4


def test_the_amihud_denominator_is_yuan_and_not_the_columns_own_thousands() -> None:
    """The factor-of-1,000 nothing downstream would catch, asserted at the one place it can be.

    A rank IC and a z-score are both scale-free, so an Amihud built on the raw column ranks and
    standardises identically to one built on yuan. The unit only shows up in a number somebody
    reads, which is why it is pinned here against the answer the unconverted denominator gives.
    """
    value = _amihud_60(_window())

    assert value == pytest.approx(EXPECTED_AMIHUD)
    assert value != pytest.approx(EXPECTED_AMIHUD_WITHOUT_THE_UNIT)
    assert pytest.approx(EXPECTED_AMIHUD * CNY_PER_AMOUNT_UNIT) == EXPECTED_AMIHUD_WITHOUT_THE_UNIT


def test_the_downside_deviation_divides_by_the_window_and_not_by_its_own_negative_count() -> None:
    """The divisor that would make a sample size a function of the data.

    Two of `PROBE_RETURNS`' four are negative, so the two divisors give visibly different answers
    and the fixture can tell them apart -- which a window with all four negative could not. The
    rule is the family's: a value's sample size is the count its definition declares, never the
    count of rows that happened to qualify.
    """
    value = _downside_vol_60(_window())

    assert value == pytest.approx(EXPECTED_DOWNSIDE_VOL)
    assert value != pytest.approx(EXPECTED_DOWNSIDE_VOL_OVER_ITS_NEGATIVES)
    assert sum(1 for item in PROBE_RETURNS if item < 0.0) == 2
    assert len(PROBE_RETURNS) == 4


def test_a_quarter_with_no_down_session_is_a_downside_deviation_of_zero_and_not_undefined() -> None:
    """Zero is an answer here and `undefined_value` would be a lie about it.

    `validate_factor_observation` refuses a `computed` observation with no value and a
    non-`computed` one with a value, so the two are not interchangeable downstream: a security with
    no down day in the quarter is scored, and one whose arithmetic had no answer is not.
    """
    rising = _window(closes=(110.0, 120.0, 130.0, 140.0), pre_closes=(100.0, 110.0, 120.0, 130.0))

    downside = _downside_vol_60(rising)
    total = _return_vol_60(rising)

    assert downside == 0.0
    assert total is not None and total > 0.0


def test_one_zero_turnover_session_makes_the_whole_amihud_undefined_rather_than_shrinking_it() -> (
    None
):
    """The alternative that was rejected, asserted so that choosing it later fails here.

    Skipping the sessions whose `amount` is zero would produce a mean over however many sessions
    happened to trade, which is a value whose sample size is a function of the data. Fail-closed
    instead: one such session and the observation is `undefined_value`, whose remedy a reader can
    tell from `input_missing`'s.
    """
    window = _window(amounts=(1000.0, 0.0, 500.0, 4000.0))

    assert _amihud_60(window) is None

    # And the three sessions that *do* have turnover are a mean this could have answered with --
    # a plausible number, distinct from the four-session answer, that nothing on the stored row
    # would have said was taken over three sessions rather than sixty.
    survivors = (0.10 / 1e6, 0.02 / 5e5, 0.08 / 4e6)
    assert math.fsum(survivors) / 3 == pytest.approx(5.333333333333333e-08)
    assert math.fsum(survivors) / 3 != pytest.approx(EXPECTED_AMIHUD)


def test_a_negative_amount_is_refused_on_the_same_branch_as_a_zero_one() -> None:
    """`amount` is money and cannot be negative, so this is a corrupt cell rather than a state.

    Guarded on the same `<= 0.0` branch as the zero because the alternative -- letting it through --
    produces a *negative* illiquidity, which is finite, stores as `computed`, and reverses this
    factor's declared direction for that security.
    """
    assert _amihud_60(_window(amounts=(1000.0, -2000.0, 500.0, 4000.0))) is None


def test_turnover_reads_its_own_dataset_and_a_zero_rate_is_a_real_answer() -> None:
    """A session with a bar and almost no trade has a turnover of zero; that is data.

    Also pins that this factor reads `daily_basic` rather than `daily`: it is the only member of
    the family that does, and a window carrying only `daily` columns is where an evaluator that
    reached for the wrong dataset would fail.
    """
    assert _turnover_60(_window(turnover=(0.0, 0.0, 0.0, 0.0))) == 0.0
    assert _turnover_60(_window(turnover=(1.0, 2.0, 3.0, 6.0))) == pytest.approx(3.0)

    with pytest.raises(FactorEngineError, match=r"did not declare daily_basic\.turnover_rate"):
        _turnover_60(_window(turnover=None))


def test_amihud_reaching_for_an_undeclared_amount_is_refused_by_the_window() -> None:
    """A `KeyError` would read as "the engine is broken"; what it means is that `required_fields`
    does not cover what the formula reads, which is the field the coverage check is built on."""
    with pytest.raises(FactorEngineError, match=r"did not declare daily\.amount"):
        _amihud_60(_window(amounts=None))
