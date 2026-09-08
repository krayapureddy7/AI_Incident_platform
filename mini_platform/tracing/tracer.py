"""
Audit Tracer and Post-Mortem Replay Engine.
Captures agent decisions, A2A messages, tool calls, redacted inputs/outputs,
latency, token counts, and rejected alternatives.
"""

import json
import os
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from ..models import A2AMessage, TraceStep

#: Version of the trace schema and redaction patterns. 1.4.0 adds reasoning
#: provenance to every step: how the conclusion was reached, by which model
#: and prompt, and what the schema validation said.
__version__ = "1.4.0"


# Sensitive value patterns applied to every string written to a trace.
#
# The email pattern must cover internal domains, not just public TLDs. Service
# owners in this platform are addresses like `checkout-eng@corp.internal`, whose
# 8-character final label a `{2,7}` TLD bound would miss -- leaking owner
# identities into every persisted trace.
#
# `tool@version` identifiers (`simulate_restart@1.2.0`) must survive redaction,
# since provenance depends on them; the local part is therefore required to
# contain a letter and the domain to contain a non-numeric label.
SECRET_PATTERNS = [
    (
        re.compile(
            r'(?i)(password|secret|token|api[_-]?key|authorization|bearer)\s*[:=]\s*["\']?([^"\'\s,]+)'
        ),
        r'\1="[REDACTED]"',
    ),
    (
        re.compile(
            r'\b[A-Za-z0-9._%+-]*[A-Za-z][A-Za-z0-9._%+-]*'
            r'@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\b'
        ),
        r'[REDACTED_EMAIL]',
    ),
    (re.compile(r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'), r'[REDACTED_CARD]'),
]


def redact_sensitive_data(obj: Any) -> Any:
    """Recursively scrub credentials, secrets, tokens, and PII."""
    if isinstance(obj, str):
        cleaned = obj
        for pattern, replacement in SECRET_PATTERNS:
            cleaned = pattern.sub(replacement, cleaned)
        return cleaned
    elif isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            if any(s in k.lower() for s in ["password", "secret", "token", "auth", "credential", "private_key", "key"]):
                new_dict[k] = "[REDACTED]"
            else:
                new_dict[k] = redact_sensitive_data(v)
        return new_dict
    elif isinstance(obj, list):
        return [redact_sensitive_data(item) for item in obj]
    return obj


# Bounded registry of live tracers, keyed by run id. LangGraph nodes resolve
# their tracer by run id rather than carrying the instance through serialized
# state, so a process-level registry is required. It is capped and evicted in
# FIFO order to keep long-lived services (the API) from growing without bound;
# durable history lives in the persisted trace files and the SQLite store.
MAX_LIVE_TRACERS: int = 256
_GLOBAL_TRACERS: "OrderedDict[str, AuditTracer]" = OrderedDict()


class AuditTracer:
    """
    Structured execution tracer recording Workflow -> Agent -> Step -> Tool Call.
    """

    def __init__(self, run_id: str, storage_dir: str = ".traces"):
        self.run_id = run_id
        self.storage_dir = storage_dir
        self.steps: List[TraceStep] = []
        self.a2a_messages: List[Dict[str, Any]] = []
        self.state_transitions: List[Dict[str, Any]] = []
        self.start_time = time.time()
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self._register(run_id, self)

    @staticmethod
    def _register(run_id: str, tracer: "AuditTracer") -> None:
        """Insert into the live registry, evicting the oldest entry when full."""
        _GLOBAL_TRACERS[run_id] = tracer
        _GLOBAL_TRACERS.move_to_end(run_id)
        while len(_GLOBAL_TRACERS) > MAX_LIVE_TRACERS:
            _GLOBAL_TRACERS.popitem(last=False)

    @classmethod
    def get_or_create(cls, run_id: str, storage_dir: str = ".traces") -> "AuditTracer":
        existing = _GLOBAL_TRACERS.get(run_id)
        if existing is not None:
            _GLOBAL_TRACERS.move_to_end(run_id)
            return existing
        return cls(run_id=run_id, storage_dir=storage_dir)

    @classmethod
    def get(cls, run_id: str) -> Optional["AuditTracer"]:
        tracer = _GLOBAL_TRACERS.get(run_id)
        if tracer is not None:
            _GLOBAL_TRACERS.move_to_end(run_id)
        return tracer

    @classmethod
    def release(cls, run_id: str) -> None:
        """Drop a tracer from the live registry once its run is complete."""
        _GLOBAL_TRACERS.pop(run_id, None)

    @classmethod
    def live_count(cls) -> int:
        """Number of tracers currently held in the live registry."""
        return len(_GLOBAL_TRACERS)

    def record_transition(self, from_state: str, to_state: str, trigger: str, metadata: Optional[Dict[str, Any]] = None):
        self.state_transitions.append({
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "metadata": redact_sensitive_data(metadata or {})
        })

    def record_message(self, message: A2AMessage):
        self.a2a_messages.append(redact_sensitive_data(message.to_dict()))

    def record_step(
        self,
        step_id: str,
        workflow_state: str,
        agent: str,
        action: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        latency_ms: float,
        estimated_tokens: int = 0,
        cost_usd: float = 0.0,
        decisions: Optional[List[str]] = None,
        rejected_alternatives: Optional[List[str]] = None,
        mode: str = "deterministic",
        model: Optional[str] = None,
        prompt_version: Optional[str] = None,
        validation_result: Optional[str] = None,
        usage_in: int = 0,
        usage_out: int = 0,
        llm_latency_ms: float = 0.0
    ) -> TraceStep:
        self.total_tokens += estimated_tokens
        self.total_cost_usd += cost_usd

        step = TraceStep(
            step_id=step_id,
            workflow_state=workflow_state,
            agent=agent,
            action=action,
            inputs_redacted=redact_sensitive_data(inputs),
            outputs_redacted=redact_sensitive_data(outputs),
            latency_ms=round(latency_ms, 2),
            estimated_tokens=estimated_tokens,
            estimated_cost_usd=round(cost_usd, 6),
            decisions=decisions or [],
            rejected_alternatives=rejected_alternatives or [],
            mode=mode,
            model=model,
            prompt_version=prompt_version,
            validation_result=validation_result,
            usage_in=usage_in,
            usage_out=usage_out,
            llm_latency_ms=round(llm_latency_ms, 2)
        )
        self.steps.append(step)
        return step

    def export_trace(self) -> Dict[str, Any]:
        total_latency_ms = (time.time() - self.start_time) * 1000.0
        return {
            "run_id": self.run_id,
            "total_latency_ms": round(total_latency_ms, 2),
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "step_count": len(self.steps),
            "message_count": len(self.a2a_messages),
            "state_transitions": self.state_transitions,
            "steps": [s.to_dict() for s in self.steps],
            "a2a_messages": self.a2a_messages
        }

    def persist(self) -> str:
        """Save trace JSON to disk for replayability and audit."""
        os.makedirs(self.storage_dir, exist_ok=True)
        path = os.path.join(self.storage_dir, f"trace_{self.run_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.export_trace(), f, indent=2, default=str)
        return path


class TraceReplayer:
    """
    Post-mortem trace replay and audit engine.

    Output is deliberately ASCII-only so replay works on consoles that do not
    default to UTF-8 (notably Windows cp1252), where decorative glyphs raise
    UnicodeEncodeError and hide the report.
    """

    @staticmethod
    def load_trace(path_or_json: Any) -> Dict[str, Any]:
        """Load a trace from a JSON file path, or pass through an in-memory dict."""
        if isinstance(path_or_json, str):
            with open(path_or_json, "r", encoding="utf-8") as f:
                return json.load(f)
        return path_or_json

    @staticmethod
    def replay_summary(trace_data: Dict[str, Any]) -> str:
        """Render a human-readable reconstruction of a recorded run."""
        lines: List[str] = [
            f"=== Replaying Audit Trace: {trace_data.get('run_id')} ===",
            f"Total Steps: {trace_data.get('step_count')}, "
            f"Total Latency: {trace_data.get('total_latency_ms')}ms",
            f"Tokens: {trace_data.get('total_tokens')}, "
            f"Estimated Cost: ${trace_data.get('total_cost_usd')}",
            "",
            "--- State Transitions ---",
        ]
        for st in trace_data.get("state_transitions", []):
            lines.append(
                f"  [{st['timestamp']}] {st['from_state']} -> {st['to_state']} "
                f"(via {st['trigger']})"
            )

        lines.extend(["", "--- Agent Execution Timeline ---"])
        for idx, step in enumerate(trace_data.get("steps", []), start=1):
            lines.append(
                f"  Step {idx} [{step['agent']}]: {step['action']} "
                f"(State: {step['workflow_state']}, {step['latency_ms']}ms, "
                f"{step.get('estimated_tokens', 0)} tok, "
                f"${step.get('estimated_cost_usd', 0.0)})"
            )
            # Reasoning provenance, rendered only when a model was involved, so
            # a purely deterministic trace reads exactly as it did before.
            mode = step.get("mode", "deterministic")
            if mode and mode != "deterministic":
                lines.append(
                    f"    [~] Reasoning: {mode}"
                    f" (model: {step.get('model') or 'n/a'},"
                    f" prompt: {step.get('prompt_version') or 'n/a'},"
                    f" usage: {step.get('usage_in', 0)} in / {step.get('usage_out', 0)} out,"
                    f" validation: {step.get('validation_result') or 'n/a'})"
                )
            for d in step.get("decisions", []):
                lines.append(f"    [+] Decision: {d}")
            for r in step.get("rejected_alternatives", []):
                lines.append(f"    [-] Rejected: {r}")

        lines.extend(["", "--- A2A Message Flow ---"])
        for msg in trace_data.get("a2a_messages", []):
            lines.append(
                f"  {msg.get('sender')} -> {msg.get('recipient')}: "
                f"{msg.get('message_type')} ({msg.get('message_id', '')[:8]})"
            )

        return "\n".join(lines)
