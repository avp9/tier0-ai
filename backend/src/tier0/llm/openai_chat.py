from __future__ import annotations

from collections.abc import Iterator

from langsmith.wrappers import wrap_openai
from openai import OpenAI, OpenAIError

from tier0.llm.base import LLMError
from tier0.observability.tracing import inside_a_turn


class OpenAIChatModel:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._client = OpenAI(api_key=api_key, max_retries=3, timeout=30.0)
        # The same calls, recorded (model, tokens, time) as steps of the current turn.
        self._recorded = wrap_openai(OpenAI(api_key=api_key, max_retries=3, timeout=30.0))
        self._model = model

    def _api(self) -> OpenAI:
        """The client to call now: the recording one inside a turn, the plain one anywhere else."""
        return self._recorded if inside_a_turn() else self._client

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        try:
            if json_mode:
                response = self._api().chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                )
            else:
                response = self._api().chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
        except OpenAIError as error:
            raise LLMError(f"OpenAI request failed: {type(error).__name__}") from error
        if not response.choices:  # e.g. everything filtered out
            raise LLMError("OpenAI returned no choices")
        content = response.choices[0].message.content
        if not content:
            raise LLMError("OpenAI returned an empty response")
        return content

    def stream(self, system: str, user: str) -> Iterator[str]:
        """Yield the answer text in pieces. Raises LLMError, also if it fails part-way."""
        produced = False
        try:
            response = self._api().chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                stream=True,
            )
            for chunk in response:
                if not chunk.choices:
                    continue
                piece = chunk.choices[0].delta.content
                if piece:
                    produced = True
                    yield piece
        except OpenAIError as error:
            raise LLMError(f"OpenAI request failed: {type(error).__name__}") from error
        if not produced:
            raise LLMError("OpenAI returned an empty response")
