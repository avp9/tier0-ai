from __future__ import annotations

from collections.abc import Mapping

from tier0.intent.jev import JevIntentClassifier
from tier0.intent.openai_classifier import OpenAIIntentClassifier
from tier0.intent.resilient import ResilientIntentClassifier
from tier0.intent.types import IntentClassifier
from tier0.llm.openai_chat import OpenAIChatModel


def build_classifier(env: Mapping[str, str]) -> ResilientIntentClassifier:
    """JEV first when a TypeSafe key is set, then OpenAI when an OpenAI key is set."""
    chain: list[IntentClassifier] = []
    if env.get("TYPESAFE_API_KEY"):
        chain.append(JevIntentClassifier(api_key=env["TYPESAFE_API_KEY"]))
    if env.get("OPENAI_API_KEY"):
        model = env.get("OPENAI_MODEL_NAME") or "gpt-4o-mini"
        chat = OpenAIChatModel(api_key=env["OPENAI_API_KEY"], model=model)
        chain.append(OpenAIIntentClassifier(chat, model_name=model))
    if not chain:
        raise ValueError("set TYPESAFE_API_KEY and/or OPENAI_API_KEY to classify intents")
    return ResilientIntentClassifier(chain)
