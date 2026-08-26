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
// 1(c). Liveness of the checks themselves: every `schemaFile` a spec names must exist
//    and must still declare the version its filename claims. Layers 1 and 2 both assume
//    a spec's schema *loads*; a spec pointing at a re-versioned contract does not fail as
//    drift, it throws ENOENT out of `readSchema` before `findFieldDrift` is ever called —
//    the check stops checking, while `DRIFT_CHECKED_TYPES` (derived from `coversType`,
//    not from whether the spec can run) still reports its type as protected. See the
//    "no DriftCheckSpec silently stops checking" test below for the full argument.
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

/**
 * The stem of the document a schema *declares* it should be checked in as — the mirror of
 * `openalpha_cn.domain.schema.schema_document_name`, which is what actually names the files
 * under `docs/api/schemas/`: `"run-manifest/v3"` becomes `"run-manifest-v3"`. Returns `null`
 * for a schema with no `schema_version` const, which is not something any of the five
 * checked-in contracts does today.
 */
function declaredSchemaDocumentStem(raw: JsonSchemaNode): string | null {
  const properties = raw.properties;
  if (typeof properties !== "object" || properties === null) return null;
  const schemaVersion = (properties as Record<string, JsonSchemaNode>).schema_version;
  if (typeof schemaVersion !== "object" || schemaVersion === null) return null;
  const declared = (schemaVersion as JsonSchemaNode).const;
  if (typeof declared !== "string") return null;
  return declared.replace("/", "-");
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
    label: "ResearchResult.manifest matches run-manifest-v3",
    schemaFile: "run-manifest-v3.json",
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
  // V2-P5-014. These four follow ReplayReport's precedent above rather than widening what
  // this list means: each is a real wire mirror whose contract is simply not checked in as
  // JSON. `docs/api/schemas/` holds five documents — evidence-snapshot, signal-frame,
  // decision-ledger, run-manifest and validation-result — and none of them is a panel
  // health report or a shortlist answer, so there is nothing here to drift-check against.
  // Each entry names the Python serialiser that *is* the contract, so a reader can find it.
  ReadinessState:
    "Mirrors openalpha_cn.panel.catalog.ReadinessState (Literal['ready','blocked']); an enum " +
    "alias, not a message shape, and no checked-in JSON schema declares it.",
  PanelHealthReport:
    "Mirrors GET /api/v1/panel/health, serialised by openalpha_cn.panel_view." +
    "health_report_payload — only the five schemas under docs/api/schemas/*.json are checked " +
    "in as contracts, and no JSON schema for the panel health report exists to check against.",
  ShortlistIndex:
    "Mirrors GET /api/v1/shortlists, whose whole body is {shortlist_ids: [...]}; declared " +
    "inline in api/app.py's shortlist_list and backed by no checked-in JSON schema.",
  ShortlistAnswer:
    "Mirrors GET /api/v1/shortlists/{id} and POST /api/v1/shortlists/run, serialised by " +
    "openalpha_cn.shortlist_view.shortlist_view — versioned by SHORTLIST_VIEW_SCHEMA_VERSION " +
    "in Python, but that version has no checked-in JSON schema under docs/api/schemas/.",
  // V2-P5-017 / V2-P5-018. Ten more on the same precedent, and the precedent is the whole
  // reason they are listed rather than checked: `docs/api/schemas/` holds exactly five
  // documents (evidence-snapshot, signal-frame, decision-ledger, run-manifest,
  // validation-result) and **none of the routes these mirror declares a `response_model`**
  // — every one returns `JSONResponse(content=<view function's dict>)`, so the Python view
  // function *is* the contract and there is no JSON to drift-check against. Each reason
  // names that function, so a reader can go find the thing this mirror must agree with.
  FactorTier:
    "Mirrors openalpha_cn.backtest.factor_ic.FactorTier (Literal['raw','processed'," +
    "'neutralized']); an enum alias like ReadinessState above, not a message shape.",
  AttributionVerdict:
    "Mirrors openalpha_cn.backtest.factor_experiment.AttributionVerdict, the six verdicts one " +
    "tier step can earn; an enum alias with no checked-in JSON schema declaring it.",
  FactorTierAttribution:
    "One cell of the three-tier grid, from openalpha_cn.backtest.factor_experiment." +
    "TierAttribution, reaching HTTP inside factor_view.experiment_view's `document`; no " +
    "checked-in JSON schema covers the factor experiment artifact.",
  FactorTierReport:
    "One tier row, from openalpha_cn.backtest.factor_experiment.TierReport, reaching HTTP " +
    "inside factor_view.experiment_view's `document`; no checked-in JSON schema covers it.",
  FactorExperimentIndex:
    "Mirrors GET /api/v1/factors/experiments, whose whole body is {experiment_ids: [...]}; " +
    "declared inline in api/app.py's factor_experiment_list and backed by no JSON schema.",
  FactorExperimentEnvelope:
    "Mirrors GET /api/v1/factors/experiments/{id} and POST /api/v1/factors/run, serialised " +
    "by openalpha_cn.factor_view.experiment_view — versioned by VIEW_SCHEMA_VERSION in " +
    "Python, but that version has no checked-in JSON schema under docs/api/schemas/.",
  PredictionIndexEntry:
    "One row of GET /api/v1/predictions, serialised by openalpha_cn.model_view." +
    "_prediction_index_entry; versioned by MODEL_VIEW_SCHEMA_VERSION with no checked-in JSON.",
  PredictionIndex:
    "Mirrors GET /api/v1/predictions, serialised by openalpha_cn.model_view." +
    "prediction_index_view; no checked-in JSON schema declares the prediction register.",
  PortfolioTargetWeight:
    "One weighted name from openalpha_cn.backtest.portfolio_policy.construction_view's " +
    "`targets`; the view function is the contract and no JSON schema is checked in for it.",
  PortfolioConstructionView:
    "Mirrors POST /api/v1/portfolio/construct, serialised by openalpha_cn.backtest." +
    "portfolio_policy.construction_view — which emits three keys the PortfolioConstruction " +
    "model does not declare (invested_weight, targets[].was_adjusted, limitations), so the " +
    "view function and not the model is what this mirrors; no checked-in JSON schema exists.",
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

  // Why this test exists, concretely: `V2-P4-001` re-versioned the run manifest
  // (run-manifest/v2 -> /v3, adding the `paper`/`daily` modes, a comparable `horizon`
  // enum, model attribution, an explicit residual, and a content-addressed
  // `run_manifest_id`). The Python side renames its schema document from the version
  // string, so `docs/api/schemas/run-manifest-v2.json` became `run-manifest-v3.json`.
  // The `ResearchResult.manifest` spec above still named the v2 file. That did not
  // surface as drift — `readSchema` threw ENOENT before `findFieldDrift` ran, so the
  // manifest mirror had *no* drift protection from that row until `V2-P5-025`, and the
  // only symptom was one opaque ENOENT in a suite the integrator's gate did not run.
  //
  // A missing file is the cheap half. The expensive half is that a spec's type stays in
  // `DRIFT_CHECKED_TYPES` regardless of whether its spec can run, because that set is
  // derived from `coversType`, not from the assertion succeeding — so the "every exported
  // type is accounted for" test below keeps reporting the type as protected. Where a type
  // is covered by exactly one spec (Evidence, Timeline, ValidationResult today), a stale
  // filename therefore removes the type's only real check while the bookkeeping still
  // claims coverage. This test is what makes that state fail up front and by name.
  it("no DriftCheckSpec silently stops checking: every schemaFile exists and still declares the version its name claims", () => {
    const shipped = fs
      .readdirSync(schemaDir)
      .filter((name) => name.endsWith(".json"))
      .sort();
    const problems: string[] = [];

    for (const filename of [...new Set(DRIFT_CHECKS.map((spec) => spec.schemaFile))].sort()) {
      const covers = DRIFT_CHECKS.filter((spec) => spec.schemaFile === filename)
        .map((spec) => spec.coversType)
        .join("/");

      if (!fs.existsSync(path.join(schemaDir, filename))) {
        problems.push(
          `${filename} (named by the ${covers} spec) does not exist. docs/api/schemas ships: ` +
            `${shipped.join(", ")}. If the contract was re-versioned, point the spec at the new ` +
            `file and re-check the mirror in types.ts against it — do not assume the mirror is ` +
            `still correct just because the rename makes this test green again.`,
        );
        continue;
      }

      const raw = JSON.parse(
        fs.readFileSync(path.join(schemaDir, filename), "utf-8"),
      ) as JsonSchemaNode;
      const declaredStem = declaredSchemaDocumentStem(raw);
      if (declaredStem === null) {
        problems.push(`${filename} declares no schema_version const, so its version cannot be verified.`);
        continue;
      }
      if (`${declaredStem}.json` !== filename) {
        problems.push(
          `${filename} declares schema_version "${String(
            (raw.properties as Record<string, JsonSchemaNode>).schema_version.const,
          )}", which belongs in ${declaredStem}.json — the file name and the contract version ` +
            `it carries have diverged.`,
        );
      }
    }

    expect(problems, problems.join("\n")).toEqual([]);
  });

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
