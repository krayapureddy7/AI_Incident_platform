# Engineering Execution Record

What was built, in what order, and why. This is the record of decisions rather
than a forward-looking plan.

---

## Objectives

1. Four agents with clear, enforced separation of duties, communicating through
   structured envelopes rather than free-form text.
2. Graph-based orchestration with explicit states, deterministic transitions,
   retry/timeout budgets, and replayable persisted state.
3. A single mandatory authorization boundary in front of a schema-first,
   versioned tool plane — with no path around it.
4. Hybrid retrieval and a knowledge graph, with cited evidence and
   graph-derived blast radius.
5. Auditable, replayable traces suitable for a post-mortem.
6. An offline evaluation gate covering both trajectory and safety invariants.
7. Versioned artifacts with an enforced, documented rollback story.

---

## Phase 1 — Tool plane consolidation

**Problem.** Two parallel tool servers existed. One (`McpToolServer`) had its own
cluster and performed **no authorization**, and the Investigator held a direct
reference to it. A read-only agent could execute a tier-0 restart with no policy
check, no approval, and no audit entry — defeating the platform's central claim.
The two clusters also had to be hand-synchronized after every mutation.

**Work.**
- Extracted `app/tools/contracts.py` as the single source of truth for tool
  versions, permission classes, the agent permission matrix, and safety ceilings.
- Rewrote `app/tools/server.py` around `InfraToolServer`, bound to one injected
  cluster, with `build_fastmcp_server(impl)` wrapping it in FastMCP. Removes the
  module-global cluster that made tests order-dependent.
- Deleted the duplicate `McpToolServer`; `mini_platform/tools/mcp_server.py` is
  now a re-export shim, matching the existing style of `tools/gateway.py`.
- Rewrote `ToolGateway` with an explicit eight-check pipeline and a
  deny-by-default permission matrix; wired up the previously dead
  `execute_read_only` so observation traverses the same boundary.
- Split `mutating_call_count` from `fastmcp_call_count`, since reads now
  legitimately cross the gateway and the safety-critical invariant is specifically
  *no mutations*.

**Outcome.** One tool implementation, one cluster, one boundary. The hand-sync
hack in the executor node is gone.

---

## Phase 2 — Agent tool access

- `InvestigatorAgent` now receives a `ToolGateway`, not a tool server. It holds
  no tool handle at all.
- Reads are scoped to the **session** environment and tenant rather than the
  incident's declared values, so a staging session cannot read production
  telemetry by asserting `environment: prod`. Rejected reads are recorded in
  `evidence.tool_errors` and surfaced in the trace.
- Added an `EVIDENCE_UNAVAILABLE` root cause. When no telemetry can be gathered,
  the Ops agent proposes a read-only re-check instead of an unjustified mutation.
- `OpsAgent` now derives `estimated_blast_radius` from knowledge-graph traversal
  instead of a hardcoded constant. The proposal still declares the *target*
  environment and tenant, deliberately unclamped, so the Verifier can detect a
  boundary mismatch.

---

## Phase 3 — Correctness fixes

- **Recovery healthcheck read the wrong tool.** It requested `query_logs`, which
  was never registered. The call failed silently, so `post_logs` was always empty
  and `has_fatal_logs` was always `False` — the fatal-log gate passed vacuously on
  every run. Fixed to `get_logs`, routed through the gateway.
- Added a `telemetry_available` condition: a healthcheck that cannot read
  telemetry has verified nothing and must not pass.
- Added `MockInfrastructureCluster.record_recovery` so a successful remediation
  clears the stale FATAL lines, keeping logs and metrics consistent.
- Centralized the replica ceiling. Guardrails capped at 10 while the tool layer
  capped at each service's `max_replicas` (12 for payment-service); the effective
  limit is now explicitly the stricter of the two.
- Tightened human-token validation to require an approver segment beyond the
  prefix, so a bare `TOKEN-HUMAN-APPROVED-` no longer passes.
- Bounded the live tracer registry (FIFO, 256) which previously grew without
  limit in any long-lived process.

---

## Phase 4 — Retry and timeout

**Problem.** `max_retries` and `step_timeout_sec` were accepted by the
orchestrator constructor, stored as attributes, and never read. There was no
retry and no timeout anywhere.

**Work.** Added `orchestrator/resilience.py` with `with_resilience`, applied to
every graph node.

Design decisions worth recording:

- **The executor is never auto-retried.** Re-issuing a mutation whose outcome is
  unknown is unsafe; idempotency is a safety net, not a licence to retry.
- **Only exceptions retry.** A deterministic policy rejection is a normal return
  value, so a Verifier verdict is never retried into a different outcome.
- **The pool is not used as a context manager.** `__exit__` calls
  `shutdown(wait=True)`, which blocks on exactly the worker the timeout exists to
  abandon. (Caught by a test that took 61s instead of timing out.) Python cannot
  kill a thread, so a stalled worker lingers — but no longer holds up the
  workflow.
- An exhausted budget terminates as `ORCHESTRATION_FAILED` with the failing node
  named, and the trace is still persisted.

---

## Phase 5 — Durable persistence

- Added `persistence/store.py`: SQLite catalogue of runs and audit traces, so a
  run is queryable and replayable after the originating process exits.
- Added `orchestrator/checkpointing.py` with a durable SQLite checkpointer,
  falling back to in-memory when the optional dependency is absent. This makes a
  Tier-3 hold resumable **across processes** — the engineer approving an action is
  rarely in the process that proposed it.
- Added `IncidentOrchestrator.close()` and context-manager support after a test
  teardown surfaced an unclosed SQLite handle (a real leak in a long-lived API
  process, and a file-deletion blocker on Windows).

---

## Phase 6 — API

Built `mini_platform/api/server.py`, which the documentation and
`Dockerfile.api` already referenced but which did not exist — `docker compose up`
failed on a missing module. It is a thin, typed surface with no policy of its
own, delegating to the same orchestrator and gateway as the CLI, with an
injectable factory so tests get an isolated cluster and a temporary database.

---

## Phase 7 — Tests and evaluation

Grew the suite from 49 to 138 tests.

- `tests/conftest.py` provides `build_isolated_stack` / `build_isolated_orchestrator`,
  so every test owns a private cluster. Previously tests constructed a fresh
  cluster but the tools closed over a module global, making them order-dependent.
- New: `test_orchestrator_resilience.py`, `test_persistence.py`, `test_api.py`,
  `test_recovery_verification.py`, `test_versioning.py`.
- `test_fastmcp_contracts.py` gained `TestAgentPermissionMatrix` and
  `TestRejectedActionsNeverReachTools` as direct regression cover for the
  escalation hole.
- `test_tools_contract.py` was retargeted from the deleted duplicate server to
  the surviving implementation via the gateway, preserving the original intent.
- Refactored the eval runner from per-scenario `if/elif` blocks into declarative
  check families (`trajectory`, `human_gate`, `isolation`), added an approved
  Tier-0 execution scenario, made it exit non-zero on failure, and made output
  ASCII-only — the decorative glyphs previously raised `UnicodeEncodeError` on
  Windows cp1252 and masked the result.

---

## Phase 8 — Versioning, CI, docs

- `tools/check_versions.py` compares `VERSION.json` against the versions the code
  reports and fails on drift, making "versioned artifacts" enforceable rather
  than aspirational. Wired as a required CI job.
- CI split into `test` → `eval-gate` → `versioning`, with artifact upload.
- Rewrote `demo.py` so every printed value is read back from the actual run.
  Previously stages 1–4 were hardcoded `print()` literals that did not reflect
  agent output.
- Rewrote the documentation to match the implementation. The prior README claimed
  FastAPI, sentence-transformers, FAISS, NetworkX, SQLite, and LangSmith; none
  were imported. Unused dependencies were removed from `requirements.txt` and
  `pyproject.toml`, and the dense ranker is now described accurately as a hashed
  projection rather than a neural embedding model.
- Added `DEMO.md` with a runnable walkthrough including the refusal paths.
- Fixed `.gitignore` (it omitted `.traces/`, and trailing comments on pattern
  lines would have been treated as literals), and replaced the leftover AI Studio
  `.env.example`.
- Docker: `Dockerfile.mcp` no longer uses a conditional-expression `CMD` that
  silently no-ops; both images run as non-root; the MCP container is deliberately
  **not** published to the host, since it performs no authorization.
