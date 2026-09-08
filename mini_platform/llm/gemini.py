"""
Google Gemini provider.

The SDK is an optional extra (``pip install -e ".[llm]"``), so it is imported
inside the constructor rather than at module scope. A deployment that never sets
``LLM_PROVIDER=gemini`` neither needs the package nor pays for importing it, and
``resolve_provider`` turns a missing package into deterministic operation rather
than an ImportError at startup.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Type

from pydantic import BaseModel

from .provider import LLMProvider

#: Version of this provider adapter.
__version__ = "1.0.0"


class GeminiProvider(LLMProvider):
    """Generates JSON through the Gemini API, constrained by response schema."""

    name = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash", **kwargs: Any):
        super().__init__(model=model, **kwargs)
        try:
            from google import genai  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "LLM_PROVIDER=gemini requires the google-genai package. "
                'Install the optional extra: pip install -e ".[llm]"'
            ) from exc

        self._genai = genai
        self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def _generate(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        # Ask the API for JSON directly. Schema validation still happens in
        # `complete` -- a provider claiming JSON mode is not evidence that the
        # payload satisfies our contract.
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )

        usage = getattr(response, "usage_metadata", None)
        return {
            "text": getattr(response, "text", "") or "",
            "model": self.model,
            "usage_in": int(getattr(usage, "prompt_token_count", 0) or 0),
            "usage_out": int(getattr(usage, "candidates_token_count", 0) or 0),
        }

    def close(self) -> None:
        client = getattr(self, "_client", None)
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()
