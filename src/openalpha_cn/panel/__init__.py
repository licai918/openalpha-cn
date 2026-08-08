"""Panel data plane (ADR-0002): `dataset/year/`-partitioned Parquet with a persistent
DuckDB catalog.

Separate from `openalpha_cn.storage.parquet.ParquetEvidenceStore` (the evidence plane,
unchanged by this package's introduction): that store rebuilds a full Pydantic model and
recomputes a SHA-256 for every row it reads, one file per append, full-directory glob per
query -- a correctness feature at evidence-plane scale (a few thousand discrete, individually
citable events) and a performance wall at panel-plane scale (~1.35e7 rows/field across 5,534
stocks x ~2,440 trading days). See `docs/architecture/ADR-0002-two-data-planes.md` and
`docs/architecture/ADR-0003-numerical-stack-boundary.md`.

No concrete dataset (prices, fundamentals, calendar, ...) is modeled here -- that starts at
`V2-P1-004`. This package is the storage skeleton only: `PanelStore` writes/reads
partitions generically, given caller-supplied column names and types.
"""

from __future__ import annotations

from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore, PartitionRef

__all__ = [
    "ColumnSpec",
    "PanelStorageError",
    "PanelStore",
    "PartitionRef",
]
