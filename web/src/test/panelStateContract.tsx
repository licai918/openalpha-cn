// V2-P5-019. The panel-state contract, asserted identically against every panel.
//
// This is deliberately one shared suite rather than four hand-written ones. The row's
// finding is that the four panels *diverged* — one used a discriminated union, three used
// ad-hoc `loading`/`error` booleans — so a per-panel test written by hand would let them
// diverge again in exactly the way that produced the defect. Every panel is run through
// the same assertions here; a panel that renders a refusal differently from its siblings
// fails this suite by name.
//
// `V2-P5-020` records that all four panels have a `role="alert"` branch that **no test has
// ever rendered**. That is what `renders a role="alert" ...` below fixes, for all four at
// once: because each panel is now a pure function of a `state` prop, reaching the alert
// branch is passing `{kind: "failed", error: "..."}` — no fetch stub, no error injection.

import { cleanup, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { PANEL_STATE_KINDS, panelData, panelTone, type PanelState } from "../panelState";

export type PanelStateContractOptions<T> = {
  /** Panel name, used in test titles. */
  name: string;
  /** Render the panel in the given state. */
  renderState: (state: PanelState<T>) => ReactElement;
  /** A contract-shaped payload for the data-carrying kinds. */
  data: T;
  /** Text that must be on screen whenever the payload is rendered. */
  dataText: string | RegExp;
};

/** Build one sample per kind for a given payload. */
function samplesFor<T>(data: T): Record<string, PanelState<T>> {
  return {
    idle: { kind: "idle" },
    loading: { kind: "loading" },
    ready: { kind: "ready", data },
    succeeded: { kind: "succeeded", data },
    empty: { kind: "empty", reason: "契约固定的空态原因" },
    degraded: { kind: "degraded", data, reason: "契约固定的降级原因" },
    stale: { kind: "stale", data, reason: "契约固定的过期原因" },
    blocked: { kind: "blocked", reason: "契约固定的拒绝原因" },
    failed: { kind: "failed", error: "契约固定的失败原因" },
  };
}

export function describePanelStateContract<T>(options: PanelStateContractOptions<T>): void {
  const { name, renderState, data, dataText } = options;
  const samples = samplesFor(data);

  describe(`${name} obeys the panel-state contract`, () => {
    afterEach(cleanup);

    it("renders every one of the nine kinds without throwing", () => {
      // The weakest assertion here, and the one that would have caught the original
      // divergence earliest: a panel that does not handle a kind at all used to render
      // nothing and pass. `renders something` below is what makes that fail.
      for (const kind of PANEL_STATE_KINDS) {
        expect(() => render(renderState(samples[kind]))).not.toThrow();
        cleanup();
      }
    });

    for (const kind of ["blocked", "failed"] as const) {
      it(`renders a role="alert" carrying the message for ${kind}`, () => {
        // Never rendered before this row: V2-P5-020 names this exact branch on all four
        // panels. `getByRole` throws if the branch is missing, so this cannot pass vacuously.
        render(renderState(samples[kind]));
        const alert = screen.getByRole("alert");
        expect(alert).toBeInTheDocument();
        expect(alert.textContent?.trim()).toBeTruthy();
        expect(alert).toHaveTextContent(
          kind === "blocked" ? "契约固定的拒绝原因" : "契约固定的失败原因",
        );
      });

      it(`renders no payload for ${kind}: a refusal is never dressed as a result`, () => {
        // The defect the row exists to prevent, asserted on screen rather than on the type.
        // `panelData` returning null is a unit fact; this is the rendered consequence.
        expect(panelData(samples[kind])).toBeNull();
        render(renderState(samples[kind]));
        expect(screen.queryByText(dataText)).not.toBeInTheDocument();
      });
    }

    for (const kind of ["ready", "succeeded", "degraded", "stale"] as const) {
      it(`renders the payload for ${kind}`, () => {
        render(renderState(samples[kind]));
        expect(screen.getByText(dataText)).toBeInTheDocument();
      });
    }

    for (const kind of ["degraded", "stale"] as const) {
      it(`qualifies ${kind} with a visible notice instead of passing it off as a plain success`, () => {
        // The half that separates `degraded`/`stale` from `ready`/`succeeded`. Without it
        // the union would have four data kinds that render identically — four names for
        // one behaviour, which is the "assertion cannot separate the two answers" defect.
        render(renderState(samples[kind]));
        const notice = screen.getByRole("status");
        expect(notice).toHaveTextContent(
          kind === "degraded" ? "契约固定的降级原因" : "契约固定的过期原因",
        );
        // ...and it is a notice, not an alert: the data is still trustworthy enough to show.
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(panelTone(samples[kind])).toBe("warning");
      });
    }

    it("renders the backend's own words for empty, not a generic placeholder", () => {
      render(renderState(samples.empty));
      expect(screen.getByText("契约固定的空态原因")).toBeInTheDocument();
    });

    it("renders a busy indicator for loading and no payload", () => {
      render(renderState(samples.loading));
      expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
      expect(screen.queryByText(dataText)).not.toBeInTheDocument();
    });

    it("renders something for every kind — no kind produces a blank panel", () => {
      // The generalisation of the row: whatever state a panel is in, the user is told
      // something. A kind that renders an empty <section> is the "error rendered as an
      // empty success" defect in its purest form.
      for (const kind of PANEL_STATE_KINDS) {
        const { container } = render(renderState(samples[kind]));
        const panel = container.querySelector("section");
        expect(panel, `${name}/${kind} rendered no panel`).not.toBeNull();
        expect(
          (panel?.textContent ?? "").trim().length,
          `${name}/${kind} rendered a blank panel`,
        ).toBeGreaterThan(0);
        cleanup();
      }
    });
  });
}
