import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { ALICE, signIn } from "../support/stack";

// Screenshot of the search quality overlay. Run it against the REAL stack (scripts/run-local.sh):
// on the fake-provider stack the index is a tiny fixture and the numbers would mislead.
//   DEMO_REAL_STACK=1 DEMO_EMAIL=... DEMO_PASSWORD=... npm run demo
const OUT = fileURLToPath(new URL("../../docs/demo", import.meta.url));

test("search quality overlay (real stack)", async ({ page }) => {
  test.skip(!process.env.DEMO_REAL_STACK, "needs the real stack: set DEMO_REAL_STACK=1");
  const user = {
    email: process.env.DEMO_EMAIL ?? ALICE.email,
    password: process.env.DEMO_PASSWORD ?? ALICE.password,
  };
  await page.setViewportSize({ width: 1100, height: 900 });
  await signIn(page, user);
  await page.getByRole("button", { name: "Search quality check" }).click();
  const dialog = page.getByRole("dialog", { name: "Search quality check" });
  await expect(dialog.getByRole("button", { name: "What is Hit@1?" })).toBeVisible({ timeout: 120_000 });
  await expect(dialog).toContainText("Suggested cut-off");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/09-search-quality.png` });
  // The report scrolls inside the dialog: show its lower half, the answer cut-off advice, too.
  await dialog.evaluate((root) => {
    for (const el of [root, ...Array.from(root.querySelectorAll<HTMLElement>("*"))]) {
      if (el.scrollHeight > el.clientHeight + 4) el.scrollTop = el.scrollHeight;
    }
  });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/10-search-quality-cutoff.png` });
});
