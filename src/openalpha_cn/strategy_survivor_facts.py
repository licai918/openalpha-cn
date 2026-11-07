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

**The registry's year.** `stock_basic` is filed by lifecycle year, and `write_stock_universe`
holds a partition for a year only once something listed or died in it by the fetch's `as_of`. A
year's first sessions therefore used to have no partition for the session's year although
nothing was missing; `V2-P7-025` closed that at the writer, `panel build` recording the fetch's
observation year as a zero-row partition when the whole-registry fetch carries no row for it
(`panel_ingest.record_empty_universe_year`, `cli._write_registry_year`). The registry is still
read through the newest lifecycle year the store holds at or before the session's (the loader
reads every stored year beneath it), which is the session's own year whenever that is stored --
the normal case now. A store without that year (one built before the record) still takes the
fall-back, and a fall-back to an earlier year is accepted only from a registry fetched at or
after the session's 16:30 publication -- what step 2 of the daily command writes for the
session -- and refused by name (`StaleRegistryError`) otherwise; a store with no lifecycle year
at or before the session's has no registry and is refused too (`_registry_year`).

**A main board the adapter recognises.** `backtest.execution.security_board` files every prefix
it does not recognise under the main board, which fails open for a main-board-only book: a new
ChiNext prefix would be bought as main board, as `302*` once was. So a code counts as main board
here only when `security_board` says so **and** its prefix, with its exchange, is a pair in
`MAIN_BOARD_PREFIXES` (`600` on SH, not on SZ); one it calls main with any other prefix, or a
main-board prefix on the other exchange, gets no row, and is counted and named
(`FactsNotes.unrecognised_board`, `unrecognised_board_codes`). An excluded code is counted once,
by the first rule that excludes it: not listed (or not in the universe at all), then the board,
then no bar in reach.

- **A halted security with a bar in reach still gets its row** (T6/T7 0(d)): `suspended=True`,
  its last close, and the mean amount of its last bars within that reach -- up to 20, fewer for
  a security not held whose reach is bounded (next bullet). Without the row the kernel would
  sell a halted holding as unassessed instead of freezing it.
- **How far back a halted security is looked for.** For a code in `held` (the book's holdings)
  the last bar and the 20 bars are looked for as far back as the panel goes -- to the listing
  day, or to the first session the bar panel holds: a holding halted for a year must still be
  frozen, not sold. For every other security both are looked for at most `max_carry_sessions`
  sessions back. Its `avg_amount` is then the mean of the bars within that reach, which may be
  fewer than 20 (at least the last one); and one with no bar at all in reach gets no row,
  counted as `FactsNotes.no_bar_within_carry`. Nothing is lost by that: such a security is not
  held, and a row could only have said `suspended`, which keeps it out of every population the
  screen ranks.
- **What the held look-back costs.** It reads the bar panel one session at a time, newest
  first, and stops for a held code once it has the last bar and 20 bars. A holding halted for
  250 sessions is about 250 more reads than an unheld day (some 10 s, the A2 implementer's own
  estimate, not a measurement and not in the plan). That is the cost of a held code that has
  bars, not the bound. The worst case is a held code the registry lists and that has **no bar
  anywhere** in the panel: nothing stops the walk before its listing day or the first session
  of the earliest daily-bar partition year (or the calendar's first day, if later), so on a
  store whose bars go back twenty years that is about 5,000 reads -- minutes at that rate --
  on a day the caller then refuses anyway (`no_bar_in_reach`). That stop is already the one the
  panel's partitions give for free; stopping earlier without changing any output would need to
  know, without reading them, which sessions a code has bars on.
- **Every held code is told what became of it** (`FactsDay.held_status`), because the caller
  acts on each one: `row` -- it has a fact row; `no_bar_in_reach` -- listed, and never a bar
  since its listing or the panel's first session, so no close to carry and no row (the kernel
  would sell it as unassessed: the caller refuses the day by name); `delisted` -- the registry
  knows it and dates its delisting on or before the day, no row on purpose, so the kernel sells
  it; `not_yet_listed` -- the registry knows it and dates its listing **after** the day (a re-read
  of a past day, made after the listing row was published, or a `.BJ` code priced before its
  listing date), no row: for a name already held that is a registry inconsistency, not a
  delisting, and a write-off of the holding is never to be taken from it; `not_in_registry` --
  the registry has no listing row for it at all, whether or not a bar on the day gave it a row
  aged 0 (the caller refuses the day by name: a typo or a restated code); `board_unrecognised`
  -- see above, no row. A registry row that ends before it begins (a delisting on or before the
  day and a listing after it; `build_stock_universe` refuses one, a hand-built `StockUniverse`
  does not) is neither of the pair, and a held code with one refuses the day by name instead of
  getting a status. `held` itself is part of the day, spelled exactly (`MalformedHeldCodeError`
  otherwise): a misspelled holding would match nothing and pass as unheld.
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
  published bounded band (controller decision 1); elsewhere it is `None`. It is a witness only
  for sessions before 2025-07-07 (next section), and answers `False` for every main-board
  security after it.
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
- **By the exchange band, as a union -- before 2025-07-07 only.** Until then a main-board 5%
  band was the exchange's own statement that a security is under special treatment,
  independent of the name corpus (T3 2.2: 6,674 of 6,675 name-ST rows had it, and the 50 rows
  the names missed were all one security whose first name row arrived in 2021; the T3 script
  header dates the witness "before 2025-07"). `band_is_five_percent=True` therefore makes a
  security `st` unless its name already says `delisting`. The caller passes `True` only for
  the main board: on the other boards the band is no independent witness (T3 2.2).

  **After 2025-07-07 the band is not a witness.** From that session the main board's
  risk-warning securities trade under a 10% band, the same as every other main-board security,
  so `_band_is_five_percent` can only answer `False` for a main-board security (and `None` for
  every other board) and the union adds nothing to the name. The forward window the paper book
  runs in lies wholly after that date: there the name corpus alone says `st`, and
  `FactsNotes.st_by_band` is structurally 0 -- a 0 there is not the band agreeing with the
  names, it is the band no longer saying anything. T3
  measured the band as a witness over 2017-01-03..2021-10-14 only; nothing here measures it on
  or after 2025-07-07, so the name is the only evidence of "is it ST" for the book's own days
  (the name's own gaps are the `unknown` verdicts counted below).
- **`unknown`** is returned only when neither can say: no history, nothing announced yet, or no
  record in force yet and no pending ST name, and no band that says otherwise (from 2025-07-07
  on, no band ever does). The adapter
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
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from types import MappingProxyType
from typing import Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

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
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET, SecurityLifecycle
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
  band is 5%. The panel path reaches it only for sessions before 2025-07-07: from then on
  main-board risk-warning securities trade under a 10% band, so the band it reads is never 5%.
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


class MalformedHeldCodeError(SurvivorFactsError):
    """A held code is not spelled as a security code, so it could match no security."""


class StaleRegistryError(SurvivorFactsError):
    """The session's year has no registry partition and the newest stored one was fetched before
    the session published, so no fetch since then says nothing listed or died in between."""


HeldStatusValue = Literal[
    "row",
    "no_bar_in_reach",
    "delisted",
    "not_yet_listed",
    "not_in_registry",
    "board_unrecognised",
]
"""What became of one held code on the signal day; see `HeldStatus`."""

_HELD_STATUSES_WITH_A_ROW: Final[frozenset[str]] = frozenset({"row", "not_in_registry"})
"""The statuses a held code with a fact row can have: `not_in_registry` is about the registry,
not the row, and a code it has never heard of has one when it has a bar on the day. `delisted` and
`not_yet_listed` say the registry knows the code and does not list it on the day, which is why it
has none."""


class HeldStatus(BaseModel):
    """What became of one held code on the signal day; `FactsDay.held_status` has one each.

    - `row`: the code has a fact row, whatever the screen will say of it.
    - `no_bar_in_reach`: the registry lists it on the day and there is no bar of it back to its
      listing day or the panel's first session -- no close to carry, so no row. The kernel would
      sell it as unassessed; the caller refuses the day by name.
    - `delisted`: the registry knows it, it had listed by the day, and its delisting is dated on
      or before the day (the date is exclusive: the first day it is gone) -- no row on purpose;
      the kernel sells it. The one status a write-off of a holding may be taken from.
    - `not_yet_listed`: the registry knows it and dates its listing after the day, and no
      delisting on or before it -- no row, with a bar on the day or without. The registry says the
      code had not listed yet, so it is not a delisting and a holding is never written off as one.
    - `not_in_registry`: the registry has no listing row for it at all. With a bar on the day it
      keeps the row such a code always gets (aged 0, `listing_too_recent`); without one it has
      none. Either way the caller refuses the day by name: a holding the registry has never heard
      of is a typo or a restated code, not a security to sell.
    - `board_unrecognised`: `security_board` calls it main board and its prefix, with its
      exchange, is not a pair in `MAIN_BOARD_PREFIXES` -- no row.

    The tests are made in this order (`_held_status`): `not_in_registry` first -- a code the
    registry has no row for is `not_in_registry` whatever else is true of it -- then `row`, then
    the pair `delisted` / `not_yet_listed`, then `board_unrecognised`, and `no_bar_in_reach` for
    what is left. That is not the order the statuses are listed in above.

    A known code is never both of the pair and never `delisted` on a hunch. A row whose dates say
    it ended on or before the day **and** had not begun by it -- which `build_stock_universe`
    refuses, and a hand-built `StockUniverse` does not -- has no true status, so it has none:
    `build_facts_day` refuses a day holding one by name (`SurvivorFactsError`). A status for it
    would be a value no registry the loaders return can produce, for a caller to handle or to
    miss.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    status: HeldStatusValue


AMOUNT_WINDOW_SESSIONS: Final[int] = 20
"""The sessions a trailing average amount is taken over (design section 6; T8's `a20`)."""

DEFAULT_MIN_COVERAGE: Final[float] = 0.5
"""`build_facts_day`'s coverage threshold unless the registration states another."""

DEFAULT_MAX_CARRY_SESSIONS: Final[int] = 250
"""How far back a halted security's last bar and its 20 bars are looked for, unless the
registration says; a held security's are not bounded."""

FIVE_PERCENT_BAND: Final[float] = 0.05
BAND_TOLERANCE: Final[float] = 0.0051
"""A main-board band within this of 5% is the exchange's own special-treatment band (T3 7) --
before 2025-07-07; the main board's risk-warning band is 10% from that session."""

MAIN_BOARD_PREFIXES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("600", "SH"),
        ("601", "SH"),
        ("603", "SH"),
        ("605", "SH"),
        ("000", "SZ"),
        ("001", "SZ"),
        ("002", "SZ"),
        ("003", "SZ"),
    }
)
"""The code prefixes of the main board, each with the exchange it is on, as T8 measured them
(`docs/research/p7-step0-validation/T8-main-board-remeasure.md`, its board table: SH
`600/601/603/605`, SZ `000/001/002/003`).

A code counts as main board here only when `security_board` says "main" **and** its first three
digits and its exchange suffix are one of these pairs. `security_board` answers "main" for any
prefix it does not recognise, so without this a main-board-only book would buy a new board's
codes as main board, as it once would have bought `302*`. The exchange is part of the pair
because a prefix alone is not the board: no `600*.SZ` stock exists, `000*.SH` is how Shanghai's
index codes are spelled (`000300.SH`), and a code paired otherwise than T8 measured is a typo, a
restated code or something new -- not a main-board security, so it gets the unrecognised-prefix
treatment. Growing this set is a decision, not a fix: a pair joins it once the exchange's own
classification of it has been checked, and the test that pins the set changes with it."""

_SECURITY_CODE: Final[re.Pattern[str]] = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)")
"""How a held code must be spelled, matched whole (`fullmatch`): six ASCII digits, a dot and the
exchange. `[0-9]` and `fullmatch` rather than `\\d` and `$`, which would admit non-ASCII digits
and a trailing newline."""


@dataclass(frozen=True, slots=True)
class FactsNotes:
    """What the adapter assumed or noticed while it built one day's facts.

    Counts, and the codes behind one of them: they land in the daily log so an unusual day shows
    up as a number, not as a silent change in who was eligible. `st` and
    `name_unknown_assumed_ordinary` are totals, and each is refused unless its parts add up to
    it; `unrecognised_board` is refused unless it counts `unrecognised_board_codes`, which must be
    ascending, each code once. Build the notes with `tally`.

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
    """Securities that are ST by name, by pending name or (before 2025-07-07 only) by the 5%
    band."""
    st_by_name: int
    """Of `st`: an ST name is in force."""
    st_pending: int
    """Of `st`: only because an announced ST rename is still pending (decided early)."""
    st_by_band: int
    """Of `st`: only because the main-board band is 5%. Structurally 0 for every session on or
    after 2025-07-07, when the main board's risk-warning securities moved to a 10% band: read
    there, 0 is **not** the band agreeing with the names, it is the band no longer answering. Only
    for earlier sessions does a 0 here say the names missed nothing the band saw."""
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
    unrecognised_board: int
    """Securities `security_board` calls main board whose prefix, with its exchange, is not a pair
    in `MAIN_BOARD_PREFIXES`, which therefore have no fact row; `unrecognised_board_codes` names
    them."""
    unrecognised_board_codes: tuple[str, ...]
    """The codes `unrecognised_board` counts, ascending: a new prefix is a decision to take, so
    the log names it."""

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, int) and value < 0:
                raise SurvivorFactsError(f"{item.name} is a count and cannot be negative")
        codes = self.unrecognised_board_codes
        if not isinstance(codes, tuple) or list(codes) != sorted(set(codes)):
            raise SurvivorFactsError(
                f"unrecognised_board_codes must be a tuple ascending by code, each code once, "
                f"not {codes!r}"
            )
        if self.unrecognised_board != len(codes):
            raise SurvivorFactsError(
                f"unrecognised_board is {self.unrecognised_board} but {len(codes)} code(s) are "
                f"named: {codes}"
            )
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
        unrecognised_board_codes: Iterable[str],
    ) -> "FactsNotes":
        """One day's notes from every fact's name-status reason, the four other counts and the
        codes left out for their board (named ascending, and counted)."""
        board_codes = tuple(sorted(unrecognised_board_codes))
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
            unrecognised_board=len(board_codes),
            unrecognised_board_codes=board_codes,
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
    band is `st_by_name`, and the band never overrides `delisting`. The panel path
    (`build_facts_day`) cannot pass `True` for a session on or after 2025-07-07, when the band is
    10% for the whole main board (module docstring); this function, which reads no band itself,
    would still say `st_by_band` for a caller who did.
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
    on `boards` carrying every factor value (so never more than `universe_size`). It refuses a
    `held` that is not strictly ascending, a `held_status` that is not one entry per code of
    `held` in its order, a `row` status for a code with no row, and any status but `row` or
    `not_in_registry` for a code with one.
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
    held: tuple[str, ...]
    """The codes the caller passed as `held`, ascending, each once. They change the day (a held
    code's look-back is not bounded), so they are part of its bytes: the caller records them with
    the day's inputs and passes the same codes to any re-read of the day."""
    held_status: tuple[HeldStatus, ...]
    """What became of each code of `held`, one entry each in `held`'s order; see `HeldStatus`."""

    def __post_init__(self) -> None:
        self._check_rows()
        self._check_held()

    def _check_rows(self) -> None:
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

    def _check_held(self) -> None:
        if list(self.held) != sorted(set(self.held)):
            raise SurvivorFactsError(
                f"held must be strictly ascending, each code once, not {self.held}"
            )
        described = tuple(status.ts_code for status in self.held_status)
        if described != self.held:
            raise SurvivorFactsError(
                f"held_status must give one status per held code, in held's order: held is "
                f"{self.held}, held_status describes {described}"
            )
        rows = {fact.ts_code for fact in self.facts}
        for status in self.held_status:
            has_row = status.ts_code in rows
            if status.status == "row" and not has_row:
                raise SurvivorFactsError(
                    f"held code {status.ts_code} has the status 'row' and no fact row"
                )
            if has_row and status.status not in _HELD_STATUSES_WITH_A_ROW:
                raise SurvivorFactsError(
                    f"held code {status.ts_code} has a fact row and the status "
                    f"{status.status!r}, which says it has none"
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
    `held` is the book's current holdings, each spelled as a security code (six ASCII digits, a
    dot, `SH`, `SZ` or `BJ`; `MalformedHeldCodeError` otherwise): a held security halted for
    longer than `max_carry_sessions` keeps its carried row, because its look-back is not bounded
    (the module docstring states what that costs, the worst case included).

    Every listed security gets a row except one on a board the adapter does not recognise
    (`MAIN_BOARD_PREFIXES`) and one with no bar in reach -- for a code not in `held` none within
    `max_carry_sessions` sessions, for a held code none at all since its listing or the panel's
    first session -- which is counted in `FactsNotes.no_bar_within_carry`. `FactsDay.held_status`
    says what became of every held code; **the caller refuses by name a day on which a held
    code is `no_bar_in_reach` or `not_in_registry`**, and lets the kernel sell one that is
    `delisted`; a `not_yet_listed` one is not a delisting. A held code whose registry row ends
    before it begins refuses the day (`SurvivorFactsError`, naming it). A session whose year has
    no registry partition yet is read through the newest
    stored lifecycle year, when the registry was fetched at or after the session's publication,
    and refused by name (`StaleRegistryError`) when it was not. See the module docstring for each
    rule; it raises the loaders' own refusals unchanged.
    """
    if tuple(factor_ids) != config.composite_factors:
        raise SurvivorFactsError(
            f"factor_ids {tuple(factor_ids)} are not the configuration's composite_factors "
            f"{config.composite_factors}; the screen pairs the values with them by position"
        )
    _require_coverage_threshold(min_coverage)
    if max_carry_sessions < 1:
        raise SurvivorFactsError(f"max_carry_sessions must be at least 1, not {max_carry_sessions}")
    held_codes = _held_codes(held)
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
            held=held_codes,
            published=published,
        )


def _held_codes(held: Collection[str]) -> tuple[str, ...]:
    """`held` ascending and each code once, or a refusal naming every misspelled code."""
    if isinstance(held, str):
        raise SurvivorFactsError(
            f"held must be a collection of codes, not the one string {held!r}, whose characters "
            "would each be read as a code"
        )
    malformed = sorted(
        {
            repr(code)
            for code in held
            if not isinstance(code, str) or _SECURITY_CODE.fullmatch(code) is None
        }
    )
    if malformed:
        raise MalformedHeldCodeError(
            f"held code(s) {', '.join(malformed)} are not spelled as a security code -- six "
            "digits, a dot and SH, SZ or BJ -- so each would match no security and the holding "
            "would pass as not held"
        )
    return tuple(sorted(set(held)))


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
    held: tuple[str, ...],
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
    # The newest stored lifecycle year at or before the session's, which need not be the
    # session's own (`_registry_year`); the loader reads every stored year beneath it by itself.
    registry = load_stock_universe(
        store,
        years=(_registry_year(store, session=session, published=published),),
        as_of=as_of,
        max_staleness=None,
    )
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
    lifecycle = {entry.ts_code: entry for entry in registry.securities}
    listed_on = {code: entry.listed_on for code, entry in lifecycle.items()}
    listed = frozenset(registry.listed_on(session))
    bars = _Walk(
        store=store,
        calendar=calendar,
        as_of=as_of,
        history=history,
        listed=listed,
        listed_on=listed_on,
        held=frozenset(held),
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
    rows = frozenset(fact.ts_code for fact in facts)
    return FactsDay(
        session=session,
        facts=tuple(facts),
        notes=FactsNotes.tally(
            reasons=reasons,
            suspended_without_bar=carried,
            no_listing_row=no_listing_row,
            no_bar_within_carry=no_bar,
            not_listed_with_bar=bars.not_listed_with_bar,
            unrecognised_board_codes=bars.unrecognised_board,
        ),
        boards=config.boards,
        universe_size=universe,
        scored_size=scored,
        held=held,
        held_status=tuple(
            HeldStatus(
                ts_code=code,
                status=_held_status(
                    code,
                    rows=rows,
                    lifecycle=lifecycle,
                    session=session,
                    unrecognised_board=bars.unrecognised_board,
                ),
            )
            for code in held
        ),
    )


def _held_status(
    code: str,
    *,
    rows: Collection[str],
    lifecycle: Mapping[str, SecurityLifecycle],
    session: date,
    unrecognised_board: Collection[str],
) -> HeldStatusValue:
    """What became of one held code; the order of the tests is `HeldStatus`'s.

    A known code is judged by its own two dates against `session`, the same interval
    `StockUniverse.listed_on` tests (`listed_on <= session < delisted_on`): it **began** when
    `listed_on <= session` and **ended** when `delisted_on <= session`. Began and not ended is
    listed; began and ended is `delisted`; neither is `not_yet_listed`. Ended without having begun
    is none of them -- `build_stock_universe` refuses `delisted_on <= listed_on`, so no loader
    returns it, but `StockUniverse`'s constructor validates nothing -- and it is refused by name
    rather than called `delisted`, which would hand a write-off a code that never listed.
    """
    entry = lifecycle.get(code)
    if entry is None:
        return "not_in_registry"
    if code in rows:
        return "row"
    began = entry.listed_on <= session
    delisted_on = entry.delisted_on
    if delisted_on is not None and delisted_on <= session:
        if not began:
            raise SurvivorFactsError(
                f"held code {code}'s registry row ends before it begins: listed on "
                f"{entry.listed_on.isoformat()} and delisted on {delisted_on.isoformat()}, read "
                f"on {session.isoformat()}; it is neither delisted nor not yet listed, and a "
                "write-off of the holding must not be taken from it"
            )
        return "delisted"
    if not began:
        return "not_yet_listed"
    if code in unrecognised_board:
        return "board_unrecognised"
    return "no_bar_in_reach"


def _years_through(store: PanelStore, dataset: str, year: int) -> tuple[int, ...]:
    """Every year from the first one `dataset` holds through `year`, contiguous.

    Named as a contiguous range on purpose: a year the store never ingested is then refused by
    the loader as missing rather than skipped.
    """
    held = [stored for stored in store.registered_years(dataset) if stored <= year]
    if not held:
        raise SurvivorFactsError(f"the panel holds no {dataset} partition at or before {year}")
    return tuple(range(held[0], year + 1))


def _registry_year(store: PanelStore, *, session: date, published: datetime) -> int:
    """The lifecycle year the registry is read through: the newest one the store holds at or
    before `session`'s, so `session.year` itself whenever it is stored -- and then the read is the
    one it always was, its fetch instant not judged.

    `stock_basic` is filed by lifecycle year (`panel_ingest.write_stock_universe`), and a fetch
    holds a partition for a year only once something listed or died in it by the fetch's
    `as_of`. Before `V2-P7-025` a year's first sessions, until its first listing or delisting,
    left the store step 2 produces with no partition for `session.year`, and naming that year
    was refused as `partition_missing` although nothing was missing. A `025` build records that
    year empty (`panel_ingest.record_empty_universe_year`), so the fall-back now serves a store
    built without that record rather than every new year. Naming the newest stored year instead
    reads what a `session.year` partition would have added nothing to: the loader reads every
    stored year beneath the one named, and a fetch made at or after `published` (below) that
    holds no `session.year` row has none dated on or before the session.

    What the missing year used to refuse along with that -- a store no fetch has written since
    the session -- is judged on the newest year's fetch instant instead, its coverage `as_of`.
    A fetch of the whole registry writes every lifecycle year it holds, and records its `as_of`
    on each one even when the rows are unchanged (`PanelStore.write_partitions`); the newest
    stored year is always among them, since a lifecycle year's rows only grow. Step 2 of the
    daily command fetches at the day's `as_of`, an instant at which the session is the newest
    one published, so at or after `published`. A newest year fetched before `published` was
    therefore last written by a fetch made before the session existed, which cannot say that
    nothing listed or died since: `StaleRegistryError`. The bound is `published` and not the
    read's `as_of`, so that a later re-read of the day passes as the evening's did.

    A store with no lifecycle year at or before `session.year` has no registry that reaches the
    session, and no security could be listed on it: refused by name.
    """
    stored = [year for year in store.registered_years(STOCK_BASIC_DATASET) if year <= session.year]
    if not stored:
        raise SurvivorFactsError(
            f"the panel holds no {STOCK_BASIC_DATASET} partition at or before {session.year}: "
            "there is no security registry to list a security on "
            f"{session.isoformat()}; build it with `openalpha panel build --dataset "
            f"{STOCK_BASIC_DATASET} --year {session.year}`"
        )
    newest = max(stored)
    if newest == session.year:
        return newest
    coverage = store.read_coverage(STOCK_BASIC_DATASET, newest)
    fetched = None if coverage is None else coverage.as_of
    if fetched is None or fetched < published:
        when = (
            "has no coverage record to say when it was fetched"
            if fetched is None
            else f"was fetched at {fetched.astimezone(published.tzinfo).isoformat()}"
        )
        raise StaleRegistryError(
            f"the security registry ({STOCK_BASIC_DATASET}) holds no lifecycle year "
            f"{session.year}, and its newest, {newest}, {when}, before {session.isoformat()} "
            f"published at {published.isoformat()}: no fetch since then says that nothing listed "
            "or died in between. Fetch the registry for the session -- step 2 of the daily "
            f"command, or `openalpha panel build --dataset {STOCK_BASIC_DATASET} --year "
            f"{session.year} --as-of <an instant at or after {published.isoformat()}>` -- and "
            "read the day again"
        )
    return newest


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


def _board_recognised(code: str) -> bool:
    """False for a code `security_board` calls main board whose prefix and exchange are not a
    main-board pair (`MAIN_BOARD_PREFIXES`)."""
    _digits, _dot, exchange = code.rpartition(".")
    return security_board(code) != "main" or (code[:3], exchange) in MAIN_BOARD_PREFIXES


def _band_is_five_percent(
    board: Board, bar: DailyBar | None, limit: PriceLimit | None
) -> bool | None:
    """Whether today's published band is 5%: main board, a bar and a bounded band only.

    On or after 2025-07-07 the main board's risk-warning band is 10%, so this answers `False` for
    every main-board security there; the band is a witness of special treatment only before that.
    """
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
        # A code `security_board` calls main board by default stays out too, named: it is not
        # looked for, and gets no row.
        universe = listed | unregistered
        self.unrecognised_board = tuple(
            sorted(code for code in universe if not _board_recognised(code))
        )
        self.codes = tuple(sorted(universe.difference(self.unrecognised_board)))
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
