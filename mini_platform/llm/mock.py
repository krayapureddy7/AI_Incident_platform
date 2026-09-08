"""
Offline providers: a deterministic mock and a scripted adversary.

``MockProvider`` exists so the LLM code path can be exercised in CI without a
network, a key, or sampling noise. It reproduces the trajectories the
deterministic agents produce, which means a run with ``LLM_PROVIDER=mock``
reaches the same verdicts as a run with no provider at all -- the difference is
that the LLM branch, its validation, and its trace fields are all executed.

``ScriptedProvider`` is the opposite tool: it returns exactly what a test tells
it to, including malformed JSON, hallucinated tool names, and prompt-injected
output. It is how the adversarial cases are written, and it is why those tests
are hermetic rather than dependent on coaxing a real model into misbehaving.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List, Optional, Type

from pydantic import BaseModel

from .provider import LLMProvider

#: Version of the offline provider implementations.
__version__ = "1.0.0"


def _first_match(text: str, needles: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(n in lowered for n in needles)


def _section(prompt: str, start: str, end: str = "Respond with a single JSON") -> str:
    """
    Extract the evidence region of a prompt.

    Keyword matching must see only the incident and evidence, never the
    surrounding instructions -- the prompts embed the allowed vocabulary, and
    scanning the whole prompt would match every symptom against its own list.
    """
    head = prompt.find(start)
    if head == -1:
        return ""
    head += len(start)
    tail = prompt.find(end, head)
    return prompt[head:tail] if tail != -1 else prompt[head:]


class MockProvider(LLMProvider):
    """
    A deterministic stand-in for a language model.

    Responses are derived from the prompt with the same rules the deterministic
    agents use, so the mock agrees with the fallback path by construction. It
    performs no I/O and is safe to use in CI.
    """

    name = "mock"

    def __init__(self, model: str = "mock-deterministic", **kwargs: Any):
        super().__init__(model=model, **kwargs)
        #: Number of generations served, asserted by tests that need to prove
        #: the LLM path was actually taken.
        self.call_count = 0

    def _generate(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        self.call_count += 1
        payload = self._payload_for(prompt, schema)
        text = json.dumps(payload)
        return {
            "text": text,
            "model": self.model,
            # Usage figures are proportional to the payload so traces show
            # varying, non-constant numbers rather than the fixed estimates the
            # deterministic path records.
            "usage_in": max(1, len(prompt) // 4),
            "usage_out": max(1, len(text) // 4),
            "cost_usd": 0.0,
        }

    def _payload_for(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        """Route by the schema the caller asked for, then mirror deterministic rules."""
        name = schema.__name__

        if name == "LLMPlannerReasoning":
            return self._planner_payload(prompt)
        if name == "LLMDiagnosis":
            return self._diagnosis_payload(prompt)
        if name == "LLMRemediationProposal":
            return self._proposal_payload(prompt)
        if name == "LLMRiskNarrative":
            return {"risk_narrative": "Deterministic mock narrative: the verdict above stands."}
        return {}

    @staticmethod
    def _planner_payload(prompt: str) -> Dict[str, Any]:
        """Mirror the keyword classification in ``PlannerAgent``."""
        incident = _section(prompt, "Incident:")
        symptoms: List[str] = []
        if _first_match(incident, ["oom", "memory", "heap", "leak"]):
            symptoms.append("MEMORY_EXHAUSTION_OR_LEAK")
        if _first_match(incident, ["latency", "504", "slow", "timeout"]):
            symptoms.append("LATENCY_SPIKE_OR_TIMEOUT")
        if _first_match(incident, ["500", "503", "error rate", "crash"]):
            symptoms.append("HIGH_ERROR_RATE")
        if _first_match(incident, ["redis", "database", "connection", "pool"]):
            symptoms.append("RESOURCE_OR_CONNECTION_STARVATION")
        if not symptoms:
            symptoms.append("UNKNOWN_DEGRADATION")
        return {
            "symptoms": symptoms,
            "triage_summary": "Mock triage identified {0} symptom(s).".format(len(symptoms)),
        }

    @staticmethod
    def _diagnosis_payload(prompt: str) -> Dict[str, Any]:
        """
        Mirror the threshold logic in ``InvestigatorAgent``.

        The prompt embeds the serialized metrics, so the thresholds are read back
        out of it rather than being guessed.
        """
        doc_ids = []
        for chunk in prompt.split('"doc_id": "')[1:]:
            doc_ids.append(chunk.split('"')[0])

        metrics = _section(prompt, "Metrics:", "Logs:")
        logs = _section(prompt, "Logs:", "Retrieved documents:")

        if metrics.strip() in {"{}", ""} and logs.strip() in {"[]", ""}:
            root_cause = "EVIDENCE_UNAVAILABLE"
            summary = "Mock diagnosis: telemetry unavailable, no mutation justified."
        elif _first_match(logs, ["outofmemoryerror", "memory leak"]) or _exceeds(
            metrics, "memory_utilization_pct", 90.0
        ):
            root_cause = "MEMORY_LEAK_HEAP_EXHAUSTION"
            summary = "Mock diagnosis: heap exhaustion indicated by memory saturation."
        elif _exceeds(metrics, "cpu_utilization_pct", 90.0):
            root_cause = "CPU_AND_CONNECTION_SATURATION"
            summary = "Mock diagnosis: CPU and connection saturation."
        else:
            root_cause = "SERVICE_UNRESPONSIVE"
            summary = "Mock diagnosis: service degraded without a single dominant cause."

        return {
            "identified_root_cause": root_cause,
            "diagnosis_summary": summary,
            "cited_doc_ids": doc_ids[:1],
        }

    @staticmethod
    def _proposal_payload(prompt: str) -> Dict[str, Any]:
        """Mirror the root-cause dispatch in ``OpsAgent``."""
        if "Root cause: MEMORY_LEAK_HEAP_EXHAUSTION" in prompt:
            return {
                "tool_name": "simulate_restart",
                "parameters": {"reason": "Rolling restart to flush the leaked heap pool."},
                "reasoning": "Mock proposal: the runbook prescribes a rolling restart for heap exhaustion.",
                "rejected_alternatives": [
                    {
                        "alternative": "simulate_scale",
                        "reason_rejected": "Scaling adds replicas that inherit the same leak.",
                    }
                ],
            }
        if "Root cause: EVIDENCE_UNAVAILABLE" in prompt:
            return {
                "tool_name": "get_metrics",
                "parameters": {},
                "reasoning": "Mock proposal: telemetry is missing, so re-read before mutating.",
                "rejected_alternatives": [
                    {
                        "alternative": "simulate_restart",
                        "reason_rejected": "Mutating without evidence is unjustified.",
                    }
                ],
            }

        current = _read_number(prompt, "current_replicas")
        target = int(current) + 2 if current is not None else 4
        return {
            "tool_name": "simulate_scale",
            "parameters": {
                "replicas": min(target, 10),
                "reason": "Scale out to relieve saturation.",
            },
            "reasoning": "Mock proposal: additional replicas relieve the observed saturation.",
            "rejected_alternatives": [
                {
                    "alternative": "simulate_restart",
                    "reason_rejected": "A cold restart under load causes a connection stampede.",
                }
            ],
        }


def _read_number(prompt: str, field: str) -> Optional[float]:
    """Pull a numeric field out of JSON embedded in a prompt."""
    marker = '"{0}":'.format(field)
    idx = prompt.find(marker)
    if idx == -1:
        return None
    tail = prompt[idx + len(marker):].strip()
    number = ""
    for char in tail:
        if char.isdigit() or char == "." or (char == "-" and not number):
            number += char
        else:
            break
    try:
        return float(number)
    except ValueError:
        return None


def _exceeds(prompt: str, field: str, threshold: float) -> bool:
    value = _read_number(prompt, field)
    return value is not None and value > threshold


class ScriptedProvider(LLMProvider):
    """
    Returns pre-programmed responses, in order, regardless of the prompt.

    Intended for tests that need a specific failure: malformed JSON, a tool that
    does not exist, a fabricated citation, or an injected instruction. Each entry
    may be a JSON string, a dict (serialized for you), or a callable raising an
    exception to simulate a transport failure.
    """

    name = "scripted"

    def __init__(
        self,
        responses: Optional[List[Any]] = None,
        model: str = "scripted-test",
        **kwargs: Any,
    ):
        super().__init__(model=model, **kwargs)
        self.responses: List[Any] = list(responses or [])
        self.calls: List[str] = []

    def _generate(self, prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
        self.calls.append(prompt)
        if not self.responses:
            raise RuntimeError("ScriptedProvider exhausted: no response queued")

        item = self.responses.pop(0)
        if isinstance(item, Callable):  # type: ignore[arg-type]
            item = item()
        if isinstance(item, BaseException):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return {"text": text, "model": self.model, "usage_in": 10, "usage_out": 10}
