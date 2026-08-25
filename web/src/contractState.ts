// V2-P5-019. Contract payload → panel state.
//
// Kept out of `App.tsx` so each classification is a pure function with a direct unit test
// (`contractState.test.ts`) rather than something only reachable by driving the whole app
// through a stubbed `fetch`. That matters for this row specifically: the point of adding
// `degraded` / `stale` / `blocked` is that the application *constructs* them from real
// contract fields. A union whose extra kinds are only ever built inside a component test
// is decorative, and decorative states are what let a refusal render as a success.
//
// Every rule below keys on a field that exists in the checked-in schemas under
// `docs/api/schemas/` and is mirrored in `types.ts`. Where the backend supplies words for
// the condition (`abstention_reason`, `failures[]`, `risk_flags[]`), those words are
// carried through verbatim rather than replaced with a generic sentence.

import type { PanelState } from "./panelState";
import type { Evidence, ReplayReport, ResearchResult, ValidationResult } from "./types";

/**
 * Evidence rows. `degraded` when any row cannot be redistributed.
 *
 * `redistribution` is `"allowed" | "restricted" | "unknown"`; only `"allowed"` is safe to
 * put in an export, so `"unknown"` is treated as restrictive rather than optimistic —
 * "we do not know the licence" is not a licence.
 */
export function evidenceStateFrom(items: Evidence[]): PanelState<Evidence[]> {
  if (items.length === 0) {
    return { kind: "empty", reason: "该标的在所选时间点没有可见证据。" };
  }
  const restricted = items.filter((item) => item.redistribution !== "allowed");
  if (restricted.length > 0) {
    return {
      kind: "degraded",
      data: items,
      reason: `${restricted.length} 条证据不可再分发（restricted 或 unknown），导出时会被过滤。`,
    };
  }
  return { kind: "ready", data: items };
}

/**
 * A research result. `blocked` outranks `degraded`: when the risk gate refused, that is
 * the answer, and a signal's flags are a detail of a decision that will not be acted on.
 */
export function researchStateFrom(result: ResearchResult): PanelState<ResearchResult> {
  if (result.decision.risk_decision === "block") {
    return {
      kind: "blocked",
      reason: `风险门判定为 block：${result.decision.final_action}。该结论不可作为可执行结果。`,
    };
  }
  if (result.signal.direction === "abstain") {
    return {
      kind: "degraded",
      data: result,
      reason: `信号弃权：${result.signal.abstention_reason ?? "未给出原因"}`,
    };
  }
  if (result.signal.risk_flags.length > 0) {
    return {
      kind: "degraded",
      data: result,
      reason: `携带风险标记：${result.signal.risk_flags.join("、")}`,
    };
  }
  return { kind: "succeeded", data: result };
}

/**
 * A replay report. A look-ahead violation is `blocked`, not `degraded`.
 *
 * PRD Decision 8 makes look-ahead a fail-closed gate and Decision 19 makes zero known
 * severe violations a release gate. The report's success counters can all look perfect
 * while `look_ahead_violations` is non-zero — that combination is exactly what a
 * counter-driven classifier gets wrong, so the violation count is read first.
 */
export function replayStateFrom(report: ReplayReport): PanelState<ReplayReport> {
  if (report.look_ahead_violations > 0) {
    return {
      kind: "blocked",
      reason: `回放发现 ${report.look_ahead_violations} 处前视违规，本次结果不可用。`,
    };
  }
  if (report.total_cases === 0) {
    return { kind: "empty", reason: "该语料没有可回放的案例。" };
  }
  if (report.failures.length > 0) {
    return {
      kind: "degraded",
      data: report,
      reason: `${report.failures.length} 个案例未通过：${report.failures.join("；")}`,
    };
  }
  return { kind: "succeeded", data: report };
}

/**
 * A validation result. `degraded` when the attribution names nothing.
 *
 * Deliberately not a threshold on the residual's magnitude: no cut-off separating an
 * "acceptable" residual from an unacceptable one has been measured anywhere in this
 * repository, and inventing one here would be a number with no evidence behind it. Zero
 * named terms needs no cut-off — it means the entire net active return is unexplained,
 * which is not a completed attribution under any threshold.
 */
export function validationStateFrom(result: ValidationResult): PanelState<ValidationResult> {
  if (result.attribution.length === 0) {
    return {
      kind: "degraded",
      data: result,
      reason: "本次归因没有任何具名项，净主动收益全部落在残差中。",
    };
  }
  return { kind: "succeeded", data: result };
}

/**
 * Re-label a state whose payload answers an older question than the one now on screen.
 *
 * Only data-carrying states can go stale — `stale` carries data, so converting a `blocked`
 * or `failed` state into it would put a refused payload back on screen. Those are returned
 * untouched, by identity, which is what `contractState.test.ts` asserts.
 */
export function markStale<T>(state: PanelState<T>, reason: string): PanelState<T> {
  switch (state.kind) {
    case "ready":
    case "succeeded":
    case "degraded":
    case "stale":
      return { kind: "stale", data: state.data, reason };
    case "idle":
    case "loading":
    case "empty":
    case "blocked":
    case "failed":
      return state;
  }
}
