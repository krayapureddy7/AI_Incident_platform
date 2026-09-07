# Architecture

Components, data flow, and control flow of the Mini Agentic AI Platform.

---

## 1. System overview

```
                    ┌─────────────────────────────────────────────┐
                    │        Incident Ingestion (CLI / API)       │
                    │  text, service, environment, tenant, sev    │
                    └─────────────────────────────────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                    LANGGRAPH STATEGRAPH ORCHESTRATOR                          │
│                                                                               │
│  IDLE → TRIAGE → PLANNING → INVESTIGATING → PROPOSING → SAFETY_VERIFICATION   │
│                                                    │                          │
│              ┌─────────────────────────────────────┼──────────────┐           │
│              ▼ approved             requires_human ▼      violation▼          │
│          EXECUTING              AWAITING_APPROVAL         FAILED              │
│              │                        │ (+ human token)                       │
│              ▼                        └──→ re-enters SAFETY_VERIFICATION      │
│      VERIFYING_RECOVERY                                                       │
│              │                                                                │
│      ┌───────┴────────┐                                                       │
│      ▼                ▼                                                       │
│  COMPLETED         FAILED                                                     │
│                                                                               │
│  Every node wrapped in a timeout; read/compute nodes also carry a retry       │
│  budget. The executor is never auto-retried.                                  │
└───────────────────────────────────────────────────────────────────────────────┘
        │              │               │                │
        ▼              ▼               ▼                ▼
   ┌─────────┐   ┌──────────────┐  ┌─────────┐   ┌──────────────┐
   │ Planner │   │ Investigator │  │   Ops   │   │   Verifier   │
   │ no tools│   │  read-only   │  │ propose │   │  adjudicate  │
   └─────────┘   └──────────────┘  └─────────┘   └──────────────┘
        │              │               │                │
        └──────────────┴───────┬───────┴────────────────┘
              typed Pydantic A2AMessage envelopes
                               │
                               ▼
                   ┌───────────────────────┐        ┌──────────────────┐
                   │     TOOL GATEWAY      │◄───────│  Safety Engine   │
                   │  authorization        │        │  policy, tiers,  │
                   │  boundary (8 checks)  │        │  blast radius    │
                   └───────────────────────┘        └──────────────────┘
                               │                             │
                               ▼                             ▼
                   ┌───────────────────────┐        ┌──────────────────┐
                   │      MCP Client       │        │ Knowledge Graph  │
                   └───────────────────────┘        │ services→deps→   │
                               │                    │ owners→runbooks  │
                               ▼                    └──────────────────┘
                   ┌───────────────────────┐
                   │    FastMCP Server     │
                   │  5 versioned tools    │
                   └───────────────────────┘
                               │
                               ▼
                   ┌───────────────────────┐
                   │ Simulated Cluster     │
                   │ (one per orchestrator)│
                   └───────────────────────┘
```

---

## 2. Agents and A2A messaging

No agent both diagnoses and executes. Separation of concerns is enforced by the
permission matrix in [`app/tools/contracts.py`](app/tools/contracts.py), not by
convention.

| Agent | Version | Responsibility | Inbound | Outbound | Tool access |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Planner** | 1.2.0 | Decomposes incident text into symptoms and discrete diagnostic tasks | `TASK_DELEGATION` | `TASK_DELEGATION` | **none** |
| **Investigator** | 1.4.0 | Gathers telemetry via the gateway; hybrid-RAG runbook citations | `TASK_DELEGATION` | `EVIDENCE_REPORT` | read-only, session-scoped |
| **Ops** | 1.3.0 | Synthesizes an `ActionProposal` with rejected alternatives and KG blast radius | `EVIDENCE_REPORT` | `ACTION_PROPOSAL` | **none** |
| **Verifier** | 1.4.0 | Deterministic policy, blast-radius, tier, and boundary adjudication | `ACTION_PROPOSAL` | `SAFETY_DECISION` | **none** |

Agents communicate only through validated `A2AMessage` envelopes — never
free-form text:

```json
{
  "message_id": "uuid4",
  "correlation_id": "RUN-3B00B72E",
  "sender": "planner",
  "recipient": "investigator",
  "message_type": "TASK_DELEGATION",
  "timestamp": "2026-09-04T14:34:47Z",
  "payload": {
    "incident": { "...": "..." },
    "symptoms": ["MEMORY_EXHAUSTION_OR_LEAK"],
    "delegated_tasks": [{ "task_id": "TASK-01-LOGS", "action": "FETCH_LOGS" }],
    "plan_version": "1.2.0"
  }
}
```

Each envelope carries the producing agent's version, so a trace attributes every
decision to an exact artifact version.

### LangGraph nodes

| Node | Implementation | Input schema | Output schema | Retryable |
| :--- | :--- | :--- | :--- | :--- |
| `triage` | orchestrator | state | state | yes |
| `planner` | `PlannerAgent` | `PlannerInput` | `PlannerOutput` | yes |
| `investigator` | `InvestigatorAgent` | `InvestigatorInput` | `InvestigatorOutput` | yes |
| `ops` | `OpsAgent` | `OpsInput` | `OpsOutput` | yes |
| `verifier` | `VerifierAgent` | `VerifierInput` | `VerifierOutput` | yes |
| `executor` | `ToolGateway` | state | tool envelope | **no** (mutating) |
| `verify_recovery` | orchestrator | state | recovery comparison | yes |
| `hold_for_approval` | orchestrator | state | terminal status | no |
| `policy_rejected` | orchestrator | state | terminal status | no |
| `execution_failed` | orchestrator | state | terminal status | no |
| `recovery_failed` | orchestrator | state | terminal status | no |

---

## 3. Control-flow determinism

Routing reads **only** structured fields produced by the deterministic safety
engine or by tool envelopes:

```python
def safety_router(state):
    safety = state["safety_result"]        # from SafetyGuardrails
    if safety["approved"]:              return "executor"
    if safety["requires_human_token"]:  return "hold_for_approval"
    return "policy_rejected"

def execution_router(state):
    return "verify_recovery" if state["execution_result"]["ok"] else "execution_failed"
```

No agent narrative influences a transition. Were an LLM introduced into the
Planner, Investigator, or Ops agent, its output would still be schema-validated
and would still reach the executor only via a Verifier verdict.

---

## 4. Tool plane

### Authorization pipeline

`ToolGateway.execute_proposal` applies eight checks in order. A failure at any
step returns a structured error envelope and the tool layer is never reached.

| # | Check | Failure code |
| :--- | :--- | :--- |
| 1 | Tool is registered | `TOOL_NOT_FOUND` |
| 2 | Agent permission matrix (deny-by-default) | `AUTONOMY_LIMIT` / `POLICY_DENIED` |
| 3 | Pydantic contract validation | `INVALID_ARGUMENT` |
| 4 | Tenant boundary | `TENANT_MISMATCH` |
| 5 | Environment boundary | `ENVIRONMENT_MISMATCH` |
| 6 | Policy, blast radius, autonomy tier | `POLICY_DENIED` / `BLAST_RADIUS_EXCEEDED` / `AUTONOMY_LIMIT` |
| 7 | Human approval gate (Tier-3) | `AUTONOMY_LIMIT` |
| 8 | Idempotency deduplication | `ALREADY_EXECUTED` |

`execute_read_only` routes observation through the same pipeline, with a guard
rejecting any mutating tool name before it is even proposed.

> **Boundary responsibility.** FastMCP *serves* tools with typed schemas and
> structured envelopes. It is not the safety layer, and the tool implementations
> deliberately do not duplicate authorization. The Tool Gateway is the single
> boundary — which is why the standalone MCP container is never published to the
> host.

### Tool contracts

| Tool | Version | Mode | Description |
| :--- | :--- | :--- | :--- |
| `get_logs` | 1.1.0 | read | Redacted application/system logs for a timeframe |
| `get_metrics` | 1.0.0 | read | CPU, memory, error rate, p99, connections |
| `get_dependency_graph` | 1.0.0 | read | Upstream/downstream topology, owner, tier |
| `simulate_restart` | 1.2.0 | mutate | Controlled rolling restart |
| `simulate_scale` | 1.2.0 | mutate | Horizontal replica scaling |

**Success envelope**

```json
{ "ok": true, "data": { "...": "..." },
  "metadata": { "tool": "get_metrics", "version": "1.0.0", "fastmcp_invoked": true } }
```

**Error envelope**

```json
{ "ok": false,
  "error": { "code": "ENVIRONMENT_MISMATCH", "message": "...", "retryable": false,
             "details": { "...": "..." } },
  "metadata": { "tool": "simulate_restart", "version": "1.2.0", "fastmcp_invoked": false } }
```

Error codes: `INVALID_ARGUMENT`, `MISSING_REQUIRED_PARAMETER`, `TENANT_MISMATCH`,
`ENVIRONMENT_MISMATCH`, `POLICY_DENIED`, `BLAST_RADIUS_EXCEEDED`,
`AUTONOMY_LIMIT`, `SERVICE_NOT_FOUND`, `TOOL_NOT_FOUND`,
`SCALE_CEILING_EXCEEDED`, `TIMEOUT`, `RATE_LIMITED`, `ALREADY_EXECUTED`,
`INTERNAL_ERROR`.

### Single source of infrastructure state

Each orchestrator owns one `InfraToolServer` bound to one cluster, and one
gateway in front of it. Reads, mutations, and the post-action healthcheck all
observe the same cluster, so no state synchronization is needed anywhere.
Test and evaluation isolation comes from constructing separate stacks
(`tests/conftest.py: build_isolated_stack`), not from resetting shared globals.

---

## 5. Knowledge and retrieval

### Hybrid RAG

```
query ──┬──► BM25 (Okapi, k1=1.5, b=0.75) ──► ranked list ──┐
        │                                                   ├──► RRF (k=60) ──► metadata filters ──► top-k with citations
        └──► dense projection (256-dim hashed, IDF) ────────┘
```

The dense ranker hashes unigrams and bigrams into a fixed 256-dim space with IDF
weighting and L2 normalization, then scores by cosine similarity. It is
deterministic and dependency-free. It captures term co-occurrence but **not**
true semantic similarity — a neural encoder is the production substitution.

Metadata filters (`service`, `env`, `type`) are applied after fusion. Every
result carries `doc_id`, `rrf_score`, `bm25_score`, `dense_score`, and a
citation snippet, and the Ops agent grounds its reasoning in the returned
`doc_id`.

### Knowledge graph

Directed graph of `services → dependencies → dependents`, with `owner`, `tier`,
and `runbook_id` per node. `calculate_blast_radius` runs a depth-bounded BFS over
dependents and returns direct/transitive dependents, total affected count,
tier-0 impact, and a risk rating. The Verifier uses this for tier classification;
the Ops agent uses it to state an estimated blast radius, which the Verifier
independently recomputes rather than trusting.

---

## 6. Safety engine

Deterministic checks in `SafetyGuardrails.evaluate_proposal`:

1. **Environment isolation** — proposal target vs session authority.
2. **Tenant isolation** — proposal tenant vs session authority.
3. **Blast-radius assessment** — KG BFS traversal.
4. **Tool invariants** — replica bounds against `MAX_REPLICA_CEILING` (10); the
   effective limit is the stricter of the platform ceiling and the service's own
   `max_replicas`.
5. **Autonomy tier classification** — see below.
6. **Human approval gate** - Tier-3 requires an HMAC-signed token whose payload
   is bound to this exact action (action id, tool, service, environment, tenant,
   and a hash of the parameters) and is still inside its validity window. The
   token prefix is a public constant and carries no authority on its own, so an
   approval can neither be forged from the format nor replayed onto a different
   action. See `mini_platform/safety/approval.py`.

### Tier rules

| Condition | Tier |
| :--- | :--- |
| Read-only tool | 1 |
| Tier-0 service, tier-0 impacted, >2 direct dependents, or >4 total affected | 3 |
| `simulate_restart` on non-tier-0 with ≤2 direct dependents | 2 |
| `simulate_scale` with ≤6 replicas and ≤2 direct dependents | 2 |
| anything else | 3 |

Boundary violations are **hard rejections**: `requires_human_token` is false, so
no token can approve a cross-environment or cross-tenant action.

---

## 7. Recovery verification

After a mutation the workflow re-reads metrics *and* logs through the gateway and
compares against the pre-action baseline. Recovery passes only if **all** hold:

- telemetry was actually readable (an unreadable check verifies nothing),
- status is `HEALTHY`,
- memory ≤ 75%,
- error rate ≤ 1%,
- no `FATAL`/`CRITICAL` log lines and no `OOMKilled`.

Both reads use registered tool names; an unregistered name would surface as
`TOOL_NOT_FOUND` rather than silently returning empty data and passing the gate
vacuously. `tests/test_recovery_verification.py` asserts each failure mode.

---

## 8. Observability

```
Workflow (run_id)
  └── State transitions (from, to, trigger, redacted metadata)
  └── Agent steps
        ├── redacted inputs / outputs
        ├── latency, token and cost estimates
        ├── decisions taken
        └── alternatives rejected
  └── Tool calls (versioned: tool_name@version)
  └── A2A envelopes (sender, recipient, type, correlation_id)
  └── Retry / timeout events (node, attempt, error, will_retry)
```

Redaction runs before anything is written. Traces persist to
`.traces/trace_<run_id>.json` and to SQLite, and replay needs neither the
original process nor the original cluster.

The live tracer registry is bounded (FIFO, 256 entries) so a long-lived API
process does not accumulate tracers; durable history lives in the trace files and
the database.

---

## 9. Persistence

| Store | Backend | Contents | Durability |
| :--- | :--- | :--- | :--- |
| Run catalogue | SQLite `incident_runs` | incident, scope, outcome, proposal, verdict | across restarts |
| Audit traces | SQLite `audit_traces` + JSON files | full redacted trace | across restarts |
| Workflow checkpoints | SQLite via `langgraph-checkpoint-sqlite` | graph position and state | across restarts |

Durable checkpoints are what make human-in-the-loop approval practical: the
engineer approving a Tier-3 action is rarely in the process that proposed it.
`tests/test_persistence.py::TestDurableCheckpointing` asserts a held run resumes
in a freshly constructed orchestrator.

---

## 10. Production evolution

**Current (single node, offline)**

```
CLI / FastAPI → LangGraph (SQLite checkpoints) → Tool Gateway → FastMCP → simulated cluster
```

**Hardened**

```
API Gateway / IAM (OIDC)
  → Incident Service
  → Event bus (Kafka / NATS)
  → Distributed workers (Temporal)
  → Tool Gateway
  → Policy-as-code (OPA / Gatekeeper)
  → Remote MCP tool plane (mTLS / SPIFFE)
  → Kubernetes / cloud APIs
```

See [`TRADE_OFFS.md`](./TRADE_OFFS.md) for the reasoning behind each
simplification and what hardening each would require.
