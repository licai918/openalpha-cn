"""`V2-P4-069`: reading N partitions must not assess readiness N times over.

`PanelStore.read_if_ready` and `PanelStore.read_visible_at` each run `assess_readiness` over the
**whole** requirement and then read **one** year, so a caller reading a multi-year history used
to re-evaluate every year once per year: N partitions cost N**2 catalog round trips to answer a
question whose verdict is identical all N times. `PanelStore.assessed()` is the fix -- one
verdict, and the per-year reads it licenses -- and these tests are the measurement kept as its
gate.

## Measured, not estimated -- and the estimate it replaced was wrong by three orders of magnitude

`load_stock_universe`'s docstring said this re-evaluation was "milliseconds". `V2-P4-059`
measured it on a 36-year registry over `V2-P4-004`'s 5,545-security market: **4.0 s** for one
call, of which cProfile attributed 4.59 s to `assess_readiness` across **1,296** `_read_coverage`
round trips, against **0.21 s** for the Parquet the read actually wanted. The old sentence was
true of the fixtures it was written against -- a handful of years -- and a whole history is 36.

Reproduced here on a store of 20 securities per partition, so that the cost is unmistakably the
catalog rather than the data:

| partitions | assessments | coverage reads | per partition | seconds |
|------------|-------------|----------------|---------------|---------|
| 6          | 6           | 36             | 6.0           | 0.193   |
| 12         | 12          | 144            | 12.0          | 0.546   |
| 18         | 18          | 324            | 18.0          | 1.111   |
| 24         | 24          | 576            | 24.0          | 1.875   |
| 36         | 36          | 1,296          | 36.0          | 4.087   |

The 36-partition row reproduces `V2-P4-059`'s 1,296 and 4.0 s exactly, on 720 rows rather than
on a real market -- which is the sharpest thing the reproduction says: none of this time is data.

The same harness after the fix:

| partitions | assessments | coverage reads | per partition | seconds |
|------------|-------------|----------------|---------------|---------|
| 6          | 1           | 6              | 1.0           | 0.151   |
| 12         | 1           | 12             | 1.0           | 0.226   |
| 24         | 1           | 24             | 1.0           | 0.437   |
| 36         | 1           | 36             | 1.0           | 0.727   |
| 72         | 1           | 72             | 1.0           | 1.256   |

One assessment, one coverage read per partition, and 72 partitions cost twice what 36 do --
which is the linearity rather than a smaller constant, and the reason a size beyond the one the
defect was reported at is in the table.

## Why the assertions count round trips rather than seconds

Those seconds are here as context and are not what anything below asserts. This repository runs
several agents on one machine, and the measurements above were taken at a load average around
5; a stopwatch assertion calibrated on an idle box is a test that fails for a reason that has
nothing to do with the code. `_read_coverage` calls are exact integers, they are what cProfile
found the time in, and they are deterministic across runs.

The tests compare two sizes rather than bounding one, because that is what separates the two
answers: doubling the partition count doubles a linear cost and quadruples a quadratic one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel import store as store_module
from openalpha_cn.panel.catalog import KNOWN_STORAGE_LIMITATIONS, ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import write_panel_batch

DATASET = "prices_daily"
SUBJECTS = tuple(f"{index:06d}.SZ" for index in range(20))
NEWEST_YEAR = 2026
AS_OF = datetime(2027, 1, 5, tzinfo=UTC)
FROZEN = datetime(2027, 1, 5, 1, 0, tzinfo=UTC)

SMALL = 8
LARGE = 16
"""The two partition counts every assertion here compares.

`LARGE == 2 * SMALL`, because the assertions are about what doubling costs. Smaller than the 36
the table above reports: the quadratic is already unambiguous at 8 against 16 (64 against 256
coverage reads), and 36 partitions cost four seconds a call, which is not a price a suite that
runs on every change should pay to re-derive a number this module's docstring already records.
"""

GROWTH_CEILING = 3.0
"""Cost at `LARGE` over cost at `SMALL` must stay below this.

Linear doubles (2.0), quadratic quadruples (4.0). Three sits between them with room on both
sides, so the assertion can fail for the reason it names and not for jitter -- and there is no
jitter here anyway, since it compares two integers.
"""


def _close(year: int, index: int) -> float:
    """A value that names the partition it came from.

    **A mutation sweep is why this is a function.** Every partition used to carry the identical
    `10.0 + index`, and on that fixture "the read answered year `k`" and "the read answered year
    0 over and over" produce the *same row count* -- so three mutants that pinned the scan to
    `requirement.years[0]` survived every assertion here. A count is not an answer; this makes
    the rows say which year they are from, and `test_the_cheaper_read_still_returns_every_row_of
    _every_partition` holds them against the year it asked for.
    """
    return float(year) * 1000.0 + float(index)


def _batch(year: int) -> ColumnarPanelBatch:
    """One partition: every security on one event date, so a dropped year is 20 missing rows.

    Written at a `written_at` after its own session, because `ColumnarPanelBatch` refuses a
    batch whose rows became knowable after its `as_of` -- the point-in-time check, which is a
    different thing from the readiness this file measures and would otherwise stop the fixture
    existing at all.
    """
    event = datetime(year, 6, 2, 7, 0, tzinfo=UTC)
    available = datetime(year, 6, 2, 8, 30, tzinfo=UTC)
    written_at = datetime(year, 6, 3, tzinfo=UTC)
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=DATASET,
        kind="daily",
        as_of=written_at,
        fetched_at=written_at,
        status="success",
        subjects=SUBJECTS,
        timeline=TimelineColumns(
            event_time=(event,) * len(SUBJECTS),
            available_time=(available,) * len(SUBJECTS),
            ingested_time=(available,) * len(SUBJECTS),
            revision_time=(available,) * len(SUBJECTS),
        ),
        columns=(
            PanelColumn(
                "close", "float", tuple(_close(year, index) for index in range(len(SUBJECTS)))
            ),
        ),
    )


@dataclass
class _ReadCost:
    """What one full-history read cost, in the unit cProfile found the time in."""

    partitions: int
    assessments: int = 0
    coverage_reads: int = 0
    rows_read: int = 0
    years_read: tuple[int, ...] = field(default=())
    closes_by_year: dict[int, tuple[float, ...]] = field(default_factory=dict)
    """What each year's read actually answered with, keyed by the year that was asked for."""


@pytest.fixture
def measured_history_read(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _ReadCost]:
    """Build an `N`-partition store and read every year, counting the catalog round trips."""

    def _run(tmp_path: Path, partitions: int, *, door: str) -> _ReadCost:
        cost = _ReadCost(partitions=partitions)
        store = PanelStore(tmp_path / f"panel-{partitions}-{door}", clock=lambda: FROZEN)
        years = tuple(range(NEWEST_YEAR - partitions + 1, NEWEST_YEAR + 1))
        for year in years:
            write_panel_batch(store, _batch(year), year=year)
        requirement = ReadinessRequirement(
            dataset=DATASET,
            as_of=AS_OF,
            years=years,
            required_dates=None,
            required_subjects=None,
            required_fields=("close",),
            max_staleness=None,
        )

        real_coverage = store_module._read_coverage
        real_states = PanelStore._partition_states

        def counting_coverage(*args: Any, **kwargs: Any) -> Any:
            cost.coverage_reads += 1
            return real_coverage(*args, **kwargs)

        def counting_states(self: PanelStore, target: ReadinessRequirement) -> Any:
            cost.assessments += 1
            return real_states(self, target)

        monkeypatch.setattr(store_module, "_read_coverage", counting_coverage)
        monkeypatch.setattr(PanelStore, "_partition_states", counting_states)

        assessed = store.assessed(requirement)
        for year in years:
            outcome = (
                assessed.read(year=year, columns=("close",))
                if door == "read_if_ready"
                else assessed.read_visible_at(year=year, columns=("close",))
            )
            assert not outcome.is_blocked, outcome.readiness.issues
            cost.rows_read += len(outcome.rows)
            cost.closes_by_year[year] = tuple(sorted(float(row[-1]) for row in outcome.rows))
        cost.years_read = years
        monkeypatch.undo()
        return cost

    return _run


@pytest.fixture(params=("read_if_ready", "read_visible_at"))
def door(request: pytest.FixtureRequest) -> Iterator[str]:
    """Both doors, because both run the same whole-requirement assessment per call.

    `V2-P4-076` moved five loaders from the first onto the second and recorded that it left the
    quadratic untouched and made it fractionally worse -- one partition-scope assessment before
    the loop plus one census read per year, 1,332 coverage lookups against 1,296. Fixing only
    the door named in the row would therefore have left the measured caller -- the 36-year
    registry read, which is now on `read_visible_at` -- exactly as slow as it was.
    """
    yield str(request.param)


def test_reading_every_partition_does_not_re_evaluate_every_partition_each_time(
    tmp_path: Path,
    measured_history_read: Callable[..., _ReadCost],
    door: str,
) -> None:
    """The row's acceptance: the read path's readiness evaluations must not grow with N**2.

    Before the fix, `coverage_reads` is exactly `N**2` on either door -- 64 at `N=8` and 256 at
    `N=16`, a ratio of 4.0. After it, the verdict is taken once and the reads are the N the
    caller asked for.
    """
    small = measured_history_read(tmp_path, SMALL, door=door)
    large = measured_history_read(tmp_path, LARGE, door=door)

    ratio = large.coverage_reads / max(small.coverage_reads, 1)
    assert ratio < GROWTH_CEILING, (
        f"{door}: reading {SMALL} partitions cost {small.coverage_reads} coverage reads and "
        f"reading {LARGE} cost {large.coverage_reads} -- a factor of {ratio:.2f} for twice the "
        "partitions. The whole requirement is being re-assessed once per year read"
    )
    assert large.assessments <= 2, (
        f"{door}: {large.assessments} readiness assessments for one history read of "
        f"{LARGE} partitions; the verdict is identical across all of them"
    )


def test_the_cheaper_read_still_returns_every_row_of_every_partition(
    tmp_path: Path,
    measured_history_read: Callable[..., _ReadCost],
    door: str,
) -> None:
    """The sentinel, without which both assertions above are satisfied by a store that reads
    nothing at all -- which is precisely the shape a bad fold of the loop would take.

    **The count alone was not enough, and a mutation sweep is what said so.** Every partition
    holds one event date for all 20 securities, so a read of `N` years that kept its rows
    returns `20 * N` of them -- and so does a read that answered with year 0's rows `N` times.
    Three mutants pinning the scan to `requirement.years[0]` survived this file until `_close`
    made each partition's values name their own year. So the assertion is now on *which* rows
    came back, per year, and the count is the weaker half kept beside it.
    """
    expected = tuple(sorted(_close(0, index) for index in range(len(SUBJECTS))))

    for partitions in (SMALL, LARGE):
        cost = measured_history_read(tmp_path, partitions, door=door)

        assert cost.rows_read == len(SUBJECTS) * partitions
        assert len(cost.years_read) == partitions
        assert set(cost.closes_by_year) == set(cost.years_read)
        for year in cost.years_read:
            assert cost.closes_by_year[year] == tuple(
                value + float(year) * 1000.0 for value in expected
            ), (
                f"{door}: the read of {year} answered with "
                f"{cost.closes_by_year[year][:3]}..., which is not that partition's own rows. "
                "A read pinned to one year of the scope returns the right count and the wrong "
                "answer"
            )


def test_the_scope_declares_what_it_stops_checking_per_read() -> None:
    """The residue, as a registry entry rather than as a paragraph in a docstring.

    A scope reads each partition file's three physical facts -- presence, Parquet's magic at
    both ends, and the footer's row count -- once, before the first read, instead of before
    every read. So a row appended to year `k`'s file after year 0 was read and before year `k`
    is read escapes the gate, in a window one loop wide where the per-call door left none. That
    is a narrowed version of the injection `V2-P1`'s product acceptance measured, so it is
    disclosed by code rather than described, and this assertion is what puts the code in
    executable test source where `tests/unit/test_known_limitation_registries.py` can find it.
    """
    codes = {limitation.code for limitation in KNOWN_STORAGE_LIMITATIONS}

    assert "an_assessed_read_scope_checks_each_partition_file_once_and_not_once_per_read" in codes


def test_the_unscoped_doors_read_the_year_they_were_asked_for(tmp_path: Path) -> None:
    """`PanelStore.read_if_ready` and `read_visible_at` on a **multi-year** requirement.

    Both are now one line on top of `assessed`, and a line that forwards the wrong argument is
    the whole failure mode a delegation has. The tests above drive `AssessedPanelRead` directly,
    so they exercise the scope and not the two public doors' forwarding -- measured: a mutant
    replacing `year=year` with `year=requirement.years[0]` inside `read_visible_at`'s delegation
    survived every other assertion in this file.

    Multi-year is the whole point: on a one-year requirement `requirement.years[0] == year`, so
    the fixture would agree with the mutant and prove nothing.
    """
    store = PanelStore(tmp_path / "panel-doors", clock=lambda: FROZEN)
    years = (NEWEST_YEAR - 2, NEWEST_YEAR - 1, NEWEST_YEAR)
    for year in years:
        write_panel_batch(store, _batch(year), year=year)
    requirement = ReadinessRequirement(
        dataset=DATASET,
        as_of=AS_OF,
        years=years,
        required_dates=None,
        required_subjects=None,
        required_fields=("close",),
        max_staleness=None,
    )

    for year in years:
        expected = tuple(sorted(_close(year, index) for index in range(len(SUBJECTS))))
        gated = store.read_if_ready(requirement, year=year, columns=("close",))
        visible = store.read_visible_at(requirement, year=year, columns=("close",))

        assert tuple(sorted(float(row[-1]) for row in gated.rows)) == expected
        assert tuple(sorted(float(row[-1]) for row in visible.rows)) == expected


def test_one_year_read_through_either_door_still_assesses_that_year(tmp_path: Path) -> None:
    """The other direction: a single-year read must not have become *un*-gated.

    The fix moves the assessment out of the per-year call and into a scope the caller opens, so
    the failure mode to guard against is a `read` that skips the gate rather than sharing it.
    `read_if_ready`'s own contract is unchanged -- one call is still one assessment plus one
    read -- and this is the assertion that says so.
    """
    store = PanelStore(tmp_path / "panel-one", clock=lambda: FROZEN)
    write_panel_batch(store, _batch(NEWEST_YEAR), year=NEWEST_YEAR)
    requirement = ReadinessRequirement(
        dataset=DATASET,
        as_of=AS_OF,
        years=(NEWEST_YEAR,),
        required_dates=None,
        required_subjects=None,
        required_fields=("close",),
        max_staleness=None,
    )

    outcome = store.read_if_ready(requirement, year=NEWEST_YEAR, columns=("close",))

    assert outcome.readiness.state == "ready"
    assert len(outcome.rows) == len(SUBJECTS)
