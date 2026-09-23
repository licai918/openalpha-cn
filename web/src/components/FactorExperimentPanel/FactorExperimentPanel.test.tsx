// V2-P5-017. Page ③'s detail panel in isolation.
//
// Three of these assert rendering obligations the *backend* states, not preferences of this
// panel's: mark the acceptance step, keep `coverage` beside the statistic it qualifies, and
// do not let a raw-vs-tier survival correlation sit under a cross-factor heading.

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FactorExperimentPanel } from "./FactorExperimentPanel";
import type { PanelState } from "../../panelState";
import { describePanelStateContract } from "../../test/panelStateContract";
import { buildFactorExperiment, buildTierReport } from "../../test/fixtures";
import type { FactorExperimentEnvelope } from "../../types";

function renderPanel(state: PanelState<FactorExperimentEnvelope>) {
  return <FactorExperimentPanel state={state} experimentId="fxp_fixture" />;
}

describePanelStateContract({
  name: "FactorExperimentPanel",
  renderState: renderPanel,
  data: buildFactorExperiment(),
  // The factor's own identity: present whenever the payload is rendered, absent otherwise.
  dataText: "momentum/v1",
});

describe("FactorExperimentPanel", () => {
  afterEach(cleanup);

  it("renders the factor definition rather than only its id", () => {
    render(renderPanel({ kind: "succeeded", data: buildFactorExperiment() }));
    expect(screen.getByText("momentum/v1")).toBeInTheDocument();
    expect(screen.getByText("momentum_reversal")).toBeInTheDocument();
    expect(screen.getByText("越大越好")).toBeInTheDocument();
    // V2-P5-042. Asserted as the **whole** rendered string, qualified by dataset. This read
    // `/close、adj_factor/` and passed for two releases while the page showed
    // `所需字段：[object Object]、[object Object]` — because the fixture supplied
    // `["close","adj_factor"]` to match a `types.ts` that wrongly said `string[]`, so the
    // regex matched a fixture nobody's server had ever sent. A substring regex over a
    // hand-written fixture is exactly as strong as the fixture, which here was zero.
    expect(screen.getByText("所需字段：daily.close、daily.adj_factor")).toBeInTheDocument();
  });

  it("marks the acceptance step so the grid is not six equal rows", () => {
    // `factor_view.ACCEPTANCE_STEP`'s own argument: the artifact "treats all three as equals
    // on purpose" and "a face that printed six equal rows left the reader to know which".
    // The marker must be on the acceptance step and on no other, which is what the second
    // half asserts — a panel that marked every row would pass the first half alone.
    render(renderPanel({ kind: "succeeded", data: buildFactorExperiment() }));
    const marks = screen.getAllByText("（验收判据）");
    expect(marks).toHaveLength(2); // two statistics on the one step
    for (const mark of marks) {
      expect(mark.closest("th")?.textContent).toContain("processed→neutralized");
    }
  });

  it("keeps coverage beside the statistic, so an unmeasured cell is not a zero", () => {
    // A tier that measured nothing carries `mean_ic: null`. Rendered as a blank cell it
    // would be indistinguishable from a measured zero; rendered as `—` beside the coverage
    // code it is a different claim, which is the whole point of the column.
    const neutralized = buildTierReport("neutralized");
    render(
      renderPanel({
        kind: "degraded",
        reason: "neutralized 档的 IC 未测量",
        data: buildFactorExperiment({
          tiers: [
            buildTierReport("raw"),
            buildTierReport("processed"),
            {
              ...neutralized,
              ic: {
                ...neutralized.ic,
                coverage: "insufficient_as_ofs",
                mean_ic: null,
                icir: null,
                measured_count: 0,
              },
            },
          ],
        }),
      }),
    );
    expect(screen.getByText("insufficient_as_ofs")).toBeInTheDocument();
    // The `—` is the null statistic, and it is on screen rather than an empty cell.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("labels the survival correlation as raw-vs-tier and never as factor-vs-factor", () => {
    // The contract gap this panel refuses to paper over: `survival` is the *same* factor at
    // two tiers. A heading reading 因子相关性 over it would be a misreport, so the assertion
    // is on both halves — the honest heading present, the misleading one absent.
    render(renderPanel({ kind: "succeeded", data: buildFactorExperiment() }));
    expect(
      screen.getByRole("heading", { name: /档位存活相关性（同一因子 raw 对本档）/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "因子相关性" })).not.toBeInTheDocument();
    expect(screen.getByText(/这不是因子与因子之间的相关性/)).toBeInTheDocument();
  });

  it("says the raw tier has no baseline instead of rendering an empty row", () => {
    render(renderPanel({ kind: "succeeded", data: buildFactorExperiment() }));
    expect(screen.getByText(/基准档本身，没有可比较的上一档/)).toBeInTheDocument();
  });

  it("names both contract gaps on the page rather than only in a comment", () => {
    // The named-absence half of the row has to reach the user, not just the source file.
    render(renderPanel({ kind: "succeeded", data: buildFactorExperiment() }));
    expect(screen.getByText("ic_decay_curve_reaches_no_http_contract")).toBeInTheDocument();
    expect(
      screen.getByText("cross_factor_correlation_reaches_no_http_contract"),
    ).toBeInTheDocument();
  });

  it("draws no decay curve and shows the single horizon the experiment actually ran at", () => {
    // The other direction of the same gap: absence is claimed, so absence is asserted.
    render(renderPanel({ kind: "succeeded", data: buildFactorExperiment() }));
    expect(screen.queryByRole("heading", { name: /衰减曲线/ })).not.toBeInTheDocument();
    expect(screen.getByText("5 个交易日")).toBeInTheDocument();
  });

  it("renders the other half of every nullable and enum the contract declares", () => {
    // One test over the *weak* end of each field, because the fixture is deliberately the
    // strongest answer and a panel is only half-exercised by it. Every branch here is a real
    // payload: `lower_is_better` is one of two directions, `lookback_sessions` is nullable,
    // both coverage codes are reachable, and `no_baseline` is a verdict the grid emits when
    // the earlier tier's statistic is at or below zero.
    const neutralized = buildTierReport("neutralized");
    const experiment = buildFactorExperiment({
      spec: {
        ...buildFactorExperiment().document.artifact.spec,
        ic: {
          method: "pearson",
          definition: {
            key: "reversal",
            version: 2,
            // A second real family, so the two fixtures do not both stand on one value.
            family: "volatility_liquidity",
            direction: "lower_is_better",
            // V2-P5-042: a `FactorField`, as the server sends it. One field here rather
            // than the fixture's two, so the single-entry join is exercised too.
            required_fields: [{ dataset: "daily", column: "close" }],
            lookback_sessions: null,
          },
        },
      },
      tiers: [
        buildTierReport("raw"),
        buildTierReport("processed", {
          ...buildTierReport("processed"),
          portfolio: {
            ...buildTierReport("processed").portfolio,
            coverage: "insufficient_periods",
            mean_spread: null,
            hit_rate: null,
          },
        }),
        {
          ...neutralized,
          survival: {
            ...neutralized.survival!,
            coverage: "insufficient_as_ofs",
            mean_correlation: null,
            mean_abs_correlation: null,
          },
        },
      ],
      attributions: buildFactorExperiment().document.artifact.attributions.map((cell) =>
        cell.from_tier === "raw" && cell.to_tier === "neutralized"
          ? { ...cell, verdict: "no_baseline" as const, retention: null }
          : cell,
      ),
    });

    render(renderPanel({ kind: "degraded", reason: "多处未测量", data: experiment }));
    expect(screen.getByText("越小越好")).toBeInTheDocument();
    expect(screen.getByText("未声明")).toBeInTheDocument();
    expect(screen.getByText("insufficient_periods")).toBeInTheDocument();
    // `no_baseline` is marked like `not_measured` and unlike `removed`: both mean the
    // question could not be put, which is not the same as a bad answer. Two cells carry it,
    // because the step was overwritten for both of its statistics.
    const noBaseline = screen.getAllByText("no_baseline");
    expect(noBaseline).toHaveLength(2);
    for (const cell of noBaseline) {
      expect(cell).toHaveClass("warning-inline");
    }
    expect(screen.getByText("（insufficient_as_ofs）")).toBeInTheDocument();
  });

  it("says a freshly created experiment was created, not read back", () => {
    // `write` is `"created"` on POST /api/v1/factors/run and `"unchanged"` on the GET. The
    // two are different facts about what the store just did.
    render(
      renderPanel({
        kind: "succeeded",
        data: buildFactorExperiment({}, { write: "created" }),
      }),
    );
    expect(screen.getByText("新建")).toBeInTheDocument();
  });

  it("renders a dash rather than crashing when the two group arrays disagree in length", () => {
    // Defensive, and deliberately so: the mirror cannot enforce that `group_mean_net_returns`
    // and `group_mean_gross_returns` are parallel, and an out-of-range index would otherwise
    // render `undefined`. The contract builds them together, so this is a guard rather than
    // an expected payload — but a guard with no test is a guard nobody has run.
    const raw = buildTierReport("raw");
    render(
      renderPanel({
        kind: "succeeded",
        data: buildFactorExperiment({
          tiers: [
            {
              ...raw,
              portfolio: { ...raw.portfolio, group_mean_gross_returns: [0.1] },
            },
            buildTierReport("processed"),
            buildTierReport("neutralized"),
          ],
        }),
      }),
    );
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it("renders all three tiers in the contract's declared order", () => {
    render(renderPanel({ kind: "succeeded", data: buildFactorExperiment() }));
    const tierTable = screen.getByRole("table", { name: /三档，各自的 IC/ });
    const rowHeaders = within(tierTable)
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent);
    expect(rowHeaders).toEqual(["raw", "processed", "neutralized"]);
  });
});
