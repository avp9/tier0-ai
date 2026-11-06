import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginPage } from "./LoginPage";
import { ME, json, noContent, stubApi } from "./test/helpers";

async function fillAndSubmit(email = "alice@buzztech.example", password = "correct-pass") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  return user;
}

describe("LoginPage", () => {
  it("has labelled fields with the right autofill hints", () => {
    render(<LoginPage onSignedIn={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Tier0 Help Desk" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("Email")).toBeRequired();
    expect(screen.getByLabelText("Password")).toBeRequired();
  });

  it("signs in, then loads who the employee is", async () => {
    const onSignedIn = vi.fn();
    const { calls } = stubApi({
      "POST /api/auth/login": () => noContent(),
      "GET /api/auth/me": () => json(ME),
    });
    render(<LoginPage onSignedIn={onSignedIn} />);
    await fillAndSubmit("  alice@buzztech.example  ");
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(ME));
    expect(calls[0]?.body).toEqual({ email: "alice@buzztech.example", password: "correct-pass" });
  });

  it("explains a wrong password, clears it, and keeps the email for another try", async () => {
    stubApi({ "POST /api/auth/login": () => json({ detail: "Invalid email or password" }, 401) });
    render(<LoginPage onSignedIn={vi.fn()} />);
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Email")).toHaveValue("alice@buzztech.example");
    expect(screen.getByLabelText("Email")).toBeInvalid();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("tells the employee how long to wait after too many attempts", async () => {
    stubApi({
      "POST /api/auth/login": () =>
        json({ detail: "Too many attempts" }, 429, { "Retry-After": "900" }),
    });
    render(<LoginPage onSignedIn={vi.fn()} />);
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent("try again in 15 minutes");
  });

  it("reports a network failure without technical detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<LoginPage onSignedIn={vi.fn()} />);
    await fillAndSubmit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("can't reach the server");
    expect(alert).not.toHaveTextContent("Failed to fetch");
  });

  it("blocks a second submit while signing in", async () => {
    let release: (r: Response) => void = () => undefined;
    const { calls } = stubApi({
      "POST /api/auth/login": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    render(<LoginPage onSignedIn={vi.fn()} />);
    const user = await fillAndSubmit();
    const button = screen.getByRole("button", { name: "Signing in…" });
    expect(button).toBeDisabled();
    await user.click(button);
    expect(calls).toHaveLength(1);
    release(json({ detail: "Invalid email or password" }, 401));
    await screen.findByRole("alert");
  });

  it("shows why the employee was sent back to sign in", () => {
    render(<LoginPage onSignedIn={vi.fn()} notice="Your session expired. Please sign in again." />);
    expect(screen.getByRole("status")).toHaveTextContent("Your session expired");
  });

  it("never puts the password anywhere it could be read back or stored", async () => {
    stubApi({ "POST /api/auth/login": () => json({ detail: "x" }, 401) });
    render(<LoginPage onSignedIn={vi.fn()} />);
    await fillAndSubmit("alice@buzztech.example", "super-secret-password");
    await screen.findByRole("alert");
    expect(document.body.textContent).not.toContain("super-secret-password");
    expect(JSON.stringify({ ...sessionStorage })).not.toContain("super-secret-password");
    expect(JSON.stringify({ ...localStorage })).not.toContain("super-secret-password");
  });
});
