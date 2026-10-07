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

  it("requires each co-located test to import its subject and assert something", () => {
    // V2-P5-037. The check above `stat`s a filename and nothing more, so the cheapest way
    // to satisfy it is a file that satisfies nothing: measured, a new `probeDrift.ts` went
    // red and named itself, and a `probeDrift.test.ts` holding one `it("works", () => {})`
    // -- importing nothing, asserting nothing -- turned the suite green at `6 passed`.
    //
    // Two static properties, and neither is a proof: a test can import its subject and
    // assert something irrelevant. What they remove is the *empty* file, which is the only
    // thing a filename check invites. This is `test_known_limitation_registries.py`'s "the
    // literal must sit in executable test code" in frontend form, and it is weak in the
    // same stated way. All 24 co-located tests satisfied it on the day it was written, so
    // nothing was grandfathered in.
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      const stem = file.replace(/^.*\//, "").replace(/\.(ts|tsx)$/, "");
      const siblings = readdirSync(dirname(file));
      for (const suffix of [".test.ts", ".test.tsx"]) {
        if (!siblings.includes(`${stem}${suffix}`)) continue;
        const candidate = join(dirname(file), `${stem}${suffix}`);
        const text = readFileSync(candidate, "utf8");
        const specifiers = [...text.matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1]);
        const wanted = [`./${stem}`, `./${stem}.ts`, `./${stem}.tsx`, `./${stem}.js`];
        if (!specifiers.some((specifier) => wanted.includes(specifier))) {
          offenders.push(`${relative(SRC, candidate)} imports nothing from ./${stem}`);
        }
        if (!text.includes("expect(")) {
          offenders.push(`${relative(SRC, candidate)} contains no expect(`);
        }
      }
    }

    expect(offenders).toEqual([]);
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

  it("keeps the thresholds block at the ratchet the tree actually stands on", () => {
    // The ratchet's own rule is "only ever up". Reading the numbers back out of the config
    // makes lowering them a two-file edit with a red test in between, instead of a
    // one-character edit that turns a red run green.
    //
    // V2-P5-037: these read 90/84/89/91 while `vite.config.ts` shipped 93/87/94/94, so
    // "only ever up" could be walked back by three, three, five and three points with this
    // test still green -- a floor under the floor, which is the one thing a ratchet must
    // not have. They are the shipped numbers now. Raising the config raises these in the
    // same commit; that is the cost, and it is the cost of the ratchet meaning anything.
    const thresholds = /thresholds:\s*\{([^}]*)\}/.exec(config)?.[1] ?? "";
    const value = (name: string) =>
      Number(new RegExp(`${name}:\\s*(\\d+)`).exec(thresholds)?.[1] ?? "-1");
    expect(value("statements")).toBeGreaterThanOrEqual(93);
    expect(value("branches")).toBeGreaterThanOrEqual(87);
    expect(value("functions")).toBeGreaterThanOrEqual(94);
    expect(value("lines")).toBeGreaterThanOrEqual(94);
  });
});
