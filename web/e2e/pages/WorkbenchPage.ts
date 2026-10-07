// V2-P5-021. The workbench: evidence → decision → replay → attribution.
//
// Attribution is the half audit finding F64 says the suite never reached. It is reachable
// only after a research run, because `AttributionPanel` disables its button on `!hasResearch`
// — so "click 计算归因" is not a step a test can take on its own, and the flow is the unit.

import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

import { ROUTES } from "../../src/routes";
import { AppShell } from "./AppShell";

export class WorkbenchPage extends AppShell {
  constructor(page: Page) {
    super(page);
  }

  async goto(): Promise<void> {
    await this.page.goto(ROUTES.workbench);
    await expect(this.evidenceHeading()).toBeVisible();
  }

  evidenceHeading(): Locator {
    return this.page.getByRole("heading", { name: "证据时间线" });
  }

  serviceStatus(): Locator {
    return this.page.getByText("服务正常");
  }

  async queryEvidence(): Promise<void> {
    await this.page.getByRole("button", { name: "查询证据" }).click();
  }

  evidenceSummary(): Locator {
    return this.page.getByText("合成涨停证据。");
  }

  async runResearch(): Promise<void> {
    await this.page.getByRole("button", { name: "运行研究" }).click();
  }

  finalAction(): Locator {
    return this.page.getByText("观察", { exact: true });
  }

  routingPath(): Locator {
    return this.page.getByText("market-agent → risk-gate");
  }

  attributionButton(): Locator {
    return this.page.getByRole("button", { name: "计算归因" });
  }

  async runAttribution(): Promise<void> {
    await expect(this.attributionButton()).toBeEnabled();
    await this.attributionButton().click();
  }

  /** The net active return the server computed, read from the panel's own total row. */
  netActiveReturn(): Locator {
    return this.page.locator(".attribution-total").first();
  }

  /** The residual the panel is required to print rather than spread over the named terms. */
  unexplainedResidual(): Locator {
    return this.page.locator(".attribution-residual");
  }

  attributionTerm(name: string): Locator {
    return this.page.locator(".attribution-list li").filter({ hasText: name });
  }
}
