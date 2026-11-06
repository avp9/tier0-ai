import { ApiError, api, waitText } from "./api";
import { json, noContent, stubApi } from "./test/helpers";

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected the call to fail");
}

describe("api", () => {
  it("sends cookies, JSON bodies and the right content type only when there is a body", async () => {
    const { fetchMock } = stubApi({
      "POST /api/auth/login": () => noContent(),
      "GET /api/auth/me": () => json({ email: "a@b.co", display_name: "A" }),
    });
    await api.login("a@b.co", "pw");
    await api.me();
    const [loginInit, meInit] = fetchMock.mock.calls.map((call) => call[1]);
    expect(loginInit).toMatchObject({ credentials: "same-origin", method: "POST" });
    expect(loginInit?.headers).toEqual({ "Content-Type": "application/json" });
    expect(meInit?.headers).toBeUndefined();
  });

  it("returns nothing for 204 and the parsed body otherwise", async () => {
    stubApi({
      "POST /api/auth/logout": () => noContent(),
      "GET /api/auth/me": () => json({ email: "a@b.co", display_name: "A" }),
    });
    await expect(api.logout()).resolves.toBeUndefined();
    await expect(api.me()).resolves.toEqual({ email: "a@b.co", display_name: "A" });
  });

  it("only includes thread_id when there is one", async () => {
    const { calls } = stubApi({ "POST /api/chat": () => json({}) });
    await api.sendMessage("hi", null);
    await api.sendMessage("hi", "t-1");
    expect(calls.map((c) => c.body)).toEqual([{ message: "hi" }, { message: "hi", thread_id: "t-1" }]);
  });

  it("encodes the thread id in the history URL", async () => {
    const { calls } = stubApi({ "GET /api/chat/a%2Fb": () => json({}) });
    await api.history("a/b");
    expect(calls[0]?.path).toBe("/api/chat/a%2Fb");
  });

  it("shows the server's plain sentence for an error", async () => {
    stubApi({ "GET /api/auth/me": () => json({ detail: "Not authenticated" }, 401) });
    const error = await failure(api.me());
    expect(error).toMatchObject({ status: 401, message: "Not authenticated", retryAfter: null });
  });

  it("never shows structured validation errors or long or odd bodies", async () => {
    for (const body of [
      { detail: [{ loc: ["body", "message"], msg: "field required", type: "missing" }] },
      { detail: "x".repeat(400) },
      { detail: { nested: true } },
      { other: 1 },
      "plain",
    ]) {
      stubApi({ "GET /api/auth/me": () => json(body, 422) });
      const error = await failure(api.me());
      expect(error.message).not.toContain("field required");
      expect(error.message.length).toBeLessThan(80);
    }
  });

  it("uses a generic message when the error body is not JSON", async () => {
    stubApi({ "GET /api/auth/me": () => new Response("<html>oops</html>", { status: 502 }) });
    expect((await failure(api.me())).message).toBe("Something went wrong. Please try again.");
  });

  it("reads Retry-After from a 429", async () => {
    stubApi({
      "POST /api/chat": () => json({ detail: "Slow down" }, 429, { "Retry-After": "42" }),
    });
    expect(await failure(api.sendMessage("hi", null))).toMatchObject({ status: 429, retryAfter: 42 });
  });

  it("ignores a nonsense Retry-After", async () => {
    stubApi({ "POST /api/chat": () => json({ detail: "x" }, 429, { "Retry-After": "soon" }) });
    expect((await failure(api.sendMessage("hi", null))).retryAfter).toBeNull();
  });

  it("turns a network failure into a friendly status-0 error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await failure(api.me());
    expect(error.status).toBe(0);
    expect(error.message).toContain("can't reach the server");
    expect(error.message).not.toContain("Failed to fetch");
  });
});

describe("waitText", () => {
  it.each([
    [null, "a little while"],
    [1, "1 second"],
    [30, "30 seconds"],
    [89, "89 seconds"],
    [900, "15 minutes"],
    [91, "2 minutes"],
  ])("%j -> %s", (seconds, expected) => {
    expect(waitText(seconds)).toBe(expected);
  });
});
