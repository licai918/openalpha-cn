// V2-P5-017. Page ③'s experiment index in isolation.

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { FactorExperimentIndexPanel } from "./FactorExperimentIndexPanel";
import type { PanelState } from "../../panelState";
import { describePanelStateContract } from "../../test/panelStateContract";

function renderPanel(state: PanelState<string[]>) {
  return (
    <MemoryRouter>
      <FactorExperimentIndexPanel state={state} />
    </MemoryRouter>
  );
}

describePanelStateContract({
  name: "FactorExperimentIndexPanel",
  renderState: renderPanel,
  data: ["fxp_aaa", "fxp_bbb"],
  dataText: "fxp_aaa",
});

describe("FactorExperimentIndexPanel", () => {
  afterEach(cleanup);

  it("links each sealed experiment to its own detail location", () => {
    render(renderPanel({ kind: "ready", data: ["fxp_aaa", "fxp_bbb"] }));
    expect(screen.getByRole("link", { name: "fxp_aaa" })).toHaveAttribute(
      "href",
      "/factor-lab/fxp_aaa",
    );
    expect(screen.getByRole("link", { name: "fxp_bbb" })).toHaveAttribute(
      "href",
      "/factor-lab/fxp_bbb",
    );
  });

  it("builds the href through ROUTES rather than by interpolation", () => {
    // The silent failure `routes.ts` exists for, asserted where a user would meet it: an id
    // with a slash must not produce a *working* link to another address.
    render(renderPanel({ kind: "ready", data: ["fxp_a/b"] }));
    expect(screen.getByRole("link", { name: "fxp_a/b" })).toHaveAttribute(
      "href",
      "/factor-lab/fxp_a%2Fb",
    );
  });

  it("says the store is empty in the backend's terms rather than showing a bare list", () => {
    render(renderPanel({ kind: "empty", reason: "本地还没有任何已封存的因子实验。" }));
    expect(screen.getByText("本地还没有任何已封存的因子实验。")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
