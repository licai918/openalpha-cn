# Changelog

All notable changes follow Keep a Changelog and Semantic Versioning.

## [Unreleased]

### Added

- **`openalpha portfolio construct` and `OpenAlphaSDK.construct_portfolio`: heuristic target
  weights over one admitted shortlist** (`V2-P5-001`, the first module of P5). A twelfth
  pure-stdlib `backtest/` leaf, `backtest/portfolio_policy.py`, turns one `as_of`'s ranked list
  into weights by three declared arithmetic steps -- a contiguous cut on rank into tiers that each
  split their share **equally**, a bounded clamp/redistribute/clamp pass against the caps, and a
  proportional move toward the target bounded by a turnover budget -- and labels the answer
  `heuristic, not optimized` on `PortfolioConstruction.method` (a `Literal`, so a build that
  stopped saying it would not validate) and on every rendered body, terminal and `--json` alike.
  There is no optimiser and that is ADR-0003's decision rather than a shortfall: nine runtime
  dependencies, no numerical stack, so a covariance estimate and a solver are not shippable here.
  **Nothing is pushed onto a last name to make a column add up** -- weight the caps will not take
  becomes cash and is reported as `unallocated_weight`, which is the residual-absorption trick
  `V2-P5-005` exists to delete out of `backtest/validation.py`, not reintroduced one phase earlier.
  **A shortlist the gate refused cannot be turned into weights on either face**: `admitted` is
  `null` for a refusal and `[]` for an admitted empty list, two answers `V2-P4-032` separated on
  purpose, and building a portfolio out of the first would launder the refusal into a set of
  numbers. Driven end to end from a `CliRunner` and an `OpenAlphaSDK` over a real generated panel,
  a real `openalpha factor build` and a real `openalpha shortlist run`, because a policy nobody can
  invoke is not delivered.
- **`PortfolioOrder.target_weight`; `PortfolioLimits` from two fields to five** (`V2-P5-002`).
  The order carries the share of equity it was *meant* to reach, so a stored transition says which
  plan produced it; `PortfolioSimulator` refuses a buy whose **declared** target already exceeds
  `max_position_weight` and still checks the **realised** weight after the fill, which is a
  different fact and the one that differs on a drifted book. `PortfolioLimits` gains
  `max_industry_weight`, `turnover_budget` and `min_cash_weight`, and **which consumer reads which
  field is written down rather than discovered**: `LIMITS_ENFORCED_BY_THE_SIMULATOR` and
  `LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY` are held *covering* against
  `PortfolioLimits.model_fields`, so a limit the contract declares and nobody enforces is red --
  the fail-open shape `V2-P4-030` found four instances of in the risk gate. The two the simulator
  omits are omitted structurally: `MarketBar` carries no industry, and one order carries no book
  history.

### Measured, and it falsifies two premises this work started from

- **The roadmap's "现金下限" is not a third limit.** Under long-only accounting
  `equity == cash + market_value`, so `cash / equity >= f` and `market_value / equity <= 1 - f`
  are one inequality: a 30% cash floor and a 70% exposure ceiling fund exactly the same book, and
  `test_the_cash_floor_and_the_exposure_ceiling_are_one_inequality_and_the_tighter_one_binds`
  drives both through the policy and compares the weights. The field ships because the row asks
  for it and because stating intent as a floor is legible; what the code does not do is pretend
  the two compose.
- **An industry cap has no input on any shipped face, so it is refused rather than satisfied.**
  `shortlist_view` builds its ranking with `exposures=None` and the stored answer renders no
  industry for any name, so `RankedCandidate.exposure` is `None` everywhere a caller can reach.
  A cap that cannot see an industry is satisfied by every book, so a declared
  `max_industry_weight` over candidates carrying no `industry_code` is a **named refusal** on the
  CLI and in the SDK. `OpenAlphaSDK.construct_portfolio_from_ranking` is where it starts working
  the day exposures are loaded (`V2-P5-015`), and the cap's arithmetic is unit-tested there today.
- **`V2-P5-002` is not a breaking change to a stored row, and that was measured rather than
  assumed.** `PortfolioTransition` embeds `PortfolioOrder` and *is* persisted, under
  `single_version()`, so AGENTS.md rule 3 applies and `V2-P4-001`'s window is closed. A payload
  written before the field reads back unchanged through the same `read_versioned` the ledger uses,
  because the default supplies the missing key. What *does* move is the bytes: the payload
  `SQLitePortfolioLedger.append` compares by equality now carries `"target_weight":null`, so
  **re-appending a transition an older build stored raises the conflict guard**. That is the
  migration cost -- a ledger rewrite, not a contract version bump, since there is no second
  version of this model and no portfolio contract is among the five checked-in schemas.

- **The placeholder attribution is deleted; what a run cannot measure is now a named residual**
  (`V2-P5-005`, `V2-P5-006`). `OutcomeValidator._attribute` claimed the entire net active return
  in fixed proportions nothing had measured -- 20% to a `rule` called `decision-policy`, 30% to a
  `factor` called `benchmark-and-cost` (two quantities `net_active_return` has *already*
  subtracted), and the remaining 50% split across the agents by `abs(signal.strength)` with the
  **last agent absorbing whatever was left over**. That last step is why
  `ValidationResult.validate_window_and_attribution` had never once failed on a computed result:
  a reconciliation with a free variable in it cannot fail, and so had never measured anything.
  Two terms survive, both exact: `transaction-cost` (`-transaction_cost`, emitted even at zero so
  "cost was nil" stays distinguishable from "cost is not modelled") and, for a decision that took
  **no** position, `no-position-versus-benchmark` -- worth `realized_return - benchmark_return`,
  which is `-benchmark_return` exactly, with one claimant and nothing left over. A decision that
  *held* a position books its whole selection return to `unexplained_return` instead: a finished
  `ResearchRunResult` carries a conviction, a confidence and some version strings, and none of
  those is a return, so no rule/factor/agent/model share can be shown. `KNOWN_ATTRIBUTION_LIMITATIONS`
  (the thirty-fifth registry, four entries) states the four things now never claimed.
  The control is closed-form and has **two arms**, because one arm separates nothing: every figure
  is a dyadic rational, so both arms are asserted with `==` rather than `approx` -- held reads
  `net 0.1796875 / residual 0.1875 / one term −0.0078125`, flat reads `net −0.0703125 /
  residual 0.0 / two terms`. An implementation that routed everything to the residual passes the
  held arm and fails the flat one; one that keeps any invented split fails the held arm.
  Driven through **both** product faces (`OpenAlphaSDK.validate_outcome` and
  `POST /api/v1/backtests/validate`, byte-identical, and queryable back out), and the web
  attribution panel now prints 未归因残差 beside the terms -- a residual computed and then dropped
  on the way to a reader is the same defect as one never computed.
  **Mutation sweep** (baseline proven at `2970 passed, 1 skipped`): **24 mutants, 24 killed**.
  The one survivor was not equivalent and was **measured rather than labelled** -- spelling the
  flat term `-benchmark_return` instead of `realized_return - benchmark_return` agrees on every
  reachable value except `benchmark_return == 0.0`, where it yields `-0.0` against `+0.0`;
  canonical JSON writes the sign, so the same result took two addresses
  (`val_dba127649bf529e77e53d6aa` vs `val_470895b1ba7335601a265760`). A test now drives that.

- **Two guards for quantifiers and gates that prose asserted and nothing measured** (`V2-P4-112`,
  `V2-P4-115`). `AgentRouter` satisfies an evidence family when **any** declared family is present
  and a feature dependency only when **every** declared column is, and two docstrings cite
  `ThemeAgent`'s `{theme, catalyst, disclosure}` as the reason for the asymmetry. The feature half
  had `test_every_declared_column_must_be_on_the_plane_and_not_merely_one_of_them`; the family half
  had nothing, because every `evidence_families=` in that file declared exactly one family, and on
  a single-family declaration `&` and `<=` agree for every run. Mutating `&` to `<=` left the
  router's own unit file green. Two tests now close it -- one symmetric to the feature half, one
  routing the real `ThemeAgent` so the citation is executable. (The mutant was already killed
  incidentally by `test_research_cycle.py`, whose fixture carries exactly one of the three
  families; what was missing was a *named* guard in the file that owns the rule.)
  Separately, two mutation survivors from `V2-P4-007/008/009` classified "provably equivalent" were
  remeasured and are not: a `Literal` member inside a local-variable annotation survives pytest but
  **`mypy` reports 2 errors**, so it is a sweep-tooling survivor rather than an equivalent mutant --
  a sweep whose oracle is pytest alone under-reports whenever a second gate ships with the build --
  and `ensure_ascii=False` on `shortlist compare --json` was equivalent **on the fixture only**,
  now killed by a test that renders a non-ASCII exchange name. `@dataclass(slots=True)` is the one
  of the three that really is equivalent.

- **`openalpha panel doctor --no-limitation-detail` and `GET /api/v1/panel/health?limitation_detail=false`**
  (`V2-P4-110`). Measured on a generated panel asked about `index_daily`, the `--json` answer was
  16,936 bytes of which **14,359 (84.8%) were the limitation paragraphs** and 1,340 were the
  findings — prose that is byte-identical on a healthy panel and a broken one, on the first run
  and the thousandth. The text face has rendered them as a count since it was written, for the
  reason in `_echo_report`; a machine reader had no such choice. Declining keeps each entry's
  `code`, `datasets` and `dates` and drops only the paragraph, and the default is unchanged —
  a registry served only on request is a registry that stops being read. **The report was filed
  as "the whole ledger, unrelated to the dataset asked about" and that half does not survive
  measurement**: `known_limitations` already selects on `wanted & set(item.datasets)`, so four of
  the ten are `index_daily`'s own and six are the storage plane's, which name no dataset because
  they hold for every dataset alike.
- **`openalpha migrate prune-backups`** (`V2-P4-111`), the documented cleanup path for
  `runtime/backups/`. `--keep N` (default 10), `--dry-run` to list first, `.bak` files only, and
  exit `0` whether or not anything was removed.
- **A durable scheduling primitive, where audit `F98` measured there was none** (`V2-P5-010`).
  `openalpha_cn/job_contracts.py` (the durable shapes), `storage/jobs.py` (`SQLiteJobStore`) and
  `openalpha_cn/scheduler.py` (`TradingDayScheduler`) give the six things `F98` enumerates: a
  persistent job table with a next-fire-time, a lease, a per-trading-day idempotency key, a
  catch-up policy, a calendar dependency, and crash recovery. No new runtime dependency —
  ADR-0003's nine stand; SQLite through the existing `storage/` layer, and the lock is
  `BEGIN IMMEDIATE` rather than a broker.
  - **The idempotency key is the `PRIMARY KEY`**, not a check: `job_id@YYYY-MM-DD`, so a second
    run of the same trading session is an `IntegrityError` from SQLite rather than a race two
    processes can both win between a `SELECT` and an `INSERT`.
  - **Crash recovery is lease expiry**, not a sweeper — a sweeper would itself need scheduling.
    `claim()` takes an expired lease as readily as an absent one, so a process that died holding
    the job is recovered by the next process that asks for it.
  - **`due()` deliberately does not read `next_fire_time`.** A stored fire time is derived from a
    calendar that changes; asking `panel_ingest.newest_published_session` — the one function that
    owns the 16:30 `DAILY_AVAILABILITY_TIME` rule — and comparing against `last_fired_session` is
    the only formulation that survives a holiday being announced after the fire time was written.
    The stored column is kept as a poller's index and recomputed on every advance.
  - **`panel_ingest.session_publication_instant`** is the one new function on the panel plane: the
    inverse of `_sessions_published_through`, placed beside it and reading the same constant.
    `V2-P4-063` found that rule restated three times with two disagreeing and `V2-P4-114` found a
    fourth; a scheduler computing `time(16, 30)` for itself would have been the fifth. The two are
    pinned against each other by a round trip over a full year at half-hourly resolution (17,520
    instants), not against a literal.
  - **Measured while building this, and it decided the shape**: on a *fresh* `state.sqlite3`,
    `create_app()` reaches `schema_version: 2` and stops — migration 3
    (`demo_add_runs_archived_at`) raises `MigrationNotYetApplicable` because `runs` does not exist
    yet, and `run_migrations` breaks out of the loop on that, so **migrations 4 through 8 never
    run on a new database**. A ninth migration adding these tables would never have run either.
    `CREATE TABLE IF NOT EXISTS` in the owning store is the only construction that works on both a
    new database and an old one, which is what `_baseline_apply`'s docstring already says.
  - **Not yet done, and stated rather than implied**: these three modules have no CLI command, no
    REST route and no entry in `build_storage`. Nothing in the shipped product calls them yet, so
    no product-surface claim is made for them here; a later row has to give them a face.
- **The request-body ceiling is now metered on the way in, not read off a header** (`V2-P5-012`,
  audit `F100`). It read `Content-Length` and nothing else, so a chunked request bypassed it
  entirely. Measured on `c847295` against a deliberately tiny 1,024-byte ceiling: a chunked
  `POST /api/v1/research/batches` of **36,000,030 bytes** was answered `422 json_invalid` -- the
  JSON *parser's* verdict, reachable only after the whole body had been read -- with a
  `tracemalloc` peak of **108,346,472 bytes**, three times the body, because Starlette
  accumulates the chunks in a list and then joins them. Bodies with no declared length are now
  counted chunk by chunk and reading **stops** at the ceiling; measured through
  `httpx2.ASGITransport`, which pulls one chunk per `receive`, the fix reads **1 of 400 chunks**
  (100 KB instead of 40 MB) before answering `413`. A declared `Content-Length` above the ceiling
  is still refused before anything is read at all. The refusal carries the same `reason` and
  `limit_bytes` from either gate and adds `measured_bytes` beside `declared_bytes`, exactly one of
  which is ever non-null -- `measured_bytes` is a **floor** on the body, never its size, because
  the rest was never asked for. One case is deliberately still unmetered and is documented as
  such: a body sent to a route that never reads one.
- **The three browser hardening headers audit `F102` named** (`V2-P5-012`):
  `Strict-Transport-Security: max-age=31536000; includeSubDomains` (without `preload`, which is a
  commitment about a domain that a library must not make on an operator's behalf),
  `Cross-Origin-Embedder-Policy: require-corp` and `Cross-Origin-Resource-Policy: same-origin`.
  The same finding's second half is fixed with them: the headers were **appended** to whatever a
  response already carried, so a route setting `x-frame-options: SAMEORIGIN` produced two raw
  header lines and a browser read `SAMEORIGIN, DENY` (measured). They are replaced by name now.
- **`openalpha serve` no longer advertises `server: uvicorn`** (`V2-P5-012`, `F102`).
  `--no-server-header` was passed by the `Dockerfile` and not by the command a developer runs, so
  one deployment of the same application leaked its server software and the other did not.
- **CORS admits every method this service serves, plus the three v2 will add** (`V2-P5-011`,
  audit `F101`). The list was `["GET", "POST"]`, written by hand, and the roadmap row states the
  cost as a v2 risk -- a later `PUT`/`DELETE`/`PATCH` route refused at the browser. **Measured, it
  had already fallen behind the route table it guards**: a preflight naming `HEAD` answered
  `400 Disallowed CORS method` while the application declares four `HEAD` routes. The allowed
  origins (the two local Vite dev servers) and `allow_credentials=False` are unchanged, and both
  are now pinned by tests, because widening methods is only safe while those two do not move.
  The guard against a third divergence reads the methods off the running application rather than
  restating them. `docs/api/http.md` now states the method list and all nine response headers, and
  two tests read the document and the live response together so the table cannot drift — which is
  how a false claim written into that document during this change was caught: `Starlette` does
  **not** append `OPTIONS` to `Access-Control-Allow-Methods` (measured on 1.3.1, it carries
  exactly the list it is given), so `allow_methods=["*"]` and the explicit tuple are *not*
  observationally identical the way the first draft of this code's docstring asserted.
- **`CORSMiddleware` is now the outer of the two middlewares.** While `SecurityHeadersMiddleware`
  sat outside it, every refusal that middleware short-circuits -- the `413` `V2-P4-043` worded so
  carefully, naming the number exceeded and the variable that raises it -- skipped the layer that
  adds `Access-Control-Allow-Origin`, so a cross-origin browser caller saw an opaque network
  failure instead. The one thing given up is the hardening headers on a CORS *preflight*
  response, which renders nothing and carries no body.
- **`V2-P4-043` raised the request ceiling in `config.py` and nowhere it ships** — found while
  documenting that ceiling for `V2-P5-012`, and this one is not a stale sentence. `Dockerfile`
  carried `OPENALPHA_MAX_REQUEST_BYTES=8388608` and `deploy/compose.yml` carried
  `${OPENALPHA_MAX_REQUEST_BYTES:-8388608}`; both are **configuration that overrides the
  default**, so the shipped container ran at 8 MiB. Measured: with that environment,
  `load_config().max_request_bytes` is `8388608`, and `V2-P4-043`'s own measurement of a
  `MAX_BATCH_ITEMS` batch — **9,840,054 bytes** — is still `413`. The row exists to make that
  batch postable and it was postable nowhere the product is deployed. Both files, the deployment
  doc's table, and a second contradicting sentence in `docs/api/http.md` (which said 8 MiB two
  hundred lines after the same file said 33554432) are corrected, and
  `test_every_deployment_that_sets_the_ceiling_sets_the_one_this_service_declares` now reads
  every byte count beside `OPENALPHA_MAX_REQUEST_BYTES` in the four files that set it and
  requires each to equal `OpenAlphaConfig`'s **declared** default. That test also falsifies a
  claim its neighbour made: `test_the_request_body_ceiling_is_named_in_the_http_doc_with_its_variable`
  says in its docstring that "a deployment-doc number that fell behind `config.max_request_bytes`
  goes red here", and it never read the deployment doc at all.
- **The README's own API landscape diagram said `8 MiB 默认请求上限`**, and has since
  `V2-P4-043` raised the default to 32 MiB -- a fourth restatement of a number that lives in
  `config.py`, and the one that was wrong. `scripts/generate_api_relationship_diagrams.py` now
  reads the **declared** default off `OpenAlphaConfig.model_fields` (declared, not effective, so
  the generated asset never depends on the environment of whoever regenerates it), and
  `openalpha-api-01-landscape.svg` is regenerated: one line changed.
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

- **The neutralised tier builds inside the membership year, not after it (`V2-P4-028`).**
  `panel_neutralization.load_industry_market_cap_cross_section` reads `index_member_all` through
  `panel_ingest.load_industry_cross_section` — `V2-P4-027`'s day-scoped door — instead of
  `load_industry_histories`, which took `PanelStore.read_if_ready` and decided `not_yet_knowable`
  on a partition's **max** `available_time`. A membership year was therefore unreadable until its
  last adjustment took effect, which on the real corpus is the annual constituent review (613
  assignments start 2021-07-30, 255 on 2022-07-29), so a walk-forward that fetched today and
  replayed history was refused once a year. Measured on the generated fixture, the cross section
  assembled on **3 of the window's 10 sessions** before this change and on **10 of 10** after it,
  and `openalpha factor build --tier neutralized` at a mid-window prediction instant now stores
  all three tiers where it used to exit `blocked` — `factor run` answers over the same two days
  at the end of `test_the_dead_end_the_acceptance_review_found_is_closed_end_to_end`. The storage
  door itself shipped with `V2-P4-027` and was never on the product path.
- **A behaviour change inside that: `panel_neutralization._industry_answer` folds two absences
  where it folded three.** "This read cannot speak for that day" — a stored membership year at or
  before the day that `membership_years` did not name — used to be counted as `industry_missing`
  alongside "no assignment covers this day", which made a fail-closed refusal look like a
  property of the market. It is now a **named refusal** that says which year to add. A caller who
  narrows `membership_years` past the day being priced gets an error where it previously got a
  cross section short by exactly the securities it could not speak for.
- **Two `KNOWN_*` codes renamed because `V2-P4-028` made their sentences false**, which is the
  registry mechanism working rather than an edit around it.
  `KNOWN_FACTOR_RUN_LIMITATIONS.the_builder_cannot_produce_a_residual_before_its_years_stored_horizon`
  becomes `...for_a_session_that_has_not_closed` — the third tier is now bounded by one session
  (the prediction instant must be at or after its own day's close, on a day the exchange was
  open) rather than by any year's horizon. `KNOWN_NEUTRALIZATION_LIMITATIONS
  .the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused` becomes
  `a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it`, which is the
  narrowing cost that survives. Registry totals are unchanged at 32 / 301.
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
  that was still true and the residual `V2-P4-026` closes. `V2-P4-028` then made *that*
  sentence false in turn; see the entry at the top of this section.
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

- **The tradable tier named nothing -- not the rule, not the security** (`V2-P4-066`). A
  whole-market screen answered `5545 listed -> 5542 scored -> 5533 tradeable` and `measured
  tradable=0.9978`, and the words `halted`, `below_board_minimum` and `up_limit` appeared nowhere
  in the body: `funnel.excluded_by_coverage` explains stage **one** only, so the arrow
  `--min-tradable-ratio` actually gates was a subtraction with no explanation beside it. The
  census underneath was never the missing part -- `TradeabilityCensus` has carried
  `refused_by_verdict` and `rejection_reasons` since `V2-P4-005` and neither reached a shipped
  surface. It now carries `refused` as well, every non-admitted security **by name** with the rule
  that decided it and, for exactly the ones the execution policy refused, that policy's own
  sentence; `__post_init__` holds the names to the counts in four directions, so a census whose
  list disagreed with its own cells fails its own arithmetic rather than reporting a plausible
  total. All four cells are reported on every answer, occurred or not -- `ScoreCensus`' rule that
  "nobody was `below_board_minimum`" and "nothing looked" are different claims -- while the named
  list is bounded by `MAX_NAMED_UNTRADEABLE` with `untradeable_not_named` carrying the residual,
  so a body cannot scale with the market (`V2-P4-110`'s 13.18 MiB lesson). The
  `tradable_ratio_below_floor` refusal now names the rules, the first securities under each and
  the commonest policy sentence, and the terminal grew an `untradeable` line beside `unscored`.
  Driven through `CliRunner` and `TestClient` on a panel where one name is limit-up and one is
  over budget, because a test importing `shortlist_view` would have been green throughout.
- **`failed` and `interrupted` runs resolved evidence and cleared a `1.0` floor** (`V2-P4-075`).
  A `RunManifest(status="failed")` stored under an address, with evidence filed against it,
  answered `exit 0, researched_ratio=1.0, is_blocked=False` under `--min-researched-ratio 1.0` --
  while the refusal that floor raises described the ratio as "a fact about which runs finished".
  `stored_run_manifest_ids` was *literally* true throughout (the deployment did hold those runs),
  so what was corrected is the thing built on it: it now returns `held` and `finished` apart, the
  evidence join resolves against `FINISHED_RUN_STATUSES`, and the two ways an address fails to
  resolve are reported apart -- `evidence_without_a_stored_run` for a run nobody made and
  `evidence_from_an_unfinished_run` for one that broke, because the remedies differ. The
  quantifier is over the **address**, not over a row: `status` is in
  `RUN_MANIFEST_UNADDRESSED_FIELDS`, so an interrupted run and its successful re-run are one
  declaration at one address.
- **The adjustment corpus can now say how far a read looked** (`V2-P4-086`, first of its two
  edits). `build_adjustment_history` and `adjustment_histories_from_panel_rows` take
  `answerable_through`, `statement_histories_from_panel_rows`' shape one dataset over;
  `covered_through` returns it when given and `observed_through` is the newest observation, kept
  apart because on a step function "no row after D" means the factor did not move, never that the
  series stopped. **The second edit is now measured rather than argued, and one half of
  `V2-P4-079`'s reasoning did not survive the measurement.** Moving `load_adjustment_histories`
  onto the row-filtered door *does* work for the census -- `adj_factor` is
  `ClockStrategy.daily_close`, the visible slice and the per-date census agree with no subject
  axis anywhere, and `panel doctor` answered at an earlier instant instead of losing
  `unpriced_explained` and `return_paths` to `not_yet_knowable`. What binds is the per-security
  half, and it is now reproducible from a shipped report: with a read-level horizon in place the
  `adjustment.factor_series_stops_inside_the_window` shape went from provoking
  `return_path_disagreement` to provoking nothing, i.e. a series that genuinely ended started
  being answered by a factor carried across a window it never covered. The move was therefore
  reverted, a frontier rule was checked and rejected for failing the ordinary "quiet since the
  opening anchor" case, and the measurement is pinned by
  `test_a_horizon_the_read_declares_cannot_carry_the_per_security_half` rather than left as prose.

- **The web contract-drift guard stopped guarding the run manifest, and `pnpm test` has been red
  in CI since 2026-08-20** (`V2-P5-025`). `V2-P4-010` (`9f68d65`) renamed
  `docs/api/schemas/run-manifest-v2.json` to `run-manifest-v3.json` when it gave the manifest its
  three component planes, and touched no file under `web/`. The `ResearchResult.manifest` spec in
  `web/src/typesContractDrift.test.ts` still named the v2 file, so `readSchema` threw `ENOENT`
  before `findFieldDrift` ever ran (`1 failed | 51 passed (52)`) and the manifest mirror had *no*
  drift protection at all for five days. Attribution correction: `V2-P4-001`/`V2-P4-025`
  (`5b3383f`) did **not** cause this -- that commit updated `web/src/types.ts` and the drift test
  in step (v1 -> v2). The sync was a manual habit rather than a test, and it lapsed on the next
  re-version. The spec now names `run-manifest-v3.json`, against which the measured drift is
  **zero**: the mirror declares only `run_id` and `status`, both unchanged across v2 -> v3, and
  `findFieldDrift` is one-directional by design (`schemaDrift.ts:277`), so the sixteen properties
  v3 has that the mirror lacks are never inspected. The mirror was therefore not lying and was
  not expanded -- declaring a subset is what that module explicitly permits. What the `ENOENT`
  hid was the guard's own liveness, so a new test requires every `schemaFile` in `DRIFT_CHECKS`
  to exist **and** to still declare the version its filename claims; the second half catches what
  a rename cannot, where a stale-but-present document loads cleanly and drift-checks against the
  wrong contract silently. All five checked-in schemas were re-verified: every filename matches
  its own `schema_version` const, and no second stale reference remains in `DRIFT_CHECKS`. Also
  corrects `domain/run_mode.py`'s prose, which pointed `mode`'s full enum at the deleted
  `run-manifest-v2.json`.

- **The write-time session census stopped one session short of what every other layer required**
  (`V2-P4-114`). `panel_ingest._session_census` bounded a partition at `fetched_at - 1 day` and
  justified it with the 16:30 publication rule -- which is that rule only *below* 16:30, and is
  the sentence `V2-P4-063` deleted from `cli._build_sessions` and left standing here. Above 16:30
  the two came apart by exactly one session: the build loop fetched through `D`,
  `_price_requirement` and `panel doctor` required `D`, and the write-time refusal stopped at
  `D-1`, so a partition that had lost precisely the newest session was **accepted at write time**
  and refused by the reader afterwards. Measured on a weekday-open January 2026 calendar at
  `fetched_at=2026-01-20T17:00+08` holding every session but 2026-01-20:
  `_sessions_published_through` answered `2026-01-20` and the census answered
  `([], 2026-01-01, 2026-01-19)`. The bound is now that same function rather than arithmetic of
  its own, so the three layers are one set by construction. Of the eight panel/CLI files, two
  assertions moved and **both were rendered date literals rather than behaviour** (`2026-08-07`
  to `2026-08-08`); the missing session `2026-06-12` is found under either rule. `cli.panel_build`'s
  claim that the two layers apply "the same rule" is true again, and now carried by a shared name.
  A mutation sweep over the two functions' executable lines, with the baseline proven green
  first, ran **9 mutants, 8 killed**. One of the kills is new: `>=` to `>` on
  `DAILY_AVAILABILITY_TIME` had survived, and is invisible at every instant of the day except
  exactly 16:30 -- the side the constant's own wording decides -- so the test now drives that
  instant too. The single survivor, `closes_on < opens_on` to `<=`, is **measured unreachable**
  rather than relabelled: separating the two would need a year-2026 batch fetched
  2026-01-01T16:30+08 that is *missing* 2026-01-01, and `ColumnarPanelBatch` refuses any row
  whose availability is after the fetch, so such a batch can carry only the 2026-01-01 row it
  is supposed to be missing.

- **A registry read pinned at the last prediction instant is look-ahead, and nothing measured it**
  (`V2-P4-113`). `V2-P4-064` recorded that `factor build`'s per-instant registry read "fails in
  both directions". Remeasured on `037ffa8`, mutating only that read's `as_of`: pinning at
  `as_ofs[0]` is red but by *refusal* rather than by a count, and pinning at `as_ofs[-1]` is
  **green over the whole file** (`36 passed`) and answers `[8, 7]` -- the correct answer. The late
  read is the look-ahead direction and it was unguarded. `universe_counts` cannot see it on **any**
  fixture: `stock_basic` is `calendar_static`, so a lifecycle row is visible exactly when its date
  is at or before the reading day, which is exactly when it can change `listed_on` for that day.
  What a late read does move is `subjects`, taken from `universe.securities`, which is not
  date-filtered -- so a new test gives a registry-only security a listing and a termination that
  both fall *between* the two instants, and the late-pinned read hands the earlier cross section a
  name that had not listed yet (`not_in_universe` 2 instead of 1). The `V2-P4-064` roadmap row and
  the citing docstrings in `factor_view._computed` and `test_factor_build.py` are corrected.

- **`openalpha factor run` never named the command that builds a factor it could not read**
  (`V2-P4-067(b)`). The row was closed on a fix that landed in `shortlist_view.py`; its own
  reproduction command goes through `factor_view.py`, which was untouched, so all three tiers on
  the factor face refused with `the raw reversal_1d/v1 observations could not be read out of
  <path>: factor_obs_reversal_1d_v1 year=2026 cannot be read at …: ['partition_missing',
  'field_missing']` and no remedy anywhere in it. All three tiers on **both** faces now carry
  `openalpha factor build --factor <key> --tier <tier> --year <year>`, on the CLI, the REST body
  and the SDK, with the store path still absent from the disclosable half.
  **The raw-only boundary was retired rather than narrowed, because its stated reason was measured
  false.** It said `neutralized` has "two partition spellings depending on the declared
  neutralization (`factor_neut_*` and `factor_neutmn_*`)"; `neutralized_factor_dataset` takes the
  *definition* and no neutralisation at all, `factor_neutmn_*` is the manifest dataset — the
  structural twin of `factor_procmn_*`, which the same paragraph did not treat as making
  `processed` ambiguous — and `load_neutralized_factor_observations` says "the neutralisation is a
  filter here and the factor is the dataset". Residuals written under one neutralisation are read
  back by a request naming another, and no rebuild is suggested. Two further measurements: the
  `tier != "raw"` guard that carried the whole justification was **unreachable** (its one call
  site passed the literal `tier="raw"` from inside `if tier == "raw":`), and the test that pinned
  the boundary **could not separate the two answers** — driven without `--transform`,
  `shortlist run --tier processed` exits `3` at request time, so `assert "openalpha factor build"
  not in result.stderr` was asserted against a sentence that could never contain it.
  `KNOWN_SHORTLIST_VIEW_LIMITATIONS.only_the_raw_tiers_…` is removed (8 → 7) and
  `KNOWN_FACTOR_RUN_LIMITATIONS.the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered`
  records the boundary that survives (8 → 9): the remedy fires on "no year of this tier is
  registered" and on nothing else.
- **A `422` no longer answers with a copy of the request** (`V2-P4-043`'s own fix reproducing
  `V2-P4-040`'s shape). `max_length=MAX_BATCH_ITEMS` on `ScreeningApiRequest.research` made an
  over-count a `422` naming the number, and pydantic's `too_long` error carries `input` — the
  whole rejected collection. Measured: `POST /api/v1/screen` with 10,001 records (14,771,528
  bytes in) replied **13,821,594 bytes**, and `POST /api/v1/research/batches` with 10,001
  requests replied **9,261,138 bytes** — the same defect on a second route, which the row
  neither named nor measured. The sharpest case is not a ceiling at all: a misspelled top-level
  key is two errors, each echoing the body, so at *200* records the refusal was **1.87×** the
  request. A ceiling this service declares now answers with the `{"reason", "message", "field",
  "limit", "received"}` object `V2-P4-041` built for the same route in the same commit, and every
  other `422` keeps FastAPI's list with each `input` elided past `MAX_ECHOED_INPUT_BYTES` (512)
  and the list truncated past `MAX_VALIDATION_ERRORS` (20) with a final entry saying how many
  were dropped. `V2-P4-051`'s documented shape discriminator is unchanged — every entry still
  carries a `loc` — and `docs/api/http.md` gains the fourth row and both rules. A ceiling fault
  reported *alongside* per-item faults is derivative (a body whose items all fail leaves nothing
  and trips `min_length`), measured as `Counter({'missing': 25, 'too_short': 1})`, so the item
  faults answer in that case.
- **An agent written against an older `ResearchAgent` crashed instead of being refused**
  (`V2-P4-008`/`V2-P4-010`). Both rows added a required attribute to a Protocol whose own
  docstring calls it an extension contract, and neither installed a check.
  `runtime/router.py:222` read `agent.feature_dependencies` unguarded — one line above
  `UndeclaredAgentDependencyError`, the named refusal built for this class of failure — so a
  third-party agent got `AttributeError: 'LegacyAgent' object has no attribute
  'feature_dependencies'`. **`provenance` was in the same state and worse**: its docstring claims
  an agent omitting it "fails structurally at the point it is handed to the engine", and there
  was no such check — `ResearchEngine._pair` reads it inside a dict comprehension *after* every
  selected agent has run, measured as `AttributeError` at `engine.py:223` **with a recovery row
  already on disk**. `REQUIRED_AGENT_DECLARATIONS` and `MissingAgentDeclarationError` are the
  check the contract advertised, at the router, over the whole roster and before any agent runs,
  naming the agent, the missing attributes and what to declare instead. A `TypeError` and
  deliberately not a subclass of `UndeclaredAgentDependencyError`: "this object does not
  implement the current contract" and "this agent can never be satisfied by any run" have
  different remedies. Nothing is defaulted — the guess `feature_dependencies` would need is
  indistinguishable from the misdeclaration the sibling refusal exists to name.
- **`factor build`'s residual refusal named three remedies, two of which could not work**
  (`V2-P4-109`). A Saturday and a session hours before its own close both exit `1` and both got
  the same sentence — "move `--as-of` … or fetch the later sessions first" — of which nothing
  helps a day the exchange is never going to open. `RESIDUAL_REMEDIES` is keyed by
  `CalendarDayStatus`, which is three-valued for exactly this reason, and the calendar is already
  loaded before the read that raises. The **exit code is deliberately not split**, recorded as
  `KNOWN_FACTOR_RUN_LIMITATIONS.a_closed_day_and_an_unclosed_session_share_one_exit_code`
  (9 → 10): `bad_request` means "no amount of re-fetching fixes it", and a day the loaded
  calendar reports `closed` can also be a day whose `trade_cal` partition is merely short, for
  which re-fetching *is* the remedy.
- **`V2-P4-108`'s roadmap row recorded the wrong exit code.** It said the fix yields `3`; it
  yields `1` (`FACTOR_EXIT["blocked"]` is `PanelExit.unhealthy`, and `factor build --help` already
  said "1 when the panel could not answer"). The envelope name was right and the number beside it
  was not, which is what a reader would have written `if [ $? -eq 3 ]` against.
- **Importing the API module wrote to the user's runtime directory, and the backups never
  stopped** (`V2-P4-111`). `app = create_app()` at module scope meant a bare `import
  openalpha_cn.api.app` ran migrations and wrote a ~139 KB backup, in a function whose docstring
  makes a point of that line being filesystem-free with respect to `.env`. **The growth had a
  second cause, and it is the one that mattered**: `run_migrations` took the backup *before* the
  loop, so a migration that raises `MigrationNotYetApplicable` cost a full copy and applied
  nothing — on every process start, with no terminating condition. Measured against a copy of a
  real `state.sqlite3` stuck at `user_version=4` (its history predates `create_validation_results`,
  so `_rewrite_contract_identities` has no `validation_results` table to alter): three runs,
  three backups, nothing applied. That is 125 of the 128 files in that repository's
  `runtime/backups/`. `app` is now built on first attribute access (PEP 562), so
  `uvicorn openalpha_cn.api.app:app` and `from … import app` are unchanged while a bare import
  touches nothing; a run that applies nothing removes the backup it took, which is provably its
  own (`O_CREAT | O_EXCL`) and provably redundant; and a failed migration still keeps the copy
  `MigrationFailedError` points at. **No existing backup was deleted** — `openalpha migrate
  prune-backups --keep N [--dry-run]` is the cleanup path, chosen over an automatic retention cap
  precisely because a cap would have removed a user's existing data on their next command.
- **`--config-digest` is refused when the request is read, not after the store is** (`V2-P4-065`).
  `shortlist_request` had checked `code_commit` at request time since `V2-P4-046`, and the comment
  above that check says exactly why: `build_ranking_manifest` "raises the same objection after a
  store has already been read and would therefore report a mistyped flag as a fact about the
  panel". `config_digest` was left on that later path, so a mistyped one came back as *the
  shortlist could not be joined to the evidence this request supplied* — naming an evidence join
  for a request that supplied no evidence, quoting the internal `CandidateRankingManifest`, and
  only after a whole panel read. The separator in the test is an **empty** store rather than a
  built one: a request-time refusal cannot depend on a panel, so before the fix the empty store
  answered first (`['partition_missing', 'field_missing']`, exit 1) and the digest was never
  examined; after it, the digest answers first (exit 3) and no partition is opened. A built panel
  would have made both orderings refuse and the test could not have told them apart. Six bad
  inputs are refused by name on all three faces, and a seventh well-formed one must still reach
  the panel — that row is what separates a real check from one that refuses everything.
  Adjacent, and fixed with it: `code_commit` was checked only for `< 7` while the contract is
  `min_length=7, max_length=64`, so a 65-character commit still failed late in the same wrong
  place.
- **A factor read that cannot open a partition now names the command that builds it**
  (`V2-P4-067`). The prefix half was already right at `be262ea` — repaired in passing by
  `V2-P4-032`/`049` with nothing pinning it, so an audit was added and proved red by writing
  `rmf_` back into the real file. The second half corrected the row's own diagnosis: `_read`'s
  docstring claimed the factor-tier reads "already refuse with `openalpha factor build ...` (see
  `_resolve_instant`)". `_resolve_instant` refuses a read that *succeeds and returns nothing*;
  an empty store reaches the read that *raises* first, and that refusal named no command at all.
  The exemption covered the class and left the instance uncovered. `_unbuilt_factor_remedy`
  copies `_unbuilt_dataset_remedy`'s boundary — it fires only when no year of that factor's
  partition is registered — and is **raw-only on purpose**: `neutralized` has two partition
  spellings (`factor_neut_*`, `factor_neutmn_*`), so asking `registered_years` about the wrong
  one would answer "nothing is stored" for a panel holding the other and hand back a rebuild the
  caller does not need. Both directions are asserted, and the asymmetry is recorded rather than
  left in prose.

- **Recovery wrote every completed result again after every agent** (`V2-P4-020`).
  `ResearchEngine` saved the whole accumulated `RunRecoveryState` once per agent, and each save
  serialised it twice over — `_updated_recovery` round-tripped it through `model_dump` and
  `model_validate` before `SQLiteRecoveryStore.save`'s own `model_dump_json` ran — so persisting
  `N` results cost `N(N+1)/2` serialisations. Measured on `be262ea` with empty agents: 12 agents
  cost the roadmap's 78; 200 cost 20,100 and 11.74 MB of JSON; 400 cost 80,200 and 46.68 MB, of
  which the dump-and-revalidate half alone was 0.327 s. A run's graph now lives in
  `run_recovery_results`, one row per agent **slot** carrying the `agent_id` the graph declares
  there and a `payload` that is `NULL` until that agent completes, and `RecoveryStore` gained
  `append_result` — one `UPDATE` on the primary key, guarded by `agent_id = ?` and
  `payload IS NULL`, so a result written into the wrong slot or over a finished one is refused by
  name rather than found later as a state that no longer validates. `agent_ids`,
  `completed_results` and `next_agent_index` are derived from those rows on read, which makes
  `validate_progress`' prefix invariant true by construction. After: one serialisation per result
  at every size — 0.23 MB at `N=400` against 46.68 MB — and the loop's wall clock flat per agent.
  **No migration, deliberately**: a row written before the split carries `completed_results`
  inside its payload and is read whole by the key's presence, and `append_result` converts it in
  place the first time it finds no slot to claim. `storage/migrations.py`'s
  `_refuse_uncountable_stored_horizons` now reads both tables, because the recovery plane is the
  only place a whole `SignalFrame` is stored and it had moved.
- **A whole-market shortlist was blocked by the shortlist ceiling, not by the batch it restates**
  (`V2-P4-031`). `V2-P4-019` raised `MAX_BATCH_ITEMS` from 1,000 to 10,000 so a 5,545-security
  market could be expressed, could not touch `backtest/`, and left `MAXIMUM_SHORTLIST` at 1,000
  with its test weakened from `==` to `<=` — an assertion true of every number from 1 to 10,000,
  so nothing was left saying the wall had moved. The two are equal again and the assertion is an
  equality. **The concern that this would make an unreachable path look reachable is measured and
  does not hold**: `V2-P4-043`'s 8 MB wall is on `POST /api/v1/screen`, which carries
  already-researched results inline, while `POST /api/v1/shortlists/run` names a stored cross
  section — 450 bytes at `shortlist_size=1` and **454 at 10,000**. The answer grows and the
  request does not: 53 bytes per shortlist entry and 191 per admitted candidate on the fixture
  panel, so the new ceiling extrapolates to roughly 2.4 MB.
- **Reading an N-year history assessed readiness N times over N partitions** (`V2-P4-069`).
  `read_if_ready` and `read_visible_at` each judge the *whole* requirement and then read *one*
  year, so a full-history read paid N² catalog round trips for a verdict identical all N times.
  Reproduced on a store of 20 securities per partition, which is what says the cost is the
  catalog and not the data: 36 partitions cost **1,296** `_read_coverage` calls and **4.087 s** —
  the same 1,296 and 4.0 s `V2-P4-059` profiled on the real 5,545-security market, where the
  Parquet the read actually wanted was 0.21 s of it. `PanelStore.assessed()` takes the verdict
  once and hands back the per-year reads it licenses; the seven `panel_ingest` loaders that walk
  years take it. After: 36 partitions cost 36 lookups and 0.727 s, and 72 cost 72 and 1.256 s —
  linear, not a smaller constant. `read_if_ready` and `read_visible_at` are unchanged for their
  fourteen callers, being one line each on top of it. **Two docstrings that called this
  "milliseconds" are corrected with numbers**: `load_stock_universe`'s, and `load_daily_bars`',
  where a caller walking a year of 244 sessions spends **5.367 s** re-assessing (22 ms a call)
  against 3.025 s of the `query` calls it wanted — that one is linear rather than quadratic and
  is left standing with a measurement on it. New `KNOWN_STORAGE_LIMITATIONS` entry
  `an_assessed_read_scope_checks_each_partition_file_once_and_not_once_per_read` records what a
  scope gives up.
- **The allowlist whose purpose is reviewing `read_visible_at` callers could not see a new one**
  (`V2-P4-074`). `FILTERED_READ_CALLERS` is scoped to a *file*, so once `panel_ingest.py` was
  granted, every later caller written in it arrived without a line moving — which is not what
  "adding a name here is a deliberate act with a review attached" describes. `V2-P4-061` added
  `load_daily_bars` and `load_price_limits`, `V2-P4-083` added `load_statement_histories`, and
  none of the three tripped it. `FILTERED_READ_REACHERS` is the finer table and the file-level
  allowlist is derived from it. **The row's own framing is corrected by measurement**: those
  issues added no `read_visible_at` *call sites* — `panel_ingest.py` has had exactly two since
  `V2-P4-027` and still has two — they added *reachers* of an existing private helper, so the
  call-site granularity the row's acceptance line offers first would have stayed silent through
  `V2-P4-061` as well. The audit follows intra-module calls instead, and running it surfaced the
  third unreviewed reacher the row does not mention. All three are legitimate and each carries
  its own measured justification; what was missing was the review, not the argument.

  Verified across the four rows by a **52-mutant sweep, 41 killed / 11 survived**, with the
  baseline proven green on all four target suites before a single mutant was generated. Five
  survivors landed on docstring prose and are not mutants at all (recorded rather than removed
  from the denominator); one is the ordering of years inside an error message; one is
  `read_visible_at`'s `pooled_years` condition, moved verbatim by this branch rather than
  written by it. The remaining four were all the same defect this project books most often --
  an assertion that exists but cannot separate the two answers on its fixture -- and all four
  are now killed. Three mutants pinning the scan to `requirement.years[0]` survived because
  every partition in the cost fixture held identical values, so "read year `k`" and "read year 0
  `N` times" returned the same row *count*; the fixture now makes each partition's values name
  its own year, and a fourth test drives the two un-scoped public doors on a multi-year
  requirement, which nothing else did. The fourth replaced `assessed.read_visible_at(...)` with
  `store.read_visible_at(filtered, ...)` inside `_read_visible_event_dated_rows` -- semantically
  identical, byte-identical answer, and the N-squared quietly back for the very caller
  `V2-P4-069` was filed about. No assertion about the answer can catch that, so the audit is
  structural: a function that opens a readiness scope may not also take a per-call door on the
  same store, discriminated by the *receiver* because `assessed.read_visible_at` and
  `store.read_visible_at` share an attribute name.
- **`max_concurrency` was narrowed from 32 to 8 and no user-facing document said so**
  (`V2-P4-042`). `V2-P4-019` lowered `MAX_BATCH_WORKERS` on `POST /api/v1/research/batches`, so a
  request that worked the day before answered `422 Input should be less than or equal to 8` —
  and `grep -rn max_concurrency docs README.md README.en.md web CHANGELOG.md` returned **zero
  hits**, so nowhere a caller looks explained it. The reasoning existed all along, in
  `batch_contracts.py`'s docstring, with the measured 1/2/4/8/16/32 throughput plateau behind it;
  a source comment is not documentation. `docs/api/http.md` now carries the ceiling, the
  measurement table, and the fact that `8` is a property of how batch state is persisted rather
  than a throttle that can be turned back up — and this entry is the release note the narrowing
  should have shipped with. **Breaking-change note for callers still on 32:** nothing about the
  behaviour changed here, only its documentation.
- **`GET /api/v1/research/batches` inlined every item of every batch** (`V2-P4-040`). Twenty
  whole-market batches — about a trading month — answered `items: 115,355, bytes: 36,857,096`
  (36.9 MB) in 2.35 s, and three batches already exceeded the 8 MiB body this same service
  refuses on the way *in*: a listing that had become a bulk export, because `V2-P4-019` raised
  the item ceiling tenfold and the route stayed `return batch_store.list()`. It now answers a
  paginated envelope of **summaries** — `batch_id`, status, the two clocks,
  `cancellation_requested`, `item_count` and a per-status census — with `limit` (default 50, max
  500) and `offset`, and a `total` for the whole shelf. The counting is a `GROUP BY` inside
  SQLite rather than 115,355 items through pydantic. **This is a response-shape change**: the
  route returned a bare JSON array of full `BatchResearchTask` objects and now returns
  `{"batches": [...], "total": n, "limit": n, "offset": n}` with no `items` key per batch. The
  items moved to `GET /api/v1/research/batches/{batch_id}`, which is unchanged. Nothing in this
  repository consumed the listing — no test, no SDK method, no page under `web/` — which is also
  why the defect shipped. No stored contract moved and no migration is involved.
- **The batch ceiling and the request-body ceiling contradicted each other** (`V2-P4-043`). A
  whole-market screen of 5,545 names is 8,190,016 bytes and answered `200`; 6,000 names is
  8,862,051 and answered `413`, against an 8 MiB `OPENALPHA_MAX_REQUEST_BYTES` default — 198,592
  bytes of headroom, about 134 more listings. Measured here and worse than the report: a batch at
  exactly `MAX_BATCH_ITEMS` (10,000, raised by `V2-P4-019` *because* "the market is a moving
  number") is 9,840,054 bytes and was refused `413`, so the ceiling this service declares was
  **unreachable through the only surface that can express it** — no test caught it because every
  test at that scale builds the task in process instead of posting it. The default is now
  33554432 bytes (32 MiB), which clears both declared ceilings with a factor of two for the
  richer evidence real callers send; a body over the ceiling is still refused before it is read.
  The `413` now carries `{"reason": "request_too_large", ...}` naming
  `OPENALPHA_MAX_REQUEST_BYTES`, the declared size and the configured limit, and
  `POST /api/v1/screen` states its own 10,000-item ceiling so one name too far is a `422` naming
  the number rather than a `413` about bytes.
- **`POST /api/v1/screen`'s 422 collapsed three distinct causes into one sentence**
  (`V2-P4-041`). `_parse_research_result` distinguishes `signal_id`, `decision_id` and
  `run_manifest_id` each failing to match its own content, and all three came back as
  `Research result failed integrity validation.` — so a caller holding 5,545 results learned
  neither which record nor which of the three addresses had moved. The refusal now carries the
  `{"reason", "message"}` object the panel gate's `409` established, plus `index`, `subject`,
  `field`, and both the `claimed` and the `derived` address, which is the difference between an
  edited record and an edited identifier. `malformed_research_result` is a fourth, separate
  reason. `POST /api/v1/reports` and `POST /api/v1/backtests/validate` share the fix.
- **The HTTP reference's content-address examples were pinned to the minting function**
  (`V2-P4-067`(a)). The documented `run_manifest_id` prefix was `rmf_` where this repository
  mints `run_`, so a caller copying the example was refused; it was repaired in passing by
  `V2-P4-032`/`V2-P4-049` and nothing held it there. Every `<prefix>_…` example in
  `docs/api/http.md` is now checked against the AST-read prefix census, and the audit is itself
  proved to fail on the exact text the row measured.
- **`openalpha panel build --as-of T` produced a panel that `openalpha panel doctor --as-of T`
  called `BLOCKING`** (`V2-P4-063`). `cli._build_sessions` bounded the fetch loop at the fetch
  clock's Asia/Shanghai date **minus one day**, unconditionally. That is
  `panel_ingest._sessions_published_through` only for the part of the day *before* 16:30
  (`DAILY_AVAILABILITY_TIME`); above it the two came apart by exactly one session, and that
  session is the one the rest of the price plane already agreed about — `_price_requirement`
  clamps a dataset's `required_dates` at it, so a health check **required** it;
  `_read_visible_price_session` refuses only what is past it, so a read would have **served** it;
  and `newest_published_session` resolves a shortlist's pricing session through it, so
  `shortlist run` **priced** against it. Three rules against one, and the one was the build.
  Measured through `CliRunner` at one instant used twice: build exit `0`, eleven sessions ending
  2026-01-19; doctor at that same literal instant exit `1`, `blocking date_gap 1 required date(s)
  are absent from stk_limit, starting at 2026-01-20`. The loop now shares
  `_sessions_published_through` rather than restating it, and the bound is
  `min(date(year, 12, 31), published_through)` — `_price_requirement`'s own expression — so what a
  build fetches and what a health check requires are the same set by construction.
- **`openalpha factor run` and `openalpha factor build` published artifacts stamped with a commit
  the caller never declared** (`V2-P4-052`, `V2-P4-046`'s defect on two more commands). Both
  declared `--code-commit` with an empty-string default and then wrote
  `_resolved_code_commit(code_commit or None)`, so there was no value the parser could hand back
  that meant "the caller typed an empty one": `""` collapsed into *omitted* and resolved from the
  server's git, while the same literal reached the request contract's seven-character rule on the
  SDK and over HTTP and was a `bad_request`. Measured: `factor run --code-commit ""` exited `0`
  having **sealed** an experiment, and `factor build --code-commit ""` exited `0` having written
  four partitions — and `code_commit` is inside every observation's build column, so the mis-stamp
  outlives the command. Both flags now default to `None`; omitting them still resolves the real
  commit, which is driven separately on each command.
- **`--max-staleness-days` refused a factor build on a price panel one day old** (`V2-P4-064`).
  The flag is a *session* bound — its own refusal says so, "a price panel whose newest session is
  a month old has missed a month of the market" — and it was applied unchanged to the security
  registry, which is event-driven: `stock_basic`'s newest instant is the last time a security
  listed or delisted, so its age measures the market's corporate-action calendar rather than this
  fetch. The only way to run the command was to widen the bar to 20–25 days, which switches off
  the check it exists for. `panel doctor` already answers this correctly through
  `DATASET_CADENCE`, and `factor_view.CADENCE_WAIVED_READS` is now held against that table — a
  strict containment plus a literal complement, so a sixth `event_driven` dataset turns it red
  naming itself. What is **not** waived is recorded rather than left to be discovered: the four
  quarterly statement datasets keep the caller's bar because `compute_factor` refuses a waived
  one for every dataset a factor reads, and `index_member_all` keeps it because
  `load_industry_market_cap_cross_section` states one bound for it and `daily_basic` together.
  Both are `KNOWN_FACTOR_RUN_LIMITATIONS
  .the_freshness_bar_is_waived_by_cadence_only_where_the_read_is_outside_the_engine`.
  Two existing guards turned out to be resting on the defect and were re-grounded rather than
  relaxed: the test that proves the registry is read once per *prediction instant* separated the
  two instants by this bound, and now separates them by a delisting whose `available_time` falls
  between them — `universe_counts` reads `[8, 7]`, against `[8, 8]` for a read pinned at the first
  instant and `[7, 7]` for one pinned at the last, so it fails in both directions where the bound
  failed in one. And the sweep that requires every declared build parameter to reach the answer
  had this flag reaching it only by refusing the registry; it now drives the flag at the one
  instant in the fixture window where a session bound can decide anything — the Saturday after the
  newest session, where `1` is `stale` and `2` builds.
- **`openalpha factor build --tier neutralized --as-of <a day the exchange was shut>` exited `5`
  with a withheld traceback instead of a verdict** (`V2-P4-108`, found by the same acceptance and
  pre-existing). `_neutralized` catches `_PANEL_FAULTS` around
  `load_industry_market_cap_cross_section`, and `PriceDataError` — which is what
  `_read_visible_price_session` raises for a non-session day, and which
  `cli._PANEL_WRITE_REFUSALS` and `panel_doctor._LOAD_FAILURES` have both called a fact about data
  for eleven error types — was not in it. So a refusal designed to be an answer reached
  `cli._panel_command` as an unanticipated exception: "a defect in the command, not a verdict
  about the panel — nothing was checked", with the refusal's own sentence withheld because an
  unanticipated frame can be holding the credential. `V2-P4-060`'s shape, one refusal over. Fixed
  at the read rather than in the constant, which is that issue's own arrangement: `_PANEL_FAULTS`
  is restated by `shortlist_view` and pinned as a union across both faces' read seams, and the
  registry read cannot raise this at all. Measured at the same instant: `--tier raw` and
  `--tier processed` both exit `0`, so the residual is the whole of the hole.
- **`openalpha panel doctor --dataset index_daily --no-calendar` had a fix with no product-surface
  test under it** (`V2-P4-087`). The bare `KeyError` was closed when it was found, but the
  assertion beside it calls `panel_health_report` directly while the report is about a command
  line — everything between the two is unasserted. The literal command is now driven through
  `CliRunner`, and the test was checked to separate: removing `_PRICE_SHAPED_FIELDS`' `index_daily`
  row turns it from exit `1` (an empty store is unhealthy, which is the point of the fallback) to
  exit `5`.
- **An agent that declares a *feature* dependency was never routed, and nothing said so**
  (`V2-P4-008`, S38). `AgentRouter.route` was `agent.evidence_families & families`, so an agent
  whose whole dependency is a panel column -- and which therefore declares no evidence family --
  intersected the empty set and was dropped: no entry in `DecisionLedger.routing_path`, no
  `AgentVersion` in the manifest, no abstention. "This agent had nothing to say about this run"
  and "this agent can never say anything about any run" were one observation. `ResearchAgent`
  now declares `feature_dependencies` beside `evidence_families`, and routing satisfies **both**
  halves: a family is satisfied by *any* declared family being present (`ThemeAgent` scores
  whichever of its three arrived, so a partial arrival is a smaller sample), a column only when
  *every* declared one is on the plane (an agent's arithmetic names a column by `feature_id`,
  and a missing column is a missing term rather than a smaller sample). An agent declaring
  neither is refused by name with `UndeclaredAgentDependencyError` rather than dropped -- the
  fail-open answer is worse than it looks, because `SignalFrame` refuses every non-abstaining
  direction with no `evidence_ids`, so such an agent's only reachable output is an abstention
  that `_aggregate` would then average into the run. **This entry understated the breaking
  change and the ninth-wave acceptance measured it**: "an agent declaring an empty
  `evidence_families` is now refused by name" is not the same statement as "an agent that does
  not declare `feature_dependencies` at all crashes", which is what a third-party agent written
  before this row actually got. See `MissingAgentDeclarationError` under Fixed.
- **`AgentContext` had no handle for anything but evidence** (`V2-P4-009`, S36/S38). It now
  carries `features: FeaturePlane | None`, a `runtime_checkable` Protocol declared beside its
  consumer -- `ShortlistDocumentStore`'s and `ExperimentDocumentStore`'s arrangement -- which
  `domain/alpha_model.py::FeatureCrossSection` satisfies structurally with no adapter, so
  `agents/` gains no edge into `feature_matrix` and through it DuckDB. Under
  `arbitrary_types_allowed` the field is an `isinstance` check on method presence rather than a
  pydantic rebuild, which is Implementation Decision 31 on a ~5,500-row panel read; object
  identity is asserted, because equality passes on a rebuilt copy. **The row's proposal to reuse
  `tools/base.py::ResearchTool` was measured and declined**: `ToolRequest.kind` is
  `max_length=64` and the neutralized spelling of this build's longest factor key is **89
  characters** (refused with a `ValidationError`), and `ToolResult` has exactly three fields
  under `extra="forbid"`, none numeric, with `status="success"` requiring a non-empty
  `evidence_ids`. `agents/feature.py::FeatureScoreAgent` ships as the consumer, so the new seam
  is not a second declared-and-unused extension point. Reachable through
  `OpenAlphaSDK(features=...)`; the CLI and REST faces compose no plane, which is recorded
  rather than implied.
- **A cycle in which every routed agent abstained raised `ValidationError` out of `run_cycle`**.
  `_aggregate` computed `direction` from the mean strength before anything looked at
  `evidence_ids`, so an all-abstaining run built a `neutral` frame citing nothing and
  `SignalFrame.validate_conclusion` refused it. Measured on `be262ea` before `V2-P4-008` touched
  anything, with a deterministic agent returning an abstention -- so this predates the row that
  found it and only a `StructuredSignalAgent` whose model abstained could reach it before; a
  feature-dependent agent reaches it on any security the composed column has no number for. The
  repair is `V2-P4-029`'s, one module over: an abstention is the claim that the evidence supports
  no direction, and overruling it means minting a directional conclusion from a frame that cites
  nothing. The aggregate abstains, says **which** of the two reasons applied (nobody was routed,
  or everybody abstained), and carries the abstaining agents' `risk_flags` forward so a `block`
  does not become a `pass`.
- **"Run it, run it again tomorrow, and compare the two" ended at the comparing** (`V2-P4-007`,
  S44/S49). `openalpha shortlist get`'s own docstring describes that workflow and
  `tests/integration/test_shortlist_workflow.py` had to do the last step with a `set` difference
  written into the test. `openalpha shortlist compare <baseline> <current>` and
  `OpenAlphaSDK.compare_shortlists` now report added, removed and held names with each held
  name's rank change, score change and **changed reason** -- direction, risk flags, backing run,
  or a name that stopped being published at all. Both addresses are arguments and neither is
  inferred: `shortlist_id` is a content address and `list_ids` is ascending by sha256, so the
  store genuinely cannot say which answer came first
  (`the_stored_answer_is_addressed_by_content_and_not_by_when_it_was_run`), and a command that
  guessed would be inventing the ordering. Two answers to *different* questions are refused
  naming the key that differs, because a diff across two questions reports every name added and
  every name removed -- true about two lists and false about one market. `rank_change` and
  `score_change` both read "positive means the name moved up", and the sign is asserted against
  the pair it was derived from rather than against a literal.
  A mutation sweep over the two rows' code ran **341 mutants, 326 killed**; the fifteen
  survivors are seven provably equivalent (`Literal` members inside local-variable *type
  annotations*, `@dataclass(slots=True)`, `ensure_ascii` on an all-ASCII payload) and eight
  CLI presentation strings.
  **Two of those three "provably equivalent" examples were remeasured under `V2-P4-115` and
  neither claim held; see that row.** In short: the `Literal` one is killed by `mypy`, which
  this project ships as a gate, so it is a *sweep-tooling* survivor and not an equivalent
  mutant — a sweep whose oracle is pytest alone under-reports whenever a second gate is part
  of the build. The `ensure_ascii` one was equivalent **on the fixture only**, and there is now
  a test with a non-ASCII exchange that kills it. `@dataclass(slots=True)` is the one of the
  three that is genuinely equivalent.
  Two survivors were closed by **changing the design rather than
  adding an assertion**: `schema_version` was removed from `COMPARABLE_KEYS`, where it was
  dead because the shape is refused by name before the two answers are compared with each
  other. And the sweep found a real defect in the refusal it was probing -- `declaration`
  keys were compared with `.get(key)`, so a key **absent** on one side and `null` on the
  other compared equal and the refusal reported "these differ on `[]`", naming nothing.
  `declaration.neutralization` is rendered `null` on every answer this build produces, so
  that is the path an older stored answer takes. It now uses a sentinel.

- **A closed vocabulary with no way to refuse: an undeclared `quality_flags` string answered
  `500 text/plain` on `POST /api/v1/research/run`** (`V2-P4-101`). `V2-P4-030` closed the
  risk-flag set and was right to — a payload writing `future-data` instead of `future_data` used
  to be *scored*, and scored **above** the flag it misspells, so the typo moved its candidate up
  a governed screen. What it did not do is give the refusal a delivery. Measured on `d748796`
  with an evidence payload shaped `{"schema", "family", "facts", "quality_flags"}` (the first two
  are required or `MarketAgent` drops the item by family before this code sees it, and every
  assertion goes vacuously green): `['future_data']` → `200`; `['future-data']` and
  `['totally_made_up']` → **`500`, `content-type: text/plain`, body `Internal Server Error`**.
  `_quality_flags`' own docstring names five paths reachable from outside the process. **The
  fail-open is not restored**: the refusal is correct and only its delivery was wrong.
  `domain/risk_flag.py` now raises `UndeclaredRiskFlagError(ValueError)` carrying the offending
  string, the vocabulary, and — filled in by `_quality_flags`, the only frame that knows them —
  the offending snapshot's `evidence_id` and the flag's position. A **named** exception rather
  than `except ValueError` around the route, which would report an unrelated arithmetic or
  parsing defect as the caller's spelling mistake (the over-broad catch `V2-P4-045` booked on the
  shortlist face). The route answers the FastAPI field-error **list** — not the `{reason,
  message}` object a panel refusal carries, the two `422` schemas this app's docstring records —
  with `loc == ["body", "evidence", 1, "payload", "quality_flags", 1]`, the `input` echoed, and
  a `msg` byte-identical to the one pydantic already writes for `signal.risk_flags` on
  `POST /api/v1/research/deliberate`. `evidence_id` and not an index crosses the agent boundary
  because an agent sees only its own family's items, so an index taken there names the wrong
  item on any mixed-family request.
- **The same refusal was equally undeliverable on two more faces** (`V2-P4-102`).
  `openalpha research run` rendered a rich Python traceback and exited 1 — the *message* was
  already right and the presentation was a stack trace, which `create_app`'s own docstring rules
  out ("naming the specific variable, never a bare traceback"). It now prints the flag and the
  vocabulary on **stderr** and still exits 1: the finding is about presentation, and moving the
  code too would fail a CI job already branching on it for a second, unrelated reason. And
  `POST /api/v1/research/batches` degraded to `{"status":"failed","error_type":"ValueError"}` —
  no message, no flag name, no vocabulary, discarding exactly the diagnostic `parse_risk_flag`
  promises. `error_type` now names the specific subclass, and the whole reason goes into the
  `item_failed` progress event's `detail`, a free `str | None` already published by
  `GET /api/v1/research/batches/{batch_id}/events` — so nothing about a stored contract changed
  and no migration was needed (`BatchTaskItem` is `extra="forbid"`, where an added key is a
  breaking change). The default is still the type alone: `DISCLOSABLE_ITEM_FAULTS` is an
  allow-list, because an unanticipated exception carries whatever the frame it escaped was
  holding and a progress event is append-only and durable. **One claim in the report was
  falsified by measurement**: `POST /api/v1/backtests/replay` was named alongside the other two
  and was never broken — `ReplayRunner.run()` catches `(RuntimeError, ValueError)` per case and
  records `f"{case.run_id}: {type(error).__name__}: {error}"`, so it returned `200` with the
  offending string and all ten flags in `failures[0]` all along. It is the model the other three
  now copy, and it works because the new exception still subclasses `ValueError`; the test is
  kept as a regression guard on that base class rather than deleted.
- **`factor build --tier`'s option help kept a bound `V2-P4-028` had already retracted, and
  contradicted the same `--help` two paragraphs up** (`V2-P4-103`). The option said
  `--tier neutralized` "only succeeds at a prediction instant at or after the panel's own stored
  horizon"; the command's own docstring, in the same output, said that bound "IS GONE" and that
  what remains is one session wide. Measured before choosing: the command line **does** write the
  neutralised tier before the panel's horizon, so the help was the stale half and the code was
  right. The prose and a real build are now asserted in one test — a test that only greps
  `--help` proves the sentence changed, not that it is true, which is the exact failure this
  file exists for. (The report's "eight sessions before the panel's horizon" is eight *calendar*
  days; by sessions it is four and five.)
- **`--min-securities` documented a floor the face does not have, and refused with a pydantic
  model name instead of the flag** (`V2-P4-104`). The help said "the contract's own floor is 3";
  passing `3` got `1 validation error for RedundancySpec … Input should be greater than or equal
  to 4` and exit 3 — no occurrence of `--min-securities` anywhere in it. There is no single
  contract: `factor_request` hands the same integer to `FactorICSpec` (floor 3) and
  `RedundancySpec` (floor 4), and the higher binds. Measured before choosing: the **help** was
  wrong. Both floors are arithmetic — three points are the first cross section at which
  `|r| < 1` is attainable, and at `n = 3` an untied rank correlation is only `±0.5` or `±1`, so
  no `--redundancy-threshold` at or below 0.5 distinguishes anything and lowering the redundancy
  floor would make the survival row call every pair redundant. `factor_request` now refuses
  before constructing either spec, naming the option and both floors, so `openalpha factor run`
  and `POST /api/v1/factors/run` get it from the one shared resolver; previously whichever spec
  happened to be built first decided the message (`2` reported `FactorICSpec`, `3` reported
  `RedundancySpec`). The help interpolates the two constants rather than restating them.
- **The offline guard shadowed a class, not a surface; it is an audit hook now** (`V2-P4-105`).
  `tests/offline_guard.py` shadowed four names on `socket.socket` — the Python *wrapper*, which
  inherits every one of them from the C `_socket.socket` and defines none of its own. `import
  _socket` is one line, and from inside a non-e2e test under the autouse fixture, loopback only,
  three probes walked straight out: `_socket.socket` `connect`+`sendall` delivered
  `b'ESCAPED-TCP'`, `_socket.socket.sendto` returned 11 and the listener received
  `b'ESCAPED-UDP'`, and — needing no fresh class at all — a **guarded** socket's own `detach()`ed
  file descriptor re-wrapped in `_socket.socket` delivered `b'ESCAPED-DETACH'`. The test that was
  supposed to close the surface asserted over `vars(socket.socket)` and is structurally blind to
  all three. The row's preferred repair, widening the shadow onto the base class, was measured and
  **cannot be done**: `setattr(_socket.socket, "connect", …)` raises `TypeError: cannot set
  'connect' attribute of immutable type '_socket.socket'`, and the C class is reachable by too
  many spellings to hold by name (`__bases__[0]`, `__mro__[1]`, `type(sock).__mro__[1]`). So the
  guard moved *below* the class graph instead of across it: a PEP 578 audit hook on
  `socket.connect`, `socket.sendto` and `socket.sendmsg`, raised inside `_socket`'s own C code, so
  a caller reaches them whichever class object it got. Narrowing the claim to "outbound TCP"
  stayed refused for `V2-P4-039`'s reason. Three events and not four is a measurement: CPython
  raises `socket.connect` for `connect_ex` too, and there is no `socket.connect_ex` event. The
  price is stated rather than hidden — an audit hook can never be uninstalled, so `_depth` is what
  turns it on and an e2e test runs with it installed and inert; the compensation is that
  `socket.socket` is now never mutated at all, so the `delattr` a mutation could once skip does
  not exist. Overhead is below this suite's noise (`tests/unit` 33.58s with, 35.49s without). The
  closure argument for `send`/`sendall`/`sendfile` is now driven over the C class rather than
  asserted about a class dict: connect is refused, `sendall` fails as an unconnected socket fails,
  and the loopback listener receives nothing. DNS stays outside the guard and is **not** quietly
  swept in — a child interpreter measures that resolving a name raises the declared event and no
  guarded one — and one new limit is declared: code that reaches the kernel without passing
  through `_socket` (`ctypes.CDLL(None).connect(…)`) raises no event, which is the same class of
  deliberate evasion as a child process. Restoration is now observed end to end in a child
  process: refused inside the block, delivered after it.

- **The content-address audit disclosed one evasion where there were three** (`V2-P4-106`).
  `V2-P4-037` keyed on the literal `24` written at a slice and disclosed `hexdigest()[:_WIDTH]`.
  Two more were found, neither disclosed, each minting a valid `sgs_<24 hex>` and each violating
  all three canonicalisation keywords. Measured on that module alone: the control
  `sha256(c).hexdigest()[:24]` **2 failed**; `[:_WIDTH]` **39 passed**;
  `sha256(c).digest()[:12].hex()` **39 passed**; `blake2b(c, digest_size=12).hexdigest()`, which
  has no slice anywhere, **39 passed**. The third settles it — same hash function, same bytes, and
  it minted the byte-identical address the control did (`sgs_2d711642b726b04401627ca9`), so it is
  not a loophole with a different meaning but the same mint with one token moved. The extractor was
  widened rather than the disclosure: it no longer looks for a slice, for `24`, or for `sha256` by
  name, but finds **every `hashlib` constructor call under `src/`** and sorts each by whether its
  digest is *narrowed* below the algorithm's full width — a subscript on `digest()`/`hexdigest()`
  whatever is in the brackets, a length argument to either (`shake_128(…).hexdigest(12)`, a third
  spelling added as a probe), or `digest_size=`/`digest_length=`/`dklen=` on the constructor.
  `037`'s reason for not widening — that it would sweep in the plain 64-hex checksums — is answered
  by construction rather than by a skip list: those are declared in their own equality-pinned table
  and it is the narrowing *measurement*, not a name, that decides which table a site belongs to, so
  a mint parked among the checksums is red and a checksum that starts truncating is red. Two of the
  row's own numbers moved: "seven checksums" is right about calls and off by one about functions —
  seven full-width `hexdigest()` calls in **six** functions, because
  `ResearchEngine._load_or_start_recovery` hashes twice — and the whole tree measures **14 sites,
  15 calls, 8 mints + 6 checksum functions**. `DIGESTS_PER_SITE` records the one two-hash site and
  closes the direction two function-keyed tables cannot see: a second mint added *inside* a function
  that already hashes. The extractor carries its own test, run over source the module writes itself
  and `exec`s to prove each probe really does mint an address the live pattern accepts.

- **The threshold-2 risk-flag audit fell to one broken literal; half closed, half disclosed by
  name** (`V2-P4-107`). In `decisions/risk.py`, `frozenset({"future" "_data", "look_ahead" +
  "_violation"})` passed (**1 passed**): adjacent literals fold at parse time and *were* caught,
  explicit `+` is an `ast.BinOp` whose halves are each a non-name and was not — and the `blocked`
  band has exactly two members, so a regressing `_blocking_flags` only ever needed one literal
  broken to hide. All four spellings were reproduced: written out **1 failed**, implicit **1
  failed**, `+` **9 passed**, `"".join([…])` **9 passed**. `+` is folded now, and the reason is
  stated rather than dressed up as closing the class: it removes a difference that was an accident
  of where CPython folds constants, not a line anybody drew. The rest of the class is disclosed
  **specifically and executably** — `KNOWN_RUNTIME_ASSEMBLY_EVASION` holds the source, not prose,
  and a test drives all three spellings through the real extractor, requiring the first two to be
  seen and `"".join([…])` **not** to be; it goes red the day somebody closes the class, which is
  the right signal. It is deliberately not a `KNOWN_*` registry entry: all thirty-two registries
  are limitations of the shipped product declared in `src/`, and this is a limitation of a test —
  the precedent is `037`'s own disclosure, living in the module that owns the audit, except that
  this one is executable and so cannot rot into a sentence that used to be true.
  `REGISTRY_ENTRY_COUNTS` is untouched. The identical helper duplicated in
  `tests/unit/domain/test_run_mode.py` carried the identical hole and was fixed and covered too.
  Two more of this repository's sentences were falsified on the way: the helper's claim that
  counting docstrings "would make every one of those modules an offender" is false on this tree —
  the comparison is exact equality between a whole `ast.Constant` and a flag name, a docstring is
  one long constant that never equals `"future_data"`, and counting docstrings changes neither
  audit's answer on **any** module of `src/`; the filter is kept for the case that would match (a
  docstring that *is* a flag name) and now has a test that drives it, because it was unexercised
  code carrying a justification the tree does not support. And `DECLARATION_THRESHOLD` is lifted
  out of the comparison and named, because the threshold *is* all of `V2-P4-030` and nothing
  pinned it: `domain/risk_flag.py` spells all five names and satisfies any threshold at all, so a
  threshold that drifted upward left the suite green.

- **Nothing stopped a second content-address canonicalisation; now an AST audit does**
  (`V2-P4-037`). `domain/_identity.py` says every identity goes through `stable_model_id`, and
  that a second spelling of "canonical" would put two things in play whose difference is
  "invisible until two IDs disagreed" — with nothing enforcing it. The row's own probe turned
  out **not** to be green: rewriting `ShortlistGateManifest.gate_manifest_id` as its own
  `json.dumps` plus `sha256[:24]` moves that declaration's address (`sgt_6c3ec68a…` to
  `sgt_3248f195…`) and does go red, but on arithmetic and by accident — the prefix census counts
  *call sites* of the one function, so replacing one drops it from 27 to 26. Adding a mint
  instead of replacing a call moves that census not at all: a second computed field spelling its
  own `sgs_<24 hex>` left ruff, mypy (140 files), `lint-imports` (8 kept) and `tests/unit`
  (2813 passed) green. `tests/unit/domain/test_contract_identity.py` now reads every truncation
  to a content address's width off `src/` by AST, keyed by the function it sits in — per
  function and not per file, because `domain/factor.py` already holds two and a file-level
  allowlist would admit a third — with equality in both directions, and holds each mint's
  `json.dumps` keywords to `stable_model_id`'s. Two of this repository's own sentences were
  falsified doing it: the allowlist is **eight** mints where the row named five (it missed
  `cross_section_digest`, `stable_answer_digest` and `ParquetEvidenceStore.append`, whose
  `part-<24 hex>` is a file name the pattern rejects), and `_identity.py`'s "three builders" is
  **seven**, because `chr_`, `rkc_`, `sla_` and `ev_` all match `CONTENT_ADDRESS_PATTERN` too.
  What the audit cannot see is written beside it: it reads the literal `24` at the slice, so
  `hexdigest()[:_WIDTH]` would pass, and widening to every `sha256` call in `src/` would mix in
  seven plain 64-hex checksums that are a different question.
- **The `KNOWN_*` entry count is an equality per registry, and a code cannot recur across two
  registries unnoticed** (`V2-P4-038`). `sum(...) >= 301` is satisfied by any non-negative net
  change, and nothing asserted anything about a `code` across registries — which matters because
  the binding one section up tests membership in a set of every literal the *whole* suite
  evaluates, so a code carried by registry A is "bound" by a literal written about registry B,
  making a foreign code the cheapest possible filler for a hole. The row's probe is caught
  already, in `tests/integration` rather than `tests/unit`: 30 of the 32 registries carry a
  literal collection equal to their whole code set, measured by AST. The two that do not are
  where the hole lives, so the probe was rebuilt there — adding
  `KNOWN_CROSS_SECTION_LIMITATIONS`' own `the_cut_is_broken_by_subject_code_when_two_scores_tie`
  as a tenth `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS` entry left `tests/unit` at 2816 passed and the
  seven integration and contract modules that touch a registry at 233 passed, with the total up
  from 301 to 303 under a floor of 301. The floor is now `REGISTRY_ENTRY_COUNTS`, one line per
  registry: per registry rather than one scalar because a scalar sees only the net, and because
  two siblings editing two different lines merge correctly where this module's own history
  records two siblings bumping one scalar to the same wrong value and git merging it silently.
  Cross-registry recurrence is a table of `code → the exact registries it lives in` rather than a
  bare "no code twice", because global uniqueness is **false today and rightly so**: three codes
  recur, in 4, 3 and 2 registries, each for a stated reason.
- **The offline guarantee covers UDP, not only TCP `connect`** (`V2-P4-039`). Reproduced: with
  the guard installed, `connect` on an `AF_INET` socket raised while `sendto` and `sendmsg` each
  returned 5 bytes — a wrapped set of `{connect, connect_ex}` and nothing else — and the guard
  refuses by family rather than by address, so a routable destination was no more refused than
  loopback. Wrapped rather than narrowing the claim to "outbound TCP", because narrowing makes
  the sentence true by making the guarantee smaller, which is the direction every Critical this
  project has booked already went. `GUARDED_SOCKET_METHODS` is the whole outbound surface and
  that is an argument rather than a list: `send`, `sendall` and `socket.sendfile` all need a
  connected socket and `connect` is guarded, so shadowing them would be code no input can reach —
  asserted in both directions. Along the way the patching moved into `tests/offline_guard.py` as
  `refusing_outbound_traffic(target)`: inside an autouse fixture no test could ever see
  `socket.socket` unguarded, so "deleting the shadow is the only restoration that leaves the
  class exactly as it was found" was a `finally` block with nothing under it — measured by
  replacing that `delattr` with `pass` and watching 59 tests stay green. The round trip is now
  driven over a throwaway subclass that inherits the same methods from the same C base.
- **`V2-P4-068`'s ordering-dependent test is closed**, by `V2-P4-089`'s containment rather than by
  anything in this change — verified rather than assumed, because the row asked for a measurement
  either way. The original failing selection is green in both directions
  (`tests/unit/test_import_layering.py` with `tests/unit/runtime/`, 70 passed either order), and
  both reproductions written verbatim into `tests/import_linter_containment.py` are green where
  they were 4 failed and 6 failed. The green is caused by the containment and not by luck: the
  mechanism is still live, and `raw_lint_imports_disables` asserts that the **raw** CLI still
  disables existing loggers, so returning either call site to a bare call turns those selections
  red again.
- **The risk committee no longer answers `500` to an abstention, and the risk-flag vocabulary has
  one owner** (`V2-P4-029`, `V2-P4-030`, `V2-P4-036`). `SignalFrame` has always called itself "a
  research conclusion **or abstention**", and `DeliberationCommittee.review` could not accept one:
  it recomputed `direction` from `adjusted_strength` into a `Literal` with no `abstain` in it, so
  an abstention -- which carries no `evidence_ids`, because that is what abstaining means -- came
  back out directional and died on its own output. `POST /api/v1/research/deliberate` answered
  **`500` with a `text/plain` body reading `Internal Server Error`**, and `OpenAlphaSDK.deliberate`
  raised `ValidationError: directional signal requires evidence`. Both now return the abstention
  unchanged, with the debate still reported beside it: widening the annotation alone would not have
  been enough, since an abstaining signal has `strength == 0` and a live debate would have put
  `debate_net / 2` into it and minted a conclusion out of a frame with no evidence behind it.
- **`risk_flags` is a closed vocabulary, declared once with what each flag is worth.**
  `domain/risk_flag.py::RiskFlag` replaces three disjoint sets -- two on `RiskGate`, one a literal
  inside `DeliberationCommittee.review`'s body -- and both gates derive from it, so
  `regulatory`, `data-quality`, `suspension` and `committee-disagreement` no longer reach the
  runtime gate and clear it. A misspelling is now a **`422` naming `risk_flags` and listing the
  vocabulary** instead of a silent demotion to `unrecognised`, which used to move the candidate
  carrying it *up* a governed screen. Closing the set exposed a drift nobody had recorded: all
  three shipped providers declare `redistribution="restricted"`, so `redistribution_restricted`
  was the only redistribution flag this build could produce and **no gate named it**, while the
  one that was named could not be generated at all. `RiskFlag` is a `StrEnum`, so every stored
  `signal_id` is byte-identical; `docs/api/schemas/signal-frame-v1.json` now states the
  vocabulary instead of `"items": {"type": "string"}`.
- **`SHIPPED_RISK_GATES` is deleted rather than wired up.** It called itself the single source for
  what counts as severe and nothing read it: adding an always-blocking third gate left
  `flag_severity('bogus-flag')` at `unrecognised`, and emptying the registry entirely left
  `flag_severity('future_data')` at `blocked`. A declared vocabulary leaves it nothing to do -- a
  gate does not get to decide what a flag is *worth*, only what to do about one -- so the registry,
  the synthetic one-flag probe that existed to route around the committee's crash, and the
  `lru_cache` that memoised a severity derived by running the gates all went with it.

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
