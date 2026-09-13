// V2-P5-016. The two route containers for page ②.
//
// Unlike page ①, both of these do fetch on mount, and for the same reason page ① does not:
// the question is already fully determined. `GET /api/v1/shortlists` takes no parameters at
// all, and the detail route's only parameter is in the URL the user navigated to. There is
// nothing to ask, so `idle` would be a state the user could never leave except by pressing
// a button that adds no information.

import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { getShortlist, listShortlists } from "../api/client";
import { ShortlistDetailPanel } from "../components/ShortlistDetailPanel/ShortlistDetailPanel";
import { ShortlistIndexPanel } from "../components/ShortlistIndexPanel/ShortlistIndexPanel";
import { shortlistStateFrom } from "../contractState";
import type { PanelState } from "../panelState";
import type { ShortlistAnswer } from "../types";

export function ShortlistPage() {
  const [state, setState] = useState<PanelState<string[]>>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    listShortlists()
      .then((index) => {
        if (cancelled) return;
        setState(
          index.shortlist_ids.length === 0
            ? { kind: "empty", reason: "本地还没有任何已存的候选清单。" }
            : { kind: "ready", data: index.shortlist_ids },
        );
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "failed",
          error: error instanceof Error ? error.message : "候选清单列表载入失败",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <ShortlistIndexPanel state={state} />;
}

/**
 * One stored answer, fetched once per id.
 *
 * Split out of `ShortlistDetailPage` and mounted under `key={shortlistId}` so that a new id
 * is a new component instance whose state *starts* at `loading`. The obvious alternative —
 * one instance whose effect sets `loading` and then fetches — fails
 * `react-hooks/set-state-in-effect`, and the lint rule is right on the substance rather than
 * merely on style: a synchronous `setState` in an effect body renders twice for every id
 * change, and it leaves a window in which the previous answer is on screen under the new
 * address. Remounting closes that window by construction.
 */
function ShortlistAnswerView({ shortlistId }: { shortlistId: string }) {
  const [state, setState] = useState<PanelState<ShortlistAnswer>>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    getShortlist(shortlistId)
      .then((answer) => {
        if (!cancelled) setState(shortlistStateFrom(answer));
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "failed",
          error: error instanceof Error ? error.message : "候选清单载入失败",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [shortlistId]);

  return <ShortlistDetailPanel state={state} shortlistId={shortlistId} />;
}

export function ShortlistDetailPage() {
  // `useParams` and not a prop: the URL is the source of truth for which answer this is, so
  // the address can be bookmarked and shared — which is the whole reason this row wanted a
  // router. `shortlist_id` is a content address, so the URL names an immutable answer.
  const { shortlistId } = useParams();

  // Unreachable through `AppRouter`, since `/shortlists/:shortlistId` cannot match without a
  // segment to bind. Kept, and tested directly, because the alternative is requesting
  // `/api/v1/shortlists/` — a wrong request whose 404 would be reported to the user as
  // "that shortlist does not exist" when the truth is that no shortlist was named.
  if (shortlistId === undefined || shortlistId === "") {
    return (
      <ShortlistDetailPanel
        state={{ kind: "failed", error: "地址中没有清单编号。" }}
        shortlistId=""
      />
    );
  }

  return <ShortlistAnswerView key={shortlistId} shortlistId={shortlistId} />;
}
