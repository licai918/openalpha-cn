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
  FactorExperimentEnvelope,
  PanelHealthReport,
  PortfolioConstructionView,
  PredictionIndex,
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

// =========================================================================================
// V2-P5-017 / V2-P5-018.
//
// Each of the three below replaces a naive version that was written first and measured:
// 9 of the 19 cases in `contractStateLab.test.ts` failed against it. What each naive
// version got wrong is recorded on the function it became.
// =========================================================================================

/**
 * The one tier step a three-tier report's acceptance criterion is decided on.
 *
 * Mirrors `factor_view.ACCEPTANCE_STEP`, whose docstring says it exists because the grid
 * "treats all three as equals on purpose" while only one carries the finding, and that "a
 * face that printed six equal rows left the reader to know which". This face is one of
 * those faces, so it points.
 */
const ACCEPTANCE_STEP: readonly [string, string] = ["processed", "neutralized"];

/**
 * Whether a Decimal-rendered-as-string is zero, without going through a float.
 *
 * The naive version of `portfolioConstructionStateFrom` used `if (view.unallocated_weight)`
 * and degraded every clean construction, because `"0"` is a non-empty string and therefore
 * truthy. `Number(value) === 0` would have worked for the zeros but re-opens the very hole
 * the wire format closes — the serialiser renders these as strings precisely so no reader
 * takes a weight through a float — so the test is on the spelling instead. Python emits an
 * exact zero as `"0"`, `"0.00"` or `"0E-10"` depending on the arithmetic behind it, and all
 * three mean the same thing.
 */
function isZeroDecimalString(value: string): boolean {
  const trimmed = value.trim();
  // A digit must be present, or the empty string would match `0*` vacuously.
  return /\d/.test(trimmed) && /^[+-]?0*(?:\.0*)?(?:[eE][+-]?\d+)?$/.test(trimmed);
}

/**
 * Two things page ③ is asked for that no shipped HTTP contract can answer.
 *
 * Named rather than drawn. This repository books an invented number as a defect, and both
 * of these would be inventions: the data structures exist in the library and reach no face.
 */
export const FACTOR_LAB_CONTRACT_GAPS: ReadonlyArray<{ code: string; detail: string }> = [
  {
    code: "ic_decay_curve_reaches_no_http_contract",
    detail:
      "衰减：`ICDecayCurve` 与 `ICDecayRung` 在 `backtest/factor_ic.py` 里是完整的契约，但实测" +
      "全库没有任何调用方——它们不进 `FactorExperimentArtifact`，不进任何 view，也不进任何路由。" +
      "而且客户端拼不出来：一个 `TierReport` 的四项研究被校验器强制同一个 `horizon_sessions`，" +
      "所以一次实验就是一个 horizon；把多次实验横排也不成立，因为 `ICDecayCurve` 要求各档位于" +
      "同一样本之上，两次独立运行不保证这一点。故本页不画衰减曲线，只标出本次实验的那一个 horizon。",
  },
  {
    code: "cross_factor_correlation_reaches_no_http_contract",
    detail:
      "相关性：唯一能经 HTTP 拿到的相关系数是 `tiers[].survival`，而它是**同一个因子**的 raw 档" +
      "与本档之间的相关性（左右 `factor_id` 相同），回答的是「中性化之后还剩多少排序」。" +
      "因子 A 对因子 B 的相关性没有字段：`ICSeriesCorrelation` 与 `RedundancyStudy." +
      "correlate_ic_series` 同样零调用方，也没有任何路由接受两个因子。故本页把这个数标注为" +
      "「档位存活相关性」而不是「因子相关性」，两者是不同的问题，用同一个标题就是误报。",
  },
];

/** Three things page ④ is asked for that no browser-reachable contract can answer. */
export const PORTFOLIO_CONTRACT_GAPS: ReadonlyArray<{ code: string; detail: string }> = [
  {
    code: "capacity_reaches_no_portfolio_contract",
    detail:
      "容量：`/portfolio` 与 `/backtests` 下没有任何路由返回容量字段，而且构建面自己就把这件事" +
      "写成了一条限制——`no_capacity_liquidity_or_cost_term_enters_a_weight`，即没有任何容量、" +
      "流动性或成本项进入过权重。真正的容量数字在 `backtest/factor_tradeability.py`，只经" +
      "`POST /api/v1/factors/run` 出现在**因子实验**上（页面③），它按因子实验计量而不按组合计量，" +
      "把它搬到本页当作本组合的容量就是换了一个对象作答。",
  },
  {
    code: "paper_portfolio_has_no_http_face",
    detail:
      "Paper 净值：`backtest/paper.py` 存在，但实测 `api/app.py` 不 import 它，也没有" +
      "`POST /api/v1/portfolio/paper/advance` 这条路由——CLI 与 SDK 同样没有对应面。" +
      "另外全库检索不到 `nav` / `net_value` 任何一个标识符；最接近的是" +
      "`PortfolioBacktestReport.equity_curve[].equity`，那是回测的权益曲线而不是 paper 账本。" +
      "paper 账本唯一能经 HTTP 看见的痕迹是它写进同一个 ledger 的成交流水。",
  },
  {
    code: "segmented_report_has_no_http_face",
    detail:
      "分段：`backtest/segmented_reporting.py` 与 `OpenAlphaSDK.segmented_outcomes` 都在，" +
      "`openalpha validation segmented` 也在，但没有任何 HTTP 路由——`api/app.py` 不 import 该模块。" +
      "同一条缺口也盖住 `outcome_statistics`（V2-P5-008）与其中的 BH 多重检验 `family` 块" +
      "（V2-P5-007）：三者都是 CLI/SDK 面，浏览器一个都够不着。故本页不显示分段结论。",
  },
];

/**
 * A sealed factor experiment. **An unmeasured grid is not a passing grid.**
 *
 * The naive version filtered the grid for `removed`/`reversed` and called everything else a
 * success. Measured, that answered `succeeded` for an experiment whose six cells were *all*
 * `not_measured` — and that is not a hypothetical, it is the case
 * `factor_view.everything_is_unmeasured` exists for. Its docstring calls it "the quietest
 * bad answer" and records that the acceptance review "named it the most dangerous thing on
 * this face": such a run exits `0`, answers `200`, and "a reader (or a CI step) that greps
 * for `removed`, finds nothing and stops has concluded 'this factor survived neutralisation'
 * about two tiers that never computed a number."
 *
 * **The browser is the face with the least protection against it.** The CLI prints a named
 * line when it is true and `factor run --json` prints the same line on stderr, but the
 * property is deliberately *not* a field on the envelope, so an HTTP client that does not
 * recompute it is told nothing at all. Recomputing it is this function's first rule.
 *
 * `removed` is emphatically **not** degraded (the fourth test pins this): a measured step
 * that destroyed the statistic is a report doing its job, and folding it together with "we
 * could not measure" would delete the distinction the row is about.
 */
export function factorExperimentStateFrom(
  envelope: FactorExperimentEnvelope,
): PanelState<FactorExperimentEnvelope> {
  const artifact = envelope.document.artifact;

  if (artifact.tiers.length === 0) {
    // The artifact's own validator forbids this, so it should be unreachable. Kept because
    // the alternative — rendering a three-tier comparison with no tiers as a pass — is the
    // failure mode this whole function exists to refuse.
    return {
      kind: "empty",
      reason: "该实验文档没有携带任何档位，不构成一次三档对比。",
    };
  }

  // 1. The quietest bad answer, first, because every rule below reads a number it does not have.
  if (
    artifact.attributions.length > 0 &&
    artifact.attributions.every((cell) => cell.verdict === "not_measured")
  ) {
    return {
      kind: "degraded",
      data: envelope,
      reason:
        "归因网格的六个格子全部为 not_measured：本次实验没有测出任何一个可比较的数，" +
        "因此「没有出现 removed」不等于「因子通过了中性化」。各档自己的 coverage 码才是原因。",
    };
  }

  // 2. The one step the acceptance criterion is decided on. Five surviving cells do not
  //    substitute for it — that is the separation the third test makes.
  const acceptance = artifact.attributions.filter(
    (cell) => cell.from_tier === ACCEPTANCE_STEP[0] && cell.to_tier === ACCEPTANCE_STEP[1],
  );
  const unmeasuredAcceptance = acceptance.filter(
    (cell) => cell.verdict === "not_measured" || cell.verdict === "no_baseline",
  );
  if (unmeasuredAcceptance.length > 0) {
    return {
      kind: "degraded",
      data: envelope,
      reason:
        `验收判据所在的 processed→neutralized 步骤有 ${unmeasuredAcceptance.length} 个统计量` +
        `未能作答（${unmeasuredAcceptance.map((cell) => `${cell.statistic}=${cell.verdict}`).join("、")}）；` +
        "其余格子无论判为什么，都不是这一步的答案。",
    };
  }

  // 3. Coverage before the statistic — `panelHealthStateFrom`'s rule. A `null` mean_ic beside
  //    `insufficient_as_ofs` means nothing was measured, not that the factor scored zero.
  const uncovered: string[] = [];
  for (const tier of artifact.tiers) {
    if (tier.ic.coverage !== "measured") {
      uncovered.push(`${tier.tier} 档的 IC 未测量（${tier.ic.coverage}）`);
    }
    if (tier.portfolio.coverage !== "measured") {
      uncovered.push(`${tier.tier} 档的分组组合未测量（${tier.portfolio.coverage}）`);
    }
  }
  if (uncovered.length > 0) {
    return { kind: "degraded", data: envelope, reason: uncovered.join("；") };
  }

  return { kind: "succeeded", data: envelope };
}

/**
 * The prediction register. **A backfill is not an out-of-sample result.**
 *
 * The naive version read `scored_count` and called any register with scores `ready`, which
 * put a recomputation on screen in the same table and the same columns as a genuine forward
 * prediction. `PREDICTION_STANDING_MEANINGS` is unambiguous about what that costs: a
 * `backfill` proves "anything at all about foresight" — nothing — and an `unwitnessed`
 * record's claim "is uncorroborated, which may be a slow disk and may be a backdated
 * predicted_at, and this record cannot tell you which".
 *
 * So anything that is not `forward` degrades the register, and the affected `record_id`s are
 * listed rather than counted: the third test asserts the untouched record is *not* named, so
 * a blanket sentence over the whole register fails it.
 */
export function predictionRegisterStateFrom(index: PredictionIndex): PanelState<PredictionIndex> {
  if (index.predictions.length === 0) {
    return {
      kind: "empty",
      reason: "本地还没有任何已登记的预测，因而没有可读的样本外记录。",
    };
  }

  const notForward = index.predictions.filter((entry) => entry.standing !== "forward");
  if (notForward.length > 0) {
    return {
      kind: "degraded",
      data: index,
      reason:
        `以下记录不是 forward 立场，不能当作样本外证据阅读：` +
        `${notForward.map((entry) => `${entry.record_id}（${entry.standing}）`).join("、")}。`,
    };
  }

  // `ready` and not `succeeded`: this is a read of a register, not the outcome of a run the
  // user started — the distinction `panelState.ts` keeps the two kinds apart for.
  return { kind: "ready", data: index };
}

/**
 * A heuristic portfolio construction.
 *
 * The naive version had two defects and the tests caught both. It called `targets.length > 0`
 * a success, so a construction whose declared caps were **left breached** by the turnover
 * budget rendered as a clean portfolio — the backend ships a limitation for exactly that
 * case (`the_turnover_budget_can_leave_a_cap_breached_and_says_so_instead_of_retrimming`),
 * so those weights really do violate a limit the user declared. And it tested
 * `if (view.unallocated_weight)`, which degraded every clean construction, because the wire
 * value is the *string* `"0"` and every non-empty string is truthy. See `isZeroDecimalString`.
 */
export function portfolioConstructionStateFrom(
  view: PortfolioConstructionView,
): PanelState<PortfolioConstructionView> {
  if (view.targets.length === 0) {
    return {
      kind: "empty",
      reason: "本次构建没有得到任何持仓，全部权重都留在现金里。",
    };
  }

  if (view.caps_breached_after_turnover_damping.length > 0) {
    return {
      kind: "degraded",
      data: view,
      reason:
        `换手预算缩放之后，以下上限仍被突破：` +
        `${view.caps_breached_after_turnover_damping.join("、")}。` +
        "服务端选择如实上报而不是再裁一次，因此屏幕上的权重确实违反了声明的上限。",
    };
  }

  if (!isZeroDecimalString(view.unallocated_weight)) {
    return {
      kind: "degraded",
      data: view,
      reason:
        `有 ${view.unallocated_weight} 的权重上限吃不下，已作为现金报出而非摊派给最后一名；` +
        "只看持仓表会少算这一部分。",
    };
  }

  return { kind: "succeeded", data: view };
}
