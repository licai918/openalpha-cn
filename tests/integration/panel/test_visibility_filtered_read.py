"""The visibility-filtered read, against a real store (`V2-P3-002`, roadmap section 11).

`tests/integration/panel/test_lookahead_injection.py` proved that the partition-level gate's
refusals are *attributable* -- inject a row the read cannot know about, and the loader refuses
because of that row. This file proves the other half of the same seam: that the read which does
**not** refuse withholds exactly those rows and says how many.

## The pairing is the method here too

An assertion that "the filtered read answered" proves nothing on its own -- a read that returned
the whole partition would also answer. Every test here therefore pins **both** numbers over one
partition at one `as_of`: which sessions came back, and how many rows did not. The two are then
reconciled against the partition's own row count, so a predicate that silently stopped filtering
would break the first pair and a count that stopped counting would break the second.

The frame is the generated panel: ten sessions from 2026-01-05 to 2026-01-16, `daily`
availability at 16:30 Asia/Shanghai (08:30 UTC) on each session's own date, one partition, one
year. `MID_WINDOW` is noon Asia/Shanghai on 2026-01-12, which is the shape roadmap section 11 is
about in miniature -- the partition's `max_available_time` is on 2026-01-16, so `read_if_ready`
refuses the whole of it, and every one of the five sessions before the read is inside it.

## The second gate, and the pairing it needs

`V2-P3-002`'s review found that pinning both numbers over the *returned* rows is still not
enough, because it says nothing about the checks the rule table cleared on evidence the filter
then removed. `stale` was the extreme case: it compares `as_of` against the whole partition's
newest event, so on the read this method exists for it is arithmetically unable to fire, and
three declared bounds (one hour, one day, two days) all answered against a slice 2 days 21 hours
short. The tests below therefore pin a **third** thing wherever a scope-sensitive check is in
play: what the check decided over the visible rows, paired against the same read with the check
waived, so a refusal is attributable to the bound rather than to the fixture.

## What this file does not claim

That the filtered read reconstructs what a fetch made on 2026-01-12 would have returned. It
reconstructs what the *stored* partition says was knowable then, which is a weaker and
different thing wherever the upstream restates or re-scopes what it serves. That is disclosed as
`KNOWN_STORAGE_LIMITATIONS.
a_visibility_filtered_read_replays_a_partition_that_was_not_there_yet` and asserted as a
disclosure in `tests/unit/test_panel_doctor_rules.py`, not papered over here.

Nor that every scope-sensitive check is re-decided. `date_gap` is not, and the measurement that
makes the exclusion affordable is asserted rather than quoted: see
`test_the_date_gap_recheck_would_be_a_no_op_on_every_requirement_that_states_dates`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from panel_fixtures import AS_OF, YEAR, generate_panel, write_generated_panel

from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import (
    KNOWN_STORAGE_LIMITATIONS,
    READINESS_ISSUE_CODES,
    ROW_FILTERABLE_ISSUE_CODES,
    SCOPE_SENSITIVE_ISSUE_CODES,
    VISIBLE_SLICE_RECHECKS,
    DateCoverage,
    FieldCoverage,
    PanelReadOutcome,
    PanelVisibleReadOutcome,
    PartitionCoverage,
    ReadinessRequirement,
    evaluate_visible_slice,
)
from openalpha_cn.panel.store import (
    AVAILABILITY_COLUMN,
    EVENT_TIME_COLUMN,
    SUBJECT_COLUMN,
    ColumnSpec,
    PanelStorageError,
    PanelStore,
)
from openalpha_cn.panel_ingest import daily_requirement, write_panel_batch

MID_WINDOW = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on the sixth session. Five sessions are knowable, five are not."""

VISIBLE_SESSIONS = (
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),
)

WITHHELD_SESSIONS = (
    date(2026, 1, 12),
    date(2026, 1, 13),
    date(2026, 1, 14),
    date(2026, 1, 15),
    date(2026, 1, 16),
)

PROJECTION = ("subject", "event_time", "close")


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    """A store holding the shapeless generated panel's `daily` partition and its neighbours."""
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, generate_panel())
    return built


def _requirement(as_of: datetime) -> ReadinessRequirement:
    panel = generate_panel()
    return daily_requirement(panel.calendar(), years=(YEAR,), as_of=as_of, max_staleness=None)


def _sessions_of(rows: tuple[tuple[object, ...], ...]) -> tuple[date, ...]:
    return tuple(
        sorted({instant.astimezone(UTC).date() for _, instant, _ in rows})  # type: ignore[union-attr]
    )


def test_the_gated_read_refuses_the_whole_year_and_the_filtered_read_does_not(
    store: PanelStore,
) -> None:
    """Roadmap section 11's constraint, and the exit from it, over one partition at one `as_of`.

    Both halves are asserted together because either alone is misleading. That `read_if_ready`
    blocks is the *premise* of this issue -- without it the second method has no reason to
    exist -- and it blocks on exactly one code, which is what makes substituting a row-level
    predicate for it a bounded change rather than a weakening.
    """
    requirement = _requirement(MID_WINDOW)

    gated = store.read_if_ready(requirement, year=YEAR, columns=PROJECTION)
    filtered = store.read_visible_at(requirement, year=YEAR, columns=PROJECTION)

    assert gated.is_blocked
    assert {issue.code for issue in gated.readiness.issues} == {"not_yet_knowable"}
    assert not filtered.is_blocked
    assert _sessions_of(filtered.rows) == VISIBLE_SESSIONS


def test_the_filtered_read_withholds_the_sessions_that_had_not_published(
    store: PanelStore,
) -> None:
    """The magnitude, not just the fact. Five sessions in, five out, and the counts add up.

    The last assertion is the one that would survive a predicate that stopped filtering: with
    `visible + withheld` pinned to the partition's own row count, a read that returned
    everything would have to make `withheld_row_count` zero at the same time, and a count that
    stopped counting would have to shrink the rows to match.
    """
    requirement = _requirement(MID_WINDOW)
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None

    filtered = store.read_visible_at(requirement, year=YEAR, columns=PROJECTION)

    assert _sessions_of(filtered.rows) == VISIBLE_SESSIONS
    assert set(_sessions_of(filtered.rows)) & set(WITHHELD_SESSIONS) == set()
    assert filtered.withheld_row_count > 0
    assert filtered.visible_row_count + filtered.withheld_row_count == coverage.row_count


def test_the_boundary_is_the_availability_instant_itself(store: PanelStore) -> None:
    """A session opens at its own `available_time` and is withheld one microsecond earlier.

    The read-side counterpart of the `<=` boundary `test_columnar_batch_parity.py` pins on the
    write side, and the reason it is asserted rather than assumed: an off-by-one in the
    comparison is invisible to every test that reads a whole day away from the edge.

    **The complement is checked here and not only at `MID_WINDOW`**, and a mutant is why. The
    two statements have to partition the rows exactly -- `<=` on one side, `>` on the other --
    and a withheld count written `>=` double-counts precisely the rows that sit *on* the
    boundary. `MID_WINDOW` has none of those (the sessions publish at 08:30 UTC and it is
    04:00), so the arithmetic there is blind to it: with the count mutated to `>=`, every
    assertion in this file passed except this one.
    """
    opens = datetime(2026, 1, 12, 8, 30, tzinfo=UTC)
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None

    just_before = store.read_visible_at(
        _requirement(opens - timedelta(microseconds=1)),
        year=YEAR,
        columns=PROJECTION,
    )
    exactly_at = store.read_visible_at(_requirement(opens), year=YEAR, columns=PROJECTION)

    assert date(2026, 1, 12) not in _sessions_of(just_before.rows)
    assert date(2026, 1, 12) in _sessions_of(exactly_at.rows)
    assert exactly_at.visible_row_count > just_before.visible_row_count
    assert exactly_at.visible_row_count + exactly_at.withheld_row_count == coverage.row_count
    assert just_before.visible_row_count + just_before.withheld_row_count == coverage.row_count


def test_a_filter_narrows_both_halves_to_the_rows_the_caller_asked_about(
    store: PanelStore,
) -> None:
    """`withheld_row_count` is about the caller's own selection, not about the partition.

    A count taken without the equality filters would report how many rows of the whole
    partition were withheld -- eight securities' worth rather than one's -- which reads as a
    much bigger hole than the caller has. The two halves have to be filtered identically, which
    is why `_equality_clauses` is shared between them.
    """
    requirement = _requirement(MID_WINDOW)

    everything = store.read_visible_at(requirement, year=YEAR, columns=PROJECTION)
    one_name = store.read_visible_at(
        requirement,
        year=YEAR,
        columns=PROJECTION,
        filters={"subject": "000001.SZ"},
    )

    assert {row[0] for row in one_name.rows} == {"000001.SZ"}
    assert one_name.visible_row_count == len(VISIBLE_SESSIONS)
    assert one_name.withheld_row_count == len(WITHHELD_SESSIONS)
    assert one_name.withheld_row_count < everything.withheld_row_count


def test_any_issue_other_than_the_filterable_one_still_blocks_the_whole_partition(
    store: PanelStore,
) -> None:
    """The substitution is bounded to one code, and a second issue restores the refusal.

    Injected by requiring a session the partition does not hold -- a `date_gap`, which no row
    predicate can repair, since withholding more rows cannot supply a missing one. The pair is
    over one partition at one `as_of`: the same read without the invented requirement answers,
    so the refusal is attributable to the added issue and not to the granularity.
    """
    sound = _requirement(MID_WINDOW)
    holed = replace(sound, required_dates=(*(sound.required_dates or ()), date(2026, 1, 2)))

    answered = store.read_visible_at(sound, year=YEAR, columns=PROJECTION)
    refused = store.read_visible_at(holed, year=YEAR, columns=PROJECTION)

    assert not answered.is_blocked
    assert refused.is_blocked
    assert {issue.code for issue in refused.readiness.issues} == {"date_gap", "not_yet_knowable"}


def test_a_blocked_filtered_read_refuses_to_answer_either_of_its_two_questions(
    store: PanelStore,
) -> None:
    """Neither `rows` nor `withheld_row_count` has a value when nothing was read.

    `0` would be the one number a caller must not be told: a blocked read withheld everything.
    The merged shapes stay reachable under names that say what they are, which is
    `PanelReadOutcome`'s own arrangement.
    """
    sound = _requirement(MID_WINDOW)
    holed = replace(sound, required_dates=(*(sound.required_dates or ()), date(2026, 1, 2)))

    refused = store.read_visible_at(holed, year=YEAR, columns=PROJECTION)

    with pytest.raises(PanelStorageError, match="no visible rows to read"):
        _ = refused.rows
    with pytest.raises(PanelStorageError, match="has no answer"):
        _ = refused.withheld_row_count
    assert refused.rows_or_none is None
    assert refused.withheld_row_count_or_none is None


def test_the_outcome_refuses_truthiness_length_and_iteration_on_both_paths(
    store: PanelStore,
) -> None:
    """`if outcome:` is the most available way to write the check and is true of a blocked one.

    `DependencyClearance` set this precedent and refuses all three on the *clearing* path as
    well, for the reason that matters: a guard that only rejects the failing case teaches
    nothing on the passing case. So both outcomes are driven through all three dunders.
    """
    sound = _requirement(MID_WINDOW)
    holed = replace(sound, required_dates=(*(sound.required_dates or ()), date(2026, 1, 2)))

    for outcome in (
        store.read_visible_at(sound, year=YEAR, columns=PROJECTION),
        store.read_visible_at(holed, year=YEAR, columns=PROJECTION),
    ):
        with pytest.raises(PanelStorageError, match="no truth value"):
            bool(outcome)
        with pytest.raises(PanelStorageError, match="no length"):
            len(outcome)
        with pytest.raises(PanelStorageError, match="not iterable"):
            list(outcome)  # type: ignore[call-overload]


def test_the_predicate_runs_even_when_the_verdict_did_not_ask_for_it(
    store: PanelStore,
) -> None:
    """At an `as_of` past the whole partition the readiness verdict is clean, and the filter is
    still in the statement.

    This is the difference between a filter and a hope. If the predicate were added only when
    the verdict said `not_yet_knowable`, the point-in-time guarantee would rest on a judgement
    computed from catalog metadata -- one number per partition -- rather than on the rows. The
    observable consequence at a clean `as_of` is that `withheld_row_count` is `0` and every row
    comes back, which is what a predicate that removes nothing looks like.
    """
    requirement = _requirement(AS_OF)
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None

    filtered = store.read_visible_at(requirement, year=YEAR, columns=PROJECTION)

    assert filtered.readiness.state == "ready"
    assert filtered.withheld_row_count == 0
    assert filtered.visible_row_count == coverage.row_count


def test_the_availability_column_need_not_be_projected_for_the_filter_to_apply(
    store: PanelStore,
) -> None:
    """A caller that does not select `available_time` is still filtered by it.

    The predicate is applied in SQL over the stored column, not against a projected value, so
    the guarantee cannot be switched off by narrowing the projection -- which is exactly how a
    caller-side re-filter would have been switchable.
    """
    requirement = _requirement(MID_WINDOW)

    narrow = store.read_visible_at(requirement, year=YEAR, columns=("subject", "event_time"))
    wide = store.read_visible_at(
        requirement,
        year=YEAR,
        columns=("subject", "event_time", AVAILABILITY_COLUMN),
    )

    assert AVAILABILITY_COLUMN not in ("subject", "event_time")
    assert narrow.visible_row_count == wide.visible_row_count
    assert narrow.withheld_row_count == wide.withheld_row_count
    assert all(
        instant.astimezone(UTC) <= MID_WINDOW  # type: ignore[union-attr]
        for _, _, instant in wide.rows
    )


def test_a_filtered_read_names_the_refusal_its_predicate_answered(store: PanelStore) -> None:
    """`readiness.state` says `blocked` on an outcome that carries rows, and that is the design.

    The verdict is `read_if_ready`'s, kept verbatim, so the record of *which* refusal was
    compensated survives; `is_blocked` is this outcome's own answer and
    `compensated_issue_codes` names the difference. The pair is asserted at two `as_of`s
    because the interesting half is the second: at an `as_of` past the whole partition nothing
    was compensated, and a property that answered "not_yet_knowable" there would be reporting a
    refusal that never happened.
    """
    filtered = store.read_visible_at(_requirement(MID_WINDOW), year=YEAR, columns=PROJECTION)
    clean = store.read_visible_at(_requirement(AS_OF), year=YEAR, columns=PROJECTION)

    assert filtered.readiness.state == "blocked"
    assert not filtered.is_blocked
    assert filtered.compensated_issue_codes == ("not_yet_knowable",)
    assert clean.readiness.state == "ready"
    assert clean.compensated_issue_codes == ()


def test_a_blocked_outcome_compensated_nothing(store: PanelStore) -> None:
    """The third case, which the two above do not cover: a refusal answers no codes at all,
    because it answered nothing. Reporting its issue list here would read as "these were
    compensated" for a read that returned no rows."""
    sound = _requirement(MID_WINDOW)
    holed = replace(sound, required_dates=(*(sound.required_dates or ()), date(2026, 1, 2)))

    refused = store.read_visible_at(holed, year=YEAR, columns=PROJECTION)

    assert refused.is_blocked
    assert refused.compensated_issue_codes == ()


def test_the_filterable_code_set_is_a_strict_subset_of_the_readiness_vocabulary() -> None:
    """A guard against the constant quietly becoming "everything".

    `ROW_FILTERABLE_ISSUE_CODES` decides which refusals a row predicate may replace. If it ever
    equalled `READINESS_ISSUE_CODES`, `read_visible_at` would answer over a partition whose file
    is missing, whose coverage is stale or whose required fields are absent -- and every
    assertion in this file would still pass, because none of them names a code it does not
    contain.
    """
    assert ROW_FILTERABLE_ISSUE_CODES < READINESS_ISSUE_CODES
    assert {"not_yet_knowable"} == ROW_FILTERABLE_ISSUE_CODES


# --- V2-P3-002 review: the scope-sensitive checks, the reach, and the NULL clock ---------------


def _bounded(as_of: datetime, bound: timedelta) -> ReadinessRequirement:
    """The same requirement with a freshness bound stated instead of waived."""
    return replace(_requirement(as_of), max_staleness=bound)


def _probe_requirement(
    dataset: str,
    as_of: datetime,
    *,
    subjects: tuple[str, ...] | None,
    bound: timedelta | None = None,
) -> ReadinessRequirement:
    return ReadinessRequirement(
        dataset=dataset,
        as_of=as_of,
        years=(YEAR,),
        required_dates=None,
        required_subjects=subjects,
        required_fields=(SUBJECT_COLUMN,),
        max_staleness=bound,
    )


def _write_probe(
    store: PanelStore,
    *,
    dataset: str,
    subjects: tuple[str, ...],
    event: tuple[datetime, ...],
    available: tuple[datetime, ...],
) -> None:
    """A hand-written partition through the real batch writer, so every guard is live."""
    written = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
    batch = ColumnarPanelBatch(
        provider_id="synthetic",
        dataset=dataset,
        kind="probe",
        as_of=written,
        fetched_at=written,
        status="success",
        subjects=subjects,
        timeline=TimelineColumns(
            event_time=event,
            available_time=available,
            ingested_time=available,
            revision_time=available,
        ),
        columns=(PanelColumn("score", "float", tuple(float(i) for i in range(len(subjects)))),),
    )
    write_panel_batch(store, batch, year=YEAR)


def _write_late_subject_probe(store: PanelStore, *, dataset: str, late_name: str) -> None:
    """A partition where `late_name` publishes only on a session after `MID_WINDOW`."""
    _write_probe(
        store,
        dataset=dataset,
        subjects=("000002.SZ", late_name),
        event=(datetime(2026, 1, 6, 7, 0, tzinfo=UTC), datetime(2026, 1, 15, 7, 0, tzinfo=UTC)),
        available=(
            datetime(2026, 1, 6, 8, 30, tzinfo=UTC),
            datetime(2026, 1, 15, 8, 30, tzinfo=UTC),
        ),
    )


def _write_reach_probe(store: PanelStore, *, dataset: str) -> None:
    """The review's fourteen rows: ten January, four December, two securities."""
    subjects: list[str] = []
    event: list[datetime] = []
    available: list[datetime] = []
    for name in ("000001.SZ", "000002.SZ"):
        for day in (5, 6, 7, 8, 9):
            subjects.append(name)
            event.append(datetime(2026, 1, day, 7, 0, tzinfo=UTC))
            available.append(datetime(2026, 1, day, 8, 30, tzinfo=UTC))
        for day in (28, 29):
            subjects.append(name)
            event.append(datetime(2026, 12, day, 7, 0, tzinfo=UTC))
            available.append(datetime(2026, 12, day, 8, 30, tzinfo=UTC))
    _write_probe(
        store,
        dataset=dataset,
        subjects=tuple(subjects),
        event=tuple(event),
        available=tuple(available),
    )


def _record_probe_coverage(
    store: PanelStore, *, dataset: str, row_count: int, subjects: tuple[str, ...]
) -> None:
    """The coverage record a raw `write_partition` needs before readiness will look at it.

    `max_available_time` is placed after every `as_of` these tests use, so `not_yet_knowable`
    fires and the filtered read is exercised on the path it exists for rather than on the
    trivial one where the predicate removes nothing.
    """
    written = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
    store.record_coverage(
        PartitionCoverage(
            dataset=dataset,
            year=YEAR,
            provider_id="synthetic",
            kind="probe",
            schema_version="panel-batch/v1",
            batch_digest="0" * 64,
            as_of=written,
            fetched_at=written,
            row_count=row_count,
            date_timezone="Asia/Shanghai",
            last_event_time=datetime(2026, 1, 5, 8, 30, tzinfo=UTC),
            max_available_time=datetime(2027, 6, 1, tzinfo=UTC),
            revised_row_count=0,
            subjects=subjects,
            fields=(FieldCoverage(name=SUBJECT_COLUMN, kind="string"),),
            dates=(DateCoverage(event_date=date(2026, 1, 5), row_count=row_count),),
        )
    )


def test_a_declared_staleness_bound_is_honoured_against_the_rows_the_read_returns(
    store: PanelStore,
) -> None:
    """C1. The bound the caller stated was accepted and then structurally ignored.

    `stale` compares `as_of - coverage.last_event_time`, and `last_event_time` is the *whole
    partition's* newest event. On this path `not_yet_knowable` fires precisely because the
    partition holds rows knowable after `as_of`, so the newest event is after `as_of` too and
    the difference is negative: the check could not fire whatever bound was passed. Measured
    before the fix, at bounds of one hour, one day and two days, the read answered all three
    times with a visible slice whose newest event was 2 days 21 hours old.

    Three bounds rather than one, because a single bound leaves "the check now fires" and "the
    check fires for the right reason" indistinguishable -- and the fourth case is the pair that
    makes the refusal attributable: the same partition at the same `as_of`, a bound the slice
    *does* satisfy, and an answer.
    """
    reach = datetime(2026, 1, 9, 7, 0, tzinfo=UTC)
    behind = MID_WINDOW - reach

    for bound in (timedelta(hours=1), timedelta(days=1), timedelta(days=2)):
        assert bound < behind
        refused = store.read_visible_at(_bounded(MID_WINDOW, bound), year=YEAR, columns=PROJECTION)
        assert refused.is_blocked
        assert [issue.code for issue in refused.visible_slice_issues] == ["stale"]
        assert {issue.code for issue in refused.blocking_issues} == {"not_yet_knowable", "stale"}

    generous = store.read_visible_at(
        _bounded(MID_WINDOW, timedelta(days=4)), year=YEAR, columns=PROJECTION
    )

    assert not generous.is_blocked
    assert generous.visible_slice_issues == ()
    assert generous.visible_last_event_time == reach
    assert behind == timedelta(days=2, hours=21)


def test_a_bound_equal_to_the_slices_own_staleness_answers_and_one_tick_less_refuses(
    store: PanelStore,
) -> None:
    """The comparison's boundary, pinned for the reason the availability boundary already is.

    `staleness_issue` refuses when `as_of - last_event_time` is **greater than** the bound, so a
    bound exactly equal to the observed staleness is satisfied. That edge is invisible to every
    test that picks a round number: `<=` mutated to `<` changes the verdict only at equality, and
    nothing else in this file sits there.

    One microsecond less is asserted alongside it, because "the boundary is inclusive" and "the
    check fires at all" are different facts and a test that only showed the first could be
    satisfied by a check that never fires.
    """
    behind = MID_WINDOW - datetime(2026, 1, 9, 7, 0, tzinfo=UTC)

    exactly_at = store.read_visible_at(_bounded(MID_WINDOW, behind), year=YEAR, columns=PROJECTION)
    one_tick_less = store.read_visible_at(
        _bounded(MID_WINDOW, behind - timedelta(microseconds=1)), year=YEAR, columns=PROJECTION
    )

    assert not exactly_at.is_blocked
    assert exactly_at.visible_slice_issues == ()
    assert one_tick_less.is_blocked
    assert [issue.code for issue in one_tick_less.visible_slice_issues] == ["stale"]


def test_a_slice_blocked_read_refuses_to_say_how_far_it_would_have_reached(
    store: PanelStore,
) -> None:
    """The third accessor joins the two that already raise on a blocked outcome.

    `rows` and `withheld_row_count` raise because "blocked" and "empty" are different facts and
    a merged answer re-collapses them. `visible_last_event_time` has the same pair -- `None`
    means "this answer reaches nothing", which is a statement about an answer that exists -- so
    it raises here too, and the refusal names the codes rather than only the dataset. The
    escape hatch keeps the merged shape reachable under a name that says what it is.
    """
    refused = store.read_visible_at(
        _bounded(MID_WINDOW, timedelta(hours=1)), year=YEAR, columns=PROJECTION
    )

    assert refused.is_blocked
    with pytest.raises(PanelStorageError, match="how far do the visible rows reach"):
        _ = refused.visible_last_event_time
    with pytest.raises(PanelStorageError, match="no visible rows to read"):
        _ = refused.rows
    assert refused.visible_last_event_time_or_none is None
    assert refused.compensated_issue_codes == ()


def test_judging_a_visible_slice_without_probing_the_subjects_it_names_raises() -> None:
    """The sentinel on the pure evaluator, aimed at the likeliest mistake in this arrangement.

    A caller adding a statement to the scan and forgetting to thread its result through would
    otherwise get a green suite, because an absent probe reads as an empty set difference and an
    empty set difference is a pass. The criterion this repository uses for a sentinel is whether
    the most available wrong spelling passes quietly; here it would, so `None` beside a
    requirement that names subjects is refused rather than treated as "nothing missing".

    The waived case is asserted next to it, because a guard that refused both would make
    `required_subjects=None` unusable and the two are one line apart in the implementation.
    """
    naming = _probe_requirement("probe", MID_WINDOW, subjects=("000001.SZ",))
    waiving = _probe_requirement("probe", MID_WINDOW, subjects=None)

    with pytest.raises(PanelStorageError, match="has to be probed"):
        evaluate_visible_slice(naming, visible_last_event_time=None, visible_subjects=None)

    assert (
        evaluate_visible_slice(waiving, visible_last_event_time=None, visible_subjects=None) == ()
    )


def test_the_partition_wide_check_cannot_see_what_the_visible_slice_check_refuses(
    store: PanelStore,
) -> None:
    """The reason the second gate is not decoration, asserted as arithmetic rather than argued.

    If `evaluate_readiness` already caught this, re-deciding it would be duplication. It does
    not: at `MID_WINDOW` the coverage record's `last_event_time` is *after* `as_of`, so the
    partition-level difference is negative and no bound, however small, can be exceeded.
    """
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None
    assert coverage.last_event_time > MID_WINDOW

    partition_level = store.assess_readiness(_bounded(MID_WINDOW, timedelta(seconds=1)))

    assert {issue.code for issue in partition_level.issues} == {"not_yet_knowable"}


def test_a_required_subject_visible_only_after_the_as_of_refuses_the_read(
    store: PanelStore,
) -> None:
    """MA1's general form: a pooled check clears on rows the answer does not carry.

    `subject_missing` is decided against `coverage.subjects`, the census of the whole partition.
    A security whose rows all became knowable after `as_of` is in that census and absent from the
    answer, so before the re-check the requirement cleared and the caller received a cross
    section quietly one name short -- the review's own example, a required name that never
    appears in the rows and no issue anywhere.

    The pair is the same partition at the same `as_of` with that one name dropped from
    `required_subjects`, which answers: so the refusal is attributable to the name rather than to
    the partition being mid-year.
    """
    late_name = "000001.SZ"
    dataset = "probe_late_subject"
    _write_late_subject_probe(store, dataset=dataset, late_name=late_name)
    requirement = _probe_requirement(dataset, MID_WINDOW, subjects=("000002.SZ", late_name))

    refused = store.read_visible_at(requirement, year=YEAR, columns=(SUBJECT_COLUMN,))
    without_it = store.read_visible_at(
        replace(requirement, required_subjects=("000002.SZ",)),
        year=YEAR,
        columns=(SUBJECT_COLUMN,),
    )

    assert {issue.code for issue in store.assess_readiness(requirement).issues} == {
        "not_yet_knowable"
    }
    assert refused.is_blocked
    assert [issue.code for issue in refused.visible_slice_issues] == ["subject_missing"]
    assert refused.visible_slice_issues[0].missing_items == (late_name,)
    assert not without_it.is_blocked
    assert {str(row[0]) for row in without_it.rows} == {"000002.SZ"}


def test_the_visible_slice_checks_judge_the_callers_own_selection_filters_included(
    store: PanelStore,
) -> None:
    """`VISIBLE_SLICE_SCOPE` is "the rows this read returns", and `filters` are part of that.

    The re-decided checks share `_equality_clauses` with the withheld count, so all three answer
    about one row set. The consequence is worth pinning rather than discovering: a caller that
    narrows to one security while requiring two is refused, because the security it required is
    genuinely not in the answer -- even though the partition holds it and the partition-level
    `subject_missing` (which reads the coverage census and never sees `filters`) clears.

    The partition here is deliberately `ready` outright, with nothing to compensate: it makes
    the disagreement between the two verdicts the only thing on the table, and shows that the
    second gate is not something that only happens on a compensated read.
    """
    dataset = "probe_filtered_subject"
    _write_probe(
        store,
        dataset=dataset,
        subjects=("000001.SZ", "000002.SZ"),
        event=(datetime(2026, 1, 6, 7, 0, tzinfo=UTC), datetime(2026, 1, 6, 7, 0, tzinfo=UTC)),
        available=(
            datetime(2026, 1, 6, 8, 30, tzinfo=UTC),
            datetime(2026, 1, 6, 8, 30, tzinfo=UTC),
        ),
    )
    requirement = _probe_requirement(dataset, MID_WINDOW, subjects=("000001.SZ", "000002.SZ"))

    unfiltered = store.read_visible_at(requirement, year=YEAR, columns=(SUBJECT_COLUMN,))
    narrowed = store.read_visible_at(
        requirement, year=YEAR, columns=(SUBJECT_COLUMN,), filters={SUBJECT_COLUMN: "000001.SZ"}
    )

    assert store.assess_readiness(requirement).state == "ready"
    assert not unfiltered.is_blocked
    assert narrowed.is_blocked
    assert [issue.code for issue in narrowed.visible_slice_issues] == ["subject_missing"]
    assert narrowed.visible_slice_issues[0].missing_items == ("000002.SZ",)
    assert "the rows this read returns" in narrowed.visible_slice_issues[0].detail


def test_a_visible_slice_that_reaches_nothing_is_refused_rather_than_answered_empty(
    store: PanelStore,
) -> None:
    """The boundary case of the reach check, and the one a bare `None` would have waved through.

    Read one microsecond before the first session publishes: every structural check clears, the
    predicate keeps no row, and `max(event_time)` over nothing is `NULL`. Treating that as "no
    staleness evidence, therefore no issue" is the fail-open shape this repository keeps paying
    for, so a declared bound refuses it. With the bound waived the same read answers -- empty,
    every row withheld, reach `None` -- which is the honest description of it, and the two are
    asserted together so the refusal cannot be mistaken for the partition being unreadable.
    """
    before_anything = datetime(2026, 1, 5, 8, 29, 59, 999999, tzinfo=UTC)
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None
    dateless = replace(_requirement(MID_WINDOW), as_of=before_anything, required_dates=None)

    refused = store.read_visible_at(
        replace(dateless, max_staleness=timedelta(days=365)), year=YEAR, columns=PROJECTION
    )
    waived = store.read_visible_at(dateless, year=YEAR, columns=PROJECTION)

    assert refused.is_blocked
    assert [issue.code for issue in refused.visible_slice_issues] == ["stale"]
    assert "reaches no event instant at all" in refused.visible_slice_issues[0].detail
    assert not waived.is_blocked
    assert waived.rows == ()
    assert waived.visible_last_event_time is None
    assert waived.withheld_row_count == coverage.row_count


def test_the_reach_and_the_withheld_count_are_not_the_same_fact(store: PanelStore) -> None:
    """The non-correlation the review measured, rebuilt here as the partition that showed it.

    `withheld_row_count` was the whole of `V2-P3-002`'s compensation -- "shortness is stated".
    The measurement that broke it withholds **four** rows out of fourteen while the visible slice
    ends 172 days before `as_of`: the compensation at its weakest exactly where the answer is
    worst. So the two numbers are pinned against each other on one read, and then the same read
    with a bound stated is pinned as a refusal.
    """
    dataset = "probe_reach"
    as_of = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    _write_reach_probe(store, dataset=dataset)

    waived = store.read_visible_at(
        _probe_requirement(dataset, as_of, subjects=None), year=YEAR, columns=(SUBJECT_COLUMN,)
    )

    assert waived.withheld_row_count == 4
    assert waived.visible_row_count == 10
    reach = waived.visible_last_event_time
    assert reach is not None
    assert reach.astimezone(UTC).date() == date(2026, 1, 9)
    assert as_of - reach > timedelta(days=170)

    refused = store.read_visible_at(
        _probe_requirement(dataset, as_of, subjects=None, bound=timedelta(days=7)),
        year=YEAR,
        columns=(SUBJECT_COLUMN,),
    )

    assert refused.is_blocked
    assert [issue.code for issue in refused.visible_slice_issues] == ["stale"]


def test_a_row_with_no_availability_instant_is_withheld_and_still_counted(
    tmp_path: Path,
) -> None:
    """MI1. `visible + withheld == row_count` was asserted unconditionally and was false.

    `available_time` is nullable in Parquet and SQL's three-valued logic drops a NULL row from
    both `<= as_of` and `> as_of`, so such a row appeared in neither half: measured at 2 + 0 over
    a 3-row partition, with no readiness code naming it and `partition_row_count_mismatch` blind
    to it because the Parquet footer count never changed.

    Two things are pinned, because the identity alone would also be satisfied by a fix that
    leaked the row: it is **never visible at any `as_of`**, including one a year after every
    other row, and it is counted as withheld so the halves add up. `write_partition` with raw
    rows is the only door to this shape -- `TimelineColumns` refuses a missing clock -- so the
    partition is built that way here on purpose.
    """
    store = PanelStore(tmp_path / "panel")
    dataset = "probe_null_clock"
    early = datetime(2026, 1, 5, 8, 30, tzinfo=UTC)
    store.write_partition(
        dataset,
        YEAR,
        (
            ColumnSpec(SUBJECT_COLUMN, "VARCHAR"),
            ColumnSpec(EVENT_TIME_COLUMN, "TIMESTAMPTZ"),
            ColumnSpec(AVAILABILITY_COLUMN, "TIMESTAMPTZ"),
        ),
        (
            ("000001.SZ", early, early),
            ("000002.SZ", early, datetime(2026, 6, 1, 8, 30, tzinfo=UTC)),
            ("000003.SZ", early, None),
        ),
    )
    _record_probe_coverage(store, dataset=dataset, row_count=3, subjects=("000001.SZ",))

    for as_of in (early, datetime(2027, 1, 1, tzinfo=UTC)):
        outcome = store.read_visible_at(
            _probe_requirement(dataset, as_of, subjects=None), year=YEAR, columns=(SUBJECT_COLUMN,)
        )
        assert "000003.SZ" not in {str(row[0]) for row in outcome.rows}
        assert outcome.visible_row_count + outcome.withheld_row_count == 3


def test_the_date_gap_recheck_would_be_a_no_op_on_every_requirement_that_states_dates(
    store: PanelStore,
) -> None:
    """Why `date_gap` is in `SCOPE_SENSITIVE_ISSUE_CODES` and not in `VISIBLE_SLICE_RECHECKS`.

    The exclusion's structural half (re-deriving session dates in SQL would be a second copy of
    `panel_ingest._date_census`, which `openalpha_cn.panel` cannot even import) is an argument.
    Its measured half is this, and it is what makes the argument affordable rather than merely
    available: `_price_requirement` is the only requirement in `panel_ingest` that states
    `required_dates`, and it clamps them with `_sessions_published_through`, which cuts at the
    same 16:30 Asia/Shanghai instant the provider dates `available_time` at. Every required
    session is therefore visible **by construction**, and re-deciding `date_gap` over the
    returned rows would find nothing on any path that exists today.

    Asserted as an equality rather than a subset, because a subset would also hold if the clamp
    started under-requiring. The day a caller states dates on some other footing, that caller
    will not satisfy this and the exclusion has to be re-argued instead of inherited.
    """
    requirement = _requirement(MID_WINDOW)
    answered = store.read_visible_at(requirement, year=YEAR, columns=PROJECTION)

    assert requirement.required_dates is not None
    assert set(requirement.required_dates) == set(_sessions_of(answered.rows))
    assert set(requirement.required_dates) == set(VISIBLE_SESSIONS)
    assert not answered.is_blocked
    assert "date_gap" in SCOPE_SENSITIVE_ISSUE_CODES
    assert "date_gap" not in VISIBLE_SLICE_RECHECKS
    assert "date_gap_clears_on_partition_rows_the_filtered_read_withholds" in {
        item.code for item in KNOWN_STORAGE_LIMITATIONS
    }


def test_every_readiness_code_is_classified_as_filterable_scope_sensitive_or_neither() -> None:
    """The partition, asserted as a partition, so a fourteenth code cannot arrive unclassified.

    **Judgement criterion, because more than one branch may edit `READINESS_ISSUE_CODES`.** The
    three sets below must be the *union* of what the vocabulary declares, never a subset that
    happens to pass. A code introduced by any change belongs in exactly one bucket, decided by
    `SCOPE_SENSITIVE_ISSUE_CODES`'s stated rule -- does the verdict read a per-row fact pooled
    over the partition? -- and a merge that brings one must place it here in the same diff.
    Deleting a member to make this green is the failure this assertion exists to catch: a
    hand-maintained table losing an entry across a merge is exactly what `V2-P2`'s remediation
    paid for once already.
    """
    scope_invariant = frozenset(
        {
            "no_years_requested",
            "empty_requirement",
            "partition_missing",
            "partition_file_missing",
            "partition_file_unreadable",
            "partition_row_count_mismatch",
            "coverage_missing",
            "coverage_stale",
            "field_missing",
        }
    )

    assert not ROW_FILTERABLE_ISSUE_CODES & SCOPE_SENSITIVE_ISSUE_CODES
    assert not ROW_FILTERABLE_ISSUE_CODES & scope_invariant
    assert not SCOPE_SENSITIVE_ISSUE_CODES & scope_invariant
    assert (
        ROW_FILTERABLE_ISSUE_CODES | SCOPE_SENSITIVE_ISSUE_CODES | scope_invariant
        == READINESS_ISSUE_CODES
    )
    assert VISIBLE_SLICE_RECHECKS < SCOPE_SENSITIVE_ISSUE_CODES


def test_the_two_read_outcomes_expose_rows_at_the_same_static_type() -> None:
    """MA2. "It is a different type, so mypy stops it" was measured and is too strong.

    `PanelVisibleReadOutcome.rows` and `PanelReadOutcome.rows` carry the identical annotation, so
    `stock_universe_from_panel_rows(list(filtered.rows), ...)` -- one of the three consumers P2
    named -- type-checks clean under `mypy --strict`, and so does assigning either `.rows` to a
    variable annotated for the other. The type checker stops exactly one mistake: passing a whole
    *outcome* where the other outcome was expected.

    Asserted rather than only corrected in prose, because the corrected prose depends on it: if a
    later change gives the two genuinely different row types, this fails, and the docstrings
    saying the allowlist is the real obstacle can be strengthened instead of left stale.
    """
    gated = PanelReadOutcome.rows.fget
    filtered = PanelVisibleReadOutcome.rows.fget
    assert gated is not None and filtered is not None

    assert gated.__annotations__["return"] == filtered.__annotations__["return"]
    assert gated.__annotations__["return"] == "tuple[tuple[object, ...], ...]"
