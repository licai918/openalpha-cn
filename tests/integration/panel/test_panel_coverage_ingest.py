"""Deriving catalog coverage from a `ColumnarPanelBatch` (`V2-P1-003`).

`panel_ingest` is already the one place the columnar contract and `PanelStore` meet, so it
is where a batch turns into the catalog's coverage record. Three things are pinned here that
nothing else can pin:

- **The two hashes are not the same hash.** `PanelStore`'s `content_hash` answers "is this
  write the same write"; `ColumnarPanelBatch.content_digest` answers "is this partition
  still what that provider sent at that `as_of`". A refetch of identical data has the same
  `content_hash` and a different `content_digest`, so neither can stand in for the other.
- **The revision dimension needs a label, not a clock.** Roadmap section 7 measured that
  Tushare's restatements share both announcement dates, so the four PIT clocks cannot tell an
  original filing from its correction. The clock-derived count therefore reads 0 on exactly
  the data that motivates the feature, while the label census sees both versions.
- **Dates are trading dates in a declared timezone.** `panel_ingest`'s own docstring gives the
  case: a session at 08:00 Asia/Shanghai on 2024-01-01 is 2023-12-31 in UTC. The catalog
  records which convention it used rather than leaving it implicit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import panel_coverage, write_panel_batch

DATASET = "prices_daily"
AS_OF = datetime(2024, 1, 5, tzinfo=UTC)
FROZEN = datetime(2024, 1, 5, 1, 0, tzinfo=UTC)
EVENT = datetime(2024, 1, 2, 7, 0, tzinfo=UTC)
AVAILABLE = datetime(2024, 1, 2, 8, 30, tzinfo=UTC)


def _store(root: Path) -> PanelStore:
    return PanelStore(root, clock=lambda: FROZEN)


def _batch(
    *,
    subjects: tuple[str, ...] = ("000001.SZ", "000002.SZ"),
    event_time: tuple[datetime, ...] | None = None,
    available_time: tuple[datetime, ...] | None = None,
    revision_time: tuple[datetime, ...] | None = None,
    extra: tuple[PanelColumn, ...] = (),
    fetched_at: datetime = AS_OF,
    as_of: datetime = AS_OF,
) -> ColumnarPanelBatch:
    count = len(subjects)
    events = event_time if event_time is not None else tuple(EVENT for _ in range(count))
    available = (
        available_time if available_time is not None else tuple(AVAILABLE for _ in range(count))
    )
    revisions = revision_time if revision_time is not None else available
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=DATASET,
        kind="daily",
        as_of=as_of,
        fetched_at=fetched_at,
        status="success",
        subjects=subjects,
        timeline=TimelineColumns(
            event_time=events,
            available_time=available,
            ingested_time=available,
            revision_time=revisions,
        ),
        columns=(
            PanelColumn("close", "float", tuple(10.5 + index for index in range(count))),
            *extra,
        ),
    )


# --- what the seam derives ------------------------------------------------------------------


def test_write_panel_batch_records_every_s8_dimension_from_the_batch(tmp_path: Path) -> None:
    store = _store(tmp_path / "panel")
    batch = _batch()

    write_panel_batch(store, batch, year=2024)

    coverage = store.read_coverage(DATASET, 2024)
    assert coverage is not None
    assert coverage.subjects == ("000001.SZ", "000002.SZ")
    assert [field.name for field in coverage.fields] == [
        "subject",
        "event_time",
        "available_time",
        "ingested_time",
        "revision_time",
        "close",
    ]
    assert [(day.event_date, day.row_count) for day in coverage.dates] == [(date(2024, 1, 2), 2)]
    assert coverage.last_event_time == EVENT
    assert coverage.max_available_time == AVAILABLE
    assert coverage.revised_row_count == 0
    assert coverage.revisions == ()
    assert coverage.provider_id == "tushare"
    assert coverage.kind == "daily"
    assert coverage.as_of == AS_OF
    assert coverage.batch_digest == batch.content_digest
    assert coverage.schema_version == batch.schema_version


def test_the_subject_census_is_the_distinct_set_not_the_row_column(tmp_path: Path) -> None:
    """`subjects` carries one entry per row -- 5,534 names times ~2,440 trading days at real
    panel scale. The catalog records the *universe*, deduplicated and ordered, which is what
    "which securities does this partition contain" actually means."""
    store = _store(tmp_path / "panel")
    write_panel_batch(
        store,
        _batch(
            subjects=("000002.SZ", "000001.SZ", "000002.SZ", "000001.SZ"),
            event_time=(EVENT, EVENT, EVENT, EVENT),
        ),
        year=2024,
    )

    coverage = store.read_coverage(DATASET, 2024)
    assert coverage is not None
    assert coverage.subjects == ("000001.SZ", "000002.SZ")
    assert coverage.subject_count == 2
    assert coverage.row_count == 4


# --- the two hashes -------------------------------------------------------------------------


def test_the_store_hash_and_the_batch_digest_answer_different_questions(tmp_path: Path) -> None:
    """A refetch of byte-identical data at a later `fetched_at`. The store's `content_hash`
    is unchanged (so the Parquet file is not rewritten -- the idempotency the store promises),
    while the batch digest differs, because a different fetch produced it. Using the batch
    digest for idempotency would rewrite the partition on every refetch; using the store hash
    for provenance would lose the fetch entirely. Both are kept, and they are not the same
    number.
    """
    store = _store(tmp_path / "panel")
    first = _batch(fetched_at=AS_OF)
    refetched = _batch(fetched_at=datetime(2024, 1, 6, tzinfo=UTC))
    assert first.content_digest != refetched.content_digest

    first_ref = write_panel_batch(store, first, year=2024)
    parquet_mtime = first_ref.path.stat().st_mtime_ns
    second_ref = write_panel_batch(store, refetched, year=2024)

    assert second_ref.content_hash == first_ref.content_hash
    assert second_ref.path.stat().st_mtime_ns == parquet_mtime

    coverage = store.read_coverage(DATASET, 2024)
    assert coverage is not None
    assert coverage.batch_digest == refetched.content_digest
    assert coverage.batch_digest != first.content_digest
    assert coverage.batch_digest != first_ref.content_hash


def test_a_changed_value_moves_both_hashes(tmp_path: Path) -> None:
    """The other half of the judgement: the two hashes are not independent, they overlap on
    content. Only their non-overlapping halves (provenance for one, storage identity for the
    other) justify keeping both."""
    store = _store(tmp_path / "panel")
    clean = _batch()
    changed = _batch(subjects=("000001.SZ", "000003.SZ"))

    clean_ref = write_panel_batch(store, clean, year=2024)
    changed_ref = write_panel_batch(store, changed, year=2024)

    assert clean_ref.content_hash != changed_ref.content_hash
    assert clean.content_digest != changed.content_digest


# --- revision coverage ----------------------------------------------------------------------


def test_the_clock_derived_revision_count_cannot_see_an_update_flag_restatement(
    tmp_path: Path,
) -> None:
    """Roadmap section 7, measured against real Tushare data: `000001.SZ` / `end_date=20231231`
    returns two `balancesheet` rows whose `ann_date` and `f_ann_date` are both 20240315, and
    only `update_flag` (0 vs 1) separates them. Their four PIT clocks are therefore equal,
    so `revised_row_count` -- which counts rows whose `revision_time` post-dates their
    `available_time` -- reads 0. The label census is the dimension that can see them, which
    is why the catalog carries both and why `V2-P1-011` has somewhere to write `update_flag`.
    """
    store = _store(tmp_path / "panel")
    restated = _batch(
        subjects=("000001.SZ", "000001.SZ"),
        extra=(PanelColumn("update_flag", "integer", (0, 1)),),
    )

    write_panel_batch(store, restated, year=2024, revision_field="update_flag")

    coverage = store.read_coverage(DATASET, 2024)
    assert coverage is not None
    assert coverage.revised_row_count == 0
    assert [(item.label, item.row_count) for item in coverage.revisions] == [("0", 1), ("1", 1)]


def test_a_genuinely_revised_row_is_counted_by_the_clock_facet(tmp_path: Path) -> None:
    """Where the clocks *do* carry the information -- a row corrected after it first became
    available -- the count sees it. Both facets are real; neither covers the other."""
    store = _store(tmp_path / "panel")
    revised = _batch(
        subjects=("000001.SZ", "000002.SZ"),
        revision_time=(AVAILABLE, datetime(2024, 1, 3, 8, 30, tzinfo=UTC)),
    )

    write_panel_batch(store, revised, year=2024)

    coverage = store.read_coverage(DATASET, 2024)
    assert coverage is not None
    assert coverage.revised_row_count == 1
    assert coverage.revisions == ()


def test_a_revision_field_absent_from_the_batch_is_refused() -> None:
    with pytest.raises(PanelBatchError, match="update_flag"):
        panel_coverage(_batch(), year=2024, revision_field="update_flag")


def test_a_revision_label_that_is_missing_for_some_row_is_refused() -> None:
    batch = _batch(extra=(PanelColumn("update_flag", "integer", (0, None)),))

    with pytest.raises(PanelBatchError, match="never nullable"):
        panel_coverage(batch, year=2024, revision_field="update_flag")


def test_a_reserved_clock_column_cannot_stand_in_as_the_revision_field() -> None:
    """`revision_field` names one of the caller's own columns. Pointing it at `revision_time`
    would silently turn a per-row instant into thousands of one-row "versions".

    Matched on the reserved-column guard's own wording, not on `"revision_time"`. The name
    appears in the *fallback* message too ("is not a column of this batch"), which
    `revision_time` reaches on its own because it is a timeline column rather than a data
    column -- so a `match="revision_time"` passed with the guard deleted entirely, and proved
    nothing about the branch it was written for.
    """
    with pytest.raises(PanelBatchError, match="a revision label must be a data column"):
        panel_coverage(_batch(), year=2024, revision_field="revision_time")


# --- the date convention --------------------------------------------------------------------


def test_coverage_dates_are_trading_dates_in_the_declared_timezone() -> None:
    """`panel_ingest`'s own worked example. The instant below is 2023-12-31 23:00 UTC, which
    is 2024-01-01 07:00 in Asia/Shanghai -- the two conventions disagree about which day this
    row belongs to, and the catalog says which one it used."""
    crossing = datetime(2023, 12, 31, 23, 0, tzinfo=UTC)
    batch = _batch(
        subjects=("000001.SZ",),
        event_time=(crossing,),
        available_time=(datetime(2024, 1, 1, 8, 30, tzinfo=UTC),),
    )

    shanghai = panel_coverage(batch, year=2024)
    utc = panel_coverage(batch, year=2023, date_timezone="UTC")

    assert shanghai.date_timezone == "Asia/Shanghai"
    assert [day.event_date for day in shanghai.dates] == [date(2024, 1, 1)]
    assert utc.date_timezone == "UTC"
    assert [day.event_date for day in utc.dates] == [date(2023, 12, 31)]


def test_an_unknown_timezone_label_is_refused() -> None:
    with pytest.raises(PanelBatchError, match="Not/AZone"):
        panel_coverage(_batch(), year=2024, date_timezone="Not/AZone")


def test_a_no_data_batch_produces_no_coverage_record() -> None:
    """A `no_data` batch has no partition, so it has nothing to describe -- the same refusal
    `write_panel_batch` already makes, applied one step earlier so a caller cannot build a
    coverage record for a partition that will never exist."""
    empty = ColumnarPanelBatch(
        provider_id="tushare",
        dataset=DATASET,
        kind="daily",
        as_of=AS_OF,
        fetched_at=AS_OF,
        status="no_data",
        no_data_reason="every listed name was suspended",
    )

    with pytest.raises(PanelBatchError, match="no_data"):
        panel_coverage(empty, year=2024)
