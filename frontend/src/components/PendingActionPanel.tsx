import type { Ref } from "react";
import type { PendingAction, TicketPreview } from "../types";

interface Props {
  action: PendingAction | null;
  busy: boolean;
  /** Send a plain message on the employee's behalf ("yes", "no" or a ticket key). */
  onChoose: (message: string) => void;
  panelRef: Ref<HTMLElement>;
}

/** Whether this question is answered with buttons (otherwise the employee just types). */
export function hasControls(action: PendingAction | null): boolean {
  if (!action) return false;
  if (action.type === "confirm_ticket") return action.preview !== null;
  return action.type === "offer_ticket" || action.type === "select_ticket";
}

function PreviewCard({
  preview,
  busy,
  onChoose,
  panelRef,
}: {
  preview: TicketPreview;
  busy: boolean;
  onChoose: (message: string) => void;
  panelRef: Ref<HTMLElement>;
}) {
  return (
    <section ref={panelRef} className="card" aria-labelledby="preview-title">
      <h2 id="preview-title">Ticket to be created</h2>
      <dl>
        <dt>Requester</dt>
        <dd>{preview.requester_email}</dd>
        <dt>Title</dt>
        <dd>{preview.title ?? "Not set"}</dd>
        <dt>Category</dt>
        <dd>{preview.category ?? "Not set"}</dd>
        <dt>Priority</dt>
        <dd>{preview.priority ?? "Not set"}</dd>
        <dt>Description</dt>
        <dd className="multiline">{preview.description ?? "Not set"}</dd>
      </dl>
      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={() => onChoose("yes")}>
          Create ticket
        </button>
        <button type="button" disabled={busy} onClick={() => onChoose("no")}>
          Cancel
        </button>
      </div>
    </section>
  );
}

export function PendingActionPanel({ action, busy, onChoose, panelRef }: Props) {
  if (!action) return null;

  if (action.type === "confirm_ticket" && action.preview) {
    return <PreviewCard preview={action.preview} busy={busy} onChoose={onChoose} panelRef={panelRef} />;
  }

  if (action.type === "offer_ticket") {
    return (
      <section ref={panelRef} className="card" aria-label="Raise a support ticket?">
        <div className="actions">
          <button type="button" className="primary" disabled={busy} onClick={() => onChoose("yes")}>
            Yes, raise a ticket
          </button>
          <button type="button" disabled={busy} onClick={() => onChoose("no")}>
            No thanks
          </button>
        </div>
      </section>
    );
  }

  if (action.type === "select_ticket") {
    return (
      <section ref={panelRef} className="card" aria-labelledby="pick-title">
        <h2 id="pick-title">Choose a ticket</h2>
        <ul className="ticket-options">
          {action.options.map((option) => (
            <li key={option.ticket_key}>
              <button
                type="button"
                disabled={busy}
                onClick={() => onChoose(option.ticket_key)}
              >
                <span className="ticket-key">{option.ticket_key}</span>{" "}
                <span>{option.title}</span>{" "}
                <span className="status">{option.status}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  return null;
}
