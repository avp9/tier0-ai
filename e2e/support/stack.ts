import { expect, request, type APIRequestContext, type Page } from "@playwright/test";

export const ALICE = { email: "alice@buzztech.example", password: "alice-demo-pass-1" };
export const BOB = { email: "bob@buzztech.example", password: "bob-demo-pass-22" };

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:8000";
const TOKEN = process.env.E2E_TOKEN ?? "e2e-local-token";

export interface FakeTicket {
  key: string;
  requester_email: string;
  summary: string;
  description: string;
  category: string;
  status: string;
  submission_id: string | null;
}

export interface Seed {
  email: string;
  summary: string;
  status?: string;
  comments?: { author?: string; body: string; internal?: boolean }[];
}

export type Failure =
  | "tickets_unavailable"
  | "tickets_unknown_outcome"
  | "tickets_lost_response"
  | "llm"
  | "classifier";

/** Control endpoints that exist only in the isolated e2e stack. */
export class Controls {
  private constructor(private readonly api: APIRequestContext) {}

  static async open(): Promise<Controls> {
    const api = await request.newContext({
      baseURL: BASE_URL,
      extraHTTPHeaders: { "X-E2E-Token": TOKEN, Origin: BASE_URL },
    });
    return new Controls(api);
  }

  async reset(): Promise<void> {
    expect((await this.api.post("/api/_e2e/reset")).status()).toBe(200);
  }

  async seed(ticket: Seed): Promise<string> {
    const response = await this.api.post("/api/_e2e/tickets", { data: ticket });
    expect(response.status()).toBe(201);
    return ((await response.json()) as { key: string }).key;
  }

  async fail(target: Failure, times = 1): Promise<void> {
    const response = await this.api.post("/api/_e2e/fail", { data: { target, times } });
    expect(response.status()).toBe(200);
  }

  async tickets(): Promise<FakeTicket[]> {
    const response = await this.api.get("/api/_e2e/tickets");
    expect(response.status()).toBe(200);
    return (await response.json()) as FakeTicket[];
  }

  /** The thumbs up and down given so far. */
  async feedback(): Promise<{ trace_id: string; vote: "up" | "down"; comment: string | null }[]> {
    const response = await this.api.get("/api/_e2e/feedback");
    expect(response.status()).toBe(200);
    return (await response.json()) as { trace_id: string; vote: "up" | "down"; comment: string | null }[];
  }

  async dispose(): Promise<void> {
    await this.api.dispose();
  }
}

export async function signIn(page: Page, user = ALICE): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password").fill(user.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("log", { name: "Conversation" })).toBeVisible();
}

/** Type a message in the composer and send it. */
export async function say(page: Page, message: string): Promise<void> {
  await page.getByRole("textbox", { name: /message/i }).fill(message);
  await page.getByRole("button", { name: "Send" }).click();
}

export function conversation(page: Page) {
  return page.getByRole("log", { name: "Conversation" });
}

export const PROBLEM = "My laptop battery drains within an hour of unplugging it";

/** Ask for a ticket and answer the agent's questions until the preview is showing. */
export async function reachPreview(page: Page): Promise<void> {
  await say(page, "I want to raise a ticket");
  await expect(conversation(page)).toContainText("what is the problem");
  await say(page, PROBLEM);
  const preview = page.getByRole("region", { name: "Ticket to be created" });
  const categoryQuestion = conversation(page).getByText(/IT matter or an HR matter/);
  await expect(preview.or(categoryQuestion)).toBeVisible();
  if (await categoryQuestion.isVisible()) await say(page, "IT");
  await expect(preview).toBeVisible();
}
