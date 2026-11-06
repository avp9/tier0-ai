"""Ask the knowledge base a question and show how the agent decided to answer.

Needs OPENAI_API_KEY and an index built with the same embedding model:
  OPENAI_API_KEY=... uv run python scripts/ask.py "How many casual leave days do I get?"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tier0.llm.openai_chat import OpenAIChatModel
from tier0.rag.answerability import AnswerabilityConfig
from tier0.rag.answering import AnswerService
from tier0.rag.embeddings import OpenAIEmbedder
from tier0.rag.retriever import Retriever

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / ".data" / "chroma")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY is not set")
    embedder = OpenAIEmbedder(
        api_key=api_key, model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    chat = OpenAIChatModel(
        api_key=api_key, model=os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini")
    )
    threshold = float(os.environ.get("RETRIEVAL_SCORE_THRESHOLD", "0.30"))
    top_k = int(os.environ.get("RETRIEVAL_TOP_K", "5"))

    service = AnswerService(
        Retriever.open(args.index_dir, embedder),
        chat,
        AnswerabilityConfig(score_threshold=threshold, top_k=top_k),
    )
    result = service.answer(args.question)
    print(f"Outcome: {result.outcome.value}\n\n{result.reply}\n")
    if result.decision:
        print(result.decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
