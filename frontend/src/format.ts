import type { ChatResponse, PendingAction, Source } from "./types";

export const REVIEW_PROMPT = "Please review the ticket below, then create it or cancel.";

export const choosePrompt = (count: number) =>
  `You have ${count} tickets. Choose the one you want to see.`;

/** The server appends "Sources: a; b" to an answer; the UI shows the sources as a list instead. */
export function withoutSourcesLine(text: string, sources: Source[]): string {
  if (sources.length === 0) return text;
  const suffix = `\n\nSources: ${sources.map((s) => s.title).join("; ")}`;
  return text.endsWith(suffix) ? text.slice(0, -suffix.length) : text;
}

/** Text for the transcript: the card or the buttons already show the details, so don't repeat them. */
export function promptText(action: PendingAction): string {
  if (action.type === "confirm_ticket" && action.preview) return REVIEW_PROMPT;
  if (action.type === "select_ticket" && action.options.length > 0) {
    return choosePrompt(action.options.length);
  }
  return action.prompt;
}

export function replyText(reply: ChatResponse): string {
  const action = reply.pending_action;
  if (action) {
    const condensed = promptText(action);
    if (condensed !== action.prompt) return condensed;
  }
  return withoutSourcesLine(reply.reply, reply.sources);
}
