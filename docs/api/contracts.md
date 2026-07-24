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
uv run python -m openalpha_cn.domain.schema
uv run pytest tests/unit/domain/test_schema_export.py
```

The compatibility test fails when runtime models and checked-in schemas differ.
Changing an existing `/v1` contract requires a reviewed compatibility decision;
breaking changes use a new schema version and coexist with the old version
during migration.
