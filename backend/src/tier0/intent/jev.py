from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError
from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient, TypeSafeError

from tier0.intent.prompts import (
    CONTINUITY_DEFINITIONS,
    DOMAIN_DEFINITIONS,
    INTENT_DEFINITIONS,
    recent_history,
)
from tier0.intent.types import ClassifierError, Continuity, Domain, Intent, IntentResult, Turn

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2

QUESTIONS = {
    "intent": Choice(
        instructions=(
            "What does the employee want in the CURRENT MESSAGE? Judge the current message, "
            "using the conversation only to resolve short replies such as 'yes' or 'the first'."
        ),
        criteria={i.value: text for i, text in INTENT_DEFINITIONS.items()},
    ),
    "domain": Choice(
        instructions="Which area does the CURRENT MESSAGE concern?",
        criteria={d.value: text for d, text in DOMAIN_DEFINITIONS.items()},
    ),
    "continuity": Choice(
        instructions="How does the CURRENT MESSAGE relate to the earlier conversation?",
        criteria={c.value: text for c, text in CONTINUITY_DEFINITIONS.items()},
    ),
}


class JevClient(Protocol):
    def system_one(self, state: Any, questions: Any) -> Any: ...


def default_client_factory(api_key: str | None, timeout: float, max_retries: int) -> JevClient:
    client = TypeSafeClient(
        api_key=api_key, retry=RetryPolicy(max_retries=max_retries, timeout=timeout)
    )
    return client


class JevIntentClassifier:
    name = "jev"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client_factory: Callable[[str | None, float, int], JevClient] = default_client_factory,
    ) -> None:
        self._settings = (api_key, timeout, max_retries)
        self._factory = client_factory
        self._client: JevClient | None = None

    def classify(self, message: str, history: list[Turn]) -> IntentResult:
        if self._client is None:
            self._client = self._factory(*self._settings)
        state = {"current_message": message, "conversation": recent_history(history)}
        try:
            response = self._client.system_one(state=state, questions=QUESTIONS)
            answers = response.choices
            intent, domain, continuity = (
                answers["intent"],
                answers["domain"],
                answers["continuity"],
            )
            return IntentResult(
                intent=Intent(intent.choice),
                domain=Domain(domain.choice),
                continuity=Continuity(continuity.choice),
                confidence=intent.confidence,
                source="jev",
                model=str(response.model),
            )
        except TypeSafeError as error:
            raise ClassifierError(f"JEV request failed: {type(error).__name__}") from error
        except (KeyError, ValueError, ValidationError, AttributeError) as error:
            raise ClassifierError(
                f"JEV returned an unusable answer: {type(error).__name__}"
            ) from error
