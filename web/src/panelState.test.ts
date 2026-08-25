// V2-P5-019. The invariants the panel-state vocabulary exists to enforce.
//
// The row this file serves exists for one reason, stated in the roadmap and in
// `V2-P5-020`: **an error state must not be renderable as an empty success**. Before
// this module, three of the four panels carried `loading: boolean` + `error: string |
// null`, which makes `{loading: false, error: null, result: null}` — a blank panel that
// says nothing happened — indistinguishable from a request that failed and lost its
// message. `PanelState` makes that combination unrepresentable.
//
// These tests are deliberately *not* a restatement of the type. A `switch` over a
// discriminated union is already checked by `tsc`; asserting "the switch has nine arms"
// in vitest would be a test that cannot fail while the build is green. What is asserted
// here is the part `tsc` cannot see: that the runtime partition of the nine kinds into
// data-carrying / message-carrying / alert-worthy is the partition the panels rely on,
// so that adding a tenth kind — or adding `data` to `blocked` — goes red *here* rather
// than silently rendering a refusal as a verdict in a browser.

import { describe, expect, it } from "vitest";

import {
  PANEL_STATE_KINDS,
  panelData,
  panelMessage,
  panelTone,
  type PanelState,
  type PanelStateKind,
} from "./panelState";

/** One sample per kind, so a test can iterate the whole vocabulary rather than a
 * hand-picked subset. `data` is a sentinel string; nothing here depends on its shape. */
const SAMPLES: { [K in PanelStateKind]: PanelState<string> } = {
  idle: { kind: "idle" },
  loading: { kind: "loading" },
  ready: { kind: "ready", data: "payload" },
  succeeded: { kind: "succeeded", data: "payload" },
  empty: { kind: "empty", reason: "没有可见证据" },
  degraded: { kind: "degraded", data: "payload", reason: "部分来源不可再分发" },
  stale: { kind: "stale", data: "payload", reason: "证据已变更" },
  blocked: { kind: "blocked", reason: "风险门拒绝" },
  failed: { kind: "failed", error: "HTTP 500" },
};

/** The kinds whose whole purpose is to reach a `role="alert"` branch. */
const ALERT_KINDS: readonly PanelStateKind[] = ["blocked", "failed"];

/** The kinds that put data on screen. `degraded` and `stale` are here on purpose:
 * they are successes that must still be qualified, not failures. */
const DATA_KINDS: readonly PanelStateKind[] = ["ready", "succeeded", "degraded", "stale"];

describe("PANEL_STATE_KINDS", () => {
  it("enumerates every kind exactly once, so iterating it is iterating the union", () => {
    // `SAMPLES` is typed as a total mapped type over `PanelStateKind`, so `tsc` already
    // forces it to have one entry per kind. Comparing the *runtime* array against those
    // keys is what catches the other direction: a kind added to the type and to SAMPLES
    // but forgotten in the exported array, which would silently shrink every loop below.
    expect([...PANEL_STATE_KINDS].sort()).toEqual(Object.keys(SAMPLES).sort());
    expect(new Set(PANEL_STATE_KINDS).size).toBe(PANEL_STATE_KINDS.length);
  });
});

describe("panelData", () => {
  it("returns the payload for exactly the four data-carrying kinds", () => {
    const carrying = PANEL_STATE_KINDS.filter((kind) => panelData(SAMPLES[kind]) !== null);
    expect([...carrying].sort()).toEqual([...DATA_KINDS].sort());
  });

  it("returns null for every kind that has no data, so a panel cannot read a stale payload", () => {
    for (const kind of PANEL_STATE_KINDS) {
      if (DATA_KINDS.includes(kind)) continue;
      expect(panelData(SAMPLES[kind]), `${kind} must not carry data`).toBeNull();
    }
  });
});

describe("panelTone", () => {
  it("marks exactly blocked and failed as alerts", () => {
    const alerting = PANEL_STATE_KINDS.filter((kind) => panelTone(SAMPLES[kind]) === "alert");
    expect([...alerting].sort()).toEqual([...ALERT_KINDS].sort());
  });

  it("marks degraded and stale as warnings rather than alerts or plain successes", () => {
    // The distinction that matters: `degraded`/`stale` still show data, so they cannot be
    // alerts (an alert must not render a payload, asserted below), but they must not be
    // silently indistinguishable from `ready` either — that is the defect this row names.
    expect(panelTone(SAMPLES.degraded)).toBe("warning");
    expect(panelTone(SAMPLES.stale)).toBe("warning");
    expect(panelTone(SAMPLES.ready)).toBe("info");
    expect(panelTone(SAMPLES.succeeded)).toBe("info");
  });
});

describe("the invariant the row exists for", () => {
  it("no alert state carries data: a refusal can never be rendered as a verdict", () => {
    for (const kind of ALERT_KINDS) {
      expect(panelData(SAMPLES[kind]), `${kind} must not carry data`).toBeNull();
    }
  });

  it("every alert state carries a non-empty message: a refusal can never render blank", () => {
    // This is the half that `loading: boolean` + `error: string | null` could not express.
    // `{loading:false, error:null}` was a representable failure with nothing to say; here,
    // reaching an alert kind at all requires supplying the text that explains it.
    for (const kind of ALERT_KINDS) {
      const message = panelMessage(SAMPLES[kind]);
      expect(message, `${kind} must explain itself`).toBeTruthy();
      expect(message?.trim().length ?? 0).toBeGreaterThan(0);
    }
  });

  // Written first as "every kind except `loading`", which went red on `idle` — correctly.
  // `panelMessage` returns null for `idle` by design (the copy is panel-supplied: "尚未查询
  // 证据" reads differently from "尚未运行研究"), so `idle` behaves exactly like `loading`
  // and the original exemption set was simply wrong about the code, not the code wrong
  // about the row. The assertion below is the corrected rule, not a loosened one: the
  // exemption is pinned to those two by name and asserted to be exactly those two, so a
  // future kind cannot join them by being forgotten.
  const PRE_ANSWER_KINDS: readonly PanelStateKind[] = ["idle", "loading"];

  it("only idle and loading are pre-answer kinds; every other kind reports a backend outcome", () => {
    const withoutOwnContent = PANEL_STATE_KINDS.filter(
      (kind) =>
        panelData(SAMPLES[kind]) === null && (panelMessage(SAMPLES[kind])?.trim().length ?? 0) === 0,
    );
    expect([...withoutOwnContent].sort()).toEqual([...PRE_ANSWER_KINDS].sort());
  });

  it("every kind that reports an outcome shows data or says something — none renders blank", () => {
    for (const kind of PANEL_STATE_KINDS) {
      if (PRE_ANSWER_KINDS.includes(kind)) continue;
      const state = SAMPLES[kind];
      const shows = panelData(state) !== null || (panelMessage(state)?.trim().length ?? 0) > 0;
      expect(shows, `${kind} would render an empty panel`).toBe(true);
    }
  });
});

describe("panelMessage", () => {
  it("surfaces the reason a state was constructed with, verbatim", () => {
    // Verbatim rather than reworded: the reason strings come from the backend's own
    // refusals (`api/app.py` returns them as the response body), and rewriting a refusal
    // in the UI is how a specific refusal becomes a generic one.
    expect(panelMessage(SAMPLES.blocked)).toBe("风险门拒绝");
    expect(panelMessage(SAMPLES.failed)).toBe("HTTP 500");
    expect(panelMessage(SAMPLES.empty)).toBe("没有可见证据");
    expect(panelMessage(SAMPLES.degraded)).toBe("部分来源不可再分发");
    expect(panelMessage(SAMPLES.stale)).toBe("证据已变更");
  });

  it("returns null for the kinds that have nothing of their own to say", () => {
    expect(panelMessage(SAMPLES.idle)).toBeNull();
    expect(panelMessage(SAMPLES.loading)).toBeNull();
    expect(panelMessage(SAMPLES.ready)).toBeNull();
    expect(panelMessage(SAMPLES.succeeded)).toBeNull();
  });
});
