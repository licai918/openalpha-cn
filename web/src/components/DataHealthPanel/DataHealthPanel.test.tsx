// V2-P5-015. Page ① in isolation.
//
// The shared panel-state contract runs first, so this panel is held to exactly the same
// nine-kind obligations as the four `V2-P5-019` converted. The tests after it are the ones
// specific to a data-health page: every one of them is about a fact that a report can carry
// while still *looking* fine, which is the only class of defect this page exists to catch.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DataHealthPanel } from "./DataHealthPanel";
import type { PanelState } from "../../panelState";
import { describePanelStateContract } from "../../test/panelStateContract";
import { buildPanelHealthReport } from "../../test/fixtures";
import type { PanelHealthReport } from "../../types";

function renderPanel(state: PanelState<PanelHealthReport>) {
  return (
    <DataHealthPanel
      state={state}
      datasets="index_daily"
      years="2026"
      asOf="2026-07-24T10:00"
      exchange="XSHG"
      onDatasetsChange={vi.fn()}
      onYearsChange={vi.fn()}
      onAsOfChange={vi.fn()}
      onExchangeChange={vi.fn()}
      onRun={vi.fn()}
    />
  );
}

describePanelStateContract({
  name: "DataHealthPanel",
  renderState: renderPanel,
  data: buildPanelHealthReport(),
  // A cross-check name: it exists only inside the rendered report, never in the form, so
  // "the payload is on screen" and "the panel rendered at all" stay separable.
  dataText: "index_membership_vs_daily",
});

describe("DataHealthPanel surfaces what a clean-looking report can still be hiding", () => {
  afterEach(cleanup);

  it("names the waived checks rather than reporting the dataset as simply ready", () => {
    // The rendered half of `panelHealthStateFrom`'s headline rule. A table that printed
    // only `state` would show "就绪" here and be, strictly, true and useless.
    const base = buildPanelHealthReport();
    render(
      renderPanel({
        kind: "degraded",
        data: buildPanelHealthReport({
          datasets: [{ ...base.datasets[0], checks_waived: ["daily_coverage", "revision"] }],
        }),
        reason: "跳过了检查",
      }),
    );
    expect(screen.getByText("daily_coverage、revision")).toBeInTheDocument();
    expect(screen.queryByText("全部已运行")).not.toBeInTheDocument();
  });

  it("says a cross-dataset check did not run, and why", () => {
    render(
      renderPanel({
        kind: "degraded",
        data: buildPanelHealthReport({
          cross_checks: [
            {
              name: "index_membership_vs_daily",
              datasets: ["index_daily", "index_member"],
              ran: false,
              skipped_reason: "index_member 未在本次请求中",
              finding_count: 0,
            },
          ],
        }),
        reason: "有检查未运行",
      }),
    );
    expect(screen.getByText(/未运行：index_member 未在本次请求中/)).toBeInTheDocument();
  });

  it("calls out a requested year the panel does not actually hold", () => {
    // `years_present` alone reads as a fact; the gap between requested and present is the
    // defect. A page rendering only the former hides its own missing coverage.
    const base = buildPanelHealthReport();
    render(
      renderPanel({
        kind: "ready",
        data: buildPanelHealthReport({
          datasets: [
            { ...base.datasets[0], years_requested: [2025, 2026], years_present: [2026] },
          ],
        }),
      }),
    );
    expect(screen.getByText(/缺 2025/)).toBeInTheDocument();
  });

  it("renders a null freshness age as 未知, never as an empty cell", () => {
    // `event_age_seconds` is nullable on the wire. On a freshness page a blank cell and
    // "two days stale" must not look alike, so the null case gets words of its own.
    const base = buildPanelHealthReport();
    render(
      renderPanel({
        kind: "ready",
        data: buildPanelHealthReport({
          datasets: [{ ...base.datasets[0], event_age_seconds: null, fetch_age_seconds: null }],
        }),
      }),
    );
    expect(screen.getAllByText("未知").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps structural limitations in their own section, apart from this fetch's findings", () => {
    // S48 / S72 / S73's explicit caveat requirement, and the serialiser's own separation:
    // a structural boundary of a dataset and a defect of this fetch have different remedies.
    render(renderPanel({ kind: "ready", data: buildPanelHealthReport() }));
    const limitations = screen.getByRole("region", { name: "结构性限制（非本次抓取的缺陷）" });
    expect(limitations).toHaveTextContent("current_universe_is_not_pit_universe");
    expect(limitations).toHaveTextContent("成分股为当前股票池快照，非时间点股票池");
  });

  it.each([
    [45, "45 秒"],
    [600, "10 分钟"],
    [7200, "2.0 小时"],
    [172800, "2.0 天"],
  ])("renders a %i second age at a readable scale (%s)", (seconds, expected) => {
    // A freshness page that printed "172800" would be accurate and unreadable, and one
    // that printed everything in one unit would make "45 秒" and "2.0 天" look alike at a
    // glance. Each threshold is exercised so a mis-ordered comparison cannot hide.
    const base = buildPanelHealthReport();
    render(
      renderPanel({
        kind: "ready",
        data: buildPanelHealthReport({
          datasets: [{ ...base.datasets[0], event_age_seconds: seconds }],
        }),
      }),
    );
    expect(screen.getAllByText(expected).length).toBeGreaterThanOrEqual(1);
  });

  it("says so explicitly when no structural limitation is registered", () => {
    // The section is rendered either way. A limitations heading with nothing under it
    // reads as "not loaded"; "none registered" is a different and checkable claim.
    render(renderPanel({ kind: "ready", data: buildPanelHealthReport({ limitations: [] }) }));
    expect(
      screen.getByText("该请求范围内没有已登记的结构性限制。"),
    ).toBeInTheDocument();
  });

  it("shows all three severity counters, including the ones that are zero", () => {
    // The client half of the serialiser's rule: a severity with no findings must read `0`,
    // not be missing, or "no blocking findings" and "the key was never emitted" collapse.
    render(renderPanel({ kind: "ready", data: buildPanelHealthReport() }));
    for (const label of ["阻断", "警告", "提示"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});
