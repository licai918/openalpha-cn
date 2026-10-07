import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000"
    }
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    coverage: {
      provider: "v8",
      enabled: true,
      reporter: ["text", "html"],
      // `include` is the load-bearing line here, not the thresholds (V2-P5-020).
      //
      // Without it, vitest's v8 provider measures only the files some test happened to
      // import, so the denominator is chosen by the test suite rather than by the source
      // tree. Measured on this repository: dropping a module into `src/` with two
      // exported functions and no test left every number byte-identical — 370/405
      // statements, 246/294 branches, 80/89 functions, 348/376 lines before and after. A
      // coverage gate that cannot see untested code is a gate against nothing. With
      // `include` the same probe drove all four thresholds red.
      //
      // Two things left the denominator's *old* shape when it was fixed, which is why the
      // numbers below are not comparable to the ones above them:
      //   - `src/main.tsx` was never counted (no test imports it). It is counted now, at
      //     0%: it is a single statement, it is exercised by `pnpm test:e2e`, and
      //     excluding it would move statements 89.88 → 90.14 — not enough to change any
      //     floor, so it stays visible rather than hidden behind an exemption.
      //   - `src/test/**` *was* counted. Test helpers are not production code, and they
      //     were contributing 52 statements at 50 covered, i.e. inflating the headline by
      //     scoring the tests with the tests. They are excluded now.
      //
      // Floor set at (rounded down from) the coverage actually measured. It must never be
      // lowered to make a red run pass; the drop from the 2026-08-25 line to the one after
      // it is a change of denominator, not a relaxation — the same 150 tests cover the
      // same code.
      //
      // 2026-08-07, two App.test.tsx cases: statements 68.59%, branches 60.67%,
      //   functions 64.86%, lines 70.08%  →  68 / 60 / 64 / 70.
      // 2026-08-25, V2-P5-019 (panel-state union + component tests for all four
      //   panels, 53 → 150 tests): statements 91.35%, branches 83.67%,
      //   functions 89.88%, lines 92.55%  →  91 / 83 / 89 / 92.
      // 2026-08-25, V2-P5-020 (scope corrected to the source tree; isolation tests added
      //   for api/client.ts, PanelNotice and StatusBar, 150 → 181 tests):
      //   statements 90.16%, branches 84.85%, functions 89.04%, lines 91.46%
      //   →  90 / 84 / 89 / 91.
      //
      // Re-measured under the *previous* scope so the two lines above are comparable:
      // 91.60% / 85.03% / 91.01% / 92.81% (371/405, 250/294, 81/89, 349/376), i.e. up on
      // all four against V2-P5-019's 91.35 / 83.67 / 89.88 / 92.55. The printed drop is
      // the denominator changing, not tests being removed. (That run is 175 tests, not
      // 181: `testDiscipline.test.ts` reads this file's literal text and cannot pass
      // against a scratch config.)
      //
      // Margin to the floor, in whole units rather than percentage points:
      // 0 statements, 2 branches, 0 functions, 1 line. Two of the four are at zero — one
      // uncovered function or one uncovered statement turns this red. That is the ratchet
      // being a ratchet, and it is why the guard that can *name* the file lives next door
      // in `src/testDiscipline.test.ts` rather than being simulated with a lower number
      // here.
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/vite-env.d.ts"],
      // Deliberately a single aggregate floor and no per-file floor. vitest 4.1.10 cannot
      // express both: `thresholds.perFile` is global-only (a glob entry is typed
      // `Pick<Thresholds, 100 | "statements" | "functions" | "branches" | "lines">`, with
      // no `perFile`), and a glob entry aggregates over the group rather than checking
      // each file — measured, `"src/components/**": { functions: 60 }` stayed green while
      // `ReplayPanel.tsx` sat at 50%. Naming a single rotting file is therefore done by
      // `src/testDiscipline.test.ts`, which can say which file, rather than by a
      // percentage that can only say that some percentage moved.
      // NOTE (integrator, merge of V2-P5-020 with V2-P5-014/015/016): both sides raised
      //   this ratchet, and their numbers are not comparable -- 020 added `include:` so
      //   the denominator now holds files nobody imports. Neither side's thresholds
      //   apply to the union, so the four below are re-measured on the merged tree.
      // 2026-08-25, V2-P5-014/015/016 (React Router, pages ① and ②, their two
      //   contract classifiers and the data layer's own tests, 150 → 260 tests):
      //   statements 92.30%, branches 84.36%, functions 92.14%, lines 93.50%
      //   →  92 / 84 / 92 / 93. Every metric rose despite ~500 lines of new
      //   component code, because each page landed with its shared panel-state
      //   contract suite rather than after it.
      // 2026-08-25, the merge of the two lines above (integrator). Measured on the union
      //   under 020's wider `include:` scope, 296 tests / 22 files:
      //   statements 91.76%, branches 85.10%, functions 92.00%, lines 93.24%
      //   →  91 / 85 / 92 / 93.
      //   Statements reads *below* 014/015/016's 92.30% and that is the denominator, not a
      //   regression: 020 put files nobody imports into the scope, and `routes.ts` joined
      //   the tree with its own co-located test at this merge rather than an exemption.
      //   Branches is the metric that actually rose, 84 → 85.
      // 2026-08-25, V2-P5-017/018 (pages ③ and ④: two route containers, four panels, three
      //   contract classifiers and the data layer for five more endpoints, 296 → 438 tests
      //   / 22 → 30 files). Same `include:` scope as the line above, so these numbers are
      //   directly comparable to it: statements 93.19%, branches 87.52%, functions 94.41%,
      //   lines 94.36%  →  93 / 87 / 94 / 94. All four rose.
      //
      //   Branches is the one worth recording how. The first green run of this row measured
      //   83.88% — *below* the 85 inherited floor — because ~500 lines of new panel arrived
      //   whose nullable fields, enum second-arms and `cancelled` guards no test reached.
      //   The floor was left alone and the branches were covered instead: one test over the
      //   weak end of every nullable the factor contract declares, one over a policy with
      //   every limit undeclared, two over a non-Error rejection, and four that unmount a
      //   page mid-flight and then let the answer land. One dead branch was deleted rather
      //   than tested — `STANDING_LABEL[...] ?? entry.standing`, unreachable once the map is
      //   keyed by the union — because covering an unreachable arm is not possible and
      //   leaving it lowers the ceiling for everyone after.
      thresholds: {
        statements: 93,
        branches: 87,
        functions: 94,
        lines: 94
      }
    }
  }
});
