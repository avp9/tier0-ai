"""Classify one message with the real providers (JEV first, OpenAI as fallback).

  TYPESAFE_API_KEY=... OPENAI_API_KEY=... \\
    uv run python scripts/classify_intent.py "any news on my ticket?"
Prints which provider answered, so you can also test the fallback by unsetting TYPESAFE_API_KEY.
"""

from __future__ import annotations

import argparse
import os
import sys

from tier0.intent.factory import build_classifier
from tier0.intent.types import IntentUnavailable


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("message")
    args = parser.parse_args()
    try:
        classifier = build_classifier(os.environ)
        print(classifier.classify(args.message, []).model_dump_json(indent=2))
    except (ValueError, IntentUnavailable) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
