import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PanelState } from "../../panelState";
import { buildValidationResult } from "../../test/fixtures";
import { describePanelStateContract } from "../../test/panelStateContract";
import type { ValidationResult } from "../../types";
import { AttributionPanel } from "./AttributionPanel";

function renderState(state: PanelState<ValidationResult>) {
  return (
    <AttributionPanel
      hasResearch
      state={state}
      asOf="2026-07-24T10:00"
      onRun={() => {}}
    />
  );
}

describePanelStateContract({
  name: "AttributionPanel",
  renderState,
  data: buildValidationResult(),
  dataText: "+7.50%",
});

describe("AttributionPanel", () => {
  it("shows the unattributed residual beside the named terms", () => {
    render(renderState({ kind: "succeeded", data: buildValidationResult() }));
    expect(screen.getByText("未归因残差")).toBeInTheDocument();
    expect(screen.getByText("+6.00%")).toBeInTheDocument();
    expect(screen.getByText("transaction-cost")).toBeInTheDocument();
  });

  it("qualifies an attribution with no named terms rather than showing it as reconciled", () => {
    // Chosen because it needs no threshold. A residual "large relative to" the net active
    // return would require a cut-off nobody has measured, and an invented cut-off is the
    // kind of unfounded claim this repository keeps deleting. Zero named terms is crisp:
    // 100% of the return is unexplained, so presenting it as a completed attribution is
    // false on its face, whatever the magnitudes are.
    render(
      renderState({
        kind: "degraded",
        data: buildValidationResult({ attribution: [], unexplained_return: 0.075 }),
        reason: "本次归因没有任何具名项，净主动收益全部落在残差中。",
      }),
    );
    expect(screen.getByRole("status")).toHaveTextContent("没有任何具名项");
    // The number is still shown — it is a real measurement, just an unattributed one.
    // It appears exactly twice, and that is the finding rather than an inconvenience: with
    // no named terms the residual *is* the net active return, so the panel prints the same
    // figure as "净主动收益" and as "未归因残差". Asserting the pair is what distinguishes
    // this from a normal attribution, where the two differ (+7.50% against +6.00% above).
    expect(screen.getAllByText("+7.50%")).toHaveLength(2);
    expect(screen.getByText("净主动收益")).toBeInTheDocument();
    expect(screen.getByText("未归因残差")).toBeInTheDocument();
  });
});
