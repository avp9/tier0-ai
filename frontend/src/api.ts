import type { ChatResponse, HistoryResponse, Me } from "./types";

export const MAX_MESSAGE_CHARS = 2000;

export class ApiError extends Error {
  readonly status: number;
  /** Seconds the server asked us to wait (HTTP 429), if it said. */
  readonly retryAfter: number | null;

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

const NETWORK_MESSAGE = "I can't reach the server right now. Check your connection and try again.";
const GENERIC_MESSAGE = "Something went wrong. Please try again.";

/** Only ever show a plain sentence from the server; structured validation errors are not one. */
function readableDetail(body: unknown): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const { detail } = body as { detail: unknown };
    if (typeof detail === "string" && detail.length > 0 && detail.length < 300) return detail;
    if (Array.isArray(detail)) return "Please check what you entered and try again.";
  }
  return GENERIC_MESSAGE;
}

async function failure(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* the server sent no readable body */
  }
  const wait = Number(response.headers.get("Retry-After"));
  return new ApiError(
    response.status,
    readableDetail(body),
    Number.isFinite(wait) && wait > 0 ? wait : null,
  );
}

async function send(path: string, init: RequestInit, accept?: string): Promise<Response> {
  const headers: Record<string, string> = {};
  if (init.body) headers["Content-Type"] = "application/json";
  if (accept) headers.Accept = accept;
  try {
    return await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: Object.keys(headers).length > 0 ? headers : undefined,
    });
  } catch {
    throw new ApiError(0, NETWORK_MESSAGE);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init);
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const INTERRUPTED_MESSAGE = "The connection was interrupted. Please try again.";

/** Read server-sent events: "token" pieces while the reply is written, then "done" with the turn. */
async function readReplyStream(
  body: ReadableStream<Uint8Array>,
  onToken: (text: string) => void,
): Promise<ChatResponse> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: ChatResponse | null = null;

  const handle = (block: string) => {
    let name = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!name || !data) return;
    let payload: unknown;
    try {
      payload = JSON.parse(data);
    } catch {
      return;
    }
    if (name === "token") {
      const text = (payload as { text?: unknown }).text;
      if (typeof text === "string") onToken(text);
    } else if (name === "done") {
      done = payload as ChatResponse;
    } else if (name === "error") {
      throw new ApiError(500, readableDetail(payload));
    }
  };

  try {
    for (;;) {
      const { value, done: finished } = await reader.read();
      buffer += decoder.decode(value, { stream: !finished });
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        handle(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        end = buffer.indexOf("\n\n");
      }
      if (finished) break;
    }
  } catch (failed) {
    if (failed instanceof ApiError) throw failed;
    throw new ApiError(0, INTERRUPTED_MESSAGE);
  }
  if (buffer.trim()) handle(buffer);
  if (!done) throw new ApiError(0, INTERRUPTED_MESSAGE);
  return done;
}

/**
 * Send a chat message. A server that streams answers with server-sent events; anything else
 * (an older server, a proxy that buffers) is read as the ordinary single JSON reply.
 */
async function sendChat(
  message: string,
  threadId: string | null,
  onToken: (text: string) => void,
): Promise<ChatResponse> {
  const response = await send(
    "/api/chat",
    { method: "POST", body: JSON.stringify(threadId ? { message, thread_id: threadId } : { message }) },
    "text/event-stream, application/json",
  );
  if (!response.ok) throw await failure(response);
  const type = response.headers.get("Content-Type") ?? "";
  if (!type.includes("text/event-stream") || !response.body) {
    return (await response.json()) as ChatResponse;
  }
  return readReplyStream(response.body, onToken);
}

export const api = {
  login: (email: string, password: string) =>
    request<undefined>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<undefined>("/api/auth/logout", { method: "POST" }),
  me: () => request<Me>("/api/auth/me"),
  /** ``onToken`` receives the reply text as it is written, when the server streams it. */
  sendMessage: (
    message: string,
    threadId: string | null,
    onToken: (text: string) => void = () => {},
  ) => sendChat(message, threadId, onToken),
  history: (threadId: string) => request<HistoryResponse>(`/api/chat/${encodeURIComponent(threadId)}`),
};

/** How long to tell the employee to wait, in words. */
export function waitText(seconds: number | null): string {
  if (seconds === null) return "a little while";
  if (seconds < 90) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} minutes`;
}
