// V2-P5-021. Page ② 候选清单 and its detail route 个股详情.
//
// Two objects rather than one, because they are two addresses. The index fetches on mount and
// the detail fetches on mount *per id* — `ShortlistAnswerView` is keyed by `shortlistId`, so a
// second id is a second component instance whose state starts at `loading`. The money flow
// this pair encodes is the one a user actually performs: land on the index, click an id, read
// the answer, and be able to send that URL to somebody else.

import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

import { ROUTES } from "../../src/routes";
import { AppShell } from "./AppShell";

export class ShortlistIndexPage extends AppShell {
  constructor(page: Page) {
    super(page);
  }

  async goto(): Promise<void> {
    await this.page.goto(ROUTES.shortlists);
    await expect(this.heading()).toBeVisible();
  }

  heading(): Locator {
    return this.page.getByRole("heading", { name: "候选清单" });
  }

  entry(shortlistId: string): Locator {
    return this.page.getByRole("link", { name: shortlistId });
  }

  async open(shortlistId: string): Promise<ShortlistDetailPage> {
    await this.entry(shortlistId).click();
    await expect(this.page).toHaveURL(new RegExp(`/shortlists/${shortlistId}$`));
    return new ShortlistDetailPage(this.page);
  }
}

export class ShortlistDetailPage extends AppShell {
  constructor(page: Page) {
    super(page);
  }

  async goto(shortlistId: string): Promise<void> {
    await this.page.goto(ROUTES.shortlistDetail(shortlistId));
    await expect(this.heading()).toBeVisible();
  }

  heading(): Locator {
    return this.page.getByRole("heading", { name: "个股详情" });
  }

  /** The scoring declaration: the universe this list was cut from and how it was scored. */
  declaration(): Locator {
    return this.page.getByRole("heading", { level: 3, name: "股票池与打分口径" });
  }

  admittedRow(subject: string): Locator {
    return this.page.getByRole("row").filter({ hasText: subject });
  }

  runManifest(runId: string): Locator {
    return this.page.getByText(runId);
  }
}
