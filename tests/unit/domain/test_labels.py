"""The label contract (`V2-P1-017`): prediction day -> session -> window -> tradability.

Two claims this file exists to hold down:

- **The window is built, never assumed.** Its entry is the first open session after the
  prediction day, its exit is `horizon.sessions` sessions later, and both come from the
  published calendar, which refuses rather than guessing past its horizon.
- **The return is computed on a path that is right across an ex-rights day.** The measured
  `000001.SZ` 2026-06-11/12 pair is used verbatim, because on it the naive
  `close[t]/close[t-1] - 1` gives `-0.530973%` where the truth is `+2.742230%` -- the *sign* is
  wrong, so a test that checked only the magnitude would pass on the broken path.

The fixed window every fixture below uses: a signal dated 16:30 Asia/Shanghai on Wednesday
2026-06-10, entering Thursday the 11th and exiting Friday the 12th on a `1d` horizon.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain import labels as labels_module
from openalpha_cn.domain.adjustment import (
    AdjustmentHistory,
    FactorObservation,
    build_adjustment_history,
)
from openalpha_cn.domain.daily_prices import DailyBar, PriceDataError, SessionReturns
from openalpha_cn.domain.horizon import HorizonError, parse_horizon
from openalpha_cn.domain.labels import (
    KNOWN_LABEL_LIMITATIONS,
    LABEL_REFUSAL_CODES,
    MINIMUM_LABEL_ZONE_OFFSET,
    REFUSAL_BEYOND_REGISTRY_SNAPSHOT,
    REFUSAL_DELISTED,
    REFUSAL_HALTED_INTO_THE_CLOSE,
    REFUSAL_HALTED_SESSION,
    REFUSAL_LOCKED_AT_LIMIT,
    REFUSAL_MISSING_BAR,
    REFUSAL_NOT_YET_LISTED,
    REFUSAL_UNPUBLISHED_BAND,
    HaltCorpus,
    LabelError,
    LabelWindow,
    OutcomeLabel,
    WindowReturn,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
    window_return,
)
from openalpha_cn.domain.price_limits import (
    PriceLimit,
    SuspensionRecord,
    build_suspension_day,
)
from openalpha_cn.domain.stock_universe import (
    SecurityLifecycle,
    StockUniverse,
    StockUniverseError,
)
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    CalendarHorizonError,
    TradingCalendar,
    build_trading_calendar,
)

CODE = "000001.SZ"
EXCHANGE = "SZSE"
SHANGHAI = ZoneInfo("Asia/Shanghai")
YEAR = 2026

FIRST_DAY = date(2026, 6, 1)
LAST_DAY = date(2026, 6, 30)
PREDICTED_AT = datetime(2026, 6, 10, 8, 30, tzinfo=UTC)
"""16:30 Asia/Shanghai on Wednesday 2026-06-10 -- `DAILY_AVAILABILITY_TIME`, the instant that
session's cross section becomes knowable."""

# --- the measured ex-rights pair -------------------------------------------------------------
#
# `domain/daily_prices.py`'s module docstring, carried here as data rather than restated as
# prose. `pre_close` on the 12th is 10.94 while the 11th closed at 11.30.

ENTRY = date(2026, 6, 11)
EXIT = date(2026, 6, 12)
ENTRY_CLOSE = 11.30
ENTRY_PRE_CLOSE = 11.32
ENTRY_FACTOR = 134.5794
EXIT_CLOSE = 11.24
EXIT_PRE_CLOSE = 10.94
EXIT_FACTOR = 139.008

PUBLISHED_RETURN = 0.02742230347349195
ADJUSTED_RETURN = 0.027422506154573423
UNADJUSTED_RETURN = -0.005309734513274433
"""The three paths over that one session, to full double precision.

The first two are the correct ones and agree to 2.0e-7; the third is the one this contract
exists to keep out of a label, and it is negative where both correct paths are positive.
"""

AFTER = date(2026, 6, 15)
AFTER_CLOSE = 11.50


# --- builders --------------------------------------------------------------------------------


def _calendar(*, closed: Sequence[date] = ()) -> TradingCalendar:
    """A weekday calendar over June 2026 with `closed` additionally marked shut."""
    shut = set(closed)
    span = (LAST_DAY - FIRST_DAY).days + 1
    days = [FIRST_DAY + timedelta(days=offset) for offset in range(span)]
    return build_trading_calendar(
        EXCHANGE,
        [
            CalendarDay(calendar_date=day, is_trading=day.weekday() < 5 and day not in shut)
            for day in days
        ],
    )


def _bar(
    day: date,
    close: float,
    pre_close: float,
    *,
    code: str = CODE,
    high: float | None = None,
    low: float | None = None,
) -> DailyBar:
    return DailyBar(
        ts_code=code,
        trade_date=day,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1000.0,
        amount=10000.0,
    )


def _history(*observations: tuple[date, float]) -> AdjustmentHistory:
    return build_adjustment_history(
        CODE,
        [
            FactorObservation(ts_code=CODE, observed_on=day, factor=factor)
            for day, factor in observations
        ],
    )


def _universe(
    *,
    listed_on: date = date(1991, 4, 3),
    delisted_on: date | None = None,
    snapshot: date = LAST_DAY,
) -> StockUniverse:
    return StockUniverse(
        snapshot_date=snapshot,
        securities=(
            SecurityLifecycle(
                ts_code=CODE, exchange=EXCHANGE, listed_on=listed_on, delisted_on=delisted_on
            ),
        ),
    )


def _band(days: Sequence[date]) -> dict[date, PriceLimit]:
    """A band no fixture bar comes near, so limit handling is off unless a test turns it on."""
    return {
        day: PriceLimit(ts_code=CODE, trade_date=day, up_limit=1000.0, down_limit=0.01)
        for day in days
    }


def _locked(days: Sequence[date], day: date, price: float, *, side: str) -> dict[date, PriceLimit]:
    limits = _band(days)
    limits[day] = PriceLimit(
        ts_code=CODE,
        trade_date=day,
        up_limit=price if side == "up" else 1000.0,
        down_limit=price if side == "down" else 0.01,
    )
    return limits


def _halt(day: date, *, suspend_type: str, timing: str | None) -> HaltCorpus:
    return halt_corpus_for_years(
        {
            day: build_suspension_day(
                day,
                [
                    SuspensionRecord(
                        ts_code=CODE, trade_date=day, suspend_type=suspend_type, timing=timing
                    )
                ],
            )
        },
        years=(YEAR,),
    )


def _no_halts() -> HaltCorpus:
    """A corpus that was read for 2026 and found nothing, which is not the same value as one
    that was never read -- see
    `test_a_window_reaching_outside_the_halt_read_raises_rather_than_labelling`."""
    return halt_corpus_for_years({}, years=(YEAR,))


def _window(horizon: str = "1d", *, closed: Sequence[date] = ()) -> LabelWindow:
    return build_label_window(
        as_of=PREDICTED_AT,
        zone=SHANGHAI,
        horizon=parse_horizon(horizon),
        calendar=_calendar(closed=closed),
    )


def _bars(*days: date) -> dict[date, DailyBar]:
    prices = {
        ENTRY: (ENTRY_CLOSE, ENTRY_PRE_CLOSE),
        EXIT: (EXIT_CLOSE, EXIT_PRE_CLOSE),
        AFTER: (AFTER_CLOSE, EXIT_CLOSE),
    }
    return {day: _bar(day, *prices[day]) for day in days}


def _factors(*days: date) -> AdjustmentHistory:
    steps = {ENTRY: ENTRY_FACTOR, EXIT: EXIT_FACTOR, AFTER: EXIT_FACTOR}
    return _history(*((day, steps[day]) for day in days))


def _label(
    *,
    bars: Mapping[date, DailyBar] | None = None,
    factors: AdjustmentHistory | None = None,
    limits: Mapping[date, PriceLimit] | None = None,
    halts: HaltCorpus | None = None,
    universe: StockUniverse | None = None,
) -> OutcomeLabel:
    window = _window()
    return label_outcome(
        window,
        ts_code=CODE,
        bars=_bars(ENTRY, EXIT) if bars is None else bars,
        factors=_factors(ENTRY, EXIT) if factors is None else factors,
        limits=_band(window.sessions) if limits is None else limits,
        halts=_no_halts() if halts is None else halts,
        universe=_universe() if universe is None else universe,
    )


def _codes(label: OutcomeLabel) -> list[tuple[str, date]]:
    return [(item.code, item.day) for item in label.refusals]


# --- prediction day -> tradable session ------------------------------------------------------


def test_the_entry_session_is_the_first_one_after_the_prediction_day() -> None:
    window = _window()

    assert window.prediction_day == date(2026, 6, 10)
    assert window.entry_day == ENTRY
    assert window.exit_day == EXIT
    assert window.exchange == EXCHANGE


def test_the_prediction_day_is_the_exchange_date_of_the_signals_own_instant() -> None:
    """08:30 UTC on the 11th is 16:30 Asia/Shanghai the same day; 23:00 UTC is already the 12th.

    So which zone dates the instant decides which sessions the label is measured over. Which
    *direction* a UTC-dated one would go is the next test's subject, and it is not the one an
    earlier reading of this file claimed.
    """
    horizon = parse_horizon("1d")
    calendar = _calendar()

    after_close = build_label_window(
        as_of=datetime(2026, 6, 11, 8, 30, tzinfo=UTC),
        zone=SHANGHAI,
        horizon=horizon,
        calendar=calendar,
    )
    late_evening = build_label_window(
        as_of=datetime(2026, 6, 11, 23, 0, tzinfo=UTC),
        zone=SHANGHAI,
        horizon=horizon,
        calendar=calendar,
    )

    assert (after_close.prediction_day, after_close.entry_day) == (ENTRY, EXIT)
    assert (late_evening.prediction_day, late_evening.entry_day) == (EXIT, AFTER)


def test_the_zone_argument_and_not_a_hard_coded_shanghai_decides_the_window() -> None:
    """One instant, two legal zones, two different windows -- and the UTC one is *not* early.

    This is the test the `zone` parameter did not have. Every other fixture in this file passes
    `Asia/Shanghai`, so an implementation that ignored the argument and hard-coded
    `ZoneInfo("Asia/Shanghai")` answered every one of them correctly. Here it cannot: 23:00 UTC
    on the 11th is already the 12th in Shanghai, so Shanghai enters on Monday the 15th while
    UTC enters on Friday the 12th.

    And the second half is the correction. 16:00 Asia/Shanghai is 08:00 UTC the *same* day, so
    the two datings agree on everything from 08:00 UTC onward and can differ only in the
    pre-open hours -- where UTC is the *earlier* date and therefore the *earlier*, still
    unfixed, entry close. `2026-06-12T07:00+08:00` is the exit session's own close, and the UTC
    entry is that very session: in the future at the moment the signal was formed, not the
    past. A UTC-dated label is tighter than a Shanghai-dated one, never early.
    """
    instant = datetime(2026, 6, 11, 23, 0, tzinfo=UTC)
    horizon = parse_horizon("1d")
    calendar = _calendar()

    in_shanghai = build_label_window(
        as_of=instant, zone=SHANGHAI, horizon=horizon, calendar=calendar
    )
    in_utc = build_label_window(as_of=instant, zone=UTC, horizon=horizon, calendar=calendar)

    assert (in_shanghai.prediction_day, in_shanghai.entry_day) == (EXIT, AFTER)
    assert (in_utc.prediction_day, in_utc.entry_day) == (ENTRY, EXIT)
    assert in_utc.zone is UTC
    assert datetime.combine(in_utc.entry_day, time(15, 0), tzinfo=SHANGHAI) > instant


def test_a_zone_far_enough_west_to_date_a_look_ahead_is_refused() -> None:
    """The counterexample to "never early", made unreachable.

    A 15:30 Asia/Shanghai signal on 2026-01-15 -- after the close, which is this design's own
    target case -- is 2026-01-14T23:30-08:00 in Los Angeles. Dated there the prediction day is
    the 14th, the entry is the 15th, and the entry price is the 15th's 15:00 close: fixed
    thirty minutes *before* the signal existed. A whole session of look-ahead, silently.
    """
    signal = datetime(2026, 1, 15, 15, 30, tzinfo=SHANGHAI)
    january = build_trading_calendar(
        EXCHANGE,
        [
            CalendarDay(
                calendar_date=date(2026, 1, day), is_trading=date(2026, 1, day).weekday() < 5
            )
            for day in range(1, 32)
        ],
    )

    with pytest.raises(LabelError, match=r"is -8h from UTC .* west of the -7h"):
        build_label_window(
            as_of=signal,
            zone=ZoneInfo("America/Los_Angeles"),
            horizon=parse_horizon("1d"),
            calendar=january,
        )


def test_the_zone_is_judged_on_the_offset_it_had_at_that_instant_not_on_its_name() -> None:
    """`America/Los_Angeles` is -8h in January and -7h in July, and only the first can date a
    look-ahead: the derivation in `MINIMUM_LABEL_ZONE_OFFSET` makes the boundary exactly -7h,
    at which the interval of look-ahead hours is empty. A guard keyed on the zone's name, or on
    a fixed offset table, would refuse both.
    """
    july = build_trading_calendar(
        EXCHANGE,
        [
            CalendarDay(
                calendar_date=date(2026, 7, day), is_trading=date(2026, 7, day).weekday() < 5
            )
            for day in range(1, 32)
        ],
    )
    los_angeles = ZoneInfo("America/Los_Angeles")
    signal = datetime(2026, 7, 15, 15, 30, tzinfo=SHANGHAI)

    window = build_label_window(
        as_of=signal, zone=los_angeles, horizon=parse_horizon("1d"), calendar=july
    )

    assert signal.astimezone(los_angeles).utcoffset() == MINIMUM_LABEL_ZONE_OFFSET
    assert window.prediction_day == date(2026, 7, 15)
    assert datetime.combine(window.entry_day, time(15, 0), tzinfo=SHANGHAI) > signal


def test_a_prediction_made_on_a_closed_day_still_enters_on_the_next_session() -> None:
    window = build_label_window(
        as_of=datetime(2026, 6, 13, 4, 0, tzinfo=UTC),
        zone=SHANGHAI,
        horizon=parse_horizon("1d"),
        calendar=_calendar(),
    )

    assert window.prediction_day == date(2026, 6, 13)
    assert window.entry_day == AFTER


def test_a_naive_signal_instant_is_refused_before_it_can_be_dated() -> None:
    with pytest.raises(ValueError, match="datetime must be timezone-aware"):
        build_label_window(
            as_of=datetime(2026, 6, 10, 16, 30),
            zone=SHANGHAI,
            horizon=parse_horizon("1d"),
            calendar=_calendar(),
        )


# --- the return window -----------------------------------------------------------------------


def test_the_window_spans_exactly_the_horizons_sessions_across_a_weekend() -> None:
    """Five sessions from Thursday the 11th is Thursday the 18th -- seven calendar days.

    The sessions are asserted against the calendar's own listing of the interval rather than
    against the arithmetic that produced the exit, so a `shift` counting calendar days is
    caught here rather than showing up as a window one weekend too short.
    """
    window = _window("5d")

    assert window.exit_day == date(2026, 6, 18)
    assert window.sessions == (
        ENTRY,
        EXIT,
        date(2026, 6, 15),
        date(2026, 6, 16),
        date(2026, 6, 17),
        date(2026, 6, 18),
    )
    assert window.session_count == 5


def test_the_window_skips_a_closed_weekday_the_way_the_exchange_did() -> None:
    window = _window("3d", closed=(date(2026, 6, 16),))

    assert window.sessions == (ENTRY, EXIT, date(2026, 6, 15), date(2026, 6, 17))
    assert window.session_count == 3


def test_a_window_whose_exit_leaves_the_published_calendar_is_refused_not_truncated() -> None:
    with pytest.raises(CalendarHorizonError, match=r"\+5 sessions from 2026-06-30"):
        build_label_window(
            as_of=datetime(2026, 6, 29, 4, 0, tzinfo=UTC),
            zone=SHANGHAI,
            horizon=parse_horizon("5d"),
            calendar=_calendar(),
        )


def test_a_sessions_close_instant_is_dated_in_the_windows_own_timezone() -> None:
    """15:00 Asia/Shanghai is 07:00 UTC, and it is the instant the two prices were observed at.

    Dating it in UTC would put the pair on the wrong calendar date for every session, which is
    the same one-day slip `panel/catalog.py` records for a session at 08:00 Asia/Shanghai.
    """
    window = _window()

    assert window.close_instant(ENTRY) == datetime(2026, 6, 11, 7, 0, tzinfo=UTC)
    assert window.close_instant(EXIT).astimezone(SHANGHAI).hour == 15


def test_an_instant_is_refused_for_a_day_the_window_does_not_cover() -> None:
    with pytest.raises(LabelError, match="2026-06-15 is not one of this window's sessions"):
        _window().close_instant(AFTER)


def test_a_calendar_horizon_cannot_size_a_return_window() -> None:
    with pytest.raises(HorizonError, match="is not a whole number of trading sessions"):
        build_label_window(
            as_of=PREDICTED_AT,
            zone=SHANGHAI,
            horizon=parse_horizon("3m"),
            calendar=_calendar(),
        )


# --- the return itself -----------------------------------------------------------------------


def test_the_label_return_is_right_across_an_ex_rights_day_where_the_naive_path_flips_sign() -> (
    None
):
    """The whole point of the contract, on the pair it was measured on.

    `close[t]/close[t-1] - 1` is `-0.53%` and both correct paths are `+2.74%`. All three are
    carried so the size of the error is visible; the factor path is the label's number.
    """
    computed = _label().window_return

    assert computed is not None
    assert computed.published == pytest.approx(PUBLISHED_RETURN, abs=1e-15)
    assert computed.adjusted == pytest.approx(ADJUSTED_RETURN, abs=1e-15)
    assert computed.unadjusted == pytest.approx(UNADJUSTED_RETURN, abs=1e-15)
    assert computed.unadjusted < 0.0 < computed.adjusted
    assert _label().realized_return == ADJUSTED_RETURN


def test_the_two_correct_paths_agree_inside_the_bound_the_published_precision_allows() -> None:
    """Magnitudes, not merely `<=`: the residue is 2.0e-7 against a 9.2e-4 bound, and the naive
    path's error on the same session is 3.3e-2 -- five orders of magnitude above the
    residue and 36 times the bound.
    """
    computed = _label().window_return

    assert computed is not None
    assert computed.disagreement == pytest.approx(1.972714e-07, rel=1e-4)
    assert computed.tolerance == pytest.approx(0.0009155392, rel=1e-6)
    assert computed.disagreement <= computed.tolerance
    assert abs(computed.unadjusted - computed.adjusted) > 30.0 * computed.tolerance


def test_a_factor_history_missing_its_ex_rights_step_is_refused_rather_than_returned() -> None:
    """The failure the cross-check exists for. A factor partition with a session hole answers
    with the previous day's factor, which implies a previous close of 11.30 against a published
    `pre_close` of 10.94 -- a 0.36 gap where 0.0100 is allowed.
    """
    flat = _history((ENTRY, ENTRY_FACTOR), (EXIT, ENTRY_FACTOR))

    with pytest.raises(PriceDataError, match="The two datasets disagree about that session"):
        _label(factors=flat)


def test_a_multi_session_window_chains_the_published_path_and_telescopes_the_factor_one() -> None:
    """Two sessions, the ex-rights step falling on the first of them.

    The chained `close/pre_close` path never reads a factor and the telescoped path never reads
    a `pre_close`, so their agreement is evidence rather than arithmetic. The naive path is
    1.77% against a true 5.12% -- still the wrong answer, without the sign flip to advertise it.
    """
    window = _window("2d")

    computed = window_return(
        window,
        ts_code=CODE,
        bars=_bars(ENTRY, EXIT, AFTER),
        factors=_factors(ENTRY, EXIT, AFTER),
    )

    assert (computed.entry_day, computed.exit_day) == (ENTRY, AFTER)
    assert len(computed.per_session) == 2
    assert computed.published == pytest.approx(0.05118829981718487, abs=1e-15)
    assert computed.adjusted == pytest.approx(0.05118850718661849, abs=1e-15)
    assert computed.unadjusted == pytest.approx(0.017699115044247815, abs=1e-15)
    assert computed.tolerance == pytest.approx(0.001807473, rel=1e-6)
    assert computed.disagreement <= computed.tolerance


def test_the_window_return_refuses_a_bar_belonging_to_another_security() -> None:
    bars = _bars(ENTRY, EXIT)
    bars[EXIT] = _bar(EXIT, EXIT_CLOSE, EXIT_PRE_CLOSE, code="600000.SH")

    with pytest.raises(LabelError, match=re.escape("is 600000.SH's and this label is 000001.SZ's")):
        window_return(_window(), ts_code=CODE, bars=bars, factors=_factors(ENTRY, EXIT))


def test_the_window_return_refuses_a_bar_dated_other_than_the_session_it_is_filed_under() -> None:
    bars = _bars(ENTRY, EXIT)
    bars[EXIT] = _bar(ENTRY, EXIT_CLOSE, EXIT_PRE_CLOSE)

    with pytest.raises(LabelError, match="is dated 2026-06-11 and is filed under 2026-06-12"):
        window_return(_window(), ts_code=CODE, bars=bars, factors=_factors(ENTRY, EXIT))


def test_the_window_return_refuses_a_session_it_has_no_bar_for() -> None:
    with pytest.raises(LabelError, match="has no bar on 2026-06-12"):
        window_return(_window(), ts_code=CODE, bars=_bars(ENTRY), factors=_factors(ENTRY, EXIT))


def test_a_refused_label_will_not_hand_out_a_number() -> None:
    """And the refusal it raises names the security, the window and every code against it, so
    a caller catching it can act without going back for the label.
    """
    label = _label(halts=_halt(EXIT, suspend_type="S", timing=None))

    assert not label.is_labelled
    assert label.window_return is None
    assert label.refusal_summary == (
        "000001.SZ over 2026-06-11..2026-06-12 carries 1 refusal(s): halted_session on 2026-06-12"
    )
    with pytest.raises(LabelError, match=re.escape(label.refusal_summary)):
        _ = label.realized_return


# --- boundary: price limits ------------------------------------------------------------------


def test_a_session_locked_at_its_up_limit_all_day_refuses_the_label() -> None:
    """一字板: `low >= up_limit`, so there was no counterparty and no position was opened."""
    label = _label(limits=_locked(_window().sessions, ENTRY, ENTRY_CLOSE, side="up"))

    assert _codes(label) == [(REFUSAL_LOCKED_AT_LIMIT, ENTRY)]
    assert label.entry_touch is not None
    assert label.entry_touch.one_price_up


def test_a_session_locked_at_its_down_limit_all_day_refuses_the_label_too() -> None:
    label = _label(limits=_locked(_window().sessions, ENTRY, ENTRY_CLOSE, side="down"))

    assert _codes(label) == [(REFUSAL_LOCKED_AT_LIMIT, ENTRY)]
    assert label.entry_touch is not None
    assert label.entry_touch.one_price_down


def test_a_lock_on_the_exit_session_refuses_the_label_as_well() -> None:
    label = _label(limits=_locked(_window().sessions, EXIT, EXIT_CLOSE, side="up"))

    assert _codes(label) == [(REFUSAL_LOCKED_AT_LIMIT, EXIT)]
    assert label.exit_touch is not None
    assert label.exit_touch.one_price_up


def test_a_limit_the_session_merely_touched_is_recorded_and_does_not_refuse() -> None:
    """The distinction the acceptance criterion turns on. A bar that reached its band and also
    traded away from it filled perfectly well; only a session with no counterparty did not.
    """
    bars = _bars(ENTRY, EXIT)
    bars[ENTRY] = _bar(ENTRY, ENTRY_CLOSE, ENTRY_PRE_CLOSE, high=11.55, low=11.00)
    limits = _band(_window().sessions)
    limits[ENTRY] = PriceLimit(ts_code=CODE, trade_date=ENTRY, up_limit=11.55, down_limit=10.90)

    label = _label(bars=bars, limits=limits)

    assert label.is_labelled
    assert label.entry_touch is not None
    assert label.entry_touch.at_up
    assert not label.entry_touch.one_price_up


def test_a_session_with_no_published_band_refuses_rather_than_assuming_it_was_tradeable() -> None:
    """`stk_limit` serves nothing before 2007-01-04 and reached the Beijing board a decade
    after `daily` did, so a bar with no band is a real shape. Reading an absent band as "not at
    a limit" is the fail-open direction, and this is the fail-closed one.
    """
    limits = _band(_window().sessions)
    del limits[ENTRY]

    label = _label(limits=limits)

    assert _codes(label) == [(REFUSAL_UNPUBLISHED_BAND, ENTRY)]
    assert label.entry_touch is None
    assert label.exit_touch is not None


def test_a_lock_on_a_session_between_the_two_ends_does_not_refuse() -> None:
    """Only the sessions a position is opened and closed on decide tradability. A locked
    session in the middle is held through and its return is in the chain like any other.
    """
    window = _window("2d")

    label = label_outcome(
        window,
        ts_code=CODE,
        bars=_bars(ENTRY, EXIT, AFTER),
        factors=_factors(ENTRY, EXIT, AFTER),
        limits=_locked(window.sessions, EXIT, EXIT_CLOSE, side="up"),
        halts=_no_halts(),
        universe=_universe(),
    )

    assert label.is_labelled
    assert label.realized_return == pytest.approx(0.05118850718661849, abs=1e-15)


# --- boundary: the three suspension states ---------------------------------------------------


def test_a_whole_day_halt_refuses_and_names_both_the_halt_and_the_absent_bar() -> None:
    """An untimed `S` is the only state that means "no bar", and both facts are reported.

    Merging them is the error `explain_unpriced` exists to avoid: an absent bar with a halt
    behind it and an absent bar with nothing behind it are different findings.
    """
    label = _label(bars=_bars(ENTRY), halts=_halt(EXIT, suspend_type="S", timing=None))

    assert _codes(label) == [(REFUSAL_HALTED_SESSION, EXIT), (REFUSAL_MISSING_BAR, EXIT)]


def test_an_intraday_halt_that_ended_before_the_close_traded_and_is_labelled() -> None:
    """31 of 2015-07-08's 1,343 `S` rows carried a timing window and all 31 had a bar.

    A morning halt is the case where "the security traded" and "the close is an auction price"
    are both true, so nothing refuses.
    """
    label = _label(halts=_halt(EXIT, suspend_type="S", timing="09:30-13:00"))

    assert label.is_labelled
    assert label.realized_return == ADJUSTED_RETURN


def test_an_intraday_halt_running_into_the_close_refuses_that_end_and_this_is_the_limitation() -> (
    None
):
    """`13:00-15:00` -- halted from the lunch break to the bell -- is the common shape, and the
    session's `close` is then the last print before 13:00 rather than a price anything could
    have been filled at.

    This file previously asserted the opposite of this (`test_an_intraday_halt_traded_and_is_
    labelled`, on this very timing string): the row *had* traded, so `TradingState.interrupted`
    was right and the label went out anyway. That is `REFUSAL_LOCKED_AT_LIMIT`'s own argument
    -- "there was no counterparty ... could not have been opened or closed at that session's
    price" -- left unenforced on the other side. 39 of the 59 timed `S` rows served across 68
    whole-market sessions run through the close; on 2015-07-08, 30 of the 31 do, of which 20
    were already one-price sessions and 10 were being labelled at a 13:00 print.
    """
    label = _label(halts=_halt(EXIT, suspend_type="S", timing="13:00-15:00"))

    assert _codes(label) == [(REFUSAL_HALTED_INTO_THE_CLOSE, EXIT)]
    assert label.window_return is None
    assert "was still open at 14:57" in label.refusals[0].detail


@pytest.mark.parametrize(
    ("timing", "refused"),
    [
        ("13:00-15:00", True),
        ("14:52-14:57", True),
        ("13:36-13:46,14:53-14:57", True),
        ("09:30-11:30,13:00-15:00", True),
        ("halted all afternoon", True),
        ("09:30-13:00", False),
        ("09:30-10:00", False),
        ("09:31-09:41,09:43-09:53", False),
        ("11:21-13:01", False),
    ],
)
def test_each_measured_timing_shape_is_read_the_way_its_right_endpoint_reads(
    timing: str, refused: bool
) -> None:
    """All but `09:30-11:30,13:00-15:00` and the unparseable one were served by `suspend_d` on
    a real session between 2013 and 2026.

    The boundary is `14:57`, when the closing call auction opens: `14:52-14:57` refuses because
    `suspend_timing` does not say whether that security joined the auction and reading it as
    "resumed in time" is the fail-open direction, while `11:21-13:01` does not because the
    afternoon ran normally after it. An unparseable window refuses for the same reason as the
    boundary one -- an unknown right endpoint is not an early one.
    """
    label = _label(halts=_halt(ENTRY, suspend_type="S", timing=timing))

    assert (_codes(label) == [(REFUSAL_HALTED_INTO_THE_CLOSE, ENTRY)]) is refused
    assert label.is_labelled is not refused


def test_a_close_spanning_halt_between_the_two_ends_is_held_through() -> None:
    """`only_the_two_ends_are_tested_for_tradability`, applied to this refusal too: a position
    opened at the entry close and closed at the exit close is not traded in between, and the
    published chain still telescopes because the next session's `pre_close` is that stale close.
    """
    window = _window("2d")

    label = label_outcome(
        window,
        ts_code=CODE,
        bars=_bars(ENTRY, EXIT, AFTER),
        factors=_factors(ENTRY, EXIT, AFTER),
        limits=_band(window.sessions),
        halts=_halt(EXIT, suspend_type="S", timing="13:00-15:00"),
        universe=_universe(),
    )

    assert label.is_labelled
    assert label.realized_return == pytest.approx(0.05118850718661849, abs=1e-15)


def test_a_resumption_traded_and_is_labelled() -> None:
    """An `R` row is a resumption, not a halt, and it carries no window to test against the
    close either.

    Not "every `R` row has a bar": `TradingState.resumed` withdrew that reading -- a quarterly
    sweep found seven sessions carrying an `R` with no bar, every one a NEEQ-era `.BJ` code
    `daily` did not serve at all. Such a row lands in `REFUSAL_MISSING_BAR` here, which is the
    loud direction, rather than being excused.
    """
    label = _label(halts=_halt(EXIT, suspend_type="R", timing=None))

    assert label.is_labelled


def test_an_absent_bar_with_no_halt_behind_it_is_reported_as_exactly_that() -> None:
    """A short fetch, which `suspend_d` does not explain. One refusal here, not two."""
    label = _label(bars=_bars(ENTRY))

    assert _codes(label) == [(REFUSAL_MISSING_BAR, EXIT)]


def test_a_session_the_halt_corpus_says_nothing_about_is_treated_as_having_traded() -> None:
    """`suspensions_from_panel_rows` keys only the sessions that carry rows, and 5,312 of
    2024-06-28's 5,338 priced names have no row at all, so an absent session **inside the read**
    is the ordinary case rather than an unread one.
    """
    assert _label(halts=_halt(date(2026, 6, 8), suspend_type="S", timing=None)).is_labelled


def test_a_window_reaching_outside_the_halt_read_raises_rather_than_labelling() -> None:
    """The one input that used to be fail-open. `bars` and `limits` refuse a session they have
    no key for, `universe` carries `snapshot_date`, `factors` carries `covered_from`/
    `covered_through` and `calendar` raises past its horizon -- but an absent halt session means
    "nothing happened", so a corpus that was never read is indistinguishable from a quiet one.

    `load_suspensions` reads **by year** while a 5d or 10d window straddles New Year by
    construction, which is how a caller reaches this without doing anything unusual.
    """
    window = _window()

    with pytest.raises(LabelError, match=r"read over 2025-01-01\.\.2025-12-31 and this window"):
        label_outcome(
            window,
            ts_code=CODE,
            bars=_bars(ENTRY, EXIT),
            factors=_factors(ENTRY, EXIT),
            limits=_band(window.sessions),
            halts=halt_corpus_for_years({}, years=(2025,)),
            universe=_universe(),
        )


def test_the_same_bars_label_or_not_depending_only_on_which_year_of_halts_was_read() -> None:
    """The failure the coverage span closes, shown as the pair it used to be.

    One corpus was read for 2026 and found an untimed halt on the exit; the other was read for
    2025 and knows nothing about June 2026. Before `HaltCorpus`, the second answered "labelled"
    -- the same bars, the same limits, the same registry, and a number instead of a refusal,
    decided entirely by whether the caller happened to load that partition.
    """
    read_2026 = _label(bars=_bars(ENTRY), halts=_halt(EXIT, suspend_type="S", timing=None))

    assert _codes(read_2026) == [(REFUSAL_HALTED_SESSION, EXIT), (REFUSAL_MISSING_BAR, EXIT)]
    with pytest.raises(LabelError, match="a partition nobody opened"):
        label_outcome(
            _window(),
            ts_code=CODE,
            bars=_bars(ENTRY),
            factors=_factors(ENTRY, EXIT),
            limits=_band(_window().sessions),
            halts=halt_corpus_for_years({}, years=(2025,)),
            universe=_universe(),
        )


def test_a_halt_corpus_over_no_years_is_refused_at_construction() -> None:
    with pytest.raises(LabelError, match="a halt corpus needs at least one year"):
        halt_corpus_for_years({}, years=())


def test_a_corpus_read_across_a_year_boundary_covers_both_sides_of_it() -> None:
    """The shape the year-keyed reader produces when a caller does load both partitions."""
    corpus = halt_corpus_for_years({}, years=(2025, 2026))

    assert (corpus.covered_from, corpus.covered_through) == (date(2025, 1, 1), date(2026, 12, 31))
    corpus.require_coverage((date(2025, 12, 31), date(2026, 1, 5)))


# --- boundary: the registry ------------------------------------------------------------------


def test_a_delisting_inside_the_window_refuses_and_names_the_delisting() -> None:
    """`delisted_securities_carry_unstable_factors`: a delisted name keeps producing factor
    rows for years and they oscillate between two levels, so an adjusted return spanning a
    delisting date is untrustworthy. The absent bar is reported beside it, not instead of it.
    """
    label = _label(bars=_bars(ENTRY), universe=_universe(delisted_on=EXIT))

    assert _codes(label) == [(REFUSAL_DELISTED, EXIT), (REFUSAL_MISSING_BAR, EXIT)]


def test_a_security_not_yet_listed_over_the_window_refuses_on_every_session() -> None:
    label = _label(universe=_universe(listed_on=date(2026, 6, 22)))

    assert _codes(label) == [
        (REFUSAL_NOT_YET_LISTED, ENTRY),
        (REFUSAL_NOT_YET_LISTED, EXIT),
    ]


def test_a_window_past_the_registry_snapshot_refuses_rather_than_reading_it_as_unlisted() -> None:
    label = _label(universe=_universe(snapshot=ENTRY))

    assert _codes(label) == [(REFUSAL_BEYOND_REGISTRY_SNAPSHOT, EXIT)]


def test_a_security_the_registry_has_never_heard_of_raises_rather_than_being_refused() -> None:
    """An unknown code is a caller mistake, not a property of the window."""
    unknown = StockUniverse(
        snapshot_date=LAST_DAY,
        securities=(
            SecurityLifecycle(ts_code="600000.SH", exchange="SSE", listed_on=date(1999, 11, 10)),
        ),
    )

    with pytest.raises(StockUniverseError, match="is not in the 2026-06-30 registry snapshot"):
        _label(universe=unknown)


# --- the closed set --------------------------------------------------------------------------


def test_every_declared_refusal_code_is_reachable_from_a_real_window() -> None:
    """A code nobody can produce is a branch nobody tests. Each one is driven here and the
    total compared against the declared tuple, so a code added with no way to reach it fails.
    """
    window = _window()
    unpublished = _band(window.sessions)
    del unpublished[ENTRY]

    produced = {
        item.code
        for label in (
            _label(bars=_bars(ENTRY)),
            _label(bars=_bars(ENTRY), halts=_halt(EXIT, suspend_type="S", timing=None)),
            _label(halts=_halt(EXIT, suspend_type="S", timing="13:00-15:00")),
            _label(limits=_locked(window.sessions, ENTRY, ENTRY_CLOSE, side="up")),
            _label(limits=unpublished),
            _label(bars=_bars(ENTRY), universe=_universe(delisted_on=EXIT)),
            _label(universe=_universe(listed_on=date(2026, 6, 22))),
            _label(universe=_universe(snapshot=ENTRY)),
        )
        for item in label.refusals
    }

    assert produced == set(LABEL_REFUSAL_CODES)


def test_the_refusal_table_reconciles_against_the_modules_own_constants_in_both_directions() -> (
    None
):
    """The half the reachability test above cannot see, and the hole `V2-P1-015`'s review named.

    That test compares *produced* codes against the tuple, so a `REFUSAL_*` constant declared,
    raised from `label_outcome` and simply left out of `LABEL_REFUSAL_CODES` passes it as long
    as no fixture happens to drive the new branch -- the table and the implementation drift
    apart and every assertion stays green. Scanning the module for the constants themselves
    closes it from the other side, and the two together mean the tuple is neither short nor
    long.
    """
    declared = {
        value
        for name, value in vars(labels_module).items()
        if name.startswith("REFUSAL_") and isinstance(value, str)
    }

    assert declared == set(LABEL_REFUSAL_CODES)
    assert len(LABEL_REFUSAL_CODES) == len(declared) == 8
    assert list(LABEL_REFUSAL_CODES) == sorted(LABEL_REFUSAL_CODES)


def test_the_tolerance_is_scaled_by_the_published_pre_close_and_not_the_implied_one() -> None:
    """The two agree to 2e-7 on the measured pair, so every fixture in this file passes either
    way. This one separates them by 10% and names which denominator the bound is stated in:
    `published` is the denominator of the `published` return the bound is a bound on, while
    `implied` is the factor path's reconstruction of it and is exactly what the tolerance
    exists to let differ.
    """
    session = SessionReturns(
        ts_code=CODE,
        day=EXIT,
        previous_day=ENTRY,
        published=0.0,
        adjusted=0.0,
        unadjusted=0.0,
        published_pre_close=10.0,
        implied_pre_close=11.0,
        factor=1.0,
        previous_factor=1.0,
    )
    computed = WindowReturn(
        ts_code=CODE,
        entry_day=ENTRY,
        exit_day=EXIT,
        published=0.0,
        adjusted=0.0,
        unadjusted=0.0,
        entry_adjusted_close=10.0,
        exit_adjusted_close=10.0,
        per_session=(session,),
    )

    assert session.tolerance == pytest.approx(0.0122, abs=1e-12)
    assert computed.tolerance == pytest.approx(session.tolerance / 10.0, rel=1e-12)
    assert computed.tolerance != pytest.approx(session.tolerance / 11.0, rel=1e-9)


def test_the_reported_bound_inflates_to_uselessness_over_a_long_window() -> None:
    """Stated because `disagreement <= tolerance` reads like a guarantee and stops being one.

    The per-session term is dominated by `MAX_PRE_CLOSE_DISAGREEMENT`, a flat 0.01 yuan, so it
    is largest exactly where the price is smallest. A 10-yuan `pre_close` on unit factors is
    allowed 0.0012 a session, and sixty of those compound to 7.46% -- wider than most things a
    sixty-session return could be.
    """
    session = SessionReturns(
        ts_code=CODE,
        day=EXIT,
        previous_day=ENTRY,
        published=0.0,
        adjusted=0.0,
        unadjusted=0.0,
        published_pre_close=10.0,
        implied_pre_close=10.0,
        factor=1.0,
        previous_factor=1.0,
    )
    one = WindowReturn(
        ts_code=CODE,
        entry_day=ENTRY,
        exit_day=EXIT,
        published=0.0,
        adjusted=0.0,
        unadjusted=0.0,
        entry_adjusted_close=10.0,
        exit_adjusted_close=10.0,
        per_session=(session,),
    )
    sixty = WindowReturn(
        ts_code=CODE,
        entry_day=ENTRY,
        exit_day=EXIT,
        published=0.0,
        adjusted=0.0,
        unadjusted=0.0,
        entry_adjusted_close=10.0,
        exit_adjusted_close=10.0,
        per_session=(session,) * 60,
    )

    assert one.tolerance == pytest.approx(0.0012, abs=1e-12)
    assert sixty.tolerance == pytest.approx(0.0746, abs=5e-5)


def test_the_declared_limitations_are_distinct_and_each_carries_a_reason() -> None:
    codes = [item.code for item in KNOWN_LABEL_LIMITATIONS]

    assert len(codes) == len(set(codes))
    assert all(len(item.detail) > 200 for item in KNOWN_LABEL_LIMITATIONS)
