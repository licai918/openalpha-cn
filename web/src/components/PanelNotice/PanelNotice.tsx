import { panelData, panelMessage, panelTone, type PanelState } from "../../panelState";

type PanelNoticeProps = {
  /** Accepts any panel's state: the payload type is never read here, only its presence. */
  state: PanelState<unknown>;
  /** Panel-specific copy for `idle`, which has no message of its own. */
  idleText: string;
};

/**
 * The one place a panel state becomes markup (V2-P5-019).
 *
 * All four panels render this, so `role="alert"` is emitted from a single line rather than
 * copy-pasted four times with four chances to differ. That matters here specifically: the
 * row's finding was that the panels had *diverged* in how they signalled failure, and the
 * fix is not "write the same JSX four times more carefully" but "have one implementation".
 *
 * `degraded` and `stale` deliberately render a `role="status"` banner **in addition to**
 * the panel's data, not instead of it — the data is real and worth showing, it just must
 * not be shown as an unqualified success. `blocked` and `failed` render `role="alert"` and
 * the panel renders no data at all, because `panelData` returns null for both by
 * construction (asserted in panelState.test.ts).
 */
export function PanelNotice({ state, idleText }: PanelNoticeProps) {
  if (state.kind === "loading") {
    return (
      <div className="skeleton-stack" aria-busy="true" aria-label="正在加载">
        <span />
        <span />
        <span />
      </div>
    );
  }

  if (state.kind === "idle") {
    return <p className="empty-state">{idleText}</p>;
  }

  const message = panelMessage(state);
  if (message === null) {
    // `ready` / `succeeded`: the panel's own data block is the whole answer.
    return null;
  }

  const tone = panelTone(state);
  if (tone === "alert") {
    return (
      <p className="error-state" role="alert">
        {message}
      </p>
    );
  }
  if (tone === "warning") {
    return (
      <p className="warning-state" role="status">
        {message}
      </p>
    );
  }
  return <p className="empty-state">{message}</p>;
}

/** Convenience re-export so panels import their data accessor from one place. */
export { panelData };
