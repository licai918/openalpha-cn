"""Shape, validation, and integrity tests for the columnar panel batch (`V2-P1-002`).

The point-in-time half of this contract -- the part that must stay *exactly* as strong as
`ProviderBatch.validate_result`'s per-record `is_visible_at` check -- is proven separately,
against the row-wise contract itself, in
`tests/contract/panel/test_columnar_batch_parity.py`. This file covers everything else:
batch shape, identifier and `dataset` validation, per-column typing, and the batch-level
integrity digest that replaces the row-wise contract's per-record content hash.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from openalpha_cn.domain import panel_batch as panel_batch_module
from openalpha_cn.domain.panel_batch import (
    RESERVED_COLUMN_NAMES,
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    TimelineColumns,
)

AS_OF = datetime(2024, 6, 28, 12, 0, tzinfo=UTC)
AVAILABLE = datetime(2024, 6, 28, 7, 30, tzinfo=UTC)
INGESTED = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)


def _timeline(count: int = 2, *, available: tuple[datetime, ...] | None = None) -> TimelineColumns:
    availables = available if available is not None else tuple(AVAILABLE for _ in range(count))
    return TimelineColumns(
        event_time=tuple(AVAILABLE - timedelta(hours=1) for _ in range(count)),
        available_time=availables,
        ingested_time=tuple(INGESTED for _ in range(count)),
        revision_time=tuple(INGESTED for _ in range(count)),
    )


def _batch(**overrides: Any) -> ColumnarPanelBatch:
    defaults: dict[str, Any] = {
        "provider_id": "tushare",
        "dataset": "prices_daily",
        "kind": "daily",
        "as_of": AS_OF,
        "fetched_at": AS_OF,
        "status": "success",
        "subjects": ("000001.SZ", "000002.SZ"),
        "timeline": _timeline(),
        "columns": (
            PanelColumn("close", "float", (10.5, 22.5)),
            PanelColumn("volume", "integer", (1000, 2000)),
        ),
    }
    defaults.update(overrides)
    return ColumnarPanelBatch(**defaults)


# --- batch shape ------------------------------------------------------------------------


def test_a_success_batch_exposes_its_row_count_and_full_storage_column_list() -> None:
    batch = _batch()

    assert batch.row_count == 2
    assert [column.name for column in batch.storage_columns()] == [
        "subject",
        "event_time",
        "available_time",
        "ingested_time",
        "revision_time",
        "close",
        "volume",
    ]
    assert batch.to_rows() == (
        ("000001.SZ", AVAILABLE - timedelta(hours=1), AVAILABLE, INGESTED, INGESTED, 10.5, 1000),
        ("000002.SZ", AVAILABLE - timedelta(hours=1), AVAILABLE, INGESTED, INGESTED, 22.5, 2000),
    )


def test_a_column_shorter_than_the_subject_column_is_rejected() -> None:
    with pytest.raises(PanelBatchError, match="close"):
        _batch(columns=(PanelColumn("close", "float", (10.5,)),))


def test_a_clock_column_shorter_than_the_others_is_rejected() -> None:
    with pytest.raises(PanelBatchError, match="available_time"):
        TimelineColumns(
            event_time=(AVAILABLE, AVAILABLE),
            available_time=(AVAILABLE,),
            ingested_time=(INGESTED, INGESTED),
            revision_time=(INGESTED, INGESTED),
        )


def test_two_columns_with_the_same_name_are_rejected() -> None:
    with pytest.raises(PanelBatchError, match="duplicate"):
        _batch(
            columns=(
                PanelColumn("close", "float", (10.5, 22.5)),
                PanelColumn("close", "float", (1.0, 2.0)),
            )
        )


@pytest.mark.parametrize("reserved", sorted(RESERVED_COLUMN_NAMES))
def test_a_data_column_may_not_shadow_a_reserved_storage_column(reserved: str) -> None:
    """`storage_columns()` emits `subject` plus the four clocks ahead of the caller's own
    columns, so a caller-supplied column with one of those names would silently produce two
    same-named Parquet columns."""
    with pytest.raises(PanelBatchError, match="reserved"):
        _batch(columns=(PanelColumn(reserved, "string", ("a", "b")),))


def test_success_requires_at_least_one_row() -> None:
    with pytest.raises(PanelBatchError, match="at least one row"):
        _batch(subjects=(), timeline=_timeline(0), columns=())


def test_success_cannot_carry_a_no_data_reason() -> None:
    with pytest.raises(PanelBatchError, match="no_data_reason"):
        _batch(no_data_reason="nothing here")


def test_no_data_requires_a_reason() -> None:
    with pytest.raises(PanelBatchError, match="no_data_reason"):
        _batch(status="no_data", subjects=(), timeline=_timeline(0), columns=())


def test_no_data_cannot_carry_rows() -> None:
    with pytest.raises(PanelBatchError, match="no_data"):
        _batch(status="no_data", no_data_reason="suspended all day")


def test_a_no_data_batch_is_valid_and_reports_zero_rows() -> None:
    batch = _batch(
        status="no_data",
        subjects=(),
        timeline=_timeline(0),
        columns=(),
        no_data_reason="market closed",
    )

    assert batch.row_count == 0
    assert batch.to_rows() == ()


def test_a_timeline_carrying_a_different_number_of_rows_than_the_subjects_is_rejected() -> None:
    """The shape invariant a row model gets for free. Without it, `to_rows()`'s `zip(...,
    strict=True)` would be the first thing to notice, deep inside the write path."""
    with pytest.raises(PanelBatchError, match="timeline carries 3 rows"):
        _batch(timeline=_timeline(3))


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_provider_id_is_rejected(blank: str) -> None:
    with pytest.raises(PanelBatchError, match="provider_id must be a non-empty string"):
        _batch(provider_id=blank)


def test_an_over_long_kind_is_rejected() -> None:
    with pytest.raises(PanelBatchError, match="kind must be at most 64 characters"):
        _batch(kind="d" * 65)


def test_an_over_long_source_uri_is_rejected() -> None:
    with pytest.raises(PanelBatchError, match="source_uri must be at most 2048"):
        _batch(source_uri="https://example.invalid/" + "x" * 2048)


def test_a_source_uri_is_carried_and_hashed() -> None:
    assert _batch(source_uri="tushare://daily").content_digest != _batch().content_digest


@pytest.mark.parametrize("bad", [("000001.SZ", ""), ("000001.SZ", None), ("000001.SZ", 2)])
def test_an_empty_or_non_string_subject_is_rejected(bad: tuple[object, ...]) -> None:
    """The subject column is what every panel row is keyed by; unlike a data column it is
    never nullable."""
    with pytest.raises(PanelBatchError, match="subject row 1"):
        _batch(subjects=bad)


def test_an_unknown_column_kind_is_rejected() -> None:
    """`PanelColumnKind` is a `Literal`, so mypy already rejects this at every typed call
    site -- but the contract is also reachable from untyped callers (a provider adapter
    building a kind from a config string), and a kind the DuckDB type table does not know
    must fail here rather than at `KeyError` time inside `panel_ingest`."""
    with pytest.raises(PanelBatchError, match="unknown kind"):
        PanelColumn("close", "decimal", (1.0,))  # type: ignore[arg-type]


def test_a_non_datetime_value_in_a_timestamp_column_is_rejected() -> None:
    with pytest.raises(PanelBatchError, match=r"'ann_date' row 1: expected timestamp, got str"):
        PanelColumn("ann_date", "timestamp", (AVAILABLE, "2024-06-28"))


# --- identifier and dataset validation ---------------------------------------------------
#
# `panel/store.py` interpolates both the column name and the DuckDB type straight into SQL
# (`f'"{column.name}" {column.duckdb_type}'` in `write_partition`, `f'"{name}"'` in
# `_build_scan_sql`), so a column name carrying a `"` closes the quote and everything after
# it is executed as SQL. This contract is the boundary that makes that unreachable for any
# value routed through a `ColumnarPanelBatch`; see the module docstring of
# `openalpha_cn.panel.ingest` and this task's report.


@pytest.mark.parametrize(
    "hostile",
    [
        "a\" INTEGER); ATTACH '/tmp/evil.duckdb' AS evil; CREATE TABLE staging2(\"b",
        'ts_code" , "close',
        "close; DROP TABLE staging",
        "close DOUBLE",
        "",
        "1close",
        "clo-se",
        "клоуз",
    ],
)
def test_a_column_name_that_is_not_a_plain_identifier_is_rejected(hostile: str) -> None:
    with pytest.raises(PanelBatchError):
        PanelColumn(hostile, "float", (1.0,))


@pytest.mark.parametrize("dataset", ["", "../escaped", "/abs/path", "a/b", ".", "..", "/../../x"])
def test_a_dataset_that_is_not_a_single_plain_path_segment_is_rejected(dataset: str) -> None:
    """The same rule `panel/store.py::_validate_dataset` enforces, applied one layer
    earlier: a `ColumnarPanelBatch` is handed to `write_panel_batch()`, which passes
    `batch.dataset` straight to `PanelStore.write_partition()` as a path component."""
    with pytest.raises(PanelBatchError):
        _batch(dataset=dataset)


def test_the_contract_and_the_store_agree_on_every_rejected_dataset() -> None:
    """Two independent implementations of the same rule can drift; this pins them together.

    `domain/` must not import `panel/` (it is the lower layer), so the check is duplicated
    rather than shared -- this test is what keeps the duplicate honest.
    """
    from openalpha_cn.panel.store import PanelStorageError, _validate_dataset

    corpus = ["", "../escaped", "/abs/path", "a/b", ".", "..", "/../../x", "a\\b", "prices/"]
    for dataset in corpus:
        store_rejected = False
        try:
            _validate_dataset(dataset)
        except PanelStorageError:
            store_rejected = True
        contract_rejected = False
        try:
            panel_batch_module.validate_panel_dataset(dataset)
        except PanelBatchError:
            contract_rejected = True
        assert store_rejected == contract_rejected, (
            f"{dataset!r}: store rejected={store_rejected}, contract rejected={contract_rejected}"
        )


# --- per-column typing and clock normalisation -------------------------------------------


def test_a_value_of_the_wrong_python_type_is_rejected_with_the_column_and_row_named() -> None:
    with pytest.raises(PanelBatchError, match="close"):
        PanelColumn("close", "float", (10.5, "22.5"))


def test_none_is_allowed_in_a_data_column() -> None:
    column = PanelColumn("close", "float", (10.5, None))

    assert column.values == (10.5, None)


def test_none_is_rejected_in_a_clock_column() -> None:
    with pytest.raises(PanelBatchError):
        TimelineColumns(
            event_time=(AVAILABLE,),
            available_time=(None,),  # type: ignore[arg-type]
            ingested_time=(INGESTED,),
            revision_time=(INGESTED,),
        )


def test_a_naive_datetime_in_a_clock_column_is_rejected() -> None:
    naive = datetime(2024, 6, 28, 7, 30)

    with pytest.raises(ValueError, match="timezone-aware"):
        TimelineColumns(
            event_time=(naive,),
            available_time=(naive,),
            ingested_time=(naive,),
            revision_time=(naive,),
        )


def test_a_naive_datetime_in_a_timestamp_data_column_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PanelColumn("ann_date", "timestamp", (datetime(2024, 6, 28),))


def test_clock_values_are_normalised_to_utc_like_timeline_does() -> None:
    shanghai = timezone(timedelta(hours=8))
    local = AVAILABLE.astimezone(shanghai)

    timeline = TimelineColumns(
        event_time=(local,),
        available_time=(local,),
        ingested_time=(local,),
        revision_time=(local,),
    )

    assert timeline.available_time[0].tzinfo is UTC
    assert timeline.available_time[0] == AVAILABLE


def test_the_per_row_timeline_ordering_invariant_is_enforced() -> None:
    """`Timeline.__post_init__` rejects `ingested_time < available_time`; the transposed
    contract must reject exactly the same row, and does so by handing that row to the real
    `Timeline` rather than re-implementing its rule."""
    with pytest.raises(ValueError, match="ingested_time cannot precede available_time"):
        TimelineColumns(
            event_time=(AVAILABLE, AVAILABLE),
            available_time=(AVAILABLE, AVAILABLE),
            ingested_time=(INGESTED, AVAILABLE - timedelta(seconds=1)),
            revision_time=(INGESTED, INGESTED),
        )


def test_row_timeline_reconstructs_the_row_wise_timeline_for_any_index() -> None:
    batch = _batch()

    timeline = batch.row_timeline(1)

    assert timeline.event_time == AVAILABLE - timedelta(hours=1)
    assert timeline.available_time == AVAILABLE
    assert timeline.ingested_time == INGESTED
    assert timeline.revision_time == INGESTED


# --- integrity: the batch-level digest ----------------------------------------------------


def test_two_structurally_identical_batches_share_one_digest() -> None:
    assert _batch().content_digest == _batch().content_digest
    assert _batch().content_digest.startswith("sha256:")


def test_changing_a_single_value_in_a_single_column_changes_the_digest() -> None:
    """The core integrity claim: this is what replaces `ProviderRecord.record_id`'s
    per-record content address on the panel plane."""
    baseline = _batch()

    corrupted = _batch(
        columns=(
            PanelColumn("close", "float", (10.5, 22.500000001)),
            PanelColumn("volume", "integer", (1000, 2000)),
        )
    )

    assert corrupted.content_digest != baseline.content_digest


def test_changing_a_single_clock_value_by_one_microsecond_changes_the_digest() -> None:
    """Deliberately a *sub-second* nudge: `AVAILABLE` falls exactly on a second boundary, so
    a digest that rounded timestamps to whole seconds would still see two identical values
    here. Revisions of the same statement can land microseconds apart (roadmap section 7:
    two `balancesheet` versions share every announcement date and differ only by
    `update_flag`), so the clock digest has to be exact at `datetime`'s full resolution."""
    baseline = _batch()

    corrupted = _batch(
        timeline=_timeline(available=(AVAILABLE, AVAILABLE + timedelta(microseconds=1)))
    )

    assert corrupted.content_digest != baseline.content_digest


def test_changing_a_single_subject_changes_the_digest() -> None:
    assert _batch(subjects=("000001.SZ", "000003.SZ")).content_digest != _batch().content_digest


def test_reordering_two_columns_changes_the_digest() -> None:
    baseline = _batch()

    reordered = _batch(
        columns=(
            PanelColumn("volume", "integer", (1000, 2000)),
            PanelColumn("close", "float", (10.5, 22.5)),
        )
    )

    assert reordered.content_digest != baseline.content_digest


def test_renaming_a_column_changes_the_digest() -> None:
    baseline = _batch()

    renamed = _batch(
        columns=(
            PanelColumn("close_px", "float", (10.5, 22.5)),
            PanelColumn("volume", "integer", (1000, 2000)),
        )
    )

    assert renamed.content_digest != baseline.content_digest


def test_changing_a_column_kind_changes_the_digest() -> None:
    baseline = _batch(columns=(PanelColumn("flag", "integer", (0, 1)),))

    retyped = _batch(columns=(PanelColumn("flag", "float", (0.0, 1.0)),))

    assert retyped.content_digest != baseline.content_digest


def test_changing_batch_level_metadata_changes_the_digest() -> None:
    baseline = _batch()

    assert _batch(provider_id="akshare").content_digest != baseline.content_digest
    assert _batch(kind="daily_basic").content_digest != baseline.content_digest
    assert _batch(dataset="prices_weekly").content_digest != baseline.content_digest
    assert _batch(as_of=AS_OF + timedelta(seconds=1)).content_digest != baseline.content_digest
    assert _batch(fetched_at=AS_OF + timedelta(seconds=1)).content_digest != baseline.content_digest


def test_moving_string_content_between_two_columns_changes_the_digest() -> None:
    """Same characters, same row count, same column names and kinds -- only the boundary
    between the two columns moved. The last two variants push the raw column-separator byte
    inside a value, which the JSON encoder escapes rather than emitting into the framing."""
    separator = chr(30)
    variants = (
        (("x", "y"), ("z", "w")),
        (("x", "yz"), ("", "w")),
        (("x", ""), ("y", "zw")),
        (("x", "y" + separator + "z"), ("", "w")),
        (("x" + separator + "y", ""), ("z", "w")),
    )

    digests = {
        _batch(
            columns=(
                PanelColumn("a", "string", left),
                PanelColumn("b", "string", right),
            )
        ).content_digest
        for left, right in variants
    }

    assert len(digests) == len(variants)


def test_a_missing_value_and_the_literal_string_none_do_not_share_a_digest() -> None:
    """The collision a `str()`-based column encoder admits: `str(None)` and `str("None")`
    are the same four bytes, but a missing observation and a name that happens to read
    "None" are different facts. The JSON encoder writes `null` and `"None"`."""
    missing = _batch(columns=(PanelColumn("name", "string", ("SPDB", None)),))
    literal = _batch(columns=(PanelColumn("name", "string", ("SPDB", "None")),))

    assert missing.content_digest != literal.content_digest


def test_the_digest_is_representation_independent_for_the_same_instant() -> None:
    """DuckDB hands `TIMESTAMPTZ` values back in the session timezone, not UTC (measured
    against DuckDB 1.5 while writing this task). A digest computed over a datetime's *text*
    would therefore change across a storage round trip even though no information did; this
    one is computed over the UTC instant."""
    shanghai = timezone(timedelta(hours=8))
    utc_batch = _batch()
    shifted = _batch(
        timeline=TimelineColumns(
            event_time=tuple(
                (AVAILABLE - timedelta(hours=1)).astimezone(shanghai) for _ in range(2)
            ),
            available_time=tuple(AVAILABLE.astimezone(shanghai) for _ in range(2)),
            ingested_time=tuple(INGESTED.astimezone(shanghai) for _ in range(2)),
            revision_time=tuple(INGESTED.astimezone(shanghai) for _ in range(2)),
        ),
        as_of=AS_OF.astimezone(shanghai),
        fetched_at=AS_OF.astimezone(shanghai),
    )

    assert shifted.content_digest == utc_batch.content_digest


def test_the_digest_is_computed_once_at_construction_not_on_every_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct contrast with `ProviderBatch.payload_digest`, an uncached
    `computed_field` that re-serializes every record on *each* access (measured at 12.0 ms
    for 2,000 rows, every time)."""
    calls = {"count": 0}
    real_sha256 = panel_batch_module.sha256

    def _counting_sha256(*args: object, **kwargs: object) -> Any:
        calls["count"] += 1
        return real_sha256(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(panel_batch_module, "sha256", _counting_sha256)
    batch = _batch()
    construction_calls = calls["count"]
    assert construction_calls > 0, "the spy is not wired to the real digest call site"

    for _ in range(5):
        assert batch.content_digest.startswith("sha256:")

    assert calls["count"] == construction_calls


def test_the_batch_is_frozen_so_the_digest_cannot_drift_from_its_content() -> None:
    batch = _batch()

    with pytest.raises(Exception, match=r"frozen|immutable|cannot assign"):
        batch.provider_id = "other"  # type: ignore[misc]

    # `dataclasses.replace` is the supported way to derive a changed batch, and it
    # recomputes the digest rather than carrying the old one forward.
    derived = replace(batch, provider_id="other")
    assert derived.content_digest != batch.content_digest
