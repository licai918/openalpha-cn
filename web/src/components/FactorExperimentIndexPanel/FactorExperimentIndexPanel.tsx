// V2-P5-017. Page ③ part one: every sealed factor experiment, by content address.
//
// `ShortlistIndexPanel`'s shape and its reason, one plane over: the endpoint answers with
// keys rather than bodies, and each key is a link, so the server's content address and the
// app's URL are the same string. An experiment id is a `stable_model_id` over the sealed
// declaration, so the address names an immutable answer and can be handed to someone else.

import { Link } from "react-router";

import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import { ROUTES } from "../../routes";

type FactorExperimentIndexPanelProps = {
  state: PanelState<string[]>;
};

export function FactorExperimentIndexPanel(props: FactorExperimentIndexPanelProps) {
  const ids = panelData(props.state);

  return (
    <section
      className="panel factor-experiment-index-panel"
      aria-labelledby="factor-experiment-index-heading"
    >
      <header className="panel-heading">
        <div>
          <p className="eyebrow">P3 / FACTOR EXPERIMENTS</p>
          <h2 id="factor-experiment-index-heading">因子实验</h2>
        </div>
      </header>

      <PanelNotice state={props.state} idleText="尚未载入因子实验" />

      {ids !== null && (
        <ol className="shortlist-index">
          {ids.map((id) => (
            <li key={id}>
              <Link to={ROUTES.factorExperimentDetail(id)}>
                <code>{id}</code>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
