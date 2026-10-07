import { defineConfig, devices } from "@playwright/test";

// V2-P5-021 gave this file a second project and a second server, and the reason is a defect
// rather than a preference.
//
// Every test here ran against `pnpm dev`. `vite` serves `index.html` for any path it cannot
// match, so `/data-health`, `/shortlists/sl_abc`, `/factor-lab/fxp_abc` and `/portfolio` all
// resolved — and all four 404'd under the server `openalpha serve` actually runs, because
// `api/app.py` mounted `StaticFiles(html=True)` and Starlette's `html=True` falls back only
// for *directory* requests. Seven rows shipped a bookmarkable URL that was bookmarkable only
// in development, and this suite structurally could not see it: the thing answering was the
// dev server, not the application. `V2-P5-027` fixed the server; the `production` project
// below is what stops it silently regressing.
//
// The production server is started as `uvicorn openalpha_cn.api.app:app` and **not** as
// `openalpha serve`, deliberately. They serve the same ASGI application — that string is the
// entry point the `Dockerfile` runs — but `cli.main()` merges `.env` into the environment
// before dispatching, and a test harness that reads a developer's real `.env` could pick up
// their `OPENALPHA_RUNTIME_DIR` and write into it. `create_app` reads only the real process
// environment (its own docstring makes a point of this), so the module entry point is both the
// production path and the hermetic one. `OPENALPHA_RUNTIME_DIR` is a fresh `mktemp -d` for the
// same reason: no run of this suite may touch the repository's `runtime/`.

const PRODUCTION_PORT = 8123;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure"
  },
  // V2-P5-014 removed the `mobile-chromium` (Pixel 5) project.
  //
  // It contradicted the PRD in two places, not one. Implementation Decision 15 scopes this
  // suite as "覆盖桌面 golden 流程 … 移动端宽度流程移出范围" — desktop golden flows, mobile-width
  // flows out of scope — and Decision 24 repeats "移动端宽度移出范围" for the web app as a
  // whole; scenario S82 ("Mobile-width usable daily candidate and alert views") is listed
  // OUT in §5.10. A project that replays the entire desktop golden flow at 393×851 *is* a
  // mobile-width flow, so it was asserting a scope the PRD had removed, at the cost of
  // doubling this suite's runtime.
  //
  // What was worth keeping from it is kept: the horizontal-overflow assertion in
  // golden-flow.spec.ts still runs, now named for the viewport it actually tests.
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      testIgnore: /production-routing\.spec\.ts/
    },
    {
      // The same browser against the *shipped* server over a real `pnpm build`. It runs one
      // file, because what it is for is the property the dev server was masking — that an
      // address typed into a browser reaches the page — and not a second copy of every flow.
      name: "production",
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PRODUCTION_PORT}` },
      testMatch: /production-routing\.spec\.ts/
    }
  ],
  webServer: [
    {
      command: "pnpm dev",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 120_000
    },
    {
      command:
        "pnpm build && cd .. && OPENALPHA_WEB_DIR=web/dist OPENALPHA_RUNTIME_DIR=$(mktemp -d) " +
        `uv run uvicorn openalpha_cn.api.app:app --host 127.0.0.1 --port ${PRODUCTION_PORT}`,
      // `/health` and not `/`: `/` would be answered by the static mount before the
      // application finished opening its storage, so readiness would be asserted against the
      // file system rather than against the app.
      url: `http://127.0.0.1:${PRODUCTION_PORT}/health`,
      reuseExistingServer: false,
      timeout: 180_000
    }
  ]
});
