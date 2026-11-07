import { groupConversations } from "./groupConversations";
import type { Conversation } from "./types";

// Noon on Saturday 7 November 2026, local time.
const NOW = new Date(2026, 10, 7, 12, 0, 0);
const at = (daysAgo: number, hour = 9): string =>
  new Date(2026, 10, 7 - daysAgo, hour, 0, 0).toISOString();
const chat = (id: string, updated_at: string): Conversation => ({
  thread_id: id,
  title: `Chat ${id}`,
  updated_at,
});

describe("groupConversations", () => {
  it("puts each chat under Today, Yesterday, Previous 7 days or Older by calendar day", () => {
    const groups = groupConversations(
      [chat("a", at(0)), chat("b", at(1)), chat("c", at(2)), chat("d", at(7)), chat("e", at(8))],
      NOW,
    );
    expect(groups.map((g) => [g.label, g.items.map((c) => c.thread_id)])).toEqual([
      ["Today", ["a"]],
      ["Yesterday", ["b"]],
      ["Previous 7 days", ["c", "d"]],
      ["Older", ["e"]],
    ]);
  });

  it("goes by the calendar day, not by 24 hour spans", () => {
    const lateLastNight = new Date(2026, 10, 6, 23, 59, 0).toISOString();
    const earlyThisMorning = new Date(2026, 10, 7, 0, 1, 0).toISOString();
    const groups = groupConversations([chat("new", earlyThisMorning), chat("old", lateLastNight)], NOW);
    expect(groups.map((g) => g.label)).toEqual(["Today", "Yesterday"]);
  });

  it("keeps the order it was given inside a section and leaves empty sections out", () => {
    const groups = groupConversations([chat("x", at(0, 11)), chat("y", at(0, 10)), chat("z", at(0, 8))], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.items.map((c) => c.thread_id)).toEqual(["x", "y", "z"]);
  });

  it("files a chat with an unreadable time under Older rather than losing it", () => {
    const groups = groupConversations([chat("odd", "not a date")], NOW);
    expect(groups).toEqual([{ label: "Older", items: [chat("odd", "not a date")] }]);
  });

  it("treats a chat dated in the future as today", () => {
    expect(groupConversations([chat("f", at(-1))], NOW)[0]?.label).toBe("Today");
  });

  it("returns nothing for no chats", () => {
    expect(groupConversations([], NOW)).toEqual([]);
  });
});
