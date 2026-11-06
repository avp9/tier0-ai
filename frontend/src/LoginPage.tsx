import { useState, type FormEvent } from "react";
import { ApiError, api, waitText } from "./api";
import type { Me } from "./types";

interface Props {
  onSignedIn: (me: Me) => void;
  notice?: string;
}

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Invalid email or password.";
    if (error.status === 429) {
      return `Too many attempts. Please try again in ${waitText(error.retryAfter)}.`;
    }
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

export function LoginPage({ onSignedIn, notice }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(email.trim(), password);
      onSignedIn(await api.me());
    } catch (failure) {
      setPassword("");
      setError(messageFor(failure));
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Tier0 Help Desk</h1>
      <p className="lede">Sign in to ask IT and HR questions or raise a support ticket.</p>
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      <form onSubmit={submit} aria-describedby={error ? "login-error" : undefined}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        </div>
        {error && (
          <p id="login-error" role="alert" className="error">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
