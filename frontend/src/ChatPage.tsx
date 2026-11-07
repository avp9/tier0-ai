import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, MAX_MESSAGE_CHARS, api, waitText } from "./api";
import { Avatar } from "./components/Avatar";
import { CardiogramIcon, SignOutIcon } from "./components/icons";
import { HeaderButton } from "./components/HeaderButton";
import { AssistantPicture, MessageItem, type Item } from "./components/MessageItem";
import { RetrievalCheck } from "./components/RetrievalCheck";
import { PendingActionPanel, hasControls } from "./components/PendingActionPanel";
import { Sidebar } from "./components/Sidebar";
import { promptText, replyText } from "./format";
import { threadStore } from "./threadStore";
import type {
  ChatResponse,
  Conversation,
  HistoryResponse,
  Me,
  Message,
  PendingAction,
  TicketEdit,
} from "./types";

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

/** What the conversation records for an edit made in the form (the server keeps the same words). */
const EDITED_TEXT = "I edited the ticket.";

/** Below this width the history opens over the chat instead of beside it. */
const NARROW_PX = 900;
const isNarrow = () => typeof window !== "undefined" && window.innerWidth < NARROW_PX;

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
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(() => !isNarrow());
  const [checkOpen, setCheckOpen] = useState(false);

  const composerRef = useRef<HTMLTextAreaElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  /** The reader is at the bottom of the conversation, so a new reply should be brought into view. */
  const following = useRef(true);
  /** The last button the employee pressed, so focus can come back to it (a list of tickets stays). */
  const lastChoice = useRef<string | null>(null);
  /** The next change of messages is a different conversation being shown: start at its end. */
  const showEnd = useRef(false);

  /** Show a saved conversation and remember it for a reload. */
  const showHistory = useCallback((history: HistoryResponse) => {
    const restored = history.messages.map((m: Message) => newItem(m.role, m.content));
    if (history.pending_action) {
      restored.push(newItem("assistant", promptText(history.pending_action)));
    }
    threadStore.set(history.thread_id);
    showEnd.current = true;
    setThreadId(history.thread_id);
    setItems(restored);
    setPending(history.pending_action);
  }, []);

  /** The history list is a convenience: if it cannot load, the chat still works. */
  const refreshConversations = useCallback(() => {
    api
      .conversations()
      .then((list) => setConversations(Array.isArray(list) ? list : []))
      .catch((failure: unknown) => {
        if (failure instanceof ApiError && failure.status === 401) onSessionExpired();
      });
  }, [onSessionExpired]);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  // Bring back the conversation after a reload; start fresh if it is gone.
  useEffect(() => {
    const stored = threadStore.get();
    if (!stored) return;
    let cancelled = false;
    api
      .history(stored)
      .then((history) => {
        if (!cancelled) showHistory(history);
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
  }, [onSessionExpired, showHistory]);

  // Bring a new reply into view, but only when the reader was already at the bottom, and only by
  // scrolling the conversation itself (never the page). Nothing moves when the employee sends a
  // message or clicks a button, and a reply taller than the window starts at its top.
  useEffect(() => {
    const log = logRef.current;
    if (!log) return;
    if (showEnd.current) {
      showEnd.current = false;
      following.current = true;
      log.scrollTop = log.scrollHeight;
      return;
    }
    const newest = items[items.length - 1];
    if (!following.current || (live === null && newest?.role !== "assistant")) return;
    const message = log.querySelector<HTMLElement>("ol > li:last-child");
    if (!message) return;
    const box = message.getBoundingClientRect();
    const top = box.top - log.getBoundingClientRect().top + log.scrollTop;
    const bottomAligned = top + box.height - log.clientHeight + 8;
    const target = Math.min(bottomAligned, top - 8); // the end if it fits, else its start
    if (target > log.scrollTop) log.scrollTop = target;
  }, [items, live]);

  // After each reply, put the keyboard where the next action is.
  useEffect(() => {
    if (busy || restoring) return;
    if (hasControls(pending)) {
      const panel = panelRef.current;
      const again = [...(panel?.querySelectorAll<HTMLElement>("[data-choice]") ?? [])].find(
        (el) => el.getAttribute("data-choice") === lastChoice.current,
      );
      (again ?? panel?.querySelector("button"))?.focus();
    } else composerRef.current?.focus();
  }, [pending, busy, restoring]);

  /** Send one turn: a typed message, or (with ``edit``) the employee's changes to the ticket. */
  const send = useCallback(
    async (text: string, fromComposer: boolean, edit?: TicketEdit): Promise<boolean> => {
      const message = text.trim();
      if (!message || busy) return false;
      if (fromComposer && hasControls(pending)) return false; // an answer is needed first
      setBusy(true);
      setError(null);
      // Clear the box now, not when the reply ends: the employee may already be typing the next
      // message while this answer streams in, and it must not be wiped.
      if (fromComposer) setDraft("");
      const sent = newItem("user", message);
      setItems((previous) => [...previous, sent]);
      try {
        let written = "";
        const reply: ChatResponse =
          edit && threadId
            ? await api.editTicket(edit, threadId)
            : await api.sendMessage(message, threadId, (piece) => {
                written += piece;
                setLive(written);
              });
        threadStore.set(reply.thread_id);
        setThreadId(reply.thread_id);
        setItems((previous) => [
          ...previous,
          newItem("assistant", replyText(reply), {
            sources: reply.sources,
            ticket: reply.ticket,
            cached: reply.cached === true,
          }),
        ]);
        setPending(reply.pending_action);
        refreshConversations();
        return true;
      } catch (failure) {
        setItems((previous) => previous.filter((item) => item.id !== sent.id));
        // Give the unsent message back, ahead of anything typed since.
        if (fromComposer) setDraft((typed) => (typed === "" ? message : `${message}\n${typed}`));
        if (failure instanceof ApiError && failure.status === 401) onSessionExpired();
        else setError(describe(failure));
        if (edit && threadId && failure instanceof ApiError && failure.status === 409) {
          // The ticket moved on (it was sent, or the question changed): show what is true now.
          void api.history(threadId).then(showHistory, () => undefined);
        }
        return false;
      } finally {
        setLive(null);
        setBusy(false);
      }
    },
    [busy, threadId, pending, onSessionExpired, refreshConversations, showHistory],
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
    if (isNarrow()) setSidebarOpen(false);
    composerRef.current?.focus();
  }

  async function openConversation(id: string) {
    if (busy || id === threadId) return;
    setError(null);
    try {
      showHistory(await api.history(id));
      if (isNarrow()) setSidebarOpen(false);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) onSessionExpired();
      else {
        setError("That conversation could not be opened.");
        refreshConversations();
      }
    }
  }

  // While the agent waits for a choice on the buttons (offer, preview, pick a ticket), the box is
  // locked so the question cannot be skipped by typing something else.
  const waitingForChoice = hasControls(pending);
  const canSend = draft.trim().length > 0 && !busy && !waitingForChoice;

  return (
    <div className="app">
      {sidebarOpen && (
        <Sidebar
          conversations={conversations}
          activeId={threadId}
          busy={busy}
          onSelect={(id) => void openConversation(id)}
          onNewChat={startOver}
          onClose={() => setSidebarOpen(false)}
        />
      )}

      <div className="chat">
        <header>
          <div className="title">
            {!sidebarOpen && (
              <button
                type="button"
                className="icon-button"
                onClick={() => setSidebarOpen(true)}
                aria-label="Show chat history"
                aria-controls="chat-sidebar"
                aria-expanded="false"
              >
                <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
                  <path d="M4 5h16v14H4zM9 5v14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            )}
            <h1>Tier0 Help Desk</h1>
          </div>
          <div className="who-am-i">
            <HeaderButton label="Search quality check" onClick={() => setCheckOpen(true)}>
              <CardiogramIcon />
            </HeaderButton>
            <Avatar me={me} />
            <HeaderButton label="Sign out" onClick={onSignOut}>
              <SignOutIcon />
            </HeaderButton>
          </div>
        </header>

        <main>
          {/* It scrolls on its own, so the keyboard must be able to focus it to scroll it (WCAG 2.1.1). */}
          <div
            role="log"
            aria-live="polite"
            aria-label="Conversation"
            className="log"
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
            tabIndex={0}
            ref={logRef}
            onScroll={(event) => {
              const log = event.currentTarget;
              following.current = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
            }}
          >
            {items.length === 0 && !restoring && (
              <div className="welcome">
                <p className="welcome-title">How can I help today?</p>
                <p className="empty">
                  Ask an IT or HR question, such as &ldquo;How do I set up the VPN?&rdquo;, or ask me
                  to raise a support ticket.
                </p>
              </div>
            )}
            {restoring && <p role="status">Restoring your conversation…</p>}
            <ol>
              {items.map((item) => (
                <MessageItem key={item.id} item={item} gender={me.gender} />
              ))}
              {live !== null && (
                <MessageItem
                  item={{ id: 0, role: "assistant", text: live, sources: [], ticket: null }}
                  gender={me.gender}
                  live
                />
              )}
            </ol>
            {busy && live === null && (
              <p role="status" className="typing">
                <AssistantPicture size={28} />
                <span className="dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                is typing…
              </p>
            )}
          </div>

          {error && (
            <p role="alert" className="error banner">
              {error}
            </p>
          )}

          <PendingActionPanel
            action={pending}
            busy={busy}
            onChoose={(m) => {
              lastChoice.current = m;
              void send(m, false);
            }}
            onEdit={(edit) => send(EDITED_TEXT, false, edit)}
            panelRef={panelRef}
          />

          <form onSubmit={submit} className={waitingForChoice ? "composer locked" : "composer"}>
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
              placeholder={waitingForChoice ? "Choose an option above to continue" : "Ask anything…"}
              disabled={restoring || waitingForChoice}
            />
            <p id="composer-hint" className="hint">
              {waitingForChoice
                ? pending?.type === "select_ticket"
                  ? "Pick another ticket above, or press Done to ask something else."
                  : "Please answer the question above to continue."
                : "Press Enter to send, Shift+Enter for a new line."}
            </p>
            <button type="submit" className="send" disabled={!canSend} aria-label="Send">
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
                <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </form>
        </main>
      </div>

      {checkOpen && <RetrievalCheck onClose={() => setCheckOpen(false)} />}
    </div>
  );
}
