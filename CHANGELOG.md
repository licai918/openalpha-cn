# Changelog

All notable changes follow Keep a Changelog and Semantic Versioning.

## [Unreleased]

### Added

- **A factor plane reachable from a command line.** `openalpha factor build` computes and
  stores the raw, processed and neutralised tiers through the real `compute_factor`,
  `apply_factor_transform` and `apply_factor_neutralization` and the three write-time
  guarded writers. Before it, a store built by `openalpha panel build` held no factor
  partition, `openalpha factor run` against it was refused by name, and
  `openalpha panel build --dataset factor_obs_...` answered that the dataset is not one of
  its thirteen build targets — the three engine functions had no operator-reachable caller
  anywhere in the repository and no usage example outside one integration test. On the
  command line and in the SDK only, matching `panel build`: it writes panel partitions and
  the service ships with no authentication of its own.
- **`openalpha factor list` / `openalpha factor describe`, `GET /api/v1/factors`,
  `OpenAlphaSDK.factor_catalog()` / `.describe_factor()`.** The legal values of
  `--factor`, `--transform` and `--neutralization` were listed by no face, route or
  document; the only discovery channel was a typo, which answered with nineteen opaque
  content addresses. The catalog serves every declaration with its **whole** prose note
  (705 to 4,830 characters each), the tier order, one sentence per attribution verdict, and
  a flag on the grid cell the acceptance criterion is decided on.
- The nineteen `note_for` disclosures now reach an operator. Several state in full what a
  factor deliberately does *not* measure — `return_vol_60/v1` records that it occupies
  `V2-P3-013`'s residual-volatility slot, is deliberately not named for a residual, and
  that neither residual is computable in this build.
- Durable bounded-concurrency batch research with progress events, cooperative
  cancellation, item retry, and restart recovery.
- Model capability registry, classified transient retry, and SQLite token/cost
  usage accounting.
- Immutable portfolio transition ledger and multi-day return, benchmark,
  turnover, capacity, and exposure reports.
- Contract-first ChainLin BYOK data Provider with PIT/revision clocks, Bearer
  authentication, client rate limiting, and explicit failure categories.
- Ablatable bull/bear and three-perspective risk committee.
- Event-study CAR, t-statistic, and deterministic Bootstrap confidence interval.
- Structured screening, durable watchlists, and immutable report center through
  REST and Python SDK interfaces.
- Durable per-agent recovery with request-digest and graph-signature isolation.
- SQLite-backed decision memory exposed through the SDK and HTTP API.
- Deterministic A-share portfolio accounting with cash, T+1/FIFO lots, costs,
  realized PnL, and hard single-position/total-exposure limits.
- Secure OpenAI-compatible BYOK model provider with structured-output validation
  and custom-agent injection through the Python SDK.
- Fixed-SHA source audit against TradingAgents, AI Hedge Fund, and
  TradingAgents-CN.

### Changed

- **`openalpha factor run` says which grid row is the answer, and warns when the grid
  measured nothing.** The `processed->neutralized` rows are marked inline, and an
  experiment whose six cells are all `not_measured` prints a named warning on stderr in
  both `--json` and plain modes. Exit `0` and `200` still cover it — it did assemble, and
  each tier carries its own coverage codes — but "no `removed` cell" had been readable as
  "the factor survived neutralisation" about two tiers that never computed a number.
  `docs/api/http.md` documented the `removed` case and not this one.
- **Every required option of `openalpha factor run` has help text.** Fourteen of the
  seventeen showed a bare `[required]`, on a command whose own docstring says the numbers
  move every verdict it prints.
- A mistyped `--factor` is answered with the declared **qualified keys** and a pointer to
  `openalpha factor list`, instead of nineteen `fct_` content addresses from a help text
  that had just said "the key is the form for a human".
- `KNOWN_FACTOR_RUN_LIMITATIONS` replaced
  `nothing_in_this_repository_builds_a_factor_panel_from_a_command_line` — which
  `openalpha factor build` makes false — with
  `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon`, the part of it
  that is still true and the residual `V2-P4-026` closes.
- `docs/HANDOFF_CURRENT.md` no longer says "v2 implementation has not started; the next
  step is `V2-P0A-001`". P0.A, P0.B, P1, P2 and P3 are merged; a reader following the
  repository's own pointer would have concluded the factor plane does not exist.
- The feature ledger now contains 160 terminally reviewed capabilities, with 155
  supported by local source and test evidence (`96.88%` true completion,
  `UNREVIEWED=0`, `UNKNOWN=0`).
- The repository work pointer now targets v2. `AGENTS.md`, `CONTRIBUTING.md` and
  `docs/HANDOFF_CURRENT.md` reference the `docs/specs/v2/` workspace, which holds
  the re-scoped PRD, a seven-phase roadmap sliced to issue level, and a four-seam
  code audit whose findings each map to a closing issue. The v1 spec remains
  the contract baseline.

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
