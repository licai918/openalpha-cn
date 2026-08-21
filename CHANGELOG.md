# Changelog

All notable changes follow Keep a Changelog and Semantic Versioning.

## [Unreleased]

### Added

- **A model plane reachable from a command line, and a prediction store something can fill.**
  `openalpha model evaluate` fits one declaration once per walk-forward fold and reports the
  five statistics `V2-P4-014` measures; `openalpha model daily-run` fits on the outcomes that
  have already closed, scores one stored cross section and **registers the prediction before its
  outcome is known** (Story S32). Before them, eight issues of contracts -- the `AlphaModel`
  protocol, the versioned feature matrix, the walk-forward split with purge and embargo, both
  stdlib baselines, the content-addressed artifact and the prediction record -- had no caller
  outside `tests/`: the CLI had no `model` command, no route's path contained `model` or
  `prediction`, and `OpenAlphaSDK` had no method that fitted anything. `POST
  /api/v1/models/{evaluate,daily-run}`, `GET /api/v1/predictions[/{record_id}]` and
  `OpenAlphaSDK.evaluate_model()` / `.run_daily_model()` / `.held_prediction()` are the other two
  faces; all three resolve and run through one module, so they cannot fit three models from one
  declaration.
- **`FilePredictionStore` is wired into the composition root**, as the twelfth store, under
  `runtime_dir / "predictions"` and with `build_storage`'s own clock. `V2-P4-017` shipped it and
  left it out by name -- two `lint-imports` contracts stand between a `PredictionBatch` producer
  and `openalpha_cn.storage`, one per direction, so nothing could hand it a batch until a face
  above both planes existed.
- **`RunManifest.alpha_model_versions` is filled**, by `model daily-run` and only by it. The slot
  was declared at `V2-P4-010`, which named `V2-P4-016` for it; that issue measured that
  `run_cycle` has no `AlphaModel` on its path and passed it on, and `V2-P4-017` reached the same
  conclusion from the store side. A daily run files a `mode=daily` manifest naming the one
  artifact it consumed, under a `run_id` derived from the prediction's own content address, so
  re-running an identical day reports `unchanged` on both stores rather than a duplicate on one.
  `model evaluate` writes no manifest and registers no prediction, and both absences are stated:
  it fits one artifact per fold and acts on none of them, and every record it could register
  would stand `unwitnessed`, because a simulated prediction is dated at the instant it simulates.
- **`feature_matrix.require_declared_features` has its first caller.** `--feature-version`
  omitted resolves from the columns the request declares (`--code-commit`'s arrangement, because
  nobody can type a `feat_` digest by hand) and supplied is checked, with a mismatch refused by
  name on all three faces. The answer records which of the two happened, because a resolved
  recipe proves only that the artifact records what it was fitted on. `V2-P4-014` had been named
  as this function's first caller and structurally could not be:
  `backtest-no-numeric-stack-or-panel-plane` forbids `openalpha_cn.feature_matrix` to the whole
  `backtest` package.
- **`--min-scored-ratio` on both model faces, with no default, and a refusal that is not an empty
  answer.** Above the declared floor: exit `0` / `200` with `admitted` carrying what the run
  stands behind. Below it: exit `1` / `409` with `"admitted": null` and both sides of the bar
  under `blocks`, while the `measurement` body stays byte-identical across the pair. It exists
  because `FoldEvaluation.scored_ratio` does -- abstaining on the hard names is otherwise a free
  way to win -- and it is a coverage verdict and never a quality one. A refused `daily-run` still
  registered its prediction, and the `record_id` is on the `409` body.
- **Every rendered prediction says what its `standing` does *not* prove.** `V2-P4-017` states
  plainly that `predicted_at` is unverifiable and that nothing defends against whoever owns the
  disk; a face printing `"standing": "forward"` and stopping would turn a single-user
  bookkeeping fact into what reads like an attestation. Both sentences travel in the body and in
  the terminal rendering.
- **`feature_matrix.stored_cross_section_instants`**, so a face can take a **range** of
  prediction days rather than one flag per instant: the builds every declared column shares,
  visible at the reading `as_of`. The intersection and not the union, which is
  `_resolve_instant`'s existing rule read forward.
- **`openalpha model evaluate` and `daily-run` need `adj_factor` and `shortlist run` does not**,
  while `shortlist run` needs `namechange` and these do not. A label is a return *between two
  sessions*, so the labeller requires an adjustment series; nothing on the model faces builds a
  `MarketBar`, so no name history is read. A panel built for one face is short for the other in
  both directions, and each refusal names the `panel build` line that repairs it (`V2-P4-078`'s
  bar).
- **`runs.mode` is a queryable, indexed column, and the payload is still its only copy.**
  Listing every `paper` run used to mean a full table scan plus one JSON parse per stored run,
  because `mode` existed only inside the opaque `runs.payload`. It is now a `GENERATED ALWAYS
  AS (json_extract(payload, '$.mode')) VIRTUAL` column with an index on `(mode, run_id)`, and
  `SQLiteRunRepository.list_runs(mode=...)` is the query that uses it. A generated column
  rather than a written one so there is no second copy to drift: SQLite derives it, refuses
  every attempt to write it, and recomputes it whenever the payload changes. Measured through
  `list_runs` on 100,000 stored runs: 461 ms → 106 ms for a one-in-five spread, 450 ms →
  5.3 ms at one-in-a-hundred, 437 ms → 0.8 ms at one-in-a-thousand — the saving is rows *not*
  parsed, so it scales with how rare the mode is rather than with the table. Most of the first
  win is the column rather than the index, which is why both were measured separately.
  Migration 6, `add_runs_mode_projection`, makes the same change a recorded, backed-up event on
  an existing database and re-derives the projection in Python before committing, so a
  generating expression that silently produced `NULL` for every row rolls the migration back
  instead of turning every mode-filtered listing into a confident empty answer.
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

- **Three breaking contract versions cut at once, with the identity rewrite they require.**
  `RunManifest.mode` gains `paper` and `daily` (`run-manifest/v2`), `AttributionTerm.category`
  gains `model` and `ValidationResult` gains an explicit `unexplained_return`
  (`validation-result/v2`), and `DecisionLedger` carries the run declaration's content address
  (`decision-ledger/v2`). Two of those move a **stored key**, so reading an un-migrated row of
  either raises `IdentityRewriteRequiredError` rather than upcasting it and stranding every
  reference; `openalpha migrate run` applies `rewrite_contract_identities`, which recomputes
  each identity and re-points `validation_results`, `research_memory`, `research_reports` and
  `batch_tasks` in one transaction and refuses to commit an incomplete rewrite. Checked-in
  schema documents are now named after the version they hold
  (`docs/api/schemas/decision-ledger-v2.json` and two siblings).
- **`SignalFrame.horizon` is a countable, comparable span.** It narrows from four units to
  trading days -- the only unit with a session count, so any two horizons a signal carries can
  be ordered and every one of them sizes the return window that scores it. A narrowing changes
  no serialized value, so no `signal_id` moved and `signal-frame` stays at v1. A stored signal
  carrying a calendar horizon is refused by name during migration rather than converted with a
  constant this repository has never measured.
- **The research-cycle modes are declared once.** `domain/run_mode.py` replaces the three
  independent copies in the manifest contract, the request contract and the CLI, so
  `--mode paper` and the two contracts could not disagree.
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
