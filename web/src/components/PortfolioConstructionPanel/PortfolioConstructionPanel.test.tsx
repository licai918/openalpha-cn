// V2-P5-018. Page ④ in isolation.
//
// The load-bearing test in this file is `renders the contract's invested_weight verbatim`.
// It is the only one that can tell a panel that *renders* the total from one that
// *recomputes* it, and it can only do that because the fixture's weights were chosen to sum
// differently in IEEE-754 than the contract says — see `buildPortfolioConstruction`'s note,
// and the guard in `contractStateLab.test.ts` that keeps that property true.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PortfolioConstructionPanel } from "./PortfolioConstructionPanel";
import type { PanelState } from "../../panelState";
import { describePanelStateContract } from "../../test/panelStateContract";
import { buildPortfolioConstruction } from "../../test/fixtures";
import type { PortfolioConstructionView } from "../../types";

function renderPanel(state: PanelState<PortfolioConstructionView>) {
  return (
    <PortfolioConstructionPanel
      state={state}
      shortlistId="sla_fixture"
      maxPositionWeight="0.7"
      turnoverBudget=""
      onShortlistIdChange={() => {}}
      onMaxPositionWeightChange={() => {}}
      onTurnoverBudgetChange={() => {}}
      onRun={() => {}}
    />
  );
}

describePanelStateContract({
  name: "PortfolioConstructionPanel",
  renderState: renderPanel,
  data: buildPortfolioConstruction(),
  dataText: "000001.SZ",
});

describe("PortfolioConstructionPanel", () => {
  afterEach(cleanup);

  it("renders the contract's invested_weight verbatim rather than summing the weights", () => {
    // THE test. Summed left to right in IEEE-754 the three weights give
    // 0.9999999999999999; the contract says "1". A panel that recomputed the total to
    // display it would put the first string on screen, so asserting the second — and
    // asserting the first is *absent* — separates the two implementations.
    const view = buildPortfolioConstruction();
    const floatSum = String(
      view.targets.reduce((total, target) => total + Number(target.weight), 0),
    );
    expect(floatSum).toBe("0.9999999999999999"); // the fixture really can separate them

    render(renderPanel({ kind: "succeeded", data: view }));
    // Scoped to the term's own value rather than a bare `getByText("1")`: "1" is also a
    // rank, a tier and the total-exposure cap, and a query that matched any of them would
    // pass whatever the panel printed for the total.
    const invested = screen.getByText("已投权重").parentElement?.querySelector("dd");
    expect(invested?.textContent).toBe("1");
    expect(screen.queryByText(floatSum)).not.toBeInTheDocument();
  });

  it("renders each weight as the string the contract sent", () => {
    render(renderPanel({ kind: "succeeded", data: buildPortfolioConstruction() }));
    for (const weight of ["0.7", "0.2", "0.1"]) {
      expect(screen.getAllByText(weight).length).toBeGreaterThan(0);
    }
  });

  it("says the method is a heuristic, in the contract's own words", () => {
    // `method` is a Literal the backend will not let a construction omit, and dropping it
    // here would present rank-and-clamp as an optimiser.
    render(renderPanel({ kind: "succeeded", data: buildPortfolioConstruction() }));
    expect(screen.getByText("heuristic, not optimized")).toBeInTheDocument();
  });

  it("reports unallocated weight instead of letting the holdings table stand for the whole book", () => {
    render(
      renderPanel({
        kind: "degraded",
        reason: "有 0.1 的权重上限吃不下",
        data: buildPortfolioConstruction({
          targets: buildPortfolioConstruction().targets.slice(0, 2),
          invested_weight: "0.9",
          unallocated_weight: "0.1",
        }),
      }),
    );
    expect(screen.getByText("放不下（已作现金）")).toBeInTheDocument();
    expect(screen.getAllByText("0.1").length).toBeGreaterThan(0);
  });

  it("names a cap the turnover budget left breached", () => {
    render(
      renderPanel({
        kind: "degraded",
        reason: "换手预算缩放之后，以下上限仍被突破：max_position_weight",
        data: buildPortfolioConstruction({
          turnover_budget: "0.3",
          turnover_damping: "0.48",
          caps_breached_after_turnover_damping: ["max_position_weight"],
        }),
      }),
    );
    expect(screen.getByText(/仍被突破的上限：max_position_weight/)).toBeInTheDocument();
  });

  it("shows both turnover numbers, so a damped one is not mistaken for the raw one", () => {
    render(
      renderPanel({
        kind: "succeeded",
        data: buildPortfolioConstruction({
          turnover: "0.3",
          turnover_before_budget: "0.62",
          turnover_budget: "0.3",
          turnover_damping: "0.48",
        }),
      }),
    );
    expect(screen.getByText("换手")).toBeInTheDocument();
    expect(screen.getByText("预算前换手")).toBeInTheDocument();
    expect(screen.getByText("0.62")).toBeInTheDocument();
  });

  it("says the industry column is empty rather than leaving a blank cell", () => {
    // `industry_code` is structurally null on the shipped shortlist path. A blank cell would
    // read as "this name has no industry"; the words say the field is not carried.
    render(renderPanel({ kind: "succeeded", data: buildPortfolioConstruction() }));
    expect(screen.getAllByText("无行业字段").length).toBe(3);
  });

  it("renders the backend's own limitations rather than a summary of them", () => {
    render(renderPanel({ kind: "succeeded", data: buildPortfolioConstruction() }));
    expect(
      screen.getByText("no_capacity_liquidity_or_cost_term_enters_a_weight"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("the_policy_is_a_heuristic_and_optimises_nothing"),
    ).toBeInTheDocument();
  });

  it("names the three contract gaps on the page", () => {
    render(renderPanel({ kind: "succeeded", data: buildPortfolioConstruction() }));
    expect(screen.getByText("capacity_reaches_no_portfolio_contract")).toBeInTheDocument();
    expect(screen.getByText("paper_portfolio_has_no_http_face")).toBeInTheDocument();
    expect(screen.getByText("segmented_report_has_no_http_face")).toBeInTheDocument();
  });

  it("draws no paper NAV curve and no segmented table", () => {
    // Absence claimed is absence asserted.
    render(renderPanel({ kind: "succeeded", data: buildPortfolioConstruction() }));
    expect(screen.queryByRole("heading", { name: /净值/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /分段/ })).not.toBeInTheDocument();
  });

  it("says a limit was not declared rather than rendering an empty cell for it", () => {
    // Every field of `PortfolioLimits` is optional on the wire, and "no cap declared" is a
    // materially different statement from "a cap of nothing". A blank cell says neither.
    render(
      renderPanel({
        kind: "succeeded",
        data: buildPortfolioConstruction({
          policy: {
            schema_version: "portfolio-construction-policy/v1",
            tier_weights: ["1"],
            limits: {
              max_position_weight: null,
              max_total_exposure: null,
              min_cash_weight: null,
              max_industry_weight: null,
              turnover_budget: null,
            },
          },
        }),
      }),
    );
    expect(screen.getAllByText("未声明").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("未声明（本面不可执行）")).toBeInTheDocument();
  });

  it("runs the construction from the form", () => {
    const onRun = vi.fn();
    render(
      <PortfolioConstructionPanel
        state={{ kind: "idle" }}
        shortlistId=""
        maxPositionWeight="0.1"
        turnoverBudget=""
        onShortlistIdChange={() => {}}
        onMaxPositionWeightChange={() => {}}
        onTurnoverBudgetChange={() => {}}
        onRun={onRun}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "构建组合" }));
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("reports each field edit to its own handler", () => {
    const onShortlistIdChange = vi.fn();
    const onTurnoverBudgetChange = vi.fn();
    render(
      <PortfolioConstructionPanel
        state={{ kind: "idle" }}
        shortlistId=""
        maxPositionWeight="0.1"
        turnoverBudget=""
        onShortlistIdChange={onShortlistIdChange}
        onMaxPositionWeightChange={() => {}}
        onTurnoverBudgetChange={onTurnoverBudgetChange}
        onRun={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("候选清单编号"), { target: { value: "sla_x" } });
    expect(onShortlistIdChange).toHaveBeenCalledWith("sla_x");
    fireEvent.change(screen.getByLabelText("换手预算（留空表示不设）"), {
      target: { value: "0.4" },
    });
    expect(onTurnoverBudgetChange).toHaveBeenCalledWith("0.4");
  });
});
