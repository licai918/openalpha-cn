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
standard library only. Putting the seam under `panel/` would break that assertion.

A neutral top-level module is one answer to that, and the project has reached for it before:
`openalpha_cn/batch_contracts.py` was created as a neutral top-level sibling in V2-P0B-012,
after a Critical review rejected relaxing a layering rule instead. The precedent is about the
*response* -- move the module rather than weaken the contract -- and not about the direction,
which is not the same here and should not be read as such: `batch_contracts` sits *below* the
packages it decouples (`storage -> batch_contracts -> domain`), whereas this module sits
*above* the package it must not be inside (`panel_ingest -> panel`).

Nor is a top-level module the only shape that satisfies the rule. The seam could instead be
inverted with the Protocol-plus-injection pattern this codebase already uses throughout
(`runtime/repository.py`'s `RunRepository`, `evidence/service.py`'s `EvidenceStore`, and the
rest of `OA-OPS-019`): `panel/` would declare the narrow writer Protocol it needs, and a
composition root would hand it an implementation. This task's reviewer built exactly that in
a scratch worktree and reported it working. It is not what is here because the seam
currently has exactly one consumer and one producer, and a Protocol pair plus a composition
edge is a heavier structure than a ten-line translation table earns until `V2-P1-004` gives
it a second caller. If that changes, the inversion is the upgrade path, not a rewrite.

## Why it is thin on purpose

The entire point of the columnar contract is that a batch reaches storage without being taken
apart a row at a time. `write_panel_batch()` therefore does no per-row work at all: column
specs come from `batch.storage_columns()` (built once, at batch construction) and the rows
come from `batch.to_rows()`, a single C-level `zip` transpose. Anything that walked the rows
here would hand back the throughput the contract exists to win.

## Why the type table is closed

`PanelStore.write_partition` builds its `CREATE TABLE` DDL by interpolation and
`_build_scan_sql` builds its projection and `WHERE` keys the same way. Two operands used to
reach that SQL unprotected -- a column name inside bare double quotes, which a `"` in the
name closes, and a `duckdb_type` with no quoting at all -- and both were reproduced as live
injections, first against `43f3522` and again against `cb9e8f4` (see task 25's review
report).

An earlier version of this section claimed that "nothing routed through this module can
express either one", and deferred fixing `store.py` on the strength of that claim. The claim
was false, and the review demonstrated it: `ColumnarPanelBatch.columns` is an ordinary tuple
field, so a column object that never ran `PanelColumn.__post_init__` -- a duck-typed
stand-in, or a subclass that overrides it away -- carried a hostile name straight through to
the DDL, and the `ATTACH` inside that name created a file at a path of the attacker's
choosing while `write_panel_batch()` returned normally. Two things changed as a result:

- `ColumnarPanelBatch._check_shape` now re-validates every caller-supplied column's name and
  kind (and rejects anything that is not a `PanelColumn`) instead of trusting that
  construction ran. That is what makes the property above actually hold for this path.
- `panel/store.py` escapes every identifier it interpolates and accepts a `duckdb_type` only
  from the closed `DUCKDB_COLUMN_TYPES` set. The gap is closed at the storage layer too, so
  it no longer depends on any one caller's discipline.

The type table here stays closed for the same reason it always was: the DuckDB type is never
caller-supplied, it comes from `PANEL_DUCKDB_TYPES` keyed by a validated logical kind, and
`tests/integration/panel/test_panel_store_hardening.py` pins its values as a subset of the
store's own accepted set.

## Known gaps, recorded rather than fixed here

- **`content_digest` is never persisted.** `write_panel_batch()` hands `PanelStore` the
  batch's rows and column specs; the store computes its own `content_hash` over that
  material for idempotency, and the batch's digest -- the thing that would let a *later*
  reader re-prove that a partition still holds what the provider sent -- is dropped on the
  floor. Today the digest is only checkable while the batch object is still in memory. Giving
  the catalog a column for it belongs with `V2-P1-003`'s data catalog, which is where
  partition-level metadata is being designed.
- **No `src/` caller yet.** Nothing in `src/openalpha_cn` calls `write_panel_batch()`; the
  first real one arrives with `V2-P1-004`, when a dataset is actually wired up. Until then
  this seam is exercised by tests only, which is expected for a contract that deliberately
  landed ahead of its consumer.

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
