import type {
  Evidence,
  Health,
  OutcomeInput,
  ProviderBatchUpload,
  ReplayReport,
  ResearchResult,
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
  return requestJson<ResearchResult>("/api/v1/research/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      run_id: `web-${input.subject}-${Date.now()}`,
      mode: "live",
      subject: input.subject,
      as_of: input.asOf,
      evidence: input.evidence,
      code_commit: "web-development",
      config_digest: "0".repeat(64),
      random_seed: 7
    })
  });
}

export function runReplay(corpus: unknown): Promise<ReplayReport> {
  return requestJson<ReplayReport>("/api/v1/backtests/replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      corpus,
      code_commit: "web-development",
      config_digest: "0".repeat(64),
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
