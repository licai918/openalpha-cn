// V2-P5-027. Every address the app has, asked of the server that actually ships it.
//
// This is the one file in this directory that does **not** run against `vite dev`. Its project
// (`production`, in `playwright.config.ts`) points at `uvicorn openalpha_cn.api.app:app` — the
// `Dockerfile`'s entry point — serving a real `pnpm build` out of `web/dist`.
//
// ## Why it exists
//
// `routing.spec.ts` next door proves the client router resolves these addresses. It proved it
// against a dev server that answers `index.html` for anything it cannot match, so it was
// silent about the production server, which did not. Measured on `12532e3`:
//
//     /                    -> 200 shell    /factor-lab          -> 404
//     /data-health         -> 404          /factor-lab/fxp_abc  -> 404
//     /shortlists          -> 404          /portfolio           -> 404
//     /shortlists/sl_abc   -> 404
//
// Seven rows shipped a URL that worked when a developer ran `pnpm dev` and 404'd when a user
// ran the server. No test could see it, because no test asked the server.
//
// ## What it deliberately does not do
//
// It does not re-run the money flows. Those belong on the dev server, where the whole point is
// a stubbed backend and a fast loop. What is untestable there and only there is *the server*,
// so that is all this file asserts: the address resolves, the shell arrives, the router takes
// over, and the API's own 404 is still the API's own 404.
//
// The API is stubbed here too, exactly as next door. The production server would answer these
// endpoints for real out of an empty temp database, and "empty store" is a different fixture
// from the one the rest of the suite uses — the assertions would then be about a second,
// undeclared backend state. Stubbing keeps this file about routing.

import { expect, test } from "@playwright/test";

import { ROUTES } from "../src/routes";
import { EXPERIMENT_ID, SHORTLIST_ID, StubbedApi } from "./stubs";

test.beforeEach(async ({ page }) => {
  await StubbedApi.install(page);
});

const ADDRESSES: ReadonlyArray<{ path: string; heading: string }> = [
  { path: ROUTES.workbench, heading: "证据时间线" },
  { path: ROUTES.dataHealth, heading: "数据体检" },
  { path: ROUTES.shortlists, heading: "候选清单" },
  { path: ROUTES.shortlistDetail(SHORTLIST_ID), heading: "个股详情" },
  { path: ROUTES.factorLab, heading: "因子实验" },
  { path: ROUTES.factorExperimentDetail(EXPERIMENT_ID), heading: `因子实验 ${EXPERIMENT_ID}` },
  { path: ROUTES.portfolio, heading: "组合与验证" },
];

for (const { path, heading } of ADDRESSES) {
  test(`the shipped server serves ${path} to a browser pointed straight at it`, async ({
    page,
  }) => {
    // `page.goto` and not a click: a click never leaves the shell, so it cannot tell a working
    // server from a broken one. Every one of these was a 404 before `V2-P5-027`.
    const response = await page.goto(path);

    expect(response?.status(), `${path} was not served`).toBe(200);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  });
}

test("a reload holds the address rather than bouncing to the workbench", async ({ page }) => {
  // The failure this separates out: a server that redirected every unknown path to `/` would
  // pass every case above (the shell arrives, the router runs) while destroying the property
  // the addresses exist for. After a reload the URL must still be the one that was asked for.
  await page.goto(ROUTES.shortlistDetail(SHORTLIST_ID));
  await page.reload();

  await expect(page).toHaveURL(new RegExp(`/shortlists/${SHORTLIST_ID}$`));
  await expect(page.getByRole("heading", { name: "个股详情" })).toBeVisible();
});

test("an unknown location is the router's 404, not a blank shell", async ({ page }) => {
  const response = await page.goto("/no-such-page");

  expect(response?.status()).toBe(200);
  await expect(page.getByRole("alert")).toContainText("/no-such-page");
});

test("an unknown api path is refused in the api's own vocabulary", async ({ page }) => {
  // The load-bearing negative, asserted here rather than only in `tests/unit/
  // test_spa_addressability.py`, because this one goes through a real socket and a real
  // uvicorn: if the fallback ever swallowed `/api/`, a client-side typo would come back as an
  // HTML `200` and every caller that branches on `response.ok` would read a page as a payload.
  const response = await page.request.get("/api/v1/no-such-route");

  expect(response.status()).toBe(404);
  expect(response.headers()["content-type"]).toContain("application/json");
});

test("the built assets the shell asks for are the ones it gets", async ({ page }) => {
  // The other side of the same rule. A fallback that answered every miss with `index.html`
  // would hand `text/html` to a `<script>` tag, and the browser would report a MIME error
  // rather than a `404` — several layers from the cause.
  const failures: string[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/assets/") && !response.ok()) failures.push(url.pathname);
  });

  await page.goto(ROUTES.workbench);
  await expect(page.getByRole("heading", { name: "证据时间线" })).toBeVisible();

  expect(failures).toEqual([]);
  const missing = await page.request.get("/assets/index-does-not-exist.js");
  expect(missing.status()).toBe(404);
});
