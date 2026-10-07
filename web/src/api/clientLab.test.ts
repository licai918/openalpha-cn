// V2-P5-017 / V2-P5-018. The data layer for pages ③ and ④.
//
// Kept apart from `client.test.ts` and `clientRoutes.test.ts` for the reason their own
// headers give: those two were split at a merge because each carried its own fixture setup
// and a hand-merge lost one. This file carries a third setup — a `fetch` stub that records
// request *bodies* as well as URLs, which neither of the others needs.
//
// The property this file exists for is the one on `constructPortfolio`: every decimal must
// leave as a JSON **string**. `JSON.stringify` renders `"0.1"` and `0.1` differently, so the
// assertion can separate them; nothing else in the request could.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { constructPortfolio, getFactorExperiment, listFactorExperiments, listPredictions } from "./client";
import {
  buildFactorExperiment,
  buildPortfolioConstruction,
  buildPredictionIndex,
} from "../test/fixtures";

let calls: Array<{ url: string; body: unknown }>;

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        // Parsed back from the serialised body, so the assertion sees exactly what would go
        // over the wire rather than the object handed to `JSON.stringify`.
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      if (url === "/api/v1/factors/experiments") {
        return Response.json({ experiment_ids: ["fxp_a", "fxp_b"] });
      }
      if (url.startsWith("/api/v1/factors/experiments/")) {
        return Response.json(buildFactorExperiment());
      }
      if (url === "/api/v1/predictions") {
        return Response.json(buildPredictionIndex());
      }
      if (url === "/api/v1/portfolio/construct") {
        return Response.json(buildPortfolioConstruction());
      }
      return new Response("unexpected", { status: 500 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the factor laboratory's reads", () => {
  it("lists experiments off the listing route", async () => {
    await expect(listFactorExperiments()).resolves.toEqual({
      experiment_ids: ["fxp_a", "fxp_b"],
    });
    expect(calls[0].url).toBe("/api/v1/factors/experiments");
  });

  it("encodes an experiment id that would otherwise address the listing route", async () => {
    // `getShortlist`'s defect one route over, and the same reason it is not hypothetical: an
    // id containing a slash produces a *working* request to a different address, not a
    // failure. Asserting the encoded form alone would pass on `String(id)`, so the
    // unencoded form is asserted against too.
    await getFactorExperiment("fxp_a/b");
    expect(calls[0].url).not.toBe("/api/v1/factors/experiments/fxp_a/b");
    expect(calls[0].url).toBe("/api/v1/factors/experiments/fxp_a%2Fb");
  });

  it("leaves an ordinary content address untouched", async () => {
    await getFactorExperiment("fxp_3f9c2a");
    expect(calls[0].url).toBe("/api/v1/factors/experiments/fxp_3f9c2a");
  });

  it("reads the prediction register", async () => {
    const index = await listPredictions();
    expect(calls[0].url).toBe("/api/v1/predictions");
    expect(index.predictions[0].standing).toBe("forward");
  });
});

describe("constructPortfolio", () => {
  const query = {
    shortlistId: "sla_fixture",
    tierWeights: ["0.5", "0.3", "0.2"],
    maxPositionWeight: "0.1",
    maxTotalExposure: "1",
    minCashWeight: "0",
    turnoverBudget: null,
  };

  it("sends every decimal as a JSON string and never as a number", async () => {
    // THE assertion of this file. pydantic parses "0.1" into Decimal("0.1") exactly, while
    // the JSON number 0.1 becomes a float first — so sending numbers would put a rounding
    // step in front of the arithmetic `construction_view` renders as strings to protect.
    // `toBe` on the string form is what separates the two: `JSON.parse` gives back `"0.1"`
    // for a string and `0.1` for a number, and `toEqual` would not tell them apart under a
    // loose matcher.
    await constructPortfolio(query);
    const body = calls[0].body as {
      shortlist_id: string;
      policy: { tier_weights: unknown[]; limits: Record<string, unknown> };
    };
    expect(body.shortlist_id).toBe("sla_fixture");
    for (const weight of body.policy.tier_weights) {
      expect(typeof weight).toBe("string");
    }
    expect(body.policy.tier_weights).toEqual(["0.5", "0.3", "0.2"]);
    expect(typeof body.policy.limits.max_position_weight).toBe("string");
    expect(body.policy.limits.max_position_weight).toBe("0.1");
    expect(typeof body.policy.limits.max_total_exposure).toBe("string");
  });

  it("omits max_industry_weight rather than declaring a cap the shipped face must refuse", async () => {
    // `candidates_from_shortlist_answer` reads the shortlist's `admitted` array, which
    // carries no industry, so a declared `max_industry_weight` is refused with a 422 by
    // `construct_portfolio` itself. A browser that sent one would make every construction
    // fail for a reason the user never chose. Asserted as absence of the key, because
    // sending `null` and omitting it are two different request bodies.
    await constructPortfolio(query);
    const body = calls[0].body as { policy: { limits: Record<string, unknown> } };
    expect(Object.keys(body.policy.limits)).not.toContain("max_industry_weight");
  });

  it("does not declare a previous book the user never stated", async () => {
    // The contract says the previous book "is declared by the caller and never read from a
    // ledger", and turnover is measured against it. Inventing one would fabricate a
    // position history and therefore fabricate the turnover number beside it.
    await constructPortfolio(query);
    expect(Object.keys(calls[0].body as object)).toEqual(["shortlist_id", "policy"]);
  });

  it("carries a declared turnover budget through when there is one", async () => {
    await constructPortfolio({ ...query, turnoverBudget: "0.3" });
    const body = calls[0].body as { policy: { limits: Record<string, unknown> } };
    expect(body.policy.limits.turnover_budget).toBe("0.3");
  });

  it("posts to the singular /portfolio/ path the rest of this API uses", async () => {
    await constructPortfolio(query);
    expect(calls[0].url).toBe("/api/v1/portfolio/construct");
  });
});
