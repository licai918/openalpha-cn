// V2-P5-014. Every location this app has, in one place.
//
// Paths are built here rather than written as string literals at each `<Link>` and
// `<Route>`, so a path and the link that points at it cannot drift apart — the routing
// equivalent of the defect `V2-P5-019` fixed by giving four panels one `PanelNotice`
// instead of four copies of the same JSX.
//
// `shortlistDetail` is a *function*, and it encodes. `shortlist_id` is a content address
// the server mints (`stable_answer_digest` over the finished answer body), so today it is
// hex and could be interpolated raw with no ill effect. It is encoded anyway because the
// cost is one function call and the failure it prevents is silent: an id containing `/`
// would not produce a broken link, it would produce a *working* link to a different route.

export const ROUTES = {
  workbench: "/",
  dataHealth: "/data-health",
  shortlists: "/shortlists",
  shortlistDetail: (shortlistId: string) => `/shortlists/${encodeURIComponent(shortlistId)}`,
  // V2-P5-017 / V2-P5-018. The last two of PRD Decision 24's areas.
  factorLab: "/factor-lab",
  // Encoded for `shortlistDetail`'s reason, and it is the same class of id: `experiment_id`
  // is a content address the server mints (`stable_model_id(prefix="fxp", ...)`), so today
  // it is `fxp_` plus hex and interpolating it raw would be harmless. It is encoded anyway
  // because the failure it prevents is the silent one — an id containing `/` addresses the
  // *listing* route successfully rather than failing.
  factorExperimentDetail: (experimentId: string) =>
    `/factor-lab/${encodeURIComponent(experimentId)}`,
  portfolio: "/portfolio",
} as const;

/** The pattern react-router matches for the detail page, paired with its builder above. */
export const SHORTLIST_DETAIL_PATTERN = "/shortlists/:shortlistId";

/** The same pairing for page ③'s detail route. */
export const FACTOR_EXPERIMENT_DETAIL_PATTERN = "/factor-lab/:experimentId";

/** The nav bar, in order. Every route a user can reach has an entry: a location reachable
 * only by typing it is a location nobody will find. */
export const NAV_ITEMS: ReadonlyArray<{ path: string; label: string }> = [
  { path: ROUTES.workbench, label: "工作台" },
  { path: ROUTES.dataHealth, label: "数据体检" },
  { path: ROUTES.shortlists, label: "候选清单" },
  { path: ROUTES.factorLab, label: "因子与模型实验室" },
  { path: ROUTES.portfolio, label: "组合与验证" },
];
