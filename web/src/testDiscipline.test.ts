// V2-P5-020. The part of the frontend test gate that can name a file.
//
// ## Why a percentage floor is not enough
//
// Measured on this tree, at the ratchet `vite.config.ts` carried at the time: deleting
// `ReplayPanel.test.tsx` outright — sixteen tests, 150 → 134 — moved statements, functions
// and lines by **exactly zero** (320/356, 64/73, 299/328 before and after). Only branches
// moved, by 1.06pp. The reason is that `App.test.tsx` mounts the whole tree, so every
// panel's lines are executed on the way past whether or not anyone asserts anything about
// them. Coverage measures execution; it cannot see an assertion, so it cannot see an
// assertion's removal. `V2-P4-038`'s lesson in frontend form: a floor does not catch a
// deletion.
//
// So the aggregate floor stays in `vite.config.ts` for what it is good at — noticing that
// a lot of new code arrived untested — and the two things it cannot do are done here:
// notice that a module has no test of its own, and notice that the measured scope has
// been narrowed back to "whatever the tests happened to import".

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = dirname(fileURLToPath(import.meta.url));
const WEB = dirname(SRC);

/**
 * Modules with no co-located `*.test.*`, each with the reason it is allowed not to have
 * one. An **equality**, not an allowlist that is only ever consulted: a module that gains
 * a test while staying on this list fails just as loudly as one that loses its test
 * without joining it, so the list cannot quietly become the place untested code goes.
 */
const MODULES_WITHOUT_A_CO_LOCATED_TEST: Record<string, string> = {
  "main.tsx":
    "Three-line DOM bootstrap: createRoot(...).render(<App />). It is one statement in " +
    "the v8 denominator, it is exercised by `pnpm test:e2e`, and a jsdom test of it would " +
    "assert that React mounts. Counted at 0% in the coverage report rather than excluded, " +
    "so it stays visible.",
  "types.ts":
    "The mirror of docs/api/schemas/. Its co-located test is typesContractDrift.test.ts, " +
    "which is named for the drift it checks rather than for the file, and which already " +
    "requires every exported type to have a running check or a stated reason.",
};

/** Directories under `src/` that hold test scaffolding, not production code. */
const TEST_SUPPORT_DIRS = ["test"];

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (TEST_SUPPORT_DIRS.includes(relative(SRC, full))) continue;
      out.push(...sourceFiles(full));
      continue;
    }
    if (!/\.(ts|tsx)$/.test(entry.name)) continue;
    if (entry.name.endsWith(".d.ts")) continue;
    if (/\.test\.(ts|tsx)$/.test(entry.name)) continue;
    out.push(full);
  }
  return out;
}

function hasCoLocatedTest(file: string): boolean {
  const base = file.replace(/\.(ts|tsx)$/, "");
  const siblings = new Set(readdirSync(dirname(file)));
  return siblings.has(`${relative(dirname(file), base)}.test.ts`)
    || siblings.has(`${relative(dirname(file), base)}.test.tsx`);
}

describe("every production module is someone's subject, not just someone's dependency", () => {
  it("has a co-located test for each module, or a stated reason it has none", () => {
    const untested = sourceFiles(SRC)
      .filter((file) => !hasCoLocatedTest(file))
      .map((file) => relative(SRC, file))
      .sort();

    expect(untested).toEqual(Object.keys(MODULES_WITHOUT_A_CO_LOCATED_TEST).sort());
  });

  it("gives every exemption a reason long enough to have been thought about", () => {
    for (const [module, reason] of Object.entries(MODULES_WITHOUT_A_CO_LOCATED_TEST)) {
      expect(reason.length, module).toBeGreaterThan(80);
    }
  });

  it("does not list a module that no longer exists", () => {
    const existing = new Set(sourceFiles(SRC).map((file) => relative(SRC, file)));
    for (const module of Object.keys(MODULES_WITHOUT_A_CO_LOCATED_TEST)) {
      expect(existing.has(module), `${module} is exempted but is not in src/`).toBe(true);
    }
  });
});

describe("the coverage gate measures the source tree, not the import graph", () => {
  const config = readFileSync(join(WEB, "vite.config.ts"), "utf8");

  it("declares an explicit coverage include", () => {
    // Without this line v8 measures only files a test imported, so a new untested module
    // changes no number at all. Measured: a two-function module dropped into src/ left
    // 370/405 statements, 246/294 branches, 80/89 functions and 348/376 lines identical.
    expect(config).toContain('include: ["src/**/*.{ts,tsx}"]');
  });

  it("excludes exactly the non-production paths and nothing else", () => {
    // An equality on the exclusion list: widening it is how a coverage gate is made to
    // pass without writing a test, and this is where that shows up as a red test rather
    // than as a number that quietly went up.
    expect(config).toContain(
      'exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/vite-env.d.ts"]'
    );
  });

  it("keeps the thresholds block above the floor V2-P5-020 measured", () => {
    // The ratchet's own rule is "only ever up". Reading the numbers back out of the config
    // makes lowering them a two-file edit with a red test in between, instead of a
    // one-character edit that turns a red run green.
    const thresholds = /thresholds:\s*\{([^}]*)\}/.exec(config)?.[1] ?? "";
    const value = (name: string) =>
      Number(new RegExp(`${name}:\\s*(\\d+)`).exec(thresholds)?.[1] ?? "-1");
    expect(value("statements")).toBeGreaterThanOrEqual(90);
    expect(value("branches")).toBeGreaterThanOrEqual(84);
    expect(value("functions")).toBeGreaterThanOrEqual(89);
    expect(value("lines")).toBeGreaterThanOrEqual(91);
  });
});
