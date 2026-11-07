import type { Page } from "@playwright/test";
import { BOB, conversation, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

const QUESTION = "Request for VPN setup for remote work";

test.beforeEach(async ({ page, controls: _controls }) => {
  await signIn(page);
});

const rating = (page: Page) => conversation(page).getByRole("group", { name: "Rate this answer" });
const up = (page: Page) => rating(page).getByRole("button", { name: "Good answer" });
const down = (page: Page) => rating(page).getByRole("button", { name: "Bad answer" });

async function askAndWait(page: Page, question = QUESTION) {
  await say(page, question);
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await expect(rating(page)).toBeVisible(); // arrives with the finished answer
}

test("thumbs appear under an answer, and only under answers", async ({ page }) => {
  await askAndWait(page);
  await expect(conversation(page).getByRole("group", { name: "Rate this answer" })).toHaveCount(1);
  await expect(up(page)).toHaveAttribute("aria-pressed", "false");
  await expect(down(page)).toHaveAttribute("aria-pressed", "false");
});

test("a thumbs up is stored against the answer", async ({ page, controls }) => {
  await askAndWait(page);
  await up(page).click();
  await expect(up(page)).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => (await controls.feedback()).length).toBe(1);
  const [vote] = await controls.feedback();
  expect(vote).toMatchObject({ vote: "up", comment: null });
  expect(vote?.trace_id).toMatch(/^[0-9a-f-]{36}$/);
});

test("changing the rating changes the stored vote rather than adding another", async ({
  page,
  controls,
}) => {
  await askAndWait(page);
  await up(page).click();
  await expect.poll(async () => (await controls.feedback())[0]?.vote).toBe("up");
  await down(page).click();
  await expect(down(page)).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => (await controls.feedback())[0]?.vote).toBe("down");
  expect(await controls.feedback()).toHaveLength(1);
});

test("pressing the chosen thumb again takes the rating back", async ({ page, controls }) => {
  await askAndWait(page);
  await up(page).click();
  await expect.poll(async () => (await controls.feedback()).length).toBe(1);
  await up(page).click();
  await expect(up(page)).toHaveAttribute("aria-pressed", "false");
  await expect.poll(async () => (await controls.feedback()).length).toBe(0);
});

test("each answer is rated on its own", async ({ page, controls }) => {
  await askAndWait(page);
  await say(page, "Unable to reset password via self-service portal");
  await expect(conversation(page).getByRole("group", { name: "Rate this answer" })).toHaveCount(2);
  await conversation(page).getByRole("group", { name: "Rate this answer" }).first().getByRole("button", { name: "Good answer" }).click();
  await conversation(page).getByRole("group", { name: "Rate this answer" }).last().getByRole("button", { name: "Bad answer" }).click();
  await expect.poll(async () => (await controls.feedback()).length).toBe(2);
  const votes = (await controls.feedback()).map((v) => v.vote).sort();
  expect(votes).toEqual(["down", "up"]);
});

test("a cached answer can be rated like any other", async ({ page, controls }) => {
  await askAndWait(page);
  await page.getByRole("button", { name: "New chat" }).click();
  await askAndWait(page);
  await expect(conversation(page).getByText("Answered from cache")).toBeVisible();
  await down(page).click();
  await expect.poll(async () => (await controls.feedback()).length).toBe(1);
});

test("an answer that offers a ticket can be rated, a ticket preview cannot", async ({ page }) => {
  await say(page, "Does BuzzTech reimburse gym memberships?");
  await expect(page.getByRole("region", { name: "Raise a support ticket?" })).toBeVisible();
  await expect(rating(page)).toBeVisible();
  await page.getByRole("button", { name: "Yes" }).click();
  const preview = page.getByRole("region", { name: "Ticket to be created" });
  await expect(preview).toBeVisible();
  // The preview's own question carries no rating; the earlier answer keeps its own.
  await expect(conversation(page).getByRole("group", { name: "Rate this answer" })).toHaveCount(1);
});

test("nobody can use someone else's rating link", async ({ page, browser, controls }) => {
  const tokens: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/feedback")) {
      tokens.push((request.postDataJSON() as { token: string }).token);
    }
  });
  await askAndWait(page);
  await up(page).click();
  await expect.poll(() => tokens.length).toBe(1);

  const other = await browser.newContext({ baseURL: page.url().split("/").slice(0, 3).join("/") });
  const bob = await other.newPage();
  await signIn(bob, BOB);
  const response = await other.request.post("/api/feedback", { data: { token: tokens[0], vote: "down" } });
  expect(response.status()).toBe(404);
  await other.close();
  expect((await controls.feedback())[0]?.vote).toBe("up"); // untouched
});

test("a rating button does not move the conversation", async ({ page }) => {
  await askAndWait(page);
  const before = await page.locator(".log").evaluate((el) => el.scrollTop);
  await up(page).click();
  await expect(up(page)).toHaveAttribute("aria-pressed", "true");
  expect(await page.locator(".log").evaluate((el) => el.scrollTop)).toBe(before);
});
