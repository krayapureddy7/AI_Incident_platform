# Mini Agentic AI Platform — Incident Remediation

A multi-agent system that analyzes a production incident, plans remediation,
validates blast radius against policy, executes or withholds the action, and
produces a replayable audit trace.

Everything runs locally and offline by default. With no configuration there are
no API keys, no external services, and no network calls.

---

## What this is, precisely

**LLM = reasoning and proposals. Deterministic code = workflow control, safety,
authorization, and execution.**

The Planner, Investigator and Ops agents reason. When a model provider is
configured they use it: to classify an incident into symptoms, to diagnose a root
cause from retrieved evidence, and to propose a remediation. When one is not,
they fall back to the rule-based logic that has always been here. Either way the
result is validated against a Pydantic schema before it enters graph state.

Everything the platform actually *does* is deterministic Python, and stays that
way:

| Concern | Decided by |
| :--- | :--- |
| Symptoms, root cause, proposed action, risk narrative | LLM, where configured |
| Approve / reject / hold for a human | Deterministic safety engine, always |
| Autonomy tier and blast radius | Knowledge-graph traversal, always |
| Which node runs next | LangGraph edges reading engine output, always |
| Whether a tool may be called | Tool Gateway permission matrix, always |
| Retrieval ranking | BM25 + dense + RRF, always |

Three properties hold in both modes, and the tests in
[`tests/test_llm_agents.py`](tests/test_llm_agents.py) assert them against a
model that is actively trying to misbehave:

1. **A generation is data, never an instruction.** Every agent output is schema
   validated; a malformed one is discarded and the deterministic path runs
   instead, so the incident is still remediated.
2. **Nothing generated can route the workflow.** Branching reads only
   `safety_result.approved`, `requires_human_token`, and tool envelopes.
3. **The authorization path has no model on it.** A tool the model invented, a
   service it made up, a citation it fabricated, or a log line telling it to
   ignore policy all fail before dispatch -- and a scale beyond the replica
   ceiling is still rejected by the engine, whatever the proposal argued.

The trade-off this introduces is stated plainly in
[`TRADE_OFFS.md`](./TRADE_OFFS.md) §1: live reasoning is non-deterministic, so
the evaluation gate runs offline against a deterministic mock, and a live run is
*not* covered by it.

### Choosing a reasoning backend

```bash
# Deterministic reasoning (the default: no key, no network)
python -m mini_platform.demo

# Same code path as a live model, with canned offline responses. CI uses this.
LLM_PROVIDER=mock python -m mini_platform.demo

# Live reasoning
pip install -e ".[llm]"
export LLM_PROVIDER=gemini GEMINI_API_KEY=...     # or groq / GROQ_API_KEY
python -m mini_platform.demo
```

A provider named without its key, or without its SDK installed, resolves back to
deterministic reasoning rather than failing the run. Every trace step records
which mode produced it (`mode`, `model`, `prompt_version`, `validation_result`,
`usage_in`/`usage_out`), so a decision is always attributable.

---

## Technology stack

| Concern | Implementation |
| :--- | :--- |
| Orchestration | **LangGraph** `StateGraph` — explicit states, deterministic transitions |
| Durable checkpoints | **langgraph-checkpoint-sqlite** — held runs resume across processes |
| Tool serving | **FastMCP** — schema-first, versioned, typed tools |
| Contracts | **Pydantic v2** — agent I/O, A2A envelopes, tool proposals |
| API | **FastAPI** + **uvicorn** |
| Lexical retrieval | Okapi **BM25** (standard library) |
| Semantic retrieval | 256-dim hashed term/bigram projection with IDF weighting, cosine similarity |
| Fusion | **Reciprocal Rank Fusion** (k=60) |
| Knowledge graph | In-memory directed graph, BFS blast-radius traversal |
| Persistence | **SQLite** (standard library) — run catalogue + audit traces |
| Reasoning | **Gemini** / **Groq** behind an `LLMProvider` interface; deterministic fallback and offline mock |
| Tests / eval | **pytest** — 191 tests, 8-scenario eval gate |
| CI | **GitHub Actions** — tests → eval gate → version manifest check |
| Containers | **Docker** / docker-compose |

Retrieval, the knowledge graph, and persistence are implemented against the
standard library. The dense ranker is a deterministic hashed projection, **not**
a neural embedding model — it needs no model download and runs identically
offline, at the cost of true semantic generalization.
[`TRADE_OFFS.md`](./TRADE_OFFS.md) lists the managed equivalents production
would adopt.

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 1. End-to-end demo

Shows planning, retrieval, blast radius, verification, action, and trace review.
Every value printed is read back from the actual run.

```bash
python -m mini_platform.demo
```

### 2. Evaluation gate

Eight offline scenarios covering remediation trajectory, Tier-3 human gates,
cross-environment and cross-tenant isolation, blast-radius governance, and
idempotency. **Exits non-zero on any failure**, so it works as a CI gate.

```bash
python -m mini_platform.evals.eval_runner
```

### 3. Test suite

```bash
pytest tests/ -q
```

### 4. Full local CI pipeline

```bash
./run_ci.sh
```

---

## CLI

```bash
# Remediate an incident (Tier-2: executes autonomously after verification)
python -m mini_platform.cli run --service payment-service \
  --desc "Alert: high heap OOM and latency spike"

# Tier-0 service: halts at AWAITING_APPROVAL without executing
python -m mini_platform.cli run --service auth-service \
  --desc "Redis timeout, CPU 94%" --run-id INC-42

# Supply human approval and resume. Works from a separate process,
# because checkpoints are durable.
# The token is signed and bound to the held action, so --approver mints one
# against the proposal the run is actually waiting on. Set
# APPROVAL_SIGNING_SECRET when the approver runs in a different process.
python -m mini_platform.cli approve --run-id INC-42 --approver sre-001

# Or present a token minted elsewhere
python -m mini_platform.cli approve --run-id INC-42 --token "$APPROVAL_TOKEN"

# Demonstrate cross-environment rejection: target prod from a staging session
python -m mini_platform.cli run --service payment-service \
  --desc "test" --env prod --env-context staging

# Hybrid retrieval (BM25 + dense projection, RRF fused)
python -m mini_platform.cli rag --query "payment memory leak rolling restart"

# Knowledge-graph blast radius
python -m mini_platform.cli blast-radius --service payment-service

# Versioned tool catalog with JSON-Schema contracts
python -m mini_platform.cli tools

# List persisted runs, and replay one by id
python -m mini_platform.cli runs
python -m mini_platform.cli replay --run-id INC-42

# Start the HTTP API
python -m mini_platform.cli serve --port 8000
```

---

## HTTP API

```bash
uvicorn mini_platform.api.server:app --port 8000
# Interactive docs: http://localhost:8000/docs
```

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/health` | Liveness and persistence reachability |
| `GET` | `/tools` | Versioned tool catalog with JSON-Schema contracts |
| `POST` | `/incidents` | Submit an incident; runs the full workflow |
| `POST` | `/incidents/{run_id}/approve` | Supply a Tier-3 token and resume |
| `GET` | `/incidents` | List persisted runs |
| `GET` | `/incidents/{run_id}` | Fetch a persisted run record |
| `GET` | `/incidents/{run_id}/state` | Live LangGraph checkpoint |
| `GET` | `/traces/{run_id}` | Redacted audit trace |
| `GET` | `/traces/{run_id}/replay` | Human-readable post-mortem replay |
| `POST` | `/knowledge/search` | Hybrid retrieval with metadata filters |
| `GET` | `/services/{service}/blast-radius` | Blast-radius assessment |
| `GET` | `/services` | Service topology as nodes and edges |
| `POST` | `/tools/{name}/invoke` | Dispatch one read-only tool; 403 for mutating tools |
| `POST` | `/safety/preview` | Evaluate a hypothetical action; read-only, never dispatches |
| `POST` | `/safety/redact-preview` | Run a payload through the trace redactor |
| `GET` | `/eval/scenarios` / `POST` `/eval/run` | Benchmark catalog and evaluation gate |

The API enforces no policy of its own — it delegates to the same orchestrator
and Tool Gateway as the CLI.

---

## Safety model

**Every tool call goes through the Tool Gateway — reads included.** No agent holds
a reference to a tool implementation, a FastMCP server, or an infrastructure SDK.

```
Agent
  ↓
Tool Gateway  ← the mandatory authorization boundary
  ↓  1. tool registered?
  ↓  2. agent permission matrix (deny-by-default)
  ↓  3. Pydantic contract validation
  ↓  4. tenant boundary
  ↓  5. environment boundary
  ↓  6. deterministic policy + blast radius + autonomy tier
  ↓  7. human approval gate (Tier-3)
  ↓  8. idempotency deduplication
  ↓
MCP Client → FastMCP Server → Simulated Infrastructure
```

If any check fails, the tool layer is never reached. `ToolGateway` exposes
`mutating_call_count`, which the contract tests and the eval gate assert stays at
zero for every rejected or held action.

### Agent permission matrix

Declared once in [`app/tools/contracts.py`](app/tools/contracts.py) and enforced
by the gateway:

| Agent | Permitted tools |
| :--- | :--- |
| Planner | *none* |
| Investigator | `get_logs`, `get_metrics`, `get_dependency_graph` |
| Ops | *none* — proposes only, no execution authority |
| Verifier | *none* — adjudicates only |
| Orchestrator | all, and only after a Verifier approval |
| *unknown role* | *none* — deny by default |

### Autonomy tiers

| Tier | Scope | Behaviour |
| :--- | :--- | :--- |
| 1 | Read-only observation | Executes after boundary checks |
| 2 | Low blast radius, non-tier-0 | Executes after Verifier approval |
| 3 | Tier-0 service, tier-0 impact, >2 direct dependents, or >4 total affected | **Halts** for a human token |

Cross-environment and cross-tenant attempts are *hard rejections*, not
escalations — no token can approve them.

### Read scoping

The Investigator scopes reads to the **session** environment and tenant, not the
values declared on the incident. A staging session cannot read production
telemetry by asserting `environment: prod`. Rejected reads are recorded in
`evidence.tool_errors` and surfaced in the trace, so a gap in the evidence is
visible rather than silent. Where evidence cannot be gathered, the Ops agent
proposes a read-only re-check instead of an unjustified mutation.

---

## Observability

Traces capture `Workflow → Agent → Step → Tool Call` with redacted inputs and
outputs, per-step latency, token/cost estimates, decisions taken, alternatives
rejected, retry and timeout events, and every A2A envelope.

Traces are written to `.traces/trace_<run_id>.json` and to SQLite, and are
replayable without the originating process:

```bash
python -m mini_platform.cli replay --run-id RUN-XXXXXXXX
```

Redaction covers secret-like keys, emails, and card numbers before anything is
written to disk.

---

## Resilience

Every graph node runs under a timeout and, where safe, a retry budget.

- **Retryable**: triage, planner, investigator, ops, verifier, verify_recovery.
- **Never retried**: the executor. Re-issuing a mutation whose outcome is unknown
  is unsafe, so it gets a timeout only. The gateway's idempotency window is a
  second line of defence, not a licence to retry.

Only exceptions trigger a retry. A deterministic policy rejection is a normal
return value, so a Verifier decision is never retried into a different outcome.
An exhausted budget terminates the workflow as `ORCHESTRATION_FAILED` with the
failing node named, and the trace is still persisted.

---

## Docker

```bash
docker compose up --build
```

- **api** (`:8000`) — FastAPI, LangGraph, Tool Gateway.
- **mcp-server** — FastMCP tool plane. **Deliberately not published to the host**:
  it performs no authorization, so only the API may reach it.
- **shared-data** volume — SQLite run catalogue and workflow checkpoints.

---

## Repository layout

```
app/tools/            Tool plane
  contracts.py          Single source of truth: versions, permissions, ceilings
  server.py             InfraToolServer implementations + FastMCP factory
  client.py             MCP client (the only MCP speaker)
  gateway.py            Tool Gateway — the authorization boundary
  serve_mcp.py          Standalone MCP server entry point
mini_platform/
  agents/               Planner, Investigator, Ops, Verifier + Pydantic schemas
  llm/                  Provider interface, Gemini/Groq adapters, offline mock,
                        versioned prompts
  orchestrator/         LangGraph graph, resilience guards, checkpointing
  knowledge/            Hybrid RAG, knowledge graph, corpus
  safety/               Deterministic guardrails and autonomy tiers
  tracing/              Audit tracer, redaction, replay
  persistence/          SQLite run catalogue and trace store
  evals/                Benchmark scenarios and the eval gate
  api/                  FastAPI application
  cli.py  demo.py
tools/check_versions.py Version manifest enforcement (CI gate)
tests/                  191 tests; conftest.py provides isolation helpers
src/                    Optional standalone React visualization (see note)
```

> **Note on `src/`**: the React app consumes the HTTP API above -- topology,
> retrieval, guardrail verdicts, tool invocations and evaluation runs are all
> served by the Python backend, so there is one implementation of record.

---

## Documentation

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — components, data flow, control flow
- [`TRADE_OFFS.md`](./TRADE_OFFS.md) — what was simplified, what production needs
- [`VERSION.json`](./VERSION.json) — component versions and rollback procedure
- [`PLAN.md`](./PLAN.md) — engineering execution record
