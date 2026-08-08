/// <reference types="node" />
//
// This is the guard itself: it runs the drift check from `schemaDrift.ts` against the
// *real* checked-in contract schemas (`docs/api/schemas/*.json`) and the *real*
// `types.ts`, not fixtures. It is part of `pnpm test`, so it runs on every CI build.
//
// What it protects: every field `types.ts` declares for Evidence, Timeline,
// ValidationResult, AttributionTerm (the `attribution` array item), and the three
// contract-shaped members of `ResearchResult` (`signal`, `decision`, `manifest`)
// must still exist in its schema under the same name and a compatible kind.
//
// What it deliberately does NOT protect: fields the schema has that `types.ts`
// chooses not to declare (e.g. SignalFrame.horizon, RunManifest.mode today) are
// never inspected — see task-23-report.md for why that is the correct scope, and
// for the four mutation experiments (rename / retype / delete / add-field) proving
// this file actually goes red for the first three and stays green for the fourth.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import ts from "typescript";

import {
  extractTypeLiteralFields,
  findArrayItemTypeLiteral,
  findFieldDrift,
  findNestedTypeLiteral,
  findTypeAlias,
  loadSchema,
  schemaDefProperties,
  type JsonSchemaNode,
} from "./schemaDrift";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemaDir = path.resolve(here, "../../docs/api/schemas");
const typesPath = path.resolve(here, "types.ts");

function readSchema(filename: string) {
  const raw = JSON.parse(fs.readFileSync(path.join(schemaDir, filename), "utf-8")) as JsonSchemaNode;
  return loadSchema(raw);
}

function readTypesSourceFile(): ts.SourceFile {
  const text = fs.readFileSync(typesPath, "utf-8");
  return ts.createSourceFile(typesPath, text, ts.ScriptTarget.Latest, true);
}

describe("web/src/types.ts declared fields never silently drift from the checked-in contract schemas", () => {
  it("Evidence matches evidence-snapshot-v1", () => {
    const { properties, defs } = readSchema("evidence-snapshot-v1.json");
    const sourceFile = readTypesSourceFile();
    const evidence = findTypeAlias(sourceFile, "Evidence");
    const drift = findFieldDrift(extractTypeLiteralFields(evidence, sourceFile), properties, defs);
    expect(drift).toEqual([]);
  });

  it("Timeline (referenced by Evidence.timeline) matches evidence-snapshot-v1 $defs.Timeline", () => {
    const { defs } = readSchema("evidence-snapshot-v1.json");
    const timelineProperties = schemaDefProperties(defs, "Timeline");
    const sourceFile = readTypesSourceFile();
    const timeline = findTypeAlias(sourceFile, "Timeline");
    const drift = findFieldDrift(extractTypeLiteralFields(timeline, sourceFile), timelineProperties, defs);
    expect(drift).toEqual([]);
  });

  it("ValidationResult matches validation-result-v1", () => {
    const { properties, defs } = readSchema("validation-result-v1.json");
    const sourceFile = readTypesSourceFile();
    const validationResult = findTypeAlias(sourceFile, "ValidationResult");
    const drift = findFieldDrift(
      extractTypeLiteralFields(validationResult, sourceFile),
      properties,
      defs,
    );
    expect(drift).toEqual([]);
  });

  it("ValidationResult.attribution items match validation-result-v1 $defs.AttributionTerm", () => {
    const { defs } = readSchema("validation-result-v1.json");
    const attributionProperties = schemaDefProperties(defs, "AttributionTerm");
    const sourceFile = readTypesSourceFile();
    const validationResult = findTypeAlias(sourceFile, "ValidationResult");
    const item = findArrayItemTypeLiteral(validationResult, sourceFile, "attribution");
    const drift = findFieldDrift(extractTypeLiteralFields(item, sourceFile), attributionProperties, defs);
    expect(drift).toEqual([]);
  });

  it("ResearchResult.signal matches signal-frame-v1", () => {
    const { properties, defs } = readSchema("signal-frame-v1.json");
    const sourceFile = readTypesSourceFile();
    const researchResult = findTypeAlias(sourceFile, "ResearchResult");
    const signal = findNestedTypeLiteral(researchResult, sourceFile, "signal");
    const drift = findFieldDrift(extractTypeLiteralFields(signal, sourceFile), properties, defs);
    expect(drift).toEqual([]);
  });

  it("ResearchResult.decision matches decision-ledger-v1", () => {
    const { properties, defs } = readSchema("decision-ledger-v1.json");
    const sourceFile = readTypesSourceFile();
    const researchResult = findTypeAlias(sourceFile, "ResearchResult");
    const decision = findNestedTypeLiteral(researchResult, sourceFile, "decision");
    const drift = findFieldDrift(extractTypeLiteralFields(decision, sourceFile), properties, defs);
    expect(drift).toEqual([]);
  });

  it("ResearchResult.manifest matches run-manifest-v1", () => {
    const { properties, defs } = readSchema("run-manifest-v1.json");
    const sourceFile = readTypesSourceFile();
    const researchResult = findTypeAlias(sourceFile, "ResearchResult");
    const manifest = findNestedTypeLiteral(researchResult, sourceFile, "manifest");
    const drift = findFieldDrift(extractTypeLiteralFields(manifest, sourceFile), properties, defs);
    expect(drift).toEqual([]);
  });
});
