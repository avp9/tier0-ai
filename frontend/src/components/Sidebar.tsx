import { groupConversations } from "../groupConversations";
import type { Conversation } from "../types";

interface Props {
  conversations: Conversation[];
  /** The chat on screen, if it has been saved yet. */
  activeId: string | null;
  /** A reply is on its way: switching chats now would lose it. */
  busy: boolean;
  onSelect: (threadId: string) => void;
  onNewChat: () => void;
  onClose: () => void;
}

function Icon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** The chat history: a New chat button and the employee's earlier chats by day. */
export function Sidebar({ conversations, activeId, busy, onSelect, onNewChat, onClose }: Props) {
  const groups = groupConversations(conversations);
  return (
    <aside className="sidebar" aria-label="Chat history" id="chat-sidebar">
      <div className="sidebar-head">
        <h2>Chat History</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Hide chat history">
          <Icon path="M4 5h16v14H4zM9 5v14" />
        </button>
      </div>

      <button type="button" className="new-chat" onClick={onNewChat} disabled={busy}>
        <Icon path="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
        New chat
      </button>

      <nav aria-label="Your conversations" className="history">
        {groups.length === 0 && (
          <p className="history-empty">Your conversations will appear here.</p>
        )}
        {groups.map((group) => (
          <section key={group.label}>
            <h3 className="group-label">{group.label}</h3>
            <ul>
              {group.items.map((chat) => (
                <li key={chat.thread_id}>
                  <a
                    href={`#${chat.thread_id}`}
                    title={chat.title}
                    aria-current={chat.thread_id === activeId ? "true" : undefined}
                    aria-disabled={busy || undefined}
                    onClick={(event) => {
                      event.preventDefault();
                      if (!busy) onSelect(chat.thread_id);
                    }}
                  >
                    {chat.title}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>
    </aside>
  );
}
