"""
Cross-provider fallback.

Every adapter already retries schema-invalid output against *itself* (see
``LLMProvider.complete``). ``FallbackProvider`` handles the other failure mode:
the primary vendor itself did not produce a usable generation -- rate-limited,
in an outage, missing a dependency at call time, or answering with an empty or
non-JSON body instead of raising -- in which case retrying the same vendor is
pointless and the platform should reach for a different one before giving up
and dropping to deterministic reasoning.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Type

from pydantic import BaseModel

from .provider import LLMProvider, _strip_code_fence

#: Version of this provider adapter.
__version__ = "1.1.0"


def _is_parseable_json(text: str) -> bool:
    """
    True when ``text`` is non-empty and parses as JSON of some shape.

    Not a schema check -- ``complete()`` already retries a schema mismatch
    against the same provider with the validation error fed back, and that
    behaviour is unchanged here. This only catches the case a raised
    exception cannot: a primary that responds with HTTP 200 and an empty or
    non-JSON body (a real, observed Gemini behaviour), which is exactly as
    much a primary failure as an exception and must not be treated as a
    successful generation.
    """
    if not text or not text.strip():
        return False
    try:
        json.loads(_strip_code_fence(text))
    except (ValueError, TypeError):
        return False
    return True


class FallbackProvider(LLMProvider):
    """
    Tries a primary provider, then a secondary one if the primary fails.

    "Fails" covers two cases: the primary's ``_generate`` raises (a
    transport error, a timeout, a missing dependency), or it returns
    normally but with nothing parseable as JSON (an empty or truncated
    body, or plain prose) -- observed in practice from a live Gemini call
    that answered HTTP 200 with an empty response. Both leave the primary
    unable to produce a generation, so both hand the attempt to the
    secondary instead of exhausting the primary's own retry budget on a
    vendor that is not going to answer differently.
    """

    def __init__(self, primary: LLMProvider, secondary: LLMProvider):
        super().__init__(
            model=primary.model,
            timeout_sec=primary.timeout_sec,
            max_attempts=primary.max_attempts,
        )
        self._primary = primary
        self._secondary = secondary
        # Recorded in traces so it is visible which pair is configured, even
        # though a given generation may have been answered by either half.
        self.name = "{0}+{1}".format(primary.name, secondary.name)

    def _generate(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        try:
            raw = self._primary._generate(prompt, schema)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - the secondary gets its own chance
            return self._secondary._generate(prompt, schema)  # noqa: SLF001

        if _is_parseable_json(raw.get("text", "")):
            return raw
        # The primary answered without raising, but returned nothing schema
        # validation could ever accept (empty, truncated, or plain prose).
        # That is as much a primary failure as an exception, so the secondary
        # still gets a chance instead of the run falling straight through to
        # deterministic reasoning.
        return self._secondary._generate(prompt, schema)  # noqa: SLF001

    def close(self) -> None:
        self._primary.close()
        self._secondary.close()
