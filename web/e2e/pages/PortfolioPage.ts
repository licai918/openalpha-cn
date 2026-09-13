// V2-P5-021. Page ④ 组合与验证.
//
// Like page ①, this one starts `idle` on purpose: `POST /api/v1/portfolio/construct` needs a
// `shortlist_id`, and a mount-time request would have to invent one. So the money flow is
// "name a list, construct, read the weights", and the assertion that matters most is the one
// about a *rendered* weight rather than a summed one — `invested_weight` comes off the wire as
// the string `"1"` while the three targets sum to `0.9999999999999999` in IEEE-754.

import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

import { ROUTES } from "../../src/routes";
import { AppShell } from "./AppShell";

export type ConstructionRequest = {
  shortlistId?: string;
  maxPositionWeight?: string;
  turnoverBudget?: string;
};

export class PortfolioPage extends AppShell {
  constructor(page: Page) {
    super(page);
  }

  async goto(): Promise<void> {
    await this.page.goto(ROUTES.portfolio);
    await expect(this.heading()).toBeVisible();
  }

  heading(): Locator {
    return this.page.getByRole("heading", { name: "组合与验证" });
  }

  idleNotice(): Locator {
    return this.page.getByText("尚未构建组合");
  }

  async construct(request: ConstructionRequest = {}): Promise<void> {
    if (request.shortlistId !== undefined) {
      await this.page.getByLabel("候选清单编号").fill(request.shortlistId);
    }
    if (request.maxPositionWeight !== undefined) {
      await this.page.getByLabel("单标的权重上限").fill(request.maxPositionWeight);
    }
    if (request.turnoverBudget !== undefined) {
      await this.page.getByLabel("换手预算（留空表示不设）").fill(request.turnoverBudget);
    }
    await this.page.getByRole("button", { name: "构建组合" }).click();
  }

  refusal(): Locator {
    return this.page.getByRole("alert");
  }

  /** The sentence the backend refuses to let a construction omit. */
  methodNote(): Locator {
    return this.page.getByRole("note");
  }

  /** `已投权重`'s value: the field, not a sum of the target column. */
  investedWeight(): Locator {
    return this.page.locator(".portfolio-body .metric-row div").filter({ hasText: "已投权重" });
  }

  targetRow(subject: string): Locator {
    return this.page.getByRole("row").filter({ hasText: subject });
  }

  /** The three things this page states it cannot answer, rather than estimating them. */
  namedGaps(): Locator {
    return this.page.getByRole("heading", { level: 3, name: /本页无法作答/ });
  }
}
