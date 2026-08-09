"""The corporate-action adjustment factor (`V2-P1-006`) -- without it every return is wrong.

## What the number is, and which convention it belongs to

Tushare's `adj_factor` is a **cumulative back-adjustment factor**, anchored at 1.0 on the
security's first session. A live probe on 2026-08-08 measured `000001.SZ`'s series running
from `1.0` on 1991-04-03 to `139.008` on 2026-08-07 across 8,627 rows.

That anchoring is what fixes the convention, and it is worth stating because the two
conventions are routinely confused:

- **后复权 (`PriceAdjustment.backward`)** holds the *earliest* price fixed and scales later
  ones up: `backward(day) = close(day) * factor(day)`. On 1991-04-03 the factor is 1.0, so
  the listing-day price is its own adjusted price. No base day is needed -- the factor is an
  absolute quantity, so this answer does not depend on which window was read.
- **前复权 (`PriceAdjustment.forward`)** holds a *chosen* day's price fixed and scales earlier
  ones down: `forward(day, base) = close(day) * factor(day) / factor(base)`. It is only
  defined relative to that base day, so `adjust_price` **refuses** to run without one rather
  than silently defaulting to "the last day I happen to hold" -- a default that would make
  every stored price move the next time the panel is refreshed.

An earlier statement of this contract called the published number a "前复权累积因子". It is
not: a forward-adjusted factor is 1.0 at the *base* day and below 1.0 before it, and this
series is 1.0 at the *listing* and above 1.0 after it. The published factor is the backward
one, and the forward one is derived from it by division.

A **return** is the same under both, because they differ by a constant scale:
`(P1*f1)/(P0*f0)` is unchanged when both factors are divided by `f(base)`. So
`adjusted_return` takes no convention at all -- offering one would suggest a choice that does
not exist. On the real ex-dividend day 2026-06-12 the unadjusted return is `-0.53%` and the
adjusted one is `+2.7423%`, which matches Tushare's own `pct_chg` of `2.7422%` to four
decimal places; the sign is not the same, so the error this dataset removes is not a rounding
error.

## Why the storage is piecewise constant, and what the anchors are for

The factor is a step function that moves only on ex-rights/ex-dividend days.
`000001.SZ`'s 8,627 rows carry **43** distinct steps -- a 201x compression -- and the whole
market's daily form would be ~4.8e7 rows for a series with roughly one move per security per
year. So the panel stores the steps.

`load_bearing_observations` is the rule, and it keeps **three** kinds of row, not one:

1. the **first** observation of the fetched window,
2. every observation whose factor differs from its predecessor,
3. the **last** observation of the fetched window.

(1) and (3) are the answer to the partition-boundary problem. A partition is one calendar
year; a reader who loads 2024 alone must be able to answer every day of 2024 without the
2023 partition, and the year's opening observation is what anchors the run of days before the
first change. (3) is the mirror image and is the easier one to get wrong: `000001.SZ`'s last
2024 change is on 2024-10-10, so dropping the closing observation would leave the partition
claiming -- through `covered_through` -- that the read stopped in October, and every November
and December question would be refused. Both rows are **real observations**, not interpolated
markers: the factor genuinely was 116.713 on 2024-01-02 and 127.7841 on 2024-12-31.

Two consecutive partitions therefore overlap in meaning but not in rows: 2023 closes with its
own 2023-12-29 observation and 2024 opens with its own 2024-01-02 one. Concatenating them
yields a longer history with, at worst, one redundant equal-valued row per boundary, which
`bisect` does not care about.

## Two horizons, and why this contract has an upper one where `NameHistory` does not

`domain/name_history.py` deliberately answers every day after its last record: a name persists
until it is changed, so extrapolating it forward invents nothing. A factor is the opposite. It
changes on a schedule nobody can see from the data already held, and `000001.SZ` moved four
times in the two years after 2024. Answering "the last factor we fetched" for a later day is
therefore an assertion that no corporate action happened in a window this read never looked
at -- the exact fail-open shape this repository has rejected four times. So `factor_on`
refuses both ends, and `covered_from` / `covered_through` state the window rather than making
a caller discover it by tripping over it.

## What is measured and what is merely believed

`KNOWN_ADJUSTMENT_LIMITATIONS` carries the boundaries, each with a number behind it. The one
worth repeating here is that **the series is not monotone**, contrary to the obvious reading
of "cumulative": eight of `000001.SZ`'s own rows step down, and across the whole market
between 2023-12-29 and 2024-06-28 **28 of 5,351** securities' factors fell -- five of them
substantially, `600069.SH` by 89.7% in one day (6.415 -> 0.6604 on 2024-06-17). A
monotonicity assertion would be a universal claim the data falsifies, so this module does not
make one.

## Layering

Pure `datetime`, `bisect` and `dataclasses`, plus three sibling `domain` modules
(`panel_batch` for the reserved subject-column name, `trading_calendar` and `stock_universe`
for the cross-section join). No provider, no store, no clock -- the same placement, and the
same reason, as `domain/trading_calendar.py`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME
from openalpha_cn.domain.stock_universe import StockUniverse
from openalpha_cn.domain.trading_calendar import TradingCalendar

ADJ_FACTOR_DATASET: Final[str] = "adj_factor"
"""The panel dataset (and partition directory) the factor steps are stored under.

Declared here rather than in `providers/tushare.py` for the reason `TRADING_CALENDAR_DATASET`
is: `panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

ADJUSTMENT_FACTOR_COLUMN: Final[str] = "adj_factor"
ADJUSTMENT_DATE_COLUMN: Final[str] = "factor_date"

ADJUSTMENT_DATA_COLUMNS: Final[tuple[str, ...]] = (
    ADJUSTMENT_DATE_COLUMN,
    ADJUSTMENT_FACTOR_COLUMN,
)
"""The columns a provider projects, in order. `subject` is added by the batch itself.

`factor_date` duplicates the `event_time` clock column as plain ISO text, exactly as
`namechange` stores `effective_date` beside its own. Keeping the trading date visible *as
data* is what lets a stored partition be read back without re-deriving a date from an instant
in whichever timezone the reader happens to be in.
"""

ADJUSTMENT_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *ADJUSTMENT_DATA_COLUMNS,
)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows back."""


class AdjustmentError(ValueError):
    """Raised for any malformed adjustment history or malformed query against one.

    A `ValueError` subclass to match `domain/trading_calendar.py`'s `TradingCalendarError`
    and `domain/panel_batch.py`'s `PanelBatchError`.
    """


class AdjustmentHorizonError(AdjustmentError):
    """Raised when a question's answer would lie outside the window that was actually read.

    Separate from its parent because it is not a caller mistake: the question was well formed
    and the fetch simply did not cover that day. `V2-P1-013`'s gate blocks on it; a malformed
    query is a bug.
    """


class PriceAdjustment(Enum):
    """Which end of the series a price adjustment holds fixed. Deliberately not a `bool`.

    `__bool__` raises for both members. There are exactly two conventions and neither is the
    "off" one, so `if convention:` has no correct reading -- the same answer
    `CalendarDayStatus`, `ListingStatus` and `RiskWarning` give to the same class of mistake.
    """

    backward = "backward"
    """后复权: hold the security's first session fixed, scale later prices up."""

    forward = "forward"
    """前复权: hold a named base day fixed, scale earlier prices down."""

    def __bool__(self) -> bool:
        raise AdjustmentError(
            f"{type(self).__name__}.{self.name} names one of two price-adjustment "
            "conventions and has no truth value; compare it against the member you mean "
            "(`is PriceAdjustment.forward`) rather than reading one of them as 'off'"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorObservation:
    """One security's cumulative adjustment factor on one trading day.

    A plain carrier with no validation of its own, following `CalendarDay`'s precedent: a
    nominal type is not a boundary, so the rules live once, in `build_adjustment_history`.
    """

    ts_code: str
    observed_on: date
    factor: float


@dataclass(frozen=True, slots=True, kw_only=True)
class AdjustmentLimitation:
    """One named boundary on what an adjustment history can be trusted to answer."""

    code: str
    detail: str


KNOWN_ADJUSTMENT_LIMITATIONS: Final[tuple[AdjustmentLimitation, ...]] = (
    AdjustmentLimitation(
        code="factor_is_not_monotone",
        detail=(
            "A cumulative factor sounds like it can only rise, and it does not. Eight of "
            "000001.SZ's 8,627 rows step down: five are genuine 1991-93 movements of up to "
            "6.4% (2.608 -> 2.463 on 1991-11-11), three are four-to-three-decimal rounding "
            "worth 0.0002% (125.0496 -> 125.0493 on 2024-06-25). Market-wide, 28 of the "
            "5,351 securities present on both 2023-12-29 and 2024-06-28 have a lower factor "
            "on the later date, and five of those fell by more than 0.1% -- 600069.SH by "
            "89.7% in one day (6.415 -> 0.6604 on 2024-06-17). Any code that assumes a "
            "non-decreasing series is wrong on real data."
        ),
    ),
    AdjustmentLimitation(
        code="silent_truncation_at_the_response_cap",
        detail=(
            "The endpoint serves at most 6,000 rows per response and drops the *oldest* "
            "ones. A bare per-security request for 000001.SZ returns 6,000 rows starting "
            "2001-11-14; the true history is 8,627 rows starting 1991-04-03. Nothing in the "
            "row data says so -- `data.count` is always 0 -- and the only signals are "
            "`data.has_more` and the row count itself. "
            "`providers/tushare.py::_check_response_completeness` refuses such a response "
            "rather than storing a factor series with its first decade missing."
        ),
    ),
    AdjustmentLimitation(
        code="no_revision_history",
        detail=(
            "The endpoint serves one snapshot per request and carries no announcement or "
            "revision instant, so a factor corrected upstream is indistinguishable from one "
            "that was always that value. Availability is dated at the trading day's close "
            "(15:00/16:30 Asia/Shanghai, ClockStrategy.daily_close) and revision_time equals "
            "available_time, which invents nothing; read a partition's revised_row_count of "
            "0 as 'unmeasured', not 'none'. The three 0.0002% rounding steps above are "
            "themselves evidence that the published numbers do get restated."
        ),
    ),
    AdjustmentLimitation(
        code="dates_are_not_the_stored_trading_calendar",
        detail=(
            "The dates the factor series carries are not exactly the ones trade_cal reports. "
            "000001.SZ has adj_factor rows on 64 dates in 1991 that the SZSE calendar marks "
            "closed -- every one of them a date the SSE calendar marks open. So an expected "
            "row count derived from the stored calendar is not a sound completeness check "
            "for this dataset, and none is used: completeness is judged from the response "
            "itself."
        ),
    ),
    AdjustmentLimitation(
        code="suspension_is_invisible",
        detail=(
            "A factor row exists on days the security did not trade. 000024.SZ's last daily "
            "bar is 2015-12-07 and its factor series runs to 2015-12-29, so the presence of "
            "a factor is not evidence of a session. Suspension is suspend_d (V2-P1-008)."
        ),
    ),
)
"""Named boundaries on what an adjustment history answers, each measured on real data.

**Not an enumeration of every way the series could be wrong.** These are the ones a live
probe of the endpoint could demonstrate on 2026-08-08.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class AdjustmentHistory:
    """One security's adjustment factor as a step function over a bounded window.

    Build it with `build_adjustment_history`; this constructor is not a boundary and validates
    nothing. `observations` is ascending by `observed_on` and holds at least one entry.
    """

    ts_code: str
    observations: tuple[FactorObservation, ...]

    @property
    def covered_from(self) -> date:
        """The first day this history can answer for."""
        return self.observations[0].observed_on

    @property
    def covered_through(self) -> date:
        """The last day this history can answer for; see this module's docstring."""
        return self.observations[-1].observed_on

    def change_points(self) -> tuple[FactorObservation, ...]:
        """The observations at which the factor actually moved, excluding the anchor."""
        return tuple(
            entry
            for index, entry in enumerate(self.observations)
            if index > 0 and entry.factor != self.observations[index - 1].factor
        )

    def factor_on(self, day: date) -> float:
        """The cumulative back-adjustment factor in effect on `day`.

        Raises `AdjustmentHorizonError` outside `[covered_from, covered_through]` rather than
        extrapolating in either direction.
        """
        _require_plain_date(day, "day")
        if day < self.covered_from:
            raise AdjustmentHorizonError(
                f"{day.isoformat()} is before {self.ts_code}'s first adjustment factor, "
                f"observed {self.covered_from.isoformat()}; an unfetched factor is unknown "
                "rather than equal to the earliest one on file"
            )
        if day > self.covered_through:
            raise AdjustmentHorizonError(
                f"{day.isoformat()} is after {self.ts_code}'s last adjustment factor, "
                f"observed {self.covered_through.isoformat()}; carrying the last factor "
                "forward would assert that no corporate action happened in a window this "
                "read never covered"
            )
        position = bisect_right([entry.observed_on for entry in self.observations], day)
        return self.observations[position - 1].factor

    def adjustment_ratio(
        self,
        day: date,
        *,
        convention: PriceAdjustment,
        base_day: date | None = None,
    ) -> float:
        """The multiplier that turns `day`'s raw price into its adjusted one.

        `backward` is the published factor itself and takes no `base_day`; `forward` is that
        factor divided by the base day's, and has no meaning without one. Passing the wrong
        combination raises rather than being ignored -- a caller who supplies a base day has
        asked for the forward convention, and quietly answering the backward one would return
        a plausible number from the other scale.
        """
        if type(convention) is not PriceAdjustment:
            raise AdjustmentError(
                f"convention must be a PriceAdjustment, got {type(convention).__name__} "
                f"{convention!r}"
            )
        factor = self.factor_on(day)
        if convention is PriceAdjustment.backward:
            if base_day is not None:
                raise AdjustmentError(
                    "backward adjustment takes no base_day: 后复权 is anchored at the "
                    "security's own first session, where the published factor is 1.0. A "
                    f"base_day of {base_day} means the forward convention was intended"
                )
            return factor
        if base_day is None:
            raise AdjustmentError(
                "forward adjustment needs a base_day: 前复权 holds one chosen day's price "
                "fixed, and defaulting to the newest day held would silently restate every "
                "adjusted price the next time the panel is refreshed"
            )
        return factor / self.factor_on(base_day)

    def adjust_price(
        self,
        price: float,
        day: date,
        *,
        convention: PriceAdjustment,
        base_day: date | None = None,
    ) -> float:
        """`price`, observed on `day`, restated on the requested convention's scale."""
        return price * self.adjustment_ratio(day, convention=convention, base_day=base_day)

    def adjusted_return(
        self, *, start: date, end: date, start_price: float, end_price: float
    ) -> float:
        """The corporate-action-adjusted simple return between two days.

        Takes no convention on purpose: forward and backward adjustment differ by a constant
        scale, which cancels in a ratio, so there is exactly one adjusted return and offering
        a choice would suggest otherwise. `tests/unit/domain/test_adjustment.py` proves the
        equality on real prices rather than asserting it here.
        """
        _require_plain_date(start, "start")
        _require_plain_date(end, "end")
        if start >= end:
            raise AdjustmentError(
                f"start {start.isoformat()} is not before end {end.isoformat()}; a return "
                "needs two distinct days in order"
            )
        _require_price(start_price, "start_price")
        _require_price(end_price, "end_price")
        return (end_price * self.factor_on(end)) / (start_price * self.factor_on(start)) - 1


def load_bearing_observations(
    observations: Sequence[FactorObservation],
) -> tuple[FactorObservation, ...]:
    """The rows of an ascending window that a step function cannot be rebuilt without.

    The window's first observation, every observation whose factor differs from its
    predecessor, and the window's last observation. See this module's docstring for why all
    three kinds are needed and why the third is the one that is easy to drop.

    Refuses an input that is not already ascending rather than sorting it. "First" and "last"
    are positional here, so a caller who concatenated two partitions in the wrong order would
    otherwise get a silently mis-anchored window instead of an error.
    """
    entries = tuple(observations)
    if not entries:
        raise AdjustmentError(
            "load_bearing_observations needs at least one observation; an empty window has "
            "no anchor and would produce a history that refuses every question"
        )
    for index in range(1, len(entries)):
        if entries[index].observed_on <= entries[index - 1].observed_on:
            raise AdjustmentError(
                f"observations must be ascending by observed_on; row {index} is "
                f"{entries[index].observed_on.isoformat()} after row {index - 1}'s "
                f"{entries[index - 1].observed_on.isoformat()}"
            )
    kept = [entries[0]]
    kept.extend(
        entries[index]
        for index in range(1, len(entries))
        if entries[index].factor != entries[index - 1].factor
    )
    if kept[-1] is not entries[-1]:
        kept.append(entries[-1])
    return tuple(kept)


def build_adjustment_history(
    ts_code: str, observations: Iterable[FactorObservation]
) -> AdjustmentHistory:
    """Assemble an `AdjustmentHistory` from factor observations, in any order.

    Tushare returns `adj_factor` in *descending* `trade_date` order, so accepting any order
    and sorting here is the actual shape of the data -- unlike `load_bearing_observations`,
    which is positional by construction and therefore refuses to sort.

    Refuses, rather than repairs, five things: a malformed `ts_code`, an observation belonging
    to another security, a malformed date, a factor that cannot scale a price, and two
    *different* factors on one day. Byte-identical duplicates collapse, because a duplicate
    carries no fact the original does not.
    """
    _require_text(ts_code, "ts_code")
    by_day: dict[date, FactorObservation] = {}
    for entry in observations:
        _require_plain_date(entry.observed_on, "observed_on")
        _require_price(entry.factor, "factor")
        if entry.ts_code != ts_code:
            raise AdjustmentError(
                f"an adjustment history for {ts_code} carries {entry.ts_code}; one history "
                "covers one security, and mixing two would adjust prices by another "
                "security's corporate actions"
            )
        existing = by_day.get(entry.observed_on)
        if existing is not None and existing.factor != entry.factor:
            raise AdjustmentError(
                f"{ts_code} has two adjustment factors on {entry.observed_on.isoformat()} "
                f"({existing.factor!r} and {entry.factor!r}); a security has one factor per "
                "session, so this is two sources that were never reconciled"
            )
        by_day[entry.observed_on] = entry
    if not by_day:
        raise AdjustmentError(
            f"an adjustment history needs at least one factor observation; {ts_code} has "
            "none, and an empty history refuses every question, which is indistinguishable "
            "from a failed read"
        )
    return AdjustmentHistory(
        ts_code=ts_code, observations=tuple(by_day[key] for key in sorted(by_day))
    )


def adjustment_histories_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> Mapping[str, AdjustmentHistory]:
    """Rebuild one history per security from rows shaped like `ADJUSTMENT_PANEL_COLUMNS`.

    The counterpart of the provider's projection. What this function owns is the shape of a
    *stored row* -- its width, the ISO text its date column is stored as, and the fact that
    the factor comes back as a real `float` -- which `build_adjustment_history` never sees.

    A read-only mapping rather than a `dict`, because a whole-market read is shared and a
    caller mutating one entry would be editing what other callers hold.
    """
    grouped: dict[str, list[FactorObservation]] = {}
    for index, row in enumerate(rows):
        if len(row) != len(ADJUSTMENT_PANEL_COLUMNS):
            raise AdjustmentError(
                f"row {index} has {len(row)} values, expected "
                f"{len(ADJUSTMENT_PANEL_COLUMNS)} ({', '.join(ADJUSTMENT_PANEL_COLUMNS)})"
            )
        subject, day_text, factor = row
        ts_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        grouped.setdefault(ts_code, []).append(
            FactorObservation(
                ts_code=ts_code,
                observed_on=_parse_iso_date(day_text, index, ADJUSTMENT_DATE_COLUMN),
                factor=_stored_factor(factor, index),
            )
        )
    return MappingProxyType(
        {ts_code: build_adjustment_history(ts_code, group) for ts_code, group in grouped.items()}
    )


def adjustment_factors_on(
    histories: Mapping[str, AdjustmentHistory],
    *,
    universe: StockUniverse,
    calendar: TradingCalendar,
    day: date,
) -> tuple[tuple[str, float], ...]:
    """Every listed security's factor on one open session, ascending by `ts_code`.

    The join `V2-P1-006` owes `V2-P1-004` and `V2-P1-005`, and it is fail-closed on both
    sides. The session comes from `calendar.is_trading_day`, which raises rather than
    answering `False` beyond its horizon, and a day the exchange was shut has no cross section
    at all -- adjusting prices on a non-session would be adjusting prices that do not exist.
    Membership comes from `universe.listed_on`, restricted to the calendar's own exchange for
    the reason `listed_universe_by_trading_day` states.

    A security that was listed and has **no history**, or whose history does not reach the
    day, blocks the whole cross section rather than being dropped from it. A partial cross
    section is the failure this is guarding: returning the other 5,386 names would produce a
    perfectly plausible market snapshot with one security silently missing, and nothing
    downstream could tell it from a security that was not listed.
    """
    _require_plain_date(day, "day")
    if not calendar.is_trading_day(day):
        raise AdjustmentError(
            f"{day.isoformat()} is not an open session on the {calendar.exchange} calendar, "
            "so there are no prices to adjust on it"
        )
    section: list[tuple[str, float]] = []
    for ts_code in universe.listed_on(day, exchange=calendar.exchange):
        history = histories.get(ts_code)
        if history is None:
            raise AdjustmentError(
                f"{ts_code} was listed on {day.isoformat()} and this read holds no "
                "adjustment factor for it; a cross section that quietly omitted it would be "
                "indistinguishable from one in which it was not listed"
            )
        section.append((ts_code, history.factor_on(day)))
    return tuple(sorted(section))


def _require_text(value: str, role: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise AdjustmentError(
            f"{role} must be a non-empty string without surrounding whitespace; got {value!r}"
        )


def _require_stored_text(value: object, index: int, column: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise AdjustmentError(
            f"row {index}: {column} must be a non-empty string without surrounding "
            f"whitespace, got {type(value).__name__} {value!r}"
        )
    return value


def _require_price(value: float, role: str) -> None:
    """Reject anything that is not exactly a finite positive `float`.

    `type(...) is float`, not `isinstance`: `bool` is a subclass of `int`, so an `isinstance`
    check written against a numeric tower would let `True` through as the factor 1.0 and make
    every adjusted price equal to its raw one -- the failure this whole module exists to
    prevent, arriving silently. Zero and the negatives are refused because they cannot scale a
    price, and the forward convention divides by one.
    """
    if type(value) is not float or not isfinite(value) or value <= 0.0:
        raise AdjustmentError(
            f"{role} must be a finite positive float; got {type(value).__name__} {value!r}"
        )


def _stored_factor(value: object, index: int) -> float:
    if type(value) is not float:
        raise AdjustmentError(
            f"row {index}: {ADJUSTMENT_FACTOR_COLUMN} must be a float, got "
            f"{type(value).__name__} {value!r}"
        )
    return value


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise AdjustmentError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AdjustmentError(f"row {index}: {column} is not an ISO date: {value!r}") from error


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`; see `stock_universe._require_plain_date`."""
    if type(value) is not date:
        raise AdjustmentError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and compares against dates without ever equalling one"
        )
