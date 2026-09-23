"""The performance-budget acceptance for V2-P1-001 -- the brief calls this "本任务最有说服力
的验收" (this task's most convincing acceptance evidence).

`storage/parquet.py`'s `ParquetEvidenceStore` is a correctness feature for a few thousand
discrete, individually-verifiable events (ADR-0002); at panel-plane scale (5,534 stocks x
~2,440 trading days ~= 1.35e7 rows/field, 152 columns for `balancesheet`) its three flaws
become a wall:

- F72: `sorted(root.glob("*.parquet"))` -- every query lists and scans every file, no
  partition pruning.
- (implicit in `query()`'s `SELECT * FROM read_parquet(?)`) -- no column projection; a
  152-column financial statement panel would always read all 152 columns.
- F74: `_deserialize()` rebuilds a full Pydantic model and recomputes a SHA-256 per row
  (`computed_field` does not cache), on every read.

Per the brief: prove partition pruning and column projection *structurally* -- via DuckDB's
own `EXPLAIN`/profiling output, not wall-clock timing (flaky in CI, gets `@skip`'d) -- and
prove zero pydantic construction / zero SHA-256 on the read path via instrumentation or AST,
not by eyeballing the source.

The synthetic panel below is generated at test time (never checked in -- `.parquet`/
`.duckdb` are `scripts/verify_publication.py` publication-blocked suffixes): 5 year
partitions x 400 rows x 26 columns is large enough to make "scanned 1 file instead of 5" and
"projected 3 columns instead of 26" measurable and unambiguous, without paying real 1.35e7
row I/O cost in a unit test.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from openalpha_cn.panel import store as panel_store_module
from openalpha_cn.panel.store import ColumnSpec, PanelStore

_STORE_SOURCE_PATH = Path(panel_store_module.__file__)


def _parse_store_module() -> ast.Module:
    source = _STORE_SOURCE_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(_STORE_SOURCE_PATH))


_YEARS = (2020, 2021, 2022, 2023, 2024)
_ROWS_PER_YEAR = 400
_PADDING_COLUMN_COUNT = 24  # plus ts_code + trade_date = 26 columns total.

_WIDE_COLUMNS = (
    ColumnSpec("ts_code", "VARCHAR"),
    ColumnSpec("trade_date", "VARCHAR"),
    *(ColumnSpec(f"field_{index}", "DOUBLE") for index in range(_PADDING_COLUMN_COUNT)),
)


def _wide_rows(year: int) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            f"{i:06d}.SZ",
            f"{year}-01-02",
            *(float(index * 10 + i) for index in range(_PADDING_COLUMN_COUNT)),
        )
        for i in range(_ROWS_PER_YEAR)
    )


def _build_wide_panel(root: Path) -> PanelStore:
    store = PanelStore(root)
    for year in _YEARS:
        store.write_partition("wide_panel", year, _WIDE_COLUMNS, _wide_rows(year))
    return store


# --- partition pruning: structural proof via DuckDB's own profiling output --------------


def test_query_prunes_to_the_single_requested_years_partition_file(tmp_path: Path) -> None:
    store = _build_wide_panel(tmp_path / "panel")
    all_partition_files = sorted((tmp_path / "panel" / "wide_panel").rglob("*.parquet"))
    assert len(all_partition_files) == len(_YEARS), (
        "fixture setup check: expected one Parquet file per year on disk"
    )

    profile = store.profile_query("wide_panel", year=2022, columns=["ts_code", "trade_date"])

    assert profile["Total Files Read"] == "1", (
        f"expected exactly 1 file scanned out of {len(all_partition_files)} on disk, "
        f"got profiling extra_info={profile}"
    )
    scanned_filename = Path(profile["Filename(s)"])
    assert scanned_filename == tmp_path / "panel" / "wide_panel" / "2022" / "data.parquet"
    for year in _YEARS:
        if year == 2022:
            continue
        untouched = tmp_path / "panel" / "wide_panel" / str(year) / "data.parquet"
        assert str(untouched) != profile["Filename(s)"], (
            f"query for year=2022 must not touch the {year} partition file"
        )


def test_pruned_query_reads_far_fewer_bytes_than_the_full_five_year_panel_on_disk(
    tmp_path: Path,
) -> None:
    """A structural (byte-count, not wall-clock) measure of the same pruning claim: the
    bytes DuckDB's profiler reports reading for one partition must be a small fraction of
    the total bytes the five partitions occupy on disk."""
    store = _build_wide_panel(tmp_path / "panel")
    total_bytes_on_disk = sum(
        path.stat().st_size for path in (tmp_path / "panel" / "wide_panel").rglob("*.parquet")
    )

    profile = store.profile_query("wide_panel", year=2022, columns=["ts_code"])

    bytes_read = int(profile["total_bytes_read"])
    assert 0 < bytes_read < total_bytes_on_disk / 2, (
        f"pruned query read {bytes_read} bytes; expected well under half of "
        f"{total_bytes_on_disk} bytes spread across all 5 partitions"
    )


# --- column projection: structural proof via DuckDB's own profiling output --------------


def test_query_projects_only_the_requested_columns_not_the_full_wide_schema(
    tmp_path: Path,
) -> None:
    store = _build_wide_panel(tmp_path / "panel")
    requested = ["ts_code", "field_3", "field_9"]

    profile = store.profile_query("wide_panel", year=2022, columns=requested)

    projected = profile["Projections"]
    assert isinstance(projected, list)
    assert set(projected) == set(requested)
    assert len(projected) == len(requested) == 3
    total_columns = len(_WIDE_COLUMNS)
    assert len(projected) < total_columns, (
        f"projected {len(projected)} columns; expected strictly fewer than "
        f"all {total_columns} columns in the schema"
    )


# --- zero pydantic construction on the read path: whole-module AST proof ----------------


def test_panel_store_module_never_imports_pydantic() -> None:
    """The panel read path returns plain tuples, never a Pydantic model -- unlike
    `ParquetEvidenceStore._deserialize`, which rebuilds a full `EvidenceSnapshot` (and
    recomputes its uncached SHA-256 `computed_field`) on every row. Checked for the whole
    module, not just `query()`, because the claim is that `panel/store.py` has no reason to
    construct a Pydantic model anywhere at this layer -- the panel plane does not yet model
    typed business records (that starts at `V2-P1-004`+)."""
    tree = _parse_store_module()
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "pydantic" not in imported_roots, (
        f"panel/store.py must not import pydantic at all; found imports of {imported_roots}"
    )


def test_panel_store_read_path_never_globs_the_dataset_directory() -> None:
    """AST proof of the fix for F72 (`sorted(root.glob("*.parquet"))`): no method on
    `PanelStore` may call `.glob(`/`.rglob(` at all. Partition selection instead goes
    through the persistent catalog's `panel_partitions` table (an indexed point lookup),
    resolving to an explicit single file path handed to `read_parquet`."""
    tree = _parse_store_module()
    glob_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"glob", "rglob"}
    ]
    assert not glob_calls, (
        "panel/store.py must never call Path.glob()/Path.rglob() -- partition resolution "
        "must go through the catalog, not directory listing"
    )


# --- zero SHA-256 on the read path: instrumentation (call-count spy), not AST -----------
#
# `write_partition` legitimately hashes its content for idempotency (mirroring
# `ParquetEvidenceStore.append`'s batch-hash short circuit), so a whole-module "never
# imports hashlib" AST assertion would be false by design. This has to be a dynamic
# call-count proof, scoped to the two operations, instead.


def test_write_path_hashes_content_but_query_path_calls_sha256_zero_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}
    real_sha256 = hashlib.sha256

    def _counting_sha256(*args: object, **kwargs: object) -> hashlib._Hash:
        calls["count"] += 1
        return real_sha256(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(panel_store_module, "sha256", _counting_sha256)
    store = PanelStore(tmp_path / "panel")
    store.write_partition("wide_panel", 2022, _WIDE_COLUMNS, _wide_rows(2022))
    assert calls["count"] > 0, (
        "the spy never fired during write_partition -- it is not wired to the real call "
        "site, so a 0 count on the read path below would be meaningless"
    )

    calls["count"] = 0
    rows = store.query("wide_panel", year=2022, columns=["ts_code"])

    assert len(rows) == _ROWS_PER_YEAR
    assert calls["count"] == 0, f"query() must call sha256 zero times, called {calls['count']}x"


def test_profile_query_path_also_calls_sha256_zero_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _build_wide_panel(tmp_path / "panel")
    calls = {"count": 0}
    real_sha256 = hashlib.sha256

    def _counting_sha256(*args: object, **kwargs: object) -> hashlib._Hash:
        calls["count"] += 1
        return real_sha256(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(panel_store_module, "sha256", _counting_sha256)

    store.profile_query("wide_panel", year=2022, columns=["ts_code"])

    assert calls["count"] == 0, (
        f"profile_query() must call sha256 zero times, called {calls['count']}x"
    )
