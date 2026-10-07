// V2-P5-016. Page ②'s index in isolation.

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { ShortlistIndexPanel } from "./ShortlistIndexPanel";
import type { PanelState } from "../../panelState";
import { describePanelStateContract } from "../../test/panelStateContract";

function renderPanel(state: PanelState<string[]>) {
  // A router is required because every row is a `<Link>`. `MemoryRouter` keeps the panel
  // testable in isolation without a real history.
  return (
    <MemoryRouter>
      <ShortlistIndexPanel state={state} />
    </MemoryRouter>
  );
}

describePanelStateContract({
  name: "ShortlistIndexPanel",
  renderState: renderPanel,
  data: ["sl_aaa", "sl_bbb"],
  dataText: "sl_aaa",
});

describe("ShortlistIndexPanel", () => {
  afterEach(cleanup);

  it("links each content address to its own detail location", () => {
    // The row's real deliverable: the server's content address and the app's URL are the
    // same string, so an answer can be handed to someone as a link.
    render(renderPanel({ kind: "ready", data: ["sl_aaa", "sl_bbb"] }));
    expect(screen.getByRole("link", { name: "sl_aaa" })).toHaveAttribute(
      "href",
      "/shortlists/sl_aaa",
    );
    expect(screen.getByRole("link", { name: "sl_bbb" })).toHaveAttribute(
      "href",
      "/shortlists/sl_bbb",
    );
  });

  it("says the store is empty in the backend's terms rather than showing a bare list", () => {
    render(renderPanel({ kind: "empty", reason: "本地还没有任何已存的候选清单。" }));
    expect(screen.getByText("本地还没有任何已存的候选清单。")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
