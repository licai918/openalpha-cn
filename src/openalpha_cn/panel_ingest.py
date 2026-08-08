"""The seam between the columnar batch contract and `PanelStore` (`V2-P1-002`).

`domain/panel_batch.py` deliberately speaks *logical* column kinds (`"float"`,
`"timestamp"`, ...) and knows nothing about DuckDB; `panel/store.py` speaks DuckDB SQL types
and knows nothing about provider contracts. This module is the one place the two vocabularies
meet, and it is intentionally thin: a closed kind-to-type table, and a writer that hands the
store the batch's own transposed row block.

## Why it is a top-level module and not `panel/ingest.py`

The natural home would be inside `openalpha_cn.panel`, and `panel -> domain` would be a
perfectly ordinary downward dependency. It is not there because `V2-P1-001` pinned a
stronger property with `grimp`:
`tests/unit/test_import_layering.py::test_panel_package_has_zero_direct_edges_into_any_other_openalpha_cn_subpackage`
asserts that `openalpha_cn.panel` imports *no* sibling subpackage at all -- DuckDB and the
standard library only. Putting the seam under `panel/` would break that assertion, and the
project's answer to a package-scoped layering rule blocking a natural home is already on
record: `openalpha_cn/batch_contracts.py` was created as a neutral top-level sibling for
exactly this reason (V2-P0B-012), after a Critical review rejected relaxing the rule
instead. This module follows that precedent -- `panel/` stays self-contained, `domain/`
stays pure, and the one module that has to know about both sits above them.

## Why it is thin on purpose

The entire point of the columnar contract is that a batch reaches storage without being taken
apart a row at a time. `write_panel_batch()` therefore does no per-row work at all: column
specs come from `batch.storage_columns()` (built once, at batch construction) and the rows
come from `batch.to_rows()`, a single C-level `zip` transpose. Anything that walked the rows
here would hand back the throughput the contract exists to win.

## Why the type table is closed

`PanelStore.write_partition` builds its `CREATE TABLE` DDL by interpolation --
`f'"{column.name}" {column.duckdb_type}'` -- and `_build_scan_sql` builds its projection the
same way. Neither operand is escaped beyond the surrounding double quotes, so a column name
containing `"` closes the quote and everything after it executes as SQL, and a
`duckdb_type` is interpolated with no quoting at all. Both shapes were reproduced directly
against `43f3522` while writing this task (see this task's report). Nothing routed through
this module can express either one:

- the DuckDB type is never caller-supplied; it comes from `PANEL_DUCKDB_TYPES`, keyed by a
  `Literal` kind that the contract has already validated;
- the column name has already passed `validate_panel_identifier()`, which admits only plain
  ASCII identifiers, at `PanelColumn` construction.

That is a structural property of this path, not a fix to `PanelStore` itself -- `store.py`
remains as `V2-P1-001` left it, and its own callers are still responsible for what they pass
it. Closing the gap inside `store.py` is recorded as a follow-up rather than folded into this
task.

## What is deliberately not decided here

`year` is a required keyword argument, never derived from the batch. It is tempting to read
it off `event_time`, but panel `event_time` is a UTC instant while A-share partitioning is by
*trading date* in Asia/Shanghai: a session on 2024-01-01 08:00 CST is 2023-12-31 24:00 UTC,
so a UTC-derived year would silently misfile every early-January morning. Choosing the
partition key needs the real trading calendar that arrives with `V2-P1-004`; until then the
caller says which partition it means.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelBatchError, PanelColumnKind
from openalpha_cn.panel.store import ColumnSpec, PanelStore, PartitionRef

PANEL_DUCKDB_TYPES: Final[Mapping[PanelColumnKind, str]] = MappingProxyType(
    {
        "boolean": "BOOLEAN",
        "float": "DOUBLE",
        "integer": "BIGINT",
        "string": "VARCHAR",
        "timestamp": "TIMESTAMPTZ",
    }
)


def panel_column_specs(batch: ColumnarPanelBatch) -> tuple[ColumnSpec, ...]:
    """Translate a batch's storage columns into `PanelStore` column specs, in order."""
    return tuple(
        ColumnSpec(column.name, PANEL_DUCKDB_TYPES[column.kind])
        for column in batch.storage_columns()
    )


def write_panel_batch(store: PanelStore, batch: ColumnarPanelBatch, *, year: int) -> PartitionRef:
    """Write one columnar batch into the `(batch.dataset, year)` partition.

    Overwrite-per-partition and content-hash-idempotent, exactly as
    `PanelStore.write_partition` documents -- this function adds no storage semantics of its
    own.

    A `no_data` batch is refused rather than written: it carries no rows by construction, so
    writing it would either raise deep inside the store ("cannot write an empty partition
    batch") or, worse, be mistaken for a successful empty partition. Explicit no-data is a
    result to record somewhere, not a partition to create -- the same distinction
    `ProviderBatch` draws between `status="no_data"` and an empty success.
    """
    if batch.status != "success":
        raise PanelBatchError(
            f"cannot write a {batch.status!r} batch to a partition: {batch.no_data_reason!r}"
        )
    return store.write_partition(batch.dataset, year, panel_column_specs(batch), batch.to_rows())
