import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PanelState } from "../../panelState";
import { buildResearchResult } from "../../test/fixtures";
import { describePanelStateContract } from "../../test/panelStateContract";
import type { ResearchResult } from "../../types";
import { DecisionPanel } from "./DecisionPanel";

function renderState(state: PanelState<ResearchResult>) {
  return <DecisionPanel evidenceCount={1} state={state} onRun={() => {}} />;
}

function buildResult(finalAction: string): ResearchResult {
  return buildResearchResult({
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
  });
}

describePanelStateContract({
  name: "DecisionPanel",
  renderState,
  data: buildResult("watch"),
  dataText: "观察",
});

describe("DecisionPanel", () => {
  it("renders the Chinese label for a known final_action", () => {
    render(renderState({ kind: "succeeded", data: buildResult("watch") }));
    expect(screen.getByText("观察")).toBeInTheDocument();
  });

  it("does not render a blank verdict for a final_action it does not recognise", () => {
    render(renderState({ kind: "succeeded", data: buildResult("escalate") }));
    const verdict = document.querySelector(".decision-verdict strong");
    expect(verdict).not.toBeNull();
    expect(verdict?.textContent?.trim()).not.toBe("");
  });

  it("surfaces the raw unrecognised value instead of hiding it", () => {
    render(renderState({ kind: "succeeded", data: buildResult("escalate") }));
    expect(screen.getByText(/escalate/)).toBeInTheDocument();
  });

  it("tells the user evidence is missing when there is none to run on", () => {
    render(<DecisionPanel evidenceCount={0} state={{ kind: "idle" }} onRun={() => {}} />);
    expect(screen.getByText("证据不足")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运行研究" })).toBeDisabled();
  });

  it("renders a blocking risk decision as a refusal, not as a verdict", () => {
    // `risk_decision: "block"` is a real value in the checked-in contract (types.ts mirrors
    // it as `"pass" | "reduce" | "block"`). Before this row the panel rendered it as an
    // ordinary decision block with the word "block" in small print beside the verdict —
    // the risk gate's refusal and its approval looked the same at a glance.
    render(
      renderState({
        kind: "blocked",
        reason: "风险门判定为 block：该标的当前不可交易。",
      }),
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("风险门判定为 block");
    // No verdict block at all — not a verdict that happens to say "block".
    expect(document.querySelector(".decision-verdict")).toBeNull();
  });
});
