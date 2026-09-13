"""The market series reaching an evaluator, off a real partition (`V2-P3-016`).

`V2-P3-013` measured two obstacles between this build and a residual volatility and neither was
arithmetic: no dataset carried an index level, and `FactorWindow` carried one security's rows so
an evaluator could not have reached a market series if one had been stored. The first is answered
by `domain/index_prices.py` and by a sixteenth Tushare descriptor; the second by
`panel_factors.SHARED_SUBJECT_DATASETS` and by the window's `shared` channel. Both halves are
unit-tested elsewhere. **What needs a store is that the two meet**, and the four claims below are
what a store is required for.

## What this module exists to make executable

**The market series is read at the security's own sessions and does not widen its window.** That
is the one asymmetry in the engine change: `_complete_series` and `_stored_rows` substitute the
shared subject and `_points_held` deliberately does not. If it did, a halted security's window
would contain sessions it never traded and every security in the cross section would come back
`input_missing`. `test_the_market_series_is_read_at_the_securitys_own_sessions_and_does_not_widen_
its_window` drives a security with a real interior halt and checks both halves -- the window it
gets and the market values that arrive with it.

**The residual is a different number from the total, with a floor rather than a hope.** The two
factors share a return series and differ only by `beta * r_m`, so they are the pair most at risk of
agreeing on a fixture -- `V2-P3-004`'s review found a column asserted on a fixture that could not
tell two answers apart, and this repository has repeated that shape ten times since.
`test_the_residual_and_the_total_are_different_numbers_and_the_gap_has_a_floor` asserts the
relationship exactly (`residual^2 * (N-2) = total^2 * (N-1) * (1 - R^2)`) *and* refuses a fixture
on which the gap is small.

**A market that did not move is `undefined_value`, not the total.** `var(r_m) == 0` makes the slope
`0/0`. Reading it as "nothing to explain, so the residual is the total" is a different factor's
answer reached by an undefined division, and `test_a_motionless_market_is_undefined_rather_than_the
_total_volatility` drives a second store where the index is flat.

**A missing market session is `input_missing` for the securities that needed it.** Not for the
build, and not for the whole cross section: a partition truncated at its oldest rows -- which is
what this endpoint's cap does -- has to code the securities whose windows reach into the hole and
leave the rest computed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_DATA_COLUMNS,
    DAILY_DATASET,
    PRE_CLOSE_COLUMN,
)
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.index_prices import (
    INDEX_DAILY_DATA_COLUMNS,
    INDEX_DAILY_DATASET,
    INDEX_PRICE_INDEX_CODES,
    MARKET_INDEX_CODE,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    RESIDUAL_VOL_60,
    RETURN_VOL_60,
    VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS,
    FactorPanel,
    FactorWindow,
    compute_factor,
)
from openalpha_cn.panel_ingest import (
    index_price_requirement,
    write_index_prices,
    write_panel_batch,
)

YEAR: Final[int] = 2026
PANEL_SESSION_COUNT: Final[int] = 90
COMMIT: Final[str] = "c4a2e10"
BUILT_AT: Final[datetime] = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
STALENESS: Final[timedelta] = timedelta(days=7)
SHANGHAI_OFFSET: Final[timedelta] = timedelta(hours=8)

FULL: Final[str] = "000001.SZ"
HALTED: Final[str] = "000002.SZ"
FLAT: Final[str] = "000003.SZ"
SUBJECTS: Final[tuple[str, ...]] = (FULL, HALTED, FLAT)
UNIVERSE: Final[frozenset[str]] = frozenset(SUBJECTS)

HALT_RUN: Final[tuple[int, ...]] = (40, 41, 42, 43, 44)
"""Five interior panel sessions `HALTED` has no bar on.

Interior rather than at either end, and five rather than one, because what is being separated is
"the window is the security's" from "the window is the panel's": with a run of five, the security's
sixtieth-most-recent session is five panel sessions further back than `FULL`'s, so the two windows
have different *first* dates and a market series read on the wrong grid is off by five rows.
"""

BASE_CLOSE: Final[float] = 12.0
BASE_LEVEL: Final[float] = 4000.0

MARKET_CYCLE: Final[tuple[float, ...]] = (0.011, -0.007, 0.004, -0.013, 0.009, -0.002, 0.006)
"""The market's own session returns, seven of them so the cycle does not divide 60 evenly."""

BETA: Final[tuple[float, ...]] = (1.4, 0.9, 1.1)
"""Each security's true loading on the market, three different values.

A fixture where every security had one beta could not show that the residual tracks the loading:
the gap between the residual and the total is a function of `beta^2 * var(r_m)`, so one beta would
make three securities' gaps agree by construction and an evaluator that used a *constant* slope
would pass.
"""

IDIOSYNCRATIC: Final[tuple[float, ...]] = (0.005, -0.011, 0.003, 0.008, -0.006)
"""The part no market return explains, five long so it is out of phase with `MARKET_CYCLE`."""

IDIOSYNCRATIC_SCALE: Final[tuple[float, ...]] = (1.0, 0.7, 1.3)
"""Per security, so the three residual volatilities are three different numbers.

Scaled *and* phase-shifted (see `_security_return`), because scaling alone would make the three
residuals proportional and a phase shift alone would leave them equal. Both together is what makes
`test_the_residual_is_the_hand_computed_closed_form_for_every_security` an assertion three
different answers have to satisfy rather than one answer asserted three times.
"""


def _sessions() -> tuple[date, ...]:
    days: list[date] = []
    day = date(YEAR, 1, 2)
    while len(days) < PANEL_SESSION_COUNT:
        if day.weekday() < 5:
            days.append(day)
        day = day.fromordinal(day.toordinal() + 1)
    return tuple(days)


SESSIONS: Final[tuple[date, ...]] = _sessions()
AS_OF: Final[datetime] = datetime.combine(SESSIONS[-1], time(23, 0), tzinfo=UTC)


def _instant(day: date) -> datetime:
    return datetime.combine(day, time(16, 30), tzinfo=UTC) - SHANGHAI_OFFSET


def _traded_indices(subject: str) -> tuple[int, ...]:
    if subject == HALTED:
        return tuple(index for index in range(PANEL_SESSION_COUNT) if index not in HALT_RUN)
    return tuple(range(PANEL_SESSION_COUNT))


def _market_return(index: int) -> float:
    return MARKET_CYCLE[index % len(MARKET_CYCLE)]


def _security_return(subject: str, index: int) -> float:
    """`beta * r_m + e`, so the residual is a fact about the fixture rather than a fitted number.

    Not the same thing as "the regression will recover `beta`": a 60-session ordinary least
    squares over a *periodic* market and a periodic residual recovers something close to it and
    not it, and nothing here asserts otherwise. What the construction buys is that the residual is
    non-zero by design and that its size is controlled per security, which is what
    `test_the_residual_and_the_total_are_different_numbers_and_the_gap_has_a_floor` needs a floor
    on.
    """
    position = SUBJECTS.index(subject)
    beta = BETA[position]
    noise = IDIOSYNCRATIC[(index + position) % len(IDIOSYNCRATIC)]
    return beta * _market_return(index) + IDIOSYNCRATIC_SCALE[position] * noise


def _security_path(subject: str) -> dict[int, tuple[float, float]]:
    path: dict[int, tuple[float, float]] = {}
    close = BASE_CLOSE
    for index in _traded_indices(subject):
        previous = close
        close = previous * (1.0 + _security_return(subject, index))
        path[index] = (close, previous)
    return path


def _market_path(*, moves: bool = True) -> dict[int, tuple[float, float]]:
    path: dict[int, tuple[float, float]] = {}
    level = BASE_LEVEL
    for index in range(PANEL_SESSION_COUNT):
        previous = level
        level = previous * (1.0 + (_market_return(index) if moves else 0.0))
        path[index] = (level, previous)
    return path


def _batch(
    dataset: str,
    kind: str,
    subjects: Sequence[str],
    instants: tuple[datetime, ...],
    columns: tuple[PanelColumn, ...],
) -> ColumnarPanelBatch:
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=dataset,
        kind=kind,
        as_of=BUILT_AT,
        fetched_at=BUILT_AT,
        status="success",
        subjects=tuple(subjects),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=tuple(max(BUILT_AT, moment) for moment in instants),
            revision_time=instants,
        ),
        columns=columns,
    )


def _daily_batch() -> ColumnarPanelBatch:
    subjects: list[str] = []
    days: list[date] = []
    closes: list[float] = []
    pre_closes: list[float] = []
    for subject in SUBJECTS:
        for index, (close, previous) in sorted(_security_path(subject).items()):
            subjects.append(subject)
            days.append(SESSIONS[index])
            closes.append(close)
            pre_closes.append(previous)

    columns = (
        PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        PanelColumn("open", "float", tuple(closes)),
        PanelColumn("high", "float", tuple(closes)),
        PanelColumn("low", "float", tuple(closes)),
        PanelColumn("close", "float", tuple(closes)),
        PanelColumn("pre_close", "float", tuple(pre_closes)),
        PanelColumn(
            "pct_chg",
            "float",
            tuple(
                (close / previous - 1.0) * 100.0
                for close, previous in zip(closes, pre_closes, strict=True)
            ),
        ),
        PanelColumn("vol", "float", tuple(1000.0 for _ in days)),
        PanelColumn("amount", "float", tuple(5000.0 for _ in days)),
    )
    assert tuple(column.name for column in columns) == DAILY_DATA_COLUMNS
    return _batch(
        DAILY_DATASET, "daily_bar", subjects, tuple(_instant(day) for day in days), columns
    )


def _index_batch(
    *, moves: bool = True, skip: Sequence[int] = (), codes: Sequence[str] = INDEX_PRICE_INDEX_CODES
) -> ColumnarPanelBatch:
    """Every index's levels over the whole nine-column projection.

    All three of `INDEX_PRICE_INDEX_CODES` rather than only the market one, because the partition a
    real build writes holds three series told apart by the subject column alone -- so a fixture
    with one subject could not show that the engine reads `MARKET_INDEX_CODE`'s rows rather than
    whichever rows the partition happens to start with. The two that are not the market carry a
    deliberately *different* path, so reading the wrong one is a different number rather than the
    same one.
    """
    path = _market_path(moves=moves)
    subjects: list[str] = []
    days: list[date] = []
    levels: list[float] = []
    pre_levels: list[float] = []
    for code in codes:
        scale = 1.0 if code == MARKET_INDEX_CODE else -0.5
        running = BASE_LEVEL
        for index in range(PANEL_SESSION_COUNT):
            if index in skip:
                continue
            previous = running
            step = (path[index][0] / path[index][1] - 1.0) * scale
            running = previous * (1.0 + step)
            subjects.append(code)
            days.append(SESSIONS[index])
            levels.append(running)
            pre_levels.append(previous)

    columns = (
        PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        PanelColumn("open", "float", tuple(levels)),
        PanelColumn("high", "float", tuple(levels)),
        PanelColumn("low", "float", tuple(levels)),
        PanelColumn("close", "float", tuple(levels)),
        PanelColumn("pre_close", "float", tuple(pre_levels)),
        PanelColumn(
            "pct_chg",
            "float",
            tuple(
                (level / previous - 1.0) * 100.0
                for level, previous in zip(levels, pre_levels, strict=True)
            ),
        ),
        PanelColumn("vol", "float", tuple(300000.0 for _ in days)),
        PanelColumn("amount", "float", tuple(900000.0 for _ in days)),
    )
    assert tuple(column.name for column in columns) == INDEX_DAILY_DATA_COLUMNS
    return _batch(
        INDEX_DAILY_DATASET, "index_daily", subjects, tuple(_instant(day) for day in days), columns
    )


def _store(tmp_path: Path, index_batch: ColumnarPanelBatch) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    write_panel_batch(built, _daily_batch(), year=YEAR)
    write_index_prices(built, [index_batch])
    return built


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    return _store(tmp_path, _index_batch())


def _calendar() -> TradingCalendar:
    """A real calendar over `YEAR` whose open sessions are exactly `SESSIONS`."""
    first, last = date(YEAR, 1, 1), SESSIONS[-1]
    opens = set(SESSIONS)
    return build_trading_calendar(
        "SSE",
        [
            CalendarDay(
                calendar_date=first + timedelta(days=offset),
                is_trading=(first + timedelta(days=offset)) in opens,
            )
            for offset in range((last - first).days + 1)
        ],
    )


def _requirement(dataset: str) -> ReadinessRequirement:
    """The readiness question, built by the **production** builder for the new dataset.

    `daily`'s is hand-built here (`test_volatility_liquidity_family.py`'s shape: this module's
    session grid is weekdays rather than a published calendar, and that dataset's own census is
    not what is under test). `index_daily`'s is not, and the difference is deliberate:
    `index_price_requirement` is the only place that says `required_subjects` is
    `MARKET_INDEX_CODE`, and a hand-built requirement here would restate that claim beside the
    code instead of exercising it -- so a builder that dropped the subject would leave this
    module green while the shipped read stopped being able to tell "this year has no market
    series" from "this year has an empty one". Measured: it does. Mutating that argument to
    `None` leaves every other test in this file passing and turns
    `test_the_engine_reads_the_market_index_and_not_whichever_series_the_partition_holds` red.
    """
    if dataset == INDEX_DAILY_DATASET:
        return index_price_requirement(
            _calendar(), years=(YEAR,), as_of=AS_OF, max_staleness=STALENESS
        )
    return ReadinessRequirement(
        dataset=dataset,
        as_of=AS_OF,
        years=(YEAR,),
        required_dates=None,
        required_subjects=None,
        required_fields=DAILY_DATA_COLUMNS,
        max_staleness=STALENESS,
    )


def _compute(store: PanelStore, definition: FactorDefinition, **overrides: Any) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "subjects": SUBJECTS,
        "universe": UNIVERSE,
        "requirements": {name: _requirement(name) for name in definition.datasets},
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        **overrides,
    }
    return compute_factor(store, definition, **settings)


def _coverage(panel: FactorPanel) -> dict[str, str]:
    return {item.subject: item.coverage for item in panel.observations}


def _observation(panel: FactorPanel, subject: str) -> Any:
    return next(item for item in panel.observations if item.subject == subject)


# --- the derived expectations, computed from the fixture rather than copied ----------------------


def _window_indices(subject: str) -> tuple[int, ...]:
    return _traded_indices(subject)[-VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS:]


def _expected_residual_vol(subject: str) -> float:
    """The closed form, restated from the fixture rather than pasted from a run."""
    indices = _window_indices(subject)
    returns = tuple(_security_return(subject, index) for index in indices)
    market = tuple(_market_return(index) for index in indices)
    count = len(returns)
    mean_r = math.fsum(returns) / count
    mean_m = math.fsum(market) / count
    variance = math.fsum((value - mean_m) ** 2 for value in market)
    covariance = math.fsum(
        (value - mean_r) * (other - mean_m) for value, other in zip(returns, market, strict=True)
    )
    beta = covariance / variance
    intercept = mean_r - beta * mean_m
    residuals = tuple(
        value - intercept - beta * other for value, other in zip(returns, market, strict=True)
    )
    return math.sqrt(math.fsum(item * item for item in residuals) / (count - 2))


def _expected_return_vol(subject: str) -> float:
    returns = tuple(_security_return(subject, index) for index in _window_indices(subject))
    mean = math.fsum(returns) / len(returns)
    return math.sqrt(math.fsum((item - mean) ** 2 for item in returns) / (len(returns) - 1))


# --- the four claims ------------------------------------------------------------------------------


def test_the_market_series_is_read_at_the_securitys_own_sessions_and_does_not_widen_its_window(
    store: PanelStore,
) -> None:
    """The engine change's one asymmetry, driven on a security with a five-session interior halt.

    Three of the four per-security helpers substitute the shared subject and `_points_held` must
    not, because the window is formed from the security's own points. `HALTED` has no bar on five
    interior panel sessions, so:

    - its window reaches **five panel sessions further back** than `FULL`'s while holding the same
      sixty of its own -- if `_points_held` had substituted the market, both windows would start on
      the same date and `HALTED`'s would contain sessions it never traded;
    - it is still `computed`, because `_complete_series` finds a market row on every session
      `HALTED` did trade;
    - `input_row_count` counts **both** datasets over that window -- 120, not 60 -- which is the
      half `_stored_rows` owns, and it is asserted because a `_reading_subject` applied in one of
      the two and not the other would leave the provenance field wrong exactly where a reader
      consults it.
    """
    panel = _compute(store, RESIDUAL_VOL_60)

    assert _coverage(panel) == {subject: "computed" for subject in SUBJECTS}

    full = _observation(panel, FULL)
    halted = _observation(panel, HALTED)

    assert full.input_session_last == halted.input_session_last == SESSIONS[-1]
    assert SESSIONS.index(halted.input_session_first) == SESSIONS.index(
        full.input_session_first
    ) - len(HALT_RUN)
    assert (
        SESSIONS.index(full.input_session_first)
        == PANEL_SESSION_COUNT - VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS
    )

    assert (
        full.input_row_count == halted.input_row_count == 2 * VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS
    )


def test_the_window_handed_to_the_evaluator_keeps_the_two_channels_apart(
    store: PanelStore,
) -> None:
    """The partition itself, caught in flight, because `subject` is only honest if it holds.

    `FactorWindow.subject` names one security and `values` is that security's own rows. The
    cheap way to make a market series reachable would have been to key it into `values` beside
    them -- the dataclass's shape would not have changed and `subject` would have become false
    about part of its own contents. The field set is asserted by equality one file over, which
    catches the shape; this catches the *contents*, which no shape assertion can see: measured,
    a `_classify` that put both channels into `values` and left `shared` populated as well
    leaves every other test in this file and in `test_factor_engine_rules.py` green.

    Driven by injecting an evaluator that captures the window rather than by reading a stored
    row, because the split happens between `_complete_series` and the call and nothing
    downstream records it.
    """
    seen: list[FactorWindow] = []

    def _capture(window: FactorWindow) -> float | None:
        seen.append(window)
        return 0.0

    _compute(store, RESIDUAL_VOL_60, evaluators={RESIDUAL_VOL_60.qualified_key: _capture})

    assert len(seen) == len(SUBJECTS)
    for window in seen:
        assert set(window.values) == {
            (DAILY_DATASET, CLOSE_COLUMN),
            (DAILY_DATASET, PRE_CLOSE_COLUMN),
        }
        assert set(window.shared) == {
            (INDEX_DAILY_DATASET, CLOSE_COLUMN),
            (INDEX_DAILY_DATASET, PRE_CLOSE_COLUMN),
        }
        assert not set(window.values) & set(window.shared)
        assert all(
            len(series) == VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS
            for series in (*window.values.values(), *window.shared.values())
        )

    # The market series really is the same one for every security, restricted to that
    # security's own sessions -- so the three windows agree wherever their sessions do.
    full = next(window for window in seen if window.subject == FULL)
    halted = next(window for window in seen if window.subject == HALTED)
    shared_key = (INDEX_DAILY_DATASET, CLOSE_COLUMN)
    overlap = {
        day: value for day, value in zip(full.sessions, full.shared[shared_key], strict=True)
    }
    for day, value in zip(halted.sessions, halted.shared[shared_key], strict=True):
        if day in overlap:
            assert overlap[day] == value


def test_the_residual_is_the_hand_computed_closed_form_for_every_security(
    store: PanelStore,
) -> None:
    """Every value, against the arithmetic restated from the fixture.

    Three securities with three betas rather than one, so a fixture on which the evaluator ignored
    the market entirely would fail on at least two of them: the expected values differ by a factor
    of more than two across the three, and a residual computed without subtracting `beta * r_m`
    would be `_expected_return_vol` instead.
    """
    panel = _compute(store, RESIDUAL_VOL_60)
    values = panel.values()

    for subject in SUBJECTS:
        assert values[subject] == pytest.approx(_expected_residual_vol(subject), rel=1e-12), subject


def test_the_residual_and_the_total_are_different_numbers_and_the_gap_has_a_floor(
    store: PanelStore,
) -> None:
    """The pair most at risk of agreeing, separated exactly and then separated by a floor.

    They share a return series and differ only by `beta * r_m`, so a fixture on which the market
    explained nothing would give one number twice -- the `V2-P3-004` review's finding, which this
    repository has now met more than ten times. Two assertions rather than one:

    - **Exactly.** `residual^2 * (N-2) = total^2 * (N-1) * (1 - R^2)` is an identity of ordinary
      least squares, so it holds to floating point on any fixture and would fail for an evaluator
      that divided by `N-1`, or that forgot the intercept, or that regressed the wrong way round.
    - **With a floor.** The identity is satisfied at `R^2 = 0` with the two equal, so it alone
      cannot say the fixture discriminates. The floor refuses that fixture: the market must explain
      at least two fifths of every security's variance here (measured 0.70 / 0.69 / 0.42), which
      makes the residual at most 80% of the total on all three (measured 0.55 / 0.56 / 0.77).
    """
    residual = _compute(store, RESIDUAL_VOL_60).values()
    total = _compute(store, RETURN_VOL_60).values()

    count = VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS
    for subject in SUBJECTS:
        assert total[subject] == pytest.approx(_expected_return_vol(subject), rel=1e-12)
        explained = 1.0 - (residual[subject] ** 2 * (count - 2)) / (
            total[subject] ** 2 * (count - 1)
        )
        assert explained > 0.4, f"{subject} has a market share of variance of {explained}"
        assert residual[subject] < total[subject] * 0.8, subject

    assert len({round(value, 12) for value in residual.values()}) == len(SUBJECTS)


def test_a_motionless_market_is_undefined_rather_than_the_total_volatility(
    tmp_path: Path,
) -> None:
    """`var(r_m) == 0`, and the tempting wrong answer named beside the right one.

    The slope is `0/0` there. "Nothing to explain, so the residual is the total" is a plausible
    reading and it is a *different factor's* answer arrived at by an undefined division, so the
    observation is `undefined_value` and carries no value at all. Asserted against
    `RETURN_VOL_60`'s value on the same store, which is finite and positive -- so this is "the
    evaluator declined" and not "the window was empty".
    """
    flat = _store(tmp_path, _index_batch(moves=False))

    residual = _compute(flat, RESIDUAL_VOL_60)
    total = _compute(flat, RETURN_VOL_60)

    assert _coverage(residual) == {subject: "undefined_value" for subject in SUBJECTS}
    assert residual.values() == {}
    assert all(value > 0.0 for value in total.values().values())
    assert set(total.values()) == set(SUBJECTS)


def test_a_truncated_market_year_is_refused_at_the_read_and_not_answered_per_security(
    tmp_path: Path,
) -> None:
    """What this endpoint's 8,000-row cap would produce, and what stops it being quiet.

    The cap drops the **oldest** rows, so a truncated year is a contiguous suffix -- exactly the
    shape a gap rule cannot see. What sees it is the census: `index_price_requirement` states
    `required_dates` from the stored calendar, `daily_requirement`'s shape, so a year missing ten
    of its sessions is blocked at **every** read with `date_gap` rather than answered.

    That is a whole-build refusal and not a per-security coverage code, and the difference is
    worth pinning rather than assumed. The first draft of this test asserted the other thing --
    that the securities whose windows reached the hole would come back `input_missing` and the
    rest `computed` -- which is what the engine would do if the requirement waived its dates. It
    does not, so the refusal happens one layer earlier and applies to everybody. A caller reading
    `input_missing` would conclude the factor is not computable for that name yet; a caller
    reading `date_gap` is told the partition is short, which is the true statement and the one
    with a remedy.

    **This is also the honest form of the truncation-flag argument.** With the census in place a
    truncated partition is not silently wrong -- it is unreadable. What the flag buys is that the
    fetch that caused it fails, rather than `panel build` reporting success over a partition that
    every later factor build refuses. `panel_ingest.write_suspensions` states the same principle
    for the same reason: a store that accepts what it cannot return is worse than one that
    refuses at either end.
    """
    truncated = _store(tmp_path, _index_batch(skip=tuple(range(10))))

    with pytest.raises(Exception, match="date_gap") as blocked:
        _compute(truncated, RESIDUAL_VOL_60)
    assert INDEX_DAILY_DATASET in str(blocked.value)
    assert "10 required date(s) are absent" in str(blocked.value)

    # The same store answers the factor that does not read the market at all, so the refusal is
    # about the dataset that is short and not about the panel.
    assert _coverage(_compute(truncated, RETURN_VOL_60)) == {
        subject: "computed" for subject in SUBJECTS
    }


def test_the_engine_reads_the_market_index_and_not_whichever_series_the_partition_holds(
    tmp_path: Path,
) -> None:
    """The subject selection, separated by a partition whose other two series are different.

    `INDEX_PRICE_INDEX_CODES` are all stored and only `MARKET_INDEX_CODE` is reachable. The other
    two carry the market's returns scaled by `-0.5`, so an engine that read the first subject in
    the partition, or pooled them, would produce a different number rather than the same one --
    which is what makes this a measurement instead of an assertion that cannot fail.

    The second half is the partition with *only* the two unreachable series: the requirement names
    `MARKET_INDEX_CODE` in `required_subjects`, so the read is blocked by name rather than
    answering an empty market series.
    """
    every = _store(tmp_path, _index_batch())
    only_market = _store(tmp_path / "one", _index_batch(codes=(MARKET_INDEX_CODE,)))

    assert _compute(every, RESIDUAL_VOL_60).values() == pytest.approx(
        _compute(only_market, RESIDUAL_VOL_60).values()
    )

    others = tuple(code for code in INDEX_PRICE_INDEX_CODES if code != MARKET_INDEX_CODE)
    without = _store(tmp_path / "without", _index_batch(codes=others))
    with pytest.raises(Exception, match="subject_missing"):
        _compute(without, RESIDUAL_VOL_60)
