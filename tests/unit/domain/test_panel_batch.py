"""Shape, validation, and integrity tests for the columnar panel batch (`V2-P1-002`).

The point-in-time half of this contract -- the part that must stay *exactly* as strong as
`ProviderBatch.validate_result`'s per-record `is_visible_at` check -- is proven separately,
against the row-wise contract itself, in
`tests/contract/panel/test_columnar_batch_parity.py`. This file covers everything else:
batch shape, identifier and `dataset` validation, per-column typing, and the batch-level
integrity digest that replaces the row-wise contract's per-record content hash.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from openalpha_cn.domain import panel_batch as panel_batch_module
from openalpha_cn.domain.panel_batch import (
    MAX_IDENTIFIER_LENGTH,
    RESERVED_COLUMN_NAMES,
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.domain.time import Timeline

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


@pytest.mark.parametrize("hostile", ["close\n", "close\n; DROP TABLE staging", "_close\r"])
def test_a_column_name_with_a_trailing_line_break_is_rejected(hostile: str) -> None:
    """`re.match` with a trailing `$` accepts a final newline: `$` matches at the end of the
    string *and* immediately before a terminating newline. `"close\\n"` therefore passed the
    plain-identifier rule against `cb9e8f4` and was written into a real Parquet column and
    read back again. `re.fullmatch` is what closes it."""
    with pytest.raises(PanelBatchError):
        PanelColumn(hostile, "float", (1.0,))


def test_the_identifier_length_limit_is_exactly_the_advertised_maximum() -> None:
    """Both sides of the boundary, because the same `$`-versus-`fullmatch` bug also let a
    64-character name through -- 63 identifier characters plus the newline `$` forgave."""
    longest = "c" * MAX_IDENTIFIER_LENGTH

    assert PanelColumn(longest, "float", (1.0,)).name == longest

    with pytest.raises(PanelBatchError):
        PanelColumn("c" * (MAX_IDENTIFIER_LENGTH + 1), "float", (1.0,))
    with pytest.raises(PanelBatchError):
        PanelColumn("c" * (MAX_IDENTIFIER_LENGTH - 1) + "\n", "float", (1.0,))


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


# --- whitespace: refused here, stripped by the row contract -------------------------------


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("kind", "  daily  "), ("provider_id", " tushare "), ("dataset", " prices_daily ")],
)
def test_a_value_with_surrounding_whitespace_is_refused_rather_than_silently_stripped(
    field_name: str, value: str
) -> None:
    """A deliberate divergence from the row contract, not an oversight.

    `ProviderRecord`/`ProviderBatch` set pydantic's `str_strip_whitespace=True`, so every
    `str` field there is silently normalised. This contract refuses instead, because it makes
    two promises normalisation would quietly break: `content_digest` is supposed to be a
    faithful function of what the caller handed over (`kind="  daily  "` and `kind="daily"`
    hashing alike would mean the digest describes a value the caller never supplied), and
    `dataset` becomes a directory name under `PanelStore`'s root (stripping would leave the
    caller's string and the path on disk different; accepting would create a partition
    directory called `" prices_daily "`). A caller who wants the row contract's behaviour
    writes `.strip()` and gets a digest over the value it actually meant.
    """
    with pytest.raises(PanelBatchError, match="leading or trailing whitespace"):
        _batch(**{field_name: value})


def test_the_row_contract_really_does_strip_what_this_one_refuses() -> None:
    """Pins the divergence above as a fact about both contracts rather than a claim about
    one, so a future change to either side shows up here."""
    from openalpha_cn.providers.base import ProviderRecord

    record = ProviderRecord(
        subject="000001.SZ",
        kind="  daily  ",
        timeline=Timeline(
            event_time=AVAILABLE,
            available_time=AVAILABLE,
            ingested_time=INGESTED,
            revision_time=INGESTED,
        ),
        summary="daily bar",
        payload={"close": 10.5},
    )

    assert record.kind == "daily"
    with pytest.raises(PanelBatchError):
        _batch(kind="  daily  ")


def test_the_contract_and_the_store_agree_that_a_padded_dataset_is_rejected() -> None:
    """The whitespace rule is duplicated across the same seam as the path-segment rule, so
    it gets the same drift pin: `domain/` cannot import `panel/`, so the two copies can only
    be held together by a test."""
    from openalpha_cn.panel.store import PanelStorageError, _validate_dataset

    for dataset in (" prices_daily", "prices_daily ", "\tprices_daily", "prices_daily\n"):
        with pytest.raises(PanelStorageError, match="leading or trailing whitespace"):
            _validate_dataset(dataset)
        with pytest.raises(PanelBatchError, match="leading or trailing whitespace"):
            panel_batch_module.validate_panel_dataset(dataset)


def test_the_batch_contract_and_the_panel_catalog_agree_on_the_readable_batch_stamps() -> None:
    """The third copy across this seam, and the one that only started existing when the stamp
    got a gate.

    `PANEL_BATCH_SCHEMA_VERSION` is what this contract *writes*;
    `panel/catalog.py::PANEL_BATCH_SCHEMA_VERSIONS_READABLE` is what `PanelStore` will accept
    into (and out of) a coverage record. `panel/` imports no sibling subpackage, so the second
    cannot import the first and the two are duplicated -- with the standing hazard that a
    `panel-batch/v2` bump here would make every fresh coverage record unreadable by the store
    that just wrote it. This test is what turns that into a failing test rather than a
    production refusal.

    The store must know the version this contract emits, and must know nothing this contract
    cannot emit -- a stamp in the readable set that no build ever writes is an unclosed door,
    which is the exact shape of the hole the P1 review found (`panel-batch/v99` accepted, and
    `assess_readiness` reporting `ready` with `issues == []`).
    """
    from openalpha_cn.panel.catalog import PANEL_BATCH_SCHEMA_VERSIONS_READABLE

    assert panel_batch_module.PANEL_BATCH_SCHEMA_VERSION in PANEL_BATCH_SCHEMA_VERSIONS_READABLE
    # Every readable stamp is one some build of this contract could have written, i.e. one
    # this contract's own `schema_version` field would accept.
    written = ColumnarPanelBatch.__dataclass_fields__["schema_version"].type
    for stamp in PANEL_BATCH_SCHEMA_VERSIONS_READABLE:
        assert stamp in str(written), (
            f"{stamp!r} is readable by the panel catalog but no ColumnarPanelBatch can carry "
            "it; the readable set must not be wider than the contract"
        )


# --- per-column typing and clock normalisation -------------------------------------------


def test_a_value_of_the_wrong_python_type_is_rejected_with_the_column_and_row_named() -> None:
    with pytest.raises(PanelBatchError, match="close"):
        PanelColumn("close", "float", (10.5, "22.5"))


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("float", 1),
        ("float", True),
        ("integer", True),
        ("integer", 1.0),
        ("boolean", 1),
        ("boolean", 0),
        ("string", 1),
    ],
)
def test_a_column_kind_admits_its_exact_python_type_and_never_a_subclass_or_a_near_miss(
    kind: str, value: object
) -> None:
    """`_EXACT_TYPES` is checked with `type(v) is expected`, not `isinstance`, and the
    difference is load-bearing for `content_digest` rather than merely pedantic:

    - `bool` is a subclass of `int`, so `isinstance` would admit `True` into an `integer`
      column, where it hashes as `true` rather than `1`. This is the one case that
      distinguishes the two spellings -- an `isinstance` mutation survives every other row
      of this table, and survived the whole suite before this test existed.
    - An `int` in a `float` column hashes as `1`, not `1.0`, so two batches holding the same
      numbers would carry different digests depending on how the caller happened to spell
      them.
    """
    with pytest.raises(PanelBatchError, match="expected"):
        PanelColumn("value", kind, (value,))  # type: ignore[arg-type]


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


def _row_model_verdict(clocks: dict[str, datetime]) -> str | None:
    try:
        Timeline(**clocks)
    except ValueError as error:
        return str(error)
    return None


def _column_model_verdict(clocks: dict[str, datetime]) -> str | None:
    try:
        TimelineColumns(**{name: (value,) for name, value in clocks.items()})
    except ValueError as error:
        return str(error)
    return None


def test_the_row_model_and_the_column_model_agree_on_every_single_row_verdict() -> None:
    """The drift pin `_check_ordering` was missing.

    `TimelineColumns._check_ordering` re-states `Timeline.__post_init__`'s ordering predicate
    in transposed form so it can scan a whole column at once. Two hand-written copies of one
    rule: whichever is edited second wins, silently. Demonstrated while reviewing `cb9e8f4`
    by adding a fifth rule to `Timeline` (`available_time` may not precede `event_time`) --
    the row model rejected, the column model accepted, and all 74 tests stayed green.

    Same shape of fix as `test_the_contract_and_the_store_agree_on_every_rejected_dataset`
    above: run both implementations over a randomised corpus of single rows and require an
    identical verdict, message included. The message comparison is not decoration -- the
    column model produces its error by handing the offending row to the real `Timeline`, and
    this is what holds it to that instead of re-wording the rule.
    """
    rng = random.Random(20260808)
    offsets = [-86_400_000_000, -1_000_000, -1000, -1, 0, 1, 1000, 1_000_000, 86_400_000_000]
    names = ("event_time", "available_time", "ingested_time", "revision_time")
    disagreements: list[tuple[dict[str, int], str | None, str | None]] = []
    rejected = 0

    for _ in range(400):
        micros = {name: rng.choice(offsets) for name in names}
        clocks = {name: AVAILABLE + timedelta(microseconds=value) for name, value in micros.items()}
        row_verdict = _row_model_verdict(clocks)
        column_verdict = _column_model_verdict(clocks)
        rejected += int(row_verdict is not None)
        if row_verdict != column_verdict:
            disagreements.append((micros, row_verdict, column_verdict))

    assert not disagreements, (
        f"the row model and the column model disagreed on {len(disagreements)} of 400 rows; "
        f"first three: {disagreements[:3]}"
    )
    assert 40 < rejected < 360, (
        f"corpus is degenerate: {rejected}/400 rows were rejected, so agreement would be "
        "trivially satisfiable"
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
    against DuckDB 1.5 while writing this task), so the same instant can arrive tagged
    differently across a storage round trip. What this test pins is the normalisation that
    makes that survivable: every timestamp goes through `ensure_aware` before it is hashed.

    It does *not* pin the choice of *integer microseconds* over `isoformat()` -- swapping
    `_epoch_micros` for `isoformat()` leaves this test green, because both are
    representation-independent once the value is UTC. That choice is a cost decision, not a
    correctness one (0.095 us versus 0.443 us per value, across at least five timestamp
    columns per batch), and it is argued in the module docstring rather than asserted here.
    """
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
