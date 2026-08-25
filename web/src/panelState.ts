// V2-P5-019. One state vocabulary for all four panels.
//
// This deliberately does **not** live in `types.ts`. That file is the mirror of the
// checked-in contract schemas under `docs/api/schemas/`, and `typesContractDrift.test.ts`
// requires every top-level `export type` in it to either have a running drift check or an
// explicit "intentionally unmapped" reason. `PanelState` is a presentation shape with no
// wire contract behind it and no schema that could ever be written for it; putting it in
// the mirror would mean adding a fourth entry to `INTENTIONALLY_UNMAPPED_TYPES`, which
// dilutes what that list means (today it holds shapes that *look* like contracts but have
// no checked-in schema — not UI vocabulary). A separate module keeps the mirror a mirror.
//
// ## Which eight, and why there are nine
//
// PRD Implementation Decision 14 names the states component tests must cover:
// `loading / ready / empty / degraded / stale / blocked / failed / succeeded`. That list
// is the source for eight of the nine kinds below. The ninth is `idle`, which is **not**
// one of the PRD's eight but which `EvidencePanel` already had (`"idle" | "loading" |
// "ready" | "empty" | "error"`) and which renders "尚未查询证据" — a real state, distinct
// from `empty` ("we asked and there is nothing") in exactly the way that matters here:
// one means the user has not acted, the other is a substantive answer from the backend.
// Collapsing them to satisfy a count would delete information, so `idle` is kept and
// named as a ninth rather than quietly folded into one of the eight.
//
// Two renames come with that: EvidencePanel's `"error"` is the PRD's `failed`, and its
// `"ready"` is joined by `succeeded` for the three panels that run a *job* rather than
// perform a *read*. `ready` and `succeeded` are kept apart because they answer different
// questions — "the data you asked for is on screen" versus "the run you started finished"
// — but both are rendered through the same data path, so neither is an unreachable arm.
//
// ## The invariant
//
// `blocked` and `failed` carry a message and **no data**. That is the whole point of the
// row: with `loading: boolean` + `error: string | null` + `result: T | null`, the tuple
// `{loading: false, error: null, result: null}` is representable and renders a blank
// panel — a failure that lost its message is indistinguishable from nothing having
// happened. Here, reaching an alert kind requires supplying the text that explains it,
// and no alert kind has a `data` field to render. `panelState.test.ts` asserts both
// halves at runtime, because `tsc` checks the switch arms but not the partition.

/** The data a panel shows, plus how the panel must qualify it. */
export type PanelState<T> =
  /** The user has not asked for anything yet. Not one of PRD Decision 14's eight; see above. */
  | { kind: "idle" }
  /** A request is in flight. The only kind allowed to render neither data nor a message. */
  | { kind: "loading" }
  /** A read returned data that needs no qualification. */
  | { kind: "ready"; data: T }
  /** A job the user started finished, and its outcome needs no qualification. */
  | { kind: "succeeded"; data: T }
  /** The backend answered, and the answer is "nothing". `reason` says what was asked. */
  | { kind: "empty"; reason: string }
  /** Data is on screen but something about it is not whole — a restricted source, a
   * partial run, an attribution with no named terms. Shown, but never as a plain success. */
  | { kind: "degraded"; data: T; reason: string }
  /** Data is on screen but it answers an older question than the one now on the form. */
  | { kind: "stale"; data: T; reason: string }
  /** A gate refused. Carries no data: a refusal must never be rendered as a verdict. */
  | { kind: "blocked"; reason: string }
  /** The request itself failed. Carries no data, and must carry the failure's own words. */
  | { kind: "failed"; error: string };

/** Every kind in `PanelState`, as a value so tests and renderers can iterate the union. */
export const PANEL_STATE_KINDS = [
  "idle",
  "loading",
  "ready",
  "succeeded",
  "empty",
  "degraded",
  "stale",
  "blocked",
  "failed",
] as const;

export type PanelStateKind = (typeof PANEL_STATE_KINDS)[number];

// Compile-time proof that the array above and the union above cannot drift apart. If a
// member is added to `PanelState` and not to `PANEL_STATE_KINDS` (or the reverse), one of
// these two assignments stops type-checking and `tsc -b` fails. The runtime test in
// panelState.test.ts covers the third way they can disagree — a duplicate entry.
const _kindsAreExhaustive: PanelStateKind extends PanelState<unknown>["kind"] ? true : never = true;
const _kindsAreSufficient: PanelState<unknown>["kind"] extends PanelStateKind ? true : never = true;
void _kindsAreExhaustive;
void _kindsAreSufficient;

/** How a state must be presented, independent of any panel's wording. */
export type PanelTone = "info" | "warning" | "alert";

/**
 * The payload a state puts on screen, or `null` when it has none.
 *
 * Exhaustive by `switch` rather than by a `"data" in state` probe, so that adding a kind
 * without deciding whether it carries data is a compile error rather than a silent `null`.
 */
export function panelData<T>(state: PanelState<T>): T | null {
  switch (state.kind) {
    case "ready":
    case "succeeded":
    case "degraded":
    case "stale":
      return state.data;
    case "idle":
    case "loading":
    case "empty":
    case "blocked":
    case "failed":
      return null;
  }
}

/**
 * `alert` for the two kinds that must reach a `role="alert"` branch, `warning` for the two
 * that show data that needs qualifying, `info` otherwise.
 */
export function panelTone<T>(state: PanelState<T>): PanelTone {
  switch (state.kind) {
    case "blocked":
    case "failed":
      return "alert";
    case "degraded":
    case "stale":
      return "warning";
    case "idle":
    case "loading":
    case "ready":
    case "succeeded":
    case "empty":
      return "info";
  }
}

/**
 * The state's own words, verbatim, or `null` when it has none of its own.
 *
 * Verbatim rather than reworded: these strings come from the backend's refusals, and
 * rewriting a specific refusal in the UI is how it becomes a generic one. Panels supply
 * their own copy for `idle`, because "nothing asked for yet" reads differently per panel.
 */
export function panelMessage<T>(state: PanelState<T>): string | null {
  switch (state.kind) {
    case "empty":
    case "degraded":
    case "stale":
    case "blocked":
      return state.reason;
    case "failed":
      return state.error;
    case "idle":
    case "loading":
    case "ready":
    case "succeeded":
      return null;
  }
}
