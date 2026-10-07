import type { ChangeEvent } from "react";

import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import type { Evidence } from "../../types";

type EvidencePanelProps = {
  subject: string;
  asOf: string;
  /** V2-P5-019: `evidence` and `error` used to be separate props, which made
   * `{state: "error", evidence: [...]}` representable — a failure still rendering the
   * previous query's rows. The payload now travels inside the state that admits it. */
  state: PanelState<Evidence[]>;
  onSubjectChange: (value: string) => void;
  onAsOfChange: (value: string) => void;
  onQuery: () => void;
  onImport: (file: File) => void;
};

function formatClock(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
    hour12: false
  }).format(new Date(value));
}

export function EvidencePanel(props: EvidencePanelProps) {
  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (file) props.onImport(file);
  };
  const evidence = panelData(props.state);

  return (
    <section className="panel evidence-panel" aria-labelledby="evidence-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">01 / EVIDENCE</p>
          <h2 id="evidence-heading">证据时间线</h2>
        </div>
        <label className="file-action">
          导入 Provider 批次
          <input type="file" accept=".json,application/json" onChange={onFile} />
        </label>
      </header>

      <div className="query-grid">
        <label>
          标的代码
          <input
            value={props.subject}
            onChange={(event) => props.onSubjectChange(event.target.value)}
            placeholder="000001.SZ"
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
        <button className="button button--secondary" onClick={props.onQuery}>
          查询证据
        </button>
      </div>

      <PanelNotice state={props.state} idleText="尚未查询证据" />
      {evidence !== null && (
        <ol className="evidence-list">
          {evidence.map((item) => (
            <li key={item.evidence_id}>
              <div className="evidence-marker" aria-hidden="true" />
              <article>
                <header>
                  <span className="kind-label">{item.kind}</span>
                  <time dateTime={item.timeline.available_time}>
                    {formatClock(item.timeline.available_time)}
                  </time>
                </header>
                <p>{item.summary}</p>
                <footer>
                  <code>{item.evidence_id}</code>
                  <span>{item.source_id}</span>
                </footer>
              </article>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
