// V2-P5-014 / 015 / 016. The addressability the router was actually taken for.
//
// `AppRouter.test.tsx` proves the routes resolve; this proves they are *addresses* — that a
// URL typed into a real browser reaches the page, and that the browser's own back button
// works. Neither is observable under `MemoryRouter`, which is why both belong here rather
// than in the unit suite.
//
// Offline like the rest of this directory: every endpoint is stubbed with `page.route`, the
// only navigation targets are relative paths, and the vite dev server is the config's own
// `webServer`.
//
// ## What these tests do NOT cover, measured rather than assumed
//
// They run against `vite dev`, which serves `index.html` for any unmatched path. The
// production server does not. `api/app.py` mounts `StaticFiles(directory=web_dir,
// html=True)` at `/`, and Starlette's `html=True` falls back to `index.html` only for
// *directory* requests — an unmatched path is a 404. Measured on this build with a
// `TestClient` over the real `create_app(web_dir=web/dist)`:
//
//     /                    -> 200  serves SPA shell: True
//     /data-health         -> 404  serves SPA shell: False
//     /shortlists          -> 404  serves SPA shell: False
//     /shortlists/sl_abc   -> 404  serves SPA shell: False
//
// So every deep link asserted below works in development and 404s in production until
// `api/app.py` grows an SPA fallback. That is reported as a blocked dependency of
// `V2-P5-014` rather than fixed here, because it is a Python-side change in a file sibling
// agents are editing. These tests are still correct about the *application* — the router
// resolves these addresses — they simply cannot see how the app is served.
//
// **V2-P5-017 / V2-P5-018 re-measured this for their own two areas rather than assuming the
// finding carried over**, on a fresh `pnpm build` with the same `TestClient` over the real
// `create_app(web_dir=web/dist)`. It does carry over, and the new addresses are affected
// identically:
//
//     /factor-lab          -> 404  serves SPA shell: False
//     /factor-lab/fxp_abc  -> 404  serves SPA shell: False
//     /portfolio           -> 404  serves SPA shell: False
//
// Pages ③ and ④ therefore ship with the same caveat as ① and ②: bookmarkable under
// `vite dev`, 404 under `openalpha serve`. Neither page's *unit* proof depends on the dev
// server (both route containers are driven through `MemoryRouter` with a stubbed `fetch` in
// `src/pages/*.test.tsx`), but their **addressability** is proven only in development, and
// `V2-P5-021` should not read the four page objects as evidence that the four URLs work in
// production. One `api/app.py` fallback closes all seven rows at once.

import { expect, test } from "@playwright/test";

const shortlistAnswer = {
  schema_version: "shortlist-view/v1",
  shortlist_id: "sl_e2e",
  is_blocked: false,
  as_of: "2026-07-24T10:00:00+00:00",
  horizon: "swing",
  tier: "processed",
  declaration: {
    tier: "processed",
    transform: "zscore/v1",
    neutralization: null,
    exchange: "XSHG",
    years: [2026],
    components: [{ factor_id: "momentum_20d", factor: "momentum_20d/v1", weight: 1 }]
  },
  cross_section: {
    as_of: "2026-07-24T10:00:00+00:00",
    pricing_session: "2026-07-24",
    universe_count: 300
  },
  funnel: {
    coverage: "complete",
    scored_count: 300,
    excluded_by_coverage: { incomplete_components: 0, not_admissible: 0, not_valued: 0 },
    tradeable_count: 300,
    refused_by_verdict: {},
    rejection_reasons: {},
    untradeable: [],
    untradeable_not_named: 0,
    shortlist: [{ subject: "000001.SZ", rank: 1, score: 2.31 }]
  },
  measurement: {
    universe_count: 300,
    scored_count: 300,
    tradeable_count: 300,
    shortlist_count: 1,
    candidate_count: 1,
    tradable_ratio: 1,
    researched_ratio: 1,
    ranking_age_days: 0
  },
  blocks: [],
  admitted: [
    {
      subject: "000001.SZ",
      rank: 1,
      score: 2.31,
      direction: "bullish",
      confidence: 0.72,
      run_manifest_id: "run_e2e",
      risk_flags: []
    }
  ],
  unresearched: [],
  evidence_not_shortlisted: [],
  evidence_from_an_unfinished_run: [],
  evidence_without_a_stored_run: []
};

test.beforeEach(async ({ page }) => {
  await page.route("**/health", (route) =>
    route.fulfill({ json: { status: "ok", version: "1.0.0" } })
  );
  await page.route("**/api/v1/evidence?**", (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/v1/shortlists", (route) =>
    route.fulfill({ json: { shortlist_ids: ["sl_e2e"] } })
  );
  await page.route("**/api/v1/shortlists/*", (route) =>
    route.fulfill({ json: shortlistAnswer })
  );
});

test("each page has its own address a browser can be pointed at directly", async ({ page }) => {
  // A deep link, not a click: this is the property that makes the app bookmarkable and
  // shareable, and it is the whole reason V2-P5-014 concluded React Router was worth taking.
  await page.goto("/data-health");
  await expect(page.getByRole("heading", { name: "数据体检" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "证据时间线" })).toBeHidden();
});

test("a shortlist's content address is its URL", async ({ page }) => {
  // `shortlist_id` is a digest the server computes over the finished answer body, so this
  // URL names one immutable answer. Pasting it to a colleague hands them that answer.
  await page.goto("/shortlists/sl_e2e");
  await expect(page.getByRole("heading", { name: "个股详情" })).toBeVisible();
  await expect(page.getByText("run_e2e")).toBeVisible();
});

test("navigation updates the address bar and the back button returns", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "证据时间线" })).toBeVisible();

  await page.getByRole("link", { name: "候选清单" }).click();
  await expect(page).toHaveURL(/\/shortlists$/);
  await page.getByRole("link", { name: "sl_e2e" }).click();
  await expect(page).toHaveURL(/\/shortlists\/sl_e2e$/);
  await expect(page.getByRole("heading", { name: "个股详情" })).toBeVisible();

  // The browser's own history, which is the half a MemoryRouter test cannot reach.
  await page.goBack();
  await expect(page).toHaveURL(/\/shortlists$/);
  await expect(page.getByRole("heading", { name: "候选清单" })).toBeVisible();

  expect(errors).toEqual([]);
});

test("an unknown address says so instead of rendering an empty shell", async ({ page }) => {
  await page.goto("/no-such-page");
  await expect(page.getByRole("alert")).toContainText("/no-such-page");
});
