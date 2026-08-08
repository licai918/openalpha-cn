import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ResearchResult } from "../../types";
import { DecisionPanel } from "./DecisionPanel";

function buildResult(finalAction: string): ResearchResult {
  return {
    signal: {
      signal_id: "sig_1",
      direction: "bullish",
      strength: 0.5,
      confidence: 0.5,
      evidence_ids: [],
      risk_flags: [],
    },
    decision: {
      decision_id: "dec_1",
      // Cast, not a real literal: this simulates a backend that returns a
      // final_action value outside the frontend's known union (a schema enum
      // gaining a value types.ts and DecisionPanel don't yet know about — see
      // typesContractDrift.test.ts's enum-value drift guard for the compile-time
      // side of this same scenario).
      final_action: finalAction as ResearchResult["decision"]["final_action"],
      risk_decision: "pass",
      routing_path: ["market-agent"],
    },
    manifest: { run_id: "run_1", status: "succeeded" },
    agent_results: [],
  };
}

describe("DecisionPanel", () => {
  it("renders the Chinese label for a known final_action", () => {
    render(
      <DecisionPanel evidence={[]} result={buildResult("watch")} loading={false} error={null} onRun={() => {}} />,
    );
    expect(screen.getByText("观察")).toBeInTheDocument();
  });

  it("does not render a blank verdict for a final_action it does not recognise", () => {
    render(
      <DecisionPanel
        evidence={[]}
        result={buildResult("escalate")}
        loading={false}
        error={null}
        onRun={() => {}}
      />,
    );
    const verdict = document.querySelector(".decision-verdict strong");
    expect(verdict).not.toBeNull();
    expect(verdict?.textContent?.trim()).not.toBe("");
  });

  it("surfaces the raw unrecognised value instead of hiding it", () => {
    render(
      <DecisionPanel
        evidence={[]}
        result={buildResult("escalate")}
        loading={false}
        error={null}
        onRun={() => {}}
      />,
    );
    expect(screen.getByText(/escalate/)).toBeInTheDocument();
  });
});
