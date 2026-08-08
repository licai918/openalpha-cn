import { describe, expect, it } from "vitest";
import ts from "typescript";

import {
  extractTypeLiteralFields,
  findArrayItemTypeLiteral,
  findFieldDrift,
  findNestedTypeLiteral,
  findTypeAlias,
  schemaFieldKind,
  type JsonSchemaNode,
} from "./schemaDrift";

function parseTypeLiteral(source: string, typeName: string): { node: ts.TypeLiteralNode; sourceFile: ts.SourceFile } {
  const sourceFile = ts.createSourceFile("fixture.ts", source, ts.ScriptTarget.Latest, true);
  return { node: findTypeAlias(sourceFile, typeName), sourceFile };
}

describe("schemaFieldKind", () => {
  it("resolves plain JSON Schema primitive types", () => {
    expect(schemaFieldKind({ type: "string" }, {})).toEqual(new Set(["string"]));
    expect(schemaFieldKind({ type: "number" }, {})).toEqual(new Set(["number"]));
    expect(schemaFieldKind({ type: "boolean" }, {})).toEqual(new Set(["boolean"]));
    expect(schemaFieldKind({ type: "array" }, {})).toEqual(new Set(["array"]));
    expect(schemaFieldKind({ type: "object" }, {})).toEqual(new Set(["object"]));
  });

  it("folds integer into number (TypeScript has no separate int type)", () => {
    expect(schemaFieldKind({ type: "integer" }, {})).toEqual(new Set(["number"]));
  });

  it("resolves a string const to the string kind", () => {
    expect(schemaFieldKind({ const: "signal-frame/v1" }, {})).toEqual(new Set(["string"]));
  });

  it("resolves a string enum to the string kind", () => {
    expect(schemaFieldKind({ enum: ["bullish", "bearish", "neutral"] }, {})).toEqual(new Set(["string"]));
  });

  it("resolves anyOf[string, null] (an optional/nullable field) to both kinds", () => {
    const node: JsonSchemaNode = { anyOf: [{ type: "string" }, { type: "null" }] };
    expect(schemaFieldKind(node, {})).toEqual(new Set(["string", "null"]));
  });

  it("resolves a $ref into $defs recursively", () => {
    const defs = { Timeline: { type: "object" } };
    expect(schemaFieldKind({ $ref: "#/$defs/Timeline" }, defs)).toEqual(new Set(["object"]));
  });

  it("resolves a $ref to an empty $defs entry (e.g. JsonValue) as 'any'", () => {
    const defs = { JsonValue: {} };
    expect(schemaFieldKind({ $ref: "#/$defs/JsonValue" }, defs)).toEqual(new Set(["any"]));
  });
});

describe("tsFieldKind", () => {
  it("resolves primitive keywords", () => {
    const { node, sourceFile } = parseTypeLiteral(
      "type X = { a: string; b: number; c: boolean };",
      "X",
    );
    const fields = extractTypeLiteralFields(node, sourceFile);
    expect(fields.a).toEqual(new Set(["string"]));
    expect(fields.b).toEqual(new Set(["number"]));
    expect(fields.c).toEqual(new Set(["boolean"]));
  });

  it("resolves a string-literal union (enum mirror) to the string kind", () => {
    const { node, sourceFile } = parseTypeLiteral(
      'type X = { d: "bullish" | "bearish" | "neutral" };',
      "X",
    );
    expect(extractTypeLiteralFields(node, sourceFile).d).toEqual(new Set(["string"]));
  });

  it("resolves `T | null` to both kinds", () => {
    const { node, sourceFile } = parseTypeLiteral("type X = { e: string | null };", "X");
    expect(extractTypeLiteralFields(node, sourceFile).e).toEqual(new Set(["string", "null"]));
  });

  it("resolves `T[]` and `Array<T>` to the array kind", () => {
    const { node, sourceFile } = parseTypeLiteral(
      "type X = { f: string[]; g: Array<{ h: number }> };",
      "X",
    );
    const fields = extractTypeLiteralFields(node, sourceFile);
    expect(fields.f).toEqual(new Set(["array"]));
    expect(fields.g).toEqual(new Set(["array"]));
  });

  it("resolves Record<string, unknown> to the object kind", () => {
    const { node, sourceFile } = parseTypeLiteral("type X = { i: Record<string, unknown> };", "X");
    expect(extractTypeLiteralFields(node, sourceFile).i).toEqual(new Set(["object"]));
  });

  it("resolves an inline object type literal to the object kind", () => {
    const { node, sourceFile } = parseTypeLiteral("type X = { j: { k: string } };", "X");
    expect(extractTypeLiteralFields(node, sourceFile).j).toEqual(new Set(["object"]));
  });

  it("resolves a reference to another named type alias to the object kind", () => {
    const { node, sourceFile } = parseTypeLiteral(
      "type Timeline = { m: string }; type X = { l: Timeline };",
      "X",
    );
    expect(extractTypeLiteralFields(node, sourceFile).l).toEqual(new Set(["object"]));
  });
});

describe("findFieldDrift — the four mutation shapes the guard must tell apart", () => {
  it("flags a field types.ts declares that the schema no longer has under that name (rename)", () => {
    const tsFields = { subject: new Set<"string">(["string"]) };
    const schemaProps: Record<string, JsonSchemaNode> = { subj: { type: "string" } };
    const drift = findFieldDrift(tsFields, schemaProps, {});
    expect(drift).toEqual([
      { field: "subject", reason: "missing_in_schema", tsKinds: ["string"], schemaKinds: [] },
    ]);
  });

  it("flags a field whose schema type changed underneath the declared TS type (retype)", () => {
    const tsFields = { strength: new Set<"string">(["string"]) };
    const schemaProps: Record<string, JsonSchemaNode> = { strength: { type: "number" } };
    const drift = findFieldDrift(tsFields, schemaProps, {});
    expect(drift).toEqual([
      { field: "strength", reason: "kind_mismatch", tsKinds: ["string"], schemaKinds: ["number"] },
    ]);
  });

  it("flags a field types.ts declares that no longer exists in the schema at all (delete)", () => {
    const tsFields = { horizon: new Set<"string">(["string"]) };
    const schemaProps: Record<string, JsonSchemaNode> = {}; // deleted from the schema
    const drift = findFieldDrift(tsFields, schemaProps, {});
    expect(drift).toEqual([
      { field: "horizon", reason: "missing_in_schema", tsKinds: ["string"], schemaKinds: [] },
    ]);
  });

  it("does NOT flag a field the schema gained that types.ts does not declare (intentional subset)", () => {
    const tsFields = { run_id: new Set<"string">(["string"]) };
    const schemaProps: Record<string, JsonSchemaNode> = {
      run_id: { type: "string" },
      brand_new_field: { type: "string" }, // schema grew; types.ts never looked at it
    };
    const drift = findFieldDrift(tsFields, schemaProps, {});
    expect(drift).toEqual([]);
  });

  it("treats a schema field resolving to 'any' (e.g. payload) as compatible with anything declared", () => {
    const tsFields = { payload: new Set<"object">(["object"]) };
    const defs = { JsonValue: {} };
    const schemaProps: Record<string, JsonSchemaNode> = { payload: { $ref: "#/$defs/JsonValue" } };
    expect(findFieldDrift(tsFields, schemaProps, defs)).toEqual([]);
  });
});

describe("navigation helpers used to locate nested contract shapes inside types.ts", () => {
  const source = `
    export type ValidationResult = {
      validation_id: string;
      attribution: Array<{
        category: "rule" | "factor" | "agent";
        name: string;
        contribution: number;
      }>;
    };

    export type ResearchResult = {
      signal: {
        signal_id: string;
        direction: "bullish" | "bearish" | "neutral" | "abstain";
      };
    };
  `;

  it("findNestedTypeLiteral resolves an inline object field (ResearchResult.signal)", () => {
    const { node, sourceFile } = parseTypeLiteral(source, "ResearchResult");
    const signal = findNestedTypeLiteral(node, sourceFile, "signal");
    expect(extractTypeLiteralFields(signal, sourceFile)).toEqual({
      signal_id: new Set(["string"]),
      direction: new Set(["string"]),
    });
  });

  it("findArrayItemTypeLiteral resolves the item shape of Array<{...}> (ValidationResult.attribution)", () => {
    const { node, sourceFile } = parseTypeLiteral(source, "ValidationResult");
    const item = findArrayItemTypeLiteral(node, sourceFile, "attribution");
    expect(extractTypeLiteralFields(item, sourceFile)).toEqual({
      category: new Set(["string"]),
      name: new Set(["string"]),
      contribution: new Set(["number"]),
    });
  });
});
