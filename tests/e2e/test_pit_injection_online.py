"""P2's red-team gates, aimed at rows Tushare served this morning (`V2-P2-001`..`009`).

`tests/integration/test_injection_register.py` is the register those nine issues close, and
every one of its eight vectors runs over a **generated** panel: `tests/panel_fixtures.py`
composes a shape, the real writers store it, and a real domain read refuses it. That is the
right instrument for the question it asks -- a generator can produce a restatement, a doubled
index publication and a halt running into the close on demand, and live rows cannot be ordered
to contain one.

What it cannot ask is whether the same gates bite on the panel a user actually builds. Two of
the three planes it injects into have no `panel build` target at all (`index_weight`,
`index_member_all` and the four statement datasets), so on a real runtime directory the only P2
subject matter that exists is the price plane and the registry beside it; and the partitions
there are five orders of magnitude larger, carry every corporate action of the year to date,
and are written by a five-invocation build rather than by one call. This module is the part of
P2 that had no e2e representative: **build a real panel, put a defect into a real partition
through the real writer, and ask the CLI, the HTTP face and the SDK.**

## The four injections, and why these four

Each is aimed at a mechanism the register exercises on generated rows, chosen so that the
mechanism -- not the shape of a fixture -- is what is under test.

1. **A row that became knowable after the read** (`V2-P2-001`/`003`/`004`'s shared mechanism).
   `evaluate_readiness` refuses a whole partition whose newest `available_time` is later than
   the request's `as_of`, judged per partition rather than per row. Injected into `suspend_d`,
   which is the one price-plane partition small enough to round-trip in a second and is one of
   the five the doctor and the gate are asked about.
2. **A close that disagrees with its own `pct_chg`** (`V2-P2-006`). The third witness, on live
   `daily` rows, where `MAX_PUBLISHED_RETURN_DISAGREEMENT`'s bound was measured. It is the one
   injection invisible to the catalog by construction -- it moves no clock, no subject, no date
   and no row count -- so the readiness census stays `ready` and only the session-scoped
   cross-check can see it, which is the finding that issue exists to make.
3. **A correction of a stored fact, published after the fact** (`V2-P2-002`'s shape, on the
   plane `panel build` can build). What the panel plane does here is *not* what
   `StatementHistory.filing_for` does, and the difference is asserted rather than smoothed over:
   there is no row-level `available_time` filter, so the earlier question is refused outright
   instead of being answered from the version that was current then, and the later question is
   refused too because two rows now contradict each other about one session.
4. **A legal Parquet file whose contents changed behind the store** (P2's product acceptance).
   The three injections above all go *through* the writer, which is right for them and is
   exactly what makes this fourth one a different question. `test_panel_chain_online.py` covers
   two file-level defects -- a partition **deleted** and one **replaced by bytes that are not
   Parquet** -- and both are decided by the file's existence and its first and last eight
   bytes. The third shape was missing, and it is the one that was invisible: a file that is
   still a valid Parquet file, still registered, still magic at both ends, and no longer
   holding what the catalog says. On a real `stock_basic` partition one appended row produced
   `READY ... rows=152` over a 153-row file, `CLEARED` with exit 0, and the injected security
   inside `load_stock_universe`'s answer. Both halves are asserted here: the row count is now
   reconciled against the footer (`partition_row_count_mismatch`), and an edit that changes a
   value *in place* still is not, which is the disclosed residue
   (`KNOWN_STORAGE_LIMITATIONS.a_value_edited_in_place_leaves_the_census_intact`) and is
   asserted in both directions -- the census does not see it, the cross-check that reads the
   column does.

## Every injection is a pair

An injection that only shows "the read refused" proves nothing, because a partition is a year
and `not_yet_knowable` is decided for the year. So each test is two writes of **one** partition
read at **one** `as_of`: the injected one, and the same one with exactly the injected row
removed or restored, written through the same writer at the same fetch clock. That is what makes
"the refusal came from that row" an assertion rather than an argument, and it is the method
`tests/integration/panel/test_lookahead_injection.py` uses one plane down.

The reverse direction is load-bearing in its own right -- a red-team gate that refuses a sound
panel is worse than none -- so the "before" half of every test below asserts the untouched panel
answers cleanly, and `test_the_untouched_panel_is_still_ready_after_every_injection_above` is
the control for the module.

Injection 4 keeps the pairing and changes what the two halves are, because its subject is a
write the store never saw: both halves are the *same* stored partition, asked at the same
`as_of`, before and after the file underneath it is swapped. `withheld_partition` moves the
build's own file aside and restores it, so the "before" reading and the restored panel are the
same bytes rather than a rewrite that happens to agree.

## Determinism

The rule `test_panel_chain_online.py` states, and it binds harder here because an injection is a
number this file chose. No assertion names a price, a session, a security or a row count. What is
named is the *delta* -- five percentage points onto one `pct_chg`, three days onto one
`available_time` -- and the identity of the refusal it must produce. Everything else is read
back out of the panel that was built.

## Cost, and where all of it goes

195s of the 215s the whole `tests/e2e/` suite takes against a reused panel, and 185s of that is
`test_a_close_that_disagrees_with_its_own_pct_chg_is_caught_only_by_the_third_witness` alone.
The reason is the partition it has to aim at: `pct_chg` exists only on `daily`, whose 2026
partition is 796,497 rows, and going through `write_panel_batch` means transposing those rows
into a batch and back out through DuckDB twice -- once to inject, once to restore. Editing the
Parquet file directly would take a second, and the reason that is refused is the same reason the
rest of this module exists: the test would then be about a file rather than about a panel, and
`test_restoring_a_rewritten_partition_reproduces_the_catalog_row_the_build_wrote` -- the check
that the panel twenty-three other tests share comes back byte for byte -- would have nothing to
check. The other seven injections go into `suspend_d` and cost about a second each, so
`-k "not pct_chg"` is the way to iterate.

Injection 4 is the one case where editing the Parquet file directly is not a shortcut but the
whole subject, so it does exactly that -- through `withheld_partition`, which moves the build's
own file aside and restores it in a `finally`. Its `daily` half rewrites the 20 MB partition
once with one cell changed, which is seconds rather than the three minutes the same defect
costs through `write_panel_batch`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from e2e_support import (
    PARQUET_MAGIC,
    BuiltPanel,
    E2EEnvironmentError,
    InjectPartition,
    StoredPartition,
    WithholdPartition,
    catalogued_path,
    http_get,
    knowable_datasets,
    parquet_with_a_row_appended,
    parquet_with_one_cell_changed,
    read_as_of,
    read_stored_partition,
    rewrite_partition,
    run_data_check,
    run_doctor,
)

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET, AdjustmentError
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    MAX_PUBLISHED_RETURN_DISAGREEMENT,
    PRICE_DATE_COLUMN,
    PriceDataError,
    session_returns,
)
from openalpha_cn.domain.price_limits import (
    HALT_TYPE,
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    SUSPENSION_TIMING_COLUMN,
    SUSPENSION_TYPE_COLUMN,
    SuspensionError,
)
from openalpha_cn.panel.store import PanelStorageError
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_suspensions,
)
from openalpha_cn.sdk import OpenAlphaSDK

pytestmark = pytest.mark.e2e

PRICE_PLANE: tuple[str, ...] = (
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
)
"""The five datasets `panel doctor` and `data-check` are asked about here.

Deliberately the same five `test_panel_chain_online.py::ASSESSED_DATASETS` names, so the two
modules are talking about one panel rather than two subsets of it."""

PUBLICATION_LAG: timedelta = timedelta(days=3)
"""How far past the read the injected row's `available_time` is pushed.

Days rather than microseconds because the boundary is pinned separately
(`test_the_boundary_is_the_injected_rows_own_availability_instant`), and a gap this wide cannot
be mistaken for a rounding artefact of the store's microsecond timestamps."""

PCT_CHG_ERROR: float = 5.0
"""Percentage points added to one stored `pct_chg`.

Five rather than one tick: `MAX_PUBLISHED_RETURN_DISAGREEMENT` is a *full* tick of a
two-decimal percentage, because `pct_chg` is published on two grids across this corpus' eras
(`domain/daily_prices.py`), so an injection near the bound would be measuring the bound instead
of the witness. Five points is a corrupted cell or a column read one position over, which is the
shape the check exists for, and it is two orders of magnitude outside the tolerance -- asserted
in `test_a_close_that_disagrees_with_its_own_pct_chg_is_caught_only_by_the_third_witness` rather
than left as arithmetic in a comment."""


# --- reading the reports back ----------------------------------------------------------------


def assessed(built_panel: BuiltPanel) -> tuple[str, ...]:
    """`PRICE_PLANE` minus any partition already holding a row newer than the read.

    The same rule `test_panel_chain_online.py::_assessed` applies, for the same reason: a
    dataset the *build* left ahead of the read is refused for a cause this module did not
    create, and asking about it would make every injection here fail for someone else's finding.
    Unlike that one, this refuses to continue if a dataset an injection is aimed at drops out --
    an injection asked about a set it is not in is a test that cannot fail.
    """
    as_of = read_as_of(built_panel)
    knowable = knowable_datasets(built_panel.store, PRICE_PLANE, year=built_panel.year, as_of=as_of)
    for required in (DAILY_DATASET, SUSPENSION_DATASET):
        if required not in knowable:
            raise E2EEnvironmentError(
                f"{required} already holds a row that is not knowable at {as_of.isoformat()}, so "
                "an injection into it cannot be told apart from what the build left behind; "
                "test_every_session_scoped_dataset_reaches_the_same_last_session is where that "
                "is the finding"
            )
    return knowable


def codes(payload: Mapping[str, Any]) -> set[str]:
    return {finding["code"] for finding in payload["findings"]}


def findings(payload: Mapping[str, Any], code: str) -> list[Mapping[str, Any]]:
    return [finding for finding in payload["findings"] if finding["code"] == code]


def readiness_state(payload: Mapping[str, Any], dataset: str) -> str:
    for entry in payload["datasets"]:
        if entry["dataset"] == dataset:
            return str(entry["state"])
    raise AssertionError(f"the report says nothing about {dataset}: {payload['datasets']}")


def stored_date(value: object) -> date:
    """One `trade_date` cell as a date, whichever text the writer stored it as."""
    text = str(value)
    if "-" in text:
        return date.fromisoformat(text)
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


# --- the two row surgeries every injection is built from --------------------------------------


def a_prunable_row(stored: StoredPartition) -> int:
    """The index of a row whose removal moves nothing the readiness contract checks but the count.

    Three conditions, and each closes a way the pair below would stop being an attribution.
    Its subject and its session must both appear on another row, because
    `PartitionCoverage.subjects` and `.dates` are what `evaluate_readiness` checks
    `required_subjects` and `required_dates` against -- pruning the only row of either would make
    the two halves differ in a *second* way. And its `available_time` must be below the
    partition's own newest, so that removing it cannot move `max_available_time` by itself.

    Chosen in sorted order rather than by position, so which row it is is a property of the
    partition and not of the order Tushare happened to serve it in.
    """
    dates = tuple(stored_date(value) for value in stored.column(PRICE_DATE_COLUMN).values)
    subject_counts: dict[str, int] = {}
    date_counts: dict[date, int] = {}
    for subject, day in zip(stored.subjects, dates, strict=True):
        subject_counts[subject] = subject_counts.get(subject, 0) + 1
        date_counts[day] = date_counts.get(day, 0) + 1
    newest = max(stored.clocks["available_time"])
    for index in sorted(
        range(stored.row_count), key=lambda position: (stored.subjects[position], dates[position])
    ):
        if (
            subject_counts[stored.subjects[index]] > 1
            and date_counts[dates[index]] > 1
            and stored.clocks["available_time"][index] < newest
        ):
            return index
    raise E2EEnvironmentError(
        f"no row of {stored.coverage.dataset} year={stored.coverage.year} shares both its "
        "subject and its session with another row and sits below the partition's own ceiling; "
        "there is nothing to prune cleanly"
    )


def delayed(stored: StoredPartition, index: int, *, until: datetime) -> StoredPartition:
    """`stored` with row `index` first knowable at `until`.

    All three of the later clocks move, not `available_time` alone: `Timeline` refuses an
    `ingested_time` or a `revision_time` earlier than the instant its row became available, so a
    row that moved one clock would be rejected by the *batch* -- a different gate than the read
    this is aimed at.
    """
    return stored.with_row(
        index, clocks={"available_time": until, "ingested_time": until, "revision_time": until}
    )


def without(stored: StoredPartition, index: int) -> StoredPartition:
    """`stored` with exactly row `index` gone, and nothing else touched."""
    return stored.taking([position for position in range(stored.row_count) if position != index])


def a_delayed_row(built_panel: BuiltPanel) -> tuple[int, datetime, datetime]:
    """The row every look-ahead test below delays, the instant it moves to, and the fetch clock.

    One function so the four tests are demonstrably injecting the same thing: the row is picked
    from the stored partition by `a_prunable_row`'s rule, and the fetch clock is an hour past the
    row's new availability because `ColumnarPanelBatch` refuses a batch holding a row that became
    knowable after its own `as_of` -- the write-side half of the same contract, which a real late
    fetch satisfies the same way.
    """
    stored = read_stored_partition(
        built_panel.store, dataset=SUSPENSION_DATASET, year=built_panel.year
    )
    knowable_at = read_as_of(built_panel) + PUBLICATION_LAG
    return a_prunable_row(stored), knowable_at, knowable_at + timedelta(hours=1)


# --- injection 1: a row nobody could have known about yet -------------------------------------


def test_a_row_that_became_knowable_after_the_read_blocks_its_partition_in_the_doctor(
    built_panel: BuiltPanel, cli_workspace: Path, injected_partition: InjectPartition
) -> None:
    """One delayed row, and `panel doctor` refuses the year it sits in.

    The finding has to name the instant as well as the code. `not_yet_knowable` alone is
    satisfied by a partition refused for any reason at all, and what is under test is that the
    refusal is *this row's*: `evaluate_readiness` prints the partition's `max_available_time`,
    which -- because `a_prunable_row` kept the injection off the partition's own ceiling -- is
    the injected instant and nothing else.

    The "before" call is not scaffolding. It is the assertion that this panel was sound at this
    `as_of` before anything was done to it, without which the "after" call proves only that the
    doctor says `not_yet_knowable` about something.
    """
    as_of = read_as_of(built_panel)
    # Resolved once, before the injection, and reused for both halves. `assessed` drops a
    # dataset holding a row newer than the read, which after the injection is exactly the
    # dataset under test -- asking the second time would quietly stop asking about it.
    datasets = assessed(built_panel)
    index, knowable_at, fetched_at = a_delayed_row(built_panel)

    before = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of)
    assert "not_yet_knowable" not in codes(before), findings(before, "not_yet_knowable")

    injected_partition(
        SUSPENSION_DATASET,
        built_panel.year,
        lambda partition: delayed(partition, index, until=knowable_at),
        fetched_at,
    )

    after = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of)
    blocked = findings(after, "not_yet_knowable")
    assert len(blocked) == 1, after["findings"]
    assert blocked[0]["severity"] == "blocking"
    assert blocked[0]["category"] == "unanswerable"
    assert SUSPENSION_DATASET in blocked[0]["datasets"]
    # `evaluate_readiness` prints the coverage record's instant, which the store normalises
    # to UTC; the injection was expressed in the panel's own Asia/Shanghai clock.
    assert knowable_at.astimezone(UTC).isoformat() in blocked[0]["detail"], blocked[0]["detail"]
    assert readiness_state(after, SUSPENSION_DATASET) == "blocked"
    assert after["is_clean"] is False


def test_the_same_partition_without_that_row_is_ready_at_the_same_as_of(
    built_panel: BuiltPanel, cli_workspace: Path, injected_partition: InjectPartition
) -> None:
    """The other half of the pair: prune the delayed row and the refusal goes with it.

    Same partition, same `as_of`, same fetch clock on the write. The two halves differ by one row
    and by nothing else, which is what makes the refusal attributable to that row rather than to
    the year it sits in -- without this half, `not_yet_knowable` firing is equally consistent
    with a gate that refuses whatever it is handed.

    The partition is written as *delayed then pruned* rather than simply left alone, so that the
    thing removed is demonstrably the thing injected; and the two censuses are checked afterwards
    to show the prune took one row and no dimension of the catalog with it.
    """
    as_of = read_as_of(built_panel)
    datasets = assessed(built_panel)
    original = read_stored_partition(
        built_panel.store, dataset=SUSPENSION_DATASET, year=built_panel.year
    )
    index, knowable_at, fetched_at = a_delayed_row(built_panel)

    injected_partition(
        SUSPENSION_DATASET,
        built_panel.year,
        lambda partition: without(delayed(partition, index, until=knowable_at), index),
        fetched_at,
    )

    payload = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of)
    assert "not_yet_knowable" not in codes(payload), findings(payload, "not_yet_knowable")
    assert readiness_state(payload, SUSPENSION_DATASET) == "ready"

    coverage = built_panel.store.read_coverage(SUSPENSION_DATASET, built_panel.year)
    assert coverage is not None
    assert coverage.row_count == original.row_count - 1
    assert coverage.max_available_time == original.coverage.max_available_time
    assert set(coverage.subjects) == set(original.subjects)
    assert {entry.event_date for entry in coverage.dates} == {
        stored_date(value) for value in original.column(PRICE_DATE_COLUMN).values
    }


def test_the_boundary_is_the_injected_rows_own_availability_instant(
    built_panel: BuiltPanel, injected_partition: InjectPartition
) -> None:
    """Readiness opens at the row's instant and is shut one microsecond earlier.

    The read-side counterpart of the `<=` that `tests/contract/panel/test_columnar_batch_parity.py`
    pins on the write side, asked through the SDK so the comparison runs where a library caller
    meets it. A microsecond is the store's own timestamp resolution, so this is the smallest
    difference the partition is able to express.
    """
    index, knowable_at, fetched_at = a_delayed_row(built_panel)
    injected_partition(
        SUSPENSION_DATASET,
        built_panel.year,
        lambda partition: delayed(partition, index, until=knowable_at),
        fetched_at,
    )
    sdk = OpenAlphaSDK(runtime_dir=built_panel.runtime_dir)

    def state(at: datetime) -> tuple[str, tuple[str, ...]]:
        entry = sdk.panel_readiness(
            datasets=(SUSPENSION_DATASET,),
            years=(built_panel.year,),
            as_of=at,
            exchange=built_panel.exchange,
            with_calendar=True,
        )[0]
        return entry.state, tuple(issue.code for issue in entry.issues)

    assert state(knowable_at) == ("ready", ())
    state_before, codes_before = state(knowable_at - timedelta(microseconds=1))
    assert state_before == "blocked"
    assert codes_before == ("not_yet_knowable",)


def test_the_injected_row_blocks_the_gate_and_the_three_faces_say_the_same_thing(
    built_panel: BuiltPanel,
    cli_workspace: Path,
    served: str,
    injected_partition: InjectPartition,
) -> None:
    """`data-check`, `GET /panel/gate` and `OpenAlphaSDK.panel_clearance` over one broken panel.

    The gate is what a downstream job runs, and its deliverable is an exit code on the CLI, a
    status code over HTTP and an object that raises rather than answering in the SDK. Three
    different ways to render the same verdict, so all three are asked -- on a panel whose only
    fault is the row this test put in it, at the `as_of` that row is invisible to.

    A named session is passed for the reason `test_the_gate_refuses_a_daily_dataset_that_no_
    session_corroborated` states: without one the daily-cadence datasets carry
    `unverified_daily_coverage` and the gate would refuse for a cause this injection did not
    create.
    """
    as_of = read_as_of(built_panel)
    session = built_panel.sessions()[-1]
    datasets = assessed(built_panel)
    index, knowable_at, fetched_at = a_delayed_row(built_panel)

    injected_partition(
        SUSPENSION_DATASET,
        built_panel.year,
        lambda partition: delayed(partition, index, until=knowable_at),
        fetched_at,
    )

    exit_code, cli = run_data_check(
        built_panel,
        cli_workspace,
        datasets=datasets,
        as_of=as_of,
        extra=("--session", session.isoformat()),
    )
    assert exit_code == 1
    assert cli["is_blocked"] is True
    assert "not_yet_knowable" in {block["code"] for block in cli["blocks"]}
    assert SUSPENSION_DATASET in cli["blocked_datasets"]

    status, over_http = http_get(
        served,
        "/api/v1/panel/gate",
        [
            *[("dataset", dataset) for dataset in datasets],
            ("year", str(built_panel.year)),
            ("as_of", as_of.isoformat()),
            ("exchange", built_panel.exchange),
            ("calendar", "true"),
            ("session", session.isoformat()),
        ],
    )
    assert status == 409, over_http
    assert over_http["is_blocked"] is True

    clearance = OpenAlphaSDK(runtime_dir=built_panel.runtime_dir).panel_clearance(
        datasets=datasets,
        years=(built_panel.year,),
        sessions=(session,),
        as_of=as_of,
        exchange=built_panel.exchange,
        with_calendar=True,
    )
    assert clearance.is_blocked is True
    in_process = {block.code for block in clearance.blocks}
    assert in_process == {block["code"] for block in over_http["blocks"]}
    assert in_process == {block["code"] for block in cli["blocks"]}
    assert sorted(clearance.blocked_datasets) == sorted(over_http["blocked_datasets"])


# --- injection 2: the third witness, on rows the provider published ---------------------------


def a_bar_the_return_check_reaches(built_panel: BuiltPanel) -> tuple[str, date]:
    """A security and a session `panel_doctor._return_path_check` computes a clean return for.

    Two conditions, and both are about making the "before" half of the pair mean something.

    All three inputs have to be real or the injected row is simply skipped: the check needs a bar
    on the session, a bar on the session before it, and a factor series for the same code, and it
    steps past anything missing one of the three. And the chosen bar must be one `session_returns`
    already accepts, because a live panel legitimately carries real `return_path_disagreement`
    findings -- the gate blocks 2026-08-07 on this panel over a genuine corporate-action
    disagreement -- and injecting into a security that was already disagreeing would make the
    "before" assertion fail for a reason that is not a defect.

    `session_returns` is called here to *choose* rather than to assert, which is why the choice is
    the first candidate in sorted order and the test below still puts its claim through the CLI.
    """
    calendar = built_panel.calendar
    as_of = read_as_of(built_panel)
    session = built_panel.sessions()[-1]
    previous = calendar.previous_trading_day(session)
    bars = load_daily_bars(
        built_panel.store, day=session, calendar=calendar, as_of=as_of, max_staleness=None
    )
    earlier = load_daily_bars(
        built_panel.store, day=previous, calendar=calendar, as_of=as_of, max_staleness=None
    )
    factors = load_adjustment_histories(
        built_panel.store, years=(built_panel.year,), as_of=as_of, max_staleness=None
    )
    reachable = sorted(set(bars) & set(earlier) & set(factors))
    assert reachable, (
        f"no security has a bar on {session}, a bar on {previous} and a factor series, so the "
        "return-path check computes nothing on this panel"
    )
    for ts_code in reachable:
        try:
            session_returns(
                bars[ts_code],
                previous_close=earlier[ts_code].close,
                previous_day=previous,
                factors=factors[ts_code],
            )
        except (AdjustmentError, PriceDataError):
            continue
        return ts_code, session
    raise E2EEnvironmentError(
        f"all {len(reachable)} securities with a bar on {session}, a bar on {previous} and a "
        "factor series already disagree about their own return, so there is no sound bar to "
        "corrupt; test_the_gate_opens_on_a_real_session_of_the_panel_it_was_given is where a "
        "whole market of disagreements is the finding"
    )


def row_of(stored: StoredPartition, *, subject: str, day: date) -> int:
    dates = tuple(stored_date(value) for value in stored.column(PRICE_DATE_COLUMN).values)
    for index in range(stored.row_count):
        if stored.subjects[index] == subject and dates[index] == day:
            return index
    raise AssertionError(f"{stored.coverage.dataset} has no row for {subject} on {day}")


def test_a_close_that_disagrees_with_its_own_pct_chg_is_caught_only_by_the_third_witness(
    built_panel: BuiltPanel, cli_workspace: Path, injected_partition: InjectPartition
) -> None:
    """`V2-P2-006` on live rows, in both directions: what sees this, and what cannot.

    Five percentage points onto one row's `pct_chg` leaves every catalog dimension of the
    partition untouched -- same subject, same session, same four clocks, same row count -- so
    readiness stays `ready`, and that is asserted rather than assumed. `_refuse_close_disagreement`
    compares two *pre_close* values and never reads `close`; `close_disagreements` compares
    `daily`'s close against `daily_basic`'s, which this injection did not move either. The only
    thing in this repository that reads a row's own statement of its own return is
    `session_returns`' third witness, so exactly one new finding must appear, it must be that
    one, and it must name this security, this session, `pct_chg` and the value injected.

    The injected size is checked against the published tolerance in the same test, because "it
    refused" is only evidence about the witness if the error was nowhere near the bound.
    """
    assert PCT_CHG_ERROR / 100.0 > MAX_PUBLISHED_RETURN_DISAGREEMENT * 100.0, (
        f"{PCT_CHG_ERROR} percentage points is not two orders of magnitude past "
        f"{MAX_PUBLISHED_RETURN_DISAGREEMENT} of return, so a refusal would be about the bound"
    )
    as_of = read_as_of(built_panel)
    datasets = assessed(built_panel)
    ts_code, session = a_bar_the_return_check_reaches(built_panel)
    stored = read_stored_partition(built_panel.store, dataset=DAILY_DATASET, year=built_panel.year)
    index = row_of(stored, subject=ts_code, day=session)
    corrupted = float(str(stored.column("pct_chg").values[index])) + PCT_CHG_ERROR
    extra = ("--session", session.isoformat())

    def about_this_bar(payload: Mapping[str, Any], code: str) -> list[Mapping[str, Any]]:
        return [
            finding
            for finding in findings(payload, code)
            if ts_code in finding["items"] and session.isoformat() in finding["dates"]
        ]

    before = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of, extra=extra)
    assert readiness_state(before, DAILY_DATASET) == "ready"
    assert not about_this_bar(before, "return_path_disagreement")

    injected_partition(
        DAILY_DATASET,
        built_panel.year,
        lambda partition: partition.with_row(index, values={"pct_chg": corrupted}),
        stored.coverage.as_of,
    )

    after = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of, extra=extra)

    # Nothing the catalog censuses moved, so nothing that reads the catalog may have noticed.
    assert readiness_state(after, DAILY_DATASET) == "ready"
    coverage = built_panel.store.read_coverage(DAILY_DATASET, built_panel.year)
    assert coverage is not None
    assert coverage.row_count == stored.coverage.row_count
    assert coverage.max_available_time == stored.coverage.max_available_time
    assert coverage.subjects == stored.coverage.subjects

    caught = about_this_bar(after, "return_path_disagreement")
    assert len(caught) == 1, after["findings"]
    assert caught[0]["severity"] == "warning"
    assert "pct_chg" in caught[0]["detail"], caught[0]["detail"]
    assert repr(corrupted) in caught[0]["detail"], caught[0]["detail"]

    # The check that compares two closes is blind to this by construction, and stays blind.
    assert about_this_bar(after, "close_disagreement") == about_this_bar(
        before, "close_disagreement"
    )

    exit_code, gate = run_data_check(
        built_panel, cli_workspace, datasets=datasets, as_of=as_of, extra=extra
    )
    assert exit_code == 1
    assert "return_path_disagreement" in {block["code"] for block in gate["blocks"]}


# --- injection 3: a correction filed after the fact -------------------------------------------


CORRECTED_WINDOW: str = "09:30-10:00"
"""The halt window a correction restates one row to, when the stored one is blank.

A window rather than a `suspend_type`, and the reason is a live census rather than taste: a
security carrying both an `R` and an `S` row for one session used to be refused and is
*reconciled* now (`_reconcile_states` prefers the `S`), because 73 of 334,289 `(security,
session)` pairs over 2015..2026 carry exactly that pair from one request. What is still refused
is two rows of one security saying different things about *how much* of the session it was
halted for -- an untimed row against a timed one, which is what this restates."""


def a_halted_row(stored: StoredPartition) -> int:
    """The first row, in sorted order, whose `suspend_type` is a halt rather than a resumption.

    A halt is the only state a correction can contradict. `_reconcile_states` prefers the halt
    over a resumption by design, so restating an `R` row would be reconciled away and the test
    would assert nothing.
    """
    types = stored.column(SUSPENSION_TYPE_COLUMN).values
    dates = tuple(stored_date(value) for value in stored.column(PRICE_DATE_COLUMN).values)
    for index in sorted(
        range(stored.row_count), key=lambda position: (stored.subjects[position], dates[position])
    ):
        if str(types[index]) == HALT_TYPE:
            return index
    raise E2EEnvironmentError(
        f"{stored.coverage.dataset} year={stored.coverage.year} carries no {HALT_TYPE!r} row, so "
        f"there is no halt for a correction to contradict (it holds {set(map(str, types))})"
    )


def corrected(stored: StoredPartition, index: int, *, published_at: datetime) -> StoredPartition:
    """`stored` with a second, contradicting version of row `index` appended.

    A halt is the panel plane's nearest thing to a restatement, and the column that carries the
    contradiction is `suspend_timing`: `build_suspension_day` reads an untimed `S` as a whole-day
    halt and a timed one as an intraday interruption, and refuses a security that is stored as
    both on one session -- "there is no finer row to prefer ... nothing in the corpus decides
    between them". So the appended row keeps the original's subject, session and `suspend_type`
    and restates only how much of the session the halt covered, which is exactly the shape of a
    correction filed after the original.

    Its `available_time` is the correction's own publication instant, later than the original's.
    That is what makes the pair a revision rather than a duplicate, and it is what the earlier
    read has to refuse to see.
    """
    stored_timing = stored.column(SUSPENSION_TIMING_COLUMN).values[index]
    restated = None if stored_timing is not None else CORRECTED_WINDOW
    grown = stored.taking((*range(stored.row_count), index))
    return grown.with_row(
        grown.row_count - 1,
        clocks={
            "available_time": published_at,
            "ingested_time": published_at,
            "revision_time": published_at,
        },
        values={SUSPENSION_TIMING_COLUMN: restated},
    )


def test_a_correction_published_after_the_read_serves_neither_version(
    built_panel: BuiltPanel, cli_workspace: Path, injected_partition: InjectPartition
) -> None:
    """The panel plane's honest answer to "does the read hand back the pre-correction value".

    It does not, and it does not hand back the corrected one either. The earlier question is
    refused rather than answered from the version that was current then. That is fail-closed,
    which is the direction this plane is built to fail in, and it is a different contract from
    `StatementHistory.filing_for`'s, which does answer from the earlier version on a plane
    `panel build` has no target for.

    Asked at the *later* clock the read refuses again, for the other reason: two stored rows now
    disagree about one security's state on one session, which is the row-level contradiction no
    census dimension can see and which `domain_rebuild_refused` exists for. Both refusals are
    asserted, because either one alone is consistent with a reader that silently picked a
    version.

    ## Which rule refuses the earlier read, measured rather than assumed (`V2-P5-051`)

    **This test named `not_yet_knowable`, and that rule can no longer reach this dataset.** The
    reading it recorded -- "`PanelStore.query()` carries no row-level `available_time` predicate
    ... so the earlier question is refused for the whole partition" -- was `read_if_ready`'s, and
    `V2-P4-076` took `load_suspensions` off it. `panel_ingest.load_suspensions`' own docstring is
    the record of the move and of why: a halt on the newest session made every earlier cross
    section in that year unscreenable. It now reads `_read_visible_event_dated_rows`, which
    applies the row-level predicate the sentence above says does not exist and then reconciles
    what survived against the catalog's per-event-date census.

    Measured on the panel this suite fetched, *before* anything was injected: `load_suspensions`
    answers at 2026-08-24T17:30+08:00 while the `suspend_d` 2026 partition's `max_available_time`
    is 2026-08-26T16:30+08:00, two days later. Under `read_if_ready` that is textbook
    `not_yet_knowable` and it does not fire. So the census is not firing *early* and masking the
    rule this test meant to exercise -- there is no longer a second rule behind it to mask.

    **And the census is the right answer for this injection rather than an accident of ordering.**
    The two are about different things -- a partition's readiness against a single row's
    knowability -- but the correction this test files is precisely a row whose knowability
    contradicts the dataset's own availability rule: `suspend_d` is `ClockStrategy.daily_close`,
    so a halt dated 2026-04-28 is knowable at that session's 16:30 and cannot legitimately become
    available four months later. The census sees the partition claim 32 rows on that event date
    while the visible slice carries 31, and refuses because -- in `load_suspensions`' own words --
    "an answer short by it is indistinguishable from one where the row does not exist". That is
    the same fail-closed outcome the paragraph above asks for, reached by the rule that now owns
    the door. The assertion below therefore pins the census sentence *and* the arithmetic that
    separates the two answers: a reader that had silently served the pre-correction version would
    return the corpus rather than raise, and `pytest.raises` is what catches that.
    """
    as_of = read_as_of(built_panel)
    original = read_stored_partition(
        built_panel.store, dataset=SUSPENSION_DATASET, year=built_panel.year
    )
    index = a_halted_row(original)
    published_at = as_of + PUBLICATION_LAG
    later = published_at + timedelta(hours=1)

    assert load_suspensions(
        built_panel.store, years=(built_panel.year,), as_of=as_of, max_staleness=None
    ), "the halt corpus read back empty before anything was injected"

    injected_partition(
        SUSPENSION_DATASET,
        built_panel.year,
        lambda partition: corrected(partition, index, published_at=published_at),
        later,
    )

    corrected_session = stored_date(original.column(PRICE_DATE_COLUMN).values[index])
    with pytest.raises(PanelStorageError, match=r"date census counts") as census:
        load_suspensions(
            built_panel.store, years=(built_panel.year,), as_of=as_of, max_staleness=None
        )
    refusal = str(census.value)
    # The census has to name the event date of the row this test injected and the arithmetic that
    # found it, not merely refuse: a partition-wide refusal that happened to fire for some other
    # reason would satisfy a bare `pytest.raises` and prove nothing about *this* correction. The
    # count is one because the injection appended exactly one row, so it is a fact about what was
    # done here rather than about which panel this ran against.
    assert SUSPENSION_DATASET in refusal, refusal
    assert corrected_session.isoformat() in refusal, refusal
    assert "1 row(s) withheld in all" in refusal, refusal
    # The falsified reading, kept as an assertion so that putting this door back on
    # `read_if_ready` fails here rather than quietly restoring the wall `V2-P4-076` removed.
    assert "not_yet_knowable" not in refusal, refusal
    with pytest.raises(SuspensionError, match=r"nothing in the corpus decides between them"):
        load_suspensions(
            built_panel.store, years=(built_panel.year,), as_of=later, max_staleness=None
        )

    payload = run_doctor(built_panel, cli_workspace, datasets=(SUSPENSION_DATASET,), as_of=later)
    refused = findings(payload, "domain_rebuild_refused")
    assert refused, payload["findings"]
    assert original.subjects[index] in " ".join(finding["detail"] for finding in refused)


# --- injection 4: a legal Parquet file whose contents changed behind the store ----------------

INJECTED_SUBJECT: str = "999999.ZZ"
"""The identity the appended row carries.

A name no exchange issues, so the row cannot be confused with anything the build fetched, and
`assessed`'s subject census cannot accidentally have contained it already."""


def test_a_valid_parquet_with_one_row_appended_behind_the_store_blocks_its_partition(
    built_panel: BuiltPanel, cli_workspace: Path, withheld_partition: WithholdPartition
) -> None:
    """The third shape of file tampering, and the one the chain had no case for.

    `test_panel_chain_online.py` covers two: a partition file **deleted**
    (`partition_file_missing`) and one **replaced by bytes that are not Parquet**
    (`partition_file_unreadable`). Both are decided by facts about the file's existence and its
    first and last eight bytes. This is the third: a file that is still a perfectly valid
    Parquet file, still readable, still registered, and no longer holding what the catalog says
    -- which is what P2's product acceptance drove a real `stock_basic` partition into, one
    appended row with an `available_time` in 2035, and watched `panel doctor` answer
    `READY ... rows=152` over a 153-row file, `data-check` answer `CLEARED` with exit 0, and
    `load_stock_universe(as_of=2026-08-11).listed_on(2024-07-02)` come back with the injected
    security in it.

    The Parquet magic is asserted *after* the swap on purpose. Without it this test would be
    indistinguishable from the `partition_file_unreadable` case it exists to be different from,
    and a helper that quietly produced rubble would make it pass for the wrong reason.

    Injected through `withheld_partition` rather than through `injected_partition`: the whole
    point is a write that did **not** go through the store, so the catalog is left describing
    the previous file. `injected_partition` re-runs the real writer and makes the catalog agree,
    which is the right instrument for the three injections above and would erase this one.
    """
    as_of = read_as_of(built_panel)
    datasets = assessed(built_panel)
    store = built_panel.store
    path = catalogued_path(store, dataset=SUSPENSION_DATASET, year=built_panel.year)
    coverage = store.read_coverage(SUSPENSION_DATASET, built_panel.year)
    assert coverage is not None
    tampered = parquet_with_a_row_appended(
        path,
        subject=INJECTED_SUBJECT,
        available_time=as_of + PUBLICATION_LAG,
        workspace=cli_workspace,
    )

    before = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of)
    assert readiness_state(before, SUSPENSION_DATASET) == "ready"
    assert "partition_row_count_mismatch" not in codes(before)

    withheld_partition(SUSPENSION_DATASET, built_panel.year, replacement=tampered)

    # Still a Parquet file at both ends, so this is not the `partition_file_unreadable` case:
    # every fact the store had before this task's change still agrees with the catalog.
    written = path.read_bytes()
    assert written[: len(PARQUET_MAGIC)] == PARQUET_MAGIC
    assert written[-len(PARQUET_MAGIC) :] == PARQUET_MAGIC

    after = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of)
    blocked = findings(after, "partition_row_count_mismatch")
    assert len(blocked) == 1, after["findings"]
    assert blocked[0]["severity"] == "blocking"
    assert blocked[0]["category"] == "inconsistent"
    assert SUSPENSION_DATASET in blocked[0]["datasets"]
    # Both numbers, matched with their surrounding words: the detail also carries the
    # partition's absolute path, and a bare `str(2293) in detail` could pass on a path digit.
    assert f"describing {coverage.row_count} row(s)" in blocked[0]["detail"], blocked[0]["detail"]
    assert f"says it holds {coverage.row_count + 1};" in blocked[0]["detail"], blocked[0]["detail"]
    assert readiness_state(after, SUSPENSION_DATASET) == "blocked"
    assert after["is_clean"] is False

    # And the row does not reach an answer, which is the half that makes this a look-ahead
    # finding rather than a bookkeeping one.
    with pytest.raises(PanelStorageError, match=r"partition_row_count_mismatch"):
        load_suspensions(
            built_panel.store, years=(built_panel.year,), as_of=as_of, max_staleness=None
        )

    exit_code, gate = run_data_check(
        built_panel,
        cli_workspace,
        datasets=datasets,
        as_of=as_of,
        extra=("--session", built_panel.sessions()[-1].isoformat()),
    )
    assert exit_code == 1
    assert "partition_row_count_mismatch" in {block["code"] for block in gate["blocks"]}
    assert SUSPENSION_DATASET in gate["blocked_datasets"]


def test_a_value_edited_in_place_is_invisible_to_the_census_and_visible_to_the_witness(
    built_panel: BuiltPanel, cli_workspace: Path, withheld_partition: WithholdPartition
) -> None:
    """The residue of the check above, asserted rather than described.

    The footer's row count catches every row inserted or removed behind the store. It cannot
    catch an edit that changes a value *in place*, because that moves none of the three facts
    the store reads off the file -- the magic at both ends, the file's presence, and the count
    -- and none of the catalog's own. `KNOWN_STORAGE_LIMITATIONS`'
    `a_value_edited_in_place_leaves_the_census_intact` says so, and a disclosure with no test
    under it is the prose this whole module exists to replace.

    So both directions are asserted on one file. The **census** stays `ready` and its coverage
    record is unchanged in every dimension. The **witness** that reads the column sees it:
    `session_returns`' third witness recomputes the session's return from `close` and
    `pre_close` and finds it cannot be the `pct_chg` the row now states, which is
    `return_path_disagreement`, and the gate blocks on it. That is the boundary stated exactly
    -- what protects a column is a check that reads it, and a column no cross-check reads is
    protected by nothing.

    The same defect as `test_a_close_that_disagrees_with_its_own_pct_chg_is_caught_only_by_the_
    third_witness`, aimed one layer down. That one goes through `write_panel_batch` and takes
    185s because it must; this one edits the file, which is the whole subject here, and takes
    seconds.
    """
    as_of = read_as_of(built_panel)
    datasets = assessed(built_panel)
    store = built_panel.store
    ts_code, session = a_bar_the_return_check_reaches(built_panel)
    # One security's rows rather than the whole 796,497-row partition: this test edits the file
    # and never rebuilds a batch from it, so `read_stored_partition`'s full transposition would
    # be paid for nothing. The date cell is taken as the writer stored it, so the `WHERE` the
    # tamper builds matches on the same value rather than on a re-derived text form.
    held = store.query(
        DAILY_DATASET,
        year=built_panel.year,
        columns=(PRICE_DATE_COLUMN, "pct_chg"),
        filters={"subject": ts_code},
    )
    (day_cell, published) = next(row for row in held if stored_date(row[0]) == session)
    corrupted = float(str(published)) + PCT_CHG_ERROR
    path = catalogued_path(store, dataset=DAILY_DATASET, year=built_panel.year)
    original = store.read_coverage(DAILY_DATASET, built_panel.year)
    assert original is not None
    extra = ("--session", session.isoformat())

    def about_this_bar(payload: Mapping[str, Any], code: str) -> list[Mapping[str, Any]]:
        return [
            finding
            for finding in findings(payload, code)
            if ts_code in finding["items"] and session.isoformat() in finding["dates"]
        ]

    before = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of, extra=extra)
    assert readiness_state(before, DAILY_DATASET) == "ready"
    assert not about_this_bar(before, "return_path_disagreement")

    tampered = parquet_with_one_cell_changed(
        path,
        subject=ts_code,
        key_column=PRICE_DATE_COLUMN,
        key_value=day_cell,
        column="pct_chg",
        value=corrupted,
        workspace=cli_workspace,
    )
    withheld_partition(DAILY_DATASET, built_panel.year, replacement=tampered)

    after = run_doctor(built_panel, cli_workspace, datasets=datasets, as_of=as_of, extra=extra)

    # The census is blind to this, and that is the disclosed limitation rather than a bug.
    assert readiness_state(after, DAILY_DATASET) == "ready"
    assert "partition_row_count_mismatch" not in codes(after)
    coverage = store.read_coverage(DAILY_DATASET, built_panel.year)
    assert coverage is not None
    assert coverage.row_count == original.row_count
    assert coverage.max_available_time == original.max_available_time
    assert coverage.subjects == original.subjects
    # The edited cell really is on disk, so "the census is blind" is a statement about the
    # census rather than about a tamper that did not land.
    assert corrupted in {
        float(str(row[1]))
        for row in store.query(
            DAILY_DATASET,
            year=built_panel.year,
            columns=(PRICE_DATE_COLUMN, "pct_chg"),
            filters={"subject": ts_code},
        )
    }
    # The disclosure is on the artifact the reader is handed, not only in a docstring.
    assert "a_value_edited_in_place_leaves_the_census_intact" in {
        limitation["code"] for limitation in after["limitations"]
    }

    # The witness that reads the column is not blind to it.
    caught = about_this_bar(after, "return_path_disagreement")
    assert len(caught) == 1, after["findings"]
    assert caught[0]["severity"] == "warning"
    assert "pct_chg" in caught[0]["detail"], caught[0]["detail"]
    assert repr(corrupted) in caught[0]["detail"], caught[0]["detail"]

    exit_code, gate = run_data_check(
        built_panel, cli_workspace, datasets=datasets, as_of=as_of, extra=extra
    )
    assert exit_code == 1
    assert "return_path_disagreement" in {block["code"] for block in gate["blocks"]}


# --- the harness, and the reverse direction ---------------------------------------------------


def test_restoring_a_rewritten_partition_reproduces_the_catalog_row_the_build_wrote(
    built_panel: BuiltPanel,
) -> None:
    """The harness's own falsifiability check, and the reason these tests may share one panel.

    Every injection above mutates a session-scoped panel that twenty-odd other tests read, and
    what makes that safe is that restoration goes back through `write_panel_batch` rather than
    patching anything: the Parquet file is rebuilt, the coverage census is recomputed from the
    batch and the content hash is recomputed from the rows. So the round trip has to be exact --
    a float that did not survive DuckDB, a clock that lost its zone, a null that came back as
    something else would all leave a different content hash behind.

    Written without the `injected_partition` fixture on purpose: this test *is* the fixture's
    contract, so it performs both halves itself and asserts the catalog in between, where a test
    that delegated the restore could only assert what the fixture chose to do.
    """
    store = built_panel.store
    original = read_stored_partition(store, dataset=SUSPENSION_DATASET, year=built_panel.year)
    assert original.coverage.partition_content_hash is not None
    index, knowable_at, fetched_at = a_delayed_row(built_panel)

    rewrite_partition(store, delayed(original, index, until=knowable_at), as_of=fetched_at)
    injured = store.read_coverage(SUSPENSION_DATASET, built_panel.year)
    assert injured is not None
    assert injured.partition_content_hash != original.coverage.partition_content_hash
    assert injured.max_available_time == knowable_at

    rewrite_partition(
        store,
        original,
        as_of=original.coverage.as_of,
        fetched_at=original.coverage.fetched_at,
    )
    restored = store.read_coverage(SUSPENSION_DATASET, built_panel.year)
    assert restored is not None
    assert restored.partition_content_hash == original.coverage.partition_content_hash
    assert restored.row_count == original.coverage.row_count
    assert restored.max_available_time == original.coverage.max_available_time
    assert restored.last_event_time == original.coverage.last_event_time
    assert restored.fetched_at == original.coverage.fetched_at
    assert restored.subjects == original.coverage.subjects
    assert restored.fields == original.coverage.fields
    assert restored.dates == original.coverage.dates


def test_the_untouched_panel_is_still_ready_after_every_injection_above(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """A red-team gate that refuses a sound panel protects nothing, and neither does a harness
    that leaves one injured.

    Asked with the same arguments every injected test above uses -- the same datasets, the same
    `as_of`, the same named session -- so it is their shared control in both directions: the
    verdict on a panel with nothing wrong with it is clean, and the panel this module has been
    breaking all module long is that panel again.
    """
    as_of = read_as_of(built_panel)
    payload = run_doctor(
        built_panel,
        cli_workspace,
        datasets=assessed(built_panel),
        as_of=as_of,
        extra=("--session", built_panel.sessions()[-1].isoformat()),
    )
    assert "not_yet_knowable" not in codes(payload), findings(payload, "not_yet_knowable")
    refused = findings(payload, "domain_rebuild_refused")
    assert "domain_rebuild_refused" not in codes(payload), refused
    for dataset in assessed(built_panel):
        assert readiness_state(payload, dataset) == "ready", payload["datasets"]
