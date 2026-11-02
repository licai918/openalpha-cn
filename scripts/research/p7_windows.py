"""The windows a P7 diagnostic may read, and a panel store that cannot leave them (`V2-P7-004`).

The research protocol cuts 2015-01-05..2026-09-24 into three stretches and the diagnostics may look
at one of them by default:

* **research** 2015-01-05..2021-12-31 (`discovery`), last year 2021 -- the only stretch the
  exploratory diagnostics open without a ceremony;
* **validation** 2022-01-04..2023-12-29, last year 2023 -- spent only through
  `p7_reading.read_once`: a committed pre-registration, claimed before it is measured, usable once;
* **second use** 2024-01-02..2026-09-24 (`holdout`'s first day to the day P6's one-shot holdout
  ended), last year 2026 -- the same ceremony, and a secondary, non-confirmatory reading.

No P7 diagnostic is to use anything dated after 2026-09-24: confirmatory evidence comes only from
the forward record registered before its outcomes, which is the weekly report's business. That is
a rule for the readers and the planner below, not something the store enforces (see "What the
guard is, and is not").

`WindowCappedStore` is the guard. It is a `PanelStore` whose every read door first checks that the
year asked for is at most the window's last year, whose catalogue listings show only such years, and
whose every write door refuses. **There is no override parameter**: a store is built for a window,
and the only way past a window is a different window. The named ones are `WINDOWS`; a caller that
builds its own `DiagnosticWindow` (a test fixture does) can only build one whose year cap does not
exceed its own last year, but it can place that window anywhere, so a command line must take
windows by name from `WINDOWS` and never assemble one from user input.

What the guard is, and is not:

* It is a guard **by year**. The research/validation and validation/second-use borders fall exactly
  on year borders (2021-12-31 | 2022-01-04 and 2023-12-29 | 2024-01-02: the exchange is shut on
  the days between), so a year cap is the window there. The second-use stretch is the exception:
  its last year, 2026, holds rows after 2026-09-24, the store serves that partition whole, and
  only the label rule below (and the reader, which must cut by date) keeps them out.
* It does not replace `plan_signal_days`. A signal day whose label would exit after the window's
  last day must never be asked for, and the planner **names** it (`SignalPlan.embargoed`) instead
  of dropping it. Both checks are needed: the store knows no dates, the planner reads no data.
* It guards the doors of `PanelStore`, not the files. Code that builds its own `PanelStore` on
  `store.root`, or opens the Parquet files, is outside it. The diagnostics open stores only through
  `capped_panel_store`.

What the code that uses this module must do (none of it is enforced here):

* The command line (`p7_diagnostics`) resolves a window by name from `WINDOWS`, defaults to
  `RESEARCH`, and refuses `VALIDATION` and `SECOND_USE` outside `p7_reading.read_once`.
* A diagnostic's `as_of` is its **knowledge clock** -- what the panel knew when the diagnostic
  ran, revisions included -- and may be long after the window; the year cap is what enforces the
  **economic date**, since no door serves a year past the ceiling whatever the `as_of`. A
  diagnostic records its `as_of` in its `config`, so it enters the config digest. Rows are read
  through `read_visible_at`; `query` ignores the availability and revision clocks and hands back
  every row.
* `partitions_read()` asks the catalogue for each hash when it is called, not when the partition
  was read: a read-once log must take the hashes inside `PanelStore.reading()`, with the reads.
* A `SignalPlan` is a plain record and can be built by hand; whoever consumes one must have it
  from `plan_signal_days` itself (or re-check its sessions against the window) before asking labels.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NoReturn

import grid as _grid

from openalpha_cn.domain.horizon import MAX_HORIZON_COUNT
from openalpha_cn.domain.trading_calendar import CalendarHorizonError, TradingCalendar
from openalpha_cn.panel.catalog import (
    DatasetReadiness,
    PanelReadOutcome,
    PanelVisibleReadOutcome,
    PartitionCoverage,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import AssessedPanelRead, PanelStore, PartitionStamp
from openalpha_cn.panel_view import PANEL_SUBDIRECTORY

SECOND_USE_LAST: Final[date] = date(2026, 9, 24)
"""The last day any P7 diagnostic reads: the day P6's one-shot holdout ended."""

CATALOG_FILE: Final[str] = "catalog.duckdb"
"""The panel catalogue's file name inside a panel root (`PanelStore.__init__`)."""

READ_ONCE_NOTE: Final[str] = (
    "the validation window and the holdout are read only through p7_reading.read_once; "
    f"the second-use store admits the whole of {SECOND_USE_LAST.year}, rows after "
    f"{SECOND_USE_LAST} included, so cutting rows by date is the reader's duty (the read-once "
    "path), not the store's"
)
"""What every refusal for reaching past a window says about the way past it, and about what a
year cap does not do."""


class DiagnosticWindowError(ValueError):
    """A read, a write or a plan that leaves, or would leave, the window it was built for."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagnosticWindow:
    """A stretch of sessions a diagnostic may measure, and the last year of panel it may read.

    `ceiling_year` is the year cap a `WindowCappedStore` enforces. It may be lower than
    `last.year` (a window that reads less than it measures) and never higher: a cap above the
    window's last year would open partitions of years the window does not contain.
    """

    name: str
    first: date
    last: date
    ceiling_year: int

    def __post_init__(self) -> None:
        if self.last < self.first:
            raise DiagnosticWindowError(
                f"the {self.name} window ends {self.last}, before it begins {self.first}"
            )
        if self.ceiling_year > self.last.year:
            raise DiagnosticWindowError(
                f"the {self.name} window's year ceiling {self.ceiling_year} is past its last "
                f"day {self.last}; a cap above the window's last year opens years it does not hold"
            )


def _stage_bounds(stage: str) -> tuple[date, date]:
    """A closed protocol stage's first and last day, from the grid runner's own table."""
    window = _grid.PROTOCOL_STAGE_WINDOWS[stage]
    if window.last is None:
        raise DiagnosticWindowError(f"the {stage} stage is open-ended and has no last day")
    return window.first, window.last


_DISCOVERY: Final[tuple[date, date]] = _stage_bounds("discovery")
_VALIDATION: Final[tuple[date, date]] = _stage_bounds("validation")

RESEARCH: Final[DiagnosticWindow] = DiagnosticWindow(
    name="research", first=_DISCOVERY[0], last=_DISCOVERY[1], ceiling_year=2021
)
"""The discovery stage: the one window the exploratory diagnostics open by default."""

VALIDATION: Final[DiagnosticWindow] = DiagnosticWindow(
    name="validation", first=_VALIDATION[0], last=_VALIDATION[1], ceiling_year=2023
)
"""The validation stage: read only through `p7_reading.read_once`."""

SECOND_USE: Final[DiagnosticWindow] = DiagnosticWindow(
    name="second_use",
    first=_grid.PROTOCOL_STAGE_WINDOWS["holdout"].first,
    last=SECOND_USE_LAST,
    ceiling_year=2026,
)
"""The holdout stage's first day to `SECOND_USE_LAST`: read only through `p7_reading.read_once`."""

WINDOWS: Final[Mapping[str, DiagnosticWindow]] = MappingProxyType(
    {window.name: window for window in (RESEARCH, VALIDATION, SECOND_USE)}
)
"""The windows by the names the command line takes."""


class WindowCappedStore(PanelStore):
    """A read-only `PanelStore` that serves no year past its window's `ceiling_year`.

    Every read door checks the year (for a `ReadinessRequirement`, every year it names) **before**
    it delegates, so a refused read opens nothing, and records what it served in `served` once the
    base class has answered. `registered_years` and `partition_stamps` list only years at or below
    the ceiling. Every write door raises, and so does `discard_merge_bases`, which would otherwise
    clear the merge bases a writable store on the same root recorded on this thread. Construction
    refuses a root with no catalogue instead of creating one.
    """

    def __init__(self, root: Path, *, window: DiagnosticWindow) -> None:
        if not (root / CATALOG_FILE).is_file():
            # Before `super().__init__`, which creates the root: a typo must not make a store.
            raise DiagnosticWindowError(
                f"{root} has no {CATALOG_FILE} catalog; a window-capped store opens an existing "
                "panel and creates nothing"
            )
        self._window = window
        self._served: set[tuple[str, int]] = set()
        self._served_lock = threading.Lock()
        self._calls = threading.local()
        super().__init__(root)

    @property
    def window(self) -> DiagnosticWindow:
        return self._window

    @property
    def served(self) -> tuple[tuple[str, int], ...]:
        """Every `(dataset, year)` a read door answered for, ascending.

        A door that raised -- the window's refusal or the base class's -- adds nothing. A door that
        answered adds every year its requirement names, a blocked outcome included: the verdict
        was taken over those partitions' catalogue rows.
        """
        with self._served_lock:
            return tuple(sorted(self._served))

    def partitions_read(self) -> tuple[tuple[str, int, str | None], ...]:
        """What was served, each with the catalogue's content hash (`None` if unregistered).

        The hash is asked of `PanelStore` itself, so asking is not a read to record.
        """
        found: list[tuple[str, int, str | None]] = []
        for dataset, year in self.served:
            found.append((dataset, year, super().partition_content_hash(dataset, year)))
        return tuple(found)

    @contextmanager
    def _door(self, dataset: str, years: Iterable[int]) -> Iterator[None]:
        """What every read door runs around the base class's answer.

        Before it: refuse any year past the ceiling, so a refused read opens nothing. After it:
        record what the door admitted, but only once the base class has answered -- a call that
        raises (a year the requirement does not name, a malformed dataset) leaves `served`
        as it was, because no partition was read. The base class reaches its own doors through one
        another (`read_if_ready` -> `assessed` -> `assess_readiness`); those nested doors check
        again, which costs nothing and keeps each door safe without the others, but only the
        outermost one records.
        """
        asked = tuple(years)
        late = sorted({year for year in asked if year > self._window.ceiling_year})
        if late:
            raise DiagnosticWindowError(
                f"{dataset} {late} is past the {self._window.name} window's last year "
                f"{self._window.ceiling_year} ({self._window.first}..{self._window.last}); "
                f"{READ_ONCE_NOTE}"
            )
        depth: int = getattr(self._calls, "depth", 0)
        self._calls.depth = depth + 1
        try:
            yield
        finally:
            self._calls.depth = depth
        if depth == 0:
            with self._served_lock:
                self._served.update((dataset, year) for year in asked)

    # --- the read doors ----------------------------------------------------------------------

    def partition_content_hash(self, dataset: str, year: int) -> str | None:
        with self._door(dataset, (year,)):
            return super().partition_content_hash(dataset, year)

    def query(
        self,
        dataset: str,
        *,
        year: int,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
    ) -> list[tuple[object, ...]]:
        with self._door(dataset, (year,)):
            return super().query(dataset, year=year, columns=columns, filters=filters)

    def profile_query(
        self,
        dataset: str,
        *,
        year: int,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        with self._door(dataset, (year,)):
            return super().profile_query(dataset, year=year, columns=columns, filters=filters)

    def read_coverage(self, dataset: str, year: int) -> PartitionCoverage | None:
        with self._door(dataset, (year,)):
            return super().read_coverage(dataset, year)

    def assess_readiness(self, requirement: ReadinessRequirement) -> DatasetReadiness:
        with self._door(requirement.dataset, requirement.years):
            return super().assess_readiness(requirement)

    def assessed(self, requirement: ReadinessRequirement) -> AssessedPanelRead:
        with self._door(requirement.dataset, requirement.years):
            return super().assessed(requirement)

    def read_if_ready(
        self,
        requirement: ReadinessRequirement,
        *,
        year: int,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
    ) -> PanelReadOutcome:
        with self._door(requirement.dataset, (*requirement.years, year)):
            return super().read_if_ready(requirement, year=year, columns=columns, filters=filters)

    def read_visible_at(
        self,
        requirement: ReadinessRequirement,
        *,
        year: int,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
        event_time_as_naive_utc: bool = False,
        newly_visible_since: datetime | None = None,
    ) -> PanelVisibleReadOutcome:
        with self._door(requirement.dataset, (*requirement.years, year)):
            return super().read_visible_at(
                requirement,
                year=year,
                columns=columns,
                filters=filters,
                event_time_as_naive_utc=event_time_as_naive_utc,
                newly_visible_since=newly_visible_since,
            )

    # --- the listings: only years the window may see -----------------------------------------

    def registered_years(self, dataset: str) -> tuple[int, ...]:
        ceiling = self._window.ceiling_year
        return tuple(year for year in super().registered_years(dataset) if year <= ceiling)

    def partition_stamps(
        self, *, excluding_prefixes: Sequence[str] = ()
    ) -> tuple[PartitionStamp, ...]:
        ceiling = self._window.ceiling_year
        return tuple(
            stamp
            for stamp in super().partition_stamps(excluding_prefixes=excluding_prefixes)
            if stamp[1] <= ceiling
        )

    # --- the write doors: none ---------------------------------------------------------------

    def _refuse_a_write(self, door: str) -> NoReturn:
        raise DiagnosticWindowError(
            f"{door} is refused: a window-capped store is read-only (the {self._window.name} "
            "window is read, never written)"
        )

    def merge_base(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._refuse_a_write("merge_base")

    def write_partition(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._refuse_a_write("write_partition")

    def write_partitions(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._refuse_a_write("write_partitions")

    def record_coverage(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._refuse_a_write("record_coverage")

    def remove_partition(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._refuse_a_write("remove_partition")

    def discard_merge_bases(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._refuse_a_write("discard_merge_bases")


def capped_panel_store(runtime_dir: Path, window: DiagnosticWindow) -> WindowCappedStore:
    """The panel plane inside `runtime_dir`, opened read-only and capped at `window`.

    The location is `panel_view.panel_store`'s: `runtime_dir/panel`.
    """
    return WindowCappedStore(runtime_dir / PANEL_SUBDIRECTORY, window=window)


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalPlan:
    """The signal days a window allows, and the ones it names and refuses.

    `embargoed` holds the days whose longest label would exit after `window.last` (or past what
    the calendar knows): named here so a report can say how many were not measured and why.
    """

    window: DiagnosticWindow
    first: date
    last: date
    stride: int
    horizons: tuple[int, ...]
    sessions: tuple[date, ...]
    embargoed: tuple[date, ...]


def label_exit_day(calendar: TradingCalendar, day: date, horizon: int) -> date | None:
    """The session the label of `day` exits on -- entered at the next close, left `horizon`
    sessions later (`build_label_window`'s rule) -- or `None` when `calendar` stops before it.

    The planner embargoes by it, and the diagnostics engine checks a hand-built plan with it
    before it asks a label reader for a window: `None` counts as past the window."""
    try:
        return calendar.shift(calendar.next_trading_day(day), horizon)
    except CalendarHorizonError:
        return None


def _exits_by(calendar: TradingCalendar, day: date, horizon: int, limit: date) -> bool:
    """Whether the label of `day` ends on or before `limit`. A calendar that stops before the
    exit answers no."""
    exit_day = label_exit_day(calendar, day, horizon)
    return exit_day is not None and exit_day <= limit


def plan_signal_days(
    calendar: TradingCalendar,
    *,
    window: DiagnosticWindow,
    first: date,
    last: date,
    stride: int,
    horizons: Sequence[int],
) -> SignalPlan:
    """One signal day in every `stride` sessions from `first` to `last`, inside `window`.

    A signal day whose longest label (`max(horizons)` sessions after the next close) would exit
    after `window.last` goes to `embargoed` rather than `sessions`. Refuses, by name, a first day
    before the window, a last day after it, a last day before the first, a stride under 1, a
    horizon outside `1..MAX_HORIZON_COUNT` (or none), and a first day that is not a session.
    """
    for role, day in (("first", first), ("last", last)):
        if type(day) is not date:
            raise DiagnosticWindowError(f"the plan's {role} day must be a plain date, not {day!r}")
    if first < window.first:
        raise DiagnosticWindowError(
            f"the plan's first day {first} is before the {window.name} window's first day "
            f"{window.first}"
        )
    if last > window.last:
        raise DiagnosticWindowError(
            f"the plan's last day {last} is past the {window.name} window's last day "
            f"{window.last}; {READ_ONCE_NOTE}"
        )
    if last < first:
        raise DiagnosticWindowError(f"the plan's last day {last} is before its first day {first}")
    if type(stride) is not int or stride < 1:
        raise DiagnosticWindowError(
            f"the stride must be a whole number of sessions, at least 1, not {stride!r}"
        )
    chosen = tuple(horizons)
    if not chosen or any(type(h) is not int or not 1 <= h <= MAX_HORIZON_COUNT for h in chosen):
        raise DiagnosticWindowError(
            f"at least one horizon is needed and each must be a whole number of sessions from 1 "
            f"to {MAX_HORIZON_COUNT}; got {chosen!r}"
        )
    if not calendar.is_trading_day(first):
        raise DiagnosticWindowError(
            f"the plan's first day {first} is not a session on the {calendar.exchange} calendar"
        )
    longest = max(chosen)
    kept: list[date] = []
    named: list[date] = []
    for day in calendar.trading_days_between(first, last)[::stride]:
        (kept if _exits_by(calendar, day, longest, window.last) else named).append(day)
    return SignalPlan(
        window=window,
        first=first,
        last=last,
        stride=stride,
        horizons=chosen,
        sessions=tuple(kept),
        embargoed=tuple(named),
    )
