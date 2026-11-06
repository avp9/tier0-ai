import { safeHttpUrl } from "../safeUrl";
import type { Role, Source, TicketRef } from "../types";

export interface Item {
  id: number;
  role: Role;
  text: string;
  sources: Source[];
  ticket: TicketRef | null;
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

export function MessageItem({ item }: { item: Item }) {
  const mine = item.role === "user";
  return (
    <li className={`message ${mine ? "mine" : "theirs"}`}>
      <span className="who">{mine ? "You" : "Tier0"}</span>
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
      {item.ticket && <CreatedTicket ticket={item.ticket} />}
    </li>
  );
}
