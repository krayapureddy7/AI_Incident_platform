"""
Planner Agent: Decomposes the incident description, sets triage goals, and delegates tasks.
Exposed as a typed Python agent class for LangGraph nodes with Pydantic structured I/O.
"""
import time
from typing import Dict, Any, List
from .base import BaseAgent
from .schemas import PlannerInput, PlannerOutput, DelegatedTask
from ..models import AgentRole, A2AMessage, MessageType


class PlannerAgent(BaseAgent):
    def __init__(self, version: str = "1.2.0"):
        super().__init__(role=AgentRole.PLANNER, version=version)

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

        # Incident Decomposition Logic
        description = validated_input.description.lower()
        title = validated_input.title.lower()
        combined_text = f"{title} {description}"

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
                estimated_tokens=320,
                cost_usd=0.00064,
                decisions=decisions,
                rejected_alternatives=rejected
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

