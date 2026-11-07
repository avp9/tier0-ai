import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RetrievalCheck } from "./RetrievalCheck";
import { json, stubApi } from "../test/helpers";
import type { RetrievalReport } from "../types";

const REPORT: RetrievalReport = {
  generated_at: "2026-11-07T10:02:00Z",
  duration_s: 14.2,
  embedding_model: "text-embedding-3-small",
  cases: 44,
  gap_questions: 30,
  ranks: [
    { k: 1, hit: 0.682, recall: 0.636 },
    { k: 3, hit: 0.864, recall: 0.837 },
    { k: 5, hit: 0.886, recall: 0.879 },
  ],
  mrr: 0.767,
  by_kind: [
    { kind: "handbook", hit: [{ k: 1, hit: 0.73, recall: 0.73 }, { k: 3, hit: 0.8, recall: 0.8 }, { k: 5, hit: 0.83, recall: 0.83 }] },
    { kind: "it", hit: [{ k: 1, hit: 0.6, recall: 0.6 }, { k: 3, hit: 1, recall: 1 }, { k: 5, hit: 1, recall: 1 }] },
  ],
  misses_at: 5,
  misses: [
    { id: "ret-004", query: "How long do I have to enroll in health insurance?" },
    { id: "ret-005", query: "Who is my buddy?" },
  ],
  separation: { answerable_min: 0.301, answerable_median: 0.685, gap_max: 0.63, gap_median: 0.369, separable: false },
  threshold: { current: 0.2, suggested: 0.553, answerable_kept: 0.89, gaps_rejected: 0.93, separable: false },
};

const ROUTE = "GET /api/diagnostics/retrieval";
const REFRESH = "GET /api/diagnostics/retrieval?refresh=true";

function open(onClose = vi.fn()) {
  render(<RetrievalCheck onClose={onClose} />);
  return { onClose, user: userEvent.setup() };
}

const dialog = () => screen.getByRole("dialog", { name: "Search quality check" });

describe("the search quality check", () => {
  it("says what it is doing while the check runs", async () => {
    stubApi({ [ROUTE]: () => new Promise<Response>(() => undefined) });
    open();
    expect(await screen.findByText(/Running the check/)).toBeVisible();
  });

  it("opens with a plain-language summary of how often the right article is found", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    open();
    const summary = await within(dialog()).findByText(/the right article was the very first result/);
    expect(summary).toHaveTextContent("For 68% of the 44 test questions");
    expect(summary).toHaveTextContent("for 89% it was in the top 5 results");
  });

  it("shows every measure the script prints, as percentages", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    open();
    await ready();
    const text = dialog().textContent ?? "";
    for (const expected of [
      "Hit@1", "68%", "Hit@3", "86%", "Hit@5", "89%",
      "Recall@1", "64%", "Recall@3", "84%", "Recall@5", "88%",
      "MRR", "0.767",
    ]) {
      expect(text).toContain(expected);
    }
  });

  it("explains each measure in one line behind a small question mark", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    open();
    await ready();
    const terms = ["Hit@1", "Hit@3", "Hit@5", "Recall@1", "Recall@3", "Recall@5", "MRR"];
    for (const term of terms) {
      const button = within(dialog()).getByRole("button", { name: `What is ${term}?` });
      const tip = document.getElementById(button.getAttribute("aria-describedby") ?? "");
      expect(tip, term).not.toBeNull();
      expect(tip).toHaveAttribute("role", "tooltip");
      expect(tip?.textContent ?? "").toMatch(/\S{3,}/);
      expect(tip?.textContent).not.toMatch(/\n/);
      expect((tip?.textContent ?? "").length).toBeLessThan(160); // one line
    }
    expect(describedBy("What is Hit@1?")).toMatch(/very first result/);
    expect(describedBy("What is Hit@5?")).toMatch(/top 5 results/);
    expect(describedBy("What is Recall@3?")).toMatch(/top 3 results/);
    expect(describedBy("What is MRR?")).toMatch(/position of the first correct article/);
  });

  it("also explains the knowledge-gap figures", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    open();
    await ready();
    for (const label of [
      "Questions it can answer",
      "Questions it should decline",
      "One cut-off separates them",
      "Cut-off in use",
      "Suggested cut-off",
      "Answers kept",
      "Declined correctly",
    ]) {
      expect(describedBy(`What is ${label}?`).length, label).toBeGreaterThan(20);
    }
  });

  it("splits the results by kind of question with readable names", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    open();
    const table = await within(dialog()).findByRole("table");
    const handbook = within(table).getByRole("row", { name: /Company handbook/ });
    expect(handbook).toHaveTextContent("73%");
    expect(within(table).getByRole("row", { name: /IT tickets/ })).toHaveTextContent("100%");
  });

  it("lists the questions it missed, or says there were none", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    const { unmount } = render(<RetrievalCheck onClose={() => undefined} />);
    expect(await screen.findByText("Who is my buddy?")).toBeVisible();
    expect(screen.getByText(/for these 2 questions/)).toBeVisible();
    unmount();

    stubApi({ [ROUTE]: () => json({ ...REPORT, misses: [] }) });
    render(<RetrievalCheck onClose={() => undefined} />);
    expect(await screen.findByText(/Every test question found its article/)).toBeVisible();
  });

  it("reports whether one cut-off separates the questions and what it suggests", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    open();
    await within(dialog()).findByText("No, the scores overlap");
    const text = dialog().textContent ?? "";
    for (const expected of ["0.301", "0.685", "0.630", "0.369", "0.200", "0.553", "89%", "93%"]) {
      expect(text).toContain(expected);
    }
    stubApi({
      [ROUTE]: () => json({ ...REPORT, separation: { ...REPORT.separation, separable: true } }),
    });
    render(<RetrievalCheck onClose={() => undefined} />);
    expect((await screen.findAllByText("Yes")).length).toBeGreaterThan(0);
  });

  it("runs the check again on request and shows the new result", async () => {
    let calls = 0;
    const { calls: seen } = stubApi({
      [ROUTE]: () => json(REPORT),
      [REFRESH]: () => {
        calls += 1;
        return json({ ...REPORT, mrr: 0.5 });
      },
    });
    const { user } = open();
    await within(dialog()).findByText("0.767", { exact: false });
    await user.click(screen.getByRole("button", { name: "Run again" }));
    expect(await within(dialog()).findByText("0.500", { exact: false })).toBeVisible();
    expect(calls).toBe(1);
    expect(seen.map((c) => c.path)).toEqual([
      "/api/diagnostics/retrieval",
      "/api/diagnostics/retrieval?refresh=true",
    ]);
  });

  it("keeps the old result and says why when run again is refused", async () => {
    stubApi({
      [ROUTE]: () => json(REPORT),
      [REFRESH]: () =>
        json({ detail: "The check was run a moment ago. Try again in 12 seconds." }, 429, {
          "Retry-After": "12",
        }),
    });
    const { user } = open();
    await within(dialog()).findByText("0.767", { exact: false });
    await user.click(screen.getByRole("button", { name: "Run again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 12 seconds");
    expect(within(dialog()).getByText("0.767", { exact: false })).toBeVisible();
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
  });

  it("disables Run again while it is running", async () => {
    let release: (r: Response) => void = () => undefined;
    stubApi({
      [ROUTE]: () => json(REPORT),
      [REFRESH]: () => new Promise<Response>((resolve) => (release = resolve)),
    });
    const { user } = open();
    await within(dialog()).findByText("0.767", { exact: false });
    await user.click(screen.getByRole("button", { name: "Run again" }));
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    release(json(REPORT));
    await waitFor(() => expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled());
  });

  it.each([
    [404, "The evaluation questions are not part of this deployment."],
    [503, "The search check is not available right now."],
  ])("explains a %i answer in the server's own words", async (status, detail) => {
    stubApi({ [ROUTE]: () => json({ detail }, status) });
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.queryByRole("button", { name: "Run again" })).not.toBeInTheDocument();
  });

  it("shows a question in the report as text, never as markup", async () => {
    const hostile = { ...REPORT, misses: [{ id: "x", query: "<img src=x onerror=alert(1)>" }] };
    stubApi({ [ROUTE]: () => json(hostile) });
    open();
    const item = await screen.findByText("<img src=x onerror=alert(1)>");
    expect(item.querySelector("img")).toBeNull();
  });
});

describe("the dialog", () => {
  it("starts on the Close button, closes on Escape, and on the Close button", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    const { onClose, user } = open();
    await ready();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("closes when the dimmed page behind it is clicked, but not when the dialog is", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    const { onClose, user } = open();
    await ready();
    await user.click(dialog());
    expect(onClose).not.toHaveBeenCalled();
    await user.click(dialog().parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps Tab inside the dialog, wrapping from the last control to the first", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    const { user } = open();
    await ready();
    const runAgain = screen.getByRole("button", { name: "Run again" });
    runAgain.focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(runAgain).toHaveFocus();
  });

  it("is a modal dialog with a name", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    open();
    expect(dialog()).toHaveAttribute("aria-modal", "true");
  });
});

describe("a question mark", () => {
  it("opens and closes on press, and Escape closes only the tip, not the dialog", async () => {
    stubApi({ [ROUTE]: () => json(REPORT) });
    const { onClose, user } = open();
    await ready();
    const help = screen.getByRole("button", { name: "What is Hit@1?" });
    expect(help).toHaveAttribute("aria-expanded", "false");
    await user.click(help);
    expect(help).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(help).toHaveAttribute("aria-expanded", "false");
    expect(onClose).not.toHaveBeenCalled();
  });
});

/** The report is on screen once its first question mark is. */
const ready = () => within(dialog()).findByRole("button", { name: "What is Hit@1?" });

function describedBy(name: string): string {
  const button = screen.getByRole("button", { name });
  return document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent ?? "";
}
