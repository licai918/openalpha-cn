// V2-P5-019. The classifiers that turn a contract payload into a panel state.
//
// These exist so `degraded` / `stale` / `blocked` are not decorative. A union can declare
// nine kinds and still be a lie if the application only ever constructs four of them — the
// extra names would then be reachable in a component test and nowhere else, which is the
// same "branch no test has ever rendered" defect one level up. Every assertion below pins
// a classification to a **field that exists in the checked-in contract**, so the state is
// derived from what the backend actually says rather than from a UI guess.

import { describe, expect, it } from "vitest";

import {
  evidenceStateFrom,
  markStale,
  replayStateFrom,
  researchStateFrom,
  validationStateFrom,
} from "./contractState";
import {
  buildEvidence,
  buildReplayReport,
  buildResearchResult,
  buildValidationResult,
} from "./test/fixtures";

describe("evidenceStateFrom", () => {
  it("is empty when the backend returned no visible rows", () => {
    const state = evidenceStateFrom([]);
    expect(state.kind).toBe("empty");
  });

  it("is ready when every row is redistributable", () => {
    expect(evidenceStateFrom([buildEvidence()]).kind).toBe("ready");
  });

  it("is degraded when any row's redistribution is not `allowed`", () => {
    // `redistribution: "allowed" | "restricted" | "unknown"` is a real field in
    // docs/api/schemas' evidence snapshot and is mirrored in types.ts. It has a real
    // consequence too — V2-P5-022 is the row for filtering restricted payloads out of
    // exports — so showing a restricted row as an unqualified success is how a licence
    // breach starts. Both non-`allowed` values count; neither is safe to export.
    for (const redistribution of ["restricted", "unknown"] as const) {
      const state = evidenceStateFrom([buildEvidence({ redistribution })]);
      expect(state.kind, `redistribution=${redistribution}`).toBe("degraded");
      if (state.kind !== "degraded") throw new Error("unreachable");
      expect(state.reason).toContain("1");
      // The rows are still carried: the user asked for them and they are real.
      expect(state.data).toHaveLength(1);
    }
  });

  it("counts only the non-redistributable rows, not the whole result set", () => {
    // A count that said "3" for one restricted row among three would be an assertion that
    // cannot separate "some are restricted" from "all are restricted".
    const state = evidenceStateFrom([
      buildEvidence({ evidence_id: "a" }),
      buildEvidence({ evidence_id: "b", redistribution: "restricted" }),
      buildEvidence({ evidence_id: "c" }),
    ]);
    if (state.kind !== "degraded") throw new Error(`expected degraded, got ${state.kind}`);
    expect(state.reason).toContain("1");
    expect(state.reason).not.toContain("3 条不可");
    expect(state.data).toHaveLength(3);
  });
});

describe("researchStateFrom", () => {
  it("is succeeded for a clean pass with no risk flags", () => {
    expect(researchStateFrom(buildResearchResult()).kind).toBe("succeeded");
  });

  it("is blocked when the risk gate returned `block`", () => {
    // `risk_decision: "pass" | "reduce" | "block"` is mirrored from the contract. Before
    // this row a blocked decision rendered as an ordinary verdict card with the word
    // "block" in small print — the gate's refusal and its approval looked alike.
    const result = buildResearchResult({
      decision: {
        decision_id: "dec_blocked",
        final_action: "avoid",
        risk_decision: "block",
        routing_path: ["risk-gate"],
      },
    });
    const state = researchStateFrom(result);
    expect(state.kind).toBe("blocked");
    if (state.kind !== "blocked") throw new Error("unreachable");
    expect(state.reason).toContain("block");
  });

  it("is degraded, not succeeded, when the signal abstained", () => {
    const state = researchStateFrom(
      buildResearchResult({
        signal: {
          signal_id: "sig_abstain",
          direction: "abstain",
          strength: 0,
          confidence: 0,
          evidence_ids: [],
          risk_flags: [],
          abstention_reason: "证据不足以形成方向",
        },
      }),
    );
    expect(state.kind).toBe("degraded");
    if (state.kind !== "degraded") throw new Error("unreachable");
    // The backend's own words, not a UI rewording.
    expect(state.reason).toContain("证据不足以形成方向");
  });

  it("is degraded when risk flags are carried even though the gate passed", () => {
    const state = researchStateFrom(
      buildResearchResult({
        signal: {
          signal_id: "sig_flagged",
          direction: "bullish",
          strength: 0.4,
          confidence: 0.4,
          evidence_ids: ["ev_fixture"],
          risk_flags: ["st_stock", "suspended"],
        },
      }),
    );
    expect(state.kind).toBe("degraded");
    if (state.kind !== "degraded") throw new Error("unreachable");
    expect(state.reason).toContain("st_stock");
    expect(state.reason).toContain("suspended");
  });

  it("prefers blocked over degraded when the gate blocked a flagged signal", () => {
    // Both conditions hold at once. A classifier that checked flags first would downgrade
    // a refusal to a warning — the exact direction of error this row forbids.
    const state = researchStateFrom(
      buildResearchResult({
        signal: {
          signal_id: "sig_both",
          direction: "bullish",
          strength: 0.4,
          confidence: 0.4,
          evidence_ids: ["ev_fixture"],
          risk_flags: ["st_stock"],
        },
        decision: {
          decision_id: "dec_both",
          final_action: "avoid",
          risk_decision: "block",
          routing_path: ["risk-gate"],
        },
      }),
    );
    expect(state.kind).toBe("blocked");
  });
});

describe("replayStateFrom", () => {
  it("is succeeded for a clean corpus", () => {
    expect(replayStateFrom(buildReplayReport()).kind).toBe("succeeded");
  });

  it("is empty when the corpus contained no cases", () => {
    const state = replayStateFrom(
      buildReplayReport({ total_cases: 0, succeeded: 0, deterministic_replays: 0, success_rate: 0 }),
    );
    expect(state.kind).toBe("empty");
  });

  it("is blocked when the replay found a look-ahead violation", () => {
    // PRD Decision 19 makes "zero known severe look-ahead violations" a release gate, and
    // Decision 8 makes look-ahead a fail-closed gate of its own. A report carrying
    // violations is a finding, not a result — it must not draw the passing progress bar.
    const state = replayStateFrom(buildReplayReport({ look_ahead_violations: 3 }));
    expect(state.kind).toBe("blocked");
    if (state.kind !== "blocked") throw new Error("unreachable");
    expect(state.reason).toContain("3");
  });

  it("is blocked on violations even when every case otherwise succeeded", () => {
    // The fixture that separates the two answers: succeeded === total_cases and
    // success_rate === 1, so any classifier keying on the success counters alone calls
    // this a clean run. Only reading look_ahead_violations gets it right.
    const state = replayStateFrom(
      buildReplayReport({ total_cases: 4, succeeded: 4, success_rate: 1, look_ahead_violations: 1 }),
    );
    expect(state.kind).toBe("blocked");
  });

  it("is degraded when some cases failed but no look-ahead violation was found", () => {
    const state = replayStateFrom(
      buildReplayReport({
        total_cases: 4,
        succeeded: 3,
        success_rate: 0.75,
        failures: ["case_2: determinism mismatch"],
      }),
    );
    expect(state.kind).toBe("degraded");
    if (state.kind !== "degraded") throw new Error("unreachable");
    expect(state.reason).toContain("case_2");
  });
});

describe("validationStateFrom", () => {
  it("is succeeded when the attribution names at least one term", () => {
    expect(validationStateFrom(buildValidationResult()).kind).toBe("succeeded");
  });

  it("is degraded when no term is named and the whole return is residual", () => {
    const state = validationStateFrom(buildValidationResult({ attribution: [] }));
    expect(state.kind).toBe("degraded");
    if (state.kind !== "degraded") throw new Error("unreachable");
    expect(state.reason).toContain("具名");
  });
});

describe("markStale", () => {
  it("turns a data-carrying state into `stale` while keeping its payload", () => {
    const ready = evidenceStateFrom([buildEvidence()]);
    const stale = markStale(ready, "表单已改动，结果对应上一次查询");
    expect(stale.kind).toBe("stale");
    if (stale.kind !== "stale") throw new Error("unreachable");
    expect(stale.data).toHaveLength(1);
    expect(stale.reason).toBe("表单已改动，结果对应上一次查询");
  });

  it("leaves a refusal alone: a blocked result does not become merely stale", () => {
    // Downgrading `blocked` to `stale` would put the payload back on screen (stale carries
    // data, blocked does not) — it would re-introduce exactly the defect, by a side door.
    const blocked = replayStateFrom(buildReplayReport({ look_ahead_violations: 2 }));
    expect(markStale(blocked, "any reason")).toBe(blocked);
  });

  it("leaves idle, loading and empty alone", () => {
    for (const state of [
      { kind: "idle" } as const,
      { kind: "loading" } as const,
      { kind: "empty", reason: "无" } as const,
    ]) {
      expect(markStale(state, "any reason")).toBe(state);
    }
  });
});
