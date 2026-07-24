import type { ChangeEvent } from "react";

import type { ReplayReport } from "../../types";

type ReplayPanelProps = {
  report: ReplayReport | null;
  loading: boolean;
  error: string | null;
  onRun: (file: File) => void;
};

export function ReplayPanel({ report, loading, error, onRun }: ReplayPanelProps) {
  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (file) onRun(file);
  };
  return (
    <section className="panel replay-panel" aria-labelledby="replay-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">03 / REPLAY</p>
          <h2 id="replay-heading">回放验证</h2>
        </div>
        <label className={`file-action ${loading ? "file-action--disabled" : ""}`}>
          {loading ? "验证中…" : "选择冻结语料"}
          <input type="file" accept=".json,application/json" disabled={loading} onChange={onFile} />
        </label>
      </header>
      {!report && !error && (
        <p className="empty-state">上传版本化 ReplayCorpus，执行确定性与前视检查。</p>
      )}
      {error && (
        <p className="error-state" role="alert">
          {error}
        </p>
      )}
      {report && (
        <div className="replay-results">
          <dl className="metric-row">
            <div>
              <dt>成功</dt>
              <dd>
                {report.succeeded}/{report.total_cases}
              </dd>
            </div>
            <div>
              <dt>确定性</dt>
              <dd>{report.deterministic_replays}</dd>
            </div>
            <div>
              <dt>前视违规</dt>
              <dd>{report.look_ahead_violations}</dd>
            </div>
          </dl>
          <progress value={report.success_rate} max={1}>
            {Math.round(report.success_rate * 100)}%
          </progress>
          <p>{Math.round(report.success_rate * 100)}% 案例通过完整验证</p>
        </div>
      )}
    </section>
  );
}
