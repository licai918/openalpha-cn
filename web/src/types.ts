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
