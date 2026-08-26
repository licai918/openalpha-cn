import { defineConfig, devices } from "@playwright/test";

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
      use: { ...devices["Desktop Chrome"] }
    }
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: false,
    timeout: 120_000
  }
});
