// V2-P5-014. The four locations this app has, and the shell around them.
//
// PRD Implementation Decision 24 is what makes this a requirement rather than a preference:
// "Web 应用演进为 4 个路由区域" — the workbench keeps its evidence / decision / replay /
// attribution views and gains 数据体检, 候选清单（含个股详情）, plus the two areas rows
// `017` and `018` will add. `NAV_ITEMS` carries three of those today; the remaining two get
// entries when their pages land, and `AppRouter.test.tsx` asserts every nav item resolves
// to a real page, so a link added ahead of its page fails rather than 404s in a user's face.
//
// **Declarative `<Routes>` rather than `createBrowserRouter`.** The data-router API's value
// is loaders, actions and deferred data — a fetch-before-render pipeline with its own error
// and pending model. This app already has a state model for exactly that (`PanelState`'s
// nine kinds, plus the `contractState` classifiers that derive six of them from contract
// *payload* fields a loader could not see), so adopting loaders would mean two vocabularies
// describing one thing. `<Routes>` gives the addressability this row is for and nothing else.
//
// **No `<Suspense>` / lazy boundary.** The whole bundle is 246 kB and the server is on
// 127.0.0.1; code-splitting here would trade a measured zero for real complexity.

import { NavLink, Route, Routes, useLocation } from "react-router";

import { App } from "./App";
import { DataHealthPage } from "./pages/DataHealthPage";
import { ShortlistDetailPage, ShortlistPage } from "./pages/ShortlistPage";
import { NAV_ITEMS, ROUTES, SHORTLIST_DETAIL_PATTERN } from "./routes";

/**
 * A location that matches no route.
 *
 * `V2-P5-019` established that no panel state may render a blank panel, because "an error
 * rendered as an empty success" is the defect this product cannot afford. An unmatched
 * location is the same defect at the routing layer, so it gets the same treatment: a
 * `role="alert"` that names the address that failed, rather than an empty shell that reads
 * like a page which failed to load.
 */
function NotFoundPage() {
  const location = useLocation();
  return (
    <section className="panel" aria-labelledby="not-found-heading">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">404 / NO SUCH LOCATION</p>
          <h2 id="not-found-heading">地址不存在</h2>
        </div>
      </header>
      <p className="error-state" role="alert">
        本应用没有 {location.pathname} 这个位置。请从上方导航进入某个区域。
      </p>
    </section>
  );
}

export function AppRouter() {
  return (
    <>
      <nav className="app-nav" aria-label="主导航">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            // `end` so that "/" is only current at "/" — without it the workbench link
            // stays marked as the current page on every other route, since every path
            // starts with "/". The "exactly one nav link is current" test pins that.
            end={item.path === ROUTES.workbench}
            className={({ isActive }) => (isActive ? "app-nav__link is-current" : "app-nav__link")}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route path={ROUTES.workbench} element={<App />} />
        <Route path={ROUTES.dataHealth} element={<DataHealthPage />} />
        <Route path={ROUTES.shortlists} element={<ShortlistPage />} />
        <Route path={SHORTLIST_DETAIL_PATTERN} element={<ShortlistDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </>
  );
}
