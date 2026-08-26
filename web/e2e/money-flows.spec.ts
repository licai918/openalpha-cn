// V2-P5-021. One money flow per page, through the page objects.
//
// "Money flow" here means the sequence a user performs to get the answer the page exists for —
// and the assertion in each is a *request*, not a rendered string. Audit finding F64 is what
// makes that distinction load-bearing: the previous suite asserted only on text, so a panel
// that never called its endpoint was indistinguishable from one that did.
//
// Two of the four pages start `idle` by design (`DataHealthPage`, `PortfolioPage`: their
// endpoints have required parameters the app must not invent) and two fetch on mount
// (`ShortlistPage`, `FactorLabPage`: their endpoints take nothing, so `idle` would be a state
// the user could only leave by pressing a button that adds no information). Each flow asserts
// which of the two its page is, because that decision is the page's contract with the user and
// it is invisible to a test that only reads the screen after it settles.

import { expect, test } from "@playwright/test";

import { DataHealthPage } from "./pages/DataHealthPage";
import { FactorLabPage } from "./pages/FactorLabPages";
import { PortfolioPage } from "./pages/PortfolioPage";
import { ShortlistIndexPage } from "./pages/ShortlistPages";
import { EXPERIMENT_ID, SHORTLIST_ID, StubbedApi } from "./stubs";

test.describe("页 ① 数据体检", () => {
  test("asks nothing until it is asked, then reports the panel it was asked about", async ({
    page,
  }) => {
    const api = await StubbedApi.install(page);
    const health = new DataHealthPage(page);

    await health.goto();
    await expect(health.idleNotice()).toBeVisible();
    // The decision `DataHealthPage.tsx` documents, asserted rather than trusted: four required
    // query parameters means a mount-time request would have to invent a dataset and a year.
    api.expectNotReached("panelHealth");

    await health.ask({ datasets: "index_daily", years: "2026", exchange: "XSHG" });
    await api.expectRequested("panelHealth");
    await expect(health.crossChecks()).toBeVisible();

    health.expectQuietBrowser();
  });

  test("an empty form is refused here rather than sent to a 422", async ({ page }) => {
    const api = await StubbedApi.install(page);
    const health = new DataHealthPage(page);

    await health.goto();
    await health.ask({ datasets: "", years: "" });

    await expect(health.refusal()).toContainText("请至少填写一个数据集与一个年份。");
    // The point of refusing locally: a validation error rendered as a failed health check
    // reads as "the panel is broken" when what happened is "the form was empty".
    api.expectNotReached("panelHealth");
  });
});

test.describe("页 ② 候选清单 → 个股详情", () => {
  test("lists the stored answers and opens one at its own address", async ({ page }) => {
    const api = await StubbedApi.install(page);
    const index = new ShortlistIndexPage(page);

    await index.goto();
    await api.expectRequestedOnMount("shortlistIndex");

    const answer = await index.open(SHORTLIST_ID);
    await api.expectRequestedOnMount("shortlistDetail");
    await expect(answer.declaration()).toBeVisible();
    await expect(answer.admittedRow("000001.SZ")).toBeVisible();
    // The evidence chain, which is what makes this an answer rather than a ranking: every
    // admitted candidate names the run that produced it, and the two admitted rows in the
    // shared fixture name *different* runs, so this cannot pass on a hard-coded single id.
    await expect(answer.runManifest("run_aaa")).toBeVisible();
    await expect(answer.runManifest("run_bbb")).toBeVisible();

    answer.expectQuietBrowser();
  });
});

test.describe("页 ③ 因子与模型实验室", () => {
  test("renders its two independent panels and opens a sealed experiment", async ({ page }) => {
    const api = await StubbedApi.install(page);
    const lab = new FactorLabPage(page);

    await lab.goto();
    await api.expectRequestedOnMount("factorExperimentIndex");
    await api.expectRequestedOnMount("predictions");
    await expect(lab.predictionsHeading()).toBeVisible();

    const experiment = await lab.open(EXPERIMENT_ID);
    await api.expectRequestedOnMount("factorExperimentDetail");
    await expect(experiment.heading(EXPERIMENT_ID)).toBeVisible();

    experiment.expectQuietBrowser();
  });

  test("a broken prediction store does not blank out the experiment index", async ({ page }) => {
    // The claim `FactorLabPage.tsx` makes about *not* using `Promise.all`. It is about two
    // in-flight requests rather than two rendered states, so a component test cannot make it:
    // one endpoint fails and the other must still land.
    await StubbedApi.install(page);
    await page.route("**/api/v1/predictions", (route) =>
      route.fulfill({ status: 500, json: { detail: "prediction store unavailable" } }),
    );

    const lab = new FactorLabPage(page);
    await lab.goto();

    await expect(lab.entry(EXPERIMENT_ID)).toBeVisible();
    await expect(lab.predictionsPanel().getByRole("alert")).toBeVisible();
    await expect(lab.experimentsPanel().getByRole("alert")).toHaveCount(0);
  });
});

test.describe("页 ④ 组合与验证", () => {
  test("constructs from a named list and renders the weight it was given", async ({ page }) => {
    const api = await StubbedApi.install(page);
    const portfolio = new PortfolioPage(page);

    await portfolio.goto();
    await expect(portfolio.idleNotice()).toBeVisible();
    api.expectNotReached("portfolioConstruct");

    await portfolio.construct({ shortlistId: SHORTLIST_ID, maxPositionWeight: "0.7" });
    await api.expectRequested("portfolioConstruct");

    // `invested_weight` arrives as the string `"1"`. The three target weights are `"0.7"`,
    // `"0.2"` and `"0.1"`, which sum left to right to `0.9999999999999999` — so a panel that
    // recomputed the total instead of rendering the field prints a visibly different string,
    // and this assertion can tell the two apart. That is the whole reason the fixture uses
    // this triple rather than one that happens to sum exactly.
    await expect(portfolio.investedWeight()).toContainText("1");
    await expect(portfolio.investedWeight()).not.toContainText("0.9999");
    await expect(portfolio.methodNote()).toContainText("heuristic, not optimized");
    await expect(portfolio.targetRow("600519.SH")).toBeVisible();
    // The three questions this page states it cannot answer instead of estimating them.
    await expect(portfolio.namedGaps()).toBeVisible();

    portfolio.expectQuietBrowser();
  });

  test("an unnamed list is refused here rather than sent to a 422", async ({ page }) => {
    const api = await StubbedApi.install(page);
    const portfolio = new PortfolioPage(page);

    await portfolio.goto();
    await portfolio.construct({ shortlistId: "" });

    await expect(portfolio.refusal()).toContainText("请先填写候选清单编号。");
    api.expectNotReached("portfolioConstruct");
  });
});
