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
- **A stale model abstains out loud, and the abstention is not free** (Story S35).
  `--shelf-life-days` on both model faces, `shelf_life_days` on both routes and both SDK methods,
  declares how far past its training cutoff a fit may still be asked. Beyond it every security in
  the batch carries `ABSTAIN_STALE_MODEL` instead of a score -- not a raise, which would delete
  the answer, and not a `0.0`, which is a number a reader cannot tell from an opinion. The check
  lives in `domain/alpha_model.py::prediction_batch_for`, the one chokepoint every implementation
  goes through including a third party's, and it is `require_features`' own argument for being
  there. The span is a property of the *ask* and reaches no artifact field: putting it on the
  declaration would give one fitted model as many addresses as there are opinions about how
  strictly to read it.

  What stops such a model looking skilful is machinery that already existed. An expired fold
  scores nothing, so no test day is `measured`, `FoldEvaluation` refuses to carry a `mean_rank_ic`
  beside a coverage that is not, and `scored_ratio` reads `0.0` -- which `--min-scored-ratio`
  refuses. The interesting case is a fold that expires *partway*: its headline is taken over the
  fresh days alone and is not a worse-looking number, which is exactly why `V2-P4-014` made
  `scored_ratio` the one statistic that is never `null`.
- **The abstention vocabulary is coded and closed over what this repository produces.** Three
  codes for three conditions -- `incomplete_features`, `unrankable_cross_section`, `stale_model` --
  in `ABSTENTION_VOCABULARY`, with `abstention_code` reading one back. `V2-P4-014`'s two sentences
  moved from `backtest/alpha_baseline.py` to `domain/alpha_model.py` and are re-exported unchanged;
  `Prediction.abstention` stays free text, so a third-party model's own reason answers `None`
  rather than raising.
- **A synthetic corpus with a known signal-to-noise ratio and a known-null control**
  (`tests/known_signal_corpus.py`). Sixty securities over thirty prediction days, two columns of
  which one carries the plant, and a realized return of `beta * signal + noise` whose population
  rank IC is closed-form -- `0.317` for the alpha arm and exactly zero for the null, which is the
  same draw with the coefficient set to zero and nothing else changed. Measured: the alpha arm's
  folds read `0.286`/`0.294`/`0.372`; the null arm's read `-0.009`/`-0.008`/`-0.033`. It separates
  a fitted model from an unfitted one three ways, which a one-column corpus provably cannot -- with
  a single feature every rank statistic is invariant to the coefficient, and the two readings come
  out bit-identical. What it cannot do is certify a *realistic* IC: the null arm's own folds wander
  as far as `0.113` from zero, so a plant of `0.03` would be inside this corpus's noise, and that
  is stated where a reader meets it rather than hidden behind a plausible-looking number.
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
- **A walk-forward panel now carries its own invariants, and three claims that were
  broader than what held are narrowed to it.** `V2-P4-013` said an unordered split is
  *unrepresentable*; the acceptance measured that the ordering behind that derivation lived
  only in the `labelled_panel` factory, while `LabelledPanel` and `PanelSection` were exported
  frozen dataclasses with no `__post_init__` — so one `dataclasses.replace` moving an early day
  to the end of the tuple produced a fold the shipped `walk_forward_folds` accepted and whose
  own `leaked_sessions` reported six shared sessions. A second bypass found while closing it
  purged 0 of 48 candidates by moving a section's instant without its prediction day. Both are
  refusals on the types now; the factory keeps only what a panel cannot see. The registry code
  moved to `train_membership_is_unrepresentable_and_the_order_behind_it_is_only_refused`,
  because membership really is unrepresentable and the order really is only refused.
- **Two `KNOWN_BASELINE_LIMITATIONS` entries contradicted each other and the false one is
  rewritten.** A mean rank IC does not separate a leaked fold from a purged one: measured
  `-1.0` in all four configurations of both corpora, with the leak visible only in the
  coefficient. The `1.0` and `0.0` that do separate them are `V2-P4-013`'s concordance numbers.
  The audit that binds every registry `code` to executable test code cannot see a false
  `detail`, and the cheapest structural candidate for closing that was measured and declined —
  it would have been satisfied by this very sentence while raising 38 false alarms — so the
  boundary is written where a reader meets it instead.
- **`Prediction.score` normalises the sign of a zero**, the last of the three addressed floats
  to get `V2-P4-016`'s `_unsign_zero`; before it, two batches that compared equal were filed
  under two `record_id`s. Filed as latent and measured otherwise: the reference model's
  `sign * (value - centre)` hands `-0.0` to any security sitting exactly on the learned centre
  under a negative sign, which is the shipped `predict` and not a hand-built payload. `FilePredictionStore.put`'s `supersedes` referent check is recorded
  as contract-only — no face can supply a lineage edge — with an AST assertion that goes red
  the day one is wired. `feature_matrix._PANEL_FAULTS` speaks for five loaders, not six, and
  the count is now executable. And `lint-imports` alone does not stop a new `backtest/*.py`:
  a probe importing `numpy` and a store reads `8 kept, 0 broken`, so the sentences that said
  otherwise now point at the pytest assertion that does.

### Fixed

- **A stored prediction that cannot be parsed is a named refusal, not "a defect in the command"**
  (`V2-P4-096`). A write a power cut stopped half way reached the command line as `exit 5` with
  the message withheld, HTTP as a bare `500 text/plain`, and the SDK as an unenveloped
  `JSONDecodeError` — while a document with one number *edited* was already refused perfectly,
  because the store re-derived the address and never checked the parse. Measuring the class first
  is what changed the fix: `read_versioned` is the single entry point every deserializing store
  in this package reads through, and **four damaged documents reach three different exception
  types** — a truncation raises `JSONDecodeError`, a newer build's `schema_version` and a payload
  that is an array rather than an object raise `UnknownSchemaVersionError`, and one retyped field
  raises pydantic's `ValidationError`. So the faults are named once as
  `domain.versioning.STORED_DOCUMENT_FAULTS` beside the function that raises them, rather than as
  a fourth `except json.JSONDecodeError` at a fourth call site, and `FilePredictionStore.get`
  converts them where it already re-derives the address. One `except` covers both readers: `put`
  reads through `get`, so re-running the daily run that would register the same prediction is
  refused by name too — and refused rather than repaired, because "never write where something is
  already held" is this store's one guarantee. The message names the record, the underlying fault
  and the document to remove. `openalpha model predictions` still lists the address, and that is
  now deliberate: verifying every name means parsing every body, measured at 3.6 ms per document
  at market width — 22 s for five models over five years — and it would *hide* the damage from
  the one person who needs to see it.
- **A same-day `daily-run` may set `--end` to the last session it built** (`V2-P4-095`). A
  training range reaching within `--horizon` sessions of the panel's newest session died reading
  price bars for a session that had not published yet, on all three faces, so a caller had to pull
  `--end` back `horizon + 1` sessions and nothing — no message, flag or limitations entry — said
  so. It contradicted the command's own contract: the training set is every example whose outcome
  window had closed at `--predict-at`, and those cross sections were always going to be purged.
  The labelling read simply ran first. `run_daily` now drops them **before** labelling, through
  `_outcome_had_closed` — the one inequality `trainable_at` already applied, extracted so the two
  cannot drift — so nothing asks the panel for prices it does not hold. Measured on the
  ten-session corpus: `--end 2026-01-15` refused before and now answers with the same
  `artifact_id` and the same `record_id` as `--end 2026-01-14`. A window the *calendar* cannot
  place at all is untouched and stays `V2-P4-088`'s named refusal: an outcome dated after the
  deadline and an outcome that cannot be dated are two different facts.
- **Both `openalpha model --help` examples run, and a test executes the ones that are printed**
  (`V2-P4-094`). Neither did. Three faults, only the first of which was reported: `--as-of` is a
  **partition**-level clock, so the printed `2026-01-20T04:00:00+00:00` refuses any 2026 panel
  holding a row published after it; the bound runs the other way as well, because the calendar
  requires every session up to `--as-of` to be present, so a later instant is a `date_gap` and the
  wall-clock default lands outside the interval on every panel not built up to today; and
  `model evaluate`'s example could not run on *any* panel, since `--horizon 5d` over the seven
  prediction days it names purges the first fold to nothing and `walk_forward_folds` refuses the
  schedule — a reason this repository's own test corpus had already recorded. The examples now
  read a whole year from after it, both spell `--as-of` out, and the help states the rule in both
  directions. The `not_yet_knowable` refusal stops describing a **maximum** as when the dataset
  "first became available", says that the judgement is per partition rather than per row, and
  names the earliest `as_of` that would read it — the number a caller needs was always in the
  message, framed as a fault rather than as a bound, which is why the acceptance found the
  reachable set by bisection. **What is not fixed is the partition-level gate itself**, and the
  reason is measured rather than deferred: see `panel_ingest.load_adjustment_histories`.
- **A daily run on the last trading day of the year is a named refusal on all three faces,
  not a bare `500`** (`V2-P4-088`). The prediction store seals a batch against the calendar's
  answer to when its outcome becomes knowable, and derives that answer through the same
  `build_label_window` the training side goes through — but `run_daily` handed the batch over
  *after* its only `try` block had closed, so `CalendarHorizonError` reached the REST route as
  `500 text/plain` and `OpenAlphaSDK` as an unenveloped `ValueError` subclass. It is not an
  exotic input: `daily_request` requires `predict_at`'s date to be strictly after `end`, so the
  prediction day is always later than every training day, and any prediction day in the last
  `horizon.sessions + 1` sessions of a year-keyed calendar has an outcome window the exchange
  has not published. Both places that build such a window now share one fault tuple and one
  sentence, and the remedy names the command: `openalpha panel build --dataset trade_cal --year
  <next>`, then declare that year with `--year`.
- **`panel_fixtures.generate_panel` can price a window anywhere in its calendar year**, which is
  what made the above reachable from a test at all. The generated calendar has always covered
  the whole partition year while the priced window was ten sessions in January, so no generated
  panel could put a prediction day near the calendar's last session — the third time a fixture
  has been found hiding a wall by never walking up to it (`V2-P4-080`, `V2-P4-085`). Each batch's
  fetch instant and the panel's `as_of` now follow the last session priced, which is
  `_index_weight_batch`'s existing rule extended to the five builders that lacked it; the default
  window is unchanged instant for instant.
- **One test file no longer disables logging for the whole process** (`V2-P4-089`).
  `tests/unit/test_model_view.py` imported the raw `importlinter.cli.lint_imports` under the
  alias `_lint_imports` — the name of the containment wrapper one directory over — so it read as
  contained while `dictConfig(disable_existing_loggers=True)` silently disabled every logger
  already in the process, and six `caplog` acceptances under `tests/integration` failed whenever
  that file was collected first. The convention that was supposed to prevent this had failed
  twice (`V2-P4-068`, `V2-P4-012`) because it was a per-file regex over a *call spelling*: an
  alias dodges it and another file is out of its scope. `tests/import_linter_containment.py` is
  now the only import of `importlinter.cli` in the tree, both of its exports restore the whole
  logger snapshot, and one AST sweep over every file under `tests/` replaces the three private
  regex guards. That sweep found four files reaching the raw CLI rather than two, and then the
  same defect inside the guard itself: the test that proves the pollution is real restored only
  the logger it named, taking four `test_batch_research.py` acceptances with it whenever the unit
  tests ran first — so four of the six failures had two causes, and routing the reported call
  sites through the existing wrapper would have left `pytest tests/unit tests/integration
  tests/contract` red.
- **One name declared twice with values of two types is one verdict on every face**
  (`V2-P4-091`). `ModelRunApiRequest.declared_hyperparameters` sorted whole `(name, value)`
  pairs while `cli._model_hyperparameters` sorted by name, so two hyperparameters sharing a name
  made the HTTP sort compare `1 < "a"` and answer `500 text/plain` where the command line and the
  SDK answered `bad_request`. A caller error reported as a service fault pages an operator and
  trips retries. The ordering rule now lives once, in `model_view.declared_hyperparameters`, and
  both faces call it — the HTTP copy had carried a comment claiming it was "`cli
  ._model_hyperparameters`' rule", which is what kept the disagreement invisible.

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
