// V2-P5-021. One place that answers every endpoint this app calls, and counts the calls.
//
// ## Why the fixtures come from `src/test/fixtures.ts`
//
// Before this row, `golden-flow.spec.ts` and `routing.spec.ts` each carried their own
// hand-written copy of a wire payload, and `src/test/fixtures.ts` carried a third — which is
// how audit finding **F64** happened: `page.route` stubbed evidence, research and replay and
// **not** `/api/v1/backtests/validate`, so the desktop golden flow never once reached
// attribution. Nothing went red, because there was no statement anywhere that the flow was
// supposed to get there.
//
// `src/test/fixtures.ts` already says of itself that it is shaped like the checked-in schemas
// under `docs/api/schemas/`, "the same shapes `App.test.tsx` and `e2e/golden-flow.spec.ts`
// stub". That was true by intention and untrue by construction — the e2e file stubbed its own
// literals. Importing the fixtures here makes the sentence load-bearing: a contract change that
// breaks the component tests now breaks the browser tests in the same commit, instead of
// leaving the browser asserting against a shape the backend stopped sending.
//
// ## Why the calls are counted
//
// `expect(page.getByText(...)).toBeVisible()` cannot tell "the panel rendered the answer" from
// "the panel was already showing that text". A money flow's claim is that a *request happened*,
// so the request is what gets counted. `expectReached("validate")` is the assertion F64's
// finding actually needed and nobody could write.

import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

import {
  buildEvidence,
  buildFactorExperiment,
  buildPanelHealthReport,
  buildPortfolioConstruction,
  buildPredictionIndex,
  buildReplayReport,
  buildResearchResult,
  buildShortlistAnswer,
  buildValidationResult,
} from "../src/test/fixtures";

/** Every endpoint `src/api/client.ts` can issue, named the way a test wants to talk about it. */
export type Endpoint =
  | "health"
  | "evidence"
  | "research"
  | "replay"
  | "validate"
  | "panelHealth"
  | "shortlistIndex"
  | "shortlistDetail"
  | "factorExperimentIndex"
  | "factorExperimentDetail"
  | "predictions"
  | "portfolioConstruct";

export const SHORTLIST_ID = "sl_e2e";
export const EXPERIMENT_ID = "fxp_e2e";

/**
 * The stubbed backend for one `page`.
 *
 * Install it in `beforeEach`. Every route is fulfilled from `src/test/fixtures.ts`, every
 * navigation target is a relative path, and no request leaves the machine -- the same offline
 * property the two original spec files had, kept while removing their duplicated payloads.
 */
export class StubbedApi {
  private readonly reached = new Map<Endpoint, number>();

  private constructor(private readonly page: Page) {}

  static async install(page: Page): Promise<StubbedApi> {
    const api = new StubbedApi(page);
    await api.route("health", "**/health", { status: "ok", version: "1.0.0" });
    await api.route("evidence", "**/api/v1/evidence?**", { items: [buildEvidence()] });
    await api.route("research", "**/api/v1/research/run", buildResearchResult());
    await api.route("replay", "**/api/v1/backtests/replay", buildReplayReport());
    // F64's missing stub. Without it this route 404s under the dev proxy and the attribution
    // panel never leaves `loading`, which is exactly what shipped.
    await api.route("validate", "**/api/v1/backtests/validate", buildValidationResult());
    await api.route("panelHealth", "**/api/v1/panel/health?**", buildPanelHealthReport());
    await api.route("shortlistIndex", "**/api/v1/shortlists", { shortlist_ids: [SHORTLIST_ID] });
    await api.route(
      "shortlistDetail",
      "**/api/v1/shortlists/*",
      buildShortlistAnswer({ shortlist_id: SHORTLIST_ID }),
    );
    await api.route("factorExperimentIndex", "**/api/v1/factors/experiments", {
      experiment_ids: [EXPERIMENT_ID],
    });
    await api.route(
      "factorExperimentDetail",
      "**/api/v1/factors/experiments/*",
      buildFactorExperiment({}, { experiment_id: EXPERIMENT_ID }),
    );
    await api.route("predictions", "**/api/v1/predictions", buildPredictionIndex());
    await api.route(
      "portfolioConstruct",
      "**/api/v1/portfolio/construct",
      buildPortfolioConstruction(),
    );
    return api;
  }

  private async route(endpoint: Endpoint, pattern: string, json: unknown): Promise<void> {
    await this.page.route(pattern, (route) => {
      this.reached.set(endpoint, (this.reached.get(endpoint) ?? 0) + 1);
      return route.fulfill({ json });
    });
  }

  /** How many times the app actually issued this request. */
  timesReached(endpoint: Endpoint): number {
    return this.reached.get(endpoint) ?? 0;
  }

  /**
   * The user did something and the app issued this request **exactly once**.
   *
   * For requests a click or a form submission produces. A handler is not an effect, so the
   * count really is one, and one is worth asserting: a double-submitting button is a defect
   * this repository would otherwise ship silently.
   */
  async expectRequested(endpoint: Endpoint): Promise<void> {
    await expect
      .poll(() => this.timesReached(endpoint), {
        message: `the app never issued the ${endpoint} request`,
      })
      .toBe(1);
  }

  /**
   * The page mounted and the app issued this request, once or twice.
   *
   * **Measured, not assumed**: under `vite dev` every mount-time fetch here arrives *twice*.
   * `main.tsx` wraps the tree in `<StrictMode>`, which deliberately double-invokes effects in
   * development to surface effects that are not idempotent. The first version of this method
   * asserted `toBe(1)` and went red on three flows at once with `Expected: 1, Received: 2` --
   * which is how the behaviour was found rather than reasoned about.
   *
   * So the bound is two-sided rather than a floor: at least one request (the page did ask) and
   * at most two (`StrictMode`'s replay, and nothing more). A fetch loop -- the failure a bare
   * "at least once" could never see -- still goes red, because it would run past two.
   */
  async expectRequestedOnMount(endpoint: Endpoint): Promise<void> {
    await expect
      .poll(() => this.timesReached(endpoint), {
        message: `the app never issued the ${endpoint} request on mount`,
      })
      .toBeGreaterThanOrEqual(1);
    expect(
      this.timesReached(endpoint),
      `${endpoint} was requested more than StrictMode's double-invoke explains`,
    ).toBeLessThanOrEqual(2);
  }

  /** The flow did **not** touch this endpoint -- the half that catches an over-eager mount. */
  expectNotReached(endpoint: Endpoint): void {
    expect(this.timesReached(endpoint), `${endpoint} was requested and should not have been`).toBe(
      0,
    );
  }
}
