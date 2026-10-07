import { describe, expect, it } from "vitest";

import {
  FACTOR_EXPERIMENT_DETAIL_PATTERN,
  NAV_ITEMS,
  ROUTES,
  SHORTLIST_DETAIL_PATTERN,
} from "./routes";

/**
 * `routes.ts`'s co-located test, required by `testDiscipline.test.ts`.
 *
 * Written at the merge of `V2-P5-014/015/016` with `V2-P5-020` rather than exempting the
 * module: the discipline gate named it, and the file's own header states a failure mode
 * precise enough to test — an id containing `/` "would not produce a broken link, it would
 * produce a *working* link to a different route". A module whose comment can name its own
 * silent failure is a module that can hold a test.
 */
describe("routes", () => {
  it("encodes a shortlist id that would otherwise address a different route", () => {
    // The whole point: this is not a malformed URL, it is a well-formed URL to the wrong
    // place. Asserting `!== "/shortlists/sl_a/data-health"` is what separates the two
    // answers; asserting the encoded form alone would pass on `String(id)`.
    const built = ROUTES.shortlistDetail("sl_a/data-health");
    expect(built).not.toBe("/shortlists/sl_a/data-health");
    expect(built).toBe("/shortlists/sl_a%2Fdata-health");
    expect(built.split("/")).toHaveLength(3);
  });

  it("leaves an ordinary content address untouched", () => {
    // The other direction, so the encoder cannot be satisfied by encoding everything: a
    // hex address is what the server actually mints, and it must round-trip unchanged.
    expect(ROUTES.shortlistDetail("sla_3f9c2a")).toBe("/shortlists/sla_3f9c2a");
  });

  it("pairs the detail builder with the pattern react-router matches", () => {
    // The drift this file exists to prevent, in its own terms: builder and pattern are two
    // declarations of one path, so they are asserted against each other rather than each
    // against a literal.
    const prefix = SHORTLIST_DETAIL_PATTERN.slice(0, SHORTLIST_DETAIL_PATTERN.indexOf(":"));
    expect(ROUTES.shortlistDetail("x")).toBe(`${prefix}x`);
  });

  it("gives every nav entry a route this app declares", () => {
    // `NAV_ITEMS`'s own docstring says a location reachable only by typing it is a location
    // nobody will find. The reverse is the testable half: a nav entry pointing at a path no
    // route serves is a link to a 404.
    const declared = new Set<string>(
      Object.values(ROUTES).flatMap((value) => (typeof value === "string" ? [value] : [])),
    );
    expect(NAV_ITEMS.map((item) => item.path).filter((path) => !declared.has(path))).toEqual([]);
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "工作台",
      "数据体检",
      "候选清单",
      "因子与模型实验室",
      "组合与验证",
    ]);
  });

  // V2-P5-017. The second builder gets the same three assertions as the first, rather than a
  // spot check: it is a second copy of the same encoding decision, and the whole reason
  // `routes.ts` exists is that two declarations of one path drift.
  it("encodes an experiment id that would otherwise address a different route", () => {
    const built = ROUTES.factorExperimentDetail("fxp_a/b");
    expect(built).not.toBe("/factor-lab/fxp_a/b");
    expect(built).toBe("/factor-lab/fxp_a%2Fb");
    expect(built.split("/")).toHaveLength(3);
  });

  it("leaves an ordinary experiment content address untouched", () => {
    expect(ROUTES.factorExperimentDetail("fxp_3f9c2a")).toBe("/factor-lab/fxp_3f9c2a");
  });

  it("pairs the experiment builder with the pattern react-router matches", () => {
    const prefix = FACTOR_EXPERIMENT_DETAIL_PATTERN.slice(
      0,
      FACTOR_EXPERIMENT_DETAIL_PATTERN.indexOf(":"),
    );
    expect(ROUTES.factorExperimentDetail("x")).toBe(`${prefix}x`);
  });

  it("gives the two detail patterns different prefixes", () => {
    // A guard against the copy-paste this file exists to survive: two `:param` patterns that
    // shared a prefix would have react-router match whichever was declared first for both,
    // and the symptom would be a factor page rendering under a shortlist address.
    expect(FACTOR_EXPERIMENT_DETAIL_PATTERN).not.toBe(SHORTLIST_DETAIL_PATTERN);
    expect(ROUTES.factorExperimentDetail("x")).not.toBe(ROUTES.shortlistDetail("x"));
  });
});
