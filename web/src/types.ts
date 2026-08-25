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
