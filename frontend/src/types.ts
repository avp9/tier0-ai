export type Role = "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
}

export interface Source {
  document_id: string;
  title: string;
  category: string;
  source?: string;
  score?: number | null;
}

export interface TicketPreview {
  requester_email: string;
  title: string | null;
  description: string | null;
  category: "IT" | "HR" | null;
  priority: "Low" | "Medium" | "High" | "Critical" | null;
}

/** The employee's changes to a ticket preview. Every field is sent, so it replaces the draft. */
export interface TicketEdit {
  title: string;
  description: string;
  category: "IT" | "HR";
  priority: "Low" | "Medium" | "High" | "Critical" | null;
}

export const TITLE_MAX = 120;
export const DESCRIPTION_MAX = 4000;

export interface TicketOption {
  ticket_key: string;
  title: string;
  status: string;
}

export type PendingType =
  | "offer_ticket"
  | "collect_ticket_details"
  | "confirm_ticket"
  | "select_ticket";

export interface PendingAction {
  type: PendingType;
  prompt: string;
  preview: TicketPreview | null;
  /** The preview can still be edited: nothing has been sent to the ticket system yet. */
  editable?: boolean;
  options: TicketOption[];
}

export interface TicketRef {
  ticket_key: string;
  url: string;
  status: string | null;
}

export interface ChatResponse {
  thread_id: string;
  reply: string;
  intent: string | null;
  sources: Source[];
  pending_action: PendingAction | null;
  ticket: TicketRef | null;
  /** The answer was reused from an earlier, similar question rather than worked out afresh. */
  cached?: boolean;
  /** Hand this back to rate the answer. Only finished answers carry one. */
  feedback_token?: string | null;
}

export type Vote = "up" | "down";

export interface HistoryResponse {
  thread_id: string;
  messages: Message[];
  pending_action: PendingAction | null;
}

/** One of the signed-in employee's earlier chats, as listed in the history sidebar. */
export interface Conversation {
  thread_id: string;
  title: string;
  updated_at: string;
}

export interface Me {
  email: string;
  display_name: string;
  gender?: "male" | "female" | null;
}

/** The search-quality report: the output of the retrieval evaluation, as data. */
export interface RankMetric {
  k: number;
  hit: number;
  recall: number;
}

export interface RetrievalReport {
  generated_at: string;
  duration_s: number;
  embedding_model: string;
  cases: number;
  gap_questions: number;
  ranks: RankMetric[];
  mrr: number;
  by_kind: { kind: string; hit: RankMetric[] }[];
  misses_at: number;
  misses: { id: string; query: string }[];
  separation: {
    answerable_min: number;
    answerable_median: number;
    gap_max: number;
    gap_median: number;
    separable: boolean;
  };
  threshold: {
    current: number;
    suggested: number;
    answerable_kept: number;
    gaps_rejected: number;
    separable: boolean;
  };
}
