import type { Page } from "@playwright/test";
import { ALICE, conversation, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

const STATUS = "What is the status of my tickets?";

/** A conversation long enough to scroll, ending at the list of tickets to choose from. */
async function reachTicketChoice(page: Page, controls: { seed: (t: never) => Promise<string> }) {
  for (const summary of ["Printer offline", "Outlook keeps crashing", "Laptop battery drains"]) {
    await (controls.seed as (t: unknown) => Promise<string>)({
      email: ALICE.email,
      summary,
      comments: [{ body: "Support update about this ticket. ".repeat(30) }],
    });
  }
  for (const question of [
    "Request for VPN setup for remote work",
    "Unable to reset password via self-service portal",
    "Request for VPN setup for remote work",
  ]) {
    await say(page, question);
    await expect(conversation(page)).toContainText("Here is what our knowledge base says");
    await expect(page.getByRole("status").filter({ hasText: /typing/i })).toHaveCount(0);
  }
  await say(page, STATUS);
  await expect(page.getByRole("region", { name: "Choose a ticket" })).toBeVisible();
}

const scrollOf = (page: Page) =>
  page.evaluate(() => {
    const log = document.querySelector(".log") as HTMLElement;
    return {
      log: Math.round(log.scrollTop),
      max: Math.round(log.scrollHeight - log.clientHeight),
      page: window.scrollY,
    };
  });

/** Hold the next chat reply back, so the moment between a click and its answer can be observed. */
async function delayReplies(page: Page, ms: number) {
  await page.route("**/api/chat", async (route) => {
    if (route.request().method() === "POST") await new Promise((r) => setTimeout(r, ms));
    await route.continue();
  });
}

test.use({ viewport: { width: 1280, height: 600 } });

test.beforeEach(async ({ page, controls: _controls }) => {
  await signIn(page);
});

test("clicking a ticket does not move the chat while the reply is on its way", async ({
  page,
  controls,
}) => {
  await reachTicketChoice(page, controls);
  const before = await scrollOf(page);
  expect(before.max).toBeGreaterThan(0); // there is something to scroll
  await delayReplies(page, 1500);
  await page.getByRole("region", { name: "Choose a ticket" }).getByRole("button").first().click();
  await expect(page.getByRole("status").filter({ hasText: /typing/i })).toBeVisible();
  await page.waitForTimeout(400);
  const during = await scrollOf(page);
  expect(during.log).toBe(before.log);
  expect(during.page).toBe(0);
});

test("a reply is brought into view when the reader was at the bottom", async ({
  page,
  controls,
}) => {
  await reachTicketChoice(page, controls);
  await page.getByRole("region", { name: "Choose a ticket" }).getByRole("button").first().click();
  await expect(conversation(page)).toContainText("Latest updates from the support team");
  const visible = await page.evaluate(() => {
    const log = document.querySelector(".log") as HTMLElement;
    const last = log.querySelector("ol > li:last-child") as HTMLElement;
    const l = log.getBoundingClientRect();
    const r = last.getBoundingClientRect();
    return { topIn: r.top >= l.top - 1, bottomIn: r.bottom <= l.bottom + 1 };
  });
  expect(visible.topIn && visible.bottomIn).toBe(true);
});

test("someone reading earlier messages is not pulled down by a reply", async ({
  page,
  controls,
}) => {
  await reachTicketChoice(page, controls);
  await page.locator(".log").evaluate((el) => (el.scrollTop = 0));
  await page.waitForTimeout(150);
  await page.getByRole("region", { name: "Choose a ticket" }).getByRole("button").first().click();
  await expect(conversation(page)).toContainText("Latest updates from the support team");
  await page.waitForTimeout(300);
  const after = await scrollOf(page);
  expect(after.log).toBe(0); // still where they left it
  expect(after.page).toBe(0);
});

test("a reply taller than the window starts at its top, not its end", async ({
  page,
  controls,
}) => {
  await page.setViewportSize({ width: 1280, height: 440 });
  await reachTicketChoice(page, controls);
  await page.getByRole("region", { name: "Choose a ticket" }).getByRole("button").first().click();
  await expect(conversation(page)).toContainText("Latest updates from the support team");
  const where = await page.evaluate(() => {
    const log = document.querySelector(".log") as HTMLElement;
    const last = log.querySelector("ol > li:last-child") as HTMLElement;
    return {
      tall: last.getBoundingClientRect().height > log.clientHeight,
      topGap: last.getBoundingClientRect().top - log.getBoundingClientRect().top,
    };
  });
  expect(where.tall).toBe(true);
  expect(where.topGap).toBeGreaterThanOrEqual(-1); // its first line is on screen
  expect(where.topGap).toBeLessThan(40);
});

test("the page itself never scrolls, only the conversation does", async ({ page, controls }) => {
  await reachTicketChoice(page, controls);
  const shell = await page.evaluate(() => {
    const app = document.querySelector(".app") as HTMLElement;
    window.scrollTo(0, 5000);
    return {
      position: getComputedStyle(app).position,
      tall: document.documentElement.scrollHeight > innerHeight,
      moved: window.scrollY,
    };
  });
  expect(shell).toEqual({ position: "fixed", tall: false, moved: 0 });
});

test("opening an earlier chat shows its latest messages", async ({ page, controls }) => {
  await reachTicketChoice(page, controls);
  await page.getByRole("button", { name: "New chat" }).click();
  await say(page, "Request for VPN setup for remote work");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await page.getByRole("complementary", { name: "Chat history" }).getByRole("link").nth(1).click();
  await expect(conversation(page)).toContainText("choose the one you want to see", { ignoreCase: true });
  await expect
    .poll(async () => {
      const s = await scrollOf(page);
      return s.max - s.log;
    })
    .toBeLessThan(4);
});
