"""
OpenRouter provider.

OpenRouter fronts many vendors' models behind one OpenAI-compatible endpoint, so
this adapter speaks plain HTTP (via ``requests``) rather than a vendor SDK. The
``requests`` package is still an optional extra -- imported inside the
constructor -- for the same reason as the Gemini and Groq adapters: a deployment
that does not select this provider should neither need the package nor fail at
startup without it.

A model behind a free OpenRouter slot is routinely rate-limited or temporarily
unavailable. Rather than surface that as a hard failure, this adapter asks
OpenRouter itself to fail over: the request's ``models`` field lists the primary
model followed by ``fallback_models``, and OpenRouter tries them in order within
the same request. See :func:`resolve_provider` for the separate, platform-level
fallback that routes to this provider when Gemini itself is unavailable.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from .provider import LLMProvider

#: Version of this provider adapter.
__version__ = "1.0.0"

_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(LLMProvider):
    """Generates JSON through OpenRouter's chat-completions API."""

    name = "openrouter"

    def __init__(
        self,
        model: str = "google/gemma-4-26b-a4b-it:free",
        fallback_models: Optional[List[str]] = None,
        **kwargs: Any,
    ):
        super().__init__(model=model, **kwargs)
        try:
            import requests  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "LLM_PROVIDER=openrouter requires the requests package. "
                'Install the optional extra: pip install -e ".[llm]"'
            ) from exc

        self._requests = requests
        self._session = requests.Session()
        self._api_key = os.environ["OPENROUTER_API_KEY"]
        #: Additional models OpenRouter should try, in order, if ``model`` is
        #: unavailable -- e.g. rate-limited or temporarily down. Kept distinct
        #: from ``model`` itself so a trace records which one actually answered.
        self._fallback_models = list(fallback_models or [])

    def _generate(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        models = [self.model] + [m for m in self._fallback_models if m != self.model]
        response = self._session.post(
            _CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": "Bearer {0}".format(self._api_key),
                "Content-Type": "application/json",
            },
            json={
                # A single-element list behaves like the plain "model" field;
                # more than one lets OpenRouter fail over server-side.
                "models": models,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()

        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = ((choices[0] or {}).get("message") or {}).get("content", "") or ""

        usage = payload.get("usage") or {}
        return {
            "text": text,
            "model": payload.get("model") or self.model,
            "usage_in": int(usage.get("prompt_tokens", 0) or 0),
            "usage_out": int(usage.get("completion_tokens", 0) or 0),
        }

    def close(self) -> None:
        session = getattr(self, "_session", None)
        closer = getattr(session, "close", None)
        if callable(closer):
            closer()
