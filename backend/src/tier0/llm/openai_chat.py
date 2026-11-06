from __future__ import annotations

from openai import OpenAI, OpenAIError

from tier0.llm.base import LLMError


class OpenAIChatModel:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._client = OpenAI(api_key=api_key, max_retries=3, timeout=30.0)
        self._model = model

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        try:
            if json_mode:
                response = self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                )
            else:
                response = self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
        except OpenAIError as error:
            raise LLMError(f"OpenAI request failed: {type(error).__name__}") from error
        content = response.choices[0].message.content
        if not content:
            raise LLMError("OpenAI returned an empty response")
        return content
