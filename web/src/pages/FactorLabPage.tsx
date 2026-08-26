// V2-P5-017. The two route containers for page ③.
//
// Both listings fetch on mount, for `ShortlistPage`'s reason: neither endpoint takes a
// parameter, so there is nothing to ask and `idle` would be a state the user could only
// leave by pressing a button that adds no information. The detail route's only parameter is
// in the URL the user navigated to.
//
// The two listings are *independent* requests rendered as two panels, deliberately not
// awaited together. They answer different questions from different stores, and a failure of
// one is not a failure of the other — `Promise.all` here would let a broken prediction store
// blank out a perfectly readable factor experiment index.

import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { getFactorExperiment, listFactorExperiments, listPredictions } from "../api/client";
import { FactorExperimentIndexPanel } from "../components/FactorExperimentIndexPanel/FactorExperimentIndexPanel";
import { FactorExperimentPanel } from "../components/FactorExperimentPanel/FactorExperimentPanel";
import { PredictionRegisterPanel } from "../components/PredictionRegisterPanel/PredictionRegisterPanel";
import { factorExperimentStateFrom, predictionRegisterStateFrom } from "../contractState";
import type { PanelState } from "../panelState";
import type { FactorExperimentEnvelope, PredictionIndex } from "../types";

export function FactorLabPage() {
  const [experiments, setExperiments] = useState<PanelState<string[]>>({ kind: "loading" });
  const [predictions, setPredictions] = useState<PanelState<PredictionIndex>>({
    kind: "loading",
  });

  useEffect(() => {
    let cancelled = false;
    listFactorExperiments()
      .then((index) => {
        if (cancelled) return;
        setExperiments(
          index.experiment_ids.length === 0
            ? { kind: "empty", reason: "本地还没有任何已封存的因子实验。" }
            : { kind: "ready", data: index.experiment_ids },
        );
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setExperiments({
          kind: "failed",
          error: error instanceof Error ? error.message : "因子实验列表载入失败",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    listPredictions()
      .then((index) => {
        if (!cancelled) setPredictions(predictionRegisterStateFrom(index));
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setPredictions({
          kind: "failed",
          error: error instanceof Error ? error.message : "预测登记载入失败",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <FactorExperimentIndexPanel state={experiments} />
      <PredictionRegisterPanel state={predictions} />
    </>
  );
}

/**
 * One sealed experiment, fetched once per id.
 *
 * Split out and mounted under `key={experimentId}` for the reason `ShortlistAnswerView`
 * states: a synchronous `setState` in an effect body fails `react-hooks/set-state-in-effect`
 * and, on the substance, leaves a window in which the previous answer is on screen under the
 * new address. Remounting closes that window by construction.
 */
function FactorExperimentView({ experimentId }: { experimentId: string }) {
  const [state, setState] = useState<PanelState<FactorExperimentEnvelope>>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    getFactorExperiment(experimentId)
      .then((envelope) => {
        if (!cancelled) setState(factorExperimentStateFrom(envelope));
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "failed",
          error: error instanceof Error ? error.message : "因子实验载入失败",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [experimentId]);

  return <FactorExperimentPanel state={state} experimentId={experimentId} />;
}

export function FactorExperimentDetailPage() {
  const { experimentId } = useParams();

  // Unreachable through `AppRouter` — `/factor-lab/:experimentId` cannot match without a
  // segment to bind — but kept and tested for `ShortlistDetailPage`'s reason: the
  // alternative is requesting `/api/v1/factors/experiments/`, whose 404 would be reported to
  // the user as "that experiment does not exist" when the truth is that none was named.
  if (experimentId === undefined || experimentId === "") {
    return (
      <FactorExperimentPanel
        state={{ kind: "failed", error: "地址中没有实验编号。" }}
        experimentId=""
      />
    );
  }

  return <FactorExperimentView key={experimentId} experimentId={experimentId} />;
}
