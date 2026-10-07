// V2-P5-016. Page ② part one: every stored shortlist, by content address.
//
// The listing is of keys rather than of bodies, which is the endpoint's own shape and its
// own reason ("a shortlist answer is kilobytes and the caller almost always wants one").
// Each key is a link to the detail route, so the server's content address and the app's
// URL are the same string — which is the concrete reason this row wanted a router at all:
// a research terminal has to be able to hand someone the address of a specific answer.

import { Link } from "react-router";

import { panelData, type PanelState } from "../../panelState";
import { PanelNotice } from "../PanelNotice/PanelNotice";
import { ROUTES } from "../../routes";

type ShortlistIndexPanelProps = {
  state: PanelState<string[]>;
};

export function ShortlistIndexPanel(props: ShortlistIndexPanelProps) {
  const ids = panelData(props.state);

  return (
    <section className="panel shortlist-index-panel" aria-labelledby="shortlist-index-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">P2 / SHORTLISTS</p>
          <h2 id="shortlist-index-heading">候选清单</h2>
        </div>
      </header>

      <PanelNotice state={props.state} idleText="尚未载入候选清单" />

      {ids !== null && (
        <ol className="shortlist-index">
          {ids.map((id) => (
            <li key={id}>
              <Link to={ROUTES.shortlistDetail(id)}>
                <code>{id}</code>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
