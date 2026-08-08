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
  reason: "missing_in_schema" | "kind_mismatch";
  tsKinds: Kind[];
  schemaKinds: Kind[];
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
    if (schemaKinds.has("any")) continue;

    const isSubset = [...tsKinds].every((kind) => kind === "any" || schemaKinds.has(kind));
    if (!isSubset) {
      mismatches.push({
        field,
        reason: "kind_mismatch",
        tsKinds: [...tsKinds],
        schemaKinds: [...schemaKinds],
      });
    }
  }

  return mismatches;
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
