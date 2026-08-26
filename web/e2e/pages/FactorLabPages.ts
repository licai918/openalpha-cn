// V2-P5-021. Page ③ 因子与模型实验室 and its experiment detail route.
//
// The index route renders **two** panels from **two independent** requests, deliberately not
// awaited together: a broken prediction store must not blank out a readable factor experiment
// index. That independence is a claim a browser test can check and a component test cannot,
// because it is about two in-flight requests rather than two rendered states — so the page
// object exposes both panels separately and the spec fails one endpoint at a time.

import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

import { ROUTES } from "../../src/routes";
import { AppShell } from "./AppShell";

export class FactorLabPage extends AppShell {
  constructor(page: Page) {
    super(page);
  }

  async goto(): Promise<void> {
    await this.page.goto(ROUTES.factorLab);
    await expect(this.experimentsHeading()).toBeVisible();
  }

  experimentsHeading(): Locator {
    return this.page.getByRole("heading", { name: "因子实验", exact: true });
  }

  predictionsHeading(): Locator {
    return this.page.getByRole("heading", { name: "模型预测登记（样本外立场）" });
  }

  experimentsPanel(): Locator {
    return this.page.locator(".panel").filter({ has: this.experimentsHeading() });
  }

  predictionsPanel(): Locator {
    return this.page.locator(".panel").filter({ has: this.predictionsHeading() });
  }

  entry(experimentId: string): Locator {
    return this.page.getByRole("link", { name: experimentId });
  }

  async open(experimentId: string): Promise<FactorExperimentDetailPage> {
    await this.entry(experimentId).click();
    await expect(this.page).toHaveURL(new RegExp(`/factor-lab/${experimentId}$`));
    return new FactorExperimentDetailPage(this.page);
  }
}

export class FactorExperimentDetailPage extends AppShell {
  constructor(page: Page) {
    super(page);
  }

  async goto(experimentId: string): Promise<void> {
    await this.page.goto(ROUTES.factorExperimentDetail(experimentId));
    await expect(this.heading(experimentId)).toBeVisible();
  }

  heading(experimentId: string): Locator {
    return this.page.getByRole("heading", { name: `因子实验 ${experimentId}` });
  }

  definition(): Locator {
    return this.page.getByRole("heading", { level: 3 }).first();
  }

  tierRow(tier: string): Locator {
    return this.page.getByRole("row").filter({ hasText: tier });
  }
}
