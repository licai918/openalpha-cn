import type {
  Evidence,
  FactorExperimentEnvelope,
  FactorExperimentIndex,
  Health,
  OutcomeInput,
  PanelHealthReport,
  PortfolioConstructionView,
  PredictionIndex,
  ProviderBatchUpload,
  ReplayReport,
  ResearchResult,
  ShortlistAnswer,
  ShortlistIndex,
  ValidationResult
} from "../types";
import { refusalMessage } from "./refusal";

/**
 * Every request this client makes, with a refused response turned back into prose.
 *
 * The `throw new Error(body)` this used to do put the refusal's raw JSON on the screen,
 * because all four pages render `error.message` verbatim (`V2-P5-041`). `refusalMessage`
 * is where the four documented body shapes are read; see `refusal.ts` for the table and
 * for why the status code is not the discriminator.
 */
async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    throw new Error(refusalMessage(response.status, await response.text()));
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

// ---------------------------------------------------------------------------
// V2-P5-017 data layer: page ③.
// ---------------------------------------------------------------------------

/** Every sealed factor experiment this installation holds, by content address, ascending. */
export function listFactorExperiments(): Promise<FactorExperimentIndex> {
  return requestJson<FactorExperimentIndex>("/api/v1/factors/experiments");
}

/**
 * One sealed experiment, reopened and re-sealed on the way out.
 *
 * The server runs the document through `open_experiment` before answering, so a stored
 * document whose content no longer hashes to its own seal comes back as a refusal rather
 * than as a record that merely differs. That means a non-`ok` response here can mean "the
 * artifact on disk was edited", which is why the panel renders the server's own words
 * instead of a generic "load failed".
 *
 * Encoded for `getShortlist`'s reason: the id arrives from the URL.
 */
export function getFactorExperiment(experimentId: string): Promise<FactorExperimentEnvelope> {
  return requestJson<FactorExperimentEnvelope>(
    `/api/v1/factors/experiments/${encodeURIComponent(experimentId)}`,
  );
}

/** The prediction register, in custody order (not digest order — see V2-P4-098). */
export function listPredictions(): Promise<PredictionIndex> {
  return requestJson<PredictionIndex>("/api/v1/predictions");
}

// ---------------------------------------------------------------------------
// V2-P5-018 data layer: page ④.
// ---------------------------------------------------------------------------

/** The question `POST /api/v1/portfolio/construct` requires, in the units it wants them. */
export type PortfolioConstructionQuery = {
  shortlistId: string;
  /** Tier weights as decimal **strings**; see the note on `constructPortfolio`. */
  tierWeights: string[];
  maxPositionWeight: string;
  maxTotalExposure: string;
  minCashWeight: string;
  turnoverBudget: string | null;
};

/**
 * Weight one admitted shortlist under a declared heuristic policy.
 *
 * **Every decimal goes out as a JSON string, deliberately.** pydantic parses `"0.1"` into
 * `Decimal("0.1")` exactly, while the JSON number `0.1` is a float first and a Decimal
 * second — so sending numbers would put a rounding step in front of the very arithmetic
 * `construction_view` renders as strings to protect. The request and the response therefore
 * use the same representation, and nothing in this file converts a weight to a `number`.
 *
 * `previous` is deliberately not sent. It is the caller's declaration of the book being
 * traded away from, and the contract is explicit that it "is declared by the caller and
 * never read from a ledger" — a browser that invented one would be declaring a position
 * history the user never stated, and turnover is measured against it. Omitting it means the
 * turnover reported is turnover from flat, which is a true answer to a stated question.
 */
export function constructPortfolio(
  query: PortfolioConstructionQuery,
): Promise<PortfolioConstructionView> {
  return requestJson<PortfolioConstructionView>("/api/v1/portfolio/construct", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      shortlist_id: query.shortlistId,
      policy: {
        tier_weights: query.tierWeights,
        limits: {
          max_position_weight: query.maxPositionWeight,
          max_total_exposure: query.maxTotalExposure,
          min_cash_weight: query.minCashWeight,
          // `max_industry_weight` is omitted rather than sent as null: the shipped shortlist
          // face carries no industry on its candidates, so a declared industry cap is
          // refused with a 422 by `construct_portfolio` itself. Sending one from a browser
          // would make every construction fail for a reason the user did not choose.
          turnover_budget: query.turnoverBudget,
        },
      },
    }),
  });
}
