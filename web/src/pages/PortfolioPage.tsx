// V2-P5-018. The route container for page ④.
//
// Starts `idle` rather than fetching on mount, and for `DataHealthPage`'s reason:
// `POST /api/v1/portfolio/construct` requires a `shortlist_id`, which is a content address
// of a specific stored answer. A mount-time request would have to invent one — or pick "the
// first shortlist" and weight it — and then report a portfolio for a list the user never
// chose. `idle` is the honest state before the question is put.

import { useState } from "react";

import { constructPortfolio } from "../api/client";
import { PortfolioConstructionPanel } from "../components/PortfolioConstructionPanel/PortfolioConstructionPanel";
import { portfolioConstructionStateFrom } from "../contractState";
import type { PanelState } from "../panelState";
import type { PortfolioConstructionView } from "../types";

/**
 * The three tier weights the policy declares.
 *
 * A constant rather than a form field, and the reason is that the contract validates their
 * *sum*: `PortfolioConstructionPolicy` refuses a tier vector that does not sum to one, with
 * a 422 carrying a list of field errors rather than this app's `{reason, message}` shape.
 * Three free-text boxes would make that the commonest thing a user sees. They are declared
 * here, as strings, so the exactness the wire format protects survives the form too.
 */
const TIER_WEIGHTS = ["0.5", "0.3", "0.2"];

export function PortfolioPage() {
  const [shortlistId, setShortlistId] = useState("");
  const [maxPositionWeight, setMaxPositionWeight] = useState("0.1");
  const [turnoverBudget, setTurnoverBudget] = useState("");
  const [state, setState] = useState<PanelState<PortfolioConstructionView>>({ kind: "idle" });

  const run = async () => {
    // Refused here rather than sent, `DataHealthPage`'s rule: the endpoint answers 422 for a
    // malformed address, and a validation error rendered as a failed construction reads as
    // "the policy is broken" when what happened is "the form was empty".
    if (shortlistId.trim() === "") {
      setState({ kind: "failed", error: "请先填写候选清单编号。" });
      return;
    }

    setState({ kind: "loading" });
    try {
      const view = await constructPortfolio({
        shortlistId: shortlistId.trim(),
        tierWeights: TIER_WEIGHTS,
        maxPositionWeight: maxPositionWeight.trim(),
        maxTotalExposure: "1",
        minCashWeight: "0",
        // An empty box means "no budget declared", which is a different request from a
        // budget of zero — the latter would forbid all trading.
        turnoverBudget: turnoverBudget.trim() === "" ? null : turnoverBudget.trim(),
      });
      setState(portfolioConstructionStateFrom(view));
    } catch (error) {
      setState({
        kind: "failed",
        error: error instanceof Error ? error.message : "组合构建失败",
      });
    }
  };

  return (
    <PortfolioConstructionPanel
      state={state}
      shortlistId={shortlistId}
      maxPositionWeight={maxPositionWeight}
      turnoverBudget={turnoverBudget}
      onShortlistIdChange={setShortlistId}
      onMaxPositionWeightChange={setMaxPositionWeight}
      onTurnoverBudgetChange={setTurnoverBudget}
      onRun={run}
    />
  );
}
