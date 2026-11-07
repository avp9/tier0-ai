import { ALICE, conversation, reachPreview, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

test.beforeEach(async ({ page, controls: _controls }) => {
  await signIn(page);
});

test("AT-04 accepting escalation never asks for an email: the session supplies it", async ({
  page,
  controls,
}) => {
  await say(page, "My monitor keeps flickering, how do I fix it?");
  await page.getByRole("button", { name: "Yes" }).click();
  const preview = page.getByRole("region", { name: "Ticket to be created" });
  await expect(preview).toContainText(ALICE.email);
  await expect(preview).toContainText("monitor keeps flickering");
  await expect(conversation(page)).not.toContainText(/your email|email address/i);
  expect(await controls.tickets()).toEqual([]); // still nothing until the employee confirms
});

test("AT-05 creating a ticket without details collects the mandatory fields first", async ({
  page,
  controls,
}) => {
  await say(page, "I want to raise a ticket");
  await expect(conversation(page)).toContainText("what is the problem");
  await expect(page.getByRole("region", { name: "Ticket to be created" })).toHaveCount(0);
  expect(await controls.tickets()).toEqual([]);
});

test("AT-06 confirming the preview creates exactly one ticket and shows its link", async ({
  page,
  controls,
}) => {
  await reachPreview(page);
  const preview = page.getByRole("region", { name: "Ticket to be created" });
  await expect(preview).toContainText(ALICE.email);
  expect(await controls.tickets()).toEqual([]); // nothing exists before confirmation
  await page.getByRole("button", { name: "Create ticket" }).click();
  const card = page.getByRole("group", { name: "Created ticket" });
  await expect(card).toContainText("SUP-");
  const tickets = await controls.tickets();
  expect(tickets).toHaveLength(1);
  expect(tickets[0]?.requester_email).toBe(ALICE.email);
  expect(tickets[0]?.summary).toContain("laptop battery");
});

test("AT-07 declining the preview creates no ticket", async ({ page, controls }) => {
  await reachPreview(page);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(conversation(page)).toContainText("haven't created a ticket");
  await expect(page.getByRole("group", { name: "Created ticket" })).toHaveCount(0);
  expect(await controls.tickets()).toEqual([]);
});

test("AT-08 an ambiguous confirmation is clarified and creates nothing yet", async ({
  page,
  controls,
}) => {
  await reachPreview(page);
  await say(page, "hmm maybe");
  await expect(conversation(page)).toContainText("I need a clear answer");
  expect(await controls.tickets()).toEqual([]);
  await expect(page.getByRole("region", { name: "Ticket to be created" })).toBeVisible();
  await say(page, "yes please");
  await expect(page.getByRole("group", { name: "Created ticket" })).toBeVisible();
  expect(await controls.tickets()).toHaveLength(1);
});

test("AT-18 a lost response is reconciled and never creates a duplicate", async ({
  page,
  controls,
}) => {
  await reachPreview(page);
  await controls.fail("tickets_lost_response");
  await page.getByRole("button", { name: "Create ticket" }).click();
  // The ticket was created but the answer never arrived; the agent finds it again by its label.
  await expect(page.getByRole("group", { name: "Created ticket" })).toBeVisible();
  expect(await controls.tickets()).toHaveLength(1);
  await say(page, "yes"); // a repeated confirmation with nothing pending
  await expect(page.getByRole("status").filter({ hasText: /typing/i })).toHaveCount(0);
  expect(await controls.tickets()).toHaveLength(1);
});

test("AT-18 an unknown outcome keeps the preview so the employee can safely retry", async ({
  page,
  controls,
}) => {
  await reachPreview(page);
  await controls.fail("tickets_unknown_outcome");
  await page.getByRole("button", { name: "Create ticket" }).click();
  await expect(page.getByRole("group", { name: "Created ticket" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Ticket to be created" })).toBeVisible();
  expect(await controls.tickets()).toEqual([]);
  await page.getByRole("button", { name: "Create ticket" }).click();
  await expect(page.getByRole("group", { name: "Created ticket" })).toBeVisible();
  expect(await controls.tickets()).toHaveLength(1);
});

test("AT-13 a ticket system outage is reported honestly and no ticket appears", async ({
  page,
  controls,
}) => {
  await reachPreview(page);
  await controls.fail("tickets_unavailable");
  await page.getByRole("button", { name: "Create ticket" }).click();
  await expect(page.getByRole("group", { name: "Created ticket" })).toHaveCount(0);
  await expect(conversation(page)).toContainText(/couldn't|unable|try again/i);
  expect(await controls.tickets()).toEqual([]);
});
