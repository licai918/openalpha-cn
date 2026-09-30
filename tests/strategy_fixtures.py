"""A generated panel a strategy backtest can run on, shared by the view and the CLI tests
(`V2-P6-007`).

At the `tests/` root for `panel_fixtures.py`'s reason: two subtrees use it --
`tests/unit/test_strategy_view.py` and `tests/unit/test_cli_strategy_backtest.py` -- and each
building its own copy would let the two faces be tested against two different panels.

The panel is `panel_fixtures.generate_panel`'s ten-session, eight-security corpus with closes that
move between sessions, plus two things that generator has no synthetic form for: an `index_daily`
partition (000300.SH, which the level read requires, and 000905.SH, the protocol's benchmark) and
raw `reversal_1d/v1` cross sections built through the real engine at each session's 16:30
publication instant. `write_tiered_corpus` adds the processed and neutralized tiers under probe
specs an eight-name panel clears. Everything is written at test time; nothing is checked in.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from panel_fixtures import (
    DAILY_BASIC_DATASET,
    EXCHANGE,
    WINDOW_FIRST,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from panel_fixtures import _bar_batch as bar_batch
from panel_fixtures import _calendar_batch as calendar_batch
from panel_fixtures import _factor_batch as factor_batch
from panel_fixtures import _index_weight_batch as index_weight_batch
from panel_fixtures import _limit_batch as limit_batch
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from panel_fixtures import _read_instant as read_instant
from panel_fixtures import _suspension_batch as suspension_batch
from panel_fixtures import _valuation_batch as valuation_batch

from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformRegistry,
    FactorTransformSpec,
    MissingValuePolicy,
    WinsorizationPolicy,
)
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATA_COLUMNS, INDEX_DAILY_DATASET
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import LISTING_EVENT, STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, CalendarDay
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorPanel,
    apply_factor_transform,
    compute_factor,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import (
    daily_requirement,
    load_suspensions,
    session_publication_instant,
    split_panel_batch_by_year,
    write_adjustment_factors,
    write_daily_panel,
    write_index_prices,
    write_price_limits,
    write_stock_universe,
    write_suspensions,
    write_trading_calendar,
)
from openalpha_cn.panel_neutralization import (
    apply_factor_neutralization,
    load_industry_market_cap_cross_section,
    write_neutralized_factor_panels,
)

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
COMMIT: Final[str] = "abcdef1234567"
BUILT_AT: Final[datetime] = datetime(2026, 1, 17, 1, 0, tzinfo=UTC)
READ_AT: Final[datetime] = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
"""12:00 Shanghai on the Saturday after the panel's last session: every close is knowable."""

INDEX_LEVELS: Final[dict[str, tuple[float, ...]]] = {
    "000300.SH": (4000.0, 4040.0, 4020.0, 4060.0, 4100.0, 4080.0, 4120.0, 4160.0, 4140.0, 4180.0),
    "000905.SH": (6000.0, 6030.0, 6090.0, 6060.0, 6120.0, 6150.0, 6180.0, 6120.0, 6180.0, 6240.0),
}


def build_instant(session: date, *, late: bool = False) -> datetime:
    """The session's publication instant (16:30 Shanghai), or half an hour after it."""
    instant = session_publication_instant(session)
    return instant + timedelta(minutes=30) if late else instant


def stored_value(subjects: Sequence[str], subject: str, session_index: int) -> float:
    """`reversal_1d/v1`'s stored value: a rotation of the securities, so holdings change."""
    position = (subjects.index(subject) + session_index) % len(subjects)
    return (position + 1) / 100.0


def _index_batch(sessions: Sequence[date]) -> ColumnarPanelBatch:
    subjects: list[str] = []
    days: list[date] = []
    levels: list[float] = []
    previous_levels: list[float] = []
    for code, path in INDEX_LEVELS.items():
        previous = path[0] / 1.001
        for day, level in zip(sessions, path[: len(sessions)], strict=True):
            subjects.append(code)
            days.append(day)
            levels.append(level)
            previous_levels.append(previous)
            previous = level
    columns = (
        PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        PanelColumn("open", "float", tuple(levels)),
        PanelColumn("high", "float", tuple(levels)),
        PanelColumn("low", "float", tuple(levels)),
        PanelColumn("close", "float", tuple(levels)),
        PanelColumn("pre_close", "float", tuple(previous_levels)),
        PanelColumn(
            "pct_chg",
            "float",
            tuple(
                (level / previous - 1.0) * 100.0
                for level, previous in zip(levels, previous_levels, strict=True)
            ),
        ),
        PanelColumn("vol", "float", tuple(300000.0 for _ in days)),
        PanelColumn("amount", "float", tuple(900000.0 for _ in days)),
    )
    assert tuple(column.name for column in columns) == INDEX_DAILY_DATA_COLUMNS
    instants = tuple(session_publication_instant(day) for day in days)
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=INDEX_DAILY_DATASET,
        kind="index_daily",
        as_of=BUILT_AT,
        fetched_at=BUILT_AT,
        status="success",
        subjects=tuple(subjects),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=tuple(max(BUILT_AT, moment) for moment in instants),
            revision_time=instants,
        ),
        columns=columns,
    )


def _build(
    store: PanelStore,
    panel: GeneratedPanel,
    session: date,
    *,
    late: bool,
    reversed_: bool = False,
    at: datetime | None = None,
) -> FactorPanel:
    """One raw cross section through the real engine, the evaluator seam supplying values.

    `reversed_` files the securities in the opposite order, so a second build on one session
    carries values a reader could tell apart from the first. `at` stamps it at that instant
    instead of `build_instant(session, late=late)` -- a re-run later on the session's evening.
    """
    instant = build_instant(session, late=late) if at is None else at
    index = panel.sessions.index(session)
    subjects = tuple(reversed(panel.securities)) if reversed_ else tuple(panel.securities)
    return compute_factor(
        store,
        REVERSAL,
        as_of=instant,
        subjects=subjects,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(),
                years=(session.year,),
                as_of=instant,
                max_staleness=timedelta(days=30),
            )
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={
            REVERSAL.qualified_key: lambda context: stored_value(subjects, context.subject, index)
        },
    )


def write_strategy_corpus(
    root: Path, *, late: bool = False, rebuilt_late: bool = False
) -> GeneratedPanel:
    """Write the panel, the index levels and a raw cross section on every session but the first.

    `late=True` stamps every build half an hour after its session's publication instant, which
    is the look-ahead shape the backtest must refuse. `rebuilt_late=True` keeps the on-time
    builds and adds a second, reversed build half an hour later on every session -- the shape a
    re-run produces, which a reader must not trade on in place of the one it had at the signal.
    """
    store = PanelStore(root / "panel")
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))
    write_generated_panel(store, panel)
    write_index_prices(store, [_index_batch(panel.sessions)])
    builds = [_build(store, panel, session, late=late) for session in panel.sessions[1:]]
    if rebuilt_late:
        builds.extend(
            _build(store, panel, session, late=True, reversed_=True)
            for session in panel.sessions[1:]
        )
    write_factor_panels(store, builds)
    return panel


def write_strategy_corpus_published_daily(
    root: Path,
    *,
    label_inputs_through: date | None = None,
    through: date | None = None,
    late: bool = False,
    builds_through: date | None = None,
) -> GeneratedPanel:
    """`write_strategy_corpus`'s ten-session panel, but with `adj_factor` and `suspend_d`
    published session by session, each session's own row available at its own 16:30 close, the
    way the real store accumulates a year (`V2-P6-012` fix round 3).

    `write_strategy_corpus` writes each of those as one partition covering all ten sessions in a
    single call, so the partition's own stored `max_available_time` -- what `read_if_ready()`'s
    per-partition `not_yet_knowable` gate compares an `as_of` against
    (`panel_ingest.load_adjustment_histories`/`load_suspensions`; that module's own docstring,
    `V2-P4-079`/`086`/`094`) -- is the *tenth* session's, regardless of which session a caller
    actually asks about. A record recomputed at its own filing time, days before the panel had
    grown that far (`strategy_registration.RecordCheck`), then refuses to even read the
    partition -- not because anything it read changed, but because the fixture's own one-shot
    write makes every earlier `as_of` look premature, a fact about how this fixture was built
    rather than about the record.

    This fixture builds `adj_factor`/`suspend_d` through growing-window writes instead --
    `generate_panel(window=(WINDOW_FIRST, sessions[i]))` for each `i` up to
    `label_inputs_through`, which reproduces exactly session `i`'s own values (`_close_of`/
    `_factor_of` index into `sessions` by position, and a prefix of the full ten shares every
    earlier position with it) -- so a read at session N's own evening sees a partition whose
    current coverage stops at session N, exactly as the real store would after N days of
    ingestion. `write_adjustment_factors`/`write_suspensions` are the real writers, every guard
    included; only the *shape of the calls* -- growing writes instead of one whole-year write --
    differs from `write_strategy_corpus`.

    `daily`/`daily_basic` are **not** built incrementally: `daily` is a step function's opposite
    -- a new row every session regardless -- and its read door is already per-session
    (`panel_ingest.load_daily_bars`, unlike `load_adjustment_histories`/`load_suspensions`'s
    whole-year one; `_restate_a_close`'s own precedent in `test_daily_selection.py` already reads
    a one-shot `daily` partition at an early `as_of` without incident). They, the trading
    calendar, the security registry, the index levels and the factor builds are written once,
    whole-range, through `through` (which may run *later* than `label_inputs_through` -- a book
    needs a session after its newest record to hold a period open to, and `adj_factor`/
    `suspend_d` do not need a fresh row for a session nothing changed on; a step function answers
    a later date from its last change point). `through=None` (the default) matches `label_inputs_
    through`'s own resolution, or the whole ten sessions if that is `None` too.

    `label_inputs_through=None` (the default) builds `adj_factor`/`suspend_d` incrementally
    through the same session `through` resolves to -- growing writes all the way, not a one-shot
    build, so `through`-without-`label_inputs_through` is `write_strategy_corpus_published_daily`
    at its plainest: every `LABEL_INPUTS` dataset published session by session through the given
    end. `upstream_defects` is not written here either, matching `write_strategy_corpus`: nothing
    in this corpus ever names a return-path defect, and an unwritten partition of that dataset
    answers "none" rather than refusing.

    `late=True` carries the same meaning `write_strategy_corpus`'s does, for the factor builds.
    `builds_through` stops the factor builds at that session (default: `through`), so every
    label input can reach later than any factor partition (`V2-P6-011` fix round 15).
    """
    store = PanelStore(root / "panel")
    full = generate_panel(shapes=("daily.close_moves_between_sessions",))
    all_sessions = full.sessions
    through_stop = len(all_sessions) if through is None else all_sessions.index(through) + 1
    sessions = all_sessions[:through_stop]
    label_stop = (
        through_stop
        if label_inputs_through is None
        else all_sessions.index(label_inputs_through) + 1
    )
    write_trading_calendar(store, full.batch(TRADING_CALENDAR_DATASET))
    write_stock_universe(store, full.batch(STOCK_BASIC_DATASET))
    calendar = full.calendar()
    # The shapeless panel's one untimed halt sits at the fifth session regardless of window
    # (`_halted_key`), so no window shorter than five sessions can be generated at all; the
    # smallest publishable state is therefore "through the fifth session", not "through the
    # first" -- sessions 0-3 are never published alone, exactly as a five-name halt convention
    # would make them unreachable in a real, incrementally-published corpus too.
    for index in range(min(4, label_stop - 1), label_stop):
        window = generate_panel(
            shapes=("daily.close_moves_between_sessions",), window=(WINDOW_FIRST, sessions[index])
        )

        def _fetched_at_this_window(dataset: str, *, window: GeneratedPanel = window) -> Any:
            # `_factor_batch`/`_suspension_batch` stamp
            # `fetched_at=max([panel_fixtures.AS_OF, *available])` -- `AS_OF` is that module's
            # own constant, the *full* corpus's read instant, not this window's -- so every
            # batch `generate_panel` builds, however short its own window, claims to have been
            # fetched on the full corpus's last day. `_refuse_missing_factor_sessions`'s upper
            # bound is "the day before the fetch", so an unmodified window batch is checked
            # against the *full* ten-session range regardless of `index`. Restamping
            # `as_of`/`fetched_at` to `window.as_of` -- already `_read_instant(sessions[index])`,
            # the morning after this window's own last session -- makes the guard's upper bound
            # this window's, not the full corpus's.
            return dataclasses.replace(
                window.batch(dataset), as_of=window.as_of, fetched_at=window.as_of
            )

        write_adjustment_factors(
            store, [_fetched_at_this_window(ADJ_FACTOR_DATASET)], calendar=calendar
        )
        write_suspensions(store, [_fetched_at_this_window(SUSPENSION_DATASET)])
    # Everything else this corpus carries -- `suspend_d` extended past `label_inputs_through`
    # (a step of "nothing suspended" needs no new row, but `write_daily_panel`'s halt
    # cross-check still wants every session through `through` explained), `daily`/`daily_basic`
    # and `stk_limit` (the model plane's label window reads published limit bands too) -- is
    # written once, whole-range, through `through`: none of their read doors is the one this
    # fixture exists to work around.
    through_window = generate_panel(
        shapes=("daily.close_moves_between_sessions",), window=(WINDOW_FIRST, sessions[-1])
    )

    def _fetched_at_through(dataset: str) -> Any:
        return dataclasses.replace(
            through_window.batch(dataset),
            as_of=through_window.as_of,
            fetched_at=through_window.as_of,
        )

    if through_stop > label_stop:
        write_suspensions(store, [_fetched_at_through(SUSPENSION_DATASET)])
    halts = load_suspensions(
        store, years=(through_window.year,), as_of=through_window.as_of, max_staleness=None
    )
    write_daily_panel(
        store,
        bars=[_fetched_at_through(DAILY_DATASET)],
        fundamentals=[_fetched_at_through(DAILY_BASIC_DATASET)],
        calendar=calendar,
        halts=halts,
    )
    write_price_limits(store, [_fetched_at_through(PRICE_LIMIT_DATASET)], calendar=calendar)
    write_index_prices(store, [_index_batch(sessions)])
    built = sessions if builds_through is None else sessions[: sessions.index(builds_through) + 1]
    builds = [_build(store, full, session, late=late) for session in built[1:]]
    write_factor_panels(store, builds)
    return dataclasses.replace(full, sessions=sessions, as_of=read_instant(sessions[-1]))


# --- a corpus that crosses a calendar year (V2-P6-014 fix round 2) --------------------------------

TWO_YEAR_FIRST: Final[date] = date(2026, 1, 5)
"""The first priced session. A past year's `adj_factor` partition must cover the whole year
(`panel_ingest._refuse_missing_factor_sessions`), so all of 2026 is priced."""
TWO_YEAR_BUILDS_FROM: Final[date] = date(2026, 12, 1)
"""Factor builds start here: every lookback the two-year tests declare fits inside December."""
TWO_YEAR_FACTOR_YEAR_START: Final[date] = date(2027, 1, 4)
"""2027's first session: a store built from here holds no factor partition for 2026 at all."""
TWO_YEAR_LAST: Final[date] = date(2027, 1, 22)
TWO_YEAR_NEW_YEAR_HOLIDAY: Final[date] = date(2027, 1, 1)
TWO_YEAR_LATE_LISTING: Final[tuple[str, date]] = ("000011.SZ", date(2027, 1, 11))
"""A registry-only listing in 2027, so `stock_basic` holds a partition for the second year as a
real registry does (a year with no lifecycle event has none, and a read naming it refuses)."""
TWO_YEAR_SHAPES: Final[tuple[str, ...]] = (
    "daily.close_moves_between_sessions",
    "suspension.halt_on_the_newest_session",
)
"""The newest-session halt gives `suspend_d` a 2027 row, and so a 2027 partition."""


def _two_year_panel() -> GeneratedPanel:
    """`generate_panel`'s whole-2026 window, extended session by session into January 2027.

    Every session-keyed batch is rebuilt over the whole two-year session tuple in one call, so a
    January session's `pre_close` is December 31's close and every label chained across the
    boundary is priced from one continuous path -- two single-year batches would restart it.
    """
    base = generate_panel(shapes=TWO_YEAR_SHAPES, window=(TWO_YEAR_FIRST, date(2026, 12, 31)))
    second = tuple(
        CalendarDay(
            calendar_date=day,
            is_trading=day.weekday() < 5 and day != TWO_YEAR_NEW_YEAR_HOLIDAY,
        )
        for day in (date(2027, 1, 1) + timedelta(days=offset) for offset in range(59))
    )
    days = (*base.calendar_days, *second)
    sessions = tuple(
        day.calendar_date
        for day in days
        if day.is_trading and TWO_YEAR_FIRST <= day.calendar_date <= TWO_YEAR_LAST
    )
    grid = {"sessions": sessions, "securities": base.securities, "shapes": base.shapes}
    code, listed = TWO_YEAR_LATE_LISTING
    universe = base.batch(STOCK_BASIC_DATASET)
    late = midnight_shanghai(listed)
    registry = dataclasses.replace(
        universe,
        as_of=late,
        fetched_at=late,
        subjects=(*universe.subjects, code),
        timeline=TimelineColumns(
            event_time=(*universe.timeline.event_time, late),
            available_time=(*universe.timeline.available_time, late),
            ingested_time=(*universe.timeline.ingested_time, late),
            revision_time=(*universe.timeline.revision_time, late),
        ),
        columns=tuple(
            PanelColumn(
                column.name,
                column.kind,
                (
                    *column.values,
                    {
                        "lifecycle_event": LISTING_EVENT,
                        "lifecycle_date": listed.isoformat(),
                        "exchange": EXCHANGE,
                    }[column.name],
                ),
            )
            for column in universe.columns
        ),
    )
    batches = {
        **base.batches,
        TRADING_CALENDAR_DATASET: calendar_batch(days),
        STOCK_BASIC_DATASET: registry,
        ADJ_FACTOR_DATASET: factor_batch(**grid),
        DAILY_DATASET: bar_batch(**grid),
        DAILY_BASIC_DATASET: valuation_batch(**grid),
        SUSPENSION_DATASET: suspension_batch(**grid),
        PRICE_LIMIT_DATASET: limit_batch(**grid),
        INDEX_WEIGHT_DATASET: index_weight_batch(**grid),
    }
    return dataclasses.replace(
        base,
        calendar_days=days,
        sessions=sessions,
        batches=MappingProxyType(batches),
        as_of=read_instant(sessions[-1]),
    )


def _two_year_build(store: PanelStore, panel: GeneratedPanel, session: date) -> FactorPanel:
    """One raw `reversal_1d/v1` build at `session`'s 16:30, over the years its inputs span."""
    instant = build_instant(session)
    index = panel.sessions.index(session)
    subjects = tuple(panel.securities)
    years = tuple(year for year in (session.year - 1, session.year) if year >= 2026)
    return compute_factor(
        store,
        REVERSAL,
        as_of=instant,
        subjects=subjects,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(), years=years, as_of=instant, max_staleness=timedelta(days=30)
            )
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={
            REVERSAL.qualified_key: lambda context: stored_value(subjects, context.subject, index)
        },
    )


def _write_by_year(store: PanelStore, panel: GeneratedPanel) -> None:
    """The datasets a strategy backtest reads, each split by year before its real writer runs.

    `write_generated_panel` hands each writer one batch, which is one partition year; the writers
    refuse a batch spanning two, so this splits with `split_panel_batch_by_year` and writes one
    partition year at a time.
    """
    calendar = panel.calendar()

    def parts(dataset: str) -> list[ColumnarPanelBatch]:
        return [part for _, part in split_panel_batch_by_year(panel.batch(dataset))]

    for part in parts(TRADING_CALENDAR_DATASET):
        write_trading_calendar(store, part)
    for part in parts(STOCK_BASIC_DATASET):
        write_stock_universe(store, part)
    for part in parts(ADJ_FACTOR_DATASET):
        write_adjustment_factors(store, [part], calendar=calendar)
    for part in parts(SUSPENSION_DATASET):
        write_suspensions(store, [part])
    halts = load_suspensions(store, years=(2026, 2027), as_of=panel.as_of, max_staleness=None)
    for bars, fundamentals in zip(parts(DAILY_DATASET), parts(DAILY_BASIC_DATASET), strict=True):
        write_daily_panel(
            store, bars=[bars], fundamentals=[fundamentals], calendar=calendar, halts=halts
        )
    for part in parts(PRICE_LIMIT_DATASET):
        write_price_limits(store, [part], calendar=calendar)


def write_two_year_corpus(
    root: Path, *, builds_from: date = TWO_YEAR_BUILDS_FROM
) -> GeneratedPanel:
    """A panel priced 2026-01-05 .. 2027-01-22, with a raw build on every session from 12-01.

    Two calendar years in every dataset a strategy backtest reads, so a signal day in December
    trades in January and every year-scoped read the streamed feeds make is exercised across the
    boundary. No `index_daily`: a test over it benchmarks against `equal_weight_all_a` alone.

    `builds_from` moves the first build (`V2-P6-025`): from `TWO_YEAR_FACTOR_YEAR_START` the factor
    store holds 2027 alone while every other dataset still holds 2026 -- the research store's
    shape, whose factor builds begin two years after its price warm-up does.
    """
    store = PanelStore(root / "panel")
    panel = _two_year_panel()
    _write_by_year(store, panel)
    write_factor_panels(
        store,
        [
            _two_year_build(store, panel, session)
            for session in panel.sessions
            if session >= builds_from
        ],
    )
    return panel


# --- the processed and neutralized tiers ---------------------------------------------------------

PROBE_TRANSFORM: Final[FactorTransformSpec] = FactorTransformSpec(
    key="probe_zscore",
    version=1,
    winsorization=WinsorizationPolicy(method="none"),
    standardization="zscore",
    missing_values=MissingValuePolicy(
        not_in_universe="exclude",
        insufficient_history="exclude",
        ambiguous_filing="exclude",
        input_missing="exclude",
        undefined_value="exclude",
    ),
    min_cross_section=1,
)
"""A transform whose floor an eight-name panel clears; the shipped one needs fifty names.

`tests/integration/panel/test_factor_neutralizations.py::_transform_spec`'s probe, restated with
the same settings because that helper lives in a test module no other file may import."""

PROBE_NEUTRALIZATION: Final[FactorNeutralizationSpec] = FactorNeutralizationSpec(
    key="probe_neutral",
    version=1,
    industry_level="L1",
    market_cap_measure="total_mv",
    market_cap_scale="log",
    participation="measured_only",
    min_industry_members=2,
    min_cross_section=2,
)
"""A neutralisation whose floors an eight-name panel clears (`industry_and_size/v1` needs 100)."""

PROBE_TRANSFORMS: Final[FactorTransformRegistry] = FactorTransformRegistry((PROBE_TRANSFORM,))
PROBE_NEUTRALIZATIONS: Final[FactorNeutralizationRegistry] = FactorNeutralizationRegistry(
    (PROBE_NEUTRALIZATION,)
)

CAP_BASE: Final[float] = 2_000_000.0
CAP_STEP: Final[float] = 750_000.0


def _with_market_caps(panel: GeneratedPanel) -> GeneratedPanel:
    """The generated panel with a `total_mv` that varies, so the size regressor is not flat.

    The generator writes `1.0` on every row, a design with no dispersion that the neutralisation
    refuses as degenerate; `test_factor_neutralizations._with_market_caps`' substitution.
    """
    batch = panel.batch(DAILY_BASIC_DATASET)
    order = tuple(panel.securities)
    caps = tuple(CAP_BASE + CAP_STEP * order.index(str(subject)) for subject in batch.subjects)
    columns = tuple(
        PanelColumn(column.name, column.kind, caps) if column.name == "total_mv" else column
        for column in batch.columns
    )
    replaced = dataclasses.replace(batch, columns=columns)
    return dataclasses.replace(panel, batches={**panel.batches, DAILY_BASIC_DATASET: replaced})


@dataclass(frozen=True, slots=True, kw_only=True)
class TieredCorpus:
    """What `write_tiered_corpus` stored, so a read can be held against the writer's rows."""

    panel: GeneratedPanel
    processed: Mapping[date, Mapping[str, float]]
    """Each build session's stored processed value per security, admitted rows only."""
    neutralized: Mapping[date, Mapping[str, float]]
    """Each build session's stored residual per security, admitted rows only."""


def write_tiered_corpus(root: Path) -> TieredCorpus:
    """The strategy panel with all three tiers of `reversal_1d/v1` written through the real writers.

    Every session but the first gets a raw build at its 16:30 instant, the probe transform of it
    and the probe neutralisation of that, each written by its own plane's writer
    (`write_factor_panels`, `write_processed_factor_panels`, `write_neutralized_factor_panels`).
    """
    store = PanelStore(root / "panel")
    panel = _with_market_caps(generate_panel(shapes=("daily.close_moves_between_sessions",)))
    write_generated_panel(store, panel)
    write_index_prices(store, [_index_batch(panel.sessions)])
    raw, processed, neutralized = [], [], []
    for session in panel.sessions[1:]:
        source = _build(store, panel, session, late=False)
        transformed = apply_factor_transform(
            source, PROBE_TRANSFORM, code_commit=COMMIT, built_at=source.built_at
        )
        section = load_industry_market_cap_cross_section(
            store,
            PROBE_NEUTRALIZATION,
            subjects=panel.securities,
            day=session,
            as_of=build_instant(session),
            calendar=panel.calendar(),
            membership_years=(session.year,),
            max_staleness=None,
        )
        raw.append(source)
        processed.append(transformed)
        neutralized.append(
            apply_factor_neutralization(
                transformed,
                PROBE_NEUTRALIZATION,
                section,
                code_commit=COMMIT,
                built_at=source.built_at,
            )
        )
    write_factor_panels(store, raw)
    write_processed_factor_panels(store, processed)
    write_neutralized_factor_panels(store, neutralized)
    zone = session_publication_instant(panel.sessions[0]).tzinfo
    return TieredCorpus(
        panel=panel,
        processed={
            build.observations[0].as_of.astimezone(zone).date(): {
                row.subject: row.value
                for row in build.observations
                if row.coverage in TIER_ADMITTED_CODES["processed"] and row.value is not None
            }
            for build in processed
        },
        neutralized={
            build.observations[0].as_of.astimezone(zone).date(): {
                row.subject: row.value
                for row in build.observations
                if row.coverage in TIER_ADMITTED_CODES["neutralized"] and row.value is not None
            }
            for build in neutralized
        },
    )
