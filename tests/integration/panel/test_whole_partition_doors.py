"""The doors still judged on a whole partition, each driven from the path that reaches it.

`V2-P4-076` moved `stock_basic`, `namechange` and `suspend_d` off `read_if_ready` and left
`trade_cal` alone with a measurement. The fifth P4 acceptance then swept what remained, and this
file is the acceptance that row asked for: **for each door, drive it from a shipped path and find
out whether the whole-partition wall is reachable there.**

## What the sweep measured, before this issue

`generate_panel(shapes=HEALTHY_SHAPES)` written through the real writers, read at five instants::

    load_adjustment_histories         NYK NYK NYK OK  OK      <- V2-P4-079
    load_statement_histories[income]  NYK NYK OK  OK  OK      <- V2-P4-083
    load_index_membership[000300.SH]  NYK NYK NYK OK  OK      <- V2-P4-083
    load_industry_trees               (index_classify)        <- V2-P4-083

Every `NYK` is one shape: a whole-year partition whose newest row post-dates the read, refused
entire for the sake of that row. **One of the four moved. Three did not, and each is closed by a
different measurement rather than by inattention** -- which is the outcome, not a shortfall
against it: `V2-P4-076`'s discipline is to measure the clock, measure the corpus, and pick the
door that fits, and three of these corpora do not fit the door that suits the fourth.

## `V2-P4-083` -- the statement corpus, which moved

`load_statement_histories` is read by `panel_doctor._ambiguity_check` and by nothing else in
`src/` (`panel_factors` reads the four statement datasets through `read_visible_at` already).
Standing at 2026-01-09T20:00+08 it was `SKIPPED` with `income cannot be read ...
['not_yet_knowable']`, for a filing announced on 2026-01-12 that nobody had asked about.

`ClockStrategy.announcement` sets `event_time == available_time ==` midnight of the row's own
`ann_date`, so the per-event-date census reconciles exactly and the bound is
`_knowable_through_the_same_day`. The partition is stored uncompressed and this reader already
carries an explicit `answerable_through`, so a short read answers narrowly rather than wrongly.

## `V2-P4-079` -- the adjustment corpus: the wall is real and the door still does not fit

`adj_factor` has the walling shape and `V2-P4-076` deliberately left it, because the shortlist
face does not read it -- `test_shortlist_whole_year_reads.py::
test_the_shortlist_face_reads_no_adjustment_factor` holds the import graph to that. It is
`factor_view._PanelInputs`' read and `panel_doctor`'s, and this issue drove the second.

**The wall is reachable.** At 2026-01-09T20:00+08, asked about 2026-01-06 -- a session that had
published three days earlier -- five of the eight cross-checks ran and two died on this door::

    ran      close_agreement
    SKIPPED  unpriced_explained   the adjustment factor series cannot be read at
                                  2026-01-09T12:00:00+00:00: ['not_yet_knowable']
    SKIPPED  return_paths         the adjustment factor series cannot be read at
                                  2026-01-09T12:00:00+00:00: ['not_yet_knowable']

`close_agreement` ran because `V2-P4-061` put `load_daily_bars` on the session read. The two that
died asked the same store about the same session and were refused because a **different** dataset's
partition holds rows from a week later.

**And the door that fixed the other three would make it worse.** `compress_adjustment_batch`
stores a step function, not one row per event: the year's opening anchor, every change point, the
year's closing anchor. Measured below -- 64 rows written, 18 stored, six of eight securities
keeping exactly two -- a row predicate at the earlier instant leaves **one** row for six of eight
and drops `covered_through` from 2026-01-16 to 2026-01-05 for all eight. Every question the move
was made to answer becomes `AdjustmentHorizonError`.

The census cannot repair that, and it is the reason the door is not merely awkward but wrong:
`PartitionCoverage.dates` carries `event_date` and `row_count` and no subject, while "did this
series end or was its tail withheld" is a per-security question the live corpus really does answer
both ways (`KNOWN_ADJUSTMENT_LIMITATIONS.suspension_is_invisible`). A move needs an answerable
horizon on `AdjustmentHistory` (`domain/adjustment.py`) and a per-subject census
(`panel/catalog.py`), neither of which is `panel_ingest`'s.

## The two with no caller to answer to

**`load_industry_trees`.** `index_classify` is `ClockStrategy.calendar_static` on `taxonomy_date`,
and `industry_trees_from_panel_rows` refuses a stored `taxonomy_date` that disagrees with
`INDUSTRY_TAXONOMY_EFFECTIVE_FROM`. So every row of a vintage's partition carries the **same**
date, the partition's newest availability instant is its only one, and `not_yet_knowable` can only
mean "you asked before this taxonomy existed" -- never "the panel has moved on". That is
`trade_cal`'s argument arriving by a different route: there is no refusal here to remove.

**`load_index_membership`.** No caller in `src/` at all, pinned below.

## The gated reader that had no caller anywhere, and got one

`load_index_prices` was in `GATED_READERS` and called by nothing in `src/` or `tests/` -- a guard
entry that could never fail. It is `panel_doctor._rebuild_check`'s sixth dataset now, for the
reason `panel_ingest._refuse_unrebuildable_index_prices` had already written down and could only
act on at write time.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

import pytest
from panel_fixtures import (
    AS_OF,
    EXCHANGE,
    INDEX_CODE,
    PANEL_SHAPES,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from typer.testing import CliRunner

from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.adjustment import (
    ADJ_FACTOR_DATASET,
    ADJUSTMENT_PANEL_COLUMNS,
    AdjustmentHorizonError,
    adjustment_histories_from_panel_rows,
)
from openalpha_cn.domain.index_prices import (
    INDEX_DAILY_DATA_COLUMNS,
    INDEX_DAILY_DATASET,
    MARKET_INDEX_CODE,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import CrossCheckOutcome, panel_health_report
from openalpha_cn.panel_ingest import write_panel_batch

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SOURCE: Final[Path] = ROOT / "src" / "openalpha_cn"

HEALTHY_SHAPES: Final[tuple[str, ...]] = tuple(
    shape_id for shape_id, shape in PANEL_SHAPES.items() if not shape.provokes
)
"""Every shape that is a form of sound data.

The defect shapes are deliberately out: `financials.announced_after_the_as_of` and
`index.publication_after_the_as_of` each inject a row from *after* `AS_OF`, so a panel carrying
them is `not_yet_knowable` at every instant this file reads at and the measurement could not say
whether the refusal was the wall or the injection.
"""

EARLIER_AS_OF: Final[datetime] = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
"""20:00 Asia/Shanghai on 2026-01-09, four days before the panel's newest session.

Past 16:30 on its own day, so `_sessions_published_through` places the census bound on 2026-01-09
itself and the three sessions at or before it are all due. The panel goes on to 2026-01-16, which
is the whole point: the store has moved on and the read has not.
"""

PUBLISHED_SESSION: Final[date] = date(2026, 1, 6)
"""A session that had published three days before `EARLIER_AS_OF`.

Named rather than derived so the assertion is about the door and not about the session: nothing
`daily`'s own session read could object to, so a refusal can only have come from a partition
somewhere else in the store.
"""


@pytest.fixture(scope="module")
def panel() -> GeneratedPanel:
    return generate_panel(shapes=HEALTHY_SHAPES)


@pytest.fixture(scope="module")
def stored(
    panel: GeneratedPanel, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[PanelStore, tuple[str, ...]]]:
    store = PanelStore(tmp_path_factory.mktemp("doors") / "panel")
    yield store, write_generated_panel(store, panel)


def _report_checks(
    store: PanelStore,
    panel: GeneratedPanel,
    datasets: tuple[str, ...],
    *,
    as_of: datetime,
    session: date,
) -> dict[str, CrossCheckOutcome]:
    report = panel_health_report(
        store,
        as_of=as_of,
        datasets=datasets,
        years=(YEAR,),
        calendar=panel.calendar(),
        index_codes=(INDEX_CODE,),
        cross_section_days=(session,),
    )
    return {check.name: check for check in report.cross_checks}


# --- V2-P4-079: the adjustment door, driven from `panel doctor` ------------------------------


def test_the_adjustment_wall_costs_the_doctor_two_checks_about_a_session_that_had_published(
    panel: GeneratedPanel, stored: tuple[PanelStore, tuple[str, ...]]
) -> None:
    """The first half of `V2-P4-079`: the wall is reachable, and this is where it bites.

    `unpriced_explained` and `return_paths` are the two cross-checks whose first read is
    `load_adjustment_histories`. Standing at `EARLIER_AS_OF` and asking about a session that had
    published three days earlier, both are `SKIPPED` on this partition's own `not_yet_knowable`
    -- the store having advanced to 2026-01-16 is the entire reason, and it is a fact about a
    week the report was not asked about.

    `close_agreement` is the control, and it is what makes the finding a wall rather than a
    limitation of the report: it reads the same store, at the same instant, about the same
    session, and it runs, because `V2-P4-061` put `load_daily_bars` on the session door. The
    report can talk about that session's closes and not about whether its unpriced names were
    halted, and nothing about the session accounts for the difference.

    The last assertion is the discriminating one: the refusal must **not** name
    `PUBLISHED_SESSION`. `close_agreement`'s own refusal, at an `as_of` before a session
    published, does name the session (`daily cannot be read for 2026-01-16 ... that session had
    not published yet`), so a message-blind test could not tell the two kinds of refusal apart --
    and telling them apart is the whole claim. This one is the partition's year-wide verdict and
    is about nothing that happened on 2026-01-06.
    """
    store, datasets = stored

    checks = _report_checks(store, panel, datasets, as_of=EARLIER_AS_OF, session=PUBLISHED_SESSION)

    assert checks["close_agreement"].ran is True
    assert checks["unpriced_explained"].ran is False
    assert checks["return_paths"].ran is False
    for name in ("unpriced_explained", "return_paths"):
        reason = str(checks[name].skipped_reason)
        assert "the adjustment factor series cannot be read" in reason
        assert "not_yet_knowable" in reason
        assert PUBLISHED_SESSION.isoformat() not in reason


def test_the_compressed_factor_partition_is_why_a_row_predicate_cannot_replace_that_wall(
    panel: GeneratedPanel, stored: tuple[PanelStore, tuple[str, ...]]
) -> None:
    """The second half of `V2-P4-079`: the door `V2-P4-076` used does not fit this corpus.

    Every dataset on `_read_visible_event_dated_rows` stores one row per event. `adj_factor`
    stores a **step function**: `compress_adjustment_batch` keeps the year's opening anchor,
    every change point and the year's closing anchor, and drops the rest. So "no row on day D"
    means "the factor did not move on D", not "nothing happened" -- and a predicate that removes
    the closing anchor removes the only row carrying `covered_through`.

    That is measured here rather than asserted about the loader, because the loader does not
    take that door: the numbers are read straight off the stored partition, and they are what a
    filtered read would have to work from. Six of the eight securities keep exactly two rows, so
    at `EARLIER_AS_OF` a row predicate leaves **one**, and the horizon of a whole year of factors
    collapses onto its first session.

    The last assertion is the one that makes it unrepairable rather than merely awkward.
    `PartitionCoverage.dates` is the instrument the five callers on that door reconcile against,
    and its entries carry an event date and a row count and **no subject**. Whether a security's
    tail was withheld or its series genuinely ended is a per-security question --
    `KNOWN_ADJUSTMENT_LIMITATIONS.suspension_is_invisible` is the live case, `000024.SZ`'s last
    bar on 2015-12-07 against a factor series running to 2015-12-29 -- and a per-partition census
    cannot answer it.
    """
    store, _datasets = stored
    written = len(panel.batch(ADJ_FACTOR_DATASET).subjects)

    rows = store.query(dataset=ADJ_FACTOR_DATASET, year=YEAR, columns=ADJUSTMENT_PANEL_COLUMNS)
    dates_by_subject: dict[str, list[date]] = {}
    for subject, day, _factor in rows:
        dates_by_subject.setdefault(str(subject), []).append(date.fromisoformat(str(day)))
    visible_at_the_earlier_instant = {
        code: [day for day in days if day <= EARLIER_AS_OF.date()]
        for code, days in dates_by_subject.items()
    }
    coverage = store.read_coverage(ADJ_FACTOR_DATASET, YEAR)
    assert coverage is not None

    assert written == 64
    assert sum(len(days) for days in dates_by_subject.values()) == 18
    assert sorted(len(days) for days in dates_by_subject.values()) == [2, 2, 2, 2, 2, 2, 3, 3]
    assert {len(days) for days in visible_at_the_earlier_instant.values()} == {1}
    assert set(type(coverage.dates[0]).__dataclass_fields__) == {"event_date", "row_count"}


def test_a_horizon_the_read_declares_cannot_carry_the_per_security_half(
    tmp_path: Path,
) -> None:
    """`V2-P4-086`: edit (a) is delivered and edit (b) is measured here to be load-bearing.

    That row names two edits. The first landed --
    `adjustment_histories_from_panel_rows(rows, *, answerable_through=...)`, following
    `statement_histories_from_panel_rows`' shape, so a read can say how far it looked instead of
    letting its newest row say it. This test is the second edit's acceptance, written from the
    side that shows why one date cannot stand in for it.

    **The move was attempted and reverted on this measurement.** Putting
    `load_adjustment_histories` on `_read_visible_event_dated_rows` with a read-level
    `answerable_through` does make `panel doctor` answer at `EARLIER_AS_OF` -- the census
    reconciliation fits `adj_factor` exactly, because its clock is `ClockStrategy.daily_close` and
    a row's availability is a function of its own event date, so that half of `V2-P4-079`'s
    objection turned out not to bind. What did bind is this one:
    `tests/integration/panel/test_panel_shape_coverage.py::
    test_a_shape_provokes_exactly_the_health_codes_it_declares[adjustment.factor_series_stops
    _inside_the_window]` went from `['return_path_disagreement']` to `[]`. A security whose factor
    series genuinely ends inside the window stopped being refused and started being answered, from
    the shipped health report -- the exact fail-open `AdjustmentHistory`'s upper horizon exists to
    prevent, and the exact per-security distinction `KNOWN_ADJUSTMENT_LIMITATIONS.
    suspension_is_invisible` names.

    Its own store rather than the module fixture, and the difference is the point: `HEALTHY_SHAPES`
    excludes every shape that provokes a finding, so the panel the tests above read carries no
    stopped series at all and could not separate the two answers. Below is the defect shape in one
    partition and no report: the corpus holds one security whose series stops
    before the others', a single read-level bound lifts *its* horizon along with everybody else's,
    and nothing in the rows or in `PartitionCoverage` can tell the two apart. `covered_through`
    and `observed_through` are asserted together because their coming apart is the whole finding:
    the read is answering past its last measurement for a security it has no business answering
    for.

    **A frontier rule does not rescue it, and that was checked rather than assumed.** "Widen only
    the securities whose last visible row sits at the read's newest visible event date" separates
    the two here, and it fails on the ordinary case instead: on a step function a security that
    simply did not move since the opening anchor also sits behind the frontier, so it would be
    refused for being quiet. What separates "quiet" from "finished" is a per-subject
    `last_event_date`, which is `V2-P4-086`'s second edit in the shape `V2-P4-094` corrected it to
    -- cardinality `PartitionCoverage.subjects`, not one entry per stored row.
    """
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(
        store, generate_panel(shapes=("adjustment.factor_series_stops_inside_the_window",))
    )

    rows = store.query(dataset=ADJ_FACTOR_DATASET, year=YEAR, columns=ADJUSTMENT_PANEL_COLUMNS)
    unbounded = adjustment_histories_from_panel_rows(rows)

    horizons = {code: history.covered_through for code, history in unbounded.items()}
    newest = max(horizons.values())
    stopped = sorted(code for code, day in horizons.items() if day < newest)
    assert stopped, (
        "the fixture no longer carries a series that stops inside the window, so this test "
        "cannot separate the two answers it exists to separate"
    )

    bounded = adjustment_histories_from_panel_rows(rows, answerable_through=newest)
    for code in stopped:
        assert bounded[code].observed_through == horizons[code]
        assert bounded[code].covered_through == newest
        assert bounded[code].factor_on(newest) == unbounded[code].factor_on(horizons[code]), (
            "a read-level horizon answers this security's last factor across a window its own "
            "series never covered, which is what edit (b) has to stop"
        )
        with pytest.raises(AdjustmentHorizonError):
            unbounded[code].factor_on(newest)


# --- V2-P4-083: the statement door, driven from `panel doctor` -------------------------------


def test_the_doctor_can_count_ambiguous_filings_at_an_instant_inside_the_year(
    panel: GeneratedPanel, stored: tuple[PanelStore, tuple[str, ...]]
) -> None:
    """`_ambiguity_check` is the only `src/` reader of `load_statement_histories`.

    `income`'s newest stored announcement is 2026-01-12, so at `EARLIER_AS_OF` the partition holds
    a row three days out and the whole year was refused for it -- including the filings announced
    on 2026-01-05, which every reader standing on 2026-01-09 was entitled to.
    """
    store, datasets = stored

    checks = _report_checks(store, panel, datasets, as_of=EARLIER_AS_OF, session=PUBLISHED_SESSION)

    assert checks["statement_ambiguity"].skipped_reason is None
    assert checks["statement_ambiguity"].ran is True


# --- V2-P4-083: the two doors that are closed by a measurement --------------------------------


def _module_functions(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_no_shipped_path_calls_the_index_membership_door(
    stored: tuple[PanelStore, tuple[str, ...]],
) -> None:
    """The measurement `load_index_membership` is closed by, rather than moved on.

    A door nothing in `src/` opens cannot wall anything off, so the `not_yet_knowable` the sweep
    measured is a property of the reader in isolation. This is the assertion that turns "we
    looked and found no caller" into a fact that stays checked: the day something reaches for it,
    this fails and the door has to be judged on that caller's clock rather than on this sentence.
    """
    callers = sorted(
        str(path.relative_to(SOURCE))
        for path in sorted(SOURCE.rglob("*.py"))
        if "load_index_membership" in _called_names(path)
    )

    assert callers == []


# --- V2-P4-083: the gated reader that had no caller at all ------------------------------------


def _published_time(day: date) -> datetime:
    """16:30 Asia/Shanghai, `panel_fixtures`' own instant for a session becoming knowable."""
    return datetime(day.year, day.month, day.day, 8, 30, tzinfo=UTC)


def _duplicated_index_session(sessions: tuple[date, ...]) -> ColumnarPanelBatch:
    """One level series over `sessions`, with the newest session stored twice.

    The one shape `index_bars_from_panel_rows` singles out -- "a duplicated session would put two
    returns where the market had one and change the sample size of every regression that reads
    the series" -- and the one `write_index_prices` would have caught, which is why this is
    written through the generic writer instead.

    Only `MARKET_INDEX_CODE`, because `index_price_requirement` names exactly that subject and a
    partition holding the other two without it is a *different* defect (`subject_missing`) that
    readiness already reports.
    """
    days = [*sessions, sessions[-1]]
    levels = tuple(1000.0 + index for index, _ in enumerate(days))
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=INDEX_DAILY_DATASET,
        kind=INDEX_DAILY_DATASET,
        as_of=AS_OF,
        fetched_at=AS_OF,
        status="success",
        subjects=tuple(MARKET_INDEX_CODE for _ in days),
        timeline=TimelineColumns(
            event_time=tuple(_published_time(day) for day in days),
            available_time=tuple(_published_time(day) for day in days),
            ingested_time=tuple(_published_time(day) for day in days),
            revision_time=tuple(_published_time(day) for day in days),
        ),
        columns=(
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("open", "float", levels),
            PanelColumn("high", "float", levels),
            PanelColumn("low", "float", levels),
            PanelColumn("close", "float", levels),
            PanelColumn("pre_close", "float", levels),
            PanelColumn("pct_chg", "float", tuple(0.0 for _ in days)),
            PanelColumn("vol", "float", tuple(1.0 for _ in days)),
            PanelColumn("amount", "float", tuple(1.0 for _ in days)),
        ),
    )


assert (
    tuple(column.name for column in _duplicated_index_session((date(2026, 1, 5),)).columns)
    == INDEX_DAILY_DATA_COLUMNS
), "the generic writer takes the projection unchecked, so the projection is checked here"


def test_the_index_level_reader_is_reached_by_the_check_that_needs_it(
    tmp_path: Path, panel: GeneratedPanel
) -> None:
    """`load_index_prices` had no caller in `src/` **or** in `tests/`, and a `GATED_READERS` row.

    A gated reader nothing calls is a guard entry that can never fail: the allowlist counts it,
    the audit passes, and the line proves nothing about the tree. `V2-P4-083` gave it the caller
    the argument for it already named. `_refuse_unrebuildable_index_prices` says why a partition
    this reader refuses matters more than the ones beside it -- `panel_factors` reads `close` and
    `pre_close` straight out of the partition and never rebuilds, so a duplicated session puts
    two market returns where the market had one and changes the sample size of every regression
    over that window -- and that guard runs at **write** time only, so a partition written before
    it existed is never re-examined. That is `_rebuild_check`'s whole subject.

    The partition here goes in through `write_panel_batch`, bypassing `write_index_prices`'
    guard, so it reaches disk exactly as one written before the guard did. Readiness clears it:
    the sessions are all present, the subject is named, the row count matches. The reader is what
    refuses.
    """
    store = PanelStore(tmp_path / "panel")
    write_panel_batch(store, _duplicated_index_session(panel.sessions), year=YEAR)

    report = panel_health_report(
        store,
        as_of=AS_OF,
        datasets=(INDEX_DAILY_DATASET,),
        years=(YEAR,),
        calendar=panel.calendar(),
        cross_section_days=(panel.sessions[-1],),
    )

    assert report.dataset(INDEX_DAILY_DATASET).readiness.state == "ready"
    (finding,) = report.findings_with_code("domain_rebuild_refused")
    assert finding.datasets == (INDEX_DAILY_DATASET,)
    assert finding.severity == "warning"
    assert "IndexPriceError" in finding.detail
    assert "more than one stored level" in finding.detail


def test_the_report_survives_being_asked_about_index_levels_with_no_calendar(
    tmp_path: Path,
) -> None:
    """`panel doctor --dataset index_daily --no-calendar` raised a bare `KeyError`.

    Found while giving `load_index_prices` its caller, and it is the same missing row.
    `_requirement_for` dispatches `index_daily` to `index_price_requirement` -- a calendar-scoped
    builder -- and falls back to `_PRICE_SHAPED_FIELDS` when no calendar can be used, and that
    table had rows for the other three price-shaped datasets and not for this one. So the
    fallback that exists precisely so the rest of a verdict survives was the thing that raised.

    `panel_health_report`'s own docstring is what this breaks: *"Raises `PanelDoctorError` for a
    dataset with no declared cadence. Everything else that can go wrong ... becomes a finding,
    because a health report that raises on an unhealthy panel is a health report that is never
    there when it is needed."* `index_daily` has a declared cadence, and `--no-calendar` is a
    shipped flag.

    The second arm is the one a `--no-calendar` reading cannot reach: a calendar that does not
    cover the requested year takes the same fallback through `TradingCalendarError`, so a caller
    who supplied a calendar was equally exposed.
    """
    store = PanelStore(tmp_path / "panel")

    without = panel_health_report(
        store, as_of=AS_OF, datasets=(INDEX_DAILY_DATASET,), years=(YEAR,), calendar=None
    )
    unreachable_year = panel_health_report(
        store,
        as_of=AS_OF,
        datasets=(INDEX_DAILY_DATASET,),
        years=(YEAR,),
        calendar=build_trading_calendar(
            EXCHANGE, (CalendarDay(calendar_date=date(2011, 1, 4), is_trading=True),)
        ),
    )

    for report, expected in (
        (without, "no trading calendar was supplied"),
        (unreachable_year, "required_dates could not be derived from the calendar"),
    ):
        (note,) = report.findings_with_code("check_unavailable")
        assert expected in note.detail
        assert report.dataset(INDEX_DAILY_DATASET).readiness.state == "blocked"


def test_the_shipped_command_survives_being_asked_about_index_levels_with_no_calendar(
    tmp_path: Path,
) -> None:
    """`V2-P4-087` as the caller typed it, which the test above does not drive.

    The measurement in that row is a **command line** -- `openalpha panel doctor --dataset
    index_daily --no-calendar` -- and the assertion beside it calls `panel_health_report`
    directly. Those are not the same claim: everything between the two, `_panel_request`'s
    dispatch and `_panel_command`'s envelope included, is unasserted by the library call, and this
    repository's most-repeated measured root cause is a green unit test with no green product path
    under it. So the literal command is driven here.

    `--no-calendar` and not `--calendar` on an empty store, because the two reach the fallback by
    different routes and only one of them is a shipped flag: with no `trade_cal` partition the
    request resolution would refuse before `_requirement_for` ran at all, which measures the wrong
    thing.

    The exit code is `1` and not `0`: an empty store is an unhealthy store, and that is the point
    -- the whole reason the fallback exists is to keep producing a verdict about a panel that is
    in trouble. What must not happen is exit `5` with a traceback, which is what
    `create_app`'s docstring rules out for this repository: "naming the specific variable, never a
    bare traceback".
    """
    PanelStore(tmp_path / "panel")

    result = CliRunner().invoke(
        app,
        [
            "panel",
            "doctor",
            "--dataset",
            INDEX_DAILY_DATASET,
            "--year",
            str(YEAR),
            "--runtime-dir",
            str(tmp_path),
            "--no-calendar",
            "--json",
        ],
    )

    assert result.exit_code == int(PanelExit.unhealthy), result.output
    assert "KeyError" not in result.output
    assert "Traceback" not in result.output
    assert "no trading calendar was supplied" in result.output
