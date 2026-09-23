// V2-P5-017. Page ③ part three: 模型样本外指标, as the register actually reports them.
//
// **What this panel is, and what it deliberately is not.** The row asks for out-of-sample
// model metrics. The only *statistics* the model plane computes — `mean_rank_ic`,
// `rank_icir`, per-fold `scored_ratio` — come from `POST /api/v1/models/evaluate`, and that
// route stores nothing: there is no listing route and no retrieval route for a past
// evaluation, so a page cannot show one on load. Re-POSTing an evaluation needs fourteen
// declared parameters with no defaults, none of which a browser can honestly invent (a
// `code_commit`, a feature version, a fold schedule). So this panel reads the one thing that
// *is* addressable and stored — the prediction register — and page ④'s gap list is where the
// evaluation route's absence is recorded.
//
// **The rendering obligation this panel exists to honour.** `_prediction_index_entry` puts
// `standing_proves` and `standing_does_not_prove` on every row, and says why in as many
// words: a rendering printing `"standing": "forward"` and stopping "turns a local-first
// bookkeeping fact into what reads like an attestation, and a column in a table does that at
// least as fast as a field in a document". So the two sentences are rendered per row, from
// the contract's own text, and never summarised.

import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import type { PredictionIndex, PredictionIndexEntry } from "../../types";

type PredictionRegisterPanelProps = {
  state: PanelState<PredictionIndex>;
};

/** The three standings, as the register's own vocabulary rather than as a verdict.
 *
 * Keyed by the union rather than by `string`, so the map is *total* by construction: adding a
 * fourth standing to the mirror stops this file compiling instead of silently rendering a
 * fallback. That is why there is no `?? entry.standing` at the call site — it would be an
 * unreachable branch standing in for a compile-time guarantee. */
const STANDING_LABEL: Record<PredictionIndexEntry["standing"], string> = {
  forward: "forward（先于结果可知而持有）",
  unwitnessed: "unwitnessed（声称在先，但本机时钟读在其后）",
  backfill: "backfill（回溯重算）",
};

export function PredictionRegisterPanel(props: PredictionRegisterPanelProps) {
  const index = panelData(props.state);

  return (
    <section
      className="panel prediction-register-panel"
      aria-labelledby="prediction-register-heading"
    >
      <header className="panel-heading">
        <div>
          <p className="eyebrow">P3 / MODEL PREDICTIONS</p>
          <h2 id="prediction-register-heading">模型预测登记（样本外立场）</h2>
        </div>
      </header>

      <PanelNotice state={props.state} idleText="尚未载入预测登记" />

      {index !== null && (
        <div className="prediction-register-body">
          <table className="data-table">
            <caption>
              按保管顺序列出（并非内容摘要顺序）。standing 一列不是评分，而是这份记录在结果可知
              之前是否真的被本机持有过。
            </caption>
            <thead>
              <tr>
                <th scope="col">记录</th>
                <th scope="col">立场</th>
                <th scope="col">模型</th>
                <th scope="col">横截面时点</th>
                <th scope="col">入库时间</th>
                <th scope="col">结果可知时间</th>
                <th scope="col">持有期</th>
                <th scope="col">已打分 / 已给出</th>
              </tr>
            </thead>
            <tbody>
              {index.predictions.map((entry) => (
                <tr key={entry.record_id}>
                  <th scope="row">
                    <code>{entry.record_id}</code>
                  </th>
                  <td>
                    {entry.standing === "forward" ? (
                      STANDING_LABEL[entry.standing]
                    ) : (
                      <span className="warning-inline">{STANDING_LABEL[entry.standing]}</span>
                    )}
                  </td>
                  <td>{entry.model_name}</td>
                  <td>{entry.as_of}</td>
                  <td>{entry.recorded_at}</td>
                  <td>{entry.outcome_known_at}</td>
                  <td>{entry.horizon}</td>
                  <td>
                    {entry.scored_count} / {entry.offered_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* The two sentences, per row, verbatim. Never folded into one blanket note: they
              differ per standing, and the whole point is that `forward` also has a
              `does_not_prove` half. */}
          <section aria-labelledby="prediction-standing-heading">
            <h3 id="prediction-standing-heading">每条记录的立场证明了什么、没有证明什么</h3>
            <ul className="check-list">
              {index.predictions.map((entry) => (
                <li key={entry.record_id}>
                  <code>{entry.record_id}</code> · <span>{entry.standing}</span>
                  <p>证明：{entry.standing_proves}</p>
                  <p className="warning-inline">不证明：{entry.standing_does_not_prove}</p>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </section>
  );
}
