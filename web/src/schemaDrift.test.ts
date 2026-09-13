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
  schemaFieldEnumValues,
  schemaFieldKind,
  tsFieldLiteralValues,
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

describe("tsFieldLiteralValues — recognising a field types.ts declared as an enum mirror", () => {
  function literalValuesOf(source: string): string[] | null {
    const { node } = parseTypeLiteral(`type X = { v: ${source} };`, "X");
    const member = node.members[0];
    if (!ts.isPropertySignature(member) || member.type === undefined) {
      throw new Error("fixture property has no type");
    }
    return tsFieldLiteralValues(member.type);
  }

  it("resolves a single string-literal type to its one value", () => {
    expect(literalValuesOf('"watch"')).toEqual(["watch"]);
  });

  it("resolves a string-literal union to all of its values, in declaration order", () => {
    expect(literalValuesOf('"watch" | "avoid" | "abstain"')).toEqual(["watch", "avoid", "abstain"]);
  });

  it("returns null for a plain string keyword (not an enum mirror)", () => {
    expect(literalValuesOf("string")).toBeNull();
  });

  it("returns null for a union containing a non-string-literal member (e.g. `T | null`)", () => {
    expect(literalValuesOf('"watch" | null')).toBeNull();
  });
});

describe("schemaFieldEnumValues — resolving a JSON Schema field's fixed value set", () => {
  it("resolves a plain `enum` array to its string values", () => {
    expect(schemaFieldEnumValues({ enum: ["watch", "avoid", "abstain"] }, {})).toEqual([
      "watch",
      "avoid",
      "abstain",
    ]);
  });

  it("resolves a `const` string to a single-value array", () => {
    expect(schemaFieldEnumValues({ const: "decision-ledger/v1" }, {})).toEqual(["decision-ledger/v1"]);
  });

  it("resolves a $ref into $defs recursively", () => {
    const defs = { FinalAction: { enum: ["watch", "avoid", "abstain"] } };
    expect(schemaFieldEnumValues({ $ref: "#/$defs/FinalAction" }, defs)).toEqual([
      "watch",
      "avoid",
      "abstain",
    ]);
  });

  it("returns null for a field with no enum/const constraint (plain `type: string`)", () => {
    expect(schemaFieldEnumValues({ type: "string" }, {})).toBeNull();
  });

  it("resolves anyOf[enum, null] (a nullable enum field) to the enum's values", () => {
    const node: JsonSchemaNode = { anyOf: [{ enum: ["watch", "avoid"] }, { type: "null" }] };
    expect(schemaFieldEnumValues(node, {})).toEqual(["watch", "avoid"]);
  });
});

describe("findFieldDrift — enum *value* drift, not just kind drift", () => {
  it("flags a field whose schema enum gained a value the ts literal union does not declare (the 'escalate' shape)", () => {
    const tsFields = { final_action: new Set<"string">(["string"]) };
    const tsLiteralValues = { final_action: ["watch", "avoid", "abstain"] };
    const schemaProps: Record<string, JsonSchemaNode> = {
      final_action: { enum: ["watch", "avoid", "abstain", "escalate"] },
    };
    const drift = findFieldDrift(tsFields, schemaProps, {}, tsLiteralValues);
    expect(drift).toEqual([
      {
        field: "final_action",
        reason: "enum_value_mismatch",
        tsKinds: ["string"],
        schemaKinds: ["string"],
        tsValues: ["abstain", "avoid", "watch"],
        schemaValues: ["abstain", "avoid", "escalate", "watch"],
      },
    ]);
  });

  it("flags a field whose schema enum lost a value the ts literal union still declares", () => {
    const tsFields = { final_action: new Set<"string">(["string"]) };
    const tsLiteralValues = { final_action: ["watch", "avoid", "abstain"] };
    const schemaProps: Record<string, JsonSchemaNode> = { final_action: { enum: ["watch", "avoid"] } };
    const drift = findFieldDrift(tsFields, schemaProps, {}, tsLiteralValues);
    expect(drift).toEqual([
      {
        field: "final_action",
        reason: "enum_value_mismatch",
        tsKinds: ["string"],
        schemaKinds: ["string"],
        tsValues: ["abstain", "avoid", "watch"],
        schemaValues: ["avoid", "watch"],
      },
    ]);
  });

  it("does NOT flag when both sides declare the exact same enum value set", () => {
    const tsFields = { final_action: new Set<"string">(["string"]) };
    const tsLiteralValues = { final_action: ["watch", "avoid", "abstain"] };
    const schemaProps: Record<string, JsonSchemaNode> = {
      final_action: { enum: ["watch", "avoid", "abstain"] },
    };
    expect(findFieldDrift(tsFields, schemaProps, {}, tsLiteralValues)).toEqual([]);
  });

  it("does NOT run the enum-value check when types.ts declares the field as plain `string`, even though the schema has an enum", () => {
    // This is the intentional-subset escape hatch applied to enums: declaring `kind: string`
    // instead of a literal union is types.ts opting out of value-level tracking for that
    // field, same as declaring a subset of a schema's object fields is opting out of the
    // fields it never mirrors.
    const tsFields = { kind: new Set<"string">(["string"]) };
    const tsLiteralValues = { kind: null };
    const schemaProps: Record<string, JsonSchemaNode> = { kind: { enum: ["a", "b", "c"] } };
    expect(findFieldDrift(tsFields, schemaProps, {}, tsLiteralValues)).toEqual([]);
  });

  it("does NOT run the enum-value check when tsLiteralValues is omitted entirely (back-compat with the original 3-arg call)", () => {
    const tsFields = { final_action: new Set<"string">(["string"]) };
    const schemaProps: Record<string, JsonSchemaNode> = { final_action: { enum: ["watch", "avoid"] } };
    expect(findFieldDrift(tsFields, schemaProps, {})).toEqual([]);
  });
});

describe("extractTypeLiteralLiteralValues — per-field literal-value extraction for a whole object literal", () => {
  it("extracts literal values for enum-mirror fields and null for everything else", () => {
    const { node, sourceFile } = parseTypeLiteral(
      'type X = { final_action: "watch" | "avoid" | "abstain"; run_id: string };',
      "X",
    );
    expect(extractTypeLiteralLiteralValues(node, sourceFile)).toEqual({
      final_action: ["watch", "avoid", "abstain"],
      run_id: null,
    });
  });
});

describe("listExportedTypeAliasNames — generic discovery of every mirrored type in types.ts", () => {
  it("lists every top-level `export type` alias name, in declaration order", () => {
    const sourceFile = ts.createSourceFile(
      "fixture.ts",
      `
      export type A = { a: string };
      export type B = { b: number };
      export type C = { c: boolean };
      `,
      ts.ScriptTarget.Latest,
      true,
    );
    expect(listExportedTypeAliasNames(sourceFile)).toEqual(["A", "B", "C"]);
  });

  it("ignores a non-exported type alias", () => {
    const sourceFile = ts.createSourceFile(
      "fixture.ts",
      `
      type Internal = { x: string };
      export type Exported = { y: string };
      `,
      ts.ScriptTarget.Latest,
      true,
    );
    expect(listExportedTypeAliasNames(sourceFile)).toEqual(["Exported"]);
  });

  it("ignores other exported statement kinds (functions, consts, interfaces)", () => {
    const sourceFile = ts.createSourceFile(
      "fixture.ts",
      `
      export const x = 1;
      export function f() {}
      export interface I { z: string }
      export type OnlyThisOne = { w: string };
      `,
      ts.ScriptTarget.Latest,
      true,
    );
    expect(listExportedTypeAliasNames(sourceFile)).toEqual(["OnlyThisOne"]);
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
