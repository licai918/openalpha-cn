"""The panel-to-facts adapter of the survivor book (`V2-P7-016`).

The survivor-book kernel (`domain/survivor_*.py`) screens a list of `SecurityFacts` and knows
nothing about where they came from. This module is the other half: it turns what the panel
holds on one signal day -- a name history, a listing date, a halt flag, a price, a few factor
values -- into one `SecurityFacts` row per listed security. T3
(`docs/research/p7-step0-validation/T3-st-and-new-listings.md`) is the measured basis for
every rule below.

Two halves. The **pure** functions (`name_status`, `listing_age_sessions`, `assemble_fact`,
`require_coverage`) take plain values and read nothing. `build_facts_day` is the panel half: it
reads one signal day through the panel's own point-in-time loaders and feeds them.

## One signal day off the panel (`build_facts_day`)

Every read is at `as_of`, through the loader that owns the dataset, so the panel's visibility
rules are the only time filter here; `as_of` must be at or after the session's 16:30
publication. The universe is every security the registry lists on the day, on **every** board
(the screen, not the adapter, says `board_not_allowed`), plus any security with a bar on the day
that the registry has no listing row for at all (aged 0, so `listing_too_recent`, T3 3.3;
counted as `FactsNotes.no_listing_row`). A code the registry *does* know and does not list that
day -- delisted, or priced before its listing date (T3 3.2's `.BJ` rows) -- has no row even with
a bar: the kernel sells such a holding rather than freezing it. One with a bar on the day is
counted as `FactsNotes.not_listed_with_bar`, so the exclusion is a number in the daily log.

A registry listing row is published at its own day's midnight, so a code priced before its
listing is "never heard of" (a row aged 0) to a read before that day and "known, not listed" (no
row, counted) to a read after it: two reads of one signal day differ by that row and those two
counts.

- **A halted security with no bar still gets its row** (T6/T7 0(d)): `suspended=True`, its last
  close, and the mean amount of its last 20 bars. Without the row the kernel would sell a halted
  holding as unassessed instead of freezing it.
- **How far back a halted security is looked for.** For a code in `held` (the book's holdings)
  the last bar and the 20 bars are looked for as far back as the panel goes -- to the listing
  day, or to the first session the bar panel holds: a holding halted for a year must still be
  frozen, not sold. For every other security both are looked for at most `max_carry_sessions`
  sessions back. Its `avg_amount` is then the mean of the bars within that reach, which may be
  fewer than 20 (at least the last one); and one with no bar at all in reach gets no row,
  counted as `FactsNotes.no_bar_within_carry`. Nothing is lost by that: such a security is not
  held, and a row could only have said `suspended`, which keeps it out of every population the
  screen ranks.
- **A held code with no row is the caller's to refuse.** A held code that is listed on the day
  and has never had a bar in the panel's reach has no close to carry, gets no row and is counted
  in `no_bar_within_carry`; the kernel would sell it as unassessed. The caller (the daily task)
  must refuse such a day by name. A held code that is *not* listed on the day (delisted) has no
  row on purpose: the kernel sells it.
- **A trading security's `avg_amount`** is the mean of its bars over the 20 sessions ending on
  the signal day (T8's `a20`). **`suspended`** is "no bar", or a halt through the close
  (`backtest.execution.suspended_at_the_close`).
- **Factor values** are the signal day's own build, the one stamped at the session's 16:30 (the
  instant the daily command builds at), read narrowly by that instant; only values the processed
  tier admits count, each negated for a `lower_is_better` definition. A halted security takes
  what that build says about it -- the engine values it off its own last sessions while they
  fit the factor's window -- or `None`: nothing is carried from older builds, because a frozen
  holding needs no composite and a halted name cannot be an entrant. A factor with **no build at
  all** at that instant is refused by name, the factor and the instant
  (`MissingFactorBuildError`), before any row is built.
- **The 5% band** is offered to `name_status` only for a main-board security with a bar and a
  published bounded band (controller decision 1); elsewhere it is `None`.
- **The coverage guard.** It counts only the rows on the boards the book may trade
  (`config.boards`): `FactsDay.universe_size` is their number and `scored_size` those whose
  security has a bar on the day and every factor value. A day on which fewer than
  `min_coverage` of them are scored is refused with `FactsCoverageError`, as is a day with none:
  a screen that cannot see most of the market would make the kernel sell every holding it
  cannot see -- a trading holding with a missing value is sold, not frozen -- and rows the book
  cannot trade must not dilute that. A carried row never counts, because the engine values a
  halted security off its own last sessions -- a market-wide halt would otherwise pass with
  every value present -- so a day on which the bars are missing lands here too.

## The three-state name

"Is it ST" is a question the name corpus cannot always answer, and a filter that raises when
the corpus has a gap rejects the whole run for one security. `name_status` is therefore total:
it answers `st`, `delisting`, `ordinary` or `unknown`, and `unknown` is a value, not an error.

- **By name, when the history can judge.** The history is first cut to what had been
  *announced* by the signal day (the announcement clock), so a rename that lands after the day
  is invisible whatever the caller passed in. Then the name in force decides; and a rename
  already announced as ST but not yet in force counts as `st` too (T3 2.4(iv): 99.2% of ST
  names were announced a trading day or more ahead, and `pending_at` carries no calendar, so
  any announced-not-yet-effective ST-like name qualifies).
- **By the exchange band, as a union.** A main-board 5% band is the exchange's own statement
  that a security is under special treatment, independent of the name corpus (T3 2.2: 6,674 of
  6,675 name-ST rows had it, and the 50 rows the names missed were all one security whose
  first name row arrived in 2021). `band_is_five_percent=True` therefore makes a security
  `st` unless its name already says `delisting`. The caller passes `True` only for the main
  board: on the other boards the band is no independent witness (T3 2.2).
- **`unknown`** is returned only when neither can say: no history, nothing announced yet, or no
  record in force yet and no pending ST name, and no band that says otherwise. The adapter
  treats it as ordinary and counts it (`FactsNotes.name_unknown_assumed_ordinary`), because
  the alternative -- refusing every security the corpus has never renamed -- would refuse most
  of the market.

`name_status_reason` says which of these rules reached the answer, and `FactsNotes.tally` counts
them apart: an ST by name, an ST only because the rename is still pending, an ST only by the
band; and of the `unknown`s, the securities never renamed since the corpus began (T3's
`no_record`, 1,516-1,790 a day) apart from the genuine gaps (`before_first`, 0-11 a day).

Only the two "the corpus does not reach that day" `NameHistoryHorizonError` cases become a
verdict. Any other `NameHistoryError` is a malformed call and still raises.

## Listing age

`listing_age_sessions` counts open exchange sessions from the listing day, inclusive. A listing
older than the calendar cannot be counted exactly, only from the calendar's first day on; that
lower bound is safe only when it already reaches the threshold, so a shorter one is refused
with a named error instead of being reported as a young security.
"""

import math
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from types import MappingProxyType
from typing import Final, Literal, get_args

from openalpha_cn.backtest.execution import security_board, suspended_at_the_close
from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES
from openalpha_cn.domain.daily_prices import DAILY_DATASET, DailyBar
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.labels import halt_corpus_for_years
from openalpha_cn.domain.name_history import (
    NAMECHANGE_DATASET,
    NameHistory,
    NameHistoryHorizonError,
    RiskWarning,
)
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.survivor_book import Board, SecurityFacts, SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, TradingCalendar
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS, load_processed_factor_observations
from openalpha_cn.panel_ingest import (
    load_daily_bars,
    load_name_histories,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
    session_publication_instant,
)

NameStatus = Literal["st", "delisting", "ordinary", "unknown"]

NameStatusReason = Literal[
    "st_by_name",
    "st_pending",
    "st_by_band",
    "delisting",
    "ordinary",
    "unknown_no_record",
    "unknown_before_first",
]
"""Which rule reached a `NameStatus`; `name_status_reason` answers it.

- `st_by_name`: an ST, *ST or PT name is in force on the day.
- `st_pending`: no warning is in force, and an ST-like rename announced by the day takes effect
  after it.
- `st_by_band`: the name does not say ST (ordinary, or the corpus cannot say) and the main-board
  band is 5%.
- `delisting`: a delisting-period name is in force.
- `ordinary`: an ordinary name is in force and no band says otherwise.
- `unknown_no_record`: the corpus holds no rename of the security announced by the day -- it has
  not been renamed since the corpus began (T3 2.3's `no_record`).
- `unknown_before_first`: renames are on file and none of them is in force yet, with no ST
  pending -- a genuine gap at the corpus's left edge (T3 2.4(ii)'s `before_first`).
"""

_ST_WARNINGS: Final[frozenset[RiskWarning]] = frozenset(
    {RiskWarning.st, RiskWarning.star_st, RiskWarning.pt}
)
"""The warnings that are special treatment. `delisting_process` is deliberately not here."""

_NAME_STATUSES: Final[tuple[str, ...]] = get_args(NameStatus)
_NAME_STATUS_REASONS: Final[tuple[str, ...]] = get_args(NameStatusReason)
_STATUS_OF_REASON: Final[Mapping[NameStatusReason, NameStatus]] = MappingProxyType(
    {
        "st_by_name": "st",
        "st_pending": "st",
        "st_by_band": "st",
        "delisting": "delisting",
        "ordinary": "ordinary",
        "unknown_no_record": "unknown",
        "unknown_before_first": "unknown",
    }
)
_SPECIAL_TREATMENT_STATUSES: Final[frozenset[str]] = frozenset({"st", "delisting"})


class SurvivorFactsError(ValueError):
    """Raised for an input the facts adapter cannot turn into a fact."""


class ListingAgeHorizonError(SurvivorFactsError):
    """The calendar is too short to say whether a listing older than it is old enough."""


class FactsCoverageError(SurvivorFactsError):
    """Too few of the day's rows carry every factor value for the screen to be trusted."""


class MissingFactorBuildError(SurvivorFactsError):
    """A composite factor has no processed build at the signal day's instant at all."""


AMOUNT_WINDOW_SESSIONS: Final[int] = 20
"""The sessions a trailing average amount is taken over (design section 6; T8's `a20`)."""

DEFAULT_MIN_COVERAGE: Final[float] = 0.5
"""`build_facts_day`'s coverage threshold unless the registration states another."""

DEFAULT_MAX_CARRY_SESSIONS: Final[int] = 250
"""How far back a halted security's last bar and its 20 bars are looked for, unless the
registration says; a held security's are not bounded."""

FIVE_PERCENT_BAND: Final[float] = 0.05
BAND_TOLERANCE: Final[float] = 0.0051
"""A main-board band within this of 5% is the exchange's own special-treatment band (T3 7)."""


@dataclass(frozen=True, slots=True)
class FactsNotes:
    """What the adapter assumed or noticed while it built one day's facts.

    Counts only: they land in the daily log so an unusual day shows up as a number, not as a
    silent change in who was eligible. `st` and `name_unknown_assumed_ordinary` are totals, and
    each is refused unless its parts add up to it; build the name counts with `tally`.

    The notes count rows on **every** board; `FactsDay.universe_size` and `scored_size`, which
    the coverage guard compares, count only the rows on the boards the book may trade.
    """

    name_unknown_assumed_ordinary: int
    """Securities whose name state was `unknown` and who were treated as ordinary."""
    suspended_without_bar: int
    """Suspended securities with no daily row that day, carried on their last close."""
    no_listing_row: int
    """Securities the stock registry has no listing row for (judged too recent)."""
    delisting: int
    """Securities in the delisting-period name state."""
    st: int
    """Securities that are ST by name, by pending name or by the 5% band."""
    st_by_name: int
    """Of `st`: an ST name is in force."""
    st_pending: int
    """Of `st`: only because an announced ST rename is still pending (decided early)."""
    st_by_band: int
    """Of `st`: only because the main-board band is 5%."""
    name_unknown_no_record: int
    """Of `name_unknown_assumed_ordinary`: never renamed since the corpus began."""
    name_unknown_before_first: int
    """Of `name_unknown_assumed_ordinary`: a genuine gap at the corpus's left edge."""
    no_bar_within_carry: int
    """Listed securities with no daily bar in reach, which therefore have no fact row at all:
    for a code not held, none within `max_carry_sessions` sessions; for a held code, none since
    its listing or the panel's first session -- a day the caller must refuse by name."""
    not_listed_with_bar: int
    """Securities with a daily bar on the day that the registry knows and does not list on it --
    delisted, or priced before their listing date -- and which therefore have no fact row. A
    count, not a row: no fact changes with it."""

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) < 0:
                raise SurvivorFactsError(f"{item.name} is a count and cannot be negative")
        st_parts = self.st_by_name + self.st_pending + self.st_by_band
        if st_parts != self.st:
            raise SurvivorFactsError(
                f"st is {self.st} but its parts add up to {st_parts} ({self.st_by_name} by "
                f"name + {self.st_pending} pending + {self.st_by_band} by band)"
            )
        unknown_parts = self.name_unknown_no_record + self.name_unknown_before_first
        if unknown_parts != self.name_unknown_assumed_ordinary:
            raise SurvivorFactsError(
                f"name_unknown_assumed_ordinary is {self.name_unknown_assumed_ordinary} but its "
                f"parts add up to {unknown_parts} ({self.name_unknown_no_record} never renamed "
                f"+ {self.name_unknown_before_first} before the first record)"
            )

    @classmethod
    def tally(
        cls,
        *,
        reasons: Iterable[NameStatusReason],
        suspended_without_bar: int,
        no_listing_row: int,
        no_bar_within_carry: int,
        not_listed_with_bar: int,
    ) -> "FactsNotes":
        """One day's notes from every fact's name-status reason and the four other counts."""
        counted = Counter(reasons)
        unrecognised = sorted(set(counted) - set(_NAME_STATUS_REASONS))
        if unrecognised:
            raise SurvivorFactsError(
                f"unrecognised name-status reason(s) {unrecognised}; expected one of "
                f"{_NAME_STATUS_REASONS}"
            )
        return cls(
            name_unknown_assumed_ordinary=(
                counted["unknown_no_record"] + counted["unknown_before_first"]
            ),
            suspended_without_bar=suspended_without_bar,
            no_listing_row=no_listing_row,
            delisting=counted["delisting"],
            st=counted["st_by_name"] + counted["st_pending"] + counted["st_by_band"],
            st_by_name=counted["st_by_name"],
            st_pending=counted["st_pending"],
            st_by_band=counted["st_by_band"],
            name_unknown_no_record=counted["unknown_no_record"],
            name_unknown_before_first=counted["unknown_before_first"],
            no_bar_within_carry=no_bar_within_carry,
            not_listed_with_bar=not_listed_with_bar,
        )


def name_status(
    *,
    history: NameHistory | None,
    band_is_five_percent: bool | None,
    day: date,
) -> NameStatus:
    """The special-treatment state of one security on `day`. Total: it never raises on a gap.

    `history` is `None` when the corpus has no entry for the security. `band_is_five_percent`
    is `True` when the exchange's published band for the day is 5% of the previous close,
    `False` when it is another band, and `None` when no band is known; pass `True` only for a
    main-board security. See the module docstring for the rules and their evidence.
    """
    return _STATUS_OF_REASON[
        name_status_reason(history=history, band_is_five_percent=band_is_five_percent, day=day)
    ]


def name_status_reason(
    *,
    history: NameHistory | None,
    band_is_five_percent: bool | None,
    day: date,
) -> NameStatusReason:
    """Which rule decides `name_status` for the same arguments; see `NameStatusReason`.

    The name is asked first and the band second, so a security that is ST by name and has a 5%
    band is `st_by_name`, and the band never overrides `delisting`.
    """
    by_name = _reason_by_name(history, day)
    if by_name in ("st_by_name", "st_pending", "delisting"):
        return by_name
    if band_is_five_percent:
        return "st_by_band"
    return by_name


def _reason_by_name(history: NameHistory | None, day: date) -> NameStatusReason:
    """What the name alone says, and how; `unknown_*` when the history cannot judge `day`."""
    if history is None:
        return "unknown_no_record"
    try:
        visible = history.announced_by(day)
    except NameHistoryHorizonError:
        return "unknown_no_record"
    try:
        warning: RiskWarning | None = visible.risk_warning_on(day)
    except NameHistoryHorizonError:
        warning = None
    if warning is RiskWarning.delisting_process:
        return "delisting"
    if warning in _ST_WARNINGS:
        return "st_by_name"
    # Nothing in force warns (or nothing is in force yet): a rename already announced as ST
    # still counts, since it is public information on the signal day.
    if any(record.risk_warning in _ST_WARNINGS for record in visible.pending_at(day)):
        return "st_pending"
    return "unknown_before_first" if warning is None else "ordinary"


def listing_age_sessions(
    *,
    listed_on: date | None,
    calendar_first: date,
    day: date,
    sessions_between: Callable[[date, date], int],
    required_lookback: int,
) -> int | None:
    """Open sessions from the listing day through `day`, both inclusive.

    `sessions_between(first, last)` counts the open sessions in the closed interval; with a
    `TradingCalendar` it is `len(calendar.trading_days_between(first, last))`.
    `required_lookback` is the minimum listing age the screen will apply -- the adapter passes
    `SurvivorBookConfig.min_listing_sessions` -- and has no default, so the bound a truncated
    count is held to always follows the registration rather than a constant beside it.

    - `listed_on is None` (the registry has no listing row) gives `None`.
    - A listing after `day` has had no session yet and gives `0`.
    - A listing before `calendar_first` is counted from `calendar_first`: a lower bound on the
      true age. It is returned only when it is at least `required_lookback`, where the verdict
      "old enough" is exact; a shorter one raises `ListingAgeHorizonError` rather than present
      a calendar artefact as a young security.
    """
    if listed_on is None:
        return None
    if listed_on > day:
        return 0
    if listed_on >= calendar_first:
        return sessions_between(listed_on, day)
    if day < calendar_first:
        raise ListingAgeHorizonError(
            f"the calendar starts {calendar_first.isoformat()}, after the signal day "
            f"{day.isoformat()}; a listing from {listed_on.isoformat()} cannot be aged from it"
        )
    available = sessions_between(calendar_first, day)
    if available < required_lookback:
        raise ListingAgeHorizonError(
            f"the calendar shows only {available} sessions "
            f"({calendar_first.isoformat()}..{day.isoformat()}), fewer than the "
            f"{required_lookback} needed to age a listing from "
            f"{listed_on.isoformat()} that predates it; counting from its start would report "
            "an old security as a young one"
        )
    return available


def assemble_fact(
    *,
    ts_code: str,
    board: Board,
    name_status: NameStatus,
    listed_sessions: int | None,
    suspended: bool,
    close: Decimal,
    avg_amount: Decimal,
    oriented_values: Sequence[float | None],
) -> SecurityFacts:
    """One `SecurityFacts` row from the adapter's already-resolved values.

    `unknown` is treated as ordinary; `st` and `delisting` are both special treatment (the
    screen has one reason for them). No listing row (`listed_sessions is None`) is zero
    sessions, so the screen reports `listing_too_recent`. Everything else passes through, and
    `SecurityFacts` itself rejects a non-positive close, a negative amount or age, a factor
    value that is not finite, and a board outside its vocabulary.
    """
    if name_status not in _NAME_STATUSES:
        raise SurvivorFactsError(
            f"unrecognised name status {name_status!r}; expected one of {_NAME_STATUSES}"
        )
    return SecurityFacts(
        ts_code=ts_code,
        board=board,
        listed_sessions=0 if listed_sessions is None else listed_sessions,
        special_treatment=name_status in _SPECIAL_TREATMENT_STATUSES,
        suspended=suspended,
        close=close,
        avg_amount=avg_amount,
        oriented_values=tuple(oriented_values),
    )


def require_coverage(*, scored_size: int, universe_size: int, min_coverage: float) -> None:
    """Refuse a day on which fewer than `min_coverage` of the rows are scored.

    `universe_size` is the day's rows on the boards the book may trade, and `scored_size` those
    of them whose security has a bar on the day and every factor value; see the module docstring
    for why a carried row never counts and why other boards do not.

    Exact: the comparison is on `Fraction`s, so a coverage exactly at the threshold passes. An
    empty day is refused whatever the threshold -- a screen of nothing would sell everything.
    """
    _require_coverage_threshold(min_coverage)
    if universe_size <= 0 or Fraction(scored_size) < Fraction(min_coverage) * universe_size:
        raise FactsCoverageError(
            f"{scored_size} of {universe_size} securities on the boards the book trades have a "
            f"bar and every factor value, below min_coverage {min_coverage}; a screen that "
            "cannot see most of the market would sell every holding it cannot see, so the day "
            "is refused instead"
        )


def _require_coverage_threshold(min_coverage: float) -> None:
    if not math.isfinite(min_coverage) or not 0 <= min_coverage <= 1:
        raise SurvivorFactsError(f"min_coverage must lie between 0 and 1, not {min_coverage}")


@dataclass(frozen=True, slots=True)
class FactsDay:
    """One signal day's facts, ready for `domain.survivor_screen.screen`.

    It checks its own counts on construction and refuses with `SurvivorFactsError` a day whose
    rows are not strictly ascending by `ts_code` (so each code once), whose `universe_size` is
    not the number of rows on `boards`, or whose `scored_size` is negative or more than the rows
    on `boards` carrying every factor value (so never more than `universe_size`).
    """

    session: date
    facts: tuple[SecurityFacts, ...]
    """One row per listed security on every board (halted ones included), ascending by
    `ts_code`; see `build_facts_day` for the securities that have none."""
    notes: FactsNotes
    boards: tuple[Board, ...]
    """The boards the book may trade (`SurvivorBookConfig.boards`): the two sizes count only the
    rows on them."""
    universe_size: int
    """The rows whose `board` is in `boards` -- the coverage guard's denominator."""
    scored_size: int
    """Of those, the rows whose security has a bar on the day and every factor value -- the
    coverage guard's numerator. A carried row (no bar) is never scored."""

    def __post_init__(self) -> None:
        codes = [fact.ts_code for fact in self.facts]
        out_of_order = [(earlier, later) for earlier, later in pairwise(codes) if later <= earlier]
        if out_of_order:
            earlier, later = out_of_order[0]
            raise SurvivorFactsError(
                f"facts must be strictly ascending by ts_code, one row per code; {later} follows "
                f"{earlier}"
            )
        allowed = [fact for fact in self.facts if fact.board in self.boards]
        if self.universe_size != len(allowed):
            raise SurvivorFactsError(
                f"universe_size is {self.universe_size} but {len(allowed)} of the {len(codes)} "
                f"rows are on the boards {self.boards}"
            )
        complete = sum(1 for fact in allowed if None not in fact.oriented_values)
        if not 0 <= self.scored_size <= complete:
            raise SurvivorFactsError(
                f"scored_size is {self.scored_size}, outside 0..{complete}: only {complete} of "
                f"the {self.universe_size} rows on the boards {self.boards} carry every factor "
                "value"
            )


def build_facts_day(
    *,
    store: PanelStore,
    session: date,
    as_of: datetime,
    config: SurvivorBookConfig,
    factor_ids: Sequence[str],
    transform: FactorTransformSpec,
    exchange: str,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    max_carry_sessions: int = DEFAULT_MAX_CARRY_SESSIONS,
    held: Collection[str] = (),
) -> FactsDay:
    """Read `session` off the panel at `as_of` as one `SecurityFacts` row per listed security.

    Read-only. `factor_ids` must be `config.composite_factors` (the kernel pairs the values with
    them by position), `transform` the processed tier they were built under and `exchange` the
    calendar's; the three, `min_coverage` and `max_carry_sessions` come from the registration.
    `held` is the book's current holdings: a held security halted for longer than
    `max_carry_sessions` keeps its carried row, because its look-back is not bounded.

    Every listed security gets a row except one with no bar in reach -- for a code not in `held`
    none within `max_carry_sessions` sessions, for a held code none at all since its listing or
    the panel's first session -- which is counted in `FactsNotes.no_bar_within_carry`. **A held
    code that is listed on the day and has no row must be refused by the caller, by name**: the
    kernel would sell it as unassessed. (A held code that is not listed on the day -- delisted --
    has no row on purpose, and is sold.) See the module docstring for each rule; it raises the
    loaders' own refusals unchanged.
    """
    if tuple(factor_ids) != config.composite_factors:
        raise SurvivorFactsError(
            f"factor_ids {tuple(factor_ids)} are not the configuration's composite_factors "
            f"{config.composite_factors}; the screen pairs the values with them by position"
        )
    _require_coverage_threshold(min_coverage)
    if max_carry_sessions < 1:
        raise SurvivorFactsError(f"max_carry_sessions must be at least 1, not {max_carry_sessions}")
    if isinstance(held, str):
        raise SurvivorFactsError(
            f"held must be a collection of codes, not the one string {held!r}, whose characters "
            "would each be read as a code"
        )
    published = session_publication_instant(session)
    if as_of < published:
        raise SurvivorFactsError(
            f"{session.isoformat()} publishes at {published.isoformat()} (16:30 Asia/Shanghai) "
            f"and the read is at {as_of.isoformat()}; its facts did not exist yet"
        )
    with store.reading():
        return _read_facts_day(
            store=store,
            session=session,
            as_of=as_of,
            config=config,
            factor_ids=tuple(factor_ids),
            transform=transform,
            exchange=exchange,
            min_coverage=min_coverage,
            max_carry_sessions=max_carry_sessions,
            held=frozenset(held),
            published=published,
        )


def _read_facts_day(
    *,
    store: PanelStore,
    session: date,
    as_of: datetime,
    config: SurvivorBookConfig,
    factor_ids: tuple[str, ...],
    transform: FactorTransformSpec,
    exchange: str,
    min_coverage: float,
    max_carry_sessions: int,
    held: frozenset[str],
    published: datetime,
) -> FactsDay:
    calendar = load_trading_calendar(
        store,
        exchange=exchange,
        years=_years_through(store, TRADING_CALENDAR_DATASET, session.year),
        as_of=as_of,
    )
    if not calendar.is_trading_day(session):
        raise SurvivorFactsError(f"{session.isoformat()} is not an open {exchange} session")
    # The session's lifecycle year; the loader reads every stored year beneath it by itself.
    registry = load_stock_universe(store, years=(session.year,), as_of=as_of, max_staleness=None)
    histories = load_name_histories(
        store,
        years=_years_through(store, NAMECHANGE_DATASET, session.year),
        as_of=as_of,
        max_staleness=None,
    )
    halts = halt_corpus_for_years(
        load_suspensions(store, years=(session.year,), as_of=as_of, max_staleness=None),
        years=(session.year,),
    )
    limits = load_price_limits(
        store, day=session, calendar=calendar, as_of=as_of, max_staleness=None
    )
    values = [
        _oriented_column(store, factor_id, transform, year=session.year, as_of=as_of, at=published)
        for factor_id in factor_ids
    ]

    first = max(
        calendar.horizon.first_date,
        date(_years_through(store, DAILY_DATASET, session.year)[0], 1, 1),
    )
    history = calendar.trading_days_between(first, session)
    listed_on = {entry.ts_code: entry.listed_on for entry in registry.securities}
    bars = _Walk(
        store=store,
        calendar=calendar,
        as_of=as_of,
        history=history,
        listed=frozenset(registry.listed_on(session)),
        listed_on=listed_on,
        held=held,
        max_carry_sessions=max_carry_sessions,
    )

    facts: list[SecurityFacts] = []
    reasons: list[NameStatusReason] = []
    carried = no_bar = no_listing_row = universe = scored = 0
    for code in bars.codes:
        bar = bars.today.get(code)
        last = bar if bar is not None else bars.last_bar.get(code)
        if last is None:
            no_bar += 1
            continue
        oriented = [column.get(code) for column in values]
        if bar is None:
            carried += 1
            suspended = True
        else:
            suspended = suspended_at_the_close(
                halts.state_on(session, code), halts.timing_on(session, code)
            )
        amounts = bars.amounts[code]
        board = security_board(code)
        reason = name_status_reason(
            history=histories.get(code),
            band_is_five_percent=_band_is_five_percent(board, bar, limits.get(code)),
            day=session,
        )
        reasons.append(reason)
        if code not in listed_on:
            no_listing_row += 1
        facts.append(
            assemble_fact(
                ts_code=code,
                board=board,
                name_status=_STATUS_OF_REASON[reason],
                listed_sessions=listing_age_sessions(
                    listed_on=listed_on.get(code),
                    calendar_first=calendar.horizon.first_date,
                    day=session,
                    sessions_between=lambda start, end: len(
                        calendar.trading_days_between(start, end)
                    ),
                    required_lookback=config.min_listing_sessions,
                ),
                suspended=suspended,
                close=Decimal(str(last.close)),
                avg_amount=sum(amounts, Decimal(0)) / len(amounts),
                oriented_values=oriented,
            )
        )
        if board in config.boards:
            universe += 1
            if bar is not None and all(value is not None for value in oriented):
                scored += 1

    require_coverage(scored_size=scored, universe_size=universe, min_coverage=min_coverage)
    return FactsDay(
        session=session,
        facts=tuple(facts),
        notes=FactsNotes.tally(
            reasons=reasons,
            suspended_without_bar=carried,
            no_listing_row=no_listing_row,
            no_bar_within_carry=no_bar,
            not_listed_with_bar=bars.not_listed_with_bar,
        ),
        boards=config.boards,
        universe_size=universe,
        scored_size=scored,
    )


def _years_through(store: PanelStore, dataset: str, year: int) -> tuple[int, ...]:
    """Every year from the first one `dataset` holds through `year`, contiguous.

    Named as a contiguous range on purpose: a year the store never ingested is then refused by
    the loader as missing rather than skipped.
    """
    held = [stored for stored in store.registered_years(dataset) if stored <= year]
    if not held:
        raise SurvivorFactsError(f"the panel holds no {dataset} partition at or before {year}")
    return tuple(range(held[0], year + 1))


def _oriented_column(
    store: PanelStore,
    factor_id: str,
    transform: FactorTransformSpec,
    *,
    year: int,
    as_of: datetime,
    at: datetime,
) -> dict[str, float]:
    """One factor's admitted processed values in the build stamped at `at`, higher-is-better.

    No row at all means no build at `at`, which is refused by name: the daily build did not run
    for this factor, and the coverage guard's "0 of N" would hide which one and when.
    """
    definition = FACTOR_DEFINITIONS.get(factor_id)
    sign = -1.0 if definition.direction == "lower_is_better" else 1.0
    admitted = TIER_ADMITTED_CODES["processed"]
    rows = load_processed_factor_observations(
        store, definition, transform, years=(year,), as_of=as_of, at=at
    )
    if not rows:
        raise MissingFactorBuildError(
            f"{factor_id} has no {transform.qualified_key} build at {at.isoformat()}, the signal "
            "day's 16:30 instant the daily command builds at; the day cannot be screened "
            "without it"
        )
    return {
        row.subject: sign * row.value
        for row in rows
        if row.coverage in admitted and row.value is not None
    }


def _band_is_five_percent(
    board: Board, bar: DailyBar | None, limit: PriceLimit | None
) -> bool | None:
    """Whether today's published band is 5%: main board, a bar and a bounded band only."""
    if board != "main" or bar is None or limit is None or not limit.is_bounded(bar.pre_close):
        return None
    return abs(limit.implied_ratio(bar.pre_close) - FIVE_PERCENT_BAND) <= BAND_TOLERANCE


class _Walk:
    """The day's bars, and one walk back through earlier sessions for what it is missing.

    One read per session, newest first, and no session is kept after it is used: the 20-session
    amount window of every security trading today, and for each security with no bar today its
    last bar and up to 20 bars back from it. Both are looked for within `max_carry_sessions`
    sessions of the signal day, or for a code in `held` as far back as `history` goes. The walk
    stops as soon as nothing is missing, and never asks about a session before a security listed.
    """

    def __init__(
        self,
        *,
        store: PanelStore,
        calendar: TradingCalendar,
        as_of: datetime,
        history: Sequence[date],
        listed: frozenset[str],
        listed_on: Mapping[str, date],
        held: frozenset[str],
        max_carry_sessions: int,
    ) -> None:
        def read(day: date) -> dict[str, DailyBar]:
            return load_daily_bars(
                store,
                day=day,
                calendar=calendar,
                as_of=as_of,
                max_staleness=None,
            )

        self.today = read(history[-1])
        # A bar brings a security in only when the registry has never heard of it: a code it
        # knows and does not list that day (delisted, or priced before its listing) stays out,
        # and is counted.
        unregistered = {code for code in self.today if code not in listed_on}
        self.not_listed_with_bar = sum(
            1 for code in self.today if code in listed_on and code not in listed
        )
        self.codes = tuple(sorted(listed | unregistered))
        trading = [code for code in self.codes if code in self.today]
        halted = [code for code in self.codes if code not in self.today]
        self.last_bar: dict[str, DailyBar] = {}
        self.amounts: dict[str, list[Decimal]] = {code: [] for code in self.codes}

        def wanted(code: str, depth: int, day: date) -> bool:
            if day < listed_on[code]:
                return False
            # The last bar and the 20-bar window share one reach: the carry bound, or for a held
            # code the whole history.
            if depth > max_carry_sessions and code not in held:
                return False
            return code not in self.last_bar or len(self.amounts[code]) < AMOUNT_WINDOW_SESSIONS

        for depth, day in enumerate(reversed(history)):
            window = depth < AMOUNT_WINDOW_SESSIONS
            missing = [code for code in halted if depth > 0 and wanted(code, depth, day)]
            if not window and not missing:
                break
            bars = self.today if depth == 0 else read(day)
            for code in (*(trading if window else ()), *missing):
                bar = bars.get(code)
                if bar is not None:
                    self.last_bar.setdefault(code, bar)
                    self.amounts[code].append(Decimal(str(bar.amount)))
