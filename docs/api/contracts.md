# OpenAlpha CN Contract Schemas

The files in `schemas/` are the canonical JSON serialization schemas for the
public research records. Each document is named after the version it holds, and
that name is derived from the contract's own version registry rather than typed
out (`domain/schema.py::schema_document_name`), so a document called `-v2` is a
`v2` document and cannot become a stale label:

- `evidence-snapshot-v1.json`
- `signal-frame-v1.json`
- `decision-ledger-v2.json`
- `run-manifest-v3.json`
- `validation-result-v2.json`

Regenerate them after an intentional contract change:

```powershell
uv run python -m openalpha_cn.schema_export
uv run pytest tests/unit/domain/test_schema_export.py
```

The compatibility test fails when runtime models and checked-in schemas differ.
Changing an existing published contract requires a reviewed compatibility
decision. Breaking changes cut a new schema version; the previous version stays
readable through `domain/versioning.py`'s registry (the `*V1` snapshot classes
in `domain/run.py`, `domain/decision.py` and `domain/validation.py` are what
read it) until `storage/migrations.py`'s `rewrite_contract_identities` has
advanced every stored row.

`V2-P4-001` cut three v2 documents in one window. Two of those bumps
move a content-addressed identity, so they are **not** upgraded transparently on
read: `decisions.decision_id` and `validation_results.validation_id` are stored
keys, and recomputing one at read time would leave every reference to it
pointing at the old value. Reading an un-migrated row of either contract raises
`IdentityRewriteRequiredError` and names the migration to run.

`V2-P4-010` cut `run-manifest/v3`, and it is the case that shows the rule is
about *references* rather than about a row's own key. `runs.run_id` is
caller-supplied and does not move, which is why `run-manifest/v1` upgrades
transparently on read to this day. But `V2-P4-025` put the manifest's content
address into `DecisionLedger.run_manifest_id` in between, so a v2 manifest
advanced at read time hands back an address no stored decision names -- and
nothing raises, because the reference is a pattern-checked string rather than a
foreign key. `run-manifest/v2` therefore refuses, and migration 8
(`rewrite_manifest_component_planes`) advances the run rows and re-keys the
decisions, validation results and reports behind them in one transaction.

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
