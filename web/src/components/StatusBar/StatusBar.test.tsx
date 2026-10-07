// V2-P5-020. StatusBar rendered in isolation.
//
// StatusBar is the only panel-level component outside the four `PanelState` panels, so it
// is the one that the shared contract suite cannot reach. It was at 100% statements and
// 83.33% branches purely as a side effect of App.test.tsx mounting the whole tree — the
// uncovered branch being the one where the backend answers with a non-`ok` status, which
// is the only interesting thing this component does.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBar } from "./StatusBar";

describe("StatusBar", () => {
  it("reports a healthy backend with its version", () => {
    const { container } = render(
      <StatusBar health={{ status: "ok", version: "1.2.3" }} error={null} />
    );
    expect(screen.getByText("服务正常")).toBeInTheDocument();
    expect(screen.getByText("API 1.2.3")).toBeInTheDocument();
    expect(container.querySelector(".status-dot--ok")).not.toBeNull();
  });

  it("does not call the service healthy when the backend reports status=error", () => {
    // The branch App.test.tsx never reached: a reachable backend that says it is unwell.
    // "服务正常" here would be the status bar contradicting the service it reports on.
    const { container } = render(
      <StatusBar health={{ status: "error", version: "1.2.3" }} error={null} />
    );
    expect(screen.queryByText("服务正常")).toBeNull();
    expect(screen.getByText("连接中")).toBeInTheDocument();
    expect(container.querySelector(".status-dot--error")).not.toBeNull();
  });

  it("shows the transport error verbatim and lets it win over a stale health payload", () => {
    render(<StatusBar health={{ status: "ok", version: "1.2.3" }} error="连接被拒绝" />);
    expect(screen.getByText("异常")).toBeInTheDocument();
    expect(screen.getByText("连接被拒绝")).toBeInTheDocument();
    expect(screen.queryByText("API 1.2.3")).toBeNull();
  });

  it("says it is still connecting before the first answer arrives", () => {
    const { container } = render(<StatusBar health={null} error={null} />);
    expect(screen.getByText("连接中")).toBeInTheDocument();
    expect(screen.getByText("API —")).toBeInTheDocument();
    expect(container.querySelector(".status-dot--loading")).not.toBeNull();
  });

  it("is announced politely rather than as an alert", () => {
    const { container } = render(<StatusBar health={null} error="连接被拒绝" />);
    expect(container.querySelector('[aria-live="polite"]')).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
