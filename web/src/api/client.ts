import type {
  Evidence,
  Health,
  OutcomeInput,
  PanelHealthReport,
  ProviderBatchUpload,
  ReplayReport,
  ResearchResult,
  ShortlistAnswer,
  ShortlistIndex,
  ValidationResult
} from "../types";

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `请求失败：HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<Health> {
  return requestJson<Health>("/health");
}

export async function queryEvidence(asOf: string, subject: string): Promise<Evidence[]> {
  const search = new URLSearchParams({ as_of: asOf });
  if (subject.trim()) {
    search.set("subject", subject.trim());
  }
  const response = await requestJson<{ items: Evidence[] }>(
    `/api/v1/evidence?${search.toString()}`
  );
  return response.items;
}

export async function buildEvidence(upload: ProviderBatchUpload): Promise<Evidence[]> {
  const response = await requestJson<{ items: Evidence[] }>("/api/v1/evidence/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(upload)
  });
  return response.items;
}

export function runResearch(input: {
  subject: string;
  asOf: string;
  evidence: Evidence[];
}): Promise<ResearchResult> {
  // code_commit/config_digest are intentionally omitted: a browser cannot know the
  // server's own git commit or effective config, so the server resolves both fields
  // itself when they are absent (see api/app.py's ResearchApiRequest). Sending a
  // literal placeholder here used to fabricate provenance on every run started from
  // this UI (task 17 critical finding).
  return requestJson<ResearchResult>("/api/v1/research/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      run_id: `web-${input.subject}-${Date.now()}`,
      mode: "live",
      subject: input.subject,
      as_of: input.asOf,
      evidence: input.evidence,
      random_seed: 7
    })
  });
}

export function runReplay(corpus: unknown): Promise<ReplayReport> {
  // See runResearch above: code_commit/config_digest are omitted so the server
  // resolves them itself (api/app.py's ReplayApiRequest).
  return requestJson<ReplayReport>("/api/v1/backtests/replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      corpus,
      random_seed: 7
    })
  });
}

export function validateOutcome(
  research: ResearchResult,
  outcome: OutcomeInput
): Promise<ValidationResult> {
  return requestJson<ValidationResult>("/api/v1/backtests/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      research,
      observation: {
        observation_start: new Date(outcome.observationStart).toISOString(),
        observation_end: new Date(outcome.observationEnd).toISOString(),
        start_price: outcome.startPrice,
        end_price: outcome.endPrice,
        benchmark_return: outcome.benchmarkReturn,
        transaction_cost: outcome.transactionCost,
        data_quality_notes: ["Submitted from the local OpenAlpha CN workbench."]
      }
    })
  });
}

// ---------------------------------------------------------------------------
// V2-P5-014 data layer: pages ① and ②.
// ---------------------------------------------------------------------------

/** The question `GET /api/v1/panel/health` requires. Every field here is a declared query
 * parameter of that endpoint; `dataset` and `year` are repeatable and mandatory. */
export type PanelHealthQuery = {
  datasets: string[];
  years: number[];
  asOf: string;
  exchange: string;
  calendar: boolean;
};

/**
 * Ask what is wrong with the stored panel at a stated `as_of`.
 *
 * Distinct from `getHealth()`, which is the service's dependency-free liveness probe on
 * `/health`. These are two different questions on two different paths and the names keep
 * them apart, because "the API is up" and "the data is fit to read" is exactly the pair a
 * data-health page exists to stop anyone from conflating.
 *
 * Note this endpoint answers `200` for a *filthy* panel as readily as a clean one — the
 * verdict is `is_clean` in the body, not the status code, which is why `panelHealthStateFrom`
 * and not `response.ok` is what decides how page ① renders it.
 */
export function getPanelHealth(query: PanelHealthQuery): Promise<PanelHealthReport> {
  const search = new URLSearchParams({
    as_of: query.asOf,
    exchange: query.exchange,
    calendar: String(query.calendar)
  });
  for (const dataset of query.datasets) search.append("dataset", dataset);
  for (const year of query.years) search.append("year", String(year));
  return requestJson<PanelHealthReport>(`/api/v1/panel/health?${search.toString()}`);
}

/** Every shortlist answer this installation holds, by content address. */
export function listShortlists(): Promise<ShortlistIndex> {
  return requestJson<ShortlistIndex>("/api/v1/shortlists");
}

/**
 * One stored shortlist answer, by the `shortlist_id` its own body carried.
 *
 * The id is encoded rather than interpolated raw: it arrives from the URL, and a value
 * containing `/` would otherwise address a different route instead of failing.
 */
export function getShortlist(shortlistId: string): Promise<ShortlistAnswer> {
  return requestJson<ShortlistAnswer>(`/api/v1/shortlists/${encodeURIComponent(shortlistId)}`);
}
