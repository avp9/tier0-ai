import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, request, test, type Locator, type Page } from "@playwright/test";
import { ALICE, Controls, conversation } from "../support/stack";

// A 90-second tour of the features, recorded in one go with a label on top of each beat.
// Needs the four instances from demo/tour-stack.sh (real models on 8101-8103, fakes on 8104).
//   DEMO_TOUR=1 E2E_BASE_URL=http://localhost:8104 E2E_TOKEN=tour-token npx playwright test \
//     -c playwright.demo.config.ts demo/feature-tour.spec.ts
const OUT = fileURLToPath(new URL("../../docs/demo", import.meta.url));
const HYBRID = "http://localhost:8101";
const DENSE = "http://localhost:8102";
const JEV_DOWN = "http://localhost:8103";
const FAKE = "http://localhost:8104";
const HOTEL = "What is the hotel limit for business travel?";

// Hold times are multiplied by TOUR_PACE; the cutting step (retime.py) then fills the video to length.
const PACE = Number(process.env.TOUR_PACE ?? "1.25");
const wait = (page: Page, ms: number) => page.waitForTimeout(ms * PACE);
let stepNo = 0;

// How long each wait for a model answer took, in seconds from the start of the recording, so the
// finished video can drop the dull part of it (demo/retime.py). The label still states the truth.
const waits: [number, number][] = [];
let videoStart = Date.now();
const sinceStart = () => (Date.now() - videoStart) / 1000;
async function waitingFor<T>(work: () => Promise<T>): Promise<T> {
  const from = sinceStart();
  const result = await work();
  waits.push([Number(from.toFixed(2)), Number(sinceStart().toFixed(2))]);
  return result;
}

const clock = Date.now();
const note = (text: string) => process.stderr.write(`[${Math.round((Date.now() - clock) / 1000)}s] ${text}\n`);

async function label(page: Page, text: string, tone: "info" | "good" | "bad" = "info"): Promise<void> {
  note(text);
  await page.evaluate(
    ([t, color]) => {
      let el = document.getElementById("tour-label");
      if (!el) {
        el = document.createElement("div");
        el.id = "tour-label";
        Object.assign(el.style, {
          position: "fixed", top: "0", left: "0", right: "0", height: "52px", zIndex: "2147483647",
          color: "#fff", font: "600 19px system-ui, sans-serif", display: "flex",
          alignItems: "center", justifyContent: "center", padding: "0 16px", textAlign: "center",
          boxShadow: "0 2px 8px rgba(0,0,0,.35)", transition: "background .2s", pointerEvents: "none",
        });
        document.body.appendChild(el);
      }
      // The page's content-security policy blocks an injected <style>, so move the app down with
      // element styles: it is pinned to the window and must start below the banner.
      document.body.style.overflow = "hidden";
      const app = document.querySelector<HTMLElement>(".app");
      if (app) {
        app.style.top = "52px";
        app.style.height = "calc(100dvh - 52px)";
      }
      el.style.background = color as string;
      el.textContent = t as string;
    },
    [text, tone === "good" ? "#065f46" : tone === "bad" ? "#9f1239" : "#1e1b4b"],
  );
}

/** The banner for a new beat: numbered 1, 2, 3 ... in the order they appear. */
async function step(page: Page, text: string, tone: "info" | "good" | "bad" = "info"): Promise<void> {
  stepNo += 1;
  await label(page, `${stepNo} · ${text}`, tone);
}

/** A second banner within the same beat: same number. */
async function again(page: Page, text: string, tone: "info" | "good" | "bad" = "info"): Promise<void> {
  await label(page, `${stepNo} · ${text}`, tone);
}

async function glow(target: Locator, on = true): Promise<void> {
  await target.evaluate(
    (el, show) => {
      const node = el as HTMLElement;
      node.style.outline = show ? "4px solid #f59e0b" : "";
      node.style.outlineOffset = show ? "4px" : "";
      node.style.borderRadius = show ? "10px" : node.style.borderRadius;
    },
    on,
    { timeout: 5_000 },
  );
}

async function signIn(page: Page, base: string, reload = true): Promise<void> {
  if (reload) {
    await page.context().clearCookies();
    await page.goto(base + "/");
  }
  await page.getByLabel("Email").fill(ALICE.email);
  await page.getByLabel("Password").fill(ALICE.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("log", { name: "Conversation" })).toBeVisible();
}

async function ask(page: Page, text: string): Promise<void> {
  await page.getByRole("textbox", { name: /message/i }).fill(text);
  await page.getByRole("button", { name: "Send" }).click();
}

/** Press a button the way a viewer can follow: it lights up first, then it is clicked. */
async function press(button: Locator): Promise<void> {
  await glow(button);
  await button.page().waitForTimeout(450);
  await button.click();
  await glow(button, false).catch(() => undefined); // the button may be gone by now
}

/** Warm the server's cached search-quality report, so the overlay opens at once on camera. */
async function warmQualityReport(base: string): Promise<void> {
  const api = await request.newContext({ baseURL: base, extraHTTPHeaders: { Origin: base } });
  await api.post("/api/auth/login", { data: { email: ALICE.email, password: ALICE.password } });
  await api.get("/api/diagnostics/retrieval", { timeout: 180_000 });
  await api.dispose();
}

test("feature tour", async ({ browser }) => {
  test.skip(!process.env.DEMO_TOUR, "needs the tour stack: set DEMO_TOUR=1");
  test.setTimeout(900_000);
  const controls = await Controls.open(); // the fake stack: seeded tickets and injected failures
  await controls.reset(); // also empties the chat history the instances share
  const warmStart = Date.now();
  await warmQualityReport(HYBRID);
  console.log(`quality report warmed in ${Math.round((Date.now() - warmStart) / 1000)}s`);

  mkdirSync(`${OUT}/tmp-tour`, { recursive: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    recordVideo: { dir: `${OUT}/tmp-tour`, size: { width: 1280, height: 800 } },
  });
  const page = await context.newPage();
  videoStart = Date.now();
  page.setDefaultTimeout(30_000); // an action that cannot happen fails instead of waiting for ever
  const sources = page.getByRole("list", { name: "Sources" });
  const offer = page.getByRole("region", { name: "Raise a support ticket?" });
  const started = Date.now();

  // 6 · before: dense search alone
  await page.goto(DENSE + "/");
  await label(page, "Tier0 AI · feature tour");
  await wait(page, 2300);
  await signIn(page, DENSE, false);
  await step(page, "BEFORE: dense (embedding) search only");
  await ask(page, HOTEL);
  await waitingFor(() => expect(sources.or(offer)).toBeVisible({ timeout: 60_000 }));
  await again(
    page,
    (await sources.isVisible())
      ? "Dense only: the wrong article is cited"
      : "Dense only: the right article is not found, so no answer",
    "bad",
  );
  await wait(page, 2800);

  // 6 · after + 1 JEV + 7 sources and score + 8/9 grounded answers
  await signIn(page, HYBRID);
  await step(page, "AFTER: dense + SPLADE + reranker find the right article", "good");
  const t0 = Date.now();
  await ask(page, HOTEL);
  await waitingFor(() => expect(sources).toBeVisible({ timeout: 60_000 }));
  const firstSeconds = ((Date.now() - t0) / 1000).toFixed(1);
  await wait(page, 1800);
  await step(page, "JEV classifies the intent of every message (OpenAI is the fallback)");
  await wait(page, 2200);
  await glow(sources);
  await step(page, "Source and relevance score returned with the answer", "good");
  await wait(page, 2600);
  await step(page, "Answers use only the curated IT and HR knowledge base", "good");
  await wait(page, 2200);
  await glow(sources, false);
  const thumbsUp = page.getByRole("group", { name: "Rate this answer" }).getByRole("button", { name: "Good answer" });
  await expect(thumbsUp).toBeVisible({ timeout: 30_000 });
  await press(thumbsUp);
  await step(page, "Thumbs up / down on every answer");
  await wait(page, 1500);

  // 18 · multi-turn
  await step(page, "Multi-turn: the follow-up is understood from the conversation");
  await ask(page, "And what about client meals?");
  await waitingFor(() => expect(page.getByRole("list", { name: "Sources" })).toHaveCount(2, { timeout: 60_000 }));
  await wait(page, 2800);

  // 5 · Redis cache
  await page.getByRole("button", { name: "New chat" }).click();
  const t1 = Date.now();
  await ask(page, HOTEL);
  await waitingFor(() => expect(page.getByText("Answered from cache")).toBeVisible({ timeout: 30_000 }));
  const cachedSeconds = ((Date.now() - t1) / 1000).toFixed(1);
  await glow(page.getByText("Answered from cache"));
  await step(page, `Redis cache: first answer ${firstSeconds} s → same question now ${cachedSeconds} s`, "good");
  await wait(page, 3200);

  console.log(`at cache beat: ${Math.round((Date.now() - started) / 1000)}s`);
  // search quality overlay
  await step(page, "Search quality check: 44 labelled questions, scored live");
  await press(page.getByRole("button", { name: "Search quality check" }));
  const dialog = page.getByRole("dialog", { name: "Search quality check" });
  await waitingFor(() => expect(dialog.getByRole("button", { name: "What is Hit@1?" })).toBeVisible({ timeout: 60_000 }));
  await wait(page, 3200);
  await page.keyboard.press("Escape");

  console.log(`after quality overlay: ${Math.round((Date.now() - started) / 1000)}s`);
  // 11 · out of the domain
  await page.getByRole("button", { name: "New chat" }).click();
  await step(page, "Outside the domain: politely declined");
  await ask(page, "Write me a poem about pizza");
  await waitingFor(() => expect(conversation(page)).toContainText(/IT and HR/, { timeout: 60_000 }));
  await wait(page, 2400);

  // 10 · knowledge gap, 12 · ticket offered
  await page.getByRole("button", { name: "New chat" }).click();
  await step(page, "Not in the knowledge base: nothing is invented, so a ticket is offered");
  await ask(page, "Where can I park my car at the office?");
  await waitingFor(() => expect(offer).toBeVisible({ timeout: 60_000 }));
  await wait(page, 3000);

  // 13 · mandatory fields, 15 · locked text box, 3 · edit, 14 · confirm, 16 · key
  await press(page.getByRole("button", { name: "Yes, raise a ticket" }));
  const preview = page.getByRole("region", { name: "Ticket to be created" });
  const category = conversation(page).getByText(/IT matter or an HR matter/);
  await waitingFor(() => expect(preview.or(category)).toBeVisible({ timeout: 60_000 }));
  if (await category.isVisible()) {
    await ask(page, "HR");
    await waitingFor(() => expect(preview).toBeVisible({ timeout: 60_000 }));
  }
  await step(page, "Requester, title, description and category are collected before anything is sent");
  await wait(page, 3000);
  const composer = page.getByRole("textbox", { name: /message/i });
  await glow(composer);
  await step(page, "The text box is locked while the ticket is reviewed, to keep the user focused");
  await wait(page, 3000);
  await glow(composer, false);
  await step(page, "The employee can edit the ticket before it is sent");
  await press(preview.getByRole("button", { name: "Edit" }));
  await preview.getByLabel("Title").fill("Where can I park at the office?");
  await wait(page, 1500);
  await press(preview.getByRole("button", { name: "Save changes" }));
  await step(page, "Nothing is created until the employee explicitly confirms");
  await wait(page, 2600);
  await press(page.getByRole("button", { name: "Create ticket" }));
  const created = page.getByRole("group", { name: "Created ticket" });
  await waitingFor(() => expect(created).toBeVisible({ timeout: 30_000 }));
  await glow(created);
  await step(page, "The ticket key and link are returned after creation", "good");
  await wait(page, 3000);

  // 2 · JEV down
  await signIn(page, JEV_DOWN);
  await step(page, "JEV fails (invalid key): the OpenAI classifier takes over", "bad");
  await ask(page, "I cannot connect to the company VPN from my home network");
  await waitingFor(() => expect(sources).toBeVisible({ timeout: 90_000 }));
  await again(page, "The employee still gets a correct, sourced answer", "good");
  await wait(page, 3200);

  // 4/17 tickets, 19 failures (fake stack with seeded tickets and injected failures)
  await controls.seed({
    email: ALICE.email,
    summary: "Printer on floor 2 is offline",
    status: "In Progress",
    comments: [{ body: "We have ordered a replacement toner; it should arrive tomorrow." }],
  });
  await controls.seed({ email: ALICE.email, summary: "Outlook keeps asking for my password" });
  await signIn(page, FAKE);
  await step(page, "Retrieve my tickets: all of mine, to choose from");
  await ask(page, "What is the status of my tickets?");
  const choose = page.getByRole("region", { name: "Choose a ticket" });
  await expect(choose).toBeVisible({ timeout: 30_000 });
  await wait(page, 2400);
  await press(choose.getByRole("button", { name: /Printer/ }));
  await expect(conversation(page)).toContainText("replacement toner", { timeout: 30_000 });
  await step(page, "Current status and the latest support update", "good");
  await wait(page, 3000);
  await page.getByRole("button", { name: "Done" }).click();

  await controls.fail("tickets_unavailable");
  await step(page, "Ticket system down: an honest message, never a false success", "bad");
  await ask(page, "What is the status of my tickets?");
  await expect(conversation(page)).toContainText(/unavailable|try again|trouble/i, { timeout: 30_000 });
  await wait(page, 2800);
  await controls.fail("llm");
  await step(page, "Language model down: no invented answer", "bad");
  await ask(page, "Unable to reset password via self-service portal");
  await expect(conversation(page)).toContainText(/trouble|try again/i, { timeout: 30_000 });
  await wait(page, 2800);
  await controls.dispose();

  writeFileSync(`${OUT}/tmp-tour/waits.json`, JSON.stringify(waits));
  console.log(`tour length about ${Math.round((Date.now() - started) / 1000)}s`);
  const video = page.video();
  await context.close();
  if (video) renameSync(await video.path(), `${OUT}/tmp-tour/raw.webm`);
});
