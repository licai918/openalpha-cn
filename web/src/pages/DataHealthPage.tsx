// V2-P5-015. The route container for page ①.
//
// Starts `idle` rather than fetching on mount, and that is a decision rather than an
// omission: `GET /api/v1/panel/health` has four *required* query parameters (`dataset`,
// `year`, `as_of`, `exchange`), so a mount-time request would have to invent a dataset and
// a year on the user's behalf and then report health for whatever it guessed. `idle` — the
// ninth kind `panelState.ts` kept precisely because "you have not asked" and "we asked and
// there is nothing" are different facts — is the honest state before the question is put.

import { useState } from "react";

import { getPanelHealth } from "../api/client";
import { DataHealthPanel } from "../components/DataHealthPanel/DataHealthPanel";
import { panelHealthStateFrom } from "../contractState";
import type { PanelState } from "../panelState";
import type { PanelHealthReport } from "../types";

function localClockValue() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

/** Split a comma-separated field, dropping blanks so a trailing comma is not a request for
 * a dataset named "". */
function splitList(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export function DataHealthPage() {
  const [datasets, setDatasets] = useState("index_daily");
  const [years, setYears] = useState(String(new Date().getFullYear()));
  const [asOf, setAsOf] = useState(localClockValue);
  const [exchange, setExchange] = useState("XSHG");
  const [state, setState] = useState<PanelState<PanelHealthReport>>({ kind: "idle" });

  const run = async () => {
    const requestedDatasets = splitList(datasets);
    const requestedYears = splitList(years)
      .map((year) => Number(year))
      .filter((year) => Number.isInteger(year));

    // Refused here rather than sent: the endpoint answers 422 for a missing `dataset`, and
    // a validation error rendered as a failed health check reads as "the panel is broken"
    // when what happened is "the form was empty".
    if (requestedDatasets.length === 0 || requestedYears.length === 0) {
      setState({ kind: "failed", error: "请至少填写一个数据集与一个年份。" });
      return;
    }

    setState({ kind: "loading" });
    try {
      const report = await getPanelHealth({
        datasets: requestedDatasets,
        years: requestedYears,
        asOf: new Date(asOf).toISOString(),
        exchange,
        calendar: true,
      });
      setState(panelHealthStateFrom(report));
    } catch (error) {
      setState({
        kind: "failed",
        error: error instanceof Error ? error.message : "数据体检失败",
      });
    }
  };

  return (
    <DataHealthPanel
      state={state}
      datasets={datasets}
      years={years}
      asOf={asOf}
      exchange={exchange}
      onDatasetsChange={setDatasets}
      onYearsChange={setYears}
      onAsOfChange={setAsOf}
      onExchangeChange={setExchange}
      onRun={run}
    />
  );
}
