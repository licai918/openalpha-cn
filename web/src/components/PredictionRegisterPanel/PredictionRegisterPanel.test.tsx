// V2-P5-017. Page ③'s prediction register in isolation.
//
// The headline assertion is the one the contract asks for in as many words: a face that
// prints `standing` and stops "turns a local-first bookkeeping fact into what reads like an
// attestation, and a column in a table does that at least as fast as a field in a document".

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PredictionRegisterPanel } from "./PredictionRegisterPanel";
import type { PanelState } from "../../panelState";
import { describePanelStateContract } from "../../test/panelStateContract";
import { buildPredictionEntry, buildPredictionIndex } from "../../test/fixtures";
import type { PredictionIndex } from "../../types";

function renderPanel(state: PanelState<PredictionIndex>) {
  return <PredictionRegisterPanel state={state} />;
}

describePanelStateContract({
  name: "PredictionRegisterPanel",
  renderState: renderPanel,
  data: buildPredictionIndex(),
  // The model name rather than the record id: the id is deliberately rendered twice (once in
  // the table, once on its standing note), and the shared contract's `getByText` requires a
  // single match. Picking a field that appears exactly once keeps the shared assertion about
  // "the payload reached the screen" rather than about how many times.
  dataText: "cross-sectional-rank",
});

describe("PredictionRegisterPanel", () => {
  afterEach(cleanup);

  it("renders what a standing does NOT prove, not only what it is", () => {
    // The obligation the serialiser put the two sentences on every row to create. Asserted
    // on a `forward` record on purpose: the tempting shortcut is to show the caveat only for
    // the bad standings, and `forward`'s own `does_not_prove` half is the one that says
    // predicted_at is uncheckable. A panel that qualified only backfills passes every other
    // test in this file and fails this one.
    render(renderPanel({ kind: "ready", data: buildPredictionIndex() }));
    expect(screen.getByText(/不证明：/)).toBeInTheDocument();
    expect(screen.getByText(/predicted_at is whatever the caller/)).toBeInTheDocument();
  });

  it("renders the contract's own sentences rather than a paraphrase", () => {
    const index = buildPredictionIndex([
      buildPredictionEntry({
        standing_does_not_prove: "一句只有契约会说的话，用来证明这里没有改写。",
      }),
    ]);
    render(renderPanel({ kind: "ready", data: index }));
    expect(
      screen.getByText(/一句只有契约会说的话，用来证明这里没有改写。/),
    ).toBeInTheDocument();
  });

  it("marks a backfill visibly rather than putting it in the same column style as a forward", () => {
    render(
      renderPanel({
        kind: "degraded",
        reason: "以下记录不是 forward 立场",
        data: buildPredictionIndex([
          buildPredictionEntry({ record_id: "prd_backfill", standing: "backfill" }),
        ]),
      }),
    );
    const cell = screen.getByText(/backfill（回溯重算）/);
    expect(cell).toHaveClass("warning-inline");
  });

  it("does not mark a forward standing as a warning", () => {
    // The counter-assertion, so "mark everything" cannot satisfy the one above.
    render(renderPanel({ kind: "ready", data: buildPredictionIndex() }));
    const cell = screen.getByText(/forward（先于结果可知而持有）/);
    expect(cell).not.toHaveClass("warning-inline");
  });

  it("shows scored against offered, so an abstaining batch is not a full one", () => {
    render(
      renderPanel({
        kind: "ready",
        data: buildPredictionIndex([
          buildPredictionEntry({ offered_count: 300, scored_count: 12 }),
        ]),
      }),
    );
    expect(screen.getByText("12 / 300")).toBeInTheDocument();
  });

  it("says the register is empty in the backend's terms", () => {
    render(
      renderPanel({
        kind: "empty",
        reason: "本地还没有任何已登记的预测，因而没有可读的样本外记录。",
      }),
    );
    expect(
      screen.getByText("本地还没有任何已登记的预测，因而没有可读的样本外记录。"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
