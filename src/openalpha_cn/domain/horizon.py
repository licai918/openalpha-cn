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
from functools import total_ordering
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

COUNTABLE_HORIZON_PATTERN: Final[str] = (
    r"^[1-9][0-9]{0,2}[" + HorizonUnit.trading_days.value + r"]$"
)
"""`HORIZON_PATTERN` restricted to the one unit a session count exists for (`V2-P4-001`).

PRD D36 asks for `SignalFrame.horizon` to be "规范化为可比较枚举" -- a comparable enumeration
-- and this is the only spelling of that which invents nothing. Comparing two horizons needs
one measure they are both in, and this module's own docstring records why three of the four
units have none: a calendar span holds a variable number of sessions, a *future* one's count
is not knowable at all, and `ResearchHorizon.sessions` therefore refuses rather than
multiplying by a constant nobody measured. A total order over the full four-unit grammar
would have to invent exactly that constant. A total order over this restriction is just
integer order on the count, and `ResearchHorizon` is `@total_ordering` below because of it.

**This narrows a domain and changes no representation, which is why it moves no identity.**
Roadmap section 8 measured that distinction directly, on this very field: `V2-P1-017`
replaced `min_length/max_length` with `HORIZON_PATTERN` and every accepted value serialised
to the bytes it always had, so not one `signal_id` moved. The same holds here -- `5d` and
`10d`, the only two horizons anything in this repository writes, are inside this pattern and
their canonical JSON is unchanged. Replacing the field's *type* with a structured value
would have been the other thing entirely: section 8 names it, and
`tests/unit/domain/test_contract_identity.py::test_narrowing_the_signal_horizon_moved_no_stored_signal_id`
measures that it did not happen.

`HORIZON_PATTERN` itself is untouched and stays four-unit: `parse_horizon` is the grammar for
a *label window* (`domain/labels.py`, `factor_view.py`, `openalpha factor run --horizon`),
where a caller may legitimately state an intent this module then refuses to count. What
narrows is what a `SignalFrame` -- the record that gets scored, ranked and validated -- is
allowed to carry, because a signal whose horizon nothing can turn into a return window is a
signal nothing can ever score.
"""

_COUNTABLE_HORIZON = re.compile(COUNTABLE_HORIZON_PATTERN)


@total_ordering
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

    def __lt__(self, other: ResearchHorizon) -> bool:
        """Order two horizons by the sessions they span, or refuse by name.

        The comparison PRD D36's "可比较" asks for, and it is deliberately built on
        `sessions` rather than on `(unit, count)`. A lexicographic order over the four units
        would rank `999d` below `1w` -- 999 sessions is roughly four years and a week is
        five -- so it would be a total order that is wrong rather than a partial one that is
        honest. Reusing `sessions` means the refusal for a calendar unit is stated once, in
        the property that measured it, and a comparison involving one raises `HorizonError`
        with that property's full explanation instead of a bare `TypeError`.

        `@total_ordering` derives `>`, `<=` and `>=` from this and the dataclass's `__eq__`.
        Equality is **not** routed through `sessions`: two horizons are equal when they are
        the same count of the same unit, which is defined for all four units and is what
        `parse_horizon(h.text) == h` relies on. So `1w == 5d` is `False` (they are different
        horizons) while `1w < 5d` refuses (nothing here knows which is longer) -- the two
        answers are about different questions and neither is guessed.

        `COUNTABLE_HORIZON_PATTERN` is what keeps this from being a trap in practice: every
        horizon a `SignalFrame` may carry is a trading-day horizon, so a list of them sorts.
        """
        if not isinstance(other, ResearchHorizon):
            return NotImplemented
        return self.sessions < other.sessions


def is_countable_horizon(text: object) -> bool:
    """Whether `text` is a horizon `SignalFrame` still admits at `V2-P4-001` (see the pattern).

    Reads `COUNTABLE_HORIZON_PATTERN`, the same string the field's `pattern` is built from, so
    a caller asking this question and pydantic answering it cannot disagree. Its one caller
    outside the tests is `storage/migrations.py`'s identity rewrite, which has to say
    *which* stored run carries a horizon this build no longer accepts before pydantic refuses
    the row with a message about a regex.
    """
    return type(text) is str and _COUNTABLE_HORIZON.fullmatch(text) is not None


def parse_horizon(text: str) -> ResearchHorizon:
    """Parse a research horizon string, or refuse it by name.

    The counterpart of the `pattern` on a horizon-carrying field: the field refuses a
    malformed value at construction, and this turns an accepted one into something a window
    can be sized from. Both read a pattern built from `HorizonUnit`, so a value that
    constructs always parses -- note that `SignalFrame.horizon` constructs against the
    narrower `COUNTABLE_HORIZON_PATTERN` while this accepts the whole four-unit
    `HORIZON_PATTERN`, because a label window may be asked for in a unit a signal may not be
    stated in.
    """
    if type(text) is not str or _HORIZON.fullmatch(text) is None:
        raise HorizonError(
            f"{text!r} is not a research horizon; the grammar is a count of 1..999 with no "
            f"leading zero followed by one of {[unit.value for unit in HorizonUnit]} "
            f"({HORIZON_PATTERN}), so '5d' is five trading sessions and '3m' is three months"
        )
    return ResearchHorizon(count=int(text[:-1]), unit=HorizonUnit(text[-1]))
