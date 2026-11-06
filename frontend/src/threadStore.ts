const KEY = "tier0.thread";

// Browsers can refuse storage access (private modes, blocked cookies); the chat still works then,
// it just can't restore the conversation after a reload.
export const threadStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(KEY);
    } catch {
      return null;
    }
  },
  set(id: string): void {
    try {
      sessionStorage.setItem(KEY, id);
    } catch {
      /* ignore */
    }
  },
  clear(): void {
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
  },
};
