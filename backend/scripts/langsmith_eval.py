"""Put the evaluation questions in LangSmith and score the real system against them.

  uv run python scripts/langsmith_eval.py upload
  uv run python scripts/langsmith_eval.py run retrieval           # search only, cheap
  uv run python scripts/langsmith_eval.py run knowledge --limit 10
  uv run python scripts/langsmith_eval.py run qa --prefix after-reranking  # reference answers
  uv run python scripts/langsmith_eval.py run all --prefix after-reranking

``upload`` makes the five datasets (tier0-retrieval, tier0-knowledge, tier0-gaps,
tier0-conversations, tier0-qa) match data/evaluation; run it again whenever those files
change. ``run`` makes an experiment per dataset: each case is run through the real search or
agent, scored, and shown in
LangSmith next to earlier experiments, with the full trace of every case. Needs LANGSMITH_API_KEY
(and OPENAI_API_KEY for real providers). Tickets always go to an in-memory fake ticket system.
Experiments label themselves with the models, top-k, threshold and git commit they ran with.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langsmith import Client
from pydantic import SecretStr

from tier0.agent.checkpoint import make_serializer
from tier0.agent.factory import Components, build_components
from tier0.agent.service import build_agent
from tier0.config import Settings
from tier0.evalrun import langsmith_eval as ls_eval
from tier0.evalrun.cases import gap_cases, knowledge_cases, load_conversation_cases
from tier0.evalrun.qa_dataset import load_jsonl
from tier0.evalrun.runner import Evaluator
from tier0.llm.base import ChatModel
from tier0.llm.openai_chat import OpenAIChatModel
from tier0.rag.evaluation import load_cases
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.submission import InMemorySubmissionStore

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "data" / "evaluation"
KEYS = list(ls_eval.DATASETS)
CONCURRENCY = {
    "retrieval": 4,
    "knowledge": 4,
    "gaps": 4,
    "conversations": 1,
    "qa": 4,
}  # tickets are shared


def specs(key: str) -> list[ls_eval.ExampleSpec]:
    retrieval = load_cases(EVAL_DIR / "retrieval_cases.json")
    return {
        "retrieval": lambda: ls_eval.retrieval_examples(retrieval),
        "knowledge": lambda: ls_eval.knowledge_examples(knowledge_cases(retrieval)),
        "gaps": lambda: ls_eval.gap_examples(gap_cases(EVAL_DIR / "knowledge_gaps.json")),
        "qa": lambda: ls_eval.qa_examples(load_jsonl(EVAL_DIR / "qa_dataset.jsonl")),
        "conversations": lambda: ls_eval.conversation_examples(
            load_conversation_cases(EVAL_DIR / "conversation_cases.json")
        ),
    }[key]()


def settings_for(args: argparse.Namespace) -> Settings:
    # Only chooses providers here: the database is never opened.
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql://unused@localhost/unused",
        session_secret=SecretStr("e" * 40),
        app_env="local",
        ticketing_provider="fake",
        llm_provider="fake" if args.providers == "fake" else "openai",
        search_mode=args.search_mode,
        vector_store_path=str(args.index_dir),
        langsmith_tracing=False,  # experiments are traced by LangSmith itself, not the app's tracer
    )


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def experiment_metadata(settings: Settings) -> dict[str, Any]:
    return {
        "embedding_model": settings.embedding_model,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.openai_model_name if settings.llm_provider == "openai" else "fake",
        "retrieval_top_k": settings.retrieval_top_k,
        "search_mode": settings.search_mode,
        "score_threshold": settings.effective_score_threshold,
        "git_commit": git_commit(),
    }


def summarize(results: Any) -> dict[str, float]:
    """The mean of each score over the cases (what the experiment page shows as its headline)."""
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for row in results:
        for evaluation in row["evaluation_results"]["results"]:
            if evaluation.score is not None:
                totals[evaluation.key] += evaluation.score
                counts[evaluation.key] += 1
    return {key: totals[key] / counts[key] for key in totals}


def targets(parts: Components, evaluator: Evaluator) -> dict[str, Any]:
    return {
        "retrieval": ls_eval.retrieval_target(parts.retriever),
        "knowledge": ls_eval.knowledge_target(evaluator),
        "gaps": ls_eval.gap_target(evaluator),
        "conversations": ls_eval.conversation_target(evaluator),
        "qa": ls_eval.qa_target(evaluator),
    }


def command_upload(args: argparse.Namespace, client: Client) -> int:
    for key in KEYS:
        result = ls_eval.sync_dataset(client, key, specs(key), limit=args.limit)
        print(
            f"{result.dataset:<22} +{result.created} new, {result.updated} refreshed, "
            f"{result.removed} removed"
        )
    return 0


def command_run(args: argparse.Namespace, client: Client) -> int:
    settings = settings_for(args)
    parts = build_components(settings, None, submission_store=InMemorySubmissionStore())
    if parts.controls is None or parts.controls.adapter is None:
        sys.exit("expected the fake ticket system")
    adapter: FakeTicketAdapter = parts.controls.adapter
    agent = build_agent(parts.deps, MemorySaver(serde=make_serializer()))  # no app tracer
    run_targets = targets(parts, Evaluator(agent, adapter))
    metadata = experiment_metadata(settings)
    judge: ChatModel | None = None  # grades answers against the reference (real models only)
    if args.providers == "real":
        judge = OpenAIChatModel(
            api_key=os.environ["OPENAI_API_KEY"], model=settings.openai_model_name
        )
    chosen = KEYS if args.dataset == "all" else [args.dataset]
    for key in chosen:
        name = ls_eval.DATASETS[key]
        data: Any = (
            client.list_examples(dataset_name=name, limit=args.limit) if args.limit else name
        )
        print(f"\n== {name} ==", flush=True)
        results = client.evaluate(
            run_targets[key],
            data=data,
            evaluators=ls_eval.evaluators_for(key, judge),
            experiment_prefix=f"{args.prefix}-{key}",
            description=f"{key} cases through the real system ({args.providers} providers)",
            metadata={**metadata, "dataset": key},
            max_concurrency=CONCURRENCY[key],
        )
        for score, value in sorted(summarize(results).items()):
            print(f"  {score:<24} {value:.3f}")
        print(f"  experiment: {results.experiment_name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--providers", choices=["real", "fake"], default="real")
    parser.add_argument(
        "--search-mode",
        choices=["dense", "hybrid_rerank"],
        default="dense",
        help="dense: embeddings alone (today's search). hybrid_rerank: plus SPLADE and a reranker",
    )
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / ".data" / "chroma")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("upload", help="make the datasets match data/evaluation")
    run = commands.add_parser("run", help="run an experiment")
    run.add_argument("dataset", choices=[*KEYS, "all"])
    run.add_argument("--prefix", default="tier0", help="names the experiment, e.g. after-reranking")
    args = parser.parse_args()
    if not os.environ.get("LANGSMITH_API_KEY"):
        sys.exit("LANGSMITH_API_KEY is not set")
    os.environ["LANGSMITH_TRACING"] = "true"  # experiments are always traced
    client = Client()
    return command_upload(args, client) if args.command == "upload" else command_run(args, client)


if __name__ == "__main__":
    sys.exit(main())
