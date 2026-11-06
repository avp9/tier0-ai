import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { ME, chatReply, json, noContent, stubApi } from "./test/helpers";

describe("App", () => {
  it("shows the sign-in page to someone who is not signed in", async () => {
    stubApi({ "GET /api/auth/me": () => json({ detail: "Not authenticated" }, 401) });
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeVisible();
    expect(screen.queryByLabelText("Message")).not.toBeInTheDocument();
  });

  it("goes straight to the chat for someone who already has a session", async () => {
    stubApi({ "GET /api/auth/me": () => json(ME) });
    render(<App />);
    expect(await screen.findByLabelText("Message")).toBeVisible();
    expect(screen.getByRole("banner")).toHaveTextContent("Alice Demo");
  });

  it("falls back to sign-in if the server cannot be reached at start", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeVisible();
  });

  it("takes an employee from sign-in into the chat", async () => {
    let signedIn = false;
    stubApi({
      "GET /api/auth/me": () => (signedIn ? json(ME) : json({ detail: "no" }, 401)),
      "POST /api/auth/login": () => {
        signedIn = true;
        return noContent();
      },
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText("Email"), "alice@buzztech.example");
    await user.type(screen.getByLabelText("Password"), "alice-demo-pass-1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByLabelText("Message")).toBeVisible();
  });

  it("signs out, forgets the conversation and says so", async () => {
    sessionStorage.setItem("tier0.thread", "t-1");
    const { calls } = stubApi({
      "GET /api/auth/me": () => json(ME),
      "GET /api/chat/t-1": () => json({ detail: "Conversation not found" }, 404),
      "POST /api/auth/logout": () => noContent(),
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("status")).toHaveTextContent("You have been signed out.");
    expect(calls.some((c) => c.path === "/api/auth/logout")).toBe(true);
    expect(sessionStorage.getItem("tier0.thread")).toBeNull();
    expect(screen.queryByLabelText("Message")).not.toBeInTheDocument();
  });

  it("still signs out locally if the server cannot be reached", async () => {
    let reachable = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (!reachable) throw new TypeError("offline");
        return String(input) === "/api/auth/me" ? json(ME) : noContent();
      }),
    );
    const user = userEvent.setup();
    render(<App />);
    const button = await screen.findByRole("button", { name: "Sign out" });
    reachable = false;
    await user.click(button);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeVisible();
  });

  it("sends the employee back to sign-in, with a reason, when the session expires mid-chat", async () => {
    sessionStorage.setItem("tier0.thread", "t-1");
    stubApi({
      "GET /api/auth/me": () => json(ME),
      "GET /api/chat/t-1": () => json(chatReply().pending_action),
      "POST /api/chat": () => json({ detail: "Not authenticated" }, 401),
    });
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Message")).toBeEnabled());
    await user.type(screen.getByLabelText("Message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Your session expired");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeVisible();
    expect(sessionStorage.getItem("tier0.thread")).toBeNull();
  });
});
