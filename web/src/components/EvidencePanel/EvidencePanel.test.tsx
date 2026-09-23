import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PanelState } from "../../panelState";
import { buildEvidence } from "../../test/fixtures";
import { describePanelStateContract } from "../../test/panelStateContract";
import type { Evidence } from "../../types";
import { EvidencePanel } from "./EvidencePanel";

function renderState(state: PanelState<Evidence[]>) {
  return (
    <EvidencePanel
      subject="000001.SZ"
      asOf="2026-07-24T10:00"
      state={state}
      onSubjectChange={() => {}}
      onAsOfChange={() => {}}
      onQuery={() => {}}
      onImport={() => {}}
    />
  );
}

describePanelStateContract({
  name: "EvidencePanel",
  renderState,
  data: [buildEvidence()],
  dataText: "合成涨停证据。",
});

describe("EvidencePanel", () => {
  it("keeps its own idle copy rather than the shared empty copy", () => {
    // `idle` and `empty` are different answers: "you have not asked" versus "we asked and
    // there is nothing visible at that clock". EvidencePanel had this distinction before
    // this row and must not lose it — panelMessage returns null for idle precisely so the
    // panel supplies wording that reads correctly for *this* panel.
    render(renderState({ kind: "idle" }));
    expect(screen.getByText("尚未查询证据")).toBeInTheDocument();
  });

  it("lists one entry per evidence item with its own id and available_time", () => {
    render(
      renderState({
        kind: "ready",
        data: [
          buildEvidence({ evidence_id: "ev_one", summary: "第一条" }),
          buildEvidence({ evidence_id: "ev_two", summary: "第二条" }),
        ],
      }),
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("ev_one")).toBeInTheDocument();
    expect(screen.getByText("ev_two")).toBeInTheDocument();
  });
});
