// V2-P5-021. Page ① 数据体检.
//
// The money flow here is a *form*, not a mount: `DataHealthPage.tsx` starts `idle` on purpose,
// because `GET /api/v1/panel/health` has four required query parameters and a mount-time
// request would have to invent a dataset and a year. So the page object exposes the question
// as a question — `ask({...})` — and the test that matters is the one that proves nothing was
// requested before it was asked.

import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

import { ROUTES } from "../../src/routes";
import { AppShell } from "./AppShell";

export type HealthQuestion = {
  datasets?: string;
  years?: string;
  exchange?: string;
};

export class DataHealthPage extends AppShell {
  constructor(page: Page) {
    super(page);
  }

  async goto(): Promise<void> {
    await this.page.goto(ROUTES.dataHealth);
    await expect(this.heading()).toBeVisible();
  }

  heading(): Locator {
    return this.page.getByRole("heading", { name: "数据体检" });
  }

  /** The state before the question is put, which is a different fact from "nothing found". */
  idleNotice(): Locator {
    return this.page.getByText("尚未运行数据体检");
  }

  async ask(question: HealthQuestion = {}): Promise<void> {
    if (question.datasets !== undefined) {
      await this.page.getByLabel("数据集（逗号分隔）").fill(question.datasets);
    }
    if (question.years !== undefined) {
      await this.page.getByLabel("年份（逗号分隔）").fill(question.years);
    }
    if (question.exchange !== undefined) {
      await this.page.getByLabel("交易所").fill(question.exchange);
    }
    await this.page.getByRole("button", { name: "运行体检" }).click();
  }

  /** The refusal the page issues itself rather than sending an empty form to a 422. */
  refusal(): Locator {
    return this.page.getByRole("alert");
  }

  checkedAt(): Locator {
    return this.page.getByRole("term").filter({ hasText: "体检时点" });
  }

  crossChecks(): Locator {
    return this.page.getByRole("heading", { level: 3 }).filter({ hasText: "跨数据集检查" });
  }
}
