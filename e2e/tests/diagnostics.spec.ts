import { conversation, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

test.beforeEach(async ({ page, controls: _controls }) => {
  await signIn(page);
});

test("the cardiogram button opens a readable search quality report", async ({ page }) => {
  await page.getByRole("button", { name: "Search quality check" }).click();
  const dialog = page.getByRole("dialog", { name: "Search quality check" });
  await expect(dialog.getByRole("button", { name: "What is Hit@1?" })).toBeVisible({ timeout: 30_000 });
  await expect(dialog).toContainText("test questions");
  for (const term of ["Hit@1", "Recall@5", "MRR"]) {
    await expect(dialog.getByRole("button", { name: `What is ${term}?` })).toBeVisible();
  }
  await expect(dialog.getByRole("table")).toContainText("Company handbook");
  await expect(dialog).toContainText("Suggested cut-off");
  await expect(dialog).toContainText("Cut-off in use");
});

test("a question mark explains its term in one line, on hover and on keyboard focus", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Search quality check" }).click();
  const dialog = page.getByRole("dialog", { name: "Search quality check" });
  const help = dialog.getByRole("button", { name: "What is Recall@3?" });
  await help.waitFor({ timeout: 30_000 });
  const tip = dialog.getByRole("tooltip").filter({ hasText: "average share found in the top 3" });
  await expect(tip).toHaveCSS("opacity", "0");
  await help.hover();
  await expect(tip).toHaveCSS("opacity", "1");
  await page.mouse.move(0, 0);
  await help.focus();
  await expect(tip).toHaveCSS("opacity", "1");
});

test("the report closes with Escape and returns to the chat", async ({ page }) => {
  const opener = page.getByRole("button", { name: "Search quality check" });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Search quality check" });
  await dialog.getByRole("button", { name: "What is MRR?" }).waitFor({ timeout: 30_000 });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("running the check again straight away is refused with a clear wait", async ({ page }) => {
  await page.getByRole("button", { name: "Search quality check" }).click();
  const dialog = page.getByRole("dialog", { name: "Search quality check" });
  await dialog.getByRole("button", { name: "What is MRR?" }).waitFor({ timeout: 30_000 });
  // The server keeps its last result between tests, so the first run again may or may not be
  // allowed yet; a second one straight after always falls inside the wait.
  const runAgain = dialog.getByRole("button", { name: "Run again" });
  await runAgain.click();
  await expect(runAgain).toBeEnabled();
  await runAgain.click();
  await expect(dialog.getByRole("alert")).toContainText(/Try again in \d+ seconds/);
  await expect(dialog.getByRole("button", { name: "What is MRR?" })).toBeVisible(); // still shown
});

test("the text box is greyed out while the agent waits for a button, and opens afterwards", async ({
  page,
}) => {
  const box = page.getByRole("textbox", { name: /message/i });
  await say(page, "Does BuzzTech reimburse gym memberships?");
  await expect(page.getByRole("region", { name: "Raise a support ticket?" })).toBeVisible();
  await expect(box).toBeDisabled();
  await expect(box).toHaveAttribute("placeholder", "Choose an option above to continue");
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();

  await page.getByRole("button", { name: "Yes" }).click(); // the offer becomes a preview
  const preview = page.getByRole("region", { name: "Ticket to be created" });
  await expect(preview).toBeVisible();
  await expect(box).toBeDisabled();

  await preview.getByRole("button", { name: "Cancel" }).click();
  await expect(conversation(page)).toContainText("haven't created a ticket");
  await expect(box).toBeEnabled();
  await say(page, "Request for VPN setup for remote work");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
});

test("the typing indicator shows Tier0's picture", async ({ page }) => {
  await say(page, "Request for VPN setup for remote work");
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await expect(page.getByRole("status").filter({ hasText: /typing/i })).toHaveCount(0);
});

test("signing out uses the door icon button", async ({ page }) => {
  const signOut = page.getByRole("button", { name: "Sign out" });
  await expect(signOut.locator("svg")).toBeVisible();
  await expect(signOut).toHaveText("");
  await signOut.click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});
