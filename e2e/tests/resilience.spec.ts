import { conversation, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

test.beforeEach(async ({ page, controls: _controls }) => {
  await signIn(page);
});

test("AT-14 an intent classifier outage is reported and the next message works", async ({
  page,
  controls,
}) => {
  await controls.fail("classifier");
  await say(page, "Unable to reset password via self-service portal");
  await expect(conversation(page)).toContainText(/trouble|unavailable|try again/i);
  await say(page, "Unable to reset password via self-service portal");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
});

test("AT-14 a language model outage never invents an answer", async ({ page, controls }) => {
  await controls.fail("llm", 5);
  await say(page, "Unable to reset password via self-service portal");
  await expect(page.getByRole("status").filter({ hasText: /thinking/i })).toHaveCount(0);
  await expect(conversation(page)).not.toContainText("Here is what our knowledge base says");
  await controls.reset();
  await say(page, "Unable to reset password via self-service portal");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
});

test("the conversation survives a page reload", async ({ page }) => {
  await say(page, "Unable to reset password via self-service portal");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await page.reload();
  await expect(conversation(page)).toContainText("Unable to reset password via self-service portal");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
});
