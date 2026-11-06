import { ALICE, signIn } from "../support/stack";
import { expect, test } from "./fixtures";

test("signing in with a wrong password shows an error and no conversation", async ({
  page,
  controls: _controls,
}) => {
  await page.goto("/");
  await page.getByLabel("Email").fill(ALICE.email);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid email or password.")).toBeVisible();
  await expect(page.getByRole("log", { name: "Conversation" })).toHaveCount(0);
});

test("a signed-in employee can sign out and is asked to sign in again", async ({
  page,
  controls: _controls,
}) => {
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("the chat API refuses an unauthenticated caller", async ({ request, baseURL }) => {
  const response = await request.post("/api/chat", {
    data: { message: "hello" },
    headers: { Origin: baseURL ?? "" },
  });
  expect(response.status()).toBe(401);
});
