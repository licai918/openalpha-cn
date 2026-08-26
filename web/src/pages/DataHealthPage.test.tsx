// V2-P5-015. The route container for page ①.
//
// `DataHealthPanel.test.tsx` covers what the report looks like; this covers what the page
// *does*. The two facts worth a container test are that it does not ask a question nobody
// put (it starts `idle` and stays there until the button is pressed), and that an empty
// form is refused locally rather than sent — a 422 rendered as a failed health check reads
// as "the panel is broken" when what happened is "the form was blank".

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DataHealthPage } from "./DataHealthPage";
import { buildPanelHealthReport } from "../test/fixtures";

let calls: string[];
let respond: () => Response;

beforeEach(() => {
  calls = [];
  respond = () => Response.json(buildPanelHealthReport());
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return respond();
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DataHealthPage", () => {
  it("asks nothing until the user asks, and says so", () => {
    // `idle` is the ninth kind for exactly this: four of this endpoint's query parameters
    // are required, so a mount-time fetch would have to invent a dataset and a year and
    // then report health for whatever it guessed.
    render(<DataHealthPage />);
    expect(screen.getByText("尚未运行数据体检")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("runs the check and renders the report the backend returned", async () => {
    render(<DataHealthPage />);
    fireEvent.click(screen.getByRole("button", { name: "运行体检" }));
    await waitFor(() => {
      expect(screen.getByText("index_membership_vs_daily")).toBeInTheDocument();
    });
    expect(calls[0]).toContain("/api/v1/panel/health?");
    expect(calls[0]).toContain("dataset=index_daily");
  });

  it("carries the edited form values into the request", async () => {
    render(<DataHealthPage />);
    fireEvent.change(screen.getByLabelText(/数据集/), {
      target: { value: "index_daily, index_member" },
    });
    fireEvent.change(screen.getByLabelText(/年份/), { target: { value: "2024,2025" } });
    fireEvent.change(screen.getByLabelText(/交易所/), { target: { value: "XSHE" } });
    fireEvent.click(screen.getByRole("button", { name: "运行体检" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    const url = new URL(calls[0], "http://127.0.0.1");
    expect(url.searchParams.getAll("dataset")).toEqual(["index_daily", "index_member"]);
    expect(url.searchParams.getAll("year")).toEqual(["2024", "2025"]);
    expect(url.searchParams.get("exchange")).toBe("XSHE");
  });

  it("refuses an empty form locally instead of sending a request that must 422", async () => {
    render(<DataHealthPage />);
    fireEvent.change(screen.getByLabelText(/数据集/), { target: { value: "  ,  " } });
    fireEvent.click(screen.getByRole("button", { name: "运行体检" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("请至少填写一个数据集与一个年份。");
    });
    expect(calls, "an unanswerable request was sent anyway").toEqual([]);
  });

  it("refuses a year field that holds no whole number", async () => {
    render(<DataHealthPage />);
    fireEvent.change(screen.getByLabelText(/年份/), { target: { value: "去年" } });
    fireEvent.click(screen.getByRole("button", { name: "运行体检" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(calls).toEqual([]);
  });

  it("renders the server's own words when the check itself fails", async () => {
    respond = () => new Response("panel store is not initialised", { status: 400 });
    render(<DataHealthPage />);
    fireEvent.click(screen.getByRole("button", { name: "运行体检" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("panel store is not initialised");
    });
  });

  it("renders a blocking report as a refusal carrying no report body", async () => {
    // The container's half of `panelHealthStateFrom`: the endpoint answers `200` for a
    // filthy panel exactly as it does for a clean one, so `response.ok` decides nothing
    // here and the body's own verdict decides everything.
    respond = () =>
      Response.json(
        buildPanelHealthReport({
          is_clean: false,
          counts_by_severity: { blocking: 1, warning: 0, notice: 0 },
          blocked_datasets: ["index_daily"],
          findings: [
            {
              code: "missing_year",
              category: "coverage",
              severity: "blocking",
              dataset: "index_daily",
              datasets: ["index_daily"],
              detail: "index_daily 缺少 2026 年分区。",
              year: 2026,
              count: null,
            },
          ],
        }),
      );
    render(<DataHealthPage />);
    fireEvent.click(screen.getByRole("button", { name: "运行体检" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("index_daily 缺少 2026 年分区。");
    });
    // A refusal renders no report table.
    expect(screen.queryByText("index_membership_vs_daily")).not.toBeInTheDocument();
  });
});
