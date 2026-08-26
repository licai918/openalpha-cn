export type Health = {
  status: "ok" | "error";
  version: string;
};

export type Timeline = {
  event_time: string;
  available_time: string;
  ingested_time: string;
  revision_time: string;
};

export type Evidence = {
  schema_version: string;
  evidence_id: string;
  content_hash: string;
  subject: string;
  kind: string;
  timeline: Timeline;
  source_id: string;
  source_uri: string | null;
  source_license: string;
  redistribution: "allowed" | "restricted" | "unknown";
  summary: string;
  payload: Record<string, unknown>;
};

export type ResearchResult = {
  signal: {
    signal_id: string;
    direction: "bullish" | "bearish" | "neutral" | "abstain";
    strength: number;
    confidence: number;
    evidence_ids: string[];
    risk_flags: string[];
    abstention_reason?: string | null;
  };
  decision: {
    decision_id: string;
    final_action: "watch" | "avoid" | "abstain";
    risk_decision: "pass" | "reduce" | "block";
    routing_path: string[];
  };
  manifest: {
    run_id: string;
    status: string;
  };
  agent_results: unknown[];
};

export type ReplayReport = {
  total_cases: number;
  succeeded: number;
  deterministic_replays: number;
  look_ahead_violations: number;
  success_rate: number;
  validation_ids: string[];
  failures: string[];
};

export type ValidationResult = {
  validation_id: string;
  signal_id: string;
  decision_id: string;
  realized_return: number;
  benchmark_return: number;
  transaction_cost: number;
  net_active_return: number;
  unexplained_return: number;
  confidence: number;
  attribution: Array<{
    category: "rule" | "factor" | "agent" | "model";
    name: string;
    contribution: number;
  }>;
};

export type OutcomeInput = {
  observationStart: string;
  observationEnd: string;
  startPrice: number;
  endPrice: number;
  benchmarkReturn: number;
  transactionCost: number;
};

export type ProviderBatchUpload = {
  metadata: Record<string, unknown>;
  batch: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// V2-P5-014 data layer: the two payloads pages ① and ② read.
//
// Both are **deliberate subsets**. `typesContractDrift.test.ts` compares mirror → schema
// in one direction on purpose: a field this file declares must still exist in the
// contract, but a contract field this file omits is not drift. That asymmetry is what
// makes a subset safe and an over-claim a defect, so these mirrors carry only the fields
// pages ① and ② actually render, and nothing is declared "just in case".
//
// Neither has a checked-in JSON schema — `docs/api/schemas/` holds five contracts and
// none of them is a panel report or a shortlist answer — so both are registered in
// `INTENTIONALLY_UNMAPPED_TYPES` naming the Python serialiser they mirror. That follows
// `ReplayReport`'s existing precedent exactly rather than widening what that list means:
// it already holds real wire mirrors whose contract is simply not checked in as JSON.
// ---------------------------------------------------------------------------

/** `openalpha_cn.panel.catalog.ReadinessState`. */
export type ReadinessState = "ready" | "blocked";

/**
 * `GET /api/v1/panel/health` — `openalpha_cn.panel_view.health_report_payload`.
 *
 * `counts_by_severity` is mirrored as three required keys rather than as an open
 * `Record<string, number>`, because the serialiser builds it with
 * `dict.fromkeys(sorted(HEALTH_SEVERITIES), 0)` and says why: "a severity with no findings
 * must read `0`, not be missing, or 'no blocking findings' and 'the blocking key was never
 * emitted' become the same observation for a consumer." Mirroring it as optional keys would
 * re-introduce on this side the exact collapse the server went out of its way to prevent.
 *
 * `checks_waived` and `cross_checks[].ran` are the fields page ① exists to surface: the
 * serialiser's own note is that "the empty tuple is the *stronger* claim ('every check
 * ran'), and a caller drawing a conclusion from `state == "ready"` has to be able to see
 * which questions were never put."
 */
export type PanelHealthReport = {
  as_of: string;
  is_clean: boolean;
  counts_by_severity: { blocking: number; warning: number; notice: number };
  blocked_datasets: string[];
  datasets: Array<{
    dataset: string;
    is_ready: boolean;
    state: ReadinessState;
    years_requested: number[];
    years_present: number[];
    row_count: number;
    subject_count: number;
    checks_waived: string[];
    cadence: string;
    max_staleness_seconds: number | null;
    freshness_basis: string;
    event_age_seconds: number | null;
    fetch_age_seconds: number | null;
    revised_row_count: number;
    codes: string[];
  }>;
  findings: Array<{
    code: string;
    category: string;
    severity: "blocking" | "warning" | "notice";
    dataset: string | null;
    datasets: string[];
    detail: string;
    year: number | null;
    count: number | null;
  }>;
  cross_checks: Array<{
    name: string;
    datasets: string[];
    ran: boolean;
    skipped_reason: string | null;
    finding_count: number;
  }>;
  /** The structural caveats S48/S72/S73 require on screen — kept a sibling of `findings`
   * because, as the serialiser puts it, "a structural boundary of a dataset and a defect
   * of this fetch are different kinds of fact with different remedies". */
  limitations: Array<{
    code: string;
    datasets: string[];
    dates: string[];
    detail?: string;
  }>;
};

/** `GET /api/v1/shortlists` — a listing of content addresses, not of bodies. */
export type ShortlistIndex = {
  shortlist_ids: string[];
};

/**
 * `GET /api/v1/shortlists/{id}` and `POST /api/v1/shortlists/run` —
 * `openalpha_cn.shortlist_view.shortlist_view`.
 *
 * **`admitted: null` and `admitted: []` are two different answers and the mirror keeps them
 * that way.** The serialiser's own words: "`null` and `[]` are the two answers the product
 * acceptance found collapsed into one, and they are now two" — a refused list versus a
 * cleared list over a market that offered nothing. Typing this as `Candidate[]` with an
 * empty-array default would erase, in the mirror, the distinction the server rebuilt.
 */
export type ShortlistAnswer = {
  schema_version: string;
  shortlist_id: string;
  is_blocked: boolean;
  as_of: string;
  horizon: string;
  tier: string;
  /** The whole resolved question. `V2-P4-050`: `tier` alone was not a content address, so
   * the universe and licensing provenance S72 requires per list is read from here. */
  declaration: {
    tier: string;
    transform: string | null;
    neutralization: string | null;
    exchange: string;
    years: number[];
    components: Array<{ factor_id: string; factor: string; weight: number }>;
  };
  cross_section: {
    as_of: string;
    pricing_session: string;
    universe_count: number;
  };
  funnel: {
    coverage: string;
    scored_count: number;
    excluded_by_coverage: Record<string, number>;
    tradeable_count: number;
    refused_by_verdict: Record<string, number>;
    rejection_reasons: Record<string, number>;
    /** Bounded by `MAX_NAMED_UNTRADEABLE` server-side; `untradeable_not_named` is the
     * residual, so a page that showed only this array would under-report. */
    untradeable: Array<{ subject: string; verdict: string; reason: string }>;
    untradeable_not_named: number;
    shortlist: Array<{ subject: string; rank: number; score: number }>;
  };
  measurement: {
    universe_count: number;
    scored_count: number;
    tradeable_count: number;
    shortlist_count: number;
    candidate_count: number;
    tradable_ratio: number;
    researched_ratio: number;
    ranking_age_days: number;
  };
  /** The bars a refused list failed, each with both sides of the comparison. */
  blocks: Array<{
    code: string;
    detail: string;
    measured: number;
    required: number;
  }>;
  admitted: Array<{
    subject: string;
    rank: number;
    score: number;
    direction: string;
    confidence: number;
    run_manifest_id: string;
    risk_flags: string[];
  }> | null;
  unresearched: string[];
  evidence_not_shortlisted: string[];
  evidence_from_an_unfinished_run: string[];
  evidence_without_a_stored_run: string[];
};

// ---------------------------------------------------------------------------
// V2-P5-017 mirrors: the factor laboratory and the prediction register.
//
// Every field below was read off the Python serialiser that produces it, not off a JSON
// schema — `docs/api/schemas/` holds five documents and none of them is a factor
// experiment. The three sources are `factor_view.experiment_view` (the envelope),
// `backtest.factor_experiment.experiment_payload` (the document) and
// `model_view.prediction_index_view` (the register).
//
// **The document excludes computed fields**, and that is measured rather than assumed:
// `experiment_payload` dumps with `exclude_computed_fields=True`, so
// `artifact.content_digest`, `spec.experiment_id`, `definition.factor_id` and
// `definition.qualified_key` are *not* in `document` even though the models declare them.
// The two addresses a caller needs are lifted onto the envelope instead, which is why
// `experiment_id` is mirrored there and nowhere else.
// ---------------------------------------------------------------------------

/** `backtest.factor_ic.FactorTier`. The three rows every experiment carries, in this order. */
export type FactorTier = "raw" | "processed" | "neutralized";

/** `backtest.factor_experiment.AttributionVerdict`.
 *
 * `not_measured` and `no_baseline` are **not** failures — they say the question could not be
 * put, which is a different answer from "the step destroyed the statistic" (`removed`). A
 * rendering that folded them together would report an unmeasured cell as a bad one. */
export type AttributionVerdict =
  | "not_measured"
  | "no_baseline"
  | "reversed"
  | "amplified"
  | "removed"
  | "survives";

/** One cell of the three-tier grid. `ATTRIBUTION_CELL_ORDER` fixes the six, step-major. */
export type FactorTierAttribution = {
  from_tier: FactorTier;
  to_tier: FactorTier;
  statistic: "mean_ic" | "mean_spread";
  retention_floor: number;
  from_value: number | null;
  to_value: number | null;
  retention: number | null;
  verdict: AttributionVerdict;
};

/**
 * One tier's row. `coverage` is mirrored on both studies and is load-bearing:
 * `"insufficient_as_ofs"` / `"insufficient_periods"` mean the statistic beside it is `null`
 * because nothing was measured, not because the factor scored zero.
 */
export type FactorTierReport = {
  tier: FactorTier;
  source_manifest_ids: string[];
  ic: {
    tier: FactorTier;
    method: "pearson" | "spearman";
    direction: "higher_is_better" | "lower_is_better";
    factor_id: string;
    horizon_sessions: number;
    coverage: "measured" | "insufficient_as_ofs";
    measured_count: number;
    mean_ic: number | null;
    stdev_ic: number | null;
    icir: number | null;
    positive_count: number;
    negative_count: number;
    zero_count: number;
    sign_consistency: number | null;
  };
  portfolio: {
    tier: FactorTier;
    group_count: number;
    horizon_sessions: number;
    coverage: "measured" | "insufficient_periods";
    measured_count: number;
    group_mean_net_returns: number[];
    group_mean_gross_returns: number[];
    mean_spread: number | null;
    stdev_spread: number | null;
    spread_ir: number | null;
    hit_rate: number | null;
  };
  turnover: {
    tier: FactorTier;
    group_count: number;
    rebalances: number;
    mean_name_turnover: number | null;
    mean_money_turnover: number | null;
  };
  /**
   * `null` on the raw tier and present on the other two, by construction.
   *
   * **This is raw-vs-this-tier for one and the same factor**, not factor-A against factor-B.
   * `left_key`/`right_key` are carried so the page can say which pair the number is over
   * rather than letting a heading imply a cross-factor correlation the contract never
   * measured. See `FACTOR_LAB_CONTRACT_GAPS` in `contractState.ts`.
   */
  survival: {
    method: "pearson" | "spearman";
    left_key: string;
    right_key: string;
    left_tier: FactorTier;
    right_tier: FactorTier;
    coverage: "measured" | "insufficient_as_ofs";
    measured_count: number;
    mean_correlation: number | null;
    mean_abs_correlation: number | null;
    stdev_correlation: number | null;
    verdict: string;
  } | null;
};

/** `GET /api/v1/factors/experiments`. The whole body. */
export type FactorExperimentIndex = {
  experiment_ids: string[];
};

/** `GET /api/v1/factors/experiments/{id}` and `POST /api/v1/factors/run`. */
export type FactorExperimentEnvelope = {
  schema_version: string;
  experiment_id: string;
  content_digest: string;
  write: "created" | "unchanged";
  document: {
    schema_version: string;
    sealed_digest: string;
    built_at: string;
    note: { subject: string; summary: string } | null;
    artifact: {
      schema_version: string;
      spec: {
        retention_floor: number;
        code_commit: string;
        horizon_sessions: number;
        ic: {
          method: "pearson" | "spearman";
          definition: {
            key: string;
            version: number;
            /**
             * The five families `FactorDefinition.family` declares (`V2-P5-042`).
             *
             * Mirrored as a literal union rather than `string`, which is what it read. Not a
             * cosmetic tightening: while it was `string`, both fixtures carried
             * `price_momentum` / `price_reversal` — values **no server can send**, since the
             * model constrains this to the five below. The same fixture-versus-reality gap
             * that made `required_fields` render `[object Object]`, one field over and not yet
             * visible. As a union, `tsc` refuses an invented value, and
             * `tests/unit/test_web_factor_definition_mirror.py` keeps the five in step with
             * the model.
             */
            family:
              | "value"
              | "quality"
              | "growth"
              | "momentum_reversal"
              | "volatility_liquidity";
            direction: "higher_is_better" | "lower_is_better";
            /**
             * The panel columns this factor reads, one object per column (`V2-P5-042`).
             *
             * Mirrors `openalpha_cn.domain.factor.FactorField`, which declares exactly
             * `dataset` and `column` and forbids extras. **This read `string[]` and shipped**,
             * so `FactorExperimentPanel` rendered `所需字段：[object Object]` on every
             * experiment page. Measured off a live `openalpha serve` rather than inferred:
             *
             *     GET /api/v1/factors/experiments/fxp_3c31ffda36fe1d75227eff70
             *     …"required_fields": [{"column": "close", "dataset": "daily"}]…
             *
             * The wrong type survived because `docs/api/schemas/` holds no contract for this
             * artifact, so `typesContractDrift.test.ts` exempts the whole envelope — and the
             * two fixtures were hand-written to match the wrong type, which made the panel's
             * own assertion green. `tests/unit/test_web_factor_definition_mirror.py` is the
             * check that closes that: it compares this block against `FactorDefinition`'s
             * pydantic-generated schema, so a mirror that disagrees with the model goes red
             * without anyone having to write a fixture correctly.
             */
            required_fields: { dataset: string; column: string }[];
            lookback_sessions: number | null;
          };
        };
      };
      tiers: FactorTierReport[];
      attributions: FactorTierAttribution[];
    };
  };
};

/**
 * One row of `GET /api/v1/predictions`.
 *
 * `standing_proves` and `standing_does_not_prove` are **fields of the contract**, not UI
 * copy, and they are mirrored because the serialiser exists to stop a face printing
 * `standing` and stopping: doing so "turns a local-first bookkeeping fact into what reads
 * like an attestation". A page that renders the enum without the two sentences commits the
 * exact defect the backend added them to prevent.
 */
export type PredictionIndexEntry = {
  record_id: string;
  standing: "forward" | "unwitnessed" | "backfill";
  standing_proves: string;
  standing_does_not_prove: string;
  as_of: string;
  recorded_at: string;
  outcome_known_at: string;
  horizon: string;
  artifact_id: string;
  model_name: string;
  offered_count: number;
  scored_count: number;
};

/** `GET /api/v1/predictions`. `record_ids` is custody order, not digest order. */
export type PredictionIndex = {
  record_ids: string[];
  predictions: PredictionIndexEntry[];
};

// ---------------------------------------------------------------------------
// V2-P5-018 mirrors: portfolio construction.
//
// Read off `backtest.portfolio_policy.construction_view`, which *is* the contract — there
// is no checked-in JSON schema for it.
//
// **Every weight is a `string` and that is deliberate on the wire**, not a mirror being
// lazy. The serialiser says why: decimals are rendered as strings "so a JSON reader cannot
// silently take a weight through a float, which is the one conversion that would make
// `sum(weights) == invested_weight` stop being exactly true". Typing these as `number`
// here would re-open that hole at the first `Number(...)` a component wrote, so the mirror
// keeps them strings and the panel renders them verbatim.
// ---------------------------------------------------------------------------

/** One weighted name. `industry_code` is nullable **and null on the shipped path** — see
 * the `an_industry_cap_is_unenforceable_on_the_shipped_shortlist_face` limitation. */
export type PortfolioTargetWeight = {
  subject: string;
  tier: number;
  rank: number;
  score: number;
  industry_code: string | null;
  weight: string;
  untrimmed_weight: string;
  was_adjusted: boolean;
};

/** `POST /api/v1/portfolio/construct`. */
export type PortfolioConstructionView = {
  schema_version: string;
  /** `Literal["heuristic, not optimized"]` in Python. Rendered, never summarised: a
   * construction that cannot say this sentence does not pass the backend's own validation. */
  method: string;
  policy: {
    schema_version: string;
    tier_weights: string[];
    limits: {
      max_position_weight: string | null;
      max_total_exposure: string | null;
      min_cash_weight: string | null;
      max_industry_weight: string | null;
      turnover_budget: string | null;
    };
  };
  targets: PortfolioTargetWeight[];
  invested_weight: string;
  cash_weight: string;
  unallocated_weight: string;
  turnover: string;
  turnover_before_budget: string;
  turnover_budget: string | null;
  turnover_damping: string | null;
  caps_breached_after_turnover_damping: string[];
  limitations: Array<{ code: string; detail: string }>;
};
