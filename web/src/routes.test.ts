import { describe, expect, it } from "vitest";

import { NAV_ITEMS, ROUTES, SHORTLIST_DETAIL_PATTERN } from "./routes";

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
    const declared = new Set(
      Object.values(ROUTES).filter((value): value is string => typeof value === "string"),
    );
    expect(NAV_ITEMS.map((item) => item.path).filter((path) => !declared.has(path))).toEqual([]);
    expect(NAV_ITEMS.map((item) => item.label)).toEqual(["工作台", "数据体检", "候选清单"]);
  });
});
