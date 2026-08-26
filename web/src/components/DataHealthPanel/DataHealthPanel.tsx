// V2-P5-015. Page ① 数据体检.
//
// Pure in `state`, like the four panels `V2-P5-019` converted, so every one of the nine
// kinds is reachable in a test by passing a prop rather than by stubbing a fetch. The
// route container (`pages/DataHealthPage`) owns the request; this owns the rendering.
//
// The page's job is narrower than "show the report": it is to make a *qualified* pass
// visibly different from an unqualified one. `panelHealthStateFrom` decides that, and the
// two things this component must not do are (a) render `is_clean` as a green tick without
// the qualification beside it, and (b) render the findings while hiding which checks never
// ran. `checks_waived` and `cross_checks[].ran` therefore have columns of their own rather
// than being folded into a count.

import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import type { PanelHealthReport } from "../../types";

type DataHealthPanelProps = {
  state: PanelState<PanelHealthReport>;
  datasets: string;
  years: string;
  asOf: string;
  exchange: string;
  onDatasetsChange: (value: string) => void;
  onYearsChange: (value: string) => void;
  onAsOfChange: (value: string) => void;
  onExchangeChange: (value: string) => void;
  onRun: () => void;
};

/** Seconds as a human span, or an explicit "unknown" — never a bare `null` rendered as blank.
 *
 * `event_age_seconds` and `fetch_age_seconds` are genuinely nullable on the wire (the
 * dataset may carry no event yet). A blank cell and "two days stale" must not look alike on
 * a freshness page, so the null case is spelled out. */
function formatAge(seconds: number | null): string {
  if (seconds === null) return "未知";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`;
  return `${(seconds / 86400).toFixed(1)} 天`;
}

export function DataHealthPanel(props: DataHealthPanelProps) {
  const report = panelData(props.state);

  return (
    <section className="panel data-health-panel" aria-labelledby="data-health-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">P1 / DATA HEALTH</p>
          <h2 id="data-health-heading">数据体检</h2>
        </div>
      </header>

      <div className="query-grid">
        <label>
          数据集（逗号分隔）
          <input
            value={props.datasets}
            onChange={(event) => props.onDatasetsChange(event.target.value)}
            placeholder="index_daily,index_member"
          />
        </label>
        <label>
          年份（逗号分隔）
          <input
            value={props.years}
            onChange={(event) => props.onYearsChange(event.target.value)}
            placeholder="2026"
          />
        </label>
        <label>
          可见性时钟
          <input
            type="datetime-local"
            value={props.asOf}
            onChange={(event) => props.onAsOfChange(event.target.value)}
          />
        </label>
        <label>
          交易所
          <input
            value={props.exchange}
            onChange={(event) => props.onExchangeChange(event.target.value)}
          />
        </label>
        <button className="button button--secondary" onClick={props.onRun}>
          运行体检
        </button>
      </div>

      <PanelNotice state={props.state} idleText="尚未运行数据体检" />

      {report !== null && (
        <div className="data-health-body">
          <dl className="metric-row">
            <div>
              <dt>体检时点</dt>
              <dd>{report.as_of}</dd>
            </div>
            <div>
              <dt>阻断</dt>
              <dd>{report.counts_by_severity.blocking}</dd>
            </div>
            <div>
              <dt>警告</dt>
              <dd>{report.counts_by_severity.warning}</dd>
            </div>
            <div>
              <dt>提示</dt>
              <dd>{report.counts_by_severity.notice}</dd>
            </div>
          </dl>

          <table className="data-table">
            <caption>各数据集就绪状态与新鲜度</caption>
            <thead>
              <tr>
                <th scope="col">数据集</th>
                <th scope="col">状态</th>
                <th scope="col">年份</th>
                <th scope="col">行数</th>
                <th scope="col">标的数</th>
                <th scope="col">事件时延</th>
                <th scope="col">抓取时延</th>
                <th scope="col">已跳过的检查</th>
              </tr>
            </thead>
            <tbody>
              {report.datasets.map((dataset) => (
                <tr key={dataset.dataset}>
                  <th scope="row">{dataset.dataset}</th>
                  <td>{dataset.state === "ready" ? "就绪" : "受阻"}</td>
                  <td>
                    {dataset.years_present.join("、") || "无"}
                    {/* Requested-but-absent years are the missing-coverage fact this page
                        exists to surface; showing only what is present hides it. */}
                    {dataset.years_requested.some(
                      (year) => !dataset.years_present.includes(year),
                    ) && (
                      <span className="warning-inline">
                        （缺 {dataset.years_requested
                          .filter((year) => !dataset.years_present.includes(year))
                          .join("、")}）
                      </span>
                    )}
                  </td>
                  <td>{dataset.row_count}</td>
                  <td>{dataset.subject_count}</td>
                  <td>{formatAge(dataset.event_age_seconds)}</td>
                  <td>{formatAge(dataset.fetch_age_seconds)}</td>
                  <td>
                    {dataset.checks_waived.length === 0 ? (
                      "全部已运行"
                    ) : (
                      <span className="warning-inline">{dataset.checks_waived.join("、")}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <section aria-labelledby="cross-checks-heading">
            <h3 id="cross-checks-heading">跨数据集检查</h3>
            <ul className="check-list">
              {report.cross_checks.map((check) => (
                <li key={check.name}>
                  <code>{check.name}</code>{" "}
                  {check.ran ? (
                    <span>已运行，{check.finding_count} 项发现</span>
                  ) : (
                    <span className="warning-inline">
                      未运行：{check.skipped_reason ?? "未给出原因"}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>

          {report.findings.length > 0 && (
            <section aria-labelledby="findings-heading">
              <h3 id="findings-heading">本次发现</h3>
              <ul className="check-list">
                {report.findings.map((finding, index) => (
                  <li key={`${finding.code}-${index}`}>
                    <code>{finding.code}</code> <span>[{finding.severity}]</span>{" "}
                    <span>{finding.detail}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* S48 / S72 / S73's explicit caveats. A sibling of `findings`, never merged with
              it: a structural boundary of a dataset and a defect of this fetch have
              different remedies, and a list that merged them would teach its reader to skim
              both. */}
          <section aria-labelledby="limitations-heading">
            <h3 id="limitations-heading">结构性限制（非本次抓取的缺陷）</h3>
            {report.limitations.length === 0 ? (
              <p className="empty-state">该请求范围内没有已登记的结构性限制。</p>
            ) : (
              <ul className="check-list">
                {report.limitations.map((limitation) => (
                  <li key={limitation.code}>
                    <code>{limitation.code}</code>
                    {limitation.datasets.length > 0 && (
                      <span> · {limitation.datasets.join("、")}</span>
                    )}
                    {limitation.detail !== undefined && <p>{limitation.detail}</p>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
