"""
Tests for the OpenRouter provider and the Gemini -> OpenRouter fallback.

Two behaviours matter here beyond "the adapter parses a response":

- **A primary provider's failure does not end the run.** ``FallbackProvider``
  hands the retry to a second vendor instead of letting the first vendor's
  exception exhaust ``complete``'s attempts on its own.
- **``resolve_provider`` still never raises or makes a network call at
  construction time.** Only ``OPENROUTER_API_KEY`` gates whether the fallback is
  wired in; the model list is otherwise all defaults.
"""
import os
import unittest

from pydantic import BaseModel

from mini_platform.llm.fallback import FallbackProvider
from mini_platform.llm.mock import ScriptedProvider
from mini_platform.llm.openrouter import OpenRouterProvider
from mini_platform.llm.provider import resolve_provider


class _Schema(BaseModel):
    value: str


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Records the request body and returns a canned OpenRouter payload."""

    def __init__(self, payload):
        self._payload = payload
        self.last_kwargs = None

    def post(self, url, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(self._payload)

    def close(self):
        pass


class EnvTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)


class TestOpenRouterProvider(EnvTestCase):
    def test_generate_parses_response_and_lists_fallback_models(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
        provider = OpenRouterProvider(
            model="google/gemma-4-26b-a4b-it:free",
            fallback_models=["nvidia/nemotron-3-ultra-550b-a55b:free"],
        )
        fake = _FakeSession({
            "model": "google/gemma-4-26b-a4b-it:free",
            "choices": [{"message": {"content": '{"value": "ok"}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        })
        provider._session = fake

        raw = provider._generate("prompt", _Schema)

        self.assertEqual(raw["text"], '{"value": "ok"}')
        self.assertEqual(raw["usage_in"], 5)
        self.assertEqual(raw["usage_out"], 3)
        sent_models = fake.last_kwargs["json"]["models"]
        self.assertEqual(
            sent_models,
            ["google/gemma-4-26b-a4b-it:free", "nvidia/nemotron-3-ultra-550b-a55b:free"],
        )

    def test_complete_validates_against_schema(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
        provider = OpenRouterProvider(model="google/gemma-4-26b-a4b-it:free")
        provider._session = _FakeSession({
            "choices": [{"message": {"content": '{"value": "ok"}'}}],
            "usage": {},
        })

        result = provider.complete(prompt="p", schema=_Schema, purpose="test")

        self.assertTrue(result.valid)
        self.assertEqual(result.parsed.value, "ok")


class TestFallbackProvider(unittest.TestCase):
    def test_secondary_answers_when_primary_raises(self):
        primary = ScriptedProvider(responses=[RuntimeError("gemini unavailable")])
        secondary = ScriptedProvider(responses=[{"value": "from-secondary"}])
        fallback = FallbackProvider(primary, secondary)

        result = fallback.complete(prompt="p", schema=_Schema, purpose="test")

        self.assertTrue(result.valid)
        self.assertEqual(result.parsed.value, "from-secondary")
        self.assertEqual(fallback.name, "scripted+scripted")

    def test_primary_answers_when_it_succeeds(self):
        primary = ScriptedProvider(responses=[{"value": "from-primary"}])
        secondary = ScriptedProvider(responses=[RuntimeError("should not be called")])
        fallback = FallbackProvider(primary, secondary)

        result = fallback.complete(prompt="p", schema=_Schema, purpose="test")

        self.assertTrue(result.valid)
        self.assertEqual(result.parsed.value, "from-primary")

    def test_secondary_answers_when_primary_returns_empty_body(self):
        """
        A primary can answer HTTP 200 with an empty body instead of raising --
        observed live from Gemini. That is still a primary failure and must
        still hand off to the secondary, not fall through to the caller's own
        (non-existent, from FallbackProvider's perspective) retry of the same
        vendor.
        """
        primary = ScriptedProvider(responses=[""])
        secondary = ScriptedProvider(responses=[{"value": "from-secondary"}])
        fallback = FallbackProvider(primary, secondary)

        result = fallback.complete(prompt="p", schema=_Schema, purpose="test")

        self.assertTrue(result.valid)
        self.assertEqual(result.parsed.value, "from-secondary")

    def test_secondary_answers_when_primary_returns_non_json_prose(self):
        primary = ScriptedProvider(responses=["I cannot help with that request."])
        secondary = ScriptedProvider(responses=[{"value": "from-secondary"}])
        fallback = FallbackProvider(primary, secondary)

        result = fallback.complete(prompt="p", schema=_Schema, purpose="test")

        self.assertTrue(result.valid)
        self.assertEqual(result.parsed.value, "from-secondary")


class TestResolveProviderOpenRouter(EnvTestCase):
    def test_openrouter_selected_directly(self):
        os.environ["LLM_PROVIDER"] = "openrouter"
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
        provider = resolve_provider()
        self.assertIsInstance(provider, OpenRouterProvider)
        self.assertEqual(provider.model, "google/gemma-4-26b-a4b-it:free")

    def test_openrouter_without_key_is_unconfigured(self):
        os.environ["LLM_PROVIDER"] = "openrouter"
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.assertIsNone(resolve_provider())

    def test_gemini_without_openrouter_key_is_unwrapped(self):
        """No OPENROUTER_API_KEY means no fallback wrapper -- and, absent the
        optional google-genai package in this environment, Gemini itself
        degrades to deterministic reasoning exactly as it did before this
        feature existed."""
        os.environ["LLM_PROVIDER"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ.pop("OPENROUTER_API_KEY", None)
        provider = resolve_provider()
        self.assertNotIsInstance(provider, FallbackProvider)


if __name__ == "__main__":
    unittest.main()
