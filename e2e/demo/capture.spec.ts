import { mkdirSync, renameSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { ALICE, conversation, Controls, say, signIn } from "../support/stack";

const OUT = fileURLToPath(new URL("../../docs/demo", import.meta.url));
const shot = (page: import("@playwright/test").Page, name: string) =>
  page.screenshot({ path: `${OUT}/${name}.png` });

test.describe.configure({ mode: "serial" });
test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

test("flow 1: a knowledge question", async ({ page }) => {
  const controls = await Controls.open();
  await controls.reset();
  await page.goto("/");
  await shot(page, "01-sign-in");
  await signIn(page, ALICE);
  await say(page, "Unable to reset password via self-service portal");
  await expect(page.getByRole("list", { name: "Sources" })).toBeVisible();
  await shot(page, "02-knowledge-answer");
  await controls.dispose();
});

test("flow 2: raise a ticket (recorded)", async ({ browser }) => {
  const controls = await Controls.open();
  await controls.reset();
  const context = await browser.newContext({
    viewport: { width: 1100, height: 780 },
    recordVideo: { dir: `${OUT}/tmp-video`, size: { width: 1100, height: 780 } },
  });
  const page = await context.newPage();
  await signIn(page, ALICE);
  await say(page, "My monitor keeps flickering, how do I fix it?");
  await expect(page.getByRole("region", { name: "Raise a support ticket?" })).toBeVisible();
  await shot(page, "03-offer-ticket");
  await page.getByRole("button", { name: "Yes, raise a ticket" }).click();
  const preview = page.getByRole("region", { name: "Ticket to be created" });
  await expect(preview).toBeVisible();
  await page.waitForTimeout(600);
  await shot(page, "04-ticket-preview");
  await page.getByRole("button", { name: "Create ticket" }).click();
  await expect(page.getByRole("group", { name: "Created ticket" })).toBeVisible();
  await page.waitForTimeout(600);
  await shot(page, "05-ticket-created");
  const video = page.video();
  await context.close();
  if (video) renameSync(await video.path(), `${OUT}/ticket-flow.webm`);
  await controls.dispose();
});

test("flow 3: ticket status", async ({ page }) => {
  const controls = await Controls.open();
  await controls.reset();
  await controls.seed({
    email: ALICE.email,
    summary: "Printer on floor 2 is offline",
    status: "In Progress",
    comments: [
      { body: "We have ordered a replacement toner; it should arrive tomorrow." },
      { body: "INTERNAL: vendor escalation reference 4471", internal: true },
    ],
  });
  await controls.seed({ email: ALICE.email, summary: "Outlook keeps asking for my password" });
  await signIn(page, ALICE);
  await say(page, "What is the status of my tickets?");
  const choose = page.getByRole("region", { name: "Choose a ticket" });
  await expect(choose).toBeVisible();
  await shot(page, "06-status-choose");
  await choose.getByRole("button", { name: /Printer/ }).click();
  await expect(conversation(page)).toContainText("replacement toner");
  await shot(page, "07-status-details");
  await controls.dispose();
});

test("phone width", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 780 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  await signIn(page, ALICE);
  await say(page, "Unable to reset password via self-service portal");
  await expect(page.getByRole("list", { name: "Sources" })).toBeVisible();
  await shot(page, "08-phone");
  await context.close();
});
