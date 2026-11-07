import type { ChatResponse, PendingAction } from "../types";

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

export const noContent = () => new Response(null, { status: 204 });

/** Replace fetch with a router keyed by "METHOD /path"; anything unexpected fails the test. */
export function stubApi(overrides: Record<string, Handler>) {
  // Every chat page loads the chat history list; most tests do not care about it.
  const routes: Record<string, Handler> = { "GET /api/conversations": () => json([]), ...overrides };
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });
    const handler = routes[`${method} ${path}`];
    if (!handler) throw new Error(`Unexpected request: ${method} ${path}`);
    return handler(path, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock, sentMessages: () => calls.filter((c) => c.path === "/api/chat") };
}

export function chatReply(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    thread_id: "11111111-1111-4111-8111-111111111111",
    reply: "Here is the answer.",
    intent: "knowledge",
    sources: [],
    pending_action: null,
    ticket: null,
    ...overrides,
  };
}

export function pending(overrides: Partial<PendingAction> = {}): PendingAction {
  return {
    type: "offer_ticket",
    prompt: "Would you like me to raise a support ticket?",
    preview: null,
    options: [],
    ...overrides,
  };
}

export const ME = {
  email: "alice@buzztech.example",
  display_name: "Alice Demo",
  gender: "female" as const,
};

const encoder = new TextEncoder();
export const sse = (name: string, data: unknown) =>
  encoder.encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);

/** A reply streamed as server-sent events that the test feeds one piece at a time. */
export function streamedReply() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start: (c) => (controller = c),
  });
  return {
    response: () =>
      new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    token: (text: string) => controller.enqueue(sse("token", { text })),
    done: (reply: ChatResponse) => {
      controller.enqueue(sse("done", reply));
      controller.close();
    },
    send: (chunk: Uint8Array) => controller.enqueue(chunk),
    close: () => controller.close(),
  };
}
