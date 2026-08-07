# ADR-0002: Two data planes — panel and evidence

Date: 2026-08-06
Status: Accepted

## Context

These are measured facts about the current codebase, not projections:

1. `` `src/openalpha_cn/providers/tushare.py#TushareProvider` `` only ever produces
   `kind="daily"`, and the `_NORMALIZERS` mapping in
   `` `src/openalpha_cn/evidence/builder.py#_NORMALIZERS` `` (lines 55-63) has no entry for
   `"daily"` — `` `src/openalpha_cn/evidence/builder.py#EvidenceBuilder` ``'s `_build_one`
   raises `unsupported evidence kind: daily`. **The only paid data source we have today
   cannot enter the evidence layer at all.**
2. `storage/parquet.py` writes one flat `part-<hash>.parquet` file per batch; `query()`
   performs a full scan with `root.glob("*.parquet")` and opens a fresh
   `duckdb.connect(":memory:")` on every call; `_deserialize()` rebuilds a complete Pydantic
   model per row and recomputes its SHA-256 (`computed_field` is not cached, so every access
   re-runs one JSON parse plus three canonical serializations plus three SHA-256 hashes, per
   row).
3. Panel scale: the full listed market is 5,534 stocks (measured from Tushare's
   `stock_basic`) × roughly 2,440 trading days ≈ 1.35×10⁷ rows per field. `balancesheet`
   alone has 152 columns.

## Decision

Split storage into two data planes:

| Plane | Contents | Storage | Consumers |
|---|---|---|---|
| Panel plane (new) | Trading calendar; stock basics and renames; adjustment factors; daily price/volume; market cap and valuation; suspensions; limit-up/down; index constituents; industry classification; financial statements and indicators; **factor observations** | Parquet partitioned by `dataset/year/`, plus a **persistent** DuckDB catalog | Factor layer, model layer, portfolio layer |
| Evidence plane (carried over from v1, unchanged) | Discrete, citable events: limit-up / broken-board / consecutive-board, disclosure filings, thematic catalysts, capital flow | The existing `` `src/openalpha_cn/storage/parquet.py#ParquetEvidenceStore` `` | Agents, `run_cycle`, candidate re-review |

The panel plane's storage layer is not implemented as part of this ADR — this decision fixes
the data model and the plane boundary, not the code. No concrete panel-plane storage module
exists yet.

## Consequences

1. **`ParquetEvidenceStore` is not rewritten.** Its per-row rebuild-and-rehash is a
   verifiability strength on the evidence plane — every read is independently re-provable.
   At panel-plane scale (10⁷ rows), the same design is a performance disaster, not a
   correctness feature. Separating the planes means the fix is simpler than a rewrite: keep
   panel data from ever flowing into the evidence store.
2. **The correct fix for `kind="daily"` is not to register a `"daily"` normalizer.** It is
   to route Tushare's daily output to the panel plane instead. This has to be written down
   explicitly, or a future contributor will "conveniently" add the missing normalizer and
   push on the order of 10⁷ price/volume records into a store designed for discrete,
   low-volume evidence.
3. Factor observations belong to the panel plane. They must never be written to the evidence
   store, even though they are computed from panel data and could plausibly be mistaken for
   "events."

## Guardrail

Panel-plane data (every row in the left column of the table above, including factor
observations) must never be written through
`` `src/openalpha_cn/storage/parquet.py#ParquetEvidenceStore` ``. The evidence plane's
implementation does not change as part of this decision. See
[ADR-0003](./ADR-0003-numerical-stack-boundary.md) for the numerical-stack boundary the new
panel plane introduces, and [ADR-0001](./ADR-0001-local-first-runtime.md) for the storage
choices this decision builds on.
