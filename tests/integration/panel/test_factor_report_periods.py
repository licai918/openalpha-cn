"""The report-period axis, against real `income` partitions on disk.

`V2-P3-009`..`011` are all built on filings, and a filing does not live on the session axis.
This file is the measurement behind that sentence and behind every field the contract gained for
it, taken against partitions written through the real writers and read through the real
point-in-time reader rather than against in-memory objects.

## What the session axis could not do, measured as an A/B on one engine

The fixture below is one corpus of five `(period, announcement)` filings. It is written **twice**,
under two dataset names that differ in nothing but which side of
`domain/factor.py::PERIOD_INDEXED_DATASETS` they fall on:

- as `probe_announcements`, an ordinary dataset, so the engine indexes it by the session its
  `event_time` resolves to. `000001.SZ` disclosed its 2024 annual and its 2025 Q1 on one day --
  which is what an A-share issuer routinely does -- so two rows share one `(subject, session)`
  key and `compute_factor` **raises for the whole build**.
- as `income`, so the engine indexes it by `report_period`. The same rows compute.

That is the defect the second axis exists to correct, and it is asserted as one pair of calls
rather than as an argument about a version of the engine nobody can run any more.

## What did **not** change, which is the half that matters more

The multiplicity refusal is still fail-closed, and on the case that carries 81.7% of the real
duplication: two rows of one `(ts_code, end_date, ann_date)` key, which `fina_indicator` has no
column to order (no `update_flag`, no `f_ann_date`) and which
`providers/tushare.py::_announcement_timeline` deliberately gives byte-equal four-clock
timelines. Those collided on `(subject, event_time)` before and collide on `(subject, period)`
now, and `test_two_versions_of_one_filing_announced_on_one_day_still_refuse_the_whole_build`
holds it.

What stopped raising is the case that was never a duplicate: two *different* periods disclosed on
one day. And what is newly resolved rather than newly refused is the ordinary point-in-time
restatement -- one period announced twice on different days, where the later announcement is the
one a reader standing at `as_of` would have. That is
`financial_statements.StatementHistory.filing_for`'s rule, and
`test_the_engines_period_selection_is_the_domains_filing_for` runs both over this corpus rather
than asserting that two docstrings agree.

## The frame

Five periods across two announcement years, three securities chosen so the two period fields
separate from each other and from their neighbours:

| security | periods filed | what it is for |
|---|---|---|
| `000001.SZ` | all five, with a restatement and a two-period day | the computable case |
| `000002.SZ` | four, skipping 2024-12-31 | `max_window_periods`, the field that tells it apart |
| `600000.SH` | two | `lookback_periods`, the count shortfall |

`000002.SZ` is the fixture the coverage lesson asks for: with `max_window_periods == 4` it is
`insufficient_history` and with `5` it is `computed`, on the same rows, the same reach and the
same `as_of` -- so the field is separable from `lookback_periods` rather than merely rendered.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.factor import FactorDefinition, FactorField
from openalpha_cn.domain.financial_statements import (
    INCOME_DATA_COLUMNS,
    INCOME_DATASET,
    REPORT_PERIOD_COLUMN,
    statement_histories_from_panel_rows,
    statement_panel_columns,
)
from openalpha_cn.domain.panel_batch import (
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FactorEngineError,
    FactorPanel,
    FactorWindow,
    _report_period,
    compute_factor,
    load_factor_manifests,
    load_factor_observations,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import financial_statement_requirement, write_panel_batch

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

AS_OF: Final[datetime] = datetime(2025, 5, 20, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai, a month after the corpus's last disclosure day."""

BUILT_AT: Final[datetime] = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"

STALENESS: Final[timedelta] = timedelta(days=120)
"""The freshness bound every requirement here states, because the engine refuses a waiver.

120 days rather than a number chosen to make the fixture pass: A-share issuers disclose in four
bursts a year, so the widest gap between an `as_of` and the newest filing visible at it is about
a quarter, and this corpus's own widest is 30 days (2025-04-20 to `AS_OF`). A bound that only
just cleared would make every assertion here depend on one fixture's arithmetic.
"""

SESSION_DATASET: Final[str] = "probe_announcements"
"""The same rows under a name that is **not** in `PERIOD_INDEXED_DATASETS`.

The control half of the A/B this module's docstring describes. It is a probe dataset rather than
a real one because there is no real dataset that carries filings on the session axis -- that is
the whole point -- and naming a real one would make the test about that dataset.
"""

PANEL_PERIODS: Final[tuple[date, ...]] = (
    date(2024, 3, 31),
    date(2024, 6, 30),
    date(2024, 9, 30),
    date(2024, 12, 31),
    date(2025, 3, 31),
)
"""Every report period this corpus holds, which is the engine's own period calendar for it."""

DISCLOSURE_DAY: Final[date] = date(2025, 4, 20)
"""The day `000001.SZ` disclosed **two** periods: its 2024 annual and its 2025 Q1.

The ordinary A-share arrangement, and the shape a session index cannot hold.
"""

RESTATED_PERIOD: Final[date] = date(2024, 6, 30)
FIRST_ANNOUNCEMENT: Final[date] = date(2024, 8, 25)
LATER_ANNOUNCEMENT: Final[date] = date(2024, 11, 10)
"""One period announced twice on different days -- an ordinary point-in-time restatement.

Rare and real: 3 of `income`'s 3,198 periods over the 53-security probe
`domain/financial_statements.py` records. The later announcement carries `RESTATED_REVENUE`, and
a reader standing at `AS_OF` has seen both.
"""

FIRST_REVENUE: Final[float] = 110.0
RESTATED_REVENUE: Final[float] = 111.0

_Filing = tuple[str, date, date, float]

CORPUS: Final[tuple[_Filing, ...]] = (
    ("000001.SZ", date(2024, 3, 31), date(2024, 4, 20), 100.0),
    ("000001.SZ", RESTATED_PERIOD, FIRST_ANNOUNCEMENT, FIRST_REVENUE),
    ("000001.SZ", RESTATED_PERIOD, LATER_ANNOUNCEMENT, RESTATED_REVENUE),
    ("000001.SZ", date(2024, 9, 30), date(2024, 10, 25), 120.0),
    ("000001.SZ", date(2024, 12, 31), DISCLOSURE_DAY, 130.0),
    ("000001.SZ", date(2025, 3, 31), DISCLOSURE_DAY, 140.0),
    ("000002.SZ", date(2024, 3, 31), date(2024, 4, 22), 200.0),
    ("000002.SZ", date(2024, 6, 30), date(2024, 8, 26), 210.0),
    ("000002.SZ", date(2024, 9, 30), date(2024, 10, 28), 220.0),
    ("000002.SZ", date(2025, 3, 31), date(2025, 4, 21), 240.0),
    ("600000.SH", date(2024, 12, 31), date(2025, 4, 18), 330.0),
    ("600000.SH", date(2025, 3, 31), date(2025, 4, 21), 340.0),
)
"""`(security, period, announcement, revenue)`, one row per filing this corpus states."""

SUBJECTS: Final[tuple[str, ...]] = ("000001.SZ", "000002.SZ", "600000.SH")

YEARS: Final[tuple[int, ...]] = (2024, 2025)
"""The **announcement** years these rows land in, which is how a statement partition is filed.

Two of them for five periods, which is `cli.py::PANEL_BUILD_SPAN_TARGETS`' finding from the read
side: a request window is a report-period year and the rows are filed by `ann_date`, so a period
reach and a year list are not the same list.
"""


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=SHANGHAI)


def _batch(dataset: str, rows: tuple[_Filing, ...]) -> ColumnarPanelBatch:
    """One announcement year's filings, through `income`'s own projection and its own clock.

    All four clocks are the announcement instant, which is what
    `providers/tushare.py::_announcement_timeline` does for every statement row and why two
    versions of one filing cannot be told apart by any of them.
    """
    announced = tuple(_midnight(item[2]) for item in rows)
    columns = (
        PanelColumn(REPORT_PERIOD_COLUMN, "string", tuple(item[1].isoformat() for item in rows)),
        PanelColumn("ann_date", "string", tuple(item[2].isoformat() for item in rows)),
        PanelColumn("f_ann_date", "string", tuple(item[2].isoformat() for item in rows)),
        PanelColumn("update_flag", "string", tuple("1" for _ in rows)),
        *(
            PanelColumn(
                name,
                "float",
                tuple(item[3] if name == "revenue" else 1.0 for item in rows),
            )
            for name in INCOME_DATA_COLUMNS
        ),
    )
    assert tuple(column.name for column in columns) == statement_panel_columns(INCOME_DATASET)
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=dataset,
        kind="income",
        as_of=BUILT_AT,
        fetched_at=BUILT_AT,
        status="success",
        subjects=tuple(item[0] for item in rows),
        timeline=TimelineColumns(
            event_time=announced,
            available_time=announced,
            ingested_time=tuple(max(BUILT_AT, moment) for moment in announced),
            revision_time=announced,
        ),
        columns=columns,
    )


def _written(tmp_path: Path, *, dataset: str, rows: tuple[_Filing, ...] = CORPUS) -> PanelStore:
    """`rows` on disk under `dataset`, one partition per announcement year."""
    store = PanelStore(tmp_path / dataset)
    for year in YEARS:
        year_rows = tuple(item for item in rows if item[2].year == year)
        if year_rows:
            write_panel_batch(store, _batch(dataset, year_rows), year=year)
    return store


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    return _written(tmp_path, dataset=INCOME_DATASET)


def _requirement(dataset: str = INCOME_DATASET, **overrides: Any) -> ReadinessRequirement:
    if dataset == INCOME_DATASET:
        built = financial_statement_requirement(
            dataset=dataset, years=YEARS, as_of=AS_OF, max_staleness=STALENESS
        )
    else:
        built = ReadinessRequirement(
            dataset=dataset,
            as_of=AS_OF,
            years=YEARS,
            required_dates=None,
            required_subjects=None,
            required_fields=statement_panel_columns(INCOME_DATASET),
            max_staleness=STALENESS,
        )
    return built if not overrides else replace(built, **overrides)


def _definition(
    *,
    key: str = "probe_revenue_yoy",
    lookback_periods: int = 5,
    max_window_periods: int = 5,
    dataset: str = INCOME_DATASET,
) -> FactorDefinition:
    """A one-column probe on the report-period axis, `V2-P3-011`'s revenue year-on-year shape."""
    on_period_axis = dataset == INCOME_DATASET
    return FactorDefinition(
        key=key,
        version=1,
        family="growth",
        direction="higher_is_better",
        required_fields=(FactorField(dataset=dataset, column="revenue"),),
        lookback_sessions=None if on_period_axis else lookback_periods,
        max_window_sessions=None if on_period_axis else max_window_periods,
        lookback_periods=lookback_periods if on_period_axis else None,
        max_window_periods=max_window_periods if on_period_axis else None,
    )


def _year_on_year(window: FactorWindow) -> float | None:
    """`revenue[-1] / revenue[-5] - 1`: the latest period against the one four quarters back.

    Legitimate only because `max_window_periods == lookback_periods` makes the window contiguous;
    see `FactorDefinition.max_window_periods`. A denominator of zero is `undefined_value`.
    """
    revenue = window.series(window_dataset(window), "revenue")
    if revenue[-5] == 0.0:
        return None
    return revenue[-1] / revenue[-5] - 1.0


def window_dataset(window: FactorWindow) -> str:
    """Whichever dataset this window's one column came from -- the probe reads exactly one."""
    (name, _), *_ = window.values
    return name


def _compute(
    store: PanelStore,
    definition: FactorDefinition,
    *,
    dataset: str = INCOME_DATASET,
    evaluator: Any = _year_on_year,
    **overrides: Any,
) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "subjects": SUBJECTS,
        "universe": frozenset(SUBJECTS),
        "requirements": {dataset: _requirement(dataset)},
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        "evaluators": {definition.qualified_key: evaluator},
        **overrides,
    }
    return compute_factor(store, definition, **settings)


def _coverage(panel: FactorPanel) -> dict[str, str]:
    return {item.subject: item.coverage for item in panel.observations}


# --- the A/B the second axis exists for ----------------------------------------------------------


def test_two_periods_disclosed_on_one_day_are_two_rows_a_session_index_cannot_hold(
    tmp_path: Path,
) -> None:
    """The defect, as one pair of calls over one corpus.

    An A-share issuer routinely discloses its annual report and its first-quarter report on the
    same day; `000001.SZ` does it here on `DISCLOSURE_DAY`. Under a session index those are two
    rows of one `(subject, event_time)` key and the engine refuses the **whole build** -- which
    is right for a dataset with two versions of one observation and wrong for a dataset with two
    observations. The identical rows on the period axis compute.

    Matched on "per session" as well as on the count, so the refusal cannot be satisfied by some
    other guard that also mentions a duplicate.
    """
    session_store = _written(tmp_path, dataset=SESSION_DATASET)
    session_probe = _definition(key="probe_on_sessions", dataset=SESSION_DATASET)

    with pytest.raises(FactorEngineError, match=r"more than one row for 000001.SZ on 2025-04-20"):
        _compute(session_store, session_probe, dataset=SESSION_DATASET)
    with pytest.raises(FactorEngineError, match="one row per security per session"):
        _compute(session_store, session_probe, dataset=SESSION_DATASET)

    period_store = _written(tmp_path / "periods", dataset=INCOME_DATASET)
    panel = _compute(period_store, _definition())

    assert _coverage(panel)["000001.SZ"] == "computed"


def test_two_versions_of_one_filing_announced_on_one_day_still_refuse_the_whole_build(
    tmp_path: Path,
) -> None:
    """The fail-closed that must **not** have been weakened, on the shape that carries most of the
    real duplication.

    `fina_indicator` serves more than one row for 81.7% of its `(ts_code, end_date, ann_date)`
    keys, has neither `update_flag` nor `f_ann_date`, and `_announcement_timeline` gives such rows
    byte-equal four-clock timelines -- so nothing in the panel orders them. They collided on
    `(subject, event_time)` under a session index and collide on `(subject, period)` under this
    one, and the message says which axis it is talking about.

    The two rows disagree about `revenue`, so a build that silently picked one would answer, and
    the assertion below would be `computed` rather than a refusal.
    """
    duplicated = (
        *CORPUS,
        ("000001.SZ", date(2024, 9, 30), date(2024, 10, 25), 999.0),
    )
    store = _written(tmp_path, dataset=INCOME_DATASET, rows=duplicated)

    with pytest.raises(FactorEngineError, match=r"more than one row for 000001.SZ on 2024-09-30"):
        _compute(store, _definition())
    with pytest.raises(FactorEngineError, match="one row per security per period"):
        _compute(store, _definition())


def test_the_engines_period_selection_is_the_domains_filing_for(store: PanelStore) -> None:
    """The engine's selection rule against the domain's, over one corpus rather than two
    docstrings.

    `_read_dataset` states `filing_for`'s rule a second time -- keep the greatest visible
    announcement for a period -- because reusing `statement_histories_from_panel_rows` would make
    a factor that reads one column fetch all ten of `income`'s projected values. A second
    statement of one rule is only safe with a run-time audit against the first, which is this.

    `RESTATED_PERIOD` is the case that discriminates: two announcements, two revenues, and the
    later one is what a reader standing at `AS_OF` has.
    """
    captured: dict[str, FactorWindow] = {}

    def capture(window: FactorWindow) -> float | None:
        captured[window.subject] = window
        return 1.0

    _compute(store, _definition(), evaluator=capture)
    rows = [
        (
            item[0],
            item[1].isoformat(),
            item[2].isoformat(),
            item[2].isoformat(),
            "1",
            *(item[3] if name == "revenue" else 1.0 for name in INCOME_DATA_COLUMNS),
        )
        for item in CORPUS
    ]
    histories = statement_histories_from_panel_rows(
        dataset=INCOME_DATASET,
        columns=(SUBJECT_COLUMN_NAME, *statement_panel_columns(INCOME_DATASET)),
        rows=rows,
    )
    window = captured["000001.SZ"]
    history = histories["000001.SZ"]
    day = AS_OF.astimezone(SHANGHAI).date()

    assert window.periods == PANEL_PERIODS
    assert history.periods_on(day) == PANEL_PERIODS
    assert history.latest_filing_on(day).period == PANEL_PERIODS[-1]
    assert window.series(INCOME_DATASET, "revenue") == tuple(
        history.filing_for(period, day).value_of("revenue") for period in window.periods
    )
    assert history.filing_for(RESTATED_PERIOD, day).announced_on == LATER_ANNOUNCEMENT
    assert RESTATED_REVENUE in window.series(INCOME_DATASET, "revenue")
    assert FIRST_REVENUE not in window.series(INCOME_DATASET, "revenue")


def test_the_later_announcement_wins_whichever_order_the_partition_returns_them_in(
    tmp_path: Path,
) -> None:
    """The selection has to be order-independent, and the corpus above cannot show that it is.

    There the two announcements of `RESTATED_PERIOD` land in different partitions, which the
    engine reads oldest year first -- so the later one always arrives second and "keep the greater
    announcement" would be indistinguishable from "keep the last row seen". A partition returns
    its rows in no promised order, so this puts both announcements of one period in **one**
    partition with the later one first and asserts the same answer comes back.

    Without the guard this test drives, the engine takes 200.0 here rather than 240.0 -- the
    original rather than the restatement, which is the version a reader standing at `AS_OF` does
    not have.
    """
    one_year = datetime(2024, 9, 2, 4, 0, tzinfo=UTC)
    restated_first: tuple[_Filing, ...] = (
        ("000002.SZ", date(2024, 3, 31), date(2024, 8, 20), 240.0),
        ("000002.SZ", date(2024, 3, 31), date(2024, 4, 22), 200.0),
    )
    store = PanelStore(tmp_path / "one_partition")
    write_panel_batch(store, _batch(INCOME_DATASET, restated_first), year=2024)
    requirement = financial_statement_requirement(
        dataset=INCOME_DATASET, years=(2024,), as_of=one_year, max_staleness=STALENESS
    )
    panel = _compute(
        store,
        _definition(key="probe_one_period", lookback_periods=1, max_window_periods=1),
        evaluator=lambda window: window.series(INCOME_DATASET, "revenue")[-1],
        as_of=one_year,
        subjects=("000002.SZ",),
        universe=frozenset({"000002.SZ"}),
        requirements={INCOME_DATASET: requirement},
    )

    assert panel.values()["000002.SZ"] == pytest.approx(240.0)


@pytest.mark.parametrize(
    ("stored", "message"),
    [
        (None, "holds NoneType for 000001.SZ"),
        (20240331, "holds int for 000001.SZ"),
        ("2024-13-45", "holds '2024-13-45' for 000001.SZ, which is not an ISO date"),
        ("Q1 2024", "holds 'Q1 2024' for 000001.SZ, which is not an ISO date"),
    ],
)
def test_a_report_period_that_is_not_a_stored_iso_date_is_refused(
    stored: object, message: str
) -> None:
    """A refusal rather than a coverage code, for `_numeric`'s reason: a `report_period` this
    engine cannot read is a property of the *partition* and not of one security's fundamentals,
    so `input_missing` would tell a reader to re-fetch a row that is already there.

    Four shapes, two per branch, because each branch has a way of being wrong that the other
    does not catch: a stored null and a stored integer both fail the type check, and a
    well-formed-looking date with an impossible month and a free-text quarter label both fail the
    parse. Tushare's own `YYYYMMDD` is deliberately **not** among them -- `date.fromisoformat`
    accepts the basic ISO form on Python 3.11, so `'20240331'` parses to the right day, which is a
    property worth knowing rather than one to assert against. Driven on the decoder directly, in
    `_stored_value`'s and `_coverage_code`'s shape, because a partition carrying any of these
    would have to be hand-built to exist at all.
    """
    with pytest.raises(FactorEngineError, match=message):
        _report_period(stored, dataset=INCOME_DATASET, subject="000001.SZ")

    assert _report_period("2024-03-31", dataset=INCOME_DATASET, subject="000001.SZ") == date(
        2024, 3, 31
    )


# --- what the two period fields decide ------------------------------------------------------------


def test_the_latest_knowable_period_is_the_latest_period_and_the_window_reaches_a_year_back(
    store: PanelStore,
) -> None:
    """`V2-P3-011`'s year-on-year, and the answer to "which period is the latest at `as_of`".

    Both halves are asserted as *values* rather than as shape: the window's last period is the
    2025 Q1 the issuer had announced by `AS_OF`, its `[-5]` is the same quarter of the previous
    year, and the factor's own arithmetic on them is the number a hand computation gives. A
    window that reached one period short, or that ordered by announcement rather than by period,
    would produce a different number here rather than a different length.
    """
    panel = _compute(store, _definition())
    values = panel.values()

    assert panel.observations[0].subject == "000001.SZ"
    assert values["000001.SZ"] == pytest.approx(140.0 / 100.0 - 1.0)
    assert panel.observations[0].input_period_first == date(2024, 3, 31)
    assert panel.observations[0].input_period_last == date(2025, 3, 31)
    assert panel.observations[0].input_session_first is None
    assert panel.observations[0].input_session_last is None
    assert panel.observations[0].input_row_count == 5


def test_a_security_with_too_few_filings_is_insufficient_history_and_records_no_window(
    store: PanelStore,
) -> None:
    """The count shortfall, and the answer that is not an error.

    `600000.SH` has announced two periods by `AS_OF`; a five-period reach genuinely has no answer
    for it, exactly as a 120-session momentum has none for a name that listed nine sessions ago.
    It carries no period window, because there was none to record -- which is what keeps this
    distinguishable on the stored row from the span overrun below without a sixth coverage code.
    """
    panel = _compute(store, _definition())
    short = next(item for item in panel.observations if item.subject == "600000.SH")

    assert short.coverage == "insufficient_history"
    assert short.value is None
    assert short.input_period_first is None
    assert short.input_period_last is None
    assert short.input_row_count == 2


def test_a_gap_in_the_filings_is_separable_from_the_count_by_max_window_periods_alone(
    store: PanelStore,
) -> None:
    """The fixture that tells `max_window_periods` apart from its neighbour.

    `000002.SZ` filed four periods and skipped 2024-12-31, so its four most recent filings span
    **five** panel periods. Same rows, same `lookback_periods`, same `as_of`: at
    `max_window_periods=4` it is `insufficient_history` and at `5` it is `computed`. A test that
    only asserted the column was rendered would pass on an engine that ignored the field.

    `000001.SZ` filed every period and is `computed` under both, which is what makes the moving
    part the span rather than the reach.
    """
    strict = _compute(
        store,
        _definition(key="probe_contiguous", lookback_periods=4, max_window_periods=4),
        evaluator=lambda window: window.series(INCOME_DATASET, "revenue")[-1],
    )
    tolerant = _compute(
        store,
        _definition(key="probe_tolerant", lookback_periods=4, max_window_periods=5),
        evaluator=lambda window: window.series(INCOME_DATASET, "revenue")[-1],
    )

    assert _coverage(strict)["000002.SZ"] == "insufficient_history"
    assert _coverage(tolerant)["000002.SZ"] == "computed"
    assert _coverage(strict)["000001.SZ"] == "computed"
    assert _coverage(tolerant)["000001.SZ"] == "computed"

    refused = next(item for item in strict.observations if item.subject == "000002.SZ")

    assert refused.input_period_first == date(2024, 3, 31)
    assert refused.input_period_last == date(2025, 3, 31)
    assert tolerant.values()["000002.SZ"] == pytest.approx(240.0)


def test_a_reach_wider_than_the_whole_panel_is_a_refusal_rather_than_a_panel_of_codes(
    store: PanelStore,
) -> None:
    """ "This build has no answer for anybody" is `FactorEngineError`'s category on both axes.

    Every security's period set is a subset of the panel's, so a nine-period reach over a corpus
    holding five makes `insufficient_history` for the whole cross section arithmetic rather than
    a finding -- and a stored panel of it is the fail-open dressed as coverage that the session
    axis already refuses. The message names the announcement years, because a statement partition
    is filed by `ann_date` while a reach is counted in periods, which is where the fault is.
    """
    with pytest.raises(FactorEngineError, match=r"needs 9 report periods .* holds 5"):
        _compute(store, _definition(key="probe_too_deep", lookback_periods=9, max_window_periods=9))
    with pytest.raises(FactorEngineError, match=r"year\(s\) \[2024, 2025\]"):
        _compute(store, _definition(key="probe_too_deep", lookback_periods=9, max_window_periods=9))


def test_a_requirement_that_does_not_require_the_period_column_is_refused(
    store: PanelStore,
) -> None:
    """The engine projects `report_period` whether or not the factor names it, so a requirement
    that did not require it would report `ready` for a partition the projection then fails to
    bind -- several layers down, as a binder error naming neither the factor nor the dataset.

    The same check the factor's own columns are already under, one column further out.
    """
    without = _requirement(
        required_fields=tuple(
            name for name in statement_panel_columns(INCOME_DATASET) if name != REPORT_PERIOD_COLUMN
        )
    )

    with pytest.raises(FactorEngineError, match=r"reads \['report_period'\] from income"):
        _compute(store, _definition(), requirements={INCOME_DATASET: without})


# --- the stored row and the stored build ----------------------------------------------------------


def test_the_period_reach_and_the_period_window_survive_the_round_trip(
    store: PanelStore,
) -> None:
    """Both new pairs, read back off Parquet rather than off the objects that were written.

    A column that was never written, or one that decodes into the wrong field, is only visible
    here. The manifest's session pair is asserted to be **null** in the same breath, because
    "stored as null" and "stored as zero" are the difference between a factor that reads no
    session and one that reads none of them.
    """
    definition = _definition()
    write_factor_panels(store, [_compute(store, definition)])

    (manifest,) = load_factor_manifests(store, definition, years=(AS_OF.year,), as_of=AS_OF)
    observations = load_factor_observations(store, definition, years=(AS_OF.year,), as_of=AS_OF)
    computed = next(item for item in observations if item.subject == "000001.SZ")
    short = next(item for item in observations if item.subject == "600000.SH")

    assert manifest.lookback_periods == 5
    assert manifest.max_window_periods == 5
    assert manifest.lookback_sessions is None
    assert manifest.max_window_sessions is None
    assert computed.input_period_first == date(2024, 3, 31)
    assert computed.input_period_last == date(2025, 3, 31)
    assert computed.input_session_first is None
    assert short.input_period_first is None
    assert short.coverage == "insufficient_history"


def test_the_period_reach_reaches_the_build_identity(store: PanelStore) -> None:
    """Two builds that differ only in `max_window_periods` produce different numbers here --
    `000002.SZ` is `computed` under one and not the other -- so a `manifest_id` that ignored the
    field would file two different cross sections under one identity, which is the collision
    `subject_digest` was added for in the other direction.

    The reproduction half is asserted beside it: the same declaration twice is the same ID.
    """
    strict = _compute(
        store,
        _definition(key="probe_reach", lookback_periods=4, max_window_periods=4),
        evaluator=lambda window: window.series(INCOME_DATASET, "revenue")[-1],
    )
    tolerant = _compute(
        store,
        _definition(key="probe_reach", lookback_periods=4, max_window_periods=5),
        evaluator=lambda window: window.series(INCOME_DATASET, "revenue")[-1],
    )
    again = _compute(
        store,
        _definition(key="probe_reach", lookback_periods=4, max_window_periods=4),
        evaluator=lambda window: window.series(INCOME_DATASET, "revenue")[-1],
    )

    assert strict.manifest.manifest_id != tolerant.manifest.manifest_id
    assert strict.definition.factor_id != tolerant.definition.factor_id
    assert strict.manifest.manifest_id == again.manifest.manifest_id
    assert strict.values() != tolerant.values()


def test_a_factor_on_both_axes_gets_one_window_per_axis(tmp_path: Path) -> None:
    """`V2-P3-009`'s EP shape: a filing over a price, which is two indices in one evaluator.

    The two series are aligned to different axes and have different lengths, which is the whole
    reason `FactorWindow` carries both tuples rather than reinterpreting one. Asserted as the
    arithmetic an EP would do, so a window that aligned the filing to sessions would divide by
    the wrong number rather than raise.
    """
    store = _written(tmp_path, dataset=INCOME_DATASET)
    # The price side is written under the probe name so it lands on the session axis. Its rows
    # are dated at the period ends only so the two axes' points are comparable by eye; nothing
    # here reads its `report_period` column, because a session-indexed dataset is not projected
    # for one.
    prices: tuple[_Filing, ...] = tuple(
        (code, period, period, 10.0 + index)
        for code in ("000001.SZ", "600000.SH")
        for index, period in enumerate(PANEL_PERIODS)
    )
    for year in YEARS:
        rows = tuple(item for item in prices if item[2].year == year)
        if rows:
            write_panel_batch(store, _batch(SESSION_DATASET, rows), year=year)

    definition = FactorDefinition(
        key="probe_earnings_yield",
        version=1,
        family="value",
        direction="higher_is_better",
        required_fields=(
            FactorField(dataset=INCOME_DATASET, column="revenue"),
            FactorField(dataset=SESSION_DATASET, column="revenue"),
        ),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=2,
        max_window_periods=2,
    )
    captured: dict[str, FactorWindow] = {}

    def earnings_yield(window: FactorWindow) -> float | None:
        captured[window.subject] = window
        filings = window.series(INCOME_DATASET, "revenue")
        price = window.series(SESSION_DATASET, "revenue")[-1]
        return filings[-1] / price

    panel = _compute(
        store,
        definition,
        evaluator=earnings_yield,
        subjects=("000001.SZ",),
        universe=frozenset({"000001.SZ"}),
        requirements={
            INCOME_DATASET: _requirement(INCOME_DATASET),
            SESSION_DATASET: _requirement(SESSION_DATASET),
        },
    )
    window = captured["000001.SZ"]

    assert len(window.periods) == 2
    assert len(window.sessions) == 1
    assert window.periods == (date(2024, 12, 31), date(2025, 3, 31))
    assert panel.values()["000001.SZ"] == pytest.approx(140.0 / 14.0)
    assert panel.observations[0].input_period_last == date(2025, 3, 31)
    assert panel.observations[0].input_session_last == window.sessions[-1]
    assert panel.observations[0].input_row_count == 3

    # The half a one-axis factor cannot reach: one axis forms its window and the other falls
    # short, so the stored row has to carry the window that *was* formed and nothing for the one
    # that was not. `600000.SH` has priced every panel period and announced only two of them, so
    # against a five-period reach it is short on filings and complete on sessions, and both facts
    # land on the one observation. A row that recorded neither window -- which is what the session
    # axis alone would have justified -- would lose the half that was formed.
    thin = _compute(
        store,
        definition.model_copy(
            update={"key": "probe_short_filings", "lookback_periods": 5, "max_window_periods": 5}
        ),
        evaluator=earnings_yield,
        subjects=("600000.SH",),
        universe=frozenset({"600000.SH"}),
        requirements={
            INCOME_DATASET: _requirement(INCOME_DATASET),
            SESSION_DATASET: _requirement(SESSION_DATASET),
        },
    )
    refused = thin.observations[0]

    assert refused.coverage == "insufficient_history"
    assert refused.input_period_first is None
    assert refused.input_period_last is None
    assert refused.input_session_first == refused.input_session_last == PANEL_PERIODS[-1]
