"""
LLM provider abstraction.

The reasoning agents (Planner, Investigator, Ops) may delegate their judgement to
a language model. Everything about *how* that model is reached -- which vendor,
which model id, how failures are handled -- lives behind this interface, so an
agent never imports a vendor SDK and never sees a raw HTTP response.

Two properties this module exists to guarantee:

- **A generation is only ever data.** ``complete`` returns an ``LLMResult`` whose
  ``parsed`` field is either an instance of the caller's Pydantic schema or
  ``None``. Free-form model text never reaches an agent unvalidated, and a
  provider failure is a return value rather than an exception, so a graph node
  cannot be taken down by a vendor outage.
- **Absence of configuration is not an error.** ``resolve_provider`` returns
  ``None`` when nothing is configured, and every agent treats ``None`` as "use
  the deterministic path". The platform therefore runs identically offline with
  no keys, which is what keeps the evaluation gate meaningful.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ValidationError

#: Version of the provider interface and result contract.
__version__ = "1.0.0"

#: Environment variables read by :func:`resolve_provider`.
PROVIDER_ENV_VAR = "LLM_PROVIDER"
MODEL_ENV_VAR = "LLM_MODEL"
TIMEOUT_ENV_VAR = "LLM_TIMEOUT_SEC"
MAX_ATTEMPTS_ENV_VAR = "LLM_MAX_ATTEMPTS"

#: Per-provider API key variables. A provider selected without its key resolves
#: to ``None`` rather than raising, so a half-configured environment degrades to
#: deterministic operation instead of failing every run.
API_KEY_ENV_VARS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}

#: Default model per provider. Overridable with ``LLM_MODEL``.
#:
#: Model ids are retired by vendors on their own schedule -- a request for one
#: that no longer exists comes back 404, which this platform reports as an
#: invalid generation and answers by reasoning deterministically. That degrades
#: safely, but it degrades silently unless someone reads the trace, so these
#: defaults are worth revisiting when a run shows `fallback:invalid_output`
#: across every step.
DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash",
    "groq": "llama-3.3-70b-versatile",
}

#: Wall-clock budget for a single generation. Deliberately shorter than the
#: LLM-bearing graph nodes' timeout so the provider fails into the deterministic
#: fallback rather than being abandoned mid-flight by the resilience guard,
#: which cannot kill the thread it started.
DEFAULT_TIMEOUT_SEC = 20.0

#: Attempts per call, including the first. A schema-invalid generation is
#: retried once by default with the validation error fed back to the model.
DEFAULT_MAX_ATTEMPTS = 2


@dataclass
class LLMResult:
    """
    Outcome of one generation request.

    Field names avoid the substrings the trace redactor blanks (``token``,
    ``key``, ``auth``, ``secret``) -- ``usage_in``/``usage_out`` rather than
    ``prompt_tokens``/``completion_tokens`` -- because every one of these values
    is written into an audit trace, and a redacted usage figure is worse than no
    usage figure at all.
    """

    valid: bool
    parsed: Optional[BaseModel] = None
    raw_text: str = ""
    model: str = ""
    prompt_version: str = ""
    purpose: str = ""
    latency_ms: float = 0.0
    usage_in: int = 0
    usage_out: int = 0
    cost_usd: float = 0.0
    attempts: int = 0
    validation_error: Optional[str] = None

    @property
    def validation_result(self) -> str:
        """Compact status string recorded in the trace."""
        if self.valid:
            return "valid"
        return "invalid: " + (self.validation_error or "unknown")

    def trace_fields(self) -> Dict[str, Any]:
        """Metadata to attach to the calling agent's trace step."""
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "validation_result": self.validation_result,
            "usage_in": self.usage_in,
            "usage_out": self.usage_out,
            "llm_latency_ms": round(self.latency_ms, 2),
        }


class LLMProvider(ABC):
    """
    A source of schema-validated generations.

    Implementations must not raise from :meth:`complete`. A transport error, a
    timeout, a refusal, or an unparseable response are all reported as
    ``LLMResult(valid=False, ...)``, so the caller can fall back deterministically
    without wrapping every call site in try/except.
    """

    #: Identifier recorded in traces, e.g. ``"gemini"``.
    name: str = "abstract"

    def __init__(
        self,
        model: str = "",
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ):
        self.model = model
        self.timeout_sec = timeout_sec
        self.max_attempts = max(1, max_attempts)

    @abstractmethod
    def _generate(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        """
        Perform one raw generation.

        Returns a dict with at least ``text``; optionally ``usage_in``,
        ``usage_out``, ``cost_usd``, ``model``. May raise -- :meth:`complete`
        converts exceptions into an invalid result.
        """

    def complete(
        self,
        *,
        prompt: str,
        schema: Type[BaseModel],
        purpose: str,
        prompt_version: str = "",
    ) -> LLMResult:
        """
        Generate and validate against ``schema``.

        A schema-invalid generation is retried up to ``max_attempts`` with the
        validation error appended to the prompt, so the model can correct itself.
        The result is valid only if some attempt parsed cleanly.
        """
        started = time.time()
        last_error: Optional[str] = None
        last_text = ""
        usage_in = 0
        usage_out = 0
        cost_usd = 0.0
        attempt = 0

        while attempt < self.max_attempts:
            attempt += 1
            if last_error is None:
                effective_prompt = prompt
            else:
                effective_prompt = (
                    prompt
                    + "\n\nYour previous response was rejected by schema validation:\n"
                    + last_error
                    + "\n\nReturn corrected JSON only."
                )

            try:
                raw = self._generate(effective_prompt, schema)
            except Exception as exc:  # noqa: BLE001 - reported, never propagated
                last_error = "{0}: {1}".format(type(exc).__name__, exc)
                continue

            last_text = raw.get("text", "") or ""
            usage_in += int(raw.get("usage_in", 0) or 0)
            usage_out += int(raw.get("usage_out", 0) or 0)
            cost_usd += float(raw.get("cost_usd", 0.0) or 0.0)

            try:
                parsed = schema.model_validate_json(_strip_code_fence(last_text))
            except (ValidationError, ValueError) as exc:
                last_error = _compact_error(exc)
                continue

            return LLMResult(
                valid=True,
                parsed=parsed,
                raw_text=last_text,
                model=raw.get("model") or self.model,
                prompt_version=prompt_version,
                purpose=purpose,
                latency_ms=(time.time() - started) * 1000.0,
                usage_in=usage_in,
                usage_out=usage_out,
                cost_usd=cost_usd,
                attempts=attempt,
            )

        return LLMResult(
            valid=False,
            parsed=None,
            raw_text=last_text,
            model=self.model,
            prompt_version=prompt_version,
            purpose=purpose,
            latency_ms=(time.time() - started) * 1000.0,
            usage_in=usage_in,
            usage_out=usage_out,
            cost_usd=cost_usd,
            attempts=attempt,
            validation_error=last_error,
        )

    def close(self) -> None:
        """Release client resources. Overridden where a client holds sockets."""


def trace_kwargs(
    mode: str,
    result: Optional[LLMResult],
    fallback_tokens: int,
    fallback_cost_usd: float,
) -> Dict[str, Any]:
    """
    Build the reasoning-provenance keyword arguments for ``record_step``.

    Usage figures follow the mode rather than the attempt: a step that reasoned
    with a model reports what the provider measured, and a step that fell back
    reports the deterministic estimate, because that is what actually produced
    the result. A failed generation still contributes its own usage on top --
    those tokens were really spent, and a trace that hid them would understate
    the cost of a run that fell back.
    """
    if result is None:
        return {"mode": mode, "estimated_tokens": fallback_tokens, "cost_usd": fallback_cost_usd}

    fields = result.trace_fields()
    if mode == "llm":
        fields["estimated_tokens"] = result.usage_in + result.usage_out
        fields["cost_usd"] = result.cost_usd
    else:
        # Fallback: the deterministic estimate plus whatever the rejected
        # generation consumed.
        fields["estimated_tokens"] = fallback_tokens + result.usage_in + result.usage_out
        fields["cost_usd"] = fallback_cost_usd + result.cost_usd
    fields["mode"] = mode
    return fields


def _strip_code_fence(text: str) -> str:
    """
    Remove a markdown code fence around a JSON body.

    Models routinely wrap JSON in a fenced block despite instructions not to.
    Stripping it here means one formatting quirk does not read as a schema
    violation and burn a retry.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def _compact_error(exc: Exception) -> str:
    """Render a validation error small enough to feed back into a prompt."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:5]:
            location = ".".join(str(p) for p in err.get("loc", ()))
            parts.append("{0}: {1}".format(location, err.get("msg", "")))
        return "; ".join(parts) or "schema validation failed"
    return "{0}: {1}".format(type(exc).__name__, exc)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def resolve_provider() -> Optional[LLMProvider]:
    """
    Build the provider described by the environment, or ``None``.

    ``None`` is the unconfigured default and means "use deterministic
    reasoning". A provider named without its API key also resolves to ``None``:
    a half-configured environment degrades to today's behaviour rather than
    failing every incident, and the trace records which mode was used either way.

    This function is called during agent construction and must never raise --
    ``tools/check_versions.py`` instantiates every agent with no arguments.
    """
    selected = (os.environ.get(PROVIDER_ENV_VAR) or "").strip().lower()
    if not selected or selected in {"none", "off", "deterministic"}:
        return None

    timeout_sec = _env_float(TIMEOUT_ENV_VAR, DEFAULT_TIMEOUT_SEC)
    max_attempts = _env_int(MAX_ATTEMPTS_ENV_VAR, DEFAULT_MAX_ATTEMPTS)
    model = (os.environ.get(MODEL_ENV_VAR) or "").strip()

    if selected == "mock":
        from .mock import MockProvider

        return MockProvider(
            model=model or "mock-deterministic",
            timeout_sec=timeout_sec,
            max_attempts=max_attempts,
        )

    key_var = API_KEY_ENV_VARS.get(selected)
    if key_var is None or not os.environ.get(key_var):
        return None

    try:
        if selected == "gemini":
            from .gemini import GeminiProvider

            return GeminiProvider(
                model=model or DEFAULT_MODELS["gemini"],
                timeout_sec=timeout_sec,
                max_attempts=max_attempts,
            )
        if selected == "groq":
            from .groq import GroqProvider

            return GroqProvider(
                model=model or DEFAULT_MODELS["groq"],
                timeout_sec=timeout_sec,
                max_attempts=max_attempts,
            )
    except ImportError:
        # The SDK is an optional extra. A missing package is a configuration
        # gap, not a crash: fall back to deterministic reasoning.
        return None
    return None
