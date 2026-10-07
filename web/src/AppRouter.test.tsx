// V2-P5-014. The routing tests, and the one thing they have to be able to separate.
//
// `V2-P5-019` left this row open with a reasoned argument: in a single-page app a routing
// test "cannot separate 'routing works' from 'the app rendered'", because with one route
// those two claims have the same evidence. That argument was correct and it is now spent —
// `015` and `016` land in this same change, so there are four locations rendering three
// different pages, and a router that did nothing at all would render the *same* page at
// every location. Every test below is written so that a no-op router fails it:
//
//   * `renders only the page the location names` asserts the *absence* of the other pages'
//     headings, not just the presence of one. A shell that rendered all three panels
//     stacked (which is what "the app rendered" looks like without routing) passes a
//     presence-only assertion and fails this one.
//   * `carries the shortlist id from the URL into the request` asserts the id reached the
//     network layer. A detail page hardcoding a fixture, or ignoring `useParams`, is green
//     on "the detail page rendered" and red here.
//   * `navigating swaps the page` asserts the previous page is *gone* after a click, which
//     separates real navigation from a nav bar that merely highlights itself.
//
// The unknown-path test is the `PanelNotice` ethos one layer up: `V2-P5-019` established
// that no panel state may render a blank panel, and a location that matches no route is
// the same defect at the routing layer — a blank shell that looks like a page that failed
// to load rather than an address that does not exist.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppRouter } from "./AppRouter";
import { ROUTES } from "./routes";
import { buildShortlistAnswer } from "./test/fixtures";

/** Every heading that identifies a page, so "only this one" can be asserted by exclusion. */
const PAGE_HEADINGS = {
  workbench: "证据时间线",
  dataHealth: "数据体检",
  shortlist: "候选清单",
} as const;

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRouter />
    </MemoryRouter>,
  );
}

/** Assert the named page is on screen and the other two are not. */
async function expectOnlyPage(page: keyof typeof PAGE_HEADINGS) {
  await waitFor(() => {
    expect(screen.getByRole("heading", { name: PAGE_HEADINGS[page] })).toBeInTheDocument();
  });
  for (const other of Object.keys(PAGE_HEADINGS) as (keyof typeof PAGE_HEADINGS)[]) {
    if (other === page) continue;
    expect(
      screen.queryByRole("heading", { name: PAGE_HEADINGS[other] }),
      `${PAGE_HEADINGS[other]} is rendered at a location that does not name it`,
    ).not.toBeInTheDocument();
  }
}

let requestedUrls: string[] = [];

beforeEach(() => {
  requestedUrls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url === "/health") {
        return Response.json({ status: "ok", version: "1.0.0" });
      }
      if (url === "/api/v1/shortlists") {
        return Response.json({ shortlist_ids: ["sl_aaa", "sl_bbb"] });
      }
      if (url.startsWith("/api/v1/shortlists/")) {
        return Response.json(buildShortlistAnswer());
      }
      if (url.startsWith("/api/v1/evidence")) {
        return Response.json({ items: [] });
      }
      return new Response("not stubbed", { status: 404 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("V2-P5-014 routing", () => {
  it("renders only the workbench at the root location", async () => {
    renderAt(ROUTES.workbench);
    await expectOnlyPage("workbench");
  });

  it("renders only the data-health page at its own location", async () => {
    // The test the previous row could not write. With one route, "the data-health page is
    // on screen" and "the app rendered" were the same observation; with three, a router
    // that ignores the location renders the workbench here and fails.
    renderAt(ROUTES.dataHealth);
    await expectOnlyPage("dataHealth");
  });

  it("renders only the shortlist index at its own location", async () => {
    renderAt(ROUTES.shortlists);
    await expectOnlyPage("shortlist");
  });

  it("carries the shortlist id from the URL into the request, not a hardcoded one", async () => {
    // Proves the path parameter is load-bearing. A detail page that renders a fixture, or
    // that reads a constant instead of `useParams`, renders correctly and fails here.
    renderAt(ROUTES.shortlistDetail("sl_from_the_url"));
    await waitFor(() => {
      expect(requestedUrls).toContain("/api/v1/shortlists/sl_from_the_url");
    });
  });

  it("encodes a shortlist id that would otherwise change the path it addresses", () => {
    // `shortlist_id` is a content address the server hands out, so this is defence in
    // depth rather than a live hazard — but a builder that interpolates raw is one server
    // change away from producing a location that addresses a different route entirely.
    expect(ROUTES.shortlistDetail("a/b")).toBe("/shortlists/a%2Fb");
  });

  it("renders a named not-found notice for an unknown location, never a blank shell", async () => {
    // The routing-layer form of V2-P5-019's "no kind produces a blank panel": an address
    // that matches nothing must say so, or it reads as a page that failed to load.
    const { container } = renderAt("/no-such-page");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("/no-such-page");
    expect((container.textContent ?? "").trim().length).toBeGreaterThan(0);
    for (const heading of Object.values(PAGE_HEADINGS)) {
      expect(screen.queryByRole("heading", { name: heading })).not.toBeInTheDocument();
    }
  });

  it("navigating by the nav bar swaps the page, not merely the highlight", async () => {
    renderAt(ROUTES.workbench);
    await expectOnlyPage("workbench");
    fireEvent.click(screen.getByRole("link", { name: "数据体检" }));
    await expectOnlyPage("dataHealth");
  });

  it("gives every page a nav link, so no route is reachable only by typing it", () => {
    renderAt(ROUTES.workbench);
    for (const item of NAV_LINK_LABELS) {
      expect(screen.getByRole("link", { name: item })).toBeInTheDocument();
    }
  });

  it("marks exactly one nav link as the current page", async () => {
    renderAt(ROUTES.dataHealth);
    await expectOnlyPage("dataHealth");
    const current = screen
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("数据体检");
  });
});

const NAV_LINK_LABELS = ["工作台", "数据体检", "候选清单"] as const;
