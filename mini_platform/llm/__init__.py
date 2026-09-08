"""
LLM layer.

**LLM = reasoning and proposals. Deterministic code = workflow control, safety,
authorization, and execution.**

The Planner, Investigator and Ops agents may use a language model to reason. The
Verifier may use one to narrate a verdict it did not reach. Nothing here can
route the workflow, widen a permission, or dispatch a tool -- every generation is
validated against a Pydantic schema and then handed to the same deterministic
safety engine that adjudicates a rule-based proposal.

With no configuration this layer is inert: ``resolve_provider()`` returns
``None`` and every agent uses its deterministic path, which is what keeps the
platform runnable offline and the evaluation gate reproducible.
"""
from .provider import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MODELS,
    DEFAULT_TIMEOUT_SEC,
    LLMProvider,
    LLMResult,
    resolve_provider,
    trace_kwargs,
)

#: Version of the LLM layer as a whole.
__version__ = "1.0.0"

__all__ = [
    "LLMProvider",
    "LLMResult",
    "resolve_provider",
    "trace_kwargs",
    "DEFAULT_MODELS",
    "DEFAULT_TIMEOUT_SEC",
    "DEFAULT_MAX_ATTEMPTS",
    "__version__",
]
