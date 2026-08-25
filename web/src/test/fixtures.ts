// Contract-shaped fixtures for the panel component tests (V2-P5-019).
//
// PRD Implementation Decision 14 requires the component tests to use "公开 API fixture" —
// public API fixtures. These are shaped to match the checked-in schemas under
// `docs/api/schemas/`, the same shapes `App.test.tsx` and `e2e/golden-flow.spec.ts` stub,
// so a panel test cannot pass against a shape the backend would never send.

import type { Evidence, ReplayReport, ResearchResult, ValidationResult } from "../types";

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
