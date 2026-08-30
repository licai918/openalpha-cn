"""`V2-P1-002`'s two headline acceptances: the columnar batch is *cheaper* than the row-wise
one, and its point-in-time guarantee is *exactly as strong*.

`ProviderBatch.validate_result` (`providers/base.py:148`) is the enforcement point of the
four-clock PIT contract: `any(not is_visible_at(record.timeline, self.request.as_of) for
record in self.records)` rejects a batch the moment a single record was not yet available at
`as_of`. `ColumnarPanelBatch` replaces that per-record scan with a single `max()` over the
`available_time` column, which is exactly equivalent -- `all(t <= as_of)` holds iff
`max(t) <= as_of` -- but "exactly equivalent" is a claim, so the tests below prove it by
running both contracts over the same corpus and asserting they accept and reject the *same*
inputs, including corpora where exactly one row out of many is a microsecond late.

The cost side is asserted four times, deliberately, because no one of them is sufficient on
its own -- one structural assertion per distinct cost claim, plus a wall-clock backstop:

- structurally, by counting canonical-JSON serializations (`test_...serializations...`) at
  two different row counts: the row-wise contract's count grows with the row count, the
  columnar contract's does not. No wall clock is involved, so this assertion cannot go flaky
  on a loaded CI runner. What it covers is the *digest* path only.
- structurally, by counting `is_visible_at` calls (`test_...visibility_check...`). The
  point-in-time path never calls `json.dumps`, so the serialization count above cannot see
  it at all: reverting `_check_visible_at_as_of` to a per-row scan left that assertion green
  (measured while reviewing `cb9e8f4`). This one counts the single call the batch-level
  check is allowed to make, at two row counts.
- structurally, by counting transposes (`test_to_rows_performs_one_transpose...`). Reverting
  `to_rows()` to a per-row comprehension is the one regression the wall clock below still
  cannot catch on its own -- it measures 6.7x, above the 6x threshold. A transpose count of
  one is exact.
- by wall clock (`test_...per_row...`), as a *relative* ratio between the two paths measured
  in the same process moments apart, never as an absolute millisecond budget. See that
  test's docstring for the measured numbers, how its threshold was chosen, and -- explicitly
  -- which regressions it does and does not catch.
"""

from __future__ import annotations

import json
import random
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from openalpha_cn.domain import panel_batch as panel_batch_module
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.time import Timeline
from openalpha_cn.providers import base as providers_base
from openalpha_cn.providers.base import ProviderBatch, ProviderRecord, ProviderRequest

AS_OF = datetime(2024, 6, 28, 12, 0, tzinfo=UTC)
EVENT = datetime(2024, 6, 28, 1, 30, tzinfo=UTC)
INGESTED = datetime(2024, 6, 28, 11, 0, tzinfo=UTC)
_FIELDS = ("open", "high", "low", "close", "vol")


# --- shared corpus builders ---------------------------------------------------------------


def _availables(
    count: int, *, late_index: int | None = None, lateness: timedelta
) -> list[datetime]:
    base = AS_OF - timedelta(hours=4)
    values = [base + timedelta(seconds=index) for index in range(count)]
    if late_index is not None:
        values[late_index] = AS_OF + lateness
    return values


def _clocks(availables: list[datetime]) -> tuple[list[datetime], ...]:
    """`Timeline` forbids `ingested_time`/`revision_time` before `available_time`, so both
    are pinned at or after each row's own `available_time` -- keeping every corpus below
    legal for reasons *other* than the visibility rule under test."""
    ingested = [max(INGESTED, value) for value in availables]
    return ([EVENT] * len(availables), availables, ingested, list(ingested))


def _row_wise_rejects(availables: list[datetime], as_of: datetime) -> bool:
    event, available, ingested, revision = _clocks(availables)
    records = tuple(
        ProviderRecord(
            subject=f"{index:06d}.SZ",
            kind="daily",
            timeline=Timeline(
                event_time=event[index],
                available_time=available[index],
                ingested_time=ingested[index],
                revision_time=revision[index],
            ),
            summary="daily bar",
            payload={field: float(index) for field in _FIELDS},
        )
        for index in range(len(availables))
    )
    try:
        ProviderBatch(
            provider_id="tushare",
            request=ProviderRequest(dataset="prices_daily", as_of=as_of),
            fetched_at=AS_OF,
            status="success",
            records=records,
        )
    except ValueError:
        return True
    return False


def _columnar_rejects(availables: list[datetime], as_of: datetime) -> bool:
    event, available, ingested, revision = _clocks(availables)
    try:
        _columnar_batch(event, available, ingested, revision, as_of)
    except ValueError:
        return True
    return False


def _columnar_batch(
    event: list[datetime],
    available: list[datetime],
    ingested: list[datetime],
    revision: list[datetime],
    as_of: datetime,
) -> ColumnarPanelBatch:
    count = len(available)
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset="prices_daily",
        kind="daily",
        as_of=as_of,
        fetched_at=AS_OF,
        status="success",
        subjects=tuple(f"{index:06d}.SZ" for index in range(count)),
        timeline=TimelineColumns(
            event_time=tuple(event),
            available_time=tuple(available),
            ingested_time=tuple(ingested),
            revision_time=tuple(revision),
        ),
        columns=tuple(
            PanelColumn(field, "float", tuple(float(index) for index in range(count)))
            for field in _FIELDS
        ),
    )


# --- PIT equivalence: the four-clock contract must not weaken -----------------------------


def test_a_batch_whose_every_row_was_available_before_as_of_is_accepted_by_both() -> None:
    availables = _availables(64, lateness=timedelta(0))

    assert _row_wise_rejects(availables, AS_OF) is False
    assert _columnar_rejects(availables, AS_OF) is False


def test_a_row_available_exactly_at_as_of_is_accepted_by_both() -> None:
    """`is_visible_at` is `available_time <= as_of`; the boundary belongs to the accepted
    side, and `max()` must land on the same side of it."""
    availables = _availables(16, late_index=7, lateness=timedelta(0))

    assert _row_wise_rejects(availables, AS_OF) is False
    assert _columnar_rejects(availables, AS_OF) is False


@pytest.mark.parametrize("late_index", [0, 1, 31, 62, 63])
def test_one_late_row_anywhere_in_a_64_row_batch_is_rejected_by_both(late_index: int) -> None:
    """The failure mode a batch-level assertion could plausibly have: missing a single
    violating row buried among compliant ones. `max()` cannot -- the maximum is attained by
    some row, so a violating row makes the maximum itself violate."""
    availables = _availables(64, late_index=late_index, lateness=timedelta(microseconds=1))

    assert _row_wise_rejects(availables, AS_OF) is True
    assert _columnar_rejects(availables, AS_OF) is True


def test_the_columnar_rejection_names_the_offending_row() -> None:
    """Better than the row-wise message, not merely as good: `ProviderBatch` raises
    "provider batch contains records unavailable at request as_of" without saying which."""
    availables = _availables(64, late_index=41, lateness=timedelta(microseconds=1))
    event, available, ingested, revision = _clocks(availables)

    with pytest.raises(ValueError, match="row 41"):
        _columnar_batch(event, available, ingested, revision, AS_OF)


def test_the_two_contracts_agree_on_a_randomised_corpus() -> None:
    """200 seeded cases, each with a random row count and a random number of rows nudged to
    either side of `as_of` by a random margin -- including the exact-equality boundary."""
    rng = random.Random(20260808)
    disagreements: list[tuple[int, list[float]]] = []
    rejected_cases = 0

    for case in range(200):
        count = rng.randint(1, 40)
        availables = _availables(count, lateness=timedelta(0))
        for index in range(count):
            if rng.random() < 0.15:
                micros = rng.choice([-1000, -1, 0, 1, 1000, 86_400_000_000])
                availables[index] = AS_OF + timedelta(microseconds=micros)
        row_wise = _row_wise_rejects(availables, AS_OF)
        columnar = _columnar_rejects(availables, AS_OF)
        rejected_cases += int(row_wise)
        if row_wise != columnar:
            disagreements.append((case, [(value - AS_OF).total_seconds() for value in availables]))

    assert not disagreements, f"row-wise and columnar disagreed on {disagreements[:3]}"
    assert 20 < rejected_cases < 180, (
        f"corpus is degenerate: only {rejected_cases}/200 cases were rejected, so the "
        "agreement above would be trivially satisfiable"
    )


def test_an_empty_available_time_column_cannot_smuggle_rows_past_the_visibility_check() -> None:
    """`max(())` raises, so a zero-row batch has to be handled explicitly rather than
    accidentally skipping the check -- and a zero-row `success` batch is refused outright,
    exactly as `ProviderBatch` refuses `status="success"` with no records."""
    with pytest.raises(ValueError):
        ColumnarPanelBatch(
            provider_id="tushare",
            dataset="prices_daily",
            kind="daily",
            as_of=AS_OF,
            fetched_at=AS_OF,
            status="success",
        )


# --- cost, proven structurally (no wall clock) --------------------------------------------


class _CountingJson:
    """Stand-in for `panel_batch`'s module-level `json`, counting `dumps` calls."""

    def __init__(self) -> None:
        self.calls = 0

    def dumps(self, *args: object, **kwargs: object) -> str:
        self.calls += 1
        return json.dumps(*args, **kwargs)  # type: ignore[arg-type]


def _row_wise_serialization_count(availables: list[datetime], monkeypatch: object) -> int:
    calls = {"count": 0}
    real = providers_base.canonical_json_bytes

    def _counting(value: object) -> bytes:
        calls["count"] += 1
        return real(value)

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setattr(providers_base, "canonical_json_bytes", _counting)
    _row_wise_rejects(availables, AS_OF)
    monkeypatch.undo()
    return calls["count"]


def _columnar_serialization_count(availables: list[datetime], monkeypatch: object) -> int:
    counting = _CountingJson()
    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setattr(panel_batch_module, "json", counting)
    _columnar_batch(*_clocks(availables), AS_OF)
    monkeypatch.undo()
    return counting.calls


def test_row_wise_serialization_count_grows_with_row_count_and_columnar_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ProviderRecord.freeze_payload` calls `canonical_json_bytes` once per record and
    `ProviderBatch.payload_digest` re-serializes every record on *every* access;
    `ColumnarPanelBatch` serializes once per *column* plus a batch header, so its count is a
    function of schema width alone. Counting instead of timing makes this assertion immune
    to CI load."""
    row_wise_counts: list[int] = []
    columnar_counts: list[int] = []

    for count in (250, 1000):
        availables = _availables(count, lateness=timedelta(0))
        row_wise_counts.append(_row_wise_serialization_count(availables, monkeypatch))
        columnar_counts.append(_columnar_serialization_count(availables, monkeypatch))

    assert row_wise_counts[1] > row_wise_counts[0] * 3, (
        f"expected the row-wise count to scale with rows, got {row_wise_counts}"
    )
    assert columnar_counts[0] == columnar_counts[1], (
        f"expected a row-count-independent columnar count, got {columnar_counts}"
    )
    assert columnar_counts[0] < row_wise_counts[0], (
        f"columnar {columnar_counts[0]} must be cheaper than row-wise {row_wise_counts[0]}"
    )


def _columnar_visibility_check_count(row_count: int, monkeypatch: pytest.MonkeyPatch) -> int:
    calls = {"count": 0}
    real = panel_batch_module.is_visible_at

    def _counting(timeline: Timeline, as_of: datetime) -> bool:
        calls["count"] += 1
        return real(timeline, as_of)

    monkeypatch.setattr(panel_batch_module, "is_visible_at", _counting)
    _columnar_batch(*_clocks(_availables(row_count, lateness=timedelta(0))), AS_OF)
    monkeypatch.undo()
    return calls["count"]


def test_the_columnar_visibility_check_consults_one_row_whatever_the_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point-in-time half of the cost claim, pinned without a wall clock.

    `all(t <= as_of)` is enforced as `max(t) <= as_of` plus one `is_visible_at` on the row
    that attained the maximum, so exactly one call is made no matter how many rows the batch
    carries -- whereas `ProviderBatch.validate_result` makes one per record.

    This exists because neither of the other two cost assertions sees this path. The
    serialization count above cannot: the visibility check never serializes anything. The
    wall-clock ratio below barely can: reverting this check to a per-row `is_visible_at` scan
    measured 4.29x against a 7.8x baseline while reviewing `cb9e8f4`, which passed the 4x
    threshold that version asserted. A call count is exact, load-independent, and goes red on
    the first extra call.
    """
    counts = [_columnar_visibility_check_count(count, monkeypatch) for count in (250, 1000)]

    assert counts == [1, 1], (
        f"expected exactly one visibility check per batch at both row counts, got {counts}: "
        "the batch-level check has reverted to per-row work"
    )


def _columnar_transpose_count(row_count: int, monkeypatch: pytest.MonkeyPatch) -> int:
    batch = _columnar_batch(*_clocks(_availables(row_count, lateness=timedelta(0))), AS_OF)
    calls = {"count": 0}
    real = zip

    def _counting(*iterables: object, **kwargs: object) -> object:
        calls["count"] += 1
        return real(*iterables, **kwargs)  # type: ignore[call-overload]

    # A module-level `zip` shadows the builtin for every function defined in that module, so
    # this counts the transposes `to_rows()` itself performs and nothing else. The batch is
    # built before the patch goes on, so `TimelineColumns._check_ordering`'s own `zip` calls
    # are not in the count.
    monkeypatch.setattr(panel_batch_module, "zip", _counting, raising=False)
    rows = batch.to_rows()
    monkeypatch.undo()

    assert len(rows) == row_count
    return calls["count"]


def test_to_rows_performs_one_transpose_whatever_the_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third cost claim, pinned the same wall-clock-free way as the other two.

    `to_rows()` exists to hand storage a row block without constructing anything per row: one
    C-level `zip` over the column tuples, whatever the row count. The wall-clock ratio below
    cannot police this on its own -- reverting `to_rows()` to a per-row Python comprehension
    still measured 6.7x, comfortably above the 6x threshold (measured directly, by making
    exactly that change and re-running). A count of one is not.

    A per-row implementation fails this either way: a comprehension that calls no `zip` at
    all counts 0, and one that zips per row counts `row_count`.
    """
    counts = [_columnar_transpose_count(count, monkeypatch) for count in (250, 1000)]

    assert counts == [1, 1], (
        f"expected exactly one transpose per to_rows() call at both row counts, got {counts}"
    )


# --- cost, measured (relative ratio only) --------------------------------------------------

_BENCH_ROWS = 2000
_MIN_SPEEDUP = 6.0
_MIN_SPEEDUP_UNDER_A_TRACER = 3.0


def _min_speedup() -> float:
    """The applicable threshold, which depends on whether a line tracer is installed.

    This project's own completion gate runs the suite twice, once plain and once under
    `pytest --cov`, and `coverage.py` installs a `sys.settrace` tracer that taxes the two
    paths very unequally: the row-wise path is mostly C-level pydantic work the tracer barely
    sees, while the columnar path is Python-level generator expressions the tracer charges
    for on every element. Measured here, same machine, same commit:

        untraced       7.54x - 8.18x   (48 samples)
        under --cov    4.88x - 5.23x   (36 samples)

    One constant cannot serve both -- 6x is a ~20% cushion untraced and simply unreachable
    traced. So the regime is detected rather than guessed. `sys.gettrace()` is not None under
    `coverage.py` (and under a debugger, which deserves the same treatment).

    **The traced threshold was 4.0 and a third machine broke it (`V2-P5-069`).** The two ranges
    above come from machines that agree with each other; this repository's matrix contains one
    that does not. Measured, same commit, `--cov` in every case:

        author's machine       4.88x - 5.23x   (36 samples)
        this one, macOS arm64  4.70x - 5.00x   (5 samples)
        windows-latest, 3.12   **3.92x**       (CI, one run)

    A cushion sized against machines that agree is not a cushion. And the precision was never
    load-bearing: this file already records that reverting `to_rows()` to a per-row comprehension
    **still measured 6.7x**, above the 6x untraced threshold -- so the ratio does not catch the
    regression it looks like it guards. What catches that is
    `test_to_rows_transposes_once_per_call`, structurally, by counting transposes. This number's
    job is the one it can actually do: notice if the columnar path stops being fundamentally
    cheaper at all. 3.0 is 23% below the lowest value any machine has produced, and a collapse
    to parity lands near 1.0 and still fails.

    **The untraced 6.0 is not changed, and what is unknown about it is stated rather than
    assumed**: CI always runs under `--cov`, so no machine in the matrix has ever exercised it.
    Its cushion rests on the same two agreeing machines that made the traced one look safe.
    """
    return _MIN_SPEEDUP_UNDER_A_TRACER if sys.gettrace() is not None else _MIN_SPEEDUP


def test_the_columnar_path_costs_a_small_fraction_of_the_row_wise_path_per_row() -> None:
    """Both paths start from the same column-oriented source data -- which is the shape a
    real provider response actually arrives in (Tushare returns a column-oriented frame) --
    and end at the same place: a validated, integrity-stamped batch ready to hand to
    storage. The row-wise path pays for the transpose into per-row payload dicts because
    `ProviderRecord.payload` requires one; that is a real cost of the row-wise contract, not
    a handicap invented here.

    Measured on this task's development machine at 2,000 rows x 5 float fields, best of five
    runs each:

        row-wise   13.9 us/row   (records 6.10 + batch 2.63 + payload_digest 5.10)
        columnar    1.80 us/row  (TimelineColumns 0.66 + batch incl. digest 1.00 +
                                  PanelColumns 0.09 + to_rows 0.03)
        ratio       7.8x

    48 samples of this test's own measurement (best-of-three each side, as below) spanned
    7.54x to 8.18x, so the ratio itself is stable to about +/-4% on an idle machine. Under
    `coverage.py`'s tracer the whole scale shifts; `_min_speedup()` explains that and picks
    the applicable threshold.

    What the threshold is *for*, precisely: it is a backstop against a wholesale collapse of
    the columnar advantage, sized to leave ~20% below the lowest ratio observed in its own
    regime. What it is *not* is a reliable detector of an individual per-row regression --
    measured directly, by mutating the implementation and re-running both regimes:

                                                      untraced      under --cov
        clean                                         7.5 - 8.2x    4.9 - 5.2x
        to_rows() reverted to a per-row loop          6.7x  pass    3.9x  ~ on the line
        PIT check reverted to per-row is_visible_at   4.3x  FAIL    3.0x  FAIL
        both at once                                  3.9x  FAIL

    An earlier version of this docstring claimed "an implementation that quietly reverted to
    per-row work could not pass it" against a flat 4x threshold. That was untrue: both single
    mutations cleared 4x untraced. Raising the threshold catches the point-in-time collapse
    and nothing more; the `to_rows()` row above is why this test cannot be the primary
    evidence for any of the three cost claims. The three structural, wall-clock-free
    assertions above are -- the serialization count, the visibility-check count and the
    transpose count, one per claim, each exact rather than statistical, and each verified to
    go red under exactly the mutation it exists for.
    """
    availables = _availables(_BENCH_ROWS, lateness=timedelta(0))
    event, available, ingested, revision = _clocks(availables)
    subjects = tuple(f"{index:06d}.SZ" for index in range(_BENCH_ROWS))
    field_values = {
        field: tuple(float(index) + offset for index in range(_BENCH_ROWS))
        for offset, field in enumerate(_FIELDS)
    }

    def row_wise_path() -> str:
        records = tuple(
            ProviderRecord(
                subject=subjects[index],
                kind="daily",
                timeline=Timeline(
                    event_time=event[index],
                    available_time=available[index],
                    ingested_time=ingested[index],
                    revision_time=revision[index],
                ),
                summary="daily bar",
                payload={field: field_values[field][index] for field in _FIELDS},
            )
            for index in range(_BENCH_ROWS)
        )
        batch = ProviderBatch(
            provider_id="tushare",
            request=ProviderRequest(dataset="prices_daily", as_of=AS_OF),
            fetched_at=AS_OF,
            status="success",
            records=records,
        )
        return batch.payload_digest

    def columnar_path() -> str:
        batch = ColumnarPanelBatch(
            provider_id="tushare",
            dataset="prices_daily",
            kind="daily",
            as_of=AS_OF,
            fetched_at=AS_OF,
            status="success",
            subjects=subjects,
            timeline=TimelineColumns(
                event_time=tuple(event),
                available_time=tuple(available),
                ingested_time=tuple(ingested),
                revision_time=tuple(revision),
            ),
            columns=tuple(PanelColumn(field, "float", field_values[field]) for field in _FIELDS),
        )
        batch.to_rows()
        return batch.content_digest

    row_wise_seconds = _best_of(row_wise_path)
    columnar_seconds = _best_of(columnar_path)
    speedup = row_wise_seconds / columnar_seconds
    threshold = _min_speedup()

    assert speedup >= threshold, (
        f"columnar path was only {speedup:.1f}x cheaper than row-wise "
        f"({columnar_seconds * 1e6 / _BENCH_ROWS:.2f} us/row vs "
        f"{row_wise_seconds * 1e6 / _BENCH_ROWS:.2f} us/row over {_BENCH_ROWS} rows); "
        f"expected at least {threshold}x"
    )


def _best_of(callable_under_test: Callable[[], object], rounds: int = 3) -> float:
    best = float("inf")
    for _ in range(rounds):
        start = time.perf_counter()
        callable_under_test()
        best = min(best, time.perf_counter() - start)
    return best
