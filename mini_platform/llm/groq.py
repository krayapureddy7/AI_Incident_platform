"""
Groq provider.

The SDK is an optional extra (``pip install -e ".[llm]"``), imported inside the
constructor for the same reason as the Gemini adapter: a deployment that does not
select this provider should neither need the package nor fail at startup without
it.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Type

from pydantic import BaseModel

from .provider import LLMProvider

#: Version of this provider adapter.
__version__ = "1.0.0"


class GroqProvider(LLMProvider):
    """Generates JSON through the Groq chat-completions API."""

    name = "groq"

    def __init__(self, model: str = "llama-3.3-70b-versatile", **kwargs: Any):
        super().__init__(model=model, **kwargs)
        try:
            from groq import Groq  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "LLM_PROVIDER=groq requires the groq package. "
                'Install the optional extra: pip install -e ".[llm]"'
            ) from exc

        self._client = Groq(
            api_key=os.environ["GROQ_API_KEY"],
            timeout=self.timeout_sec,
        )

    def _generate(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        choice = response.choices[0] if response.choices else None
        text = ""
        if choice is not None:
            text = getattr(choice.message, "content", "") or ""

        usage = getattr(response, "usage", None)
        return {
            "text": text,
            "model": getattr(response, "model", self.model) or self.model,
            "usage_in": int(getattr(usage, "prompt_tokens", 0) or 0),
            "usage_out": int(getattr(usage, "completion_tokens", 0) or 0),
        }

    def close(self) -> None:
        client = getattr(self, "_client", None)
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()
