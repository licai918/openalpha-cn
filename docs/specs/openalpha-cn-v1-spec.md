# OpenAlpha CN v1 Specification

Status: Approved for implementation
Owner: ss8875
License: MIT
Local repository: `D:\d-soft\openalpha-cn`
Remote repository: `https://github.com/ss8875/openalpha-cn`

## 1. Objective

OpenAlpha CN is an A-share-native, evidence-traceable, multi-agent research system. It turns point-in-time market data, disclosures, themes, catalysts, and capital-flow observations into reproducible research runs, structured signals, decision records, backtests, and attribution.

The v1 release serves two user paths:

1. Developers and researchers self-host OpenAlpha CN, connect data and model providers, and extend the research engine.
2. Users who do not want to deploy locally download the separately licensed ChainLin Limit-Up Review desktop application from a dedicated GitHub Release.

OpenAlpha CN is a research and review tool. It does not provide investment advice, broker execution, guaranteed returns, or an included right to redistribute third-party market data.

## 2. Assumptions

1. The repository is public and uses the MIT license.
2. The default deployment is local-first and single-node.
3. Python 3.11 is the minimum runtime; Python 3.12 is covered by CI.
4. The initial web client targets current Chromium, Firefox, and Safari releases.
5. Data provider credentials are supplied and stored by the user.
6. Live broker execution is outside v1 scope.
7. Source releases and ChainLin desktop releases use separate tag namespaces.
8. The approved implementation plan in the 2026-07-24 project conversation is the v1 scope baseline.

## 3. Product Success Criteria

The v1 release is complete only when:

- Every research conclusion references evidence IDs with source, availability time, ingestion time, revision time, and content hash.
- Point-in-time replay has zero known severe look-ahead violations.
- A fixed input and frozen provider payload reproduce the same structured result.
- Live research and historical replay call the same `run_cycle` core path.
- Provider, agent, tool, risk, memory, and validator extensions use documented contracts.
- At least 60 trading days and 300 representative events pass deterministic replay validation.
- Provider failures are explicit; silent empty-success degradation is forbidden.
- REST API, Python SDK, CLI, and the core web research flow are usable.
- Windows and Linux CI pass unit, contract, integration, replay, security, and packaging checks.
- README, architecture, deployment, data licensing, API, security, contribution, and release handoff documentation are complete.
- The ChainLin 1.0.9 installer is published as a Release asset with size, SHA-256, unsigned-binary disclosure, and verified download.
- `UNREVIEWED=0` and `UNKNOWN=0` in the feature destination ledger.

## 4. Technology Stack

### Backend and core

- Python 3.11+
- `uv` for dependency and environment management
- FastAPI for HTTP APIs
- Pydantic v2 for versioned domain contracts
- DuckDB and Parquet for analytical and point-in-time datasets
- SQLite WAL for run metadata, decisions, checkpoints, and durable local jobs
- Typer for the CLI
- pytest for tests
- Ruff for linting and formatting
- mypy for static type checking

### Web

- TypeScript
- React
- Vite
- pnpm
- Vitest and Testing Library
- Playwright for critical end-to-end flows

### Delivery

- Docker and Docker Compose
- GitHub Actions
- GitHub Container Registry
- GitHub Releases

Exact dependency versions are locked in repository lock files and updated through reviewed dependency changes.

## 5. Commands

The following commands are the required stable developer interface:

```powershell
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run openalpha doctor
uv run openalpha serve
```

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm lint
pnpm test
pnpm build
pnpm exec playwright test
```

```powershell
docker compose -f deploy/compose.yml up -d
docker compose -f deploy/compose.yml ps
```

## 6. Project Structure

```text
src/openalpha_cn/domain       Versioned domain contracts
src/openalpha_cn/providers    Data and model provider contracts/adapters
src/openalpha_cn/evidence     Evidence normalization and snapshots
src/openalpha_cn/agents       Agent contracts and built-in research roles
src/openalpha_cn/runtime      Router, graph, run cycle, retry, checkpoint
src/openalpha_cn/decisions    Signals, risk, decision ledger, attribution
src/openalpha_cn/backtest     Point-in-time replay and evaluation
src/openalpha_cn/storage      DuckDB/Parquet and SQLite repositories
src/openalpha_cn/api          FastAPI application and routes
src/openalpha_cn/cli          Typer commands
tests/unit                    Pure deterministic behavior
tests/contract                Provider and extension contracts
tests/integration             Storage and API boundaries
tests/replay                  Point-in-time golden scenarios
tests/e2e                     Critical product flows
web                           React research workbench
docs                          Architecture, data, APIs, deployment, release
deploy                        Container and deployment assets
assets                        Repository-owned brand assets
```

## 7. Core Contracts

### EvidenceSnapshot

Immutable evidence package containing:

- `evidence_id`
- `subject`
- `kind`
- `event_time`
- `available_time`
- `ingested_time`
- `revision_time`
- `source_id`
- `source_uri`
- `content_hash`
- structured payload and human-readable summary

### SignalFrame

Structured research output containing:

- subject and as-of time
- direction, strength, confidence, horizon
- evidence references
- confirmation and invalidation conditions
- risk flags
- abstention reason when evidence is insufficient

### DecisionLedger

Append-only record containing:

- run and decision IDs
- participating agents and their structured outputs
- routing and risk decisions
- final action class
- evidence and signal references
- timestamps and code/model/prompt versions

### RunManifest

Reproduction record containing:

- code commit
- configuration digest
- provider payload digests
- model and prompt versions
- random seed
- environment metadata
- start/end status and checkpoints

### ValidationResult

Outcome and attribution record containing:

- evaluated signal/decision
- observation window
- realized outcome
- benchmark and costs
- rule, factor, and agent attribution
- confidence and data-quality notes

## 8. Data Source Policy

### Enabled by default

- User-owned CSV, JSON, JSONL, and Parquet imports
- Synthetic fixtures shipped with the repository
- Tushare Pro through a user-supplied token

### Optional and disabled by default

- AKShare research adapter, with explicit academic/research-use warning
- Official exchange and disclosure metadata adapters after source-specific terms are recorded
- Commercial provider adapters requiring user-held licenses

### Never bundled or redistributed by default

- User tokens, cookies, passwords, or model keys
- ChainLin local databases or private runtime data
- Paid-provider raw datasets
- Scraped third-party data without confirmed redistribution permission

Every provider declares credential requirements, caching rules, redistribution status, rate limits, freshness expectations, and failure semantics.

## 9. Testing Strategy

- Unit tests cover pure time, hashing, normalization, evidence, signal, and decision rules.
- Contract tests run every provider against a shared behavior suite.
- Integration tests use temporary real SQLite and DuckDB/Parquet stores.
- Replay tests use frozen synthetic payloads and clocks.
- End-to-end tests cover data import, snapshot creation, research run, replay, and report inspection.
- External providers are never required for CI.
- Tests assert outcomes and persisted state rather than internal call ordering.

Quality thresholds:

- Core package statement coverage: at least 80%
- Evidence/time/decision modules branch coverage: at least 90%
- Fixed-payload replay success: at least 99%
- Severe look-ahead violations: zero
- Silent provider degradation: zero

## 10. Code Style

```python
from datetime import datetime

from openalpha_cn.domain.time import ensure_aware


def visible_at(*, available_time: datetime, as_of: datetime) -> bool:
    """Return whether evidence was available to the researcher at ``as_of``."""
    return ensure_aware(available_time) <= ensure_aware(as_of)
```

- Use explicit keyword arguments at domain boundaries.
- Use timezone-aware datetimes; naive datetimes are rejected.
- Keep domain logic pure and deterministic.
- Return structured errors at provider/API boundaries.
- Do not use bare `except` or silent fallback-to-empty behavior.
- IDs and hashes are stable and content-derived where appropriate.
- Public contracts include docstrings and versioning notes.

## 11. Boundaries

### Always

- Read repository instructions and current handoff first.
- Use CodeGraph before text search when `.codegraph/` exists.
- Write or update tests before implementing behavior.
- Preserve source, time, version, and license provenance.
- Keep incomplete features disabled.
- Run the narrow verification after every slice and the full gate before release.

### Requires explicit owner decision

- Destructive database migrations
- Live broker or order-routing integration
- Redistribution of third-party raw data
- A license change
- Force push or shared-history rewrite
- Publishing credentials or user data

### Never

- Commit secrets, runtime databases, or user research output.
- Claim that a stub, mock, UI button, or filename is a completed feature.
- Read future data in a historical run.
- Convert provider errors into apparently valid empty data.
- Present research output as guaranteed investment advice.
- Put the 138 MiB ChainLin installer in Git history.

## 12. Release Boundaries

- Open-source source tags: `vMAJOR.MINOR.PATCH`
- ChainLin desktop tags: `chainlin-desktop-vMAJOR.MINOR.PATCH`
- ChainLin 1.0.9 is a separate binary Release asset, not MIT source.
- Release notes disclose that the current 1.0.9 installer is not digitally signed.
- Every binary asset includes file size, SHA-256, system requirements, and verification instructions.
- The WeChat consultation image appears only in the README download section, deployment quickstart, desktop Release notes, and optional project site download section.

## 13. v1 Non-Goals

- Automated live order execution
- Custody of user funds or broker credentials
- A hosted public raw-data resale API
- High-frequency or tick-level trading infrastructure
- Multi-market parity outside mainland A shares
- Kubernetes or multi-region deployment
- Claims of strategy profitability

## 14. Implementation Checkpoints

1. Repository foundation and quality gates
2. Point-in-time domain contracts and storage
3. Provider framework and first legal-safe inputs
4. Evidence snapshot vertical slice
5. Agent, signal, risk, and decision vertical slice
6. Same-path replay, backtest, and attribution
7. REST API, SDK, CLI, and web workbench
8. Security, packaging, documentation, and GitHub publication

Each checkpoint must leave the repository installable, testable, and free of knowingly broken user-visible flows.
