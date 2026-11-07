import { ALICE, BOB, conversation, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

test.beforeEach(async ({ page, controls: _controls }) => {
  await signIn(page);
});

test("AT-09 status uses the signed-in employee's email and never asks for one", async ({
  page,
  controls,
}) => {
  await controls.seed({ email: BOB.email, summary: "Bob's secret VPN problem" });
  await say(page, "What is the status of my ticket?");
  await expect(conversation(page)).not.toContainText(/your email|email address/i);
  await expect(conversation(page)).not.toContainText("Bob's secret VPN problem");
});

test("AT-10 a single ticket shows its status and the latest public support update", async ({
  page,
  controls,
}) => {
  await controls.seed({
    email: ALICE.email,
    summary: "Printer offline",
    status: "In Progress",
    comments: [
      { body: "We are replacing the toner today." },
      { body: "INTERNAL-ONLY escalate to vendor", internal: true },
    ],
  });
  await say(page, "Any update on my ticket?");
  await expect(conversation(page)).toContainText("Printer offline");
  await expect(conversation(page)).toContainText("In Progress");
  await expect(conversation(page)).toContainText("We are replacing the toner today.");
  await expect(conversation(page)).not.toContainText("INTERNAL-ONLY");
});

test("AT-11 several tickets are listed and the employee chooses one", async ({
  page,
  controls,
}) => {
  const first = await controls.seed({ email: ALICE.email, summary: "Printer offline" });
  await controls.seed({ email: ALICE.email, summary: "Outlook keeps crashing" });
  await say(page, "What is the status of my tickets?");
  const choose = page.getByRole("region", { name: "Choose a ticket" });
  await expect(choose).toBeVisible();
  await choose.getByRole("button", { name: new RegExp(first) }).click();
  await expect(conversation(page)).toContainText("Printer offline");
  // The list stays, with every ticket still there, so another can be looked at.
  await expect(choose).toBeVisible();
  await expect(choose.getByRole("button")).toHaveCount(4); // two tickets, Done and Close
});

test("AT-11 several tickets can be looked at one after another, then the list is closed", async ({
  page,
  controls,
}) => {
  const first = await controls.seed({ email: ALICE.email, summary: "Printer offline" });
  const second = await controls.seed({ email: ALICE.email, summary: "Outlook keeps crashing" });
  await say(page, "What is the status of my tickets?");
  const choose = page.getByRole("region", { name: "Choose a ticket" });
  const box = page.getByRole("textbox", { name: /message/i });
  await expect(choose).toBeVisible();

  await choose.getByRole("button", { name: new RegExp(first) }).click();
  await expect(conversation(page)).toContainText("Printer offline");
  await choose.getByRole("button", { name: new RegExp(second) }).click();
  await expect(conversation(page)).toContainText("Outlook keeps crashing");
  await choose.getByRole("button", { name: new RegExp(first) }).click(); // and back again
  await expect(choose).toBeVisible();
  await expect(box).toBeDisabled(); // the list is still waiting for an answer

  await choose.getByRole("button", { name: "Done" }).click();
  await expect(choose).toHaveCount(0);
  await expect(box).toBeEnabled();
});

test("AT-11 the ticket list can be closed with its close button", async ({ page, controls }) => {
  const first = await controls.seed({ email: ALICE.email, summary: "Printer offline" });
  await controls.seed({ email: ALICE.email, summary: "Outlook keeps crashing" });
  await say(page, "What is the status of my tickets?");
  const choose = page.getByRole("region", { name: "Choose a ticket" });
  await choose.getByRole("button", { name: new RegExp(first) }).click();
  await expect(conversation(page)).toContainText("Printer offline");
  await choose.getByRole("button", { name: "Close ticket list" }).click();
  await expect(choose).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: /message/i })).toBeEnabled();
});

test("AT-11 the ticket list is still there after a page reload", async ({ page, controls }) => {
  const first = await controls.seed({ email: ALICE.email, summary: "Printer offline" });
  await controls.seed({ email: ALICE.email, summary: "Outlook keeps crashing" });
  await say(page, "What is the status of my tickets?");
  const choose = page.getByRole("region", { name: "Choose a ticket" });
  await choose.getByRole("button", { name: new RegExp(first) }).click();
  await expect(conversation(page)).toContainText("Printer offline");
  await page.reload();
  await expect(page.getByRole("region", { name: "Choose a ticket" })).toBeVisible();
});

test("AT-11 pressing a ticket keeps keyboard focus on it", async ({ page, controls }) => {
  await controls.seed({ email: ALICE.email, summary: "Printer offline" });
  const second = await controls.seed({ email: ALICE.email, summary: "Outlook keeps crashing" });
  await say(page, "What is the status of my tickets?");
  const choose = page.getByRole("region", { name: "Choose a ticket" });
  const button = choose.getByRole("button", { name: new RegExp(second) });
  await button.click();
  await expect(conversation(page)).toContainText("Outlook keeps crashing");
  await expect(button).toBeFocused();
});

test("AT-12 no tickets gives a clear no-results answer", async ({ page }) => {
  await say(page, "What is the status of my ticket?");
  await expect(conversation(page)).toContainText(/don't (see|have|find)|no tickets|couldn't find/i);
});

test("AT-13 a ticket system outage during lookup is not reported as no tickets", async ({
  page,
  controls,
}) => {
  await controls.seed({ email: ALICE.email, summary: "Printer offline" });
  await controls.fail("tickets_unavailable");
  await say(page, "What is the status of my ticket?");
  await expect(conversation(page)).toContainText(/couldn't|unable|try again|unavailable/i);
  await expect(conversation(page)).not.toContainText(/no tickets/i);
});
