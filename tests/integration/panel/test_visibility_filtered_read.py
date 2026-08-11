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

## What this file does not claim

That the filtered read reconstructs what a fetch made on 2026-01-12 would have returned. It
reconstructs what the *stored* partition says was knowable then, which is a weaker and
different thing wherever the upstream restates or re-scopes what it serves. That is disclosed as
`KNOWN_STORAGE_LIMITATIONS.
a_visibility_filtered_read_replays_a_partition_that_was_not_there_yet` and asserted as a
disclosure in `tests/unit/test_panel_doctor_rules.py`, not papered over here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from panel_fixtures import AS_OF, YEAR, generate_panel, write_generated_panel

from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.panel.catalog import (
    READINESS_ISSUE_CODES,
    ROW_FILTERABLE_ISSUE_CODES,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import AVAILABILITY_COLUMN, PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import daily_requirement

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
