"""The readiness *rules* in isolation (`V2-P1-003`): no DuckDB, no filesystem.

`openalpha_cn.panel.catalog.evaluate_readiness` is a pure, total function over a
requirement and a per-year `PartitionState`. Keeping it pure is what lets these tests state
the contract `V2-P1-013` ("assert blocking, not an empty success") depends on without
building a store first, and what makes every judgement here reproducible: the only clock
involved is `ReadinessRequirement.as_of`, which the caller supplies.

The integration side -- that a real catalog produces these states, and that a blocked read
never hands back rows -- lives in `tests/integration/panel/test_panel_readiness.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from openalpha_cn.panel.catalog import (
    READINESS_ISSUE_CODES,
    READINESS_WAIVABLE_CHECKS,
    DateCoverage,
    FieldCoverage,
    PartitionCoverage,
    PartitionState,
    ReadinessRequirement,
    RevisionCoverage,
    evaluate_readiness,
)

DATASET = "prices_daily"
TIMEZONE = "Asia/Shanghai"
COVERED_DATES = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
LAST_EVENT = datetime(2024, 1, 4, 7, 0, tzinfo=UTC)
LAST_AVAILABLE = datetime(2024, 1, 4, 8, 30, tzinfo=UTC)
AS_OF = datetime(2024, 1, 4, 12, 0, tzinfo=UTC)
SUBJECTS = ("000001.SZ", "000002.SZ")
FIELDS = ("close", "vol")
CONTENT_HASH = "sha256:" + "a" * 64
"""The partition hash a state and its coverage record agree on, unless a test says otherwise.

Stated on both sides rather than defaulted, because `PartitionState.content_hash` and
`PartitionCoverage.partition_content_hash` disagreeing is itself one of the faults under test
(`coverage_stale`) -- a helper that quietly filled in matching values, or a dataclass field
that defaulted to `None` on both sides, would make every other test in this file pass the
freshness cross-check by accident rather than by construction.
"""


def _coverage(
    *,
    year: int = 2024,
    dates: tuple[date, ...] = COVERED_DATES,
    subjects: tuple[str, ...] = SUBJECTS,
    fields: tuple[str, ...] = FIELDS,
    last_event_time: datetime = LAST_EVENT,
    max_available_time: datetime = LAST_AVAILABLE,
    revised_row_count: int = 0,
    revisions: tuple[RevisionCoverage, ...] = (),
    partition_content_hash: str | None = CONTENT_HASH,
) -> PartitionCoverage:
    return PartitionCoverage(
        dataset=DATASET,
        year=year,
        provider_id="tushare",
        kind="daily",
        schema_version="panel-batch/v1",
        batch_digest="sha256:" + "0" * 64,
        as_of=AS_OF,
        fetched_at=AS_OF,
        row_count=len(dates) * len(subjects),
        date_timezone=TIMEZONE,
        last_event_time=last_event_time,
        max_available_time=max_available_time,
        revised_row_count=revised_row_count,
        subjects=subjects,
        fields=tuple(FieldCoverage(name=name, kind="float") for name in fields),
        dates=tuple(DateCoverage(event_date=day, row_count=len(subjects)) for day in dates),
        revisions=revisions,
        partition_content_hash=partition_content_hash,
    )


def _state(
    *,
    year: int = 2024,
    registered: bool = True,
    file_present: bool = True,
    file_readable: bool = True,
    content_hash: str | None = CONTENT_HASH,
    profiled: bool = True,
) -> PartitionState:
    return PartitionState(
        year=year,
        registered=registered,
        file_present=file_present,
        file_readable=file_readable,
        content_hash=content_hash,
        coverage=_coverage(year=year) if profiled else None,
    )


def _requirement(**overrides: object) -> ReadinessRequirement:
    defaults: dict[str, object] = {
        "dataset": DATASET,
        "as_of": AS_OF,
        "years": (2024,),
        "required_dates": COVERED_DATES,
        "required_subjects": SUBJECTS,
        "required_fields": FIELDS,
        "max_staleness": timedelta(days=2),
    }
    defaults.update(overrides)
    return ReadinessRequirement(**defaults)  # type: ignore[arg-type]


def _bare_requirement(**overrides: object) -> ReadinessRequirement:
    """A requirement that asks only "does this year exist and can it be described".

    Deliberately waives `required_dates`/`required_subjects`/`required_fields`/
    `max_staleness`: those are *pooled* checks, evaluated across every usable year, so a
    requirement carrying them reports a coverage shortfall alongside the per-year fault that
    caused it. That cascade is correct behaviour
    (`test_the_verdict_reports_every_issue_it_found_not_only_the_first` pins it) and merely
    noise for the tests below, which are about the per-year state alone.

    `None`, not `()`: an empty tuple is a *declared* expectation that can never find a
    shortfall, which the evaluator blocks on (`empty_requirement`). `None` says the check was
    switched off deliberately, and the verdict records it in `checks_waived`.
    """
    waived: dict[str, object] = {
        "required_dates": None,
        "required_subjects": None,
        "required_fields": None,
        "max_staleness": None,
    }
    waived.update(overrides)
    return _requirement(**waived)


def _codes(requirement: ReadinessRequirement, states: tuple[PartitionState, ...]) -> list[str]:
    return [issue.code for issue in evaluate_readiness(requirement, partitions=states).issues]


def test_a_fully_covered_partition_is_ready_with_no_issues() -> None:
    readiness = evaluate_readiness(_requirement(), partitions=(_state(),))

    assert readiness.state == "ready"
    assert readiness.is_ready
    assert readiness.issues == ()
    assert readiness.years_present == (2024,)
    assert readiness.row_count == 6
    assert readiness.subject_count == 2
    assert readiness.last_event_time == LAST_EVENT


def test_a_year_with_no_catalog_row_blocks_with_partition_missing() -> None:
    readiness = evaluate_readiness(
        _requirement(years=(2023, 2024)),
        partitions=(_state(year=2023, registered=False, profiled=False), _state(year=2024)),
    )

    assert readiness.state == "blocked"
    assert [(issue.code, issue.year) for issue in readiness.issues] == [("partition_missing", 2023)]
    assert readiness.years_present == (2024,)


def test_a_catalog_row_whose_parquet_file_is_gone_blocks_with_partition_file_missing() -> None:
    """The "delete a partition" defect `V2-P1-012`'s acceptance calls for: the catalog still
    advertises the partition, but the file it points at is not there. Reporting this needs a
    state distinct from `partition_missing` -- a catalog row with no file is a corrupted
    store, not an un-ingested year."""
    readiness = evaluate_readiness(_bare_requirement(), partitions=(_state(file_present=False),))

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["partition_file_missing"]
    assert readiness.years_present == ()


def test_a_registered_but_never_profiled_partition_blocks_instead_of_passing_silently() -> None:
    """Fail-closed on absent knowledge. A partition written through `PanelStore` directly
    (no batch, so no coverage record) can answer none of S8's five questions, so readiness
    must refuse it rather than treat "no issues found" as "no issues exist"."""
    readiness = evaluate_readiness(_bare_requirement(), partitions=(_state(profiled=False),))

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["coverage_missing"]


def test_a_date_hole_is_named_rather_than_merely_counted() -> None:
    covered = (COVERED_DATES[0], COVERED_DATES[2])
    state = PartitionState(
        year=2024,
        registered=True,
        file_present=True,
        file_readable=True,
        content_hash=CONTENT_HASH,
        coverage=_coverage(dates=covered),
    )

    readiness = evaluate_readiness(_requirement(), partitions=(state,))

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["date_gap"]
    assert readiness.issues[0].missing_dates == (COVERED_DATES[1],)


def test_staleness_is_judged_against_as_of_and_never_against_the_wall_clock() -> None:
    """The same catalog contents are ready at one `as_of` and stale at another. Nothing here
    reads a machine clock, so this judgement is reproducible on any day the suite runs --
    which is exactly the property `V2-P1-013`'s gate needs when it replays a historical
    `as_of`."""
    states = (_state(),)
    fresh = _requirement(as_of=LAST_EVENT + timedelta(days=2), max_staleness=timedelta(days=2))
    stale = _requirement(
        as_of=LAST_EVENT + timedelta(days=2, seconds=1), max_staleness=timedelta(days=2)
    )

    assert evaluate_readiness(fresh, partitions=states).state == "ready"

    verdict = evaluate_readiness(stale, partitions=states)
    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["stale"]


def test_no_staleness_bound_means_no_staleness_issue_however_old_the_data_is() -> None:
    ancient = _requirement(as_of=datetime(2031, 1, 1, tzinfo=UTC), max_staleness=None)

    assert evaluate_readiness(ancient, partitions=(_state(),)).state == "ready"


def test_a_partition_holding_information_newer_than_as_of_blocks_the_read() -> None:
    """Point-in-time fail-closed: a partition whose newest `available_time` post-dates the
    requirement's `as_of` cannot be read at that `as_of` without leaking hindsight."""
    early = _requirement(as_of=LAST_AVAILABLE - timedelta(seconds=1), max_staleness=None)

    verdict = evaluate_readiness(early, partitions=(_state(),))

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["not_yet_knowable"]


def test_a_missing_required_field_blocks_and_names_the_field() -> None:
    requirement = _requirement(required_fields=("close", "vol", "amount"))

    verdict = evaluate_readiness(requirement, partitions=(_state(),))

    assert [issue.code for issue in verdict.issues] == ["field_missing"]
    assert verdict.issues[0].missing_items == ("amount",)


def test_a_missing_required_subject_blocks_and_names_the_subject() -> None:
    requirement = _requirement(required_subjects=(*SUBJECTS, "600519.SH"))

    verdict = evaluate_readiness(requirement, partitions=(_state(),))

    assert [issue.code for issue in verdict.issues] == ["subject_missing"]
    assert verdict.issues[0].missing_items == ("600519.SH",)


def test_a_partition_whose_file_is_present_but_not_a_parquet_file_blocks() -> None:
    """`Path.is_file()` is true of a zero-byte file and of a file whose bytes were replaced.
    Both used to reach `ready` with no issues, and the second made a subsequent read raise a
    bare DuckDB exception out of `read_if_ready()`."""
    verdict = evaluate_readiness(_bare_requirement(), partitions=(_state(file_readable=False),))

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["partition_file_unreadable"]
    assert verdict.years_present == ()


def test_a_coverage_record_describing_a_replaced_partition_blocks_as_stale() -> None:
    """The fail-open this rule exists for. `record_coverage` binds a record to a partition
    once; `write_partition` is overwrite-per-partition, so the very operation the store is
    designed for -- a backfill or a correction -- silently unbinds it again. Every other check
    still passes on the obsolete record: the file is there, coverage is there, and its dates,
    subjects and fields all come from it."""
    replaced = _state(content_hash="sha256:" + "b" * 64)

    verdict = evaluate_readiness(_bare_requirement(), partitions=(replaced,))

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["coverage_stale"]
    assert verdict.years_present == ()
    assert "a" * 64 in verdict.issues[0].detail
    assert "b" * 64 in verdict.issues[0].detail
    # The record is dropped from the usable pool, so nothing it claims is inherited: a
    # requirement that *does* name dates and subjects finds none of them, rather than being
    # reassured by a description of content that is no longer there.
    strict = evaluate_readiness(_requirement(), partitions=(replaced,))
    assert [issue.code for issue in strict.issues] == [
        "coverage_stale",
        "date_gap",
        "subject_missing",
        "field_missing",
    ]


def test_a_coverage_record_that_never_recorded_a_partition_hash_blocks_too() -> None:
    """What a `panel-catalog/v1` coverage row reads back as. Unknown is not agreement: the
    record cannot be shown to describe what is on disk, so it blocks rather than being taken
    on trust -- one `record_coverage()` call clears it."""
    legacy = PartitionState(
        year=2024,
        registered=True,
        file_present=True,
        file_readable=True,
        content_hash=CONTENT_HASH,
        coverage=_coverage(partition_content_hash=None),
    )

    verdict = evaluate_readiness(_bare_requirement(), partitions=(legacy,))

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["coverage_stale"]


def test_a_declared_but_empty_expectation_blocks_instead_of_passing_vacuously() -> None:
    """The same rule `no_years_requested` already enforced, reaching the checks that carry
    Story S8's hole detection. `required_dates=()` makes the date check a set difference
    against nothing, which can never report a gap -- so an entire year holding one trading
    day used to be `ready` under a requirement that looked like it was checking dates."""
    verdict = evaluate_readiness(
        _bare_requirement(required_dates=(), required_subjects=()), partitions=(_state(),)
    )

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["empty_requirement"]
    assert verdict.issues[0].missing_items == ("required_dates", "required_subjects")


def test_the_year_long_partition_holding_one_trading_day_is_no_longer_vacuously_ready() -> None:
    """The reviewer's reproduction, as a rule-table test. A partition covering a single day of
    2024, assessed at the end of 2024, reported `ready` with no issues under a requirement
    built entirely from field defaults. There are no such defaults now: the two checks that
    would have caught it have to be stated, and stating them catches it."""
    sparse = PartitionState(
        year=2024,
        registered=True,
        file_present=True,
        file_readable=True,
        content_hash=CONTENT_HASH,
        coverage=_coverage(dates=(date(2024, 1, 2),)),
    )
    year_end = datetime(2024, 12, 31, tzinfo=UTC)

    verdict = evaluate_readiness(
        _requirement(
            as_of=year_end,
            required_dates=COVERED_DATES,
            max_staleness=timedelta(days=5),
        ),
        partitions=(sparse,),
    )

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["date_gap", "stale"]
    assert verdict.issues[0].missing_dates == COVERED_DATES[1:]


def test_a_waived_check_is_recorded_on_the_verdict_rather_than_silently_skipped() -> None:
    """`None` is a legitimate answer -- not every caller has a date universe to declare -- but
    a verdict that did not look at dates must not be indistinguishable from one that did.
    `V2-P1-012`'s report and `V2-P1-013`'s gate read `checks_waived` to tell them apart."""
    waived = evaluate_readiness(_bare_requirement(), partitions=(_state(),))
    checked = evaluate_readiness(_requirement(), partitions=(_state(),))

    assert waived.state == "ready"
    assert set(waived.checks_waived) == {
        "required_dates",
        "required_subjects",
        "required_fields",
        "max_staleness",
    }
    assert set(waived.checks_waived) <= READINESS_WAIVABLE_CHECKS
    assert checked.state == "ready"
    assert checked.checks_waived == ()


def test_a_requirement_naming_no_year_is_blocked_rather_than_vacuously_ready() -> None:
    """An empty year list has nothing to find fault with, so a naive implementation reports
    "ready" for a dataset it never looked at -- the precise shape of the empty success
    `V2-P1-013` must not accept."""
    verdict = evaluate_readiness(_bare_requirement(years=()), partitions=())

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["no_years_requested"]


def test_coverage_spanning_several_years_is_pooled_before_the_date_check() -> None:
    requirement = _requirement(
        years=(2023, 2024),
        required_dates=(date(2023, 12, 29), *COVERED_DATES),
        required_subjects=SUBJECTS,
    )
    older = PartitionState(
        year=2023,
        registered=True,
        file_present=True,
        file_readable=True,
        content_hash=CONTENT_HASH,
        coverage=_coverage(
            year=2023,
            dates=(date(2023, 12, 29),),
            last_event_time=datetime(2023, 12, 29, 7, 0, tzinfo=UTC),
            max_available_time=datetime(2023, 12, 29, 8, 30, tzinfo=UTC),
        ),
    )

    verdict = evaluate_readiness(requirement, partitions=(older, _state()))

    assert verdict.state == "ready"
    assert verdict.years_present == (2023, 2024)
    assert verdict.row_count == 8
    assert verdict.last_event_time == LAST_EVENT


def test_every_issue_this_evaluator_can_emit_is_declared_in_the_closed_code_set() -> None:
    """The codes are an interface, not prose: `V2-P1-012`'s report groups by them and
    `V2-P1-013`'s gate branches on them. A code emitted but never declared would be
    invisible to both."""
    emitted = set()
    emitted.update(_codes(_requirement(years=()), ()))
    emitted.update(_codes(_requirement(required_dates=()), (_state(),)))
    emitted.update(_codes(_requirement(), (_state(registered=False, profiled=False),)))
    emitted.update(_codes(_requirement(), (_state(file_present=False),)))
    emitted.update(_codes(_requirement(), (_state(file_readable=False),)))
    emitted.update(_codes(_requirement(), (_state(profiled=False),)))
    emitted.update(_codes(_requirement(), (_state(content_hash="sha256:" + "b" * 64),)))
    emitted.update(
        _codes(
            _requirement(required_dates=(*COVERED_DATES, date(2024, 1, 5))),
            (_state(),),
        )
    )
    emitted.update(_codes(_requirement(required_subjects=("nope",)), (_state(),)))
    emitted.update(_codes(_requirement(required_fields=("nope",)), (_state(),)))
    emitted.update(_codes(_requirement(as_of=LAST_EVENT + timedelta(days=9)), (_state(),)))
    emitted.update(
        _codes(
            _requirement(as_of=LAST_AVAILABLE - timedelta(seconds=1), max_staleness=None),
            (_state(),),
        )
    )

    assert emitted == set(READINESS_ISSUE_CODES)


def test_the_verdict_reports_every_issue_it_found_not_only_the_first() -> None:
    """`V2-P1-012`'s report is a summary of everything wrong with a dataset. Short-circuiting
    on the first fault would make a doctor run a game of whack-a-mole."""
    requirement = _requirement(
        years=(2023, 2024),
        required_dates=(date(2023, 12, 29), *COVERED_DATES),
        required_fields=("close", "amount"),
        max_staleness=timedelta(seconds=0),
    )

    verdict = evaluate_readiness(
        requirement, partitions=(_state(year=2023, registered=False, profiled=False), _state())
    )

    assert [issue.code for issue in verdict.issues] == [
        "partition_missing",
        "date_gap",
        "field_missing",
        "stale",
    ]
