import type { Page } from "@playwright/test";
import { conversation, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

const QUESTION = "Request for VPN setup for remote work";
const NOTE = "Answered from cache";

test.beforeEach(async ({ page, controls: _controls }) => {
  await signIn(page);
});

const newChat = (page: Page) => page.getByRole("button", { name: "New chat" }).click();
const lastAnswer = (page: Page) => conversation(page).locator(".bubble").last();

/** Ask and wait for the whole reply: the typing indicator goes as soon as the first words stream
 * in, so also wait for the half-written bubble (marked aria-live="off") to be replaced. */
async function ask(page: Page, question: string) {
  await say(page, question);
  await expect(conversation(page).locator('li[aria-live="off"]')).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: /typing/i })).toHaveCount(0);
  await expect(conversation(page).locator("ol > li")).not.toHaveCount(0);
}

test("the first time a question is answered normally, with no note about the cache", async ({
  page,
}) => {
  await ask(page, QUESTION);
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await expect(page.getByText(NOTE)).toHaveCount(0);
});

test("asking the same question again is answered from the cache, with the same answer", async ({
  page,
}) => {
  const sources = page.getByRole("list", { name: "Sources" });
  await ask(page, QUESTION);
  await expect(sources).toBeVisible(); // the sources arrive with the final reply, never mid-way
  const first = await lastAnswer(page).innerText();
  await newChat(page);
  await ask(page, QUESTION);
  await expect(conversation(page).getByText(NOTE)).toBeVisible();
  await expect(sources).toBeVisible(); // sources came with it
  expect(await lastAnswer(page).innerText()).toBe(first);
});

test("capitals and punctuation do not make it a different question", async ({ page }) => {
  await ask(page, QUESTION);
  await newChat(page);
  await ask(page, "  request FOR vpn setup for remote work?!  ");
  await expect(conversation(page).getByText(NOTE)).toBeVisible();
});

test("a slightly reworded question is answered from the cache by its meaning", async ({
  page,
}) => {
  // Not an exact match, so this goes through the embedding comparison. (The fake embedder here
  // counts shared words, so a rewording has to keep most of them; the real model is looser.)
  await ask(page, "How do I set up the VPN for remote work?");
  await newChat(page);
  await ask(page, "how do I set up the VPN for remote work please");
  await expect(conversation(page).getByText(NOTE)).toBeVisible();
});

test("a different question is not answered with another question's answer", async ({ page }) => {
  await ask(page, QUESTION);
  await newChat(page);
  await ask(page, "Unable to reset password via self-service portal");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await expect(page.getByText(NOTE)).toHaveCount(0);
});

test("a question the knowledge base cannot answer is never cached", async ({ page }) => {
  for (let turn = 0; turn < 2; turn += 1) {
    await ask(page, "Does BuzzTech reimburse gym memberships?");
    await expect(page.getByRole("region", { name: "Raise a support ticket?" })).toBeVisible();
    await expect(page.getByText(NOTE)).toHaveCount(0);
    await page.getByRole("button", { name: "No thanks" }).click();
    await newChat(page);
  }
});

test("a question with an email address in it is never cached", async ({ page }) => {
  const question = "Request for VPN setup for remote work for alice@buzztech.example";
  await ask(page, question);
  await newChat(page);
  await ask(page, question);
  await expect(page.getByText(NOTE)).toHaveCount(0);
});

test("an answered question is still answered during a language model outage", async ({
  page,
  controls,
}) => {
  await ask(page, QUESTION);
  await newChat(page);
  await controls.fail("llm", 5);
  await ask(page, QUESTION);
  await expect(conversation(page).getByText(NOTE)).toBeVisible(); // no model was needed
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
});

test("the test controls clear the cache, so one test cannot leak into the next", async ({
  page,
  controls,
}) => {
  await ask(page, QUESTION);
  await controls.reset();
  await newChat(page);
  await ask(page, QUESTION);
  await expect(page.getByText(NOTE)).toHaveCount(0);
});

test("a cached answer is part of the conversation and survives a reload", async ({ page }) => {
  await ask(page, QUESTION);
  await newChat(page);
  await ask(page, QUESTION);
  await expect(conversation(page).getByText(NOTE)).toBeVisible();
  await page.reload();
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
});
