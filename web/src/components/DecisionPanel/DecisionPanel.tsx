import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import type { ResearchResult } from "../../types";

type DecisionPanelProps = {
  /** Only the count was ever read from the evidence array (to gate the run button and the
   * "证据不足" copy), so the panel takes the number rather than the rows. */
  evidenceCount: number;
  /** V2-P5-019: replaces `result` + `loading` + `error`, which made
   * `{result: null, loading: false, error: null}` representable — a research run that
   * failed and lost its message rendered identically to one that never started. */
  state: PanelState<ResearchResult>;
  onRun: () => void;
};

const actionLabels: Record<string, string> = {
  watch: "观察",
  avoid: "回避",
  abstain: "弃权"
};

// `final_action` is typed as a closed literal union, but that is only a compile-time
// claim about what the backend will send — see typesContractDrift.test.ts's
// enum-value drift guard for what keeps it honest. If the contract ever adds a value
// (or a stale frontend build is served against a newer backend) before the frontend
// is rebuilt, this must not render blank: an unrecognised label is confusing, but a
// silently blank verdict for a real decision is worse.
function actionLabel(action: string): string {
  return actionLabels[action] ?? `未知动作（${action}）`;
}

export function DecisionPanel({ evidenceCount, state, onRun }: DecisionPanelProps) {
  const loading = state.kind === "loading";
  const result = panelData(state);
  // The precondition ("you have nothing to run on") outranks the idle copy, because it
  // tells the user what to do next rather than merely that nothing has happened yet.
  const blockedOnEvidence = evidenceCount === 0 && result === null && state.kind === "idle";

  return (
    <section className="panel decision-panel" aria-labelledby="decision-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">02 / DECISION</p>
          <h2 id="decision-heading">研究决策</h2>
        </div>
        <button className="button button--primary" disabled={!evidenceCount || loading} onClick={onRun}>
          {loading ? "研究中…" : "运行研究"}
        </button>
      </header>

      {blockedOnEvidence ? (
        <div className="empty-state">
          <strong>证据不足</strong>
          <span>先查询或导入当前时钟可见的证据。</span>
        </div>
      ) : (
        <PanelNotice state={state} idleText="尚未运行研究" />
      )}
      {result && (
        <div className="decision-content">
          <div className="decision-verdict">
            <span>最终动作</span>
            <strong>{actionLabel(result.decision.final_action)}</strong>
            <small>风险门：{result.decision.risk_decision}</small>
          </div>
          <dl className="metric-row">
            <div>
              <dt>方向</dt>
              <dd>{result.signal.direction}</dd>
            </div>
            <div>
              <dt>强度</dt>
              <dd>{result.signal.strength.toFixed(2)}</dd>
            </div>
            <div>
              <dt>置信度</dt>
              <dd>{Math.round(result.signal.confidence * 100)}%</dd>
            </div>
          </dl>
          <div className="trace-block">
            <h3>路由链</h3>
            <p>{result.decision.routing_path.join(" → ")}</p>
          </div>
          <div className="trace-block">
            <h3>引用证据</h3>
            <ul>
              {result.signal.evidence_ids.map((id) => (
                <li key={id}>
                  <code>{id}</code>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
