import { useCallback, useEffect, useState } from "react";
import { ChatPage } from "./ChatPage";
import { LoginPage } from "./LoginPage";
import { api } from "./api";
import { threadStore } from "./threadStore";
import type { Me } from "./types";

type State =
  | { status: "loading" }
  | { status: "signed-out"; notice?: string }
  | { status: "signed-in"; me: Me };

export default function App() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => !cancelled && setState({ status: "signed-in", me }))
      .catch(() => !cancelled && setState({ status: "signed-out" }));
    return () => {
      cancelled = true;
    };
  }, []);

  const sessionExpired = useCallback(() => {
    threadStore.clear();
    setState({ status: "signed-out", notice: "Your session expired. Please sign in again." });
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* the cookie is cleared on the server when it can be reached; either way we leave */
    }
    threadStore.clear();
    setState({ status: "signed-out", notice: "You have been signed out." });
  }, []);

  if (state.status === "loading") {
    return (
      <p role="status" className="loading">
        Loading…
      </p>
    );
  }
  if (state.status === "signed-out") {
    return (
      <LoginPage
        notice={state.notice}
        onSignedIn={(me) => setState({ status: "signed-in", me })}
      />
    );
  }
  return <ChatPage me={state.me} onSignOut={() => void signOut()} onSessionExpired={sessionExpired} />;
}
