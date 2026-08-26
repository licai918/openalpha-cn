// V2-P5-015 / V2-P5-016. The classifiers for pages ① and ②.
//
// Kept in their own file rather than appended to `contractState.test.ts` only because the
// two pages arrive together and their fixtures are large; the rules are the same rules.
//
// Each `describe` below has one test that is the reason the classifier exists — the
// fixture on which a plausible wrong implementation and the right one give **different**
// answers. `contractState.ts` set that precedent with `replayStateFrom`, whose pinning
// fixture is `succeeded === total_cases && success_rate === 1 && look_ahead_violations === 1`:
// perfect counters, and still refused. The two here are:
//
//   * a panel report that is clean **only because the checks were waived** — every counter
//     zero, `is_clean: true`, every dataset `ready`, and nobody asked the questions;
//   * a shortlist that was **refused while its funnel had already computed a list** —
//     `is_blocked: true`, `admitted: null`, and `funnel.shortlist` full of names.
//
// An implementation keying on `is_clean` fails the first. One keying on
// `funnel.shortlist.length` fails the second and puts names off a refused list on screen.

import { describe, expect, it } from "vitest";

import { panelHealthStateFrom, shortlistStateFrom } from "./contractState";
import { panelData, panelMessage } from "./panelState";
import { buildPanelHealthReport, buildShortlistAnswer } from "./test/fixtures";

describe("panelHealthStateFrom", () => {
  it("is ready for a clean report where every check actually ran", () => {
    expect(panelHealthStateFrom(buildPanelHealthReport()).kind).toBe("ready");
  });

  it("is degraded when the report is clean only because a check was waived", () => {
    // THE test for this classifier. Every counter is zero, `is_clean` is true, the dataset
    // is `ready` — and one check never ran. An implementation that reads `is_clean`, or
    // that sums `counts_by_severity`, answers `ready` here and is wrong: the serialiser's
    // own note is that the empty `checks_waived` tuple is the *stronger* claim, so a
    // verdict resting on a waived check is not the same verdict as one that earned it.
    const report = buildPanelHealthReport({
      datasets: [
        {
          ...buildPanelHealthReport().datasets[0],
          checks_waived: ["daily_coverage"],
        },
      ],
    });
    expect(report.is_clean).toBe(true);
    expect(report.counts_by_severity).toEqual({ blocking: 0, warning: 0, notice: 0 });
    expect(report.datasets[0].state).toBe("ready");

    const state = panelHealthStateFrom(report);
    expect(state.kind).toBe("degraded");
    // The waived check is named, not merely counted: "something was waived" sends nobody
    // anywhere, "daily_coverage was waived" does.
    expect(panelMessage(state)).toContain("daily_coverage");
    // …and the report is still on screen. A waived check does not make the data unreadable.
    expect(panelData(state)).toBe(report);
  });

  it("is degraded when a cross-dataset check was skipped, and says why it was skipped", () => {
    // The same rule on the report's other half. `cross_checks[].ran` is carried precisely
    // so an empty `findings` list can be read correctly, and `skipped_reason` is the
    // backend's own words for why.
    const report = buildPanelHealthReport({
      cross_checks: [
        {
          name: "index_membership_vs_daily",
          datasets: ["index_daily", "index_member"],
          ran: false,
          skipped_reason: "index_member 未在本次请求中",
          finding_count: 0,
        },
      ],
    });
    const state = panelHealthStateFrom(report);
    expect(state.kind).toBe("degraded");
    expect(panelMessage(state)).toContain("index_membership_vs_daily");
    expect(panelMessage(state)).toContain("index_member 未在本次请求中");
  });

  it("is blocked when a blocking finding exists, and carries that finding's own words", () => {
    const report = buildPanelHealthReport({
      is_clean: false,
      counts_by_severity: { blocking: 1, warning: 0, notice: 0 },
      blocked_datasets: ["index_daily"],
      datasets: [
        { ...buildPanelHealthReport().datasets[0], is_ready: false, state: "blocked" },
      ],
      findings: [
        {
          code: "missing_year",
          category: "coverage",
          severity: "blocking",
          dataset: "index_daily",
          datasets: ["index_daily"],
          detail: "index_daily 缺少 2026 年分区。",
          year: 2026,
          count: null,
        },
      ],
    });
    const state = panelHealthStateFrom(report);
    expect(state.kind).toBe("blocked");
    expect(panelMessage(state)).toContain("index_daily 缺少 2026 年分区。");
    // A refusal renders no report: the same invariant the union enforces everywhere else.
    expect(panelData(state)).toBeNull();
  });

  it("separates a notice-only report from a blocking one, which `is_clean` alone cannot", () => {
    // `is_clean` is false for both of these. A classifier keying on it renders a panel with
    // a revision notice exactly as it renders one with a missing partition. `notice` is,
    // per panel_doctor, "a measured fact the report was asked for that is not a fault".
    const noticeOnly = buildPanelHealthReport({
      is_clean: false,
      counts_by_severity: { blocking: 0, warning: 0, notice: 3 },
      findings: [
        {
          code: "revised_rows",
          category: "revision",
          severity: "notice",
          dataset: "index_daily",
          datasets: ["index_daily"],
          detail: "3 行在本次抓取中被修订。",
          year: 2026,
          count: 3,
        },
      ],
    });
    const state = panelHealthStateFrom(noticeOnly);
    expect(state.kind).toBe("degraded");
    expect(state.kind).not.toBe("blocked");
    expect(panelData(state)).toBe(noticeOnly);
  });

  it("blocked outranks a waived check: a refusal is never softened into a caveat", () => {
    const report = buildPanelHealthReport({
      is_clean: false,
      counts_by_severity: { blocking: 1, warning: 0, notice: 0 },
      blocked_datasets: ["index_daily"],
      datasets: [
        { ...buildPanelHealthReport().datasets[0], checks_waived: ["daily_coverage"] },
      ],
      findings: [
        {
          code: "missing_year",
          category: "coverage",
          severity: "blocking",
          dataset: "index_daily",
          datasets: ["index_daily"],
          detail: "缺分区。",
          year: 2026,
          count: null,
        },
      ],
    });
    expect(panelHealthStateFrom(report).kind).toBe("blocked");
  });

  it("is empty rather than ready for a report over no dataset at all", () => {
    // `readiness_payload` refuses to *render* this server-side because "`all_ready` over no
    // dataset is vacuously `True`… the empty success in its purest form". The same
    // reasoning applies to a client that would otherwise draw a green tick from it.
    const report = buildPanelHealthReport({ datasets: [], cross_checks: [] });
    expect(report.is_clean).toBe(true);
    expect(panelHealthStateFrom(report).kind).toBe("empty");
  });
});

describe("shortlistStateFrom", () => {
  it("succeeds for an admitted list whose candidates are all researched and tradeable", () => {
    expect(shortlistStateFrom(buildShortlistAnswer()).kind).toBe("succeeded");
  });

  it("is blocked for a refused list even though its funnel already computed names", () => {
    // THE test for this classifier, and the client-side half of the defect the backend row
    // exists for: "a caller told `200` with an empty array cannot tell a refusal from a
    // market that offered nothing". Here `funnel.shortlist` has two names in it — the
    // funnel ran to completion — and the gate then refused. An implementation that renders
    // `funnel.shortlist`, or that tests `admitted?.length`, puts those two names on screen
    // as a result. `admitted` is `null`, and `null` is the answer.
    const answer = buildShortlistAnswer({
      is_blocked: true,
      admitted: null,
      blocks: [
        {
          code: "researched_ratio_below_minimum",
          detail: "已研究比例 0.42 低于要求的 0.80。",
          measured: 0.42,
          required: 0.8,
        },
      ],
    });
    expect(answer.funnel.shortlist).toHaveLength(2);

    const state = shortlistStateFrom(answer);
    expect(state.kind).toBe("blocked");
    expect(panelData(state)).toBeNull();
    // Both sides of the comparison, not just "refused".
    expect(panelMessage(state)).toContain("已研究比例 0.42 低于要求的 0.80。");
  });

  it("is empty, not blocked, when the list cleared over a market that offered nothing", () => {
    // The other side of the same coin. Byte for byte this differs from a refusal only in
    // `is_blocked` and `admitted: [] vs null` — and it is a completely different answer.
    // A classifier collapsing them re-creates, on the client, the bug the server fixed.
    const answer = buildShortlistAnswer({ is_blocked: false, admitted: [] });
    const state = shortlistStateFrom(answer);
    expect(state.kind).toBe("empty");
    expect(state.kind).not.toBe("blocked");
  });

  it("refuses an incoherent answer that claims to have cleared but carries no list", () => {
    // `admitted` is null iff the gate refused, so this shape should never arrive. If it
    // does, the safe reading is "no list", not "a list of length zero" — silently treating
    // a missing list as an empty one is how a null becomes a green tick.
    const state = shortlistStateFrom(buildShortlistAnswer({ is_blocked: false, admitted: null }));
    expect(state.kind).toBe("blocked");
    expect(panelData(state)).toBeNull();
  });

  it("is degraded when candidates have no closed evidence chain, and names them", () => {
    const answer = buildShortlistAnswer({ unresearched: ["000003.SZ", "000004.SZ"] });
    const state = shortlistStateFrom(answer);
    expect(state.kind).toBe("degraded");
    expect(panelMessage(state)).toContain("000003.SZ");
    expect(panelData(state)).toBe(answer);
  });

  it("is degraded when the tradeability census could not name every excluded security", () => {
    // `untradeable` is bounded server-side by `MAX_NAMED_UNTRADEABLE` and
    // `untradeable_not_named` is the residual. A page that renders only the array and not
    // the residual under-reports; saying so is the point of this arm.
    const answer = buildShortlistAnswer({
      funnel: { ...buildShortlistAnswer().funnel, untradeable_not_named: 37 },
    });
    const state = shortlistStateFrom(answer);
    expect(state.kind).toBe("degraded");
    expect(panelMessage(state)).toContain("37");
  });

  it("is degraded when supplied evidence resolved to no stored run", () => {
    const answer = buildShortlistAnswer({ evidence_without_a_stored_run: ["ev_orphan"] });
    expect(shortlistStateFrom(answer).kind).toBe("degraded");
  });

  it("blocked outranks degraded: a refused list is not reported as a caveated one", () => {
    const answer = buildShortlistAnswer({
      is_blocked: true,
      admitted: null,
      unresearched: ["000003.SZ"],
      blocks: [{ code: "tradable_ratio", detail: "不足。", measured: 0.1, required: 0.9 }],
    });
    expect(shortlistStateFrom(answer).kind).toBe("blocked");
  });
});
