import { useEffect, useRef, useState, type FormEvent, type Ref } from "react";
import {
  DESCRIPTION_MAX,
  TITLE_MAX,
  type PendingAction,
  type TicketEdit,
  type TicketPreview,
} from "../types";

interface Props {
  action: PendingAction | null;
  busy: boolean;
  /** Send a plain message on the employee's behalf ("yes", "no" or a ticket key). */
  onChoose: (message: string) => void;
  /** Replace the preview's contents with the employee's edits; true once they were accepted. */
  onEdit: (edit: TicketEdit) => Promise<boolean>;
  panelRef: Ref<HTMLElement>;
}

/** Whether this question is answered with buttons (otherwise the employee just types). */
export function hasControls(action: PendingAction | null): boolean {
  if (!action) return false;
  if (action.type === "confirm_ticket") return action.preview !== null;
  return action.type === "offer_ticket" || action.type === "select_ticket";
}

const PRIORITIES = ["Low", "Medium", "High", "Critical"] as const;

interface Draft {
  title: string;
  description: string;
  category: "IT" | "HR";
  priority: TicketEdit["priority"];
}

const draftOf = (preview: TicketPreview): Draft => ({
  title: preview.title ?? "",
  description: preview.description ?? "",
  category: preview.category ?? "IT",
  priority: preview.priority,
});

function EditForm({
  preview,
  busy,
  onSave,
  onDiscard,
}: {
  preview: TicketPreview;
  busy: boolean;
  onSave: (edit: TicketEdit) => void;
  onDiscard: () => void;
}) {
  const original = draftOf(preview);
  const [draft, setDraft] = useState<Draft>(original);
  const titleRef = useRef<HTMLInputElement>(null);
  useEffect(() => titleRef.current?.focus(), []);

  const complete = draft.title.trim() !== "" && draft.description.trim() !== "";
  const changed =
    draft.title !== original.title ||
    draft.description !== original.description ||
    draft.category !== original.category ||
    draft.priority !== original.priority;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!complete || !changed || busy) return;
    onSave({ ...draft, title: draft.title.trim(), description: draft.description.trim() });
  }

  return (
    <form className="edit-form" onSubmit={submit}>
      <p className="edit-requester">
        Requester: <strong>{preview.requester_email}</strong>
      </p>
      <div>
        <label htmlFor="edit-title">Title</label>
        <input
          id="edit-title"
          ref={titleRef}
          type="text"
          maxLength={TITLE_MAX}
          value={draft.title}
          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          aria-describedby={complete ? undefined : "edit-required"}
        />
      </div>
      <div className="edit-pair">
        <div>
          <label htmlFor="edit-category">Category</label>
          <select
            id="edit-category"
            value={draft.category}
            onChange={(e) => setDraft({ ...draft, category: e.target.value as Draft["category"] })}
          >
            <option value="IT">IT</option>
            <option value="HR">HR</option>
          </select>
        </div>
        <div>
          <label htmlFor="edit-priority">Priority</label>
          <select
            id="edit-priority"
            value={draft.priority ?? ""}
            onChange={(e) =>
              setDraft({ ...draft, priority: (e.target.value || null) as Draft["priority"] })
            }
          >
            <option value="">Not set</option>
            {PRIORITIES.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label htmlFor="edit-description">Description</label>
        <textarea
          id="edit-description"
          rows={7}
          maxLength={DESCRIPTION_MAX}
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          aria-describedby={complete ? undefined : "edit-required"}
        />
      </div>
      {!complete && (
        <p id="edit-required" className="hint">
          The title and the description cannot be empty.
        </p>
      )}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy || !complete || !changed}>
          Save changes
        </button>
        <button type="button" disabled={busy} onClick={onDiscard}>
          Discard changes
        </button>
      </div>
    </form>
  );
}

function PreviewCard({
  preview,
  editable,
  busy,
  onChoose,
  onEdit,
  panelRef,
}: {
  preview: TicketPreview;
  editable: boolean;
  busy: boolean;
  onChoose: (message: string) => void;
  onEdit: (edit: TicketEdit) => Promise<boolean>;
  panelRef: Ref<HTMLElement>;
}) {
  const [editing, setEditing] = useState(false);

  async function save(edit: TicketEdit) {
    if (await onEdit(edit)) setEditing(false); // on a failure the form stays, so nothing typed is lost
  }

  return (
    <section ref={panelRef} className="card" aria-labelledby="preview-title">
      <h2 id="preview-title">Ticket to be created</h2>
      {editing ? (
        <EditForm preview={preview} busy={busy} onSave={(e) => void save(e)} onDiscard={() => setEditing(false)} />
      ) : (
        <>
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
            {editable && (
              <button type="button" disabled={busy} onClick={() => setEditing(true)}>
                Edit
              </button>
            )}
            <button type="button" disabled={busy} onClick={() => onChoose("no")}>
              Cancel
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export function PendingActionPanel({ action, busy, onChoose, onEdit, panelRef }: Props) {
  if (!action) return null;

  if (action.type === "confirm_ticket" && action.preview) {
    return (
      <PreviewCard
        preview={action.preview}
        editable={action.editable === true}
        busy={busy}
        onChoose={onChoose}
        onEdit={onEdit}
        panelRef={panelRef}
      />
    );
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
