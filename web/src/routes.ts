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
} as const;

/** The pattern react-router matches for the detail page, paired with its builder above. */
export const SHORTLIST_DETAIL_PATTERN = "/shortlists/:shortlistId";

/** The nav bar, in order. Every route a user can reach has an entry: a location reachable
 * only by typing it is a location nobody will find. */
export const NAV_ITEMS: ReadonlyArray<{ path: string; label: string }> = [
  { path: ROUTES.workbench, label: "工作台" },
  { path: ROUTES.dataHealth, label: "数据体检" },
  { path: ROUTES.shortlists, label: "候选清单" },
];
