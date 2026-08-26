// V2-P5-016. The two route containers for page ②.
//
// The container facts worth pinning here are the ones the panel tests structurally cannot
// see: that the detail page reads the id from the *URL* (so the address is the thing being
// bookmarked, not a prop somebody wired), that changing that id re-asks, and that an empty
// store is `empty` rather than a bare list rendering as nothing.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShortlistDetailPage, ShortlistPage } from "./ShortlistPage";
import { SHORTLIST_DETAIL_PATTERN } from "../routes";
import { buildShortlistAnswer } from "../test/fixtures";

let calls: string[];
let respond: (url: string) => Response;

beforeEach(() => {
  calls = [];
  respond = (url) =>
    url === "/api/v1/shortlists"
      ? Response.json({ shortlist_ids: ["sl_aaa", "sl_bbb"] })
      : Response.json(buildShortlistAnswer());
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return respond(String(input));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderDetailAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={SHORTLIST_DETAIL_PATTERN} element={<ShortlistDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ShortlistPage", () => {
  it("loads the stored answers on mount and links each one", async () => {
    render(
      <MemoryRouter>
        <ShortlistPage />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByRole("link", { name: "sl_aaa" })).toBeInTheDocument();
    });
    expect(calls).toEqual(["/api/v1/shortlists"]);
  });

  it("is empty, with words, when this installation holds no shortlist yet", async () => {
    respond = () => Response.json({ shortlist_ids: [] });
    render(
      <MemoryRouter>
        <ShortlistPage />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("本地还没有任何已存的候选清单。")).toBeInTheDocument();
    });
  });

  it("renders the failure's own words when the listing cannot be read", async () => {
    respond = () => new Response("shortlist store is unreadable", { status: 500 });
    render(
      <MemoryRouter>
        <ShortlistPage />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("shortlist store is unreadable");
    });
  });
});

describe("ShortlistDetailPage", () => {
  it("asks for the id in the address bar, not one it was handed", async () => {
    renderDetailAt("/shortlists/sl_from_the_url");
    await waitFor(() => expect(calls).toContain("/api/v1/shortlists/sl_from_the_url"));
  });

  it("re-asks when the address changes to a different answer", async () => {
    // A detail page that fetched only on mount would show the previous answer under the new
    // URL — and the URL is the thing this row made shareable, so it has to be the thing
    // that decides what is on screen.
    //
    // Navigation is driven by clicking a real link rather than by re-rendering with new
    // `initialEntries`: that prop is initial state, read once, so a rerender leaves the
    // location untouched and the test passes or fails for a reason that has nothing to do
    // with the page. (It failed here first, for exactly that reason.)
    render(
      <MemoryRouter initialEntries={["/shortlists/sl_first"]}>
        <Link to="/shortlists/sl_second">第二份</Link>
        <Routes>
          <Route path={SHORTLIST_DETAIL_PATTERN} element={<ShortlistDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(calls).toContain("/api/v1/shortlists/sl_first"));

    fireEvent.click(screen.getByRole("link", { name: "第二份" }));
    await waitFor(() => expect(calls).toContain("/api/v1/shortlists/sl_second"));
  });

  it("renders a refused list as a refusal, never as its funnel's names", async () => {
    respond = () =>
      Response.json(
        buildShortlistAnswer({
          is_blocked: true,
          admitted: null,
          blocks: [
            {
              code: "researched_ratio_below_minimum",
              detail: "已研究比例 0.42 低于要求的 0.80。",
              measured: 0.42,
              required: 0.8,
            },
          ],
        }),
      );
    renderDetailAt("/shortlists/sl_refused");

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("已研究比例 0.42 低于要求的 0.80。");
    });
    // The funnel had two names in it. Neither is on screen.
    expect(screen.queryByText("000001.SZ")).not.toBeInTheDocument();
    expect(screen.queryByText("000002.SZ")).not.toBeInTheDocument();
  });

  it("says no shortlist was named rather than requesting an empty path segment", async () => {
    // Unreachable through `AppRouter` — the route pattern cannot match without a segment —
    // but the alternative implementation requests `/api/v1/shortlists/` and reports its 404
    // as "that shortlist does not exist", which is a different and wrong statement.
    render(
      <MemoryRouter initialEntries={["/shortlists"]}>
        <Routes>
          <Route path="/shortlists" element={<ShortlistDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("地址中没有清单编号。");
    expect(calls).toEqual([]);
  });

  it("renders the failure's own words when the id addresses nothing", async () => {
    respond = () => new Response("no shortlist is filed under sl_missing", { status: 404 });
    renderDetailAt("/shortlists/sl_missing");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("no shortlist is filed under sl_missing");
    });
  });
});
