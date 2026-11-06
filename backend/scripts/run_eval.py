"""Evaluate Tier0 end to end: retrieval, answers, routing, ticket flows, latency and cost.

Examples:
  # nightly / manual, real providers (needs OPENAI_API_KEY and the real index)
  uv run python scripts/run_eval.py --providers real --index-dir .data/chroma --out eval-out
  # check the harness itself, no keys (numbers say nothing about real quality)
  uv run python scripts/run_eval.py --providers fake --index-dir <fake index> --out eval-out

Tickets always go to an in-memory fake ticket system: a nightly job must not file real tickets.
Exits 1 if a hard safety rule is broken (a ticket created without confirmation).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from tier0.agent.checkpoint import make_serializer
from tier0.agent.factory import build_components
from tier0.agent.service import build_agent
from tier0.config import Settings
from tier0.evalrun.cases import gap_cases, knowledge_cases, load_conversation_cases
from tier0.evalrun.report import Results, gate_failures, summarize, to_markdown
from tier0.evalrun.runner import Evaluator
from tier0.evalrun.usage import Prices, Usage, instrument
from tier0.rag.evaluation import evaluate_gap_separation, evaluate_retrieval, load_cases
from tier0.rag.gaps import load_gap_topics
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.submission import InMemorySubmissionStore

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "data" / "evaluation"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--providers", choices=["real", "fake"], required=True)
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / ".data" / "chroma")
    parser.add_argument("--out", type=Path, default=Path("eval-out"))
    parser.add_argument(
        "--max-knowledge",
        type=int,
        default=None,
        help="only run this many knowledge conversations (limits cost)",
    )
    parser.add_argument("--price-chat-in", type=float, default=Prices.chat_input)
    parser.add_argument("--price-chat-out", type=float, default=Prices.chat_output)
    parser.add_argument("--price-embedding", type=float, default=Prices.embedding)
    args = parser.parse_args()

    # The settings only choose providers here; the database is never opened.
    base: dict[str, object] = {
        "database_url": "postgresql://unused@localhost/unused",
        "session_secret": SecretStr("e" * 40),
        "app_env": "local",
        "ticketing_provider": "fake",
        "llm_provider": "fake" if args.providers == "fake" else "openai",
        "vector_store_path": str(args.index_dir),
    }
    settings = Settings(**base)  # type: ignore[arg-type]
    parts = build_components(settings, None, submission_store=InMemorySubmissionStore())
    if parts.controls is None or parts.controls.adapter is None:
        sys.exit("expected the fake ticket system")
    adapter: FakeTicketAdapter = parts.controls.adapter

    agent = build_agent(parts.deps, MemorySaver(serde=make_serializer()))
    evaluator = Evaluator(agent, adapter)
    usage = Usage()
    prices = Prices(args.price_chat_in, args.price_chat_out, args.price_embedding)

    retrieval_cases = load_cases(EVAL_DIR / "retrieval_cases.json")
    knowledge = knowledge_cases(retrieval_cases)[: args.max_knowledge]
    gaps = gap_cases(EVAL_DIR / "knowledge_gaps.json")
    conversations = load_conversation_cases(EVAL_DIR / "conversation_cases.json")

    with instrument(usage):
        report = evaluate_retrieval(retrieval_cases, parts.retriever)
        separation = evaluate_gap_separation(
            load_gap_topics(EVAL_DIR / "knowledge_gaps.json"), report, parts.retriever
        )
        results = Results(
            retrieval=report,
            separation=separation,
            knowledge=[evaluator.run_knowledge(c) for c in knowledge],
            gaps=[evaluator.run_gap(c) for c in gaps],
            cases=[evaluator.run_case(c) for c in conversations],
            usage=usage,
            prices=prices,
            providers={"llm": settings.llm_provider, "tickets": "in-memory fake"},
        )

    summary = summarize(results)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    markdown = to_markdown(summary)
    (args.out / "summary.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    problems = gate_failures(summary)
    for problem in problems:
        print(f"GATE FAILED: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
