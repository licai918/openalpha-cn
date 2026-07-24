# Changelog

All notable changes follow Keep a Changelog and Semantic Versioning.

## [Unreleased]

### Added

- Durable per-agent recovery with request-digest and graph-signature isolation.
- SQLite-backed decision memory exposed through the SDK and HTTP API.
- Deterministic A-share portfolio accounting with cash, T+1/FIFO lots, costs,
  realized PnL, and hard single-position/total-exposure limits.
- Secure OpenAI-compatible BYOK model provider with structured-output validation
  and custom-agent injection through the Python SDK.
- Fixed-SHA source audit against TradingAgents, AI Hedge Fund, and
  TradingAgents-CN.

### Changed

- The feature ledger now contains 75 terminally reviewed capabilities, with 70
  supported by local source and test evidence (`93.33%` true completion,
  `UNREVIEWED=0`, `UNKNOWN=0`).

## [1.0.0] - 2026-07-24

### Added

- Four-clock point-in-time evidence contracts and content-addressed snapshots.
- SQLite WAL ledgers plus Parquet/DuckDB evidence storage.
- File, BYOT Tushare, and optional allowlisted AKShare provider adapters.
- A-share market-event, theme, catalyst, disclosure, and capital normalizers.
- Deterministic multi-agent research, structured model output, bounded retry, router, risk gate, memory, and immutable manifests.
- Same-path live/replay/backtest engine, A-share execution constraints, 300-event frozen replay corpus, outcome validation, and reconciled attribution.
- REST API, Python SDK, CLI, responsive React workbench, and Playwright golden flow.
- Non-root read-only Docker Compose deployment with persistent-volume recovery verification.
- Windows/Linux CI, dependency audits, publication safety scan, and 100% feature-destination ledger.

### Security

- Restricted CORS origins, strict Pydantic boundary validation, request size limit, browser security headers, BYOT credentials, and public-release secret/artifact checks.

### Boundaries

- No live broker execution, short/cover execution, commercial data resale, or bundled provider credentials.
