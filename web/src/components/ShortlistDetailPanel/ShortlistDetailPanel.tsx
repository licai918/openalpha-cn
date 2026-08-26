// V2-P5-016. Page ② part two: one shortlist answer, and each admitted name in it.
//
// The row asks for 排序/分数/置信度/排名变化/证据链/失效条件/可交易性告警 plus, per list, the
// universe it was cut from. Two of those need saying out loud.
//
// **What this panel renders as candidates is `admitted`, never `funnel.shortlist`.** They
// differ on exactly the answer that matters: on a refused list the funnel's list is full and
// `admitted` is `null`. `shortlistStateFrom` turns that into `blocked`, which carries no
// data, so this component never sees a refused answer at all — but the choice of field is
// still load-bearing, because a future edit reaching for `funnel.shortlist` here would put
// names from a refused list back on screen and every state test would stay green.
//
// **排名变化 is not rendered, because the contract carries no previous rank.** A shortlist
// answer is a single point in time; `ShortlistAnswer` has `rank` and no `previous_rank`, and
// the endpoint serves stored answers individually rather than as a series. Rendering a
// movement arrow would mean inventing the comparison, and an invented "▲2" is indistinguish-
// able on screen from a measured one. See this row's report for the exact field that would
// be needed.

import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import type { ShortlistAnswer } from "../../types";

type ShortlistDetailPanelProps = {
  state: PanelState<ShortlistAnswer>;
  shortlistId: string;
};

function percent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

export function ShortlistDetailPanel(props: ShortlistDetailPanelProps) {
  const answer = panelData(props.state);

  return (
    <section className="panel shortlist-detail-panel" aria-labelledby="shortlist-detail-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">P2 / SHORTLIST DETAIL</p>
          <h2 id="shortlist-detail-heading">个股详情</h2>
          <p className="mono-note">
            <code>{props.shortlistId}</code>
          </p>
        </div>
      </header>

      <PanelNotice state={props.state} idleText="尚未载入该清单" />

      {answer !== null && (
        <div className="shortlist-detail-body">
          {/* S72: every list states the universe it was cut from and how it was scored.
              Deliberately *not* labelled a data-licensing statement: `ShortlistAnswer`
              carries no redistribution field, and a licence claim this payload cannot
              support would be exactly the "mirror implying more than the contract holds"
              defect. What the contract does carry — tier, transform, exchange, years and
              the universe count — is stated, and nothing else is. */}
          <section aria-labelledby="declaration-heading">
            <h3 id="declaration-heading">股票池与打分口径</h3>
            <dl className="metric-row">
              <div>
                <dt>数据层级</dt>
                <dd>{answer.declaration.tier}</dd>
              </div>
              <div>
                <dt>变换</dt>
                <dd>{answer.declaration.transform ?? "无"}</dd>
              </div>
              <div>
                <dt>中性化</dt>
                <dd>{answer.declaration.neutralization ?? "无"}</dd>
              </div>
              <div>
                <dt>交易所</dt>
                <dd>{answer.declaration.exchange}</dd>
              </div>
              <div>
                <dt>年份</dt>
                <dd>{answer.declaration.years.join("、")}</dd>
              </div>
              <div>
                <dt>横截面股票池</dt>
                <dd>{answer.cross_section.universe_count}</dd>
              </div>
              <div>
                <dt>定价交易日</dt>
                <dd>{answer.cross_section.pricing_session}</dd>
              </div>
            </dl>
            <ul className="check-list">
              {answer.declaration.components.map((component) => (
                <li key={component.factor_id}>
                  <code>{component.factor}</code> · 权重 {component.weight}
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="candidates-heading">
            <h3 id="candidates-heading">入选候选</h3>
            <table className="data-table">
              <caption>排序、分数、置信度与证据链</caption>
              <thead>
                <tr>
                  <th scope="col">排名</th>
                  <th scope="col">标的</th>
                  <th scope="col">分数</th>
                  <th scope="col">方向</th>
                  <th scope="col">置信度</th>
                  <th scope="col">风险标记</th>
                  <th scope="col">证据链（运行清单）</th>
                </tr>
              </thead>
              <tbody>
                {(answer.admitted ?? []).map((candidate) => (
                  <tr key={candidate.subject}>
                    <td>{candidate.rank}</td>
                    <th scope="row">{candidate.subject}</th>
                    <td>{candidate.score.toFixed(4)}</td>
                    <td>{candidate.direction}</td>
                    <td>{percent(candidate.confidence)}</td>
                    <td>
                      {candidate.risk_flags.length === 0 ? (
                        "无"
                      ) : (
                        <span className="warning-inline">
                          {candidate.risk_flags.join("、")}
                        </span>
                      )}
                    </td>
                    <td>
                      <code>{candidate.run_manifest_id}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* 可交易性告警. The named rows AND the residual: `untradeable` is capped
              server-side by MAX_NAMED_UNTRADEABLE, so showing only the array under-reports
              by exactly `untradeable_not_named`. */}
          <section aria-labelledby="tradeability-heading">
            <h3 id="tradeability-heading">可交易性告警</h3>
            <dl className="metric-row">
              <div>
                <dt>可交易比例</dt>
                <dd>{percent(answer.measurement.tradable_ratio)}</dd>
              </div>
              <div>
                <dt>已研究比例</dt>
                <dd>{percent(answer.measurement.researched_ratio)}</dd>
              </div>
              <div>
                <dt>排序龄期</dt>
                <dd>{answer.measurement.ranking_age_days} 天</dd>
              </div>
            </dl>
            {answer.funnel.untradeable.length === 0 &&
            answer.funnel.untradeable_not_named === 0 ? (
              <p className="empty-state">本次横截面没有被剔除的不可交易标的。</p>
            ) : (
              <>
                <ul className="check-list">
                  {answer.funnel.untradeable.map((item) => (
                    <li key={item.subject}>
                      <strong>{item.subject}</strong> · {item.verdict} · {item.reason}
                    </li>
                  ))}
                </ul>
                {answer.funnel.untradeable_not_named > 0 && (
                  <p className="warning-inline">
                    另有 {answer.funnel.untradeable_not_named} 只不可交易标的未被逐一列名。
                  </p>
                )}
              </>
            )}
          </section>

          {/* 失效条件: the bars this list was read against, on a cleared list too. A list
              that scraped over a bar and one that sailed over it are different facts. */}
          <section aria-labelledby="blocks-heading">
            <h3 id="blocks-heading">失效条件</h3>
            {answer.blocks.length === 0 ? (
              <p className="empty-state">本次没有触发任何门槛。</p>
            ) : (
              <ul className="check-list">
                {answer.blocks.map((block) => (
                  <li key={block.code}>
                    <code>{block.code}</code> · 实测 {block.measured} / 要求 {block.required} ·{" "}
                    {block.detail}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {answer.unresearched.length > 0 && (
            <section aria-labelledby="unresearched-heading">
              <h3 id="unresearched-heading">证据链未闭合</h3>
              <p className="warning-inline">{answer.unresearched.join("、")}</p>
            </section>
          )}
        </div>
      )}
    </section>
  );
}
