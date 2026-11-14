"""`V2-P7-028`'s two limit-event factors, driven through the real engine and store.

The declarations and the evaluator's arithmetic are `tests/unit/test_factor_limit_events_rules.py`.
What is under test here is the three behaviours that need a panel:

1. **The counts are `session_limit_events`' counts over the factor's own window.** One generated
   panel, two readers: the factor's observations and `V2-P7-011`'s domain events, compared session
   by session. `limit_up_count_20` is `limit_up` + `consecutive_board`, `broken_board_count_20` is
   `broken_board`.
2. **A halt and a bandless session are skipped, not counted and not `input_missing`.** The window
   the factor sees is the sessions it can classify -- both datasets present -- and the skipped ones
   stretch it, which `max_window_sessions` prices.
3. **A window stretched past the declared span is refused.** Five bandless sessions inside the last
   twenty-five leave a security twenty classifiable sessions spanning twenty-five panel sessions:
   `insufficient_history` with the window recorded, while the rest of the cross section computes.
4. **Nothing classifiable is `insufficient_history`, not `input_missing`.** A security halted over
   the whole read (bands published, no bar) and one with bars but no published band both have no
   session this factor can classify; the window is empty rather than band-only, which is the honest
   code and not the one reserved for a short read.
5. **A recorded `V2-P6-020` return-path decision does not touch these factors.** Their close is a
   level compared against a published band, not a link in a return chain, so a recorded
   unknowable-return session inside the window leaves the observation `computed` on the
   band-derived count instead of abstaining.
6. **The family builds through `factor build`'s own path, carried and fresh alike.** Both factors,
   raw and processed tiers, two instants: one carried build's stored identities and coverage are
   the two single-instant builds' -- the path the controller's real build runs
   (`REQUIREMENT_BUILDERS`, `_CALENDAR_SCOPED_REQUIREMENTS`, `FactorReadCarry`).

The fixture is generated at test time and written through the real writers; no `.parquet` is
committed and no network is used (hard rule 6).
"""

from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from panel_fixtures import (
    LIMIT_STREAK_FIRST_SESSION_INDEX,
    LIMIT_STREAK_SECURITY_INDEX,
    SENTINEL_DOWN_LIMIT,
    SENTINEL_UP_LIMIT,
    GeneratedPanel,
    _arrayed_rows,
    _close_time,
    _published_time,
    _with_a_halt,
    generate_limit_streak_panel,
    write_generated_panel,
)

from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.limit_events import SessionLimitState, session_limit_events, session_state
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.upstream_defects import UPSTREAM_DEFECT_DATA_COLUMNS
from openalpha_cn.factor_view import (
    FactorBuildReport,
    FactorBuildRequest,
    build_factor_panels,
    factor_build_request,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    BROKEN_BOARD_COUNT_20,
    LIMIT_UP_COUNT_20,
    FactorObservation,
    FactorPanel,
    compute_factor,
)
from openalpha_cn.panel_ingest import (
    SUSPENSION_DATASET,
    UPSTREAM_DEFECTS_DATASET,
    daily_requirement,
    load_daily_bars,
    load_price_limits,
    price_limit_requirement,
    session_publication_instant,
    write_upstream_defects,
)

YEAR: Final[int] = 2026
FIRST: Final[date] = date(2026, 1, 2)
LAST: Final[date] = date(2026, 2, 27)
"""The priced window: forty sessions, enough for the twenty-session reach plus slack."""

STALENESS: Final[timedelta] = timedelta(days=30)
"""One trading month, the bound every input requirement in this file states.

A bound rather than a waiver because `compute_factor` refuses a waived one: a build whose input
slice reaches arbitrarily far short of `as_of` is not a build whose coverage codes mean anything.
"""

WINDOW_END_INDEX: Final[int] = 21
"""The session whose evening the count tests read at: sessions 0..21 are visible, 22 counted."""

SPAN_END_INDEX: Final[int] = 24
"""The session the span test reads at: twenty-five visible sessions, so five skipped ones leave a
count of twenty that reaches back to session 0 -- a span of twenty-five against a declared 24."""


def _panel() -> GeneratedPanel:
    return generate_limit_streak_panel(window=(FIRST, LAST))


def _store(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)
    return store


def _requirements(panel: GeneratedPanel, as_of: datetime) -> dict[str, object]:
    calendar = panel.calendar()
    return {
        DAILY_DATASET: daily_requirement(
            calendar, years=(YEAR,), as_of=as_of, max_staleness=STALENESS
        ),
        PRICE_LIMIT_DATASET: price_limit_requirement(
            calendar, years=(YEAR,), as_of=as_of, max_staleness=STALENESS
        ),
    }


def _published(panel: GeneratedPanel, index: int) -> datetime:
    """An hour after the session's own 16:30, so that session is visible and the next one not."""
    return session_publication_instant(panel.sessions[index]) + timedelta(hours=1)


def _build(
    store: PanelStore,
    panel: GeneratedPanel,
    definition: object,
    as_of: datetime,
) -> FactorPanel:
    return compute_factor(
        store,
        definition,  # type: ignore[arg-type]
        as_of=as_of,
        subjects=panel.securities,
        universe=frozenset(panel.securities),
        requirements=_requirements(panel, as_of),  # type: ignore[arg-type]
        code_commit="a1b2c3d",
        built_at=as_of,
    )


def _by_subject(panel: FactorPanel) -> dict[str, FactorObservation]:
    return {observation.subject: observation for observation in panel.observations}


def _events_for(
    store: PanelStore,
    panel: GeneratedPanel,
    code: str,
    sessions: tuple[date, ...],
    *,
    as_of: datetime,
) -> tuple[tuple[date, str], ...]:
    """`session_limit_events`' events for `code` over `sessions`, on the same panel at `as_of`.

    The bars and bands are read through the loaders that own them -- the same doors
    `panel_limit_events.limit_events_on` uses -- and the domain rule is called directly, so this
    is `V2-P7-011`'s answer rather than a second reading of `limit_touch` written here.
    """
    calendar = panel.calendar()
    bars = {
        day: load_daily_bars(store, day=day, calendar=calendar, as_of=as_of, max_staleness=None)
        for day in sessions
    }
    limits = {
        day: load_price_limits(store, day=day, calendar=calendar, as_of=as_of, max_staleness=None)
        for day in sessions
    }
    states: dict[date, SessionLimitState] = {
        day: session_state(bars[day], limits[day], trade_date=day) for day in sessions
    }
    events: list[tuple[date, str]] = []
    for index, day in enumerate(sessions):
        earlier = tuple(states[prior] for prior in reversed(sessions[:index]))
        for event in session_limit_events(states[day], bars[day], earlier):
            if event.ts_code == code:
                events.append((day, event.kind))
    return tuple(events)


def _kinds(events: tuple[tuple[date, str], ...]) -> list[str]:
    return [kind for _, kind in events]


def _bands_restated(
    panel: GeneratedPanel,
    *,
    dropped: frozenset[tuple[str, str]] = frozenset(),
    overrides: dict[tuple[str, str], dict[str, object]] | None = None,
) -> GeneratedPanel:
    """`panel` with named `stk_limit` rows dropped and named cells replaced."""
    batches = dict(panel.batches)
    batches[PRICE_LIMIT_DATASET] = _arrayed_rows(
        panel.batch(PRICE_LIMIT_DATASET),
        date_column="trade_date",
        drops=dropped,
        overrides=overrides or {},
    )
    return replace(panel, batches=batches)


def _close_of(panel: GeneratedPanel, code: str, day: date) -> float:
    """One stored `daily.close`, looked up by `(subject, date)` rather than by row position."""
    batch = panel.batch(DAILY_DATASET)
    dates = panel.column(DAILY_DATASET, "trade_date")
    closes = panel.column(DAILY_DATASET, "close")
    for subject, day_text, close in zip(batch.subjects, dates, closes, strict=True):
        if subject == code and day_text == day.isoformat():
            return float(close)
    raise AssertionError(f"no stored bar for {code} on {day.isoformat()}")


def _halted_over_the_whole_read(panel: GeneratedPanel, code: str) -> GeneratedPanel:
    """`panel` with **every** bar and valuation of `code` dropped and a halt row put in each place.

    The shape the real panel has for a security suspended for months: `stk_limit` still publishes
    its band every session (`domain/price_limits.py` measures 26 halted A shares on 2024-06-28),
    `daily` has no bar, and `suspend_d` explains every absent bar -- the price writer's own
    explained-thin-session guard refuses the fixture otherwise.
    """
    batches = dict(panel.batches)
    dropped = frozenset((code, day.isoformat()) for day in panel.sessions)
    for dataset in (DAILY_DATASET, DAILY_BASIC_DATASET):
        batches[dataset] = _arrayed_rows(
            panel.batch(dataset), date_column="trade_date", drops=dropped
        )
    suspensions = panel.batch(SUSPENSION_DATASET)
    for day in panel.sessions:
        suspensions = _with_a_halt(suspensions, code=code, day=day)
    batches[SUSPENSION_DATASET] = suspensions
    return replace(panel, batches=batches)


def _copy_store(store: PanelStore, target: Path) -> PanelStore:
    """A byte copy of a written store, so two builds can run from identical inputs."""
    shutil.copytree(store.root, target)
    return PanelStore(target)


def _decision_batch(
    panel: GeneratedPanel, code: str, *, session: date, previous: date
) -> ColumnarPanelBatch:
    """One `V2-P6-020` record saying `session`'s return is unknowable, on this panel's own rows.

    Kind `pre_close_contradicts_adj_factor` -- neither the published `pre_close` nor the factor
    path is corroborated, so `recorded_return_paths` gives `path=None` and a window that reads a
    session return abstains. The two closes are the stored ones, which is what makes the record
    match the rows a reader holds (`_decided_series`' rule).
    """
    values: dict[str, object] = {
        "trade_date": session.isoformat(),
        "source_dataset": "stk_limit",
        "defect_kind": "pre_close_contradicts_adj_factor",
        "bar_close": _close_of(panel, code, session),
        "valuation_close": _close_of(panel, code, previous) * 0.8,
        "previous_bar_close": _close_of(panel, code, previous),
        "up_limit": None,
        "down_limit": None,
        "valuation_repeats_previous_close": None,
        "list_date": None,
    }
    kinds = {
        "trade_date": "string",
        "source_dataset": "string",
        "defect_kind": "string",
        "valuation_repeats_previous_close": "boolean",
        "list_date": "string",
    }
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=UPSTREAM_DEFECTS_DATASET,
        kind=UPSTREAM_DEFECTS_DATASET,
        as_of=panel.as_of,
        fetched_at=panel.as_of,
        status="success",
        subjects=(code,),
        timeline=TimelineColumns(
            event_time=(_close_time(session),),
            available_time=(_published_time(session),),
            ingested_time=(_published_time(session),),
            revision_time=(_published_time(session),),
        ),
        columns=tuple(
            PanelColumn(name, kinds.get(name, "float"), (values[name],))  # type: ignore[arg-type]
            for name in UPSTREAM_DEFECT_DATA_COLUMNS
        ),
    )


def _request(panel: GeneratedPanel, key: str, as_ofs: tuple[datetime, ...]) -> FactorBuildRequest:
    """A `factor build` request for one factor at `as_ofs`, raw and processed tiers."""
    return factor_build_request(
        factor=key,
        tier="processed",
        transform="cross_section_standard/v1",
        neutralization="",
        as_ofs=list(as_ofs),
        years=[YEAR],
        exchange=panel.exchange,
        max_staleness_days=30,
        waive_max_staleness=False,
        subjects=[],
        supersedes_raw=[],
        supersedes_processed=[],
        supersedes_neutralized=[],
        code_commit="a1b2c3d",
    )


# --- 1. the counts are V2-P7-011's counts ---------------------------------------------------------


def test_the_counts_are_the_events_session_limit_events_produces_on_the_same_panel(
    tmp_path: Path,
) -> None:
    """The acceptance `V2-P7-028` inherits from `V2-P7-011`: one fixture, two readers agree.

    The scripted security walks the hand-labelled eight-session table (five closed-at-limit
    sessions and one touched-not-closed), and the expected numbers are that producer's own events
    over the factor's window -- so a second implementation of "where a bar sat against its band"
    would have to reproduce `limit_touch` on all eight sessions to pass.
    """
    panel = _panel()
    store = _store(tmp_path, panel)
    as_of = _published(panel, WINDOW_END_INDEX)
    code = panel.securities[LIMIT_STREAK_SECURITY_INDEX]

    limit_up = _by_subject(_build(store, panel, LIMIT_UP_COUNT_20, as_of))[code]
    broken = _by_subject(_build(store, panel, BROKEN_BOARD_COUNT_20, as_of))[code]

    assert limit_up.coverage == "computed"
    assert broken.coverage == "computed"
    session_first, session_last = limit_up.input_session_first, limit_up.input_session_last
    assert session_first is not None and session_last is not None
    events = _events_for(
        store,
        panel,
        code,
        tuple(day for day in panel.sessions if session_first <= day <= session_last),
        as_of=as_of,
    )
    kinds = _kinds(events)

    assert limit_up.value == float(kinds.count("limit_up") + kinds.count("consecutive_board"))
    assert broken.value == float(kinds.count("broken_board"))
    # Non-vacuous: the script was chosen to carry both kinds on this window.
    assert (limit_up.value, broken.value) == (5.0, 1.0)


# --- 2. a halt and a bandless session are skipped, not counted ------------------------------------


def test_a_halted_security_is_computed_and_the_halt_contributes_no_session(
    tmp_path: Path,
) -> None:
    """The halt is not a window point, not a zero, and not `input_missing`.

    Unioning the two datasets' points would make every security with a halt inside its last
    twenty sessions `input_missing` -- the loud code for a published fact. With the window formed
    from the sessions both datasets cover, the halt is skipped, the window reaches one session
    further back, and the span stays inside `max_window_sessions`. Both halted names on this
    fixture are asserted, because the fixture carries one halt the price generator adds and one
    the limit-streak script adds.
    """
    panel = _panel()
    store = _store(tmp_path, panel)
    as_of = _published(panel, WINDOW_END_INDEX)
    result = _build(store, panel, LIMIT_UP_COUNT_20, as_of)

    census = dict(result.coverage_census())
    assert census["input_missing"] == 0
    assert census["computed"] == len(panel.securities)

    observations = _by_subject(result)
    halts = {
        panel.securities[LIMIT_STREAK_SECURITY_INDEX]: panel.sessions[
            LIMIT_STREAK_FIRST_SESSION_INDEX + 2
        ],
        panel.securities[-1]: panel.sessions[4],
    }
    for code, halt in halts.items():
        observation = observations[code]
        assert observation.coverage == "computed"
        assert observation.input_session_first is not None
        assert observation.input_session_last is not None
        # The skipped session sits inside the window's span, so the window reached across it
        # rather than ending before it.
        assert observation.input_session_first < halt < observation.input_session_last
        assert observation.value is not None

    scripted = observations[panel.securities[LIMIT_STREAK_SECURITY_INDEX]]
    assert scripted.input_session_first == panel.sessions[1]
    assert scripted.input_session_last == panel.sessions[WINDOW_END_INDEX]
    assert scripted.value == pytest.approx(5.0)


def test_a_session_with_no_published_band_is_skipped_and_never_a_touch(
    tmp_path: Path,
) -> None:
    """Dropping the band on a closed-at-limit session changes the count and stretches the window.

    The session is one the *unmodified* fixture counts (its close is its band), so this is not
    "an ordinary session happens to be outside the window": the band's absence is what removes it,
    the window reaches one session further back, and the span is still inside the declaration.
    """
    panel = _panel()
    code = panel.securities[LIMIT_STREAK_SECURITY_INDEX]
    dropped_day = panel.sessions[LIMIT_STREAK_FIRST_SESSION_INDEX + 5]  # d6, a limit-up session
    panel = _bands_restated(panel, dropped=frozenset({(code, dropped_day.isoformat())}))
    store = _store(tmp_path, panel)
    as_of = _published(panel, WINDOW_END_INDEX)

    observation = _by_subject(_build(store, panel, LIMIT_UP_COUNT_20, as_of))[code]
    sibling = _by_subject(_build(store, panel, BROKEN_BOARD_COUNT_20, as_of))[code]

    assert observation.coverage == "computed"
    assert observation.input_session_first == panel.sessions[0]
    assert observation.input_session_last == panel.sessions[WINDOW_END_INDEX]
    assert observation.value == pytest.approx(4.0)
    assert sibling.value == pytest.approx(1.0)

    # The skipped session is a closed-at-limit session on the unmodified fixture: the script's
    # close is its published band, which is why dropping the band row removes a count.
    batch = panel.batch(DAILY_DATASET)
    dates = panel.column(DAILY_DATASET, "trade_date")
    closes = panel.column(DAILY_DATASET, "close")
    pre_closes = panel.column(DAILY_DATASET, "pre_close")
    matching = [
        index
        for index, (subject, day_text) in enumerate(zip(batch.subjects, dates, strict=True))
        if subject == code and day_text == dropped_day.isoformat()
    ]
    assert len(matching) == 1
    assert float(closes[matching[0]]) >= round(float(pre_closes[matching[0]]) * 1.1, 2)


def test_a_limit_free_sentinel_band_is_a_window_session_but_not_a_touch(tmp_path: Path) -> None:
    """The sentinel is a published row, so it counts as one of the twenty and contributes nothing.

    The same closed-at-limit session as the bandless test, with its band replaced by the Beijing
    board's no-limit sentinel instead of dropped. The count is the same as the bandless case and
    the window is **not** stretched -- which is the whole difference between "the exchange
    published no limit" and "the exchange published nothing": `limit_touch` already reports the
    sentinel as no touch, and the engine already has the session as a window point.
    """
    panel = _panel()
    code = panel.securities[LIMIT_STREAK_SECURITY_INDEX]
    sentinel_day = panel.sessions[LIMIT_STREAK_FIRST_SESSION_INDEX + 5]  # d6
    panel = _bands_restated(
        panel,
        overrides={
            (code, sentinel_day.isoformat()): {
                "up_limit": SENTINEL_UP_LIMIT,
                "down_limit": SENTINEL_DOWN_LIMIT,
            }
        },
    )
    store = _store(tmp_path, panel)
    as_of = _published(panel, WINDOW_END_INDEX)

    observation = _by_subject(_build(store, panel, LIMIT_UP_COUNT_20, as_of))[code]

    assert observation.coverage == "computed"
    assert observation.input_session_first == panel.sessions[1]
    assert observation.input_session_last == panel.sessions[WINDOW_END_INDEX]
    assert observation.value == pytest.approx(4.0)


# --- 3. a window stretched past its declared span is refused --------------------------------------


def test_a_window_stretched_past_the_declared_span_is_insufficient_history(tmp_path: Path) -> None:
    """Five bandless sessions leave twenty classifiable sessions spanning twenty-five.

    The count is exactly the reach, so this cannot be the too-few-points refusal: what overruns is
    the **span**, and the observation records the window it was refused for -- the distinction
    `max_window_sessions` exists to make and `V2-P7-042`'s halt windows are the precedent for.
    The rest of the cross section computes, so the refusal is attributable to the five skipped
    sessions rather than to the build.
    """
    panel = _panel()
    victim = panel.securities[0]
    skipped = frozenset((victim, panel.sessions[index].isoformat()) for index in range(5, 10))
    panel = _bands_restated(panel, dropped=skipped)
    store = _store(tmp_path, panel)
    as_of = _published(panel, SPAN_END_INDEX)

    result = _build(store, panel, LIMIT_UP_COUNT_20, as_of)
    observations = _by_subject(result)

    classifiable = [
        day
        for day in panel.sessions[: SPAN_END_INDEX + 1]
        if (victim, day.isoformat()) not in skipped
    ]
    assert len(classifiable) == 20, "the fixture must leave exactly the declared reach"
    assert victim in {item.subject for item in result.observations}

    refused = observations[victim]
    assert refused.coverage == "insufficient_history"
    assert refused.value is None
    assert refused.input_session_first == panel.sessions[0]
    assert refused.input_session_last == panel.sessions[SPAN_END_INDEX]
    assert dict(result.coverage_census())["insufficient_history"] == 1
    assert observations[panel.securities[LIMIT_STREAK_SECURITY_INDEX]].coverage == "computed"


# --- 4. nothing classifiable is insufficient_history, not input_missing -------------------------


def test_a_security_halted_over_the_whole_read_is_insufficient_history(tmp_path: Path) -> None:
    """Bands for every session and no bar in any of them: no session is classifiable.

    This is the real shape of a long suspension -- the exchange keeps publishing the band -- and
    the first draft's fallback turned it into `input_missing` by forming a band-only window that
    `_complete_series` could not fill. `input_missing` is the code reserved for a short read, so
    the census could not tell a fetch hole from a halt; the honest answer is
    `insufficient_history`, which is what a daily-only factor answers for the same security. It
    also has no window to record, unlike the span overrun above.
    """
    panel = _panel()
    victim = panel.securities[0]
    panel = _halted_over_the_whole_read(panel, victim)
    store = _store(tmp_path, panel)
    as_of = _published(panel, WINDOW_END_INDEX)

    result = _build(store, panel, LIMIT_UP_COUNT_20, as_of)
    observation = _by_subject(result)[victim]

    assert observation.coverage == "insufficient_history"
    assert observation.value is None
    assert observation.input_session_first is None
    assert observation.input_session_last is None
    assert dict(result.coverage_census())["input_missing"] == 0


def test_a_security_with_bars_but_no_band_rows_at_all_is_insufficient_history(
    tmp_path: Path,
) -> None:
    """Every bar present, every `stk_limit` row for the security gone: nothing to classify against.

    The other half of the empty-window rule, and the one a "union the datasets' points" reading
    gets wrong in the quiet direction: the bars alone would form a window that then has no band on
    any session, and the observation would be `input_missing`. The factor needs the *pair* on a
    session, so the intersection is empty and the answer is `insufficient_history`.
    """
    panel = _panel()
    victim = panel.securities[0]
    dropped = frozenset((victim, day.isoformat()) for day in panel.sessions)
    panel = _bands_restated(panel, dropped=dropped)
    store = _store(tmp_path, panel)
    as_of = _published(panel, WINDOW_END_INDEX)

    observation = _by_subject(_build(store, panel, LIMIT_UP_COUNT_20, as_of))[victim]

    assert observation.coverage == "insufficient_history"
    assert observation.value is None
    assert observation.input_session_first is None
    assert observation.input_session_last is None


# --- 5. a return-path decision is not this family's --------------------------------------------


def test_a_recorded_unknowable_return_session_leaves_the_count_computed(tmp_path: Path) -> None:
    """`session_return_links` excludes a band-reading close, and this is what that buys.

    The record says one session inside the window has an unknowable *return*. A factor that read
    session returns would abstain (`undefined_value`) on it; this family compares the session's own
    close to its own published band, which the disagreement does not change -- and `V2-P7-011`'s
    events still answer the session. So the observation stays `computed` with the band-derived
    count, and no abstention is reported. Without the `session_return_links` branch this is
    `undefined_value`, which is why the test is behavioural rather than a declaration check.
    """
    panel = _panel()
    store = _store(tmp_path, panel)
    code = panel.securities[LIMIT_STREAK_SECURITY_INDEX]
    defect_day = panel.sessions[WINDOW_END_INDEX - 5]
    previous_day = panel.sessions[WINDOW_END_INDEX - 6]
    as_of = _published(panel, WINDOW_END_INDEX)

    # The defect is on a session the scripted security has a bar on, so it is a window point.
    write_upstream_defects(
        store,
        _decision_batch(panel, code, session=defect_day, previous=previous_day),
        year=YEAR,
        source_datasets=frozenset({"stk_limit"}),
    )

    result = _build(store, panel, LIMIT_UP_COUNT_20, as_of)
    observation = _by_subject(result)[code]

    assert observation.coverage == "computed"
    assert observation.value == pytest.approx(5.0)
    assert result.unknowable_return_sessions == ()
    assert observation.input_session_first is not None
    assert observation.input_session_last is not None
    assert observation.input_session_first < defect_day < observation.input_session_last


# --- 6. the family through `factor build`'s own path ------------------------------------------


def _census_sum(reports: tuple[FactorBuildReport, ...], tier: str) -> Counter[str]:
    total: Counter[str] = Counter()
    for report in reports:
        total.update(report.coverage[tier])
    return total


def test_the_family_builds_through_the_factor_build_face_carried_and_fresh(
    tmp_path: Path,
) -> None:
    """Both factors, raw and processed, two instants: carried build == two fresh single builds.

    This is the path the controller's real factor build runs -- `REQUIREMENT_BUILDERS`'
    `stk_limit` row, `_CALENDAR_SCOPED_REQUIREMENTS`, the build's one `FactorReadCarry` across
    instants -- and the read-path pin suites exclude these two factors because their corpora carry
    no band partition. The identities are `manifest_id`s, so the equality is about what was
    stored rather than about what a report printed.
    """
    panel = _panel()
    base = PanelStore(tmp_path / "panel")
    write_generated_panel(base, panel)
    first = _published(panel, WINDOW_END_INDEX)
    second = _published(panel, SPAN_END_INDEX)
    built_at = second + timedelta(days=1)

    for position, key in enumerate(
        (LIMIT_UP_COUNT_20.qualified_key, BROKEN_BOARD_COUNT_20.qualified_key)
    ):
        carried_store = _copy_store(base, tmp_path / f"carried_{position}")
        carried = build_factor_panels(
            carried_store, _request(panel, key, (first, second)), built_at=built_at
        )

        fresh: list[FactorBuildReport] = []
        for instant_position, instant in enumerate((first, second)):
            fresh_store = _copy_store(base, tmp_path / f"fresh_{position}_{instant_position}")
            fresh.append(
                build_factor_panels(
                    fresh_store, _request(panel, key, (instant,)), built_at=built_at
                )
            )

        for tier in ("raw", "processed"):
            assert len(carried.manifest_ids[tier]) == 2, (key, tier)
            assert set(carried.manifest_ids[tier]) == {
                report.manifest_ids[tier][0] for report in fresh
            }, (key, tier)
            assert Counter(carried.coverage[tier]) == _census_sum(tuple(fresh), tier), (key, tier)
        assert carried.partitions
