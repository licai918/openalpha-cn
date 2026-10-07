// The desktop golden flow: evidence → decision → replay → **attribution**.
//
// V2-P5-021 rewrote this file, and the rewrite is not cosmetic. Audit finding **F64** says the
// flow "从未走到归因" — never reached attribution — because `page.route` did not stub
// `/api/v1/backtests/validate`. That was true, and the reason nothing caught it is that no
// assertion here was ever about a *request*: `toBeVisible()` on a heading passes whether or
// not the panel behind it ever spoke to the server. The flow now runs to the end, and
// `StubbedApi.expectReached("validate")` is the assertion that says so.

import { expect, test } from "@playwright/test";

import { WorkbenchPage } from "./pages/WorkbenchPage";
import { StubbedApi } from "./stubs";

test("evidence to attribution, with the browser silent throughout", async ({ page }) => {
  const api = await StubbedApi.install(page);
  const workbench = new WorkbenchPage(page);

  await workbench.goto();
  await expect(workbench.serviceStatus()).toBeVisible();
  await api.expectRequestedOnMount("health");

  // Nothing downstream may have been requested yet. This is the half that catches a panel
  // fetching on mount for a question the user has not asked.
  api.expectNotReached("evidence");
  api.expectNotReached("research");
  api.expectNotReached("validate");

  await workbench.queryEvidence();
  await expect(workbench.evidenceSummary()).toBeVisible();
  await api.expectRequested("evidence");

  await workbench.runResearch();
  await expect(workbench.finalAction()).toBeVisible();
  await expect(workbench.routingPath()).toBeVisible();
  await api.expectRequested("research");

  // F64's gap. The button is disabled until a research result exists, so reaching an enabled
  // 计算归因 is itself part of the claim — this cannot be faked by clicking earlier.
  await workbench.runAttribution();
  await api.expectRequested("validate");

  // `net_active_return` is 0.075 and `unexplained_return` is 0.06 in the shared fixture, and
  // the two are asserted separately on purpose: `V2-P5-006` is the row that stopped the
  // residual being silently spread across the named terms, so a panel that printed only a
  // total would be the defect returning with a green test.
  await expect(workbench.netActiveReturn()).toContainText("+7.50%");
  await expect(workbench.unexplainedResidual()).toContainText("+6.00%");
  await expect(workbench.attributionTerm("transaction-cost")).toBeVisible();

  workbench.expectQuietBrowser();
});

test("workbench never scrolls horizontally at the desktop viewport", async ({ page }) => {
  // V2-P5-014 renamed this from "stays within a mobile viewport". The assertion is
  // unchanged and still worth making — a table or a nav bar that pushes the document
  // sideways is a real defect at any width — but the old name claimed a scope PRD
  // Decision 15 puts out of range, and it was the only thing justifying the
  // `mobile-chromium` project that ran every test here a second time at 393×851.
  await StubbedApi.install(page);
  const workbench = new WorkbenchPage(page);

  await workbench.goto();
  await workbench.expectNoHorizontalOverflow();
  await expect(page.getByRole("button", { name: "查询证据" })).toBeVisible();
});
