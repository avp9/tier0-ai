import assistantAvatar from "../assets/tier0-avatar.png";
import { safeHttpUrl } from "../safeUrl";
import type { Me, Role, Source, TicketRef, Vote } from "../types";
import { Headshot } from "./Avatar";

export interface Item {
  id: number;
  role: Role;
  text: string;
  sources: Source[];
  ticket: TicketRef | null;
  cached?: boolean;
  /** To rate this answer; only finished answers have one. */
  feedbackToken?: string | null;
  vote?: Vote | null;
}

function Thumb({ up }: { up: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
      <path
        d={
          up
            ? "M7 11v9H4v-9zM7 11l4-8a2 2 0 0 1 2 2v4h5.5a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 17.3 20H7"
            : "M7 13V4H4v9zM7 13l4 8a2 2 0 0 0 2-2v-4h5.5a2 2 0 0 0 2-2.3l-1.2-6A2 2 0 0 0 17.3 4H7"
        }
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Thumbs up and down under an answer. Pressing the chosen one again takes the rating back. */
function Rating({ vote, onVote }: { vote: Vote | null; onVote: (vote: Vote | null) => void }) {
  return (
    <div className="rating" role="group" aria-label="Rate this answer">
      {(["up", "down"] as const).map((kind) => (
        <button
          key={kind}
          type="button"
          className="rating-button"
          aria-pressed={vote === kind}
          aria-label={kind === "up" ? "Good answer" : "Bad answer"}
          title={kind === "up" ? "Good answer" : "Bad answer"}
          onClick={() => onVote(vote === kind ? null : kind)}
        >
          <Thumb up={kind === "up"} />
        </button>
      ))}
    </div>
  );
}

function CreatedTicket({ ticket }: { ticket: TicketRef }) {
  const href = safeHttpUrl(ticket.url);
  return (
    <div className="card created" role="group" aria-label="Created ticket">
      <p>
        Ticket <strong>{ticket.ticket_key}</strong> was created.
      </p>
      {href && (
        <p>
          <a href={href} target="_blank" rel="noopener noreferrer">
            Open {ticket.ticket_key}
            <span className="visually-hidden"> (opens in a new tab)</span>
          </a>
        </p>
      )}
    </div>
  );
}

/** Tier0's own picture. Its name is the alt text, so the words are not repeated on screen. */
export function AssistantPicture({ size = 36 }: { size?: number }) {
  return (
    <img className="speaker" src={assistantAvatar} alt="Tier0" width={size} height={size} />
  );
}

/** Who is speaking: Tier0's own picture, or the employee's headshot (by their gender). */
export function Speaker({ role, gender }: { role: Role; gender?: Me["gender"] }) {
  if (role === "assistant") return <AssistantPicture />;
  return (
    <span className="speaker" role="img" aria-label="You">
      <Headshot gender={gender} />
    </span>
  );
}

/**
 * ``live``: the text is still arriving, so screen readers wait for the final reply.
 * ``gender`` picks the employee's own icon.
 */
export function MessageItem({
  item,
  gender,
  live = false,
  onVote,
}: {
  item: Item;
  gender?: Me["gender"];
  live?: boolean;
  onVote?: (item: Item, vote: Vote | null) => void;
}) {
  const mine = item.role === "user";
  return (
    <li className={`message ${mine ? "mine" : "theirs"}`} aria-live={live ? "off" : undefined}>
      <Speaker role={item.role} gender={gender} />
      <div className="message-body">
        {/* Always a text node: replies can contain anything and must never be treated as HTML. */}
        <p className="bubble">{item.text}</p>
        {item.sources.length > 0 && (
          <ul className="sources" aria-label="Sources">
            {item.sources.map((source) => (
              <li key={source.document_id}>
                <span>{source.title}</span> <span className="category">{source.category}</span>
                <span className="match-details">
                  KB article {source.document_id}
                  {source.source ? ` · Source: ${source.source}` : ""}
                  {typeof source.score === "number" ? ` · Relevance: ${source.score.toFixed(2)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
          {item.cached && (
          <span className="cache-note">
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">
              <path d="M13 2 4 14h7l-1 8 9-12h-7z" fill="currentColor" />
            </svg>
            Answered from cache
          </span>
        )}
        {item.ticket && <CreatedTicket ticket={item.ticket} />}
        {!mine && !live && item.feedbackToken && onVote && (
          <Rating vote={item.vote ?? null} onVote={(vote) => onVote(item, vote)} />
        )}
      </div>
    </li>
  );
}
