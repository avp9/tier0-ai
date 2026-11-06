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
  await expect(choose).toHaveCount(0);
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
