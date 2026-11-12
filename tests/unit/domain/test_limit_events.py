"""The limit-event rule (`V2-P7-011`) -- who closed at its limit, and for how many sessions.

The hand-labelled fixture below is the specification. One security walks eight sessions through
every shape the rule has to tell apart, and each expected answer is written down beside the bar
it comes from rather than derived from the implementation:

    session   bar                                  expected event
    d1        closed at the 10% band               limit_up, board 1
    d2        the whole session traded at the band consecutive_board, board 2, one_price
    d3        no bar, band published (halt)        none -- and d2's streak survives
    d4        closed at the band                   consecutive_board, board 3, one halt skipped
    d5        touched the band, closed below it    broken_board, prior board 3
    d6        closed at the band                   limit_up, board 1 (d5 reset it)
    d7        an ordinary session                  none
    d8        closed at the band                   limit_up, board 1

Two decisions are pinned here rather than argued in prose. **A halt is skipped, not counted**
(D2): the exchange published the band for a security that did not trade, so the session neither
extends a streak nor breaks it, and the event carries `halted_sessions_skipped`. **A limit-free
session is a listed security with no ceiling**, so it cannot produce an event and it does break
a streak -- unlike a security `stk_limit` does not carry at all, whose history the walk treats
as ended (D2's "no band = not listed").

The last test checks the module's layering with `grimp` rather than the contract's static
`forbidden_modules` list, following `tests/unit/test_import_layering.py`: a numeric stack
imported transitively through a sibling module would slip past a hand-maintained list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import grimp
import pytest

from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.json_value import JsonValue
from openalpha_cn.domain.limit_events import (
    MAX_BOARD_LOOKBACK_SESSIONS,
    BoardStreak,
    LimitEvent,
    LimitEventKind,
    SessionLimitState,
    board_streak,
    session_limit_events,
    session_state,
)
from openalpha_cn.domain.price_limits import LimitTouch, PriceLimit

A: Final[str] = "000001.SZ"
B: Final[str] = "600000.SH"
Z: Final[str] = "600519.SH"


def _bar(
    ts_code: str,
    day: date,
    *,
    high: float,
    low: float,
    close: float,
    pre_close: float,
) -> DailyBar:
    return DailyBar(
        ts_code=ts_code,
        trade_date=day,
        open=close,
        high=high,
        low=low,
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1.0,
        amount=1.0,
    )


def _band(ts_code: str, day: date, *, pre_close: float, ratio: float = 0.10) -> PriceLimit:
    return PriceLimit(
        ts_code=ts_code,
        trade_date=day,
        up_limit=round(pre_close * (1.0 + ratio), 2),
        down_limit=round(pre_close * (1.0 - ratio), 2),
    )


def _limit_up_bar(ts_code: str, day: date, *, pre_close: float) -> DailyBar:
    up = round(pre_close * 1.1, 2)
    return _bar(
        ts_code, day, high=up, low=round(pre_close * 1.02, 2), close=up, pre_close=pre_close
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Session:
    """One hand-labelled session of security A: its bar, or a halt, and its published band."""

    day: date
    pre_close: float
    close: float | None = None
    high: float | None = None
    low: float | None = None

    @property
    def halted(self) -> bool:
        return self.close is None

    def bars(self) -> dict[str, DailyBar]:
        if self.close is None or self.high is None or self.low is None:
            return {}
        return {
            A: _bar(
                A,
                self.day,
                high=self.high,
                low=self.low,
                close=self.close,
                pre_close=self.pre_close,
            )
        }


# The eight sessions of the specification table above. `d3` is the halt: the exchange published
# a band (so `stk_limit` carries A that session) but `daily` has no bar.
_SESSIONS: Final[tuple[_Session, ...]] = (
    _Session(day=date(2024, 6, 3), pre_close=10.00, close=11.00, high=11.00, low=10.20),
    _Session(day=date(2024, 6, 4), pre_close=11.00, close=12.10, high=12.10, low=12.10),
    _Session(day=date(2024, 6, 5), pre_close=12.10),
    _Session(day=date(2024, 6, 6), pre_close=12.10, close=13.31, high=13.31, low=12.50),
    _Session(day=date(2024, 6, 7), pre_close=13.31, close=13.90, high=14.64, low=13.50),
    _Session(day=date(2024, 6, 11), pre_close=13.90, close=15.29, high=15.29, low=14.20),
    _Session(day=date(2024, 6, 12), pre_close=15.29, close=15.50, high=15.60, low=15.40),
    _Session(day=date(2024, 6, 13), pre_close=15.50, close=17.05, high=17.05, low=16.00),
)


def _scheduled() -> tuple[tuple[SessionLimitState, dict[str, DailyBar]], ...]:
    """Each session's state, plus the bars it was assembled from, in trade-date order."""
    return tuple(
        (
            session_state(
                session.bars(),
                {A: _band(A, session.day, pre_close=session.pre_close)},
                trade_date=session.day,
            ),
            session.bars(),
        )
        for session in _SESSIONS
    )


def _events_by_session() -> tuple[tuple[LimitEvent, ...], ...]:
    scheduled = _scheduled()
    return tuple(
        session_limit_events(
            state,
            bars,
            tuple(reversed([earlier[0] for earlier in scheduled[:index]])),
        )
        for index, (state, bars) in enumerate(scheduled)
    )


def _only(events: tuple[LimitEvent, ...]) -> LimitEvent:
    assert len(events) == 1, events
    return events[0]


def test_the_hand_labelled_fixture_reproduces_kind_and_board_count_day_by_day() -> None:
    """The eight-session table at the top of this module, asserted row by row."""
    expected: tuple[tuple[LimitEventKind, int], ...] = (
        ("limit_up", 1),
        ("consecutive_board", 2),
        ("limit_up", 0),  # unused: the halt session has no event at all
        ("consecutive_board", 3),
        ("broken_board", 3),
        ("limit_up", 1),
        ("limit_up", 0),  # unused
        ("limit_up", 1),
    )
    produced = _events_by_session()
    assert len(produced) == len(_SESSIONS)
    for index, events in enumerate(produced):
        expected_kind, expected_count = expected[index]
        if not events:
            assert _SESSIONS[index].halted or index == 6, index
            continue
        event = _only(events)
        assert (event.kind, event.board_count) == (expected_kind, expected_count), index

    assert _only(produced[1]).one_price is True
    assert _only(produced[3]).halted_sessions_skipped == 1
    assert _only(produced[3]).board_count_is_lower_bound is False
    broken = _only(produced[4])
    assert broken.kind == "broken_board"
    assert broken.board_count == 3
    assert broken.facts()["prior_board_count"] == 3


def test_a_halt_is_skipped_not_counted_and_is_recorded() -> None:
    """D2: the halt session neither extends nor breaks the streak, and says it was skipped."""
    events = _events_by_session()
    assert events[2] == ()
    fourth = _only(events[3])
    assert fourth.kind == "consecutive_board"
    assert fourth.board_count == 3
    assert fourth.halted_sessions_skipped == 1
    # The halt is a *real* band row (the exchange publishes the band for a security that did
    # not trade); calling it "not listed" would make the walk stop one session too early.
    halt_state = _scheduled()[2][0]
    assert A in halt_state.banded
    assert A not in halt_state.touches


def test_a_security_with_no_band_ends_the_streak_walk() -> None:
    """A listing that `stk_limit` does not carry has no history to walk, so the walk stops."""
    day_one = date(2024, 6, 3)
    day_two = date(2024, 6, 4)
    day_three = date(2024, 6, 5)

    first = session_state(
        {A: _bar(A, day_one, high=11.0, low=10.2, close=11.0, pre_close=10.0)},
        {A: _band(A, day_one, pre_close=10.0)},
        trade_date=day_one,
    )
    # Day two: the security traded but has no published band at all.
    second = session_state(
        {A: _bar(A, day_two, high=11.5, low=11.1, close=11.4, pre_close=11.0)},
        {},
        trade_date=day_two,
    )
    third_bars = {A: _limit_up_bar(A, day_three, pre_close=11.4)}
    third_state = session_state(
        third_bars,
        {A: _band(A, day_three, pre_close=11.4)},
        trade_date=day_three,
    )

    assert board_streak(A, (second, first), max_lookback=MAX_BOARD_LOOKBACK_SESSIONS) == (
        BoardStreak(count=0, halted_sessions_skipped=0, truncated=False)
    )
    third = _only(session_limit_events(third_state, third_bars, (second, first)))
    assert (third.kind, third.board_count) == ("limit_up", 1)


def test_a_limit_free_session_produces_no_event_and_breaks_a_streak() -> None:
    """A sentinel band is a listed security with no ceiling: no event, and the streak ends.

    `PriceLimit.is_bounded` is what separates the two: `up_limit = 99999.999` is 115.61x the
    previous close at its narrowest, no bar reaches it, so the touch is all `False`.
    """
    day_one = date(2024, 6, 3)
    day_two = date(2024, 6, 4)
    day_three = date(2024, 6, 5)

    first = session_state(
        {A: _bar(A, day_one, high=11.0, low=10.2, close=11.0, pre_close=10.0)},
        {A: _band(A, day_one, pre_close=10.0)},
        trade_date=day_one,
    )
    sentinel = PriceLimit(ts_code=A, trade_date=day_two, up_limit=99999.999, down_limit=0.01)
    second_bars = {A: _bar(A, day_two, high=14.4, low=14.0, close=14.4, pre_close=10.0)}
    second = session_state(second_bars, {A: sentinel}, trade_date=day_two)
    third_bars = {A: _limit_up_bar(A, day_three, pre_close=14.4)}
    third_state = session_state(
        third_bars,
        {A: _band(A, day_three, pre_close=14.4)},
        trade_date=day_three,
    )

    assert A in second.banded
    assert second.touches[A] == LimitTouch(
        ts_code=A,
        trade_date=day_two,
        at_up=False,
        at_down=False,
        closed_at_up=False,
        closed_at_down=False,
        one_price_up=False,
        one_price_down=False,
    )
    assert _only(_events_by_session()[0]).kind == "limit_up"
    assert session_limit_events(second, second_bars, (first,)) == ()
    third = _only(session_limit_events(third_state, third_bars, (second, first)))
    assert (third.kind, third.board_count) == ("limit_up", 1)


def test_a_st_five_percent_band_is_judged_by_its_published_limit() -> None:
    """The published band decides, not a nominal 10% ratio: an ST name closes `limit_up` at +5%.

    The same +5% close on a 10%-band name touches nothing -- judging by a constant ratio would
    invent the first event and erase the second.
    """
    day = date(2024, 6, 3)
    bars = {
        B: _bar(B, day, high=10.5, low=10.2, close=10.5, pre_close=10.0),
        Z: _bar(Z, day, high=10.5, low=9.8, close=10.5, pre_close=10.0),
    }
    limits = {
        B: _band(B, day, pre_close=10.0, ratio=0.05),
        Z: _band(Z, day, pre_close=10.0),
    }
    events = session_limit_events(session_state(bars, limits, trade_date=day), bars, ())
    assert [(event.ts_code, event.kind, event.board_count) for event in events] == [
        (B, "limit_up", 1)
    ]


def test_the_lookback_cap_marks_the_count_a_lower_bound() -> None:
    """Examining `max_lookback` sessions and finding each `closed_at_up` cannot see further."""
    days = [date(2024, 6, index) for index in (3, 4, 5, 6)]
    states: list[SessionLimitState] = []
    bars_by_day: list[dict[str, DailyBar]] = []
    pre_close = 10.0
    for day in days:
        bars = {A: _limit_up_bar(A, day, pre_close=pre_close)}
        states.append(session_state(bars, {A: _band(A, day, pre_close=pre_close)}, trade_date=day))
        bars_by_day.append(bars)
        pre_close = round(pre_close * 1.1, 2)

    earlier = tuple(reversed(states[:-1]))
    capped = _only(session_limit_events(states[-1], bars_by_day[-1], earlier, max_lookback=2))
    assert (capped.kind, capped.board_count) == ("consecutive_board", 3)
    assert capped.board_count_is_lower_bound is True

    uncapped = _only(session_limit_events(states[-1], bars_by_day[-1], earlier))
    assert (uncapped.kind, uncapped.board_count) == ("consecutive_board", 4)
    assert uncapped.board_count_is_lower_bound is False

    assert board_streak(A, earlier, max_lookback=2) == BoardStreak(
        count=2, halted_sessions_skipped=0, truncated=True
    )


def test_events_are_ordered_by_ts_code_and_only_securities_with_an_event_appear() -> None:
    """The day's cross section is ~5.5k rows; only the handful with an event comes back."""
    day = date(2024, 6, 3)
    bars = {
        A: _bar(A, day, high=11.0, low=10.2, close=11.0, pre_close=10.0),
        B: _bar(B, day, high=10.5, low=9.8, close=10.2, pre_close=10.0),
        Z: _bar(Z, day, high=22.0, low=20.5, close=22.0, pre_close=20.0),
    }
    limits = {
        A: _band(A, day, pre_close=10.0),
        B: _band(B, day, pre_close=10.0),
        Z: _band(Z, day, pre_close=20.0),
    }
    events = session_limit_events(session_state(bars, limits, trade_date=day), bars, ())
    assert [event.ts_code for event in events] == [A, Z]
    assert all(event.kind == "limit_up" for event in events)
    assert all(event.trade_date == day for event in events)


def test_every_event_facts_validate_against_the_builder_normalizers() -> None:
    """The evidence plane's seven-key vocabulary is the contract `facts()` must satisfy.

    Imported here, in the test only: `domain/` cannot import `evidence/`, and the normalizers
    carrying `ge=1` on `open_count`/`board_count` are what turn an off-by-one into a refusal at
    the evidence boundary instead of a plausible payload.
    """
    from openalpha_cn.evidence.builder import _NORMALIZERS

    events = [event for session in _events_by_session() for event in session]
    assert {event.kind for event in events} == {"limit_up", "consecutive_board", "broken_board"}

    facts: JsonValue
    for event in events:
        family, facts_model = _NORMALIZERS[event.kind]
        assert family == "market_event"
        facts = event.facts()
        facts_model.model_validate(facts)
        assert facts["close"] == event.close
        assert facts["pct_change"] == pytest.approx(event.pct_chg)

    broken = next(event for event in events if event.kind == "broken_board")
    broken_facts = broken.facts()
    assert broken_facts["open_count"] == 1
    assert broken_facts["open_count_is_lower_bound"] is True


def test_limit_events_imports_nothing_numeric() -> None:
    """Hard rule 2 read off the live graph, independent of the static contract list."""
    graph = grimp.build_graph("openalpha_cn", cache_dir=None, include_external_packages=True)
    upstream = graph.find_upstream_modules("openalpha_cn.domain.limit_events", as_package=False)
    forbidden = {"numpy", "pandas", "scipy", "sklearn", "duckdb", "sqlite3"}
    offenders = {
        module
        for module in upstream
        if module.split(".")[0] in forbidden
        or (module.startswith("openalpha_cn.") and not module.startswith("openalpha_cn.domain"))
    }
    assert not offenders, f"domain.limit_events reaches {sorted(offenders)}"


# --- fix round 1, finding 6: a halt that bridged no streak is not "skipped" --------------------


def test_a_halt_that_bridged_no_streak_is_not_counted() -> None:
    """`halted_sessions_skipped` names halts a streak was carried across, nothing else.

    History newest-first below the target: a halt and then an ordinary session. The streak
    starts at today, so the halt bridged nothing and must not be counted (the old walk added
    every halt it stepped over, wherever it sat).
    """
    halt_day = date(2024, 6, 21)
    flat_day = date(2024, 6, 20)
    up_day = date(2024, 6, 19)
    target_day = date(2024, 6, 24)
    halt = session_state({}, {A: _band(A, halt_day, pre_close=10.0)}, trade_date=halt_day)
    flat_bar = _bar(A, flat_day, high=10.2, low=9.9, close=10.0, pre_close=10.0)
    flat = session_state(
        {A: flat_bar}, {A: _band(A, flat_day, pre_close=10.0)}, trade_date=flat_day
    )
    up_bar = _limit_up_bar(A, up_day, pre_close=10.0)
    up = session_state({A: up_bar}, {A: _band(A, up_day, pre_close=10.0)}, trade_date=up_day)
    today_bar = _limit_up_bar(A, target_day, pre_close=up_bar.close)
    today = session_state(
        {A: today_bar}, {A: _band(A, target_day, pre_close=up_bar.close)}, trade_date=target_day
    )

    after_a_break = _only(session_limit_events(today, {A: today_bar}, (halt, flat)))
    assert (after_a_break.kind, after_a_break.board_count) == ("limit_up", 1)
    assert after_a_break.halted_sessions_skipped == 0

    # The same halt immediately below a limit-up *is* bridged by the streak and is counted.
    across_a_halt = _only(session_limit_events(today, {A: today_bar}, (halt, up)))
    assert (across_a_halt.kind, across_a_halt.board_count) == ("consecutive_board", 2)
    assert across_a_halt.halted_sessions_skipped == 1
