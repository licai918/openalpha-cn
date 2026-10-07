// V2-P5-018. The route container for page ④.
//
// What only a container test can see here: that the page does **not** ask on mount (it would
// have to invent a shortlist id), that an empty form is refused locally rather than turned
// into a 422 the user reads as "the policy is broken", and that every decimal leaves as a
// string.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PortfolioPage } from "./PortfolioPage";
import { buildPortfolioConstruction } from "../test/fixtures";

let calls: Array<{ url: string; body: unknown }>;
let respond: () => Response;

beforeEach(() => {
  calls = [];
  respond = () => Response.json(buildPortfolioConstruction());
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      return respond();
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function fillAndRun(shortlistId: string) {
  fireEvent.change(screen.getByLabelText("候选清单编号"), {
    target: { value: shortlistId },
  });
  fireEvent.click(screen.getByRole("button", { name: "构建组合" }));
}

describe("PortfolioPage", () => {
  it("asks nothing on mount, because a shortlist id cannot be invented", async () => {
    // `idle` is the honest state before the question is put. A page that picked "the first
    // stored shortlist" would report a portfolio for a list the user never chose.
    render(<PortfolioPage />);
    expect(screen.getByText("尚未构建组合")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("refuses an empty form locally instead of sending a request that must 422", async () => {
    render(<PortfolioPage />);
    fireEvent.click(screen.getByRole("button", { name: "构建组合" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请先填写候选清单编号。");
    expect(calls).toEqual([]);
  });

  it("constructs from the form and renders the weights the contract sent", async () => {
    render(<PortfolioPage />);
    fillAndRun("sla_typed");
    await waitFor(() => {
      expect(screen.getByText("000001.SZ")).toBeInTheDocument();
    });
    expect(calls[0].url).toBe("/api/v1/portfolio/construct");
    expect((calls[0].body as { shortlist_id: string }).shortlist_id).toBe("sla_typed");
  });

  it("sends every declared decimal as a string, from the form as well as the constants", async () => {
    // The client test pins this for a hand-built query; this pins that the *page* does not
    // parse its own form fields into numbers on the way past.
    render(<PortfolioPage />);
    fireEvent.change(screen.getByLabelText("单标的权重上限"), { target: { value: "0.25" } });
    fireEvent.change(screen.getByLabelText("换手预算（留空表示不设）"), {
      target: { value: "0.3" },
    });
    fillAndRun("sla_typed");
    await waitFor(() => expect(calls).toHaveLength(1));

    const limits = (calls[0].body as { policy: { limits: Record<string, unknown> } }).policy
      .limits;
    expect(limits.max_position_weight).toBe("0.25");
    expect(limits.turnover_budget).toBe("0.3");
    expect(typeof limits.max_position_weight).toBe("string");
  });

  it("treats an empty budget box as no budget rather than as a budget of zero", async () => {
    // The two are different requests and only one of them is what an empty box means: a
    // declared budget of zero forbids all trading, which is not "I did not declare one".
    render(<PortfolioPage />);
    fillAndRun("sla_typed");
    await waitFor(() => expect(calls).toHaveLength(1));
    const limits = (calls[0].body as { policy: { limits: Record<string, unknown> } }).policy
      .limits;
    expect(limits.turnover_budget).toBeNull();
    expect(limits.turnover_budget).not.toBe("0");
  });

  it("trims the typed id rather than requesting an address with a space in it", async () => {
    render(<PortfolioPage />);
    fillAndRun("  sla_padded  ");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect((calls[0].body as { shortlist_id: string }).shortlist_id).toBe("sla_padded");
  });

  it("renders the server's own refusal, which is how a gated shortlist is explained", async () => {
    // `construct_portfolio` refuses an `admitted: null` shortlist with a sentence naming the
    // gate. Replacing it with "construction failed" would delete the only useful half.
    respond = () =>
      new Response("this shortlist was refused by the gate and carries no admitted list", {
        status: 422,
      });
    render(<PortfolioPage />);
    fillAndRun("sla_blocked");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "this shortlist was refused by the gate and carries no admitted list",
      );
    });
  });

  it("still says something when what was thrown is not an Error", async () => {
    // `fetch` rejects with a `TypeError` on a network fault, but a stub, a polyfill or a
    // cancelled request can reject with anything at all. The fallback exists so that
    // `error.message` on a non-Error cannot turn a failure into a blank panel — the defect
    // `PanelState` was built to make unrepresentable.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw "a bare string, not an Error";
      }),
    );
    render(<PortfolioPage />);
    fillAndRun("sla_typed");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("组合构建失败");
    });
  });

  it("classifies a breached cap as degraded rather than rendering a clean portfolio", async () => {
    respond = () =>
      Response.json(
        buildPortfolioConstruction({
          turnover_budget: "0.3",
          turnover_damping: "0.48",
          caps_breached_after_turnover_damping: ["max_position_weight"],
        }),
      );
    render(<PortfolioPage />);
    fillAndRun("sla_typed");
    await waitFor(() => {
      expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
    });
    // The data is still on screen — `degraded` shows it, qualified.
    expect(screen.getByText("000001.SZ")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
