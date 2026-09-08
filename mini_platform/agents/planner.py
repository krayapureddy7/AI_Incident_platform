"""
Planner Agent: Decomposes the incident description, sets triage goals, and delegates tasks.
Exposed as a typed Python agent class for LangGraph nodes with Pydantic structured I/O.

Triage is the one judgement here, and it may be made by a language model or by
keyword rules. Both paths converge on the same closed symptom vocabulary, and the
task plan that follows is fixed either way -- the model chooses *what is wrong*,
never *what the platform does next*.
"""
import time
from typing import Dict, Any, List, Optional, Tuple
from .base import BaseAgent
from .schemas import (
    SYMPTOM_LITERALS,
    DelegatedTask,
    LLMPlannerReasoning,
    PlannerInput,
    PlannerOutput,
)
from ..llm import LLMProvider, resolve_provider, trace_kwargs
from ..llm.prompts import PLANNER_PROMPT_VERSION, planner_prompt
from ..models import AgentRole, A2AMessage, MessageType

#: Per-step usage recorded when reasoning deterministically. These are estimates
#: that describe the trace schema, not measured spend; the LLM path replaces them
#: with figures reported by the provider.
DETERMINISTIC_TOKENS = 320
DETERMINISTIC_COST_USD = 0.00064


class PlannerAgent(BaseAgent):
    def __init__(self, version: str = "1.3.0", llm_provider: Optional[LLMProvider] = None):
        super().__init__(role=AgentRole.PLANNER, version=version)
        # Resolved at construction, never at import, and never raising: the CI
        # version gate instantiates every agent with no arguments.
        self.llm = llm_provider if llm_provider is not None else resolve_provider()

    @staticmethod
    def _deterministic_symptoms(title: str, description: str) -> List[str]:
        """
        Keyword triage over the incident text.

        This is the fallback path and the offline default. It is deliberately
        conservative: an incident phrased outside these keyword sets falls
        through to UNKNOWN_DEGRADATION rather than guessing.
        """
        combined_text = f"{title.lower()} {description.lower()}"

        symptoms = []
        if any(k in combined_text for k in ["oom", "memory", "heap", "leak"]):
            symptoms.append("MEMORY_EXHAUSTION_OR_LEAK")
        if any(k in combined_text for k in ["latency", "504", "slow", "timeout"]):
            symptoms.append("LATENCY_SPIKE_OR_TIMEOUT")
        if any(k in combined_text for k in ["500", "503", "error rate", "crash"]):
            symptoms.append("HIGH_ERROR_RATE")
        if any(k in combined_text for k in ["redis", "database", "connection", "pool"]):
            symptoms.append("RESOURCE_OR_CONNECTION_STARVATION")

        if not symptoms:
            symptoms.append("UNKNOWN_DEGRADATION")
        return symptoms

    def _triage(
        self, validated_input: PlannerInput, incident_data: Dict[str, Any]
    ) -> Tuple[List[str], Dict[str, Any], str]:
        """
        Produce the symptom list, by model where available and by rules otherwise.

        Returns ``(symptoms, trace_kwargs, mode)``. A generation that fails schema
        validation is discarded in favour of the deterministic result -- the run
        continues either way, and the trace records which happened.
        """
        if self.llm is None:
            return (
                self._deterministic_symptoms(validated_input.title, validated_input.description),
                trace_kwargs("deterministic", None, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                "deterministic",
            )

        result = self.llm.complete(
            prompt=planner_prompt(incident_data, list(SYMPTOM_LITERALS)),
            schema=LLMPlannerReasoning,
            purpose="planner_triage",
            prompt_version=PLANNER_PROMPT_VERSION,
        )
        if not result.valid or result.parsed is None:
            mode = "fallback:invalid_output"
            return (
                self._deterministic_symptoms(validated_input.title, validated_input.description),
                trace_kwargs(mode, result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                mode,
            )

        # De-duplicate while preserving order: the symptom list is joined into the
        # retrieval query, so a repeated term would skew BM25 scoring.
        seen: List[str] = []
        for symptom in result.parsed.symptoms:
            if symptom not in seen:
                seen.append(symptom)
        return seen, trace_kwargs("llm", result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD), "llm"

    def run_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute Planner agent as a LangGraph node.
        Accepts state, validates incident data via Pydantic PlannerInput,
        decomposes the incident into structured delegated tasks,
        validates output via Pydantic PlannerOutput, and updates the graph state.
        """
        tracer = state.get("tracer")
        start_time = time.time()
        incident_raw = state.get("incident", {})
        if hasattr(incident_raw, "to_dict"):
            incident_data = incident_raw.to_dict()
        elif isinstance(incident_raw, dict):
            incident_data = incident_raw
        else:
            incident_data = {
                "id": getattr(incident_raw, "id", "UNKNOWN"),
                "title": getattr(incident_raw, "title", ""),
                "description": getattr(incident_raw, "description", ""),
                "service": getattr(incident_raw, "service", "unknown"),
                "environment": getattr(incident_raw, "environment", "prod"),
                "severity": getattr(incident_raw, "severity", "SEV-1")
            }

        # Pydantic Structured Input Validation
        validated_input = PlannerInput(**incident_data)

        # Incident decomposition. The symptom list may come from a model; the
        # task plan below never does.
        symptoms, llm_trace, mode = self._triage(validated_input, incident_data)

        # Plan sub-tasks with typed DelegatedTask schema
        tasks: List[DelegatedTask] = [
            DelegatedTask(
                task_id="TASK-01-LOGS",
                assigned_to=AgentRole.INVESTIGATOR.value,
                action="FETCH_LOGS",
                service=validated_input.service,
                timeframe="15m",
                purpose="Identify stack traces, error codes, and OOM indicators."
            ),
            DelegatedTask(
                task_id="TASK-02-METRICS",
                assigned_to=AgentRole.INVESTIGATOR.value,
                action="FETCH_METRICS",
                service=validated_input.service,
                purpose="Assess CPU, memory saturation, error rate %, and p99 latency."
            ),
            DelegatedTask(
                task_id="TASK-03-RAG-RUNBOOK",
                assigned_to=AgentRole.INVESTIGATOR.value,
                action="RETRIEVE_HYBRID_KNOWLEDGE",
                service=validated_input.service,
                query=f"{validated_input.service} {' '.join(symptoms)} remediation runbook",
                service_filter=validated_input.service,
                purpose="Retrieve verified runbook procedures using BM25 and dense semantic search."
            ),
            DelegatedTask(
                task_id="TASK-04-TOPOLOGY",
                assigned_to=AgentRole.INVESTIGATOR.value,
                action="GET_DEPENDENCY_GRAPH",
                service=validated_input.service,
                purpose="Identify upstream dependencies and downstream blast-radius dependents."
            )
        ]

        decisions = [
            f"Triage complete for service '{validated_input.service}' in environment '{validated_input.environment}'.",
            f"Identified primary symptoms: {', '.join(symptoms)}.",
            f"Generated {len(tasks)} discrete investigation subtasks delegated to Investigator Agent."
        ]
        if mode != "deterministic":
            decisions.append(f"Symptom classification reasoning mode: {mode}.")
        rejected = [
            "Rejected immediate blind reboot before gathering diagnostic evidence.",
            "Rejected automated cluster-wide restart to avoid uncoordinated blast-radius cascade."
        ]

        duration_ms = (time.time() - start_time) * 1000.0
        if tracer:
            tracer.record_step(
                step_id="STEP-PLANNER-01",
                workflow_state="PLANNING",
                agent=self.role.value,
                action="DECOMPOSE_INCIDENT",
                inputs={"incident_id": validated_input.id, "service": validated_input.service, "description": validated_input.description},
                outputs={"symptoms": symptoms, "task_count": len(tasks), "delegated_tasks": [t.model_dump() for t in tasks]},
                latency_ms=duration_ms,
                decisions=decisions,
                rejected_alternatives=rejected,
                **llm_trace
            )

        # Pydantic Structured Output Validation
        validated_output = PlannerOutput(
            incident=incident_data,
            symptoms=symptoms,
            delegated_tasks=tasks,
            plan_version=self.version
        )

        return {
            "plan": validated_output.model_dump(),
            "current_state": "PLANNING"
        }

    def handle_message(self, message: A2AMessage, context: Dict[str, Any]) -> A2AMessage:
        node_res = self.run_node({
            "incident": message.payload.get("incident", {}),
            "tracer": context.get("tracer")
        })
        plan = node_res["plan"]
        return self.create_message(
            recipient=AgentRole.INVESTIGATOR,
            message_type=MessageType.TASK_DELEGATION,
            correlation_id=message.correlation_id,
            payload=plan
        )

