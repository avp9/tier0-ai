import type { Conversation } from "./types";

export interface ConversationGroup {
  label: "Today" | "Yesterday" | "Previous 7 days" | "Older";
  items: Conversation[];
}

const DAY_MS = 24 * 60 * 60 * 1000;
const startOfDay = (date: Date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/**
 * Sort chats into the sidebar's sections by calendar day in the viewer's time zone. The order
 * within a section is the order given (the server sends the newest first); empty sections are
 * left out.
 */
export function groupConversations(
  conversations: Conversation[],
  now: Date = new Date(),
): ConversationGroup[] {
  const today = startOfDay(now);
  const buckets: Record<ConversationGroup["label"], Conversation[]> = {
    Today: [],
    Yesterday: [],
    "Previous 7 days": [],
    Older: [],
  };
  for (const chat of conversations) {
    const when = new Date(chat.updated_at);
    const age = Number.isNaN(when.getTime())
      ? Infinity
      : Math.round((today - startOfDay(when)) / DAY_MS); // rounding absorbs 23 and 25 hour days
    if (age <= 0) buckets.Today.push(chat);
    else if (age === 1) buckets.Yesterday.push(chat);
    else if (age <= 7) buckets["Previous 7 days"].push(chat);
    else buckets.Older.push(chat);
  }
  return (Object.keys(buckets) as ConversationGroup["label"][])
    .filter((label) => buckets[label].length > 0)
    .map((label) => ({ label, items: buckets[label] }));
}
