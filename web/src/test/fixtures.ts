// Contract-shaped fixtures for the panel component tests (V2-P5-019).
//
// PRD Implementation Decision 14 requires the component tests to use "公开 API fixture" —
// public API fixtures. These are shaped to match the checked-in schemas under
// `docs/api/schemas/`, the same shapes `App.test.tsx` and `e2e/golden-flow.spec.ts` stub,
// so a panel test cannot pass against a shape the backend would never send.

import type {
  Evidence,
  FactorExperimentEnvelope,
  FactorTier,
  FactorTierAttribution,
  FactorTierReport,
  PanelHealthReport,
  PortfolioConstructionView,
  PredictionIndex,
  PredictionIndexEntry,
  ReplayReport,
  ResearchResult,
  ShortlistAnswer,
  ValidationResult,
} from "../types";

export function buildEvidence(overrides: Partial<Evidence> = {}): Evidence {
  return {
    schema_version: "evidence-snapshot/v1",
    evidence_id: "ev_fixture",
    content_hash: "a".repeat(64),
    subject: "000001.SZ",
    kind: "limit_up",
    timeline: {
      event_time: "2026-07-24T09:30:00Z",
      available_time: "2026-07-24T10:00:00Z",
      ingested_time: "2026-07-24T10:01:00Z",
      revision_time: "2026-07-24T10:00:00Z",
    },
    source_id: "synthetic",
    source_uri: "fixture://limit-up",
    source_license: "CC0-1.0",
    redistribution: "allowed",
    summary: "合成涨停证据。",
    payload: {
      schema: "a-share-evidence/v1",
      family: "market_event",
      facts: { close: 10.5, pct_change: 9.99, board_count: 1 },
      quality_flags: [],
    },
    ...overrides,
  };
}

export function buildResearchResult(overrides: Partial<ResearchResult> = {}): ResearchResult {
  return {
    signal: {
      signal_id: "sig_fixture",
      direction: "bullish",
      strength: 0.65,
      confidence: 0.65,
      evidence_ids: ["ev_fixture"],
      risk_flags: [],
    },
    decision: {
      decision_id: "dec_fixture",
      final_action: "watch",
      risk_decision: "pass",
      routing_path: ["market-agent", "risk-gate"],
    },
    manifest: { run_id: "run_fixture", status: "succeeded" },
    agent_results: [],
    ...overrides,
  };
}

export function buildReplayReport(overrides: Partial<ReplayReport> = {}): ReplayReport {
  return {
    total_cases: 4,
    succeeded: 4,
    deterministic_replays: 4,
    look_ahead_violations: 0,
    success_rate: 1,
    validation_ids: ["val_fixture"],
    failures: [],
    ...overrides,
  };
}

export function buildValidationResult(overrides: Partial<ValidationResult> = {}): ValidationResult {
  return {
    validation_id: "val_fixture",
    signal_id: "sig_fixture",
    decision_id: "dec_fixture",
    realized_return: 0.1,
    benchmark_return: 0.02,
    transaction_cost: 0.005,
    net_active_return: 0.075,
    unexplained_return: 0.06,
    confidence: 0.65,
    attribution: [{ category: "rule", name: "transaction-cost", contribution: 0.015 }],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// V2-P5-015 / V2-P5-016 fixtures.
//
// Both defaults are deliberately the *strongest* answer their endpoint can give — a
// spotless panel, an admitted shortlist — so that every test below has to state the one
// field it is actually about. A fixture that already carried a defect would let a test
// pass on the fixture rather than on the rule.
// ---------------------------------------------------------------------------

/** A spotless panel health report: clean, every dataset ready, and **every check run**. */
export function buildPanelHealthReport(
  overrides: Partial<PanelHealthReport> = {},
): PanelHealthReport {
  return {
    as_of: "2026-07-24T10:00:00+00:00",
    is_clean: true,
    counts_by_severity: { blocking: 0, warning: 0, notice: 0 },
    blocked_datasets: [],
    datasets: [
      {
        dataset: "index_daily",
        is_ready: true,
        state: "ready",
        years_requested: [2026],
        years_present: [2026],
        row_count: 4820,
        subject_count: 300,
        // The empty tuple is the stronger claim: every check ran.
        checks_waived: [],
        cadence: "daily",
        max_staleness_seconds: 172800,
        freshness_basis: "event_time",
        event_age_seconds: 3600,
        fetch_age_seconds: 1800,
        revised_row_count: 0,
        codes: [],
      },
    ],
    findings: [],
    cross_checks: [
      {
        name: "index_membership_vs_daily",
        datasets: ["index_daily", "index_member"],
        ran: true,
        skipped_reason: null,
        finding_count: 0,
      },
    ],
    limitations: [
      {
        code: "current_universe_is_not_pit_universe",
        datasets: ["index_member"],
        dates: [],
        detail: "成分股为当前股票池快照，非时间点股票池；回测据此可能高估覆盖率。",
      },
    ],
    ...overrides,
  };
}

/** An admitted shortlist with two candidates, nothing untradeable and nothing unresearched. */
export function buildShortlistAnswer(overrides: Partial<ShortlistAnswer> = {}): ShortlistAnswer {
  return {
    schema_version: "shortlist-view/v1",
    shortlist_id: "sl_fixture",
    is_blocked: false,
    as_of: "2026-07-24T10:00:00+00:00",
    horizon: "swing",
    tier: "processed",
    declaration: {
      tier: "processed",
      transform: "zscore/v1",
      neutralization: null,
      exchange: "XSHG",
      years: [2026],
      components: [{ factor_id: "momentum_20d", factor: "momentum_20d/v1", weight: 1 }],
    },
    cross_section: {
      as_of: "2026-07-24T10:00:00+00:00",
      pricing_session: "2026-07-24",
      universe_count: 300,
    },
    funnel: {
      coverage: "complete",
      scored_count: 300,
      excluded_by_coverage: { incomplete_components: 0, not_admissible: 0, not_valued: 0 },
      tradeable_count: 298,
      refused_by_verdict: { halted: 2 },
      rejection_reasons: { halted: 2 },
      untradeable: [{ subject: "600519.SH", verdict: "halted", reason: "停牌" }],
      untradeable_not_named: 0,
      shortlist: [
        { subject: "000001.SZ", rank: 1, score: 2.31 },
        { subject: "000002.SZ", rank: 2, score: 1.88 },
      ],
    },
    measurement: {
      universe_count: 300,
      scored_count: 300,
      tradeable_count: 298,
      shortlist_count: 2,
      candidate_count: 2,
      tradable_ratio: 0.9933,
      researched_ratio: 1,
      ranking_age_days: 0,
    },
    blocks: [],
    admitted: [
      {
        subject: "000001.SZ",
        rank: 1,
        score: 2.31,
        direction: "bullish",
        confidence: 0.72,
        run_manifest_id: "run_aaa",
        risk_flags: [],
      },
      {
        subject: "000002.SZ",
        rank: 2,
        score: 1.88,
        direction: "bullish",
        confidence: 0.61,
        run_manifest_id: "run_bbb",
        risk_flags: [],
      },
    ],
    unresearched: [],
    evidence_not_shortlisted: [],
    evidence_from_an_unfinished_run: [],
    evidence_without_a_stored_run: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// V2-P5-017 / V2-P5-018 fixtures.
//
// Same rule as the two above: the default is the *strongest* answer its endpoint can give,
// so every test has to state the one thing it weakened. A fixture that started degraded
// would let a classifier pass by returning `degraded` unconditionally.
// ---------------------------------------------------------------------------

/** One tier row. Defaults to a fully measured row; `coverage` is the knob tests turn. */
export function buildTierReport(
  tier: FactorTier,
  overrides: Partial<FactorTierReport> = {},
): FactorTierReport {
  return {
    tier,
    source_manifest_ids: [`fbm_${tier}`],
    ic: {
      tier,
      method: "spearman",
      direction: "higher_is_better",
      factor_id: "fct_fixture",
      horizon_sessions: 5,
      coverage: "measured",
      measured_count: 60,
      mean_ic: 0.031,
      stdev_ic: 0.12,
      icir: 0.258,
      positive_count: 38,
      negative_count: 21,
      zero_count: 1,
      sign_consistency: 0.633,
    },
    portfolio: {
      tier,
      group_count: 5,
      horizon_sessions: 5,
      coverage: "measured",
      measured_count: 60,
      group_mean_net_returns: [-0.004, -0.001, 0.0005, 0.002, 0.006],
      group_mean_gross_returns: [-0.003, 0.0, 0.0015, 0.003, 0.007],
      mean_spread: 0.01,
      stdev_spread: 0.04,
      spread_ir: 0.25,
      hit_rate: 0.58,
    },
    turnover: {
      tier,
      group_count: 5,
      rebalances: 12,
      mean_name_turnover: 0.31,
      mean_money_turnover: 0.29,
    },
    // `null` on raw and present on the other two, exactly as the contract requires.
    survival:
      tier === "raw"
        ? null
        : {
            method: "spearman",
            left_key: "momentum/v1",
            right_key: "momentum/v1",
            left_tier: "raw",
            right_tier: tier,
            coverage: "measured",
            measured_count: 60,
            mean_correlation: 0.82,
            mean_abs_correlation: 0.84,
            stdev_correlation: 0.09,
            verdict: "distinct",
          },
    ...overrides,
  };
}

/** One grid cell. */
export function buildAttribution(
  from_tier: FactorTier,
  to_tier: FactorTier,
  statistic: "mean_ic" | "mean_spread",
  overrides: Partial<FactorTierAttribution> = {},
): FactorTierAttribution {
  return {
    from_tier,
    to_tier,
    statistic,
    retention_floor: 0.6,
    from_value: 0.04,
    to_value: 0.031,
    retention: 0.775,
    verdict: "survives",
    ...overrides,
  };
}

/**
 * The six cells in `ATTRIBUTION_CELL_ORDER` — step-major, statistic-minor — all `survives`.
 *
 * Built as the full six rather than as a short list because the artifact's own validator
 * requires exactly these keys in exactly this order, and a fixture that carried three cells
 * would let a classifier pass against a document the backend would refuse to seal.
 */
export function buildAttributionGrid(): FactorTierAttribution[] {
  const steps: Array<[FactorTier, FactorTier]> = [
    ["raw", "processed"],
    ["processed", "neutralized"],
    ["raw", "neutralized"],
  ];
  return steps.flatMap(([from, to]) => [
    buildAttribution(from, to, "mean_ic"),
    buildAttribution(from, to, "mean_spread"),
  ]);
}

export function buildFactorExperiment(
  overrides: Partial<FactorExperimentEnvelope["document"]["artifact"]> = {},
  envelope: Partial<FactorExperimentEnvelope> = {},
): FactorExperimentEnvelope {
  return {
    schema_version: "factor-experiment-view/v1",
    experiment_id: "fxp_fixture",
    content_digest: "c".repeat(64),
    write: "unchanged",
    ...envelope,
    document: {
      schema_version: "factor-experiment-record/v1",
      sealed_digest: "c".repeat(64),
      built_at: "2026-08-20T02:00:00Z",
      note: null,
      artifact: {
        schema_version: "factor-experiment-artifact/v1",
        spec: {
          retention_floor: 0.6,
          code_commit: "a1b2c3d",
          horizon_sessions: 5,
          ic: {
            method: "spearman",
            definition: {
              key: "momentum",
              version: 1,
              family: "price_momentum",
              direction: "higher_is_better",
              required_fields: ["close", "adj_factor"],
              lookback_sessions: 20,
            },
          },
        },
        tiers: [
          buildTierReport("raw"),
          buildTierReport("processed"),
          buildTierReport("neutralized"),
        ],
        attributions: buildAttributionGrid(),
        ...overrides,
      },
    },
  };
}

export function buildPredictionEntry(
  overrides: Partial<PredictionIndexEntry> = {},
): PredictionIndexEntry {
  return {
    record_id: "prd_fixture",
    standing: "forward",
    standing_proves:
      "this store held these bytes before the instant the outcome became knowable, and the " +
      "batch says it was produced before it too",
    standing_does_not_prove:
      "that the batch was produced when it says it was. predicted_at is whatever the caller " +
      "passed to predict and nothing in this repository can check it",
    as_of: "2026-08-20T07:00:00Z",
    recorded_at: "2026-08-20T07:05:00Z",
    outcome_known_at: "2026-08-27T07:00:00Z",
    horizon: "5d",
    artifact_id: "mdl_fixture",
    model_name: "cross-sectional-rank",
    offered_count: 300,
    scored_count: 288,
    ...overrides,
  };
}

export function buildPredictionIndex(entries?: PredictionIndexEntry[]): PredictionIndex {
  const predictions = entries ?? [buildPredictionEntry()];
  return {
    record_ids: predictions.map((entry) => entry.record_id),
    predictions,
  };
}

/**
 * A clean construction.
 *
 * The three weights are `"0.7"`, `"0.2"` and `"0.1"` and `invested_weight` is `"1"`, and the
 * exact triple is load-bearing rather than decorative. **Measured in this repository's own
 * node**: summed left to right as they appear in `targets`, `0.7 + 0.2 + 0.1` is
 * `0.9999999999999999`, so a panel that recomputes the total instead of rendering the
 * `invested_weight` the contract carries prints a visibly different string. That is the
 * float hole the serialiser renders every decimal as a string to close, and
 * `PortfolioConstructionPanel.test.tsx` asserts on it directly.
 *
 * The first triple tried here was `0.4 / 0.35 / 0.25`, and it was **discarded on
 * measurement**: that one sums to exactly `1` in IEEE-754, so the assertion would have
 * existed while being unable to separate a summing panel from a rendering one. Reordering
 * matters too — `0.1 + 0.2 + 0.7` is exactly `1` — so these stay in descending rank order,
 * which is both the order the backend emits and the order that breaks.
 */
export function buildPortfolioConstruction(
  overrides: Partial<PortfolioConstructionView> = {},
): PortfolioConstructionView {
  return {
    schema_version: "portfolio-construction/v1",
    method: "heuristic, not optimized",
    policy: {
      schema_version: "portfolio-construction-policy/v1",
      tier_weights: ["0.5", "0.3", "0.2"],
      limits: {
        max_position_weight: "0.7",
        max_total_exposure: "1",
        min_cash_weight: "0",
        max_industry_weight: null,
        turnover_budget: null,
      },
    },
    targets: [
      {
        subject: "000001.SZ",
        tier: 1,
        rank: 1,
        score: 0.91,
        industry_code: null,
        weight: "0.7",
        untrimmed_weight: "0.8",
        was_adjusted: true,
      },
      {
        subject: "600519.SH",
        tier: 1,
        rank: 2,
        score: 0.88,
        industry_code: null,
        weight: "0.2",
        untrimmed_weight: "0.2",
        was_adjusted: false,
      },
      {
        subject: "300750.SZ",
        tier: 2,
        rank: 3,
        score: 0.71,
        industry_code: null,
        weight: "0.1",
        untrimmed_weight: "0.1",
        was_adjusted: false,
      },
    ],
    invested_weight: "1",
    cash_weight: "0",
    unallocated_weight: "0",
    turnover: "0.62",
    turnover_before_budget: "0.62",
    turnover_budget: null,
    turnover_damping: null,
    caps_breached_after_turnover_damping: [],
    limitations: [
      {
        code: "the_policy_is_a_heuristic_and_optimises_nothing",
        detail: "No objective is minimised and no covariance is estimated.",
      },
      {
        code: "no_capacity_liquidity_or_cost_term_enters_a_weight",
        detail: "No capacity, liquidity or cost term enters a weight.",
      },
    ],
    ...overrides,
  };
}
