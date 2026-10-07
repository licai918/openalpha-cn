// V2-P5-017 / V2-P5-018. The three classifiers pages ③ and ④ are decided by.
//
// Written before the implementations and run first against a deliberately naive one, the
// way `V2-P5-015`/`016` were: the naive reading of each of these payloads is a *plausible*
// reading, which is exactly why it needs a fixture that separates it from the right one.
// The three naive readings, and what each gets wrong, are recorded beside their tests.
//
// The headline is `factorExperimentStateFrom`. Its defect is not invented here — the
// backend names it, in `factor_view.everything_is_unmeasured`'s docstring: an experiment
// whose grid is entirely `not_measured` "also exits 0 and also answers 200, and a reader
// (or a CI step) that greps for `removed`, finds nothing and stops has concluded 'this
// factor survived neutralisation' about two tiers that never computed a number." The CLI
// prints a named line for that case and `--json` prints it on stderr — **the HTTP body
// carries no such field**, so a browser is the one face that gets no warning at all unless
// it recomputes the property. That recomputation is what these tests pin.

import { describe, expect, it } from "vitest";

import {
  FACTOR_LAB_CONTRACT_GAPS,
  PORTFOLIO_CONTRACT_GAPS,
  factorExperimentStateFrom,
  portfolioConstructionStateFrom,
  predictionRegisterStateFrom,
} from "./contractState";
import { panelData } from "./panelState";
import {
  buildAttribution,
  buildAttributionGrid,
  buildFactorExperiment,
  buildPortfolioConstruction,
  buildPredictionEntry,
  buildPredictionIndex,
  buildTierReport,
} from "./test/fixtures";
import type { FactorTier, FactorTierAttribution } from "./types";

/** The six cells with one step's pair overwritten — the shape a real refusal takes. */
function gridWithStep(
  from: FactorTier,
  to: FactorTier,
  overrides: Partial<FactorTierAttribution>,
): FactorTierAttribution[] {
  return buildAttributionGrid().map((cell) =>
    cell.from_tier === from && cell.to_tier === to ? { ...cell, ...overrides } : cell,
  );
}

describe("factorExperimentStateFrom", () => {
  it("calls a fully measured three-tier experiment a success", () => {
    const state = factorExperimentStateFrom(buildFactorExperiment());
    expect(state.kind).toBe("succeeded");
    expect(panelData(state)).not.toBeNull();
  });

  it("refuses to call an all-not_measured grid anything but degraded", () => {
    // THE row's headline. Every cell `not_measured`, every tier still present, HTTP 200.
    // The naive reading — "no cell says `removed`, so the factor survived neutralisation" —
    // is the one the backend's own acceptance review called the most dangerous thing on
    // this face. A classifier that greps for a bad verdict answers `succeeded` here.
    const state = factorExperimentStateFrom(
      buildFactorExperiment({
        attributions: buildAttributionGrid().map((cell) => ({
          ...cell,
          from_value: null,
          to_value: null,
          retention: null,
          verdict: "not_measured" as const,
        })),
      }),
    );
    expect(state.kind).toBe("degraded");
    // Asserted on the contract's own verdict word rather than on a phrase of the UI's, so
    // rewording the sentence cannot quietly turn this into an assertion about nothing.
    expect(state.kind === "degraded" && state.reason).toContain("not_measured");
  });

  it("degrades when the acceptance step alone is unmeasured, though five cells survive", () => {
    // The separation that a "some cell survives" reading cannot make. `ACCEPTANCE_STEP` is
    // ("processed", "neutralized") in factor_view.py, and factor_view says in as many words
    // that "a face that printed six equal rows left the reader to know which". Here the two
    // acceptance cells are `not_measured` while the other four say `survives`: a classifier
    // that polls the grid for any survivor, or that reports the majority verdict, answers
    // `succeeded` about the one step the acceptance criterion is actually decided on.
    const state = factorExperimentStateFrom(
      buildFactorExperiment({
        attributions: gridWithStep("processed", "neutralized", {
          from_value: null,
          to_value: null,
          retention: null,
          verdict: "not_measured",
        }),
      }),
    );
    expect(state.kind).toBe("degraded");
    expect(state.kind === "degraded" && state.reason).toContain("processed→neutralized");
  });

  it("does not degrade an experiment whose acceptance step measured and said `removed`", () => {
    // The other direction, so the classifier cannot be satisfied by degrading on any verdict
    // it dislikes. `removed` is a *finding* — the report did its job and the answer is that
    // neutralisation destroyed the statistic. Degrading it would make "we measured, and the
    // news is bad" indistinguishable from "we could not measure", which is the whole
    // distinction this row is about.
    const state = factorExperimentStateFrom(
      buildFactorExperiment({
        attributions: gridWithStep("processed", "neutralized", {
          retention: 0.02,
          verdict: "removed",
        }),
      }),
    );
    expect(state.kind).toBe("succeeded");
  });

  it("degrades when a tier row measured nothing, even with a full grid", () => {
    // `coverage` before the statistic — `panelHealthStateFrom`'s rule and `replayStateFrom`'s.
    // `mean_ic` is `null` here because nothing was measured, not because the factor scored
    // zero, and a page that printed an empty cell would show those as the same thing.
    const state = factorExperimentStateFrom(
      buildFactorExperiment({
        tiers: [
          buildTierReport("raw"),
          buildTierReport("processed"),
          buildTierReport("neutralized", {
            ic: {
              ...buildTierReport("neutralized").ic,
              coverage: "insufficient_as_ofs",
              mean_ic: null,
              icir: null,
              measured_count: 0,
            },
          }),
        ],
      }),
    );
    expect(state.kind).toBe("degraded");
    expect(state.kind === "degraded" && state.reason).toContain("neutralized");
  });

  it("calls a document carrying no tiers empty rather than a pass", () => {
    const state = factorExperimentStateFrom(buildFactorExperiment({ tiers: [] }));
    expect(state.kind).toBe("empty");
  });

  it("names the two things the contract cannot answer, and does not invent them", () => {
    // The named-absence half of the row, as data rather than as a comment: decay has no
    // field on any HTTP body, and the only correlation that ships is raw-vs-tier for one
    // factor rather than factor-against-factor.
    const codes = FACTOR_LAB_CONTRACT_GAPS.map((gap) => gap.code);
    expect(codes).toContain("ic_decay_curve_reaches_no_http_contract");
    expect(codes).toContain("cross_factor_correlation_reaches_no_http_contract");
    for (const gap of FACTOR_LAB_CONTRACT_GAPS) {
      expect(gap.detail.length, gap.code).toBeGreaterThan(60);
    }
  });
});

describe("predictionRegisterStateFrom", () => {
  it("calls a register of forward predictions ready", () => {
    const state = predictionRegisterStateFrom(buildPredictionIndex());
    expect(state.kind).toBe("ready");
  });

  it("degrades a backfill rather than reporting it as out-of-sample evidence", () => {
    // The naive reading is `scored_count > 0 → ready`, and it is wrong for a reason the
    // contract states: a `backfill` "proves anything at all about foresight" — nothing.
    // A recomputation rendered beside forward predictions, with the same score column and
    // no qualification, is a backtest presented as a track record.
    const state = predictionRegisterStateFrom(
      buildPredictionIndex([
        buildPredictionEntry({
          record_id: "prd_backfill",
          standing: "backfill",
          scored_count: 288,
        }),
      ]),
    );
    expect(state.kind).toBe("degraded");
    expect(state.kind === "degraded" && state.reason).toContain("backfill");
  });

  it("degrades an unwitnessed record too, and says which records", () => {
    const state = predictionRegisterStateFrom(
      buildPredictionIndex([
        buildPredictionEntry({ record_id: "prd_ok" }),
        buildPredictionEntry({ record_id: "prd_late", standing: "unwitnessed" }),
      ]),
    );
    expect(state.kind).toBe("degraded");
    expect(state.kind === "degraded" && state.reason).toContain("prd_late");
    // ...and not the forward one, so the reason is a list of the affected and not a blanket.
    expect(state.kind === "degraded" && state.reason).not.toContain("prd_ok");
  });

  it("calls an empty register empty, not ready", () => {
    expect(predictionRegisterStateFrom(buildPredictionIndex([])).kind).toBe("empty");
  });
});

describe("portfolioConstructionStateFrom", () => {
  it("calls a clean construction a success", () => {
    expect(portfolioConstructionStateFrom(buildPortfolioConstruction()).kind).toBe("succeeded");
  });

  it("degrades a construction whose turnover budget left a cap breached", () => {
    // The naive reading is "there are targets, so we have a portfolio". The contract has a
    // whole limitation about this case — `the_turnover_budget_can_leave_a_cap_breached_and_
    // says_so_instead_of_retrimming` — so the weights on screen genuinely violate a limit
    // the user declared. Rendering them unqualified reports a policy as satisfied when the
    // backend has explicitly said it is not.
    const state = portfolioConstructionStateFrom(
      buildPortfolioConstruction({
        turnover_budget: "0.3",
        turnover_damping: "0.48",
        caps_breached_after_turnover_damping: ["max_position_weight"],
      }),
    );
    expect(state.kind).toBe("degraded");
    expect(state.kind === "degraded" && state.reason).toContain("max_position_weight");
  });

  it("degrades when weight could not be placed and became cash", () => {
    // `unallocated_weight` is the weight the caps would not take. V2-P5-001 chose to report
    // it rather than smear it onto the last name, so a page that renders only `targets`
    // under-reports by exactly this number.
    const state = portfolioConstructionStateFrom(
      buildPortfolioConstruction({
        targets: buildPortfolioConstruction().targets.slice(0, 2),
        invested_weight: "0.9",
        unallocated_weight: "0.1",
      }),
    );
    expect(state.kind).toBe("degraded");
    expect(state.kind === "degraded" && state.reason).toContain("0.1");
  });

  it("does not read a decimal-string zero as non-zero however it is spelled", () => {
    // The counter-test to the one above: `unallocated_weight` is a Decimal rendered as a
    // string, and Python renders an exact zero as "0", "0.00" or "0E-10" depending on the
    // arithmetic that produced it. A truthiness check (`if (view.unallocated_weight)`)
    // degrades all three, because every one of them is a non-empty string.
    for (const zero of ["0", "0.00", "0E-10", "-0"]) {
      const state = portfolioConstructionStateFrom(
        buildPortfolioConstruction({ unallocated_weight: zero }),
      );
      expect(state.kind, `unallocated_weight=${zero}`).toBe("succeeded");
    }
  });

  it("calls a construction with no targets empty", () => {
    expect(
      portfolioConstructionStateFrom(
        buildPortfolioConstruction({ targets: [], invested_weight: "0", cash_weight: "1" }),
      ).kind,
    ).toBe("empty");
  });

  it("names the three things no portfolio contract can answer", () => {
    const codes = PORTFOLIO_CONTRACT_GAPS.map((gap) => gap.code);
    expect(codes).toContain("capacity_reaches_no_portfolio_contract");
    expect(codes).toContain("paper_portfolio_has_no_http_face");
    expect(codes).toContain("segmented_report_has_no_http_face");
    for (const gap of PORTFOLIO_CONTRACT_GAPS) {
      expect(gap.detail.length, gap.code).toBeGreaterThan(60);
    }
  });
});

describe("the fixtures can actually separate the two answers", () => {
  it("has a weight triple whose float sum differs from the contract's invested_weight", () => {
    // Guards the guard. This is the property `PortfolioConstructionPanel.test.tsx` relies on
    // to catch a panel that recomputes the total, and the first triple written here
    // (0.4/0.35/0.25) did **not** have it — it sums to exactly 1 in IEEE-754, so the
    // assertion would have been present and unable to fail. If someone edits the fixture
    // back to a float-exact triple, this goes red instead of the panel test going quietly
    // vacuous.
    const view = buildPortfolioConstruction();
    const summed = view.targets.reduce((total, target) => total + Number(target.weight), 0);
    expect(String(summed)).not.toBe(view.invested_weight);
    expect(view.invested_weight).toBe("1");
  });

  it("builds the six attribution cells in the contract's own order", () => {
    // `FactorExperimentArtifact` requires exactly `ATTRIBUTION_CELL_ORDER`, step-major and
    // statistic-minor. A fixture in another order is a document the backend would refuse to
    // seal, and a classifier passing against it proves nothing.
    expect(
      buildAttributionGrid().map((cell) => `${cell.from_tier}→${cell.to_tier}/${cell.statistic}`),
    ).toEqual([
      "raw→processed/mean_ic",
      "raw→processed/mean_spread",
      "processed→neutralized/mean_ic",
      "processed→neutralized/mean_spread",
      "raw→neutralized/mean_ic",
      "raw→neutralized/mean_spread",
    ]);
    expect(buildAttribution("raw", "processed", "mean_ic").verdict).toBe("survives");
  });
});
