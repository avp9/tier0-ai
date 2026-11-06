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
}

export interface HistoryResponse {
  thread_id: string;
  messages: Message[];
  pending_action: PendingAction | null;
}

export interface Me {
  email: string;
  display_name: string;
}
