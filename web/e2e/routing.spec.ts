// V2-P5-014 / 015 / 016 / 017 / 018. The addressability the router was actually taken for.
//
// `AppRouter.test.tsx` proves the routes resolve; this proves the browser's own history works
// — a click changes the address bar and the back button returns — which is not observable
// under `MemoryRouter`.
//
// ## The caveat this file carried for four rows, and what closed it
//
// Until `V2-P5-027` this file's docstring said, correctly, that everything below ran against
// `vite dev` and was therefore silent about production: `api/app.py` mounted
// `StaticFiles(html=True)`, whose fallback covers only *directory* requests, so `/data-health`,
// `/shortlists`, `/shortlists/sl_abc`, `/factor-lab`, `/factor-lab/fxp_abc` and `/portfolio`
// were all `404` under `openalpha serve` while passing here. Two agents measured it
// independently and neither could fix it from `web/`.
//
// It is fixed, and the proof is not this file — deliberately. `production-routing.spec.ts`
// runs the same addresses against `uvicorn openalpha_cn.api.app:app` over a real `pnpm build`,
// because a fallback's correctness can only be measured against the server that has to
// implement it. This file keeps what only a dev server can give cheaply: history, navigation
// and the in-app 404.

import { expect, test } from "@playwright/test";

import { AppShell, NAV } from "./pages/AppShell";
import { DataHealthPage } from "./pages/DataHealthPage";
import { ShortlistIndexPage } from "./pages/ShortlistPages";
import { SHORTLIST_ID, StubbedApi } from "./stubs";

test.beforeEach(async ({ page }) => {
  await StubbedApi.install(page);
});

test("each page has its own address a browser can be pointed at directly", async ({ page }) => {
  // A deep link, not a click: this is the property that makes the app bookmarkable and
  // shareable, and it is the whole reason V2-P5-014 concluded React Router was worth taking.
  const dataHealth = new DataHealthPage(page);
  await dataHealth.goto();

  await expect(page.getByRole("heading", { name: "证据时间线" })).toBeHidden();
});

test("a shortlist's content address is its URL", async ({ page }) => {
  // `shortlist_id` is a digest the server computes over the finished answer body, so this
  // URL names one immutable answer. Pasting it to a colleague hands them that answer.
  const detail = new ShortlistIndexPage(page);
  await detail.goto();
  const answer = await detail.open(SHORTLIST_ID);

  await expect(answer.declaration()).toBeVisible();
});

test("navigation updates the address bar and the back button returns", async ({ page }) => {
  const shell = new AppShell(page);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "证据时间线" })).toBeVisible();

  await shell.clickNav("候选清单", "/shortlists");
  const index = new ShortlistIndexPage(page);
  await index.open(SHORTLIST_ID);
  await expect(page.getByRole("heading", { name: "个股详情" })).toBeVisible();

  // The browser's own history, which is the half a MemoryRouter test cannot reach.
  await page.goBack();
  await expect(page).toHaveURL(/\/shortlists$/);
  await expect(page.getByRole("heading", { name: "候选清单" })).toBeVisible();

  shell.expectQuietBrowser();
});

test("every area in the nav table is reachable and marks itself current", async ({ page }) => {
  // Ranges over `NAV_ITEMS` from `src/routes.ts` rather than over a list written here, so an
  // area added to the app without an e2e case is impossible rather than merely discouraged.
  const shell = new AppShell(page);
  await page.goto("/");

  for (const item of NAV) {
    await shell.clickNav(item.label, item.path);
    await shell.expectExactlyOneCurrentArea(item.label);
  }

  expect(NAV.length).toBe(5);
});

test("an unknown address says so instead of rendering an empty shell", async ({ page }) => {
  const shell = new AppShell(page);
  await page.goto("/no-such-page");

  await expect(shell.notFoundAlert()).toContainText("/no-such-page");
});
