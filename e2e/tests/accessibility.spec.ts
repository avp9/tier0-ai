import AxeBuilder from "@axe-core/playwright";
import { reachPreview, say, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

test("the sign-in page has no detectable accessibility violations", async ({
  page,
  controls: _controls,
}) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(results.violations).toEqual([]);
});

test("the chat page, including a ticket preview, has no detectable violations", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page);
  await reachPreview(page);
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(results.violations).toEqual([]);
});

test("the ticket edit form has no detectable violations", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page);
  await reachPreview(page);
  await page.getByRole("region", { name: "Ticket to be created" }).getByRole("button", { name: "Edit" }).click();
  await expect(page.getByLabel("Title")).toBeFocused();
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(results.violations).toEqual([]);
});

test("the search quality report has no detectable violations", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page);
  await page.getByRole("button", { name: "Search quality check" }).click();
  const dialog = page.getByRole("dialog", { name: "Search quality check" });
  await dialog.getByRole("button", { name: "What is MRR?" }).waitFor({ timeout: 30_000 });
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(results.violations).toEqual([]);
});

test("the chat page with the text box greyed out has no detectable violations", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page);
  await say(page, "Does BuzzTech reimburse gym memberships?");
  await expect(page.getByRole("region", { name: "Raise a support ticket?" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: /message/i })).toBeDisabled();
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(results.violations).toEqual([]);
});
