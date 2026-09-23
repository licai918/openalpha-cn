// V2-P5-017. The two route containers for page ③.
//
// The container facts the panel tests structurally cannot see: that the detail page reads
// the id from the *URL*, that changing it re-asks, and — the one specific to this page —
// that the two listings are independent, so a broken prediction store does not blank out a
// readable factor experiment index.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FactorExperimentDetailPage, FactorLabPage } from "./FactorLabPage";
import { FACTOR_EXPERIMENT_DETAIL_PATTERN } from "../routes";
import { buildFactorExperiment, buildPredictionIndex } from "../test/fixtures";

let calls: string[];
let respond: (url: string) => Response;

beforeEach(() => {
  calls = [];
  respond = (url) => {
    if (url === "/api/v1/factors/experiments") {
      return Response.json({ experiment_ids: ["fxp_aaa", "fxp_bbb"] });
    }
    if (url === "/api/v1/predictions") {
      return Response.json(buildPredictionIndex());
    }
    return Response.json(buildFactorExperiment());
  };
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
        <Route path={FACTOR_EXPERIMENT_DETAIL_PATTERN} element={<FactorExperimentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FactorLabPage", () => {
  it("loads both listings on mount and links every experiment", async () => {
    render(
      <MemoryRouter>
        <FactorLabPage />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByRole("link", { name: "fxp_aaa" })).toHaveAttribute(
        "href",
        "/factor-lab/fxp_aaa",
      );
    });
    expect(calls).toContain("/api/v1/factors/experiments");
    expect(calls).toContain("/api/v1/predictions");
  });

  it("keeps a readable experiment index when the prediction register fails", async () => {
    // The reason the two effects are not a `Promise.all`. They read different stores and
    // answer different questions, so one store being unreadable must not blank the other's
    // panel — which is exactly what awaiting them together would do.
    respond = (url) =>
      url === "/api/v1/predictions"
        ? new Response("prediction store unreadable", { status: 500 })
        : Response.json({ experiment_ids: ["fxp_aaa"] });

    render(
      <MemoryRouter>
        <FactorLabPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("prediction store unreadable");
    });
    // ...and the other panel still has its data.
    expect(screen.getByRole("link", { name: "fxp_aaa" })).toBeInTheDocument();
  });

  it("calls an empty experiment store empty rather than rendering nothing", async () => {
    respond = (url) =>
      url === "/api/v1/factors/experiments"
        ? Response.json({ experiment_ids: [] })
        : Response.json(buildPredictionIndex());

    render(
      <MemoryRouter>
        <FactorLabPage />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("本地还没有任何已封存的因子实验。")).toBeInTheDocument();
    });
  });
});

describe("a request that lands after its component is gone", () => {
  // The `cancelled` flag in all three effects is unreachable by any test that lets the
  // request finish first, so it is reached here by holding every response behind a gate and
  // unmounting before opening it. The window is real: a user clicking through the nav
  // faster than the local server answers takes exactly this path, and without the flag the
  // late answer would be written into a component React has already thrown away.

  function gatedFetch(behaviour: "resolve" | "reject") {
    let open: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        await gate;
        if (behaviour === "reject") throw new Error("answered too late");
        return respond(String(input));
      }),
    );
    return open;
  }

  /** Let the gated promise and every `.then` chained off it run to completion. */
  async function flush() {
    for (let index = 0; index < 4; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  it("drops a listing answer that arrives after the page unmounted", async () => {
    const open = gatedFetch("resolve");
    const { unmount } = render(
      <MemoryRouter>
        <FactorLabPage />
      </MemoryRouter>,
    );
    unmount();
    open();
    await flush();
    expect(document.body.textContent).toBe("");
  });

  it("drops a listing failure that arrives after the page unmounted", async () => {
    const open = gatedFetch("reject");
    const { unmount } = render(
      <MemoryRouter>
        <FactorLabPage />
      </MemoryRouter>,
    );
    unmount();
    open();
    await flush();
    expect(document.body.textContent).toBe("");
  });

  it("drops a detail answer that arrives after the address changed away", async () => {
    const open = gatedFetch("resolve");
    const { unmount } = renderDetailAt("/factor-lab/fxp_gone");
    unmount();
    open();
    await flush();
    expect(document.body.textContent).toBe("");
  });

  it("drops a detail failure that arrives after the address changed away", async () => {
    const open = gatedFetch("reject");
    const { unmount } = renderDetailAt("/factor-lab/fxp_gone");
    unmount();
    open();
    await flush();
    expect(document.body.textContent).toBe("");
  });
});

describe("FactorExperimentDetailPage", () => {
  it("carries the experiment id from the URL into the request", async () => {
    // The assertion that makes the address load-bearing: a page that ignored `useParams` and
    // used a fixture id would still render an experiment, and would still pass "the detail
    // page rendered".
    renderDetailAt("/factor-lab/fxp_from_the_url");
    await waitFor(() => {
      expect(calls).toContain("/api/v1/factors/experiments/fxp_from_the_url");
    });
  });

  it("re-asks when the address changes, and does not show the previous answer under it", async () => {
    render(
      <MemoryRouter initialEntries={["/factor-lab/fxp_first"]}>
        <Routes>
          <Route
            path={FACTOR_EXPERIMENT_DETAIL_PATTERN}
            element={
              <>
                <Link to="/factor-lab/fxp_second">next</Link>
                <FactorExperimentDetailPage />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(calls).toContain("/api/v1/factors/experiments/fxp_first"));

    fireEvent.click(screen.getByRole("link", { name: "next" }));
    await waitFor(() => expect(calls).toContain("/api/v1/factors/experiments/fxp_second"));
    // The heading is the id from the URL, so the two cannot silently disagree.
    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("fxp_second");
    });
  });

  it("renders the server's own refusal rather than a generic load failure", async () => {
    // A 409 here means the stored document no longer hashes to its seal — "the artifact on
    // disk was edited", which is a very different thing from "not found". Rewriting it as a
    // generic message would delete the only sentence that says which.
    respond = () => new Response("the sealed digest does not match the content", { status: 409 });
    renderDetailAt("/factor-lab/fxp_broken");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "the sealed digest does not match the content",
      );
    });
  });

  it("still says something when what was thrown is not an Error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw "a bare string, not an Error";
      }),
    );
    renderDetailAt("/factor-lab/fxp_broken");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("因子实验载入失败");
    });
  });

  it("says no experiment was named rather than requesting a trailing-slash address", async () => {
    render(
      <MemoryRouter initialEntries={["/factor-lab/"]}>
        <Routes>
          <Route path="/factor-lab/*" element={<FactorExperimentDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("地址中没有实验编号。");
    expect(calls).toEqual([]);
  });
});
