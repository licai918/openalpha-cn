// V2-P5-020. The one component every panel delegates its refusal rendering to, rendered
// in isolation.
//
// V2-P5-019 made `PanelNotice` the single place `role="alert"` is emitted, and the four
// panels' contract suites reach it through them. That gave it 100% line coverage without a
// single test of its own — the most load-bearing component in the alert story was the one
// module under `src/components/` with no co-located test, and the coverage report said
// nothing because four other files were exercising it on the way past. Coverage measures
// execution, not assertion; these are the assertions.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PANEL_STATE_KINDS, panelTone, type PanelState } from "../../panelState";
import { PanelNotice } from "./PanelNotice";

const IDLE_TEXT = "面板自己的空闲文案";
const REASON = "后端给出的具体原因";

function statesByKind(): Record<string, PanelState<{ id: string }>> {
  const data = { id: "payload" };
  return {
    idle: { kind: "idle" },
    loading: { kind: "loading" },
    ready: { kind: "ready", data },
    succeeded: { kind: "succeeded", data },
    empty: { kind: "empty", reason: REASON },
    degraded: { kind: "degraded", data, reason: REASON },
    stale: { kind: "stale", data, reason: REASON },
    blocked: { kind: "blocked", reason: REASON },
    failed: { kind: "failed", error: REASON },
  };
}

describe("PanelNotice", () => {
  it("emits role=alert for exactly the kinds panelTone calls alert", () => {
    // Pinned to `panelTone` rather than to a literal list of kinds: the two must agree,
    // and asserting against a second hand-written list would let them drift together.
    const samples = statesByKind();
    for (const kind of PANEL_STATE_KINDS) {
      const { unmount } = render(<PanelNotice state={samples[kind]} idleText={IDLE_TEXT} />);
      const alerts = screen.queryAllByRole("alert");
      expect(alerts.length, `${kind} should ${panelTone(samples[kind]) === "alert" ? "" : "not "}be an alert`).toBe(
        panelTone(samples[kind]) === "alert" ? 1 : 0
      );
      unmount();
    }
  });

  it("emits role=status for exactly the kinds panelTone calls warning", () => {
    const samples = statesByKind();
    for (const kind of PANEL_STATE_KINDS) {
      const { unmount } = render(<PanelNotice state={samples[kind]} idleText={IDLE_TEXT} />);
      expect(screen.queryAllByRole("status").length, `${kind}`).toBe(
        panelTone(samples[kind]) === "warning" ? 1 : 0
      );
      unmount();
    }
  });

  it("renders the refusal's own words verbatim rather than a generic message", () => {
    render(<PanelNotice state={{ kind: "blocked", reason: REASON }} idleText={IDLE_TEXT} />);
    expect(screen.getByRole("alert")).toHaveTextContent(REASON);
  });

  it("renders the failure's own words verbatim", () => {
    render(<PanelNotice state={{ kind: "failed", error: REASON }} idleText={IDLE_TEXT} />);
    expect(screen.getByRole("alert")).toHaveTextContent(REASON);
  });

  it("uses the panel's own copy for idle, not a shared string", () => {
    render(<PanelNotice state={{ kind: "idle" }} idleText={IDLE_TEXT} />);
    expect(screen.getByText(IDLE_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("marks the loading skeleton busy and shows no message", () => {
    const { container } = render(
      <PanelNotice state={{ kind: "loading" }} idleText={IDLE_TEXT} />
    );
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.queryByText(IDLE_TEXT)).toBeNull();
  });

  it("renders nothing at all for ready and succeeded, leaving the panel's data alone", () => {
    for (const state of [
      { kind: "ready" as const, data: { id: "payload" } },
      { kind: "succeeded" as const, data: { id: "payload" } },
    ]) {
      const { container, unmount } = render(
        <PanelNotice state={state} idleText={IDLE_TEXT} />
      );
      expect(container.innerHTML, state.kind).toBe("");
      unmount();
    }
  });

  it("qualifies degraded and stale without hiding the data they carry", () => {
    for (const state of [
      { kind: "degraded" as const, data: { id: "payload" }, reason: REASON },
      { kind: "stale" as const, data: { id: "payload" }, reason: REASON },
    ]) {
      const { unmount } = render(<PanelNotice state={state} idleText={IDLE_TEXT} />);
      expect(screen.getByRole("status"), state.kind).toHaveTextContent(REASON);
      // role="status", not role="alert": the data beside it is real.
      expect(screen.queryByRole("alert")).toBeNull();
      unmount();
    }
  });

  it("states the reason for an empty answer instead of rendering a blank panel", () => {
    render(<PanelNotice state={{ kind: "empty", reason: REASON }} idleText={IDLE_TEXT} />);
    expect(screen.getByText(REASON)).toBeInTheDocument();
    expect(screen.queryByText(IDLE_TEXT)).toBeNull();
  });
});
