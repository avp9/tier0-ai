import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, MAX_MESSAGE_CHARS, api, waitText } from "./api";
import { Avatar } from "./components/Avatar";
import { MessageItem, type Item } from "./components/MessageItem";
import { PendingActionPanel, hasControls } from "./components/PendingActionPanel";
import { promptText, replyText } from "./format";
import { threadStore } from "./threadStore";
import type { ChatResponse, Me, Message, PendingAction } from "./types";

interface Props {
  me: Me;
  onSignOut: () => void;
  onSessionExpired: () => void;
}

let nextId = 1;
const newItem = (role: Item["role"], text: string, extra: Partial<Item> = {}): Item => ({
  id: nextId++,
  role,
  text,
  sources: [],
  ticket: null,
  ...extra,
});

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return `You're sending messages too quickly. Please wait ${waitText(error.retryAfter)}.`;
    }
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

export function ChatPage({ me, onSignOut, onSessionExpired }: Props) {
  const [items, setItems] = useState<Item[]>([]);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  /** The start of the answer, while it is still being written. */
  const [live, setLive] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(() => threadStore.get() !== null);
  const [error, setError] = useState<string | null>(null);

  const composerRef = useRef<HTMLTextAreaElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // Bring back the conversation after a reload; start fresh if it is gone.
  useEffect(() => {
    const stored = threadStore.get();
    if (!stored) return;
    let cancelled = false;
    api
      .history(stored)
      .then((history) => {
        if (cancelled) return;
        const restored = history.messages.map((m: Message) => newItem(m.role, m.content));
        if (history.pending_action) {
          restored.push(newItem("assistant", promptText(history.pending_action)));
        }
        setThreadId(history.thread_id);
        setItems(restored);
        setPending(history.pending_action);
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        if (failure instanceof ApiError && failure.status === 401) onSessionExpired();
        else threadStore.clear();
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });
    return () => {
      cancelled = true;
    };
  }, [onSessionExpired]);

  // Keep the newest message in view.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [items, pending, busy, live]);

  // After each reply, put the keyboard where the next action is.
  useEffect(() => {
    if (busy || restoring) return;
    if (hasControls(pending)) panelRef.current?.querySelector("button")?.focus();
    else composerRef.current?.focus();
  }, [pending, busy, restoring]);

  const send = useCallback(
    async (text: string, fromComposer: boolean) => {
      const message = text.trim();
      if (!message || busy) return;
      setBusy(true);
      setError(null);
      const sent = newItem("user", message);
      setItems((previous) => [...previous, sent]);
      try {
        let written = "";
        const reply: ChatResponse = await api.sendMessage(message, threadId, (piece) => {
          written += piece;
          setLive(written);
        });
        threadStore.set(reply.thread_id);
        setThreadId(reply.thread_id);
        setItems((previous) => [
          ...previous,
          newItem("assistant", replyText(reply), { sources: reply.sources, ticket: reply.ticket }),
        ]);
        setPending(reply.pending_action);
        if (fromComposer) setDraft("");
      } catch (failure) {
        setItems((previous) => previous.filter((item) => item.id !== sent.id));
        if (failure instanceof ApiError && failure.status === 401) onSessionExpired();
        else setError(describe(failure));
      } finally {
        setLive(null);
        setBusy(false);
      }
    },
    [busy, threadId, onSessionExpired],
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    void send(draft, true);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(draft, true);
    }
  }

  function startOver() {
    threadStore.clear();
    setThreadId(null);
    setItems([]);
    setPending(null);
    setError(null);
    setDraft("");
    composerRef.current?.focus();
  }

  const canSend = draft.trim().length > 0 && !busy;

  return (
    <div className="chat">
      <header>
        <h1>Tier0 Help Desk</h1>
        <div className="who-am-i">
          <Avatar me={me} />
          <button type="button" onClick={startOver} disabled={busy}>
            New conversation
          </button>
          <button type="button" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>

      <main>
        <div role="log" aria-live="polite" aria-label="Conversation" className="log">
          {items.length === 0 && !restoring && (
            <p className="empty">
              Ask an IT or HR question, such as &ldquo;How do I set up the VPN?&rdquo;, or ask me to
              raise a support ticket.
            </p>
          )}
          {restoring && <p role="status">Restoring your conversation…</p>}
          <ol>
            {items.map((item) => (
              <MessageItem key={item.id} item={item} />
            ))}
            {live !== null && (
              <MessageItem item={{ id: 0, role: "assistant", text: live, sources: [], ticket: null }} live />
            )}
          </ol>
          {busy && live === null && (
            <p role="status" className="typing">
              <span className="dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              Tier0 is typing…
            </p>
          )}
          <div ref={endRef} />
        </div>

        {error && (
          <p role="alert" className="error banner">
            {error}
          </p>
        )}

        <PendingActionPanel action={pending} busy={busy} onChoose={(m) => void send(m, false)} panelRef={panelRef} />

        <form onSubmit={submit} className="composer">
          <label htmlFor="message" className="visually-hidden">
            Message
          </label>
          <textarea
            id="message"
            ref={composerRef}
            rows={2}
            maxLength={MAX_MESSAGE_CHARS}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            aria-describedby="composer-hint"
            placeholder="Type your message"
            disabled={restoring}
          />
          <p id="composer-hint" className="hint">
            Press Enter to send, Shift+Enter for a new line.
          </p>
          <button type="submit" className="primary" disabled={!canSend}>
            Send
          </button>
        </form>
      </main>
    </div>
  );
}
