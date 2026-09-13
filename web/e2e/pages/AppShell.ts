// V2-P5-021. The shell every page object is reached through.
//
// Audit finding **F64** was "1 file, no page object". The value of a page object here is not
// tidiness — it is that a selector written once is a selector one commit can fix. Before this
// row, `getByRole("heading", { name: "证据时间线" })` appeared in two spec files with no
// connection between them, so renaming a heading broke two tests in two places for one reason.
//
// The nav labels and paths are *not* restated here. They are imported from `src/routes.ts`,
// which is the module that exists so "a path and the link that points at it cannot drift
// apart". A second copy in the e2e directory would be exactly the drift it was written against.

import type { ConsoleMessage, Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

import { NAV_ITEMS, ROUTES } from "../../src/routes";

/** The five areas PRD Decision 24 requires, in nav order, taken from the app's own table. */
export const NAV = NAV_ITEMS;

export class AppShell {
  /** Everything the browser complained about since this shell was constructed. */
  readonly browserComplaints: string[] = [];

  constructor(readonly page: Page) {
    page.on("console", (message: ConsoleMessage) => {
      if (message.type() === "error") this.browserComplaints.push(message.text());
    });
    page.on("pageerror", (error) => this.browserComplaints.push(error.message));
  }

  nav(): Locator {
    return this.page.getByRole("navigation", { name: "主导航" });
  }

  navLink(label: string): Locator {
    return this.nav().getByRole("link", { name: label });
  }

  /** Follow a nav link the way a user does, and wait for the address to actually change. */
  async clickNav(label: string, expectedPath: string): Promise<void> {
    await this.navLink(label).click();
    await expect(this.page).toHaveURL(new RegExp(`${escapeForUrl(expectedPath)}$`));
  }

  /** Exactly one nav link may be marked current; zero and two are both defects. */
  async expectExactlyOneCurrentArea(label: string): Promise<void> {
    await expect(this.nav().locator("a.is-current")).toHaveCount(1);
    await expect(this.nav().locator("a.is-current")).toHaveText(label);
  }

  /** The unmatched-location page, which must name the address rather than render blank. */
  notFoundAlert(): Locator {
    return this.page.getByRole("alert");
  }

  /** No panel may push the document sideways; asserted at the desktop viewport only. */
  async expectNoHorizontalOverflow(): Promise<void> {
    const width = await this.page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(width.scroll).toBeLessThanOrEqual(width.client);
  }

  expectQuietBrowser(): void {
    expect(this.browserComplaints).toEqual([]);
  }
}

export { ROUTES };

/** `.` and `-` are the only regex metacharacters the route table can produce today. */
function escapeForUrl(path: string): string {
  return path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
