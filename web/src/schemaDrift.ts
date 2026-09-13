// Drift guard between the hand-maintained TypeScript mirrors in `types.ts` and the
// checked-in Python contract schemas under `docs/api/schemas/*.json`.
//
// Design (see task-23-report.md for the full argument): declaring a *subset* of a
// contract in `types.ts` is legitimate — TypeScript is structurally typed and the
// frontend only needs the fields it renders. What must never happen silently is a
// field `types.ts` *does* declare being renamed, retyped, or deleted on the Python
// side. This module implements that one-directional check:
//
//   for every field `types.ts` declares, it must exist in the schema and its
//   TypeScript "kind" must be a subset of the field's JSON Schema "kind".
//
// Fields present in the schema but not declared in `types.ts` are never inspected —
// that is the intentional-subset escape hatch (mutation experiment 4 in the report).
//
// Kind resolution intentionally stops at the JSON Schema / TypeScript primitive
// level (string / number / boolean / array / object / null / any) and does not
// compare enum *values* or array item shapes beyond one level. That is a deliberate
// scope boundary, not an oversight — see the "known limitations" note in the report.

import ts from "typescript";

export type Kind = "string" | "number" | "boolean" | "array" | "object" | "null" | "any";

/** A raw JSON Schema node as parsed from one of the checked-in `docs/api/schemas/*.json` files. */
export type JsonSchemaNode = Record<string, unknown>;

export interface ResolvedSchema {
  properties: Record<string, JsonSchemaNode>;
  defs: Record<string, JsonSchemaNode>;
}

export interface DriftMismatch {
  field: string;
  reason: "missing_in_schema" | "kind_mismatch" | "enum_value_mismatch";
  tsKinds: Kind[];
  schemaKinds: Kind[];
  /** Only set for `reason: "enum_value_mismatch"`. */
  tsValues?: string[];
  /** Only set for `reason: "enum_value_mismatch"`. */
  schemaValues?: string[];
}

/** Split a raw parsed schema document into its top-level properties and `$defs`. */
export function loadSchema(raw: JsonSchemaNode): ResolvedSchema {
  const properties = raw.properties;
  const defs = raw.$defs;
  return {
    properties: isRecord(properties) ? (properties as Record<string, JsonSchemaNode>) : {},
    defs: isRecord(defs) ? (defs as Record<string, JsonSchemaNode>) : {},
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Resolve a single JSON Schema property node to the set of primitive kinds it can
 * take. Handles the shapes actually used by the five checked-in contract schemas:
 * `$ref` (into `$defs`), `const`, `enum`, `anyOf` (optional/nullable fields),
 * `type: "integer"` (folded into "number" — TypeScript has no separate int type),
 * and plain `type`.
 */
export function schemaFieldKind(node: JsonSchemaNode, defs: Record<string, JsonSchemaNode>): Set<Kind> {
  const ref = node.$ref;
  if (typeof ref === "string") {
    const key = ref.replace("#/$defs/", "");
    const target = defs[key];
    if (target === undefined || Object.keys(target).length === 0) {
      // An empty `$defs` entry (e.g. `JsonValue`) is JSON Schema's way of saying
      // "no constraint" — treat it as accepting anything.
      return new Set<Kind>(["any"]);
    }
    return schemaFieldKind(target, defs);
  }

  if ("const" in node) {
    const value = node.const;
    return new Set<Kind>([typeof value === "string" ? "string" : (typeof value as Kind)]);
  }

  if (Array.isArray(node.enum)) {
    const kinds = new Set<Kind>();
    for (const value of node.enum) kinds.add(typeof value as Kind);
    return kinds;
  }

  if (Array.isArray(node.anyOf)) {
    const kinds = new Set<Kind>();
    for (const sub of node.anyOf as JsonSchemaNode[]) {
      for (const kind of schemaFieldKind(sub, defs)) kinds.add(kind);
    }
    return kinds;
  }

  if (typeof node.type === "string") {
    if (node.type === "integer") return new Set<Kind>(["number"]);
    return new Set<Kind>([node.type as Kind]);
  }

  throw new Error(`schemaFieldKind: cannot classify schema node ${JSON.stringify(node)}`);
}

/**
 * Resolve a JSON Schema property node to its fixed set of legal string values, if it
 * has one — `enum` (filtered to string members), `const` (a single-value enum in
 * effect), or either resolved through `$ref`/`anyOf` (e.g. a nullable enum field:
 * `anyOf: [{enum: [...]}, {type: "null"}]`). Returns `null` for a field with no such
 * constraint (e.g. plain `type: "string"`) — that is what makes declaring a field as
 * plain `string` in `types.ts` (instead of mirroring it as a literal union) a legitimate
 * opt-out of value-level tracking, the enum equivalent of the object-field subset
 * escape hatch `findFieldDrift` already grants.
 */
export function schemaFieldEnumValues(
  node: JsonSchemaNode,
  defs: Record<string, JsonSchemaNode>,
): string[] | null {
  const ref = node.$ref;
  if (typeof ref === "string") {
    const key = ref.replace("#/$defs/", "");
    const target = defs[key];
    if (target === undefined || Object.keys(target).length === 0) return null;
    return schemaFieldEnumValues(target, defs);
  }

  if (Array.isArray(node.enum)) {
    return node.enum.filter((value): value is string => typeof value === "string");
  }

  if ("const" in node && typeof node.const === "string") {
    return [node.const];
  }

  if (Array.isArray(node.anyOf)) {
    for (const sub of node.anyOf as JsonSchemaNode[]) {
      const values = schemaFieldEnumValues(sub, defs);
      if (values !== null) return values;
    }
    return null;
  }

  return null;
}

/**
 * Resolve a TypeScript type node (the right-hand side of a `PropertySignature`) to
 * the set of primitive kinds it declares. Handles the shapes actually used by
 * `types.ts`: keywords, string-literal unions (enum mirrors), `T | null`, `T[]`,
 * `Array<T>`, `Record<string, unknown>`, inline object literals, and references to
 * another named type alias in the same file (treated as `"object"`).
 *
 * Deliberately throws on anything it does not recognise rather than guessing — a
 * silently-wrong kind would defeat the whole point of the guard.
 */
export function tsFieldKind(node: ts.TypeNode): Set<Kind> {
  switch (node.kind) {
    case ts.SyntaxKind.StringKeyword:
      return new Set<Kind>(["string"]);
    case ts.SyntaxKind.NumberKeyword:
      return new Set<Kind>(["number"]);
    case ts.SyntaxKind.BooleanKeyword:
      return new Set<Kind>(["boolean"]);
    case ts.SyntaxKind.UnknownKeyword:
    case ts.SyntaxKind.AnyKeyword:
      return new Set<Kind>(["any"]);
    default:
      break;
  }

  if (ts.isLiteralTypeNode(node)) {
    if (node.literal.kind === ts.SyntaxKind.NullKeyword) return new Set<Kind>(["null"]);
    if (ts.isStringLiteral(node.literal)) return new Set<Kind>(["string"]);
    throw new Error(`tsFieldKind: unsupported literal type ${node.getText()}`);
  }

  if (ts.isUnionTypeNode(node)) {
    const kinds = new Set<Kind>();
    for (const member of node.types) {
      for (const kind of tsFieldKind(member)) kinds.add(kind);
    }
    return kinds;
  }

  if (ts.isArrayTypeNode(node)) return new Set<Kind>(["array"]);

  if (ts.isTypeReferenceNode(node)) {
    const name = node.typeName.getText();
    if (name === "Array") return new Set<Kind>(["array"]);
    if (name === "Record") return new Set<Kind>(["object"]);
    // A reference to another named type alias declared in this same file
    // (e.g. `timeline: Timeline`) — every such reference in `types.ts` today
    // points at an object-shaped mirror.
    return new Set<Kind>(["object"]);
  }

  if (ts.isTypeLiteralNode(node)) return new Set<Kind>(["object"]);

  throw new Error(
    `tsFieldKind: unsupported ts type node ${ts.SyntaxKind[node.kind]} (${node.getText()})`,
  );
}

/** Extract `{ fieldName: kindSet }` for every property signature of an object type literal. */
export function extractTypeLiteralFields(
  node: ts.TypeLiteralNode,
  sourceFile: ts.SourceFile,
): Record<string, Set<Kind>> {
  const fields: Record<string, Set<Kind>> = {};
  for (const member of node.members) {
    if (!ts.isPropertySignature(member) || member.type === undefined) continue;
    const name = member.name.getText(sourceFile);
    fields[name] = tsFieldKind(member.type);
  }
  return fields;
}

/**
 * Resolve a TypeScript type node to the fixed set of string values it declares, if it
 * is exclusively made of string literals — a single literal (`"watch"`) or a union of
 * them (`"watch" | "avoid" | "abstain"`), types.ts's way of mirroring a JSON Schema
 * `enum`. Returns `null` for anything else (a plain `string` keyword, or a union that
 * mixes in a non-string-literal member such as `T | null`) — that field is not an enum
 * mirror, so `findFieldDrift` should not attempt a value-level comparison for it.
 */
export function tsFieldLiteralValues(node: ts.TypeNode): string[] | null {
  if (ts.isLiteralTypeNode(node) && ts.isStringLiteral(node.literal)) {
    return [node.literal.text];
  }

  if (ts.isUnionTypeNode(node)) {
    const values: string[] = [];
    for (const member of node.types) {
      const memberValues = tsFieldLiteralValues(member);
      if (memberValues === null) return null;
      values.push(...memberValues);
    }
    return values;
  }

  return null;
}

/** Extract `{ fieldName: literalValues }` (see `tsFieldLiteralValues`) for every property
 * signature of an object type literal — the enum-mirror counterpart of
 * `extractTypeLiteralFields`, walking the same members. */
export function extractTypeLiteralLiteralValues(
  node: ts.TypeLiteralNode,
  sourceFile: ts.SourceFile,
): Record<string, string[] | null> {
  const values: Record<string, string[] | null> = {};
  for (const member of node.members) {
    if (!ts.isPropertySignature(member) || member.type === undefined) continue;
    const name = member.name.getText(sourceFile);
    values[name] = tsFieldLiteralValues(member.type);
  }
  return values;
}

/**
 * The core drift check. For every field `types.ts` declares (`tsFields`), require
 * that it exists in the schema's declared properties and that every kind it
 * declares is a member of the schema's kind set for that field (a schema field
 * resolving to `"any"` — e.g. the `payload` catch-all — accepts anything).
 *
 * Fields present in `schemaProps` but absent from `tsFields` are never visited:
 * that is what makes declaring a subset legitimate.
 */
export function findFieldDrift(
  tsFields: Record<string, Set<Kind>>,
  schemaProps: Record<string, JsonSchemaNode>,
  defs: Record<string, JsonSchemaNode>,
  tsLiteralValues: Record<string, string[] | null> = {},
): DriftMismatch[] {
  const mismatches: DriftMismatch[] = [];

  for (const [field, tsKinds] of Object.entries(tsFields)) {
    const schemaNode = schemaProps[field];
    if (schemaNode === undefined) {
      mismatches.push({
        field,
        reason: "missing_in_schema",
        tsKinds: [...tsKinds],
        schemaKinds: [],
      });
      continue;
    }

    const schemaKinds = schemaFieldKind(schemaNode, defs);
    if (!schemaKinds.has("any")) {
      const isSubset = [...tsKinds].every((kind) => kind === "any" || schemaKinds.has(kind));
      if (!isSubset) {
        mismatches.push({
          field,
          reason: "kind_mismatch",
          tsKinds: [...tsKinds],
          schemaKinds: [...schemaKinds],
        });
        continue;
      }
    }

    // Enum *value* drift: only checked when types.ts chose to mirror this field as a
    // string-literal union (its declared claim that these are the only legal values) and
    // the schema constrains the field to a fixed value set. A kind-only comparison folds
    // every string enum to `"string"` and can never see this — a schema enum gaining,
    // losing, or renaming a value would otherwise pass silently even though the field types.ts
    // declared no longer means what it says.
    const literalValues = tsLiteralValues[field];
    if (literalValues) {
      const schemaEnumValues = schemaFieldEnumValues(schemaNode, defs);
      if (schemaEnumValues !== null) {
        const tsSet = new Set(literalValues);
        const schemaSet = new Set(schemaEnumValues);
        const sameValues = tsSet.size === schemaSet.size && [...tsSet].every((v) => schemaSet.has(v));
        if (!sameValues) {
          mismatches.push({
            field,
            reason: "enum_value_mismatch",
            tsKinds: [...tsKinds],
            schemaKinds: [...schemaKinds],
            tsValues: [...tsSet].sort(),
            schemaValues: [...schemaSet].sort(),
          });
        }
      }
    }
  }

  return mismatches;
}

/**
 * List every top-level `export type <name> = ...` alias name declared in a source
 * file, in declaration order. This is the generic-discovery half of the drift guard:
 * it enumerates *every* mirrored type `types.ts` exports, independent of any
 * hand-curated list of what the guard currently checks — so a new exported type
 * always shows up here even if no test was ever written to cover it. Non-exported
 * type aliases and other statement kinds (interfaces, consts, functions) are ignored.
 */
export function listExportedTypeAliasNames(sourceFile: ts.SourceFile): string[] {
  const names: string[] = [];
  for (const statement of sourceFile.statements) {
    if (
      ts.isTypeAliasDeclaration(statement) &&
      statement.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword)
    ) {
      names.push(statement.name.text);
    }
  }
  return names;
}

/** Find a top-level `export type <name> = { ... };` object type alias in a source file. */
export function findTypeAlias(sourceFile: ts.SourceFile, name: string): ts.TypeLiteralNode {
  for (const statement of sourceFile.statements) {
    if (ts.isTypeAliasDeclaration(statement) && statement.name.text === name) {
      if (!ts.isTypeLiteralNode(statement.type)) {
        throw new Error(`findTypeAlias: type alias '${name}' is not an object type literal`);
      }
      return statement.type;
    }
  }
  throw new Error(`findTypeAlias: no type alias named '${name}' found`);
}

function findPropertySignature(
  parent: ts.TypeLiteralNode,
  sourceFile: ts.SourceFile,
  fieldName: string,
): ts.PropertySignature {
  const member = parent.members.find(
    (candidate) => ts.isPropertySignature(candidate) && candidate.name.getText(sourceFile) === fieldName,
  );
  if (member === undefined || !ts.isPropertySignature(member) || member.type === undefined) {
    throw new Error(`findPropertySignature: field '${fieldName}' not found`);
  }
  return member;
}

/** Resolve a nested inline object literal field, e.g. `ResearchResult.signal`. */
export function findNestedTypeLiteral(
  parent: ts.TypeLiteralNode,
  sourceFile: ts.SourceFile,
  fieldName: string,
): ts.TypeLiteralNode {
  const member = findPropertySignature(parent, sourceFile, fieldName);
  const type = member.type;
  if (type === undefined || !ts.isTypeLiteralNode(type)) {
    throw new Error(`findNestedTypeLiteral: field '${fieldName}' is not an object literal`);
  }
  return type;
}

/** Resolve the item shape of an array-of-object field, e.g. `ValidationResult.attribution`. */
export function findArrayItemTypeLiteral(
  parent: ts.TypeLiteralNode,
  sourceFile: ts.SourceFile,
  fieldName: string,
): ts.TypeLiteralNode {
  const member = findPropertySignature(parent, sourceFile, fieldName);
  const type = member.type;

  if (type !== undefined && ts.isTypeReferenceNode(type) && type.typeName.getText() === "Array") {
    const [itemType] = type.typeArguments ?? [];
    if (itemType !== undefined && ts.isTypeLiteralNode(itemType)) return itemType;
  }
  if (type !== undefined && ts.isArrayTypeNode(type) && ts.isTypeLiteralNode(type.elementType)) {
    return type.elementType;
  }

  throw new Error(`findArrayItemTypeLiteral: field '${fieldName}' is not an array-of-object type`);
}

/** Look up a named entry in a schema's `$defs` and return its own `.properties`. */
export function schemaDefProperties(
  defs: Record<string, JsonSchemaNode>,
  name: string,
): Record<string, JsonSchemaNode> {
  const def = defs[name];
  if (def === undefined) throw new Error(`schemaDefProperties: no $defs entry named '${name}'`);
  const properties = def.properties;
  if (!isRecord(properties)) throw new Error(`schemaDefProperties: $defs.${name} has no properties`);
  return properties as Record<string, JsonSchemaNode>;
}
