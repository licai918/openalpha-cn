// Contract-shaped fixtures for the panel component tests (V2-P5-019).
//
// PRD Implementation Decision 14 requires the component tests to use "公开 API fixture" —
// public API fixtures. These are shaped to match the checked-in schemas under
// `docs/api/schemas/`, the same shapes `App.test.tsx` and `e2e/golden-flow.spec.ts` stub,
// so a panel test cannot pass against a shape the backend would never send.

import type {
  Evidence,
  PanelHealthReport,
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
