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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: init.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError(0, NETWORK_MESSAGE);
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      /* the server sent no readable body */
    }
    const wait = Number(response.headers.get("Retry-After"));
    throw new ApiError(
      response.status,
      readableDetail(body),
      Number.isFinite(wait) && wait > 0 ? wait : null,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<undefined>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<undefined>("/api/auth/logout", { method: "POST" }),
  me: () => request<Me>("/api/auth/me"),
  sendMessage: (message: string, threadId: string | null) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify(threadId ? { message, thread_id: threadId } : { message }),
    }),
  history: (threadId: string) => request<HistoryResponse>(`/api/chat/${encodeURIComponent(threadId)}`),
};

/** How long to tell the employee to wait, in words. */
export function waitText(seconds: number | null): string {
  if (seconds === null) return "a little while";
  if (seconds < 90) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} minutes`;
}
