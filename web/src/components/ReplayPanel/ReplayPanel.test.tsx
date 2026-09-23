import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PanelState } from "../../panelState";
import { buildReplayReport } from "../../test/fixtures";
import { describePanelStateContract } from "../../test/panelStateContract";
import type { ReplayReport } from "../../types";
import { ReplayPanel } from "./ReplayPanel";

function renderState(state: PanelState<ReplayReport>) {
  return <ReplayPanel state={state} onRun={() => {}} />;
}

describePanelStateContract({
  name: "ReplayPanel",
  renderState,
  data: buildReplayReport(),
  dataText: "100% 案例通过完整验证",
});

describe("ReplayPanel", () => {
  it("shows the look-ahead violation count on a clean report", () => {
    render(renderState({ kind: "succeeded", data: buildReplayReport() }));
    expect(screen.getByText("前视违规")).toBeInTheDocument();
  });

  it("renders a look-ahead violation as a refusal, never as a passing report", () => {
    // The sharpest case this row exists for. A ReplayReport with look_ahead_violations > 0
    // is a *finding*, and PRD Decision 19 makes "zero known severe look-ahead violations" a
    // release gate. Under the old `report`/`loading`/`error` props this arrived as a
    // perfectly ordinary `report` with `error: null`, and the panel drew the same green
    // progress bar it draws for a clean run — an error rendered as a success, literally.
    render(
      renderState({
        kind: "blocked",
        reason: "回放发现 3 处前视违规，结果不可用。",
      }),
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("3 处前视违规");
    // The progress bar is the "everything passed" affordance; it must be absent.
    expect(document.querySelector("progress")).toBeNull();
  });
});
