// V2-P5-041. The refusal reader, against bodies measured off a running server.
//
// Every body asserted below was captured with `curl` from `openalpha serve` on a seeded
// temp runtime dir, not hand-written from the docs — which matters, because the defect
// this module fixes was next door to a *fixture* written to match a wrong type. The
// capture is recorded in `docs/api/http.md`'s error-shape table and reproduced in the
// docstrings here.
//
// The rule the four shapes share: a refusal must reach the user as the sentence the
// server wrote, and a pydantic field-error list must still say *which field* failed.

import { describe, expect, it } from "vitest";

import { formatLocation, refusalMessage } from "./refusal";

describe("refusalMessage unwraps every refusal shape this service documents", () => {
  it("reads detail.message out of a panel refusal (page ①, measured: HTTP 409)", () => {
    // curl 'http://127.0.0.1:8731/api/v1/panel/health?dataset=trade_cal&year=2026&...'
    // Note the status: the panel plane's `panel_unreadable` is a 409, not a 422, so the
    // reader may not key off 422 — it keys off the body.
    const body = JSON.stringify({
      detail: {
        reason: "panel_unreadable",
        message:
          "the XSHG calendar could not be read out of this service's panel store: " +
          "Build it first (`openalpha panel build --dataset trade_cal --year <year>`)",
      },
    });

    const message = refusalMessage(409, body);

    expect(message).toBe(
      "the XSHG calendar could not be read out of this service's panel store: " +
        "Build it first (`openalpha panel build --dataset trade_cal --year <year>`)",
    );
    // The whole point: no JSON punctuation survives into what the page renders.
    expect(message).not.toContain('{"detail"');
    expect(message).not.toContain("panel_unreadable");
  });

  it("reads detail.message out of a shortlist refusal (page ②, measured: HTTP 422)", () => {
    const body = JSON.stringify({
      detail: {
        reason: "bad_request",
        message:
          "'sla_doesnotexist000000000000' is not a shortlist address; a stored answer is " +
          "filed under the `shortlist_id` its own body carries (`sla_` and 24 lowercase " +
          "hex characters)",
      },
    });

    expect(refusalMessage(422, body)).toBe(
      "'sla_doesnotexist000000000000' is not a shortlist address; a stored answer is " +
        "filed under the `shortlist_id` its own body carries (`sla_` and 24 lowercase " +
        "hex characters)",
    );
  });

  it("keeps the declared-ceiling refusal's own sentence, extra keys and all", () => {
    // `declared_ceiling_exceeded` is the {reason, message} object plus field/limit/received.
    // It is read by the same branch: `message` is the sentence, the rest is machine detail.
    const body = JSON.stringify({
      detail: {
        reason: "declared_ceiling_exceeded",
        message: "records holds 10001 items, above the declared ceiling of 10000",
        field: "records",
        limit: 10000,
        received: 10001,
      },
    });

    expect(refusalMessage(422, body)).toBe(
      "records holds 10001 items, above the declared ceiling of 10000",
    );
  });

  it("names every field a pydantic list refused, and never flattens the list into one blob", () => {
    // curl 'http://127.0.0.1:8731/api/v1/panel/health' (every required query param missing).
    // This is the shape `V2-P4-051` pinned with tests and `docs/api/http.md` documents as
    // "detail is a **list** of error objects".
    const body = JSON.stringify({
      detail: [
        { type: "missing", loc: ["query", "dataset"], msg: "Field required", input: null },
        { type: "missing", loc: ["query", "year"], msg: "Field required", input: null },
        { type: "missing", loc: ["query", "as_of"], msg: "Field required", input: null },
      ],
    });

    const message = refusalMessage(422, body);

    // Which field failed must survive — that is the whole value of the list shape.
    expect(message).toContain("query.dataset");
    expect(message).toContain("query.year");
    expect(message).toContain("query.as_of");
    expect(message).toContain("Field required");
    expect(message.split("\n")).toHaveLength(3);
  });

  it("addresses a nested item by index, so position is not lost", () => {
    // The `loc` docs/api/http.md quotes for V2-P4-101's quality_flags refusal.
    const body = JSON.stringify({
      detail: [
        {
          type: "literal_error",
          loc: ["body", "evidence", 1, "payload", "quality_flags", 1],
          msg: "Input should be 'stale' or 'partial'",
        },
      ],
    });

    expect(refusalMessage(422, body)).toBe(
      "body.evidence[1].payload.quality_flags[1]：Input should be 'stale' or 'partial'",
    );
  });

  it("renders the errors_elided sentinel, which carries an empty loc and addresses no field", () => {
    // `MAX_VALIDATION_ERRORS` truncation appends {"loc": [], "type": "errors_elided", ...}.
    // An empty `loc` must not render as a stray separator with nothing in front of it.
    const body = JSON.stringify({
      detail: [
        { type: "missing", loc: ["body", "records"], msg: "Field required" },
        { type: "errors_elided", loc: [], msg: "3 further validation error(s) were not listed" },
      ],
    });

    const message = refusalMessage(422, body);

    expect(message).toBe(
      "body.records：Field required\n3 further validation error(s) were not listed",
    );
  });

  it("passes through a bare-string detail, which two portfolio routes use", () => {
    // docs/api/http.md: "a 422 whose `detail` is a plain string: today the only one is
    // reusing an `order_id` the ledger already holds with different content."
    const body = JSON.stringify({ detail: "order_id 'ord_1' is already held with different content" });

    expect(refusalMessage(422, body)).toBe(
      "order_id 'ord_1' is already held with different content",
    );
  });

  it("passes a non-JSON body through unchanged rather than reporting a parse failure", () => {
    expect(refusalMessage(500, "Internal Server Error")).toBe("Internal Server Error");
  });

  it("falls back to the status code when the server sent no body at all", () => {
    expect(refusalMessage(503, "")).toBe("请求失败：HTTP 503");
    expect(refusalMessage(503, "   ")).toBe("请求失败：HTTP 503");
  });

  it("shows the raw body when the JSON carries no detail this reader understands", () => {
    // A shape nobody documented is still information; swallowing it for a generic
    // sentence would be the same defect in the other direction.
    expect(refusalMessage(500, '{"oops":true}')).toBe('{"oops":true}');
    expect(refusalMessage(500, "[1,2,3]")).toBe("[1,2,3]");
    expect(refusalMessage(500, "null")).toBe("null");
  });

  it("shows the raw body when detail is an object with no usable message", () => {
    expect(refusalMessage(422, '{"detail":{"reason":"bad_request"}}')).toBe(
      '{"detail":{"reason":"bad_request"}}',
    );
    expect(refusalMessage(422, '{"detail":{"reason":"x","message":"  "}}')).toBe(
      '{"detail":{"reason":"x","message":"  "}}',
    );
  });

  it("shows the raw body when detail is an empty list", () => {
    expect(refusalMessage(422, '{"detail":[]}')).toBe('{"detail":[]}');
  });

  it("shows the raw body when detail is a blank string", () => {
    expect(refusalMessage(422, '{"detail":"   "}')).toBe('{"detail":"   "}');
  });

  it("survives a list entry that is not shaped like a pydantic error", () => {
    const body = JSON.stringify({
      detail: [{ msg: "no loc here" }, "just a string", 7, { type: "weird" }],
    });

    expect(refusalMessage(422, body)).toBe(
      'no loc here\njust a string\n7\n{"type":"weird"}',
    );
  });
});

describe("formatLocation renders a pydantic loc as an addressable path", () => {
  it("joins names with dots and wraps indexes in brackets", () => {
    expect(formatLocation(["query", "dataset"])).toBe("query.dataset");
    expect(formatLocation(["body", "evidence", 1, "payload"])).toBe("body.evidence[1].payload");
    expect(formatLocation(["body", 0])).toBe("body[0]");
  });

  it("returns an empty string for a loc that addresses nothing", () => {
    expect(formatLocation([])).toBe("");
  });

  it("starts with an index when the very first segment is one", () => {
    expect(formatLocation([2, "name"])).toBe("[2].name");
  });
});
