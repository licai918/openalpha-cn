"""`V2-P6-022`: an unchanged partition is not re-assessed for every session read, and a changed
one always is.

`load_daily_bars` and `load_price_limits` read one session per call, and each call assessed its
partition afresh -- a DuckDB connection, the catalog row, the file's magic and footer, and the whole
coverage record with its census. On one horizon-20 backtest of the research store that was 13,483
assessments and 206 s, for partitions that did not change once during the run.

`PanelStore` now keeps each `(dataset, year)`'s `PartitionState` beside the fingerprint it was read
under -- the catalog file's identity, size, both timestamps and its header bytes, the WAL's, and a
count this process bumps on every catalog write -- plus the partition file's own identity, size,
timestamps, and the bytes its magic and footer length live in. A state is served again only while
every one of those is what it was.

The first test is the saving, counted in catalog round trips rather than seconds. Every other test
changes the partition between two reads in one of the ways it can change -- through the same
store, through another store on the same root, through a raw catalog write no store saw, by
damaging the file behind the store's back, and by deleting it -- and requires the second verdict
to be the new partition's. A cache that ignores its fingerprint fails every one of them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel import store as store_module
from openalpha_cn.panel.catalog import (
    KNOWN_STORAGE_LIMITATIONS,
    DatasetReadiness,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import write_panel_batch

DATASET = "prices_daily"
YEAR = 2025
AS_OF = datetime(2027, 1, 5, tzinfo=UTC)
FROZEN = datetime(2027, 1, 5, 1, 0, tzinfo=UTC)


def _batch(subjects: int, *, close: float = 10.0) -> ColumnarPanelBatch:
    codes = tuple(f"{index:06d}.SZ" for index in range(subjects))
    event = datetime(YEAR, 6, 2, 7, 0, tzinfo=UTC)
    available = datetime(YEAR, 6, 2, 8, 30, tzinfo=UTC)
    written_at = datetime(YEAR, 6, 3, tzinfo=UTC)
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=DATASET,
        kind="daily",
        as_of=written_at,
        fetched_at=written_at,
        status="success",
        subjects=codes,
        timeline=TimelineColumns(
            event_time=(event,) * subjects,
            available_time=(available,) * subjects,
            ingested_time=(available,) * subjects,
            revision_time=(available,) * subjects,
        ),
        columns=(PanelColumn("close", "float", tuple(close + index for index in range(subjects))),),
    )


REQUIREMENT = ReadinessRequirement(
    dataset=DATASET,
    as_of=AS_OF,
    years=(YEAR,),
    required_dates=None,
    required_subjects=None,
    required_fields=("close",),
    max_staleness=None,
)


def _store(root: Path) -> PanelStore:
    return PanelStore(root, clock=lambda: FROZEN)


def _verdict(readiness: DatasetReadiness) -> tuple[str, int, tuple[str, ...]]:
    return (
        readiness.state,
        readiness.row_count,
        tuple(sorted(issue.code for issue in readiness.issues)),
    )


@pytest.fixture
def coverage_reads(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """How many coverage records have been read out of any catalog since the fixture began."""
    count = 0
    real = store_module._read_coverage

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal count
        count += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(store_module, "_read_coverage", counting)
    return lambda: count


def test_an_unchanged_partition_is_assessed_once_for_many_session_reads(
    tmp_path: Path, coverage_reads: Callable[[], int]
) -> None:
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(20), year=YEAR)
    before = coverage_reads()

    verdicts = {_verdict(store.assess_readiness(REQUIREMENT)) for _ in range(25)}
    rows = {
        len(store.read_visible_at(REQUIREMENT, year=YEAR, columns=("close",)).rows)
        for _ in range(25)
    }

    assert verdicts == {("ready", 20, ())}
    assert rows == {20}
    assert coverage_reads() - before == 1, (
        f"{coverage_reads() - before} coverage reads for 50 reads of one unchanged partition"
    )


def test_a_partition_rewritten_through_the_same_store_is_re_assessed(
    tmp_path: Path, coverage_reads: Callable[[], int]
) -> None:
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(20), year=YEAR)
    assert _verdict(store.assess_readiness(REQUIREMENT)) == ("ready", 20, ())

    write_panel_batch(store, _batch(25), year=YEAR)

    assert _verdict(store.assess_readiness(REQUIREMENT)) == ("ready", 25, ())
    rows = store.read_visible_at(REQUIREMENT, year=YEAR, columns=("close",)).rows
    assert len(rows) == 25


def test_a_partition_rewritten_through_another_store_is_re_assessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer's process-wide count is switched off, so only the files can say it moved --
    which is the situation of a write made by another process."""
    reader = _store(tmp_path / "panel")
    write_panel_batch(reader, _batch(20), year=YEAR)
    assert _verdict(reader.assess_readiness(REQUIREMENT)) == ("ready", 20, ())
    monkeypatch.setattr(store_module, "_note_catalog_write", lambda key: None)

    write_panel_batch(_store(tmp_path / "panel"), _batch(25, close=50.0), year=YEAR)

    assert _verdict(reader.assess_readiness(REQUIREMENT)) == ("ready", 25, ())


def test_a_write_through_any_store_in_this_process_is_seen_even_by_blind_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem whose timestamps, sizes and bytes could not tell a write apart -- modelled by
    making every file fingerprint one constant -- still re-assesses after a write made through
    any `PanelStore` in this process, because each catalog write is counted as it releases."""
    reader = _store(tmp_path / "panel")
    write_panel_batch(reader, _batch(20), year=YEAR)
    monkeypatch.setattr(store_module, "_file_fingerprint", lambda path, **_: ("blind",))
    assert _verdict(reader.assess_readiness(REQUIREMENT)) == ("ready", 20, ())
    assert _verdict(reader.assess_readiness(REQUIREMENT)) == ("ready", 20, ())

    write_panel_batch(_store(tmp_path / "panel"), _batch(25, close=50.0), year=YEAR)

    assert _verdict(reader.assess_readiness(REQUIREMENT)) == ("ready", 25, ())


def test_a_catalog_row_changed_by_a_raw_write_is_re_assessed(tmp_path: Path) -> None:
    """A write no `PanelStore` made at all: the catalog's own bytes are the only witness."""
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(20), year=YEAR)
    assert _verdict(store.assess_readiness(REQUIREMENT)) == ("ready", 20, ())

    with duckdb.connect(str(store.catalog_path)) as connection:
        connection.execute(
            "UPDATE panel_partitions SET content_hash = 'tampered' WHERE dataset = ? AND year = ?",
            [DATASET, YEAR],
        )

    state, _, issues = _verdict(store.assess_readiness(REQUIREMENT))
    assert state == "blocked"
    assert issues


def test_a_partition_file_damaged_behind_the_store_is_re_assessed(tmp_path: Path) -> None:
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(20), year=YEAR)
    assert _verdict(store.assess_readiness(REQUIREMENT)) == ("ready", 20, ())

    (parquet,) = sorted(Path(store.root).glob(f"{DATASET}/**/*.parquet"))
    with parquet.open("r+b") as handle:
        handle.seek(-4, 2)
        handle.write(b"XXXX")

    state, _, issues = _verdict(store.assess_readiness(REQUIREMENT))
    assert state == "blocked"
    assert issues


def test_a_deleted_partition_file_is_re_assessed(tmp_path: Path) -> None:
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(20), year=YEAR)
    assert _verdict(store.assess_readiness(REQUIREMENT)) == ("ready", 20, ())

    (parquet,) = sorted(Path(store.root).glob(f"{DATASET}/**/*.parquet"))
    parquet.unlink()

    state, _, issues = _verdict(store.assess_readiness(REQUIREMENT))
    assert state == "blocked"
    assert issues


def test_a_removed_partition_is_re_assessed(tmp_path: Path) -> None:
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(20), year=YEAR)
    assert _verdict(store.assess_readiness(REQUIREMENT)) == ("ready", 20, ())

    assert store.remove_partition(DATASET, YEAR)

    state, _, issues = _verdict(store.assess_readiness(REQUIREMENT))
    assert state == "blocked"
    assert issues


def test_the_residue_of_the_fingerprint_is_disclosed() -> None:
    codes = {limitation.code for limitation in KNOWN_STORAGE_LIMITATIONS}

    assert "a_partition_state_is_reused_while_its_catalog_and_file_fingerprints_stand" in codes
