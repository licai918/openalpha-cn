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
import type {
  Evidence,
  PanelHealthReport,
  ReplayReport,
  ResearchResult,
  ShortlistAnswer,
  ValidationResult,
} from "./types";

/**
 * A panel health report. **Read the waived checks before the verdict.**
 *
 * `replayStateFrom` below reads `look_ahead_violations` before the success counters for a
 * reason it states: the counters can all look perfect while the run is unusable. This is
 * the same shape of mistake one plane over, and it is the one this row exists to prevent.
 * A report can carry `is_clean: true`, every severity counter at `0`, and every dataset
 * `state: "ready"` — and be clean **only because the questions were never put**. The
 * serialiser says so in as many words: `checks_waived`'s empty tuple "is the *stronger*
 * claim ('every check ran'), and a caller drawing a conclusion from `state == "ready"` has
 * to be able to see which questions were never put."
 *
 * So an unrun check is `degraded`, not `ready`. It is deliberately **not** `blocked`: the
 * data is readable and the report is worth showing, it simply must not be shown as an
 * unqualified pass. The three severities are honoured as the backend defines them —
 * `blocking` refuses, while `warning` and `notice` ("a measured fact the report was asked
 * for that is not a fault") qualify — so a revision notice and a missing partition, which
 * `is_clean` alone renders identically, come out as two different states here.
 */
export function panelHealthStateFrom(report: PanelHealthReport): PanelState<PanelHealthReport> {
  const blocking = report.findings.filter((finding) => finding.severity === "blocking");
  if (report.counts_by_severity.blocking > 0 || report.blocked_datasets.length > 0) {
    const detail =
      blocking.length > 0
        ? blocking.map((finding) => finding.detail).join("；")
        : `受阻数据集：${report.blocked_datasets.join("、")}`;
    return {
      kind: "blocked",
      reason: `面板体检判定为阻断：${detail}`,
    };
  }

  if (report.datasets.length === 0) {
    // `readiness_payload` refuses to render this server-side because "`all_ready` over no
    // dataset is vacuously `True`… the empty success in its purest form". A client that
    // drew a green tick from the same emptiness would rebuild it here.
    return { kind: "empty", reason: "本次体检没有覆盖任何数据集，因而不构成结论。" };
  }

  // Every reason this report is less than an unqualified pass, named rather than counted.
  const caveats: string[] = [];
  for (const dataset of report.datasets) {
    if (dataset.checks_waived.length > 0) {
      caveats.push(`${dataset.dataset} 跳过了检查：${dataset.checks_waived.join("、")}`);
    }
  }
  for (const check of report.cross_checks) {
    if (!check.ran) {
      caveats.push(
        `跨数据集检查 ${check.name} 未运行：${check.skipped_reason ?? "未给出原因"}`,
      );
    }
  }
  for (const finding of report.findings) {
    caveats.push(`${finding.severity}/${finding.code}：${finding.detail}`);
  }

  if (caveats.length > 0) {
    return { kind: "degraded", data: report, reason: caveats.join("；") };
  }
  return { kind: "ready", data: report };
}

/**
 * A shortlist answer. **`admitted` decides, never the funnel's own list.**
 *
 * `funnel.shortlist` is what the ranking computed; `admitted` is what the gate allowed out.
 * On a refused list the first is full and the second is `null`, so a classifier that reads
 * the funnel renders names off a list that was refused — the exact defect the endpoint was
 * rebuilt to prevent one plane over ("a caller told `200` with an empty array cannot tell a
 * refusal from a market that offered nothing").
 *
 * `null` and `[]` are therefore kept as two answers here as they are on the wire: `null` is
 * `blocked`, `[]` is `empty`. Collapsing them with `admitted?.length` or `admitted ?? []`
 * re-creates on the client the bug the server fixed, and both spellings type-check.
 */
export function shortlistStateFrom(answer: ShortlistAnswer): PanelState<ShortlistAnswer> {
  if (answer.is_blocked) {
    const detail =
      answer.blocks.length > 0
        ? answer.blocks.map((block) => block.detail).join("；")
        : "未给出具体门槛。";
    return { kind: "blocked", reason: `候选清单被门槛拒绝：${detail}` };
  }

  if (answer.admitted === null) {
    // Should be unreachable: `admitted` is null iff the gate refused. If it ever arrives,
    // "no list" is the safe reading — treating a missing list as an empty one is how a
    // null becomes a green tick.
    return {
      kind: "blocked",
      reason: "该答案声称已放行却没有携带清单，结果自相矛盾，不可作为候选依据。",
    };
  }

  if (answer.admitted.length === 0) {
    return {
      kind: "empty",
      reason: `门槛已通过，但本次横截面（${answer.cross_section.universe_count} 只）没有任何标的入选。`,
    };
  }

  const caveats: string[] = [];
  if (answer.unresearched.length > 0) {
    caveats.push(`以下标的没有闭合的证据链：${answer.unresearched.join("、")}`);
  }
  if (answer.funnel.untradeable_not_named > 0) {
    // The residual behind a server-side cap: a page rendering only `untradeable` under-reports.
    caveats.push(`另有 ${answer.funnel.untradeable_not_named} 只不可交易标的未被逐一列名。`);
  }
  if (answer.evidence_without_a_stored_run.length > 0) {
    caveats.push(
      `以下证据没有对应的已存运行：${answer.evidence_without_a_stored_run.join("、")}`,
    );
  }
  if (answer.evidence_from_an_unfinished_run.length > 0) {
    caveats.push(
      `以下证据来自未完成的运行：${answer.evidence_from_an_unfinished_run.join("、")}`,
    );
  }

  if (caveats.length > 0) {
    return { kind: "degraded", data: answer, reason: caveats.join("；") };
  }
  return { kind: "succeeded", data: answer };
}

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
