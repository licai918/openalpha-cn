"""`PanelStore.remove_partition` (`V2-P6-013`): what it removes, and who may call it.

The store's first delete. Every other dataset keeps replace-only semantics, and this file is
what keeps that true: the delete is an allowlist of exactly one caller, in the style of
`test_query_callers.py`, plus the store-level behaviour that caller relies on.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import write_panel_batch

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "openalpha_cn"

REMOVE_PARTITION_CALLERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("panel_ingest.py", "write_upstream_defects"),
        ("panel_ingest.py", "write_withdrawn_rows"),
    }
)
"""Every `src/` function allowed to call `remove_partition`, as `(file, function)`.

`write_upstream_defects` rebuilds the defects record from the drops the current build performs,
and a year whose build dropped nothing has to be able to become empty. `write_withdrawn_rows`
(`V2-P6-016`) keeps the withdrawn rows that record indexes, under the same rule: when the last
withdrawal of a year is retired -- its row served again -- the partition must become empty with
its index, or it would claim a withdrawal the stored data no longer reflects. No other writer
removes anything: a price, factor or registry partition is replaced whole or refused, never
deleted.
"""


def _callers() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(SOURCE).as_posix()
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "remove_partition"
                ):
                    found.add((relative, function.name))
    return found


def test_only_the_defects_writer_removes_a_partition() -> None:
    assert _callers() == set(REMOVE_PARTITION_CALLERS), (
        "PanelStore.remove_partition is the store's only delete and is granted to the defects "
        "record and the withdrawn rows it indexes alone; a new caller has to be argued for here"
    )


def _batch(dataset: str, year: int) -> ColumnarPanelBatch:
    instant = datetime(year, 6, 3, 8, 30, tzinfo=UTC)
    return ColumnarPanelBatch(
        provider_id="test",
        dataset=dataset,
        kind=dataset,
        as_of=instant,
        fetched_at=instant,
        status="success",
        subjects=("000001.SZ",),
        timeline=TimelineColumns(
            event_time=(instant,),
            available_time=(instant,),
            ingested_time=(instant,),
            revision_time=(instant,),
        ),
        columns=(PanelColumn("trade_date", "string", (date(year, 6, 3).isoformat(),)),),
    )


def test_a_removed_partition_leaves_no_file_no_catalog_row_and_no_coverage(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    reference = write_panel_batch(store, _batch("upstream_defects", 2024), year=2024)
    kept = write_panel_batch(store, _batch("upstream_defects", 2025), year=2025)
    assert reference.path.exists()

    assert store.remove_partition("upstream_defects", 2024) is True

    assert not reference.path.exists()
    assert store.registered_years("upstream_defects") == (2025,)
    assert store.read_coverage("upstream_defects", 2024) is None
    assert store.read_coverage("upstream_defects", 2025) is not None
    assert kept.path.exists()


def test_removing_a_partition_that_is_not_there_answers_false(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    assert store.remove_partition("upstream_defects", 2024) is False  # no catalog at all
    write_panel_batch(store, _batch("upstream_defects", 2025), year=2025)
    assert store.remove_partition("upstream_defects", 2024) is False  # catalog, no such year


@pytest.mark.parametrize("dataset", ["../escaped", "/abs/path", "a/b", ""])
def test_a_dataset_name_that_is_not_a_plain_segment_is_refused(
    tmp_path: Path, dataset: str
) -> None:
    with pytest.raises(PanelStorageError):
        PanelStore(tmp_path / "panel").remove_partition(dataset, 2024)
