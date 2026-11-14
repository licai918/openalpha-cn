"""The limit-event rule (`V2-P7-011`): who closed at its limit, and for how many sessions.

`domain/price_limits.py` answers *where one bar sat against its own published band* -- one
security, one session, four booleans. This module is the session-over-session reading of the
same facts: an event, its board count, and the two shapes that make the count honest.

## The three events, and what each records

- `limit_up` -- closed at the published upper limit, with no closed-at-limit session behind it.
  `board_count` is 1.
- `consecutive_board` -- closed at the published upper limit with a streak behind it.
  `board_count` is `streak + 1`.
- `broken_board` -- *touched* the upper limit and closed below it. `board_count` is the streak
  the break ended, so a `broken_board` with `board_count=3` reads "touched on the 4th session
  and did not hold". Its facts carry `prior_board_count` for the same number, because the
  evidence vocabulary names the session after the streak rather than the streak itself.

Nothing is emitted for an ordinary session, and nothing is emitted for a limit-free session: a
security in its first five sessions has a published sentinel band, no real bar reaches it, and
`limit_touch` already reports the four booleans as `False`.

## A halt is skipped, not counted (D2)

The exchange publishes a band for a security that did not trade, so `stk_limit` carries the
session while `daily` does not. That session is **not** a break -- the security did not close
below its limit, it did not trade -- and it is **not** a board either. The walk steps over it,
`halted_sessions_skipped` counts it, and the next limit-up continues the streak. A security
`stk_limit` does not carry at all is a different thing: not listed, so its history has ended
and the walk stops rather than reading the missing band as a halt. `SessionLimitState.banded`
is the distinction: it holds every security with a published band row, sentinel or real, and
the absence of that row is what ends the walk.

## The lookback cap

`MAX_BOARD_LOOKBACK_SESSIONS` bounds one session's walk, because the caller reads a panel:
sixty stored sessions is already more than any live streak (the registry's longest is far
shorter), and reading a security's whole history to label one session would make the producer
per-security rather than per-session. A streak still alive at the cap cannot say how long it
really is, so the event carries `board_count_is_lower_bound=True` instead of a number that
looks exact. This is the same "report the bound" rule `WindowReturn.tolerance` follows.

## Layering

Pure `dataclasses`/`datetime` plus three sibling `domain` modules. No panel read, no store, no
clock, no numeric stack -- the panel adapter (`panel_limit_events.py`) does the reading and the
evidence plane (`evidence/limit_events.py`) does the building.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Final, Literal

from pydantic import JsonValue

from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.price_limits import LimitTouch, PriceLimit, limit_touch

LimitEventKind = Literal["limit_up", "consecutive_board", "broken_board"]

MAX_BOARD_LOOKBACK_SESSIONS: Final[int] = 60
"""How many earlier sessions one walk may read -- see this module's docstring."""

_BROKEN_BOARD: Final[LimitEventKind] = "broken_board"

MARKET_EVENT_WEIGHTS: Final[Mapping[str, float]] = MappingProxyType(
    {
        "limit_up": 0.65,
        "consecutive_board": 0.85,
        "broken_board": -0.65,
    }
)
"""What one limit event scores a name, as the deterministic `MarketAgent` already weighs it.

The table lives here, beside the three events it weights, because two readers now score the same
events: `agents/baseline.py::MarketAgent` sums these for its signal strength, and the plan-A entry
veto (`domain/survivor_veto.py`, `V2-P7-027`) means them by the same numbers. R-A2 of
`docs/superpowers/plans/2026-11-14-p7-plans-a-b.md` is explicit that the veto **is the existing
agent's rule** -- so the numbers are one shared constant and not a second copy that could drift.
The key set is the closed `LimitEventKind` vocabulary, which the module's tests pin.

An unknown kind reads as 0.0 through `.get` exactly as the agent's private table did before the
move: `evidence` payloads carry a free-form `kind`, so the lookup must stay total.
"""

MARKET_BEARISH_THRESHOLD: Final[float] = -0.15
"""The strength below which the market reading is *bearish*, and the veto's mean-weight line.

`MarketAgent` calls a strength above `-MARKET_BEARISH_THRESHOLD` bullish, below this value
bearish, and anything between neutral, so its signal is unchanged by the move; the entry veto
(`V2-P7-027`) vetoes a name whose events' mean weight over five sessions is **below** this line.
Shared for the same reason `MARKET_EVENT_WEIGHTS` is: the plan fixes one rule, not two.
"""


class LimitEventError(ValueError):
    """Raised when a state or an event would be assembled from two different sessions."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionLimitState:
    """One session's touches and the securities it published a band for, joined by security.

    `touches` holds the securities with **both** a bar and a band, so a key missing from it is
    a security that did not trade -- the halt D2 steps over. `banded` holds every security the
    session's `stk_limit` carries, sentinel or real, so a key missing from *that* is a security
    with no band and therefore no history to walk.
    """

    trade_date: date
    touches: Mapping[str, LimitTouch]
    banded: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class BoardStreak:
    """How many consecutive closed-at-limit sessions a walk found, and how far it looked.

    `count` is the number of consecutive `closed_at_up` sessions immediately before the target,
    reaching back across halts. `halted_sessions_skipped` is how many halts the streak was
    actually carried across: a halt below the first `closed_at_up` it reaches bridged nothing
    and is not counted (fix round 1, finding 6). `truncated` says the walk stopped at the
    lookback cap while the streak was still alive, so a caller must not report `count` as exact.
    """

    count: int
    halted_sessions_skipped: int
    truncated: bool

    def __post_init__(self) -> None:
        if self.count < 0:
            raise LimitEventError(f"count cannot be negative; got {self.count}")
        if self.halted_sessions_skipped < 0:
            raise LimitEventError(
                f"halted_sessions_skipped cannot be negative; got {self.halted_sessions_skipped}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class LimitEvent:
    """One security's limit event on one session, plus the streak that gives it its board count.

    `board_count` is the number of consecutive closed-at-limit sessions the event reports: 1 for
    a fresh `limit_up`, the full streak for a `consecutive_board`, and the streak the break ended
    for a `broken_board`. `board_count_is_lower_bound` is `True` exactly when the walk stopped at
    `MAX_BOARD_LOOKBACK_SESSIONS` while the streak was still alive.
    """

    ts_code: str
    trade_date: date
    kind: LimitEventKind
    board_count: int
    one_price: bool
    close: float
    high: float
    pct_chg: float
    halted_sessions_skipped: int
    board_count_is_lower_bound: bool

    def facts(self) -> dict[str, JsonValue]:
        """The evidence payload's `facts`, in the vocabulary `evidence/builder.py` normalizes.

        `close`/`high`/`pct_change` are the session's own numbers; `board_count` is the count
        this event claims. A `broken_board` adds the three keys `_BrokenBoardFacts` reads:
        `open_count` is **1 because a daily bar cannot count intraday openings** -- it is a lower
        bound and says so (D3) -- and `prior_board_count` is the streak the break ended.
        """
        facts: dict[str, JsonValue] = {
            "close": self.close,
            "high": self.high,
            "pct_change": self.pct_chg,
            "board_count": self.board_count,
            "one_price": self.one_price,
            "halted_sessions_skipped": self.halted_sessions_skipped,
            "board_count_is_lower_bound": self.board_count_is_lower_bound,
        }
        if self.kind == _BROKEN_BOARD:
            facts["open_count"] = 1
            facts["open_count_is_lower_bound"] = True
            facts["prior_board_count"] = self.board_count
        return facts

    def summary(self) -> str:
        """One line naming the security, the session and the board count it reports."""
        day = self.trade_date.isoformat()
        if self.kind == "limit_up":
            price = ", one price" if self.one_price else ""
            return f"{self.ts_code} closed at its limit on {day} (board 1{price})"
        if self.kind == "consecutive_board":
            price = ", one price" if self.one_price else ""
            return (
                f"{self.ts_code} closed at its limit on {day} for "
                f"{self.board_count} consecutive sessions{price}"
            )
        return (
            f"{self.ts_code} touched its limit on {day} and closed below it, "
            f"breaking a {self.board_count}-session streak"
        )


def session_state(
    bars: Mapping[str, DailyBar],
    limits: Mapping[str, PriceLimit],
    *,
    trade_date: date,
) -> SessionLimitState:
    """Join one session's bars to its published bands, refusing rows from another session.

    A bar with no band and a band with no bar are both legitimate and both kept: the first is a
    security `stk_limit` does not carry (no history to walk), the second is a halt (skipped).
    Only a row dated any other day is refused, because joining it would produce a `LimitTouch`
    from two sessions' numbers -- the failure `limit_touch` already refuses pair by pair, lifted
    here to the whole cross section.
    """
    for ts_code, bar in bars.items():
        if bar.trade_date != trade_date:
            raise LimitEventError(
                f"{ts_code}'s bar is dated {bar.trade_date.isoformat()} and this state is for "
                f"{trade_date.isoformat()}"
            )
    for ts_code, limit in limits.items():
        if limit.trade_date != trade_date:
            raise LimitEventError(
                f"{ts_code}'s band is dated {limit.trade_date.isoformat()} and this state is "
                f"for {trade_date.isoformat()}"
            )
    touches = {
        ts_code: limit_touch(bars[ts_code], limit)
        for ts_code, limit in limits.items()
        if ts_code in bars
    }
    return SessionLimitState(
        trade_date=trade_date,
        touches=MappingProxyType(touches),
        banded=frozenset(limits),
    )


def board_streak(
    ts_code: str,
    earlier: Sequence[SessionLimitState],
    *,
    max_lookback: int = MAX_BOARD_LOOKBACK_SESSIONS,
) -> BoardStreak:
    """Walk `earlier` (newest first) and count consecutive closed-at-limit sessions.

    The walk ends at the first of: a session with no band for this security (not listed), a
    session where it traded and did not close at its limit, or the lookback cap. A halted
    session (band published, no bar) is stepped over; it is **counted only if a later
    `closed_at_up` session carries the streak across it** (fix round 1, finding 6) -- a halt
    immediately below a break bridged nothing, and the event must not claim one was skipped
    inside a streak that never crossed it.
    """
    if max_lookback < 0:
        raise LimitEventError(f"max_lookback cannot be negative; got {max_lookback}")
    if max_lookback == 0:
        return BoardStreak(count=0, halted_sessions_skipped=0, truncated=len(earlier) > 0)
    count = 0
    skipped = 0
    pending_halts = 0
    examined = 0
    alive = True
    for state in earlier:
        if examined >= max_lookback:
            break
        examined += 1
        if ts_code not in state.banded:
            alive = False
            break
        touch = state.touches.get(ts_code)
        if touch is None:
            pending_halts += 1
            continue
        if not touch.closed_at_up:
            # The pending halts sit below a break; the streak never reached them.
            alive = False
            break
        count += 1
        skipped += pending_halts
        pending_halts = 0
    return BoardStreak(
        count=count,
        halted_sessions_skipped=skipped,
        truncated=alive and examined >= max_lookback,
    )


def session_limit_events(
    today: SessionLimitState,
    bars: Mapping[str, DailyBar],
    earlier: Sequence[SessionLimitState],
    *,
    max_lookback: int = MAX_BOARD_LOOKBACK_SESSIONS,
    truncated_alive: frozenset[str] = frozenset(),
) -> tuple[LimitEvent, ...]:
    """Every limit event `today` carries, ordered by `ts_code`.

    `today` is the session's `SessionLimitState`; `bars` is the same session's bars (the state
    holds touches, which do not carry the prices an event reports); `earlier` is the prior
    sessions newest-first. Only securities with a touch today can produce an event, so the
    result is bounded by the day's events rather than by the cross section.

    `truncated_alive` names the candidates whose walk ended at the **edge of the stored panel**
    while their streak was still alive (fix round 1, finding 3) -- the panel adapter reports it
    when a prior year's calendar or prices cannot be read. Their count is a lower bound for the
    same reason the lookback cap's is, and the session's other events are still produced.
    """
    events: list[LimitEvent] = []
    for ts_code in sorted(today.touches):
        touch = today.touches[ts_code]
        if not (touch.closed_at_up or touch.at_up):
            continue
        bar = bars.get(ts_code)
        if bar is None:
            raise LimitEventError(
                f"{ts_code} has a touch on {today.trade_date.isoformat()} but no bar was given "
                "for it; the state and the bars come from different reads"
            )
        streak = board_streak(ts_code, earlier, max_lookback=max_lookback)
        if touch.closed_at_up:
            board_count = streak.count + 1
            kind: LimitEventKind = "limit_up" if streak.count == 0 else "consecutive_board"
        else:
            board_count = streak.count
            kind = _BROKEN_BOARD
        events.append(
            LimitEvent(
                ts_code=ts_code,
                trade_date=today.trade_date,
                kind=kind,
                board_count=board_count,
                one_price=touch.one_price_up,
                close=bar.close,
                high=bar.high,
                pct_chg=bar.pct_chg,
                halted_sessions_skipped=streak.halted_sessions_skipped,
                board_count_is_lower_bound=streak.truncated or ts_code in truncated_alive,
            )
        )
    return tuple(events)
