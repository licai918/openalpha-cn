"""The research horizon (`V2-P1-017`) -- a countable span, not a free string.

`SignalFrame.horizon` was `Field(min_length=1, max_length=64)`: any 1-64 characters, so
`"5d"`, `"五天"` and `"whenever"` were equally legal. Nothing downstream could turn any of them
into a return window, which is why the return window and the horizon had no relationship at
all -- `backtest/validation.py`'s `OutcomeObservation` took two datetimes and checked only that
the second followed the first.

## The grammar, and what it deliberately does *not* do to a stored value

`HORIZON_PATTERN` is `<count><unit>`: a one-to-three digit count with no leading zero, and one
of four unit letters. It is attached to the field as `Field(pattern=...)` rather than as a
`field_validator` so it reaches `docs/api/schemas/signal-frame-v1.json` and is part of the
published contract rather than a rule only Python enforces.

**It constrains and never normalises**, and that distinction is load-bearing.
`SignalFrame.signal_id` is `sha256` over the model's canonical JSON, so a validator that
lower-cased a unit or stripped a leading zero would silently move the identity of every stored
signal that it touched. Restricting a field's *domain* moves no ID at all: every value that was
already well formed serialises to exactly the bytes it did before. All three horizon literals
this repository writes (`5d`, `10d`, `3m`) are inside the grammar, so no stored `signal_id`
changes and no migration is needed. What the change does remove is the ability to *construct*
a signal whose horizon nothing can count -- which is the point.

## Why only one of the four units is countable in sessions

`HorizonUnit.trading_days` maps to sessions one-for-one, by definition rather than by
measurement: `d` counts **open sessions**, so a `5d` window is the five sessions the exchange
calendar reports after the entry session. The alternative reading -- `d` as calendar days --
was rejected because it lands the window's endpoints on days the exchange was shut, and every
rule for moving them off a holiday (forward, backward, nearest) is an invented convention of
exactly the kind `V2-P1-017` exists to replace. Counting sessions needs no such rule:
`TradingCalendar.shift` is already defined only on open sessions and refuses an anchor that is
not one.

The other three units are legal horizons for a *signal* -- a week, a month and a year are how
research states its intent, and `3m` is stored in this repository today -- and they are
**not** convertible. A calendar span is not a fixed number of sessions -- a month holding the
Spring Festival recess is far shorter than an ordinary one -- and, worse, the count of a
*future* one is not knowable at all: `domain/trading_calendar.py`'s `KNOWN_CALENDAR_LOOKAHEAD`
carries a 2020 amendment that closed a session already published as open. So
`ResearchHorizon.sessions` refuses rather than multiplying by a sessions-per-unit constant
nobody measured, and a caller who wants a label window from a calendar horizon has to state the
session count itself.

## Layering

A module of its own, importing nothing but `re`, `dataclasses` and `enum`, because two
otherwise unrelated modules need it: `domain/signal.py` needs the pattern for its field, and
`domain/labels.py` needs the parsed value to size a return window. Putting the grammar in
`labels.py` would make `SignalFrame` -- a slim contract with three imports -- reach the whole
price panel (`daily_prices`, `adjustment`, `price_limits`, `stock_universe`) just to declare
one field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final


class HorizonError(ValueError):
    """Raised for a malformed research horizon, or a question one cannot answer.

    A `ValueError` subclass to match `domain/trading_calendar.py`'s `TradingCalendarError` and
    `domain/adjustment.py`'s `AdjustmentError`.
    """


class HorizonUnit(Enum):
    """The four spans a research horizon is written in. One of them counts sessions.

    Not a `bool` question and not a verdict, so unlike `CalendarDayStatus` and `TradingState`
    this enum does not override `__bool__`: every member is a unit, none of them is an "off"
    value, and `if unit:` has no tempting wrong reading to guard against.
    """

    trading_days = "d"
    """Open sessions, counted on the exchange calendar. The only countable unit."""

    weeks = "w"
    months = "m"
    years = "y"
    """Calendar spans. Legal on a signal, not convertible into a session count."""


MAX_HORIZON_COUNT: Final[int] = 999
"""The largest count `HORIZON_PATTERN`'s three digits admit, as a number rather than a regex.

`ResearchHorizon.__post_init__` needs the bound arithmetically and the field needs it as a
pattern, so the two are stated once each and
`tests/unit/domain/test_horizon.py::test_the_direct_constructor_and_the_grammar_admit_the_same_counts`
walks the boundary through both to keep them from drifting apart.
"""

HORIZON_PATTERN: Final[str] = (
    r"^[1-9][0-9]{0,2}[" + "".join(unit.value for unit in HorizonUnit) + r"]$"
)
"""The grammar `SignalFrame.horizon` is constrained to: `<1..999><d|w|m|y>`.

Built from `HorizonUnit`'s own members rather than restating the four letters, so the regex and
the enum cannot drift into disagreeing about which units exist. The count excludes zero and
leading zeroes because `0d` is not a window and `05d` is a second spelling of `5d` -- and a
second spelling is a second `signal_id` for one horizon.
"""

_HORIZON = re.compile(HORIZON_PATTERN)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchHorizon:
    """A parsed research horizon: how many of which unit.

    **Not a plain carrier**, unlike most of the dataclasses in `domain/`, because this one has
    a second constructor that is not `parse_horizon`: a caller can write
    `ResearchHorizon(count=..., unit=...)` directly and skip the grammar entirely. Every value
    the grammar admits satisfies these checks, so `parse_horizon` is unaffected; what they
    close is the direct path. Left open, `count=0` built a window collapsed onto a single
    session and failed several frames later inside `domain/adjustment.py` with an
    `AdjustmentError`, and `count=-3` failed inside `domain/trading_calendar.py` with a
    `TradingCalendarError` -- both fail-closed, but both answering for a malformed horizon in a
    sibling module's vocabulary, which is the wrong module's error to catch.
    """

    count: int
    unit: HorizonUnit

    def __post_init__(self) -> None:
        if not isinstance(self.unit, HorizonUnit):
            raise HorizonError(
                f"{self.unit!r} is not a HorizonUnit; the four units are "
                f"{[unit.value for unit in HorizonUnit]}"
            )
        if type(self.count) is not int or not 1 <= self.count <= MAX_HORIZON_COUNT:
            raise HorizonError(
                f"{self.count!r} is not a horizon count; the grammar admits 1..."
                f"{MAX_HORIZON_COUNT} ({HORIZON_PATTERN}), and a count of zero is not a "
                "window while a negative one runs backwards from the entry session"
            )

    @property
    def text(self) -> str:
        """The canonical spelling, which is the string this was parsed from.

        `parse_horizon(h.text) == h` for every value the grammar admits, which is what makes it
        safe to write back into a `SignalFrame.horizon` field without moving its `signal_id`.
        """
        return f"{self.count}{self.unit.value}"

    @property
    def sessions(self) -> int:
        """How many open sessions this horizon spans, or `HorizonError`.

        Defined for `HorizonUnit.trading_days` alone; see this module's docstring for why a
        calendar unit refuses instead of converting.
        """
        if self.unit is not HorizonUnit.trading_days:
            raise HorizonError(
                f"{self.text!r} is not a whole number of trading sessions: a "
                f"{self.unit.name[:-1]} is a calendar span and the number of sessions in one "
                "varies -- a stretch holding the Spring Festival recess is far shorter than an "
                "ordinary one -- while a future one's count is not knowable at all, because the "
                "schedule is amended mid-year (trading_calendar.KNOWN_CALENDAR_LOOKAHEAD "
                "carries a 2020 amendment that closed a session already published as open). "
                "Converting it would need a sessions-per-unit constant this repository has not "
                "measured; state the session count directly, as a horizon in "
                f"{HorizonUnit.trading_days.value!r}"
            )
        return self.count


def parse_horizon(text: str) -> ResearchHorizon:
    """Parse a `SignalFrame.horizon` string, or refuse it by name.

    The counterpart of the `pattern` on the field itself: the field refuses a malformed value at
    construction, and this turns an accepted one into something a window can be sized from.
    Both read `HORIZON_PATTERN`, so a value that constructs always parses.
    """
    if type(text) is not str or _HORIZON.fullmatch(text) is None:
        raise HorizonError(
            f"{text!r} is not a research horizon; the grammar is a count of 1..999 with no "
            f"leading zero followed by one of {[unit.value for unit in HorizonUnit]} "
            f"({HORIZON_PATTERN}), so '5d' is five trading sessions and '3m' is three months"
        )
    return ResearchHorizon(count=int(text[:-1]), unit=HorizonUnit(text[-1]))
