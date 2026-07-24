import { expect, test } from "@playwright/test";

const evidence = {
  schema_version: "evidence-snapshot/v1",
  evidence_id: "ev_e2e",
  content_hash: "a".repeat(64),
  subject: "000001.SZ",
  kind: "limit_up",
  timeline: {
    event_time: "2026-07-24T09:30:00Z",
    available_time: "2026-07-24T10:00:00Z",
    ingested_time: "2026-07-24T10:01:00Z",
    revision_time: "2026-07-24T10:00:00Z"
  },
  source_id: "synthetic",
  source_uri: "fixture://e2e",
  source_license: "CC0-1.0",
  redistribution: "allowed",
  summary: "端到端合成涨停证据。",
  payload: {
    schema: "a-share-evidence/v1",
    family: "market_event",
    facts: { close: 10.5, pct_change: 9.99, board_count: 1 },
    quality_flags: []
  }
};

test.beforeEach(async ({ page }) => {
  await page.route("**/health", (route) =>
    route.fulfill({ json: { status: "ok", version: "1.0.0" } })
  );
  await page.route("**/api/v1/evidence?**", (route) =>
    route.fulfill({ json: { items: [evidence] } })
  );
  await page.route("**/api/v1/research/run", (route) =>
    route.fulfill({
      json: {
        signal: {
          signal_id: "sig_e2e",
          direction: "bullish",
          strength: 0.65,
          confidence: 0.65,
          evidence_ids: ["ev_e2e"],
          risk_flags: []
        },
        decision: {
          decision_id: "dec_e2e",
          final_action: "watch",
          risk_decision: "pass",
          routing_path: ["market-agent", "risk-gate"]
        },
        manifest: { run_id: "e2e", status: "succeeded" },
        agent_results: []
      }
    })
  );
});

test("evidence-to-decision flow has no browser errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "证据先于观点" })).toBeVisible();
  await expect(page.getByText("服务正常")).toBeVisible();
  await page.getByRole("button", { name: "查询证据" }).click();
  await expect(page.getByText("端到端合成涨停证据。")).toBeVisible();
  await page.getByRole("button", { name: "运行研究" }).click();
  await expect(page.getByText("观察", { exact: true })).toBeVisible();
  await expect(page.getByText("market-agent → risk-gate")).toBeVisible();

  expect(errors).toEqual([]);
});

test("workbench stays within a mobile viewport", async ({ page }) => {
  await page.goto("/");
  const width = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth
  }));

  expect(width.scroll).toBeLessThanOrEqual(width.client);
  await expect(page.getByRole("button", { name: "查询证据" })).toBeVisible();
});
