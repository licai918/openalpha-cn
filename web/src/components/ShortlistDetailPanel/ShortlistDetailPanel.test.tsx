// V2-P5-016. Page ②'s detail view in isolation.
//
// The sharpest test in this file is `renders the admitted list and never the funnel's own`.
// `shortlistStateFrom` already refuses to hand a refused answer to this component, so that
// test is not covering a reachable user-facing bug today — it is pinning the *field choice*,
// which is the thing that would silently regress. `funnel.shortlist` and `admitted` hold
// overlapping data with different meanings, and an edit that reached for the wrong one would
// leave every state test green while putting names off a refused list on screen.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ShortlistDetailPanel } from "./ShortlistDetailPanel";
import type { PanelState } from "../../panelState";
import { describePanelStateContract } from "../../test/panelStateContract";
import { buildShortlistAnswer } from "../../test/fixtures";
import type { ShortlistAnswer } from "../../types";

function renderPanel(state: PanelState<ShortlistAnswer>) {
  return <ShortlistDetailPanel state={state} shortlistId="sl_under_test" />;
}

describePanelStateContract({
  name: "ShortlistDetailPanel",
  renderState: renderPanel,
  data: buildShortlistAnswer(),
  // A run manifest id: only ever rendered from an admitted candidate's evidence chain.
  dataText: "run_aaa",
});

describe("ShortlistDetailPanel", () => {
  afterEach(cleanup);

  it("renders the admitted list and never the funnel's own list", () => {
    // The two lists are made to disagree here. `funnel.shortlist` is what the ranking
    // computed; `admitted` is what the gate let out. Only the second is a result.
    const answer = buildShortlistAnswer({
      funnel: {
        ...buildShortlistAnswer().funnel,
        shortlist: [
          { subject: "000001.SZ", rank: 1, score: 2.31 },
          { subject: "999999.SZ", rank: 2, score: 1.88 },
        ],
      },
      admitted: [
        {
          subject: "000001.SZ",
          rank: 1,
          score: 2.31,
          direction: "bullish",
          confidence: 0.72,
          run_manifest_id: "run_aaa",
          risk_flags: [],
        },
      ],
    });
    render(renderPanel({ kind: "succeeded", data: answer }));

    expect(screen.getByText("000001.SZ")).toBeInTheDocument();
    expect(
      screen.queryByText("999999.SZ"),
      "a name the gate did not admit is on screen as a candidate",
    ).not.toBeInTheDocument();
  });

  it("shows each candidate's score, confidence, risk flags and evidence chain", () => {
    render(renderPanel({ kind: "succeeded", data: buildShortlistAnswer() }));
    expect(screen.getByText("2.3100")).toBeInTheDocument();
    expect(screen.getByText("72.00%")).toBeInTheDocument();
    expect(screen.getByText("run_aaa")).toBeInTheDocument();
  });

  it("names a candidate's risk flags rather than only counting them", () => {
    const base = buildShortlistAnswer();
    render(
      renderPanel({
        kind: "degraded",
        data: buildShortlistAnswer({
          admitted: [{ ...base.admitted![0], risk_flags: ["st_stock", "thin_liquidity"] }],
        }),
        reason: "携带风险标记",
      }),
    );
    expect(screen.getByText("st_stock、thin_liquidity")).toBeInTheDocument();
  });

  it("reports the unnamed untradeable residual, not just the names it was given", () => {
    // `untradeable` is capped server-side by MAX_NAMED_UNTRADEABLE. A page that rendered
    // only the array would under-report by exactly `untradeable_not_named`.
    render(
      renderPanel({
        kind: "degraded",
        data: buildShortlistAnswer({
          funnel: { ...buildShortlistAnswer().funnel, untradeable_not_named: 37 },
        }),
        reason: "未列名",
      }),
    );
    expect(screen.getByText(/另有 37 只不可交易标的未被逐一列名/)).toBeInTheDocument();
    // …and the named one is still there beside it.
    expect(screen.getByText(/600519\.SH/)).toBeInTheDocument();
  });

  it("states the universe and scoring basis this list was cut from (S72)", () => {
    render(renderPanel({ kind: "succeeded", data: buildShortlistAnswer() }));
    const declaration = screen.getByRole("region", { name: "股票池与打分口径" });
    expect(declaration).toHaveTextContent("processed");
    expect(declaration).toHaveTextContent("XSHG");
    expect(declaration).toHaveTextContent("300");
    expect(declaration).toHaveTextContent("momentum_20d/v1");
  });

  it("shows both sides of every bar, on a list that cleared as well as one that did not", () => {
    // "A list that scraped over a bar and one that sailed over it are different facts."
    render(
      renderPanel({
        kind: "succeeded",
        data: buildShortlistAnswer({
          blocks: [
            {
              code: "tradable_ratio_below_minimum",
              detail: "可交易比例接近下限。",
              measured: 0.81,
              required: 0.8,
            },
          ],
        }),
      }),
    );
    expect(screen.getByText(/实测 0\.81 \/ 要求 0\.8/)).toBeInTheDocument();
  });

  it("renders an absent transform and neutralisation as 无, not as a blank cell", () => {
    // `declaration.neutralization` is always null on this face today (`run_shortlist`
    // refuses the neutralised tier before anything is read), and `transform` is null on the
    // raw tier. A blank definition list cell would read as "not loaded" rather than "none".
    render(
      renderPanel({
        kind: "succeeded",
        data: buildShortlistAnswer({
          declaration: {
            ...buildShortlistAnswer().declaration,
            transform: null,
            neutralization: null,
          },
        }),
      }),
    );
    expect(screen.getAllByText("无").length).toBeGreaterThanOrEqual(2);
  });

  it("says the cross section had nothing untradeable when that is the case", () => {
    render(
      renderPanel({
        kind: "succeeded",
        data: buildShortlistAnswer({
          funnel: {
            ...buildShortlistAnswer().funnel,
            untradeable: [],
            untradeable_not_named: 0,
          },
        }),
      }),
    );
    expect(screen.getByText("本次横截面没有被剔除的不可交易标的。")).toBeInTheDocument();
  });

  it("gives unresearched candidates a section of their own", () => {
    render(
      renderPanel({
        kind: "degraded",
        data: buildShortlistAnswer({ unresearched: ["000003.SZ"] }),
        reason: "证据链未闭合",
      }),
    );
    expect(
      screen.getByRole("region", { name: "证据链未闭合" }),
    ).toHaveTextContent("000003.SZ");
  });

  it("always shows which shortlist it is, even before the answer arrives", () => {
    render(renderPanel({ kind: "loading" }));
    expect(screen.getByText("sl_under_test")).toBeInTheDocument();
  });
});
