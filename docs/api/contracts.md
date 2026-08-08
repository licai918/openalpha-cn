# OpenAlpha CN v1 Contract Schemas

The files in `schemas/` are the canonical JSON serialization schemas for the
public v1 research records:

- `evidence-snapshot-v1.json`
- `signal-frame-v1.json`
- `decision-ledger-v1.json`
- `run-manifest-v1.json`
- `validation-result-v1.json`

Regenerate them after an intentional contract change:

```powershell
uv run python -m openalpha_cn.schema_export
uv run pytest tests/unit/domain/test_schema_export.py
```

The compatibility test fails when runtime models and checked-in schemas differ.
Changing an existing `/v1` contract requires a reviewed compatibility decision;
breaking changes use a new schema version and coexist with the old version
during migration.

## Runtime records

The following strict Pydantic records are also exposed through the Python SDK
and HTTP API. They are runtime contracts rather than exported canonical research
schemas:

- `RunRecoveryState`: request digest, graph signature, completed node results,
  next-node index, attempt count, status, timestamps, and failure type;
- `MemoryEntry`: decision-linked durable research memory;
- `PortfolioState`: cash, position lots, valuation marks, fees, and realized PnL;
- `PortfolioOrder` and `PortfolioTransition`: one order intent and its immutable
  accepted/rejected before-and-after result;
- `PortfolioLimits`: maximum single-position and total-exposure weights.

All reject paths are explicit. A rejected portfolio order returns the unchanged
state and a reason; a recovery request with a changed immutable input or graph
signature raises a conflict instead of silently reusing stale state.
