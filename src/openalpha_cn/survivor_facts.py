"""The panel-to-facts adapter of the survivor book (`V2-P7-016`).

The survivor-book kernel (`domain/survivor_*.py`) screens a list of `SecurityFacts` and knows
nothing about where they came from. This module is the other half: it turns what the panel
holds on one signal day -- a name history, a listing date, a halt flag, a price, a few factor
values -- into one `SecurityFacts` row per listed security. T3
(`docs/research/p7-step0-validation/T3-st-and-new-listings.md`) is the measured basis for
every rule below.

This first part holds only the **pure** functions: they take plain values and a name history,
read nothing, and import no numeric or infrastructure library. The panel reads that feed them
join this module in later parts of the same issue.

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

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal, get_args

from openalpha_cn.domain.name_history import (
    NameHistory,
    NameHistoryHorizonError,
    RiskWarning,
)
from openalpha_cn.domain.survivor_book import Board, SecurityFacts

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


@dataclass(frozen=True, slots=True)
class FactsNotes:
    """What the adapter assumed or noticed while it built one day's facts.

    Counts only: they land in the daily log so an unusual day shows up as a number, not as a
    silent change in who was eligible. `st` and `name_unknown_assumed_ordinary` are totals, and
    each is refused unless its parts add up to it; build the name counts with `tally`.
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
    ) -> "FactsNotes":
        """One day's notes from every fact's name-status reason and the two other counts."""
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
