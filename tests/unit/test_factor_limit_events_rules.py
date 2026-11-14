"""`V2-P7-028`'s limit-event family: its declarations, its engines and its count.

The engine is not under test here -- `tests/integration/panel/test_limit_event_factors.py` drives
it against a generated panel. What is under test is everything decided *before* or *around* a
store is involved: the two declarations, the one evaluator they share, the promise that the
evaluator's count is `session_limit_events`' count, and the two engine facts the family relies on
(`PUBLISHED_ABSENCE_DATASETS` forms the window, and a band-reading close is not a return path).

## What this file is built to catch

- **A second implementation of a touch.** The acceptance `V2-P7-028` inherits from `V2-P7-011` is
  "agree by construction", so the count is asserted against `session_limit_events` on one fixture
  rather than against numbers computed here. A change to `limit_touch` moves both or the test
  fails.
- **Two factors that count the same thing.** `limit_up_count_20` and `broken_board_count_20` read
  one window of one fixture; the closed-at-limit sessions and the touched-not-closed sessions
  partition the touched sessions, and the file asserts the partition rather than each count alone.
- **A direction declared without the disclosure every note owes.** The registry-wide check lives in
  `tests/unit/test_factor_engine_rules.py`; here the two new notes are read as prose, because the
  shared sentence is a constant this file also owns.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType

from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_DATA_COLUMNS,
    DAILY_DATASET,
    DailyBar,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorField
from openalpha_cn.domain.limit_events import (
    SessionLimitState,
    session_limit_events,
    session_state,
)
from openalpha_cn.domain.price_limits import (
    DOWN_LIMIT_COLUMN,
    PRICE_LIMIT_DATASET,
    UP_LIMIT_COLUMN,
    PriceLimit,
    limit_touch,
)
from openalpha_cn.panel_factors import (
    BROKEN_BOARD_COUNT_20,
    FACTOR_DEFINITIONS,
    FACTOR_EVALUATORS,
    HIGH_COLUMN,
    LIMIT_EVENT_LOOKBACK_SESSIONS,
    LIMIT_EVENT_MAX_WINDOW_SESSIONS,
    LIMIT_UP_COUNT_20,
    LOW_COLUMN,
    MAX_HALTED_SESSIONS_IN_FIVE,
    PUBLISHED_ABSENCE_DATASETS,
    FactorWindow,
    _broken_board_count_20,
    _DatasetReading,
    _limit_up_count_20,
    _points_held,
    session_return_links,
)

AS_OF = datetime(2026, 6, 30, 4, 0, tzinfo=UTC)

FAMILY: tuple[FactorDefinition, ...] = (LIMIT_UP_COUNT_20, BROKEN_BOARD_COUNT_20)
"""The family `V2-P7-028` ships, as the registry declares it."""

SCRIPT: tuple[tuple[float, float, float, float], ...] = (
    (10.00, 11.00, 11.00, 10.20),
    (11.00, 12.10, 12.10, 12.10),
    (12.10, None, None, None),
    (12.10, 13.31, 13.31, 12.50),
    (13.31, 13.90, 14.64, 13.50),
    (13.90, 15.29, 15.29, 14.20),
    (15.29, 15.50, 15.60, 15.40),
    (15.50, 17.05, 17.05, 16.00),
)
"""(pre_close, close, high, low) per scripted session; a halt when `close` is `None`.

The eight rows of `tests/unit/domain/test_limit_events.py`'s hand-labelled table, restated here
because this file counts the same events. d1 closes at its band; d2 is a one-price session; d3 is a
halt -- a band and no bar; d4 and d6 close at their band; d5 touches and closes below it; d7 is
ordinary; d8 closes at its band.
"""

SESSIONS = 21
"""One more than the factor's reach, because the fixture's halt is not a classifiable session.

The script is eight sessions long and the third is the halt, so twenty-one dates leave exactly
twenty classifiable bars -- the factor's declared reach -- which is what makes the unit window the
same shape the engine forms: `PUBLISHED_ABSENCE_DATASETS` drops the halt session and the window
reaches one session further back for it.
"""

ORDINARY_GROWTH = 0.001
"""How an unscripted session moves: up, and nowhere near a 10% band."""


def _dates() -> tuple[date, ...]:
    return tuple(date(2026, 1, 5 + index) for index in range(SESSIONS))


def _bars_and_limits() -> tuple[
    tuple[date, ...], dict[date, dict[str, DailyBar]], dict[date, dict[str, PriceLimit]]
]:
    """The hand-labelled fixture, session by session: bars, published bands, and the dates.

    A halt has a band and no bar, which is `stk_limit`'s own published shape for a security that
    did not trade. The scripted sessions are the first eight; the rest are ordinary and never
    touch, which is what makes the scripted events the whole of the expected count.
    """
    days = _dates()
    price = 20.0
    bars: dict[date, dict[str, DailyBar]] = {}
    limits: dict[date, dict[str, PriceLimit]] = {}
    for index, day in enumerate(days):
        if index < len(SCRIPT):
            pre_close, close, high, low = SCRIPT[index]
            up_limit = round(pre_close * 1.1, 2)
            down_limit = round(pre_close * 0.9, 2)
            limits[day] = {
                "000001.SZ": PriceLimit(
                    ts_code="000001.SZ", trade_date=day, up_limit=up_limit, down_limit=down_limit
                )
            }
            if close is None:
                bars[day] = {}
                continue
            assert high is not None and low is not None
            bars[day] = {
                "000001.SZ": DailyBar(
                    ts_code="000001.SZ",
                    trade_date=day,
                    open=close,
                    high=high,
                    low=low,
                    close=close,
                    pre_close=pre_close,
                    pct_chg=(close / pre_close - 1.0) * 100.0,
                    vol=1000.0,
                    amount=10000.0,
                )
            }
            continue
        pre_close = price
        price = round(price * (1.0 + ORDINARY_GROWTH), 2)
        bars[day] = {
            "000001.SZ": DailyBar(
                ts_code="000001.SZ",
                trade_date=day,
                open=price,
                high=price,
                low=price,
                close=price,
                pre_close=pre_close,
                pct_chg=(price / pre_close - 1.0) * 100.0,
                vol=1000.0,
                amount=10000.0,
            )
        }
        limits[day] = {
            "000001.SZ": PriceLimit(
                ts_code="000001.SZ",
                trade_date=day,
                up_limit=round(pre_close * 1.1, 2),
                down_limit=round(pre_close * 0.9, 2),
            )
        }
    return days, bars, limits


def _states(
    days: tuple[date, ...],
    bars: dict[date, dict[str, DailyBar]],
    limits: dict[date, dict[str, PriceLimit]],
) -> tuple[SessionLimitState, ...]:
    return tuple(session_state(bars[day], limits[day], trade_date=day) for day in days)


def _events(
    days: tuple[date, ...],
    bars: dict[date, dict[str, DailyBar]],
    limits: dict[date, dict[str, PriceLimit]],
) -> tuple[tuple[date, str], ...]:
    """`(day, kind)` for every event `session_limit_events` emits over the fixture, newest last."""
    states = _states(days, bars, limits)
    events: list[tuple[date, str]] = []
    for index, day in enumerate(days):
        earlier = tuple(reversed(states[:index]))
        for event in session_limit_events(states[index], bars[day], earlier):
            events.append((day, event.kind))
    return tuple(events)


def _window(
    days: tuple[date, ...],
    bars: dict[date, dict[str, DailyBar]],
    limits: dict[date, dict[str, PriceLimit]],
) -> FactorWindow:
    """A complete `FactorWindow` over the fixture, exactly as `_complete_series` would build it."""
    # The engine's own rule: a session with no bar is not a window point (see
    # `PUBLISHED_ABSENCE_DATASETS`). The halt day is the one such session here.
    sessions = tuple(day for day in days if "000001.SZ" in bars[day])

    def _series(dataset: str) -> tuple[float, ...]:
        values: list[float] = []
        for day in sessions:
            bar = bars[day]["000001.SZ"]
            limit = limits[day]["000001.SZ"]
            match dataset:
                case "high":
                    values.append(bar.high)
                case "low":
                    values.append(bar.low)
                case "close":
                    values.append(bar.close)
                case "up":
                    values.append(limit.up_limit)
                case "down":
                    values.append(limit.down_limit)
                case _:  # pragma: no cover - a typo in this helper
                    raise AssertionError(dataset)
        return tuple(values)

    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=sessions,
        periods=(),
        values=MappingProxyType(
            {
                (DAILY_DATASET, HIGH_COLUMN): _series("high"),
                (DAILY_DATASET, LOW_COLUMN): _series("low"),
                (DAILY_DATASET, CLOSE_COLUMN): _series("close"),
                (PRICE_LIMIT_DATASET, UP_LIMIT_COLUMN): _series("up"),
                (PRICE_LIMIT_DATASET, DOWN_LIMIT_COLUMN): _series("down"),
            }
        ),
    )


# --- the declarations ----------------------------------------------------------------------------


def test_the_family_is_exactly_two_definitions_and_they_are_this_issues_only_members() -> None:
    """Both directions: the two this issue owns are declared, and nothing else claims the family.

    Off the live registry, not off this file's own tuple, so a factor from elsewhere mis-labelled
    `limit_events` -- or a sixth family member shipped without a note -- fails here.
    """
    declared = tuple(
        definition.qualified_key
        for definition in FACTOR_DEFINITIONS.definitions
        if definition.family == "limit_events"
    )

    assert declared == (LIMIT_UP_COUNT_20.qualified_key, BROKEN_BOARD_COUNT_20.qualified_key)
    for handle in declared:
        note = FACTOR_DEFINITIONS.note_for(handle)
        assert note is not None and len(note) > 100


def test_each_definition_declares_its_reach_span_direction_and_inputs() -> None:
    """Every field the engine reads, on both definitions, including the absent period axis.

    `lower_is_better` is the plan's frozen declaration (R-B1) and is asserted against the value
    rather than the note, because the reading orients its one-sided test from this field.
    """
    for definition in FAMILY:
        assert definition.family == "limit_events"
        assert definition.direction == "lower_is_better"
        assert definition.lookback_sessions == LIMIT_EVENT_LOOKBACK_SESSIONS == 20
        assert definition.max_window_sessions == LIMIT_EVENT_MAX_WINDOW_SESSIONS
        assert definition.lookback_periods is None
        assert definition.max_window_periods is None
        assert definition.datasets == (DAILY_DATASET, PRICE_LIMIT_DATASET)
        assert definition.required_fields == (
            FactorField(dataset=DAILY_DATASET, column="high"),
            FactorField(dataset=DAILY_DATASET, column="low"),
            FactorField(dataset=DAILY_DATASET, column="close"),
            FactorField(dataset=PRICE_LIMIT_DATASET, column="up_limit"),
            FactorField(dataset=PRICE_LIMIT_DATASET, column="down_limit"),
        )
        assert definition.qualified_key in FACTOR_EVALUATORS


def test_the_span_is_the_momentum_familys_halt_tolerance_of_its_own_reach() -> None:
    """`20 + 20 // 5` = 24, derived from the constant that states the tolerance rather than fitted.

    The declaration is the price of a skipped session: a halt or a bandless session inside the
    window pushes the twenty counted sessions further back on the panel's axis, and the momentum
    family's one-in-five rule is the number this family inherits. Pinned as arithmetic, so moving
    the divisor or the lookback fails here before it changes a single value's coverage code.
    """
    assert MAX_HALTED_SESSIONS_IN_FIVE == 1
    assert LIMIT_EVENT_MAX_WINDOW_SESSIONS == (
        LIMIT_EVENT_LOOKBACK_SESSIONS
        + LIMIT_EVENT_LOOKBACK_SESSIONS * MAX_HALTED_SESSIONS_IN_FIVE // 5
    )
    assert LIMIT_EVENT_MAX_WINDOW_SESSIONS == 24


def test_the_two_factors_declare_distinct_identities_over_identical_inputs() -> None:
    """One window, two questions -- so the content addresses must separate them.

    They share every declared field, which is the shape `V2-P3-008`'s redundancy analysis will
    report as identical inputs. That is deliberate: it is the same statement
    `momentum_120_sessions`/`reversal_5_sessions` make about disjoint sessions, here about the two
    halves of `limit_touch`'s verdict.
    """
    assert LIMIT_UP_COUNT_20.required_fields == BROKEN_BOARD_COUNT_20.required_fields
    assert LIMIT_UP_COUNT_20.key != BROKEN_BOARD_COUNT_20.key
    assert LIMIT_UP_COUNT_20.factor_id != BROKEN_BOARD_COUNT_20.factor_id
    assert (LIMIT_UP_COUNT_20.direction, BROKEN_BOARD_COUNT_20.direction) == (
        "lower_is_better",
        "lower_is_better",
    )


def test_the_declared_columns_are_the_contracts_own_names() -> None:
    """`FactorField` validates a reference syntactically, so the binding is asserted here.

    `stk_limit`'s two names are imported from `domain/price_limits.py`; the bar's two extremes have
    no scalar there, so they are held to `DAILY_DATA_COLUMNS` instead of to a second copy.
    """
    assert (UP_LIMIT_COLUMN, DOWN_LIMIT_COLUMN) == ("up_limit", "down_limit")
    assert (HIGH_COLUMN, LOW_COLUMN) == ("high", "low")
    assert HIGH_COLUMN in DAILY_DATA_COLUMNS
    assert LOW_COLUMN in DAILY_DATA_COLUMNS
    assert CLOSE_COLUMN in DAILY_DATA_COLUMNS


def test_the_engine_declares_the_one_dataset_whose_absence_is_a_published_fact() -> None:
    """The *declaration* only; the window's behaviour is driven in the integration file.

    This pins which dataset carries the meaning, so a second one cannot acquire it silently. What
    the rule does to a halt, a bandless session and a whole-history halt is
    `tests/integration/panel/test_limit_event_factors.py`'s subject -- a declaration test cannot
    show that a window formed by the rule is the one the domain can classify.
    """
    assert frozenset({PRICE_LIMIT_DATASET}) == PUBLISHED_ABSENCE_DATASETS
    for definition in FAMILY:
        assert PRICE_LIMIT_DATASET in definition.datasets


def _reading(
    axis: str,
    points_by_subject: dict[str, tuple[date, ...]],
    *,
    columns: tuple[str, ...] = ("close",),
) -> _DatasetReading:
    """One `_DatasetReading` with only what `_points_held` reads populated."""
    return _DatasetReading(
        points_by_subject=MappingProxyType(points_by_subject),
        values=MappingProxyType({}),
        columns=columns,
        axis=axis,  # type: ignore[arg-type]
        ambiguous_points_by_subject=MappingProxyType({}),
    )


def test_with_no_published_absence_dataset_the_window_is_the_old_union() -> None:
    """`PUBLISHED_ABSENCE_DATASETS` is inert for every factor that reads none of its datasets.

    The window rule changed, so "no shipped answer moves" needs a direct test of the function
    rather than only the per-definition read-path pins: with no absence dataset in `readings` the
    answer is the previous code's answer exactly -- one reading's own tuple, or the sorted union
    of the readings that hold the subject -- on both axes, and a dataset outside the table never
    intersects. The period-axis pair is `V2-P3-010`'s two-statement union; the second session
    reading is `daily`+`daily_basic`, whose coverage is equal by the daily writer's cross-check.
    """
    subject = "000001.SZ"
    first, second, third, fourth = (date(2026, 1, day) for day in (5, 6, 7, 8))
    assert _points_held(
        subject, readings={"daily": _reading("session", {subject: (first, second)})}, axis="session"
    ) == (first, second)

    sessions = {
        "daily": _reading("session", {subject: (first, second, third)}),
        "daily_basic": _reading("session", {subject: (second, third, fourth)}),
    }
    # The union and not the intersection: the points only one reading holds are window points.
    assert _points_held(subject, readings=sessions, axis="session") == (
        first,
        second,
        third,
        fourth,
    )
    assert _points_held("000002.SZ", readings=sessions, axis="session") == ()

    filings = {
        "income": _reading("period", {subject: (first, second)}, columns=("revenue",)),
        "balancesheet": _reading("period", {subject: (second, third)}, columns=("total_assets",)),
    }
    assert _points_held(subject, readings=filings, axis="period") == (first, second, third)


# --- the count, against the domain's own events --------------------------------------------------


def test_the_counts_are_the_event_counts_session_limit_events_produces_on_one_fixture() -> None:
    """The acceptance that makes "by construction" checkable: one fixture, two readers.

    The expected numbers are `V2-P7-011`'s events, split the way the plan splits them:
    `limit_up_count_20` is every `limit_up` plus `consecutive_board` event -- both are sessions
    whose close is the band -- and `broken_board_count_20` is every `broken_board`. A second
    implementation of the comparison inside `panel_factors` would have to agree with
    `limit_touch` on every one of these sessions, and this is the fixture that would catch it.
    """
    days, bars, limits = _bars_and_limits()
    events = _events(days, bars, limits)
    kinds = [kind for _, kind in events]

    window = _window(days, bars, limits)

    assert _limit_up_count_20(window) == float(
        kinds.count("limit_up") + kinds.count("consecutive_board")
    )
    assert _broken_board_count_20(window) == float(kinds.count("broken_board"))
    # Non-vacuous: the scripted fixture carries both kinds.
    assert kinds.count("limit_up") + kinds.count("consecutive_board") == 5
    assert kinds.count("broken_board") == 1


def test_a_closed_at_limit_session_is_in_one_count_and_never_in_both() -> None:
    """The partition: closed-at-limit sessions are `limit_up_count_20`'s, the rest are breaks.

    Driven from the domain's own four booleans rather than from the fixture's script, so this is
    `limit_touch`'s verdict on each bar being split once. A session that closed *at* the band is
    not a break even though it touched it -- the single most likely copy-paste defect in the pair.
    """
    days, bars, limits = _bars_and_limits()
    window = _window(days, bars, limits)
    touches = [
        limit_touch(bars[day]["000001.SZ"], limits[day]["000001.SZ"])
        for day in days
        if "000001.SZ" in bars[day]
    ]

    touched = sum(1 for touch in touches if touch.at_up)
    closed = sum(1 for touch in touches if touch.closed_at_up)
    broken = sum(1 for touch in touches if touch.at_up and not touch.closed_at_up)

    assert _limit_up_count_20(window) == float(closed)
    assert _broken_board_count_20(window) == float(broken)
    assert closed + broken == touched
    assert (_limit_up_count_20(window) or 0) + (_broken_board_count_20(window) or 0) == float(
        touched
    )


def test_a_limit_free_sentinel_band_is_a_session_but_not_a_touch() -> None:
    """`limit_touch`'s sentinel handling, driven through the evaluator's own input shape.

    A session whose published band is an out-of-range sentinel is a real row: it is one of the
    twenty sessions (so it is in the window), and no bar reaches it (so it contributes no event).
    Both halves are asserted -- a factor that dropped the session would have a window of 19, and a
    factor that read the sentinel as a 10%-style band would invent a touch.
    """
    days, bars, limits = _bars_and_limits()
    sentinel_day = days[6]  # d7, the ordinary scripted session
    limits[sentinel_day]["000001.SZ"] = PriceLimit(
        ts_code="000001.SZ",
        trade_date=sentinel_day,
        up_limit=99999.999,
        down_limit=0.01,
    )
    window = _window(days, bars, limits)
    events = _events(days, bars, limits)

    assert _limit_up_count_20(window) == float(
        sum(1 for _, kind in events if kind in ("limit_up", "consecutive_board"))
    )
    assert _broken_board_count_20(window) == float(
        sum(1 for _, kind in events if kind == "broken_board")
    )
    touch = limit_touch(bars[sentinel_day]["000001.SZ"], limits[sentinel_day]["000001.SZ"])
    assert not touch.at_up and not touch.closed_at_up


# --- the two engine facts this family depends on -------------------------------------------------


def test_a_band_reading_close_is_not_a_session_return_path() -> None:
    """`session_return_links`' new branch: a close compared to a published band is not a return.

    `close_to_close` was written for `reversal_1d`'s two-close return. The limit-event factors read
    one close column over twenty sessions to compare against `stk_limit.up_limit`, so loading the
    `V2-P6-020` return-path decisions for them would abstain on sessions whose *return* is
    undecidable while their close still sat at the band -- and the factor would then disagree with
    `V2-P7-011`'s events on a panel that producer answers. Both shipped return shapes are asserted
    beside it, so the new branch cannot swallow them.
    """
    assert session_return_links(LIMIT_UP_COUNT_20) is None
    assert session_return_links(BROKEN_BOARD_COUNT_20) is None
    assert session_return_links(FACTOR_DEFINITIONS.get("reversal_1d/v1")) == "close_to_close"
    assert session_return_links(FACTOR_DEFINITIONS.get("momentum_20_sessions/v1")) == "own_row"

    # A factor that declares both a band and pre_close has declared the return it reads.
    both = FactorDefinition(
        key="probe_band_and_return",
        version=1,
        family="limit_events",
        direction="lower_is_better",
        required_fields=(
            FactorField(dataset=DAILY_DATASET, column=CLOSE_COLUMN),
            FactorField(dataset=DAILY_DATASET, column="pre_close"),
            FactorField(dataset=PRICE_LIMIT_DATASET, column=UP_LIMIT_COLUMN),
        ),
        lookback_sessions=2,
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
    )
    assert session_return_links(both) == "own_row"


def test_each_note_discloses_the_declared_direction_and_the_borrowed_rule() -> None:
    """The two notes say the direction is a prior with nothing measured, and say what they reuse.

    The registry-wide disclosure check is `tests/unit/test_factor_engine_rules.py`; this is the
    content the two new notes carry besides it -- the `limit_touch`/`session_limit_events`
    agreement and the skipped-session rule -- because those are the claims a reader needs in order
    to know what the number is.
    """
    for definition in FAMILY:
        note = FACTOR_DEFINITIONS.note_for(definition.qualified_key)
        assert note is not None
        assert "declared direction is" in note
        assert "measured nothing" in note
        assert "limit_touch" in note
        assert "session_limit_events" in note
        assert "skipped" in note
