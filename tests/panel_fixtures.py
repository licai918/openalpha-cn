"""A panel fixture **generator** driven by a table of measured shapes (`V2-P1-014`).

## Why a generator, and why the table rather than the de-duplication

`scripts/verify_publication.py` blocks `.parquet`/`.duckdb`/`.sqlite3`/`.db` from the tree, so
every panel fixture in this repository is already built at run time -- 148 module-level helper
functions across the 17 files of `tests/integration/panel/` at `754ba9e`, counted as every
top-level `def` that is not a `test_*`. Sharing them would save lines. It would not have
prevented a single one of the defects that made this module necessary.

Three review passes over `V2-P1-010`..`V2-P1-012` ran fresh mutants against the suite and the
survivors had one cause between them: **the fixtures did not carry a form the real data has.**
`is_across_a_gap`'s `> 1` survived being widened to `> 400` because the only transitions on
file were one day apart and seventeen years apart, with nothing in between. `value_of`
survived being narrowed to `versions[1:2]` because no fixture key carried three rows.
`_ambiguity_check` survived being cut to `datasets[:1]` because only `income` was stored. Each
was closed afterwards by hand, one test at a time, by whoever happened to review it -- which
is the process this module replaces.

So the deliverable is not a de-duplicated builder. It is `PANEL_SHAPES`: **a table of forms
the live data demonstrably has, each with the measurement behind it and a detector that reads
the generated artifact and says whether the form is actually there.** Coverage of a form
becomes a property of the generator, checkable in one place
(`tests/unit/test_panel_fixtures.py`), instead of a property of each author's imagination.

## The shapeless panel is the point of comparison, not the recommended fixture

`generate_panel()` with no shapes builds the smallest panel the real writers accept: one price
per security that never moves, one factor that never steps, one filing per security with one
version, one name that is never changed, one industry assignment that never changes. **That is
deliberately the fixture whose poverty this task exists to name.** It is here so every shape
can be shown to be *absent* from it -- a detector that answers `True` on the shapeless panel is
measuring nothing, and `tests/unit/test_panel_fixtures.py` fails it.

One thing the shapeless panel does carry is a single untimed `suspend_d` halt, with the bar and
the valuation for that name and session withheld. `suspend_d` needs at least one row for the
partition to exist at all, and an untimed halt with no bar is the only row shape that is not
itself a defect. It is named here rather than filed as a shape for that reason.

## A shape may be a defect, and it has to say so

Every shape was a form of *sound* data until `V2-P2` needed one that was not. Nineteen shapes
that all wrote panels the real writers accept and the doctor is silent about is what a fixture
table should be -- and it left the whole "must report unhealthy" half of `panel_doctor` and
`panel_gate` reachable only by digging a hole in the store by hand (`.unlink()` on a Parquet
file) or by asking a question about a year that was never built. Neither is a defect of the
*data*, which is what the nine `V2-P2` injection issues are about.

So `PanelShape.provokes` names the health codes a shape makes the report emit, empty for the
sound ones, and `tests/integration/panel/test_panel_shape_coverage.py` asserts the table in
both directions against a real `panel_health_report`. Four shapes carry a `warning` and three
`blocking`s between them: `daily.uncorroborated_factor_step` restates a `pre_close` the factors
do not corroborate, and `financials.announced_after_the_as_of`,
`index.publication_after_the_as_of` and `industry.reclassification_after_the_as_of` each store a
row that had not become knowable at the read -- one per dataset, which is `V2-P2-001`, `003`
and `004`. All three of those are built to the same rule: the injected row is dated after the
read **and inside the panel's own partition year**, so the same partition at the same `as_of` is
`ready` without it and `blocked` with it, and the refusal is attributable to the row rather than
to the year (`tests/integration/panel/test_lookahead_injection.py` asserts the pair by pruning
the batch itself, and pins the boundary at a microsecond).

## What a shape is allowed to imply

Shapes compose and some imply others: `daily.ex_rights_session` moves a factor, so a panel
carrying it also carries a factor series that is not flat. Nothing here pretends otherwise --
the table's contract is *"asking for the shape produces the shape"* and *"the shapeless panel
does not have it"*, not *"asking for one shape produces exactly one"*. Requesting every shape
at once is required to produce every one of them, which is what rules out a pair that cancel.

## Layering

Under `tests/`, not `src/`. It is test infrastructure: in `src/` it would ship in the wheel,
enter `--cov=openalpha_cn` (so a generator branch no test happens to walk would have to be
covered like product code), and add a module to the published API that no product caller can
use. It also spans `domain`, `panel` and `panel_ingest` at once, which inside `src/` would
have to answer to `openalpha_cn.panel`'s zero-sibling-edge rule; here the question does not
arise. It sits at the `tests/` root rather than in one subtree because two subtrees use it --
`tests/unit/test_panel_fixtures.py` and `tests/integration/panel/test_panel_shape_coverage.py`
-- which is the rule `tests/conftest.py`'s own docstring states for shared fixtures.

Existing constructors are deliberately **not** migrated onto this generator. Rewriting them
would move a large number of live assertions, and the assertions are the thing under
protection. What this module owes them is the proof, in
`tests/integration/panel/test_panel_shape_coverage.py`, that a panel built only from shapes
kills the same mutants their hand-written fixtures kill.

Being under `tests/` does not put it outside the type gate. `[tool.mypy]` names
`packages = ["openalpha_cn"]`, so `mypy tests/` is not implied by it -- but this one file is
checked explicitly: the completion command is `uv run mypy src scripts tests/panel_fixtures.py`
and it passes under the same `strict` settings `src/` answers to, `warn_unused_ignores`
included. That is what makes the eight `# type: ignore[arg-type]`s below evidence rather than
decoration: each marks a real narrowing from `PanelColumn`'s `object` values, and an
unnecessary one would fail the gate. The rest of `tests/` is deliberately not pulled in --
this module is a generator many tests depend on, which is a different thing from a test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    MAX_PRE_CLOSE_DISAGREEMENT,
    DailyBar,
)
from openalpha_cn.domain.financial_statements import (
    ANNOUNCEMENT_DATE_COLUMN,
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    DATASETS_WITH_REVISION_LABEL,
    FINANCIAL_INDICATOR_DATASET,
    FIRST_ANNOUNCEMENT_COLUMN,
    INCOME_DATASET,
    REPORT_PERIOD_COLUMN,
    REVISION_LABEL_COLUMN,
    STATEMENT_DATA_COLUMNS,
    ReportRow,
)
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_FROM_COLUMN,
    INDUSTRY_L1_COLUMN,
    INDUSTRY_L2_COLUMN,
    INDUSTRY_L3_COLUMN,
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_THROUGH_COLUMN,
    IndustryAssignment,
)
from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
    NameRecord,
    RiskWarning,
)
from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    PriceLimit,
    limit_touch,
)
from openalpha_cn.domain.stock_universe import (
    DELISTING_EVENT,
    LISTING_EVENT,
    STOCK_BASIC_DATASET,
)
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_suspensions,
    write_adjustment_factors,
    write_daily_panel,
    write_financial_statements,
    write_index_weights,
    write_industry_memberships,
    write_name_history,
    write_price_limits,
    write_stock_universe,
    write_suspensions,
    write_trading_calendar,
)

# --- the fixed frame ------------------------------------------------------------------------
#
# The window and the clocks are `tests/integration/panel/test_panel_doctor.py`'s, because that
# file's healthy panel is the one shape combination already proved to satisfy every write-time
# guard in `panel_ingest` -- the session census, the thin-cross-section floor, the freshness
# bounds. Reproducing it rather than inventing a second frame keeps the generator's baseline on
# ground that is known to be storable.

EXCHANGE: Final[str] = "SZSE"
YEAR: Final[int] = 2026
FIRST_DAY: Final[date] = date(2026, 1, 1)
LAST_DAY: Final[date] = date(2026, 12, 31)

NEW_YEAR_RECESS: Final[tuple[date, ...]] = (date(2026, 1, 1), date(2026, 1, 2))
"""The two weekdays `_session_census` needs closed.

Both write-time censuses bound their expectation at 1 January of the partition's year, so a
partition whose first stored row is the fifth is short two sessions unless the calendar says
the exchange was shut on them. They sit *before* the first session of the window, which is
what lets `calendar.mid_window_weekday_closure` be a shape at all: the shapeless panel has no
closed weekday between its first session and its last.
"""

WINDOW_FIRST: Final[date] = date(2026, 1, 5)
WINDOW_LAST: Final[date] = date(2026, 1, 16)

LISTED_ON: Final[date] = date(2026, 1, 2)

AS_OF: Final[datetime] = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on Saturday 2026-01-17, so the last session published through is the
Friday before it."""

SECURITIES: Final[tuple[str, ...]] = (
    "000001.SZ",
    "000002.SZ",
    "600000.SH",
    "600519.SH",
    "300750.SZ",
    "688981.SH",
    "002415.SZ",
    "601318.SH",
)
"""Eight names, and the count is load-bearing rather than cosmetic.

`_refuse_unexplained_thin_sessions` refuses a session whose bars plus whole-day halts fall
under 85% of the local median. On a three-name universe a single withheld row is 67% and the
writer refuses it, so `daily.bar_without_valuation` -- the shape that makes
`close_disagreements`' two arguments distinguishable at all -- could only be built by going
round the writer with `write_panel_batch`, which is what
`tests/integration/panel/test_panel_doctor.py` has to do today. At eight names one withheld row
is 87.5% and the shape reaches the store through the real writer with every guard live.
"""
DELISTED_SECURITY: Final[str] = "000004.SZ"
DELISTED_ON: Final[date] = date(2026, 1, 5)
"""The first session of the window, and `delist_date` is exclusive.

So the terminated name is in the registry, is knowable well before `AS_OF`, and is in **no**
session's cross section -- which is what makes the shape load-bearing rather than decorative:
a reader that treated `delist_date` as inclusive, or ignored it, would put a ninth name into
every cross section and `panel_doctor`'s unpriced check would report it as unexplained.

**A termination *inside* the window is the stronger shape and this is not it.** That one would
also exercise a cross section that legitimately shrinks part-way through a partition, and it
is not built here because the terminated name would then have to join the price grid for the
sessions it did trade -- a change to every price dataset for one shape. The bound is recorded
rather than papered over; `KNOWN_UNIVERSE_LIMITATIONS`' `suspension_invisible` is the closest
thing the corpus offers to a reason it matters.
"""

INDEX_CODE: Final[str] = "000300.SH"

BASE_CLOSE: Final[float] = 10.0
SESSION_STEP: Final[float] = 0.5
"""Half a yuan a session, once `daily.close_moves_between_sessions` is asked for.

Far more than one tick on purpose: `pre_close_tolerance` allows a tick of `pre_close` plus a
tick of each `adj_factor`, so a one-tick drift would sit inside the tolerance and a check that
read the wrong session's close would still agree.

**The magnitude is asserted, not left to this sentence.** `_has_moving_closes` only asks
whether the closes differ at all, so nothing in the shape table distinguishes a step of 0.5
from a step of 0.005 -- and at 0.005 the gap-6 mutant (the return path taking the bar's own
close as its previous) lands back inside the tolerance and survives.
`test_panel_shape_coverage.py::test_a_session_return_is_computed_against_the_previous_session_and_not_against_itself`
therefore requires the generated step to exceed `pre_close_tolerance` at the panel's widest
close, and to be at least the 0.06 the shape's own `measurement` records for
2026-06-11 -> 2026-06-12. Shrinking this constant fails there.
"""

INCOME_COLUMNS: Final[tuple[str, ...]] = STATEMENT_DATA_COLUMNS[INCOME_DATASET]
CASHFLOW_COLUMNS: Final[tuple[str, ...]] = STATEMENT_DATA_COLUMNS[CASH_FLOW_DATASET]

STATEMENT_DATASETS: Final[tuple[str, ...]] = (
    INCOME_DATASET,
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
)
"""The four statement endpoints, in `FINANCIAL_STATEMENT_DATASETS`' order.

Restated rather than imported, and reconciled against the domain's own tuple in
`tests/unit/test_panel_fixtures.py::test_the_four_statement_endpoints_are_the_domains_own_four`.
`financials.statement_dataset_without_a_revision_label` claims something about *four* endpoints
-- all four stored, exactly one of them label-less -- so a generator that silently followed the
domain to a fifth would keep answering `True` while covering four fifths of it. The
reconciliation is what makes the restatement a pin rather than a copy.
"""

BALANCE_SHEET_COLUMNS: Final[tuple[str, ...]] = STATEMENT_DATA_COLUMNS[BALANCE_SHEET_DATASET]
FINANCIAL_INDICATOR_COLUMNS: Final[tuple[str, ...]] = STATEMENT_DATA_COLUMNS[
    FINANCIAL_INDICATOR_DATASET
]

BASE_PERIOD: Final[date] = date(2025, 9, 30)
EARLIER_PERIOD: Final[date] = date(2025, 6, 30)
BASE_ANNOUNCEMENT: Final[date] = date(2026, 1, 5)
LATE_ANNOUNCEMENT: Final[date] = date(2026, 1, 12)
FUTURE_ANNOUNCEMENT: Final[date] = date(2026, 1, 20)
"""Three days after `AS_OF`, and still inside the panel's own partition year.

Both halves are load-bearing. After `AS_OF` is what makes the row unknowable to the read;
inside 2026 is what keeps the refusal attributable to the row rather than to the granularity
of the check -- roadmap section 11 records that `not_yet_knowable` is decided per partition and
a partition is a year, so an `as_of` that simply predates a whole year's worth of data is
blocked for a reason that has nothing to do with anything injected. Here the same partition at
the same `as_of` is `ready` without this row and `blocked` with it.
"""

FUTURE_PUBLICATION: Final[date] = date(2026, 2, 27)
"""The last open weekday of the month *after* the read, and still inside 2026.

`FUTURE_ANNOUNCEMENT`'s two halves, in the one dataset that cannot use its date.
`build_index_membership` refuses a doubled month and a missing one, so a second publication
cannot be dropped three days after the first the way a restatement can: it has to be the next
month's, which on this generator's calendar is Friday 2026-02-27 (the 28th is a Saturday).
That is late enough to be unknowable at `AS_OF` and early enough to stay in the same partition,
which is what keeps the refusal attributable to the injected publication rather than to the
year the partition covers.
"""

RECLASSIFIED_THROUGH: Final[date] = date(2026, 1, 16)
RECLASSIFIED_FROM: Final[date] = date(2026, 2, 2)
"""A reclassification whose *opening* row is the only thing the read cannot see.

`index_member_all` dates a row's availability at the day it is about (see
`KNOWN_INDUSTRY_LIMITATIONS.no_announcement_and_no_revision_history`), so closing the old
interval on the panel's last session keeps that row knowable -- 2026-01-15 16:00Z, before
`AS_OF` -- while the new interval's opening row, dated 2026-02-02, is not. **Exactly one stored
row of the partition is after `AS_OF`**, which is what makes "remove that row and the same
partition at the same `as_of` is `ready`" a literal statement rather than an approximate one.

The 17-day delta is chosen to stay clear of both existing industry shapes' detectors:
`_has_session_adjacent_handover` only counts a hand-over of 10 days or fewer, and
`_has_industry_coverage_hole` counts sessions strictly between the two, of which there are none
past the window's last session. So this shape composes with them instead of implying them.
"""

DAILY_BASIC_EXTRA_COLUMNS: Final[tuple[str, ...]] = (
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)

INDUSTRY_L1: Final[tuple[str, str]] = ("801780.SI", "801120.SI")
INDUSTRY_L2: Final[tuple[str, str]] = ("801783.SI", "801124.SI")
INDUSTRY_L3: Final[tuple[str, str]] = ("857831.SI", "851241.SI")


class PanelFixtureError(ValueError):
    """Raised when a caller asks the generator for something it does not have."""


# --- the generated panel --------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedPanel:
    """One run-time panel fixture: batches, domain carriers, and the shapes asked for.

    Nothing here touches the filesystem. `write_generated_panel` is the separate step that
    drives the batches through the real `panel_ingest` writers into a `PanelStore`, so a test
    that only needs the shapes never pays for a store.
    """

    shapes: frozenset[str]
    calendar_days: tuple[CalendarDay, ...]
    sessions: tuple[date, ...]
    securities: tuple[str, ...]
    batches: Mapping[str, ColumnarPanelBatch]
    name_records: Mapping[str, tuple[NameRecord, ...]]
    industry_assignments: Mapping[str, tuple[IndustryAssignment, ...]]
    year: int = YEAR
    as_of: datetime = AS_OF
    exchange: str = EXCHANGE
    """The frame, carried on the panel rather than only importable beside it.

    `YEAR`, `AS_OF` and `EXCHANGE` were module constants a caller had to import separately, so
    every reader of a panel had to know which module its partition year came from -- and a
    second frame arriving later (a two-year panel, a second exchange) would have had to change
    every call site rather than one object. They are defaulted from the constants, not
    duplicated: `generate_panel` never passes them, and `dataclasses.replace` on a panel keeps
    whatever the panel already carried.
    """

    def calendar(self) -> TradingCalendar:
        return build_trading_calendar(self.exchange, self.calendar_days)

    def batch(self, dataset: str) -> ColumnarPanelBatch:
        """The batch for `dataset`, or a refusal naming what is stored.

        A `KeyError` here would read as "the generator is broken"; what it actually means is
        that a shape the caller expected to add a dataset was not requested.
        """
        try:
            return self.batches[dataset]
        except KeyError:
            raise PanelFixtureError(
                f"this panel carries no {dataset!r} batch; it carries "
                f"{sorted(self.batches)} for shapes {sorted(self.shapes)}"
            ) from None

    def column(self, dataset: str, name: str) -> tuple[object, ...]:
        """One stored column of one dataset, in row order."""
        batch = self.batch(dataset)
        for column in batch.columns:
            if column.name == name:
                return column.values
        raise PanelFixtureError(
            f"{dataset!r} has no column {name!r}; it has "
            f"{sorted(column.name for column in batch.columns)}"
        )

    def rows_of(self, dataset: str, *names: str) -> tuple[tuple[object, ...], ...]:
        """`(subject, *named columns)` per row, in stored order."""
        batch = self.batch(dataset)
        columns = [self.column(dataset, name) for name in names]
        return tuple(zip(batch.subjects, *columns, strict=True))

    def statement_rows(self, dataset: str) -> Mapping[str, tuple[ReportRow, ...]]:
        """The generated statement batch as `build_statement_history`'s own input type.

        Read off the batch's columns rather than off the private row objects that built it, so
        a caller exercising `ReportFiling` is exercising the same bytes a `PanelStore` would
        hand back -- in batch order, which a store round trip does not promise and which the
        three-version shape needs.
        """
        value_columns = STATEMENT_DATA_COLUMNS[dataset]
        values = {name: self.column(dataset, name) for name in value_columns}
        periods = self.column(dataset, REPORT_PERIOD_COLUMN)
        announcements = self.column(dataset, ANNOUNCEMENT_DATE_COLUMN)
        labelled = dataset in DATASETS_WITH_REVISION_LABEL
        first = self.column(dataset, FIRST_ANNOUNCEMENT_COLUMN) if labelled else ()
        labels = self.column(dataset, REVISION_LABEL_COLUMN) if labelled else ()
        rows: dict[str, list[ReportRow]] = {}
        for index, subject in enumerate(self.batch(dataset).subjects):
            rows.setdefault(subject, []).append(
                ReportRow(
                    period=date.fromisoformat(str(periods[index])),
                    announced_on=date.fromisoformat(str(announcements[index])),
                    values={name: float(column[index]) for name, column in values.items()},  # type: ignore[arg-type]
                    first_announced_on=date.fromisoformat(str(first[index])) if labelled else None,
                    revision_label=str(labels[index]) if labelled else None,
                )
            )
        return MappingProxyType({code: tuple(group) for code, group in rows.items()})


# --- the shape table ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelShape:
    """One form the live data has, with the measurement behind it and a detector for it.

    `detect` reads the **generated artifact** -- the stored columns, or the domain carriers the
    generator hands back -- and never the request. A detector that consulted
    `panel.shapes` would answer `True` for every shape by construction and prove nothing, which
    is this module's own most available failure.
    """

    shape_id: str
    datasets: tuple[str, ...]
    summary: str
    measurement: str
    detect: Callable[[GeneratedPanel], bool]
    provokes: tuple[str, ...] = ()
    """The `panel_doctor` codes a panel carrying this shape and nothing else reports.

    Empty for a shape that is a form of *sound* data, which is what every shape was until
    `V2-P2-000`. It is stated rather than left to each test because the two useful questions
    about a fixture -- "does asking for this produce a clean panel?" and "does asking for this
    produce exactly this complaint?" -- are the same question with a different answer, and a
    table is the only place a reviewer can total them up. `test_panel_shape_coverage.py`
    asserts both directions against the real report, so a code named here that the doctor does
    not emit fails, and so does a shape declaring `()` that turns out to complain.
    """


def _has_mid_window_weekday_closure(panel: GeneratedPanel) -> bool:
    first, last = panel.sessions[0], panel.sessions[-1]
    return any(
        not day.is_trading and day.calendar_date.weekday() < 5 and first < day.calendar_date < last
        for day in panel.calendar_days
    )


def _has_multi_session_recess(panel: GeneratedPanel) -> bool:
    """A gap between consecutive sessions wider than a plain Friday-to-Monday weekend."""
    return any((later - earlier).days > 3 for earlier, later in pairwise(panel.sessions))


def _has_delisted_security(panel: GeneratedPanel) -> bool:
    return DELISTING_EVENT in panel.column(STOCK_BASIC_DATASET, "lifecycle_event")


def _newest_session(panel: GeneratedPanel) -> str:
    """The last session the price panel reaches, as the three datasets below spell a date."""
    return panel.sessions[-1].isoformat()


def _has_termination_on_the_newest_session(panel: GeneratedPanel) -> bool:
    return any(
        event == DELISTING_EVENT and day == _newest_session(panel)
        for _code, event, day in panel.rows_of(
            STOCK_BASIC_DATASET, "lifecycle_event", "lifecycle_date"
        )
    )


def _has_halt_on_the_newest_session(panel: GeneratedPanel) -> bool:
    return any(
        day == _newest_session(panel)
        for _code, day in panel.rows_of(SUSPENSION_DATASET, "trade_date")
    )


def _has_rename_announced_on_the_newest_session(panel: GeneratedPanel) -> bool:
    return any(
        day == _newest_session(panel)
        for _code, day in panel.rows_of(NAMECHANGE_DATASET, NAME_ANNOUNCEMENT_COLUMN)
    )


def _has_announcement_before_effect(panel: GeneratedPanel) -> bool:
    return any(
        record.announced_on < record.effective_from
        for records in panel.name_records.values()
        for record in records
    )


def _naive_special_treatment(name: str) -> bool:
    """The rule `domain/name_history.py` measured 235 rows against and rejected."""
    return name.startswith(("ST", "*ST"))


def _has_reform_prefixed_special_treatment(panel: GeneratedPanel) -> bool:
    """A name the naive prefix test reads as ordinary and the real grammar does not."""
    return any(
        record.risk_warning is not RiskWarning.none and not _naive_special_treatment(record.name)
        for records in panel.name_records.values()
        for record in records
    )


def _factor_series(panel: GeneratedPanel) -> dict[str, list[tuple[str, float]]]:
    series: dict[str, list[tuple[str, float]]] = {}
    for subject, day, factor in panel.rows_of(ADJ_FACTOR_DATASET, "factor_date", "adj_factor"):
        series.setdefault(str(subject), []).append((str(day), float(factor)))  # type: ignore[arg-type]
    return {code: sorted(rows) for code, rows in series.items()}


def _has_factor_step_down(panel: GeneratedPanel) -> bool:
    return any(
        later < earlier
        for rows in _factor_series(panel).values()
        for (_, earlier), (_, later) in pairwise(rows)
    )


def _close_series(panel: GeneratedPanel, dataset: str) -> dict[str, dict[str, float]]:
    series: dict[str, dict[str, float]] = {}
    for subject, day, close in panel.rows_of(dataset, "trade_date", "close"):
        series.setdefault(str(subject), {})[str(day)] = float(close)  # type: ignore[arg-type]
    return series


def _has_moving_closes(panel: GeneratedPanel) -> bool:
    series = _close_series(panel, DAILY_DATASET)
    return any(len(set(closes.values())) > 1 for closes in series.values())


def _has_ex_rights_session(panel: GeneratedPanel) -> bool:
    """A session whose `pre_close` is a restatement *and* whose factor moved with it.

    Both halves are required. A restated `pre_close` with a flat factor series is
    `V2-P1-007`'s cross-check failing, not an ex-rights day, and a factor step with an
    uncorroborated `pre_close` is the defect `panel_doctor`'s `return_paths` check exists to
    report -- neither is the shape the live 2026-06-12 row has.
    """
    factors = _factor_series(panel)
    closes = _close_series(panel, DAILY_DATASET)
    for subject, day, pre_close in panel.rows_of(DAILY_DATASET, "trade_date", "pre_close"):
        code, session = str(subject), str(day)
        steps = dict(factors.get(code, []))
        earlier = [other for other in sorted(closes.get(code, {})) if other < session]
        if not earlier or session not in steps:
            continue
        previous = earlier[-1]
        if steps.get(previous) == steps[session]:
            continue
        if abs(float(pre_close) - closes[code][previous]) > MAX_PRE_CLOSE_DISAGREEMENT / 2:  # type: ignore[arg-type]
            return True
    return False


def _has_uncorroborated_factor_step(panel: GeneratedPanel) -> bool:
    """A restated `pre_close` whose factor series did **not** move with it.

    Exactly the clause `_has_ex_rights_session` excludes, and it is the panel's only reachable
    `return_path_disagreement`: the published return and the factor-adjusted one then answer
    different numbers for the same session, and neither can be trusted.
    """
    factors = _factor_series(panel)
    closes = _close_series(panel, DAILY_DATASET)
    for subject, day, pre_close in panel.rows_of(DAILY_DATASET, "trade_date", "pre_close"):
        code, session = str(subject), str(day)
        steps = dict(factors.get(code, []))
        earlier = [other for other in sorted(closes.get(code, {})) if other < session]
        if not earlier or session not in steps:
            continue
        previous = earlier[-1]
        if steps.get(previous) != steps[session]:
            continue
        if abs(float(pre_close) - closes[code][previous]) > MAX_PRE_CLOSE_DISAGREEMENT / 2:  # type: ignore[arg-type]
            return True
    return False


def _rows_after_the_as_of(panel: GeneratedPanel, dataset: str) -> int:
    """How many of `dataset`'s stored rows had not become knowable by the panel's own `as_of`.

    Per dataset rather than over the whole panel, which is what the three look-ahead shapes
    need to stay independent: a detector that swept every batch would answer `True` for
    `financials.announced_after_the_as_of` on a panel carrying only the *index* injection, and
    the table's one non-negotiable property is that a detector reports the form it names.
    """
    if dataset not in panel.batches:
        return 0
    return sum(
        1 for available in panel.batch(dataset).timeline.available_time if available > panel.as_of
    )


def _has_disclosure_after_the_as_of(panel: GeneratedPanel) -> bool:
    """An `income` row that had not become knowable by the panel's own `as_of`."""
    return _rows_after_the_as_of(panel, INCOME_DATASET) > 0


def _has_publication_after_the_as_of(panel: GeneratedPanel) -> bool:
    """An `index_weight` row that had not become knowable by the panel's own `as_of`."""
    return _rows_after_the_as_of(panel, INDEX_WEIGHT_DATASET) > 0


def _has_reclassification_after_the_as_of(panel: GeneratedPanel) -> bool:
    """An `index_member_all` row that had not become knowable by the panel's own `as_of`."""
    return _rows_after_the_as_of(panel, INDUSTRY_MEMBERSHIP_DATASET) > 0


def _has_bar_without_valuation(panel: GeneratedPanel) -> bool:
    bars = {(str(subject), str(day)) for subject, day in panel.rows_of(DAILY_DATASET, "trade_date")}
    valuations = {
        (str(subject), str(day))
        for subject, day in panel.rows_of(DAILY_BASIC_DATASET, "trade_date")
    }
    return bool(bars - valuations)


def _halt_rows(panel: GeneratedPanel) -> tuple[tuple[str, str, str, str | None], ...]:
    return tuple(
        (str(subject), str(day), str(kind), None if timing is None else str(timing))
        for subject, day, kind, timing in panel.rows_of(
            SUSPENSION_DATASET, "trade_date", "suspend_type", "suspend_timing"
        )
    )


def _priced_keys(panel: GeneratedPanel) -> set[tuple[str, str]]:
    return {(str(subject), str(day)) for subject, day in panel.rows_of(DAILY_DATASET, "trade_date")}


def _has_timed_interruption(panel: GeneratedPanel) -> bool:
    priced = _priced_keys(panel)
    return any(
        kind == "S" and timing is not None and (code, day) in priced
        for code, day, kind, timing in _halt_rows(panel)
    )


def _has_resumption(panel: GeneratedPanel) -> bool:
    priced = _priced_keys(panel)
    return any(kind == "R" and (code, day) in priced for code, day, kind, _ in _halt_rows(panel))


def _has_limit_free_sentinel(panel: GeneratedPanel) -> bool:
    """A published band the domain's own ratio test classifies as "no limit that session"."""
    closes = _close_series(panel, DAILY_DATASET)
    for subject, day, up, down in panel.rows_of(
        PRICE_LIMIT_DATASET, "trade_date", "up_limit", "down_limit"
    ):
        code, session = str(subject), str(day)
        reference = closes.get(code, {}).get(session)
        if reference is None:
            continue
        limit = PriceLimit(
            ts_code=code,
            trade_date=date.fromisoformat(session),
            up_limit=float(up),  # type: ignore[arg-type]
            down_limit=float(down),  # type: ignore[arg-type]
        )
        if not limit.is_bounded(reference):
            return True
    return False


def _has_one_price_limit_up(panel: GeneratedPanel) -> bool:
    """A session the domain's own `limit_touch` calls locked at the upper band.

    Asked through `limit_touch` rather than by re-spelling `low >= up_limit` here, for
    `_has_limit_free_sentinel`'s reason: a detector that restates the rule agrees with a
    mutated rule.
    """
    bands = {
        (str(subject), str(day)): (float(up), float(down))  # type: ignore[arg-type]
        for subject, day, up, down in panel.rows_of(
            PRICE_LIMIT_DATASET, "trade_date", "up_limit", "down_limit"
        )
    }
    for subject, day, high, low, close in panel.rows_of(
        DAILY_DATASET, "trade_date", "high", "low", "close"
    ):
        band = bands.get((str(subject), str(day)))
        if band is None:
            continue
        session = date.fromisoformat(str(day))
        bar = DailyBar(
            ts_code=str(subject),
            trade_date=session,
            open=float(close),  # type: ignore[arg-type]
            high=float(high),  # type: ignore[arg-type]
            low=float(low),  # type: ignore[arg-type]
            close=float(close),  # type: ignore[arg-type]
            pre_close=float(close),  # type: ignore[arg-type]
            pct_chg=0.0,
            vol=1000.0,
            amount=10000.0,
        )
        limit = PriceLimit(
            ts_code=str(subject), trade_date=session, up_limit=band[0], down_limit=band[1]
        )
        if limit_touch(bar, limit).one_price_up:
            return True
    return False


def _has_inexact_weight_sum(panel: GeneratedPanel) -> bool:
    totals: dict[tuple[str, str], float] = {}
    for subject, day, weight in panel.rows_of(INDEX_WEIGHT_DATASET, "publication_date", "weight"):
        key = (str(subject), str(day))
        totals[key] = totals.get(key, 0.0) + float(weight)  # type: ignore[arg-type]
    return any(total != 100.0 for total in totals.values())


def _statement_keys(
    panel: GeneratedPanel, dataset: str
) -> dict[tuple[str, str, str], list[tuple[float, ...]]]:
    columns = STATEMENT_DATA_COLUMNS[dataset]
    values = [panel.column(dataset, name) for name in columns]
    keys: dict[tuple[str, str, str], list[tuple[float, ...]]] = {}
    batch = panel.batch(dataset)
    periods = panel.column(dataset, "report_period")
    announcements = panel.column(dataset, "ann_date")
    for index, subject in enumerate(batch.subjects):
        key = (subject, str(periods[index]), str(announcements[index]))
        keys.setdefault(key, []).append(tuple(float(column[index]) for column in values))  # type: ignore[arg-type]
    return keys


def _has_same_day_duplicate_versions(panel: GeneratedPanel) -> bool:
    return any(len(set(rows)) > 1 for rows in _statement_keys(panel, INCOME_DATASET).values())


def _has_three_versions_of_one_key(panel: GeneratedPanel) -> bool:
    """A key with three rows whose *first two agree* on a column a later one moves.

    The extra clause is the whole shape. Three rows that disagree pairwise from the start are
    already caught by comparing the first two, so a fixture built that way leaves
    `value_of`'s `values[1:]` free to be narrowed to `values[1:2]`.
    """
    for rows in _statement_keys(panel, INCOME_DATASET).values():
        if len(rows) < 3:
            continue
        for position in range(len(rows[0])):
            column = [row[position] for row in rows]
            if column[0] == column[1] and any(value != column[0] for value in column[2:]):
                return True
    return False


def _has_earlier_period_announced_later(panel: GeneratedPanel) -> bool:
    by_security: dict[str, list[tuple[str, str]]] = {}
    for subject, period, announced in panel.rows_of(INCOME_DATASET, "report_period", "ann_date"):
        by_security.setdefault(str(subject), []).append((str(announced), str(period)))
    return any(
        any(
            earlier_announcement < later_announcement and earlier_period > later_period
            for earlier_announcement, earlier_period in filings
            for later_announcement, later_period in filings
        )
        for filings in by_security.values()
    )


def _has_second_statement_dataset(panel: GeneratedPanel) -> bool:
    """A second statement endpoint with its own projection, not a copy of the first."""
    if CASH_FLOW_DATASET not in panel.batches:
        return False
    stored = {column.name for column in panel.batch(CASH_FLOW_DATASET).columns}
    return bool(stored & set(CASHFLOW_COLUMNS)) and not (stored & set(INCOME_COLUMNS))


def _labelled_statement_datasets(panel: GeneratedPanel) -> dict[str, bool]:
    """Every stored statement dataset, mapped to whether it carries a revision label."""
    return {
        dataset: REVISION_LABEL_COLUMN in {column.name for column in panel.batch(dataset).columns}
        for dataset in STATEMENT_DATASETS
        if dataset in panel.batches
    }


def _has_statement_dataset_without_a_revision_label(panel: GeneratedPanel) -> bool:
    """All four endpoints stored, and exactly the one that has no `update_flag` lacking it.

    Both halves are the shape. A panel holding only `fina_indicator` would satisfy "a dataset
    with no label" while proving nothing about the branch, because there would be no labelled
    dataset for the reader to have taken the wrong turning towards.
    """
    labelled = _labelled_statement_datasets(panel)
    if set(labelled) != set(STATEMENT_DATASETS):
        return False
    return {dataset for dataset, has_label in labelled.items() if not has_label} == {
        FINANCIAL_INDICATOR_DATASET
    }


def _transitions(panel: GeneratedPanel) -> Iterable[tuple[IndustryAssignment, IndustryAssignment]]:
    for assignments in panel.industry_assignments.values():
        yield from pairwise(sorted(assignments, key=lambda item: item.effective_from))


def _unclassified_sessions(
    panel: GeneratedPanel, previous: IndustryAssignment, current: IndustryAssignment
) -> int:
    through = previous.effective_through
    if through is None:
        return 0
    return sum(1 for session in panel.sessions if through < session < current.effective_from)


def _has_session_adjacent_handover(panel: GeneratedPanel) -> bool:
    """A hand-over that is *not* calendar-adjacent and still leaves no session uncovered.

    The band matters as much as the gap: a transition 6,000 days wide separates nothing, since
    every threshold from `> 1` to `> 400` calls it a gap. What discriminates them is a delta
    inside the market's own shape -- three days across a weekend, ten across a holiday week.
    """
    for previous, current in _transitions(panel):
        through = previous.effective_through
        if through is None:
            continue
        delta = (current.effective_from - through).days
        if 1 < delta <= 10 and _unclassified_sessions(panel, previous, current) == 0:
            return True
    return False


def _has_industry_coverage_hole(panel: GeneratedPanel) -> bool:
    return any(
        _unclassified_sessions(panel, previous, current) > 0
        for previous, current in _transitions(panel)
    )


_SHAPES: Final[tuple[PanelShape, ...]] = (
    PanelShape(
        shape_id="calendar.mid_window_weekday_closure",
        datasets=(TRADING_CALENDAR_DATASET,),
        summary="a weekday inside the session window on which the exchange was shut",
        measurement=(
            "605 of the 13,162 published SSE calendar rows are closed weekdays and zero "
            "weekends are open (live probe 2026-08-08, domain/trading_calendar.py)"
        ),
        detect=_has_mid_window_weekday_closure,
    ),
    PanelShape(
        shape_id="calendar.multi_session_recess",
        datasets=(TRADING_CALENDAR_DATASET,),
        summary="consecutive sessions more than a weekend apart",
        measurement=(
            "the 2015 Victory Day parade closed Thursday 2015-09-03 and Friday 2015-09-04, so "
            "2015-09-07's pretrade_date is 2015-09-02 -- five calendar days, which is the "
            "recess this shape builds. Two closed weekdays in a row is ordinary rather than "
            "exotic: 605 of the 13,162 published SSE calendar rows are closed weekdays and "
            "zero weekends are open (live probe 2026-08-10, domain/trading_calendar.py, whose "
            "KNOWN_CALENDAR_LOOKAHEAD names both of those two days)"
        ),
        detect=_has_multi_session_recess,
    ),
    PanelShape(
        shape_id="universe.delisted_security",
        datasets=(STOCK_BASIC_DATASET,),
        summary="a registry that carries a termination and not only listings",
        measurement=(
            "stock_basic's bare list_status='L' request returns 5,539 rows and 'L,D' returns "
            "5,878 -- 339 delisted securities a listed-only fixture cannot represent "
            "(live probe 2026-08-08, domain/stock_universe.py). The termination here falls "
            "after the session window on purpose: it puts the delisting *row* in the registry "
            "without shrinking the priced cross section, which write_daily_panel's 0.85 "
            "explained-share floor would refuse on a three-name universe."
        ),
        detect=_has_delisted_security,
    ),
    PanelShape(
        shape_id="universe.termination_on_the_newest_session",
        datasets=(STOCK_BASIC_DATASET,),
        summary="a lifecycle partition whose newest row lands on the newest priced session",
        measurement=(
            "the form V2-P4-076 was found on and no fixture had. A live panel measured "
            "2026-08-19 carried stock_basic at 2026-08-19T00:00+08 against a price panel whose "
            "newest session was the same day, and stock_basic is clocked calendar_static -- "
            "event_time == available_time == midnight on the lifecycle date "
            "(providers/tushare.py::_calendar_static_timeline). A live probe of the full "
            "registry on 2026-08-08 found lifecycle events in every year from 1990 to 2026, "
            "6,217 rows over 37 years (panel_ingest.py::load_stock_universe), so a year whose "
            "newest event is its newest session is the ordinary case rather than an exotic "
            "one; domain/stock_universe.py is what folds the two rows back into a lifecycle"
        ),
        detect=_has_termination_on_the_newest_session,
    ),
    PanelShape(
        shape_id="suspension.halt_on_the_newest_session",
        datasets=(SUSPENSION_DATASET,),
        summary="a halt corpus whose newest row lands on the newest priced session",
        measurement=(
            "V2-P4-076's second form. The same live panel carried suspend_d at "
            "2026-08-19T16:30+08, the newest session's own close -- suspend_d is clocked "
            "daily_close, so a halt is knowable at DAILY_AVAILABILITY_TIME on its own "
            "trade_date (providers/tushare.py's suspend_d descriptor). Halts on the newest "
            "session are routine rather than rare: 28 rows on 2024-06-28, 5 on 2026-08-07 and "
            "1,466 on 2015-07-09 (providers/tushare.py), and domain/labels.py is what reads "
            "the corpus back per session"
        ),
        detect=_has_halt_on_the_newest_session,
    ),
    PanelShape(
        shape_id="name_history.announcement_on_the_newest_session",
        datasets=(NAMECHANGE_DATASET,),
        summary="a rename corpus whose newest announcement lands on the newest priced session",
        measurement=(
            "V2-P4-076's third form, and the one that was already visible in a docstring: a "
            "live fetch on 2026-08-08 found namechange already carrying 920165.BJ / 珈凯生物 "
            "announced 2026-08-11, three days ahead of the fetch "
            "(providers/tushare.py::_calendar_static_timeline). Over the 14,166-row corpus "
            "6,150 rows carry announcement and effect on one day (domain/name_history.py), so "
            "an announcement dated on the newest session is the corpus's commonest shape"
        ),
        detect=_has_rename_announced_on_the_newest_session,
    ),
    PanelShape(
        shape_id="name_history.announcement_precedes_effect",
        datasets=(NAMECHANGE_DATASET,),
        summary="a rename announced months before it takes effect",
        measurement=(
            "000001.SZ was announced 平安银行 on 2012-01-20 and became it on 2012-08-02; over "
            "the 14,166-row corpus 6,150 rows carry the two clocks on the same day and the "
            "rest separate them (domain/name_history.py)"
        ),
        detect=_has_announcement_before_effect,
    ),
    PanelShape(
        shape_id="name_history.reform_prefixed_special_treatment",
        datasets=(NAMECHANGE_DATASET,),
        summary="a special-treated name the naive ST prefix test reads as ordinary",
        measurement=(
            "the 2005-2007 share reform put a G/S marker in front of the ST marker; "
            "name.startswith(('ST','*ST')) marks 2,592 rows of the corpus and the "
            "reform-aware rule marks 2,827, so 235 rows are special-treated names the naive "
            "rule calls ordinary (domain/name_history.py)"
        ),
        detect=_has_reform_prefixed_special_treatment,
    ),
    PanelShape(
        shape_id="adjustment.step_down",
        datasets=(ADJ_FACTOR_DATASET, DAILY_DATASET),
        summary="a cumulative factor that falls rather than rises",
        measurement=(
            "eight of 000001.SZ's own rows step down, and across the market between "
            "2023-12-29 and 2024-06-28 28 of 5,351 securities' factors fell -- so a "
            "monotonicity assumption is falsified by the data (domain/adjustment.py)"
        ),
        detect=_has_factor_step_down,
    ),
    PanelShape(
        shape_id="daily.close_moves_between_sessions",
        datasets=(DAILY_DATASET, DAILY_BASIC_DATASET),
        summary="a close that changes from one session to the next, not a per-name constant",
        measurement=(
            "000001.SZ closed 11.30 on 2026-06-11 and 11.24 on 2026-06-12 "
            "(domain/daily_prices.py). With a flat series every session's pre_close equals "
            "its own close, so a reader that compared a bar against itself instead of against "
            "the previous session would agree perfectly"
        ),
        detect=_has_moving_closes,
    ),
    PanelShape(
        shape_id="daily.ex_rights_session",
        datasets=(DAILY_DATASET, ADJ_FACTOR_DATASET),
        summary="a session whose pre_close is a restatement and whose factor moved with it",
        measurement=(
            "000001.SZ on 2026-06-12: close 11.24, pre_close 10.94 against the previous "
            "session's close of 11.30, adj_factor 134.5794 -> 139.008. close/prev_close - 1 is "
            "-0.53% and both correct paths give +2.74% (domain/daily_prices.py)"
        ),
        detect=_has_ex_rights_session,
    ),
    PanelShape(
        shape_id="daily.uncorroborated_factor_step",
        datasets=(DAILY_DATASET, ADJ_FACTOR_DATASET),
        summary="a restated pre_close the adjustment factors do not corroborate",
        measurement=(
            "the `V2-P1-006` Critical, reproduced: with 000001.SZ's 2026-06-12 row present in "
            "daily (close 11.24, pre_close 10.94 against 11.30) and the matching "
            "134.5794 -> 139.008 step absent from adj_factor, the partition is `ready` with "
            "`issues == []` and adjusted_return answers -0.530973% against a true +2.742251% "
            "-- the sign is wrong, not just the magnitude (domain/daily_prices.py). Across the "
            "market 28 of 5,351 securities' factors moved between 2023-12-29 and 2024-06-28, "
            "so a fetch that drops one is a routine partial read rather than an exotic one"
        ),
        detect=_has_uncorroborated_factor_step,
        provokes=("return_path_disagreement",),
    ),
    PanelShape(
        shape_id="financials.announced_after_the_as_of",
        datasets=(INCOME_DATASET,),
        summary="a filing that had not been announced yet when the panel is read",
        measurement=(
            "the announcement clock is the only thing standing between a reader and next "
            "quarter's numbers: income dates every row at its own ann_date, so 001278.SZ's "
            "2018 annual report -- announced 2022-01-06 -- is invisible to every as_of before "
            "that day and lands in the 2022 partition (domain/financial_statements.py). A "
            "corpus fetched on one day and read as of an earlier one is the ordinary case, "
            "not a contrived one: `panel build` writes what it can see now"
        ),
        detect=_has_disclosure_after_the_as_of,
        provokes=("not_yet_knowable", "check_unavailable"),
    ),
    PanelShape(
        shape_id="daily.bar_without_valuation",
        datasets=(DAILY_DATASET, DAILY_BASIC_DATASET),
        summary="a security with a daily bar and no daily_basic row that session",
        measurement=(
            "daily_basic was a subset of daily on every session probed and never a superset; "
            "all 60 of 2020-03-02's missing names are Beijing-board codes "
            "(domain/daily_prices.py). The direction is the whole point: a bar without a "
            "valuation is ordinary and a valuation without a bar is a contradiction"
        ),
        detect=_has_bar_without_valuation,
    ),
    PanelShape(
        shape_id="suspension.timed_interruption",
        datasets=(SUSPENSION_DATASET, DAILY_DATASET),
        summary="an S row carrying an intraday window whose security still traded",
        measurement=(
            "2015-07-08 served 1,343 S rows, 31 of them with a suspend_timing window, and all "
            "31 have a daily bar while none of the other 1,312 do (domain/price_limits.py)"
        ),
        detect=_has_timed_interruption,
    ),
    PanelShape(
        shape_id="suspension.resumption",
        datasets=(SUSPENSION_DATASET, DAILY_DATASET),
        summary="an R row, which is a resumption and not a halt",
        measurement=(
            "2024-06-28's 28 suspend_d rows are 26 S and 2 R, and every R row probed across "
            "eight sessions has a daily bar (27/27, 5/5, 17/17, 6/6, 5/5, 2/2, 1/1, 1/1) "
            "(domain/price_limits.py)"
        ),
        detect=_has_resumption,
    ),
    PanelShape(
        shape_id="price_limits.limit_free_sentinel",
        datasets=(PRICE_LIMIT_DATASET,),
        summary="a session published with no price limit, encoded as an out-of-range band",
        measurement=(
            "1,387 of 1,918,266 rows over 459 whole-market sessions are sentinels in six "
            "distinct encodings; SSE has published (99999.999, 0.01) since 2023-06-21. The "
            "widest real band is 1.4409x pre_close and the narrowest sentinel 115.61x "
            "(domain/price_limits.py)"
        ),
        detect=_has_limit_free_sentinel,
    ),
    PanelShape(
        shape_id="price_limits.one_price_limit_up",
        datasets=(PRICE_LIMIT_DATASET, DAILY_DATASET),
        summary="a session that traded at one price, and that price was the upper band",
        measurement=(
            "the 一字板 the two tradability contracts both claim to judge and judge "
            "differently. limit_touch separates at_up (the bar reached its band) from "
            "one_price_up (the whole session traded there) and only the second means there "
            "was no counterparty; domain/labels.py refuses such a session on either end "
            "(REFUSAL_LOCKED_AT_LIMIT) while backtest/execution.py refuses only the buy side "
            "of it. Without this shape the generated panel has no locked session at all -- "
            "every band is the session's own close +/-10% against a bar whose high, low and "
            "close are equal -- so the whole one-price half of both contracts was reachable "
            "only from hand-written carriers (domain/price_limits.py)"
        ),
        detect=_has_one_price_limit_up,
    ),
    PanelShape(
        shape_id="index.published_weights_do_not_sum_to_exactly_one_hundred",
        datasets=(INDEX_WEIGHT_DATASET,),
        summary="a publication whose rounded weight column misses 100",
        measurement=(
            "the three indices summed to 100.002, 99.997 and 100.002 on 2024-06-28; over the "
            "633-publication corpus the deviation has a median of 0.005 and a maximum of 0.21 "
            "(domain/index_membership.py). A fixture that sums to exactly 100 lets an equality "
            "check stand in for the derived tolerance"
        ),
        detect=_has_inexact_weight_sum,
    ),
    PanelShape(
        shape_id="index.publication_after_the_as_of",
        datasets=(INDEX_WEIGHT_DATASET,),
        summary="a second monthly publication, with a changed membership, dated after the read",
        measurement=(
            "the response carries no announcement column, so availability is dated at the "
            "publication session's own close over all 633 publications "
            "(KNOWN_INDEX_MEMBERSHIP_LIMITATIONS.publication_lag_is_not_in_the_response, "
            "domain/index_membership.py) -- which makes 'a publication the read cannot see yet' "
            "the ordinary monthly case rather than a contrived one. It is a composition change "
            "and not a repeat because 166 of the 630 measured publication-to-publication "
            "transitions changed the membership; 000300.SH's 2026-05-29 and 2026-06-30 "
            "publications differ by 19 names, 000905.SH's by 50 and 000852.SH's by 100. Those "
            "two numbers are cited for the size of a real rebalance and nothing else -- the "
            "49,900 name-session look-ahead they belong to is "
            "scheduled_review_takes_effect_before_it_is_published, a rebalance that took effect "
            "BEFORE the publication reporting it, which is the opposite direction from this "
            "shape and is not what this injects"
        ),
        detect=_has_publication_after_the_as_of,
        provokes=("not_yet_knowable",),
    ),
    PanelShape(
        shape_id="industry.reclassification_after_the_as_of",
        datasets=(INDUSTRY_MEMBERSHIP_DATASET,),
        summary="an assignment that takes effect after the read, closing one that already had",
        measurement=(
            "neither industry endpoint carries an announcement column, so inside a vintage's own "
            "era an assignment's availability is dated at in_date's midnight "
            "(KNOWN_INDUSTRY_LIMITATIONS.no_announcement_and_no_revision_history, "
            "domain/industry_classification.py) -- so on this dataset 'a reclassification the "
            "read cannot see yet' is exactly an in_date after the as_of, with nothing else to "
            "distinguish it. The corpus's 7,893 membership rows over 5,889 securities hold "
            "2,004 superseded intervals, so a security whose interval closes and reopens is the "
            "form the feed actually serves. This is not the 1,038,252-pair (10.85%) SW2021 "
            "backfill the module's own docstring measures: that one is a 2021 taxonomy stated "
            "over 1984 memberships, a revision erased BACKWARDS, where this is an assignment "
            "the read is standing in front of"
        ),
        detect=_has_reclassification_after_the_as_of,
        provokes=("not_yet_knowable",),
    ),
    PanelShape(
        shape_id="financials.same_day_duplicate_versions",
        datasets=(INCOME_DATASET,),
        summary="one (period, announcement) key carrying two rows that disagree",
        measurement=(
            "income serves 3,836 rows under 3,201 keys, 633 of which carry more than one row "
            "and 259 of those disagree; fina_indicator's figure is 81.7% of its keys "
            "(53-security probe 2026-08-09, domain/financial_statements.py)"
        ),
        detect=_has_same_day_duplicate_versions,
        provokes=("ambiguous_filing", "duplicate_versions"),
    ),
    PanelShape(
        shape_id="financials.three_versions_of_one_key",
        datasets=(INCOME_DATASET,),
        summary="a key with three rows whose first two agree on a column a third moves",
        measurement=(
            "002514.SZ's 2022 annual report, announced 2023-03-15, is served as three rows "
            "under one (period, announcement) key with update_flag 0/1/1, and its "
            "total_revenue reads 684026684.14, 684026684.14, 667399250.52 -- the first two "
            "agreeing and the third moving, on nine of the ten stored columns at once. "
            "000981.SZ's 2025 interim announced 2025-08-28 has the same shape "
            "(n_income 202494549.62, 202494549.62, 198527426.34; basic_eps 0.0219, 0.0219, "
            "0.0215) and so does 002682.SZ's 2025 annual announced 2026-04-24. 4 of the "
            "15,801 keys a 277-security probe returned carry three raw rows "
            "(live probe 2026-08-10, domain/financial_statements.py)"
        ),
        detect=_has_three_versions_of_one_key,
        provokes=("ambiguous_filing", "duplicate_versions"),
    ),
    PanelShape(
        shape_id="financials.earlier_period_announced_later",
        datasets=(INCOME_DATASET,),
        summary="a security whose newest announcement carries the older report period",
        measurement=(
            "920403.BJ re-announced its 2023 interim on 2024-01-05, six weeks after its 2023 "
            "Q3 report. Over a 76-security probe on 2026-08-09 period order and announcement "
            "order disagree on 12 of income's 3,796 answerable days, and 11 of the 76 "
            "securities have at least one such day (domain/financial_statements.py)"
        ),
        detect=_has_earlier_period_announced_later,
    ),
    PanelShape(
        shape_id="financials.second_statement_dataset",
        datasets=(INCOME_DATASET, CASH_FLOW_DATASET),
        summary="a second statement endpoint with its own projection",
        measurement=(
            "the four endpoints are four separate partition sets with four different "
            "projections (10 / 7 / 5 / 12 stored columns) and four different ambiguity rates "
            "-- 8.15%, 1.29%, 15.80%, 13.70% of filings "
            "(domain/financial_statements.py). A single-dataset fixture cannot tell a loop "
            "over them from a read of the first one"
        ),
        detect=_has_second_statement_dataset,
    ),
    PanelShape(
        shape_id="financials.statement_dataset_without_a_revision_label",
        datasets=(
            INCOME_DATASET,
            BALANCE_SHEET_DATASET,
            CASH_FLOW_DATASET,
            FINANCIAL_INDICATOR_DATASET,
        ),
        summary="the fourth endpoint, which carries neither update_flag nor f_ann_date",
        measurement=(
            "three of the four endpoints carry update_flag and f_ann_date and fina_indicator "
            "carries neither, which is why DATASETS_WITH_REVISION_LABEL is a declared tuple of "
            "3 rather than a dataset-name test -- and it is the endpoint that needs them most: "
            "2,671 of its 3,270 keys (81.7%) hold more than one row, against income's 633 of "
            "3,201 (53-security probe 2026-08-09, domain/financial_statements.py). A fixture "
            "storing only labelled datasets cannot tell a reader that branches on the label's "
            "presence from one that assumes it"
        ),
        detect=_has_statement_dataset_without_a_revision_label,
    ),
    PanelShape(
        shape_id="industry.session_adjacent_handover",
        datasets=(INDUSTRY_MEMBERSHIP_DATASET, TRADING_CALENDAR_DATASET),
        summary="a reclassification across a closure that leaves no session unclassified",
        measurement=(
            "600354.SH hands over Friday 2007-06-29 to Monday 2007-07-02. 442 of the corpus's "
            "2,004 transitions are not calendar-adjacent and only 49 are real gaps; the other "
            "393 are this, with deltas of 3 days (237), 10 (48), 4 (47), 5 (32) and 2 (14) "
            "(domain/industry_classification.py)"
        ),
        detect=_has_session_adjacent_handover,
    ),
    PanelShape(
        shape_id="industry.coverage_hole",
        datasets=(INDUSTRY_MEMBERSHIP_DATASET, TRADING_CALENDAR_DATASET),
        summary="a reclassification that leaves the security unclassified for whole sessions",
        measurement=(
            "49 of the 2,004 transitions leave a gap rather than handing straight over: "
            "000639.SZ ST西王 is 社会服务 through 2002-08-29 and unclassified for 4,103 "
            "sessions until 2019-07-24, 000716.SZ 黑芝麻 for 3,428 and 600365.SH ST通葡 for "
            "3,299 (domain/industry_classification.py)"
        ),
        detect=_has_industry_coverage_hole,
    ),
)

PANEL_SHAPES: Final[Mapping[str, PanelShape]] = MappingProxyType(
    {shape.shape_id: shape for shape in _SHAPES}
)
"""Every form `generate_panel` can produce, keyed by id.

Stated as one table for `panel_gate.GATE_CODE_BLOCKS`' reason: a verdict per entry over a
closed set is a diff against a literal when it changes, where the same facts spread across
per-test assertions are a set of independent judgements nobody can total up.
"""

EVERY_SHAPE: Final[tuple[str, ...]] = tuple(PANEL_SHAPES)


# --- generation -----------------------------------------------------------------------------


def _requested(shapes: Iterable[str]) -> frozenset[str]:
    asked = frozenset(shapes)
    unknown = sorted(asked - set(PANEL_SHAPES))
    if unknown:
        raise PanelFixtureError(
            f"unknown panel shape(s) {unknown}; PANEL_SHAPES declares {sorted(PANEL_SHAPES)}"
        )
    return asked


def _midnight_shanghai(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=8)


def _close_time(day: date) -> datetime:
    """15:00 Asia/Shanghai, the session's event instant."""
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)


def _published_time(day: date) -> datetime:
    """16:30 Asia/Shanghai, when the session became knowable."""
    return datetime(day.year, day.month, day.day, 8, 30, tzinfo=UTC)


def _batch(
    dataset: str,
    *,
    subjects: Sequence[str],
    columns: Sequence[PanelColumn],
    event_time: Sequence[datetime],
    available_time: Sequence[datetime],
    revision_time: Sequence[datetime] | None = None,
    fetched_at: datetime = AS_OF,
) -> ColumnarPanelBatch:
    """One batch, fetched at `AS_OF` unless a shape needs a later fetch.

    `fetched_at` is the whole mechanism behind `financials.announced_after_the_as_of`.
    `ColumnarPanelBatch` refuses a row whose `available_time` is after its own `as_of`
    (`domain/panel_batch.py::_check_visible_at_as_of`), so a row that is not yet knowable *at
    the read's* `as_of` cannot be smuggled in by lying about the batch -- it has to be a batch
    genuinely fetched later, which is what a real corpus built on Tuesday and queried as of
    Friday-before is. The read's `as_of` stays `AS_OF`; only the fetch moves.
    """
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=dataset,
        kind=dataset,
        as_of=fetched_at,
        fetched_at=fetched_at,
        status="success",
        subjects=tuple(subjects),
        timeline=TimelineColumns(
            event_time=tuple(event_time),
            available_time=tuple(available_time),
            ingested_time=tuple(available_time),
            revision_time=tuple(revision_time if revision_time is not None else available_time),
        ),
        columns=tuple(columns),
    )


CLOSED_WEEKDAY: Final[date] = date(2026, 1, 8)
RECESS_WEEKDAYS: Final[tuple[date, ...]] = (date(2026, 1, 8), date(2026, 1, 9))


def _closed_days(shapes: frozenset[str]) -> frozenset[date]:
    closed = set(NEW_YEAR_RECESS)
    if "calendar.mid_window_weekday_closure" in shapes:
        closed.add(CLOSED_WEEKDAY)
    if "calendar.multi_session_recess" in shapes:
        closed.update(RECESS_WEEKDAYS)
    return frozenset(closed)


def _calendar_days(shapes: frozenset[str]) -> tuple[CalendarDay, ...]:
    closed = _closed_days(shapes)
    span = (LAST_DAY - FIRST_DAY).days + 1
    days = [FIRST_DAY + timedelta(days=offset) for offset in range(span)]
    return tuple(
        CalendarDay(calendar_date=day, is_trading=day.weekday() < 5 and day not in closed)
        for day in days
    )


def _calendar_batch(days: Sequence[CalendarDay]) -> ColumnarPanelBatch:
    previous: list[str | None] = []
    seen: date | None = None
    for day in days:
        previous.append(seen.isoformat() if seen is not None else None)
        if day.is_trading:
            seen = day.calendar_date
    return _batch(
        TRADING_CALENDAR_DATASET,
        subjects=[EXCHANGE] * len(days),
        columns=[
            PanelColumn("cal_date", "string", tuple(day.calendar_date.isoformat() for day in days)),
            PanelColumn("is_open", "boolean", tuple(day.is_trading for day in days)),
            PanelColumn("pretrade_date", "string", tuple(previous)),
        ],
        event_time=[_midnight_shanghai(day.calendar_date) for day in days],
        available_time=[_midnight_shanghai(FIRST_DAY)] * len(days),
    )


TERMINATED_SECURITY_INDEX: Final[int] = 4
"""`securities[4]`, which is the one name no other shape writes a lifecycle event for.

`securities[0]` carries the ex-rights step, the limit-free sentinel and the timed halt,
`securities[1]` the factor step-down and the reform-marked rename, `securities[2]` the
uncorroborated restatement, `securities[3]` the locked band and `securities[-1]` the whole-day
halt and the resumption. `securities[4]` is free, and it has to be a name the price grid already
prices for every session: `delist_date` is exclusive, so a termination dated on the newest
session leaves the name listed for every session but that one, and each of those sessions then
wants a bar. Terminating a name the grid does *not* price is what
`universe.termination_on_the_newest_session` cannot be built from -- measured, on a listing
dated the same day: `panel doctor` answers `check_unavailable` because the unpriced cross-check
cannot read an adjustment factor for a security the price panel never carried.
"""


def _universe_batch(securities: Sequence[str], shapes: frozenset[str]) -> ColumnarPanelBatch:
    codes = list(securities)
    events = [LISTING_EVENT] * len(codes)
    days = [LISTED_ON] * len(codes)
    if "universe.termination_on_the_newest_session" in shapes:
        # One row rather than two, unlike `universe.delisted_security`: this name's listing is
        # already in the batch above, so only the termination is new.
        codes.append(securities[TERMINATED_SECURITY_INDEX])
        events.append(DELISTING_EVENT)
        days.append(WINDOW_LAST)
    if "universe.delisted_security" in shapes:
        # Two rows, not one: `providers/tushare.py` files a listing and a termination as
        # separate panel rows precisely because they became knowable at different instants,
        # and a registry row carrying only the termination is not a shape the feed produces.
        codes.extend((DELISTED_SECURITY, DELISTED_SECURITY))
        events.extend((LISTING_EVENT, DELISTING_EVENT))
        days.extend((LISTED_ON, DELISTED_ON))
    return _batch(
        STOCK_BASIC_DATASET,
        subjects=codes,
        columns=[
            PanelColumn("lifecycle_event", "string", tuple(events)),
            PanelColumn("lifecycle_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("exchange", "string", tuple(EXCHANGE for _ in codes)),
        ],
        event_time=[_midnight_shanghai(day) for day in days],
        available_time=[_midnight_shanghai(day) for day in days],
    )


EX_RIGHTS_FACTOR: Final[float] = 1.05
STEP_DOWN_FACTOR: Final[float] = 0.98
EX_RIGHTS_INDEX: Final[int] = 3
STEP_DOWN_INDEX: Final[int] = 6


def _factor_of(
    code: str,
    day: date,
    *,
    sessions: Sequence[date],
    securities: Sequence[str],
    shapes: frozenset[str],
) -> float:
    """The cumulative factor in force on `day`, as a step function over the sessions."""
    factor = 1.0
    if (
        "daily.ex_rights_session" in shapes
        and code == securities[0]
        and day >= sessions[EX_RIGHTS_INDEX]
    ):
        factor = EX_RIGHTS_FACTOR
    if (
        "adjustment.step_down" in shapes
        and code == securities[1]
        and day >= sessions[STEP_DOWN_INDEX]
    ):
        factor = STEP_DOWN_FACTOR
    return factor


def _close_of(
    code: str,
    day: date,
    *,
    sessions: Sequence[date],
    securities: Sequence[str],
    shapes: frozenset[str],
) -> float:
    base = BASE_CLOSE + securities.index(code)
    if "daily.close_moves_between_sessions" not in shapes:
        return base
    return base + SESSION_STEP * sessions.index(day)


UNCORROBORATED_RESTATEMENT: Final[float] = 1.05
UNCORROBORATED_SECURITY_INDEX: Final[int] = 2
"""`securities[2]`, on the panel's **last** session.

The security index keeps this shape off `daily.ex_rights_session`'s `securities[0]` and
`adjustment.step_down`'s `securities[1]`, so all three compose. The session is the last one
because the three session-scoped cross-checks run only on the sessions the *request* names
(`panel_gate.SESSION_SCOPED_CROSS_CHECKS`), and `panel.sessions[-1]` is the one a caller
reaches for -- a defect on an unnamed session is invisible to the gate, which is a true fact
about the gate and a useless place to put a fixture.
"""


def _published_factor_of(
    code: str,
    day: date,
    *,
    sessions: Sequence[date],
    securities: Sequence[str],
    shapes: frozenset[str],
) -> float:
    """The factor the *published* `pre_close` was restated by.

    Equal to `_factor_of` everywhere except where `daily.uncorroborated_factor_step` pulls the
    two apart on purpose -- which is the whole of that shape: `adj_factor` keeps saying nothing
    happened while `daily` restates its previous close as though something did.
    """
    factor = _factor_of(code, day, sessions=sessions, securities=securities, shapes=shapes)
    if (
        "daily.uncorroborated_factor_step" in shapes
        and code == securities[UNCORROBORATED_SECURITY_INDEX]
        and day >= sessions[-1]
    ):
        return factor * UNCORROBORATED_RESTATEMENT
    return factor


def _pre_close_of(
    code: str,
    day: date,
    *,
    sessions: Sequence[date],
    securities: Sequence[str],
    shapes: frozenset[str],
) -> float:
    """The published previous close: restated by whatever the factors did overnight.

    `previous_close * f_prev / f`, the same arithmetic `session_returns` implies, so the
    published and the factor-adjusted return paths agree exactly on every generated session
    and disagree only where a shape deliberately makes them.
    """
    position = sessions.index(day)
    previous = sessions[position - 1] if position else None
    if previous is None:
        return _close_of(code, day, sessions=sessions, securities=securities, shapes=shapes) - (
            SESSION_STEP if "daily.close_moves_between_sessions" in shapes else 0.0
        )
    earlier = _close_of(code, previous, sessions=sessions, securities=securities, shapes=shapes)
    factor = _published_factor_of(
        code, day, sessions=sessions, securities=securities, shapes=shapes
    )
    previous_factor = _published_factor_of(
        code, previous, sessions=sessions, securities=securities, shapes=shapes
    )
    return earlier * previous_factor / factor


def _factor_batch(
    *, sessions: Sequence[date], securities: Sequence[str], shapes: frozenset[str]
) -> ColumnarPanelBatch:
    pairs = [(day, code) for day in sessions for code in securities]
    return _batch(
        ADJ_FACTOR_DATASET,
        subjects=[code for _, code in pairs],
        columns=[
            PanelColumn("factor_date", "string", tuple(day.isoformat() for day, _ in pairs)),
            PanelColumn(
                "adj_factor",
                "float",
                tuple(
                    _factor_of(code, day, sessions=sessions, securities=securities, shapes=shapes)
                    for day, code in pairs
                ),
            ),
        ],
        event_time=[_close_time(day) for day, _ in pairs],
        available_time=[_published_time(day) for day, _ in pairs],
    )


def _halted_key(sessions: Sequence[date], securities: Sequence[str]) -> tuple[str, date]:
    """The one untimed halt the shapeless panel carries, so `suspend_d` has a partition."""
    return securities[-1], sessions[4]


def _timed_key(sessions: Sequence[date], securities: Sequence[str]) -> tuple[str, date]:
    return securities[0], sessions[2]


def _resumed_key(sessions: Sequence[date], securities: Sequence[str]) -> tuple[str, date]:
    return securities[-1], sessions[5]


NEWEST_HALT_SECURITY_INDEX: Final[int] = 5
"""`securities[5]`, which no other halt row names.

`_halted_key` is `securities[-1]` and `_timed_key` is `securities[0]`, so a third name keeps
`suspension.halt_on_the_newest_session` from being read as either of those two moved.
"""


def _missing_bars(
    *, sessions: Sequence[date], securities: Sequence[str]
) -> frozenset[tuple[str, date]]:
    return frozenset({_halted_key(sessions, securities)})


def _missing_valuations(
    *, sessions: Sequence[date], securities: Sequence[str], shapes: frozenset[str]
) -> frozenset[tuple[str, date]]:
    missing = set(_missing_bars(sessions=sessions, securities=securities))
    if "daily.bar_without_valuation" in shapes:
        missing.add((securities[0], sessions[-1]))
    return frozenset(missing)


def _price_grid(
    *, sessions: Sequence[date], securities: Sequence[str], omit: frozenset[tuple[str, date]]
) -> tuple[tuple[date, str], ...]:
    return tuple((day, code) for day in sessions for code in securities if (code, day) not in omit)


def _bar_batch(
    *, sessions: Sequence[date], securities: Sequence[str], shapes: frozenset[str]
) -> ColumnarPanelBatch:
    pairs = _price_grid(
        sessions=sessions,
        securities=securities,
        omit=_missing_bars(sessions=sessions, securities=securities),
    )
    close = tuple(
        _close_of(code, day, sessions=sessions, securities=securities, shapes=shapes)
        for day, code in pairs
    )
    pre_close = tuple(
        _pre_close_of(code, day, sessions=sessions, securities=securities, shapes=shapes)
        for day, code in pairs
    )
    return _batch(
        DAILY_DATASET,
        subjects=[code for _, code in pairs],
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day, _ in pairs)),
            PanelColumn("open", "float", close),
            PanelColumn("high", "float", close),
            PanelColumn("low", "float", close),
            PanelColumn("close", "float", close),
            PanelColumn("pre_close", "float", pre_close),
            PanelColumn(
                "pct_chg",
                "float",
                tuple(
                    (value / before - 1.0) * 100.0
                    for value, before in zip(close, pre_close, strict=True)
                ),
            ),
            PanelColumn("vol", "float", tuple(1000.0 for _ in close)),
            PanelColumn("amount", "float", tuple(10000.0 for _ in close)),
        ],
        event_time=[_close_time(day) for day, _ in pairs],
        available_time=[_published_time(day) for day, _ in pairs],
    )


def _valuation_batch(
    *, sessions: Sequence[date], securities: Sequence[str], shapes: frozenset[str]
) -> ColumnarPanelBatch:
    pairs = _price_grid(
        sessions=sessions,
        securities=securities,
        omit=_missing_valuations(sessions=sessions, securities=securities, shapes=shapes),
    )
    close = tuple(
        _close_of(code, day, sessions=sessions, securities=securities, shapes=shapes)
        for day, code in pairs
    )
    return _batch(
        DAILY_BASIC_DATASET,
        subjects=[code for _, code in pairs],
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day, _ in pairs)),
            PanelColumn("close", "float", close),
            *(
                PanelColumn(name, "float", tuple(1.0 for _ in close))
                for name in DAILY_BASIC_EXTRA_COLUMNS
            ),
        ],
        event_time=[_close_time(day) for day, _ in pairs],
        available_time=[_published_time(day) for day, _ in pairs],
    )


def _suspension_batch(
    *, sessions: Sequence[date], securities: Sequence[str], shapes: frozenset[str]
) -> ColumnarPanelBatch:
    rows: list[tuple[str, date, str, str | None]] = []
    halted_code, halted_day = _halted_key(sessions, securities)
    rows.append((halted_code, halted_day, "S", None))
    if "suspension.timed_interruption" in shapes:
        code, day = _timed_key(sessions, securities)
        rows.append((code, day, "S", "13:00-15:00"))
    if "suspension.resumption" in shapes:
        code, day = _resumed_key(sessions, securities)
        rows.append((code, day, "R", None))
    if "suspension.halt_on_the_newest_session" in shapes:
        # Timed rather than whole-day, so the name keeps its bar: `_missing_bars` withholds a
        # bar for `_halted_key` alone, and a whole-day halt with a stored bar beside it is a
        # contradiction the price writer's own cross-check would be right to object to.
        rows.append((securities[NEWEST_HALT_SECURITY_INDEX], sessions[-1], "S", "13:00-15:00"))
    return _batch(
        SUSPENSION_DATASET,
        subjects=[code for code, _, _, _ in rows],
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for _, day, _, _ in rows)),
            PanelColumn("suspend_type", "string", tuple(kind for _, _, kind, _ in rows)),
            PanelColumn("suspend_timing", "string", tuple(timing for _, _, _, timing in rows)),
        ],
        event_time=[_close_time(day) for _, day, _, _ in rows],
        available_time=[_published_time(day) for _, day, _, _ in rows],
    )


SENTINEL_UP_LIMIT: Final[float] = 99999.999
SENTINEL_DOWN_LIMIT: Final[float] = 0.01
"""The pair, and it is one of the six encodings actually published rather than merely a band
the ratio test rejects.

`_has_limit_free_sentinel` asks `PriceLimit.is_bounded`, which is a ratio test, so it would
answer `True` for any band wide enough -- 200.0 against a ten-yuan close is 20x and passes it,
while falling in the hole between the widest real band (1.4409x pre_close) and the narrowest
sentinel (115.61x) and so being a value the feed never serves. That is the difference between
"a fixture the domain rule accepts" and "the shape the data has", so
`test_panel_fixtures.py::test_the_generated_limit_free_band_is_one_the_exchange_actually_publishes`
pins the pair to (99999.999, 0.01) -- what SSE has published since 2023-06-21.
"""


LOCKED_SECURITY_INDEX: Final[int] = 3
LOCKED_SESSION_INDEX: Final[int] = 1
"""`securities[3]` on `sessions[1]`, which is the one cell no other shape writes to.

`securities[0]` carries the ex-rights step, the limit-free sentinel and the timed halt,
`securities[1]` the factor step-down, `securities[2]` the uncorroborated restatement and
`securities[-1]` the whole-day halt and the resumption; `sessions[0]` is the sentinel's session
and the first session of every window a label test builds. So a panel carrying every shape at
once still has exactly one locked session, which is what lets the parity test name it.
"""


def _locked_key(sessions: Sequence[date], securities: Sequence[str]) -> tuple[str, date]:
    return securities[LOCKED_SECURITY_INDEX], sessions[LOCKED_SESSION_INDEX]


def _limit_batch(
    *, sessions: Sequence[date], securities: Sequence[str], shapes: frozenset[str]
) -> ColumnarPanelBatch:
    pairs = [(day, code) for day in sessions for code in securities]
    sentinel: tuple[str, date] | None = None
    if "price_limits.limit_free_sentinel" in shapes:
        sentinel = (securities[0], sessions[0])
    locked: tuple[str, date] | None = None
    if "price_limits.one_price_limit_up" in shapes:
        locked = _locked_key(sessions, securities)
    up: list[float] = []
    down: list[float] = []
    for day, code in pairs:
        if sentinel is not None and (code, day) == sentinel:
            up.append(SENTINEL_UP_LIMIT)
            down.append(SENTINEL_DOWN_LIMIT)
            continue
        reference = _close_of(code, day, sessions=sessions, securities=securities, shapes=shapes)
        if locked is not None and (code, day) == locked:
            # The band is the lever, not the bar: every generated bar already has
            # `open == high == low == close`, so publishing an upper limit *at* that price is
            # exactly `limit_touch`'s `one_price_up` (`low >= up_limit`) and nothing else in
            # the panel moves. Restating the close instead would restate the next session's
            # `pre_close` and every return chained through it.
            up.append(reference)
            down.append(reference * 0.9)
            continue
        up.append(reference * 1.1)
        down.append(reference * 0.9)
    return _batch(
        PRICE_LIMIT_DATASET,
        subjects=[code for _, code in pairs],
        columns=[
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day, _ in pairs)),
            PanelColumn("up_limit", "float", tuple(up)),
            PanelColumn("down_limit", "float", tuple(down)),
        ],
        event_time=[_close_time(day) for day, _ in pairs],
        available_time=[_published_time(day) for day, _ in pairs],
    )


INEXACT_WEIGHT: Final[float] = 33.33
"""Three cells at two decimals summing to 99.99.

Within `published_weight_tolerance(count=3, decimals=2)` -- 0.015 -- and outside anything an
equality test would accept, which is the entire point of the shape.
"""


def _index_weight_batch(
    *, sessions: Sequence[date], securities: Sequence[str], shapes: frozenset[str]
) -> ColumnarPanelBatch:
    """One publication a month, and a second month only when a shape asks for one.

    The first publication is byte-identical whether or not `index.publication_after_the_as_of`
    is requested, which is what makes the pair of panels a controlled comparison: the injected
    panel is the clean one plus `FUTURE_PUBLICATION`'s rows and nothing else.
    """
    if "index.published_weights_do_not_sum_to_exactly_one_hundred" in shapes:
        constituents = tuple(securities[:3])
        weights = tuple(INEXACT_WEIGHT for _ in constituents)
    else:
        constituents = tuple(securities[:2])
        weights = tuple(100.0 / len(constituents) for _ in constituents)
    publications: list[tuple[date, tuple[str, ...], tuple[float, ...]]] = [
        (sessions[-1], constituents, weights)
    ]
    if "index.publication_after_the_as_of" in shapes:
        # One name dropped and one added against whichever first publication was built, so the
        # later publication is a rebalance rather than a repeat -- otherwise a read that leaked
        # it would answer the same constituents and the leak would be undetectable.
        later = tuple(securities[1:3])
        publications.append((FUTURE_PUBLICATION, later, tuple(100.0 / len(later) for _ in later)))
    rows = [
        (day, con_code, weight)
        for day, codes, cells in publications
        for con_code, weight in zip(codes, cells, strict=True)
    ]
    available = [_published_time(day) for day, _, _ in rows]
    return _batch(
        INDEX_WEIGHT_DATASET,
        subjects=[INDEX_CODE] * len(rows),
        columns=[
            PanelColumn("publication_date", "string", tuple(day.isoformat() for day, _, _ in rows)),
            PanelColumn("con_code", "string", tuple(code for _, code, _ in rows)),
            PanelColumn("weight", "float", tuple(weight for _, _, weight in rows)),
        ],
        event_time=[_close_time(day) for day, _, _ in rows],
        available_time=available,
        fetched_at=max([AS_OF, *available]),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _StatementRow:
    security: str
    period: date
    announced_on: date
    label: str
    values: tuple[float, ...]


DISAGREEING_VALUE: Final[float] = 999.0
AGREED_VALUE: Final[float] = 101.0
"""The value `securities[1]`'s first *two* versions both carry in the disputed column.

Equal to the base row's `100.0 + 1` on purpose: the agreement has to be with the row the
generator already emits, not with a row added alongside it, or the three-version key would be
two pairs rather than a first-two-agree triple.
"""
THIRD_VERSION_VALUE: Final[float] = 777.0


def _income_rows(securities: Sequence[str], shapes: frozenset[str]) -> tuple[_StatementRow, ...]:
    """One filing per security, then whichever extra versions and periods were asked for.

    The disagreement always rides on the projection's **first** value column, so the same rule
    describes every statement dataset through a different column name.
    """
    width = len(INCOME_COLUMNS)
    rows = [
        _StatementRow(
            security=code,
            period=BASE_PERIOD,
            announced_on=BASE_ANNOUNCEMENT,
            label="1",
            values=(100.0 + index, *(1.0 for _ in range(width - 1))),
        )
        for index, code in enumerate(securities)
    ]
    if "financials.same_day_duplicate_versions" in shapes:
        rows.append(
            _StatementRow(
                security=securities[0],
                period=BASE_PERIOD,
                announced_on=BASE_ANNOUNCEMENT,
                label="0",
                values=(DISAGREEING_VALUE, *(1.0 for _ in range(width - 1))),
            )
        )
    if "financials.three_versions_of_one_key" in shapes:
        # securities[1]'s own first row already carries 101.0 in the disputed column. The
        # second row repeats that 101.0 and differs elsewhere -- so it stays a distinct
        # version rather than collapsing -- and only the third moves the disputed column.
        # A comparison narrowed to `values[1:2]` therefore sees 101.0 against 101.0 and
        # answers where the contract refuses.
        rows.append(
            _StatementRow(
                security=securities[1],
                period=BASE_PERIOD,
                announced_on=BASE_ANNOUNCEMENT,
                label="0",
                values=(AGREED_VALUE, *(1.0 for _ in range(width - 2)), 2.0),
            )
        )
        rows.append(
            _StatementRow(
                security=securities[1],
                period=BASE_PERIOD,
                announced_on=BASE_ANNOUNCEMENT,
                label="1",
                values=(THIRD_VERSION_VALUE, *(1.0 for _ in range(width - 2)), 3.0),
            )
        )
    if "financials.earlier_period_announced_later" in shapes:
        rows.append(
            _StatementRow(
                security=securities[-1],
                period=EARLIER_PERIOD,
                announced_on=LATE_ANNOUNCEMENT,
                label="1",
                values=(50.0, *(1.0 for _ in range(width - 1))),
            )
        )
    if "financials.announced_after_the_as_of" in shapes:
        # The same (security, period) key as that security's own base row, announced after the
        # read: a restatement a reader standing on AS_OF must not be able to see. Its own
        # `available_time` is what pushes the partition's `max_available_time` past AS_OF.
        rows.append(
            _StatementRow(
                security=securities[0],
                period=BASE_PERIOD,
                announced_on=FUTURE_ANNOUNCEMENT,
                label="1",
                values=(THIRD_VERSION_VALUE, *(1.0 for _ in range(width - 1))),
            )
        )
    return tuple(rows)


def _cashflow_rows(securities: Sequence[str], shapes: frozenset[str]) -> tuple[_StatementRow, ...]:
    """`cashflow`'s own filings, through its own projection.

    The same-day duplicate is mirrored here when it is asked for, because it is a property of
    every one of the four endpoints and not of `income`: 718 of `cashflow`'s 2,849 keys carry
    more than one row and 468 of them disagree. A fixture whose second statement dataset were
    always clean could not tell a loop over the datasets from a read of the first.
    """
    width = len(CASHFLOW_COLUMNS)
    rows = [
        _StatementRow(
            security=code,
            period=BASE_PERIOD,
            announced_on=BASE_ANNOUNCEMENT,
            label="1",
            values=(200.0 + index, *(1.0 for _ in range(width - 1))),
        )
        for index, code in enumerate(securities)
    ]
    if "financials.same_day_duplicate_versions" in shapes:
        rows.append(
            _StatementRow(
                security=securities[0],
                period=BASE_PERIOD,
                announced_on=BASE_ANNOUNCEMENT,
                label="0",
                values=(DISAGREEING_VALUE, *(1.0 for _ in range(width - 1))),
            )
        )
    return tuple(rows)


def _statement_batch(
    dataset: str, columns: Sequence[str], rows: Sequence[_StatementRow]
) -> ColumnarPanelBatch:
    """One statement endpoint's partition, through its own projection and its own clock.

    The two label columns are present for exactly the three datasets that carry them.
    `fina_indicator` has neither, which is not an omission here but the shape of the endpoint --
    `statement_panel_columns` and `write_financial_statements`' `revision_field` both branch on
    `DATASETS_WITH_REVISION_LABEL`, and a generator that always emitted the labels would leave
    the branch that reads `None` unreachable from any generated panel.

    The fetch instant is derived from the rows rather than fixed at `AS_OF`: a filing announced
    after the read is only storable in a batch that was fetched after it, which is what makes
    `financials.announced_after_the_as_of` a property of the data rather than of the request.
    """
    announced = [_midnight_shanghai(row.announced_on) for row in rows]
    labels: list[PanelColumn] = []
    if dataset in DATASETS_WITH_REVISION_LABEL:
        labels = [
            PanelColumn(
                FIRST_ANNOUNCEMENT_COLUMN,
                "string",
                tuple(row.announced_on.isoformat() for row in rows),
            ),
            PanelColumn(REVISION_LABEL_COLUMN, "string", tuple(row.label for row in rows)),
        ]
    return _batch(
        dataset,
        subjects=[row.security for row in rows],
        columns=[
            PanelColumn(
                REPORT_PERIOD_COLUMN, "string", tuple(row.period.isoformat() for row in rows)
            ),
            PanelColumn(
                ANNOUNCEMENT_DATE_COLUMN,
                "string",
                tuple(row.announced_on.isoformat() for row in rows),
            ),
            *labels,
            *(
                PanelColumn(name, "float", tuple(row.values[index] for row in rows))
                for index, name in enumerate(columns)
            ),
        ],
        event_time=announced,
        available_time=announced,
        fetched_at=max([AS_OF, *announced]),
    )


def _plain_statement_rows(
    securities: Sequence[str], base: float, width: int
) -> tuple[_StatementRow, ...]:
    """One filing per security and nothing else, for an endpoint no shape varies."""
    return tuple(
        _StatementRow(
            security=code,
            period=BASE_PERIOD,
            announced_on=BASE_ANNOUNCEMENT,
            label="1",
            values=(base + index, *(1.0 for _ in range(width - 1))),
        )
        for index, code in enumerate(securities)
    )


def _namechange_batch(records: Mapping[str, tuple[NameRecord, ...]]) -> ColumnarPanelBatch:
    """The rename corpus as `namechange` stores it: one panel row per `NameRecord`.

    No splitting, unlike `stock_basic` and `index_member_all`. A rename's effective date is
    published *in* its announcement, so one record states one fact that became knowable at one
    instant -- which is why `write_name_history`'s docstring can say this dataset "stay[s] one
    row per record while `stock_basic` splits", and why writing the encoding out here is a
    projection rather than a second statement of a contract. `name_histories_from_panel_rows`
    is the counterpart, and `test_panel_shape_coverage.py` round-trips these rows back through
    it to the carriers they were built from.

    Both clocks are the announcement, matching `providers/tushare.py`'s descriptor: the row is
    dated at `ann_date` because a request window here is an announcement window, and the
    effective date rides along as `effective_date`.
    """
    rows = sorted(
        (record for group in records.values() for record in group),
        key=lambda record: (record.announced_on, record.ts_code, record.name),
    )
    announced = [_midnight_shanghai(record.announced_on) for record in rows]
    return _batch(
        NAMECHANGE_DATASET,
        subjects=[record.ts_code for record in rows],
        columns=[
            PanelColumn(NAME_COLUMN, "string", tuple(record.name for record in rows)),
            PanelColumn(
                NAME_EFFECTIVE_COLUMN,
                "string",
                tuple(record.effective_from.isoformat() for record in rows),
            ),
            PanelColumn(
                NAME_ANNOUNCEMENT_COLUMN,
                "string",
                tuple(record.announced_on.isoformat() for record in rows),
            ),
            PanelColumn(
                NAME_REASON_COLUMN, "string", tuple(record.change_reason for record in rows)
            ),
        ],
        event_time=announced,
        available_time=announced,
    )


def _industry_membership_batch(
    assignments: Mapping[str, tuple[IndustryAssignment, ...]],
) -> ColumnarPanelBatch:
    """The assignments as `index_member_all` stores them: **two rows for a closed interval**.

    An opening row dated at `industry_from` with `industry_through` empty, and -- only when the
    interval has closed -- a closing row dated at `industry_through` carrying both ends. The
    split is not decoration: the two ends became knowable at different instants, and a single
    row carrying both would make a 2026-01-09 hand-over visible to a reader standing on
    2026-01-02.

    This is the one encoding in this module that a reader could reasonably call a second
    statement of `providers/tushare.py::_industry_membership_panel_rows`. It is written out
    anyway, because the alternative -- handing `IndustryAssignment` carriers back and storing
    nothing -- leaves `index_member_all` with no partition, and a dataset with no partition
    cannot be blocked, cannot be stale, and cannot carry a row that is not yet knowable. What
    makes it one statement rather than two is that `domain/industry_classification.py` already
    owns the *decoder* (`industry_histories_from_panel_rows` and its `_fold_split_rows`), so
    `test_panel_shape_coverage.py` asserts the round trip: these rows, driven through the real
    writer and read back by the real reader, reproduce the carriers they were built from.
    """
    rows: list[tuple[IndustryAssignment, date, date | None]] = []
    for group in assignments.values():
        for assignment in group:
            rows.append((assignment, assignment.effective_from, None))
            if assignment.effective_through is not None:
                rows.append(
                    (assignment, assignment.effective_through, assignment.effective_through)
                )
    rows.sort(key=lambda row: (row[1], row[0].ts_code))
    dated = [_midnight_shanghai(event) for _, event, _ in rows]
    # `_statement_batch`'s rule, for the same reason: an assignment that takes effect after the
    # read is only storable in a batch genuinely fetched after it, because `ColumnarPanelBatch`
    # refuses a row whose `available_time` is after its own `as_of`. The read's `as_of` does not
    # move; only the fetch does.
    return _batch(
        INDUSTRY_MEMBERSHIP_DATASET,
        subjects=[assignment.ts_code for assignment, _, _ in rows],
        columns=[
            PanelColumn(
                INDUSTRY_FROM_COLUMN,
                "string",
                tuple(assignment.effective_from.isoformat() for assignment, _, _ in rows),
            ),
            PanelColumn(
                INDUSTRY_THROUGH_COLUMN,
                "string",
                tuple(None if through is None else through.isoformat() for _, _, through in rows),
            ),
            PanelColumn(INDUSTRY_L1_COLUMN, "string", tuple(item.l1_code for item, _, _ in rows)),
            PanelColumn(INDUSTRY_L2_COLUMN, "string", tuple(item.l2_code for item, _, _ in rows)),
            PanelColumn(INDUSTRY_L3_COLUMN, "string", tuple(item.l3_code for item, _, _ in rows)),
        ],
        event_time=dated,
        available_time=dated,
        fetched_at=max([AS_OF, *dated]),
    )


REFORM_MARKED_NAME: Final[str] = "S*ST兰宝"
PENDING_RENAME: Final[str] = "平安银行"
RENAME_ANNOUNCED_ON: Final[date] = date(2026, 1, 5)
RENAME_EFFECTIVE_FROM: Final[date] = date(2026, 8, 2)

NEWEST_RENAME_SECURITY_INDEX: Final[int] = 6
NEWEST_RENAME: Final[str] = "招商蛇口"
"""`securities[6]`, and a name that carries **no** risk warning.

The index is the one no other rename names: `securities[0]` carries the pending rename and
`securities[1]` the reform-marked one. The *name* matters as much as the index. A rename
announced on the newest session moves `_bars_on`'s `is_st` for that name on that session, so an
ST-marked one would change which securities the newest cross section can trade -- and a shape
that quietly moved a screen's verdict would make every assertion about the screen an assertion
about the shape. `risk_warning_of('招商蛇口')` is `RiskWarning.none`, the same as the generated
`标的N` names it replaces, so this shape moves an availability instant and nothing else.
"""


def _name_records(
    securities: Sequence[str], shapes: frozenset[str]
) -> Mapping[str, tuple[NameRecord, ...]]:
    records: dict[str, list[NameRecord]] = {
        code: [
            NameRecord(
                ts_code=code,
                name=f"标的{index}",
                effective_from=LISTED_ON,
                announced_on=LISTED_ON,
                change_reason="其他",
            )
        ]
        for index, code in enumerate(securities)
    }
    if "name_history.announcement_precedes_effect" in shapes:
        records[securities[0]].append(
            NameRecord(
                ts_code=securities[0],
                name=PENDING_RENAME,
                effective_from=RENAME_EFFECTIVE_FROM,
                announced_on=RENAME_ANNOUNCED_ON,
                change_reason="其他",
            )
        )
    if "name_history.reform_prefixed_special_treatment" in shapes:
        records[securities[1]].append(
            NameRecord(
                ts_code=securities[1],
                name=REFORM_MARKED_NAME,
                effective_from=date(2026, 1, 6),
                announced_on=date(2026, 1, 6),
                change_reason="*ST",
            )
        )
    if "name_history.announcement_on_the_newest_session" in shapes:
        records[securities[NEWEST_RENAME_SECURITY_INDEX]].append(
            NameRecord(
                ts_code=securities[NEWEST_RENAME_SECURITY_INDEX],
                name=NEWEST_RENAME,
                effective_from=WINDOW_LAST,
                announced_on=WINDOW_LAST,
                change_reason="其他",
            )
        )
    return MappingProxyType({code: tuple(rows) for code, rows in records.items()})


HANDOVER_THROUGH: Final[date] = date(2026, 1, 9)
HANDOVER_FROM: Final[date] = date(2026, 1, 12)
HOLE_THROUGH: Final[date] = date(2026, 1, 6)
HOLE_FROM: Final[date] = date(2026, 1, 14)


def _assignment(
    code: str, vintage: int, *, since: date, through: date | None
) -> IndustryAssignment:
    return IndustryAssignment(
        ts_code=code,
        l1_code=INDUSTRY_L1[vintage],
        l2_code=INDUSTRY_L2[vintage],
        l3_code=INDUSTRY_L3[vintage],
        effective_from=since,
        effective_through=through,
    )


def _industry_assignments(
    securities: Sequence[str], shapes: frozenset[str]
) -> Mapping[str, tuple[IndustryAssignment, ...]]:
    assignments: dict[str, tuple[IndustryAssignment, ...]] = {
        code: (_assignment(code, 0, since=LISTED_ON, through=None),) for code in securities
    }
    if "industry.session_adjacent_handover" in shapes:
        code = securities[0]
        assignments[code] = (
            _assignment(code, 0, since=LISTED_ON, through=HANDOVER_THROUGH),
            _assignment(code, 1, since=HANDOVER_FROM, through=None),
        )
    if "industry.coverage_hole" in shapes:
        code = securities[1]
        assignments[code] = (
            _assignment(code, 0, since=LISTED_ON, through=HOLE_THROUGH),
            _assignment(code, 1, since=HOLE_FROM, through=None),
        )
    if "industry.reclassification_after_the_as_of" in shapes:
        # `securities[2]`, so all three industry shapes compose: the hand-over is on
        # `securities[0]` and the coverage hole on `securities[1]`.
        code = securities[2]
        assignments[code] = (
            _assignment(code, 0, since=LISTED_ON, through=RECLASSIFIED_THROUGH),
            _assignment(code, 1, since=RECLASSIFIED_FROM, through=None),
        )
    return MappingProxyType(assignments)


def generate_panel(*, shapes: Iterable[str] = ()) -> GeneratedPanel:
    """Build a run-time panel fixture carrying exactly the shapes asked for.

    Nothing is written to disk and no `.parquet` is produced; see `write_generated_panel` for
    the step that drives the result through the real writers into a temporary `PanelStore`.

    Refuses an unknown shape id by name rather than ignoring it, because a typo in a shape
    request is a test that silently asserts against the shapeless panel.
    """
    asked = _requested(shapes)
    days = _calendar_days(asked)
    open_days = tuple(day.calendar_date for day in days if day.is_trading)
    sessions = tuple(day for day in open_days if WINDOW_FIRST <= day <= WINDOW_LAST)
    securities = SECURITIES
    batches: dict[str, ColumnarPanelBatch] = {
        TRADING_CALENDAR_DATASET: _calendar_batch(days),
        STOCK_BASIC_DATASET: _universe_batch(securities, asked),
        ADJ_FACTOR_DATASET: _factor_batch(sessions=sessions, securities=securities, shapes=asked),
        DAILY_DATASET: _bar_batch(sessions=sessions, securities=securities, shapes=asked),
        DAILY_BASIC_DATASET: _valuation_batch(
            sessions=sessions, securities=securities, shapes=asked
        ),
        SUSPENSION_DATASET: _suspension_batch(
            sessions=sessions, securities=securities, shapes=asked
        ),
        PRICE_LIMIT_DATASET: _limit_batch(sessions=sessions, securities=securities, shapes=asked),
        INDEX_WEIGHT_DATASET: _index_weight_batch(
            sessions=sessions, securities=securities, shapes=asked
        ),
        INCOME_DATASET: _statement_batch(
            INCOME_DATASET, INCOME_COLUMNS, _income_rows(securities, asked)
        ),
    }
    names = _name_records(securities, asked)
    assignments = _industry_assignments(securities, asked)
    batches[NAMECHANGE_DATASET] = _namechange_batch(names)
    batches[INDUSTRY_MEMBERSHIP_DATASET] = _industry_membership_batch(assignments)
    if (
        "financials.second_statement_dataset" in asked
        or "financials.statement_dataset_without_a_revision_label" in asked
    ):
        batches[CASH_FLOW_DATASET] = _statement_batch(
            CASH_FLOW_DATASET, CASHFLOW_COLUMNS, _cashflow_rows(securities, asked)
        )
    if "financials.statement_dataset_without_a_revision_label" in asked:
        batches[BALANCE_SHEET_DATASET] = _statement_batch(
            BALANCE_SHEET_DATASET,
            BALANCE_SHEET_COLUMNS,
            _plain_statement_rows(securities, 300.0, len(BALANCE_SHEET_COLUMNS)),
        )
        batches[FINANCIAL_INDICATOR_DATASET] = _statement_batch(
            FINANCIAL_INDICATOR_DATASET,
            FINANCIAL_INDICATOR_COLUMNS,
            _plain_statement_rows(securities, 400.0, len(FINANCIAL_INDICATOR_COLUMNS)),
        )
    return GeneratedPanel(
        shapes=asked,
        calendar_days=days,
        sessions=sessions,
        securities=securities,
        batches=MappingProxyType(batches),
        name_records=names,
        industry_assignments=assignments,
    )


STORED_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    NAMECHANGE_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
    PRICE_LIMIT_DATASET,
    INDEX_WEIGHT_DATASET,
    INDUSTRY_MEMBERSHIP_DATASET,
    INCOME_DATASET,
)
"""The datasets every generated panel carries, in write order.

`index_classify` is the one declared dataset that is **not** here, and the reason is the frame
rather than the encoding. `write_industry_tree` files a vintage's whole tree under the
vintage's own effective year -- 2021 for SW2021 -- and `industry_trees_from_panel_rows` refuses
a stored `taxonomy_date` that disagrees with `INDUSTRY_TAXONOMY_EFFECTIVE_FROM`, so the tree
cannot be moved into this generator's single 2026 partition year and a panel carrying it would
have to hand every caller a per-dataset year map. That is an interface change to
`GeneratedPanel`, and it should arrive with the first shape that needs a second partition year
rather than ahead of it. No P2 injection does: `V2-P2-004` injects a *membership* change, which
`index_member_all` carries, and the tree only resolves a code to a name and a parent.

`namechange` and `index_member_all` **were** absent for a different reason, recorded here
because it was half wrong. The claim was that both encodings are produced by
`providers/tushare.py`'s row splitting rather than by a projection, so restating them here
would state one contract twice. `index_member_all` really does split -- see
`_industry_membership_batch` -- but `namechange` never has: `write_name_history`'s own
docstring says it "stay[s] one row per record while `stock_basic` splits", because a rename's
effective date is published in its own announcement. And for the one that does split, the
answer to "stated twice" is not to store nothing; it is that `domain/` already owns the
decoder, so the round trip is assertable. Storing nothing had a cost that abstention hides: a
dataset with no partition cannot be blocked, cannot be stale, and cannot hold a row that is not
yet knowable, so `V2-P2-003`/`004`'s whole subject matter was unreachable from the generator.
"""


def _write_statements(store: PanelStore, panel: GeneratedPanel, selected: frozenset[str]) -> None:
    for dataset in STATEMENT_DATASETS:
        if dataset in panel.batches and dataset in selected:
            write_financial_statements(store, [panel.batch(dataset)])


def write_generated_panel(
    store: PanelStore,
    panel: GeneratedPanel,
    *,
    halts: bool = True,
    datasets: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Drive `panel` through the real `panel_ingest` writers; return the datasets stored.

    Every write-time guard runs, which is the point: a shape that produced a panel the real
    writers refuse would be a shape no test could use, and this is where that shows up.

    `datasets=None` writes everything the panel carries. Naming a subset writes only those, in
    the same order, which is what a test asserting about a fixed dataset list needs when the
    generator later learns to carry more -- and what a test that only reads one dataset needs
    to stop paying for ten. It refuses a name the panel does not carry, and refuses a selection
    that asks for `daily` without `suspend_d` while the halt guard is on, because
    `_refuse_unexplained_thin_sessions` reads the halts back **out of the store**: the request
    would otherwise succeed and mean something different from what it says.

    `halts=False` passes `write_daily_panel(halts=None)`, which switches off
    `_refuse_unexplained_thin_sessions` -- the strongest guard that writer has, and the one
    `panel build --no-halts` waives. It exists here so a test can assert what that waiver
    leaves behind, which is **nothing**: `halts` feeds that guard alone, so both builds store
    identical partitions and identical catalog records, and no read-side face can tell them
    apart (`tests/integration/test_panel_interfaces.py::
    test_a_panel_built_with_the_halt_guard_waived_is_indistinguishable_from_one_that_was_not`).
    Deliberately keyword-only with the guard on by default: a waiver that is a default is an
    accident, which is `write_daily_panel`'s own argument for having no default at all.
    """
    available = tuple(name for name in _write_order(panel) if name in panel.batches)
    selected = _selected_datasets(available, datasets, halts=halts)
    calendar = panel.calendar()
    if TRADING_CALENDAR_DATASET in selected:
        write_trading_calendar(store, panel.batch(TRADING_CALENDAR_DATASET))
    if STOCK_BASIC_DATASET in selected:
        write_stock_universe(store, panel.batch(STOCK_BASIC_DATASET))
    if NAMECHANGE_DATASET in selected:
        write_name_history(store, panel.batch(NAMECHANGE_DATASET))
    if ADJ_FACTOR_DATASET in selected:
        write_adjustment_factors(store, [panel.batch(ADJ_FACTOR_DATASET)], calendar=calendar)
    if SUSPENSION_DATASET in selected:
        write_suspensions(store, [panel.batch(SUSPENSION_DATASET)])
    if DAILY_DATASET in selected:
        write_daily_panel(
            store,
            bars=[panel.batch(DAILY_DATASET)],
            fundamentals=[panel.batch(DAILY_BASIC_DATASET)],
            calendar=calendar,
            halts=(
                load_suspensions(store, years=(panel.year,), as_of=panel.as_of, max_staleness=None)
                if halts
                else None
            ),
        )
    if PRICE_LIMIT_DATASET in selected:
        write_price_limits(store, [panel.batch(PRICE_LIMIT_DATASET)], calendar=calendar)
    if INDEX_WEIGHT_DATASET in selected:
        write_index_weights(store, [panel.batch(INDEX_WEIGHT_DATASET)])
    if INDUSTRY_MEMBERSHIP_DATASET in selected:
        write_industry_memberships(store, [panel.batch(INDUSTRY_MEMBERSHIP_DATASET)])
    _write_statements(store, panel, selected)
    return tuple(name for name in available if name in selected)


def _write_order(panel: GeneratedPanel) -> tuple[str, ...]:
    """`STORED_DATASETS` followed by whichever optional statement endpoints the panel carries."""
    optional = tuple(
        dataset
        for dataset in STATEMENT_DATASETS
        if dataset not in STORED_DATASETS and dataset in panel.batches
    )
    return (*STORED_DATASETS, *optional)


def _selected_datasets(
    available: Sequence[str], datasets: Iterable[str] | None, *, halts: bool
) -> frozenset[str]:
    if datasets is None:
        return frozenset(available)
    asked = frozenset(datasets)
    unknown = sorted(asked - set(available))
    if unknown:
        raise PanelFixtureError(
            f"this panel carries no {unknown} batch to write; it carries {sorted(available)}"
        )
    if DAILY_DATASET in asked and DAILY_BASIC_DATASET not in asked:
        raise PanelFixtureError(
            f"{DAILY_DATASET!r} and {DAILY_BASIC_DATASET!r} are written by one call to "
            "write_daily_panel and cannot be selected apart"
        )
    if DAILY_BASIC_DATASET in asked and DAILY_DATASET not in asked:
        raise PanelFixtureError(
            f"{DAILY_BASIC_DATASET!r} is written by write_daily_panel alongside "
            f"{DAILY_DATASET!r} and cannot be selected without it"
        )
    if halts and DAILY_DATASET in asked and SUSPENSION_DATASET not in asked:
        raise PanelFixtureError(
            f"writing {DAILY_DATASET!r} with the halt guard on reads {SUSPENSION_DATASET!r} "
            "back out of the store, so a selection that omits it would silently judge every "
            "session as having no halts; add it, or pass halts=False"
        )
    return asked
