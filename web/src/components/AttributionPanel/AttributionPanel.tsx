import { useMemo, useState } from "react";

import type { OutcomeInput, ResearchResult, ValidationResult } from "../../types";

type AttributionPanelProps = {
  research: ResearchResult | null;
  result: ValidationResult | null;
  loading: boolean;
  error: string | null;
  asOf: string;
  onRun: (outcome: OutcomeInput) => void;
};

function percent(value: number) {
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

export function AttributionPanel({
  research,
  result,
  loading,
  error,
  asOf,
  onRun
}: AttributionPanelProps) {
  const observationEnd = useMemo(() => {
    const end = new Date(asOf);
    end.setDate(end.getDate() + 5);
    end.setMinutes(end.getMinutes() - end.getTimezoneOffset());
    return end.toISOString().slice(0, 16);
  }, [asOf]);
  const [startPrice, setStartPrice] = useState(10);
  const [endPrice, setEndPrice] = useState(10.5);
  const [benchmarkReturn, setBenchmarkReturn] = useState(0);
  const [transactionCost, setTransactionCost] = useState(0.001);

  return (
    <section className="panel attribution-panel" aria-labelledby="attribution-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">04 / ATTRIBUTION</p>
          <h2 id="attribution-heading">结果归因</h2>
        </div>
        <button
          type="button"
          disabled={!research || loading}
          onClick={() =>
            onRun({
              observationStart: asOf,
              observationEnd,
              startPrice,
              endPrice,
              benchmarkReturn,
              transactionCost
            })
          }
        >
          {loading ? "计算中…" : "计算归因"}
        </button>
      </header>
      <div className="attribution-inputs">
        <label>
          起始价格
          <input
            type="number"
            min="0.0001"
            step="0.01"
            value={startPrice}
            onChange={(event) => setStartPrice(event.currentTarget.valueAsNumber)}
          />
        </label>
        <label>
          结束价格
          <input
            type="number"
            min="0.0001"
            step="0.01"
            value={endPrice}
            onChange={(event) => setEndPrice(event.currentTarget.valueAsNumber)}
          />
        </label>
        <label>
          基准收益
          <input
            type="number"
            step="0.001"
            value={benchmarkReturn}
            onChange={(event) => setBenchmarkReturn(event.currentTarget.valueAsNumber)}
          />
        </label>
        <label>
          交易成本
          <input
            type="number"
            min="0"
            step="0.001"
            value={transactionCost}
            onChange={(event) => setTransactionCost(event.currentTarget.valueAsNumber)}
          />
        </label>
      </div>
      {!research && !error && <p className="empty-state">完成一次研究后，录入未来观察结果。</p>}
      {research && !result && !error && (
        <p className="empty-state">按 5 日观察窗计算净主动收益与可对账归因。</p>
      )}
      {error && (
        <p className="error-state" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className="attribution-results">
          <div className="attribution-total">
            <span>净主动收益</span>
            <strong>{percent(result.net_active_return)}</strong>
            <code>{result.validation_id}</code>
          </div>
          <ol className="attribution-list">
            {result.attribution.map((term) => (
              <li key={`${term.category}-${term.name}`}>
                <span>{term.category}</span>
                <strong>{term.name}</strong>
                <data value={term.contribution}>{percent(term.contribution)}</data>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
