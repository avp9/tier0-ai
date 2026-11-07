import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { RetrievalReport } from "../types";
import { HelpTip } from "./HelpTip";
import { Modal } from "./Modal";

const percent = (value: number) => `${Math.round(value * 100)}%`;
const score = (value: number) => value.toFixed(3);
const results = (k: number) => (k === 1 ? "the very first result" : `the top ${k} results`);

const KIND_LABELS: Record<string, string> = {
  handbook: "Company handbook",
  it: "IT tickets",
  compliance: "Compliance",
};

const HIT_TIP = (k: number) =>
  k === 1
    ? "Share of test questions where a correct article is the very first result."
    : `Share of test questions where a correct article is among the top ${k} results.`;
const RECALL_TIP = (k: number) =>
  `Of all the correct articles for a question, the average share found in the top ${k} result${k === 1 ? "" : "s"}.`;
const MRR_TIP =
  "Average of 1 ÷ the position of the first correct article (1 means always first, 0.5 usually second); closer to 1 is better.";

function Metric({ name, value, tip }: { name: string; value: number; tip: string }) {
  return (
    <div className="metric">
      <div className="metric-label">
        {name}
        <HelpTip term={name}>{tip}</HelpTip>
      </div>
      <div className="metric-value">{percent(value)}</div>
      <div className="bar" aria-hidden="true">
        <span style={{ width: percent(value) }} />
      </div>
    </div>
  );
}

function Line({ label, tip, children }: { label: string; tip: string; children: React.ReactNode }) {
  return (
    <div className="check-line">
      <dt>
        {label}
        <HelpTip term={label}>{tip}</HelpTip>
      </dt>
      <dd>{children}</dd>
    </div>
  );
}

function Report({ report }: { report: RetrievalReport }) {
  const first = report.ranks[0];
  const last = report.ranks[report.ranks.length - 1];
  const { separation, threshold } = report;
  return (
    <>
      <p className="check-summary">
        {first && last && (
          <>
            For <strong>{percent(first.hit)}</strong> of the {report.cases} test questions the right
            article was {results(first.k)}, and for <strong>{percent(last.hit)}</strong> it was in{" "}
            {results(last.k)}.
          </>
        )}
      </p>

      <section aria-labelledby="check-found">
        <h3 id="check-found">How often the right article is found</h3>
        <div className="metrics">
          {report.ranks.map((r) => (
            <Metric key={`hit${r.k}`} name={`Hit@${r.k}`} value={r.hit} tip={HIT_TIP(r.k)} />
          ))}
          {report.ranks.map((r) => (
            <Metric key={`recall${r.k}`} name={`Recall@${r.k}`} value={r.recall} tip={RECALL_TIP(r.k)} />
          ))}
        </div>
        <dl className="check-lines">
          <Line label="MRR" tip={MRR_TIP}>
            <strong>{report.mrr.toFixed(3)}</strong> <span className="muted">(1.000 is perfect)</span>
          </Line>
        </dl>
      </section>

      <section aria-labelledby="check-topics">
        <h3 id="check-topics">By kind of question</h3>
        <table className="check-table">
          <caption className="visually-hidden">Hit rate by kind of question</caption>
          <thead>
            <tr>
              <th scope="col">Kind</th>
              {report.ranks.map((r) => (
                <th key={r.k} scope="col">{`Hit@${r.k}`}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {report.by_kind.map((row) => (
              <tr key={row.kind}>
                <th scope="row">{KIND_LABELS[row.kind] ?? row.kind}</th>
                {row.hit.map((cell) => (
                  <td key={cell.k}>{percent(cell.hit)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-labelledby="check-missed">
        <h3 id="check-missed">Questions it missed</h3>
        {report.misses.length === 0 ? (
          <p>Every test question found its article in {results(report.misses_at)}.</p>
        ) : (
          <>
            <p className="muted">
              No correct article in {results(report.misses_at)} for these {report.misses.length}{" "}
              questions:
            </p>
            <ul className="check-misses">
              {report.misses.map((miss) => (
                <li key={miss.id}>
                  <span>{miss.query}</span> <span className="muted">({miss.id})</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section aria-labelledby="check-gap">
        <h3 id="check-gap">Knowing when not to answer</h3>
        <p className="muted">
          Search scores each match from 0 to 1. Answers should only be given above a cut-off score.
        </p>
        <dl className="check-lines">
          <Line
            label="Questions it can answer"
            tip="Similarity of the best match for the test questions the knowledge base covers: the lowest and the typical (median) one."
          >
            lowest <strong>{score(separation.answerable_min)}</strong> · typical{" "}
            <strong>{score(separation.answerable_median)}</strong>
          </Line>
          <Line
            label="Questions it should decline"
            tip="Similarity of the best match for questions the knowledge base does not cover: the highest and the typical (median) one."
          >
            highest <strong>{score(separation.gap_max)}</strong> · typical{" "}
            <strong>{score(separation.gap_median)}</strong>
          </Line>
          <Line
            label="One cut-off separates them"
            tip="Yes means a single cut-off score would cleanly split answerable questions from ones to decline."
          >
            <strong>{separation.separable ? "Yes" : "No, the scores overlap"}</strong>
          </Line>
          <Line
            label="Cut-off in use"
            tip="Your RETRIEVAL_SCORE_THRESHOLD: matches scoring below it are turned down and offered as a ticket."
          >
            <strong>{score(threshold.current)}</strong>
          </Line>
          <Line
            label="Suggested cut-off"
            tip="The score that best splits the two groups on these test questions."
          >
            <strong>{score(threshold.suggested)}</strong>
          </Line>
          <Line
            label="Answers kept"
            tip="Share of answerable questions that would still get an answer at the suggested cut-off."
          >
            <strong>{percent(threshold.answerable_kept)}</strong>
          </Line>
          <Line
            label="Declined correctly"
            tip="Share of questions to decline that would be turned down (offered a ticket) at the suggested cut-off."
          >
            <strong>{percent(threshold.gaps_rejected)}</strong>
          </Line>
        </dl>
      </section>
    </>
  );
}

type State =
  | { status: "loading" }
  | { status: "failed"; message: string }
  | { status: "ready"; report: RetrievalReport };

const describe = (failure: unknown) =>
  failure instanceof ApiError ? failure.message : "The check could not be run. Please try again.";

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/** The search-quality check, shown over the chat: what the evaluation measured, in plain words. */
export function RetrievalCheck({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // The first look: a recent result comes straight back; otherwise the check runs.
  useEffect(() => {
    let cancelled = false;
    api
      .retrievalReport(false)
      .then((report) => !cancelled && setState({ status: "ready", report }))
      .catch((failure: unknown) => !cancelled && setState({ status: "failed", message: describe(failure) }));
    return () => {
      cancelled = true;
    };
  }, []);

  async function runAgain() {
    setRunning(true);
    setNotice(null);
    try {
      setState({ status: "ready", report: await api.retrievalReport(true) });
    } catch (failure) {
      setNotice(describe(failure)); // the report already on screen stays
    } finally {
      setRunning(false);
    }
  }

  return (
    <Modal title="Search quality check" onClose={onClose}>
      {state.status === "ready" && (
        <p className="muted check-meta">
          Based on {state.report.cases} test questions and {state.report.gap_questions} questions
          that should be declined · {state.report.embedding_model} · checked{" "}
          {when(state.report.generated_at)}, took {state.report.duration_s} s
        </p>
      )}
      {state.status === "loading" && (
        <p role="status">Running the check… the first time this can take up to half a minute.</p>
      )}
      {state.status === "failed" && <p role="alert">{state.message}</p>}
      {state.status === "ready" && <Report report={state.report} />}
      {notice && (
        <p role="alert" className="error banner">
          {notice}
        </p>
      )}
      {state.status === "ready" && (
        <div className="actions check-actions">
          <button type="button" onClick={() => void runAgain()} disabled={running}>
            {running ? "Running…" : "Run again"}
          </button>
        </div>
      )}
    </Modal>
  );
}
