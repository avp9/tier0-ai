from types import SimpleNamespace
from typing import Any

import pytest
from openai import OpenAIError

from tier0.llm.base import LLMError
from tier0.llm.openai_chat import OpenAIChatModel


class StubCompletions:
    def __init__(self, content: str | None = "hello", error: Exception | None = None) -> None:
        self.content, self.error = content, error
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def model_with(stub: StubCompletions) -> OpenAIChatModel:
    model = OpenAIChatModel(api_key="test-key", model="m")
    model._client = SimpleNamespace(chat=SimpleNamespace(completions=stub))  # type: ignore[assignment]
    return model


def test_returns_text_and_sends_both_messages_deterministically() -> None:
    stub = StubCompletions("the answer")
    assert model_with(stub).complete("be brief", "question?") == "the answer"
    assert stub.kwargs["temperature"] == 0 and stub.kwargs["model"] == "m"
    assert [m["role"] for m in stub.kwargs["messages"]] == ["system", "user"]
    assert "response_format" not in stub.kwargs


def test_json_mode_requests_a_json_object() -> None:
    stub = StubCompletions("{}")
    model_with(stub).complete("s", "u", json_mode=True)
    assert stub.kwargs["response_format"] == {"type": "json_object"}


def test_provider_failures_become_llm_errors_without_leaking_details() -> None:
    stub = StubCompletions(error=OpenAIError("secret-detail-from-provider"))
    with pytest.raises(LLMError, match="OpenAIError") as raised:
        model_with(stub).complete("s", "u")
    assert "secret-detail" not in str(raised.value)


def test_empty_responses_are_errors() -> None:
    with pytest.raises(LLMError, match="empty"):
        model_with(StubCompletions(content=None)).complete("s", "u")
