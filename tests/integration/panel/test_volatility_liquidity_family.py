"""The volatility and liquidity family against real partitions on disk (`V2-P3-013`).

`tests/unit/test_factor_volatility_liquidity.py` measures the four evaluators as functions of a
window. This file measures the three things a function cannot answer: that `daily.amount` and
`daily_basic.turnover_rate` are columns a written partition actually binds, that a 60-session reach
forms the window the contract says it does, and that each of the two declared bounds is
**separable from the other on its own boundary** rather than merely rendered.

## The fixture, and why it is 81 sessions rather than 60

`VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS` is 80, so the smallest panel on which the span bound can
be shown to *refuse* anything is 81 sessions wide. The panel is therefore 81 weekday sessions of
one year, and the securities on it are chosen so that the two bounds each have a witness on both
sides:

| security | own sessions | window spans | what it decides |
|---|---|---|---|
| `000001.SZ` | all 81, one ex-rights morning | 60 | the computable case, and the return path |
| `000002.SZ` | 60, first at panel index 2 | 79 | inside the span bound |
| `600000.SH` | 60, first at panel index 1 | 80 | **on** the span bound, still computed |
| `600519.SH` | 60, first at panel index 0 | 81 | one past it: `insufficient_history` |
| `000063.SZ` | exactly 60, contiguous | 60 | on the count, computed |
| `002415.SZ` | exactly 59, contiguous | -- | one short: `insufficient_history` |
| `300750.SZ` | all 81, one null `amount` | 60 | `input_missing`, and only for `amihud_60` |
| `002594.SZ` | all 81, one zero `amount` | 60 | `undefined_value`, and only for `amihud_60` |
| `601318.SH` | all 81, outside the universe | -- | `not_in_universe` |

The last three are what make this file say something the engine's own
`tests/integration/panel/test_factor_engine.py` cannot. That file records that `undefined_value` is
unreachable through a real partition for `reversal_1d`, because no writer here stores a
non-positive close, and splits the code's two halves across two files as a result. `amihud_60`'s
denominator is `daily.amount`, which is not one of `DAILY_PRICE_COLUMNS`' five guarded columns, so
a zero in it is storable -- and `undefined_value` is provoked here through the whole engine, on
disk, for the first time.

## Both bounds are separable, which is the `V2-P3-004` review's lesson rather than a formality

That review's finding was not an unasserted field; it was a field asserted on a fixture where the
assertion could not tell two settings apart. So neither of this family's two numbers is asserted by
being read back off the definition:

- `test_the_span_bound_is_separable_from_the_count_at_its_own_boundary` runs one build in which
  three securities hold sixty of their own sessions each and differ only in how many panel sessions
  those sixty reach across -- 79, 80 and 81 -- and gets `computed`, `computed`,
  `insufficient_history`.
- `test_the_count_is_separable_from_the_span_at_its_own_boundary` does the same for 60 against 59
  own sessions with no gap in either, so the *span* is identical and only the count moves.

## The return path is separable here too, and that needs the ex-rights session

`000001.SZ` carries one morning on which `pre_close` is not the previous session's `close`. Without
it every close-to-close implementation would agree with the correct one on this fixture and the
assertion would be measuring nothing;
`test_the_stored_value_is_the_pre_close_path_and_not_the_close_to_close_one` computes both off the
same stored rows and asserts the engine returned the first.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATA_COLUMNS,
    DAILY_BASIC_DATASET,
    DAILY_DATA_COLUMNS,
    DAILY_DATASET,
)
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    AMIHUD_60,
    AMOUNT_COLUMN,
    CNY_PER_AMOUNT_UNIT,
    DOWNSIDE_VOL_60,
    RETURN_VOL_60,
    TURNOVER_60,
    TURNOVER_RATE_COLUMN,
    VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS,
    VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS,
    FactorPanel,
    compute_factor,
    factor_observation_dataset,
    load_factor_observations,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import write_panel_batch

YEAR: Final[int] = 2026
SHANGHAI_OFFSET: Final[timedelta] = timedelta(hours=8)
PANEL_SESSION_COUNT: Final[int] = 81
"""One more than `VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS`, which is the smallest panel on which
that bound can be shown to refuse anything at all."""

RETURN_CYCLE: Final[tuple[float, ...]] = (0.10, -0.05, 0.02, -0.08)
"""The session-return pattern every security's price path follows, by panel session index.

Four values, two of them negative, repeating -- and 60 is fifteen whole cycles of it, so a
60-session window's statistics are hand-computable from one cycle. Two negatives out of four is
what makes `downside_vol_60`'s divisor separable: over 60 sessions the window length is 60 and the
count of negative returns is 30.
"""

AMOUNT_CYCLE: Final[tuple[float, ...]] = (1000.0, 2000.0, 500.0, 4000.0)
"""Thousands of yuan, on the same four-session cycle. No two alike, so a mean that dropped or
duplicated a session cannot land on the same number."""

TURNOVER_CYCLE: Final[tuple[float, ...]] = (1.0, 2.0, 3.0, 6.0)
"""Percent, same cycle, stored in `daily_basic.turnover_rate`. Their mean is exactly 3.0."""

FREE_FLOAT_MULTIPLE: Final[float] = 2.5
"""What `turnover_rate_f` carries in this fixture, relative to `turnover_rate`.

A *different* series rather than the same one, so the two columns are separable on this partition:
a `TURNOVER_60` that read the free-float column would answer 7.5 where 3.0 is asserted. The
multiple is in the range the two real rows this repository stores actually exhibit -- 2.4905
against 1.0473 for `000001.SZ` and 0.9334 against 0.4039 for `600519.SH`, both about 2.3x.
"""

BASE_CLOSE: Final[float] = 100.0

EX_RIGHTS_INDEX: Final[int] = 40
EX_RIGHTS_RESTATEMENT: Final[float] = 0.9
"""One session inside every window, on which `pre_close` is 0.9 times the previous close.

A tenth of the price restated overnight, which is an ordinary A-share ex-rights morning and is what
`domain/daily_prices.py`'s own worked example is about. Without it `close[t] / close[t-1] - 1`
equals `close[t] / pre_close[t] - 1` on every session of this fixture, and the assertion that the
engine took the second one would be measuring nothing.
"""

FULL: Final[str] = "000001.SZ"
SPAN_79: Final[str] = "000002.SZ"
SPAN_80: Final[str] = "600000.SH"
SPAN_81: Final[str] = "600519.SH"
COUNT_60: Final[str] = "000063.SZ"
COUNT_59: Final[str] = "002415.SZ"
NULL_AMOUNT: Final[str] = "300750.SZ"
ZERO_AMOUNT: Final[str] = "002594.SZ"
OUTSIDE: Final[str] = "601318.SH"

SUBJECTS: Final[tuple[str, ...]] = (
    FULL,
    SPAN_79,
    SPAN_80,
    SPAN_81,
    COUNT_60,
    COUNT_59,
    NULL_AMOUNT,
    ZERO_AMOUNT,
    OUTSIDE,
)

UNIVERSE: Final[frozenset[str]] = frozenset(SUBJECTS) - {OUTSIDE}

COMMIT: Final[str] = "b7c1d9e"
BUILT_AT: Final[datetime] = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
STALENESS: Final[timedelta] = timedelta(days=7)
"""One calendar week. Wider than the fixture's own weekend gaps and narrower than the window it
bounds, so it is a real bound rather than one sized to clear."""


def _sessions() -> tuple[date, ...]:
    """`PANEL_SESSION_COUNT` consecutive weekdays from 2026-01-02, one partition year.

    Weekdays rather than a real trading calendar because nothing the engine does here consults one:
    `_panel_axis_points` builds the session axis out of the rows the read returned, which is
    exactly the property that makes the span bound countable in panel sessions.
    """
    days: list[date] = []
    day = date(YEAR, 1, 2)
    while len(days) < PANEL_SESSION_COUNT:
        if day.weekday() < 5:
            days.append(day)
        day = day.fromordinal(day.toordinal() + 1)
    return tuple(days)


SESSIONS: Final[tuple[date, ...]] = _sessions()
AS_OF: Final[datetime] = datetime.combine(SESSIONS[-1], time(23, 0), tzinfo=UTC)


def _traded_indices(subject: str) -> tuple[int, ...]:
    """Which panel session indices this security has a row on.

    The gapped securities skip a run of *interior* sessions so that the first and last index of
    their range are both traded -- which is what fixes the span their last sixty sessions reach
    across, independently of how many they hold.
    """
    last = PANEL_SESSION_COUNT - 1
    if subject in (SPAN_79, SPAN_80, SPAN_81):
        span = {SPAN_79: 79, SPAN_80: 80, SPAN_81: 81}[subject]
        first = PANEL_SESSION_COUNT - span
        skipped = span - VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS
        return (first, *range(first + 1 + skipped, last + 1))
    if subject == COUNT_60:
        return tuple(range(last + 1 - VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS, last + 1))
    if subject == COUNT_59:
        return tuple(range(last + 2 - VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS, last + 1))
    return tuple(range(PANEL_SESSION_COUNT))


def _price_path(subject: str) -> dict[int, tuple[float, float]]:
    """`{panel session index: (close, pre_close)}` for one security.

    Built forward from `BASE_CLOSE` so that `pre_close` is the previous session's close on every
    session but one, and `close` is `pre_close * (1 + r)` for `r` off `RETURN_CYCLE`. The exception
    is `EX_RIGHTS_INDEX` on `FULL`, where `pre_close` is the previous close restated -- so the
    return is still the cycle's and the close-to-close quotient is not.
    """
    path: dict[int, tuple[float, float]] = {}
    close = BASE_CLOSE
    for index in _traded_indices(subject):
        previous = close
        if subject == FULL and index == EX_RIGHTS_INDEX:
            previous = close * EX_RIGHTS_RESTATEMENT
        close = previous * (1.0 + RETURN_CYCLE[index % len(RETURN_CYCLE)])
        path[index] = (close, previous)
    return path


def _amount(subject: str, index: int) -> float | None:
    if subject == NULL_AMOUNT and index == PANEL_SESSION_COUNT - 10:
        return None
    if subject == ZERO_AMOUNT and index == PANEL_SESSION_COUNT - 10:
        return 0.0
    return AMOUNT_CYCLE[index % len(AMOUNT_CYCLE)]


def _instant(day: date) -> datetime:
    """16:30 Asia/Shanghai on `day`, as UTC -- `DAILY_AVAILABILITY_TIME`'s shape."""
    return datetime.combine(day, time(16, 30), tzinfo=UTC) - SHANGHAI_OFFSET


def _daily_batch() -> ColumnarPanelBatch:
    """Every security's bars, projected onto the whole of `DAILY_DATA_COLUMNS`.

    The full nine-column shape rather than the three this family reads, so that the engine's own
    column projection is exercised: a factor reading `close`, `pre_close` and `amount` out of a
    partition that also holds `open`, `high`, `low`, `pct_chg` and `vol` has to pick the right
    three, and a partition holding only three could not show that it did.
    """
    subjects: list[str] = []
    days: list[date] = []
    closes: list[float] = []
    pre_closes: list[float] = []
    amounts: list[float | None] = []
    for subject in SUBJECTS:
        for index, (close, previous) in sorted(_price_path(subject).items()):
            subjects.append(subject)
            days.append(SESSIONS[index])
            closes.append(close)
            pre_closes.append(previous)
            amounts.append(_amount(subject, index))

    instants = tuple(_instant(day) for day in days)
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
        PanelColumn(AMOUNT_COLUMN, "float", tuple(amounts)),
    )
    assert tuple(column.name for column in columns) == DAILY_DATA_COLUMNS
    return _batch(DAILY_DATASET, "daily_bar", subjects, instants, columns)


def _daily_basic_batch() -> ColumnarPanelBatch:
    """Every security's valuations, on the same sessions, over the whole of
    `DAILY_BASIC_DATA_COLUMNS`.

    Two columns move and both are named by **literal** rather than through a constant this module
    imports: `turnover_rate` carries the cycle and `turnover_rate_f` carries `FREE_FLOAT_MULTIPLE`
    times it, so the two candidate columns hold different series and a reader pointed at the wrong
    one answers a different number. The remaining fifteen are a constant, because `turnover_60`
    reads exactly one column and a second moving one would be noise."""
    subjects: list[str] = []
    days: list[date] = []
    rates: list[float] = []
    closes: list[float] = []
    for subject in SUBJECTS:
        for index, (close, _) in sorted(_price_path(subject).items()):
            subjects.append(subject)
            days.append(SESSIONS[index])
            closes.append(close)
            rates.append(TURNOVER_CYCLE[index % len(TURNOVER_CYCLE)])

    instants = tuple(_instant(day) for day in days)
    constant = tuple(1.0 for _ in days)
    # The two turnover columns are keyed by **literal** name rather than by
    # `TURNOVER_RATE_COLUMN`, and each carries a different series. A fixture that filled whichever
    # column that constant named would follow the constant, so swapping it to `turnover_rate_f`
    # would move the fixture and the reader together and every value assertion would still hold --
    # which is the self-referential shape that makes a test agree with whatever the code does.
    moved = {
        "trade_date": PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        "close": PanelColumn("close", "float", tuple(closes)),
        "turnover_rate": PanelColumn("turnover_rate", "float", tuple(rates)),
        "turnover_rate_f": PanelColumn(
            "turnover_rate_f", "float", tuple(rate * FREE_FLOAT_MULTIPLE for rate in rates)
        ),
    }
    columns = tuple(
        moved.get(name, PanelColumn(name, "float", constant)) for name in DAILY_BASIC_DATA_COLUMNS
    )
    return _batch(DAILY_BASIC_DATASET, "daily_basic", subjects, instants, columns)


def _batch(
    dataset: str,
    kind: str,
    subjects: list[str],
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


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    write_panel_batch(built, _daily_batch(), year=YEAR)
    write_panel_batch(built, _daily_basic_batch(), year=YEAR)
    return built


def _requirement(dataset: str) -> ReadinessRequirement:
    fields = DAILY_DATA_COLUMNS if dataset == DAILY_DATASET else DAILY_BASIC_DATA_COLUMNS
    return ReadinessRequirement(
        dataset=dataset,
        as_of=AS_OF,
        years=(YEAR,),
        required_dates=None,
        required_subjects=None,
        required_fields=fields,
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


def _value(panel: FactorPanel, subject: str) -> float:
    values = panel.values()
    assert subject in values, f"{subject} is {_coverage(panel)[subject]}"
    return values[subject]


# --- the derived expectations, computed from the fixture rather than copied -----------------------


def _window_returns(subject: str) -> tuple[float, ...]:
    """The 60 returns the engine's window should hand this security's evaluator.

    Derived from `_price_path` -- the same arithmetic the batch was built from -- so an expected
    value here is a restatement of the fixture rather than a number pasted from a run.
    """
    path = _price_path(subject)
    tail = sorted(path)[-VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS:]
    return tuple(path[index][0] / path[index][1] - 1.0 for index in tail)


def _window_closes(subject: str) -> tuple[float, ...]:
    path = _price_path(subject)
    tail = sorted(path)[-VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS:]
    return tuple(path[index][0] for index in tail)


def _expected_return_vol(subject: str) -> float:
    returns = _window_returns(subject)
    mean = math.fsum(returns) / len(returns)
    return math.sqrt(math.fsum((item - mean) ** 2 for item in returns) / (len(returns) - 1))


def _expected_downside_vol(subject: str) -> float:
    returns = _window_returns(subject)
    return math.sqrt(math.fsum(item * item for item in returns if item < 0.0) / len(returns))


def _expected_amihud(subject: str) -> float:
    returns = _window_returns(subject)
    path = _price_path(subject)
    tail = sorted(path)[-VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS:]
    amounts = tuple(AMOUNT_CYCLE[index % len(AMOUNT_CYCLE)] for index in tail)
    return math.fsum(
        abs(item) / (amount * CNY_PER_AMOUNT_UNIT)
        for item, amount in zip(returns, amounts, strict=True)
    ) / len(returns)


# --- the four values, off a partition ------------------------------------------------------------


def test_the_four_factors_compute_off_one_partition_and_give_four_different_numbers(
    store: PanelStore,
) -> None:
    """The whole family through the real engine, each value pinned as a number.

    Every one is derived from `_price_path`, the same arithmetic the partition was written from, so
    these are restatements of the fixture rather than numbers copied out of a run -- and the four
    are asserted to be pairwise distinct, because four factors of one family sharing a fixture is
    exactly where the `V2-P3-004` review's finding recurs.

    That they compute at all is the part only a partition can say: `FactorField` validates a column
    reference *syntactically* and says so, so `daily.amount` and `daily_basic.turnover_rate` being
    real bindable columns of a written partition is not something a definition can assert.
    """
    answers = {
        definition.key: _value(_compute(store, definition), FULL)
        for definition in (RETURN_VOL_60, DOWNSIDE_VOL_60, TURNOVER_60, AMIHUD_60)
    }

    assert answers["return_vol_60"] == pytest.approx(_expected_return_vol(FULL))
    assert answers["downside_vol_60"] == pytest.approx(_expected_downside_vol(FULL))
    assert answers["turnover_60"] == pytest.approx(3.0)
    assert answers["amihud_60"] == pytest.approx(_expected_amihud(FULL))

    assert len({round(value, 12) for value in answers.values()}) == 4

    # The turnover answer is separable from the column beside it: this partition carries a
    # different series in `turnover_rate_f`, so a reader that took the free-float column would have
    # answered 7.5. The constant is held against the literal the fixture wrote, because a fixture
    # keyed on the constant would move with it and assert nothing.
    assert TURNOVER_RATE_COLUMN == "turnover_rate"
    assert answers["turnover_60"] != pytest.approx(3.0 * FREE_FLOAT_MULTIPLE)


def test_the_stored_value_is_the_pre_close_path_and_not_the_close_to_close_one(
    store: PanelStore,
) -> None:
    """The one defect that would have reached three of the four factors at once.

    `FULL` carries one ex-rights morning inside its window, so the two paths disagree on exactly one
    session of sixty and the fixture can tell them apart. Both are computed here off the same stored
    closes and the engine's answer is asserted to be the first; without the ex-rights session the
    two would agree on every session and this assertion would be vacuous, which is asserted too.
    """
    correct = _expected_return_vol(FULL)
    closes = _window_closes(FULL)
    naive_returns = tuple(
        closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))
    )
    naive_mean = math.fsum(naive_returns) / len(naive_returns)
    naive = math.sqrt(
        math.fsum((item - naive_mean) ** 2 for item in naive_returns) / (len(naive_returns) - 1)
    )

    assert _value(_compute(store, RETURN_VOL_60), FULL) == pytest.approx(correct)
    assert naive != pytest.approx(correct)

    # The fixture's own witness: on a security with no ex-rights morning the two paths agree, so
    # the separation above is the restatement and not the arithmetic.
    plain = _window_closes(COUNT_60)
    plain_returns = tuple(plain[index] / plain[index - 1] - 1.0 for index in range(1, len(plain)))
    assert plain_returns[0] == pytest.approx(_window_returns(COUNT_60)[1])


# --- the two bounds, each separable on its own boundary -------------------------------------------


def test_the_span_bound_is_separable_from_the_count_at_its_own_boundary(store: PanelStore) -> None:
    """79, 80 and 81 panel sessions over sixty of a security's own: computed, computed, refused.

    Three securities in one build, identical in everything the *count* can see -- each holds exactly
    `VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS` of its own sessions -- and differing only in how many
    panel sessions those sixty reach across. So the answer moves on `max_window_sessions` alone, and
    the boundary is driven from both sides rather than from one.

    The refused security still records the window it was refused for, which is what the two window
    columns buy: a span overrun is distinguishable on the stored row from a count shortfall without
    a sixth coverage code.
    """
    panel = _compute(store, RETURN_VOL_60)
    coverage = _coverage(panel)

    assert coverage[SPAN_79] == "computed"
    assert coverage[SPAN_80] == "computed"
    assert coverage[SPAN_81] == "insufficient_history"

    for subject in (SPAN_79, SPAN_80, SPAN_81):
        assert len(_traded_indices(subject)) == VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS

    (refused,) = (item for item in panel.observations if item.subject == SPAN_81)
    assert refused.input_session_first == SESSIONS[0]
    assert refused.input_session_last == SESSIONS[-1]
    assert VOLATILITY_LIQUIDITY_MAX_WINDOW_SESSIONS == 80


def test_the_count_is_separable_from_the_span_at_its_own_boundary(store: PanelStore) -> None:
    """60 against 59 contiguous sessions: computed against refused, with no gap in either.

    The other direction of the same lesson. Both securities trade every panel session in their own
    range, so neither can overrun a span bound at all and the only thing that moves is the count. A
    count shortfall records **no** window, because there was none to form -- which is what
    distinguishes it on the stored row from the span overrun above.
    """
    panel = _compute(store, RETURN_VOL_60)
    coverage = _coverage(panel)

    assert len(_traded_indices(COUNT_60)) == 60
    assert len(_traded_indices(COUNT_59)) == 59
    assert coverage[COUNT_60] == "computed"
    assert coverage[COUNT_59] == "insufficient_history"

    (short,) = (item for item in panel.observations if item.subject == COUNT_59)
    assert short.input_session_first is None
    assert short.input_session_last is None
    assert VOLATILITY_LIQUIDITY_LOOKBACK_SESSIONS == 60


# --- the two Amihud denominators, through the engine ----------------------------------------------


def test_a_zero_turnover_session_is_undefined_value_through_the_whole_engine(
    store: PanelStore,
) -> None:
    """`undefined_value` provoked on disk, which `reversal_1d` cannot do.

    `tests/integration/panel/test_factor_engine.py` records that this code is unreachable through a
    real partition for that factor, because `DAILY_PRICE_COLUMNS`' five guarded columns admit no
    zero and `daily_bars_from_panel_rows` refuses one -- so its two halves are split across two
    files. `daily.amount` is not one of those five, so a zero in it is storable, and the code is
    reachable end to end here: stored partition, real read, real window, real evaluator.

    And it is the *code* that separates the two faults, not the value: the same security is
    `computed` for the three factors that do not read `amount`, so a reader can tell "this
    security's data has a hole" from "this factor has no answer for this security".
    """
    assert _coverage(_compute(store, AMIHUD_60))[ZERO_AMOUNT] == "undefined_value"

    for definition in (RETURN_VOL_60, DOWNSIDE_VOL_60, TURNOVER_60):
        assert _coverage(_compute(store, definition))[ZERO_AMOUNT] == "computed"


def test_a_null_amount_is_input_missing_and_only_for_the_factor_that_reads_it(
    store: PanelStore,
) -> None:
    """The other half of the pair, and the reason the two codes are not one.

    A null cell is a fetch away from being fixed; a zero denominator is not, and
    `FactorCoverage` keeps them apart for exactly that reason. Both are provoked on the same
    partition, one session apart in construction, so the difference between them is the cell's value
    and nothing else.
    """
    assert _coverage(_compute(store, AMIHUD_60))[NULL_AMOUNT] == "input_missing"

    for definition in (RETURN_VOL_60, DOWNSIDE_VOL_60, TURNOVER_60):
        assert _coverage(_compute(store, definition))[NULL_AMOUNT] == "computed"


def test_a_security_outside_the_declared_universe_is_scored_by_none_of_the_four(
    store: PanelStore,
) -> None:
    """`not_in_universe` for all four, so the family cannot disagree about the cross section.

    `OUTSIDE` has a complete 81-session history, so every other code is available to it and only the
    universe argument keeps it out -- which is the shape that makes this an assertion about the
    engine's precedence rather than about the fixture's data.
    """
    for definition in (RETURN_VOL_60, DOWNSIDE_VOL_60, TURNOVER_60, AMIHUD_60):
        panel = _compute(store, definition)
        assert _coverage(panel)[OUTSIDE] == "not_in_universe"
        assert OUTSIDE not in panel.values()
        assert len(_traded_indices(OUTSIDE)) == PANEL_SESSION_COUNT


# --- the round trip -------------------------------------------------------------------------------


def test_all_four_factors_write_and_read_back_from_their_own_partitions(
    store: PanelStore,
) -> None:
    """Four factors, eight derived datasets, one store -- and the values survive.

    The partition axis this plane offers is the factor, so a family of four writes four observation
    partitions and four manifest ones into the same year without any of them colliding. Asserted by
    reading each one back through `load_factor_observations` and comparing to the in-memory panel,
    because a column that was never written or that decodes into the wrong field is only visible
    across the round trip.
    """
    panels = [
        _compute(store, definition)
        for definition in (RETURN_VOL_60, DOWNSIDE_VOL_60, TURNOVER_60, AMIHUD_60)
    ]
    written = write_factor_panels(store, panels)

    names = {reference.dataset for reference in written}
    assert len(names) == 8
    assert factor_observation_dataset(AMIHUD_60) in names

    for panel in panels:
        stored = load_factor_observations(store, panel.definition, years=(YEAR,), as_of=AS_OF)
        assert {item.subject: item.coverage for item in stored} == _coverage(panel)
        assert {
            item.subject: item.value for item in stored if item.value is not None
        } == pytest.approx(dict(panel.values()))
