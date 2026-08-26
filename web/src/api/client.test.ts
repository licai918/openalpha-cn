// V2-P5-014. The data layer's own tests.
//
// URL construction is the whole logic in these three functions, and two of the three ways
// it can be wrong are silent: `set` instead of `append` on a repeatable parameter drops
// every value but the last, and a raw interpolation of a path segment produces a *working*
// request to the wrong address. Both are asserted below against the exact URL, because a
// test that only checked "fetch was called" cannot see either.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getPanelHealth, getShortlist, listShortlists } from "./client";
import { buildPanelHealthReport, buildShortlistAnswer } from "../test/fixtures";

let calls: string[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      if (String(input).startsWith("/api/v1/panel/health")) {
        return Response.json(buildPanelHealthReport());
      }
      if (String(input) === "/api/v1/shortlists") {
        return Response.json({ shortlist_ids: ["sl_aaa"] });
      }
      return Response.json(buildShortlistAnswer());
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("getPanelHealth", () => {
  it("repeats `dataset` and `year` once per value instead of overwriting them", async () => {
    // The endpoint declares both as `list[...]`. Building this with `URLSearchParams.set`
    // would send `dataset=index_member&year=2026` — a request for *one* dataset and one
    // year that answers `200` with a perfectly valid report about the wrong question.
    await getPanelHealth({
      datasets: ["index_daily", "index_member"],
      years: [2025, 2026],
      asOf: "2026-07-24T10:00:00.000Z",
      exchange: "XSHG",
      calendar: true,
    });
    const url = calls[0];
    expect(url).toContain("dataset=index_daily");
    expect(url).toContain("dataset=index_member");
    expect(url).toContain("year=2025");
    expect(url).toContain("year=2026");
    expect(url.match(/dataset=/g)).toHaveLength(2);
    expect(url.match(/year=/g)).toHaveLength(2);
  });

  it("sends as_of, exchange and calendar as the endpoint declares them", async () => {
    await getPanelHealth({
      datasets: ["index_daily"],
      years: [2026],
      asOf: "2026-07-24T10:00:00.000Z",
      exchange: "XSHE",
      calendar: false,
    });
    const url = new URL(calls[0], "http://127.0.0.1");
    expect(url.searchParams.get("as_of")).toBe("2026-07-24T10:00:00.000Z");
    expect(url.searchParams.get("exchange")).toBe("XSHE");
    // `calendar` is a declared boolean parameter; "false" and a missing key are different
    // requests, so the value is always sent rather than omitted when falsy.
    expect(url.searchParams.get("calendar")).toBe("false");
  });

  it("returns the report body unchanged", async () => {
    const report = await getPanelHealth({
      datasets: ["index_daily"],
      years: [2026],
      asOf: "2026-07-24T10:00:00.000Z",
      exchange: "XSHG",
      calendar: true,
    });
    expect(report.is_clean).toBe(true);
    expect(report.cross_checks[0].name).toBe("index_membership_vs_daily");
  });
});

describe("listShortlists", () => {
  it("asks the parameterless listing route and hands back the ids", async () => {
    expect(await listShortlists()).toEqual({ shortlist_ids: ["sl_aaa"] });
    expect(calls).toEqual(["/api/v1/shortlists"]);
  });
});

describe("getShortlist", () => {
  it("addresses the id as a single path segment", async () => {
    await getShortlist("sl_aaa");
    expect(calls).toEqual(["/api/v1/shortlists/sl_aaa"]);
  });

  it("encodes an id that would otherwise address a different route", async () => {
    // Defence in depth: `shortlist_id` is a server-minted content digest today, so this is
    // not a live hazard — but interpolating a path segment raw fails by *succeeding*
    // somewhere else, which is the failure mode worth spending one function call on.
    await getShortlist("a/b");
    expect(calls).toEqual(["/api/v1/shortlists/a%2Fb"]);
  });

  it("throws with the server's own words when the id is unknown", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no shortlist is filed under sl_missing", { status: 404 })),
    );
    await expect(getShortlist("sl_missing")).rejects.toThrow(
      "no shortlist is filed under sl_missing",
    );
  });
});
