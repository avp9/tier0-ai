import { ALICE, BOB, conversation, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

const sidebar = (page: import("@playwright/test").Page) =>
  page.getByRole("complementary", { name: "Chat history" });

const VPN = "Request for VPN setup for remote work";
const PASSWORD = "Unable to reset password via self-service portal";

test("a chat appears in the history under Today and can be reopened after starting another", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page);
  await expect(sidebar(page)).toContainText("Your conversations will appear here.");

  await say(page, VPN);
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await expect(sidebar(page).getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(sidebar(page).getByRole("link", { name: VPN })).toHaveAttribute("aria-current", "true");

  await page.getByRole("button", { name: "New chat" }).click();
  await expect(conversation(page)).not.toContainText(VPN);
  await say(page, PASSWORD);
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  // Newest first, and the new chat is the one on screen.
  const links = sidebar(page).getByRole("link");
  await expect(links).toHaveCount(2);
  await expect(links.first()).toHaveText(PASSWORD);

  await sidebar(page).getByRole("link", { name: VPN }).click();
  await expect(conversation(page)).toContainText(VPN);
  await expect(conversation(page)).not.toContainText(PASSWORD);
  await expect(sidebar(page).getByRole("link", { name: VPN })).toHaveAttribute("aria-current", "true");
});

test("the history survives a page reload and signing in again", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page);
  await say(page, VPN);
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await page.getByRole("link", { name: VPN }).waitFor();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page);
  await expect(sidebar(page).getByRole("link", { name: VPN })).toBeVisible();
  await sidebar(page).getByRole("link", { name: VPN }).click();
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
});

test("one employee never sees another's chats", async ({ page, controls: _controls }) => {
  await signIn(page, ALICE);
  await say(page, VPN);
  await expect(sidebar(page).getByRole("link", { name: VPN })).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, BOB);
  await expect(sidebar(page)).toContainText("Your conversations will appear here.");
  await expect(sidebar(page).getByRole("link")).toHaveCount(0);
});

test("messages show Tier0's picture and the employee's own icon, not the words You and Tier0", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page, ALICE);
  await say(page, VPN);
  await expect(conversation(page)).toContainText("Here is what our knowledge base says");
  await expect(conversation(page).getByRole("img", { name: "Tier0" })).toBeVisible();
  const you = conversation(page).getByRole("img", { name: "You" });
  await expect(you).toBeVisible();
  await expect(you.locator("svg")).toHaveAttribute("data-figure", "female"); // Alice's account
  await expect(conversation(page).getByText("You", { exact: true })).toHaveCount(0);
});
