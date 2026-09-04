# Trade-Offs: What Was Simplified, What Production Needs

This document states what the prototype does *not* do, and what hardening each
gap would require. It is deliberately specific about failure modes rather than
listing aspirational replacements.

---

## 1. The largest trade-off: the agents are deterministic, not LLM-driven

**What the prototype does.** All four agents are rule-based Python. Symptom
extraction is keyword matching; root-cause synthesis is threshold logic on
metrics and log content; remediation selection is a branch on the identified root
cause; the safety verdict is a deterministic policy matrix.

**Why.** For a system authorized to mutate production, an inspectable and
reproducible decision path was worth more than natural-language flexibility. It
also makes the eval gate meaningful: an identical incident always produces an
identical verdict, so a failing scenario is a real regression rather than
sampling noise.

**What this costs — stated plainly.**

| Limitation | Consequence |
| :--- | :--- |
| Keyword symptom extraction | An incident phrased outside the keyword sets falls through to `UNKNOWN_DEGRADATION` |
| Fixed remediation branches | Only restart and scale are reachable; a novel remediation cannot be synthesized |
| No natural-language synthesis | Reasoning strings are templated, not genuinely explanatory |
| Token/cost figures | Fixed per-step estimates, **not** measured usage — they demonstrate the trace schema, not real spend |
| Hashed dense retrieval | Captures term co-occurrence, not semantic similarity; a paraphrased query without shared terms will not match |

**Hardening.** Introduce an LLM into the Planner, Investigator, and Ops agents
only, keeping three properties that already hold:

1. Every agent output is validated against its Pydantic schema before entering
   graph state; a malformed generation fails the node rather than propagating.
2. Control flow branches only on fields produced by the deterministic safety
   engine, so no generated text can route the workflow.
3. The Verifier and Tool Gateway stay rule-based. **An LLM must never be on the
   authorization path** — a prompt-injected log line must not be able to talk its
   way past a blast-radius limit.

Then add generation-specific evals: schema-conformance rate, citation
groundedness (does the cited `doc_id` actually support the proposal?), and
adversarial prompt-injection scenarios seeded into log content.

---

## 2. Component trade-offs

| Component | Prototype | Production target | Why simplified |
| :--- | :--- | :--- | :--- |
| **Orchestration** | LangGraph `StateGraph`, single process, SQLite checkpoints | Temporal or distributed LangGraph workers with a shared event log | Declarative state machine with real checkpoint durability, no cluster dependency |
| **Tool plane** | FastMCP, in-process by default; optional local SSE container | Remote MCP over mTLS with SPIFFE/SPIRE workload identity and per-tool network RBAC | Schema-first typed contracts without transport complexity; the Gateway remains the boundary either way |
| **Retrieval** | Okapi BM25 + 256-dim hashed projection + RRF | Managed hybrid vector store (Qdrant/Vertex) with a neural encoder and cross-encoder reranking | Fully offline, deterministic, zero model downloads |
| **Knowledge graph** | In-memory directed graph, BFS | Neo4j/Neptune fed by live service-mesh telemetry (Cilium/Istio/eBPF) | Static topology is adequate when declared dependencies are complete |
| **Safety engine** | Deterministic Python policy matrix | Policy-as-code (OPA/Rego) with admission-controller enforcement | Zero-latency, fully inspectable, no policy-engine deployment |
| **Persistence** | SQLite (runs, traces, checkpoints) | Postgres with read replicas; traces to object storage | Single-node durability with no service to operate |
| **Audit integrity** | Redacted JSON + SQLite rows | Hash-chained ledger (Hₙ = SHA256(Hₙ₋₁ ‖ stepₙ)), KMS-signed, WORM storage | Demonstrates replayability without signing infrastructure |
| **Human approval** | Token with required prefix and non-empty approver segment | WebAuthn/FIDO2 dual-approval with time-bounded leases | Shows the gate mechanism, not a real identity system |
| **Simulated infrastructure** | In-memory cluster with modelled effects | Real Kubernetes/cloud APIs behind progressive delivery | Safe to run anywhere, deterministic for evals |

---

## 3. Specific residual weaknesses

### 3.1 Approval tokens are not real credentials

`TOKEN-HUMAN-APPROVED-<id>` is validated on prefix and non-emptiness only. It is
not signed, not bound to an identity, not scoped to a specific action, and does
not expire. Anyone able to call the API can mint one.

**Harden with:** signed, single-use tokens bound to `(run_id, action_id,
approver)`, a short TTL, two distinct approvers for tier-0 actions, and rejection
of a token whose bound action differs from the one being executed.

### 3.2 Blast radius trusts declared dependencies

The graph is hand-declared. A service calling an undeclared endpoint is invisible,
so blast radius is *under*-estimated — the dangerous direction, since it can
lower a Tier-3 action to Tier-2.

**Harden with:** eBPF/service-mesh discovery of actual socket connections over a
recent window, union'd with declared topology, plus QPS weighting so a 5000 RPS
dependent outranks a 2 RPS one. Fail closed when discovery is unavailable.

### 3.3 Idempotency is in-process and unbounded

The idempotency store is a per-gateway dict with no TTL. It is lost on restart
(so a duplicate could execute after a crash) and grows without bound.

**Harden with:** Redis with a TTL matched to the action's blast-radius decay,
plus a persisted execution-intent record written *before* dispatch so a crash
mid-call is recoverable.

### 3.4 Node timeouts cannot kill a hung thread

Python cannot terminate a thread. The timeout abandons a stalled node so the
workflow proceeds, but the worker lingers until it returns on its own.

**Harden with:** process-level isolation (a subprocess or worker pool) with hard
kill, and cancellation tokens propagated into tool calls.

### 3.5 Recovery verification is immediate and single-shot

The healthcheck runs once, right after the mutation. A real remediation can look
healthy for thirty seconds and then crashloop.

**Harden with:** a sustained observation window (e.g. error rate under threshold
for two continuous minutes), and canary-first rollout via Argo Rollouts or
Flagger with automatic abort — remediate 10% of pods, observe, then proceed.

### 3.6 No compensating action on failed recovery

When recovery verification fails, the workflow terminates as `FAILED`. It does not
undo the mutation.

**Harden with:** the Saga pattern — pair every mutating tool with a compensating
action (`simulate_restart` → roll back to the previous deployment revision) and
trigger it deterministically on verification failure.

### 3.7 Single-tenant process boundary

Tenant isolation is enforced by comparing string fields. One process serves all
tenants, and a bug in the comparison is a cross-tenant breach.

**Harden with:** per-tenant worker pools and credentials, tenant-scoped database
schemas, and tenancy asserted from an authenticated principal rather than a
request field.

### 3.8 No authentication on the API

Every endpoint is unauthenticated. Anyone who can reach the port can remediate
production.

**Harden with:** OIDC at an API gateway, RBAC mapping principals to permitted
services and maximum autonomy tier, and audit records carrying the authenticated
caller.

### 3.9 The frontend is a separate re-implementation

`src/` is an independent TypeScript re-implementation for visual exploration. It
does not call the Python backend, so its logic can drift from the platform's and
is not covered by the Python tests or the eval gate.

**Harden with:** delete the duplicated engine and have the UI consume the HTTP
API, so there is one implementation of record.

---

## 4. What the prototype does get right

These are load-bearing properties, verified by tests rather than asserted:

- **One authorization boundary.** Every tool call, reads included, passes through
  the Tool Gateway. No agent holds a tool handle, a FastMCP server, or an SDK
  client. `tests/test_fastmcp_contracts.py::TestAgentPermissionMatrix` asserts
  that no role can escalate through either gateway entry point.
- **Rejections never reach the tool layer.** `mutating_call_count` stays at zero
  across every rejection path, asserted per-path and by the eval gate.
- **Deterministic control flow.** Routing reads only safety-engine output and
  tool envelopes.
- **Reads are session-scoped.** A staging session cannot read production
  telemetry by declaring `environment: prod`.
- **Recovery verification can actually fail.** Five distinct failure modes are
  tested, including unreadable telemetry.
- **Durable, replayable audit.** Traces survive process death and replay without
  the original cluster.
- **Resumable human approval.** A Tier-3 hold resumes in a different process.
- **Enforced versioning.** CI fails on version drift, so every release has a
  named rollback target.

---

## 5. Rollback

Agents, tools, and safety policy are independently versioned artifacts
([`VERSION.json`](./VERSION.json)). `tools/check_versions.py` fails CI when a
declared version drifts from the code, so a behaviour change cannot ship without
a version bump.

To roll back: read the offending component and version from the audit trace
(`selected_tool_version` and the `*_version` fields in the A2A envelopes), revert
that component to its previous tag, re-run the eval gate, and restore the
matching `VERSION.json` entry. A2A and tool envelopes are additive within a major
version — fields are never removed or retyped — so peer components need no
coordinated change.
