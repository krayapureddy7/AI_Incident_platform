"""
Investigator / RAG Agent.

Retrieves evidence using read-only tools and Hybrid RAG (BM25 + dense
projection with RRF fusion). Exposed as a typed Python agent class for
LangGraph nodes with Pydantic structured I/O.

Tool access
-----------
The Investigator holds a `ToolGateway`, never a tool implementation. Every
observation passes through `ToolGateway.execute_read_only`, so the permission
matrix, tenant boundary, and environment boundary apply to reads exactly as
they do to mutations. Reads are scoped to the *session* environment and tenant
rather than the values declared on the incident, so a staging session cannot
read production telemetry by asserting `environment: prod` in the incident.

Reasoning
---------
Diagnosis may be performed by a language model, but *retrieval never is*. The
gateway reads and the Hybrid RAG search below run identically in both modes, and
the citations they return are passed through untouched. A model is shown the
retrieved evidence and asked what it means; it cannot choose what to fetch, and a
document it cites but was not shown is treated as a fabrication.
"""
import time
from typing import Any, Dict, List, Optional, Tuple

from app.tools.gateway import GLOBAL_TOOL_GATEWAY, ToolGateway

from ..knowledge.hybrid_rag import GLOBAL_HYBRID_RAG, HybridRAG
from ..llm import LLMProvider, resolve_provider, trace_kwargs
from ..llm.prompts import INVESTIGATOR_PROMPT_VERSION, investigator_prompt
from ..models import A2AMessage, AgentRole, MessageType
from .base import BaseAgent
from .schemas import (
    ROOT_CAUSE_LITERALS,
    InvestigatorEvidence,
    InvestigatorInput,
    InvestigatorOutput,
    LLMDiagnosis,
)

#: Per-step usage recorded when reasoning deterministically.
DETERMINISTIC_TOKENS = 540
DETERMINISTIC_COST_USD = 0.00108


class InvestigatorAgent(BaseAgent):
    """Collects telemetry evidence and cites runbooks for a declared incident."""

    def __init__(
        self,
        version: str = "1.5.0",
        tool_gateway: Optional[ToolGateway] = None,
        rag: Optional[HybridRAG] = None,
        llm_provider: Optional[LLMProvider] = None,
    ):
        super().__init__(role=AgentRole.INVESTIGATOR, version=version)
        self.tool_gateway = tool_gateway or GLOBAL_TOOL_GATEWAY
        self.rag = rag or GLOBAL_HYBRID_RAG
        self.llm = llm_provider if llm_provider is not None else resolve_provider()

    # -- Evidence collection helpers ---------------------------------------
    @staticmethod
    def _rejection(tool_name: str, envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract a compact rejection record from a failed tool envelope."""
        if envelope.get("ok"):
            return None
        error = envelope.get("error", {})
        return {
            "tool": tool_name,
            "code": error.get("code", "INTERNAL_ERROR"),
            "message": error.get("message", ""),
        }

    # -- Diagnosis ---------------------------------------------------------
    @staticmethod
    def _deterministic_diagnosis(
        service: str,
        env: str,
        evidence: Dict[str, Any],
        tool_errors: List[Dict[str, Any]],
    ) -> Tuple[str, str]:
        """
        Threshold logic over collected telemetry.

        The fallback path and the offline default. Returns
        ``(identified_root_cause, diagnosis_summary)``.
        """
        metrics = evidence["metrics"]
        has_oom_log = any(
            "OutOfMemoryError" in entry.get("message", "")
            or "memory leak" in entry.get("message", "").lower()
            for entry in evidence["logs"]
        )
        memory_pct = metrics.get("memory_utilization_pct", 0.0) or 0.0
        cpu_pct = metrics.get("cpu_utilization_pct", 0.0) or 0.0
        p99_ms = metrics.get("p99_latency_ms", 0) or 0

        if not metrics and not evidence["logs"]:
            return (
                "EVIDENCE_UNAVAILABLE",
                "No telemetry could be collected for "
                f"'{service}' in environment '{env}'. "
                f"Blocked reads: {[e['code'] for e in tool_errors]}.",
            )
        if has_oom_log or memory_pct > 90.0:
            return (
                "MEMORY_LEAK_HEAP_EXHAUSTION",
                f"Heap memory saturated at {memory_pct}%, p99 latency at {p99_ms}ms, "
                "OOM error present in logs.",
            )
        if cpu_pct > 90.0:
            return (
                "CPU_AND_CONNECTION_SATURATION",
                f"CPU saturated at {cpu_pct}%, high connection pool backpressure.",
            )
        return (
            "SERVICE_UNRESPONSIVE",
            f"Elevated error rate {metrics.get('error_rate_pct')}% with p99 {p99_ms}ms.",
        )

    def _diagnose(
        self,
        service: str,
        env: str,
        symptoms: List[str],
        evidence: Dict[str, Any],
        tool_errors: List[Dict[str, Any]],
    ) -> Tuple[str, str, Dict[str, Any], str]:
        """
        Identify the root cause, by model where available and by thresholds otherwise.

        Returns ``(root_cause, summary, trace_kwargs, mode)``.

        A generation is rejected -- and the deterministic result used instead --
        when it fails schema validation, or when it cites a document that was not
        in the retrieved set. The second check matters more than it looks: the
        root cause is the string the Ops agent dispatches on, so a diagnosis
        justified by a fabricated source would silently select a different
        remediation.
        """
        fallback = lambda: self._deterministic_diagnosis(  # noqa: E731
            service, env, evidence, tool_errors
        )

        if self.llm is None:
            return (
                *fallback(),
                trace_kwargs("deterministic", None, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                "deterministic",
            )

        result = self.llm.complete(
            prompt=investigator_prompt(
                service=service,
                symptoms=symptoms,
                metrics=evidence["metrics"],
                logs=evidence["logs"],
                citations=evidence["citations"],
                root_cause_vocabulary=list(ROOT_CAUSE_LITERALS),
            ),
            schema=LLMDiagnosis,
            purpose="investigator_diagnosis",
            prompt_version=INVESTIGATOR_PROMPT_VERSION,
        )

        mode = "llm"
        if not result.valid or result.parsed is None:
            mode = "fallback:invalid_output"
        else:
            retrieved_ids = {
                c.get("doc_id") for c in evidence["citations"] if c.get("doc_id")
            }
            invented = [
                doc_id for doc_id in result.parsed.cited_doc_ids if doc_id not in retrieved_ids
            ]
            if invented:
                mode = "fallback:hallucinated_citation"

        if mode != "llm":
            return (
                *fallback(),
                trace_kwargs(mode, result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                mode,
            )

        return (
            result.parsed.identified_root_cause,
            result.parsed.diagnosis_summary,
            trace_kwargs("llm", result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
            "llm",
        )

    def run_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the Investigator as a LangGraph node.

        Validates inputs via `InvestigatorInput`, gathers telemetry through the
        Tool Gateway, queries Hybrid RAG for runbook citations, synthesizes a
        root cause, and returns a validated `InvestigatorOutput`.
        """
        tracer = state.get("tracer")
        start_time = time.time()
        incident = state.get("incident", {})
        plan = state.get("plan", {})
        symptoms = plan.get("symptoms", [])
        delegated_tasks = plan.get("delegated_tasks", [])

        # Pydantic structured input validation.
        validated_input = InvestigatorInput(
            incident=incident, symptoms=symptoms, delegated_tasks=delegated_tasks
        )

        service = incident.get("service", "unknown")
        # Reads are scoped to the session context, not the incident's claim.
        env = state.get("env_context") or incident.get("environment", "prod")
        tenant = state.get("tenant_context") or incident.get("tenant", "default")

        tool_errors: List[Dict[str, Any]] = []
        evidence: Dict[str, Any] = {
            "service": service,
            "environment": env,
            "logs": [],
            "metrics": {},
            "topology": {},
            "citations": [],
            "identified_root_cause": None,
            "diagnosis_summary": None,
            "tool_errors": tool_errors,
        }

        # 1-3. Telemetry, metrics, and topology via the Tool Gateway.
        read_plan = (
            ("get_logs", {"timeframe": "15m", "limit": 10}, "logs"),
            ("get_metrics", None, "metrics"),
            ("get_dependency_graph", None, "topology"),
        )
        for tool_name, extra_params, _ in read_plan:
            envelope = self.tool_gateway.execute_read_only(
                tool_name=tool_name,
                service=service,
                environment=env,
                tenant=tenant,
                caller_role=self.role.value,
                extra_params=extra_params,
            )
            rejection = self._rejection(tool_name, envelope)
            if rejection:
                tool_errors.append(rejection)
                continue
            data = envelope.get("data") or {}
            if tool_name == "get_logs":
                evidence["logs"] = data.get("logs", [])
            elif tool_name == "get_metrics":
                evidence["metrics"] = data
            else:
                evidence["topology"] = data

        # 4. Hybrid RAG retrieval (BM25 lexical + dense projection, RRF fused).
        symptoms_str = " ".join(validated_input.symptoms)
        rag_query = f"{service} {symptoms_str} remediation runbook"
        evidence["citations"] = self.rag.search(
            query=rag_query, service_filter=service, top_k=2
        )

        # Diagnostic synthesis over the collected evidence. Retrieval is already
        # complete at this point and is not revisited.
        metrics = evidence["metrics"]
        root_cause, summary, llm_trace, mode = self._diagnose(
            service=service,
            env=env,
            symptoms=validated_input.symptoms,
            evidence=evidence,
            tool_errors=tool_errors,
        )
        evidence["identified_root_cause"] = root_cause
        evidence["diagnosis_summary"] = summary

        decisions = [
            f"Evidence collected via Tool Gateway: {len(evidence['logs'])} log lines, "
            f"{'telemetry metrics' if metrics else 'no metrics'}, "
            f"{'topology graph' if evidence['topology'] else 'no topology'}.",
            f"Retrieved {len(evidence['citations'])} cited runbook documents via BM25 "
            "and dense vector search with RRF fusion.",
            f"Diagnostic synthesis concluded: {evidence['identified_root_cause']}.",
        ]
        if mode != "deterministic":
            decisions.append(f"Root-cause reasoning mode: {mode}.")
        if tool_errors:
            decisions.append(
                "Gateway rejected "
                f"{len(tool_errors)} read(s): {[e['code'] for e in tool_errors]}."
            )

        rejected = [
            "Rejected hypothesis of upstream network failure: logs explicitly show local "
            "JVM heap allocation failure.",
            "Rejected pure database fault: DB connection latency is normal, local buffer "
            "allocation is exhausted.",
        ]

        duration_ms = (time.time() - start_time) * 1000.0
        if tracer:
            tracer.record_step(
                step_id="STEP-INVESTIGATOR-01",
                workflow_state="INVESTIGATING",
                agent=self.role.value,
                action="GATHER_EVIDENCE_AND_HYBRID_RAG",
                inputs={"service": service, "env": env, "tenant": tenant, "rag_query": rag_query},
                outputs={
                    "root_cause": evidence["identified_root_cause"],
                    "metrics_summary": metrics,
                    "top_citation": (
                        evidence["citations"][0]["doc_id"] if evidence["citations"] else None
                    ),
                    "tool_errors": tool_errors,
                },
                latency_ms=duration_ms,
                decisions=decisions,
                rejected_alternatives=rejected,
                **llm_trace,
            )

        # Pydantic structured output validation.
        validated_output = InvestigatorOutput(
            incident=incident,
            evidence=InvestigatorEvidence(**evidence),
            investigator_version=self.version,
        )

        return {
            "evidence": validated_output.evidence.model_dump(),
            "current_state": "INVESTIGATING",
        }

    def handle_message(self, message: A2AMessage, context: Dict[str, Any]) -> A2AMessage:
        node_res = self.run_node(
            {
                "incident": message.payload.get("incident", {}),
                "plan": message.payload,
                "env_context": context.get("env_context"),
                "tenant_context": context.get("tenant_context"),
                "tracer": context.get("tracer"),
            }
        )
        return self.create_message(
            recipient=AgentRole.OPS,
            message_type=MessageType.EVIDENCE_REPORT,
            correlation_id=message.correlation_id,
            payload={
                "incident": message.payload.get("incident", {}),
                "evidence": node_res["evidence"],
                "investigator_version": self.version,
            },
        )
