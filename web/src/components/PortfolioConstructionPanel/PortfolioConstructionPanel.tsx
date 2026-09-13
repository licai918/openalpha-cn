// V2-P5-018. Page ④ 组合与验证.
//
// Pure in `state`; the route container owns the request.
//
// **Every weight on this page is rendered as the string the contract sent.** Nothing here
// calls `Number()` on a weight, and that is the panel's single most load-bearing property.
// `construction_view` renders each Decimal as a string with a stated purpose — so a JSON
// reader "cannot silently take a weight through a float, which is the one conversion that
// would make `sum(weights) == invested_weight` stop being exactly true" — and a panel that
// recomputed the total to display it would undo that at the last step, on screen, where the
// user reads it. `PortfolioConstructionPanel.test.tsx` drives a fixture whose weights sum to
// `0.9999999999999999` in IEEE-754 while the contract says `"1"`, so a summing
// implementation fails rather than merely being frowned upon.
//
// **`method` is rendered, never summarised.** It is `Literal["heuristic, not optimized"]` in
// Python and a construction that cannot say that sentence does not pass the backend's own
// validation. A page that dropped it would present a rank-and-clamp heuristic as an
// optimiser, which is the one claim this whole plane is built to avoid making.
//
// Three of the row's seven items have no browser-reachable contract at all; they are listed
// by name in `PORTFOLIO_CONTRACT_GAPS` and rendered as absences rather than drawn.

import { PORTFOLIO_CONTRACT_GAPS } from "../../contractState";
import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import type { PortfolioConstructionView } from "../../types";

type PortfolioConstructionPanelProps = {
  state: PanelState<PortfolioConstructionView>;
  shortlistId: string;
  maxPositionWeight: string;
  turnoverBudget: string;
  onShortlistIdChange: (value: string) => void;
  onMaxPositionWeightChange: (value: string) => void;
  onTurnoverBudgetChange: (value: string) => void;
  onRun: () => void;
};

export function PortfolioConstructionPanel(props: PortfolioConstructionPanelProps) {
  const view = panelData(props.state);

  return (
    <section className="panel portfolio-panel" aria-labelledby="portfolio-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">P4 / PORTFOLIO</p>
          <h2 id="portfolio-heading">组合与验证</h2>
        </div>
      </header>

      <div className="query-grid">
        <label>
          候选清单编号
          <input
            value={props.shortlistId}
            onChange={(event) => props.onShortlistIdChange(event.target.value)}
            placeholder="sla_…"
          />
        </label>
        <label>
          单标的权重上限
          <input
            value={props.maxPositionWeight}
            onChange={(event) => props.onMaxPositionWeightChange(event.target.value)}
            placeholder="0.1"
          />
        </label>
        <label>
          换手预算（留空表示不设）
          <input
            value={props.turnoverBudget}
            onChange={(event) => props.onTurnoverBudgetChange(event.target.value)}
            placeholder="0.3"
          />
        </label>
        <button className="button button--secondary" onClick={props.onRun}>
          构建组合
        </button>
      </div>

      <PanelNotice state={props.state} idleText="尚未构建组合" />

      {view !== null && (
        <div className="portfolio-body">
          {/* The sentence the backend refuses to let a construction omit. */}
          <p className="warning-state" role="note">
            构建方法：<strong>{view.method}</strong>。分层按 rank 切块、块内等权，再按上限裁剪；
            没有目标函数、没有协方差、没有求解器。
          </p>

          <dl className="metric-row">
            <div>
              <dt>已投权重</dt>
              {/* Rendered from the field, never summed from `targets`. */}
              <dd>{view.invested_weight}</dd>
            </div>
            <div>
              <dt>现金</dt>
              <dd>{view.cash_weight}</dd>
            </div>
            <div>
              <dt>放不下（已作现金）</dt>
              <dd>{view.unallocated_weight}</dd>
            </div>
            <div>
              <dt>换手</dt>
              <dd>{view.turnover}</dd>
            </div>
            <div>
              <dt>预算前换手</dt>
              <dd>{view.turnover_before_budget}</dd>
            </div>
            <div>
              <dt>换手预算</dt>
              <dd>{view.turnover_budget ?? "未声明"}</dd>
            </div>
            <div>
              <dt>预算缩放系数</dt>
              <dd>{view.turnover_damping ?? "未触发"}</dd>
            </div>
          </dl>

          {view.caps_breached_after_turnover_damping.length > 0 && (
            <p className="warning-state" role="status">
              换手预算缩放之后仍被突破的上限：
              {view.caps_breached_after_turnover_damping.join("、")}。
              服务端选择如实上报而不是再裁一次。
            </p>
          )}

          {/* 权重 */}
          <table className="data-table">
            <caption>
              目标权重。所有权重按契约原样以字符串呈现，本页不对其做任何浮点运算。
            </caption>
            <thead>
              <tr>
                <th scope="col">标的</th>
                <th scope="col">层</th>
                <th scope="col">排名</th>
                <th scope="col">权重</th>
                <th scope="col">裁剪前</th>
                <th scope="col">是否被裁剪</th>
                <th scope="col">行业</th>
              </tr>
            </thead>
            <tbody>
              {view.targets.map((target) => (
                <tr key={target.subject}>
                  <th scope="row">{target.subject}</th>
                  <td>{target.tier}</td>
                  <td>{target.rank}</td>
                  <td>{target.weight}</td>
                  <td>{target.untrimmed_weight}</td>
                  <td>
                    {target.was_adjusted ? (
                      <span className="warning-inline">已裁剪</span>
                    ) : (
                      "否"
                    )}
                  </td>
                  <td>
                    {target.industry_code ?? (
                      <span className="warning-inline">无行业字段</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* 暴露 —— 只报契约真的给了的那半 */}
          <section aria-labelledby="portfolio-exposure-heading">
            <h3 id="portfolio-exposure-heading">暴露：只有总量口径</h3>
            <p className="empty-state">
              契约给出的是<strong>声明的上限</strong>与总投出权重，没有行业、风格或规模的分解。
              候选清单面不携带行业字段，故上表的行业列在出货路径上恒为空，行业暴露无法计算；
              这里不做任何替代推算。
            </p>
            <dl className="metric-row">
              <div>
                <dt>单标的上限</dt>
                <dd>{view.policy.limits.max_position_weight ?? "未声明"}</dd>
              </div>
              <div>
                <dt>总暴露上限</dt>
                <dd>{view.policy.limits.max_total_exposure ?? "未声明"}</dd>
              </div>
              <div>
                <dt>现金下限</dt>
                <dd>{view.policy.limits.min_cash_weight ?? "未声明"}</dd>
              </div>
              <div>
                <dt>行业上限</dt>
                <dd>{view.policy.limits.max_industry_weight ?? "未声明（本面不可执行）"}</dd>
              </div>
              <div>
                <dt>分层权重</dt>
                <dd>{view.policy.tier_weights.join(" / ")}</dd>
              </div>
            </dl>
          </section>

          {/* 服务端自己声明的限制 */}
          <section aria-labelledby="portfolio-limitations-heading">
            <h3 id="portfolio-limitations-heading">本次构建自带的限制（服务端声明）</h3>
            <ul className="check-list">
              {view.limitations.map((limitation) => (
                <li key={limitation.code}>
                  <code>{limitation.code}</code>
                  <p>{limitation.detail}</p>
                </li>
              ))}
            </ul>
          </section>

          {/* 具名缺口 */}
          <section aria-labelledby="portfolio-gaps-heading">
            <h3 id="portfolio-gaps-heading">本页无法作答的三项（无任何浏览器可达的路由）</h3>
            <ul className="check-list">
              {PORTFOLIO_CONTRACT_GAPS.map((gap) => (
                <li key={gap.code}>
                  <code>{gap.code}</code>
                  <p>{gap.detail}</p>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </section>
  );
}
