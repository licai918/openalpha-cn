# OpenAlpha CN v1 Implementation Plan

This plan implements `openalpha-cn-v1-spec.md` in thin, independently verifiable vertical slices.

## Phase 1: Repository foundation

- [x] Task 1.1: Establish repository policy and metadata
  - Acceptance: MIT license, repository instructions, ignore rules, attributes, security and contribution boundaries exist.
  - Verify: repository validation test passes; secret/runtime/binary patterns are ignored.
  - Files: `AGENTS.md`, `LICENSE`, `.gitignore`, `.gitattributes`, `SECURITY.md`

- [x] Task 1.2: Establish the Python package and stable developer commands
  - Acceptance: package installs with `uv`; CLI exposes `version` and `doctor`.
  - Verify: CLI unit tests fail before implementation and pass after implementation.
  - Files: `pyproject.toml`, `src/openalpha_cn/__init__.py`, `src/openalpha_cn/cli.py`, `tests/unit/test_cli.py`

- [x] Task 1.3: Establish project documentation and product split
  - Acceptance: README explains OpenAlpha CN, self-host path, ChainLin download path, license split, and product boundary.
  - Verify: documentation link and asset validation test passes.
  - Files: `README.md`, `README.en.md`, `THIRD_PARTY_NOTICES.md`, `assets/brand/wechat-contact-qr.jpg`

### Checkpoint 1

- [x] `uv sync --all-extras --dev` succeeds.
- [x] `uv run pytest` succeeds.
- [x] `uv run ruff check .` succeeds.
- [x] repository is initialized on `main`.

## Phase 2: Point-in-time contracts

- [x] Task 2.1: Implement strict timezone and visibility rules.
- [x] Task 2.2: Implement immutable `EvidenceSnapshot`.
- [x] Task 2.3: Implement `SignalFrame`, `DecisionLedger`, `RunManifest`, and `ValidationResult`.
- [x] Task 2.4: Add JSON schema export and compatibility tests.

### Checkpoint 2

- [x] Naive datetimes are rejected.
- [x] Evidence after `as_of` is rejected.
- [x] Stable payloads produce stable IDs and hashes.
- [x] Contract schemas are versioned and checked into `docs/api/schemas`.

## Phase 3: Storage and providers

- [x] Task 3.1: Implement SQLite run and checkpoint repository.
- [x] Task 3.2: Implement Parquet event store and DuckDB point-in-time query.
- [x] Task 3.3: Define the shared provider contract and explicit failure model.
- [x] Task 3.4: Implement file provider for CSV, JSONL, JSON, and Parquet.
- [x] Task 3.5: Implement Tushare BYOT adapter.
- [x] Task 3.6: Implement optional AKShare research adapter.

### Checkpoint 3

- [x] Provider contract suite passes for every enabled provider.
- [x] External-provider tests use frozen payloads and no real credentials.
- [x] Failed providers never appear as successful empty datasets.
- [x] Historical query returns only records visible at `as_of`.

## Phase 4: Evidence vertical slice

- [x] Task 4.1: Normalize limit-up, broken-board, and consecutive-board events.
- [x] Task 4.2: Normalize disclosure, theme, catalyst, and capital observations.
- [x] Task 4.3: Build evidence snapshots with provenance and quality flags.
- [x] Task 4.4: Expose snapshot creation through CLI and REST API.

### Checkpoint 4

- [x] A user can import frozen events and create a traceable evidence snapshot.
- [x] Every evidence item has source, time, URI policy, and content hash.
- [x] API and CLI return the same structured snapshot.

## Phase 5: Research and decision vertical slice

- [x] Task 5.1: Define agent, tool, router, risk, and memory contracts.
- [x] Task 5.2: Implement deterministic baseline market, theme, capital, and risk agents.
- [x] Task 5.3: Implement optional LLM model provider boundary and structured-output validation.
- [x] Task 5.4: Implement signal construction, abstention, and invalidation rules.
- [x] Task 5.5: Append decisions and run manifests to the ledger.

### Checkpoint 5

- [x] A research run goes from evidence to signal to decision.
- [x] Every conclusion cites evidence IDs.
- [x] Insufficient evidence returns an explicit abstention.
- [x] Retry and recovery are idempotent.

## Phase 6: Replay, backtest, and attribution

- [x] Task 6.1: Implement shared `run_cycle` clock and mode adapters.
- [x] Task 6.2: Implement A-share execution constraints and transaction costs.
- [x] Task 6.3: Implement outcome validation and benchmark comparison.
- [x] Task 6.4: Implement factor and agent attribution.
- [x] Task 6.5: Build 60-trading-day / 300-event frozen replay corpus.

### Checkpoint 6

- [x] Live and replay modes share the same decision core.
- [x] Known look-ahead violations are zero.
- [x] Frozen-payload replay success is at least 99%.
- [x] Attribution reconciles with total simulated outcome.

## Phase 7: SDK, API, CLI, and web

- [x] Task 7.1: Stabilize OpenAPI and Python SDK interfaces.
- [x] Task 7.2: Implement market/event/theme/evidence/research/backtest endpoints.
- [x] Task 7.3: Implement the research workbench shell and health/data-status view.
- [x] Task 7.4: Implement evidence inspection and research-run flow.
- [x] Task 7.5: Implement decision, replay, and attribution views.

### Checkpoint 7

- [x] API, SDK, CLI, and web complete the same golden flow.
- [x] Browser console is clean.
- [x] Loading, error, stale-data, and insufficient-evidence states are visible.
- [x] Critical Playwright flow passes.

## Phase 8: Delivery and publication

- [x] Task 8.1: Complete Docker Compose and persistent-volume recovery tests.
- [x] Task 8.2: Complete Windows/Linux CI, security, license, and packaging gates.
- [x] Task 8.3: Complete Chinese/English documentation and release handoff.
- [x] Task 8.4: Publish source repository and `v1.0.0`.
- [x] Task 8.5: Publish ChainLin installer under `chainlin-desktop-v1.0.9`.
- [x] Task 8.6: Verify anonymous clone, container start, installer download, and checksums.

### Final checkpoint

- [x] All v1 specification success criteria pass.
- [x] All features have a unique destination and evidence.
- [x] `UNREVIEWED=0`; `UNKNOWN=0`.
- [x] No secret, user database, or unlicensed raw dataset is published.
- [x] Release handoff records commit SHA, tags, assets, sizes, hashes, CI, and known limitations.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Third-party data redistribution rights are unclear | High | BYOT/local adapters, no bundled raw data, provider-level license metadata |
| Historical data contains look-ahead revisions | High | Four timestamps, immutable revisions, replay guards and fixtures |
| LLM output is nondeterministic | High | Structured contracts, frozen outputs, deterministic baseline, abstention |
| Desktop installer is unsigned | Medium | Explicit warning, SHA-256, antivirus scan, later code signing |
| Large scope produces incomplete surfaces | High | Feature flags, vertical slices, checkpoint gates |
| Windows and Linux path/runtime differences | Medium | Dual-platform CI from first checkpoint |
