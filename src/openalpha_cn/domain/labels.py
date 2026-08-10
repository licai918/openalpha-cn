"""The label contract (`V2-P1-017`) -- prediction day, tradable session, return, tradability.

A label is the supervised signal P3's factors and P4's leaderboards are fitted against. Before
this module there was no contract for one: `backtest/validation.py`'s `OutcomeObservation` took
two free `datetime`s and two free positive `float`s, checked that the second instant followed
the first, and `OutcomeValidator` computed `end_price / start_price - 1`. Nothing looked at the
calendar, at `SignalFrame.horizon`, at a halt, at a band, or at an adjustment factor.

## The chain, as four types rather than four conventions

    signal.as_of  --zone-->  prediction_day
                  --calendar.next_trading_day-->  entry_day
                  --calendar.shift(horizon.sessions)-->  exit_day
                  --bars + adj_factor + stk_limit + suspend_d-->  OutcomeLabel

`LabelWindow` is the first three and cannot be built inconsistently: `next_trading_day` returns
an open session or raises, and `shift` counts sessions from a session or raises, so "both ends
are trading days" and "the interval spans exactly `horizon.sessions` of them" are properties of
the constructor rather than checks bolted on after it. `sessions` is the calendar's own listing
of the closed interval, so a reader never has to re-derive it -- and a test comparing it against
the exit `shift` produced is comparing two different traversals of the same calendar.

**The entry is the session *after* the prediction day, never the prediction day itself.** A
session's own cross section becomes knowable at `DAILY_AVAILABILITY_TIME` (16:30 Asia/Shanghai,
`domain/daily_prices.py`), which is after its close, so a signal dated on session D was formed
from information that is complete only once D can no longer be traded. Entering on D+1 is
therefore exact for a post-close signal and one session conservative for a pre-open one; it is
never early. See `KNOWN_LABEL_LIMITATIONS`.

## The return, and why two of them are computed

`domain/daily_prices.py` measured three paths on `000001.SZ`'s 2026-06-12 ex-dividend session:
`close/pre_close - 1` gives `+2.742230%`, the `adj_factor` path `+2.742251%`, and
`close/prev_close - 1` gives **`-0.530973%`** -- wrong, with the sign reversed, and right on
every other day of that month. `session_returns` already cross-checks the two correct ones per
session against a tolerance built from the publication precision of the rows themselves.

`window_return` lifts that to a window and keeps the cross-check honest by making the two paths
read **different columns**:

- `published` is `prod(close_t / pre_close_t) - 1` over the window's sessions. It never reads a
  factor.
- `adjusted` is `(close_exit * f_exit) / (close_entry * f_entry) - 1`. It never reads a
  `pre_close`, and it reads only the two endpoint bars.

Those are not two spellings of one computation: the first is a chain of `len(sessions) - 1`
independent quotients and the second a single ratio of two adjusted prices, and they agree only
if the factor series and the `pre_close` series tell the same story about every session in
between. `unadjusted` is carried beside them for `SessionReturns.unadjusted`'s reason -- so the
size of the error is visible when a reader compares them, never so it is used.

`OutcomeLabel.realized_return` reports `adjusted`. The choice is not a judgement that the
factor path is the more correct of the two -- both are correct, and the module they come from
says so. It is that `adjusted` is exactly reproducible from **two prices on one scale**, which
is what lets `backtest.validation.observation_from_label` hand `OutcomeValidator` an
`OutcomeObservation` whose `end_price / start_price - 1` *is* this number rather than merely
resembling it. `published` is then the independent witness, and `disagreement`/`tolerance`
report how far apart they came out.

`WindowReturn.tolerance` is not a new constant. It is the per-session bound `pre_close_tolerance`
already defines, carried into return space (`tol_t / pre_close_t`) and compounded across the
chain. It is **reported and not enforced**, because `session_returns` has already enforced every
term of it: once each session passed, their product cannot exceed the product of their bounds.
What it is for is the reader who wants to see the residue -- 2.0e-7 against 9.2e-4 on the
measured pair -- rather than being told it is small.

## Tradability: three states, four flags, and one band that may not exist

A window can be well formed and still not carry a label, and every reason is named rather than
folded into a `None`:

- **A whole-day halt** (`TradingState.halted`, an untimed `S`) on any session of the window.
  The other two states traded -- all 31 of 2015-07-08's timed `S` rows had a bar, and so did
  every `R` row probed across eight sessions -- so they do not refuse. Counting them would drop
  most of a market on a heavy day for no reason.
- **A session with no counterparty** (一字板) on the entry or the exit. `LimitTouch` separates
  `at_up` (the bar reached its band) from `one_price_up` (the *whole* session traded there), and
  only the second refuses: a bar that touched its limit and also traded away from it filled.
  Only the two ends matter -- a locked session in the middle is held through.
- **No published band** on the entry or the exit. `stk_limit` serves nothing before 2007-01-04
  and reached the Beijing board a decade after `daily` did, so an absent band is a real shape;
  reading it as "not at a limit" is the fail-open direction.
- **An absent bar**, reported separately from the halt that may explain it, for
  `explain_unpriced`'s reason: a missing bar with a halt behind it and a missing bar with
  nothing behind it are different findings and a short fetch looks exactly like the second.
- **A registry verdict other than listed** -- delisted, not yet listed, or past the snapshot.
  The delisting case is the sharp one: `KNOWN_ADJUSTMENT_LIMITATIONS`'
  `delisted_securities_carry_unstable_factors` measured `600069.SH`'s factor oscillating
  between 6.415 and 0.6604 for years after its last bar, so an adjusted return spanning a
  delisting date is untrustworthy by measurement rather than by caution.

## Layering

Pure `datetime`/`dataclasses` plus six sibling `domain` modules. No provider, no store, no
clock; the same placement, and the same reason, as `domain/daily_prices.py`. The timezone is a
required argument rather than a default, because turning an instant into an exchange session
date is a decision this repository already records once (`panel/catalog.py`'s
`DEFAULT_DATE_TIMEZONE`) and a second default here would be a second place for it to drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Final

from openalpha_cn.domain.adjustment import AdjustmentHistory
from openalpha_cn.domain.daily_prices import (
    SESSION_CLOSE_TIME,
    DailyBar,
    SessionReturns,
    session_returns,
)
from openalpha_cn.domain.horizon import ResearchHorizon
from openalpha_cn.domain.price_limits import (
    LimitTouch,
    PriceLimit,
    SuspensionDay,
    TradingState,
    limit_touch,
)
from openalpha_cn.domain.stock_universe import ListingStatus, StockUniverse
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.trading_calendar import TradingCalendar

REFUSAL_HALTED_SESSION: Final[str] = "halted_session"
REFUSAL_MISSING_BAR: Final[str] = "missing_bar"
REFUSAL_LOCKED_AT_LIMIT: Final[str] = "locked_at_limit"
REFUSAL_UNPUBLISHED_BAND: Final[str] = "unpublished_band"
REFUSAL_DELISTED: Final[str] = "delisted_in_window"
REFUSAL_NOT_YET_LISTED: Final[str] = "not_yet_listed_in_window"
REFUSAL_BEYOND_REGISTRY_SNAPSHOT: Final[str] = "beyond_registry_snapshot"

LABEL_REFUSAL_CODES: Final[tuple[str, ...]] = (
    REFUSAL_BEYOND_REGISTRY_SNAPSHOT,
    REFUSAL_DELISTED,
    REFUSAL_HALTED_SESSION,
    REFUSAL_LOCKED_AT_LIMIT,
    REFUSAL_MISSING_BAR,
    REFUSAL_NOT_YET_LISTED,
    REFUSAL_UNPUBLISHED_BAND,
)
"""Every reason `label_outcome` declines to put a number on a window, as one closed set.

Stated as a tuple for `panel_gate.GATE_CODE_BLOCKS`' reason: a verdict per entry over a closed
set is a diff against a literal when it changes, where the same facts spread across per-branch
strings are a set of independent judgements nobody can total up.
`tests/unit/domain/test_labels.py` drives each one and compares the union against this tuple, so
a code added with no way to reach it fails there rather than sitting as an untested branch.
"""


class LabelError(ValueError):
    """Raised for a malformed label window, or a malformed question about one.

    A `ValueError` subclass to match `domain/daily_prices.py`'s `PriceDataError` and
    `domain/trading_calendar.py`'s `TradingCalendarError`. It is deliberately *not* what a
    halt, a limit lock or a delisting produces: those are properties of the market on that
    window, they are reported as `LabelRefusal`s, and a per-name loop has to be able to keep
    going past them.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class LabelLimitation:
    """One named boundary on what a label can be trusted to say."""

    code: str
    detail: str


KNOWN_LABEL_LIMITATIONS: Final[tuple[LabelLimitation, ...]] = (
    LabelLimitation(
        code="a_pre_open_signal_is_entered_one_session_late",
        detail=(
            "The entry is the first session strictly after the prediction day, and the "
            "prediction day is the exchange date of the signal's own instant. A session's "
            "cross section becomes knowable at DAILY_AVAILABILITY_TIME (16:30 Asia/Shanghai), "
            "so a signal dated after that on session D is entered on D+1, which is exact. A "
            "signal dated at 09:00 on D was formed from data through D-1 and could in reality "
            "have been traded on D itself; this contract still enters it on D+1. The error is "
            "one-directional -- one session late, never early -- and closing it needs the "
            "signal to carry which session's data it read, which SignalFrame does not have a "
            "field for. domain/price_limits.py's "
            "both_datasets_are_dated_one_session_late_rather_than_at_the_open is the same "
            "trade-off one layer down."
        ),
    ),
    LabelLimitation(
        code="a_calendar_horizon_cannot_size_a_window",
        detail=(
            "Only HorizonUnit.trading_days converts into a session count. A signal written "
            "'3m' -- and this repository stores one -- is a legal horizon that no window can "
            "be built from, because the number of sessions in a month is not fixed -- a "
            "month holding the Spring Festival recess is far shorter than an ordinary one, "
            "and trade_cal is amended mid-year (KNOWN_CALENDAR_LOOKAHEAD names a 2020 "
            "amendment that closed a session already published as open) -- and converting "
            "through a sessions-per-month constant "
            "would be an invented figure of exactly the kind V2-P1-017 exists to replace. "
            "build_label_window raises HorizonError rather than rounding, so such a signal is "
            "unlabelled by refusal rather than mislabelled by a guess."
        ),
    ),
    LabelLimitation(
        code="halted_over_counts_before_2015",
        detail=(
            "REFUSAL_HALTED_SESSION reads TradingState.halted, which is an S row with no "
            "suspend_timing. domain/price_limits.py's "
            "intraday_halts_are_unmarked_before_2015 measured that the timing column is null "
            "throughout the early history -- on 2005-01-04, 12 of 18 untimed S rows had a "
            "daily bar; on 2008-01-02, 15 of 143; on 2010-01-04, 1 of 41 -- so before "
            "2015-07-08 a security that traded through a partial halt is called halted here "
            "and its window is refused. The direction is the safe one for a label (a name is "
            "dropped, never mislabelled) and the wrong one for coverage: early history yields "
            "fewer labels than it should, and nothing in the data says how many."
        ),
    ),
    LabelLimitation(
        code="no_published_band_refuses_the_whole_of_the_early_history",
        detail=(
            "REFUSAL_UNPUBLISHED_BAND fires whenever the entry or the exit session has no "
            "stk_limit row, and domain/price_limits.py's "
            "stk_limit_starts_in_2007_and_reached_the_beijing_board_late measured that the "
            "dataset serves 0 rows for 2005-01-04 and 2006-01-04 and that daily is a subset "
            "of it only from 2023-01-03 (133 unbanded bars on 2021-10-08, 56 on 2020-01-02, "
            "44 on 2016-01-04, nearly all .BJ codes). So no window ending before 2007-01-04 "
            "can be labelled at all, and a Beijing-board name is unlabelable for years after "
            "it started trading. Fail-open is the alternative and it is worse: an absent band "
            "read as 'not at a limit' lets a one-price session into the training set as an "
            "ordinary fill."
        ),
    ),
    LabelLimitation(
        code="only_the_two_ends_are_tested_for_tradability",
        detail=(
            "A limit lock or an absent band on a session strictly between the entry and the "
            "exit does not refuse, because a position opened at the entry close and closed at "
            "the exit close is not traded in between. What that does not model is a strategy "
            "that would have exited early, or a stop that could not have been filled on a "
            "locked session, and neither is expressible in a contract whose only inputs are a "
            "horizon and a calendar. A whole-day halt in the middle does refuse, but for a "
            "different reason: there is no bar, so the published chain has a hole in it."
        ),
    ),
    LabelLimitation(
        code="a_delisting_inside_the_window_is_refused_rather_than_priced",
        detail=(
            "domain/adjustment.py's delisted_securities_carry_unstable_factors measured "
            "600069.SH's factor oscillating between 6.415 and 0.6604 for years after its last "
            "bar, and all five securities whose factor fell by more than 0.1% market-wide "
            "between 2023-12-29 and 2024-06-28 are delisted SH main-board names -- so 'any "
            "adjusted return spanning a delisting date is untrustworthy'. The label refuses "
            "the window rather than reporting the number the last tradeable session would "
            "give, which means a delisting return is absent from the supervised signal "
            "entirely. That is a survivorship hole in the labels, stated rather than closed: "
            "closing it needs a terminal-value convention (the last close? zero? the "
            "arrangement-board close?) that nothing in this repository has measured."
        ),
    ),
)
"""Named boundaries on what a label answers, each traceable to a measurement made elsewhere.

**Not an enumeration of every way a label could be wrong.** These are the ones that follow from
the boundaries the datasets underneath this contract have already measured.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class LabelRefusal:
    """One reason a window carries no number, and the session it applies to.

    A plain carrier, `LimitTouch`'s precedent: the rules live in `label_outcome`.
    """

    code: str
    day: date
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LabelWindow:
    """The sessions a label is measured over, and what it was derived from.

    Build it with `build_label_window`; this constructor is not a boundary and validates
    nothing, following `TradingCalendar`'s precedent. Every field it carries is what that
    function got from the calendar, so `entry_day == sessions[0]`, `exit_day == sessions[-1]`
    and `session_count == horizon.sessions` hold by construction rather than by check.
    """

    prediction_day: date
    entry_day: date
    exit_day: date
    sessions: tuple[date, ...]
    horizon: ResearchHorizon
    exchange: str
    zone: tzinfo

    @property
    def session_count(self) -> int:
        """How many session returns the window spans; one fewer than `sessions` holds."""
        return len(self.sessions) - 1

    def close_instant(self, day: date) -> datetime:
        """`day`'s 15:00 close, as an aware instant on this window's own timezone.

        The bridge to the instant-shaped contracts above `domain/`: `OutcomeObservation` takes
        two `datetime`s, and pinning them to the session close in the zone the window was dated
        in keeps a stored validation result readable as "close to close" rather than as two
        arbitrary timestamps.
        """
        if day not in self.sessions:
            raise LabelError(
                f"{day.isoformat()} is not one of this window's sessions "
                f"({self.entry_day.isoformat()}..{self.exit_day.isoformat()}); an instant "
                "outside the window would date a price the label never read"
            )
        return datetime.combine(day, SESSION_CLOSE_TIME, tzinfo=self.zone)


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowReturn:
    """The three ways to compute a multi-session return, side by side.

    The window-level counterpart of `SessionReturns`, and shaped after it deliberately:
    `unadjusted` is carried so the size of the error is visible when a reader compares them,
    never so it is used, and there is no method here called "the return" because both
    `published` and `adjusted` are correct. `OutcomeLabel.realized_return` picks one, and says
    why it picks that one.
    """

    ts_code: str
    entry_day: date
    exit_day: date
    published: float
    """`prod(close_t / pre_close_t) - 1` over the window's sessions. No factor is read."""
    adjusted: float
    """`(close_exit * f_exit) / (close_entry * f_entry) - 1`. No `pre_close` is read."""
    unadjusted: float
    """`close_exit / close_entry - 1`. **Wrong** across any ex-rights day. Never used."""
    entry_adjusted_close: float
    """`close_entry * f_entry`: the entry price on the 后复权 scale."""
    exit_adjusted_close: float
    """`close_exit * f_exit`. `exit / entry - 1` is exactly `adjusted`, which is what makes
    `backtest.validation.observation_from_label`'s two prices provably one path."""
    per_session: tuple[SessionReturns, ...]
    """One entry per session return, each already cross-checked by `session_returns`."""

    @property
    def disagreement(self) -> float:
        """How far the two correct paths came out apart, as a relative gap in gross return."""
        return abs((1.0 + self.published) / (1.0 + self.adjusted) - 1.0)

    @property
    def tolerance(self) -> float:
        """The gap this window's own rows allow, compounded from the per-session bounds.

        Each session's `SessionReturns.tolerance` is a number of yuan of `pre_close`; dividing
        by that session's published `pre_close` puts it in return space, and the chain's bound
        is the product. Reported rather than enforced: `session_returns` has already refused
        any session whose own term was exceeded, so this product cannot be exceeded once the
        window was built at all. See this module's docstring.
        """
        bound = 1.0
        for entry in self.per_session:
            bound *= 1.0 + entry.tolerance / entry.published_pre_close
        return bound - 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class OutcomeLabel:
    """One security's forward outcome over one window: the number, or why there is none.

    `window_return` is `None` exactly when `refusals` is non-empty, so the two cannot disagree
    about whether this label carries anything.
    """

    ts_code: str
    window: LabelWindow
    refusals: tuple[LabelRefusal, ...]
    entry_touch: LimitTouch | None
    exit_touch: LimitTouch | None
    window_return: WindowReturn | None

    @property
    def is_labelled(self) -> bool:
        """Whether this window produced a number."""
        return not self.refusals

    @property
    def refusal_summary(self) -> str:
        """One line naming this label's security, window, and every refusal against it.

        Stated once here rather than at each raise site: `realized_return` and
        `backtest.validation.observation_from_label` both have to explain the same absence, and
        two hand-written copies of it are two messages that drift.
        """
        return (
            f"{self.ts_code} over {self.window.entry_day.isoformat()}.."
            f"{self.window.exit_day.isoformat()} carries {len(self.refusals)} refusal(s): "
            + "; ".join(f"{item.code} on {item.day.isoformat()}" for item in self.refusals)
        )

    @property
    def realized_return(self) -> float:
        """The label: the adjusted return over the window, or `LabelError`.

        The factor path rather than the published one, because it is reproducible from two
        prices on one scale -- see this module's docstring. Raises rather than returning `0.0`
        or `None` for a refused window: a zero is a return, and a supervised target that
        silently reads zero for every halted name teaches the model that halts are flat.
        """
        if self.window_return is None:
            raise LabelError(f"{self.refusal_summary}, and therefore no return")
        return self.window_return.adjusted


def build_label_window(
    *,
    as_of: datetime,
    zone: tzinfo,
    horizon: ResearchHorizon,
    calendar: TradingCalendar,
) -> LabelWindow:
    """Turn a signal's instant and horizon into the sessions its outcome is measured over.

    `zone` has no default on purpose. An instant is not a session date until a timezone says
    so, and 23:00 UTC on the 11th is the 12th in Asia/Shanghai -- so a UTC-dated label enters
    one session early on every signal produced after 16:00 local. This repository already
    records its answer once, as `panel/catalog.py`'s `DEFAULT_DATE_TIMEZONE`; a second default
    here would be a second place for it to drift.

    Propagates `CalendarHorizonError` when the entry or the exit would fall outside the
    published calendar, and `HorizonError` when the horizon is not countable in sessions.
    Neither is repaired: a window that cannot be built is not a window that is empty.
    """
    prediction_day = ensure_aware(as_of).astimezone(zone).date()
    entry_day = calendar.next_trading_day(prediction_day)
    exit_day = calendar.shift(entry_day, horizon.sessions)
    return LabelWindow(
        prediction_day=prediction_day,
        entry_day=entry_day,
        exit_day=exit_day,
        sessions=calendar.trading_days_between(entry_day, exit_day),
        horizon=horizon,
        exchange=calendar.exchange,
        zone=zone,
    )


def window_return(
    window: LabelWindow,
    *,
    ts_code: str,
    bars: Mapping[date, DailyBar],
    factors: AdjustmentHistory,
) -> WindowReturn:
    """The window's return on both correct paths, with the wrong one carried beside them.

    Runs `session_returns` on every adjacent pair of sessions, so each link is cross-checked
    against the tolerance that module calibrated on 37,602 real rows, and propagates its
    `PriceDataError` when the `adj_factor` series and the published `pre_close` disagree about
    a session. That refusal is the point: a factor partition with a hole in it produces a
    return that is wrong by percentage points with the sign reversed, and this is where it is
    caught rather than where it is used.

    Refuses a bar for another security, a bar filed under the wrong session, and a session with
    no bar at all -- the first two produce a plausible number from the wrong rows, and the third
    would have to be papered over by skipping a link in the chain.
    """
    for day in window.sessions:
        bar = bars.get(day)
        if bar is None:
            raise LabelError(
                f"{ts_code} has no bar on {day.isoformat()}, a session of the window "
                f"{window.entry_day.isoformat()}..{window.exit_day.isoformat()}; the published "
                "return path chains one quotient per session and skipping a link would compare "
                "a close against a pre_close from another session"
            )
        if bar.ts_code != ts_code:
            raise LabelError(
                f"the bar filed under {day.isoformat()} is {bar.ts_code}'s and this label is "
                f"{ts_code}'s; one security's price against another's is a plausible number "
                "from the wrong scale"
            )
        if bar.trade_date != day:
            raise LabelError(
                f"{ts_code}'s bar is dated {bar.trade_date.isoformat()} and is filed under "
                f"{day.isoformat()}; the factor and the previous close are both resolved for "
                "the session it is filed under"
            )

    per_session: list[SessionReturns] = []
    gross_published = 1.0
    for previous_day, day in zip(window.sessions, window.sessions[1:], strict=False):
        computed = session_returns(
            bars[day],
            previous_close=bars[previous_day].close,
            previous_day=previous_day,
            factors=factors,
        )
        per_session.append(computed)
        gross_published *= 1.0 + computed.published

    entry_bar = bars[window.entry_day]
    exit_bar = bars[window.exit_day]
    return WindowReturn(
        ts_code=ts_code,
        entry_day=window.entry_day,
        exit_day=window.exit_day,
        published=gross_published - 1.0,
        adjusted=factors.adjusted_return(
            start=window.entry_day,
            end=window.exit_day,
            start_price=entry_bar.close,
            end_price=exit_bar.close,
        ),
        unadjusted=exit_bar.close / entry_bar.close - 1.0,
        entry_adjusted_close=entry_bar.close * factors.factor_on(window.entry_day),
        exit_adjusted_close=exit_bar.close * factors.factor_on(window.exit_day),
        per_session=tuple(per_session),
    )


def label_outcome(
    window: LabelWindow,
    *,
    ts_code: str,
    bars: Mapping[date, DailyBar],
    factors: AdjustmentHistory,
    limits: Mapping[date, PriceLimit],
    halts: Mapping[date, SuspensionDay],
    universe: StockUniverse,
) -> OutcomeLabel:
    """Label one security over one window, or name every reason it cannot be labelled.

    `halts` is keyed only by the sessions that carry `suspend_d` rows, which is the shape
    `suspensions_from_panel_rows` returns and the shape the data has -- 5,312 of 2024-06-28's
    5,338 priced names have no row at all -- so a session absent from it means nothing happened,
    not that the halt corpus was never read.

    `limits` is keyed by session for this one security; an absent entry means the exchange
    published no band, which is a real shape before 2023 and refuses rather than being read as
    "not at a limit". None of the four mappings has a default: a waiver that is a default is an
    accident, which is `panel_ingest.write_daily_panel(halts=...)`'s own argument.

    Every refusal is collected rather than short-circuited, so a caller sees all of what is
    wrong with a window instead of the first thing to be checked.
    """
    refusals: list[LabelRefusal] = [
        refusal
        for day in window.sessions
        for refusal in _session_refusals(
            day, ts_code=ts_code, bars=bars, halts=halts, universe=universe
        )
    ]
    touches: dict[date, LimitTouch] = {}
    for day in (window.entry_day, window.exit_day):
        bar = bars.get(day)
        limit = limits.get(day)
        if limit is None:
            refusals.append(
                LabelRefusal(
                    code=REFUSAL_UNPUBLISHED_BAND,
                    day=day,
                    detail=(
                        f"the exchange published no price band for {ts_code} on "
                        f"{day.isoformat()}, so whether the session could be traded is unknown "
                        "rather than unrestricted; stk_limit serves no rows at all before "
                        "2007-01-04 and reached the Beijing board a decade after daily did"
                    ),
                )
            )
            continue
        if bar is None:
            continue
        touch = limit_touch(bar, limit)
        touches[day] = touch
        if touch.one_price_up or touch.one_price_down:
            side = "upper" if touch.one_price_up else "lower"
            refusals.append(
                LabelRefusal(
                    code=REFUSAL_LOCKED_AT_LIMIT,
                    day=day,
                    detail=(
                        f"{ts_code} spent the whole of {day.isoformat()} at its {side} limit "
                        f"(high {bar.high!r}, low {bar.low!r} against a band of "
                        f"{limit.down_limit!r}..{limit.up_limit!r}), so there was no "
                        "counterparty on the other side and the position this label assumes "
                        "could not have been opened or closed at that session's price"
                    ),
                )
            )

    ordered = tuple(sorted(refusals, key=lambda item: (item.day, item.code)))
    return OutcomeLabel(
        ts_code=ts_code,
        window=window,
        refusals=ordered,
        entry_touch=touches.get(window.entry_day),
        exit_touch=touches.get(window.exit_day),
        window_return=(
            None if ordered else window_return(window, ts_code=ts_code, bars=bars, factors=factors)
        ),
    )


_LISTING_REFUSALS: Final[Mapping[ListingStatus, str]] = {
    ListingStatus.delisted: REFUSAL_DELISTED,
    ListingStatus.not_yet_listed: REFUSAL_NOT_YET_LISTED,
    ListingStatus.beyond_snapshot: REFUSAL_BEYOND_REGISTRY_SNAPSHOT,
}
"""The three verdicts other than `listed`, each with the code it produces.

A mapping rather than a chain of `if`s so the four-valued enum and the refusal vocabulary are
one table: a member added to `ListingStatus` would fall through to a `KeyError` here instead of
being silently treated as tradeable.
"""


def _session_refusals(
    day: date,
    *,
    ts_code: str,
    bars: Mapping[date, DailyBar],
    halts: Mapping[date, SuspensionDay],
    universe: StockUniverse,
) -> tuple[LabelRefusal, ...]:
    """Everything wrong with one session of a window, from the registry, the halts and the
    bars.

    The three are independent and all three are reported: a delisted name has no bar *and* is
    delisted, and collapsing that into one finding is how a survivorship hole becomes
    indistinguishable from a short fetch.
    """
    found: list[LabelRefusal] = []
    status = universe.status_on(ts_code, day)
    if status is not ListingStatus.listed:
        found.append(
            LabelRefusal(
                code=_LISTING_REFUSALS[status],
                day=day,
                detail=(
                    f"the registry reports {ts_code} {status.value} on {day.isoformat()}, a "
                    "session inside the return window; a return measured across it would be "
                    "priced from rows the registry does not stand behind"
                ),
            )
        )
    suspensions = halts.get(day)
    if suspensions is not None and suspensions.state_of(ts_code) is TradingState.halted:
        found.append(
            LabelRefusal(
                code=REFUSAL_HALTED_SESSION,
                day=day,
                detail=(
                    f"{ts_code} was halted for the whole of {day.isoformat()} (an untimed S "
                    "row), so that session has no price. A timed S and an R both traded and "
                    "neither refuses"
                ),
            )
        )
    if day not in bars:
        found.append(
            LabelRefusal(
                code=REFUSAL_MISSING_BAR,
                day=day,
                detail=(
                    f"{ts_code} has no daily bar on {day.isoformat()}. Reported separately "
                    "from any halt that may explain it: a missing bar with a halt behind it "
                    "and a missing bar with nothing behind it are different findings, and a "
                    "short fetch looks exactly like the second"
                ),
            )
        )
    return tuple(found)
