/// <reference types="node" />
//
// This is the guard itself: it runs the drift check from `schemaDrift.ts` against the
// *real* checked-in contract schemas (`docs/api/schemas/*.json`) and the *real*
// `types.ts`, not fixtures. It is part of `pnpm test`, so it runs on every CI build.
//
// What it protects, and how, comes in two layers:
//
// 1. Field-level + enum-value-level drift for the types this guard knows how to check
//    (`DRIFT_CHECKS` below): every field `types.ts` declares for Evidence, Timeline,
//    ValidationResult, AttributionTerm (the `attribution` array item), and the three
//    contract-shaped members of `ResearchResult` (`signal`, `decision`, `manifest`)
//    must still exist in its schema under the same name and a compatible kind — and,
//    for any field types.ts mirrors as a string-literal union (an enum mirror), the
//    schema's enum must still be the *exact same set of values*, not just the same
//    kind. A schema enum gaining, losing, or renaming a value is a real behavioural
//    break for callers like `DecisionPanel` that switch on the value, even though
//    kind-only comparison sees no difference (both sides still say "string").
//
// 2. Generic discovery, so a *new* mirrored type doesn't slip in unguarded: every
//    top-level `export type` in `types.ts` must be accounted for, either by appearing
//    in `DRIFT_CHECKS` (and therefore having a real, running assertion against a real
//    schema — `DRIFT_CHECKED_TYPES` below is *derived* from `DRIFT_CHECKS`, not a
//    separately hand-maintained list a contributor could satisfy without writing a
//    check) or by being named in `INTENTIONALLY_UNMAPPED_TYPES` with a one-line reason.
//    Neither list is optional busywork: the "every exported type is accounted for"
//    test at the bottom of this file fails, by name, for anything in neither list.
//
// What it deliberately does NOT protect: fields the schema has that `types.ts`
// chooses not to declare (e.g. SignalFrame.horizon, RunManifest.mode today) are
// never inspected — see task-23-report.md for why that is the correct scope, and
// for the four mutation experiments (rename / retype / delete / add-field) proving
// this file actually goes red for the first three and stays green for the fourth.
// See task-23-report.md's Finding 1 / Finding 2 sections for the two probes that
// motivated layers 2 and 1(b) above, and how each is now caught.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import ts from "typescript";

import {
  extractTypeLiteralFields,
  extractTypeLiteralLiteralValues,
  findArrayItemTypeLiteral,
  findFieldDrift,
  findNestedTypeLiteral,
  findTypeAlias,
  listExportedTypeAliasNames,
  loadSchema,
  schemaDefProperties,
  type JsonSchemaNode,
  type Kind,
  type ResolvedSchema,
} from "./schemaDrift";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemaDir = path.resolve(here, "../../docs/api/schemas");
const typesPath = path.resolve(here, "types.ts");

function readSchema(filename: string): ResolvedSchema {
  const raw = JSON.parse(fs.readFileSync(path.join(schemaDir, filename), "utf-8")) as JsonSchemaNode;
  return loadSchema(raw);
}

function readTypesSourceFile(): ts.SourceFile {
  const text = fs.readFileSync(typesPath, "utf-8");
  return ts.createSourceFile(typesPath, text, ts.ScriptTarget.Latest, true);
}

interface DriftCheckSpec {
  /** The top-level exported type in types.ts this check exercises drift protection for.
   * `DRIFT_CHECKED_TYPES` is derived from this field across all specs, so adding a name
   * here only counts once a spec actually runs a `findFieldDrift` assertion for it. */
  coversType: string;
  label: string;
  schemaFile: string;
  resolve: (
    sourceFile: ts.SourceFile,
    schema: ResolvedSchema,
  ) => {
    tsFields: Record<string, Set<Kind>>;
    tsLiteralValues: Record<string, string[] | null>;
    schemaProps: Record<string, JsonSchemaNode>;
  };
}

const DRIFT_CHECKS: DriftCheckSpec[] = [
  {
    coversType: "Evidence",
    label: "Evidence matches evidence-snapshot-v1",
    schemaFile: "evidence-snapshot-v1.json",
    resolve: (sourceFile, schema) => {
      const node = findTypeAlias(sourceFile, "Evidence");
      return {
        tsFields: extractTypeLiteralFields(node, sourceFile),
        tsLiteralValues: extractTypeLiteralLiteralValues(node, sourceFile),
        schemaProps: schema.properties,
      };
    },
  },
  {
    coversType: "Timeline",
    label: "Timeline (referenced by Evidence.timeline) matches evidence-snapshot-v1 $defs.Timeline",
    schemaFile: "evidence-snapshot-v1.json",
    resolve: (sourceFile, schema) => {
      const node = findTypeAlias(sourceFile, "Timeline");
      return {
        tsFields: extractTypeLiteralFields(node, sourceFile),
        tsLiteralValues: extractTypeLiteralLiteralValues(node, sourceFile),
        schemaProps: schemaDefProperties(schema.defs, "Timeline"),
      };
    },
  },
  {
    coversType: "ValidationResult",
    label: "ValidationResult matches validation-result-v2",
    schemaFile: "validation-result-v2.json",
    resolve: (sourceFile, schema) => {
      const node = findTypeAlias(sourceFile, "ValidationResult");
      return {
        tsFields: extractTypeLiteralFields(node, sourceFile),
        tsLiteralValues: extractTypeLiteralLiteralValues(node, sourceFile),
        schemaProps: schema.properties,
      };
    },
  },
  {
    coversType: "ValidationResult",
    label: "ValidationResult.attribution items match validation-result-v2 $defs.AttributionTerm",
    schemaFile: "validation-result-v2.json",
    resolve: (sourceFile, schema) => {
      const validationResult = findTypeAlias(sourceFile, "ValidationResult");
      const item = findArrayItemTypeLiteral(validationResult, sourceFile, "attribution");
      return {
        tsFields: extractTypeLiteralFields(item, sourceFile),
        tsLiteralValues: extractTypeLiteralLiteralValues(item, sourceFile),
        schemaProps: schemaDefProperties(schema.defs, "AttributionTerm"),
      };
    },
  },
  {
    coversType: "ResearchResult",
    label: "ResearchResult.signal matches signal-frame-v1",
    schemaFile: "signal-frame-v1.json",
    resolve: (sourceFile, schema) => {
      const researchResult = findTypeAlias(sourceFile, "ResearchResult");
      const signal = findNestedTypeLiteral(researchResult, sourceFile, "signal");
      return {
        tsFields: extractTypeLiteralFields(signal, sourceFile),
        tsLiteralValues: extractTypeLiteralLiteralValues(signal, sourceFile),
        schemaProps: schema.properties,
      };
    },
  },
  {
    coversType: "ResearchResult",
    label: "ResearchResult.decision matches decision-ledger-v2",
    schemaFile: "decision-ledger-v2.json",
    resolve: (sourceFile, schema) => {
      const researchResult = findTypeAlias(sourceFile, "ResearchResult");
      const decision = findNestedTypeLiteral(researchResult, sourceFile, "decision");
      return {
        tsFields: extractTypeLiteralFields(decision, sourceFile),
        tsLiteralValues: extractTypeLiteralLiteralValues(decision, sourceFile),
        schemaProps: schema.properties,
      };
    },
  },
  {
    coversType: "ResearchResult",
    label: "ResearchResult.manifest matches run-manifest-v2",
    schemaFile: "run-manifest-v2.json",
    resolve: (sourceFile, schema) => {
      const researchResult = findTypeAlias(sourceFile, "ResearchResult");
      const manifest = findNestedTypeLiteral(researchResult, sourceFile, "manifest");
      return {
        tsFields: extractTypeLiteralFields(manifest, sourceFile),
        tsLiteralValues: extractTypeLiteralLiteralValues(manifest, sourceFile),
        schemaProps: schema.properties,
      };
    },
  },
];

/** Derived — not hand-maintained — from `DRIFT_CHECKS`, so a type only counts as
 * "protected" once a spec above actually runs a `findFieldDrift` assertion for it. */
const DRIFT_CHECKED_TYPES = new Set(DRIFT_CHECKS.map((spec) => spec.coversType));

/**
 * Every other top-level exported type in `types.ts`, and why it has no drift check
 * above. This is the escape hatch for legitimate UI-only shapes — it must stay
 * possible without ceremony, but it must be an explicit, reviewable decision per type,
 * not a default a new type falls into silently.
 */
const INTENTIONALLY_UNMAPPED_TYPES: Record<string, string> = {
  Health: "UI-only health-check shape rendered by StatusBar; no contract schema exists for it.",
  ReplayReport:
    "Mirrors openalpha_cn.backtest.replay.ReplayReport, but only the five schemas under " +
    "docs/api/schemas/*.json are checked in as contracts — there is no checked-in JSON " +
    "schema for ReplayReport to drift-check against.",
  OutcomeInput: "UI-only form-input shape for submitting an outcome; not a mirror of any wire contract.",
  ProviderBatchUpload: "UI-only batch-upload form shape; not a mirror of any wire contract.",
};

describe("web/src/types.ts declared fields never silently drift from the checked-in contract schemas", () => {
  for (const spec of DRIFT_CHECKS) {
    it(spec.label, () => {
      const schema = readSchema(spec.schemaFile);
      const sourceFile = readTypesSourceFile();
      const { tsFields, tsLiteralValues, schemaProps } = spec.resolve(sourceFile, schema);
      const drift = findFieldDrift(tsFields, schemaProps, schema.defs, tsLiteralValues);
      expect(drift).toEqual([]);
    });
  }

  it("every exported type in types.ts is either drift-checked above or explicitly listed as unmapped", () => {
    const sourceFile = readTypesSourceFile();
    const declaredTypes = listExportedTypeAliasNames(sourceFile);
    const unaccounted = declaredTypes.filter(
      (name) => !DRIFT_CHECKED_TYPES.has(name) && !(name in INTENTIONALLY_UNMAPPED_TYPES),
    );

    expect(
      unaccounted,
      unaccounted.length === 0
        ? undefined
        : `types.ts exports ${unaccounted.join(", ")} with no drift protection and no ` +
          `documented reason it's exempt. For each: if it mirrors a docs/api/schemas/*.json ` +
          `contract, add a DriftCheckSpec for it to DRIFT_CHECKS in typesContractDrift.test.ts ` +
          `(this both protects it and marks it covered). If it is a UI-only shape with no ` +
          `schema counterpart, add it to INTENTIONALLY_UNMAPPED_TYPES with a one-line reason.`,
    ).toEqual([]);
  });
});
