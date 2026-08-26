// V2-P5-017. Page ③ part two: one sealed experiment, read.
//
// Pure in `state` like every panel since `V2-P5-019`, so all nine kinds are reachable by
// passing a prop.
//
// Four of the row's seven items are rendered here (因子定义 / IC / 分组 / 三档对比), a fifth
// is rendered **under a corrected heading** (相关性), and two are named as absences rather
// than drawn. Three rendering obligations come from the contract itself rather than from
// taste, and each is asserted in the test file:
//
//   1. **The acceptance step is marked.** `factor_view.ACCEPTANCE_STEP` exists because the
//      grid "treats all three as equals on purpose" while only one carries the finding, and
//      because "a face that printed six equal rows left the reader to know which". Six equal
//      rows is precisely what this table must not be.
//   2. **`coverage` sits beside every statistic.** A `null` mean_ic under
//      `insufficient_as_ofs` means the question was not answerable — not that the factor
//      scored zero — and a blank cell renders those two as the same thing.
//   3. **The survival correlation is labelled raw-vs-tier, not factor-vs-factor.** It is the
//      same factor on both sides (`left_key === right_key` on the shipped path). A heading
//      reading 因子相关性 over this number would be a misreport, so the heading says what
//      the number is and `FACTOR_LAB_CONTRACT_GAPS` says what is missing.

import { FACTOR_LAB_CONTRACT_GAPS } from "../../contractState";
import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import type { FactorExperimentEnvelope, FactorTierAttribution } from "../../types";

type FactorExperimentPanelProps = {
  state: PanelState<FactorExperimentEnvelope>;
  experimentId: string;
};

/** The one step the acceptance criterion is decided on; mirrors `factor_view.ACCEPTANCE_STEP`. */
const ACCEPTANCE_FROM = "processed";
const ACCEPTANCE_TO = "neutralized";

/**
 * A statistic that is genuinely `null` on the wire, spelled rather than blanked.
 *
 * `DataHealthPanel.formatAge`'s rule, for its reason: on a page whose job is to separate
 * "measured and it is zero" from "never measured", an empty cell is the one rendering that
 * makes them look alike.
 */
function stat(value: number | null, digits = 4): string {
  return value === null ? "—" : value.toFixed(digits);
}

function isAcceptanceCell(cell: FactorTierAttribution): boolean {
  return cell.from_tier === ACCEPTANCE_FROM && cell.to_tier === ACCEPTANCE_TO;
}

export function FactorExperimentPanel(props: FactorExperimentPanelProps) {
  const envelope = panelData(props.state);
  const artifact = envelope?.document.artifact;
  const definition = artifact?.spec.ic.definition;

  return (
    <section className="panel factor-experiment-panel" aria-labelledby="factor-experiment-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">P3 / FACTOR LABORATORY</p>
          <h2 id="factor-experiment-heading">因子实验 {props.experimentId}</h2>
        </div>
      </header>

      <PanelNotice state={props.state} idleText="尚未载入该因子实验" />

      {envelope !== null && artifact !== undefined && definition !== undefined && (
        <div className="factor-experiment-body">
          {/* 因子定义 */}
          <section aria-labelledby="factor-definition-heading">
            <h3 id="factor-definition-heading">因子定义</h3>
            <dl className="metric-row">
              <div>
                <dt>因子</dt>
                <dd>
                  <code>
                    {definition.key}/v{definition.version}
                  </code>
                </dd>
              </div>
              <div>
                <dt>族</dt>
                <dd>{definition.family}</dd>
              </div>
              <div>
                <dt>方向</dt>
                <dd>
                  {definition.direction === "higher_is_better" ? "越大越好" : "越小越好"}
                </dd>
              </div>
              <div>
                <dt>回看窗口</dt>
                <dd>
                  {definition.lookback_sessions === null
                    ? "未声明"
                    : `${definition.lookback_sessions} 个交易日`}
                </dd>
              </div>
              <div>
                <dt>持有期</dt>
                <dd>{artifact.spec.horizon_sessions} 个交易日</dd>
              </div>
              <div>
                <dt>IC 方法</dt>
                <dd>{artifact.spec.ic.method}</dd>
              </div>
              <div>
                <dt>留存下限</dt>
                <dd>{artifact.spec.retention_floor}</dd>
              </div>
              <div>
                <dt>代码提交</dt>
                <dd>
                  <code>{artifact.spec.code_commit}</code>
                </dd>
              </div>
            </dl>
            <p className="empty-state">
              所需字段：{definition.required_fields.join("、")}
            </p>
          </section>

          {/* 三档对比 + IC + 分组，一张表 */}
          <section aria-labelledby="factor-tiers-heading">
            <h3 id="factor-tiers-heading">三档对比：IC 与分组</h3>
            <table className="data-table">
              <caption>
                raw / processed / neutralized 三档，各自的 IC 与分组组合统计；覆盖码与统计量并列，
                因为「测出来是 0」与「没测」不是同一个答案。
              </caption>
              <thead>
                <tr>
                  <th scope="col">档位</th>
                  <th scope="col">IC 覆盖</th>
                  <th scope="col">mean IC</th>
                  <th scope="col">ICIR</th>
                  <th scope="col">符号一致性</th>
                  <th scope="col">分组覆盖</th>
                  <th scope="col">分组数</th>
                  <th scope="col">多空价差</th>
                  <th scope="col">胜率</th>
                  <th scope="col">名义换手</th>
                </tr>
              </thead>
              <tbody>
                {artifact.tiers.map((tier) => (
                  <tr key={tier.tier}>
                    <th scope="row">{tier.tier}</th>
                    <td>
                      {tier.ic.coverage === "measured" ? (
                        `已测量（${tier.ic.measured_count}）`
                      ) : (
                        <span className="warning-inline">{tier.ic.coverage}</span>
                      )}
                    </td>
                    <td>{stat(tier.ic.mean_ic)}</td>
                    <td>{stat(tier.ic.icir, 3)}</td>
                    <td>{stat(tier.ic.sign_consistency, 3)}</td>
                    <td>
                      {tier.portfolio.coverage === "measured" ? (
                        `已测量（${tier.portfolio.measured_count}）`
                      ) : (
                        <span className="warning-inline">{tier.portfolio.coverage}</span>
                      )}
                    </td>
                    <td>{tier.portfolio.group_count}</td>
                    <td>{stat(tier.portfolio.mean_spread)}</td>
                    <td>{stat(tier.portfolio.hit_rate, 3)}</td>
                    <td>{stat(tier.turnover.mean_name_turnover, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* 分组，逐组 */}
          <section aria-labelledby="factor-groups-heading">
            <h3 id="factor-groups-heading">分组净收益（按原始值升序）</h3>
            <table className="data-table">
              <caption>
                每组的平均单期净收益，与同一组的毛收益并列——两者之差就是平均成本拖累。
              </caption>
              <thead>
                <tr>
                  <th scope="col">档位</th>
                  {artifact.tiers[0]?.portfolio.group_mean_net_returns.map((_, index) => (
                    <th scope="col" key={index}>
                      第 {index + 1} 组
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {artifact.tiers.map((tier) => (
                  <tr key={tier.tier}>
                    <th scope="row">{tier.tier}</th>
                    {tier.portfolio.group_mean_net_returns.map((net, index) => (
                      <td key={index}>
                        {stat(net)}
                        <span className="warning-inline">
                          {" "}
                          / {stat(tier.portfolio.group_mean_gross_returns[index] ?? null)}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* 归因网格 */}
          <section aria-labelledby="factor-attribution-heading">
            <h3 id="factor-attribution-heading">档位归因网格</h3>
            <table className="data-table">
              <caption>
                六个格子，步骤为主、统计量为次，与契约的 ATTRIBUTION_CELL_ORDER 同序。
                验收判据只由 processed→neutralized 这一步决定，故该行单独标出。
              </caption>
              <thead>
                <tr>
                  <th scope="col">步骤</th>
                  <th scope="col">统计量</th>
                  <th scope="col">起值</th>
                  <th scope="col">止值</th>
                  <th scope="col">留存</th>
                  <th scope="col">判定</th>
                </tr>
              </thead>
              <tbody>
                {artifact.attributions.map((cell) => (
                  <tr key={`${cell.from_tier}-${cell.to_tier}-${cell.statistic}`}>
                    <th scope="row">
                      {cell.from_tier}→{cell.to_tier}
                      {isAcceptanceCell(cell) && (
                        <span className="warning-inline">（验收判据）</span>
                      )}
                    </th>
                    <td>{cell.statistic}</td>
                    <td>{stat(cell.from_value)}</td>
                    <td>{stat(cell.to_value)}</td>
                    <td>{stat(cell.retention, 3)}</td>
                    <td>
                      {cell.verdict === "not_measured" || cell.verdict === "no_baseline" ? (
                        <span className="warning-inline">{cell.verdict}</span>
                      ) : (
                        cell.verdict
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* 相关性 —— 标题就是它实际测的那个问题 */}
          <section aria-labelledby="factor-survival-heading">
            <h3 id="factor-survival-heading">档位存活相关性（同一因子 raw 对本档）</h3>
            <p className="empty-state">
              这不是因子与因子之间的相关性。左右两侧是同一个因子的两个档位，回答的是
              「变换与中性化之后还剩多少排序」。
            </p>
            <ul className="check-list">
              {artifact.tiers.map((tier) =>
                tier.survival === null ? (
                  <li key={tier.tier}>
                    <code>{tier.tier}</code> · 基准档本身，没有可比较的上一档
                  </li>
                ) : (
                  <li key={tier.tier}>
                    <code>
                      {tier.survival.left_tier}→{tier.survival.right_tier}
                    </code>{" "}
                    <span>
                      均值 {stat(tier.survival.mean_correlation, 3)}、绝对值均值{" "}
                      {stat(tier.survival.mean_abs_correlation, 3)}、判定 {tier.survival.verdict}
                    </span>
                    {tier.survival.coverage !== "measured" && (
                      <span className="warning-inline">（{tier.survival.coverage}）</span>
                    )}
                  </li>
                ),
              )}
            </ul>
          </section>

          {/* 具名缺口 */}
          <section aria-labelledby="factor-gaps-heading">
            <h3 id="factor-gaps-heading">本页无法作答的两项（契约无字段，未杜撰）</h3>
            <ul className="check-list">
              {FACTOR_LAB_CONTRACT_GAPS.map((gap) => (
                <li key={gap.code}>
                  <code>{gap.code}</code>
                  <p>{gap.detail}</p>
                </li>
              ))}
            </ul>
          </section>

          <dl className="metric-row">
            <div>
              <dt>内容摘要</dt>
              <dd>
                <code>{envelope.content_digest.slice(0, 16)}…</code>
              </dd>
            </div>
            <div>
              <dt>本次取用</dt>
              <dd>{envelope.write === "unchanged" ? "读取既有封存件" : "新建"}</dd>
            </div>
            <div>
              <dt>封存时间</dt>
              <dd>{envelope.document.built_at}</dd>
            </div>
          </dl>
        </div>
      )}
    </section>
  );
}
