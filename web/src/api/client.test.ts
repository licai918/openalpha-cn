// V2-P5-020. The API client tested against a stubbed `fetch` rather than through App.
//
// `client.ts` had no co-located test: it was reached only through App.test.tsx's global
// fetch stub, which asserts on what the screen shows, not on what went over the wire. The
// two things this module gets right that nothing was holding it to are (a) the deliberate
// omission of `code_commit`/`config_digest` from research and replay requests — sending a
// placeholder there fabricated provenance on every run started from this UI (task 17's
// critical finding), and it is a one-line regression to "fix" it back — and (b) surfacing
// the backend's own error body instead of a generic HTTP message.

import { afterEach, describe, expect, it, vi } from "vitest";

import { buildEvidence } from "../test/fixtures";
import {
  buildEvidence as buildEvidenceRequest,
  getHealth,
  queryEvidence,
  runReplay,
  runResearch,
  validateOutcome
} from "./client";
import { buildResearchResult } from "../test/fixtures";

type Call = { url: string; init: RequestInit | undefined };

function stubFetch(response: { ok?: boolean; body?: unknown; text?: string; status?: number }) {
  const calls: Call[] = [];
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return {
      ok: response.ok ?? true,
      status: response.status ?? 200,
      json: async () => response.body ?? {},
      text: async () => response.text ?? ""
    } as Response;
  });
  vi.stubGlobal("fetch", spy);
  return calls;
}

function bodyOf(call: Call): Record<string, unknown> {
  return JSON.parse(String(call.init?.body)) as Record<string, unknown>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("requestJson error handling", () => {
  it("surfaces the backend's own error body rather than a generic message", async () => {
    stubFetch({ ok: false, status: 422, text: "证据的 as_of 晚于事件时间" });
    await expect(getHealth()).rejects.toThrow("证据的 as_of 晚于事件时间");
  });

  // V2-P5-041. The four pages render `error.message` verbatim, so whatever this throws is
  // literally what a user reads. Before this, a refusal reached the screen as its own JSON
  // — `{"detail":{"reason":"panel_unreadable","message":"…"}}` — and the server's sentence,
  // which names the command that fixes the problem, was the part buried in punctuation.
  // The body below was captured with curl from a running `openalpha serve`.
  // These two assert the *whole* message with `toBe`, and that is load-bearing rather than
  // fastidious. Written first as `rejects.toThrow(<the sentence>)`, the panel case passed
  // against the unfixed client — because the raw blob **contains** the sentence as a
  // substring, so a substring matcher cannot tell "the message" from "the message wrapped
  // in JSON punctuation", which is the entire defect. Measured: it went green before a line
  // of `refusal.ts` was wired in. An equality is the only matcher that can see this bug.
  it("unwraps a {reason, message} refusal down to the sentence the server wrote", async () => {
    const sentence =
      "the XSHG calendar could not be read out of this service's panel store: " +
      "Build it first (`openalpha panel build --dataset trade_cal --year <year>`)";
    stubFetch({
      ok: false,
      status: 409,
      text: JSON.stringify({ detail: { reason: "panel_unreadable", message: sentence } }),
    });

    const error = (await getHealth().catch((caught: unknown) => caught)) as Error;

    expect(error.message).toBe(sentence);
  });

  it("keeps every refused field addressable when the backend answers pydantic's list", async () => {
    stubFetch({
      ok: false,
      status: 422,
      text: JSON.stringify({
        detail: [
          { type: "missing", loc: ["query", "dataset"], msg: "Field required" },
          { type: "missing", loc: ["query", "year"], msg: "Field required" },
        ],
      }),
    });

    const error = (await getHealth().catch((caught: unknown) => caught)) as Error;

    // Not one flattened blob: the two fields stay told apart, which is the whole reason
    // `V2-P4-051` pinned this shape.
    expect(error.message).toBe("query.dataset：Field required\nquery.year：Field required");
  });

  it("falls back to the status code only when the backend sent no body", async () => {
    stubFetch({ ok: false, status: 503, text: "" });
    await expect(getHealth()).rejects.toThrow("503");
  });

  it("does not throw on a successful response", async () => {
    stubFetch({ body: { status: "ok", version: "1.2.3" } });
    await expect(getHealth()).resolves.toEqual({ status: "ok", version: "1.2.3" });
  });
});

describe("queryEvidence", () => {
  it("always sends as_of", async () => {
    const calls = stubFetch({ body: { items: [] } });
    await queryEvidence("2024-01-02", "");
    expect(calls[0].url).toContain("as_of=2024-01-02");
  });

  it("omits a blank subject instead of filtering on an empty string", async () => {
    const calls = stubFetch({ body: { items: [] } });
    await queryEvidence("2024-01-02", "   ");
    expect(calls[0].url).not.toContain("subject=");
  });

  it("trims a subject before sending it", async () => {
    const calls = stubFetch({ body: { items: [] } });
    await queryEvidence("2024-01-02", "  600519.SH  ");
    expect(calls[0].url).toContain("subject=600519.SH");
  });

  it("unwraps the items envelope", async () => {
    const evidence = buildEvidence();
    stubFetch({ body: { items: [evidence] } });
    await expect(queryEvidence("2024-01-02", "600519.SH")).resolves.toEqual([evidence]);
  });
});

describe("provenance is never fabricated by the browser", () => {
  it("omits code_commit and config_digest from a research request", async () => {
    const calls = stubFetch({ body: buildResearchResult() });
    await runResearch({ subject: "600519.SH", asOf: "2024-01-02", evidence: [] });
    const body = bodyOf(calls[0]);
    expect(Object.keys(body)).not.toContain("code_commit");
    expect(Object.keys(body)).not.toContain("config_digest");
    expect(body.subject).toBe("600519.SH");
  });

  it("omits code_commit and config_digest from a replay request", async () => {
    const calls = stubFetch({ body: {} });
    await runReplay({ cases: [] });
    const body = bodyOf(calls[0]);
    expect(Object.keys(body)).not.toContain("code_commit");
    expect(Object.keys(body)).not.toContain("config_digest");
  });
});

describe("request shaping", () => {
  it("posts evidence build payloads as JSON and unwraps items", async () => {
    const evidence = buildEvidence();
    const calls = stubFetch({ body: { items: [evidence] } });
    await buildEvidenceRequest({ source_id: "file", rows: [] } as never);
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].init?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("converts observation timestamps to ISO before sending them", async () => {
    const calls = stubFetch({ body: {} });
    await validateOutcome(buildResearchResult(), {
      observationStart: "2024-01-02T00:00:00Z",
      observationEnd: "2024-02-02T00:00:00Z",
      startPrice: 100,
      endPrice: 110,
      benchmarkReturn: 0.01,
      transactionCost: 0.001
    } as never);
    const observation = bodyOf(calls[0]).observation as Record<string, unknown>;
    expect(observation.observation_start).toBe("2024-01-02T00:00:00.000Z");
    expect(observation.observation_end).toBe("2024-02-02T00:00:00.000Z");
  });
});
