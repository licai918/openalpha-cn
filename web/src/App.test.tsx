import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const evidence = {
  evidence_id: "ev_123",
  content_hash: "a".repeat(64),
  schema_version: "evidence-snapshot/v1",
  subject: "000001.SZ",
  kind: "limit_up",
  timeline: {
    event_time: "2026-07-24T09:30:00Z",
    available_time: "2026-07-24T10:00:00Z",
    ingested_time: "2026-07-24T10:01:00Z",
    revision_time: "2026-07-24T10:00:00Z"
  },
  source_id: "synthetic",
  source_uri: "fixture://limit-up",
  source_license: "CC0-1.0",
  redistribution: "allowed",
  summary: "Synthetic limit-up evidence.",
  payload: {
    schema: "a-share-evidence/v1",
    family: "market_event",
    facts: { close: 10.5, pct_change: 9.99, board_count: 1 },
    quality_flags: []
  }
};

describe("OpenAlpha workbench", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/health") {
          return Response.json({ status: "ok", version: "1.0.0" });
        }
        if (url.startsWith("/api/v1/evidence")) {
          return Response.json({ items: [evidence] });
        }
        if (url === "/api/v1/research/run") {
          return Response.json({
            signal: {
              signal_id: "sig_123",
              direction: "bullish",
              strength: 0.65,
              confidence: 0.65,
              evidence_ids: ["ev_123"],
              risk_flags: []
            },
            decision: {
              decision_id: "dec_123",
              final_action: "watch",
              risk_decision: "pass",
              routing_path: ["market-agent", "risk-gate"]
            },
            manifest: { run_id: "web-run", status: "succeeded" },
            agent_results: []
          });
        }
        if (url === "/api/v1/backtests/validate") {
          return Response.json({
            validation_id: "val_123",
            signal_id: "sig_123",
            decision_id: "dec_123",
            realized_return: 0.1,
            benchmark_return: 0.02,
            transaction_cost: 0.005,
            net_active_return: 0.075,
            unexplained_return: 0.06,
            confidence: 0.65,
            attribution: [
              { category: "rule", name: "transaction-cost", contribution: 0.015 }
            ]
          });
        }
        throw new Error(`Unexpected URL: ${url}`);
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows service readiness and an explicit empty evidence state", async () => {
    render(<App />);

    expect(await screen.findByText("服务正常")).toBeInTheDocument();
    expect(screen.getByText("尚未查询证据")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运行研究" })).toBeDisabled();
  });

  it("queries evidence and renders the decision trail", async () => {
    render(<App />);
    await screen.findByText("服务正常");

    fireEvent.click(screen.getByRole("button", { name: "查询证据" }));
    expect(await screen.findByText("Synthetic limit-up evidence.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "运行研究" }));

    await waitFor(() => {
      expect(screen.getByText("观察")).toBeInTheDocument();
    });
    expect(screen.getByText("market-agent → risk-gate")).toBeInTheDocument();
    expect(screen.getAllByText("ev_123")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "计算归因" }));
    expect(await screen.findByText("+7.50%")).toBeInTheDocument();
    expect(screen.getByText("transaction-cost")).toBeInTheDocument();
    // V2-P5-006: the terms shown add to +1.50% against a +7.50% net active return, and the
    // difference is on screen rather than folded into whichever term came last.
    expect(screen.getByText("未归因残差")).toBeInTheDocument();
    expect(screen.getByText("+6.00%")).toBeInTheDocument();
  });
});
